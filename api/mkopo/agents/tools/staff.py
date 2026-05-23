"""Staff-side tools — the agent surface for underwriters, loan officers,
committee chairs, and admins.

Same registry pattern as ``borrower.py``; the ``roles`` field on each
Tool gates who can invoke. Internal staff roles for now:

  - ``"underwriter"``    — full read; can override extractions, advance
                            stages, run/re-run underwriting + decision
                            agents, draft notes to the borrower.
  - ``"admin"``           — everything an underwriter can do, plus
                            destructive maintenance actions (none
                            shipped here yet — placeholder).

We deliberately keep loan_officer + committee_chair off the role
filter for now because the JD's roles map cleanly to "underwriter"
in this single-tenant product. When that changes, just update the
``roles`` frozenset on each tool and the new roles inherit the
right subset automatically.

Tools shipped:

Read (non-destructive):
  - ``get_loan_overview``        — stage, KPIs, extraction count
  - ``list_recent_activity``     — last 10 audit events
  - ``search_loans``             — pipeline-wide full-text + filter
  - ``get_borrower_messages``    — internal_note + borrower_reply
                                    audit events for the loan

Write (destructive — interrupt-gated):
  - ``override_extraction``      — fix a wrong field value
  - ``advance_loan_stage``       — move a loan forward in the funnel
  - ``send_borrower_message``    — write a borrower-visible note
  - ``run_underwriting_agent``   — kick a fresh underwriting run
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select

from mkopo.agents.tools import Tool, ToolContext, ToolError, register
from mkopo.models import (
    AuditEvent,
    Document,
    Extraction,
    ExtractionStatus,
    Loan,
    LoanParty,
    LoanStage,
    Party,
    PartyRole,
)
from mkopo.services.audit import Actor, record

# Roles that can act on the staff surface. ``admin`` is a strict
# superset of ``underwriter`` for now; future loan_officer /
# committee_chair would have narrower subsets.
_STAFF_READ = frozenset({"underwriter", "admin"})
_STAFF_WRITE = frozenset({"underwriter", "admin"})


# ---- shared helpers --------------------------------------------------------


async def _load_loan(ctx: ToolContext, loan_id: uuid.UUID | None) -> Loan:
    """Resolve the target loan. Falls back to ``ctx.loan_id`` (set
    when the chat was opened on a specific loan); errors if neither
    the args nor the context have one."""
    target = loan_id or ctx.loan_id
    if target is None:
        raise ToolError(
            "No loan in scope. Either open the chat on a specific loan or "
            "pass a loan_id explicitly."
        )
    loan = (
        await ctx.session.execute(
            select(Loan).where(Loan.id == target, Loan.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if loan is None:
        raise ToolError("Loan not found.")
    return loan


async def _audit_tool_call(
    ctx: ToolContext,
    *,
    tool_name: str,
    args: dict[str, Any],
    result_summary: str,
    loan_id: uuid.UUID | None = None,
) -> None:
    """Same idea as the borrower-side audit helper. ``actor`` is a
    staff user, so we use ``Actor.user(user_id)`` rather than
    ``Actor.borrower(email)``."""
    target = loan_id or ctx.loan_id
    if target is None:
        return
    await record(
        ctx.session,
        loan_id=target,
        actor=Actor.user(str(ctx.user_id)),
        action="tool_invoked",
        payload={
            "tool_name": tool_name,
            "args": args,
            "result_summary": result_summary[:200],
            "via": "staff_chat",
        },
    )


# ---- read tools ------------------------------------------------------------


class LoanRefArgs(BaseModel):
    """Optional explicit loan_id. Omit to use the chat's current scope."""

    loan_id: uuid.UUID | None = None


