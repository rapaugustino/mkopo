"""Seed a golden eval baseline for the dashboard to compare against.

In a real deployment, ``task_runs`` rows with ``source='golden'`` come
from the CI eval gate — a fixed labelled set the extractor is scored
against on every merge. For the portfolio scope we don't ship that
suite; instead this script writes one snapshot of plausible numbers so
the dashboard's "vs golden" / drift-delta UI has something real to
display.

Run:
    uv run python scripts/seed_eval_baseline.py

Safe to re-run — duplicates create later-dated rows which the dashboard
treats as the new authoritative baseline.
"""

from __future__ import annotations

import asyncio

import structlog

from mkopo.db import get_session
from mkopo.models.eval import TaskRun

logger = structlog.get_logger()

# A plausible labelled-set snapshot. The dashboard compares production
# (live drift monitor) against these; the numbers are intentionally
# below 100% because a real eval suite has hard examples.
BASELINE: list[tuple[str, int, float]] = [
    ("extraction.borrower_entity", 120, 0.96),
    ("extraction.property_address", 120, 0.94),
    ("extraction.property_type", 120, 0.93),
    ("extraction.guarantor_list", 120, 0.92),
    ("extraction.annual_noi", 120, 0.89),
    ("extraction.appraised_value", 120, 0.93),
    ("extraction.appraisal_date", 120, 0.85),
    ("extraction.loan_amount", 120, 0.97),
]


async def seed_baseline() -> None:
    async with get_session() as session:
        added = 0
        for task_name, n, accuracy in BASELINE:
            session.add(
                TaskRun(
                    task_name=task_name,
                    source="golden",
                    n=n,
                    accuracy=accuracy,
                    avg_score=accuracy,
                    details={
                        "labelled_set": "v1",
                        "note": "seeded golden baseline; replace with CI eval output",
                    },
                )
            )
            added += 1
        await session.flush()
    logger.info("eval_baseline_seeded", rows=added)
    print(f"✅ Seeded {added} golden baseline row(s).")
    print("   Run the drift monitor (Refresh in the dashboard, or")
    print("   `await mkopo.workers.tasks.drift_monitor(ctx)`) to get")
    print("   matching production rows.")


if __name__ == "__main__":
    asyncio.run(seed_baseline())
