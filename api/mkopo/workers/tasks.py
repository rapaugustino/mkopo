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
from mkopo.services.agent_economics import run_agent_economics_monitor
from mkopo.services.calibration import run_calibration_monitor
from mkopo.services.drift import run_drift_monitor
from mkopo.services.fairness import run_fairness_monitor
from mkopo.services.prompt_drift import run_prompt_drift_monitor
from mkopo.services.psi import run_psi_monitor
from mkopo.services.refusal import run_refusal_monitor

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


async def fairness_monitor(ctx: dict[str, Any]) -> dict[str, Any]:
    """Adverse Impact Ratio sweep — runs at 3:45 AM UTC, between the
    calibration monitor (3:30) and the golden sweep (4:00).

    Computes per-protected-class approval rate over the last 90 days
    and writes one ``task_runs`` row with
    ``task_name='fairness.adverse_impact_ratio'``. Flag bands:
    ``ok`` ≥ 0.85, ``watch`` 0.80–0.85, ``concern`` < 0.80 (EEOC
    four-fifths threshold).

    See ``services/fairness.py`` for the regulatory framing + the
    note on why this is a screening heuristic, not a per-se finding.
    """
    try:
        async with get_session() as session:
            result = await run_fairness_monitor(session)
            await session.commit()
        logger.info(
            "fairness_monitor_complete",
            n=result.n_loans_decisioned,
            air=result.air,
            flag=result.flag,
        )
        return {
            "status": "ok",
            "n": result.n_loans_decisioned,
            "air": result.air,
            "flag": result.flag,
        }
    except Exception as e:
        logger.exception("fairness_monitor_failed")
        return {"status": "failed", "error": str(e)}


async def psi_monitor(ctx: dict[str, Any]) -> dict[str, Any]:
    """PSI sweep — runs at 3:50 AM UTC, between fairness (3:45) and
    the golden sweep (4:00).

    Computes Population Stability Index per input feature
    (loan_amount, loan_class, loan_type) and writes one
    ``task_runs`` row per feature with ``task_name='psi.<feature>'``.
    Cheap (pure SQL aggregate). See ``services/psi.py`` for the
    threshold bands (Siddiqi 2017).
    """
    try:
        async with get_session() as session:
            result = await run_psi_monitor(session)
            await session.commit()
        logger.info(
            "psi_monitor_complete",
            n_features=len(result.features),
            features=[(f.feature, f.psi, f.flag) for f in result.features],
        )
        return {
            "status": "ok",
            "n_features": len(result.features),
        }
    except Exception as e:
        logger.exception("psi_monitor_failed")
        return {"status": "failed", "error": str(e)}


async def refusal_monitor(ctx: dict[str, Any]) -> dict[str, Any]:
    """Refusal-rate sweep — 3:52 UTC. Computes block-rate (current
    7d) vs baseline (prior 28d) on InjectionDetection and flags a
    spike at ≥ 2σ above baseline. See ``services/refusal.py``.
    """
    try:
        async with get_session() as session:
            result = await run_refusal_monitor(session)
            await session.commit()
        logger.info(
            "refusal_monitor_complete",
            current_rate=result.current_rate,
            baseline_rate=result.baseline_rate,
            flag=result.flag,
        )
        return {"status": "ok", "flag": result.flag}
    except Exception as e:
        logger.exception("refusal_monitor_failed")
        return {"status": "failed", "error": str(e)}


async def agent_economics_monitor(ctx: dict[str, Any]) -> dict[str, Any]:
    """Per-agent $/run + p95 latency — 3:55 UTC. Aggregates LLMCall
    rows joined to AgentRun.thread_id over the last 30 days. Writes
    one ``task_runs`` row per agent (task_name='economics.<agent>')
    so the trend chart picks up cost regression alongside accuracy.
    """
    try:
        async with get_session() as session:
            rows = await run_agent_economics_monitor(session)
            await session.commit()
        logger.info(
            "agent_economics_monitor_complete",
            n_agents=len(rows),
        )
        return {"status": "ok", "n_agents": len(rows)}
    except Exception as e:
        logger.exception("agent_economics_monitor_failed")
        return {"status": "failed", "error": str(e)}


