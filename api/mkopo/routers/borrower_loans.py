"""Borrower self-service loan operations.

Split out of ``borrower_auth.py`` (which had grown to 1241 lines, half
of which was loan ops) so the auth router stays focused on
authentication: login, magic links, JWT issuance.

URL paths are unchanged — every endpoint here is still mounted under
the ``/borrower-auth`` prefix so the frontend doesn't change. FastAPI
accepts multiple routers with the same prefix; the auth router and
this one cooperate.

Surface:

  - ``GET  /borrower-auth/me/loans``                                — list my loans
  - ``GET  /borrower-auth/me/loans/{id}/documents/{doc_id}/download-url``
  - ``PATCH /borrower-auth/me/loans/{id}/fields``                  — whitelisted self-edit
  - ``POST /borrower-auth/me/loans/{id}/withdraw``                 — terminal stage transition
  - ``GET  /borrower-auth/me/data/export``                         — DSAR
  - ``POST /borrower-auth/me/erasure``                             — soft-delete + retention

Every mutation is gated by a fresh-auth challenge token from
``POST /borrower-auth/me/challenge`` (see ``borrower_auth.py``) —
even a valid session cookie isn't sufficient on its own. That was
#169's contract; we preserve it on the split.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.deps import CurrentBorrowerDep, DbSessionDep
from mkopo.models import (
    AuditEvent,
    Document,
    Loan,
    LoanParty,
    LoanStage,
    Party,
    PartyRole,
    User,
)
from mkopo.routers.borrower_portal import _next_step_for_borrower
from mkopo.services.audit import Actor, record
from mkopo.services.loans import IllegalStageTransitionError, transition_stage
from mkopo.services.redis_client import consume_challenge
from mkopo.services.storage import StorageAuthzError, mint_download_url

logger = structlog.get_logger()


router = APIRouter(prefix="/borrower-auth", tags=["borrower-loans"])


# --- Shared response schemas (used by /me/loans + /me/loans/.../withdraw) ----


class MyLoanRow(BaseModel):
    """One row in the borrower's loan list. Trimmed to the fields the
    /account landing page actually renders."""

    loan_id: str
    reference: str
    stage: str
    loan_type: str
    loan_class: str
    amount: str
    submitted_at: datetime
    next_step: str


def _loan_to_row(loan: Loan) -> MyLoanRow:
    """Shared row-shaper — keeps the loan-class coercion in one place."""
    return MyLoanRow(
        loan_id=str(loan.id),
        reference=loan.reference,
        stage=loan.stage.value,
        loan_type=loan.loan_type.value,
        loan_class=loan.loan_class.value
        if hasattr(loan.loan_class, "value")
        else str(loan.loan_class),
        amount=str(loan.amount),
        submitted_at=loan.created_at,
        next_step=_next_step_for_borrower(loan.stage),
    )


# --- Helpers ---------------------------------------------------------------


async def _require_challenge(*, user: User, token: str | None) -> None:
    """Sensitive-op gate. Raises 403 unless ``token`` is a valid
    fresh-auth challenge for ``user``. Single-use; the token is
    burned on success. Documented in borrower_auth.py:mint_reauth_challenge."""
    if not token:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Re-authentication required: call /me/challenge first.",
        )
    ok = await consume_challenge(user_id=user.id, plain_token=token)
    if not ok:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Re-authentication challenge is invalid or expired.",
        )


async def _assert_loan_owned_by(db: AsyncSession, loan_id: uuid.UUID, user: User) -> Loan:
    """Load the loan and confirm the signed-in borrower owns it.

    Same email-keyed check borrower_portal uses. Raises HTTP 404 if
    the loan doesn't exist; HTTP 403 if it does but the user isn't
    the borrower party.
    """
    loan = (await db.execute(select(Loan).where(Loan.id == loan_id))).scalar_one_or_none()
    if loan is None or loan.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
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
    if (row or "").lower().strip() != user.email.lower().strip():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This loan isn't associated with your account",
        )
    return loan


# --- /me/loans (listing) ---------------------------------------------------


@router.get("/me/loans", response_model=list[MyLoanRow])
async def my_loans(user: CurrentBorrowerDep, db: DbSessionDep) -> list[MyLoanRow]:
    """List the loans associated with the signed-in borrower.

    Match is by email — the borrower's ``users.email`` joins to the
    ``Party.email`` of every loan where they're the borrower party.
    Cheap query (indexed both sides) and the response stays small
    because borrowers typically have 1–3 loans, not 1000.

    Powers the ``/account`` landing page after login.
    """
    rows = (
        (
            await db.execute(
                select(Loan)
                .join(LoanParty, LoanParty.loan_id == Loan.id)
                .join(Party, Party.id == LoanParty.party_id)
                .where(
                    LoanParty.role == PartyRole.BORROWER,
                    Party.email == user.email,
                    Loan.deleted_at.is_(None),
                )
                .order_by(Loan.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    return [_loan_to_row(loan) for loan in rows]


# --- Withdraw --------------------------------------------------------------


class WithdrawRequest(BaseModel):
    """Borrower's explanation for walking away. Stored verbatim on the
    audit event so internal staff can see why the application died.

    ``challenge_token`` is the one-shot value from
    ``POST /borrower-auth/me/challenge`` — stolen session cookies
    shouldn't be able to trigger irreversible loan withdrawal."""

    reason: str = Field(min_length=1, max_length=500)
    challenge_token: str | None = Field(default=None, max_length=128)


