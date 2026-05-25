"""Observability endpoints.

A small surface over the audit-grade tables that already exist in the
schema:

- ``llm_calls``       — one row per LLM call (model, status, latency,
                        retry attempt, schema name, prompt hash).
- ``agent_runs``      — one row per LangGraph thread invocation.
- ``audit_events``    — append-only ledger of every action.

The endpoints here exist for the ``/observability`` dashboard, not for
admins log-spelunking on the box. So we return summarised shapes
(p50/p95, counts, ratios) alongside recent raw rows — enough for the
frontend to draw the cards and one filterable table per axis.

Why this matters: the JD calls for "observability and feedback loops to
monitor model performance." The eval dashboard answers "how accurate is
the system?"; the observability dashboard answers "how healthy is the
system?" — latency, retries, errors, throughput. Both are first-class.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import AgentRun, AgentStep, AuditEvent
from mkopo.models.errors import InfrastructureError
from mkopo.models.eval import LLMCall, ToolUse

router = APIRouter(prefix="/observability", tags=["observability"])


# ----- response shapes ---------------------------------------------------


class LLMCallRow(BaseModel):
    """One row of the recent-LLM-calls table."""

    id: str
    created_at: datetime
    model: str
    schema_name: str | None
    status: str
    attempt: int
    elapsed_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    system_prompt_hash: str  # first 12 chars used by the UI
    # Short failure summary. ``None`` for successful calls; populated by
    # the gateway on schema_failed / error rows so the table can hint
    # at the reason before the user opens the detail drawer.
    error_reason: str | None = None
    # Parent step inside the agent run, if this call happened during
    # one. Backfilled by mkopo.agents.streaming._persist_step using a
    # wall-clock window match (see that function's docstring). The
    # AgentRunDrawer uses this to nest calls under their owning step
    # instead of rendering a flat list.
    parent_step_id: str | None = None


class ToolUseRow(BaseModel):
    """One persisted tool invocation. Renders as a step in the
    trajectory timeline inside the observability drawer."""

    id: str
    sequence_num: int
    tool_name: str
    status: str  # "ok" | "error" | "cancelled"
    elapsed_ms: int | None
    input: dict[str, Any]
    output: dict[str, Any] | None
    error_message: str | None
    created_at: datetime


class LLMCallDetail(BaseModel):
    """Full LLM call record for the observability drill-in drawer.

    Same shape as ``LLMCallRow`` plus ``error_detail`` (long-form
    technical content), a small list of *related* calls — rows
    sharing the same ``system_prompt_hash`` in the recent window, so an
    operator looking at one schema_failed call can see whether the
    same prompt fails repeatedly or is a one-off — and the full
    ``tool_uses`` trajectory if this was a tool-using call.
    """

    id: str
    created_at: datetime
    model: str
    schema_name: str | None
    status: str
    attempt: int
    elapsed_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    system_prompt_hash: str  # full hash (64 chars) for grouping
    error_reason: str | None
    error_detail: str | None
    related: list[LLMCallRow]  # other calls with the same prompt hash
    tool_uses: list[ToolUseRow]  # ordered tool trajectory, empty if none


class ModelStats(BaseModel):
    """Per-model rollup over the requested window.

    ``cost_usd`` is the summed (input + output) dollars across the
    window. Nullable so models without pricing data (third-party,
    unknown) don't show up as $0 — which would understate the bill.
    """

    model: str
    calls: int
    error_rate: float | None
    retry_rate: float | None  # fraction of calls with attempt > 0
    p50_seconds: float | None
    p95_seconds: float | None
    cost_usd: float | None
    input_tokens: int
    output_tokens: int


class LLMSummary(BaseModel):
    """Headline KPIs for the observability dashboard."""

    window_hours: int
    total_calls: int
    error_rate: float | None
    schema_fail_rate: float | None
    p50_seconds: float | None
    p95_seconds: float | None
    # Cost rollup. ``total_cost_usd`` is the sum of ``cost_input_usd
    # + cost_output_usd`` across the window for rows that have a
    # cost recorded; ``uncosted_calls`` counts rows that don't (the
    # model wasn't in the pricing registry). The frontend uses the
    # uncosted count to surface "cost is incomplete" when relevant.
    total_cost_usd: float
    uncosted_calls: int
    total_input_tokens: int
    total_output_tokens: int
    by_model: list[ModelStats]
    recent: list[LLMCallRow]


class InfrastructureErrorRow(BaseModel):
    """One row of the recent-errors table."""

    id: str
    created_at: datetime
    path: str
    method: str
    status_code: int
    error_class: str
    error_message: str
    user_id: str | None
    request_id: str | None


class InfrastructureErrorDetail(InfrastructureErrorRow):
    """Drill-in shape — same row plus the full traceback."""

    traceback: str | None


class ErrorClassStat(BaseModel):
    """One row of the per-error-class rollup."""

    error_class: str
    count: int
    last_seen: datetime


class InfrastructureErrorSummary(BaseModel):
    """Headline shape for the errors surface."""

    window_hours: int
    total: int
    by_class: list[ErrorClassStat]
    recent: list[InfrastructureErrorRow]


class AgentRunRow(BaseModel):
    id: str
    created_at: datetime
    agent_name: str
    thread_id: str
    status: str
    loan_id: str


class AgentSummary(BaseModel):
    window_hours: int
    total_runs: int
    by_agent: dict[str, int]
    by_status: dict[str, int]
    recent: list[AgentRunRow]


# ----- helpers -----------------------------------------------------------


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile on a pre-sorted list.

    Kept here rather than pulling in numpy/statistics because we only
    use it three times and the readability cost is minimal.
    """
    if not sorted_values:
        return None
    k = max(0, min(len(sorted_values) - 1, int(round((pct / 100) * (len(sorted_values) - 1)))))
    return sorted_values[k]


