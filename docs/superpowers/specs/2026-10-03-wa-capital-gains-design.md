# Washington capital-gains excise — Z5

## Scope and questionnaire (completed before implementation)

TY2025 individual capital-gains excise, full-year Washington domicile, with
explicit asset classification. This adds the `capital_gains_excise` regime;
ordinary-income planners must never use its rates. Separate spouses and
registered domestic partners require a household/community allocation not
modeled here and refuse. Midyear allocation remains gated; Z6 owns harvest
pricing, transaction domicile and the WFTC reminder.

| # | Question | Answer and primary authority |
|---|---|---|
| 1 | Starting point | Federal net LONG-TERM gains, not combined Schedule D line 16. RCW 82.87.020(1),(8); special federal rules below require normalization. |
| 2 | Adjustments | Remove exempt/nonallocated gains AND losses; add back excluded pre-2022, exempt and nonallocated LT carryforwards. RCW 82.87.020. HSA, IRA and ordinary retirement income do not enter this base. |
| 3 | Credits | Other-jurisdiction tax capped at WA tax on the same assets (.100); from TY2025 B&O paid on the same sale credits the excise (.130). No exemption/EITC piggyback. |
| 4 | Local tax | No local income-tax computation here; excise allocation follows domicile at disposal and tangible-property situs (.100). |
| 5 | Surtax | TY2025 adds a second tier; fetch both rates and the band boundary. DOR's June 30, 2025 tier notice and .040. |
| 6 | Estimates | No required quarterly estimates. Annual payment normally April 15, weekend/holiday adjustment. DOR FAQ. Optional TY2026 prepayments are not quarterly deadlines. |
| 7 | Payroll items | PFML/WA Cares are separate payroll programs, not deductions in this excise base. No payroll computation added. |
| 8 | Withholding | No state wage-withholding form or Box 17 tie-out for this tax. |
| 9 | Extension | Federal extension is a prerequisite, but separately request WA extension through My DOR by the original deadline. Filing only; payment remains due. DOR capital-gains page. |
| 10 | PTET | Individual owners include passed-through LT gains; no toolkit WA elective PTET computation. B&O credit is distinct (.130). |
| 11 | Community property | WA is already in the community-property set. Spouses/RDPs are one taxpayer for ALL caps/credits/assets even if separately filed (.120, amended 2025). Refuse separate household computation. |
| 12 | Deductions | One indexed standard deduction per household (.060); qualified family-business deduction (.070); qualifying WA-directed charities above indexed threshold, indexed cap (.080/.150). No federal itemized election/AGI phaseout. |
| 13 | Tax math | Graduated bands on WA gains AFTER deductions, no NY-style recapture. Rates are fetched, never ordinary-wage brackets. |

Verified with live no-cache requests on 2026-10-03:
[DOR rules and annual deductions](https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax),
[FAQ](https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax/frequently-asked-questions-about-washingtons-capital-gains-tax),
[TY2025 bands](https://dor.wa.gov/forms-publications/publications-subject/special-notices/new-tiered-rates-washingtons-capital-gains-tax),
[RCW chapter](https://app.leg.wa.gov/RCW/default.aspx?cite=82.87).

## Cache and computation

`state.code: WA`, `state.type: capital_gains_excise`, `state.brackets: []`.
`state.wa_capital_gains` contains fetched `standard_deduction`,
`charitable_minimum`, `charitable_cap`, and `rates` (min/max/rate bands).
The block's `tax_year` must match the filing year. No amount is inline.
Freshness requires every key and rejects an ordinary bracket table for WA.

`compute_wa_capital_gains` consumes Schedule D lines 15 and 14, explicit
classification rows, excluded carryforwards bounded by line 14, confirmed
qualifying business and charitable deductions, manually substantiated
same-asset credit limits and confirmed payments. It emits the
`washington_ltcg_excise` section. `computed_tax_year` proves computation at
zero income. All missing, nonfinite or unverified inputs refuse; there is no
silent zero-deduction fallback. Asset exemptions are recorded flags, never
auto-inferred from a payer name. Rows cannot classify the same asset twice.

## Registrations and verification

Registry, result template, package, capability note, compute/setup interview,
annual calendar and extension/amendment instructions are included. Registry
tests distinguish excise from wage-income taxes; no Box 17 category or true-up
is appropriate. The common bracket reader refuses WA rates even from corrupt
legacy caches; ordinary projections disclose the separate tax is unpriced.

Hand worksheets: TY2025 $500,000 LT less $278,000 deduction gives $15,540;
$1.5m gives $70,000 + $222,000 × .099 = $91,978. Test band/deduction boundaries,
ST-loss independence, gain AND loss exclusions, carryforwards, credit caps,
charity eligibility, MFS/RDP refusal, zero-tax filing after extension/payment,
cache year/type corruption and calendar absence of quarterly deadlines.

## Explicit limits

Section 1256 actual-holding-period normalization, QOF and other federal
special-gain rules refuse; federal Schedule D alone is not the WA base then.
Separate spouse/RDP community allocations and partyear/nonresident asset
sourcing refuse. Deductions and credits require their eligibility worksheets;
the engine prices confirmed amounts and does not determine legal eligibility.
Z6 harvest/crypto/WFTC work remains open. WA filings and amendments require
separate electronic submission plus the federal return copy; generating the
summary does not file it.
