"""OpenAI embeddings service with persistent caching.

Properties:

- All calls flow through one place so we can audit, batch, and rate-limit.
- Persistent cache (`embedding_cache` table) — keyed by sha256(content) +
  model + dimensions. Re-running seed.py never re-bills.
- Returns lists of floats (the on-the-wire format pgvector expects). No
  numpy dependency leaks out.
- Truncates to the configured `embeddings_dimensions` via OpenAI's
  Matryoshka API field — no client-side slicing.

We do NOT route this through `LLMGateway` because the gateway is built
around schema-validated structured output. Embeddings have no schema to
validate — they're just vectors. Audit logging is still done here.
"""

from __future__ import annotations

import hashlib

import structlog
from openai import AsyncOpenAI
from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.config import get_settings

logger = structlog.get_logger()


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class EmbeddingService:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set — required for the embedding service. "
                "Set it in api/.env and restart."
            )
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.embeddings_model
        self._dimensions = settings.embeddings_dimensions

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, content: str, *, session: AsyncSession) -> list[float]:
        """Embed a single string. Cached. Returns a 1024-d vector."""
        results = await self.embed_batch([content], session=session)
        return results[0]

    async def embed_batch(self, contents: list[str], *, session: AsyncSession) -> list[list[float]]:
        """Embed a batch. Cache-aware — only un-cached contents hit OpenAI.

        Order of returned vectors matches order of input contents.
        """
        if not contents:
            return []

        hashes = [_hash(c) for c in contents]
        cached = await self._fetch_cached(session, hashes)
        results: list[list[float] | None] = [cached.get(h) for h in hashes]

        # Anything not in cache, batch-call OpenAI for.
        misses_idx = [i for i, v in enumerate(results) if v is None]
        misses = [contents[i] for i in misses_idx]

        if misses:
            response = await self._client.embeddings.create(
                model=self._model,
                input=misses,
                dimensions=self._dimensions,
            )
            for slot, item in zip(misses_idx, response.data, strict=True):
                vec = list(item.embedding)
                results[slot] = vec
                await self._store_cached(session, hashes[slot], vec)

            logger.info(
                "embeddings_call",
                model=self._model,
                dimensions=self._dimensions,
                requested=len(misses),
                cached_hits=len(contents) - len(misses),
                tokens=response.usage.total_tokens if response.usage else None,
            )
        else:
            logger.info(
                "embeddings_call_all_cached",
                model=self._model,
                dimensions=self._dimensions,
                cached_hits=len(contents),
            )

        return [r for r in results if r is not None]

    async def _fetch_cached(
        self, session: AsyncSession, hashes: list[str]
    ) -> dict[str, list[float]]:
        if not hashes:
            return {}
        # Tell SQLAlchemy the `embedding` result column is pgvector so it
        # decodes asyncpg's raw bytes via pgvector's adapter.
        from sqlalchemy import String, column

        stmt = text(
            "SELECT content_hash, embedding "
            "FROM embedding_cache "
            "WHERE model = :model AND dimensions = :dim "
            "AND content_hash = ANY(:hashes)"
        ).columns(
            column("content_hash", String),
            column("embedding", Vector(self._dimensions)),
        )
        rows = await session.execute(
            stmt,
            {"model": self._model, "dim": self._dimensions, "hashes": hashes},
        )
        return {row.content_hash: list(row.embedding) for row in rows}

    async def _store_cached(
        self, session: AsyncSession, content_hash: str, vec: list[float]
    ) -> None:
        # Bind `:v` with type_=Vector so the asyncpg driver encodes it via
        # pgvector's adapter, not as a generic string. Without this binding
        # asyncpg raises "expected str, got list".
        stmt = text(
            "INSERT INTO embedding_cache (content_hash, model, dimensions, embedding) "
            "VALUES (:h, :m, :d, :v) "
            "ON CONFLICT (content_hash, model, dimensions) DO NOTHING"
        ).bindparams(bindparam("v", type_=Vector(self._dimensions)))
        await session.execute(
            stmt,
            {"h": content_hash, "m": self._model, "d": self._dimensions, "v": vec},
        )


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
