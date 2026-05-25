"""Intake agent. Runs when a new loan packet arrives.

Responsibilities:
1. Classify and read each uploaded document
2. Extract required fields with confidence scoring
3. Identify missing items vs requirements
4. Draft a doc-request email to the borrower
5. Pause via interrupt() for underwriter approval before sending
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, TypedDict

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from sqlalchemy import select

from mkopo.agents._serde import make_serializer
from mkopo.config import get_settings
from mkopo.db import get_session
from mkopo.llm_gateway import get_gateway
from mkopo.models import Document, Extraction, ExtractionStatus, Loan, ReviewTask
from mkopo.services.audit import Actor, record
from mkopo.tools.comms import OutboundEmail, get_comms
from mkopo.tools.extractor import (
    ExtractedField,
    ExtractionResult,
    extract_fields,
    routing_decision,
    threshold_for,
)

logger = structlog.get_logger()


# Required-field lists branch by loan class.
#
# Commercial real-estate intake needs the borrower entity name,
# property collateral facts (address, type, NOI, appraised value,
# appraisal age), guarantors, and loan size — the inputs to DSCR /
# LTV / debt yield.
#
# Personal-loan intake needs the borrower individual name, SSN
# last-4, employer, annual income, outstanding debt, credit score,
# loan purpose, and loan amount — the inputs to DTI / FICO floor.
#
# A loan's ``loan_class`` selects the list. Until full personal-loan
# rule support lands, the personal list is used for required-field
# detection only; the underwriting agent and rules engine still run
# the commercial path. That's the current honest scope — see the
# DESIGN doc §Loan classes.
REQUIRED_FIELDS_BUSINESS = [
    "borrower_entity",
    "property_address",
    "property_type",  # short phrase, e.g. "12-unit multifamily" or "Class B office"
    "guarantor_list",
    "annual_noi",
    "appraised_value",
    "appraisal_date",
    "loan_amount",
]

REQUIRED_FIELDS_PERSONAL = [
    "borrower_name",
    "ssn_last4",
    "employer",
    "annual_income",
    "outstanding_debt",
    "credit_score",
    "loan_purpose",
    "loan_amount",
]


def required_fields_for(loan_class: str | None) -> list[str]:
    """Return the required-fields list for a loan class.

    ``None`` and unknown values fall back to the business list so
    pre-class loans behave exactly as they did before.
    """
    if loan_class == "personal":
        return REQUIRED_FIELDS_PERSONAL
    return REQUIRED_FIELDS_BUSINESS


# Backwards-compatibility alias — anything that imported the symbol
# before the class branch gets the business list.
REQUIRED_FIELDS = REQUIRED_FIELDS_BUSINESS


# --- State ---


class IntakeState(TypedDict, total=False):
    """Agent working state. Source of truth for loan stays in the database."""

    loan_id: str
    # Loan class — "personal" | "business". Read out of the DB once
    # in ``extract_all_documents`` and threaded through every
    # downstream node so the required-fields list and the email
    # draft prompt both branch on it. Without this, personal-loan
    # applicants would get business-flavoured extraction + email
    # asks ("please send us your rent roll") which is the wrong
    # product entirely.
    loan_class: Literal["personal", "business"]
    extracted_fields: dict[str, ExtractedField]
    missing_fields: list[str]
    draft_email: dict[str, str] | None  # subject + body
    final_email: dict[str, str] | None  # after HITL approval
    status: Literal["running", "awaiting_approval", "complete", "failed"]


# --- Structured outputs for the email drafting step ---


class DraftedEmail(BaseModel):
    subject: str = Field(max_length=120)
    body_text: str = Field(max_length=4000)


# --- Nodes ---


async def extract_all_documents(state: IntakeState) -> IntakeState:
    """Extract required fields from every document on the loan.

    Pre-flight gate: if there are no documents at all, short-circuit
    here with ``status='needs_documents'``. The intake agent's downstream
    nodes (``identify_missing`` → ``draft_request``) would otherwise
    spend an LLM call drafting a borrower email asking for *every*
    required field — which the borrower can't act on because they
    don't even know what they uploaded. The right human action is to
    upload a packet first; the agent should say so plainly and exit
    rather than burning tokens drafting an email that's pure noise.
    """
    loan_id = uuid.UUID(state["loan_id"])
    extracted: dict[str, ExtractedField] = {}

    async with get_session() as session:
        # Load the loan first so we know which required-fields list
        # to ask the extractor for. Personal loans want
        # income/employer/credit-score; business loans want
        # NOI/appraised-value/property-address. Asking for the wrong
        # set wastes tokens and produces "missing" fields the
        # borrower can never satisfy.
        loan_stmt = select(Loan).where(Loan.id == loan_id)
        loan = (await session.execute(loan_stmt)).scalar_one_or_none()
        loan_class_str: Literal["personal", "business"]
        if loan is None or loan.loan_class is None:
            # Defensive: treat missing/unknown class as business —
            # matches the legacy behaviour before the personal class
            # existed.
            loan_class_str = "business"
        else:
            loan_class_str = (
                "personal" if loan.loan_class.value == "personal" else "business"
            )
        required_fields = required_fields_for(loan_class_str)

        docs_stmt = select(Document).where(Document.loan_id == loan_id)
        documents = (await session.execute(docs_stmt)).scalars().all()

        if not documents:
            await record(
                session,
                loan_id=loan_id,
                actor=Actor.agent("intake"),
                action="intake_skipped",
                payload={"reason": "no_documents", "loan_class": loan_class_str},
            )
            return {
                **state,
                "loan_class": loan_class_str,
                "extracted_fields": {},
                "status": "needs_documents",
            }

        # Documents exist but at least one might be image-only PDF
        # with no extractable text yet. We continue — the per-doc
        # text check inside the loop handles those cases. The point
        # of the gate above is the "no packet at all" path.

        for doc in documents:
            # Portfolio scope: documents already carry text in `meta.text_content`.
            # A production build would run OCR (e.g. pdfplumber, Tesseract) first.
            doc_text = doc.meta.get("text_content", "")
            if not doc_text:
                logger.warning("doc_no_text", document_id=str(doc.id))
                continue

            result: ExtractionResult = await extract_fields(
                document_text=doc_text,
                fields_to_extract=required_fields,
                document_id=doc.id,
            )

            for field in result.fields:
                # Keep the highest-confidence answer if multiple docs answer the same field
                existing = extracted.get(field.field_name)
                if existing is None or field.confidence > existing.confidence:
                    extracted[field.field_name] = field

                # Persist extraction with routing decision
                routing = routing_decision(field)
                status = (
                    ExtractionStatus.ACCEPTED
                    if routing == "accepted"
                    else ExtractionStatus.QUEUED_FOR_REVIEW
                )
                extraction = Extraction(
                    document_id=doc.id,
                    field_name=field.field_name,
                    value=field.value,
                    confidence=field.confidence,
                    source_span=field.source_span.model_dump(),
                    status=status,
                )
                session.add(extraction)

                # Confidence gate (DESIGN §7.2): below-threshold extractions
                # don't just get a status — they enter a human review queue
                # via a `review_tasks` row. The reason names the delta so the
                # underwriter knows *why* it's in the queue.
                if status == ExtractionStatus.QUEUED_FOR_REVIEW:
                    await session.flush()  # populate extraction.id
                    threshold = threshold_for(field.field_name)
                    session.add(
                        ReviewTask(
                            extraction_id=extraction.id,
                            reason=(
                                f"Low confidence ({field.confidence:.2f}) "
                                f"below threshold ({threshold:.2f}) for "
                                f"{field.field_name}"
                            ),
                            status="open",
                        )
                    )

        await record(
            session,
            loan_id=loan_id,
            actor=Actor.agent("intake"),
            action="extraction_complete",
            payload={
                "n_extracted": len(extracted),
                "n_required": len(required_fields),
                "loan_class": loan_class_str,
            },
        )

    return {
        **state,
        "loan_class": loan_class_str,
        "extracted_fields": extracted,
    }


async def identify_missing(state: IntakeState) -> IntakeState:
    """Compute what's still needed from the borrower.

    Per-class required-fields list so a personal-loan borrower
    doesn't get chased for an appraisal (and vice-versa for a
    business borrower being asked for pay stubs).
    """
    extracted = state.get("extracted_fields", {})
    required = required_fields_for(state.get("loan_class"))
    # Treat low-confidence and missing the same way for messaging — both need follow-up
    missing = [
        f for f in required if f not in extracted or extracted[f].confidence < 0.7
    ]
    return {**state, "missing_fields": missing}


# Per-class document "asks" — the human-readable list the email
# composer suggests on each missing-fields email. The agent has the
# field list (e.g. ``annual_income``); these tell it which
# *documents* would supply those fields, in the language a borrower
# expects to see.
_DOC_ASKS_PERSONAL = (
    "Most recent two pay stubs (or 1099s if self-employed)",
    "Most recent two months of bank statements",
    "Most recent year of W-2s or full tax return (Form 1040)",
    "A government-issued photo ID",
)

_DOC_ASKS_BUSINESS = (
    "The most recent property appraisal",
    "A current rent roll (if income-producing)",
    "Trailing-12 operating statements",
    "Two years of business tax returns",
    "Personal financial statement for any guarantor",
)


def _doc_asks_for(loan_class: str | None) -> tuple[str, ...]:
    if loan_class == "personal":
        return _DOC_ASKS_PERSONAL
    return _DOC_ASKS_BUSINESS


async def draft_doc_request(state: IntakeState) -> IntakeState:
    """Draft an email to the borrower listing what's still needed.

    The prompt is class-aware: a personal-loan borrower gets a
    "send us pay stubs / W-2s / bank statements / ID" email; a
    business borrower gets "appraisal / rent roll / operating
    statements" copy. Same agent, same node — different vocabulary.
    """
    missing = state.get("missing_fields", [])
    if not missing:
        return {**state, "status": "complete", "draft_email": None}

    settings = get_settings()
    gateway = get_gateway()
    loan_class = state.get("loan_class") or "business"

    missing_str = "\n".join(f"- {f.replace('_', ' ').title()}" for f in missing)
    doc_asks = "\n".join(f"- {a}" for a in _doc_asks_for(loan_class))
    product_context = (
        "This is an UNSECURED PERSONAL LOAN application. The borrower is "
        "an individual. Documents we expect them to provide: pay stubs, "
        "tax returns, bank statements, a government ID. We do NOT need "
        "an appraisal or rent roll — there is no property securing this "
        "loan."
        if loan_class == "personal"
        else (
            "This is a COMMERCIAL / BUSINESS loan application. The "
            "borrower is typically an entity (LLC, corp) and the loan "
            "is secured by a property or business asset. Documents we "
            "expect: appraisal, rent roll (if income-producing), "
            "operating statements, business tax returns, personal "
            "financial statement for guarantors."
        )
    )

    # Pull real identifiers off the loan so the LLM can address the
    # borrower by name, reference the loan number, and sign off as the
    # actual loan officer. Without this the draft comes back with
    # template placeholders like [Loan Officer Name] / [Title] /
    # [Email] which then need scrubbing before send.
    loan_id = uuid.UUID(state["loan_id"])
    async with get_session() as session:
        from sqlalchemy.orm import selectinload

        loan_stmt = (
            select(Loan)
            .options(selectinload(Loan.parties), selectinload(Loan.owner))
            .where(Loan.id == loan_id)
        )
        loan = (await session.execute(loan_stmt)).scalar_one()
        borrower_party = loan.borrower
        owner_user = loan.owner

    borrower_name = (
        borrower_party.name if borrower_party else "the borrower"
    )
    loan_reference = loan.reference or "this application"
    settings = get_settings()
    officer_name = (owner_user.name if owner_user else "") or "Your loan officer"
    officer_email = (
        (owner_user.email if owner_user else None) or settings.resend_from_address
    )
    officer_title = "Loan Officer"
    institution = settings.resend_from_name or "Mkopo Lens"

    context_block = (
        "Real identifiers — use these verbatim, no placeholders:\n"
        f"- Borrower name (greeting / salutation): {borrower_name}\n"
        f"- Loan reference (mention in opening or subject): {loan_reference}\n"
        f"- Sign-off name: {officer_name}\n"
        f"- Sign-off title: {officer_title}\n"
        f"- Sign-off institution: {institution}\n"
        f"- Sign-off email: {officer_email}\n"
    )

    # Class-branched system prompt — managed through the /prompts UI.
    # The product_context + identifier blocks stay inline because they
    # carry mechanical, fact-bearing context the LLM needs from the
    # specific loan row, not editorial copy the underwriting team
    # would want to tune.
    from mkopo.services.prompts import get as get_prompt

    system = get_prompt(
        "intake.draft_doc_request.personal"
        if loan_class == "personal"
        else "intake.draft_doc_request.business"
    )
    user = (
        f"{product_context}\n\n"
        f"{context_block}\n"
        f"Items the borrower needs to send us (these are the underwriting "
        f"fields still missing — translate to the documents that would "
        f"contain them):\n{missing_str}\n\n"
        f"Suggested documents you can mention by name:\n{doc_asks}\n\n"
        f"FORMAT — this is an email body sent via Resend / SMTP and "
        f"rendered as plain text. **Do NOT use Markdown.** No asterisks, "
        f"no hashes, no numbered lists with `1.` syntax. Plain prose "
        f"paragraphs and (if you must enumerate) lettered bullets like "
        f'"a)" / "b)" or simple inline phrasing. Greet the borrower by '
        f"name. Reference the loan number once.\n"
        f"Tone: professional, friendly. Length: ≤120 words. "
        f"Subject line should be specific and include the loan reference."
    )
    drafted = await gateway.call_structured(
        model=settings.llm_default_model,
        system=system,
        user=user,
        schema=DraftedEmail,
    )
    return {
        **state,
        "draft_email": {"subject": drafted.subject, "body_text": drafted.body_text},
        "status": "awaiting_approval",
    }


def request_human_approval(state: IntakeState) -> IntakeState:
    """Pause execution until an underwriter approves or edits the email.

    The UI surfaces the draft, the user reviews/edits, and resumes with
    Command(resume={"action": "send"|"cancel", "subject": ..., "body_text": ...}).
    """
    draft = state.get("draft_email")
    if not draft:
        return {**state, "status": "complete"}

    response = interrupt(
        {
            "type": "approve_email",
            "loan_id": state["loan_id"],
            "draft": draft,
            "missing_fields": state.get("missing_fields", []),
        }
    )

    if response.get("action") == "cancel":
        return {**state, "status": "complete", "final_email": None}

    return {
        **state,
        "final_email": {
            "subject": response.get("subject", draft["subject"]),
            "body_text": response.get("body_text", draft["body_text"]),
        },
    }


async def send_email(state: IntakeState) -> IntakeState:
    """Send the approved email via Resend and log the message."""
    final = state.get("final_email")
    if not final:
        return {**state, "status": "complete"}

    loan_id = uuid.UUID(state["loan_id"])
    comms = get_comms()

    async with get_session() as session:
        loan_stmt = select(Loan).where(Loan.id == loan_id)
        loan = (await session.execute(loan_stmt)).scalar_one()
        borrower_email = loan.meta.get("borrower_email", "")
        if not borrower_email:
            logger.error("no_borrower_email", loan_id=str(loan_id))
            return {**state, "status": "failed"}

        send_result = await comms.send(
            OutboundEmail(
                to=borrower_email,
                subject=final["subject"],
                body_text=final["body_text"],
            )
        )

        await record(
            session,
            loan_id=loan_id,
            actor=Actor.agent("intake"),
            action="send_email",
            payload={
                "subject": final["subject"],
                "body_text": final["body_text"],
                "to": borrower_email,
                "resend_message_id": send_result.message_id,
                "drafted_by_agent": True,
            },
        )

    return {**state, "status": "complete"}


# --- Graph ---


@asynccontextmanager
async def build_intake_graph() -> AsyncIterator[Any]:
    """Yield a compiled intake agent graph with a Postgres checkpointer.

    `AsyncPostgresSaver.from_conn_string` is an async context manager that owns
    a database connection, so the graph is only valid inside the `async with`.
    Callers must scope each invocation:

        async with build_intake_graph() as graph:
            result = await graph.ainvoke(state, config=config)
    """
    settings = get_settings()

    builder = StateGraph(IntakeState)
    builder.add_node("extract", extract_all_documents)
    builder.add_node("identify_missing", identify_missing)
    builder.add_node("draft_request", draft_doc_request)
    builder.add_node("approve", request_human_approval)
    builder.add_node("send", send_email)

    builder.add_edge(START, "extract")

    def route_after_extract(state: IntakeState) -> str:
        """Pre-flight check. If extraction found no documents at all,
        the agent short-circuits — there's no point drafting an email
        asking the borrower to re-supply the entire packet when they
        haven't uploaded anything yet. The human action is to upload
        first."""
        if state.get("status") == "needs_documents":
            return END
        return "identify_missing"

    builder.add_conditional_edges(
        "extract",
        route_after_extract,
        {END: END, "identify_missing": "identify_missing"},
    )
    builder.add_edge("identify_missing", "draft_request")

    def route_after_draft(state: IntakeState) -> str:
        if state.get("status") == "complete":
            return END
        return "approve"

    builder.add_conditional_edges(
        "draft_request", route_after_draft, {END: END, "approve": "approve"}
    )
    builder.add_edge("approve", "send")
    builder.add_edge("send", END)

    # Postgres checkpointer — durable across restarts. Uses the libpq-format
    # DSN, not the SQLAlchemy one (psycopg rejects the `+psycopg` suffix).
    # The custom serializer allowlists ``mkopo.schemas`` symbols so
    # LangGraph stops warning about unregistered deserializations and
    # keeps working when the default flips to strict — see _serde.py.
    async with AsyncPostgresSaver.from_conn_string(
        settings.database_url_libpq, serde=make_serializer()
    ) as checkpointer:
        await checkpointer.setup()  # idempotent
        yield builder.compile(checkpointer=checkpointer)
