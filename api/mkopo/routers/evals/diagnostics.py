"""Secondary eval signals: confidence calibration, review-queue
burn-down, agent reliability, recent failures.

Returned as one ``EvalDiagnostics`` payload so the dashboard makes a
single extra fetch (alongside summary / fields / trend) and renders
four cards. Keeping the response monolithic — rather than splitting
into four endpoints — is the right trade for a dashboard that loads
them all at once.

Every helper here is local; the diagnostics section is the largest
single feature of the eval router and benefits from staying together
in one file rather than scattering across the package.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import (
    AgentRun,
    AgentStep,
    Extraction,
    ExtractionStatus,
    ReviewTask,
)
from mkopo.models.eval import LLMCall

router = APIRouter()


# ----- response models ----------------------------------------------------


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


# ----- endpoint -----------------------------------------------------------


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


# ----- helpers ------------------------------------------------------------


async def _extractions_total(db: AsyncSession) -> int:
    """Total count of resolved extractions across history.

    Used as the denominator of the calibration view + the "we have
    N total observations" hint on the empty state — so a fresh
    install can tell the difference between "no data yet" and "lots
    of data but no recent activity".
    """
    stmt = select(func.count(Extraction.id)).where(
        Extraction.status.in_((ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN))
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
                        when & (Extraction.status == ExtractionStatus.ACCEPTED),
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
                        when & (Extraction.status == ExtractionStatus.OVERRIDDEN),
                        1,
                    ),
                    else_=0,
                )
            ).label(f"ov_{label}")
        )

    stmt = select(*cols).where(
        Extraction.status.in_((ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN))
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
            await db.execute(select(func.count(ReviewTask.id)).where(ReviewTask.status == "open"))
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
    ages_q = await db.execute(select(ReviewTask.created_at).where(ReviewTask.status == "open"))
    now = datetime.now(UTC)
    ages_hours = sorted((now - ts).total_seconds() / 3600.0 for ts in ages_q.scalars().all())
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


async def _recent_failures(db: AsyncSession, limit: int = 8) -> list[FailureRow]:
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
