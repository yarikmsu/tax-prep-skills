# New York — maintained IT-201 and IT-203 contracts

Use this reference with `docs/contributing/state-module-playbook.md` and
`docs/contributing/tax-law-cache.md`. Annual figures come from the filing-year
authorities through tax-setup; this document defines structure and scope.
Implementation history is in `docs/changelog/epic-Z.md` and `epic-AA.md`.

## Scope and questionnaire

IT-201 covers a full-year NY resident, with full-year NYC resident tax,
MCTMT and the scoped Yonkers computations below. A declared TY2025 IT-203
variant covers nonresident/part-year NY and NYC IT-360.1 using reviewed actual
source allocations. Mailing address alone selects neither residency nor form.
Residency assertions follow `docs/contributing/residency-guardrails.md`.

| # | Question | Current contract / authority |
|---|---|---|
| 1 | Starting point | Federal income and adjustments, recomputed with NY modifications; IT-201 lines 19–33 and IT-203 all-source/NY-source columns. IT-201-I / IT-203-I. |
| 2 | Modifications | IT-225 coded additions/subtractions, public-employee W-2 box 14 add-backs, government pensions, per-person pension exclusions and 529 contributions have separate inputs. Tax Law §§612–613. |
| 3 | Credits | Dependent exemptions under §616 reference IRC §151(c); no taxpayer/spouse exemption. Household credit and refundable payments have different ordering and status groups. IT-201-I credit tables and payment lines. |
| 4 | Locals | NYC, scoped Yonkers and geographic-zone MCTMT are separate computations. IT-201 lines 47–58; IT-360.1 for the declared part-year variant. |
| 5 | Surtaxes | Benefit recapture is integrated into tax, rather than an optional caller adjustment. Tax Law §601(d)–(d-7). |
| 6 | Estimates | Equal IT-2105 quarters, combined NYS/NYC/Yonkers/MCTMT liability, NY's own fetched shares and NYAGI thresholds. IT-2105 / IT-2105-I. |
| 7 | Payroll | Public-employee retirement/cafeteria additions follow parsed box 14 inputs. Do not infer a PFL/DBL cap or add a payroll forecast from the income-tax schedules. |
| 8 | Withholding | IT-2104; IT-2104.1 for nonresidents. Actual state/NYC/Yonkers withholding must reach the selected return variant. |
| 9 | Extension | Separate IT-370; federal extension alone is insufficient and payment remains due. IT-370 instructions. |
| 10 | PTET | IT-653 owner credits pair exactly with IT-225 A-219/A-222 add-backs; refundable payment treatment. NY entity pricing/window remains shared contributor tooling. |
| 11 | Community property | NY is absent from the community-property set; another domicile's Form 8958 gate still applies. |
| 12 | Itemized | Verified IT-196 line 45, independent of the federal election, followed by §615 reductions and charitable-only regimes. Federal Schedule A total is not this input. |
| 13 | Tax math | Published tax table, published-base rate schedule and NYAGI benefit recapture are distinct regimes. IT-201-I tax computation worksheets. |

## Cache and IT-201 arithmetic

`create_state_template("NY")` declares `type: graduated_income`.
`state.brackets` uses schedules `a`/`b`/`c`, with
`bracket_schedule_by_status`: QSS shares MFJ's schedule and MFS shares single's.
Deduction pairing is also QSS/MFJ and MFS/single; `single_dependent` is a
separate deduction key. The cache guide and tax-setup fetch reference enumerate
the exact keys, sources and credit-specific groupings. Do not assume all blocks
use the same filing-status map.

Fetch published cumulative bases and tax-table bases as well as rates.
The table uses the row midpoint and whole-dollar half-up rounding; summing
rate bands or taking a row floor changes the published result. Recapture uses
both NYAGI and taxable income, multiple benefit tiers and a top flat-rate
regime. Its phase-in fraction is rounded as the worksheet directs before the
whole-dollar tax line. A negative NYAGI is valid, rather than an invalid input.

IT-225 additions (`A-`/`EA-`) and subtractions (`S-`/`ES-`) are nonnegative
amounts with direction checked against the code. Missing fetched disallowed-code
checks emit a named eligibility warning. Pension eligibility is explicit;
spouses cannot pool unused exclusions, and government pensions use their own
full-exclusion line. The 529 cap uses its own two-key grouping.

`compute_ny_itemized` takes verified IT-196 line 45 and applies four mutually
exclusive NYAGI regimes. The second reduction includes the whole first
reduction before its own phase-in. The charitable-only boundary and fetched
charitable tiers must agree, and charitable totals are required there.
IT-196 lines 1–45 remain a manual worksheet, not an engine computation.