# ----- endpoints ---------------------------------------------------------


@router.get("/llm", response_model=LLMSummary)
async def llm_observability(
    user: CurrentUserDep,
    db: DbSessionDep,
    hours: int = 24,
    limit: int = 50,
) -> LLMSummary:
    """Recent LLM activity rollup.

    ``hours`` chooses the rollup window; ``limit`` caps how many recent
    raw rows we return for the table at the bottom of the dashboard.
    """
    hours = max(1, min(hours, 720))  # cap at 30 days
    limit = max(10, min(limit, 500))
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    stmt = select(LLMCall).where(LLMCall.created_at >= cutoff)
    rows = (await db.execute(stmt)).scalars().all()

    latencies_all = sorted([r.elapsed_seconds for r in rows])
    errors = sum(1 for r in rows if r.status not in ("ok",))
    schema_fails = sum(1 for r in rows if r.status == "schema_failed")

    # Per-model rollup.
    by_model: dict[str, list[LLMCall]] = {}
    for r in rows:
        by_model.setdefault(r.model, []).append(r)

    by_model_stats: list[ModelStats] = []
    for model, calls in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        lats = sorted([c.elapsed_seconds for c in calls])
        errs = sum(1 for c in calls if c.status != "ok")
        retries = sum(1 for c in calls if c.attempt > 0)
        # Per-model cost rollup. Any row missing a cost (model not in
        # the pricing registry) contributes None here; we sum only
        # what we have and surface model_cost=None when the *whole*
        # model is uncosted so the UI knows to flag it.
        model_cost = sum(
            (float(c.cost_input_usd or 0) + float(c.cost_output_usd or 0))
            for c in calls
        )
        has_cost = any(c.cost_input_usd is not None for c in calls)
        by_model_stats.append(
            ModelStats(
                model=model,
                calls=len(calls),
                error_rate=errs / len(calls) if calls else None,
                retry_rate=retries / len(calls) if calls else None,
                p50_seconds=_percentile(lats, 50),
                p95_seconds=_percentile(lats, 95),
                cost_usd=round(model_cost, 4) if has_cost else None,
                input_tokens=sum(c.input_tokens or 0 for c in calls),
                output_tokens=sum(c.output_tokens or 0 for c in calls),
            )
        )

    # Window-level cost totals. ``uncosted_calls`` is the count of
    # rows with no cost (unknown model) so the UI can warn when the
    # bill is incomplete.
    total_cost = sum(
        (float(r.cost_input_usd or 0) + float(r.cost_output_usd or 0))
        for r in rows
    )
    uncosted = sum(1 for r in rows if r.cost_input_usd is None)

    # Pull the most recent ``limit`` rows for the raw-events table.
    recent_stmt = (
        select(LLMCall)
        .where(LLMCall.created_at >= cutoff)
        .order_by(desc(LLMCall.created_at))
        .limit(limit)
    )
    recent_rows = (await db.execute(recent_stmt)).scalars().all()

    return LLMSummary(
        window_hours=hours,
        total_calls=len(rows),
        error_rate=errors / len(rows) if rows else None,
        schema_fail_rate=schema_fails / len(rows) if rows else None,
        p50_seconds=_percentile(latencies_all, 50),
        p95_seconds=_percentile(latencies_all, 95),
        total_cost_usd=round(total_cost, 4),
        uncosted_calls=uncosted,
        total_input_tokens=sum(r.input_tokens or 0 for r in rows),
        total_output_tokens=sum(r.output_tokens or 0 for r in rows),
        by_model=by_model_stats,
        recent=[
            LLMCallRow(
                id=str(r.id),
                created_at=r.created_at,
                model=r.model,
                schema_name=r.schema_name,
                status=r.status,
                attempt=r.attempt,
                elapsed_seconds=r.elapsed_seconds,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                system_prompt_hash=r.system_prompt_hash[:12],
                error_reason=r.error_reason,
            )
            for r in recent_rows
        ],
    )


