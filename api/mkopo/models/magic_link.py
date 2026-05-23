"""Magic-link ORM — single-use signed tokens for borrower auth.

Why it's a separate table (not a JSONB column on users):

  - One user can hold multiple outstanding links (signup link +
    password-reset link + email-verify link could all coexist).
  - Token-hash uniqueness needs an index for O(log n) consume.
  - Expiry sweep is a single DELETE WHERE expires_at < now() job —
    far cheaper than scanning a JSONB array on every user.

We never store the plain-text token, only ``sha256(token)``. A DB
dump leak can't be replayed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mkopo.models.base import Base

if TYPE_CHECKING:
    from mkopo.models.user import User


class MagicLink(Base):
    """A single-use signed-URL token delivered to a user's email.

    ``purpose`` describes *what* the token authorises when consumed:

    - ``"login"``          — replaces a password for one-shot auth
    - ``"set_password"``   — sent on signup so the user can set their
                              initial password without ever typing one
    - ``"password_reset"`` — sent on "forgot password"
    - ``"email_verify"``   — sent to confirm a new email address

    The consume endpoint must always verify both ``purpose`` and
    ``token_hash`` — accepting a ``login`` token at a
    ``password_reset`` endpoint would be a category-confusion bug.
    """

    __tablename__ = "magic_links"
    __table_args__ = (
        Index("ix_magic_links_token_hash", "token_hash", unique=True),
        Index("ix_magic_links_user_expires", "user_id", "expires_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # sha256 of the plain-text token. 64 hex chars.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set to ``now()`` when consumed. Single-use semantics: ``IS NOT
    # NULL`` is the "burned" marker.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(lazy="joined")
