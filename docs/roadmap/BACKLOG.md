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
`main` is green locally. The suite's size is stated nowhere: since AA8 the
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
| **Now** | **Y1** — publish and enable Discussions, or demote the distribution goal in writing. | Oct 15 is the last TY2025 window; the next is January. |
| **Oct-Nov** | **Y2** — run the staged fetch → auditor edit (including **AA19**) → fresh-session replay chain. | Begins when the year's figures publish. |
| **Opportunistic** | **W11** — unify state withholding true-ups. | Test-owned hook inputs are merged. |
| **Closed** | W7, W8, W9 — declined. Y3 — declined with a re-open trigger. | All three trade a working mechanism for tidiness. |

---

## Open

### AA — the 2026-09-20 product-owner review, and what later reviews filed under it

- **AA19 (P2, S): The auditor's SALT-cap check still awaits Y2.** The engine
  validation is implemented under Q14: Schedule A and PTET refuse a cached
  amount that differs from `salt.base_cap`, accepting its exact amount at cents
  or its nearest whole dollar (half up), before the per-return phasedown.
  AGENTS.md §2 now requires agreement rather than unconditional cache precedence.
  The remaining auditor edit rides Y2's chain, since the definition is
  digest-pinned to a passed acceptance. Its check 5 still says `federal.salt_cap`
  "indexes roughly 1%/yr" and that an absent value "degrades quietly", and never
  checks for a stale or already-reduced amount. Found by AA16's second review.


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

- **W10 (P3, M): a state REGISTRY.** **The data tables are BUILT by the pull request that
  records "W10, first slice" in `docs/changelog/epic-W.md`:**
  `tax_computations/state/registry.py` holds each state's section, code,
  computed-evidence fields, movers and informational key; projection's and
  yoy_diff's tables derive from it, and a test refuses a local copy. The
  PTET regime windows `/tax-ptet` and `/tax-calendar` each carried are ONE
  registry table by the pull request that records "W10, second slice", and
  the filing package's state forms derive from it by the one recording "W10,
  third slice" (a NY package had no IT-201). The per-state BEHAVIOUR stays
  in its module by design, and the one recording "W10, fourth slice" makes
  missing it fail: a test requires each computed state's tie-out category,
  depth note, estimated-tax and extension events, withholding true-up and
  package form. **What remains open is
  the two generic deferrals below.** *Raised by the 2026-09-12 four-angle review.*
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

### X — tax correctness


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
  and FRESH-SESSION replay — neither a decision. **Pre-stage the chain now**, in
  one place, because it has three links and they are currently recorded
  separately: replay from a fresh session → correct the auditor's stale notes
  (it still tells the auditor `federal.capital_gains_rates` is keyed
  `single`/`mfj` alone, which #89 changed a month ago, and AA19's `salt_cap`
  check) → run the runbook. The
  definition is digest-pinned to a PASSED acceptance, so editing it without the
  replay silently withdraws an acceptance this repository advertises.
  One more check rides the runbook: when the TY2026 State and City tables
  publish, compare them with their schedules and fetch `tax_table.table_bases`
  (AA14). A cache without the key gets a note, not a refusal, and the row can
  then be a dollar off in either direction.

### Z — state coverage

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

