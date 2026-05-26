"""Shared helpers for the tool registries.

``borrower.py`` and ``staff.py`` both need the same two primitives:

- Resolve the target loan + verify the caller is allowed to touch it.
- Write a ``tool_invoked`` audit event when the agent calls a tool.

Both pieces lived in each catalog file as private helpers
(``_resolve_loan`` / ``_load_loan``, ``_audit_tool_call``) — the
shape was identical, only the error strings + the audit-event
``actor`` differed. This module is the de-duplicated form.

Keeping the helpers in their own module also gives a single place to
document the contract: every tool that touches a loan goes through
:func:`resolve_loan` (which means the ownership / soft-delete checks
can never be skipped by accident), and every tool invocation writes
one audit event via :func:`audit_tool_call`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from mkopo.agents.tools import ToolContext, ToolError
from mkopo.models import Loan, LoanParty, Party, PartyRole
from mkopo.services.audit import Actor, record


async def resolve_loan(
    ctx: ToolContext,
    loan_id: uuid.UUID | None = None,
    *,
    require_owner_email_match: bool = True,
    not_found_msg: str = "Loan not found.",
    no_scope_msg: str = (
        "No loan in scope. Either open the chat on a specific loan "
        "or pass a loan_id explicitly."
    ),
    not_owned_msg: str = "That loan isn't on your account.",
) -> Loan:
    """Resolve the loan a tool operates on.

    Two checks run regardless of caller:

    1. The loan exists and is not soft-deleted (``deleted_at IS NULL``).
    2. If ``require_owner_email_match`` is True (the borrower case),
       the borrower party on the loan matches ``ctx.user_email``.
       Staff callers pass ``require_owner_email_match=False`` because
       any staff user with the role can act on any loan.

    Either ``loan_id`` is explicitly supplied, or we fall back to
    ``ctx.loan_id`` (set when the chat was opened on a specific loan
    page). If neither is present we raise ``ToolError`` with
    ``no_scope_msg``.

    The error-string parameters exist because the borrower-facing
    message ("That application isn't on your account.") is materially
    different from the staff-facing message ("Loan not found.") even
    though the underlying check is the same — the borrower's UI is
    where these strings surface verbatim.
    """
    target = loan_id or ctx.loan_id
    if target is None:
        raise ToolError(no_scope_msg)

    loan = (
        await ctx.session.execute(
            select(Loan).where(Loan.id == target, Loan.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if loan is None:
        raise ToolError(not_found_msg)

    if require_owner_email_match:
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
            raise ToolError(not_owned_msg)

    return loan


async def audit_tool_call(
    ctx: ToolContext,
    *,
    tool_name: str,
    args: dict[str, Any],
    result_summary: str,
    actor: Actor,
    via: str,
    loan_id: uuid.UUID | None = None,
) -> None:
    """Write the standard ``tool_invoked`` audit event.

    Account-scoped tools (data export, erasure) that don't have a
    loan_id should not call this — they audit their own
    account-scoped events elsewhere.

    ``actor`` and ``via`` differ between the borrower surface
    (``Actor.borrower(email)`` + ``via="borrower_chat"``) and the
    staff surface (``Actor.user(user_id)`` + ``via="staff_chat"``).
    Everything else — the action name, the payload shape, the
    200-char truncation — is identical.
    """
    target = loan_id or ctx.loan_id
    if target is None:
        return
    await record(
        ctx.session,
        loan_id=target,
        actor=actor,
        action="tool_invoked",
        payload={
            "tool_name": tool_name,
            "args": args,
            "result_summary": result_summary[:200],
            "via": via,
        },
    )
