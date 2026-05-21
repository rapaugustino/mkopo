"""Loan-metadata fields + users table.

Adds (per design Phase A):
- `users` table (owner reference, future auth backing)
- `loans.reference` — human-readable identifier `LN-YYYY-NNNN`, server-generated
  via a Postgres sequence. UUIDs stay as primary keys; the reference is
  what humans and regulators see.
- `loans.owner_user_id` — assigned underwriter
- `loans.stage_entered_at` — when the loan entered its current stage, used
  to compute aging on the pipeline page
- `loans.risk_band` — coarse 'low' | 'med' | 'high' bucket, set by the
  underwriting agent after it runs

Revision ID: 0002_loan_metadata
Revises: 0001_init
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_loan_metadata"
down_revision: str | None = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- users ---------------------------------------------------------
    op.create_table(
        "users",
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
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="underwriter"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # --- loan_reference sequence --------------------------------------
    op.execute("CREATE SEQUENCE IF NOT EXISTS loan_reference_seq START 1000")

    # --- new loan columns ---------------------------------------------
    op.add_column("loans", sa.Column("reference", sa.String(16), nullable=True))
    op.add_column(
        "loans",
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "loans",
        sa.Column(
            "stage_entered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column("loans", sa.Column("risk_band", sa.String(8), nullable=True))

    # --- backfill -----------------------------------------------------
    op.execute(
        """
        UPDATE loans
        SET reference = 'LN-' || TO_CHAR(created_at, 'YYYY')
                            || '-'
                            || LPAD(NEXTVAL('loan_reference_seq')::text, 4, '0')
        WHERE reference IS NULL
        """
    )
    op.execute("UPDATE loans SET stage_entered_at = created_at WHERE stage_entered_at IS NULL")

    # --- lock in defaults / NOT NULL / unique -------------------------
    op.alter_column("loans", "reference", nullable=False)
    op.alter_column("loans", "stage_entered_at", nullable=False)
    op.create_unique_constraint("uq_loans_reference", "loans", ["reference"])
    op.create_index("ix_loans_reference", "loans", ["reference"])
    op.create_index("ix_loans_owner", "loans", ["owner_user_id"])

    # Default for new rows so future INSERTs don't need to supply `reference`.
    op.execute(
        """
        ALTER TABLE loans
        ALTER COLUMN reference
        SET DEFAULT 'LN-' || EXTRACT(YEAR FROM CURRENT_DATE)::text
                          || '-'
                          || LPAD(NEXTVAL('loan_reference_seq')::text, 4, '0')
        """
    )
    op.execute("ALTER TABLE loans ALTER COLUMN stage_entered_at SET DEFAULT NOW()")


def downgrade() -> None:
    op.execute("ALTER TABLE loans ALTER COLUMN stage_entered_at DROP DEFAULT")
    op.execute("ALTER TABLE loans ALTER COLUMN reference DROP DEFAULT")
    op.drop_index("ix_loans_owner", table_name="loans")
    op.drop_index("ix_loans_reference", table_name="loans")
    op.drop_constraint("uq_loans_reference", "loans", type_="unique")
    op.drop_column("loans", "risk_band")
    op.drop_column("loans", "stage_entered_at")
    op.drop_column("loans", "owner_user_id")
    op.drop_column("loans", "reference")
    op.execute("DROP SEQUENCE IF EXISTS loan_reference_seq")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