| Item | PRs | Not done |
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
| W3 | #163, #164 | OPEN: the stamp state machine, above |
| W4 | #90, #118 | the MEASURED COSTS seconds were retired by Q13 rather than re-measured |
| W5 | #90, #166 | W5a, below |
| W5a | #191 | the matrix legs, the tool targets, `MIN_PYTHON` and the prose still state the floor, compared rather than read; the lock headers are uv's record and stay |
| W6 | #105 | — |
| W12 | #225 | Test-owned hook input declarations and independent read/import audit. |
| X1 | #89 | the auditor's keying note is WRONG, not pending; only the Y2 replay can correct it |
| X2 | #86 | — |
| X3 | #88 | — |
| X4 | #110, #142 | — |
| X5 | #143, #144, #155 | the line-break blind spot is X9, open above |
| X6 | #154 | 2027-2029 SALT figures are computed from the statute's 101% step, not published; the IRS figure wins through the cache |
| X7 | #218 | future-sale tax and chained wash-sale repricing remain outside the replacement basis plan |
| X8 | #159 | Engine entry-point status validation: direct checks or declared delegation probed at both ends |
| X10 | #224 | Roth catch-up split, confirmed HSA eligibility and fetched per-account state conformity. |
| Y4 | #112 | — |
| Y5 | #113 | — |
| Y6 | #114, #119, #165 | — |
| Y7 | #120 | no Windows coverage — the Windows job runs two modules |
| Z4 | #219 | TY2025 full-year D-400; other form years and farmer/annualized NC-40 worksheets remain manual |
| Z5 | #220 | TY2025 excise; separate households, special federal normalization and Z6 harvest pricing remain out of scope |
| Z6 | #223 | WA excise harvest pricing, crypto domicile workpapers and separate WFTC reminder. |
| Z8 | #221 | TY2025 NY source allocations and NYC IT-360.1; other-state returns/credits, MFS, mixed spouses, IT-230 ratios and IT-203 Yonkers remain manual/refused; NY forecasts are skipped |
| Z1 | #123, #124-#137 | scope is a FULL-YEAR resident of New York alone: a move between states needs a supported IT-203 plan (#221; AA7), while statutory second residency and other taxable-state returns retain their gates, the City resident tax (line 47a) came later with AA13, a part-year City resident is refused (AA11), and the Yonkers lines 55-56 came later with AA12, for a whole-year Yonkers resident or a nonresident who does not file jointly (special condition Y1 and a joint Form Y-203 are refused); within it, IT-196 lines 1-45, IT-201 line 45's other taxes, each refundable credit's AMOUNT, line 59's sales/use tax, part-year City and Yonkers lines 50/57 and city-level day counts — all NAMED on `meta.limitations` |
| Z2 | #130 | City taxable income, the City household credit, the MCTMT and the line-58 caps; the City resident tax, line 47a, came later with AA13, and Yonkers lines 55-56 with AA12 |
| Z3 | #131 | — |
| Z7 | #121 | domicile as a GATE is deliberately not modeled |
| AA1 | #171 | the EIC is DISCLOSED, not computed — out of persona by the 2026-07-12 ruling |
| AA3 | #172 | the whole-suite count stays pinned (AA8); the hook does not yet time itself (AA9); small leftovers and the timing test's accepted limits (AA10) |
| AA7 | #174 | IT-201 remains resident-only; IT-203 uses the declared plan in #221, while other taxable-state obligations retain their gates |
| AA13 | #175 | a part-year City return (IT-360.1, line 50) is refused, not modeled |
| AA11 | #175 | — |
| AA12 | #176 | a part-year Yonkers resident (IT-360.1, line 57), special condition Y1 and a joint Form Y-203 are refused, not modeled; the zero default on the line-58 total is AA15 |
| AA6 | #178 | three blocks cannot be sized from public data (statutory residency in a second state, a PFIC event, out-of-state income other than a commute); the figures are tax year 2023 and need a yearly refresh |
| AA16 | #179 | modified AGI is an input the compute step forms, as Schedule 1-A's is; the planning tools say the cap is held at the baseline rather than re-price it |
| AA20 | #180 | the planning tools hold the return's deduction rather than re-price it: the SALT phasedown and the medical floor are caveated, a capped charitable deduction refuses, and the TY2026 charitable floor, the §70111 haircut and an itemize-or-standard switch are held; /tax-project was AA21 |
| AA21 | #181 | a declared itemized total is priced as entered, not re-worked at the projected AGI (AA23); the §170(p) deduction for non-itemizers was AA22 |
| AA22 | #182 | the gifts are priced as declared, which of them qualify said in a note rather than asked; an itemizer's cache fetched before it lacks the cap and refuses until that one block is fetched; no engine function owns the itemize-or-standard choice (AA23); the note's $250 acknowledgment threshold is the owner's call |
| AA25 | #185 | most permissions-hook routing tests still pass with the exposure scan off (AA26) |
| AA4 | #184 | the walk is pinned by its shape, in chmods, not priced in seconds; nothing prices it against its timeout, which is held where it is (AA24) |
| AA5 | #186 | — |
| AA8 | #186 | the suite's size is stated nowhere, and the scale row's "about twice" a cold pass is a reader's to keep, not a check's |
| AA17 | #188 | a part-year return across the border is blocked, not modeled, and so is a non-citizen's dual-status federal year |
| AA14 | #189 | a cache without `table_bases` gets a note, and the row can be a dollar off either way; the TY2026 check rides Y2 |
| AA27 | #189 | — |
| AA28 | #190 | the TY2026 `table_bases` check rides Y2 |
| AA29 | #192 | a 0 written after the template passes as computed, and the refused households are caught at Step 12.7 rather than Step 0.7 (AA30) |
| AA18 | #193 | an acknowledged territory return adds the excluded income back by hand; a territory mailing address on a stateside resident blocks too, the degrade direction |
| AA31 | #194 | — |
| AA30 | #195 | a household line's 0 still passes as computed (AA32); an acknowledged refused household's City or Yonkers tax is prepared by hand |
| AA34 | #207 | the planning tools still hold above the top bracket's floor, where §68's base needs the priced point's QBI deduction |
| AA32 | #202 | an agent that types a household line can type its method too; a receipt binding would close it |
| AA26 | #201 | the scan call for a bounded command naming archive outside `archive/YYYY` has no test; the `git` and `rg` tests pin less than their names |
| AA10 | #200 | outside MEASURED COSTS the timing rule reads whole-line comments only, and catches no duration in words |
| AA33 | #199 | outside MEASURED COSTS the figure rule still reads whole-line comments only (AA10) |
| AA24 | #198 | it prices the walk only when the walk ran past a second, and reads its timeout from `settings.json` |
| AA9 | #197 | it times the pytest pass, not the selector and scan around it |
| AA2 | #189 | the first green full matrix on `main` ran on #189's merge |
| AA15 | #188 | nothing yet refuses a line 58 typed by hand or an agent that never calls the total (AA29) |
| Q7 | #156 | — |
| Q9 | #156 | — |
| Q8 | #158 | — |
| Q10 | #158 | — |
| hygiene | #160, #161, #162, #167 | not backlog items, though some change what a return says (the Status section's `git log` range lists the candidates); narrated in `docs/changelog/` |

Epics I through U shipped earlier; see `docs/changelog/README.md` for the
per-epic index and `epic-R-U.md` for R1's audit baseline.
