"""Institution-settings accessor + helper for prompt context.

Two consumers today: the staff ``/settings/institution`` endpoint
(read + update) and every agent that drafts a borrower-visible
artifact (read-only, threaded into the LLM user message).

The accessor reads the singleton row by fixed UUID
(:data:`mkopo.models.INSTITUTION_SINGLETON_ID`). If the row is
missing — should only happen in pre-migration code paths or in a
test that runs against an empty schema — we materialise an
in-memory default with all fields ``None`` so callers don't have
to special-case the absent state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import INSTITUTION_SINGLETON_ID, InstitutionSettings


@dataclass(frozen=True)
class InstitutionContext:
    """Snapshot returned to agents.

    Wraps :class:`InstitutionSettings` as a frozen dataclass so the
    agent code (which lives across a session boundary) doesn't
    accidentally mutate the ORM row or hold stale references after
    the session is closed.

    Every field is ``str | None``. The ``configured`` flag is True
    iff *any* lender contact field is populated — agents use this
    to decide whether to emit "[NOT CONFIGURED]" markers (clear
    operator signal that settings need filling in) versus simply
    skipping optional clauses (e.g. the credit-reporting paragraph
    is only emitted when the agency triple is set).
    """

    lender_name: str | None
    lender_address: str | None
    lender_phone: str | None
    lender_email: str | None
    authorized_officer_name: str | None
    authorized_officer_title: str | None
    credit_reporting_agency_name: str | None
    credit_reporting_agency_address: str | None
    credit_reporting_agency_phone: str | None
    configured: bool

    def has_credit_reporting_agency(self) -> bool:
        """True iff the three CRA fields are all populated. ECOA
        Reg B § 1002.9(b)(2) only requires the disclosure when a
        report was actually consulted; we treat "no agency
        configured" as "no report consulted" for letter-drafting
        purposes. A future feature could mark per-loan which CRA
        was used; for now we read from settings."""
        return (
            self.credit_reporting_agency_name is not None
            and self.credit_reporting_agency_address is not None
            and self.credit_reporting_agency_phone is not None
        )


async def get_institution(session: AsyncSession) -> InstitutionContext:
    """Load the singleton institution settings.

    Returns a fully-typed dataclass even when the DB row is missing,
    so caller code is straight-line. The ``configured`` flag is True
    iff at least one lender-identity field is populated; that's the
    signal the prompts use to decide between "include lender block
    verbatim" and "include a clear placeholder marker so the
    operator knows to fill settings in".
    """
    row = await session.get(InstitutionSettings, INSTITUTION_SINGLETON_ID)
    if row is None:
        return InstitutionContext(
            lender_name=None,
            lender_address=None,
            lender_phone=None,
            lender_email=None,
            authorized_officer_name=None,
            authorized_officer_title=None,
            credit_reporting_agency_name=None,
            credit_reporting_agency_address=None,
            credit_reporting_agency_phone=None,
            configured=False,
        )
    any_lender_field = any(
        v is not None
        for v in (
            row.lender_name,
            row.lender_address,
            row.lender_phone,
            row.lender_email,
        )
    )
    return InstitutionContext(
        lender_name=row.lender_name,
        lender_address=row.lender_address,
        lender_phone=row.lender_phone,
        lender_email=row.lender_email,
        authorized_officer_name=row.authorized_officer_name,
        authorized_officer_title=row.authorized_officer_title,
        credit_reporting_agency_name=row.credit_reporting_agency_name,
        credit_reporting_agency_address=row.credit_reporting_agency_address,
        credit_reporting_agency_phone=row.credit_reporting_agency_phone,
        configured=any_lender_field,
    )


async def update_institution(
    session: AsyncSession, **fields: str | None
) -> InstitutionContext:
    """Patch the singleton row. Unknown keys are ignored (defensive
    against frontend drift); known keys land directly. Strings are
    trimmed; empty strings become ``None`` so the prompt logic's
    "not configured" check works on either form.

    Returns the post-update snapshot so the endpoint can echo it
    back to the client without a follow-up read.
    """
    allowed = {
        "lender_name",
        "lender_address",
        "lender_phone",
        "lender_email",
        "authorized_officer_name",
        "authorized_officer_title",
        "credit_reporting_agency_name",
        "credit_reporting_agency_address",
        "credit_reporting_agency_phone",
    }
    cleaned: dict[str, str | None] = {}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if v is None:
            cleaned[k] = None
            continue
        stripped = v.strip()
        cleaned[k] = stripped or None

    row = await session.get(InstitutionSettings, INSTITUTION_SINGLETON_ID)
    if row is None:
        # Defensive — migration seeds the row, but if a fresh
        # database somehow lands here without it, create on the fly.
        row = InstitutionSettings(id=INSTITUTION_SINGLETON_ID)
        session.add(row)

    for k, v in cleaned.items():
        setattr(row, k, v)

    await session.flush()
    return await get_institution(session)


def materials_block(ctx: InstitutionContext, *, today: datetime | None = None) -> str:
    """Build the "Real identifiers" block agents inject into LLM
    user prompts.

    Mirrors the pattern :mod:`agents.intake` uses for the doc-request
    email: a verbatim block of authoritative values + a hard rule in
    the system prompt forbidding the LLM from emitting bracketed
    placeholders. The dual-side rule (data here, instruction in the
    prompt) is what gets us past the "[LENDER NAME]" failure mode
    where the LLM hallucinates a placeholder.
    """
    when = today or datetime.now()
    lines = [
        "Real identifiers — use these verbatim, no placeholders:",
        f"- Today's date: {when.strftime('%B %d, %Y')}",
    ]

    def _add(label: str, value: str | None, missing_note: str | None = None) -> None:
        if value:
            lines.append(f"- {label}: {value}")
        elif missing_note:
            lines.append(f"- {label}: {missing_note}")

    _add("Lender name", ctx.lender_name, "(not configured — omit clause)")
    _add("Lender address", ctx.lender_address, "(not configured — omit line)")
    _add("Lender phone", ctx.lender_phone, "(not configured — omit line)")
    _add("Lender email", ctx.lender_email, "(not configured — omit line)")
    _add(
        "Authorized officer name",
        ctx.authorized_officer_name,
        "(not configured — sign as 'Credit Committee' generically)",
    )
    _add(
        "Authorized officer title",
        ctx.authorized_officer_title,
        "(not configured — omit title line)",
    )
    if ctx.has_credit_reporting_agency():
        lines.append(
            f"- Credit reporting agency: {ctx.credit_reporting_agency_name}, "
            f"{ctx.credit_reporting_agency_address}, "
            f"{ctx.credit_reporting_agency_phone}"
        )
    else:
        lines.append(
            "- Credit reporting agency: (none consulted — OMIT THE ENTIRE "
            "credit-reporting paragraph from the letter)"
        )
    return "\n".join(lines)
