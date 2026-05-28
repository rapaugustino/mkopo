"""Unit tests for the refusal-rate spike z-test.

The math sits in ``compute_z_score`` (factored out of
``compute_refusal_rate`` so it's testable without a database
session). Formula is the binomial-proportion test:

    z = (p_cur - p_base) / sqrt(p_base * (1 - p_base) / n_cur)

Pinned cases:
- A clear 2× rate increase trips ``z > 2`` and the band flips
  to ``spike``.
- Identical rates → ``z ≈ 0`` → ``stable``.
- Sample-size floors enforced: below them, the function returns
  ``None`` rather than a noisy z.
- Degenerate baseline rates (0.0 or 1.0) return ``None`` — zero
  variance makes the test statistic undefined; we'd rather say
  "insufficient data" than show a nonsense pill.
"""

from __future__ import annotations

import math

from mkopo.services.refusal import _band, compute_z_score


def test_z_stable_when_current_matches_baseline():
    # 5% on both sides, plenty of samples. z should be ≈ 0
    # (down to whatever precision the rate math gives us).
    z = compute_z_score(cur_rate=0.05, base_rate=0.05, n_cur=200, n_base=200)
    assert z is not None
    assert abs(z) < 0.01
    assert _band(z) == "stable"


def test_z_spike_at_two_sigma():
    # Baseline 5% on 200 samples. Current jumps to 12% on 200
    # samples. The standard error is sqrt(0.05 * 0.95 / 200) ≈
    # 0.0154, so z = (0.12 - 0.05) / 0.0154 ≈ 4.5. Comfortably
    # past the 2σ threshold → ``spike``.
    z = compute_z_score(cur_rate=0.12, base_rate=0.05, n_cur=200, n_base=400)
    assert z is not None
    assert z > 2.0
    assert _band(z) == "spike"


def test_z_formula_pinned_value():
    # Pin the exact arithmetic so a refactor of the std-err
    # expression can't silently move the alert threshold.
    # n_cur=100, p_cur=0.20, p_base=0.10, n_base=400.
    # std_err = sqrt(0.10 * 0.90 / 100) = 0.03
    # z = (0.20 - 0.10) / 0.03 = 3.333...
    z = compute_z_score(cur_rate=0.20, base_rate=0.10, n_cur=100, n_base=400)
    assert z is not None
    assert math.isclose(z, 10 / 3, rel_tol=1e-9)


def test_z_negative_when_refusals_dropped():
    # If refusals dropped meaningfully, the z is negative. The
    # spike band catches |z| ≥ 2 either direction so a sudden
    # *drop* in blocks (maybe the detector got disabled?) also
    # surfaces.
    z = compute_z_score(cur_rate=0.01, base_rate=0.10, n_cur=200, n_base=400)
    assert z is not None
    assert z < 0
    assert _band(z) == "spike"


def test_z_none_when_current_below_floor():
    # min_current=20 by default; pass 19 → should return None.
    z = compute_z_score(
        cur_rate=0.5,
        base_rate=0.1,
        n_cur=19,
        n_base=100,
    )
    assert z is None


def test_z_none_when_baseline_below_floor():
    # min_baseline=30 by default; pass 29 → should return None.
    z = compute_z_score(
        cur_rate=0.5,
        base_rate=0.1,
        n_cur=100,
        n_base=29,
    )
    assert z is None


def test_z_none_when_baseline_rate_is_zero():
    # Zero variance — the test statistic is undefined. Returning
    # None lets the dashboard show "insufficient data" rather
    # than a +inf z-score.
    z = compute_z_score(cur_rate=0.05, base_rate=0.0, n_cur=200, n_base=400)
    assert z is None


def test_z_none_when_baseline_rate_is_one():
    # Same reasoning at the other extreme — variance is zero.
    z = compute_z_score(cur_rate=0.95, base_rate=1.0, n_cur=200, n_base=400)
    assert z is None


def test_band_thresholds():
    # The two-sigma threshold on either side.
    assert _band(0.0) == "stable"
    assert _band(1.99) == "stable"
    assert _band(2.0) == "spike"
    assert _band(-2.0) == "spike"
    assert _band(None) == "insufficient_data"


def test_custom_floors_for_testing():
    # The keyword-only floors let a test exercise the math at
    # n=5 without depending on the production constants.
    z = compute_z_score(
        cur_rate=0.20,
        base_rate=0.10,
        n_cur=5,
        n_base=10,
        min_current=5,
        min_baseline=10,
    )
    assert z is not None