@router.get("/llm/{call_id}", response_model=LLMCallDetail)
async def llm_call_detail(
    call_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> LLMCallDetail:
    """One LLM call's full record + related calls sharing the same
    system_prompt_hash.

    Powers the observability drill-in drawer. The audit reports the
    Eval / Obs pages were "REAL but shallow — can see *that* a call
    failed but never *why*." This endpoint is the *why* — full
    ``error_detail`` plus the prompt-hash neighbourhood so the
    operator can tell "this prompt always fails on this model" from
    "one-off transient API blip".
    """
    call = (
        await db.execute(select(LLMCall).where(LLMCall.id == call_id))
    ).scalar_one_or_none()
    if call is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="LLM call not found",
        )
    # Same-prompt neighbours in a broad window so the operator sees
    # whether this is the only failure or a pattern. Limit kept low
    # because the drawer doesn't need a huge list.
    cutoff = call.created_at - timedelta(hours=72)
    neighbours = (
        await db.execute(
            select(LLMCall)
            .where(
                LLMCall.system_prompt_hash == call.system_prompt_hash,
                LLMCall.id != call.id,
                LLMCall.created_at >= cutoff,
            )
            .order_by(desc(LLMCall.created_at))
            .limit(20)
        )
    ).scalars().all()

    # Tool trajectory issued by this call. Ordered by sequence_num so
    # the drawer renders the agent's reasoning in the same order the
    # model proposed it.
    tool_uses = (
        await db.execute(
            select(ToolUse)
            .where(ToolUse.llm_call_id == call.id)
            .order_by(ToolUse.sequence_num.asc())
        )
    ).scalars().all()

    return LLMCallDetail(
        id=str(call.id),
        created_at=call.created_at,
        model=call.model,
        schema_name=call.schema_name,
        status=call.status,
        attempt=call.attempt,
        elapsed_seconds=call.elapsed_seconds,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        system_prompt_hash=call.system_prompt_hash,
        error_reason=call.error_reason,
        error_detail=call.error_detail,
        related=[
            LLMCallRow(
                id=str(n.id),
                created_at=n.created_at,
                model=n.model,
                schema_name=n.schema_name,
                status=n.status,
                attempt=n.attempt,
                elapsed_seconds=n.elapsed_seconds,
                input_tokens=n.input_tokens,
                output_tokens=n.output_tokens,
                system_prompt_hash=n.system_prompt_hash[:12],
                error_reason=n.error_reason,
            )
            for n in neighbours
        ],
        tool_uses=[
            ToolUseRow(
                id=str(t.id),
                sequence_num=t.sequence_num,
                tool_name=t.tool_name,
                status=t.status,
                elapsed_ms=t.elapsed_ms,
                input=t.input,
                output=t.output,
                error_message=t.error_message,
                created_at=t.created_at,
            )
            for t in tool_uses
        ],
    )


