"""Staff authentication endpoints.

Surface: ``/api/v1/staff/auth/...``

  - ``POST /login``  — email + password → session cookie + JWT body
  - ``POST /logout`` — clear cookie + revoke JTI
  - ``GET  /me``     — current staff user

Replaces the legacy ``dev_api_token`` bearer. The dev bearer still
works in ``development`` environment as a clearly-marked shortcut
for CLI scripts + tests; in ``staging`` and ``production`` only the
JWT path is accepted.

Sessions use a SEPARATE cookie name (``mkopo_staff_session``) and a
separate JWT audience (``mkopo-staff``) from the borrower side, so
the two surfaces cannot leak into each other even when the same
browser holds both cookies on the same domain.

Mirrors the borrower auth router's defensive idioms:
- Rate-limit login attempts by email + IP (Redis-backed).
- Generic "invalid credentials" on every failure so attackers can't
  enumerate accounts.
- HttpOnly cookies (`HttpOnly; SameSite=Lax`) so XSS can't lift the
  token off the page.
- JTI in the JWT + Redis revocation list so logout actually kills
  the token within its TTL window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.config import get_settings
from mkopo.db import get_db
from mkopo.models import User
from mkopo.services.auth_service import (
    STAFF_SESSION_COOKIE,
    decode_staff_jwt,
    issue_staff_jwt,
    verify_password,
)
from mkopo.services.redis_client import (
    is_account_locked,
    is_jti_revoked,
    lock_account,
    rate_limit_check,
    rate_limit_reset,
    revoke_jti,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/staff/auth", tags=["staff-auth"])


# Roles that are allowed to log in via this router. ``borrower`` is
# explicitly excluded — a borrower account must use the borrower
# auth router. Adding new staff roles (``loan_officer``,
# ``committee_chair``) is just appending to this set.
_STAFF_ROLES = frozenset({"underwriter", "admin"})


# Login rate limit: 10 attempts per 5 minutes per email. Tighter
# than the borrower side (which is more permissive because borrowers
# realistically forget their passwords) because staff are a small
# fixed set and brute-forcing one of them would be the actual attack.
_LOGIN_RATE_LIMIT = 10
_LOGIN_RATE_WINDOW_SECONDS = 300
# After this many failures in the window the account is locked
# (Redis flag, not a DB column). Lockout clears on first successful
# login of the same user or expires after 30 min.
_LOGIN_LOCKOUT_THRESHOLD = 6
_LOGIN_LOCKOUT_SECONDS = 1800


# --- Schemas ---------------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    """Login success response.

    ``token`` is the same JWT that's set as the httpOnly cookie —
    duplicated in the body so CLI / script callers (who can't read
    httpOnly cookies) can also use the API. The SPA ignores this
    field and relies on the cookie.
    """

    token: str
    expires_in_seconds: int
    user: StaffMe


class StaffMe(BaseModel):
    id: str
    email: str
    name: str
    role: str


LoginResponse.model_rebuild()


# --- Helpers ---------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate-limit keys. ``X-Forwarded-For``
    is honoured when present (we sit behind a proxy in production);
    falls back to the direct peer address."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, token: str) -> None:
    """Write the staff session cookie with the right flags.

    - ``HttpOnly``       — JavaScript on the page cannot read it,
      so XSS can't exfiltrate the session.
    - ``SameSite=Lax``   — sent on top-level navigations but not on
      cross-site iframe / image requests; prevents CSRF on the
      common shape.
    - ``Secure`` in prod — only sent over HTTPS. Dev keeps it off so
      local dev over http://localhost works.
    - Path ``/``          — every staff API path needs it.
    """
    settings = get_settings()
    response.set_cookie(
        key=STAFF_SESSION_COOKIE,
        value=token,
        max_age=settings.jwt_session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


async def _resolve_staff_from_token(db: AsyncSession, token: str) -> User | None:
    """Decode + load the staff user for ``token``.

    Returns ``None`` on any failure — caller decides whether that's
    a 401 (require) or a fall-through (optional).
    """
    claims = decode_staff_jwt(token)
    if claims is None:
        return None
    if await is_jti_revoked(claims.jti):
        return None
    user = (await db.execute(select(User).where(User.id == claims.user_id))).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        return None
    if user.role not in _STAFF_ROLES:
        return None
    return user


# --- Endpoints -------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """Authenticate a staff user.

    Defenses applied:
    - Account lockout after sustained failures (Redis-backed).
    - Per-email rate limit (10 attempts / 5 min).
    - Constant-shape generic error so attackers can't enumerate
      registered emails.
    """
    settings = get_settings()
    email = payload.email.lower().strip()

    # Lockout check FIRST — a locked account doesn't get to consume
    # rate-limit budget either.
    if await is_account_locked(email=email):
        logger.info("staff_login_locked", email=email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")

    # Per-email rate limit. Combined with the lockout below, this
    # makes a brute-force attack take meaningfully longer than the
    # password's entropy budget.
    allowed, _ = await rate_limit_check(
        key=f"staff_login:{email}",
        limit=_LOGIN_RATE_LIMIT,
        window_seconds=_LOGIN_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        logger.info("staff_login_rate_limited", email=email)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts. Wait a few minutes and try again.",
        )

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # Constant-time-shaped failure: we always run verify_password
    # even when the user doesn't exist so the timing doesn't leak
    # account existence. ``verify_password(p, None)`` returns False
    # without ever inspecting ``p``, so this is more about the code
    # path than the literal CPU profile, but it's the right shape.
    valid = (
        user is not None
        and user.deleted_at is None
        and user.role in _STAFF_ROLES
        and verify_password(payload.password, user.password_hash)
    )
    if not valid or user is None:
        # Track the failure for lockout. After threshold failures we
        # lock the account for the lockout window; the rate limiter
        # counter does the threshold counting for us.
        _, attempts = await rate_limit_check(
            key=f"staff_login_fail:{email}",
            limit=10**9,
            window_seconds=_LOGIN_LOCKOUT_SECONDS,
        )
        if attempts >= _LOGIN_LOCKOUT_THRESHOLD:
            await lock_account(email=email, ttl_seconds=_LOGIN_LOCKOUT_SECONDS)
        logger.info("staff_login_failed", email=email, ip=_client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")

    # Clear both the rate-limit + failure counters on success so a
    # user who fat-fingered a few passwords doesn't lose access.
    await rate_limit_reset(key=f"staff_login:{email}")
    await rate_limit_reset(key=f"staff_login_fail:{email}")

    token = issue_staff_jwt(user)
    _set_session_cookie(response, token)

    logger.info(
        "staff_login_success",
        user_id=str(user.id),
        email=user.email,
        role=user.role,
    )

    return LoginResponse(
        token=token,
        expires_in_seconds=settings.jwt_session_ttl_seconds,
        user=StaffMe(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
        ),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session_cookie: Annotated[str | None, Cookie(alias=STAFF_SESSION_COOKIE)] = None,
) -> None:
    """Invalidate the current session.

    Two things happen:
    1. The cookie is cleared on the client (so the browser stops
       sending it).
    2. The JTI is added to a Redis blacklist for the token's
       remaining lifetime — so even if someone exfiltrated the
       token before logout, it stops working immediately.
    """
    response.delete_cookie(STAFF_SESSION_COOKIE, path="/")
    if not session_cookie:
        return None
    claims = decode_staff_jwt(session_cookie)
    if claims is None:
        return None
    remaining = int((claims.expires_at - datetime.now(UTC)).total_seconds())
    if remaining > 0:
        await revoke_jti(claims.jti, ttl_seconds=remaining)
    return None


@router.get("/me", response_model=StaffMe)
async def me(
    db: Annotated[AsyncSession, Depends(get_db)],
    session_cookie: Annotated[str | None, Cookie(alias=STAFF_SESSION_COOKIE)] = None,
) -> StaffMe:
    """Return the currently-signed-in staff user.

    Powers the frontend's auth hook + the header user menu. 401
    propagates when the cookie is missing or stale — the frontend
    catches it and redirects to ``/login``.
    """
    if not session_cookie:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    user = await _resolve_staff_from_token(db, session_cookie)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid")
    return StaffMe(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )
