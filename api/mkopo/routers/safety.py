"""Safety & guardrails observability — input-side injection detections + output-side judge rollups.

Powers two surfaces:

- The top-level ``/safety`` dashboard (system-wide overview,
  injection log, judgment rollup, recent blocks).
- The Safety tab embedded in ``/observability`` (windowed recent
  events, no deep-dive).

Read-only endpoints — the only writes to ``injection_detections``
happen inside :func:`mkopo.agents.injection.detect_injection`, and
the only writes to ``agent_runs.payload.guardrail_judgment`` happen
inside the validator nodes of the three agents. This router just
reads.

All endpoints require staff auth (same ``CurrentUserDep`` as
``observability.py``). A regular borrower can't see another
borrower's blocked uploads.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import desc, select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import AgentRun, InjectionDetection
from mkopo.safety import SCENARIOS

logger = structlog.get_logger()


router = APIRouter(prefix="/safety", tags=["safety"])


# --- Response schemas -------------------------------------------------------


class InjectionDetectionRow(BaseModel):
    """One row in the dashboard's detection table.

    Truncated to the fields a row needs — ``raw_text_excerpt`` and
    ``matched_patterns`` come back only from the detail endpoint so
    the list payload stays small.
    """

    id: str
    created_at: datetime
    loan_id: str | None
    source_kind: str
    source_id: str | None
    severity: str
    decision: str
    llm_judge_called: bool
    llm_judge_severity: str | None
    actor_kind: str
    actor_id: str | None
    n_patterns: int


class InjectionDetectionDetail(InjectionDetectionRow):
    """Full row + payload — for the drawer."""

    matched_patterns: list[dict]
    llm_judge_critique: str | None
    raw_text_excerpt: str


class PatternHitCount(BaseModel):
    pattern_id: str
    description: str
    hits: int
    severity_floor: str


class SafetySummary(BaseModel):
    """The /safety dashboard's headline payload."""

    window_hours: int

    # Totals
    total_scanned: int
    total_allowed: int
    total_flagged: int
    total_blocked: int

    # Severity histogram
    by_severity: dict[str, int]

    # By source kind (document / chat / etc.)
    by_source_kind: dict[str, int]

    # Pattern catalog top-N hits
    pattern_top: list[PatternHitCount]

    # Cost envelope
    llm_judge_calls: int
    cost_estimate_usd: float

    # Recent rows
    recent: list[InjectionDetectionRow]


class JudgmentRow(BaseModel):
    """One agent_runs row whose payload carried a guardrail_judgment."""

    agent_run_id: str
    agent_name: str
    loan_id: str
    started_at: datetime
    severity: Literal["ok", "warn", "block"]
    attempts: int
    failed_principles: list[str]
    failed_red_lines: list[str]
    critique: str | None
    constitution_hint: str  # derived from agent_name


class JudgmentSummary(BaseModel):
    """Output-side rollup — payloads of every agent_run with a judgment."""

    window_hours: int
    total_judgments: int
    by_severity: dict[str, int]
    by_agent: dict[str, int]
    retry_distribution: dict[int, int]  # attempts → count
    rows: list[JudgmentRow]


# --- Helpers ---------------------------------------------------------------


# Per-Haiku-call cost estimate for the dashboard. Conservative — actual
# token spend varies by input length; the ~$0.001 figure is for a
# ~300-input/~100-output Haiku call which matches the typical
# detector escalation payload. Updated when llm_fast_model changes.
_HAIKU_CALL_COST_USD = 0.001


def _summarize(rows: Sequence[InjectionDetection]) -> dict[str, int]:
    """Counter of rows by decision."""
    c = Counter(r.decision for r in rows)
    return {
        "allowed": c.get("allowed", 0),
        "flagged": c.get("flagged", 0),
        "blocked": c.get("blocked", 0),
    }


def _to_row(d: InjectionDetection) -> InjectionDetectionRow:
    return InjectionDetectionRow(
        id=str(d.id),
        created_at=d.created_at,
        loan_id=str(d.loan_id) if d.loan_id else None,
        source_kind=d.source_kind,
        source_id=str(d.source_id) if d.source_id else None,
        severity=d.severity,
        decision=d.decision,
        llm_judge_called=d.llm_judge_called,
        llm_judge_severity=d.llm_judge_severity,
        actor_kind=d.actor_kind,
        actor_id=d.actor_id,
        n_patterns=len(d.matched_patterns or []),
    )


