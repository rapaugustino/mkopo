"""Document storage. Two backends behind one interface.

Selected via `STORAGE_BACKEND`:

- `local` (default) — writes under `STORAGE_ROOT` on the local filesystem and
  returns `file://` URIs as the canonical storage_uri. Presigned downloads,
  however, are minted as short-lived JWT-signed HTTP URLs that point back at
  this API's ``/storage/local/{token}`` proxy route. Browsers can't iframe
  ``file://`` from an ``http://`` page (security model), so the dev-only
  in-app viewer needs an HTTP origin even when the bytes themselves live on
  the local disk.
- `s3` — writes to `S3_BUCKET` via aioboto3 and returns `s3://` URIs. Point
  `S3_ENDPOINT_URL` at MinIO or LocalStack to exercise this path without
  needing a real AWS account.

The `documents.storage_uri` column carries the scheme, so swapping backends
later is a config change, not a migration.

Security model — single tenant, but with hard decision-integrity guarantees:

- Every read-side method (``get_object``, ``presigned_url``) takes
  ``expected_loan_id`` and verifies the URI's ``loans/<uuid>/...``
  prefix matches before returning bytes. If it doesn't, we raise
  :class:`StorageAuthzError`. The application layer should authz at
  the router boundary — but this is the last line of defense.

- :func:`mint_download_url` is the canonical "I want a download link"
  helper. It does the storage-layer cross-check *and* writes a
  ``document_accessed`` audit event so the case-file timeline reflects
  every read alongside every write. Endpoints should call this rather
  than ``storage.presigned_url`` directly so the audit trail can't be
  forgotten.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

import aioboto3
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.config import get_settings
from mkopo.services.audit import Actor, record

logger = structlog.get_logger()


class Storage(Protocol):
    """Common interface both backends implement.

    Every read-side method takes ``expected_loan_id`` and the
    implementation must verify the URI's loan prefix matches before
    returning bytes or a signed URL. This is defense in depth — even
    if a future router forgets an authz check, the storage layer
    refuses to hand back data for the wrong loan.
    """

    async def put_object(
        self, *, loan_id: uuid.UUID, filename: str, body: bytes, content_type: str
    ) -> str: ...

    async def get_object(self, uri: str, *, expected_loan_id: uuid.UUID) -> bytes: ...

    async def presigned_url(
        self, uri: str, *, expected_loan_id: uuid.UUID, expires_in: int = 300
    ) -> str: ...


class StorageAuthzError(Exception):
    """Raised when a storage read is requested for a loan the URI
    doesn't belong to.

    The storage layer is the last line of defense; if this fires it
    means a router somewhere computed an authz boundary differently
    from the storage key layout. Treat as a security event in logs.
    """


def _loan_id_from_key(key: str) -> uuid.UUID | None:
    """Extract the loan id from a storage key shaped ``loans/<uuid>/...``.

    Returns ``None`` if the key doesn't match — callers treat that as
    "can't verify" and refuse to hand the bytes back. Centralised here
    so both backends use the same parser.
    """
    parts = key.split("/", 3)
    if len(parts) < 3 or parts[0] != "loans":
        return None
    try:
        return uuid.UUID(parts[1])
    except ValueError:
        return None


def _enforce_loan_match(
    actual_loan_id: uuid.UUID | None, expected_loan_id: uuid.UUID, uri: str
) -> None:
    """Raise :class:`StorageAuthzError` if the URI's loan doesn't match.

    Both ``None`` (couldn't parse) and a mismatch are refused — we
    fail closed. Logs the mismatch with both ids so operators can
    triage which caller produced the bad pairing.
    """
    if actual_loan_id != expected_loan_id:
        logger.warning(
            "storage_authz_mismatch",
            uri=uri,
            actual_loan_id=str(actual_loan_id) if actual_loan_id else None,
            expected_loan_id=str(expected_loan_id),
        )
        raise StorageAuthzError(f"document URI does not belong to loan {expected_loan_id}")


class LocalStorage:
    """Filesystem-backed storage. Writes under STORAGE_ROOT."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    async def put_object(
        self,
        *,
        loan_id: uuid.UUID,
        filename: str,
        body: bytes,
        content_type: str,
    ) -> str:
        key = f"loans/{loan_id}/{uuid.uuid4()}/{filename}"
        full_path = self._root / key
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(body)
        logger.info(
            "storage_put_local",
            path=str(full_path),
            bytes=len(body),
            content_type=content_type,
        )
        return f"file://{full_path}"

    async def get_object(self, uri: str, *, expected_loan_id: uuid.UUID) -> bytes:
        if not uri.startswith("file://"):
            raise ValueError(f"LocalStorage cannot handle URI: {uri}")
        # Parse the loan id back out of the path. LocalStorage paths
        # mirror the S3 key layout (loans/<uuid>/...) under STORAGE_ROOT,
        # so the same parser works.
        full = uri[len("file://") :]
        # Best-effort: strip whatever's before the ``loans/`` segment.
        idx = full.find("loans/")
        key = full[idx:] if idx >= 0 else full
        _enforce_loan_match(_loan_id_from_key(key), expected_loan_id, uri)
        return Path(full).read_bytes()

    async def presigned_url(
        self, uri: str, *, expected_loan_id: uuid.UUID, expires_in: int = 300
    ) -> str:
        if not uri.startswith("file://"):
            raise ValueError(f"LocalStorage cannot handle URI: {uri}")
        full = uri[len("file://") :]
        idx = full.find("loans/")
        key = full[idx:] if idx >= 0 else full
        _enforce_loan_match(_loan_id_from_key(key), expected_loan_id, uri)
        # Mint a short-lived JWT that the proxy route below verifies.
        # Returning the raw ``file://`` URI used to be enough for
        # curl-based testing, but the in-app DocumentViewer iframes
        # the URL — and browsers refuse to load ``file://`` resources
        # from an ``http://`` page. So the dev loop has to go through
        # an HTTP proxy on the API itself.
        import time

        import jwt as _jwt

        settings = get_settings()
        payload = {
            "key": key,
            "loan_id": str(expected_loan_id),
            "exp": int(time.time()) + expires_in,
        }
        token = _jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        return f"{settings.api_public_url.rstrip('/')}/api/v1/storage/local/{token}"


