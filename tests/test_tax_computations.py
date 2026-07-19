#!/usr/bin/env python3
"""Comprehensive tests for tax_computations.py.

Expected values are hand-computed from IRS worksheets and publications.
Tolerance: $0.01 (exact match after rounding to 2 decimal places).
"""

import json
import math
import pytest
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.tax_computations import (
    _coerce_numeric,
    compute_bracket_tax,
    compute_qdcg_tax,
    compute_se_tax,
    compute_additional_medicare,
    compute_excess_sdi,
    compute_excess_ss_withholding,
    compute_ftc_limitation,
    compute_child_tax_credit,
    compute_education_credits,
    compute_qbi_deduction,
    compute_niit,
    compute_form_8606,
    compute_taxable_ss,
    compute_form_2210,
    compute_schedule_a,
    compute_ca_itemized,
    compute_schedule_1a,
    compute_charitable_non_itemizer,
    compute_form_6251,
    compute_form_8801_credit,
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


class TestComputeTaxableSS:
    """1040 line 6b Social Security Benefits Worksheet (Pub 915).
    Every expected value hand-worked through worksheet lines 1-18."""

    def test_mfj_mid_tier(self):
        """MFJ, $24K benefits, $40K other. Provisional 52000; excess over
        32000 base = 20000; tier1 = 12000 -> min(12000, 6000) = 6000;
        over-second 8000 x 85% = 6800 -> 12800 (under the 20400 cap)."""
        r = compute_taxable_ss(24000, 40000, tax_exempt_interest=0, filing_status="MFJ")
        assert r["provisional_income"] == 52000.00
        assert r["taxable_ss"] == 12800.00

    def test_mfj_below_base_zero(self):
        """Provisional 30000 <= 32000 base -> nothing taxable."""
        r = compute_taxable_ss(20000, 20000, tax_exempt_interest=0, filing_status="MFJ")
        assert r["taxable_ss"] == 0.00

    def test_mfj_85_pct_cap_binds(self):
        """High income: worksheet line 15 = 151350 but the 85%-of-benefits
        cap (0.85 x 30000 = 25500) binds."""
        r = compute_taxable_ss(30000, 200000, tax_exempt_interest=0, filing_status="MFJ")
        assert r["taxable_ss"] == 25500.00
        assert r["taxable_fraction"] == 0.85

    def test_mfj_fifty_pct_region(self):
        """Provisional 36000, excess 4000 within the 12000 tier-1 span ->
        taxable = half the excess = 2000."""
        r = compute_taxable_ss(12000, 30000, tax_exempt_interest=0, filing_status="MFJ")
        assert r["taxable_ss"] == 2000.00

    def test_single_mid_tier(self):
        """Single, $20K benefits, $28K other: provisional 38000; excess 13000;
        tier1 9000 -> min(10000, 4500) = 4500; over 4000 x 85% = 3400 -> 7900."""
        r = compute_taxable_ss(20000, 28000, tax_exempt_interest=0, filing_status="Single")
        assert r["taxable_ss"] == 7900.00

    def test_tax_exempt_interest_counts(self):
        """Muni interest raises provisional income even though untaxed —
        20000 other + 8000 TEI matches the 28000-other case exactly."""
        r = compute_taxable_ss(20000, 20000, tax_exempt_interest=8000,
                               filing_status="Single")
        assert r["taxable_ss"] == 7900.00

    def test_adjustments_reduce_provisional(self):
        """13000 of above-the-line adjustments pulls provisional to exactly
        the 25000 base -> 0 taxable."""
        r = compute_taxable_ss(20000, 28000, adjustments=13000,
                               tax_exempt_interest=0, filing_status="Single")
        assert r["taxable_ss"] == 0.00

    def test_qss_uses_single_base(self):
        """Codex P1: Pub 915 groups QSS with Single/HoH at the $25,000 base —
        NOT the joint $32,000. Worksheet: provisional 52000; excess 27000;
        tier1 9000 -> min(12000, 4500) = 4500; over 18000 x 85% = 15300 ->
        19800 (under the 20400 cap)."""
        r = compute_taxable_ss(24000, 40000, tax_exempt_interest=0, filing_status="QSS")
        assert r["base_amount"] == 25000.0
        assert r["taxable_ss"] == 19800.00

    def test_qss_taxable_between_single_and_joint_bases(self):
        """The exact failure the joint base caused: QSS provisional 30000 is
        ABOVE the 25000 single base -> taxable (2500), not $0."""
        r = compute_taxable_ss(20000, 20000, tax_exempt_interest=0, filing_status="QSS")
        # excess 5000 within the 9000 span -> half = 2500
        assert r["taxable_ss"] == 2500.00

    def test_mfs_lived_together_85pct_path(self):
        """$0 base: lesser of 85% x provisional (34000) and 85% x benefits
        (17000) -> 17000."""
        r = compute_taxable_ss(20000, 30000, tax_exempt_interest=0, filing_status="MFS",
                               lived_apart_from_spouse_all_year=False)
        assert r["taxable_ss"] == 17000.00
        assert r["path"] == "mfs_lived_together_85pct"

    def test_mfs_lived_apart_uses_single_worksheet(self):
        r = compute_taxable_ss(20000, 28000, tax_exempt_interest=0, filing_status="MFS",
                               lived_apart_from_spouse_all_year=True)
        assert r["taxable_ss"] == 7900.00
        assert r["base_amount"] == 25000.0

    def test_mfs_missing_lived_apart_flag_raises(self):
        """The flag is load-bearing (Pub 915) — silently defaulting either way
        moves real dollars."""
        with pytest.raises(ValueError, match="lived_apart_from_spouse_all_year"):
            compute_taxable_ss(20000, 30000, tax_exempt_interest=0, filing_status="MFS")

    def test_no_benefits_path(self):
        r = compute_taxable_ss(0, 50000, tax_exempt_interest=0, filing_status="Single")
        assert r["taxable_ss"] == 0.00
        assert r["path"] == "no_benefits"

    def test_noncanonical_status_raises(self):
        with pytest.raises(ValueError, match="Single/MFJ/MFS/HoH/QSS"):
            compute_taxable_ss(20000, 30000, tax_exempt_interest=0, filing_status="Married Filing Jointly")

    def test_nan_input_raises(self):
        with pytest.raises(ValueError, match="finite"):
            compute_taxable_ss(float("nan"), 30000, tax_exempt_interest=0,
                               filing_status="Single")

    def test_bool_input_raises(self):
        with pytest.raises(ValueError, match="bool"):
            compute_taxable_ss(True, 30000, tax_exempt_interest=0, filing_status="Single")

    def test_negative_tei_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_taxable_ss(20000, 30000, tax_exempt_interest=-1,
                               filing_status="Single")

    def test_lump_sum_flags_election_warning(self):
        """Codex P2 round 2: prior-year benefits in box 5 make the Pub 915
        lump-sum election available — it can only LOWER tax and is not
        modeled, so the result must warn that line 6b is an upper bound."""
        r = compute_taxable_ss(24000, 40000, tax_exempt_interest=0, filing_status="MFJ",
                               lump_sum_prior_year_benefits=6000)
        assert r["lump_sum_election_available"] is True
        assert "lump-sum" in r["warning"]
        assert r["taxable_ss"] == 12800.00  # numbers unchanged — flag only

    def test_no_lump_sum_no_warning(self):
        r = compute_taxable_ss(24000, 40000, tax_exempt_interest=0, filing_status="MFJ")
        assert r["lump_sum_election_available"] is False
        assert "warning" not in r

    def test_negative_lump_sum_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_taxable_ss(24000, 40000, tax_exempt_interest=0, filing_status="MFJ",
                               lump_sum_prior_year_benefits=-1)

    def test_missing_filing_status_raises(self):
        """Codex P2 round 4: no MFJ default — a Single filer between the $25K
        and $32K bases would silently get $0 taxable."""
        with pytest.raises(ValueError, match="filing_status is required"):
            compute_taxable_ss(20000, 20000, tax_exempt_interest=0)

    def test_missing_tax_exempt_interest_raises(self):
        """Codex P2 round 12: the skill calls line 2a a required input — a
        silent 0 default would let an accidental omission understate line 6b
        for exactly the muni-holding taxpayers the input exists for."""
        with pytest.raises(ValueError, match="tax_exempt_interest is required"):
            compute_taxable_ss(20000, 20000, filing_status="Single")

    def test_excluded_income_addbacks_raise_provisional(self):
        """Codex P2 round 4: Pub 915 line-5-region add-backs (FEIE, excluded
        adoption benefits, territory income) re-enter provisional income.
        Single, $20K benefits, $10K other + $18K FEIE addback = provisional
        38000 — identical to the $28K-other case -> 7900 (omitting the
        addback would have been $1,500: excess 3000 within the tier ->
        half)."""
        r = compute_taxable_ss(20000, 10000, tax_exempt_interest=0, filing_status="Single",
                               excluded_income_addbacks=18000)
        assert r["provisional_income"] == 38000.00
        assert r["taxable_ss"] == 7900.00

    def test_negative_addbacks_raise(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_taxable_ss(20000, 20000, tax_exempt_interest=0, filing_status="Single",
                               excluded_income_addbacks=-1)


class TestComputeExcessSSWithholding:
    """Schedule 3 line 11 — excess Social Security withholding, per person,
    wage-aware (Topic 608)."""

    def test_two_employers_over_max(self):
        """2025 wage base 176100 -> max 10918.20; correctly-withheld 7000 +
        6000 = 13000 (wages support both) -> 2081.80 refundable credit."""
        r = compute_excess_ss_withholding([7000, 6000], [120000, 100000],
                                          ss_wage_base=176100)
        assert r["max_ss_tax"] == 10918.20
        assert r["excess_credit"] == 2081.80
        assert r["employer_over_withheld"] is False

    def test_below_base_wages_never_creditable(self):
        """Codex P2 round 6: two \\$50K W-2s each erroneously withholding
        \\$6000 exceed the annual max, but wages support only 3100 each —
        the whole 5800 overage is an employer error, credit \\$0."""
        r = compute_excess_ss_withholding([6000, 6000], [50000, 50000],
                                          ss_wage_base=176100)
        assert r["excess_credit"] == 0.0
        assert r["employer_error_amount"] == 5800.00
        assert r["employer_over_withheld"] is True

    def test_single_employer_over_is_flagged_not_credited(self):
        """One employer over-withholding is corrected via the employer — the
        return gets no credit, but the situation is surfaced."""
        r = compute_excess_ss_withholding([12000], [176100], ss_wage_base=176100)
        assert r["excess_credit"] == 0.0
        assert r["employer_error_amount"] == 1081.80
        assert r["employer_over_withheld"] is True

    def test_multi_employer_individual_overage_clamped(self):
        """Codex P2 (IRS Topic 608): with [12000, 1000], the 1081.80 that the
        first employer withheld ABOVE what full-base wages support is an
        employer error — only the clamped total counts: 10918.20 + 1000 - max
        = 1000 creditable, NOT 2081.80."""
        r = compute_excess_ss_withholding([12000, 1000], [176100, 20000],
                                          ss_wage_base=176100)
        assert r["excess_credit"] == 1000.00
        assert r["employer_error_amount"] == 1081.80
        assert r["employer_over_withheld"] is True

    def test_two_employers_under_max_no_credit(self):
        r = compute_excess_ss_withholding([5000, 4000], [90000, 70000],
                                          ss_wage_base=176100)
        assert r["excess_credit"] == 0.0
        assert r["employer_over_withheld"] is False

    def test_empty_list_is_zero(self):
        r = compute_excess_ss_withholding([], [], ss_wage_base=176100)
        assert r["excess_credit"] == 0.0
        assert r["num_employers"] == 0

    def test_wage_list_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="must parallel"):
            compute_excess_ss_withholding([7000, 6000], [120000],
                                          ss_wage_base=176100)

    def test_none_wage_base_raises(self):
        with pytest.raises(ValueError, match="wage_base cannot be None"):
            compute_excess_ss_withholding([7000, 6000], [120000, 100000],
                                          ss_wage_base=None)

    def test_nan_entry_raises(self):
        with pytest.raises(ValueError, match="finite non-negative"):
            compute_excess_ss_withholding([7000, float("nan")], [120000, 100000],
                                          ss_wage_base=176100)

    def test_bool_entry_raises(self):
        with pytest.raises(ValueError, match="bool"):
            compute_excess_ss_withholding([7000, True], [120000, 100000],
                                          ss_wage_base=176100)

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            compute_excess_ss_withholding(13000, [120000], ss_wage_base=176100)


class TestComputeExcessSDI:
    """CA Form 540 line 74 — excess SDI, per person, cache-driven cap."""

    def test_pre_2024_two_employers_over_cap(self):
        """TY2023: 1.1% x 153164 = 1684.80 max; correctly-withheld 1200 + 900
        = 2100 (wages support both) -> 415.20 credit."""
        r = compute_excess_sdi([1200, 900], [153164, 100000], sdi_rate=0.011,
                               sdi_wage_limit=153164, tax_year=2023)
        assert r["max_sdi"] == 1684.80
        assert r["excess_credit"] == 415.20

    def test_below_limit_wages_never_creditable(self):
        """Codex P2 round 6 (SDI side): erroneous over-withholding on
        below-limit wages exceeds the annual max without any creditable
        excess — wages support only 550 + 550."""
        r = compute_excess_sdi([1000, 1000], [50000, 50000], sdi_rate=0.011,
                               sdi_wage_limit=153164, tax_year=2023)
        assert r["excess_credit"] == 0.0
        assert r["employer_error_amount"] == 900.00

    def test_no_wage_ceiling_means_no_credit(self):
        """SB 951 removed the ceiling for TY2024+ (wage_limit None in the
        cache) — there is no per-person maximum, so no excess-SDI credit;
        flagged not_applicable instead of computing against a stale cap."""
        r = compute_excess_sdi([3000, 2500], [250000, 200000], sdi_rate=0.012,
                               sdi_wage_limit=None, tax_year=2025)
        assert r["not_applicable"] is True
        assert r["excess_credit"] == 0.0
        assert "SB 951" in r["note"]

    def test_post_2024_employer_error_still_reported(self):
        """Codex P2 round 9: no ceiling != no employer errors — TY2024+ an
        employer should withhold exactly wages x rate; $3000 on $100K at 1.2%
        leaves an $1800 recover-from-employer amount even though the credit
        path is not applicable."""
        r = compute_excess_sdi([3000], [100000], sdi_rate=0.012,
                               sdi_wage_limit=None, tax_year=2025)
        assert r["not_applicable"] is True
        assert r["excess_credit"] == 0.0
        assert r["employer_error_amount"] == 1800.00
        assert r["employer_over_withheld"] is True

    def test_stale_cap_in_2024_plus_cache_is_ignored(self):
        """Codex P2 round 3: the YEAR decides applicability — a stale pre-2024
        wage limit left in a TY2024+ cache must not invent a line 74 credit."""
        r = compute_excess_sdi([2000, 2000], [200000, 200000], sdi_rate=0.011,
                               sdi_wage_limit=153164, tax_year=2024)
        assert r["not_applicable"] is True
        assert r["excess_credit"] == 0.0
        assert "IGNORED" in r["note"]

    def test_pre_2024_missing_cap_fails_closed(self):
        """Codex P2 round 2: for a pre-2024 year the ceiling EXISTED — a None
        wage limit means the cache is unfetched, and treating it as
        no-ceiling would silently drop a refundable credit. Hard error."""
        with pytest.raises(ValueError, match="missing from tax_law"):
            compute_excess_sdi([1200, 900], [153164, 100000], sdi_rate=0.011,
                               sdi_wage_limit=None, tax_year=2023)

    def test_bad_tax_year_raises(self):
        with pytest.raises(ValueError, match="4-digit year"):
            compute_excess_sdi([1200, 900], [153164, 100000], sdi_rate=0.011,
                               sdi_wage_limit=None, tax_year=True)
        with pytest.raises(ValueError, match="4-digit year"):
            compute_excess_sdi([1200, 900], [153164, 100000], sdi_rate=0.011,
                               sdi_wage_limit=None, tax_year=24)

    def test_single_employer_over_flagged(self):
        r = compute_excess_sdi([2000], [153164], sdi_rate=0.011,
                               sdi_wage_limit=153164, tax_year=2023)
        assert r["excess_credit"] == 0.0
        assert r["employer_error_amount"] == 315.20
        assert r["employer_over_withheld"] is True

    def test_multi_employer_individual_overage_clamped(self):
        """Codex P2 (EDD DE 1964): [2000, 200] — the 315.20 one employer
        withheld above what full-limit wages support is recovered from that
        employer; creditable = 1684.80 + 200 - max = 200."""
        r = compute_excess_sdi([2000, 200], [153164, 20000], sdi_rate=0.011,
                               sdi_wage_limit=153164, tax_year=2023)
        assert r["excess_credit"] == 200.00
        assert r["employer_error_amount"] == 315.20

    def test_wage_list_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="must parallel"):
            compute_excess_sdi([1200, 900], [153164], sdi_rate=0.011,
                               sdi_wage_limit=153164, tax_year=2023)

    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError, match="finite positive"):
            compute_excess_sdi([1200, 900], [153164, 100000], sdi_rate=0,
                               sdi_wage_limit=153164, tax_year=2023)

    def test_nan_limit_raises(self):
        with pytest.raises(ValueError, match="finite positive"):
            compute_excess_sdi([1200, 900], [153164, 100000], sdi_rate=0.011,
                               sdi_wage_limit=float("nan"), tax_year=2023)


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


