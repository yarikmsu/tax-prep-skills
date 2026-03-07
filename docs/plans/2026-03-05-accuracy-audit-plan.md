# Accuracy Audit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Audit all 8 tax computation functions for correctness, build a comprehensive test suite with IRS-verified expected values, and validate skill instruction accuracy.

**Architecture:** TDD approach — write failing tests with hand-computed IRS values first, then fix any bugs found. Skill instructions audited by cross-referencing IRS form line numbers. Tests use pytest with no external dependencies beyond the standard library.

**Tech Stack:** Python 3, pytest, `agents/lib/tax_computations.py`

---

### Task 1: Set Up Test Infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_tax_computations.py`

**Step 1: Create test directory and init file**

```bash
mkdir -p tests
touch tests/__init__.py
```

**Step 2: Create test file with imports and TY2025 bracket fixture**

```python
#!/usr/bin/env python3
"""Comprehensive tests for tax_computations.py.

Expected values are hand-computed from IRS worksheets and publications.
Tolerance: $0.01 (exact match after rounding to 2 decimal places).
"""

import math
import pytest
import sys
import os

# Add project root to path so we can import from agents/lib
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


# ═══ TY2025 MFJ Brackets (OBBBA) ═══
# Source: IRS Rev. Proc. or OBBBA enacted rates for TY2025
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

# TY2025 Capital Gains thresholds (MFJ)
CG_ZERO_LIMIT_MFJ = 96700
CG_FIFTEEN_LIMIT_MFJ = 600050

# TY2025 Capital Gains thresholds (Single)
CG_ZERO_LIMIT_SINGLE = 48350
CG_FIFTEEN_LIMIT_SINGLE = 533800
```

**Step 3: Run to verify imports work**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py -v --co`
Expected: "no tests ran" (collected 0 items) — imports succeed, no tests yet

**Step 4: Commit**

```bash
git add tests/
git commit -m "test: scaffold test infrastructure for tax computation audit"
```

---

### Task 2: Test `compute_bracket_tax` — Normal Scenarios

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write tests with hand-computed expected values**

Append to `tests/test_tax_computations.py`:

```python
# ═══════════════════════════════════════════════════
# Tests: compute_bracket_tax
# ═══════════════════════════════════════════════════

class TestComputeBracketTax:
    """Test progressive bracket tax computation.

    Hand-computed expected values for MFJ TY2025 brackets:
      10% on $0 - $23,850           = $2,385.00
      12% on $23,850 - $96,950      = $8,772.00
      22% on $96,950 - $206,700     = $24,145.00
      24% on $206,700 - $394,600    = $45,096.00
      32% on $394,600 - $501,050    = $34,064.00
      35% on $501,050 - $751,600    = $87,692.50
      37% on $751,600+
    """

    def test_mfj_150k(self):
        """$150K MFJ: spans 10%, 12%, 22% brackets.

        Hand calc:
          10% on 23,850 = 2,385.00
          12% on (96,950 - 23,850) = 12% on 73,100 = 8,772.00
          22% on (150,000 - 96,950) = 22% on 53,050 = 11,671.00
          Total = 22,828.00
        """
        tax, breakdown = compute_bracket_tax(150_000, MFJ_BRACKETS_2025)
        assert tax == 22_828.00
        assert len(breakdown) == 3

    def test_mfj_400k(self):
        """$400K MFJ: spans 10% through 32% brackets.

        Hand calc:
          10% on 23,850 = 2,385.00
          12% on 73,100 = 8,772.00
          22% on 109,750 = 24,145.00
          24% on 187,900 = 45,096.00
          32% on (400,000 - 394,600) = 32% on 5,400 = 1,728.00
          Total = 82,126.00
        """
        tax, _ = compute_bracket_tax(400_000, MFJ_BRACKETS_2025)
        assert tax == 82_126.00

    def test_mfj_650k(self):
        """$650K MFJ: spans 10% through 35% brackets.

        Hand calc:
          10% on 23,850 = 2,385.00
          12% on 73,100 = 8,772.00
          22% on 109,750 = 24,145.00
          24% on 187,900 = 45,096.00
          32% on 106,450 = 34,064.00
          35% on (650,000 - 501,050) = 35% on 148,950 = 52,132.50
          Total = 166,594.50
        """
        tax, _ = compute_bracket_tax(650_000, MFJ_BRACKETS_2025)
        assert tax == 166_594.50

    def test_zero_income(self):
        """$0 income = $0 tax."""
        tax, breakdown = compute_bracket_tax(0, MFJ_BRACKETS_2025)
        assert tax == 0.0
        assert breakdown == []

    def test_income_at_first_bracket_boundary(self):
        """Income exactly at 10% bracket boundary.

        $23,850 * 10% = $2,385.00
        """
        tax, breakdown = compute_bracket_tax(23_850, MFJ_BRACKETS_2025)
        assert tax == 2_385.00
        assert len(breakdown) == 1

    def test_single_bracket_only(self):
        """Single bracket [10% on everything]."""
        single = [{"rate": 0.10, "min": 0, "max": None}]
        tax, _ = compute_bracket_tax(100_000, single)
        assert tax == 10_000.00
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestComputeBracketTax -v`
Expected: All 6 tests PASS (if computation is correct) or specific failures indicating bugs

**Step 3: Fix any bugs found, re-run until all pass**

If any test fails, the hand-computed values are the ground truth. Fix `compute_bracket_tax` to match.

**Step 4: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: bracket tax tests with hand-computed MFJ TY2025 values"
```

---

### Task 3: Test `compute_bracket_tax` — Error Handling

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write error-handling tests**

Append:

```python
    def test_empty_brackets_raises(self):
        with pytest.raises(ValueError, match="empty"):
            compute_bracket_tax(100_000, [])

    def test_none_brackets_raises(self):
        with pytest.raises(ValueError, match="empty"):
            compute_bracket_tax(100_000, None)

    def test_none_income_raises(self):
        with pytest.raises(ValueError, match="cannot be None"):
            compute_bracket_tax(None, MFJ_BRACKETS_2025)

    def test_negative_income_raises(self):
        with pytest.raises(ValueError, match="negative"):
            compute_bracket_tax(-1000, MFJ_BRACKETS_2025)

    def test_malformed_bracket_raises(self):
        bad = [{"rate": 0.10}]  # missing "min"
        with pytest.raises(ValueError, match="missing required keys"):
            compute_bracket_tax(100_000, bad)
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestComputeBracketTax -v`
Expected: All 11 tests PASS

