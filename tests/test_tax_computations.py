#!/usr/bin/env python3
"""Comprehensive tests for tax_computations.py.

Expected values are hand-computed from IRS worksheets and publications.
Tolerance: $0.01 (exact match after rounding to 2 decimal places).
"""

import json
import pytest
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.skill_text import skill_text  # noqa: E402
from agents.lib.tax_law_fetcher import create_empty_tax_law  # noqa: E402
from agents.lib.tax_computations import (
    qdcg_worksheet_applies,
    _coerce_numeric,
    compute_bracket_tax,
    compute_ny_tax,
    compute_ny_agi,
    compute_ny_itemized,
    compute_qdcg_tax,
    compute_se_tax,
    compute_additional_medicare,
    compute_excess_sdi,
    compute_form_8889,
    compute_form_2441,
    compute_excess_ss_withholding,
    compute_ftc_limitation,
    compute_child_tax_credit,
    compute_education_credits,
    compute_qbi_deduction,
    compute_form_8995a,
    _qbi_phase_in_range,
    compute_niit,
    compute_form_8606,
    compute_taxable_ss,
    compute_form_2210,
    compute_schedule_a,
    compute_ca_itemized,
    compute_ca_schedule_p,
    compute_schedule_1a,
    compute_charitable_non_itemizer,
    compute_espp_disposition,
    compute_form_6251,
    compute_form_8801_credit,
    check_form_8938_required,
    compute_late_filing_penalty,
    compute_underpayment_interest,
    compute_section_988,
    compute_pfic_excess_distribution,
    compute_pfic_1291,
    compute_sales_tax_deduction,
    prior_year_agi_threshold,
    prior_year_prong,
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
        # SE earnings: 50_000 * 0.9235 = 46175.0
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
        # SE earnings: 400 * 0.9235 = 369.40
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

    # --- §199A(c)(4) / Reg. §1.199A-3(b)(1)(vi): QBI base netting by the
    # --- SE deductions attributable to the trade (½ SE tax, SE health
    # --- insurance, SE retirement) BEFORE the 20% is taken ---

    def test_se_deductions_reduce_qbi_base(self):
        """Schedule C net $100K, but ½ SE tax $7,065 + SEHI $6,000 + SEP $18,587
        are attributable to the trade. QBI = 100000 - 31652 = 68348;
        component = 20% x 68348 = 13669.60. Omitting the netting would give
        20% x 100000 = 20000 (a $6,330 overstatement)."""
        result = compute_qbi_deduction(
            100000, 0,
            half_se_tax=7065, se_health_insurance=6000, se_retirement=18587)
        assert result["se_deductions_attributable"] == 31652.00
        assert result["qbi_income_before_se_deductions"] == 100000.00
        assert result["qbi_income"] == 68348.00
        assert result["qbi_component"] == 13669.60
        assert result["total_deduction"] == 13669.60

    def test_se_deductions_default_zero_backward_compatible(self):
        """No SE-deduction args → QBI unchanged (legacy callers unaffected)."""
        result = compute_qbi_deduction(50000, 1000)
        assert result["se_deductions_attributable"] == 0.00
        assert result["qbi_income"] == 50000.00
        assert result["qbi_income_before_se_deductions"] == 50000.00
        assert result["qbi_component"] == 10000.00
        assert result["total_deduction"] == 10200.00

    def test_se_deductions_only_half_se_tax(self):
        """Only ½ SE tax supplied: QBI = 80000 - 5652 = 74348;
        component = 20% x 74348 = 14869.60."""
        result = compute_qbi_deduction(80000, 0, half_se_tax=5652)
        assert result["se_deductions_attributable"] == 5652.00
        assert result["qbi_component"] == 14869.60

    def test_se_deductions_do_not_touch_reit(self):
        """SE deductions net only the QBI component, never REIT dividends:
        QBI 40000 - 3000 = 37000 (component 7400); REIT 5000 (component 1000)."""
        result = compute_qbi_deduction(40000, 5000, half_se_tax=3000)
        assert result["qbi_component"] == 7400.00
        assert result["reit_component"] == 1000.00
        assert result["total_deduction"] == 8400.00

    def test_se_deductions_exceeding_qbi_create_loss_carryforward(self):
        """Pathological (SEHI/retirement are normally capped at net earnings):
        QBI 10000 - 12000 = -2000 → component 0, loss carryforward -2000."""
        result = compute_qbi_deduction(10000, 0, se_retirement=12000)
        assert result["qbi_component"] == 0.00
        assert result["loss_carryforward"] == -2000.00
        assert result["qbi_income"] == -2000.00

    def test_negative_net_qbi_does_not_offset_reit(self):
        """Form 8995 keeps the QBI and REIT/PTP buckets SEPARATE: a negative
        total QBI is floored at 0 for the QBI component (line 4 "if zero or less
        enter -0-", line 5) and the REIT component (lines 8-9) is added
        independently (line 10) — the buckets do NOT offset. Net QBI
        10000-12000=-2000 with $10K REIT → component 0 + 2000 = 2000 (NOT the
        combined-then-20% $1,600), and the -2000 carries forward (line 16)
        regardless of the positive REIT deduction. Pins the Form 8995 mechanics
        against a same-year QBI-loss-vs-REIT netting."""
        result = compute_qbi_deduction(10000, 10000, se_retirement=12000)
        assert result["qbi_component"] == 0.00
        assert result["reit_component"] == 2000.00
        assert result["total_deduction"] == 2000.00
        assert result["loss_carryforward"] == -2000.00

    def test_se_deduction_negative_raises(self):
        with pytest.raises(ValueError, match="reduces QBI"):
            compute_qbi_deduction(50000, 0, se_health_insurance=-1)

    def test_se_deduction_nan_raises(self):
        """json.loads accepts NaN/Infinity — they must not defeat the netting."""
        with pytest.raises(ValueError, match="finite"):
            compute_qbi_deduction(50000, 0, half_se_tax=float("inf"))

    def test_se_deduction_bool_raises(self):
        with pytest.raises(ValueError, match="finite"):
            compute_qbi_deduction(50000, 0, se_retirement=True)

    def test_se_deductions_net_before_income_cap(self):
        """The overall cap sees the netted component: QBI 100000, SE deds 40000 →
        component 12000. TI-before-QBI 55000, no cap gain → cap 11000 < 12000 →
        capped to 11000 (proves netting happens before the cap)."""
        result = compute_qbi_deduction(
            100000, 0, half_se_tax=40000,
            taxable_income_before_qbi=55000, net_capital_gain=0)
        assert result["qbi_component"] == 12000.00
        assert result["income_cap"] == 11000.00
        assert result["total_deduction"] == 11000.00
        assert result["income_limited"] is True

    def test_se_deductions_gate_70105_minimum_on_net_qbi(self):
        """§70105 floor gates on NET QBI: gross 1500 but SE deds 600 → net 900,
        below the $1,000 income_threshold → floor NOT applied (component 180)."""
        qm = {"income_threshold": 1000, "amount": 400}
        r = compute_qbi_deduction(
            1500, 0, tax_year=2026, qbi_minimum=qm,
            active_qualified_trade=True, half_se_tax=600)
        assert r["qbi_income"] == 900.00
        assert r["qbi_minimum_applied"] is False
        assert r["qbi_component"] == 180.00


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

    # The Form 8938 instructions' four rows, read from the ONE table the seed
    # and the engine's fallback share (three hand-mirrored copies carried the
    # foreign-resident single pair under `mfj_us` until the 2026-09-06
    # auditor replay found one of them).
    THRESHOLDS = create_empty_tax_law(2026)["federal"]["form_8938_thresholds"]

    def test_mfj_us_below_threshold(self):
        """MFJ US, totals AT the thresholds → not required ("more than")."""
        accts = [{"year_end_value_usd": 100000, "max_value_usd": 150000}]
        result = check_form_8938_required(accts, "mfj", False, self.THRESHOLDS)
        assert result["required"] is False

    def test_mfj_us_thresholds_are_the_instructions_row_everywhere(self):
        """A domestic MFJ couple with $120K abroad at year end IS required to
        file; the doubled pair said no. The seed, the engine fallback and this
        fixture must all carry the instructions' $100K / $150K."""
        from agents.lib.tax_law_fetcher import create_empty_tax_law
        seed = create_empty_tax_law(2026)["federal"]["form_8938_thresholds"]
        assert seed["mfj_us"] == {"year_end": 100000, "any_time": 150000}
        assert seed["single_foreign"] == {"year_end": 200000, "any_time": 300000}
        accts = [{"year_end_value_usd": 120000, "max_value_usd": 120000}]
        assert check_form_8938_required(accts, "mfj", False, seed)["required"] is True
        assert check_form_8938_required(accts, "mfj", False, None)["required"] is True
        assert check_form_8938_required(accts, "mfj", True, None)["required"] is False

    def test_mfj_us_year_end_triggers(self):
        """MFJ US, year-end exceeds threshold → required."""
        accts = [
            {"year_end_value_usd": 110000, "max_value_usd": 120000},
            {"year_end_value_usd": 100000, "max_value_usd": 130000},
        ]
        result = check_form_8938_required(accts, "mfj", False, self.THRESHOLDS)
        assert result["required"] is True  # total_ye=210K > 100K

    def test_mfj_us_any_time_triggers(self):
        """MFJ US, any-time max exceeds threshold → required."""
        accts = [{"year_end_value_usd": 100000, "max_value_usd": 310000}]
        result = check_form_8938_required(accts, "mfj", False, self.THRESHOLDS)
        assert result["required"] is True  # max=310K > 150K

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
        accts = [{"year_end_value_usd": 100000, "max_value_usd": 150000}]
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
        """A $0 prior-year base has TWO routes to a $0 requirement.

        §6654(e)(2) needs the prior year to have covered 12 months and the
        taxpayer to have been a US citizen/resident throughout it; it does not
        need a return to have been filed, and it clears the year outright.
        §6654(d)(1)(B)(ii) — the ORDINARY prong — needs a 12-month prior year
        and a filed return, and asks nothing about citizenship: 100%/110% of
        $0 is $0. They are independent, and a $0 figure evidences neither, so
        unconfirmed on BOTH the prong is DROPPED rather than taken at $0 and
        the 90%-of-current test stands alone.

        One flag standing for both is what shipped, and it cost the second
        route: a dual-status or first-year-resident filer with a full 12-month
        $0 return could not affirm citizenship, so the engine billed them 90%
        of the current year while tax-safe-harbor SKILL.md — which has listed
        the two as separate preconditions since it shipped — cleared them.
        The planning path (`projection._prior_tax_agi`) gates the same figure
        the same way; the three must agree.
        """
        confirmed = compute_form_2210(
            20000, 1000, "Single", 2025, prior_year_tax=0,
            prior_year_zero_liability_confirmed=True)
        assert confirmed["no_penalty"]
        assert "6654(e)(2)" in confirmed["no_penalty_reason"]

        # The ordinary prong, WITHOUT the citizenship confirmation.
        ordinary = compute_form_2210(
            20000, 1000, "Single", 2025, prior_year_tax=0,
            prior_year_return_filed_12_months_confirmed=True)
        assert ordinary["required_annual_payment"] == 0.0, (
            "a confirmed filed 12-month $0 prior year was refused the "
            "ordinary prong, which has no citizenship condition")
        assert "6654(e)(2)" not in (ordinary["no_penalty_reason"] or ""), (
            "the ordinary prong was reported as the citizenship exception — "
            "two different findings, and only one of them was confirmed")
        assert any("6654(d)(1)(B)" in n for n in ordinary["notes"])
        # The factor cannot matter against $0, so no caveat may claim it does.
        assert not any("110%" in n and "UNDERSTATED" in n
                       for n in ordinary["notes"]), (
            "a caveat that is false where it fires teaches the reader to "
            "discount the ones that are true")

        unconfirmed = compute_form_2210(20000, 1000, "Single", 2025,
                                        prior_year_tax=0)
        assert not unconfirmed["no_penalty"], (
            "a $0 prior-year figure still clears the year unasked")
        assert unconfirmed["required_annual_payment"] == 18000.0, (
            "the dropped prong came back as a $0 prior-year prong, which "
            "zeroes the requirement by another route")
        assert any("6654(e)(2)" in n and "6654(d)(1)(B)" in n
                   for n in unconfirmed["notes"]), (
            "the run overstates while naming at most one of the two ways out")

    def test_the_dropped_zero_prong_does_not_also_claim_the_figures_are_absent(self):
        """One situation, one instruction.

        The unconfirmed-$0 path sets `prior_year_tax = None`, which fell into
        the branch for figures that were never supplied: the caller was told
        the prior-year tax/AGI were "unavailable" (they had just passed them)
        and that supplying them "can only LOWER" the requirement — where
        CONFIRMING them clears the year outright. Both notes in one result,
        each contradicting the other.
        """
        dropped = compute_form_2210(20000, 1000, "Single", 2025,
                                    prior_year_tax=0)
        absent = compute_form_2210(20000, 1000, "Single", 2025)

        unavailable = [n for n in dropped["notes"]
                       if "prior-year tax/AGI unavailable" in n]
        assert not unavailable, (
            "the dropped-$0 run also claims the prior-year figures were "
            f"never supplied: {unavailable}")
        assert any("can only\nLOWER" in n or "can only LOWER" in n
                   for n in absent["notes"]), (
            "the genuinely-absent case lost the note this test exempts the "
            "dropped case from — it is the one that still needs it")

        # The basis strings say which of the two happened, so a reader of
        # `required_basis` alone is not left to guess.
        assert dropped["required_basis"] != absent["required_basis"]
        assert "prong dropped" in dropped["required_basis"]
        assert "unavailable" in absent["required_basis"]
        # Both still land on the same 90%-of-current requirement.
        assert (dropped["required_annual_payment"]
                == absent["required_annual_payment"] == 18000.0)

    def test_the_missing_agi_note_quotes_the_threshold_the_prong_tests(self):
        """The note's figure and the prong's figure are the same selection.

        `prior_year_agi_threshold` exists because the threshold has a SECOND
        reader — this note. Hand-selecting it beside the prong that selects it
        internally is the two-copy drift the helper was extracted to remove,
        and nothing related the two until this test.
        """
        for status, mfs in (("Single", False), ("MFS", True)):
            r = compute_form_2210(20000, 1000, status, 2025,
                                  prior_year_tax=10000, prior_year_agi=None)
            note = next(n for n in r["notes"] if "prior_year_agi unavailable" in n)
            expected = prior_year_agi_threshold(mfs)
            assert f"${expected:,.0f} threshold" in note, (
                f"{status}: the note quotes a threshold the prong does not "
                f"test against (expected ${expected:,.0f}) — {note}")
            # And the prong really did take the 100% factor it describes.
            assert f"{prior_year_prong(10000.0, None, mfs)[0]:.0%}" in \
                r["required_basis"]

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
        compute_md = " ".join(skill_text("tax-compute").split())
        assert "mfs_spouse_itemizes" in compute_md
        assert "63(c)(6)(A)" in compute_md
        assert "mandatory itemization" in compute_md.lower()


class TestESPPDisposition:
    """J2 — §423 ESPP qualifying/disqualifying split (Form 3922 boxes 3/4/5/8).

    Hand-computed per IRC §423(c) / Pub 525. Per-share boxes: box3 grant FMV,
    box4 purchase FMV, box5 price paid; ×100 shares. box5=$8.50 (a 15% discount
    off the $10 grant FMV); box4=$12 purchase FMV → price-total $850."""

    def test_disqualifying_gain_sale(self):
        # Sold 2024-12-01, within 1 yr of the 2024-06-30 purchase → disqualifying.
        # Ordinary = (12 − 8.5)×100 = 350; basis = 850 + 350 = 1200;
        # gain = 1500 − 1200 = 300; short-term (held < 1 yr from purchase).
        r = compute_espp_disposition(100, 10, 12, 8.5, 1500,
                                     "2024-01-01", "2024-06-30", "2024-12-01")
        assert r["classification"] == "disqualifying"
        assert r["ordinary_income"] == 350.0
        assert r["capital_basis"] == 1200.0
        assert r["capital_gain"] == 300.0
        assert r["term"] == "short"

    def test_disqualifying_loss_sale_ordinary_fixed(self):
        # Sold at a loss (900 = $9/sh), but the disqualifying ordinary element
        # is STILL box4−box5 = 350 regardless of sale price; capital loss −300.
        r = compute_espp_disposition(100, 10, 12, 8.5, 900,
                                     "2024-01-01", "2024-06-30", "2024-12-01")
        assert r["ordinary_income"] == 350.0
        assert r["capital_gain"] == -300.0
        assert r["term"] == "short"

    def test_qualifying_gain_exceeds_discount(self):
        # > 2 yr grant AND > 1 yr purchase → qualifying. Grant discount =
        # (10 − 8.5)×100 = 150; actual gain = 2000 − 850 = 1150; ordinary =
        # lesser = 150; basis 1000; LTCG 1000.
        r = compute_espp_disposition(100, 10, 12, 8.5, 2000,
                                     "2020-01-01", "2020-06-30", "2023-01-01")
        assert r["classification"] == "qualifying"
        assert r["ordinary_income"] == 150.0
        assert r["capital_basis"] == 1000.0
        assert r["capital_gain"] == 1000.0
        assert r["term"] == "long"
        # Codex PR #47 round 2: a qualifying disposition's ordinary component is
        # generally NOT on the W-2 → the notes must direct line-1h reporting,
        # and box3−box5 is the exact §423(c)(2) limit for a non-lookback plan
        # (a conservative upper bound only for lookback), not a generic proxy.
        joined = " ".join(r["notes"])
        assert "line 1h" in joined
        assert "UPPER BOUND" in joined and "lookback" in joined.lower()

    def test_qualifying_gain_below_discount(self):
        # Actual gain (900 − 850 = 50) < discount (150) → ordinary = 50, no LTCG.
        r = compute_espp_disposition(100, 10, 12, 8.5, 900,
                                     "2020-01-01", "2020-06-30", "2023-01-01")
        assert r["ordinary_income"] == 50.0
        assert r["capital_gain"] == 0.0

    def test_qualifying_loss_sale_ordinary_zero(self):
        # Loss sale (800 < 850 basis): actual gain floored at 0 → ordinary 0,
        # never negative; capital loss −50.
        r = compute_espp_disposition(100, 10, 12, 8.5, 800,
                                     "2020-01-01", "2020-06-30", "2023-01-01")
        assert r["ordinary_income"] == 0.0
        assert r["capital_gain"] == -50.0

    def test_exact_two_year_grant_anniversary_is_disqualifying(self):
        # Sold exactly 2 years after grant (2022-01-01) — the >2yr test is
        # STRICT, so this fails the grant leg → disqualifying.
        r = compute_espp_disposition(100, 10, 12, 8.5, 2000,
                                     "2020-01-01", "2020-06-30", "2022-01-01")
        assert r["classification"] == "disqualifying"

    def test_just_past_both_anniversaries_is_qualifying(self):
        # One day past the grant+2yr anniversary, well past purchase+1yr.
        r = compute_espp_disposition(100, 10, 12, 8.5, 2000,
                                     "2020-01-01", "2020-06-30", "2022-01-02")
        assert r["classification"] == "qualifying"

    def test_purchase_before_grant_hard_errors(self):
        with pytest.raises(ValueError, match="precedes grant_date"):
            compute_espp_disposition(100, 10, 12, 8.5, 1500,
                                     "2024-06-30", "2024-01-01", "2024-12-01")

    def test_sale_before_purchase_hard_errors(self):
        with pytest.raises(ValueError, match="precedes purchase_date"):
            compute_espp_disposition(100, 10, 12, 8.5, 1500,
                                     "2024-01-01", "2024-06-30", "2024-05-01")

    def test_rejects_bad_numeric_inputs(self):
        base = dict(shares=100, grant_fmv_per_share=10, purchase_fmv_per_share=12,
                    price_paid_per_share=8.5, sale_proceeds=1500,
                    grant_date="2024-01-01", purchase_date="2024-06-30",
                    sale_date="2024-12-01")
        for bad in (dict(shares=0), dict(shares=-1), dict(shares=True),
                    dict(sale_proceeds=float("nan")),
                    dict(price_paid_per_share=float("inf")),
                    dict(grant_date="not-a-date")):
            kw = dict(base)
            kw.update(bad)
            with pytest.raises(ValueError):
                compute_espp_disposition(**kw)


class TestEquityCompWiring:
    """J1/J2 — the equity-comp basis-adjustment plumbing that lives in
    templates and skills (pinned so it can't silently regress)."""

    def _repo(self):
        return Path(__file__).resolve().parent.parent

    def test_flags_in_template_and_interview(self):
        template = json.loads(
            (self._repo() / "agents/templates/taxpayer_profile_template.json").read_text())
        for flag in ("has_rsu_vesting", "has_espp"):
            assert flag in template["flags"], flag
        setup_md = skill_text("tax-setup")
        assert "has_rsu_vesting" in setup_md and "has_espp" in setup_md

    def test_schedule_d_rows_carry_adjustment_and_code_b_note(self):
        template = json.loads(
            (self._repo() / "agents/templates/result_template.json").read_text())
        sd = template["schedule_d"]
        assert "_note_8949_code_b" in sd
        # Every box detail row (short- and long-term) carries a signed adjustment.
        for part in ("part_i_short_term", "part_ii_long_term"):
            box_rows = [v for k, v in sd[part].items()
                        if isinstance(v, dict) and "adjustment" in v]
            assert box_rows, part
            for row in box_rows:
                assert set(("proceeds", "cost", "adjustment", "gain")) <= set(row)

    def test_line_1h_present_for_espp_dd_routing(self):
        template = json.loads(
            (self._repo() / "agents/templates/result_template.json").read_text())
        assert "line_1h_other_earned_income" in template["form_1040"]

    def test_tie_out_category_registered_and_documented(self):
        from agents.lib.tie_out import CATEGORY_TARGETS, _INFORMATIONAL
        assert "employer_stock_basis_adjustment" in CATEGORY_TARGETS
        assert "espp_purchase_info" in _INFORMATIONAL
        parse_md = (self._repo() / ".agents/skills/tax-parse/SKILL.md").read_text()
        assert "employer_stock_basis_adjustment" in parse_md
        assert "espp_purchase_info" in parse_md


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
        setup_md = skill_text("tax-setup")
        assert "qbi_active_trade" in setup_md
        compute_md = skill_text("tax-compute")
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


class TestComputeLateFilingPenalty:
    """Tests for compute_late_filing_penalty — §6651 FTF/FTP additions to tax."""

    def test_classic_five_months_both(self):
        """$10K unpaid, 5 months late (both). FTF 25% gross − 2.5% overlap =
        22.5% = $2,250; FTP 2.5% = $250; combined $2,500 (the classic 25%)."""
        r = compute_late_filing_penalty(10000, 5, 5)
        assert r["ftf_penalty_gross"] == 2500.00
        assert r["overlap_reduction"] == 250.00
        assert r["ftf_penalty"] == 2250.00
        assert r["ftp_penalty"] == 250.00
        assert r["total_penalty"] == 2500.00

    def test_ftf_caps_at_25_percent(self):
        """10 months late: FTF caps at 25% (5 months accrual), FTP keeps running.
        FTF 25% − 2.5% overlap = 22.5% = $2,250; FTP 5% = $500; total $2,750."""
        r = compute_late_filing_penalty(10000, 10, 10)
        assert r["ftf_penalty_gross"] == 2500.00   # capped at 25%
        assert r["overlap_reduction"] == 250.00     # only 5 accrual months overlap
        assert r["ftf_penalty"] == 2250.00
        assert r["ftp_penalty"] == 500.00
        assert r["total_penalty"] == 2750.00

    def test_ftp_caps_at_25_percent_combined_max(self):
        """FTP caps at 25% after 50 months; with FTF's 22.5% the combined max is
        ~47.5%. $10K, FTF 5 / FTP 60 → $2,250 + $2,500 = $4,750."""
        r = compute_late_filing_penalty(10000, 5, 60)
        assert r["ftp_penalty"] == 2500.00          # capped at 25%
        assert r["ftf_penalty"] == 2250.00
        assert r["total_penalty"] == 4750.00

    def test_minimum_penalty_applies_small_balance(self):
        """Return filed >60 days late, small balance: §6651(a) minimum = lesser of
        the floor ($485) or 100% of tax due ($300) → $300, which exceeds the
        computed FTF ($40.50) → FTF floored to $300. FTP ($4.50) is separate."""
        r = compute_late_filing_penalty(300, 3, 3, filed_over_60_days_late=True,
                                        min_penalty_floor=485)
        assert r["ftf_penalty"] == 300.00
        assert r["minimum_penalty"] == 300.00
        assert r["minimum_penalty_applied"] is True
        assert r["ftp_penalty"] == 4.50
        assert r["total_penalty"] == 304.50
        # The percentage FTF (before the minimum) still reconciles to the
        # gross/reduction breakdown even though the minimum overrides the final.
        assert r["ftf_penalty_before_minimum"] == 40.50
        assert round(r["ftf_penalty_gross"] - r["overlap_reduction"], 2) == \
            r["ftf_penalty_before_minimum"]

    def test_minimum_penalty_uses_floor_when_lesser(self):
        """>60 days late, 1 month, $5K balance: computed FTF is 5% = $250, but the
        §6651(a) minimum = lesser($485 floor, $5,000) = $485 > $250 → FTF $485."""
        r = compute_late_filing_penalty(5000, 1, 0, filed_over_60_days_late=True,
                                        min_penalty_floor=485)
        assert r["ftf_penalty"] == 485.00
        assert r["minimum_penalty"] == 485.00
        assert r["minimum_penalty_applied"] is True
        assert r["total_penalty"] == 485.00

    def test_minimum_not_applied_when_computed_exceeds_floor(self):
        """A large balance's percentage FTF already exceeds the floor → no bump."""
        r = compute_late_filing_penalty(50000, 3, 0, filed_over_60_days_late=True,
                                        min_penalty_floor=485)
        assert r["ftf_penalty"] == 7500.00          # 15% x 50000
        assert r["minimum_penalty_applied"] is False

    def test_fully_paid_but_late_is_zero(self):
        """No unpaid tax → $0 of both penalties (and $0 minimum), even >60 days
        late: the FTF penalty is measured on the net amount due."""
        r = compute_late_filing_penalty(0, 5, 5, filed_over_60_days_late=True,
                                        min_penalty_floor=485)
        assert r["total_penalty"] == 0.00
        assert r["minimum_penalty_applied"] is False

    def test_no_minimum_when_not_over_60_days(self):
        """Filed within 60 days → no §6651(a) minimum floor, just the percentages."""
        r = compute_late_filing_penalty(300, 1, 1, filed_over_60_days_late=False,
                                        min_penalty_floor=485)
        assert r["minimum_penalty_applied"] is False
        assert r["ftf_penalty"] == 13.50            # 5% x 300 − 0.5% overlap
        assert r["ftp_penalty"] == 1.50
        assert r["total_penalty"] == 15.00

    def test_over_60_days_without_floor_degrades_with_note(self):
        """>60 days late but no floor supplied → minimum NOT applied, loud note
        (don't silently understate); percentages still computed."""
        r = compute_late_filing_penalty(300, 3, 3, filed_over_60_days_late=True)
        assert r["minimum_penalty_applied"] is False
        assert r["minimum_penalty"] is None
        assert "not supplied" in r["note"]

    def test_no_overlap_when_ftp_months_zero(self):
        """FTF with no overlapping FTP month keeps the full 5%/month (no §6651(c)
        reduction). $10K, FTF 2 / FTP 0 → 10% = $1,000, no reduction."""
        r = compute_late_filing_penalty(10000, 2, 0)
        assert r["overlap_reduction"] == 0.00
        assert r["ftf_penalty"] == 1000.00
        assert r["ftp_penalty"] == 0.00

    def test_negative_unpaid_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_late_filing_penalty(-1, 1, 1)

    def test_bool_months_raises(self):
        with pytest.raises(ValueError, match="non-negative int"):
            compute_late_filing_penalty(1000, True, 1)

    def test_float_months_raises(self):
        with pytest.raises(ValueError, match="non-negative int"):
            compute_late_filing_penalty(1000, 1.5, 1)

    def test_nan_unpaid_raises(self):
        with pytest.raises(ValueError, match="finite"):
            compute_late_filing_penalty(float("nan"), 1, 1)

    def test_rate_over_one_raises(self):
        with pytest.raises(ValueError, match="fraction in"):
            compute_late_filing_penalty(1000, 1, 1, ftf_rate=1.5)

    def test_string_rate_coerced_not_typeerror(self):
        """Codex P3 (PR #52): a coercible string rate must be used as a float in
        the arithmetic (not raise TypeError at the multiply). ftf_rate='0.05'
        behaves exactly like 0.05 → $10K, 5/5 months → $2,500 total."""
        r = compute_late_filing_penalty(10000, 5, 5, ftf_rate="0.05")
        assert r["total_penalty"] == 2500.00
        # A truly invalid string still raises the documented ValueError.
        with pytest.raises(ValueError, match="fraction in"):
            compute_late_filing_penalty(10000, 5, 5, ftf_rate="not-a-rate")

    def test_overlap_accrual_months_ceil_for_noninteger_ratio(self):
        """Codex P2 (PR #52): with an override making cap/rate non-integer
        (0.25/0.06 = 4.17), FTF still accrues in the 5th month (reaching the 25%
        cap), so all 5 concurrent FTP months offset it — NOT 4 (round). $10K,
        5/5 months, ftf_rate=0.06: FTF gross 25% = $2,500, overlap 5 × 0.5% =
        $250 → FTF $2,250 (round-to-4 would wrongly give $2,300 → $2,550 total)."""
        r = compute_late_filing_penalty(10000, 5, 5, ftf_rate=0.06)
        assert r["ftf_penalty_gross"] == 2500.00
        assert r["overlap_reduction"] == 250.00
        assert r["ftf_penalty"] == 2250.00
        assert r["total_penalty"] == 2500.00

    def test_note_flags_partial_payment_and_post_notice_rate(self):
        """Codex P2/P1 (PR #52): the scope limitations are surfaced loudly in the
        note — the FTF base is fixed at the due-date balance per §6651(b)(1) (a
        later payment doesn't shrink it), and §6651(d) raises FTP post-notice."""
        r = compute_late_filing_penalty(10000, 3, 3)
        assert "§6651(b)(1)" in r["note"]
        assert "FTF base" in r["note"]
        assert "§6651(d)" in r["note"]
        assert "NOT MODELED" in r["note"]  # both the partial-payment + mixed-rate scopes
        assert "UNDER-reported" in r["note"]  # separate FTF/FTP bases scope

    def test_overlap_reduction_capped_at_accrued_ftp(self):
        """Codex P2 (PR #52): the §6651(c)(1) reduction can't exceed the FTP that
        actually accrued (capped by ftp_cap). With ftp_rate=0.10, ftp_cap=0.10,
        $1,000, 5/5 months: FTP is capped at 10% = $100, and the FTF offset is
        that same $100 (not 0.10×5=50% = $500), so FTF = 25% − 10% = $150 and the
        combined total is $250 (the 25% combined amount), not $100."""
        r = compute_late_filing_penalty(1000, 5, 5, ftp_rate=0.10, ftp_cap=0.10)
        assert r["ftp_penalty"] == 100.00
        assert r["overlap_reduction"] == 100.00
        assert r["ftf_penalty"] == 150.00
        assert r["total_penalty"] == 250.00

    def test_breakdown_reconciles_to_the_cent(self):
        """Codex P3 (PR #52, both rounds): every displayed field reconciles —
        ftf_penalty == ftf_penalty_gross − overlap_reduction, and total_penalty
        == ftf_penalty + ftp_penalty — because gross, reduction, and the two
        components are all rounded at source. unpaid=100.10, 3/3 → gross $15.02,
        reduction $1.50, FTF $13.52, FTP $1.50, total $15.02."""
        r = compute_late_filing_penalty(100.10, 3, 3)
        assert r["ftf_penalty_gross"] == 15.02
        assert r["overlap_reduction"] == 1.50
        assert r["ftf_penalty"] == 13.52
        assert r["ftp_penalty"] == 1.50
        assert r["total_penalty"] == 15.02
        # The reconciliation identities the returned detail must satisfy:
        assert r["ftf_penalty_before_minimum"] == 13.52
        assert round(r["ftf_penalty_gross"] - r["overlap_reduction"], 2) == \
            r["ftf_penalty_before_minimum"]
        # No minimum here, so ftf_penalty == the percentage figure.
        assert r["ftf_penalty"] == r["ftf_penalty_before_minimum"]
        assert round(r["ftf_penalty"] + r["ftp_penalty"], 2) == r["total_penalty"]


# 8%/365 daily compounding over 183 days (2026, non-leap) on $10,000
# = 10000*(1+0.08/365)**183 − 10000 = $409.20 (hand-computed).
_RATES_8PCT_2026 = [
    {"effective_from": "2026-04-01", "annual_rate": 0.08},
    {"effective_from": "2026-07-01", "annual_rate": 0.08},
    {"effective_from": "2026-10-01", "annual_rate": 0.08},
]


class TestComputeUnderpaymentInterest:
    """Tests for compute_underpayment_interest — §6601/§6622 daily-compounded
    interest on a late-paid balance (M3). Expected figures are hand-computed
    from the daily-compounding closed form (1 + r/D)**n."""

    def test_full_coverage_daily_compounded(self):
        """TY2025 $10,000 balance, due 2026-04-15, paid 2026-10-15 (183 days),
        8% all three quarters. Daily-compounded interest = $409.20."""
        r = compute_underpayment_interest(
            10000, "2026-04-15", "2026-10-15", _RATES_8PCT_2026)
        assert r["computed"] is True
        assert r["complete"] is True
        assert r["interest"] == 409.20
        assert r["days"] == 183
        assert r["covered_through"] == "2026-10-15"

    def test_rate_change_mid_period_splits_daily(self):
        """7% Q2 then 9% Q3: Apr15→Jul1 (77d @7%) then Jul1→Aug15 (45d @9%),
        compounded across the change = $261.98."""
        rates = [
            {"effective_from": "2026-04-01", "annual_rate": 0.07},
            {"effective_from": "2026-07-01", "annual_rate": 0.09},
        ]
        r = compute_underpayment_interest(10000, "2026-04-15", "2026-08-15", rates)
        assert r["complete"] is True
        assert r["interest"] == 261.98

    def test_leap_year_uses_366_denominator(self):
        """2024 is a leap year: 8% over Apr15→Jul15 (91 days) uses /366 →
        $200.88 (vs $201.44 with /365)."""
        rates = [
            {"effective_from": "2024-04-01", "annual_rate": 0.08},
            {"effective_from": "2024-07-01", "annual_rate": 0.08},
        ]
        r = compute_underpayment_interest(10000, "2024-04-15", "2024-07-15", rates)
        assert r["interest"] == 200.88

    def test_partial_coverage_stops_at_last_quarter_loudly(self):
        """The Oct-15-extension case: only Q2 2026 is cached (the 2210 fetch
        window stops ~Apr). Interest is reported PARTIAL through Jul 1 (the end
        of the last cached quarter) — never extrapolating 8% across Q3/Q4."""
        r = compute_underpayment_interest(
            10000, "2026-04-15", "2026-10-15",
            [{"effective_from": "2026-04-01", "annual_rate": 0.08}])
        assert r["computed"] is True
        assert r["complete"] is False
        assert r["covered_through"] == "2026-07-01"
        assert r["days"] == 77  # Apr 15 → Jul 1
        # interest accrued only through Jul 1, not the full span
        assert r["interest"] == 170.18
        assert "PARTIAL" in r["note"]
        assert "2026-07-01..2026-10-15" in r["note"]

    def test_opening_gap_refuses_rather_than_understates(self):
        """If the rate for the due-date quarter is absent (earliest cached rate
        is a later quarter), computing from it would misstate the opening
        interest — refuse."""
        r = compute_underpayment_interest(
            10000, "2026-04-15", "2026-10-15",
            [{"effective_from": "2026-07-01", "annual_rate": 0.08}])
        assert r["computed"] is False
        assert r["interest"] is None
        assert "due-date quarter" in r["note"]

    def test_due_date_quarter_absent_refuses(self):
        """Codex P1 (PR #54): a Q1-only cache does NOT cover an April (Q2) due
        date — the Q1 rate is authoritative only for Jan-Mar. Refuse rather than
        applying the Q1 rate to Q2."""
        r = compute_underpayment_interest(
            10000, "2026-04-15", "2026-10-15",
            [{"effective_from": "2026-01-01", "annual_rate": 0.08}])
        assert r["computed"] is False
        assert r["interest"] is None
        assert "2026-04-01" in r["note"]

    def test_mid_timeline_gap_stops_at_gap_not_falsely_complete(self):
        """Codex P1 (PR #54): a cache with Q2 and Q4 but NO Q3 must NOT report
        complete=True by applying the Q2 rate through the missing Q3. Coverage
        stops at Jul 1 (the first gap) — partial, not falsely complete."""
        gap = [{"effective_from": "2026-04-01", "annual_rate": 0.08},
               {"effective_from": "2026-10-01", "annual_rate": 0.08}]  # Jul missing
        r = compute_underpayment_interest(10000, "2026-04-15", "2026-10-15", gap)
        assert r["complete"] is False
        assert r["covered_through"] == "2026-07-01"
        assert r["days"] == 77  # Apr 15 → Jul 1, the Q2 rate only
        assert r["interest"] == 170.18
        assert "PARTIAL" in r["note"]

    def test_empty_rates_degrades_not_computed(self):
        r = compute_underpayment_interest(10000, "2026-04-15", "2026-10-15", [])
        assert r["computed"] is False
        assert r["interest"] is None
        assert "unfetched" in r["note"]

    def test_paid_on_due_date_zero_interest(self):
        r = compute_underpayment_interest(
            10000, "2026-04-15", "2026-04-15", _RATES_8PCT_2026)
        assert r["computed"] is True
        assert r["complete"] is True
        assert r["interest"] == 0.0
        assert r["days"] == 0

    def test_zero_balance_zero_interest(self):
        r = compute_underpayment_interest(
            0, "2026-04-15", "2026-10-15", _RATES_8PCT_2026)
        assert r["interest"] == 0.0

    def test_pay_before_due_raises(self):
        with pytest.raises(ValueError, match="before due_date"):
            compute_underpayment_interest(
                100, "2026-04-15", "2026-04-14", _RATES_8PCT_2026)

    def test_nan_balance_raises(self):
        with pytest.raises(ValueError):
            compute_underpayment_interest(
                float("nan"), "2026-04-15", "2026-10-15", _RATES_8PCT_2026)

    def test_negative_balance_raises(self):
        with pytest.raises(ValueError):
            compute_underpayment_interest(
                -100, "2026-04-15", "2026-10-15", _RATES_8PCT_2026)

    def test_bool_balance_raises(self):
        with pytest.raises(ValueError):
            compute_underpayment_interest(
                True, "2026-04-15", "2026-10-15", _RATES_8PCT_2026)

    def test_corrupt_zero_rate_raises(self):
        """§6621 can never be 0 — a 0 in the cache is a fetch bug (silent
        understatement), hard-error like compute_form_2210."""
        with pytest.raises(ValueError):
            compute_underpayment_interest(
                100, "2026-04-15", "2026-10-15",
                [{"effective_from": "2026-04-01", "annual_rate": 0}])

    def test_malformed_date_raises(self):
        with pytest.raises(ValueError):
            compute_underpayment_interest(
                100, "not-a-date", "2026-10-15", _RATES_8PCT_2026)

    def test_datetime_inputs_normalized_to_date(self):
        """Codex P3 (PR #54): a datetime.datetime (subclass of date) must be
        normalized to a date, not retained — otherwise a mixed date/datetime
        compare raises a raw TypeError. Passing datetimes matches the ISO-string
        result exactly."""
        import datetime as _dt
        r = compute_underpayment_interest(
            10000, _dt.datetime(2026, 4, 15, 9, 30),
            _dt.datetime(2026, 10, 15, 0, 0), _RATES_8PCT_2026)
        assert r["computed"] is True
        assert r["interest"] == 409.20
        assert r["due_date"] == "2026-04-15"


class TestCaSchedulePAMT:
    """CA Schedule P (540) AMT (N1) — compute_ca_schedule_p. Expected values
    hand-computed from R&TC §17062 (7% flat TMT, 25% exemption phase-out) with
    representative CA 2025 indexed figures (single exemption $92,749, phase-out
    start $347,808; MFJ exemption $123,667, phase-out start $463,809)."""

    def test_iso_exercise_triggers_ca_amt(self):
        """CA taxable $300K + ISO spread $200K, CA regular tax $25K.
        AMTI = 500,000; exemption 92,749 − 0.25×(500,000−347,808) = 54,701;
        TMT = 0.07×(500,000−54,701) = 31,170.93; CA AMT = 31,170.93 − 25,000
        = 6,170.93 (all deferral-attributable)."""
        r = compute_ca_schedule_p(
            ca_taxable_income=300000, ca_regular_tax=25000,
            filing_status="Single", exemption=92749, phaseout_start=347808,
            iso_bargain_element=200000)
        assert r["ca_amti"] == 500000.0
        assert r["ca_exemption_allowed"] == 54701.0
        assert r["ca_tmt"] == 31170.93
        assert r["ca_amt"] == 6170.93
        assert r["schedule_p_carryforward"] == 6170.93

    def test_no_iso_no_ca_amt(self):
        """Same regular tax, no ISO: TMT (14,507.57) < regular tax (25,000) →
        $0 CA AMT and $0 carryforward. Pins the 'no-ISO CA return unchanged'
        acceptance."""
        r = compute_ca_schedule_p(
            ca_taxable_income=300000, ca_regular_tax=25000,
            filing_status="Single", exemption=92749, phaseout_start=347808,
            iso_bargain_element=0)
        assert r["ca_amt"] == 0.0
        assert r["schedule_p_carryforward"] == 0.0

    def test_exemption_fully_phased_out(self):
        """AMTI far above the phase-out ceiling zeroes the exemption. Single
        ceiling = 347,808 + 92,749/0.25 = 718,804. AMTI 800,000 → exemption 0;
        TMT = 0.07×800,000 = 56,000."""
        r = compute_ca_schedule_p(
            ca_taxable_income=600000, ca_regular_tax=40000,
            filing_status="Single", exemption=92749, phaseout_start=347808,
            iso_bargain_element=200000)
        assert r["ca_exemption_allowed"] == 0.0
        assert r["ca_tmt"] == 56000.0
        assert r["ca_amt"] == 16000.0

    def test_mfj_status_uses_its_exemption(self):
        r = compute_ca_schedule_p(
            ca_taxable_income=400000, ca_regular_tax=30000,
            filing_status="MFJ", exemption=123667, phaseout_start=463809,
            iso_bargain_element=200000)
        # AMTI 600,000; exemption 123,667 − 0.25×(600,000−463,809)=123,667−34,047.75
        # = 89,619.25; TMT 0.07×(600,000−89,619.25)=0.07×510,380.75=35,726.65;
        # CA AMT 35,726.65 − 30,000 = 5,726.65
        assert r["ca_exemption_allowed"] == 89619.25
        assert r["ca_tmt"] == 35726.65
        assert r["ca_amt"] == 5726.65

    def test_deferral_split_isolates_iso_portion(self):
        """When there are non-ISO CA preferences too, only the ISO-attributable
        AMT is the carryforward. Adjustments 50K (exclusion) + ISO 200K, regular
        tax 15K so the exclusion part alone produces AMT.
        Without ISO: AMTI 350,000 (just over the 347,808 phase-out start);
        exemption 92,749−0.25×(350,000−347,808)=92,201; TMT 0.07×(350,000−92,201)
        =18,045.93; AMT_excl 18,045.93−15,000=3,045.93.
        Full: AMTI 550,000; exemption 92,749−0.25×(550,000−347,808)=42,201;
        TMT 0.07×(550,000−42,201)=35,545.93; AMT 35,545.93−15,000=20,545.93.
        Carryforward = 20,545.93 − 3,045.93 = 17,500.00 (only the ISO delta)."""
        r = compute_ca_schedule_p(
            ca_taxable_income=300000, ca_regular_tax=15000,
            filing_status="Single", exemption=92749, phaseout_start=347808,
            ca_preference_adjustments=50000, iso_bargain_element=200000)
        assert r["ca_amt"] == 20545.93
        assert r["schedule_p_carryforward"] == 17500.00
        assert r["ca_amt"] > r["schedule_p_carryforward"]  # exclusion part not in cf

    def test_unfetched_exemption_hard_errors(self):
        with pytest.raises(ValueError, match="unfetched"):
            compute_ca_schedule_p(
                300000, 25000, "Single", None, 347808, iso_bargain_element=200000)

    def test_unfetched_phaseout_start_hard_errors(self):
        with pytest.raises(ValueError, match="unfetched"):
            compute_ca_schedule_p(
                300000, 25000, "Single", 92749, None, iso_bargain_element=200000)

    def test_negative_ca_taxable_income_accepted(self):
        """Deductions exceeding CA AGI → negative line 19; AMTI still adds the
        ISO spread. AMTI = −5,000 + 150,000 = 145,000; exemption 123,667 (no
        phase-out); TMT 0.07×(145,000−123,667) = 1,493.31; regular 0 → AMT."""
        r = compute_ca_schedule_p(
            ca_taxable_income=-5000, ca_regular_tax=0, filing_status="MFJ",
            exemption=123667, phaseout_start=463809, iso_bargain_element=150000)
        assert r["ca_amt"] == 1493.31

    def test_nan_rate_rejected(self):
        with pytest.raises(ValueError):
            compute_ca_schedule_p(
                300000, 25000, "Single", 92749, 347808,
                iso_bargain_element=200000, tmt_rate=float("nan"))

    def test_iso_bargain_echoed(self):
        r = compute_ca_schedule_p(
            300000, 25000, "Single", 92749, 347808, iso_bargain_element=200000)
        assert r["iso_bargain_element"] == 200000.0

    def test_breakdown_reconciles(self):
        """Self-review: the returned breakdown must reconcile — ca_amt ==
        round(ca_tmt − ca_regular_tax) exactly (rounded at source), even at a
        rounding boundary. Odd figures that leave a half-cent in the raw TMT."""
        r = compute_ca_schedule_p(
            ca_taxable_income=321433, ca_regular_tax=18761.07,
            filing_status="Single", exemption=92749, phaseout_start=347808,
            iso_bargain_element=133337)
        assert r["ca_amt"] == round(r["ca_tmt"] - r["ca_regular_tax"], 2)


class TestCaSchedulePMfsCaveat:
    """MFS-past-ceiling caveat (self-review addition) — CA exemption phases to
    $0 (modeled), but the §55(d)(3) MFS recapture add-back is flagged not
    modeled (unconfirmed on Schedule P; inventing it would overstate)."""

    def test_mfs_past_ceiling_flags_recapture_caveat(self):
        # MFS 2024: exemption 58,111, phase-out start 217,924, ceiling
        # 217,924 + 58,111/0.25 = 450,368. AMTI 500,000 > ceiling.
        r = compute_ca_schedule_p(
            ca_taxable_income=300000, ca_regular_tax=10000,
            filing_status="MFS", exemption=58111, phaseout_start=217924,
            iso_bargain_element=200000)
        assert r["ca_exemption_allowed"] == 0.0  # fully phased out (modeled)
        assert any("§55(d)(3)" in n and "recapture" in n for n in r["notes"])

    def test_mfs_below_ceiling_no_caveat(self):
        r = compute_ca_schedule_p(
            ca_taxable_income=150000, ca_regular_tax=5000,
            filing_status="MFS", exemption=58111, phaseout_start=217924,
            iso_bargain_element=50000)
        assert not any("recapture" in n for n in r["notes"])


class TestForm8995A:
    """Form 8995-A (N2) — §199A wage/UBIA limitation + SSTB phase-out. Expected
    values hand-computed from the 8995-A worksheet. 2024 MFJ threshold 383,900,
    pre-2026 phase-in range 100,000."""

    def test_nonsstb_above_range_wage_limited(self):
        """qbi 300k, wages 100k, ubia 500k, TI 500k (excess 116,100 ≥ 100k range).
        limit = max(50k, 25k+12.5k)=50k; tentative 60k → min(60k, 50k)=50k."""
        r = compute_form_8995a(300000, 100000, 500000, False, 500000, 383900, 100000)
        assert r["qbi_component"] == 50000.0
        assert r["regime"] == "wage_ubia_limited"

    def test_nonsstb_above_range_ubia_branch_wins(self):
        """Low wages, high UBIA: 25%×40k + 2.5%×2M = 10k+50k=60k > 50%×40k=20k."""
        r = compute_form_8995a(400000, 40000, 2000000, False, 600000, 383900, 100000)
        assert r["wage_ubia_limit"] == 60000.0

    def test_sstb_above_range_zero(self):
        r = compute_form_8995a(300000, 100000, 500000, True, 500000, 383900, 100000)
        assert r["qbi_component"] == 0.0
        assert r["regime"] == "sstb_fully_excluded"

    def test_nonsstb_within_range_phases_in_limit(self):
        """qbi 300k, wages 40k, ubia 0, TI 420k. excess 36,100/100k = 0.361.
        tentative 60k; limit max(20k,10k)=20k; reduction (60k−20k)×0.361=14,440;
        component 60k−14,440 = 45,560."""
        r = compute_form_8995a(300000, 40000, 0, False, 420000, 383900, 100000)
        assert r["qbi_component"] == 45560.0
        assert r["regime"] == "phase_in"

    def test_sstb_within_range_double_haircut(self):
        """SSTB same inputs: applicable% = 1−0.361 = 0.639. Reduce first:
        qbi 191,700, wages 25,560. tentative 38,340; limit max(12,780,6,390)=12,780;
        reduction (38,340−12,780)×0.361=9,227.16; component 29,112.84."""
        r = compute_form_8995a(300000, 40000, 0, True, 420000, 383900, 100000)
        assert r["qbi_component"] == 29112.84
        assert r["applicable_percentage"] == 0.639
        assert r["regime"] == "sstb_phase_in"

    def test_below_threshold_no_limit(self):
        r = compute_form_8995a(100000, 0, 0, False, 150000, 383900, 100000)
        assert r["qbi_component"] == 20000.0
        assert r["regime"] == "below_threshold"

    def test_overall_cap_binds(self):
        """Deduction ≤ 20% of (TI − net cap gain)."""
        r = compute_form_8995a(300000, 200000, 0, False, 80000, 60000, 50000,
                               net_capital_gain=0)
        assert r["overall_cap"] == 16000.0
        assert r["deduction"] == 16000.0
        assert r["income_limited"] is True

    def test_is_sstb_must_be_bool(self):
        with pytest.raises(ValueError, match="is_sstb"):
            compute_form_8995a(300000, 100000, 0, None, 500000, 383900, 100000)

    def test_nan_input_rejected(self):
        with pytest.raises(ValueError):
            compute_form_8995a(float("nan"), 100000, 0, False, 500000, 383900, 100000)

    def test_zero_range_rejected(self):
        with pytest.raises(ValueError):
            compute_form_8995a(300000, 100000, 0, False, 500000, 383900, 0)

    def test_negative_qbi_loss_floors_component_no_crash(self):
        """Self-review catch: a QBI LOSS over the threshold must not crash — the
        loss carries forward, so the component floors at 0 (not a hard error)."""
        r = compute_form_8995a(-50000, 100000, 0, False, 500000, 383900, 100000)
        assert r["qbi_component"] == 0.0

    # --- year-aware phase-in range (OBBBA §70105 expansion TY2026+) ---
    def test_phase_in_range_pre_2026(self):
        assert _qbi_phase_in_range(2025, True) == 100000.0
        assert _qbi_phase_in_range(2025, False) == 50000.0

    def test_phase_in_range_2026_expanded(self):
        assert _qbi_phase_in_range(2026, True) == 150000.0
        assert _qbi_phase_in_range(2026, False) == 75000.0

    def test_phase_in_range_override_wins(self):
        assert _qbi_phase_in_range(2025, True, override=120000) == 120000.0

    def test_phase_in_range_bad_override_rejected(self):
        with pytest.raises(ValueError):
            _qbi_phase_in_range(2025, True, override=-1)


class TestQBIDeductionForm8995AIntegration:
    """compute_qbi_deduction delegates to Form 8995-A when over threshold +
    wage/UBIA/SSTB inputs are supplied; keeps the simplified path + warning
    otherwise."""

    def test_over_threshold_with_wage_inputs_applies_8995a(self):
        r = compute_qbi_deduction(
            300000, 0, taxable_income_before_qbi=500000, net_capital_gain=0,
            threshold=383900, w2_wages=100000, ubia=500000, is_sstb=False,
            filing_status="MFJ")
        assert r["form_8995a_applied"] is True
        assert r["qbi_component"] == 50000.0
        assert "warning" not in r  # limit modeled → no unmodeled warning

    def test_over_threshold_without_wage_inputs_keeps_warning(self):
        r = compute_qbi_deduction(
            300000, 0, taxable_income_before_qbi=500000, net_capital_gain=0,
            threshold=383900)
        assert r.get("form_8995a_applied") is None
        assert "warning" in r
        assert r["over_threshold"] is True

    def test_sstb_excluded_not_resurrected_by_70105_minimum(self):
        """An SSTB above the phase-out is $0 and the §70105 active-trade minimum
        must NOT lift it (an SSTB above the range is not a qualified trade)."""
        r = compute_qbi_deduction(
            300000, 0, taxable_income_before_qbi=500000, net_capital_gain=0,
            threshold=383900, tax_year=2025, active_qualified_trade=True,
            qbi_minimum={"income_threshold": 1000, "amount": 400},
            w2_wages=100000, ubia=0, is_sstb=True, filing_status="MFJ")
        assert r["total_deduction"] == 0.0
        assert r["qbi_minimum_applied"] is False

    def test_below_threshold_uses_simplified(self):
        r = compute_qbi_deduction(
            100000, 0, taxable_income_before_qbi=150000, net_capital_gain=0,
            threshold=383900, w2_wages=0, ubia=0, is_sstb=False)
        assert r.get("form_8995a_applied") is None
        assert r["qbi_component"] == 20000.0


class TestForm8889HSA:
    """Form 8889 return-side HSA limit test (N3a). 2025 limits: self-only
    $4,300, family $8,550, catch-up $1,000. Hand-computed from §223(b)."""

    def test_full_year_no_excess(self):
        r = compute_form_8889("family", 12, 5000, 3000, 4300, 8550)
        assert r["allowed_limit"] == 8550.0
        assert r["hsa_deduction"] == 5000.0
        assert r["excess_contribution"] == 0.0

    def test_partial_year_full_contribution_catches_excess(self):
        """THE bias inversion: 6 months eligible, contributed the full $4,300
        self-only limit → $2,150 excess + $129 §4973 excise that a full-year
        assumption would MISS (understatement)."""
        r = compute_form_8889("self_only", 6, 4300, 0, 4300, 8550)
        assert r["allowed_limit"] == 2150.0
        assert r["excess_contribution"] == 2150.0
        assert r["excise_tax"] == 129.0

    def test_last_month_rule_full_limit_with_testing_period(self):
        r = compute_form_8889("self_only", 1, 4300, 0, 4300, 8550,
                              last_month_rule=True)
        assert r["allowed_limit"] == 4300.0
        assert r["excess_contribution"] == 0.0
        assert r["last_month_rule_applied"] is True
        assert any("TESTING PERIOD" in n for n in r["notes"])

    def test_catch_up_full_year(self):
        r = compute_form_8889("self_only", 12, 5300, 0, 4300, 8550, age_55_plus=True)
        assert r["allowed_limit"] == 5300.0

    def test_catch_up_prorated_partial_year(self):
        """(4,300 + 1,000) × 6/12 = 2,650; contributed 3,000 → 350 excess."""
        r = compute_form_8889("self_only", 6, 3000, 0, 4300, 8550, age_55_plus=True)
        assert r["allowed_limit"] == 2650.0
        assert r["excess_contribution"] == 350.0

    def test_family_split_between_spouses(self):
        r = compute_form_8889("family", 12, 4000, 0, 4300, 8550, family_limit_share=0.5)
        assert r["allowed_limit"] == 4275.0

    def test_family_no_split_caveat(self):
        r = compute_form_8889("family", 12, 4000, 0, 4300, 8550)
        assert any("SPLIT between them" in n for n in r["notes"])

    def test_employer_over_contribution_taxable(self):
        r = compute_form_8889("self_only", 12, 0, 5000, 4300, 8550)
        assert r["excess_contribution"] == 700.0
        assert r["excess_employer_taxable"] == 700.0

    def test_none_coverage_zero_limit(self):
        r = compute_form_8889("none", 0, 1000, 0, 4300, 8550)
        assert r["allowed_limit"] == 0.0
        assert r["excess_contribution"] == 1000.0

    def test_bad_coverage_type_rejected(self):
        with pytest.raises(ValueError, match="coverage_type"):
            compute_form_8889("bogus", 12, 0, 0, 4300, 8550)

    def test_bad_months_rejected(self):
        with pytest.raises(ValueError, match="eligible_months"):
            compute_form_8889("self_only", 13, 0, 0, 4300, 8550)

    def test_bool_month_rejected(self):
        with pytest.raises(ValueError):
            compute_form_8889("self_only", True, 0, 0, 4300, 8550)

    def test_nan_contribution_rejected(self):
        with pytest.raises(ValueError):
            compute_form_8889("self_only", 12, float("nan"), 0, 4300, 8550)


class TestForm2441DependentCare:
    """Form 2441 §21 dependent-care credit (N3b). Statutory-fixed: 35%→20%
    over $15k-$43k AGI, $3k/$6k caps, $250/$500 deemed income. Hand-computed."""

    def test_applicable_percentage_tiers(self):
        from agents.lib.tax_computations import _dcc_applicable_percentage as p
        assert p(15000) == 0.35
        assert p(20000) == 0.32   # ceil(5000/2000)=3 → 35−3
        assert p(43000) == 0.21   # ceil(28000/2000)=14 → 35−14
        assert p(43001) == 0.20   # floor
        assert p(200000) == 0.20

    def test_high_agi_two_persons_20pct(self):
        r = compute_form_2441(2, 10000, 150000, 200000, "MFJ",
                              spouse_earned_income=80000)
        assert r["credit"] == 1200.0  # 20% × $6,000 cap

    def test_one_person_mid_agi(self):
        """HoH, 1 person, $4,000 expenses, AGI $20,000 → 32% × $3,000 = $960."""
        r = compute_form_2441(1, 4000, 25000, 20000, "HoH")
        assert r["applicable_percentage"] == 0.32
        assert r["credit"] == 960.0

    def test_dcb_reduces_cap(self):
        """$5,000 excluded DCB reduces the $6,000 cap to $1,000 → 20% × 1,000."""
        r = compute_form_2441(2, 6000, 80000, 50000, "MFJ",
                              spouse_earned_income=80000, dependent_care_benefits=5000)
        assert r["expense_cap_after_dcb"] == 1000.0
        assert r["credit"] == 200.0

    def test_earned_income_limit(self):
        """Spouse earns only $2,000 → limit $2,000; 22% × 2,000 = $440."""
        r = compute_form_2441(2, 6000, 90000, 40000, "MFJ", spouse_earned_income=2000)
        assert r["earned_income_limit"] == 2000.0
        assert r["credit"] == 440.0

    def test_deemed_income_student_spouse(self):
        """Non-working full-time-student spouse, 12 months, 2 persons → deemed
        $500×12 = $6,000; 27% × $6,000 = $1,620."""
        r = compute_form_2441(2, 6000, 90000, 30000, "MFJ",
                              spouse_earned_income=0, spouse_student_disabled_months=12)
        assert r["earned_income_limit"] == 6000.0
        assert r["credit"] == 1620.0

    def test_mfs_disallowed(self):
        r = compute_form_2441(2, 6000, 50000, 50000, "MFS", spouse_earned_income=50000)
        assert r["credit"] == 0.0
        assert r["disallowed_mfs"] is True

    def test_mfs_lived_apart_exception_allowed(self):
        r = compute_form_2441(1, 3000, 40000, 30000, "MFS",
                              spouse_earned_income=40000, lived_apart_exception=True)
        assert r["disallowed_mfs"] is False
        assert r["credit"] == 810.0  # 27% × 3,000

    def test_nonrefundable_capped_at_tax(self):
        r = compute_form_2441(1, 3000, 50000, 20000, "HoH", tax_liability=500)
        assert r["credit"] == 500.0

    def test_no_qualifying_person_zero(self):
        r = compute_form_2441(0, 5000, 50000, 50000, "MFJ", spouse_earned_income=50000)
        assert r["credit"] == 0.0

    def test_both_spouses_students_deemed_to_spouse_only(self):
        r = compute_form_2441(2, 6000, 0, 30000, "MFJ", spouse_earned_income=0,
                              taxpayer_student_disabled_months=12,
                              spouse_student_disabled_months=12)
        # taxpayer EI 0 (not given deemed since both flagged → min with spouse
        # deemed 6000 is min(0, 6000) = 0) → credit 0, with a caveat note
        assert any("only ONE" in n for n in r["notes"])

    def test_bad_qualifying_persons_rejected(self):
        with pytest.raises(ValueError):
            compute_form_2441(-1, 5000, 50000, 50000, "MFJ", spouse_earned_income=50000)

    def test_bad_months_rejected(self):
        with pytest.raises(ValueError):
            compute_form_2441(1, 3000, 50000, 50000, "HoH",
                              taxpayer_student_disabled_months=13)


class TestSection988FXBond:
    """§988 foreign-currency bond disposition: ordinary exchange vs capital
    market split (Reg. §1.988-2(b)). Hand-computed."""

    def test_par_bond_pure_fx_gain_is_all_ordinary(self):
        # Bought & redeemed at the SAME foreign-currency amount (par, no market
        # move); FX rose 0.010 → 0.012. Entire gain is EXCHANGE (ordinary).
        #   exchange = 100000 × (0.012 − 0.010) = $200
        #   market   = (100000 − 100000) × 0.012 = $0
        r = compute_section_988(100000, 100000, 0.010, 0.012, long_term=True)
        assert r["exchange_gain_ordinary"] == 200.0
        assert r["market_gain_capital"] == 0.0
        assert r["total_gain"] == 200.0
        assert r["capital_term"] == "long"

    def test_market_gain_and_fx_loss_do_not_cancel(self):
        # The load-bearing case: FX FELL (ordinary loss) while the bond's FC
        # price ROSE (capital gain). They must NOT net — different lines/character.
        #   exchange = 100000 × (0.011 − 0.013) = −$200 (ordinary loss)
        #   market   = (110000 − 100000) × 0.011 = $110 (capital gain)
        #   total    = 110000×0.011 − 100000×0.013 = 1210 − 1300 = −$90
        r = compute_section_988(100000, 110000, 0.013, 0.011, long_term=False)
        assert r["exchange_gain_ordinary"] == -200.0
        assert r["market_gain_capital"] == 110.0
        assert r["total_gain"] == -90.0
        assert r["capital_term"] == "short"
        # reconciliation: the two pieces sum to the total
        assert round(r["exchange_gain_ordinary"] + r["market_gain_capital"], 2) == r["total_gain"]

    def test_reconciles_to_usd_basis_and_proceeds(self):
        r = compute_section_988(50000, 48000, 0.012, 0.014)
        assert r["basis_usd"] == round(50000 * 0.012, 2)
        assert r["proceeds_usd"] == round(48000 * 0.014, 2)
        assert r["total_gain"] == round(r["proceeds_usd"] - r["basis_usd"], 2)

    def test_rounded_components_always_sum_to_total(self):
        # Codex P2: messy inputs must not leave a 1-cent reconciliation drift —
        # total_gain is DERIVED from the two rounded reportables, not rounded
        # independently. This exact case drifted before the fix (−86.95 vs −86.94).
        r = compute_section_988(12345.67, 99, 0.0071, 0.0072)
        assert r["exchange_gain_ordinary"] + r["market_gain_capital"] == r["total_gain"]

    def test_exchange_component_flagged_us_source(self):
        # Codex P1: §988(a)(3) sources exchange gain by residence → US-source for
        # a US resident; the note must warn against Form 1116 foreign-source use.
        r = compute_section_988(1000, 1000, 1.0, 1.1)
        assert any("US-SOURCE" in n and "1116" in n for n in r["notes"])

    def test_term_unknown_when_long_term_none(self):
        r = compute_section_988(1000, 1000, 1.0, 1.0)
        assert r["capital_term"] is None
        assert any("term not specified" in n.lower() for n in r["notes"])

    def test_zero_or_negative_rate_rejected(self):
        with pytest.raises(ValueError):
            compute_section_988(1000, 1000, 0.0, 1.0)
        with pytest.raises(ValueError):
            compute_section_988(1000, 1000, 1.0, -1.0)

    def test_nan_and_bool_rejected(self):
        with pytest.raises(ValueError):
            compute_section_988(float("nan"), 1000, 1.0, 1.0)
        with pytest.raises(ValueError):
            compute_section_988(1000, 1000, 1.0, 1.0, long_term="yes")


# §6621 rates covering 2024-Q1 .. 2025-Q2 (contiguous) for the interest span.
_PFIC_RATES = [
    {"effective_from": "2024-01-01", "annual_rate": 0.08},
    {"effective_from": "2024-04-01", "annual_rate": 0.08},
    {"effective_from": "2024-07-01", "annual_rate": 0.08},
    {"effective_from": "2024-10-01", "annual_rate": 0.08},
    {"effective_from": "2025-01-01", "annual_rate": 0.08},
    {"effective_from": "2025-04-01", "annual_rate": 0.08},
]


class TestPFICExcessDistribution:
    """§1291(b) 125%-of-prior-3-year excess-distribution threshold."""

    def test_125_percent_of_three_year_average(self):
        r = compute_pfic_excess_distribution(10000, [2000, 3000, 1000])
        assert r["threshold"] == 2500.0          # 1.25 × 2000
        assert r["excess_distribution"] == 7500.0
        assert r["ordinary_now"] == 2500.0        # the non-excess portion

    def test_first_year_has_zero_excess(self):
        # §1291(b)(2)(B): first holding year → no excess distribution at all.
        r = compute_pfic_excess_distribution(10000, [], is_first_holding_year=True)
        assert r["excess_distribution"] == 0.0
        assert r["ordinary_now"] == 10000.0

    def test_shorter_prior_period_averages_available_years(self):
        # Held only 1 prior year → average over that one year, not three.
        r = compute_pfic_excess_distribution(5000, [1000])
        assert r["threshold"] == 1250.0
        assert r["excess_distribution"] == 3750.0

    def test_distribution_below_threshold_is_all_ordinary(self):
        r = compute_pfic_excess_distribution(2000, [2000, 2000, 2000])
        assert r["excess_distribution"] == 0.0
        assert r["ordinary_now"] == 2000.0

    def test_nan_rejected(self):
        with pytest.raises(ValueError):
            compute_pfic_excess_distribution(float("inf"), [1000])

    def test_zero_distribution_years_count_in_base(self):
        # Codex P1: a 3-year base with two $0 years must average over 3, not 1.
        # priors=[0,0,6000], preceding_holding_years=3 → avg 2000, threshold 2500.
        r = compute_pfic_excess_distribution(10000, [0, 0, 6000], preceding_holding_years=3)
        assert r["threshold"] == 2500.0
        assert r["complete"] is True

    def test_base_uses_most_recent_three_years_not_oldest(self):
        # Codex round 15 P1: §1291(b)(2)(A) base = the THREE tax years IMMEDIATELY
        # PRECEDING. prior_distributions is chronological oldest→newest, so an old
        # $8,000 distribution followed by four $0 years must NOT inflate the base.
        # Held 5 prior years: [8000, 0, 0, 0, 0] → most-recent-3 = [0,0,0] → avg 0,
        # threshold 0 → the entire distribution is excess.
        r = compute_pfic_excess_distribution(10000, [8000, 0, 0, 0, 0],
                                             preceding_holding_years=5)
        assert r["threshold"] == 0.0
        assert r["excess_distribution"] == 10000.0
        # The buggy head-slice would have averaged 8000/3 → threshold 3333.33.

    def test_absent_holding_year_count_is_incomplete(self):
        # Codex round 18 P1: without preceding_holding_years the list length can't
        # prove the zero-inclusive base — supplying only the nonzero $6,000 for a
        # 3-year holding would give a 1-year $7,500 threshold. Mark INCOMPLETE so
        # the tie-out keeps the §1291 block.
        r = compute_pfic_excess_distribution(10000, [6000])  # no count
        assert r["complete"] is False
        r2 = compute_pfic_excess_distribution(10000, [0, 0, 6000], preceding_holding_years=3)
        assert r2["complete"] is True  # count supplied → verifiable

    def test_incomplete_history_flags_not_complete(self):
        # Held 3 prior years but only 1 distribution amount supplied → incomplete.
        # Denominator is still 3 (shortfall treated as $0 → conservative), and the
        # result is flagged complete=False so the caller keeps the block.
        r = compute_pfic_excess_distribution(10000, [6000], preceding_holding_years=3)
        assert r["complete"] is False
        assert r["threshold"] == round(1.25 * 6000 / 3, 2)   # 2500, missing = $0
        assert any("incomplete" in n.lower() for n in r["notes"])

    def test_first_holding_year_flag_must_be_bool(self):
        # Codex P2: a truthy non-bool ("false"/1) must not silently zero the excess.
        with pytest.raises(ValueError):
            compute_pfic_excess_distribution(10000, [], is_first_holding_year="false")

    def test_zero_preceding_years_is_first_year(self):
        r = compute_pfic_excess_distribution(10000, [], preceding_holding_years=0)
        assert r["excess_distribution"] == 0.0 and r["ordinary_now"] == 10000.0

    def test_contradictory_first_year_and_multiyear_rejected(self):
        # Codex P1: is_first_holding_year=True against a 3-year holding must not
        # silently zero the excess.
        with pytest.raises(ValueError):
            compute_pfic_excess_distribution(10000, [0, 0, 6000],
                                             is_first_holding_year=True,
                                             preceding_holding_years=3)

    def test_first_year_with_nonzero_prior_history_rejected(self):
        # Codex round 23 P1: is_first_holding_year=True (or preceding=0) with a
        # NONZERO prior distribution is contradictory — a genuine first year has no
        # preceding distributions. Trusting the stale flag would silently zero the
        # excess for a multi-year fund. Both entry points (None count + flag, and
        # preceding=0) must reject.
        with pytest.raises(ValueError, match="first"):
            compute_pfic_excess_distribution(10000, [0, 0, 6000],
                                             is_first_holding_year=True)
        with pytest.raises(ValueError, match="first"):
            compute_pfic_excess_distribution(10000, [6000], preceding_holding_years=0)

    def test_first_year_with_all_zero_prior_history_ok(self):
        # A first year with an all-ZERO prior list (or empty) is consistent — no
        # excess, whole distribution ordinary.
        r = compute_pfic_excess_distribution(10000, [0, 0],
                                             is_first_holding_year=True)
        assert r["excess_distribution"] == 0.0 and r["ordinary_now"] == 10000.0
        assert r["complete"] is True


class TestPFIC1291:
    """§1291 allocation + deferred tax at each prior year's highest rate + §6621
    interest. Verified vs IRS Instructions for Form 8621 (Part V)."""

    def test_allocation_split_ordinary_vs_prior_pfic(self):
        # PFIC from acquisition, held 2022-01-02..2025-12-31 (holding period = day
        # AFTER acq = 1460 days), $40k excess. 2025 (current) → ordinary (365 days).
        r = compute_pfic_1291(40000, "2022-01-01", "2025-12-31", 2025,
                              {2022: 0.37, 2023: 0.37, 2024: 0.37}, underpayment_rates=None)
        assert r["ordinary_income_current"] == round(40000 * 365 / 1460, 2)  # 10000.0
        # 2022(364)+2023(365)+2024(366) deferred at 37%, per-year rounded sum
        assert r["deferred_tax_total"] == 11100.0
        assert r["complete"] is False  # no §6621 rates → interest incomplete

    def test_full_computation_with_rates_is_complete(self):
        # Held 2023-01-02..2024-12-31 (day after acq); 2023 is the one prior year.
        r = compute_pfic_1291(20000, "2023-01-01", "2024-12-31", 2024,
                              {2023: 0.37}, underpayment_rates=_PFIC_RATES)
        assert r["complete"] is True
        # 2023 allocated 364/730 of $20k (day-after-acq), taxed at 37%
        deferred = round(20000 * 364 / 730 * 0.37, 2)
        assert r["deferred_tax_total"] == deferred
        assert r["interest_total"] > 0            # a full year of §6621 interest
        assert r["total_1291_addon"] == round(r["deferred_tax_total"] + r["interest_total"], 2)
        # 2024 (current) portion is ordinary now (366 days)
        assert r["ordinary_income_current"] == round(20000 * 366 / 730, 2)

    def test_disposition_whole_gain_is_excess(self):
        r = compute_pfic_1291(30000, "2023-01-01", "2024-12-31", 2024,
                              {2023: 0.37}, underpayment_rates=_PFIC_RATES,
                              is_disposition=True)
        assert r["is_disposition"] is True
        assert any("entire gain" in n.lower() for n in r["notes"])

    def test_datetime_inputs_normalized(self):
        # Codex round 27 P2: datetime.datetime is a subclass of datetime.date, so a
        # datetime acquisition date must be normalized to a pure date — otherwise a
        # later `date(y, ...) < datetime` comparison raises TypeError and blocks the
        # documented date-input API. A datetime acq + date event must compute the
        # same as the ISO-string form.
        import datetime as _dt
        r_dt = compute_pfic_1291(20000, _dt.datetime(2023, 1, 1, 9, 30),
                                 _dt.date(2024, 12, 31), 2024,
                                 {2023: 0.37}, underpayment_rates=_PFIC_RATES)
        r_str = compute_pfic_1291(20000, "2023-01-01", "2024-12-31", 2024,
                                  {2023: 0.37}, underpayment_rates=_PFIC_RATES)
        assert r_dt["ordinary_income_current"] == r_str["ordinary_income_current"]
        assert r_dt["total_1291_addon"] == r_str["total_1291_addon"]

    def test_holding_period_excludes_acquisition_day(self):
        # Codex P2: the holding period starts the DAY AFTER acquisition (§1223).
        # Bought 2023-01-01, sold 2024-12-31 → 2023 has 364 allocated days (Jan 2–
        # Dec 31), 2024 has 366, total 730 — not 365/366/731.
        r = compute_pfic_1291(7300, "2023-01-01", "2024-12-31", 2024,
                              {2023: 0.50}, underpayment_rates=_PFIC_RATES)
        assert r["per_year"][0]["year"] == 2023 and r["per_year"][0]["days"] == 364
        assert r["per_year"][1]["year"] == 2024 and r["per_year"][1]["days"] == 366

    def test_mid_holding_pfic_transition_is_blocked(self):
        # Codex P1 (v1 scope): a fund that became a PFIC AFTER acquisition (a
        # mid-holding transition) is unsupported — fail closed rather than compute
        # the pre-PFIC-period case either direction.
        with pytest.raises(ValueError):
            compute_pfic_1291(40000, "2022-01-01", "2024-12-31", 2024,
                              {2023: 0.37}, underpayment_rates=_PFIC_RATES,
                              pfic_start_date="2023-07-01")

    def test_return_due_date_models_covid_postponements(self):
        # Codex P2: §1291 interest runs from the ACTUAL unextended due date. The
        # IRS-wide COVID postponements (TY2019→Jul 15 2020, TY2020→May 17 2021)
        # are modeled so interest doesn't start too early; other years use Apr 15.
        import datetime as _dt
        from agents.lib.tax_computations import _return_due_date
        assert _return_due_date(2019) == _dt.date(2020, 7, 15)
        assert _return_due_date(2020) == _dt.date(2021, 5, 17)
        # Apr 15 2023 = Sat; Apr 17 = DC Emancipation Day (observed) → Tue Apr 18.
        assert _return_due_date(2022) == _dt.date(2023, 4, 18)

    def test_pfic_from_acquisition_is_supported(self):
        # pfic_start_date == acquisition (the default / common case) computes fine.
        r = compute_pfic_1291(20000, "2023-01-01", "2024-12-31", 2024,
                              {2023: 0.37}, underpayment_rates=_PFIC_RATES,
                              pfic_start_date="2023-01-01")
        assert r["complete"] is True and r["deferred_tax_total"] > 0

    def test_zero_historical_rate_rejected(self):
        # Codex P2: a 0 rate is malformed (every year has a positive top rate) —
        # accepting it would zero the deferred tax while reading complete.
        with pytest.raises(ValueError):
            compute_pfic_1291(20000, "2023-01-01", "2024-12-31", 2024,
                              {2023: 0.0}, underpayment_rates=_PFIC_RATES)

    def test_is_disposition_must_be_bool(self):
        with pytest.raises(ValueError):
            compute_pfic_1291(20000, "2023-01-01", "2024-12-31", 2024,
                              {2023: 0.37}, underpayment_rates=_PFIC_RATES,
                              is_disposition="yes")

    def test_zero_amount_is_complete_without_rates(self):
        # Codex P2: a below-threshold (non-excess) distribution → amount=0 has no
        # deferred tax/interest, so missing historical rates must NOT mark it
        # incomplete (which would block a below-threshold ordinary distribution).
        r = compute_pfic_1291(0, "2020-01-01", "2024-12-31", 2024,
                              {}, underpayment_rates=None)
        assert r["complete"] is True
        assert r["total_1291_addon"] == 0.0
        assert r["deferred_tax_total"] == 0.0

    def test_negative_amount_rejected_loss_goes_to_schedule_d(self):
        # A PFIC disposition at a LOSS is a Schedule D capital loss, not a §1291
        # event — compute_pfic_1291 correctly rejects a negative amount so a loss
        # can't be run through the excess-distribution path.
        with pytest.raises(ValueError):
            compute_pfic_1291(-5000, "2023-01-01", "2024-12-31", 2024,
                              {2023: 0.37}, underpayment_rates=_PFIC_RATES,
                              is_disposition=True)

    def test_pfic_start_after_event_rejected(self):
        # Codex P2: a start date after the event would zero the §1291 tax (all
        # days pre-PFIC) — impossible for an attested taxable event → fail closed.
        with pytest.raises(ValueError):
            compute_pfic_1291(20000, "2023-01-01", "2024-12-31", 2024,
                              {2023: 0.37}, underpayment_rates=_PFIC_RATES,
                              pfic_start_date="2025-01-01")

    def test_missing_prior_year_rate_degrades_not_silent(self):
        # Rate for 2023 absent → that year's deferred tax not computed, complete False.
        r = compute_pfic_1291(20000, "2023-01-01", "2024-12-31", 2024,
                              {}, underpayment_rates=_PFIC_RATES)
        assert r["complete"] is False
        assert any("highest ordinary rate missing" in n.lower() for n in r["notes"])

    def test_event_year_must_equal_current_year(self):
        with pytest.raises(ValueError):
            compute_pfic_1291(1000, "2023-01-01", "2024-06-01", 2025, {2023: 0.37})

    def test_event_before_acquisition_rejected(self):
        with pytest.raises(ValueError):
            compute_pfic_1291(1000, "2024-06-01", "2024-01-01", 2024, {})

    def test_rate_above_one_rejected(self):
        with pytest.raises(ValueError):
            compute_pfic_1291(20000, "2023-01-01", "2024-12-31", 2024,
                              {2023: 37}, underpayment_rates=_PFIC_RATES)

    def test_nan_amount_rejected(self):
        with pytest.raises(ValueError):
            compute_pfic_1291(float("nan"), "2023-01-01", "2024-12-31", 2024, {2023: 0.37})


class TestComputeSalesTaxDeduction:
    """IRS Schedule A Optional State Sales Tax Tables method (line 5a input).

    deduction = table_amount + table_amount x local_rate/state_rate
              + actual sales tax on specified big-ticket items.
    Output feeds compute_schedule_a(state_local_sales_taxes=...) — the
    income-vs-sales election and SALT cap live THERE, not here.
    """

    def test_table_only(self):
        r = compute_sales_tax_deduction(1000.0, 0.06)
        assert r == {"table_amount": 1000.0, "local_addition": 0.0,
                     "big_ticket_tax": 0.0, "total": 1000.0}

    def test_local_proration_fl_county(self):
        # 1% county surtax on FL's 6% state rate: 1000 x (0.01/0.06) = 166.67
        r = compute_sales_tax_deduction(1000.0, 0.06, local_rate=0.01)
        assert r["local_addition"] == 166.67
        assert r["total"] == 1166.67

    def test_big_ticket_added(self):
        r = compute_sales_tax_deduction(1000.0, 0.06, local_rate=0.01,
                                        big_ticket_tax=450.0)
        assert r["total"] == 1616.67

    def test_zero_state_rate_rejected(self):
        with pytest.raises(ValueError):
            compute_sales_tax_deduction(1000.0, 0.0)

    def test_negative_rejected(self):
        for kwargs in ({"table_amount": -1.0, "state_rate": 0.06},
                       {"table_amount": 1000.0, "state_rate": 0.06, "local_rate": -0.01},
                       {"table_amount": 1000.0, "state_rate": 0.06, "big_ticket_tax": -5.0}):
            with pytest.raises(ValueError):
                compute_sales_tax_deduction(**kwargs)

    def test_bool_and_nonfinite_rejected(self):
        with pytest.raises(ValueError):
            compute_sales_tax_deduction(True, 0.06)
        for bad in (float("nan"), float("inf")):
            with pytest.raises(ValueError):
                compute_sales_tax_deduction(bad, 0.06)
            with pytest.raises(ValueError):
                compute_sales_tax_deduction(1000.0, 0.06, local_rate=bad)

    def test_state_rate_percentage_form_rejected(self):
        # A WebSearch-transcribed "6" (meaning 6%) instead of 0.06 must be
        # rejected outright — silently accepting it would compute at 100x
        # the real rate (worst-direction overstatement).
        with pytest.raises(ValueError, match="decimal fraction"):
            compute_sales_tax_deduction(1000.0, 1.0)
        with pytest.raises(ValueError, match="decimal fraction"):
            compute_sales_tax_deduction(1000.0, 6.0)

    def test_local_rate_percentage_form_rejected(self):
        # local_rate=1 (meaning "1%") would otherwise compute a 100x local
        # addition: 1000 * 1/0.06 = 16,666.67 instead of 1000 * 0.01/0.06.
        with pytest.raises(ValueError, match="decimal fraction"):
            compute_sales_tax_deduction(1000.0, 0.06, local_rate=1.0)

    def test_state_rate_just_under_one_accepted(self):
        # Boundary check: rates strictly between 0 and 1 remain valid.
        r = compute_sales_tax_deduction(1000.0, 0.99)
        assert r["table_amount"] == 1000.0

    def test_local_rate_zero_accepted(self):
        # Boundary check: local_rate=0 (no county surtax) remains valid.
        r = compute_sales_tax_deduction(1000.0, 0.06, local_rate=0.0)
        assert r["local_addition"] == 0.0

    def test_docstring_enumerates_qualifying_big_ticket_classes(self):
        # Codex round-2 P2: the docstring must name the IRS-qualifying
        # purchase classes (Schedule A instructions) so a caller doesn't
        # feed in "furniture/jewelry/electronics" sales tax, which is
        # already priced into table_amount and would double-count. Doc-only
        # pin — no behavior change.
        doc = compute_sales_tax_deduction.__doc__
        for term in ("motor vehicles", "boats", "aircraft",
                     "home", "renovation"):
            assert term in doc, f"{term!r} missing from docstring"
        assert "already priced into table_amount" in doc \
            or "double-count" in doc


class TestBigTicketQualifyingClassesDoc:
    """Codex round-2 P2: the optional-tables method only allows ADDING
    actual sales tax on specific IRS-enumerated purchase classes (motor
    vehicles, boats, aircraft, homes/substantial additions/major
    renovations' building materials) — anything else (furniture, jewelry,
    electronics) is already inside the table amount, so recording it as
    "big-ticket" would double-count. Both interview-facing surfaces
    (tax-setup.md, tax-compute.md) must enumerate the classes and warn
    against ordinary purchases, not just invite "other big-ticket item."""

    def _repo(self):
        return Path(__file__).resolve().parent.parent

    def test_setup_md_enumerates_classes_and_warns_against_ordinary_purchases(self):
        setup_md = skill_text("tax-setup")
        for term in ("motor vehicle", "boat", "aircraft", "renovation"):
            assert term in setup_md, f"{term!r} missing from tax-setup.md"
        # The old open-ended invitation must be gone.
        assert "or other big-ticket item" not in setup_md
        # And ordinary purchase classes must be explicitly excluded.
        assert "furniture" in setup_md and "jewelry" in setup_md
        assert "double-count" in setup_md

    def test_compute_md_references_same_enumerated_classes(self):
        compute_md = skill_text("tax-compute")
        for term in ("motor vehicle", "boat", "aircraft", "renovation"):
            assert term in compute_md, f"{term!r} missing from tax-compute.md"
        assert "furniture" in compute_md
        assert "double-count" in compute_md


class TestQdcgWorksheetApplies:
    """X2: one guard for "may the worksheet be skipped?", shared by
    `compute_scenario` and `projection.project_federal_tax`. Skipping is
    allowed only when there is nothing preferential to price."""

    def test_no_preferential_income_skips_the_worksheet_whatever_the_limits(self):
        assert qdcg_worksheet_applies(0.0, None, None) is False
        assert qdcg_worksheet_applies(0.0, 90000, 550000) is False

    def test_preferential_income_with_both_limits_applies(self):
        assert qdcg_worksheet_applies(1.0, 90000, 550000) is True

    @pytest.mark.parametrize("zero, fifteen", [(None, 550000), (90000, None), (None, None)])
    def test_preferential_income_with_a_missing_limit_refuses(self, zero, fifteen):
        with pytest.raises(ValueError, match="tax-setup"):
            qdcg_worksheet_applies(1.0, zero, fifteen)

    def test_a_net_capital_loss_is_not_preferential(self):
        """The worksheet prices gains; a loss is capped and deducted from
        ordinary income, so it must not trip the refusal."""
        assert qdcg_worksheet_applies(-3000.0, None, None) is False


def test_the_capital_gains_limits_are_read_at_one_site():
    """Four divergent read sites were the root cause X1 and X2 share; the
    accessor is the single one now, and this keeps it so. The schema
    (`tax_law_fetcher`) and the AMT function's own parameter names are the
    only other places the key may be spelled."""
    import re
    lib = Path(__file__).resolve().parent.parent / "agents" / "lib"
    reads = {}
    key = r"""["'](?:zero|fifteen)_pct_limit["']"""
    for f in sorted(lib.glob("*.py")):
        if f.name == "tax_law_fetcher.py":
            continue
        hits = re.findall(rf"""\.get\(\s*{key}|\[{key}\]""", f.read_text())
        if hits:
            reads[f.name] = len(hits)
    assert reads == {"projection.py": 2}, (
        f"a capital-gains breakpoint is read outside the accessor: {reads}")


def test_the_fbar_skill_states_the_form_8938_figures_the_engine_holds():
    """The skill's parenthetical is the fourth copy of the table and the one
    that drifted last; it is derived from `FORM_8938_THRESHOLDS` here the way
    tax-safe-harbor's figures are pinned to the engine's constants."""
    from agents.lib.tax_computations import FORM_8938_THRESHOLDS as T
    text = skill_text("tax-fbar")
    k = lambda n: f"${n // 1000}K"
    line = next(l for l in text.splitlines() if "form_8938_thresholds" in l and "MFJ in US" in l)
    assert f"MFJ in US = {k(T['mfj_us']['year_end'])} year-end / {k(T['mfj_us']['any_time'])} any time" in line
    assert f"unmarried abroad = {k(T['single_foreign']['year_end'])} / {k(T['single_foreign']['any_time'])}" in line


# --- Z1 slice 2: the §601 schedule and the benefit recapture ----------------
#
# Every expected value below is hand-computed from New York's OWN published
# worksheets and rate schedules — Form IT-201-I (2025), pages 33 through 38 —
# and each case names the worksheet it reproduces. The figures are TY2025
# because that is the instruction set that exists to check against; the module
# fetches whatever year it is run for, which is why the function takes them all
# as arguments and holds none.
#
# The recapture lives INSIDE compute_ny_tax on purpose. Above the entry
# threshold, plain bracket math under-states New York tax by the whole benefit
# of the graduated schedule, and under-stating what a taxpayer owes is the
# direction AGENTS.md §4 calls a hard error.

# IT-201-I (2025) p.33, "Married filing jointly and qualifying surviving
# spouse". Written as bands so the test reads against the published table.
_NY_2025_SCHEDULE_A = [
    {"rate": 0.04, "min": 0, "max": 17150, "base": 0},
    {"rate": 0.045, "min": 17150, "max": 23600, "base": 686},
    {"rate": 0.0525, "min": 23600, "max": 27900, "base": 976},
    {"rate": 0.055, "min": 27900, "max": 161550, "base": 1202},
    {"rate": 0.06, "min": 161550, "max": 323200, "base": 8553},
    {"rate": 0.0685, "min": 323200, "max": 2155350, "base": 18252},
    {"rate": 0.0965, "min": 2155350, "max": 5000000, "base": 143754},
    {"rate": 0.103, "min": 5000000, "max": 25000000, "base": 418263},
    {"rate": 0.109, "min": 25000000, "max": None, "base": 2478263},]
# IT-201-I (2025) p.33, "Single and married filing separately".
_NY_2025_SCHEDULE_C = [
    {"rate": 0.04, "min": 0, "max": 8500, "base": 0},
    {"rate": 0.045, "min": 8500, "max": 11700, "base": 340},
    {"rate": 0.0525, "min": 11700, "max": 13900, "base": 484},
    {"rate": 0.055, "min": 13900, "max": 80650, "base": 600},
    {"rate": 0.06, "min": 80650, "max": 215400, "base": 4271},
    {"rate": 0.0685, "min": 215400, "max": 1077550, "base": 12356},
    {"rate": 0.0965, "min": 1077550, "max": 5000000, "base": 71413},
    {"rate": 0.103, "min": 5000000, "max": 25000000, "base": 449929},
    {"rate": 0.109, "min": 25000000, "max": None, "base": 2509929},]
_NY_SCHEDULE_BY_STATUS = {"single": "c", "mfs": "c", "mfj": "a", "qss": "a",
                          "hoh": "b"}
# IT-201-I (2025) pp.34-38, tax computation worksheets 1-16.
_NY_2025_RECAPTURE = {
    "entry_threshold": 107650,
    "phase_in_band": 50000,
    "entry_tier": {
        # WS1: "Multiply line 2 by 5.5%", taxable income $161,550 or less.
        "a": {"flat_rate": 0.055, "taxable_income_not_over": 161550},
        # WS12: 6%, $269,300 or less.
        "b": {"flat_rate": 0.06, "taxable_income_not_over": 269300},
        # WS7: 6%, $215,400 or less.
        "c": {"flat_rate": 0.06, "taxable_income_not_over": 215400},
    },
    "tiers": {
        "a": [  # WS2-5
            {"taxable_income_over": 161550, "taxable_income_not_over": 323200,
             "recapture_base": 333, "incremental_benefit": 807,
             "nyagi_excess_over": 161550},
            {"taxable_income_over": 323200, "taxable_income_not_over": 2155350,
             "recapture_base": 1140, "incremental_benefit": 2747,
             "nyagi_excess_over": 323200},
            {"taxable_income_over": 2155350,
             "taxable_income_not_over": 5000000,
             "recapture_base": 3887, "incremental_benefit": 60350,
             "nyagi_excess_over": 2155350},
            {"taxable_income_over": 5000000, "taxable_income_not_over": None,
             "recapture_base": 64237, "incremental_benefit": 32500,
             "nyagi_excess_over": 5000000},
        ],
        "b": [  # WS13-15
            {"taxable_income_over": 269300, "taxable_income_not_over": 1616450,
             "recapture_base": 787, "incremental_benefit": 2289,
             "nyagi_excess_over": 269300},
            {"taxable_income_over": 1616450,
             "taxable_income_not_over": 5000000,
             "recapture_base": 3076, "incremental_benefit": 45261,
             "nyagi_excess_over": 1616450},
            {"taxable_income_over": 5000000, "taxable_income_not_over": None,
             "recapture_base": 48337, "incremental_benefit": 32500,
             "nyagi_excess_over": 5000000},
        ],
        "c": [  # WS8-10
            {"taxable_income_over": 215400, "taxable_income_not_over": 1077550,
             "recapture_base": 568, "incremental_benefit": 1831,
             "nyagi_excess_over": 215400},
            {"taxable_income_over": 1077550,
             "taxable_income_not_over": 5000000,
             "recapture_base": 2399, "incremental_benefit": 30172,
             "nyagi_excess_over": 1077550},
            {"taxable_income_over": 5000000, "taxable_income_not_over": None,
             "recapture_base": 32571, "incremental_benefit": 32500,
             "nyagi_excess_over": 5000000},
        ],
    },
    # WS6/WS11/WS16: above this NYAGI the whole taxable income is taxed at one
    # rate, with no schedule at all.
    "full_recapture": {"nyagi_over": 25000000, "rate": 0.109},
}
_NY_BRACKETS = {"a": _NY_2025_SCHEDULE_A, "c": _NY_2025_SCHEDULE_C, "b": []}
# IT-201-I (2025) p.32: the TABLE is used when NYAGI is at or below the entry
# threshold AND taxable income is under $65,000; the rate schedule takes over
# above it. The rows are $50 wide over most of the range and NARROWER at the
# very bottom — $0-13, $13-25, $25-50 — which is why the widths are fetched as
# segments rather than assumed to be one figure.
_NY_2025_TAX_TABLE = {
    "max_taxable_income": 65000,
    "row_segments": [
        {"over": 0, "not_over": 13, "row_width": 13},
        {"over": 13, "not_over": 25, "row_width": 12},
        {"over": 25, "not_over": 50, "row_width": 25},
        {"over": 50, "not_over": 65000, "row_width": 50},
    ],
}


def _ny(taxable_income, nyagi, status="mfj", **over):
    kwargs = {"brackets": _NY_BRACKETS,
              "schedule_by_status": _NY_SCHEDULE_BY_STATUS,
              "recapture": _NY_2025_RECAPTURE,
              "tax_table": _NY_2025_TAX_TABLE}
    kwargs.update(over)
    return compute_ny_tax(taxable_income, nyagi, status, **kwargs)


def test_ny_below_the_entry_threshold_is_the_schedule_alone():
    """IT-201-I (2025) p.33, MFJ: "$1,202 plus 5.5% of the excess over
    $27,900". 1,202 + 0.055 x (90,000 - 27,900) = 1,202 + 3,415.50.

    Taxable income $90,000 is ABOVE the $65,000 table cutoff, so the schedule
    is the right method here — which is the distinction the next test exists
    for."""
    out = _ny(90000, 100000)
    assert out["tax_exact"] == 4617.50
    assert out["tax"] == 4618   # line 39 carries whole dollars
    assert out["regime"] == "graduated"
    assert out["recapture_amount"] == 0.0
    assert out["schedule"] == "a"


def test_ny_entry_tier_phases_toward_the_flat_rate():
    """Worksheet 1. NYAGI 132,650 is halfway through the 50,000 band, so half
    the benefit is recaptured.

    schedule tax = 1,202 + 0.055 x (130,000 - 27,900)        = 6,817.50
    flat         = 0.055 x 130,000                           = 7,150.00
    benefit      = 7,150.00 - 6,817.50                       =   332.50
    fraction     = 25,000 / 50,000                           =    0.5
    tax          = 6,817.50 + 166.25                         = 6,983.75
    """
    out = _ny(130000, 132650)
    assert out["tax_exact"] == 6983.75
    assert out["tax"] == 6984
    assert out["regime"] == "entry_phase_in"
    assert out["recapture_amount"] == 166.25


def test_ny_entry_tier_fully_phased_in_is_exactly_the_flat_rate():
    """Worksheet 1's own stop rule: "If the line 1 amount is $157,650 or more,
    skip lines 4 through 8 and enter the line 3 amount". A capped fraction
    reaches the same place without a special case — 0.055 x 150,000."""
    out = _ny(150000, 160000)
    assert out["tax_exact"] == 8250.00 == round(150000 * 0.055, 2)


def test_ny_higher_tier_adds_a_base_and_a_phased_benefit():
    """Worksheet 3: Recapture Base 1,140, Incremental Benefit 2,747, excess
    over $323,200.

    schedule tax = 18,252 + 0.0685 x (340,000 - 323,200)     = 19,402.80
    fraction     = 26,800 / 50,000                           =    0.536
    recapture    = 1,140 + 2,747 x 0.536                     =  2,612.39
    tax                                                      = 22,015.19
    """
    out = _ny(340000, 350000)
    assert out["tax_exact"] == 22015.192
    assert out["tax"] == 22015
    assert out["regime"] == "tier"
    assert out["recapture_amount"] == 2612.392


def test_ny_above_the_top_nyagi_is_one_flat_rate_with_no_schedule():
    """Worksheet 6: "Multiply line 1 by 10.9%". Not a recapture — a different
    tax, which is why it needs its own key rather than a fourth tier."""
    out = _ny(26000000, 30000000)
    assert out["tax_exact"] == round(26000000 * 0.109, 4)
    assert out["regime"] == "full_recapture"


def test_ny_mfs_uses_the_single_schedule_not_the_married_one():
    """§601(c) covers the single filer AND married-filing-separately. The
    pairing looks wrong — MFS is married — and it is the one most often guessed
    wrong, so it is checked against the SINGLE table's own figures:
    12,356 + 0.0685 x (250,000 - 215,400) = 14,726.10, then WS8's base 568 and
    benefit 1,831 at a fraction of (260,000 - 215,400) / 50,000 = 0.892."""
    out = _ny(250000, 260000, status="mfs")
    assert out["schedule"] == "c"
    assert out["tax_exact"] == round(14726.10 + 568 + 1831 * 0.892, 4)


def test_ny_qss_uses_the_married_joint_schedule():
    """§601(a) covers the surviving spouse. The playbook says not to map QSS by
    reflex — this checks the mapping is the STATUTE's, not a reflex."""
    assert _ny(90000, 100000, status="qss")["schedule"] == "a"
    assert _ny(90000, 100000, status="qss")["tax"] == _ny(90000, 100000)["tax"]


def test_ny_the_tier_boundary_is_inclusive_at_the_top_of_the_entry_tier():
    """Worksheet 1 applies when taxable income is "$161,550 or less"; worksheet
    2 when it is "more than $161,550". One dollar apart, two regimes."""
    assert _ny(161550, 200000)["regime"] == "entry_phase_in"
    assert _ny(161551, 200000)["regime"] == "tier"


def test_ny_plain_bracket_math_under_states_above_the_threshold():
    """The reason the recapture is inside this function and not a caller's
    option. If these two were ever equal, the whole regime would be a no-op and
    every test above would pass against a function that ignored it."""
    for taxable, nyagi in ((130000, 132650), (340000, 350000),
                           (3000000, 3100000)):
        schedule_only, _ = compute_bracket_tax(taxable, _NY_2025_SCHEDULE_A)
        assert _ny(taxable, nyagi)["tax_exact"] > schedule_only, (
            f"the recapture added nothing at {taxable:,} / {nyagi:,}")


def test_ny_refuses_a_return_above_the_threshold_with_no_recapture_fetched():
    """The bias-direction guard, and the only safe answer. Every alternative —
    falling back to the schedule, to the entry tier, to zero — under-states."""
    for missing in ("entry_threshold", "phase_in_band", "entry_tier", "tiers",
                    "full_recapture"):
        recapture = {k: v for k, v in _NY_2025_RECAPTURE.items()
                     if k != missing}
        with pytest.raises(ValueError, match="not fetched|is empty"):
            _ny(340000, 350000, recapture=recapture)


def test_ny_refuses_when_the_brackets_and_the_flat_rate_disagree():
    """A negative benefit means the schedule and the entry tier's flat rate
    were fetched from different years or different states. Applying it would
    REDUCE the tax, so it refuses instead of clamping — a clamp would hide the
    mis-fetch and quietly compute on it."""
    broken = {**_NY_2025_RECAPTURE,
              "entry_tier": {**_NY_2025_RECAPTURE["entry_tier"],
                             "a": {"flat_rate": 0.01,
                                   "taxable_income_not_over": 161550}}}
    with pytest.raises(ValueError, match="disagree"):
        _ny(130000, 132650, recapture=broken)


def test_ny_refuses_taxable_income_above_new_york_agi():
    """Line 38 is computed FROM line 33, so this cannot happen — unless a
    FEDERAL AGI was handed in where New York's belongs, which would then be
    measured against every New York threshold."""
    with pytest.raises(ValueError, match="mis-sourced"):
        _ny(200000, 150000)


def test_ny_refuses_an_unmapped_status_and_an_unfetched_schedule():
    with pytest.raises(ValueError, match="§601 schedule"):
        _ny(90000, 100000, status="qualifying_widow")
    with pytest.raises(ValueError, match="empty"):
        _ny(90000, 100000, status="hoh")   # schedule "b" is [] in the fixture


@pytest.mark.parametrize("taxable,nyagi", [
    (-1, 100000), (float("nan"), 100000),
    (100000, float("inf")), (True, 100000),
])
def test_ny_refuses_a_malformed_figure(taxable, nyagi):
    """NaN is rejected explicitly: it passes isinstance and every comparison
    with it is False, so it would sail through both threshold tests and land in
    the graduated branch."""
    with pytest.raises(ValueError):
        _ny(taxable, nyagi)


def test_ny_refuses_a_taxable_income_no_tier_covers():
    """A tier table whose last entry is CLOSED — a transcription that dropped
    the "and over" row — would otherwise fall out of the loop and return the
    schedule alone, which is the unbounded under-statement compute_bracket_tax
    already refuses for brackets."""
    closed = {**_NY_2025_RECAPTURE,
              "tiers": {**_NY_2025_RECAPTURE["tiers"],
                        "a": [{"taxable_income_over": 161550,
                               "taxable_income_not_over": 323200,
                               "recapture_base": 333,
                               "incremental_benefit": 807,
                               "nyagi_excess_over": 161550}]}}
    with pytest.raises(ValueError, match="no fetched recapture tier"):
        _ny(400000, 420000, recapture=closed)


def test_ny_refuses_a_bracket_band_with_no_published_base():
    """New York publishes its schedule as "base plus rate on the excess" and
    ROUNDS each base to the nearest dollar, so summing the bands disagrees with
    the form: on the TY2025 married-joint schedule the $323,200 base sums to
    18,251.75 against a published 18,252. `/tax-verify` ties line by line
    against software that uses the published schedule, so the drift reads as a
    defect in whichever side is checked second. Refusing beats falling back to
    summation, which would be a second answer that differs quietly."""
    stripped = [{k: v for k, v in band.items() if k != "base"}
                for band in _NY_2025_SCHEDULE_A]
    with pytest.raises(ValueError, match="published cumulative base|'base'"):
        _ny(90000, 100000,
            brackets={**_NY_BRACKETS, "a": stripped})


def test_ny_uses_the_published_base_not_the_sum_of_the_bands():
    """The finding, pinned. If these ever agreed the requirement above would be
    ceremony; they do not, and the difference is systematic rather than a
    rounding accident of one case."""
    summed, _ = compute_bracket_tax(340000, [
        {k: v for k, v in band.items() if k != "base"}
        for band in _NY_2025_SCHEDULE_A])
    published = 18252 + 0.0685 * (340000 - 323200)
    assert round(summed, 2) != round(published, 2), (
        "band summation and the published base now agree — if New York has "
        "stopped rounding its bases, this requirement can be relaxed")
    out = _ny(340000, 350000)
    assert out["tax_exact"] == round(round(published, 2) + 2612.392, 4)


def test_ny_the_phase_in_fraction_is_rounded_to_four_places():
    """"Divide line 6 by $50,000 and round the result to the fourth decimal
    place" is the worksheet's own instruction, not a convenience. A mutation
    to two places survived every other case here, because they all happened to
    land on fractions that are exact at two — 0.5, 1.0, 0.536, 0.892.

    NYAGI 120,987 is 13,337 into the band: 0.26674, which the form rounds to
    0.2667 and two places would make 0.27.

    schedule tax = 1,202 + 0.055 x (120,000 - 27,900)        = 6,267.50
    flat         = 0.055 x 120,000                           = 6,600.00
    benefit                                                  =   332.50
    tax          = 6,267.50 + round(332.50 x 0.2667, 2)      = 6,356.18
    """
    out = _ny(120000, 120987)
    assert out["recapture_amount"] == round(332.50 * 0.2667, 4)
    assert out["tax_exact"] == round(6267.50 + 332.50 * 0.2667, 4)
    assert out["tax"] == 6356


def test_ny_a_higher_tiers_fraction_is_capped_at_the_band():
    """"Enter the LESSER of line 6 or $50,000". Every other tier case here sits
    inside the band, so an uncapped fraction survived them — and uncapped it
    would recapture more than the whole incremental benefit, over-stating the
    tax without limit as NYAGI rises.

    NYAGI 400,000 is 76,800 over $323,200, so the fraction is 1.0, not 1.536.
    tax = 19,402.80 + 1,140 + 2,747 = 23,289.80
    """
    out = _ny(340000, 400000)
    assert out["recapture_amount"] == 1140 + 2747
    assert out["tax_exact"] == round(18252 + 0.0685 * 16800 + 1140 + 2747, 4)


def test_ny_a_negative_new_york_agi_is_an_ordinary_return():
    """IT-201 line 33 is federal AGI plus and minus the IT-225 modifications and
    has NO floor: a Schedule C loss, a K-1 loss or an NOL carryforward makes it
    negative routinely. Line 38 IS floored — "if line 34 is more than line 33,
    leave blank" — so the taxable income is zero and the tax is zero.

    The first version refused this, which is a gate firing on correct data, and
    it would have blocked the whole return rather than saying "you owe nothing".
    """
    out = _ny(0, -5000)
    assert out["tax"] == 0.0
    assert out["regime"] in ("graduated", "tax_table")


def test_ny_uses_the_table_below_the_cutoff_and_the_schedule_above_it():
    """IT-201-I (2025) p.32 selects the METHOD on BOTH figures, and the
    taxable-income condition is not implied by the NYAGI one — which is why it
    cannot be folded into one "at or below the threshold" branch.

    The published $50,000-$50,050 row reads $2,419 for married-joint, which is
    the schedule at the row's MIDPOINT ($50,025 -> 2,418.875). The row FLOOR
    gives 2,417.50, a dollar low, and the schedule at the taxpayer's own
    taxable income is wrong in EITHER direction depending on where in the row
    they sit — over-stating for anyone in the upper half, which is half of all
    returns.
    """
    assert _ny(50000, 80000)["tax"] == 2419
    assert _ny(50000, 80000)["regime"] == "tax_table"
    assert _ny(50049, 80000)["tax"] == 2419, (
        "everyone in one row pays the same tax — that is what a table is")
    # The published single/MFS and head-of-household figures for the same row.
    assert _ny(50000, 80000, status="single")["tax"] == 2587
    # One dollar above the cutoff the method changes.
    assert _ny(65000, 80000)["regime"] == "graduated"


def test_ny_refuses_when_the_table_region_is_unfetched():
    """Falling back to the schedule is the silent two-dollar error this key
    exists to prevent, so an unfetched table refuses rather than guessing."""
    with pytest.raises(ValueError, match="max_taxable_income"):
        _ny(50000, 80000, tax_table=None)
    with pytest.raises(ValueError, match="row_segments"):
        _ny(50000, 80000, tax_table={"max_taxable_income": 65000})


def test_ny_table_rows_are_not_all_the_same_width():
    """The bottom of the table uses narrower rows than the $50 it settles into,
    so a single assumed width would be a guess at exactly the figure this
    method exists to get right. $30 sits in a 25-wide row whose midpoint is
    $37.50, not in a 50-wide row."""
    out = _ny(30, 1000)
    assert out["breakdown"][0]["row"] == "25 - 50"
    assert out["tax"] == round(37.50 * 0.04)


def test_ny_rounds_half_up_the_way_the_form_says():
    """"Increase amounts from 50 to 99 cents to the next dollar" (IT-201-I,
    Entering whole dollar amounts). Python's round() is HALF-EVEN and turns
    2,416.50 into 2,416, which is a dollar low every time the cents tie."""
    from agents.lib.tax_computations import _ny_round_half_up
    assert _ny_round_half_up(2416.50) == 2417
    assert _ny_round_half_up(2417.50) == 2418
    assert round(2416.50) == 2416, (
        "if Python's round has stopped being half-even, this helper can go")


def test_ny_refuses_a_schedule_whose_bands_do_not_meet():
    """A gap swallows the incomes inside it and an overlap makes the first
    match win. Neither is visible to a per-band check, and both are ordinary
    transcription errors."""
    gapped = [dict(b) for b in _NY_2025_SCHEDULE_A]
    gapped[3]["min"] = 28000          # the band below still ends at 27,900
    with pytest.raises(ValueError, match="gap or an overlap"):
        _ny(90000, 100000, brackets={**_NY_BRACKETS, "a": gapped})


def test_ny_refuses_a_mis_transcribed_published_base():
    """A MISSING base refuses already. A WRONG one — 18,522 for 18,252 — is the
    likelier transcription error and would shift every affected return by
    hundreds. The argument for requiring the published figure is that it differs
    from the sum by CENTS, so a dollar of difference refutes the figure."""
    transposed = [dict(b) for b in _NY_2025_SCHEDULE_A]
    transposed[5]["base"] = 18522
    with pytest.raises(ValueError, match="mis-transcribed"):
        _ny(340000, 350000, brackets={**_NY_BRACKETS, "a": transposed})


def test_ny_refuses_a_band_that_is_open_ended_in_the_middle():
    """A dropped `max` makes its band terminal and every band above it
    unreachable — a $400,000 return taxed at the 5.5% rate all the way up, from
    one missing key."""
    truncated = [dict(b) for b in _NY_2025_SCHEDULE_A]
    truncated[3]["max"] = None
    with pytest.raises(ValueError, match="not the last band"):
        _ny(400000, 420000, brackets={**_NY_BRACKETS, "a": truncated})


def test_ny_does_not_round_the_recapture_before_the_total():
    """The form rounds the FRACTION and nothing else. Rounding the product to
    cents first can carry a value across a whole-dollar boundary the form would
    not cross.

    MFJ, taxable income $323,201, NYAGI $323,588 (worksheet 3):
      line 3 = 18,252 + 0.0685 x 1                     = 18,252.07
      line 8 = 388 / 50,000 -> 0.0078
      line 9 = 2,747 x 0.0078                          =     21.4266
      line 10 = 18,252.07 + 1,140 + 21.4266            = 19,413.4966
    which the form enters as $19,413. Rounding line 9 to cents first gives
    19,413.50, which enters as $19,414.
    """
    out = _ny(323201, 323588)
    assert out["tax_exact"] == round(
        round(18252 + 0.0685 * 1, 2) + 1140 + 2747 * 0.0078, 4)
    assert out["tax"] == 19413, (
        "rounding the recapture to cents first would make this 19,414")


# --- Z1 slice 3: New York AGI and the IT-225 modifications ------------------
#
# Line references and rules are Form IT-201-I (2025) pp.7-10 and Form IT-225-I
# (2025) pp.1-2. The figures a taxpayer's return would carry are the CALLER's;
# the two the engine holds — the exclusion cap and the 529 cap — come from the
# fetched block, which is why the tests hand them in.

_NY_2025_SUBTRACTION_LIMITS = {
    "pension_annuity_exclusion": {"amount": 20000, "minimum_age": 59.5},
    # "$5,000 ($10,000 for married taxpayers filing a joint return)"
    "college_savings_deduction": {"single": 5000, "mfj": 10000},
}
# IT-225-I (2025) p.1-2: "Form IT-201 filers do not enter A-103, A-104, or
# A-113 … EA-113 … S-103, S-104, S-106, S-107, or S-125 … ES-106, ES-107, or
# ES-125".
_NY_IT201_INVALID_CODES = ["A-103", "A-104", "A-113", "EA-113",
                           "S-103", "S-104", "S-106", "S-107", "S-125",
                           "ES-106", "ES-107", "ES-125"]


def _agi(federal=100000.0, additions=None, subtractions=None,
         status="single", limits=None, invalid=None):
    return compute_ny_agi(
        federal, additions or {}, subtractions or {}, status,
        _NY_2025_SUBTRACTION_LIMITS if limits is None else limits,
        _NY_IT201_INVALID_CODES if invalid is None else invalid)


def test_ny_agi_is_federal_agi_plus_and_minus_new_yorks_own():
    """NYAGI is not federal AGI, and the two are not interchangeable: every New
    York threshold downstream — the recapture's entry, the itemized reductions,
    the safe harbour — is measured against THIS figure."""
    out = _agi(100000,
               {"state_local_bond_interest": 1500,
                "public_employee_414h": 3000},
               {"taxable_social_security": 5000,
                "us_government_bond_interest": 800})
    assert out["total_additions"] == 4500
    assert out["total_subtractions"] == 5800
    assert out["nyagi"] == 98700
    assert out["lines"]["line_24_total_additions"] == 104500
    assert out["lines"]["line_33_nyagi"] == 98700


def test_ny_agi_may_be_negative():
    """Line 33 has NO floor — a Schedule C loss, a K-1 loss or an NOL
    carryforward reaches it routinely — while each modification is floored,
    because New York enters both directions as positive figures."""
    assert _agi(-40000)["nyagi"] == -40000


def test_ny_modifications_refuse_the_wrong_schedule():
    """Schedule A carries additions and Schedule B subtractions, and the code
    prefix says which. A subtraction filed as an addition OVER-states the tax;
    an addition filed as a subtraction UNDER-states it. The caller states the
    direction and the prefix cross-checks the claim."""
    with pytest.raises(ValueError, match="prefix is not one of"):
        _agi(additions={"it225": [{"code": "S-201", "amount": 100}]})
    with pytest.raises(ValueError, match="prefix is not one of"):
        _agi(subtractions={"it225": [{"code": "A-201", "amount": 100}]})


def test_ny_modifications_accept_the_pass_through_prefixes():
    """`EA-`/`ES-` are the same modifications arriving through a partnership,
    S corporation or estate — Part 2 of each schedule. Rejecting them would
    refuse an ordinary K-1 return."""
    out = _agi(additions={"it225": [{"code": "EA-201", "amount": 250}]},
               subtractions={"it225": [{"code": "ES-201", "amount": 100}]})
    assert out["total_additions"] == 250
    assert out["total_subtractions"] == 100


def test_ny_modifications_refuse_a_negative_amount():
    """A negative addition is a subtraction wearing the wrong prefix. Flipping
    it silently is the direction confusion the schedules exist to prevent."""
    with pytest.raises(ValueError, match="SCHEDULE carries the direction"):
        _agi(additions={"it225": [{"code": "A-201", "amount": -100}]})


def test_ny_modifications_refuse_a_repeated_code():
    """The same NUMBER may legitimately appear as both an individual (Part 1)
    and a pass-through (Part 2) modification — but those carry DIFFERENT
    prefixes, so a repeated full code is a double count."""
    with pytest.raises(ValueError, match="appears twice"):
        _agi(additions={"it225": [{"code": "A-201", "amount": 100},
                                  {"code": "A-201", "amount": 100}]})
    # The legitimate pair still passes.
    assert _agi(additions={"it225": [{"code": "A-201", "amount": 100},
                                     {"code": "EA-201", "amount": 100}]}
                )["total_additions"] == 200


def test_ny_modifications_refuse_a_code_this_form_may_not_carry():
    """New York DISALLOWS the modification rather than adjusting it, so this is
    a return that would be corrected, not rounded."""
    with pytest.raises(ValueError, match="not valid on the form"):
        _agi(subtractions={"it225": [{"code": "S-125", "amount": 500}]})


def test_ny_modifications_say_loudly_when_they_could_not_be_checked():
    """An absent cross-check may not become a silent pass — but it may not
    block a correct return either, so it totals and says so."""
    out = _agi(additions={"it225": [{"code": "A-201", "amount": 100}]},
               invalid=[])
    assert out["total_additions"] == 100
    assert any("NOT checked" in n for n in out["notes"])


def test_ny_pension_exclusion_is_per_taxpayer_and_does_not_pool():
    """"You and your spouse can each subtract up to $20,000 of your OWN pension
    and annuity income. However, neither of you can claim any unused part of
    your spouse's exclusion." A household-level cap would let one spouse's
    unused allowance shelter the other's income."""
    out = _agi(subtractions={"pension_annuity": [
        {"taxpayer": "primary", "qualifying_amount": 45000, "entitled": True},
        {"taxpayer": "spouse", "qualifying_amount": 3000, "entitled": True},
    ]}, status="mfj")
    # 20,000 capped + 3,000 uncapped — NOT 40,000 - 3,000 of shared headroom.
    assert out["lines"]["line_29_pension_annuity_exclusion"] == 23000


def test_ny_pension_exclusion_refuses_to_guess_entitlement():
    """The age gate is "59½ before the start of the year", a taxpayer who
    turned 59½ DURING it counts only what came after, and a beneficiary
    qualifies on the decedent's entitlement regardless of their own age. Those
    are judgements about facts this function is not given, and granting the
    exclusion unearned UNDER-states the tax."""
    with pytest.raises(ValueError, match="entitled"):
        _agi(subtractions={"pension_annuity": [
            {"taxpayer": "primary", "qualifying_amount": 10000}]})


def test_ny_pension_exclusion_refuses_a_government_pension():
    """Those are fully excluded on their own line. Capping one here would
    silently limit a full exclusion — an over-statement of tax that looks like
    arithmetic."""
    with pytest.raises(ValueError, match="GOVERNMENT pension"):
        _agi(subtractions={"pension_annuity": [
            {"taxpayer": "primary", "qualifying_amount": 50000,
             "entitled": True, "government_pension": True}]})


def test_ny_pension_exclusion_refuses_two_entries_for_one_taxpayer():
    """Separate entries would each get the full cap, which is the pooling the
    form forbids arriving by a different route."""
    with pytest.raises(ValueError, match="two pension exclusion entries"):
        _agi(subtractions={"pension_annuity": [
            {"taxpayer": "primary", "qualifying_amount": 15000,
             "entitled": True},
            {"taxpayer": "primary", "qualifying_amount": 15000,
             "entitled": True}]})


def test_ny_an_unclaimed_pension_exclusion_is_loud():
    """Not claiming it OVER-states the tax, which the bias rule says degrades
    with a banner rather than hard-erroring — but a silent zero would leave
    nobody able to tell "no pension income" from "nobody asked"."""
    out = _agi()
    assert out["lines"]["line_29_pension_annuity_exclusion"] == 0.0
    assert any("OVER-STATES" in n for n in out["notes"])


def test_ny_529_cap_is_married_joint_against_everybody_else():
    """New York's own grouping, and the same two-key shape the federal
    child-credit phase-out uses — which is why the cache doc warns the keying
    is not uniform. Reading it with four status keys finds three absent."""
    assert _agi(subtractions={"college_savings_contributions": 9000},
                status="mfj")["lines"]["line_30_college_savings"] == 9000
    for status in ("single", "hoh", "mfs", "qss"):
        out = _agi(subtractions={"college_savings_contributions": 9000},
                   status=status)
        assert out["lines"]["line_30_college_savings"] == 5000, status
        assert any("exceed the deduction cap" in n for n in out["notes"])


def test_ny_refuses_a_capped_subtraction_whose_cap_is_unfetched():
    """Claiming a capped deduction uncapped UNDER-states the tax, so both caps
    refuse rather than defaulting."""
    with pytest.raises(ValueError, match="college_savings_deduction"):
        _agi(subtractions={"college_savings_contributions": 9000},
             limits={"pension_annuity_exclusion": {"amount": 20000}})
    with pytest.raises(ValueError, match="pension_annuity_exclusion"):
        _agi(subtractions={"pension_annuity": [
            {"taxpayer": "primary", "qualifying_amount": 10000,
             "entitled": True}]}, limits={})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "1000"])