# ============================================================
# Form 2210 — underpayment of estimated tax (1040 line 38)
# ============================================================

# §6621 quarterly rate fixture fully covering Apr 15 2025 → Apr 15 2026
# (including the Apr 1 2026 reset, so no coverage-gap note fires).
_2210_RATES = [
    {"effective_from": "2025-01-01", "annual_rate": 0.08},
    {"effective_from": "2025-10-01", "annual_rate": 0.07},
    {"effective_from": "2026-01-01", "annual_rate": 0.07},
    {"effective_from": "2026-04-01", "annual_rate": 0.07},
]


class TestForm2210:
    def test_de_minimis_under_1000(self):
        """§6654(e)(1): balance after withholding under $1,000 → no penalty.
        Estimated payments do NOT enter this test (2210 line 7 is tax minus
        withholding only)."""
        r = compute_form_2210(1900, 1000, "Single", 2025)
        assert r["no_penalty"] and "de minimis" in r["no_penalty_reason"]

    def test_de_minimis_boundary_exactly_1000_owes(self):
        r = compute_form_2210(2000, 1000, "Single", 2025, prior_year_tax=2000,
                              prior_year_agi=50000,
                              underpayment_rates=_2210_RATES)
        assert not r["no_penalty"]

    def test_prior_year_zero_exception(self):
        """§6654(e)(2): explicit $0 prior-year liability → no penalty."""
        r = compute_form_2210(20000, 1000, "Single", 2025, prior_year_tax=0)
        assert r["no_penalty"] and "6654(e)(2)" in r["no_penalty_reason"]

    def test_withholding_meets_safe_harbor(self):
        """Prior-based harbor 100% × 10,000 = 10,000 ≤ withholding."""
        r = compute_form_2210(20000, 10000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000)
        assert r["no_penalty"]
        assert r["required_annual_payment"] == 10000.00

    def test_required_is_lesser_of_90_and_prior(self):
        """90% of 10,000 = 9,000 < 100% of 20,000 prior."""
        r = compute_form_2210(10000, 2000, "Single", 2025,
                              prior_year_tax=20000, prior_year_agi=100000,
                              underpayment_rates=_2210_RATES)
        assert r["required_annual_payment"] == 9000.00

    def test_110_pct_factor_above_150k_agi(self):
        r = compute_form_2210(30000, 2000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=200000,
                              underpayment_rates=_2210_RATES)
        # min(27000, 1.10 × 10000 = 11000)
        assert r["required_annual_payment"] == 11000.00

    def test_mfs_110_threshold_is_75k(self):
        r_mfs = compute_form_2210(30000, 2000, "MFS", 2025,
                                  prior_year_tax=10000, prior_year_agi=80000,
                                  underpayment_rates=_2210_RATES)
        assert r_mfs["required_annual_payment"] == 11000.00
        r_single = compute_form_2210(30000, 2000, "Single", 2025,
                                     prior_year_tax=10000, prior_year_agi=80000,
                                     underpayment_rates=_2210_RATES)
        assert r_single["required_annual_payment"] == 10000.00

    def test_hand_computed_penalty_even_withholding(self):
        """Required 10,000 (prior harbor); wh 8,000 → each installment
        underpaid $500 with the §6654(b)(3) FIFO cascade. TY2025 Q2 is due
        Jun 16 (§7503 — Jun 15 is a Sunday), matching the printed form.
        Hand-worked:
          Q1 500 × 8% × 62/365 (Apr 15→Jun 16)          =  6.7945
          Q2 1000 × 8% × 91/365 (Jun 16→Sep 15)         = 19.9452
          Q3 1500 × [8% × 16/365 + 7% × 92/365
                     + 7% × 14/365] (Sep 15→Jan 15)     = 35.7534
          Q4 2000 × 7% × 90/365 (Jan 15→Apr 15)         = 34.5205
          total                                          = 97.0137 → 97.01"""
        r = compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              underpayment_rates=_2210_RATES)
        assert r["penalty"] == 97.01
        assert r["penalty_computed"]
        assert [q["underpayment"] for q in r["quarters"]] == \
            [500.0, 1000.0, 1500.0, 2000.0]
        assert all(q["required_installment"] == 2500.0 for q in r["quarters"])

    def test_timely_estimate_cures_all_quarters(self):
        """A $2,000 estimate on Apr 15 + even withholding covers every
        installment through the credit carryforward → $0 penalty."""
        r = compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              estimated_payments=[
                                  {"date": "2025-04-15", "amount": 2000}],
                              underpayment_rates=_2210_RATES)
        assert r["penalty"] == 0.0
        assert [q["underpayment"] for q in r["quarters"]] == [0.0, 0.0, 0.0, 0.0]

    def test_late_estimate_accrues_until_paid(self):
        """The same $2,000 paid Jun 1 leaves Q1's $500 accruing 47 days at 8%:
        500 × 0.08 × 47/365 = 5.1507 → 5.15."""
        r = compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              estimated_payments=[
                                  {"date": "2025-06-01", "amount": 2000}],
                              underpayment_rates=_2210_RATES)
        assert r["penalty"] == 5.15
        assert [q["underpayment"] for q in r["quarters"]] == [500.0, 0.0, 0.0, 0.0]

    def test_leap_year_denominator_366(self):
        """TY2023's Q4 installment is due Jan 16 2024 (§7503 — Jan 15 IS MLK
        Monday that year) and accrues to Apr 15 2024 over 90 days in a leap
        year: 500 × 0.08 × 90/366 = 9.8360 (365 would give 9.86; the
        statutory Jan 15 start would give 9.95)."""
        rates = [{"effective_from": "2023-01-01", "annual_rate": 0.08}]
        r = compute_form_2210(20000, 8000, "Single", 2023,
                              prior_year_tax=10000, prior_year_agi=100000,
                              estimated_payments=[
                                  {"date": "2023-04-15", "amount": 500},
                                  {"date": "2023-06-15", "amount": 500},
                                  {"date": "2023-09-15", "amount": 500}],
                              underpayment_rates=rates)
        # Q1-Q3 covered (early estimates + deemed withholding on the rolled
        # due dates); only Q4's 500 remains → accrues 2024-01-16 → 2024-04-15
        # = 90 days, leap denominator: 500×0.08×90/366 = 9.8360
        assert r["penalty"] == 9.84

    def test_rates_unfetched_degrades_loudly(self):
        r = compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000)
        assert r["penalty"] is None and not r["penalty_computed"]
        assert not r["no_penalty"]
        assert any("underpayment_rates is unfetched" in n for n in r["notes"])
        # quarterly underpayments still reported
        assert [q["underpayment"] for q in r["quarters"]] == \
            [500.0, 1000.0, 1500.0, 2000.0]

    def test_rates_starting_after_span_hard_error(self):
        with pytest.raises(ValueError, match="rate timeline must cover"):
            compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              underpayment_rates=[
                                  {"effective_from": "2025-07-01",
                                   "annual_rate": 0.08}])

    def test_rate_timeline_gap_note_when_fetched_early(self):
        """Rates fetched only through mid-TY assume the last rate persists —
        noted, not silent."""
        r = compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              underpayment_rates=[
                                  {"effective_from": "2025-01-01",
                                   "annual_rate": 0.08}])
        assert r["penalty_computed"]
        assert any("assume that rate persists" in n for n in r["notes"])

    def test_missing_prior_year_degrades_to_90pct(self):
        r = compute_form_2210(20000, 8000, "Single", 2025,
                              underpayment_rates=_2210_RATES)
        assert r["required_annual_payment"] == 18000.00
        assert any("90%-of-current test only" in n for n in r["notes"])

    def test_missing_prior_agi_notes_possible_understatement(self):
        r = compute_form_2210(30000, 2000, "Single", 2025,
                              prior_year_tax=10000,
                              underpayment_rates=_2210_RATES)
        assert r["required_annual_payment"] == 10000.00  # 100% factor assumed
        assert any("UNDERSTATED" in n for n in r["notes"])

    def test_matches_projection_safe_harbor(self):
        """The required-annual-payment math is duplicated from
        projection.compute_safe_harbor (circular-import constraint) — pin
        that they agree so they can't drift."""
        from agents.lib.projection import compute_safe_harbor
        for tax, prior, agi, fs in [
                (20000, 10000, 100000, "Single"),
                (30000, 10000, 200000, "Single"),
                (30000, 10000, 80000, "MFS"),
                (10000, 20000, 100000, "MFJ")]:
            sh = compute_safe_harbor(projected_tax=tax, prior_tax=prior,
                                     prior_agi=agi, filing_status=fs,
                                     withholding=0.0, estimates_paid=0.0)
            r = compute_form_2210(tax, 0, fs, 2025, prior_year_tax=prior,
                                  prior_year_agi=agi,
                                  underpayment_rates=_2210_RATES)
            assert r["required_annual_payment"] == sh.required_annual

    def test_invalid_inputs_rejected(self):
        with pytest.raises(ValueError):
            compute_form_2210(float("nan"), 0, "Single", 2025)
        with pytest.raises(ValueError):
            compute_form_2210(20000, -1, "Single", 2025)
        with pytest.raises(ValueError):
            compute_form_2210(20000, 0, "Married Filing Jointly", 2025)
        with pytest.raises(ValueError):
            compute_form_2210(True, 0, "Single", 2025)
        with pytest.raises(ValueError):
            compute_form_2210(20000, 0, "Single", 2025.0)
        with pytest.raises(ValueError, match="date"):
            compute_form_2210(20000, 0, "Single", 2025,
                              estimated_payments=[
                                  {"date": "June 1", "amount": 100}])
        with pytest.raises(ValueError, match="amount"):
            compute_form_2210(20000, 0, "Single", 2025,
                              estimated_payments=[
                                  {"date": "2025-06-01", "amount": -5}])
        with pytest.raises(ValueError, match="duplicate"):
            compute_form_2210(20000, 0, "Single", 2025,
                              underpayment_rates=[
                                  {"effective_from": "2025-01-01", "annual_rate": 0.08},
                                  {"effective_from": "2025-01-01", "annual_rate": 0.07}])
        with pytest.raises(ValueError, match="annual_rate"):
            compute_form_2210(20000, 0, "Single", 2025,
                              underpayment_rates=[
                                  {"effective_from": "2025-01-01", "annual_rate": 1.5}])
        with pytest.raises(ValueError, match="annual_rate"):
            compute_form_2210(20000, 0, "Single", 2025,
                              underpayment_rates=[
                                  {"effective_from": "2025-01-01",
                                   "annual_rate": float("inf")}])

    def test_payment_after_apr15_does_not_extend_accrual(self):
        """§6654(b)(2): accrual stops at Apr 15 of tax_year+1 even when the
        retiring payment arrives later — a May payment must yield the same
        penalty as never paying (it retires the balance, not the clock)."""
        unpaid = compute_form_2210(20000, 8000, "Single", 2025,
                                   prior_year_tax=10000, prior_year_agi=100000,
                                   underpayment_rates=_2210_RATES)
        late = compute_form_2210(20000, 8000, "Single", 2025,
                                 prior_year_tax=10000, prior_year_agi=100000,
                                 estimated_payments=[
                                     {"date": "2026-05-20", "amount": 5000}],
                                 underpayment_rates=_2210_RATES)
        assert late["penalty"] == unpaid["penalty"] == 97.01

    def test_wrong_year_payment_date_hard_errors(self):
        """A payment dated before the tax year is a typo'd year that would
        fabricate day-one credit and UNDERSTATE the penalty — hard error."""
        with pytest.raises(ValueError, match="predates tax year"):
            compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              estimated_payments=[
                                  {"date": "2015-04-15", "amount": 2000}],
                              underpayment_rates=_2210_RATES)

    def test_payment_on_rolled_due_date_is_timely(self):
        """Self-review P1: TY2025 Q2 is due Jun 16 (Jun 15 is a Sunday) — a
        taxpayer paying on the IRS-printed dates owes NOTHING, not a phantom
        1-day penalty."""
        r = compute_form_2210(20000, 0, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              estimated_payments=[
                                  {"date": "2025-04-15", "amount": 2500},
                                  {"date": "2025-06-16", "amount": 2500},
                                  {"date": "2025-09-15", "amount": 2500},
                                  {"date": "2026-01-15", "amount": 2500}],
                              underpayment_rates=_2210_RATES)
        assert r["penalty"] == 0.0 and r["penalty_computed"]
        assert [q["underpayment"] for q in r["quarters"]] == [0.0, 0.0, 0.0, 0.0]

    def test_zero_rate_in_cache_hard_errors(self):
        """§6621 can never be 0 (short-term AFR + 3 points) — a 0 placeholder
        from a fetch bug would silently zero line 38 (the understating
        direction)."""
        with pytest.raises(ValueError, match="never be 0"):
            compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              underpayment_rates=[
                                  {"effective_from": "2025-01-01",
                                   "annual_rate": 0.0}])

    def test_fully_covered_return_needs_no_rates(self):
        """Timely payments → $0 penalty is computable WITHOUT a rate
        timeline; an unfetched cache must not degrade a return that owes
        nothing."""
        r = compute_form_2210(20000, 0, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              estimated_payments=[
                                  {"date": "2025-04-15", "amount": 10000}])
        assert r["penalty"] == 0.0 and r["penalty_computed"]

    def test_future_dated_payment_notes_forfeited_credit(self):
        """An off-by-one-year typo in the FUTURE direction can't understate
        (the payment simply earns nothing) but must be flagged, not silent."""
        r = compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              estimated_payments=[
                                  {"date": "2026-06-15", "amount": 2000}],
                              underpayment_rates=_2210_RATES)
        assert r["penalty"] == 97.01  # same as never paying
        assert any("cannot reduce this year's penalty" in n for n in r["notes"])

    def test_negative_line_4_refundable_credits_no_crash(self):
        """Codex P2: refundable credits can exceed line 24 → negative 2210
        line 4. The unconditional /tax-compute call must resolve to the
        de minimis no-penalty path, not crash on validation."""
        r = compute_form_2210(-1500, 0, "Single", 2025,
                              prior_year_tax=5000, prior_year_agi=60000)
        assert r["no_penalty"] and "de minimis" in r["no_penalty_reason"]

    def test_rates_missing_apr1_reset_noted(self):
        """Codex P2: coverage through Jan 1 still extrapolates Apr 1-15 at
        the Q1 rate — that must be noted, not silent."""
        r = compute_form_2210(20000, 8000, "Single", 2025,
                              prior_year_tax=10000, prior_year_agi=100000,
                              underpayment_rates=_2210_RATES[:3])
        assert r["penalty_computed"]
        assert any("assume that rate persists" in n for n in r["notes"])
        full = compute_form_2210(20000, 8000, "Single", 2025,
                                 prior_year_tax=10000, prior_year_agi=100000,
                                 underpayment_rates=_2210_RATES)
        assert not any("assume that rate persists" in n for n in full["notes"])


