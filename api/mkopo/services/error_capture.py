"""Server-error capture — best-effort persist of 5xx exceptions.

Mounted as a FastAPI exception handler so anything an endpoint
raises that isn't an :class:`fastapi.HTTPException` (those are
intentional 4xx/5xx responses with a known shape) lands in
``infrastructure_errors`` for later forensics.

The handler runs *in the request lifecycle*, after the endpoint has
already failed. Two guard rails:

  1. **Don't break the response.** If the persist fails (DB down,
     fk violation, anything), swallow and continue. The client must
     still get a 500 — we just lose the row.
  2. **Don't recurse.** A persist failure here can't itself trip
     the handler. We catch ``Exception`` broadly around the DB
     write and log via structlog only.

User correlation: the handler tries to pull a user id from the
request state (set by the auth dependency when it runs successfully).
If the request failed *before* auth ran, that's null — fine.
"""

from __future__ import annotations

import traceback
import uuid
from typing import Any

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse

from mkopo.db import get_session
from mkopo.models.errors import InfrastructureError

logger = structlog.get_logger()


# Truncate the traceback so a runaway one doesn't blow up the column.
# 8 KB is more than enough for a 30-frame stack with locals; anything
# beyond that is probably an infinite-recursion bug and the first
# 8 KB will tell you that on its own.
_MAX_TRACEBACK_CHARS = 8000


async def persist_uncaught_exception(
    request: Request, exc: Exception
) -> JSONResponse:
    """FastAPI exception handler. Records the error, returns a 500.

    Wired into the app in ``main.py``. Anything not handled by an
    earlier handler (HTTPException, RequestValidationError, …) ends
    up here.
    """
    error_class = type(exc).__name__
    error_message = str(exc) or "(no message)"

    tb_text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    if len(tb_text) > _MAX_TRACEBACK_CHARS:
        tb_text = (
            tb_text[:_MAX_TRACEBACK_CHARS]
            + f"\n... (truncated, full {len(tb_text)} chars)"
        )

    # Try to pull user id off request state. The auth dependency
    # writes it on success; for requests that died before auth ran
    # we leave it null. Same for the structured request id if the
    # tracing middleware set one.
    user_id: uuid.UUID | None = None
    try:
        raw_user_id = getattr(request.state, "user_id", None)
        if isinstance(raw_user_id, str):
            user_id = uuid.UUID(raw_user_id)
        elif isinstance(raw_user_id, uuid.UUID):
            user_id = raw_user_id
    except Exception:
        user_id = None

    request_id: str | None = getattr(request.state, "request_id", None)

    # Log first — even if the DB write fails, the structured log
    # captures the incident. Keep traceback fields compact.
    logger.exception(
        "uncaught_exception",
        path=request.url.path,
        method=request.method,
        error_class=error_class,
        error_message=error_message[:200],
        user_id=str(user_id) if user_id else None,
        request_id=request_id,
    )

    # Persist. Best-effort — we already have to return a 500.
    try:
        async with get_session() as session:
            session.add(
                InfrastructureError(
                    path=request.url.path[:512],
                    method=request.method[:16],
                    status_code=500,
                    error_class=error_class[:128],
                    error_message=error_message[:1024],
                    traceback=tb_text,
                    user_id=user_id,
                    request_id=request_id[:64] if request_id else None,
                )
            )
            await session.commit()
    except Exception:
        # Don't recurse. The exception handler can't itself throw.
        logger.exception("error_capture_persist_failed")

    # Mirror FastAPI's default 500 body — clients don't get extra
    # detail from us beyond what they had before.
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


# Convenience for tests that want to assert "this error landed in the
# table" without monkeypatching the handler.
async def record_error(
    *,
    path: str,
    method: str,
    status_code: int,
    error_class: str,
    error_message: str,
    traceback_text: str | None,
    user_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> None:
    """Persist one error row programmatically.

    Used by:

    - The exception handler (above).
    - Future targeted error paths (worker failures, scheduled-job
      crashes) that want to surface in the same UI.
    """
    async with get_session() as session:
        session.add(
            InfrastructureError(
                path=path[:512],
                method=method[:16],
                status_code=status_code,
                error_class=error_class[:128],
                error_message=error_message[:1024],
                traceback=traceback_text,
                user_id=user_id,
                request_id=request_id[:64] if request_id else None,
            )
        )
        await session.commit()


# Mypy hint shim — the handler signature FastAPI expects.
HandlerType = Any
