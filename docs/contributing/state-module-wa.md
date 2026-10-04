# Washington — maintained capital-gains excise contract

Use with `docs/contributing/state-module-playbook.md`. This is a separate
`capital_gains_excise` regime, never `type: none` or ordinary-wage brackets.
TY2025 full-year domicile and explicit asset/household eligibility are the
reviewed return scope. Z5 and Z6 implementation history is in
`docs/changelog/epic-Z.md`; the planning extensions below have shipped.

## Scope and questionnaire

| # | Question | Current contract / authority |
|---|---|---|
| 1 | Starting point | Federal net long-term gains, not combined Schedule D line 16. RCW 82.87.020. |
| 2 | Adjustments | Remove exempt/nonallocated gains AND losses; add back excluded pre-2022, exempt and nonallocated LT carryforwards. RCW 82.87.020. |
| 3 | Credits | Other-jurisdiction tax is capped at WA tax on the same assets; TY2025 B&O credit also requires the same-sale worksheet. RCW 82.87.100/.130. |
| 4 | Local tax | No local income-tax calculation; allocation follows disposal domicile/tangible situs. RCW 82.87.100. |
| 5 | Surtax | TY2025 has tiered rates; fetch both rates and the boundary. DOR tier notice / RCW 82.87.040. |
| 6 | Estimates | No required quarterly estimates. Annual payment deadline follows DOR guidance; optional prepayments are not quarterly deadlines. |
| 7 | Payroll | PFML/WA Cares are separate programs; ordinary retirement/HSA income does not enter this base. |
| 8 | Withholding | No wage-withholding form or Box 17 ledger category for this excise. |
| 9 | Extension | Federal extension prerequisite plus separate My DOR request by the original deadline; payment is not extended. DOR FAQ. |
| 10 | PTET | Passed-through LT gains enter the owner's excise. No toolkit WA elective PTET pricing. |
| 11 | Community property | Household caps/credits/assets are shared; separate spouse/RDP household computation refuses. RCW 82.87.120. |
| 12 | Deductions | Indexed household standard deduction, qualifying family business and qualifying WA-directed charity threshold/cap. RCW 82.87.060–.080/.150. |
| 13 | Tax math | Bands after deductions, not ordinary-income rates or NY recapture. |

## Cache, inputs and result

`state.code: WA`, `state.type: capital_gains_excise`, `state.brackets: []`.
`state.wa_capital_gains` holds fetched `standard_deduction`,
`charitable_minimum`, `charitable_cap`, `rates` and matching `tax_year`.
Freshness requires every field. Common bracket readers refuse WA rates,
including corrupt legacy caches, so an ordinary planner cannot tax wages
using excise bands. Setup fetches annual amounts; none are supplied here.

`compute_wa_capital_gains` consumes Schedule D lines 15/14, explicit asset
classification rows, excluded carryforwards bounded by line 14, confirmed
business/charity deductions, substantiated same-asset credit limits and
confirmed payments. Missing, nonfinite or unverified required inputs refuse;
no zero-deduction fallback or payer-name exemption inference is allowed.
Duplicate asset classification refuses. `washington_ltcg_excise` includes
`computed_tax_year`, proving computation even when tax is zero.

Registry, result template, package, capability note, setup/compute interview,
annual calendar and extension/amendment instructions are wired. There is no
wage-withholding true-up or quarterly estimate schedule for this excise.
Ordinary projections disclose the separate tax is unpriced.

## Harvest, crypto and WFTC

Harvest reuses confirmed `washington_inputs` and fetched annual parameters.
A holdings `wa_harvest` object confirms unchanged deductions and includes
a boolean `taxable_lots` entry for every eligible LT lot. The computed WA
baseline must agree with a fresh worksheet calculation. Only allowed taxable
LT losses reduce the base; ST losses do not. Credits require scenario-specific
allocation worksheets and otherwise make savings visibly unpriced. Missing
or unverified inputs also disclose unpriced savings. Individual lot ranking
uses each lot's excise difference; combined savings are recomputed separately
so deduction/band crossings cannot be counted twice. The baseline is unchanged.

Crypto's optional domicile map names a valid state for every actual LT sale
date. No calendar proration or address inference substitutes for that map.
Output includes signed allocated/nonallocated LT gains and losses already
in federal totals; ST/receipt income is excluded from this worksheet. It is
an allocation workpaper, not support for a moved taxpayer's WA return.

WFTC is a separate application reminder, including potential ITIN eligibility,
in setup/compute and result notes. No federal EIC or WA credit is guessed;
current eligibility must be verified before applying. No WFTC annual figure
or deadline is hardcoded.

## Sources, tests and limits

The original TY2025 source checks covered
[DOR rules/deductions](https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax),
[DOR FAQ](https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax/frequently-asked-questions-about-washingtons-capital-gains-tax),
[tiered-rate notice](https://dor.wa.gov/forms-publications/publications-subject/special-notices/new-tiered-rates-washingtons-capital-gains-tax),
[RCW 82.87](https://app.leg.wa.gov/RCW/default.aspx?cite=82.87),
[WFTC eligibility](https://workingfamiliescredit.wa.gov/eligibility) and
[TY2025 WFTC instructions](https://workingfamiliescredit.wa.gov/sites/default/files/2026-01/WFTC_AppInstructions_TY2025_English.pdf).
Use the covered filing year's authorities for runtime law fetches.

Hand worksheets cover deduction/tier crossings, ST independence, exempt
gain/loss and carryforward adjustments, same-asset credit caps and charity
eligibility. Tests also cover wash-sale allowed losses, individual versus
combined savings, baseline disagreement, incomplete inputs, malformed domicile,
household refusals, zero-tax evidence, cache corruption and calendar scope.

Section 1256 holding-period normalization, QOF/special federal gains,
separate household community allocation and midyear/nonresident sourcing
remain refused. Deduction/credit legal eligibility is attested, not inferred.
WA filing/amendment requires separate electronic submission and the federal
return copy; summary generation does not file it.
