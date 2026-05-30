"""Seed the database with synthetic loan data for development.

Run:
    uv run python scripts/seed.py            # append 10 loans
    uv run python scripts/seed.py --reset    # truncate first, then seed

Idempotent on the underwriter user; loan rows are added each run (so the
reference number increments and you can see the LN-YYYY-NNNN sequence
working). Documents are chunked + embedded so the comparable-loans /
"Ask the file" features have a corpus to work with.

The ``--reset`` flag TRUNCATEs loans (CASCADE to documents, parties,
extractions, audit_events, ...) so a fresh demo starts with exactly the
10 fixtures defined here and the pipeline view isn't cluttered with
appended re-runs.

The fixture catalog itself lives in ``scripts/seed_fixtures/`` —
this file is the runner (helpers + entry point); add new fixtures
there, append them to ``LOANS_TO_SEED`` in
``scripts/seed_fixtures/__init__.py``.
"""

from __future__ import annotations

import argparse
import asyncio

import structlog
from sqlalchemy import select, text

from mkopo.db import get_session
from mkopo.models import (
    Document,
    DocumentType,
    Loan,
    LoanParty,
    LoanStage,
    Party,
    User,
)
from mkopo.services.audit import Actor, record
from mkopo.services.ingest import documents_for_loan, embed_document
from mkopo.services.pdf_render import filename_for_pdf, render_text_to_pdf
from mkopo.services.storage import get_storage

from scripts.seed_fixtures import LOANS_TO_SEED, SeedDoc, SeedLoan, SeedParty

logger = structlog.get_logger()


def _doc_title_for(doc: SeedDoc) -> str:
    """PDF title block for a seeded document.

    Falls through the registered doc-type names ("Loan Application",
    "Appraisal Report", …) with a fallback to a title-cased
    filename for one-off fixtures (``pip_scope.txt`` →
    ``"Pip Scope"``). The result lands as both the visible header
    on every PDF page and the embedded PDF title metadata.
    """
    by_type = {
        DocumentType.LOAN_APPLICATION: "Loan Application",
        DocumentType.APPRAISAL: "Appraisal Report",
        DocumentType.RENT_ROLL: "Rent Roll",
        DocumentType.PERSONAL_FINANCIAL_STATEMENT: "Personal Financial Statement",
        DocumentType.BANK_STATEMENT: "Bank Statement",
        DocumentType.TAX_RETURN: "Tax Return",
        DocumentType.INSURANCE: "Certificate of Insurance",
        DocumentType.TITLE_REPORT: "Title Report",
    }
    if doc.doc_type in by_type:
        return by_type[doc.doc_type]
    stem = doc.filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
    return stem.title()


async def _upsert_party(session, sp: SeedParty) -> Party:
    """Find-or-create a Party by (name + role-driven type)."""
    existing = await session.execute(select(Party).where(Party.name == sp.name))
    p = existing.scalars().first()
    if p:
        return p
    p = Party(name=sp.name, party_type=sp.party_type, email=sp.email)
    session.add(p)
    await session.flush()
    return p


