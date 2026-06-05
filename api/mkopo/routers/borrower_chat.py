"""Borrower-side chat endpoint. Bound to the cookie-authed borrower
identity, scoped to one loan, role-bound to ``"borrower"`` tools.

Implementation lives in ``mkopo.agents.tool_chat_loop`` — this
router is thin glue. See ``staff_chat.py`` for the symmetrical
staff-side surface.

SSE event protocol (mirrored on both surfaces):

  - ``thinking``         — the agent is calling the LLM
  - ``message``          — assistant prose; ``{text}``
  - ``tool_call``        — non-destructive tool executing;
                           ``{id, name, args, human_action}``
  - ``tool_result``      — that tool's result; ``{id, ok, result|error}``
  - ``confirm_required`` — destructive tool waiting for the user;
                           stream closes. Client resumes via a new
                           POST with ``tool_resume``.
  - ``done``             — clean end-of-turn; carries the updated
                           ``messages`` history for the client.
  - ``error``            — terminal; ``{reason, detail}``.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mkopo.agents.tool_chat_loop import run_chat_turn
from mkopo.deps import CurrentBorrowerDep

router = APIRouter(prefix="/borrower-auth", tags=["borrower-chat"])


class ToolResume(BaseModel):
    tool_use_id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    action: str  # "confirm" | "cancel"
    # UUID of the LLM call that issued the original tool request.
    # Echoed back from the ``confirm_required`` SSE event so the
    # persisted ``tool_uses`` row can link to it. Optional for
    # backwards compat — old clients still work, just with a null FK.
    call_id: str | None = None
    # Fresh password-challenge token, minted via
    # POST /borrower-auth/me/challenge. Required when the resumed
    # tool is marked ``requires_reauth=True`` (currently
    # withdraw_application + request_erasure). The chat loop
    # consumes the token; if it's missing or invalid the tool does
    # not execute. See #169.
    challenge_token: str | None = Field(default=None, max_length=128)


class ChatRequest(BaseModel):
    loan_id: uuid.UUID
    messages: list[dict[str, Any]] = Field(default_factory=list)
    user_message: str | None = None
    tool_resume: ToolResume | None = None


# System prompt now lives in the ``prompts`` table; loaded per-turn
# via prompts.get(). The identifier is kept as a constant so the
# call-site reads obviously and refactors are search-friendly.
_PROMPT_ID = "chat.borrower.system"


def _get_borrower_system_prompt() -> str:
    """Indirection so test fixtures can monkeypatch the prompt source.

    Equivalent to ``prompts.get(_PROMPT_ID)`` in production. Kept as
    a function rather than calling ``get`` at call-site so the lazy
    import below doesn't repeat on every chat turn.
    """
    from mkopo.services.prompts import get as get_prompt

    return get_prompt(_PROMPT_ID)


@router.post("/me/chat/stream")
async def chat_stream(payload: ChatRequest, user: CurrentBorrowerDep) -> StreamingResponse:
    """Run one chat turn for the signed-in borrower and stream the
    result as SSE."""

    async def generate():
        async for chunk in run_chat_turn(
            user_id=user.id,
            user_email=user.email,
            user_role="borrower",
            loan_id=payload.loan_id,
            messages=payload.messages,
            user_message=payload.user_message,
            tool_resume=payload.tool_resume.model_dump() if payload.tool_resume else None,
            system_prompt=_get_borrower_system_prompt(),
            audit_chat_message=True,  # compliance value — borrower side audits literal text
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")
