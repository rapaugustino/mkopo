"""Underwriting agent.

Runs after intake is complete (manual or stage-driven trigger):

1. Load extractions, documents, and guarantor exposure for the loan.
2. Convert extractions into the deterministic `RuleContext` and run the
   rules engine (LTV, DSCR, appraisal age, doc completeness, guarantor
   concentration).
3. Have the LLM draft a structured underwriting summary that cites the
   extractions it relied on. Risk flags come straight from the rules engine
   — the LLM does not generate them.
4. Persist an `agent_runs` row + audit events so the action is replayable.

Design choices:

- The rules engine is the source of truth for risk flags. The LLM only
  *describes* — it does not classify pass/fail. This keeps the boundary
  between "interpretive" (LLM) and "deterministic" (rules) crisp.
- The agent runs synchronously per request — no interrupts. The Postgres
  checkpointer is still attached because the spec calls for one across all
  agents and it costs us nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import select

from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.llm_gateway import get_gateway
from mkopo.models import AgentRun, Loan
from mkopo.rules.policy import DSCR_FLOORS, REQUIRED_DOCS, PropertyType, has_blocking_failures
from mkopo.schemas import (
    RiskFlag,
    UnderwritingKPIs,
    UnderwritingResult,
    UnderwritingSection,
)
from mkopo.services.audit import Actor, record
from mkopo.services.ingest import embed_loan_summary
from mkopo.services.rules_eval import evaluate as evaluate_rules_against_loan

logger = structlog.get_logger()


# --- State ---


class UnderwritingState(TypedDict, total=False):
    loan_id: str
    extractions: dict[str, str]  # field_name -> value (accepted only)
    rule_outcomes: list[RiskFlag]
    kpis: UnderwritingKPIs | None
    summary: UnderwritingResult | None
    agent_run_id: str


# --- LLM output schema (separate from the persisted UnderwritingResult so
#     the model doesn't try to invent `generated_at` and `agent_run_id`). ---


class _DraftedUnderwriting(BaseModel):
    sections: list[UnderwritingSection]
    recommendation: Literal["proceed_to_decision", "request_more_info", "decline"]
    rationale: str = Field(max_length=800)


# --- Nodes ---


async def fetch_and_evaluate(state: UnderwritingState) -> UnderwritingState:
    """Combined fetch + rules + KPI computation via the shared evaluator.

    Both underwriting and decision agents route through
    `services.rules_eval.evaluate` so the engine remains the single
    source of truth across agents.
    """
    loan_id = uuid.UUID(state["loan_id"])
    async with get_session() as session:
        result = await evaluate_rules_against_loan(session, loan_id)

    # Compute KPIs from the engine's typed context.
    ctx = result.ctx
    ltv = float(ctx.loan_amount / ctx.appraised_value) if ctx.appraised_value else None
    dscr = (
        float(ctx.annual_noi / ctx.annual_debt_service)
        if ctx.annual_noi and ctx.annual_debt_service
        else None
    )
    debt_yield = float(ctx.annual_noi / ctx.loan_amount) if ctx.annual_noi else None
    doc_confidence = (
        sum(result.confidences.values()) / len(result.confidences) if result.confidences else None
    )
    kpis = UnderwritingKPIs(
        loan_amount=ctx.loan_amount,
        ltv=ltv,
        dscr=dscr,
        debt_yield=debt_yield,
        doc_confidence=doc_confidence,
        property_type=ctx.property_type.value,
    )

    return {
        **state,
        "extractions": result.extractions,
        "rule_outcomes": result.flags,
        "kpis": kpis,
    }


async def draft_summary(state: UnderwritingState) -> UnderwritingState:
    """Ask the LLM to draft a cited summary of the loan + rule outcomes."""
    extractions = state.get("extractions", {})
    flags = state.get("rule_outcomes", [])
    kpis = state["kpis"]
    assert kpis is not None, "evaluate_rules did not produce KPIs"

    settings = get_settings()
    gateway = get_gateway()

    extractions_block = "\n".join(f"- {k}: {v}" for k, v in extractions.items())
    rules_block = "\n".join(
        f"- {f.rule_id} ({f.severity}, {'PASS' if f.passed else 'FAIL'}): {f.message}"
        for f in flags
    )
    kpi_block = (
        (
            f"- loan_amount: ${float(kpis.loan_amount):,.0f}\n"
            f"- property_type: {kpis.property_type}\n"
            f"- LTV: {kpis.ltv:.1%}\n"
            if kpis.ltv
            else "- LTV: unknown\n"
        )
        + (f"- DSCR: {kpis.dscr:.2f}\n" if kpis.dscr else "- DSCR: unknown\n")
        + (
            f"- Debt yield: {kpis.debt_yield:.2%}\n"
            if kpis.debt_yield
            else "- Debt yield: unknown\n"
        )
    )

    system = (
        "You are an experienced commercial loan underwriter writing for a "
        "credit committee. You produce factual, concise, citation-backed "
        "summaries. Hard rules:\n"
        "1. You MUST cite the field names you rely on in each section's "
        "   `citations` array (e.g. 'annual_noi', 'appraised_value').\n"
        "2. You do NOT invent values — if a field is missing, say so.\n"
        "3. You do NOT compute or assert pass/fail on policy rules — the "
        "   rules engine has already done that. You may *describe* what it "
        "   found, but the verdict belongs to the engine.\n"
        "4. You do NOT name a recommendation that contradicts the engine's "
        "   blocking failures — if the engine blocks the deal, your only "
        "   options are 'decline' or 'request_more_info'.\n"
    )

    has_blocking = any(not f.passed and f.severity == "block" for f in flags)
    has_warn = any(not f.passed and f.severity == "warn" for f in flags)
    has_missing = any(f.rule_id == "doc_completeness" and not f.passed for f in flags)
    if has_blocking:
        recommendation_hint = (
            "At least one BLOCKING rule failed. Recommend 'decline' unless the "
            "missing data could change that, in which case 'request_more_info'."
        )
    elif has_missing:
        recommendation_hint = "Missing required documents — recommend 'request_more_info'."
    elif has_warn:
        recommendation_hint = (
            "Warnings but no blocks. Recommend 'proceed_to_decision' and "
            "call the warnings out in the rationale."
        )
    else:
        recommendation_hint = "All rules pass — recommend 'proceed_to_decision'."

    user = (
        "Underwrite this loan for committee review. Produce 3–5 sections covering: "
        "Borrower & sponsorship, Property & collateral, Financials (NOI, DSCR, "
        "debt yield), Risks. Each section is 1–3 sentences.\n\n"
        f"Key metrics:\n{kpi_block}\n"
        f"Extractions accepted by intake (use these — cite by field_name):\n"
        f"{extractions_block or '(none)'}\n\n"
        f"Rule engine outcomes (do not restate; reference in the rationale if "
        f"useful):\n{rules_block or '(none)'}\n\n"
        f"Recommendation guidance: {recommendation_hint}\n\n"
        f"DSCR floor for {kpis.property_type}: {DSCR_FLOORS[PropertyType(kpis.property_type)]}. "
        f"Required doc types: {sorted(REQUIRED_DOCS)}."
    )

    drafted: _DraftedUnderwriting = await gateway.call_structured(
        model=settings.llm_heavy_model,
        system=system,
        user=user,
        schema=_DraftedUnderwriting,
    )

    return {
        **state,
        "summary": UnderwritingResult(
            kpis=kpis,
            sections=drafted.sections,
            risk_flags=flags,
            recommendation=drafted.recommendation,
            rationale=drafted.rationale,
            generated_at=datetime.now(UTC),
            agent_run_id=uuid.UUID(state.get("agent_run_id", str(uuid.uuid4()))),
        ),
    }


def _derive_risk_band(summary: UnderwritingResult) -> str:
    """Map (recommendation, rule failures) → 'low' | 'med' | 'high'.

    Used to drive the pipeline risk dot. Deterministic so re-runs are stable.
    """
    if summary.recommendation == "decline":
        return "high"
    failed_warns = sum(1 for f in summary.risk_flags if not f.passed and f.severity == "warn")
    failed_blocks = sum(1 for f in summary.risk_flags if not f.passed and f.severity == "block")
    if failed_blocks > 0 or summary.recommendation == "request_more_info":
        return "med"
    if failed_warns > 0:
        return "med"
    return "low"


def _band(amount: float) -> str:
    """Bucket a loan amount into a non-identifying band."""
    if amount < 1_000_000:
        return "under_1m"
    if amount < 2_500_000:
        return "1m_to_2_5m"
    if amount < 5_000_000:
        return "2_5m_to_5m"
    if amount < 10_000_000:
        return "5m_to_10m"
    return "over_10m"


def _city_from_address(addr: str) -> str:
    """Coarsen `123 Main St, Tacoma, WA 98409` → `Tacoma, WA`.

    City-level geography is operationally useful for comparable search
    (submarket dynamics) without identifying the specific property.
    """
    parts = [p.strip() for p in addr.split(",")]
    if len(parts) >= 3:
        return f"{parts[-2]}, {parts[-1].split()[0]}"
    return ""


def _build_search_corpus(summary: UnderwritingResult, extractions: dict[str, str]) -> str:
    """Privacy-conscious embedding corpus for comparable-loans kNN.

    Deliberately EXCLUDES borrower entity name and full property address —
    those make embeddings inversion-attack-friendly and let one borrower's
    information bleed into another's underwriting context. We keep the
    operational features (asset type, city-level geography, financial
    ratios, rule outcomes) so two loans still cluster when they're
    genuinely similar deals.

    See conversation re: "privacy + leakage across loans" for rationale.
    """
    addr = extractions.get("property_address", "")
    parts = [
        f"Property type: {summary.kpis.property_type}.",
        f"Market: {_city_from_address(addr) or 'unspecified'}.",
        f"Loan size band: {_band(float(summary.kpis.loan_amount))}.",
    ]
    if summary.kpis.ltv is not None:
        parts.append(f"LTV {summary.kpis.ltv:.0%}.")
    if summary.kpis.dscr is not None:
        parts.append(f"DSCR {summary.kpis.dscr:.2f}.")
    if summary.kpis.debt_yield is not None:
        parts.append(f"Debt yield {summary.kpis.debt_yield:.1%}.")
    parts.append(f"Recommendation: {summary.recommendation}.")
    # Rationale text can mention property type, financials, etc.; the LLM
    # is prompted not to name borrowers, so it's safe to embed.
    parts.append(summary.rationale)
    for f in summary.risk_flags:
        if not f.passed:
            parts.append(f"Flag: {f.rule_id}: {f.message}.")
    return " ".join(parts)


async def persist(state: UnderwritingState) -> UnderwritingState:
    """Write the agent_run, set loan.risk_band + embedding, log an audit event."""
    loan_id = uuid.UUID(state["loan_id"])
    summary = state["summary"]
    assert summary is not None, "draft_summary did not produce a summary"

    risk_band = _derive_risk_band(summary)
    corpus = _build_search_corpus(summary, state.get("extractions", {}))

    async with get_session() as session:
        # Set risk band on the loan row so the pipeline can show it.
        loan = (await session.execute(select(Loan).where(Loan.id == loan_id))).scalar_one()
        loan.risk_band = risk_band

        # Embed the search corpus and stamp it onto loans.embedding so
        # comparable-loans kNN can find this loan as a neighbor.
        await embed_loan_summary(session, loan_id, corpus)

        agent_run = AgentRun(
            id=summary.agent_run_id,
            loan_id=loan_id,
            agent_name="underwriting",
            thread_id=f"underwriting-{loan_id}",
            status="complete",
            started_at=Decimal(int(summary.generated_at.timestamp())),
            payload={
                "recommendation": summary.recommendation,
                "risk_band": risk_band,
                "n_sections": len(summary.sections),
                "n_flags": len(summary.risk_flags),
                "has_blocking_failures": has_blocking_failures(
                    [_outcome_from_flag(f) for f in summary.risk_flags]
                ),
            },
        )
        session.add(agent_run)

        await record(
            session,
            loan_id=loan_id,
            actor=Actor.agent("underwriting"),
            action="underwriting_complete",
            payload={
                "recommendation": summary.recommendation,
                "risk_band": risk_band,
                "rationale": summary.rationale[:280],
                "agent_run_id": str(summary.agent_run_id),
                "rule_summary": [
                    {"rule_id": f.rule_id, "passed": f.passed, "severity": f.severity}
                    for f in summary.risk_flags
                ],
            },
        )

    return state


def _outcome_from_flag(f: RiskFlag) -> Any:
    """Minimal duck-typed adapter so we can reuse `has_blocking_failures`."""
    return type("O", (), {"passed": f.passed, "severity": f.severity})()


# --- Graph ---


@asynccontextmanager
async def build_underwriting_graph() -> AsyncIterator[Any]:
    """Yield a compiled underwriting graph. Same context-manager pattern as intake."""
    settings = get_settings()

    builder: StateGraph = StateGraph(UnderwritingState)
    builder.add_node("fetch_and_evaluate", fetch_and_evaluate)
    builder.add_node("draft_summary", draft_summary)
    builder.add_node("persist", persist)

    builder.add_edge(START, "fetch_and_evaluate")
    builder.add_edge("fetch_and_evaluate", "draft_summary")
    builder.add_edge("draft_summary", "persist")
    builder.add_edge("persist", END)

    async with AsyncPostgresSaver.from_conn_string(settings.database_url_libpq) as checkpointer:
        await checkpointer.setup()
        yield builder.compile(checkpointer=checkpointer)
