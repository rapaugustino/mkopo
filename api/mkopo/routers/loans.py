"""Loan REST endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import (
    AuditEvent,
    Condition,
    Extraction,
    Loan,
    LoanParty,
    Party,
    PartyRole,
    PartyType,
)
from mkopo.schemas import (
    AskRequest,
    AskResponse,
    AuditEventOut,
    ComparableLoanOut,
    ConditionOut,
    ExtractionOut,
    LoanCreate,
    LoanOut,
    NoteIn,
    StageTransitionIn,
)
from mkopo.services import loans as loan_service
from mkopo.services.audit import Actor, record
from mkopo.services.comparables import comparable_loans
from mkopo.services.qa import answer_question

router = APIRouter(prefix="/loans", tags=["loans"])


@router.get("", response_model=list[LoanOut])
async def list_loans(user: CurrentUserDep, db: DbSessionDep) -> list[Loan]:
    # ``deleted_at IS NULL`` keeps soft-deleted loans out of the
    # internal pipeline view — once a borrower requests erasure, the
    # loan disappears from operational surfaces immediately even
    # though the row sticks around for the regulatory retention
    # window. Cited by the partial index ``ix_loans_active``.
    result = await db.execute(
        select(Loan)
        .where(Loan.deleted_at.is_(None))
        .order_by(Loan.created_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())


@router.post("", response_model=LoanOut, status_code=status.HTTP_201_CREATED)
async def create_loan(payload: LoanCreate, user: CurrentUserDep, db: DbSessionDep) -> Loan:
    from mkopo.models import LoanClass

    # Validate the loan_class on the boundary — the inbound payload
    # is a plain string from JSON. Falls back to BUSINESS rather than
    # raising so a typo doesn't 500; the audit event still records
    # what the client sent.
    try:
        klass = LoanClass(payload.loan_class)
    except ValueError:
        klass = LoanClass.BUSINESS

    loan = Loan(
        loan_type=payload.loan_type,
        loan_class=klass,
        amount=payload.amount,
        meta={"borrower_email": payload.borrower_email},
    )
    db.add(loan)
    await db.flush()

    for p in payload.parties:
        party = Party(
            name=p.name,
            party_type=PartyType(p.party_type),
            email=p.email,
        )
        db.add(party)
        await db.flush()
        db.add(LoanParty(loan_id=loan.id, party_id=party.id, role=PartyRole(p.role)))

    await record(
        db,
        loan_id=loan.id,
        actor=Actor.user(user.user_id),
        action="loan_created",
        payload={"amount": str(payload.amount), "loan_type": payload.loan_type.value},
    )
    await db.commit()
    await db.refresh(loan)
    return loan


@router.get("/{loan_id}", response_model=LoanOut)
async def get_loan(loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep) -> Loan:
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    return loan


@router.post("/{loan_id}/transition", response_model=LoanOut)
async def transition(
    loan_id: uuid.UUID,
    payload: StageTransitionIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> Loan:
    try:
        loan = await loan_service.transition_stage(
            db,
            loan_id=loan_id,
            to_stage=payload.to_stage,
            actor=Actor.user(user.user_id),
            reason=payload.reason,
        )
        await db.commit()
        return loan
    except loan_service.IllegalStageTransitionError as e:
        # 422 is the right semantic for "the request is well-formed but
        # the loan's current state forbids this action". 400 would have
        # said "your request is malformed" which it isn't.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e


class AutonomyIn(BaseModel):
    """PATCH payload for the autonomy toggle. The reason is required —
    it goes onto the audit event so committee reviewers can see *why*
    a particular deal was put on or off the autonomous track."""

    level: str  # "assisted" | "autonomous"
    reason: str


@router.patch("/{loan_id}/autonomy", response_model=LoanOut)
async def set_autonomy(
    loan_id: uuid.UUID,
    payload: AutonomyIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> Loan:
    """Switch a loan between ``assisted`` and ``autonomous`` modes.

    Autonomous mode lets the orchestrator chain agents end-to-end
    (intake → underwriting → decision) without prompting the
    underwriter at each step. Irreversible HITL gates (sending
    borrower email, sending the decision package) are still
    human-only — the toggle does not bypass them.

    The mode change writes an ``autonomy_changed`` audit event so the
    decision to fast-track (or slow down) a loan is itself part of the
    auditable record.
    """
    from mkopo.models import AutonomyLevel

    if payload.level not in ("assisted", "autonomous"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid autonomy level: {payload.level}",
        )
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    from_level = (
        loan.autonomy_level.value
        if hasattr(loan.autonomy_level, "value")
        else str(loan.autonomy_level)
    )
    loan.autonomy_level = AutonomyLevel(payload.level)
    await record(
        db,
        loan_id=loan_id,
        actor=Actor.user(user.user_id),
        action="autonomy_changed",
        payload={
            "from": from_level,
            "to": payload.level,
            "reason": payload.reason,
        },
    )
    await db.commit()
    return loan


@router.get("/{loan_id}/transitions")
async def list_allowed_transitions(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> dict[str, str | None]:
    """Per-stage readiness map: ``{<stage>: null}`` if ready, otherwise
    ``{<stage>: "<reason>"}``. The UI uses this to disable a transition
    button before the user clicks it, with the reason as the tooltip.

    Cheap to compute — the prerequisite checks are bounded `SELECT
    .. LIMIT 1` queries against indexed columns.
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    return await loan_service.allowed_transitions(db, loan)


