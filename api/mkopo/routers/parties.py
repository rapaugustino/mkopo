"""Party endpoints — the data layer behind the entity graph inspector."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.services.concentration import party_profile

router = APIRouter(prefix="/parties", tags=["parties"])


class LoanRefOut(BaseModel):
    """Minimal loan shape for the inspector's graph + loan list — full LoanOut
    would drag in the parties relationships unnecessarily."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    stage: str
    loan_type: str
    amount: Decimal
    risk_band: str | None


class RelatedPartyOut(BaseModel):
    party_id: uuid.UUID
    name: str
    role: str
    shared_loan_count: int
    shared_exposure: Decimal


class PartyProfileOut(BaseModel):
    party_id: uuid.UUID
    name: str
    party_type: str
    email: str | None
    role: str
    active_exposure: Decimal
    active_loans: list[LoanRefOut]
    delinquencies: int
    policy_limit: Decimal
    related_parties: list[RelatedPartyOut]


@router.get("/{party_id}/profile", response_model=PartyProfileOut)
async def get_party_profile(
    party_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> PartyProfileOut:
    """Everything the entity inspector page needs.

    Returns 404 if the party doesn't exist.
    """
    profile = await party_profile(db, party_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    return PartyProfileOut(
        party_id=profile.party_id,
        name=profile.name,
        party_type=profile.party_type,
        email=profile.email,
        role=profile.role,
        active_exposure=profile.active_exposure,
        active_loans=[LoanRefOut.model_validate(loan) for loan in profile.active_loans],
        delinquencies=profile.delinquencies,
        policy_limit=profile.policy_limit,
        related_parties=[
            RelatedPartyOut(
                party_id=r.party_id,
                name=r.name,
                role=r.role,
                shared_loan_count=r.shared_loan_count,
                shared_exposure=r.shared_exposure,
            )
            for r in profile.related_parties
        ],
    )
