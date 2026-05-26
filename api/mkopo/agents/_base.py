"""Shared infrastructure for the LangGraph agents.

The three agents (intake, underwriting, decision) each ended their
module with the same boilerplate:

.. code-block:: python

    async with AsyncPostgresSaver.from_conn_string(
        settings.database_url_libpq, serde=make_serializer()
    ) as checkpointer:
        await checkpointer.setup()
        yield builder.compile(checkpointer=checkpointer)

That's the only piece of the agent build-up that was genuinely
identical across the three; factoring it here means a future change
(e.g. swapping to a different checkpointer backend, adding a
trace-id propagation step) lands in one place.

Not factored: the per-agent ``persist`` nodes. They each have
agent-specific payload shapes + side effects (decision deletes
conditions, underwriting updates ``loan.risk_band`` + the
comparable-loans embedding). A parameterized ``persist_agent_run``
helper would need so many callable hooks that it would be longer
than the duplication it removes — better to leave them as-is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import StateGraph

from mkopo.agents._serde import make_serializer
from mkopo.config import get_settings


@asynccontextmanager
async def build_compiled_graph(
    builder: StateGraph,
) -> AsyncIterator[Any]:
    """Wrap a built :class:`StateGraph` with a Postgres checkpointer
    and yield the compiled graph.

    The checkpointer is an async context manager that owns a database
    connection, so the compiled graph is only valid inside the
    ``async with`` block — callers must scope every invocation:

    .. code-block:: python

        async with build_compiled_graph(builder) as graph:
            result = await graph.ainvoke(state, config=config)

    Uses the libpq-format DSN, not the SQLAlchemy one (psycopg
    rejects the ``+psycopg`` suffix). The custom serializer
    allowlists ``mkopo.schemas`` symbols so LangGraph stops warning
    about unregistered deserializations and keeps working when the
    default flips to strict — see ``_serde.py`` for the rationale.
    """
    settings = get_settings()
    async with AsyncPostgresSaver.from_conn_string(
        settings.database_url_libpq, serde=make_serializer()
    ) as checkpointer:
        await checkpointer.setup()  # idempotent
        yield builder.compile(checkpointer=checkpointer)
