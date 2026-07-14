---
name: tax-generate
description: Generate PDF summary of tax return from result.json
---

# /tax-generate

Generate a formatted PDF summary of the computed tax return.

## Usage
```
/tax-generate                   # summary PDF
/tax-generate --filing-package  # + the F1 filing package (see below)
```

## Prerequisites
- `result.json` must exist with computed values (run `/tax-compute` first)
- `generate_tax_pdf.py` must exist in the workspace
- Python package: reportlab

## Steps

### 1. Verify Prerequisites

Check that `result.json` exists and has non-zero values in key fields:
- `form_1040.line_9_total_income` > 0
- `form_1040.line_15_taxable_income` > 0
- `balance_due_summary` exists

If result.json looks empty/template-like, warn the user and suggest running `/tax-compute` first.

### 1.5. Pre-Generation Completeness Check

Before generating the PDF, verify result.json internal consistency:

1. **Income sum check:**
   - Verify `line_9_total_income` = `line_1z_total_wages` + 2b + 3b + 4b + 5b + `line_6b_taxable_ss` + 7 + 8
   - Note: `line_1z_total_wages` = `line_1a_wages` when no OBBBA sub-line adjustments apply; `line_6b_taxable_ss` may be $0
   - If mismatch > $1: warn and show expected vs actual

2. **Tax computation check:**
   - Verify `line_16_tax` is reasonable for `line_15_taxable_income` (within expected bracket range)
   - Verify `line_24_total_tax` = line_16 - credits + other taxes

3. **Payments check:**
   - Verify `line_25d_total_withholding` = sum of withholding components
   - Verify refund/owed = total payments - total tax
   - If `line_38_estimated_tax_penalty` > 0: show it as its own line in the
     summary ("Estimated-tax penalty (Form 2210)"). On a balance-due return
     the payment amount includes it; on an overpayment return verify
     `line_35a_refund` = line 34 − line 38 (an unreduced refund alongside a
     nonzero line 38 is a mismatch — warn). If the `form_2210` section says
     a penalty applies but `penalty_computed` is false, print the
     rates-unfetched note instead of silently showing $0

4. **State return check:**
   - If taxpayer is in an income-tax state: verify state return section is populated
   - If state return is empty: warn "State return not computed — run /tax-compute first?"

5. **Missing sections check:**
   - If `has_self_employment=true` in profile but no Schedule C in result.json: warn
   - If `has_hsa=true` but no Form 8889 section: warn
   - If `has_capital_gains=true` but no Schedule D: warn

6. **Limitations manifest check (C2) — HARD STOP, not a warning:**
   - `meta.limitations` must be a non-empty list (it always carries at least
     the state-scope note after `/tax-compute` Step 12). If it is empty or
     missing: **STOP — do not generate.** Re-run `/tax-compute` Step 12 (or the
     capability gate directly: `python3 agents/lib/capabilities.py --workspace
     . --json`) to populate it first. This check is deliberately EXCLUDED from
     the "proceed anyway?" prompt below: a PDF without the page-1 scope box
     presents itself as complete coverage, which is the exact failure C2 exists
     to prevent.

If any of checks 1-5 fails: print warnings and ask "Proceed with PDF generation anyway, or fix first?" (Check 6 is a hard stop and is never bypassed this way.)

### 2. Check PDF Generator

If `generate_tax_pdf.py` does not exist, create one that:
- Reads `result.json`
- Uses reportlab to produce a multi-page PDF
- **Renders a "Scope & Limitations" box on PAGE 1** from `meta.limitations` —
  every line, verbatim, before any dollar figures. The taxpayer must see what
  this return does NOT cover (acknowledged gaps, manual worksheets, state
  scope) with the same prominence as the results themselves.
- Includes all sections present in result.json
- Skips sections that are absent or have `_include_if` markers
- Follows the same format as the existing generator (if one exists)

If `generate_tax_pdf.py` already exists, verify it references all current sections in result.json.
If result.json has new sections the PDF generator doesn't handle, update the generator.
**Specifically check that the existing generator renders `meta.limitations` as the
page-1 "Scope & Limitations" box** — generators created before the C2 manifest
existed don't, and reusing one silently drops the box even though the
completeness check passed. If it doesn't, update it before generating.

### 3. Generate PDF