async def _handle_get_loan_overview(
    ctx: ToolContext, args: LoanRefArgs
) -> dict[str, Any]:
    loan = await _load_loan(ctx, args.loan_id)
    # Borrower name (handy for cross-references in the conversation).
    borrower_email = (
        await ctx.session.execute(
            select(Party.name, Party.email)
            .join(LoanParty, LoanParty.party_id == Party.id)
            .where(
                LoanParty.loan_id == loan.id,
                LoanParty.role == PartyRole.BORROWER,
            )
        )
    ).first()
    # Document + extraction tallies — quick way to answer "how
    # complete is this packet?"
    doc_count = (
        await ctx.session.execute(
            select(func.count(Document.id)).where(Document.loan_id == loan.id)
        )
    ).scalar_one()
    accepted_count = (
        await ctx.session.execute(
            select(func.count(Extraction.id))
            .join(Document)
            .where(
                Document.loan_id == loan.id,
                Extraction.status.in_(
                    (ExtractionStatus.ACCEPTED, ExtractionStatus.OVERRIDDEN)
                ),
            )
        )
    ).scalar_one()
    result = {
        "reference": loan.reference,
        "stage": loan.stage.value,
        "amount": str(loan.amount),
        "loan_type": loan.loan_type.value,
        "loan_class": loan.loan_class.value
        if hasattr(loan.loan_class, "value")
        else str(loan.loan_class),
        "borrower_name": borrower_email[0] if borrower_email else None,
        "borrower_email": borrower_email[1] if borrower_email else None,
        "risk_band": loan.risk_band,
        "documents": doc_count,
        "extractions_accepted": accepted_count,
        "submitted_at": loan.created_at.isoformat(),
    }
    await _audit_tool_call(
        ctx,
        tool_name="get_loan_overview",
        args=args.model_dump(),
        result_summary=f"{loan.reference} · {loan.stage.value}",
        loan_id=loan.id,
    )
    return result


register(
    Tool(
        name="get_loan_overview",
        description=(
            "Pull the high-level summary of a loan — stage, amount, "
            "borrower name, risk band, document and extraction tallies. "
            "Use this when an underwriter asks 'tell me about this loan' "
            "or 'where is this in the pipeline'."
        ),
        schema=LoanRefArgs,
        roles=_STAFF_READ,
        is_destructive=False,
        handler=_handle_get_loan_overview,
        human_action="Pull loan overview",
    )
)


class RecentActivityArgs(BaseModel):
    loan_id: uuid.UUID | None = None
    limit: int = Field(default=10, ge=1, le=50)


async def _handle_list_recent_activity(
    ctx: ToolContext, args: RecentActivityArgs
) -> dict[str, Any]:
    loan = await _load_loan(ctx, args.loan_id)
    rows = (
        await ctx.session.execute(
            select(AuditEvent)
            .where(AuditEvent.loan_id == loan.id)
            .order_by(desc(AuditEvent.created_at))
            .limit(args.limit)
        )
    ).scalars().all()
    events = [
        {
            "action": e.action,
            "actor_type": e.actor_type.value
            if hasattr(e.actor_type, "value")
            else str(e.actor_type),
            "actor_id": e.actor_id,
            "at": e.created_at.isoformat(),
            # Trim payloads — the agent doesn't need the raw JSON of
            # every event, just a hint of what happened.
            "summary": _summarise_audit_payload(e.action, e.payload or {}),
        }
        for e in rows
    ]
    await _audit_tool_call(
        ctx,
        tool_name="list_recent_activity",
        args=args.model_dump(),
        result_summary=f"{len(events)} event(s)",
        loan_id=loan.id,
    )
    return {"events": events, "count": len(events)}


