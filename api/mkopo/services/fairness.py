"""Adverse Impact Ratio — four-fifths rule fairness screen.

Background
----------

The Equal Credit Opportunity Act (ECOA) prohibits discrimination
on prohibited bases (race, color, religion, national origin, sex,
marital status, age, source of income). The CFPB's enforcement
guidance for AI-assisted credit decisions (Circular 2022-03 +
2023-09) makes explicit that model risk includes *disparate impact*
even when no protected attribute is in the model's feature set.

The **Adverse Impact Ratio (AIR)** is the EEOC's "four-fifths rule"
applied to credit approval rates:

  AIR = (approval rate of the lowest-rate group)
        / (approval rate of the highest-rate group)

The convention: AIR < 0.80 (the "four-fifths rule") is a *screening*
threshold — it warrants further investigation, not a per-se finding
of discrimination. This is a one-line summary of decades of case law;
see Watkins et al. 2024 ("The Four-Fifths Rule is Not Disparate
Impact") for why this metric is necessary but not sufficient.

What this module does
---------------------

1. Pulls every loan with a *decision* (APPROVED, SERVICING,
   CONDITIONS, CLOSING, or DECLINED — i.e. the lender acted on it).
   In-flight applications and WITHDRAWN apps are excluded; the
   former don't have an outcome yet, the latter aren't a lender
   decision so they don't count toward disparate-impact analysis.
2. Bucketises each loan into a synthetic protected class via a
   stable hash of ``loan.id``. **This is a portfolio demo.** A
   production deployment would replace ``_synthetic_class_for_loan``
   with the actual demographic from HMDA / application data. See
   the docstring on that function.
3. Per group: approval rate = approved / (approved + declined).
4. Returns the per-group counts + the AIR (min/max).

What this module DOES NOT do
----------------------------

- Does not substitute for a real fair-lending audit. Disparate
  treatment, residual disparity after controls, and model-feature
  proxies for protected attributes all require deeper analysis.
- Does not access protected attributes outside the lawful-purpose
  scope of fair-lending testing. The synthetic-class assignment
  here is a portfolio demo; production code must follow HMDA's
  data-segregation rules.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import Loan, LoanStage
from mkopo.models.eval import TaskRun

logger = structlog.get_logger()

# Window for the AIR sweep — same horizon the drift_monitor +
# calibration_monitor use, so the dashboard's trend lines align on
# the same population.
_WINDOW_DAYS = 90

# Stages that count as "lender said yes" for the approval-rate
# numerator. Once decisioned, a loan can move through conditions →
# closing → servicing; all of those still count as approved
# decisions.
_APPROVED_STAGES = frozenset(
    {
        LoanStage.APPROVED,
        LoanStage.CONDITIONS,
        LoanStage.CLOSING,
        LoanStage.SERVICING,
    }
)
# The denominator is approved + declined. In-flight (INTAKE /
# UNDERWRITING / DECISION) and WITHDRAWN are excluded by the
# selection below — see the rationale at the top of the module.


@dataclass
class _GroupStats:
    name: str
    n_decisioned: int
    n_approved: int
    n_declined: int

    @property
    def approval_rate(self) -> float:
        return self.n_approved / self.n_decisioned if self.n_decisioned > 0 else 0.0


@dataclass
class FairnessResult:
    """Wire shape returned to the dashboard."""

    window_days: int
    n_loans_decisioned: int
    groups: list[dict]  # see ``to_payload``
    air: float | None  # None when only one group has data
    flag: str  # "ok" | "watch" | "concern" — banded against 0.80

    @classmethod
    def from_groups(cls, groups: list[_GroupStats], window_days: int) -> FairnessResult:
        # AIR requires at least two groups with decisioned loans.
        active = [g for g in groups if g.n_decisioned > 0]
        rates = [g.approval_rate for g in active]
        if len(active) < 2 or max(rates) == 0:
            air = None
            flag = "insufficient_data"
        else:
            air = min(rates) / max(rates)
            # EEOC four-fifths convention: < 0.80 = screen flag.
            # We add a 0.80–0.85 "watch" band so a borderline value
            # surfaces on the dashboard before it trips the gate.
            if air < 0.80:
                flag = "concern"
            elif air < 0.85:
                flag = "watch"
            else:
                flag = "ok"
        return cls(
            window_days=window_days,
            n_loans_decisioned=sum(g.n_decisioned for g in groups),
            groups=[
                {
                    "name": g.name,
                    "n_decisioned": g.n_decisioned,
                    "n_approved": g.n_approved,
                    "n_declined": g.n_declined,
                    "approval_rate": g.approval_rate,
                }
                for g in groups
            ],
            air=air,
            flag=flag,
        )


def _synthetic_class_for_loan(loan_id: uuid.UUID) -> str:
    """Hash-based stable bucketization into a synthetic protected
    class. **Portfolio demo only.**

    Why hash:
      - Stable across runs (same loan always lands in the same
        group), so AIR trends are reproducible.
      - No PII required — every fresh seed produces the same
        distribution.
      - Avoids a migration adding a real demographic column to the
        loan table, which would in turn need data-handling controls
        we don't want to half-implement.

    Production replacement: read the actual ECOA-protected
    demographic field from the loan application packet (typically
    HMDA-collected). The function signature stays — only the body
    changes. The dashboard / endpoint / worker code calling this
    don't need to change.

    Two synthetic classes are enough for the four-fifths rule
    (the rule compares two groups pairwise). Names are
    intentionally neutral so the demo doesn't pretend to
    represent any real protected class.
    """
    digest = hashlib.sha256(loan_id.bytes).digest()
    return "Group A" if digest[0] % 2 == 0 else "Group B"


async def _load_decisioned_loans(session: AsyncSession, window_days: int) -> list[Loan]:
    """Pull every loan that received a lender decision within the
    window. The dashboard's other monitors (drift, calibration) use
    ``created_at`` for the same purpose; we follow suit so the three
    monitors' trends share a population definition."""
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    stmt = select(Loan).where(
        Loan.stage.in_(tuple(_APPROVED_STAGES) + (LoanStage.DECLINED,)),
        Loan.created_at >= cutoff,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def compute_adverse_impact_ratio(
    session: AsyncSession, window_days: int = _WINDOW_DAYS
) -> FairnessResult:
    """Compute the AIR + per-group counts for the window.

    Returns an "insufficient_data" flag when there aren't enough
    decisioned loans to compute a ratio (fresh installs, or windows
    with all-INTAKE traffic). The dashboard renders that as an empty
    state rather than a misleading "0% AIR".
    """
    rows = await _load_decisioned_loans(session, window_days)
    groups: dict[str, _GroupStats] = {}
    for loan in rows:
        cls = _synthetic_class_for_loan(loan.id)
        bucket = groups.setdefault(
            cls, _GroupStats(name=cls, n_decisioned=0, n_approved=0, n_declined=0)
        )
        bucket.n_decisioned += 1
        if loan.stage == LoanStage.DECLINED:
            bucket.n_declined += 1
        else:
            bucket.n_approved += 1
    return FairnessResult.from_groups(
        sorted(groups.values(), key=lambda g: g.name),
        window_days=window_days,
    )


async def run_fairness_monitor(session: AsyncSession) -> FairnessResult:
    """Compute fairness + persist a ``task_runs`` row so the /eval
    dashboard's trend chart picks it up.

    Mirrors the writer pattern in ``services/drift.py`` and
    ``services/calibration.py``. Returns 0 inserts (no row) when the
    window is empty — same convention as those monitors, so the
    dashboard renders "no data" instead of a 0% AIR line.
    """
    result = await compute_adverse_impact_ratio(session)
    if result.n_loans_decisioned == 0 or result.air is None:
        # Skip the write when either the window is empty OR we have
        # only one populated group. A 0.0 AIR on the trend chart
        # would read as "100% disparate impact" — that's flat-out
        # wrong when the real story is "not enough data yet". The
        # dashboard's task-detail endpoint returns ``found=false``
        # in this case, which the card renders as an empty state
        # explaining the threshold.
        logger.info(
            "fairness_monitor_skipped",
            reason="empty_window" if result.n_loans_decisioned == 0 else "single_group",
            n=result.n_loans_decisioned,
        )
        return result

    # ``accuracy`` field on task_runs is repurposed as the AIR so the
    # dashboard's existing trend chart picks it up alongside other
    # production-source rows. Documented in the trend chart's
    # tooltip + on the fairness card.
    row = TaskRun(
        task_name="fairness.adverse_impact_ratio",
        source="production",
        n=result.n_loans_decisioned,
        accuracy=result.air,
        avg_score=result.air,
        details={
            "window_days": result.window_days,
            "flag": result.flag,
            "groups": result.groups,
            "air": result.air,
            # Stash the discrimination threshold for the dashboard
            # tooltip so the "0.80" number is regulatable in one
            # place if EEOC ever moves the goalposts (they have not
            # since the 1978 Uniform Guidelines, but).
            "four_fifths_threshold": 0.80,
        },
    )
    session.add(row)
    await session.flush()
    logger.info(
        "fairness_monitor_ran",
        air=result.air,
        flag=result.flag,
        n=result.n_loans_decisioned,
        n_groups=len(result.groups),
    )
    return result