@router.post("/me/loans/{loan_id}/withdraw", response_model=MyLoanRow)
async def withdraw_loan(
    loan_id: uuid.UUID,
    payload: WithdrawRequest,
    user: CurrentBorrowerDep,
    db: DbSessionDep,
) -> MyLoanRow:
    """Borrower withdraws their own application.

    Routes through ``services.loans.transition_stage`` so the
    transition + audit happen together and stage-machine rules are
    enforced. Withdrawal is terminal — the loan stays in the DB,
    anonymisable later via erasure but never re-opened.

    Gated by :func:`_require_challenge` — even a valid session cookie
    isn't sufficient without a fresh password re-entry. See #169.
    """
    await _require_challenge(user=user, token=payload.challenge_token)
    loan = await _assert_loan_owned_by(db, loan_id, user)

    try:
        await transition_stage(
            db,
            loan_id=loan.id,
            to_stage=LoanStage.WITHDRAWN,
            actor=Actor.borrower(user.email),
            reason=payload.reason,
        )
    except IllegalStageTransitionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    await db.commit()
    await db.refresh(loan)
    return _loan_to_row(loan)


# --- Field self-edit -------------------------------------------------------

# Fields a borrower can self-edit via the REST patch endpoint. Each
# entry is ``(meta_key, coerce_fn)``. Anything not on the whitelist
# is silently ignored. Decision-feeding fields are deliberately
# included; if a change drifts the materials hash, the Phase 0
# guard refuses forward stage transitions until decision is re-run.
_BORROWER_EDITABLE_META: dict[str, tuple[str, Any]] = {
    "annual_income": ("annual_income", str),
    "monthly_debt_payments": ("monthly_debt_payments", str),
    "employer": ("employer", str),
    "credit_score": ("credit_score", int),
    "years_employment": ("years_employment", str),
    "purpose": ("purpose", str),
}


class LoanFieldsUpdate(BaseModel):
    """Whitelisted borrower-supplied fields. Pre-decision edits flow
    through here; post-decision they still work but materials drift
    detection will fire and block forward transitions."""

    annual_income: float | None = None
    monthly_debt_payments: float | None = None
    employer: str | None = Field(default=None, max_length=128)
    credit_score: int | None = Field(default=None, ge=300, le=850)
    years_employment: float | None = Field(default=None, ge=0, le=80)
    purpose: str | None = Field(default=None, max_length=512)


