# Tips and overtime: covered-year source review

Read the calendar year and revision before mapping a source. Reviewed forms:
TY2025 W-2 and April 2025 NEC/MISC; TY2026 W-2 and December 2026 NEC/MISC/K.
The published source forms establish the fields below. Use the final covered-year
return instructions for the deduction worksheet; a draft return is not authority
for new line numbers or limits. Fetch limits through the normal tax-law cache.

## TY2026 source fields

These are source amounts to REVIEW for the existing informational categories;
they do not themselves establish the allowed deduction.

| Form | Income already reported | Cash tips | Tipped occupation | Qualified overtime premium |
|---|---|---|---|---|
| W-2 | box 1 | box 12 code TP | box 14b | box 12 code TT |
| 1099-NEC | box 1a | box 1b | box 1c | box 1d |
| 1099-MISC | box 3 | box 13a | box 13b | box 14 |
| 1099-K | box 1a | box 1c | box 1d | — |

Use eligible cash tips for `qualified_tips` and the eligible premium for
`qualified_overtime_premium`. TT, NEC 1d and MISC 14 already report the
FLSA premium: **do not divide these amounts again**. A separate statement
showing gross overtime needs its own reviewed premium calculation.
Record these categories only for income already captured on the return;
resolve missing income and any paired payroll tax before claiming a deduction.

NEC 1b/1d are **included in box 1a**; MISC 13a/14 are **included in box 3**;
1099-K 1c is **included in box 1a**.
Never add these subamounts to the income total. Preserve wages from W-2 box 1;
route NEC 1a and the reviewed MISC box-3 business/other split once using their
normal income categories. Neither a TTOC nor W-2 code TA (employer Trump-account
contributions) is tips, overtime or extra wages. Keep TA for its own review.
W-2 box 14a replaces old box 14 for other labels, including state payroll taxes.

## TY2025 fallback only

The old forms have no dedicated TP/TT or 1099 tip/overtime fields. NEC's total
is **box 1**; MISC's remains box 3. For employee tips use the employer's
designated amount in box 14 or a separate statement, otherwise W-2 box 7.
Keep box 7 as `ss_tips` too; it is not added to wages again. Box 7 is capped at
the Social Security wage base, so a higher earner needs Form 4070 totals or
actual charged/cash-tip records to establish the full amount. Use the fetched
wage base, never a remembered annual amount. For 1099 tips and FLSA overtime,
obtain the separate TY2025 statement/records allowed by that year's guidance.

For TY2026, do not silently substitute box 7 for missing TP, copy a generic
box-14a amount into TT, or treat missing fields as zero when intake or another
box indicates tips/overtime. Reconcile a missing or inconsistent field with
the payer, a corrected form or another payer-furnished statement permitted by
the covered-year instructions. Do not carry TY2025 information-reporting
transition relief into another year by default.

## Qualification and unresolved facts

For tips, retain each source's TTOC(s), occupation and amount allocation in the
secure summary. Look up the code AND occupation in the current authoritative
[IRS occupation list](https://www.irs.gov/TippedOccupations). A three-digit
string alone proves nothing. Sole `000` means no qualified tips from that
source; preserve its income and explain the excluded deduction. With `000`
and another code, or otherwise mixed eligible/ineligible occupations, resolve
the qualifying amount from records; never qualify or discard the whole total
by default. Multiple qualifying codes do not themselves reduce the amount.
Missing/unknown codes need resolution, not guessed eligibility.
TTOC gates the TIP review; **overtime does not require a TTOC**.

Confirm voluntary cash/charged/tip-sharing amounts and the applicable-year
qualification rules. Mandatory service charges are wages/receipts, not qualified
tips. Apply the recipient-specific valid-SSN and joint-filing requirements to
both deductions, recording eligibility only, never a full SSN. Review SSTB
status and the applicable transition relief for employees and businesses.
The April 2026 final occupation/tip regulations reserve the SSTB issue for
separate guidance; their publication alone does not end that relief. Verify
the covered year's current rule, rather than assuming relief lasts forever.

For overtime, review FLSA section 7 eligibility and premium treatment. A 1099
source is not automatically disqualified; a true independent contractor's pay
generally is not FLSA overtime. Resolve classification and an inconsistent
statement instead of assuming all overtime qualifies. An occupation code used
for tips does not decide overtime eligibility.

Record **[tips_source_review]** and **[overtime_source_review]** in `answers.md`
when applicable, including the year/revision, source field, amount already
reported as income, eligibility and any allocation/exclusion. Update the
existing `has_qualified_tips` / `has_qualified_overtime` intake flags when a
potentially qualifying source needs review; do not leave their false defaults
because the new fields were missed. The `/tax-compute` required-answer check
stops a headless run for an unanswered applicable review, including amounts
already in its workpaper. Keep source amounts separate from
the amount supplied to the deduction engine and the final allowed deduction.

## Existing computation limits still apply

- Unreported or employer-allocated tips (W-2 box 8) require manual Form 4137:
  paired Form1040 line1c income AND Schedule2 line5 payroll tax. Include them
  in `qualified_tips` only after both are recorded. Reconcile overlapping TP,
  statements and Form4137 amounts once under the covered-year worksheet;
  do not sum duplicate reports. Keep `has_unreported_or_allocated_tips` set.
- Self-employed tips need the separately worked per-business NET-income cap,
  reduced by allocable half-SE-tax, retirement and health-insurance deductions.
  `compute_schedule_1a` does not compute this cap or source eligibility. Pass
  an already capped, eligible amount, preserve the source gross in the summary,
  and record the allowed SE portion as `schedule_1a.se_tips_deducted` for QBI.
- The informational ledger categories prompt a deduction review; they are not
  sum-tied to `tips.allowed` or `overtime.allowed`, which can be reduced by
  caps, MAGI or eligibility. During `/tax-compute`, call `compute_schedule_1a`; total goes to
  Form1040 line13b BELOW AGI. Income, AGI and withholding keep their source
  totals. Surface the function's caveats and explain a worked zero.

Sources read live 2026-10-20: [2026 W-2 instructions](https://www.irs.gov/instructions/iw2w3),
[2026 W-2 recipient copy](https://www.irs.gov/pub/irs-pdf/fw2.pdf),
[December 2026 NEC/MISC instructions](https://www.irs.gov/instructions/i1099mec),
[NEC recipient copy](https://www.irs.gov/pub/irs-pdf/f1099nec.pdf),
[MISC recipient copy](https://www.irs.gov/pub/irs-pdf/f1099msc.pdf),
[prior NEC](https://www.irs.gov/pub/irs-prior/f1099nec--2025.pdf),
[TY2025 Schedule1-A instructions](https://www.irs.gov/pub/irs-prior/i1040gi--2025.pdf),
[current tip guidance](https://www.irs.gov/businesses/small-businesses-self-employed/tip-recordkeeping-and-reporting)
and [TD10044, SSTB section6](https://www.irs.gov/irb/2026-18_IRB).
