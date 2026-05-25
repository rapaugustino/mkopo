"""llm_calls.parent_step_id — link each call to its owning agent step.

Today the AgentRunDrawer renders every LLM call in a run as a flat
list under the step trace. Useful but unstructured — you can't see
"which calls happened inside ``extract_documents`` vs ``draft_email``"
without cross-referencing timestamps. For multi-call nodes (intake's
extractor runs N times in parallel for N documents) that flattening
loses the parent-child structure entirely.

This migration adds ``parent_step_id`` to ``llm_calls``. The
streaming layer (mkopo.routers.agents._stream_graph_run) binds an
``agent_step_id`` ContextVar around each node's execution; the
gateway reads it on ``_record_call`` the same way it already reads
``thread_id``. No call-signature changes anywhere — agents that were
written before this still work, they just leave the column null.

Nullable on purpose:

- Free-form LLM calls (eval CI, smoke tests, the new "rewrite
  prompt" assist) aren't inside an agent step. They keep the
  column null and render in a separate "ad-hoc calls" bucket on
  the dashboard.
- Backfill on existing rows would require reading the structlog
  trail to reconstruct parent links. The drawer just falls back
  to the flat list view for rows that don't have a parent — same
  behaviour as before this migration.

Indexed on (parent_step_id) for the drawer's "show every call
under this step" query.

Revision ID: 0018_llm_call_parent_step
Revises: 0017_annotations
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018_llm_call_parent_step"
down_revision = "0017_annotations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_calls",
        sa.Column(
            "parent_step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_llm_calls_parent_step",
        "llm_calls",
        ["parent_step_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_calls_parent_step", table_name="llm_calls")
    op.drop_column("llm_calls", "parent_step_id")
