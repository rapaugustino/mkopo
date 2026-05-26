"""Tests for the rules engine. Demonstrates the testing pattern for deterministic code."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mkopo.rules.policy import (
    DSCR_FLOORS,
    LTV_CAPS,
    REQUIRED_DOCS,
    PropertyType,
    RuleContext,
    rule_appraisal_age,
    rule_debt_yield_above_floor,
    rule_doc_completeness,
    rule_dscr_above_floor,
    rule_ltv_under_cap,
    run_rules,
)

# Multifamily is the most permissive bucket — convenient default for tests
# that want a "happy-path" loan. Other tests pin a specific asset type.
MF = PropertyType.MULTIFAMILY


class TestLtvCap:
    def test_below_cap_passes(self):
        ctx = RuleContext(
            loan_amount=Decimal("680000"),
            appraised_value=Decimal("1000000"),
            property_type=MF,
        )
        result = rule_ltv_under_cap(ctx)
        assert result.passed
        assert result.severity == "block"

    def test_at_cap_passes(self):
        cap = LTV_CAPS[MF]
        appraised = Decimal("1000000")
        ctx = RuleContext(
            loan_amount=appraised * cap,
            appraised_value=appraised,
            property_type=MF,
        )
        assert rule_ltv_under_cap(ctx).passed

    def test_over_cap_fails(self):
        ctx = RuleContext(
            loan_amount=Decimal("800000"),
            appraised_value=Decimal("1000000"),
            property_type=MF,
        )
        assert not rule_ltv_under_cap(ctx).passed

    def test_asset_type_tightens_cap(self):
        # Hotel cap is 0.65; same loan that passes for multifamily (0.75)
        # should fail for hotel.
        ctx_mf = RuleContext(
            loan_amount=Decimal("700000"),
            appraised_value=Decimal("1000000"),
            property_type=MF,
        )
        ctx_hotel = RuleContext(
            loan_amount=Decimal("700000"),
            appraised_value=Decimal("1000000"),
            property_type=PropertyType.HOTEL,
        )
        assert rule_ltv_under_cap(ctx_mf).passed
        assert not rule_ltv_under_cap(ctx_hotel).passed

    def test_missing_appraised_value_warns(self):
        ctx = RuleContext(loan_amount=Decimal("100000"), property_type=MF)
        result = rule_ltv_under_cap(ctx)
        assert not result.passed
        assert result.severity == "warn"


class TestDscrFloor:
    def test_meets_floor(self):
        ctx = RuleContext(
            loan_amount=Decimal("1000000"),
            annual_noi=Decimal("180000"),
            annual_debt_service=Decimal("100000"),
            property_type=MF,
        )
        assert rule_dscr_above_floor(ctx).passed

    def test_below_floor(self):
        ctx = RuleContext(
            loan_amount=Decimal("1000000"),
            annual_noi=Decimal("100000"),
            annual_debt_service=Decimal("100000"),
            property_type=MF,
        )
        assert not rule_dscr_above_floor(ctx).passed

    def test_hotel_floor_tighter_than_multifamily(self):
        # NOI=160k, DS=100k → DSCR 1.60. Passes for multifamily (>=1.20)
        # but fails for hotel (>=1.45)... wait, 1.60 > 1.45 so it passes
        # there too. Use a value between the two floors.
        ctx_mf = RuleContext(
            loan_amount=Decimal("1000000"),
            annual_noi=Decimal("130000"),  # DSCR 1.30
            annual_debt_service=Decimal("100000"),
            property_type=MF,
        )
        ctx_hotel = RuleContext(
            loan_amount=Decimal("1000000"),
            annual_noi=Decimal("130000"),
            annual_debt_service=Decimal("100000"),
            property_type=PropertyType.HOTEL,
        )
        assert rule_dscr_above_floor(ctx_mf).passed
        assert not rule_dscr_above_floor(ctx_hotel).passed
        assert DSCR_FLOORS[PropertyType.HOTEL] > DSCR_FLOORS[MF]


class TestDebtYield:
    def test_clears_floor(self):
        # NOI 100k / loan 1M = 10% — clears multifamily 8% floor
        ctx = RuleContext(
            loan_amount=Decimal("1000000"),
            annual_noi=Decimal("100000"),
            property_type=MF,
        )
        assert rule_debt_yield_above_floor(ctx).passed

    def test_below_floor(self):
        # NOI 50k / loan 1M = 5% — below 8% floor
        ctx = RuleContext(
            loan_amount=Decimal("1000000"),
            annual_noi=Decimal("50000"),
            property_type=MF,
        )
        assert not rule_debt_yield_above_floor(ctx).passed

    def test_missing_noi_warns(self):
        ctx = RuleContext(loan_amount=Decimal("1000000"), property_type=MF)
        result = rule_debt_yield_above_floor(ctx)
        assert not result.passed
        assert result.severity == "warn"


class TestAppraisalAge:
    def test_recent_appraisal_passes(self):
        ctx = RuleContext(
            loan_amount=Decimal("1000000"),
            appraisal_date=datetime.now(UTC).date() - timedelta(days=30),
        )
        assert rule_appraisal_age(ctx).passed

    def test_old_appraisal_fails(self):
        ctx = RuleContext(
            loan_amount=Decimal("1000000"),
            appraisal_date=datetime.now(UTC).date() - timedelta(days=300),
        )
        assert not rule_appraisal_age(ctx).passed


class TestDocCompleteness:
    def test_all_docs_present(self):
        ctx = RuleContext(loan_amount=Decimal("1000000"), documents_present=set(REQUIRED_DOCS))
        assert rule_doc_completeness(ctx).passed

    def test_missing_doc_fails(self):
        ctx = RuleContext(
            loan_amount=Decimal("1000000"),
            documents_present=REQUIRED_DOCS - {"appraisal"},
        )
        result = rule_doc_completeness(ctx)
        assert not result.passed
        assert "appraisal" in result.details["missing"]


class TestPropertyTypeFromText:
    def test_multifamily_variants(self):
        assert PropertyType.from_text("12-unit multifamily") == PropertyType.MULTIFAMILY
        assert PropertyType.from_text("apartment building") == PropertyType.MULTIFAMILY
        assert PropertyType.from_text("24 unit complex") == PropertyType.MULTIFAMILY

    def test_office_retail_industrial(self):
        assert PropertyType.from_text("Class B office") == PropertyType.OFFICE
        assert PropertyType.from_text("retail strip center") == PropertyType.RETAIL
        assert PropertyType.from_text("industrial warehouse") == PropertyType.INDUSTRIAL

    def test_unknown_falls_back_to_other(self):
        assert PropertyType.from_text("") == PropertyType.OTHER
        assert PropertyType.from_text(None) == PropertyType.OTHER
        assert PropertyType.from_text("rocketship pad") == PropertyType.OTHER


def test_run_rules_returns_all_outcomes():
    ctx = RuleContext(
        loan_amount=Decimal("2400000"),
        appraised_value=Decimal("3529400"),
        appraisal_date=datetime.now(UTC).date() - timedelta(days=30),
        annual_noi=Decimal("284200"),
        annual_debt_service=Decimal("144000"),  # 6% IO of $2.4M
        guarantor_total_exposure=Decimal("4700000"),
        documents_present=set(REQUIRED_DOCS),
        property_type=MF,
    )
    outcomes = run_rules(ctx)
    # 6 rules: doc_completeness, ltv, dscr, debt_yield, appraisal_age, guarantor
    assert len(outcomes) == 6
    assert all(o.passed for o in outcomes), [
        (o.rule_id, o.passed, o.message) for o in outcomes if not o.passed
    ]


class TestRulePackRegistry:
    """Verifies the new pluggable-pack registry. Existing packs
    (``business``, ``personal``) are exercised everywhere else;
    these tests cover the registration mechanism itself."""

    def test_business_and_personal_packs_are_pre_registered(self):
        from mkopo.rules.policy import (
            DEFAULT_RULES,
            PERSONAL_RULES,
            RULE_PACKS,
        )

        assert RULE_PACKS["business"] is DEFAULT_RULES
        assert RULE_PACKS["personal"] is PERSONAL_RULES

    def test_register_rule_pack_then_run(self):
        """A newly-registered pack must be reachable from
        ``run_rules_for`` without any code change to ``policy.py``."""
        from mkopo.rules.policy import (
            register_rule_pack,
            run_rules_for,
        )

        sentinel: list = []

        def fake_rule(ctx):
            from mkopo.rules.policy import RuleOutcome

            sentinel.append("called")
            return RuleOutcome(
                rule_id="fake",
                severity="warn",
                passed=True,
                message="ok",
            )

        register_rule_pack("custom_class", [fake_rule])
        ctx = RuleContext(
            loan_amount=Decimal("1000"),
            appraised_value=None,
            appraisal_date=None,
            annual_noi=None,
            annual_debt_service=None,
            guarantor_total_exposure=None,
            documents_present=set(),
            property_type=MF,
        )
        outcomes = run_rules_for("custom_class", ctx)
        assert len(outcomes) == 1
        assert outcomes[0].rule_id == "fake"
        assert sentinel == ["called"]

    def test_unknown_pack_falls_back_to_default(self):
        """Unknown classes must NOT crash — they fall back to the
        commercial pack (the conservative choice). This catches
        typos at the call site without taking the agent down."""
        from mkopo.rules.policy import DEFAULT_RULES, run_rules_for

        ctx = RuleContext(
            loan_amount=Decimal("2400000"),
            appraised_value=Decimal("3529400"),
            appraisal_date=datetime.now(UTC).date() - timedelta(days=30),
            annual_noi=Decimal("284200"),
            annual_debt_service=Decimal("144000"),
            guarantor_total_exposure=Decimal("4700000"),
            documents_present=set(REQUIRED_DOCS),
            property_type=MF,
        )
        outcomes = run_rules_for("does-not-exist", ctx)
        assert len(outcomes) == len(DEFAULT_RULES)
