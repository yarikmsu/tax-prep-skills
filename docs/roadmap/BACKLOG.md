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

**Engineering: the original V, W and X queue is complete.** The 2026-10-03
review of the retired design directory recovered useful, unimplemented extensions;
AA42-AA46 below now carry that scope instead of relying on historical plans.
The suite's size is stated nowhere: since AA8 the
MEASURED COSTS block in `.claude/hooks/skill-invariants.sh` counts only the
seven modules that hook selects, and a count restated here would be the mirror
this file was collapsed to remove.

**Product: blocked on one decision.** The repository is public, but Discussions
are off and there are zero non-owner users. Every distribution item has been
waiting on that since 2026-08-12.

**CI passes, but runs only when dispatched.** Minutes returned 2026-10-01, and
the first full matrix on `main`, at #189, passed: the 3.13 floor for the first
time, 3.14, Windows, lint, the PII scan and the Dependency audit once AA27 moved
urllib3. Everything merged on one local interpreter from #155 on is now covered
by it. Since the repository was recreated on 2026-10-10, though, CI runs only
when dispatched, and a dispatched run skips the PII range scan, so each merge is
scanned locally and the weekly dependency audit is dispatched by hand until
AA57 finds why.

---

## The plan, in order

| When | What | Why |
|---|---|---|
| **Owner decision; excluded from the current implementation queue** | **Y1** — enable Discussions on the now-public repository, or demote the distribution goal in writing. | The TY2025 window ended 2026-10-15; the next opens in January 2027. |
| **Now: California** | **AA75** — run line 32's exemption credit phase-out on the return. **AA68** — name every Form 540 situation the engine does not compute. **AA60** — route CA and NY 1099 state withholding. **AA69** — build Form 540 in the engine and check it. **AA70** — record CA estimated payments and next year's applied overpayment. **AA71** — compute the Schedule CA adjustments the persona meets. | The owner put California first on 2026-10-18, and that day's Form 540 audit found that the agent types most of the state return and nothing handles part of it. AA77 shipped first, ending an over-statement of an ISO exerciser's CA tax. AA75 is next. It is small, and it closes the one under-statement every high earner meets with the worksheet `planning.py` already holds. AA68 turns every remaining silent line into a named one. It is large, so if it threatens AA64, AA52 and AA58's January deadline, land its blocking rules first and its wording sweep after. AA60 comes next because a CA 1099 amount, a retiree's 1099-R among them, blocks the whole return today. AA69 puts the lines in a checked builder that AA70 and AA71 feed. AA70 fixes the balance due of anyone who paid CA estimates, and AA71 turns AA68's commonest refusals into computed lines. |
| **Next: every filer's intake and the auditor, before TY2026 filing opens** | **AA65** — record Form 1099-G's unemployment and family leave benefits. **AA67** — map Form 1099-MISC's boxes by the current form. **AA66** — give Form W-2G a parse route. **AA64** — read the TY2026 tip and overtime boxes on the W-2, 1099-NEC and 1099-MISC. **AA52** with **AA54** — the export's remaining differences from the engine, and the `--dest` a failed export leaves behind. **AA76** — price FTB 5805's underpayment penalty on line 113, landed with AA52 to share its auditor replay. **AA62** — ask about specific identification before FIFO prices a crypto sale. **AA58** — stop Form 2210 pricing a quarter the cache does not hold. **AA57** — find why CI's push, pull-request and scheduled runs never register. | A Californian files a federal return too, and these holes reach it. AA65 and AA66 close dropped-form holes open today: a 1099-G's unemployment compensation and a W-2G's winnings and federal withholding have no ledger category, so the gate cannot see them go missing. AA65 subtracts its amounts on Schedule CA through AA69, which lands before it, and blocks a CA return holding those amounts if it is taken out of order. AA67 errs the other way: a 1099-MISC's federal withholding, recorded as its row says, joins Schedule C gross receipts and over-states income and self-employment tax. AA64, AA52 and AA58 must land before TY2026 filing opens, and AA76 rides with AA52 because both edit the auditor's digest-pinned definition. The 2026 Form W-2 reports qualified overtime in box 12 code TT, where no `/tax-parse` row looks. Two of AA52's `set` defects make the freshness gate cry wolf on a correct refetch, so the season-open sitting would leave the auditor a table short through the TY2026 season. AA52 lands whole, with a fresh-session replay of its own as AA51 had on 2026-10-13, because its definition edit and the test change it forces cannot land apart. A return filed before the April 2027 rate publishes would price that quarter at the last cached rate. AA62 closes the last silent crypto gap. Until AA57 lands, scanning each merge to main for PII and auditing the locks weekly are steps someone has to remember. |
| **As each publishes** | **Y8** — fetch the 2027 §6621 quarters, the 2026 exchange rates and the TY2026 state tables through `set`, with the NY `table_bases`. | The quarters are the one item no gate catches: Form 2210 carries the last cached rate forward with only a note until AA58 lands, so this row is their reminder. The TY2026 Form 540 also re-keys AA69's line map. |
| **Then: California's remaining blocks** | **AA72** — claim the CA AMT credit and the PTET credit. **AA73** — compute rental real estate on Schedule E and Schedule CA. **AA74** — prepare a part-year California return (Form 540NR). | AA72 recovers a credit the rollover already carries and nothing claims. Above $200,000, AA73 is the largest block for every persona: 17% of California returns there carry rental or royalty income. It needs scoping before it is sized. AA74's block, 3.7% of California returns, is smaller than the marketplace block (Form 8962: 6.0% of all California returns, 1.3% above $200,000), which has no item. |
| **After** | **AA43**, then **AA42** with **AA53** — NY/NC estimated-payment schedules in the mid-year projection, then its AMT and IRMAA exposure. | Both are absent from the current engine. AA43 is the smaller and extends the CA schedule. AA42's IRMAA layer reuses the Roth premium computation, whose tier rule AA53 fixes, so that fix lands with it or before. |
| **Later** | **AA47**, **AA49**, **AA50** — the crypto follow-ups AA41 filed. **AA56** — tell the shipped index's two PR numberings apart. **AA61** and **AA63** — the TY2023-2024 Form 1099-B box review, and an advisory for local tax withheld that no return here claims. AA44-AA46 — prior-return import, multi-year Roth planning and expanded YoY reports. | AA47 turns a permanent block into a return. AA49 must be reproduced first, and AA50 is a performance change, so it needs a before-and-after benchmark. AA56 changes a test, not a return. AA61 and AA63 are P2: crypto on a 1099-B stopped with TY2024's forms, and a local amount no return claims still reaches Schedule A line 5a. AA44-AA46 are larger product extensions. |
| **Closed** | W7, W8, W9 — declined. Y3 — declined with a re-open trigger. | All three trade a working mechanism for tidiness. |

