"""Generic per-task accessor for the latest task_run row.

Used by the dashboard to fetch the richer per-task details (confusion
matrix, per-criterion rates, calibration bins) that AggregatingEvalTask
subclasses emit via their ``aggregate()`` hook. Without this, the
flat ``/eval/summary`` payload would be the only way to see a task's
state and the structured details would be unreachable.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import desc, select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models.eval import TaskRun

router = APIRouter()


class TaskDetailResponse(BaseModel):
    """Latest task_run snapshot for one task_name. ``details`` is
    the JSONB payload the AggregatingEvalTask wrote — shape depends
    on the task. The frontend dispatches on task_name to render the
    right card. Returns ``None``-valued fields when no run exists
    yet (fresh DB, task disabled, etc.) so the UI can render an
    empty state instead of an error."""

    task_name: str
    found: bool
    accuracy: float | None
    avg_score: float | None
    n: int | None
    source: str | None
    ran_at: datetime | None
    details: dict | None


@router.get("/task-detail/{task_name:path}", response_model=TaskDetailResponse)
async def task_detail(
    task_name: str,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> TaskDetailResponse:
    """Return the latest task_run for ``task_name`` with its full
    details payload.

    ``task_name`` can contain dots (e.g. ``calibration.extractor_confidence``)
    — the ``{task_name:path}`` converter accepts them. Pick the most
    recent row regardless of source; the dashboard cards specialise
    on task_name, not on golden-vs-production.
    """
    row = (
        await db.execute(
            select(TaskRun)
            .where(TaskRun.task_name == task_name)
            .order_by(desc(TaskRun.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return TaskDetailResponse(
            task_name=task_name,
            found=False,
            accuracy=None,
            avg_score=None,
            n=None,
            source=None,
            ran_at=None,
            details=None,
        )
    return TaskDetailResponse(
        task_name=row.task_name,
        found=True,
        accuracy=row.accuracy,
        avg_score=row.avg_score,
        n=row.n,
        source=row.source,
        ran_at=row.created_at,
        details=row.details or {},
    )
