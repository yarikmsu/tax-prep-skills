# Staff Agent-Developer Review + Backlog — 2026-09-05

> **Status 2026-09-11 — ADOPTED; one task per PR, in the Part 4 order** (Y1 postponed
> until the owner asks). Every item below is reviewed locally — code review, cleanup,
> bias direction — and then by `codex review --base <its base branch>` where the budget
> allowed; the findings and their fixes are in `docs/changelog/epic-V.md` and
> `epic-X.md`. **Step 4 is blocked on the owner rulings in Part 5**, which is why step 9
> was taken ahead of it.

### Shipped

This table is the STATUS, and every Part 4 entry it names carries `— DONE` to match. It
replaces a single run-on paragraph that had grown past reading: V11 was recorded as
shipped there, was never marked DONE below, and was very nearly re-implemented from the
Part 4 list a month later. A test holds the two halves together in both directions.

| Item | PRs | What is NOT done |
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
| W4 | #90, #118 | the MEASURED COSTS *seconds* sitting is OWED — it needs an otherwise-idle machine, and three attempts were killed for host memory |
| W5 | #90 | — |
| W6 | #105 | — |
| X1 | #89 | the auditor's own keying note joins the Y2 replay |
| X2 | #86 | — |
| X3 | #88 | — |
| X4 | #110, #142 | — |
| X5 | #143 | `tax-compute`'s eighteen quoted figures are NOT covered — the next slice |
| Y4 | #112 | — |
| Y5 | #113 | — |
| Y6 | #114, #119 | — |
| Y7 | #120 | — |
| Z1 | #123 (spec), #124-#137 | IT-196 lines 1-45, IT-201 lines 44/45, each refundable credit's AMOUNT, line 59's sales/use tax, part-year city lines 50/57 and city-level day counts — all NAMED on `meta.limitations` |
| Z2 | #130 | — |
| Z3 | #131 | — |
| Z7 | #121 | domicile as a GATE is deliberately not modeled |

**`context_interruption` (V3) SHIPPED in #141, and V10's run lock is what made it
possible.** A planted `summaries/` was tried in #116 and withdrawn: `tax-parse` scans
`documents/`, no skill had a resume instruction, so a compliant agent simply re-parsed
and the plant played no part. The case now plants `.tax-workspace.abandoned` — not a
simulation of an interruption but the artifact the engine writes when one happens, read
by `receipt.verify` and acted on by `/tax-compute` step 0.4.

**Two deferrals are owed to the Y2 fresh-session replay**, and both are blocked for the
same reason: `.claude/agents/tax-law-freshness-auditor.md` is digest-pinned to a PASSED
acceptance in `docs/ops/review-subagent-acceptance.md`, definitions load at session
start, and so a same-session replay tests the old one. Editing it without a replay
silently withdraws an acceptance this repository still advertises. Z1's own New York
keying gotchas went to `docs/contributing/tax-law-cache.md` for that reason.

### Not started

Nothing here has shipped in any part. An item that shipped in PART is in the table
above with the remainder named in its third column, and is deliberately NOT repeated
here — an id in both lists leaves a reader unable to say which is true. As of #141 there
are none: every item in the table shipped in full. **Bold** in this section means
the item is in this list; the backticks above are references to the table.

**W2**, **W3**, **W7**, **W8**, **W9**,
**X6** (= T3), **X7** (= U1), **Y1** (postponed until the owner asks), **Y2** (gated on
TY2026 figures publishing), **Y3** (an owner ruling is open), **Z4**, **Z5**, **Z6**,
**Z8**.

> This backlog sits beside
> `2026-08-12-product-owner-review.md`, which remains the active product backlog (Epics
> R-U). Items here that duplicate one there (Y1 = R6, Y2 = T1, X6 = T3, X7 = U1) defer
> to that file's status markers; Y4 and Y6 retire the two risks R2 polices. Epic Z
> re-cuts `2026-state-support-ny-fl-wa.md` against the next users the owner named on
> 2026-09-05: CA first, alongside the agent-reliability P1s; FL and NYC P2; NC and
> WA P3. Epic letters continue from U.

> **Second review folded in, 2026-09-05.** An independent agent-engineering review
> of `9f3bdb7` re-checked every finding here with synthetic probes and added one this
> review missed: the parse CLI prints raw document text, so a full SSN crosses into
> tool output before the agent is told to redact it (now V0, the only P0). Its other
> conclusions are adopted below: the three confirmed directional defects X1-X3 move
> to P1 ahead of the orchestrator; V3 gates V1 and V2 per migrated slice, not as a
> whole; V4, V6 and V7 gain enforceable acceptance criteria, V6 rising to P1/M;
> V8-V11 are new; W3 drops to P3. Part 2c holds the evidence. Two of its details
> were corrected on folding: the rollover defect is every non-finite value, not
> "+Infinity" alone, and the fix belongs at the JSON load boundary (E6); the
> truncating open it cited at `scenario.py:483` is at `:524`, and `tie_out.py` has
> a twin (E8). Its privacy item was narrowed to SSN/ITIN masking so the employer
> block the summaries need is not stripped, and its "refuse when isolation cannot be
> established" clause was bounded so the sequential fallback AGENTS.md §4 promises
> every non-Claude agent survives (V6).

Reviewer lens: staff engineer for AI-agent systems — reliability of the agent-mediated
path, evaluation, determinism, tooling and maintainability. Tax-domain correctness was
NOT re-audited; the three prior reviews found the engine sound and this one takes that
as given. Every measurement below was taken on `main` at `20266ed`.

---

## Part 1 — The headline

The engine and the integrity chain are in good shape. The security posture (PII gate,
source pinning, capability gate, tie-out) is unusually rigorous for a project of this
size. The weak spots are on the agent-engineering side, and they are structural:

- **Raw document text crosses into tool output.** `parse_utils.py extract` and
  `tables` print page text and tables unmasked; the `redact` helper in `secure_fs`
  masks SSN shapes in paths and error messages but is never applied to extracted
  content. The worker is told to mask the SSN AFTER reading it, by which point the
  full number sits in the session transcript on disk — the "intermediate file"
  AGENTS.md §1 forbids. The exposure is local (transcripts and tool logs), not the
  network; it still conflicts with the security contract and is the one P0 here.
- **The return itself is computed by prose, not by code.** `tax-compute` is ~1,700
  lines of Markdown instructing the agent to call 27 pure functions through inline
  `run.sh -c` snippets and hand-assemble `result.json`. Every run re-derives the wiring
  inside the model's context. The tie-out gate proves sums match the ledger, but it
  cannot see a wrong filing-status key, a skipped step, or a function called with the
  wrong argument. This is the largest remaining correctness risk, and no test covers it.
- **Numbers are transcribed by the agent twice.** Parsed documents become Markdown
  summaries; the agent then copies figures into `result.json` and, separately, into
  `parse_ledger.json`. The second-read step (A3 in the 07-12 review) mitigates the
  first transcription only.
- **There are no agent-level evals.** The suite has ~3,400 tests; none executes a skill
  with a model against a fixture workspace and checks the output. The one integration
  path that matters is untested by construction. `test_ty2026_readiness.py` says so in
  its own docstring ("a fixture proves WIRING and COMPUTATION, not FETCHABILITY").
- **Contributor tooling now outweighs the product.** The two Claude hooks total ~3,800
  lines of bash with ~5,200 lines of Python tests; `preflight.py` is ~2,700 lines with
  ~9,900 lines of tests. Infrastructure test code (~27k lines) is near parity with
  engine test code (~31k). No linter, no type checker, no `pyproject.toml`; pyflakes
  already reports five dead imports.

## Part 2 — Measurements

| Metric | Value | Note |
|---|---|---|
| Suite | 3424 passed, 2 skipped | 3 min 11 s wall, 38% CPU — I/O-bound on hook subprocesses |
| Slowest 15 tests | 14 in `test_claude_hooks.py` | 2-6 s each, all subprocess calls into bash |
| `tax_computations.py` | 5,615 lines, 61 functions | single module |
| `projection.py` | 4,562 lines | single module |
| `preflight.py` | 2,732 lines | an engine-presence check |
| Hooks (bash) | 2,330 + 1,473 lines | tested by 5,201 lines of Python |
| AGENTS.md + CLAUDE.md | 39 KB | loaded into every Claude Code session before any skill |
| Local venv | Python 3.14 | lock compiled for 3.12 |
| Lint findings | 12 pyflakes | 7 unused imports, 1 unused local, 4 placeholder-less f-strings; re-run before W1, the count moves |

