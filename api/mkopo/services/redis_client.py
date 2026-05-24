"""Async Redis client + small primitives the rest of the system reaches
for: a session-JWT blacklist (for logout-revocation) and a token-bucket
style rate limiter.

Why a thin module rather than reaching for ``redis.asyncio`` directly
from every caller:

  - We want a single connection pool. ``redis-py``'s ``Redis`` class
    already owns a pool internally; this module ensures everyone
    shares the same instance so the pool actually matters.
  - The blacklist + rate-limit operations are short two-line scripts
    each. Centralising them here means a future change (TTL bump,
    namespace prefix tweak, fallover to a different backend) lands
    in one file and not scattered across the routers.
  - Tests can ``monkeypatch.setattr(redis_client, "get_redis", fake)``
    to swap in a fake without touching any of the routers.

Failure semantics: every helper here is async. If Redis is down, we
log + degrade rather than crash the request:

  - Blacklist check failing closed (i.e., treating "Redis down" as
    "the token MIGHT be revoked, refuse it") would lock everyone out
    on a Redis outage; we'd be choosing availability of a security
    feature over availability of the auth system. We go the other
    way: degrade-open with a structlog warning so operators see it.
  - Rate-limit check failing open similarly — we'd rather let a few
    extra requests through during a Redis outage than 500 the auth
    flow.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from typing import TYPE_CHECKING

import structlog

from mkopo.config import get_settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = structlog.get_logger()

# Module-level singleton. ``redis.asyncio.Redis`` already owns the
# connection pool internally, so sharing this instance is the right
# pattern — creating a new ``Redis`` per request would create a new
# pool per request.
_client: Redis | None = None


def get_redis() -> Redis:
    """Return the shared async Redis client.

    Lazily constructed on first call so the import side-effects don't
    require Redis to be reachable at process start — tests + ops can
    boot the app without a running Redis instance, and only the code
    paths that actually use Redis fail.
    """
    global _client
    if _client is None:
        # Lazy import so the module can be imported in environments
        # where redis isn't installed (e.g. tests that monkeypatch
        # everything). The dep IS in pyproject; this is belt-and-braces.
        from redis.asyncio import Redis

        settings = get_settings()
        # ``from_url`` parses the standard ``redis://[:password]@host:port/db``
        # form. ``decode_responses=True`` makes every reply a str
        # rather than bytes — we never store binary blobs in Redis,
        # only short ASCII keys + counters.
        _client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        # Suppress the noisy "AUTH called without a password set"
        # info log redis-py emits on first use against a passwordless
        # local Redis. We log our own structured boot signal instead.
        logging.getLogger("redis.connection").setLevel(logging.WARNING)
        logger.info("redis_client_initialised", url=_safe_url(settings.redis_url))
    return _client


def _safe_url(url: str) -> str:
    """Strip the password from a redis URL for logging."""
    # Cheap split-based parse; we don't need a real URL parser to
    # redact ``redis://user:pass@host`` ⟶ ``redis://user:***@host``.
    if "@" not in url:
        return url
    prefix, host = url.rsplit("@", 1)
    if ":" in prefix and "//" in prefix:
        scheme, rest = prefix.split("//", 1)
        user, _ = rest.split(":", 1)
        return f"{scheme}//{user}:***@{host}"
    return url


async def ping() -> bool:
    """Liveness check for the readiness probe + startup banner."""
    try:
        r = get_redis()
        return await r.ping()
    except Exception as e:
        logger.warning("redis_ping_failed", error=str(e)[:200])
        return False


# ---- JWT blacklist -----------------------------------------------------

# We store revoked JWT ids under this namespace. The key is just
# ``revoked:<jti>``; the value is "1" (we only care about existence).
# TTL is set to the remaining lifetime of the token at the moment of
# revocation, so the key auto-expires when the token would have
# expired naturally — Redis cleans up for us without a sweep job.
_REVOKE_PREFIX = "jwt:revoked:"


async def is_jti_revoked(jti: str) -> bool:
    """True if this JWT id is on the blacklist. False on success OR
    on a Redis failure (degrade-open — see module docstring)."""
    try:
        r = get_redis()
        return bool(await r.exists(f"{_REVOKE_PREFIX}{jti}"))
    except Exception as e:
        logger.warning("jwt_blacklist_check_failed", jti=jti[:8], error=str(e)[:200])
        return False


async def revoke_jti(jti: str, *, ttl_seconds: int) -> None:
    """Add a JWT id to the blacklist for the rest of its natural life.

    No-op on Redis failure — we log so operators see it, but we don't
    crash the logout endpoint over an observability backend being
    flaky."""
    try:
        r = get_redis()
        # ``set ex=<ttl> nx=False`` — overwrite if the key somehow
        # already exists (idempotent re-logout). TTL is bounded above
        # by the JWT TTL itself so we never store revocations longer
        # than they're useful.
        await r.set(f"{_REVOKE_PREFIX}{jti}", "1", ex=max(1, ttl_seconds))
    except Exception as e:
        logger.warning("jwt_revoke_failed", jti=jti[:8], error=str(e)[:200])


# ---- Rate limiter ------------------------------------------------------


_RATELIMIT_PREFIX = "ratelimit:"


async def rate_limit_check(
    *,
    key: str,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Fixed-window counter rate limit.

    ``key`` is a caller-supplied scope ("login:1.2.3.4",
    "magic-link:user@example.com") — the namespace prefix is added
    here so callers don't have to think about Redis-side hygiene.

    Returns ``(allowed, current_count)``. ``allowed`` is False once
    the count exceeds ``limit`` in the rolling ``window_seconds``.
    Degrades open (allowed=True) on Redis failure so a backend outage
    doesn't break auth.

    Fixed window is simpler + cheaper than a sliding window for this
    use case — the auth-endpoint volumes are low, and the worst case
    is a 2x burst at the window boundary which is still safer than
    the unlimited status quo. Move to a leaky bucket if we ever care
    about boundary smoothing.
    """
    redis_key = f"{_RATELIMIT_PREFIX}{key}"
    try:
        r = get_redis()
        # Pipeline: INCR + EXPIRE atomic against the same key. EXPIRE
        # only persists if the key was JUST created (NX flag), so
        # subsequent INCRs within the window don't reset the clock.
        pipe = r.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, window_seconds, nx=True)
        results = await pipe.execute()
        count = int(results[0])
        return count <= limit, count
    except Exception as e:
        logger.warning(
            "rate_limit_check_failed",
            key=key,
            error=str(e)[:200],
        )
        # Fail open. Worth re-evaluating once we have monitoring for
        # auth-endpoint volume — degrading open during an outage of
        # the limiter is the conservative-for-availability choice.
        return True, 0


