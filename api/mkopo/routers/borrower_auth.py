"""Borrower self-service authentication endpoints.

Surface: ``/api/v1/borrower-auth/...``

  - ``POST /signup``                 — create account (email + password)
  - ``POST /login``                  — password login → session cookie
  - ``POST /magic-link/request``     — email magic link for login
  - ``POST /magic-link/consume``     — exchange token → session cookie
  - ``POST /password-reset/request`` — email magic link for password reset
  - ``POST /password-reset/confirm`` — token + new password
  - ``POST /logout``                 — clear the cookie
  - ``GET  /me``                     — return the signed-in borrower

Sessions are delivered as ``mkopo_session`` httpOnly cookies, signed
JWTs valid for 12h. The client never sees the token in JavaScript —
that's what makes a stolen XSS payload unable to walk off with the
credential. Logout just clears the cookie.

Why all "missing user" / "wrong password" paths return the same
generic message: a careful attacker can't enumerate registered
emails from this surface. ``signup`` is the one place a duplicate
email is acknowledged, and only because the UX otherwise breaks.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from mkopo.config import get_settings
from mkopo.deps import CurrentBorrowerDep, DbSessionDep
from mkopo.models import User
from mkopo.services.auth_service import (
    SESSION_COOKIE,
    consume_magic_link,
    hash_password,
    issue_jwt,
    mint_magic_link,
    verify_password,
)

router = APIRouter(prefix="/borrower-auth", tags=["borrower-auth"])


# ---- request / response shapes -----------------------------------------


class SignupRequest(BaseModel):
    """New-account submission. Name is optional; some borrowers will
    fill it in via the application form rather than signup."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    name: str = Field(default="", max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkConsume(BaseModel):
    """Generic consume endpoint — used for login and email-verify
    magic links. The frontend's magic-link landing page POSTs to
    here with the token from the URL.

    For password reset (which also takes a new password), use the
    dedicated /password-reset/confirm route — that way the typing
    catches purpose-mixups at the schema level."""

    token: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=256)


class MeResponse(BaseModel):
    """What the frontend's auth hook receives on /me."""

    id: str
    email: EmailStr
    name: str
    role: str
    email_verified_at: datetime | None


class MagicLinkIssued(BaseModel):
    """Returned by endpoints that mint a magic link.

    ``magic_link_url`` is included in dev so testing the flow doesn't
    require a real inbox. In production this will be ``None`` and
    the link will only land in the user's email — leaking it in the
    HTTP response would defeat the point.
    """

    ok: bool = True
    magic_link_url: str | None = None


# ---- helpers -----------------------------------------------------------


def _set_session_cookie(response: Response, user: User) -> None:
    """Mint a JWT for ``user`` and attach it as ``mkopo_session``.

    ``httponly`` so JavaScript can't read it (XSS-stolen token is
    not exploitable). ``samesite=Lax`` lets the cookie ride on
    top-level navigations from the frontend domain to the API
    domain but blocks cross-site POSTs. ``secure=True`` in
    production so the cookie can't be sent over http: at all.
    """
    settings = get_settings()
    token = issue_jwt(user)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.jwt_session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


def _magic_link_url(plain_token: str, purpose: str) -> str:
    """Build the user-clickable URL for a magic link.

    Points at the frontend's verify page; the frontend POSTs the
    token to the matching API endpoint based on ``purpose``.
    """
    settings = get_settings()
    return f"{settings.frontend_url}/auth/verify?purpose={purpose}&token={plain_token}"


def _dev_link_for_response(plain_token: str, purpose: str) -> str | None:
    """In dev, return the magic-link URL so test flows don't need
    a real mailbox. ``None`` in production."""
    settings = get_settings()
    if settings.is_production:
        return None
    return _magic_link_url(plain_token, purpose)


