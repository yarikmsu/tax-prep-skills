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
  (the per-child ACTC cap, indexed — $1,700 for TY2024/2025; needed for the
  Schedule 8812 refundable split in `/tax-compute`)
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
- Search: `"IRS {year} capital gains tax brackets"`
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
  populate `federal.qbi_minimum` (`income_threshold` ~$1,000, `amount` ~$400,
  both indexed). ONLY needed for a TY2026+ filer with a small ACTIVE qualified
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
- For NY: Search `"New York {year} income tax brackets"`
- **No-income-tax states (template `type: "none"` — FL):** do NOT search for
  income brackets; there are none, and an empty bracket list would keep the
  cache permanently INCOMPLETE. Record the pre-built FL template and fetch
  only its `null` field:
  - FL: Search `"Florida {county recorded in answers.md} county discretionary
    sales surtax rate {year} site:floridarevenue.com"` — use the county the
    taxpayer named in the Group 2 FL interview above (never infer it from the
    ZIP) → `state.sales_tax.county_surtax_rate` (decimal, e.g. 0.01; 0.0 for
    no-surtax counties). Pin the source: `_sources["state.FL"]` →
    the floridarevenue.com page (allowlisted authoritative host).
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

Save all fetched rates to `tax_law_{year}.json` using the structure from `agents/lib/tax_law_fetcher.py`.

**Pin the source per block (H4).** As you populate EACH fetched block, record
the AUTHORITATIVE URL you fetched it from in the `_sources` map, keyed by block
path — every populated federal rate/bracket block (`federal.filing_statuses`,
`federal.capital_gains_rates`, `federal.child_tax_credit`, `federal.amt`,
`federal.salt_cap`, `federal.qbi_threshold`, …), the single state block keyed by
its **2-letter code** (`state.CA`), AND `exchange_rates` when foreign currencies
are used. Example:
`"_sources": {"federal.filing_statuses": "https://www.irs.gov/pub/irs-drop/rp-24-40.pdf", "federal.capital_gains_rates": "https://www.irs.gov/...", "state.CA": "https://www.ftb.ca.gov/...", "exchange_rates": "https://www.irs.gov/individuals/international-taxpayers/yearly-average-currency-exchange-rates"}`.
Use only PRIMARY sources — any US-government `.gov` site (irs.gov, ftb.ca.gov,
edd.ca.gov, tax.ny.gov, nj.gov, ssa.gov, treasury.gov, congress.gov,
govinfo.gov) or law.cornell.edu, with an **https/http** URL. A blog or
aggregator (smartasset, nerdwallet, a news site) is NOT acceptable —
`tax_law_fetcher.check_freshness` reports a non-authoritative (or non-web)
recorded URL as **INCOMPLETE**, and a populated-but-unpinned fetched block as an
advisory. This is the chain-of-custody pin: an LLM-fetched bracket must trace to
a government source before it flows into a filed return.

