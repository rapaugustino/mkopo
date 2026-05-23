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
    borrower_portal,
    documents,
    evals,
    loans,
    observability,
    parties,
    review,
    staff_chat,
)
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
app.include_router(borrower_auth.router, prefix="/api/v1")
app.include_router(borrower_chat.router, prefix="/api/v1")
app.include_router(borrower_portal.router, prefix="/api/v1")
app.include_router(staff_chat.router, prefix="/api/v1")
