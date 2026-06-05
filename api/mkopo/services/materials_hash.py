"""Cryptographic hash of the materials that feed a loan decision.

Why this exists
---------------

The underwriting agent and the decision agent cite specific facts:
"the appraised value is $1.2M (see appraisal)", "annual NOI is $284k
(see rent_roll)", "FICO 720 (per applicant disclosure)". Those facts
fed the recommendation. If any of them change after the decision is
made — and before the loan funds — the recommendation has become a
lie. Worse, it's an *invisible* lie: the rationale text still reads
the same, the audit log still says "decision_complete", but the
materials underneath are different.

The materials hash closes that loop. We compute a single sha256 over
a canonical representation of everything that fed the decision:

  - loan core facts (id, amount, loan_type, loan_class)
  - decision-feeding meta (income, credit score, monthly debt, etc.
    for personal; purpose, property facts for business)
  - all accepted/overridden extractions (field, value)
  - every document's sha256 content hash
  - guarantor party ids

The underwriting and decision agents stamp this hash on their
``agent_run.payload`` when they complete. The stage-transition guard
compares the current hash to the decision's hash when advancing past
``decision``. Mismatch → refuse with a clear message ("Materials
have changed since the decision was made — re-run the decision
agent before advancing").

Determinism
-----------

The hash must be reproducible. Two runs on identical inputs MUST
produce the same hash. We enforce this by:

  - Sorting every list/dict consistently before serialisation
  - Using ``json.dumps`` with ``sort_keys=True`` + ``separators=(",", ":")``
    so whitespace doesn't drift the bytes
  - Stringifying Decimal/UUID/datetime to deterministic forms (no
    locale dependence, no microsecond jitter)

What's deliberately NOT in the hash
-----------------------------------

  - audit_events (observers, not inputs)
  - LLM rationale text (output)
  - comparable loans (reference data, not the loan's own)
  - any timestamp / created_at field
  - agent_run metadata

Versioning
----------

The hash is prefixed with a version tag so the algorithm can evolve
without invalidating existing decisions. ``v1:`` is the current
schema. When we add a new input (e.g., guarantor-party-level
financials), bump to ``v2:`` and run a re-baseline.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models import (
    Document,
    Extraction,
    ExtractionStatus,
    Loan,
    LoanParty,
    PartyRole,
)

HASH_VERSION = "v1"

# Meta keys that materially influence a decision. Other keys in
# ``loan.meta`` (borrower_email, borrower_submitted_via_portal, etc.)
# are operational metadata and intentionally excluded so e.g. updating
# a contact email doesn't invalidate a decision.
_DECISION_META_KEYS = (
    # Personal-loan inputs
    "annual_income",
    "monthly_debt_payments",
    "credit_score",
    "employer",
    "years_employment",
    # Business / commercial inputs
    "purpose",
    "property_address",
    "property_type",
)


async def compute_materials_hash(session: AsyncSession, loan_id: uuid.UUID) -> str:
    """Return the canonical materials hash for a loan as ``"v1:<hex>"``.

    Pulls every input that an underwriting or decision agent might
    cite, serialises them deterministically, and hashes the bytes.
    Reads are scoped to ``loan_id`` only — the function cannot
    accidentally fold another loan's data in.
    """
    payload = await _materials_payload(session, loan_id)
    return hash_payload(payload)


def hash_payload(payload: dict[str, Any]) -> str:
    """Hash a materials payload dict.

    Factored out of :func:`compute_materials_hash` so the hash logic
    is testable without a database — pass any dict in, get the
    deterministic ``"v1:<hex>"`` string out. The DB query path
    builds the payload; this function does the canonical-JSON +
    sha256 step.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{HASH_VERSION}:{digest}"


