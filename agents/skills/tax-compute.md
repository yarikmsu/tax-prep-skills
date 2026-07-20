---
name: tax-compute
description: Compute all tax form values from parsed data — federal 1040 + state return
---

# /tax-compute

Compute all tax form line values from parsed source data.

## Usage
```
/tax-compute            # Compute all forms
/tax-compute --refresh  # Re-fetch tax law data before computing
```

## Prerequisites
- `taxpayer_profile.json` must exist
- `result.json` must have parsed income data (run `/tax-parse` first)
- `tax_law_{year}.json` must exist (run `/tax-setup` first)
- `summaries/*.md` should exist with detailed parsed data

## Computation Order

Order matters — each step depends on previous results.

### Step 0.5: Tax Law Freshness Verification

Before any computation, verify tax law data is current:

1. **Validate** `{year}` is a 4-digit integer (2000–2099) before using in any command.
2. Run: `python3 agents/lib/tax_law_fetcher.py freshness {year}`
2. Act on the result:
   - **FRESH:** Proceed. Print the key rates being used (standard deduction, CTC amount, top bracket) so user can eyeball them.
   - **STALE:** Use **WebSearch** to check for new legislation since the fetch date (`"federal tax law changes {year} since {fetch_date}"`). If new law found, re-fetch affected rates. If no changes, proceed.
   - **INCOMPLETE:** Re-fetch the missing fields listed in `null_fields` via WebSearch, then update `tax_law_{year}.json`.
   - **NOT_FOUND:** **STOP.** Tell user to run `/tax-setup` first. Do not proceed.
3. Print the rates being used:
   ```
   Tax Law Verification — TY{year}
   Status: {status} (fetched {age_days} days ago)
   MFJ standard deduction: ${amount}
   Child tax credit: ${amount}/child
   Top bracket: {rate}% on income over ${threshold}
   ```

### Step 0.7: Capability Gate (scope guard)

Before loading any data, verify the engine can actually compute this taxpayer's
situation — the toolkit must never produce a plausible-looking return that
silently omits a form it cannot model:

1. Run: `python3 agents/lib/capabilities.py --workspace .`
2. Act on the exit code:
   - **0:** Proceed. The printed limitations manifest is reused in Step 12.
   - **2 (BLOCKED):** **STOP.** Show the user the blocked situations and the
     two options verbatim from the tool output. Do NOT compute past this point
     unless the user chooses one:
     a. Prepare this return in tax software / with a professional — the toolkit
        stays useful as a checker (`/tax-verify`) and planner; or
     b. The user EXPLICITLY accepts proceeding without the named form(s). Then:
        (1) record the decision in `answers.md` (date, rule id, form, one-line
        user rationale); (2) add the rule id(s) to `acknowledged_limitations`
        in `taxpayer_profile.json`; (3) re-run the gate and confirm exit 0.
   - **1 (error):** Fix the reported profile problem (non-bool flag, unknown
     acknowledged id, malformed state). Never bypass the gate on an error.
3. NEVER add ids to `acknowledged_limitations` without the user's explicit
   confirmation in this conversation — the acknowledgment is the taxpayer's
   decision, not a convenience.

### Step 1: Load All Input Data

Read these files:
1. `taxpayer_profile.json` — filing status, dependents, flags
2. `tax_law_{year}.json` — brackets, rates, thresholds, limits
   - If missing or `--refresh` flag: re-fetch via WebSearch (see `/tax-setup` step 4)
3. `result.json` — current parsed data
4. `answers.md` — resolved questions
5. All `summaries/*.md` — detailed parsed data

### Step 1.5: Pre-Computation Confidence Check

Before computing, identify assumptions that could change the tax outcome by >$100. **First check `answers.md` "Confirmed Assumptions" and "Resolved Questions" — skip any question already answered there.** Use **AskUserQuestion** in batches (max 5 questions per call) for remaining unknowns. Classify each as:
- **HIGH** (>$500 impact): MUST ask before proceeding
- **MEDIUM** ($100–$500): Ask with "Use default / Skip" option
- **LOW** (<$100): State default assumption, proceed

**Batch 1 — IRA & Retirement** (if `has_ira_contributions` or `has_employer_retirement_plan`):
- Traditional IRA or Roth IRA? (affects deductibility — HIGH)
- Is spouse covered by an employer retirement plan? (changes phaseout range — HIGH)
- Any pre-tax Traditional IRA balance at year-end? (pro-rata trap for backdoor Roth — HIGH)

**Batch 2 — Schedule C** (if `has_self_employment`):
- Is this a new business (started this year)? (startup cost deduction — MEDIUM)
- Home office used regularly and exclusively for business? (MEDIUM)
- Business vehicle miles driven? (MEDIUM)
- Any unreported business expenses not in documents? (HIGH if significant)

**Batch 3 — Credits eligibility:**
- CTC: Confirm each child's age and residency for the full year (HIGH — `child_tax_credit.amount_per_child` from tax_law JSON)
- Dependent care expenses amount? (HIGH if >$0 — verify current credit limits via WebSearch if legislation changed)
- Education credits: Any qualifying tuition/fees paid? (HIGH — AOTC up to $2,500)
- Saver's credit: Does income qualify? (MEDIUM)

**Batch 4 — Carryforwards & prior year:**
- Confirm capital loss carryforward amount from prior year return (HIGH if >$0)
- QBI loss carryforward? (MEDIUM)
- Estimated tax payments: confirm amounts and dates (HIGH — affects refund directly)

