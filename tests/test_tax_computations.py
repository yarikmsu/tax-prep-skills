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

    # --- Normal scenarios (6 tests) ---

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

    def test_none_taxable_income_raises(self):
        """None taxable_income raises ValueError."""
        with pytest.raises(ValueError):
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

    def test_none_profit_raises(self):
        """None net_profit raises ValueError."""
        with pytest.raises(ValueError):
            compute_se_tax(None, ss_wage_base=self.SS_WAGE_BASE_2025)

    def test_negative_w2_wages_raises(self):
        """Negative w2_ss_wages raises ValueError."""
        with pytest.raises(ValueError):
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
        with pytest.raises(ValueError):
            compute_additional_medicare(
                medicare_wages=200_000, se_income=0, threshold=None, withheld=2900,
            )

    def test_negative_wages_raises(self):
        """Negative wages raises ValueError."""
        with pytest.raises(ValueError):
            compute_additional_medicare(
                medicare_wages=-5000, se_income=0, threshold=250_000, withheld=0,
            )