# ============================================================
# Schedule A (D1) — itemized deductions + computed election
# ============================================================

class TestScheduleA:
    def test_ca_persona_itemizes(self):
        """The target user: high CA SALT + mortgage. AGI 400K Single TY2025,
        OBBBA cap $40K: SALT = min(40000, 35000+12000) = 40,000; mortgage
        18,000 × 750/900 (Pub 936) = 15,000; charity 10,000; medical 5,000
        under the 30,000 floor → 0. Total 65,000 vs std 15,750."""
        r = compute_schedule_a(
            agi=400000, filing_status="Single", tax_year=2025,
            standard_deduction=15750, salt_cap=40000,
            medical_expenses=5000,
            state_local_income_taxes=35000, real_estate_taxes=12000,
            mortgage_interest=18000, average_mortgage_balance=900000,
            charitable_cash=10000, charitable_over_250_substantiated=True)
        assert r["salt_deductible"] == 40000.00
        assert r["salt_before_cap"] == 47000.00
        assert r["mortgage_deductible"] == 15000.00
        assert r["charitable_deductible"] == 10000.00
        assert r["medical_deductible"] == 0.00
        assert r["itemized_total"] == 65000.00
        assert r["use_itemized"] and r["election_margin"] == 49250.00

    def test_obbba_charitable_floor_ty2026(self):
        """OBBBA §70425: from TY2026 the first 0.5% of AGI of charitable
        gifts is non-deductible. AGI 200K, cash 5,000 → floor 1,000 →
        4,000 deductible. TY2025 → full 5,000."""
        r26 = compute_schedule_a(
            agi=200000, filing_status="Single", tax_year=2026,
            standard_deduction=16000, salt_cap=40400,
            charitable_cash=5000, charitable_over_250_substantiated=True)
        assert r26["charitable_deductible"] == 4000.00
        r25 = compute_schedule_a(
            agi=200000, filing_status="Single", tax_year=2025,
            standard_deduction=15750, salt_cap=40000,
            charitable_cash=5000, charitable_over_250_substantiated=True)
        assert r25["charitable_deductible"] == 5000.00

    def test_charitable_agi_caps_and_carryforward(self):
        """Pub 526 ordering: cash to 60% of AGI first; appreciated noncash
        to min(30% AGI, 50% overall remainder). AGI 100K, cash 70K,
        noncash 20K → cash 60K, noncash 0 (overall headroom exhausted),
        carryforward 30K."""
        r = compute_schedule_a(
            agi=100000, filing_status="Single", tax_year=2025,
            standard_deduction=15750, salt_cap=40000,
            charitable_cash=70000, charitable_noncash=20000,
            charitable_over_250_substantiated=True)
        assert r["charitable_deductible"] == 60000.00
        assert r["charitable_carryforward"] == 30000.00
        assert any("carries forward" in n for n in r["notes"])

    def test_mfs_spouse_itemizes_forces_itemizing(self):
        r = compute_schedule_a(
            agi=80000, filing_status="MFS", tax_year=2025,
            standard_deduction=15750, salt_cap=20000,
            state_local_income_taxes=5000, mfs_spouse_itemizes=True)
        assert r["itemized_total"] == 5000.00
        assert r["use_itemized"] is True  # despite margin being negative
        assert r["election_margin"] == -10750.00

    def test_sales_tax_election_uses_greater(self):
        r = compute_schedule_a(
            agi=90000, filing_status="Single", tax_year=2025,
            standard_deduction=15750, salt_cap=40000,
            state_local_income_taxes=4000, state_local_sales_taxes=6000,
            real_estate_taxes=1000)
        assert r["salt_before_cap"] == 7000.00
        assert any("sales-tax election" in n for n in r["notes"])

    def test_grandfathered_1m_limit(self):
        """Pre-Dec-16-2017 origination keeps the $1M limit — a 950K balance
        is NOT prorated."""
        r = compute_schedule_a(
            agi=300000, filing_status="MFJ", tax_year=2025,
            standard_deduction=31500, salt_cap=40000,
            mortgage_interest=30000, average_mortgage_balance=950000,
            mortgage_pre_dec_16_2017=True)
        assert r["mortgage_deductible"] == 30000.00

    def test_unknown_balance_notes_overstatement(self):
        r = compute_schedule_a(
            agi=300000, filing_status="MFJ", tax_year=2025,
            standard_deduction=31500, salt_cap=40000,
            mortgage_interest=30000)
        assert r["mortgage_deductible"] == 30000.00
        assert any("OVERSTATES" in n for n in r["notes"])

    def test_substantiation_gate_warnings(self):
        unknown = compute_schedule_a(
            agi=100000, filing_status="Single", tax_year=2025,
            standard_deduction=15750, salt_cap=40000, charitable_cash=1000)
        assert any("SUBSTANTIATION UNKNOWN" in n for n in unknown["notes"])
        missing = compute_schedule_a(
            agi=100000, filing_status="Single", tax_year=2025,
            standard_deduction=15750, salt_cap=40000, charitable_cash=1000,
            charitable_over_250_substantiated=False)
        assert any("SUBSTANTIATION GATE" in n for n in missing["notes"])

    def test_input_validation(self):
        with pytest.raises(ValueError):
            compute_schedule_a(float("nan"), "Single", 2025, 15750, 40000)
        with pytest.raises(ValueError):
            compute_schedule_a(100000, "Married Filing Jointly", 2025,
                               15750, 40000)
        with pytest.raises(ValueError):
            compute_schedule_a(100000, "Single", 2025.0, 15750, 40000)
        with pytest.raises(ValueError):
            compute_schedule_a(100000, "Single", 2025, 15750, 40000,
                               medical_expenses=-1)

    def test_negative_agi_floors_at_zero(self):
        """Self-review: an unclamped negative AGI would turn the 7.5%
        medical floor into a bonus and give charitable caps of 0 anyway —
        the floored base gives full medical, zero charitable capacity."""
        r = compute_schedule_a(
            agi=-20000, filing_status="Single", tax_year=2025,
            standard_deduction=15750, salt_cap=40000,
            medical_expenses=8000, charitable_cash=1000,
            charitable_over_250_substantiated=True)
        assert r["medical_deductible"] == 8000.00
        assert r["charitable_deductible"] == 0.00
        assert r["charitable_carryforward"] == 1000.00


