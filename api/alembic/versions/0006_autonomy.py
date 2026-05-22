"""Autonomy level on loans.

Adds ``loans.autonomy_level`` — controls whether the orchestrator chains
agents end-to-end ("autonomous") or pauses at every gate for human
approval ("assisted"). The column defaults to ``assisted`` so all
existing loans behave exactly as before.

Why a column rather than a global setting: real deployments will want
this on per-loan (some borrowers are pre-approved candidates the
sponsor wants to push through fast; others are committee-required).
Storing it on the loan keeps the choice auditable alongside the rest
of the case file.

Revision ID: 0006_autonomy
Revises: 0005_eval
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_autonomy"
down_revision: str | None = "0005_eval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loans",
        sa.Column(
            "autonomy_level",
            sa.String(16),
            nullable=False,
            server_default="assisted",
        ),
    )


def downgrade() -> None:
    op.drop_column("loans", "autonomy_level")
