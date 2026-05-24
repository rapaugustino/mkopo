"""Tests for the Redis-backed primitives in ``mkopo.services.redis_client``.

These tests touch a real Redis (the same one configured for the app,
typically ``redis://localhost:6379/0``) and run against an isolated
key prefix so they don't collide with anything else. We skip the
whole module if Redis isn't reachable — CI can run the rest of the
suite without it.

Why not a fake-Redis in-memory shim? Two reasons:
  1. The blacklist + rate-limit code uses real ``SET ex=``, ``INCR``,
     ``EXPIRE``, and pipeline semantics. Fakes routinely diverge on
     pipeline + TTL behaviour, which is the most failure-prone part
     of this code.
  2. The whole point of the module is to be a thin wrapper. Mocking
     it means testing the mock, not the wrapper.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from mkopo.services.redis_client import (
    is_account_locked,
    is_jti_revoked,
    lock_account,
    ping,
    rate_limit_check,
    rate_limit_reset,
    revoke_jti,
    unlock_account,
)


@pytest.fixture(autouse=True)
async def _redis_available():
    """Skip the whole test module if Redis isn't reachable.

    pytest-asyncio's default loop scope is ``function``, so each test
    runs in a fresh event loop. The redis-py async client caches a
    connection pool bound to whichever loop created it — reusing it
    across loops surfaces as opaque "Future attached to a different
    loop" or as the ping silently failing. We clear the module-level
    cache before each test so the next ``get_redis()`` call binds to
    the current loop's pool.
    """
    from mkopo.services import redis_client as _rc

    _rc._client = None

    if not await ping():
        pytest.skip("Redis not reachable — skipping redis_client tests")


def _unique(prefix: str) -> str:
    """Per-test namespace so concurrent test runs don't fight."""
    return f"test-{prefix}-{uuid.uuid4()}"


class TestJwtBlacklist:
    async def test_revoke_then_check_returns_true(self):
        jti = _unique("jti")
        assert not await is_jti_revoked(jti)
        await revoke_jti(jti, ttl_seconds=60)
        assert await is_jti_revoked(jti)

    async def test_unrevoked_jti_returns_false(self):
        assert not await is_jti_revoked(_unique("unrevoked"))

    async def test_revocation_auto_expires(self):
        """TTL must actually fire — otherwise the blacklist would
        grow unboundedly and we'd never reclaim memory."""
        jti = _unique("expiring")
        await revoke_jti(jti, ttl_seconds=1)
        assert await is_jti_revoked(jti)
        await asyncio.sleep(1.5)
        assert not await is_jti_revoked(jti)


class TestRateLimit:
    async def test_first_request_allowed(self):
        allowed, count = await rate_limit_check(
            key=_unique("rl"), limit=3, window_seconds=10
        )
        assert allowed is True
        assert count == 1

    async def test_blocks_after_limit_exceeded(self):
        key = _unique("rl-exceed")
        for _ in range(3):
            allowed, _ = await rate_limit_check(
                key=key, limit=3, window_seconds=10
            )
            assert allowed
        # 4th request exceeds the budget.
        allowed, count = await rate_limit_check(
            key=key, limit=3, window_seconds=10
        )
        assert allowed is False
        assert count == 4

    async def test_reset_clears_counter(self):
        key = _unique("rl-reset")
        for _ in range(5):
            await rate_limit_check(key=key, limit=3, window_seconds=10)
        await rate_limit_reset(key=key)
        allowed, count = await rate_limit_check(
            key=key, limit=3, window_seconds=10
        )
        assert allowed
        assert count == 1


class TestAccountLockout:
    async def test_default_unlocked(self):
        assert not await is_account_locked(email=_unique("locked"))

    async def test_lock_then_check(self):
        email = _unique("victim")
        await lock_account(email=email, ttl_seconds=60)
        assert await is_account_locked(email=email)

    async def test_unlock_clears_lock(self):
        email = _unique("recovered")
        await lock_account(email=email, ttl_seconds=60)
        await unlock_account(email=email)
        assert not await is_account_locked(email=email)

    async def test_lockout_auto_expires(self):
        email = _unique("auto-unlock")
        await lock_account(email=email, ttl_seconds=1)
        assert await is_account_locked(email=email)
        await asyncio.sleep(1.5)
        assert not await is_account_locked(email=email)
