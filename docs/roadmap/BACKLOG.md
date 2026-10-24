# Backlog

**This is the only file in `docs/roadmap/`.** It holds what is OPEN, what is
blocked and on whom, what was declined and why, and an index of what shipped.
It does not hold implementation history.

Written 2026-09-20, replacing eight roadmap files totalling 3,626 lines.

## The three rules this file exists under

1. **Shipped work LEAVES this file.** When an item ships, its narrative moves to
   `docs/changelog/epic-<letter>.md` — PR numbers, review rounds, what was
   refuted and why — and its row moves to the shipped index at the bottom. That
   is `docs/changelog/README.md`'s own resolution rule, and it stopped being
   applied around PR #120: Epic Z's sixteen pull requests, W2's package split
   and Y7 all accumulated here instead. That is how one file reached 1,604
   lines with 633 of them — about two fifths — inside entries marked DONE,
   under a heading called "Backlog".
2. **Status is stated ONCE.** It was previously stated in five places — a
   shipped table, a `— DONE` marker on the entry, the sequencing step, the
   not-started list, and sometimes a ruling annotation — and a 396-line test
   class existed to keep those five synchronized. That is a mirror, and this
   repository's answer to a mirror is to have one copy (AGENTS.md §1 on the PII
   gate, CLAUDE.md on the `.claude/` three-way mirror). An id appears in the
   Open section or in the shipped index, never both.
3. **A review is a dated snapshot and is never maintained.** The measurements in
   a review go stale the week after it is written. Five of the eight deleted
   files were reviews by their own titles, still asserting figures that had
   moved — the 09-05 review's Part
   2 said 3,424 tests against a suite half again that size today, put
   `preflight.py` at 2,732 lines when it has since grown by about 7%, and said
   "No linter, no type checker, no `pyproject.toml`", all three of which shipped
   in PR #90. A reader acting on that files a ticket for finished work. Reviews
   now live in git history; their CONCLUSIONS live here as items.

## Where the deleted files went

| Deleted | Its content now |
|---|---|
| `2026-09-05-staff-agent-review.md` | Items → the Open section or shipped index below. Epic Z narrative → `docs/changelog/epic-Z.md`. W2 and W3 slice 3 → `epic-W.md`. Y7 → `epic-Y.md`. The review itself → git history. |
| `2026-08-12-product-owner-review.md` | Shipped Epics R-U → `docs/changelog/epic-R-U.md` (new), including **R1's audit baseline**. Distribution analysis → below. |
| `2026-state-support-ny-fl-wa.md` | Its WA stories shipped as Z5/Z6; see `docs/changelog/epic-Z.md`. Its FL and NY story tables are NOT moved — that work shipped and is narrated in `epic-P.md` and `epic-Z.md` from other sources; the tables themselves are in git history. |
| `2026-tax-planning-features.md` | All twelve features shipped; see `docs/changelog/pre-epic.md` and the historical design snapshot below. Its `/tax-retirement` v1.1 deferrals subsequently shipped as X10; see `docs/changelog/epic-X.md`. |
| `2026-multi-agent-portability.md` | Epic Q shipped in PR #66; plan retained at [2026-08-03-multi-agent-portability.md](https://github.com/yarikmsu/tax-prep-skills/blob/017d2dd45ead/docs/superpowers/plans/2026-08-03-multi-agent-portability.md). |
| `2026-07-12`, `2026-07-17`, `2026-07-20` reviews | Epics I-O, all shipped; `docs/changelog/epic-{I,J,K,L,M,N,O}.md`. |