---

## Open

### AA — the 2026-09-20 product-owner review, and what later reviews filed under it

- **AA52 (P2, M): The export and the engine still answer four questions
  differently.** `audit_schema` adds `retirement_conformity_seed` when the cache
  holds that block, but the definition never names it, and the test that holds
  every exported table to a name builds its key set from
  `create_empty_tax_law(2026)`, which has no such block: it stays green over the
  exact gap it exists to catch. Build the set from a cache with every optional
  block present. `state_seed` takes its template from the code as written, the
  way `set` does through `declared_state_code`, while `validate_cache` grades
  the same block against `_state_pin_code`'s normalised one, so a block coded
  `ca`, or a legacy block carrying only `name`, is checked against two
  templates, and the export ships `state_seed: null` for the second. That wants
  one selection rule in the engine, not a second copy in the export. A
  whole-block `set state` keys its `_provenance` entry by the pin (`state.FL`)
  and clears only entries under that pin, so an earlier leaf entry keyed by
  path (`state.sales_tax.state_rate`) survives the rewrite. `check_freshness`
  dates the block by its oldest entry and item 9 by the deepest, so both date
  the refetched figure from the stale one: a gate crying wolf on correct data.
  And with no resolvable code `_pin_key` writes `state.state` where
  `_expected_pins` expects `state`, so the block reads UNPINNED however often
  it is refetched. The second and the last two are engine fixes, the last two
  predating Y2. The first is a definition edit plus the key-set change above,
  and the two cannot land apart: the test holds them to each other in both
  directions, so either alone turns it red. Land the whole entry with a
  fresh-session replay of its own, as AA51 did on 2026-10-13, and remove its
  line from the season-open runbook's §5 step 2, which holds it until then. A
  season-open sitting that comes first makes the definition half there, and the
  engine fixes stay here. Found by Y2's second review, which reproduced the two
  `set` defects.

- **AA53 (P3, S): The IRMAA top-tier rule trusts the cached list to be
  complete.** `_irmaa_tiers_reached` gives "at least" to whichever tier is last
  in the cache, not to the statutory top tier, so a partial list charges a MAGI
  exactly at its last threshold one tier early. That over-states the
  surcharge, and the crossing label prints `≥` where CMS prints "greater
  than". Nothing reaches it today: the TY2026 cache holds every tier for both
  statuses, and `main`'s uniform `>=` was wrong at every threshold but the top.
  Y2 also changed `magi_threshold` from the first dollar of a tier to the
  printed figure, and nothing detects a cache written under the old meaning,
  where a MAGI exactly at the stored figure now goes uncharged: an
  under-statement with no banner. The TY2026 thresholds are all whole thousands, so the
  printed figure was stored. Refuse a tier list short of the statutory count,
  and a threshold that is not a whole thousand once the rounding rule is
  confirmed in 42 U.S.C. §1395r(i). Found by Y2's second review.

- **AA54 (P3, XS): A failed export leaves a `--dest` it created behind.**
  `build_audit_copy` passes `made_here=dest is None`, reading "a `--dest` was
  named" as "the directory already existed", but `_resolve_dest` creates a
  named one that does not exist, parents included. A failed write then leaves
  it in place, empty, where `_write_export`'s docstring says the directory goes
  when this call made it. A re-run still accepts it. In the same tests, the
  stub that fails only the schema write is copied three times; one helper would
  serve all three. Found by Y2's second review.

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

- **AA57 (P2, S): Find why CI's push, pull-request and scheduled runs never register.**
  `ci.yml` declares `pull_request`, `push` to main and a weekly `schedule`, but
  since the repository was recreated on 2026-10-10 none has registered a run:
  every CI run on record, verified 2026-10-15, was dispatched by hand, the
  merges of #6, #7 and #8 have none, and Monday 2026-10-12 passed with no
  scheduled run. `scripts/ci-pii-range.sh` exits 3 for a dispatched run, so its
  PII job passes without scanning, and the commit that lands on main is scanned
  only if someone runs the push-event scan by hand. The weekly dependency audit,
  the one run that catches a new advisory against an unchanged lock, has
  stopped with it. Until the runs register, dispatch CI on the branch and bind
  the poll to its head commit; after each merge, straight after
  `git reset --hard origin/main` syncs main, run
  `RANGE=$(EVENT_NAME=push PUSH_BEFORE_SHA=$(git rev-parse ORIG_HEAD) bash scripts/ci-pii-range.sh) && bash scripts/pre-commit-pii-scan.sh "$RANGE"`.
  `ORIG_HEAD` is main's head before the sync, so a sync that brings in several
  merges scans them all, where `HEAD~1` would scan only the newest. Dispatch
  `gh workflow run ci.yml --ref main` weekly in place of the schedule. The
  cause has not been investigated. Landing it removes the interim sentences in
  this file's CI status paragraph, in `docs/contributing/ci-and-locks.md` and in
  `README.md`'s CI section. Found by the review that recorded Y2 as shipped.

