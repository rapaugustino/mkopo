"""Loan class — personal vs. business.

Commercial real-estate and personal consumer loans run through
genuinely different underwriting frameworks. They share an audit log,
agent vocabulary, and review queue, but the *inputs* to underwriting
differ enough that we want a class enum on the loan row so the rules
engine, intake field list, and agent prompts can branch on it.

Existing loans default to ``business`` because everything seeded so
far is commercial real estate. New personal loans coming in via the
borrower portal will set this explicitly.

Revision ID: 0007_loan_class
Revises: 0006_autonomy
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_loan_class"
down_revision: str | None = "0006_autonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loans",
        sa.Column(
            "loan_class",
            sa.String(16),
            nullable=False,
            server_default="business",
        ),
    )
    # Indexed because pipeline filters and analytics will routinely
    # slice by class — "show me all personal loans waiting on docs"
    # is the kind of query a loan officer makes daily.
    op.create_index("ix_loans_loan_class", "loans", ["loan_class"])


def downgrade() -> None:
    op.drop_index("ix_loans_loan_class", table_name="loans")
    op.drop_column("loans", "loan_class")
