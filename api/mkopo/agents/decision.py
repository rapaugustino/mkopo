"""Decision support agent.

Picks a credit decision path (approve / conditional / decline) on top of
the underwriting workup, drafts the path-specific artifact (term sheet,
conditions list, or an ECOA-defensible adverse action letter), and
persists the package.

What this agent does NOT do:

- It does not invent rule outcomes. Same boundary as the underwriting
  agent: the rules engine is the source of truth, and the LLM cannot
  contradict its BLOCKING failures.
- It does not transition the loan's stage. That's a deliberate human
  action via `transition_stage` once the underwriter accepts the
  recommended path or overrides it.
- It does not produce real market-rate pricing — the term sheet uses
  per-loan-type rate proxies consistent with `rules_eval._debt_service_proxy`.

ECOA / Regulation B compliance (decline path):

`AdverseActionLetter.principal_reasons` MUST contain at least one cited
rule. The system prompt instructs the LLM to name those reasons
specifically in the body — "DSCR 1.08 below the 1.20 multifamily floor",
not "internal policy". This is what makes the letter defensible if
challenged. Real production would also include FCRA disclosures (credit
report source, right to dispute) — out of scope for the portfolio demo.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypedDict

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.llm_gateway import get_gateway
from mkopo.models import AgentRun, Condition, ConditionStatus, Loan
from mkopo.schemas import (
    AdverseActionLetter,
    ConditionDraft,
    DecisionPathLiteral,
    DecisionResult,
    RiskFlag,
    TermSheet,
)
from mkopo.services.audit import Actor, record
from mkopo.services.rules_eval import evaluate as evaluate_rules_against_loan

logger = structlog.get_logger()


# --- State ---


class DecisionState(TypedDict, total=False):
    loan_id: str
    extractions: dict[str, str]
    rule_outcomes: list[RiskFlag]
    decision: DecisionResult | None
    agent_run_id: str


# --- LLM output schemas (separate from the persisted DecisionResult so the
#     model isn't asked to invent `generated_at` and `agent_run_id`). ---


class _DraftedDecision(BaseModel):
    path: DecisionPathLiteral
    confidence: float = Field(ge=0, le=1)
    verdict_text: str = Field(max_length=120)
    rationale: str = Field(max_length=800)


class _DraftedApproveConditional(BaseModel):
    term_sheet: TermSheet
    conditions: list[ConditionDraft] = Field(default_factory=list)


class _DraftedDecline(BaseModel):
    adverse_action_letter: AdverseActionLetter


# --- Helpers ---


def _has_blocking_failure(flags: list[RiskFlag]) -> bool:
    return any(not f.passed and f.severity == "block" for f in flags)


def _has_missing_docs(flags: list[RiskFlag]) -> bool:
    return any(f.rule_id == "doc_completeness" and not f.passed for f in flags)


def _has_warning(flags: list[RiskFlag]) -> bool:
    return any(not f.passed and f.severity == "warn" for f in flags)


def _rate_basis_for(loan_type_value: str) -> tuple[float, str, int, str]:
    """Return (rate_pct, rate_basis, term_months, amortization) defaults.

    Illustrative — consistent with the `_debt_service_proxy` rates the
    rules engine uses so DSCR stays consistent across agents.
    """
    if loan_type_value == "bridge":
        return 6.50, "SOFR + 175bps (illustrative)", 24, "Interest-only"
    return 7.00, "10Y T-bill + 275bps (illustrative)", 120, "25-year amortization"


# --- Nodes ---


async def fetch_and_evaluate(state: DecisionState) -> DecisionState:
    """Re-evaluate rules from current extractions — same source as underwriting."""
    loan_id = uuid.UUID(state["loan_id"])
    async with get_session() as session:
        result = await evaluate_rules_against_loan(session, loan_id)
    return {
        **state,
        "extractions": result.extractions,
        "rule_outcomes": result.flags,
    }


async def draft_decision(state: DecisionState) -> DecisionState:
    """LLM picks the path + drafts the path-specific artifact."""
    flags = state.get("rule_outcomes", [])
    extractions = state.get("extractions", {})
    settings = get_settings()
    gateway = get_gateway()

    has_block = _has_blocking_failure(flags)
    has_missing = _has_missing_docs(flags)
    has_warn = _has_warning(flags)
    if has_block:
        allowed_paths = "decline"
        guidance = "At least one BLOCKING rule failed. You MUST recommend 'decline'."
    elif has_missing:
        allowed_paths = "conditional"
        guidance = (
            "Required documents are missing. Recommend 'conditional' with a "
            "condition for each missing document."
        )
    elif has_warn:
        allowed_paths = "approve | conditional"
        guidance = (
            "Warnings exist but no blocking failures. Choose 'conditional' if "
            "the warnings warrant explicit conditions; otherwise 'approve'."
        )
    else:
        allowed_paths = "approve"
        guidance = "All rules pass — recommend 'approve' with standard terms."

    rules_block = "\n".join(
        f"- {f.rule_id} ({f.severity}, {'PASS' if f.passed else 'FAIL'}): {f.message}"
        for f in flags
    )
    extractions_block = "\n".join(f"- {k}: {v}" for k, v in extractions.items())

    # 1) Pick the path + write the verdict.
    system_path = (
        "You are a senior credit officer making a decision recommendation. "
        "Hard rules:\n"
        "1. You may NOT contradict the rules engine's BLOCKING failures.\n"
        f"2. Allowed paths for THIS loan, per the engine: {allowed_paths}.\n"
        "3. Be decisive — verdict_text is a one-line action statement.\n"
        "4. Rationale names the specific rule outcomes that informed the "
        "   choice. Do NOT invent values.\n"
    )
    user_path = (
        f"{guidance}\n\n"
        f"Rule outcomes:\n{rules_block}\n\n"
        f"Accepted extractions:\n{extractions_block or '(none)'}\n\n"
        "Produce: path, confidence (your assessment, 0-1), a one-line verdict, "
        "and a rationale paragraph."
    )
    drafted: _DraftedDecision = await gateway.call_structured(
        model=settings.llm_heavy_model,
        system=system_path,
        user=user_path,
        schema=_DraftedDecision,
    )

    # Belt + suspenders: enforce engine verdict server-side too.
    if has_block and drafted.path != "decline":
        logger.warning(
            "decision_override_to_decline",
            attempted=drafted.path,
            reason="blocking_failure",
        )
        drafted.path = "decline"

    term_sheet: TermSheet | None = None
    conditions: list[ConditionDraft] = []
    aal: AdverseActionLetter | None = None

    if drafted.path in ("approve", "conditional"):
        loan_id = uuid.UUID(state["loan_id"])
        async with get_session() as session:
            loan = (await session.execute(select(Loan).where(Loan.id == loan_id))).scalar_one()
        rate_pct, rate_basis, term_months, amort = _rate_basis_for(loan.loan_type.value)

        ac_system = (
            "You draft a commercial loan term sheet and (if conditional) a "
            "list of conditions to close. Hard rules:\n"
            "1. Principal = the loan amount provided. Do not invent another.\n"
            "2. Use the provided rate basis exactly — do not invent market rates.\n"
            "3. For 'conditional' path: write 1–4 SPECIFIC conditions tied to "
            "   actual rule findings (missing docs, stale appraisal, etc.). "
            "   For 'approve' path: an empty conditions list.\n"
            "4. Conditions must be concrete and verifiable. Bad: "
            '"improve financials". Good: "Provide updated appraisal dated '
            'within 6 months of closing."'
        )
        ac_user = (
            f"Path: {drafted.path}.\n"
            f"Loan amount: ${float(loan.amount):,.0f}.\n"
            f"Loan type: {loan.loan_type.value}.\n"
            f"Suggested rate: {rate_pct}% ({rate_basis}).\n"
            f"Suggested term: {term_months} months, {amort}.\n\n"
            f"Rule outcomes (use to inform conditions):\n{rules_block}\n\n"
            "Draft origination_fee_pct (typical 1.0–1.75), prepay_terms "
            "(typical '3-2-1 step-down' or 'open after 12 months'). "
            "If conditional, list specific conditions; if approve, leave empty."
        )
        ac: _DraftedApproveConditional = await gateway.call_structured(
            model=settings.llm_default_model,
            system=ac_system,
            user=ac_user,
            schema=_DraftedApproveConditional,
        )
        # Pin principal server-side; the model shouldn't get to invent it.
        ac.term_sheet.principal = loan.amount
        term_sheet = ac.term_sheet
        conditions = ac.conditions if drafted.path == "conditional" else []

    elif drafted.path == "decline":
        failed_block_ids = [f.rule_id for f in flags if not f.passed and f.severity == "block"]
        failed_warn_ids = [f.rule_id for f in flags if not f.passed and f.severity == "warn"]
        candidate_reasons = failed_block_ids + failed_warn_ids
        reason_block = "\n".join(
            f"- {f.rule_id}: {f.message}" for f in flags if f.rule_id in candidate_reasons
        )

        dec_system = (
            "You draft an ADVERSE ACTION LETTER under ECOA Regulation B. "
            "Hard rules:\n"
            "1. `principal_reasons` MUST contain at least one rule_id from "
            "   the provided list of failed rules. The body MUST reference "
            '   each of those reasons by name (e.g. "Debt service coverage '
            "   ratio of 1.08 is below our underwriting floor of 1.20 for "
            '   multifamily").\n'
            "2. Do NOT cite 'internal policy', 'scoring', or 'creditworthiness' "
            "   as the reason. Reg B requires specific principal reasons.\n"
            "3. Professional, plain language. No jargon. ≤ 350 words.\n"
            "4. Inform the borrower of their right to request additional "
            "   information about the reasons within 60 days.\n"
            "5. Do NOT promise outcomes (e.g. 'you may reapply at any time' "
            "   is fine; 'we will approve a smaller loan' is not)."
        )
        dec_user = (
            f"Verdict rationale: {drafted.rationale}\n\n"
            f"Failed rules (cite at least one in principal_reasons; reference "
            f"each cited reason by name in the body):\n{reason_block}\n\n"
            "Subject line should be specific to the applicant and product."
        )
        dec: _DraftedDecline = await gateway.call_structured(
            model=settings.llm_heavy_model,
            system=dec_system,
            user=dec_user,
            schema=_DraftedDecline,
        )
        aal = dec.adverse_action_letter

    decision = DecisionResult(
        path=drafted.path,
        confidence=drafted.confidence,
        verdict_text=drafted.verdict_text,
        rationale=drafted.rationale,
        term_sheet=term_sheet,
        conditions=conditions,
        adverse_action_letter=aal,
        generated_at=datetime.now(UTC),
        agent_run_id=uuid.UUID(state.get("agent_run_id", str(uuid.uuid4()))),
    )
    return {**state, "decision": decision}


async def persist(state: DecisionState) -> DecisionState:
    """Write conditions (if any), an agent_runs row, and an audit event."""
    loan_id = uuid.UUID(state["loan_id"])
    decision = state["decision"]
    assert decision is not None, "draft_decision did not produce a decision"

    async with get_session() as session:
        # Clear previous AI-drafted conditions; the latest run replaces them.
        # Manually-added (non-AI) conditions are preserved.
        await session.execute(
            delete(Condition)
            .where(Condition.loan_id == loan_id)
            .where(Condition.drafted_by_agent == True)  # noqa: E712
        )

        for cond in decision.conditions:
            session.add(
                Condition(
                    loan_id=loan_id,
                    description=cond.description,
                    status=ConditionStatus.OPEN,
                    drafted_by_agent=True,
                )
            )

        agent_run = AgentRun(
            id=decision.agent_run_id,
            loan_id=loan_id,
            agent_name="decision",
            thread_id=f"decision-{loan_id}",
            status="complete",
            started_at=Decimal(int(decision.generated_at.timestamp())),
            payload={
                "path": decision.path,
                "confidence": decision.confidence,
                "verdict": decision.verdict_text,
                "n_conditions": len(decision.conditions),
                "has_term_sheet": decision.term_sheet is not None,
                "has_aal": decision.adverse_action_letter is not None,
            },
        )
        session.add(agent_run)

        await record(
            session,
            loan_id=loan_id,
            actor=Actor.agent("decision"),
            action="decision_complete",
            payload={
                "path": decision.path,
                "confidence": decision.confidence,
                "verdict": decision.verdict_text,
                "agent_run_id": str(decision.agent_run_id),
                "principal_reasons": (
                    decision.adverse_action_letter.principal_reasons
                    if decision.adverse_action_letter
                    else None
                ),
            },
        )

    return state


# --- Graph ---


@asynccontextmanager
async def build_decision_graph() -> AsyncIterator[Any]:
    """Yield a compiled decision agent graph. Same context-manager pattern as the others."""
    settings = get_settings()

    builder: StateGraph = StateGraph(DecisionState)
    builder.add_node("fetch_and_evaluate", fetch_and_evaluate)
    builder.add_node("draft_decision", draft_decision)
    builder.add_node("persist", persist)

    builder.add_edge(START, "fetch_and_evaluate")
    builder.add_edge("fetch_and_evaluate", "draft_decision")
    builder.add_edge("draft_decision", "persist")
    builder.add_edge("persist", END)

    async with AsyncPostgresSaver.from_conn_string(settings.database_url_libpq) as checkpointer:
        await checkpointer.setup()
        yield builder.compile(checkpointer=checkpointer)