def test_ny_agi_refuses_a_malformed_modification(bad):
    """NaN passes isinstance and defeats every comparison, so an unchecked one
    walks past the cap and into a total that then reads as a number."""
    with pytest.raises(ValueError):
        _agi(additions={"state_local_bond_interest": bad})


def test_ny_agi_reports_every_line_for_the_verify_tie_out():
    """`/tax-verify` ties line by line against a software-prepared IT-201, so
    the intermediate lines are part of the contract, not debug output."""
    out = _agi(subtractions={"government_pensions": 12000})
    for key in ("line_19_federal_agi", "line_24_total_additions",
                "line_26_government_pensions", "line_32_total_subtractions",
                "line_33_nyagi"):
        assert key in out["lines"], key


def test_ny_an_absent_or_null_line_is_zero():
    """Most returns carry none of these lines, so absent means zero — and a
    present null means the same, which is the rule `_flag_value` already sets
    for the capability flags ("Absent/None -> False"). The figure that must NOT
    default is a CAP, and both of those refuse instead."""
    assert _agi(100000)["nyagi"] == 100000
    assert _agi(100000, {"state_local_bond_interest": None})["nyagi"] == 100000


# --- Z1 slice 4: the §615 adjustment and the itemized election --------------
#
# Form IT-196-I (2025) pp.20-21, lines 46 through 49. The four regimes are
# mutually exclusive by New York AGI, and the top two do not reduce the
# deduction at all — they REPLACE it with a share of the charitable
# contributions.

