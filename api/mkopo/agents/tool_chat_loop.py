"""Shared tool-using chat loop. Powers both ``borrower_chat`` and
``staff_chat``.

What both surfaces need:

  - Append the user's message (or tool_resume) to the running
    transcript.
  - Loop: ask the LLM (with role-filtered tools bound) → if the
    response is text, emit it; if it's a tool call, execute or
    pause for confirmation.
  - Emit SSE events: ``thinking``, ``message``, ``tool_call``,
    ``tool_result``, ``confirm_required``, ``done``, ``error``.

What's different per surface:

  - **Role** (filters the tool list)
  - **System prompt** (borrower-facing vs staff-facing tone)
  - **Audit detail** (borrower chat audits the literal user
    message verbatim — compliance gold; staff chat doesn't — too
    much PII volume for too little incremental audit value)

We accept those as parameters and otherwise share the body. The
SSE event shape is identical on the wire so a single client-side
reader handles both.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import structlog

from mkopo.agents.tools import (
    ToolContext,
    ToolError,
    get_tool,
    to_anthropic_tools,
    tools_for_role,
)
from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.llm_gateway import get_gateway
from mkopo.models import ToolUse
from mkopo.services.audit import Actor, record

logger = structlog.get_logger()


_MAX_ITERATIONS = 6
"""Cap on consecutive tool-call rounds in a single turn."""


def _sse(event: str, data: Any) -> bytes:
    payload = json.dumps(data, default=str, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _summarise_args(args: dict[str, Any]) -> str:
    if not args:
        return "(no arguments)"
    out = " · ".join(f"{k}: {v}" for k, v in args.items())
    return out[:200] + ("…" if len(out) > 200 else "")


async def run_chat_turn(
    *,
    user_id: uuid.UUID,
    user_email: str,
    user_role: str,
    loan_id: uuid.UUID,
    messages: list[dict[str, Any]],
    user_message: str | None,
    tool_resume: dict[str, Any] | None,
    system_prompt: str,
    audit_chat_message: bool,
) -> AsyncGenerator[bytes, None]:
    """Run one chat turn end-to-end. Yields SSE-formatted bytes.

    The caller's responsibility is just auth + tool-role binding;
    everything below this entrypoint is uniform across surfaces.
    """
    settings = get_settings()
    gateway = get_gateway()

    tools = tools_for_role(user_role)
    anthropic_tools = to_anthropic_tools(tools)

    messages = list(messages)  # don't mutate caller's list

    if user_message:
        messages.append({"role": "user", "content": user_message})
        if audit_chat_message:
            # Borrower side: audit literal text (compliance value).
            # Staff side opts out via the flag.
            async with get_session() as session:
                await record(
                    session,
                    loan_id=loan_id,
                    actor=Actor.borrower(user_email)
                    if user_role == "borrower"
                    else Actor.user(str(user_id)),
                    action="borrower_chat_message"
                    if user_role == "borrower"
                    else "staff_chat_message",
                    payload={
                        "text": user_message[:1000],
                        "at": datetime.now(UTC).isoformat(),
                    },
                )
                await session.commit()

    if tool_resume:
        # Resuming after a confirmation. Either we execute the held
        # tool (action=confirm) or synthesise a cancellation (else).
        # ``call_id`` is echoed back from the client (it came in on
        # the ``confirm_required`` event); it links the resulting
        # ``tool_uses`` row back to the LLM call that originally
        # asked for the tool.
        held_call_id = tool_resume.get("call_id")
        if tool_resume.get("action") == "confirm":
            async for chunk in _execute_one_tool(
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
                loan_id=loan_id,
                tool_use_id=tool_resume["tool_use_id"],
                tool_name=tool_resume["name"],
                tool_input=tool_resume.get("input") or {},
                messages=messages,
                llm_call_id=held_call_id,
                sequence_num=0,
            ):
                yield chunk
        else:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_resume["tool_use_id"],
                            "content": (
                                "User cancelled this action. Acknowledge "
                                "and ask what they'd like to do instead."
                            ),
                            "is_error": True,
                        }
                    ],
                }
            )
            yield _sse(
                "tool_result",
                {
                    "id": tool_resume["tool_use_id"],
                    "ok": False,
                    "error": "Cancelled by user",
                },
            )
            # Record the cancellation in tool_uses too — the
            # observability drawer otherwise wouldn't show that the
            # user actively refused, only that the tool wasn't run.
            await _persist_tool_use(
                llm_call_id=held_call_id,
                loan_id=loan_id,
                tool_name=tool_resume["name"],
                sequence_num=0,
                tool_input=tool_resume.get("input") or {},
                output=None,
                status="cancelled",
                error_message="User cancelled at confirmation prompt",
                elapsed_ms=0,
            )

    # Main loop.
    for _ in range(_MAX_ITERATIONS):
        yield _sse("thinking", {})
        try:
            response = await gateway.call_with_tools(
                model=settings.llm_default_model,
                system=system_prompt,
                messages=messages,
                tools=anthropic_tools,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "chat_llm_failed", user_role=user_role, loan_id=str(loan_id)
            )
            yield _sse(
                "error",
                {
                    "reason": "The assistant ran into an unexpected error.",
                    "detail": f"{type(e).__name__}: {e}",
                },
            )
            return

        messages.append(response.assistant_message)

        if response.text:
            yield _sse(
                "message",
                {"role": "assistant", "text": response.text},
            )

        if not response.tool_calls:
            yield _sse("done", {"messages": messages})
            return

        for sequence_num, tc in enumerate(response.tool_calls):
            tool = get_tool(tc.name)
            if tool is None:
                yield _sse(
                    "tool_result",
                    {"id": tc.id, "ok": False, "error": f"Unknown tool: {tc.name}"},
                )
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": f"Unknown tool: {tc.name}",
                                "is_error": True,
                            }
                        ],
                    }
                )
                continue

            # Role gate — defence in depth even after the
            # tools_for_role filter at the top.
            if user_role not in tool.roles:
                yield _sse(
                    "tool_result",
                    {"id": tc.id, "ok": False, "error": "Not permitted"},
                )
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": "Not permitted",
                                "is_error": True,
                            }
                        ],
                    }
                )
                continue

            if tool.is_destructive:
                yield _sse(
                    "confirm_required",
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "args": tc.input,
                        "human_action": tool.human_action,
                        "summary": _summarise_args(tc.input),
                        "messages": messages,
                        # Pass the LLM call's id back to the client so
                        # the eventual tool_resume can echo it. Links
                        # the persisted tool_uses row to the original
                        # LLM call once the user confirms.
                        "call_id": response.call_id,
                    },
                )
                return

            async for chunk in _execute_one_tool(
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
                loan_id=loan_id,
                tool_use_id=tc.id,
                tool_name=tc.name,
                tool_input=tc.input,
                messages=messages,
                llm_call_id=response.call_id,
                sequence_num=sequence_num,
            ):
                yield chunk

    # Safety-budget exit.
    yield _sse(
        "error",
        {
            "reason": "The assistant got stuck in a tool-call loop.",
            "detail": (
                f"Hit the {_MAX_ITERATIONS}-iteration safety cap. Try a "
                "more specific question, or escalate."
            ),
        },
    )


async def _execute_one_tool(
    *,
    user_id: uuid.UUID,
    user_email: str,
    user_role: str,
    loan_id: uuid.UUID,
    tool_use_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    messages: list[dict[str, Any]],
    llm_call_id: str | None = None,
    sequence_num: int = 0,
) -> AsyncGenerator[bytes, None]:
    """Execute a single tool requested by the LLM. Streams SSE events
    + persists a ``tool_uses`` row for the observability drawer.

    ``llm_call_id`` is the UUID of the LLM call that asked for this
    tool. Passed through so the persisted ``tool_uses`` row can join
    back to its parent LLM call — without this, the observability
    drawer can't reconstruct "the agent called X, then Y, then Z".
    """
    tool = get_tool(tool_name)
    if tool is None:
        return

    yield _sse(
        "tool_call",
        {
            "id": tool_use_id,
            "name": tool_name,
            "args": tool_input,
            "human_action": tool.human_action,
        },
    )

    started_at = datetime.now(UTC)
    status: str = "ok"
    output: dict[str, Any] | None = None
    error_message: str | None = None

    async with get_session() as session:
        try:
            try:
                validated = tool.schema(**tool_input)
            except Exception as e:  # noqa: BLE001
                raise ToolError(f"Invalid arguments: {e}") from e

            ctx = ToolContext(
                session=session,
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
                loan_id=loan_id,
            )
            result = await tool.handler(ctx, validated)
            await session.commit()
            output = result if isinstance(result, dict) else {"result": result}
            yield _sse(
                "tool_result",
                {"id": tool_use_id, "ok": True, "result": result},
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(result, default=str),
                        }
                    ],
                }
            )
        except ToolError as e:
            await session.rollback()
            status = "error"
            error_message = str(e)
            yield _sse(
                "tool_result",
                {"id": tool_use_id, "ok": False, "error": str(e)},
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": str(e),
                            "is_error": True,
                        }
                    ],
                }
            )
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            status = "error"
            error_message = f"{type(e).__name__}: {e}"
            logger.exception(
                "tool_handler_unexpected",
                tool=tool_name,
                user_role=user_role,
            )
            yield _sse(
                "tool_result",
                {
                    "id": tool_use_id,
                    "ok": False,
                    "error": "Something went wrong on our end.",
                },
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": f"Tool raised: {type(e).__name__}",
                            "is_error": True,
                        }
                    ],
                }
            )

    # Persist the trajectory in its own session — the tool's own
    # session may have rolled back on error, and we still want the
    # observability row to land. Never raise from this path; if
    # observability is broken the chat must still complete.
    elapsed_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
    await _persist_tool_use(
        llm_call_id=llm_call_id,
        loan_id=loan_id,
        tool_name=tool_name,
        sequence_num=sequence_num,
        tool_input=tool_input,
        output=output,
        status=status,
        error_message=error_message,
        elapsed_ms=elapsed_ms,
    )


async def _persist_tool_use(
    *,
    llm_call_id: str | None,
    loan_id: uuid.UUID,
    tool_name: str,
    sequence_num: int,
    tool_input: dict[str, Any],
    output: dict[str, Any] | None,
    status: str,
    error_message: str | None,
    elapsed_ms: int,
) -> None:
    """Best-effort write of one ``tool_uses`` row. Used by both the
    auto-execute path and the cancellation/resume paths so every
    tool invocation lands on disk."""
    try:
        async with get_session() as session:
            session.add(
                ToolUse(
                    llm_call_id=uuid.UUID(llm_call_id) if llm_call_id else None,
                    loan_id=loan_id,
                    tool_name=tool_name,
                    sequence_num=sequence_num,
                    input=tool_input,
                    output=output,
                    status=status,
                    error_message=error_message,
                    elapsed_ms=elapsed_ms,
                )
            )
            await session.commit()
    except Exception:
        logger.exception(
            "tool_use_persist_failed",
            tool=tool_name,
            llm_call_id=llm_call_id,
        )
