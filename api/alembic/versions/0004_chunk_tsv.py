"""Hybrid-search support: tsvector on document_chunks.

Adds the sparse-retrieval half of hybrid search. Dense retrieval (the
existing `embedding` column with HNSW cosine) handles semantic queries
like "what's the property's income approach value?". Sparse retrieval
catches literal-string queries like "loans guaranteed by Matthew Chen"
or "PFS for Park" — names, codes, and acronyms that embeddings smooth
over.

`content_tsv` is a STORED GENERATED column derived from `content` —
Postgres 12+ supports this and it auto-updates on insert/update with no
trigger needed. GIN index on the tsvector for full-text search speed.

Combined with `embedding` + HNSW, this gives us proper hybrid retrieval.
Fusion happens at the application layer (mkopo/services/qa.py) via
Reciprocal Rank Fusion.

Revision ID: 0004_chunk_tsv
Revises: 0003_embeddings
"""

from __future__ import annotations

from alembic import op

revision = "0004_chunk_tsv"
down_revision: str | None = "0003_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        """
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_content_tsv ON document_chunks USING GIN (content_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_tsv")
