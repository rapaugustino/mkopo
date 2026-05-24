"""tool_uses — per-invocation tool-call trajectory.

The Langsmith replacement story: when an LLM call decides to use a
tool, we need to persist the trajectory — name, args, result, status,
timing — so the observability surface can reconstruct "the agent
asked for tool X with args Y and got result Z" without having to
crawl the structured logs.

Today only the chat loops (borrower_chat + staff_chat) make
tool-using LLM calls; the deterministic agents (intake, underwriting,
decision) call ``LLMGateway.call`` without tools. The table is built
generic so we can extend tool use to those agents without a schema
change — every tool invocation across the codebase writes here.

Why a separate table rather than a JSONB column on ``llm_calls``:

  - One LLM call can issue multiple tools. Storing them in a JSONB
    array on the call would make filtering ("show me every loan
    that ran `withdraw_application`") expensive — you'd need GIN
    indexes on a nested path.
  - The tool execution timing is independent of the LLM call (the
    LLM returns the tool request, then we execute synchronously +
    record). Modelling tool runs as first-class rows means the
    timing column has clean semantics.
  - Foreign keys + cascade deletes — if we erase an LLM call (for
    GDPR), the tool uses go with it.

Columns:

  - ``id``: UUID PK.
  - ``llm_call_id``: nullable FK to ``llm_calls.id``. Most tools are
    tied to a specific LLM call (the one that asked for them).
    Nullable for direct API-driven tool invocations (none today,
    but the door is open).
  - ``agent_run_id``: nullable FK to ``agent_runs.id``. Always set
    when the call happened inside an agent's run, even if the LLM
    call's own ``thread_id`` is nullable.
  - ``thread_id``: chat thread id (chat-loop calls only) for fast
    "show me every tool call in this conversation" filters.
  - ``loan_id``: nullable FK to ``loans.id``. Most tools are scoped
    to a loan; recording it here makes the per-loan trace query
    trivial.
  - ``sequence_num``: 0-indexed ordering within the parent LLM call.
    Lets the UI render the tool sequence in the order the agent
    requested them.
  - ``tool_name``: short identifier (``"withdraw_application"``,
    ``"get_loan_status"``, etc.). Indexed for "which loans called
    this tool?" rollups.
  - ``input``: JSONB of the tool args the LLM passed.
  - ``output``: JSONB of the tool's structured result (when ok).
  - ``status``: ``ok | error | cancelled``. ``cancelled`` is the
    confirmation-required path where the user clicked Cancel rather
    than Confirm.
  - ``error_message``: human-readable on ``status='error'``.
  - ``elapsed_ms``: integer ms from tool start to return. Useful for
    p95s on slow tools.

Indexes are scoped to the queries the observability page actually
runs: by LLM call (to fill the drawer), by loan (to fill the loan
trace tab), and by tool_name + created_at (for the eval dashboard's
"tool usage by name" rollup).

Revision ID: 0014_tool_uses
Revises: 0013_soft_delete_retention
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_tool_uses"
down_revision = "0013_soft_delete_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_uses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "llm_call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("llm_calls.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sequence_num", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column(
            "input", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "output", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
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
    op.create_index(
        "ix_tool_uses_llm_call_id_sequence",
        "tool_uses",
        ["llm_call_id", "sequence_num"],
    )
    op.create_index("ix_tool_uses_agent_run_id", "tool_uses", ["agent_run_id"])
    op.create_index("ix_tool_uses_loan_id", "tool_uses", ["loan_id"])
    op.create_index("ix_tool_uses_thread_id", "tool_uses", ["thread_id"])
    op.create_index(
        "ix_tool_uses_tool_name_created_at",
        "tool_uses",
        ["tool_name", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_uses_tool_name_created_at", table_name="tool_uses")
    op.drop_index("ix_tool_uses_thread_id", table_name="tool_uses")
    op.drop_index("ix_tool_uses_loan_id", table_name="tool_uses")
    op.drop_index("ix_tool_uses_agent_run_id", table_name="tool_uses")
    op.drop_index("ix_tool_uses_llm_call_id_sequence", table_name="tool_uses")
    op.drop_table("tool_uses")
