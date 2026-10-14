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
Unimplemented extensions are AA41-AA46 below. Maintained state contracts and
the template moved to `docs/contributing/state-module-{ny,nc,wa,template}.md`,
indexed in AGENTS.md §9. Origins below and historical changelog citations link
to the [pre-migration Git snapshot](https://github.com/yarikmsu/tax-prep-skills/tree/017d2dd45ead/docs/superpowers).
Old plans remain available there, including their contemporary roadmap
citations; they no longer direct contributor work in the current checkout.

---

## Status

**Engineering: the original V, W and X queue is complete.** The 2026-10-03
review of the retired design directory recovered useful, unimplemented extensions;
AA41-AA46 below now carry that scope instead of relying on historical plans.
The suite's size is stated nowhere: since AA8 the
MEASURED COSTS block in `.claude/hooks/skill-invariants.sh` counts only the
seven modules that hook selects, and a count restated here would be the mirror
this file was collapsed to remove.

**Product: blocked on one decision.** The repository is private, Discussions are
off, and there are zero non-owner users. Every distribution item has been
waiting on that since 2026-08-12.

**CI is back and green.** Minutes returned 2026-10-01, and the first full
matrix on `main`, at #189, passed: the 3.13 floor for the first time, 3.14,
Windows, lint, the PII scan and the Dependency audit once AA27 moved urllib3.
Everything merged on one local interpreter from #155 on is now covered by it.

---

## The plan, in order

| When | What | Why |
|---|---|---|
| **Owner decision; excluded from the current implementation queue** | **Y1** — publish and enable Discussions, or demote the distribution goal in writing. | Oct 15 is the last TY2025 window; the next is January. |
| **Oct-Nov** | **Y2** — run the staged fetch → auditor edit (including **AA19**) → fresh-session replay chain. | Begins when the year's figures publish. |
| **Next, in entry order** | AA41-AA43 — bounded carryforward, intake, scenario and projection extensions recovered from the historical specs. | These remain absent in the current engine; old follow-ups that already shipped are excluded. |
| **Later** | AA44-AA46 — prior-return import, multi-year Roth planning and expanded YoY reports. | Larger product extensions; the existing single-year engines remain their starting point. |
| **Closed** | W7, W8, W9 — declined. Y3 — declined with a re-open trigger. | All three trade a working mechanism for tidiness. |

---

## Open

### AA — the 2026-09-20 product-owner review, and what later reviews filed under it

- **AA19 (P2, S): The auditor's SALT-cap check still awaits Y2.** The engine
  validation shipped in #216 under Q14: Schedule A and PTET refuse a cached
  amount that differs from `salt.base_cap`, accepting its exact amount at cents
  or its nearest whole dollar (half up), before the per-return phasedown.
  AGENTS.md §2 now requires agreement rather than unconditional cache precedence.
  The remaining auditor edit rides Y2's chain, since the definition is
  digest-pinned to a passed acceptance. Its check 5 still says `federal.salt_cap`
  "indexes roughly 1%/yr" and that an absent value "degrades quietly", and never
  checks for a stale or already-reduced amount. Found by AA16's second review.

- **AA51 (P2, M): Four auditor rules still restate what `check_freshness` does in
  code.** Y2 moved the schema into the export's `schema.json`; these are the rules the
  definition still describes in prose, and each disagrees with the engine on some cache.
  Item 9 dates a figure by its own `_provenance` entry, its block's pin or
  `_meta.fetched`, but `check_freshness` also counts an entry keyed by any path ABOVE
  the figure, which `set` writes for a subtree, so a cache written as documented reads
  UNVERIFIED for age and STOPs. Item 8 looks `state.code` up as written where the engine
  normalises it through `_state_pin_code`, so `fl` typed `none` is a false finding and a
  `ca` with no type is missed; publish the normalised code in `schema.json`, the table
  rather than the rule. Item 9's `applies_to` exemption reads as covering item 2's CA
  surcharge, which `_MULTI_PERIOD_PINS` does not, though only a hand edit reaches it
  since `set` refuses the entry. And the scope sentence bars a PDF the agent's own fetch
  saved outside the directory, so a figure whose only year-named source is one reads
  COULD NOT VERIFY: it fails closed, and the 2026-10-12 replay hit it. Each is a
  definition edit, so all four ride the next fresh-session replay. Found by Y2's review.

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
  it is refetched. The last two predate Y2 and are engine fixes; the first is a
  definition edit and rides the next fresh-session replay. Found by Y2's second
  review, which reproduced the two `set` defects.

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
  wash sale, loss not allowed) refuses the WHOLE import, though the doc says to
  report that one transaction manually and no input can mark it so; and a sale
  the import dates January 1 that the broker dates December 31 in UTC fails
  both years, because a date a day off matches only within one year. Each
  needs its own evidenced treatment, not a tolerance. Until then the import
  blocks instead of guessing. Found by AA41's review.

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
  year, not that the file exists. The 1099-DA re-check this entry used to carry
  is AA55, which is not blocked.

