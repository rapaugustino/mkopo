"""Tests for the personal-loan rule pack.

The commercial rules have their own coverage in ``test_rules.py``. This
file pins the new personal pack: DTI, LTI, FICO floor, employment
tenure, and the personal doc-completeness check. Same testing pattern —
build a ``RuleContext``, call one rule, assert on the outcome.
"""

from __future__ import annotations

from decimal import Decimal

from mkopo.rules.policy import (
    POLICY_MAX_DTI_PERSONAL,
    POLICY_MAX_LTI_PERSONAL,
    POLICY_MIN_FICO,
    REQUIRED_DOCS_PERSONAL,
    RuleContext,
    rule_credit_score_floor,
    rule_dti_under_cap,
    rule_employment_tenure,
    rule_lti_under_cap,
    rule_personal_doc_completeness,
    run_rules_for,
)


class TestDtiUnderCap:
    def test_below_cap_passes(self):
        # $5k/mo income, $1k/mo debt → DTI 20% (well under 40% cap).
        ctx = RuleContext(
            loan_amount=Decimal("20000"),
            annual_income=Decimal("60000"),
            monthly_debt_payments=Decimal("1000"),
        )
        result = rule_dti_under_cap(ctx)
        assert result.passed
        assert result.severity == "block"
        assert "DTI 20.0% within" in result.message

    def test_above_cap_blocks(self):
        # $5k/mo income, $2.5k/mo debt → DTI 50% (above 40% cap).
        ctx = RuleContext(
            loan_amount=Decimal("20000"),
            annual_income=Decimal("60000"),
            monthly_debt_payments=Decimal("2500"),
        )
        result = rule_dti_under_cap(ctx)
        assert not result.passed
        assert "exceeds policy cap" in result.message

    def test_missing_income_warns_not_blocks(self):
        ctx = RuleContext(
            loan_amount=Decimal("20000"),
            monthly_debt_payments=Decimal("1000"),
        )
        result = rule_dti_under_cap(ctx)
        assert not result.passed
        assert result.severity == "warn"

    def test_missing_debts_warns(self):
        ctx = RuleContext(
            loan_amount=Decimal("20000"),
            annual_income=Decimal("60000"),
        )
        result = rule_dti_under_cap(ctx)
        assert not result.passed
        assert result.severity == "warn"


class TestLtiUnderCap:
    def test_below_cap_passes(self):
        # $20k loan against $100k income → LTI 20% (under 40% ceiling).
        ctx = RuleContext(
            loan_amount=Decimal("20000"),
            annual_income=Decimal("100000"),
        )
        result = rule_lti_under_cap(ctx)
        assert result.passed
        assert result.severity == "warn"  # passing is warn-severity ack

    def test_above_cap_blocks(self):
        # $50k loan against $80k income → LTI 62.5% (above 40% ceiling).
        ctx = RuleContext(
            loan_amount=Decimal("50000"),
            annual_income=Decimal("80000"),
        )
        result = rule_lti_under_cap(ctx)
        assert not result.passed
        assert result.severity == "block"

    def test_missing_income_warns(self):
        ctx = RuleContext(loan_amount=Decimal("20000"))
        result = rule_lti_under_cap(ctx)
        assert not result.passed
        assert result.severity == "warn"


class TestCreditScoreFloor:
    def test_exceptional_passes(self):
        ctx = RuleContext(loan_amount=Decimal("20000"), credit_score=780)
        result = rule_credit_score_floor(ctx)
        assert result.passed
        assert result.details["band"] == "exceptional"

    def test_at_floor_passes(self):
        ctx = RuleContext(loan_amount=Decimal("20000"), credit_score=POLICY_MIN_FICO)
        assert rule_credit_score_floor(ctx).passed

    def test_below_floor_blocks(self):
        ctx = RuleContext(loan_amount=Decimal("20000"), credit_score=620)
        result = rule_credit_score_floor(ctx)
        assert not result.passed
        assert result.severity == "block"
        assert result.details["band"] == "subprime"

    def test_missing_score_warns(self):
        ctx = RuleContext(loan_amount=Decimal("20000"))
        result = rule_credit_score_floor(ctx)
        assert not result.passed
        assert result.severity == "warn"


