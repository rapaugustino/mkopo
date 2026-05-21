"""Eval + drift-monitor data layer.

Two tables this migration adds:

- `task_runs` — one row per (eval task, source, day). The CI eval gate
  writes rows with source='golden' when the suite runs; the nightly
  drift monitor writes source='production' rows sampled from real
  extractions. Together they back the weekly-trend chart on the eval
  dashboard and the drift alert.

- `llm_calls` — minimal record of every LLM call (model, latency, token
  counts, schema name, status). We already log via structlog from the
  gateway, but logs aren't queryable. Persisting here lets us answer
  "what's our p95 LLM latency?" with one indexed query.

Both tables are append-only and partition naturally on `created_at`.

Revision ID: 0005_eval
Revises: 0004_chunk_tsv
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_eval"
down_revision: str | None = "0004_chunk_tsv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("task_name", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("n", sa.Integer, nullable=False),
        sa.Column("accuracy", sa.Float, nullable=False),
        sa.Column("avg_score", sa.Float, nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_task_runs_task_source_created",
        "task_runs",
        ["task_name", "source", "created_at"],
    )
    op.create_index("ix_task_runs_created", "task_runs", ["created_at"])

    op.create_table(
        "llm_calls",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("system_prompt_hash", sa.String(64), nullable=False),
        sa.Column("elapsed_seconds", sa.Float, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("schema_name", sa.String(128), nullable=True),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_llm_calls_created", "llm_calls", ["created_at"])
    op.create_index("ix_llm_calls_model_created", "llm_calls", ["model", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_model_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_created", table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index("ix_task_runs_created", table_name="task_runs")
    op.drop_index("ix_task_runs_task_source_created", table_name="task_runs")
    op.drop_table("task_runs")