class TestCaItemized:
    def test_ca_persona_no_salt_cap_but_income_tax_disallowed(self):
        """Same CA persona: income taxes (35K) disallowed entirely, property
        (12K) uncapped; mortgage 18K NOT prorated (balance 900K < $1M);
        charity 10K. Subtotal 40K; §17077 phase-out at threshold 244K:
        min(6% × 156,000 = 9,360, 80% × 40,000 = 32,000) = 9,360 →
        30,640. CA itemizes (std ~5,809) even though numbers differ from
        federal."""
        r = compute_ca_itemized(
            federal_agi=400000, ca_agi=400000, tax_year=2025,
            state_local_income_taxes=35000, real_estate_taxes=12000,
            mortgage_interest=18000, average_mortgage_balance=900000,
            charitable_cash=10000, medical_expenses=5000,
            ca_standard_deduction=5809, phaseout_threshold=244000)
        assert r["taxes_deductible"] == 12000.00
        assert r["mortgage_deductible"] == 18000.00
        assert r["charitable_deductible"] == 10000.00
        assert r["phaseout_reduction"] == 9360.00
        assert r["itemized_total"] == 30640.00
        assert r["use_itemized"] is True

    def test_phaseout_unfetched_degrades_loudly(self):
        r = compute_ca_itemized(
            federal_agi=400000, ca_agi=400000, tax_year=2025,
            real_estate_taxes=12000)
        assert r["phaseout_reduction"] == 0.0
        assert any("OVERSTATED" in n for n in r["notes"])

    def test_ca_cash_cap_is_50_pct(self):
        """CA never conformed to the TCJA 60% cash cap."""
        r = compute_ca_itemized(
            federal_agi=100000, ca_agi=100000, tax_year=2025,
            charitable_cash=55000, phaseout_threshold=244000)
        assert r["charitable_deductible"] == 50000.00
        assert any("50%" in n for n in r["notes"])

    def test_misc_2pct_floor_allowed_for_ca(self):
        r = compute_ca_itemized(
            federal_agi=100000, ca_agi=100000, tax_year=2025,
            misc_2pct_expenses=5000, phaseout_threshold=244000)
        assert r["misc_deductible"] == 3000.00

    def test_ca_mortgage_limit_1m(self):
        """A $2M balance prorates against $1M for CA (federal would use
        $750K — CA is more generous)."""
        r = compute_ca_itemized(
            federal_agi=500000, ca_agi=500000, tax_year=2025,
            mortgage_interest=60000, average_mortgage_balance=2000000,
            phaseout_threshold=244000)
        assert r["mortgage_deductible"] == 30000.00

    def test_phaseout_80pct_branch_and_exemptions(self):
        """Medical and investment interest are EXEMPT from the §17077
        phase-out subject amount. Huge AGI so 6% side is enormous → the
        80%-of-subject side binds: subject = taxes only (medical/inv
        exempt)."""
        r = compute_ca_itemized(
            federal_agi=2000000, ca_agi=2000000, tax_year=2025,
            medical_expenses=200000, investment_interest=10000,
            real_estate_taxes=20000, phaseout_threshold=244000)
        # medical deductible = 200000 - 7.5%×2M = 50,000 (exempt)
        assert r["medical_deductible"] == 50000.00
        # subject = 20,000 (taxes only) → phaseout = 80% × 20,000 = 16,000
        assert r["phaseout_reduction"] == 16000.00
        assert r["itemized_total"] == 50000.00 + 10000.00 + 20000.00 - 16000.00

    def test_ca_floors_anchor_on_federal_agi(self):
        """Review P1: Schedule CA Part II anchors the medical/charitable/
        misc floors on FEDERAL AGI (1040 line 11), not CA AGI. The classic
        CA retiree (SS subtracted → CA AGI < federal): fed 100K / CA 60K,
        medical 9,000 → 9,000 − 7.5%×100,000 = 1,500 (a CA-AGI base would
        overstate to 4,500)."""
        r = compute_ca_itemized(
            federal_agi=100000, ca_agi=60000, tax_year=2025,
            medical_expenses=9000, phaseout_threshold=244000)
        assert r["medical_deductible"] == 1500.00
        cap = compute_ca_itemized(
            federal_agi=100000, ca_agi=60000, tax_year=2025,
            charitable_cash=55000, phaseout_threshold=244000)
        assert cap["charitable_deductible"] == 50000.00  # 50% of FEDERAL AGI

    def test_ca_mfs_spouse_itemizes_forces_itemizing(self):
        """Review P2: CA mirrors §63(c)(6)(A) — MFS/RDP-separate with an
        itemizing spouse cannot take the CA standard deduction."""
        r = compute_ca_itemized(
            federal_agi=80000, ca_agi=80000, tax_year=2025,
            real_estate_taxes=2000, ca_standard_deduction=5809,
            phaseout_threshold=244000, mfs_spouse_itemizes=True)
        assert r["use_itemized"] is True
        assert r["election_margin"] == 2000.00 - 5809.00

    def test_obbba_floor_joins_carryforward_when_cap_exceeded(self):
        """Review P2 — §170(d)(1)(C): when the percentage cap is exceeded,
        the floor-disallowed slice ALSO carries forward. TY2026, AGI 100K,
        cash 70K: deductible 60,000 − 500 = 59,500; carryforward 10,000 +
        500 = 10,500 (not 10,000)."""
        r = compute_schedule_a(
            agi=100000, filing_status="Single", tax_year=2026,
            standard_deduction=16000, salt_cap=40400,
            charitable_cash=70000, charitable_over_250_substantiated=True)
        assert r["charitable_deductible"] == 59500.00
        assert r["charitable_carryforward"] == 10500.00

    def test_truthy_string_flags_rejected(self):
        """Review P2: a truthy string in a bool parameter silently changes
        the money — strict validation per repo convention."""
        with pytest.raises(ValueError, match="mortgage_pre_dec_16_2017"):
            compute_schedule_a(100000, "Single", 2025, 15750, 40000,
                               mortgage_pre_dec_16_2017="false")
        with pytest.raises(ValueError, match="mfs_spouse_itemizes"):
            compute_schedule_a(100000, "MFS", 2025, 15750, 20000,
                               mfs_spouse_itemizes="no")
        with pytest.raises(ValueError, match="charitable_over_250"):
            compute_schedule_a(100000, "Single", 2025, 15750, 40000,
                               charitable_cash=1000,
                               charitable_over_250_substantiated="yes")
        with pytest.raises(ValueError, match="mfs_spouse_itemizes"):
            compute_ca_itemized(100000, 100000, 2025,
                                mfs_spouse_itemizes="yes")

    def test_mfs_mortgage_limits_halved(self):
        """Codex P2: Pub 936 halves the caps for MFS — $375K post-TCJA
        ($500K grandfathered). Balance 600K, interest 24K → 24K × 375/600
        = 15,000."""
        r = compute_schedule_a(
            agi=200000, filing_status="MFS", tax_year=2025,
            standard_deduction=15750, salt_cap=20000,
            mortgage_interest=24000, average_mortgage_balance=600000)
        assert r["mortgage_deductible"] == 15000.00

    def test_ca_mfs_mortgage_limit_500k(self):
        """Codex P2: CA's pre-TCJA limit is likewise halved for MFS/RDP-
        separate ($500K). Balance 800K, interest 32K → 20,000."""
        r = compute_ca_itemized(
            federal_agi=200000, ca_agi=200000, tax_year=2025,
            mortgage_interest=32000, average_mortgage_balance=800000,
            married_filing_separately=True, phaseout_threshold=244000)
        assert r["mortgage_deductible"] == 20000.00

    def test_obbba_70111_haircut(self):
        """Codex P2 — H.R.1 §70111 (TY2026+): itemized reduced by 2/37 of
        the lesser of the itemized total or 37%-bracket income. SALT 40,400
        + charity floor'd → total 50,000-ish exercise: use round numbers —
        itemized 44,400; top-bracket income 100,000 → haircut 2/37 ×
        44,400 = 2,400."""
        r = compute_schedule_a(
            agi=1000000, filing_status="Single", tax_year=2026,
            standard_deduction=16000, salt_cap=40400,
            state_local_income_taxes=90000,   # capped at 40,400
            investment_interest=4000, top_bracket_income=100000)
        assert r["itemized_total"] == 44400.00 - 2400.00
        assert r["obbba_70111_reduction"] == 2400.00
        unknown = compute_schedule_a(
            agi=1000000, filing_status="Single", tax_year=2026,
            standard_deduction=16000, salt_cap=40400,
            state_local_income_taxes=90000, investment_interest=4000)
        assert unknown["obbba_70111_reduction"] == 0.0
        assert any("§70111" in n and "OVERSTATED" in n for n in unknown["notes"])
        r25 = compute_schedule_a(
            agi=1000000, filing_status="Single", tax_year=2025,
            standard_deduction=15750, salt_cap=40000,
            state_local_income_taxes=90000)
        assert r25["obbba_70111_reduction"] == 0.0
        assert not any("§70111" in n for n in r25["notes"])

    def test_ca_charitable_carryforward_returned(self):
        """Codex P2: CA tracks its own carryover (50% cap ≠ federal 60%) —
        the excess must be RETURNED, not just noted."""
        r = compute_ca_itemized(
            federal_agi=100000, ca_agi=100000, tax_year=2025,
            charitable_cash=55000, phaseout_threshold=244000)
        assert r["charitable_carryforward"] == 5000.00


