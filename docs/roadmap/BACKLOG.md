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
| `2026-09-05-staff-agent-review.md` | Open items → below. Epic Z narrative → `docs/changelog/epic-Z.md` (new). W2 and W3 slice 3 → `epic-W.md`. Y7 → `epic-Y.md`. The review itself → git history. |
| `2026-08-12-product-owner-review.md` | Shipped Epics R-U → `docs/changelog/epic-R-U.md` (new), including **R1's audit baseline**. Distribution analysis → below. |
| `2026-state-support-ny-fl-wa.md` | Its WA stories are restated as Z5/Z6 below, anchors included. Its FL and NY story tables are NOT moved — that work shipped and is narrated in `epic-P.md` and `epic-Z.md` from other sources; the tables themselves are in git history. |
| `2026-tax-planning-features.md` | All twelve features shipped; `pre-epic.md` and the per-skill specs under `docs/superpowers/specs/`. Its `/tax-retirement` v1.1 deferrals did NOT ship and are X10 below. |
| `2026-multi-agent-portability.md` | Epic Q shipped in PR #66; plan retained at `docs/superpowers/plans/2026-08-03-multi-agent-portability.md`. |
| `2026-07-12`, `2026-07-17`, `2026-07-20` reviews | Epics I-O, all shipped; `docs/changelog/epic-{I,J,K,L,M,N,O}.md`. |

Historical specs and plans under `docs/superpowers/` still cite the deleted
filenames. Those are frozen records of what was true when they were written and
are deliberately not rewritten — a May 2026 spec citing a May 2026 roadmap is
accurate history, and git resolves it.

---

## Status

**Engineering: drained.** Epics V, W and X are complete but for P3 filler, and
`main` is green locally. The suite's size is stated once, in
`.claude/hooks/skill-invariants.sh`'s MEASURED COSTS block, and deliberately
not repeated here — a count restated in a second place is the mirror this file
was collapsed to remove.

**Product: blocked on one decision.** The repository is private, Discussions are
off, and there are zero non-owner users. Every distribution item has been
waiting on that since 2026-08-12.

**Unverified: everything from #155 on.** CI minutes were exhausted 2026-09-13
18:29 UTC and return 2026-10-01. Every merge since was validated on one local
interpreter. A merge that can change what a return says touches the engine, a
skill or a template, so after a `git fetch`,
`git log 70d10b2..origin/main -- agents/lib .agents/skills agents/templates`
lists every candidate since the last green CI run. It is a superset to triage,
not a verdict, and a list typed here would go stale at the next such merge. The
declared floor has been 3.13 since #166 and **has never executed anywhere**;
Windows has not run since 2026-09-13.

---

## The plan, in order

| When | What | Why |
|---|---|---|
| **Now** | **Y1** — publish and enable Discussions, or demote the distribution goal in writing. | Oct 15 is the last TY2025 window; the next is January. |
| **2026-10-01** | **AA2** — full matrix on `main` before any new work. | Everything from #155 on ran on one interpreter; the floor has never run. |
| **2026-10-01+** | **W5a**'s two unblocked clusters. | The only tooling work that DELETES assertions rather than adding them. |
| **Oct-Nov** | **Y2** — pre-stage the replay → auditor edit → runbook chain now. | So the gate lifting means execution, not discovery. |
| **Before publish** | **AA6**, **AA7**, **AA13**, **AA11**, **AA12** — size the UNSUPPORTED list; state NYC's real scope; compute the City resident tax, with the part-year guard; compute or name the Yonkers taxes. | The first public issue will be someone hitting a block. |
| **Opportunistic** | **AA4**, **AA5**, **AA8**, **AA9**, **AA10**, **AA14**, X7, Z4-Z6, Z8. | — |
| **Closed** | W7, W8, W9 — declined. Y3 — declined with a re-open trigger. | All three trade a working mechanism for tidiness. |

---

## Open

### AA — the 2026-09-20 product-owner review, and what later reviews filed under it

