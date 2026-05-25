"""Stage-based locks on mutating operations.

The state machine (``VALID_TRANSITIONS`` in :mod:`mkopo.models.loan`)
prevents *stage* changes that don't make sense — e.g. a serviced loan
can never go back to underwriting. But the state machine doesn't
gate the *operations on a loan in a stage*. Without this module:

- The decision agent could be re-run on a loan that's already
  ``approved``, rewriting its conditions and writing a new
  ``decision_complete`` audit event that contradicts the borrower-
  facing verdict.
- New documents could be uploaded onto a ``servicing`` loan, which
  would change the materials hash retroactively.
- An underwriter could "Extract documents" again on a closed deal,
  burning tokens and confusing the audit timeline.

This module centralizes the question "is this operation legal at
this stage?" so every entry point answers it the same way. Three
levels of lock, in increasing severity:

- **Agent locks** — apply once the loan is in ``conditions`` or any
  later stage. Past the decision-drafting window, the agents that
  produced the decision-feeding inputs (intake, underwriting,
  decision) must not run again. There's no ``VALID_TRANSITIONS``
  path back to decision-drafting, so there's no legitimate use case
  for re-running.

- **Document locks** — apply once the loan is in ``approved`` or
  later. ``conditions`` stays unlocked because a borrower satisfying
  outstanding conditions ("send updated proof of income") needs to
  be able to upload.

- **Terminal locks** — ``servicing | declined | withdrawn``. Loan is
  read-only except for notes (notes are append-only by design) and
  owner reassignment (banks legitimately move serviced loans between
  servicing reps).

Every gated entry point calls :func:`raise_if_locked_for_*` early
and returns ``HTTP 409 Conflict`` with a friendly reason if the
operation isn't legal. The frontend reads the same predicates via
:func:`loan_lock_status` to hide the mutation buttons before the
user even clicks.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status

from mkopo.models import LoanStage

# Agents are locked once the loan has moved past decision-drafting.
# ``decision`` itself stays unlocked because that's the iteration
# stage — underwriters refine the result before transitioning out.
_AGENT_LOCKED_STAGES: frozenset[LoanStage] = frozenset(
    {
        LoanStage.CONDITIONS,
        LoanStage.APPROVED,
        LoanStage.CLOSING,
        LoanStage.SERVICING,
        LoanStage.DECLINED,
        LoanStage.WITHDRAWN,
    }
)

# Document uploads stay open in ``conditions`` so the borrower can
# satisfy outstanding requirements (updated paystub, missing tax
# return). Anything past ``conditions`` rejects new uploads — the
# materials that fed the decision are frozen at that point.
_DOC_UPLOAD_LOCKED_STAGES: frozenset[LoanStage] = frozenset(
    {
        LoanStage.APPROVED,
        LoanStage.CLOSING,
        LoanStage.SERVICING,
        LoanStage.DECLINED,
        LoanStage.WITHDRAWN,
    }
)

# Terminal — the loan is done. Same set as
# ``VALID_TRANSITIONS[stage] == set()``; centralised here for the
# UI's "is this loan still active" check.
_TERMINAL_STAGES: frozenset[LoanStage] = frozenset(
    {LoanStage.SERVICING, LoanStage.DECLINED, LoanStage.WITHDRAWN}
)


class LoanLockedError(Exception):
    """Raised when a stage-gated operation is attempted on a locked loan.

    The message is meant to be shown to the user verbatim — say
    *what* is locked and *why*, not just "operation not allowed".
    Surfaced as HTTP 409 by ``raise_if_locked_*`` rather than 403
    because the auth check passed; the loan's state is what's
    refusing.
    """


def is_terminal(stage: LoanStage) -> bool:
    """True if the loan is in a terminal stage (no further work)."""
    return stage in _TERMINAL_STAGES


def is_locked_for_agent(stage: LoanStage) -> bool:
    """True if mutating agent runs (intake / underwriting / decision)
    are disallowed at this stage. Replay against checkpoints is a
    separate concern — replay is a debugging affordance and should
    be gated by RBAC rather than by stage."""
    return stage in _AGENT_LOCKED_STAGES


def is_locked_for_documents(stage: LoanStage) -> bool:
    """True if new document uploads are disallowed. Note that
    ``conditions`` stays unlocked even though it's past the decision
    — borrowers may need to upload condition responses there."""
    return stage in _DOC_UPLOAD_LOCKED_STAGES


@dataclass(frozen=True)
class LoanLockStatus:
    """Frontend-facing lock state. Returned from a read endpoint so
    the UI can hide mutation actions before the user clicks.

    The strings here are deliberately friendly — they show up in
    banners and tooltips, not in audit logs.
    """

    stage: str
    is_terminal: bool
    agents_locked: bool
    documents_locked: bool
    headline: str | None  # Banner copy, or ``None`` if no lock applies
    detail: str | None  # Sub-copy explaining what the user can still do


def loan_lock_status(stage: LoanStage) -> LoanLockStatus:
    """Compute a UI-friendly snapshot of which operations are locked.

    Single read so the loan-detail page can render one banner +
    several disabled-button states from one query, without sprinkling
    stage checks across components.
    """
    agents_locked = is_locked_for_agent(stage)
    documents_locked = is_locked_for_documents(stage)
    terminal = is_terminal(stage)

    headline: str | None = None
    detail: str | None = None
    if terminal:
        headline = "Loan is finalized."
        if stage == LoanStage.SERVICING:
            detail = (
                "This loan has moved to servicing. The case file is read-only "
                "for underwriting purposes — only notes, owner reassignment, "
                "and audit views remain available."
            )
        elif stage == LoanStage.DECLINED:
            detail = (
                "This application was declined. The decision and adverse-"
                "action letter are final; agents and uploads are locked."
            )
        else:  # WITHDRAWN
            detail = (
                "The borrower withdrew this application. Agents and uploads "
                "are locked; the audit trail remains read-only."
            )
    elif documents_locked:
        # Approved / closing — past conditions, pre-servicing.
        headline = "Loan is in funding."
        detail = (
            "The credit decision is final and documents are locked. "
            "Agents are no longer runnable; transition to servicing once "
            "the loan funds."
        )
    elif agents_locked:
        # CONDITIONS — borrower can still upload, but agents are off.
        headline = "Decision finalized — clearing conditions."
        detail = (
            "The credit decision is final. New documents can still be "
            "uploaded to satisfy outstanding conditions, but the agents "
            "won't re-run."
        )

    return LoanLockStatus(
        stage=stage.value,
        is_terminal=terminal,
        agents_locked=agents_locked,
        documents_locked=documents_locked,
        headline=headline,
        detail=detail,
    )


def raise_if_locked_for_agent(stage: LoanStage, agent_name: str) -> None:
    """Refuse to run an agent on a locked loan with HTTP 409.

    Called by every ``POST /loans/{id}/agents/.../run`` endpoint
    after the loan lookup but before the streaming response starts —
    a 409 here is cheaper and more honest than a stream that emits
    one event and dies.
    """
    if not is_locked_for_agent(stage):
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Cannot run the {agent_name} agent — loan is in stage "
            f"{stage.value!r}. Agents are locked past the decision "
            "to keep the audit trail consistent with the verdict the "
            "borrower has already seen."
        ),
    )


def raise_if_locked_for_documents(stage: LoanStage) -> None:
    """Refuse to accept a document upload on a locked loan with HTTP 409.

    ``conditions`` stays open so borrowers can satisfy outstanding
    requirements. Anything past that — approved, closing, servicing,
    declined, withdrawn — rejects.
    """
    if not is_locked_for_documents(stage):
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Cannot upload documents — loan is in stage {stage.value!r}. "
            "Materials are frozen past approval so the decision audit "
            "trail can't be retroactively changed."
        ),
    )
