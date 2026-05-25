"""Per-run context for the agent stack.

Why a ContextVar instead of threading values through call signatures:
the LLM gateway (``mkopo.llm_gateway``) deliberately doesn't know
about agents — it's a thin wrapper over the Anthropic SDK that any
caller can use. But for observability we need every LLM call made
*inside* an agent run to carry that run's ``thread_id`` so the
trace UI can show "these 7 calls happened during this run."

Plumbing ``thread_id`` through every call site would either pollute
the gateway's signature or require every agent node to read state
and pass the field down. A ``ContextVar`` solves it cleanly:

- ``stream_graph_run`` enters the ``current_run`` context when it
  starts running an agent.
- ``LLMGateway._record_call`` reads ``current_thread_id()`` when it
  writes a row. If the gateway is being called from outside any run
  (CI eval gate, ad-hoc smoke test), the var is None and the column
  stays null. No special-casing required.

Async-safe: ``ContextVar`` is the canonical asyncio mechanism for
passing per-task context. asyncio.TaskGroup, asyncpg, sqlalchemy
async — all of them carry the active context across ``await`` so
the gateway sees the same value the streaming layer set.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# ``None`` outside any agent run; a UUID string while a run is active.
_current_thread_id: ContextVar[str | None] = ContextVar(
    "mkopo_current_thread_id", default=None
)
# ``None`` outside any step; a UUID string while a step is executing.
# Stacks under thread_id — a run binds thread_id at the start, then
# each node binds the step id around its execution. Calls made inside
# the node carry both; calls made outside any node (rare —
# orchestrator-level helpers) carry only thread_id.
_current_step_id: ContextVar[str | None] = ContextVar(
    "mkopo_current_step_id", default=None
)


def current_thread_id() -> str | None:
    """The LangGraph thread id of the currently-active agent run, if any."""
    return _current_thread_id.get()


def current_step_id() -> str | None:
    """The agent_steps.id of the currently-executing node, if any.

    Read by ``LLMGateway._record_call`` to populate
    ``llm_calls.parent_step_id`` so the AgentRunDrawer can nest LLM
    calls under their owning step. Pairs with :func:`current_thread_id`
    — thread_id is the run; step_id is the node inside it.
    """
    return _current_step_id.get()


@contextmanager
def agent_run_context(thread_id: str) -> Iterator[None]:
    """Bind ``thread_id`` for the duration of a ``with`` block.

    Used by the streaming layer to mark the run window so any
    downstream LLM gateway calls attribute themselves to this run.

    Don't ``await`` outside the ``with`` and expect the binding to
    persist — once the block exits, ``current_thread_id()`` flips
    back to whatever it was before (usually ``None``).
    """
    token = _current_thread_id.set(thread_id)
    try:
        yield
    finally:
        _current_thread_id.reset(token)


@contextmanager
def agent_step_context(step_id: str) -> Iterator[None]:
    """Bind the current ``agent_steps.id`` for the duration of a node.

    Wrap a node's body in this so any LLM calls it makes get
    ``parent_step_id`` set on their llm_calls row. Nests cleanly
    under :func:`agent_run_context` — the var is independent so
    leaving this block leaves the thread_id binding intact.
    """
    token = _current_step_id.set(step_id)
    try:
        yield
    finally:
        _current_step_id.reset(token)
