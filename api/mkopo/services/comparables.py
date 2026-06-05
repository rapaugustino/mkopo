"""Comparable-loans search via cosine kNN on `loans.embedding`.

Given a loan that's been underwritten (so `embedding` is populated), find
the other underwritten loans most similar to it semantically. The
similarity metric is cosine — pgvector exposes it via the `<=>` operator
(distance), so similarity = 1 - distance.

What "similar" means here is whatever the underwriting-summary embedding
captures: property type, geography, financial profile, risk flags. The
underwriting agent builds that corpus deliberately in `_build_search_corpus`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.services.embeddings import get_embedding_service


@dataclass(frozen=True)
class ComparableLoan:
    """One match from the comparable-loans search."""

    loan_id: uuid.UUID
    reference: str
    borrower: str | None
    loan_type: str
    amount: Decimal
    risk_band: str | None
    similarity: float  # 0..1


async def comparable_loans(
    session: AsyncSession,
    loan_id: uuid.UUID,
    *,
    limit: int = 5,
) -> list[ComparableLoan]:
    """Top-K loans most similar to `loan_id` by underwriting-summary embedding.

    Skips loans without an embedding (haven't been underwritten yet).
    Excludes the source loan from its own results.
    """
    svc = get_embedding_service()

    # Single SQL with a self-cross to compute distance from the source loan's
    # embedding to every other loan with one. We use the `<=>` operator
    # (cosine distance) directly — pgvector returns a float, similarity is
    # 1 - distance.
    #
    # Borrower comes from joining loan_parties + parties for the BORROWER
    # role. Multiple borrowers per loan get the first by name.
    stmt = text(
        """
        WITH src AS (
            SELECT embedding
            FROM loans
            WHERE id = :loan_id AND embedding IS NOT NULL
        ),
        borrower AS (
            SELECT DISTINCT ON (lp.loan_id)
                lp.loan_id, p.name AS borrower_name
            FROM loan_parties lp
            JOIN parties p ON p.id = lp.party_id
            WHERE lp.role = 'borrower'
            ORDER BY lp.loan_id, p.name
        )
        SELECT
            l.id,
            l.reference,
            l.loan_type,
            l.amount,
            l.risk_band,
            b.borrower_name,
            1 - (l.embedding <=> src.embedding) AS similarity
        FROM loans l
        CROSS JOIN src
        LEFT JOIN borrower b ON b.loan_id = l.id
        WHERE l.embedding IS NOT NULL
          AND l.id <> :loan_id
          AND l.deleted_at IS NULL
        ORDER BY l.embedding <=> src.embedding
        LIMIT :limit
        """
    ).bindparams(bindparam("loan_id", type_=None))  # uuid handled by asyncpg

    rows = await session.execute(stmt, {"loan_id": loan_id, "limit": limit})
    _ = svc  # kept import for future hybrid-search use; quiet linter
    return [
        ComparableLoan(
            loan_id=row.id,
            reference=row.reference,
            borrower=row.borrower_name,
            loan_type=row.loan_type,
            amount=Decimal(row.amount),
            risk_band=row.risk_band,
            similarity=float(row.similarity),
        )
        for row in rows
    ]
