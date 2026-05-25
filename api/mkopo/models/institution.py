"""Institution settings — single-row config table.

Holds the lender's contact identity (name, address, phone, email),
the credit authority whose name signs decision letters (authorized
officer), and the consumer reporting agency triple that ECOA Reg B
requires the adverse-action letter to disclose.

Every agent that drafts a borrower-visible artifact reads from here:

- ``agents/intake.py``'s doc-request email uses lender name +
  sign-off + authorized officer.
- ``agents/decision.py``'s adverse-action letter uses every field
  (lender contact + credit reporting agency + authorized officer).
- ``agents/decision.py``'s term sheet uses lender name + address.

The pattern is "one row per database, always the same UUID, always
upsert". ``services.institution.get_or_create()`` handles the read
path and returns ``None``-valued fields when they haven't been
configured yet — prompts treat null as "skip the clause" rather
than fabricating data.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base

# Fixed UUID used by the service-layer accessor so we never have to
# query first to know the singleton's id. Matches the value the
# 0020 migration seeded.
SINGLETON_ID: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class InstitutionSettings(Base):
    """One row per database. Edited via the staff settings page."""

    __tablename__ = "institution_settings"

    # Lender identity — borrower-visible on every outbound artifact.
    lender_name: Mapped[str | None] = mapped_column()
    lender_address: Mapped[str | None] = mapped_column()
    lender_phone: Mapped[str | None] = mapped_column()
    lender_email: Mapped[str | None] = mapped_column()

    # The credit authority whose signature anchors a decision letter.
    # In a small lender this is one person; in a larger shop it's a
    # designated committee chair. Distinct from the loan owner — the
    # owner reviews day-to-day; the authorized officer's name appears
    # at the bottom of legal communications.
    authorized_officer_name: Mapped[str | None] = mapped_column()
    authorized_officer_title: Mapped[str | None] = mapped_column()

    # ECOA Reg B § 1002.9(b)(2) — adverse-action letters where a
    # consumer report was used must disclose the agency name +
    # address + a toll-free phone the borrower can call. We store
    # all three so the prompt can spell out the full clause; if
    # they're left blank the prompt is instructed to omit the
    # entire credit-reporting paragraph (Reg B only requires the
    # disclosure *when* a report was actually consulted).
    credit_reporting_agency_name: Mapped[str | None] = mapped_column()
    credit_reporting_agency_address: Mapped[str | None] = mapped_column()
    credit_reporting_agency_phone: Mapped[str | None] = mapped_column()

    # ``created_at`` + ``updated_at`` inherited from Base — same
    # ``onupdate=func.now()`` pattern every other table uses.
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
