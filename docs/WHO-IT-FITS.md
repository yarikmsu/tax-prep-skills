# Who this fits

The engine stops rather than guess. When a return includes something it cannot
compute, `/tax-compute` blocks at its capability gate, names the form it would
need, and goes no further. This page measures how often that happens for the five
kinds of filer the toolkit is built for: a Californian first, then Floridians and
New York City residents, then North Carolinians and Seattle residents. Read it
before you start, to see whether your return is likely to be one of them.

**The short answer: 16–27% of returns hit a block, depending on the persona,
and 21–29% of returns above $200,000 of income.** Above $200,000, one block,
rental property, is most of it everywhere.

## What a block means

- **It is a stop, not a warning.** The gate exits with the form named, and
  nothing after it runs.
- **Accepting it does not fill the gap.** Naming the rule in
  `acknowledged_limitations` lets the return finish with that part marked NOT
  COMPUTED. The rental schedule, the part-year return or the premium credit
  reconciliation still has to be prepared somewhere else and added before you
  file. So "hits a block" below means the toolkit alone cannot produce the return
  you file, with one exception.
- **The exception is a foreign fund's distribution or sale**
  (`pfic_taxable_event`). Once you accept that block and supply each fund's
  facts, `/tax-compute` computes the §1291 tax. A fund with a QEF or
  mark-to-market election, or one that became a PFIC after you bought it, is
  still not computed.
- **Three New York cases are refused later, at compute.** A move between New
  York City and the rest of the state; a joint return where only one spouse
  lived in the City all year; and a joint return with wages earned in Yonkers
  by someone who does not live there, whose Yonkers tax (Form Y-203) is not
  modeled on a joint return. Accepting a limitation at the gate clears none of
  them.
- **A California or New York return with that state's tax withheld on a 1099
  or W-2G is refused at the tie-out**, a pension's 1099-R among them, and so is
  New York City or Yonkers tax withheld on a 1099-R or W-2G. The toolkit does
  not route that withholding to Form 540 or IT-201 yet, and nothing waives it:
  the whole return, federal included, has to be prepared elsewhere until it
  does.

## The blocks, measured

Each figure is the share of that group's returns that hit the block. The first
figure is for returns with adjusted gross income of $200,000 or more, the
high-earner filer the equity-compensation and investment features were written
for; the second is for every return. Where a source covers only everyone, there
is one figure. How each was measured, and what it misses, is at the end.

| Block (gate rule) | California | Florida | New York City | North Carolina | Seattle (King County) |
|---|---|---|---|---|---|
| Rental real estate, Schedule E (`rental_income`) | 17% · 7.1% | 15% · 4.7% | 11% · 5.8% | 14% · 5.0% | 13% · 6.8% |
| Marketplace health insurance with an advance credit, Form 8962 (`marketplace_insurance`) | 1.3% · 6.0% | 2.4% · 17% | 0.32% · 1.4% | 1.6% · 9.2% | 0.69% · 3.6% |
| Moved to or from another state during the year (`part_year_residency`) | 3.7% | 6.7% | 8.8%&nbsp;¹ | 6.9% | 7.7% |
| Moved to or from another country during the year (`move_abroad`) | 0.15% | 0.19% | 0.23% | 0.13% | 0.32% |
| Works in another state (`nonresident_source_income`) ³ | 0.26% | 1.0% | 2.5% | 2.4% | 0.36% |
| **At least one block** ² | **22–27%** · 16% | **24–29%** · 27% | **22–27%** · 18% | **24–29%** · 22% | **21–26%** · 18% |

¹ Of the 8.8%, 6.7% cross a state line, which the gate stops, and 2.0% move
between the City and the rest of New York State; each is rounded on its own.
The gate does not stop the second group, but the City tax refuses them at
compute, because the part-year City return (IT-360.1) is not modeled.

² Computed from the figures above as printed, plus the two national figures
below, treating the blocks as independent, which they are not quite. The low
end is the rows above and household employees. The high end adds the rental
owners the IRS field misses (4 points nationally above $200,000, none across
all returns) and the Form 8615 figure. Where the two ends round to the same
figure there is one. `scripts/who_it_fits.py` recomputes the table from the
sources, and a test holds the table to it.

³ A share of workers, not of returns. A joint return where one of two earners
commutes is one blocked return, so this undercounts joint returns.

**Measured only nationally**, with no state or county series:

- **Household employees**, Schedule H (`household_employees`): 0.89% of
  $200,000+ returns, 2.8% of $500,000+, and 0.15% of all returns. A nanny in New
  York City or the Bay Area is likely above the national rate, and no public
  figure says by how much.
