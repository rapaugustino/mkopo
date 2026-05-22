"""Borrower-facing portal.

This is the second app surface — a public route where a business
fills in a loan application themselves. Everything they do mirrors
into the internal platform so the underwriter sees the application
land in their pipeline in real time.

Why a separate router (and a separate auth model):

- The internal app's auth is bearer-token / future-Clerk; the
  borrower portal is intentionally low-friction (email + a one-time
  link). Mixing the two complicates both.
- Borrower endpoints write to the same tables (loans, parties,
  documents, audit_events) but their actor_type is ``borrower`` so
  the case-file timeline can colour-code their actions distinctly.
- A borrower can read their own loan; they CANNOT read others'.
  This module enforces that with a per-request loan-scoped check.

Scope for the portfolio demo: no real auth, no rate limiting, no
email verification. The endpoints exist to demonstrate the
dual-surface story. A production deployment would put Clerk
borrower-only sessions in front of these routes.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from mkopo.deps import DbSessionDep
from mkopo.models import (
    AutonomyLevel,
    Document,
    DocumentType,
    Loan,
    LoanClass,
    LoanParty,
    LoanStage,
    LoanType,
    Party,
    PartyRole,
    PartyType,
)
from mkopo.services.audit import Actor, record
from mkopo.services.ingest import embed_document
from mkopo.services.pdf import extract_text as extract_pdf_text
from mkopo.services.storage import get_storage

router = APIRouter(prefix="/borrower-portal", tags=["borrower-portal"])


# ----- payloads ---------------------------------------------------------


class BorrowerPartyIn(BaseModel):
    """Either the primary borrower entity or an attached guarantor."""

    name: str = Field(min_length=2, max_length=128)
    party_type: str = Field(pattern="^(entity|person)$")
    email: EmailStr | None = None


class BorrowerApplyIn(BaseModel):
    """Self-service loan application payload."""

    # Personal vs. business — defaults to business for backwards
    # compatibility, but the portal form surfaces an explicit picker
    # so this default rarely fires in practice. The class drives:
    # which intake fields are required, which rules-engine policy
    # path runs, and which underwriting agent prompt is used.
    loan_class: str = "business"
    loan_type: LoanType
    amount: Decimal = Field(gt=0)
    purpose: str | None = Field(default=None, max_length=512)
    borrower: BorrowerPartyIn
    guarantors: list[BorrowerPartyIn] = []
    property_address: str | None = Field(default=None, max_length=512)
    property_type: str | None = Field(default=None, max_length=64)
    # Personal-loan-specific (optional even when present so the
    # form can submit partial state and the intake agent can chase
    # the rest via the borrower-email flow).
    annual_income: Decimal | None = None
    employer: str | None = Field(default=None, max_length=128)
    credit_score: int | None = Field(default=None, ge=300, le=850)
    # New: monthly debt payments + employment tenure feed the personal
    # rule pack (DTI + employment-tenure rules). Both optional so the
    # form can still submit; the rules emit "warn" outcomes when the
    # values aren't on file rather than blocking.
    monthly_debt_payments: Decimal | None = Field(default=None, ge=0)
    years_employment: Decimal | None = Field(default=None, ge=0, le=80)


class BorrowerApplyOut(BaseModel):
    """Returned on application submit — the borrower remembers the id
    to come back and check status."""

    loan_id: uuid.UUID
    reference: str
    stage: str
    message: str


class BorrowerStatusOut(BaseModel):
    """The status view a borrower sees when they return to the portal.
    Deliberately a thin projection — no internal risk flags, no
    AI-drafted prose, just where the loan is and what's expected next."""

    loan_id: uuid.UUID
    reference: str
    stage: str
    next_step: str
    submitted_at: str
    documents: list[dict[str, Any]]


# ----- create -----------------------------------------------------------


@router.post(
    "/apply",
    response_model=BorrowerApplyOut,
    status_code=status.HTTP_201_CREATED,
)
async def borrower_apply(payload: BorrowerApplyIn, db: DbSessionDep) -> BorrowerApplyOut:
    """Create a new loan from a borrower-facing application.

    Mirrors LoanCreate but:
    - the actor type on every audit event is ``borrower`` (not user),
      so the internal timeline colours these actions distinctly;
    - the loan starts in ``intake`` regardless of autonomy mode (the
      borrower can't fast-track themselves; that's a sponsor decision);
    - the borrower's contact email is stored in ``loan.meta`` so the
      intake agent's email tool finds it.
    """
    try:
        klass = LoanClass(payload.loan_class)
    except ValueError:
        klass = LoanClass.BUSINESS

    loan = Loan(
        loan_type=payload.loan_type,
        loan_class=klass,
        amount=payload.amount,
        stage=LoanStage.INTAKE,
        autonomy_level=AutonomyLevel.ASSISTED,
        meta={
            "borrower_email": payload.borrower.email,
            "borrower_submitted_via_portal": True,
            "purpose": payload.purpose,
            "property_address": payload.property_address,
            "property_type": payload.property_type,
            # Personal-loan-specific fields land in meta because they
            # don't fit the commercial-real-estate-shaped extractions
            # table. The underwriting agent reads them from meta.
            "annual_income": str(payload.annual_income)
            if payload.annual_income is not None
            else None,
            "employer": payload.employer,
            "credit_score": payload.credit_score,
            "monthly_debt_payments": str(payload.monthly_debt_payments)
            if payload.monthly_debt_payments is not None
            else None,
            "years_employment": str(payload.years_employment)
            if payload.years_employment is not None
            else None,
        },
    )
    db.add(loan)
    await db.flush()
    await db.refresh(loan)

    # Borrower party
    borrower_party = Party(
        name=payload.borrower.name,
        party_type=PartyType(payload.borrower.party_type),
        email=payload.borrower.email,
    )
    db.add(borrower_party)
    await db.flush()
    db.add(
        LoanParty(loan_id=loan.id, party_id=borrower_party.id, role=PartyRole.BORROWER)
    )

    # Optional guarantors
    for g in payload.guarantors:
        gp = Party(
            name=g.name,
            party_type=PartyType(g.party_type),
            email=g.email,
        )
        db.add(gp)
        await db.flush()
        db.add(LoanParty(loan_id=loan.id, party_id=gp.id, role=PartyRole.GUARANTOR))

    # Audit: the application landing event. ``actor=Actor.borrower(email)``
    # gives the case-file timeline its borrower-coloured row.
    await record(
        db,
        loan_id=loan.id,
        actor=Actor.borrower(payload.borrower.email or "unknown"),
        action="borrower_applied",
        payload={
            "borrower": payload.borrower.name,
            "amount": str(payload.amount),
            "loan_type": payload.loan_type.value,
            "guarantors": [g.name for g in payload.guarantors],
            "property_address": payload.property_address,
            "property_type": payload.property_type,
            "purpose": payload.purpose,
        },
    )
    await db.commit()
    await db.refresh(loan)

    return BorrowerApplyOut(
        loan_id=loan.id,
        reference=loan.reference,
        stage=loan.stage.value,
        message=(
            "Application received. An underwriter will review your packet shortly. "
            "Save your reference to come back and check status."
        ),
    )