async def prompt_drift_monitor(ctx: dict[str, Any]) -> dict[str, Any]:
    """Embedding-distribution drift on borrower inbound messages —
    3:58 UTC. MMD² between the last 7d corpus and the prior 30d
    reference (with 7d gap). Cost: one embedding call per *new*
    message body (cached via EmbeddingService). See
    ``services/prompt_drift.py``.
    """
    try:
        async with get_session() as session:
            result = await run_prompt_drift_monitor(session)
            await session.commit()
        logger.info(
            "prompt_drift_monitor_complete",
            mmd2=result.mmd2,
            flag=result.flag,
        )
        return {"status": "ok", "flag": result.flag}
    except Exception as e:
        logger.exception("prompt_drift_monitor_failed")
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
        from evals.tasks.extract_appraised_value import (
            ExtractAppraisedValueTask,
        )
        from evals.tasks.extract_borrower_entity import (
            ExtractBorrowerEntityTask,
        )
        from evals.tasks.extract_credit_score import (
            ExtractCreditScoreTask,
        )
        from evals.tasks.extract_loan_amount import (
            ExtractLoanAmountTask,
        )
        from evals.tasks.extract_noi import ExtractNOITask
        from evals.tasks.intake_email import IntakeEmailTask
        from evals.tasks.summarize_underwriting import (
            SummarizeUnderwritingTask,
        )
        from evals.tasks.tool_call_accuracy import ToolCallAccuracyTask
        from evals.tasks.uw_groundedness import UWGroundednessTask

        # Keep this list in lock-step with ``evals/runner.py:_main`` —
        # if either drifts, the dashboard's nightly trend and the CLI
        # gate stop reporting the same population.
        tasks = [
            ExtractBorrowerEntityTask(),
            ExtractNOITask(),
            SummarizeUnderwritingTask(),
            ExtractAppraisedValueTask(),
            ExtractCreditScoreTask(),
            ExtractLoanAmountTask(),
            AdversarialInjectionTask(),
            DecisionVerdictTask(),
            AALFidelityTask(),
            IntakeEmailTask(),
            UWGroundednessTask(),
            ToolCallAccuracyTask(),
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
        fairness_monitor,
        psi_monitor,
        refusal_monitor,
        agent_economics_monitor,
        prompt_drift_monitor,
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
        # 3:45 AM UTC — fairness sweep (AIR / four-fifths) on the
        # last 90 days of decisioned loans. Cheap (pure SQL aggregate);
        # docked next to calibration because both are "operational
        # quality" monitors.
        cron(fairness_monitor, hour=3, minute=45),
        # 3:50 AM UTC — PSI sweep on input features. Cheap; the
        # *leading* indicator of model drift, runs before the
        # golden sweep so the dashboard's accuracy-vs-input-shift
        # correlation is one timestamp.
        cron(psi_monitor, hour=3, minute=50),
        # 3:52 AM UTC — refusal-rate sweep on the injection
        # detector. Fast SQL aggregate.
        cron(refusal_monitor, hour=3, minute=52),
        # 3:55 AM UTC — per-agent $/run + p95 latency over the
        # last 30 days. Joins llm_calls ↔ agent_runs by thread_id.
        cron(agent_economics_monitor, hour=3, minute=55),
        # 3:58 AM UTC — embedding-distribution drift (MMD²) on
        # borrower inbound messages. Catches semantic shifts PSI
        # can't see.
        cron(prompt_drift_monitor, hour=3, minute=58),
        # 4:00 AM UTC — golden-set sweep rescores against the YAML
        # fixtures. Scheduled after every production-side monitor so
        # the dashboard's trend lines have aligned timestamps.
        cron(golden_eval_sweep, hour=4, minute=0),
    ]

    @classmethod
    def get_redis_settings(cls) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)

    redis_settings = property(get_redis_settings)