- **AA58 (P2, S): Stop Form 2210 pricing a quarter the cache does not hold.**
  `compute_form_2210` in `agents/lib/tax_computations/federal/penalties.py`
  accrues the penalty through April 15 of the year after the tax year. When the
  cached §6621 rates stop before April 1 of that year, it prices the rest at the
  last cached rate, sets `penalty_computed`, and adds only a note. AGENTS.md §2
  lets a fallback degrade only when the output is advisory and under-states;
  line 38 is neither, and the carried rate over-states the penalty whenever the
  next quarter's rate falls. The engine already holds both alternatives: the
  unfetched case reports that a penalty applies without computing it, and
  `compute_underpayment_interest` (Epic M) never extrapolates a rate past the
  quarters the cache holds. Choose one, and test it with a timeline whose last
  change is January 1 of the filing year. Until it lands, Y8's fetch of the 2027
  quarters is the only guard. Found by the review that recorded Y2 as shipped.

- **AA60 (P1, S): Route CA and NY 1099 state withholding.**
  Only NC's 1099 state withholding has a ledger category. The CA and NY
  categories carry W-2 box 17: `/tax-compute`'s state reference takes CA
  withholding from box 17 (`references/state-return.md`, step 10), and
  `/tax-verify` names box 17 as IT-201 line 72's source. AA59 records a CA or
  NY 1099 amount as `unrouted_state_withholding`, which blocks the tie-out gate
  instead of losing the payment, so until this lands a run that computes CA's
  or NY's return, with that state's tax withheld on a 1099-R, a 1099-DA, a
  W-2G or any other 1099, cannot complete, its federal return included; a state whose
  return the run does not compute is only an advisory. NYC and Yonkers tax
  withheld on a 1099-R or W-2G (box 17) blocks the same way: IT-201 lines
  72-74 (IT-203 lines 62-64) take New York, NYC and Yonkers tax withheld from
  IT-2, IT-1099-R, Form 1099-G and Form W-2G. CA has a second gap with no row
  at all: Form 540 line 73 takes withholding on Forms 592-B and 593, which
  `/tax-parse` never names. Read Form 540's and IT-201's withholding-line instructions
  first, IT-203's lines 62-64 too, then decide whether each state's category
  widens to its 1099 boxes, which changes what `ca_withholding_box17` holds,
  or gains a field of its own. Give each `other_state_withholding` record the
  state it holds, too, and fail the gate when that state has its own
  withholding category and this run computes its return: today nothing checks
  the parser's choice, so a CA, NY or NC amount recorded there passes as an
  advisory and leaves that return short. A generic-estimate state's W-2 box 17
  sits there by design and must still pass.
  Found by AA59's design and its fourth review.

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

- **AA62 (P1, S): Ask about specific identification before FIFO prices a crypto sale.**
  The engine is FIFO-only and `crypto-accounts.md` puts specific
  identification outside it, but nothing asks. Under §1.6045-1(d)(2)(ii)(B)(2)
  a taxpayer can identify lots at a broker, by a standing order among other
  ways, and §1.1012-1(j)(3) then makes that identification the taxpayer's. A
  TY2025 form reports no basis for noncovered units, so the reconciliation
  shows no difference and FIFO's gain reaches the return in either direction.
  Box 8, which can carry such an identification, now refuses the import
  (AA59). Add a `tax-setup` question and a capability rule that blocks a
  specific identification at any broker, and let box 8 through once the
  taxpayer answers FIFO. Found by AA59's review.

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