@router.patch("/me/loans/{loan_id}/fields", response_model=dict)
async def update_loan_fields(
    loan_id: uuid.UUID,
    payload: LoanFieldsUpdate,
    user: CurrentBorrowerDep,
    db: DbSessionDep,
) -> dict:
    """Edit borrower-supplied loan fields.

    Whitelisted to fields the borrower legitimately knows about. Every
    mutation writes an audit event ``borrower_field_updated``.

    Materials-hash discipline: if these change after a decision has
    been made, the materials hash drifts and the stage-transition
    guard refuses forward progress until the decision agent is re-run.
    """
    loan = await _assert_loan_owned_by(db, loan_id, user)

    if loan.stage in (LoanStage.CLOSING, LoanStage.SERVICING, LoanStage.WITHDRAWN):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Loan is in {loan.stage.value} stage — fields are locked.",
        )

    diff: dict[str, dict[str, Any]] = {}
    meta = dict(loan.meta or {})
    payload_dict = payload.model_dump(exclude_unset=True)
    for k, v in payload_dict.items():
        if k not in _BORROWER_EDITABLE_META:
            continue
        meta_key, coerce = _BORROWER_EDITABLE_META[k]
        new_val = coerce(v) if v is not None else None
        old_val = meta.get(meta_key)
        if new_val != old_val:
            diff[k] = {"from": old_val, "to": new_val}
            meta[meta_key] = new_val

    if not diff:
        return {"changed": [], "message": "No changes."}

    loan.meta = meta
    await record(
        db,
        loan_id=loan.id,
        actor=Actor.borrower(user.email),
        action="borrower_field_updated",
        payload={"changes": diff, "stage_at_edit": loan.stage.value},
    )
    await db.commit()
    return {"changed": sorted(diff.keys()), "diff": diff}


# --- Document download -----------------------------------------------------


@router.get("/me/loans/{loan_id}/documents/{document_id}/download-url")
async def borrower_document_download_url(
    loan_id: uuid.UUID,
    document_id: uuid.UUID,
    user: CurrentBorrowerDep,
    db: DbSessionDep,
) -> dict[str, object]:
    """Mint a short-lived presigned download URL for a borrower-owned
    document. Mirrors the staff endpoint in ``documents.py`` but with
    cookie-based auth and an ownership check.
    """
    loan = await _assert_loan_owned_by(db, loan_id, user)
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.loan_id == loan.id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    try:
        url = await mint_download_url(
            db,
            loan_id=loan.id,
            document_id=doc.id,
            storage_uri=doc.storage_uri,
            actor=Actor.borrower(user.email),
            purpose="preview",
            expires_in=300,
        )
    except StorageAuthzError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    await db.commit()
    return {
        "url": url,
        "filename": doc.filename,
        "content_type": doc.content_type,
        "expires_in_seconds": 300,
    }


# --- DSAR export -----------------------------------------------------------


@router.get("/me/data/export")
async def export_my_data(user: CurrentBorrowerDep, db: DbSessionDep) -> dict:
    """DSAR-shaped export of everything Mkopo holds about the borrower.

    Returns a JSON blob with their user record, every loan they're
    associated with, the loan's meta + stage + amount, all audit
    events tied to those loans, and a manifest of attached documents
    (filenames, content_hashes — NOT bytes; bytes are downloadable
    via the per-document presigned-URL flow separately).
    """
    loans = (
        (
            await db.execute(
                select(Loan)
                .join(LoanParty, LoanParty.loan_id == Loan.id)
                .join(Party, Party.id == LoanParty.party_id)
                .where(
                    LoanParty.role == PartyRole.BORROWER,
                    Party.email == user.email,
                )
                .order_by(Loan.created_at)
            )
        )
        .scalars()
        .all()
    )

    loan_blocks: list[dict[str, Any]] = []
    for loan in loans:
        docs = (
            (
                await db.execute(
                    select(Document)
                    .where(Document.loan_id == loan.id)
                    .order_by(Document.created_at)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.loan_id == loan.id)
                    .order_by(AuditEvent.created_at)
                )
            )
            .scalars()
            .all()
        )
        loan_blocks.append(
            {
                "loan_id": str(loan.id),
                "reference": loan.reference,
                "stage": loan.stage.value,
                "loan_type": loan.loan_type.value,
                "loan_class": loan.loan_class.value
                if hasattr(loan.loan_class, "value")
                else str(loan.loan_class),
                "amount": str(loan.amount),
                "submitted_at": loan.created_at.isoformat(),
                "deleted_at": loan.deleted_at.isoformat() if loan.deleted_at else None,
                "retention_until": loan.retention_until.isoformat()
                if loan.retention_until
                else None,
                "meta": loan.meta or {},
                "documents": [
                    {
                        "filename": d.filename,
                        "doc_type": d.doc_type if isinstance(d.doc_type, str) else d.doc_type.value,
                        "size_bytes": d.size_bytes,
                        "content_hash": d.content_hash,
                        "uploaded_at": d.created_at.isoformat(),
                    }
                    for d in docs
                ],
                "audit_events": [
                    {
                        "action": e.action,
                        "actor_type": e.actor_type.value
                        if hasattr(e.actor_type, "value")
                        else str(e.actor_type),
                        "actor_id": e.actor_id,
                        "payload": e.payload,
                        "at": e.created_at.isoformat(),
                    }
                    for e in events
                ],
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "email_verified_at": user.email_verified_at.isoformat()
            if user.email_verified_at
            else None,
            "created_at": user.created_at.isoformat(),
        },
        "loans": loan_blocks,
        "_notes": [
            "This export covers everything Mkopo holds about you as of the timestamp above.",
            "Document contents are referenced by sha256 hash but not "
            "included inline. Use the loan's status page to download "
            "the bytes directly.",
        ],
    }


