# Reviewed multi-year Roth plan

This branch uses the skill's engine preflight first. It requires a computed
`result.json`, matching current-year profile, and a reviewed plan spanning one
through ten consecutive years, starting in the return year or the following
year. A prior-return import seed is not a computed baseline.

## Interview and record assumptions

Write `scenarios/roth_plan_{name}.input.json`, owner-only. Validate the short name
as letters, digits, underscores or hyphens, starting with a letter. Secure the
workspace root and `scenarios/` with `secure_fs`; refuse linked/junction paths.
Never save identity data or full SSNs in a plan. In headless runs use only
explicit answers already in `answers.md`; a missing personal fact or assumption
stops. A worked zero is a value; a missing key is not zero.

The JSON contract is `schema: "roth-plan/v1"`, `name`, `start_year`,
`assumptions_reviewed: true`, `ira` and `years`. No undocumented keys or implicit
defaults are accepted.

`ira` contains:

- `owner`: `primary` or `spouse`, one individual's traditional/SEP/SIMPLE pool.
- `traditional_balance`, `roth_balance`, `basis`: explicit starting amounts.
- `growth_rate`: the signed annual forecast fraction, shown on each row. It is
  applied after conversion: the grown remainder is both Form 8606 line 6 (the
  Dec 31 value) and the next year's starting balance.
- `basis_source`: `opening_profile` for a filing-year plan;
  `closing_form_8606` for the following year; or `reviewed_worksheet` when the
  filer supplies an independently reviewed worksheet. Resolve conflicts with
  known individual balances before proceeding. A following-year plan cannot
  start below the owner's closing Form 8606 line 6 plus line 4 (the closed
  year's contributions made January to April, which line 14's basis already
  counts), whichever basis source it uses; later deposits only add to it. A
  filing-year plan is refused when the return already records the owner's
  contributions, distributions or conversions (on a legacy return without
  individual records, any household ones or any Form 1040 line 4a): plan from
  the following year. Where the return records the owner's Dec 31 value, the
  filing year grows at the return it realized, and the forecast rate applies
  from the next year.
- `basis_confirmed`, `regular_worksheet_confirmed`,
  `no_other_ira_flows_confirmed`: all true. Confirm no new IRA contributions,
  other distributions, rollovers, inherited IRA treatment or tax withholding
  from the IRA. Conversion tax is paid from outside the IRA.

Every annual object requires ALL of these fields:

| Fields | Meaning |
|---|---|
| `year`, `filing_status` | Consecutive year; `Single`, `MFJ`, `MFS`, `HOH` or `QSS`. |
| `wages`, `other_ordinary_income`, `self_employment_net` | Annual income before the conversion. Other ordinary income excludes separately entered qualified dividends, net long-term gains, Social Security and SE net income. It includes pensions, taxable interest, nonqualified dividends and ordinary short-term gains. The filing year copies the return, Schedule 1 losses included; a forecast year cannot be negative: enter a capital loss in `capital_gains`, where the §1211 capital-loss limit applies, and an ordinary loss in `adjustments`. |
| `qualified_dividends`, `capital_gains` | Preferential dividends and net long-term gain/loss, including separately reviewed carryforward effects. No automatic capital-loss/QBI carryover between plan years. |
| `adjustments` | Fixed non-SE above-AGI deductions. The engine calculates deductible half of SE tax. Business-attributable health/retirement adjustments and changing deduction phaseouts need separate modeling. |
| `deduction`, `additional_standard_deduction` | `standard` reads this year's selected law; enter any age/blind addition explicitly for that year. Otherwise give the complete deduction amount and an additional amount of zero. |
| `spouse_itemizes` | Explicit boolean for MFS, null otherwise. MFS cannot choose standard when the spouse itemizes. |
| `ctc_children` | Reviewed eligible-child count for THAT year, never last year's count copied forward. The credit is priced as nonrefundable; each such year notes that a lost refundable additional child tax credit can raise the cost. |
| `social_security`, `social_security_inputs` | Gross benefits. When positive, inputs are `tax_exempt_interest`, `excluded_income_addbacks`, `regular_worksheet_confirmed: true`. Otherwise inputs are null. Only the regular worksheet is supported; no repayment/lump-sum method. |
| `net_investment_income` | The explicit NIIT subset already included in income, not another income addition. |
| `niit_magi_addbacks`, `irmaa_magi_addbacks` | Separate reviewed amounts: NIIT and Medicare MAGI are different concepts. Medicare MAGI adds tax-exempt interest back, so the IRMAA addbacks cannot be below the year's `tax_exempt_interest` or the filing year's Form 1040 line 2a. |
| `state`, `state_inputs` | `CA` or null for federal-only modeling, with state cost left unknown. For CA, supply that year's reviewed `state_planning_inputs` contract or null to report state cost unmodeled. Filing-year data come from the computed return and must agree. |
| `medicare_enrollees_two_years_later`, `medicare_months_two_years_later` | Zero through two beneficiaries, and a matching list of each beneficiary's enrollment months. Two beneficiaries require MFJ for the lookback year; other returns can model one person's premium. Empty list for no beneficiaries. Partial years are explicit. |
| `medicare_parts_b_and_d_confirmed` | Confirm both parts for beneficiaries whose combined B/D surcharge is priced; the cache holds a combined amount. |
| `medicare_lived_apart_all_year` | Explicit boolean for MFS in the income/lookback year, null otherwise. |
| `rmd_not_required_confirmed` | True for every year. End the plan before required distributions begin; do not infer RMD age from an address or incomplete birth record. |
| `other_federal_effects_absent_confirmed` | True only after reviewing AMT, other credits, special/phaseout deductions and other federal effects outside the simplified keystone. An affected year needs separate modeling. |
| `law`, `premium_law` | Explicit law selections below. Premium law can be null to leave Medicare cost unmodeled. |

