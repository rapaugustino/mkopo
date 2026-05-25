"""InfrastructureError — non-business-rule failures captured by the
FastAPI exception handler.

See migration ``0016_cost_and_errors`` for the column-by-column
rationale. The short version: ``audit_events`` records actions that
happened, ``llm_calls`` records calls that ran, but neither captures
"the request crashed before any of that". This table is the missing
third leg.

Lifetime: append-only. The retention sweep can prune rows older than
some window (TBD) when this corpus gets large, but for now we keep
everything so an operator looking at "what broke last week" gets the
full picture.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base


class InfrastructureError(Base):
    __tablename__ = "infrastructure_errors"
    __table_args__ = (
        Index("ix_infrastructure_errors_created", "created_at"),
        Index(
            "ix_infrastructure_errors_class_created",
            "error_class",
            "created_at",
        ),
        Index(
            "ix_infrastructure_errors_path_created",
            "path",
            "created_at",
        ),
    )

    # Request envelope. We record the routed path (no path params
    # substituted) and the verb so the rollups can group by endpoint
    # rather than every unique URL.
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    # Always a 5xx by the time we reach the handler; recorded
    # explicitly so the inspector can show "503 — Postgres timed out"
    # vs "500 — uncaught NoneType" at a glance.
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    # Short identifiers for grouping. ``error_class`` is the Python
    # exception class name (``RuntimeError`` / ``OperationalError`` /
    # etc.). ``error_message`` is the truncated ``str(exc)``.
    error_class: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Full traceback (truncated). Kept in a separate Text column so
    # the list view doesn't need to load it.
    traceback: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    # Free-form correlation id when the upstream tracing layer sets
    # one. Lets a runbook follow the error from the UI through to the
    # structured log line.
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
