"""Retrieval-augmented Q&A over a loan's documents and comparable loans.

This is the "Ask the file" feature from the copilot mockup. It is a
service (not an agent) per DESIGN §6.5 — vector search is listed as a
tool, not an agent. One request in, one structured answer out, citations
attached. No interrupts, no checkpointer.

Pipeline:

1. Embed the user's question.
2. Retrieve top-K document chunks for this loan via cosine kNN on
   `document_chunks.embedding`.
3. Retrieve top-K comparable loans via cosine kNN on `loans.embedding`.
4. Hand both, with the question, to the LLM via the schema-gated gateway.
5. Return the typed answer + citations + comparables.

The model is told explicitly it MUST cite by chunk number and that an
"insufficient context" answer is preferred over hallucination. This is
the same boundary discipline we use for the underwriting summary.
"""

from __future__ import annotations

import uuid

import structlog
from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway
from mkopo.schemas import AskResponse, CitedChunk, ComparableLoanOut
from mkopo.services.comparables import comparable_loans
from mkopo.services.embeddings import get_embedding_service

logger = structlog.get_logger()


def _reciprocal_rank_fusion(*ranked_lists, key, k: int = 60):
    """Reciprocal Rank Fusion. Cormack, Clarke, Buettcher (2009).

    For each item, score = sum over each list of 1 / (k + rank), where rank
    is the 1-indexed position in that list (items absent from a list
    contribute 0). Returns items sorted by combined score, descending.

    Why RRF over a learned linear combination: it has no hyperparameters
    to tune per-corpus, it's robust to the wildly different magnitudes
    that cosine-similarity and ts_rank_cd produce, and it's the de-facto
    choice in modern retrieval systems (Elastic, OpenSearch, LangChain).
    """
    scores: dict = {}
    representative: dict = {}  # last-seen row object for each key
    for lst in ranked_lists:
        for rank, item in enumerate(lst, start=1):
            ident = key(item)
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (k + rank)
            # Prefer the row from the dense list (assumed first) for the
            # `.similarity` field used in display.
            if ident not in representative:
                representative[ident] = item

    ordered = sorted(scores, key=lambda i: scores[i], reverse=True)
    return [representative[i] for i in ordered]


# Retrieval shape: hybrid (dense + sparse) → RRF fusion → top-K to prompt.
#
# `RETRIEVAL_DEPTH` is how many candidates each of the dense and sparse
# searches pulls back BEFORE fusion. `CHUNK_K` is how many fused results
# we hand to the LLM.
#
# RRF constant k=60 is the value used in the original paper (Cormack et
# al. 2009) and works well across a wide range of corpora.
RETRIEVAL_DEPTH = 8
CHUNK_K = 4
COMPARABLE_K = 3
RRF_K = 60


class _DraftedAnswer(BaseModel):
    """LLM output. `cited_chunk_ordinals` lists which retrieved chunks
    the answer actually used — by position in the prompt's chunk list,
    1-indexed. The service then maps back to real document/chunk rows.
    """

    answer: str = Field(min_length=1, max_length=1500)
    cited_chunk_ordinals: list[int] = Field(default_factory=list)


