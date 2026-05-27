"""extract_credit_score — exact-integer extraction for FICO floor.

The credit score is the gate for the personal-loan FICO-floor rule
(default 660). A wrong integer here is a direct compliance miss —
either a creditworthy applicant gets a hard decline or a risky one
slips past the rule. The pattern is exact-integer match because
FICO scores are integers in the [300, 850] band by definition.

Uses Haiku because the extraction is straightforward and the
volume on personal-loan packets justifies the cost win.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway


class _ExtractedCreditScore(BaseModel):
    credit_score: int = Field(
        ge=300,
        le=850,
        description="FICO credit score as an integer in [300, 850].",
    )
    source_quote: str
    confidence: float = Field(ge=0, le=1)


SYSTEM = (
    "Extract the applicant's FICO credit score from this credit "
    "report or loan packet. Return the score as an integer between "
    "300 and 850. If multiple scores are reported (Equifax, Experian, "
    "TransUnion), prefer the MIDDLE of the three — that's the score "
    "underwriting will use. If only one score is given, return it. "
    "Do not return Vantage scores or any score outside [300, 850]."
)


class ExtractCreditScoreTask:
    name = "extract_credit_score"
    threshold = 0.95

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        result: _ExtractedCreditScore = await get_gateway().call_structured(
            model=settings.llm_fast_model,
            system=SYSTEM,
            user=example.inputs["document_text"],
            schema=_ExtractedCreditScore,
        )
        return {
            "credit_score": result.credit_score,
            "source_quote": result.source_quote,
        }

    def score(
        self, prediction: dict[str, Any], expected: dict[str, Any]
    ) -> TaskScore:
        pred = int(prediction["credit_score"])
        gold = int(expected["credit_score"])
        passed = pred == gold
        return TaskScore(
            score=1.0 if passed else 0.0,
            passed=passed,
            details={
                "predicted": pred,
                "expected": gold,
                "diff": pred - gold,
            },
        )
