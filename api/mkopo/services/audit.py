"""Audit service. Every action on a loan flows through here for logging."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import ActorType, AuditEvent

logger = structlog.get_logger()


class Actor:
    """Lightweight actor identity for audit logging."""

    def __init__(self, actor_type: ActorType, actor_id: str) -> None:
        self.actor_type = actor_type
        self.actor_id = actor_id

    @classmethod
    def user(cls, user_id: str) -> Actor:
        return cls(ActorType.USER, user_id)

    @classmethod
    def agent(cls, agent_name: str) -> Actor:
        return cls(ActorType.AGENT, agent_name)

    @classmethod
    def system(cls) -> Actor:
        return cls(ActorType.SYSTEM, "system")

    @classmethod
    def borrower(cls, email: str) -> Actor:
        """The borrower acting on their own loan via the self-service
        portal. We key by email since the borrower portal doesn't
        require an internal user account — this is enough to dedupe
        the actor across multiple events.
        """
        return cls(ActorType.BORROWER, email)


async def record(
    session: AsyncSession,
    *,
    loan_id: uuid.UUID,
    actor: Actor,
    action: str,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Write an audit event. Caller is responsible for the session commit."""
    event = AuditEvent(
        loan_id=loan_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=action,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    logger.info(
        "audit_event",
        loan_id=str(loan_id),
        actor_type=actor.actor_type.value,
        actor_id=actor.actor_id,
        action=action,
    )
    return event