async def rate_limit_reset(*, key: str) -> None:
    """Manually clear a rate-limit counter. Used by a successful
    login to wipe the "failed attempts" counter for that user/IP."""
    try:
        r = get_redis()
        await r.delete(f"{_RATELIMIT_PREFIX}{key}")
    except Exception as e:
        logger.warning("rate_limit_reset_failed", key=key, error=str(e)[:200])


# ---- Account lockout ---------------------------------------------------
#
# Built on top of rate_limit_check + a separate "locked" marker. The
# lockout state is intentionally stored as its own key (rather than
# inferred from the counter) so:
#   1. Unlock is a single DEL of the lock key, not a counter rewind
#   2. The counter can roll past the lockout limit without "permanent"
#      lockout semantics — operators can clear locks independently.

_LOCKOUT_PREFIX = "auth:locked:"


async def is_account_locked(*, email: str) -> bool:
    try:
        r = get_redis()
        return bool(await r.exists(f"{_LOCKOUT_PREFIX}{email}"))
    except Exception as e:
        logger.warning("lockout_check_failed", email=email, error=str(e)[:200])
        return False


async def lock_account(*, email: str, ttl_seconds: int) -> None:
    """Mark an account locked for ``ttl_seconds``. Subsequent password
    logins will refuse until a magic-link consume resets the lock.

    The magic-link consume path also needs to call
    :func:`unlock_account` so the legitimate user can recover after a
    brute-force attempt."""
    try:
        r = get_redis()
        await r.set(f"{_LOCKOUT_PREFIX}{email}", "1", ex=max(1, ttl_seconds))
    except Exception as e:
        logger.warning("lockout_set_failed", email=email, error=str(e)[:200])


