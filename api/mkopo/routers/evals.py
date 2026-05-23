"""Eval dashboard endpoints.

Three reads + one write, all keyed off ``task_runs`` and ``llm_calls``:

- ``GET  /eval/summary`` — headline KPIs (latest production vs golden, LLM
  calls in last 24h, p95 latency).
- ``GET  /eval/fields``  — per-field rollup with the drift delta the
  dashboard chips off.
- ``GET  /eval/trend``   — series for the weekly chart (last ``days``
  days, default 30).
- ``POST /eval/refresh`` — manually run the drift monitor and return its
  result. The Arq cron also runs it nightly; this gives the demo a
  "run now" button.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import desc, select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models.eval import LLMCall, TaskRun
from mkopo.services.drift import run_drift_monitor

router = APIRouter(prefix="/eval", tags=["eval"])


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
    overall_production_accuracy: float | None
    overall_golden_accuracy: float | None
    overall_delta: float | None
    fields_tracked: int
    fields_drifting: int  # count where delta <= -drift_threshold
    drift_threshold: float
    llm_calls_24h: int
    llm_p95_latency_seconds: float | None
    llm_error_rate_24h: float | None


class TrendPoint(BaseModel):
    task_name: str
    source: str
    created_at: datetime
    accuracy: float
    n: int


class TrendResponse(BaseModel):
    days: int
    points: list[TrendPoint]


class RefreshResponse(BaseModel):
    status: str
    fields_written: int


# ----- helpers ------------------------------------------------------------


# Drift below the golden baseline by this much (3 percentage points)
# is the threshold the DESIGN doc calls out for alerting.
DRIFT_THRESHOLD = 0.03


def _latest_per_field(
    rows: list[TaskRun],
    source: str,
) -> dict[str, TaskRun]:
    """Pick the most recent row for each task_name + source pairing.

    ``rows`` is already ordered ``created_at DESC``, so the first hit
    per ``task_name`` wins.
    """
    out: dict[str, TaskRun] = {}
    for r in rows:
        if r.source != source:
            continue
        if r.task_name not in out:
            out[r.task_name] = r
    return out


def _percentile(values: list[float], pct: float) -> float | None:
    """Plain nearest-rank percentile. ``values`` is mutated (sorted).

    No numpy dep needed for one call from one endpoint.
    """
    if not values:
        return None
    values.sort()
    k = max(0, min(len(values) - 1, int(round((pct / 100.0) * (len(values) - 1)))))
    return values[k]


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
    rows = (
        await db.execute(select(TaskRun).order_by(desc(TaskRun.created_at)))
    ).scalars().all()

    latest_prod = _latest_per_field(list(rows), "production")
    latest_gold = _latest_per_field(list(rows), "golden")

    prod_acc = (
        sum(r.accuracy for r in latest_prod.values()) / len(latest_prod)
        if latest_prod
        else None
    )
    gold_acc = (
        sum(r.accuracy for r in latest_gold.values()) / len(latest_gold)
        if latest_gold
        else None
    )
    delta = (prod_acc - gold_acc) if (prod_acc is not None and gold_acc is not None) else None

    # Per-field drift count uses paired (prod, gold) only.
    drifting = 0
    for name, prod_row in latest_prod.items():
        gold_row = latest_gold.get(name)
        if gold_row is None:
            continue
        if prod_row.accuracy - gold_row.accuracy <= -DRIFT_THRESHOLD:
            drifting += 1

    # LLM call stats from the last 24h.
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    calls_24h = (
        await db.execute(
            select(LLMCall.elapsed_seconds, LLMCall.status).where(LLMCall.created_at >= cutoff)
        )
    ).all()
    latencies = [c.elapsed_seconds for c in calls_24h]
    p95 = _percentile(latencies, 95)
    errors = sum(1 for c in calls_24h if c.status != "ok")
    error_rate = errors / len(calls_24h) if calls_24h else None

    fields_tracked = len({*latest_prod.keys(), *latest_gold.keys()})

    return EvalSummary(
        overall_production_accuracy=prod_acc,
        overall_golden_accuracy=gold_acc,
        overall_delta=delta,
        fields_tracked=fields_tracked,
        fields_drifting=drifting,
        drift_threshold=DRIFT_THRESHOLD,
        llm_calls_24h=len(calls_24h),
        llm_p95_latency_seconds=p95,
        llm_error_rate_24h=error_rate,
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
    rows = (
        await db.execute(select(TaskRun).order_by(desc(TaskRun.created_at)))
    ).scalars().all()
    latest_prod = _latest_per_field(list(rows), "production")
    latest_gold = _latest_per_field(list(rows), "golden")

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

    stmt = (
        select(TaskRun)
        .where(TaskRun.created_at >= cutoff)
        .order_by(TaskRun.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
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
        ],
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_drift(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> RefreshResponse:
    """Manually re-run the drift monitor.

    Identical to the Arq cron job — useful for the "Refresh" button on
    the dashboard so demos don't have to wait for 3 AM UTC. Commits
    rows via the request-scoped session.
    """
    persisted = await run_drift_monitor(db)
    await db.commit()
    return RefreshResponse(status="ok", fields_written=len(persisted))


# Stats: total llm_calls so the dashboard can show a sparkline of
# call volume per hour over the last 24h. Folded into /summary at
# present; kept here as a deliberate non-export, will surface if the
# eval page grows a third tile row.
__all__ = ["router"]
