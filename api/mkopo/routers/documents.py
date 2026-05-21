"""Document upload and listing endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import Document, DocumentType
from mkopo.services.audit import Actor, record
from mkopo.services.ingest import embed_document
from mkopo.services.storage import get_storage

router = APIRouter(prefix="/loans/{loan_id}/documents", tags=["documents"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    loan_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
    file: UploadFile = File(...),
) -> dict[str, str]:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Filename required")

    body = await file.read()
    storage = get_storage()
    uri = await storage.put_object(
        loan_id=loan_id,
        filename=file.filename,
        body=body,
        content_type=file.content_type or "application/octet-stream",
    )

    # Naive content extraction for plain text. A production build would run OCR for PDFs.
    text_content = ""
    if file.content_type and file.content_type.startswith("text/"):
        text_content = body.decode("utf-8", errors="ignore")

    document = Document(
        loan_id=loan_id,
        filename=file.filename,
        doc_type=DocumentType.UNKNOWN,
        storage_uri=uri,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(body),
        meta={"text_content": text_content},
    )
    db.add(document)
    await db.flush()

    # Chunk + embed the text so "Ask the file" can retrieve from it. Skips
    # silently when text_content is empty (PDFs awaiting OCR, etc.).
    chunk_count = await embed_document(db, document)

    await record(
        db,
        loan_id=loan_id,
        actor=Actor.user(user.user_id),
        action="document_uploaded",
        payload={
            "filename": file.filename,
            "document_id": str(document.id),
            "chunks_embedded": chunk_count,
        },
    )
    await db.commit()

    return {
        "document_id": str(document.id),
        "storage_uri": uri,
        "chunks_embedded": chunk_count,
    }
