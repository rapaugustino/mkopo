"""Review queue + extraction-source endpoints.

Closes the confidence-gate loop from DESIGN §7.2: low-confidence extractions
land in `review_tasks` (written by the intake agent), and the underwriter
resolves them via Accept / Override actions.

Boundary discipline (matches the rest of the system):
- Accept records that a human ratified the AI's value — extraction.status
  becomes ACCEPTED with no value change.
- Override records that the human corrected it — extraction.value updates,
  status becomes OVERRIDDEN, and an audit_event captures the diff. The
  override flows downstream automatically because rules_eval picks the
  highest-confidence accepted extraction per field.

Every accept/override writes an audit_events row so the audit log shows
who ratified what and when.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import Document, Extraction, ExtractionStatus, ReviewTask
from mkopo.services.audit import Actor, record

router = APIRouter(tags=["review"])


# --- Response shapes ---


class ReviewTaskExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    field_name: str
    value: str
    confidence: float
    status: str
    source_span: dict[str, Any]


class ReviewTaskLoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str


class ReviewTaskDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str


class ReviewTaskOut(BaseModel):
    """List-view row — keeps the joined context flat for the table cells."""

    id: uuid.UUID
    reason: str
    status: str
    created_at: Any
    extraction: ReviewTaskExtractionOut
    loan: ReviewTaskLoanOut
    document: ReviewTaskDocumentOut


class ExtractionSourceOut(BaseModel):
    """Full extraction + the document text it was pulled from. Powers the
    DocSourceViewer's left column (text with the source-span highlighted)."""

    extraction: ReviewTaskExtractionOut
    document: ReviewTaskDocumentOut
    loan: ReviewTaskLoanOut
    document_text: str


class OverrideIn(BaseModel):
    value: str = Field(min_length=1, max_length=2000)
    notes: str | None = Field(default=None, max_length=500)


# --- Helpers ---


def _row_to_review_task_out(task: ReviewTask) -> ReviewTaskOut:
    ext = task.extraction
    doc = ext.document
    loan = doc.loan
    return ReviewTaskOut(
        id=task.id,
        reason=task.reason,
        status=task.status,
        created_at=task.created_at,
        extraction=ReviewTaskExtractionOut.model_validate(ext),
        loan=ReviewTaskLoanOut.model_validate(loan),
        document=ReviewTaskDocumentOut.model_validate(doc),
    )


# --- Endpoints ---


@router.get("/review-tasks", response_model=list[ReviewTaskOut])
async def list_review_tasks(
    user: CurrentUserDep,
    db: DbSessionDep,
    status_filter: str = "open",
    limit: int = 100,
) -> list[ReviewTaskOut]:
    """All review tasks across all loans. Default: open only.

    Joins extraction → document → loan so the list-view row can show all
    the context without per-row N+1.
    """
    stmt = (
        select(ReviewTask)
        .options(
            joinedload(ReviewTask.extraction)
            .joinedload(Extraction.document)
            .joinedload(Document.loan)
        )
        .where(ReviewTask.status == status_filter)
        .order_by(ReviewTask.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).unique().scalars().all()
    return [_row_to_review_task_out(t) for t in rows]


@router.get("/review-tasks/{task_id}", response_model=ReviewTaskOut)
async def get_review_task(
    task_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> ReviewTaskOut:
    task = await _load_task(db, task_id)
    return _row_to_review_task_out(task)


@router.get("/review-tasks/{task_id}/source", response_model=ExtractionSourceOut)
async def get_review_task_source(
    task_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> ExtractionSourceOut:
    """The full source view for this task: extraction + document text +
    loan context. The DocSourceViewer renders the document text with the
    source-span quote highlighted."""
    task = await _load_task(db, task_id)
    ext = task.extraction
    doc = ext.document
    text_content = (doc.meta or {}).get("text_content", "")
    return ExtractionSourceOut(
        extraction=ReviewTaskExtractionOut.model_validate(ext),
        document=ReviewTaskDocumentOut.model_validate(doc),
        loan=ReviewTaskLoanOut.model_validate(doc.loan),
        document_text=text_content,
    )


@router.post(
    "/review-tasks/{task_id}/accept",
    response_model=ReviewTaskOut,
    status_code=status.HTTP_200_OK,
)
async def accept_review_task(
    task_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> ReviewTaskOut:
    """Ratify the AI's extracted value. Status → accepted; task → resolved."""
    task = await _load_task(db, task_id)
    ext = task.extraction
    loan = ext.document.loan

    ext.status = ExtractionStatus.ACCEPTED
    task.status = "resolved"
    task.notes = "Accepted by underwriter."

    await record(
        db,
        loan_id=loan.id,
        actor=Actor.user(user.user_id),
        action="extraction_accepted",
        payload={
            "extraction_id": str(ext.id),
            "field_name": ext.field_name,
            "value": ext.value,
            "confidence": ext.confidence,
            "review_task_id": str(task.id),
        },
    )
    await db.commit()
    await db.refresh(task)
    return _row_to_review_task_out(task)


@router.post(
    "/review-tasks/{task_id}/override",
    response_model=ReviewTaskOut,
    status_code=status.HTTP_200_OK,
)
async def override_review_task(
    task_id: uuid.UUID,
    payload: OverrideIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> ReviewTaskOut:
    """Human correction of the AI's extracted value. Records both old and
    new values in the audit log — that's the data the calibration log uses
    to tune per-field thresholds quarterly (DESIGN §7.2)."""
    task = await _load_task(db, task_id)
    ext = task.extraction
    loan = ext.document.loan
    old_value = ext.value

    ext.value = payload.value
    ext.status = ExtractionStatus.OVERRIDDEN
    task.status = "resolved"
    task.notes = payload.notes or "Overridden by underwriter."

    await record(
        db,
        loan_id=loan.id,
        actor=Actor.user(user.user_id),
        action="extraction_overridden",
        payload={
            "extraction_id": str(ext.id),
            "field_name": ext.field_name,
            "old_value": old_value,
            "new_value": payload.value,
            "old_confidence": ext.confidence,
            "review_task_id": str(task.id),
            "notes": payload.notes,
        },
    )
    await db.commit()
    await db.refresh(task)
    return _row_to_review_task_out(task)


# --- Extraction source endpoint (also addressable via extraction_id) ---


@router.get("/extractions/{extraction_id}/source", response_model=ExtractionSourceOut)
async def get_extraction_source(
    extraction_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> ExtractionSourceOut:
    """Same payload as the review-task source — handy when the caller has the
    extraction id but not the task id (e.g. from the workspace's citation
    chips in the future)."""
    stmt = (
        select(Extraction)
        .options(joinedload(Extraction.document).joinedload(Document.loan))
        .where(Extraction.id == extraction_id)
    )
    ext = (await db.execute(stmt)).unique().scalar_one_or_none()
    if not ext:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction not found")
    doc = ext.document
    return ExtractionSourceOut(
        extraction=ReviewTaskExtractionOut.model_validate(ext),
        document=ReviewTaskDocumentOut.model_validate(doc),
        loan=ReviewTaskLoanOut.model_validate(doc.loan),
        document_text=(doc.meta or {}).get("text_content", ""),
    )


# --- Internals ---


async def _load_task(db: Any, task_id: uuid.UUID) -> ReviewTask:
    stmt = (
        select(ReviewTask)
        .options(
            joinedload(ReviewTask.extraction)
            .joinedload(Extraction.document)
            .joinedload(Document.loan)
        )
        .where(ReviewTask.id == task_id)
    )
    task = (await db.execute(stmt)).unique().scalar_one_or_none()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review task not found")
    return task
