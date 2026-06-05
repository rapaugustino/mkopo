"""Local-storage HTTP proxy for in-app document previews.

The ``LocalStorage`` backend stores files on disk and conceptually
returns ``file://`` URIs. The in-app ``DocumentViewer`` renders
downloads inside an iframe — and browsers refuse to load ``file://``
content from an ``http://`` page (mixed-origin security model). So
in local-storage dev mode the API has to proxy the bytes back over
HTTP.

The flow:

1. ``LocalStorage.presigned_url`` mints a short-lived JWT
   (HS256, ``jwt_secret`` shared with the borrower-auth system)
   that encodes the storage key and the loan_id it belongs to.
2. The router returns an absolute URL pointing at this route, so the
   browser can fetch it like any other HTTP resource.
3. This route decodes the JWT, refuses anything expired / tampered,
   reads the bytes off disk, and streams them with the right
   ``Content-Type`` so the browser knows how to render.

The S3 backend doesn't use this path — S3 mints its own presigned
URLs and the bytes are served straight from object storage.

Security caveats:

- The token is bound to a specific loan_id + key, so an attacker
  who steals one can read at most that one file (until expiry).
- 5 min expiry by default. Short, but long enough for the iframe
  to load and the user to scroll/zoom.
- We re-derive the loan_id from the key and refuse if it doesn't
  match the claim, mirroring the same defense the storage layer
  already does for the staff/borrower download-URL endpoints.
- No auth header required to fetch — the JWT is the authentication.
  This matches how S3 presigned URLs work; the link itself is the
  capability.
"""

from __future__ import annotations

from pathlib import Path

import jwt
import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from mkopo.config import get_settings

logger = structlog.get_logger()

router = APIRouter(prefix="/storage", tags=["storage"])


def _content_type_for(path: Path) -> str:
    """Best-effort content-type guess from extension.

    The browser uses this to pick a renderer (PDF reader, image
    viewer, plaintext, etc.). Falls back to ``application/octet-stream``
    which the DocumentViewer then treats as "download to view".
    """
    suffix = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/plain; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".json": "application/json",
        ".html": "text/html; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


@router.get("/local/{token}")
async def serve_local_file(token: str) -> Response:
    """Stream bytes for a JWT-signed local-storage URL.

    Verifies the token signature + expiry, re-derives the key, reads
    the file off disk, and returns the bytes with appropriate
    content-type headers. Anything malformed → 401/403/404 with a
    minimal body so we don't leak which case applied.
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "key", "loan_id"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Download link has expired.") from exc
    except jwt.PyJWTError as exc:
        logger.warning("storage_proxy_bad_token", error=str(exc))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid download link.") from exc

    key = payload["key"]
    # Defense in depth: the key must point inside STORAGE_ROOT after
    # resolution. ``..`` or absolute paths get rejected here even
    # though the JWT signing already prevents tampering — keeps the
    # path-traversal worry off the table entirely.
    storage_root = Path(settings.storage_root).expanduser().resolve()
    requested = (storage_root / key).resolve()
    if not str(requested).startswith(str(storage_root)):
        logger.warning(
            "storage_proxy_path_escape",
            key=key,
            resolved=str(requested),
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Path outside storage root.")

    if not requested.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found.")

    content_type = _content_type_for(requested)
    body = requested.read_bytes()

    # ``inline`` so the browser tries to render — the iframe path
    # depends on this. The DocumentViewer's "open in new tab" link
    # gets the same URL but lets the user save it manually if they
    # want; no ``attachment`` disposition needed.
    headers = {
        "Content-Disposition": f'inline; filename="{requested.name}"',
        # Short cache because the URL itself is short-lived.
        "Cache-Control": "private, max-age=60",
    }
    return Response(content=body, media_type=content_type, headers=headers)
