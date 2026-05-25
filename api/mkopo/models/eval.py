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

import uuid
from decimal import Decimal

from sqlalchemy import Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    ``error_reason`` and ``error_detail`` are populated on failure
    rows so the observability inspector can show *why* a call broke
    instead of just "status=error". Both stay null on success.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_created", "created_at"),
        Index("ix_llm_calls_model_created", "model", "created_at"),
        Index("ix_llm_calls_thread_created", "thread_id", "created_at"),
    )

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    system_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_name: Mapped[str | None] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Failure forensics. ``error_reason`` is the short one-line summary
    # (the API exception class, the validation error count, etc.) and
    # is safe to show in the observability table. ``error_detail`` is
    # the longer message — full validation error pretty-print, or the
    # API error body — for the drill-in drawer. Both ``None`` on success.
    error_reason: Mapped[str | None] = mapped_column(String(256))
    error_detail: Mapped[str | None] = mapped_column(String(4096))
    # LangGraph thread id of the agent run that issued this call, when
    # the call happened inside one. Populated via a ContextVar in
    # ``mkopo.agents.context`` so the gateway never has to thread it
    # through call signatures. ``None`` for ad-hoc calls outside an
    # agent run (eval CI, smoke tests, manual scripts).
    thread_id: Mapped[str | None] = mapped_column(String(128))
    # ``agent_steps.id`` of the node this call ran inside, when the
    # call happened during a graph step. Mirror of ``thread_id``'s
    # ContextVar plumbing — the streaming layer binds the var on
    # node entry and the gateway reads it on _record_call. Lets the
    # AgentRunDrawer nest calls under their owning step instead of
    # showing a flat list. Nullable so ad-hoc calls and pre-migration
    # rows behave the same as before.
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_steps.id", ondelete="SET NULL"),
    )
    # Computed dollar cost split into input vs output. NULL when the
    # model isn't in the pricing registry (unknown name, third-party
    # provider, etc.) or when the call failed before token counts
    # were available. Numeric(10, 6) preserves fractional-cent
    # precision so aggregations over thousands of calls don't drift.
    cost_input_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    cost_output_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    # ``prompts.id`` of the active prompt row whose body fed this
    # call. Stamped by the gateway via a ContextVar set inside
    # :func:`mkopo.services.prompts.get`. Nullable: free-form calls
    # outside the registry (rewrite-assist endpoint, eval CI, ad-hoc
    # scripts) have no registry row, and pre-migration rows stay null
    # too. The observability page joins on this to surface which
    # prompt version produced any given output.
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompts.id", ondelete="SET NULL"),
    )


class ToolUse(Base):
    """One tool invocation made by an LLM during a chat / agent turn.

    Persists the trajectory the model asked for and what came back.
    The observability drawer renders these as a timeline under the
    LLM call that issued them, so an operator can see "the agent
    called ``withdraw_application`` with ``{loan_id: …}`` and got
    ``{ok: true}``" without scraping the structured logs.

    Foreign keys are nullable everywhere except ``tool_name`` so we
    can record an ad-hoc tool execution (a future direct API path
    that doesn't go through an LLM) — today every row will have an
    ``llm_call_id`` because every tool use goes through the chat
    loop's ``call_with_tools`` cycle.

    See migration ``0014_tool_uses`` for the matching DDL.
    """

    __tablename__ = "tool_uses"
    __table_args__ = (
        Index("ix_tool_uses_llm_call_id_sequence", "llm_call_id", "sequence_num"),
        Index("ix_tool_uses_agent_run_id", "agent_run_id"),
        Index("ix_tool_uses_loan_id", "loan_id"),
        Index("ix_tool_uses_thread_id", "thread_id"),
        Index("ix_tool_uses_tool_name_created_at", "tool_name", "created_at"),
    )

    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_calls.id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    loan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("loans.id", ondelete="CASCADE"),
        nullable=True,
    )
    sequence_num: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSONB)
    # ``ok`` on a clean execution, ``error`` on a raised tool, ``cancelled``
    # for the confirmation-required path where the user clicked Cancel.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error_message: Mapped[str | None] = mapped_column(Text)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
