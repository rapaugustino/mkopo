"""Concentration analysis.

DESIGN §5.3 calls the recursive guarantor → loan → property traversal the
core of risk visibility: "this single query powers the risk flag on the
underwriting workspace, the entity inspector view, and the committee
escalation rule in the decision agent."

This module owns that query in one place so all three callers stay in
sync. Indexes that make it cheap live on `loan_parties (party_id, role)`
and `loans (stage)`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import Loan, LoanParty, LoanStage, Party, PartyRole

# Stages we consider "active" for concentration math. Excludes intake
# (deal not committed) and the terminal states.
ACTIVE_STAGES: tuple[LoanStage, ...] = (
    LoanStage.UNDERWRITING,
    LoanStage.DECISION,
    LoanStage.CONDITIONS,
    LoanStage.CLOSING,
    LoanStage.SERVICING,
    LoanStage.APPROVED,
)


@dataclass(frozen=True)
class GuarantorExposure:
    """Per-guarantor concentration row, suitable for direct JSON serialisation."""

    party_id: uuid.UUID
    name: str
    total_exposure: Decimal
    loan_count: int


async def guarantor_exposure_for_loan(
    session: AsyncSession,
    loan_id: uuid.UUID,
) -> Decimal | None:
    """Sum of active-loan amounts across all guarantors on `loan_id`,
    EXCLUDING the loan itself.

    Returns None if the loan has no guarantors. Used by the rules engine
    (`rule_guarantor_concentration`) and the entity inspector.
    """
    guarantors_q = select(LoanParty.party_id).where(
        LoanParty.loan_id == loan_id,
        LoanParty.role == PartyRole.GUARANTOR,
    )
    guarantor_ids = (await session.execute(guarantors_q)).scalars().all()
    if not guarantor_ids:
        return None

    stmt = (
        select(func.coalesce(func.sum(Loan.amount), 0))
        .join(LoanParty, LoanParty.loan_id == Loan.id)
        .where(
            LoanParty.party_id.in_(guarantor_ids),
            LoanParty.role == PartyRole.GUARANTOR,
            Loan.stage.in_(ACTIVE_STAGES),
            Loan.id != loan_id,
        )
    )
    total = (await session.execute(stmt)).scalar_one()
    return Decimal(total)


async def guarantor_concentration(
    session: AsyncSession,
    threshold: Decimal = Decimal("0"),
) -> list[GuarantorExposure]:
    """All guarantors with total active-loan exposure above `threshold`.

    This is the §5.3 query. Returns a typed list ordered by exposure DESC.
    Pass `threshold=POLICY_MAX_GUARANTOR_EXPOSURE` to surface only
    over-limit cases for committee escalation.
    """
    stmt = (
        select(
            Party.id,
            Party.name,
            func.sum(Loan.amount).label("total_exposure"),
            func.count(Loan.id).label("loan_count"),
        )
        .join(LoanParty, LoanParty.party_id == Party.id)
        .join(Loan, Loan.id == LoanParty.loan_id)
        .where(
            LoanParty.role == PartyRole.GUARANTOR,
            Loan.stage.in_(ACTIVE_STAGES),
        )
        .group_by(Party.id, Party.name)
        .having(func.sum(Loan.amount) > threshold)
        .order_by(func.sum(Loan.amount).desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        GuarantorExposure(
            party_id=row.id,
            name=row.name,
            total_exposure=Decimal(row.total_exposure),
            loan_count=int(row.loan_count),
        )
        for row in rows
    ]


async def loans_for_guarantor(
    session: AsyncSession,
    party_id: uuid.UUID,
    include_inactive: bool = False,
) -> list[Loan]:
    """All loans where `party_id` is a guarantor. Used by the entity inspector."""
    stmt = (
        select(Loan)
        .join(LoanParty, LoanParty.loan_id == Loan.id)
        .where(
            LoanParty.party_id == party_id,
            LoanParty.role == PartyRole.GUARANTOR,
        )
        .order_by(Loan.created_at.desc())
    )
    if not include_inactive:
        stmt = stmt.where(Loan.stage.in_(ACTIVE_STAGES))
    return list((await session.execute(stmt)).scalars().all())


# --- Entity inspector ---


@dataclass(frozen=True)
class RelatedParty:
    """A co-guarantor who appears on at least one of `party_id`'s active loans."""

    party_id: uuid.UUID
    name: str
    role: str
    shared_loan_count: int
    shared_exposure: Decimal