def _summarise_audit_payload(action: str, payload: dict[str, Any]) -> str:
    """One-line distillation of an audit payload for the LLM to read.

    The audit log is rich; the agent doesn't need the full JSON. We
    pull a short summary so the LLM can reason about the timeline
    without overwhelming its context window.
    """
    if action == "stage_transition":
        return f"{payload.get('from_stage')} → {payload.get('to_stage')}"
    if action == "underwriting_complete":
        return (
            f"recommendation={payload.get('recommendation')}, "
            f"risk_band={payload.get('risk_band')}"
        )
    if action == "decision_complete":
        return (
            f"path={payload.get('path')}, "
            f"confidence={payload.get('confidence')}"
        )
    if action == "tool_invoked":
        return f"tool={payload.get('tool_name')}"
    if action == "document_uploaded" or action == "borrower_document_uploaded":
        return f"filename={payload.get('filename')}"
    if action == "borrower_field_updated":
        changes = payload.get("changes") or {}
        return f"changes={list(changes.keys())}"
    # Generic: first short key/value pair.
    items = list(payload.items())[:2]
    return "; ".join(f"{k}={v}" for k, v in items)


register(
    Tool(
        name="list_recent_activity",
        description=(
            "Surface the last N audit events on this loan — stage "
            "transitions, agent runs, document uploads, borrower "
            "field updates, etc. Use this when an underwriter wants "
            "to know 'what's happened on this loan recently?'"
        ),
        schema=RecentActivityArgs,
        roles=_STAFF_READ,
        is_destructive=False,
        handler=_handle_list_recent_activity,
        human_action="List recent activity",
    )
)


class SearchLoansArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Free-text search across loan reference, borrower name, "
            "borrower email, and purpose."
        ),
    )
    stage: str | None = Field(
        default=None,
        description=(
            "Optional filter — one of "
            "intake/underwriting/decision/conditions/closing/"
            "approved/servicing/declined/withdrawn."
        ),
    )
    limit: int = Field(default=10, ge=1, le=50)


async def _handle_search_loans(
    ctx: ToolContext, args: SearchLoansArgs
) -> dict[str, Any]:
    """Pipeline-wide search. Read-only; respects soft-delete."""
    q = args.query.strip()
    stmt = (
        select(Loan, Party.name, Party.email)
        .join(LoanParty, LoanParty.loan_id == Loan.id, isouter=True)
        .join(Party, Party.id == LoanParty.party_id, isouter=True)
        .where(Loan.deleted_at.is_(None))
        .where(
            or_(
                Loan.reference.ilike(f"%{q}%"),
                Party.name.ilike(f"%{q}%"),
                Party.email.ilike(f"%{q}%"),
            )
        )
        .limit(args.limit)
    )
    if args.stage:
        try:
            stmt = stmt.where(Loan.stage == LoanStage(args.stage))
        except ValueError as e:
            raise ToolError(
                f"Unknown stage '{args.stage}'. Valid: "
                f"{', '.join(s.value for s in LoanStage)}."
            ) from e
    rows = (await ctx.session.execute(stmt)).all()
    # Dedupe (same loan may match on multiple LoanParty rows).
    seen: set[uuid.UUID] = set()
    matches: list[dict[str, Any]] = []
    for loan, borrower_name, borrower_email in rows:
        if loan.id in seen:
            continue
        seen.add(loan.id)
        matches.append(
            {
                "loan_id": str(loan.id),
                "reference": loan.reference,
                "stage": loan.stage.value,
                "amount": str(loan.amount),
                "borrower_name": borrower_name,
                "borrower_email": borrower_email,
            }
        )
    await _audit_tool_call(
        ctx,
        tool_name="search_loans",
        args=args.model_dump(),
        result_summary=f"q={q!r} → {len(matches)} match(es)",
    )
    return {"matches": matches, "count": len(matches)}


register(
    Tool(
        name="search_loans",
        description=(
            "Find loans across the pipeline by reference, borrower "
            "name, email, or stage. Use when an underwriter asks "
            "'pull up Maya Patel's application' or 'show me intake-"
            "stage loans for Acme'."
        ),
        schema=SearchLoansArgs,
        roles=_STAFF_READ,
        is_destructive=False,
        handler=_handle_search_loans,
        human_action="Search loans",
    )
)


class BorrowerMessagesArgs(BaseModel):
    loan_id: uuid.UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)


