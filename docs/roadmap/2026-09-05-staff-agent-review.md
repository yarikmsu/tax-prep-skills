# Staff Agent-Developer Review + Backlog — 2026-09-05

> **Status 2026-09-05 — this backlog is PROPOSED, not yet adopted.** It sits beside
> `2026-08-12-product-owner-review.md`, which remains the active product backlog (Epics
> R-U). Items here that duplicate one there (Y1 = R6, Y2 = T1, X6 = T3, X7 = U1) defer
> to that file's status markers; Y4 and Y6 retire the two risks R2 polices. Epic Z
> re-cuts `2026-state-support-ny-fl-wa.md` against the next users the owner named on
> 2026-09-05: CA first, alongside the agent-reliability P1s; FL and NYC P2; NC and
> WA P3. Epic letters continue from U.

Reviewer lens: staff engineer for AI-agent systems — reliability of the agent-mediated
path, evaluation, determinism, tooling and maintainability. Tax-domain correctness was
NOT re-audited; the three prior reviews found the engine sound and this one takes that
as given. Every measurement below was taken on `main` at `20266ed`.

---

## Part 1 — The headline

The engine and the integrity chain are in good shape. The security posture (PII gate,
source pinning, capability gate, tie-out) is unusually rigorous for a project of this
size. The weak spots are on the agent-engineering side, and they are structural:

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
| Lint findings | 5 unused imports | `archive`, `crypto_importer`, `loss_harvest` |

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
| **NYC** | `nyc_resident` is an UNSUPPORTED capability rule: the gate **BLOCKS** the return (exit 2) until the user acknowledges the limitation in writing. Nothing computes the resident tax. | **Blocked.** A NYC user cannot get a computed return from the toolkit today. |
| **NC** | No template, no rule: the generic fallback (`note: "State template not pre-built"`, empty brackets, no `type`) and the same ESTIMATE note. NC is a flat-rate D-400 whose rate steps down on a statutory year schedule, with its own standard deduction and child deduction off federal AGI plus Schedule S additions/subtractions. | **Estimate only**, but the cheapest state to model: one bracket, one deduction table, a short modification list. |
| **WA (Seattle)** | Deliberately NOT in `_NO_INCOME_TAX_STATES` because of the RCW 82.87 capital-gains excise, so a wage-only WA filer gets the ESTIMATE note instead of the affirmative "no return" statement FL gets. If a fetch ever populates `state.brackets`, projection and harvest would tax ORDINARY income — phantom liability. The excise itself is not computed. | **Not modeled.** Understates for a large-LTCG seller (over-statement is impossible; that is the safe direction) and cannot reassure a wage-only filer. |

The cross-cutting blockers matter as much as the per-state ones for this user set:
`moved_states_this_year` (part-year) and `has_nonresident_source_income` both BLOCK, and
NY→FL is the most-audited migration corridor in the country. The design for all of this
already exists — `2026-state-support-ny-fl-wa.md` Epics 0-4 — with the schema
discriminator (Epic 0) shipped in #65 and Epic 1 (FL) done. What follows is that
document's sequencing re-cut against the named users; the stories themselves are not
restated here.

## Part 3 — Backlog

Priorities: **P1** before the TY2026 season opens; **P2** next quarter; **P3**
opportunistic. Size: XS / S / M / L.

### EPIC V — Agent reliability and determinism — **P1**

> As the operator, I want the numbers on the return produced by code I can test, with
> the agent asking questions and explaining, not doing arithmetic and transcription.

- **V1 (P1, L): Deterministic return orchestrator.** A `return_builder` module under
  `agents/lib` takes profile + tax law + structured parsed inputs and writes
  `result.json`. The skill shrinks to: run it, explain, ask the interview questions,
  re-run. Migrate one form at a time behind V3, starting with 1040 lines 1-24.
- **V2 (P1, M): Structured parse output.** Parse workers emit one typed JSON per
  document (form type, boxes, payer); a script derives `parse_ledger.json` and the
  result inputs from those files. Markdown summaries stay for humans. Second-read runs
  on the JSON.
