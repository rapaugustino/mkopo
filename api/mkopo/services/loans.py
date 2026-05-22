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
        raise IllegalStageTransitionError(
            f"Cannot move from {from_stage.value} to {to_stage.value}. "
            f"Allowed next stages: {sorted(s.value for s in VALID_TRANSITIONS.get(from_stage, set())) or 'none (terminal)'}."
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
