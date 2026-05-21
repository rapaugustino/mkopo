"""Eval harness shared types. Every task implements this protocol."""

from __future__ import annotations

from typing import Any, Protocol

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
    """Aggregated result for a task across all examples."""

    task_name: str
    threshold: float
    accuracy: float  # passed / total
    avg_score: float
    n: int
    passed_count: int
    failed_count: int
    failures: list[str]  # IDs of examples that failed


class EvalTask(Protocol):
    """Interface every eval task implements."""

    name: str
    threshold: float

    async def predict(self, example: Example) -> dict[str, Any]: ...

    def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore: ...
