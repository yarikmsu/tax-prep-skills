# tax-compute Steps 6-9.5 — tax, AMT, credits, other taxes and payments

Form 1040 lines 16-38. Read this when you reach Step 6, after taxable
income is final.

## Contents

- [Step 6: Compute Tax (Form 1040 Line 16)](#step-6-compute-tax-form-1040-line-16)
- [Step 6.5: AMT (Form 6251 → Schedule 2 line 1 → 1040 line 17)](#step-65-amt-form-6251-schedule-2-line-1-1040-line-17)
- [Step 7: Compute Credits (Form 1040 Lines 19-21)](#step-7-compute-credits-form-1040-lines-19-21)
- [Step 8: Compute Other Taxes (Form 1040 Line 23)](#step-8-compute-other-taxes-form-1040-line-23)
- [Step 9: Compute Payments and Result (Form 1040 Lines 25-37)](#step-9-compute-payments-and-result-form-1040-lines-25-37)
- [Step 9.5: Estimated-Tax Penalty (Form 2210 → line 38)](#step-95-estimated-tax-penalty-form-2210-line-38)

---

### Step 6: Compute Tax (Form 1040 Line 16)

**QDCG Tax Worksheet** (if qualified dividends or net capital gain > 0):

Use `compute_qdcg_tax()` from `agents/lib/tax_computations.py`:
- taxable_income: Form 1040 line 15
- qualified_dividends: Form 1040 line 3a
- **net_ltcg: Schedule D line 15 (NET long-term, AFTER foreign losses)**
  ⚠️ CRITICAL: Use NET LTCG, not gross capital gain distributions
- brackets: from `tax_law_{year}.json`
- cg_zero_limit / cg_fifteen_limit: from `tax_law_{year}.json` at
  `federal.capital_gains_rates.{zero,fifteen}_pct_limit.<single|mfj|mfs|hoh>` — the
  FILER'S status key (QSS reads `mfj`); if that key is absent or null, **STOP** and
  run `/tax-setup` — never substitute another status's breakpoint (MFS's 15%
  breakpoint is half of joint, far below single's)

If no qualified dividends and no net capital gain: use regular bracket tax via `compute_bracket_tax()`.

**PFIC §1291 — order matters (ordinary income BEFORE the tax calc; the add-on
AFTER):** if `form_8621_pfic` carries computed funds (Step for PFIC above), do
these IN ORDER:

**6a. In the INCOME phase (back at Steps 3-4, BEFORE AGI and every AGI/MAGI-gated
calc) — route the PFIC current-year ordinary income through GROSS INCOME, not
bolted onto line 15.** BOTH current-year ordinary components are ordinary income
this year and must be in GROSS income → line 9 total income → line 11 AGI so that
every AGI/MAGI-dependent item (NIIT/Form 8960 MAGI, taxable Social Security,
education credits, IRMAA, IRA-deduction phase-outs, etc.) sees them; adding them
only at line 15 leaves AGI pre-PFIC and those items understated:
   - `ordinary_income_current` = the EXCESS-distribution current-year allocation
     (from `compute_pfic_1291`) → **Schedule 1 line 8 other income** (Form 8621
     Part V current-year inclusion), which flows to line 9/line 11; and
   - `nonexcess_ordinary` = the NON-excess normal-dividend portion
     (`compute_pfic_excess_distribution`'s `ordinary_now`) → an **ordinary dividend
     (line 3b, NOT qualified)** — $0 for a disposition, but for a BELOW-THRESHOLD
     distribution the excess is $0 and this is the ENTIRE distribution. **ALWAYS
     record `nonexcess_ordinary` at its true value (> 0 for any nonzero
     distribution), even when the distribution was already reported on a parsed
     1099-DIV** — there is NO broker-reported all-zero exemption (an unverifiable
     "it's already in line 3b" claim let a foreign-statement distribution be omitted
     entirely; Codex round 27 retired it). The value flows through the line-9/11/15
     reconciliation, and the 1099-DIV parse-ledger tie-out then proves the income is
     actually on the return: if it's genuinely in a parsed 1099-DIV, line 3b (incl.
     it) ties the box-1a ledger; if it's a foreign statement not in any parsed doc,
     either line 3b omits it (→ line 9/11/15 fail the reconciliation) or line 3b
     includes it without a matching parsed doc (→ the box-1a parse-ledger fails).
   - **PFIC dividends are NEVER qualified (remove from line 3a).** Even the
     non-excess portion is ordinary income — it must NOT sit in line 3a (qualified
     dividends), or the QDCG worksheet applies the preferential rate and understates
     tax. If a broker reported the PFIC distribution as qualified (box 1b → line 3a),
     **REMOVE the full PFIC amount from line 3a while KEEPING it in line 3b** (still
     an ordinary dividend). Record the removed qualified amount in
     **`form_8621_pfic.reclassified_from_line_3a`** so the box-1b parse-ledger ties
     (the tie-out adds it back UNCAPPED — the income stays in line 3b, proven by the
     box-1a check, and recharacterizing qualified→ordinary only raises tax).
   - **1099-DIV EXCESS OVERLAP (avoid double-count).** If the distribution was
     parsed into line 3b and it has an EXCESS portion, **REMOVE the FULL excess from
     line 3b** (and line 3a if it was there) and record the **FULL amount removed** in
     **`form_8621_pfic.reclassified_from_line_3b`** — this is the entire excess
     distribution, NOT just its current-year allocation. (Under §1291 the excess is
     allocated across the holding period: only the current-year slice is
     `ordinary_income_current` on Schedule 1; the prior-year slices are taxed via the
     deferred-tax add-on, not reported as current income. So the amount removed from
     line 3b exceeds `ordinary_income_current` for a multi-year distribution.) The
     tie-out adds the full `reclassified_from_line_3b` back to the result-side
     ordinary-dividend total **UNCAPPED** — the box-1a ledger reflects the broker's
     full distribution, and that the excess was actually run through §1291 (not just
     erased) is verified by the per-fund recompute (the deferred-tax add-on must cover
     the recomputed prior-year tax). Set `reclassified_from_line_3b`/`_3a` to 0 when
     nothing was removed from that line.
   After AGI is rebuilt, record the COUNTERFACTUAL baselines (each recomputed
   WITHOUT the PFIC ordinary income, everything else held):
   `form_8621_pfic.line_9_regular_total_income`, `line_11_regular_agi`, and
   `line_15_regular_taxable_income`. Then the filed `form_1040.line_9_total_income`,
   `line_11_agi`, and `line_15_taxable_income` include the PFIC income AND any
   downstream effects. Record `total_ordinary_income_current` = Σ
   `ordinary_income_current`, `total_nonexcess_ordinary` = Σ `nonexcess_ordinary`,
   `line_15_pfic_ordinary_income` = their SUM. **The tie-out FAILS** unless those
   aggregates == the per-fund sums AND EACH of `form_1040.line_9_total_income`,
   `line_11_agi`, `line_15_taxable_income` **>=** its `*_regular_*` counterfactual +
   the PFIC ordinary income. Each must grow by AT LEAST the PFIC ordinary income
   (the >= — MORE is valid, since routing it through AGI can raise taxable Social
   Security or shrink AGI-limited items; an exact-equality check would wrongly
   reject a correctly-recomputed threshold return, and checking only line 15 would
   miss income bolted onto taxable income but absent from AGI). **Every computed
   fund MUST carry `total_1291_addon`, `ordinary_income_current`, AND
   `nonexcess_ordinary`** (0
   valid, but present).

**6b. Compute the QDCG/bracket regular tax on the AUGMENTED line 15** (which already
includes the PFIC ordinary income from 6a, and AGI/MAGI-dependent items already
recomputed) → this is `form_8621_pfic.line_16_regular_tax`, and it is ALSO
`form_1040.line_16_tax` **unchanged**: the §1291 deferred-tax/interest add-on does
NOT go on line 16. Then finish the return NORMALLY through the nonrefundable credits
(line 22) and the Schedule 2 Part II other taxes (line 23) to get the counterfactual
**`line_24_regular_total_tax`** = Form 1040 line 24 total tax computed WITHOUT the
§1291 add-on.

**6c. AFTER 6b — apply the §1291 add-on to TOTAL TAX (line 24), NOT line 16.** The
aggregate `total_1291_addon` (Σ funds' deferred tax at each prior PFIC year's top
rate + the §6621 interest charge) is an ADDITIONAL TAX added AFTER nonrefundable
credits: per the IRS Instructions for Form 8621 (Rev. Dec 2025, Part V line 16), the
deferred tax is entered to the LEFT of Form 1040 line 24 and the interest on Schedule
2 line 17p (→ Schedule 2 Part II → Form 1040 line 23 → line 24). **Do NOT add it to
line 16** — line 16 feeds the pre-credit base (line 22 = line 16 + line 17 − credits),
so a §1291 charge on line 16 would be reduced by the CTC and other nonrefundable
credits and UNDERSTATE the tax. Set **`form_1040.line_24_total_tax` =
`line_24_regular_total_tax` + the add-on**, and record `line_24_addon` = the add-on
and `line_24_with_addon` = the same `form_1040.line_24_total_tax`. **The tie-out
FAILS** unless, when `total_1291_addon` is nonzero: `total_1291_addon` == Σ funds'
`total_1291_addon` == `line_24_addon`, `form_1040.line_16_tax` == `line_16_regular_tax`
(the add-on is NOT on line 16), `line_24_regular_total_tax` + add-on ==
`form_1040.line_24_total_tax`, and `line_24_with_addon` == `form_1040.line_24_total_tax`.
(All §1291 figures are ≥ 0; a NEGATIVE or non-finite amount is malformed and also
FAILS — a loss disposition is a Schedule D capital loss, not a §1291 entry.)

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

**Dependent-care credit (Form 2441, `has_dependent_care`):** run
`compute_form_2441()` from `agents/lib/tax_computations.py` (Schedule 3 line 6f
→ nonrefundable). Pass from `dependent_care_detail`: `qualifying_persons`,
`qualified_expenses`, `dependent_care_benefits` (= W-2 box 10 excluded), the
`taxpayer_student_disabled_months` / `spouse_student_disabled_months`, and
`lived_apart_exception`; plus `taxpayer_earned_income` / `spouse_earned_income`
(computed earned income), `agi`, `filing_status`, and `tax_liability` (the
Credit Limit Worksheet amount — it is NONREFUNDABLE). The GATES stay step 0:
only run it when `work_related_confirmed` and a qualifying person exist, and
confirm the provider name/TIN is on file (due diligence). MFS gets $0 unless
`lived_apart_exception`. Surface the notes (deemed-income both-spouses caveat,
DCB Part III, nonrefundable limit).

**Other credits:** energy/EV/etc. as applicable (manual worksheets — see the limitations manifest).

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
- `prior_year_zero_liability_confirmed`: pass `True` **only** when the
  composition below comes out to exactly $0 AND you have confirmed all THREE
  §6654(e)(2) preconditions with the user — the prior year covered a full 12
  months, the taxpayer had NO LIABILITY for it, and was a US citizen or
  resident throughout it. No liability means that year's Form 1040 TOTAL TAX
  line was $0, or that no return was required at all; it is NOT the
  composition below, which subtracts refundable credits and reaches $0 on a
  positive total tax ($500 of SE tax under a $2,000 EIC). That exception
  clears the whole year, so it is not taken from a figure that happens to
  equal zero. It does NOT require that a return was filed.
- `prior_year_return_filed_12_months_confirmed`: the SECOND route to a $0
  base, and an independent test — ask about it separately whenever the
  composition comes out to $0, including when the answer to the citizenship
  question above was "no". Pass `True` once the user confirms the prior year
  covered a full 12 months **and** a return was filed for it. That is
  §6654(d)(1)(B)'s closing text, the only thing standing between a taxpayer
  and the ordinary prior-year prong, and it asks nothing about citizenship or
  residency: 100%/110% of a $0 prior-year tax is $0, so the required annual
  payment is $0. Collapsing the two questions charged 90% of the current year
  to every $0-prior-year filer who could not affirm citizenship — a first-year
  green-card holder or dual-status filer with a full 12-month $0 return is
  covered, and was being told to pay.
  Left unconfirmed on BOTH, the engine DROPS the prior-year prong (the
  90%-of-current test stands alone), which overstates rather than clearing a
  penalty that attaches; record both answers in `answers.md`.
- `prior_year_tax`: the completed **pre-factor** prior-year tax composition
  under line 8 of the Form 2210 instructions for the COVERED year. Build it
  from that chart's enumerated income-tax and other-tax lines, exclusions, and
  refundable credits; raw Form 1040 line 24 minus credits is not sufficient.
  In particular, the chart conditionally excludes household employment taxes
  and can exclude §965 net tax, while admitting only named Schedule 2 items.
  Record the verified composition in `answers.md`; if it is not complete, pass
  `None` and leave the prior prong unresolved rather than infer it from the
  archive. `prior_year_agi` must come from **the same return figure 1 was
  composed from**, read off THAT year's own layout by the printed label:
  **Form 1040 line 11 holds AGI only from 2020 on**, and is 8b on 2019, 7
  on 2018, 37 on 2017. The number printed at line 11 on each of those is a
  small one — it reads as under the threshold and applies 100% where 110%
  is owed, the same tenth-low failure the amended-AGI trap below produces.
  Do NOT take it from the archived `line_11_agi` without confirming which
  return the archive holds: `/tax-amend` overwrites
  `archive/{year}/result.json` with the AMENDED return's figures, and
  §6654(d)(1)(B)(ii) reads the tax shown on the controlling return (the
  original, unless amended before that year's return was due). Pairing an
  original-return base with an amended AGI that crossed the
  $150,000/$75,000 threshold applies 100% where 110% is owed — the
  required annual payment comes out 10% low, on the filed return. Ask
  whether the prior return was amended and when; if the pair cannot be
  established from one return, pass `prior_year_agi=None` and take the
  flagged 100% factor rather than guessing.
  With no completed base, the calculation degrades to the 90%-of-current test
  — the prior-year harbor could only lower the requirement, so this never
  understates the amount needed for that remaining prong.
- `estimated_payments`: the FULL Form 2210 Table-1 payment timeline, not
  just line 26. Ask the user and record in answers.md:
  - dated 1040-ES payments ("paid evenly on the due dates" → four equal
    entries on Apr/Jun/Sep 15 and Jan 15). Dates matter: a January payment
    cannot retroactively cure an April underpayment;
  - a prior-year overpayment applied to estimates — dated Apr 15 of the tax
    year when the prior year was PAID IN FULL before that date; if the
    overpayment arose from a payment made after it, use that payment's date.
    The Form 2210 line 11 test is when the money went in, NOT when the return
    was filed: a return on extension whose tax was fully covered by
    withholding still credits on Apr 15, and dating it to the October filing
    would overstate every earlier installment's shortfall;
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