class S3Storage:
    """S3-backed storage. Compatible with MinIO/LocalStack via S3_ENDPOINT_URL."""

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._endpoint_url = endpoint_url
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key

    def _client(self):  # type: ignore[no-untyped-def]
        # aioboto3's client() returns an async-context-manager whose type isn't
        # exposed publicly. Callers use `async with self._client() as s3: ...`.
        session = aioboto3.Session()
        return session.client(
            "s3",
            region_name=self._region,
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
        )

    async def put_object(
        self,
        *,
        loan_id: uuid.UUID,
        filename: str,
        body: bytes,
        content_type: str,
    ) -> str:
        key = f"loans/{loan_id}/{uuid.uuid4()}/{filename}"
        async with self._client() as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
        logger.info("storage_put_s3", bucket=self._bucket, key=key, bytes=len(body))
        return f"s3://{self._bucket}/{key}"

    async def get_object(self, uri: str, *, expected_loan_id: uuid.UUID) -> bytes:
        bucket, key = _parse_s3_uri(uri)
        _enforce_loan_match(_loan_id_from_key(key), expected_loan_id, uri)
        async with self._client() as s3:
            response = await s3.get_object(Bucket=bucket, Key=key)
            return await response["Body"].read()  # type: ignore[no-any-return]

    async def presigned_url(
        self, uri: str, *, expected_loan_id: uuid.UUID, expires_in: int = 300
    ) -> str:
        # Default expiry tightened from 3600s (1h) to 300s (5 min) —
        # presigned URLs are bearer tokens; shorter is safer. Callers
        # that need longer (e.g. an explicit "download once" link)
        # pass expires_in explicitly.
        bucket, key = _parse_s3_uri(uri)
        _enforce_loan_match(_loan_id_from_key(key), expected_loan_id, uri)
        async with self._client() as s3:
            return await s3.generate_presigned_url(  # type: ignore[no-any-return]
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in,
            )


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """`s3://bucket/key/with/slashes` → ('bucket', 'key/with/slashes')."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Not an s3 URI: {uri}")
    rest = uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"Malformed s3 URI: {uri}")
    return bucket, key


_storage: Storage | None = None


def get_storage() -> Storage:
    """Module-level singleton. Backend is chosen once, on first access."""
    global _storage
    if _storage is not None:
        return _storage

    settings = get_settings()
    if settings.storage_backend == "s3":
        _storage = S3Storage(
            bucket=settings.s3_bucket,
            region=settings.aws_region,
            endpoint_url=settings.s3_endpoint_url or None,
            access_key_id=settings.aws_access_key_id or None,
            secret_access_key=settings.aws_secret_access_key or None,
        )
    else:
        _storage = LocalStorage(Path(settings.storage_root))
    logger.info("storage_backend_selected", backend=settings.storage_backend)
    return _storage


async def mint_download_url(
    session: AsyncSession,
    *,
    loan_id: uuid.UUID,
    document_id: uuid.UUID,
    storage_uri: str,
    actor: Actor,
    purpose: str = "download",
    expires_in: int = 300,
) -> str:
    """Mint a short-lived presigned download URL **and** record an audit event.

    This is the only correct way for an endpoint to hand a download
    link back to a client. It does two things atomically (in the
    caller's transaction):

    1. Asks the storage layer for a presigned URL, which cross-checks
       the URI against the claimed ``loan_id``. Mismatch raises
       :class:`StorageAuthzError`.
    2. Writes a ``document_accessed`` audit event so the case-file
       timeline shows every read alongside every write — closes the
       "who read borrower X's appraisal" loop without relying on S3
       server-access logs.

    Caller commits the session as usual.

    ``purpose`` is a short tag ("download", "preview", "evidence")
    that lands in the audit payload so timeline filters can split
    operational reads from review-side reads later.
    """
    storage = get_storage()
    url = await storage.presigned_url(storage_uri, expected_loan_id=loan_id, expires_in=expires_in)
    await record(
        session,
        loan_id=loan_id,
        actor=actor,
        action="document_accessed",
        payload={
            "document_id": str(document_id),
            "purpose": purpose,
            "expires_in_seconds": expires_in,
        },
    )
    return url
