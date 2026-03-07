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