@router.get("/agents", response_model=AgentSummary)
async def agents_observability(
    user: CurrentUserDep,
    db: DbSessionDep,
    hours: int = 24,
    limit: int = 50,
) -> AgentSummary:
    """Recent LangGraph agent runs rollup.

    Pairs cleanly with the LLM view: an agent run includes any number
    of LLM calls — when one of those failed, you'll see it in /llm,
    and the parent run shows here.
    """
    hours = max(1, min(hours, 720))
    limit = max(10, min(limit, 500))
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    runs = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.created_at >= cutoff)
            .order_by(desc(AgentRun.created_at))
        )
    ).scalars().all()

    by_agent_counts = await db.execute(
        select(AgentRun.agent_name, func.count())  # type: ignore[arg-type]
        .where(AgentRun.created_at >= cutoff)
        .group_by(AgentRun.agent_name)
    )
    by_status_counts = await db.execute(
        select(AgentRun.status, func.count())  # type: ignore[arg-type]
        .where(AgentRun.created_at >= cutoff)
        .group_by(AgentRun.status)
    )

    return AgentSummary(
        window_hours=hours,
        total_runs=len(runs),
        by_agent={row[0]: row[1] for row in by_agent_counts},
        by_status={row[0]: row[1] for row in by_status_counts},
        recent=[
            AgentRunRow(
                id=str(r.id),
                created_at=r.created_at,
                agent_name=r.agent_name,
                thread_id=r.thread_id,
                status=r.status,
                loan_id=str(r.loan_id),
            )
            for r in runs[:limit]
        ],
    )


class AgentStepRow(BaseModel):
    """One LangGraph node execution inside an agent run.

    Mirrors the ``AgentStep`` ORM row; lands on the trace timeline.
    """

    id: str
    created_at: datetime
    node: str
    status: str  # "ok" | "skipped" | "interrupt" | "failed"
    summary: str | None
    elapsed_ms: int | None
    payload: dict[str, Any]


class AgentRunDetail(BaseModel):
    """Full record of one agent run + its step trace + LLM calls.

    Powers the agent-run drawer. The point: an auditor opening this
    sees *what the agent did*, not just *that it ran*. Steps render
    as a vertical trail (one row per node); ``llm_calls`` is the
    same shape as the recent-calls list so the drawer can reuse the
    LLM-call drill-in.
    """

    id: str
    created_at: datetime
    agent_name: str
    thread_id: str
    status: str
    loan_id: str
    payload: dict[str, Any]
    steps: list[AgentStepRow]
    llm_calls: list[LLMCallRow]


