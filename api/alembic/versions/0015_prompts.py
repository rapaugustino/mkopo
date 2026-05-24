"""prompts — versioned system prompts for every LLM call site.

Before this migration system prompts lived as module-level constants
(extractor, underwriting commercial/personal, borrower_chat, staff_chat)
or as inline strings inside agent nodes (intake doc-request, decision
path-select / approve-conditional / decline-letter, qa.answer). Editing
one meant a code change + redeploy — fine for engineers, bad for the
underwriting team that should be able to tune phrasing without us.

This migration introduces a ``prompts`` table that the LLM gateway's
callers consult via :mod:`mkopo.services.prompts`. Code defaults stay
in the codebase as fallback (so the app boots and behaves correctly
without a DB seed), and the DB row — when present — overrides the
default. A startup hook seeds v1 of every code default the first time
the app starts against a fresh DB, so the management UI has something
to show even on a brand-new install.

Schema:

  - ``identifier`` — logical name (``"intake.draft_doc_request.personal"``).
    Stable across versions; the prompt registry in code maps this to
    the call site.
  - ``version`` — monotonically increasing per identifier. v1 is the
    code default, v2+ are user edits.
  - ``body`` — full prompt text. Stored verbatim so the gateway can
    pass it straight through; no templating layer.
  - ``is_active`` — exactly one row per identifier has this set. The
    partial unique index below enforces it. Activating a different
    version (e.g. rolling back to v3 after v5 was a bad edit) is a
    two-step transaction: clear is_active on the current row, set it
    on the target.
  - ``change_note`` — what changed in this version. Required when the
    edit comes from the UI; nullable so seed-script-imported defaults
    can leave it empty.
  - ``created_by_user_id`` — author. Nullable so the bootstrap seeds
    (no human) can record without a user.

Indexes:

  - Primary lookup is ``identifier + is_active`` (the runtime "get me
    the active prompt for X" query). Partial unique index doubles as
    the integrity constraint and the read index.
  - ``(identifier, version)`` unique covers history reads and prevents
    duplicate version numbers on retries.

Revision ID: 0015_prompts
Revises: 0014_tool_uses
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_prompts"
down_revision = "0014_tool_uses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("identifier", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("change_note", sa.String(512), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
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
        # (identifier, version) is the natural composite key; UUID id
        # stays primary for FK targeting (e.g. future annotations).
        sa.UniqueConstraint(
            "identifier", "version", name="uq_prompts_identifier_version"
        ),
    )
    # Partial unique index — at most one is_active row per identifier.
    # This is the constraint that makes "activate version N" safe
    # under concurrent writes: the other writer's UPDATE will hit a
    # duplicate-key error and roll back cleanly.
    op.execute(
        "CREATE UNIQUE INDEX uq_prompts_active_per_identifier "
        "ON prompts(identifier) WHERE is_active"
    )
    # Lookup index for history reads — "show every version of X
    # newest first" on the detail page.
    op.create_index(
        "ix_prompts_identifier_version",
        "prompts",
        ["identifier", "version"],
    )


def downgrade() -> None:
    op.drop_index("ix_prompts_identifier_version", table_name="prompts")
    op.execute("DROP INDEX IF EXISTS uq_prompts_active_per_identifier")
    op.drop_table("prompts")
