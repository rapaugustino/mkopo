"""extract_loan_amount — numeric extraction shared across both loan classes.

The loan amount is the numerator of LTV (business) and the principal
input to DTI (personal). It's surfaced on the pipeline header, the
case-file summary, and the AAL. Pattern mirrors ``extract_noi``: ±2%
tolerance, soft score degrading to 0 over a 20% error band.

Distinct from ``extract_appraised_value`` (which is the property's
market value, not the loan). Many fixtures put both numbers
side-by-side; that's the test.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway


class _ExtractedLoanAmount(BaseModel):
    loan_amount: float = Field(
        description=(
            "The principal amount the borrower has requested, in USD "
            "as a number (no commas, no currency symbol, no scaling "
            "words like 'M' or 'million')."
        ),
    )
    source_quote: str
    confidence: float = Field(ge=0, le=1)


SYSTEM = (
    "Extract the loan amount requested by the borrower from this loan "
    "application or intake document. Return the value as a number "
    "(no commas, no currency symbol, no scaling words). "
    "Do not confuse the loan amount with the property's appraised "
    "value, the purchase price, or any prior loan balance. The "
    "answer is what the BORROWER is asking us to lend on THIS "
    "application."
)


class ExtractLoanAmountTask:
    name = "extract_loan_amount"
    threshold = 0.90
    TOLERANCE = 0.02

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        result: _ExtractedLoanAmount = await get_gateway().call_structured(
            model=settings.llm_default_model,
            system=SYSTEM,
            user=example.inputs["document_text"],
            schema=_ExtractedLoanAmount,
        )
        return {
            "loan_amount": result.loan_amount,
            "source_quote": result.source_quote,
        }

    def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore:
        pred = float(prediction["loan_amount"])
        gold = float(expected["loan_amount"])
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
