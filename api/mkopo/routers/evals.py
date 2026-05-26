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

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import (
    AgentRun,
    AgentStep,
    Annotation,
    AnnotationTargetKind,
    Extraction,
    ExtractionStatus,
    ReviewTask,
)
from mkopo.models.eval import LLMCall, TaskRun
from mkopo.services import annotations as annotations_service
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

    # LLM call stats. Cascade through windows so a quiet 24h doesn't
    # leave the page showing "—" everywhere — the same stat at "7d" or
    # "all-time" is much more informative on a low-traffic install.
    calls, window_label = await _llm_calls_with_fallback(db)
    latencies = [elapsed for elapsed, _status in calls]
    p95 = _percentile(latencies, 95)
    errors = sum(1 for _elapsed, status in calls if status != "ok")
    error_rate = errors / len(calls) if calls else None

    fields_tracked = len({*latest_prod.keys(), *latest_gold.keys()})

    return EvalSummary(
        overall_production_accuracy=prod_acc,
        overall_golden_accuracy=gold_acc,
        overall_delta=delta,
        fields_tracked=fields_tracked,
        fields_drifting=drifting,
        drift_threshold=DRIFT_THRESHOLD,
        llm_calls_24h=len(calls),
        llm_p95_latency_seconds=p95,
        llm_error_rate_24h=error_rate,
        llm_window_label=window_label,
    )


# ----- helper: LLM-call window cascade ------------------------------------


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


# ----- task detail endpoint (Phase 2) ---------------------------------------
#
# Generic accessor for the latest task_run row of a specific task.
# Used by the dashboard to fetch the richer per-task details
# (confusion matrix, per-criterion rates, calibration bins) that
# Phase 2 tasks emit via their AggregatingEvalTask.aggregate() hook.
# Without this, ``GET /eval/summary`` only exposes the flat accuracy
# — the structured details would be unreachable.


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


# ----- diagnostics: confidence + review queue + agent reliability + failures --
#
# Everything below feeds the "what else can the eval page tell me?" cards
# the dashboard renders below the drift trend. Each section is a quick
# rollup over existing tables — no new schema. The endpoint stays a
# single read so the page can fetch it once and render four sections.


class ConfidenceBucket(BaseModel):
    """One bucket of the extraction confidence histogram.

    ``label`` is the human-readable range (e.g. ``"≥0.95"``). ``n`` is
    the total extractions in this bucket. ``accepted`` / ``overridden``
    let the UI render the calibration story — extractions reported at
    very high confidence *should* almost never be overridden; if they
    are, the confidence number isn't calibrated and the auto-accept
    threshold needs tuning.
    """

    label: str
    n: int
    accepted: int
    overridden: int


class ReviewQueueStats(BaseModel):
    """Counters for the review-queue burn-down section."""

    open: int
    resolved_7d: int
    median_open_age_hours: float | None


class AgentReliabilityRow(BaseModel):
    """One row of the agent-reliability table.

    ``ok`` / ``interrupted`` / ``failed`` come from the LangGraph
    ``status`` written to ``agent_steps`` on each node completion. A
    run is counted "ok" if every step it produced has ``status='ok'``;
    ``interrupted`` if any step is awaiting human input (the HITL
    confirmation gates); ``failed`` if any step is ``status='failed'``.
    Mutually exclusive — the worst outcome wins.
    """

    agent_name: str
    runs: int
    ok: int
    interrupted: int
    failed: int


class FailureRow(BaseModel):
    """One row of the recent-failures table.

    Two ``kind`` values:

    - ``"llm"`` — an ``llm_calls`` row with status != 'ok'. ``id`` is
      the LLMCall row id so the frontend can open the existing
      observability detail drawer.
    - ``"agent_step"`` — an ``agent_steps`` row with status='failed'.
      ``id`` is the parent ``agent_runs.id`` since the drawer
      navigates by run, not by step.
    """

    kind: str
    id: str
    at: datetime
    summary: str
    detail: str | None = None


class EvalDiagnostics(BaseModel):
    """Everything the /eval page renders below the drift trend.

    Returned in one shape so the frontend page makes one extra fetch
    (alongside summary / fields / trend) and renders four extra cards.
    Keeping the response monolithic — rather than splitting into four
    endpoints — is the right trade for a dashboard that loads them all
    at once. If any section grows beyond a quick rollup we'll factor
    it out then.
    """

    confidence_buckets: list[ConfidenceBucket]
    extractions_total: int
    review_queue: ReviewQueueStats
    agent_reliability: list[AgentReliabilityRow]
    recent_failures: list[FailureRow]