- **AA2 (P1, XS): Run the full matrix on `main` the day CI returns.**
  Before any new work on 2026-10-01. A red 3.13 leg or a red Windows job is then
  the highest-priority defect in the repository, ahead of everything below. The
  first `windows-latest` run ever taken found two shipped defects, and every
  merge from #155 on is drift since the last one, which is not a small exposure.
  Bind the poll to the commit (`gh run list --commit "$SHA"`) — `gh pr checks`
  reads the previous commit's runs.

- **AA4 (P2, XS): A gate whose verdict depends on machine load.**
  `test_the_permissions_hook_timeout_covers_a_full_archive_walk` failed at load
  6.9 and passes at 3.1. That is the cry-wolf shape — a gate that fires on
  correct data teaches the agent to reason past it, and the recorded outcome is
  that the assertion gets deleted rather than fixed. Either give it a
  load-independent assertion or mark it `slow` and take it off the default path.

- **AA5 (P3, XS): The runtime lock has no universal-resolution guard.**
  `test_every_lock_was_resolved_universally` parametrizes over
  `requirements-dev.txt` and `requirements-test.txt` and omits
  **`requirements.txt`** — the runtime lock, the one pinning `pdfplumber`,
  `openpyxl` and `defusedxml`, the only lock that reaches a taxpayer's documents.
  It IS universal today, so this is a missing guard rather than a live defect.
  One line.

- **AA6 (P2, S): Size the UNSUPPORTED list against the named personas.**
  The capability gate's BLOCK list defines the addressable user base and nobody
  has written down how large the hole is. Rental income blocks, and a CA or NYC
  high earner with a rental condo is not an exotic filer. "Explicitly not
  scheduled" is a defensible list; leaving it unsized until the first public
  issue arrives is not. Deliverable is a paragraph per named persona (CA, FL,
  NYC, NC, WA) saying what fraction of that persona hits a block, and which
  blocks. It is analysis, not a feature, and it belongs before Y1 rather than
  after.

- **AA7 (P2, XS): State NYC's real scope.** **BUILT by the pull request that
  records it in `docs/changelog/epic-AA.md`; this entry moves to the shipped
  index the day that merges.** "Parity with CA for a NYC taxpayer" was the
  owner's definition of done and Z1-Z3 delivered most of it — **for a full-year
  resident of New York alone, and without the City resident tax itself (AA13).**
  For the NYC persona specifically that excludes two of the commonest cases in
  the metro: `part_year_residency` BLOCKS (moved in or out mid-year) and
  `nonresident_source_income` BLOCKS (a City resident working in New Jersey, a
  New Jersey or Connecticut resident working in the City; the other-state credit
  is not modeled). Both land on Z8, which is P3 and L. The blocking is the
  correct bias and Z8 is genuinely large — the defect is that nothing SAYS so. A
  reader of the shipped index concludes NYC is finished.

- **AA8 (P2, XS): Stop pinning the whole suite's count in the hook.**
  `test_the_measured_costs_counts_match_live_collection` holds MEASURED COSTS'
  scale row and its `pytest tests/` sentence to the exact collected count, so
  every PR that adds a test ANYWHERE must hand-edit
  `.claude/hooks/skill-invariants.sh`, and that edit selects the hooks module,
  the slowest check the hook runs, at tool time. Since ruling Q13 the row's only
  job is "about twice" a cold pass, which needs no exact count. Drop the count
  from the scale row, and the suite-wide pair and skip reconciliation with it,
  leaving the seven hook-selected modules pinned. In the same change, reword the
  Status sentence saying the suite's size "is stated once, in" MEASURED COSTS:
  after AA8 nothing states it. Raised by AA3's review rounds (#172).

- **AA9 (P2, S): The skill hook times itself against its own timeout.**
  An overrun of the registered timeout CANCELS the hook, and a cancelled check
  reads to the agent exactly like a pass. The headroom test guards the half of
  that the count drives; what it cannot see is a per-test cost that climbs while
  the count stands still, which MEASURED COSTS names as its one blind spot and
  ruling Q13 leaves to a cold pass re-timed only when a threshold moves. The
  hook can close it at runtime: time its own pass and report, without blocking,
  when a pass uses more than a set fraction of the timeout. That is a comparison
  against a threshold, not a recorded reading, so it stays inside Q13. Raised by
  AA3's review rounds (#172).

