"""Trace-annotation endpoints — humans recording verdicts on
``llm_calls`` / ``agent_runs`` / ``agent_steps``.

Three thin HTTP wrappers around ``mkopo.services.annotations``.
Lives under /eval (not its own router) because the eval page is the
canonical home for "what humans thought of these traces" — the
observability drawers consume the same endpoints but are not the
semantic owner of the resource.

"bad" or "incorrect" verdicts auto-spawn a ``review_tasks`` row when
the trace can be linked back to a loan; see
``mkopo.services.annotations`` for the linkage rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import Annotation, AnnotationTargetKind
from mkopo.services import annotations as annotations_service

router = APIRouter()


class AnnotationOut(BaseModel):
    """One annotation, shaped for the drawer + dashboard reads."""

    id: str
    target_kind: str
    target_id: str
    verdict: str
    note: str | None
    created_by_user_id: str | None
    created_at: datetime
    spawned_review_task_id: str | None


class AnnotationCreateIn(BaseModel):
    """Inputs for ``POST /eval/annotations``.

    The closed-set enums are validated server-side in the service
    layer too; declaring them here gets the OpenAPI schema right and
    rejects obvious bad payloads at the boundary.
    """

    target_kind: str = Field(
        description=(
            "One of llm_call, agent_run, agent_step — what kind of "
            "trace this annotation applies to."
        )
    )
    target_id: uuid.UUID
    verdict: str = Field(
        description="One of good, bad, incorrect.",
    )
    note: str | None = Field(default=None, max_length=4000)


def _to_out(row: Annotation) -> AnnotationOut:
    return AnnotationOut(
        id=str(row.id),
        target_kind=row.target_kind,
        target_id=str(row.target_id),
        verdict=row.verdict,
        note=row.note,
        created_by_user_id=(str(row.created_by_user_id) if row.created_by_user_id else None),
        created_at=row.created_at,
        spawned_review_task_id=(
            str(row.spawned_review_task_id) if row.spawned_review_task_id else None
        ),
    )


def _looks_like_uuid(s: str) -> bool:
    """``user.user_id`` is a string in dev (bearer dev-user) — coerce
    safely. Same pattern as routers/staff_chat.py.
    """
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


@router.get("/annotations", response_model=list[AnnotationOut])
async def list_annotations(
    target_kind: str,
    target_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> list[AnnotationOut]:
    """List annotations on one trace row, newest first.

    Drives the "Existing annotations" section the drawers render
    under the verdict buttons.
    """
    if target_kind not in {k.value for k in AnnotationTargetKind}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown target_kind.",
        )
    rows = await annotations_service.list_for_target(
        db, target_kind=target_kind, target_id=target_id
    )
    return [_to_out(r) for r in rows]


@router.post(
    "/annotations",
    response_model=AnnotationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_annotation(
    payload: AnnotationCreateIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> AnnotationOut:
    """Persist a verdict + optional note on a trace row.

    "bad" or "incorrect" verdicts auto-spawn a ``review_tasks`` row
    when we can link the trace back to a loan — see
    mkopo.services.annotations for the linkage rules. The response
    carries ``spawned_review_task_id`` so the frontend can render
    "+ added to review queue" inline.
    """
    try:
        row = await annotations_service.create(
            db,
            target_kind=payload.target_kind,
            target_id=payload.target_id,
            verdict=payload.verdict,
            note=payload.note,
            created_by_user_id=(
                uuid.UUID(user.user_id) if _looks_like_uuid(user.user_id) else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    return _to_out(row)


@router.delete(
    "/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_annotation(
    annotation_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> None:
    """Hard-delete one annotation. 204 on success, 404 if not found.

    Spawned review tasks are NOT cascaded — see the service docstring.
    A reviewer may have already picked the task up; the verdict tally
    rolls back without disrupting in-flight work.
    """
    removed = await annotations_service.delete(db, annotation_id=annotation_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Annotation not found.",
        )
    await db.commit()
