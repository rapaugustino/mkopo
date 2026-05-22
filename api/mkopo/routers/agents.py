"""Agent run endpoints.

Each agent's ``/run`` endpoint now streams Server-Sent Events as the
LangGraph executes — one event per node completion, an ``interrupt``
event when the graph pauses for human approval, and a final ``done``
event with the same payload the synchronous endpoints used to return.

Why SSE rather than WebSockets / polling: the streams are one-way
(server → client), one-shot per invocation, and short-lived. SSE has
trivial server semantics (yield bytes), works over plain HTTP/2, and
needs no extra protocol library on either side. The "REST + SSE
streaming" call-out in the design doc is what this resolves.

The ``/resume`` endpoint also streams: when an underwriter approves the
drafted email, we ``Command(resume=...)`` into the checkpointed graph
and the remaining nodes (``send``) emit events on the way out.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from mkopo.agents import build_decision_graph, build_intake_graph, build_underwriting_graph
from mkopo.agents.orchestrator import (
    maybe_chain_after_decision,
    maybe_chain_after_intake,
    maybe_chain_after_underwriting,
)
from mkopo.agents.streaming import (
    DECISION_NODES,
    INTAKE_NODES,
    UNDERWRITING_NODES,
    stream_graph_run,
)
from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.schemas import ApproveEmailIn
from mkopo.services import loans as loan_service

router = APIRouter(prefix="/loans/{loan_id}/agents", tags=["agents"])


# ``text/event-stream`` is the SSE media type. The other headers are
# defensive: disable buffering on intermediate proxies so events arrive
# as soon as they're flushed, and tell the browser this stream isn't
# cacheable.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",  # nginx; harmless if absent
    "Connection": "keep-alive",
}


def _stream(generator: Any) -> StreamingResponse:
    """Wrap a byte-yielding async generator as an SSE response."""
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)


# --- Intake --------------------------------------------------------------


@router.post("/intake/run")
async def run_intake(loan_id: uuid.UUID, user: CurrentUserDep) -> StreamingResponse:
    """Kick off the intake agent, streaming progress as it executes.

    Frontends should read the response body chunk by chunk and parse SSE
    events. The terminal ``done`` event carries ``{thread_id, status,
    interrupt, result}`` so existing post-run handlers (cache refresh,
    modal open on interrupt) keep working.
    """
    thread_id = f"intake-{loan_id}"
    config = {"configurable": {"thread_id": thread_id}}

    async def _after(state: dict[str, object], seen_interrupt: bool) -> None:
        if seen_interrupt:
            return  # email approval gate — orchestrator stays out.
        completed = str(state.get("status") or "")
        await maybe_chain_after_intake(loan_id, completed_with=completed)

    return _stream(
        stream_graph_run(
            build_graph=build_intake_graph,
            nodes=INTAKE_NODES,
            initial_input={"loan_id": str(loan_id), "status": "running"},
            config=config,
            thread_id=thread_id,
            loan_id=loan_id,
            agent_name="intake",
            # Intake's "result" the frontend cares about is the interrupt
            # payload; the full state contains lots of internal scratch
            # we don't want to serialise.
            extract_result=lambda s: {"status": s.get("status")},
            extract_interrupt=_peek_intake_interrupt,
            on_complete=_after,
        )
    )


@router.post("/intake/resume")
async def resume_intake(
    loan_id: uuid.UUID,
    payload: ApproveEmailIn,
    user: CurrentUserDep,
) -> StreamingResponse:
    """Resume after the underwriter approves / edits / cancels the draft.

    Streams the remaining graph nodes (``send`` if approved, none if
    cancelled). The terminal ``done`` event mirrors the resumed state.
    """
    thread_id = f"intake-{loan_id}"
    config = {"configurable": {"thread_id": thread_id}}

    resume_value: dict[str, Any] = {"action": payload.action}
    if payload.subject:
        resume_value["subject"] = payload.subject
    if payload.body_text:
        resume_value["body_text"] = payload.body_text

    return _stream(
        stream_graph_run(
            build_graph=build_intake_graph,
            nodes=INTAKE_NODES,
            initial_input=Command(resume=resume_value),
            config=config,
            thread_id=thread_id,
            loan_id=loan_id,
            agent_name="intake",
            extract_result=lambda s: {"status": s.get("status")},
        )
    )


def _peek_intake_interrupt(state: dict[str, Any]) -> Any | None:
    """Pull the interrupt-payload shape the intake modal expects.

    LangGraph stashes the interrupt value at runtime; by the time we
    inspect the final state it's usually visible on the snapshot's
    ``tasks``. We defer to the streaming helper for that — this hook
    exists only so the ``done`` event can carry the same ``interrupt``
    field the old synchronous response had.
    """
    return state.get("draft_email") if state.get("status") == "awaiting_approval" else None


# --- Underwriting --------------------------------------------------------


@router.post("/underwriting/run")
async def run_underwriting(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> StreamingResponse:
    """Stream the underwriting agent: rules → cited summary → persist.

    No HITL pause — the underwriting agent runs end-to-end. Frontends
    pick the ``result`` field off the terminal ``done`` event to
    populate the workspace.
    """
    if not await loan_service.get_loan(db, loan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    thread_id = f"underwriting-{loan_id}"
    config = {"configurable": {"thread_id": thread_id}}

    async def _after(_state: dict[str, object], _interrupt: bool) -> None:
        await maybe_chain_after_underwriting(loan_id)

    return _stream(
        stream_graph_run(
            build_graph=build_underwriting_graph,
            nodes=UNDERWRITING_NODES,
            initial_input={"loan_id": str(loan_id)},
            config=config,
            thread_id=thread_id,
            loan_id=loan_id,
            agent_name="underwriting",
            extract_result=_extract_underwriting_result,
            on_complete=_after,
        )
    )


def _extract_underwriting_result(state: dict[str, Any]) -> Any:
    """Serialise the agent's ``summary`` Pydantic model for the done event."""
    summary = state.get("summary")
    if summary is None:
        return None
    # Pydantic v2 — ``model_dump`` round-trips through JSON cleanly.
    return summary.model_dump(mode="json") if hasattr(summary, "model_dump") else summary


# --- Decision ------------------------------------------------------------


@router.post("/decision/run")
async def run_decision(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> StreamingResponse:
    """Stream the decision agent: re-evaluate rules → draft package → persist.

    Conditional approve, full approve, and decline paths all flow
    through the same three nodes; the path determination happens
    inside ``draft_decision``.
    """
    if not await loan_service.get_loan(db, loan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    thread_id = f"decision-{loan_id}"
    config = {"configurable": {"thread_id": thread_id}}

    async def _after(_state: dict[str, object], _interrupt: bool) -> None:
        await maybe_chain_after_decision(loan_id)

    return _stream(
        stream_graph_run(
            build_graph=build_decision_graph,
            nodes=DECISION_NODES,
            initial_input={"loan_id": str(loan_id)},
            config=config,
            thread_id=thread_id,
            loan_id=loan_id,
            agent_name="decision",
            extract_result=_extract_decision_result,
            on_complete=_after,
        )
    )


def _extract_decision_result(state: dict[str, Any]) -> Any:
    decision = state.get("decision")
    if decision is None:
        return None
    return decision.model_dump(mode="json") if hasattr(decision, "model_dump") else decision