# --- Endpoints -------------------------------------------------------------


@router.get("/summary", response_model=SafetySummary)
async def safety_summary(
    user: CurrentUserDep,
    db: DbSessionDep,
    hours: int = 24,
    recent_limit: int = 25,
) -> SafetySummary:
    """Dashboard top-of-page rollup for ``/safety``."""
    hours = max(1, min(hours, 720))  # cap at 30 days
    recent_limit = max(5, min(recent_limit, 200))
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    stmt = select(InjectionDetection).where(
        InjectionDetection.created_at >= cutoff
    )
    rows = (await db.execute(stmt)).scalars().all()

    by_decision = _summarize(rows)
    by_severity = dict(Counter(r.severity for r in rows))
    by_source_kind = dict(Counter(r.source_kind for r in rows))

    # Pattern top-N. Each row carries 1+ matched patterns; flatten
    # and count by pattern_id. Description + severity come along from
    # the first hit (every row carrying the same pattern_id has the
    # same description by construction — pattern catalog is static).
    pattern_counter: Counter[str] = Counter()
    pattern_descriptions: dict[str, str] = {}
    pattern_severities: dict[str, str] = {}
    for r in rows:
        for m in r.matched_patterns or []:
            pid = m.get("pattern_id")
            if not pid:
                continue
            pattern_counter[pid] += 1
            pattern_descriptions.setdefault(pid, m.get("description", ""))
            pattern_severities.setdefault(
                pid, m.get("severity_floor", "low")
            )

    pattern_top = [
        PatternHitCount(
            pattern_id=pid,
            description=pattern_descriptions[pid],
            hits=hits,
            severity_floor=pattern_severities[pid],
        )
        for pid, hits in pattern_counter.most_common(10)
    ]

    llm_calls = sum(1 for r in rows if r.llm_judge_called)
    cost_estimate = round(llm_calls * _HAIKU_CALL_COST_USD, 4)

    recent_stmt = (
        select(InjectionDetection)
        .where(InjectionDetection.created_at >= cutoff)
        .order_by(desc(InjectionDetection.created_at))
        .limit(recent_limit)
    )
    recent_rows = (await db.execute(recent_stmt)).scalars().all()

    return SafetySummary(
        window_hours=hours,
        total_scanned=len(rows),
        total_allowed=by_decision["allowed"],
        total_flagged=by_decision["flagged"],
        total_blocked=by_decision["blocked"],
        by_severity=by_severity,
        by_source_kind=by_source_kind,
        pattern_top=pattern_top,
        llm_judge_calls=llm_calls,
        cost_estimate_usd=cost_estimate,
        recent=[_to_row(r) for r in recent_rows],
    )


@router.get("/detections", response_model=list[InjectionDetectionRow])
async def list_detections(
    user: CurrentUserDep,
    db: DbSessionDep,
    hours: int = 24,
    severity: str | None = None,
    decision: str | None = None,
    source_kind: str | None = None,
    limit: int = 100,
) -> list[InjectionDetectionRow]:
    """Filterable list for the dashboard's recent-events table."""
    hours = max(1, min(hours, 720))
    limit = max(10, min(limit, 500))
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    stmt = select(InjectionDetection).where(
        InjectionDetection.created_at >= cutoff
    )
    if severity:
        stmt = stmt.where(InjectionDetection.severity == severity)
    if decision:
        stmt = stmt.where(InjectionDetection.decision == decision)
    if source_kind:
        stmt = stmt.where(InjectionDetection.source_kind == source_kind)
    stmt = stmt.order_by(desc(InjectionDetection.created_at)).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return [_to_row(r) for r in rows]


