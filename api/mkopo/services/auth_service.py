"""Borrower-side authentication primitives.

Three concerns, one module:

  - **Password hashing** (bcrypt, cost 12). Plain-text passwords
    enter ``hash_password`` and never leave; ``verify_password``
    compares a plain-text candidate against a stored hash in
    constant time.

  - **Magic-link tokens**. ``mint_magic_link`` generates a
    cryptographically-random plain-text token, stores
    ``sha256(token)`` in the DB (never the plain text), and returns
    the plain text *exactly once* so the caller can put it in an
    email. ``consume_magic_link`` accepts the plain text, looks up
    by hash, checks expiry + purpose + single-use, and burns the row.

  - **Session JWTs**. ``issue_jwt`` creates a short-lived (12h by
    default) HS256 token carrying the user's id, email, and role.
    ``decode_jwt`` verifies + returns the claims. Tokens are
    delivered to the frontend via an httpOnly + SameSite=Lax cookie
    — that's the right primitive for "browser-mediated login that
    doesn't expose the token to JS".

Why HS256 (symmetric) instead of RS256 (asymmetric): the borrower
side and the API side are the same service. There's no third-party
verifier that needs a public key. HS256 with a 256-bit secret is
simpler, faster, and exactly as secure for this topology.

Why bcrypt instead of argon2: bcrypt has the wider library
ecosystem in Python land, has stood up to two decades of scrutiny,
and the cost-12 setting (~250ms per hash) is the conventional
sweet spot for password verification latency.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import bcrypt
import jwt
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.config import get_settings
from mkopo.models import MagicLink, User

logger = structlog.get_logger()

# The four kinds of magic link the system knows how to issue. Each
# has a distinct purpose so a "set password" token can't be replayed
# at the "login" consume endpoint.
MagicLinkPurpose = Literal["login", "set_password", "password_reset", "email_verify"]


# ---- password hashing --------------------------------------------------


# Cost 12 → ~250ms per verify on modern hardware. Tight enough to
# slow brute force, slow enough to feel like nothing in a real flow.
_BCRYPT_COST = 12


def hash_password(plaintext: str) -> str:
    """Hash a password with bcrypt. Returns the ``$2b$…`` ASCII form
    you can stuff into ``users.password_hash``.

    ``plaintext`` must be non-empty; we don't enforce a minimum
    length here because that's a UX policy decision belonging on the
    signup route. The hash itself is a function of whatever you give
    it.
    """
    if not plaintext:
        raise ValueError("password must be non-empty")
    return bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_COST)
    ).decode("ascii")


def verify_password(plaintext: str, hashed: str | None) -> bool:
    """Constant-time compare. ``None`` hash always returns False —
    handles magic-link-only users (who have no password) without
    needing the caller to guard.

    Catches malformed hashes and returns False rather than crashing,
    because a corrupted ``password_hash`` should look identical from
    the outside to a wrong password (don't leak structure)."""
    if not hashed or not plaintext:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed hash. Treat as "doesn't match".
        return False


# ---- magic-link tokens -------------------------------------------------


@dataclass(frozen=True)
class MintedLink:
    """Return shape for :func:`mint_magic_link`.

    ``plain_token`` is the value you put in the email link — it's
    only ever in scope inside the caller's stack frame. ``link_id``
    can be logged or audited safely; it's not a credential.
    ``expires_at`` is informational.
    """

    plain_token: str
    link_id: uuid.UUID
    expires_at: datetime


def _hash_token(plain_token: str) -> str:
    return hashlib.sha256(plain_token.encode("utf-8")).hexdigest()


async def mint_magic_link(
    session: AsyncSession,
    *,
    user: User,
    purpose: MagicLinkPurpose,
) -> MintedLink:
    """Generate a single-use token for ``user`` and persist its hash.

    Returns the plain token in a :class:`MintedLink` so the caller
    can put it in the outbound email. The plain text is NEVER stored.
    The DB sees only ``sha256(plain_token)``.

    Caller commits the session.
    """
    settings = get_settings()
    # 32 bytes ⇒ 256 bits of entropy. ``token_urlsafe`` produces a
    # URL-safe base64 string (~43 chars) — fits cleanly in an email
    # link without escaping.
    plain_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain_token)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.magic_link_ttl_seconds)
    row = MagicLink(
        user_id=user.id,
        token_hash=token_hash,
        purpose=purpose,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "magic_link_minted",
        user_id=str(user.id),
        purpose=purpose,
        link_id=str(row.id),
        expires_at=expires_at.isoformat(),
    )
    return MintedLink(plain_token=plain_token, link_id=row.id, expires_at=expires_at)


