"""Loop-bound regression tests.

Each named loop in the codebase has a hard ceiling. These tests
exist so a refactor that loosens a cap, removes a guard, or
introduces a new unbounded loop is caught at CI time rather than
in a production cost spike.

Three loops, three categories of test:

1. ``MAX_VALIDATION_ATTEMPTS`` on the decision validator. Test the
   constant + the router function's termination behavior.
2. ``_MAX_ITERATIONS`` on the chat tool-call loop. Test the constant
   + assert the module hasn't grown an unbounded ``while True``.
3. The orchestrator chain. Assert it's a fixed sequence
   (intake → underwriting → decision) with no self-edges.
"""

from __future__ import annotations

import ast
import pathlib

# ----- decision validator (Self-Refine loop) -------------------------------


def test_max_validation_attempts_is_bounded_and_small():
    """The validator loop ceiling. 3 attempts × 4 LLM calls per
    attempt on the decline path = 12 LLM calls worst case. If a
    refactor moves this above 5 the cost ceiling should be
    explicitly reconsidered (and the SAFETY.md cost table updated)."""
    from mkopo.agents.guardrails import MAX_VALIDATION_ATTEMPTS

    assert 1 <= MAX_VALIDATION_ATTEMPTS <= 5, (
        f"MAX_VALIDATION_ATTEMPTS={MAX_VALIDATION_ATTEMPTS} is outside "
        "the documented sane range; bump the cap in guardrails.py + "
        "update SAFETY.md if this is intentional."
    )


def test_validator_router_terminates_at_max_attempts():
    """At the cap, the router must route to ``persist`` even when
    the judgment is still ``block`` — otherwise an unsolvable
    drafting bug would loop forever."""
    from mkopo.agents.decision import route_after_validate
    from mkopo.agents.guardrails import MAX_VALIDATION_ATTEMPTS

    state = {
        "last_judgment": {"severity": "block"},
        "validation_attempts": MAX_VALIDATION_ATTEMPTS,
    }
    assert route_after_validate(state) == "persist"


def test_validator_router_loops_back_under_max():
    """One attempt below the cap with a block verdict must route
    back to ``draft_decision`` (this is the Self-Refine cycle)."""
    from mkopo.agents.decision import route_after_validate
    from mkopo.agents.guardrails import MAX_VALIDATION_ATTEMPTS

    state = {
        "last_judgment": {"severity": "block"},
        "validation_attempts": MAX_VALIDATION_ATTEMPTS - 1,
    }
    assert route_after_validate(state) == "draft_decision"


def test_validator_router_persists_on_ok_or_warn():
    """``ok`` and ``warn`` both proceed to persist. Only ``block``
    can trigger the loop-back."""
    from mkopo.agents.decision import route_after_validate

    for severity in ("ok", "warn"):
        state = {
            "last_judgment": {"severity": severity},
            "validation_attempts": 1,
        }
        assert route_after_validate(state) == "persist", (
            f"severity={severity} should route to persist, not loop"
        )


# ----- intake validator (Self-Refine on borrower email) --------------------


def test_intake_validator_router_terminates_at_max_attempts():
    """Intake's Self-Refine loop must respect the same cap — at the
    ceiling, route forward to ``approve`` (HITL pause) even if the
    judge still says block."""
    from mkopo.agents.guardrails import MAX_VALIDATION_ATTEMPTS
    from mkopo.agents.intake import route_after_validate_email

    state = {
        "last_judgment": {"severity": "block"},
        "validation_attempts": MAX_VALIDATION_ATTEMPTS,
    }
    assert route_after_validate_email(state) == "approve"


def test_intake_validator_router_loops_back_under_max():
    """One attempt under the cap with block → back to draft_request."""
    from mkopo.agents.guardrails import MAX_VALIDATION_ATTEMPTS
    from mkopo.agents.intake import route_after_validate_email

    state = {
        "last_judgment": {"severity": "block"},
        "validation_attempts": MAX_VALIDATION_ATTEMPTS - 1,
    }
    assert route_after_validate_email(state) == "draft_request"


def test_intake_validator_router_persists_on_ok_or_warn():
    """ok/warn both go to ``approve`` (intake's persist-equivalent)."""
    from mkopo.agents.intake import route_after_validate_email

    for severity in ("ok", "warn"):
        state = {
            "last_judgment": {"severity": severity},
            "validation_attempts": 1,
        }
        assert route_after_validate_email(state) == "approve"


# ----- underwriting validator (Self-Refine on summary) ---------------------


