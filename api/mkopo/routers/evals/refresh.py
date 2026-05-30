"""Manual "refresh" endpoints for production monitors + the read-only
agent-economics summary.

Each refresh endpoint mirrors the equivalent Arq cron path so a
demo'er can re-run a monitor on demand instead of waiting for the
nightly sweep. Returns a small shape with the post-run numbers so the
UI can render the updated card without a second fetch.

``agent_economics`` is read-only — the cron at 3:55 UTC persists
``economics.<agent_name>`` rows that drive the trend chart; this
endpoint computes the at-a-glance table the dashboard card shows.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.services.drift import run_drift_monitor

router = APIRouter()


# ----- response models ----------------------------------------------------


class RefreshResponse(BaseModel):
    status: str
    fields_written: int


class FairnessRefreshResponse(BaseModel):
    """Hand-rolled return shape for the manual fairness refresh.

    Mirrors what ``run_fairness_monitor`` would log — gives the UI
    enough to render an updated card without re-fetching."""

    status: str
    n_loans_decisioned: int
    air: float | None
    flag: str
    window_days: int


class PSIFeatureSummary(BaseModel):
    """Per-feature summary returned by the manual PSI refresh."""

    feature: str
    psi: float
    flag: str
    n_reference: int
    n_current: int


class PSIRefreshResponse(BaseModel):
    """Return shape for the manual PSI refresh."""

    status: str
    features: list[PSIFeatureSummary]
    window_current_days: int
    window_reference_days: int


class RefusalRefreshResponse(BaseModel):
    """Return shape for the manual refusal-rate refresh."""

    status: str
    current_rate: float
    baseline_rate: float
    n_current: int
    n_baseline: int
    z_score: float | None
    flag: str


class AgentEconRowOut(BaseModel):
    """Per-agent economics row — what the dashboard card renders."""

    agent_name: str
    n_runs: int
    n_calls: int
    total_cost_usd: float
    cost_per_run_usd: float
    p95_latency_seconds: float | None
    p50_latency_seconds: float | None


class AgentEconResponse(BaseModel):
    rows: list[AgentEconRowOut]
    window_days: int


# ----- endpoints ----------------------------------------------------------


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_drift(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> RefreshResponse:
    """Manually re-run the drift monitor.

    Identical to the Arq cron job — useful for the "Refresh" button on
    the dashboard so demos don't have to wait for 3 AM UTC. Commits
    rows via the request-scoped session.
    """
    persisted = await run_drift_monitor(db)
    await db.commit()
    return RefreshResponse(status="ok", fields_written=len(persisted))


@router.post("/fairness/refresh", response_model=FairnessRefreshResponse)
async def refresh_fairness(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> FairnessRefreshResponse:
    """Manually re-run the fairness (AIR) monitor.

    Same code path the Arq cron at 3:45 UTC calls. Useful for the
    dashboard's "Refresh" button so demos don't have to wait — and
    so a staff user who just transitioned a loan can see the AIR
    move immediately.
    """
    from mkopo.services.fairness import run_fairness_monitor

    result = await run_fairness_monitor(db)
    await db.commit()
    return FairnessRefreshResponse(
        status="ok",
        n_loans_decisioned=result.n_loans_decisioned,
        air=result.air,
        flag=result.flag,
        window_days=result.window_days,
    )


@router.post("/refusal/refresh", response_model=RefusalRefreshResponse)
async def refresh_refusal(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> RefusalRefreshResponse:
    """Manually re-run the refusal-rate monitor. Mirrors the 3:52
    UTC cron path."""
    from mkopo.services.refusal import run_refusal_monitor

    result = await run_refusal_monitor(db)
    await db.commit()
    return RefusalRefreshResponse(
        status="ok",
        current_rate=result.current_rate,
        baseline_rate=result.baseline_rate,
        n_current=result.n_current,
        n_baseline=result.n_baseline,
        z_score=result.z_score,
        flag=result.flag,
    )


@router.post("/psi/refresh", response_model=PSIRefreshResponse)
async def refresh_psi(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> PSIRefreshResponse:
    """Manually re-run the PSI monitor.

    Same code path the Arq cron at 3:50 UTC calls. Useful for the
    dashboard's "Refresh" button. Returns a per-feature summary so
    the UI can render the updated bars without re-fetching.
    """
    from mkopo.services.psi import run_psi_monitor

    result = await run_psi_monitor(db)
    await db.commit()
    return PSIRefreshResponse(
        status="ok",
        features=[
            PSIFeatureSummary(
                feature=f.feature,
                psi=f.psi,
                flag=f.flag,
                n_reference=f.n_reference,
                n_current=f.n_current,
            )
            for f in result.features
        ],
        window_current_days=result.window_current_days,
        window_reference_days=result.window_reference_days,
    )


@router.get("/agent-economics", response_model=AgentEconResponse)
async def agent_economics(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> AgentEconResponse:
    """Per-agent $/run + p95 latency over the last 30 days.

    Read-only — doesn't persist a task_runs row. The dashboard
    card uses this for the at-a-glance table; the trend chart picks
    up persisted rows from ``economics.<agent_name>`` written by
    the 3:55 UTC cron.
    """
    from mkopo.services.agent_economics import get_agent_economics_summary

    rows = await get_agent_economics_summary(db)
    return AgentEconResponse(
        rows=[
            AgentEconRowOut(
                agent_name=r.agent_name,
                n_runs=r.n_runs,
                n_calls=r.n_calls,
                total_cost_usd=r.total_cost_usd,
                cost_per_run_usd=r.cost_per_run_usd,
                p95_latency_seconds=r.p95_latency_seconds,
                p50_latency_seconds=r.p50_latency_seconds,
            )
            for r in rows
        ],
        window_days=30,
    )
