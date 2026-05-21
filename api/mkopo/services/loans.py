"""Loan service. All stage transitions flow through here so the audit log is automatic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import VALID_TRANSITIONS, Loan, LoanStage
from mkopo.services.audit import Actor, record


class IllegalStageTransitionError(Exception):
    """Raised when code attempts a transition not in VALID_TRANSITIONS."""


async def transition_stage(
    session: AsyncSession,
    *,
    loan_id: uuid.UUID,
    to_stage: LoanStage,
    actor: Actor,
    reason: str,
) -> Loan:
    """Move a loan to a new stage. Validates transition and writes audit event.

    This is the ONLY way loan.stage should ever change. Also resets
    `stage_entered_at` so pipeline aging is correct.
    """
    stmt = select(Loan).where(Loan.id == loan_id).with_for_update()
    result = await session.execute(stmt)
    loan = result.scalar_one()

    from_stage = loan.stage
    if to_stage not in VALID_TRANSITIONS.get(from_stage, set()):
        raise IllegalStageTransitionError(
            f"Cannot transition loan {loan_id} from {from_stage.value} to {to_stage.value}. "
            f"Allowed: {[s.value for s in VALID_TRANSITIONS.get(from_stage, set())]}"
        )

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


async def get_loan(session: AsyncSession, loan_id: uuid.UUID) -> Loan | None:
    result = await session.execute(select(Loan).where(Loan.id == loan_id))
    return result.scalar_one_or_none()