async def _materials_payload(session: AsyncSession, loan_id: uuid.UUID) -> dict[str, Any]:
    """Build the dict that gets hashed. Exposed for testing and for
    the explainability surface ("show me what fed this hash")."""
    loan = (await session.execute(select(Loan).where(Loan.id == loan_id))).scalar_one()

    # Filter meta to the decision-feeding keys; sort the result so
    # JSON serialisation is stable across Python dict-iteration order.
    raw_meta = loan.meta or {}
    meta = {
        k: raw_meta[k] for k in _DECISION_META_KEYS if raw_meta.get(k) not in (None, "", "None")
    }

    # Accepted + overridden extractions — the field/value pairs the
    # rules engine actually evaluates against. Sorted by (field_name,
    # extraction_id) so a re-extraction that adds a new row with the
    # same field doesn't accidentally collide with a different one.
    ext_rows = (
        await session.execute(
            select(Extraction.field_name, Extraction.value, Extraction.id)
            .join(Document)
            .where(
                Document.loan_id == loan_id,
                Extraction.status.in_((ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN)),
            )
            .order_by(Extraction.field_name, Extraction.id)
        )
    ).all()
    extractions = [{"field": r.field_name, "value": r.value, "id": str(r.id)} for r in ext_rows]

    # Documents — capture each one's content_hash. content_hash is
    # nullable for backwards compat; ``null`` is included as itself
    # in the canonical form so the hash differs the moment a hash is
    # filled in (which is also the moment "we know what the bytes
    # are" — the decision should be re-validated then).
    doc_rows = (
        await session.execute(
            select(Document.id, Document.filename, Document.content_hash)
            .where(Document.loan_id == loan_id)
            .order_by(Document.id)
        )
    ).all()
    documents = [
        {
            "id": str(r.id),
            "filename": r.filename,
            "content_hash": r.content_hash,
        }
        for r in doc_rows
    ]

    # Guarantor party ids — sorted, so adding/removing a guarantor
    # shifts the hash but reordering DB-side doesn't.
    g_rows = (
        await session.execute(
            select(LoanParty.party_id)
            .where(
                LoanParty.loan_id == loan_id,
                LoanParty.role == PartyRole.GUARANTOR,
            )
            .order_by(LoanParty.party_id)
        )
    ).all()
    guarantors = [str(r.party_id) for r in g_rows]

    return {
        "v": HASH_VERSION,
        "loan_id": str(loan.id),
        "amount": str(loan.amount),
        "loan_type": _enum_value(loan.loan_type),
        "loan_class": _enum_value(loan.loan_class),
        "meta": meta,
        "extractions": extractions,
        "documents": documents,
        "guarantors": guarantors,
    }


def _enum_value(v: Any) -> str:
    """Coerce a StrEnum to its wire value; plain strings pass through."""
    return v.value if hasattr(v, "value") else str(v)


def _json_default(v: Any) -> Any:
    """Make Decimal/UUID/etc. JSON-serialisable in a deterministic way."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    raise TypeError(f"unhashable type {type(v).__name__}")


async def latest_decision_materials_hash(session: AsyncSession, loan_id: uuid.UUID) -> str | None:
    """Return the materials hash stamped on the most recent COMPLETED
    decision agent run for this loan, or ``None`` if no decision has
    ever completed.

    The hash lives on ``AgentRun.payload["materials_hash"]``. We pick
    the latest by ``created_at`` so re-running the decision agent
    naturally refreshes the baseline. ``None`` means "there is no
    decision to compare against" — the transition guard treats that
    case as "no protection yet" rather than as a mismatch, because
    the protection only matters once a decision exists.
    """
    from mkopo.models import AgentName, AgentRun

    row = (
        await session.execute(
            select(AgentRun.payload)
            .where(
                AgentRun.loan_id == loan_id,
                AgentRun.agent_name == AgentName.DECISION,
                AgentRun.status == "complete",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return row.get("materials_hash") if isinstance(row, dict) else None


async def materials_drift_detected(
    session: AsyncSession, loan_id: uuid.UUID
) -> tuple[bool, str | None, str | None]:
    """Compare the current materials to the most recent decision's
    stamped hash. Returns ``(drifted, current_hash, decision_hash)``.

    - ``(False, h, h)``  — hashes match, safe to proceed
    - ``(False, h, None)`` — no decision has run yet, so there's
      nothing to drift from (caller decides whether that's allowed)
    - ``(True, h_now, h_then)`` — drift detected; caller should
      refuse the action or warn loudly
    """
    decision_hash = await latest_decision_materials_hash(session, loan_id)
    current_hash = await compute_materials_hash(session, loan_id)
    if decision_hash is None:
        return False, current_hash, None
    return current_hash != decision_hash, current_hash, decision_hash
