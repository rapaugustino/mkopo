"""Eval runner. Loads golden sets, runs tasks, applies the threshold gate.

Two writers feed the ``/eval`` dashboard:

- This runner (``source="golden"``) — scores prompts against the
  labelled YAML fixtures in ``evals/golden_sets/``. Runs from CI
  (``cd api && uv run python -m evals.runner``) and from the
  scheduled job in ``workers/tasks.py``.
- ``services/drift.py:run_drift_monitor`` (``source="production"``)
  — computes accuracy from staff overrides in the review queue.
  Runs from ``/eval/refresh`` + a separate scheduled job.

Both write to ``task_runs``; the dashboard surfaces them side-by-side
as ``golden_accuracy`` vs ``production_accuracy``. The delta between
them is the drift signal — golden stable, production dropping →
prompts drifting away from the eval fixtures' coverage.

Each task_name written is namespaced ``extraction.<field>`` to match
``drift_monitor``'s convention so per-field pairing on the dashboard
is automatic.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import structlog
import yaml

from evals.types import (
    AggregatingEvalTask,
    EvalTask,
    Example,
    TaskResult,
    TaskScore,
)
from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.models.eval import TaskRun

logger = structlog.get_logger()


def load_examples(task_name: str) -> list[Example]:
    """Load all YAML examples for a task from the golden set directory."""
    settings = get_settings()
    task_dir = Path(settings.eval_golden_set_dir) / task_name
    if not task_dir.exists():
        return []
    examples: list[Example] = []
    for yaml_file in sorted(task_dir.glob("*.yaml")):
        with yaml_file.open() as f:
            data = yaml.safe_load(f)
            examples.append(Example.model_validate(data))
    return examples


async def run_task(task: EvalTask, examples: list[Example] | None = None) -> TaskResult:
    """Run a single task across its golden set."""
    examples = examples if examples is not None else load_examples(task.name)
    if not examples:
        return TaskResult(
            task_name=task.name,
            threshold=task.threshold,
            accuracy=0.0,
            avg_score=0.0,
            n=0,
            passed_count=0,
            failed_count=0,
            failures=[],
        )

    scores: list[TaskScore] = []
    failed_ids: list[str] = []

    for ex in examples:
        try:
            pred = await task.predict(ex)
            score_or_coro = task.score(pred, ex.expected)
            score = await score_or_coro if inspect.isawaitable(score_or_coro) else score_or_coro
        except Exception as e:
            score = TaskScore(score=0.0, passed=False, details={"error": str(e)})
        scores.append(score)
        if not score.passed:
            failed_ids.append(ex.id)

    n = len(scores)
    passed = sum(1 for s in scores if s.passed)
    avg = sum(s.score for s in scores) / n if n else 0.0

    # Optional per-task aggregate hook. Tasks that implement
    # ``AggregatingEvalTask`` (decision-verdict, AAL-fidelity,
    # injection, calibration) return a richer details dict that
    # the dashboard renders as task-specific cards (confusion
    # matrix, per-criterion pass rates, etc.). Tasks without it
    # leave details empty.
    details: dict = {}
    if isinstance(task, AggregatingEvalTask):
        try:
            details = task.aggregate(scores, examples)
        except Exception as e:
            details = {"_aggregate_error": str(e)}

    return TaskResult(
        task_name=task.name,
        threshold=task.threshold,
        accuracy=passed / n if n else 0.0,
        avg_score=avg,
        n=n,
        passed_count=passed,
        failed_count=n - passed,
        failures=failed_ids,
        details=details,
    )


async def run_suite(tasks: list[EvalTask]) -> dict[str, TaskResult]:
    """Run every task and return results keyed by name."""
    results: dict[str, TaskResult] = {}
    for task in tasks:
        results[task.name] = await run_task(task)
    return results


def gate(results: dict[str, TaskResult]) -> tuple[bool, list[str]]:
    """The CI gate. Returns (all_passed, list_of_failed_task_names)."""
    failed = [r.task_name for r in results.values() if r.accuracy < r.threshold]
    return (not failed, failed)


def write_results(results: dict[str, TaskResult], path: Path) -> None:
    """Write results to a JSON file for CI artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: r.model_dump() for name, r in results.items()}
    path.write_text(json.dumps(payload, indent=2, default=str))


