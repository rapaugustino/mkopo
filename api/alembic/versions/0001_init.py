"""Initial schema.

Revision ID: 0001_init
Revises:
Create Date: 2026-05-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_init"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Note: extensions (`pgcrypto`, `vector`) are created by a DBA as a
    # superuser before this migration runs — they require privileges the
    # application role intentionally doesn't have. See README §1.
    # `gen_random_uuid()` is built into Postgres 13+, so pgcrypto is only
    # needed if you also want its hashing/crypto helpers later.

    # loans
    op.create_table(
        "loans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("stage", sa.String(32), nullable=False, server_default="intake"),
        sa.Column("loan_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status_detail", sa.String(256)),
        sa.Column("meta", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("ix_loans_stage", "loans", ["stage"])

    # parties
    op.create_table(
        "parties",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("party_type", sa.String(32), nullable=False),
        sa.Column("email", sa.String(256)),
        sa.Column("meta", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("ix_parties_name", "parties", ["name"])
    op.create_index("ix_parties_email", "parties", ["email"])

    # loan_parties
    op.create_table(
        "loan_parties",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "party_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.UniqueConstraint("loan_id", "party_id", "role", name="uq_loan_party_role"),
    )
    op.create_index("ix_loan_parties_loan", "loan_parties", ["loan_id"])
    op.create_index("ix_loan_parties_party", "loan_parties", ["party_id"])
    op.create_index("ix_loan_parties_role", "loan_parties", ["role"])

    # documents
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("doc_type", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("storage_uri", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("meta", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("ix_documents_loan", "documents", ["loan_id"])

    # extractions
    op.create_table(
        "extractions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column(
            "source_span", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("model_used", sa.String(128)),
    )
    op.create_index("ix_extractions_document", "extractions", ["document_id"])
    op.create_index("ix_extractions_field", "extractions", ["field_name"])

    # review_tasks
    op.create_table(
        "review_tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "extraction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extractions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True)),
        sa.Column("notes", sa.Text),
    )
    op.create_index("ix_review_tasks_extraction", "review_tasks", ["extraction_id"])
    op.create_index("ix_review_tasks_assigned", "review_tasks", ["assigned_to"])

    # messages
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("sender", sa.String(256), nullable=False),
        sa.Column("recipient", sa.String(256)),
        sa.Column("subject", sa.String(512)),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "resend_metadata",
            postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("drafted_by_agent", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_index("ix_messages_loan", "messages", ["loan_id"])
    op.create_index("ix_messages_thread", "messages", ["thread_id"])
    op.create_index("ix_messages_loan_created", "messages", ["loan_id", "created_at"])

    # conditions
    op.create_table(
        "conditions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("due_date", sa.DateTime(timezone=True)),
        sa.Column("drafted_by_agent", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )
    op.create_index("ix_conditions_loan", "conditions", ["loan_id"])

    # audit_events
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.create_index("ix_audit_events_loan", "audit_events", ["loan_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_loan_at", "audit_events", ["loan_id", "created_at"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor_type", "actor_id"])

    # agent_runs
    op.create_table(
        "agent_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("started_at", sa.Numeric),
        sa.Column(
            "payload", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.create_index("ix_agent_runs_loan", "agent_runs", ["loan_id"])
    op.create_index("ix_agent_runs_thread", "agent_runs", ["thread_id"])
    op.create_index("ix_agent_runs_loan_started", "agent_runs", ["loan_id", "started_at"])


def downgrade() -> None:
    for tbl in (
        "agent_runs",
        "audit_events",
        "conditions",
        "messages",
        "review_tasks",
        "extractions",
        "documents",
        "loan_parties",
        "parties",
        "loans",
    ):
        op.drop_table(tbl)
    # Extensions are owned by the DBA, not the migration — leave them alone.