# ----- upload docs from the portal --------------------------------------


@router.post(
    "/loans/{loan_id}/documents",
    status_code=status.HTTP_201_CREATED,
)
async def borrower_upload_document(
    loan_id: uuid.UUID, db: DbSessionDep, file: UploadFile = File(...)
) -> dict[str, object]:
    """Borrower attaches a document to their own loan.

    Same storage + PDF extraction pipeline as the internal upload —
    we just write a borrower-typed audit event so the case-file
    timeline reads "borrower attached X" rather than "user attached X".
    """
    loan = (await db.execute(select(Loan).where(Loan.id == loan_id))).scalar_one_or_none()
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Filename required")

    body = await file.read()
    content_type = file.content_type or "application/octet-stream"

    storage = get_storage()
    uri = await storage.put_object(
        loan_id=loan_id,
        filename=file.filename,
        body=body,
        content_type=content_type,
    )

    # Text extraction (same as the internal /documents path).
    text_content = ""
    extract_stats: dict[str, Any] = {"method": "skipped"}
    if content_type.startswith("text/"):
        text_content = body.decode("utf-8", errors="ignore")
        extract_stats = {"method": "decode", "char_count": len(text_content)}
    elif content_type == "application/pdf":
        text_content, extract_stats = extract_pdf_text(body)
        extract_stats = {**extract_stats, "method": "pypdf"}

    document = Document(
        loan_id=loan_id,
        filename=file.filename,
        doc_type=DocumentType.UNKNOWN,
        storage_uri=uri,
        content_type=content_type,
        size_bytes=len(body),
        meta={"text_content": text_content, "extract": extract_stats},
    )
    db.add(document)
    await db.flush()
    chunk_count = await embed_document(db, document)

    borrower_email = (loan.meta or {}).get("borrower_email") or "unknown"
    await record(
        db,
        loan_id=loan_id,
        actor=Actor.borrower(borrower_email),
        action="borrower_document_uploaded",
        payload={
            "filename": file.filename,
            "document_id": str(document.id),
            "content_type": content_type,
            "size_bytes": len(body),
            "chunks_embedded": chunk_count,
            **extract_stats,
        },
    )
    await db.commit()
    return {
        "document_id": str(document.id),
        "filename": file.filename,
        "extract": extract_stats,
    }


# ----- status check -----------------------------------------------------


@router.get("/loans/{loan_id}/status", response_model=BorrowerStatusOut)
async def borrower_status(loan_id: uuid.UUID, db: DbSessionDep) -> BorrowerStatusOut:
    """Borrower-facing status projection of their loan."""
    loan = (await db.execute(select(Loan).where(Loan.id == loan_id))).scalar_one_or_none()
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    docs = (
        await db.execute(
            select(Document).where(Document.loan_id == loan_id).order_by(Document.created_at)
        )
    ).scalars().all()

    return BorrowerStatusOut(
        loan_id=loan.id,
        reference=loan.reference,
        stage=loan.stage.value,
        next_step=_next_step_for_borrower(loan.stage),
        submitted_at=loan.created_at.isoformat(),
        documents=[
            {
                "filename": d.filename,
                "uploaded_at": d.created_at.isoformat(),
                "size_bytes": d.size_bytes,
            }
            for d in docs
        ],
    )


def _next_step_for_borrower(stage: LoanStage) -> str:
    """Borrower-facing copy describing what happens next. Internal
    vocabulary like "rules engine" / "policy gate" stays out of this
    surface; the borrower sees plain English."""
    if stage == LoanStage.INTAKE:
        return "Our team is reviewing your application. We may email if any documents are missing."
    if stage == LoanStage.UNDERWRITING:
        return "Your application is being underwritten. No action needed."
    if stage == LoanStage.DECISION:
        return "Our credit committee is reviewing your application."
    if stage == LoanStage.CONDITIONS:
        return "Your loan has been conditionally approved. We'll send the conditions to close."
    if stage == LoanStage.CLOSING:
        return "Your loan is in closing. We'll be in touch about scheduling."
    if stage == LoanStage.APPROVED:
        return "Your loan is approved. Closing documents are being prepared."
    if stage == LoanStage.SERVICING:
        return "Your loan has closed and is now being serviced."
    if stage == LoanStage.DECLINED:
        return "Your application has been declined. You should have received a notification by email with the reason."
    return "We're reviewing your application."