- **AA10 (P3, XS): Tidy what AA3's review rounds left.**
  In `tests/test_claude_hooks.py` unless named: `_words_to_int` keeps negative
  and thousands branches and a docstring for the counts-ahead paragraph AA3
  deleted; the counts test flattens the block with its own regex beside
  `_comment_prose`, and re-implements `_the_one_statement` for the portability
  and hooks sentences; the `HOOKS_DEPENDENTS_EXCLUDED` message still says the
  counts test reacts to "one of" six modules; the headroom comment cites "the
  rule that keeps the seconds in one place"; the retry cost below MEASURED COSTS
  states a timing ratio the block does not hold; and `agents-compat.md` and the
  `GIT_TIMEOUT` comment in `tests/test_claude_agents.py` argue an absolute from
  a ratio. One owner question rides along: whether CLAUDE.md's one-owner rule
  for timings covers `harden-runtime-perms.sh`, which states its own readings in
  seconds. Accepted and recorded so a review does not re-find them: outside the
  block the timing test reads whole-line comments only, and catches neither a
  duration spelled in words nor a number two words from its unit. Fold into the
  next PR that touches these tests.

- **AA11 (P1, XS): A part-year City resident can be taxed for the whole year.**
  **BUILT by the pull request that records it in `docs/changelog/epic-AA.md`;
  this entry moves to the shipped index the day that merges.**
  A single filer is asked only "are you a New York City resident?", and
  `residency.days_present_by_state` is keyed by STATE, so City residency is the
  taxpayer's assertion. One who lived in the City for part of the year and
  stayed in New York can answer yes and have City taxable income (line 47) taken
  for the whole year; once AA13 computes the tax on it, that over-states the
  City tax by the months spent outside the City, the direction §4 calls a hard
  error. Land it with or before AA13. A joint return is asked how many spouses
  were residents for the WHOLE year, but `compute_nyc_taxable_income` refuses
  only a count of one: a couple who both lived in the City for part of the year
  answer zero with `nyc_resident` true, and are treated as full-year residents
  the same way. Ask the single filer the whole-year question, and refuse
  whenever `nyc_resident` is true and nobody was a whole-year resident, until
  the part-year City line 50 (IT-360.1) is modeled. Found while scoping AA7; the
  joint case by its review.

- **AA13 (P1, S): The New York City resident tax is never computed.**
  **BUILT by the pull request that records it in `docs/changelog/epic-AA.md`;
  this entry moves to the shipped index the day that merges.**
  `state.nyc_tax` is fetched (the three rate schedules and the school tax credit
  tables) and read by NOTHING in the engine. No function computes IT-201 line
  47a: `compute_city_and_yonkers_total` takes `nyc_resident_tax` as an argument
  defaulting to 0.0, and the compute step never said how to produce it. So a
  City resident's return omits the City tax, the core of the NYC persona, unless
  the agent improvises bracket math the engine should own. Z2 shipped City
  taxable income, the City household credit, the MCTMT and the line-58 caps, was
  recorded as lines 47-58, and the `nyc_resident` block was deleted on the
  strength of it. Compute line 47a from the fetched schedules by filing status,
  verified against the instructions, and wire it to line 52; land AA11 with it.
  Until then the capability note and the compute step name the gap (AA7). Owner
  question: whether the `nyc_resident` block comes back until this ships. Found
  by AA7's second review.

- **AA12 (P1, S): The Yonkers taxes are fetched and never computed.**
  `state.yonkers_tax` is fetched (the resident surcharge rate on the net State
  tax, the nonresident earnings rate and its exclusion) and the interview sets
  `yonkers_resident` and `yonkers_nonresident_earnings`, but no engine function
  computes IT-201 line 55 or 56. `compute_city_and_yonkers_total` takes both
  amounts as arguments defaulting to 0.0, and the compute step names the flags
  without a formula, so a Yonkers resident's return omits the surcharge unless
  the agent improvises one, and until AA7 nothing on `meta.limitations` said so.
  The capability note now names the gap, which makes it §4's degrade, an
  under-statement WITH a banner; the amounts are still wrong. Either compute
  both lines from the fetched block (the fetcher's own note: the nonresident tax
  is capped at what the resident surcharge would have been) or take them as
  named inputs on `meta.limitations`, as the refundable credits are. AA7 stopped
  the README and the capability note claiming Yonkers. Found by AA7's review.

