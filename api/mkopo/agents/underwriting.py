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
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from mkopo.agents._base import build_compiled_graph
from mkopo.agents.guardrails import (
    UNDERWRITING_SUMMARY_CONSTITUTION,
    JudgmentSpec,
    make_validator_node,
    make_validator_router,
)
from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.llm_gateway import get_gateway
from mkopo.models import AgentName, AgentRun, Loan
from mkopo.rules.policy import (
    DSCR_FLOORS,
    POLICY_MAX_DTI_PERSONAL,
    POLICY_MAX_LTI_PERSONAL,
    POLICY_MIN_FICO,
    REQUIRED_DOCS,
    REQUIRED_DOCS_PERSONAL,
    PropertyType,
    has_blocking_failures,
)
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
    loan_class: str  # "business" | "personal" — drives prompt + rule pack
    extractions: dict[str, str]  # field_name -> value (accepted only)
    rule_outcomes: list[RiskFlag]
    kpis: UnderwritingKPIs | None
    summary: UnderwritingResult | None
    agent_run_id: str
    # Self-Refine loop bookkeeping (see :mod:`agents.guardrails`).
    # The summary drafter increments ``validation_attempts``; the
    # validator writes the worst severity into ``last_judgment`` and
    # the critique into ``last_critique``; the conditional edge
    # router below reads both to decide whether to retry or persist.
    validation_attempts: int
    last_critique: str | None
    last_judgment: dict | None


# --- LLM output schema (separate from the persisted UnderwritingResult so
#     the model doesn't try to invent `generated_at` and `agent_run_id`). ---


class _DraftedUnderwriting(BaseModel):
    sections: list[UnderwritingSection]
    recommendation: Literal["proceed_to_decision", "request_more_info", "decline"]
    rationale: str = Field(max_length=800)


# --- Prompts -------------------------------------------------------------
#
# Two persona/system prompts: one for commercial loans (DSCR, LTV,
# property collateral, sponsor entity) and one for personal/consumer
# loans (DTI, FICO, employment, no collateral). Keeping them separate
# beats a "covers both" prompt that reads like neither — the LLM picks
# up on the persona and writes more credibly when it's not asked to
# code-switch mid-paragraph.

# System prompts now live in the ``prompts`` table and are loaded via
# ``prompts.get(identifier)`` below. The identifiers are kept as
# constants so the call-site reads obviously. See
# mkopo.services.prompts.DEFAULTS for the canonical default bodies.
_COMMERCIAL_PROMPT_ID = "underwriting.summary.commercial"
_PERSONAL_PROMPT_ID = "underwriting.summary.personal"


def _commercial_kpi_block(kpis: UnderwritingKPIs) -> str:
    """Format the KPI bullet list for a commercial-loan prompt."""
    parts = [
        f"- loan_amount: ${float(kpis.loan_amount):,.0f}",
        f"- property_type: {kpis.property_type}",
    ]
    parts.append(f"- LTV: {kpis.ltv:.1%}" if kpis.ltv is not None else "- LTV: unknown")
    parts.append(f"- DSCR: {kpis.dscr:.2f}" if kpis.dscr is not None else "- DSCR: unknown")
    parts.append(
        f"- Debt yield: {kpis.debt_yield:.2%}"
        if kpis.debt_yield is not None
        else "- Debt yield: unknown"
    )
    return "\n".join(parts) + "\n"


def _personal_kpi_block(kpis: UnderwritingKPIs) -> str:
    """Format the KPI bullet list for a personal-loan prompt."""
    parts = [f"- loan_amount: ${float(kpis.loan_amount):,.0f}"]
    parts.append(f"- DTI: {kpis.dti:.1%}" if kpis.dti is not None else "- DTI: unknown")
    parts.append(
        f"- LTI (loan / annual income): {kpis.lti:.1%}"
        if kpis.lti is not None
        else "- LTI: unknown"
    )
    if kpis.credit_score is not None:
        band = f" ({kpis.credit_band})" if kpis.credit_band else ""
        parts.append(f"- FICO: {kpis.credit_score}{band}")
    else:
        parts.append("- FICO: unknown")
    parts.append(
        f"- Employment: {kpis.years_employment:.1f} yrs"
        if kpis.years_employment is not None
        else "- Employment tenure: unknown"
    )
    return "\n".join(parts) + "\n"


# --- Nodes ---


