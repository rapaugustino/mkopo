"""Eval runner. Loads golden sets, runs tasks, applies the threshold gate."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import yaml

from evals.types import EvalTask, Example, TaskResult, TaskScore
from mkopo.config import get_settings


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

    return TaskResult(
        task_name=task.name,
        threshold=task.threshold,
        accuracy=passed / n if n else 0.0,
        avg_score=avg,
        n=n,
        passed_count=passed,
        failed_count=n - passed,
        failures=failed_ids,
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


# --- CLI entry point ---


async def _main() -> int:
    from evals.tasks.extract_borrower_entity import ExtractBorrowerEntityTask
    from evals.tasks.extract_noi import ExtractNOITask
    from evals.tasks.summarize_underwriting import SummarizeUnderwritingTask

    tasks: list[EvalTask] = [
        ExtractBorrowerEntityTask(),
        ExtractNOITask(),
        SummarizeUnderwritingTask(),
    ]

    print("Running eval suite...")
    results = await run_suite(tasks)

    for name, r in results.items():
        status = "PASS" if r.accuracy >= r.threshold else "FAIL"
        print(f"  {status}  {name:<35} {r.accuracy:.2%} (n={r.n}, threshold={r.threshold:.0%})")

    settings = get_settings()
    write_results(results, Path(settings.eval_results_dir) / "results.json")

    all_passed, failed = gate(results)
    if not all_passed:
        print(f"\n❌ Eval gate failed. Tasks below threshold: {', '.join(failed)}")
        return 1
    print("\n✅ All tasks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