# ============================================================
# Form 6251 AMT + Form 8801 credit (D9)
# ============================================================

# TY2025 Single AMT parameters (Rev. Proc. 2024-40): exemption 88,100;
# phase-out starts 626,350; 26%→28% breakpoint 239,100.
_AMT_2025 = dict(exemption=88100, phaseout_start=626350,
                 rate_28_breakpoint=239100, tax_year=2025)


_SCH1A_LIMITS = {
    "tips_cap": 25000,
    "overtime_cap": {"single": 12500, "mfj": 25000},
    "car_loan_cap": 10000,
    "senior_amount_per_person": 6000,
    "tips_overtime_phaseout_start": {"single": 150000, "mfj": 300000},
    "car_loan_phaseout_start": {"single": 100000, "mfj": 200000},
    "senior_phaseout_start": {"single": 75000, "mfj": 150000},
}


class TestSchedule1A:
    """OBBBA Schedule 1-A (I2) — hand-computed from the enacted caps/phase-outs.
    Figures in _SCH1A_LIMITS are representative TY2025 amounts."""

    def _c(self, fs="Single", year=2025, magi=120000, **kw):
        return compute_schedule_1a(fs, year, magi, _SCH1A_LIMITS, **kw)

    def test_all_provisions_hand_computed(self):
        """Single, MAGI 120K. Tips 30K→cap 25K; OT premium 10K (under cap);
        car-loan 12K→cap 10K then phased $200×ceil(20K/1K)=4,000 → 6,000;
        senior (1 elig) 6,000 − 6%×(120K−75K)=2,700 → 3,300. Total 44,300."""
        r = self._c(qualified_tips=30000, overtime_premium=10000,
                    car_loan_interest=12000, primary_age_65=True)
        assert r["tips"] == {"allowed": 25000.0, "disallowed_by_cap": 5000.0,
                             "disallowed_by_phaseout": 0.0}
        assert r["overtime"]["allowed"] == 10000.0
        assert r["car_loan"] == {"allowed": 6000.0, "disallowed_by_cap": 2000.0,
                                 "disallowed_by_phaseout": 4000.0}
        assert r["senior"]["allowed"] == 3300.0
        assert r["total"] == 44300.0
        assert r["senior_deduction"] == 3300.0

    def test_tips_overtime_fully_phased_out(self):
        """MAGI far over the 150K start → tips/overtime phase to $0 at
        $100/$1,000. Tips cap 25K needs 250 increments = 250K over → MAGI
        400K zeroes it exactly."""
        r = self._c(magi=400000, qualified_tips=25000, overtime_premium=12500)
        assert r["tips"]["allowed"] == 0.0
        assert r["tips"]["disallowed_by_phaseout"] == 25000.0
        assert r["overtime"]["allowed"] == 0.0

    def test_senior_two_eligible_mfj(self):
        """MFJ, both 65, MAGI 150K = threshold exactly → full 12,000."""
        r = self._c(fs="MFJ", magi=150000, primary_age_65=True,
                    spouse_age_65=True)
        assert r["senior"]["allowed"] == 12000.0

    def test_spouse_age_ignored_when_not_mfj(self):
        """A Single filer's spouse_age_65 flag can't add a second $6K."""
        r = self._c(fs="Single", magi=75000, primary_age_65=True,
                    spouse_age_65=True)
        assert r["senior"]["allowed"] == 6000.0

    def test_mfs_zeroes_all_schedule_1a_provisions(self):
        """MFS: ALL four Schedule 1-A provisions are $0. Tips/overtime/senior
        require a joint return, and the OBBBA §70203 car-loan interest
        deduction is likewise UNAVAILABLE to a married filer who files
        separately (verified vs IRS / H&R Block / Federal Register — the
        round-1 "no MFS bar" reading was wrong). MAGI 120K, car 9000 → $0."""
        r = self._c(fs="MFS", qualified_tips=20000, overtime_premium=8000,
                    car_loan_interest=9000, primary_age_65=True)
        assert r["tips"]["allowed"] == 0.0
        assert r["overtime"]["allowed"] == 0.0
        assert r["senior"]["allowed"] == 0.0
        assert r["car_loan"]["allowed"] == 0.0
        assert r["car_loan"]["disallowed_by_phaseout"] == 9000.0  # full disallow
        assert r["total"] == 0.0
        assert any("joint return" in n for n in r["notes"])

    def test_senior_per_person_phaseout_mfj_over_threshold(self):
        """Codex P1: the 6% phase-out is PER PERSON, not on the combined base.
        MFJ both 65, MAGI 200K, threshold 150K → reduction 6%×50K=3,000 per
        person → each 6,000−3,000=3,000 → 6,000 total (NOT 12,000−3,000=9,000).
        A two-senior couple thus reaches $0 at the same MAGI a one-senior filer
        does."""
        r = self._c(fs="MFJ", magi=200000, primary_age_65=True,
                    spouse_age_65=True)
        assert r["senior"]["allowed"] == 6000.0
        assert r["senior"]["disallowed_by_phaseout"] == 6000.0
        # Fully phased at threshold + amount/6% = 150K + 100K = 250K.
        r2 = self._c(fs="MFJ", magi=250000, primary_age_65=True,
                     spouse_age_65=True)
        assert r2["senior"]["allowed"] == 0.0

    def test_tips_overtime_round_down_car_loan_rounds_up(self):
        """Codex P2: tips/overtime phase-out counts WHOLE $1,000s (round down —
        $1 over loses nothing); car-loan counts fractions (round up — $1 over
        loses a full $200 increment)."""
        one_over = self._c(magi=150001, qualified_tips=20000,
                           overtime_premium=5000)
        assert one_over["tips"]["allowed"] == 20000.0       # floor → 0 reduction
        assert one_over["overtime"]["allowed"] == 5000.0
        car = self._c(magi=100001, car_loan_interest=8000)
        assert car["car_loan"]["allowed"] == 7800.0         # ceil → 1×$200

    def test_qss_uses_single_caps_not_joint(self):
        """Codex P2: QSS is not a joint return — it gets the SINGLE overtime
        cap ($12,500) and single thresholds, not the doubled MFJ figures. QSS
        is unmarried, so the joint-filing bar does not apply (unlike MFS)."""
        r = self._c(fs="QSS", magi=120000, overtime_premium=20000,
                    primary_age_65=True, spouse_age_65=True)
        assert r["overtime"]["allowed"] == 12500.0          # single cap, not 25K
        assert r["overtime"]["disallowed_by_cap"] == 7500.0
        # spouse_age_65 ignored (not MFJ) → 1 eligible senior on the single
        # $75K threshold: 6,000 − 6%×(120K−75K)=2,700 → 3,300.
        assert r["senior"]["allowed"] == 3300.0

    def test_year_gate_before_and_after_window(self):
        for yr in (2024, 2029):
            r = self._c(year=yr, qualified_tips=20000, primary_age_65=True)
            assert r["total"] == 0.0
            assert any("not in effect" in n for n in r["notes"])

    def test_overtime_premium_warning(self):
        r = self._c(overtime_premium=5000)
        assert any("premium portion ONLY" in n for n in r["notes"])

    def test_all_zero_needs_no_cache(self):
        r = compute_schedule_1a("Single", 2025, 120000, None)
        assert r["total"] == 0.0

    def test_missing_cache_hard_errors_when_input_present(self):
        with pytest.raises(ValueError, match="unfetched"):
            compute_schedule_1a("Single", 2025, 120000, {},
                                qualified_tips=10000)

    def test_input_validation(self):
        with pytest.raises(ValueError, match="tax_year"):
            self._c(year=2025.0, qualified_tips=1000)
        with pytest.raises(ValueError, match="primary_age_65"):
            compute_schedule_1a("Single", 2025, 120000, _SCH1A_LIMITS,
                                primary_age_65="yes")
        for bad in (-1, float("nan"), True):
            with pytest.raises(ValueError):
                self._c(qualified_tips=bad)

    def test_negative_magi_yields_no_phaseout_not_error(self):
        """Codex round-3 P2: a loss/NOL year with negative MAGI must not
        hard-error — MAGI only tests excess over the threshold, so a negative
        MAGI simply produces no phase-out (full deduction)."""
        r = self._c(magi=-20000, qualified_tips=30000, car_loan_interest=5000,
                    primary_age_65=True)
        assert r["tips"]["allowed"] == 25000.0       # cap only, no phaseout
        assert r["car_loan"]["allowed"] == 5000.0    # under cap, no phaseout
        assert r["senior"]["allowed"] == 6000.0      # full, no phaseout
        # NaN/Inf MAGI is still rejected.
        with pytest.raises(ValueError):
            self._c(magi=float("nan"), qualified_tips=1000)

    def test_qbi_base_reduced_by_schedule_1a(self):
        """Ordering invariant: line 13b reduces the Form 8995 overall-cap
        base. TI-before-QBI 100K, QBI 90K → uncapped 18K but cap
        20%×100K=20K (not binding) → 18K. Subtract 20K of 13b → base 80K →
        cap 16K binds → 16K."""
        no_1a = compute_qbi_deduction(90000, 0,
                                      taxable_income_before_qbi=100000,
                                      net_capital_gain=0, threshold=191950)
        with_1a = compute_qbi_deduction(90000, 0,
                                        taxable_income_before_qbi=80000,
                                        net_capital_gain=0, threshold=191950)
        assert no_1a["total_deduction"] == 18000.0
        assert with_1a["total_deduction"] == 16000.0


