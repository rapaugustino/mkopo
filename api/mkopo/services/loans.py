"""Loan service. All stage transitions flow through here so the audit log is automatic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import (
    VALID_TRANSITIONS,
    Document,
    Extraction,
    ExtractionStatus,
    Loan,
    LoanStage,
)
from mkopo.services.audit import Actor, record


class IllegalStageTransitionError(Exception):
    """Raised when a transition is illegal — either the (from, to) edge
    isn't in ``VALID_TRANSITIONS`` or the destination's prerequisites
    aren't satisfied yet.

    The message is meant to be shown to the user verbatim, so it
    explains *what* is missing rather than just naming the rule.
    """


# ----- prerequisite checks ----------------------------------------------
#
# Each function returns ``None`` when the prerequisite is satisfied or a
# human-readable error message otherwise. The orchestrator (below) runs
# the matching check per destination stage and aggregates messages.
#
# Why each check exists:
#
# - **intake → underwriting** requires at least one document and at
#   least one ACCEPTED or OVERRIDDEN extraction. Moving forward
#   without extractions leaves the rules engine with nothing to
#   evaluate against, and the underwriting agent will fail on the
#   first node.
#
# - **underwriting → decision** requires an underwriting result on
#   the loan — the recommendation drives the decision agent's prompt
#   and the cited summary is what the decision panel surfaces to the
#   underwriter.
#
# - **decision → conditions / approved / declined** requires a
#   decision result and ensures the destination matches the AI's
#   drafted path. Underwriters can override the path, but only by
#   re-running the decision agent.


async def _has_accepted_extraction(session: AsyncSession, loan_id: uuid.UUID) -> bool:
    """True if at least one extraction is ACCEPTED or OVERRIDDEN.

    OVERRIDDEN counts because a human-corrected extraction is
    authoritative — that's the whole point of the review queue.
    """
    stmt = (
        select(Extraction.id)
        .join(Document)
        .where(
            Document.loan_id == loan_id,
            Extraction.status.in_(
                (ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN)
            ),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _has_documents(session: AsyncSession, loan_id: uuid.UUID) -> bool:
    stmt = select(Document.id).where(Document.loan_id == loan_id).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _missing_required_docs(
    session: AsyncSession, loan: Loan
) -> set[str]:
    """Return the set of required document types missing for this loan
    class, or an empty set if the packet is complete.

    Mirrors the rules engine's later doc-completeness check (which fires
    in underwriting) so the borrower / loan officer gets the same gate
    surfaced *before* underwriting starts — far more useful as a "you're
    missing X" prompt than as a warn-severity rule outcome after the
    LLM has already drafted a summary against an incomplete packet.

    The set comparison is on the underlying doc_type string. Document.doc_type
    is declared ``Mapped[DocumentType]`` but the column is ``String(64)``,
    so SQLAlchemy returns plain strings on read — see services/rules_eval.py
    for the same pattern.
    """
    from mkopo.rules.policy import REQUIRED_DOCS, REQUIRED_DOCS_PERSONAL

    loan_class_str = (
        loan.loan_class.value if loan.loan_class is not None else "business"
    )
    required = (
        REQUIRED_DOCS_PERSONAL if loan_class_str == "personal" else REQUIRED_DOCS
    )

    docs_q = select(Document.doc_type).where(Document.loan_id == loan.id)
    present = set((await session.execute(docs_q)).scalars().all())
    return required - present


# Stub flags. The "decision result" and "underwriting result" objects
# live in LangGraph checkpoint state, not in their own tables — we
# don't have a cheap synchronous way to inspect them from here.
# Pragmatic substitute: check for the matching audit_events that the
# agents emit on completion. This is honest about reality (a decision
# only "exists" once it's been recorded) without coupling this module
# to LangGraph internals.


async def _has_audit_action(
    session: AsyncSession, loan_id: uuid.UUID, action: str
) -> bool:
    from mkopo.models import AuditEvent

    stmt = (
        select(AuditEvent.id)
        .where(AuditEvent.loan_id == loan_id, AuditEvent.action == action)
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _materials_drift_message(
    session: AsyncSession, loan_id: uuid.UUID
) -> str | None:
    """Return a user-readable message when the loan's current
    materials no longer match the materials the latest decision was
    made against; ``None`` otherwise.

    "Drift" is what changed: a document swapped, an extraction
    overridden, a meta field updated, a guarantor added/removed. Any
    of those between decision and the next forward transition means
    we'd be acting on a stale recommendation — so we refuse with a
    message that tells the user exactly what to do (re-run the
    decision agent).

    Only fires when a decision actually exists for the loan. Loans
    that haven't reached decision yet get ``None`` here regardless of
    document churn — the protection only matters once a decision is
    on file.
    """
    from mkopo.services.materials_hash import materials_drift_detected

    drifted, _current, _previous = await materials_drift_detected(session, loan_id)
    if drifted:
        return (
            "Materials have changed since the decision was made — "
            "an extraction, document, or borrower-supplied field was "
            "updated. Re-run the decision agent so the recommendation "
            "reflects the current loan packet."
        )
    return None


async def check_prerequisites(
    session: AsyncSession, loan: Loan, to_stage: LoanStage
) -> str | None:
    """Return an error message if the loan isn't ready for ``to_stage``,
    or ``None`` if it is.

    Centralised so the same check runs:
    - from ``transition_stage`` (the write path)
    - from the UI via ``GET /loans/{id}/transitions`` (so the case file
      header can show *why* a button is disabled before the user clicks)
    - from the autonomous orchestrator (so it knows whether to chain
      forward automatically)
    """
    loan_id = loan.id

    if to_stage == LoanStage.UNDERWRITING:
        if not await _has_documents(session, loan_id):
            return "No documents uploaded yet — add the loan packet first."
        # Class-aware document completeness. We surface the same gate
        # the rules engine enforces in underwriting (rule_doc_completeness
        # / rule_personal_doc_completeness) early — refusing the
        # forward transition with a specific "you're missing X, Y" list
        # is much more actionable than letting underwriting run on an
        # incomplete packet and emitting a warn-severity rule outcome.
        missing = await _missing_required_docs(session, loan)
        if missing:
            human_missing = ", ".join(
                sorted(name.replace("_", " ") for name in missing)
            )
            class_label = (
                "personal"
                if loan.loan_class is not None
                and loan.loan_class.value == "personal"
                else "commercial"
            )
            return (
                f"Missing required {class_label}-loan document(s): "
                f"{human_missing}. Upload these before moving to underwriting."
            )
        if not await _has_accepted_extraction(session, loan_id):
            return (
                "Intake hasn't produced any accepted extractions yet. "
                "Run the intake agent, or accept fields manually in the "
                "review queue."
            )
        return None

    if to_stage == LoanStage.DECISION:
        # The underwriting agent writes "underwriting_complete" on its
        # persist node. Until that exists the decision agent has
        # nothing to anchor its prompt to.
        if not await _has_audit_action(session, loan_id, "underwriting_complete"):
            return (
                "Underwriting agent hasn't completed yet. "
                "Run underwriting first to produce a cited summary and "
                "risk-flag set."
            )
        return None

    if to_stage in (LoanStage.CONDITIONS, LoanStage.APPROVED):
        if not await _has_audit_action(session, loan_id, "decision_complete"):
            return (
                "Decision agent hasn't drafted a recommendation yet. "
                "Run the decision agent first."
            )
        # Materials-drift gate: if the inputs that fed the latest
        # decision have changed (a document was swapped, an income
        # field was edited, an extraction was overridden), the
        # decision is stale and must not be acted on. Re-run the
        # decision agent to produce a fresh recommendation against
        # the current materials.
        drift_msg = await _materials_drift_message(session, loan_id)
        if drift_msg:
            return drift_msg
        return None

    if to_stage == LoanStage.DECLINED:
        # Declines can happen from any prior stage — they're the
        # universal early-exit — so no prerequisite beyond the
        # VALID_TRANSITIONS edge check.
        return None

    if to_stage == LoanStage.CLOSING:
        # Coming from conditions: every condition must be satisfied
        # or waived (the conditions table tracks this). Coming from
        # approved: no extra check.
        from mkopo.models import Condition

        if loan.stage == LoanStage.CONDITIONS:
            open_conds = (
                await session.execute(
                    select(Condition.id).where(
                        Condition.loan_id == loan_id, Condition.status == "open"
                    )
                )
            ).scalars().all()
            if open_conds:
                n = len(open_conds)
                return (
                    f"{n} condition{'' if n == 1 else 's'} still open. Mark them "
                    "satisfied or waived before closing."
                )
        # Closing is the last gate before funding — materials drift
        # here would mean we're funding a loan on data the decision
        # was never made against. Refuse and force re-decision.
        drift_msg = await _materials_drift_message(session, loan_id)
        if drift_msg:
            return drift_msg
        return None

    # No prerequisites for SERVICING beyond the VALID_TRANSITIONS edge.
    return None


# ----- main transition entrypoint ---------------------------------------


async def transition_stage(
    session: AsyncSession,
    *,
    loan_id: uuid.UUID,
    to_stage: LoanStage,
    actor: Actor,
    reason: str,
    skip_prereqs: bool = False,
) -> Loan:
    """Move a loan to a new stage. Validates and writes the audit event.

    Two layers of validation:

    1. **Edge legality** — must be in ``VALID_TRANSITIONS`` for the
       loan's current stage.
    2. **Prerequisite checks** — destination-specific (see
       ``check_prerequisites``). The CLI / seed scripts can bypass
       these via ``skip_prereqs=True`` when bootstrapping.

    This is the ONLY way ``loan.stage`` should ever change. Also resets
    ``stage_entered_at`` so pipeline aging is correct.
    """
    stmt = select(Loan).where(Loan.id == loan_id).with_for_update()
    loan = (await session.execute(stmt)).scalar_one()

    from_stage = loan.stage
    if to_stage not in VALID_TRANSITIONS.get(from_stage, set()):
        allowed = (
            sorted(s.value for s in VALID_TRANSITIONS.get(from_stage, set()))
            or "none (terminal)"
        )
        raise IllegalStageTransitionError(
            f"Cannot move from {from_stage.value} to {to_stage.value}. "
            f"Allowed next stages: {allowed}."
        )

    if not skip_prereqs:
        msg = await check_prerequisites(session, loan, to_stage)
        if msg:
            raise IllegalStageTransitionError(msg)

    loan.stage = to_stage
    loan.stage_entered_at = datetime.now(UTC)
    await record(
        session,
        loan_id=loan_id,
        actor=actor,
        action="stage_transition",
        payload={
            "from_stage": from_stage.value,
            "to_stage": to_stage.value,
            "reason": reason,
        },
    )
    return loan


async def allowed_transitions(
    session: AsyncSession, loan: Loan
) -> dict[str, str | None]:
    """For each legal next stage, return either ``None`` (ready) or the
    reason it's not ready yet. Drives the UI's disabled-button tooltips.

    Returns a mapping keyed by stage value (``"underwriting"``,
    ``"declined"`` etc.) so the frontend can render in one pass.
    """
    out: dict[str, str | None] = {}
    for next_stage in VALID_TRANSITIONS.get(loan.stage, set()):
        out[next_stage.value] = await check_prerequisites(session, loan, next_stage)
    return out


async def get_loan(session: AsyncSession, loan_id: uuid.UUID) -> Loan | None:
    result = await session.execute(select(Loan).where(Loan.id == loan_id))
    return result.scalar_one_or_none()
