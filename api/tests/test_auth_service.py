"""Auth service primitives — pinned contracts.

The auth service is small but every piece of it gates the entire
borrower self-service product. Tests here exercise:

  - bcrypt password hash/verify round-trip + the "no password set"
    edge (magic-link-only users)
  - JWT issue/decode round-trip + tampering detection + expiry
  - magic-link mint/consume (we do this in a separate file because
    it needs the DB — see test_magic_link_lifecycle.py if/when added)

The JWT tests run without a DB by constructing a User instance
directly. The password tests are pure-function. The whole file
finishes in well under a second.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

# Settings need at least these to import without exploding.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x/y")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://x/y")

import jwt as pyjwt
import pytest

from mkopo.config import get_settings
from mkopo.models import User
from mkopo.services.auth_service import (
    decode_jwt,
    hash_password,
    issue_jwt,
    verify_password,
)


def _fake_borrower(email: str = "borrower@example.com") -> User:
    """In-memory ``User`` for tests that don't need persistence.

    We're only exercising ``issue_jwt`` / ``decode_jwt`` here, which
    look at ``id``, ``email``, and ``role`` — no DB attached.
    """
    u = User(
        email=email,
        name="Test Borrower",
        role="borrower",
        password_hash=None,
    )
    u.id = uuid.uuid4()
    return u


# ---- password hashing --------------------------------------------------


class TestPasswordHashing:
    def test_hash_then_verify_passes(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", h)

    def test_wrong_password_rejected(self):
        h = hash_password("correct horse battery staple")
        assert not verify_password("wrong", h)

    def test_different_calls_produce_different_hashes(self):
        # bcrypt salts each invocation — two hashes of the same plain
        # text must differ. (And both must still verify successfully.)
        a = hash_password("same plaintext")
        b = hash_password("same plaintext")
        assert a != b
        assert verify_password("same plaintext", a)
        assert verify_password("same plaintext", b)

    def test_empty_password_rejected_at_hash_time(self):
        with pytest.raises(ValueError):
            hash_password("")

    def test_none_hash_always_rejects(self):
        # Magic-link-only users have no password — verify_password
        # must short-circuit to False rather than blowing up.
        assert not verify_password("anything", None)

    def test_empty_plaintext_rejected(self):
        h = hash_password("real password")
        assert not verify_password("", h)

    def test_malformed_hash_fails_closed(self):
        # A corrupted ``password_hash`` column should NOT crash —
        # treated identically to a wrong password so the failure mode
        # doesn't leak structural info.
        assert not verify_password("anything", "not-a-real-bcrypt-hash")


# ---- JWT round-trip ----------------------------------------------------


class TestJwtRoundTrip:
    def test_issue_then_decode_recovers_claims(self):
        user = _fake_borrower()
        token = issue_jwt(user)
        claims = decode_jwt(token)
        assert claims is not None
        assert claims.user_id == user.id
        assert claims.email == user.email
        assert claims.role == "borrower"

    def test_expiry_is_in_the_future(self):
        user = _fake_borrower()
        before = datetime.now(UTC)
        token = issue_jwt(user)
        claims = decode_jwt(token)
        assert claims is not None
        # Expiry should be roughly settings.jwt_session_ttl_seconds
        # ahead of "now". Allow a few seconds of clock drift.
        ttl = get_settings().jwt_session_ttl_seconds
        target = before + timedelta(seconds=ttl)
        delta = abs((claims.expires_at - target).total_seconds())
        assert delta < 5

    def test_tampered_signature_rejected(self):
        user = _fake_borrower()
        token = issue_jwt(user)
        # Replace the entire signature segment with a different but
        # valid-looking base64url string. Flipping a single char
        # isn't reliable because base64url has multi-bit alphabet
        # symbols and a final byte's padding can absorb a flip
        # without changing the decoded bytes.
        head, payload, _sig = token.split(".")
        tampered = ".".join([head, payload, "AAAAAAAAAAAAAAAAAAAAAA"])
        assert decode_jwt(tampered) is None

    def test_wrong_secret_rejected(self):
        # A token signed with a different secret must NOT decode.
        # We mint one with PyJWT directly using a wrong key.
        user = _fake_borrower()
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "role": "borrower",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        bogus = pyjwt.encode(payload, "definitely-not-the-server-secret", algorithm="HS256")
        assert decode_jwt(bogus) is None

    def test_expired_token_rejected(self):
        # Mint a token whose ``exp`` is in the past.
        user = _fake_borrower()
        settings = get_settings()
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "role": "borrower",
            "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        }
        expired = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        assert decode_jwt(expired) is None

    def test_malformed_token_rejected(self):
        assert decode_jwt("not.a.real.jwt") is None
        assert decode_jwt("") is None
        assert decode_jwt("totallybogus") is None

    def test_payload_missing_subject_rejected(self):
        # Token signed correctly but lacks ``sub`` — must NOT crash
        # downstream code. Our decoder returns None on KeyError.
        # Use the canonical iss/aud so the token gets past the
        # PyJWT-level checks; the failure should be on the payload
        # shape, not the claims validation.
        settings = get_settings()
        payload = {
            "email": "x@y.z",
            "role": "borrower",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iss": "mkopo-borrower-api",
            "aud": "mkopo-borrower",
        }
        token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        assert decode_jwt(token) is None

    def test_wrong_issuer_rejected(self):
        """A token signed with our secret but with a foreign ``iss``
        must NOT be accepted — defense-in-depth against a future
        deployment where the same secret might be reused across
        sister services."""
        settings = get_settings()
        payload = {
            "sub": str(uuid.uuid4()),
            "email": "x@y.z",
            "role": "borrower",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iss": "some-other-service",
            "aud": "mkopo-borrower",
            "jti": str(uuid.uuid4()),
        }
        token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        assert decode_jwt(token) is None

    def test_wrong_audience_rejected(self):
        """Wrong ``aud`` claim is rejected with the same logic as
        wrong ``iss`` — the decoder pins both."""
        settings = get_settings()
        payload = {
            "sub": str(uuid.uuid4()),
            "email": "x@y.z",
            "role": "borrower",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iss": "mkopo-borrower-api",
            "aud": "mkopo-staff",
            "jti": str(uuid.uuid4()),
        }
        token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        assert decode_jwt(token) is None

    def test_clock_skew_leeway_tolerates_recently_expired(self):
        """A token that expired up to ~30s ago (the configured
        leeway) should still validate. Past that boundary it fails.
        This protects users whose device clock drifted slightly."""
        settings = get_settings()
        # Expired 10 seconds ago — inside the 30s leeway.
        recent_expiry = int(datetime.now(UTC).timestamp()) - 10
        payload = {
            "sub": str(uuid.uuid4()),
            "email": "x@y.z",
            "role": "borrower",
            "iat": recent_expiry - 60,
            "exp": recent_expiry,
            "iss": "mkopo-borrower-api",
            "aud": "mkopo-borrower",
            "jti": str(uuid.uuid4()),
        }
        token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        assert decode_jwt(token) is not None

        # Expired 5 minutes ago — well past the leeway window.
        long_expired = int(datetime.now(UTC).timestamp()) - 300
        payload["exp"] = long_expired
        payload["iat"] = long_expired - 60
        token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        assert decode_jwt(token) is None

    def test_issued_token_carries_iss_and_aud_and_jti(self):
        """Smoke: the live ``issue_jwt`` path emits the iss + aud
        claims that ``decode_jwt`` requires, plus a unique jti per
        token (the blacklist primitive needs it)."""
        user = _fake_borrower()
        settings = get_settings()
        token_a = issue_jwt(user)
        token_b = issue_jwt(user)
        # Decode without enforcing iss/aud so we can read them raw.
        decoded_a = pyjwt.decode(
            token_a,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False, "verify_iss": False},
        )
        decoded_b = pyjwt.decode(
            token_b,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False, "verify_iss": False},
        )
        assert decoded_a["iss"] == "mkopo-borrower-api"
        assert decoded_a["aud"] == "mkopo-borrower"
        assert "jti" in decoded_a
        # Two issuances for the same user must produce different
        # jti — otherwise the blacklist can't distinguish "this
        # session" from "that session" on logout.
        assert decoded_a["jti"] != decoded_b["jti"]