The historical design directory was retired after the 2026-10-03 review.
Unimplemented extensions are AA42-AA46 below. Maintained state contracts and
the template moved to `docs/contributing/state-module-{ny,nc,wa,template}.md`,
indexed in AGENTS.md §9. Origins below and historical changelog citations link
to the [pre-migration Git snapshot](https://github.com/yarikmsu/tax-prep-skills/tree/017d2dd45ead/docs/superpowers).
Old plans remain available there, including their contemporary roadmap
citations; they no longer direct contributor work in the current checkout.

---

## Status

**Order: California filers first** (owner, 2026-10-24). Every current user
files in California, so the plan finishes what a CA filer needs before anything
else: CA work, then federal work a CA filer reaches, then other states, then
the repository itself. An entry's priority is its severity for a filer it
reaches; the plan's order is who those filers are.

**Engineering: the original V, W and X queue is complete.** The 2026-10-03
review of the retired design directory recovered useful, unimplemented extensions;
AA44-AA46 below now carry the rest of that scope (AA42 and AA43 have shipped) instead of relying on historical plans.
The suite's size is stated nowhere: since AA8 the
MEASURED COSTS block in `.claude/hooks/skill-invariants.sh` counts only the
seven modules that hook selects, and a count restated here would be the mirror
this file was collapsed to remove.

**Product: blocked on one decision.** The repository is public, but Discussions
are off and there are zero non-owner users. Every distribution item has been
waiting on that since 2026-08-12.

**CI runs automatically.** Pull requests and main-branch pushes register CI,
including the per-trigger PII scan, and the weekly schedule audits the locks.
Trigger and range behavior is documented in
[`ci-and-locks.md`](../contributing/ci-and-locks.md).

---

## The plan, in order

| When | What | Why |
|---|---|---|
| **1. California filers: ready now** | **AA81** — show the CA payment total where the reconciliation is asked. **AA78** — let a foreign-tax filer's projection take a reviewed credit without the full AMT worksheet. **AA82** — check IRMAA tiers when `set` writes them. **AA61** — review the TY2023-2024 Form 1099-B boxes the import never names. **AA47**, **AA49**, **AA50** — the crypto follow-ups AA41 filed. | Nothing here waits on a publication. AA81 is the one CA item that can ship before FTB publishes TY2026. The rest is federal work a CA filer reaches: AA78 stops `/tax-project` for a routine profile, and AA82 lands before CMS's November table. AA61 is P2, because crypto on a 1099-B stopped with TY2024's forms. AA47 turns a permanent block into a return. AA49 must be reproduced first, and AA50 is a performance change, so it needs a before-and-after benchmark. |
| **2. California filers: as FTB and the IRS publish** | **AA83** — review the TY2026 Form 540. **AA84** — review TY2026 CA tax in Roth and scenario planning. **AA80** — review AA74's part-year return for TY2026, with the renter's credit six-month test. **AA48** — add the TY2026 Form 8949 boxes. **Y8** — fetch the 2027 §6621 quarters, the 2026 exchange rates and the TY2026 state tables through `set`. | AA83 decides whether any CA resident can file a TY2026 return here. AA84 has a December 31 deadline, because a 2026 Roth conversion is made this year. AA80 serves fewer filers. AA48 blocks every TY2026 crypto import. Since AA58, Form 2210 leaves a quarter the cache does not hold uncomputed rather than pricing it, so Y8 is the reminder to fetch it; its NY `table_bases` step serves no current user. The blocked table dates each check. |
| **3. California filers: larger extensions** | AA44-AA46 — prior-return import, multi-year Roth planning and expanded YoY reports. | Larger product extensions. |
| **4. Other states** | **AA79** — review TY2026 for the NY and NC payment forecasts. **AA63** — say when a local tax is withheld that no return here claims. | No current user files in New York, North Carolina or a locality with a return of its own. AA79 moves up the day one does, since the last 2026 estimate is due 2027-01-15. |
| **5. The repository** | **AA56** — tell the shipped index's two PR numberings apart. | It changes a test, not a return. |
| **Owner decision; excluded from the current implementation queue** | **Y1** — enable Discussions on the now-public repository, or demote the distribution goal in writing. | The TY2025 window ended 2026-10-15; the next opens in January 2027. |
| **Closed** | W7, W8, W9 — declined. Y3 — declined with a re-open trigger. | All three trade a working mechanism for tidiness. |

---

## Open

### AA — the 2026-09-20 product-owner review, and what later reviews filed under it

- **AA47 (P2, M): Give the 1099-DA cases AA41 blocks for good a way through.**
  AA41 links each form to one disposal by `txn_id` and requires matching units
  and proceeds, so four cases block for good: a broker using §1.6045-1(d)(10)'s
  optional aggregate method for qualifying stablecoins or specified NFTs (one
  form per stablecoin type, date sold possibly blank); one form line covering
  several partial fills; units that differ only in precision (box 1c asks for
  18 decimal places, which an import or broker may not carry); and a proceeds
  gap of a cent or so that no recorded fee explains. Two more block every way:
  a filled box the engine does not model (ordinary, accrued market discount,
  wash sale, loss not allowed, and the six 1099-DA boxes AA59 added: 3a's
  net proceeds, the QOF box 3b, customer-provided acquisition information,
  the optional aggregate method, first-sale NFT proceeds and CUSIP) refuses the WHOLE
  import, though the doc says to report that one transaction manually and no
  input can mark it so, and an acknowledged `digital_asset_option_exercise`
  leaves that sale in the import, priced without its premium; and a sale the import dates January 1
  that the broker dates December 31 in UTC fails both years, because a date a
  day off matches only within one year. Each needs its own evidenced
  treatment, not a tolerance. Until then the import blocks instead of
  guessing. Found by AA41's review.

- **AA48 (P1, S): Add the TY2026 Form 8949 boxes before TY2026 crypto imports.**
  `_REPORTING_REGIMES` holds TY2023-2025 only, so every TY2026 import with a
  filing-year disposal blocks. One that supplies `broker_reporting` is refused
  outright, exit 1, with no FIFO or form workpaper. A test pins that refusal on
  purpose: evidence the engine cannot read for its year is refused like
  malformed evidence. When the TY2026 Form 8949 instructions publish, add the
  entry and its row in `crypto-accounts.md`'s table from a year-pinned
  `irs-prior` copy, as the source test requires. Found by AA41's review.
  **Blocked on the IRS, as of a live check on 2026-10-13:** the year-pinned
  [TY2026 Form 8949 instructions](https://www.irs.gov/pub/irs-prior/i8949--2026.pdf)
  returned HTTP 404, and the IRS's
  [current instructions](https://www.irs.gov/pub/irs-pdf/i8949.pdf) still named
  TY2025. Keep the unreviewed-year refusal until the TY2026 instructions
  publish. Do not carry the TY2025 boxes forward, and do not pin the entry to a
  URL that does not resolve yet: the source test checks the prefix and the
  year, not that the file exists. AA55 re-read every TY2026 Form 1099-DA box,
  whether the import reads it or not, and each kept its meaning, so the entry
  needs no per-year input map.

- **AA49 (P3, M): Tie each form-backed digital-asset row to its own broker
  figures.** The `capital_proceeds` tie subtracts one aggregate,
  `crypto_summary.reporting_proceeds_above_forms`, from the result side. A stale
  figure surviving a re-merge, or one for a 1099-DA the ledger does not hold,
  would shift the whole Schedule D tie and could offset an equal over-merge of
  result-side proceeds. Carrying each routed row's broker proceeds and summing
  those would remove the special case. Not reproduced: reproduce it first.
  Found by AA41's review.

- **AA50 (P3, XS): Build the importer's event map from filing-year disposals
  only.** `build_crypto_import` parses every transaction's date and converts
  every fee to cents, buys and receives included, to look up the filing-year
  disposals that reconcile against broker forms, though `_match_account_fifo`
  has already parsed those dates. Negligible at realistic sizes. Found by
  AA41's review.

- **AA56 (P3, S): Tell the shipped index's two PR numberings apart.**
  `test_every_shipped_row_names_a_pull_request_that_really_merged` builds its
  merged set from every subject in history that ends in `(#N)`. Pull-request
  numbers restarted at #1 when the repository was recreated on 2026-10-10, and
  old and new merges already share #3, #4 and #8, so a row naming a new number
  that never merged passes on an old merge carrying it: a new #5 would pass on
  one from 2026-03-08. The test needs a way to tell which numbering a row
  means, such as the merge's side of that date. Found by the review that
  recorded Y2 as shipped.

- **AA61 (P2, S): Review the TY2023-2024 Form 1099-B boxes the import never names.**
  Crypto reported on Form 1099-B for TY2023-2024 reconciles through the same
  importer, whose 1099-B key set AA59 left empty: AA55 reviewed Form 1099-DA
  only. The 1099-B has unread boxes of its own, among them box 3's
  collectibles checkbox, whose gain is taxed at up to 28%, its QOF checkbox,
  box 6's gross or net proceeds and the CUSIP field. Re-read every box of each
  year's 1099-B against that year's instructions and form, record them in a
  table like AA55's, and give each a key in `_FORM_UNSUPPORTED_BY_TYPE` or a
  stated reason. Until then the gap is silent: a TY2024 NFT sale with box 3's
  collectibles box checked routes at capital-gain rates rather than up to
  28%. Found by AA59's design.

- **AA63 (P2, S): Say when a local tax is withheld that no return here claims.**
  `/tax-parse` records local tax withheld only for NYC and Yonkers, whose
  amounts IT-201 and IT-203 settle (a 1099-R's or W-2G's blocks until AA60).
  Local tax withheld anywhere else, such as an Ohio or Pennsylvania
  municipality's on a W-2 box 19, 1099-R box 17 or W-2G box 17, reaches
  Schedule A line 5a (AA59) but no ledger category, so nothing tells the
  taxpayer that the locality may expect a return of its own or owe a refund,
  and the toolkit computes no local return. Add an advisory category like
  `other_state_withholding`, or a capability rule that names the local
  return, deciding between them by whether the payment is ever claimed
  without one. Found while fixing AA59's Schedule A recipe.

- **AA78 (P1, S): Let a foreign-tax filer's projection take its credit without
  the full AMT worksheet.** With no `amt_projection` worksheet, `/tax-project`
  refuses the payment projection when `has_foreign_tax_paid` is true or the
  profile carries a prior-year AMT credit (`_unmodeled_credit_screen` in
  `agents/lib/projection/exposure.py`). The refusal is right on its own terms:
  the projection's foreign tax credit stays zero unless that worksheet supplies
  `regular_foreign_tax_credit`, so the AMT-excluding estimate would over-state
  the payment. But foreign tax paid is routine for anyone holding an
  international fund in a taxable account, and many such filers can take the
  credit under §904(j)'s election without Form 1116. Before AA42 they got a
  silent over-statement; now they get a refusal whose easiest way around is
  setting the flag false, which the return's own foreign tax credit section
  also keys on (`result_template.json`). Accept a reviewed regular foreign tax
  credit on its own, keep the AMT-not-modeled note, and keep refusing a
  prior-year AMT credit, whose filers are the ones the worksheet exists for. If
  the path is limited to the §904(j) election, its ceiling is statutory but
  must still be fetched or listed in AGENTS.md §2. Found by AA42's review.

- **AA79 (P2, M): Review TY2026 for the NY and NC payment forecasts.** AA43
  shipped for the TY2025 form year only: `_REVIEWED_YEARS` in
  `agents/lib/tax_computations/state/planning.py` holds 2025 for CA, NY and
  NC, and `/tax-project` refuses a TY2026 NY or NC forecast ("State forecast
  form year has not been reviewed"). TY2025's extended deadline passed on
  2026-10-15, so a TY2025 forecast prices no payment still to be made, while
  the last 2026 estimate is due 2027-01-15. As of a live check on 2026-10-24,
  New York's [2026 IT-2105 instructions](https://www.tax.ny.gov/pdf/2026/inc/it2105i_2026.pdf)
  and North Carolina's [2026 estimator worksheet](https://www.ncdor.gov/taxes-forms/individual-income-tax/estimated-income-tax/2026-individual-income-tax-estimator-worksheet)
  are published. Decide whether the review can rest on those, re-reading the
  TY2025 forms' deduction, credit and local contracts against them, or must
  wait for the TY2026 IT-201 and D-400; if it must wait, add AA79 to the
  blocked table. CA's TY2026 reviews are AA83, for the return, and AA84, for
  planning. P2 while no current user files in NY or NC (owner, 2026-10-24);
  the 2027-01-15 estimate makes it P1 the day one does. Found by AA43's review.

- **AA80 (P2, M): Review AA74's part-year return for TY2026, with the renter's
  credit six-month test.** AA74 computes a reviewed CA part-year return for
  TY2025 only. The renter's credit is computed on neither form: it is a manual
  capability rule, and on the 540NR path `agents/lib/capabilities.py` swaps
  only the line, to Form 540NR line 61. Its note warns one way, that omitting
  an eligible credit over-states CA tax, but a part-year filer's likelier error
  is the opposite. The 540NR qualification record asks whether the filer was a
  California resident for at least six full months of the year and paid rent
  for at least half of it on a California principal residence
  ([2025 540NR booklet](https://www.ftb.ca.gov/forms/2025/2025-540nr-booklet.pdf),
  page 25), and one who fails either gets no credit. A banner does not fix a
  filed position nothing asked about: when FTB publishes the 2026 booklet,
  review the part-year return against it and give the rule a 540NR note naming
  that test, as `ca_use_tax` and `ca_noniso_amt` already have, with a test
  pinning the wording. Found by AA74's review.

- **AA81 (P2, S): Show the CA payment total where the reconciliation is
  asked.** The CA builder refuses until `estimated_payments.ca_verified` is
  true, the answer that the payments were reconciled against the FTB account
  (`agents/lib/return_builder.py`). It refuses a repeated `payment_id`, but one
  payment entered twice under two ids passes once that answer is given, and
  under-states the balance due by the whole payment. Reconciling against the
  account is the right control, since two equal payments on one day can both
  be real, but a total is easier to check against the account than a list.
  Where `ca_verified` is asked, state the payment count and the total claimed
  for the credited year, and record a question in `answers.md`, not a refusal,
  for two records sharing state, credited year, kind, person, date and amount.
  Found by AA70's review.

- **AA82 (P3, XS): Check the IRMAA tiers when `set` writes them.** The
  fetcher's `set` checks a `federal.irmaa.tiers.*` list for shape and order
  (`_check_ascending` in `agents/lib/tax_law_fetcher.py`). AA53's
  `validate_irmaa_tiers` (`agents/lib/tax_computations/federal/irmaa.py`),
  with the statutory row count, the printed boundaries and the first-dollar
  refusal, runs only when `/tax-roth-convert` or `/tax-project` reads the
  table. A malformed table still cannot price anything, but the refusal
  arrives at planning time, far from the fetch that caused it. Call the same
  validator from `set` with the cache's `tax_year`, the premium year the read
  already uses, before CMS publishes the next premium-year table in November.
  Found by AA53's review.

- **AA83 (P1, M): Review the TY2026 Form 540 before TY2026 CA returns.** The
  CA return path is reviewed for TY2025 only, at four gates:
  `_CA_540_REVIEWED_YEARS` in `agents/lib/tax_computations/state/ca.py` holds
  2025 for the Form 540 line map ("Form 540 line map has not been reviewed for
  this tax year"), for Schedule CA, which the tie-out gate rebuilds, and for
  FTB 5805; `ca_credits.py`'s credit lines accept 2025 alone; and the filing
  package refuses a checked Form 540 outside that set. Until each is reviewed,
  no CA resident can finish a TY2026 return here. This was one clause in Y8's
  plan row. When FTB publishes the 2026 Form 540 booklet
  ([its year-pinned URL](https://www.ftb.ca.gov/forms/2026/2026-540-booklet.pdf)
  returned 404 on 2026-10-24) and the year's Schedule CA and FTB 5805
  instructions, re-read every line AA69's checked builder computes, and every
  line it leaves manual or refuses, against them before adding 2026; carry no
  TY2025 line forward unread. AA80 is the part-year half. Found by the
  2026-10-24 California-first review of the plan.

- **AA84 (P1, S): Review TY2026 for CA tax in Roth and scenario planning.**
  `_REVIEWED_YEARS` in `agents/lib/tax_computations/state/planning.py` holds
  2025 for CA, a gate separate from AA83's, so `/tax-roth-convert` and
  `/tax-scenario` price no CA tax for TY2026 and print "State/local tax not
  modeled". The degrade under-states, as AGENTS.md §4 allows, but a 2026
  conversion has to be made by December 31, so a CA filer deciding one now sees
  no state cost. The planner reads CA's brackets, standard deduction and
  exemption credits from the cache, and the 2026 Form 540-ES worksheet prices
  tax from the 2025 table, so it cannot stand in. This waits on FTB's TY2026
  indexed amounts, which FTB announced for TY2025 in its
  [October 2025 Tax News](https://www.ftb.ca.gov/about-ftb/newsroom/tax-news/2025/10.html)
  (the October 2026 issue returned 404 on 2026-10-24), and then on re-reading
  the planner's CA contracts against them. Found by the same review.

### Recovered design follow-ups — reviewed against the engine on 2026-10-03

These are extensions, not unfinished portions of the shipped v1 deliveries.
The historical specs supply their origin; the scope stated here governs the
work. The split capital-loss carryforward, Form 1040 line 36 rollover, IRA and
AMT basis rollover, NIIT projection, full-status capital-gains cache and
CA/NY/NC withholding true-ups already exist and are not reopened.

- **AA44 (P3, L): Import a prior return PDF for first-time rollover users.**
  `load_prior` reads toolkit archive JSON, not a software-exported prior return;
  `/tax-verify` is a comparison flow rather than an archive importer. Start with
  documented TurboTax/FreeTaxUSA PDF forms, explicit year and worksheet mappings,
  masked identity and user-reviewed provenance. Missing carryover worksheets
  stay unresolved instead of being reconstructed from summary lines. Proprietary
  software formats require a separate supported-format decision.
  Origin: [2026-05-24-tax-rollover-design.md](https://github.com/yarikmsu/tax-prep-skills/blob/017d2dd45ead/docs/superpowers/specs/2026-05-24-tax-rollover-design.md) §Open follow-ups.

- **AA45 (P3, L): Model multi-year Roth conversion plans.**
  The ladder currently sweeps one tax year. Add explicit annual income/balance
  assumptions and basis carry between years, preserve each year's computation
  inputs, and distinguish fetched covered-year law from hypothetical future-law
  assumptions. Show tax, Medicare premiums and distribution effects separately;
  a future-year forecast must not masquerade as a filing computation.
  Origin: [2026-05-26-roth-conversion-ladder-design.md](https://github.com/yarikmsu/tax-prep-skills/blob/017d2dd45ead/docs/superpowers/specs/2026-05-26-roth-conversion-ladder-design.md) §Out of scope.

- **AA46 (P3, M): Extend YoY reports to multi-year, saved and PDF outputs.**
  `yoy_diff` still compares two years and renders inline Markdown. Reuse its
  curated movers for optional additional archive columns, a secured standalone
  deliverable and an explicitly ordered PDF embed. Any narrative must follow
  the measured changes without asserting unsupported causes. Keep arbitrary
  recursive field diffs out; a separate `/tax-diff` skill is demand-gated.
  Origin: [2026-05-25-yoy-diff-design.md](https://github.com/yarikmsu/tax-prep-skills/blob/017d2dd45ead/docs/superpowers/specs/2026-05-25-yoy-diff-design.md) §Open follow-ups.

Other explicit historical boundaries remain demand-gated scope candidates:
NOL computation/rollover; ISO dual-basis and unsold-lot rollover; crypto
specific identification, NFT/DeFi/business classification; Roth distribution
ordering and inherited IRAs; PFIC QEF/MTM/transition elections and broader
Form 8938 computation; NC business/recovery/Taxed-PTE work and additional NY
credit worksheets. The current capability gates and manual-input contracts
continue to describe those limits. These are not evidence that the shipped
v1 features failed their definitions of done. The existing declined packaging
and vendor-CI rulings remain closed.


### Y — distribution and product

- **Y1 (P1, XS): Enable Discussions on the public repository.**
  The repository is public, verified 2026-10-15; Discussions are still off.
  Every artifact the publish gate asked for is in place: R1's full-history audit
  (one adjudicated-benign finding, baseline recorded in
  `docs/changelog/epic-R-U.md`), R3's intake surface, R5's QUICKSTART with
  verified output, R2's standing controls, the pre-push PII scan, hashed locks,
  Apache-2.0. There is no remaining engineering precondition. Discussions are
  what R6's pinned thread needs, and that thread is the ONLY signal the design
  permits — the tool is local-first, so filing is structurally unobservable and
  the stated success criterion ("a stranger files a return with it") cannot be
  evaluated without it.

- **Y8 (P2, XS): Fetch the TY2026 figures still unpublished at season open.**
  The season-open runbook's "Verified 2026-10-12 (Y2)" record names three: the
  2027 §6621 quarters, the IRS yearly average exchange rates for 2026, due in
  January, and the state figures that publish with the year's full tables.
  With them goes the step AA14 and AA28 left for TY2026: when the NY State and
  City tables publish, compare each with its rate schedule and fetch
  `state.tax_table.table_bases` and `state.nyc_tax.tax_table.table_bases`, per
  the runbook's AA14 section. The freshness gate reads INCOMPLETE on the
  exchange rates and the state figures, so neither can be missed at compute
  time. It does not gate the quarters: Form 2210 carries the last cached rate
  through April 15, 2027 with only a note, and an extension's interest window
  reaches later quarters still. Fetch each through `set` as it publishes, the
  quarters included; gating them is AA58. Filed by the review that recorded Y2
  as shipped.

---

## Blocked on an owner ACTION or an outside publication, not a ruling

| Item | What it waits on |
|---|---|
| **Y1** | The owner enabling Discussions. Verified 2026-10-15: public, Discussions off, 0 stars. |
| **Y8** | The 2027 §6621 quarters, published one quarter at a time; the IRS's 2026 yearly average exchange rates, in January; and the TY2026 state tables, NY's included. |
| **AA48** | The IRS publishing the TY2026 Form 8949 instructions. The entry dates the last check, and the season-open runbook's Form 8949 step repeats it. |
| **AA83** | FTB publishing the 2026 Form 540 booklet and the year's Schedule CA and FTB 5805 instructions. The booklet's year-pinned URL returned 404 on 2026-10-24. |
| **AA84** | FTB publishing the TY2026 indexed amounts. Its October 2026 Tax News returned 404 on 2026-10-24. |
| **AA80** | FTB publishing the 2026 Form 540NR booklet, whose renter's credit qualification record and line 61 the review reads. Its year-pinned URL returned 404 on 2026-10-24. |

## Declined, with the reasoning kept so it is not re-proposed

- **W7 — ADRs for multi-paragraph "why" comments. DECLINED.** AGENTS.md §9's size
  criterion governs the INSTRUCTION FILES, which are capped because Codex
  truncates them silently. Source comments have no such cap and no such reader —
  what they have is the next person editing the line, and moving the reasoning to
  `docs/adr/` removes it from exactly there. Every defect fixed in the 09-11 and
  09-12 sessions was found by reading one of those paragraphs beside the code it
  guards. If `preflight.py`'s ~890 comment lines are the concern, the question is
  whether they are TRUE and load-bearing, not where they live.

- **W8 — renumber the fractional skill steps. DECLINED.** The fractional numbers
  are what keeps every existing cross-reference valid. `tests/test_portability.py`
  pins every `/tax-safe-harbor step N` an engine module names against the skill's
  real headings; AGENTS.md §4 cites "Step 12.7" and "Step 0.7" by number; the
  changelog and merged PR bodies cite them too. A renumber breaks a reference in
  each for a tidier sequence, and the one thing fractional numbering buys — that
  step 6.5 is visibly a later insertion between 6 and 7 — is information, not
  accretion.

- **W9 — trim `_comment_*` keys in the profile template. DECLINED for
  `foreign_holdings_detail._comment` specifically.** Measured at 2,923 characters
  of 17,663. It is not prose: it is the field-by-field schema an agent must
  follow to fill the block, and several clauses are ALARMS — "the signed amount
  MUST be recorded because the tie-out treats a `disposed` holding with no finite
  value as an unverifiable event and BLOCKS the return"; "every taxable fund needs
  a UNIQUE one"; "carry BOTH `distribution_date` and `disposition_date` when both
  occur". The agent that fills this block reads this template. The smaller keys
  are a different question and could go.

- **Epic Q's v2 parking lot — DECLINED at portability v1 (PR #66), and one of
  the four is now live again.** Recorded here because the reasons are the kind
  that get re-proposed: **plugin/marketplace packaging** (a Codex
  `.codex-plugin/plugin.json` plus a repo marketplace, a `kimi.plugin.json`, and a
  Cursor plugin — the last blocked outright because the public marketplace
  requires a PUBLIC repository, so Y1 gates it); **MCP server packaging** (one
  `.mcp.json` already serves Claude and Kimi natively, and the Codex/Cursor config
  is documented in `docs/contributing/agents-compat.md` instead); **Windows
  symlink automation**, with the superpowers workflow docs staying Claude-only;
  and **a per-agent CI matrix**, declined because there are no vendor CLI licenses
  in CI, with a monthly "agent-surface drift" re-verification checklist as the
  substitute. AA2 restored the Python/Windows CI matrix in #189; that does not
  exercise vendor agent sessions or assign an owner to the drift checklist.
  Note this is a DIFFERENT ground from the Distribution table's
  refusal of a plugin bundle below, which turns on §1 forbidding a real return in
  a hosted sandbox rather than on packaging mechanics.

- **Y3 — package `agents/lib` as a distribution. DECLINED at Q11, with a re-open
  trigger.** At Q11 the repository was not public, so there was no consumer to
  install a distribution — the largest contract change in the backlog against
  zero demand signal, and it defeats the venv-refusal guard by design. It is
  public now, verified 2026-10-15, and still has no consumer outside a clone, so
  the trigger has not fired. **Re-open when a second consumer of the engine
  exists outside a clone.**

## Rulings

Recorded rather than asked mid-flight (owner instruction 2026-09-07: document the
question, skip that task, take the next). **None is re-opened by an implementer
without the owner.** The full question, what each answer would have cost, and the
reasoning are in git history and — for those that shipped — in the changelog entry
for the PR that carried them.

| # | Question | Ruling | Date |
|---|---|---|---|
| Q1 | V3's first golden: whose return shape? | The synthetic CA + federal MFJ return, hand-computed from the worksheets. | 2026-09-07 |
| Q2 | Where do V3's model-driven runs happen, on whose budget? | Locally, on demand, before a release tag. The workflow ships DISABLED; no API key becomes a repository secret. | 2026-09-07 |
| Q3 | Which agents form V3's cross-agent matrix? | One for the first slice, growing when a second is actually in use. Cost accepted: the five-agent portability promise stays unexercised. | 2026-09-07 |
| Q4 | V6: what counts as "isolation established"? | The implementer picks the strongest mechanism the installed build supports and writes down what it does NOT stop. If nothing clears the adversarial bar, ship the sequential fallback SAYING so. | 2026-09-07 |
| Q5 | Y2's uncertified reviewer definitions. | Write the procedure, touch nothing. The digest pin makes the state visible rather than silent. | 2026-09-07 |
| Q6 | Should the toolkit SHIP a font? | Keep the declaration; ship no font. A Cyrillic or CJK name prints as empty boxes and page 1 says so. Committing a binary would widen the PII gate. | 2026-09-07 |
| Q7 | Three sibling forms, three contracts for one itemize-vs-standard election. | Require `standard_deduction` on all three. "Breaks callers" measured at 22 test edits and 3 skill paragraphs, all in this repository. Shipped #156. | 2026-09-13 |
| Q8 | May an indexed figure that changes a return DEGRADE, when §2 says STOP? | Degrade only when BOTH hold — the output is ADVISORY and the fallback UNDER-states; otherwise STOP. Now AGENTS.md §2. Shipped #158. | 2026-09-13 |
| Q9 | Should `state/ny.py` VALUE-validate filing status? | Convert. The only input that starts being refused is input already taking the wrong branch. Shipped #156. | 2026-09-13 |
| Q10 | May a skill state a figure both statutorily NAMED and FETCHED? | Yes for §164(b)(7): state the two anchors, the 101% rule and the 2030 reversion, name the cache key, pin to an engine constant by test. §2's third enumerated exception. Shipped #158. | 2026-09-13 |
| Q11 | Y3: package `agents/lib` as a distribution? | No, with a re-open trigger. See Declined above. | 2026-09-13 |
| Q12 | W3: what runs the hook before the venv exists? | A bash shim of a few lines, and W3 proceeds. Fail-DEFERRED, not fail-open. | 2026-09-13 |
| Q13 | AA3: what does MEASURED COSTS record, when no sitting on this host can meet its idle-machine precondition? | Counts, ratios and relative ordering; no absolute seconds. The hook timeout and the per-test headroom budget remain as compared thresholds, and one cold pass is re-timed only when either changes. | 2026-09-21 |
| Q14 | AA19: may the cached SALT cap override a conflicting statutory amount? | Refuse mismatches. Accept the statutory amount at cents or its nearest whole dollar (half up); a law change requires updating the schedule. AGENTS.md §2 changes with the check; the auditor edit stays gated on Y2. | 2026-10-03 |

Q13's "per-test headroom budget" is two constants, the per-test rate and the
absolute minimum under it. Which constants re-time a cold pass when they change
is stated once, in `skill-invariants.sh`'s MEASURED COSTS block; the row above
keeps the owner's words.

---

## Distribution

Carried forward from the 2026-08-12 review, because it is the live plan and not a
finding. **The landing point is fixed by AGENTS.md §1, not chosen:** "never run a
real return on a vendor cloud VM" applies to every hosted channel including a chat
sandbox, so **every hosted channel is advisory-only and PII-free; filing is always
local.** The funnel has one shape and `docs/QUICKSTART.md` is its landing page.

| Option | Verdict |
|---|---|
| **GitHub-native** — publish, add topics, let the aggregators that index public `SKILL.md` repos find it | **Foundation.** It is Y1 anyway. |
| **Skill-upload channel** — the advisory skill in Claude.ai and the plugin marketplaces | **Yes.** Reaches non-developers; the chat→clone jump is the weak link. |
| **Plugin bundle of the whole toolkit** | **No.** §1 blocks real returns in a hosted sandbox, so it only serves local-agent users, who can already clone. |
| **Seasonal posts** timed to Oct 15 / Jan 15 / season open | **Yes.** Highest-intent traffic available, no infrastructure; one-shot, so it needs Y1 first. |

**Competitive context.** `filedcom/opentax` and `habutax` already occupy "an
open-source tax engine exists". The differentiation is not the engine — it is the
integrity chain (parse ledger → tie-out gate → capability gate → source-pinned law
→ limitations manifest) and running natively across five agents. The README must
say that in its first screen.

---

## Shipped

The index. Narrative lives in `docs/changelog/` — one file per epic, listed in
`docs/changelog/README.md`. Pull-request numbers restarted at #1 when the
repository was recreated on 2026-10-10, so a low number can name two pull
requests; a row's numbers are read from its merge commits' subjects, and where
two commits carry the same one, the date tells the numberings apart. The test
that checks each row cannot yet; that is AA56.

| Item | PRs | Remaining scope / follow-ups |
|---|---|---|
| AA23 | #203, #204, #205, #206, #217 | — |
| V0 | #85 | — |
| V1 | #109, #110, #111 | — |
| V2 | #108 | — |
| V3 | #97, #98, #107, #116, #117, #141 | — |
| V4 | #87 | the auditor's `_provenance` check shipped with Y2 in #7 |
| V5 | #103, #104 | — |
| V6 | #99, #100, #101, #106 | — |
| V7 | #115 | needs `TAX_WORKSPACE` set, or it isolates nothing |
| V8 | #91 | — |
| V9 | #92 | — |
| V10 | #93, #102, #139, #140 | — |
| V11 | #95 | — |
| W1 | #90 | — |
| W2 | #148, #149, #150, #151, #152 | — |
| W3 | #163, #164, #230 | Single invariant-pass runtime; cheap Bash change boundary and red memory preserved. |
| W4 | #90, #118 | the MEASURED COSTS seconds were retired by Q13 rather than re-measured |
| W5 | #90, #166 | Floor-reading follow-up shipped as W5a; its remaining comparisons are listed below. |
| W5a | #191 | the matrix legs, the tool targets, `MIN_PYTHON` and the prose still state the floor, compared rather than read; the lock headers are uv's record and stay |
| W6 | #105 | — |
| W10 | #208, #209, #210, #211, #227 | Registry and wiring; generic year-bound adjustment workpapers and explicit local-tax routing. |
| W11 | #226 | Shared state true-up specification; public report behavior retained and NY timing-note crash fixed. |
| W12 | #225 | Test-owned hook input declarations and independent read/import audit. |
| X1 | #89 | the auditor's keying note was corrected with Y2 in #7 |
| X2 | #86 | — |
| X3 | #88 | — |
| X4 | #110, #142 | — |
| X5 | #143, #144, #155 | Wrapped and paired limit detection completed under X9. |
| X6 | #154 | The statutory SALT step schedule remains the fallback; #216 requires cached amounts to agree at cents or the nearest whole dollar (half up), before phasedown. |
| X7 | #218 | future-sale tax and chained wash-sale repricing remain outside the replacement basis plan |
| X8 | #159 | Engine entry-point status validation: direct checks or declared delegation probed at both ends |
| X9 | #228 | Wrapped and paired amounts, Markdown block boundaries, code/CLI handling and shared figure gates. |
| X10 | #224 | Roth catch-up split, confirmed HSA eligibility and fetched per-account state conformity. |
| Y2 | #7 | The figures still unpublished on 2026-10-12 are Y8, and Y2's review filed AA52-AA54. |
| Y4 | #112 | — |
| Y5 | #113 | — |
| Y6 | #114, #119, #165 | — |
| Y7 | #120 | no Windows coverage — the Windows job runs two modules |
| Z4 | #219 | TY2025 full-year D-400; other form years and farmer/annualized NC-40 worksheets remain manual |
| Z5 | #220 | TY2025 excise; separate households and special federal normalization remain out of scope. Harvest pricing shipped as Z6. |
| Z6 | #223 | WA excise harvest pricing, crypto domicile workpapers and separate WFTC reminder. |
| Z8 | #221 | TY2025 NY source allocations and NYC IT-360.1; other-state returns/credits, MFS, mixed spouses, IT-230 ratios and IT-203 Yonkers remain manual/refused; NY forecasts are skipped |
| Z1 | #123, #124-#137 | IT-201 remains full-year NY resident only. Reviewed TY2025 IT-203 and NYC IT-360.1 plans shipped as Z8; statutory second residency and other taxable-state returns retain their gates. AA13 supplies IT-201 line 47a, and AA12 supplies whole-year Yonkers line 55 or non-joint nonresident line 56. IT-201 part-year City/Yonkers lines 50/57, special condition Y1, joint Y-203, IT-196 worksheets, other taxes, refundable-credit amounts, use tax and city-level day counts retain manual work or refusals on `meta.limitations`. |
| Z2 | #130 | City taxable income, the City household credit, the MCTMT and the line-58 caps; the City resident tax, line 47a, came later with AA13, and Yonkers lines 55-56 with AA12 |
| Z3 | #131 | — |
| Z7 | #121 | domicile as a GATE is deliberately not modeled |
| AA1 | #171 | the EIC is DISCLOSED, not computed — out of persona by the 2026-07-12 ruling |
| AA3 | #172 | Whole-suite count removed by AA8, pass timing added by AA9, and review leftovers addressed by AA10. The timing lint's accepted limits remain listed under AA10. |
| AA7 | #174 | IT-201 remains resident-only; IT-203 uses the declared plan in #221, while other taxable-state obligations retain their gates |
| AA13 | #175 | IT-201 part-year City line 50 remains manual/refused; reviewed TY2025 IT-203 NYC IT-360.1 plans shipped as Z8. |
| AA11 | #175 | — |
| AA12 | #176 | Part-year Yonkers, special condition Y1 and joint Y-203 remain refused/manual. Line-58 guards and household-method checks shipped as AA15, AA29, AA30 and AA32. |
| AA6 | #178 | three blocks cannot be sized from public data (statutory residency in a second state, a PFIC event, out-of-state income other than a commute); the figures are tax year 2023 and need a yearly refresh |
| AA16 | #179 | The compute step forms modified AGI. AA23 re-prices the SALT phasedown from recorded inputs; legacy or unsupported planning inputs retain held-baseline caveats. PTET still holds Schedule A. |
| AA20 | #180 | AA23 re-prices supported Schedule A inputs, including medical/SALT limits, confirmed charitable caps, elections and §68. Legacy or unsupported inputs stay held with caveats or refuse where required. |
| AA21 | #181 | Complete Schedule A forecasts re-price under AA23; the legacy declared-total path retains its warning. Non-itemizer §170(p) support shipped as AA22. |
| AA22 | #182 | AA23 owns the itemize-or-standard election. Gifts remain declared inputs; an unfetched required cap refuses until its block is fetched. The note's acknowledgment-threshold policy remains the owner's call. |
| AA25 | #185 | Exposure-scan dependence is verified under AA26; its remaining test limits are listed below. |
| AA4 | #184 | Walk shape remains pinned; AA24 added comparison with the configured timeout. |
| AA5 | #186 | — |
| AA8 | #186 | the suite's size is stated nowhere, and the scale row's "about twice" a cold pass is a reader's to keep, not a check's |
| AA17 | #188 | a part-year return across the border is blocked, not modeled, and so is a non-citizen's dual-status federal year |
| AA14 | #189 | a cache without `table_bases` gets a note, and the row can be a dollar off either way; the TY2026 check is Y8 |
| AA27 | #189 | — |
| AA28 | #190 | the TY2026 `table_bases` check is Y8 |
| AA29 | #192 | AA30 moved household screening to Step 0.7; AA32 requires computation methods beside flagged household values. A typed method is not a receipt-bound proof. |
| AA18 | #193 | an acknowledged territory return adds the excluded income back by hand; a territory mailing address on a stateside resident blocks too, the degrade direction |
| AA31 | #194 | — |
| AA30 | #195 | AA32 added household computation methods; an acknowledged unsupported household's City or Yonkers tax remains manual. Supported Z8 plans use their variant guards. |
| AA35 | #238 | Verified federal/CA charitable vintage ledgers through setup, computation and rollover; missing worksheets require explicit resolution. |
| AA36 | #240 | Local paystub intake reconciles confirmed jobs into projection YTD with source and period evidence, masked extraction and secured publication. |
| AA37 | #241 | Confirmed regular Social Security worksheets reprice taxable benefits across scenario stacks and Roth rungs; the original income frame and covered-year evidence remain bound. |
| AA38 | #242 | Reviewed full-year TY2025 CA/NY/NC state/local liability reprices independently in scenarios and Roth sweeps; missing worksheets, allocations and unreviewed form years stay named limitations. |
| AA39 | #243 | Independent primary/spouse Form 8606 worksheets bind owned IRA balances and source allocations, preserve separate closing basis through rollover, and label individual reports and Roth planning. |
| AA40 | #244 | Account-specific crypto FIFO moves reviewed original lot slices through owned transfer links; missing inventory/evidence blocks completion and untagged universal imports from 2025 are diagnostic-only. |
| AA41 | #6 | Reviewed years TY2023-2025 only: the TY2026 boxes are AA48, the cases that block for good AA47, a per-row broker-proceeds tie AA49, a narrower event map AA50, the 1099-DA boxes it never names AA59. |
| AA19 | #216, #7 | — |
| AA51 | #7 | A PDF the auditor's own fetch saves outside the export stays unread by decision, so such a figure goes UNVERIFIED; the export and the engine still answer four questions differently, filed as AA52. |
| AA55 | #10 | Input boxes only: TY2026's Form 8949 boxes are AA48, and the 1099-DA boxes the import never names are AA59. |
| AA59 | #12 | CA and NY 1099 withholding blocks the tie-out until AA60 routes it; the TY2023-2024 Form 1099-B boxes are AA61, specific identification AA62, local tax no return here claims AA63. |
| AA77 | #15 | Schedule P Part II only: Part III's credit limits start from Form 540 line 35, after the exemption credits, and are AA72's. |
| AA75 | #16 | — |
| AA68 | #17 | The CA family credits (lines 75-77), FTB 3506 and the renter's credit carry manual notes rather than computing. |
| AA60 | #18 | Withholding for a state no return here prepares stays an advisory other_state_withholding record with a state-coded breakdown. |
| AA69 | #19 | Reviewed TY2025 form only; a claimable child's Schedule P line 22 exemption limit refuses rather than computing. |
| AA70 | #20 | A payment entered twice under two payment ids is guarded only by the ca_verified reconciliation. |
| AA71 | #21 | The section 17145(d) fund-of-funds exception is a review step, not an input; the taxable state refund is not cross-checked against Schedule 1. |
| AA65 | #22 | Prior-year unemployment repayments over $3,000 need their own worksheet. |
| AA67 | #23 | Boxes 2, 9, 10 and 15 refuse until they have routes; box 1 rents are AA73's. |
| AA66 | #24 | Gambling losses stay a manual worksheet; CA lottery winnings not on a W-2G use the manual Schedule CA path. |
| AA64 | #25 | The continuous-use December 2026 forms need re-reading at each season open for TY2027-2028. |
| AA52 | #26 | — |
| AA54 | #26 | — |
| AA76 | #26 | Regular method only; annualized, farmer and waiver cases need their own worksheet. |
| AA62 | #27 | A taxpayer who specifically identified lots prepares those lots and Form 8949 externally. |
| AA58 | #28 | — |
| AA57 | #29 | The cause of the dispatch-only period was never established. |
| AA72 | #30 | A disregarded single-member LLC owner's PTET credit refuses. |
| AA73 | #31 | Reviewed residential_rental_v1 scope only; vacation, commercial, royalty and disposition cases stay blocked. |
| AA74 | #32 | TY2025 only; the renter's credit six-month residency test is not checked. |
| AA43 | #33 | Reviewed TY2025 form year only: TY2026 mid-year runs refuse until that year is reviewed. |
| AA42 | #34 | The IRMAA dollar figure needs the premium-year table CMS publishes the November before, so a mid-year run normally reports MAGI only. |
| AA53 | #34 | — |
| AA34 | #207 | AA23 now supplies provisional QBI when re-pricing §68; legacy or unsupported inputs retain the held path and its caveats. |
| AA32 | #202 | an agent that types a household line can type its method too; a receipt binding would close it |
| AA26 | #201 | the scan call for a bounded command naming archive outside `archive/YYYY` has no test; the `git` and `rg` tests pin less than their names |
| AA10 | #200 | outside MEASURED COSTS the timing rule reads whole-line comments only, and catches no duration in words |
| AA33 | #199 | The permissions-hook absolute-timing rule reads whole-line comments only, as recorded under AA10; this is separate from X9's tax-figure prose lint. |
| AA24 | #198 | it prices the walk only when the walk ran past a second, and reads its timeout from `settings.json` |
| AA9 | #197 | it times the pytest pass, not the selector and scan around it |
| AA2 | #189 | the first green full matrix on `main` ran on #189's merge |
| AA15 | #188 | AA29 checks flagged household values and the line-58 floor even for a typed total; AA32 adds method markers, which remain unbound to a computation receipt. |
| Q7 | #156 | — |
| Q9 | #156 | — |
| Q8 | #158 | — |
| Q10 | #158 | — |
| hygiene | #160, #161, #162, #167 | Changes outside named backlog items; narrated in `docs/changelog/`. |

Epics I through U shipped earlier; see `docs/changelog/README.md` for the
per-epic index and `epic-R-U.md` for R1's audit baseline.
