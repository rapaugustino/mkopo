"""Shared rules-engine evaluation for agents.

The underwriting and decision agents both need to: load a loan's accepted
extractions, build the typed `RuleContext`, and run the rules engine.
Without a shared helper they'd drift — and that's the worst kind of drift
because pass/fail outcomes would differ between agents looking at the
same data. Centralise here.

Returns both the typed flags (the rules engine's outputs) AND the
context that produced them, so callers can compute KPIs without
re-parsing extraction strings.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import Document, Extraction, ExtractionStatus, Loan
from mkopo.rules.policy import PropertyType, RuleContext, run_rules
from mkopo.schemas import RiskFlag
from mkopo.services.concentration import guarantor_exposure_for_loan


@dataclass
class EvaluationResult:
    loan: Loan
    extractions: dict[str, str]
    confidences: dict[str, float]
    ctx: RuleContext
    flags: list[RiskFlag]


def _to_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "").replace("$", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _to_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _debt_service_proxy(loan: Loan) -> Decimal | None:
    """Stand-in interest-only debt service until the decision agent writes a real term sheet.

    Picks a rate roughly aligned to 2025–2026 market conditions. The
    decision agent will produce real terms; until then both underwriting
    and decision use the same proxy so DSCR is consistent across agents.
    """
    if loan.loan_type.value == "bridge":
        return (loan.amount * Decimal("0.06")).quantize(Decimal("1"))
    if loan.loan_type.value in ("permanent", "refinance"):
        return (loan.amount * Decimal("0.07")).quantize(Decimal("1"))
    return None


async def fetch_accepted_extractions(
    session: AsyncSession, loan_id: uuid.UUID
) -> tuple[dict[str, str], dict[str, float]]:
    """Latest accepted extraction per field, plus its confidence."""
    extractions: dict[str, str] = {}
    confidences: dict[str, float] = {}
    stmt = (
        select(Extraction)
        .join(Document)
        .where(
            Document.loan_id == loan_id,
            Extraction.status == ExtractionStatus.ACCEPTED,
        )
        .order_by(Extraction.confidence.desc(), Extraction.created_at.desc())
    )
    for row in (await session.execute(stmt)).scalars().all():
        if row.field_name not in extractions:
            extractions[row.field_name] = row.value
            confidences[row.field_name] = row.confidence
    return extractions, confidences


async def evaluate(session: AsyncSession, loan_id: uuid.UUID) -> EvaluationResult:
    """Run the rules engine against this loan's current accepted extractions.

    Agents call this rather than reading another agent's persisted output,
    so the rules engine remains the single source of truth across the system.
    """
    loan = (await session.execute(select(Loan).where(Loan.id == loan_id))).scalar_one()
    extractions, confidences = await fetch_accepted_extractions(session, loan_id)

    # `doc_type` is declared `Mapped[DocumentType]` but the SQL column is
    # `String(64)`, so SQLAlchemy returns plain strings on load — there's
    # no `.value` to call. The comparison set in REQUIRED_DOCS is also
    # plain strings, so just collect them.
    docs_present_q = select(Document.doc_type).where(Document.loan_id == loan_id)
    present = set((await session.execute(docs_present_q)).scalars().all())

    appraisal_date = (
        _to_date(extractions["appraisal_date"]) if "appraisal_date" in extractions else None
    )
    annual_noi = _to_decimal(extractions["annual_noi"]) if "annual_noi" in extractions else None
    appraised_value = _to_decimal(extractions.get("appraised_value", ""))
    property_type = PropertyType.from_text(extractions.get("property_type"))

    guarantor_exposure = await guarantor_exposure_for_loan(session, loan_id)

    ctx = RuleContext(
        loan_amount=loan.amount,
        appraised_value=appraised_value,
        appraisal_date=appraisal_date,
        annual_noi=annual_noi,
        annual_debt_service=_debt_service_proxy(loan),
        guarantor_total_exposure=guarantor_exposure,
        documents_present=present,
        property_type=property_type,
    )
    outcomes = run_rules(ctx)
    flags = [
        RiskFlag(
            rule_id=o.rule_id,
            severity=o.severity,  # type: ignore[arg-type]
            passed=o.passed,
            message=o.message,
            details=o.details or {},
        )
        for o in outcomes
    ]
    return EvaluationResult(
        loan=loan,
        extractions=extractions,
        confidences=confidences,
        ctx=ctx,
        flags=flags,
    )