# --- Erasure ---------------------------------------------------------------


class ErasureRequest(BaseModel):
    """Borrower's confirmation that they understand the retention
    window. Erasure is the single most consequential borrower-initiated
    action in the system; a stolen session cookie shouldn't be enough.
    """

    reason: str = Field(min_length=1, max_length=500)
    confirm: bool
    challenge_token: str | None = Field(default=None, max_length=128)


@router.post("/me/erasure")
async def request_erasure(
    payload: ErasureRequest, user: CurrentBorrowerDep, db: DbSessionDep
) -> dict:
    """Soft-delete the borrower's account + all their loans.

    Behaviour:
      - ``users.deleted_at = now()``.
      - For every loan: ``loan.deleted_at = now()`` + ``retention_until``
        per HMDA (5y) for closed/serviced, Reg B/ECOA (25mo) otherwise.
      - Writes ``borrower_erasure_requested`` audit events.
      - Soft-deletes mean the rows still exist until the retention sweep
        picks them up. Operational views filter them out immediately.

    Frontend should log the borrower out after success — the session
    cookie is still valid but the next ``/me`` call sees ``deleted_at``
    and refuses.
    """
    if not payload.confirm:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Confirmation is required to request erasure.",
        )
    await _require_challenge(user=user, token=payload.challenge_token)

    now = datetime.now(UTC)
    user.deleted_at = now

    loans = (
        (
            await db.execute(
                select(Loan)
                .join(LoanParty, LoanParty.loan_id == Loan.id)
                .join(Party, Party.id == LoanParty.party_id)
                .where(
                    LoanParty.role == PartyRole.BORROWER,
                    Party.email == user.email,
                    Loan.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    hmda_years = 5
    regb_months = 25
    for loan in loans:
        loan.deleted_at = now
        if loan.stage in (LoanStage.APPROVED, LoanStage.CLOSING, LoanStage.SERVICING):
            loan.retention_until = now + timedelta(days=365 * hmda_years)
        else:
            loan.retention_until = now + timedelta(days=30 * regb_months)
        await record(
            db,
            loan_id=loan.id,
            actor=Actor.borrower(user.email),
            action="borrower_erasure_requested",
            payload={
                "reason": payload.reason,
                "stage_at_request": loan.stage.value,
                "retention_until": loan.retention_until.isoformat(),
            },
        )

    await db.commit()

    latest_retention = max(
        (loan.retention_until for loan in loans if loan.retention_until),
        default=None,
    )
    return {
        "ok": True,
        "loans_affected": len(loans),
        "retention_until_max": latest_retention.isoformat() if latest_retention else None,
        "message": (
            "Your account and applications have been marked for erasure. "
            "We're required to keep the records on file until the regulatory "
            "retention windows expire, after which they'll be permanently "
            "deleted. You're now signed out."
        ),
    }


# Re-exports kept so existing imports of these names from
# ``borrower_auth`` continue to work during the migration. New code
# should import from this module.
__all__ = [
    "MyLoanRow",
    "_assert_loan_owned_by",
    "_require_challenge",
    "router",
]
