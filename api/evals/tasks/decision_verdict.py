"""decision_verdict — scored classification task on the decision agent.

Each fixture supplies a rule-outcome set + extractions; the task
calls the same prompt the live decision agent uses and asserts the
LLM picks the expected path (approve / conditional / decline). Score
is per-class precision/recall/F1 + macro-F1 + a confusion matrix —
this is what SR 11-7 outcome analysis requires for any classification
model in a US bank (Federal Reserve SR 11-7 §VI).

The model used is ``llm_default_model`` — same model the agent
runs in production, so we measure the prompt-as-deployed, not the
prompt in a research configuration.

Note: this task tests JUST the LLM step. The server-side rule
override (in ``agents/decision.py``) is verified separately by
``tests/test_safety_scenarios.py::TestServerSideOverride``. If the
LLM picks ``approve`` here on a blocking-failure case the eval fails
— that's by design: in production the override would rescue the
decision, but the eval surface is *prompt quality*, not the
defense-in-depth wrapper. A regression in the prompt that the
override silently masks is exactly what we want to catch.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway

VerdictPath = Literal["approve", "conditional", "decline"]

CLASSES: tuple[VerdictPath, ...] = ("approve", "conditional", "decline")


class _DraftedVerdict(BaseModel):
    """Structured-output schema, mirrors the live agent's shape."""

    path: VerdictPath
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=400)


SYSTEM = (
    "You are a senior credit officer evaluating a loan application. "
    "Pick ONE verdict path:\n"
    "- 'approve': all rules pass; no blocking failures.\n"
    "- 'conditional': missing required documents OR warnings serious "
    "enough to warrant conditions; the loan can proceed once those "
    "are resolved.\n"
    "- 'decline': any blocking-severity rule failed AND the failure "
    "isn't a doc-completeness issue that conditions could resolve.\n\n"
    "Output the verdict, a confidence in [0, 1], and a one-paragraph "
    "rationale. You must base your verdict ONLY on the supplied rule "
    "outcomes — do not speculate about unstated facts."
)


def _user_message(inputs: dict[str, Any]) -> str:
    """Render the example's rule outcomes + extractions as the user
    message. Mirrors the format the live decision drafter uses so
    the eval measures the same thing the agent does."""
    rule_block = "\n".join(
        f"- {r['rule_id']} (severity={r['severity']}, "
        f"passed={r['passed']}): {r['message']}"
        for r in inputs.get("rule_outcomes", [])
    )
    ext_block = "\n".join(
        f"- {k}: {v}" for k, v in (inputs.get("extractions") or {}).items()
    )
    return (
        f"Loan class: {inputs.get('loan_class', 'business')}\n\n"
        f"Rule outcomes:\n{rule_block}\n\n"
        f"Accepted extractions:\n{ext_block or '(none)'}\n\n"
        "Pick a verdict path now."
    )


class DecisionVerdictTask:
    name = "decision_verdict"
    threshold = 0.85

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        result: _DraftedVerdict = await get_gateway().call_structured(
            model=settings.llm_default_model,
            system=SYSTEM,
            user=_user_message(example.inputs),
            schema=_DraftedVerdict,
        )
        return {
            "path": result.path,
            "confidence": result.confidence,
        }

    def score(
        self, prediction: dict[str, Any], expected: dict[str, Any]
    ) -> TaskScore:
        pred_path = prediction["path"]
        gold_path = expected["path"]
        passed = pred_path == gold_path
        return TaskScore(
            score=1.0 if passed else 0.0,
            passed=passed,
            details={
                "predicted_path": pred_path,
                "expected_path": gold_path,
                "predicted_confidence": prediction.get("confidence"),
            },
        )

    def aggregate(
        self,
        scores: list[TaskScore],
        examples: list[Example],
    ) -> dict[str, Any]:
        """Build the confusion matrix + per-class precision/recall/F1
        + macro-F1. Returns the dict that lands in ``task_runs.details``
        and gets rendered as the confusion-matrix card on the eval
        dashboard.
        """
        # Confusion matrix as a nested dict keyed [expected][predicted].
        # NB: we use the closed CLASSES tuple so the matrix has stable
        # axis order even when a class is unrepresented in the run.
        cm: dict[str, dict[str, int]] = {
            c: {c2: 0 for c2 in CLASSES} for c in CLASSES
        }
        for score, ex in zip(scores, examples, strict=True):
            expected_path = ex.expected.get("path")
            predicted_path = score.details.get("predicted_path")
            if expected_path in CLASSES and predicted_path in CLASSES:
                cm[expected_path][predicted_path] += 1

        # Per-class precision / recall / F1. The standard one-vs-rest
        # decomposition for multi-class confusion matrices.
        per_class: dict[str, dict[str, float]] = {}
        f1s: list[float] = []
        for c in CLASSES:
            tp = cm[c][c]
            fn = sum(cm[c][c2] for c2 in CLASSES if c2 != c)
            fp = sum(cm[c2][c] for c2 in CLASSES if c2 != c)
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if (precision + recall)
                else 0.0
            )
            per_class[c] = {
                "n": sum(cm[c].values()),  # support
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "tp": tp,
                "fp": fp,
                "fn": fn,
            }
            f1s.append(f1)

        macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0

        return {
            "confusion_matrix": cm,
            "per_class": per_class,
            "macro_f1": macro_f1,
            "classes": list(CLASSES),
        }
