# State effects in income planning

`tax-scenario` and `tax-roth-convert` share
`tax_computations/state/planning.py::price_state_points`. The initial reviewed
form year is **TY2025** for full-year CA, NY and NC households. A fetched
TY2026 rate does not certify another year's form structure. Unsupported states,
excise regimes, moves, second-state residency, IT-203, mixed spouses and local
allocations remain named limitations. Mailing addresses establish none of this.

The federal anchor remains Form 1040 line 24. State income uses the **actual
return's** federal AGI plus the federal model's AGI change, rather than replacing
the return's AGI with the simplified model's baseline. Each state excludes the
new taxable Social Security through its own modification. State liability must
reproduce the recorded return within one dollar of whole-dollar transcription;
only that independent state anchor receives the state marginal effect. A sweep
is entirely priced or entirely unavailable, with empty points and null combined
costs when unavailable. Unknown never means zero.

The reported liability excludes refundable credits, withholding, estimates,
sales/use tax, penalties and interest. Consequently combined **liability cost**
does not price lost refundable credits or estimate a settlement balance. Yonkers
credits used inside its surcharge worksheet are a separate issue and refuse
unless the reviewed worksheet claims none. Existing federal caveats still apply.

## Required local evidence

Keep `result.state_planning_inputs` absent/null until reviewed; it is a runtime
object, never a contributor fixture with real data. Record its review and missing
answers in `answers.md`, including in headless runs. No default is an answer.
The common fields are:

- `tax_year`, `state` (CA/NY/NC), canonical `filing_status`, `residency: full_year`.
- `scope_confirmed: true`: the reviewer verified complete household residency,
  all additions/subtractions and raw deduction inputs, no omitted state tax or
  nonrefundable credit, and no moving modification beyond Social Security or
  the expressly classified NY pension change. CA also requires no dependent
  standard-deduction worksheet, RDP/pro-forma federal AGI or special tax method.
  NC requires no uncomputed Schedule S/credit provision. This is a substantive
  review, not a flag to clear a refusal. Retained profile allocation flags or an
  IT-203 return contradicting the scope refuse.
- `income_treatment`: each changed normalized income key must be `taxable`
  after independent covered-year source/classification review. The qualified
  dividend split changes no state gross income. For NY alone, an IRA/pension
  delta may be `ny_pension`, with `retirement_owner: primary|spouse` and a reviewed
  per-owner eligible pension entry. This normalized key combines IRA/pension;
  never infer ownership, eligibility or government retirement from its name.
- `retirement_basis_matches_federal: true` when changing retirement income:
  independently confirm that the taxable portion and basis used by this run
  match the state's. Federal conformity metadata alone is insufficient.

Business/above-AGI/below-AGI deduction deltas need additional conformity and
local worksheets and are unavailable in this first scope. Existing modifications
and source amounts remain fixed under the review. Negative retirement changes
cannot reduce an eligible owner's qualifying income below zero. Saved scenario
stacks bind the state plan, law, original income evidence and recorded state
return by a digest; changing them requires restarting from the return.

## State-specific evidence

**CA**: `ca` contains positive `additions`, `subtractions_except_ss`,
`personal_blind_senior_count`, `dependent_count`, `no_amt_preferences_confirmed`
and `itemized`, the raw keyword inputs to `compute_ca_itemized` (zero values and
an empty object only after completeness review). Its own election, federal-AGI
medical/miscellaneous floors, gift caps and itemized phaseout rerun at each
point. MFS must explicitly pass its spouse election/status inputs. Vintage gift
worksheets cannot travel to another AGI. General Schedule P/credit limitations
remain unavailable: every point must clear the reviewed no-preferences screen
and fetched Form 540 Box C ceiling. Special credits/taxes remain outside scope.

The optional fetched CA cache blocks are `state.tax_table` (covered-year ceiling
and sparse published `rows` with `min`, `max`, and `tax` by lower-case filing
status), `state.exemption_phaseout` (`threshold` and `band` by all five statuses,
`reduction_per_step`), and `state.amt_screening` by all five statuses. Rows use
inclusive minimum/exclusive maximum; transcribe the published range accordingly
and include the covered ceiling's final row. Missing rows refuse; do not invent
a midpoint rule. Above the ceiling, use the fetched contiguous rate schedule.
The exemption credit re-prices the line-32 per-head ceiling steps independently
for personal/blind/senior and dependent credits. All new amounts are fetched,
source-pinned and covered-year checked, never engine defaults. These blocks are
optional for the return freshness walker and required by this planner.

**NY**: `ny` contains the actual `additions` and `subtractions` dictionaries for
`compute_ny_agi`, `dependent_count`, `claimed_as_dependent`, and `itemized`
(IT-196 pre-adjustment amount, charity, college deduction and MFS election).
`it196_fixed_base_confirmed` verifies that lines 1-45 have no moving
medical/miscellaneous/gift/investment-interest limits across the proposed points;
otherwise those raw worksheets need separate modeling. Lines 46-49, §615
reductions/charitable replacement and the itemize-or-standard election rerun.
The tax table, published bases and §601 recapture use the existing engine.
The household credit re-selects its band on federal AGI, not NYAGI.

`ny.local` requires independent boolean `nyc_resident`, `yonkers_resident` and
`yonkers_nonresident_earnings`, plus `mctmt` with both actual zone net earnings.
Zone earnings stay fixed; SE deltas are refused. NYC requires full-year evidence
(`nyc_resident_spouses: 2` on MFJ, otherwise `nyc_resident_full_year: true`) and
`charitable_gifts_trust_deducted`. Its taxable base, fetched table/schedule and
household credit reprice. Yonkers requires the parallel full-year evidence,
`yonkers_worksheet_credits: {}` (none claimed) and `star_reconciliation: 0`;
the existing surcharge reruns on state tax. Y-203 and IT-360.1 remain unavailable.
Use tax is removed from line 60, or use the independent lines 46 + 58.

**NC**: `nc` is the complete `compute_nc_d400` keyword input object, including
all mandatory Schedule S amounts/retirement presence flags, qualifying-child
count, actual NC Schedule A source inputs (or reviewed `null`), dependent and
standard-deduction eligibility, and the MFS spouse election. The existing form
engine re-prices the child deduction, medical floor, election and flat tax.
Other-state D-400TC allocation credits need separate modeling and refuse. State
liability is line 17, excluding line 18 use tax and settlement.

## Sources and verification

Form structure reviewed against the
[TY2025 FTB Form 540 booklet](https://www.ftb.ca.gov/forms/2025/2025-540-booklet.html),
[NY maintained contract](state-module-ny.md) and
[NC maintained contract](state-module-nc.md). These links do not authorize
carrying annual figures into a later year. Setup fetches each needed amount
from the filing year's primary authority with a source pin.

Tests use published NC/NY worksheets and clearly synthetic CA parameters.
Hand calculations cover child cliffs, medical/election changes, exemption
phaseout, retirement ownership caps, Social Security exclusion and local tax;
stack/inverse, partial Roth rungs/basis, malformed data, baseline mismatches,
unreviewed years and secured CLI publication exercise the boundaries.