class TestEmploymentTenure:
    def test_well_tenured_passes(self):
        ctx = RuleContext(loan_amount=Decimal("20000"), years_employment=Decimal("5.5"))
        assert rule_employment_tenure(ctx).passed

    def test_below_minimum_fails(self):
        ctx = RuleContext(loan_amount=Decimal("20000"), years_employment=Decimal("0.5"))
        result = rule_employment_tenure(ctx)
        assert not result.passed

    def test_missing_warns(self):
        ctx = RuleContext(loan_amount=Decimal("20000"))
        assert rule_employment_tenure(ctx).severity == "warn"


class TestPersonalDocCompleteness:
    def test_all_present_passes(self):
        ctx = RuleContext(
            loan_amount=Decimal("20000"),
            documents_present=set(REQUIRED_DOCS_PERSONAL),
        )
        assert rule_personal_doc_completeness(ctx).passed

    def test_missing_one_fails(self):
        partial = set(REQUIRED_DOCS_PERSONAL) - {"bank_statement"}
        ctx = RuleContext(loan_amount=Decimal("20000"), documents_present=partial)
        result = rule_personal_doc_completeness(ctx)
        assert not result.passed
        assert "bank_statement" in result.message


class TestRunRulesForSelector:
    """``run_rules_for`` should pick the right pack."""

    def test_personal_runs_personal_pack(self):
        ctx = RuleContext(
            loan_amount=Decimal("20000"),
            annual_income=Decimal("80000"),
            monthly_debt_payments=Decimal("1500"),
            credit_score=720,
            years_employment=Decimal("3.0"),
            documents_present=set(REQUIRED_DOCS_PERSONAL),
        )
        outcomes = run_rules_for("personal", ctx)
        rule_ids = {o.rule_id for o in outcomes}
        # All personal rules ran.
        assert "dti_under_cap" in rule_ids
        assert "lti_under_cap" in rule_ids
        assert "credit_score_floor" in rule_ids
        assert "employment_tenure" in rule_ids
        assert "personal_doc_completeness" in rule_ids
        # And no commercial rules leaked in.
        assert "dscr_above_floor" not in rule_ids
        assert "ltv_under_cap" not in rule_ids

    def test_business_runs_commercial_pack(self):
        ctx = RuleContext(loan_amount=Decimal("1000000"))
        outcomes = run_rules_for("business", ctx)
        rule_ids = {o.rule_id for o in outcomes}
        assert "dscr_above_floor" in rule_ids
        assert "ltv_under_cap" in rule_ids
        assert "dti_under_cap" not in rule_ids

    def test_unknown_class_defaults_to_commercial(self):
        ctx = RuleContext(loan_amount=Decimal("1000000"))
        outcomes = run_rules_for("space-station-mortgage", ctx)
        rule_ids = {o.rule_id for o in outcomes}
        # Conservative fallback — commercial pack carries the blocking
        # thresholds.
        assert "dscr_above_floor" in rule_ids


class TestThresholdsAreRealisticDefaults:
    """Smoke-test the constants — protects against typos that would
    completely change policy without anyone noticing in code review."""

    def test_dti_cap_is_in_industry_range(self):
        assert Decimal("0.30") <= POLICY_MAX_DTI_PERSONAL <= Decimal("0.50")

    def test_lti_cap_is_in_industry_range(self):
        assert Decimal("0.20") <= POLICY_MAX_LTI_PERSONAL <= Decimal("0.60")

    def test_min_fico_is_subprime_boundary(self):
        # 660 is the conventional break between subprime and "fair".
        assert 600 <= POLICY_MIN_FICO <= 700
