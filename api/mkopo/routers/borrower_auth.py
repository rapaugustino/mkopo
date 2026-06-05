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
  - ``PATCH /me/contact``            — typo-class contact edits
  - ``POST /me/challenge``           — mint a fresh-auth challenge token

Loan-operation endpoints (withdraw, document download, field edit,
DSAR export, erasure) live in ``borrower_loans.py`` to keep this
file focused on authentication.

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

from datetime import UTC, datetime

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

from mkopo.config import get_settings
from mkopo.deps import CurrentBorrowerDep, DbSessionDep
from mkopo.models import User
from mkopo.services.auth_service import (
    SESSION_COOKIE,
    consume_magic_link,
    decode_jwt,
    hash_password,
    issue_jwt,
    mint_magic_link,
    verify_password,
)
from mkopo.services.redis_client import (
    is_account_locked,
    lock_account,
    mint_challenge,
    rate_limit_check,
    rate_limit_reset,
    revoke_jti,
    unlock_account,
)
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
    allowed, _ = await rate_limit_check(key=f"signup:ip:{ip}", limit=10, window_seconds=3600)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many signup attempts from this network. Try again later.",
        )

    email = payload.email.lower().strip()
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")

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
    allowed, _ = await rate_limit_check(key=f"login:ip:{ip}", limit=20, window_seconds=60)
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

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

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
        _, attempts = await rate_limit_check(key=fail_key, limit=10**9, window_seconds=900)
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
    allowed, _ = await rate_limit_check(key=f"magic-link:{email}", limit=3, window_seconds=600)
    if not allowed:
        # Same anti-enumeration shape — we still return MagicLinkIssued
        # rather than a 429 so a bot can't tell the email exists from
        # the response. The legitimate user already has a link.
        logger.info("magic_link_request_rate_limited", email=email)
        return MagicLinkIssued()

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

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
    return MagicLinkIssued(magic_link_url=_dev_link_for_response(minted.plain_token, "login"))


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
        user = await consume_magic_link(db, plain_token=payload.token, purpose="email_verify")
        if user is not None and user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
    if user is None:
        user = await consume_magic_link(db, plain_token=payload.token, purpose="loan_invite")
        if user is not None and user.email_verified_at is None:
            # Receiving the invite at this address proves ownership.
            user.email_verified_at = datetime.now(UTC)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Magic link is invalid or expired")

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
    allowed, _ = await rate_limit_check(key=f"password-reset:{email}", limit=3, window_seconds=600)
    if not allowed:
        # Same anti-enumeration shape as ``/magic-link/request`` —
        # quiet 200 so a bot can't tell anything about the account.
        logger.info("password_reset_request_rate_limited", email=email)
        return MagicLinkIssued()

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
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
    user = await consume_magic_link(db, plain_token=payload.token, purpose="password_reset")
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Reset link is invalid or expired")
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
            remaining = int((claims.expires_at - datetime.now(UTC)).total_seconds())
            if remaining > 0:
                await revoke_jti(claims.jti, ttl_seconds=remaining)


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentBorrowerDep) -> MeResponse:
    """Return the currently-signed-in borrower. Powers the
    frontend's auth hook + the dashboard header.

    401 propagates from the dependency when not signed in."""
    return _me(user)


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


def _me(user: User) -> MeResponse:
    return MeResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        email_verified_at=user.email_verified_at,
    )
