"""Document content hash for tamper detection.

The materials hash (see ``services/materials_hash.py``) needs a way to
notice when a document's bytes have changed. A storage URI tells us
*where* the document is; the content hash tells us *what it was* when
the underwriting/decision agent cited it.

Without this column, a swapped appraisal slides through closing
silently — the URI stays the same, the cited fields stay the same in
the extractions table, but the actual bytes underneath are different.
That's the worst kind of decision corruption: invisible.

The column is nullable so we can ship the migration without a
backfill blocker. New uploads populate it at write time; old rows
get filled in on first read by the storage service (lazy backfill).

``CHAR(64)`` because sha256 is exactly 64 hex characters; using the
fixed-width form lets Postgres pack the index tighter than VARCHAR.

Revision ID: 0011_document_content_hash
Revises: 0010_agent_steps_updated_at
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_document_content_hash"
down_revision: str | None = "0010_agent_steps_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("content_hash", sa.CHAR(64), nullable=True),
    )
    # Indexed because (a) the materials-hash service joins on it
    # heavily, and (b) future "have we seen this document before?"
    # dedup checks across loans will need it.
    op.create_index(
        "ix_documents_content_hash",
        "documents",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_column("documents", "content_hash")
