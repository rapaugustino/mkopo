"""Headline KPIs + per-field table + trend chart endpoints.

The dashboard's first three reads:

- ``GET /eval/summary`` — headline KPIs (production vs golden,
  LLM call stats with the 24h → 7d → all-time cascade)
- ``GET /eval/fields``  — per-field rollup (the table)
- ``GET /eval/trend``   — series for the chart (last N days)

All three filter to accuracy-shaped tasks via ``_is_accuracy_metric``
in ``_shared`` — derived monitors (PSI, AIR, ECE, etc.) have their
own dashboard cards and would corrupt these averages.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models.eval import LLMCall, TaskRun
from mkopo.routers.evals._shared import (
    DRIFT_THRESHOLD,
    _is_accuracy_metric,
    _latest_accuracy_rows,
    _percentile,
    compute_summary_aggregates,
)

router = APIRouter()


# ----- response models ----------------------------------------------------


class FieldRow(BaseModel):
    """One row of the per-field table.

    ``production_accuracy`` / ``golden_accuracy`` are ``None`` when no
    row of that source has been written yet (fresh install, or a field
    only the eval suite covers). The frontend treats ``None`` as "no
    data" rather than 0.
    """

    field_name: str
    production_accuracy: float | None
    production_n: int | None
    production_at: datetime | None
    golden_accuracy: float | None
    golden_n: int | None
    golden_at: datetime | None
    delta: float | None  # production - golden, when both are known


class EvalSummary(BaseModel):
    # Apples-to-apples production vs golden — same task set on both
    # sides. ``overall_production_accuracy`` and ``overall_golden_accuracy``
    # are means over the INTERSECTION of (latest_prod, latest_gold) by
    # task_name. Comparing the full production set (extraction-only,
    # because the drift monitor only writes those) against the full
    # golden set (12 tasks) would produce a misleading delta — see the
    # June 2026 cleanup. ``fields_tracked`` is the size of that
    # intersection.
    overall_production_accuracy: float | None
    overall_golden_accuracy: float | None
    overall_delta: float | None
    fields_tracked: int
    fields_drifting: int  # count where delta <= -drift_threshold
    drift_threshold: float
    # Full eval suite — the average across ALL latest golden rows
    # (not just those with a production counterpart). Surfaced as a
    # separate tile so the dashboard reads honestly: ``overall_golden``
    # answers "how does golden look on the things we monitor live",
    # while ``golden_suite`` answers "how is the eval suite doing
    # overall". Confusing them was the pre-cleanup bug.
    golden_suite_accuracy: float | None
    golden_suite_n_tasks: int
    # LLM stats. We try 24h first, but cascade to 7d → all-time so a
    # demo environment that hasn't had a call today still shows
    # meaningful numbers. ``llm_window_label`` reports which window the
    # stats actually came from ("24h", "7d", or "all-time") so the
    # frontend can label the tiles honestly.
    llm_calls_24h: int
    llm_p95_latency_seconds: float | None
    llm_error_rate_24h: float | None
    llm_window_label: str = "24h"


class TrendPoint(BaseModel):
    task_name: str
    source: str
    created_at: datetime
    accuracy: float
    n: int


class TrendResponse(BaseModel):
    days: int
    points: list[TrendPoint]


# ----- helpers ------------------------------------------------------------


async def _llm_calls_with_fallback(
    db: AsyncSession,
) -> tuple[list[tuple[float, str]], str]:
    """Return ``(rows, window_label)`` for the LLM stats tiles.

    Tries 24h first, then 7d, then all-time. The label tells the
    frontend which window actually carried the data so it can render
    "p95 over last 7 days" instead of leaving an unexplained "—".

    A fresh demo install will fall through to "all-time" on first
    page load; once an agent run happens the next refresh will snap
    back to "24h".
    """
    windows: list[tuple[str, datetime | None]] = [
        ("24h", datetime.now(UTC) - timedelta(hours=24)),
        ("7d", datetime.now(UTC) - timedelta(days=7)),
        ("all-time", None),
    ]
    for label, cutoff in windows:
        stmt = select(LLMCall.elapsed_seconds, LLMCall.status)
        if cutoff is not None:
            stmt = stmt.where(LLMCall.created_at >= cutoff)
        rows = (await db.execute(stmt)).all()
        if rows:
            # Each Row tuples-up as (elapsed_seconds, status) — convert
            # to plain tuples so the return type is honest about the
            # shape and downstream consumers don't depend on Row's
            # attribute-access API.
            return [(r[0], r[1]) for r in rows], label
    return [], "24h"


# ----- endpoints ----------------------------------------------------------


@router.get("/summary", response_model=EvalSummary)
async def get_eval_summary(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> EvalSummary:
    """Top-of-dashboard KPIs.

    "Overall accuracy" is the unweighted mean of the latest per-field
    accuracy. Weighting by ``n`` would let one chatty field dominate;
    the dashboard's job is to surface per-field drift, not to optimise
    portfolio-wide volume.
    """
    rows = (await db.execute(select(TaskRun).order_by(desc(TaskRun.created_at)))).scalars().all()

    # Accuracy headline uses ONLY accuracy-shaped tasks — see
    # ``_is_accuracy_metric``. Derived monitors (fairness AIR,
    # PSI, economics $/run, refusal block rate, calibration ECE)
    # write to ``task_runs.accuracy`` for trend-chart compatibility
    # but mean entirely different things across rows; averaging
    # them produces a nonsense number.
    latest_prod = _latest_accuracy_rows(list(rows), "production")
    latest_gold = _latest_accuracy_rows(list(rows), "golden")

    # Headline aggregation is delegated to the pure
    # ``compute_summary_aggregates`` helper so the pairing /
    # apples-to-apples logic is unit-testable independently of the
    # async session, the SQL, and the FastAPI runtime. The helper
    # accepts pre-flattened {task_name: accuracy} maps; we adapt
    # the ORM rows here.
    agg = compute_summary_aggregates(
        prod_by_task={k: r.accuracy for k, r in latest_prod.items()},
        gold_by_task={k: r.accuracy for k, r in latest_gold.items()},
    )
    prod_acc = agg.prod_acc
    gold_acc = agg.gold_acc
    delta = agg.delta
    suite_acc = agg.suite_acc
    drifting = agg.drifting

    # LLM call stats. Cascade through windows so a quiet 24h doesn't
    # leave the page showing "—" everywhere — the same stat at "7d" or
    # "all-time" is much more informative on a low-traffic install.
    calls, window_label = await _llm_calls_with_fallback(db)
    latencies = [elapsed for elapsed, _status in calls]
    p95 = _percentile(latencies, 95)
    errors = sum(1 for _elapsed, status in calls if status != "ok")
    error_rate = errors / len(calls) if calls else None

    return EvalSummary(
        overall_production_accuracy=prod_acc,
        overall_golden_accuracy=gold_acc,
        overall_delta=delta,
        # ``fields_tracked`` is the intersection size — same
        # denominator as the three values above. Comes straight
        # from the pure aggregator so the test pins this too.
        fields_tracked=agg.fields_tracked,
        fields_drifting=drifting,
        drift_threshold=DRIFT_THRESHOLD,
        golden_suite_accuracy=suite_acc,
        golden_suite_n_tasks=agg.n_suite_tasks,
        llm_calls_24h=len(calls),
        llm_p95_latency_seconds=p95,
        llm_error_rate_24h=error_rate,
        llm_window_label=window_label,
    )


@router.get("/fields", response_model=list[FieldRow])
async def get_eval_fields(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> list[FieldRow]:
    """One row per ``task_name`` known to either production or golden.

    Sorted by delta ascending (largest drop first) so the worst fields
    surface at the top of the table. Fields without both sides sort to
    the bottom — they can't drift if there's no baseline.
    """
    rows = (await db.execute(select(TaskRun).order_by(desc(TaskRun.created_at)))).scalars().all()
    # Same accuracy-only filter as ``/summary`` — the per-field table
    # is for accuracy drift, not for surfacing the derived monitors
    # (those have dedicated cards under /eval).
    latest_prod = _latest_accuracy_rows(list(rows), "production")
    latest_gold = _latest_accuracy_rows(list(rows), "golden")

    names = {*latest_prod.keys(), *latest_gold.keys()}
    out: list[FieldRow] = []
    for name in names:
        p = latest_prod.get(name)
        g = latest_gold.get(name)
        delta = (p.accuracy - g.accuracy) if (p and g) else None
        out.append(
            FieldRow(
                field_name=name,
                production_accuracy=p.accuracy if p else None,
                production_n=p.n if p else None,
                production_at=p.created_at if p else None,
                golden_accuracy=g.accuracy if g else None,
                golden_n=g.n if g else None,
                golden_at=g.created_at if g else None,
                delta=delta,
            )
        )

    # Largest drop first; nulls last.
    out.sort(key=lambda r: (r.delta is None, r.delta if r.delta is not None else 0))
    return out


@router.get("/trend", response_model=TrendResponse)
async def get_eval_trend(
    user: CurrentUserDep,
    db: DbSessionDep,
    days: int = 30,
) -> TrendResponse:
    """All ``task_runs`` from the last ``days`` days, oldest first.

    The frontend groups by ``(task_name, source)`` into one line per
    series. Keeping the grouping client-side is cheap (sub-thousand
    points) and lets the frontend re-shape without another round trip.
    """
    days = max(1, min(days, 365))
    cutoff = datetime.now(UTC) - timedelta(days=days)

    stmt = select(TaskRun).where(TaskRun.created_at >= cutoff).order_by(TaskRun.created_at.asc())
    rows = (await db.execute(stmt)).scalars().all()
    # Same accuracy-only filter as ``/summary`` — the chart's y-axis
    # is a 0–1 accuracy scale, so plotting fairness AIR (also 0–1
    # but lower-is-better), PSI (≥0, unbounded), or cost-per-run
    # ($, on the same axis) is misleading at best. Those metrics
    # have dedicated cards.
    return TrendResponse(
        days=days,
        points=[
            TrendPoint(
                task_name=r.task_name,
                source=r.source,
                created_at=r.created_at,
                accuracy=r.accuracy,
                n=r.n,
            )
            for r in rows
            if _is_accuracy_metric(r.task_name)
        ],
    )
