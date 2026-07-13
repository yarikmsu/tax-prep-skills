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
Line 10: Adjustments to income from Schedule 1:
  - IRA deduction (check eligibility based on employer plan, MAGI, filing status)
  - SE tax deduction (half of SE tax, if Schedule C profit)
  - HSA deduction (personal contributions beyond employer)
  - Student loan interest (if applicable)
  - OBBBA above-the-line exclusions (Schedule 1 Part II, if applicable):
    · No tax on tips: exclude qualifying tip income
    · No tax on overtime: exclude qualifying overtime pay
    · Car loan interest deduction
    (These are tracked within line_10_adjustments in result.json.
     No separate taxpayer_profile flags needed — detect from W-2 data.)
Line 11: AGI = Line 9 - Line 10
```

⚠️ **OBBBA note:** The tips/overtime/car-loan provisions are ABOVE-the-line adjustments
(Schedule 1 Part II). Do NOT also deduct them below the line. The senior enhanced standard
deduction is already reflected in the Line 12 standard deduction amount from `tax_law_{year}.json`.

**IRA deduction eligibility check:**
- Use phaseout ranges from `tax_law_{year}.json`
- If spouse not covered by employer plan: use higher phaseout
- MAGI = AGI before IRA deduction (circular — use iterative or direct calc)

### Step 5: Compute Taxable Income (Form 1040 Lines 12-15)

```
Line 12: Standard deduction (from tax_law JSON, based on filing status;
         already includes OBBBA senior enhancement if applicable)
         OR itemized deductions (Schedule A) — election COMPUTED below
Line 13a: QBI deduction (Form 8995)
Line 14: Total deductions = Line 12 + Line 13a
Line 15: Taxable income = max(0, Line 11 - Line 14)
```

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

**QBI Deduction (Form 8995):**
Use `compute_qbi_deduction()` from `agents/lib/tax_computations.py`:
- QBI = Schedule C net income (can be negative → loss carryforward)
- REIT dividends = Section 199A dividends from 1099-DIVs
- **ALWAYS pass the overall-cap inputs** — Form 8995 lines 11-14 cap the deduction
  at 20% × (taxable income before QBI − net capital gain):
  - `taxable_income_before_qbi` = max(0, Line 11 − Line 12)
  - `net_capital_gain` = qualified dividends (line 3a) + net LTCG (Schedule D line 15, floored at 0)
  - `threshold` = `federal.qbi_threshold` from tax_law JSON (keyed single/mfj — map MFJ/QSS→mfj, else single)
- If the result has `over_threshold: true`, surface its `warning`: above the §199A
  threshold the Form 8995-A W-2-wage/UBIA limits and SSTB phase-out apply and are
  NOT modeled — the deduction may be overstated (an SSTB phases to $0). Verify
  against Form 8995-A / tax software before filing.

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

### Step 7: Compute Credits (Form 1040 Lines 19-21)

**Child Tax Credit + ODC (Line 19) and refundable ACTC (Line 28):**
Use `compute_child_tax_credit()` — check OBBBA/current year amount from tax_law JSON.
Pass the full Schedule 8812 inputs, not just the child count:
- `num_other_dependents` — dependents NOT qualifying for CTC (17+, qualifying
  relatives) each earn the $500 nonrefundable ODC; the phase-out reduces the
  COMBINED CTC+ODC.
- `tax_liability` — the **Credit Limit Worksheet A amount**, NOT raw line 18:
  line 18 tax MINUS the nonrefundable credits claimed before the CTC (Schedule 3
  part I — FTC, dependent care, education, residential energy, etc.). Passing
  raw line 18 lets the CTC consume tax already used by earlier credits,
  overstating line 19 and suppressing the ACTC on line 28.
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

**Other credits:** Education (Form 8863), dependent care (Form 2441), etc. as applicable.

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
10. CA withholding from W-2 Box 17
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