- **V3 (P1, M): Agent-level eval harness.** Synthetic fixture workspaces (W-2 and 1099
  PDFs generated with reportlab), parse + compute run headless via the agents' batch
  modes, `result.json` diffed against a golden with tolerances. Nightly or on release
  tags, not per PR. **Prerequisite for V1 and V2** — it is what makes those refactors
  safe.
- **V4 (P1, S): Tax-law ingest CLI.** `tax_law_fetcher.py set <dotted.path> <value>
  --source <url> --year <y>` with schema validation, year assertion and sanity ranges
  (brackets monotonic, deduction positive, MFJ ≥ single). The agent stops writing the
  cache JSON by hand.
- **V5 (P2, S): Headless answers contract.** A schema for `answers.md` (or an
  `answers.json`) plus a `--check-required` script that lists missing required answers
  before a run starts, instead of a hard stop mid-compute.
- **V6 (P2, S): Enforced parse-worker sandbox.** A `tax-parse-worker` subagent under
  `.claude/agents/` with no WebFetch/WebSearch, and a deny rule for `curl` and `wget`
  in project permissions. Today the "no network" rule for parse sub-tasks is
  prompt-level only.
- **V7 (P2, S): Isolate the freshness auditor.** Run it against a copy of
  `tax_law_<year>.json` in a temp directory, so the only network-capable agent never
  has the workspace in reach. Its "read nothing else" scope is prompt-level only.

### EPIC W — Engineering hygiene and maintainability — **P2**

- **W1 (P2, S): `pyproject.toml` with ruff and mypy** (gradual, untyped defs off), both
  in CI. Fix the five pyflakes findings in the same PR.
- **W2 (P2, M): Split the monoliths.** `tax_computations.py` into a `federal/` package
  by form and a `state/` package by state; `projection.py` likewise. Pure moves, tests
  unchanged.
- **W3 (P2, L): Rewrite the two Claude hooks in Python.** 3,800 lines of bash tested by
  5,200 lines of Python is the wrong language boundary; the stamp and red-carry-forward
  logic is a state machine bash makes hard to read. Python also lets the hook share
  `secure_fs`.
- **W4 (P2, S): Suite speed.** pytest-xdist plus a `slow` marker on the hook and
  preflight modules. The suite is I/O-bound at 38% CPU, so xdist alone should roughly
  halve wall time; an engine-only run should finish well under a minute.
- **W5 (P2, S): Pin the dev interpreter.** `.python-version` (3.12) and a `uv sync`
  recipe. The local venv is 3.14 while the lock targets 3.12, which AGENTS.md §7
  itself warns about.
- **W6 (P2, S): CI gaps.** A scheduled weekly run, a push-to-main run, coverage upload,
  and a `windows-latest` job for `secure_fs` and `run.sh` — Windows behavior is claimed
  in 27 places and exercised nowhere.
- **W7 (P3, M): ADRs.** Move multi-paragraph "why" comments into `docs/adr/` and leave
  a one-line rule plus a link in code. The narrative style is valuable, but preflight
  carries ~890 comment lines for an existence check.
- **W8 (P3, S): Renumber skill steps** (0.5, 0.7, 6.5, 6.7, 6.8, 12.7) in a docs-only
  PR. Fractional numbering is accretion made visible.
- **W9 (P3, S): Trim `_comment_*` keys** in the profile template.
  `foreign_holdings_detail._comment` is a ~2,000-character paragraph inside JSON; it
  belongs in a schema doc.

### EPIC X — Tax correctness, known and unfixed — **P2**

Carried from the memory of prior reviews; none has a user behind it yet, all are
directional.

- **X1 (P2, M):** MFS and HOH capital-gains brackets collapse to single in the schema
  and in compute, projection and scenario. A real directional error, not a gap.
- **X2 (P2, S):** Promote the capital-gains threshold check into `compute_scenario` so
  all three anchored skills fail closed when the limits are unfetched.