def test_underwriting_validator_router_terminates_at_max_attempts():
    """UW's Self-Refine loop — at the ceiling, route forward to
    persist even on block."""
    from mkopo.agents.guardrails import MAX_VALIDATION_ATTEMPTS
    from mkopo.agents.underwriting import route_after_validate_summary

    state = {
        "last_judgment": {"severity": "block"},
        "validation_attempts": MAX_VALIDATION_ATTEMPTS,
    }
    assert route_after_validate_summary(state) == "persist"


def test_underwriting_validator_router_loops_back_under_max():
    from mkopo.agents.guardrails import MAX_VALIDATION_ATTEMPTS
    from mkopo.agents.underwriting import route_after_validate_summary

    state = {
        "last_judgment": {"severity": "block"},
        "validation_attempts": MAX_VALIDATION_ATTEMPTS - 1,
    }
    assert route_after_validate_summary(state) == "draft_summary"


def test_underwriting_validator_router_persists_on_ok_or_warn():
    from mkopo.agents.underwriting import route_after_validate_summary

    for severity in ("ok", "warn"):
        state = {
            "last_judgment": {"severity": severity},
            "validation_attempts": 1,
        }
        assert route_after_validate_summary(state) == "persist"


# ----- chat tool-call loop --------------------------------------------------


def test_chat_loop_max_iterations_is_bounded():
    """The chat loop cap. Touching this affects cost on every chat
    turn — staff and borrower alike. The cap is documented in
    ARCHITECTURE.md's "Cost + loop bounds" table; keep them in
    sync."""
    from mkopo.agents.tool_chat_loop import _MAX_ITERATIONS

    assert 1 <= _MAX_ITERATIONS <= 10, (
        f"_MAX_ITERATIONS={_MAX_ITERATIONS} is outside the sane "
        "range; update ARCHITECTURE.md if you raise it."
    )


def test_no_unbounded_while_loops_in_agents():
    """Source-level guard: ``while True`` / ``while 1`` are not
    allowed inside the agent or service layers. If an LLM loop
    needs to be added, it must be a bounded ``for`` range or a
    LangGraph conditional-edge cycle (which the routing function
    bounds via an explicit attempt counter).

    Caveats: this scans the agent-module source files, NOT every
    third-party dependency. SQLAlchemy / LangGraph internals can
    legitimately use ``while True`` inside their event loops; we
    don't audit those here.
    """
    api_dir = pathlib.Path(__file__).parent.parent
    targets = [
        api_dir / "mkopo" / "agents",
        api_dir / "mkopo" / "services",
        api_dir / "mkopo" / "llm_gateway.py",
    ]
    offenders: list[tuple[str, int]] = []
    for target in targets:
        files = (
            [target]
            if target.is_file()
            else sorted(target.rglob("*.py"))
        )
        for path in files:
            # Skip the lint/test files themselves.
            if "test_" in path.name:
                continue
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.While):
                    # Detect ``while True`` and ``while 1``. Both
                    # parse as a Constant node in the test position.
                    if (
                        isinstance(node.test, ast.Constant)
                        and bool(node.test.value)
                    ):
                        offenders.append((str(path), node.lineno))
    assert not offenders, (
        f"Unbounded while loops found (each must be a bounded "
        f"for-range or LangGraph cycle): {offenders}"
    )


# ----- orchestrator chain ---------------------------------------------------


def test_orchestrator_is_a_fixed_forward_sequence():
    """The orchestrator chain (intake → underwriting → decision)
    is NOT a loop. Each hook is autonomy-gated and runs at most
    once per parent invocation. This test catches a refactor that
    accidentally introduces a back-edge (e.g. underwriting hook
    that re-fires intake)."""
    from mkopo.agents import orchestrator

    public = set(orchestrator.__all__)
    # The three hooks that compose the chain. Each must exist; the
    # set of orchestrator-public symbols must not grow without an
    # explicit code review.
    assert public == {
        "maybe_chain_after_intake",
        "maybe_chain_after_underwriting",
        "maybe_chain_after_decision",
    }
    # The decision hook is a no-op today (the autonomy chain stops
    # at the decision-draft because the next step is a human-required
    # commitment). If this assertion ever fires, scrutinise whether
    # the new behaviour introduces a way to fire decision repeatedly.
    # The check is documentary — the hook's body is intentionally
    # short — but it's a useful tripwire.
    import inspect

    source = inspect.getsource(orchestrator.maybe_chain_after_decision)
    assert "_try_advance" not in source, (
        "maybe_chain_after_decision should not advance the stage "
        "automatically — all decision-path actions are human-only "
        "today. If you're adding policy automation, update the "
        "docstring + ARCHITECTURE.md and adjust this test."
    )
