"""Unit tests for the apples-to-apples eval-summary aggregator.

This is the math behind the headline tiles on ``/eval`` — the
fix we just landed to stop comparing the full production set
against the full golden set. The pure helper
``compute_summary_aggregates`` is what the FastAPI endpoint
delegates to; pinning it here means future refactors of the
endpoint or the SQL can't silently re-introduce the
apples-to-oranges bug.

Pinned cases:
- Paired tasks: prod_acc and gold_acc both average over the
  intersection, delta is meaningful.
- Production-only or golden-only tasks: excluded from the
  headline mean but the production-only tasks contribute to
  ``suite_acc`` only if they exist on the golden side too
  (they don't — suite_acc averages golden rows).
- Suite-level number: averaged across ALL latest golden rows,
  regardless of pairing.
- Empty input on either side: graceful — None for the headline,
  zero / None for the rest depending on the field.
- Drift counter respects the threshold (default 0.03) and only
  counts paired tasks.
- Counts: ``fields_tracked`` is the intersection size,
  ``n_suite_tasks`` is the full golden suite size.
"""

from __future__ import annotations

import math

from mkopo.routers.evals import compute_summary_aggregates


def test_aggregate_paired_tasks_compute_meaningful_delta():
    # Production and golden cover the same 3 extraction fields
    # plus golden also covers 2 chat-agent tasks. The headline
    # delta must use only the paired 3 — averaging gold over 5
    # and prod over 3 would produce an apples-to-oranges
    # comparison.
    prod = {
        "extraction.borrower_name": 1.00,
        "extraction.loan_amount": 0.95,
        "extraction.credit_score": 0.90,
    }
    gold = {
        "extraction.borrower_name": 1.00,
        "extraction.loan_amount": 0.95,
        "extraction.credit_score": 0.90,
        "intake_email": 0.50,  # golden-only, drags suite down
        "aal_fidelity": 0.40,  # golden-only, drags suite down
    }
    agg = compute_summary_aggregates(prod, gold)
    # prod_acc and gold_acc are means over the intersection.
    expected_paired_mean = (1.00 + 0.95 + 0.90) / 3
    assert math.isclose(agg.prod_acc, expected_paired_mean, rel_tol=1e-9)
    assert math.isclose(agg.gold_acc, expected_paired_mean, rel_tol=1e-9)
    # Delta is now 0 — same task set on both sides.
    assert math.isclose(agg.delta, 0.0, abs_tol=1e-9)
    # Suite covers everything in golden.
    expected_suite_mean = (1.00 + 0.95 + 0.90 + 0.50 + 0.40) / 5
    assert math.isclose(agg.suite_acc, expected_suite_mean, rel_tol=1e-9)
    # Counts
    assert agg.fields_tracked == 3
    assert agg.n_suite_tasks == 5


def test_aggregate_production_only_task_excluded_from_headline():
    # A production row exists for a task that has no golden
    # counterpart (someone wrote a monitor before the eval).
    # The headline must NOT include it on either side.
    prod = {
        "extraction.borrower_name": 1.00,
        "extraction.exotic_field": 0.5,  # no golden row
    }
    gold = {
        "extraction.borrower_name": 1.00,
    }
    agg = compute_summary_aggregates(prod, gold)
    # Only borrower_name pairs.
    assert agg.fields_tracked == 1
    assert agg.prod_acc == 1.00
    assert agg.gold_acc == 1.00


def test_aggregate_golden_only_task_drags_suite_not_headline():
    # The full suite contains tasks that don't have production
    # rows (no live drift monitor yet). They should pull
    # ``suite_acc`` down but NOT affect the paired headline.
    prod = {"extraction.borrower_name": 1.00}
    gold = {
        "extraction.borrower_name": 1.00,
        "decision_verdict": 0.30,  # underperformer on golden
    }
    agg = compute_summary_aggregates(prod, gold)
    assert agg.prod_acc == 1.00
    assert agg.gold_acc == 1.00  # paired only
    assert agg.delta == 0.0
    # Suite is hurt by the underperformer.
    assert math.isclose(agg.suite_acc, (1.00 + 0.30) / 2, rel_tol=1e-9)
    assert agg.n_suite_tasks == 2


def test_aggregate_drift_count_uses_threshold():
    # Two paired tasks: one within threshold, one below.
    prod = {
        "extraction.a": 0.95,  # delta vs gold = -0.02 (within)
        "extraction.b": 0.80,  # delta vs gold = -0.15 (drift)
    }
    gold = {
        "extraction.a": 0.97,
        "extraction.b": 0.95,
    }
    agg = compute_summary_aggregates(prod, gold, drift_threshold=0.03)
    assert agg.drifting == 1
    # Override threshold to 0.10 — only the b task is flagged.
    agg2 = compute_summary_aggregates(prod, gold, drift_threshold=0.10)
    assert agg2.drifting == 1
    # Override threshold to 0.20 — neither is flagged.
    agg3 = compute_summary_aggregates(prod, gold, drift_threshold=0.20)
    assert agg3.drifting == 0


def test_aggregate_empty_production_returns_none_headline():
    # No production data yet (fresh DB / drift monitor never ran).
    # Headline tiles should be None — the dashboard renders "—".
    # Suite still has a number because golden ran.
    prod: dict[str, float] = {}
    gold = {"intake_email": 0.875}
    agg = compute_summary_aggregates(prod, gold)
    assert agg.prod_acc is None
    assert agg.gold_acc is None
    assert agg.delta is None
    assert agg.suite_acc == 0.875
    assert agg.fields_tracked == 0
    assert agg.drifting == 0
    assert agg.n_suite_tasks == 1


def test_aggregate_empty_golden_returns_none_headline():
    # Symmetric: golden never ran. Production accuracy is still
    # nominally averageable but without a baseline it's
    # meaningless — return None for both the headline and the
    # suite.
    prod = {"extraction.borrower_name": 1.0}
    gold: dict[str, float] = {}
    agg = compute_summary_aggregates(prod, gold)
    assert agg.prod_acc is None
    assert agg.gold_acc is None
    assert agg.delta is None
    assert agg.suite_acc is None
    assert agg.fields_tracked == 0


def test_aggregate_completely_empty_returns_zeros_for_counts():
    # Nothing has run yet. All None / 0 — no exceptions.
    agg = compute_summary_aggregates({}, {})
    assert agg.prod_acc is None
    assert agg.gold_acc is None
    assert agg.delta is None
    assert agg.suite_acc is None
    assert agg.fields_tracked == 0
    assert agg.drifting == 0
    assert agg.n_suite_tasks == 0


def test_aggregate_production_better_than_golden_keeps_sign():
    # Real-world this shouldn't happen often, but the math
    # should give a positive delta when production exceeds the
    # baseline.
    prod = {"extraction.borrower_name": 0.99}
    gold = {"extraction.borrower_name": 0.90}
    agg = compute_summary_aggregates(prod, gold)
    assert agg.delta is not None
    assert math.isclose(agg.delta, 0.09, abs_tol=1e-9)
    assert agg.drifting == 0  # delta is positive, not negative
