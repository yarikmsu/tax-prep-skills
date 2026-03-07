# Accuracy Audit Design — Tax Computation Pipeline

**Date:** 2026-03-05
**Scope:** Proactive correctness audit of `/tax-parse`, `/tax-compute`, `/tax-verify`, `/tax-review`
**Scenario:** W-2 + investments filing, TY2025 (OBBBA provisions)

## 1. Audit Scope & Structure

### Four Audit Layers

| Layer | What We Check | Deliverable |
|-------|--------------|-------------|
| **Python Computations** | All 8 functions in `tax_computations.py` | Test suite + fixes |
| **Skill Instructions** | Math formulas, line references, form thresholds in skill `.md` files | Corrected instructions |
| **Parsing Accuracy** | Field mapping in `/tax-parse` (W-2 boxes, 1099 boxes → result.json) | Mapping validation table |
| **Verification Correctness** | `/tax-verify` comparison logic, tolerance rules, form checklist | Updated checklist |

### Deliverables

1. **`tests/test_tax_computations.py`** — Comprehensive test suite with IRS-verified expected values
2. **Fixes** to any computation bugs found
3. **Audit report** documenting all findings (pass/fail per function, per skill section)

## 2. Computation Audit Plan

### 2.1 `compute_bracket_tax(taxable_income, brackets)`

- **What it does:** Progressive federal tax from bracket table
- **Audit:** Hand-compute tax for MFJ $150K, $400K, $650K against IRS Tax Table / Tax Computation Worksheet
- **OBBBA check:** Verify bracket boundaries match TY2025 OBBBA rates (if enacted)
- **Edge cases:** $0 income, income exactly at bracket boundary, single-bracket scenarios

### 2.2 `compute_qdcg_tax(taxable_income, qualified_dividends, net_ltcg, filing_status, brackets, cg_rates)`

- **What it does:** Qualified Dividends and Capital Gain Tax Worksheet (QDCG)
- **Audit:** Replicate IRS QDCG worksheet line-by-line for a scenario with $200K taxable, $15K qualified dividends, $30K net LTCG (MFJ)
- **Key trap:** Must use NET LTCG from Schedule D line 15 (after losses), not gross capital gain distributions
- **Edge cases:** QDCG exceeding 0% threshold, all income is QDCG, zero QDCG

### 2.3 `compute_se_tax(net_schedule_c, se_rate, net_factor, ss_wage_base, ss_rate, medicare_rate)`

- **What it does:** Self-employment tax (Schedule SE)
- **Audit:** Verify 92.35% factor, SS wage base cap, Medicare uncapped
- **Edge cases:** SE income < $400 (no SE tax), SE income > SS wage base, combined W-2 + SE exceeding SS base

### 2.4 `compute_additional_medicare(medicare_wages, se_income, filing_status, threshold, rate, w2_medicare_withheld)`

- **What it does:** Additional Medicare Tax (Form 8959)
- **Audit:** MFJ threshold $250K, single $200K. Verify employer withholds at $200K regardless of filing status — difference becomes credit
- **Key trap:** Employer withholding threshold ($200K) differs from MFJ actual threshold ($250K)
- **Edge cases:** Wages between $200K-$250K MFJ (over-withheld), wages exactly at threshold

### 2.5 `compute_ftc_limitation(foreign_tax_paid, foreign_source_income, worldwide_income, us_tax)`

- **What it does:** Foreign Tax Credit limitation (Form 1116)
- **Audit:** FTC = lesser of (foreign tax paid) vs (US tax × foreign income / worldwide income)
- **Edge cases:** Foreign income > worldwide income (loss scenarios), FTC > US tax, zero foreign income
- **Simplified election:** Verify skill correctly gates on $300/$600 threshold

### 2.6 `compute_child_tax_credit(num_children, agi, filing_status, credit_per_child, phase_out_start, phase_out_rate, refundable_limit)`

- **What it does:** Child Tax Credit + phase-out (Schedule 8812)
- **Audit:** Verify $50 reduction per $1,000 over threshold. Check OBBBA enhanced CTC if applicable
- **Edge cases:** AGI exactly at phase-out start, AGI well above (credit → $0), 0 children

### 2.7 `compute_qbi_deduction(qbi_income, taxable_income_before_qbi, filing_status, qbi_rate, threshold)`

- **What it does:** Qualified Business Income deduction (Form 8995 simplified)
- **Audit:** 20% of QBI, limited to 20% of taxable income before QBI deduction
- **Edge cases:** QBI > taxable income (limited), REIT dividends component, negative QBI (carryforward)

### 2.8 `check_form_8938_required(max_value, year_end_value, filing_status, residency, thresholds)`

- **What it does:** FATCA Form 8938 filing threshold check
- **Audit:** Verify all 4 threshold combinations (MFJ/single × US/foreign resident)
- **Edge cases:** Values exactly at threshold, missing residency data

## 3. Skill Instruction Validation

### `/tax-compute` Skill Audit

- Verify every IRS form line reference (1040, Schedule D, Form 8959, etc.) matches current IRS instructions
- Check that QDCG worksheet steps match the actual IRS worksheet
- Validate that the skill correctly sequences: income → adjustments → AGI → deductions → taxable income → tax → credits → other taxes → payments → refund/owed

### `/tax-parse` Skill Audit

- Verify W-2 box mapping (Box 1 → wages, Box 12 codes, etc.) matches IRS W-2 instructions
- Verify 1099 box mapping matches IRS 1099 instructions
- Check cross-document anomaly detection rules for correctness

### `/tax-verify` Skill Audit

- Verify line-to-field mapping table (Line 1a → `form_1040.line_1a_wages`, etc.)
- Check form requirement triggers (Schedule B threshold = $1,500, etc.)
- Validate rounding tolerance logic

### `/tax-review` Skill Audit

- Verify EA/IRS officer review checklist items are accurate
- Check that red-flag thresholds match current IRS audit selection criteria

## 4. Test Strategy

### Test Categories

1. **Unit tests** — One per function, multiple scenarios each
2. **Integration test** — Full pipeline: W-2 ($180K) + 1099-DIV ($15K qualified) + 1099-B ($30K net LTCG) → complete 1040
3. **Edge case tests** — Boundary values, zero inputs, negative values
4. **Regression tests** — Known tax software outputs as expected values

### IRS Verification Sources

- IRS Publication 17 (Your Federal Income Tax)
- IRS Tax Computation Worksheet
- IRS QDCG Worksheet (Schedule D instructions)
- IRS Form 8959 instructions
- IRS Form 1116 instructions

## 5. Success Criteria

- All 8 functions produce results matching hand-computed IRS worksheet values (within $1 rounding)
- All skill line references verified against current IRS forms
- Test suite passes with 100% of scenarios
- No silent failures (bad input → clear error, not wrong answer)