- **AA14 (P2, S): The State tax table is a dollar low on 22 joint rows.**
  Every cell of the published 2025 State table, compared with `compute_ny_tax`:
  the single column agrees on all 1,294 rows, and the joint column differs on
  22, every one in the $23,600-27,900 band and every one a dollar LOW (the
  $23,600-23,650 row publishes 978; the engine gives 977). Those rows match the
  bands summed to that floor, 976.25, and not the published base of 976, which
  `_ny_schedule_tax` reads by design. The head-of-household column is
  unchecked, because the test fixture has no schedule b. Find out how New York
  builds its table, and why the single column does not show the same thing,
  then pin the joint rows by test. It is an under-statement within the
  one-dollar acceptance bar, so P2. It is on `main`, not introduced by the
  AA13 change, whose review found it.

### W — engineering hygiene

- **W3 (P3, L): the hook stamp state machine.** *Selection shipped in #163/#164;
  slice 3 was attempted and abandoned 2026-09-19.* What remains is the stamp
  trust cascade and the red carry-forward. It is worth doing for the READING,
  which is the item's own argument, and it is NOT urgent: mutating
  `read_red_checks` to drop one token turns two tests red, so the round trip
  already has coverage and a port adds a second implementation of well-pinned
  logic rather than closing a gap. **Before attempting it again, read the
  abandoned slice's post-mortem in `docs/changelog/epic-W.md`** — the NUL
  divergence in `$(sed …)`, the `newline=""` requirement at the trust cascade,
  the differential that was half vacuous, and the interpreter start that must
  stay below `nothing_selected && exit 0`. Q12 ruled the bootstrap: a bash shim
  that makes the same `[ -x .venv/bin/python ]` check, holds the change boundary
  the same way, then `exec`s the Python, which lives outside `agents/lib/` so it
  never meets the import guard.

- **W5a (P3, S): generate three of the copies instead of asserting about them.**
  The interpreter floor is stated in roughly two dozen places and #166 compares
  them all. Comparison is right for PROSE, which nothing generates; it is one
  level too low for three machine-readable clusters.
  - `actions/setup-python` accepts `python-version-file: .python-version`,
    deleting three single-version job pins. **Blocked until 2026-10-01** and the
    reasoning holds: setup-python's resolution of `3.13` to a patch release is
    observable only on a runner, so nothing local distinguishes "installed the
    floor" from "installed something else".
  - the three step conditions (`matrix.python-version == '3.13'` twice, `!=`
    once) become data if the matrix carries `include:` entries with a `coverage:`
    boolean. **Not blocked** — verifiable by parsing, which the guard already
    does — and it collapses `LEG_STEPS` to one question.
  - `--python-version 3.13` is hand-typed in five files. A
    `scripts/recompile-locks.sh` reading `.python-version` deletes all five and
    makes the recipe runnable. It does NOT retire the recipe parser: the three
    `.txt` headers are echoed BY uv and are a fact about how each lock was made.

- **W10 (P3, M): a state REGISTRY.** *Raised by the 2026-09-12 four-angle review.*
  Six parallel per-state tables across four modules: `projection.STATE_SECTION_KEYS`
  and `_STATE_SECTION_CODES`, the two `_*_COMPUTED_EVIDENCE_FIELDS`, `yoy_diff`'s
  three, `capabilities._state_note`'s if/elif chain, `tie_out`'s per-state
  withholding rows, and `tax_calendar`'s `if CA / elif NY`. Adding a state means
  finding all six and NOTHING fails if one is missed. One
  `agents/lib/state_registry.py` keyed by code, each module deriving its table,
  and a test forbidding a local copy. Gates Z4 and Z5 in practice.
  **Fold in the two generic deferrals recorded at the end of
  `docs/changelog/epic-P.md`** — a data-driven `state.modifications` block and a
  `state.local` block shape. New York shipped both NY-specifically, so the
  generic versions are open engine work that belongs to this item rather than
  to one of their own.