_NY_2025_ITEMIZED_REDUCTION = {
    # IT-196-I Worksheet 3 line 2: "Filing status 1 or 3 enter $100,000 or
    # filing status 4 enter $150,000, or filing status 2 or 5 enter $200,000".
    "first_threshold": {"single": 100000, "mfs": 100000, "hoh": 150000,
                        "mfj": 200000, "qss": 200000},
    "first_band": 50000,
    "first_rate": 0.25,
    "second_threshold": 475000,
    "second_band": 50000,
    "second_rate": 0.25,
    "flat_threshold": 525000,
    "flat_rate": 0.50,
    "charitable_only_threshold": 1000000,
}
# IT-196-I line 47: "more than $1,000,000, but not more than $10,000,000,
# enter 50% of line 19 … more than $10,000,000, enter 25% of line 19".
_NY_2025_CHARITABLE_LIMIT = {"tiers": [
    {"nyagi_over": 1000000, "share": 0.50},
    {"nyagi_over": 10000000, "share": 0.25},
]}


def _item(line_45=40000.0, charitable=10000.0, nyagi=90000.0, status="single",
          reduction=None, limit=None, **kw):
    return compute_ny_itemized(
        line_45, charitable, nyagi, status,
        _NY_2025_ITEMIZED_REDUCTION if reduction is None else reduction,
        _NY_2025_CHARITABLE_LIMIT if limit is None else limit, **kw)