- **AA64 (P1, S): Read the TY2026 tip and overtime boxes on the W-2, 1099-NEC and 1099-MISC.**
  The 2026 Form W-2 reports cash tips reported to the employer in box 12 code
  TP, qualified overtime compensation in code TT and employer Trump-account
  contributions in code TA, and adds box 14b for the Treasury tipped
  occupation code, box 14 becoming 14a (2026 Instructions for Forms W-2 and
  W-3, What's New). The December 2026 Form 1099-NEC renumbers box 1 as 1a,
  still the total of nonemployee compensation, with 1b (cash tips) and 1d
  (overtime compensation) amounts "included in box 1a" and 1c an occupation
  code; the December 2026 Form 1099-MISC adds 13a (cash tips) and 14 (overtime
  compensation), each "included in box 3", and 13b, a code (Instructions for
  Forms 1099-MISC and 1099-NEC, Rev. December 2026). Summing 1a with 1b or 1d
  counts them twice. `/tax-parse`'s `qualified_tips`
  and `qualified_overtime_premium` rows still send the agent to box 7, box 14
  or an employer statement, and its `self_employment_gross` row to 1099-NEC
  box 1, so a TY2026 parse can miss code TT or box 1d and leave the Schedule
  1-A overtime deduction at zero. Re-read those rows against the 2026
  instructions, deciding which box feeds which category and what each
  occupation code gates, before TY2026 filing opens. Found while fixing AA59's
  Schedule A recipe, widened by its fourth review.

- **AA65 (P1, S): Record Form 1099-G's unemployment and family leave benefits.**
  `/tax-parse` gives Form 1099-G's box 2 its own category
  (`state_refund_1099g`), and its box 4 and its state tax withheld go through
  the generic withholding rows, but no ledger category or tie-out target holds
  box 1, unemployment compensation, which Schedule 1 line 7 taxes. A 1099-G parsed and then dropped from the
  return therefore passes the gate: the dropped-1099 case the tie-out exists
  to catch, and the one a CP2000 notice follows. The December 2026 revision
  adds box 10, family leave benefits, and so renumbers the state boxes 10a, 10b
  and 11 as 11a, 11b and 12. Read both years' instructions for each box's
  line, add the categories and their targets, and say which box a state return
  reads too. California excludes unemployment and CA paid family leave. When
  the run computes CA's return, subtract them on Schedule CA through AA69's
  builder, which the plan lands first. If this lands before AA69, block a CA
  return that has these amounts, as AA68's rules do, since leaving them in
  over-states CA tax. Found by AA59's fourth review.

- **AA66 (P1, S): Give Form W-2G a parse route.**
  Gambling is a MANUAL capability rule, so a return with a W-2G computes, but
  `/tax-parse` has no W-2G extraction list and no category for box 1
  (winnings, Schedule 1 line 8b) or box 4 (federal income tax withheld, which
  Form 1040 line 25c takes from "other forms", not the 1099 line 25b). AA59
  routes its state and local boxes (15 and 17) only. A W-2G parsed and then
  dropped passes the gate with its winnings and its withholding gone, the
  first under-stating tax and the second over-stating the balance due. Add the
  extraction list, both categories and their targets. Found by AA59's fifth
  review.

- **AA67 (P1, S): Map Form 1099-MISC's boxes by the current form.**
  `/tax-parse`'s `self_employment_gross` row reads "1099-MISC boxes 3-7", the
  layout from before nonemployee compensation moved to Form 1099-NEC. On the
  Rev. April 2025 Form 1099-MISC, box 4 is federal income tax withheld, box 7
  is the direct-sales checkbox and box 3 is other income, which generally goes
  to Schedule 1 rather than Schedule C. Recorded as the row says, the
  withholding joins gross receipts, and the at-least tie then pushes Schedule
  C up to meet it, over-stating income and self-employment tax. Re-read each
  box against the year's instructions and give each its category: box 4 to
  `federal_withholding_1099`, box 3 to other income unless the taxpayer's
  trade or business earned it, boxes 5 and 6 to Schedule C. Found by AA59's
  sixth review.

### CA — what a California filer still cannot finish (the 2026-10-18 Form 540 audit)

The owner put California first on 2026-10-18. That day the 2025 Form 540 and
Schedule CA (540) were read line by line from ftb.ca.gov and each line was
mapped to the engine. Only three CA functions run on the return:
`compute_ca_itemized`, `compute_ca_schedule_p` for an ISO exercise, and
`compute_excess_sdi`. The agent types every other line from twelve prose steps
in `/tax-compute`'s `references/state-return.md`, or nothing handles the line
at all.

- **AA75 (P1, S): Run line 32's exemption credit phase-out on the return.**
  Line 32's credit phases out above a federal AGI threshold ($252,203 single
  on the 2025 form) by the AGI Limitation Worksheet. The return recipe never
  mentions the worksheet, so every return above the threshold over-states the
  credit and under-states CA tax. `planning.py`'s `_ca` already runs it for
  scenarios. Move the worksheet into a public function that the recipe calls,
  rather than blocking the persona the toolkit exists for. Lift the worksheet
  only, not `_ca`, which refuses any AGI above the Box C screening amount.
  The worksheet's inputs are an optional planning block today: the fetcher's
  validator skips `state.exemption_phaseout` for CA, as it skips
  `state.tax_table` and `state.amt_screening`, and `/tax-setup` fetches them
  only for planning. So make `exemption_phaseout` required for a CA return in
  setup's fetch, in the validator and in `tax-law-cache.md`, and never let an
  unfetched block keep the full credit. The worksheet counts heads as
  `personal_blind_senior_count`, so build the senior part of the count from
  `flags.age_65_plus_*`. AA69 then takes the worksheet into
  the builder. Split from AA68 by its third review, so that the one
  under-statement every high earner meets lands first.

