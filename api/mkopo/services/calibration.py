"""Confidence calibration metrics — ECE + Brier score + reliability bins.

Computes how well-calibrated the extractor's confidence values are
against the ground truth from the review queue. Industry-canonical
metrics:

- **Expected Calibration Error (ECE)** — Guo et al. ICML 2017
  (https://arxiv.org/abs/1706.04599). Bucket extractions into K
  confidence bins; ECE is the weighted gap between mean predicted
  confidence and empirical accuracy per bin.

- **Brier score** — strictly proper scoring rule; mean squared error
  between predicted probability and the {0, 1} outcome. Decomposes
  into reliability + resolution + uncertainty.

- **Reliability diagram bins** — the visual companion to ECE.
  Per-bin (mean confidence, empirical accuracy, count) tuples
  ready for the frontend to render as a binned bar chart.

Ground truth for an extraction: ACCEPTED → correct; OVERRIDDEN →
incorrect. (The drift_monitor uses the same convention — see
``services/drift.py``.)

Tradeoffs documented in
[``docs/EVAL_PLAN.md``](EVAL_PLAN.md) — ECE is criticized for bin
sensitivity, so we report Brier alongside as a tie-breaker. Both
land on the dashboard, no single number forced into being canonical.

Why a service module instead of an eval runner task: the data
source is live production extractions, not a YAML golden set. Runs
on a Redis-backed cron at the same cadence as the drift monitor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import Extraction, ExtractionStatus
from mkopo.models.eval import TaskRun

logger = structlog.get_logger()


# Number of equal-width confidence bins. 10 is the convention in
# Guo et al. 2017 and most calibration literature.
_N_BINS = 10
# Days of extraction history to score against. 30 matches the
# drift_monitor default — same time slice so the dashboard's two
# trend lines reference the same population.
_WINDOW_DAYS = 30


@dataclass
class CalibrationBin:
    """One equal-width bin in the reliability diagram."""

    lower: float
    upper: float
    n: int
    mean_confidence: float
    empirical_accuracy: float


@dataclass
class CalibrationResult:
    """Wire shape returned to the dashboard."""

    n: int
    ece: float
    brier: float
    bins: list[CalibrationBin]
    window_days: int


async def _load_resolved_extractions(
    session: AsyncSession, window_days: int
) -> list[Extraction]:
    """Pull every resolved extraction within the window. Same
    boundary the drift_monitor uses so the two metrics derive from
    identical populations."""
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    stmt = select(Extraction).where(
        Extraction.status.in_(
            (ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN)
        ),
        Extraction.created_at >= cutoff,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


def _binarise(ext: Extraction) -> int:
    """ACCEPTED = 1 (model was correct). OVERRIDDEN = 0 (model was
    wrong, human had to fix). This is the same ground-truth signal
    drift_monitor consumes — keeps the two metrics consistent."""
    return 1 if ext.status == ExtractionStatus.ACCEPTED else 0


def compute_ece(
    confidences: list[float],
    correct: list[int],
    n_bins: int = _N_BINS,
) -> tuple[float, list[CalibrationBin]]:
    """Expected Calibration Error + reliability-diagram bins.

    Formula (Guo et al. 2017):

        ECE = Σ_m  |B_m| / n  *  |acc(B_m) - conf(B_m)|

    where B_m is the mth bin, acc is empirical accuracy on the bin,
    conf is mean predicted confidence on the bin.

    Bins of width 1/n_bins between 0 and 1. Bin boundaries are
    half-open on the right side except for the final bin, which
    includes 1.0. This keeps confidences of exactly 1.0 from
    falling through (a classic off-by-one).
    """
    if not confidences:
        return 0.0, []
    n = len(confidences)
    bins: list[CalibrationBin] = []
    width = 1.0 / n_bins
    weighted_gap_sum = 0.0
    for i in range(n_bins):
        lower = i * width
        upper = (i + 1) * width
        in_bin = [
            (c, y)
            for c, y in zip(confidences, correct, strict=True)
            if (lower <= c < upper) or (i == n_bins - 1 and c == 1.0)
        ]
        if not in_bin:
            bins.append(
                CalibrationBin(
                    lower=lower,
                    upper=upper,
                    n=0,
                    mean_confidence=0.0,
                    empirical_accuracy=0.0,
                )
            )
            continue
        bin_conf = sum(c for c, _ in in_bin) / len(in_bin)
        bin_acc = sum(y for _, y in in_bin) / len(in_bin)
        weighted_gap_sum += (len(in_bin) / n) * abs(bin_conf - bin_acc)
        bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                n=len(in_bin),
                mean_confidence=bin_conf,
                empirical_accuracy=bin_acc,
            )
        )
    return weighted_gap_sum, bins


def compute_brier(confidences: list[float], correct: list[int]) -> float:
    """Brier score = mean squared error between predicted probability
    and the {0, 1} outcome. Strictly proper scoring rule, in [0, 1]
    where 0 = perfect calibration AND perfect resolution.

    Brier penalises a confident-and-wrong model more than ECE because
    of the squared term — useful as a tie-breaker when two models
    have similar ECE but different sharpness."""
    if not confidences:
        return 0.0
    return sum(
        (c - y) ** 2
        for c, y in zip(confidences, correct, strict=True)
    ) / len(confidences)


async def compute_calibration(
    session: AsyncSession,
    window_days: int = _WINDOW_DAYS,
) -> CalibrationResult:
    """Compute the full calibration report for the dashboard.

    Skips extractions with no confidence (legacy rows). Returns
    ``n=0`` and zero metrics if the window is empty — the dashboard
    renders that as "no data yet" rather than a misleading 0% ECE.
    """
    rows = await _load_resolved_extractions(session, window_days)
    pairs = [
        (ext.confidence, _binarise(ext))
        for ext in rows
        if ext.confidence is not None
    ]
    if not pairs:
        return CalibrationResult(
            n=0, ece=0.0, brier=0.0, bins=[], window_days=window_days
        )
    confidences = [p[0] for p in pairs]
    correct = [p[1] for p in pairs]
    ece, bins = compute_ece(confidences, correct)
    brier = compute_brier(confidences, correct)
    return CalibrationResult(
        n=len(pairs),
        ece=ece,
        brier=brier,
        bins=bins,
        window_days=window_days,
    )


async def run_calibration_monitor(
    session: AsyncSession,
) -> CalibrationResult:
    """Compute calibration and persist a ``task_runs`` row so the
    /eval dashboard can show the metric on the trend chart alongside
    accuracy. Mirrors the writer pattern in
    ``services/drift.py:run_drift_monitor``.

    Idempotent enough — re-running on the same day creates new rows
    with later timestamps. The dashboard treats the latest row per
    (task_name, source) as authoritative.
    """
    result = await compute_calibration(session)
    if result.n == 0:
        logger.info("calibration_skipped_empty_window")
        return result

    row = TaskRun(
        task_name="calibration.extractor_confidence",
        source="production",
        n=result.n,
        # Use ECE itself as the headline "accuracy" — lower is
        # better. The dashboard's existing accuracy widget shows it
        # as-is; the per-task detail card surfaces ECE + Brier +
        # bins separately.
        accuracy=1.0 - result.ece,
        avg_score=1.0 - result.brier,
        details={
            "ece": result.ece,
            "brier": result.brier,
            "window_days": result.window_days,
            "bins": [
                {
                    "lower": b.lower,
                    "upper": b.upper,
                    "n": b.n,
                    "mean_confidence": b.mean_confidence,
                    "empirical_accuracy": b.empirical_accuracy,
                }
                for b in result.bins
            ],
        },
    )
    session.add(row)
    await session.flush()
    logger.info(
        "calibration_monitor_ran",
        n=result.n,
        ece=result.ece,
        brier=result.brier,
    )
    return result