async def _handle_get_borrower_messages(
    ctx: ToolContext, args: BorrowerMessagesArgs
) -> dict[str, Any]:
    """Pull internal_note + borrower_reply + borrower_chat_message
    events — the human-readable thread between borrower and
    underwriter, plus the borrower's literal agent prompts."""
    loan = await _load_loan(ctx, args.loan_id)
    rows = (
        await ctx.session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.loan_id == loan.id,
                AuditEvent.action.in_(
                    (
                        "internal_note",
                        "borrower_reply",
                        "borrower_chat_message",
                    )
                ),
            )
            .order_by(desc(AuditEvent.created_at))
            .limit(args.limit)
        )
    ).scalars().all()
    messages = [
        {
            "action": r.action,
            "from": str(r.actor_type)
            + ":"
            + (r.actor_id or "?"),
            "text": (r.payload or {}).get("body")
            or (r.payload or {}).get("text")
            or "",
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    await _audit_tool_call(
        ctx,
        tool_name="get_borrower_messages",
        args=args.model_dump(),
        result_summary=f"{len(messages)} message(s)",
        loan_id=loan.id,
    )
    return {"messages": messages, "count": len(messages)}


register(
    Tool(
        name="get_borrower_messages",
        description=(
            "Surface the most recent borrower-facing messages on "
            "this loan — internal notes, replies sent to the "
            "borrower, and the borrower's own agent-chat prompts. "
            "Use when reviewing 'what has the borrower been saying'."
        ),
        schema=BorrowerMessagesArgs,
        roles=_STAFF_READ,
        is_destructive=False,
        handler=_handle_get_borrower_messages,
        human_action="Read borrower messages",
    )
)


# ---- write tools (destructive — confirmation interrupt) --------------------


class OverrideExtractionArgs(BaseModel):
    """Fix a wrong extracted value. ``field`` and ``new_value``
    are required; ``loan_id`` defaults to the chat scope."""

    field: str = Field(description="Extraction field_name (e.g. 'annual_noi').")
    new_value: str = Field(description="The corrected value.")
    loan_id: uuid.UUID | None = None
    rationale: str | None = Field(
        default=None,
        description="Optional reason — stored on the audit event.",
    )


async def _handle_override_extraction(
    ctx: ToolContext, args: OverrideExtractionArgs
) -> dict[str, Any]:
    loan = await _load_loan(ctx, args.loan_id)
    # Find the highest-confidence extraction for the named field
    # and override its value.
    ext = (
        await ctx.session.execute(
            select(Extraction)
            .join(Document)
            .where(
                Document.loan_id == loan.id,
                Extraction.field_name == args.field,
            )
            .order_by(desc(Extraction.confidence))
            .limit(1)
        )
    ).scalar_one_or_none()
    if ext is None:
        raise ToolError(
            f"No extraction found for field '{args.field}'. Confirm the "
            f"field name; staff can also accept a value via the review queue."
        )
    old_value = ext.value
    ext.value = args.new_value
    ext.status = ExtractionStatus.OVERRIDDEN
    await record(
        ctx.session,
        loan_id=loan.id,
        actor=Actor.user(str(ctx.user_id)),
        action="extraction_overridden",
        payload={
            "field": args.field,
            "from": old_value,
            "to": args.new_value,
            "rationale": args.rationale,
            "via": "staff_chat",
        },
    )
    await _audit_tool_call(
        ctx,
        tool_name="override_extraction",
        args=args.model_dump(),
        result_summary=f"{args.field}: {old_value} → {args.new_value}",
        loan_id=loan.id,
    )
    return {
        "overridden": True,
        "field": args.field,
        "from": old_value,
        "to": args.new_value,
        "note": (
            "The override drifts the materials hash. If a decision had "
            "already been made, the stage-transition guard will refuse "
            "forward progress until the decision agent is re-run."
        ),
    }


register(
    Tool(
        name="override_extraction",
        description=(
            "Correct an extracted field value when the LLM extractor "
            "got it wrong. Updates the latest accepted extraction "
            "for that field on this loan. Use when the underwriter "
            "says 'NOI is actually $284k, not $248k'."
        ),
        schema=OverrideExtractionArgs,
        roles=_STAFF_WRITE,
        is_destructive=True,
        handler=_handle_override_extraction,
        human_action="Override an extracted field",
    )
)


class AdvanceStageArgs(BaseModel):
    to_stage: str = Field(
        description=(
            "Target stage — must be a legal forward edge from the loan's "
            "current stage."
        )
    )
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Short reason for the transition; stored on the audit log.",
    )
    loan_id: uuid.UUID | None = None


async def _handle_advance_stage(
    ctx: ToolContext, args: AdvanceStageArgs
) -> dict[str, Any]:
    from mkopo.services.loans import (
        IllegalStageTransitionError,
        transition_stage,
    )

    loan = await _load_loan(ctx, args.loan_id)
    try:
        target = LoanStage(args.to_stage)
    except ValueError as e:
        raise ToolError(
            f"Unknown stage '{args.to_stage}'. Legal stages: "
            f"{', '.join(s.value for s in LoanStage)}."
        ) from e
    try:
        await transition_stage(
            ctx.session,
            loan_id=loan.id,
            to_stage=target,
            actor=Actor.user(str(ctx.user_id)),
            reason=args.reason,
        )
    except IllegalStageTransitionError as e:
        raise ToolError(str(e)) from e
    await _audit_tool_call(
        ctx,
        tool_name="advance_loan_stage",
        args=args.model_dump(),
        result_summary=f"→ {target.value}",
        loan_id=loan.id,
    )
    return {
        "transitioned": True,
        "from": loan.stage.value,  # was, before the in-session update
        "to": target.value,
        "reference": loan.reference,
    }


register(
    Tool(
        name="advance_loan_stage",
        description=(
            "Move this loan to a different stage in the funnel. "
            "Routes through the canonical transition service so the "
            "stage-machine edge rules + prerequisite checks (including "
            "materials-drift detection) all apply. Use when the "
            "underwriter says 'advance this to decision' or 'send to "
            "closing'."
        ),
        schema=AdvanceStageArgs,
        roles=_STAFF_WRITE,
        is_destructive=True,
        handler=_handle_advance_stage,
        human_action="Advance the loan to a new stage",
    )
)


class SendBorrowerMessageArgs(BaseModel):
    body: str = Field(
        min_length=1,
        max_length=4000,
        description="Plain-text message the borrower will see on their /apply/[id] page.",
    )
    loan_id: uuid.UUID | None = None


async def _handle_send_borrower_message(
    ctx: ToolContext, args: SendBorrowerMessageArgs
) -> dict[str, Any]:
    loan = await _load_loan(ctx, args.loan_id)
    await record(
        ctx.session,
        loan_id=loan.id,
        actor=Actor.user(str(ctx.user_id)),
        action="borrower_reply",
        payload={"body": args.body, "via": "staff_chat"},
    )
    await _audit_tool_call(
        ctx,
        tool_name="send_borrower_message",
        args=args.model_dump(),
        result_summary=f"sent {len(args.body)} chars",
        loan_id=loan.id,
    )
    return {
        "sent": True,
        "reference": loan.reference,
        "note": (
            "Saved to the case-file timeline. The borrower will see it the "
            "next time they refresh their /apply/[id] page (no email goes "
            "out — communications are in-app)."
        ),
    }


register(
    Tool(
        name="send_borrower_message",
        description=(
            "Write a message visible to the borrower on this loan. "
            "Lands on the case-file timeline as a 'borrower_reply' "
            "audit event. Use when the underwriter dictates a "
            "borrower-facing note ('Please upload the most recent "
            "tax return when you have a moment')."
        ),
        schema=SendBorrowerMessageArgs,
        roles=_STAFF_WRITE,
        is_destructive=True,
        handler=_handle_send_borrower_message,
        human_action="Send a message to the borrower",
    )
)