async def consume_magic_link(
    session: AsyncSession,
    *,
    plain_token: str,
    purpose: MagicLinkPurpose,
) -> User | None:
    """Burn a magic link and return the bound user, or ``None`` if
    invalid.

    Reasons we'd return ``None``:

      - the token doesn't exist (hash lookup miss)
      - the token was for a different purpose than the caller claims
        (category-confusion guard)
      - the token has already been consumed
      - the token has expired

    Every failure path logs at warning level with the link id (when
    available) so abuse looks visible in observability. We never log
    the plain token.
    """
    token_hash = _hash_token(plain_token)
    row = (
        await session.execute(
            select(MagicLink).where(MagicLink.token_hash == token_hash)
        )
    ).scalar_one_or_none()

    if row is None:
        logger.warning("magic_link_consume_miss", purpose=purpose)
        return None
    if row.purpose != purpose:
        logger.warning(
            "magic_link_purpose_mismatch",
            link_id=str(row.id),
            expected=purpose,
            actual=row.purpose,
        )
        return None
    if row.consumed_at is not None:
        logger.warning("magic_link_already_consumed", link_id=str(row.id))
        return None
    if row.expires_at <= datetime.now(UTC):
        logger.warning(
            "magic_link_expired",
            link_id=str(row.id),
            expired_at=row.expires_at.isoformat(),
        )
        return None

    row.consumed_at = datetime.now(UTC)
    await session.flush()
    user = (
        await session.execute(select(User).where(User.id == row.user_id))
    ).scalar_one_or_none()
    if user is None:
        # Foreign key would normally prevent this, but the user could
        # have been deleted between mint and consume. Treat as invalid.
        logger.warning("magic_link_user_missing", link_id=str(row.id))
        return None
    return user


# ---- session JWTs ------------------------------------------------------


@dataclass(frozen=True)
class SessionClaims:
    """Decoded contents of a borrower session JWT."""

    user_id: uuid.UUID
    email: str
    role: str
    issued_at: datetime
    expires_at: datetime


_JWT_ALG = "HS256"


def issue_jwt(user: User) -> str:
    """Mint a short-lived session JWT for ``user``.

    Carries the user id, email, and role so the dependency that
    resolves the current user from a cookie can do it without a DB
    round-trip on every request. Email is in the claims so logs
    show a useful identifier without re-querying users.

    Tokens are HS256-signed with ``settings.jwt_secret``. Rotating
    the secret invalidates all outstanding sessions — that's the
    intentional kill switch.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.jwt_session_ttl_seconds)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALG)


def decode_jwt(token: str) -> SessionClaims | None:
    """Verify and unpack a session JWT. ``None`` on any failure
    (bad signature, expired, malformed payload).

    Caller treats ``None`` uniformly as "not authenticated" — we
    don't differentiate the failure reason to the client because
    that would leak whether a token *was* valid at some point.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_JWT_ALG])
    except jwt.PyJWTError as e:
        logger.debug("jwt_decode_failed", reason=type(e).__name__)
        return None
    try:
        return SessionClaims(
            user_id=uuid.UUID(payload["sub"]),
            email=payload["email"],
            role=payload["role"],
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError):
        # Token was signed by us but the payload shape is wrong —
        # treat as invalid.
        return None


# Cookie name the session JWT lives under. Centralised so the auth
# router and the dependency stay in sync without a string literal
# scattered around.
SESSION_COOKIE = "mkopo_session"
