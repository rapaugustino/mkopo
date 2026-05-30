"""Per-agent economics — $/decision + p95 latency.

The eval dashboard already shows headline LLM p95 latency + error
rate aggregated across every call. That number is necessary but
not sufficient: it doesn't tell you *which* agent regressed when
quality moves. This module computes per-agent ($/run, p95 latency)
so quality, cost, and latency line up against the same axis on
the dashboard.

Why per-agent and not per-loan: the relevant question for an
underwriter is "what's an underwriting run costing me?" — not "what
did loan MK-2025-0517 cost?". Per-loan cost is already on the case
file (audit timeline). Per-agent is the aggregate trend.

Calculations
------------

For each agent (intake / underwriting / decision / borrower_chat /
staff_chat — anything that shows up in ``agent_runs.agent_name``):

  - **$/run**  = (sum of cost_input_usd + cost_output_usd across
                  LLMCalls belonging to runs of this agent in the
                  window) / (count of runs in the window).
  - **p95 latency** = 95th percentile of LLMCall.elapsed_seconds
                       across the same population.

Linkage: ``LLMCall.thread_id`` ↔ ``AgentRun.thread_id``. Existing
schema, no migration. Runs that produced no LLM calls (no-op short-
circuits, e.g. intake with no docs) count toward the run-count
denominator but contribute zero cost — which is the right number
("how much do I spend per run on average").

Window: last 30 days, same as drift/calibration. Configurable when
the dashboard wants finer slices later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import AgentRun
from mkopo.models.eval import LLMCall, TaskRun

logger = structlog.get_logger()


_WINDOW_DAYS = 30


@dataclass
class AgentEconRow:
    """Per-agent aggregate for the dashboard."""

    agent_name: str
    n_runs: int
    n_calls: int
    total_cost_usd: float
    cost_per_run_usd: float
    p95_latency_seconds: float | None
    p50_latency_seconds: float | None


def _percentile(values: list[float], p: float) -> float | None:
    """Linear-interpolation percentile. Returns None for empty input.

    We roll our own rather than depend on numpy. Two other p95
    implementations exist (``routers/evals._percentile``,
    ``routers/observability._percentile``) — both nearest-rank. This
    one differs because linear interpolation is the more standard
    p95 convention for cost analytics; the other two power tail-
    latency tiles where nearest-rank is the convention. Numbers can
    differ on small n; the help-page "Common confusions" entry
    documents this for operators.
    """
    if not values:
        return None
    sv = sorted(values)
    if len(sv) == 1:
        return sv[0]
    idx = (len(sv) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sv) - 1)
    frac = idx - lo
    return sv[lo] + (sv[hi] - sv[lo]) * frac


async def compute_agent_economics(
    session: AsyncSession, window_days: int = _WINDOW_DAYS
) -> list[AgentEconRow]:
    """Aggregate $/run + p95 latency per agent over the window.

    Returns a list sorted by ``n_runs`` descending so the busiest
    agent reads first on the dashboard.
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    # Per-agent run count.
    run_counts_stmt = (
        select(AgentRun.agent_name, func.count(AgentRun.id))
        .where(AgentRun.created_at >= cutoff)
        .group_by(AgentRun.agent_name)
    )
    runs_by_agent = {
        name: int(n)
        for (name, n) in (await session.execute(run_counts_stmt)).all()
    }

    # Per-agent LLM-call rows. Join on thread_id (both are strings
    # for historical reasons; the join is exact-match).
    calls_stmt = (
        select(
            AgentRun.agent_name,
            LLMCall.elapsed_seconds,
            LLMCall.cost_input_usd,
            LLMCall.cost_output_usd,
        )
        .join(AgentRun, AgentRun.thread_id == LLMCall.thread_id)
        .where(LLMCall.created_at >= cutoff)
    )
    rows = (await session.execute(calls_stmt)).all()

    # Bucket into per-agent lists.
    per_agent_latencies: dict[str, list[float]] = {}
    per_agent_cost: dict[str, float] = {}
    per_agent_calls: dict[str, int] = {}
    for agent_name, elapsed, cost_in, cost_out in rows:
        lst = per_agent_latencies.setdefault(agent_name, [])
        lst.append(float(elapsed))
        cost = float(cost_in or 0) + float(cost_out or 0)
        per_agent_cost[agent_name] = (
            per_agent_cost.get(agent_name, 0.0) + cost
        )
        per_agent_calls[agent_name] = (
            per_agent_calls.get(agent_name, 0) + 1
        )

    # Build result rows. Include agents with runs but no calls
    # (short-circuits) — they have cost 0 and no percentile.
    agents = set(runs_by_agent) | set(per_agent_latencies)
    out: list[AgentEconRow] = []
    for name in agents:
        n_runs = runs_by_agent.get(name, 0)
        latencies = per_agent_latencies.get(name, [])
        total_cost = per_agent_cost.get(name, 0.0)
        out.append(
            AgentEconRow(
                agent_name=name,
                n_runs=n_runs,
                n_calls=per_agent_calls.get(name, 0),
                total_cost_usd=total_cost,
                cost_per_run_usd=total_cost / n_runs if n_runs > 0 else 0.0,
                p95_latency_seconds=_percentile(latencies, 0.95),
                p50_latency_seconds=_percentile(latencies, 0.50),
            )
        )
    out.sort(key=lambda r: r.n_runs, reverse=True)
    return out


async def run_agent_economics_monitor(
    session: AsyncSession,
) -> list[AgentEconRow]:
    """Compute per-agent economics + persist ONE ``task_runs`` row
    per agent so the dashboard trend chart can plot cost-per-run
    over time. Skips agents with zero runs.

    Persistence convention: ``task_name='economics.<agent_name>'``,
    ``source='production'``, ``accuracy=cost_per_run_usd``,
    ``avg_score=p95_latency_seconds``. ``details`` carries the
    full row. The card decodes the inverted-polarity meaning
    (lower is better).
    """
    rows = await compute_agent_economics(session)
    if not rows:
        logger.info("agent_economics_skipped_no_runs")
        return rows

    n_persisted = 0
    for r in rows:
        if r.n_runs == 0:
            continue
        session.add(
            TaskRun(
                task_name=f"economics.{r.agent_name}",
                source="production",
                n=r.n_runs,
                accuracy=r.cost_per_run_usd,
                avg_score=r.p95_latency_seconds or 0.0,
                details={
                    "agent_name": r.agent_name,
                    "n_runs": r.n_runs,
                    "n_calls": r.n_calls,
                    "total_cost_usd": r.total_cost_usd,
                    "cost_per_run_usd": r.cost_per_run_usd,
                    "p95_latency_seconds": r.p95_latency_seconds,
                    "p50_latency_seconds": r.p50_latency_seconds,
                    "window_days": _WINDOW_DAYS,
                },
            )
        )
        n_persisted += 1
    await session.flush()
    logger.info(
        "agent_economics_monitor_ran",
        n_agents=n_persisted,
        rows=[(r.agent_name, r.n_runs, r.cost_per_run_usd) for r in rows],
    )
    return rows


async def get_agent_economics_summary(
    session: AsyncSession,
) -> list[AgentEconRow]:
    """Read-only path for the dashboard endpoint — doesn't persist,
    just computes."""
    return await compute_agent_economics(session)
