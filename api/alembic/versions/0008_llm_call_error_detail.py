"""LLM call error_reason + error_detail.

Until now ``llm_calls`` carried only ``status='error'`` /
``status='schema_failed'`` — operators could see *that* a call failed
but never *why*. Add two columns so the observability inspector can
show the short reason in the table and the longer detail behind a
"Show details" toggle:

- ``error_reason``  — short (≤256ch) one-line summary. Safe to render
  in dense rows.
- ``error_detail``  — longer (≤4096ch) message: full Pydantic
  validation pretty-print, SDK exception ``repr``, etc.

Both nullable; populated only on failure rows.

Revision ID: 0008_llm_call_error_detail
Revises: 0007_loan_class
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_llm_call_error_detail"
down_revision: str | None = "0007_loan_class"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("error_reason", sa.String(256), nullable=True))
    op.add_column(
        "llm_calls",
        sa.Column("error_detail", sa.String(4096), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_calls", "error_detail")
    op.drop_column("llm_calls", "error_reason")
