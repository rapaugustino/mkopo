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
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import structlog

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
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


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
    extract_result: Callable[[dict[str, Any]], Any] = lambda s: s,
    extract_interrupt: Callable[[dict[str, Any]], Any | None] = lambda s: None,
    final_status_key: str = "status",
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

    final_state: dict[str, Any] | None = None
    seen_interrupt = False

    try:
        async with build_graph() as graph:
            # stream_mode="updates" yields {node_name: state_delta} per
            # completed node. The graph also emits its own __interrupt__
            # key into a final values stream, but in updates mode we
            # detect interrupts via the final state after the loop.
            async for chunk in graph.astream(
                initial_input, config=config, stream_mode="updates"
            ):
                if not isinstance(chunk, dict):
                    continue

                # `updates` yields a dict keyed by the node that just
                # finished. Usually one key per chunk.
                for node, delta in chunk.items():
                    if node == "__interrupt__":
                        # Some LangGraph versions surface the interrupt
                        # here; handle either path.
                        interrupt_value = (
                            delta[0] if isinstance(delta, list) and delta else delta
                        )
                        yield _sse(
                            "interrupt",
                            getattr(interrupt_value, "value", interrupt_value),
                        )
                        seen_interrupt = True
                        continue

                    label = next((lbl for k, lbl in nodes if k == node), node)
                    summary = _summarise_node(node, delta if isinstance(delta, dict) else {})
                    yield _sse(
                        "node_complete",
                        {"node": node, "label": label, "summary": summary},
                    )

            # After the stream ends we may need to peek at the latest
            # checkpoint for an interrupt that wasn't surfaced inline.
            try:
                latest = await graph.aget_state(config)
                final_state = latest.values if latest else None
                if not seen_interrupt:
                    interrupt = _peek_interrupt(latest)
                    if interrupt is not None:
                        yield _sse("interrupt", interrupt)
                        seen_interrupt = True
            except Exception:  # noqa: BLE001
                logger.exception("final_state_fetch_failed", thread_id=thread_id)

    except Exception as e:
        logger.exception("agent_stream_failed", thread_id=thread_id)
        yield _sse("error", {"message": str(e)})
        return

    # The final "done" payload mirrors what the synchronous endpoint
    # used to return, so the frontend's existing post-run handlers
    # (cache invalidation, modal open) can keep working unchanged.
    state_dict = final_state or {}
    yield _sse(
        "done",
        {
            "thread_id": thread_id,
            "status": (
                "awaiting_approval"
                if seen_interrupt
                else state_dict.get(final_status_key) or "complete"
            ),
            "interrupt": extract_interrupt(state_dict),
            "result": extract_result(state_dict),
        },
    )


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


# Silence overly-chatty SQLAlchemy/asyncpg logs during the stream so the
# server console stays readable during interactive debugging. Module-level
# so it applies once at import.
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