- **W11 (P3, M): one `StateTrueUpSpec` in `withholding.py`.** *Same review.*
  `CaTrueUp`/`NyTrueUp`, `read_ca_inputs`/`read_ny_inputs`,
  `_prior_ca_tax_agi`/`_prior_ny_tax_nyagi`, `build_ca_true_up`/`build_ny_true_up`,
  two render blocks and a three-way caveat chain. `projection._section_computed`
  is the right move made once and stopped one level short; a third state
  duplicates all of it again.

- **W12 (P3, S): let a test module DECLARE its inputs.** *Same review, restated
  against what the hook is now.* The cross-module test dependencies live in
  `_ARMS` in `.claude/hooks/lib/hook_selection.py` — the shell `case` this item
  was originally written against was ported in #163 and deleted in #164, so the
  language changed and the shape did not: the map is still in a different file
  from the tests that have the dependencies, and still hand-maintained. A
  `WATCHES = (...)` at module level, with the hook building its map from those
  declarations and a test asserting every path a module opens is under its own
  `WATCHES`, collapses two hand-kept lists into one — and a test that grows a new
  input could not then go unselected.

### X — tax correctness

- **X7 (P3, S): the O3 planning-tool carry-forwards.** Deduction-only marginal
  probe for `_est_marginal_fed`; scenario-keystone §63(f)/SE-tips threading;
  `/tax-harvest` §1091 basis-shift modeling. The first two are absorbed by V1 if
  the orchestrator exposes a deduction knob on `compute_scenario`; sequence them
  after it rather than as separate work.

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
  Ruled at Q5: **write the procedure, touch nothing.** Both reviewer definitions
  and `docs/ops/review-subagent-acceptance.md` stay exactly as they are,
  certified with their digest pins matching, and the runbook carries one ordered
  procedure. What gates Y2 is the figures publishing and then the owner's fetch
  and FRESH-SESSION replay — neither a decision. **Pre-stage the chain now**, in
  one place, because it has three links and they are currently recorded
  separately: replay from a fresh session → correct the auditor's stale keying
  note (it still tells the auditor `federal.capital_gains_rates` is keyed
  `single`/`mfj` alone, which #89 changed a month ago) → run the runbook. The
  definition is digest-pinned to a PASSED acceptance, so editing it without the
  replay silently withdraws an acceptance this repository advertises.

### Z — state coverage

- **Z4 (P3, S): North Carolina D-400.** A `graduated_income` module with a single
  bracket per year — the flat rate is on a statutory step-down schedule, so it
  must be year-gated and FETCHED, never inlined — NC standard deduction and child
  deduction, the short Schedule S list (state/local bond interest, Social
  Security subtraction, no §179 conformity gap for individuals), NC-40 estimates.
  Smallest possible state module; a good first outside contribution once public,
  and a proving ground for the playbook on a flat-tax regime.

- **Z5 (P3, M): Washington capital-gains excise.** A different tax BASE, not a
  different bracket table. WA is deliberately NOT in `_NO_INCOME_TAX_STATES`
  because of RCW 82.87, so a wage-only WA filer gets the ESTIMATE note where an
  FL filer gets an affirmative "nothing is owed" — and if a fetch ever populates
  `state.brackets`, projection and harvest would tax ORDINARY income, a phantom
  liability. So: a `state.wa_capital_gains` block with `state.brackets` left
  EMPTY, `compute_wa_capital_gains`, a `washington_ltcg_excise` result section,
  the April event, and the affirmative statement.

  **The durable half — structure and authority, no figures.** Every amount is
  FETCHED per AGENTS.md §2; what does not go stale is which statute says what,
  and these are the correctness traps a prior fact-check hardened:
  - The base is federal net **LONG-TERM** gain in the §1222(7) sense. **Short-term
    losses do NOT offset it** — start from Schedule D's LT net, NOT line 16.
  - Remove non-WA-allocated gains **and losses**; **add back pre-2022 LT loss
    carryforwards**.
  - **ONE combined deduction per return, whatever the filing status.** Doubling
    it for MFS-shaped logic UNDER-states the tax, which §4's bias taxonomy makes
    a hard error rather than a banner.
  - The deduction is indexed and published annually by DOR; **never extrapolate
    the next year from the last**. The second tier is a surcharge over one
    million dollars that is **FIXED, not indexed** (ESSB 5813, effective TY2025),
    which is why it is a different kind of figure from the deduction beside it.
  - A charitable offset applies above a threshold, with a cap; both indexed.
  - Per-disposal exemptions (RCW 82.87.050): real estate, retirement-account
    assets, condemnation, qualifying farm livestock (>50% farming income), and
    depreciable trade-or-business property. v1 is flags and caveats, not
    auto-classification.
  - **Filing mechanics that differ from every state modeled so far**: the WA
    return is NOT part of the federal e-file (a separate My DOR filing), an
    extension extends FILING only with payment still due in April, and **no
    quarterly estimates exist** — emitting 1040-ES-shaped state events would
    invent a deadline. SSB 5314 (2025) added mandatory amended returns when the
    federal return changes, and **flipped the B&O credit's direction** (before:
    a credit against B&O; after: against the capital-gains tax) — the same
    hardcoded-law drift the PTET P1s taught.
  - Legal status is settled: Quinn (2023), cert denied 2024, the I-2109 repeal
    failed November 2024. Anchors: RCW 82.87 (.050 exemptions, .070 small-business
    deduction, .100 other-jurisdiction credit), ESSB 5813, SSB 5314.

