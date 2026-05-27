"""extract_appraised_value — numeric extraction scored against ±2% relative error.

The appraised value is the denominator of the LTV calc, so an
extraction miss here directly mis-prices risk. Pattern mirrors
``extract_noi``: ±2% tolerance, soft score degrading to 0 over a
20% error band. Uses the production extractor's model
(``llm_default_model``) so the eval measures the prompt-as-deployed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway


class _ExtractedAppraisedValue(BaseModel):
    appraised_value: float = Field(
        description="The appraised market value of the subject property, in USD as a number."
    )
    source_quote: str
    confidence: float = Field(ge=0, le=1)


SYSTEM = (
    "Extract the appraised market value of the subject property from "
    "this appraisal report. Return the value as a number (no commas, "
    "no currency symbol, no scaling words like 'M' or 'million'). "
    "If multiple values appear (cost approach vs income approach vs "
    "sales comparison), prefer the appraiser's RECONCILED final value "
    "estimate. Do not confuse the loan amount or any prior valuation "
    "with the current appraised value."
)


class ExtractAppraisedValueTask:
    name = "extract_appraised_value"
    threshold = 0.90
    TOLERANCE = 0.02

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        result: _ExtractedAppraisedValue = await get_gateway().call_structured(
            model=settings.llm_default_model,
            system=SYSTEM,
            user=example.inputs["document_text"],
            schema=_ExtractedAppraisedValue,
        )
        return {
            "appraised_value": result.appraised_value,
            "source_quote": result.source_quote,
        }

    def score(
        self, prediction: dict[str, Any], expected: dict[str, Any]
    ) -> TaskScore:
        pred = float(prediction["appraised_value"])
        gold = float(expected["appraised_value"])
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
            details={
                "predicted": pred,
                "expected": gold,
                "rel_error": round(err, 4),
            },
        )
