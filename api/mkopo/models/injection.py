"""InjectionDetection ORM — every input scanned by the injection detector.

One row per scan, regardless of outcome. The Safety dashboard +
loan-level chip both read this table; the global-window aggregates
in :mod:`routers.safety` GROUP BY ``decision`` and ``source_kind``
on it.

The schema lives in migration ``0021_injection_detections``.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base


class InjectionSourceKind(enum.StrEnum):
    """Where the scanned input came from.

    Closed set — corresponds 1:1 with the four detector hook sites
    so the dashboard's "by source" pie chart matches the wire
    topology. Adding a new source kind requires the migration enum
    type to be extended too.
    """

    DOCUMENT = "document"
    CHAT_MESSAGE = "chat_message"
    INBOUND_EMAIL = "inbound_email"
    BORROWER_APPLICATION = "borrower_application"


class InjectionSeverity(enum.StrEnum):
    """Three-band severity from the hybrid detector.

    - ``low``: pattern matched but the matched pattern's floor is
      benign (e.g. polite "please ignore the formatting" misfire).
      Logged, allowed.
    - ``medium``: pattern matched at a borderline floor — escalated
      to the Haiku judge. The judge's verdict drives the final
      decision (allowed or blocked).
    - ``high``: pattern matched at the kill-floor. Fail-closed; the
      caller raises ``BlockedByInjectionError``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InjectionDecision(enum.StrEnum):
    """Outcome of the scan after the optional Haiku second-pass.

    Stored alongside ``severity`` (not derived from it) so dashboard
    filters like "show me only the blocks" stay simple SQL — no
    correlated lookup against the severity-to-decision mapping in
    code, which would force an in-memory filter.
    """

    ALLOWED = "allowed"
    FLAGGED = "flagged"
    BLOCKED = "blocked"


# Bind the ORM enum columns to the postgres-native enum types
# created in the migration. ``create_type=False`` is important —
# without it SQLAlchemy would try to create the type a second time
# on first use, which conflicts with the explicit ENUM.create()
# call in the migration.
_source_kind_pg = ENUM(
    *(k.value for k in InjectionSourceKind),
    name="injection_source_kind",
    create_type=False,
)
_severity_pg = ENUM(
    *(s.value for s in InjectionSeverity),
    name="injection_severity",
    create_type=False,
)
_decision_pg = ENUM(
    *(d.value for d in InjectionDecision),
    name="injection_decision",
    create_type=False,
)


class InjectionDetection(Base):
    """One scan of one input against the injection-pattern catalog."""

    __tablename__ = "injection_detections"

    # id, created_at, updated_at from Base.

    loan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("loans.id", ondelete="CASCADE"),
        nullable=True,
    )
    source_kind: Mapped[str] = mapped_column(_source_kind_pg, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    severity: Mapped[str] = mapped_column(_severity_pg, nullable=False)
    decision: Mapped[str] = mapped_column(_decision_pg, nullable=False)
    matched_patterns: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    llm_judge_called: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    llm_judge_severity: Mapped[str | None] = mapped_column(
        _severity_pg, nullable=True
    )
    llm_judge_critique: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # ``created_at`` from Base is the detection timestamp — every
    # row writes once and is immutable, so insert time == event time.
