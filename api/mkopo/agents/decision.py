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
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update

from mkopo.agents._base import build_compiled_graph
from mkopo.agents.guardrails import (
    ADVERSE_ACTION_LETTER_CONSTITUTION,
    DECISION_VERDICT_CONSTITUTION,
    JudgmentSpec,
    make_validator_node,
    make_validator_router,
)
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
    # Self-correction loop bookkeeping. See :mod:`agents.guardrails`
    # for the pattern (Constitutional AI + LLM-as-Judge + Self-Refine).
    # ``validation_attempts`` is incremented on each draft_decision
    # invocation so the conditional edge knows when to give up.
    # ``last_critique`` is the previous judgment's critique text; the
    # drafter includes it in the next attempt's prompt so the LLM has
    # concrete guidance on what to change. ``last_judgment`` is the
    # most recent verdict — persisted onto agent_runs.payload so the
    # observability page can render it.
    validation_attempts: int
    last_critique: str | None
    last_judgment: dict | None


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
    """Re-evaluate rules from current extractions — same source as underwriting.

    Pre-flight gate: the decision agent only makes sense once
    underwriting has run, because its prompt anchors on the cited
    summary's recommendation. Without an ``underwriting_complete``
    audit event the LLM would have to guess from raw extractions —
    expensive and produces low-quality recommendations.

    Marks ``status='needs_underwriting'`` and short-circuits so the
    UI can guide the user to the underwriting tab first.
    """
    loan_id = uuid.UUID(state["loan_id"])
    async with get_session() as session:
        from sqlalchemy import select

        from mkopo.models import AuditEvent

        underwriting_ran = (
            await session.execute(
                select(AuditEvent.id)
                .where(
                    AuditEvent.loan_id == loan_id,
                    AuditEvent.action == "underwriting_complete",
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        if not underwriting_ran:
            await record(
                session,
                loan_id=loan_id,
                actor=Actor.agent("decision"),
                action="decision_skipped",
                payload={"reason": "needs_underwriting"},
            )
            return {**state, "status": "needs_underwriting"}

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
    #
    # The static persona + hard-rules portion of the prompt comes from
    # the prompts registry (editable through the /prompts UI). The
    # dynamic "allowed paths for THIS loan" line is appended in code
    # because it's not editorial — it's a derived rule-engine fact
    # the LLM needs to honour, and exposing it as editable text would
    # let a well-meaning edit corrupt the engine integration.
    from mkopo.services.prompts import get as get_prompt

    system_path = (
        get_prompt("decision.path_selection")
        + f"\n\nAllowed paths for THIS loan, per the engine: {allowed_paths}."
    )
    user_path = (
        f"{guidance}\n\n"
        f"Rule outcomes:\n{rules_block}\n\n"
        f"Accepted extractions:\n{extractions_block or '(none)'}\n\n"
        "Produce: path, confidence (your assessment, 0-1), a one-line "
        "verdict (≤120 characters), and a rationale paragraph (≤800 "
        "characters — be specific but tight; the verdict carries the "
        "headline and the rationale only needs to enumerate the "
        "decisive rule outcomes and one or two risk-mitigant notes)."
    )
    # Self-Refine pattern (Madaan et al. 2023): if the previous
    # attempt was rejected by the guardrail judge, fold its critique
    # into the prompt so the new draft can target the failure.
    # ``last_critique`` is set by the validate_decision node when
    # it loops back here; on the first attempt it's None and this
    # block is a no-op.
    last_critique = state.get("last_critique")
    if last_critique:
        user_path += (
            "\n\nIMPORTANT — previous draft was rejected by the "
            "guardrail judge:\n"
            f"\"{last_critique}\"\n\n"
            "Revise to address the specific failure above. Do not "
            "repeat the same mistake."
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

        ac_system = get_prompt("decision.approve_conditional")
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
        # Give the LLM both the engine rule_id (for the structured
        # ``principal_reasons`` output) AND a friendly label (so the
        # body prose has natural language to anchor to). Without
        # this the LLM tends to fall back to writing the rule_id in
        # the body as ``"... (doc_completeness)"`` because it's the
        # only string we showed it.
        from mkopo.services.rules_eval import friendly_rule_label

        reason_block = "\n".join(
            (
                f"- rule_id={f.rule_id} (friendly label for prose: "
                f"\"{friendly_rule_label(f.rule_id)}\"): {f.message}"
            )
            for f in flags
            if f.rule_id in candidate_reasons
        )

        # Load every real identifier the AAL needs: the borrower's
        # name, the loan reference + amount + property type, the
        # institution's lender block + authorized officer + credit
        # reporting agency. Threading these into the user message
        # via the "Real identifiers" block is what lets the prompt
        # forbid bracketed placeholders — same pattern the intake
        # email already uses.
        from mkopo.models import LoanParty, Party, PartyRole
        from mkopo.services.institution import (
            get_institution,
            materials_block,
        )

        loan_id = uuid.UUID(state["loan_id"])
        async with get_session() as session:
            loan = (
                await session.execute(select(Loan).where(Loan.id == loan_id))
            ).scalar_one()
            borrower_row = (
                await session.execute(
                    select(Party)
                    .join(LoanParty, LoanParty.party_id == Party.id)
                    .where(
                        LoanParty.loan_id == loan_id,
                        LoanParty.role == PartyRole.BORROWER,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            institution = await get_institution(session)

        applicant_name = (
            borrower_row.name if borrower_row else "(applicant name not on file)"
        )
        # Property type is in extractions if intake ran; default to
        # the loan_type as a degraded fallback so the prompt still
        # has something to interpolate.
        ext = state.get("extractions", {}) or {}
        property_type = ext.get("property_type") or loan.loan_type.value

        identifier_block = materials_block(institution)
        # Compose the full user message: rules + rationale + the
        # "Real identifiers" block at the top so the LLM treats it
        # as the source of truth for letter values.
        dec_system = get_prompt("decision.decline_letter")
        dec_user = (
            f"{identifier_block}\n"
            f"- Applicant name: {applicant_name}\n"
            f"- Loan reference: {loan.reference}\n"
            f"- Requested loan amount: ${float(loan.amount):,.0f}\n"
            f"- Property type: {property_type}\n\n"
            f"Verdict rationale: {drafted.rationale}\n\n"
            f"Failed rules (cite at least one in principal_reasons; reference "
            f"each cited reason by name in the body):\n{reason_block}\n\n"
            "Subject line should be specific to the applicant and product. "
            "Body must read as a finished letter — no [BRACKETED] placeholders "
            "anywhere."
        )
        dec: _DraftedDecline = await gateway.call_structured(
            model=settings.llm_heavy_model,
            system=dec_system,
            user=dec_user,
            schema=_DraftedDecline,
        )
        aal = dec.adverse_action_letter

    # See underwriting.py:_persist for the rationale on the missing-
    # agent_run_id branch — same pattern: log loudly rather than
    # silently fabricate, so the orphan row is detectable in
    # observability instead of disappearing into the void.
    raw_run_id = state.get("agent_run_id")
    if raw_run_id:
        result_run_id = uuid.UUID(raw_run_id)
    else:
        result_run_id = uuid.uuid4()
        logger.warning(
            "decision_persist_missing_agent_run_id",
            loan_id=state.get("loan_id"),
            fabricated_id=str(result_run_id),
        )
    decision = DecisionResult(
        path=drafted.path,
        confidence=drafted.confidence,
        verdict_text=drafted.verdict_text,
        rationale=drafted.rationale,
        term_sheet=term_sheet,
        conditions=conditions,
        adverse_action_letter=aal,
        generated_at=datetime.now(UTC),
        agent_run_id=result_run_id,
    )
    # Bump the attempt counter. The validator reads this to decide
    # when to stop retrying (max set by MAX_VALIDATION_ATTEMPTS).
    attempts = state.get("validation_attempts", 0) + 1
    return {**state, "decision": decision, "validation_attempts": attempts}


# --- Self-correction loop (LLM-as-judge + Self-Refine) -----------------------
#
# The validator node + the conditional-edge router are both built
# from factories in :mod:`agents.guardrails` so the intake and
# underwriting agents can reuse the same loop pattern. See the
# module docstring there for the rationale.


def _decision_judge_context(state: DecisionState) -> str:
    """Shared context block for both the verdict and AAL judges.

    Mirrors the drafter's ``Real identifiers`` block so the judge
    has the same source of truth for principles like "addressed by
    name". The block lists rule outcomes (so the judge can verify
    the rationale references them) plus the path + confidence the
    drafter chose.
    """
    decision = state.get("decision")
    flags = state.get("rule_outcomes", []) or []
    rules_summary = "\n".join(
        f"- {f.rule_id} (severity={f.severity}, passed={f.passed}): {f.message}"
        for f in flags
    )
    path = decision.path if decision else "(no decision)"
    confidence = decision.confidence if decision else "(no decision)"
    return (
        f"Loan rule outcomes:\n{rules_summary}\n\n"
        f"Decision path chosen: {path}\n"
        f"Confidence: {confidence}\n"
    )


def _extract_verdict_text(state: DecisionState) -> str | None:
    """Verdict text is judged on every path."""
    decision = state.get("decision")
    if decision is None:
        return None
    return (
        f"Verdict: {decision.verdict_text}\n\nRationale: {decision.rationale}"
    )


def _extract_aal_text(state: DecisionState) -> str | None:
    """AAL text is only judged when present (decline path only)."""
    decision = state.get("decision")
    if decision is None or decision.adverse_action_letter is None:
        return None
    aal = decision.adverse_action_letter
    return f"Subject: {aal.subject}\n\n{aal.body_text}"


# Validator node: runs both judgments, picks the worst severity,
# writes ``last_judgment`` + ``last_critique`` back into state.
# Built once at import time and used as a regular LangGraph node.
validate_decision = make_validator_node(
    (
        JudgmentSpec(
            constitution=DECISION_VERDICT_CONSTITUTION,
            extract_text=_extract_verdict_text,
            extract_context=_decision_judge_context,
        ),
        JudgmentSpec(
            constitution=ADVERSE_ACTION_LETTER_CONSTITUTION,
            extract_text=_extract_aal_text,
            extract_context=_decision_judge_context,
        ),
    )
)


# Router: block → loop back to draft_decision (bounded by
# MAX_VALIDATION_ATTEMPTS); ok/warn → persist.
route_after_validate = make_validator_router(
    retry_node="draft_decision",
    persist_node="persist",
)


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

        # Snapshot the materials hash so the stage-transition guard
        # can later refuse to advance the loan if anything that fed
        # this decision has changed. Computed inside the same session
        # so all SELECTs see a consistent view.
        from mkopo.services.materials_hash import compute_materials_hash

        materials_hash = await compute_materials_hash(session, loan_id)

        # The AgentRun row was created by the streaming layer at the
        # start of this run; we update it here with the final payload.
        # UPDATE-by-id instead of INSERT keeps the row's id stable for
        # any AgentStep rows that already point at it.
        # Persist the FULL Pydantic dump under ``result_json`` so the
        # decision panel can rehydrate it on page reload without
        # forcing the underwriter to re-run the agent (which wastes
        # tokens and confuses users — "I already saw this verdict,
        # where did it go?"). The summary keys above are kept for
        # the eval / observability code paths that read them
        # directly without unmarshalling the full DecisionResult.
        result_json = decision.model_dump(mode="json")
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == decision.agent_run_id)
            .values(
                status="complete",
                started_at=Decimal(int(decision.generated_at.timestamp())),
                payload={
                    "path": decision.path,
                    "confidence": decision.confidence,
                    "verdict": decision.verdict_text,
                    "n_conditions": len(decision.conditions),
                    "has_term_sheet": decision.term_sheet is not None,
                    "has_aal": decision.adverse_action_letter is not None,
                    "materials_hash": materials_hash,
                    "result_json": result_json,
                    # Guardrail judgment — captured by validate_decision.
                    # Lands on the observability page so the reviewer can
                    # see whether the constitutional judge passed/warned/
                    # gave up on this draft + the critique it produced.
                    "guardrail_judgment": state.get("last_judgment"),
                    "validation_attempts": state.get(
                        "validation_attempts", 0
                    ),
                },
            )
        )

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
    builder: StateGraph = StateGraph(DecisionState)
    builder.add_node("fetch_and_evaluate", fetch_and_evaluate)
    builder.add_node("draft_decision", draft_decision)
    # Validator node — runs the LLM-as-judge with the constitution.
    # Sits between draft and persist; on block-severity failure the
    # conditional edge routes back to draft_decision (Self-Refine),
    # bounded by MAX_VALIDATION_ATTEMPTS.
    builder.add_node("validate_decision", validate_decision)
    builder.add_node("persist", persist)

    builder.add_edge(START, "fetch_and_evaluate")

    def route_after_eval(state: DecisionState) -> str:
        """Pre-flight short-circuit. If underwriting hasn't run yet
        we skip the LLM draft — its prompt anchors on the cited
        summary, which doesn't exist yet."""
        if state.get("status") == "needs_underwriting":
            return END
        return "draft_decision"

    builder.add_conditional_edges(
        "fetch_and_evaluate",
        route_after_eval,
        {END: END, "draft_decision": "draft_decision"},
    )
    # draft → validate → (persist | back to draft). The conditional
    # edge is the entire self-correction mechanism — no special-case
    # loop construct needed; LangGraph supports cycles natively.
    builder.add_edge("draft_decision", "validate_decision")
    builder.add_conditional_edges(
        "validate_decision",
        route_after_validate,
        {"draft_decision": "draft_decision", "persist": "persist"},
    )
    builder.add_edge("persist", END)

    async with build_compiled_graph(builder) as graph:
        yield graph