@router.get(
    "/detections/{detection_id}",
    response_model=InjectionDetectionDetail,
)
async def detection_detail(
    detection_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> InjectionDetectionDetail:
    """Drawer payload — full matched-patterns list + Haiku critique +
    raw excerpt."""
    row = (
        await db.execute(
            select(InjectionDetection).where(
                InjectionDetection.id == detection_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "detection not found"
        )

    base = _to_row(row)
    return InjectionDetectionDetail(
        **base.model_dump(),
        matched_patterns=row.matched_patterns or [],
        llm_judge_critique=row.llm_judge_critique,
        raw_text_excerpt=row.raw_text_excerpt,
    )


@router.get(
    "/loans/{loan_id}/detections",
    response_model=list[InjectionDetectionRow],
)
async def loan_detections(
    loan_id: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
    limit: int = 50,
) -> list[InjectionDetectionRow]:
    """All-time detections for one loan — powers the loan-detail
    SafetyChip + drawer."""
    limit = max(1, min(limit, 200))
    stmt = (
        select(InjectionDetection)
        .where(InjectionDetection.loan_id == loan_id)
        .order_by(desc(InjectionDetection.created_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_row(r) for r in rows]


@router.get("/judgments", response_model=JudgmentSummary)
async def judgment_summary(
    user: CurrentUserDep,
    db: DbSessionDep,
    hours: int = 24,
    limit: int = 100,
) -> JudgmentSummary:
    """Output-side guardrail rollup.

    Sources from ``agent_runs.payload.guardrail_judgment`` — written
    by the validator nodes in intake, underwriting, and decision.
    No new table; the data is already there.
    """
    hours = max(1, min(hours, 720))
    limit = max(10, min(limit, 500))
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    # Find runs whose payload contains guardrail_judgment. The
    # JSONB ``?`` operator checks for top-level key existence — cheap
    # because the agent_runs table is small (one row per run).
    stmt = (
        select(AgentRun)
        .where(AgentRun.created_at >= cutoff)
        .where(AgentRun.payload.has_key("guardrail_judgment"))  # noqa: W601
        .order_by(desc(AgentRun.created_at))
        .limit(limit)
    )
    runs = (await db.execute(stmt)).scalars().all()

    rows: list[JudgmentRow] = []
    sev_counter: Counter[str] = Counter()
    agent_counter: Counter[str] = Counter()
    retry_counter: Counter[int] = Counter()

    for run in runs:
        payload = run.payload or {}
        judgment = payload.get("guardrail_judgment") or {}
        if not judgment:
            continue
        severity = judgment.get("severity", "ok")
        attempts = payload.get("validation_attempts", 0)
        sev_counter[severity] += 1
        agent_counter[run.agent_name] += 1
        retry_counter[attempts] += 1
        rows.append(
            JudgmentRow(
                agent_run_id=str(run.id),
                agent_name=run.agent_name,
                loan_id=str(run.loan_id),
                started_at=run.created_at,
                severity=severity,
                attempts=attempts,
                failed_principles=judgment.get(
                    "failed_principles", []
                ),
                failed_red_lines=judgment.get("failed_red_lines", []),
                critique=judgment.get("critique"),
                constitution_hint=_constitution_hint_for(run.agent_name),
            )
        )

    return JudgmentSummary(
        window_hours=hours,
        total_judgments=len(rows),
        by_severity=dict(sev_counter),
        by_agent=dict(agent_counter),
        retry_distribution=dict(retry_counter),
        rows=rows,
    )


class ScenarioRow(BaseModel):
    """Wire shape of a Scenario. Mirrors the dataclass exactly so the
    frontend can render the catalog without a separate translation
    step."""

    id: str
    category: str
    title: str
    threat: str
    defense: str
    defense_layer: str
    test_id: str | None
    severity: str
    status: str


class ScenariosResponse(BaseModel):
    """Two top-level slices the UI groups by — protected + known-gap.

    Splitting in the response lets the frontend render them in
    separate sections without a client-side filter pass.
    """

    protected: list[ScenarioRow]
    known_gaps: list[ScenarioRow]


@router.get("/scenarios", response_model=ScenariosResponse)
async def list_scenarios(user: CurrentUserDep) -> ScenariosResponse:
    """Return the safety scenarios catalog.

    Static manifest from ``mkopo.safety.scenarios.SCENARIOS`` — each
    entry is a (threat, defense, test_id, severity) tuple describing
    one robustness property the system pins. Backed by the tests in
    ``tests/test_safety_scenarios.py`` (CI failure on a test → the
    scenario card on the UI flips to a regression banner).
    """
    rows = [ScenarioRow(**s.to_dict()) for s in SCENARIOS]
    return ScenariosResponse(
        protected=[r for r in rows if r.status == "protected"],
        known_gaps=[r for r in rows if r.status == "known-gap"],
    )


# Pretty label for the dashboard "what got judged" column.
def _constitution_hint_for(agent_name: str) -> str:
    return {
        "intake": "intake.doc_request_v1",
        "underwriting": "underwriting.summary_v1",
        "decision": "decision.verdict_v1 + adverse_action_letter_v1",
    }.get(agent_name, agent_name)
