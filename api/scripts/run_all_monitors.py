"""
Run every production monitor once and persist results to
``task_runs``. Used when populating a freshly seeded DB for a demo
or interview walkthrough — the dashboards expect production rows
to exist before they show anything meaningful.

In production these run on the arq scheduler (see ``mkopo.worker``).
This script is the manual-trigger equivalent.

Usage::

    cd api && uv run python scripts/run_all_monitors.py

Each monitor is independent; if one fails (e.g. insufficient data
for the binomial floor), the others continue. We report
per-monitor status at the end.
"""

from __future__ import annotations

import asyncio
import sys
import traceback

from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.db import SessionLocal
from mkopo.services.agent_economics import run_agent_economics_monitor
from mkopo.services.calibration import run_calibration_monitor
from mkopo.services.drift import run_drift_monitor
from mkopo.services.fairness import run_fairness_monitor
from mkopo.services.prompt_drift import run_prompt_drift_monitor
from mkopo.services.psi import run_psi_monitor
from mkopo.services.refusal import run_refusal_monitor


async def _one(name: str, fn) -> tuple[str, str]:
    """Run a single monitor with its own session. Each monitor gets
    a fresh session so a failure in one doesn't poison the txn
    state for the next."""
    try:
        async with SessionLocal() as session:
            result = await fn(session)
            await session.commit()
        return (name, f"ok — {type(result).__name__}")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return (name, f"FAIL — {type(exc).__name__}: {exc}")


async def main() -> None:
    monitors: list[tuple[str, object]] = [
        ("drift", run_drift_monitor),
        ("calibration", run_calibration_monitor),
        ("fairness", run_fairness_monitor),
        ("psi", run_psi_monitor),
        ("refusal", run_refusal_monitor),
        ("agent_economics", run_agent_economics_monitor),
        ("prompt_drift", run_prompt_drift_monitor),
    ]

    print(f"Running {len(monitors)} production monitors...\n")
    results: list[tuple[str, str]] = []
    for name, fn in monitors:
        print(f"  → {name}", flush=True)
        results.append(await _one(name, fn))

    print("\n--- Results ---")
    width = max(len(n) for n, _ in results)
    for name, status in results:
        print(f"  {name:<{width}}  {status}")

    failures = [n for n, s in results if s.startswith("FAIL")]
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
