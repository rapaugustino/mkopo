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
    # UUID of the LLM call that issued the original tool request.
    # Echoed back from the ``confirm_required`` SSE event so the
    # persisted ``tool_uses`` row can link to it. Optional for
    # backwards compat.
    call_id: str | None = None
    # Threaded through for symmetry with the borrower-side resume
    # shape (see borrower_chat.ToolResume); no staff tool currently
    # sets ``requires_reauth=True`` so the field is unused on this
    # surface, but keeping it on the model means a future
    # staff-side reauth-gated action (e.g. force-close) doesn't
    # need a protocol change.
    challenge_token: str | None = Field(default=None, max_length=128)


class StaffChatRequest(BaseModel):
    loan_id: uuid.UUID
    messages: list[dict[str, Any]] = Field(default_factory=list)
    user_message: str | None = None
    tool_resume: ToolResume | None = None


# System prompt now lives in the ``prompts`` table; loaded per-turn
# via prompts.get(). The identifier is kept as a constant so the
# call-site reads obviously and refactors are search-friendly.
_PROMPT_ID = "chat.staff.system"


def _get_staff_system_prompt() -> str:
    """Indirection so test fixtures can monkeypatch the prompt source."""
    from mkopo.services.prompts import get as get_prompt

    return get_prompt(_PROMPT_ID)


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
            # ``user.role`` comes off the CurrentUser dataclass (set in
            # ``mkopo.routers.auth.require_user``). The tool registry
            # in ``mkopo.agents.tools.staff`` uses it to filter which
            # tools the LLM can see, so passing it through is what
            # actually enforces RBAC at the agent boundary.
            user_role=user.role,
            loan_id=payload.loan_id,
            messages=payload.messages,
            user_message=payload.user_message,
            tool_resume=payload.tool_resume.model_dump() if payload.tool_resume else None,
            system_prompt=_get_staff_system_prompt(),
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
