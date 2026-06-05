"""Schema-gated LLM gateway. All LLM calls in the system flow through this.

Guarantees:
- Every output is validated against a Pydantic schema.
- Schema-validation failures are retried with corrective feedback.
- Every call (success or failure) is logged for audit.
- Bounded retries prevent runaway behavior.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar

import structlog
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from pydantic import BaseModel, ValidationError

from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.models.eval import LLMCall

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ToolCall:
    """A single tool-use block from the LLM. Anthropic's tool-use API
    returns these inside the assistant's content; the agent loop
    executes each and feeds the result back as a ``tool_result``."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolUseResponse:
    """Decoded shape of one tool-use round-trip.

    ``text`` is the assistant's prose when present (None if the
    model went straight to a tool call). ``tool_calls`` is the list
    of tools the model wants invoked — usually 1, sometimes 0,
    occasionally multiple parallel calls when the LLM chooses to
    fan out. ``assistant_message`` is the raw turn ready to be
    appended to the messages array for the next round.
    ``stop_reason`` tells the agent loop whether to keep going
    (``"tool_use"``) or stop (``"end_turn"``).

    ``call_id`` is the UUID of the persisted ``llm_calls`` row for
    this turn. The chat loop links every ``tool_uses`` row it
    writes back to this call_id, so the observability drawer can
    render the tool sequence under the LLM call that issued it.
    """

    text: str | None
    call_id: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None