- **X3 (P2, S):** Capital-loss carryforward rolls the combined net only; split short
  and long term.
- **X4 (P3, S):** Add `prior_overpayment_applied` (line 36) to the result template and
  generator.
- **X5 (P3, S):** Extend the safe-harbor "no dollar figure in prose" lint to
  `tax-setup` and `tax-parse`, which still quote indexed figures inline.
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
- **Y4 (P2, S): Workspace outside the clone.** A `TAX_WORKSPACE` root every skill and
  CLI honors, so the dev checkout and the live return stop sharing a directory. The
  08-12 review accepted that risk as permanent (R2's standing controls); a
  one-variable fix retires the risk instead of policing it.
- **Y5 (P2, S): Instruction-file budget.** AGENTS.md + CLAUDE.md is 39 KB loaded into
  every Claude Code session before any skill. Move the hook and Codex-loop internals to
  `docs/contributing/` and keep the two files to rules only.
- **Y6 (P3, S): Pre-push hook runs the PII scan and passes**, instead of blocking, so
  the documented push command no longer needs `--no-verify`. R2 names this sharp edge
  ("commit-time gating is the only local gate that actually runs"); this closes it.

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
| Capability gate | no rule | `nyc_resident` rule DELETED, flag re-purposed as the city-tax switch |
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

- **Z1 (P2, L): New York IT-201 resident return** — Epic 3.1-3.4 and 3.10: the
  year-gated bracket tables and standard deductions, `compute_ny_tax` with tax-benefit
  recapture, the IT-225 modification set (414(h), IRC-125, PTET add-back, pension
  exclusion, Social Security subtraction), decoupled IT-196 itemizing, W-2 box 14
  extraction. Plus the registrations the playbook lists: a `new_york_it201` result
  section, a `state_withholding_ny` ledger category in `tie_out.CATEGORY_TARGETS`, PDF
  page, `STATE_SECTION_KEYS`, `STATE_MOVER_FIELDS`.
- **Z2 (P2, M): NYC resident tax and Yonkers** — Epic 3.5. Retires the `nyc_resident`
  UNSUPPORTED rule; until Z1 lands it cannot ship, because the city tax is computed on
  NY taxable income. Z1+Z2 turn the BLOCK into a return; Z3 turns the return into the
  CA experience.
- **Z3 (P2, M): NY credits tier 1, estimates and PTET reconciliation** — Epic 3.6-3.9,
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
- **Z7 (P2, S): Residency and multistate guardrails** — Epic 4.1-4.3: residency as a
  user-asserted input with the NY statutory-resident tests in skill text, the NY→FL
  mover checklist in `/tax-review`, nonresident-nexus interview questions. Content-heavy,
  code-light, and it serves the CA/FL/NY user set before any of Z1-Z5 lands.
- **Z8 (P3, L): IT-203 part-year and nonresident** — relaxes the single-state
  assumption in `address.state`. Explicitly deferred in the NY roadmap; stays deferred.
  Until then `moved_states_this_year` keeps BLOCKING, which is the correct bias.

## Part 4 — Sequencing

Owner ruling 2026-09-05: the P1s and CA first; FL and NYC second; NC and WA last.

1. **Y1 and V4 first** — both small, both change what the next season can trust.
2. **V3 on a CA fixture** — the eval harness is what makes V1 and V2 safe, and CA is
   the supported path it must protect before any state is added.
3. **V1, then V2**, one form at a time behind the harness, on the CA + federal return.
4. **Y2 and X1-X3** before the first real TY2026 return — the P1 season items and the
   directional errors that touch CA filers too.
5. **Z7 then Z1-Z3 as one delivery (P2)** — the residency guardrails are prose and
   serve CA/FL/NY movers; NY/NYC builds on the orchestrator once it has landed, and
   the bar is parity with CA, not an unblock. Any FL follow-up sits at the same tier.
6. **Z4 and Z5 (P3)** when those users appear; Z4 is small enough to be a first
   outside contribution.
7. **W1, W4, W5** as a single hygiene PR at any point.
