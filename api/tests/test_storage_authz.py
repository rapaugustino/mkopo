"""Storage layer's loan-id cross-check is the last line of defense
against cross-loan document leakage.

The application layer (routers, agents) is *supposed* to verify the
caller has rights to the loan before passing a storage URI to
``get_object`` or ``presigned_url``. But routers come and go, and one
forgotten authz check would expose every borrower's documents. The
storage layer therefore re-validates: it parses the loan id out of
the URI's ``loans/<uuid>/...`` prefix and refuses to return bytes if
that id doesn't match what the caller claimed.

These tests pin that contract. If anyone removes the check or the
parser regresses, CI flips red — not "the latent flaw is back" three
months later.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from mkopo.services.storage import (
    LocalStorage,
    StorageAuthzError,
    _enforce_loan_match,
    _loan_id_from_key,
)

# ---- key parser ---------------------------------------------------------


class TestLoanIdParser:
    def test_canonical_key_parses(self):
        loan = uuid.uuid4()
        key = f"loans/{loan}/{uuid.uuid4()}/appraisal.pdf"
        assert _loan_id_from_key(key) == loan

    def test_key_with_no_loans_prefix_returns_none(self):
        # A malformed or attacker-crafted key with no ``loans/`` segment
        # must parse to None — caller treats that as "fail closed".
        assert _loan_id_from_key("evil/secret/file.pdf") is None

    def test_key_with_garbage_uuid_returns_none(self):
        assert _loan_id_from_key("loans/not-a-uuid/x/y.pdf") is None

    def test_empty_key_returns_none(self):
        assert _loan_id_from_key("") is None


# ---- enforcement helper -------------------------------------------------


class TestEnforceLoanMatch:
    def test_matching_passes_silently(self):
        loan = uuid.uuid4()
        _enforce_loan_match(loan, loan, "s3://bucket/loans/.../x.pdf")
        # No exception — green path.

    def test_mismatch_raises_authz(self):
        loan_a = uuid.uuid4()
        loan_b = uuid.uuid4()
        with pytest.raises(StorageAuthzError):
            _enforce_loan_match(loan_a, loan_b, "s3://bucket/loans/.../x.pdf")

    def test_none_actual_fails_closed(self):
        # "Couldn't parse" must NOT be treated as "ok, allow it".
        loan = uuid.uuid4()
        with pytest.raises(StorageAuthzError):
            _enforce_loan_match(None, loan, "s3://bucket/evil/x.pdf")


# ---- LocalStorage round-trip with authz ---------------------------------


def _run(coro):
    """Sync wrapper for the async storage methods.

    ``asyncio.run`` builds and tears down its own event loop per call,
    which avoids the "no current event loop" deprecation that
    ``get_event_loop()`` warns about on 3.12+.
    """
    return asyncio.run(coro)


class TestLocalStorageEnforcement:
    """LocalStorage is the easier backend to round-trip in tests; the
    S3 backend uses the same helpers so testing one covers the
    invariant for both."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.storage = LocalStorage(Path(self.tmp))
        self.loan = uuid.uuid4()

    def teardown_method(self):
        # Best-effort cleanup so /tmp doesn't fill up.
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_object_matches_succeeds(self):
        uri = _run(
            self.storage.put_object(
                loan_id=self.loan,
                filename="ok.txt",
                body=b"ok",
                content_type="text/plain",
            )
        )
        body = _run(self.storage.get_object(uri, expected_loan_id=self.loan))
        assert body == b"ok"

    def test_get_object_mismatch_raises(self):
        uri = _run(
            self.storage.put_object(
                loan_id=self.loan,
                filename="ok.txt",
                body=b"ok",
                content_type="text/plain",
            )
        )
        other_loan = uuid.uuid4()
        with pytest.raises(StorageAuthzError):
            _run(self.storage.get_object(uri, expected_loan_id=other_loan))

    def test_presigned_url_mismatch_raises(self):
        uri = _run(
            self.storage.put_object(
                loan_id=self.loan,
                filename="ok.txt",
                body=b"ok",
                content_type="text/plain",
            )
        )
        other_loan = uuid.uuid4()
        with pytest.raises(StorageAuthzError):
            _run(self.storage.presigned_url(uri, expected_loan_id=other_loan))

    def test_presigned_url_matches_returns_http_proxy_url(self):
        uri = _run(
            self.storage.put_object(
                loan_id=self.loan,
                filename="ok.txt",
                body=b"ok",
                content_type="text/plain",
            )
        )
        url = _run(self.storage.presigned_url(uri, expected_loan_id=self.loan))
        # What matters for this test is that:
        #   (a) the authz check passed (no StorageAuthzError raised); and
        #   (b) we got back an HTTP proxy URL the in-app DocumentViewer
        #       can iframe — browsers refuse to load ``file://`` from
        #       ``http://`` pages, so LocalStorage mints a short-lived
        #       JWT for ``/api/v1/storage/local/<token>``.
        # If this URL shape changes, also update the matching proxy
        # route handler in routers/storage.py.
        assert url.startswith("http"), f"presigned_url should return an HTTP proxy URL; got {url!r}"
        assert "/api/v1/storage/local/" in url, "expected the local-proxy path segment in the URL"


def _ensure_db_env():
    """Provide enough env to import the storage module without a
    real DB connection. The storage tests don't touch the DB."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x/y")
    os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://x/y")


_ensure_db_env()