- **AA55 (P1, S): Re-read the TY2026 Form 1099-DA boxes before AA48 lands.**
  AA41 maps each 1099-DA box it reads or refuses to an import key by the box's
  TY2025 meaning, and `crypto-accounts.md` names every one. The
  [TY2026 Form 1099-DA instructions](https://www.irs.gov/pub/irs-prior/i1099da--2026.pdf)
  are published, so confirm now that each box still means the same, rather than
  waiting for the Form 8949 instructions to arrive close to filing season. If a
  box moved, the map becomes per-year like `_REPORTING_REGIMES`, because TY2025
  imports still read the old one. Split from AA48 by its review.

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

### Recovered design follow-ups — reviewed against the engine on 2026-10-03

These are extensions, not unfinished portions of the shipped v1 deliveries.
The historical specs supply their origin; the scope stated here governs the
work. The split capital-loss carryforward, Form 1040 line 36 rollover, IRA and
AMT basis rollover, NIIT projection, full-status capital-gains cache and
CA/NY/NC withholding true-ups already exist and are not reopened.

- **AA41 (P2, M): Reconcile crypto imports with Form 1099-DA.**
  The importer and skill still disclose that broker forms are not reconciled
  and default Form 8949 rows to Box C/F. Compare broker proceeds and reported
  basis with matched disposals, select the covered year's appropriate reporting
  treatment from explicit evidence, and surface unresolved differences before
  the crypto result feeds the return. Keep adjustments and their source evidence
  in the ledger rather than silently replacing the FIFO result.
  Origin: [2026-05-31-crypto-importer-design.md](https://github.com/yarikmsu/tax-prep-skills/blob/017d2dd45ead/docs/superpowers/specs/2026-05-31-crypto-importer-design.md) §Out of scope.

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

- **Y1 (P1, XS): Publish the repository and enable Discussions.**
  Every artifact the publish gate asked for is in place: R1's full-history audit
  (one adjudicated-benign finding, baseline recorded in
  `docs/changelog/epic-R-U.md`), R3's intake surface, R5's QUICKSTART with
  verified output, R2's standing controls, the pre-push PII scan, hashed locks,
  Apache-2.0. There is no remaining engineering precondition. Discussions are
  what R6's pinned thread needs, and that thread is the ONLY signal the design
  permits — the tool is local-first, so filing is structurally unobservable and
  the stated success criterion ("a stranger files a return with it") cannot be
  evaluated without it.

- **Y2 (P1, S): Season-open runbook when the TY2026 figures publish.**
  **PRE-STAGED 2026-10-02:** the chain is one ordered paragraph in
  `docs/ops/season-open-runbook.md` §5, with the two auditor edits and the
  AA14 `table_bases` check written into it. What remains is the run itself.
  Ruled at Q5: **write the procedure, touch nothing.** Both reviewer definitions
  and `docs/ops/review-subagent-acceptance.md` stay exactly as they are,
  certified with their digest pins matching, and the runbook carries one ordered
  procedure. What gates Y2 is the figures publishing and then the owner's fetch
  and FRESH-SESSION replay — neither a decision. Follow the staged chain in §5:
  fetch and gate → make the queued auditor edits → start a FRESH session →
  replay both reviewers → re-record verdicts and digests. The edits correct
  the stale `federal.capital_gains_rates` keying note from before #89 and
  AA19's `salt_cap` check. The definition is digest-pinned to a PASSED
  acceptance, so editing it without the replay silently withdraws an
  acceptance this repository advertises.
  One more check rides the runbook: when the TY2026 State and City tables
  publish, compare them with their schedules and fetch `tax_table.table_bases`
  (AA14). A cache without the key gets a note, not a refusal, and the row can
  then be a dollar off in either direction.

---

## Blocked on an owner ACTION or an outside publication, not a ruling

| Item | What it waits on |
|---|---|
| **Y1** | The owner publishing the repository and enabling Discussions. Verified still private, Discussions off, 0 stars. |
| **Y2** | TY2026 figures publishing, then the staged fetch → auditor edits → FRESH-session replay → acceptance-record chain. It is also the only route to the auditor's stale keying note. |
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
  trigger.** The repository is not public, so there is no consumer to install a
  distribution — the largest contract change in the backlog against zero demand
  signal, and it defeats the venv-refusal guard by design. **Re-open when a second
  consumer of the engine exists outside a clone.**

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
`docs/changelog/README.md`.

| Item | PRs | Remaining scope / follow-ups |
|---|---|---|
| AA23 | #203, #204, #205, #206, #217 | — |
| V0 | #85 | — |
| V1 | #109, #110, #111 | — |
| V2 | #108 | — |
| V3 | #97, #98, #107, #116, #117, #141 | — |
| V4 | #87 | the auditor's `_provenance` check waits for the Y2 replay |
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
| X1 | #89 | the auditor's keying note is WRONG, not pending; only the Y2 replay can correct it |
| X2 | #86 | — |
| X3 | #88 | — |
| X4 | #110, #142 | — |
| X5 | #143, #144, #155 | Wrapped and paired limit detection completed under X9. |
| X6 | #154 | The statutory SALT step schedule remains the fallback; #216 requires cached amounts to agree at cents or the nearest whole dollar (half up), before phasedown. |
| X7 | #218 | future-sale tax and chained wash-sale repricing remain outside the replacement basis plan |
| X8 | #159 | Engine entry-point status validation: direct checks or declared delegation probed at both ends |
| X9 | #228 | Wrapped and paired amounts, Markdown block boundaries, code/CLI handling and shared figure gates. |
| X10 | #224 | Roth catch-up split, confirmed HSA eligibility and fetched per-account state conformity. |
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
| AA14 | #189 | a cache without `table_bases` gets a note, and the row can be a dollar off either way; the TY2026 check rides Y2 |
| AA27 | #189 | — |
| AA28 | #190 | the TY2026 `table_bases` check rides Y2 |
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
