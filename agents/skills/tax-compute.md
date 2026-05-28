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
Line 7: Capital gain/loss from Schedule D line 16
Line 8: Other income from Schedule 1 (Schedule C net, etc.)
Line 9: TOTAL INCOME = sum of above
```

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
         OR itemized deductions (Schedule A) — use whichever is higher
Line 13a: QBI deduction (Form 8995)
Line 14: Total deductions = Line 12 + Line 13a
Line 15: Taxable income = max(0, Line 11 - Line 14)
```

**QBI Deduction (Form 8995):**
Use `compute_qbi_deduction()` from `agents/lib/tax_computations.py`:
- QBI = Schedule C net income (can be negative → loss carryforward)
- REIT dividends = Section 199A dividends from 1099-DIVs
- Deduction = max(0, QBI × 20%) + round(REIT × 20%)

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

**Child Tax Credit (Line 19):**
Use `compute_child_tax_credit()` — check OBBBA/current year amount from tax_law JSON.

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

### Step 10: State Return

Read taxpayer's state from `taxpayer_profile.json`. Compute state return:

**California (Form 540):**
1. Start with Federal AGI
2. Schedule CA adjustments:
   - HSA add-backs (employer contributions + HSA earnings) — CA non-conformity
   - Any other CA-specific additions/subtractions
3. CA AGI = Federal AGI + additions - subtractions
4. CA standard deduction (from tax_law JSON, CA section)
5. CA taxable income = CA AGI - CA deductions
6. CA tax from FTB tables (use brackets from tax_law JSON)
7. CA exemption credits (personal + dependent, amounts from tax_law JSON)
8. CA tax after credits
9. Mental health surcharge (1% on income > $1M)
10. CA withholding from W-2 Box 17
11. CA refund/owed

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
- Itemized vs standard deduction: compute itemized total (SALT up to $10K, mortgage interest, charitable, medical >7.5% AGI). Would itemizing save money?
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

Update `answers.md` with computed results section.

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
