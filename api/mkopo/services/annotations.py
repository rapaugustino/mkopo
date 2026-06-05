"""Annotation service — create / list / delete + auto-spawn review tasks.

The /eval drawer buttons go through this module so the "mark bad,
spawn a review task" path is one atomic write (annotation + review
task land in the same session.flush, both reference each other via
``spawned_review_task_id``).

Behavioural rules:

- A "good" annotation just persists the row. No follow-up.
- A "bad" or "incorrect" annotation persists the row AND, when the
  trace is linkable to a loan, creates a ``review_tasks`` row
  pointing at the most recent low-confidence / queued extraction on
  that loan. The review-task's ``reason`` carries the verdict + note
  so the reviewer sees why they're being routed here.

Linkability rules:

- ``llm_call``  — ``thread_id`` ties the call to an ``agent_run``,
  whose ``loan_id`` is the link.
- ``agent_run`` — has ``loan_id`` directly.
- ``agent_step`` — through its ``agent_run`` → ``loan_id``.

When linkability fails (a free-form LLM call from a CLI smoke test,
say) we still persist the annotation but skip the spawn. The dashboard
can still surface the "X bad annotations this week" rollup; the only
thing the operator loses is the auto-routing.
"""

from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import (
    AgentRun,
    AgentStep,
    Annotation,
    AnnotationTargetKind,
    AnnotationVerdict,
    Extraction,
    ExtractionStatus,
    ReviewTask,
)
from mkopo.models.eval import LLMCall

_REVIEW_VERDICTS = {AnnotationVerdict.BAD.value, AnnotationVerdict.INCORRECT.value}


async def create(
    session: AsyncSession,
    *,
    target_kind: str,
    target_id: uuid.UUID,
    verdict: str,
    note: str | None,
    created_by_user_id: uuid.UUID | None,
) -> Annotation:
    """Persist one annotation. Spawns a review task on bad/incorrect.

    The closed-set validation here is the boundary check; the API
    schema declares the enums too but we re-check because the
    constants live in the ORM module and that's the canonical source
    of truth.
    """
    if target_kind not in {k.value for k in AnnotationTargetKind}:
        raise ValueError(
            f"target_kind must be one of "
            f"{sorted(k.value for k in AnnotationTargetKind)}, got {target_kind!r}."
        )
    if verdict not in {v.value for v in AnnotationVerdict}:
        raise ValueError(
            f"verdict must be one of {sorted(v.value for v in AnnotationVerdict)}, got {verdict!r}."
        )

    row = Annotation(
        target_kind=target_kind,
        target_id=target_id,
        verdict=verdict,
        note=(note or None),
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    await session.flush()

    if verdict in _REVIEW_VERDICTS:
        review_task = await _spawn_review_task_for(
            session,
            target_kind=target_kind,
            target_id=target_id,
            verdict=verdict,
            note=note,
        )
        if review_task is not None:
            row.spawned_review_task_id = review_task.id
            await session.flush()

    return row


async def list_for_target(
    session: AsyncSession,
    *,
    target_kind: str,
    target_id: uuid.UUID,
) -> list[Annotation]:
    """All annotations on one trace, newest first.

    Drives the "Existing annotations" section the drawers render
    below the verdict buttons. Limit is intentionally absent — the
    population is bounded (a trace doesn't get hundreds of
    annotations).
    """
    rows = (
        (
            await session.execute(
                select(Annotation)
                .where(
                    Annotation.target_kind == target_kind,
                    Annotation.target_id == target_id,
                )
                .order_by(desc(Annotation.created_at))
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def delete(session: AsyncSession, *, annotation_id: uuid.UUID) -> bool:
    """Hard-delete one annotation. Returns True if a row was removed.

    Doesn't try to roll back the spawned review_task — those have
    their own lifecycle once created (a reviewer may have already
    started working it). The annotation row going away just means
    the "verdict tally" rolls back; the review queue stays intact.
    """
    row = (
        await session.execute(select(Annotation).where(Annotation.id == annotation_id))
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


# ----- private --------------------------------------------------------------


async def _loan_id_for_target(
    session: AsyncSession,
    *,
    target_kind: str,
    target_id: uuid.UUID,
) -> uuid.UUID | None:
    """Resolve the loan_id the trace is attached to, or None.

    See module docstring for the resolution rules per target kind.
    """
    if target_kind == AnnotationTargetKind.AGENT_RUN.value:
        run = (
            await session.execute(select(AgentRun).where(AgentRun.id == target_id))
        ).scalar_one_or_none()
        return run.loan_id if run else None

    if target_kind == AnnotationTargetKind.AGENT_STEP.value:
        step = (
            await session.execute(select(AgentStep).where(AgentStep.id == target_id))
        ).scalar_one_or_none()
        if step is None:
            return None
        run = (
            await session.execute(select(AgentRun).where(AgentRun.id == step.agent_run_id))
        ).scalar_one_or_none()
        return run.loan_id if run else None

    if target_kind == AnnotationTargetKind.LLM_CALL.value:
        call = (
            await session.execute(select(LLMCall).where(LLMCall.id == target_id))
        ).scalar_one_or_none()
        if call is None or call.thread_id is None:
            return None
        # thread_id on llm_calls matches AgentRun.thread_id (set by
        # the streaming layer when the call happens inside an agent
        # run — see ContextVar plumbing in mkopo.agents.context).
        run = (
            await session.execute(select(AgentRun).where(AgentRun.thread_id == call.thread_id))
        ).scalar_one_or_none()
        return run.loan_id if run else None

    return None


async def _pick_extraction_to_route(
    session: AsyncSession, *, loan_id: uuid.UUID
) -> Extraction | None:
    """Find a sensible extraction to attach a follow-up review task to.

    Preference order:

    1. An already low-confidence / queued extraction on the loan —
       routing the annotation there piggy-backs on the existing
       review queue surface.
    2. Else the most recent ``proposed`` extraction — those are the
       AI's freshest claims and most likely to need a second look.
    3. Else None — annotation persists, no spawn.

    The review task created against the picked extraction reads
    "annotated as bad/incorrect by <user>: <note>" so the reviewer
    sees the context that brought the task to their queue.
    """
    queued = (
        await session.execute(
            select(Extraction)
            .join(Extraction.document)
            .where(
                Extraction.status.in_(
                    (
                        ExtractionStatus.QUEUED_FOR_REVIEW,
                        ExtractionStatus.PROPOSED,
                    )
                ),
            )
            .order_by(desc(Extraction.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if queued is not None:
        # Cross-check it belongs to this loan — the join already
        # filters, but the explicit fetch is cheap and makes the
        # intent obvious.
        from mkopo.models import Document

        doc = (
            await session.execute(select(Document).where(Document.id == queued.document_id))
        ).scalar_one_or_none()
        if doc is not None and doc.loan_id == loan_id:
            return queued
    return None


async def _spawn_review_task_for(
    session: AsyncSession,
    *,
    target_kind: str,
    target_id: uuid.UUID,
    verdict: str,
    note: str | None,
) -> ReviewTask | None:
    """If we can find a loan + a routable extraction, create a review
    task and return it. Otherwise return None.

    Failure here is non-fatal — see module docstring. The annotation
    still persists; the spawn is best-effort.
    """
    loan_id = await _loan_id_for_target(session, target_kind=target_kind, target_id=target_id)
    if loan_id is None:
        return None

    target = await _pick_extraction_to_route(session, loan_id=loan_id)
    if target is None:
        return None

    reason_parts = [f"Annotated as {verdict} on a {target_kind.replace('_', ' ')}."]
    if note:
        reason_parts.append(note.strip()[:200])
    review_task = ReviewTask(
        extraction_id=target.id,
        reason=" — ".join(reason_parts)[:256],
        status="open",
        notes=note,
    )
    session.add(review_task)
    await session.flush()
    return review_task
