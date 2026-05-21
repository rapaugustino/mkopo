"""Loan REST endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
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
    result = await db.execute(select(Loan).order_by(Loan.created_at.desc()).limit(100))
    return list(result.scalars().all())


@router.post("", response_model=LoanOut, status_code=status.HTTP_201_CREATED)
async def create_loan(payload: LoanCreate, user: CurrentUserDep, db: DbSessionDep) -> Loan:
    loan = Loan(
        loan_type=payload.loan_type,
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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


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
