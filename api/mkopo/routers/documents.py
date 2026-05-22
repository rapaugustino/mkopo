"""Document upload and listing endpoints.

This is the entry point for both manual uploads (drag-and-drop from the
case file UI) and webhook-driven attachments later. Every accepted file
flows through the same pipeline:

  1. Persist the binary to the configured storage backend (local / S3).
  2. Extract text in-memory — direct decode for ``text/*``, pypdf for
     ``application/pdf``. Image-only PDF pages get a marker so chunking
     still produces one chunk per page.
  3. Persist a ``Document`` row pointing at the storage URI and carrying
     the extracted text in ``meta.text_content``.
  4. Chunk + embed via ``services.ingest`` so the chunk shows up in
     "Ask the file" RAG and comparable-loans search.
  5. Record a ``document_uploaded`` audit event with per-file stats.

The extracted-text-in-meta pattern is deliberate: the source-of-truth
binary stays in storage, the indexable text sits next to the row, and
re-running extraction is a single function call rather than a download
+ re-upload roundtrip.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import Document, DocumentType
from mkopo.services.audit import Actor, record
from mkopo.services.ingest import embed_document
from mkopo.services.pdf import extract_text as extract_pdf_text
from mkopo.services.storage import get_storage

router = APIRouter(prefix="/loans/{loan_id}/documents", tags=["documents"])


@router.get("", status_code=status.HTTP_200_OK)
async def list_documents(
    loan_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> list[dict[str, object]]:
    """List documents attached to a loan, newest first.

    Returns the metadata the case-file UI needs — filename, content
    type, byte size, upload time, and the per-file extraction stats so
    the UI can mark "needs OCR" pages without re-fetching the body.
    """
    rows = (
        await db.execute(
            select(Document)
            .where(Document.loan_id == loan_id)
            .order_by(Document.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "doc_type": d.doc_type if isinstance(d.doc_type, str) else d.doc_type.value,
            "content_type": d.content_type,
            "size_bytes": d.size_bytes,
            "created_at": d.created_at.isoformat(),
            "extract": d.meta.get("extract") or {},
        }
        for d in rows
    ]


def _extract_text(*, body: bytes, content_type: str) -> tuple[str, dict[str, int]]:
    """Return ``(text, stats)`` for a file payload.

    ``text/*`` decodes directly; ``application/pdf`` runs pypdf.
    Everything else returns empty text and stats indicating that
    extraction was skipped — the file is still stored and the audit
    event records it, the chunker just has nothing to index.
    """
    if content_type.startswith("text/"):
        text = body.decode("utf-8", errors="ignore")
        return text, {"char_count": len(text), "method": "decode"}
    if content_type == "application/pdf":
        text, stats = extract_pdf_text(body)
        return text, {**stats, "method": "pypdf"}
    return "", {"char_count": 0, "method": "skipped"}


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    loan_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
    file: UploadFile = File(...),
) -> dict[str, object]:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Filename required")

    body = await file.read()
    content_type = file.content_type or "application/octet-stream"

    storage = get_storage()
    uri = await storage.put_object(
        loan_id=loan_id,
        filename=file.filename,
        body=body,
        content_type=content_type,
    )

    text_content, extract_stats = _extract_text(body=body, content_type=content_type)

    document = Document(
        loan_id=loan_id,
        filename=file.filename,
        doc_type=DocumentType.UNKNOWN,
        storage_uri=uri,
        content_type=content_type,
        size_bytes=len(body),
        meta={"text_content": text_content, "extract": extract_stats},
    )
    db.add(document)
    await db.flush()

    # Chunk + embed so "Ask the file" can retrieve from it. Silently
    # skips when text_content is empty.
    chunk_count = await embed_document(db, document)

    await record(
        db,
        loan_id=loan_id,
        actor=Actor.user(user.user_id),
        action="document_uploaded",
        payload={
            "filename": file.filename,
            "document_id": str(document.id),
            "content_type": content_type,
            "size_bytes": len(body),
            "chunks_embedded": chunk_count,
            **extract_stats,
        },
    )
    await db.commit()

    return {
        "document_id": str(document.id),
        "storage_uri": uri,
        "chunks_embedded": chunk_count,
        "extract": extract_stats,
    }
