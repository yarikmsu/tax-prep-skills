#!/usr/bin/env python3
"""Comprehensive tests for tax_computations.py.

Expected values are hand-computed from IRS worksheets and publications.
Tolerance: $0.01 (exact match after rounding to 2 decimal places).
"""

import math
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.tax_computations import (
    _coerce_numeric,
    compute_bracket_tax,
    compute_qdcg_tax,
    compute_se_tax,
    compute_additional_medicare,
    compute_ftc_limitation,
    compute_child_tax_credit,
    compute_qbi_deduction,
    compute_niit,
    check_form_8938_required,
    SE_TAX_THRESHOLD,
)

# TY2025 MFJ Brackets (OBBBA)
MFJ_BRACKETS_2025 = [
    {"rate": 0.10, "min": 0, "max": 23850},
    {"rate": 0.12, "min": 23850, "max": 96950},
    {"rate": 0.22, "min": 96950, "max": 206700},
    {"rate": 0.24, "min": 206700, "max": 394600},
    {"rate": 0.32, "min": 394600, "max": 501050},
    {"rate": 0.35, "min": 501050, "max": 751600},
    {"rate": 0.37, "min": 751600, "max": None},
]

SINGLE_BRACKETS_2025 = [
    {"rate": 0.10, "min": 0, "max": 11925},
    {"rate": 0.12, "min": 11925, "max": 48475},
    {"rate": 0.22, "min": 48475, "max": 103350},
    {"rate": 0.24, "min": 103350, "max": 197300},
    {"rate": 0.32, "min": 197300, "max": 250525},
    {"rate": 0.35, "min": 250525, "max": 626350},
    {"rate": 0.37, "min": 626350, "max": None},
]

CG_ZERO_LIMIT_MFJ = 96700
CG_FIFTEEN_LIMIT_MFJ = 600050
CG_ZERO_LIMIT_SINGLE = 48350
CG_FIFTEEN_LIMIT_SINGLE = 533800


class TestComputeBracketTax:
    """Tests for compute_bracket_tax — normal scenarios and error handling."""

    # --- Normal scenarios (7 tests) ---

    def test_mfj_150k(self):
        """$150K MFJ: 10% on 23850 + 12% on 73100 + 22% on 53050 = 22828.00"""
        tax, breakdown = compute_bracket_tax(150_000, MFJ_BRACKETS_2025)
        assert tax == 22828.00
        assert len(breakdown) == 3
        assert breakdown[0]["tax"] == 2385.00
        assert breakdown[1]["tax"] == 8772.00
        assert breakdown[2]["tax"] == 11671.00

    def test_mfj_400k(self):
        """$400K MFJ: spans 5 brackets, total = 82126.00"""
        tax, breakdown = compute_bracket_tax(400_000, MFJ_BRACKETS_2025)
        assert tax == 82126.00
        assert len(breakdown) == 5
        assert breakdown[0]["tax"] == 2385.00   # 10% on 23850
        assert breakdown[1]["tax"] == 8772.00   # 12% on 73100
        assert breakdown[2]["tax"] == 24145.00  # 22% on 109750
        assert breakdown[3]["tax"] == 45096.00  # 24% on 187900
        assert breakdown[4]["tax"] == 1728.00   # 32% on 5400

    def test_mfj_650k(self):
        """$650K MFJ: spans 6 brackets, total = 166594.50"""
        tax, breakdown = compute_bracket_tax(650_000, MFJ_BRACKETS_2025)
        assert tax == 166594.50
        assert len(breakdown) == 6
        assert breakdown[4]["tax"] == 34064.00   # 32% on 106450
        assert breakdown[5]["tax"] == 52132.50   # 35% on 148950

    def test_zero_income(self):
        """$0 income = $0 tax, empty breakdown."""
        tax, breakdown = compute_bracket_tax(0, MFJ_BRACKETS_2025)
        assert tax == 0.00
        assert breakdown == []

    def test_income_at_first_bracket_boundary(self):
        """$23850 exactly fills the 10% bracket = 2385.00, 1 bracket used."""
        tax, breakdown = compute_bracket_tax(23850, MFJ_BRACKETS_2025)
        assert tax == 2385.00
        assert len(breakdown) == 1
        assert breakdown[0]["rate"] == 0.10

    def test_single_bracket_only(self):
        """Single flat 10% bracket on $100K = 10000.00"""
        flat_bracket = [{"rate": 0.10, "min": 0, "max": None}]
        tax, breakdown = compute_bracket_tax(100_000, flat_bracket)
        assert tax == 10000.00
        assert len(breakdown) == 1

    def test_top_unbounded_bracket_800k(self):
        """$800K MFJ: spans all 7 brackets including unbounded 37% tier."""
        tax, breakdown = compute_bracket_tax(800_000, MFJ_BRACKETS_2025)
        # 10%: 23850*0.10=2385 + 12%: 73100*0.12=8772 + 22%: 109750*0.22=24145
        # 24%: 187900*0.24=45096 + 32%: 106450*0.32=34064 + 35%: 250550*0.35=87692.50
        # 37%: (800000-751600)*0.37 = 48400*0.37 = 17908
        # Total: 2385 + 8772 + 24145 + 45096 + 34064 + 87692.50 + 17908 = 220062.50
        assert tax == 220062.50
        assert len(breakdown) == 7
        assert breakdown[6]["rate"] == 0.37
        assert breakdown[6]["taxable_in_bracket"] == 48400.0
        assert breakdown[6]["tax"] == 17908.00

    # --- Error handling (5 tests) ---

    def test_empty_brackets_raises(self):
        """Empty brackets list raises ValueError mentioning 'empty'."""
        with pytest.raises(ValueError, match="empty"):
            compute_bracket_tax(50_000, [])

    def test_none_brackets_raises(self):
        """None brackets raises ValueError mentioning 'empty'."""
        with pytest.raises(ValueError, match="empty"):
            compute_bracket_tax(50_000, None)

    def test_none_income_raises(self):
        """None income raises ValueError mentioning 'cannot be None'."""
        with pytest.raises(ValueError, match="cannot be None"):
            compute_bracket_tax(None, MFJ_BRACKETS_2025)

    def test_negative_income_raises(self):
        """Negative income raises ValueError mentioning 'negative'."""
        with pytest.raises(ValueError, match="negative"):
            compute_bracket_tax(-10_000, MFJ_BRACKETS_2025)

    def test_malformed_bracket_raises(self):
        """Bracket missing 'min' key raises ValueError mentioning 'missing required keys'."""
        with pytest.raises(ValueError, match="missing required keys"):
            compute_bracket_tax(50_000, [{"rate": 0.10}])


