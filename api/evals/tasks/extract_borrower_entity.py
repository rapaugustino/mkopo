"""extract_borrower_entity — normalized exact-match scoring."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway


class ExtractedBorrower(BaseModel):
    borrower_entity: str = Field(description="Legal name of the borrower")
    source_quote: str = Field(description="Exact text from the document")
    confidence: float = Field(ge=0, le=1)


SYSTEM = (
    "Extract the borrower's legal entity name from the loan document. "
    "Return the exact legal name including any LLC/Inc/Corp suffix. "
    "If multiple entities appear, choose the one identified as the borrower."
)


def normalize(name: str) -> str:
    """Strip cosmetic variants so 'Atlas Holdings, LLC' == 'Atlas Holdings LLC'."""
    name = name.upper().strip()
    name = re.sub(r"[,.]", "", name)
    name = re.sub(r"\b(LIMITED LIABILITY COMPANY|L\.?L\.?C\.?)\b", "LLC", name)
    name = re.sub(r"\b(INCORPORATED|INC\.?)\b", "INC", name)
    name = re.sub(r"\bCORPORATION\b", "CORP", name)
    return re.sub(r"\s+", " ", name).strip()


class ExtractBorrowerEntityTask:
    name = "extract_borrower_entity"
    threshold = 0.95

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        result = await get_gateway().call_structured(
            model=settings.llm_fast_model,
            system=SYSTEM,
            user=example.inputs["document_text"],
            schema=ExtractedBorrower,
        )
        return {"borrower_entity": result.borrower_entity, "source_quote": result.source_quote}

    def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore:
        pred = normalize(prediction["borrower_entity"])
        gold = normalize(expected["borrower_entity"])
        passed = pred == gold
        return TaskScore(
            score=1.0 if passed else 0.0,
            passed=passed,
            details={"predicted_normalized": pred, "expected_normalized": gold},
        )