- **AA68 (P1, L): Name every Form 540 situation the engine does not compute.**
  `capabilities.py` tells a California filer that Form 540 is "computed in
  depth (Schedule CA, exemption credits, MHST)", yet no CA gate rule exists,
  where NC has six. So a return that needs an uncomputed line finishes looking
  complete. The uncomputed lines, apart from line 32 (AA75), are:
  - Line 8 (blind) has no field: ask it in `/tax-setup`, and block a yes until
    AA69 prices it. Line 9 (senior) is already answered by
    `flags.age_65_plus_primary` and `flags.age_65_plus_spouse`, which drive
    the federal §63(f) and Schedule 1-A senior amounts. Read those flags and
    never ask a second question.
  - Schedule CA Part I beyond the HSA add-backs: US-obligation interest and
    fund dividends, non-CA municipal interest and exempt-interest dividends,
    Social Security, a state refund, unemployment and CA paid family leave,
    §1202, CA and federal basis and depreciation differences, K-1 CA amounts,
    moving expenses, IRA basis, California lottery winnings (a subtraction,
    which matters more once AA66 parses the W-2G), the educator expense
    add-back, and the foreign earned income exclusion add-back (Schedule CA
    line 8d).
  - Line 61 and Schedule P (540) outside an ISO exercise. Two of the
    booklet's boxes send a return to Schedule P:
    - Box A: AMT preference and adjustment items, with its carve-out for the
      trade or business items of a filer whose gross receipts from all trades
      or businesses are under $1,000,000.
    - Box B: investment interest expense, and income from installment sales
      of certain property. Its ISO income is `iso_exercise`'s.

    Ask about those items, carve-out included, and block a yes, since a
    missing AMT under-states CA tax. Line 61's own test is broader than
    the boxes. Schedule P Part I adjusts the deduction the return already
    took, so it can produce CA AMT for a filer who answers no to every box,
    such as an investor with large advisory fees. Enumerate Part I from the
    year's form. On the 2025 form it covers:
    - line 1: the standard deduction of a filer who did not itemize
    - line 2: part of the medical deduction
    - line 3: property taxes
    - line 4: mortgage interest on debt not used to buy or improve the home
    - line 5: miscellaneous itemized deductions
    - line 6: refunds of property taxes
    - line 18: the §17077 phase-out, subtracted back, since "for AMT, this
      limitation does not apply"

    These are return figures, so they are AA69's to compute. Line 4 is the
    exception. `compute_ca_itemized` takes one mortgage interest amount and
    does not record what the loan paid for, so line 4 needs a question. Box C (CA AGI above $347,808 single on
    the 2025 form) is a screen that sends a filer claiming special credits to
    Schedule P. It limits neither credit AA72 adds (see AA72), and it is a
    figure computed from the return, not an answer the gate can hold.
  - Line 34: the Schedule G-1 tax on a lump-sum distribution and the FTB 5870A
    tax on an accumulation distribution.
  - Line 63's FTB 3805P and other taxes.
  - Lines 40, 46 and 75-77: the dependent care credit (FTB 3506), the
    renter's credit, CalEITC, the young child credit and the foster youth
    credit. Each turns on an income figure from the return: federal AGI of
    $100,000 or less for line 40, CA AGI of $53,994 or $107,988 for the
    renter's credit, and earned income for lines 75-77 (2025 booklet). These
    are credits, so they follow the credit precedent below as manual notes,
    on existing rules where one fits:
    - Lines 75-77 join `earned_income_credit`, keyed to `may_qualify_for_eic`,
      which already covers NY's IT-215 and IT-209 and the NYC EIC. Its
      `/tax-setup` question screens on the federal shape, so give it a
      California carve-out, as it has one for IT-209. CalEITC needs no child,
      the foster youth credit covers ages 18-25, the young child credit
      allows zero earned income, and CA takes ITIN filers, so a federal "no"
      does not rule them out.
    - FTB 3506 joins the `dependent_care` rule, keyed to `has_dependent_care`.
      That note says the federal credit IS computed, so add CA text saying
      FTB 3506 is not, the way the `ny_plan` override rewrites the EIC note.
    - The renter's credit gets a note keyed to rent paid in California.
  - Lines 43-45's credits, the CA AMT credit and the PTET credit (FTB 3804-CR)
    among them, until AA72:
    - The PTET credit (FTB 3804-CR, code 242) blocks. It is not a worksheet
      credit: it is 9.3% of the owner's share of the electing entity's
      qualified net income, a figure the entity reports with the K-1, and
      that is why it blocks rather than taking a note. It is a
      nonrefundable special credit on lines 43-45, not a payment. Net tax
      limits it, and its excess carries over five years (R&TC §17052.10), so
      it must never be added to the payments. Key it to a CA entry in
      `flags.k1_states` and a question about the entity's election, and also
      to a question about an unused FTB 3804-CR carryover from an earlier
      year. A carryover blocks as `nc_other_credits` blocks NC carryovers,
      even in a year with no election.
    - The CA AMT credit blocks when the taxpayer has one, because the
      toolkit already carries the figure. A rule keys only on a boolean
      under `flags.*`, so add a flag. The gate's `derived` dict sets it from
      a nonzero `prior_year.ca_amt_credit_carryforward`, and `/tax-setup`
      asks it, which also catches a credit from a return prepared
      elsewhere. Acknowledging the block must not forfeit the credit. Step
      9.5 is skipped without an ISO exercise and, with one, writes only this
      year's figure, and the rollover skips a missing source path, so the
      next profile would carry 0. The gate writes no result fields, so the
      fix lives where the PFIC precedent puts it, a compute step paired with
      a tie-out check:
      - `state-return.md` step 9.5 writes the `schedule_p` block whenever
        the flag is set, ISO exercise or not.
      - Its `carryforward_to_next_year` is the carried credit, less the code
        188 credit allowable this year as the filer works it on FTB 3510,
        plus this year's ISO figure. That FTB 3510 amount is a required
        answer whenever the flag is set. Null refuses, and 0 is accepted
        only as the result of a worked FTB 3510, never as a default.
      - The acknowledgement note sends the filer to FTB 3510 to claim this
        year's allowable credit. Under IRC §53(b), which California follows,
        the carryover falls by the credit ALLOWABLE, not by what was
        claimed, so an unclaimed allowable credit needs an amended return,
        not a larger carryforward.
      - The tie-out fails when the flag is acknowledged and either no
        `schedule_p` block carries the figure or the FTB 3510 amount is
        unanswered.
  - Lines 72 and 73: estimated payments (those an estate, trust or S
    corporation paid on the taxpayer's behalf on Schedule K-1 (541) or (100S)
    included), an extension payment, last year's applied overpayment, and Form
    592-B and 593 withholding. Nothing records them, and each over-states the
    balance due, so a yes blocks until AA60 and AA70.
  - Line 98, the overpayment applied to next year. AA70 owns its field and
    its question. Leaving it out over-states the refund on line 115, so until
    AA70 lands, ask whether any CA overpayment goes to next year and block a
    yes.
  - Line 91's use tax and line 92's coverage penalty (FTB 3853).
  - Line 113's FTB 5805 penalty, a manual note until AA76. The booklet lets a
    filer leave it out: "The FTB can figure the penalty for you when you file
    your tax return and send you a bill." Its screens are computed figures,
    not answers:
    - line 100 under $500 ($250 if married/RDP filing separately), and
    - line 100 under 10% of line 48, excluding the tax on lump-sum
      distributions on line 34.

    So the capability gate, which reads only the profile before any data
    loads (`/tax-compute` Step 0.7), cannot run them. AA69's builder and
    `--check` emit the screened note. Until AA69 lands, every CA return
    carries the note unscreened. The federal engine likewise reports an
    unpriced Form 2210 penalty as a note (AA58).

  Pick each severity as NC's rules do, and as AGENTS.md §4's "Never silently
  out-of-scope" requires: a situation the engine cannot compute blocks,
  whatever its direction. Both directions have NC precedent:
  - `nc_business_adjustments` blocks an omitted add-back, which under-states
    NC tax.
  - `nc_recovery_deductions`, `nc_other_deductions` and `nc_other_credits`
    block omissions that "OVER-state NC tax".

  §4's "degrades with a loud banner" covers a fallback inside a computation,
  not a whole situation the engine leaves out. On the over-statement side,
  the skill catalog adds: "Over-stating what the taxpayer must pay is the
  hard error". So:
  - A situation the engine does not compute blocks whichever way it errs.
  - Each rule is keyed to a yes answer, so it stops only the returns it
    applies to.
  - Acknowledging the rule lets the return finish with that line NOT
    COMPUTED.
  - Two exceptions are manual notes:
    - A line the FTB lets the filer leave blank, which is line 113.
    - A credit the taxpayer claims from a worksheet of their own. It has no
      source document for the tie-out to miss, and its omission over-states
      the tax, which `capabilities.py` files under §4's
      degrade-with-a-banner. `earned_income_credit` is MANUAL on that
      ground, NY's state and City EICs included. `dependent_care` is MANUAL
      for a different reason: the federal credit is computed.
    - A credit the toolkit already carries is no exception and blocks, as
      `nc_other_credits` blocks NC credit carryovers.

  Ask each in `/tax-setup`, and reuse a flag the profile already has, such as
  `flags.has_1099g_state_refund`, rather than asking twice. Two
  subtractions are already visible on the federal return, and CA subtracts
  only what federal AGI included:
  - Social Security at Form 1040 line 6b (`line_6b_taxable_ss`), not the
    ledger's box 5.
  - A state refund at the part federally taxable under the tax benefit rule.
    It is taxable only if the prior year itemized (`tie_out.py`), and it has
    no field yet.

  Key those rules to those amounts in the tie-out: a nonzero taxable amount
  with no CA subtraction fails, whatever the answer was. AA59's option check
  is not the model here, because it fires only on an unanswered flag.

  A blocking rule costs more than its code. `WHO-IT-FITS.md` must size it,
  measured or stated as unmeasured as NC's and WA's are, and the skill
  catalog must name it. `test_who_it_fits_sizes_exactly_the_blocking_rules`,
  `test_the_skill_catalog_names_every_blocking_rule` and
  `test_who_it_fits_comparisons_still_hold` hold both, and the page's
  "California blocks the least of the five" must be re-checked. The script
  compares measured SOI fields only, so a block recorded as unmeasured can
  never turn that comparison red. Measure a new block wherever public data
  allows. Non-CA municipal interest is the likeliest large one, and the SOI
  files' tax-exempt interest field bounds it from above, since it counts CA
  municipal interest too. Re-check AA73's "largest block above $200,000"
  claim against what is measured. Use tax could stop a large share of
  returns, so weigh a required answer that prices it.

  Scope the rules with a `state` field on the rule, read in one place. Today
  the state is read from the rule id: `startswith("nc_")` and `("wa_")` appear
  in the applicability loop, again in the migration guard, and in
  `test_capabilities.py`. A third prefix copy that misses one site applies
  CA rules to other filers or skips the guard.

  Replace the "in depth" claim wherever it describes California's return.
  `git grep -n -i "in.depth"` finds both spellings (AGENTS.md §4 writes
  "In-depth"), but not other wordings of the claim, such as
  `WHO-IT-FITS.md`'s "the deepest the toolkit computes". Work from its output,
  plus a read of the California paragraphs, rather than a list here: on
  2026-10-18 it found more than twenty such sites. They include `README.md`,
  `capabilities.py`'s state note, projection text a taxpayer reads, the
  issue templates, two eval goldens that pin the note word for word, and the
  tests that assert it.

