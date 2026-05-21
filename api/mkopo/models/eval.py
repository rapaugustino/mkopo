"""Eval + drift-monitor ORM models.

Two append-only tables backing Phase G:

- ``TaskRun`` — one row per (eval task, source, day). The CI eval gate
  writes ``source='golden'`` rows when the suite runs; the nightly drift
  monitor writes ``source='production'`` rows sampled from real
  extractions. Together they back the weekly-trend chart and the drift
  alert on the eval dashboard.

- ``LLMCall`` — minimal record of every LLM call (model, latency, token
  counts, schema name, status). Logs already exist via structlog, but
  logs aren't queryable. Persisting here lets us answer "what's our p95
  LLM latency on this model?" with one indexed query.

See migration ``0005_eval`` for the matching DDL.
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mkopo.models.base import Base


class TaskRun(Base):
    """One scored run of an eval task against a population of items.

    ``task_name`` is the eval id (e.g. ``"extraction.noi"``).
    ``source`` is ``"golden"`` (CI ran the fixed eval suite) or
    ``"production"`` (drift monitor sampled real extractions). ``n`` is
    the population size, ``accuracy`` is the fraction passing, and
    ``details`` is a free-form JSONB blob the writer controls (per-field
    breakdown, failure ids, etc.).
    """

    __tablename__ = "task_runs"
    __table_args__ = (
        Index(
            "ix_task_runs_task_source_created",
            "task_name",
            "source",
            "created_at",
        ),
        Index("ix_task_runs_created", "created_at"),
    )

    task_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    accuracy: Mapped[float] = mapped_column(Float, nullable=False)
    avg_score: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class LLMCall(Base):
    """One LLM invocation. Written by ``LLMGateway._record_call`` on every
    completion (success or failure).

    ``system_prompt_hash`` is sha256(system_prompt) so we can group calls
    by prompt without storing potentially sensitive prompt content.
    ``schema_name`` is the name of the Pydantic model used for
    structured-output gating, or ``None`` for free-form completions.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_created", "created_at"),
        Index("ix_llm_calls_model_created", "model", "created_at"),
    )

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    system_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_name: Mapped[str | None] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