def test_ny_itemized_is_unreduced_at_or_below_the_first_threshold():
    """IT-196-I line 46: "$100,000 or less, leave line 46 blank"."""
    out = _item(nyagi=100000)
    assert out["line_46_adjustment"] == 0.0
    assert out["line_47_after_adjustment"] == 40000
    assert out["regime"] == "unreduced"


def test_ny_itemized_first_reduction_phases_a_quarter_in():
    """Worksheet 3, single, NYAGI $125,000, line 45 $40,000:
       line 3 = 125,000 - 100,000                    = 25,000
       line 4 = lesser of line 3 or 50,000           = 25,000
       line 5 = 25,000 / 50,000                      =    0.5
       line 6 = 25% of line 45                       = 10,000
       line 7 = line 5 x line 6                      =  5,000
    """
    out = _item(nyagi=125000)
    assert out["line_46_adjustment"] == 5000
    assert out["line_47_after_adjustment"] == 35000
    assert out["regime"] == "first_reduction"


def test_ny_itemized_first_threshold_is_per_status():
    """Worksheet 3 line 2 has three figures. A head-of-household filer at
    $125,000 is over the $100,000 gate that sends them to the worksheet, and
    then UNDER their own $150,000 threshold — "if line 2 is more than line 1,
    leave line 46 blank". Using single's figure would reduce their deduction
    by a quarter of the phase-in for no reason."""
    assert _item(nyagi=125000, status="hoh")["line_46_adjustment"] == 0.0
    assert _item(nyagi=125000, status="mfj")["line_46_adjustment"] == 0.0
    assert _item(nyagi=225000, status="mfj")["line_46_adjustment"] == 5000


