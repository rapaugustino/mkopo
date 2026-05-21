"""extract_noi — numeric extraction scored against ±2% relative error."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway


class ExtractedNOI(BaseModel):
    noi: float = Field(description="Annual NOI in USD as a number")
    source_quote: str
    confidence: float = Field(ge=0, le=1)


SYSTEM = (
    "Extract the property's annual Net Operating Income (NOI) from this appraisal. "
    "Return the value as a number (no commas, no currency symbol). "
    "NOI is rental income minus operating expenses, excluding debt service."
)


class ExtractNOITask:
    name = "extract_noi"
    threshold = 0.90
    TOLERANCE = 0.02

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        result = await get_gateway().call_structured(
            model=settings.llm_default_model,
            system=SYSTEM,
            user=example.inputs["document_text"],
            schema=ExtractedNOI,
        )
        return {"noi": result.noi, "source_quote": result.source_quote}

    def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore:
        pred = float(prediction["noi"])
        gold = float(expected["noi"])

        if gold == 0:
            passed = pred == 0
            err = 0.0
        else:
            err = abs(pred - gold) / abs(gold)
            passed = err <= self.TOLERANCE

        soft = 1.0 if passed else max(0.0, 1.0 - err / 0.20)
        return TaskScore(
            score=soft,
            passed=passed,
            details={"predicted": pred, "expected": gold, "rel_error": round(err, 4)},
        )
