"""injection_detections — record every input scanned for prompt-injection.

Sits at the boundary of every input vector that becomes part of an
LLM prompt:

- Uploaded documents (after text extraction, before embedding /
  before persistence).
- Borrower chat messages (before they're appended to the
  tool-chat-loop history).
- Inbound borrower emails (via the Resend webhook).
- Borrower application form free-text fields (purpose, notes).

A hybrid pattern+Haiku detector inspects each input:

1. A compile-time regex catalog flags known signatures
   (instruction-override, role-swap, tool-coerce, payload smuggling,
   data exfiltration). Free, sub-millisecond.
2. Medium-severity hits escalate to a Haiku judgment (~$0.0001
   per call) — cheaper than a heavy-model judge and good enough to
   tighten the verdict on ambiguous paraphrases.
3. High severity blocks the input (fail-closed). Medium logs +
   flags but allows. Low silently counts.

The persisted row is what makes the Safety dashboard possible — a
search-/filter-able log of every detection with the matched
patterns, the Haiku critique, the raw excerpt, and the decision
(allowed / flagged / blocked).

Revision ID: 0021_injection_detections
Revises: 0020_institution_settings
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_injection_detections"
down_revision = "0020_institution_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All three enums are postgres-native ENUM types (not SQLAlchemy
    # String + CHECK) so the database itself enforces the closed set
    # and migrations later that add a value will be explicit.
    source_kind = postgresql.ENUM(
        "document",
        "chat_message",
        "inbound_email",
        "borrower_application",
        name="injection_source_kind",
        create_type=False,
    )
    severity = postgresql.ENUM(
        "low",
        "medium",
        "high",
        name="injection_severity",
        create_type=False,
    )
    decision = postgresql.ENUM(
        "allowed",
        "flagged",
        "blocked",
        name="injection_decision",
        create_type=False,
    )

    bind = op.get_bind()
    source_kind.create(bind, checkfirst=True)
    severity.create(bind, checkfirst=True)
    decision.create(bind, checkfirst=True)

    op.create_table(
        "injection_detections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # The loan this scan is associated with. NULL is allowed only
        # for the rare cases where the input hasn't been mapped to a
        # loan yet (e.g. an inbound email from an unknown sender) —
        # the safety dashboard still wants to display those.
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("source_kind", source_kind, nullable=False),
        # ID of the source row (document.id, message.id, etc).
        # Nullable so a pre-persistence block (e.g. document upload
        # rejected before the Document row was created) can still
        # write a detection record.
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("severity", severity, nullable=False),
        # Decision derived from severity at write time. Indexed +
        # stored separately so "show me only the blocks" is a cheap
        # filter — without it the dashboard joins severity → decision
        # in application code, which doesn't push down into Postgres.
        sa.Column("decision", decision, nullable=False),
        # List of {pattern_id, description, span_start, span_end}.
        # JSONB so the safety dashboard can filter by pattern_id
        # without a JOIN on a patterns table (the catalog is in code).
        sa.Column(
            "matched_patterns",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # True iff the hybrid detector escalated to the Haiku judge.
        # Drives the "Haiku calls" metric on the dashboard so the
        # cost envelope is visible.
        sa.Column(
            "llm_judge_called",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "llm_judge_severity",
            severity,
            nullable=True,
        ),
        sa.Column("llm_judge_critique", sa.Text, nullable=True),
        # Truncated input excerpt for forensics. Capped at 2000
        # chars at the application layer — bigger inputs get
        # head+tail snipping. Storing the excerpt means a reviewer
        # can decide whether the block was right without having to
        # cross-reference the source row (which may already be
        # deleted, in the inbound-email case).
        sa.Column("raw_text_excerpt", sa.Text, nullable=False),
        # Who triggered the scan: 'user' (staff), 'borrower', or
        # 'system' (inbound webhook, automated ingest). String not
        # enum because it's never filtered on in tight loops; the
        # dashboard uses the loan_id index instead.
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # ``updated_at`` is required by the shared ORM Base. Every model
        # in this codebase inherits the (created_at, updated_at) pair so
        # row-mutation audits look the same shape across tables.
        # InjectionDetection rows are write-once in practice, but the
        # column lets the ORM construct INSERTs that match Base's
        # mapped columns.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    # Recent-first lookup per loan (loan-detail SafetyChip + per-loan
    # detail drawer). DESC index because the only access pattern is
    # "most recent N".
    op.create_index(
        "ix_injection_detections_loan_recent",
        "injection_detections",
        ["loan_id", sa.text("created_at DESC")],
    )
    # Recent-first across loans for the global Safety dashboard.
    op.create_index(
        "ix_injection_detections_recent",
        "injection_detections",
        [sa.text("created_at DESC")],
    )
    # "Show me only blocked / flagged" filter on the dashboard.
    op.create_index(
        "ix_injection_detections_decision",
        "injection_detections",
        ["decision", sa.text("created_at DESC")],
    )
    # Severity histogram + "by source kind" group-by are dashboard-
    # native — these are the columns the GROUP BY runs on.
    op.create_index(
        "ix_injection_detections_severity",
        "injection_detections",
        ["severity"],
    )
    op.create_index(
        "ix_injection_detections_source_kind",
        "injection_detections",
        ["source_kind", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_injection_detections_source_kind",
        table_name="injection_detections",
    )
    op.drop_index(
        "ix_injection_detections_severity",
        table_name="injection_detections",
    )
    op.drop_index(
        "ix_injection_detections_decision",
        table_name="injection_detections",
    )
    op.drop_index(
        "ix_injection_detections_recent",
        table_name="injection_detections",
    )
    op.drop_index(
        "ix_injection_detections_loan_recent",
        table_name="injection_detections",
    )
    op.drop_table("injection_detections")

    bind = op.get_bind()
    postgresql.ENUM(name="injection_decision").drop(bind, checkfirst=True)
    postgresql.ENUM(name="injection_severity").drop(bind, checkfirst=True)
    postgresql.ENUM(name="injection_source_kind").drop(bind, checkfirst=True)
