# AMT and Medicare premium exposure

These are reviewed **full-year forecasts**, separate from YTD payroll inputs.
Read existing answers first and resolve unanswered items in `answers.md`.
Never copy last year's preferences, set an unanswered amount to zero, or write
a projected AMT credit into the return or rollover records.

## Form 6251 forecast

Ask about every Form 6251 preference, AMT basis difference and special method,
using the covered year's [IRS instructions](https://www.irs.gov/instructions/i6251).
The supported path includes unsold ISO exercise spreads, specified private
activity bond interest, the standard/SALT and senior addbacks, reviewed regular
and AMT foreign-tax-credit worksheet outputs, and qualified opening Form 8801
credit. Other preferences require a separately reviewed model: tax refunds,
investment interest, depletion/NOL, QSBS, AMT basis or capital-gain differences,
depreciation/passive losses, business preferences, foreign earned income,
kiddie tax, Schedule J or Form 4972/8978. Record these in `unsupported_items`;
any nonempty list refuses the forecast. An acknowledgement does not compute it.

Complete `projection_inputs.json.amt_projection` with:

- `tax_year`, canonical `filing_status`, `full_year_inputs_confirmed: true`, and
  `federal_agi_at_review` matching the annualized income less adjustments.
- An explicit `unsupported_items: []` only after the complete screen above.
- Explicit nonnegative full-year numbers, including worked zeros, for
  `iso_bargain_element`, `private_activity_bond_interest`, `salt_deducted`,
  `senior_deduction`, `section_68_reduction`, `regular_foreign_tax_credit`,
  `amt_foreign_tax_credit`, and `prior_minimum_tax_credit`.

