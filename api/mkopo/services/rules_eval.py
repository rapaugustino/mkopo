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
from mkopo.rules.policy import PropertyType, RuleContext, run_rules_for
from mkopo.schemas import RiskFlag
from mkopo.services.concentration import guarantor_exposure_for_loan


@dataclass
class EvaluationResult:
    loan: Loan
    extractions: dict[str, str]
    confidences: dict[str, float]
    ctx: RuleContext
    flags: list[RiskFlag]


# Friendly labels paired with each rule_id so the LLM has natural
# prose to anchor on when drafting adverse-action letters. The
# rule_id is the engine's stable identifier (kept in
# ``principal_reasons`` for audit) and is intentionally code-shaped;
# the label below is what the borrower should ever see written in
# any letter body. Mirrors the frontend ``humanizeRuleId`` table.
_RULE_LABELS: dict[str, str] = {
    "ltv_under_cap": "loan-to-value ratio",
    "dscr_above_floor": "debt-service coverage",
    "debt_yield_above_floor": "debt yield",
    "appraisal_age": "appraisal age",
    "doc_completeness": "documentation completeness",
    "guarantor_concentration": "guarantor concentration",
    "credit_score_floor": "credit score",
    "dti_under_cap": "debt-to-income ratio",
    "lti_under_cap": "loan-to-income ratio",
    "employment_tenure_minimum": "employment tenure",
    "income_minimum": "minimum income",
}


def friendly_rule_label(rule_id: str) -> str:
    """Borrower-facing label for a rule id.

    Used in the decision agent's prompt context so the LLM has
    natural language to anchor the letter prose on. The raw rule_id
    still lives in ``principal_reasons`` for audit traceability;
    only the letter body uses this label. Falls through to
    title-cased underscores for unknown ids — same fallback the
    frontend uses.
    """
    return _RULE_LABELS.get(rule_id) or rule_id.replace("_", " ")


def _to_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "").replace("$", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def _to_int(value: object) -> int | None:
    """Coerce ``meta``-sourced ints (JSON numbers come back as int/float,
    strings if hand-edited) into a clean ``int``. Returns ``None`` on
    anything unparseable so the personal rules emit a "warn" outcome
    rather than blow up."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):  # ``isinstance(True, int)`` is True — guard.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
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

    # Personal-loan inputs land in ``loan.meta`` (the borrower portal
    # writes them there; see routers/borrower_portal.py). The commercial
    # extraction schema doesn't model income/credit-score fields, so
    # meta is the canonical source for the personal rule pack. Reads
    # are guarded — missing values fall through to ``None`` and the
    # rules emit "warn" outcomes rather than crashing.
    meta = loan.meta or {}
    annual_income = _to_decimal(str(meta.get("annual_income", "")))
    monthly_debt_payments = _to_decimal(str(meta.get("monthly_debt_payments", "")))
    credit_score = _to_int(meta.get("credit_score"))
    years_employment = _to_decimal(str(meta.get("years_employment", "")))

    ctx = RuleContext(
        loan_amount=loan.amount,
        appraised_value=appraised_value,
        appraisal_date=appraisal_date,
        annual_noi=annual_noi,
        annual_debt_service=_debt_service_proxy(loan),
        guarantor_total_exposure=guarantor_exposure,
        documents_present=present,
        property_type=property_type,
        annual_income=annual_income,
        monthly_debt_payments=monthly_debt_payments,
        credit_score=credit_score,
        years_employment=years_employment,
    )
    # Wire-value of LoanClass — ``.value`` is the string the rules
    # module expects ("business" / "personal").
    loan_class_value = (
        loan.loan_class.value if hasattr(loan.loan_class, "value") else str(loan.loan_class)
    )
    outcomes = run_rules_for(loan_class_value, ctx)
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
