"""Shared FastAPI dependencies.

Define each dependency once as an `Annotated[T, Depends(...)]` alias and
import it everywhere — keeps routers tidy and means a dependency's wiring
changes in exactly one place.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.db import get_db
from mkopo.models import User
from mkopo.routers.auth import CurrentUser, require_user
from mkopo.services.auth_service import SESSION_COOKIE, decode_jwt

# A logged-in caller (dev: bearer-token auth; production: swap require_user).
CurrentUserDep = Annotated[CurrentUser, Depends(require_user)]

# A scoped, request-bound async SQLAlchemy session.
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


async def require_borrower(
    db: Annotated[AsyncSession, Depends(get_db)],
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    """Resolve the borrower from the session cookie.

    Reads the JWT out of the ``mkopo_session`` httpOnly cookie set
    at login, verifies it, then loads the corresponding ``User``
    row. Refuses if:

      - the cookie is missing  → 401
      - the JWT is malformed / expired / signed with a stale secret
        → 401
      - the user row is gone (deleted since the token was issued) → 401
      - the user's role isn't ``borrower`` → 403 (a staff session
        cookie is not a borrower credential)

    Every 401/403 here is a real auth event; we deliberately don't
    differentiate the cause to the client (no "unknown user" vs
    "expired" leakage).
    """
    if not session_cookie:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")

    claims = decode_jwt(session_cookie)
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid")

    user = (
        await db.execute(select(User).where(User.id == claims.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid")

    # Soft-deleted users (post-erasure) get the same 401 a missing
    # session would — they don't get to act on their account during
    # the retention window. We use 401 not 403 because the borrower
    # IS authenticating correctly; we just refuse to honour the
    # session.
    if user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid")

    if user.role != "borrower":
        # A staff member's JWT (if we ever issue one for the same
        # cookie name) shouldn't auth a borrower endpoint. The two
        # surfaces will live behind separate dependencies regardless.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Borrower endpoint requires borrower role"
        )

    return user


# A logged-in *borrower* (self-service applicant).
CurrentBorrowerDep = Annotated[User, Depends(require_borrower)]
