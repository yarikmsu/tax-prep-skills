# Social Security evidence in income planning

Scenario and Roth planners use the existing `compute_taxable_ss` regular
worksheet at each income delta. The original taxable amount stays in the
normalized baseline; the shared scenario engine replaces it by adding only
the difference in taxable benefits. The comparison remains anchored to the
return's total tax. Scenario marginal cost divides by the requested income
change, excluding the additional benefits made taxable; AGI includes them.

When benefits exist, `result.json.social_security_worksheet` records the covered
`tax_year`, `regular_worksheet_confirmed: true`, and all actual engine arguments
under `inputs`: `ss_benefits`, `other_income`, `tax_exempt_interest`, `adjustments`,
`filing_status`, `lived_apart_from_spouse_all_year`,
`lump_sum_prior_year_benefits`, and `excluded_income_addbacks`.
Zero amounts are explicit inputs; the lived-apart flag is null for non-MFS.
The evidence must reproduce the return's gross and taxable benefits, other
income, exempt interest, adjustments and filing status. Missing evidence stops
planning, even when the baseline taxable portion is zero. Gross benefits cannot
be recovered from the taxable amount. Old stacked scenarios need regeneration.

The confirmed regular worksheet excludes lump-sum election and combined
IRA/MAGI-dependent adjustment feedback. A repayment case or these other methods
needs separately reviewed modeling; an upper-bound taxable-benefit figure must
not become an overstated planning saving. The source is the IRS's covered-year
[Publication 915 worksheet instructions](https://www.irs.gov/publications/p915),
which also direct combined IRA cases to Publication 590-A. Confirm applicability
for the covered year; this change introduces no new rates or thresholds.

Gross benefits, exempt interest, exclusion add-backs and non-SE adjustments stay
at their recorded amounts. The provisional-income worksheet moves by the change
in modeled non-benefit income, applying the existing capital-loss limit and
excluding qualified dividends as a second copy of ordinary dividends. The
deductible half of SE tax moves with modeled earnings and wage-base room.
Normalized origin inputs are preserved through a stack, so each additional or
inverse delta is measured from the same original worksheet. Changing the return's
evidence or its filing-status treatment refuses reuse of a saved scenario.
The return, worksheet, requested planning year and law cache's declared year
must agree; the cache filename alone is not evidence of the covered year.

Other simplified-model limits still apply: deductions or credits not repriced by
the scenario engine remain held as disclosed. These are local financial records;
the existing secured scenario writers and baseline read-only behavior apply.
