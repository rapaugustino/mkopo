"""Loan entity and its lifecycle stage."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, text
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
    # Terminal stage when the borrower self-cancels their application.
    # Set by ``POST /borrower-auth/me/loans/{id}/withdraw``. Distinct
    # from ``DECLINED`` because the lender didn't reject — the
    # borrower walked away. Tracked separately for HMDA reporting
    # (different "application result" code) and so the timeline reads
    # "Withdrawn by borrower" rather than "Declined".
    WITHDRAWN = "withdrawn"


class LoanType(enum.StrEnum):
    BRIDGE = "bridge"
    PERMANENT = "permanent"
    CONSTRUCTION = "construction"
    REFINANCE = "refinance"


class AgentName(enum.StrEnum):
    """The three agents in the loan-origination pipeline.

    Centralised here so route handlers, observability filters, eval
    task names, and audit payloads can share a single constant rather
    than each site spelling ``"intake"`` / ``"underwriting"`` /
    ``"decision"`` as a string literal. A typo in any of those
    silently routes to the wrong agent / drops a metric / lands an
    unrouted observability row; an unknown-enum-member raises.

    Each member's ``.value`` matches the agent's identifier
    everywhere downstream — ``agent_runs.agent_name``, the eval
    task-name prefix (``intake.email``, ``underwriting.summary``,
    ``decision.verdict``), the SSE event ``agent`` field, the
    orchestrator's chain-routing decisions. StrEnum so existing
    string-comparison sites keep working without migration.

    Pipeline order is set by ``PIPELINE_ORDER`` below — single source
    of truth for the orchestrator's chain + the UI's stepper.
    """

    INTAKE = "intake"
    UNDERWRITING = "underwriting"
    DECISION = "decision"


# Canonical pipeline order. The orchestrator + the loan-detail UI's
# stepper both consume this so adding a stage (e.g. "verification" pre-
# decision) is a one-place edit instead of a grep-and-pray.
PIPELINE_ORDER: tuple[AgentName, ...] = (
    AgentName.INTAKE,
    AgentName.UNDERWRITING,
    AgentName.DECISION,
)


class LoanClass(enum.StrEnum):
    """Top-level lending product class.

    Drives most of the workflow's *inputs*:

    - **business** — commercial real-estate loans. Underwriting reads
      borrower entity, guarantors, property type/address, appraised
      value, NOI, debt service. Rules engine evaluates DSCR / LTV /
      debt yield / guarantor concentration. The seeded fixtures and
      the original target-market for this codebase.
    - **personal** — consumer / individual loans. Underwriting reads
      borrower individual name, SSN-last-4, employer, annual income,
      outstanding obligations, credit score, loan purpose. Rules
      engine evaluates DTI, FICO floor, employment stability. Reg Z
      (TILA) governs disclosures rather than commercial UCC.

    Most plumbing is shared — agents, audit log, review queue, eval
    harness, observability. The branches are confined to:

    - intake's REQUIRED_FIELDS list
    - rules engine's evaluation context + policy thresholds
    - underwriting agent's system prompt
    - the borrower portal's form layout
    """

    BUSINESS = "business"
    PERSONAL = "personal"


class AutonomyLevel(enum.StrEnum):
    """How much of the workflow runs without human prompting.

    - ``assisted`` (default) — every gate requires a human click. Run
      intake, approve the email, run underwriting, run decision, send
      to committee, etc. This is the safe-by-default mode and what
      committee-bound deals need.
    - ``autonomous`` — the orchestrator chains agents end-to-end on
      its own. It still pauses at *irreversible* HITL gates (sending
      a borrower email, transmitting a decision), because those are
      real-world commitments where an undo isn't free.

    Stored on the loan, not globally, so individual deals can be put on
    fast-track or held at full review independent of org defaults.
    """

    ASSISTED = "assisted"
    AUTONOMOUS = "autonomous"


# Legal stage transitions. Centralizing this prevents illegal states.
#
# Withdrawal can happen from any non-terminal stage — the borrower can
# walk away from their application at any point before it funds. After
# CLOSING the loan is committed and withdrawal is no longer a unilateral
# borrower action; that becomes a payoff / servicing-level operation.
VALID_TRANSITIONS: dict[LoanStage, set[LoanStage]] = {
    LoanStage.INTAKE: {LoanStage.UNDERWRITING, LoanStage.DECLINED, LoanStage.WITHDRAWN},
    LoanStage.UNDERWRITING: {LoanStage.DECISION, LoanStage.DECLINED, LoanStage.WITHDRAWN},
    LoanStage.DECISION: {
        LoanStage.CONDITIONS,
        LoanStage.APPROVED,
        LoanStage.DECLINED,
        LoanStage.WITHDRAWN,
    },
    LoanStage.CONDITIONS: {LoanStage.CLOSING, LoanStage.DECLINED, LoanStage.WITHDRAWN},
    LoanStage.APPROVED: {LoanStage.CLOSING, LoanStage.WITHDRAWN},
    LoanStage.CLOSING: {LoanStage.SERVICING},
    LoanStage.SERVICING: set(),
    LoanStage.DECLINED: set(),
    LoanStage.WITHDRAWN: set(),
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
    # Personal vs business. See LoanClass docstring for what each
    # implies. Defaults at the DB level (server_default below) so
    # migrating in existing rows is a no-op — all pre-class loans
    # are commercial real estate.
    loan_class: Mapped[LoanClass] = mapped_column(
        SAEnum(
            LoanClass,
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=LoanClass.BUSINESS,
        server_default=LoanClass.BUSINESS.value,
        index=True,
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
    # How much of the workflow runs without human prompting — see
    # AutonomyLevel for semantics. native_enum=False because the column
    # is plain VARCHAR (migration 0006) and we want SQLAlchemy to coerce
    # both directions.
    autonomy_level: Mapped[AutonomyLevel] = mapped_column(
        SAEnum(
            AutonomyLevel,
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=AutonomyLevel.ASSISTED,
        server_default=AutonomyLevel.ASSISTED.value,
    )
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Embedding of the underwriting summary, used for comparable-loans
    # kNN. Populated by the underwriting agent's persist step. 1024 dims
    # to match config.embeddings_dimensions + migration 0003.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)

    # Soft-delete + retention. ``deleted_at`` is set when the borrower
    # requests erasure; operational queries filter ``IS NULL``.
    # ``retention_until`` is the earliest legal hard-delete time
    # (Reg B/ECOA: 25mo after withdrawal/decline; HMDA: 5y after
    # approval). The retention sweep job hard-deletes rows past
    # ``retention_until``. Both nullable; ``deleted_at IS NULL``
    # is the normal happy-path active state.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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

    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="agent_run",
        cascade="all, delete-orphan",
        order_by="AgentStep.created_at",
    )


class AgentStep(Base):
    """One LangGraph node execution inside an :class:`AgentRun`.

    Persisted by the streaming layer when it emits ``node_complete``,
    ``interrupt``, ``error``, or ``skipped`` SSE events — so the same
    event the frontend renders live also lands on disk for the
    auditor who shows up three days later asking "what did the
    intake agent do on loan X?"

    ``status`` mirrors the SSE event kind:

    - ``ok``         — node ran to completion. ``summary`` is the
                       short human blurb the frontend also shows.
    - ``skipped``    — pre-flight gate fired (no documents to
                       process, no extractions to evaluate, etc.).
                       ``payload.reason`` carries the friendly cause.
    - ``interrupt``  — node paused awaiting human approval.
                       ``payload`` carries the interrupt value.
    - ``failed``     — node raised. ``payload.error`` has the
                       structured reason, ``payload.detail`` the
                       longer technical text.

    ``payload`` is a small JSONB blob the writer controls — the
    extracted-field count, the missing-fields list, the rule-outcome
    counts, etc. Kept small (no PII, no document bodies) so we can
    show the whole row in the trace UI without redaction.
    """

    __tablename__ = "agent_steps"
    __table_args__ = (Index("ix_agent_steps_run_created", "agent_run_id", "created_at"),)

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(512))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    agent_run: Mapped["AgentRun"] = relationship(back_populates="steps")
