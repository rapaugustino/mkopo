"""annotations — human verdicts on traces (LLM calls, agent runs, steps).

The eval page used to surface "this trace failed" only as a side effect of
the row's ``status`` column. That leaves no place for an operator who
saw a trace and judged it bad / incorrect / good to record that
judgement — the next person opening the drawer gets no signal, the
drift monitor has no labels to pivot on, and "look back at last week's
bad runs" requires re-reading every step.

This migration introduces a lightweight annotations table that lets
any persisted trace row receive a verdict + a free-form note from an
authenticated user. The eval dashboard reads these to surface "X bad
annotations this week" alongside the drift trend, and "bad" /
"incorrect" annotations auto-create a ``review_tasks`` row so the
failure lands in the human review queue rather than dying as a
detached note.

Schema design notes:

- ``target_kind`` + ``target_id`` is a polymorphic pointer. We could
  have used three FK columns (one per kind), but the cardinality
  (≈ thousands per kind, low millions total) makes polymorphism the
  cleaner shape — the eval dashboard queries by ``target_kind``
  filter and the drawers query by ``target_id``, both well-indexed.
- No FK constraint enforced at the DB level: we rely on the service
  layer to validate ``target_kind`` against an allowed set and to
  check that ``target_id`` exists in the matching table before write.
  Cross-table FKs in Postgres + soft-delete cascades on agent_runs
  / llm_calls made the trade-off come out this way.
- ``verdict`` is a small enum stored as VARCHAR(16). Adding a new
  verdict (e.g. ``"unclear"``) is a code change; expanding the column
  size is not.
- ``created_by_user_id`` is nullable so the future "annotated by the
  eval CI" / "auto-labelled by drift monitor" paths don't need a
  fake user.

Indexes:

- ``(target_kind, target_id)`` — primary lookup for the drawer's
  "show annotations on this row" query.
- ``(verdict, created_at)`` — the dashboard's "X bad annotations
  this week" rollup.
- ``created_by_user_id`` — "show me what I've annotated lately"
  affordance on the eval dashboard.

Revision ID: 0017_annotations
Revises: 0016_cost_and_errors
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0017_annotations"
down_revision = "0016_cost_and_errors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Polymorphic pointer. ``target_kind`` is one of
        # "llm_call" / "agent_run" / "agent_step"; ``target_id`` is the
        # UUID PK of the row in the corresponding table.
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Verdict. Closed set: "good" | "bad" | "incorrect".
        # "incorrect" is reserved for cases where the trace ran fine
        # mechanically but produced a wrong answer — distinct from
        # "bad" which is for traces that failed to run or were
        # categorically inappropriate.
        sa.Column("verdict", sa.String(16), nullable=False),
        # Free-form note. ~4KB cap matches llm_calls.error_detail —
        # enough for a paragraph of reasoning, not enough for someone
        # to paste a whole log.
        sa.Column("note", sa.String(4096), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        # Whether a review_tasks row was auto-created from this
        # annotation. Lets the service layer avoid double-creating
        # if a user edits/re-saves an annotation, and lets the eval
        # dashboard surface "X bad annotations have open follow-ups".
        sa.Column(
            "spawned_review_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        "ix_annotations_target",
        "annotations",
        ["target_kind", "target_id"],
    )
    op.create_index(
        "ix_annotations_verdict_created",
        "annotations",
        ["verdict", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_annotations_verdict_created", table_name="annotations")
    op.drop_index("ix_annotations_target", table_name="annotations")
    op.drop_table("annotations")
