"""User entity — underwriters, credit officers, loan owners.

Lightweight by design: enough to attach an identity to a loan, drive the
mockup's "owner" display, and give us a place to plug real auth later.
The dev-token auth layer (`mkopo.routers.auth.require_user`) doesn't yet
hit this table — it returns a fixed identity — but the data model is ready.
"""

from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="underwriter")

    @property
    def initials(self) -> str:
        parts = self.name.split()
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()