- **AA69 (P1, M): Build Form 540 in the engine and check it.**
  Nothing derives Form 540's arithmetic. NC's D-400 comes from one
  `compute_nc_d400` call and NY settles through `compute_ny_settlement`. CA's
  lines 17-115 are typed, and the tie-out checks only box 17 withholding. Line
  32's phase-out (AA75) and lines 8 and 9 are already wrong that way:
  blindness has no field, and nothing reads the senior flags the profile
  already holds.

  Add `compute_ca_540`. It takes the Schedule CA totals, the deduction, the
  credits and the payments, and returns every line. Line 31 comes from the
  published Tax Table at or below its ceiling. `state.tax_table` is an
  optional planning block today, so make it required, as AA75 does the
  phase-out. Run `compute_ca_schedule_p` on every return, not only after an
  ISO exercise, with every Schedule P Part I adjustment AA68 lists, in both
  directions, as `ca_preference_adjustments`. Line 61 then catches the AMT
  that AA68's box questions cannot. Two inputs follow from that:
  - `state.amt` is fetched today only for an ISO exerciser, and
    `compute_ca_schedule_p` refuses it unfetched. Make it required for every
    CA return in setup's fetch condition, the validator and
    `tax-law-cache.md`, and never skip Schedule P when it is missing.
  - Schedule P line 25 reads Form 540 line 31, the tax before credits
    (AA77). Emit line 113's screened FTB 5805 note from the builder's own
  lines 100 and 48 (AA68). Its $500 ($250 for a separate filer) amount is a
  threshold, so fetch it into the cache like any other (AGENTS.md §2). The
  lines are the bracket tax, the
  phase-out, the floors and line 62's tax (renamed Behavioral Health Services
  Tax on the 2025 form). Add a `--check` like `/tax-compute` step 12.6, carried
  by the validation receipt. Key the line map to the reviewed form year, as
  `planning.py`'s `_REVIEWED_YEARS` does, so TY2026 re-reads the form.
  Correct the excess-SDI label: 2025 line 74 is the Motion Picture credit.