Finally, each year contains exactly one of:

- `conversion`: a gross amount, refused if it exceeds that year's projected IRA.
- `fill_to: {"kind": "bracket", "rate": ...}`: fill the ordinary taxable-income
  bracket under the selected law. The basis fraction and benefit-taxability
  feedback affect how much GROSS conversion fits. A selected open top bracket
  permits the remaining balance.
- `fill_to: {"kind": "irmaa", "tier": ...}`: stay below a premium-year surcharge
  tier. Requires a complete premium-year table. The top boundary is inclusive,
  so the search stops a whole dollar before it, since a return rounded to
  dollars carries $0.99 short as the boundary itself; lower tiers start above
  their boundary. If the baseline already exceeds the edge, no conversion fits
  that target.

Filing-year income, deduction, children, status and Social Security facts must
match the recorded return. Its federal/state point uses the existing ladder
unchanged. Non-SE adjustments or NIIT addbacks that its anchor cannot represent
require separate reviewed modeling. Do not edit the return to make a plan fit.

## Covered-year law and named forecasts

`law: {"mode": "fetched"}` requires `tax_law_{year}.json` declaring that year,
with a FRESH verdict. A missing, stale or incomplete cache refuses the plan.
Follow `/tax-setup`'s live authoritative-source contract; never infer a return
figure from another year. The anchored filing year must use fetched law.

For an unpublished future year, offer these explicit alternatives and record
the user's choice. Offer held law first, without silently selecting it:

- `{"mode": "hypothetical", "basis": "held_at_YEAR"}` holds that year's fetched
  figures unchanged.
- `{"mode": "hypothetical", "basis": "indexed_from_YEAR_at_RATE"}` applies the
  named annual fraction to federal ordinary bracket boundaries, base standard
  deductions, preferential boundaries, SS wage base and Medicare thresholds/
  surcharges. Rates, CTC parameters, statutory NIIT/Additional Medicare gates
  and other law remain held. Medicare boundaries follow 42 USC 1395r(i): the
  unmarried amounts are indexed and rounded to $1,000, the $500,000 top only
  from 2028 (indexed from 2027, and from a base before 2028 never below it);
  joint amounts are twice the unmarried ones and
  the top 150%; cohabiting MFS uses the unmarried threshold and the top less
  it. CA inputs are held, not automatically indexed.

The basis cache must itself be FRESH and cannot be a later year. Both are
in-memory forecasts, labelled on each row and in the header. Never save one as a
tax-law cache, a computed return or rollover record. An explicitly fetched year
never falls back to a hypothetical because it is unavailable.

`premium_law` uses the same choices for `year + 2`, not the income year's table.
Null leaves premiums unmodeled where beneficiaries exist. Medicare is a side
cost and does not enter federal tax. No beneficiaries is an explicit zero cost.
Surcharges assume the entered B/D months and no successful appeal or other relief.

CA is computed only for years in the state planner's reviewed set, with that
year's confirmed residency, deductions, retirement basis and other raw inputs.
AA84's unreviewed future years remain unmodeled even if rates are populated.
Missing costs stay null with a banner; totals do not treat them as zero.

## Run and review

After the filer has reviewed the annual assumptions, set `assumptions_reviewed`
to true and restrict the input with `secure_fs`. Run:

```bash
sh agents/lib/run.sh -m agents.lib.roth_plan --name {name}
```

The engine writes `scenarios/roth_plan_{name}.json` and `.md` through secured
writers. It does not change `result.json`, profile, law or rollover records.
Read the local Markdown and present its yearly table, law/growth assumptions,
totals and excluded-cost notes. The JSON preserves annual inputs, rate inputs,
individual Form 8606 worksheets, Medicare tiers and source-law digests.

The filing-year row is labelled computed; later rows are forecasts even when
their law is fetched, because their income is assumed. Basis remaining carries
to the next owner's worksheet. The balance after conversion grows into next
year's starting balance; the Roth balance accumulates converted amounts at the
same explicit rate. No distribution or spending strategy is implied.

Record the plan name, reviewed horizon and assumption decisions in local
`answers.md` without duplicating dollar values or identity. Restrict any manual
write using `sh agents/lib/run.sh -m agents.lib.secure_fs answers.md`.
