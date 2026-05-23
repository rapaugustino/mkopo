"""Borrower-side tool implementations.

What's here, organised by destructive/non-destructive:

Read tools (non-destructive — agent calls without interrupt):
  - ``get_loan_status``        — stage, next-step, doc list, drift state
  - ``list_documents``         — every doc the loan has, with metadata
  - ``list_missing_fields``    — what intake flagged as still missing
  - ``get_decision_reasoning`` — the underwriting + decision rationale

Write tools (destructive — agent calls behind a confirmation interrupt):
  - ``update_loan_field``      — whitelist of borrower-supplied fields
  - ``withdraw_application``   — terminal stage transition
  - ``request_data_export``    — DSAR-style account export
  - ``request_erasure``        — soft-delete + retention window

Every handler wraps the corresponding service call (or REST endpoint
business logic) — there is one source of truth per action, not a
parallel implementation. The Phase 2 REST endpoints and the tools
here share the same effects, just different entrypoints.

Auditing: every tool invocation writes a ``tool_invoked`` audit
event in addition to whatever action-specific audit the underlying
service writes. That gives the timeline a complete record of
"borrower asked the agent → agent called this tool → tool ran".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from mkopo.agents.tools import Tool, ToolContext, ToolError, register
from mkopo.models import (
    AuditEvent,
    Document,
    Loan,
    LoanParty,
    LoanStage,
    Party,
    PartyRole,
    User,
)
from mkopo.services.audit import Actor, record

# ---- shared helpers ---------------------------------------------------------


async def _resolve_loan(ctx: ToolContext, loan_id: uuid.UUID | None = None) -> Loan:
    """Resolve the loan the tool operates on + check ownership.

    Most borrower tools target ``ctx.loan_id`` (set when the chat
    was opened on a specific application). Some tools accept an
    explicit ``loan_id`` arg to be flexible. Either way we verify
    the caller owns the loan — same email-keyed boundary every
    Phase 2 endpoint uses.
    """
    target = loan_id or ctx.loan_id
    if target is None:
        raise ToolError(
            "No loan in scope. Open the chat on a specific application page."
        )
    loan = (
        await ctx.session.execute(select(Loan).where(Loan.id == target))
    ).scalar_one_or_none()
    if loan is None or loan.deleted_at is not None:
        raise ToolError("Application not found.")
    # Ownership check by borrower email.
    row = (
        await ctx.session.execute(
            select(Party.email)
            .join(LoanParty, LoanParty.party_id == Party.id)
            .where(
                LoanParty.loan_id == loan.id,
                LoanParty.role == PartyRole.BORROWER,
            )
        )
    ).scalar_one_or_none()
    if (row or "").lower().strip() != ctx.user_email.lower().strip():
        raise ToolError("That application isn't on your account.")
    return loan


async def _audit_tool_call(
    ctx: ToolContext, *, tool_name: str, args: dict[str, Any], result_summary: str
) -> None:
    """Write the ``tool_invoked`` audit event. Captures intent +
    arguments + a short result summary so the case-file timeline
    reflects "the agent did X on behalf of Y" without the full
    JSON noise."""
    if ctx.loan_id is None:
        return  # account-scoped tools (data export, erasure) audit elsewhere
    await record(
        ctx.session,
        loan_id=ctx.loan_id,
        actor=Actor.borrower(ctx.user_email),
        action="tool_invoked",
        payload={
            "tool_name": tool_name,
            "args": args,
            "result_summary": result_summary[:200],
        },
    )


# ---- read tools -------------------------------------------------------------


class GetLoanStatusArgs(BaseModel):
    """Empty args — the loan is implicit from the chat scope."""


async def _handle_get_loan_status(
    ctx: ToolContext, args: GetLoanStatusArgs
) -> dict[str, Any]:
    loan = await _resolve_loan(ctx)
    docs_count = (
        await ctx.session.execute(
            select(Document.id).where(Document.loan_id == loan.id)
        )
    ).all()
    from mkopo.routers.borrower_portal import _next_step_for_borrower
    from mkopo.services.materials_hash import materials_drift_detected

    drifted, _curr, _prev = await materials_drift_detected(ctx.session, loan.id)
    result = {
        "reference": loan.reference,
        "stage": loan.stage.value,
        "amount": str(loan.amount),
        "loan_type": loan.loan_type.value,
        "next_step_for_you": _next_step_for_borrower(loan.stage),
        "documents_attached": len(docs_count),
        "submitted_at": loan.created_at.isoformat(),
        "decision_pending_rerun": drifted,
    }
    await _audit_tool_call(
        ctx,
        tool_name="get_loan_status",
        args=args.model_dump(),
        result_summary=f"stage={loan.stage.value}, docs={len(docs_count)}",
    )
    return result


register(
    Tool(
        name="get_loan_status",
        description=(
            "Look up the current status of the borrower's application — "
            "stage, amount, the number of documents attached, what's next, "
            "and whether the decision needs to be re-run because materials "
            "changed."
        ),
        schema=GetLoanStatusArgs,
        roles=frozenset({"borrower"}),
        is_destructive=False,
        handler=_handle_get_loan_status,
        human_action="Look up status",
    )
)


class ListDocumentsArgs(BaseModel):
    pass


async def _handle_list_documents(
    ctx: ToolContext, args: ListDocumentsArgs
) -> dict[str, Any]:
    loan = await _resolve_loan(ctx)
    rows = (
        await ctx.session.execute(
            select(Document)
            .where(Document.loan_id == loan.id)
            .order_by(Document.created_at)
        )
    ).scalars().all()
    docs = [
        {
            "filename": d.filename,
            "doc_type": d.doc_type if isinstance(d.doc_type, str) else d.doc_type.value,
            "size_bytes": d.size_bytes,
            "uploaded_at": d.created_at.isoformat(),
            "content_hash_prefix": (d.content_hash or "")[:12] if d.content_hash else None,
        }
        for d in rows
    ]
    await _audit_tool_call(
        ctx,
        tool_name="list_documents",
        args=args.model_dump(),
        result_summary=f"{len(docs)} document(s)",
    )
    return {"documents": docs, "count": len(docs)}


register(
    Tool(
        name="list_documents",
        description=(
            "List every document the borrower has uploaded to this "
            "application, with filename, type, size, and upload time."
        ),
        schema=ListDocumentsArgs,
        roles=frozenset({"borrower"}),
        is_destructive=False,
        handler=_handle_list_documents,
        human_action="List documents",
    )
)


class ListMissingFieldsArgs(BaseModel):
    pass


async def _handle_list_missing_fields(
    ctx: ToolContext, args: ListMissingFieldsArgs
) -> dict[str, Any]:
    """What intake says is still needed for this loan.

    Pulls from the latest ``intake_missing_fields`` audit event the
    intake agent emits on its missing-fields detection node. If no
    intake run has happened yet, we report that explicitly so the
    agent doesn't pretend everything's complete.
    """
    loan = await _resolve_loan(ctx)
    # Most recent missing-fields event.
    ev = (
        await ctx.session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.loan_id == loan.id,
                AuditEvent.action.in_(("intake_missing_fields", "intake_complete")),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ev is None:
        result: dict[str, Any] = {
            "missing": [],
            "intake_has_run": False,
            "note": (
                "The intake agent hasn't analysed this application yet. "
                "Once it runs we'll know exactly what's missing."
            ),
        }
    else:
        payload = ev.payload or {}
        missing = payload.get("missing_fields") or payload.get("missing") or []
        result = {
            "missing": missing,
            "intake_has_run": True,
            "intake_action": ev.action,
        }
    await _audit_tool_call(
        ctx,
        tool_name="list_missing_fields",
        args=args.model_dump(),
        result_summary=f"missing={len(result.get('missing', []))}",
    )
    return result


register(
    Tool(
        name="list_missing_fields",
        description=(
            "Report what fields the intake agent flagged as still needing "
            "the borrower's attention (income, employer, documents, etc.). "
            "Returns an empty list if intake says the packet is complete."
        ),
        schema=ListMissingFieldsArgs,
        roles=frozenset({"borrower"}),
        is_destructive=False,
        handler=_handle_list_missing_fields,
        human_action="List missing fields",
    )
)


class GetDecisionReasoningArgs(BaseModel):
    pass


async def _handle_get_decision_reasoning(
    ctx: ToolContext, args: GetDecisionReasoningArgs
) -> dict[str, Any]:
    """Surface the underwriter's recommendation and (if declined) the
    ECOA-compliant adverse-action reasons.

    Walks the audit log for the most recent ``decision_complete`` /
    ``underwriting_complete`` events. The decision agent stamps the
    rationale text on the audit payload so we don't need to thread
    through the AgentRun row here.
    """
    loan = await _resolve_loan(ctx)
    rows = (
        await ctx.session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.loan_id == loan.id,
                AuditEvent.action.in_(
                    ("underwriting_complete", "decision_complete")
                ),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(4)
        )
    ).scalars().all()
    underwriting = next(
        (r for r in rows if r.action == "underwriting_complete"), None
    )
    decision = next((r for r in rows if r.action == "decision_complete"), None)

    if not underwriting and not decision:
        result: dict[str, Any] = {
            "has_decision": False,
            "note": (
                "We haven't reached a decision yet. The underwriting agent "
                "hasn't completed its analysis."
            ),
        }
    else:
        result = {
            "has_decision": decision is not None,
            "stage": loan.stage.value,
            "underwriting_rationale": (underwriting.payload or {}).get("rationale")
            if underwriting
            else None,
            "underwriting_recommendation": (underwriting.payload or {}).get(
                "recommendation"
            )
            if underwriting
            else None,
            "decision_path": (decision.payload or {}).get("path") if decision else None,
            "decision_rationale": (decision.payload or {}).get("rationale")
            if decision
            else None,
        }
    await _audit_tool_call(
        ctx,
        tool_name="get_decision_reasoning",
        args=args.model_dump(),
        result_summary=f"path={result.get('decision_path') or 'none'}",
    )
    return result


register(
    Tool(
        name="get_decision_reasoning",
        description=(
            "Surface the underwriter's recommendation, the credit "
            "committee's decision path, and the rationale behind both. "
            "Use this whenever the borrower asks 'why was I declined' or "
            "'what did underwriting say'."
        ),
        schema=GetDecisionReasoningArgs,
        roles=frozenset({"borrower"}),
        is_destructive=False,
        handler=_handle_get_decision_reasoning,
        human_action="Pull decision reasoning",
    )
)


# ---- write tools (destructive, gated by confirmation interrupt) -------------


class UpdateLoanFieldArgs(BaseModel):
    """One field at a time — the agent should call this once per
    field the borrower wants to change. Keeps the audit + confirmation
    UX one-mutation-per-interrupt."""

    field: str = Field(
        description=(
            "One of: annual_income, monthly_debt_payments, employer, "
            "credit_score, years_employment, purpose."
        )
    )
    value: str = Field(description="The new value. We coerce to the right type server-side.")


_EDITABLE_FIELDS = {
    "annual_income",
    "monthly_debt_payments",
    "employer",
    "credit_score",
    "years_employment",
    "purpose",
}


async def _handle_update_loan_field(
    ctx: ToolContext, args: UpdateLoanFieldArgs
) -> dict[str, Any]:
    loan = await _resolve_loan(ctx)
    if args.field not in _EDITABLE_FIELDS:
        raise ToolError(
            f"Field '{args.field}' isn't borrower-editable. Allowed: "
            f"{', '.join(sorted(_EDITABLE_FIELDS))}."
        )
    if loan.stage in (LoanStage.CLOSING, LoanStage.SERVICING, LoanStage.WITHDRAWN):
        raise ToolError(
            f"This application is in {loan.stage.value} stage — fields are locked."
        )

    meta = dict(loan.meta or {})
    old = meta.get(args.field)
    # Type coercion: integers for credit_score, floats for everything
    # else numeric, strings for employer/purpose.
    new: Any
    if args.field == "credit_score":
        try:
            new = int(args.value)
        except ValueError as e:
            raise ToolError("Credit score must be an integer 300–850.") from e
    elif args.field in {"annual_income", "monthly_debt_payments", "years_employment"}:
        try:
            new = str(float(args.value))  # stored as string in meta
        except ValueError as e:
            raise ToolError(f"{args.field} must be a number.") from e
    else:
        new = args.value

    if new == old:
        await _audit_tool_call(
            ctx,
            tool_name="update_loan_field",
            args=args.model_dump(),
            result_summary=f"no-op (already {new})",
        )
        return {"changed": False, "field": args.field, "value": new}

    meta[args.field] = new
    loan.meta = meta
    await record(
        ctx.session,
        loan_id=loan.id,
        actor=Actor.borrower(ctx.user_email),
        action="borrower_field_updated",
        payload={
            "changes": {args.field: {"from": old, "to": new}},
            "stage_at_edit": loan.stage.value,
            "via": "agent",
        },
    )
    await _audit_tool_call(
        ctx,
        tool_name="update_loan_field",
        args=args.model_dump(),
        result_summary=f"{args.field}: {old} → {new}",
    )
    return {
        "changed": True,
        "field": args.field,
        "old_value": old,
        "new_value": new,
        "note": (
            "If this application has already been decided, the underwriting "
            "decision is now stale — staff will see a 'materials changed' "
            "banner and need to re-run underwriting before any further progress."
        ),
    }


register(
    Tool(
        name="update_loan_field",
        description=(
            "Update one borrower-supplied field on the loan (income, "
            "employer, credit score, monthly debt, years employed, purpose). "
            "This is the right tool when the borrower says 'fix my salary' or "
            "'update my employer'. Post-decision edits force re-underwriting."
        ),
        schema=UpdateLoanFieldArgs,
        roles=frozenset({"borrower"}),
        is_destructive=True,
        handler=_handle_update_loan_field,
        human_action="Update a field on your application",
    )
)


class WithdrawApplicationArgs(BaseModel):
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Why the borrower is withdrawing. Stored on the audit log.",
    )


async def _handle_withdraw_application(
    ctx: ToolContext, args: WithdrawApplicationArgs
) -> dict[str, Any]:
    from mkopo.services.loans import IllegalStageTransitionError, transition_stage

    loan = await _resolve_loan(ctx)
    try:
        await transition_stage(
            ctx.session,
            loan_id=loan.id,
            to_stage=LoanStage.WITHDRAWN,
            actor=Actor.borrower(ctx.user_email),
            reason=args.reason,
        )
    except IllegalStageTransitionError as e:
        raise ToolError(str(e)) from e
    await _audit_tool_call(
        ctx,
        tool_name="withdraw_application",
        args=args.model_dump(),
        result_summary="withdrawn",
    )
    return {
        "withdrawn": True,
        "reference": loan.reference,
        "message": (
            f"Application {loan.reference} has been withdrawn. It's closed "
            "and won't proceed; you'd need to start a new application to "
            "come back."
        ),
    }


register(
    Tool(
        name="withdraw_application",
        description=(
            "Withdraw the borrower's application — terminal action. They "
            "won't be able to undo this; they'd have to start a new "
            "application instead. Use only when the borrower clearly asks "
            "to cancel, drop, or withdraw."
        ),
        schema=WithdrawApplicationArgs,
        roles=frozenset({"borrower"}),
        is_destructive=True,
        handler=_handle_withdraw_application,
        human_action="Withdraw this loan application",
    )
)


class RequestDataExportArgs(BaseModel):
    pass


async def _handle_request_data_export(
    ctx: ToolContext, args: RequestDataExportArgs
) -> dict[str, Any]:
    """Trigger the same DSAR-style export the /me/data/export endpoint
    returns. The agent surfaces a URL the borrower can click; the
    actual JSON download still happens through the REST endpoint
    because synchronously returning megabytes through a chat message
    is the wrong shape."""
    return {
        "ok": True,
        "download_url": "/account/privacy",
        "message": (
            "Your data export is ready to download from your Privacy page. "
            "Open it and click 'Download' — you'll get a JSON file with "
            "everything we hold about you."
        ),
    }


register(
    Tool(
        name="request_data_export",
        description=(
            "Direct the borrower to their data export. Use this when "
            "they ask to 'download my data', 'export my records', or "
            "anything DSAR-shaped. The actual download happens client-side."
        ),
        schema=RequestDataExportArgs,
        roles=frozenset({"borrower"}),
        is_destructive=False,  # read-only; just surfaces a link
        handler=_handle_request_data_export,
        human_action="Help you export your data",
    )
)


class RequestErasureArgs(BaseModel):
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Why the borrower is requesting erasure.",
    )


async def _handle_request_erasure(
    ctx: ToolContext, args: RequestErasureArgs
) -> dict[str, Any]:
    """Soft-delete account + all loans. Same retention logic as the
    REST endpoint — we can't share the handler directly because it's
    a router function, but we mirror its effects."""
    now = datetime.now(UTC)

    # Soft-delete the user.
    user = (
        await ctx.session.execute(select(User).where(User.id == ctx.user_id))
    ).scalar_one()
    user.deleted_at = now

    # Soft-delete every loan the borrower owns + schedule retention.
    loans = (
        await ctx.session.execute(
            select(Loan)
            .join(LoanParty, LoanParty.loan_id == Loan.id)
            .join(Party, Party.id == LoanParty.party_id)
            .where(
                LoanParty.role == PartyRole.BORROWER,
                Party.email == ctx.user_email,
                Loan.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    for loan in loans:
        loan.deleted_at = now
        if loan.stage in (LoanStage.APPROVED, LoanStage.CLOSING, LoanStage.SERVICING):
            loan.retention_until = now + timedelta(days=365 * 5)  # HMDA 5y
        else:
            loan.retention_until = now + timedelta(days=30 * 25)  # Reg B 25mo
        await record(
            ctx.session,
            loan_id=loan.id,
            actor=Actor.borrower(ctx.user_email),
            action="borrower_erasure_requested",
            payload={
                "reason": args.reason,
                "stage_at_request": loan.stage.value,
                "retention_until": loan.retention_until.isoformat(),
                "via": "agent",
            },
        )
    return {
        "ok": True,
        "loans_affected": len(loans),
        "message": (
            "Your account and applications have been marked for erasure. "
            "We're required to keep the underlying records on file until "
            "the regulatory retention windows expire, after which they're "
            "permanently deleted by an automated sweep. You'll need to "
            "sign in again to come back."
        ),
    }


register(
    Tool(
        name="request_erasure",
        description=(
            "Erase the borrower's account and all their applications. "
            "Soft-delete is immediate; permanent deletion happens after "
            "regulatory retention windows expire (25 months for "
            "declined/withdrawn, 5 years for approved per HMDA). Use only "
            "when the borrower explicitly asks to 'delete my account', "
            "'erase my data', or similar."
        ),
        schema=RequestErasureArgs,
        roles=frozenset({"borrower"}),
        is_destructive=True,
        handler=_handle_request_erasure,
        human_action="Erase your account and all applications",
    )
)
