"""Refusal / abstain rate trend — early-warning detector for prompt drift.

A sudden change in how often the input-layer detector + judges
block traffic is a leading indicator of:

  - A new attack family the catalog hasn't memorised yet
    (sudden spike in blocks because Haiku catches what regex
    doesn't).
  - Prompt drift away from the production distribution — borrowers
    are suddenly hitting weird edge cases the prompt wasn't tuned
    for (the LLM-judge refuses-by-default rate moves up).
  - Detector regression — a change in the regex catalog or the
    Haiku tightening prompt that suddenly blocks legitimate
    traffic (block rate moves up but pattern coverage stays flat).

The metric is a single number — fraction of all scans in the last
7 days that ended in ``decision='blocked'`` — compared against a
28-day baseline ending 7 days ago. A 2σ deviation from the
baseline (binomial-proportion approximation) flips the flag from
``stable`` to ``spike``.

This isn't a CI gate — refusals aren't bad per se (we *want* to
block real attacks). It's an operator-attention metric: the
dashboard shows the trend and the operator decides whether to
investigate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import InjectionDecision, InjectionDetection
from mkopo.models.eval import TaskRun

logger = structlog.get_logger()


# Window cuts. Same shape as PSI — recent vs baseline.
_CURRENT_DAYS = 7
_BASELINE_DAYS = 28  # 4 weeks before the current window starts
_BASELINE_GAP = 7  # current and baseline don't overlap

# Minimum samples per window before the rate is statistically
# meaningful. Below this, the dashboard shows the count but no
# spike flag.
_MIN_CURRENT = 20
_MIN_BASELINE = 30

# Spike threshold: how many standard deviations above the baseline
# rate count as a flag. 2σ ≈ 95% confidence; conservative because
# the operator-attention cost of a false alarm is small but
# missing a real attack is large.
_SPIKE_SIGMA = 2.0


@dataclass
class RefusalResult:
    """Wire shape — what lands in ``task_runs.details`` and the API
    response."""

    window_current_days: int
    window_baseline_days: int
    n_current: int
    n_current_blocked: int
    current_rate: float
    n_baseline: int
    n_baseline_blocked: int
    baseline_rate: float
    z_score: float | None  # None when sample sizes are too small
    flag: str  # "stable" | "spike" | "insufficient_data"


def _band(z: float | None) -> str:
    if z is None:
        return "insufficient_data"
    if abs(z) >= _SPIKE_SIGMA:
        return "spike"
    return "stable"


async def _count_window(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> tuple[int, int]:
    """Count total + blocked InjectionDetection rows in [start, end)."""
    total = (
        await session.execute(
            select(func.count())
            .select_from(InjectionDetection)
            .where(
                InjectionDetection.created_at >= start,
                InjectionDetection.created_at < end,
            )
        )
    ).scalar_one()
    blocked = (
        await session.execute(
            select(func.count())
            .select_from(InjectionDetection)
            .where(
                InjectionDetection.created_at >= start,
                InjectionDetection.created_at < end,
                InjectionDetection.decision == InjectionDecision.BLOCKED.value,
            )
        )
    ).scalar_one()
    return int(total), int(blocked)


def compute_z_score(
    cur_rate: float,
    base_rate: float,
    n_cur: int,
    n_base: int,
    *,
    min_current: int = _MIN_CURRENT,
    min_baseline: int = _MIN_BASELINE,
) -> float | None:
    """Pure binomial-proportion z-test. Extracted from
    ``compute_refusal_rate`` so it's unit-testable in isolation.

        z = (p_cur - p_base) / sqrt(p_base * (1 - p_base) / n_cur)

    Returns ``None`` when the test isn't meaningful: not enough
    samples on either side, or a baseline rate of 0 or 1 (zero
    variance → the test statistic is undefined). The dashboard
    surfaces "insufficient data" rather than a noisy z in those
    cases.

    Keyword-only floors so a caller can pass different thresholds
    in a test without confusion — the production thresholds are
    the module constants.
    """
    if n_cur < min_current or n_base < min_baseline or base_rate == 0.0 or base_rate == 1.0:
        return None
    std_err = math.sqrt(base_rate * (1.0 - base_rate) / n_cur)
    if std_err <= 0:
        return None
    return (cur_rate - base_rate) / std_err


async def compute_refusal_rate(session: AsyncSession) -> RefusalResult:
    """Compute the refusal rate + a z-score against baseline.

    Uses ``compute_z_score`` for the math so the formula is
    pinned by ``tests/test_refusal_math.py``. This async wrapper
    handles only the DB queries and the wire shape.
    """
    now = datetime.now(UTC)
    cur_end = now
    cur_start = now - timedelta(days=_CURRENT_DAYS)
    base_end = now - timedelta(days=_BASELINE_GAP)
    base_start = base_end - timedelta(days=_BASELINE_DAYS)

    n_cur, blk_cur = await _count_window(session, cur_start, cur_end)
    n_base, blk_base = await _count_window(session, base_start, base_end)

    cur_rate = blk_cur / n_cur if n_cur > 0 else 0.0
    base_rate = blk_base / n_base if n_base > 0 else 0.0

    z = compute_z_score(cur_rate, base_rate, n_cur, n_base)

    return RefusalResult(
        window_current_days=_CURRENT_DAYS,
        window_baseline_days=_BASELINE_DAYS,
        n_current=n_cur,
        n_current_blocked=blk_cur,
        current_rate=cur_rate,
        n_baseline=n_base,
        n_baseline_blocked=blk_base,
        baseline_rate=base_rate,
        z_score=z,
        flag=_band(z),
    )


async def run_refusal_monitor(session: AsyncSession) -> RefusalResult:
    """Compute refusal stats + persist a ``task_runs`` row so the
    /eval dashboard trend chart can plot block-rate over time.

    Skips the write when there isn't enough data to compute a
    z-score — same pattern as the other production monitors.
    """
    result = await compute_refusal_rate(session)
    if result.flag == "insufficient_data":
        logger.info(
            "refusal_monitor_skipped",
            n_current=result.n_current,
            n_baseline=result.n_baseline,
        )
        return result

    row = TaskRun(
        task_name="refusal.block_rate",
        source="production",
        n=result.n_current,
        # ``accuracy`` is the current rate so the trend chart can
        # plot it alongside other production-source rows. The
        # dashboard's refusal-card decodes the inverted
        # interpretation (this isn't a quality metric — it's a
        # canary).
        accuracy=result.current_rate,
        avg_score=result.current_rate,
        details={
            "current_rate": result.current_rate,
            "baseline_rate": result.baseline_rate,
            "n_current": result.n_current,
            "n_current_blocked": result.n_current_blocked,
            "n_baseline": result.n_baseline,
            "n_baseline_blocked": result.n_baseline_blocked,
            "z_score": result.z_score,
            "flag": result.flag,
            "window_current_days": result.window_current_days,
            "window_baseline_days": result.window_baseline_days,
            "spike_threshold_sigma": _SPIKE_SIGMA,
        },
    )
    session.add(row)
    await session.flush()
    logger.info(
        "refusal_monitor_ran",
        current_rate=result.current_rate,
        baseline_rate=result.baseline_rate,
        z=result.z_score,
        flag=result.flag,
    )
    return result
