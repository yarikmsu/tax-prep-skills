# Z6 — Washington planning follow-ups

The thirteen state questions remain answered by the Z5 WA capital-gains design.
This extension changes no return scope, annual-law schema or tax treatment.
TY2025 full-year domicile and the existing household/special-gains gates remain.

Live no-cache primary-source reads on 2026-10-03 verified the
[DOR FAQ](https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax/frequently-asked-questions-about-washingtons-capital-gains-tax),
[WFTC eligibility](https://workingfamiliescredit.wa.gov/eligibility) and
[TY2025 application instructions](https://workingfamiliescredit.wa.gov/sites/default/files/2026-01/WFTC_AppInstructions_TY2025_English.pdf).

Harvest pricing reuses the confirmed `washington_inputs` return worksheet and
fetched annual parameters. A holdings `wa_harvest` object must confirm unchanged
deductions and contain a boolean `taxable_lots` entry for every eligible LT lot.
The stored computed WA baseline must agree with a fresh worksheet computation.
Only allowed, taxable LT losses reduce the WA base; ST losses never do. Credits
need scenario-specific allocation worksheets and degrade to visibly unpriced
savings rather than freezing an allocation that could overstate savings.
Missing/unverified inputs likewise disclose unpriced savings. Individual lot
ranking uses its own excise difference; combined savings are separately computed
so deduction/band crossings cannot be counted twice. No baseline is mutated.

Crypto output accepts an optional domicile map keyed by actual LT sale date.
Every LT date must be covered by an explicit valid state; no calendar proration
or address inference. It reports included LT gains/losses and signed nonallocated
adjustments already in federal LT totals. ST/receipt income is excluded from
this allocation worksheet. A moved taxpayer still cannot use the full-year WA
return module: the allocation helper is a workpaper, not expanded return support.

WFTC is a separate application reminder in WA compute/setup instructions and
the computed section's notes, including potential ITIN eligibility. No federal
EIC amount or WA credit is guessed; verify current program eligibility before
applying. No WFTC rate, threshold or deadline is hardcoded.

Tests use hand calculations from DOR TY2025 schedules: deduction crossing,
two-tier crossing, ST independence, exempt loss exclusion, wash-sale allowed
losses, baseline disagreement, missing inputs, credits and malformed domicile.
