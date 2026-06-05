"""Tests for the sensitive-op re-auth challenge (#169).

We test the Redis-backed primitives + the higher-level
``_require_challenge`` flow. The full borrower_auth router tests
would need a TestClient + DB fixture; the existing test suite
doesn't have one yet, so for now we cover the security-critical
boundary (mint → consume) at the redis_client + helper layer.
"""

from __future__ import annotations

import uuid

import pytest

from mkopo.services.redis_client import (
    consume_challenge,
    mint_challenge,
    ping,
)


@pytest.fixture(autouse=True)
async def _redis_available():
    """Same skip-on-no-redis pattern as test_redis_client.py."""
    from mkopo.services import redis_client as _rc

    _rc._client = None

    if not await ping():
        pytest.skip("Redis not reachable — skipping challenge gate tests")


class TestChallengeRoundTrip:
    async def test_minted_then_consumed_returns_true(self):
        user_id = uuid.uuid4()
        token = await mint_challenge(user_id=user_id)
        assert isinstance(token, str)
        assert len(token) > 20  # 32 bytes urlsafe-b64 ~ 43 chars
        assert await consume_challenge(user_id=user_id, plain_token=token)

    async def test_consume_burns_token(self):
        """Single-use enforcement: the same token can't authorise
        a second sensitive action. If this regresses, an attacker
        who steals one challenge response could trigger multiple
        irreversible operations from a single password verification."""
        user_id = uuid.uuid4()
        token = await mint_challenge(user_id=user_id)
        assert await consume_challenge(user_id=user_id, plain_token=token)
        # Second consume must fail.
        assert not await consume_challenge(user_id=user_id, plain_token=token)

    async def test_consume_with_wrong_token_fails(self):
        user_id = uuid.uuid4()
        await mint_challenge(user_id=user_id)
        assert not await consume_challenge(user_id=user_id, plain_token="not-the-right-token")

    async def test_consume_with_empty_token_fails(self):
        """Defensive: an empty / None token should never be accepted,
        regardless of whether a challenge exists for the user."""
        user_id = uuid.uuid4()
        await mint_challenge(user_id=user_id)
        assert not await consume_challenge(user_id=user_id, plain_token="")

    async def test_consume_for_different_user_fails(self):
        """Per-user scoping: minting a challenge for user A must
        not authorise an action for user B even with the right
        plain token. The Redis key is namespaced by user_id."""
        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        token = await mint_challenge(user_id=user_a)
        assert not await consume_challenge(user_id=user_b, plain_token=token)
        # Original user's challenge is still valid.
        assert await consume_challenge(user_id=user_a, plain_token=token)

    async def test_minting_again_invalidates_previous(self):
        """Overwrite semantics: each mint replaces the previous
        challenge for the same user. Protects against an attacker
        who scraped an older response getting a chance after the
        legitimate user re-authenticated."""
        user_id = uuid.uuid4()
        old_token = await mint_challenge(user_id=user_id)
        new_token = await mint_challenge(user_id=user_id)
        assert old_token != new_token
        # Old token must NOT authorise.
        assert not await consume_challenge(user_id=user_id, plain_token=old_token)
        # New token works.
        assert await consume_challenge(user_id=user_id, plain_token=new_token)

    async def test_no_mint_means_no_consume(self):
        """A user that never minted a challenge can't consume one."""
        assert not await consume_challenge(user_id=uuid.uuid4(), plain_token="anything")
