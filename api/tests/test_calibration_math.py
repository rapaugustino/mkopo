"""Unit tests for the calibration formulae — ECE (Guo et al. 2017)
and Brier score — that sit behind the production-drift calibration
card on ``/eval``.

These are the pure-math helpers from ``mkopo.services.calibration``.
The async DB-loading wrapper around them is not exercised here;
that path is integration-shaped and gets exercised by the cron
sweep test in production. The unit-test job is to confirm the
formula doesn't silently drift the next time someone edits it.

What we pin:
- Perfect calibration → ECE = 0, Brier = 0.
- Constant-confidence + matching accuracy → ECE = 0.
- Confident-and-wrong is worse on Brier than confused-and-wrong
  (the squared-error property — Brier's tie-breaker role).
- Edge case: an empty input returns 0.0 without dividing by zero.
- Edge case: a confidence of exactly 1.0 falls into the last bin,
  not off the end — the classic ``c < upper`` off-by-one we
  guard against.
- Bin counts in the returned reliability diagram sum to N (no
  rows lost).
"""

from __future__ import annotations

import math

from mkopo.services.calibration import compute_brier, compute_ece


def test_ece_perfect_calibration_is_zero():
    # Confidence 0.95 ten times, all accept (correct): mean conf
    # 0.95, accuracy 1.0 — gap is 0.05 in the bin, but only one bin
    # has weight so the weighted gap = 0.05 * 1 = 0.05. The
    # *perfect* case is actually when the gap is zero, i.e. mean
    # conf MATCHES empirical accuracy. Pin that case.
    confidences = [0.7] * 10
    correct = [1, 1, 1, 1, 1, 1, 1, 0, 0, 0]  # 7/10 accuracy
    ece, bins = compute_ece(confidences, correct, n_bins=10)
    assert math.isclose(ece, 0.0, abs_tol=1e-9)
    # Only one bin has data — the [0.7, 0.8) bucket. Other 9 are empty.
    populated = [b for b in bins if b.n > 0]
    assert len(populated) == 1
    assert populated[0].n == 10


def test_ece_known_gap_value():
    # Bin 1: 5 samples at conf 0.7, all wrong (acc 0.0) → gap 0.7
    # Bin 2: 5 samples at conf 0.9, all right (acc 1.0) → gap 0.1
    # Each bin has weight 5/10 = 0.5.
    # ECE = 0.5 * 0.7 + 0.5 * 0.1 = 0.40.
    confidences = [0.7] * 5 + [0.9] * 5
    correct = [0] * 5 + [1] * 5
    ece, _ = compute_ece(confidences, correct, n_bins=10)
    assert math.isclose(ece, 0.4, abs_tol=1e-9)


def test_ece_empty_input_returns_zero():
    ece, bins = compute_ece([], [], n_bins=10)
    assert ece == 0.0
    assert bins == []


def test_ece_confidence_of_one_lands_in_final_bin():
    # Edge case: c == 1.0 must fall into the [0.9, 1.0] bucket,
    # not slip past the half-open interval. Without the
    # ``i == n_bins - 1 and c == 1.0`` clause in compute_ece, this
    # sample would be silently dropped — and a model that's
    # 100% confident on everything would show ECE = 0 even when
    # it's wrong half the time.
    confidences = [1.0] * 4
    correct = [1, 1, 0, 0]
    ece, bins = compute_ece(confidences, correct, n_bins=10)
    last_bin = bins[-1]
    assert last_bin.n == 4
    # acc = 0.5, conf = 1.0, gap = 0.5, weight = 1.0
    assert math.isclose(ece, 0.5, abs_tol=1e-9)


def test_ece_bin_counts_sum_to_n():
    confidences = [0.1, 0.25, 0.55, 0.7, 0.7, 0.95, 1.0]
    correct = [0, 1, 0, 1, 1, 1, 1]
    _, bins = compute_ece(confidences, correct, n_bins=10)
    assert sum(b.n for b in bins) == len(confidences)


def test_brier_perfect_predictions():
    # Confident-and-right: c=1.0, y=1 → (1-1)^2 = 0
    confidences = [1.0, 0.0, 1.0, 0.0]
    correct = [1, 0, 1, 0]
    brier = compute_brier(confidences, correct)
    assert math.isclose(brier, 0.0, abs_tol=1e-12)


def test_brier_known_value():
    # c=0.8 y=1: (0.8-1)^2 = 0.04
    # c=0.6 y=0: (0.6-0)^2 = 0.36
    # mean = 0.20
    confidences = [0.8, 0.6]
    correct = [1, 0]
    brier = compute_brier(confidences, correct)
    assert math.isclose(brier, 0.20, abs_tol=1e-12)


def test_brier_penalises_confident_wrong_more_than_uncertain_wrong():
    # Two models, both with one mistake. Model A is confidently
    # wrong (c=0.95, y=0); model B is hedging (c=0.55, y=0). Brier
    # should reward the hedge. This is THE reason Brier exists
    # alongside ECE: ECE looks at bin-level gap, Brier looks at
    # per-sample squared error and punishes overconfidence.
    confident_wrong = compute_brier([0.95], [0])
    uncertain_wrong = compute_brier([0.55], [0])
    assert confident_wrong > uncertain_wrong


def test_brier_empty_input_returns_zero():
    assert compute_brier([], []) == 0.0
