"""Population Stability Index — input-feature drift detector.

Background
----------

PSI is the standard credit-risk model-monitoring metric for input
drift. Originally from credit-scorecard practice (early 2000s,
Sidney Siddiqi's *Credit Risk Scorecards*) — it's the same form
as KL divergence with a symmetrised summation:

    PSI = Σ (p_current[i] - p_reference[i]) × ln(p_current[i] / p_reference[i])

over a partition of the feature into bins. Bands (Siddiqi 2017,
also adopted by the FDIC + OCC in supervisory guidance on model
risk):

    PSI < 0.10   — stable
    0.10 ≤ < 0.25 — minor population shift (investigate)
    PSI ≥ 0.25   — major shift (recalibrate / re-train / pause)

Why PSI matters
---------------

A drift in input distribution silently breaks model assumptions
well before accuracy regresses. By the time the eval gate fires,
you've already shipped weeks of mis-priced decisions. PSI is the
*leading* indicator — it catches the shift on the input side
before it propagates to the output.

This module computes PSI on three features (v1):

    - ``loan_amount`` (numeric)  — quantile-binned against the
      reference window.
    - ``loan_class``  (categorical) — distribution shift between
      personal vs commercial mix.
    - ``loan_type``   (categorical) — bridge / permanent / refi /
      construction mix.

Future expansion: DSCR, LTV, FICO are derived/extracted features
and live on different tables; adding them is just more queries
into the same PSI primitive.

Reference window: days 30–120 from now. Current window: last 30
days. These are the conventional cuts; FDIC's MRM guidance leaves
the exact horizon to the institution but mandates a comparison
that isolates *recent* traffic against a stable baseline.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import Loan
from mkopo.models.eval import TaskRun

logger = structlog.get_logger()


# Window cuts (in days). Reference is the prior 90-day window
# ending 30 days ago; current is the last 30 days. The 30-day gap
# is deliberate: it excludes recent-population-being-monitored
# from the reference so the comparison doesn't trivially overlap.
_CURRENT_DAYS = 30
_REFERENCE_LOOKBACK = 120  # how far back the reference starts
_REFERENCE_GAP = 30  # how recent the reference ends

# Standard PSI bin count. 10 is the credit-risk convention; we
# follow Siddiqi 2017. Numeric features get quantile-binned against
# the reference distribution so empty bins on the current side
# don't trivially blow up the divergence (we use Laplace smoothing
# on zero counts — see ``_safe_psi_term``).
_N_BINS = 10

# Minimum sample sizes per window. Below these we return
# "insufficient_data" rather than a numerically suspicious PSI.
# FDIC supervisory guidance is silent on exact minimums; 30 is a
# common floor for the central-limit theorem to bite.
_MIN_REF_N = 30
_MIN_CUR_N = 30


@dataclass
class BinStats:
    """One bin of the PSI partition.

    For numeric features, ``label`` is the bucket range (e.g.
    ``"[750000, 1200000)"``). For categorical features, ``label``
    is the category itself."""

    label: str
    reference_pct: float
    current_pct: float
    psi_contribution: float


@dataclass
class FeatureResult:
    """Per-feature PSI result. Slotted into ``PSIResult.features``."""

    feature: str
    feature_kind: str  # "numeric" | "categorical"
    psi: float
    flag: str  # "stable" | "minor" | "major"
    n_reference: int
    n_current: int
    bins: list[BinStats]


@dataclass
class PSIResult:
    """Wire shape returned to the dashboard."""

    window_current_days: int
    window_reference_days: int
    features: list[FeatureResult]


def _band(psi: float) -> str:
    """Map a PSI value to the FDIC / Siddiqi 2017 band labels."""
    if psi < 0.10:
        return "stable"
    if psi < 0.25:
        return "minor"
    return "major"


def _safe_psi_term(p_cur: float, p_ref: float) -> float:
    """One term of the PSI sum, with Laplace-smoothed zeros.

    Standard PSI implementations skip bins where either side is
    zero — but that silently underestimates drift when a brand new
    bin appears (or an old one empties). We add ``1e-6`` to each
    side as Laplace smoothing so the log is always defined.
    """
    p_cur = p_cur if p_cur > 0 else 1e-6
    p_ref = p_ref if p_ref > 0 else 1e-6
    return (p_cur - p_ref) * math.log(p_cur / p_ref)


def compute_psi_numeric(
    reference: list[float], current: list[float], n_bins: int = _N_BINS
) -> tuple[float, list[BinStats]]:
    """Quantile-binned PSI on a numeric feature.

    Bins are determined from the *reference* distribution — that's
    the standard, because the reference is the model's training-time
    population. The current distribution is then assigned to those
    bins; if it concentrates outside the reference's quantile
    ranges, PSI rises.
    """
    if not reference or not current:
        return 0.0, []
    n_ref = len(reference)
    n_cur = len(current)
    sorted_ref = sorted(reference)

    # Quantile cut points from the reference. We compute them as
    # 0.1, 0.2, ..., 0.9 quantiles; that gives us 10 bins.
    # Equal-width or equal-count is the standard; equal-count
    # (quantile) handles heavy-tailed distributions better, which
    # loan amounts are.
    cuts: list[float] = []
    for i in range(1, n_bins):
        idx = int(round(i * n_ref / n_bins)) - 1
        cuts.append(sorted_ref[max(0, min(idx, n_ref - 1))])
    # Deduplicate cuts so two identical quantile boundaries don't
    # produce a degenerate empty bin.
    unique_cuts = sorted(set(cuts))

    def bin_index(v: float) -> int:
        for i, c in enumerate(unique_cuts):
            if v < c:
                return i
        return len(unique_cuts)  # final bin = above last cut

    # Build bin counts.
    ref_counts = Counter(bin_index(v) for v in reference)
    cur_counts = Counter(bin_index(v) for v in current)

    # Bin labels: "[lo, hi)" for interior bins; "[-, c0)" for left
    # tail; "[cN, +)" for right tail.
    def label_for(i: int) -> str:
        if not unique_cuts:
            return "(all)"
        if i == 0:
            return f"<{_fmt_num(unique_cuts[0])}"
        if i == len(unique_cuts):
            return f"≥{_fmt_num(unique_cuts[-1])}"
        return f"[{_fmt_num(unique_cuts[i - 1])}, {_fmt_num(unique_cuts[i])})"

    bins: list[BinStats] = []
    total_psi = 0.0
    for i in range(len(unique_cuts) + 1):
        p_ref = ref_counts.get(i, 0) / n_ref
        p_cur = cur_counts.get(i, 0) / n_cur
        term = _safe_psi_term(p_cur, p_ref)
        total_psi += term
        bins.append(
            BinStats(
                label=label_for(i),
                reference_pct=p_ref,
                current_pct=p_cur,
                psi_contribution=term,
            )
        )
    return total_psi, bins


def compute_psi_categorical(
    reference: list[str], current: list[str]
) -> tuple[float, list[BinStats]]:
    """Per-category PSI on a categorical feature.

    Bins are the union of categories observed in either window;
    each category is one bin (no quantile binning). Zero-count
    smoothing same as the numeric path.
    """
    if not reference or not current:
        return 0.0, []
    n_ref = len(reference)
    n_cur = len(current)
    ref_counts = Counter(reference)
    cur_counts = Counter(current)
    categories = sorted(set(ref_counts) | set(cur_counts))

    bins: list[BinStats] = []
    total_psi = 0.0
    for cat in categories:
        p_ref = ref_counts.get(cat, 0) / n_ref
        p_cur = cur_counts.get(cat, 0) / n_cur
        term = _safe_psi_term(p_cur, p_ref)
        total_psi += term
        bins.append(
            BinStats(
                label=str(cat),
                reference_pct=p_ref,
                current_pct=p_cur,
                psi_contribution=term,
            )
        )
    return total_psi, bins


def _fmt_num(v: float) -> str:
    """Compact numeric labels — '1.5M' beats '1500000.0' on a
    densely-tiled bin axis."""
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.0f}k"
    return f"{v:.0f}"


async def _load_loans_in_window(
    session: AsyncSession, start: datetime, end: datetime
) -> list[Loan]:
    """Pull every loan whose ``created_at`` falls in [start, end).
    Status / stage doesn't matter for input drift — we're scoring
    what arrived, not what got decisioned."""
    stmt = (
        select(Loan)
        .where(Loan.created_at >= start, Loan.created_at < end)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def compute_psi(session: AsyncSession) -> PSIResult:
    """Compute per-feature PSI for the configured windows.

    Returns a result with empty ``features`` (or specific features
    flagged ``insufficient_data`` via psi=NaN) when sample sizes
    are below the floor.
    """
    now = datetime.now(UTC)
    cur_end = now
    cur_start = now - timedelta(days=_CURRENT_DAYS)
    ref_end = now - timedelta(days=_REFERENCE_GAP)
    ref_start = now - timedelta(days=_REFERENCE_LOOKBACK)

    current = await _load_loans_in_window(session, cur_start, cur_end)
    reference = await _load_loans_in_window(session, ref_start, ref_end)

    features: list[FeatureResult] = []

    if len(current) < _MIN_CUR_N or len(reference) < _MIN_REF_N:
        # Not enough samples — surface as an empty result so the
        # dashboard renders "no data" instead of a misleading PSI.
        # We still log the populations so the operator can tell
        # whether it's the reference or the current that's empty.
        logger.info(
            "psi_skipped_insufficient_samples",
            n_reference=len(reference),
            n_current=len(current),
            ref_floor=_MIN_REF_N,
            cur_floor=_MIN_CUR_N,
        )
        return PSIResult(
            window_current_days=_CURRENT_DAYS,
            window_reference_days=_REFERENCE_LOOKBACK - _REFERENCE_GAP,
            features=[],
        )

    # Numeric: loan amount. Convert Decimal → float for math.
    ref_amounts = [float(loan.amount) for loan in reference]
    cur_amounts = [float(loan.amount) for loan in current]
    psi_amt, bins_amt = compute_psi_numeric(ref_amounts, cur_amounts)
    features.append(
        FeatureResult(
            feature="loan_amount",
            feature_kind="numeric",
            psi=psi_amt,
            flag=_band(psi_amt),
            n_reference=len(ref_amounts),
            n_current=len(cur_amounts),
            bins=bins_amt,
        )
    )

    # Categorical: loan_class (personal vs business).
    ref_class = [loan.loan_class.value for loan in reference]
    cur_class = [loan.loan_class.value for loan in current]
    psi_cls, bins_cls = compute_psi_categorical(ref_class, cur_class)
    features.append(
        FeatureResult(
            feature="loan_class",
            feature_kind="categorical",
            psi=psi_cls,
            flag=_band(psi_cls),
            n_reference=len(ref_class),
            n_current=len(cur_class),
            bins=bins_cls,
        )
    )

    # Categorical: loan_type (bridge / permanent / refi / construction).
    ref_type = [loan.loan_type.value for loan in reference]
    cur_type = [loan.loan_type.value for loan in current]
    psi_typ, bins_typ = compute_psi_categorical(ref_type, cur_type)
    features.append(
        FeatureResult(
            feature="loan_type",
            feature_kind="categorical",
            psi=psi_typ,
            flag=_band(psi_typ),
            n_reference=len(ref_type),
            n_current=len(cur_type),
            bins=bins_typ,
        )
    )

    return PSIResult(
        window_current_days=_CURRENT_DAYS,
        window_reference_days=_REFERENCE_LOOKBACK - _REFERENCE_GAP,
        features=features,
    )


async def run_psi_monitor(session: AsyncSession) -> PSIResult:
    """Compute PSI per feature + persist one ``task_runs`` row per
    feature so the /eval dashboard trend chart can plot each one
    independently.

    Same writer pattern as ``services/fairness.py``: skip the
    write when the windows are too thin to compute a meaningful
    PSI.
    """
    result = await compute_psi(session)
    if not result.features:
        logger.info("psi_monitor_skipped_no_features")
        return result

    for f in result.features:
        row = TaskRun(
            task_name=f"psi.{f.feature}",
            source="production",
            n=f.n_current,
            # We repurpose ``accuracy`` so the existing trend chart
            # can plot PSI alongside accuracy values. The dashboard
            # card decodes the inverted polarity (lower is better
            # for PSI; higher is better for accuracy) via the band
            # field in ``details``.
            accuracy=f.psi,
            avg_score=f.psi,
            details={
                "feature": f.feature,
                "feature_kind": f.feature_kind,
                "psi": f.psi,
                "flag": f.flag,
                "n_reference": f.n_reference,
                "n_current": f.n_current,
                "window_current_days": result.window_current_days,
                "window_reference_days": result.window_reference_days,
                "bins": [
                    {
                        "label": b.label,
                        "reference_pct": b.reference_pct,
                        "current_pct": b.current_pct,
                        "psi_contribution": b.psi_contribution,
                    }
                    for b in f.bins
                ],
                "thresholds": {
                    "stable": 0.10,
                    "minor": 0.25,
                },
            },
        )
        session.add(row)
    await session.flush()
    logger.info(
        "psi_monitor_ran",
        features=[
            {"feature": f.feature, "psi": f.psi, "flag": f.flag}
            for f in result.features
        ],
    )
    return result
