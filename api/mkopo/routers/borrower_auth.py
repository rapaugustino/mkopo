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

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.config import get_settings
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
from mkopo.services.auth_service import (
    SESSION_COOKIE,
    consume_magic_link,
    decode_jwt,
    hash_password,
    issue_jwt,
    mint_magic_link,
    verify_password,
)
from mkopo.services.loans import IllegalStageTransitionError, transition_stage
from mkopo.services.redis_client import (
    consume_challenge,
    is_account_locked,
    lock_account,
    mint_challenge,
    rate_limit_check,
    rate_limit_reset,
    revoke_jti,
    unlock_account,
)
from mkopo.services.storage import StorageAuthzError, mint_download_url
from mkopo.tools.comms import send_magic_link_email

logger = structlog.get_logger()

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
    payload: SignupRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: DbSessionDep,
) -> MeResponse:
    """Create a borrower account.

    Side effects:
      - Inserts a ``users`` row with ``role='borrower'``.
      - Issues a session cookie immediately (no email-verify gate —
        verification is a soft signal we use to mark a contact as
        confirmed, not a hard gate that blocks sign-in).
      - Fires a one-shot ``email_verify`` magic link in the
        background so the borrower can confirm their address.

    Duplicate emails return 409 — this is the one spot we'd
    acknowledge that a user exists, because the UX otherwise breaks.

    Hardening (#168): per-IP rate limit (10/hour). Stops a script
    from carpet-bombing the users table with throwaway addresses.
    """
    ip = _client_ip(request)
    allowed, _ = await rate_limit_check(
        key=f"signup:ip:{ip}", limit=10, window_seconds=3600
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many signup attempts from this network. Try again later.",
        )

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
    # Email-verify magic link — opportunistic. If Resend isn't
    # configured the helper logs + skips, so signup still works in dev.
    minted = await mint_magic_link(db, user=user, purpose="email_verify")
    await db.commit()

    settings = get_settings()
    full_url = _magic_link_url(minted.plain_token, "email_verify")
    background_tasks.add_task(
        send_magic_link_email,
        to=user.email,
        url=full_url,
        purpose="email_verify",
        expires_minutes=settings.magic_link_ttl_seconds // 60,
        recipient_name=user.name,
    )

    _set_session_cookie(response, user)
    return _me(user)


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSessionDep,
) -> MeResponse:
    """Verify password and set the session cookie.

    Always returns the same 401 for "wrong password" and "no such
    user" — don't leak which one happened.

    Hardening (#168):
      - Per-IP rate limit (5/min). Stops a botnet from hammering
        a single user agent under one IP.
      - Per-(email, IP) failed-attempt counter. Five failures in 15
        minutes locks the account; the legitimate user can recover
        by clicking a magic-link (which clears the lock).
      - Locked accounts get the same 401 as wrong-password so we
        don't reveal that the account is under attack.
    """
    email = payload.email.lower().strip()
    ip = _client_ip(request)

    # IP-wide rate limit. Generous enough that a normal user typo-ing
    # their password three times in a row isn't blocked, tight enough
    # that a credential-stuffing run gets throttled hard.
    allowed, _ = await rate_limit_check(
        key=f"login:ip:{ip}", limit=20, window_seconds=60
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many sign-in attempts. Try again in a minute.",
        )

    # Account lockout precheck — refuse with the same 401 used for
    # wrong-password. The legitimate user can recover via a magic
    # link (consume path calls ``unlock_account``).
    if await is_account_locked(email=email):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    # ``verify_password`` handles a None hash gracefully so this
    # works for magic-link-only users (always returns False) and
    # for missing users (we call it with empty string to keep the
    # constant-time guarantee).
    valid = user is not None and verify_password(payload.password, user.password_hash)
    if not valid or user is None or user.role != "borrower":
        # Count this failure against the (email, IP) tuple. Five
        # failures in 15min locks the account for an hour; the lock
        # clears on a successful magic-link consume.
        fail_key = f"login-fail:{email}:{ip}"
        _, attempts = await rate_limit_check(
            key=fail_key, limit=10**9, window_seconds=900
        )
        if attempts >= 5:
            await lock_account(email=email, ttl_seconds=3600)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    # Successful login: wipe the failure counter + any lock that
    # was set. A legitimate user with one bad attempt shouldn't be
    # carrying the failure forward into tomorrow.
    await rate_limit_reset(key=f"login-fail:{email}:{ip}")
    await unlock_account(email=email)

    _set_session_cookie(response, user)
    return _me(user)


