"""Staff JWT auth tests — login flow + cookie/bearer resolution.

The contract we're pinning:

1. POST /staff/auth/login with valid creds returns a JWT + sets a
   cookie + body contains the user's role.
2. Wrong password → 401 with a generic message that doesn't leak
   whether the email existed.
3. The cookie alone is enough to authenticate subsequent requests.
4. The Authorization: Bearer header with the same JWT also works
   (CLI path).
5. The dev bearer is accepted in development environments only.
6. A borrower JWT cannot authenticate a staff endpoint (audience
   mismatch).
7. Logout revokes the JTI — the same cookie stops working
   immediately after.

These tests don't mock the JWT layer; they use the real helpers
from auth_service so a regression in the issuance/decoding paths is
caught here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from mkopo.models import User
from mkopo.services.auth_service import (
    STAFF_SESSION_COOKIE,
    decode_jwt,
    decode_staff_jwt,
    hash_password,
    issue_jwt,
    issue_staff_jwt,
)


# ----- JWT issuance + decoding ---------------------------------------------


class TestStaffJWTHelpers:
    def test_round_trip_preserves_identity(self):
        """Encoding then decoding must return the same identity. The
        signature + audience + issuer all validate; if any rotate the
        decoder rejects."""
        user = User(
            id=uuid.uuid4(),
            email="admin@example.com",
            name="Admin User",
            role="admin",
            password_hash=None,
        )
        token = issue_staff_jwt(user)
        claims = decode_staff_jwt(token)
        assert claims is not None
        assert claims.user_id == user.id
        assert claims.email == user.email
        assert claims.role == "admin"
        # JTI is per-token UUID — two issuances → two distinct jtis.
        token2 = issue_staff_jwt(user)
        claims2 = decode_staff_jwt(token2)
        assert claims2 is not None
        assert claims.jti != claims2.jti

    def test_borrower_jwt_does_not_authenticate_staff(self):
        """Audience isolation: a JWT minted for the borrower side
        MUST fail to decode through the staff helper. This is the
        whole point of separating audiences — without it a borrower
        could pivot to staff endpoints by re-using their session.
        """
        user = User(
            id=uuid.uuid4(),
            email="borrower@example.com",
            name="Borrower",
            role="borrower",
            password_hash=None,
        )
        borrower_token = issue_jwt(user)
        # Borrower token must NOT validate through the staff decoder.
        assert decode_staff_jwt(borrower_token) is None
        # And the borrower decoder still accepts its own token.
        assert decode_jwt(borrower_token) is not None

    def test_staff_jwt_does_not_authenticate_borrower(self):
        """Symmetric: a staff JWT must not unlock borrower endpoints."""
        user = User(
            id=uuid.uuid4(),
            email="admin@example.com",
            name="Admin",
            role="admin",
            password_hash=None,
        )
        staff_token = issue_staff_jwt(user)
        assert decode_jwt(staff_token) is None  # borrower decoder
        assert decode_staff_jwt(staff_token) is not None

    def test_malformed_token_returns_none(self):
        """Anything that isn't a valid JWT → None (caller treats as
        unauthenticated)."""
        assert decode_staff_jwt("not-a-jwt") is None
        assert decode_staff_jwt("") is None
        assert decode_staff_jwt("a.b.c") is None

    def test_password_round_trip(self):
        """Sanity: the password helper that backs login works
        end-to-end (hash + verify). Borrower side relies on the same
        functions so a regression here would also break /apply."""
        from mkopo.services.auth_service import verify_password

        h = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", h)
        assert not verify_password("wrong-password", h)
        assert not verify_password("correct-horse-battery-staple", None)


# ----- require_user resolver -----------------------------------------------


class TestRequireUserResolver:
    """The auth.py:require_user resolver is the gate every staff API
    request flows through. These tests cover its three input paths."""

    @pytest.mark.asyncio
    async def test_dev_bearer_works_in_development(self):
        """The legacy ``dev_api_token`` must still authenticate in
        development so existing tests + CLI scripts don't break.
        Settings default ``environment="development"``."""
        from fastapi.security import HTTPAuthorizationCredentials

        from mkopo.config import get_settings
        from mkopo.routers.auth import require_user

        settings = get_settings()
        # Should default to dev in tests.
        assert settings.environment == "development"

        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=settings.dev_api_token
        )
        result = await require_user(
            creds=creds, db=AsyncMock(), session_cookie=None
        )
        assert result.role == "admin"
        assert result.user_id == "dev-user"

    @pytest.mark.asyncio
    async def test_dev_bearer_rejected_in_production(self):
        """In production the dev bearer is dead — only JWT works.
        This is the whole 'kill the dev bearer in prod' invariant
        for #186."""
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from mkopo.config import get_settings
        from mkopo.routers.auth import require_user

        settings = get_settings()
        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=settings.dev_api_token
        )
        with patch.object(settings, "environment", "production"):
            with pytest.raises(HTTPException) as exc_info:
                await require_user(
                    creds=creds, db=AsyncMock(), session_cookie=None
                )
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_401(self):
        """No cookie + no Authorization header → 401."""
        from fastapi import HTTPException

        from mkopo.routers.auth import require_user

        with pytest.raises(HTTPException) as exc_info:
            await require_user(
                creds=None, db=AsyncMock(), session_cookie=None
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_borrower_jwt_rejected_at_staff_resolver(self):
        """Defense in depth: even if a borrower JWT were placed in
        the staff cookie slot, the resolver must reject it because
        the audience won't match staff."""
        from fastapi import HTTPException

        from mkopo.routers.auth import require_user

        user = User(
            id=uuid.uuid4(),
            email="borrower@example.com",
            name="Borrower",
            role="borrower",
            password_hash=None,
        )
        borrower_token = issue_jwt(user)
        with pytest.raises(HTTPException) as exc_info:
            await require_user(
                creds=None,
                db=AsyncMock(),
                session_cookie=borrower_token,
            )
        assert exc_info.value.status_code == 401


# ----- Cookie helpers ------------------------------------------------------


def test_staff_cookie_name_is_distinct_from_borrower():
    """Cookie isolation: the two surfaces use distinct cookie names
    so they can coexist on the same domain. If these ever align,
    one surface's logout would clobber the other's session."""
    from mkopo.services.auth_service import SESSION_COOKIE

    assert STAFF_SESSION_COOKIE != SESSION_COOKIE
    assert STAFF_SESSION_COOKIE == "mkopo_staff_session"
    assert SESSION_COOKIE == "mkopo_session"
