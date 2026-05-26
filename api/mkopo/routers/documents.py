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
from mkopo.models import Document
from mkopo.services import loans as loan_service
from mkopo.services.audit import Actor, record
from mkopo.services.ingest import embed_document
from mkopo.services.loan_locks import raise_if_locked_for_documents
from mkopo.services.pdf import extract_text as extract_pdf_text
from mkopo.services.storage import StorageAuthzError, get_storage, mint_download_url

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

    # Stage lock — uploads are frozen past ``conditions`` so the
    # materials that fed the decision can't be retroactively
    # changed. ``conditions`` itself stays open so borrowers can
    # satisfy outstanding requirements.
    loan = await loan_service.get_loan(db, loan_id)
    if loan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    raise_if_locked_for_documents(loan.stage)

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

    # Input-layer prompt-injection scan. Runs against extracted text
    # (not raw bytes — binary PDFs would always come back clean) and
    # writes a row to injection_detections regardless of outcome so
    # the Safety dashboard can trend it. ``decision == BLOCKED`` is
    # fail-closed: refuse the upload entirely. The Document is NOT
    # persisted in that case — there's no row for an attacker to
    # re-reference. The matched-patterns list comes back in the 422
    # body so the staff user sees why their (or their tester's)
    # upload was rejected.
    from mkopo.agents.injection import (
        detect_injection,
    )
    from mkopo.models import InjectionSourceKind

    injection_result = await detect_injection(
        text=text_content,
        source_kind=InjectionSourceKind.DOCUMENT,
        loan_id=loan_id,
        actor_kind="user",
        actor_id=str(user.user_id),
        session=db,
    )
    if injection_result.decision.value == "blocked":
        # Mirror the standard 422 shape — the frontend toast
        # surfaces ``detail`` to the user.
        await db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "reason": "injection_detected",
                "detection_id": str(injection_result.detection_id),
                "matched_patterns": [
                    {
                        "pattern_id": m["pattern_id"],
                        "description": m["description"],
                    }
                    for m in injection_result.matched_patterns
                ],
                "message": (
                    "This document contains content that looks like "
                    "a prompt-injection attempt. Upload refused."
                ),
            },
        )

    # sha256 of the exact bytes that were uploaded. Feeds the materials
    # hash so a post-decision swap of the underlying file is detectable
    # without re-reading the bytes from S3. Cheap (in-memory) so this
    # adds no observable latency to the upload.
    import hashlib

    # Classify by filename at upload time — same rationale as the
    # borrower-portal upload path. Keeps the doc_type column populated
    # so transition prereqs + rules engine see what the checklist UI
    # sees.
    from mkopo.services.doc_classify import classify_from_filename

    document = Document(
        loan_id=loan_id,
        filename=file.filename,
        doc_type=classify_from_filename(file.filename),
        storage_uri=uri,
        content_type=content_type,
        size_bytes=len(body),
        content_hash=hashlib.sha256(body).hexdigest(),
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


@router.get("/{document_id}/download-url", status_code=status.HTTP_200_OK)
async def get_document_download_url(
    loan_id: uuid.UUID,
    document_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> dict[str, object]:
    """Mint a short-lived presigned download URL for one document.

    The caller (DocsPanel / DocumentViewer on the frontend) opens
    this URL in an iframe / new tab; the browser fetches the bytes
    directly from object storage with no further auth, then the URL
    expires. The audit event written here closes the read-side
    accountability loop.

    Cross-checks the document_id against the loan_id in the URL so a
    valid presigned URL for one loan's appraisal can't be coerced
    into reading a different loan's appraisal — the storage layer
    does its own URI ↔ loan_id check inside ``mint_download_url``.
    """
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.loan_id == loan_id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    try:
        url = await mint_download_url(
            db,
            loan_id=loan_id,
            document_id=doc.id,
            storage_uri=doc.storage_uri,
            actor=Actor.user(user.user_id),
            purpose="preview",
            expires_in=300,
        )
    except StorageAuthzError as e:
        # The URI doesn't belong to this loan — bug or tampering.
        # Surface as 403 not 500 so the client can react predictably.
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    await db.commit()
    return {
        "url": url,
        "filename": doc.filename,
        "content_type": doc.content_type,
        "expires_in_seconds": 300,
    }