def test_ny_itemized_second_reduction_adds_a_whole_quarter_first():
    """Worksheet 4, NYAGI $480,000, line 45 $40,000:
       line 1 = 480,000 - 475,000 (capped at 50,000) =  5,000
       line 2 = 5,000 / 50,000                       =    0.1
       line 3 = 25% of line 45                       = 10,000
       line 4 = line 2 x line 3                      =  1,000
       line 5 = line 3 + line 4                      = 11,000
    The whole first quarter is taken before the second phases in — a second
    phase-in alone would give 1,000 and over-state the deduction by 10,000.
    """
    out = _item(nyagi=480000)
    assert out["line_46_adjustment"] == 11000
    assert out["regime"] == "second_reduction"


def test_ny_itemized_flat_band_takes_half():
    """IT-196-I line 46: "more than $525,000 but not more than $1,000,000,
    enter 50% (.50) of line 45"."""
    out = _item(nyagi=600000)
    assert out["line_46_adjustment"] == 20000
    assert out["line_47_after_adjustment"] == 20000
    assert out["regime"] == "flat"


def test_ny_itemized_becomes_a_share_of_charity_at_the_top():
    """IT-196-I line 47: "more than $1,000,000, but not more than $10,000,000,
    enter 50% of line 19 … more than $10,000,000, enter 25% of line 19". Not a
    reduction — a REPLACEMENT. Every other itemized amount is disregarded, and
    line 46 is left blank."""
    out = _item(nyagi=2000000)
    assert out["line_46_adjustment"] == 0.0
    assert out["line_47_after_adjustment"] == 5000    # 50% of 10,000
    assert out["regime"] == "charitable_only"
    assert _item(nyagi=20000000)["line_47_after_adjustment"] == 2500


