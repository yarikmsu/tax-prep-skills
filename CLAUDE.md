# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is
Reusable, taxpayer-agnostic tax preparation toolkit for Claude Code. Provides `/tax-*` skills that guide Claude through the full US federal + state return pipeline — from document parsing through IRS review. Works for any filing status, any state, any tax year.

## Architecture

```
/tax-archive  → Move prior year's files into archive/{year}/ (run at year-end cutover)
      ↓
/tax-setup    → Interview taxpayer, fetch current tax law, create workspace
      ↓        (auto-invokes /tax-rollover if archive/{year-1}/ exists)
/tax-rollover → Pull prior-year carryforwards, identity, dependents, payers from archive/
      ↓
/tax-parse    → Parse source documents (PDFs, XLSX, CSV) via parallel subagents
      ↓
/tax-compute  → Compute all form values from parsed data
      ↓
/tax-generate → Generate summary PDF from result.json
      ↓
/tax-verify   → Cross-reference against tax software return
      ↓
/tax-review   → EA + IRS officer perspective review
      ↓
/tax-fbar     → Prepare FBAR filing document (if applicable)
```

**Calendar artifact (emitted by `/tax-setup`, regeneratable standalone):**
- **`/tax-calendar`** — quarterly reminder calendar: regenerates
  `tax_calendar_{year}.ics` from `taxpayer_profile.json` whenever flags change.
  Normally produced inline by `/tax-setup` step 6.5; this skill exists for
  mid-year flag corrections (opened a foreign account in August, turned 73 in
  October). Always emits 4 federal 1040-ES events; conditionally adds FBAR
  (Apr 15 + Oct 15, in year+1), Dec 31 RMD per age-73+ spouse, and CA/NY/NJ
  PTET windows when K-1 income exists. v1 uses statutory dates only (no
  weekend/holiday rollover). UIDs are stable so re-importing the regenerated
  `.ics` updates existing calendar entries rather than creating duplicates.
  XS effort.

**Optional planning branch (off `/tax-setup`, not part of the linear flow):**
- **`/tax-project`** — mid-year projection: run any time after `/tax-setup` to project
  full-year tax, safe-harbor estimates, and W-4 4(c)/1040-ES guidance from YTD figures.
  Read-only (never mutates `result.json`); re-runnable quarterly. Uses `archive/{year-1}/`
  for the prior-year safe harbor when present, else degrades to the 90%-of-current test.
- **`/tax-withholding`** — withholding true-up: after `/tax-compute`, compares YTD federal
  withholding (projected to year-end at current pace) against the actual computed liability
  in `result.json`, and recommends a W-4 4(c) and/or 1040-ES schedule to hit the
  safe-harbor and zero-balance targets. Read-only; federal only. Uses `archive/{year-1}/`
  for the prior-year safe harbor when present, else degrades to the 90%-of-current test.
- **`/tax-scenario`** — what-if scenario modeling: after `/tax-compute`, forks the computed
  baseline, applies one or more signed income deltas (e.g. `roth_conversion=40000`), and
  reports the marginal cost + AGI/taxable-income/effective-rate movement. The absolute
  scenario tax is *anchored* to the real `result.json` line 24 (only the marginal effect is
  modeled). Writes derived `scenarios/<name>.json` siblings (stackable via `--base`); never
  mutates `result.json`/`taxpayer_profile.json`. Federal only (v1).
- **`/tax-roth-convert`** — Roth conversion ladder: after `/tax-compute`, sweeps conversion
  amounts `$0..--max` in `--step` increments (default $100K / $5K) via the `compute_scenario`
  keystone, charts the marginal cost per dollar, and flags exactly which rung first trips the
  LTCG 0/15/20% breakpoints, the NIIT threshold, and each IRMAA tier. Tax is *anchored* to
  `result.json` line 24; NIIT (the one federal component the keystone omits that a conversion
  can trigger) is layered on outside the keystone via `compute_niit`; IRMAA is a flagged
  Medicare-premium side cost, never folded into the tax total. Writes one
  `scenarios/<name>.json` via the reused scenario `save_scenario` guard; never mutates the
  baseline. Federal only (v1). Degrades to "IRMAA not modeled" when `federal.irmaa.tiers`
  is unfetched. Applies the **Form 8606 pro-rata rule** when `--ira-value` (+ `--ira-basis`,
  default from `prior_year.ira_basis`) are supplied: each rung's taxable conversion is diluted
  by the basis fraction via `compute_form_8606`; degrades to full-taxable otherwise.
- **`/tax-harvest`** — tax-loss harvesting scanner: after `/tax-compute`, reads `holdings.json`
  (open lots + recent_transactions), classifies losing lots as ELIGIBLE or wash-sale-BLOCKED
  (§1091, ±30 days, all accounts in the file pooled), computes the post-harvest Schedule D
  netting against the baseline gains in `result.json` (reusing `scenario._net_capital_gains`
  for character preservation + the $3K/$1.5K-MFS loss cap), and reports federal + state $
  savings. Federal savings is **anchored** to `result.json` line 24 via a `compute_scenario`
  delta (negative `capital_gains` for LT-loss flow, recomputed `other_income` for ST-loss
  flow); state savings is a simplified estimate (state ordinary marginal rate × federal
  taxable-income reduction) under the v1 assumption most states tax cap gains as ordinary
  income, degraded to $0 when `tax_law.state.brackets` is empty. Writes ONE
  `scenarios/<name>.json` via the reused scenario `save_scenario` guard; never mutates the
  baseline. Per-lot `est_value_per_dollar` is a ranking heuristic (ord_mtr for ST losses,
  ltcg_mtr for LT losses) — aggregate savings does the real netting. Same-ticker is the v1
  "substantially identical" proxy; different-CUSIP ETF swaps are NOT flagged (caveat).