- **AA70 (P1, M): Record California estimated payments and next year's applied overpayment.**
  Form 540 line 72 takes the year's estimated payments (those paid on the
  taxpayer's behalf on Schedule K-1 (541) or (100S) included), an extension
  payment and last year's overpayment applied, and the return has no field for any of
  them:
  - `taxpayer_profile_template.json` has an `estimated_payments.state` list,
    and nothing reads it. State estimated payments reach only the federal
    Schedule A line 5a recipe.
  - `filing_package.py` prices the CA balance as box 17 plus excess SDI.
  - `/tax-extend` subtracts "CA estimates paid" with nothing to read.

  An RSU holder who paid CA estimates gets a balance due over-stated by every
  payment. Line 98, the overpayment applied to next year, has no field either
  (AA68 blocks a yes until this lands), so `/tax-rollover` cannot carry it the
  way it carries the federal line 36 into
  `prior_year.estimated_payments_applied`. To fix both:
  - Record line 72's payments by wiring up or replacing the unread
    `estimated_payments.state` list, which already holds dated payments as
    FTB 5805 needs, rather than adding a second field beside it.
  - Add line 98's field, the intake questions and the rollover.

  FTB 5805's penalty on line 113 is AA76. Needs AA69.

- **AA76 (P1, M): Price FTB 5805's underpayment penalty on line 113.**
  Line 113 is a new computation. The projection computes only the required
  annual payment (`compute_ca_safe_harbor`), so the penalty must be priced per
  installment by the payment dates AA70 records. That is not
  `compute_form_2210` with another rate, because CA departs from the federal
  structure in the ways `/tax-safe-harbor`'s `references/california.md` lists.
  The installments are weighted 30/40/0/30 (R&TC §19136.1), not four equal
  quarters, withholding included.
  The rate is not a copy of the §6621 quarters either. R&TC §19521 resets it
  every January 1 and July 1 from the federal rate of an earlier month, and
  the penalty accrues to April 15 of the next year. So the cached schedule
  holds a rate dated after the tax year, and the freshness auditor rejects
  one today:
  - Its item 2 names `federal.underpayment_rates` among "the only" fields
    with their own effective periods.
  - `check_freshness`, and `set` through `_check_applies_to`, flag a
    provenance entry for another period unless its pin is in
    `_MULTI_PERIOD_PINS`.

  Both need the CA schedule added, and the fix must not touch `state.CA`.
  `_pin_key` maps every `state.*` path to the single pin `state.CA`, so
  listing that pin in `_MULTI_PERIOD_PINS` would switch off the tax-year
  period check for every CA figure: the brackets, the standard deduction, the
  exemption amounts, and AA75's and AA69's blocks. That is the stale-figure
  failure AGENTS.md §2 exists to stop. Instead:
  - Give the schedule a sub-pin of its own, the way
    `state.FL.county_surtax_rate` has one, in `_pin_key`, in
    `_expected_pins`, and in the runbook's registration of a new block.
  - List only that sub-pin in `_MULTI_PERIOD_PINS`.
  - Teach `_check_applies_to` a half-year period. Today it accepts only a
    calendar year or a quarter.

  The auditor's definition is digest-pinned, and editing it needs a
  fresh-session acceptance replay, as AA52 does. Land this with AA52 and
  share its replay rather than running a second one. If the season-open
  sitting comes first and makes AA52's definition edit in its own replay,
  queue this item's item-2 edit in that sitting's list in the season-open
  runbook (§5 step 2), which is how the runbook takes a pending edit. Never
  edit the definition ahead of a replay.
  Needs AA70. Split from AA70 by its fourth review.

- **AA71 (P1, M): Compute the Schedule CA adjustments the persona meets.**
  Compute the common adjustments AA68 names instead of refusing them. Each
  needs its parse field, ledger category and line:
  - US-obligation interest (1099-INT box 3) is a subtraction. Today `/tax-parse` merges box 3 into
    `taxable_interest`, which loses the figure.
  - Non-CA municipal interest and exempt-interest dividends are additions:
    1099-INT box 8 by issuer state, and 1099-DIV box 12, which `/tax-parse`
    does not capture.
  - Fund dividends follow CA's 50% rule (R&TC §17145; Schedule CA line 2's
    instructions; FTB Pub. 1001). Only a fund holding at least 50% of its
    assets in US obligations passes a US-obligation share through as a
    subtraction, or, for an exempt-interest dividend, in US and CA
    obligations. Below that nothing is subtracted, and a federally exempt
    dividend is an addition in full. Subtracting a share from a fund below 50%
    under-states CA tax.
  - Social Security is subtracted at its federally taxable amount (line 6b),
    and a state income tax refund at the part federally included, which
    needs a field.
  - Unemployment and CA paid family leave are AA65's, not this item's. AA65
    adds their 1099-G categories after AA69's builder exists, and subtracts
    them on Schedule CA itself.

  Needs AA69.

