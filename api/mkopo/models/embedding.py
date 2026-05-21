"""Embedding-related ORM models.

`DocumentChunk` holds a chunk of a document's text plus its embedding —
this is what "Ask the file" retrieves from.

The `embedding_cache` table is intentionally NOT an ORM model. It's a
key-value lookup keyed by `(content_hash, model, dim)`, used only by the
EmbeddingService. ORM overhead would add nothing; the service goes
through raw SQL there.
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base

# Must mirror config.Settings.embeddings_dimensions and migration 0003.
EMBEDDING_DIM = 1024


class DocumentChunk(Base):
    """One ~500-token slice of a document, plus its embedding."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_document_chunks_doc_ord"),
        Index("ix_document_chunks_document", "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