Run:
```bash
python3 generate_tax_pdf.py
```

### 4. Verify Output

Check that the output PDF was created and set restrictive permissions:
```bash
ls -la Tax_Return_*_Summary.pdf
chmod 600 Tax_Return_*_Summary.pdf
```

Report:
- Output file path
- File size
- Page count (use pdfplumber to count pages if desired)

### 4.5. Filing package mode (`--filing-package`, F1)

When invoked with `--filing-package`, ALSO produce
`Tax_Return_{year}_Filing_Package.pdf` (chmod 600). **This is NOT an
e-file artifact** — no MeF submission, no Form 8879; say so on page 1 of
the package in the same banner style as the Scope & Limitations box. The
package has four parts:

1. **Assembly order + attachment checklist** — computed, never
   hand-enumerated:
   ```bash
   python3 -m agents.lib.filing_package --result result.json --workspace .
   ```
   Render its table (Form 1040 first, then every present form by its
   official Attachment Sequence No., state forms last) and its checklist
   (signature, W-2 Copy B, withholding-only 1099 rule, Form 8949/1040-V/
   CA-540/FBAR conditionals) verbatim into the PDF. Spot-check the
   sequence numbers against the filing year's official forms (they are
   printed in each form's top-right corner and can change).
2. **Line-numbered form pages** — for each section in the assembly order,
   print the section's fields labeled with the OFFICIAL line numbers (the
   result.json field names encode them: `line_9_total_income` → "Line 9").
   Order lines numerically per form. Fields with no `line_` prefix print
   under a "supporting detail" subheading after the numbered lines.
   Schedules 1/2/3/B/SE/8812 appear in the assembly order as DERIVED rows
   (the module infers them from computed fields) — print their lines from
   the source fields with a note naming the schedule and line (Schedule 2
   line 1 AMT, Schedule 3 line 3 education credits, Schedule 3 line 11
   excess SS, Schedule B Parts I/II from interest_detail/dividend_detail,
   Schedule SE from schedule_c.se_tax, Schedule 8812 from line_19_ctc/
   line 28).
   **Field-name ≠ official-line caveats (print ON the affected page — a
   filer transcribing by our labels would put figures on the wrong
   official lines):**
   - Schedule D: `line_1c_1099b_box_c` is NOT official line 1c (Box C
     rows go on line 3); `line_11_1099b`/`line_12_not_reported` do NOT
     match official lines 11/12 (those are 4797/K-1 lines) — label these
     rows "internal name — place per the Schedule D instructions".
   - Form 8889: `line_14c_qualified_medical` and
     `line_15_taxable_distributions` don't match official 14c/15
     (official 15 = unreimbursed qualified medical; taxable = 16).
   - Form 1040: `line_13a_qbi_deduction` prints as "Line 13" (no 13a on
     the current form).
   - California 540: the section has NO `line_` prefixes — render it as a
     values table WITHOUT line labels and point to the 540 instructions
     for line placement.
3. **Transfer mapping** — one table: each package form → where it is
   entered in tax software (generic area names: "Income → Wages (W-2)",
   "Deductions & Credits → Education", "Other Tax Situations → AMT"...).
   Do NOT name a specific vendor's menu paths as authoritative — label the
   column "typical software section".
4. **Sign-and-file page** — paper-filing steps (sign/date both spouses,
   assemble in sequence order, mail to the address for the taxpayer's
   state + payment/no-payment variant — instruct the user to CONFIRM the
   current IRS mailing address on irs.gov, addresses change). **The CA
   return is a SEPARATE envelope to the FTB** (payment vs refund PO boxes
   differ — confirm on ftb.ca.gov), with the checklist's federal-copy and
   payment mechanics. E-file alternative (Free File / software using the
   transfer mapping), and the FBAR reminder exactly as the checklist
   words it (three-way: files-separately / determination-PENDING /
   no-signals — never a categorical all-clear).

The summary PDF from step 3 is unchanged — the package is an ADDITIONAL
artifact.

### 5. Summary

Print:
- PDF generated successfully at {path} (+ the filing package path when
  `--filing-package` was used)
- {N} pages
- Sections included: {list of forms/sections in the PDF}
- Suggested next step: `/tax-verify <software-return.pdf>` to cross-check against tax software