- **A child's investment income**, Form 8615 (`kiddie_tax`): about 229,000
  children's returns used Form 8615 for 2023, 0.14% of all returns. If every one
  belonged to a different $200,000+ household, that would be 1.7% of them;
  siblings and lower-income parents make the real share smaller. The count
  leaves out parents who report a child's interest and dividends on their own
  return with Form 8814 instead. The gate blocks them too, and no public table
  counts them, so neither figure is an upper bound.

**Not measured**, because no public source counts them:

- **A second state's resident return** (`statutory_residency_second_state`). You
  keep a home in another state and spend enough days there to be its resident
  as well. The thresholds and their sources are in
  [residency-guardrails](contributing/residency-guardrails.md).
- **A resident of a US territory** (`territory_resident`): Puerto Rico,
  American Samoa, Guam, the Northern Mariana Islands or the US Virgin Islands.
  Each has its own income-tax relationship to the United States, and none of
  the tables above is cut by it.
- **A foreign fund's distribution or sale** (`pfic_taxable_event`), which is
  computed once you accept it (above).
- **A digital asset sold, exchanged or spent because an option was exercised,
  or from a lot one bought**
  (`digital_asset_option_exercise`). The option's premium moves the sale's
  amount realized or the purchase's basis, and the crypto import prices
  neither.
- **Out-of-state income that is not a commute.** This is the same block as
  commuting, and the table's commuting figure is only its floor. It also covers
  a K-1 from a partnership doing business in other states, a sale of property
  located in another state, and remote work for an employer in a state with a
  convenience-of-the-employer rule.

## By persona

### California

California blocks the least of the five overall, and its own return is the
deepest the toolkit computes. Above $200,000, 17% of returns carry rental or
royalty income, and that one block is most of the 22–27% that hit any. Moves
(3.7%) are the lowest of the five. Almost nobody commutes to a state that
blocks: Nevada, the commonest out-of-state workplace, has no income tax and does
not block. An ISO exercise, Form 8938 for foreign accounts and a separate return
under community-property law carry a note on the return, not a block: the ISO's
AMT is computed, and the other two are worked by hand. 64% of California returns
above $500,000 itemize, and for them the federal return applies the SALT cap's
phasedown on high incomes.

### Florida

Florida has no income tax, so there is no Florida return to block, and the
federal blocks are the whole story. One of them is larger here than anywhere
else. 17% of Florida returns took an advance premium credit for marketplace
coverage, which makes Form 8962 Florida's commonest block overall and puts
Florida highest of the five for all returns (27%). Above $200,000 that falls to
2.4%, and rental takes over. 6.7% of households moved to or from another state
during the year. Two of the unmeasured blocks bite hardest here, because the
largest group of arrivals, 12%, comes from New York. Keeping a New York
home and spending enough days there makes you a New York resident as well.
Working from Florida for a New York employer whose office is your primary office
can leave the wages taxable by New York under its convenience rule. Both block,
and neither shows in public data.

### New York City

The toolkit computes the City tax and the household credit for a whole-year City
resident, and the MCTMT, a New York State tax on self-employment income in the
MTA region. Three cases are refused at compute, and accepting a limitation
clears none. One is a move between the City and the rest of the state (footnote
1). Another is a joint return where only one spouse lived in the City all year.
The third is a joint return with wages earned in Yonkers by someone who does not
live there, whose Yonkers tax (Form Y-203) is not modeled on a joint return. No
public data sizes the last two. Above $200,000, 11% carry rental income, the lowest
rental rate of the five. Moves are the highest: 8.8% of City households moved in
or out in a year (footnote 1). 2.5% of City workers work in another state that
blocks, and 73% of the City's out-of-state commuters work in New Jersey. Together,
22–27% of $200,000+ returns hit a block.

### North Carolina

Nothing blocks for living in North Carolina. But North Carolina's own return is
not produced: every North Carolinian gets a bracket-based estimate of the D-400
instead, which is a bigger gap for this persona than any block. Above $200,000
the federal blocks come out as Florida's do, 24–29% hitting at least one. Across
all returns fewer are blocked, 22% against Florida's 27%, because fewer took an
advance premium credit: 9.2% against 17%. 6.9% of households moved to or from
another state, and 2.4% of workers work in another state that blocks. 70% of
North Carolina's out-of-state commuters work in South Carolina or Virginia. Rental is 14% above $200,000.

### Seattle

Washington taxes no wages, and living there blocks nothing on its own. It is
still not on the no-income-tax list, because of its capital-gains excise, which
is not computed. A Washington resident with large long-term gains owes it and
gets no figure for it here, and a wage-only filer gets an estimate note rather
than a plain statement that no state return exists. King County's blocks are the
federal ones: rental at 13% above $200,000, moves across the state line at
7.7%, and almost no commuting (0.36%). 21–26% of $200,000+ returns hit one. As
in California, a separate return under community-property law is a hand
worksheet, not a block.

