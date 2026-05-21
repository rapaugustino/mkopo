"""High-level ingest helpers — turn raw text into searchable embeddings.

These functions are the glue between the embedding service, the chunker,
and the database. Callers (document upload, seed script, underwriting
agent persist) don't need to know about either chunking strategy or
caching — they just call `embed_document(document)` or
`embed_loan_summary(loan, summary_text)`.
"""

from __future__ import annotations

import hashlib
import uuid

import structlog
from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import Document, DocumentChunk
from mkopo.services.embeddings import get_embedding_service
from mkopo.tools.chunking import chunk_text

logger = structlog.get_logger()


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def embed_document(session: AsyncSession, document: Document) -> int:
    """Chunk a document's text and persist embeddings for each chunk.

    Idempotent: deletes existing chunks for this document first so re-runs
    produce a clean state. Returns the chunk count.

    No-op if the document has no text content yet (e.g. a PDF awaiting OCR).
    """
    text_content: str = document.meta.get("text_content", "") if document.meta else ""
    if not text_content.strip():
        logger.info("embed_document_skip_no_text", document_id=str(document.id))
        return 0

    # Clean slate
    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))

    chunks = chunk_text(text_content)
    if not chunks:
        return 0

    svc = get_embedding_service()
    vectors = await svc.embed_batch([c.content for c in chunks], session=session)

    rows = [
        DocumentChunk(
            document_id=document.id,
            ordinal=c.ordinal,
            content=c.content,
            content_hash=_hash(c.content),
            token_count=c.token_count,
            embedding=v,
        )
        for c, v in zip(chunks, vectors, strict=True)
    ]
    session.add_all(rows)
    await session.flush()
    logger.info(
        "embed_document_complete",
        document_id=str(document.id),
        chunks=len(rows),
    )
    return len(rows)


async def embed_loan_summary(
    session: AsyncSession,
    loan_id: uuid.UUID,
    summary_text: str,
) -> None:
    """Embed a one-paragraph summary of the loan and set `loans.embedding`.

    The summary text is what comparable-loan search ranks against. The
    underwriting agent constructs it from its own output (recommendation,
    rationale, key extracted fields) and calls us.

    Uses an UPDATE so we don't trample other loan fields.
    """
    svc = get_embedding_service()
    vec = await svc.embed(summary_text, session=session)

    stmt = text("UPDATE loans SET embedding = :emb WHERE id = :id").bindparams(
        bindparam("emb", type_=Vector(svc.dimensions))
    )
    await session.execute(stmt, {"emb": vec, "id": loan_id})
    logger.info("embed_loan_summary_complete", loan_id=str(loan_id))


async def documents_for_loan(session: AsyncSession, loan_id: uuid.UUID) -> list[Document]:
    """All documents on a loan — used by the seed-script's bulk-embed helper."""
    return list(
        (await session.execute(select(Document).where(Document.loan_id == loan_id))).scalars().all()
    )
