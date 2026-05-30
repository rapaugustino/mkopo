"""Staff auth resolver — accepts staff JWT via cookie or bearer header.

Two ways to authenticate a staff request, in priority order:

1. ``mkopo_staff_session`` cookie — set by ``POST /staff/auth/login``.
   This is the SPA path.
2. ``Authorization: Bearer <jwt>`` header — the same JWT, suitable
   for CLI scripts and integration tests. Useful for headless tools
   that can't carry a cookie jar.

History note: an earlier ``dev_api_token`` bearer shortcut existed
for local-dev curl convenience. It was removed (May 2026) because
both surfaces are fully cookie-based now, no caller in the codebase
used it, and the placeholder default was a credible attack surface
on any exposed dev instance. To poke the API from a script, mint a
real JWT via the login flow:

    curl -X POST $API/api/v1/staff/auth/login \\
      -H 'content-type: application/json' \\
      -d '{"email": "j.davis@mkopo.dev", "password": "password123"}'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.db import get_db
from mkopo.models import User
from mkopo.services.auth_service import (
    STAFF_SESSION_COOKIE,
    decode_staff_jwt,
)
from mkopo.services.redis_client import is_jti_revoked

bearer_scheme = HTTPBearer(auto_error=False)

BearerCredsDep = Annotated[
    HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
]


@dataclass
class CurrentUser:
    """Identity of the staff caller behind the request.

    ``role`` is the canonical RBAC value the tool registry (and any
    other gate that asks "can this user do X?") consumes.

    ``is_admin`` is kept for backwards compatibility with older
    callers that switch on it; new code should branch on ``role``.
    """

    user_id: str
    workspace_id: str
    role: str = "underwriter"
    is_admin: bool = False


# Staff roles permitted to act on the platform. The borrower role
# is explicitly NOT here — a borrower JWT also fails earlier (wrong
# audience) but listing the allowed set makes the intent obvious
# at the bottleneck.
_STAFF_ROLES = frozenset({"underwriter", "admin"})


async def _resolve_from_jwt(
    db: AsyncSession, token: str
) -> CurrentUser | None:
    """Decode a staff JWT and load the user. Returns ``None`` on
    any failure so the caller's fallback logic can fire."""
    claims = decode_staff_jwt(token)
    if claims is None:
        return None
    if await is_jti_revoked(claims.jti):
        return None
    user = (
        await db.execute(select(User).where(User.id == claims.user_id))
    ).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        return None
    if user.role not in _STAFF_ROLES:
        return None
    return CurrentUser(
        user_id=str(user.id),
        workspace_id="default",
        role=user.role,
        is_admin=user.role == "admin",
    )


async def require_user(
    creds: BearerCredsDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    session_cookie: Annotated[
        str | None, Cookie(alias=STAFF_SESSION_COOKIE)
    ] = None,
) -> CurrentUser:
    """Resolve the current staff user.

    Order of checks:

    1. Staff session cookie (``mkopo_staff_session``) — the SPA path.
    2. ``Authorization: Bearer <jwt>`` where the token is a staff JWT.

    The 401 message is intentionally generic — no "expired" vs
    "wrong secret" vs "no such user" differentiation that an
    attacker could probe.
    """
    # 1. Cookie path (preferred — set by /staff/auth/login).
    if session_cookie:
        user = await _resolve_from_jwt(db, session_cookie)
        if user is not None:
            return user

    # 2. Bearer-header path. Same JWT, different transport — for CLI
    # scripts and integration tests that can't carry a cookie jar.
    if creds and creds.credentials:
        user = await _resolve_from_jwt(db, creds.credentials)
        if user is not None:
            return user

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Authentication required"
    )