## Not blocks, but not computed either

These do not stop the return, and each can leave a figure wrong or missing. They
are the ones that matter most for these personas. Every situation the gate
notes without blocking is listed in the
[skill catalog](contributing/skill-catalog.md).

- **North Carolina's D-400** is a bracket-based estimate.
- **Washington's capital-gains excise** is not computed, and the return's state
  note does not name it: it says only that Washington is an estimate.
- **The Earned Income Credit** is never computed, federally, for New York State
  or for New York City. Each is an input taken from its worksheet, and mostly
  falls below these personas' incomes.
- **New York's refundable credits** are inputs too, each taken from its own
  form.

## How these were measured

The figures are shares of returns, except commuting, which is a share of
workers (footnote 3). They come from the latest published year of each source.
The returns this toolkit prepares are later years, so read them as the size of
the hole, not a forecast. `scripts/who_it_fits.py` recomputes every figure from
the files below and saves them in `scripts/who_it_fits_figures.json`, and a test
holds the page to what it saved. Refresh the page with it when a newer year is
published.

- **Rental and marketplace credit**: IRS Statistics of Income,
  [Historic Table 2](https://www.irs.gov/statistics/soi-tax-stats-historic-table-2)
  (states) and [county data](https://www.irs.gov/statistics/soi-tax-stats-county-data),
  tax year 2023.
  - Rental is field `N25870`, returns with rental and royalty net income or loss
    (Schedule E line 26). It includes royalties. It misses owners whose loss is
    suspended and nets to zero: nationally, returns reporting any rent are 19%
    of $200,000+ returns against 15% by this field
    ([Publication 1304 Table 1.4](https://www.irs.gov/statistics/soi-tax-stats-individual-income-tax-returns-complete-report-publication-1304-basic-tables-part-1)).
    Across all returns the field counts slightly more than returns with rent,
    because of its royalties, so nothing is added there.
  - The marketplace figure is field `N85775`, returns with an advance premium tax
    credit. The gate's question asks about any marketplace plan, subsidized or
    not, so this undercounts it.
- **Moves**: IRS Statistics of Income
  [migration data](https://www.irs.gov/statistics/soi-tax-stats-migration-data),
  2022 to 2023. Households whose return address moved to or from another state
  between the two filing years, over the households that filed in the place in
  either year. Moves abroad are the
  same files' foreign rows, sized as their own row. It covers every income only.
- **Commuting**: Census Bureau,
  [county-to-county commuting flows](https://www.census.gov/data/tables/2020/demo/metro-micro/commuting-flows-2020.html),
  American Community Survey 2016-2020. Workers whose workplace is in another
  state, over every worker living in the place. A workplace in Alaska, Florida,
  Nevada, South Dakota, Tennessee, Texas or Wyoming does not block. One in
  Washington or New Hampshire does, although neither taxes wages, because the
  gate keeps the question open for every state not on its no-income-tax list. It
  counts where people physically work, so remote work is not in it.
- **Household employees and kiddie tax**:
  [Publication 1304](https://www.irs.gov/statistics/soi-tax-stats-individual-income-tax-returns-complete-report-publication-1304-basic-tables-part-3)
  Table 3.3 (household employment tax) and Table 3.1A (Form 8615), tax year
  2023, national only. These tables are `.xls` files, so the script carries
  their counts by hand.
- **Places**: New York City is the Bronx, Kings, New York, Queens and Richmond
  counties. Seattle is King County, which includes its suburbs.
- **The gate itself**: the blocking rules are `RULES` in
  `agents/lib/capabilities.py`, and a test holds this page to that list, so a
  block added there fails until it is sized here.

North Carolina adds six state-specific blocks: `nc_business_adjustments`,
`nc_recovery_deductions`, `nc_nol`, `nc_taxed_pte`, `nc_other_deductions` and
`nc_other_credits`. They apply only to NC residents. The NC percentages above measure the
previous common blocks and exclude these additional situations. No population frequency has been measured for
these NC Schedule S and credit situations; their overlap is unknown.

WA-specific screening: `wa_special_gains` and `wa_separate_household` are unmeasured scope blocks; their prevalence is not estimated. Each must be answered for WA.

TY2025 IT-203 now covers reviewed NY part-year/nonresident allocations and NYC
IT-360.1 for that variant. The population estimates above still describe the
resident-only gate baseline, not a re-estimated adoption rate. A complete NY
plan clears move/source blocks only for covered no-income-tax states needing
no return. NJ and other taxable-state resident returns and their credits still
need separate preparation. A City move within full-year NY remains the
unmodeled IT-201 part-year City branch; this IT203 addition does not clear it.