def test_ny_itemized_refuses_charity_it_was_never_given():
    """In the top regime the charitable total IS the deduction. A caller that
    passes zero because it was not tracking the split would silently zero the
    deduction for a taxpayer who has one."""
    with pytest.raises(ValueError, match="silently zero"):
        _item(charitable=0, nyagi=2000000)


def test_ny_itemized_refuses_an_unfetched_band():
    """The middle bands take a QUARTER and then a HALF of the whole deduction,
    so a default is not a rounding error — skipping the reduction UNDER-states
    the tax."""
    for key in ("first_band", "first_rate", "second_threshold", "second_band",
                "second_rate", "flat_threshold", "flat_rate",
                "charitable_only_threshold"):
        partial = {k: v for k, v in _NY_2025_ITEMIZED_REDUCTION.items()
                   if k != key}
        with pytest.raises(ValueError, match="missing"):
            _item(nyagi=125000, reduction=partial)
    with pytest.raises(ValueError, match="charitable_limit.tiers"):
        _item(nyagi=125000, limit={})


def test_ny_itemized_refuses_a_boundary_that_moved_on_one_side_only():
    """`charitable_only_threshold` and the lowest charitable tier describe ONE
    boundary — where the reduction stops and the replacement begins. A
    half-fetch that moves one leaves a band where both apply, or neither."""
    moved = {**_NY_2025_CHARITABLE_LIMIT,
             "tiers": [{"nyagi_over": 1500000, "share": 0.50}]}
    with pytest.raises(ValueError, match="same boundary"):
        _item(nyagi=125000, limit=moved)


