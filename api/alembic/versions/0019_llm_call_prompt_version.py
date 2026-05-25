"""llm_calls.prompt_version_id — stamp each call with the active prompt row.

The prompt registry (mkopo.services.prompts) lets staff publish new
versions of any system prompt and roll back if a new version performs
worse than the prior. Without linking each LLM call to the
``prompts.id`` that produced it, the system loses the most important
question in regulated lending: *"which prompt version was responsible
for this borrower's outcome?"*

``system_prompt_hash`` (already in place) groups calls by prompt
*content*, but two distinct registry rows can share the same content
(e.g. a v3 that's identical to v2 with only a change_note difference)
and the hash doesn't carry forward to the prompt-management UI. The
new FK does — every call's owning prompt row is now resolvable in
one join.

Nullable on purpose:

- Free-form LLM calls outside the registry (the rewrite-assist
  endpoint, eval CI smoke tests, ad-hoc scripts) don't have a
  ``prompts.id``. They keep this column null.
- Pre-migration rows can't be backfilled with certainty — the
  registry didn't exist when older calls were recorded, and the
  current code path (LLMGateway) is what writes the stamp. The
  observability UI's "filter by prompt version" feature shows
  "(unstamped)" as one of the options for legacy rows.

Indexed on (prompt_version_id) so the prompt-management page can
say "this v2 has been used on 42 calls" without a tablescan.

Revision ID: 0019_llm_call_prompt_version
Revises: 0018_llm_call_parent_step
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019_llm_call_prompt_version"
down_revision = "0018_llm_call_parent_step"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_calls",
        sa.Column(
            "prompt_version_id",
            postgresql.UUID(as_uuid=True),
            # ``ondelete=SET NULL`` so that purging a long-lived prompt
            # row (rare, but possible during a registry cleanup) doesn't
            # cascade-destroy historical observability data.
            sa.ForeignKey("prompts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_llm_calls_prompt_version",
        "llm_calls",
        ["prompt_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_calls_prompt_version", table_name="llm_calls")
    op.drop_column("llm_calls", "prompt_version_id")
