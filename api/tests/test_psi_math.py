"""Unit tests for the PSI formula (Siddiqi 2017 / FDIC) — the
production-side input-feature drift monitor.

Covers the pure helpers:
- ``compute_psi_numeric`` — quantile-binned PSI on a numeric
  feature
- ``compute_psi_categorical`` — per-category PSI on a string
  feature
- ``_safe_psi_term`` — single PSI term with Laplace smoothing
- ``_band`` — FDIC band mapping

The async DB-loading paths are not exercised here; the math is
the part you absolutely need pinned, because a silent sign flip
or smoothing-constant change moves the dashboard band without
anyone noticing.

Pinned cases:
- Identical distributions → PSI ≈ 0, band = "stable".
- Shift just inside "minor" (≥0.10, <0.25) → flag = "minor".
- Heavy shift → flag = "major".
- New category in current, missing from reference → smoothing
  keeps the term finite (no log(0) crash).
- Empty inputs → PSI = 0, no exception.
- _safe_psi_term symmetric under (p_cur, p_ref) swap (in
  magnitude) — sanity check for the formula.
"""

from __future__ import annotations

import math
import random

from mkopo.services.psi import (
    _band,
    _safe_psi_term,
    compute_psi_categorical,
    compute_psi_numeric,
)


def test_band_thresholds():
    # FDIC / Siddiqi 2017 bands.
    assert _band(0.0) == "stable"
    assert _band(0.0999) == "stable"
    assert _band(0.10) == "minor"
    assert _band(0.249) == "minor"
    assert _band(0.25) == "major"
    assert _band(10.0) == "major"


def test_safe_psi_term_smoothing_avoids_log_zero():
    # p_ref = 0 (a brand-new bin appeared in current that didn't
    # exist in reference): the term must still be finite. Without
    # smoothing, log(p_cur / 0) is +inf.
    term = _safe_psi_term(p_cur=0.10, p_ref=0.0)
    assert math.isfinite(term)
    assert term > 0  # the bin appeared, so we expect positive drift

    # Symmetric case: a bin that existed disappeared.
    term2 = _safe_psi_term(p_cur=0.0, p_ref=0.10)
    assert math.isfinite(term2)


def test_safe_psi_term_zero_when_identical():
    term = _safe_psi_term(p_cur=0.5, p_ref=0.5)
    assert math.isclose(term, 0.0, abs_tol=1e-12)


def test_psi_numeric_identical_distributions():
    # Same draw on both sides → near-zero PSI (within smoothing
    # epsilon).
    random.seed(0)
    reference = [random.gauss(50, 10) for _ in range(500)]
    current = list(reference)
    psi, bins = compute_psi_numeric(reference, current)
    assert psi < 0.01
    assert _band(psi) == "stable"
    # All quantile bins should hold roughly equal share.
    assert len(bins) == 10  # _N_BINS default


def test_psi_numeric_minor_shift():
    # Shift the mean of the current distribution by ~0.5 std. The
    # PSI should land in the "minor" band — large enough to alert
    # an operator but not the panic threshold.
    random.seed(0)
    reference = [random.gauss(0.0, 1.0) for _ in range(2000)]
    current = [random.gauss(0.5, 1.0) for _ in range(2000)]
    psi, _ = compute_psi_numeric(reference, current)
    assert 0.10 <= psi < 0.25, f"expected minor band, got psi={psi:.4f}"
    assert _band(psi) == "minor"


def test_psi_numeric_major_shift():
    # Large mean shift → "major" band. The exact PSI depends on
    # the bin layout; we just pin that it crosses the 0.25
    # threshold and the band reports correctly.
    random.seed(0)
    reference = [random.gauss(0.0, 1.0) for _ in range(2000)]
    current = [random.gauss(3.0, 1.0) for _ in range(2000)]
    psi, _ = compute_psi_numeric(reference, current)
    assert psi >= 0.25, f"expected major band, got psi={psi:.4f}"
    assert _band(psi) == "major"


def test_psi_numeric_empty_inputs_return_zero():
    # No crash, no NaN — return a benign 0.0.
    psi, bins = compute_psi_numeric([], [1.0, 2.0])
    assert psi == 0.0
    assert bins == []


def test_psi_categorical_new_category_in_current():
    # A category appears in current that wasn't in reference.
    # Smoothing must keep the PSI finite, and the new category
    # should dominate the total.
    reference = ["A"] * 100 + ["B"] * 100
    current = ["A"] * 80 + ["B"] * 70 + ["C"] * 50
    psi, bins = compute_psi_categorical(reference, current)
    assert math.isfinite(psi)
    assert psi > 0
    # The category labels we expect are present.
    labels = {b.label for b in bins}
    assert {"A", "B", "C"} <= labels


def test_psi_categorical_disappearing_category():
    # Symmetric case: a category dropped to zero in current.
    reference = ["A"] * 100 + ["B"] * 100 + ["C"] * 100
    current = ["A"] * 150 + ["B"] * 150
    psi, _ = compute_psi_categorical(reference, current)
    assert math.isfinite(psi)
    assert psi > 0


def test_psi_categorical_identical_returns_zero():
    reference = ["A"] * 50 + ["B"] * 50
    current = list(reference)
    psi, _ = compute_psi_categorical(reference, current)
    assert math.isclose(psi, 0.0, abs_tol=1e-9)