class TestCharitableNonItemizer:
    """I3 — §170(p) OBBBA non-itemizer charitable deduction (TY2026+)."""
    _CAP = dict(amount=1000, joint_amount=2000)

    def test_under_cap_single(self):
        r = compute_charitable_non_itemizer(800, "Single", 2026, False, **self._CAP)
        assert r["allowed"] == 800.0
        assert r["disallowed_by_cap"] == 0.0

    def test_cap_binds_single(self):
        r = compute_charitable_non_itemizer(1500, "Single", 2026, False, **self._CAP)
        assert r["allowed"] == 1000.0
        assert r["disallowed_by_cap"] == 500.0

    def test_mfj_joint_cap(self):
        r = compute_charitable_non_itemizer(3000, "MFJ", 2026, False, **self._CAP)
        assert r["allowed"] == 2000.0

    def test_mfs_uses_non_joint_amount(self):
        # MFS follows the non-joint figure (not half the joint, not the joint).
        r = compute_charitable_non_itemizer(3000, "MFS", 2026, False, **self._CAP)
        assert r["allowed"] == 1000.0

    def test_qss_uses_non_joint_amount(self):
        # A qualifying surviving spouse does not file a joint return.
        r = compute_charitable_non_itemizer(3000, "QSS", 2026, False, **self._CAP)
        assert r["allowed"] == 1000.0

    def test_pre_2026_is_zero(self):
        r = compute_charitable_non_itemizer(800, "Single", 2025, False, **self._CAP)
        assert r["allowed"] == 0.0
        assert any("not in effect" in n for n in r["notes"])

    def test_itemizer_is_zero(self):
        r = compute_charitable_non_itemizer(800, "Single", 2026, True, **self._CAP)
        assert r["allowed"] == 0.0
        assert any("standard-deduction filers only" in n.lower()
                   or "itemizer" in n.lower() for n in r["notes"])

    def test_unfetched_cap_hard_errors_when_nonzero(self):
        with pytest.raises(ValueError, match="unfetched"):
            compute_charitable_non_itemizer(800, "Single", 2026, False)

    def test_zero_gifts_no_cache_needed(self):
        # No contributions -> $0 without consulting the (unfetched) cap.
        r = compute_charitable_non_itemizer(0, "Single", 2026, False)
        assert r["allowed"] == 0.0

    def test_substantiation_tristate(self):
        unknown = compute_charitable_non_itemizer(800, "Single", 2026, False, **self._CAP)
        assert any("SUBSTANTIATION UNKNOWN" in n for n in unknown["notes"])
        gated = compute_charitable_non_itemizer(
            800, "Single", 2026, False, over_250_substantiated=False, **self._CAP)
        assert any("SUBSTANTIATION GATE" in n for n in gated["notes"])
        ok = compute_charitable_non_itemizer(
            800, "Single", 2026, False, over_250_substantiated=True, **self._CAP)
        assert not any("SUBSTANTIATION" in n for n in ok["notes"])

    def test_notes_carry_daf_and_floor_facts(self):
        r = compute_charitable_non_itemizer(800, "Single", 2026, False, **self._CAP)
        joined = " ".join(r["notes"])
        assert "donor-advised" in joined and "509(a)(3)" in joined
        assert "0.5%" in joined and "carryforward" in joined

    def test_rejects_bad_inputs(self):
        for bad in (
            dict(cash_contributions=-1),
            dict(itemizes="no"),
            dict(over_250_substantiated="yes"),
            dict(filing_status="Married Filing Jointly"),
            dict(tax_year=26),
        ):
            kw = dict(cash_contributions=800, filing_status="Single",
                      tax_year=2026, itemizes=False, **self._CAP)
            kw.update(bad)
            with pytest.raises(ValueError):
                compute_charitable_non_itemizer(**kw)

    def test_mandatory_mfs_itemization_not_overridden_by_170p(self):
        # Codex (PR #46 round 3, SDnf3): an MFS filer whose spouse itemizes is
        # DENIED the standard deduction under §63(c)(6)(A); the §170(p) tilt
        # must NOT flip such a filer to the standard deduction to claim line
        # 13c. Engine side: a forced itemizer (itemizes=True) always gets $0.
        r = compute_charitable_non_itemizer(3000, "MFS", 2026, True, **self._CAP)
        assert r["allowed"] == 0.0
        # Skill side: /tax-compute must guard the tilt on mandatory itemization.
        compute_md = " ".join((Path(__file__).resolve().parent.parent
                      / "agents/skills/tax-compute.md").read_text().split())
        assert "mfs_spouse_itemizes" in compute_md
        assert "63(c)(6)(A)" in compute_md
        assert "mandatory itemization" in compute_md.lower()


class TestQBIMinimum:
    """I4 §70105 — minimum QBI deduction (OBBBA TY2026+)."""
    _QM = dict(income_threshold=1000, amount=400)

    def test_floor_lifts_small_active_qbi(self):
        # 20% of 1500 = 300, below the 400 floor -> 400.
        r = compute_qbi_deduction(1500, 0, tax_year=2026, qbi_minimum=self._QM,
                                  active_qualified_trade=True)
        assert r["total_deduction"] == 400.0
        assert r["qbi_minimum_applied"] is True

    def test_floor_not_applied_when_computed_exceeds(self):
        # 20% of 5000 = 1000 > floor -> no lift.
        r = compute_qbi_deduction(5000, 0, tax_year=2026, qbi_minimum=self._QM,
                                  active_qualified_trade=True)
        assert r["total_deduction"] == 1000.0
        assert r["qbi_minimum_applied"] is False

    def test_below_income_threshold_no_floor(self):
        # QBI 500 < 1000 threshold -> not eligible -> 20% of 500 = 100.
        r = compute_qbi_deduction(500, 0, tax_year=2026, qbi_minimum=self._QM,
                                  active_qualified_trade=True)
        assert r["total_deduction"] == 100.0
        assert r["qbi_minimum_applied"] is False

    def test_passive_activity_no_floor(self):
        r = compute_qbi_deduction(1500, 0, tax_year=2026, qbi_minimum=self._QM,
                                  active_qualified_trade=False)
        assert r["total_deduction"] == 300.0

    def test_pre_2026_no_floor(self):
        r = compute_qbi_deduction(1500, 0, tax_year=2025, qbi_minimum=self._QM,
                                  active_qualified_trade=True)
        assert r["total_deduction"] == 300.0

    def test_unfetched_cache_degrades_with_note(self):
        r = compute_qbi_deduction(1500, 0, tax_year=2026, qbi_minimum=None,
                                  active_qualified_trade=True)
        assert r["total_deduction"] == 300.0
        assert r["qbi_minimum_applied"] is False
        assert "qbi_minimum_note" in r and "unfetched" in r["qbi_minimum_note"]

    def test_floor_does_not_exceed_overall_income_cap(self):
        # Codex P1: the §199A(a)(2) OVERALL cap (20% of taxable income − net
        # capital gain) still binds — a $400 floor cannot exceed a $20 cap.
        r = compute_qbi_deduction(1000, 0, tax_year=2026, qbi_minimum=self._QM,
                                  active_qualified_trade=True,
                                  taxable_income_before_qbi=1000,
                                  net_capital_gain=900)
        assert r["total_deduction"] == 20.0
        assert r["qbi_minimum_applied"] is False

    def test_floor_applies_within_ample_income_cap(self):
        # Ample ordinary headroom: floor lifts 20%×1500=300 up to 400.
        r = compute_qbi_deduction(1500, 0, tax_year=2026, qbi_minimum=self._QM,
                                  active_qualified_trade=True,
                                  taxable_income_before_qbi=100000,
                                  net_capital_gain=0)
        assert r["total_deduction"] == 400.0
        assert r["qbi_minimum_applied"] is True

    def test_legacy_call_unchanged(self):
        r = compute_qbi_deduction(1500, 0)
        assert r["total_deduction"] == 300.0
        assert r["qbi_minimum_applied"] is False

    def test_active_trade_attestation_collected_by_setup(self):
        # Codex (PR #46 round 2, SDiXY): the §70105 floor is gated on
        # active_qualified_trade, which defaults False. If /tax-setup never
        # collects the attestation the normal workflow silently omits an
        # otherwise-applicable floor (overstating tax). Pin that the flag
        # exists in the profile template AND the interview asks for it AND
        # /tax-compute wires it into the QBI call.
        repo = Path(__file__).resolve().parent.parent
        template = json.loads(
            (repo / "agents/templates/taxpayer_profile_template.json").read_text())
        assert "qbi_active_trade" in template["flags"]
        setup_md = (repo / "agents/skills/tax-setup.md").read_text()
        assert "qbi_active_trade" in setup_md
        compute_md = (repo / "agents/skills/tax-compute.md").read_text()
        assert "flags.qbi_active_trade" in compute_md


