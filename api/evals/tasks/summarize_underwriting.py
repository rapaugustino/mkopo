"""summarize_underwriting — LLM-as-judge with a PINNED judge model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway


class UnderwritingSummary(BaseModel):
    summary: str
    citations: list[str] = Field(default_factory=list)


class JudgmentResult(BaseModel):
    factuality: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    conciseness: int = Field(ge=1, le=5)
    reasoning: str
    criteria_failures: list[str] = Field(default_factory=list)


SUMMARY_SYSTEM = (
    "Write a 1-paragraph underwriting summary (≤150 words) of this loan. "
    "Cover deal economics (DSCR, LTV), sponsor history, and any open risk items. "
    "Do not invent numbers — only state facts present in the source data."
)

JUDGE_SYSTEM = """You evaluate AI-generated loan underwriting summaries.

Score each criterion 1–5:
- factuality:    5 = every fact verifiable in source; 1 = significant hallucination
- completeness: 5 = covers all required items; 1 = missing critical items
- conciseness:   5 = within length, no padding; 1 = wordy or off-topic

Be strict on factuality: any wrong number or invented fact → score ≤ 2.
"""


class SummarizeUnderwritingTask:
    name = "summarize_underwriting"
    threshold = 0.80

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        result = await get_gateway().call_structured(
            model=settings.llm_default_model,
            system=SUMMARY_SYSTEM,
            user=f"Loan data:\n{example.inputs['loan_data']}",
            schema=UnderwritingSummary,
        )
        return {
            "summary": result.summary,
            "citations": result.citations,
            "_source_loan_data": example.inputs["loan_data"],
        }

    async def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore:
        settings = get_settings()
        judge_input = (
            f"Source data:\n{prediction['_source_loan_data']}\n\n"
            f"Required items:\n{expected['must_mention']}\n\n"
            f"Max words: {expected['max_words']}\n\n"
            f"Generated summary:\n{prediction['summary']}"
        )
        # CRITICAL: judge model is pinned. Changing it invalidates historical scores.
        judgment = await get_gateway().call_structured(
            model=settings.llm_judge_model,
            system=JUDGE_SYSTEM,
            user=judge_input,
            schema=JudgmentResult,
        )
        avg = (judgment.factuality + judgment.completeness + judgment.conciseness) / 3
        normalized = (avg - 1) / 4  # 1..5 -> 0..1
        passed = judgment.factuality >= 4 and avg >= 4.0
        return TaskScore(
            score=normalized,
            passed=passed,
            details={
                "factuality": judgment.factuality,
                "completeness": judgment.completeness,
                "conciseness": judgment.conciseness,
                "reasoning": judgment.reasoning,
                "failures": judgment.criteria_failures,
            },
        )
