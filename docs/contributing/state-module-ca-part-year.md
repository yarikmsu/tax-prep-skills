# California Form 540NR — maintained part-year contract

Use this with the [state-module playbook](state-module-playbook.md),
[full-year CA contract](state-module-ca.md) and
[residency guardrails](residency-guardrails.md). This is a separately declared
return variant, never a change inferred from a mailing address.

## 1. Scope and authorities

Calendar-year **TY2025**, one continuous California resident period within
the covered year. Single, HOH, QSS and MFJ are
supported; joint spouses must have identical residency periods. Federal and
CA filing status must match. MFS, RDP/pro-forma federal returns, mixed joint
periods, disjoint CA residence periods, military elections, foreign moves and statutory second
residency retain their gates. A whole-year resident uses Form540; a whole-year
nonresident is outside this part-year variant.

The filing plan lists every resident/source state and its return obligation.
Only an inventory whose other states are covered no-income-tax regimes with
no return required clears the move/source blocks. Another income-tax return
is not made complete by computing California. Competing CA variants or
simultaneously declared CA/NY allocation plans refuse.

Primary sources read live on 2026-10-21, all explicitly naming TY2025:

- [Form540NR](https://www.ftb.ca.gov/forms/2025/2025-540nr.pdf) and
  [booklet](https://www.ftb.ca.gov/forms/2025/2025-540nr-booklet.html).
- [ScheduleCA(540NR)](https://www.ftb.ca.gov/forms/2025/2025-540nr-ca.pdf) and
  [instructions, revised April2026](https://www.ftb.ca.gov/forms/2025/2025-540nr-ca-instructions.html).
- [ScheduleP(540NR)](https://www.ftb.ca.gov/forms/2025/2025-540nr-p.pdf) and
  [instructions](https://www.ftb.ca.gov/forms/2025/2025-540nr-p-instructions.html).
- [FTB3510](https://www.ftb.ca.gov/forms/2025/2025-3510.pdf),
  [FTB3804-CR instructions](https://www.ftb.ca.gov/forms/2025/2025-3804-cr-instructions.html),
  [FTB5805 instructions](https://www.ftb.ca.gov/forms/2025/2025-5805-instructions.html), and
  [540-ES instructions](https://www.ftb.ca.gov/forms/2025/2025-540-es-instructions.html).

Blank official forms were also rendered and inspected for column and line
placement. These references establish form structure, not permission to use
their figures in another year. The estimated-tax worksheet's suggestion to use
prior-year schedules is not a final-return law source.

## 2. Questionnaire (playbook step0)

| # | Question | California part-year answer |
|---|---|---|
| 1 | Starting point | Federal income/adjustments populate ScheduleCA columnA. ColumnsB/C change law; columnD is worldwide CA-law income and columnE is resident-period income plus nonresident CA-source income. |
| 2 | Modifications | Existing CA conformity screens remain. Reviewed rows distinguish law differences from source allocation. Leaving California is never a columnB subtraction. |
| 3 | Credits | Exemption credits are limited using federal AGI, then prorated using taxable income. Special-credit eligibility and refundable family-credit amounts require their own NR worksheets. |
| 4 | Local income taxes | No CA local income-tax computation is introduced. |
| 5 | Surtax | Behavioral Health Services Tax uses CA taxable income on line35, not worldwide taxable income. |
| 6 | Estimates | Existing fetched CA installment shares apply. FTB5805 regular-method tax uses NR lines63/71/72 and eligible interest; its AGI input is CA AGI, line32. Annualization requires its own NR worksheet. |
| 7 | Payroll | No new payroll computation. No excess-SDI credit exists in this reviewed form year. |
| 8 | Withholding | DE4 remains the payroll form. Actual CA withholding is routed to NR line81; 592-B/593 withholding goes to line83. |
| 9 | Extension | Automatic filing extension, payment through FTB3519; payment is still due by the applicable original deadline. |
| 10 | PTET | Direct eligible owners may use the source-reconciled FTB3804-CR contract. It follows OSTC and can reduce regular tax below TMT. It is not a payment. |
| 11 | Community property | CA remains in the global community-property screen. MFS is outside this variant; no marital allocation is guessed. |
| 12 | Deductions | Reviewed ScheduleCA PartIII itemized worksheets or the fetched annual standard deduction precede PartIV's AGI allocation. This is not resident-month proration. |
| 13 | Tax math | Tax on worldwide taxable income uses the published table/rates. Its effective rate is rounded before multiplying CA taxable income; exemption-credit allocation is a separate calculation. |

## 3. Fetched law

Use the existing `create_state_template("CA")` block, selected by the declared
plan even when the mailing address is elsewhere. No additional annual figure
or seed block is required. The covered cache year must match the plan, profile
and return. Existing standard deductions, exemptions, exemption phaseout,
published tax table/rates, AMT and payment/penalty parameters remain mandatory.
PTET vintage parameters are required when that credit is active.

The source pin remains `_sources["state.CA"]`, with the independently pinned
CA underpayment schedule where used. Never substitute a prior year's figure
or invent a deduction, credit or rate when a fetched value is absent. The
existing statutory-fixed surcharge exception is unchanged.

## 4. Plan and result contracts

`profile.state_filings.CA` declares `form: "540NR"`, the covered year,
`residency: "part_year"`, ISO `resident_period`, joint-period confirmation,
resident/source inventories and `other_state_obligations`. Residency, source,
conformity, special-accrual, deduction, AMT and credit reviews are explicit.
The move screen must agree with this declaration. Dates and obligations are
asserted from evidence, never derived from the mailing address or day counts.

Income and adjustment rows retain an opaque ID, the printed ScheduleCA line,
federal amount, law subtractions/additions, and disjoint CA-law amounts for
resident, nonresident-CA and nonresident-elsewhere periods. Those three amounts
must sum to columnD at cents before form rounding. Actual receipts, payroll
workdays, entity statements or reviewed source worksheets explain allocation;
calendar-day division of annual income is not an allocation method.

Federal rows reconcile to the checked Form1040 income lines; Schedule1 rows
sum to line8 and adjustment rows to line10. ColumnA AGI must reconcile before
rounding too. Known CA exclusions and ledger-supported conformity components
cannot disappear into an entered AGI. Source capital gains/losses and
income-dependent adjustments require their separate CA worksheets. Social
Security and CA-exempt unemployment do not become CA-source taxable income.

The result section is `california_540nr`, with a checked `computed_tax_year`
witness. It contains the source workpaper, ScheduleCA columns and PartIV,
Form540NR lines7–125 that apply, ScheduleP(540NR), credit/payment/penalty
workpapers and the stable `ca_total_tax`, `ca_refund`, `ca_amount_owed` aliases.
`ca_agi` means line32; `ca_all_source_agi` means line17. Metadata records
`ca_return_type: "540NR"`; the registry selects exactly one computed CA form.
Deleting its section or substituting a full-year scaffold cannot avoid a gate.

The builder may write a draft before the final review. Its
`allocation_input_digest` binds periods, allocations, household/worksheet
inputs, federal components, ledger evidence and covered law, excluding the
review object and derived outputs. `plan.worksheet_review` must bind that exact
digest and explicitly confirm source, conformity, deduction, AMT and credit
worksheets. Both `--check` and independent tie-out refuse a missing/stale
review. No entered final tax or AGI replaces the raw allocation workpaper.
`conformity_manual_rows` identifies each additional law difference by printed
line; known ledger-supported components cannot be shifted to another line with
the same net AGI. `w2_ca_wages` reconciles every active W-2 source's CA Box16 amount, including
worked zeros, and derives informational line12; line13 federal AGI controls the
exemption limitation. `form_inputs`, `deduction_inputs` and `amt_inputs` preserve entered worksheet
facts separately from derived form lines, so form rounding cannot change the
next build's inputs or create a review-digest cycle.

## 5. Computations and review boundaries

`ca_540nr_plan` validates the declared profile path. `compute_ca_540nr` consumes
reviewed rows, explicit worksheet inputs and fetched law; it does not resolve
domicile or allocate compensation from dates. Both entry points validate
filing-status values; all numeric boundaries reject bool and nonfinite values.

Keep three regular-tax calculations distinct:

- ScheduleCA PartIV caps the CA-AGI/worldwide-AGI deduction percentage within
  its form bounds and applies it to the elected annual deduction.
- Form540NR line36 rounds worldwide tax/worldwide taxable income to four
  decimal places, then line37 applies that rate to CA taxable income.
- Line38 separately caps CA taxable income/worldwide taxable income for the
  prorated exemption credit and the special credits subject to that ratio.

Printed amount lines use whole-dollar half-up rounding, while source rows and
reconciliation preserve cents. Raw and printed AGI columns retain their
rounding reconciliation. A credit-rounding workpaper retains original source
cents, whole-dollar form amounts and their difference. PTET current credits,
eligible carryovers and expired carryovers are rounded separately, with
deterministic largest-remainder allocation preserving each lot's vintage.
The source-reported total remains tied to the ledger; only the form amounts
enter credit limits and the resulting filed-form carryforward.
A nonpositive denominator with a positive
taxable numerator refuses for an alternate worksheet; it never produces a
computed zero. Worked zero in both columns remains a valid computed return.
Federal AGI controls the exemption limitation; the eligible head counts retain
the full-year CA rules. Dependent-filer deduction worksheets are explicit.

ScheduleP(540NR) computes worldwide AMTI/exemption/TMT and its separate
CA-source AMTI, itemized allocation, AMT rate and CA TMT. Regular tax for its
AMT comparison is Form540NR line37, before exemption credits. Itemized AMT
adjustments and the itemized-limitation reversal are reviewed components;
standard-deduction addback is derived. NOL, qualified-business AMTI exclusions,
special child exemptions and other unsupported AMT worksheets refuse rather
than defaulting to zero. ISO deferral attribution requires both worldwide and
CA-source ISO worksheets. `iso_basis_reconciliation` binds the worldwide spread
to ledger ISO plus a reviewed signed CA basis/CQSO difference, with supporting
ledger IDs for nonzero differences. Other deferrals retain manual limits.

FTB3510 PartII reads NR line37 and the prorated exemptions, with the appropriate
CA TMT and preceding A credits. Qualified opening AMT balances and PTET
vintages remain source-reconciled. Special credits needing line38 proration
receive it once; NR-specific allowed amounts are never taken from a saved
Form540 claim. FTB3506, renter/family credits, other credit eligibility,
ISR and other-tax worksheets remain explicit reviewed inputs.

Form540NR has no use-tax line. Its booklet directs reporting and payment to
CDTFA separately. Confirm that obligation explicitly and carry the separate
payment note into the filing checklist; never move Form540's use-tax line91
onto NR line91, which is the ISR penalty.

ScheduleG1/FTB5870A distribution tax is outside this variant; a positive line41
refuses before credit ordering rather than assuming a credit can offset it.

Actual state payments retain the existing destination/year/ownership checks.
NR line102 is the forward election. FTB5805 regular-method pricing uses the
NR tax/withholding map and line32 AGI, with explicit prior-return facts;
manual/FTB-billing elections retain their existing disclosures. Lines122/123
settle after the overpayment and forward election and can turn a refund into
an amount due. They never reduce the forward election silently.

## 6. Registrations and gates

- Add one CA registry variant for Form540NR; retain the primary Form540 row.
  Register its computation witness, YoY movers and package form.
- Extend CA selection/competition checks in the builder, independent tie-out,
  PDF and filing package. A federal arithmetic waiver cannot waive CA checks.
- Route existing CA ledger categories to the selected return's withholding
  and credit fields; do not create duplicate categories or count both forms.
- Setup, parse and compute instructions collect the declared plan and actual
  allocations. Headless answers require the new reviews explicitly.
- Calendar/extension labels follow the plan. Generic CA projections, Roth,
  scenarios and withholding forecasts refuse/skip with a named allocation
  reason until they can reprice a part-year return.
- Rollover uses the selected CA variant for qualified credits, PTET vintages
  and the applied overpayment. FTB5805's prior `full_year` fact means a
  twelve-month taxable year, not twelve months of CA residency. A part-year
  resident's calendar-year return can qualify; residency dates do not decide
  that fact automatically.
- Existing other-state, statutory-residency, community-property and unsupported
  conformity gates remain. The full-year rental engine is not reused for a
  move-year rental; its separate NR passive/depreciation allocation stays gated.
- Filing output identifies Form540NR, its ScheduleCA and applicable ScheduleP,
  federal attachments, withholding statements and payment guidance from the
  reviewed booklet. It is a summary package, not an e-file artifact.

## 7. Tests and review

Hand worksheets cover unequal income timing, separate deduction/credit
ratios, effective-rate rounding, source income exceeding worldwide income,
zero/negative denominators, exemption limits, CA-only surcharge, both AMT
columns, owner-credit ordering, payment destinations and final settlement.
Use synthetic rates and fetched-shaped fixtures to isolate arithmetic.

Integration tests cover an outbound and inbound no-tax-state move, differing
mailing address, zero-income witness, missing/malformed plans, omitted federal
rows, nonfinite values, unsupported households, both CA variants, dropped
sections, stale workpapers, ledger routing, rollover and planner refusals.
Deliberately remove important guards and verify their regressions fail.
Review/repair before committing, run the complete suite and required checks,
then verify all CI checks at the final published commit. Never merge the PR.

## 8. Explicit limits

This computes a reviewed California part-year return from reviewed allocation
worksheets. It does not decide legal residency, source stock compensation,
business apportionment, special accruals, CA capital-loss allocation, another
state's return, rental move-year PAL/depreciation or the separately named
eligibility worksheets. No missing answer is an assumed zero. Unsupported
cases keep a named gate and every manual boundary reaches `meta.limitations`.
