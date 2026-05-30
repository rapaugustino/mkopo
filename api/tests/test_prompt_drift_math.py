"""Unit tests for the MMD² formula (Gretton et al. 2012) — the
production-side semantic-drift monitor on inbound borrower text.

Covers the pure helpers:
- ``compute_mmd2``           — unbiased U-statistic MMD² with RBF kernel
- ``_rbf``                   — Gaussian kernel
- ``_sq_dist``               — squared L2 distance
- ``_median``                — pure-Python median
- ``_median_heuristic_sigma``— bandwidth selector
- ``_band``                  — calibrated threshold mapping

The async DB-loading + embedding paths are exercised by the
integration tests. This file pins the math so a silent sign flip
in the cross-term, a kernel-symmetry break, or a smoothing-constant
change can't move the dashboard band without a test failing — same
contract every other math-heavy monitor on the dashboard ships
(see test_psi_math.py, test_calibration_math.py).

Pinned cases:
- ``_band`` threshold edges (stable / minor / major / insufficient_data)
- RBF kernel: k(x,x)=1, symmetric, bounded in [0, 1]
- Median heuristic σ: positive, degenerate-corpus guard returns 1.0
- MMD²(X, X) on a single sample is O(1/n) — near zero for moderate n
- MMD²(shifted) clearly larger than MMD²(identical) — separation detected
- MMD² symmetric in argument order: MMD²(X, Y) == MMD²(Y, X)
- Tiny samples (n<2 or m<2) return (0.0, 1.0) instead of dividing by zero
"""

from __future__ import annotations

import math
import random

from mkopo.services.prompt_drift import (
    _band,
    _median,
    _median_heuristic_sigma,
    _rbf,
    _sq_dist,
    compute_mmd2,
)


# ---- _band -------------------------------------------------------------------


def test_band_thresholds():
    # Bands calibrated on the production text embedder
    # (text-embedding-3-small, Matryoshka-truncated to 1024d).
    # The mapping itself is independent of dimensionality.
    assert _band(0.0) == "stable"
    assert _band(0.0049) == "stable"
    assert _band(0.005) == "minor"
    assert _band(0.0199) == "minor"
    assert _band(0.02) == "major"
    assert _band(10.0) == "major"


def test_band_insufficient_data():
    # The dashboard uses this branch to render the empty-state copy
    # instead of a number; treat ``None`` as a first-class value.
    assert _band(None) == "insufficient_data"


# ---- _sq_dist + _median ------------------------------------------------------


def test_sq_dist_zero_for_identical_vectors():
    v = [1.0, 2.0, 3.0]
    assert _sq_dist(v, v) == 0.0


def test_sq_dist_known_value():
    # ||(1,0) - (0,1)||² = 1 + 1 = 2.
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert _sq_dist(a, b) == 2.0


def test_median_odd_and_even_and_empty():
    assert _median([3.0, 1.0, 2.0]) == 2.0
    assert _median([4.0, 1.0, 3.0, 2.0]) == 2.5
    # Guard against empty input — used during degenerate corpora.
    assert _median([]) == 0.0


# ---- _rbf --------------------------------------------------------------------


def test_rbf_self_kernel_is_one():
    # k(x, x) = exp(0) = 1. Fundamental for any translation-invariant
    # kernel; if this breaks the whole MMD construction is wrong.
    v = [1.0, 2.0, 3.0]
    assert math.isclose(_rbf(v, v, sigma_sq=1.0), 1.0, rel_tol=1e-12)


def test_rbf_symmetric_in_arguments():
    a = [0.0, 1.0, 2.0]
    b = [1.0, 0.5, -1.0]
    assert math.isclose(
        _rbf(a, b, sigma_sq=1.0),
        _rbf(b, a, sigma_sq=1.0),
        rel_tol=1e-12,
    )


def test_rbf_bounded_in_unit_interval():
    # RBF is bounded on [0, 1]. Pin the tail end: very-far vectors
    # should give near-zero, not negative or > 1.
    a = [0.0, 0.0]
    b = [100.0, 100.0]
    val = _rbf(a, b, sigma_sq=1.0)
    assert 0.0 <= val <= 1.0


# ---- _median_heuristic_sigma -------------------------------------------------


