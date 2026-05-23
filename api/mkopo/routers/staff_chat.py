"""Internal staff chat — the agent surface for underwriters + admins.

Same on-the-wire protocol as the borrower chat (Phase 3); the
differences are entirely in the binding:

  - **Auth**: bearer token via ``CurrentUserDep`` rather than the
    borrower's session cookie.
  - **Tools**: bound by the caller's ``user.role`` — an
    underwriter sees the underwriter tool set, an admin sees the
    admin tool set, anyone else gets zero tools (the chat will
    work in read-only-prose mode but won't take any actions).
  - **Loan scope**: the chat is opened on a specific loan, same as
    the borrower side; tools without a loan_id arg default to that
    scope.

The two routers (``borrower_chat.py`` + this one) share enough
structure that we factored the shared SSE framing + iteration loop
into a helper module:
``mkopo.agents.tool_chat_loop.run_chat_turn``. That keeps any
protocol change (new event type, safety budget tweak, audit
field) flowing into both surfaces.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mkopo.agents.tool_chat_loop import run_chat_turn
from mkopo.deps import CurrentUserDep

router = APIRouter(prefix="/staff", tags=["staff-chat"])


class ToolResume(BaseModel):
    tool_use_id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    action: str  # "confirm" | "cancel"


class StaffChatRequest(BaseModel):
    loan_id: uuid.UUID
    messages: list[dict[str, Any]] = Field(default_factory=list)
    user_message: str | None = None
    tool_resume: ToolResume | None = None


_STAFF_SYSTEM_PROMPT = """You are Mkopo's internal underwriting copilot.
You help staff (underwriters, loan officers, admins) operate on a loan
in the pipeline.

You have access to a set of tools matched to the caller's role. Use
them whenever the staff member asks to look up data, modify the
loan, or message the borrower — don't make up information.

Style: direct, concise, no apologies. You're speaking to an expert
who knows the domain — skip the explainer copy ("DSCR is the ratio
of NOI to debt service…") unless they explicitly ask.

For destructive actions — overriding an extraction, advancing the
loan to a new stage, sending a borrower-visible message — you MUST
call the appropriate tool. The system pauses before the action runs
and asks the user to confirm. Don't try to confirm yourself in
prose; the tool catalog handles confirmation. Just propose the
action by calling the tool.

If asked something outside the tool list, say so plainly. Don't
fabricate audit history, decision rationales, or rule outcomes —
pull them via tools, or admit you can't see them."""


@router.post("/chat/stream")
async def staff_chat_stream(
    payload: StaffChatRequest, user: CurrentUserDep
) -> StreamingResponse:
    """Run one staff chat turn; stream the result as SSE.

    Same protocol as ``/borrower-auth/me/chat/stream`` — clients
    can re-use the same SSE reader if they pass the right URL +
    auth mode. See ``web/lib/agentChat.ts``.
    """

    async def generate():
        async for chunk in run_chat_turn(
            user_id=uuid.UUID(user.user_id) if _is_uuid(user.user_id) else uuid.uuid4(),
            user_email=user.user_id,  # bearer dev-user has no real email
            user_role="underwriter",  # TODO: derive from real user row
            loan_id=payload.loan_id,
            messages=payload.messages,
            user_message=payload.user_message,
            tool_resume=payload.tool_resume.model_dump() if payload.tool_resume else None,
            system_prompt=_STAFF_SYSTEM_PROMPT,
            audit_chat_message=False,  # staff chat doesn't audit its raw text
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False