async def persist_results_to_db(results: dict[str, TaskResult]) -> int:
    """Write one ``task_runs`` row per task with ``source="golden"``.

    Mirrors the writer pattern in ``services/drift.py`` so the
    dashboard's ``GET /eval/summary`` / ``/eval/fields`` endpoints
    surface these alongside the production-side rows automatically.
    Each subsequent CLI run inserts new rows; the dashboard reads
    the latest row per (task_name, source) as authoritative, so the
    history accumulates into a trend without needing UPSERTs.

    Skips rows with ``n=0`` (no examples in the golden set) — those
    would write 0.0 accuracy and pollute the trend.

    Returns the number of rows inserted. Failures are logged but
    don't crash the CLI — the JSON output + exit code remain the
    primary CI gate.
    """
    inserted = 0
    try:
        async with get_session() as session:
            for name, r in results.items():
                if r.n == 0:
                    continue
                row = TaskRun(
                    task_name=name,
                    source="golden",
                    n=r.n,
                    accuracy=r.accuracy,
                    avg_score=r.avg_score,
                    details={
                        "threshold": r.threshold,
                        "passed_count": r.passed_count,
                        "failed_count": r.failed_count,
                        "failures": r.failures,
                        # Merge in the task-specific aggregate (confusion
                        # matrix, per-criterion rates, etc.). Empty for
                        # tasks that don't implement AggregatingEvalTask.
                        **r.details,
                    },
                )
                session.add(row)
                inserted += 1
            await session.commit()
    except Exception as e:
        logger.error(
            "eval_runner_persist_failed",
            error=str(e),
            n_results=len(results),
        )
        # Don't re-raise — the JSON results + CI exit code still work
        # even if the DB is unavailable (e.g. running outside the
        # cluster). The dashboard just doesn't update for this run.
        return 0
    logger.info("eval_runner_persisted", n_rows=inserted)
    return inserted


# --- CLI entry point ---


async def _main() -> int:
    from evals.tasks.aal_fidelity import AALFidelityTask
    from evals.tasks.adversarial_injection import AdversarialInjectionTask
    from evals.tasks.decision_verdict import DecisionVerdictTask
    from evals.tasks.extract_appraised_value import ExtractAppraisedValueTask
    from evals.tasks.extract_borrower_entity import ExtractBorrowerEntityTask
    from evals.tasks.extract_credit_score import ExtractCreditScoreTask
    from evals.tasks.extract_loan_amount import ExtractLoanAmountTask
    from evals.tasks.extract_noi import ExtractNOITask
    from evals.tasks.intake_email import IntakeEmailTask
    from evals.tasks.summarize_underwriting import SummarizeUnderwritingTask
    from evals.tasks.uw_groundedness import UWGroundednessTask

    tasks: list[EvalTask] = [
        # Extraction tasks. Original three + Phase 2.5 expansion to
        # cover the load-bearing numerics (LTV denominator, FICO
        # floor, principal amount). Order keeps the original three
        # first so existing dashboard/CI snapshots read consistently.
        ExtractBorrowerEntityTask(),
        ExtractNOITask(),
        SummarizeUnderwritingTask(),
        ExtractAppraisedValueTask(),
        ExtractCreditScoreTask(),
        ExtractLoanAmountTask(),
        # Phase 2 additions — see docs/EVAL_PLAN.md.
        AdversarialInjectionTask(),
        DecisionVerdictTask(),
        AALFidelityTask(),
        # Phase 2.5 — borrower-facing email + RAGAS-style faithfulness.
        IntakeEmailTask(),
        UWGroundednessTask(),
    ]

    print("Running eval suite...")
    results = await run_suite(tasks)

    for name, r in results.items():
        status = "PASS" if r.accuracy >= r.threshold else "FAIL"
        print(f"  {status}  {name:<35} {r.accuracy:.2%} (n={r.n}, threshold={r.threshold:.0%})")

    settings = get_settings()
    write_results(results, Path(settings.eval_results_dir) / "results.json")

    # Mirror the results into ``task_runs`` so the /eval dashboard
    # surfaces this run alongside the production drift numbers. Same
    # storage the scheduled job uses — interactive CLI runs and the
    # cron sweep both update the dashboard.
    n_persisted = await persist_results_to_db(results)
    if n_persisted:
        print(f"   (persisted {n_persisted} row(s) to task_runs source='golden')")

    all_passed, failed = gate(results)
    if not all_passed:
        print(f"\n❌ Eval gate failed. Tasks below threshold: {', '.join(failed)}")
        return 1
    print("\n✅ All tasks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
