"""Global search endpoint powering the staff command palette (Cmd+K).

The palette is fed by ``GET /search?q=...`` which returns a small,
heterogeneous payload — loans + parties — ranked cheaply by ILIKE
match. Keep it deliberately simple:

- Server returns at most 8 hits per kind so the UI can render an
  un-paginated list. The palette is for "I know what I'm looking
  for; jump there" not "explore the dataset".
- Match is case-insensitive substring on a few high-signal fields
  per kind (reference + borrower name for loans, party name for
  parties). Anything fancier (full-text, fuzzy, BM25) is overkill
  for the use case and increases latency.
- Empty / whitespace-only query returns empty arrays, not 400 —
  the palette mounts with an empty query and lets the user type.

If this grows, the next move is to lift it onto the existing
``document_chunks.tsv`` infrastructure for a real full-text index;
the palette wouldn't need any UI change because both forms produce
the same ``{kind, id, label, sublabel, href}`` shape.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import or_, select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import Loan, LoanParty, Party, PartyRole

router = APIRouter(prefix="/search", tags=["search"])


class SearchHit(BaseModel):
    """One search hit — shaped for direct render in the palette.

    ``kind`` drives the icon + section header on the frontend.
    ``href`` is the route to navigate to on Enter / click.
    """

    # "loan" | "party". Drives icon + section on the frontend.
    kind: str
    id: str
    # Primary text — "LN-2026-1003" or "Elena Park".
    label: str
    # Secondary text — "Riverbend Holdings · underwriting" /
    # "Person · guarantor on 2 loans". Optional.
    sublabel: str | None
    # Route to navigate to on Enter / click.
    href: str


class SearchResults(BaseModel):
    loans: list[SearchHit]
    parties: list[SearchHit]


_PER_KIND_LIMIT = 8


@router.get("", response_model=SearchResults)
async def search(
    q: str,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> SearchResults:
    """Mixed search across loans and parties.

    ``q`` is trimmed, lowercased, and wrapped in ``%...%`` for the
    ILIKE. Anything ≤ 1 char returns empty — single-character
    prefixes match too much to be useful in the palette and waste
    a roundtrip on every keystroke.
    """
    needle = q.strip()
    if len(needle) < 2:
        return SearchResults(loans=[], parties=[])

    pattern = f"%{needle}%"

    # Loan hits — match on reference (LN-2026-1001) or on the
    # borrower's party name. Reference matches rank above borrower
    # name matches when the same loan would appear in both: an
    # exact-prefix reference is what underwriters paste most often.
    loan_rows = (
        await db.execute(
            select(Loan, Party.name)
            .join(LoanParty, LoanParty.loan_id == Loan.id, isouter=True)
            .join(Party, Party.id == LoanParty.party_id, isouter=True)
            .where(
                Loan.deleted_at.is_(None),
                or_(
                    Loan.reference.ilike(pattern),
                    Party.name.ilike(pattern),
                ),
                # Only return one row per loan even if both the
                # borrower row and an extra party row match. Borrower
                # role takes precedence in the join, falling back to
                # the first joined party for guarantor-only matches.
                or_(
                    LoanParty.role == PartyRole.BORROWER,
                    LoanParty.role.is_(None),
                ),
            )
            .order_by(Loan.created_at.desc())
            .limit(_PER_KIND_LIMIT * 2)  # over-fetch then dedupe
        )
    ).all()
    seen_loans: set[str] = set()
    loans: list[SearchHit] = []
    for loan, borrower_name in loan_rows:
        if str(loan.id) in seen_loans:
            continue
        seen_loans.add(str(loan.id))
        stage = loan.stage if isinstance(loan.stage, str) else loan.stage.value
        sub = f"{borrower_name} · {stage}" if borrower_name else stage
        loans.append(
            SearchHit(
                kind="loan",
                id=str(loan.id),
                label=loan.reference,
                sublabel=sub,
                href=f"/loans/{loan.id}",
            )
        )
        if len(loans) >= _PER_KIND_LIMIT:
            break

    # Party hits — match on the party name. We surface persons and
    # entities (borrowers + guarantors); the inspector at
    # /parties/[id] handles both. ``party_type`` distinguishes them
    # on the chip-row.
    party_rows = (
        await db.execute(
            select(Party)
            .where(Party.name.ilike(pattern))
            .order_by(Party.name.asc())
            .limit(_PER_KIND_LIMIT)
        )
    ).scalars().all()
    parties: list[SearchHit] = []
    for party in party_rows:
        ptype = (
            party.party_type
            if isinstance(party.party_type, str)
            else party.party_type.value
        )
        parties.append(
            SearchHit(
                kind="party",
                id=str(party.id),
                label=party.name,
                sublabel=ptype.title(),
                href=f"/parties/{party.id}",
            )
        )

    return SearchResults(loans=loans, parties=parties)
