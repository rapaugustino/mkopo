"""Communication, condition tracking, and audit log models."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mkopo.models.base import Base

if TYPE_CHECKING:
    from mkopo.models.loan import Loan


class MessageDirection(enum.StrEnum):
    OUTBOUND = "outbound"
    # ``INBOUND`` is no longer written by any code path — Mkopo
    # decided against parsing email replies (borrowers reply in-app,
    # not by email). The value stays on the enum so historical rows
    # still load; new rows should never use it.
    INBOUND = "inbound"
    INTERNAL = "internal"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_loan_created", "loan_id", "created_at"),)

    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    direction: Mapped[MessageDirection] = mapped_column(String(16), nullable=False)
    sender: Mapped[str] = mapped_column(String(256), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(256))
    subject: Mapped[str | None] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resend_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    drafted_by_agent: Mapped[bool] = mapped_column(default=False, nullable=False)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column()

    loan: Mapped["Loan"] = relationship(back_populates="messages")


class ConditionStatus(enum.StrEnum):
    OPEN = "open"
    SATISFIED = "satisfied"
    WAIVED = "waived"


class Condition(Base):
    """Conditions to close, generated at the decision stage."""

    __tablename__ = "conditions"

    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ConditionStatus] = mapped_column(
        String(32), nullable=False, default=ConditionStatus.OPEN
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drafted_by_agent: Mapped[bool] = mapped_column(default=False, nullable=False)

    loan: Mapped["Loan"] = relationship(back_populates="conditions")


class ActorType(enum.StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    # The borrower acting on their own loan via the self-service
    # portal. Lets the case-file timeline colour-code their events
    # distinctly from internal underwriter actions.
    BORROWER = "borrower"


class AuditEvent(Base):
    """Immutable record of every action on a loan. Append-only."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_loan_at", "loan_id", "created_at"),
        Index("ix_audit_events_actor", "actor_type", "actor_id"),
    )

    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_type: Mapped[ActorType] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    loan: Mapped["Loan"] = relationship(back_populates="audit_events")
