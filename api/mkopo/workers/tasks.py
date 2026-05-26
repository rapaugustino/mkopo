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
from mkopo.services.calibration import run_calibration_monitor
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


async def calibration_monitor(ctx: dict[str, Any]) -> dict[str, Any]:
    """Calibration sweep — runs at 3:30 AM UTC, between the drift
    monitor (3:00) and the golden sweep (4:00).

    Computes ECE + Brier score on the last 30 days of resolved
    extractions and writes one ``task_runs`` row with
    ``task_name='calibration.extractor_confidence'``. The /eval
    dashboard surfaces the metric on the trend chart + the
    reliability-diagram card.
    """
    try:
        async with get_session() as session:
            result = await run_calibration_monitor(session)
            await session.commit()
        logger.info(
            "calibration_monitor_complete",
            n=result.n,
            ece=result.ece,
            brier=result.brier,
        )
        return {
            "status": "ok",
            "n": result.n,
            "ece": result.ece,
            "brier": result.brier,
        }
    except Exception as e:
        logger.exception("calibration_monitor_failed")
        return {"status": "failed", "error": str(e)}


async def golden_eval_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """Periodic golden-set eval — runs at 4 AM UTC (right after the
    drift monitor at 3 AM UTC so the dashboard's two trend lines are
    always nearly time-aligned).

    Loads the same task list the CLI runs, scores against the YAML
    fixtures, and persists ``task_runs`` rows with ``source='golden'``.
    The /eval dashboard pairs these against the production rows for
    the drift delta (golden_accuracy - production_accuracy).

    Cost: 3 tasks × ~5 examples × ~$0.001 per LLM call ≈ $0.02 per
    nightly run. Trivial. Bigger goldens (Phase 2 of EVAL_PLAN.md)
    will move this to ~$0.50 per run, still trivial at daily cadence.
    """
    try:
        # Import here so the worker's cold-start doesn't pay the cost
        # if this task is disabled. Also breaks a potential
        # workers → evals → ... import cycle.
        from evals.runner import persist_results_to_db, run_suite
        from evals.tasks.aal_fidelity import AALFidelityTask
        from evals.tasks.adversarial_injection import (
            AdversarialInjectionTask,
        )
        from evals.tasks.decision_verdict import DecisionVerdictTask
        from evals.tasks.extract_borrower_entity import (
            ExtractBorrowerEntityTask,
        )
        from evals.tasks.extract_noi import ExtractNOITask
        from evals.tasks.summarize_underwriting import (
            SummarizeUnderwritingTask,
        )

        tasks = [
            ExtractBorrowerEntityTask(),
            ExtractNOITask(),
            SummarizeUnderwritingTask(),
            AdversarialInjectionTask(),
            DecisionVerdictTask(),
            AALFidelityTask(),
        ]
        results = await run_suite(tasks)
        n_persisted = await persist_results_to_db(results)
        logger.info(
            "golden_eval_sweep_complete",
            n_tasks=len(results),
            n_persisted=n_persisted,
        )
        return {
            "status": "ok",
            "n_tasks": len(results),
            "n_persisted": n_persisted,
        }
    except Exception as e:
        logger.exception("golden_eval_sweep_failed")
        return {"status": "failed", "error": str(e)}


async def startup(ctx: dict[str, Any]) -> None:
    logger.info("worker_starting")


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker_shutting_down")


class WorkerSettings:
    """Arq worker configuration."""

    functions = [
        run_intake_for_loan,
        drift_monitor,
        calibration_monitor,
        golden_eval_sweep,
    ]
    on_startup = startup
    on_shutdown = shutdown
    cron_jobs = [
        # 3:00 AM UTC — drift monitor samples production extraction
        # accuracy from staff overrides in the review queue.
        cron(drift_monitor, hour=3, minute=0),
        # 3:30 AM UTC — calibration sweep (ECE + Brier) on the same
        # resolved-extractions window the drift monitor used.
        # Cheap (no LLM calls), uses the data drift just refreshed.
        cron(calibration_monitor, hour=3, minute=30),
        # 4:00 AM UTC — golden-set sweep rescores against the YAML
        # fixtures. Scheduled after drift + calibration so the
        # dashboard's three trend lines have aligned timestamps.
        cron(golden_eval_sweep, hour=4, minute=0),
    ]

    @classmethod
    def get_redis_settings(cls) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)

    redis_settings = property(get_redis_settings)
