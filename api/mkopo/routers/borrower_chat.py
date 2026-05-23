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


class ChatRequest(BaseModel):
    loan_id: uuid.UUID
    messages: list[dict[str, Any]] = Field(default_factory=list)
    user_message: str | None = None
    tool_resume: ToolResume | None = None


_BORROWER_SYSTEM_PROMPT = """You are Mkopo's borrower-side assistant. You
help the signed-in borrower understand and act on their loan application.

You have access to a set of tools. Use them whenever the borrower asks
about their application's status, documents, missing fields, or
decision reasoning — don't guess from prior conversation context.

For destructive actions (withdrawing an application, updating a field,
requesting erasure), you MUST call the appropriate tool. The system
will pause before the action runs and ask the borrower to confirm.
Don't try to confirm yourself by asking "are you sure?" in prose;
the tool catalog handles confirmation. Just propose the action by
calling the tool.

Style: warm, concise, no boilerplate. Refer to the borrower in
second person ("your application", "you can…"). Don't restate what
a tool returned verbatim — summarise. If a tool fails or returns
an error, explain plainly and suggest a sensible next step.

You cannot read another borrower's data, see internal staff notes,
or take actions outside the tool list. If asked, say so plainly."""


@router.post("/me/chat/stream")
async def chat_stream(
    payload: ChatRequest, user: CurrentBorrowerDep
) -> StreamingResponse:
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
            system_prompt=_BORROWER_SYSTEM_PROMPT,
            audit_chat_message=True,  # compliance value — borrower side audits literal text
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")
