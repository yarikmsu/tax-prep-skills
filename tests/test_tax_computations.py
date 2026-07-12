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
    compute_form_8606,
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

    def test_se_income_excluded_from_part_iv_reconciliation(self):
        """Form 8959 Part IV line 20 is Medicare WAGES x 1.45% only — SE income has
        no withholding to reconcile, so it must not inflate regular_medicare.
        $200K wages + $80K SE, MFJ, Box 6 withheld $3,400."""
        result = compute_additional_medicare(
            medicare_wages=200_000, se_income=80_000, threshold=250_000, withheld=3400,
        )
        # line 20 = 200000 * 0.0145 = 2900 (NOT 2900 + 80000*0.0145 = 4060)
        assert result["regular_medicare"] == 2900.00
        # line 21 = 3400 - 2900 = 500
        assert result["withheld_addl"] == 500.00
        # owed = (280000 - 250000) * 0.009 = 270; credit = 500 - 270 = 230
        assert result["additional_tax"] == 270.00
        assert result["over_withholding_credit"] == 230.00

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

    # --- ODC (Credit for Other Dependents) + refundable ACTC (Schedule 8812) ---

    def test_odc_added_for_other_dependents(self):
        """1 CTC child + 2 other dependents: $2000 + 2 x $500 = $3000."""
        result = compute_child_tax_credit(1, 2000, 100000, 400000,
                                          num_other_dependents=2)
        assert result["full_credit"] == 2000.00
        assert result["odc_full"] == 1000.00
        assert result["combined_full"] == 3000.00
        assert result["credit_allowed"] == 3000.00

    def test_phaseout_applies_to_combined_ctc_and_odc(self):
        """Schedule 8812 phases out the COMBINED credit: 1 child + 1 ODC,
        AGI $5K over → reduction ceil(5000/1000) x 50 = 250 off the $2500."""
        result = compute_child_tax_credit(1, 2000, 405000, 400000,
                                          num_other_dependents=1)
        assert result["combined_full"] == 2500.00
        assert result["phase_out_reduction"] == 250.00
        assert result["credit_allowed"] == 2250.00

    def test_refundable_actc_capped_per_child(self):
        """2 children, low tax: unused $3500 capped at 2 x $1700 = $3400 refundable
        (the per-child line 16b cap binds before the earned-income limit)."""
        result = compute_child_tax_credit(
            2, 2000, 30000, 400000,
            tax_liability=500, earned_income=30000, actc_limit_per_child=1700)
        assert result["credit_allowed"] == 4000.00
        assert result["nonrefundable_used"] == 500.00
        # unused = 3500; cap = 3400; EI limit = 0.15 x (30000-2500) = 4125
        assert result["refundable_actc"] == 3400.00

    def test_refundable_actc_earned_income_limit_binds(self):
        """EI $10K: limit = 0.15 x 7500 = 1125 < min(unused, cap) → ACTC 1125."""
        result = compute_child_tax_credit(
            1, 2000, 20000, 400000,
            tax_liability=0, earned_income=10000, actc_limit_per_child=1700)
        assert result["nonrefundable_used"] == 0.00
        # unused = 2000; cap = 1700; EI limit = 1125
        assert result["refundable_actc"] == 1125.00

    def test_refundable_actc_zero_below_earned_income_floor(self):
        """Earned income at/below the $2,500 floor → no ACTC."""
        result = compute_child_tax_credit(
            1, 2000, 10000, 400000,
            tax_liability=0, earned_income=2500, actc_limit_per_child=1700)
        assert result["refundable_actc"] == 0.00

    def test_odc_never_refundable(self):
        """0 CTC children + 2 ODC, $0 tax: the $1000 ODC has no refundable path."""
        result = compute_child_tax_credit(
            0, 2000, 50000, 400000, num_other_dependents=2,
            tax_liability=0, earned_income=50000, actc_limit_per_child=1700)
        assert result["credit_allowed"] == 1000.00
        assert result["nonrefundable_used"] == 0.00
        assert result["refundable_actc"] == 0.00

    def test_fully_used_nonrefundable_no_actc(self):
        """Tax large enough to absorb the whole credit → ACTC 0."""
        result = compute_child_tax_credit(
            2, 2000, 150000, 400000,
            tax_liability=20000, earned_income=150000, actc_limit_per_child=1700)
        assert result["nonrefundable_used"] == 4000.00
        assert result["refundable_actc"] == 0.00

    def test_three_plus_children_ss_alternative_flagged(self):
        """3 children with the 15% EI formula binding → the unmodeled SS-tax
        alternative could only RAISE the ACTC, so the understatement is flagged."""
        result = compute_child_tax_credit(
            3, 2000, 30000, 400000,
            tax_liability=0, earned_income=10000, actc_limit_per_child=1700)
        # unused = 6000; cap = 5100; EI limit = 1125 → binds
        assert result["refundable_actc"] == 1125.00
        assert result["actc_3plus_ss_alternative_not_modeled"] is True

    def test_legacy_call_has_no_split_keys_populated(self):
        """Without tax_liability the split stays None (legacy contract for
        compute_scenario / projection)."""
        result = compute_child_tax_credit(2, 2000, 200000, 400000)
        assert result["nonrefundable_used"] is None
        assert result["refundable_actc"] is None
        assert result["actc_3plus_ss_alternative_not_modeled"] is False

    def test_missing_actc_limit_raises_when_needed(self):
        """Refundable split requested with unused credit but no per-child cap →
        hard error pointing at the tax_law field, not a silent skip."""
        with pytest.raises(ValueError, match="refundable_limit"):
            compute_child_tax_credit(
                2, 2000, 30000, 400000, tax_liability=0, earned_income=30000)

    def test_missing_earned_income_raises_when_split_requested(self):
        with pytest.raises(ValueError, match="earned_income is required"):
            compute_child_tax_credit(
                2, 2000, 30000, 400000, tax_liability=0, actc_limit_per_child=1700)

    def test_negative_other_dependents_raises(self):
        with pytest.raises(ValueError, match="non-negative count"):
            compute_child_tax_credit(1, 2000, 100000, 400000,
                                     num_other_dependents=-1)

    def test_nan_tax_liability_raises(self):
        with pytest.raises(ValueError, match="finite"):
            compute_child_tax_credit(
                1, 2000, 100000, 400000, tax_liability=float("nan"),
                earned_income=50000, actc_limit_per_child=1700)

    def test_nan_actc_limit_raises(self):
        """A corrupted cache refundable_limit (json.loads accepts NaN) must not
        silently bypass the per-child cap."""
        with pytest.raises(ValueError, match="finite non-negative"):
            compute_child_tax_credit(
                1, 2000, 20000, 400000, tax_liability=0,
                earned_income=30000, actc_limit_per_child=float("nan"))

    def test_negative_actc_limit_raises(self):
        """A negative cap would produce a negative refundable ACTC."""
        with pytest.raises(ValueError, match="finite non-negative"):
            compute_child_tax_credit(
                1, 2000, 20000, 400000, tax_liability=0,
                earned_income=30000, actc_limit_per_child=-1)

    def test_bool_actc_limit_raises(self):
        with pytest.raises(ValueError, match="got bool"):
            compute_child_tax_credit(
                1, 2000, 20000, 400000, tax_liability=0,
                earned_income=30000, actc_limit_per_child=True)


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

    # --- Form 8995 overall cap (lines 11-14): deduction cannot exceed
    # --- rate x (taxable income before QBI - net capital gain) ---

    def test_income_cap_binds(self):
        """QBI $100K (component $20K) but TI-before-QBI $50K with $10K net cap gain:
        cap = 20% x (50000 - 10000) = 8000 < 20000 → deduction capped."""
        result = compute_qbi_deduction(
            100000, 0, taxable_income_before_qbi=50000, net_capital_gain=10000)
        assert result["income_cap"] == 8000.00
        assert result["total_deduction"] == 8000.00
        assert result["income_limited"] is True
        assert result["income_cap_applied"] is True

    def test_income_cap_does_not_bind(self):
        """TI-before-QBI $200K, no cap gain: cap = 40000 > 10200 → unchanged."""
        result = compute_qbi_deduction(
            50000, 1000, taxable_income_before_qbi=200000, net_capital_gain=0)
        assert result["total_deduction"] == 10200.00
        assert result["income_limited"] is False
        assert result["income_cap"] == 40000.00

    def test_income_cap_reit_included(self):
        """The cap limits the COMBINED QBI+REIT components (Form 8995 line 13):
        components = 20% x 50000 + 20% x 5000 = 11000; cap = 20% x (30000 - 25000)
        = 1000 → deduction = 1000."""
        result = compute_qbi_deduction(
            50000, 5000, taxable_income_before_qbi=30000, net_capital_gain=25000)
        assert result["income_cap"] == 1000.00
        assert result["total_deduction"] == 1000.00
        assert result["income_limited"] is True

    def test_income_cap_zero_taxable_income(self):
        """TI-before-QBI $0 (large deductions) → cap 0 → deduction 0, not 20% of QBI."""
        result = compute_qbi_deduction(
            80000, 0, taxable_income_before_qbi=0, net_capital_gain=0)
        assert result["total_deduction"] == 0.00
        assert result["income_limited"] is True

    def test_legacy_call_skips_cap_and_flags_it(self):
        """Without taxable_income_before_qbi the cap is skipped (legacy contract)
        but the result says so — income_cap_applied False, income_cap None."""
        result = compute_qbi_deduction(100000, 0)
        assert result["total_deduction"] == 20000.00
        assert result["income_cap_applied"] is False
        assert result["income_cap"] is None
        assert result["income_limited"] is False

    def test_over_threshold_flagged_with_warning(self):
        """TI above the §199A threshold → over_threshold True + warning text
        (8995-A wage/UBIA/SSTB limits not modeled — may overstate)."""
        result = compute_qbi_deduction(
            100000, 0, taxable_income_before_qbi=450000, net_capital_gain=0,
            threshold=394600)
        assert result["over_threshold"] is True
        assert "8995-A" in result["warning"]

    def test_under_threshold_no_warning(self):
        result = compute_qbi_deduction(
            50000, 0, taxable_income_before_qbi=100000, net_capital_gain=0,
            threshold=394600)
        assert result["over_threshold"] is False
        assert "warning" not in result

    def test_no_threshold_check_when_absent(self):
        """threshold None (not fetched) → over_threshold None, no crash."""
        result = compute_qbi_deduction(
            50000, 0, taxable_income_before_qbi=100000, net_capital_gain=0)
        assert result["over_threshold"] is None

    def test_threshold_without_taxable_income_raises(self):
        with pytest.raises(ValueError, match="requires taxable_income_before_qbi"):
            compute_qbi_deduction(50000, 0, threshold=394600)

    def test_negative_taxable_income_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_qbi_deduction(50000, 0, taxable_income_before_qbi=-1,
                                  net_capital_gain=0)

    def test_nan_taxable_income_raises(self):
        """json.loads accepts NaN — it must not silently defeat the cap."""
        with pytest.raises(ValueError, match="finite"):
            compute_qbi_deduction(50000, 0, taxable_income_before_qbi=float("nan"),
                                  net_capital_gain=0)


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


