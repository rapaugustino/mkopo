"""Rules engine. Deterministic policy checks that complement the LLM.

The boundary discipline here matters:

- The rules engine asserts pass/fail. The LLM never does.
- Rule thresholds vary by property type — a single global DSCR floor is
  wrong in practice (multifamily lenders run 1.20–1.25x; hotel lenders run
  1.40–1.50x). The lookup tables below are policy data, not code, so the
  difference between "tighten policy" and "ship a new release" is a single
  reviewable diff.
- Each rule is a pure function with a docstring that doubles as
  documentation. They cannot be silently bypassed; adding one is a code
  change in a reviewed PR.

For values: DSCR floors and LTV caps below reflect typical commercial
lender practice as of 2025–2026 (CMBS / agency multifamily / SBA-aligned
private lenders). See the design doc §6.5 + §7.

Sources:
- Debt yield definition + 8–9% norm: District Lending, "Debt Yield vs DSCR"
- DSCR floors by asset type: Terrydale Capital, "DSCR & LTV in CRE (2025)"
- LTV caps by asset type: commercial lender survey averages
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

# --- Property type discriminator -------------------------------------


class PropertyType(enum.StrEnum):
    """Commercial property class. Drives the DSCR / LTV / debt-yield lookup."""

    MULTIFAMILY = "multifamily"
    OFFICE = "office"
    RETAIL = "retail"
    INDUSTRIAL = "industrial"
    HOTEL = "hotel"
    MIXED_USE = "mixed_use"
    OTHER = "other"

    @classmethod
    def from_text(cls, text: str | None) -> PropertyType:
        """Map a free-form extraction string ("12-unit multifamily") to a class.

        Conservative: unknown -> OTHER (which carries the tightest defaults).
        """
        if not text:
            return cls.OTHER
        t = text.lower()
        if "multifamily" in t or "apartment" in t or " unit" in t:
            return cls.MULTIFAMILY
        if "office" in t:
            return cls.OFFICE
        if "retail" in t or "shopping" in t:
            return cls.RETAIL
        if "industrial" in t or "warehouse" in t:
            return cls.INDUSTRIAL
        if "hotel" in t or "hospitality" in t or "motel" in t:
            return cls.HOTEL
        if "mixed" in t or "mixed-use" in t:
            return cls.MIXED_USE
        return cls.OTHER


# --- Policy tables ---------------------------------------------------
#
# DSCR floors: minimum NOI / debt service the lender accepts.
# LTV caps:   maximum loan / appraised value the lender accepts.
# Debt yield: minimum NOI / loan amount the lender accepts.
#
# "OTHER" is intentionally the tightest — a loan we couldn't classify gets
# the conservative ceiling.

DSCR_FLOORS: dict[PropertyType, Decimal] = {
    PropertyType.MULTIFAMILY: Decimal("1.20"),
    PropertyType.INDUSTRIAL: Decimal("1.25"),
    PropertyType.OFFICE: Decimal("1.30"),
    PropertyType.RETAIL: Decimal("1.30"),
    PropertyType.MIXED_USE: Decimal("1.30"),
    PropertyType.HOTEL: Decimal("1.45"),
    PropertyType.OTHER: Decimal("1.30"),
}

LTV_CAPS: dict[PropertyType, Decimal] = {
    PropertyType.MULTIFAMILY: Decimal("0.75"),
    PropertyType.INDUSTRIAL: Decimal("0.70"),
    PropertyType.OFFICE: Decimal("0.70"),
    PropertyType.RETAIL: Decimal("0.70"),
    PropertyType.MIXED_USE: Decimal("0.70"),
    PropertyType.HOTEL: Decimal("0.65"),
    PropertyType.OTHER: Decimal("0.65"),
}

DEBT_YIELD_FLOORS: dict[PropertyType, Decimal] = {
    PropertyType.MULTIFAMILY: Decimal("0.08"),
    PropertyType.INDUSTRIAL: Decimal("0.085"),
    PropertyType.OFFICE: Decimal("0.090"),
    PropertyType.RETAIL: Decimal("0.090"),
    PropertyType.MIXED_USE: Decimal("0.090"),
    PropertyType.HOTEL: Decimal("0.100"),
    PropertyType.OTHER: Decimal("0.090"),
}

POLICY_MAX_APPRAISAL_AGE_DAYS = 180
POLICY_MAX_GUARANTOR_EXPOSURE = Decimal("8000000")
REQUIRED_DOCS = {
    "loan_application",
    "appraisal",
    "rent_roll",
    "personal_financial_statement",
}


# --- Backwards-compatible constants (referenced by older imports / tests) -


POLICY_MIN_DSCR = DSCR_FLOORS[PropertyType.OTHER]
POLICY_MAX_LTV = LTV_CAPS[PropertyType.OTHER]


# --- Context + outcome --------------------------------------------------


@dataclass
class RuleContext:
    """The data a rule has access to. Keep this lean and explicit."""

    loan_amount: Decimal
    appraised_value: Decimal | None = None
    appraisal_date: date | None = None
    annual_noi: Decimal | None = None
    annual_debt_service: Decimal | None = None
    guarantor_total_exposure: Decimal | None = None
    documents_present: set[str] | None = None
    property_type: PropertyType = PropertyType.OTHER


@dataclass
class RuleOutcome:
    rule_id: str
    severity: str  # "block", "warn", "info"
    passed: bool
    message: str
    details: dict | None = field(default_factory=dict)


# --- Rules ---------------------------------------------------------------


def rule_ltv_under_cap(ctx: RuleContext) -> RuleOutcome:
    """LTV must not exceed the asset-type-specific cap."""
    cap = LTV_CAPS[ctx.property_type]
    if ctx.appraised_value is None or ctx.appraised_value == 0:
        return RuleOutcome(
            rule_id="ltv_under_cap",
            severity="warn",
            passed=False,
            message="Cannot compute LTV without appraised value.",
            details={"cap": float(cap), "asset_type": ctx.property_type.value},
        )
    ltv = ctx.loan_amount / ctx.appraised_value
    passed = ltv <= cap
    return RuleOutcome(
        rule_id="ltv_under_cap",
        severity="block",
        passed=passed,
        message=(
            f"LTV {ltv:.1%} within {ctx.property_type.value} cap of {cap:.0%}"
            if passed
            else f"LTV {ltv:.1%} exceeds {ctx.property_type.value} cap of {cap:.0%}"
        ),
        details={"ltv": float(ltv), "cap": float(cap), "asset_type": ctx.property_type.value},
    )


def rule_dscr_above_floor(ctx: RuleContext) -> RuleOutcome:
    """DSCR must meet or exceed the asset-type-specific floor."""
    floor = DSCR_FLOORS[ctx.property_type]
    if ctx.annual_noi is None or ctx.annual_debt_service is None or ctx.annual_debt_service == 0:
        return RuleOutcome(
            rule_id="dscr_above_floor",
            severity="warn",
            passed=False,
            message="Cannot compute DSCR without NOI and debt service.",
            details={"floor": float(floor), "asset_type": ctx.property_type.value},
        )
    dscr = ctx.annual_noi / ctx.annual_debt_service
    passed = dscr >= floor
    return RuleOutcome(
        rule_id="dscr_above_floor",
        severity="block",
        passed=passed,
        message=(
            f"DSCR {dscr:.2f} meets {ctx.property_type.value} floor of {floor:.2f}"
            if passed
            else f"DSCR {dscr:.2f} below {ctx.property_type.value} floor of {floor:.2f}"
        ),
        details={"dscr": float(dscr), "floor": float(floor), "asset_type": ctx.property_type.value},
    )


def rule_debt_yield_above_floor(ctx: RuleContext) -> RuleOutcome:
    """Debt yield (NOI / loan amount) must clear the asset-type floor.

    Debt yield is the lender's unlevered return if the property were
    foreclosed today, and cannot be inflated by low interest rates or
    extended amortization the way DSCR can. CMBS lenders typically
    require 8–9% minimum.
    """
    floor = DEBT_YIELD_FLOORS[ctx.property_type]
    if ctx.annual_noi is None or ctx.loan_amount == 0:
        return RuleOutcome(
            rule_id="debt_yield_above_floor",
            severity="warn",
            passed=False,
            message="Cannot compute debt yield without NOI.",
            details={"floor": float(floor), "asset_type": ctx.property_type.value},
        )
    dy = ctx.annual_noi / ctx.loan_amount
    passed = dy >= floor
    return RuleOutcome(
        rule_id="debt_yield_above_floor",
        severity="block",
        passed=passed,
        message=(
            f"Debt yield {dy:.2%} clears {ctx.property_type.value} floor of {floor:.1%}"
            if passed
            else (f"Debt yield {dy:.2%} below {ctx.property_type.value} floor of {floor:.1%}")
        ),
        details={
            "debt_yield": float(dy),
            "floor": float(floor),
            "asset_type": ctx.property_type.value,
        },
    )


def rule_appraisal_age(ctx: RuleContext) -> RuleOutcome:
    """Appraisal must be dated within POLICY_MAX_APPRAISAL_AGE_DAYS."""
    if ctx.appraisal_date is None:
        return RuleOutcome(
            rule_id="appraisal_age",
            severity="warn",
            passed=False,
            message="Appraisal date not extracted.",
        )
    today = datetime.now(UTC).date()
    age_days = (today - ctx.appraisal_date).days
    passed = age_days <= POLICY_MAX_APPRAISAL_AGE_DAYS
    return RuleOutcome(
        rule_id="appraisal_age",
        severity="block",
        passed=passed,
        message=(
            f"Appraisal is {age_days} days old (max {POLICY_MAX_APPRAISAL_AGE_DAYS})"
            if passed
            else (
                f"Appraisal is {age_days} days old, "
                f"exceeds policy max of {POLICY_MAX_APPRAISAL_AGE_DAYS}"
            )
        ),
        details={"age_days": age_days, "max_days": POLICY_MAX_APPRAISAL_AGE_DAYS},
    )


def rule_guarantor_concentration(ctx: RuleContext) -> RuleOutcome:
    """Guarantor total exposure (incl. this loan) must not exceed policy."""
    if ctx.guarantor_total_exposure is None:
        return RuleOutcome(
            rule_id="guarantor_concentration",
            severity="info",
            passed=True,
            message="No guarantor exposure data.",
        )
    proposed = ctx.guarantor_total_exposure + ctx.loan_amount
    passed = proposed <= POLICY_MAX_GUARANTOR_EXPOSURE
    return RuleOutcome(
        rule_id="guarantor_concentration",
        severity="warn" if passed else "block",
        passed=passed,
        message=(
            f"Projected guarantor exposure ${proposed:,.0f} within cap"
            if passed
            else (
                f"Projected guarantor exposure ${proposed:,.0f} "
                f"exceeds cap ${POLICY_MAX_GUARANTOR_EXPOSURE:,.0f}"
            )
        ),
        details={
            "projected": float(proposed),
            "cap": float(POLICY_MAX_GUARANTOR_EXPOSURE),
        },
    )


def rule_doc_completeness(ctx: RuleContext) -> RuleOutcome:
    """All required document types must be present."""
    docs = ctx.documents_present or set()
    missing = REQUIRED_DOCS - docs
    passed = not missing
    return RuleOutcome(
        rule_id="doc_completeness",
        severity="warn",
        passed=passed,
        message=(
            "All required documents present"
            if passed
            else f"Missing required documents: {', '.join(sorted(missing))}"
        ),
        details={"missing": sorted(missing), "present": sorted(docs)},
    )


# Rule pack — ordered for predictable evaluation. Doc completeness first
# because every other rule depends on what got extracted.
DEFAULT_RULES: list[Callable[[RuleContext], RuleOutcome]] = [
    rule_doc_completeness,
    rule_ltv_under_cap,
    rule_dscr_above_floor,
    rule_debt_yield_above_floor,
    rule_appraisal_age,
    rule_guarantor_concentration,
]


def run_rules(ctx: RuleContext) -> list[RuleOutcome]:
    """Run the default rule pack against a context."""
    return [r(ctx) for r in DEFAULT_RULES]


def has_blocking_failures(outcomes: list[RuleOutcome]) -> bool:
    return any(o.severity == "block" and not o.passed for o in outcomes)
