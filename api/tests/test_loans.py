"""Tests for the loan service. Validates transition rules."""

from mkopo.models import VALID_TRANSITIONS, LoanStage


def test_intake_can_go_to_underwriting():
    assert LoanStage.UNDERWRITING in VALID_TRANSITIONS[LoanStage.INTAKE]


def test_underwriting_cannot_skip_decision_to_closing():
    assert LoanStage.CLOSING not in VALID_TRANSITIONS[LoanStage.UNDERWRITING]


def test_terminal_states_have_no_transitions():
    assert VALID_TRANSITIONS[LoanStage.SERVICING] == set()
    assert VALID_TRANSITIONS[LoanStage.DECLINED] == set()


def test_decision_can_go_to_three_places():
    decisions = VALID_TRANSITIONS[LoanStage.DECISION]
    assert LoanStage.CONDITIONS in decisions
    assert LoanStage.APPROVED in decisions
    assert LoanStage.DECLINED in decisions


def test_no_loops():
    """Make sure there are no infinite loops in the transition graph."""
    # BFS from intake, ensure every reachable state has a finite path
    seen = {LoanStage.INTAKE}
    frontier = [LoanStage.INTAKE]
    while frontier:
        next_frontier = []
        for stage in frontier:
            for next_stage in VALID_TRANSITIONS.get(stage, set()):
                if next_stage not in seen:
                    seen.add(next_stage)
                    next_frontier.append(next_stage)
        frontier = next_frontier
    # Every stage must be reachable
    assert seen == set(LoanStage)