Household credits use federal AGI, the item-C dependent bar and three groups:
single, MFS, and MFJ/HOH/QSS. City tables share this grouping. State
nonrefundable credits are capped before line 45's verified other taxes;
city nonrefundable credits are capped before MCTMT/Yonkers are added.
Line 39 is state tax before credits, line 46 state tax after credits, and
line 60 the combined liability. Refundable credits are payment inputs;
the engine settles them in form order rather than treating them as reductions
of the tax base. College credit and tuition-itemized elections cannot both apply.

NYC uses its own tax table/rate schedule. The charitable-gifts-trust add-back
and mixed-spouse residence rules must be answered before city pricing.
MCTMT is a cliff on whole earnings per geographic zone, tested separately
for each person/zone; it is not a threshold deduction or an income-band list.
Whole-year Yonkers surcharge and scoped nonresident Form Y-203 have their
own fetched rates, exclusion/credit worksheets and residency guards. Joint
Y-203 and mixed/part-year household cases outside those computations refuse.

`new_york_it201` and its `nyc` block use literal form-line names. Registry
evidence includes deduction and NYAGI (negative counts), not parsed withholding.
Line 59 use tax defaults to `null` until answered; it is not silently zero.
Local-tax method witnesses distinguish computed zero from a typed figure.
Template/output-key and tie-out tests pin that contract.

## Declared IT-203 / IT-360.1 variant

`profile.state_filings.NY` names IT203, covered year, residency and NYC periods,
joint identical-period confirmation and a completed obligation inventory.
It selects NY law even with a different mailing address. Move/source blocks
clear only if every other state is a year-valid no-income-tax state requiring
no return. Foreign moves, statutory second residency and other taxable-state
returns retain their gates/acknowledgments. MFS, differing joint periods,
multiple resident periods, special IT-230 percentages and IT-203 Yonkers refuse.

Income/adjustment rows split into disjoint resident, nonresident-NY and
nonresident-elsewhere amounts totaling the federal amount. Raw all-source AGI
must tie to federal AGI before aggregated form lines are rounded. Signed
elsewhere losses can make NY income exceed the all-source column. Convenience
sourcing, special accruals and modification/credit worksheets require explicit
review; the engine never substitutes day-based wage allocation.

The NY state deduction is annual. State tax and benefit recapture use
all-source income, then pre-allocation credits, the NYAGI ratio rounded
half-up to four decimals, and post-allocation credits. The ratio can exceed
one; either column at or below zero gives zero. NYC IT-360.1 uses actual
resident-period income, rounded full-month deduction/exemption proration and
the City rate schedule, not the IT-201 table or annualized income.
City itemized and credit worksheets are verified inputs.

`new_york_it203` has a `computed_tax_year` witness, source columns, ratio,
lines 32–72 and an embedded `nyc_it3601` worksheet. Two computed NY variants
refuse rather than sum. Actual state/NYC/Yonkers withholding goes to lines
62/63/64. Estimated-tax penalties reduce line 67; other penalties/interest
remain on lines 72/70 and do not reduce that refund.

Generic NY projections and IT-203 true-up forecasts skip with a named
allocation/scope reason; zero projected state tax is not zero NY liability.
Reviewed full-year IT-201 forecasts use the dedicated
[`state_projection` contract](state-planning.md#mid-year-payment-forecasts-aa43)
for NYS/NYC/Yonkers/MCTMT liability and equal-quarter IT-2105 payments.
IT-201 true-up reads the completed combined liability and NY's own fetched
safe-harbor inputs. IT-2105 calendar and IT-370 labels follow the selected form.
Dates use the documented state-calendar fallback and require annual verification.

## Sources, tests and limits

The original review used the TY2025
[IT-201 instructions](https://www.tax.ny.gov/pdf/2025/inc/it201i_2025.pdf),
[IT-203](https://www.tax.ny.gov/pdf/2025/inc/it203_2025_fill_in.pdf),
[IT-203 instructions](https://www.tax.ny.gov/pdf/2025/inc/it203i_2025.pdf),
[IT-360.1](https://www.tax.ny.gov/pdf/2025/inc/it360_1_2025_fill_in.pdf) and
[IT-360.1 instructions](https://www.tax.ny.gov/pdf/2025/inc/it360_1i_2025.pdf).
These establish reviewed form structure, not permission to reuse annual
figures for a later filing year. Setup fetches the covered year's authorities.

Hand worksheets test table/recapture boundaries, itemized reductions,
per-spouse pensions, credit caps, geographic MCTMT, Yonkers scope, settlement,
source ratios above one, NYC proration and rounding. Integration tests cover
registry, variant competition, template outputs, payment/local evidence,
filing attachments, cache years, false coverage claims and the launcher.

IT-196 source assembly, other-state credit worksheets, refundable-credit
amounts, use tax and eligibility judgments remain explicit manual inputs.
Unsupported residency/local variants retain named gates. PDF/package output
is a reviewable summary, not an electronic filing artifact.
