"""Shared types + helpers for the /eval router package.

Anything used by two or more eval sub-modules lives here. Sub-module-
local helpers (e.g. ``_confidence_buckets`` for diagnostics, the
formatters in ``llm_diff``) stay in their own files — moving them up
just for the sake of "shared" would force callers to import across
the boundary for no benefit.

Currently shared:
- DRIFT_THRESHOLD, _DERIVED_METRIC_PREFIXES, _is_accuracy_metric,
  _latest_per_field, _latest_accuracy_rows, _SummaryAggregates,
  compute_summary_aggregates   — used by summary + (indirectly) the
                                  field/trend endpoints
- _percentile                   — used by summary's p95-latency tile
                                  and agent_economics' per-agent rows
"""

from __future__ import annotations

from dataclasses import dataclass

from mkopo.models.eval import TaskRun

# Drift below the golden baseline by this much (3 percentage points)
# is the threshold the DESIGN doc calls out for alerting.
DRIFT_THRESHOLD = 0.03


# Task-name prefixes whose ``accuracy`` column repurposes the field for
# a NON-accuracy metric (cost, ratio, PSI, MMD, block rate, ...).
# Their per-card dashboards decode the value correctly; the headline
# "Production accuracy" average MUST exclude them or it averages
# dollars-per-run alongside extraction accuracy and produces a
# nonsensical 74.1% number.
#
# Add new prefixes here when wiring up a new derived-metric monitor.
# Accuracy-shaped tasks (extraction.*, the golden eval tasks) do NOT
# need to register — they're the default.
_DERIVED_METRIC_PREFIXES = (
    "economics.",
    "fairness.",
    "psi.",
    "refusal.",
    "prompt_drift.",
    "calibration.",
)


def _is_accuracy_metric(task_name: str) -> bool:
    """True iff ``task_run.accuracy`` for this task is a proper
    [0, 1] accuracy value comparable across tasks. False for derived
    metrics that repurpose the column (see ``_DERIVED_METRIC_PREFIXES``).
    """
    return not any(task_name.startswith(p) for p in _DERIVED_METRIC_PREFIXES)


def _latest_per_field(
    rows: list[TaskRun],
    source: str,
) -> dict[str, TaskRun]:
    """Pick the most recent row for each task_name + source pairing.

    ``rows`` is already ordered ``created_at DESC``, so the first hit
    per ``task_name`` wins.
    """
    out: dict[str, TaskRun] = {}
    for r in rows:
        if r.source != source:
            continue
        if r.task_name not in out:
            out[r.task_name] = r
    return out


def _latest_accuracy_rows(rows: list[TaskRun], source: str) -> dict[str, TaskRun]:
    """Same as ``_latest_per_field`` but filtered to accuracy-shaped
    tasks only. Used by the headline KPI + drift count where the math
    only makes sense across like-shaped metrics."""
    return {
        name: r for name, r in _latest_per_field(rows, source).items() if _is_accuracy_metric(name)
    }


@dataclass
class _SummaryAggregates:
    """Wire shape between ``compute_summary_aggregates`` (pure) and
    ``get_eval_summary`` (async router). Kept tiny so a test can
    construct one without touching SQLAlchemy."""

    prod_acc: float | None
    gold_acc: float | None
    delta: float | None
    suite_acc: float | None
    fields_tracked: int
    drifting: int
    n_suite_tasks: int


def compute_summary_aggregates(
    prod_by_task: dict[str, float],
    gold_by_task: dict[str, float],
    drift_threshold: float = DRIFT_THRESHOLD,
) -> _SummaryAggregates:
    """Pure-function version of the headline aggregation that the
    ``/eval/summary`` endpoint serves. Extracted from
    ``get_eval_summary`` so the apples-to-apples pairing logic is
    unit-testable without a database session.

    Inputs are pre-flattened ``{task_name: accuracy}`` dicts of the
    latest accuracy-shaped rows. The caller is responsible for
    filtering to accuracy-shaped tasks (i.e. excluding the
    economics / fairness / PSI / refusal / prompt_drift /
    calibration prefixes) — same filter the endpoint applies.

    Behaviour:
    - ``prod_acc`` / ``gold_acc`` / ``delta`` are means over the
      **intersection** of the two task sets — apples-to-apples.
      Comparing the full production set against the full golden
      set would over-count tasks that exist on only one side.
    - ``suite_acc`` is the unweighted mean over the entire golden
      set (NOT restricted to the intersection) — the "how is the
      eval suite doing overall" headline.
    - ``drifting`` counts paired tasks where
      ``prod − gold ≤ -threshold``.
    - ``fields_tracked`` is the size of the intersection (same
      denominator as the three headline values).
    """
    paired = set(prod_by_task) & set(gold_by_task)
    prod_acc = sum(prod_by_task[k] for k in paired) / len(paired) if paired else None
    gold_acc = sum(gold_by_task[k] for k in paired) / len(paired) if paired else None
    delta = prod_acc - gold_acc if prod_acc is not None and gold_acc is not None else None
    suite_acc = sum(gold_by_task.values()) / len(gold_by_task) if gold_by_task else None
    drifting = sum(1 for k in paired if prod_by_task[k] - gold_by_task[k] <= -drift_threshold)
    return _SummaryAggregates(
        prod_acc=prod_acc,
        gold_acc=gold_acc,
        delta=delta,
        suite_acc=suite_acc,
        fields_tracked=len(paired),
        drifting=drifting,
        n_suite_tasks=len(gold_by_task),
    )


def _percentile(values: list[float], pct: float) -> float | None:
    """Plain nearest-rank percentile. ``values`` is mutated (sorted).

    Two other p95 implementations exist (services/agent_economics uses
    linear-interpolation; routers/observability has its own
    nearest-rank). See docs/GLOSSARY.md "p50/p95/p99 latency" for the
    rationale.
    """
    if not values:
        return None
    values.sort()
    k = max(0, min(len(values) - 1, int(round((pct / 100.0) * (len(values) - 1)))))
    return values[k]