class TestForm6251:
    def test_iso_exerciser_hand_computed(self):
        """The persona case: TI 300K (after 40K SALT), ISO spread 200K.
        AMTI = 300,000 + 40,000 + 200,000 = 540,000 < 626,350 → full
        exemption 88,100. Base 451,900 > breakpoint → 28% × 451,900 −
        2% × 239,100 = 126,532 − 4,782 = 121,750. AMT = 121,750 − 80,000
        = 41,750, all deferral-attributable (without the ISO there is no
        AMT at all)."""
        r = compute_form_6251(
            taxable_income=300000, filing_status="Single",
            regular_tax=80000, salt_deducted=40000,
            iso_bargain_element=200000, **_AMT_2025)
        assert r["amti"] == 540000.00
        assert r["exemption_allowed"] == 88100.00
        assert r["tmt"] == 121750.00
        assert r["amt"] == 41750.00
        assert r["form_8801_carryforward"] == 41750.00

    def test_exemption_phaseout_25_pct(self):
        """AMTI 726,350 = 100,000 over the start → exemption reduced by
        25,000 → 63,100."""
        r = compute_form_6251(
            taxable_income=686350, filing_status="Single",
            regular_tax=250000, salt_deducted=40000, **_AMT_2025)
        assert r["amti"] == 726350.00
        assert r["exemption_allowed"] == 88100.00 - 25000.00

    def test_no_amt_when_regular_tax_higher(self):
        r = compute_form_6251(
            taxable_income=200000, filing_status="Single",
            regular_tax=60000, standard_deduction_taken=15750, **_AMT_2025)
        assert r["amt"] == 0.0 and r["form_8801_carryforward"] == 0.0

    def test_exclusion_only_amt_generates_no_credit(self):
        """SALT-only AMT (no ISO) is an EXCLUSION item — no 8801 credit.
        AMTI 440,000; base 351,900; TMT 93,750 vs regular 90,000 →
        AMT 3,750, none of it deferral-attributable."""
        r = compute_form_6251(
            taxable_income=400000, filing_status="Single",
            regular_tax=90000, salt_deducted=40000, **_AMT_2025)
        assert r["amt"] == 3750.00
        assert r["form_8801_carryforward"] == 0.0

    def test_part_iii_capital_gains_at_preferential_rates(self):
        """LTCG stays at 15/20% inside AMT — it must NOT be taxed at 26/28.
        TI 500K of which 100K LTCG; ISO 100K. Regular ordinary = 400K (over
        the 15% start), so the whole 100K cg sits below the 20% breakpoint
        → 15%. AMTI 615,750; exemption 88,100 (under phase-out start);
        base 527,650; ordinary part 427,650: 28% × 427,650 − 4,782 =
        114,960; cap part 15,000; TMT 129,960."""
        r = compute_form_6251(
            taxable_income=500000, filing_status="Single",
            regular_tax=140000, standard_deduction_taken=15750,
            iso_bargain_element=100000,
            qualified_dividends=0, net_ltcg=100000,
            zero_pct_limit=48350, fifteen_pct_limit=533400, **_AMT_2025)
        assert r["tmt"] == 129960.00

    def test_unfetched_amt_table_hard_errors(self):
        with pytest.raises(ValueError, match="unfetched"):
            compute_form_6251(
                taxable_income=300000, filing_status="Single", tax_year=2025,
                regular_tax=80000, exemption=None, phaseout_start=626350,
                rate_28_breakpoint=239100, iso_bargain_element=200000)

    def test_both_salt_and_std_rejected(self):
        with pytest.raises(ValueError, match="OR standard_deduction"):
            compute_form_6251(
                taxable_income=300000, filing_status="Single",
                regular_tax=80000, salt_deducted=40000,
                standard_deduction_taken=15750, **_AMT_2025)

    def test_mfs_breakpoint_halved_and_caveat(self):
        r = compute_form_6251(
            taxable_income=200000, filing_status="MFS", tax_year=2025,
            regular_tax=40000, standard_deduction_taken=15750,
            iso_bargain_element=50000,
            exemption=68500, phaseout_start=626350,
            rate_28_breakpoint=239100)
        # Rev. Proc. 2024-40 MFS exemption 68,500. AMTI 265,750 (below the
        # §55(d)(3) recapture ceiling — no addback); base 197,250 > 119,550
        # (halved bp) → 28% × 197,250 − 2% × 119,550 = 55,230 − 2,391
        # = 52,839
        assert r["tmt"] == 52839.00

    def test_ca_schedule_p_note_always_present(self):
        r = compute_form_6251(
            taxable_income=100000, filing_status="Single",
            regular_tax=20000, standard_deduction_taken=15750, **_AMT_2025)
        assert any("Schedule P" in n for n in r["notes"])

    def test_ty2026_exemption_phaseout_50_pct(self):
        """I1 — OBBBA §70107: the exemption phase-out rate is 50% for TY2026+
        (25% through TY2025). Same fixture, both years, AMTI 726,350 = 100,000
        over the start. Regular tax 150,000 so AMT is positive both ways.
          TY2025 (25%): exemption 88,100 − 25,000 = 63,100; base 663,250;
            TMT 28%×663,250 − 4,782 = 180,928; AMT 30,928.
          TY2026 (50%): exemption 88,100 − 50,000 = 38,100; base 688,250;
            TMT 28%×688,250 − 4,782 = 187,928; AMT 37,928.
        Delta 7,000 = the extra 25,000 of phased-out exemption × 28%."""
        common = dict(taxable_income=686350, filing_status="Single",
                      regular_tax=150000, salt_deducted=40000,
                      exemption=88100, phaseout_start=626350,
                      rate_28_breakpoint=239100)
        y25 = compute_form_6251(tax_year=2025, **common)
        y26 = compute_form_6251(tax_year=2026, **common)
        assert y25["exemption_allowed"] == 63100.00
        assert y26["exemption_allowed"] == 38100.00
        assert y25["amt"] == 30928.00
        assert y26["amt"] == 37928.00
        assert round(y26["amt"] - y25["amt"], 2) == 7000.00

    def test_phaseout_boundary_full_exemption_both_years(self):
        """AMTI exactly at phaseout_start → no reduction, either year."""
        common = dict(taxable_income=586350, filing_status="Single",
                      regular_tax=100000, salt_deducted=40000, **{
                          k: v for k, v in _AMT_2025.items()
                          if k != "tax_year"})
        # AMTI = 586,350 + 40,000 = 626,350 = phaseout_start exactly.
        y25 = compute_form_6251(tax_year=2025, **common)
        y26 = compute_form_6251(tax_year=2026, **common)
        assert y25["amti"] == 626350.00
        assert y25["exemption_allowed"] == 88100.00
        assert y26["exemption_allowed"] == 88100.00

    def test_ty2026_mfs_recapture_uses_50_pct(self):
        """§55(d)(3) recapture ceiling and the phase-out both use the
        year-resolved 50% rate. Raw AMTI 815,750 sits BELOW the TY2025 25%
        ceiling (626,350 + 68,500/0.25 = 900,350 → no addback) but ABOVE the
        TY2026 50% ceiling (626,350 + 68,500/0.50 = 763,350). TY2026 addback
        = min(68,500, 50% × (815,750 − 763,350)) = min(68,500, 26,200) =
        26,200 → AMTI 841,950; exemption fully phased out."""
        common = dict(taxable_income=700000, filing_status="MFS",
                      regular_tax=200000, standard_deduction_taken=15750,
                      iso_bargain_element=100000, exemption=68500,
                      phaseout_start=626350, rate_28_breakpoint=239100)
        y25 = compute_form_6251(tax_year=2025, **common)
        y26 = compute_form_6251(tax_year=2026, **common)
        assert y25["amti"] == 815750.00  # no recapture at 25%
        assert y26["amti"] == 841950.00  # +26,200 recapture at 50%
        assert y26["exemption_allowed"] == 0.00

    def test_cache_override_rate_wins_over_schedule(self):
        """federal.amt.phaseout_rate override beats the statutory schedule
        (the salt_cap precedent): a TY2025 return with an explicit 0.50 rate
        phases out like TY2026, not the 25% default."""
        common = dict(taxable_income=686350, filing_status="Single",
                      regular_tax=150000, salt_deducted=40000,
                      exemption=88100, phaseout_start=626350,
                      rate_28_breakpoint=239100)
        overridden = compute_form_6251(
            tax_year=2025, exemption_phaseout_rate=0.50, **common)
        y26 = compute_form_6251(tax_year=2026, **common)
        assert overridden["exemption_allowed"] == 38100.00
        assert overridden["amt"] == y26["amt"]

    def test_tax_year_validation(self):
        common = dict(taxable_income=300000, filing_status="Single",
                      regular_tax=80000, **{k: v for k, v in _AMT_2025.items()
                                            if k != "tax_year"})
        for bad in (True, 2025.0, 1999, 2100, "2025", None):
            with pytest.raises(ValueError, match="tax_year"):
                compute_form_6251(tax_year=bad, **common)

    def test_exemption_phaseout_rate_validation(self):
        common = dict(taxable_income=300000, filing_status="Single",
                      regular_tax=80000, **_AMT_2025)
        for bad in (float("nan"), float("inf"), True, 0, -0.1, 1.5, "0.5"):
            with pytest.raises(ValueError, match="exemption_phaseout_rate"):
                compute_form_6251(exemption_phaseout_rate=bad, **common)


