"""llm_calls cost columns + infrastructure_errors table.

Two related additions to make the "what does this AI cost to run and
how often does it break in non-LLM ways" question answerable in the
UI without leaving the platform.

(1) **LLM cost columns** — every ``llm_calls`` row now carries
``cost_input_usd`` and ``cost_output_usd``, computed at call
completion from the per-model rate table in
``mkopo.services.pricing``. The split keeps the two halves visible
because the input/output ratio is the lever for "tune the prompt
down" decisions — a call dominated by input tokens means the
prompt or context window is bloated; one dominated by output means
the model is over-explaining. Both are NUMERIC(10, 6) so a
fraction of a cent doesn't get rounded away on aggregate.

(2) **infrastructure_errors** — append-only table for non-LLM,
non-business-rule failures that the FastAPI exception handler
catches. Examples: a DB connection blip, an S3 timeout, an
uncaught NoneType deref. These aren't visible in ``llm_calls``
(the LLM never ran) or ``audit_events`` (the action never
completed), so without this table they live only in the structlog
output where they're hard to roll up.

Columns:

  - ``path`` / ``method`` — the request that was in-flight when the
    error fired. Useful for "is the /eval/diagnostics endpoint
    flaky?" rollups on the observability page.
  - ``status_code`` — HTTP status the client saw (always 500-class
    on this code path; 4xx errors aren't recorded because they're
    a client-side problem and would otherwise drown the signal).
  - ``error_class`` / ``error_message`` — short identifiers for
    grouping; the message is truncated to keep the row small.
  - ``traceback`` — full traceback (truncated to 8000 chars). Lets
    the inspector drawer reproduce what we'd otherwise have to
    scrape from the log archive.
  - ``user_id`` — nullable FK; populated when the request had an
    authenticated user. The exception handler may not have one
    available (early-pipeline auth failures, anonymous health
    probes) so SET NULL on user delete keeps history intact.
  - ``request_id`` — correlation id when the request had one
    attached upstream. Lets a runbook follow the error from the
    UI all the way to a structured log.

Indexes match the queries the observability / eval pages run:

  - ``(created_at)`` — "errors in the last 24h / 7d", the most
    common rollup; matches the same shape we use on ``llm_calls``.
  - ``(error_class, created_at)`` — "top error classes this week"
    grouped reads.
  - ``(path, created_at)`` — "which endpoint is flaky right now".

Revision ID: 0016_cost_and_errors
Revises: 0015_prompts
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_cost_and_errors"
down_revision = "0015_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # (1) Cost columns on llm_calls. Numeric(10, 6) gives us 4 leading
    # digits of dollars (max ~$9,999 per call — way above any realistic
    # one-shot) and 6 of cents — enough to record a $0.000125 OpenAI
    # embedding call without losing the precision in aggregation.
    op.add_column(
        "llm_calls",
        sa.Column("cost_input_usd", sa.Numeric(10, 6), nullable=True),
    )
    op.add_column(
        "llm_calls",
        sa.Column("cost_output_usd", sa.Numeric(10, 6), nullable=True),
    )

    # (2) infrastructure_errors table.
    op.create_table(
        "infrastructure_errors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("error_class", sa.String(128), nullable=False),
        sa.Column("error_message", sa.String(1024), nullable=False),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_infrastructure_errors_created", "infrastructure_errors", ["created_at"])
    op.create_index(
        "ix_infrastructure_errors_class_created",
        "infrastructure_errors",
        ["error_class", "created_at"],
    )
    op.create_index(
        "ix_infrastructure_errors_path_created",
        "infrastructure_errors",
        ["path", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_infrastructure_errors_path_created", table_name="infrastructure_errors")
    op.drop_index(
        "ix_infrastructure_errors_class_created",
        table_name="infrastructure_errors",
    )
    op.drop_index("ix_infrastructure_errors_created", table_name="infrastructure_errors")
    op.drop_table("infrastructure_errors")
    op.drop_column("llm_calls", "cost_output_usd")
    op.drop_column("llm_calls", "cost_input_usd")
