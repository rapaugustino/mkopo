"""FastAPI app entrypoint.

Run locally:
    uv run uvicorn mkopo.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from mkopo.config import get_settings
from mkopo.db import get_engine
from mkopo.routers import (
    agents,
    borrower_auth,
    borrower_chat,
    borrower_loans,
    borrower_portal,
    documents,
    evals,
    loans,
    observability,
    parties,
    review,
    safety,
    search,
    staff_auth,
    staff_chat,
    storage_proxy,
)
from mkopo.routers import (
    prompts as prompts_router,
)
from mkopo.routers import (
    settings as settings_router,
)
from mkopo.services.error_capture import persist_uncaught_exception
from mkopo.startup_check import run_startup_checks


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level))
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
            if settings.is_production
            else structlog.dev.ConsoleRenderer(),
        ],
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    logger = structlog.get_logger()
    settings = get_settings()
    logger.info("app_starting", environment=settings.environment)

    # Print the integration-status banner before anything else so a
    # fresh deployer sees missing config on first boot rather than
    # debugging a "500 Internal Server Error" from a downstream call.
    run_startup_checks(settings)

    # Telemetry — must come after the engine is created (it instruments it).
    from mkopo.telemetry import setup_telemetry

    setup_telemetry(app)

    # Prompt registry — write v1 of every code default into the
    # ``prompts`` table on first boot against a fresh DB, then warm
    # the process-level cache so the first agent run after start-up
    # already gets active bodies. Idempotent: subsequent boots see
    # the rows already there and only warm the cache. A failure
    # here doesn't take down the app — the cache stays empty and
    # ``prompts.get()`` falls through to the in-process defaults,
    # which is correct fallback behaviour.
    try:
        from mkopo.db import get_session
        from mkopo.services.prompts import (
            ensure_defaults_seeded,
        )
        from mkopo.services.prompts import (
            refresh_cache as refresh_prompt_cache,
        )

        async with get_session() as session:
            n = await ensure_defaults_seeded(session)
            await session.commit()
            if n:
                logger.info("prompts_seeded", count=n)
            await refresh_prompt_cache(session)
            logger.info("prompts_cache_warmed")
    except Exception as exc:  # pragma: no cover — startup nicety only
        logger.warning("prompts_seed_failed", error=str(exc)[:200])

    yield
    logger.info("app_shutting_down")


app = FastAPI(
    title="Mkopo",
    description="AI-first loan origination for private lenders.",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Capture uncaught exceptions into ``infrastructure_errors`` so the
# Observability / Eval pages can surface "what's broken". Mounted at
# ``Exception`` so anything not handled by an earlier registered
# handler (HTTPException, RequestValidationError) lands here. The
# handler itself is bullet-proofed against persist failures — see
# error_capture.persist_uncaught_exception.
app.add_exception_handler(Exception, persist_uncaught_exception)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    """Process is alive. Used by load balancers / Kubernetes liveness probe."""
    return {"status": "ok", "service": "mkopo", "version": "0.1.0"}


@app.get("/health", tags=["health"])
@app.get("/health/ready", tags=["health"])
async def readiness(response: Response) -> dict[str, object]:
    """Process is alive AND its dependencies are reachable. Used by readiness probe.

    Returns 503 if Postgres is unreachable so a load balancer can route traffic
    elsewhere. Liveness deliberately stays simple and never depends on the DB.
    """
    logger = structlog.get_logger()
    checks: dict[str, dict[str, str]] = {}
    overall_ok = True

    # Postgres
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = {"status": "ok"}
    except Exception as e:
        overall_ok = False
        checks["postgres"] = {"status": "error", "error": str(e)[:200]}
        logger.error("health_check_failed", component="postgres", error=str(e))

    # Redis. Auth-side uses it for JWT-blacklist + rate-limit checks,
    # both of which degrade open on Redis failure (see ``redis_client``).
    # An outage shouldn't yank the pod from the LB, but operators want
    # to see "degraded" surfaced loudly so they chase it.
    from mkopo.services.redis_client import ping as redis_ping

    if await redis_ping():
        checks["redis"] = {"status": "ok"}
    else:
        checks["redis"] = {"status": "degraded", "note": "auth runs degraded-open"}
        logger.warning("health_check_redis_unreachable")

    if not overall_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if overall_ok else "degraded",
        "service": "mkopo",
        "version": "0.1.0",
        "checks": checks,
    }


app.include_router(loans.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(parties.router, prefix="/api/v1")
app.include_router(review.router, prefix="/api/v1")
app.include_router(evals.router, prefix="/api/v1")
app.include_router(observability.router, prefix="/api/v1")
app.include_router(safety.router, prefix="/api/v1")
app.include_router(prompts_router.router, prefix="/api/v1")
app.include_router(borrower_auth.router, prefix="/api/v1")
app.include_router(borrower_loans.router, prefix="/api/v1")
app.include_router(borrower_chat.router, prefix="/api/v1")
app.include_router(borrower_portal.router, prefix="/api/v1")
app.include_router(staff_auth.router, prefix="/api/v1")
app.include_router(staff_chat.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(settings_router.router, prefix="/api/v1")
# Local-storage HTTP proxy — only meaningful when STORAGE_BACKEND=local.
# Production S3 backend mints its own URLs and bypasses this route.
app.include_router(storage_proxy.router, prefix="/api/v1")
