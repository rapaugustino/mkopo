"""Document field extraction. The shared tool every agent uses for OCR + extraction."""

from __future__ import annotations

import uuid

import structlog
from pydantic import BaseModel, Field

from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway

logger = structlog.get_logger()


class SourceSpan(BaseModel):
    """Where in the source document a value was found."""

    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    quote: str = ""


class ExtractedField(BaseModel):
    """A single field extracted from a document."""

    field_name: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: SourceSpan


class ExtractionResult(BaseModel):
    """The structured output of a document extraction."""

    document_id: uuid.UUID | None = None
    fields: list[ExtractedField]


# Confidence thresholds per field. Below these, the extraction goes to
# human review. These live as data so they can be tuned without code
# changes. Thresholds reflect cost-of-error vs ambiguity:
#
#   - entity / loan amount: high-stakes literals, tight floor
#   - financials (NOI / appraised value): mostly literals but currency
#     formatting drift is real, slightly looser
#   - classifications (property_type) are inherently fuzzy — looser
#     threshold, expect more review queue volume but acceptable
FIELD_THRESHOLDS: dict[str, float] = {
    "borrower_entity": 0.95,
    "property_address": 0.93,
    "property_type": 0.85,  # classification, allow more variance
    "guarantor_list": 0.90,
    "annual_noi": 0.92,
    "appraised_value": 0.92,
    "appraisal_date": 0.85,
    "loan_amount": 0.95,
    "ltv": 0.93,
    "dscr": 0.92,
    "_default": 0.90,
}


def threshold_for(field_name: str) -> float:
    return FIELD_THRESHOLDS.get(field_name, FIELD_THRESHOLDS["_default"])


SYSTEM_PROMPT = """You are an expert loan document analyst.

Extract the requested fields from this document. For each field:
- value: the extracted value as a string (numbers as digits with no commas or symbols)
- confidence: your confidence (0.0–1.0). Be honest — lower confidence on inferred or fuzzy values.
- source_span: include the exact verbatim quote where you found the value (max 200 chars)

If a field is not present in the document, omit it from the response. Do not invent values."""


async def extract_fields(
    *,
    document_text: str,
    fields_to_extract: list[str],
    document_id: uuid.UUID | None = None,
    model: str | None = None,
) -> ExtractionResult:
    """Extract a list of named fields from a document."""
    settings = get_settings()
    gateway = get_gateway()
    user = (
        f"Document:\n```\n{document_text}\n```\n\nFields to extract: {', '.join(fields_to_extract)}"
    )
    result = await gateway.call_structured(
        model=model or settings.llm_default_model,
        system=SYSTEM_PROMPT,
        user=user,
        schema=ExtractionResult,
    )
    result.document_id = document_id
    logger.info(
        "extraction_complete",
        document_id=str(document_id) if document_id else None,
        n_fields=len(result.fields),
        low_confidence=sum(1 for f in result.fields if f.confidence < threshold_for(f.field_name)),
    )
    return result


def routing_decision(field: ExtractedField) -> str:
    """Return 'accepted' if confidence is above threshold, else 'queued_for_review'."""
    return (
        "accepted" if field.confidence >= threshold_for(field.field_name) else "queued_for_review"
    )