def test_ny_itemized_adds_the_college_tuition_deduction_after_the_adjustment():
    """Line 48 sits AFTER line 47, so it is not reduced by §615. Folding it
    into line 45 would take a quarter or a half of it."""
    out = _item(nyagi=125000, college_tuition_deduction=1000)
    assert out["line_47_after_adjustment"] == 35000
    assert out["line_49_total"] == 36000


def test_ny_itemized_election_takes_the_larger():
    """Line 49: "For greater tax savings, enter the LARGER of the amounts"."""
    assert _item(standard_deduction=8000)["use_itemized"] is True
    assert _item(standard_deduction=50000)["use_itemized"] is False
    assert _item(standard_deduction=50000)["deduction"] == 50000


def test_ny_itemized_mfs_cannot_itemize_alone():
    """"If you are married and filing separate returns, BOTH of you must take
    the standard deduction unless BOTH of you elect to itemize." One spouse's
    choice is not their own to make, and taking the larger figure regardless
    would produce a return New York rejects."""
    alone = _item(status="mfs", standard_deduction=8000)
    assert alone["use_itemized"] is False
    assert alone["deduction"] == 8000
    assert any("BOTH spouses" in n for n in alone["notes"])
    together = _item(status="mfs", standard_deduction=8000,
                     mfs_spouse_itemizes=True)
    assert together["use_itemized"] is True


