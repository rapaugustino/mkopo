"""Embeddings — vector columns + document chunks + cache.

Adds the data structures Phase C needs:

- `loans.embedding vector(1024)` for comparable-loans kNN. One vector per
  loan, embedding of the loan's underwriting summary. Set by the
  underwriting agent's persist step after summary is drafted.

- `document_chunks` table: documents broken into ~500-token chunks, each
  with its own vector. This is the corpus "Ask the file" retrieves from
  via cosine kNN.

- `embedding_cache` table: keyed by content hash + model + dimensions.
  Same `(content, model, dim)` triple never gets re-embedded, which is
  important because re-running migrations / seed scripts during dev
  would otherwise re-bill us per text-embedding call.

Both vector columns get an HNSW index on `vector_cosine_ops` — pgvector's
recommended choice for kNN at >1k vectors. Below that the planner uses
sequential scan, which is faster for tiny tables anyway.

Revision ID: 0003_embeddings
Revises: 0002_loan_metadata
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_embeddings"
down_revision: str | None = "0002_loan_metadata"
branch_labels = None
depends_on = None


# Must match config.Settings.embeddings_dimensions. Hard-coded here so the
# migration is reproducible regardless of env at apply time.
EMBEDDING_DIM = 1024


def upgrade() -> None:
    # --- loans.embedding ----------------------------------------------
    op.add_column(
        "loans",
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
    )
    op.execute(
        "CREATE INDEX ix_loans_embedding_hnsw ON loans USING hnsw (embedding vector_cosine_ops)"
    )

    # --- document_chunks ----------------------------------------------
    op.create_table(
        "document_chunks",
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
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_document_chunks_doc_ord"),
    )
    op.create_index("ix_document_chunks_document", "document_chunks", ["document_id"])
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # --- embedding_cache ----------------------------------------------
    op.create_table(
        "embedding_cache",
        sa.Column("content_hash", sa.String(64), primary_key=True),
        sa.Column("model", sa.String(64), primary_key=True),
        sa.Column("dimensions", sa.Integer, primary_key=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("embedding_cache")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.drop_index("ix_document_chunks_document", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.execute("DROP INDEX IF EXISTS ix_loans_embedding_hnsw")
    op.drop_column("loans", "embedding")