- **AA72 (P2, M): Claim the California AMT credit and the PTET credit.**
  AA68 leaves both credits blocked, and leaves the CA AMT credit's
  carryforward written by step 9.5 from a filer-entered FTB 3510 amount.
  Start from that state. Lines 43-45 need the two credits, their
  carryforwards and their limits:
  - The prior-year AMT credit, code 188, which FTB 3510 computes, limits and
    carries over before Schedule P (540) Part III takes its result. Compute
    the allowable credit on FTB 3510, not on Part III. That computed figure
    replaces AA68's entered field, never sits beside it. The carryforward is
    the prior credit, less the credit ALLOWABLE (IRC §53(b)), plus this
    year's deferral AMT. Correct `rollover.py`'s comment, which says the
    figure already includes the unused prior credit.
  - FTB 3804-CR's credit for an owner of an electing CA entity, with its
    five-year carryover (R&TC §17052.10). NY has `compute_ny_ptet_owner`;
    CA has nothing. The TY2025 booklet limits the election to years before
    2026, so confirm the current statute for TY2026 rather than relying on
    the 2025 text.
  - The limits, from Schedule P (540)'s 2025 instructions:
    - Code 242 sits in Section B4, among credits that "can reduce regular
      tax below the TMT", as the FTB 3804-CR instructions also say. Only net
      tax limits it, after the Other State Tax Credit (B3). Capping it at
      regular tax less TMT would over-state CA tax and invent a carryover.
    - Code 188 sits in Section A2, so FTB 3510 and the excess of Form 540
      line 35 (Schedule P Part III line 1) over TMT limit it, after Section
      A1's credits, whatever Box C says. That is line 35, after the
      exemption credits, not the line 31 that Schedule P line 25 reads
      (AA77) and `compute_ca_schedule_p` returns as `ca_regular_tax`. Taking
      that field minus TMT would allow up to the whole exemption credit too
      much.
    - Box C ($347,808 single on the 2025 form) only screens a filer into
      Schedule P. It limits neither credit, so neither needs
      `state.amt_screening`.

  Needs AA69.

- **AA73 (P2, L): Compute rental real estate on Schedule E and Schedule CA.**
  `rental_income` blocks 17% of California returns above $200,000 and 7.1% of
  all of them. The figure counts rental or royalty income (`WHO-IT-FITS.md`).
  Above $200,000 it is the largest block for every persona. Across all
  returns, the marketplace block is larger in Florida and North Carolina,
  while in California rental stays larger (7.1% against 6.0%). The work
  spans:
  - Federal Schedule E and depreciation (Form 4562).
  - The passive-loss limit (Form 8582's $25,000 allowance and its phase-out)
    and the suspended-loss carryforward.
  - California's own depreciation and passive figures (FTB 3885A, FTB 3801).

  Scope it first. A long-term residential rental with no short-term or
  self-rental may cover most of the block, while every case outside the scope
  stays blocked.

- **AA74 (P2, L): Prepare a part-year California return (Form 540NR).**
  `part_year_residency` blocks 3.7% of California returns, a figure that covers
  every income. NY has IT-203 for
  TY2025 behind a reviewed filing plan; CA has nothing. Build Form 540NR, with
  Schedule CA (540NR)'s residency columns and the CA-to-total AGI ratio, on
  IT-203's pattern: a reviewed allocation plan, and a refusal without one.

### Recovered design follow-ups — reviewed against the engine on 2026-10-03

These are extensions, not unfinished portions of the shipped v1 deliveries.
The historical specs supply their origin; the scope stated here governs the
work. The split capital-loss carryforward, Form 1040 line 36 rollover, IRA and
AMT basis rollover, NIIT projection, full-status capital-gains cache and
CA/NY/NC withholding true-ups already exist and are not reopened.

- **AA42 (P2, L): Add AMT and IRMAA exposure to the mid-year projection.**
  NIIT projection is already implemented. The federal projection total still
  omits AMT, and the report has no IRMAA layer. Reuse the existing AMT and Roth
  premium computations with explicit projected inputs and covered-year fetched
  law, including a named boundary for unsupported AMT preference items. Report
  IRMAA as a per-beneficiary premium estimate with its lookback, separate from
  income tax. Never reuse current law as a verified future-year figure.
  Origin: [2026-05-25-tax-project-design.md](https://github.com/yarikmsu/tax-prep-skills/blob/017d2dd45ead/docs/superpowers/specs/2026-05-25-tax-project-design.md) §Open follow-ups.

- **AA43 (P2, M): Extend projection estimated-payment schedules beyond CA.**
  CA weighted projection vouchers and CA/NY/NC computed-return true-ups already
  exist. Projection currently models only CA's state voucher weighting. Add
  bounded full-year NY/NC projection schedules using their fetched safe-harbor
  inputs and existing deadline helpers, with state/local liability and
  withholding on the same footing. Incomplete City/Yonkers or allocation inputs
  retain a refusal instead of producing a combined payment or refund estimate.
  Origin: [2026-05-25-tax-project-design.md](https://github.com/yarikmsu/tax-prep-skills/blob/017d2dd45ead/docs/superpowers/specs/2026-05-25-tax-project-design.md) §Open follow-ups.

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
