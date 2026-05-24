"""Prompt ORM — versioned system prompts.

Backs the :mod:`mkopo.services.prompts` service that every LLM call
site consults instead of hardcoded strings. See migration
``0015_prompts`` for the schema rationale.

One Prompt row = one version of one identifier. ``is_active=True``
selects the version the runtime uses; flipping that flag is the
"activate / roll back" operation.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base


class Prompt(Base):
    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint(
            "identifier", "version", name="uq_prompts_identifier_version"
        ),
    )

    # `id`, `created_at`, `updated_at` are inherited from Base.

    identifier: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Free-form, user-supplied description of what changed in this
    # version. Required at the API boundary for any user-created
    # version; nullable so the bootstrap-seed v1 rows (no human in
    # the loop) can leave it empty.
    change_note: Mapped[str | None] = mapped_column(String(512))
    # Exactly one row per identifier has this true. The partial
    # unique index in migration 0015 enforces it at the DB level.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