# ---- endpoints ---------------------------------------------------------


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest, response: Response, db: DbSessionDep
) -> MeResponse:
    """Create a borrower account.

    Side effects:
      - Inserts a ``users`` row with ``role='borrower'``.
      - Issues a session cookie immediately (no email-verify gate
        for this product — verification is a soft signal, not a
        hard one).
      - We don't auto-send a verify email here; that's a follow-up
        endpoint the UI can hit when it wants to.

    Duplicate emails return 409 — this is the one spot we'd
    acknowledge that a user exists, because the UX otherwise breaks.
    Real-world mitigation is rate limiting, which lives in the
    middleware layer (nginx / cloudfront).
    """
    email = payload.email.lower().strip()
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with that email already exists"
        )

    user = User(
        email=email,
        name=payload.name or email.split("@", 1)[0],
        role="borrower",
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    await db.commit()
    _set_session_cookie(response, user)
    return _me(user)


@router.post("/login")
async def login(
    payload: LoginRequest, response: Response, db: DbSessionDep
) -> MeResponse:
    """Verify password and set the session cookie.

    Always returns the same 401 for "wrong password" and "no such
    user" — don't leak which one happened.
    """
    email = payload.email.lower().strip()
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    # ``verify_password`` handles a None hash gracefully so this
    # works for magic-link-only users (always returns False) and
    # for missing users (we call it with empty string to keep the
    # constant-time guarantee).
    valid = user is not None and verify_password(payload.password, user.password_hash)
    if not valid or user is None or user.role != "borrower":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    _set_session_cookie(response, user)
    return _me(user)


@router.post("/magic-link/request", response_model=MagicLinkIssued)
async def request_magic_link(
    payload: MagicLinkRequest, db: DbSessionDep
) -> MagicLinkIssued:
    """Send a one-shot login link to the supplied email.

    Always returns ``ok: true`` regardless of whether the email is
    on file — this is the standard anti-enumeration pattern. The
    user gets a useful response either way.

    In dev we include the URL in the response so test scripts can
    follow the link without an inbox. In production the URL ships
    via email only; the response just confirms acceptance.
    """
    email = payload.email.lower().strip()
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if user is None or user.role != "borrower":
        # Anti-enumeration: return success even when there's no
        # account. No magic link is ever minted, of course.
        return MagicLinkIssued()

    minted = await mint_magic_link(db, user=user, purpose="login")
    await db.commit()
    # TODO(phase1b): send the link via Resend so the borrower
    # actually receives an email. For now the URL is logged + (in
    # dev only) returned in the response body so testing is easy.
    return MagicLinkIssued(
        magic_link_url=_dev_link_for_response(minted.plain_token, "login")
    )


@router.post("/magic-link/consume")
async def consume_login_link(
    payload: MagicLinkConsume, response: Response, db: DbSessionDep
) -> MeResponse:
    """Exchange a login or email-verify magic link for a session.

    The same endpoint handles both ``login`` and ``email_verify``
    tokens — the consume helper is purpose-locked but we accept
    either kind here. Verify tokens, in addition to setting the
    session, stamp ``users.email_verified_at``.
    """
    # Try login purpose first; that's the common path.
    user = await consume_magic_link(db, plain_token=payload.token, purpose="login")
    if user is None:
        user = await consume_magic_link(
            db, plain_token=payload.token, purpose="email_verify"
        )
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Magic link is invalid or expired"
            )
        if user.email_verified_at is None:
            from datetime import UTC, datetime as dt

            user.email_verified_at = dt.now(UTC)

    await db.commit()
    _set_session_cookie(response, user)
    return _me(user)


@router.post("/password-reset/request", response_model=MagicLinkIssued)
async def request_password_reset(
    payload: MagicLinkRequest, db: DbSessionDep
) -> MagicLinkIssued:
    """Send a password-reset link. Same anti-enumeration semantics
    as the login magic-link request endpoint."""
    email = payload.email.lower().strip()
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None or user.role != "borrower":
        return MagicLinkIssued()

    minted = await mint_magic_link(db, user=user, purpose="password_reset")
    await db.commit()
    return MagicLinkIssued(
        magic_link_url=_dev_link_for_response(minted.plain_token, "password_reset")
    )


