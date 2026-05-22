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


def current_thread_id() -> str | None:
    """The LangGraph thread id of the currently-active agent run, if any."""
    return _current_thread_id.get()


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
