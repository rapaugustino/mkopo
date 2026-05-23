"""Tool catalog — the canonical mutation surface for agent-mediated actions.

Why a registry, not loose functions
-----------------------------------

The chat agent shouldn't be able to call arbitrary Python. It can
only call tools that are registered here, and a tool's *roles* list
gates who's allowed to call it. That means:

  - Borrower-side chat can only invoke tools with ``"borrower"`` in
    their roles. Even if the LLM hallucinates an internal tool name,
    the registry refuses.
  - The same registry serves staff-side chat in Phase 4, with the
    role filter swapped — one tool layer, two products.
  - Adding a new agentable action is a code change in this file
    (and only this file). Reviewable.

Each tool carries three pieces of metadata the agent loop needs:

  - **schema** — Pydantic model describing the args. Becomes the
    Anthropic ``input_schema`` for the tool, so the model can
    only call with valid arguments.
  - **roles** — set of role strings allowed to invoke this tool.
  - **is_destructive** — True for tools that mutate or surface
    sensitive data. The agent loop pauses on an interrupt so the
    user has to confirm before the tool actually runs.

Plus one runtime piece:

  - **handler** — ``async def(ctx, args) → result``. Returns a
    JSON-serialisable dict the agent will see as the tool result.
    Raises ``ToolError`` for handled failures (the agent sees the
    message); raises anything else for unhandled bugs (the agent
    sees a generic error, real diagnostics land in observability).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


class ToolError(Exception):
    """Raised by a tool handler for caller-visible failures.

    Anything the borrower / staff should see ("loan not found",
    "this loan is closed and can't be modified") goes through here.
    Unhandled exceptions look like bugs to the agent and land in
    observability with full traces.
    """


@dataclass
class ToolContext:
    """Runtime context handed to every tool handler.

    ``user`` is the calling identity (a borrower User or a staff
    user, depending on the chat surface). ``loan_id`` is the loan
    the chat is scoped to — most borrower tools want it; some
    (data export, erasure) operate on the whole account and ignore
    it. ``session`` is the SQLAlchemy async session — the handler
    can read or mutate freely; the caller commits.
    """

    session: AsyncSession
    user_id: uuid.UUID
    user_email: str
    user_role: str
    loan_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Tool:
    """One callable tool in the registry."""

    name: str
    description: str
    schema: type[BaseModel]
    roles: frozenset[str]
    is_destructive: bool
    handler: Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]
    # Short imperative phrase the UI shows during the agent's "I'm
    # about to call this tool" interrupt, e.g. "Withdraw this loan
    # application". Kept separate from ``description`` (which is
    # for the LLM) because the LLM-facing description tends to be
    # third-person and explanatory.
    human_action: str = ""


# Module-level registry. ``register`` decorates handler functions.
_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    """Add ``tool`` to the registry. Idempotent — re-registering the
    same name replaces the existing entry (helpful for tests + hot
    reload). Returns the tool so the caller can keep a reference."""
    _REGISTRY[tool.name] = tool
    return tool


def get_tool(name: str) -> Tool | None:
    """Lookup by name. ``None`` if unknown — the agent loop treats
    that as a tool-not-found error and surfaces it to the LLM."""
    return _REGISTRY.get(name)


def tools_for_role(role: str) -> list[Tool]:
    """Subset of the registry visible to ``role``. The chat
    endpoint binds this list when it starts an agent run, so the
    LLM only sees tools the caller is allowed to invoke. Even if
    the LLM hallucinates a tool name outside this set, the
    handler-lookup at execution time refuses."""
    return [t for t in _REGISTRY.values() if role in t.roles]


def to_anthropic_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    """Render ``tools`` into the Anthropic tool-use API shape.

    Anthropic's ``tools=[{name, description, input_schema}]`` is
    what the model sees when deciding which tool to call. The
    ``input_schema`` is the tool's Pydantic schema rendered as
    JSON Schema; we deliberately drop the ``title`` field
    Pydantic adds (Anthropic prefers a clean spec).
    """
    out: list[dict[str, Any]] = []
    for t in tools:
        js = t.schema.model_json_schema()
        js.pop("title", None)
        out.append(
            {
                "name": t.name,
                "description": t.description,
                "input_schema": js,
            }
        )
    return out


# Trigger the per-surface tool registrations. Side-effect imports —
# ordering matters: the registries are populated at import time.
# Both surfaces share the same registry; the ``roles`` filter on
# each tool keeps borrower vs staff scoped at the lookup boundary.
from mkopo.agents.tools import borrower as _borrower  # noqa: E402,F401
from mkopo.agents.tools import staff as _staff  # noqa: E402,F401

__all__ = [
    "Tool",
    "ToolContext",
    "ToolError",
    "get_tool",
    "register",
    "to_anthropic_tools",
    "tools_for_role",
]
