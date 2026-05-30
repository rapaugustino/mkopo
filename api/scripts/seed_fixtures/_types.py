"""Shared dataclasses for seed fixtures.

Kept tiny so fixture modules import only what they need:
``from scripts.seed_fixtures._types import SeedDoc, SeedParty, SeedLoan``.
The actual database models live in ``mkopo.models`` — these are
fixture-shape carriers, not persistence types.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from mkopo.models import (
    DocumentType,
    LoanClass,
    LoanStage,
    LoanType,
    PartyRole,
    PartyType,
)


@dataclass(frozen=True)
class SeedDoc:
    filename: str
    doc_type: DocumentType
    text: str


@dataclass(frozen=True)
class SeedParty:
    name: str
    party_type: PartyType
    role: PartyRole
    email: str | None = None


@dataclass(frozen=True)
class SeedLoan:
    loan_type: LoanType
    amount_usd: Decimal
    borrower_email: str
    parties: list[SeedParty]
    documents: list[SeedDoc]
    # Default new loans land in INTAKE (the natural lifecycle entry
    # point). Some fixtures override this to UNDERWRITING so a fresh
    # `seed` gives the pipeline a realistic mix of stages without the
    # demo'er having to manually advance each one.
    starting_stage: LoanStage = LoanStage.INTAKE
    # Top-level product class. Most fixtures are commercial real-estate
    # deals (BUSINESS) — the personal-loan fixture overrides this and
    # also supplies the income / DTI / FICO / tenure meta so the
    # personal rule pack has values to evaluate against. None means
    # "fall through to the Loan model's default", which is BUSINESS.
    loan_class: LoanClass | None = None
    # Free-form loan metadata. The borrower portal writes
    # ``annual_income`` / ``monthly_debt_payments`` / ``credit_score`` /
    # ``years_employment`` here for personal loans (see
    # services/rules_eval.py); seed fixtures mirror that so the
    # rules engine can run against seeded data without a borrower
    # round-trip. ``borrower_email`` is always written; this dict is
    # merged on top.
    meta_extra: dict | None = None
