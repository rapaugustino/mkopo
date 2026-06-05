"""Soft-delete + retention windows on loans and users.

Phase 2 self-service introduces "borrower can request erasure".
For a US lender that means: hide the loan from operational views
immediately, but keep the record on disk until the regulatory
retention window expires (Reg B/ECOA 25 months for declined/
withdrawn applications; HMDA 5 years for approved loans). After
that window, a scheduled sweep hard-deletes.

Three columns, all nullable:

  - ``loans.deleted_at`` — soft-delete marker. Operational queries
    filter ``WHERE deleted_at IS NULL``. ``NULL`` = active.
  - ``loans.retention_until`` — earliest legal hard-delete time.
    Set by the erasure endpoint; the sweep job hard-deletes rows
    where ``deleted_at IS NOT NULL AND retention_until <= now()``.
  - ``users.deleted_at`` — same idea for the borrower account.
    User row stays until the last associated loan's retention
    window expires (sweep job handles that ordering).

Why not a separate ``deleted_loans`` archive table: the sweep
gives us the same property with less ceremony, and the data
stays queryable for compliance audits in the meantime.

Revision ID: 0013_soft_delete_retention
Revises: 0012_user_auth
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_soft_delete_retention"
down_revision: str | None = "0012_user_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loans",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "loans",
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index — only the live rows. Operational queries hit
    # the index; deleted rows aren't bloated into the index pages.
    op.create_index(
        "ix_loans_active",
        "loans",
        ["created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # Sweep index — find soft-deleted rows that have aged past
    # their retention window. Bounded scan even on a huge table.
    op.create_index(
        "ix_loans_retention_sweep",
        "loans",
        ["retention_until"],
        postgresql_where=sa.text("deleted_at IS NOT NULL AND retention_until IS NOT NULL"),
    )
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "deleted_at")
    op.drop_index("ix_loans_retention_sweep", table_name="loans")
    op.drop_index("ix_loans_active", table_name="loans")
    op.drop_column("loans", "retention_until")
    op.drop_column("loans", "deleted_at")
