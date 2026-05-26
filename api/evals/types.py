"""Eval harness shared types. Every task implements this protocol."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class Example(BaseModel):
    """A single labeled golden-set example."""

    id: str
    inputs: dict[str, Any]
    expected: dict[str, Any]
    metadata: dict[str, Any] = {}


class TaskScore(BaseModel):
    """Per-example score from a task."""

    score: float  # 0..1 soft score
    passed: bool  # binary pass/fail against task threshold
    details: dict[str, Any] = {}


class TaskResult(BaseModel):
    """Aggregated result for a task across all examples.

    ``details`` holds task-specific richer aggregates — e.g.
    confusion matrix + per-class F1 for the decision-verdict task,
    per-criterion pass rates for AAL fidelity, ECE + reliability
    bins for calibration. The dashboard reads ``task_runs.details``
    via ``GET /eval/task-detail/{task_name}`` and renders
    specialised cards for tasks that ship one. Tasks without a
    richer aggregate leave ``details`` empty — back-compat with
    the extraction tasks.
    """

    task_name: str
    threshold: float
    accuracy: float  # passed / total
    avg_score: float
    n: int
    passed_count: int
    failed_count: int
    failures: list[str]  # IDs of examples that failed
    details: dict[str, Any] = {}


@runtime_checkable
class AggregatingEvalTask(Protocol):
    """Tasks that opt in to richer aggregates expose ``aggregate``.

    The runner calls it after scoring every example and stuffs the
    return value into ``TaskResult.details``. Implementations should
    return a JSON-serialisable dict — the value lands in the
    ``task_runs.details`` JSONB column verbatim.

    Examples:

    - ``DecisionVerdictTask`` returns
      ``{"confusion_matrix": {...}, "per_class": {...},
        "macro_f1": ...}``.
    - ``AALFidelityTask`` returns
      ``{"per_criterion": {"cites_blocks": 0.92, ...}}``.
    - ``AdversarialInjectionTask`` returns
      ``{"by_pattern": {"instruction_override": 1.0, ...}}``.

    The existing extraction tasks don't implement this — they leave
    ``TaskResult.details`` as the default empty dict.
    """

    def aggregate(
        self,
        scores: list[TaskScore],
        examples: list[Example],
    ) -> dict[str, Any]: ...


class EvalTask(Protocol):
    """Interface every eval task implements."""

    name: str
    threshold: float

    async def predict(self, example: Example) -> dict[str, Any]: ...

    def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore: ...
