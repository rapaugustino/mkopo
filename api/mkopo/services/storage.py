"""Document storage. Two backends behind one interface.

Selected via `STORAGE_BACKEND`:

- `local` (default) — writes under `STORAGE_ROOT` on the local filesystem and
  returns `file://` URIs. Zero external dependencies, ideal for the dev loop.
- `s3` — writes to `S3_BUCKET` via aioboto3 and returns `s3://` URIs. Point
  `S3_ENDPOINT_URL` at MinIO or LocalStack to exercise this path without
  needing a real AWS account.

The `documents.storage_uri` column carries the scheme, so swapping backends
later is a config change, not a migration.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

import aioboto3
import structlog

from mkopo.config import get_settings

logger = structlog.get_logger()


class Storage(Protocol):
    """Common interface both backends implement."""

    async def put_object(
        self, *, loan_id: uuid.UUID, filename: str, body: bytes, content_type: str
    ) -> str: ...

    async def get_object(self, uri: str) -> bytes: ...

    async def presigned_url(self, uri: str, expires_in: int = 3600) -> str: ...


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

    async def get_object(self, uri: str) -> bytes:
        if not uri.startswith("file://"):
            raise ValueError(f"LocalStorage cannot handle URI: {uri}")
        return Path(uri[len("file://") :]).read_bytes()

    async def presigned_url(self, uri: str, expires_in: int = 3600) -> str:
        del expires_in
        if not uri.startswith("file://"):
            raise ValueError(f"LocalStorage cannot handle URI: {uri}")
        return uri


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

    async def get_object(self, uri: str) -> bytes:
        bucket, key = _parse_s3_uri(uri)
        async with self._client() as s3:
            response = await s3.get_object(Bucket=bucket, Key=key)
            return await response["Body"].read()  # type: ignore[no-any-return]

    async def presigned_url(self, uri: str, expires_in: int = 3600) -> str:
        bucket, key = _parse_s3_uri(uri)
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
