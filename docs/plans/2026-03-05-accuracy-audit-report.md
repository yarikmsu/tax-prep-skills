# Accuracy Audit Report — Tax Computation Pipeline

**Date:** 2026-03-07
**Auditor:** Claude Code
**Scope:** 8 computation functions + 1 helper + 4 skill files + 1 integration test

## Function Audit Results

| # | Function | Tests | Status | Bugs Found |
|---|----------|-------|--------|------------|
| 1 | `compute_bracket_tax` | 11 | PASS | None — all hand-computed MFJ TY2025 values match |
| 2 | `compute_qdcg_tax` | 7 | PASS | None — QDCG worksheet correctly uses `max(0, net_ltcg)` and compares with regular tax |
| 3 | `compute_se_tax` | 10 | PASS | None — SS wage base cap, W-2 interaction, and $400 threshold all correct |
| 4 | `compute_additional_medicare` | 7 | PASS | None — over-withholding credit logic correct (employer $200K vs MFJ $250K threshold) |
| 5 | `compute_ftc_limitation` | 6 | PASS | None — ratio capped at 1.0 (upper) and 0.0 (lower) correctly |
| 6 | `compute_child_tax_credit` | 8 | PASS | None — `math.ceil` correctly implements "fraction thereof" rule |
| 7 | `compute_qbi_deduction` | 6 | PASS | None — negative QBI carryforward handled correctly |
| 8 | `check_form_8938_required` | 13 | PASS | None — strict `>` (not `>=`) correct per IRS; HoH/MFS correctly map to single thresholds |
| 9 | `_coerce_numeric` | 9 | PASS | None — bool rejection, None default behavior all correct |
| 10 | Integration pipeline | 1 | PASS | None — full W-2 + investments scenario produces consistent results |

**Total: 90 tests, 90 passed, 0 failed. No computation bugs found.**

## Skill Instruction Audit Results

| Skill | Items Verified | Bugs Found | Status |
|-------|---------------|------------|--------|
| `/tax-compute` | 21 line references, QDCG worksheet, Form 8959 trigger, OBBBA placement, Line 15 formula | 0 | PASS |
| `/tax-parse` | 11 W-2 boxes, 5 1099-DIV boxes, 6 1099-B types, 4 anomaly rules, 2 1099-R codes | **1 CRITICAL** | FIXED |
| `/tax-verify` | 21 field mappings, 7 form triggers, rounding tolerance | **1 gap** | FIXED |
| `/tax-review` | 3 DIF flags, 29 EA checklist items, 5 thresholds | 0 | PASS |

## Bugs Fixed

### 1. CRITICAL: 1099-B Box Type Descriptions (tax-parse.md)

**Before:** "B (basis reported, long-term)" — WRONG
**After:** "B (short-term, basis NOT reported to IRS)" — per IRS Form 8949

The skill had swapped Box B (short-term, not reported) with Box D (long-term, reported). All 6 Form 8949 checkbox types (A-F) are now correctly documented. Foreign brokerages updated from "Box C or F" to "Box B or E" (basis not reported).

**Impact:** If a subagent followed the old mapping literally, it could misclassify short-term transactions as long-term on Form 8949, potentially affecting tax computation (different holding period = different rate).

### 2. COMPLETENESS: Missing Lines in Verify Table (tax-verify.md)

**Before:** 21 lines in comparison table
**After:** 28 lines — added 4a, 4b, 10, 13a, 25c, 33, 37

These lines were present in `result_template.json` but not in the verify comparison table, meaning discrepancies on IRA distributions, adjustments, QBI deduction, other withholding, total payments, and amount owed could go undetected.

### 3. TEMPLATE: Schedule D Field Name (result_template.json)

**Before:** `line_1c_1099b_box_f` — Schedule D line 1c is Part I (short-term), but "box_f" is long-term
**After:** `line_1c_1099b_box_c` — correctly references Box C (short-term, no 1099-B)

## Key Verification Notes

- **QDCG trap verified:** Code correctly uses `max(0, net_ltcg)` — negative net LTCG does not reduce QDCG amount, only qualified dividends count
- **CTC phase-out verified:** Uses `math.ceil(excess / 1000)` implementing IRS "fraction thereof" rule correctly
- **Form 8938 threshold verified:** Uses strict `>` not `>=` — values exactly at threshold do NOT trigger filing
- **Additional Medicare verified:** Correctly distinguishes employer withholding threshold ($200K) from actual tax threshold (filing-status dependent)
- **SE tax verified:** Correctly caps SS portion at `wage_base - w2_ss_wages` while keeping Medicare uncapped
- **FTC ratio verified:** Capped at 1.0 (foreign > worldwide) and floored at 0.0 (net foreign loss)

## Test Coverage Summary

| Category | Tests |
|----------|-------|
| Bracket tax (normal + errors) | 12 |
| QDCG worksheet | 8 |
| SE tax | 11 |
| Additional Medicare | 7 |
| FTC limitation | 9 |
| Child Tax Credit | 12 |
| QBI deduction | 6 |
| Form 8938 FATCA | 15 |
| _coerce_numeric helper | 9 |
| Integration pipeline | 1 |
| **Total** | **90** |

## Recommendations

1. **Add TY2025 bracket values to self-test** — The existing `__main__` self-test in `tax_computations.py` uses TY2025 brackets but doesn't assert specific expected values. Consider adding assertions.

2. **Monitor OBBBA enactment** — The TY2025 bracket values used in tests assume OBBBA provisions. If legislation changes, update `MFJ_BRACKETS_2025` and `SINGLE_BRACKETS_2025` fixtures in the test file.

3. **Consider adding `generate_tax_pdf.py` field name tests** — The `schedule_d.line_1c_1099b_box_f` → `box_c` rename could break the PDF generator if it references the old field name. When `generate_tax_pdf.py` is created, add a test verifying field name alignment.
