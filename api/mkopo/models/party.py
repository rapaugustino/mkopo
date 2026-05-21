"""Party entity - persons and legal entities."""

import enum
import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mkopo.models.base import Base
from mkopo.models.loan import Loan


class PartyType(enum.StrEnum):
    PERSON = "person"
    ENTITY = "entity"


class PartyRole(enum.StrEnum):
    BORROWER = "borrower"
    GUARANTOR = "guarantor"
    SPONSOR = "sponsor"
    BROKER = "broker"
    CO_BORROWER = "co_borrower"


class Party(Base):
    __tablename__ = "parties"

    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    party_type: Mapped[PartyType] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256), index=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    loans: Mapped[list["LoanParty"]] = relationship(back_populates="party")


class LoanParty(Base):
    __tablename__ = "loan_parties"
    __table_args__ = (UniqueConstraint("loan_id", "party_id", "role", name="uq_loan_party_role"),)

    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[PartyRole] = mapped_column(String(32), nullable=False, index=True)

    loan: Mapped["Loan"] = relationship(back_populates="parties")
    # `lazy="joined"` so accessing `loan_party.party` in an async session
    # doesn't trigger a fresh await — every LoanParty load comes with its
    # Party already attached. Combined with Loan.parties lazy="selectin",
    # this gives us a single round-trip from `select(Loan)` to fully-loaded
    # borrowers + guarantors.
    party: Mapped["Party"] = relationship(back_populates="loans", lazy="joined")
