"""Safety package — scenarios catalog + future safety helpers.

Lives next to the dashboard router for proximity to where it's
consumed. The scenarios catalog is a structured manifest of every
safety property the system pins, written for a human reviewer
(staff, auditor, prospective adopter) — each scenario describes a
specific way the system can be attacked and the defense that
catches it. Backed by tests in ``tests/test_safety_scenarios.py``.
"""

from mkopo.safety.scenarios import SCENARIOS, Scenario

__all__ = ["SCENARIOS", "Scenario"]
