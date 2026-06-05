"""Prompt-corpus semantic-drift monitor — MMD on message embeddings.

PSI catches *feature-distribution* drift (loan amounts, classes,
types). It can't catch *semantic* drift in the user-text corpus —
borrowers suddenly asking about something the prompt wasn't tuned
for. This monitor closes that gap.

Method
------

Maximum Mean Discrepancy (Gretton et al. 2012) is the canonical
two-sample test for distribution-equality in a reproducing kernel
Hilbert space. Given two finite samples X (current) and Y
(reference), the unbiased U-statistic estimator is:

  MMD²_u = 1/(n(n-1)) Σ_{i≠j} k(x_i, x_j)
         + 1/(m(m-1)) Σ_{i≠j} k(y_i, y_j)
         − 2/(nm) Σ_{i,j} k(x_i, y_j)

with an RBF kernel k(x,y) = exp(-‖x-y‖² / (2σ²)) and σ chosen by
the **median heuristic** — median of pairwise distances in the
combined sample. The median heuristic is the standard
hyperparameter-free choice for high-dimensional embeddings; see
Garreau et al. 2017 for why it's a reasonable default.

The threshold
-------------

MMD²_u is on an arbitrary scale (depends on σ and kernel choice).
We don't run a permutation test on every monitor cycle — that
would be N² × n_permutations. Instead we publish the raw value
plus a heuristic band:

  MMD² < 0.005   → ``stable``   (no semantic shift detected)
  0.005 ≤ < 0.02 → ``minor``    (worth a look)
  MMD² ≥ 0.02   → ``major``    (corpus has materially shifted)

These bands are calibrated on the OpenAI ``text-embedding-3-small``
output (Matryoshka-truncated to 1024d) for English text — the
specific embedder our ``EmbeddingService`` uses. A different
embedder needs re-calibration.

Cost / runtime
--------------

Embeddings are cached via ``services/embeddings.py`` so each unique
message body embeds once. The MMD² compute itself is O((n+m)²) in
the sample count — fine up to ~2000 messages per window. Above
that we'd sub-sample.

Skips the row-write when either window has fewer than 20 messages
— mirrors the floor in ``services/psi.py`` for the same statistical
reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import Message, MessageDirection
from mkopo.models.eval import TaskRun
from mkopo.services.embeddings import get_embedding_service

logger = structlog.get_logger()


_CURRENT_DAYS = 7
_REFERENCE_DAYS = 30
_REFERENCE_GAP = 7
_MIN_SAMPLES = 20


@dataclass
class PromptDriftResult:
    """Wire shape for the dashboard."""

    window_current_days: int
    window_reference_days: int
    n_current: int
    n_reference: int
    mmd2: float | None
    sigma: float | None
    flag: str  # "stable" | "minor" | "major" | "insufficient_data"


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _sq_dist(a: list[float], b: list[float]) -> float:
    """Squared L2 distance. Embeddings are not necessarily unit-
    normalised post-Matryoshka truncation, so we compute the full
    distance rather than rely on ``2 − 2cos``.
    """
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def _median(values: list[float]) -> float:
    """Median; pure-Python. Pre-sorted iteration would be faster
    but ``sorted(values)[mid]`` is clear enough at this scale."""
    sv = sorted(values)
    n = len(sv)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return sv[n // 2]
    return (sv[n // 2 - 1] + sv[n // 2]) / 2.0


def _median_heuristic_sigma(combined: list[list[float]]) -> float:
    """σ via the median heuristic on the combined sample's pairwise
    distances. For embeddings in [-1, 1]^d the median squared
    distance is typically O(d) — we take √median and call it σ."""
    n = len(combined)
    # Cap the pairwise computation at 200 vectors to stay fast on
    # large windows. The median is stable under sub-sampling.
    cap = 200
    sample = combined[:cap] if n > cap else combined
    dists: list[float] = []
    for i in range(len(sample)):
        for j in range(i + 1, len(sample)):
            dists.append(_sq_dist(sample[i], sample[j]))
    if not dists:
        return 1.0
    med = _median(dists)
    # Guard against degenerate identical-text corpora.
    return math.sqrt(med) if med > 1e-9 else 1.0


def _rbf(a: list[float], b: list[float], sigma_sq: float) -> float:
    return math.exp(-_sq_dist(a, b) / (2.0 * sigma_sq))


def compute_mmd2(current: list[list[float]], reference: list[list[float]]) -> tuple[float, float]:
    """Unbiased MMD² estimator + median-heuristic σ.

    Returns ``(mmd2, sigma)``. The caller bands ``mmd2`` against
    the dashboard thresholds.
    """
    n = len(current)
    m = len(reference)
    if n < 2 or m < 2:
        return 0.0, 1.0
    sigma = _median_heuristic_sigma(current + reference)
    sigma_sq = sigma * sigma
    # Within-current cross-pairs (i ≠ j).
    sum_cc = 0.0
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            sum_cc += _rbf(current[i], current[j], sigma_sq)
    # Within-reference cross-pairs.
    sum_rr = 0.0
    for i in range(m):
        for j in range(m):
            if i == j:
                continue
            sum_rr += _rbf(reference[i], reference[j], sigma_sq)
    # Cross.
    sum_cr = 0.0
    for x in current:
        for y in reference:
            sum_cr += _rbf(x, y, sigma_sq)
    mmd2 = sum_cc / (n * (n - 1)) + sum_rr / (m * (m - 1)) - 2.0 * sum_cr / (n * m)
    return mmd2, sigma


def _band(mmd2: float | None) -> str:
    if mmd2 is None:
        return "insufficient_data"
    if mmd2 < 0.005:
        return "stable"
    if mmd2 < 0.02:
        return "minor"
    return "major"


async def _load_message_bodies(session: AsyncSession, start: datetime, end: datetime) -> list[str]:
    """Pull inbound borrower messages in the window. We filter to
    inbound only — outbound is system-generated and would dominate
    any drift signal with our own prompt-template phrasing."""
    stmt = select(Message.body).where(
        and_(
            Message.created_at >= start,
            Message.created_at < end,
            Message.direction == MessageDirection.INBOUND,
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [b for b in rows if b and b.strip()]


async def compute_prompt_drift(
    session: AsyncSession,
) -> PromptDriftResult:
    """Compute MMD² of inbound borrower messages, current week vs
    reference. Returns ``insufficient_data`` when either window has
    too few messages.
    """
    now = datetime.now(UTC)
    cur_end = now
    cur_start = now - timedelta(days=_CURRENT_DAYS)
    ref_end = now - timedelta(days=_REFERENCE_GAP)
    ref_start = ref_end - timedelta(days=_REFERENCE_DAYS)

    cur_msgs = await _load_message_bodies(session, cur_start, cur_end)
    ref_msgs = await _load_message_bodies(session, ref_start, ref_end)

    if len(cur_msgs) < _MIN_SAMPLES or len(ref_msgs) < _MIN_SAMPLES:
        logger.info(
            "prompt_drift_skipped_insufficient_samples",
            n_current=len(cur_msgs),
            n_reference=len(ref_msgs),
            floor=_MIN_SAMPLES,
        )
        return PromptDriftResult(
            window_current_days=_CURRENT_DAYS,
            window_reference_days=_REFERENCE_DAYS,
            n_current=len(cur_msgs),
            n_reference=len(ref_msgs),
            mmd2=None,
            sigma=None,
            flag="insufficient_data",
        )

    svc = get_embedding_service()
    cur_emb = await svc.embed_batch(cur_msgs, session=session)
    ref_emb = await svc.embed_batch(ref_msgs, session=session)
    mmd2, sigma = compute_mmd2(cur_emb, ref_emb)
    return PromptDriftResult(
        window_current_days=_CURRENT_DAYS,
        window_reference_days=_REFERENCE_DAYS,
        n_current=len(cur_msgs),
        n_reference=len(ref_msgs),
        mmd2=mmd2,
        sigma=sigma,
        flag=_band(mmd2),
    )


async def run_prompt_drift_monitor(
    session: AsyncSession,
) -> PromptDriftResult:
    """Compute MMD² + persist a ``task_runs`` row so the dashboard
    trend chart can plot the drift over time. Skips the write on
    ``insufficient_data`` — same pattern as the other production
    monitors.
    """
    result = await compute_prompt_drift(session)
    if result.flag == "insufficient_data" or result.mmd2 is None:
        return result
    row = TaskRun(
        task_name="prompt_drift.borrower_inbound",
        source="production",
        n=result.n_current,
        # ``accuracy`` repurposed as MMD² so the trend chart can
        # plot it. Lower is better; the dashboard card decodes.
        accuracy=result.mmd2,
        avg_score=result.mmd2,
        details={
            "mmd2": result.mmd2,
            "sigma": result.sigma,
            "n_current": result.n_current,
            "n_reference": result.n_reference,
            "window_current_days": result.window_current_days,
            "window_reference_days": result.window_reference_days,
            "flag": result.flag,
            "thresholds": {"minor": 0.005, "major": 0.02},
        },
    )
    session.add(row)
    await session.flush()
    logger.info(
        "prompt_drift_monitor_ran",
        mmd2=result.mmd2,
        sigma=result.sigma,
        flag=result.flag,
    )
    return result