async def _create_loan(session, davis: User, fixture: SeedLoan) -> Loan:
    meta: dict = {"borrower_email": fixture.borrower_email}
    if fixture.meta_extra:
        # Personal-loan fixtures land their DTI / FICO / income fields
        # here so the rules engine sees the same shape the borrower
        # portal would write. String values match how the portal posts
        # them (form-encoded → all strings) so rules_eval's _to_decimal
        # / _to_int parse paths get exercised by seeded data too.
        meta.update(fixture.meta_extra)
    loan_kwargs: dict = {
        "loan_type": fixture.loan_type,
        "amount": fixture.amount_usd,
        "owner_user_id": davis.id,
        "stage": fixture.starting_stage,
        "meta": meta,
    }
    if fixture.loan_class is not None:
        loan_kwargs["loan_class"] = fixture.loan_class
    loan = Loan(**loan_kwargs)
    session.add(loan)
    await session.flush()
    await session.refresh(loan)

    for sp in fixture.parties:
        party = await _upsert_party(session, sp)
        session.add(LoanParty(loan_id=loan.id, party_id=party.id, role=sp.role))

    # Render each seeded document into a real PDF, push it through
    # the configured storage backend (local FS or S3 depending on
    # env), and store the storage URI on the Document row.
    #
    # Why a PDF and not the raw text fixture: the intake agent's
    # extractor runs pypdf on real bytes; reading a "text/plain"
    # Document with text in ``meta.text_content`` shortcuts that
    # entire code path and means a fresh seed never actually
    # exercises the PDF extraction the production flow depends on.
    # Rendering at seed time means an underwriter clicking "Extract
    # documents" on a seeded loan runs the same pypdf round-trip
    # they'd run on a borrower upload.
    #
    # The materials hash also wants stable content_hash values per
    # document; sha256(pdf_bytes) is what we want, so we compute it
    # alongside the upload.
    import hashlib

    storage = get_storage()
    for doc in fixture.documents:
        pdf_filename = filename_for_pdf(doc.filename)
        # Title is the human-friendly doc-type name + the borrower
        # reference; gives the in-app PDF viewer a useful tab name.
        title = _doc_title_for(doc)
        body_text = doc.text
        pdf_bytes = render_text_to_pdf(
            title=title,
            body=body_text,
            footer_ref=f"{loan.reference} · {doc.doc_type.value}",
        )
        uri = await storage.put_object(
            loan_id=loan.id,
            filename=pdf_filename,
            body=pdf_bytes,
            content_type="application/pdf",
        )
        # Keep ``text_content`` in meta so the agents' fast path
        # (read text without re-running pypdf) still works for the
        # already-extracted intake summary. Production uploads go
        # through ``routers/documents.py`` which extracts via pypdf
        # and writes the same field; the seed pre-populates so
        # ad-hoc Q&A / RAG queries work immediately on seeded loans.
        d = Document(
            loan_id=loan.id,
            filename=pdf_filename,
            doc_type=doc.doc_type,
            storage_uri=uri,
            content_type="application/pdf",
            size_bytes=len(pdf_bytes),
            content_hash=hashlib.sha256(pdf_bytes).hexdigest(),
            meta={
                "text_content": body_text,
                "extract": {
                    "char_count": len(body_text),
                    "method": "seed_text_to_pdf",
                },
            },
        )
        session.add(d)

    await session.flush()

    # Chunk + embed every seeded document so "Ask the file" has corpus.
    for d in await documents_for_loan(session, loan.id):
        await embed_document(session, d)

    await record(
        session,
        loan_id=loan.id,
        actor=Actor.system(),
        action="seed_loan_created",
        payload={"source": "seed_script"},
    )

    # If the fixture asks for a non-default starting stage, write a
    # stage_changed audit event so the case-file timeline shows the
    # transition. We don't replay each intermediate stage — one event
    # from intake → starting_stage is a fine abbreviation for seed data.
    if fixture.starting_stage != LoanStage.INTAKE:
        await record(
            session,
            loan_id=loan.id,
            actor=Actor.system(),
            action="stage_changed",
            payload={
                "from": LoanStage.INTAKE.value,
                "to": fixture.starting_stage.value,
                "source": "seed_script",
            },
        )
    return loan