async def unlock_account(*, email: str) -> None:
    """Clear a lockout. Called by the magic-link consume path so the
    legitimate user can sign back in after a brute-force attempt."""
    try:
        r = get_redis()
        await r.delete(f"{_LOCKOUT_PREFIX}{email}")
    except Exception as e:
        logger.warning("lockout_clear_failed", email=email, error=str(e)[:200])


# ---- Fresh-auth challenge for sensitive operations --------------------
#
# Used to gate irreversible actions (withdrawal, erasure) behind a
# recent password re-entry. The flow:
#
#   1. UI prompts the user for their current password just before the
#      sensitive action (modal).
#   2. ``POST /borrower-auth/me/challenge`` verifies the password,
#      then calls :func:`mint_challenge` here. The plain token goes
#      back to the client in the response body; a sha256 of it lives
#      in Redis with a 5-minute TTL.
#   3. The client passes the plain token alongside the sensitive
#      request payload. The handler calls :func:`consume_challenge`
#      which burns the token (single use) and returns True iff it
#      was valid.
#
# Why the sha256 hashing on the server side: same reasoning as the
# magic-link table. If Redis were somehow dumped (compromised
# replica, mis-configured persistence backup) the plain tokens
# shouldn't be in there. The hashing is cheap.

_CHALLENGE_PREFIX = "auth:challenge:"
# Five minutes is long enough for a user to read a confirmation
# dialog, but short enough that a stolen response can't be replayed
# against the withdraw/erasure endpoint hours later.
_CHALLENGE_TTL_SECONDS = 300


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


async def mint_challenge(*, user_id: uuid.UUID) -> str:
    """Create a fresh-auth challenge token for ``user_id``.

    Returns the **plain** token — the caller (challenge endpoint)
    puts it in the response body. The sha256 of the token is what's
    in Redis; the plain text is never persisted server-side.

    Overwrites any previous challenge for the same user: only the
    most recently minted token is valid. Stops an attacker who scrapes
    an old challenge from getting a second chance after the
    legitimate user re-authenticated.
    """
    plain = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain)
    try:
        r = get_redis()
        await r.set(
            f"{_CHALLENGE_PREFIX}{user_id}",
            token_hash,
            ex=_CHALLENGE_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("challenge_mint_failed", user_id=str(user_id), error=str(e)[:200])
        # We still return the plain token — the consume side will
        # treat the absent-in-Redis case as "invalid" and the user
        # gets a clear error. Not fatal at mint time; we just won't
        # be able to verify it.
    return plain


async def consume_challenge(*, user_id: uuid.UUID, plain_token: str) -> bool:
    """Verify + burn a challenge token. Returns True iff the token
    matches the most recently minted one for this user and is still
    within its TTL.

    Single-use: success deletes the Redis key so the same token can't
    re-authorise a second sensitive action. The intentional model is
    "one challenge per sensitive operation".
    """
    if not plain_token:
        return False
    token_hash = _hash_token(plain_token)
    try:
        r = get_redis()
        key = f"{_CHALLENGE_PREFIX}{user_id}"
        stored = await r.get(key)
        if stored is None or stored != token_hash:
            return False
        # Burn the key so the token can't be replayed. Best-effort —
        # if delete fails the token is still in Redis and could be
        # used once more, but the 5-min TTL bounds the damage.
        await r.delete(key)
        return True
    except Exception as e:
        # Fail closed for the challenge — we DON'T want to silently
        # allow withdrawal/erasure during a Redis outage. This is the
        # one place in this module that prefers safety over
        # availability; sensitive ops can wait for the cache to come
        # back rather than run unauthenticated.
        logger.warning(
            "challenge_consume_failed",
            user_id=str(user_id),
            error=str(e)[:200],
        )
        return False