- **`/tax-retirement`** — retirement contribution dashboard (backlog #11): mid-year planning
  aid. Reads YTD contribution figures from `projection_inputs.json` (new `retirement_ytd`
  block) + age flags from `taxpayer_profile.json` + statutory limits from `tax_law_<year>.json`,
  and reports per-account (401(k) employee, Traditional IRA, Roth IRA, HSA, Mega Backdoor)
  YTD vs limit + marginal $ saved per remaining dollar. Read-only. Marginal rates are
  user-supplied (`--marginal-fed`, `--marginal-state`, `--magi`); v1 doesn't auto-derive
  brackets. **Key invariants:** age windows are NESTED (60-63 ⊂ 55+ ⊂ 50+) and normalized
  at input — setting only `age_60_63_primary` still grants the IRA/HSA catch-ups
  (the 401(k), IRA, and HSA sections read different age flags, so the implication
  is propagated before any per-account math); same propagation on the spouse side
  (`age_60_63_spouse` → `age_55_spouse`) so a 60-63 spouse still gets the flat $1k
  HSA catch-up (no 60-63 HSA enhancement — that's 401(k)-only); SECURE 2.0 60–63 super catch-up
  SUPERSEDES (does not stack with) the 50+ standard catch-up; SECURE 2.0 §603 mandatory-Roth-catchup (TY2026+) splits
  the 401(k) row into pre-tax base + Roth catch-up when `prior_year_fica_wages` > $150k
  indexed (gated on `tax_year >= 2026` per IRS Notice 2023-62); IRA pool capped by
  `taxable_compensation` per IRC §219(b)(1)(A) (`spouse_taxable_compensation` enables the
  §219(c) MFJ spousal exception); Traditional IRA deduction phases out per MAGI + employer
  plan coverage (full deduction if NEITHER spouse covered); Roth direct contribution phases
  out per MAGI (backdoor available via `/tax-roth-convert`); HSA non-conforming states are
  CA / NJ only (AL conforms since Ala. Code §40-18-15.6, effective 2018-01-01); HSA limit
  assumes FULL-YEAR HDHP coverage — v1 doesn't model the Pub 969 §223(b)(2) eligible-months
  proration or the §223(b)(8) last-month rule, so the HSA row carries a caveat (a mid-year
  HDHP start would otherwise show ~2x the legal capacity); HSA spouse
  catch-up added on a JOINT return only (MFJ) — MFS spouses file separately so the spouse's
  $1k catch-up belongs on the spouse's own return, not the MFS primary's; Single/HoH/QSS have
  no living contributing spouse; Mega Backdoor only
  emitted when `plan_allows_after_tax_401k`, with after-tax cap = `min(total_annual_limit,
  taxable_compensation) − non_catchup_employee_401k − employer_match` — the §415(c)
  annual-additions limit is the LESSER of the dollar limit or 100% of compensation, so the cap
  is bound by `taxable_compensation` (per-participant §415 comp proxy) when provided (falls back
  to the dollar limit + warns it may be overstated when absent); the §414(v) catch-up exclusion
  applies ONLY to age-eligible catch-up dollars (excess deferral above eligible catch-up consumes
  415c space until corrected); MFS filers MUST set `flags.lived_apart_from_spouse_all_year`
  explicitly (hard-error if missing — the flag drives IRA phase-out window per Pub 590-A
  footnote). Federal + state (state piggybacks on federal except for HSA non-conforming +
  Roth/after-tax-no-current-year-deduction + pre-tax retirement-deferral non-conforming states
  via PER-ACCOUNT state-conformity sets — `_RETIREMENT_DEFERRAL_NONCONFORMING_STATES={"PA"}`
  (401(k)/403(b) deferral → `retirement_state_rate`) and `_IRA_DEDUCTION_NONCONFORMING_STATES=
  {"PA","NJ","MA"}` (Traditional IRA deduction → `ira_state_rate`). 401(k) and IRA conformity
  differ by state (NJ/MA disallow the IRA deduction but ALLOW the 401(k) exclusion; PA disallows
  both), so they're gated SEPARATELY; v1 models these, other states caveated not modeled). The §603 catch-up Roth row caveats that per Treas. Reg. §1.414(v)-2(b)(1) the Roth
  requirement is met only to the extent prior designated-Roth deferrals are below the catch-up
  limit — a high earner who already made enough Roth deferrals may make PRE-TAX catch-up, so the
  $0-savings shown may be understated (v1 doesn't track the pre-tax/Roth YTD split). Single-bracket
  assumption for marginal-saved math.
- **`/tax-ptet`** — PTET (pass-through-entity tax) election analyzer (backlog #9): the
  SALT-cap workaround. Reads K-1 entity states from `flags.k1_states` + per-state qualifying
  income (user-supplied via `--income STATE=AMT`) and estimates the FEDERAL savings of electing
  PTET in CA/NY/NJ — the entity deducts the owners' state tax at the entity level, dodging the
  $10K individual SALT cap. Federal savings is **anchored** to `result.json` line 24: the
  entity deduction is a negative `other_income` delta through `compute_scenario` (same contract
  as scenario/roth-convert/harvest), so bracket positioning is priced automatically. CA flat
  9.3%; NY/NJ graduated schedules (applied to the owner's share — `--ptet-rate` overrides for
  multi-owner entity-pool cases). The **forgone-SALT offset** nets out any unused $10K cap
  headroom (default assumes the cap is fully exhausted — the common case; `--salt-room` models
  headroom, valued at the marginal rate); defaulting to exhausted *overstates* savings, so it's
  loudly caveated per the bias-direction taxonomy. State savings = $0 (the PTET credit offsets
  the entity tax for the owner — CA nonrefundable/5-yr carryforward, NJ/NY refundable). Surfaces
  per-state election deadlines reusing the `/tax-calendar` statutory facts (NY's March-15-of-the-
  tax-year election is the trap — can't be made on the return). **Read-only AND writes NO
  artifact** (advisory like a `/tax-review` section — the first planning skill with no
  `scenarios/` output). Backlog #9 wires it into `/tax-review` (step 3.6) and `/tax-project`
  (step 8). Federal-only v1; CA/NY/NJ only (other states surfaced as a "not modeled" note).

**Form 8606 basis tracking (item #7, not a standalone skill):** `compute_form_8606` in
`tax_computations.py` is the pro-rata keystone (Part I worksheet — basis ratio capped at 1.0,
`basis_remaining` line-14 carryforward, `taxable_to_1040` = line 15 + line 18, a
`pro_rata_triggered` trap flag). Basis persists in `taxpayer_profile.json prior_year.ira_basis`;
the computed Form 8606 lives in `result.json form_8606`. `/tax-rollover` carries
`form_8606.basis_remaining` → next year's `prior_year.ira_basis` (FIELD_MAP `ira_basis` entry).
`/tax-compute` computes it (routing the taxable portion to 1040 line 4b) and `/tax-roth-convert`
sweeps it. MAGI≈AGI; basis aggregates all traditional/SEP/SIMPLE IRAs; per-individual; ratio
kept at full precision (the IRS 3-dp form rule injects error — matches tax software for
`/tax-verify`). Federal-only v1.

## Key Design Principles
- **No hardcoded personal data** — taxpayer info lives in `taxpayer_profile.json`, not skill code
- **No hardcoded tax law** — skills fetch current rates/brackets from IRS.gov via WebSearch at runtime, cached in `tax_law_<year>.json`
- **Modular** — each skill works independently; inapplicable sections are skipped
- **Parallel** — document parsing dispatches subagents grouped by institution/language

## Directory Structure

```
agents/
├── skills/           # Skill markdown files (symlinked to ~/.claude/skills/<name>/SKILL.md by install.sh)
│   ├── tax-setup.md
│   ├── tax-archive.md
│   ├── tax-rollover.md
│   ├── tax-parse.md
│   ├── tax-compute.md
│   ├── tax-generate.md
│   ├── tax-verify.md
│   ├── tax-review.md
│   ├── tax-fbar.md
│   ├── tax-project.md
│   ├── tax-withholding.md
│   ├── tax-scenario.md
│   ├── tax-roth-convert.md
│   ├── tax-harvest.md
│   ├── tax-calendar.md
│   ├── tax-retirement.md
│   └── tax-ptet.md
├── templates/        # JSON/MD templates for workspace initialization
│   ├── result_template.json
│   ├── answers_template.md
│   └── taxpayer_profile_template.json
├── lib/              # Shared Python utilities
│   ├── parse_utils.py        # PDF/Excel/CSV extraction + document classification
│   ├── tax_law_fetcher.py    # Tax law cache structure + load/save
│   ├── tax_computations.py   # Parameterized tax math (brackets, QDCG, SE, FTC, CTC, compute_scenario, compute_niit, compute_form_8606, etc.)
│   ├── archive.py            # Move year files into archive/{year}/ for /tax-archive
│   ├── rollover.py           # Diff/merge engine for /tax-rollover
│   ├── projection.py         # Mid-year projection compute + renderer for /tax-project
│   ├── yoy_diff.py           # YoY What-Changed diff for /tax-review (read-only)
│   ├── withholding.py        # Federal withholding true-up for /tax-withholding (read-only)
│   ├── scenario.py           # What-if scenario modeling for /tax-scenario (writes scenarios/ only)
│   ├── roth_ladder.py        # Roth conversion ladder for /tax-roth-convert (writes scenarios/ only)
│   ├── loss_harvest.py       # Tax-loss harvesting scanner for /tax-harvest (writes scenarios/ only)
│   ├── tax_calendar.py       # Quarterly reminder .ics generator for /tax-calendar + /tax-setup step 6.5
│   ├── retirement_dashboard.py # Mid-year 401k/IRA/HSA/Mega-Backdoor capacity + marginal-saved (/tax-retirement, read-only)
│   └── ptet.py               # PTET election analyzer for /tax-ptet (read-only, no artifact; anchored federal SALT-cap-workaround savings)
└── install.sh        # Symlink skills to ~/.claude/skills/<name>/SKILL.md
```

## Installation

```bash
bash agents/install.sh
```
This creates `~/.claude/skills/<name>/SKILL.md` symlinks for each skill (the subdirectory-per-skill format Claude Code requires), creates/updates `.gitignore` for sensitive output files, and installs git hooks (branch guard, PII scan, push blocker).

## Runtime Files (created per-taxpayer, gitignored)
- `taxpayer_profile.json` — Taxpayer identity, filing status, flags (only last 4 of SSN)
- `tax_law_<year>.json` — Cached rates/brackets fetched from IRS.gov
- `result.json` — Computed form line values (source of truth)
- `answers.md` — Persistent Q&A and resolved questions
- `documents/` — Source tax documents (PDFs, XLSX, CSV) — place files here for parsing
- `summaries/` — Per-document parsed data as markdown
- `holdings.json` — Brokerage lot data for `/tax-harvest` (optional; gitignored). User-provided
  JSON listing open lots + `recent_transactions` for wash-sale detection.
- `scenarios/` — Derived planning artifacts written by `/tax-scenario`, `/tax-roth-convert`,
  and `/tax-harvest`. Never mutated by other skills.
- `tax_calendar_<year>.ics` — RFC 5545 iCalendar with deadlines for the year, written by
  `/tax-setup` step 6.5 (and regeneratable via `/tax-calendar`). Subscribe in Google
  Calendar / Apple Calendar / Outlook for 7-day-prior reminders on each event.

## Tooling
- PDF parsing: `pdfplumber` — use subagents for large PDFs (>10 pages)
- Excel: `openpyxl`
- PDF generation: `reportlab`
- XML safety: `defusedxml` — protects openpyxl against XXE attacks in crafted .xlsx files
- Install (virtual environment — do NOT use `--break-system-packages`):
  ```bash
  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
  ```
- Versions are pinned in `requirements.txt` for supply chain security

## Development Guidelines

### Feature design docs
- New feature specs go under `docs/superpowers/specs/YYYY-MM-DD-<feature>-design.md`
- Implementation plans go under `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
- Use `/tax-rollover` (PR #8) as the precedent for spec + plan structure

### Git Workflow
- NEVER run `git push` — print the command for the user to run manually
- After user confirms push, create PR via `gh pr create`
- Pre-commit hook blocks direct commits to `main`/`master` — always use a feature branch
- Pre-commit hook blocks PII and sensitive file types automatically
- To bypass hooks in emergencies: `git commit --no-verify` (use with caution)
- User pushes with: `git push --no-verify -u origin <branch>`
- Delete remote branches with: `git push origin --no-verify --delete <branch>` (pre-push hook blocks otherwise)
- After a squash merge, sync local main: `git reset --hard origin/main` (ensure no uncommitted changes first)

### CI (GitHub Actions)
- Runs on every PR to `main`: pytest, PII file scan, pip-audit
- All three checks must pass before merging (enforced by convention)
- Dependabot opens PRs weekly for outdated pip dependencies
- CI config: `.github/workflows/ci.yml`

### Codex PR review (automated)
- Codex auto-reviews PRs, but ONLY on a trigger: PR open / ready-for-review / a `@codex review` comment — a plain `git push` does NOT trigger a review
- Findings arrive as inline review comments tagged P1/P2; a clean pass posts a top-level "Didn't find any major issues" comment (NOT a formal review) — when polling for results, check both reviews and issue comments
- Resolve review threads via the GraphQL `resolveReviewThread` mutation (no REST endpoint); find unanswered Codex threads by querying `reviewThreads` and filtering out IDs already in some comment's `in_reply_to_id`

### Modifying tax_computations.py
- All functions take rates/brackets as parameters — never hardcode bracket values
- The only hardcoded constants are structural (SE threshold = $400, SE factor = 92.35%)
- Always round output to 2 decimal places
- Run `python3 agents/lib/tax_computations.py` after changes — self-test must pass

### Input validation (lib parsers)
- Numeric validators MUST reject non-finite values via `math.isfinite` — `json.loads` accepts `NaN`/`Infinity`, which pass `isinstance(x, (int, float))` and silently defeat `< 0` / `> 1` range checks (all comparisons with NaN are False)
- Validate VALUE not just type at the parse boundary (e.g. `filing_status` ∈ canonical set, `address.state` a 2-letter code) — a type-valid but out-of-domain string otherwise falls through to a wrong-but-plausible default

### Reading tax_law_<year>.json
- Schema is defined in `agents/lib/tax_law_fetcher.py` (`create_empty_tax_law` / `create_state_template`) — read it before writing accessors.
- Key paths: `federal.filing_statuses.<single|mfj|mfs|hoh>.{brackets,standard_deduction}`, `federal.capital_gains_rates.{zero_pct_limit,fifteen_pct_limit}`, `federal.child_tax_credit.phase_out_start`, `federal.additional_medicare_tax.threshold`, `federal.niit.threshold`, `federal.irmaa.tiers.<single|mfj>` (list of `{magi_threshold, annual_surcharge_per_person}`), `state.{brackets,standard_deduction}`.
- Gotcha: `capital_gains_rates` and `child_tax_credit.phase_out_start` are keyed ONLY `single`/`mfj` (map MFJ/QSS→mfj, else→single); `additional_medicare_tax.threshold` and `niit.threshold` add `mfs`; `filing_statuses` has all four. `irmaa.tiers` is keyed `single`/`mfj` only and ships EMPTY (`[]`) — `/tax-setup` must fetch it, and `/tax-roth-convert` degrades to "IRMAA not modeled" when it's empty. Statutory-fixed thresholds (e.g. safe-harbor $150k/$75k) are hardcoded constants, not in tax_law.

### Modifying templates
- `result_template.json` field names must match what `generate_tax_pdf.py` reads
- Sections in result_template.json should be conditional — /tax-setup removes inapplicable ones

### Modifying skills
- Skills are Markdown files that instruct Claude Code (they are NOT executed directly)
- Each skill should specify prerequisites (which files must exist first)
- Parsing skills should dispatch parallel subagents for independent document groups

### Read-only planning skills
- `/tax-project` and the YoY diff in `/tax-review` share one pattern: a pure-compute lib module (frozen dataclasses + markdown renderer + CLI `_main`) that reuses `rollover.load_prior` for the prior-year baseline and NEVER mutates `result.json`/`taxpayer_profile.json`. Follow `agents/lib/projection.py` / `yoy_diff.py` / `withholding.py` for future planning features (backlog #6).
- `/tax-scenario` (`agents/lib/scenario.py`) extends the pattern: it still never mutates the baseline, but it *does* write derived `scenarios/<name>.json` siblings via a guarded `save_scenario` (forces the target under `workspace/scenarios/`, validates the name is a single path segment, refuses `result.json`/`taxpayer_profile.json`, `chmod 600`). It anchors the absolute scenario tax to the real `result.json` line 24 and models only the marginal effect of the delta via the `compute_scenario` keystone in `tax_computations.py`. The keystone is rates-parameterized like every other function there — backlog #6 (Roth ladder) sweeps it.
- `/tax-roth-convert` (`agents/lib/roth_ladder.py`, backlog #6) reuses scenario.py wholesale (`extract_baseline_inputs`, `_rate_kwargs`, `read_tax_year`, `save_scenario`, `_validate_name`) and SWEEPS the `compute_scenario` keystone across `ira_distribution` deltas $0..--max. Two things the keystone can't express are layered on in the ladder: (1) **NIIT** via the new `compute_niit` in `tax_computations.py` (rates-parameterized; threshold None → 0.0 so it degrades), added OUTSIDE the keystone because the keystone deliberately omits it and the baseline NIIT is already inside line 24 — so only the *increment* is added and the $0 rung still equals line 24 exactly; (2) **IRMAA** tier flags from `federal.irmaa.tiers` (new schema block), which is a Medicare *premium* surcharge, NOT income tax, so it is flagged as a side cost and never folded into the tax total. LTCG bracket bumping needs no extra code — the QDCG worksheet inside the keystone already prices it into the marginal-rate column; the ladder only *names* where it happens. MAGI≈AGI. Federal-only v1. **Form 8606 basis (item #7) is now wired in**: when `--ira-value` (+ `--ira-basis`, default from `prior_year.ira_basis`) are supplied, each rung calls `compute_form_8606(year_end_value=B−C, amount_converted=C)` and feeds only the taxable portion (line 18) into the keystone — the taxable fraction `1−basis/B` is constant across the sweep, the $0 rung still equals line 24 (taxable conversion at C=0 is 0), and a conversion exceeding the balance B is capped at B and flagged once. Degrades to full-taxable when basis/value are absent (backward compatible — all 45 prior ladder tests unchanged).
- `/tax-harvest` (`agents/lib/loss_harvest.py`, backlog #4) reuses scenario.py wholesale (`extract_baseline_inputs`, `_rate_kwargs`, `_net_capital_gains`, `read_tax_year`, `save_scenario`, `_validate_name`, `_resolve_under`) and is the FIRST anchored skill that takes a separate user-supplied input file (`holdings.json`, lots + recent_transactions, validated against the workspace boundary via the reused `_resolve_under`). Per-lot logic adds three primitives: (1) `derive_holding_period` — IRS rule is strictly *more than* 365 days for LT, so exactly-365 lots are short-term; (2) `check_wash_sale` — returns `(replacement_qty, reasons)` aggregating §1091's ±30-day bilateral pool per ticker, with all accounts in the file pooled because the user controls account inclusion (a `quantity`-less buy contributes `+math.inf` for the conservative full-block fallback); (3) `_apply_wash_sale_prorate` — distributes the per-ticker pool **FIFO by acquired date** across losing lots of the ticker, computing per-lot `disallowed_loss = matched_qty/lot.quantity × unrealized_loss`. **The proration matters:** a 1-share DRIP buy against a 100-share losing lot disallows 1% of the loss, not 100% (the v1 binary-block bug Codex P2 flagged on PR #18 — when fixed, the fixture's eligible harvest jumped $4,000 → $7,450 because two `partial`-status lots became correctly harvestable). Status is `clear` / `partial` / `fully_blocked`; only `fully_blocked` is excluded from the eligible set, and `wash_sale_blocked_loss` is now the sum of the *disallowed* slices (so `total_loss + blocked = all unrealized losses` reconciles). Same-ticker is the "substantially identical" proxy and different-CUSIP ETF swaps are NOT flagged; basis-shift on disallowed losses (the §1091 deferral to replacement-share basis) is documented but NOT modeled in v1 (affects future-year Schedule D only). Aggregate logic re-does the Schedule D netting via `_net_capital_gains(new_st, new_lt)` and expresses the harvest as a delta to `compute_scenario` (`capital_gains` for LT-loss flow, `other_income` for the recovered net ST), so the $3K/$1.5K-MFS loss cap, character preservation, and LTCG bracket positioning come "for free" from the keystone. **State savings** is a simplified estimate (state ordinary marginal rate × federal taxable-income reduction, where federal taxable-income reduction is the keystone's actual `Δ taxable_income` so second-order effects like QDCG shifts flow through) under the v1 assumption that most states tax cap gains as ordinary; degrades to $0 with a loud banner when `tax_law.state.brackets` is empty. Per-lot `est_value_per_dollar` is a ranking heuristic only (ord_mtr for ST, ltcg_mtr for LT, plus state_mtr if modeled); ranking key is `value_per_dollar × allowed_loss` so partial lots rank by their harvestable portion, not their gross loss. The aggregate `fed_savings` does the real anchored math.
- "Section present" for diff/presence logic = holds a non-zero number or non-empty list; template scaffolding (strings, booleans, `_`-prefixed keys) never counts.
- `/tax-calendar` (`agents/lib/tax_calendar.py`, backlog #10) is the "minimal write-only" sibling: pure compute (frozen `CalendarEvent` dataclass + per-category builders + `build_events` orchestrator + `to_ics` RFC-5545 serializer) + CLI `_main` + workspace-bounded `write_calendar`. Reuses `rollover._validate_subpath` + `_validate_year_arg` (the same boundary check every other write-derived skill uses), catches the `RolloverError` and re-raises as `CalendarError` to keep this module's error surface consistent. Writes only `tax_calendar_{year}.ics` at the workspace root (NOT under `scenarios/` — it's a top-level deliverable for the taxpayer, not a planning artifact). UIDs use the deterministic `{category}-{key}-{year}@tax-prep-skills` slug so re-importing is idempotent. **Key schema decision: PTET reminders are driven by `flags.k1_states` (entity-state list — where each K-1 partnership/S-corp files), NOT by `address.state` (taxpayer's resident state). A TX resident with a CA K-1 needs CA PTET reminders.** RMD reminders are driven by user-asserted `subject_to_rmd_*` booleans, NOT a derived age check, because RMD start age is birth-year-cohort-dependent under SECURE 2.0. Statutory dates only (no IRS weekend/holiday rollover); CA/NY/NJ PTET only; first-year RMD April-1 deferral not modeled. Federal + 3-state.
- `/tax-ptet` (`agents/lib/ptet.py`, backlog #9) is the FIRST **read-only-AND-writes-nothing** planning skill — it's advisory output (a `/tax-review`-style section), so unlike scenario/roth-ladder/harvest it has no `save_scenario` and no `scenarios/` artifact. It reuses `scenario.extract_baseline_inputs` + `_rate_kwargs` + `read_tax_year` and `rollover._validate_subpath`/`_validate_year_arg`, and ANCHORS the federal benefit the same way the other three do: the entity-level PTET deduction is a negative `other_income` delta through the `compute_scenario` keystone, so the reported $ is the actual marginal effect around `result.json` line 24 (and tapers correctly as a large deduction falls through brackets — you can't save a top-rate on income you don't have). Three PTET-specific primitives: (1) `ptet_tax(state, income, rate_override=)` — CA flat 9.3% vs NY/NJ graduated `(lower, rate, base_tax)` schedules, statutory structural constants cited inline (like tax_calendar's deadline dates), overridable per state; (2) `_election_deadline(state, year)` — reuses the `/tax-calendar` statutory facts (NY's election due March 15 *of the tax year*, can't be made on the return — the trap); (3) the **forgone-SALT offset** = `marginal_fed × min(salt_room, total_ptet)`, defaulting `salt_room=0` (cap exhausted). **Bias direction:** defaulting to cap-exhausted *overstates* savings → loud `ASSUMPTION` note + `--salt-room` escape hatch; valuing the offset at the top marginal rate can only *reduce* net savings (safe under-statement). Per-state benefit is the anchored aggregate allocated pro-rata to each state's PTET paid (sum reconciles exactly). All numeric inputs go through `_strict_non_negative` (rejects bool/NaN/Infinity/<0). State savings is hardcoded $0 with a caveat (the owner's PTET credit offsets the entity tax). Federal-only v1; non-CA/NY/NJ states surface a `modeled=False` "not modeled" note rather than a silent $0. **Three Codex P2 fixes (PR #22):** (1) **§199A QBI clawback** — the keystone computes QBI only from `self_employment_net`, so K-1 income flowing through `other_income` gets no QBI recompute; for QBI-eligible income the PTET deduction shrinks the QBI base (owner loses ~`qbi_rate × PTET` of deduction), which would OVERSTATE savings. Priced as a haircut to the modeled income reduction (`PTET × (1 − qbi_rate)`, stays anchored since the lost-QBI add-back is taxed at the same marginal rate) gated on `--qbi-eligible STATE`; default-off emits an overstatement caveat (don't silently overstate); simplified — ignores §199A income/W-2-wage limits. Gross benefit now allocated pro-rata by each state's *income reduction*, the SALT offset by *PTET paid* (both reconcile). (2) **CA deadline year-aware** — AB 150 (≤2025) June-15 prepayment is a hard election PREREQUISITE; SB 132 (2026–2030) the election survives a missed payment but the credit drops 12.5% (don't tell a 2026+ filer the election is lost after June 15). (3) **k1_states fallback gated on `has_k1_income`** — a stale `k1_states` on a profile with `has_k1_income` false no longer resurrects PTET advice (mirrors `/tax-calendar`'s gating). **Round 2:** repeated same-state `--income` accumulates (`_parse_state_amounts(combine="sum")` — two K-1 entities in one state add up; `--ptet-rate` uses `combine="reject"` since summing rates is nonsensical). **Round 3:** (a) `_PTET_WINDOWS` gating mirrored from tax_calendar (CA 2021-2030 sunset / NY 2021+ / NJ 2020+) — an out-of-window year is `modeled=False` with a note, never a confident price for an expired/not-yet-enacted regime; (b) `--marginal-fed` validated via `_validate_rate` (finite fraction in [0,1]; argparse `type=float` otherwise accepts `-1`/`nan`/`inf`/`1.5` and corrupts the offset). **Round 4:** `--ptet-rate` state codes validated at the parse boundary (`_normalize_state` in `_parse_state_amounts`) + an orphan-override guard in `analyze` (a rate override for a state with no matching `--income` hard-errors) — the escape hatch silently no-op'd on a typo (`California=...` → never matched `CA` → priced at statutory 9.3%). **Round 5 (P1):** NJ BAIT top bracket corrected — P.L. 2021 c.419 (TY2022+) collapsed the legacy 9.12% $1M–$5M + 10.9% >$5M brackets into a single 10.9% over $1M, so the old table UNDERSTATED NJ PTET (a $3M entity by $35,600) and thus federal savings (verified vs nj.gov). NJ schedule is now year-aware via `_schedule_for` (TY2022+ 3-bracket vs pre-2022 4-bracket `_NJ_SCHEDULE_LEGACY_2020`), mirroring the year-aware CA deadline. **Round 6 (P1+P2):** (P1) SALT cap is OBBBA-aware via `_salt_cap(year,status)` — $40K/$20K-MFS for TY2025-2029 (reverts to $10K in 2030), so the old hardcoded "$10,000" text + default `salt_room=0` (assume-exhausted) OVERSTATED savings whenever a taxpayer had real headroom under the larger cap (verified vs IRS Topic 503); added `--other-salt` to DERIVE headroom (`cap − other_salt`), `--salt-room` still wins, MAGI-phasedown above $500K caveated not modeled. (P2) phantom-income guard — if supplied `--income` > baseline gross income the K-1 isn't in result.json, so subtracting the deduction would "save" tax on untaxed income; now hard-errors with a recompute prompt (+ fixture updated to carry $250K Schedule-1 K-1 income so it's not a wage-only baseline). P2×8 + P1×2 — 925 tests. **Round 7 (P2×2):** (a) phantom-income guard tightened from total gross to the PASS-THROUGH bucket (`other_income` = Sch 1 line 8) — large unrelated wages could otherwise mask a missing K-1; (b) `_est_marginal_fed` probe switched from `wages` to `other_income` so a high earner's 0.9% Additional Medicare Tax isn't folded into the SALT-offset rate (a SALT itemized deduction changes taxable income, not Medicare wages). P2×10 + P1×2 — 927 tests. **Round 8 (P2×2):** (a) forgone-SALT offset now gated on `itemizes` (CLI `--standard-deduction`) — a standard-deduction filer gets NO SALT itemized deduction, so subtracting the offset understated the benefit; default itemizes=True; (b) CA TY2021 special-cased — AB 150's first year had NO June-15 prepayment requirement (the prerequisite is TY2022-2025 only per FTB), so a TY2021 run no longer shows an impossible retroactive "election lost" deadline (elect on the timely return, due March 15 2022). P2×12 + P1×2 — 929 tests. **Round 9 (P2):** phantom-income guard now uses the RAW Schedule 1 line 8 (`passthrough_income`, threaded from `_main`) as the K-1 bucket instead of the composite `other_income` — the latter also folds in taxable SS + net short-term gains and would let a retiree's SS mask a missing K-1; falls back to `other_income` for direct callers without the raw value. P2×13 + P1×2 — 930 tests. **v2 follow-ups (deferred from PR #22 Codex review — need schema/keystone changes, not v1 tweaks):** (1) lift the PTET rate schedules + SALT cap into the fetched `tax_law_<year>.json` cache (a `state.ptet` block) so they're version-controlled per year like federal rates — the round-5/6 P1s were both hardcoded-tax-law drift. (2) **Dedicated K-1 field in result.json** — the phantom-income guard uses `line_8_schedule1_other` as the pass-through proxy, but Schedule 1 line 8 is the AGGREGATE other-income total (K-1/Sch E is mixed with Sch C, unemployment, etc.), so on a return with large non-K-1 Schedule-1 income a phantom K-1 claim can pass the guard (rounds 9-10 iterated on this with no clean field available; the output note "assumes the K-1 is in result.json" is the v1 mitigation). Add a `schedule_e_k1_income` field populated by `/tax-compute` and guard against it. (3) **Taxable-income-only marginal probe** — `_est_marginal_fed` probes ordinary income, which moves AGI as well as taxable income, so near AGI cliffs (e.g. CTC phase-out) the SALT offset can be overstated (understating savings — the safe direction); needs a `compute_scenario` deduction-only knob or a direct bracket lookup. v1 mitigation: pass explicit `--marginal-fed` near AGI cliffs. (4) **Next-business-day deadline rollover** — `_election_deadline` (and `/tax-calendar`) emit STATUTORY dates only; when a deadline lands on a weekend/holiday it actually rolls forward (e.g. NY PTET TY2026 opt-in is March 16 2026 because March 15 is a Sunday). Build a shared next-business-day helper used by BOTH `ptet.py` and `tax_calendar.py` (don't fix one in isolation — they'd print different dates for the same deadline); both currently caveat "statutory dates only". Bias is mostly safe (a day early) but the exact-day "already missed" edge is a real narrow harm.

### Testing
- Test suite: `source .venv/bin/activate && python3 -m pytest tests/ -v`
- 930 tests covering all computation functions (incl. compute_scenario + compute_niit + compute_form_8606) + archive/rollover modules (incl. IRA-basis carryforward) + YoY diff + mid-year projection + withholding true-up + scenario modeling + Roth conversion ladder (incl. Form 8606 pro-rata) + tax-loss harvesting (share-prorated wash-sale, account-aware self-acquisition exclusion, FIFO matching across same-ticker lots, zero-quantity-lot rejection, tax-advantaged-account exclusion per Rev. Rul. 2008-5, NIIT relief layered outside the keystone, CG-threshold completeness guard, Schedule D netting, state savings) + quarterly reminder calendar (1040-ES + FBAR + RMD via user-asserted `subject_to_rmd_*` flag — cohort-safe vs SECURE 2.0 + CA/NY/NJ PTET with CA "greater of" R&TC §19904(a) wording + post-TY2026 12.5% credit-reduction regime + NJ BAIT election in tax_year+1 vs NY in-year statutory difference + RFC 5545 .ics with codepoint-safe UTF-8 folding, strict bool/year/state validation, atomic write, post-init type invariants, non-PTET-state visible note, UID uniqueness invariant) + retirement-dashboard IRS Pub 590-A Worksheet 1-2 phase-out rounding (ceil-to-$10 + $200 floor for partial phase-out, no floor when fully phased out, applied to both Traditional IRA deductible cap and Roth direct-contribution cap; `_phaseout_fraction` returns FULL precision — rounding the fraction before the dollar multiply, combined with ceil-to-$10, can shift the cap a whole $10, so the $10/$200 rounding is applied ONLY to the dollar amount per the IRS worksheet order) + Pub 590-A Table 1-3 MFS+spouse-covered+lived-together $0-$10K phase-out + strict input-block typing (falsy non-dict flags/address/retirement_ytd hard-error instead of silent `or {}` coercion; all numeric validators (`_strict_num`/`_optional_non_negative_num`/`_require_tax_law_amount`/`_validate_rate`/modified_agi) reject non-finite NaN/Infinity — `json.loads` accepts them and they slip past `<0`/`>1` checks since NaN/Inf comparisons are False; same rule applied to filing_status + address.state strings; VALUE validation too — filing_status must be a canonical status (Single/MFJ/MFS/HoH/QSS, case+whitespace-normalized) else hard-error so "Married Filing Jointly"/"" can't silently fall through to Single windows; address.state must be a 2-letter USPS code else hard-error so "California" can't silently miss CA/NJ HSA non-conformity) + Mega Backdoor §415(c) accounting excludes only AGE-ELIGIBLE catch-up per §414(v) (excess deferral above eligible catch-up consumes 415c space until corrected; SECURE 2.0 60-63 super catch-up supersedes 50+ standard; the $150k §603 high-earner threshold default is TY2026-ONLY (the threshold indexes annually) — a post-2026 run on a cache lacking `secure_2_0_high_earner_threshold` HARD-ERRORS rather than silently reusing $150k (which would misclassify $150k-to-indexed earners into mandatory-Roth catch-up) — but the lookup is DEFERRED until catch-up classification is actually needed (catchup_amount>0 and §603 in effect), so an under-50 taxpayer's post-2026 dashboard isn't blocked by a threshold they can't use; when catch-up is UNAVAILABLE (high earner + no Roth source) the dollars above base are excess deferrals that CONSUME §415(c) space in the Mega Backdoor calc (eligible_catchup_max=0), not §414(v)-excluded catch-up; excess after-tax 401(k) (§415(c) annual addition) corrects via EPCRS with the returned amount taxable — NOT the IRA/HSA §4973 6%-excise/April-15 model (both the Mega Backdoor note and the render over-contribution banner give §415(c)-correct guidance keyed on kind=="after_tax"); the §109 super catch-up AMOUNT is gated on `_SUPER_CATCHUP_EFFECTIVE_YEAR=2025` — DISTINCT from §603's 2026 mandatory-Roth gate — so a pre-TY2025 dashboard with age_60_63 falls back to the standard 50+ catch-up instead of hard-erroring on the absent `catch_up_60_63` cache field; the age-fact normalization is NOT year-gated since a 60-63 person is still 55+/50+ for IRA/HSA in any year) + HSA spouse catch-up added on a JOINT return only (MFJ; MFS spouses file separately so the spouse $1k catch-up is on the spouse's own return; Single/HoH/QSS skip stale `age_55_plus_spouse` flag) + SECURE 2.0 §603 mandatory Roth catch-up split (TY2026+ high earners with prior-year FICA wages > $150k indexed → 401(k) row splits into pre-tax base + Roth catch-up with $0 current-year deduction; gated on tax_year >= 2026 per IRS Notice 2023-62 transition delay; the high-earner catch-up also requires the plan to offer a designated Roth source — `plan_offers_roth_401k` (default true) explicit-false → catch-up UNAVAILABLE (base-only row, no phantom catch-up) since a non-Roth plan can't accept a §603-mandated-Roth catch-up; absent → split shown with a caveat; warning note when wage data missing for catch-up-eligible taxpayer in effective years only; strict-`>` $150k threshold boundary pinned at exactly-$150k → no split; `prior_year_fica_wages` defined as the wages the PLAN uses for its Roth-catch-up determination per IRC §414(v)(7)(A) — generally the sponsoring employer's, BUT related common-law employers in the same §414(b)/(c)/(m)/(o) controlled group may be aggregated under the final regs, so notes defer to the plan administrator rather than asserting a blanket no-aggregation rule (categorical "don't aggregate" would make a controlled-group participant under-report → fall below threshold → wrongly keep catch-up pre-tax → overstate savings); catch-up + missing-wage notes carry the caveat) + clean RetirementError (not raw KeyError) when a half-fetched cache is missing the IRA phase-out map + IRA pool capped by taxable compensation per IRC §219(b)(1)(A) / §408A(c)(2) (retiree with $0 wages has $0 IRA capacity; §219(c) MFJ spousal exception combines compensation; warning note when compensation data missing — prevents 6%-per-year contribution-limit excise tax from following overstated capacity; ALSO warns when MFJ + primary comp provided but spouse comp null AND primary comp constrains below statutory — a single-earner couple's $0-wage subject would otherwise see a confident $0 instead of the §219(c) spousal capacity, so it warns of possible UNDERSTATEMENT while keeping the conservative cap to avoid overstatement) + MFS lived-apart flag is load-bearing for IRA computations (drives MFS-$0-$10K phase-out vs Single treatment per Pub 590-A footnote) — hard-error when MFS + missing rather than silently default to lived-together; profile template now includes the field as `null` to prompt explicit user choice + QSS excluded from the spouse-COVERED Traditional IRA phase-out (case 5 gated on filing_status=="MFJ" not the MFJ/QSS helper — a deceased spouse can't be covered by a current-year plan; QSS's OWN coverage still uses the mfj_covered window per Pub 590-A grouping) + per-account over-contributions surfaced with a ⚠ corrective-action banner in render_markdown (over_contributed was otherwise invisible — Remaining $0/Used 100% looked identical to exactly-maxed); for POOLED buckets surfaced only when over_contributed > pool_over_contributed so the Roth MAGI-reduced-cap excess shows even when the IRA pool is under the annual cap, while Traditional (bucket cap == pool cap) never double-reports against the pool banner + PTET election analyzer (CA flat 9.3% + NY/NJ graduated-schedule bracket math incl. marginal-bracket crossing, rate override flat-applied, anchored federal benefit = compute_scenario `other_income` delta around line 24, forgone-SALT offset nets unused cap headroom at marginal rate with auto-estimate probe, two-state pro-rata allocation reconciling to the aggregate, zero-income deadline-reminder mode, non-CA/NY/NJ `modeled=False` not silent-$0, per-state election deadlines CA Jun-15/NY Mar-15-in-year/NJ Mar-15-following, multi-owner graduated caveat suppressed on override, SALT-assumption note toggle, NaN/Infinity/negative/bool input rejection, STATE=VALUE arg parsing, CLI end-to-end + k1_states fallback + year-mismatch + path-traversal guard) + integration pipeline
- Expected values are hand-computed from IRS worksheets — update comments when changing assertions
- When adding tests, update the test count reference on the line above
- Test fixtures under `tests/fixtures/` are allowlisted from the PII scan because synthetic fixtures must share filenames with real workspace files; new fixtures MUST live under that path
- Fixture files sharing a runtime filename (`result.json`, `taxpayer_profile.json`, `tax_law_*.json`, `projection_inputs.json`) match `.gitignore`'s unanchored rules — add new ones with `git add -f` (existing fixtures are already tracked)

### Security
- **NEVER store full SSNs** — only last 4 digits in `ssn_last4` fields
- `taxpayer_profile.json` and all output files are in `.gitignore`
- Do not log or display sensitive data in skill output
- **File permissions:** After writing any file containing PII or financial data (`taxpayer_profile.json`, `result.json`, `answers.md`, `FBAR_*.md`, PDFs), set permissions to owner-only: `chmod 600 <file>`
- **Encryption at rest:** Users should enable full-disk encryption (FileVault on macOS, BitLocker on Windows)
- **Subagent security:** Parsing subagents must NOT have network access (no WebFetch/WebSearch) and must NOT write outside `summaries/`. All subagent prompts must include untrusted-content warnings.
- **SSN redaction:** When parsing source documents, NEVER extract full SSNs — record only last 4 digits as `XXX-XX-{last4}`
- **PII scan rules are duplicated** between `scripts/pre-commit-pii-scan.sh` and `.github/workflows/ci.yml` — when modifying rules, update both

## Common Tax Gotchas
- QDCG worksheet: use NET LTCG from Schedule D line 15 (after losses), not gross cap gain distributions
- 1099-R Code G (direct rollover): $0 taxable but must report on 1040 line 5a
- Additional Medicare Tax: employer withholds 0.9% on wages >$200K, but MFJ threshold is $250K — over-withholding becomes a credit (Form 8959)
- FBAR max values (conservative, total inflows) differ from Form 8938 values (realistic simultaneous peak)
- Form 8938 thresholds differ by filing status — MFJ in US: $200K year-end / $300K any time
- State HSA non-conformity (CA, NJ): HSA contributions and earnings are state-taxable. (Alabama formerly non-conforming — Ala. Code §40-18-15.6 has allowed the deduction since 2018-01-01)
- FTC simplified: ≤$600 MFJ ($300 single) from qualified passive sources — no Form 1116 needed
- QBI deduction (Sec 199A) applies even with standard deduction
- Form 8949 boxes: A/B/C = short-term (basis reported/not reported/no 1099-B), D/E/F = long-term (same pattern). Foreign brokerages = Box B or E (basis not reported)
- Schedule D Part I line 1c field is `line_1c_1099b_box_c` (not `box_f` — was renamed during accuracy audit)