async def answer_question(
    session: AsyncSession,
    *,
    loan_id: uuid.UUID,
    question: str,
) -> AskResponse:
    """End-to-end RAG answer for a single question about a single loan."""
    settings = get_settings()
    svc = get_embedding_service()
    gateway = get_gateway()

    q_vec = await svc.embed(question, session=session)

    # ---- Dense retrieval: cosine kNN on embedding ----
    dense_stmt = text(
        """
        SELECT
            c.id          AS chunk_id,
            c.document_id,
            c.ordinal,
            c.content,
            d.filename,
            1 - (c.embedding <=> :q) AS similarity
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE d.loan_id = :loan_id
        ORDER BY c.embedding <=> :q
        LIMIT :k
        """
    ).bindparams(bindparam("q", type_=Vector(svc.dimensions)))
    dense_rows = (
        await session.execute(dense_stmt, {"q": q_vec, "loan_id": loan_id, "k": RETRIEVAL_DEPTH})
    ).all()

    # ---- Sparse retrieval: Postgres FTS via plainto_tsquery on content_tsv ----
    # `plainto_tsquery` is tolerant of free-form input (won't error on
    # punctuation), and ts_rank_cd is the standard relevance score.
    sparse_stmt = text(
        """
        SELECT
            c.id          AS chunk_id,
            c.document_id,
            c.ordinal,
            c.content,
            d.filename,
            ts_rank_cd(c.content_tsv, plainto_tsquery('english', :q)) AS similarity
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE d.loan_id = :loan_id
          AND c.content_tsv @@ plainto_tsquery('english', :q)
        ORDER BY similarity DESC
        LIMIT :k
        """
    )
    sparse_rows = (
        await session.execute(
            sparse_stmt, {"q": question, "loan_id": loan_id, "k": RETRIEVAL_DEPTH}
        )
    ).all()

    # ---- Fusion: Reciprocal Rank Fusion ----
    # Score = sum over each retriever of 1 / (k + rank), then sort desc.
    fused = _reciprocal_rank_fusion(dense_rows, sparse_rows, key=lambda row: row.chunk_id)[:CHUNK_K]

    cited_chunks = [
        CitedChunk(
            document_id=row.document_id,
            filename=row.filename,
            ordinal=row.ordinal,
            content=row.content,
            # Surface the dense-side cosine similarity for display; the RRF
            # score is internal-only and not meaningful to humans.
            similarity=float(row.similarity),
        )
        for row in fused
    ]

    # Comparable loans for context (e.g. "show me similar deals").
    comps_raw = await comparable_loans(session, loan_id, limit=COMPARABLE_K)
    comps = [
        ComparableLoanOut(
            loan_id=c.loan_id,
            reference=c.reference,
            borrower=c.borrower,
            loan_type=c.loan_type,
            amount=c.amount,
            risk_band=c.risk_band,
            similarity=c.similarity,
        )
        for c in comps_raw
    ]

    # Build the LLM prompt. Chunks are numbered 1..N so the model can cite
    # them; we map ordinals back to documents on the way out.
    chunk_block = "\n\n".join(
        f"[{i + 1}] from {c.filename} (chunk {c.ordinal}, similarity {c.similarity:.2f}):\n"
        f"{c.content}"
        for i, c in enumerate(cited_chunks)
    )
    comp_block = (
        "\n".join(
            f"- {c.reference}: {c.borrower or 'borrower unknown'}, {c.loan_type}, "
            f"${float(c.amount):,.0f}, risk={c.risk_band or 'unrated'}, "
            f"similarity {c.similarity:.2f}"
            for c in comps
        )
        or "(none yet — only underwritten loans have embeddings)"
    )

    # System prompt managed through the /prompts UI; identifier
    # qa.answer_question.system. The canonical default lives in
    # mkopo.services.prompts.DEFAULTS.
    from mkopo.services.prompts import get as get_prompt

    system = get_prompt("qa.answer_question.system")
    user = (
        f"Question:\n{question}\n\n"
        f"Retrieved document chunks ({len(cited_chunks)}):\n"
        f"{chunk_block or '(none — this loan has no embedded documents yet)'}\n\n"
        f"Comparable loans ({len(comps)}):\n{comp_block}"
    )

    drafted: _DraftedAnswer = await gateway.call_structured(
        model=settings.llm_default_model,
        system=system,
        user=user,
        schema=_DraftedAnswer,
    )

    # Filter cited_chunks down to ones the model actually referenced, while
    # preserving its citation order (truncate-by-index, 1-based).
    used: list[CitedChunk] = []
    for idx in drafted.cited_chunk_ordinals:
        if 1 <= idx <= len(cited_chunks):
            used.append(cited_chunks[idx - 1])

    logger.info(
        "ask_complete",
        loan_id=str(loan_id),
        dense_hits=len(dense_rows),
        sparse_hits=len(sparse_rows),
        chunks_retrieved_after_fusion=len(cited_chunks),
        comparables_retrieved=len(comps),
        chunks_cited=len(used),
    )

    return AskResponse(
        question=question,
        answer=drafted.answer,
        citations=used,
        comparable_loans=comps,
    )