@dataclass(frozen=True)
class PartyProfile:
    """Everything the entity inspector page needs in one query bundle."""

    party_id: uuid.UUID
    name: str
    party_type: str
    email: str | None
    role: str  # primary role across loans, e.g. "guarantor" / "borrower"
    active_exposure: Decimal
    active_loans: list[Loan]
    delinquencies: int  # placeholder — no payment data in the portfolio scope
    policy_limit: Decimal
    related_parties: list[RelatedParty]


async def party_profile(
    session: AsyncSession,
    party_id: uuid.UUID,
) -> PartyProfile | None:
    """Aggregate everything the entity inspector page needs about a party."""
    from mkopo.rules.policy import POLICY_MAX_GUARANTOR_EXPOSURE

    party = (await session.execute(select(Party).where(Party.id == party_id))).scalar_one_or_none()
    if not party:
        return None

    # Primary role: pick whichever role this party has the most of across
    # loans, with `guarantor` winning ties because that's the
    # concentration-bearing role we surface in the UI.
    role_q = (
        select(LoanParty.role, func.count(LoanParty.role))
        .where(LoanParty.party_id == party_id)
        .group_by(LoanParty.role)
    )
    role_counts = (await session.execute(role_q)).all()
    if role_counts:
        role_counts.sort(
            key=lambda r: (r[1], 1 if r[0] == PartyRole.GUARANTOR else 0),
            reverse=True,
        )
        primary_role = (
            role_counts[0][0].value
            if hasattr(role_counts[0][0], "value")
            else str(role_counts[0][0])
        )
    else:
        primary_role = "—"

    active_loans = await loans_for_guarantor(session, party_id, include_inactive=False)
    active_exposure = sum((loan.amount for loan in active_loans), Decimal(0))

    # Related parties: other guarantors who appear on at least one of
    # `party_id`'s active loans. One query — join LoanParty to itself.
    if active_loans:
        loan_ids = [loan.id for loan in active_loans]
        related_q = (
            select(
                Party.id,
                Party.name,
                LoanParty.role,
                func.count(func.distinct(LoanParty.loan_id)).label("shared_loan_count"),
                func.sum(Loan.amount).label("shared_exposure"),
            )
            .join(LoanParty, LoanParty.party_id == Party.id)
            .join(Loan, Loan.id == LoanParty.loan_id)
            .where(
                and_(
                    LoanParty.loan_id.in_(loan_ids),
                    LoanParty.party_id != party_id,
                )
            )
            .group_by(Party.id, Party.name, LoanParty.role)
            .order_by(func.sum(Loan.amount).desc())
        )
        related_rows = (await session.execute(related_q)).all()
        related = [
            RelatedParty(
                party_id=row.id,
                name=row.name,
                role=row.role.value if hasattr(row.role, "value") else str(row.role),
                shared_loan_count=int(row.shared_loan_count),
                shared_exposure=Decimal(row.shared_exposure or 0),
            )
            for row in related_rows
        ]
    else:
        related = []

    return PartyProfile(
        party_id=party.id,
        name=party.name,
        party_type=party.party_type
        if isinstance(party.party_type, str)
        else party.party_type.value,
        email=party.email,
        role=primary_role,
        active_exposure=active_exposure,
        active_loans=active_loans,
        delinquencies=0,  # portfolio scope: no payment data
        policy_limit=POLICY_MAX_GUARANTOR_EXPOSURE,
        related_parties=related,
    )
