"""Autonomous orchestrator — chains agents end-to-end when the loan
is in ``autonomous`` autonomy mode.

The orchestrator's contract is deliberately narrow:

- It only runs on the *server side*, in response to an event that
  signals the previous step has finished (intake completing without an
  interrupt, underwriting persisting its result, etc.).
- It NEVER bypasses an irreversible HITL gate. Sending the borrower
  email and transmitting a decision package are real-world commitments
  — even autonomous mode treats them as human-only.
- Every auto-action it takes flows through the same services the human
  UI uses (``transition_stage``, the same agent graphs, the same audit
  writer). There's no parallel pipeline; the orchestrator is just an
  alternative *caller*.

That last point is what keeps the audit log consistent. From a
compliance reader's perspective there's no difference between a
human-driven and orchestrator-driven loan beyond ``actor_type`` —
``system`` for orchestrator actions, ``user`` for human ones.

When a loan is in ``assisted`` mode the orchestrator is a no-op.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from mkopo.agents import build_decision_graph, build_underwriting_graph
from mkopo.db import get_session
from mkopo.models import AgentName, AutonomyLevel, Loan, LoanStage
from mkopo.services.audit import Actor, record
from mkopo.services.loans import (
    IllegalStageTransitionError,
    check_prerequisites,
    transition_stage,
)

logger = structlog.get_logger()


# The orchestrator's auto-transition reason — distinguishable from
# human-typed reasons in audit search.
AUTO_REASON = "Autonomous mode: prerequisites met, advancing."


async def maybe_chain_after_intake(loan_id: uuid.UUID, completed_with: str) -> None:
    """Called after the intake agent completes.

    Three completion states matter:

    - ``complete`` — packet was complete, no email needed. Auto-advance
      to underwriting and kick off the underwriting agent.
    - ``email_sent`` — the underwriter approved + sent the doc request.
      Stay in intake until the borrower replies; the inbound webhook
      will eventually trigger another check.
    - ``awaiting_approval`` — interrupt is pending; the orchestrator
      MUST NOT advance, because the email hasn't been sent and the
      packet is still incomplete. This is the HITL boundary.
    """
    if completed_with != "complete":
        return
    # 1. Advance the stage. _try_advance is a no-op if the loan is
    #    not autonomous, so this is safe to call unconditionally.
    advanced = await _try_advance(
        loan_id, LoanStage.UNDERWRITING, after=AgentName.INTAKE
    )
    if not advanced:
        return
    # 2. Kick off the underwriting agent. Without this step the
    #    stage moves but the agent never runs — the loan sits idle
    #    in underwriting, which is the exact opposite of "agentic".
    #    The agent's ``persist`` node will fire
    #    :func:`maybe_chain_after_underwriting` on completion, which
    #    in turn kicks the decision agent. That's the full chain.
    await _run_underwriting_agent(loan_id)


async def maybe_chain_after_underwriting(loan_id: uuid.UUID) -> None:
    """Called after the underwriting agent's persist node completes.

    If the loan is autonomous and the recommendation is
    ``proceed_to_decision``, advance to decision and run the decision
    agent. ``request_more_info`` and ``decline`` recommendations stop
    here — both require a human to either send a doc request or sign
    off on the decline.
    """
    async with get_session() as session:
        loan = (await session.execute(_loan_q(loan_id))).scalar_one()
        if loan.autonomy_level != AutonomyLevel.AUTONOMOUS:
            return
        # Inspect the latest underwriting_complete audit event to read
        # the recommendation — it's stamped onto the payload.
        from sqlalchemy import desc, select

        from mkopo.models import AuditEvent

        latest = (
            await session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.loan_id == loan_id,
                    AuditEvent.action == "underwriting_complete",
                )
                .order_by(desc(AuditEvent.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        recommendation = (latest.payload or {}).get("recommendation") if latest else None
        if recommendation != "proceed_to_decision":
            logger.info(
                "orchestrator_paused_after_underwriting",
                loan_id=str(loan_id),
                recommendation=recommendation,
                note="non-proceed recommendation requires human review",
            )
            return

    await _try_advance(loan_id, LoanStage.DECISION, after=AgentName.UNDERWRITING)
    await _run_decision_agent(loan_id)


async def maybe_chain_after_decision(loan_id: uuid.UUID) -> None:
    """Called after the decision agent's persist node completes.

    Three paths drop out of the decision agent — ``approve``,
    ``conditional``, ``decline``. None of them auto-transmit:

    - ``approve`` could auto-advance to ``approved`` then ``closing``,
      but the term sheet has to actually be sent to the borrower and
      countersigned. That's a human send.
    - ``conditional`` produces a conditions list that has to be
      negotiated with the borrower. Human territory.
    - ``decline`` produces an ECOA adverse-action letter that must be
      reviewed before transmission. Human territory.

    So this hook is a no-op today; it exists so we have one symmetric
    place to extend if the org wants "fast-track approval below
    $500K" or similar policy automation later.
    """
    logger.info(
        "orchestrator_paused_after_decision",
        loan_id=str(loan_id),
        note="all decision-path actions are human-only",
    )


# ---- internal helpers --------------------------------------------------


def _loan_q(loan_id: uuid.UUID):
    """Build a ``SELECT`` for a single loan — extracted so the imports
    in this module stay self-contained."""
    from sqlalchemy import select

    from mkopo.models import Loan

    return select(Loan).where(Loan.id == loan_id)


async def _try_advance(
    loan_id: uuid.UUID, to_stage: LoanStage, *, after: str
) -> bool:
    """Advance ``loan_id`` to ``to_stage`` if the loan is autonomous
    and prerequisites are met. No-op otherwise.

    Returns True if the transition happened.
    """
    async with get_session() as session:
        loan: Loan = (await session.execute(_loan_q(loan_id))).scalar_one()
        if loan.autonomy_level != AutonomyLevel.AUTONOMOUS:
            return False
        # Defensive: re-check prerequisites here even though
        # transition_stage will too. Lets us log the *reason* the
        # orchestrator didn't advance.
        msg = await check_prerequisites(session, loan, to_stage)
        if msg:
            logger.info(
                "orchestrator_prereq_failed",
                loan_id=str(loan_id),
                to_stage=to_stage.value,
                after=after,
                reason=msg,
            )
            return False
        try:
            await transition_stage(
                session,
                loan_id=loan_id,
                to_stage=to_stage,
                actor=Actor.system(),
                reason=AUTO_REASON,
            )
            await record(
                session,
                loan_id=loan_id,
                actor=Actor.system(),
                action="orchestrator_advanced",
                payload={"to_stage": to_stage.value, "after": after},
            )
        except IllegalStageTransitionError as e:
            logger.warning(
                "orchestrator_transition_blocked",
                loan_id=str(loan_id),
                to_stage=to_stage.value,
                error=str(e),
            )
            return False
        await session.commit()
    logger.info("orchestrator_advanced", loan_id=str(loan_id), to_stage=to_stage.value)
    return True


async def _finalize_failed_agent_run(
    agent_run_id: uuid.UUID | None,
) -> None:
    """Flip a ``running`` AgentRun to ``failed`` after the orchestrator
    caught an exception out of ``graph.ainvoke``.

    Mirrors what the SSE path does at
    ``agents/streaming._finalize_agent_run``. Without this, the
    orchestrator's auto-fired runs left the row stuck at ``running``
    forever on failure — observability would report perpetual
    "in-flight" runs that actually crashed minutes ago. Only updates
    if the row is still ``running`` (don't clobber a richer status
    the persist node may have written).
    """
    from sqlalchemy import update

    from mkopo.models import AgentRun

    if agent_run_id is None:
        return
    try:
        async with get_session() as session:
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == agent_run_id,
                    AgentRun.status == "running",
                )
                .values(status="failed")
            )
            await session.commit()
    except Exception:
        logger.exception(
            "orchestrator_agent_run_finalize_failed",
            agent_run_id=str(agent_run_id),
        )


async def _create_running_agent_run(
    loan_id: uuid.UUID, agent_name: str, thread_id: str
) -> uuid.UUID | None:
    """Insert a ``running`` AgentRun row + return its id.

    Mirrors what ``stream_graph_run`` does at the start of an
    SSE-driven invocation. The agent's ``persist`` node will later
    UPDATE this row to ``complete`` with the result payload, the
    same as the SSE path — but only if we create the row first AND
    stamp the id into the agent's state. Without this, the
    orchestrator's auto-fired runs leave no audit trail in
    ``agent_runs`` (the persist node's UPDATE silently affects
    zero rows because the id doesn't exist), and the loan-detail
    UI's "rehydrate latest result" query returns null.

    Returns ``None`` on insert failure — observability is
    best-effort and shouldn't block the run.
    """
    from mkopo.models import AgentRun

    agent_run_id = uuid.uuid4()
    try:
        async with get_session() as session:
            session.add(
                AgentRun(
                    id=agent_run_id,
                    loan_id=loan_id,
                    agent_name=agent_name,
                    thread_id=thread_id,
                    status="running",
                    payload={"triggered_by": "orchestrator"},
                )
            )
    except Exception:
        logger.exception(
            "orchestrator_agent_run_insert_failed",
            agent_name=agent_name,
            loan_id=str(loan_id),
        )
        return None
    return agent_run_id


async def _run_decision_agent(loan_id: uuid.UUID) -> None:
    """Run the decision agent end-to-end. Used by the orchestrator
    after auto-advancing into the decision stage.

    Creates an ``agent_runs`` row first + stamps the id into the
    agent's state — same contract the SSE layer follows so the
    persist node's UPDATE lands on a real row. Without this the
    orchestrator-driven run completes successfully but leaves no
    trace on the observability page.
    """
    thread_id = f"decision-{loan_id}"
    config = {"configurable": {"thread_id": thread_id}}
    agent_run_id = await _create_running_agent_run(loan_id, AgentName.DECISION, thread_id)
    state: dict[str, Any] = {"loan_id": str(loan_id)}
    if agent_run_id is not None:
        state["agent_run_id"] = str(agent_run_id)
    try:
        async with build_decision_graph() as graph:
            await graph.ainvoke(state, config=config)
    except Exception:
        logger.exception("orchestrator_decision_agent_failed", loan_id=str(loan_id))
        # Flip the row from 'running' → 'failed' so the observability
        # page doesn't show a phantom in-flight run. Mirrors the SSE
        # path's failure-finalization at agents/streaming.py:548.
        await _finalize_failed_agent_run(agent_run_id)


async def _run_underwriting_agent(loan_id: uuid.UUID) -> None:
    """Run the underwriting agent end-to-end + chain to decision.

    Used by the orchestrator after auto-advancing into the
    underwriting stage. The graph's ``persist`` node writes the
    AgentRun row + audit event the same way the SSE-driven path
    does — but ``ainvoke`` does NOT trigger the streaming layer's
    ``on_complete`` callback that normally fires
    :func:`maybe_chain_after_underwriting`. Without explicitly
    firing the next hook here, the chain would stop at underwriting
    even on autonomous loans. We fire it ourselves so the same
    contract holds regardless of which path drove the run.
    """
    thread_id = f"underwriting-{loan_id}"
    config = {"configurable": {"thread_id": thread_id}}
    agent_run_id = await _create_running_agent_run(
        loan_id, AgentName.UNDERWRITING, thread_id
    )
    state: dict[str, Any] = {"loan_id": str(loan_id)}
    if agent_run_id is not None:
        state["agent_run_id"] = str(agent_run_id)
    try:
        async with build_underwriting_graph() as graph:
            await graph.ainvoke(state, config=config)
    except Exception:
        logger.exception(
            "orchestrator_underwriting_agent_failed", loan_id=str(loan_id)
        )
        # Same finalization the decision path does — leaving the row
        # at 'running' confuses operators reading observability.
        await _finalize_failed_agent_run(agent_run_id)
        return
    # Continue the chain: if the recommendation is
    # ``proceed_to_decision``, this advances to decision and runs the
    # decision agent. ``maybe_chain_after_underwriting`` is itself
    # autonomy-gated, so it's safe to call here unconditionally.
    await maybe_chain_after_underwriting(loan_id)


# Public re-exports kept tidy; helpers stay underscored.
__all__ = [
    "maybe_chain_after_intake",
    "maybe_chain_after_underwriting",
    "maybe_chain_after_decision",
]