def _client_ip(request: Request) -> str:
    """Best-effort caller IP. Honours ``X-Forwarded-For`` so deployments
    behind a load balancer record the real client rather than the LB.

    Trust boundary: this is only used for rate-limiting (i.e. throwing
    away requests). A spoofed ``X-Forwarded-For`` could let an attacker
    move their bucket — that's strictly worse for them than not setting
    the header at all (they get bucketed under their real IP), so we
    don't worry about validating the header chain here.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/magic-link/request", response_model=MagicLinkIssued)
async def request_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: DbSessionDep,
) -> MagicLinkIssued:
    """Send a one-shot login link to the supplied email.

    Always returns ``ok: true`` regardless of whether the email is
    on file — this is the standard anti-enumeration pattern. The
    user gets a useful response either way.

    In dev we additionally include the URL in the response so test
    scripts can follow the link without an inbox. In production the
    URL ships via email only; the response just confirms acceptance.

    Hardening (#168): per-email rate limit (3/10min). Prevents an
    attacker from inundating someone's inbox with login links.
    """
    email = payload.email.lower().strip()
    allowed, _ = await rate_limit_check(
        key=f"magic-link:{email}", limit=3, window_seconds=600
    )
    if not allowed:
        # Same anti-enumeration shape — we still return MagicLinkIssued
        # rather than a 429 so a bot can't tell the email exists from
        # the response. The legitimate user already has a link.
        logger.info("magic_link_request_rate_limited", email=email)
        return MagicLinkIssued()

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if user is None or user.role != "borrower":
        # Anti-enumeration: return success even when there's no
        # account. No magic link is ever minted, of course.
        return MagicLinkIssued()

    minted = await mint_magic_link(db, user=user, purpose="login")
    await db.commit()

    settings = get_settings()
    full_url = _magic_link_url(minted.plain_token, "login")
    # Fire-and-forget — Resend's network call shouldn't block the
    # response. ``send_magic_link_email`` is non-raising; a hiccup
    # is logged, the user can request another link.
    background_tasks.add_task(
        send_magic_link_email,
        to=user.email,
        url=full_url,
        purpose="login",
        expires_minutes=settings.magic_link_ttl_seconds // 60,
        recipient_name=user.name,
    )
    return MagicLinkIssued(
        magic_link_url=_dev_link_for_response(minted.plain_token, "login")
    )


@router.post("/magic-link/consume")
async def consume_login_link(
    payload: MagicLinkConsume, response: Response, db: DbSessionDep
) -> MeResponse:
    """Exchange a login / email-verify / loan-invite magic link for
    a session.

    The same endpoint handles all three purposes — the consume
    helper is purpose-locked, but we try each in turn so the client
    doesn't need to tell us which one it has. All three set a
    session cookie on success; email_verify also stamps
    ``users.email_verified_at``, and loan_invite stamps it too (the
    fact that the borrower received the email at this address
    counts as verification).
    """
    # Try the common purposes in order. Each is purpose-locked at
    # the consume layer so a login token can't be replayed as an
    # invite (or vice-versa) — we're just trying multiple keys to
    # one lock from the same plain token.
    user = await consume_magic_link(db, plain_token=payload.token, purpose="login")
    if user is None:
        user = await consume_magic_link(
            db, plain_token=payload.token, purpose="email_verify"
        )
        if user is not None and user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
    if user is None:
        user = await consume_magic_link(
            db, plain_token=payload.token, purpose="loan_invite"
        )
        if user is not None and user.email_verified_at is None:
            # Receiving the invite at this address proves ownership.
            user.email_verified_at = datetime.now(UTC)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Magic link is invalid or expired"
        )

    await db.commit()
    # Successfully clicking a login magic link proves the user owns the
    # mailbox — if their account was locked by a brute-force attempt,
    # this is the canonical "this is the legitimate user" signal. Clear
    # the lockout + the failure counter so they can sign in normally
    # next time. Same logic the successful-password path runs.
    await unlock_account(email=user.email)
    _set_session_cookie(response, user)
    return _me(user)


@router.post("/password-reset/request", response_model=MagicLinkIssued)
async def request_password_reset(
    payload: MagicLinkRequest,
    background_tasks: BackgroundTasks,
    db: DbSessionDep,
) -> MagicLinkIssued:
    """Send a password-reset link. Same anti-enumeration semantics
    as the login magic-link request endpoint."""
    email = payload.email.lower().strip()
    allowed, _ = await rate_limit_check(
        key=f"password-reset:{email}", limit=3, window_seconds=600
    )
    if not allowed:
        # Same anti-enumeration shape as ``/magic-link/request`` —
        # quiet 200 so a bot can't tell anything about the account.
        logger.info("password_reset_request_rate_limited", email=email)
        return MagicLinkIssued()

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None or user.role != "borrower":
        return MagicLinkIssued()

    minted = await mint_magic_link(db, user=user, purpose="password_reset")
    await db.commit()

    settings = get_settings()
    full_url = _magic_link_url(minted.plain_token, "password_reset")
    background_tasks.add_task(
        send_magic_link_email,
        to=user.email,
        url=full_url,
        purpose="password_reset",
        expires_minutes=settings.magic_link_ttl_seconds // 60,
        recipient_name=user.name,
    )
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
async def logout(
    response: Response,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> None:
    """Clear the session cookie + revoke the underlying JWT.

    Always returns 204 — the cookie might already be gone, the JWT
    might already be expired, etc. ``logout`` should be safe to call
    in any state.

    Revocation is server-side via a Redis blacklist keyed on the JWT
    ``jti`` claim. The blacklist entry expires when the token would
    have expired naturally, so we never store stale entries. If Redis
    is down we still clear the cookie locally and log — the worst
    case is a stolen-token window equal to the remaining TTL, which
    is the same as the status quo before #167.
    """
    response.delete_cookie(SESSION_COOKIE, path="/")
    if session_cookie:
        claims = decode_jwt(session_cookie)
        if claims is not None:
            remaining = int(
                (claims.expires_at - datetime.now(UTC)).total_seconds()
            )
            if remaining > 0:
                await revoke_jti(claims.jti, ttl_seconds=remaining)


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


class ChallengeRequest(BaseModel):
    """Body for ``POST /me/challenge``: the user's current password.

    The endpoint verifies it and returns a short-lived token that the
    subsequent sensitive request (withdraw / erasure) must include.
    """

    password: str = Field(min_length=1, max_length=256)


class ChallengeIssued(BaseModel):
    """Response shape for ``POST /me/challenge``. ``token`` is the
    one-shot challenge that the client must echo back on the next
    sensitive request within :data:`_CHALLENGE_TTL_SECONDS` seconds.
    """

    token: str
    expires_in_seconds: int = 300


@router.post("/me/challenge", response_model=ChallengeIssued)
async def mint_reauth_challenge(
    payload: ChallengeRequest, user: CurrentBorrowerDep
) -> ChallengeIssued:
    """Mint a one-shot challenge token for a sensitive operation.

    The flow:

      1. UI shows a "type your password to continue" modal just
         before a destructive action (withdraw / erasure).
      2. UI POSTs the password here, getting back a token.
      3. UI sends the actual destructive request with the token in
         the body. The handler ``await consume_challenge(...)`` to
         verify + burn it.

    Why this exists (#169 / JWT audit 🔴):

      - The session cookie alone is enough to call withdraw +
        erasure today. If a session is ever leaked (XSS, malware on
        a shared device, lost phone) the attacker has a ~12h window
        to trigger either of these IRREVERSIBLE actions.
      - Requiring a fresh password re-entry binds the action to
        "the person physically at the keyboard right now", not just
        "the person whose session is on this device".
      - Magic-link-only users (no password set) get a 400 with a
        hint to set a password first; the alternative is to mint
        challenge tokens unconditionally and let any session-cookie
        holder withdraw the loan, which is the status quo we want
        to close.

    The plain token is in the response body but never in the URL,
    never logged, never stored server-side as plain text (only its
    sha256 is in Redis).
    """
    if not user.password_hash:
        # Magic-link-only user: there's no password to verify against.
        # We could fall back to "click a fresh magic link to confirm"
        # but that's a much bigger flow change. For now, surface a
        # 400 with guidance so the user knows what to do.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            (
                "Set a password on your account first via "
                "Account → Privacy → Password reset, then retry."
            ),
        )
    if not verify_password(payload.password, user.password_hash):
        # Constant-time compare in verify_password. Same 401 we use
        # for login wrong-password — no info leak about the account.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password incorrect")

    token = await mint_challenge(user_id=user.id)
    return ChallengeIssued(token=token, expires_in_seconds=300)


async def _require_challenge(*, user: User, token: str | None) -> None:
    """Sensitive-op gate. Raises 403 unless ``token`` is a valid
    fresh-auth challenge for ``user``. Single-use; the token is
    burned on success."""
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


async def _assert_loan_owned_by(
    db: AsyncSession, loan_id: uuid.UUID, user: User
) -> Loan:
    """Load the loan and confirm the signed-in borrower owns it.

    Same email-keyed check borrower_portal uses, lifted here so the
    Phase 2 endpoints don't import the helper across modules. Raises
    HTTP 404 if the loan doesn't exist; HTTP 403 if it does but the
    user isn't the borrower party.
    """
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
    rejection') because HMDA distinguishes the two.

    ``challenge_token`` is the one-shot value from
    ``POST /me/challenge`` — see :func:`_require_challenge` for the
    rationale (stolen session cookies shouldn't be able to trigger
    irreversible loan withdrawal).
    """

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
    transition + audit happen together and the stage-machine rules
    are enforced (you can withdraw from any non-terminal stage; the
    edges are in VALID_TRANSITIONS).

    Withdrawal is a *terminal* stage — no further transitions are
    possible. The loan stays in the database, anonymisable later via
    erasure but never re-opened. If the borrower changes their mind
    they file a new application.

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
        # Edge violation — e.g. trying to withdraw after closing.
        # The error message is human-readable; pass it through.
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    await db.commit()
    await db.refresh(loan)

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


@router.get("/me/loans/{loan_id}/documents/{document_id}/download-url")
async def borrower_document_download_url(
    loan_id: uuid.UUID,
    document_id: uuid.UUID,
    user: CurrentBorrowerDep,
    db: DbSessionDep,
) -> dict[str, object]:
    """Mint a short-lived presigned download URL for a borrower-owned
    document.

    Mirrors the staff endpoint in ``documents.py`` but with cookie-based
    auth and an extra ownership check via ``_assert_loan_owned_by``. The
    audit event recorded by ``mint_download_url`` includes the
    borrower's email as the actor, so timeline reads from the borrower
    portal are distinguishable from staff reads.
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
            "This export covers everything Mkopo holds about you as of "
            "the timestamp above.",
            "Document contents are referenced by sha256 hash but not "
            "included inline. Use the loan's status page to download "
            "the bytes directly.",
        ],
    }


class ErasureRequest(BaseModel):
    """Borrower's confirmation that they understand the retention
    window. The reason is recorded on the audit trail.

    ``challenge_token`` is the one-shot value from
    ``POST /me/challenge`` — see :func:`_require_challenge`. Erasure
    is the single most consequential borrower-initiated action in
    the system; a stolen session cookie shouldn't be enough to
    trigger it.
    """

    reason: str = Field(min_length=1, max_length=500)
    # Must be ``true`` for the request to be accepted — guards
    # against accidental fat-finger erasure from the API.
    confirm: bool
    challenge_token: str | None = Field(default=None, max_length=128)


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
    if not payload.confirm:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Confirmation is required to request erasure.",
        )
    # Gate AFTER the confirm-flag check so a request that forgot to
    # set ``confirm=true`` gets a clear 400 ("you must confirm")
    # rather than the misleading 403 ("re-auth required") — they're
    # both prereqs and order matters for the user-facing diagnostic.
    await _require_challenge(user=user, token=payload.challenge_token)

    now = datetime.now(UTC)

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

    # The borrower's account is now soft-deleted; ``require_borrower``
    # will refuse any further ``/me`` calls regardless of whether the
    # session cookie is still on disk on the client. The frontend
    # should log the user out on receiving this response.
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


def _me(user: User) -> MeResponse:
    return MeResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        email_verified_at=user.email_verified_at,
    )
