"""Arq background workers.

Run with:
    uv run arq mkopo.workers.tasks.WorkerSettings
"""

from __future__ import annotations

from typing import Any

import structlog
from arq import cron
from arq.connections import RedisSettings

from mkopo.agents import build_intake_graph
from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.services.drift import run_drift_monitor

logger = structlog.get_logger()


async def run_intake_for_loan(ctx: dict[str, Any], loan_id: str) -> dict[str, Any]:
    """Background task: kick off the intake agent for a loan.

    The agent may pause via interrupt() — that's fine, the checkpointer persists state
    and the API can resume it later.
    """
    logger.info("worker_intake_start", loan_id=loan_id)
    thread_id = f"intake-{loan_id}"
    config = {"configurable": {"thread_id": thread_id}}
    state = {"loan_id": loan_id, "status": "running"}
    try:
        async with build_intake_graph() as graph:
            result = await graph.ainvoke(state, config=config)
        logger.info("worker_intake_complete", loan_id=loan_id, status=result.get("status"))
        return {"loan_id": loan_id, "status": result.get("status")}
    except Exception as e:
        logger.exception("worker_intake_failed", loan_id=loan_id)
        return {"loan_id": loan_id, "status": "failed", "error": str(e)}


async def drift_monitor(ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly drift monitor — runs at 3 AM UTC.

    Delegates to ``mkopo.services.drift.run_drift_monitor`` which samples
    recent resolved extractions, computes per-field accuracy, and writes
    one ``task_runs`` row per field with ``source='production'``. The
    eval dashboard joins those against ``source='golden'`` rows to draw
    the weekly trend chart and surface drift alerts.

    Failures here are logged but not raised — the worker should not
    crash the Arq process if the monitor hits a transient DB error.
    """
    try:
        async with get_session() as session:
            persisted = await run_drift_monitor(session)
        logger.info("drift_monitor_complete", fields=len(persisted))
        return {"status": "ok", "fields_written": len(persisted)}
    except Exception as e:
        logger.exception("drift_monitor_failed")
        return {"status": "failed", "error": str(e)}


async def startup(ctx: dict[str, Any]) -> None:
    logger.info("worker_starting")


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker_shutting_down")


class WorkerSettings:
    """Arq worker configuration."""

    functions = [run_intake_for_loan, drift_monitor]
    on_startup = startup
    on_shutdown = shutdown
    cron_jobs = [cron(drift_monitor, hour=3, minute=0)]  # 3 AM UTC nightly

    @classmethod
    def get_redis_settings(cls) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)

    redis_settings = property(get_redis_settings)