@router.get("/agents/{agent_run_id}", response_model=AgentRunDetail)
async def agent_run_detail(
    agent_run_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> AgentRunDetail:
    """One agent run's full trace.

    Joins:
    - the AgentRun row (status, agent_name, thread_id, loan_id, payload),
    - all AgentStep rows ordered by created_at (the node-by-node trail),
    - all LLMCall rows with the same thread_id (the model calls each
      step issued, attached at run scope rather than step scope — the
      ContextVar mechanism doesn't currently identify the originating
      step, only the run).
    """
    run = (
        await db.execute(select(AgentRun).where(AgentRun.id == agent_run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Agent run not found",
        )

    steps = (
        await db.execute(
            select(AgentStep)
            .where(AgentStep.agent_run_id == agent_run_id)
            .order_by(AgentStep.created_at)
        )
    ).scalars().all()

    llm_calls = (
        await db.execute(
            select(LLMCall)
            .where(LLMCall.thread_id == run.thread_id)
            .order_by(LLMCall.created_at)
        )
    ).scalars().all()

    return AgentRunDetail(
        id=str(run.id),
        created_at=run.created_at,
        agent_name=run.agent_name,
        thread_id=run.thread_id,
        status=run.status,
        loan_id=str(run.loan_id),
        payload=run.payload or {},
        steps=[
            AgentStepRow(
                id=str(s.id),
                created_at=s.created_at,
                node=s.node,
                status=s.status,
                summary=s.summary,
                elapsed_ms=s.elapsed_ms,
                payload=s.payload or {},
            )
            for s in steps
        ],
        llm_calls=[
            LLMCallRow(
                id=str(c.id),
                created_at=c.created_at,
                model=c.model,
                schema_name=c.schema_name,
                status=c.status,
                attempt=c.attempt,
                elapsed_seconds=c.elapsed_seconds,
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                system_prompt_hash=c.system_prompt_hash[:12],
                error_reason=c.error_reason,
                parent_step_id=(
                    str(c.parent_step_id) if c.parent_step_id else None
                ),
            )
            for c in llm_calls
        ],
    )


# Touching AuditEvent here is intentional — keeps the import warm so a
# future "recent audit" endpoint can land without re-shuffling the
# router imports.
_ = AuditEvent


# ----- infrastructure errors --------------------------------------------


@router.get("/errors", response_model=InfrastructureErrorSummary)
async def errors_observability(
    user: CurrentUserDep,
    db: DbSessionDep,
    hours: int = 168,  # default 7d — errors are rarer than LLM calls
    limit: int = 50,
) -> InfrastructureErrorSummary:
    """Recent server errors (uncaught exceptions / 5xx).

    Fed by the FastAPI exception handler in
    ``mkopo.services.error_capture``. The window default is wider than
    the LLM rollup because errors should be rare — a 24h view on a
    healthy install will usually be empty, but the 7d view tells the
    "stable or trending up?" story.
    """
    hours = max(1, min(hours, 720))
    limit = max(10, min(limit, 500))
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    rows = (
        await db.execute(
            select(InfrastructureError)
            .where(InfrastructureError.created_at >= cutoff)
            .order_by(desc(InfrastructureError.created_at))
        )
    ).scalars().all()

    # Per-class rollup. Counts + most recent occurrence so the UI can
    # show "OperationalError × 3 — last seen 2m ago" at a glance.
    by_class_map: dict[str, list[InfrastructureError]] = {}
    for r in rows:
        by_class_map.setdefault(r.error_class, []).append(r)
    by_class = [
        ErrorClassStat(
            error_class=cls,
            count=len(items),
            last_seen=max(i.created_at for i in items),
        )
        for cls, items in by_class_map.items()
    ]
    by_class.sort(key=lambda c: -c.count)

    return InfrastructureErrorSummary(
        window_hours=hours,
        total=len(rows),
        by_class=by_class,
        recent=[
            InfrastructureErrorRow(
                id=str(r.id),
                created_at=r.created_at,
                path=r.path,
                method=r.method,
                status_code=r.status_code,
                error_class=r.error_class,
                error_message=r.error_message,
                user_id=str(r.user_id) if r.user_id else None,
                request_id=r.request_id,
            )
            for r in rows[:limit]
        ],
    )


@router.get("/errors/{error_id}", response_model=InfrastructureErrorDetail)
async def error_detail(
    error_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> InfrastructureErrorDetail:
    """One error's full traceback. Powers the drill-in drawer."""
    row = (
        await db.execute(
            select(InfrastructureError).where(InfrastructureError.id == error_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND)
    return InfrastructureErrorDetail(
        id=str(row.id),
        created_at=row.created_at,
        path=row.path,
        method=row.method,
        status_code=row.status_code,
        error_class=row.error_class,
        error_message=row.error_message,
        traceback=row.traceback,
        user_id=str(row.user_id) if row.user_id else None,
        request_id=row.request_id,
    )