# Confidence band edges. The "auto-accept" threshold is configured at
# 0.85; the bands above/below that boundary are what the calibration
# card visualises. The lowest band (<0.50) intentionally has its own
# bucket because those rows are forced-routed to review and should
# never be auto-accepted regardless of the model's claim.
_CONF_BANDS = [
    ("≥0.95", 0.95, None),
    ("0.85–0.95", 0.85, 0.95),
    ("0.70–0.85", 0.70, 0.85),
    ("0.50–0.70", 0.50, 0.70),
    ("<0.50", None, 0.50),
]


@router.get("/diagnostics", response_model=EvalDiagnostics)
async def get_eval_diagnostics(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> EvalDiagnostics:
    """One-stop rollup of the secondary eval signals.

    Picks up where ``/eval/summary`` leaves off: instead of "is the
    extractor drifting?", these answer "is the confidence well-calibrated?",
    "is the review queue keeping up?", "are the agents themselves reliable?",
    and "what's broken right now?". All four rollups are cheap reads
    over already-indexed tables.
    """
    return EvalDiagnostics(
        confidence_buckets=await _confidence_buckets(db),
        extractions_total=await _extractions_total(db),
        review_queue=await _review_queue_stats(db),
        agent_reliability=await _agent_reliability(db),
        recent_failures=await _recent_failures(db),
    )


async def _extractions_total(db: AsyncSession) -> int:
    """Total count of resolved extractions across history.

    Used as the denominator of the calibration view + the "we have
    N total observations" hint on the empty state — so a fresh
    install can tell the difference between "no data yet" and "lots
    of data but no recent activity".
    """
    stmt = select(func.count(Extraction.id)).where(
        Extraction.status.in_(
            (ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN)
        )
    )
    return int((await db.execute(stmt)).scalar() or 0)


async def _confidence_buckets(db: AsyncSession) -> list[ConfidenceBucket]:
    """Bucket every resolved extraction by confidence band + outcome.

    One row per band. The frontend uses ``accepted / n`` per band as
    the "calibration accuracy" — the fraction of extractions the
    reviewer left alone. A well-calibrated extractor has the top
    band at ~1.0 and the bottom at much less; an over-confident
    extractor has the top band well below 1.0 (it's claiming 95%
    confidence on extractions humans correct anyway).
    """
    # SUM(CASE WHEN ...) pattern so we read each row exactly once
    # rather than running four queries per band.
    cols = []
    for label, lo, hi in _CONF_BANDS:
        conds = []
        if lo is not None:
            conds.append(Extraction.confidence >= lo)
        if hi is not None:
            conds.append(Extraction.confidence < hi)
        when = conds[0] if len(conds) == 1 else (conds[0] & conds[1])
        cols.append(
            func.sum(
                case(
                    (
                        when
                        & (Extraction.status == ExtractionStatus.ACCEPTED),
                        1,
                    ),
                    else_=0,
                )
            ).label(f"acc_{label}")
        )
        cols.append(
            func.sum(
                case(
                    (
                        when
                        & (Extraction.status == ExtractionStatus.OVERRIDDEN),
                        1,
                    ),
                    else_=0,
                )
            ).label(f"ov_{label}")
        )

    stmt = select(*cols).where(
        Extraction.status.in_(
            (ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN)
        )
    )
    row = (await db.execute(stmt)).one()

    out: list[ConfidenceBucket] = []
    for i, (label, _lo, _hi) in enumerate(_CONF_BANDS):
        accepted = int(row[i * 2] or 0)
        overridden = int(row[i * 2 + 1] or 0)
        out.append(
            ConfidenceBucket(
                label=label,
                n=accepted + overridden,
                accepted=accepted,
                overridden=overridden,
            )
        )
    return out


async def _review_queue_stats(db: AsyncSession) -> ReviewQueueStats:
    """Open / resolved / median-age stats for the human review queue.

    "Open" = ReviewTask.status='open' right now. "Resolved 7d" =
    ReviewTask.status!='open' updated in the last week. Median age is
    computed across open rows only; resolved age is the throughput
    story which we don't track per-row updated_at deltas for yet.
    """
    cutoff = datetime.now(UTC) - timedelta(days=7)
    open_count = int(
        (
            await db.execute(
                select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")
            )
        ).scalar()
        or 0
    )
    resolved_7d = int(
        (
            await db.execute(
                select(func.count(ReviewTask.id)).where(
                    ReviewTask.status != "open",
                    ReviewTask.updated_at >= cutoff,
                )
            )
        ).scalar()
        or 0
    )

    # Median open-age: pull created_at for open rows, compute in
    # Python. The queue is small enough (≤ thousands at most) that
    # this beats writing a percentile_cont() expression.
    ages_q = await db.execute(
        select(ReviewTask.created_at).where(ReviewTask.status == "open")
    )
    now = datetime.now(UTC)
    ages_hours = sorted(
        (now - ts).total_seconds() / 3600.0
        for ts in ages_q.scalars().all()
    )
    median: float | None
    if not ages_hours:
        median = None
    elif len(ages_hours) % 2 == 1:
        median = ages_hours[len(ages_hours) // 2]
    else:
        mid = len(ages_hours) // 2
        median = (ages_hours[mid - 1] + ages_hours[mid]) / 2

    return ReviewQueueStats(
        open=open_count,
        resolved_7d=resolved_7d,
        median_open_age_hours=median,
    )


async def _agent_reliability(db: AsyncSession) -> list[AgentReliabilityRow]:
    """Per-agent run reliability over the last 7 days.

    Joins agent_steps onto agent_runs, then groups in Python because
    the "worst step wins" outcome calculus is awkward in SQL. The
    population is small (dozens per day max) so the row-level fetch
    is fine.
    """
    cutoff = datetime.now(UTC) - timedelta(days=7)
    stmt = (
        select(AgentRun.id, AgentRun.agent_name, AgentStep.status)
        .join(AgentStep, AgentStep.agent_run_id == AgentRun.id, isouter=True)
        .where(AgentRun.created_at >= cutoff)
    )
    rows = (await db.execute(stmt)).all()

    # run_id -> set of step statuses
    by_run: dict[tuple[str, str], set[str]] = {}
    for run_id, agent_name, step_status in rows:
        key = (str(run_id), agent_name)
        statuses_for_run = by_run.setdefault(key, set())
        if step_status is not None:
            statuses_for_run.add(step_status)

    # agent_name -> {"ok": int, "interrupted": int, "failed": int}
    rollup: dict[str, dict[str, int]] = {}
    for (_run_id, agent_name), statuses in by_run.items():
        agent_bucket = rollup.setdefault(
            agent_name, {"runs": 0, "ok": 0, "interrupted": 0, "failed": 0}
        )
        agent_bucket["runs"] += 1
        # Worst-status wins. "failed" trumps "interrupt" trumps
        # everything-ok. A run with no steps yet (just-started) gets
        # counted as "ok" optimistically — it'll roll up correctly
        # once steps land.
        if "failed" in statuses:
            agent_bucket["failed"] += 1
        elif "interrupt" in statuses:
            agent_bucket["interrupted"] += 1
        else:
            agent_bucket["ok"] += 1

    return [
        AgentReliabilityRow(
            agent_name=name,
            runs=v["runs"],
            ok=v["ok"],
            interrupted=v["interrupted"],
            failed=v["failed"],
        )
        for name, v in sorted(rollup.items())
    ]


async def _recent_failures(
    db: AsyncSession, limit: int = 8
) -> list[FailureRow]:
    """Most recent failures across LLM calls and agent steps, merged.

    Order is plain ``created_at`` desc across both sources. Frontend
    renders each row with a click target into the existing
    observability drawer (LLMCallDrawer for ``llm`` rows,
    AgentRunDrawer for ``agent_step`` rows).
    """
    # LLM failures
    llm_q = (
        select(
            LLMCall.id,
            LLMCall.created_at,
            LLMCall.model,
            LLMCall.status,
            LLMCall.error_reason,
            LLMCall.error_detail,
        )
        .where(LLMCall.status != "ok")
        .order_by(desc(LLMCall.created_at))
        .limit(limit)
    )
    llm_rows = (await db.execute(llm_q)).all()
    out: list[FailureRow] = []
    for lr in llm_rows:
        # Surface the model name in the headline so the operator can
        # tell at a glance whether failures are concentrated on one
        # model. Schema_failed and api errors look identical otherwise.
        summary = f"{lr.model} · {lr.status}"
        if lr.error_reason:
            summary = f"{summary} — {lr.error_reason}"
        out.append(
            FailureRow(
                kind="llm",
                id=str(lr.id),
                at=lr.created_at,
                summary=summary,
                detail=lr.error_detail,
            )
        )

    # Agent-step failures — join to the parent run so we can show a
    # meaningful label ("intake → extract_documents failed") and link
    # the drawer to the right run id.
    step_q = (
        select(
            AgentStep.created_at,
            AgentStep.node,
            AgentStep.summary,
            AgentRun.id.label("run_id"),
            AgentRun.agent_name,
        )
        .join(AgentRun, AgentRun.id == AgentStep.agent_run_id)
        .where(AgentStep.status == "failed")
        .order_by(desc(AgentStep.created_at))
        .limit(limit)
    )
    step_rows = (await db.execute(step_q)).all()
    for r in step_rows:
        out.append(
            FailureRow(
                kind="agent_step",
                id=str(r.run_id),
                at=r.created_at,
                summary=f"{r.agent_name} → {r.node} failed",
                detail=r.summary,
            )
        )

    out.sort(key=lambda f: f.at, reverse=True)
    return out[:limit]


# ----- annotations endpoints ------------------------------------------------
#
# Three thin HTTP wrappers around mkopo.services.annotations. Listed
# here (instead of as their own router) because the eval page is the
# canonical home for "what humans thought of these traces" — the
# observability drawers consume the same endpoints but are not the
# semantic owner of the resource.


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
        created_by_user_id=(
            str(row.created_by_user_id) if row.created_by_user_id else None
        ),
        created_at=row.created_at,
        spawned_review_task_id=(
            str(row.spawned_review_task_id)
            if row.spawned_review_task_id
            else None
        ),
    )


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
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


def _looks_like_uuid(s: str) -> bool:
    """``user.user_id`` is a string in dev (bearer dev-user) — coerce
    safely. Same pattern as routers/staff_chat.py.
    """
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


# ----- LLM call regression-diff endpoint ----------------------------------
#
# Compares two ``llm_calls`` rows on the metadata we persist. We don't
# store user prompts or response text (PII + size concerns), so the
# diff is intentionally metadata-only: model match, latency delta,
# token delta, cost delta, status / error match, same system-prompt
# hash. That's what a regression check actually needs — "did the
# expensive model creep back in?", "did latency spike on this prompt?"
# rather than full prompt diffs.


class LLMDiffField(BaseModel):
    """One row of the side-by-side diff card.

    ``label`` is the human-readable field name. ``a`` / ``b`` are the
    formatted values for the two calls. ``delta`` is a short string
    describing the change (e.g. ``"+0.42s"``, ``"+$0.0012"``,
    ``"matches"``). ``flag`` is the visual cue:

    - ``"match"``       — values agree
    - ``"different"``   — values differ in a benign / informational way
    - ``"regression"``  — b is worse than a (slower / more expensive
                          / failed where a succeeded)
    - ``"improvement"`` — b is better than a
    """

    label: str
    a: str
    b: str
    delta: str
    flag: str


class LLMDiffResponse(BaseModel):
    a_id: str
    b_id: str
    fields: list[LLMDiffField]
    # Quick takeaway. Drives a one-liner in the UI like "B is 0.42s
    # slower and $0.0008 more expensive on the same prompt."
    summary: str


@router.get("/diff/llm-calls", response_model=LLMDiffResponse)
async def diff_llm_calls(
    a: uuid.UUID,
    b: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> LLMDiffResponse:
    """Compare two ``llm_calls`` rows on stored metadata.

    Used by the regression-diff card in ``LLMCallDrawer``. Returns
    one row per compared field plus a one-line summary; the
    frontend renders rows in a side-by-side table.
    """
    rows = (
        await db.execute(
            select(LLMCall).where(LLMCall.id.in_([a, b]))
        )
    ).scalars().all()
    by_id = {r.id: r for r in rows}
    row_a = by_id.get(a)
    row_b = by_id.get(b)
    if row_a is None or row_b is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both LLM call ids not found.",
        )

    fields: list[LLMDiffField] = []

    # Model
    fields.append(
        LLMDiffField(
            label="Model",
            a=row_a.model,
            b=row_b.model,
            delta="matches" if row_a.model == row_b.model else "different",
            flag="match" if row_a.model == row_b.model else "different",
        )
    )

    # System prompt hash — fingerprint match means we were running the
    # exact same prompt body, which is the precondition for the diff
    # being meaningful in the first place.
    same_prompt = row_a.system_prompt_hash == row_b.system_prompt_hash
    fields.append(
        LLMDiffField(
            label="System prompt",
            a=row_a.system_prompt_hash[:12],
            b=row_b.system_prompt_hash[:12],
            delta="same hash" if same_prompt else "DIFFERENT prompt",
            flag="match" if same_prompt else "regression",
        )
    )

    # Status
    same_status = row_a.status == row_b.status
    flag_status = (
        "match"
        if same_status
        else "regression" if row_b.status != "ok" and row_a.status == "ok"
        else "improvement" if row_a.status != "ok" and row_b.status == "ok"
        else "different"
    )
    fields.append(
        LLMDiffField(
            label="Status",
            a=row_a.status,
            b=row_b.status,
            delta="matches" if same_status else f"{row_a.status} → {row_b.status}",
            flag=flag_status,
        )
    )

    # Latency
    dt_lat = row_b.elapsed_seconds - row_a.elapsed_seconds
    fields.append(
        LLMDiffField(
            label="Latency",
            a=f"{row_a.elapsed_seconds:.2f}s",
            b=f"{row_b.elapsed_seconds:.2f}s",
            delta=f"{dt_lat:+.2f}s",
            flag=(
                "match" if abs(dt_lat) < 0.05
                else "regression" if dt_lat > 0
                else "improvement"
            ),
        )
    )

    # Tokens (input + output). Each direction can move independently;
    # we render the deltas verbatim. Output tokens going up usually
    # means the model wrote more, which can be regression (more cost,
    # more latency) or improvement (richer answer).
    fields.append(_token_field("Input tokens", row_a.input_tokens, row_b.input_tokens))
    fields.append(_token_field("Output tokens", row_a.output_tokens, row_b.output_tokens))

    # Cost — sum of input + output split. Some legacy rows have null
    # cost (model not in the pricing registry); render "—" in that
    # case and skip the regression flag.
    cost_a = _total_cost(row_a)
    cost_b = _total_cost(row_b)
    if cost_a is None or cost_b is None:
        fields.append(
            LLMDiffField(
                label="Cost",
                a="—" if cost_a is None else f"${cost_a:.6f}",
                b="—" if cost_b is None else f"${cost_b:.6f}",
                delta="not priced",
                flag="different",
            )
        )
    else:
        dc = cost_b - cost_a
        fields.append(
            LLMDiffField(
                label="Cost",
                a=f"${cost_a:.6f}",
                b=f"${cost_b:.6f}",
                delta=f"{'+' if dc >= 0 else ''}${dc:.6f}",
                flag=(
                    "match" if abs(dc) < 0.000005
                    else "regression" if dc > 0
                    else "improvement"
                ),
            )
        )

    # Attempt count — anything > 0 means schema retries fired. If
    # one row needed retries and the other didn't, that's a signal
    # the prompt's structured-output reliability changed.
    same_att = row_a.attempt == row_b.attempt
    fields.append(
        LLMDiffField(
            label="Attempts",
            a=str(row_a.attempt),
            b=str(row_b.attempt),
            delta="matches" if same_att else f"{row_a.attempt} → {row_b.attempt}",
            flag=(
                "match" if same_att
                else "regression" if row_b.attempt > row_a.attempt
                else "improvement"
            ),
        )
    )

    summary = _diff_summary(fields)
    return LLMDiffResponse(
        a_id=str(a), b_id=str(b), fields=fields, summary=summary
    )


def _token_field(
    label: str, a_val: int | None, b_val: int | None
) -> LLMDiffField:
    """Format a token-count delta row. Handles nulls (some legacy
    rows have ``None``) by rendering "—" without flagging."""
    if a_val is None or b_val is None:
        return LLMDiffField(
            label=label,
            a="—" if a_val is None else str(a_val),
            b="—" if b_val is None else str(b_val),
            delta="not recorded",
            flag="different",
        )
    d = b_val - a_val
    return LLMDiffField(
        label=label,
        a=str(a_val),
        b=str(b_val),
        delta=f"{d:+d}",
        flag=(
            "match" if d == 0
            else "regression" if d > 0
            else "improvement"
        ),
    )


def _total_cost(row: LLMCall) -> float | None:
    """Sum of input + output cost. Returns ``None`` if either
    side is null."""
    if row.cost_input_usd is None or row.cost_output_usd is None:
        return None
    return float(row.cost_input_usd) + float(row.cost_output_usd)


def _diff_summary(fields: list[LLMDiffField]) -> str:
    """One-line takeaway for the diff card.

    Counts regressions vs improvements vs matches and renders a
    short sentence. The frontend pairs this with the per-field
    table so the operator gets the gist + the detail.
    """
    regressions = sum(1 for f in fields if f.flag == "regression")
    improvements = sum(1 for f in fields if f.flag == "improvement")
    if regressions == 0 and improvements == 0:
        return "Calls agree on every recorded field."
    parts = []
    if regressions:
        parts.append(f"{regressions} regression{'' if regressions == 1 else 's'}")
    if improvements:
        parts.append(f"{improvements} improvement{'' if improvements == 1 else 's'}")
    return f"B vs A: {' · '.join(parts)}."


# Stats: total llm_calls so the dashboard can show a sparkline of
# call volume per hour over the last 24h. Folded into /summary at
# present; kept here as a deliberate non-export, will surface if the
# eval page grows a third tile row.
__all__ = ["router"]
