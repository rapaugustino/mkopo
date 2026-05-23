"""User entity — staff (underwriters, credit officers, admins) and
borrowers (self-service applicants).

One table, ``role`` discriminates. Staff roles inherited from the
mockup are ``"underwriter"`` and ``"admin"``; borrowers carry
``"borrower"``. Future RBAC expansion (``"loan_officer"``,
``"committee_chair"``) is just new string values — no schema change.

Credential columns (``password_hash``, ``email_verified_at``) were
added by migration ``0012_user_auth``. Both nullable because not
every user authenticates by password — magic-link-only borrowers
keep ``password_hash=None`` permanently, and unverified emails are
a real state we want to distinguish from "verified".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="underwriter")
    # Bcrypt hash (cost 12). ``None`` for magic-link-only users.
    password_hash: Mapped[str | None] = mapped_column(String(256))
    # Filled in when the user clicks an ``email_verify`` magic-link.
    # ``None`` ⇒ the email is on file but not yet confirmed.
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Soft-delete marker for borrower erasure. Hard-delete happens
    # via the retention sweep once all their loans' retention windows
    # have expired. ``NULL`` ⇒ active.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def initials(self) -> str:
        parts = self.name.split()
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    @property
    def is_borrower(self) -> bool:
        return self.role == "borrower"

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None