class TestComputeForm8606:
    """Tests for compute_form_8606 — Part I nondeductible basis pro-rata rule.

    Expected taxable amounts hand-computed from the Form 8606 worksheet, with the
    basis ratio kept at full precision (matching tax software, not the 3-dp form rule).
    """

    def test_clean_backdoor_no_pretax_is_tax_free(self):
        # $7K nondeductible basis, no other pre-tax IRA, convert all $7K.
        # ratio = 7000/7000 = 1.0 → conversion is fully return of basis → $0 taxable.
        r = compute_form_8606(0, 7000, 0, 0, 7000)
        assert r["line_10_ratio"] == 1.0
        assert r["taxable_conversion"] == 0.00
        assert r["basis_remaining"] == 0.00
        assert r["pro_rata_triggered"] is False  # no pre-tax money → not the trap

    def test_pro_rata_trap(self):
        # $7K basis sitting alongside a $50K pre-tax IRA, convert $7K.
        # ratio = 7000/57000 = 0.122807...; nontaxable = 7000*ratio = 859.65;
        # taxable = 7000 - 859.65 = 6140.35; basis remaining = 7000 - 859.65 = 6140.35.
        r = compute_form_8606(0, 7000, 50000, 0, 7000)
        assert r["line_9_total_value"] == 57000.00
        assert r["line_10_ratio"] == 0.12281
        assert r["line_11_nontaxable_conversion"] == 859.65
        assert r["taxable_conversion"] == 6140.35
        assert r["basis_remaining"] == 6140.35
        assert r["taxable_to_1040"] == 6140.35
        assert r["pro_rata_triggered"] is True

    def test_full_balance_conversion_with_basis_triggers_trap(self):
        # Convert the ENTIRE $57K IRA ($7K basis + $50K pre-tax) → year-end FMV is $0,
        # but the conversion is still pro-rated: $50K taxable. The trap flag must fire
        # even though year_end_value == 0 (the old line_6 > 0 proxy missed this).
        r = compute_form_8606(0, 7000, 0, 0, 57000)
        assert r["line_6_year_end_value"] == 0.00
        assert r["taxable_conversion"] == 50000.00
        assert r["line_11_nontaxable_conversion"] == 7000.00
        assert r["pro_rata_triggered"] is True

    def test_all_pretax_no_basis_fully_taxable(self):
        # No basis at all, convert $10K from a $50K pre-tax IRA → fully taxable.
        r = compute_form_8606(0, 0, 50000, 0, 10000)
        assert r["line_10_ratio"] == 0.0
        assert r["taxable_conversion"] == 10000.00
        assert r["basis_remaining"] == 0.00
        # basis=0 → no "trap" (fully taxable is expected, not a surprise)
        assert r["pro_rata_triggered"] is False

    def test_nothing_happened_basis_carries(self):
        # Basis present but no distribution/conversion and no year-end balance entered.
        # line 9 == 0 → ratio 0, basis carries forward unchanged.
        r = compute_form_8606(0, 5000, 0, 0, 0)
        assert r["line_10_ratio"] == 0.0
        assert r["basis_remaining"] == 5000.00
        assert r["taxable_to_1040"] == 0.00
        assert r["pro_rata_triggered"] is False

    def test_ratio_capped_at_one(self):
        # More basis than total value (degenerate) → ratio cannot exceed 1.0.
        r = compute_form_8606(0, 100, 0, 0, 50)
        assert r["line_10_ratio"] == 1.0
        assert r["taxable_conversion"] == 0.00
        assert r["basis_remaining"] == 50.00  # leftover basis after a tax-free conversion

    def test_current_year_nondeductible_adds_to_basis(self):
        # $4K new nondeductible contribution + $3K prior basis = $7K line 3.
        r = compute_form_8606(4000, 3000, 0, 0, 7000)
        assert r["line_3_total_basis"] == 7000.00
        assert r["line_10_ratio"] == 1.0
        assert r["taxable_conversion"] == 0.00

    def test_distribution_pro_rata(self):
        # $10K basis, $90K pre-tax, take a $20K distribution (no conversion).
        # ratio = 10000/110000 = 0.090909...; nontaxable dist = 20000*ratio = 1818.18;
        # taxable distribution = 20000 - 1818.18 = 18181.82.
        r = compute_form_8606(0, 10000, 90000, 20000, 0)
        assert r["line_12_nontaxable_distributions"] == 1818.18
        assert r["taxable_distributions"] == 18181.82
        assert r["taxable_to_1040"] == 18181.82
        assert r["basis_remaining"] == 8181.82
        assert r["pro_rata_triggered"] is False  # no conversion

    def test_contrib_by_deadline_reduces_line_5(self):
        # line 4 (next-year contributions counted on line 1) reduces line 5 but
        # NOT line 14: line 14 = line 3 - line 13, so basis still reflects line 1.
        r = compute_form_8606(2000, 3000, 0, 0, 0, contrib_made_by_deadline=2000)
        assert r["line_3_total_basis"] == 5000.00
        assert r["line_5_basis_net_of_deadline"] == 3000.00
        assert r["basis_remaining"] == 5000.00

    def test_negative_input_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_form_8606(0, -100, 0, 0, 0)

    def test_bool_input_raises(self):
        with pytest.raises(ValueError, match="must be numeric"):
            compute_form_8606(0, 7000, True, 0, 7000)

    def test_string_amount_coerced(self):
        # Money-formatted strings are coerced like the other keystones.
        r = compute_form_8606(0, "$7,000", "$50,000", 0, "7000")
        assert r["taxable_conversion"] == 6140.35

    def test_non_numeric_string_raises(self):
        with pytest.raises(ValueError, match="not numeric"):
            compute_form_8606(0, "lots", 0, 0, 7000)

    def test_disaster_distribution_carves_out_from_1040(self):
        # $10K basis, $90K pre-tax, $20K distribution of which $5K is allocable to a
        # qualified disaster distribution (Form 8915-F line 15b). Pro-rata still applies
        # to the full $20K on line 12, but only the non-disaster taxable portion
        # (line 15c = 15a - 15b) flows to 1040 4b.
        r = compute_form_8606(0, 10000, 90000, 20000, 0,
                              disaster_repayable_amount=5000)
        # Line 12 unchanged: pro-rata on the full $20K → $1,818.18 nontaxable
        assert r["line_12_nontaxable_distributions"] == 1818.18
        # 15a pre-carve-out: 20000 - 1818.18 = 18181.82
        assert r["line_15a_taxable_pre_disaster"] == 18181.82
        assert r["line_15b_disaster_repayable"] == 5000.00
        # 15c → 1040 4b: 18181.82 - 5000 = 13181.82
        assert r["line_15c_taxable_to_1040"] == 13181.82
        assert r["taxable_distributions"] == 13181.82
        assert r["taxable_to_1040"] == 13181.82
        # Basis carryforward unaffected by the 15b carve-out.
        assert r["basis_remaining"] == 8181.82

    def test_disaster_amount_default_zero_keeps_old_behavior(self):
        # When 15b is not passed, line_15_taxable_distributions == 15a (pre-existing).
        r = compute_form_8606(0, 10000, 90000, 20000, 0)
        assert r["line_15a_taxable_pre_disaster"] == 18181.82
        assert r["line_15c_taxable_to_1040"] == 18181.82
        assert r["taxable_distributions"] == 18181.82

    def test_contrib_by_deadline_exceeding_line_1_raises(self):
        # Codex's example: line 4 must not exceed line 1. Passing 5000 when line 1 is
        # only 1000 would silently shrink line 5 and over-tax $4K of legitimate basis;
        # the keystone surfaces it as a data error instead.
        with pytest.raises(ValueError, match="cannot exceed"):
            compute_form_8606(1000, 10000, 0, 0, 10000,
                              contrib_made_by_deadline=5000)

    def test_contrib_by_deadline_equal_to_line_1_allowed(self):
        # Boundary: line 4 == line 1 is the IRS-allowed extreme (the WHOLE current-year
        # contribution was made by the April-15 deadline of the following year).
        r = compute_form_8606(2000, 3000, 0, 0, 0, contrib_made_by_deadline=2000)
        assert r["line_5_basis_net_of_deadline"] == 3000.00
        assert r["basis_remaining"] == 5000.00

    def test_disaster_irs_example_all_line7_is_disaster(self):
        # IRS-style example (per Form 8606 instructions for line 15b): a single $35K
        # distribution that is ENTIRELY a qualified disaster distribution, alongside
        # $5,500 of basis and a $3,500 year-end FMV.
        #   line 9 = 35000 + 3500 = 38500; ratio = 5500/38500 ≈ 0.14286
        #   line 12 = 35000 × 0.14286 ≈ 5000.00 (nontaxable)
        #   line 15a = 35000 - 5000 = 30000.00 (TAXABLE portion — Codex's "$30,000")
        # When ALL of line 7 is disaster, line 15b == line 15a → line 15c = 0
        # (nothing flows to 1040 4b this year; the full $30K taxable amount is reported on
        # Form 8915-F and amortized over 3 years instead).
        # First pass with no disaster carve-out to read line 15a:
        probe = compute_form_8606(0, 5500, 3500, 35000, 0)
        assert probe["line_15a_taxable_pre_disaster"] == 30000.00
        # Now pass that as line 15b (the correct post-pro-rata taxable amount):
        r = compute_form_8606(0, 5500, 3500, 35000, 0,
                              disaster_repayable_amount=30000)
        assert r["line_15b_disaster_repayable"] == 30000.00
        assert r["line_15c_taxable_to_1040"] == 0.00
        assert r["taxable_to_1040"] == 0.00
        # Basis carryforward is the line-3 minus nontaxable share; unaffected by 15b.
        assert r["basis_remaining"] == 500.00  # 5500 prior basis - 5000 returned

    def test_disaster_amount_exceeding_15a_clamps_to_zero(self):
        # Defensive: if 15b is misreported larger than 15a, 15c floors at $0
        # (cannot send negative tax to 1040 4b).
        r = compute_form_8606(0, 10000, 90000, 20000, 0,
                              disaster_repayable_amount=50000)
        assert r["line_15c_taxable_to_1040"] == 0.00


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