- **Z6 (P3, S): WA harvest and crypto synergy, the WFTC reminder.** Three small
  items that only pay off once Z5 exists, and the first is a correctness
  inversion rather than a feature:
  - **`/tax-harvest` WA mode.** LT-loss harvesting has a direct state payoff
    above the deduction; **ST harvesting saves nothing state-side** — the exact
    INVERSE of the "ordinary rate × Δ taxable income" model every other state
    uses. The deduction is a cliff: losses only pay once gains exceed it. Today
    real WA savings are reported as zero under a banner blaming missing data,
    which is the safe direction and still wrong.
  - **Crypto.** DOR treats crypto as intangible under a **domicile-at-time-of-sale**
    test, so `/tax-parse-crypto`'s LT rows are exactly the WA base and its ST and
    staking rows are irrelevant to it. Mid-year movers need per-transaction
    domicile, never proration.
  - **WFTC** is a SEPARATE application (workingfamiliescredit.wa.gov), not a
    return line, and ITIN filers qualify. An EITC-eligible WA resident who never
    applies simply loses it — which is the same population AA1 is about.

- **Z8 (P3, L): IT-203 part-year and nonresident.** Relaxes the single-state
  assumption in `address.state`. Until then `moved_states_this_year` and
  `has_nonresident_source_income` keep BLOCKING, which is the correct bias — two
  resident states tax the same income and the credit between them is not
  modeled. It is the completion condition for the NYC persona, which the README,
  the Z1 row and the state-module playbook now say (AA7).

- **X10 (P3, S): `/tax-retirement`'s three v1.1 deferrals.** Surfaced in PR #20's
  review, intentionally caveated rather than modeled, and homeless since the
  planning-features roadmap was deleted. All three are still caveated in the
  shipped dashboard, so nothing is silently wrong — and all three are gated on
  the same thing, richer per-account YTD inputs than the "paste your numbers"
  path collects:
  - **Pre-tax/Roth YTD deferral split.** Treas. Reg. §1.414(v)-2(b)(1): a §603
    high earner's catch-up need be Roth only to the extent prior designated-Roth
    deferrals are BELOW the catch-up limit, so someone already over it may make a
    PRE-TAX catch-up. Needs a `roth_401k_ytd` input; the dashboard collects only
    the total. v1 treats the whole high-earner catch-up as Roth and caveats that
    the savings may be UNDER-stated — the safe direction.
  - **HSA eligible-months proration.** §223(b)(2) prorates by eligible months,
    but §223(b)(8)'s last-month rule lets a December-1-covered participant use
    the full limit against a 13-month testing period. A naive `× months / 12`
    would be wrong for the common case, which is why v1 shows the full limit and
    caveats the assumption.
  - **The full state retirement-conformity matrix.** v1 models PA, NJ and MA
    non-conformity; the complete 50-state per-account matrix is open-ended and
    lower-certainty. Note the bias trap recorded against New York in
    `docs/changelog/epic-Z.md`: absence from a non-conformity set makes the
    dashboard CLAIM a state saving, so it is not the safe default an absent
    figure usually is.

