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

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.deps import CurrentBorrowerDep, DbSessionDep
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
    # Optional password — when present we hash it onto the new
    # borrower's user row so they can log in later by password. When
    # absent (the recommended UX) the account is magic-link-only
    # until the borrower sets a password from their dashboard.
    borrower_password: str | None = Field(default=None, min_length=8, max_length=256)


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
    AI-drafted prose, just where the loan is and what's expected next.

    ``loan_class`` is exposed so the borrower portal can render the
    right document-upload hints (personal: pay stubs / tax returns /
    bank statements / ID; business: appraisal / rent roll / etc.).
    Without this, the upload card showed the business set to every
    applicant.

    ``required_docs`` is the per-class list the rules engine would
    refuse to advance the loan without. Surfacing it to the borrower
    early ("we need these four document types") is a much better UX
    than "intake ran, here's an email asking for them" — they see
    the checklist on first load.
    """

    loan_id: uuid.UUID
    reference: str
    stage: str
    next_step: str
    submitted_at: str
    loan_class: str
    required_docs: list[str]
    documents: list[dict[str, Any]]


# ----- create -----------------------------------------------------------


@router.post(
    "/apply",
    response_model=BorrowerApplyOut,
    status_code=status.HTTP_201_CREATED,
)
async def borrower_apply(
    payload: BorrowerApplyIn, response: Response, db: DbSessionDep
) -> BorrowerApplyOut:
    """Create a new loan from a borrower-facing application.

    Auth integration (Phase 1b): this endpoint stays publicly
    callable so a new borrower can apply without going through a
    separate signup step. As part of the application we:

      1. Create a ``users`` row with ``role='borrower'``. If the
         payload includes a password we hash it; otherwise the
         account is magic-link-only (the borrower will get an
         email with a "set password" link in Phase 1b's email
         delivery work).
      2. Set the ``mkopo_session`` cookie so the borrower is
         signed in immediately and can land on ``/apply/{id}``
         without a separate login step.
      3. Reject duplicate emails with 409 — frontend redirects
         to ``/login`` so the existing user can sign in first.

    Mirrors LoanCreate but:
    - the actor type on every audit event is ``borrower`` (not user),
      so the internal timeline colours these actions distinctly;
    - the loan starts in ``intake`` regardless of autonomy mode (the
      borrower can't fast-track themselves; that's a sponsor decision);
    - the borrower's contact email is stored in ``loan.meta`` so the
      intake agent's email tool finds it.
    """
    from mkopo.models import User as UserModel
    from mkopo.services.auth_service import (
        SESSION_COOKIE,
        hash_password,
        issue_jwt,
    )

    if not payload.borrower.email:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "An email address is required so we can reach you about your application.",
        )
    email = payload.borrower.email.lower().strip()

    # Refuse if there's already an account for this email. The user
    # should sign in first; we return 409 so the frontend can route
    # them to /login. We do NOT auto-link to an existing user from
    # an unauthenticated request — that would let anyone open new
    # applications under someone else's identity.
    existing_user = (
        await db.execute(select(UserModel).where(UserModel.email == email))
    ).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An account with that email already exists — please sign in to "
            "submit another application.",
        )

    # New borrower user. Password is optional in the apply payload
    # for Phase 1b's "magic-link-first" UX; if absent, the account
    # is magic-link-only until the borrower sets a password from
    # their dashboard.
    borrower_user = UserModel(
        email=email,
        name=payload.borrower.name or email.split("@", 1)[0],
        role="borrower",
        password_hash=hash_password(payload.borrower_password)
        if getattr(payload, "borrower_password", None)
        else None,
    )
    db.add(borrower_user)
    await db.flush()

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

    # Sign the new borrower in immediately by setting the session
    # cookie. Their next request to /borrower-portal/loans/{id}/status
    # or /borrower-portal/loans/{id}/documents will then carry the
    # cookie and pass the ownership gate.
    from mkopo.config import get_settings as _get_settings

    settings = _get_settings()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=issue_jwt(borrower_user),
        max_age=settings.jwt_session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )

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


async def _assert_borrower_owns_loan(
    db: AsyncSession, loan: Loan, user_email: str
) -> None:
    """Refuse the request if the signed-in borrower isn't the loan's
    borrower party.

    Closes the "anyone with a loan id can act on it" gap the audit
    flagged earlier. The match is by email — same key the borrower
    portal uses to identify the borrower across the application
    form and the audit log.

    Returns ``None`` on success (caller proceeds); raises HTTP 403
    on mismatch. 403 (not 404) because the loan *does* exist; the
    caller just isn't entitled to it. We don't differentiate
    "wrong loan id" from "not your loan" in the message — both
    look identical to a client we don't trust.
    """
    row = (
        await db.execute(
            select(Party.email)
            .join(LoanParty, LoanParty.party_id == Party.id)
            .where(
                LoanParty.loan_id == loan.id,
                LoanParty.role == PartyRole.BORROWER,
            )
        )
    ).scalar_one_or_none()
    if row is None or (row or "").lower().strip() != user_email.lower().strip():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This loan isn't associated with your account",
        )


@router.post(
    "/loans/{loan_id}/documents",
    status_code=status.HTTP_201_CREATED,
)
async def borrower_upload_document(
    loan_id: uuid.UUID,
    user: CurrentBorrowerDep,
    db: DbSessionDep,
    file: UploadFile = File(...),
) -> dict[str, object]:
    """Borrower attaches a document to their own loan.

    Same storage + PDF extraction pipeline as the internal upload —
    we just write a borrower-typed audit event so the case-file
    timeline reads "borrower attached X" rather than "user attached X".

    Authz: caller must be the signed-in borrower AND the loan's
    borrower party must be them. Both checks fire on every request.
    """
    loan = (await db.execute(select(Loan).where(Loan.id == loan_id))).scalar_one_or_none()
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    await _assert_borrower_owns_loan(db, loan, user.email)
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

    import hashlib

    document = Document(
        loan_id=loan_id,
        filename=file.filename,
        doc_type=DocumentType.UNKNOWN,
        storage_uri=uri,
        content_type=content_type,
        size_bytes=len(body),
        # sha256 of the bytes — feeds materials hash so a borrower
        # can't quietly swap their appraisal between approval and
        # closing without it being detected.
        content_hash=hashlib.sha256(body).hexdigest(),
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
async def borrower_status(
    loan_id: uuid.UUID, user: CurrentBorrowerDep, db: DbSessionDep
) -> BorrowerStatusOut:
    """Borrower-facing status projection of their loan.

    Authz: requires a borrower session, and the loan's borrower
    party must be the signed-in user. Mismatch → 403.
    """
    loan = (await db.execute(select(Loan).where(Loan.id == loan_id))).scalar_one_or_none()
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    await _assert_borrower_owns_loan(db, loan, user.email)

    docs = (
        await db.execute(
            select(Document).where(Document.loan_id == loan_id).order_by(Document.created_at)
        )
    ).scalars().all()

    # Required-docs list scoped to the loan's class. Pulled from the
    # rules engine's REQUIRED_DOCS_* sets so the borrower portal's
    # checklist stays in sync with what underwriting will actually
    # demand at the prerequisite gate — no risk of asking the
    # borrower for documents we won't accept, or skipping documents
    # we'll later refuse to advance without.
    from mkopo.rules.policy import REQUIRED_DOCS, REQUIRED_DOCS_PERSONAL

    loan_class_str = (
        loan.loan_class.value if loan.loan_class is not None else "business"
    )
    required = (
        REQUIRED_DOCS_PERSONAL if loan_class_str == "personal" else REQUIRED_DOCS
    )
    return BorrowerStatusOut(
        loan_id=loan.id,
        reference=loan.reference,
        stage=loan.stage.value,
        next_step=_next_step_for_borrower(loan.stage),
        submitted_at=loan.created_at.isoformat(),
        loan_class=loan_class_str,
        required_docs=sorted(required),
        documents=[
            {
                "id": str(d.id),
                "filename": d.filename,
                "uploaded_at": d.created_at.isoformat(),
                "size_bytes": d.size_bytes,
                "content_type": d.content_type,
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
        return (
            "Your application has been declined. You should have received "
            "a notification by email with the reason."
        )
    return "We're reviewing your application."