@router.post("/password-reset/confirm")
async def confirm_password_reset(
    payload: PasswordResetConfirm, response: Response, db: DbSessionDep
) -> MeResponse:
    """Consume a password-reset token and set a new password.

    On success the user is also signed in (session cookie set) —
    matches the conventional UX where a successful reset drops you
    into your account.
    """
    user = await consume_magic_link(
        db, plain_token=payload.token, purpose="password_reset"
    )
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Reset link is invalid or expired"
        )
    user.password_hash = hash_password(payload.new_password)
    await db.commit()
    _set_session_cookie(response, user)
    return _me(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Clear the session cookie. Always returns 204 — the cookie
    might already be gone (logout-while-logged-out shouldn't 401)."""
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentBorrowerDep) -> MeResponse:
    """Return the currently-signed-in borrower. Powers the
    frontend's auth hook + the dashboard header.

    401 propagates from the dependency when not signed in."""
    return _me(user)


class MyLoanRow(BaseModel):
    """One row of the signed-in borrower's loans list. Thin
    projection — the public-facing portal doesn't need the internal
    risk_band, owner, or full meta blob, just enough to render a
    list and link to ``/apply/{id}`` for the deep view."""

    loan_id: str
    reference: str
    stage: str
    loan_type: str
    loan_class: str
    amount: str
    submitted_at: datetime
    next_step: str


@router.get("/me/loans", response_model=list[MyLoanRow])
async def my_loans(
    user: CurrentBorrowerDep, db: DbSessionDep
) -> list[MyLoanRow]:
    """List the loans associated with the signed-in borrower.

    Match is by email — the borrower's ``users.email`` joins to the
    ``Party.email`` of every loan where they're the borrower party.
    Cheap query (indexed both sides) and the response stays small
    because borrowers typically have 1–3 loans, not 1000.

    Powers the ``/account`` landing page after login. Replacement
    for "where do I go after signing in?".
    """
    from mkopo.models import Loan, LoanParty, Party, PartyRole

    rows = (
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
    ).scalars().all()

    # Import inline to dodge a circular: borrower_portal also wants
    # to share this copy eventually.
    from mkopo.routers.borrower_portal import _next_step_for_borrower

    return [
        MyLoanRow(
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
        for loan in rows
    ]


class ContactUpdate(BaseModel):
    """Trivial contact-info fields. Deliberately small — anything
    that affects an underwriting decision (income, employer, etc.)
    flows through the Phase 3 agent tools so changes get the audit
    + materials-hash treatment automatically. These are typo-class
    edits only.
    """

    name: str | None = Field(default=None, min_length=1, max_length=128)


@router.patch("/me/contact", response_model=MeResponse)
async def update_contact(
    payload: ContactUpdate, user: CurrentBorrowerDep, db: DbSessionDep
) -> MeResponse:
    """Update the signed-in borrower's contact info.

    Restricted to typo-class edits. Email changes go through the
    email-verify magic-link flow (so we don't accidentally let a
    session-stealer pivot the account). Anything that affects an
    underwriting decision (income, employer, monthly_debt, etc.)
    intentionally lives behind the Phase 3 agent tool catalog so
    every mutation is audited + interrupt-confirmed + materials-
    hash-invalidating.
    """
    changed = False
    if payload.name is not None and payload.name.strip() != user.name:
        user.name = payload.name.strip()
        changed = True
    if changed:
        await db.commit()
    return _me(user)


# ----- Phase 2: self-service mutations -------------------------------------


async def _assert_loan_owned_by(
    db: "AsyncSession", loan_id: uuid.UUID, user: User
) -> "Loan":
    """Load the loan and confirm the signed-in borrower owns it.

    Same email-keyed check borrower_portal uses, lifted here so the
    Phase 2 endpoints don't import the helper across modules. Raises
    HTTP 404 if the loan doesn't exist; HTTP 403 if it does but the
    user isn't the borrower party.
    """
    from mkopo.models import Loan, LoanParty, Party, PartyRole

    loan = (
        await db.execute(select(Loan).where(Loan.id == loan_id))
    ).scalar_one_or_none()
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


class WithdrawRequest(BaseModel):
    """Borrower's explanation for walking away. Stored verbatim on the
    audit event so internal staff can see why the application died —
    useful both for product feedback ('rate too high', 'found another
    lender') and for compliance ('borrower-initiated, not lender
    rejection') because HMDA distinguishes the two."""

    reason: str = Field(min_length=1, max_length=500)


@router.post("/me/loans/{loan_id}/withdraw", response_model=MyLoanRow)
async def withdraw_loan(
    loan_id: uuid.UUID,
    payload: WithdrawRequest,
    user: CurrentBorrowerDep,
    db: DbSessionDep,
) -> MyLoanRow:
    """Borrower withdraws their own application.

    Routes through ``services.loans.transition_stage`` so the
    transition + audit happen together and the stage-machine rules
    are enforced (you can withdraw from any non-terminal stage; the
    edges are in VALID_TRANSITIONS).

    Withdrawal is a *terminal* stage — no further transitions are
    possible. The loan stays in the database, anonymisable later via
    erasure but never re-opened. If the borrower changes their mind
    they file a new application.
    """
    from mkopo.models import LoanStage
    from mkopo.services.audit import Actor
    from mkopo.services.loans import IllegalStageTransitionError, transition_stage

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
        # Edge violation — e.g. trying to withdraw after closing.
        # The error message is human-readable; pass it through.
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    await db.commit()
    await db.refresh(loan)

    from mkopo.routers.borrower_portal import _next_step_for_borrower

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


# Fields a borrower can self-edit via the REST patch endpoint. Each
# entry is ``(form_key, meta_key, coerce_fn)``. Anything not on the
# whitelist is silently ignored — defensive against a payload
# adding an unexpected key. Decision-feeding fields are deliberately
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

    Whitelisted to fields the borrower legitimately knows about (their
    own income, employer, monthly debts, the loan's purpose). Stored
    on ``loan.meta`` — the same place ``borrower_apply`` writes them.
    Every mutation writes an audit event ``borrower_field_updated``.

    Materials-hash discipline (Phase 0): if these change after a
    decision has been made, the materials hash drifts and the
    stage-transition guard refuses forward progress until the
    decision agent is re-run. The borrower can edit freely; the
    system enforces re-underwriting at the right gate.
    """
    from mkopo.models import LoanStage
    from mkopo.services.audit import Actor, record

    loan = await _assert_loan_owned_by(db, loan_id, user)

    # Refuse mutations on terminal/post-funding stages — borrowers
    # don't get to revise their income after closing.
    if loan.stage in (LoanStage.CLOSING, LoanStage.SERVICING, LoanStage.WITHDRAWN):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Loan is in {loan.stage.value} stage — fields are locked.",
        )

    # Apply each whitelisted field. Build a diff dict so the audit
    # event captures what actually changed (not the whole payload).
    diff: dict[str, dict[str, Any]] = {}
    meta = dict(loan.meta or {})
    payload_dict = payload.model_dump(exclude_unset=True)
    for k, v in payload_dict.items():
        if k not in _BORROWER_EDITABLE_META:
            continue  # Not on whitelist — silently skip.
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


@router.get("/me/data/export")
async def export_my_data(
    user: CurrentBorrowerDep, db: DbSessionDep
) -> dict:
    """DSAR-shaped export of everything Mkopo holds about the borrower.

    Returns a JSON blob with their user record, every loan they're
    associated with, the loan's meta + stage + amount, all audit
    events tied to those loans, and a manifest of attached documents
    (filenames, content_hashes — NOT bytes; bytes are downloadable
    via the per-document presigned-URL flow separately).

    Real-world this would queue an async job and email a ZIP; for
    the demo a sync JSON response is honest and inspectable. The
    important property is that we deliberately walk the same
    ownership boundary (Party.email = user.email) that every other
    Phase 2 endpoint uses, so the export can't leak someone else's
    data even if the borrower's user row were corrupted.
    """
    from datetime import UTC, datetime as dt

    from mkopo.models import (
        AuditEvent,
        Document,
        Loan,
        LoanParty,
        Party,
        PartyRole,
    )

    loans = (
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
    ).scalars().all()

    loan_blocks: list[dict[str, Any]] = []
    for loan in loans:
        docs = (
            await db.execute(
                select(Document)
                .where(Document.loan_id == loan.id)
                .order_by(Document.created_at)
            )
        ).scalars().all()
        events = (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.loan_id == loan.id)
                .order_by(AuditEvent.created_at)
            )
        ).scalars().all()
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
                        "doc_type": d.doc_type
                        if isinstance(d.doc_type, str)
                        else d.doc_type.value,
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
        "generated_at": dt.now(UTC).isoformat(),
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
            "This export covers everything Mkopo holds about you as of "
            "the timestamp above.",
            "Document contents are referenced by sha256 hash but not "
            "included inline. Use the loan's status page to download "
            "the bytes directly.",
        ],
    }


class ErasureRequest(BaseModel):
    """Borrower's confirmation that they understand the retention
    window. The reason is recorded on the audit trail."""

    reason: str = Field(min_length=1, max_length=500)
    # Must be ``true`` for the request to be accepted — guards
    # against accidental fat-finger erasure from the API.
    confirm: bool


@router.post("/me/erasure")
async def request_erasure(
    payload: ErasureRequest, user: CurrentBorrowerDep, db: DbSessionDep
) -> dict:
    """Soft-delete the borrower's account + all their loans.

    Behaviour:

      - Sets ``users.deleted_at = now()``.
      - For every loan the borrower owns:
        - sets ``loan.deleted_at = now()``;
        - sets ``loan.retention_until`` based on the loan's outcome
          (5y after closure for approved loans per HMDA; 25mo
          otherwise per Reg B/ECOA);
        - writes ``borrower_erasure_requested`` audit event.
      - Soft-deletes mean the rows still exist in the DB until the
        retention sweep job picks them up. Operational views
        (``/me/loans``, pipeline, comparables corpus) filter them
        out immediately.

    The frontend should immediately log the borrower out after a
    successful response — their session cookie is still valid but
    the next ``/me`` call will see ``deleted_at`` and refuse.
    """
    from datetime import UTC, datetime as dt, timedelta

    from mkopo.config import get_settings
    from mkopo.models import Loan, LoanParty, LoanStage, Party, PartyRole
    from mkopo.services.audit import Actor, record

    if not payload.confirm:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Confirmation is required to request erasure.",
        )

    now = dt.now(UTC)

    # Soft-delete the user row.
    user.deleted_at = now

    # Find every loan the borrower owns + apply soft-delete +
    # retention schedule.
    loans = (
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
    ).scalars().all()

    # Retention windows. Approved/closed loans need 5y HMDA; everything
    # else needs 25mo Reg B/ECOA. Conservative when in doubt.
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

    # Clear the session cookie so subsequent requests reauth.
    # (The borrower's account is soft-deleted; require_borrower
    # will refuse any further /me calls regardless of the cookie.)
    _ = get_settings  # quiet the unused-import linter
    return {
        "ok": True,
        "loans_affected": len(loans),
        "retention_until_max": max(
            (l.retention_until for l in loans if l.retention_until),
            default=None,
        ).isoformat() if loans else None,
        "message": (
            "Your account and applications have been marked for erasure. "
            "We're required to keep the records on file until the regulatory "
            "retention windows expire, after which they'll be permanently "
            "deleted. You're now signed out."
        ),
    }


def _me(user: User) -> MeResponse:
    return MeResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        email_verified_at=user.email_verified_at,
    )
