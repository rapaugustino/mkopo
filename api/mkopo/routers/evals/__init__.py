"""Eval dashboard router package — six sub-modules combined under /eval.

Up until the May 2026 cleanup this lived as a single 1500-line
``routers/evals.py``. The endpoints fall into six cohesive groups,
each with its own response models + helpers; splitting per-group
makes each file an opening-size that fits on screen without scrolling.

Layout:
- ``_shared.py``     — types + helpers used by more than one module
                       (DRIFT_THRESHOLD, accuracy-vs-derived filter,
                       summary aggregator, percentile)
- ``summary.py``     — GET /summary, /fields, /trend
- ``refresh.py``     — POST /refresh, /fairness/refresh, /psi/refresh,
                       /refusal/refresh   plus   GET /agent-economics
- ``task_detail.py`` — GET /task-detail/{task_name:path}
- ``diagnostics.py`` — GET /diagnostics (calibration, queue,
                       agent reliability, recent failures)
- ``annotations.py`` — GET/POST/DELETE /annotations
- ``llm_diff.py``    — GET /diff/llm-calls

The combined ``router`` re-exported here matches the import that was
in ``main.py`` before the split (``from mkopo.routers import evals;
app.include_router(evals.router)``) so the registration is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

from mkopo.routers.evals._shared import (
    DRIFT_THRESHOLD,
    compute_summary_aggregates,
)
from mkopo.routers.evals.annotations import router as _annotations_router
from mkopo.routers.evals.diagnostics import router as _diagnostics_router
from mkopo.routers.evals.llm_diff import router as _llm_diff_router
from mkopo.routers.evals.refresh import router as _refresh_router
from mkopo.routers.evals.summary import router as _summary_router
from mkopo.routers.evals.task_detail import router as _task_detail_router

router = APIRouter(prefix="/eval", tags=["eval"])
router.include_router(_summary_router)
router.include_router(_refresh_router)
router.include_router(_task_detail_router)
router.include_router(_diagnostics_router)
router.include_router(_annotations_router)
router.include_router(_llm_diff_router)

# Backwards-compat re-exports — the previous monolithic module exposed
# these at the top level. Tests + any internal callers
# (``from mkopo.routers.evals import compute_summary_aggregates``)
# keep working unchanged.
__all__ = ["DRIFT_THRESHOLD", "compute_summary_aggregates", "router"]