class LLMCallFailedError(Exception):
    """Raised when the LLM gateway exhausts retries without a valid response."""

    def __init__(self, message: str, attempts: int, last_error: str) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class LLMGateway:
    """The single chokepoint for all LLM calls.

    Usage:
        gateway = LLMGateway()
        result: BorrowerExtraction = await gateway.call_structured(
            model="claude-haiku-4-5-20251001",
            system="Extract the borrower's legal name.",
            user=document_text,
            schema=BorrowerExtraction,
        )
    """

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._client = AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        self._settings = settings

    async def call_structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        max_retries: int = 2,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        call_id: str | None = None,
    ) -> T:
        """Call the LLM and validate the response against `schema`.

        Returns a typed instance of `schema`.
        Raises `LLMCallFailedError` if the model output cannot be coerced after retries.
        """
        call_id = call_id or str(uuid.uuid4())
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        full_system = (
            f"{system}\n\n"
            f"You MUST respond with valid JSON that conforms to this schema:\n"
            f"```json\n{schema_json}\n```\n\n"
            f"Return ONLY the JSON object, no prose, no markdown fences."
        )

        current_user = user
        last_error = ""

        for attempt in range(max_retries + 1):
            started_at = time.monotonic()
            try:
                response: AnthropicMessage = await self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=full_system,
                    messages=[{"role": "user", "content": current_user}],
                )
                elapsed = time.monotonic() - started_at
                text = self._extract_text(response)
                result = schema.model_validate_json(self._strip_fences(text))

                await self._record_call(
                    call_id=call_id,
                    model=model,
                    system=system,
                    user=user,
                    response_text=text,
                    schema_name=schema.__name__,
                    status="ok",
                    attempt=attempt,
                    elapsed_seconds=elapsed,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
                return result

            except ValidationError as e:
                last_error = str(e)
                logger.warning(
                    "llm_schema_validation_failed",
                    call_id=call_id,
                    model=model,
                    attempt=attempt,
                    error=last_error[:500],
                )
                if attempt == max_retries:
                    # Pydantic's str(e) is the full multi-line pretty-
                    # printed validation error. The short reason gets
                    # the count + first field; the long detail keeps
                    # the whole thing so operators can see exactly
                    # which fields failed and how.
                    err_count = len(e.errors()) if hasattr(e, "errors") else 1
                    first_loc = (
                        e.errors()[0].get("loc", ()) if hasattr(e, "errors") and e.errors() else ()
                    )
                    first_field = ".".join(str(p) for p in first_loc) or "?"
                    short_reason = (
                        f"Schema validation failed ({err_count} "
                        f"{'error' if err_count == 1 else 'errors'}, "
                        f"first at {first_field})"
                    )
                    await self._record_call(
                        call_id=call_id,
                        model=model,
                        system=system,
                        user=user,
                        response_text=text if "text" in locals() else "",
                        schema_name=schema.__name__,
                        status="schema_failed",
                        attempt=attempt,
                        elapsed_seconds=time.monotonic() - started_at,
                        error_reason=short_reason,
                        error_detail=last_error,
                    )
                    raise LLMCallFailedError(
                        f"LLM output failed schema validation after {attempt + 1} attempts",
                        attempts=attempt + 1,
                        last_error=last_error,
                    ) from e
                current_user = self._build_correction_prompt(user, text, e)  # type: ignore[arg-type]

            except Exception as e:
                logger.exception("llm_call_error", call_id=call_id, model=model)
                # Preserve the inner error message — many SDK errors
                # have an empty ``str(e)`` but a useful ``repr(e)`` or
                # ``.message`` attribute. Try the richer accessors first
                # so the downstream LLMCallFailedError carries something
                # the UI can show. Without this, the user sees a generic
                # "LLM call errored after N attempts" with no clue why.
                inner = getattr(e, "message", None) or str(e) or repr(e) or e.__class__.__name__
                last_error = str(inner)
                if attempt == max_retries:
                    # Short reason = exception class + inner message;
                    # long detail = repr() which carries SDK-level
                    # structured info (status code, request id, etc.)
                    # — useful for distinguishing 401-auth from 429-
                    # rate-limited from 500-server-error in the
                    # observability inspector.
                    short_reason = f"{e.__class__.__name__}: {last_error}"
                    await self._record_call(
                        call_id=call_id,
                        model=model,
                        system=system,
                        user=user,
                        response_text="",
                        schema_name=schema.__name__,
                        status="error",
                        attempt=attempt,
                        elapsed_seconds=time.monotonic() - started_at,
                        error_reason=short_reason,
                        error_detail=repr(e),
                    )
                    raise LLMCallFailedError(
                        f"LLM call errored after {attempt + 1} attempts: {last_error}",
                        attempts=attempt + 1,
                        last_error=last_error,
                    ) from e

        raise LLMCallFailedError("Unreachable", attempts=0, last_error="")  # for type checker

    @staticmethod
    def _extract_text(response: AnthropicMessage) -> str:
        for block in response.content:
            if hasattr(block, "text"):
                return block.text  # type: ignore[no-any-return]
        return ""

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove ```json ... ``` wrappers if the model added them."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # drop opening fence
            lines = lines[1:]
            # drop closing fence if present
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    @staticmethod
    def _build_correction_prompt(
        original_user: str,
        bad_response: str,
        error: ValidationError,
    ) -> str:
        return (
            f"{original_user}\n\n"
            f"Your previous response did not conform to the required schema:\n"
            f"```\n{bad_response[:1000]}\n```\n\n"
            f"Validation error:\n{error}\n\n"
            f"Return ONLY a valid JSON object that conforms to the schema."
        )

    async def call_with_tools(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        call_id: str | None = None,
    ) -> ToolUseResponse:
        """Multi-turn chat with Anthropic tool-use.

        Returns a :class:`ToolUseResponse` carrying:

          - the assistant's ``text`` content (None if the model
            chose to call a tool instead of replying with prose)
          - any ``tool_calls`` the model wants the caller to execute
          - the raw assistant turn (id, role, content) — the agent
            loop needs this to keep building the conversation history
          - the ``stop_reason`` so the caller can tell "model wants a
            tool result" (``tool_use``) from "model is done"
            (``end_turn``)

        The agent loop is then: call → if tool_calls, execute each,
        append a ``tool_result`` user-turn, call again. Loop until
        ``stop_reason == "end_turn"`` or a safety budget runs out.

        We log + persist via ``_record_call`` like ``call_structured``
        does. The status string is ``"tool_use"`` when the model
        called tools, ``"ok"`` when it produced text. ``schema_name``
        is set to ``"tool_use:" + tool_names`` so the observability
        table can filter for tool-using calls cleanly.
        """
        call_id = call_id or str(uuid.uuid4())
        # ``schema_name`` is VARCHAR(128) on llm_calls. With 8+ tools
        # in the staff/borrower chat catalogs the joined names blow
        # past that and the call's persist INSERT fails with
        # StringDataRightTruncationError — taking down the chat turn
        # even though the tool call itself succeeded. Cap at a safe
        # margin under 128 (we prepend "tool_use:" later, ~10 chars).
        schema_name_max = 110
        all_names = ",".join(t["name"] for t in tools) if tools else ""
        tool_names = (
            (all_names[: schema_name_max - 1] + "…")
            if len(all_names) > schema_name_max
            else all_names
        )
        started_at = time.monotonic()
        try:
            response: AnthropicMessage = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                tools=tools,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            )
        except Exception as e:
            elapsed = time.monotonic() - started_at
            inner = getattr(e, "message", None) or str(e) or repr(e) or e.__class__.__name__
            await self._record_call(
                call_id=call_id,
                model=model,
                system=system,
                user="",  # multi-turn — full transcript not single-string
                response_text="",
                schema_name=f"tool_use:{tool_names}" if tool_names else "tool_use",
                status="error",
                attempt=0,
                elapsed_seconds=elapsed,
                error_reason=f"{e.__class__.__name__}: {inner}",
                error_detail=repr(e),
            )
            raise LLMCallFailedError(
                f"LLM tool-use call failed: {inner}",
                attempts=1,
                last_error=str(inner),
            ) from e
        elapsed = time.monotonic() - started_at

        # Extract text + tool_use blocks. Anthropic's response.content
        # is a list of blocks: text blocks have ``.text``, tool_use
        # blocks have ``.id``, ``.name``, ``.input``.
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        input=dict(getattr(block, "input", {}) or {}),
                    )
                )

        # Best-effort serialise the raw assistant turn into the dict
        # shape the next request needs as a message. Anthropic's SDK
        # returns the typed object; we reconstruct the JSON-ish
        # form to feed back in.
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": getattr(b, "text", "")}
                if getattr(b, "type", "") == "text"
                else {
                    "type": "tool_use",
                    "id": getattr(b, "id", ""),
                    "name": getattr(b, "name", ""),
                    "input": dict(getattr(b, "input", {}) or {}),
                }
                for b in response.content
            ],
        }

        await self._record_call(
            call_id=call_id,
            model=model,
            system=system,
            user="",  # multi-turn — see note above
            response_text="\n".join(text_parts),
            schema_name=f"tool_use:{tool_names}" if tool_names else "tool_use",
            status="tool_use" if tool_calls else "ok",
            attempt=0,
            elapsed_seconds=elapsed,
            input_tokens=getattr(response.usage, "input_tokens", None),
            output_tokens=getattr(response.usage, "output_tokens", None),
        )

        return ToolUseResponse(
            text="\n".join(text_parts) if text_parts else None,
            call_id=call_id,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
            stop_reason=getattr(response, "stop_reason", None),
        )

    async def _record_call(
        self,
        *,
        call_id: str,
        model: str,
        system: str,
        user: str,
        response_text: str,
        schema_name: str | None,
        status: str,
        attempt: int,
        elapsed_seconds: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_reason: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Persist one LLM call to ``llm_calls`` and log via structlog.

        DB failures here must never break the calling agent — we already
        completed (or failed) the LLM call by the time we get here, and
        the gateway's contract is that audit is best-effort. We log the
        exception and move on.

        ``system_prompt_hash`` is sha256 so we can group by prompt
        without storing potentially sensitive prompt content.

        ``error_reason`` / ``error_detail`` populate on failure rows so
        the observability inspector can show *why* a call broke. Both
        stay ``None`` for successful calls.
        """
        system_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()

        # Compute the per-call dollar cost. Both halves stay ``None``
        # when the model isn't in the pricing registry — the rollup
        # endpoints filter on IS NOT NULL so an unknown-model call
        # doesn't show up as $0 (which would understate the bill).
        from mkopo.services.pricing import compute_cost

        cost_in, cost_out = compute_cost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        # Note: we deliberately don't log `user` (could be a whole
        # document) or `response_text` (could be PII) — only metadata.
        logger.info(
            "llm_call",
            call_id=call_id,
            model=model,
            system_prompt_hash=system_hash,
            schema_name=schema_name,
            status=status,
            attempt=attempt,
            elapsed_seconds=round(elapsed_seconds, 3),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_input_usd=float(cost_in) if cost_in is not None else None,
            cost_output_usd=float(cost_out) if cost_out is not None else None,
            error_reason=error_reason,
        )

        try:
            async with get_session() as session:
                session.add(
                    LLMCall(
                        # Pin the row PK to the upstream call_id so
                        # tool_uses + (future) parent_step refs can
                        # link by the same UUID we logged. Without
                        # this, the structlog ``call_id`` and the row
                        # ``llm_calls.id`` are two different UUIDs
                        # and you can't join them.
                        id=uuid.UUID(call_id),
                        model=model,
                        system_prompt_hash=system_hash,
                        elapsed_seconds=elapsed_seconds,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_input_usd=cost_in,
                        cost_output_usd=cost_out,
                        status=status,
                        schema_name=schema_name,
                        attempt=attempt,
                        error_reason=_truncate(error_reason, 256),
                        error_detail=_truncate(error_detail, 4096),
                        # ContextVar — populated when this call is
                        # made inside ``agent_run_context``. Lets the
                        # observability UI group calls per run.
                        # Lazy-import to dodge a circular: agents.context
                        # is part of mkopo.agents, whose package __init__
                        # imports decision.py which imports this gateway.
                        thread_id=_current_thread_id(),
                        # ContextVar set by ``mkopo.services.prompts.get``.
                        # Stamps the registry row that produced the
                        # system prompt body. ``None`` for ad-hoc calls
                        # that didn't go through the registry. The
                        # observability page joins on this to surface
                        # "which prompt version produced this call".
                        prompt_version_id=_current_prompt_version_id_safe(),
                        # ``parent_step_id`` is intentionally left
                        # null here. The streaming layer's
                        # ``_persist_step`` writes the step row
                        # *after* the node completes, so by the time
                        # we'd want to set this column the calls have
                        # already happened and the step's row doesn't
                        # yet exist (FK violation). Instead the
                        # streaming layer backfills this column once
                        # the step row lands, time-window matching
                        # llm_calls against the step's [start, end]
                        # interval. Calls outside any step (eval CI,
                        # ad-hoc utilities) stay null.
                    )
                )
        except Exception:
            # Don't let observability break the calling agent.
            logger.exception("llm_call_persist_failed", call_id=call_id)


def _current_thread_id() -> str | None:
    """Lazy accessor for the agent context's thread id. Wrapped in a
    local helper because importing ``mkopo.agents.context`` at module
    top-level triggers ``mkopo.agents.__init__`` → ``decision.py`` →
    back into this module, a classic circular-import deadlock.
    """
    from mkopo.agents.context import current_thread_id

    return current_thread_id()


def _current_prompt_version_id_safe() -> uuid.UUID | None:
    """Lazy accessor for the active prompt version id ContextVar.
    Wrapped in a defensive try/except so a botched import or a bare
    in-process call (no registry loaded yet) doesn't break the
    persist path — observability is best-effort by design."""
    try:
        from mkopo.services.prompts import current_prompt_version_id

        return current_prompt_version_id()
    except Exception:
        return None


def _truncate(s: str | None, max_len: int) -> str | None:
    """Clamp a string to ``max_len`` characters, appending an ellipsis
    when the original was longer so the consumer can tell it was cut.
    Returns ``None`` unchanged so the column stays null on success."""
    if s is None:
        return None
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


# Module-level singleton — import this everywhere
_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
