"""Unit tests for the AIR / four-fifths-rule math behind the
fairness card on ``/eval``.

The pure surface is ``FairnessResult.from_groups`` — a classmethod
that takes a list of ``_GroupStats`` and computes:

    AIR = min(approval_rate) / max(approval_rate)

with a flag based on the EEOC convention (1978 Uniform Guidelines,
29 CFR §1607.4(D)). Bands:
    AIR ≥ 0.85  → "ok"
    0.80–0.85   → "watch"  (our local early-warning band)
    < 0.80      → "concern"

We also pin the insufficient-data behaviour — having only one
group with decisions, or every group with 0% approval — must not
crash and must report ``insufficient_data`` rather than a NaN AIR.

The protected-class signal is *synthetic* in the demo (see
docstring on ``_synthetic_class_for_loan``); these tests do NOT
exercise the bucketing. They exercise only the AIR math, which is
the part that has to be right regardless of where the class
attribute came from.
"""

from __future__ import annotations

import math

from mkopo.services.fairness import FairnessResult, _GroupStats


def _g(name: str, decisioned: int, approved: int) -> _GroupStats:
    """Construct a _GroupStats with explicit counts. Declined =
    decisioned - approved. The dataclass takes the three counts as
    explicit fields so we set them all."""
    return _GroupStats(
        name=name,
        n_decisioned=decisioned,
        n_approved=approved,
        n_declined=decisioned - approved,
    )


def test_air_perfectly_equal_groups():
    # Two groups, both 50% approval → AIR = 1.0 → "ok"
    groups = [_g("A", 100, 50), _g("B", 100, 50)]
    r = FairnessResult.from_groups(groups, window_days=7)
    assert r.air == 1.0
    assert r.flag == "ok"


def test_air_at_four_fifths_boundary():
    # 80%/100% = 0.80 — right at the EEOC threshold. Per code,
    # AIR < 0.80 is "concern", >= 0.80 is "watch" or "ok". So this
    # is "watch" territory (the 0.80–0.85 early-warning band).
    groups = [_g("A", 100, 80), _g("B", 100, 100)]
    r = FairnessResult.from_groups(groups, window_days=7)
    assert math.isclose(r.air, 0.80, abs_tol=1e-12)
    assert r.flag == "watch"


def test_air_below_four_fifths_is_concern():
    # 0.6 / 0.9 = 0.667 → "concern"
    groups = [_g("A", 100, 60), _g("B", 100, 90)]
    r = FairnessResult.from_groups(groups, window_days=7)
    assert math.isclose(r.air, 60 / 90, rel_tol=1e-12)
    assert r.flag == "concern"


def test_air_well_above_threshold_is_ok():
    # 90/100 vs 95/100 → 0.947 → "ok"
    groups = [_g("A", 100, 90), _g("B", 100, 95)]
    r = FairnessResult.from_groups(groups, window_days=7)
    assert r.air is not None and r.air >= 0.85
    assert r.flag == "ok"


def test_air_single_group_reports_insufficient_data():
    # Only one group has any decisions in the window — AIR is
    # undefined. The card must NOT crash or fabricate a value.
    groups = [_g("A", 100, 80), _g("B", 0, 0)]
    r = FairnessResult.from_groups(groups, window_days=7)
    assert r.air is None
    assert r.flag == "insufficient_data"


def test_air_all_zero_approvals_reports_insufficient_data():
    # Both groups present but no approvals anywhere. Division
    # would be 0/0; we want a graceful flag, not NaN.
    groups = [_g("A", 100, 0), _g("B", 100, 0)]
    r = FairnessResult.from_groups(groups, window_days=7)
    assert r.air is None
    assert r.flag == "insufficient_data"


def test_air_uses_min_max_across_multiple_groups():
    # Three groups; AIR is determined by the worst-best pair.
    # Group rates: 0.9, 0.85, 0.50 → AIR = 0.5/0.9 ≈ 0.556 → "concern".
    groups = [_g("A", 100, 90), _g("B", 100, 85), _g("C", 100, 50)]
    r = FairnessResult.from_groups(groups, window_days=7)
    assert math.isclose(r.air, 0.5 / 0.9, rel_tol=1e-12)
    assert r.flag == "concern"


def test_air_counts_only_groups_with_decisions():
    # A group with zero decisions in the window must be ignored
    # when computing min/max — otherwise its 0% rate makes AIR=0
    # and the card screams "concern" every time we add an empty
    # group to the schema.
    groups = [
        _g("A", 100, 80),
        _g("B", 100, 90),
        _g("C", 0, 0),  # no decisions in this window
    ]
    r = FairnessResult.from_groups(groups, window_days=7)
    # AIR is from {0.8, 0.9} → 0.889 → "ok".
    assert math.isclose(r.air, 0.8 / 0.9, rel_tol=1e-12)
    assert r.flag == "ok"


def test_air_payload_preserves_per_group_counts():
    # The dashboard's per-group breakdown reads from the returned
    # groups list — pin that the field names match the wire shape
    # ``FairnessCard`` consumes.
    groups = [_g("A", 100, 60), _g("B", 100, 90)]
    r = FairnessResult.from_groups(groups, window_days=7)
    a, b = r.groups
    assert a["name"] == "A"
    assert a["n_decisioned"] == 100
    assert a["n_approved"] == 60
    assert a["n_declined"] == 40
    assert math.isclose(a["approval_rate"], 0.60)
    assert b["approval_rate"] == 0.90