class TestComputeQdcgTax:
    """Tests for compute_qdcg_tax — QDCG worksheet scenarios and error handling."""

    def test_mfj_200k_15k_qdiv_30k_ltcg(self):
        """$200K MFJ with $15K qdiv + $30K LTCG. QDCG worksheet saves tax."""
        result = compute_qdcg_tax(
            taxable_income=200_000,
            qualified_dividends=15_000,
            net_ltcg=30_000,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        # QDCG = 45000, Ordinary = 155000
        # Ordinary tax: 10% on 23850=2385 + 12% on 73100=8772 + 22% on 58050=12771 = 23928
        assert result["ordinary_tax"] == 23928.00
        # 0% room = 96700 - 155000 = negative -> 0
        assert result["qdcg_at_0_pct"] == 0
        # All 45000 at 15% = 6750
        assert result["qdcg_at_15_pct"] == 45000
        assert result["qdcg_tax"] == 6750.00
        assert result["worksheet_total"] == 30678.00
        # Regular tax on $200K = 23928 + 22%*(200000-155000) = 23928 + 9900 = 33828
        assert result["regular_tax"] == 33828.00
        assert result["final_tax"] == 30678.00
        assert result["used_qdcg_worksheet"] is True

    def test_mfj_low_income_0pct_qdcg(self):
        """$80K MFJ with $10K qdiv + $5K LTCG — all QDCG at 0%."""
        result = compute_qdcg_tax(
            taxable_income=80_000,
            qualified_dividends=10_000,
            net_ltcg=5_000,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        # QDCG = 15000, Ordinary = 65000
        # 0% room = 96700 - 65000 = 31700 > 15000 → all at 0%
        assert result["qdcg_at_0_pct"] == 15000
        assert result["qdcg_tax"] == 0
        # Ordinary tax: 10% on 23850=2385 + 12% on 41150=4938 = 7323
        assert result["ordinary_tax"] == 7323.00
        assert result["final_tax"] == 7323.00

    def test_zero_qdcg_equals_regular_tax(self):
        """No QDCG → final_tax equals regular_tax, worksheet not used."""
        result = compute_qdcg_tax(
            taxable_income=150_000,
            qualified_dividends=0,
            net_ltcg=0,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        assert result["final_tax"] == result["regular_tax"]
        assert result["used_qdcg_worksheet"] is False

    def test_all_income_is_qdcg(self):
        """All $50K is qdiv — ordinary=0, all at 0% rate. Tax = 0."""
        result = compute_qdcg_tax(
            taxable_income=50_000,
            qualified_dividends=50_000,
            net_ltcg=0,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        assert result["ordinary_income"] == 0
        assert result["qdcg_at_0_pct"] == 50000
        assert result["final_tax"] == 0.0

    def test_qdcg_capped_at_taxable_income(self):
        """Preferential income exceeding taxable income (standard deduction shielded
        part of the gain) must not be taxed. $100K LTCG with only $70K taxable income,
        all under the MFJ 0% threshold → $0, not $1,500 on the un-shielded $100K."""
        result = compute_qdcg_tax(
            taxable_income=70_000,
            qualified_dividends=0,
            net_ltcg=100_000,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        assert result["final_tax"] == 0.0
        # Only the $70K that survives into taxable income is bucketed.
        assert result["qdcg_at_0_pct"] + result["qdcg_at_15_pct"] + result["qdcg_at_20_pct"] == 70000

    def test_net_ltcg_negative_treated_as_zero(self):
        """Negative net_ltcg is treated as 0 in QDCG total."""
        result = compute_qdcg_tax(
            taxable_income=100_000,
            qualified_dividends=5_000,
            net_ltcg=-3_000,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        assert result["qdcg_total"] == 5000.00

    def test_high_income_20pct_tier(self):
        """$700K MFJ with $100K qdiv + $50K LTCG — hits 20% capital gains tier."""
        result = compute_qdcg_tax(
            taxable_income=700_000,
            qualified_dividends=100_000,
            net_ltcg=50_000,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        # QDCG = 150000, Ordinary = 550000
        # 0% room = 96700 - 550000 = negative → 0
        assert result["qdcg_at_0_pct"] == 0
        # 15% room = 600050 - 550000 - 0 = 50050
        assert result["qdcg_at_15_pct"] == 50050
        # 20% remainder = 150000 - 0 - 50050 = 99950
        assert result["qdcg_at_20_pct"] == 99950
        assert result["qdcg_tax"] == round(50050 * 0.15 + 99950 * 0.20, 2)
        assert result["used_qdcg_worksheet"] is True

    def test_none_taxable_income_raises(self):
        """None taxable_income raises ValueError."""
        with pytest.raises(ValueError, match="cannot be None"):
            compute_qdcg_tax(
                taxable_income=None,
                qualified_dividends=5000,
                net_ltcg=3000,
                brackets=MFJ_BRACKETS_2025,
                cg_zero_limit=CG_ZERO_LIMIT_MFJ,
                cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
            )

    def test_none_cg_limit_raises(self):
        """None cg_zero_limit raises ValueError mentioning 'thresholds not loaded'."""
        with pytest.raises(ValueError, match="thresholds not loaded"):
            compute_qdcg_tax(
                taxable_income=100_000,
                qualified_dividends=5000,
                net_ltcg=3000,
                brackets=MFJ_BRACKETS_2025,
                cg_zero_limit=None,
                cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
            )


class TestComputeSeTax:
    """Tests for compute_se_tax — SE tax computation with SS wage base cap."""

    SS_WAGE_BASE_2025 = 176100

    def test_basic_se_tax(self):
        """$80K SE profit, no W-2. SE_earnings=73880. Full SS + Medicare."""
        result = compute_se_tax(80_000, ss_wage_base=self.SS_WAGE_BASE_2025, w2_ss_wages=0)
        assert result["se_earnings"] == 73880.00
        assert result["ss_tax"] == 9161.12
        assert result["medicare_tax"] == 2142.52
        assert result["se_tax"] == 11303.64
        assert result["deductible_half"] == 5651.82

    def test_se_with_w2_wages_capping_ss(self):
        """$80K SE + $150K W-2. SS room = 26100."""
        result = compute_se_tax(80_000, ss_wage_base=self.SS_WAGE_BASE_2025, w2_ss_wages=150_000)
        assert result["ss_room"] == 26100.00
        assert result["ss_tax"] == 3236.40
        assert result["medicare_tax"] == 2142.52
        assert result["se_tax"] == 5378.92

    def test_se_w2_exceeds_ss_base(self):
        """$50K SE + $180K W-2. SS room = 0. Only Medicare applies."""
        result = compute_se_tax(50_000, ss_wage_base=self.SS_WAGE_BASE_2025, w2_ss_wages=180_000)
        se_earnings = 50_000 * 0.9235  # 46175.0
        assert result["se_earnings"] == 46175.00
        assert result["ss_room"] == 0
        assert result["ss_tax"] == 0
        assert result["medicare_tax"] == round(46175.0 * 0.029, 2)
        assert result["se_tax"] == result["medicare_tax"]

    def test_net_loss_no_se_tax(self):
        """Negative profit → no SE tax, note mentions 'net loss'."""
        result = compute_se_tax(-5_000, ss_wage_base=self.SS_WAGE_BASE_2025)
        assert result["se_tax"] == 0
        assert "net loss" in result["note"].lower()

    def test_below_threshold_no_se_tax(self):
        """$300 profit (below $400 threshold) → no SE tax."""
        result = compute_se_tax(300, ss_wage_base=self.SS_WAGE_BASE_2025)
        assert result["se_tax"] == 0
        assert "below" in result["note"].lower()

    def test_exactly_at_threshold(self):
        """$400 profit (exactly at threshold) → SE IS computed."""
        result = compute_se_tax(400, ss_wage_base=self.SS_WAGE_BASE_2025)
        se_earnings = 400 * 0.9235  # 369.40
        expected_tax = round(369.40 * 0.124 + 369.40 * 0.029, 2)
        assert result["se_earnings"] == 369.40
        assert result["se_tax"] == expected_tax

    def test_legacy_flat_rate_no_wage_base(self):
        """No ss_wage_base → legacy flat rate (ss_rate + medicare_rate) on SE earnings."""
        result = compute_se_tax(80_000, ss_wage_base=None, w2_ss_wages=0)
        se_earnings = 80_000 * 0.9235  # 73880.0
        expected_tax = round(se_earnings * (0.124 + 0.029), 2)
        assert result["se_earnings"] == 73880.00
        assert result["se_tax"] == expected_tax
        assert "ss_tax" not in result  # no component breakdown in legacy path
        assert "Legacy flat rate" in result.get("note", "")

    def test_none_profit_raises(self):
        """None net_profit raises ValueError."""
        with pytest.raises(ValueError, match="cannot be None"):
            compute_se_tax(None, ss_wage_base=self.SS_WAGE_BASE_2025)

    def test_negative_w2_wages_raises(self):
        """Negative w2_ss_wages raises ValueError."""
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_se_tax(50_000, ss_wage_base=self.SS_WAGE_BASE_2025, w2_ss_wages=-1000)

    def test_zero_wage_base_raises(self):
        """Zero ss_wage_base raises ValueError mentioning 'must be positive'."""
        with pytest.raises(ValueError, match="must be positive"):
            compute_se_tax(50_000, ss_wage_base=0)

    def test_bool_wage_base_raises(self):
        """Bool ss_wage_base raises ValueError mentioning 'numeric value'."""
        with pytest.raises(ValueError, match="numeric value"):
            compute_se_tax(50_000, ss_wage_base=True)


class TestComputeAdditionalMedicare:
    """Tests for compute_additional_medicare — Form 8959 computation."""

    def test_mfj_260k_wages(self):
        """$260K wages, MFJ threshold $250K, withheld $4310."""
        result = compute_additional_medicare(
            medicare_wages=260_000, se_income=0, threshold=250_000, withheld=4310,
        )
        # excess = 10000, addl_tax = 90
        assert result["excess_over_threshold"] == 10000
        assert result["additional_tax"] == 90.00
        # regular_medicare = 260000 * 0.0145 = 3770
        assert result["regular_medicare"] == 3770.00
        # withheld_addl = 4310 - 3770 = 540
        assert result["withheld_addl"] == 540.00
        # over_withholding = 540 - 90 = 450
        assert result["over_withholding_credit"] == 450.00

    def test_mfj_wages_between_200k_250k(self):
        """$220K wages MFJ (threshold $250K) — no additional tax, employer over-withheld."""
        result = compute_additional_medicare(
            medicare_wages=220_000, se_income=0, threshold=250_000, withheld=3370,
        )
        assert result["additional_tax"] == 0
        # regular_medicare = 220000 * 0.0145 = 3190
        # withheld_addl = 3370 - 3190 = 180
        # over_withholding = 180 - 0 = 180
        assert result["over_withholding_credit"] == 180.00

    def test_single_200k_threshold(self):
        """$250K wages, single threshold $200K. Addl tax = $450."""
        result = compute_additional_medicare(
            medicare_wages=250_000, se_income=0, threshold=200_000, withheld=4075,
        )
        assert result["excess_over_threshold"] == 50000
        assert result["additional_tax"] == 450.00

    def test_wages_plus_se_income(self):
        """$200K wages + $80K SE income, MFJ threshold $250K."""
        result = compute_additional_medicare(
            medicare_wages=200_000, se_income=80_000, threshold=250_000, withheld=2900,
        )
        assert result["combined"] == 280000
        assert result["excess_over_threshold"] == 30000
        assert result["additional_tax"] == 270.00

    def test_below_threshold_no_tax(self):
        """$150K wages, MFJ threshold $250K — no additional tax."""
        result = compute_additional_medicare(
            medicare_wages=150_000, se_income=0, threshold=250_000, withheld=2175,
        )
        assert result["additional_tax"] == 0

    def test_none_threshold_raises(self):
        """None threshold raises ValueError."""
        with pytest.raises(ValueError, match="cannot be None"):
            compute_additional_medicare(
                medicare_wages=200_000, se_income=0, threshold=None, withheld=2900,
            )

    def test_negative_wages_raises(self):
        """Negative wages raises ValueError."""
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_additional_medicare(
                medicare_wages=-5000, se_income=0, threshold=250_000, withheld=0,
            )


class TestComputeFtcLimitation:
    """Tests for compute_ftc_limitation — FTC limitation and credit scenarios."""

    def test_basic_ftc(self):
        """Foreign=5K, worldwide=200K, us_tax=30K, paid=800. Limited by limitation."""
        result = compute_ftc_limitation(5000, 200000, 30000, 800)
        assert result["limitation"] == 750.00
        assert result["ftc_allowed"] == 750.00
        assert result["limited_by"] == "limitation"

    def test_ftc_limited_by_tax_paid(self):
        """Foreign=50K, worldwide=200K, us_tax=30K, paid=500. Limited by tax paid."""
        result = compute_ftc_limitation(50000, 200000, 30000, 500)
        assert result["limitation"] == 7500.00
        assert result["ftc_allowed"] == 500.00
        assert result["limited_by"] == "tax_paid"

    def test_foreign_income_exceeds_worldwide(self):
        """Foreign=300K > worldwide=200K. Ratio capped at 1.0."""
        result = compute_ftc_limitation(300000, 200000, 50000, 10000)
        assert result["limitation"] == 50000.00
        assert result["ftc_allowed"] == 10000.00

    def test_zero_worldwide_income(self):
        """Worldwide=0 → limitation=0, FTC=0."""
        result = compute_ftc_limitation(1000, 0, 0, 100)
        assert result["limitation"] == 0.00
        assert result["ftc_allowed"] == 0.00

    def test_negative_foreign_income(self):
        """Negative foreign income → ratio floored at 0, limitation=0."""
        result = compute_ftc_limitation(-5000, 200000, 30000, 0)
        assert result["limitation"] == 0.00

    def test_none_foreign_income_raises(self):
        """None foreign_source_income raises ValueError."""
        with pytest.raises(ValueError, match="foreign_source_income cannot be None"):
            compute_ftc_limitation(None, 200000, 30000, 800)

    def test_none_worldwide_income_raises(self):
        """None worldwide_income raises ValueError."""
        with pytest.raises(ValueError, match="worldwide_income cannot be None"):
            compute_ftc_limitation(5000, None, 30000, 800)

    def test_none_us_tax_raises(self):
        """None us_tax raises ValueError."""
        with pytest.raises(ValueError, match="us_tax cannot be None"):
            compute_ftc_limitation(5000, 200000, None, 800)

    def test_none_foreign_tax_paid_raises(self):
        """None foreign_tax_paid raises ValueError."""
        with pytest.raises(ValueError, match="foreign_tax_paid cannot be None"):
            compute_ftc_limitation(5000, 200000, 30000, None)


class TestComputeChildTaxCredit:
    """Tests for compute_child_tax_credit — CTC computation with phaseout."""

    def test_two_children_below_phaseout(self):
        """2 kids, AGI well below phaseout. Full credit."""
        result = compute_child_tax_credit(2, 2000, 200000, 400000)
        assert result["credit_allowed"] == 4000.00
        assert result["phase_out_reduction"] == 0.00

    def test_phaseout_exact_1000_increments(self):
        """2 kids, AGI $20K over phaseout (exact $1K multiple)."""
        result = compute_child_tax_credit(2, 2000, 420000, 400000)
        # excess=20000, reduction=ceil(20000/1000)*50=1000
        assert result["phase_out_reduction"] == 1000.00
        assert result["credit_allowed"] == 3000.00

    def test_phaseout_fraction_of_1000(self):
        """2 kids, AGI $500 over phaseout (fraction of $1K)."""
        result = compute_child_tax_credit(2, 2000, 400500, 400000)
        # excess=500, reduction=ceil(500/1000)*50=50
        assert result["phase_out_reduction"] == 50.00
        assert result["credit_allowed"] == 3950.00

    def test_phaseout_to_zero(self):
        """2 kids, AGI $100K over phaseout. Credit fully phased out."""
        result = compute_child_tax_credit(2, 2000, 500000, 400000)
        # excess=100000, reduction=ceil(100000/1000)*50=5000 > 4000
        assert result["credit_allowed"] == 0.00

    def test_exactly_at_phaseout_start(self):
        """1 kid, AGI exactly at phaseout. Full credit (no excess)."""
        result = compute_child_tax_credit(1, 2000, 400000, 400000)
        assert result["credit_allowed"] == 2000.00

    def test_zero_children(self):
        """0 children → 0 credit."""
        result = compute_child_tax_credit(0, 2000, 200000, 400000)
        assert result["credit_allowed"] == 0.00

    def test_obbba_enhanced_credit(self):
        """3 kids at $2200/child (OBBBA enhanced rate)."""
        result = compute_child_tax_credit(3, 2200, 200000, 400000)
        assert result["credit_allowed"] == 6600.00

    def test_one_dollar_over_phaseout(self):
        """AGI $1 over phaseout → ceil(1/1000)=1 → $50 reduction."""
        result = compute_child_tax_credit(1, 2000, 400001, 400000)
        assert result["phase_out_reduction"] == 50.00
        assert result["credit_allowed"] == 1950.00

    def test_none_children_raises(self):
        """None num_qualifying_children raises ValueError."""
        with pytest.raises(ValueError, match="num_qualifying_children cannot be None"):
            compute_child_tax_credit(None, 2000, 200000, 400000)

    def test_none_credit_per_child_raises(self):
        """None credit_per_child raises ValueError."""
        with pytest.raises(ValueError, match="credit_per_child cannot be None"):
            compute_child_tax_credit(2, None, 200000, 400000)

    def test_none_agi_raises(self):
        """None agi raises ValueError."""
        with pytest.raises(ValueError, match="agi cannot be None"):
            compute_child_tax_credit(2, 2000, None, 400000)

    def test_none_phaseout_start_raises(self):
        """None phase_out_start raises ValueError."""
        with pytest.raises(ValueError, match="phase_out_start cannot be None"):
            compute_child_tax_credit(2, 2000, 200000, None)


class TestComputeQbiDeduction:
    """Tests for compute_qbi_deduction — QBI deduction computation."""

    def test_positive_qbi_with_reit(self):
        """Positive QBI + REIT dividends."""
        result = compute_qbi_deduction(50000, 1000)
        assert result["qbi_component"] == 10000.00
        assert result["reit_component"] == 200.00
        assert result["total_deduction"] == 10200.00
        assert result["loss_carryforward"] == 0.00

    def test_negative_qbi_carryforward(self):
        """Negative QBI → loss carryforward, only REIT component."""
        result = compute_qbi_deduction(-10000, 500)
        assert result["qbi_component"] == 0.00
        assert result["reit_component"] == 100.00
        assert result["total_deduction"] == 100.00
        assert result["loss_carryforward"] == -10000.00

    def test_zero_qbi_zero_reit(self):
        """Zero QBI + zero REIT → zero deduction."""
        result = compute_qbi_deduction(0, 0)
        assert result["total_deduction"] == 0.00

    def test_reit_only(self):
        """QBI=0, REIT=2000 → only REIT component."""
        result = compute_qbi_deduction(0, 2000)
        assert result["total_deduction"] == 400.00

    def test_negative_reit_raises(self):
        """Negative REIT dividends raises ValueError."""
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_qbi_deduction(50000, -100)

    def test_none_qbi_raises(self):
        """None QBI raises ValueError."""
        with pytest.raises(ValueError, match="cannot be None"):
            compute_qbi_deduction(None, 500)


class TestComputeNiit:
    """Tests for compute_niit — Form 8960 net investment income tax (3.8%)."""

    def test_excess_caps_the_base(self):
        # MAGI 300K, NII 20K, thr 250K → min(20K, 50K)=20K × 3.8% = 760
        assert compute_niit(300000, 20000, 250000) == 760.00

    def test_nii_caps_the_base(self):
        # MAGI 300K, NII 10K, thr 250K → min(10K, 50K)=10K × 3.8% = 380
        assert compute_niit(300000, 10000, 250000) == 380.00

    def test_below_threshold_is_zero(self):
        assert compute_niit(240000, 20000, 250000) == 0.00

    def test_exactly_at_threshold_is_zero(self):
        # excess = 0 at the threshold
        assert compute_niit(250000, 20000, 250000) == 0.00

    def test_none_threshold_not_modeled(self):
        # Degrade gracefully rather than raise — caller has no fetched threshold.
        assert compute_niit(300000, 20000, None) == 0.00

    def test_zero_nii_is_zero(self):
        assert compute_niit(500000, 0, 250000) == 0.00

    def test_negative_nii_is_zero(self):
        assert compute_niit(500000, -5000, 250000) == 0.00

    def test_custom_rate(self):
        assert compute_niit(300000, 20000, 250000, rate=0.05) == 1000.00

    def test_bool_raises(self):
        with pytest.raises(ValueError, match="numeric"):
            compute_niit(True, 20000, 250000)

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError, match="numeric"):
            compute_niit("lots", 20000, 250000)


class TestCheckForm8938Required:
    """Tests for check_form_8938_required — FATCA threshold analysis."""

    THRESHOLDS = {
        "mfj_us": {"year_end": 200000, "any_time": 300000},
        "single_us": {"year_end": 50000, "any_time": 75000},
        "mfj_foreign": {"year_end": 400000, "any_time": 600000},
        "single_foreign": {"year_end": 200000, "any_time": 300000},
    }

    def test_mfj_us_below_threshold(self):
        """MFJ US, totals below thresholds → not required."""
        accts = [{"year_end_value_usd": 100000, "max_value_usd": 150000}]
        result = check_form_8938_required(accts, "mfj", False, self.THRESHOLDS)
        assert result["required"] is False

    def test_mfj_us_year_end_triggers(self):
        """MFJ US, year-end exceeds threshold → required."""
        accts = [
            {"year_end_value_usd": 110000, "max_value_usd": 120000},
            {"year_end_value_usd": 100000, "max_value_usd": 130000},
        ]
        result = check_form_8938_required(accts, "mfj", False, self.THRESHOLDS)
        assert result["required"] is True  # total_ye=210K > 200K

    def test_mfj_us_any_time_triggers(self):
        """MFJ US, any-time max exceeds threshold → required."""
        accts = [{"year_end_value_usd": 100000, "max_value_usd": 310000}]
        result = check_form_8938_required(accts, "mfj", False, self.THRESHOLDS)
        assert result["required"] is True  # max=310K > 300K

    def test_single_us_threshold(self):
        """Single US, year-end exceeds single threshold → required."""
        accts = [{"year_end_value_usd": 55000, "max_value_usd": 60000}]
        result = check_form_8938_required(accts, "single", False, self.THRESHOLDS)
        assert result["required"] is True

    def test_hoh_uses_single_thresholds(self):
        """HoH uses single thresholds."""
        accts = [{"year_end_value_usd": 55000, "max_value_usd": 60000}]
        result = check_form_8938_required(accts, "hoh", False, self.THRESHOLDS)
        assert result["required"] is True
        assert result["threshold_year_end"] == 50000

    def test_mfs_uses_single_thresholds(self):
        """MFS uses single thresholds."""
        accts = [{"year_end_value_usd": 55000, "max_value_usd": 60000}]
        result = check_form_8938_required(accts, "mfs", False, self.THRESHOLDS)
        assert result["required"] is True
        assert result["threshold_year_end"] == 50000

    def test_foreign_resident_higher_thresholds(self):
        """Single abroad, below higher foreign thresholds → not required."""
        accts = [{"year_end_value_usd": 150000, "max_value_usd": 250000}]
        result = check_form_8938_required(accts, "single", True, self.THRESHOLDS)
        assert result["required"] is False

    def test_string_values_coerced(self):
        """String values like '$55,000' are coerced to float."""
        accts = [{"year_end_value_usd": "$55,000", "max_value_usd": "60000"}]
        result = check_form_8938_required(accts, "single", False, self.THRESHOLDS)
        assert result["required"] is True
        assert result["total_year_end"] == 55000.00

    def test_empty_accounts_not_required(self):
        """Empty accounts list → not required."""
        result = check_form_8938_required([], "mfj", False, self.THRESHOLDS)
        assert result["required"] is False

    def test_exactly_at_threshold_not_required(self):
        """Exactly at threshold (not exceeding) → NOT required."""
        accts = [{"year_end_value_usd": 200000, "max_value_usd": 300000}]
        result = check_form_8938_required(accts, "mfj", False, self.THRESHOLDS)
        assert result["required"] is False

    def test_invalid_filing_status_raises(self):
        """Invalid filing status raises ValueError."""
        with pytest.raises(ValueError, match="Invalid filing_status"):
            check_form_8938_required([], "married", False, self.THRESHOLDS)

    def test_none_accounts_raises(self):
        """None accounts raises ValueError."""
        with pytest.raises(ValueError, match="cannot be None"):
            check_form_8938_required(None, "mfj", False, self.THRESHOLDS)

    def test_fallback_defaults_no_thresholds(self):
        """When form_8938_thresholds=None, uses IRS default thresholds."""
        accts = [{"year_end_value_usd": 55000, "max_value_usd": 60000}]
        result = check_form_8938_required(accts, "single", False, form_8938_thresholds=None)
        assert result["threshold_year_end"] == 50000  # IRS default single US
        assert result["threshold_any_time"] == 75000
        assert result["required"] is True  # 55K > 50K

    def test_fallback_defaults_mfj_abroad(self):
        """Fallback defaults for MFJ abroad use higher foreign thresholds."""
        accts = [{"year_end_value_usd": 300000, "max_value_usd": 500000}]
        result = check_form_8938_required(accts, "mfj", True, form_8938_thresholds=None)
        assert result["threshold_year_end"] == 400000  # MFJ abroad
        assert result["threshold_any_time"] == 600000
        assert result["required"] is False  # 300K < 400K and 500K < 600K

    def test_non_numeric_account_raises(self):
        """Non-numeric account value raises ValueError."""
        accts = [{"year_end_value_usd": "abc", "max_value_usd": 5000}]
        with pytest.raises(ValueError, match="non-numeric"):
            check_form_8938_required(accts, "mfj", False, self.THRESHOLDS)


class TestCoerceNumeric:
    """Tests for _coerce_numeric — type coercion utility."""

    def test_int(self):
        assert _coerce_numeric(42) == 42.0

    def test_float(self):
        assert _coerce_numeric(3.14) == 3.14

    def test_string_plain(self):
        assert _coerce_numeric("5000") == 5000.0

    def test_string_with_dollar_and_commas(self):
        assert _coerce_numeric("$5,000.50") == 5000.50

    def test_string_with_spaces(self):
        assert _coerce_numeric("  1234  ") == 1234.0

    def test_unparseable_returns_default(self):
        assert _coerce_numeric("abc") is None
        assert _coerce_numeric("abc", default=0) == 0

    def test_none_returns_default(self):
        assert _coerce_numeric(None) is None
        assert _coerce_numeric(None, default=0) == 0

    def test_bool_raises(self):
        with pytest.raises(TypeError, match="got bool"):
            _coerce_numeric(True)
        with pytest.raises(TypeError, match="got bool"):
            _coerce_numeric(False)

    def test_empty_string_returns_default(self):
        assert _coerce_numeric("") is None


class TestFullPipeline:
    """Integration test — full MFJ computation pipeline."""

    def test_full_computation(self):
        """MFJ scenario: W-2 $180K, interest, dividends, LTCG, foreign tax, 2 kids."""
        # Income components
        wages = 180000
        taxable_interest = 2000
        ordinary_dividends = 15000
        qualified_dividends = 12000
        net_ltcg = 30000
        cap_gain_distributions = 3000  # included in ordinary dividends on 1099-DIV
        foreign_tax_paid = 400
        foreign_source_income = 3000

        # Step 1: Total income (line 9)
        # 1099-B net LTCG + cap gain dist = 33000 reported on Schedule D
        total_capital = net_ltcg + cap_gain_distributions  # 33000
        line_9 = wages + taxable_interest + ordinary_dividends + total_capital
        assert line_9 == 230000

        # Step 2: AGI (line 11) — no adjustments in this scenario
        line_11 = line_9
        assert line_11 == 230000

        # Step 3: Deductions
        qbi_result = compute_qbi_deduction(0, 0)
        assert qbi_result["total_deduction"] == 0.00
        standard_deduction = 30000
        line_14 = standard_deduction + qbi_result["total_deduction"]
        line_15 = max(0, line_11 - line_14)  # taxable income
        assert line_15 == 200000

        # Step 4: QDCG tax (line 16)
        # net_ltcg for QDCG worksheet = Schedule D line 15 (net LTCG after losses)
        # cap gain distributions are part of LTCG, so total QDCG-eligible LTCG = 33000
        qdcg_result = compute_qdcg_tax(
            taxable_income=line_15,
            qualified_dividends=qualified_dividends,
            net_ltcg=total_capital,  # 33000 (net LTCG including cap gain dist)
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        line_16 = qdcg_result["final_tax"]
        assert line_16 == 30678.00

        # Step 5: CTC
        ctc_result = compute_child_tax_credit(2, 2000, line_11, 400000)
        assert ctc_result["credit_allowed"] == 4000.00

        # Step 6: FTC
        ftc_result = compute_ftc_limitation(
            foreign_source_income=foreign_source_income,
            worldwide_income=line_11,
            us_tax=line_16,
            foreign_tax_paid=foreign_tax_paid,
        )
        assert ftc_result["ftc_allowed"] == 400.00
        assert ftc_result["limited_by"] == "tax_paid"

        # Step 7: Additional Medicare
        medicare_result = compute_additional_medicare(
            medicare_wages=wages,
            se_income=0,
            threshold=250000,
            withheld=wages * 0.0145,  # standard withholding
        )
        assert medicare_result["additional_tax"] == 0  # 180K < 250K

        # Step 8: Final tax
        total_credits = ctc_result["credit_allowed"] + ftc_result["ftc_allowed"]
        line_24 = max(0, line_16 - total_credits) + medicare_result["additional_tax"]
        # 30678 - (4000 + 400) + 0 = 26278
        assert line_24 == 26278.00
