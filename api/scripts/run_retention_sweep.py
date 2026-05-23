"""Retention sweep — hard-delete loans + users past their retention window.

Designed to be run as a daily cron job. Idempotent: re-running is
safe and a no-op if nothing's expired since the last run.

What it does, in two passes:

  1. **Loans.** Find every loan with ``deleted_at IS NOT NULL`` and
     ``retention_until <= now()``. Hard-delete those rows. The
     ``ondelete='CASCADE'`` foreign keys on Documents, Extractions,
     AuditEvents, Conditions, AgentRuns, AgentSteps, etc. take care
     of the dependent rows automatically. The actual document bytes
     in S3 are NOT deleted here — those are handled by a separate
     S3 lifecycle policy keyed off the bucket prefix.

  2. **Users.** Find every user with ``deleted_at IS NOT NULL`` who
     no longer has any loans in the database. Hard-delete those.
     This second pass MUST run after the loans pass so a user with
     a single just-deleted loan is eligible in the same sweep.

Dry-run mode (``--dry-run``) prints what would be deleted without
actually deleting. Use this before scheduling a real cron entry.

Run from the api/ directory:
    python scripts/run_retention_sweep.py            # apply
    python scripts/run_retention_sweep.py --dry-run  # preview
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, func, select

from mkopo.db import get_session
from mkopo.models import Loan, LoanParty, Party, PartyRole, User

logger = structlog.get_logger()


async def sweep(dry_run: bool) -> int:
    now = datetime.now(UTC)
    async with get_session() as session:
        # ---- pass 1: expired loans -----------------------------------
        loan_rows = (
            await session.execute(
                select(Loan.id, Loan.reference, Loan.retention_until).where(
                    Loan.deleted_at.is_not(None),
                    Loan.retention_until.is_not(None),
                    Loan.retention_until <= now,
                )
            )
        ).all()
        print(f"Loans past retention: {len(loan_rows)}")
        for row in loan_rows:
            print(f"  - {row.reference} (id={row.id}, until={row.retention_until})")
        if not dry_run and loan_rows:
            ids = [row.id for row in loan_rows]
            await session.execute(delete(Loan).where(Loan.id.in_(ids)))
            print(f"  deleted {len(ids)} loan row(s)")

        # ---- pass 2: orphan users ------------------------------------
        # A soft-deleted user is eligible for hard-delete once they
        # have no remaining loans in the DB. We count loans via the
        # borrower party relationship — i.e., loans where they're
        # the borrower party — because that's the link that survives
        # a loan's hard-delete (no, it doesn't — the LoanParty row
        # cascades with the loan). So the count is just "any party
        # row with this user's email AND any loan tied to it".
        await session.commit()  # flush the pass-1 deletes so pass-2 sees the truth

        candidates = (
            await session.execute(
                select(User.id, User.email).where(User.deleted_at.is_not(None))
            )
        ).all()

        purgeable: list[tuple] = []
        for u in candidates:
            remaining = (
                await session.execute(
                    select(func.count(Loan.id))
                    .join(LoanParty, LoanParty.loan_id == Loan.id)
                    .join(Party, Party.id == LoanParty.party_id)
                    .where(
                        LoanParty.role == PartyRole.BORROWER,
                        Party.email == u.email,
                    )
                )
            ).scalar_one()
            if remaining == 0:
                purgeable.append(u)

        print(f"Users with no remaining loans: {len(purgeable)}")
        for u in purgeable:
            print(f"  - {u.email} (id={u.id})")
        if not dry_run and purgeable:
            ids = [u.id for u in purgeable]
            await session.execute(delete(User).where(User.id.in_(ids)))
            await session.commit()
            print(f"  deleted {len(ids)} user row(s)")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without making changes.",
    )
    args = parser.parse_args()
    return asyncio.run(sweep(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
