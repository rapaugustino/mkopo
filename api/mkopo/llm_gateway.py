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
from typing import TypeVar

import structlog
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from pydantic import BaseModel, ValidationError

from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.models.eval import LLMCall

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


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
                    )
                    raise LLMCallFailedError(
                        f"LLM output failed schema validation after {attempt + 1} attempts",
                        attempts=attempt + 1,
                        last_error=last_error,
                    ) from e
                current_user = self._build_correction_prompt(user, text, e)  # type: ignore[arg-type]

            except Exception as e:
                logger.exception("llm_call_error", call_id=call_id, model=model)
                if attempt == max_retries:
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
                    )
                    raise LLMCallFailedError(
                        f"LLM call errored after {attempt + 1} attempts",
                        attempts=attempt + 1,
                        last_error=str(e),
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
    ) -> None:
        """Persist one LLM call to ``llm_calls`` and log via structlog.

        DB failures here must never break the calling agent — we already
        completed (or failed) the LLM call by the time we get here, and
        the gateway's contract is that audit is best-effort. We log the
        exception and move on.

        ``system_prompt_hash`` is sha256 so we can group by prompt
        without storing potentially sensitive prompt content.
        """
        system_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()

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
        )

        try:
            async with get_session() as session:
                session.add(
                    LLMCall(
                        model=model,
                        system_prompt_hash=system_hash,
                        elapsed_seconds=elapsed_seconds,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        status=status,
                        schema_name=schema_name,
                        attempt=attempt,
                    )
                )
        except Exception:
            # Don't let observability break the calling agent.
            logger.exception("llm_call_persist_failed", call_id=call_id)


# Module-level singleton — import this everywhere
_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