async def _reset_demo_data(session) -> None:
    """Wipe loan-scoped demo data + reset the LN-YYYY-NNNN sequence.

    Loans cascade to documents → extractions → review_tasks → audit
    events → conditions → messages → loan_parties. We also truncate
    ``parties`` since older seed runs created duplicates by name
    (before ``_upsert_party`` enforced uniqueness) and those would
    confuse the entity inspector.

    ``users`` is intentionally NOT truncated — the upserter handles
    re-use of Jordan Davis, and a real install might have other
    underwriter users we don't want to nuke.

    Also clears ``task_runs`` / ``llm_calls`` / ``annotations`` so
    the eval dashboard starts from a clean baseline matching the new
    seed. ``CASCADE`` on llm_calls is required because ``tool_uses``
    (0014) and ``llm_calls.parent_step_id`` (0018) FK-reference it;
    plain ``TRUNCATE`` fails with "cannot truncate a table referenced
    in a foreign key constraint" otherwise.

    ``annotations`` is independent of loans (polymorphic pointers
    rather than FK), so it doesn't get cascaded by the loans wipe —
    truncate it directly so leftover verdicts don't dangle pointing
    at stale agent_run / llm_call ids.
    """
    # CASCADE on loans → documents/extractions/review_tasks/audit_events/
    # conditions/messages/loan_parties/agent_runs/agent_steps.
    await session.execute(text("TRUNCATE TABLE loans CASCADE"))
    await session.execute(text("TRUNCATE TABLE parties CASCADE"))
    await session.execute(text("TRUNCATE TABLE task_runs"))
    # CASCADE here — tool_uses (0014) and llm_calls.parent_step_id
    # FK-reference llm_calls, and annotations.target_id may point at
    # rows we're about to wipe. CASCADE clears the dependents in one
    # statement instead of forcing the order.
    await session.execute(text("TRUNCATE TABLE llm_calls CASCADE"))
    # Annotations are polymorphic (target_kind + target_id, not FK)
    # so the cascade above doesn't reach them. Clear separately to
    # avoid orphan verdicts pointing at now-gone agent_runs.
    await session.execute(text("TRUNCATE TABLE annotations"))
    # `loans.reference` uses a standalone sequence, so TRUNCATE on the
    # owning table doesn't reset it. Restart explicitly so a fresh
    # seed always begins at LN-YYYY-1001.
    await session.execute(text("ALTER SEQUENCE loan_reference_seq RESTART WITH 1001"))


async def seed(reset: bool = False) -> None:
    async with get_session() as session:
        if reset:
            print("⚠️  Truncating loans + task_runs + llm_calls (CASCADE)…")
            await _reset_demo_data(session)
            print("   …done. LN-YYYY-NNNN sequence reset to 1001.\n")

        existing = await session.execute(select(User).where(User.email == "j.davis@mkopo.dev"))
        davis = existing.scalar_one_or_none()
        if davis is None:
            # Seed staff users with a known password so the staff login
            # page works out of the box. ``davis`` is the underwriter
            # owning every seeded loan; ``admin@mkopo.dev`` exists so
            # admin-only paths are also walkthrough-able.
            #
            # The credentials are documented in the README; rotate via
            # ``UPDATE users SET password_hash = ...`` or, in production,
            # don't seed at all and create staff users via the admin
            # console (TODO when that ships).
            from mkopo.services.auth_service import hash_password

            seed_password_hash = hash_password("password123")
            davis = User(
                name="Jordan Davis",
                email="j.davis@mkopo.dev",
                role="underwriter",
                password_hash=seed_password_hash,
            )
            session.add(davis)
            await session.flush()

        # Admin seed — only created if absent, never mutated. The role
        # check on /staff/auth/login admits both 'underwriter' and
        # 'admin', so having both makes role-gated demos easier.
        admin_existing = await session.execute(
            select(User).where(User.email == "admin@mkopo.dev")
        )
        if admin_existing.scalar_one_or_none() is None:
            from mkopo.services.auth_service import hash_password

            session.add(
                User(
                    name="Mkopo Admin",
                    email="admin@mkopo.dev",
                    role="admin",
                    password_hash=hash_password("password123"),
                )
            )
            await session.flush()

        created: list[Loan] = []
        for fixture in LOANS_TO_SEED:
            loan = await _create_loan(session, davis, fixture)
            created.append(loan)
            print(
                f"   {loan.reference}  {fixture.parties[0].name:33s}"
                f"  ${float(loan.amount):>12,.0f}  {fixture.loan_type.value:13s}"
                f"  stage={loan.stage.value}"
            )

    logger.info(
        "seed_complete",
        n_loans=len(created),
        references=[loan.reference for loan in created],
    )
    print(f"\n✅ Seeded {len(created)} loan(s); owner: {davis.name}")
    print(
        "   Documents auto-chunked + embedded so comparable-loans search has corpus.\n"
        "   Run the intake + underwriting agents on each loan to populate "
        "loans.embedding for kNN.\n"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Mkopo demo data.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Truncate loans / task_runs / llm_calls and reset the "
            "LN-YYYY-NNNN sequence before seeding. Destructive but "
            "gives a clean, deterministic demo state."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(seed(reset=args.reset))
