---
name: tax-generate
description: Generate PDF summary of tax return from result.json. Use when the user asks for a PDF, a summary, a filing package, or a printable version of the return, after tax-compute has run.
license: Apache-2.0
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
   - **Cache-vs-taxpayer cross-check — BEFORE any state branching, for ALL
     regimes (mirrors `/tax-compute` Step 10):** `tax_law.state.code` MUST
     equal the taxpayer's validated `taxpayer_profile.json address.state`.
     The render branch below selects state content SOLELY from
     `tax_law.state` — a stale-but-internally-consistent cache left over
     from a different state (e.g. a leftover FL `type: "none"` cache
     sitting beside a CA taxpayer's workspace after an address correction)
     would otherwise render the FL no-income-tax statement (or any other
     state's content) for the wrong taxpayer. If `tax_law.state.code` !=
     `taxpayer_profile.address.state`, HARD STOP: "state cache is for
     {cache_code} but the taxpayer resides in {profile_state} — re-run
     /tax-setup; do not render any state content." Do not render the
     `florida_state` block, the generic no-income-tax sentence, the Form
     540/state-return page, or any other state branch until this check
     passes. This check is excluded from the "proceed anyway?" prompt for
     checks 1-5 below, for the same reason as check 6: an affirmative
     state statement for the wrong reason is worse than no PDF at all.
   - **Cache-year cross-check — BEFORE `_none_type_allowed` or any state
     branch selection (Codex round-13 P2):** `tax_law_{year}.json`'s OWN
     embedded `tax_year` field MUST equal `{year}` (the year this PDF is
     being generated for, i.e. `result.json meta.tax_year`). The
     regime-vs-code check below is itself year-gated (TN's Hall-tax repeal
     boundary), so a cache that's merely FILE-named for the right year but
     whose BODY declares a different `tax_year` can satisfy that check for
     the WRONG year — e.g. a completed TY2020 Tennessee result generated
     after `tax_law_2020.json` gets replaced with a body declaring
     `"tax_year": 2021` and `"type": "none"`: TN's repeal boundary is
     satisfied against the embedded 2021, so the next check would wrongly
     treat "none" as legitimate and this branch would emit a federal-only
     PDF instead of the Hall-tax state content that TY2020 actually owed.
     If `tax_law.tax_year` is missing, malformed, or != `{year}`, HARD
     STOP the same way as the cache-vs-taxpayer mismatch above: "state law
     cache does not declare tax_year {year} (found {found_year}) — re-run
     /tax-setup; do not render any state content." This check is likewise
     excluded from the "proceed anyway?" prompt.
   - **Regime-vs-code validation — BEFORE trusting `state.type == "none"`
     (Codex round-10 P1):** a matching `code` alone does not prove `type`
     is legitimate for it. A CA cache with the correct year and
     `code: "CA"` but a corrupted (hand-edited or fetch-bug) `type: "none"`
     passes the cross-check above and, without this step, would fall
     straight into the no-income-tax branch below — rendering the generic
     "no individual income tax" sentence and OMITTING the Form 540 page
     entirely for a taxpayer who owes CA tax. `/tax-generate` never calls
     `check_freshness` (it reads `tax_law_{year}.json` directly), so verify
     the combination here: `type == "none"` is legitimate ONLY for a code
     in `agents.lib.tax_law_fetcher._NO_INCOME_TAX_STATES` (AK, FL, NV, SD,
     TX, WY, plus TN for `tax_year >= 2021` — same allowlist
     `capabilities._state_note` and `_none_type_allowed` use). If `code` is
     NOT on that allowlist for `tax_year` (e.g. CA, NY, WA, or a
     pre-repeal-year TN) yet `type == "none"`, HARD STOP the same way as
     the cache-vs-taxpayer mismatch above: "state law cache declares
     type=none for {code}, but {code} is not a recognized no-income-tax
     state for {tax_year} — re-run /tax-setup; do not render any state
     content." This check is likewise excluded from the "proceed anyway?"
     prompt.
   - Determine the regime from `tax_law_{year}.json` `state.type`:
     - `"none"` **AND `state.code == "FL"`**: render the affirmative
       statement block from `florida_state` (`no_income_tax_statement` +
       the sales-tax-deduction table when populated) instead of a state
       return page; PDF subject/header is "Federal" only. **Rendering the
       sales-tax block when `sales_tax_deduction.method` is
       `"actual_receipts"`:** `total_line_5a_candidate` is the
       actual-receipts total and the `table_amount`/`local_addition`/
       `big_ticket_tax` components are the LOSING optional-table
       computation kept as comparison evidence — render the total as the
       deduction and LABEL the components as the table comparison; never
       present them as addends of the total (they intentionally do not
       sum to it). When `method` is `"optional_tables"` (or absent on a
       legacy result), the components DO sum to the total and may be
       rendered as its breakdown.
     - `"none"` for any OTHER state code (e.g. TN once its Hall-tax-repeal
       year gate passes, or TX/WY/AK/NV/SD): there is no `florida_state`-
       shaped result.json section for these states — do NOT render the
       `florida_state` block (it would show FL's constitutional citation
       for the wrong state). Instead render the affirmative no-income-tax
       sentence from `meta.limitations` (the capability gate's
       `_state_note`, e.g. "State (TN): no individual income tax — no
       individual income-tax return exists or is required (this state's
       own property/use-tax filings, if any, are not modeled here)..." —
       the state-NEUTRAL caveat; FL form names DR-405/DR-15MO appear ONLY
       in FL's own note (`code == "FL"`), and `/tax-review` treats Florida
       forms inside a non-FL limitation line as a finding (Codex round-22
       P2 — do not copy FL wording into another state's PDF)); PDF
       subject/header is "Federal" only.
     - otherwise: if taxpayer is in an income-tax state, verify the state
       return section is populated; if empty, warn "State return not
       computed — run /tax-compute first?"

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

If any of checks 1-5 fails: print warnings and ask "Proceed with PDF generation anyway, or fix first?" (**Check 6, and THREE checks that open check 4, are hard stops and are never bypassed this way** (Codex round-16 P2 — a later summary line can't quietly override what the detailed bullets above already declared non-bypassable): the cache-vs-taxpayer cross-check, the cache-year cross-check (round 13), and the regime-vs-code validation (round 10) — all three fire BEFORE any state branch is selected and all three are individually marked "excluded from the 'proceed anyway?' prompt" in their own bullets above. Only the rest of check 4's state-CONTENT warnings (missing state return section, etc.) — the parts that run AFTER those three checks pass — follow the "proceed anyway?" prompt.)

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
[ -d .venv ] && source .venv/bin/activate; python3 generate_tax_pdf.py
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
   - Form 1040 (2025+): QBI `line_13a_qbi_deduction` prints as **line 13a**
     and `line_13b_schedule_1a_deductions` (OBBBA Schedule 1-A total —
     tips/overtime/car-loan/senior) as **line 13b** — the 2025 form splits
     line 13 into 13a/13b (pre-2025 returns had a single "Line 13" for QBI;
     label per the return year). When 13b is $0, print QBI as 13a and omit the
     13b row. The `schedule_1a` section carries the per-provision detail.
     For TY2026+, `line_13c_charitable_non_itemizer` (OBBBA §170(p) below-AGI
     charitable deduction for standard-deduction filers) prints as **line 13c**
     when nonzero (omit the row when $0); the `charitable_non_itemizer` section
     carries the detail.
     Line 14 = line 12 + line 13a + line 13b + line 13c — spot-check the sum.
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
