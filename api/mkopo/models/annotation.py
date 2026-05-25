"""Annotation ORM — human verdicts on observability traces.

Backs the /eval annotations endpoints + the drawer "mark good/bad/
incorrect" buttons. See migration ``0017_annotations`` for schema
rationale.

Polymorphic by (target_kind, target_id) — one row per (trace, user)
isn't enforced at the DB level because reasonable people may want to
record multiple verdicts on the same trace over time as their
understanding changes; the eval dashboard uses the latest annotation
per (target, user) pair when it needs uniqueness.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base


class AnnotationTargetKind(enum.StrEnum):
    """What kind of trace this annotation applies to.

    Closed set so the service layer can validate before write and
    the dashboard's filter dropdowns can enumerate the options.
    Adding a new target kind requires updating both this enum and
    the validator in services/annotations.py.
    """

    LLM_CALL = "llm_call"
    AGENT_RUN = "agent_run"
    AGENT_STEP = "agent_step"


class AnnotationVerdict(enum.StrEnum):
    """The human's call on the trace.

    - ``good`` — the trace did what it was supposed to. Useful as
      positive labels for any future fine-tuning / eval suite.
    - ``bad`` — the trace failed to run, ran on the wrong data, or
      shouldn't have run at all. Operational regression.
    - ``incorrect`` — the trace ran fine but produced a wrong answer.
      Quality regression. Auto-spawns a review_task so the
      underlying loan gets a second look.
    """

    GOOD = "good"
    BAD = "bad"
    INCORRECT = "incorrect"


class Annotation(Base):
    __tablename__ = "annotations"

    # `id`, `created_at`, `updated_at` from Base.

    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(4096))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    # Populated when a "bad"/"incorrect" verdict auto-creates a
    # review_tasks row. Lets the dashboard show "follow-up pending"
    # vs "follow-up closed" without joining on the verdict text.
    spawned_review_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="SET NULL"),
    )
