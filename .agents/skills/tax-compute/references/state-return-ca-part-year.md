# California Form540NR — reviewed TY2025 part-year return

Use this branch only for a declared `profile.state_filings.CA` Form540NR plan.
The maintained field and scope contract is
`docs/contributing/state-module-ca-part-year.md`. Fetch the covered California
law block regardless of mailing address. Keep actual move/source flags true;
another state's return remains gated unless the reviewed inventory establishes
that no return is required in a covered no-income-tax regime.

## Required allocation and worksheets

Record **[ca_part_year_allocation]** in `answers.md`. In a headless run, stop if
that answer or a required worksheet is missing. Confirm residence from evidence,
not the mailing address. The plan requires `form: "540NR"`, `tax_year: 2025`,
`residency: "part_year"`, `resident_period` with canonical inclusive ISO dates,
`resident_states`, `source_states` and `other_state_obligations` rows containing
`state` and `return_required`. MFJ requires `joint_residency_identical: true`.
Single/HOH/QSS use null for that joint-only fact.

Confirm these plan facts true: `residency_reviewed`,
`obligation_inventory_complete`, `allocation_reviewed`, `conformity_reviewed`,
`special_accruals_reviewed`, `capital_allocation_reviewed`,
`no_military_or_rdp_elections`, `no_amt_nol_or_business_exclusion`,
`no_special_amt_child_exemption`, `no_special_distribution_tax`,
`no_move_year_rentals`, `credit_rounding_reviewed` and
`use_tax_obligation_reviewed`. These confirmations cannot make an unsupported
situation disappear. Whole-year residence uses Form540. MFS/RDP, differing
joint periods, multiple disjoint periods, foreign/statutory second residence,
move-year rentals and the named special worksheets retain their gates.

List `income_rows` and `adjustment_rows`, including explicit empty lists where
appropriate. Each row has exactly `id`, `line`, `method`, `federal`,
`subtractions`, `additions`, `resident`, `nonresident_ca`, `nonresident_elsewhere`.
IDs are opaque alphabetic labels; never use identity or account numbers.
Methods are `actual`, `payroll_workdays`, `entity_statement`,
`reviewed_worksheet`. Use printed ScheduleCA line codes from
`CA_NR_INCOME_LINES` and `CA_NR_ADJUSTMENT_LINES` in the engine. The three source
amounts are disjoint and sum to federal minus law subtractions plus additions.
Keep raw cents; the builder retains form-rounding differences separately.

Allocate actual compensation using service/source evidence and review stock,
entity, capital-loss, special-accrual and adjustment worksheets. Do not divide
annual income by residence days. A move is not a columnB law subtraction.
Federal income/adjustment rows must tie to the checked federal return.
Social Security, taxable state refunds and unemployment are excluded under CA
law. Legacy Schedule1 line7 can include family benefits: reconcile B7 to the
benefits workpaper, exclude UI and reviewed exempt family benefits, and allocate
any explicitly taxable family remainder from actual source facts. IRA receipts
while nonresident cannot enter the CA-source column. Review
qualified retirement protection separately from nonqualified deferred
compensation rather than treating every Form1099-R alike.

Keep only `california_540nr` for CA in `result.json`; metadata records
`ca_return_type: "540NR"`. Remove the resident Form540 scaffold, but keep
`schedule_ca_itemized` while it carries `charitable_ledger`: it is the CA
charitable carryover's rollover source.
Set `ca_filing_status` to the confirmed federal status. Supply every raw field
in its template; null is unanswered, never a worked zero:

- `form_inputs`: reviewed component amounts on lines41,50–53,61,73,
  84–87,91,102,120,122 and `claim_of_right_credit`. Positive line41 special
  distribution tax is outside this reviewed branch; line52 is MFS-only.
  Federal AGI on derived line13 controls the exemption limitation. Refundable credits,
  renter/childcare credits, other taxes and ISR need their own NR worksheets.
  General credit codes enter `profile.ca_credits.other_credits`; do not repeat
  prorated codes170/173/163 there. Never enter derived payments81–83 or penalty123.
- `w2_ca_wages`: `confirmed: true` and `sources` rows with `source_id` and
  nonnegative `amount` for actual CA W-2 Box16 wages, including a worked zero
  for each W-2 without CA wages. Cover every active W-2 ledger source exactly
  once. These derive line12; neither federal AGI nor allocated annual wages
  substitutes for the source boxes.
- `deduction_inputs`: `method: "standard"|"itemized"|"dependent_worksheet"`,
  `itemized_before_limitation`, `itemized_after_limitation`, `dependent_filer`,
  `dependent_standard_deduction` (null unless the dependent worksheet applies).
  Supply reviewed ScheduleCA PartIII amounts; annual deductions precede PartIV
  allocation. They are not resident-month deductions.
- `amt_inputs`: signed reviewed `world_adjustments` and `ca_adjustments`,
  nonnegative `iso_world`, `iso_ca`, and signed `itemized_amt_disallowances`.
  These are distinct worldwide/source worksheets. Standard-deduction addback
  and itemized-limitation reversal are derived; never add them again.
- `iso_basis_reconciliation`: signed `adjustment`, `confirmed: true`,
  `source_ids`. Worldwide CA ISO equals the ledger's `iso_bargain` total plus
  this reviewed CA basis/CQSO difference. Nonzero differences require supporting
  ledger IDs. Confirm a worked zero when absent. Source ISO requires its own
  service-period worksheet; never prorate it by residence months.
- `schedule_ca_adjustments`: the existing common-conformity and explicit
  other-adjustment workpaper. Also list `conformity_manual_rows` with exactly
  `line`, signed `subtractions`, signed `additions`, including an empty list.
  These explain each additional manual law difference by printed line (C
  adjustment rows reverse its AGI sign). Known ledger-supported components must reconcile
  to raw allocation rows; a general review does not waive them.

Existing CA eligibility counts, direct-owner PTET source/vintage proof,
qualified opening AMT credit, payment destination/year/ownership and FTB5805
questions still apply. FTB5805 uses NR line32 CA AGI, not worldwide line17.
Prior `full_year` means a twelve-month taxable year, not twelve-month residency.
For nonregular methods use the confirmed external worksheet or eligible
FTB-billing election with its disclosure. Use tax is reviewed and paid
**separately to CDTFA**; NR line91 is the health-coverage penalty.

## Build, review and check

Run the normal `sh agents/lib/run.sh -m agents.lib.return_builder` write command
to derive a draft. Review the source allocation, conformity, deduction, AMT and
credit worksheets against the covered-year official forms. Then set
`state_filings.CA.worksheet_review` to the current `allocation_input_digest`
as `input_digest`, plus true `sources_reviewed`, `conformity_reviewed`,
`deductions_reviewed`, `amt_reviewed`, `credits_reviewed`. Do not confirm before
review or copy an old digest. Rebuild, then run the normal builder `--check`
and `agents.lib.tie_out` through the launcher. Both refuse stale review or
changed source/worksheet inputs, including when federal arithmetic is waived.

Carry the builder's source/scope/penalty notes into `meta.limitations` through
the normal compute flow. Transfer the derived Form540NR, ScheduleCA and
ScheduleP outputs; never type the final tax/AGI. Whole-dollar printed lines and
four-decimal rates retain source-cent and credit-rounding workpapers. The filing
package identifies applicable official attachments and separate CDTFA payment.
Full-year state projections/scenario/Roth/withholding forecasts do not allocate
this return and must show their explicit 540NR limitation.
