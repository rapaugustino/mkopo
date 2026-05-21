"""Loan entity and its lifecycle stage."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mkopo.models.base import Base

if TYPE_CHECKING:
    from mkopo.models.audit import AuditEvent, Condition, Message
    from mkopo.models.document import Document
    from mkopo.models.party import LoanParty, Party
    from mkopo.models.user import User


class LoanStage(enum.StrEnum):
    INTAKE = "intake"
    UNDERWRITING = "underwriting"
    DECISION = "decision"
    CONDITIONS = "conditions"
    CLOSING = "closing"
    SERVICING = "servicing"
    DECLINED = "declined"
    APPROVED = "approved"


class LoanType(enum.StrEnum):
    BRIDGE = "bridge"
    PERMANENT = "permanent"
    CONSTRUCTION = "construction"
    REFINANCE = "refinance"


# Legal stage transitions. Centralizing this prevents illegal states.
VALID_TRANSITIONS: dict[LoanStage, set[LoanStage]] = {
    LoanStage.INTAKE: {LoanStage.UNDERWRITING, LoanStage.DECLINED},
    LoanStage.UNDERWRITING: {LoanStage.DECISION, LoanStage.DECLINED},
    LoanStage.DECISION: {LoanStage.CONDITIONS, LoanStage.APPROVED, LoanStage.DECLINED},
    LoanStage.CONDITIONS: {LoanStage.CLOSING, LoanStage.DECLINED},
    LoanStage.APPROVED: {LoanStage.CLOSING},
    LoanStage.CLOSING: {LoanStage.SERVICING},
    LoanStage.SERVICING: set(),
    LoanStage.DECLINED: set(),
}


class Loan(Base):
    __tablename__ = "loans"

    # Human-facing identifier (LN-YYYY-NNNN), server-generated via a Postgres
    # sequence. UUID `id` stays the primary key — `reference` is what appears
    # in the UI, audit log, and external comms.
    reference: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(
            "'LN-' || EXTRACT(YEAR FROM CURRENT_DATE)::text || '-' || "
            "LPAD(NEXTVAL('loan_reference_seq')::text, 4, '0')"
        ),
    )
    # `native_enum=False` keeps the DB column as VARCHAR (matches existing
    # schema) but tells SQLAlchemy to coerce loaded strings back into the
    # Python StrEnum on read — otherwise `loan.stage.value` fails because
    # SQLAlchemy returns the raw string.
    stage: Mapped[LoanStage] = mapped_column(
        SAEnum(
            LoanStage,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=LoanStage.INTAKE,
        index=True,
    )
    loan_type: Mapped[LoanType] = mapped_column(
        SAEnum(
            LoanType,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status_detail: Mapped[str | None] = mapped_column(String(256))
    # Coarse rating bucket: low | med | high. Set by the underwriting agent.
    risk_band: Mapped[str | None] = mapped_column(String(8))
    # When the loan entered its CURRENT stage — used for "3d in underwriting"
    # type aging on the pipeline. `transition_stage` updates this.
    stage_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    # Assigned underwriter / loan owner. Nullable to allow loans without an
    # owner (intake before assignment).
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Embedding of the underwriting summary, used for comparable-loans
    # kNN. Populated by the underwriting agent's persist step. 1024 dims
    # to match config.embeddings_dimensions + migration 0003.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)

    # Relationships
    owner: Mapped["User | None"] = relationship(lazy="joined")
    # Eager-load parties + their Party so the pipeline / case file can show
    # the borrower without an N+1. Cost is fine — every loan has ~1-3 parties.
    parties: Mapped[list["LoanParty"]] = relationship(
        back_populates="loan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def borrower(self) -> "Party | None":
        """The BORROWER party on this loan, if any. Returns the underlying Party
        (not the LoanParty join row) so the BorrowerOut schema can pull
        `id`/`name`/`party_type` straight off it via from_attributes.
        """
        # Imported here to avoid a circular import (party imports Loan for typing).
        from mkopo.models.party import PartyRole

        for lp in self.parties:
            if lp.role == PartyRole.BORROWER:
                return lp.party
        return None

    @property
    def guarantors(self) -> list["Party"]:
        """All GUARANTOR parties on this loan. Drives the loan-header chip row
        — each chip links to /parties/[id]."""
        from mkopo.models.party import PartyRole

        return [lp.party for lp in self.parties if lp.role == PartyRole.GUARANTOR]

    documents: Mapped[list["Document"]] = relationship(
        back_populates="loan", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="loan", cascade="all, delete-orphan"
    )
    conditions: Mapped[list["Condition"]] = relationship(
        back_populates="loan", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="loan", cascade="all, delete-orphan"
    )


class AgentRun(Base):
    """Tracks LangGraph agent runs against loans."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_loan_started", "loan_id", "started_at"),)

    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[Decimal] = mapped_column(Numeric, nullable=True)  # type: ignore
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