def test_ny_itemized_refuses_a_malformed_figure():
    for bad in (float("nan"), float("inf"), -1, True, "40000"):
        with pytest.raises(ValueError):
            _item(line_45=bad)


def test_ny_itemized_first_fraction_is_capped_and_rounded_to_four_places():
    """Worksheet 3 line 4 says "the LESSER of line 3 or $50,000" and line 5
    "round to the fourth decimal place". Every other case here sits inside the
    band and lands on a fraction exact at two places, so an uncapped or
    coarsely-rounded version survived them.

    Uncapped, a single filer at $300,000 would have a fraction of 4.0 and lose
    the WHOLE deduction instead of a quarter of it.
    """
    capped = _item(nyagi=300000)
    assert capped["line_46_adjustment"] == 10000   # 25% of 40,000, fraction 1.0
    assert capped["line_47_after_adjustment"] == 30000

    # 13,337 into the band: 0.26674, which the worksheet rounds to 0.2667 and
    # two places would make 0.27.
    out = _item(nyagi=113337)
    assert out["line_46_adjustment"] == round(40000 * 0.25 * 0.2667, 2)


def test_ny_itemized_second_fraction_caps_if_the_band_and_its_range_diverge():
    """Worksheet 4 line 1 says the excess "cannot exceed $50,000". Today that
    cap cannot bind, because the second band's WIDTH and the distance to the
    flat threshold are both $50,000 — so the guard is unreachable and a
    mutation removing it survives every ordinary case.

    They are two separately fetched figures, though, and a year that moved one
    without the other would send the second reduction past a whole quarter of
    the deduction with nothing to stop it. Constructed here rather than left to
    a future reader to discover.
    """
    diverged = {**_NY_2025_ITEMIZED_REDUCTION, "flat_threshold": 600000}
    out = _item(nyagi=580000, reduction=diverged)
    # A full quarter, then a second full quarter — not 2.1 quarters.
    assert out["line_46_adjustment"] == 20000
    assert out["regime"] == "second_reduction"