class TestForm8801:
    def test_credit_limited_by_regular_minus_tmt(self):
        """§53(c): the credit can't push you into AMT — usable = min(cf,
        regular − TMT)."""
        r = compute_form_8801_credit(
            prior_carryforward=30000, regular_tax=90000,
            tentative_minimum_tax=75000)
        assert r["credit_allowed"] == 15000.00
        assert r["carryforward_remaining"] == 15000.00

    def test_nonrefundable_credits_reduce_the_limit(self):
        """Review P1 — §53(c)(1) nets ALL nonrefundable credits (CTC,
        education, FTC...), not just the FTC: cf 30K, line 16 50K, CTC
        4,400, TMT 40K → usable min(30,000, 45,600 − 40,000) = 5,600 —
        passing 0 credits overstated it to 10,000 (understated tax)."""
        r = compute_form_8801_credit(
            prior_carryforward=30000, regular_tax=50000,
            tentative_minimum_tax=40000, nonrefundable_credits=4400)
        assert r["credit_allowed"] == 5600.00
        assert r["carryforward_remaining"] == 24400.00

    def test_full_credit_when_headroom_exceeds(self):
        r = compute_form_8801_credit(10000, 90000, 60000)
        assert r["credit_allowed"] == 10000.00
        assert r["carryforward_remaining"] == 0.0

    def test_no_credit_in_an_amt_year(self):
        r = compute_form_8801_credit(10000, 60000, 75000)
        assert r["credit_allowed"] == 0.0
        assert r["carryforward_remaining"] == 10000.00

    def test_amt_ftc_reduces_tmt(self):
        """Review P2 — 6251 line 8: the AMT FTC (= regular FTC on the
        simplified path) reduces line 7 to give TMT; omitting it overstates
        AMT by exactly the FTC."""
        base = dict(taxable_income=400000, filing_status="Single",
                    regular_tax=90000, salt_deducted=40000, **_AMT_2025)
        without = compute_form_6251(**base)
        with_ftc = compute_form_6251(amt_ftc=600, **base)
        assert round(without["amt"] - with_ftc["amt"], 2) == 600.00

    def test_ti_zero_phantom_amti_noted(self):
        r = compute_form_6251(
            taxable_income=0, filing_status="Single", regular_tax=0,
            salt_deducted=40000, iso_bargain_element=300000, **_AMT_2025)
        assert any("pass the NEGATIVE result" in n for n in r["notes"])

    def test_negative_taxable_income_accepted_and_lowers_amti(self):
        """D9-review follow-up: when 1040 line 15 is $0 because deductions
        exceeded AGI, the 6251 line 1 instructions use AGI minus deductions
        (negative). Passing the negative figure must lower AMTI vs the $0
        input — dollar-for-dollar before the exemption."""
        zero = compute_form_6251(
            taxable_income=0, filing_status="Single", regular_tax=0,
            salt_deducted=40000, iso_bargain_element=300000, **_AMT_2025)
        neg = compute_form_6251(
            taxable_income=-25000, filing_status="Single", regular_tax=0,
            salt_deducted=40000, iso_bargain_element=300000, **_AMT_2025)
        assert neg["amti"] == zero["amti"] - 25000
        assert neg["amt"] < zero["amt"]
        # The refigure note only fires on the ambiguous $0 input.
        assert not any("pass the NEGATIVE result" in n for n in neg["notes"])

    def test_mfs_exemption_recapture_above_ceiling(self):
        """§55(d)(3) last sentence (Codex P2): MFS past the phase-out
        CEILING (626,350 + 68,500/0.25 = 900,350) adds back 25% of the
        excess capped at the exemption. TI 850,000 std + ISO 100,000 →
        raw AMTI 965,750 → addback min(68,500, 25% × 65,400) = 16,350 →
        AMTI 982,100; exemption fully phased → TMT = 28% × 982,100 −
        2,391 = 272,597."""
        r = compute_form_6251(
            taxable_income=850000, filing_status="MFS", tax_year=2025,
            regular_tax=260000, standard_deduction_taken=15750,
            iso_bargain_element=100000,
            exemption=68500, phaseout_start=626350,
            rate_28_breakpoint=239100)
        assert r["amti"] == 982100.00
        assert r["exemption_allowed"] == 0.00
        assert r["tmt"] == 272597.00

    def test_senior_deduction_added_back(self):
        """Codex P2 — OBBBA Schedule 1-A line 37 (TY2025+) is NOT allowed
        for AMT: +6,000 AMTI raises TMT by 28% × 6,000 = 1,680 in the 28%
        zone."""
        base = dict(taxable_income=300000, filing_status="Single",
                    regular_tax=80000, salt_deducted=40000,
                    iso_bargain_element=200000, **_AMT_2025)
        plain = compute_form_6251(**base)
        senior = compute_form_6251(senior_deduction=6000, **base)
        assert senior["amti"] == plain["amti"] + 6000.00
        assert round(senior["amt"] - plain["amt"], 2) == 1680.00


class TestEducationCredits:
    """Form 8863 (D5) — hand-computed from the §25A statutory parameters."""

    def _one(self, exp, eligible=True, **kw):
        return compute_education_credits(
            kw.pop("magi", 100000), kw.pop("filing_status", "MFJ"),
            [{"qualified_expenses": exp, "aotc_eligible": eligible}], **kw)

    def test_aotc_tier_math(self):
        # 100% of first 2000 + 25% of next 2000.
        assert self._one(1500)["aotc_tentative"] == 1500.0
        assert self._one(2000)["aotc_tentative"] == 2000.0
        assert self._one(3000)["aotc_tentative"] == 2250.0
        assert self._one(4000)["aotc_tentative"] == 2500.0
        assert self._one(10000)["aotc_tentative"] == 2500.0  # capped

    def test_llc_twenty_pct_per_return_cap(self):
        r = self._one(6000, eligible=False)
        assert r["llc_tentative"] == 1200.0
        # Two LLC students pool to the $10,000 PER-RETURN cap.
        r2 = compute_education_credits(100000, "MFJ", [
            {"qualified_expenses": 7000, "aotc_eligible": False},
            {"qualified_expenses": 8000, "aotc_eligible": False}])
        assert r2["llc_tentative"] == 2000.0  # 20% x 10000, not 15000
        assert any("per-RETURN cap" in n for n in r2["notes"])

    def test_refundable_split_40_60(self):
        # AOTC 2500 -> 1000 refundable (1040 line 29) + 1500 nonrefundable.
        r = self._one(4000, tax_liability=50000)
        assert r["refundable_aotc"] == 1000.0
        assert r["aotc_nonrefundable"] == 1500.0
        assert r["nonrefundable_credit"] == 1500.0

    def test_line7_checkbox_folds_refundable_back(self):
        r = self._one(4000, no_refundable_aotc=True)
        assert r["refundable_aotc"] == 0.0
        assert r["aotc_nonrefundable"] == 2500.0

    def test_refundable_survives_zero_tax(self):
        # Refundable AOTC pays out at $0 tax; nonrefundable is limited to 0.
        r = self._one(4000, tax_liability=0)
        assert r["refundable_aotc"] == 1000.0
        assert r["nonrefundable_credit"] == 0.0

    def test_tax_limit_caps_nonrefundable(self):
        # AOTC nonref 1500 + LLC 1200 = 2700 vs tax 2000 -> 2000, no carryover.
        r = compute_education_credits(100000, "MFJ", [
            {"qualified_expenses": 4000, "aotc_eligible": True},
            {"qualified_expenses": 6000, "aotc_eligible": False}],
            tax_liability=2000)
        assert r["nonrefundable_credit"] == 2000.0
        assert any("limited by tax" in n for n in r["notes"])

    def test_phaseout_mfj_and_single_ranges(self):
        # MFJ midpoint 170000 -> ratio 0.5; single midpoint 85000 -> 0.5.
        r = self._one(4000, magi=170000, filing_status="MFJ")
        assert r["phase_out_ratio"] == 0.5
        assert r["refundable_aotc"] == 500.0
        r2 = self._one(4000, magi=85000, filing_status="Single")
        assert r2["phase_out_ratio"] == 0.5

    def test_phaseout_boundaries(self):
        assert self._one(4000, magi=160000)["phase_out_ratio"] == 1.0
        assert self._one(4000, magi=180000)["phase_out_ratio"] == 0.0
        assert self._one(4000, magi=180000)["refundable_aotc"] == 0.0

    def test_qss_uses_single_range_not_mfj(self):
        # Form 8863 groups QSS with Single/HoH at $80K-$90K — the usual
        # QSS->mfj mapping would phase out too LATE and overstate the credit.
        r = compute_education_credits(120000, "QSS",
            [{"qualified_expenses": 4000, "aotc_eligible": True}])
        assert r["phase_out_ratio"] == 0.0
        r2 = compute_education_credits(120000, "HoH",
            [{"qualified_expenses": 4000, "aotc_eligible": True}])
        assert r2["phase_out_ratio"] == 0.0

    def test_mfs_gets_neither_credit(self):
        r = compute_education_credits(50000, "MFS",
            [{"qualified_expenses": 4000, "aotc_eligible": True}])
        assert r["allowed"] is False
        assert r["nonrefundable_credit"] == 0.0
        assert r["refundable_aotc"] == 0.0

    def test_mixed_students_assignment(self):
        r = compute_education_credits(100000, "MFJ", [
            {"qualified_expenses": 4000, "aotc_eligible": True, "name": "A"},
            {"qualified_expenses": 3000, "aotc_eligible": False, "name": "B"}])
        by_name = {a["name"]: a for a in r["assignment"]}
        assert by_name["A"]["credit"] == "AOTC"
        assert by_name["B"]["credit"] == "LLC"
        assert r["aotc_tentative"] == 2500.0
        assert r["llc_tentative"] == 600.0

    def test_two_aotc_students_each_capped_separately(self):
        r = compute_education_credits(100000, "MFJ", [
            {"qualified_expenses": 4000, "aotc_eligible": True},
            {"qualified_expenses": 4000, "aotc_eligible": True}])
        assert r["aotc_tentative"] == 5000.0  # per STUDENT, not per return

    def test_invalid_inputs_rejected(self):
        with pytest.raises(ValueError):
            compute_education_credits(float("nan"), "MFJ",
                [{"qualified_expenses": 1, "aotc_eligible": True}])
        with pytest.raises(ValueError):
            compute_education_credits(100000, "Married Filing Jointly",
                [{"qualified_expenses": 1, "aotc_eligible": True}])
        with pytest.raises(ValueError):
            compute_education_credits(100000, "MFJ", [])
        with pytest.raises(ValueError):  # negative expenses
            compute_education_credits(100000, "MFJ",
                [{"qualified_expenses": -1, "aotc_eligible": True}])
        with pytest.raises(ValueError):  # eligibility must be explicit bool
            compute_education_credits(100000, "MFJ",
                [{"qualified_expenses": 1000}])
        with pytest.raises(ValueError):  # truthy-string eligibility
            compute_education_credits(100000, "MFJ",
                [{"qualified_expenses": 1000, "aotc_eligible": "yes"}])
        with pytest.raises(ValueError):
            compute_education_credits(100000, "MFJ",
                [{"qualified_expenses": 1000, "aotc_eligible": True}],
                no_refundable_aotc="no")

    def test_low_expense_aotc_year_notes_lifetime_budget(self):
        # Review round: §25A allows 4 lifetime AOTC years and the credit is
        # elective — a low-expense claim must surface the trade-off.
        r = self._one(800)
        assert any("4 lifetime AOTC years" in n for n in r["notes"])
        # A full-credit year ($4,000+) carries no such note.
        assert not any("lifetime" in n for n in self._one(4000)["notes"])

    def test_6251_returns_iso_bargain_echo(self):
        """Second-lens review: the result dict must echo the ISO input so
        the copy-the-result flow fills form_6251.iso_bargain_element and
        the tie-out's iso_bargain category verifies the parsed 3921 sum —
        without it a CORRECT return false-fails against the template's 0."""
        r = compute_form_6251(
            taxable_income=200000, filing_status="Single", regular_tax=40000,
            iso_bargain_element=50000, **_AMT_2025)
        assert r["iso_bargain_element"] == 50000.0
        r0 = compute_form_6251(
            taxable_income=200000, filing_status="Single", regular_tax=40000,
            **_AMT_2025)
        assert r0["iso_bargain_element"] == 0.0
