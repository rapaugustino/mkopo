"""Loan REST endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from mkopo.config import get_settings
from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models import (
    AgentName,
    AgentRun,
    AuditEvent,
    Condition,
    Extraction,
    Loan,
    LoanParty,
    Party,
    PartyRole,
    PartyType,
    User,
)
from mkopo.schemas import (
    AskRequest,
    AskResponse,
    AuditEventOut,
    ComparableLoanOut,
    ConditionOut,
    ExtractionOut,
    LoanCreate,
    LoanOut,
    NoteIn,
    StageTransitionIn,
)
from mkopo.services import loans as loan_service
from mkopo.services.audit import Actor, record
from mkopo.services.auth_service import mint_magic_link
from mkopo.services.comparables import comparable_loans
from mkopo.services.qa import answer_question
from mkopo.tools.comms import send_magic_link_email

router = APIRouter(prefix="/loans", tags=["loans"])


@router.get("", response_model=list[LoanOut])
async def list_loans(user: CurrentUserDep, db: DbSessionDep) -> list[Loan]:
    # ``deleted_at IS NULL`` keeps soft-deleted loans out of the
    # internal pipeline view — once a borrower requests erasure, the
    # loan disappears from operational surfaces immediately even
    # though the row sticks around for the regulatory retention
    # window. Cited by the partial index ``ix_loans_active``.
    result = await db.execute(
        select(Loan).where(Loan.deleted_at.is_(None)).order_by(Loan.created_at.desc()).limit(100)
    )
    return list(result.scalars().all())


@router.post("", response_model=LoanOut, status_code=status.HTTP_201_CREATED)
async def create_loan(
    payload: LoanCreate,
    user: CurrentUserDep,
    db: DbSessionDep,
    background_tasks: BackgroundTasks,
) -> Loan:
    """Staff-initiated loan creation.

    Side effects beyond the loan row itself:

      - **Borrower account**: ensures a ``users`` row exists for
        ``payload.borrower_email`` (creates one with no password if
        not). The account starts magic-link-only — the borrower can
        set a password later from /account.

      - **Invite email**: mints a 7-day ``loan_invite`` magic link
        and emails it (Resend background task). The link drops the
        borrower into the borrower portal already signed in,
        landing on /apply/[loan_id] where they can upload the
        required documents.

    Without this, a manually-created loan had no path for the
    borrower to ever discover their application — the loan officer
    would have had to send a one-off email out-of-band.
    """
    from mkopo.models import LoanClass

    # Validate the loan_class on the boundary — the inbound payload
    # is a plain string from JSON. Falls back to BUSINESS rather than
    # raising so a typo doesn't 500; the audit event still records
    # what the client sent.
    try:
        klass = LoanClass(payload.loan_class)
    except ValueError:
        klass = LoanClass.BUSINESS

    # Auto-assign an owner. Preferred: the staff member who's
    # creating the loan — they're the natural first reviewer. Falls
    # back to the first available staff user when the caller's
    # ``user_id`` isn't a real UUID (today this is the dev-bearer
    # path: user.user_id is the string ``"dev-user"``, not a real
    # PK). Without this the case file lands with "Owner: — Unassigned"
    # and downstream actions that key off the owner have no one to
    # attribute to.
    from mkopo.services.loans import pick_default_owner

    creator_uuid: uuid.UUID | None
    try:
        creator_uuid = uuid.UUID(user.user_id)
    except (TypeError, ValueError):
        creator_uuid = None
    default_owner_id = creator_uuid or await pick_default_owner(db)

    loan = Loan(
        loan_type=payload.loan_type,
        loan_class=klass,
        amount=payload.amount,
        owner_user_id=default_owner_id,
        meta={"borrower_email": payload.borrower_email},
    )
    db.add(loan)
    await db.flush()

    for p in payload.parties:
        party = Party(
            name=p.name,
            party_type=PartyType(p.party_type),
            email=p.email,
        )
        db.add(party)
        await db.flush()
        db.add(LoanParty(loan_id=loan.id, party_id=party.id, role=PartyRole(p.role)))

    # Ensure a borrower User row exists for this email so the invite
    # link consume path can sign them in. ``role='borrower'``,
    # password_hash=None (magic-link-only until they choose to set one).
    settings = get_settings()
    borrower_email = payload.borrower_email.lower().strip()
    borrower_user = (
        await db.execute(select(User).where(User.email == borrower_email))
    ).scalar_one_or_none()
    invite_minted = None
    if borrower_user is None:
        # Best-effort name from the borrower party payload — the
        # parties array typically carries the borrower's name first.
        borrower_name = (
            next(
                (p.name for p in payload.parties if p.role == "borrower"),
                None,
            )
            or borrower_email.split("@", 1)[0]
        )
        borrower_user = User(
            email=borrower_email,
            name=borrower_name,
            role="borrower",
            password_hash=None,
        )
        db.add(borrower_user)
        await db.flush()

    # Mint the invite. Long TTL because borrowers may not check
    # email for days; single-use semantics keep it safe enough.
    if borrower_user.deleted_at is None:
        invite_minted = await mint_magic_link(
            db,
            user=borrower_user,
            purpose="loan_invite",
            expires_in_seconds=settings.magic_link_loan_invite_ttl_seconds,
        )

    await record(
        db,
        loan_id=loan.id,
        actor=Actor.user(user.user_id),
        action="loan_created",
        payload={
            "amount": str(payload.amount),
            "loan_type": payload.loan_type.value,
            "invite_sent_to": borrower_email if invite_minted else None,
        },
    )
    await db.commit()
    await db.refresh(loan)

    # Dispatch the invite email AFTER commit so the borrower can't
    # click a working link before our row is durable. ``send_magic_link_email``
    # is non-raising; Resend hiccups become log entries, not 500s.
    if invite_minted:
        invite_url = (
            f"{settings.frontend_url}/auth/verify?"
            f"purpose=loan_invite&token={invite_minted.plain_token}&"
            f"loan_id={loan.id}"
        )
        background_tasks.add_task(
            send_magic_link_email,
            to=borrower_email,
            url=invite_url,
            purpose="loan_invite",
            expires_minutes=settings.magic_link_loan_invite_ttl_seconds // 60,
            recipient_name=borrower_user.name,
        )

    return loan


@router.get("/{loan_id}", response_model=LoanOut)
async def get_loan(loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep) -> Loan:
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    return loan


@router.post("/{loan_id}/transition", response_model=LoanOut)
async def transition(
    loan_id: uuid.UUID,
    payload: StageTransitionIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> Loan:
    try:
        loan = await loan_service.transition_stage(
            db,
            loan_id=loan_id,
            to_stage=payload.to_stage,
            actor=Actor.user(user.user_id),
            reason=payload.reason,
        )
        await db.commit()
        # Refresh ``updated_at`` — the server-side ``onupdate=func.now()``
        # default fired during the commit, which expires the attribute.
        # Without an explicit refresh, Pydantic's response_model
        # serialization hits a MissingGreenlet trying to lazy-load it
        # outside the async context and the client sees a misleading
        # 500 even though the transition itself committed cleanly.
        await db.refresh(loan, attribute_names=["updated_at"])
        return loan
    except loan_service.IllegalStageTransitionError as e:
        # 422 is the right semantic for "the request is well-formed but
        # the loan's current state forbids this action". 400 would have
        # said "your request is malformed" which it isn't.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e


class AutonomyIn(BaseModel):
    """PATCH payload for the autonomy toggle. The reason is required —
    it goes onto the audit event so committee reviewers can see *why*
    a particular deal was put on or off the autonomous track."""

    level: str  # "assisted" | "autonomous"
    reason: str


@router.patch("/{loan_id}/autonomy", response_model=LoanOut)
async def set_autonomy(
    loan_id: uuid.UUID,
    payload: AutonomyIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> Loan:
    """Switch a loan between ``assisted`` and ``autonomous`` modes.

    Autonomous mode lets the orchestrator chain agents end-to-end
    (intake → underwriting → decision) without prompting the
    underwriter at each step. Irreversible HITL gates (sending
    borrower email, sending the decision package) are still
    human-only — the toggle does not bypass them.

    The mode change writes an ``autonomy_changed`` audit event so the
    decision to fast-track (or slow down) a loan is itself part of the
    auditable record.
    """
    from mkopo.models import AutonomyLevel

    if payload.level not in ("assisted", "autonomous"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid autonomy level: {payload.level}",
        )
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    from_level = (
        loan.autonomy_level.value
        if hasattr(loan.autonomy_level, "value")
        else str(loan.autonomy_level)
    )
    loan.autonomy_level = AutonomyLevel(payload.level)
    await record(
        db,
        loan_id=loan_id,
        actor=Actor.user(user.user_id),
        action="autonomy_changed",
        payload={
            "from": from_level,
            "to": payload.level,
            "reason": payload.reason,
        },
    )
    await db.commit()
    # See transition() for why this refresh is required.
    await db.refresh(loan, attribute_names=["updated_at"])
    return loan


class StaffUserOut(BaseModel):
    """Minimal staff identity for the owner-reassignment dropdown.

    Same shape as :class:`OwnerOut` (which lives in
    ``mkopo.schemas``) — we keep it local to this router so the
    dropdown endpoint doesn't bring decision-side schema modules
    into scope for a UI list endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    initials: str
    role: str


