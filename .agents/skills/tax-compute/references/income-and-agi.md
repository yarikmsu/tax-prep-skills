# tax-compute Steps 1-5 — income, AGI and taxable income

Form 1040 lines 1-15. Read this when you reach Step 1.

Steps 0.5 (tax law freshness) and 0.7 (capability gate) live in
`../SKILL.md` and must have passed before any of this runs.

## Contents

- [Step 1: Load All Input Data](#step-1-load-all-input-data)
- [Step 1.5: Pre-Computation Confidence Check](#step-15-pre-computation-confidence-check)
- [Step 2: Determine Applicable Forms](#step-2-determine-applicable-forms)
- [Step 3: Compute Income (Form 1040 Lines 1-9)](#step-3-compute-income-form-1040-lines-1-9)
- [Step 4: Compute AGI (Form 1040 Lines 10-11)](#step-4-compute-agi-form-1040-lines-10-11)
- [Step 5: Compute Taxable Income (Form 1040 Lines 12-15)](#step-5-compute-taxable-income-form-1040-lines-12-15)

---

### Step 1: Load All Input Data

Read these files:
1. `taxpayer_profile.json` — filing status, dependents, flags
2. `tax_law_{year}.json` — brackets, rates, thresholds, limits
   - If missing or `--refresh` flag: re-fetch from the web (see `/tax-setup` step 4)
3. `result.json` — current parsed data
4. `answers.md` — resolved questions
5. All `summaries/*.md` — detailed parsed data

### Step 1.5: Pre-Computation Confidence Check

Before computing, identify assumptions that could change the tax outcome by >$100. **First check `answers.md` "Confirmed Assumptions" and "Resolved Questions" — skip any question already answered there.** Ask the user about remaining unknowns, in batches of at most 5 questions.

**Non-interactive runs:** if this session cannot prompt the user (headless / batch / CI), read the answers from `answers.md` and hard-stop on any missing REQUIRED answer — never guess. A guessed assumption silently produces a wrong return that looks entirely plausible.
 Classify each as:
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
- Dependent care expenses amount? (HIGH if >$0 — verify current credit limits on the web if legislation changed)
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

**`agents/lib/return_builder.py` writes the lines marked DERIVED below — do not
type one.** Lines 1a, 2a, 2b, 3a and 3b are the parse records' own sums, and
lines 1z and 9 are arithmetic over the rest. What this step is for is the
COMPONENTS: deciding what is taxable, and computing the worksheets that produce
4b, 5b, 6b, 7 and 8. Run the builder after Step 5 (see SKILL.md).

```
Line 1a: DERIVED — total wages from all W-2s (the ledger's `wages` sum)
Line 2a: DERIVED — tax-exempt interest (memo line; NOT in line 9, but the
         taxable-SS worksheet reads it)
Line 2b: DERIVED — total taxable interest (all 1099-INTs + foreign interest)
Line 3a: DERIVED — qualified dividends (sum of 1099-DIV Box 1b)
Line 3b: DERIVED — ordinary dividends (sum of 1099-DIV Box 1a)
Line 1z: DERIVED — 1a + 1h
Line 1h: other earned income NOT on a W-2 — yours
Line 4a/4b: IRA distributions (if applicable)
Line 5a/5b: Pensions (1099-R; Code G rollover = 5a only, 5b = $0)
Line 6a: DERIVED — SSA-1099 box 5 total (the ledger's `ss_benefits` sum)
Line 6b: taxable amount from compute_taxable_ss below — yours
Line 7: Capital gain/loss from Schedule D line 16
Line 8: Other income from Schedule 1 (Schedule C net, etc.)
Line 9: DERIVED — TOTAL INCOME = 1z + 2b + 3b + 4b + 5b + 6b + 7 + 8
        (6b — the TAXABLE benefit amount — is IN the sum; omitting it
        understates AGI by exactly what the worksheet just computed, which is
        why the sum is no longer done by hand)
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

**Capital loss carryforward (Schedule D):** the PRIOR year's carryforward
enters this return BY CHARACTER — `prior_year.capital_loss_carryforward_short_term`
on Schedule D line 6 (record it in `schedule_d.part_i_short_term.line_6_prior_year_carryover`,
inside line 7's net) and `prior_year.capital_loss_carryforward_long_term` on
line 14 (`schedule_d.part_ii_long_term.line_14_prior_year_carryover`, inside
line 15's net). The profile fields are POSITIVE magnitudes; lines 6 and 14 take
the carryover as a NEGATIVE number, so each enters its net as a subtraction. If only the LEGACY combined `prior_year.capital_loss_carryforward`
is non-zero and both split fields are zero, **STOP**: ask the user for the split
from the prior return's Capital Loss Carryover Worksheet (lines 8 and 13), record
it in answers.md as `- **[capital_loss_carryforward_split]** the answer` so the
next run's required-answer check reads it, write the split fields, and never divide a combined
figure by guess — short-term loss offsets ordinary income first, so a wrong split
changes the tax. When line 16 is a net LOSS, cap the line 7 deduction at $3,000
($1,500 MFS) and fill `schedule_d.loss_carryforward_to_next_year` (short_term /
long_term split) in result.json using the Capital Loss Carryover Worksheet from
the Schedule D instructions — the amount actually used can be LESS than $3,000
when taxable income is low. `/tax-rollover` carries this explicit block into next
year's split fields; leaving it zero silently loses the carryforward.

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

**§988 foreign-currency bond disposition (P3):** For a directly-held
foreign-currency (non-USD) BOND that was sold or redeemed — NOT a fund (a fund
is a PFIC and is BLOCKED by the capability gate, `pfic_taxable_event`) — call
`compute_section_988()` from `agents/lib/tax_computations.py` with the
foreign-currency cost/proceeds and the spot rates on the acquisition and
disposition dates (from `foreign_holdings_detail.holdings`). It returns the
Reg. §1.988-2(b) split:
- **`exchange_gain_ordinary` → Schedule 1 (ordinary), regardless of holding
  period** — this is §988(a)(1) exchange gain/loss. **Source it by the
  taxpayer's RESIDENCE (§988(a)(3)): for a US resident it is US-SOURCE, so do
  NOT add it to Form 1116 foreign-source income** (mis-labeling it foreign-source
  inflates the FTC and understates US tax).
- **`market_gain_capital` → Schedule D** (short/long per `capital_term`). Set
  `long_term` from the holding period using the **one-year calendar-anniversary
  rule (§1222): held MORE THAN ONE YEAR, NOT a 365-day count** — a bond bought
  2023-07-01 and sold 2024-07-01 is held exactly one year → SHORT-term (even
  though 366 days elapsed across the leap day).
- **Do NOT net the two** — an exchange gain and a market loss of opposite sign
  land on different lines with different character; reporting the lump total as
  one capital figure misclassifies (and often understates) the ordinary FX
  piece. Write both to the `section_988_fx` result block; the coupon interest is
  translated at each payment-date spot rate to ordinary interest on Schedule B,
  and any §1272 OID accrues annually even while held (the `foreign_bond_oid_
  interest` MANUAL limitation surfaces both).
- If a spot rate is unavailable for either date, the function refuses — surface
  the FX component as MANUAL rather than compute a wrong ordinary amount.

**PFIC §1291 excess-distribution / disposition tax (P4):** When
`has_pfic_taxable_event` is set AND the user has **acknowledged** the
`pfic_taxable_event` block (recording the decision in answers.md) AND the
`foreign_holdings_detail.holdings` carry the §1291 facts, compute the punitive
§1291 tax per fund — NEVER route a PFIC distribution/gain through the
qualified-dividend or LTCG path (that understates):
- **ELECTION GUARD (do this FIRST, per fund):** compute_pfic_1291 is the
  no-election DEFAULT regime. Only route funds with `pfic_election == "none"`
  here. A fund with a timely `qef` (§1295) or `mtm` (§1296) election has a
  DIFFERENT treatment (QEF: pro-rata ordinary-earnings + LTCG inclusion; MTM:
  mark-to-market ordinary) that is NOT modeled — keep such a fund BLOCKED (do not
  run §1291 on it, which would over- or mis-state), and hand-prepare its Form 8621
  Part I/II/III. (For a delisted/sanctioned fund the elections are usually
  unavailable, so `none` is the expected value — but verify per fund, don't
  assume.)
- **AGGREGATE DISTRIBUTIONS PER FUND PER YEAR FIRST:** the §1291(b) 125% test is
  on the fund's TOTAL distributions for the tax year — SUM every distribution from
  that fund in the current year into one `current_distribution` (and each prior
  year's total into `prior_distributions`) BEFORE calling. Running it
  per-distribution would let two sub-threshold payouts each escape while their
  annual total is an excess distribution. One `compute_pfic_excess_distribution` +
  one `compute_pfic_1291` for the year's DISTRIBUTIONS, not per payout.
- **DISPOSITIONS do NOT aggregate — each partial sale is its OWN §1291(a)(2) event.**
  Unlike distributions (aggregated for the 125% test), each recognized GAIN is a
  separate excess distribution with its OWN allocation endpoint (the sale date), so
  the current-year-vs-prior-year split — and therefore the ordinary amount AND the
  deferred-tax/interest add-on — differ per sale. The v1 holding schema carries ONE
  `disposition_date` + `disposition_gain_usd`, so it can represent only ONE sale.
  **If a fund was sold in MULTIPLE partial transactions in the year, do NOT collapse
  them into one synthetic sale (that mis-dates the allocation and understates or
  overstates the tax) — BLOCK the return and hand-prepare Form 8621 with a separate
  §1291 computation per sale.** (v1 limitation; a per-disposition loop is future
  work.)
- **A DISTRIBUTION and a DISPOSITION in the SAME year are SEPARATE §1291 events —
  compute each on its own, then aggregate into the fund entry.** `compute_pfic_1291`
  takes one `amount` and one `event_date`; a distribution and a sale have DIFFERENT
  allocation endpoints (the distribution ends its ratable spread at the
  distribution date, the sale at the sale date), so you canNOT fold them into one
  call and must NOT drop either. **The holding therefore carries BOTH
  `distribution_date` and `disposition_date`** (not a single `event_date`) when
  both occur — pass each to its own call. Run `compute_pfic_1291` once for the
  aggregated excess distribution (`is_disposition=False`, `event_date` =
  `distribution_date`) and once for the disposition gain (`is_disposition=True`,
  `event_date` = `disposition_date`), then SUM their `total_1291_addon` /
  `ordinary_income_current` (and carry the distribution's `nonexcess_ordinary`)
  into the fund's totals and set the fund `complete` = AND of both. The fund's
  `total_1291_addon` still feeds the single TOTAL-TAX line-24 add-on (Step 6c) —
  NOT line 16. **Also record the disposition leg's ordinary allocation in the fund
  entry as `disposition_ordinary_income`** (the sale's `compute_pfic_1291`
  `ordinary_income_current`, always > 0 for a positive gain) — when a fund has BOTH
  a distribution and a sale-at-gain the same year, the tie-out requires this
  disposition-specific field to be present + positive so the sale's §1291 can't be
  silently dropped behind a small nonexcess-distribution result.
- `compute_pfic_excess_distribution(current_distribution, prior_distributions,
  is_first_holding_year, preceding_holding_years)` → the excess distribution
  (current − 125% × prior-base-period average; ZERO in the first holding year);
  the non-excess `ordinary_now` is a normal dividend this year. **You MUST pass
  `preceding_holding_years`** = the number of tax years the fund was held BEFORE
  the current year (from the holding dates), and `prior_distributions` MUST list
  the ANNUAL-TOTAL value for EACH of those base-period years **in CHRONOLOGICAL
  order (oldest→newest)** — **including `0` for any year with no distribution**.
  Only the MOST RECENT three entries form the §1291(b)(2)(A) base (the three tax
  years immediately preceding), so ordering matters: an old large distribution
  followed by recent zero years must NOT inflate the threshold. Omitting the count,
  or supplying only the non-zero years, makes the average denominator wrong (a
  too-high threshold → too little excess → understated tax); when the supplied
  history is short the result returns `complete=False` — keep the block until the
  full zero-inclusive history is in. **`preceding_holding_years` must equal the
  actual holding length (current tax year − acquisition year), NOT a shortened
  count** — the Step 12.7 tie-out re-derives it from `acquisition_date` and FAILS a
  fund whose asserted count is shorter (a short count shrinks the base window and can
  wrongly reclassify a real excess as non-excess). **A multi-year event owes
  prior-year deferred tax:** for a fund held ≥ 1 prior tax year, a DISPOSITION gain
  (entirely excess under §1291(a)(2)) and any EXCESS distribution allocate to the
  prior years, so `total_1291_addon` MUST be > 0 — the tie-out FAILS a multi-year
  gain or reclassified-out-of-line-3b excess that carries a $0 add-on.
- **A disposition at a LOSS does NOT go through §1291** — §1291(a)(2) converts
  only recognized GAIN to an excess distribution. A PFIC sold at a loss is an
  ordinary CAPITAL LOSS on Schedule D/Form 8949 (normal capital-loss rules); do
  NOT call `compute_pfic_1291` with a negative amount (it rejects it). Route the
  loss to Schedule D and record it; no deferred tax / interest arises.
  **You MUST still write a fund entry** to `form_8621_pfic.funds[]` documenting
  the disposition — `{"fund_id": <the holding's fund_id>, "fund": <name>,
  "is_disposition": true, "disposition_result": "loss", "total_1291_addon": 0,
  "ordinary_income_current": 0, "nonexcess_ordinary": 0, "complete": true, "note":
  "Loss disposition — §1291(a)(2) applies to gain only; loss routed to Schedule
  D/Form 8949, no deferred tax or interest"}` — the `fund_id` MUST match the
  `foreign_holdings_detail` holding (the identity gate matches on it), and BOTH
  amount fields (`total_1291_addon` + `ordinary_income_current`, here 0) MUST be
  present — so the Step 12.7 tie-out gate sees the acknowledged event WAS computed
  (a complete, zero-tax result) rather than silently skipped.
  A loss disposition with NO fund entry fails the gate exactly like an uncomputed
  gain. Set `form_8621_pfic.all_complete` accounting for this fund like any other.
- `compute_pfic_1291(amount, acquisition_date, event_date, current_year,
  historical_top_rate, underpayment_rates, pfic_start_date, is_disposition)` —
  `amount` = the excess distribution, or (for a **disposition at a GAIN**) the
  whole gain (always ≥ 0).
  `pfic_start_date` defaults to `acquisition_date` (v1 computes PFIC-FROM-
  ACQUISITION only — a `pfic_start_date` AFTER acquisition is a mid-holding
  transition and the call fails closed; block it / prepare Form 8621 manually).
  The holding period runs the DAY AFTER acquisition through the event (§1223).
  Route `ordinary_income_current` → 1040 ordinary
  income (normal rates); `total_1291_addon` (deferred tax at each prior PFIC
  year's highest ordinary rate + the §6621 interest charge) → an **add-on to Form
  1040 TOTAL TAX line 24** (deferred tax to the LEFT of line 24, interest via
  Schedule 2 line 17p → line 23), added AFTER nonrefundable credits and NOT re-run
  through brackets — see Step 6b/6c for the exact placement (folding it into line 16
  would let the CTC and other nonrefundable credits erode the §1291 charge). Write
  per-fund detail to `form_8621_pfic`. **For EVERY fund sold at a positive gain
  (pure disposition OR same-year distribution+sale), ALSO record the sale leg's
  `compute_pfic_1291` current-year ordinary allocation in the fund entry as
  `disposition_ordinary_income`** (always > 0 for a gain) — the tie-out requires
  this disposition-specific field so a sale's §1291 can't be masked by a stray
  `nonexcess_ordinary` (a distribution-only field) or a small distribution result.
  - **1099-B OVERLAP (a GAIN disposition) — REMOVE the gain from Schedule D/Form
    8949 first (avoid double tax):** a PFIC sale reported on a broker 1099-B is
    normally parsed into Form 8949/Schedule D as a capital gain. But under
    §1291(a)(2) the ENTIRE gain is an excess distribution routed through
    `compute_pfic_1291` (ordinary current-year allocation + deferred-tax add-on) —
    it is NOT a Schedule D capital gain. So REMOVE (or zero) that lot's gain from
    Form 8949/Schedule D before finalizing line 7, or it is taxed twice (once as
    capital gain, once via §1291). Keep the row for disclosure with a $0 gain /
    code-adjustment if your software needs it, but the gain must not remain in the
    Schedule D total. (A LOSS disposition is the opposite — it STAYS on Schedule D;
    §1291 doesn't touch losses.)
- `historical_top_rate` = `federal.historical_top_rate` (fetched); `underpayment_
  rates` = the §6621 cache (same one Form 2210 / underpayment-interest use).
- Write each fund's `complete` flag = **the AND of BOTH computations' `complete`**
  (`compute_pfic_excess_distribution["complete"]` AND `compute_pfic_1291["complete"]`)
  to `form_8621_pfic.funds[]`, and set `form_8621_pfic.all_complete` = (every fund
  complete). A short distribution history makes the excess-distribution result
  `complete=False` even when rates/interest are fully covered — so writing only
  the `compute_pfic_1291` flag would wrongly pass the gate. **Every fund entry MUST
  carry the same `fund_id` as its `foreign_holdings_detail` holding** — the tie-out
  matches taxable no-election funds to computed results BY `fund_id`, so a taxable
  fund with no complete matching entry FAILS (a bare count let two entries for one
  fund hide a second omitted fund). **This is MACHINE-checked:** the Step 12.7
  tie-out gate (`tie_out.py`) FAILS (exit 2, return NOT complete) when a taxable
  no-election fund has no complete matching `fund_id`, when `form_8621_pfic` is
  present but not all-complete, OR when a nonzero `total_1291_addon` was not
  reconciled into TOTAL TAX line 24 (below) — so an acknowledged
  `pfic_taxable_event` cannot exit cleanly with an incomplete or unincorporated
  §1291 figure.
- **Bias — punitive regime, never under-compute:** if any fund returns
  `complete=False` (a missing historical top rate, an uncovered §6621 quarter, or
  incomplete distribution history), the return is INCOMPLETE — do NOT file it and
  keep the block; fetch the missing law. Frozen/blocked funds: do not assume a
  blocked distribution was received (the `sanctioned_blocked_asset` constructive-
  receipt question stays MANUAL); a frozen fund is not a disposition.

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

Line 11 is DERIVED (`return_builder`); line 10 is yours.

```
Line 10: Adjustments to income from Schedule 1 Part II:
  - IRA deduction (check eligibility based on employer plan, MAGI, filing status)
  - SE tax deduction (half of SE tax, if Schedule C profit)
  - HSA deduction (personal contributions beyond employer) — see the Form 8889
    return-side test below
  - Student loan interest (if applicable)
Line 11: AGI = Line 9 - Line 10
```

**Form 8889 HSA return-side test (`has_hsa`):** run `compute_form_8889()` from
`agents/lib/tax_computations.py` to get the HSA deduction (line 13) AND catch an
EXCESS contribution + its §4973 6% excise. Pass `coverage_type` /
`eligible_months` / `last_month_rule` / `family_limit_share` from
`hsa_detail`, the employee + employer (W-2 box 12 code W) contributions, and the
`hsa_limits` (self_only / family / catch_up_55_plus) from `tax_law_{year}.json`.
**Do NOT assume full-year eligibility:** on the return side, comparing
contributions against the FULL annual limit when the taxpayer was covered only
part of the year MISSES a real excess + its excise (an understatement — the
inverse of the planning-side bias). If `hsa_detail.eligible_months` is not
available, keep the HSA excess check MANUAL (note it) rather than assume 12.
Surface `excess_contribution` / `excise_tax` / the testing-period note; the
`hsa_deduction` feeds Schedule 1 line 13.

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
Line 14: DERIVED — total deductions = Line 12 + Line 13a + Line 13b + Line 13c
Line 15: DERIVED — taxable income = max(0, Line 11 - Line 14)
```

Lines 12, 13a, 13b and 13c are yours; 14 and 15 are `return_builder`'s. **Run
`sh agents/lib/run.sh -m agents.lib.return_builder` when this step
is done** — it settles lines 1z, 9, 11, 14, 15 and 16, which is what Step 6.5's
AMT and the whole of Step 6 read.

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
  paid this year. Ask the user for the last two — they're not on any W-2 —
  and record both in answers.md as
  `- **[state_taxes_paid_outside_withholding]** the answer`.
- **FL filer ordering — compute `state_local_sales_taxes` BEFORE this call
  (Codex round-18 P1):** when the profile state is FL (`type: "none"`,
  `code: "FL"`), the line-5a income-vs-sales election needs the FL sales-tax
  deduction candidate as an INPUT to this `compute_schedule_a` call — run the
  ENTIRE Step 10 FL sales-tax computation NOW, including the actual-receipts
  METHOD ELECTION that leads it (ask the substantiation question first, then
  the Optional State Sales Tax Table lookup + `compute_sales_tax_deduction`,
  then the larger-of comparison; it needs only Step 4's AGI, the
  nontaxable-income interview, and the county-surtax/big-ticket/receipts
  inputs, so nothing blocks running it here) and pass its
  `total_line_5a_candidate` as `state_local_sales_taxes`. Running only the
  table lookup here and skipping the method election would re-create the
  understatement the election exists to prevent. Deferring it to Step 10's document position and
  "passing it to the existing call" leaves line 5a at $0 inside an
  already-made election — the finalized return omits the deduction or keeps
  a stale standard-vs-itemized election.
- Mortgage: 1098 box 1 + deductible points; ask for the average balance and
  whether the loan predates Dec 16 2017 (the $1M grandfather). If the
  balance is unknown the function takes the interest in FULL and notes the
  overstatement risk — surface that note.
- Charitable: ask for cash vs noncash split and whether every gift ≥ $250
  has a written acknowledgment; record it in answers.md as
  `- **[charitable_gifts]** the answer`. Ask even when this filer takes the
  STANDARD deduction — cash gifts feed the §170(p) line-13c deduction, and
  unasked it is silently zero. Surface the carryforward figure for
  /tax-rollover if nonzero.
- MFS: ask whether the spouse itemizes (`mfs_spouse_itemizes` forces
  itemizing per §63(c)(6)(A)); record it as
  `- **[mfs_spouse_itemizes]** the answer`.
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