**Batch 5 — State-specific** (based on taxpayer's state):
- CA: Confirm HSA employer contribution amount for add-back (HIGH for CA return)
- Any state-specific credits claimed? (Renter's credit, etc.) (MEDIUM)

**Gating rule:** Do NOT proceed past Step 2 if any HIGH-impact questions are unresolved. Log all confirmed answers and defaults to `answers.md` under "Confirmed Assumptions".

### Step 2: Determine Applicable Forms

Based on `taxpayer_profile.json` flags:

| Flag | Forms Triggered |
|------|----------------|
| Everyone | Form 1040 |
| interest + dividends > $1,500 | Schedule B |
| has_self_employment | Schedule C, Schedule SE |
| has_capital_gains | Schedule D, Form 8949 |
| has_hsa | Form 8889 |
| has_foreign_tax_paid | Form 1116 (or simplified if ≤ $600 MFJ) |
| has_foreign_accounts | Form 8938 threshold check, FBAR flag |
| dependents with CTC | CTC worksheet (Schedule 8812) |
| has_ira_contributions | Form 8606 / Schedule 1 line 20 |
| has_1099r | Form 1040 lines 4a/4b or 5a/5b |
| Medicare wages > $200K | Form 8959 (Additional Medicare Tax) |
| QBI or REIT dividends | Form 8995 |

### Step 3: Compute Income (Form 1040 Lines 1-9)

```
Line 1a: Total wages from all W-2s
Line 2b: Total taxable interest (all 1099-INTs + foreign interest)
Line 3a: Qualified dividends (sum of 1099-DIV Box 1b)
Line 3b: Ordinary dividends (sum of 1099-DIV Box 1a)
Line 4a/4b: IRA distributions (if applicable)
Line 5a/5b: Pensions (1099-R; Code G rollover = 5a only, 5b = $0)
Line 6a/6b: Social Security benefits (6a = SSA-1099 box 5 total;
            6b = taxable amount from compute_taxable_ss below)
Line 7: Capital gain/loss from Schedule D line 16
Line 8: Other income from Schedule 1 (Schedule C net, etc.)
Line 9: TOTAL INCOME = 1z + 2b + 3b + 4b + 5b + 6b + 7 + 8
        (6b — the TAXABLE benefit amount — is IN the sum; omitting it
        understates AGI by exactly what the worksheet just computed)
```

**Also record `schedule_e_k1_income` (L2):** write the GROSS POSITIVE
partnership / S-corp K-1 distributive income the return reflects to
`form_1040.schedule_e_k1_income` — the PTET-ELIGIBLE pass-through-entity income
ONLY. EXCLUDE Schedule E rental real estate and royalty income (not a
qualifying PTET entity), and do NOT net against other entities' K-1 LOSSES: the
PTET base is per-entity, so a $200K profitable CA K-1 alongside a separate
$100K loss is a real $200K base — record $200K, not the $100K net. Sum the
positive qualifying K-1 shares. `/tax-ptet`'s phantom-income guard compares the
user's supplied `--income` against this figure so a large unrelated line-8 item
can't mask a K-1 that isn't in the return, while a legitimate per-entity claim
isn't blocked by an unrelated Schedule E loss. Leave it 0 when there is no
qualifying K-1 income.

**Taxable Social Security (line 6b):** When any SSA-1099 exists, use
`compute_taxable_ss()` from `agents/lib/tax_computations.py` — never hand-run
the worksheet:
- `ss_benefits` = SSA-1099 box 5, both spouses combined on a joint return
- `other_income` = all other 1040 income (lines 1z, 2b, 3b, 4b, 5b, 7, 8)
- `tax_exempt_interest` = line 2a — REQUIRED input: munis raise provisional
  income even though untaxed (the classic surprise)
- `adjustments` = Schedule 1 above-the-line adjustments
- `excluded_income_addbacks` = Pub 915 line-5-region add-backs that re-enter
  provisional income: Form 2555 FEIE/housing exclusions, excluded adoption
  benefits (8839), **Form 8815 excluded savings-bond interest** (the worksheet
  uses PRE-exclusion Schedule B line 2, not the post-exclusion 1040 line 2b —
  pass the excluded portion here), excluded territory income — REQUIRED for
  `lives_abroad` taxpayers and Form 8815 filers; omitting them understates
  line 6b
- `filing_status` is REQUIRED (no default) and MFS filers MUST pass
  `lived_apart_from_spouse_all_year` (hard error otherwise) — it selects
  between the Single-style worksheet and the $0-base 85% path.
- **ORDERING:** compute the Schedule 1 above-the-line adjustments (Step 4's
  line 10 inputs — ½ SE tax, deductible IRA/HSA, student-loan interest, etc.)
  BEFORE this worksheet and pass them as `adjustments` — leaving the default 0
  because "Step 4 comes later" overstates provisional income and line 6b. If a
  deduction itself depends on MAGI-including-6b (the deductible-IRA phase-out),
  use the Pub 590-A Appendix B combined worksheet rather than iterating by hand.
- **Lump-sum check:** if any SSA-1099/RRB-1099 shows amounts paid **for an
  earlier year**, pass them as `lump_sum_prior_year_benefits` and surface the
  returned `warning`: the Pub 915 lump-sum election (Worksheets 2-4, line 6c)
  can only LOWER taxable benefits and is NOT modeled — the computed line 6b is
  an upper bound the user should verify in tax software before filing.

**Capital loss carryforward (Schedule D):** When line 16 is a net LOSS, cap the
line 7 deduction at $3,000 ($1,500 MFS) and fill
`schedule_d.loss_carryforward_to_next_year` (short_term / long_term split) in
result.json using the Capital Loss Carryover Worksheet from the Schedule D
instructions — short-term loss is used first, and the amount actually used can
be LESS than $3,000 when taxable income is low. `/tax-rollover` carries this
explicit block into next year's `prior_year.capital_loss_carryforward`; leaving
it zero silently loses the carryforward.

**Employer-stock basis adjustment — Form 8949 code B (J1):** Brokers report the
1099-B cost basis for RSU/ESPP/NQSO shares EXCLUDING the compensation already in
W-2 box 1 (for RSUs, typically $0 basis). Reporting that basis verbatim
DOUBLE-TAXES the W-2 income. Each `schedule_d` box detail row carries a signed
`adjustment` (Form 8949 column (g), code B in column (f)); leave `cost` at the
BROKER-reported figure (column (e), so /tax-verify and software transfer match
the 1099-B) and put the correction in `adjustment`. **gain = proceeds − cost +
adjustment.** The adjustment is NEGATIVE (raises basis, reduces gain). Set the
row's `adjustment_code` to **"B"** AND set **`employer_stock` to true** — the
tie-out scopes the employer-stock reconciliation on the `employer_stock`
provenance marker, NOT the code (Form 8949 code B is also used for ordinary
non-employer 1099-B basis corrections, so the boolean is what keeps an
unrelated code-B correction on another lot from corrupting the total).

- **Report the adjusted lot on Form 8949.** A covered Box A/D transaction that
  carries ANY adjustment loses the direct-to-Schedule-D shortcut (that shortcut
  is only for adjustment-free Box A/D lots) — it MUST appear on Form 8949 (or an
  allowed substitute statement). Populate the `form_8949.part_i_short_term` /
  `part_ii_long_term` arrays for the lot IN ADDITION to the `schedule_d` box
  row, so the filing package and /tax-verify carry the required 8949 entry.
- **Apply ONLY from a documented source** — the broker supplemental
  ("adjusted cost basis") statement, Form 3922/3921, or a vest confirmation
  carrying the per-share compensation. **NEVER infer an adjustment** — an
  invented adjustment understates tax (the unsafe direction). No source
  document → NO adjustment → set a loud `possible_double_taxation` warning on
  the row instead (overstates tax but is visible; the /tax-review $0-basis
  check escalates it). Record each applied adjustment in answers.md: source
  doc, per-lot math, no aggregate invention.
- **Character/term and box classification are UNCHANGED by the adjustment** —
  an RSU lot's holding period still runs from the vest date, and the adjustment
  never moves a lot between 8949 boxes (foreign brokers stay Box B/E).
- **Sanity bounds:** the total adjustment for a grant type must NOT exceed the
  W-2-included equity compensation the user attests (ask once; if unknown,
  warn rather than block). Per lot, an adjustment that would drive corrected
  basis below $0 (adjustment magnitude > proceeds on a $0-basis lot is fine,
  but corrected basis can't go negative) is arithmetically impossible → hard
  error, re-examine the source figures.
- The tie-out (`employer_stock_basis_adjustment` category) sums these
  `adjustment` fields and FAILS the hard gate if a ledgered supplemental
  statement's correction never landed here — so a dropped adjustment cannot
  pass review.

**ESPP §423 disposition split (J2):** For ESPP sales, use
`compute_espp_disposition()` from `agents/lib/tax_computations.py` with the Form
3922 boxes (3 = grant FMV/sh, 4 = purchase FMV/sh, 5 = price paid/sh, 8 =
shares) + the sale proceeds and the grant/purchase/sale dates. It returns the
`ordinary_income` component, the corrected `capital_basis`, the
`capital_gain`, and the `term`:
- **The ordinary component must land in income exactly once — for BOTH
  disposition types.** The flow raises the capital basis by the ordinary amount
  (removing it from the capital gain), so if it isn't also added to wages it
  disappears from taxable income entirely (understates tax — the unsafe
  direction). Route it to **1040 line 1h** (other earned income) UNLESS it is
  documented as already included in W-2 box 1; record the routing/no-double-add
  decision in answers.md.
  - **DISQUALIFYING:** the employer is SUPPOSED to include it in W-2 box 1, but
    a FORMER employer often omits it → line 1h is the default. If box 1 already
    includes it, do NOT double-add — ASK, never assume.
  - **QUALIFYING:** the employer generally does NOT report it (they usually
    don't know you sold) → it almost always needs line 1h. Same
    already-in-W-2 check before adding, but the default is that it is NOT there.
- The corrected `capital_basis` = price paid + ordinary income; when the broker
  reported price-paid-only basis, the code-B `adjustment` for that lot is
  (broker basis − corrected basis) — feed it through the code-B flow above so
  the ordinary component isn't taxed twice.

**Form 8606 — Nondeductible IRA basis & Roth conversions (lines 4a/4b):**

Compute this BEFORE finalizing line 4b whenever the taxpayer has any of: a Roth
conversion (`has_roth_conversion`/`has_backdoor_roth`), a traditional-IRA distribution
with after-tax basis, a nondeductible contribution this year, or a prior-year basis
(`taxpayer_profile.json prior_year.ira_basis > 0`).

Use `compute_form_8606()` from `agents/lib/tax_computations.py`. Gather:
- `nondeductible_contrib_current` — this year's nondeductible traditional-IRA contribution.
- `prior_basis` — `taxpayer_profile.json prior_year.ira_basis` (rolled forward by `/tax-rollover`).
- `year_end_value` — **Dec 31 FMV of ALL traditional/SEP/SIMPLE IRAs** (ask the taxpayer; this
  is the pro-rata denominator — without it you cannot apply the rule correctly).
- `distributions_not_converted` — the **Form 8606 line 7** amount: traditional/SEP/SIMPLE
  IRA distributions taken during the year, but **EXCLUDING** anything line 7 leaves out —
  Roth conversions (those go in `amount_converted`), qualified charitable distributions
  (QCDs), a one-time HSA funding distribution, rollovers, returned contributions, and
  recharacterizations. Feeding any of those in would overstate the taxable amount and wrongly
  consume basis. If none apply, this is just the ordinary 1099-R distributions not converted.
- `disaster_repayable_amount` (optional, default 0) — the **Form 8606 line 15b** amount,
  which per the IRS instruction is "the amount on line 15a that is allocable to qualified
  disaster distributions reported on Form 8915-F." This is the **post-pro-rata TAXABLE
  portion** of the disaster distribution — NOT the gross disaster amount from line 7. The
  difference matters: pro-rata strips out the basis-recovery share before any of it gets
  allocated to line 15b, so passing the gross would over-state line 15b and under-state
  the 1040 line 4b amount.
  - **All of line 7 is disaster** (common case): line 15b equals `line_15a_taxable_pre_disaster`
    from the keystone output — run the keystone once with `disaster_repayable_amount=0` to
    read line 15a, then call again passing that value.
  - **Mixed disaster + ordinary distributions**: see Form 8915-F instructions for the
    allocation; in general it is `line_15a × (disaster_gross / line_7)`.
  Pass this when the taxpayer is amortizing a disaster distribution over 3 years so the
  keystone routes only the non-disaster taxable portion (line 15c) to 1040 line 4b. The
  toolkit does NOT model the Form 8915-F amortization itself — that schedule is the
  taxpayer's responsibility / a separate form. Omit (0) for typical returns.
- `amount_converted` — total converted to Roth (1099-R Code 2/7 to a Roth).

The pro-rata rule blends after-tax basis across every dollar — you cannot convert "just the
basis." Write the returned lines to `result.json form_8606`, then route:
- `taxable_to_1040` → Form 1040 **line 4b** (taxable IRA amount), with gross on line 4a.
- `basis_remaining` (line 14) stays in `form_8606` — `/tax-rollover` carries it to next year.

⚠️ **Backdoor-Roth pro-rata trap:** if `pro_rata_triggered` is true (basis AND pre-tax money
both present at a conversion), warn the taxpayer — their conversion is only *partly* tax-free,
not fully. A clean backdoor (no other pre-tax traditional IRA) converts tax-free (ratio 1.0).

### Step 4: Compute AGI (Form 1040 Lines 10-11)

```
Line 10: Adjustments to income from Schedule 1 Part II:
  - IRA deduction (check eligibility based on employer plan, MAGI, filing status)
  - SE tax deduction (half of SE tax, if Schedule C profit)
  - HSA deduction (personal contributions beyond employer)
  - Student loan interest (if applicable)
Line 11: AGI = Line 9 - Line 10
```

⚠️ **OBBBA tips / overtime / car-loan / senior are NOT here.** They are
**Schedule 1-A deductions taken BELOW AGI** (Form 1040 line 13b), computed in
Step 5 — NOT Schedule 1 Part II adjustments. Putting them in line 10 wrongly
cuts AGI and corrupts every MAGI-gated computation downstream (IRA phase-out,
taxable SS, NIIT, education credits, ACA PTC). Do not touch `line_10_adjustments`
for them.

**IRA deduction eligibility check:**
- Use phaseout ranges from `tax_law_{year}.json`
- If spouse not covered by employer plan: use higher phaseout
- MAGI = AGI before IRA deduction (circular — use iterative or direct calc)

### Step 5: Compute Taxable Income (Form 1040 Lines 12-15)

```
Line 12: Standard deduction (from tax_law JSON = the §63(c) BASE for the
         filing status only). Two separate add-ons are NOT in that cached
         figure and must be handled explicitly:
           • §63(f) age-65/blind ADDITIONAL standard deduction — an extra
             ~$1,600 (single/HoH) / ~$1,550 per box (MFJ/MFS/QSS), one box per
             65+ AND one per blind, for filers taking the STANDARD deduction.
             Fetch the per-box amount and add it here for each checked box
             (age flags come from the interview; ask about blindness). Omitting
             it overstates line 12→ taxable income for a 65+/blind filer.
           • OBBBA §70103 enhanced senior deduction — a SEPARATE Schedule 1-A
             item on line 13b (below), available to itemizers too; NOT part of
             line 12. (v1 does not yet cache the §63(f) per-box amount — flag
             it if unfetched rather than silently dropping it.)
         OR itemized deductions (Schedule A) — election COMPUTED below
Line 13a: QBI deduction (Form 8995)
Line 13b: Schedule 1-A deductions (compute_schedule_1a — below)
Line 13c: §170(p) non-itemizer charitable deduction (compute_charitable_non_itemizer — below; TY2026+, STANDARD-deduction filers only)
Line 14: Total deductions = Line 12 + Line 13a + Line 13b + Line 13c
Line 15: Taxable income = max(0, Line 11 - Line 14)
```

**Schedule 1-A (OBBBA tips / overtime / car-loan / senior) — run
`compute_schedule_1a()` whenever any of those items exist (TY2025-2028).**
BELOW-AGI deductions; they never touch line 10/11.
- Inputs: `filing_status`, `tax_year`, `magi` (= AGI + §911/§931/§933
  exclusions — reuse the FEIE add-back convention; MAGI≈AGI when
  `lives_abroad` is false), `limits` = `federal.schedule_1a` from tax_law
  JSON, plus the raw per-provision amounts and the two `age_65` booleans.
- **Overtime is the FLSA premium portion ONLY** (the "half" of
  time-and-a-half), not gross overtime pay — pass the premium, and surface
  the function's warning note.
- **SSTB tips are excluded for EVERYONE** — tips earned in a specified service
  trade or business (§199A(d)(2)) don't qualify whether the earner is a W-2
  employee OF the SSTB or self-employed IN it. Screen SSTB out of
  `qualified_tips` for both sources before calling (v1 doesn't model it).
  **Transition-relief exception:** a worker in an occupation that CUSTOMARILY
  and regularly received tips before 2025 is treated as NON-SSTB under the
  final Schedule 1-A instructions' transition relief (until final regs) — do
  NOT strip those tips even if the business is otherwise an SSTB.
- **Unreported / employer-allocated tips (Form 4137) — MANUAL, CONDITIONAL:**
  those amounts belong on 1040 line 1c (wages) and Schedule 2 line 5 (employee
  SS/Medicare tax) via Form 4137, which v1 does not compute. They ARE qualified
  tips (2025 Schedule 1-A Part II has a Form 4137 line), so do not lose the
  deduction: include them in `qualified_tips` ONLY ONCE the paired Form 4137
  income + FICA have been entered by hand. While that paired treatment is NOT
  done, keep them OUT of `qualified_tips` (else the deduction is claimed while
  the income + payroll tax are dropped). The capability gate flags this as a
  manual item either way.
- **Self-employed tips:** additionally, `qualified_tips` from a Schedule C /
  1099 trade or business must already be capped at that business's NET PROFIT
  **reduced by the allocable ½-SE-tax, SE-retirement, and SE-health-insurance
  deductions** (Schedule 1-A line 5 per the final instructions) — v1 does NOT
  model the cap, so a low/negative-profit tipped business would otherwise
  overstate the deduction. Surface the function's caveat.
- The `total` goes to `form_1040.line_13b_schedule_1a_deductions` and the
  `schedule_1a` result section. **AGI (line 11) is unchanged** — verify the
  IRA phase-out, taxable SS, NIIT, and education-credit MAGI you already
  computed do not move.
- **QBI base:** compute Schedule 1-A BEFORE the Form 8995 overall cap and
  pass `taxable_income_before_qbi` = line 11 − line 12 − line 13b to
  `compute_qbi_deduction` — Schedule 1-A shrinks the QBI cap base.
- **AMT:** pass the `senior_deduction` (= `schedule_1a` senior allowed) to
  `compute_form_6251` in Step 6.5 — the §70103 senior deduction is a
  Schedule 1-A line-37 AMT add-back. Tips/overtime/car-loan are NOT added
  back for AMT in v1 (verify against the final 6251 instructions).
- MFS gets $0 for ALL FOUR Schedule 1-A provisions — tips, overtime, senior,
  AND car-loan interest. The OBBBA §70203 car-loan deduction is unavailable to
  a married taxpayer who files separately (verified vs IRS/H&R Block/Federal
  Register guidance), same as the joint-filing bar on tips/overtime/senior; the
  function zeros them and emits the disallowed notes — do not re-add car-loan.
- **Valid-SSN eligibility (screen PER RECIPIENT, not return-wide):** tips,
  overtime, and the senior deduction require the PERSON who received the
  tips/overtime — or each 65+ person claiming the senior amount — to have a
  valid SSN; an ITIN holder does NOT qualify. Apply the test per recipient:
  on a joint return where only one spouse received the tips/overtime (or only
  one 65+ spouse has a valid SSN), the valid-SSN spouse's amount STILL flows
  to line 13b — do NOT zero the whole deduction because the OTHER spouse
  holds an ITIN. The engine takes amounts + age booleans only, so screen SSN
  eligibility BEFORE calling and pass $0 (or omit that person's age flag) for
  the non-SSN recipient ONLY; the function caveats this.

**§170(p) non-itemizer charitable deduction (OBBBA, TY2026+) — run
`compute_charitable_non_itemizer()` when the filer takes the STANDARD deduction
and made cash charitable gifts:**
- Inputs: `cash_contributions` (CASH gifts only — NOT donor-advised-fund or
  §509(a)(3) supporting-org gifts, and NOT noncash; confirm/attest),
  `filing_status`, `tax_year`, `itemizes` (the computed election result — pass
  `True` and it returns $0, since itemizers claim charitable on Schedule A),
  `over_250_substantiated` (the same tri-state as Schedule A), and
  `amount`/`joint_amount` from `federal.charitable_non_itemizer`.
- The `allowed` goes to `form_1040.line_13c_charitable_non_itemizer` and the
  `charitable_non_itemizer` result section. It is BELOW AGI (in line 14, NOT
  line 10) — verify AGI and every MAGI-gated computation are unchanged, exactly
  like line 13b.
- **QBI base:** line 13c ALSO reduces the Form 8995 taxable-income-before-QBI
  base (it comes before QBI in the deduction stack) — see the QBI section.
- CHICKEN-AND-EGG with the election: compute the itemize-vs-standard election
  FIRST (below), then call this with the resolved `itemizes`. But the election
  comparison itself must add §170(p) to the STANDARD side (below) — so compute
  the §170(p) amount provisionally (as if standard) to run the comparison, then
  finalize with the real `itemizes`.
- Copy `notes` verbatim (DAF exclusion, no-0.5%-floor, no-carryforward,
  substantiation). $0 and a not-in-effect note before TY2026.

**Itemized vs standard (Schedule A) — run `compute_schedule_a()` whenever ANY
itemizable expense exists** (state withholding alone qualifies — for the CA
persona SALT usually exceeds the OBBBA cap by itself):
- Inputs: AGI (line 11), filing status, tax year, the filing status's
  standard deduction, and `salt_cap` = `federal.salt_cap.amount`
  (`mfs_amount` for MFS) from tax_law JSON.
- `state_local_income_taxes` = W-2 box 17 (all states) + CA SDI (box 14) +
  state estimated payments PAID during the year + prior-year state balance
  paid this year. Ask the user for the last two — they're not on any W-2.
- Mortgage: 1098 box 1 + deductible points; ask for the average balance and
  whether the loan predates Dec 16 2017 (the $1M grandfather). If the
  balance is unknown the function takes the interest in FULL and notes the
  overstatement risk — surface that note.
- Charitable: ask for cash vs noncash split and whether every gift ≥ $250
  has a written acknowledgment; record the answer in answers.md. Surface
  the carryforward figure for /tax-rollover if nonzero.
- MFS: ask whether the spouse itemizes (`mfs_spouse_itemizes` forces
  itemizing per §63(c)(6)(A)).
- **TY2026+ §70111 haircut:** the OBBBA top-bracket reduction (2/37 of the
  lesser of itemized total or 37%-bracket income) is circular with the
  deduction — compute taxable income PROVISIONALLY with the un-haircut
  itemized total; if any of it falls in the 37% bracket, re-call
  `compute_schedule_a` with `top_bracket_income` set to that slice and use
  the re-called result. Skipping this on a top-bracket return overstates
  line 12 (the function emits a loud note when the input is absent).
- Use `use_itemized` for line 12; write the result into the `schedule_a`
  section when itemizing (or when `election_margin` is within $500 —
  document the close call). Copy `notes` verbatim, and surface
  `charitable_carryforward` for next year.
- **§170(p) tilts the election (TY2026+) — but only when the election is NOT
  mandatory.** The comparison is NOT (itemized total) vs (standard deduction) —
  a standard-deduction donor ALSO gets the §170(p) line-13c deduction, so
  compare `itemized_total` vs `standard_deduction + §170(p) allowed`. A donor
  near the margin who would itemize on the bare numbers can come out ahead
  taking the standard deduction once §170(p) is added. `compute_schedule_a`'s
  election does not know about §170(p) — do this tilt yourself, then set
  `itemizes` accordingly before finalizing line 13c. **EXCEPTION — mandatory
  itemization:** when `mfs_spouse_itemizes` is set, §63(c)(6)(A) DENIES the
  standard deduction to this MFS filer (their standard deduction is $0 and
  `compute_schedule_a` already forces `use_itemized=True`). Do NOT run the tilt
  in that case — the taxpayer must itemize regardless of §170(p), and because
  §170(p) is a NON-itemizer deduction it is $0 for them (line 13c stays $0). Run
  the standard-vs-standard+§170(p) tilt ONLY when the standard deduction is
  actually available (i.e. `compute_schedule_a` did not force itemization).

**QBI Deduction (Form 8995):**
Use `compute_qbi_deduction()` from `agents/lib/tax_computations.py`:
- QBI = Schedule C net income (can be negative → loss carryforward). **Exclude
  §224 qualified tips:** when the Schedule 1-A tip deduction (line 13b) came
  from this trade or business, the 2025 Form 8995 instructions EXCLUDE that tip
  income from QBI — subtract the qualified-tip amount from the Schedule C net
  income BEFORE passing it as QBI, or the same dollars get both the line-13b
  deduction and an extra 20% QBI deduction (double benefit → understatement).
- REIT dividends = Section 199A dividends from 1099-DIVs
- **Net the QBI base by the SE deductions attributable to the trade**
  (§199A(c)(4) / Reg. §1.199A-3(b)(1)(vi) / Form 8995 line 1): for a
  self-employed filer, pass the portion of each of these that is attributable to
  the qualifying trade — `half_se_tax` (the deductible ½ of SE tax, Schedule 1
  line 15), `se_health_insurance` (Schedule 1 line 17), and `se_retirement`
  (SEP/SIMPLE/qualified-plan contributions, Schedule 1 line 16). `compute_qbi_deduction`
  subtracts their sum from `qbi_income` BEFORE taking the 20%. **Do NOT pre-net
  them into `qbi_income`** — pass the raw Schedule C net profit (less any §224
  tip exclusion above) and the SE deductions separately, or they'd be
  double-counted. Omitting them OVERSTATES QBI and the deduction (understates
  tax). For a W-2-only / no-SE-activity filer these stay 0 (the default). With
  multiple trades, attribute each deduction to the trade that generated it. The
  result reports `qbi_income_before_se_deductions` and `se_deductions_attributable`
  for transparency; `qbi_income` is the netted Form 8995 line-1 figure.
- **ALWAYS pass the overall-cap inputs** — Form 8995 lines 11-14 cap the deduction
  at 20% × (taxable income before QBI − net capital gain):
  - `taxable_income_before_qbi` = max(0, Line 11 − Line 12 − Line 13b − Line 13c)
    — BOTH the Schedule 1-A total (13b) AND the §170(p) charitable deduction
    (13c) reduce the QBI overall-cap base, since both come before QBI in the
    deduction stack. Omitting either overstates the cap.
  - `net_capital_gain` = qualified dividends (line 3a) + net LTCG (Schedule D line 15, floored at 0)
  - `threshold` = `federal.qbi_threshold` from tax_law JSON (keyed single/mfj — map MFJ/QSS→mfj, else single)
- **§70105 minimum QBI deduction (TY2026+):** pass `tax_year`, `qbi_minimum` =
  `federal.qbi_minimum` (`{income_threshold, amount}`), and
  `active_qualified_trade` = the profile's `flags.qbi_active_trade` (the
  attestation `/tax-setup` collects — True only when the QBI is from an ACTIVE
  qualified trade/business the taxpayer materially participates in, NOT a
  passive/portfolio activity). A small active business at or above the income
  threshold gets AT LEAST the indexed minimum even when 20% of QBI is smaller.
  **Do NOT default this to True** — the flag defaults False so a passive/
  portfolio filer never gets a floor they don't qualify for; but confirm the
  flag was actually set (a legacy profile predating the flag reads False),
  because omitting it for a genuine active business OVERSTATES tax. Taxpayer-
  favorable: it DEGRADES (no floor, `qbi_minimum_note` surfaced) when the cache
  is unfetched — never guess. Surface `qbi_minimum_note` when present.
- **Over the §199A threshold — compute Form 8995-A (N2).** When `over_threshold`
  would be true, supply the trade's **W-2 wages, UBIA of qualified property, and
  SSTB status** so the wage/UBIA limitation and SSTB phase-out are COMPUTED, not
  warned. Pass `w2_wages`, `ubia`, `is_sstb` (an attested bool — the trade's
  SSTB determination per §199A(d)(2)), and `filing_status` (drives the phase-in
  range: $50k/$100k-MFJ ≤TY2025, $75k/$150k-MFJ TY2026+ per OBBBA §70105; a
  fetched `federal.qbi_phase_in_range.{other,mfj}` override may be passed as
  `phase_in_range`). Inputs are per **trade or business** — §199A limits are
  per-trade; a single K-1 with multiple trades needs a Reg. §1.199A-4
  aggregation election (combine the QBI/wages/UBIA first) or one call per trade.
  The result carries a `form_8995a` block (regime, wage_ubia_limit,
  applicable_percentage for an SSTB in the range) and `form_8995a_applied: true`,
  and the unmodeled-limit `warning` is suppressed.
- If `over_threshold: true` but the wage/UBIA/SSTB inputs are NOT available,
  the simplified path is kept and the `warning` is surfaced: the deduction may
  be overstated (an SSTB phases to $0) — verify against Form 8995-A / tax
  software before filing, or supply the trade's wages/UBIA to compute it.

### Step 6: Compute Tax (Form 1040 Line 16)

**QDCG Tax Worksheet** (if qualified dividends or net capital gain > 0):

Use `compute_qdcg_tax()` from `agents/lib/tax_computations.py`:
- taxable_income: Form 1040 line 15
- qualified_dividends: Form 1040 line 3a
- **net_ltcg: Schedule D line 15 (NET long-term, AFTER foreign losses)**
  ⚠️ CRITICAL: Use NET LTCG, not gross capital gain distributions
- brackets: from `tax_law_{year}.json`
- cg_zero_limit / cg_fifteen_limit: from `tax_law_{year}.json`

If no qualified dividends and no net capital gain: use regular bracket tax via `compute_bracket_tax()`.

### Step 6.5: AMT (Form 6251 → Schedule 2 line 1 → 1040 line 17)

Run `compute_form_6251()` **whenever the return itemizes, or ANY addback
input is nonzero** (`has_iso_exercise`, private-activity-bond interest, a
prior-year Form 8801 carryforward — the TMT is needed even when no AMT is
due this year). It is a cheap pure function returning $0 harmlessly — a
"looks unlikely" judgment call is how SALT-only AMT gets silently omitted
(post-OBBBA a $40K SALT deduction alone is a real trigger in the
$250-450K band):

- Inputs: `taxable_income` = line 15 — **but when line 15 is $0 because
  deductions exceeded AGI, pass line 11 AGI minus line 14 deductions (a
  NEGATIVE number)** per the Form 6251 line 1 instructions; passing $0
  overstates AMTI by the shortfall; `regular_tax` = line 16 MINUS the
  Schedule 3 line 1 foreign tax credit; `amt_ftc` = that same FTC on the
  simplified path (the AMT FTC equals the regular FTC when no Form 1116 is
  required); `federal.amt` from tax_law JSON (exemption/phaseout_start by
  status — map QSS→mfj, HoH→single; `rate_28_breakpoint` is the single
  published figure, the function halves it for MFS). Pass `tax_year` (the
  return year) and `exemption_phaseout_rate` = `federal.amt.phaseout_rate`
  (usually `null` → the function applies the §55(d)(3) statutory rate: 25%
  through TY2025, **50% from TY2026** per OBBBA §70107). A TY2026 ISO
  exerciser in the phase-out range keeps too much exemption if `tax_year` is
  wrong — pass it explicitly, never default.
- Addback: `salt_deducted` (Schedule A line 7 = the salt_deductible figure)
  when itemizing, else `standard_deduction_taken` = line 12 — one or the
  other, never both.
- `iso_bargain_element`: **prefer the parsed Form 3921s** — /tax-parse
  records (box 4 FMV − box 3 strike) × box 5 shares per exercise under the
  `iso_bargain` ledger category for shares HELD past year-end (same-year
  sales are ordinary W-2 income, not an AMT adjustment). Sum those
  records; fall back to asking the user only when no 3921 was provided
  (record in answers.md). **Write the figure to
  `form_6251.iso_bargain_element`** — the Step 12.7 tie-out compares the
  ledger's iso_bargain records against this input echo, so a dropped 3921
  fails the gate instead of silently shrinking the AMT.
- **ISO shares from a PRIOR-year exercise sold this year:** include the
  NEGATIVE line 2k basis adjustment (regular gain − AMT gain, since the
  AMT basis is higher by the old spread) in `other_adjustments` — it
  lowers this year's TMT, which is exactly what frees the Form 8801
  credit. Ask whenever `prior_year.amt_credit_carryforward` > 0.
- `senior_deduction`: the OBBBA Schedule 1-A line 37 enhanced senior
  deduction (TY2025+, up to $6K per eligible spouse) is NOT allowed for
  AMT — pass the amount claimed so it's added back to AMTI.
- Pass `qualified_dividends`/`net_ltcg` + the capital_gains_rates limits so
  Part III taxes them at 0/15/20 inside AMT.
- **Routing:** `amt` → Schedule 2 line 1 → 1040 **line 17** (tax BEFORE
  credits), NOT line 23. This matters downstream: the CTC Credit Limit
  Worksheet A base in Step 7 is line 18 = line 16 + Schedule 2 line 1 —
  omitting the AMT from it under-allows nonrefundable CTC and spills the
  difference into refundable ACTC. Copy the result into the `form_6251`
  section. **CA AMT is now computed** — a CA ISO exerciser's Schedule P (540)
  runs in the Step 10 California sub-steps (`compute_ca_schedule_p`), so the CA
  side is no longer a verify-in-software note.
- **Form 8801 prior credit:** when `prior_year.amt_credit_carryforward` >
  0, run `compute_form_8801_credit(prior_carryforward, regular_tax, tmt,
  nonrefundable_credits)` where `nonrefundable_credits` = ALL nonrefundable
  credits claimed before it (line 19 CTC + Schedule 3 lines 1-4) — §53(c)
  nets them; passing 0 overstates the usable credit and understates tax.
  `credit_allowed` goes to Schedule 3 line 6b (→ line 20).
- **ALWAYS write `form_8801_carryforward_to_next_year`** whenever
  `compute_form_6251` ran = this year's `form_8801_carryforward` + (the
  8801 `carryforward_remaining` if a prior credit was used, else 0). This
  is NOT conditional on having a prior credit — a FIRST-year ISO exerciser
  is exactly who generates the credit, and /tax-rollover reads this field;
  leaving it unwritten forfeits the money permanently.

### Step 7: Compute Credits (Form 1040 Lines 19-21)

**Child Tax Credit + ODC (Line 19) and refundable ACTC (Line 28):**
Use `compute_child_tax_credit()` — check OBBBA/current year amount from tax_law JSON.
Pass the full Schedule 8812 inputs, not just the child count:
- `num_other_dependents` — dependents NOT qualifying for CTC (17+, qualifying
  relatives) each earn the $500 nonrefundable ODC; the phase-out reduces the
  COMBINED CTC+ODC.
- `tax_liability` — the **Credit Limit Worksheet A amount**, NOT raw line 18:
  line 18 tax (= line 16 **+ Schedule 2 line 1 AMT** from Step 6.5 — the AMT
  is part of the pre-credit base) MINUS the nonrefundable credits claimed
  before the CTC (Schedule 3 part I — FTC, dependent care, education,
  residential energy, etc.). Passing raw line 18 lets the CTC consume tax
  already used by earlier credits, overstating line 19 and suppressing the
  ACTC on line 28; omitting the AMT from line 18 does the reverse — it
  under-allows nonrefundable CTC and spills the difference into refundable
  ACTC.
- `earned_income` (wages + SE earnings) and `actc_limit_per_child`
  (`federal.child_tax_credit.refundable_limit` from tax_law JSON) → the result
  splits into `nonrefundable_used` (line 19) and `refundable_actc` (**line
  28**). Skipping this drops the refundable credit for families whose tax is
  smaller than the credit.
- If `actc_3plus_ss_alternative_not_modeled` is true, tell the user: with 3+
  children the Schedule 8812 SS-tax alternative can only INCREASE the ACTC — the
  computed figure may be understated; verify in tax software.

**Foreign Tax Credit (Line 20 via Schedule 3):**
Use `compute_ftc_limitation()` — compare tax paid vs. limitation.
If FTC ≤ simplified limit (from `tax_law JSON` `foreign_tax_credit_simplified`) from passive sources: no Form 1116 needed.

**Education credits (Form 8863 → Schedule 3 line 3 + 1040 line 29):**
When `flags.has_education_expenses` is true, use `compute_education_credits()`:
0. **WHO may claim (the biggest Pub 970 trap — check FIRST):** qualified
   expenses count only for the taxpayer, spouse, or a dependent the taxpayer
   CLAIMS on this return (§25A(f)(1)(A)). A student who is claimed as a
   dependent claims NOTHING on their own return; expenses paid BY that
   dependent student are treated as paid by the claiming parent (§25A(g)(3));
   tuition paid for someone who is NOT a claimed dependent (e.g. a
   grandchild not claimed) earns $0. Confirm each 1098-T student's
   dependency status on THIS return before counting their expenses.
1. Gather PER-STUDENT inputs (from 1098-T summaries + the interview): adjusted
   qualified expenses — tuition + required fees (+ course materials for AOTC),
   **NET of tax-free scholarships/grants (1098-T box 5), employer
   assistance, and the FULL expenses used to keep any 529/Coverdell
   distribution tax-free** — no double benefit: the same dollar of expenses
   cannot both make a 1099-Q distribution tax-free AND support a credit, so
   reserve the ENTIRE distribution amount allocated to expenses (not just
   its earnings portion). Alternative election: treat part of the 529
   earnings as taxable to free up $4,000 of expenses for the AOTC — often
   favorable (a 100% credit vs tax on a slice of earnings); surface the
   trade-off and let the user choose. The Pub 970 coordination is YOUR
   worksheet — the function takes the already-netted figure. Ask whether a
   scholarship can be elected taxable only if the user raises it (not
   auto-optimized). **If a student ends up LLC-only, STRIP
   course materials not paid to the institution as a condition of
   enrollment** — they count for the AOTC regardless of vendor (§25A(i)(3))
   but for the LLC only when institution-billed (Pub 970).
2. Per student, assert the AOTC facts explicitly with the user: degree
   program, at least half-time for one academic period, within the first 4
   years, AOTC/Hope claimed fewer than 4 prior years, no felony drug
   conviction, student TIN issued by the return due date (§25A(i)(6)), the
   institution's EIN available (Form 8863 line 22), and no prior AOTC
   disallowance requiring Form 8862 → `aotc_eligible`. When false the
   expenses pool into the LLC — EXCEPT the TIN case: **a student with NO
   TIN gets NEITHER credit (§25A(g)(1)) — OMIT them from the students list
   entirely**, don't pool them into the LLC; only the issued-AFTER-the-due-
   date timing rule is AOTC-specific (such a student, with a TIN by filing,
   may still take the LLC). **Lifetime-budget caveat:** §25A allows only
   4 AOTC years per student and the credit is ELECTIVE — in a low-expense
   year (e.g. freshman fall with $800 of tuition) claiming AOTC burns a
   lifetime year for a small credit; surface the trade-off and let the user
   choose (set `aotc_eligible` false to defer).
3. `magi` = AGI (+ FEIE/foreign-housing addbacks if `lives_abroad`).
   **QSS phases out on the SINGLE $80K-$90K range** (Form 8863 groups QSS
   with Single/HoH — not the usual QSS→MFJ mapping). MFS gets neither credit.
4. `tax_liability` = line 18 minus the Form 8863 Credit Limit Worksheet
   credits — Schedule 3 **lines 1, 2, 6d, and 6l** (FTC, dependent care,
   Schedule R elderly/disabled, Form 8978): in this pipeline the FTC plus
   any hand-computed Form 2441 / Schedule R / 8978 amounts (manual
   worksheets here, but the form still reserves their tax first).
5. Route the outputs: `nonrefundable_credit` → Schedule 3 line 3;
   `refundable_aotc` → **form_1040.line_29_refundable_aotc** (payments
   section — it pays out even at $0 tax and MUST be summed into
   line_33_total_payments); set the line 7 checkbox input
   `no_refundable_aotc` ONLY when the Form 8863 line 7 conditions hold —
   the age/support BRANCHES differ: (a) UNDER 18 at year end → condition
   met regardless of support; (b) exactly 18 → met only if earned income
   was LESS than half their own support; (c) 19-23 AND a full-time
   student → met only if earned income was LESS than half their own
   support (exactly half does NOT check the box — strict less-than);
   plus, in every branch, at least one living parent and not filing MFJ.
   A self-supporting 22-year-old student KEEPS the refundable 40%;
   checking the box on "student under 24" alone denies up to $1,000.
6. Write the `form_8863` section to result.json and remember to subtract
   Schedule 3 line 3 from the CTC's Credit Limit Worksheet amount above.

**Other credits:** Dependent care (Form 2441), etc. as applicable (manual worksheets — see the limitations manifest).

### Step 8: Compute Other Taxes (Form 1040 Line 23)

**Self-Employment Tax:**
Use `compute_se_tax()` — only if Schedule C shows profit > $400.

**Additional Medicare Tax (Form 8959):**
Use `compute_additional_medicare()` — always compute if Medicare wages > $200K.
The over-withholding credit goes to 1040 line 25c.

**NIIT (Form 8960):**
If MAGI > threshold (from tax_law JSON): 3.8% on lesser of net investment income or excess MAGI.

### Step 9: Compute Payments and Result (Form 1040 Lines 25-37)

```
Line 25a: Federal tax withheld from W-2(s) Box 2
Line 25b: Federal tax withheld from 1099s (if any)
Line 25c: Other withholding (Additional Medicare over-withholding from Form 8959)
Line 25d: Total withholding = 25a + 25b + 25c
Line 26: Estimated tax payments (1040-ES) + prior-year overpayment applied
Line 27: EIC (if applicable — uncommon at higher incomes)
Lines 28-32: Other credits/payments (refundable AOTC, excess SS withholding,
             Form 4136 fuel credit, etc.) — include any that apply, skip if $0
Line 33: Total payments = sum of Lines 25d through 32
Line 34: Overpayment = Line 33 - Line 24 (if positive)
Line 35a: REFUND = Line 34 (or portion to refund)
Line 37: Amount owed = Line 24 - Line 33 (if positive)
```

**Excess Social Security withholding (Schedule 3 line 11 → line 31):** Run
`compute_excess_ss_withholding()` for **EVERY person with at least one W-2** —
no trigger condition. It is a cheap pure function that returns zeros when
nothing is wrong, and every conditional trigger misses an employer-error edge
case (e.g. $6,000 withheld on $50,000 of wages is an error well below the
annual max). One call PER PERSON (never pool a couple's
Box 4 amounts) with that person's per-employer W-2 Box 4 list, **the matching
per-employer SS-wage list = Box 3 + Box 7 (Social Security tips — Box 4 covers
both, so Box 3 alone misclassifies a tipped employee's correct withholding as
employer error)** (withholding alone can't establish the
credit — erroneous over-withholding on below-base wages is an employer error,
not a credit), and
`federal.social_security.wage_base` from tax_law JSON. If the result has a
nonzero `employer_error_amount`, tell the user to recover that portion from the
employer (or Form 843) — per IRS Topic 608 an individual employer's
over-withholding is NEVER a credit on the return, even alongside other
employers; only `excess_credit` goes on Schedule 3 line 11 — **write it to
`schedule_3_line_11_excess_ss` in result.json and include it in
`line_31_schedule3_part2` → line 33** (persisting it keeps the refundable
payment auditable for `/tax-generate` and `/tax-verify` instead of silently
folding into the total). **Railroad
employees (Tier 1 RRTA, W-2 box 14):** the helper models SS tax only — when
any employer withheld Tier 1 RRTA, compute Schedule 3 line 11 by hand per
Topic 608 (excess tier 1 RRTA, and combined SS + tier 1 over the base, are
also creditable) and tell the user the helper's figure would understate it.

### Step 9.5: Estimated-Tax Penalty (Form 2210 → line 38)

Run `compute_form_2210()` **unconditionally** — it is a cheap pure function
that returns `no_penalty` with the governing reason (under-$1,000 de minimis,
prior-year-zero exception, withholding meets the safe harbor) when nothing is
owed, and every conditional trigger misses an edge case.

Inputs:
- `current_year_tax`: line 24 **minus ALL refundable credits** — EIC,
  refundable ACTC, refundable AOTC, net Premium Tax Credit, fuel credit
  (Form 4136), §1341 credit, refundable adoption credit (the Form 2210
  line 2/3 treatment; excess SS/RRTA is NOT here — it counts as withholding
  on line 6). NOT raw line 22. The result may be NEGATIVE (credits exceed
  tax) — pass it as-is; the de minimis test resolves it to no-penalty.
- `withholding`: line 25d **plus Schedule 3 line 11 excess SS/RRTA**
  (`schedule_3_line_11_excess_ss`) — Form 2210 line 6 treats the excess-SS
  special refund as withholding (IRC §31(b)), deemed evenly paid like the
  rest. Omitting it overstates the penalty and can trigger a Form 2210 that
  isn't due at all.
- `filing_status`, `tax_year` from the profile.
- `prior_year_tax`: `archive/{year-1}/result.json` form_1040
  `line_24_total_tax` **minus the prior year's refundable credits** (same
  line 2/3 treatment as the current year — Form 2210 line 8 uses the
  prior-year tax net of refundable credits; the raw line 24 overstates the
  harbor), floored at 0. `prior_year_agi`: prior `line_11_agi`. No archive →
  pass `None` for both (degrades to the 90%-of-current test — the prior-year
  harbor could only lower the requirement, so this never understates).
- `estimated_payments`: the FULL Form 2210 Table-1 payment timeline, not
  just line 26. Ask the user and record in answers.md:
  - dated 1040-ES payments ("paid evenly on the due dates" → four equal
    entries on Apr/Jun/Sep 15 and Jan 15). Dates matter: a January payment
    cannot retroactively cure an April underpayment;
  - a prior-year overpayment applied to estimates — dated Apr 15 of the tax
    year when the prior return was filed on time; if it was filed LATE, use
    the actual filing date (the credit didn't exist until then);
  - **the balance-due payment with an early-filed return**: when this return
    is filed and paid before Apr 15, include that payment at its actual date
    — it stops the Q4 (and any open) accrual early per the 2210 line 11
    rule (payment date or Apr 15, whichever is earlier). Filing and paying
    in full by Jan 31 eliminates the Q4 installment entirely (§6654(h) —
    the function notes this case; adjust manually).
- `underpayment_rates`: `federal.underpayment_rates` from tax_law JSON.

Act on the result:
- `no_penalty` → `line_38_estimated_tax_penalty` = 0; omit the `form_2210`
  section; note the reason in answers.md. When the reason is the
  prior-year-zero exception, first CONFIRM its two preconditions with the
  user (the prior year covered a full 12 months AND the taxpayer was a US
  citizen/resident all year) — record the confirmation in answers.md before
  zeroing line 38.
- `penalty_computed` → write the penalty to `line_38_estimated_tax_penalty`
  and copy the result into the `form_2210` section. Then:
  - balance due: `line_37_amount_owed` = (line 24 − line 33) **+ line 38** —
    the 1040 line-37 instruction says to include the line 38 penalty in the
    line 37 entry, and tax software does the same, so `/tax-verify` compares
    like for like;
  - overpayment (line 34 > 0): **reduce the refund** — line 35a = line 34 −
    line 38 (a big January estimate can produce a refund at filing while
    Q1-Q3 still accrued a penalty); if the penalty exceeds the overpayment,
    the remainder becomes the line 37 balance due.
- penalty applies but `penalty_computed` is false (rates unfetched) → keep
  line 38 at 0, include the `form_2210` section with its notes, and tell the
  user: fetch the §6621 quarterly rates via `/tax-setup`, or file without
  line 38 and let the IRS bill the penalty. Never guess a rate.
- Surface the Schedule AI note when income was concentrated late in the year
  (RSU vest, year-end sale) — the annualized method usually reduces the
  penalty but is manual.

### Step 10: State Return

Read taxpayer's state from `taxpayer_profile.json`. Compute state return:

**California (Form 540):**
1. Start with Federal AGI
2. Schedule CA adjustments:
   - HSA add-backs (employer contributions + HSA earnings) — CA non-conformity
   - Any other CA-specific additions/subtractions
3. CA AGI = Federal AGI + additions - subtractions
4. CA deduction — run `compute_ca_itemized()` whenever `compute_schedule_a`
   ran (SOURCE figures, not the federal total — CA has no SALT cap but
   disallows income/sales taxes, keeps the $1M mortgage limit, caps cash
   gifts at 50% of AGI, still allows misc 2%-floor expenses, and phases out
   itemized deductions above the R&TC §17077 FEDERAL-AGI threshold from
   `state.itemized_phaseout` — keyed `single`/`mfj`/`hoh`: map MFS→single
   (FTB publishes Single/MFS as one tier) and QSS→mfj; unfetched →
   phase-out skipped with a loud note). All AGI-anchored floors use
   FEDERAL AGI (the function handles this — pass both AGIs). Pass
   `mfs_spouse_itemizes` AND `married_filing_separately` for MFS/RDP-
   separate (CA mirrors the federal forced-itemize rule, and the CA
   mortgage limit halves to $500K). Compare against the CA standard deduction
   (tax_law JSON, CA section): **the CA election is independent of the
   federal one** — with CA's small standard deduction, itemizing CA-only
   is the norm for the persona. Write the `schedule_ca_itemized` section
   when itemizing.
5. CA taxable income = CA AGI - CA deduction (line 18 = larger of CA
   itemized or CA standard)
6. CA tax from FTB tables (use brackets from tax_law JSON)
7. CA exemption credits (personal + dependent, amounts from tax_law JSON)
8. CA tax after credits
9. Mental health surcharge (1% on income > $1M)
9.5. **CA Schedule P (540) AMT** — run `compute_ca_schedule_p()` whenever
    `has_iso_exercise` (the same trigger as federal Form 6251). The CA analogue
    of Form 6251: pass `ca_taxable_income` (Form 540 line 19), `ca_regular_tax`
    (the CA tax before AMT — step 8's tax after credits), the CA
    `filing_status`, and `exemption` / `phaseout_start` from **`state.amt`**
    keyed by status (map MFS→`mfs`, QSS→`mfj`) — unfetched HARD-ERRORS (never a
    guessed exemption). Pass `iso_bargain_element` (same CA-conforming spread as
    federal — CA conforms to the ISO adjustment) and any other CA Schedule P
    preference/adjustment items via `ca_preference_adjustments`. The 7% TMT rate
    and 25% phase-out are statutory (in the function). **`ca_amt` is ADDED to
    the CA tax** (write the `schedule_p` block: ca_amti / ca_exemption_allowed /
    ca_tmt / ca_amt / iso_bargain_element / carryforward_to_next_year). The
    `schedule_p_carryforward` is the deferral-attributable (ISO) CA AMT credit —
    write it to `california_540.schedule_p.carryforward_to_next_year` so
    /tax-rollover carries it to `prior_year.ca_amt_credit_carryforward` (a
    SEPARATE carryforward from the federal Form 8801). A non-ISO CA return skips
    this step (no `schedule_p` block).
10. CA withholding from W-2 Box 17
    (`ca_total_tax` = CA tax after credits + mental-health surcharge + `ca_amt`
    from step 9.5 when present.)
11. Excess SDI/VPDI credit (Form 540 line 74): run `compute_excess_sdi()`
    for EVERY person with any CA-wage W-2 — no trigger condition (cheap pure
    function, zeros when nothing is wrong; conditional triggers miss
    below-cap wage-relative employer errors, and TY2024+ has no cap to
    exceed at all yet still surfaces recover-from-employer amounts). One
    call PER PERSON with the per-employer
    W-2 Box 14 SDI amounts, **the matching per-employer CA-wages-subject-to-SDI
    list** (same wage-eligibility rule as the federal credit),
    `state.sdi_rate` / `state.sdi_wage_limit` from
    tax_law JSON, **and the tax year** — the year disambiguates a null wage
    limit: for TY2024+ (SB 951 removed the ceiling) null correctly yields
    "not applicable"; for pre-2024 years null HARD-ERRORS (the cap existed —
    fetch it; treating it as no-ceiling would silently drop a refundable
    credit). A nonzero `employer_error_amount` means that portion is
    recovered from the employer (EDD DE 1964), not the return; only
    `excess_credit` goes on the 540. Write it to `ca_excess_sdi_credit`.
12. CA refund/owed

**Other states:** Follow state-specific rules from `tax_law_{year}.json`.

### Step 11: International Compliance

**FBAR check:**
- Sum all foreign account maximum balances
- If aggregate > `fbar_threshold` (from `tax_law JSON`): set fbar_required = true

**Form 8938 check:**
Use `check_form_8938_required()` from `agents/lib/tax_computations.py`:
- ⚠️ Use REALISTIC simultaneous peak values, NOT conservative FBAR max values
- FBAR and 8938 use different valuation approaches

### Step 11.5: Optimization Discovery Engine

After computing the return, systematically check for missed optimization opportunities. For each item, compare the current return against what's possible:

**Category 1 — Deduction optimization:**
- Itemized vs standard deduction: compute itemized total (SALT up to the cap in `federal.salt_cap` from tax_law JSON — OBBBA: $40K / $20K-MFS for TY2025-2029 (base indexed ~1%/yr 2026-2029; MAGI phasedown above $500K not modeled), reverting to $10K / $5K-MFS in TY2030 — plus mortgage interest, charitable, medical >7.5% AGI). Would itemizing save money?
- Charitable bunching: if charitable is close to tipping point, mention bunching strategy for next year
- Above-the-line deductions: student loan interest ($2,500 max), educator expenses ($300), SE health insurance — all used?

**Category 2 — Credit optimization:**
- American Opportunity Tax Credit (AOTC) — up to $2,500, 40% refundable
- Lifetime Learning Credit — up to $2,000
- Saver's Credit — 10-50% of retirement contributions if AGI qualifies
- Energy credits: Residential Clean Energy (25D, 30% uncapped), Energy Efficient Home (25C, up to $3,200)
- EV credit: Clean Vehicle (30D, up to $7,500), Previously Owned (25E, up to $4,000)
- Dependent Care Credit (Form 2441) — verify current limits via WebSearch if legislation changed
- Adoption Credit — up to $17,280/child (OBBBA: up to $5,000 refundable, remainder non-refundable)
- Premium Tax Credit (Form 8962) — if marketplace insurance

**Category 3 — Retirement optimization:**
- 401(k) utilization: current deferral as % of max from `tax_law_{year}.json`. If `age_60_63`: use `catch_up_60_63` (supersedes `catch_up_50_plus`). If `age_50_plus` but NOT `age_60_63`: use `catch_up_50_plus`.
- IRA: room for contribution? Spousal IRA (IRC §219(c)) if spouse has low/no earned income?
- Roth IRA: income below phase-out? Direct contribution or backdoor needed?
- HSA: contribution at max (self-only / family limits from `tax_law_{year}.json` + catch-up from `hsa_limits.catch_up_55_plus` if 55+)?
- Mega backdoor Roth: if employer allows after-tax + in-service conversion

**Category 4 — Entity structure:**
- If Schedule C net profit > $50K (common heuristic — not from tax_law JSON): flag S-Corp election analysis opportunity (SE tax savings)
- If multiple Schedule Cs: consolidation opportunity?

**Category 5 — State optimization:**
- CA HSA: is the add-back computed correctly?
- State-specific credits: CA renter's credit ($60/$120), CA EITC, etc.
- Multi-state: any allocation or credit opportunities?

**Category 6 — Timing strategies:**
- Roth conversion window: if in lower bracket than expected future years
- Tax-loss harvesting: unrealized losses in brokerage accounts?
- Income deferral: if next year will be lower bracket (retirement, sabbatical, etc.)
- Estimated tax safe harbor: pay at least 90% of current year tax OR 100% of prior year tax (110% if prior year AGI >$150K MFJ / $75K MFS)

Output as a table:

```
## Optimization Opportunities Found

| # | Opportunity | Est. Savings | Action Required | Difficulty |
|---|------------|-------------|-----------------|------------|
| 1 | {description} | ${amount} | {what to do} | Easy/Medium/Hard |
...

Total potential savings: ${sum}
```

Only include opportunities where estimated savings > $50. Mark "Already optimized" items with ✅.

### Step 12: Update Result Files

Update `result.json` with ALL computed values:
- form_1040 section (all lines)
- All supplemental forms
- federal_tax_computation detail
- State return
- balance_due_summary
- income_summary_by_person
- **`meta.limitations` — the C2 manifest.** Run
  `python3 agents/lib/capabilities.py --workspace . --json` and copy its
  `limitations` array verbatim into `result.json` `meta.limitations`. It is
  never empty (it always carries at least the state-scope note); `/tax-generate`
  prints it on page 1 so the taxpayer sees what this return does NOT cover.

Update `answers.md` with computed results section.

### Step 12.7: Parse-to-return tie-out (HARD GATE)

Prove every parsed document landed on the return — the CP2000 killer:

1. Run: `python3 agents/lib/tie_out.py --workspace .`
2. Act on the exit code:
   - **0:** Tied out. If the report lists ⚠ unverifiable categories or ⚠
     advisory items (a possibly-taxable state refund, a personal-item 1099-K,
     a recomputed 1099-R box 2a, a net-negative SSA-1099), surface them to the
     user — they passed the gate but need a manual look before filing.
   - **2 (FAILED): STOP — the return is NOT complete.** The report
     (`summaries/_tie_out.md`) names the exact failure: a document with no
     ledger record, a ledger record pointing at a missing file, or a category
     sum that doesn't match the return (the classic dropped-1099). Fix the
     aggregation (or the ledger, if the parse was wrong) and re-run. NEVER
     proceed to `/tax-generate` past a failed tie-out, and never "fix" a
     mismatch by editing the ledger to match the return without re-checking
     the source document.
   - **1 (error):** parse_ledger.json is missing (re-run `/tax-parse` step
     6.5) or malformed — fix it; do not bypass.
3. This gate exists because `/tax-review` audits the already-aggregated
   result.json and structurally cannot see a document that dropped during
   aggregation — only the ledger-to-result comparison can.

### Step 13: Summary

Print:
- Filing status and tax year
- Total income, AGI, taxable income
- Federal tax, credits, total tax
- Federal refund or amount owed
- State refund or amount owed
- **TOTAL refund or amount owed**
- Forms that apply to this return
- Suggested next step: `/tax-generate`