@router.get("/staff/users", response_model=list[StaffUserOut])
async def list_staff_users(user: CurrentUserDep, db: DbSessionDep) -> list[User]:
    """List staff users (underwriters + admins) for the
    owner-reassignment dropdown on the loan detail page.

    Filters:
      - ``role in ('underwriter', 'admin')`` so borrowers don't
        appear in the list
      - ``deleted_at IS NULL`` so soft-deleted users are excluded

    Sort: alphabetical by name so the dropdown order is stable
    and locale-friendly. The list is small enough (<100 users in
    a typical lender) that we don't paginate.
    """
    rows = (
        (
            await db.execute(
                select(User)
                .where(
                    User.role.in_(("underwriter", "admin")),
                    User.deleted_at.is_(None),
                )
                .order_by(User.name.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


class OwnerAssignIn(BaseModel):
    """PATCH payload for reassigning a loan to a new staff owner.

    ``owner_id`` may be ``None`` — that explicitly *unassigns* the
    loan, returning it to the "Unassigned" bucket. The audit event
    records the transition either way."""

    owner_id: uuid.UUID | None
    reason: str = Field(min_length=1, max_length=500)


@router.patch("/{loan_id}/owner", response_model=LoanOut)
async def set_loan_owner(
    loan_id: uuid.UUID,
    payload: OwnerAssignIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> Loan:
    """Reassign (or unassign) a loan's staff owner.

    The reason lands on an ``owner_reassigned`` audit event so the
    case-file timeline shows who moved a deal and why — useful when
    the loan officer hands a sticky file to a workout specialist,
    or when an underwriter recuses themselves.

    The new owner must be a non-deleted staff user (underwriter or
    admin); we don't allow assigning a borrower as the loan's
    underwriter for the obvious reasons.
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    # Resolve + validate the new owner. ``None`` is allowed (unassign).
    new_owner: User | None = None
    if payload.owner_id is not None:
        new_owner = (
            await db.execute(select(User).where(User.id == payload.owner_id))
        ).scalar_one_or_none()
        if new_owner is None or new_owner.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff user not found")
        if new_owner.role not in ("underwriter", "admin"):
            # A borrower can't be a loan owner — keep the role boundary
            # tight; the dropdown only ever offers staff, so this would
            # only fire on a hand-crafted request.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Owner must be an underwriter or admin",
            )

    from_owner_id = str(loan.owner_user_id) if loan.owner_user_id else None
    from_owner_name = loan.owner.name if loan.owner else None

    loan.owner_user_id = new_owner.id if new_owner is not None else None

    await record(
        db,
        loan_id=loan.id,
        actor=Actor.user(user.user_id),
        action="owner_reassigned",
        payload={
            "from_owner_id": from_owner_id,
            "from_owner_name": from_owner_name,
            "to_owner_id": str(new_owner.id) if new_owner else None,
            "to_owner_name": new_owner.name if new_owner else None,
            "reason": payload.reason,
        },
    )
    await db.commit()
    await db.refresh(loan)
    return loan


@router.get("/{loan_id}/transitions")
async def list_allowed_transitions(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> dict[str, str | None]:
    """Per-stage readiness map: ``{<stage>: null}`` if ready, otherwise
    ``{<stage>: "<reason>"}``. The UI uses this to disable a transition
    button before the user clicks it, with the reason as the tooltip.

    Cheap to compute — the prerequisite checks are bounded `SELECT
    .. LIMIT 1` queries against indexed columns.
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    return await loan_service.allowed_transitions(db, loan)


class MaterialsStatus(BaseModel):
    """Materials-hash drift status for a loan.

    Powers the "your decision is stale" banner. Exposed as its own
    endpoint (rather than folded into ``GET /loans/{id}``) so the UI
    can poll it cheaply and refresh the banner without re-fetching
    the entire loan blob — the moment an extraction is overridden or
    a document re-uploaded, the next poll flips ``drifted`` true.

    ``decision_hash`` is ``None`` until the decision agent has run
    at least once. ``drifted`` is therefore ``False`` for pre-
    decision loans even if their materials are churning — there's
    nothing to drift from.
    """

    drifted: bool
    current_hash: str
    decision_hash: str | None


@router.get("/{loan_id}/materials/status", response_model=MaterialsStatus)
async def materials_status(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> MaterialsStatus:
    """Has anything fed-into-the-decision changed since the decision
    agent last ran?

    Used by the loan detail page to render a prominent drift banner.
    Cheap (a few indexed SELECTs + a sha256) so safe to poll.
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    from mkopo.services.materials_hash import materials_drift_detected

    drifted, current_hash, decision_hash = await materials_drift_detected(db, loan_id)
    return MaterialsStatus(drifted=drifted, current_hash=current_hash, decision_hash=decision_hash)


class CitationOut(BaseModel):
    """Resolved citation — backs the "hover an underwriting citation,
    see the source" interaction on the workspace.

    The underwriting summary cites extracted fields by name (e.g.
    ``citations: ["property_address"]``). This endpoint resolves a
    citation key back to the underlying extraction row, then surfaces
    the quote span and the document it came from. The frontend
    renders this in a side panel so the underwriter can verify the
    AI is reading from a real document rather than hallucinating.
    """

    field_name: str
    value: str
    confidence: float
    document_id: str
    document_filename: str
    page: int | None
    quote: str
    char_start: int | None
    char_end: int | None
    # Status of the extraction — "accepted", "overridden", "proposed".
    # The drawer renders a different chip per status so reviewers can
    # see whether a human has signed off on the value or not.
    status: str


@router.get("/{loan_id}/citations/{field_name}", response_model=CitationOut)
async def resolve_citation(
    loan_id: uuid.UUID,
    field_name: str,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> CitationOut:
    """Resolve an underwriting citation back to its source extraction.

    Picks the highest-confidence ACCEPTED extraction for the field;
    falls back to OVERRIDDEN, then PROPOSED. The chain mirrors the
    rules-engine's preference order: an accepted human-signed value
    wins over a raw LLM-extracted one. Returns 404 if no extraction
    of this field exists on the loan — citations should only refer
    to extractions that were produced during intake, so a 404 is a
    "stale citation" signal (probably means the prompt changed but
    the underwriting result wasn't re-run).
    """
    if not await loan_service.get_loan(db, loan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    # ``CASE WHEN status ...`` orders by a synthetic priority so the
    # status ranking is part of the ORDER BY rather than chained
    # queries. ACCEPTED (0) ranks above OVERRIDDEN (1) ranks above
    # PROPOSED (2); anything else (3) is a long-tail status we
    # don't expect but should still sort somewhere.
    from sqlalchemy import case

    from mkopo.models import Document, Extraction, ExtractionStatus

    priority = case(
        (Extraction.status == ExtractionStatus.ACCEPTED, 0),
        (Extraction.status == ExtractionStatus.OVERRIDDEN, 1),
        (Extraction.status == ExtractionStatus.PROPOSED, 2),
        else_=3,
    )

    row = (
        await db.execute(
            select(Extraction, Document.filename)
            .join(Document, Document.id == Extraction.document_id)
            .where(
                Document.loan_id == loan_id,
                Extraction.field_name == field_name,
            )
            .order_by(priority, Extraction.confidence.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No extraction for {field_name!r} on this loan",
        )
    extraction, document_filename = row
    span = extraction.source_span or {}
    return CitationOut(
        field_name=extraction.field_name,
        value=extraction.value,
        confidence=extraction.confidence,
        document_id=str(extraction.document_id),
        document_filename=document_filename,
        page=span.get("page"),
        quote=span.get("quote") or "",
        char_start=span.get("char_start"),
        char_end=span.get("char_end"),
        status=(
            extraction.status if isinstance(extraction.status, str) else extraction.status.value
        ),
    )


class LockStatusOut(BaseModel):
    """Per-stage lock state for the loan detail page banner.

    Mirrors ``services.loan_locks.LoanLockStatus`` — the dataclass
    isn't returned directly so the frontend gets a stable Pydantic
    schema rather than a raw asdict-dump.
    """

    stage: str
    is_terminal: bool
    agents_locked: bool
    documents_locked: bool
    headline: str | None
    detail: str | None


@router.get("/{loan_id}/locks", response_model=LockStatusOut)
async def get_lock_status(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> LockStatusOut:
    """Render-friendly lock snapshot. One read so the UI can hide
    mutation buttons and surface a "Loan is finalized" banner without
    re-implementing the stage-policy in TypeScript.

    Same authoritative source as the 409s returned by the agent / doc
    endpoints — :mod:`mkopo.services.loan_locks`. If they ever
    disagree, this endpoint is wrong (it's purely a view) and the
    server-side guard wins.
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    from mkopo.services.loan_locks import loan_lock_status

    snap = loan_lock_status(loan.stage)
    return LockStatusOut(
        stage=snap.stage,
        is_terminal=snap.is_terminal,
        agents_locked=snap.agents_locked,
        documents_locked=snap.documents_locked,
        headline=snap.headline,
        detail=snap.detail,
    )


@router.get("/{loan_id}/extractions", response_model=list[ExtractionOut])
async def list_extractions(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> list[Extraction]:
    from mkopo.models import Document

    stmt = (
        select(Extraction)
        .join(Document)
        .where(Document.loan_id == loan_id)
        .order_by(Extraction.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{loan_id}/rules")
async def get_rules_preview(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> dict[str, object]:
    """Deterministic rules + KPIs for a loan, no LLM call involved.

    The underwriting agent runs ``fetch_and_evaluate`` (rules + KPIs)
    before its LLM summary node. This endpoint exposes that first half
    directly so the workspace can render extractions, KPIs, and risk
    signals at all times — even before the agent has been kicked off.
    The agent's cited prose is the only thing that requires Run.

    Returns ``{kpis, risk_flags, extractions}`` mirroring the relevant
    subset of UnderwritingResult.
    """
    if not await loan_service.get_loan(db, loan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")

    from mkopo.services.rules_eval import evaluate

    result = await evaluate(db, loan_id)
    ctx = result.ctx
    ltv = float(ctx.loan_amount / ctx.appraised_value) if ctx.appraised_value else None
    dscr = (
        float(ctx.annual_noi / ctx.annual_debt_service)
        if ctx.annual_noi and ctx.annual_debt_service
        else None
    )
    debt_yield = float(ctx.annual_noi / ctx.loan_amount) if ctx.annual_noi else None
    doc_confidence = (
        sum(result.confidences.values()) / len(result.confidences) if result.confidences else None
    )
    return {
        "kpis": {
            "loan_amount": str(ctx.loan_amount),
            "ltv": ltv,
            "dscr": dscr,
            "debt_yield": debt_yield,
            "doc_confidence": doc_confidence,
            "property_type": ctx.property_type.value
            if hasattr(ctx.property_type, "value")
            else str(ctx.property_type),
        },
        "risk_flags": [f.model_dump(mode="json") for f in result.flags],
        "extractions": result.extractions,
    }


async def _latest_agent_result(
    db: DbSessionDep, loan_id: uuid.UUID, agent_name: str
) -> dict[str, object] | None:
    """Return ``payload.result_json`` from the most recent successful run
    of ``agent_name`` for this loan, or ``None`` if no such run exists.

    Used by the workspace + decision panel to rehydrate after a page
    reload — the streaming SSE delivers the result live, but until
    this endpoint existed the cache was lost when the user navigated
    away and back. ``result_json`` is the full Pydantic dump of the
    UnderwritingResult / DecisionResult written by the persist node.
    """
    row = (
        await db.execute(
            select(AgentRun.payload)
            .where(
                AgentRun.loan_id == loan_id,
                AgentRun.agent_name == agent_name,
                AgentRun.status == "complete",
            )
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return None
    # Older rows pre-date the result_json field. The query just
    # returns ``None`` for those; the UI re-runs the agent to get
    # a fresh payload (which now includes result_json).
    return (row or {}).get("result_json")


@router.get("/{loan_id}/underwriting/latest")
async def get_latest_underwriting_result(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> dict[str, object] | None:
    """Rehydrate the most recent underwriting agent result.

    Returns the full ``UnderwritingResult`` JSON (sections, KPIs,
    risk_flags, recommendation, rationale, generated_at,
    agent_run_id), or ``null`` if the agent has never completed on
    this loan. Powers the workspace's "result survives refresh"
    behavior so underwriters don't have to re-run the agent to see
    what it last said.
    """
    if not await loan_service.get_loan(db, loan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    return await _latest_agent_result(db, loan_id, AgentName.UNDERWRITING)


@router.get("/{loan_id}/decision/latest")
async def get_latest_decision_result(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> dict[str, object] | None:
    """Rehydrate the most recent decision agent result.

    Same pattern as ``get_latest_underwriting_result`` — returns the
    full ``DecisionResult`` JSON or ``null``.
    """
    if not await loan_service.get_loan(db, loan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    return await _latest_agent_result(db, loan_id, AgentName.DECISION)


@router.post("/{loan_id}/notes", response_model=AuditEventOut, status_code=status.HTTP_201_CREATED)
async def add_note(
    loan_id: uuid.UUID,
    payload: NoteIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> AuditEvent:
    """Write an internal note (or borrower-reply transcript) to the case file.

    The note becomes an audit_events row — same source-of-truth surface
    the timeline reads from, so it shows up there as a `user` event with
    a serif quote block.
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    event = await record(
        db,
        loan_id=loan_id,
        actor=Actor.user(user.user_id),
        action=payload.kind,
        payload={"body_text": payload.text},
    )
    await db.commit()
    return event


@router.get("/{loan_id}/audit", response_model=list[AuditEventOut])
async def list_audit_events(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.loan_id == loan_id)
        .order_by(AuditEvent.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{loan_id}/conditions", response_model=list[ConditionOut])
async def list_conditions(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep
) -> list[Condition]:
    """All conditions on this loan — drafted-by-agent + manually added — most recent first."""
    stmt = (
        select(Condition).where(Condition.loan_id == loan_id).order_by(Condition.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("/{loan_id}/ask", response_model=AskResponse)
async def ask(
    loan_id: uuid.UUID,
    payload: AskRequest,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> AskResponse:
    """RAG Q&A — embed the question, retrieve chunks + comparable loans, answer."""
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    return await answer_question(db, loan_id=loan_id, question=payload.question)


@router.get("/{loan_id}/comparables", response_model=list[ComparableLoanOut])
async def get_comparables(
    loan_id: uuid.UUID, user: CurrentUserDep, db: DbSessionDep, limit: int = 5
) -> list[ComparableLoanOut]:
    """Top-K most similar already-underwritten loans by cosine on summary embedding.

    Returns 200 with [] if the loan hasn't been underwritten yet (no embedding).
    """
    loan = await loan_service.get_loan(db, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Loan not found")
    comps = await comparable_loans(db, loan_id, limit=limit)
    return [
        ComparableLoanOut(
            loan_id=c.loan_id,
            reference=c.reference,
            borrower=c.borrower,
            loan_type=c.loan_type,
            amount=c.amount,
            risk_band=c.risk_band,
            similarity=c.similarity,
        )
        for c in comps
    ]