class MaterialsStatus(BaseModel):
    """Materials-hash drift status for a loan.

    Powers the "your decision is stale" banner. Exposed as its own
    endpoint (rather than folded into ``GET /loans/{id}``) so the UI
    can poll it cheaply and refresh the banner without re-fetching
    the entire loan blob — the moment an extraction is overridden or
    a document re-uploaded, the next poll flips ``drifted`` true.

    ``decision_hash`` is ``None`` until the decision agent has run
    at least once. ``drifted`` is therefore ``False`` for pre-
    decision loans even if their materials are churning — there's
    nothing to drift from.
    """

    drifted: bool
    current_hash: str
    decision_hash: str | None


@router.get("/{loan_id}/materials/status", response_model=MaterialsStatus)
async def materials_status(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> MaterialsStatus:
    """Has anything fed-into-the-decision changed since the decision
    agent last ran?

    Used by the loan detail page to render a prominent drift banner.
    Cheap (a few indexed SELECTs + a sha256) so safe to poll.
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    from mkopo.services.materials_hash import materials_drift_detected

    drifted, current_hash, decision_hash = await materials_drift_detected(db, loan_id)
    return MaterialsStatus(
        drifted=drifted, current_hash=current_hash, decision_hash=decision_hash
    )


@router.get("/{loan_id}/extractions", response_model=list[ExtractionOut])
async def list_extractions(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> list[Extraction]:
    from mkopo.models import Document

    stmt = (
        select(Extraction)
        .join(Document)
        .where(Document.loan_id == loan_id)
        .order_by(Extraction.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{loan_id}/rules")
async def get_rules_preview(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> dict[str, object]:
    """Deterministic rules + KPIs for a loan, no LLM call involved.

    The underwriting agent runs ``fetch_and_evaluate`` (rules + KPIs)
    before its LLM summary node. This endpoint exposes that first half
    directly so the workspace can render extractions, KPIs, and risk
    signals at all times — even before the agent has been kicked off.
    The agent's cited prose is the only thing that requires Run.

    Returns ``{kpis, risk_flags, extractions}`` mirroring the relevant
    subset of UnderwritingResult.
    """
    if not await loan_service.get_loan(db, loan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    from mkopo.services.rules_eval import evaluate

    result = await evaluate(db, loan_id)
    ctx = result.ctx
    ltv = float(ctx.loan_amount / ctx.appraised_value) if ctx.appraised_value else None
    dscr = (
        float(ctx.annual_noi / ctx.annual_debt_service)
        if ctx.annual_noi and ctx.annual_debt_service
        else None
    )
    debt_yield = float(ctx.annual_noi / ctx.loan_amount) if ctx.annual_noi else None
    doc_confidence = (
        sum(result.confidences.values()) / len(result.confidences)
        if result.confidences
        else None
    )
    return {
        "kpis": {
            "loan_amount": str(ctx.loan_amount),
            "ltv": ltv,
            "dscr": dscr,
            "debt_yield": debt_yield,
            "doc_confidence": doc_confidence,
            "property_type": ctx.property_type.value
            if hasattr(ctx.property_type, "value")
            else str(ctx.property_type),
        },
        "risk_flags": [f.model_dump(mode="json") for f in result.flags],
        "extractions": result.extractions,
    }


@router.post("/{loan_id}/notes", response_model=AuditEventOut, status_code=status.HTTP_201_CREATED)
async def add_note(
    loan_id: uuid.UUID,
    payload: NoteIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> AuditEvent:
    """Write an internal note (or borrower-reply transcript) to the case file.

    The note becomes an audit_events row — same source-of-truth surface
    the timeline reads from, so it shows up there as a `user` event with
    a serif quote block.
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    event = await record(
        db,
        loan_id=loan_id,
        actor=Actor.user(user.user_id),
        action=payload.kind,
        payload={"body_text": payload.text},
    )
    await db.commit()
    return event


@router.get("/{loan_id}/audit", response_model=list[AuditEventOut])
async def list_audit_events(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.loan_id == loan_id)
        .order_by(AuditEvent.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{loan_id}/conditions", response_model=list[ConditionOut])
async def list_conditions(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> list[Condition]:
    """All conditions on this loan — drafted-by-agent + manually added — most recent first."""
    stmt = (
        select(Condition).where(Condition.loan_id == loan_id).order_by(Condition.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("/{loan_id}/ask", response_model=AskResponse)
async def ask(
    loan_id: uuid.UUID,
    payload: AskRequest,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> AskResponse:
    """RAG Q&A — embed the question, retrieve chunks + comparable loans, answer."""
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    return await answer_question(db, loan_id=loan_id, question=payload.question)


@router.get("/{loan_id}/comparables", response_model=list[ComparableLoanOut])
async def get_comparables(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep, limit: int = 5
) -> list[ComparableLoanOut]:
    """Top-K most similar already-underwritten loans by cosine on summary embedding.

    Returns 200 with [] if the loan hasn't been underwritten yet (no embedding).
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    comps = await comparable_loans(db, loan_id, limit=limit)
    return [
        ComparableLoanOut(
            loan_id=c.loan_id,
            reference=c.reference,
            borrower=c.borrower,
            loan_type=c.loan_type,
            amount=c.amount,
            risk_band=c.risk_band,
            similarity=c.similarity,
        )
        for c in comps
    ]
