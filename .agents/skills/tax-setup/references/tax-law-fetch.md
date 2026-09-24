# tax-setup Step 4 — fetching and caching current tax law

The per-figure search list, cache-write schema and source-pinning rules for
`### 4. Fetch Current Tax Law`. Read this when you reach Step 4.

**The freshness contract in `../SKILL.md` binds every fetch below:** live web
results are required, and if web access is unavailable or cached-only you STOP
and ask the user to paste the current figures with their source URL. Never infer
a figure from a prior year.

## Contents

- Federal (IRS.gov)
- State (based on taxpayer's state)
- Exchange rates (if foreign income)
- Check for new tax legislation

---

**Federal (IRS.gov):**
- Search: `"IRS {year} tax brackets married filing jointly"`
- Search: `"IRS {year} standard deduction"`
- Search: `"IRS {year} child tax credit amount"` — populate BOTH
  `child_tax_credit.amount_per_child` AND `child_tax_credit.refundable_limit`
  (the per-child ACTC cap, INDEXED — fetch it for the filing year rather than
  carrying one over; needed for the Schedule 8812 refundable split in
  `/tax-compute`)
- Search: `"IRS {year} section 199A QBI taxable income threshold"` — populate
  `federal.qbi_threshold` (`single`/`mfj`; above it Form 8995-A limits apply and
  `compute_qbi_deduction` flags `over_threshold`)
- Search: `"IRS {year} SALT deduction cap"` — populate `federal.salt_cap`
  (`amount`/`mfs_amount`; OBBBA $40K/$20K-MFS for TY2025-2029 indexed ~1%/yr,
  $10K/$5K-MFS in TY2030 — consumed by `/tax-ptet` and the itemize-vs-standard
  check in `/tax-compute`)
- Search: `"IRS {year} HSA contribution limits"`
- Search: `"IRS {year} 401k contribution limit"`
- Search: `"IRS {year} IRA contribution limit phase out"`
- Search: `"IRS {year} social security wage base"`
- Search: `"IRS {year} capital gains tax brackets"` — populate
  `federal.capital_gains_rates.zero_pct_limit` and `fifteen_pct_limit` for ALL
  FOUR statuses (`single`, `mfj`, `mfs`, `hoh`; QSS reads `mfj`) from the Rev.
  Proc. §1(h) table: head of household has its own breakpoints and married
  filing separately is half of joint. A cache carrying only `single`/`mfj`
  makes the engine REFUSE to price preferential income for an HoH or MFS
  filer rather than use the single figure.
- Search: `"Medicare IRMAA {year} income brackets Part B Part D"` — populate `federal.irmaa.tiers`
  (`single`/`mfj` lists of `{magi_threshold, annual_surcharge_per_person}`, annualizing the
  monthly Part B + Part D surcharge above the standard premium). Optional but unlocks IRMAA
  flags in `/tax-roth-convert`; leave as `[]` if not relevant to the taxpayer.
- Search: `"IRS {year} AMT exemption amount phase out"` — populate `federal.amt`
  (`exemption` + `phaseout_start` by status, and `rate_28_breakpoint` — the
  26%→28% figure, published with the Rev. Proc. inflation adjustments;
  the MFS breakpoint is derived by halving, don't store it). Required for
  ISO exercisers — `compute_form_6251` hard-errors on an unfetched table.
  Leave `phaseout_rate` as `null` unless a source contradicts the built-in
  schedule (25% ≤TY2025, 50% from TY2026 per OBBBA §70107) — the statutory
  rate is coded, so a null is correct, not a gap. **TY2026+ staleness
  tripwire:** §70107 also RESETS the phase-out thresholds to the 2018 base
  (~$500K single / $1M MFJ, reindexed), so a TY2026 fetch that returns
  TY2025-magnitude `phaseout_start` values (~$626K single) is reading a stale
  source — re-search for the post-OBBBA figures.
- Search: `"IRS Schedule 1-A {year} tips overtime deduction limits"` — populate
  `federal.schedule_1a` (`tips_cap`, `overtime_cap` by single/mfj,
  `car_loan_cap`, `senior_amount_per_person`, and the three phase-out-start
  pairs: `tips_overtime_phaseout_start`, `car_loan_phaseout_start`,
  `senior_phaseout_start`). ONLY needed when the taxpayer has tips, overtime,
  car-loan interest, or is 65+ (TY2025-2028) — leave the block all-null
  otherwise (freshness does not nag; `compute_schedule_1a` hard-errors at the
  point of use if a figure is missing when actually needed). The phase-out
  MECHANICS ($100/$1,000, $200/$1,000, 6%) are coded, not fetched.
- Search: `"IRS §170(p) non-itemizer charitable deduction {year} amount"` —
  populate `federal.charitable_non_itemizer` (`amount` ~$1,000 non-joint,
  `joint_amount` ~$2,000 MFJ). ONLY needed for a TY2026+ STANDARD-deduction
  filer with cash charitable gifts — leave all-null otherwise (freshness does
  not nag; `compute_charitable_non_itemizer` hard-errors at use if unfetched
  when actually needed).
- Search: `"IRS §70105 minimum QBI deduction {year} amount threshold"` —
  populate `federal.qbi_minimum` (`income_threshold` and `amount`, BOTH
  indexed — fetch both, and do not carry either over). ONLY needed for a TY2026+ filer with a small ACTIVE qualified
  trade — leave all-null otherwise (the minimum is taxpayer-favorable, so
  `compute_qbi_deduction` DEGRADES with a note rather than guessing).
- Search: `"IRS quarterly interest rates underpayment {year}"` (and `{year+1}`
  quarters as published) — populate `federal.underpayment_rates` as a list of
  `{"effective_from": "YYYY-MM-DD", "annual_rate": 0.08}` covering every §6621
  rate period. **Fetch scope — two consumers with DIFFERENT windows:**
  - `compute_form_2210` (estimated-tax penalty) accrues Apr 15 of {year} through
    Apr 15 of {year+1}.
  - `compute_underpayment_interest` (§6601/§6622 interest on a late-paid balance,
    M3) accrues from the {year+1} Apr-15 DUE DATE forward to the pay date — so an
    October extension filer needs the {year+1} Q2/Q3/Q4 quarters (Apr/Jul/Oct),
    which are BEYOND the 2210 window. Extend the fetch forward through the quarter
    containing the anticipated pay date whenever an extension is in play.

  The irs.gov quarterly interest-rate news releases are the source; record each
  under `_sources` (K2). Future quarters aren't published yet at setup time —
  both consumers refuse to guess: `compute_form_2210` degrades on missing periods,
  and `compute_underpayment_interest` reports interest only PARTIAL-through the
  last cached quarter with a loud note (never extrapolating the last rate across
  an unfetched quarter). `/tax-compute` and `/tax-extend` re-check at use.
- **ONLY when `has_pfic_taxable_event` is set** (an acknowledged PFIC §1291
  event on a fund held across prior years): Search `"highest individual federal
  income tax rate {Y}"` for EACH prior calendar year `Y` the fund was held (the
  span from `acquisition_date`'s year through `event_date`'s year, per
  `foreign_holdings_detail.holdings`) — populate `federal.historical_top_rate`
  as `{"{Y}": 0.37, ...}` (e.g. 37% for 2018-2025, 39.6% for 2013-2017, 35% for
  2003-2012 — verify + pin each year under `_sources`, K2). Each prior-PFIC-year
  excess-distribution slice is taxed at THAT year's top ordinary rate
  (§1291(c)(2)), not the taxpayer's bracket. **This fetch is REQUIRED to unblock
  the return:** `compute_pfic_1291` marks any prior year whose rate is absent
  `complete=False`, and the Step 12.7 tie-out gate then keeps the return blocked
  (fail-closed — never a guessed rate). Skip entirely for a first-year-only event
  (no prior PFIC year) and for every non-PFIC return (the table stays empty, which
  is the default — freshness does NOT nag).
  - **ALSO extend the `federal.underpayment_rates` (§6621) fetch BACKWARD** to
    cover the PFIC interest span: `compute_pfic_1291` runs §6621 interest from EACH
    prior PFIC year's UNEXTENDED return due date (≈ Apr 15 of Y+1) through the
    current return's due date, so the cache must include every §6621 quarter from
    the EARLIEST prior-PFIC-year due date onward — NOT just the current-year Form
    2210 window. For a 2022 allocation that means the 2023-Q2 rate (Apr 2023) and
    every quarter through the current due date. A missing opening quarter makes
    `compute_underpayment_interest` return incomplete → the fund `complete=False` →
    the return stays blocked. Search `"IRS quarterly interest rates underpayment
    {Y}"` for each such prior year and prepend those periods to
    `federal.underpayment_rates` (source-pin each). Historical §6621 rates are
    published and stable, so this is a one-time backfill.

**State (based on taxpayer's state):**
- **The `state` block is SINGLE-STATE, not multi-state**: `tax_law_<year>.json`
  holds only one state's rates/type/code at a time. If the taxpayer's
  `address.state` changes after this cache was fetched (a mid-interview
  correction, a move, or re-running `/tax-setup` for a different taxpayer in
  the same workspace by mistake), re-run this fetch step for the NEW state —
  a stale cache left over from a different state is internally consistent by
  itself and will otherwise pass freshness. `/tax-compute` step 10 hard-stops
  when `tax_law.state.code` doesn't match the validated `address.state`
  (round-4 finding E) rather than silently computing the wrong state's
  branch, but the fix at that point is to come back here and re-fetch.
- For CA: Search `"FTB California {year} tax brackets"`, `"California {year} standard deduction"`,
  and `"California SDI rate {year}"` → populate `state.sdi_rate`. Populate
  `state.sdi_wage_limit` ONLY for pre-2024 years (SB 951 removed the SDI wage
  ceiling from TY2024 — leave it null for 2024+, which correctly disables the
  excess-SDI credit in `/tax-compute`)
- For CA: Search `"FTB California {year} itemized deduction phaseout AGI threshold"`
  → populate `state.itemized_phaseout` (`single`/`mfj`/`hoh` AGI thresholds,
  indexed annually — R&TC §17077; FTB publishes them with the exemption
  credits). Unfetched → `compute_ca_itemized` skips the phase-out with a
  loud overstatement note for high-AGI itemizers.
- For CA **when the taxpayer has an ISO exercise** (`has_iso_exercise`): Search
  `"California Schedule P 540 {year} AMT exemption amount phase-out"` → populate
  `state.amt.exemption` and `state.amt.phaseout_start` (both keyed
  `single`/`mfj`/`mfs`/`hoh`, indexed annually — FTB publishes them in the
  Schedule P instructions). Record the URL under `_sources` (K2). The 7% TMT
  rate and 25% phase-out are statutory and coded in `compute_ca_schedule_p`;
  only these indexed figures are fetched. Unfetched HARD-ERRORS at compute for a
  CA ISO exerciser (never a guessed exemption that would zero the CA AMT). Leave
  all-null for a non-ISO CA return.
- **For NY — the generic one-search fallback is NOT enough.** It fetches
  brackets and a standard deduction, which leaves the regimes that actually
  decide a New York return unset, and the block then reads as fetched. Design
  spec and sources:
  `docs/superpowers/specs/2026-09-10-ny-state-module-design.md`.
  - Search `"New York State {year} tax rate schedules site:tax.ny.gov"` →
    populate `state.brackets`, keyed by **SCHEDULE** (`a`/`b`/`c`), not by
    filing status: Tax Law §601(a) covers married-filing-jointly AND the
    surviving spouse, §601(b) the head of household, §601(c) the single filer
    AND married-filing-separately. `state.bracket_schedule_by_status` already
    holds the mapping — do not re-derive it, and do not split the schedules
    per status: an empty schedule is a silent ZERO tax, not an error.
    **Record each band's published cumulative BASE as well as its rate and
    floor** (`{"rate", "min", "max", "base"}`). New York publishes the schedule
    as "base plus rate on the excess" and ROUNDS each base to the nearest
    dollar, so summing the bands disagrees with the form by cents at every
    step — and `/tax-verify` ties against software that uses the published
    schedule. `compute_ny_tax` refuses a band with no `base` rather than
    falling back to summation, because a fallback is a second answer that
    differs quietly.
  - Search `"New York {year} standard deduction site:tax.ny.gov"` →
    `state.standard_deduction`, which has **SIX** keys. `qss` equals `mfj` and
    `mfs` equals `single` today — those are the pairings, not differences to
    hunt for, and **MFS is the one most often guessed wrong** because it looks
    married. The key with no analogue is `single_dependent`: a single filer who
    CAN be claimed as a dependent on another federal return (IT-201 item C).
  - Search `"New York {year} tax computation worksheet IT-201 recapture base
    incremental benefit"` → `state.tax_table_benefit_recapture`. **This is the
    one that decides whether the return is right.** Fetch ALL of it:
    `entry_threshold` (one figure for every status), `phase_in_band`, the
    per-schedule `entry_tier` — whose FIRST worksheet phases up to a FLAT RATE
    on the whole taxable income rather than using a base and benefit, and whose
    rate differs by schedule — the per-schedule `tiers` — each
    `{taxable_income_over,
    taxable_income_not_over, recapture_base, incremental_benefit,
    nyagi_excess_over}` — and `full_recapture` for the top regime, where the
    whole taxable income is taxed at one rate with no schedule. The base and
    benefit amounts MOVE YEAR TO YEAR; they are not structural. Fetching only
    the entry threshold reproduces the first worksheet and **UNDER-states every
    return above the first tier**, which is the direction the bias rule calls a
    hard error. The governing statute is a RANGE — §601(d) through (d-7), each
    with its own year window — so cite the one whose window contains `{year}`.
  - Search `"New York {year} tax table site:tax.ny.gov"` → `state.tax_table`.
    The table and the schedule are different METHODS, selected on BOTH NYAGI
    and taxable income, and the table is the schedule evaluated at each row's
    MIDPOINT and rounded to the dollar — the row FLOOR is a dollar low. Record
    `max_taxable_income` (the table's top) AND `row_segments`, because the rows
    are NOT all the same width: the bottom of the table is narrower than the
    $50 it settles into. Each segment is `{over, not_over, row_width}` and they
    must span 0 up to the top. Using the schedule where the form says table is
    worth up to two dollars and runs BOTH ways, against an acceptance bar of
    one dollar a line — `compute_ny_tax` refuses rather than guessing a width.
  - Search `"New York IT-196 itemized deduction limitation {year}"` →
    `state.itemized_reduction`, which holds **FOUR** mutually exclusive
    mechanisms by NYAGI band: a first reduction (per-status threshold, band,
    rate), a SECOND with its own threshold AND its own band, a flat plateau
    above those, and `charitable_only_threshold` marking where §615(f) stops
    entirely. Then `state.charitable_limit.tiers` for §615(g), each tier
    carrying `first_year`/`last_year` — a lapsed tier must be expressible as
    lapsed, or it reads as unfetched. Applying a reduction AND the charitable
    limit double-limits.
  - Search `"New York IT-201 dependent exemption amount {year}"` →
    `state.dependent_exemption.amount`. §616 states it and does not index it,
    so this is a CONFIRMATION; it is fetched anyway because a statutory figure
    may still be amended. **Watch for a source that returns 0**: post-TCJA IRC
    §151(d)(5) zeroes the FEDERAL exemption amount while §616 keys off the
    §151(c) ENTITLEMENT, which is why New York still allows one. NY grants no
    exemption for the taxpayer or spouse, which is why
    `personal_exemption.amount` ships as a declared 0 rather than a null.
  - Search `"New York State household credit table {year} site:tax.ny.gov"` →
    `state.credits.household.table`, which has **THREE** sub-tables and the
    grouping is NOT the one the rate schedules use: `single` alone, `mfs`
    alone, and `mfj_hoh_qss` together. MFS pairs with single for §601 and gets
    roughly HALF the credit here. It is keyed on **FEDERAL** adjusted gross
    income — IT-201 line 19, not line 33 — unlike every other threshold in this
    block. Record each band's `agi_over`/`agi_not_over` (a null `agi_over` is
    the lowest band, whose floor "could be 0 or a negative amount"), single's
    flat `amount`, and for the other two the full `by_exemptions` column list
    PLUS `each_additional`, the per-head increment ADDED to the last column for
    a larger household.
  - Search for each remaining NY credit on IT-201 → `state.credits`: the
    Empire State child credit, the child-and-dependent-care table, the EIC
    share of the federal credit, the real-property-tax credit, and the
    college-tuition credit's expense cap and maximum.
  - Search `"New York IT-225 instructions {year} filers do not enter"` →
    `state.it225.codes_not_valid_on_it201`. Record EVERY code an IT-201 filer
    may not carry, additions and subtractions and their pass-through `EA-`/`ES-`
    forms. New York DISALLOWS those modifications rather than adjusting them,
    so one on a return is a correction, not a rounding. Unfetched, the engine
    still totals the modifications and says loudly that it did not check them.
  - Search `"New York pension and annuity income exclusion {year}"` and
    `"New York 529 college savings deduction {year}"` → `state.subtractions`.
    The pension exclusion is **PER SPOUSE** with an age gate and no transfer of
    an unused part between spouses — a household figure cannot express it — and
    it sits on the IT-201 FACE, not on IT-225. The 529 deduction is keyed
    **married-joint against everybody else**, so `single` covers single,
    head-of-household, married-filing-separately and the surviving spouse;
    reading it with four status keys finds three absent.
  - **If the taxpayer is a New York City resident** (`nyc_resident`, or NY in
    the `residency` counts): search
    `"New York City {year} resident income tax rate schedule site:tax.ny.gov"`
    → `state.nyc_tax.brackets` (same three schedules, each band carrying
    `{min, max, rate, base}` with the PUBLISHED base, as the State's do) and set
    `applies`. Then the City's own TAX TABLE → `state.nyc_tax.tax_table`:
    `max_taxable_income` (the line-47 ceiling below which the table is used) and
    `row_segments`, recorded from the CITY table itself — its narrow bottom rows
    are not the State table's, and reusing those puts a small return in the
    wrong row. `compute_nyc_resident_tax` refuses without either. The
    school tax credit is **TWO** credits: a fixed amount by status under an
    income ceiling, and a rate reduction that is its own bracket schedule.
    Also fetch the NYC household credit — tables 4, 5 and 6, with the SAME
    three-way status grouping and the SAME federal-AGI keying as the state's
    tables 1-3 — and the NYC EIC share. And record
    `state.mctmt.net_earnings_threshold_per_zone`: it is a CLIFF, not an
    exemption, so once a zone's earnings exceed it the WHOLE zone amount is
    taxed.
  - **If the taxpayer is a Yonkers resident or earns there**: search
    `"Yonkers resident income tax surcharge {year}"` and `"Yonkers nonresident
    earnings tax {year} exclusion"` → `state.yonkers_tax`. The resident
    surcharge is a rate on the STATE TAX, not on income. The nonresident
    earnings tax allows an EXCLUSION first, prorated by month for a part-year
    filer — omitting it OVER-states the tax — and is capped at what the
    resident surcharge would have been. Record `resident_surcharge_rate` from
    line n of the IT-201-I Yonkers worksheet, `nonresident_earnings_rate` from
    Form Y-203 line 6, and `nonresident_exclusion.table` from the TWELVE-month
    row of Y-203's exclusion table, as `{"over", "not_over", "exclusion"}`
    rows on line 3b with the top row's `not_over` null.
  - Leave `applies` NULL on either city block when not fetched: null and "no
    city tax" are different answers, exactly as `type` distinguishes them for
    the state.
  - **If the taxpayer has self-employment income in the MCTD**: search
    `"metropolitan commuter transportation mobility tax {year} self-employed
    zone rate threshold site:tax.ny.gov"` → `state.mctmt`. Two ZONES with
    different rates — geography, not income tiers — and the counties are
    already in the template. The threshold is **PER ZONE**, computed
    individually even on a joint return, and it is a **CLIFF**: once a zone's
    earnings exceed it the WHOLE zone amount is taxed, not the excess. **It
    changes for tax year 2026**, so confirm the page is current for `{year}`
    or a filer who owes nothing gets taxed.
  - Search `"New York estimated tax {year} 90% 100% 110% site:tax.ny.gov"` →
    `state.estimated_tax_safe_harbor`. The thresholds are keyed to **NEW YORK**
    AGI, not federal. Fetch `farmer_fisherman_share` (one January installment
    at a lower share) and `de_minimis` too — the de-minimis figure is NOT the
    federal one, and without it a compute reaches for the federal amount and
    wrongly clears a New York filer who owes. NY's installments are **equal
    quarters** — April, June, September, the following January — which is NOT
    California's 30/40/0/30, and one IT-2105 voucher covers NYS, NYC, Yonkers
    and MCTMT together. That schedule is structural and already in the template.
  - Search `"New York Paid Family Leave {year} employee contribution rate wage
    cap"` → `state.paid_family_leave`, and the disability-benefits employee
    rate → `state.disability_benefits`. **Both are year-aware on purpose**:
    the CA scar is that the SDI wage ceiling DISAPPEARED in TY2024, and a cap
    read as a constant mis-computed every later return.
- **No-income-tax states (template `type: "none"` — FL):** do NOT search for
  income brackets; there are none, and an empty bracket list would keep the
  cache permanently INCOMPLETE. Record the pre-built FL template and fetch
  only its fetched fields:
  - FL: Search `"Florida general state sales and use tax rate {year}
    site:floridarevenue.com"` and confirm the authoritative DOR result is
    current/effective for `{year}` → `set state.sales_tax.state_rate` (decimal),
    with `--source` the general-rate page — the command pins it under
    `_sources["state.FL"]`. Then search
    `"Florida {county recorded in answers.md} county discretionary
    sales surtax rate {year} site:floridarevenue.com"` — use the county the
    taxpayer named in the Group 2 FL interview above (never infer it from the
    ZIP) → `set state.sales_tax.county_surtax_rate` (decimal, e.g. 0.01; 0.0 for
    no-surtax counties), with `--source` the county table/page — the command
    pins that one separately under `_sources["state.FL.county_surtax_rate"]`
    (the authoritative-host audit checks every source entry; `state.FL` remains
    the required state-block pin).
    If either current-year figure cannot be verified live, STOP under the
    freshness rule rather than retaining the template's `null`.
- For other states: Search `"{state} {year} income tax rates"`. `type:
  "none"` (the FL pattern — a positive declaration, never an empty bracket
  list) may be assigned ONLY to a state on this closed allowlist: **AK, FL,
  NV, SD, TX, WY** — plus **TN, but only for tax_year >= 2021** (Hall
  income-tax repeal; a pre-2021 TN profile still owed the Hall tax on
  interest/dividends, so a pre-2021 TN return keeps the generic
  estimate/unmodeled path, never `type: "none"`). **Generate the block with
  `create_state_template(state, tax_year=year)` — do NOT hand-build it.**
  For AK/NV/SD/TX/WY (unconditionally) and TN (only when `tax_year >= 2021`)
  this now returns a MINIMAL none-template (round-3 finding D — code, name,
  `type: "none"`, `no_income_tax: True`, an affirmative note; deliberately
  NO `brackets`/`standard_deduction`/`sales_tax` keys, the `sales_tax` county-
  surtax block stays FL-only) instead of the generic type-less fallback, so
  the cache doesn't nag INCOMPLETE forever for fields the design never
  fetches. Passing `tax_year=None` (or omitting it) for TN falls back to the
  generic fallback — never guess `type: "none"` for TN without a resolvable
  year. Every other state — **regardless of what a web search claims** —
  keeps the generic estimate/unmodeled path (fetch brackets normally; if
  none are found, leave the state block as an unfetched generic block, NOT
  `type: "none"`).
  **WA is NEVER `type: "none"`**: WA is commonly (and misleadingly)
  described as having no income tax, but it levies a real capital-gains
  excise tax on long-term gains (RCW 82.87) — declaring WA `type: "none"`
  would silently zero that liability. Keep WA on the generic
  estimate/unmodeled path until a dedicated WA capital-gains-excise module
  exists. **NH is excluded** pending confirmation that its repealed
  interest-and-dividends tax (RSA 77, repeal effective TY2025) leaves no
  residual filing obligation for every year this tool targets — NH keeps
  the estimate note until that's verified and its own module lands.
  This allowlist's single source of truth is
  `agents/lib/capabilities.py`'s `_NO_INCOME_TAX_STATES` set (plus its
  `_tn_hall_repealed`/`_TN_HALL_REPEAL_YEAR` TN year gate) — the two MUST
  be changed together; do not add a state here without adding it there
  (and vice versa).
- **PTET rate schedules when the taxpayer has K-1 income** (`has_k1_income`,
  for EACH state in `flags.k1_states` that is CA/NY/NJ — the K-1 ENTITY state,
  not the resident state): Search `"{state} pass-through entity tax PTET rate
  schedule {year}"` → populate `ptet.<STATE>` (top-level, keyed by entity
  state) as `{"schedule": [[lower, rate, base_tax], ...], "vintage": "<cite>"}`.
  The first bracket must start at `lower: 0`; CA is a single flat bracket
  (`[[0, 0.093, 0]]`), NY/NJ are graduated. Record the URL under `_sources` as
  `ptet.<STATE>`. Unfetched is NOT fatal — `/tax-ptet` falls back to its
  built-in statutory schedule with a LOUD unfetched-schedule warning naming the
  vintage (the NJ BAIT overhaul is the drift precedent); a corrupt block
  hard-errors. Fetch it so the price is version-controlled per year.

**Exchange rates (if foreign income):**
- Search: `"IRS yearly average exchange rate {year} {currency}"`

**Check for new tax legislation:**
- Search: `"federal tax law changes {year}"` to catch any mid-year legislation (like OBBBA for 2025)

**Write every figure with the ingest command — never edit `tax_law_{year}.json`
by hand.** The cache starts from the `create` command at the top of Step 4;
each fetched figure then goes in through:

```bash
sh agents/lib/run.sh agents/lib/tax_law_fetcher.py set <dotted.path> '<value as JSON>' --source '<authoritative URL>' --year {year}
```

For example `set federal.filing_statuses.mfj.standard_deduction 32200 --source
'https://www.irs.gov/pub/irs-drop/rp-25-32.pdf' --year {year}`, or a whole list:
`set federal.filing_statuses.mfj.brackets '[{"rate":0.10,"min":0,"max":24800}, …]'`.
The state block is written first from its template, then field by field:
`set state "$(sh agents/lib/run.sh agents/lib/tax_law_fetcher.py state {year} CA)" --source '<FTB URL>' --year {year}`,
then `set state.sdi_rate 0.012 --source '<EDD URL>' --year {year}`. The command
REFUSES, writing nothing, when the path is not in the schema, the value has the
wrong shape or is out of range (a rate above 1, a negative or ZERO amount — for an
indexed figure a zero is the unfetched state wearing a number — a bracket list
out of order, with a gap, or with a closed top bracket, any joint figure below
its single one, a whole block carrying fewer keys than the seed), the cache's
`tax_year` is not `--year`, or the source host is not authoritative — a refusal
is a wrong reading to fix, never a reason to open the file in an editor.
Brackets are half-open (each `min` equals the previous `max`); the IRS's "over
$11,925" reading, transcribed as `min` 11926, is normalised, not refused. The
last bracket's `max` is null. `--applies-to` names the period a figure covers
only for `historical_top_rate` (the calendar year in the key) and
`underpayment_rates` (a `YYYY-Qn` quarter, optional — the list dates itself);
for every other figure the period IS the tax year, and any other value is
refused at write time.

**The command pins and dates the block (H4 + E7).** Each `set` records the
`--source` URL under `_sources` keyed by block path — every fetched federal
rate/bracket block (`federal.filing_statuses`, `federal.capital_gains_rates`,
`federal.child_tax_credit`, `federal.amt`, `federal.salt_cap`,
`federal.qbi_threshold`, …), the single state block keyed by its **2-letter
code** (`state.CA`), `exchange_rates`, and `ptet.<STATE>` — the FIRST source a
block was read from, replaced only by a whole-block write — and writes the
retrieval time, applicable period and source under `_provenance`, keyed by the
FIGURE written (the block's pin for a whole-block write). `freshness` is per
block: the OLDEST entry under a pin dates the block, the OLDEST block decides,
and the report names it (`oldest_block`, `block_ages`), so re-fetching one
figure never re-dates its siblings. `freshness` also runs this command's
validation over the whole cache, so a figure hand-edited around the command
reads INCOMPLETE, never FRESH.
Use only PRIMARY sources accepted by
`tax_law_fetcher._source_host_authoritative` — any US-government `.gov` site
(irs.gov, ftb.ca.gov, edd.ca.gov, tax.ny.gov, nj.gov, ssa.gov, treasury.gov,
congress.gov, govinfo.gov) or its explicit non-`.gov` primary-authority
exceptions (`law.cornell.edu` and Florida DOR's `floridarevenue.com` today),
with an **https/http** URL. A blog or aggregator (smartasset, nerdwallet, a news
site) is NOT acceptable —
`tax_law_fetcher.check_freshness` reports a non-authoritative (or non-web)
recorded URL as **INCOMPLETE**, and a populated-but-unpinned fetched block as an
advisory. This is the chain-of-custody pin: an LLM-fetched bracket must trace to
a government source before it flows into a filed return.