def test_median_heuristic_sigma_returns_positive_finite():
    random.seed(0)
    vecs = [[random.gauss(0.0, 1.0) for _ in range(8)] for _ in range(20)]
    sigma = _median_heuristic_sigma(vecs)
    assert sigma > 0
    assert math.isfinite(sigma)


def test_median_heuristic_sigma_degenerate_corpus_returns_one():
    # All-identical vectors → every pairwise squared distance is 0 →
    # the ``med > 1e-9`` guard kicks in and returns 1.0 rather than
    # propagating a div-by-zero into the kernel.
    vecs = [[1.0, 2.0, 3.0] for _ in range(10)]
    assert _median_heuristic_sigma(vecs) == 1.0


# ---- compute_mmd2 ------------------------------------------------------------


def test_compute_mmd2_identical_sample_is_near_zero():
    # X = Y exactly: the unbiased U-statistic has expectation zero.
    # The realised value is O(1/n) and may be slightly negative
    # (that's a documented property of the unbiased estimator).
    random.seed(0)
    sample = [[random.gauss(0.0, 1.0) for _ in range(8)] for _ in range(80)]
    mmd2, sigma = compute_mmd2(sample, sample)
    assert sigma > 0
    # With n=80 the magnitude is well below the "major" threshold
    # of 0.02. Loose enough to absorb sample noise; tight enough
    # that a sign-flipped cross-term would fail this case (the
    # cross-term dominates and would push |mmd2| toward ~1.0).
    assert abs(mmd2) < 0.05, f"identical-sample MMD² should be ~0, got {mmd2}"


def test_compute_mmd2_shifted_distributions_register_drift():
    # Two well-separated 8-d Gaussians. The cross-term shrinks
    # (samples land in disjoint regions of feature space) while the
    # within-terms stay moderate, so MMD² should be clearly
    # positive. We don't pin a specific band — bands are calibrated
    # for 1024d text embeddings — only that the formula registers
    # the shift at all.
    random.seed(0)
    reference = [[random.gauss(0.0, 1.0) for _ in range(8)] for _ in range(60)]
    current = [[random.gauss(5.0, 1.0) for _ in range(8)] for _ in range(60)]
    mmd2, sigma = compute_mmd2(current, reference)
    assert sigma > 0
    assert mmd2 > 0.05, f"expected clearly positive MMD² for shifted samples, got {mmd2}"


def test_compute_mmd2_shifted_greater_than_identical():
    # Sanity: any shift detector worth its salt should report a
    # strictly larger value for shifted vs identical samples on
    # the same baseline. Catches degenerate cases where MMD² is
    # constant-valued for any input.
    random.seed(0)
    base = [[random.gauss(0.0, 1.0) for _ in range(8)] for _ in range(50)]
    shifted = [[random.gauss(3.0, 1.0) for _ in range(8)] for _ in range(50)]
    mmd_same, _ = compute_mmd2(base, base)
    mmd_diff, _ = compute_mmd2(base, shifted)
    assert mmd_diff > mmd_same


def test_compute_mmd2_symmetric_in_argument_order():
    # MMD²(X, Y) == MMD²(Y, X) — the formula is symmetric. The
    # most likely way to break it is to mistype the cross-term
    # denominator from ``n*m`` to ``n*n`` (or similar). Catches
    # that class of regression.
    random.seed(0)
    x = [[random.gauss(0.0, 1.0) for _ in range(8)] for _ in range(30)]
    y = [[random.gauss(0.7, 1.0) for _ in range(8)] for _ in range(30)]
    mmd_xy, _ = compute_mmd2(x, y)
    mmd_yx, _ = compute_mmd2(y, x)
    assert math.isclose(mmd_xy, mmd_yx, rel_tol=1e-9, abs_tol=1e-9)


def test_compute_mmd2_tiny_samples_return_safe_default():
    # n < 2 or m < 2 makes the 1/(n(n-1)) factor undefined. The
    # implementation short-circuits to (0.0, 1.0) — neutral output
    # that the band layer will report as ``stable``. Important: the
    # call must not raise, because the monitor runs on a cron and
    # an exception would silently miss a window.
    mmd2, sigma = compute_mmd2([[1.0, 2.0]], [[3.0, 4.0], [5.0, 6.0]])
    assert mmd2 == 0.0
    assert sigma == 1.0
    mmd2, sigma = compute_mmd2([[1.0, 2.0], [3.0, 4.0]], [[5.0, 6.0]])
    assert mmd2 == 0.0
    assert sigma == 1.0
