"""SSE adapter for LangGraph agent runs.

Why this exists
---------------

Synchronous ``graph.ainvoke()`` blocks until the whole agent run completes —
fine for tests, terrible for the case-file timeline UX. The whole point of
the agentic workflow is that an underwriter can see what the agent is
doing: which document is being extracted, which rule fired, whether the
draft email is ready for approval. The case-file timeline is the *display
surface*; this module is the *transport* that lets it update live.

We use LangGraph's ``astream(stream_mode="updates")`` — it yields
``{node_name: state_delta}`` per node completion, which maps cleanly onto
SSE events. We don't use ``astream_events`` because it emits LangChain's
internal runnable trace (one event per llm token, per tool call, per
runnable wrap), which is too noisy for a human-facing progress UI.

Event protocol
--------------

The stream emits four event kinds — small, stable, and deliberately
human-readable so a curl debugger session is useful:

- ``started`` — payload ``{thread_id, nodes: [{key, label}]}``. Sent
  once, immediately. The frontend uses ``nodes`` to render a "checkmark
  trail" placeholder.
- ``node_complete`` — payload ``{node, label, summary}``. Sent after
  each LangGraph node returns. ``summary`` is a short human-readable
  blurb derived from the node's state delta (e.g. "Extracted 7 of 8
  fields").
- ``interrupt`` — payload mirrors the LangGraph interrupt value. Sent
  when the graph pauses on ``interrupt()``; the frontend opens the
  approval modal and stops the progress spinner.
- ``done`` — payload ``{status, result}``. Final event; closes the
  stream. ``result`` carries whatever the synchronous endpoint used to
  return so frontends can drop in.
- ``error`` — payload ``{message}``. Terminal; emitted if the graph
  raises.

SSE format follows RFC 9110-adjacent convention: ``event: <name>\\n
data: <json>\\n\\n``. We don't bother with ``id:`` or ``retry:`` — this
stream is one-shot and the frontend re-runs the whole agent on retry,
not picks up mid-stream.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.agents.context import agent_run_context
from mkopo.db import get_session
from mkopo.models import AgentRun, AgentStep
from mkopo.models.eval import LLMCall

logger = structlog.get_logger()

# ---- topology metadata --------------------------------------------------

# Per-agent node metadata. The order matches the LangGraph edge graph
# (intake has an optional `approve`/`send` branch when no docs are missing;
# the frontend handles that gracefully by treating un-completed nodes as
# "skipped" once the stream ends).
INTAKE_NODES: list[tuple[str, str]] = [
    ("extract", "Extracting fields from documents"),
    ("identify_missing", "Checking required fields"),
    ("draft_request", "Drafting borrower email"),
    ("approve", "Awaiting human approval"),
    ("send", "Sending email"),
]

UNDERWRITING_NODES: list[tuple[str, str]] = [
    ("fetch_and_evaluate", "Evaluating policy rules"),
    ("draft_summary", "Drafting cited summary"),
    ("persist", "Persisting result"),
]

DECISION_NODES: list[tuple[str, str]] = [
    ("fetch_and_evaluate", "Re-evaluating rules"),
    ("draft_decision", "Drafting decision package"),
    ("persist", "Persisting result"),
]


# ---- short-circuit status → human reason ---------------------------------
#
# An agent that hits a pre-flight gate sets ``status`` to one of these
# tokens and exits cleanly via a conditional edge to END. The frontend
# reads ``status`` off the terminal ``done`` event and renders the
# matching friendly message. Centralised here so the wording is owned
# by the agents (which understand the gate), not by each UI screen.
SKIP_REASONS: dict[str, str] = {
    "needs_documents": (
        "No documents uploaded yet — attach the loan packet first, "
        "then run intake again."
    ),
    "needs_extractions": (
        "No accepted extractions on this loan. Run intake first, or "
        "accept fields manually in the review queue."
    ),
    "needs_underwriting": (
        "Underwriting hasn't run yet. Run the underwriting agent first "
        "so the decision draft can anchor on its cited summary."
    ),
}


def skip_reason_for(status: str | None) -> str | None:
    """Return the human-readable reason for a short-circuit status, or
    None if the status is a normal completion."""
    if status is None:
        return None
    return SKIP_REASONS.get(status)


# ---- exception classification --------------------------------------------


def classify_exception(e: BaseException) -> tuple[str, str]:
    """Turn a raw exception into ``(reason, detail)``.

    ``reason`` is the user-facing one-liner — what to read first.
    ``detail`` is the supporting technical text — what to read second.
    Both go onto the SSE error event; the frontend collapses ``detail``
    behind a "show details" affordance.

    Heuristics are conservative on purpose: matching by class name +
    message substring rather than catching broad base classes, so we
    never disguise an unfamiliar exception as a known one.
    """
    cls = e.__class__.__name__
    raw = str(e) or repr(e)

    if cls == "LLMCallFailedError":
        # The gateway wraps the inner exception's message into our
        # error. We strip the leading "LLM call errored after N
        # attempts: " prefix when present so the reason reads cleanly.
        msg = raw.split(": ", 1)[-1] if ": " in raw else raw
        if "Could not resolve authentication" in msg or "401" in msg:
            return (
                "The AI service is rejecting requests — the Anthropic "
                "API key is missing or invalid.",
                "Set ANTHROPIC_API_KEY in api/.env and restart the server.",
            )
        if "model" in msg.lower() and (
            "not_found" in msg.lower() or "invalid" in msg.lower()
        ):
            return (
                "The configured Claude model isn't available. The "
                "model identifier in .env may be wrong or unreleased.",
                msg,
            )
        if "rate_limit" in msg.lower() or "429" in msg:
            return (
                "Anthropic rate-limited the request. Wait a moment "
                "and try again.",
                msg,
            )
        return ("The AI service errored after retries.", msg)

    if cls in ("ValidationError", "ValueError"):
        return ("Bad input to a step.", raw)
    if cls in ("OperationalError", "InterfaceError", "ConnectionError"):
        return ("Database connection failed.", raw)

    # Generic fallback. Still better than the raw class name dump.
    return (f"Agent step failed: {cls}.", raw)


def _failing_node_from_exception(e: BaseException) -> str | None:
    """LangGraph adds ``[NOTE] During task with name 'X' and id '...'``
    to a wrapped exception's ``args``. Parse that out when present so
    the SSE error can attribute the failure to a specific step. Falls
    back to None when the marker isn't present (older versions, plain
    exceptions)."""
    for arg in getattr(e, "args", ()):
        if not isinstance(arg, str):
            continue
        idx = arg.find("During task with name '")
        if idx != -1:
            tail = arg[idx + len("During task with name '") :]
            end = tail.find("'")
            if end > 0:
                return tail[:end]
    return None


def _next_node_after(
    nodes: list[tuple[str, str]], completed: str | None
) -> str | None:
    """Best-effort guess: the node that *would have run next* after the
    last completed one. Used when the exception doesn't tell us which
    step blew up — typically the step *after* the most recent
    ``node_complete`` is the one currently executing."""
    if not nodes:
        return None
    if completed is None:
        return nodes[0][0]
    for i, (key, _) in enumerate(nodes):
        if key == completed and i + 1 < len(nodes):
            return nodes[i + 1][0]
    return None


# ---- summary derivation -------------------------------------------------


def _summarise_node(node: str, delta: dict[str, Any]) -> str:
    """One short human-readable sentence for a node's state delta.

    The frontend renders this directly under the node's checkmark. Keep
    it specific — generic "extract complete" is worse than "extracted 7
    of 8 fields, 2 below confidence threshold".

    Unknown node / shape combinations fall back to a neutral string so
    the UI never breaks on a new field.
    """
    try:
        if node == "extract":
            n = len(delta.get("extracted_fields") or {})
            return f"Extracted {n} field{'' if n == 1 else 's'}"
        if node == "identify_missing":
            missing = delta.get("missing_fields") or []
            return (
                "All required fields present"
                if not missing
                else f"{len(missing)} required field"
                + ("" if len(missing) == 1 else "s")
                + " missing"
            )
        if node == "draft_request":
            if delta.get("status") == "complete":
                return "No outreach needed — packet is complete"
            return "Draft email prepared for review"
        if node == "approve":
            return "Underwriter approved the draft"
        if node == "send":
            return "Email sent to borrower"
        if node == "fetch_and_evaluate":
            flags = delta.get("flags") or []
            failed = sum(1 for f in flags if not f.get("passed", True))
            return (
                f"Ran {len(flags)} rule{'' if len(flags) == 1 else 's'}; "
                f"{failed} failing"
            )
        if node == "draft_summary":
            summary = delta.get("summary")
            if summary is not None:
                # Try a few shapes — Pydantic instance, dict, fallback
                rec = getattr(summary, "recommendation", None) or (
                    summary.get("recommendation") if isinstance(summary, dict) else None
                )
                if rec:
                    return f"Recommendation: {rec}"
            return "Cited summary drafted"
        if node == "draft_decision":
            decision = delta.get("decision")
            if decision is not None:
                path = getattr(decision, "path", None) or (
                    decision.get("path") if isinstance(decision, dict) else None
                )
                if path:
                    return f"Path: {path}"
            return "Decision package drafted"
        if node == "persist":
            return "Result saved to database"
    except Exception:  # noqa: BLE001 — summarisation is best-effort
        logger.exception("summary_derivation_failed", node=node)
    return "Done"


# ---- SSE framing --------------------------------------------------------


def _sse(event: str, data: Any) -> bytes:
    """Format one Server-Sent Event chunk.

    SSE expects bytes on the wire; FastAPI's StreamingResponse will pass
    bytes through unchanged. Newlines inside ``data`` are not allowed by
    the spec, so we ensure ``json.dumps`` emits single-line output.
    """
    payload = json.dumps(data, default=str, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


# Type alias for the graph-context-manager builder functions
# (``build_intake_graph`` etc.). They are async context managers that
# yield a compiled LangGraph; we accept the builder rather than the
# graph itself so the connection lifetime stays scoped to the stream.
GraphBuilder = Callable[[], Any]  # ``Any`` because the CM type is opaque


# ---- main streamer ------------------------------------------------------


async def stream_graph_run(
    *,
    build_graph: GraphBuilder,
    nodes: list[tuple[str, str]],
    initial_input: Any,
    config: dict[str, Any],
    thread_id: str,
    loan_id: uuid.UUID | str | None = None,
    agent_name: str | None = None,
    extract_result: Callable[[dict[str, Any]], Any] = lambda s: s,
    extract_interrupt: Callable[[dict[str, Any]], Any | None] = lambda s: None,
    final_status_key: str = "status",
    on_complete: Callable[[dict[str, Any], bool], Awaitable[None]] | None = None,
    replays_run_id: uuid.UUID | None = None,
) -> AsyncIterator[bytes]:
    """Run a graph and yield SSE-formatted events.

    ``initial_input`` is whatever you'd pass to ``graph.ainvoke()`` —
    typically a dict state on first run, or a ``Command(resume=...)`` to
    resume from a checkpoint. ``extract_result`` and ``extract_interrupt``
    let each agent reshape the final state into what the frontend
    expects (the synchronous endpoints had different return shapes; we
    preserve them here).

    Crashes inside the graph become an ``error`` SSE event and a clean
    stream close — the frontend handles re-runs, we never re-raise
    through the streaming generator.

    **Persistence for explainability.** When ``loan_id`` and
    ``agent_name`` are provided, we also:

    1. INSERT an ``AgentRun`` row (status=``running``) at the start of
       the stream. The id is stamped into ``initial_input`` as
       ``agent_run_id`` so the agent's persist node can update the
       same row instead of inserting a new one.
    2. INSERT an ``AgentStep`` row for every ``node_complete``,
       ``interrupt``, ``skipped``, and ``failed`` event. These rows
       are what the trace view renders.
    3. Enter ``agent_run_context(thread_id)`` for the whole window so
       any LLM gateway calls made downstream stamp their ``thread_id``
       and the trace view can join them to this run.
    4. UPDATE the AgentRun's ``status`` (``complete`` /
       ``interrupted`` / ``failed``) at the end.

    Older callers that don't pass loan_id/agent_name still work — the
    persistence is gated on having both. Used by the CI smoke tests
    that exercise the streamer without a real loan.
    """
    # Initial "started" event with the expected node list so the
    # frontend can render the progress shell before any node completes.
    yield _sse(
        "started",
        {
            "thread_id": thread_id,
            "nodes": [{"key": k, "label": label} for k, label in nodes],
        },
    )

    # ---- Persistence setup -------------------------------------------
    # Either both ``loan_id`` and ``agent_name`` are present (real
    # endpoint call) or neither (smoke test / future opt-out). We
    # gate the persistence on having both so the streamer keeps a
    # zero-DB-write mode for tests.
    persist = loan_id is not None and agent_name is not None
    agent_run_id: uuid.UUID | None = None
    if persist:
        agent_run_id = uuid.uuid4()
        # Initial payload — empty unless this run is a replay of an
        # earlier one, in which case the original run id lands here so
        # the drawer can render "Replays run X". This is the single
        # source of truth for replay linkage; we deliberately don't
        # add a column for it (payload is JSONB and queries are rare).
        initial_payload: dict[str, Any] = {}
        if replays_run_id is not None:
            initial_payload["replays_run_id"] = str(replays_run_id)
        try:
            async with get_session() as session:
                session.add(
                    AgentRun(
                        id=agent_run_id,
                        loan_id=loan_id,
                        agent_name=agent_name,
                        thread_id=thread_id,
                        status="running",
                        payload=initial_payload,
                    )
                )
        except Exception:  # noqa: BLE001
            logger.exception("agent_run_insert_failed", thread_id=thread_id)
            # Soft-fail — observability shouldn't block a run.
            agent_run_id = None

        # If the initial input is a dict and doesn't already carry an
        # agent_run_id, stamp ours in so the agent's persist node can
        # UPDATE the row we just created. The agents' state types
        # all include an ``agent_run_id`` field for this purpose.
        if (
            agent_run_id is not None
            and isinstance(initial_input, dict)
            and not initial_input.get("agent_run_id")
        ):
            initial_input = {**initial_input, "agent_run_id": str(agent_run_id)}

    final_state: dict[str, Any] | None = None
    seen_interrupt = False
    # Track the most recently completed node so when an exception
    # surfaces from inside the graph we can attribute it to whichever
    # step came next — that's the one currently in flight.
    last_completed_node: str | None = None
    # Per-node start timestamps so we can compute elapsed_ms for each
    # AgentStep. The first node starts when the stream opens; every
    # subsequent node starts when its predecessor's chunk arrives.
    node_started_at: dict[str, float] = {}
    if nodes:
        node_started_at[nodes[0][0]] = time.monotonic()

    try:
        with agent_run_context(thread_id):
            async with build_graph() as graph:
                # stream_mode="updates" yields {node_name: state_delta}
                # per completed node. The graph also emits its own
                # __interrupt__ key into a final values stream, but in
                # updates mode we detect interrupts via the final state
                # after the loop.
                async for chunk in graph.astream(
                    initial_input, config=config, stream_mode="updates"
                ):
                    if not isinstance(chunk, dict):
                        continue

                    # `updates` yields a dict keyed by the node that
                    # just finished. Usually one key per chunk.
                    for node, delta in chunk.items():
                        if node == "__interrupt__":
                            # LangGraph's interrupt payload arrives in
                            # several shapes depending on the version
                            # and streaming mode. ``_unwrap_interrupt``
                            # walks the common wrappers until it lands
                            # on the actual payload the agent passed
                            # to ``interrupt()``.
                            value = _unwrap_interrupt(delta)
                            if value is not None:
                                yield _sse("interrupt", value)
                                seen_interrupt = True
                                # Persist the interrupt as a step on
                                # the currently-active node so the
                                # trace view shows where the pause
                                # happened.
                                if persist:
                                    await _persist_step(
                                        agent_run_id=agent_run_id,
                                        node=last_completed_node
                                        or _next_node_after(nodes, None)
                                        or "interrupt",
                                        status="interrupt",
                                        summary="Paused for human approval",
                                        payload={"interrupt": _safe_json(value)},
                                        started_at=node_started_at.get(
                                            last_completed_node or "", time.monotonic()
                                        ),
                                    )
                            continue

                        label = next((lbl for k, lbl in nodes if k == node), node)
                        delta_dict = delta if isinstance(delta, dict) else {}
                        summary = _summarise_node(node, delta_dict)
                        last_completed_node = node
                        yield _sse(
                            "node_complete",
                            {"node": node, "label": label, "summary": summary},
                        )
                        if persist:
                            await _persist_step(
                                agent_run_id=agent_run_id,
                                node=node,
                                status="ok",
                                summary=summary,
                                payload=_step_payload(node, delta_dict),
                                started_at=node_started_at.get(
                                    node, time.monotonic()
                                ),
                            )
                        # Stamp the next node's start clock — best-effort,
                        # the topology in ``nodes`` is the source of
                        # truth.
                        next_node = _next_node_after(nodes, node)
                        if next_node:
                            node_started_at[next_node] = time.monotonic()

                # After the stream ends we may need to peek at the
                # latest checkpoint for an interrupt that wasn't
                # surfaced inline.
                try:
                    latest = await graph.aget_state(config)
                    final_state = latest.values if latest else None
                    if not seen_interrupt:
                        interrupt = _peek_interrupt(latest)
                        if interrupt is not None:
                            yield _sse("interrupt", interrupt)
                            seen_interrupt = True
                            if persist:
                                await _persist_step(
                                    agent_run_id=agent_run_id,
                                    node=last_completed_node
                                    or _next_node_after(nodes, None)
                                    or "interrupt",
                                    status="interrupt",
                                    summary="Paused for human approval",
                                    payload={"interrupt": _safe_json(interrupt)},
                                    started_at=time.monotonic(),
                                )
                except Exception:  # noqa: BLE001
                    logger.exception("final_state_fetch_failed", thread_id=thread_id)

    except Exception as e:
        logger.exception("agent_stream_failed", thread_id=thread_id)
        failing_node = _failing_node_from_exception(e) or _next_node_after(
            nodes, last_completed_node
        )
        reason, detail = classify_exception(e)
        yield _sse(
            "error",
            {
                "node": failing_node,
                "reason": reason,
                "detail": detail,
                # Legacy `message` field — older frontend bits may still
                # read it. Carries the same string as ``reason`` so they
                # don't break.
                "message": reason,
            },
        )
        if persist:
            await _persist_step(
                agent_run_id=agent_run_id,
                node=failing_node or "unknown",
                status="failed",
                summary=reason,
                payload={"error_reason": reason, "error_detail": detail},
                started_at=node_started_at.get(failing_node or "", time.monotonic()),
            )
            await _finalize_agent_run(agent_run_id, status="failed")
        return

    # The final "done" payload mirrors what the synchronous endpoint
    # used to return, so the frontend's existing post-run handlers
    # (cache invalidation, modal open) can keep working unchanged.
    state_dict = final_state or {}
    final_status: str = (
        "awaiting_approval"
        if seen_interrupt
        else state_dict.get(final_status_key) or "complete"
    )
    # If the run short-circuited at a pre-flight gate, surface that
    # plainly via a dedicated SSE event before ``done``. The frontend
    # uses this to render an explanatory banner instead of a generic
    # "complete" outcome with no work having happened.
    skip_msg = skip_reason_for(final_status)
    if skip_msg:
        yield _sse(
            "skipped",
            {"status": final_status, "reason": skip_msg},
        )
        # Persist the skip too — the trace view needs to show "the
        # agent declined to spend tokens because <reason>" with the
        # same fidelity as any other step.
        if persist:
            await _persist_step(
                agent_run_id=agent_run_id,
                node=last_completed_node or _next_node_after(nodes, None) or "skip",
                status="skipped",
                summary=skip_msg,
                payload={"final_status": final_status, "reason": skip_msg},
                started_at=time.monotonic(),
            )
    yield _sse(
        "done",
        {
            "thread_id": thread_id,
            "status": final_status,
            "interrupt": extract_interrupt(state_dict),
            "result": extract_result(state_dict),
            "skip_reason": skip_msg,
        },
    )

    # Finalize the AgentRun's status field so the observability view
    # reads "complete" / "interrupted" / "skipped" instead of the
    # stale "running" we stamped at the start.
    if persist:
        run_status = (
            "interrupted"
            if seen_interrupt
            else "skipped"
            if skip_msg
            else "complete"
        )
        await _finalize_agent_run(agent_run_id, status=run_status)

    # Post-completion hook — used by the autonomous orchestrator to
    # chain agents together when the loan is in autonomous mode.
    # Failures here are logged but never re-raised; the SSE response
    # is already closed by the time this runs from the client's POV.
    if on_complete is not None:
        try:
            await on_complete(state_dict, seen_interrupt)
        except Exception:  # noqa: BLE001
            logger.exception("agent_stream_on_complete_failed", thread_id=thread_id)


def _peek_interrupt(state_snapshot: Any) -> Any | None:
    """Pull an interrupt payload off a LangGraph state snapshot, if any.

    The shape varies a bit by LangGraph version: ``state.tasks`` may
    contain an ``Interrupt`` instance with a ``.value`` attribute, or
    the snapshot's ``next`` field points at an interrupted node. We
    look for the most common case and fall through quietly otherwise.
    """
    if state_snapshot is None:
        return None
    tasks = getattr(state_snapshot, "tasks", None) or []
    for t in tasks:
        interrupts = getattr(t, "interrupts", None) or []
        for it in interrupts:
            value = getattr(it, "value", None)
            if value is not None:
                return value
    return None


def _unwrap_interrupt(delta: Any) -> Any | None:
    """Strip LangGraph's wrappers off an interrupt update to get the
    payload the agent originally passed to ``interrupt(...)``.

    Across LangGraph versions we've seen, the value at
    ``chunk["__interrupt__"]`` can be:

    1. a ``tuple``/``list`` containing one ``Interrupt`` (newer 1.x —
       this is what triggered the IntakeApprovalModal crash; the old
       extractor only handled ``list`` and let the tuple fall through
       to JSON as ``[{value: ..., id: ..., ns: ...}]``);
    2. a bare ``Interrupt`` instance (older 0.x);
    3. a dict shaped like ``{"value": <payload>, "id": ...}`` (some
       serialised paths);
    4. or the raw payload dict the agent passed directly (test doubles).

    We unwrap one layer at a time and bail out as soon as we have
    something that's neither a sequence nor an Interrupt-like object.
    Returns ``None`` when nothing usable was found — callers should
    treat that as "no interrupt this chunk" rather than as the value.
    """
    cur = delta
    # Cap the depth: there's no realistic case where we need more than
    # three unwraps, but be paranoid against pathological wrappers.
    for _ in range(4):
        if cur is None:
            return None
        # (Interrupt, ...) / [Interrupt, ...] — take the first; an
        # empty sequence means no interrupt.
        if isinstance(cur, (tuple, list)):
            if not cur:
                return None
            cur = cur[0]
            continue
        # Interrupt-like object (``.value`` attribute).
        if hasattr(cur, "value") and not isinstance(cur, dict):
            cur = cur.value
            continue
        # A dict with a "value" key that itself looks like an
        # interrupt wrapper (has ``id``/``ns``/``resumable``) — strip
        # it. We do NOT strip "value" off a payload-shaped dict (one
        # that has e.g. a ``type`` key) because the agent might
        # legitimately put a "value" key in its payload.
        if (
            isinstance(cur, dict)
            and "value" in cur
            and any(k in cur for k in ("id", "ns", "resumable"))
        ):
            cur = cur["value"]
            continue
        # Anything else — assume we've reached the payload.
        return cur
    return cur


# Silence overly-chatty SQLAlchemy/asyncpg logs during the stream so the
# server console stays readable during interactive debugging. Module-level
# so it applies once at import.
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)


# ---- step persistence helpers ------------------------------------------


async def _persist_step(
    *,
    agent_run_id: uuid.UUID | None,
    node: str,
    status: str,
    summary: str | None,
    payload: dict[str, Any],
    started_at: float,
) -> None:
    """Insert one ``AgentStep`` row. Best-effort — DB errors are logged
    but never re-raised, so observability writes can't break a run.

    ``started_at`` is a ``time.monotonic()`` reading captured when the
    node became active; we convert it to ``elapsed_ms`` here. The
    ``completed_at`` timestamp is "now".

    After inserting, we backfill ``llm_calls.parent_step_id`` for any
    calls that happened during this step's wall-clock window. The
    streaming layer can't bind the step id via ContextVar ahead of
    time (LangGraph's astream doesn't give us a node-entry hook, and
    setting an FK to a row that doesn't exist yet would fail the
    INSERT), so we link the parent post-hoc. The (thread_id,
    started_at, ended_at) trio is enough to attribute each call to
    exactly one step — calls are timestamped at completion and a
    step's interval is non-overlapping with sibling steps' intervals.
    """
    if agent_run_id is None:
        return
    ended_at = datetime.now(UTC)
    elapsed_ms = max(0, int((time.monotonic() - started_at) * 1000))
    # Wall-clock window over which calls "inside" this step were
    # recorded. We computed elapsed_ms above; subtract it from
    # ``ended_at`` to get a clock-time start. Slight skew (~ms) is
    # fine; LLM calls don't fire at clock-tick granularity.
    window_start = ended_at - timedelta(milliseconds=elapsed_ms)
    try:
        async with get_session() as session:
            step = AgentStep(
                agent_run_id=agent_run_id,
                node=node,
                status=status,
                summary=summary,
                completed_at=ended_at,
                elapsed_ms=elapsed_ms,
                payload=payload,
            )
            session.add(step)
            await session.flush()  # need step.id for the backfill

            # Backfill llm_calls.parent_step_id. Match by thread_id +
            # time window. ``parent_step_id IS NULL`` guards against
            # double-attribution (a call near a node boundary could
            # otherwise be re-assigned by the next step's backfill).
            thread_id = await _thread_id_for_run(session, agent_run_id)
            if thread_id is not None:
                # Modest fuzz on the window edges (50ms each side) so
                # calls that started just before the node was marked
                # "started" or completed slightly after still get
                # attributed correctly.
                fuzz = timedelta(milliseconds=50)
                await session.execute(
                    sa_update(LLMCall)
                    .where(
                        LLMCall.thread_id == thread_id,
                        LLMCall.parent_step_id.is_(None),
                        LLMCall.created_at >= window_start - fuzz,
                        LLMCall.created_at <= ended_at + fuzz,
                    )
                    .values(parent_step_id=step.id)
                )
    except Exception:  # noqa: BLE001
        logger.exception(
            "agent_step_insert_failed",
            agent_run_id=str(agent_run_id),
            node=node,
            status=status,
        )


async def _thread_id_for_run(
    session: AsyncSession, agent_run_id: uuid.UUID
) -> str | None:
    """Read ``agent_runs.thread_id`` so the backfill knows which
    llm_calls belong to this run.

    Cheap one-row lookup. Returns ``None`` if the run row has gone
    missing (shouldn't happen mid-stream but we guard for it).
    """
    row = (
        await session.execute(
            select(AgentRun.thread_id).where(AgentRun.id == agent_run_id)
        )
    ).scalar_one_or_none()
    return row


async def _finalize_agent_run(
    agent_run_id: uuid.UUID | None, *, status: str
) -> None:
    """Flip the AgentRun's ``status`` from ``running`` to the terminal
    state. The agent's own persist node may have set a richer status
    via its update path (``complete`` carries the same meaning), so we
    only overwrite when the column still reads ``running``.

    Best-effort like ``_persist_step``.
    """
    if agent_run_id is None:
        return
    try:
        async with get_session() as session:
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == agent_run_id,
                    AgentRun.status == "running",
                )
                .values(status=status)
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "agent_run_finalize_failed",
            agent_run_id=str(agent_run_id),
            status=status,
        )


def _step_payload(node: str, delta: dict[str, Any]) -> dict[str, Any]:
    """Distil the LangGraph state delta from one node into a small,
    PII-safe payload suitable for the trace view.

    Per-node curation matters more than a generic dump: the
    ``extract_all_documents`` node carries a giant ``extracted_fields``
    dict that's interesting (the values are PII) but verbose, while
    ``identify_missing`` carries a tiny ``missing_fields`` list that's
    perfect. We pull the useful summary fields out, cap collections at
    a reasonable size, and drop anything that looks like raw document
    text.
    """
    out: dict[str, Any] = {}
    if "missing_fields" in delta:
        mf = delta.get("missing_fields") or []
        if isinstance(mf, (list, tuple)):
            out["missing_fields"] = list(mf)[:20]
    if "extracted_fields" in delta:
        ef = delta.get("extracted_fields") or {}
        if isinstance(ef, dict):
            out["extracted_field_count"] = len(ef)
            out["extracted_field_names"] = sorted(ef.keys())[:20]
    if "rule_outcomes" in delta:
        ros = delta.get("rule_outcomes") or []
        if isinstance(ros, (list, tuple)):
            out["rule_count"] = len(ros)
            out["rules_failed"] = sum(
                1 for r in ros if not getattr(r, "passed", True)
            )
    if "status" in delta and isinstance(delta["status"], str):
        out["node_status"] = delta["status"]
    return out


def _safe_json(value: Any) -> Any:
    """Round-trip ``value`` through ``json.dumps`` so the caller knows
    it'll survive the JSONB column. Falls back to ``str(value)`` for
    anything pickier than the default encoder accepts.

    We use this for interrupt payloads which may contain Pydantic
    models or dataclasses the JSONB column doesn't auto-coerce.
    """
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001
        return {"repr": repr(value)[:512]}