`salt_deducted` and `section_68_reduction` must match the repriced Schedule A
when it is supplied. For a declared aggregate itemized deduction, review its
underlying worksheet at this forecast's AGI and record both amounts explicitly;
the declared deduction is **after** section 68, SALT is before that reduction.
Both amounts are zero on the standard-deduction path. Section 68 does not apply
for AMT ([26 USC 56(b)(1)(E)](https://uscode.house.gov/us/usc/119-121/t26/s56/)).
The engine reverses that haircut and preserves a negative line-1 refigure when
deductions exceed income. `senior_deduction` is only the applied senior portion
of Schedule 1-A, never its tips/overtime/car-loan total.

Fetch `federal.amt` for the income year: status-keyed exemptions and phase-out
starts, the 26/28 breakpoint, and an optional fetched phase-out-rate override.
The core uses the year's statutory phase-out mechanics and MFS recapture.
Review FTC and Form 8801 eligibility and limits; the opening credit must agree
with the profile carryforward. Missing indexed law or a present unfinished
worksheet stops rather than inventing zero AMT. A null worksheet instead gives
an explicit **AMT NOT MODELED** banner and a lower federal estimate. Do not
describe that estimate or its payments as complete AMT coverage.
The fallback is unavailable with a positive opening minimum-tax credit:
omitting a usable credit could instead overstate payments. Complete the full
AMT/credit worksheet in that case. Foreign tax paid also needs an explicit
reviewed credit, but can use the independent regular-credit path below.

AMT and allowed credits flow into federal total tax, balance, the current-year
safe-harbor prong, vouchers and W-4 guidance. Medicare premiums never do.

## Regular foreign tax credit without an AMT forecast

When AMT remains unmodeled, supply a reviewed allowed regular foreign tax
credit in `projection_inputs.json.foreign_tax_credit_projection`. This is a
worksheet output, not an eligibility calculator or an estimate from foreign
tax paid. Complete the covered year's Form 1116 or review eligibility for its
filing exception using the [IRS instructions](https://www.irs.gov/instructions/i1116).
The path accepts either reviewed method; it does not decide the election or
encode an election ceiling. Review income categories, limitations, carryovers,
credit-versus-deduction treatment and interaction with other credits. Do not
deduct and credit the same taxes. Record the workpaper, source year and review
in `answers.md`; never clear `flags.has_foreign_tax_paid` to bypass a refusal.

The object requires `tax_year`, canonical `filing_status`,
`full_year_inputs_confirmed: true`, `federal_agi_at_review` (annualized gross
income less adjustments), `taxable_income_at_review`, `regular_tax_at_review`
(income tax before credits), and `regular_foreign_tax_credit`. Each amount is
a full-year figure, never annualized from YTD or copied from a prior return.
The last is the allowed nonrefundable credit after the reviewed limits,
including an explicit worked zero if no credit is allowed. The review's income
and tax frame must match this projection to cents; rework the worksheet after
income or deductions change. Incomplete, stale, nonfinite or excessive credits
refuse instead of silently dropping them.

Get that frame from the compute command with `--review-frame` in place of
`--json`. It prints only the tax year, status and the three `*_at_review`
figures, with no total or payment, and runs while the missing credit still
blocks the full report. Work Form 1116 at those figures; never clear the flag
to see them. A positive credit, here or in `amt_projection`, requires
`flags.has_foreign_tax_paid: true`, the flag the return's Form 1116 keys on.

Use this object only when `amt_projection` is null. With a full AMT worksheet,
enter the regular credit there instead; supplying both refuses to prevent two
sources for the same credit. A positive prior-year AMT credit still requires
the full worksheet. The regular FTC reduces income tax, balance and payment
targets without offsetting SE tax, Additional Medicare or NIIT. Keep the
**AMT NOT MODELED** banner and payment caveat: a reviewed regular FTC does not
establish zero AMT or an AMT foreign tax credit.

## IRMAA premium forecast

Income year plus two is the normal premium year. A different year can apply
after an SSA life-event redetermination or unavailable return data; those cases
need a separately reviewed estimate. See [SSA's MAGI policy](https://secure.ssa.gov/POMS.NSF/LNX/0601101010).

Complete `irmaa_projection` with `tax_year`, canonical `filing_status`,
`full_year_inputs_confirmed: true`, `federal_agi_at_review`, explicit nonnegative
`tax_exempt_interest` and `excluded_income_addbacks` (sections 135/911/931/933),
and an integer `beneficiaries` count (0–2 joint, 0–1 otherwise). Confirm expected
premium-year Medicare enrollment; zero requests no premium estimate. For a
positive count, `full_year_parts_b_and_d_confirmed: true` confirms the combined
annual surcharge applies. One-part or partial-year coverage needs a separate
worksheet, since a full Part B+D amount would overstate it.
Tax-exempt interest includes any private-activity-bond interest already known
to the AMT forecast; contradictory worksheets refuse instead of dropping it.

`lived_apart_all_year` is null except for MFS, where it must be true or false.
QSS and HOH use the unmarried Medicare table. MFS living apart uses that table;
MFS living together needs its own two-tier table. The five-tier unmarried/joint
tables cannot stand in for it. [SSA publishes all three](https://secure.ssa.gov/POMS.nsf/lnx/0601101020).

Fetch the **premium year's** `federal.irmaa` into that year's separate
`tax_law_<premium_year>.json`, pinning its authoritative source in `_sources`.
The CLI reads that cache only for a requested premium forecast. The programmatic
`build_report` argument is `irmaa_tax_law`. Do not relabel current premiums as
verified future rates. If the future year is unpublished, leave the estimate
unmodeled; the report still identifies projected MAGI and the expected lookback.

From premium year 2019 the table has five surcharge rows for `single`/`mfj`,
two for cohabiting `mfs`; before 2019 ordinary tables have four, and 2018 MFS
has one. All require strictly increasing finite positive
thresholds and annual surcharges, with no omitted/duplicate rows. Under
[42 USC 1395r(i)(5)(B)](https://www.ssa.gov/OP_Home/ssact/title18/1839.htm), indexed
boundaries round to whole thousands. Store the printed boundary, never its
first dollar. Lower tiers start **above** the threshold, only the statutory top
starts **at** it. The frozen [2018](https://www.cms.gov/newsroom/fact-sheets/2018-medicare-parts-b-premiums-and-deductibles) and [2019](https://www.cms.gov/newsroom/fact-sheets/2019-medicare-parts-b-premiums-deductibles) tables retain half-thousand boundaries; pre-2019 top rows also begin strictly above their threshold. Empty means
unmodeled; a malformed populated table refuses rather than pricing a false zero.

The report gives annual Part B+D surcharge above the base and plan premiums,
per beneficiary and for the declared household count, separately from taxes.
It is a planning estimate, not an SSA determination.
