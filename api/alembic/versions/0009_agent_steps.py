"""Agent step persistence + LLM-call ↔ agent-run linkage.

The observability dashboard could show *that* an agent ran and *that*
LLM calls happened, but nothing connected the two. ``llm_calls`` had
no ``thread_id``, and node-level progress events were streamed via
SSE and then lost — so an auditor asking "what did the intake agent
do on loan X at 14:32?" had no answer.

This migration changes that:

- ``llm_calls.thread_id`` — nullable string indexed by (thread_id,
  created_at). Populated by the gateway via a ContextVar so any LLM
  call made inside an agent run carries the run's thread id.
- ``agent_steps`` — one row per LangGraph node execution, persisted
  by the streaming layer when it emits node_complete / interrupt /
  error / skipped events. Captures the node name, status, elapsed
  time, the short summary the SSE consumer also sees, and a small
  JSONB payload for per-node forensics (extracted_field_count,
  missing_fields list, rule_outcome counts, etc.). One row per node
  per run; ``agent_run_id`` indexed for fast trace lookups.

Together they let the agent-run detail page render a step-by-step
trace with each step's LLM calls attached, which is the unit of
explainability auditors expect from a credit decisioning AI.

Revision ID: 0009_agent_steps
Revises: 0008_llm_call_error_detail
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0009_agent_steps"
down_revision: str | None = "0008_llm_call_error_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- llm_calls.thread_id ------------------------------------------
    op.add_column(
        "llm_calls",
        sa.Column("thread_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_llm_calls_thread_created",
        "llm_calls",
        ["thread_id", "created_at"],
    )

    # ---- agent_steps --------------------------------------------------
    op.create_table(
        "agent_steps",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "agent_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node", sa.String(64), nullable=False),
        # "ok" — node ran to completion
        # "skipped" — pre-flight gate fired (no docs, no extractions, ...)
        # "interrupt" — node paused awaiting human approval
        # "failed" — node raised; ``payload.error`` carries the reason
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(512), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column(
            "payload",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_agent_steps_run_created",
        "agent_steps",
        ["agent_run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_steps_run_created", table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_index("ix_llm_calls_thread_created", table_name="llm_calls")
    op.drop_column("llm_calls", "thread_id")