async def fetch_and_evaluate(state: UnderwritingState) -> UnderwritingState:
    """Combined fetch + rules + KPI computation via the shared evaluator.

    Both underwriting and decision agents route through
    `services.rules_eval.evaluate` so the engine remains the single
    source of truth across agents.

    Pre-flight gate: if there are no accepted (or human-overridden)
    extractions on this loan, the rules engine has nothing to evaluate
    against and the LLM summary would be uselessly generic. Short-
    circuit with ``status='needs_extractions'`` so downstream nodes
    skip and the UI can guide the user to run intake or accept fields
    in the review queue.
    """
    loan_id = uuid.UUID(state["loan_id"])
    async with get_session() as session:
        # Inspect the extractions table first — cheaper than running
        # the full rule pass when we already know the inputs are empty.
        from sqlalchemy import select

        from mkopo.models import Document, Extraction, ExtractionStatus

        has_inputs = (
            await session.execute(
                select(Extraction.id)
                .join(Document)
                .where(
                    Document.loan_id == loan_id,
                    Extraction.status.in_((ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN)),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        if not has_inputs:
            await record(
                session,
                loan_id=loan_id,
                actor=Actor.agent(AgentName.UNDERWRITING),
                action="underwriting_skipped",
                payload={"reason": "needs_extractions"},
            )
            return {**state, "status": "needs_extractions"}

        result = await evaluate_rules_against_loan(session, loan_id)

    # Compute KPIs from the engine's typed context. The KPI strip in the
    # UI branches on ``loan_class`` — business loans render the commercial
    # tiles (LTV / DSCR / debt-yield); personal loans render the
    # consumer-credit tiles (DTI / LTI / FICO / employment). We populate
    # whichever side applies and leave the other null.
    ctx = result.ctx
    loan_class_value = (
        result.loan.loan_class.value
        if hasattr(result.loan.loan_class, "value")
        else str(result.loan.loan_class)
    )
    doc_confidence = (
        sum(result.confidences.values()) / len(result.confidences) if result.confidences else None
    )

    if loan_class_value == "personal":
        # Personal tile set.
        monthly_income = (
            ctx.annual_income / Decimal(12) if ctx.annual_income and ctx.annual_income > 0 else None
        )
        dti = (
            float(ctx.monthly_debt_payments / monthly_income)
            if monthly_income and ctx.monthly_debt_payments is not None
            else None
        )
        lti = (
            float(ctx.loan_amount / ctx.annual_income)
            if ctx.annual_income and ctx.annual_income > 0
            else None
        )
        from mkopo.rules.policy import _credit_band  # local: avoids cycles

        kpis = UnderwritingKPIs(
            loan_amount=ctx.loan_amount,
            dti=dti,
            lti=lti,
            credit_score=ctx.credit_score,
            credit_band=_credit_band(ctx.credit_score) if ctx.credit_score is not None else None,
            years_employment=float(ctx.years_employment) if ctx.years_employment else None,
            doc_confidence=doc_confidence,
        )
    else:
        # Commercial tile set — the original computation, unchanged.
        ltv = float(ctx.loan_amount / ctx.appraised_value) if ctx.appraised_value else None
        dscr = (
            float(ctx.annual_noi / ctx.annual_debt_service)
            if ctx.annual_noi and ctx.annual_debt_service
            else None
        )
        debt_yield = float(ctx.annual_noi / ctx.loan_amount) if ctx.annual_noi else None
        kpis = UnderwritingKPIs(
            loan_amount=ctx.loan_amount,
            ltv=ltv,
            dscr=dscr,
            debt_yield=debt_yield,
            property_type=ctx.property_type.value,
            doc_confidence=doc_confidence,
        )

    return {
        **state,
        "loan_class": loan_class_value,
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

    # Branch on loan class. Personal and commercial loans have entirely
    # different headline metrics, persona language, and policy backdrop;
    # one prompt that "covers both" reads like neither so we keep them
    # separate. Loan class lands in state from evaluate_rules.
    loan_class = state.get("loan_class", "business")
    is_personal = loan_class == "personal"

    from mkopo.services.prompts import get as get_prompt

    if is_personal:
        kpi_block = _personal_kpi_block(kpis)
        system = get_prompt(_PERSONAL_PROMPT_ID)
        sections_hint = (
            "Produce 3–4 sections covering: Applicant & employment, "
            "Income & debt capacity (DTI / LTI), Credit profile (FICO + band), "
            "Risks. Each section is 1–3 sentences."
        )
        policy_footer = (
            f"DTI cap: {POLICY_MAX_DTI_PERSONAL:.0%}. "
            f"LTI ceiling: {POLICY_MAX_LTI_PERSONAL:.0%}. "
            f"FICO floor: {POLICY_MIN_FICO}. "
            f"Required doc types: {sorted(REQUIRED_DOCS_PERSONAL)}."
        )
    else:
        kpi_block = _commercial_kpi_block(kpis)
        system = get_prompt(_COMMERCIAL_PROMPT_ID)
        sections_hint = (
            "Produce 3–5 sections covering: Borrower & sponsorship, "
            "Property & collateral, Financials (NOI, DSCR, debt yield), "
            "Risks. Each section is 1–3 sentences."
        )
        # Fall back to OTHER if the property_type string drifted — keeps
        # the prompt from crashing on an unmapped string.
        try:
            prop = PropertyType(kpis.property_type)
        except ValueError:
            prop = PropertyType.OTHER
        policy_footer = (
            f"DSCR floor for {prop.value}: {DSCR_FLOORS[prop]}. "
            f"Required doc types: {sorted(REQUIRED_DOCS)}."
        )

    has_blocking = any(not f.passed and f.severity == "block" for f in flags)
    has_warn = any(not f.passed and f.severity == "warn" for f in flags)
    # The personal pack uses a different doc-completeness rule id.
    doc_rule_id = "personal_doc_completeness" if is_personal else "doc_completeness"
    has_missing = any(f.rule_id == doc_rule_id and not f.passed for f in flags)
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
        f"Underwrite this {'personal' if is_personal else 'commercial real-estate'} "
        f"loan for committee review. {sections_hint}\n\n"
        f"Key metrics:\n{kpi_block}\n"
        f"Extractions accepted by intake (use these — cite by field_name):\n"
        f"{extractions_block or '(none)'}\n\n"
        f"Rule engine outcomes (do not restate; reference in the rationale if "
        f"useful):\n{rules_block or '(none)'}\n\n"
        f"Recommendation guidance: {recommendation_hint}\n\n"
        f"{policy_footer}"
    )
    # Self-Refine: if the previous draft was rejected, fold in the
    # critique so the new draft targets the specific failure (no
    # bracketed KPIs, no mixed vocabulary, etc.). Mirrors the
    # decision-agent pattern.
    last_critique = state.get("last_critique")
    if last_critique:
        user += (
            "\n\nIMPORTANT — the previous draft was rejected by the "
            "guardrail judge:\n"
            f'"{last_critique}"\n\n'
            "Revise to address the specific failure above. Do not "
            "repeat the same mistake."
        )

    drafted: _DraftedUnderwriting = await gateway.call_structured(
        model=settings.llm_heavy_model,
        system=system,
        user=user,
        schema=_DraftedUnderwriting,
    )

    # Bump the attempt counter so the validator router can bound retries.
    attempts = state.get("validation_attempts", 0) + 1
    # ``agent_run_id`` should be stamped into state by whichever caller
    # invoked the graph (SSE path: ``agents/streaming.py``; autonomous
    # path: ``agents/orchestrator._run_underwriting_agent``). If it's
    # missing, the AgentRun insert most likely failed earlier — we
    # still build the result (the graph contract requires a non-Optional
    # UUID) but mint an unrooted id and log loudly so the orphan row
    # shows up in observability rather than silently disappearing.
    raw_run_id = state.get("agent_run_id")
    if raw_run_id:
        result_run_id = uuid.UUID(raw_run_id)
    else:
        result_run_id = uuid.uuid4()
        logger.warning(
            "underwriting_persist_missing_agent_run_id",
            loan_id=state.get("loan_id"),
            fabricated_id=str(result_run_id),
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
            agent_run_id=result_run_id,
        ),
        "validation_attempts": attempts,
    }


# --- Self-correction loop (LLM-as-judge + Self-Refine) -----------------------
#
# Same pattern as decision.py — validate the drafted summary
# against UNDERWRITING_SUMMARY_CONSTITUTION, route back to the
# drafter on block (bounded by MAX_VALIDATION_ATTEMPTS) or forward
# to persist on pass.


def _underwriting_judge_context(state: UnderwritingState) -> str:
    """Context the judge needs to evaluate "no fabricated KPIs" and
    "recommendation consistent with rules"."""
    kpis = state.get("kpis")
    flags = state.get("rule_outcomes", []) or []
    rules_summary = "\n".join(
        f"- {f.rule_id} (severity={f.severity}, passed={f.passed}): {f.message}" for f in flags
    )
    return (
        f"Loan class: {state.get('loan_class', 'business')}\n"
        f"KPI block (these are the only numeric values the summary may "
        f"cite):\n{kpis.model_dump_json(indent=2) if kpis else '(none)'}\n\n"
        f"Rule outcomes:\n{rules_summary}\n"
    )


def _extract_summary_text(state: UnderwritingState) -> str | None:
    summary = state.get("summary")
    if summary is None:
        return None
    sections_block = "\n\n".join(f"## {s.title}\n{s.body}" for s in summary.sections)
    return (
        f"Recommendation: {summary.recommendation}\n"
        f"Rationale: {summary.rationale}\n\n"
        f"{sections_block}"
    )


validate_summary = make_validator_node(
    (
        JudgmentSpec(
            constitution=UNDERWRITING_SUMMARY_CONSTITUTION,
            extract_text=_extract_summary_text,
            extract_context=_underwriting_judge_context,
        ),
    )
)


route_after_validate_summary = make_validator_router(
    retry_node="draft_summary",
    persist_node="persist",
)


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

        # The AgentRun row is created by the streaming layer at the
        # start of the run (status="running"); we update it here with
        # the final payload + flip the status to "complete". UPDATE-
        # by-id instead of INSERT avoids the duplicate-pk error and
        # keeps the row's ``id`` stable for any AgentStep rows that
        # already point at it.
        # Snapshot the materials hash — what fed this underwriting
        # recommendation. Stage-transition guard later compares the
        # current hash to this one; mismatch means materials drifted
        # and the recommendation is stale.
        from mkopo.services.materials_hash import compute_materials_hash

        materials_hash = await compute_materials_hash(session, loan_id)

        # Persist the FULL Pydantic dump under ``result_json``. The
        # streaming layer used to be the only path to the body of the
        # result; that meant a page refresh wiped the workspace until
        # the underwriter re-ran the agent (which was confusing and
        # also wasted tokens). Now the result is in the AgentRun row,
        # and the new ``/loans/{id}/underwriting/latest`` endpoint
        # rehydrates it on mount. The other top-level keys are kept
        # for the eval / observability code paths that read them
        # directly without unmarshalling the full result.
        result_json = summary.model_dump(mode="json")
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == summary.agent_run_id)
            .values(
                status="complete",
                started_at=Decimal(int(summary.generated_at.timestamp())),
                payload={
                    "recommendation": summary.recommendation,
                    "risk_band": risk_band,
                    "n_sections": len(summary.sections),
                    "n_flags": len(summary.risk_flags),
                    "materials_hash": materials_hash,
                    "has_blocking_failures": has_blocking_failures(
                        [_outcome_from_flag(f) for f in summary.risk_flags]
                    ),
                    "result_json": result_json,
                    # Constitutional judge verdict — captured by
                    # validate_summary so the observability page can
                    # render whether the draft passed cleanly or
                    # required Self-Refine retries.
                    "guardrail_judgment": state.get("last_judgment"),
                    "validation_attempts": state.get("validation_attempts", 0),
                },
            )
        )

        await record(
            session,
            loan_id=loan_id,
            actor=Actor.agent(AgentName.UNDERWRITING),
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
    builder: StateGraph = StateGraph(UnderwritingState)
    builder.add_node("fetch_and_evaluate", fetch_and_evaluate)
    builder.add_node("draft_summary", draft_summary)
    # Constitutional judge — enforces the no-fabricated-KPIs +
    # recommendation-consistent-with-rules invariants. Conditional
    # edge below routes back on block, bounded by
    # MAX_VALIDATION_ATTEMPTS.
    builder.add_node("validate_summary", validate_summary)
    builder.add_node("persist", persist)

    builder.add_edge(START, "fetch_and_evaluate")

    def route_after_eval(state: UnderwritingState) -> str:
        """Pre-flight short-circuit. When ``fetch_and_evaluate`` detects
        no extractions to underwrite, skip the LLM summary step
        entirely — drafting a summary of nothing wastes tokens and
        leaves the user with a generic 'cannot determine' paragraph
        they have to read to discover what they should have known up
        front."""
        if state.get("status") == "needs_extractions":
            return END
        return "draft_summary"

    builder.add_conditional_edges(
        "fetch_and_evaluate",
        route_after_eval,
        {END: END, "draft_summary": "draft_summary"},
    )
    builder.add_edge("draft_summary", "validate_summary")
    builder.add_conditional_edges(
        "validate_summary",
        route_after_validate_summary,
        {"draft_summary": "draft_summary", "persist": "persist"},
    )
    builder.add_edge("persist", END)

    async with build_compiled_graph(builder) as graph:
        yield graph