### Deferred with the work specified, not blocked

- **X9 (P3, S): the figure-lint detector's line-break blind spot.** Its scope is
  derived now, but `[^.\n]` between the
  limit word and the amount cannot cross a LINE BREAK, so a figure that markdown
  wrapped is invisible — which is how "~$510" survived in `/tax-extend`. Every
  fix measured so far trades one blind spot for another; the measurements are in
  `docs/ops/season-open-runbook.md` under the X6 entry.

---

## Blocked on an owner ACTION, not a ruling

| Item | What it waits on |
|---|---|
| **Y1** | The owner publishing the repository and enabling Discussions. Verified still private, Discussions off, 0 stars. |
| **Y2** | TY2026 figures publishing, AND re-running the review-subagent replay from a FRESH session first. It is also the only route to the auditor's stale keying note. |

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
  substitute. That last one is worth re-reading beside AA2: the matrix question
  is live again, the checklist substitute has no owner, and CI has been down
  since 2026-09-13. Note this is a DIFFERENT ground from the Distribution table's
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

| Item | PRs | Not done |
|---|---|---|
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
| W3 | #163, #164 | OPEN: the stamp state machine, above |
| W4 | #90, #118 | the MEASURED COSTS seconds were retired by Q13 rather than re-measured |
| W5 | #90, #166 | W5a is a separate open item, above |
| W6 | #105 | — |
| X1 | #89 | the auditor's keying note is WRONG, not pending; only the Y2 replay can correct it |
| X2 | #86 | — |
| X3 | #88 | — |
| X4 | #110, #142 | — |
| X5 | #143, #144, #155 | the line-break blind spot is X9, open above |
| X6 | #154 | 2027-2029 SALT figures are computed from the statute's 101% step, not published; the IRS figure wins through the cache |
| X8 | #159 | `return_builder.build` and `withholding.build_ca_true_up` validate one frame down — declared, and probed at both ends |
| Y4 | #112 | — |
| Y5 | #113 | — |
| Y6 | #114, #119, #165 | — |
| Y7 | #120 | no Windows coverage — the Windows job runs two modules |
| Z1 | #123, #124-#137 | scope is a FULL-YEAR resident of New York alone: a move between states, statutory residency in a second state, or income sourced to another state that taxes it BLOCKS until Z8 (AA7), the City resident tax (line 47a) came later with AA13, a part-year City resident is refused (AA11), and the Yonkers lines 55-56 are not computed (AA12); within it, IT-196 lines 1-45, IT-201 line 45's other taxes, each refundable credit's AMOUNT, line 59's sales/use tax, part-year City and Yonkers lines 50/57 and city-level day counts — all NAMED on `meta.limitations` |
| Z2 | #130 | City taxable income, the City household credit, the MCTMT and the line-58 caps; the City resident tax, line 47a, came later with AA13, and Yonkers lines 55-56 are AA12 |
| Z3 | #131 | — |
| Z7 | #121 | domicile as a GATE is deliberately not modeled |
| AA1 | #171 | the EIC is DISCLOSED, not computed — out of persona by the 2026-07-12 ruling |
| AA3 | #172 | the whole-suite count stays pinned (AA8); the hook does not yet time itself (AA9); small leftovers and the timing test's accepted limits (AA10) |
| Q7 | #156 | — |
| Q9 | #156 | — |
| Q8 | #158 | — |
| Q10 | #158 | — |
| hygiene | #160, #161, #162, #167 | not backlog items, though some change what a return says (the Status section's `git log` range lists the candidates); narrated in `docs/changelog/` |

Epics I through U shipped earlier; see `docs/changelog/README.md` for the
per-epic index and `epic-R-U.md` for R1's audit baseline.