## Part 2b — State coverage against the named next users

The owner named the next users and their order (2026-09-05): **CA first, alongside the
agent-reliability P1s of Epic V; FL and NYC at P2; NC and Seattle (WA) at P3.** CA is
already supported, so "CA first" means the P1 engineering work lands on the CA path
and the CA fixture is the eval harness's first case. Verified against `capabilities.py`, `tax_law_fetcher.py`,
`tie_out.py`, `projection.py` and `tax-compute` Step 10 on `main` at `20266ed`:

| State | What the engine does today | Verdict |
|---|---|---|
| **CA** | Form 540 in depth: Schedule CA, `compute_ca_itemized`, Schedule P AMT, MHST, exemption credits, excess SDI, 540-ES safe harbor and vouchers. Its own result section, ledger category (`state_withholding_ca`), PDF page and YoY mover fields. | **Supported.** |
| **FL** | `type: "none"` regime (PR #65): affirmative no-income-tax statement, Schedule A sales-tax election automated from the FL table, DR-405 and homestead flags, FL calendar events. | **Supported.** |
| **NY (state)** | The template is a stub (`brackets`, two standard-deduction keys, an empty `nyc_tax`). Step 10 falls to the generic "bracket-based ESTIMATE" path: no tax-benefit recapture, no IT-225 modifications, no IT-196 itemizing, no credits, no IT-2105 estimates. `tie_out` has no NY withholding category, so a W-2 box 17 for NY has nowhere to land. | **Estimate only.** Wrong for any high earner (recapture) and for every PTET owner (no add-back / IT-653). |
| **NYC** | ~~`nyc_resident` is an UNSUPPORTED capability rule~~ — **RESOLVED (Z1 slice 14).** The rule is deleted and the flag is the city-tax switch; IT-201 lines 47-58 compute the city tax, both Yonkers taxes and the MCTMT. | **Computed.** A NYC user gets a return with no gate, no acknowledgement and no manual note about the city. |
| **NC** | No template, no rule: the generic fallback (`note: "State template not pre-built"`, empty brackets, no `type`) and the same ESTIMATE note. NC is a flat-rate D-400 whose rate steps down on a statutory year schedule, with its own standard deduction and child deduction off federal AGI plus Schedule S additions/subtractions. | **Estimate only**, but the cheapest state to model: one bracket, one deduction table, a short modification list. |
| **WA (Seattle)** | Deliberately NOT in `_NO_INCOME_TAX_STATES` because of the RCW 82.87 capital-gains excise, so a wage-only WA filer gets the ESTIMATE note instead of the affirmative "no return" statement FL gets. If a fetch ever populates `state.brackets`, projection and harvest would tax ORDINARY income — phantom liability. The excise itself is not computed. | **Not modeled.** Understates for a large-LTCG seller (over-statement is impossible; that is the safe direction) and cannot reassure a wage-only filer. |

The cross-cutting blockers matter as much as the per-state ones for this user set:
`moved_states_this_year` (part-year) and `has_nonresident_source_income` both BLOCK, and
NY→FL is the most-audited migration corridor in the country. The design for all of this
already exists — `2026-state-support-ny-fl-wa.md` Epics 0-4 — with the schema
discriminator (Epic 0) shipped in #65 and Epic 1 (FL) done. What follows is that
document's sequencing re-cut against the named users; the stories themselves are not
restated here.

## Part 2c — Evidence from the second review, re-verified on folding

| ID | Observation | Where, and confidence |
|---|---|---|
| E1 | Raw document content reaches CLI stdout. | `agents/lib/parse_utils.py`: `extract_pdf_text` returns page text unchanged and the `extract` / `tables` commands print it. Reproduced with a mocked PDF carrying a synthetic marker; no taxpayer document was used. → V0 |
| E2 | The parse-worker boundary is prompt-level, and self-contradictory. | `tax-parse/SKILL.md` worker template rule 4 forbids reading outside `summaries/`; step 1 nine lines later runs `extract` on a file under `documents/`. `.claude/settings.json` supplies post-tool hooks, not a sandbox. → V6 |
| E3 | The agent assembles intake and computation more than once. | `tax-parse` Steps 5 and 6.5; `tax-compute` and its five references. → V1, V2 |
| E4 | Generation has no machine-checkable "still valid" receipt. | `tax-generate/SKILL.md` never mentions the tie-out; its hard stops cover cache/regime checks only. A package can be generated after a failed gate, or after an input changed. → V8 |
| E5 | Missing preferential-income thresholds silently select ordinary rates. | `compute_scenario` falls to `compute_bracket_tax` when either limit is `None`, and `projection.py` (near line 1353) has the same fall-through without calling `compute_scenario`. `loss_harvest.py` guards this; `scenario.py`, `roth_ladder.py` and `projection.py` do not. Over-states tax on preferential income. → X2 |
| E6 | Rollover accepts non-finite carryforwards and merges loss character. | `rollover.py::_transform_capital_loss`: +inf passes `v > 0`; a NaN in the explicit block is skipped and falls to the legacy `line_16_combined` path, where NaN and -inf pass `raw >= 0` and come out as `nan` / `inf`. `_read_json` is a bare `json.loads`; the `_abs`, `_ftc_unused` and `_ira_basis` transforms pass non-finite values the same way. ST and LT are summed. → X3 |
| E7 | Cache save time stands in for retrieval time. | `tax_law_fetcher.py::save_tax_law` rewrites `_meta.fetched` on every save, so editing one figure re-dates the whole cache. → V4 |
| E8 | Not every runtime writer is atomic. | `scenario.py:524` and `tie_out.py:1745` open their targets with `O_TRUNC` in place — the second is the hard gate's own report; the cache, rollover and calendar writers each hand-roll `mkstemp` + `os.replace`, and `secure_fs` exposes no shared writer. → V10 |
| E9 | CI runs PRs only, on Ubuntu 3.12 and 3.14. | No Windows job, no schedule, no push-to-main run, no model-driven eval. → W6, V3 |
| E10 | Instruction and safeguard weight. | Byte and line counts match Part 2 exactly. → Y5, W2, W3 |

## Part 3 — Backlog

Priorities: **P0** before the next real-document parse; **P1** before the TY2026
season opens; **P2** next quarter; **P3** opportunistic. Size: XS / S / M / L.

### EPIC V — Agent reliability and determinism — **P0 for V0; P1 for V1-V4, V6, V8**

> As the operator, I want the numbers on the return produced by code I can test, with
> the agent asking questions and explaining, not doing arithmetic and transcription.

- **V0 — DONE (#85) (P0, S-M): Mask SSN/ITIN at the extraction boundary.** A new SSN/ITIN-only
  masker — not `secure_fs.redact`, which substitutes `[REDACTED]` and masks any run of
  nine or more digits, so it would erase routing and account numbers and the last four
  digits alike — applied to every string `extract_pdf_text`, `extract_tables` and the
  Excel/CSV readers return, preserving `XXX-XX-{last4}`, and to any exception text that
  quotes document content. Scope is identifiers only: employer names, addresses, EINs,
  routing and account numbers and every box amount must survive unchanged — a test
  feeds each extraction route a synthetic page carrying all of those plus an SSN and
  asserts only the SSN moved. No full identifier is persisted as a fixture. The CLI is
  not the only route into the transcript: the worker template forbids other shell
  commands but not the agent's own file-read tool, and Steps 4 and 6.7 name no
  extractor — V0 closes those by instruction and V6 by enforcement. The Step 5
  redaction sweep stays as the belt-and-suspenders layer it already is. Ship with the
  E2 wording fix: rule 4 of the worker template reads "outside `documents/` (read) and
  `summaries/` (write)".
- **V1 — DONE (#109, #110, #111) (P1, L): Deterministic return orchestrator.** A `return_builder` module under
  `agents/lib` takes profile + tax law + structured parsed inputs and writes
  `result.json`. The skill shrinks to: run it, explain, ask the interview questions,
  re-run. Migrate one form at a time behind V3, starting with 1040 lines 1-24.
- **V2 — DONE (#108) (P1, M): Structured parse output.** Parse workers emit one typed JSON per
  document (form type, boxes, payer); a script derives `parse_ledger.json` and the
  result inputs from those files. Markdown summaries stay for humans. Second-read runs
  on the JSON.
- **V3 — DONE (#97, #98, #107, #116, #117, #141) (P1, M): Agent-level eval harness.** Synthetic fixture workspaces (W-2 and 1099
  PDFs generated with reportlab), parse + compute run headless via the agents' batch
  modes, `result.json` diffed against a golden with tolerances. Nightly or on release
  tags, not per PR. **Gates V1 and V2 per slice, not as a whole:** each migrated form
  slice needs an independent worksheet golden (hand-computed, not the old agent's
  output) before it moves; the first model-driven trial starts with the first slice,
  and the cross-agent matrix grows alongside. Cases must include a missing and a
  corrected document, hostile document text, a stale cache, a context interruption
  and a valid zero-income return; record agent version, instruction revision, repeat
  count and cost. Any observed privacy or false-completion failure blocks release.
- **V4 — DONE (#87) (P1, S): Tax-law ingest CLI.** `tax_law_fetcher.py set <dotted.path> <value>
  --source <url> --year <y>` with schema validation, year assertion and sanity ranges
  (brackets monotonic, deduction positive, MFJ ≥ single). The agent stops writing the
  cache JSON by hand. **Retrieval time is per block, not per file (E7):** `_sources`
  already pins block path → URL and `verify_source_pinning` audits it; what is
  missing is WHEN each block was verified, since `save_tax_law` rewrites
  `_meta.fetched` for the whole cache on every save. Add a sibling per-block map of
  retrieval time and applicable period beside the string-valued `_sources` pins —
  not inside them, because `_source_host_authoritative` requires a string and an
  object value would mark every existing cache INCOMPLETE — and make freshness the
  minimum over blocks with `_meta.fetched` as the legacy fallback. An allowlisted
  host certifies the source, never the figure's meaning.
- **V5 — DONE (#103, #104) (P2, S): Headless answers contract.** A schema for `answers.md` (or an
  `answers.json`) plus a `--check-required` script that lists missing required answers
  by stable id before a run starts, instead of a hard stop mid-compute. Persisted
  answers never satisfy a gate that requires a fresh explicit acknowledgement, and the
  engine CLIs converge on one success / blocked / could-not-run exit contract with
  tested JSON output — the freshness report and the gates disagree today.
- **V6 — DONE (#99, #100, #101, #106) (P1, M): Enforced parse-worker boundary.** A `tax-parse-worker` subagent under
  `.claude/agents/` with no WebFetch/WebSearch and a closed tool set, plus the
  equivalent for the sequential path. Acceptance is adversarial: a document whose text
  instructs an outbound request or an unrelated read/write produces neither, through
  the general shell and Python tools included — a deny rule on `curl` and `wget` alone
  does not qualify. Where a host cannot establish isolation, the DELEGATED mode
  refuses and the sequential path AGENTS.md §4 promises every agent still runs in the
  main session under the prompt-level rules, with the summary header stating that the
  boundary was not enforced — Codex, Kimi and Copilot have no closed tool set to give a
  sub-task, and a season-open toolkit that refuses to parse on them is a portability
  regression `docs/contributing/agents-compat.md` must record. Today the "no network"
  rule for parse sub-tasks is prompt-level only.
- **V7 — DONE (#115) (P2, S): Isolate the freshness auditor.** Run it against a schema-validated
  copy of `tax_law_<year>.json` in a temp directory, so the only network-capable agent
  never has the workspace in reach. Acceptance: a sentinel taxpayer file in the
  workspace is unreadable to it, and a free-text cache field cannot carry identity
  data into the copy. Its "read nothing else" scope is prompt-level only.
- **V8 — DONE (#91) (P1, M): Validation receipt bound to current inputs.** Tie-out and the
  capability gate write a receipt naming the document, input, law and result hashes
  plus engine and schema versions and each gate's outcome; `tax-generate` re-validates
  it and refuses a filing package whose inputs changed or whose tie-out failed. A
  valid zero-income return still passes; a draft export, if kept, is marked incomplete
  on page 1. A hash receipt detects staleness — it is not a signature against a
  hostile process. A receipt that fires on a harmless edit teaches the agent to
  reason past it, so the invalidation set is the input set and nothing wider (the
  gates-that-cry-wolf lesson from the CA cache fix, PR #69).
- **V9 — DONE (#92) (P2, M): Maintained PDF renderer.** Replace the per-workspace generated
  `generate_tax_pdf.py` with a tracked renderer and a shared field map that consumes
  validated `result.json`, never embeds the taxpayer's name in source, keeps the
  page-1 limitations manifest and rejects an incompatible schema version. Synthetic
  PDFs for zero-tax, refund, balance-due and long-manifest cases get layout and
  field-presence checks. Retires the "check the generator by hand" rule in AGENTS.md
  §8 and the PII gate's by-name block for that file.
- **V10 — DONE (#93, #102, #139, #140) (P2, M): Atomic writes and resumable state.** Extract one temp-then-replace
  writer into `secure_fs` from the three hand-rolled copies (`tax_law_fetcher`,
  `rollover`, `tax_calendar`) and route every runtime writer through it —
  `scenario.py:524` and `tie_out.py:1745` write in place today, and the second is
  the hard gate's own report, the artifact V8 binds generation to. A restart after
  interruption resumes from validated inputs without duplicating income or mixing
  artifact generations; concurrent runs serialize or refuse.
- **V11 — DONE (#95) (P2, M): Parser resource budgets.** Time, memory, decompression and output
  caps on every adapter — the 100 MB source-file cap bounds neither a decompressed
  workbook nor extracted output. Synthetic compressed-workbook and excessive-page
  cases terminate with a controlled error and no partial record is accepted.

### EPIC W — Engineering hygiene and maintainability — **P2**

- **W1 — DONE**, and **W5 — DONE**: `pyproject.toml` configures ruff and mypy and CI
  runs both; `.python-version` pins 3.12. W4 is what remains of that group.
- **W1 — DONE (#90) (P2, S): `pyproject.toml` with ruff and mypy** (gradual, untyped defs off), both
  in CI. Baseline the existing findings and enforce on changed modules and new
  contracts first; clear the current pyflakes baseline (12 at `9f3bdb7`) in the same
  PR.
- **W2 (P2, M): Split the monoliths.** `tax_computations.py` into a `federal/` package
  by form and a `state/` package by state; `projection.py` likewise. Pure moves, tests
  unchanged.
- **W3 (P3, L): Move the hook state machine to Python.** 3,800 lines of bash tested by
  5,200 lines of Python is the wrong language boundary; the stamp and red-carry-forward
  logic is a state machine bash makes hard to read. Python also lets the hook share
  `secure_fs`. Contracts first, extracted incrementally, exit codes and output
  preserved; a wholesale rewrite is not the acceptance criterion, and the bootstrap
  (a hook that must run before the venv exists) needs a stated answer.
- **W4 — DONE (#90, #118) (P2, S): Suite speed.** A `slow` marker on the hook and preflight modules
  first, then trial pytest-xdist only after checking the shared subprocess, git and
  stamp-file resources the hook tests touch. The suite is I/O-bound at 38% CPU, which
  suggests xdist could roughly halve wall time; demonstrate it on the same host rather
  than promise it. An engine-only run should finish well under a minute.
- **W5 — DONE (#90) (P2, S): Pin the dev interpreter.** `.python-version` (3.12) and a `uv sync`
  recipe. The local venv is 3.14 while the lock targets 3.12, which AGENTS.md §7
  itself warns about.
- **W6 — DONE (#105) (P2, S): CI gaps.** A scheduled weekly run, a push-to-main run, coverage upload,
  and a `windows-latest` job for `secure_fs` and `run.sh` — Windows behavior is claimed
  in 27 places and exercised nowhere. Each trigger gets its own PII-scan range (the
  `pull_request` base/head variables are empty on push and schedule), a pinned dev-tool
  lock covers pytest, and the model-driven evals of V3 run on their own schedule.
- **W7 (P3, M): ADRs.** Move multi-paragraph "why" comments into `docs/adr/` and leave
  a one-line rule plus a link in code. The narrative style is valuable, but preflight
  carries ~890 comment lines for an existence check.
- **W8 (P3, S): Renumber skill steps** (0.5, 0.7, 6.5, 6.7, 6.8, 12.7) in a docs-only
  PR. Fractional numbering is accretion made visible.
- **W9 (P3, S): Trim `_comment_*` keys** in the profile template.
  `foreign_holdings_detail._comment` is a ~2,000-character paragraph inside JSON; it
  belongs in a schema doc.

### EPIC X — Tax correctness, known and unfixed — **P1 for X1-X3, P3 for the rest**

Carried from the memory of prior reviews and re-confirmed by the second review
(Part 2c E5, E6). They are small, they are directional, and they touch CA filers, so
they run ahead of the orchestrator rather than waiting behind it.

- **X1 — DONE (#89) (P1, M):** MFS and HOH capital-gains brackets collapse to single in the schema
  and in compute, projection and scenario. A real directional error, not a gap. Verify
  the required distinctions against the year's instructions, fetch every
  status-specific value, and replace the divergent `_binary_status_key` mappings with
  one shared accessor tested per status, old caches included.
- **X2 — DONE (#86) (P1, S):** One shared accessor for the status-keyed capital-gains limits that
  raises when preferential income exists and a limit is `None`, used by
  `compute_scenario` AND by `projection.py`'s own worksheet branch, which never calls
  `compute_scenario`. Today only `loss_harvest.py` guards; `scenario.py`,
  `roth_ladder.py` and `projection.py` fall through to ordinary rates and over-state
  the tax. An ordinary-income-only case stays usable. X1 and X2 share the root cause
  (four divergent read sites), so X1 lands on the same accessor.
- **X3 — DONE (#88) (P1, S):** Capital-loss carryforward rolls the combined net only; split short
  and long term through profile, compute and the next rollover. Apply AGENTS.md §8's
  `math.isfinite` rule at `rollover._read_json` (a bare `json.loads` today) so NaN,
  ±Infinity and booleans never reach any transform — `_transform_capital_loss`'s
  legacy `line_16_combined` path turns NaN into `nan` and -inf into `inf`, and the
  `_abs`, `_ftc_unused` and `_ira_basis` transforms pass them the same way. A legacy
  combined-only figure requires an explicit resolution rather than a guessed split,
  and an explicit zero is distinguished from an absent block.
- **X4 — DONE (#110, #142) (P3, S):** Add `prior_overpayment_applied` (line 36) to the result
  template and generator. V1 (#110) added the FIELD; #142 made it a CARRY — the profile's
  `prior_year.estimated_payments_applied` was written by nothing and read by nothing, so
  last year's line 36 never reached this year's line 26 and the taxpayer was billed for
  money they had already paid.
- **X5 — DONE (#143) (P3, S):** Extend the safe-harbor "no dollar figure in prose" lint to
  `tax-setup` and `tax-parse`, which still quote indexed figures inline. **The premise
  held and was worse than stated**: `tax-review` — not on X5's list — told the agent the
  SALT cap was $10,000, three times, while `ptet._salt_cap` had been returning OBBBA's
  $40,000 since PR #22. **`tax-compute` is NOT covered and that is a named gap**: it
  quotes eighteen figures, most beside the cache key they are fetched from, and each
  needs its own judgement about instruction versus illustration. It is the next slice.
- **X6 (P3, S):** Annual statutory re-verification — **= T3** in the 08-12 backlog.
- **X7 (P3, S):** The O3 planning-tool carry-forwards — **= U1** in the 08-12 backlog:
  deduction-only marginal probe for `_est_marginal_fed`; scenario-keystone
  §63(f)/SE-tips threading; `/tax-harvest` §1091 basis-shift modeling. The first two
  are absorbed by V1 if the orchestrator exposes a deduction knob on
  `compute_scenario`; sequence them after it rather than as separate work.

### EPIC Y — Distribution and product

- **Y1 (P1, XS): Publish the repository and enable Discussions — = R6.** Everything
  else in Epic R is done; this is the only owner-blocked item.
- **Y2 (P1, S): Season-open runbook when TY2026 figures publish — = T1**, after
  re-running the subagent replay.
- **Y3 (P2, M): Package `agents/lib`** as an installable distribution with console
  entry points. `tax-engine preflight` replaces `sh agents/lib/run.sh
  agents/lib/preflight.py`, and most of preflight's 2,700 lines exist to compensate for
  not being a package.
- **Y4 — DONE (#112) (P2, S): Workspace outside the clone.** A `TAX_WORKSPACE` root every skill and
  CLI honors, so the dev checkout and the live return stop sharing a directory. The
  08-12 review accepted that risk as permanent (R2's standing controls); a
  one-variable fix retires the risk instead of policing it.
- **Y5 — DONE (#113) (P2, S): Instruction-file budget.** AGENTS.md + CLAUDE.md is 39 KB loaded into
  every Claude Code session before any skill. Move the hook and Codex-loop internals to
  `docs/contributing/` and keep the two files to rules only.
- **Y6 — DONE (#114, #119) (P3, S): Pre-push hook runs the PII scan and passes**, instead of blocking, so
  the documented push command no longer needs `--no-verify`. A normal push scans every
  outgoing commit, fails closed, and passes a clean branch. R2 names this sharp edge
  ("commit-time gating is the only local gate that actually runs"); this closes it.

- **Y7 (P1, M): Make the PII gate prove it READ what it reports as checked — DONE.**
  Y6's third adversarial pass found the gate's verdict depended on variables it
  inherited from the calling shell, and pinning each by name was an ENUMERATION WITH NO
  STOPPING RULE — three of the script's documented defects were the same shape. Three
  positive assertions are now the first line of defence; the pins stay behind them.
  1. **The per-file diff must agree with the file list that produced the path.** Two
     halves covering two classes, neither subsuming the other: `--numstat` must yield
     exactly ONE row (the file list carries no pathspec and the per-file diff does, so
     this is what catches an emptied restriction), and the patch must yield exactly as
     many added lines as the row counts (this shares the pathspec, so what it catches is
     a patch REWRITTEN rather than emptied — an external driver, textconv,
     `diff.submodule=log`). Both taken at `-U0 --no-patch`, which is what makes it an
     equality: at different context widths the two are not one edit script. With the
     pathspec pin REMOVED, `GIT_LITERAL_PATHSPECS=1` now blocks on the missing row.
  2. **Every recorded parent of a scanned commit must be inspected or excluded** — read
     from the commit object. With the graft pin REMOVED, a grafted-away commit now
     blocks, including one hidden behind a merge's SECOND parent. It does not retire
     `GIT_NO_REPLACE_OBJECTS`: `git cat-file commit` honours a replacement mapping too,
     so that pin is what makes the object read authoritative.
  3. **The work tree must belong to the repository the scan was asked about** — the
     absolute git directory, read before the `cd` to the root and again after it, with a
     caller-set `GIT_DIR` re-exported absolute first. This closes the `GIT_WORK_TREE`
     redirection, and the earlier note here overstated that hole: it reproduces against
     a DIRECT invocation and NOT against `git commit`, which exports `GIT_DIR` to its
     hooks. Staged mode now states its coverage and the pre-commit hook matches it
     against its own enumeration of the index — captured, so a clean commit stays
     silent. That is the backstop, not the fix.

  Every assertion was validated against the cry-wolf direction before being trusted, and
  the review found two that were real: `diff.submodule=log` (numstat counts a line the
  patch does not carry — fixed by pinning `--submodule=short`, a display preference
  disabling a security gate for the third time) and a relative `GIT_DIR` seen across the
  `cd`. Clean over the repository's whole history: 127 commits, 1,472 files, and the two
  diffs agreed on all 1,445 (commit, file) pairs.

  Also discharged: the test binding the hook's env-pin set and the coverage-line format
  against the scanner's, so the hand-mirrored copies cannot drift.
  `GIT_SHALLOW_FILE=/dev/null` is still NOT a safe pin — it would make a legitimately
  shallow CI clone walk into missing objects — and assertion 2 covers it without one.
  STILL OPEN: the Windows CI job runs only `test_secure_fs.py` and `test_host_guard.py`,
  so none of this has Windows coverage.

### EPIC Z — State coverage for the named next users — **P2 for FL and NY/NYC, P3 for NC and WA**

> As a NYC taxpayer, I want the same straightforward experience a CA or FL taxpayer
> gets — interview, fetch, parse, compute, PDF, verify, review — with the city tax
> simply part of my return; as a Seattle or NC taxpayer, I want the toolkit to say what
> my state actually owes.

**Definition of done for NYC (owner ruling 2026-09-05): parity with CA, not an
acknowledgeable block.** A NYC user must never see the capability gate, never be asked
to sign a limitation, and never be handed a "bracket estimate". Concretely, every
surface where CA is wired in has an NY/NYC twin:

| Surface | CA today | NYC at done |
|---|---|---|
| Capability gate | no rule | `nyc_resident` rule DELETED, flag re-purposed as the city-tax switch — **SHIPPED, slice 14** |
| `/tax-setup` interview | CA questions | NY residency + NYC/Yonkers residency + 414(h)/IRC-125 prompts |
| `tax_law_<year>.json` | `state.CA` block | `state.NY` block with year-gated tables, recapture, NYC and Yonkers brackets, pinned to tax.ny.gov |
| `/tax-parse` + ledger | `state_withholding_ca` | `state_withholding_ny`, `city_withholding_nyc` (W-2 box 19/20), box 14 414(h)/IRC-125 |
| `/tax-compute` Step 10 | Form 540 | IT-201 including lines 47-58 (NYC tax, Yonkers, MCTMT), IT-225, IT-196 |
| `result.json` | `california_540` | `new_york_it201` with a `nyc` sub-block, in `STATE_SECTION_KEYS` and `STATE_MOVER_FIELDS` |
| `/tax-generate` PDF | 540 page | IT-201 page with the city lines, filing package entry |
| `/tax-verify` | 540 line map | IT-201 line map incl. NYC lines against the software return |
| `/tax-review` | CA section | NY section: residency tests, 184-day rule, STAR/homestead conflicts |
| `/tax-calendar`, `/tax-project`, `/tax-withholding` | 540-ES | IT-2105 vouchers (one voucher covers NYS+NYC+Yonkers+MCTMT), NY safe harbor on NYAGI |
| `/tax-harvest`, `/tax-scenario`, `/tax-roth-convert` | CA marginal | NY+NYC combined marginal, recapture-band caveat |
| `/tax-ptet` | CA/NY/NJ entity side | plus owner-side IT-653 credit, IT-225 add-back, NYC PTET |
| `/tax-rollover`, `/tax-amend`, YoY diff | CA fields | NY fields, IT-201-X three-column |
| Tests | CA hand-computed worksheets | IT-201 worksheet-pinned expectations, NYC recapture and cliff cases, playbook doc-sync tests |
| Freshness auditor | audits `state.CA` | audits `state.NY` incl. the city tables |

Acceptance: a synthetic NYC fixture workspace runs the full pipeline with no gate, no
acknowledgement, no manual note about the city, and `/tax-verify` ties to a
software-prepared IT-201 within $1 on every line including 47-58. It is the second
eval-harness case, after V3's CA fixture. Z1-Z3 are one epic with three milestones,
not three optional stories, and they ship together at P2: a NYC return without
IT-2105 events, the NY withholding layer and the credits is not the CA experience.

Story detail lives in `2026-state-support-ny-fl-wa.md`; the numbers there are its
epic.story ids. Every story here follows `docs/contributing/state-module-playbook.md`.

- **Z1-Z3 — SPEC WRITTEN 2026-09-10**, `docs/superpowers/specs/2026-09-10-ny-state-module-design.md`,
  from the playbook's step-0 questionnaire. Live-verified: NY starts from federal AGI;
  IT-196 itemizing is INDEPENDENT of the federal election and runs on PRE-TCJA federal
  rules; §615 imposes two high-AGI reductions plus a separate charitable limitation; and
  **§601(d-1)'s tax-table-benefit recapture means plain bracket math UNDERSTATES NY tax**
  above a threshold — the playbook's canonical trap, so the recapture ships WITH the rate
  schedules rather than after them. Yonkers is in scope and has no flag today. Eight
  OPEN rows remain, each naming the source to consult rather than guessing; eight slices
  proposed. No indexed figure appears in the spec — §2 applies to a design document too.

  **SLICE 1 SHIPPED**: the `state.NY` block is a real skeleton — brackets keyed PER
  STATUS (§601(a)/(b)/(c) are three schedules), a key for the §601(d-1) recapture and
  three separate keys for §615's two reductions and its charitable limit, NYC and
  Yonkers with `applies` NULL so "no city tax" and "not fetched" stay different
  answers, and both payroll caps fetched because the CA scar is a wage ceiling that
  DISAPPEARED mid-decade. `conforms_to_federal` read True and is now False. A third
  keying trap surfaced: NY's standard deduction has SIX rows, splitting a single filer
  on whether they can be claimed as a dependent. Twelve mutations, twelve reds.

  **SLICE 2 SHIPPED**: `compute_ny_tax` — three methods, not one, selected on BOTH
  NYAGI and taxable income. New York's TAX TABLE is not a restatement of its schedule
  (it is the schedule at each row's MIDPOINT, rounded), the row widths are not constant,
  and the first draft used the schedule everywhere — worth up to two dollars, BOTH ways,
  against a one-dollar bar. The schedule needs each band's published cumulative base and
  now cross-checks it against the sum beneath. The form rounds the phase-in FRACTION and
  nothing else. `tax` is the whole-dollar line 39, half-up, because Python's `round` is
  half-even. A negative New York AGI is an ORDINARY return and the first draft refused
  it. Every expected value hand-computed from IT-201-I (2025) pp.32-38; twenty-two
  mutations across the two rounds, twenty-two reds.

  **SLICE 3 SHIPPED**: `compute_ny_agi` (lines 19-33) with the IT-225 modifications and
  the pension exclusion. The modification DIRECTION is cross-checked against the code
  prefix rather than inferred, and a negative amount refuses rather than flipping sides
  — a subtraction filed as an addition over-states the tax and an addition filed as a
  subtraction under-states it. The codes NY disallows on a resident return are fetched;
  unfetched, the totals still compute and say loudly that eligibility was not checked.
  The pension exclusion is PER TAXPAYER and does not pool, refuses to assume
  entitlement, and refuses a government pension (fully excluded on its own line —
  capping one would silently limit it). NYAGI may be NEGATIVE. Seventeen mutations,
  seventeen reds — two of which only went red after clearing `__pycache__`, the
  same-length mutate-and-restore trap the memory records.

  **SLICE 4 SHIPPED**: `compute_ny_itemized` — IT-196 lines 46-49. FOUR regimes by
  NYAGI, and the top two REPLACE the deduction with a share of the charitable
  contributions rather than reducing it. The second reduction takes the whole first
  quarter and then phases a second in on top. The first threshold is per status while
  the gate into the worksheet is not. Fifteen mutations, fifteen reds — three of them
  only after writing cases that reach a cap no ordinary figure reaches, including one
  that can only bind if two separately fetched figures diverge. Line 45 itself is NOT
  in this slice and is named as a non-goal: pre-TCJA federal rules, so the 2%
  miscellaneous floor is live again and a federal Schedule A total is no substitute.

  **SLICE 5 SHIPPED**: the household credit and the credit ORDERING (lines 39-46),
  which closes the spec's open row on ordering. Nonrefundable credits are summed then
  capped at the tax and the excess is LOST — so no ordering among them matters — but
  **line 45 is added AFTER the cap**, so a credit cannot offset it and netting first
  would under-state the return by the whole of line 45. The household credit is keyed on
  FEDERAL AGI (the only threshold here that is), is BARRED for a dependent of another
  taxpayer, and groups the statuses three ways that are not the §601 grouping — MFS
  pairs with single for the schedule and gets half the credit here. Ten mutations, ten
  reds. The other five credits, and which are REFUNDABLE, remain open.

  **SLICE 6 SHIPPED**: `compute_ny_settlement` (lines 63-82), and the refundability
  question is closed — **every remaining NY credit is a PAYMENT**, not a credit against
  the tax, so it can produce a refund with no tax to offset. The credit names are a
  CLOSED SET so a nonrefundable one cannot be smuggled in and refunded. Two credits that
  get missed are named: the noncustodial parent EIC and the NYC EIC. **The penalty is
  INSIDE the amount owed** and reduces an overpayment instead of being billed — the same
  shape a review caught on federal 1040 line 38. The college tuition CREDIT and
  DEDUCTION are mutually exclusive across two functions, so neither sees the other
  without being told. Ten mutations, ten reds. Open: the AMOUNT of each credit
  (IT-213/216/215/214/272 each have their own worksheet).

  **SLICE 7 SHIPPED — this is Z2**: NYC, Yonkers and the MCTMT (lines 47-58). NYC
  taxable income is the STATE's except for a Charitable Gifts Trust add-back, and a
  joint return with only ONE city resident REFUSES rather than taxing the household as
  residents. Two city credit caps, and **line 58 adds the MCTMT and both Yonkers taxes
  AFTER both** — the line-45 pattern a second time. The MCTMT threshold is a CLIFF
  applied PER ZONE: the instructions' own example exempts $40,000 in one zone and taxes
  the whole $70,000 in the other. The NYC household credit REUSES the state function —
  tables 4/5/6 to 1/2/3, verified against the instructions rather than assumed. Ten
  mutations, ten reds; one survived until a case used the second of the two caps.

  **SLICE 8 SHIPPED — this is Z3's owner side**: `compute_ny_ptet_owner` and
  `compute_ny_required_annual_payment`. The PTET credit and its IT-225 add-back are an
  exact PAIR — A-219 is "the amount of the credit claimed on IT-653 LINE 1" and A-222
  the amount on LINE 2, so claiming the credit without the addition under-states NY tax
  by the whole of the credit, and adding both back under one code is a different return.
  The credit is REFUNDABLE. The required annual payment is the LESSER floor in equal
  quarters, with the higher prior-year share keyed to NEW YORK AGI; a prior-year tax
  without its NYAGI refuses. Eleven mutations, eleven reds.

  **SLICE 9 SHIPPED — a shipped crash, found on the way into the registrations**:
  `state.brackets` has TWO shapes. California's is a flat list of bands; New York's is
  a dict keyed by §601 rate SCHEDULE, because three filing statuses share one schedule
  and none can be read off the bands. A non-empty dict is TRUTHY, so every
  `state.get("brackets") or []` gate waved a New York cache into code that iterates a
  list of dicts: `AttributeError: 'str' object has no attribute 'keys'`, from inside
  bracket math, naming neither the cache nor a remedy. Both `/tax-project` and
  `/tax-harvest` would have failed that way for every New York taxpayer.
  `tax_computations.state_brackets_for_status` is now the one reader that knows both
  shapes, and a STRUCTURAL sweep keeps it the one: asking `if state.get("brackets")`
  for a boolean stays fine anywhere, BINDING the value to a name is the accessor's job,
  because a bound name is one something later iterates. Two consequences beyond the
  crash: `/tax-project` routes a cache that declares a benefit recapture through
  `compute_ny_tax` rather than plain bracket math, which under-states New York tax by
  the whole benefit of the graduated schedule above the entry threshold; and
  `/tax-harvest` says out loud that its state rate is a FLOOR there, since a harvested
  dollar inside the recapture band saves more than the schedule's marginal rate.
  `_state_marginal_rate` took a `filing_status` it had never read — this is its first
  use. Seven mutations, seven reds.

  **SLICE 10 SHIPPED — the result section, eight slices later than the spec said**:
  `new_york_it201`, with an `nyc` sub-block, plus `/tax-compute`'s New York branch
  (`references/state-return-ny.md`), `projection.STATE_SECTION_KEYS` and the
  computed-evidence pair. The spec said the section ships WITH the compute functions
  "because a section key registered before anything computes into it is a scaffold that
  reads as coverage" — the functions shipped first and the section came last, which is
  exactly the drift that rule was written to prevent. The assertion that keeps the names
  honest is that **every line key the engine emits has somewhere in the template to go**,
  collected by RUNNING the functions rather than reading the source, because three of the
  four line tables are expanded into f-strings at call time. It found eight mismatches on
  its first run and one real defect: `compute_mctmt` and `compute_city_and_yonkers_total`
  spelled line 54e two different ways, so a reader tying out that line had to know which
  function produced the dict. Line 59 ships **null, not 0** — sales and use tax is
  reported ON the income tax return and changes the amount owed, and a 0 default asserts
  an answer nobody gave. `_ca_section_computed` and `_ny_section_computed` are now thin
  calls on one `_section_computed` that takes the field list as an argument, because only
  the caller knows which of its fields a PARSER can fill. Five mutations, five reds; two
  of them needed a purpose-written case — a renamed template field, and a truthiness test
  that accepted `True`, `NaN` and a string as a computed deduction.

  **SLICE 11 SHIPPED — the two registrations that had to be restructured first**:
  `yoy_diff.STATE_MOVER_FIELDS` is keyed per SECTION rather than being a flat CA-only
  list gated on `california_540`, which is correct for one modeled state and silently
  wrong for two — appending New York's paths would have gated NEW YORK's movers on
  CALIFORNIA's sections. New York carries a FOURTH row California has no counterpart
  for, line 58's city/Yonkers/MCTMT total: a City resident's year-over-year move is
  invisible in the state figures and is often the larger one. The state total is **line
  46, not line 39** — line 39 is the tax before the credits and before line 45's other
  New York State taxes. A taxpayer who moved between two modeled states gets no state
  movers at all, which is right: a delta between two different states' tax is not a
  movement in anything.

  `tie_out` gains `state_withholding_ny` AND `city_withholding_nyc` — **both halves**,
  each with its row in `/tax-parse`'s step-6.5 table and a test that keeps them paired,
  because a registered-but-never-emitted category is dead protection that makes the gate
  pass vacuously. TWO categories rather than one because they are two LINES and two
  refunds: folding the city amount into line 72 still ties, and leaves the city
  withholding unclaimed on a return that owes city tax. Per STATE like
  `state_withholding_ca`, never a generic `state_withholding`. **Yonkers withholding
  (line 74) has no category**, and the parse table SAYS so rather than leaving a reader
  to assume a zero. Four mutations, four reds.

  **SLICE 12 SHIPPED — the New York withholding true-up**: `/tax-withholding` was federal
  plus California and nothing else, so a New York taxpayer got the federal report and a
  caveat. The target is **IT-201 line 60, not line 46** — one Form IT-2105 voucher carries
  the State tax, the New York City resident tax, the Yonkers surcharge and the MCTMT
  together, so a requirement built on the state total alone under-states a City resident's
  by the whole of their city tax, and a City resident is the population this module exists
  for. The §685(c) shares and thresholds are FETCHED and keyed to **New York** AGI, not
  federal, so the module reads `tax_law_{year}.json` for the first time; an ABSENT cache
  sets `ny_cache_missing` and the report SAYS the cache was missing, because a report that
  simply omits the block reads as "no New York estimated tax is due". Four EQUAL quarters,
  never California's 30/40/0/30 — which would tell a New York filer to skip September. A
  prior-year tax without its NYAGI drops the PAIR rather than assuming the lower share:
  the engine refuses it, and dropping both leaves the current-year share, which
  over-states. `ytd.state_withholding` means ALL New York withholding — boxes 17 and 19
  plus Yonkers — because that is what the one voucher is measured against, and the skill
  says so where the figure is entered. **Left OPEN and named**: New York stays on
  `next_business_day_state`'s weekends-only degrade, so a printed IT-2105 date can be a
  day or two before the one New York observes — safe for a payment, wrong for a
  missed-deadline warning; the calendar note says so on the schedule rather than leaving
  the reader to find out. Six mutations, six reds; one needed a purpose-written case,
  because the two rollover rules agree on every date the first test happened to print.

  **SLICE 13 SHIPPED — the calendar, the extension, and two gaps named rather than
  guessed**: four **IT-2105** events for a New York resident, FOUR EQUAL quarters
  **including September** — the sibling California events say there is none, and a reader
  who has seen that rule must not carry it across — each description saying that ONE
  VOUCHER covers the State tax, the City tax, the Yonkers surcharge and the MCTMT
  together.

  **`/tax-extend`'s row 9 answer is the OPPOSITE of California's, and it is a trap.**
  Form IT-370-I, read directly: *"Do not submit a copy of the federal extension form. We
  will not accept it in place of Form IT-370 and your due date will not be extended."* A
  taxpayer who extends federally and assumes New York follows has filed LATE — 5% of the
  tax due per month, up to 25%. Payment is a **condition** of the extension rather than
  merely a way to avoid interest (*"you must make full payment of the properly estimated
  tax balances due"*), where federal Form 4868 is valid with no payment at all; and an
  IT-370 filed after the deadline *"will not be accepted"* at all. The estimate is IT-201
  **line 80**, because the 10% reasonable-cause presumption is measured against the same
  four taxes line 60 adds up. One IT-370 also extends Form Y-203.

  **Two registrations are NAMED rather than shipped, and the reasons are different.**
  `retirement_dashboard`: New York is in none of the three non-conforming sets, and the
  module now says that is NOT a conformity finding — absence from them makes the dashboard
  CLAIM state savings, the over-stating-a-BENEFIT direction, so it is not the safe default
  an absent figure usually is. The sources are named. `tax-law-freshness-auditor`: its
  definition is **digest-pinned to a passed acceptance**, and a definition edit needs a
  replay from a FRESH session, which a same-session replay cannot be. So the New York
  keying gotchas went to `docs/contributing/tax-law-cache.md`, which the auditor is
  already told to work from; the definition edit folds into Y2. Four mutations, four reds;
  one needed a purpose-written case, because TY2026's dates do not separate New York's
  rollover rule from the federal one at all.

  **SLICE 14 SHIPPED — Z1 IS DONE, and the depth note was written last of all**:
  the `nyc_resident` UNSUPPORTED rule is **DELETED**. A New York City taxpayer no longer
  sees the capability gate, which is the owner's own definition of done — and a rule that
  blocks a return the engine can now produce is a gate crying wolf. The flag STAYS
  required-present, and that matters MORE than it did, not less: a missing answer once
  blocked the return loudly and would now silently omit the city tax from a return that
  owes it, which is the under-stating direction.

  Yonkers got **two** flags, not one. The resident surcharge (line 55) and the
  nonresident earnings tax (line 56) are two different taxes with two different
  populations — a taxpayer can owe either without the other — so one question cannot
  stand for both, and both are required-present for the same reason.

  `/tax-verify` gained an **IT-201 line map** with three refusals beside it: a line-46
  match is NOT a New York tie-out (lines 47-58 are a separate stack and for a City
  resident often the larger half — a return can agree on line 46 to the cent and be
  thousands wrong on line 60); line 39 is not the state tax; and a null line 59 is
  UNVERIFIED, never agreement with a software zero. Every mapped path is pinned against
  the template by test.

  **The depth note comes last because it is the sentence a reader uses to decide how far
  to trust the return.** Every clause names something that computes. What it does NOT
  claim is as load-bearing: each refundable credit's AMOUNT, IT-196 lines 1-45 and IT-201
  lines 44/45 are INPUTS, line 59 is ASKED rather than assumed zero, and IT-203 is not
  modeled. A test asserts every form the note calls an input is named as one in the
  compute reference too. Five mutations, five reds.

  **A note carried here since slice 1 was STALE and is corrected rather than re-fixed**:
  the NY PTET Mar 15/16 next-business-day defect from PR #22's deferred list is already
  fixed — `_election_deadline` returns March 16, 2026 via
  `irs_dates.next_business_day_state`, and `tax_calendar.py` uses the same helper.
  Reproduced before believing it, which is the rule applied to my own notes.

- **Z1 — DONE (#123, #124-#137) (P2, L): New York IT-201 resident return** — Epic 3.1-3.4 and 3.10: the
  year-gated bracket tables and standard deductions, `compute_ny_tax` with tax-benefit
  recapture, the IT-225 modification set (414(h), IRC-125, PTET add-back, pension
  exclusion, Social Security subtraction), decoupled IT-196 itemizing, W-2 box 14
  extraction. Plus the registrations the playbook lists: a `new_york_it201` result
  section, a `state_withholding_ny` ledger category in `tie_out.CATEGORY_TARGETS`, PDF
  page, `STATE_SECTION_KEYS`, `STATE_MOVER_FIELDS`.
- **Z2 — DONE (#130) (P2, M): NYC resident tax and Yonkers** — Epic 3.5. Retires the `nyc_resident`
  UNSUPPORTED rule; until Z1 lands it cannot ship, because the city tax is computed on
  NY taxable income. Z1+Z2 turn the BLOCK into a return; Z3 turns the return into the
  CA experience.
- **Z3 — DONE (#131) (P2, M): NY credits tier 1, estimates and PTET reconciliation** — Epic 3.6-3.9,
  3.11: Empire State child credit, NY EITC, IT-2105 events in the calendar, the NY
  layer in `/tax-withholding`, IT-653 credit, NYC PTET in `/tax-ptet`, planning-skill
  NY marginal rates.
- **Z4 (P3, S): North Carolina D-400** — not in the existing roadmap; new. A
  `graduated_income` module with a single bracket per year (the flat rate is on a
  statutory step-down schedule, so it must be year-gated and FETCHED, never inlined),
  NC standard deduction and child deduction, the short Schedule S list (state/local
  bond interest, Social Security subtraction, no §179 conformity gap for individuals),
  NC-40 estimates. Smallest possible state module; a good first contribution once
  public, and a proving ground for the playbook on a flat-tax regime.
- **Z5 (P3, M): Washington capital-gains excise** — Epic 2.1-2.3 and 2.6: the
  `state.wa_capital_gains` block with `state.brackets` left EMPTY, `compute_wa_capital_gains`
  off Schedule D net LONG-TERM gain with the combined deduction and the two-tier rate, a
  `washington_ltcg_excise` result section, the April filing event. Also the affirmative
  wage-only statement: a WA filer with no LT gain above the deduction owes nothing and
  should be told so, which today only FL gets.
- **Z6 (P3, S): WA harvest and crypto synergy, WFTC reminder** — Epic 2.4, 2.5, 2.7.
- **Z7 (P2, S): Residency and multistate guardrails — DONE.** Residency is now an
  ASSERTED input: `taxpayer_profile.json` carries a `residency` block with
  `domicile_state` and per-state COUNTS for days present and abode months, validated at
  the parse boundary the way `address.state` already was — a misspelled key used to make
  the counts for the state that matters look ABSENT, which is the dangerous direction.
  The `/tax-setup` interview asks for the numbers and states the test, and
  **the engine applies New York's threshold to the counts** rather than leaving the
  taxpayer to do the arithmetic: collecting numbers instead of a boolean buys nothing if
  the line is then drawn in somebody's head. `statutory_residency_second_state` BLOCKS —
  two resident states tax the same income and the credit between them is not modeled —
  and it is distinct from `part_year_residency` (a mid-year MOVE) and
  `nonresident_source_income` (income SOURCED elsewhere). The mover checklist moved to
  `/tax-review` step 3.8, on the MAIN path: it was inside the `type: "none"` branch, so
  a taxpayer leaving New York for any income-tax state reached none of it.

  **The review caught this PR trying to ship a tax regression, and the correction is
  the point of the entry.** The first draft "fixed" the shipped `>10 months` abode
  period to eleven, citing tax.ny.gov's bulletin, and wrote a test pinning eleven. The
  Department's own Nonresident Audit Guidelines (Dec 2021, pp. 49 and 51) say Audit
  Division policy moved to **more than 10 months beginning with tax year 2022**, while
  the website still says eleven. The original author was right and correctly cited; the
  "fix" would have told a taxpayer with a 10½-month abode and 190 New York days that
  New York does not have them, left the flag False so the new gate never fired either,
  and pinned the error with a test. Both figures are now stated, year-gated, with the
  guidelines as the authority.

  Five other corrections from the same pass, each one a gate that would have fired on
  CORRECT data or missed a real one: the day-count and abode exclusions (transit,
  medical confinement, active-duty service, an undergraduate's term-time residence) are
  stated, because every omission only moves the count DOWN; the other state has to
  actually TAX INCOME, or a Florida snowbird gets blocked over a tax that does not
  exist; BOTH prongs are required; New York's numbers are labelled New York's, with
  Massachusetts (no abode-duration prong at all), Connecticut (the entire year) and
  California (no mechanical test whatsoever) named so nobody transplants them; and the
  convenience-of-the-employer rule is a five-state MINORITY, not the general rule —
  stating it as general would have fed `nonresident_source_income`, which blocks, for
  the ordinary remote worker. The NYC question was also address-gated (`If state is
  NY`), which structurally could not reach the taxpayer this epic is about.

  Every residency figure now lives in one sourced, dated table
  (`docs/contributing/residency-guardrails.md`) under a registry that is ONE positive
  assertion — a number with a unit standing near a residency noun — over every skill,
  the engine's rule text and the profile template. The first version was two scopes and
  an eleven-entry exemption mask, i.e. the enumeration-with-no-stopping-rule Y7 had just
  removed from the PII gate: four of its eleven masks were dead on arrival and the
  ten-month figure could be reintroduced in three of the five files that state it with
  every test green. Twelve mutations, twelve reds, no false positives.

  NOT in scope, and still open: residency by DOMICILE is a review finding and not a
  gate — a mover whose change of domicile fails New York's burden passes clean;
  Yonkers has the same parity as NYC in New York's own definitions and has no flag;
  city-level counts have no field; and Z8's part-year/nonresident returns are what an
  acknowledged block hands to a preparer today.

- **Z8 (P3, L): IT-203 part-year and nonresident** — relaxes the single-state
  assumption in `address.state`. Explicitly deferred in the NY roadmap; stays deferred.
  Until then `moved_states_this_year` keeps BLOCKING, which is the correct bias.

## Part 4 — Sequencing

Owner ruling 2026-09-05: the P1s and CA first; FL and NYC second; NC and WA last.
Revised on folding in the second review: contain the exposure and fix the confirmed
defects before any infrastructure, because they are small and they do not depend on
the harness.

1. **V0 with the E2 wording fix** before the next real document is parsed — the only
   P0. **X2** is the next small PR after it; neither waits for an orchestrator or an
   eval harness.
2. **Y1 and V4** — publishing is the owner's action; V4 with per-block provenance is
   what the next season's cache can trust.
3. **X1 and X3** — the remaining confirmed directional defects, scoped against the
   year's instructions.
4. **V3's first CA golden together with V2's document contract**, then **V1 in
   slices** on the CA + federal return, each slice behind its own golden. Do not build
   V1 around the Markdown summaries and migrate inputs twice.
5. **V6, V8 and Y2 before the first real TY2026 return** — the remaining P1s: the
   enforced parse boundary, the validation receipt, and the season-open runbook after
   the subagent replay. V7 rides with V6.
6. **V9** — a filing package is not complete until validation and generation consume
   the same input generation; the first V1 slice can land before V8, but the workflow
   is not whole until V8 and V9 do.
7. **Z7 then Z1-Z3 as one delivery (P2)** — the residency guardrails are prose and
   serve CA/FL/NY movers; NY/NYC builds on the orchestrator once it has landed, and
   the bar is parity with CA, not an unblock. Any FL follow-up sits at the same tier.
8. **Z4 and Z5 (P3)** when those users appear; Z4 is small enough to be a first
   outside contribution.
9. **W1, W4, W5** as a single hygiene PR at any point — W5 is one file AGENTS.md §7
   already asks for. **V5, V10, V11, W6, Y3, Y4** at any point after step 5; W3 only
   with its bootstrap answered.

---

## Part 5 — The questions, and the owner's rulings

Recorded rather than asked mid-flight (owner instruction 2026-09-07: document the
question, skip that task, take the next). **All six were ruled on 2026-09-07**; each is
kept with its question so a later reader can see what was traded away, and none of them
is re-opened by an implementer without the owner.

**Q1 — V3's first golden: whose return shape?** *(gated step 4, and V1/V2 behind it)*
A golden is only as good as the case it covers. The alternatives were a synthetic
CA + federal MFJ return, hand-computed from the worksheets, and the shape of the owner's
own return.
**RULED: the synthetic one.** W-2, 1099-INT/DIV/B, one Schedule D lot, CA 540, computed
from the IRS worksheets and committed as a fixture — buildable and reviewable in the open,
and it needs nothing from the owner. What it cannot catch is a defect that only appears in
a shape nobody put in it; a second golden can be added later without redoing the harness.

**Q2 — where do V3's model-driven runs happen, and on whose budget?** *(gated the model
half of step 4; the deterministic half never waited on it)*
**RULED: locally, on demand.** A script run before a release tag, using credentials
already on the owner's machine. The workflow file is written but left DISABLED, so
enabling a scheduled run later is a one-line change; no API key becomes a repository
secret, and there is no per-night spend. The cost accepted: nobody runs it unless somebody
remembers to, which is why it belongs in the release procedure rather than in a habit.

**Q3 — which agents form V3's cross-agent matrix?** *(gated the matrix, not the harness)*
**RULED: one agent for the first slice, growing when a second is actually in use.** The
matrix is a configuration list, not a rewrite, so widening it later is cheap. The cost
accepted, and stated because it is the reason the question existed: AGENTS.md's
five-agent portability promise stays unexercised, and the agent that runs the eval is one
of the two that wrote the code, so a blind spot shared between them stays invisible.

**Q4 — V6: what counts as "isolation established" on the host?** *(gates V6, step 5)*
The acceptance is adversarial and rules out a deny rule on `curl` and `wget`, because
`python3 -c` reaches the network too — while the worker still has to run
`sh agents/lib/run.sh -m agents.lib.parse_utils`, which needs a shell.
**RULED: the implementer investigates the installed Claude Code** — version, settings
schema, whether per-subagent permissions and a network-off sandbox exist — picks the
strongest mechanism that build actually supports, and writes the residual down: what it
does NOT stop. **If nothing available clears the adversarial bar, V6 ships as the
sequential fallback with the summary header stating that the boundary was not enforced,
rather than as a claim that reads enforced and is not.**

**Q5 — Y2 needs a fresh session and a live fetch, not a decision.** The review-subagent
acceptance replay only tests what it claims from a FRESH session — a definition loads at
session start, so the session that edits one cannot score it — and the runbook puts it
after the year's live fetch.
**RULED: write the procedure, touch nothing.** Both reviewer definitions and
`docs/ops/review-subagent-acceptance.md` stay exactly as they are — certified, and with
their digest pins matching — and the season-open runbook carries one ordered procedure for
the whole thing. Nothing is left formally uncertified in the meantime, which is the state
the digest pin exists to make visible rather than silent.

**Q6 — should the toolkit SHIP a font?** `agents/lib/pdf_render.py` embeds Bitstream Vera,
which reportlab bundles: Latin and Greek. A Cyrillic or CJK name prints as empty boxes,
and page 1 declares how many characters and says to read those fields from `result.json`.
Fixing it means committing a font binary — and the PII gate BLOCKS any binary blob
outright ("content not scannable", failing closed), so it would also mean widening the one
gate this repository guards most carefully.
**RULED: keep the declaration; ship no font.** The cost accepted is real and worth
naming: this toolkit already handles Cyrillic elsewhere (`ensure_ascii=False` exists for a
Russian bank's column headers, and the PFIC fixtures carry RU-domiciled funds), so a
taxpayer whose own name is Cyrillic gets a summary that cannot print it. The loud
declaration is what stands in for that, and this ruling is what stops it being
re-litigated as a bug.