**Step 3: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: bracket tax error handling tests"
```

---

### Task 4: Test `compute_qdcg_tax`

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write QDCG tests with IRS worksheet walkthrough**

Append:

```python
# ═══════════════════════════════════════════════════
# Tests: compute_qdcg_tax
# ═══════════════════════════════════════════════════

class TestComputeQdcgTax:
    """Test Qualified Dividends and Capital Gain Tax Worksheet.

    IRS QDCG Worksheet (Schedule D instructions) step-by-step:
    1. Taxable income (1040 line 15)
    2. Qualified dividends (1040 line 3a)
    3. Net capital gain (Sched D line 15, or line 7 if no Sched D)
    4. Add lines 2+3 = total QDCG
    5. Ordinary income = line 1 - line 4 (min 0)
    6. Tax on ordinary at bracket rates
    7. Apply 0%/15%/20% rates to QDCG portion
    8. Compare with regular bracket tax, use lower
    """

    def test_mfj_200k_15k_qdiv_30k_ltcg(self):
        """MFJ: $200K taxable, $15K qual div, $30K net LTCG.

        QDCG = 15,000 + 30,000 = 45,000
        Ordinary = 200,000 - 45,000 = 155,000

        Tax on ordinary $155K (MFJ brackets):
          10% on 23,850 = 2,385.00
          12% on 73,100 = 8,772.00
          22% on (155,000 - 96,950) = 22% on 58,050 = 12,771.00
          Ordinary tax = 23,928.00

        QDCG at preferential rates:
          0% threshold (MFJ) = 96,700
          0% room = 96,700 - 155,000 = negative → 0 at 0%
          (ordinary income exceeds 0% threshold)
          All 45,000 at 15% = 6,750.00

        Total QDCG worksheet = 23,928.00 + 6,750.00 = 30,678.00

        Regular tax on $200K:
          10% on 23,850 = 2,385.00
          12% on 73,100 = 8,772.00
          22% on (200,000 - 96,950) = 22% on 103,050 = 22,671.00
          Regular = 33,828.00

        Use lower: 30,678.00
        """
        result = compute_qdcg_tax(
            taxable_income=200_000,
            qualified_dividends=15_000,
            net_ltcg=30_000,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        assert result["final_tax"] == 30_678.00
        assert result["used_qdcg_worksheet"] is True
        assert result["ordinary_income"] == 155_000.00
        assert result["qdcg_at_0_pct"] == 0.0
        assert result["qdcg_at_15_pct"] == 45_000.00
        assert result["qdcg_at_20_pct"] == 0.0

    def test_mfj_low_income_0pct_qdcg(self):
        """MFJ: $80K taxable, $10K qual div, $5K net LTCG — all QDCG at 0%.

        QDCG = 15,000
        Ordinary = 80,000 - 15,000 = 65,000

        0% room = 96,700 - 65,000 = 31,700 > 15,000 → all QDCG at 0%
        QDCG tax = 0

        Ordinary tax on $65K:
          10% on 23,850 = 2,385.00
          12% on (65,000 - 23,850) = 12% on 41,150 = 4,938.00
          Ordinary tax = 7,323.00

        Total = 7,323.00
        """
        result = compute_qdcg_tax(
            taxable_income=80_000,
            qualified_dividends=10_000,
            net_ltcg=5_000,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        assert result["qdcg_at_0_pct"] == 15_000.00
        assert result["qdcg_at_15_pct"] == 0.0
        assert result["qdcg_tax"] == 0.0
        assert result["final_tax"] == 7_323.00

    def test_zero_qdcg_equals_regular_tax(self):
        """No qualified dividends or LTCG — QDCG worksheet = regular tax."""
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
        """$50K all qualified dividends, no ordinary income.

        Ordinary = 0
        QDCG = 50,000
        All at 0% (0% limit 96,700 > 50,000)
        Tax = $0
        """
        result = compute_qdcg_tax(
            taxable_income=50_000,
            qualified_dividends=50_000,
            net_ltcg=0,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        assert result["ordinary_income"] == 0.0
        assert result["qdcg_at_0_pct"] == 50_000.00
        assert result["final_tax"] == 0.0

    def test_net_ltcg_negative_treated_as_zero(self):
        """Negative net LTCG (net loss) — only qualified dividends count as QDCG."""
        result = compute_qdcg_tax(
            taxable_income=100_000,
            qualified_dividends=5_000,
            net_ltcg=-3_000,
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        # QDCG = qualified_dividends + max(0, net_ltcg) = 5,000 + 0 = 5,000
        assert result["qdcg_total"] == 5_000.00

    def test_none_taxable_income_raises(self):
        with pytest.raises(ValueError, match="taxable_income cannot be None"):
            compute_qdcg_tax(None, 0, 0, MFJ_BRACKETS_2025,
                           CG_ZERO_LIMIT_MFJ, CG_FIFTEEN_LIMIT_MFJ)

    def test_none_cg_limit_raises(self):
        with pytest.raises(ValueError, match="thresholds not loaded"):
            compute_qdcg_tax(100_000, 0, 0, MFJ_BRACKETS_2025, None, None)
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestComputeQdcgTax -v`
Expected: All 7 tests PASS or reveal bugs to fix

**Step 3: Fix any bugs, re-run**

**Step 4: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: QDCG worksheet tests with IRS-verified expected values"
```

---

### Task 5: Test `compute_se_tax`

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write SE tax tests**

Append:

```python
# ═══════════════════════════════════════════════════
# Tests: compute_se_tax
# ═══════════════════════════════════════════════════

class TestComputeSeTax:
    """Test self-employment tax computation.

    IRS Schedule SE:
    1. Net profit from Schedule C
    2. Multiply by 92.35% = SE earnings
    3. SS tax: min(SE_earnings, SS_wage_base - W2_SS_wages) * 12.4%
    4. Medicare tax: SE_earnings * 2.9%
    5. SE tax = SS + Medicare
    6. Deductible half = SE_tax / 2
    """

    SS_WAGE_BASE_2025 = 176_100  # TY2025 SS wage base

    def test_basic_se_tax(self):
        """$80K SE profit, no W-2.

        SE earnings = 80,000 * 0.9235 = 73,880.00
        SS tax = 73,880 * 0.124 = 9,161.12
        Medicare = 73,880 * 0.029 = 2,142.52
        Total = 11,303.64
        Deductible half = 5,651.82
        """
        result = compute_se_tax(80_000, ss_wage_base=self.SS_WAGE_BASE_2025)
        assert result["se_earnings"] == 73_880.00
        assert result["se_tax"] == 11_303.64
        assert result["deductible_half"] == 5_651.82

    def test_se_with_w2_wages_capping_ss(self):
        """$80K SE + $150K W-2 SS wages — SS room limited.

        SE earnings = 80,000 * 0.9235 = 73,880.00
        SS room = 176,100 - 150,000 = 26,100
        SS taxable = min(73,880, 26,100) = 26,100
        SS tax = 26,100 * 0.124 = 3,236.40
        Medicare = 73,880 * 0.029 = 2,142.52
        Total = 5,378.92
        Deductible half = 2,689.46
        """
        result = compute_se_tax(
            80_000,
            ss_wage_base=self.SS_WAGE_BASE_2025,
            w2_ss_wages=150_000,
        )
        assert result["ss_room"] == 26_100.00
        assert result["ss_tax"] == 3_236.40
        assert result["medicare_tax"] == 2_142.52
        assert result["se_tax"] == 5_378.92

    def test_se_w2_exceeds_ss_base(self):
        """W-2 wages already exceed SS base — SS room = 0.

        SE earnings = 50,000 * 0.9235 = 46,175.00
        SS room = 176,100 - 180,000 = 0 (max 0)
        SS tax = 0
        Medicare = 46,175 * 0.029 = 1,339.08 (rounded)
        Total = 1,339.08
        """
        result = compute_se_tax(
            50_000,
            ss_wage_base=self.SS_WAGE_BASE_2025,
            w2_ss_wages=180_000,
        )
        assert result["ss_room"] == 0.0
        assert result["ss_tax"] == 0.0
        # Medicare is uncapped
        assert result["medicare_tax"] == round(46_175.00 * 0.029, 2)

    def test_net_loss_no_se_tax(self):
        """Negative profit = no SE tax."""
        result = compute_se_tax(-5_000, ss_wage_base=self.SS_WAGE_BASE_2025)
        assert result["se_tax"] == 0
        assert "net loss" in result["note"].lower()

    def test_below_threshold_no_se_tax(self):
        """$300 profit (< $400 threshold) = no SE tax."""
        result = compute_se_tax(300, ss_wage_base=self.SS_WAGE_BASE_2025)
        assert result["se_tax"] == 0
        assert "below" in result["note"].lower()

    def test_exactly_at_threshold(self):
        """$400 profit (= threshold) = SE tax IS computed.

        SE earnings = 400 * 0.9235 = 369.40
        SS tax = 369.40 * 0.124 = 45.81
        Medicare = 369.40 * 0.029 = 10.71
        Total = 56.52
        """
        result = compute_se_tax(400, ss_wage_base=self.SS_WAGE_BASE_2025)
        assert result["se_tax"] == round(369.40 * 0.124 + 369.40 * 0.029, 2)

    def test_none_profit_raises(self):
        with pytest.raises(ValueError, match="cannot be None"):
            compute_se_tax(None)

    def test_negative_w2_wages_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_se_tax(50_000, ss_wage_base=176_100, w2_ss_wages=-1)

    def test_zero_wage_base_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            compute_se_tax(50_000, ss_wage_base=0)

    def test_bool_wage_base_raises(self):
        with pytest.raises(ValueError, match="numeric value"):
            compute_se_tax(50_000, ss_wage_base=True)
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestComputeSeTax -v`
Expected: All 10 tests PASS or reveal bugs

**Step 3: Fix any bugs, re-run**

**Step 4: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: SE tax tests with SS wage base cap scenarios"
```

---

### Task 6: Test `compute_additional_medicare`

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write Additional Medicare Tax tests**

Append:

```python
# ═══════════════════════════════════════════════════
# Tests: compute_additional_medicare
# ═══════════════════════════════════════════════════

class TestComputeAdditionalMedicare:
    """Test Additional Medicare Tax (Form 8959).

    Key IRS rules:
    - 0.9% on combined wages + SE income over threshold
    - Employer withholds 0.9% on wages > $200K regardless of filing status
    - MFJ threshold is $250K, so MFJ filers with wages $200K-$250K
      get over-withholding credit
    """

    def test_mfj_260k_wages(self):
        """MFJ $260K wages, no SE. Threshold $250K.

        Excess = 260,000 - 250,000 = 10,000
        Additional tax = 10,000 * 0.009 = 90.00

        Employer withheld 0.9% on wages > $200K:
        Regular Medicare = 260,000 * 0.0145 = 3,770.00
        W-2 Box 6 total withheld = 3,770 + (260,000 - 200,000) * 0.009
                                  = 3,770 + 540 = 4,310.00
        (Assuming employer withheld correctly)
        """
        result = compute_additional_medicare(
            medicare_wages=260_000,
            se_income=0,
            threshold=250_000,
            withheld=4_310,  # regular + addl
        )
        assert result["excess_over_threshold"] == 10_000.00
        assert result["additional_tax"] == 90.00
        assert result["regular_medicare"] == 3_770.00
        assert result["withheld_addl"] == 540.00
        # Over-withheld: paid $540 addl, owe $90 → credit $450
        assert result["over_withholding_credit"] == 450.00

    def test_mfj_wages_between_200k_250k(self):
        """MFJ $220K wages — employer over-withholds, no actual addl tax owed.

        Excess = 220,000 - 250,000 = -30,000 → 0
        Additional tax = 0

        Regular Medicare = 220,000 * 0.0145 = 3,190.00
        Employer withheld addl on $20K (over $200K): 20,000 * 0.009 = 180
        Total W-2 Box 6 = 3,190 + 180 = 3,370

        Over-withholding credit = 180 (addl withheld) - 0 (addl owed) = 180
        """
        result = compute_additional_medicare(
            medicare_wages=220_000,
            se_income=0,
            threshold=250_000,
            withheld=3_370,
        )
        assert result["additional_tax"] == 0.0
        assert result["over_withholding_credit"] == 180.00

    def test_single_200k_threshold(self):
        """Single $250K wages. Threshold $200K.

        Excess = 250,000 - 200,000 = 50,000
        Additional tax = 50,000 * 0.009 = 450.00
        """
        result = compute_additional_medicare(
            medicare_wages=250_000,
            se_income=0,
            threshold=200_000,
            withheld=3_625 + 450,  # regular + addl
        )
        assert result["additional_tax"] == 450.00

    def test_wages_plus_se_income(self):
        """MFJ $200K wages + $80K SE income.

        Combined = 200,000 + 80,000 = 280,000
        Excess = 280,000 - 250,000 = 30,000
        Additional tax = 30,000 * 0.009 = 270.00
        """
        result = compute_additional_medicare(
            medicare_wages=200_000,
            se_income=80_000,
            threshold=250_000,
            withheld=2_900,  # regular only (wages < $200K)
        )
        assert result["combined"] == 280_000.00
        assert result["additional_tax"] == 270.00

    def test_below_threshold_no_tax(self):
        """Income below threshold."""
        result = compute_additional_medicare(
            medicare_wages=150_000,
            se_income=0,
            threshold=250_000,
            withheld=2_175,
        )
        assert result["additional_tax"] == 0.0

    def test_none_threshold_raises(self):
        with pytest.raises(ValueError, match="threshold cannot be None"):
            compute_additional_medicare(200_000, 0, None, 0)

    def test_negative_wages_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_additional_medicare(-1, 0, 250_000, 0)
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestComputeAdditionalMedicare -v`
Expected: All 7 tests PASS or reveal bugs

**Step 3: Fix any bugs, re-run**

**Step 4: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: Additional Medicare Tax tests with over-withholding scenarios"
```

---

### Task 7: Test `compute_ftc_limitation`

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write FTC limitation tests**

Append:

```python
# ═══════════════════════════════════════════════════
# Tests: compute_ftc_limitation
# ═══════════════════════════════════════════════════

class TestComputeFtcLimitation:
    """Test Foreign Tax Credit limitation (Form 1116).

    IRS Form 1116:
    FTC allowed = min(foreign_tax_paid, us_tax * foreign_income / worldwide_income)
    """

    def test_basic_ftc(self):
        """$5K foreign income, $200K worldwide, $30K US tax, $800 paid.

        Limitation = 30,000 * (5,000 / 200,000) = 30,000 * 0.025 = 750
        FTC = min(800, 750) = 750 (limited by limitation)
        """
        result = compute_ftc_limitation(
            foreign_source_income=5_000,
            worldwide_income=200_000,
            us_tax=30_000,
            foreign_tax_paid=800,
        )
        assert result["limitation"] == 750.00
        assert result["ftc_allowed"] == 750.00
        assert result["limited_by"] == "limitation"

    def test_ftc_limited_by_tax_paid(self):
        """Foreign tax paid < limitation.

        Limitation = 30,000 * (50,000 / 200,000) = 7,500
        FTC = min(500, 7,500) = 500 (limited by tax paid)
        """
        result = compute_ftc_limitation(
            foreign_source_income=50_000,
            worldwide_income=200_000,
            us_tax=30_000,
            foreign_tax_paid=500,
        )
        assert result["ftc_allowed"] == 500.00
        assert result["limited_by"] == "tax_paid"

    def test_foreign_income_exceeds_worldwide(self):
        """Foreign > worldwide (net domestic loss) — ratio capped at 1.0.

        Limitation = 50,000 * min(1.0, 300,000/200,000) = 50,000
        FTC = min(10,000, 50,000) = 10,000
        """
        result = compute_ftc_limitation(
            foreign_source_income=300_000,
            worldwide_income=200_000,
            us_tax=50_000,
            foreign_tax_paid=10_000,
        )
        assert result["limitation"] == 50_000.00
        assert result["ftc_allowed"] == 10_000.00

    def test_zero_worldwide_income(self):
        """Zero worldwide income — limitation = 0."""
        result = compute_ftc_limitation(
            foreign_source_income=1_000,
            worldwide_income=0,
            us_tax=0,
            foreign_tax_paid=100,
        )
        assert result["limitation"] == 0.0
        assert result["ftc_allowed"] == 0.0

    def test_negative_foreign_income(self):
        """Negative foreign income (foreign loss) — ratio floors at 0."""
        result = compute_ftc_limitation(
            foreign_source_income=-5_000,
            worldwide_income=200_000,
            us_tax=30_000,
            foreign_tax_paid=0,
        )
        assert result["limitation"] == 0.0

    def test_none_inputs_raise(self):
        with pytest.raises(ValueError):
            compute_ftc_limitation(None, 200_000, 30_000, 800)
        with pytest.raises(ValueError):
            compute_ftc_limitation(5_000, None, 30_000, 800)
        with pytest.raises(ValueError):
            compute_ftc_limitation(5_000, 200_000, None, 800)
        with pytest.raises(ValueError):
            compute_ftc_limitation(5_000, 200_000, 30_000, None)
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestComputeFtcLimitation -v`
Expected: All 6 tests PASS

**Step 3: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: FTC limitation tests including ratio cap and floor"
```

---

### Task 8: Test `compute_child_tax_credit`

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write CTC tests**

Append:

```python
# ═══════════════════════════════════════════════════
# Tests: compute_child_tax_credit
# ═══════════════════════════════════════════════════

class TestComputeChildTaxCredit:
    """Test Child Tax Credit computation.

    IRS rules:
    - Credit per child (from tax_law JSON, e.g., $2,000 or $2,200 OBBBA)
    - Phase-out: $50 reduction per $1,000 (or fraction) over threshold
    - MFJ threshold: $400,000; Single: $200,000
    """

    def test_two_children_below_phaseout(self):
        """2 children, $200K AGI, MFJ $400K threshold. No phase-out."""
        result = compute_child_tax_credit(
            num_qualifying_children=2,
            credit_per_child=2_000,
            agi=200_000,
            phase_out_start=400_000,
        )
        assert result["credit_allowed"] == 4_000.00
        assert result["phase_out_reduction"] == 0.0

    def test_phaseout_exact_1000_increments(self):
        """AGI $420K, MFJ — exactly $20K over threshold.

        Reduction = ceil(20,000 / 1,000) * 50 = 20 * 50 = 1,000
        Credit = 4,000 - 1,000 = 3,000
        """
        result = compute_child_tax_credit(
            num_qualifying_children=2,
            credit_per_child=2_000,
            agi=420_000,
            phase_out_start=400_000,
        )
        assert result["phase_out_reduction"] == 1_000.00
        assert result["credit_allowed"] == 3_000.00

    def test_phaseout_fraction_of_1000(self):
        """AGI $400,500 — $500 over = ceil(500/1000) = 1 → $50 reduction.

        IRS: "for each $1,000 (or fraction thereof)"
        """
        result = compute_child_tax_credit(
            num_qualifying_children=2,
            credit_per_child=2_000,
            agi=400_500,
            phase_out_start=400_000,
        )
        assert result["phase_out_reduction"] == 50.00
        assert result["credit_allowed"] == 3_950.00

    def test_phaseout_to_zero(self):
        """AGI high enough to eliminate credit entirely.

        2 children * $2,000 = $4,000 full credit
        Need $4,000 / $50 = 80 increments = $80,000 over threshold
        AGI $480,000 or higher → credit = $0
        """
        result = compute_child_tax_credit(
            num_qualifying_children=2,
            credit_per_child=2_000,
            agi=500_000,
            phase_out_start=400_000,
        )
        assert result["credit_allowed"] == 0.0

    def test_exactly_at_phaseout_start(self):
        """AGI exactly at threshold — no reduction."""
        result = compute_child_tax_credit(
            num_qualifying_children=1,
            credit_per_child=2_000,
            agi=400_000,
            phase_out_start=400_000,
        )
        assert result["credit_allowed"] == 2_000.00

    def test_zero_children(self):
        """No children = no credit."""
        result = compute_child_tax_credit(
            num_qualifying_children=0,
            credit_per_child=2_000,
            agi=100_000,
            phase_out_start=400_000,
        )
        assert result["credit_allowed"] == 0.0

    def test_obbba_enhanced_credit(self):
        """OBBBA enhanced $2,200/child — just different credit_per_child."""
        result = compute_child_tax_credit(
            num_qualifying_children=3,
            credit_per_child=2_200,
            agi=200_000,
            phase_out_start=400_000,
        )
        assert result["credit_allowed"] == 6_600.00

    def test_none_inputs_raise(self):
        with pytest.raises(ValueError):
            compute_child_tax_credit(None, 2_000, 200_000, 400_000)
        with pytest.raises(ValueError):
            compute_child_tax_credit(2, None, 200_000, 400_000)
        with pytest.raises(ValueError):
            compute_child_tax_credit(2, 2_000, None, 400_000)
        with pytest.raises(ValueError):
            compute_child_tax_credit(2, 2_000, 200_000, None)
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestComputeChildTaxCredit -v`
Expected: All 8 tests PASS

**Step 3: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: CTC tests with phase-out including fraction-of-1000 rule"
```

---

### Task 9: Test `compute_qbi_deduction`

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write QBI deduction tests**

Append:

```python
# ═══════════════════════════════════════════════════
# Tests: compute_qbi_deduction
# ═══════════════════════════════════════════════════

class TestComputeQbiDeduction:
    """Test QBI deduction (Form 8995 simplified).

    IRS Form 8995:
    - QBI component = max(0, QBI * 20%)
    - REIT component = REIT dividends * 20%
    - Total deduction = QBI component + REIT component
    - Negative QBI → loss carryforward
    """

    def test_positive_qbi_with_reit(self):
        """$50K QBI + $1K REIT dividends.

        QBI component = 50,000 * 0.20 = 10,000
        REIT component = 1,000 * 0.20 = 200
        Total = 10,200
        """
        result = compute_qbi_deduction(50_000, 1_000)
        assert result["qbi_component"] == 10_000.00
        assert result["reit_component"] == 200.00
        assert result["total_deduction"] == 10_200.00
        assert result["loss_carryforward"] == 0.0

    def test_negative_qbi_carryforward(self):
        """Negative QBI = loss carryforward, no QBI component.

        QBI component = max(0, -10,000 * 0.20) = 0
        REIT component = 500 * 0.20 = 100
        Total = 100
        Loss carryforward = -10,000
        """
        result = compute_qbi_deduction(-10_000, 500)
        assert result["qbi_component"] == 0.0
        assert result["reit_component"] == 100.00
        assert result["total_deduction"] == 100.00
        assert result["loss_carryforward"] == -10_000.00

    def test_zero_qbi_zero_reit(self):
        result = compute_qbi_deduction(0, 0)
        assert result["total_deduction"] == 0.0

    def test_reit_only(self):
        """No QBI, only REIT dividends. $2K REIT → $400 deduction."""
        result = compute_qbi_deduction(0, 2_000)
        assert result["total_deduction"] == 400.00

    def test_negative_reit_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            compute_qbi_deduction(1_000, -100)

    def test_none_qbi_raises(self):
        with pytest.raises(ValueError):
            compute_qbi_deduction(None, 0)
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestComputeQbiDeduction -v`
Expected: All 6 tests PASS

**Step 3: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: QBI deduction tests with loss carryforward"
```

---

### Task 10: Test `check_form_8938_required`

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write Form 8938 tests**

Append:

```python
# ═══════════════════════════════════════════════════
# Tests: check_form_8938_required
# ═══════════════════════════════════════════════════

class TestCheckForm8938Required:
    """Test FATCA Form 8938 filing threshold.

    IRS thresholds (US resident):
      MFJ: year_end > $200K OR any_time > $300K
      Single/MFS/HoH: year_end > $50K OR any_time > $75K

    Foreign resident thresholds are higher:
      MFJ: year_end > $400K OR any_time > $600K
      Single: year_end > $200K OR any_time > $300K
    """

    THRESHOLDS = {
        "mfj_us": {"year_end": 200_000, "any_time": 300_000},
        "single_us": {"year_end": 50_000, "any_time": 75_000},
        "mfj_foreign": {"year_end": 400_000, "any_time": 600_000},
        "single_foreign": {"year_end": 200_000, "any_time": 300_000},
    }

    def test_mfj_us_below_threshold(self):
        accounts = [
            {"year_end_value_usd": 100_000, "max_value_usd": 150_000},
        ]
        result = check_form_8938_required(accounts, "mfj", False, self.THRESHOLDS)
        assert result["required"] is False

    def test_mfj_us_year_end_triggers(self):
        accounts = [
            {"year_end_value_usd": 150_000, "max_value_usd": 200_000},
            {"year_end_value_usd": 60_000, "max_value_usd": 80_000},
        ]
        result = check_form_8938_required(accounts, "mfj", False, self.THRESHOLDS)
        # total_year_end = 210,000 > 200,000 → required
        assert result["required"] is True
        assert result["total_year_end"] == 210_000.00

    def test_mfj_us_any_time_triggers(self):
        accounts = [
            {"year_end_value_usd": 100_000, "max_value_usd": 310_000},
        ]
        result = check_form_8938_required(accounts, "mfj", False, self.THRESHOLDS)
        # total_max = 310,000 > 300,000 → required
        assert result["required"] is True

    def test_single_us_threshold(self):
        accounts = [
            {"year_end_value_usd": 55_000, "max_value_usd": 60_000},
        ]
        result = check_form_8938_required(accounts, "single", False, self.THRESHOLDS)
        # 55,000 > 50,000 → required
        assert result["required"] is True

    def test_hoh_uses_single_thresholds(self):
        """HoH uses single thresholds per IRS rules."""
        accounts = [
            {"year_end_value_usd": 55_000, "max_value_usd": 60_000},
        ]
        result = check_form_8938_required(accounts, "hoh", False, self.THRESHOLDS)
        assert result["required"] is True
        assert result["threshold_year_end"] == 50_000

    def test_mfs_uses_single_thresholds(self):
        """MFS uses single thresholds per IRS rules."""
        accounts = [
            {"year_end_value_usd": 55_000, "max_value_usd": 60_000},
        ]
        result = check_form_8938_required(accounts, "mfs", False, self.THRESHOLDS)
        assert result["required"] is True
        assert result["threshold_year_end"] == 50_000

    def test_foreign_resident_higher_thresholds(self):
        """Foreign resident has much higher thresholds."""
        accounts = [
            {"year_end_value_usd": 150_000, "max_value_usd": 250_000},
        ]
        result = check_form_8938_required(accounts, "single", True, self.THRESHOLDS)
        # single_foreign: year_end 200K, any_time 300K — below both
        assert result["required"] is False

    def test_string_values_coerced(self):
        """Dollar amounts as strings are coerced to float."""
        accounts = [
            {"year_end_value_usd": "$55,000", "max_value_usd": "60000"},
        ]
        result = check_form_8938_required(accounts, "single", False, self.THRESHOLDS)
        assert result["required"] is True
        assert result["total_year_end"] == 55_000.00

    def test_empty_accounts_not_required(self):
        result = check_form_8938_required([], "mfj", False, self.THRESHOLDS)
        assert result["required"] is False

    def test_exactly_at_threshold_not_required(self):
        """Values exactly at threshold — NOT required (must EXCEED, not equal)."""
        accounts = [
            {"year_end_value_usd": 200_000, "max_value_usd": 300_000},
        ]
        result = check_form_8938_required(accounts, "mfj", False, self.THRESHOLDS)
        # 200,000 is NOT > 200,000 → not required
        assert result["required"] is False

    def test_invalid_filing_status_raises(self):
        with pytest.raises(ValueError, match="Invalid filing_status"):
            check_form_8938_required([], "married", False, self.THRESHOLDS)

    def test_none_accounts_raises(self):
        with pytest.raises(ValueError, match="cannot be None"):
            check_form_8938_required(None, "mfj", False, self.THRESHOLDS)

    def test_non_numeric_account_raises(self):
        accounts = [{"year_end_value_usd": "abc", "max_value_usd": 100}]
        with pytest.raises(ValueError, match="non-numeric"):
            check_form_8938_required(accounts, "mfj", False, self.THRESHOLDS)
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestCheckForm8938Required -v`
Expected: All 13 tests PASS

**Step 3: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: Form 8938 FATCA threshold tests for all filing statuses"
```

---

### Task 11: Test `_coerce_numeric` Helper

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write coercion tests**

Append:

```python
# ═══════════════════════════════════════════════════
# Tests: _coerce_numeric
# ═══════════════════════════════════════════════════

class TestCoerceNumeric:
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
```

**Step 2: Run tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestCoerceNumeric -v`
Expected: All 9 tests PASS

**Step 3: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: _coerce_numeric helper tests"
```

---

### Task 12: Integration Test — Full W-2 + Investments Scenario

**Files:**
- Modify: `tests/test_tax_computations.py`

**Step 1: Write end-to-end pipeline test**

Append:

```python
# ═══════════════════════════════════════════════════
# Integration Test: Full pipeline
# ═══════════════════════════════════════════════════

class TestFullPipeline:
    """End-to-end test: W-2 ($180K) + investments → complete 1040 computation.

    Scenario: MFJ TY2025
    - W-2 wages: $180,000
    - 1099-DIV: $15,000 ordinary, $12,000 qualified, $3,000 cap gain dist
    - 1099-B: $30,000 net LTCG (Schedule D line 15)
    - 1099-INT: $2,000 taxable interest
    - Foreign tax paid: $400 (on dividends, passive category)
    - Foreign source income: $3,000
    - 2 qualifying children
    - No self-employment
    - MFJ standard deduction: $30,000 (OBBBA enhanced)
    - HSA employer contribution: $2,000 (W-2 Box 12 Code W)
    """

    def test_full_computation(self):
        # ── Step 1: Income ──
        wages = 180_000
        taxable_interest = 2_000
        ordinary_dividends = 15_000
        qualified_dividends = 12_000
        cap_gain_dist = 3_000
        net_ltcg = 30_000  # Schedule D line 15

        # 1040 lines
        line_1a = wages
        line_2b = taxable_interest
        line_3a = qualified_dividends
        line_3b = ordinary_dividends
        line_7 = net_ltcg + cap_gain_dist  # Schedule D line 16 (combined)
        line_9 = line_1a + line_2b + line_3b + line_7  # total income

        assert line_9 == 180_000 + 2_000 + 15_000 + 33_000
        assert line_9 == 230_000

        # ── Step 2: AGI ──
        line_10 = 0  # no adjustments (no SE, no IRA, HSA employer-only)
        line_11 = line_9 - line_10  # AGI
        assert line_11 == 230_000

        # ── Step 3: Taxable Income ──
        standard_deduction = 30_000  # OBBBA MFJ
        qbi = compute_qbi_deduction(0, 0)  # no QBI
        line_13a = qbi["total_deduction"]
        assert line_13a == 0.0

        line_14 = standard_deduction + line_13a
        line_15 = max(0, line_11 - line_14)
        assert line_15 == 200_000

        # ── Step 4: Tax (QDCG worksheet) ──
        qdcg_result = compute_qdcg_tax(
            taxable_income=line_15,
            qualified_dividends=qualified_dividends,
            net_ltcg=net_ltcg,  # NET from Sched D, not including cap gain dist
            brackets=MFJ_BRACKETS_2025,
            cg_zero_limit=CG_ZERO_LIMIT_MFJ,
            cg_fifteen_limit=CG_FIFTEEN_LIMIT_MFJ,
        )
        line_16 = qdcg_result["final_tax"]
        assert line_16 > 0

        # ── Step 5: Credits ──
        ctc = compute_child_tax_credit(
            num_qualifying_children=2,
            credit_per_child=2_000,
            agi=line_11,
            phase_out_start=400_000,
        )
        line_19 = ctc["credit_allowed"]
        assert line_19 == 4_000.00  # below phase-out

        ftc = compute_ftc_limitation(
            foreign_source_income=3_000,
            worldwide_income=line_11,
            us_tax=line_16,
            foreign_tax_paid=400,
        )
        ftc_credit = ftc["ftc_allowed"]
        # FTC limitation = line_16 * (3,000 / 230,000)
        # Since $400 < limitation, ftc_allowed = $400
        assert ftc["limited_by"] == "tax_paid"
        assert ftc_credit == 400.00

        total_credits = line_19 + ftc_credit
        line_22 = max(0, line_16 - total_credits)

        # ── Step 6: Other Taxes ──
        # Additional Medicare: wages $180K < $200K employer threshold → no addl
        # but check anyway for the function call
        addl_med = compute_additional_medicare(
            medicare_wages=180_000,
            se_income=0,
            threshold=250_000,  # MFJ
            withheld=180_000 * 0.0145,  # regular only
        )
        assert addl_med["additional_tax"] == 0.0

        line_23 = addl_med["additional_tax"]
        line_24 = line_22 + line_23  # total tax

        # ── Step 7: Verify pipeline consistency ──
        # Total tax should be: QDCG tax - credits + other taxes
        assert line_24 == max(0, line_16 - total_credits) + line_23
        assert line_24 > 0  # sanity: positive tax owed
```

**Step 2: Run the integration test**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py::TestFullPipeline -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_tax_computations.py
git commit -m "test: integration test for full W-2 + investments pipeline"
```

---

### Task 13: Audit Skill Instructions — `/tax-compute` Line References

**Files:**
- Audit: `agents/skills/tax-compute.md`
- Potentially modify: `agents/skills/tax-compute.md`

**Step 1: Verify Form 1040 line references**

Cross-reference every line number in `tax-compute.md` Step 3-9 against IRS Form 1040 (2025):

| Skill Reference | IRS Form 1040 Line | Correct? |
|----------------|-------------------|----------|
| Line 1a: wages | Line 1a: Wages, salaries, tips | Verify |
| Line 2b: taxable interest | Line 2b: Taxable interest | Verify |
| Line 3a: qualified dividends | Line 3a: Qualified dividends | Verify |
| Line 3b: ordinary dividends | Line 3b: Ordinary dividends | Verify |
| Line 7: capital gain | Line 7: Capital gain or (loss) | Verify |
| Line 8: Schedule 1 other | Line 8: Other income from Sched 1 | Verify |
| Line 9: total income | Line 9: Total income | Verify |
| Line 10: adjustments | Line 10: Adjustments from Sched 1 | Verify |
| Line 11: AGI | Line 11: Adjusted gross income | Verify |
| Line 12: standard deduction | Line 12: Standard deduction or itemized | Verify |
| Line 13a: QBI | Line 13a: Qualified business income deduction | Verify |
| Line 15: taxable income | Line 15: Taxable income | Verify |
| Line 16: tax | Line 16: Tax | Verify |
| Line 19: CTC | Line 19: Child tax credit | Verify |
| Line 24: total tax | Line 24: Total tax | Verify |

Use **WebSearch** to verify against current IRS Form 1040 if unsure about any line.

**Step 2: Check QDCG worksheet reference**

Verify `tax-compute.md` Step 6 matches actual IRS QDCG Worksheet steps:
- Confirm the skill says "net_ltcg: Schedule D line 15" — this is correct
- Confirm it warns about not using gross cap gain distributions — this is correct

**Step 3: Check Form 8959 reference**

Verify Step 8 says "Medicare wages > $200K" as the trigger — this is the employer withholding threshold, correct. The actual additional tax threshold varies by filing status and comes from `tax_law JSON`.

**Step 4: Document findings in audit report**

Create or append findings to `docs/plans/2026-03-05-accuracy-audit-report.md`.

**Step 5: Fix any incorrect references found**

**Step 6: Commit**

```bash
git add agents/skills/tax-compute.md docs/plans/
git commit -m "audit: verify tax-compute skill line references against IRS forms"
```

---

### Task 14: Audit Skill Instructions — `/tax-verify` Field Mapping

**Files:**
- Audit: `agents/skills/tax-verify.md`
- Potentially modify: `agents/skills/tax-verify.md`

**Step 1: Verify the line-to-field mapping table**

Cross-reference the table in `tax-verify.md` Step 2 against `result_template.json`:

| Verify | Line | Field in Skill | Field in Template | Match? |
|--------|------|---------------|-------------------|--------|
| | 1a | form_1040.line_1a_wages | form_1040.line_1a_wages | |
| | 1z | form_1040.line_1z_total_wages | form_1040.line_1z_total_wages | |
| | 2b | form_1040.line_2b_taxable_interest | form_1040.line_2b_taxable_interest | |
| | 3a | form_1040.line_3a_qualified_dividends | form_1040.line_3a_qualified_dividends | |
| ... | | | | |

Check ALL entries in the table against the template.

**Step 2: Verify form requirement triggers**

Check the form trigger table in Step 3:
- Schedule B: interest or dividends > $1,500 — verify this is current IRS threshold
- Form 8959: Medicare wages > $200K — correct (employer withholding trigger)
- Form 1116: foreign tax > simplified limit — verify $300/$600 from tax_law JSON

**Step 3: Document findings**

Append to `docs/plans/2026-03-05-accuracy-audit-report.md`.

**Step 4: Fix any mismatches**

**Step 5: Commit**

```bash
git add agents/skills/tax-verify.md docs/plans/
git commit -m "audit: verify tax-verify field mapping against result_template.json"
```

---

### Task 15: Audit `/tax-parse` W-2 Box Mapping

**Files:**
- Audit: `agents/skills/tax-parse.md`
- Audit: `agents/templates/result_template.json` (w2_summary section)

**Step 1: Verify W-2 box references**

Cross-reference `tax-parse.md` "Document-Specific Parsing Notes" section:

| Skill Says | IRS W-2 Box | Correct? |
|-----------|------------|----------|
| Box 1: wages | Federal wages, tips | Verify |
| Box 2: fed withheld | Federal income tax withheld | Verify |
| Box 3: SS wages | Social security wages | Verify |
| Box 4: SS tax | Social security tax withheld | Verify |
| Box 5: Medicare wages | Medicare wages and tips | Verify |
| Box 6: Medicare tax | Medicare tax withheld | Verify |
| Box 12 Code D: 401k | Elective deferrals to 401(k) | Verify |
| Box 12 Code W: HSA | Employer HSA contributions | Verify |
| Box 12 Code DD: health | Cost of employer health coverage | Verify |
| Box 12 Code AA: Roth 401k | Designated Roth contributions to 401(k) | Verify |
| Box 12 Code C: life ins | Taxable cost of group-term life > $50K | Verify |

**Step 2: Verify the anomaly detection rules**

Check Step 5.5 rules:
- "Box 5 ≥ Box 3" — correct (Medicare wages ≥ SS wages because SS is capped)
- "Box 5 ≥ Box 1" — correct (pre-tax 401k/HSA reduce Box 1 but not Box 5)
- "Box 3 ≤ SS wage base" — correct

**Step 3: Verify 1099-DIV box mapping**

- Box 1a: ordinary dividends — verify
- Box 1b: qualified dividends — verify
- Box 2a: capital gain distributions — verify
- Box 5: Section 199A dividends — verify
- Box 7: foreign tax paid — verify

**Step 4: Document findings, fix any errors**

**Step 5: Commit**

```bash
git add agents/skills/tax-parse.md docs/plans/
git commit -m "audit: verify tax-parse W-2 and 1099 box mappings"
```

---

### Task 16: Audit `/tax-review` Checklist Accuracy

**Files:**
- Audit: `agents/skills/tax-review.md`

**Step 1: Verify DIF Score red flags**

Check that the thresholds mentioned in the IRS Officer Review section are accurate:
- "Charitable > 3% of AGI" — this is a common heuristic but verify it's reasonable
- "Schedule C expense ratio > 50%" — verify this is a reasonable audit flag
- "100% business use of vehicle" — correct (IRS does flag this)

**Step 2: Verify EA checklist items**

Check all 29 items in the Forgotten Items Checklist. Key verifications:
- Item 27: 401(k) max references `retirement_401k` from tax_law JSON — correct
- Item 29: HSA max references `hsa_limits` from tax_law JSON — correct
- Catch-up contribution age rules (50+, 60-63 SECURE 2.0) — verify

**Step 3: Verify Schedule B threshold**

The skill says "interest + dividends > $1,500" triggers Schedule B. Verify this is the current IRS threshold.

**Step 4: Document findings**

**Step 5: Commit**

```bash
git add agents/skills/tax-review.md docs/plans/
git commit -m "audit: verify tax-review checklist accuracy"
```

---

### Task 17: Run Full Test Suite & Generate Audit Report

**Files:**
- Create: `docs/plans/2026-03-05-accuracy-audit-report.md`

**Step 1: Run all tests**

Run: `cd /path/to/tax-prep-skills && python3 -m pytest tests/test_tax_computations.py -v --tb=short`
Expected: All tests PASS

**Step 2: Run existing self-test**

Run: `cd /path/to/tax-prep-skills && python3 agents/lib/tax_computations.py`
Expected: All self-tests pass, no errors

**Step 3: Write final audit report**

Create `docs/plans/2026-03-05-accuracy-audit-report.md` with:

```markdown
# Accuracy Audit Report — Tax Computation Pipeline

**Date:** 2026-03-05
**Auditor:** Claude Code
**Scope:** 8 computation functions + 4 skill files

## Function Audit Results

| # | Function | Tests | Status | Bugs Found |
|---|----------|-------|--------|------------|
| 1 | compute_bracket_tax | 11 | PASS/FAIL | {details} |
| 2 | compute_qdcg_tax | 7 | PASS/FAIL | {details} |
| 3 | compute_se_tax | 10 | PASS/FAIL | {details} |
| 4 | compute_additional_medicare | 7 | PASS/FAIL | {details} |
| 5 | compute_ftc_limitation | 6 | PASS/FAIL | {details} |
| 6 | compute_child_tax_credit | 8 | PASS/FAIL | {details} |
| 7 | compute_qbi_deduction | 6 | PASS/FAIL | {details} |
| 8 | check_form_8938_required | 13 | PASS/FAIL | {details} |
| 9 | _coerce_numeric | 9 | PASS/FAIL | {details} |
| 10 | Integration pipeline | 1 | PASS/FAIL | {details} |

## Skill Instruction Audit Results

| Skill | Line References | Field Mapping | Thresholds | Status |
|-------|----------------|---------------|------------|--------|
| /tax-compute | {count} verified | N/A | {count} verified | PASS/FAIL |
| /tax-parse | N/A | {count} verified | {count} verified | PASS/FAIL |
| /tax-verify | {count} verified | {count} verified | {count} verified | PASS/FAIL |
| /tax-review | N/A | N/A | {count} verified | PASS/FAIL |

## Bugs Fixed

{list of any bugs found and fixed during audit}

## Recommendations

{any remaining concerns or future improvements}
```

**Step 4: Final commit**

```bash
git add docs/plans/ tests/
git commit -m "audit: complete accuracy audit with test suite and report"
```

---

Plan complete and saved to `docs/plans/2026-03-05-accuracy-audit-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach?