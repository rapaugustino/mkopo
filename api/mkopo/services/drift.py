"""Drift monitor — turns observed human-review outcomes into eval rows.

Why this exists
---------------

The CI eval gate (``source='golden'``) tells us how the extractor does on
a fixed labelled set; that catches regressions in the model or prompts.
It does NOT catch the case where the production document mix shifts —
e.g. a new appraisal vendor whose template confuses the model on a
single field. For that we need to score the extractor against what
humans actually saw and corrected.

How we score it
---------------

For every ``Extraction`` row where the status has resolved past
``proposed`` / ``queued_for_review``, we treat:

- ``accepted`` as the AI got it right (either auto-accepted above
  threshold, or a reviewer hit "accept" on the queued task), and
- ``overridden`` as the AI got it wrong (reviewer typed a new value).

That's a coarse proxy — an auto-accepted high-confidence extraction was
never actually verified — but it's the same signal humans see in the
review queue, so it's the right thing to alert on.

We sample the most recent ``window_size`` resolved extractions per
field, compute accuracy, and write one ``task_runs`` row per field with
``source='production'`` and ``task_name=f'extraction.{field_name}'``.
The dashboard joins those against the matching ``source='golden'`` rows
to render the weekly trend.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import Extraction, ExtractionStatus
from mkopo.models.eval import TaskRun

logger = structlog.get_logger()

# Default sample window per field. The DESIGN doc calls for 200; in dev
# that's almost always more than we have, and the per-field group_by
# below will just include everything available.
DEFAULT_WINDOW = 200

# Below this many resolved extractions on a field, accuracy is too
# noisy to write a row. Skip and log.
MIN_SAMPLES_PER_FIELD = 5


@dataclass(frozen=True)
class FieldResult:
    """One row of drift output, suitable for direct serialisation to the API."""

    field_name: str
    n: int
    accepted: int
    overridden: int
    accuracy: float
    avg_confidence: float


async def compute_field_accuracy(
    session: AsyncSession,
    window_size: int = DEFAULT_WINDOW,
) -> list[FieldResult]:
    """Compute per-field accuracy over the most recent resolved extractions.

    "Resolved" = status in ``(accepted, overridden)``. We pull
    ``window_size`` rows per field by sorting on ``updated_at`` desc
    inside Python — the population is small enough (≤ a few thousand)
    that a window function would be overkill and harder to read.
    """
    stmt = (
        select(Extraction)
        .where(
            Extraction.status.in_(
                (ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN)
            )
        )
        .order_by(desc(Extraction.updated_at))
    )
    rows = (await session.execute(stmt)).scalars().all()

    by_field: dict[str, list[Extraction]] = defaultdict(list)
    for r in rows:
        bucket = by_field[r.field_name]
        if len(bucket) < window_size:
            bucket.append(r)

    results: list[FieldResult] = []
    for field_name, extractions in by_field.items():
        n = len(extractions)
        if n < MIN_SAMPLES_PER_FIELD:
            logger.debug(
                "drift_skip_low_n",
                field=field_name,
                n=n,
                min=MIN_SAMPLES_PER_FIELD,
            )
            continue
        accepted = sum(1 for e in extractions if e.status == ExtractionStatus.ACCEPTED)
        overridden = n - accepted
        accuracy = accepted / n
        avg_conf = sum(e.confidence for e in extractions) / n
        results.append(
            FieldResult(
                field_name=field_name,
                n=n,
                accepted=accepted,
                overridden=overridden,
                accuracy=accuracy,
                avg_confidence=avg_conf,
            )
        )

    results.sort(key=lambda r: r.field_name)
    return results


async def run_drift_monitor(
    session: AsyncSession,
    window_size: int = DEFAULT_WINDOW,
) -> list[TaskRun]:
    """Compute drift, persist one ``task_runs`` row per field, return them.

    Called by:
    - the nightly Arq cron in ``mkopo/workers/tasks.py``
    - the ``/eval/refresh`` endpoint (manual trigger from the dashboard)

    Idempotent enough — re-running on the same day creates duplicate rows
    with later timestamps. The dashboard treats the latest row per
    (task_name, source) day as authoritative, so duplicates just narrow
    the trend granularity.
    """
    field_results = await compute_field_accuracy(session, window_size=window_size)

    persisted: list[TaskRun] = []
    for fr in field_results:
        row = TaskRun(
            task_name=f"extraction.{fr.field_name}",
            source="production",
            n=fr.n,
            accuracy=fr.accuracy,
            avg_score=fr.avg_confidence,
            details={
                "accepted": fr.accepted,
                "overridden": fr.overridden,
                "window_size": window_size,
            },
        )
        session.add(row)
        persisted.append(row)

    await session.flush()
    logger.info(
        "drift_monitor_ran",
        fields=len(persisted),
        window_size=window_size,
    )
    return persisted
