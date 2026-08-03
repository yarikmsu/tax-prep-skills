# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is
Reusable, taxpayer-agnostic tax preparation toolkit for Claude Code. Provides `/tax-*` skills that guide Claude through the full US federal + state return pipeline — from document parsing through IRS review. Works for any filing status and any tax year. **State support is California-first** (2026-07-12 product decision): CA Form 540 is computed in depth; other states are bracket-estimate only; NYC / part-year / nonresident returns are not modeled. Other-state depth is community-contribution territory — see the state-module playbook at `docs/contributing/state-module-playbook.md` (spec template: `docs/superpowers/specs/TEMPLATE-state-module.md`) and the roadmap (`docs/roadmap/2026-07-12-three-perspective-review.md`; active backlog: `docs/roadmap/2026-07-20-product-owner-review.md` — TY2026 season-readiness Epic M, in-persona coverage tier 2 Epic N, hygiene Epic O; Epics I–L are fully shipped).

## Architecture

```
/tax-archive  → Move prior year's files into archive/{year}/ (run at year-end cutover)
      ↓
/tax-setup    → Interview taxpayer, fetch current tax law, create workspace
      ↓        (auto-invokes /tax-rollover if archive/{year-1}/ exists)
/tax-rollover → Pull prior-year carryforwards, identity, dependents, payers from archive/
      ↓
/tax-parse    → Parse source documents (PDFs, XLSX, CSV) via parallel subagents
      ↓        (optional: /tax-parse-crypto for exchange CSVs → Form 8949/Schedule D)
/tax-compute  → Compute all form values from parsed data
      ↓
/tax-generate → Generate summary PDF from result.json (--filing-package adds the F1 form-ordered, line-numbered package + attachment checklist + software transfer mapping; NOT e-file)
      ↓
/tax-verify   → Cross-reference against tax software return
      ↓
/tax-review   → EA + IRS officer perspective review
      ↓
/tax-fbar     → Prepare FBAR filing document (if applicable)
```

**Optional parsing branch (off `/tax-parse`, feeds `/tax-compute`):**
- **`/tax-parse-crypto`** — crypto exchange importer (backlog #8, the LAST backlog item):
  ingests Coinbase/Kraken/Gemini transaction CSVs (or a normalized `crypto_transactions.json`),
  FIFO-matches disposals against acquisition lots, and emits Form 8949 rows + Schedule D
  totals + ordinary-income receipts + the Form 1040 page-1 digital-asset answer for
  `/tax-compute` to merge. **Input-side parser** (like `/tax-parse`), NOT an anchored planning
  skill — it produces actual return line items, so it reuses the parse/validation idioms
  (`loss_harvest.derive_holding_period`, strict bool/NaN/Inf validators) NOT the
  `compute_scenario` keystone. Three pure layers: **adapters** (messy exchange CSV → normalized
  txns, header-name-driven), **event expansion** (one normalized txn → primitive
  Acquisition/Disposal/IncomeEvent — where the two crypto gotchas live), and the **FIFO lot
  engine** (disposals consume lots oldest-first → Form 8949 rows). **Key tax facts encoded
  (IRS Notice 2014-21 — crypto is PROPERTY):** (1) crypto-to-crypto TRADES are taxable
  dispositions (the #1 omitted event — a trade expands to a Disposal of the sold leg AND an
  Acquisition of the bought leg at FMV); (2) staking/rewards/interest/mining/airdrop receipts
  are ORDINARY income at FMV on receipt (Schedule 1 line 8; sets the coin's basis via a paired
  Acquisition so it's not double-taxed on later sale); (3) the **wash-sale rule (§1091) does
  NOT apply** — the exact INVERSE of `/tax-harvest` (which is for "stock or securities");
  loudly caveated; (4) the digital-asset question is "Yes" iff any disposal OR income receipt
  (acquire-and-hold = "No"). **Bias-direction decisions:** FIFO only (IRS default; HIFO/LIFO
  would under-report → forbidden in v1); unmatched disposals (no acquisition on record) get
  $0 basis + a `needs_basis` flag (OVER-states gain → safe direction → banner + degrade, every
  row surfaced, not a hard error that blocks the whole import). v1 simplifications (caveated,
  not silently wrong): one per-asset FIFO pool across all transactions (universal basis,
  default — a self-transfer `send` is a no-op so basis stays in the pool; Rev. Proc. 2024-28
  per-wallet segregation from 2025 → run per account with `--per-account`, where `send`
  removes the lot); Box C/F default (no
  1099-DA reconciliation); NFTs/collectible 28% rate, DeFi, §1256, margin, mining-as-business
  auto-classification not modeled; state handled downstream by `/tax-compute`. Adds
  `form_1040.digital_asset_question` + a `crypto_summary` block to result_template, sets
  `flags.has_crypto` AND `flags.has_capital_gains` when capital rows exist (the latter is
  what `/tax-compute` gates Schedule D/8949 on — a crypto-only return needs it). Federal
  Form 8949 only. Writes no scenarios/ artifact (it's a parser).

**Calendar artifact (emitted by `/tax-setup`, regeneratable standalone):**
- **`/tax-calendar`** — quarterly reminder calendar: regenerates
  `tax_calendar_{year}.ics` from `taxpayer_profile.json` whenever flags change.
  Normally produced inline by `/tax-setup` step 6.5; this skill exists for
  mid-year flag corrections (opened a foreign account in August, turned 73 in
  October). Always emits 4 federal 1040-ES events; conditionally adds 3 CA
  540-ES events for CA residents per `address.state` (30/40/0/30 weighting —
  NO September installment, R&TC §19136.1; descriptions carry the §19136.3
  $1M/$500K-MFS 90%-only rule + §19011.5 mandatory e-pay; E7), FBAR
  (Apr 15 + Oct 15, in year+1), extension deadlines when /tax-extend set
  `flags.filed_extension` (Apr 15 + Oct 15 of year+1, F2), Dec 31 RMD per age-73+ spouse, and CA/NY/NJ
  PTET windows when K-1 income exists. v1 uses statutory dates only (no
  weekend/holiday rollover). UIDs are stable so re-importing the regenerated
  `.ics` updates existing calendar entries rather than creating duplicates.
  XS effort.

**Extension flow (off `/tax-compute` or `/tax-project`, April-time):**
- **`/tax-extend`** — Form 4868 extension flow (F2): computes the extension payment from
  the best available estimate (computed `result.json` line 37 → `/tax-project` balance →
  user estimate, each rung labeled), recommends a 10% cushion on projected figures (the
  90%-paid late-payment-penalty waiver), and carries the load-bearing fact that an
  extension extends FILING not PAYMENT. CA: automatic extension, payment-only via FTB
  3519 + §19011.5 e-pay note; other states not modeled. Sets `flags.filed_extension`
  (skill-set, never interview-asked) and regenerates the calendar with two `extension`
  events (Apr 15 + Oct 15 of year+1; PRODID v3). SEP/solo-401k employer contributions
  follow the extended deadline; IRA/HSA and Q1 1040-ES do NOT — said explicitly.

**Optional planning branch (off `/tax-setup`, not part of the linear flow):**
- **`/tax-project`** — mid-year projection: run any time after `/tax-setup` to project
  full-year tax, safe-harbor estimates, and W-4 4(c)/1040-ES guidance from YTD figures.
  When the state projection runs and the state is CA, adds the FTB 5805 CA safe harbor
  (incl. the R&TC §19136.3 $1M/$500K-MFS 90%-only rule on CURRENT-year CA AGI) and a CA
  540-ES voucher schedule under the 30/40/0/30 weighting (E7). Read-only (never mutates
  `result.json`); re-runnable quarterly. Uses `archive/{year-1}/`
  for the prior-year safe harbor when present, else degrades to the 90%-of-current test.
- **`/tax-withholding`** — withholding true-up: after `/tax-compute`, compares YTD federal
  withholding (projected to year-end at current pace) against the actual computed liability
  in `result.json`, and recommends a W-4 4(c) and/or 1040-ES schedule to hit the
  safe-harbor and zero-balance targets. Read-only; federal + a CA true-up block (E7) when
  `result.json` carries a computed `california_540` section — actual Form 540 tax vs YTD CA
  withholding, FTB 5805 safe harbor, DE 4 per-paycheck figure, and the 30/40/0/30 540-ES
  voucher schedule; other states not covered. Uses `archive/{year-1}/`
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
  income. Three-way state outcome via `state_no_income_tax(tax_law)`: MODELED when
  `tax_law.state.brackets` is populated; **$0 — correct and complete** when
  `tax_law.state.type == "none"` (FL, TX, ...) — a positive no-tax declaration, not a gap
  (affirmed only through the full residence chain: known resident matching the cache's
  code/year; degraded to not-modeled when the resident is unknown, and to a
  "NOT the complete picture" claim when `flags.has_nonresident_source_income` is set);
  degrades to "state savings not modeled" (still $0, but flagged as a gap, not a fact) when
  brackets are empty AND type isn't `"none"` (not yet fetched). Writes ONE
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
- **Arithmetic chain of custody** — `/tax-parse` step 6.5 emits `parse_ledger.json` (one record per source document, key boxes keyed by canonical category, skips need reasons) and `/tax-compute` Step 12.7 runs the tie-out HARD GATE (`agents/lib/tie_out.py`, exit 2 = return NOT complete): documents-without-records, records-without-files, and per-category ledger-vs-result sum mismatches all fail deterministically — the dropped-1099 CP2000 scenario cannot pass. Unverifiable categories (ledger sum with no result-side figure, e.g. capital proceeds without Schedule D detail) WARN loudly, never silently pass. `/tax-review` audits the aggregated result and structurally cannot catch intake drops; only this ledger-to-result comparison can.
- **Never silently out-of-scope** — `/tax-compute` Step 0.7 runs the capability gate (`agents/lib/capabilities.py`): profile situations the engine can't compute (rental Schedule E, Form 8962, part-year, NYC, kiddie tax, Schedule H, PFIC-with-a-taxable-event → Form 8621) BLOCK with the named form (CLI exit 2) unless the user explicitly acknowledges (`acknowledged_limitations` in the profile + decision recorded in answers.md; unknown ids hard-error so a typo can't silently unblock nothing). Manual-worksheet items (8863, 2441, 5695/8936, gambling, FEIE, and the H1 life-event set — marriage/divorce year, §121 home sale w/ 1099-S rule, inheritance basis step-up, pre-2019-decree alimony, Form 8839 adoption; life-event flags read False when absent so legacy profiles pass; Form 8958 is STATUS+STATE-driven, not flag-driven — every MFS filer domiciled in a community-property state (AZ/CA/ID/LA/NV/NM/TX/WA/WI; AK/SD/TN elective, not auto-flagged) gets the community_property_mfs manifest line; post-2018-decree alimony carries the CA-nonconformity rule — CA still taxes/deducts it via Schedule CA) + the state-scope note form the `meta.limitations` manifest (C2), copied verbatim into result.json by Step 12 and printed on PAGE 1 of the PDF by `/tax-generate`. Every capability rule's flag MUST exist in `taxpayer_profile_template.json` and be asked by the `/tax-setup` interview (enforced by test) — a rule keyed on a flag nothing sets is dead code.
- **State regimes are typed** — `tax_law state.type` (`graduated_income` | `none`; WA's excise adds a third later): a no-income-tax state is a POSITIVE declaration, never an empty section. See docs/contributing/state-module-playbook.md ("Variant: no-income-tax states").

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
│   ├── tax-extend.md
│   ├── tax-amend.md
│   ├── tax-project.md
│   ├── tax-withholding.md
│   ├── tax-scenario.md
│   ├── tax-roth-convert.md
│   ├── tax-harvest.md
│   ├── tax-calendar.md
│   ├── tax-retirement.md
│   ├── tax-ptet.md
│   └── tax-parse-crypto.md
├── templates/        # JSON/MD templates for workspace initialization
│   ├── result_template.json
│   ├── answers_template.md
│   └── taxpayer_profile_template.json
├── lib/              # Shared Python utilities
│   ├── parse_utils.py        # PDF/Excel/CSV extraction + document classification
│   ├── tax_law_fetcher.py    # Tax law cache structure + load/save + freshness (incl. H4 _sources authoritative-URL pinning)
│   ├── tax_computations.py   # Parameterized tax math (brackets, QDCG, SE, FTC, CTC, compute_scenario, compute_niit, compute_form_8606, compute_schedule_1a, compute_charitable_non_itemizer, compute_espp_disposition, compute_late_filing_penalty, compute_underpayment_interest, compute_form_6251, compute_ca_schedule_p, compute_form_8995a, compute_form_8889, compute_form_2441, compute_section_988, compute_pfic_1291, compute_pfic_excess_distribution, etc.)
│   ├── archive.py            # Move year files into archive/{year}/ for /tax-archive
│   ├── rollover.py           # Diff/merge engine for /tax-rollover
│   ├── projection.py         # Mid-year projection compute + renderer for /tax-project
│   ├── yoy_diff.py           # YoY What-Changed diff for /tax-review (read-only)
│   ├── withholding.py        # Federal withholding true-up for /tax-withholding (read-only)
│   ├── scenario.py           # What-if scenario modeling for /tax-scenario (writes scenarios/ only)
│   ├── roth_ladder.py        # Roth conversion ladder for /tax-roth-convert (writes scenarios/ only)
│   ├── loss_harvest.py       # Tax-loss harvesting scanner for /tax-harvest (writes scenarios/ only)
│   ├── tax_calendar.py       # Quarterly reminder .ics generator for /tax-calendar + /tax-setup step 6.5
│   ├── capabilities.py       # C1 capability gate (/tax-compute step 0.7, exit 2 = blocked) + C2 meta.limitations manifest
│   ├── irs_dates.py          # §7503 next-business-day rollover (DC legal holidays incl. Emancipation Day) — used by compute_form_2210 due dates AND (L1) tax_calendar (federal deadlines) + ptet _election_deadline; tax_calendar state deadlines roll weekends-only
├── tie_out.py            # A2 parse-to-return tie-out (/tax-compute step 12.7 hard gate over the A1 parse_ledger.json) + foreign-payer FBAR set-difference input (G2, /tax-fbar step 1.6) + A3 second-read advisory + A4 missing_expected_payers
│   ├── retirement_dashboard.py # Mid-year 401k/IRA/HSA/Mega-Backdoor capacity + marginal-saved (/tax-retirement, read-only)
│   ├── ptet.py               # PTET election analyzer for /tax-ptet (read-only, no artifact; anchored federal SALT-cap-workaround savings)
│   ├── amend.py              # F3 1040-X three-column engine + parse-ledger Part II diff for /tax-amend
│   ├── filing_package.py     # F1 assembly order + attachment checklist for /tax-generate --filing-package
│   └── crypto_importer.py    # Crypto exchange importer for /tax-parse-crypto (Coinbase/Kraken/Gemini CSV → Form 8949/Schedule D, FIFO, no wash-sale)
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
- `reviews/` — Persisted `/tax-review` workpapers (`{year}-review.md`, chmod 600,
  gitignored). Archived into `archive/{year}/reviews/` at cutover; `/tax-rollover`
  surfaces the prior year's "Tax Planning for Next Year" section as the new
  year's checklist.
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
- Gotcha: `capital_gains_rates`, `child_tax_credit.phase_out_start`, and `qbi_threshold` are keyed ONLY `single`/`mfj` (map MFJ/QSS→mfj, else→single); `additional_medicare_tax.threshold` and `niit.threshold` add `mfs`; `filing_statuses` has all four. `irmaa.tiers` is keyed `single`/`mfj` only and ships EMPTY (`[]`) — `/tax-setup` must fetch it, and `/tax-roth-convert` degrades to "IRMAA not modeled" when it's empty. `salt_cap` is `{amount, mfs_amount}`, fetched per-year (OBBBA base indexes ~1%/yr 2026-2029; `ptet._salt_cap` prefers the cache and falls back to the unindexed statutory schedule when unset). `child_tax_credit.refundable_limit` is the per-child ACTC cap (indexed) required by `compute_child_tax_credit`'s refundable split. Statutory-fixed thresholds (e.g. safe-harbor $150k/$75k; ACTC $2,500 floor + 15% rate; $500 ODC) are hardcoded constants, not in tax_law.

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
- `/tax-harvest` (`agents/lib/loss_harvest.py`, backlog #4) reuses scenario.py wholesale (`extract_baseline_inputs`, `_rate_kwargs`, `_net_capital_gains`, `read_tax_year`, `save_scenario`, `_validate_name`, `_resolve_under`) and is the FIRST anchored skill that takes a separate user-supplied input file (`holdings.json`, lots + recent_transactions, validated against the workspace boundary via the reused `_resolve_under`). Per-lot logic adds three primitives: (1) `derive_holding_period` — IRS rule is strictly *more than* 365 days for LT, so exactly-365 lots are short-term; (2) `check_wash_sale` — returns `(replacement_qty, reasons)` aggregating §1091's ±30-day bilateral pool per ticker, with all accounts in the file pooled because the user controls account inclusion (a `quantity`-less buy contributes `+math.inf` for the conservative full-block fallback); (3) `_apply_wash_sale_prorate` — distributes the per-ticker pool **FIFO by acquired date** across losing lots of the ticker, computing per-lot `disallowed_loss = matched_qty/lot.quantity × unrealized_loss`. **The proration matters:** a 1-share DRIP buy against a 100-share losing lot disallows 1% of the loss, not 100% (a binary block would wrongly forfeit the whole lot — `partial`-status lots stay harvestable). Status is `clear` / `partial` / `fully_blocked`; only `fully_blocked` is excluded from the eligible set, and `wash_sale_blocked_loss` is now the sum of the *disallowed* slices (so `total_loss + blocked = all unrealized losses` reconciles). Same-ticker is the "substantially identical" proxy and different-CUSIP ETF swaps are NOT flagged; basis-shift on disallowed losses (the §1091 deferral to replacement-share basis) is documented but NOT modeled in v1 (affects future-year Schedule D only). Aggregate logic re-does the Schedule D netting via `_net_capital_gains(new_st, new_lt)` and expresses the harvest as a delta to `compute_scenario` (`capital_gains` for LT-loss flow, `other_income` for the recovered net ST), so the $3K/$1.5K-MFS loss cap, character preservation, and LTCG bracket positioning come "for free" from the keystone. **State savings** is a simplified estimate (state ordinary marginal rate × federal taxable-income reduction, where federal taxable-income reduction is the keystone's actual `Δ taxable_income` so second-order effects like QDCG shifts flow through) under the v1 assumption that most states tax cap gains as ordinary; degrades to $0 with a loud banner when `tax_law.state.brackets` is empty. Per-lot `est_value_per_dollar` is a ranking heuristic only (ord_mtr for ST, ltcg_mtr for LT, plus state_mtr if modeled); ranking key is `value_per_dollar × allowed_loss` so partial lots rank by their harvestable portion, not their gross loss. The aggregate `fed_savings` does the real anchored math.
- "Section present" for diff/presence logic = holds a non-zero number or non-empty list; template scaffolding (strings, booleans, `_`-prefixed keys) never counts.
- `/tax-calendar` (`agents/lib/tax_calendar.py`, backlog #10) is the "minimal write-only" sibling: pure compute (frozen `CalendarEvent` dataclass + per-category builders + `build_events` orchestrator + `to_ics` RFC-5545 serializer) + CLI `_main` + workspace-bounded `write_calendar`. Reuses `rollover._validate_subpath` + `_validate_year_arg` (the same boundary check every other write-derived skill uses), catches the `RolloverError` and re-raises as `CalendarError` to keep this module's error surface consistent. Writes only `tax_calendar_{year}.ics` at the workspace root (NOT under `scenarios/` — it's a top-level deliverable for the taxpayer, not a planning artifact). UIDs use the deterministic `{category}-{key}-{year}@tax-prep-skills` slug so re-importing is idempotent. **Key schema decision: PTET reminders are driven by `flags.k1_states` (entity-state list — where each K-1 partnership/S-corp files), NOT by `address.state` (taxpayer's resident state). A TX resident with a CA K-1 needs CA PTET reminders.** RMD reminders are driven by user-asserted `subject_to_rmd_*` booleans, NOT a derived age check, because RMD start age is birth-year-cohort-dependent under SECURE 2.0. **§7503 next-business-day rollover IS applied (L1, PRODID v4):** federal deadlines (1040-ES, extension, FBAR) roll past weekends + DC legal holidays via `irs_dates.next_business_day`; state deadlines (CA 540-ES, CA/NY/NJ PTET) roll past WEEKENDS ONLY (DC holidays don't bind state authorities — rolling a state date past DC Emancipation Day would push it a day LATE; state-specific holidays not modeled, caveated in the description); RMD Dec 31 does NOT roll. The `.ics` DTSTART is the observed date; descriptions note the statutory-vs-observed shift. CA/NY/NJ PTET only; first-year RMD April-1 deferral not modeled. Federal + 3-state.
- `/tax-ptet` (`agents/lib/ptet.py`, backlog #9) is the FIRST **read-only-AND-writes-nothing** planning skill — advisory output (a `/tax-review`-style section), so unlike scenario/roth-ladder/harvest it has no `save_scenario` and no `scenarios/` artifact. Reuses `scenario.extract_baseline_inputs` + `_rate_kwargs` + `read_tax_year` and `rollover._validate_subpath`/`_validate_year_arg`, and ANCHORS the federal benefit like the other three: the entity-level PTET deduction is a negative `other_income` delta through the `compute_scenario` keystone, so the reported $ is the actual marginal effect around `result.json` line 24 (tapers as a large deduction falls through brackets). Three primitives: (1) `ptet_tax(state, income, rate_override=)` — CA flat 9.3% vs NY/NJ graduated `(lower, rate, base_tax)` schedules; NJ BAIT year-aware via `_schedule_for` (TY2022+ single 10.9% over $1M vs pre-2022 4-bracket `_NJ_SCHEDULE_LEGACY_2020`); fetched `state.ptet` cache wins over the statutory schedule via `_resolve_schedule`/`_validate_fetched_schedule`. (2) `_election_deadline(state, year)` — reuses the `/tax-calendar` statutory facts (NY's election due March 15 *of the tax year*, can't be made on the return — the trap; CA year-aware: AB 150 ≤2025 June-15 prepayment is an election PREREQUISITE, SB 132 2026-2030 the election survives a missed payment but the credit drops 12.5%, TY2021 had no prepayment requirement) — wired through the rollover with `/tax-calendar` so both print the same weekend-rolled date (`_obs_date`/`_state_due` weekend-only; `ptet._election_deadline` ↔ `irs_dates.next_business_day`). (3) the **forgone-SALT offset** = `marginal_fed × min(salt_room, total_ptet)`, `salt_room` defaulting 0 (cap exhausted) but OBBBA-aware via `_salt_cap(year,status)` ($40K/$20K-MFS TY2025-2029, reverts $10K 2030; `federal.salt_cap` cache preferred; `--other-salt` derives headroom, `--salt-room` wins). **Bias:** cap-exhausted default OVERSTATES savings → loud `ASSUMPTION` note; offset valued at the top marginal rate can only *reduce* net savings (safe); offset gated on `itemizes` (`--standard-deduction`). §199A QBI clawback (`--qbi-eligible STATE`, priced `PTET × (1 − qbi_rate)`, default-off overstatement caveat). Gross benefit allocated pro-rata by each state's *income reduction*, SALT offset by *PTET paid* (both reconcile). `_PTET_WINDOWS` gating (CA 2021-2030 / NY 2021+ / NJ 2020+) → out-of-window `modeled=False`; non-CA/NY/NJ also `modeled=False` (never a silent $0). Phantom-income guard PREFERS `schedule_e_k1_income` over `line_8_schedule1_other`/`other_income` and hard-errors when `--income` exceeds that pass-through bucket. `_est_marginal_fed` probes `other_income` (not wages — avoids folding the 0.9% Medicare tax) with a local $100 probe + AUTO-ESTIMATED near-cliff caveat. `_parse_state_amounts` `combine="sum"` for `--income` / `"reject"` for `--ptet-rate`, `_normalize_state` validates codes at the parse boundary + an orphan-override guard; `--marginal-fed` via `_validate_rate`; all numerics via `_strict_non_negative`. State savings hardcoded $0 (the owner's PTET credit offsets the entity tax). Full round-by-round review history in `docs/changelog/pre-epic.md`.

### Testing
- Test suite: `source .venv/bin/activate && python3 -m pytest tests/ -v`
- 2077 tests covering all computation functions, with hand-computed expected values from IRS worksheets, + FL type-none state module (template/freshness, sales-tax input, harvest banner, capability note, property calendar, registrations). **Per-PR / per-Codex-round implementation history lives in `docs/changelog/` (one file per epic I–P + `pre-epic.md`); the load-bearing invariants each block must preserve are inventoried below.** When adding a test, update the count above; when changing an assertion, update its worksheet comment.
  - **Shared parse/validation infra (all lib parsers):** numeric validators reject non-finite `NaN`/`Infinity` (they pass `isinstance` + defeat `<0`/`>1` since NaN comparisons are False) — `_strict_num`, `_strict_non_negative`, `_optional_non_negative_num`, `_require_tax_law_amount`, `_validate_rate` (finite fraction in [0,1]), `_sched_a_amount` (non-negative), `_f2210_amount(allow_negative=…)`, `_field_num`. VALUE validation too: `filing_status` ∈ canonical set (Single/MFJ/MFS/HoH/QSS), `address.state` a 2-letter USPS code. Workspace-boundary writers reuse `rollover._validate_subpath` / `_validate_year_arg` / `_validate_name` / `_resolve_under`; planning skills reuse `scenario.extract_baseline_inputs` + `_rate_kwargs` + `rollover.load_prior`.
  - **Core 1040 engine:** `compute_qbi_deduction` — Form 8995 OVERALL CAP (deduction ≤ rate × (taxable income before QBI − net capital gain)); §199A(c)(4)/Reg. §1.199A-3(b)(1)(vi) QBI BASE NETTING (QBI reduced by trade-attributable `half_se_tax` + `se_health_insurance` + `se_retirement` BEFORE the 20%, passed separately so raw Schedule C net profit stays `qbi_income`); over-`federal.qbi_threshold` delegates to `compute_form_8995a` (Form 8995-A; `_qbi_phase_in_range`, §199A(b)(2) greater-of-50%-W-2 or 25%+2.5%-UBIA, §199A(d)(3) SSTB phase-out, year-aware range from `federal.qbi_phase_in_range` else statutory) when W-2/UBIA supplied and net QBI > 0; negative QBI floored not crashed; `over_threshold` warning when 8995-A limits unmodeled; planning callers (`compute_scenario`, projection) thread `half_se_tax`. Schedule 8812 CTC — ODC + refundable-ACTC split, phase-out on combined CTC+ODC, per-child `refundable_limit` cap (excludes never-refundable ODC), 15%-over-$2,500 earned-income formula; CTC tax-liability input is Credit Limit Worksheet A (line 18 minus Schedule 3 part-I credits). Form 8959 Part IV line 20 = Medicare WAGES × 1.45% only (SE income never enters withholding reconciliation); NIIT is Form 8960. Schedule A engine `compute_schedule_a` (itemize-vs-standard election, Schedule 2 additional taxes). `net_capital_gains` lives in tax_computations (projection reuses it; scenario keeps `_net_capital_gains` alias). Taxable-SS Pub 915 worksheet — provisional income adds tax-exempt interest + line-5 exclusion add-backs, filing_status REQUIRED (QSS = SINGLE $25K base; MFS lived-together $0-base 85% path), lump-sum LSE flagged upper-bound.
  - **Credits & AMT:** `compute_education_credits` (Form 8863 §25A — AOTC 100%/25% per-student + 40% refundable to line 29 surviving $0 tax vs LLC 20%-per-return $10K pool; QSS phases on SINGLE $80K-$90K; MFS neither; §25A(f)(1)/§25A(g)(3) dependent screening + §25A(i)(5) line-7 checkbox are the caller's step 0). `compute_form_2441` (Form 2441 §21 dependent-care — NONREFUNDABLE, §21(a)(2) 35%→20% AGI percentage, §21(c) $3K/$6K cap reduced by W-2 box-10 DCB, §21(d)/§21(d)(2) deemed income to the SPOUSE only when both flagged, §21(b)(1) qualifying-person + §21(e)(2) MFS disallowance; `dependent_care` capability rule computed-with-scope). Form 6251 AMT — `federal.amt.phaseout_rate` §55(d)(3) 25%≤TY2025/50% TY2026+ (§70107), null-valid/corrupt-hard-errors; Form 8801 carryforward. CA `state.amt` Schedule P — FLAT 7% TMT + 25% exemption phase-out (§17062, CA fixed at 2015 conformity, does NOT adopt §70107), no Part-III cap-gain coordination, unfetched exemption HARD-ERRORS; `schedule_p_carryforward` → `prior_year.ca_amt_credit_carryforward` (separate from federal 8801); the `has_iso_exercise` / `iso_exercise` capability rule is computed-with-scope (required-present gate flag).
  - **Penalties & interest:** `compute_late_filing_penalty` (§6651(a)(1) FTF 5%/mo cap 25%, §6651(a)(2) FTP 0.5%/mo, §6651(c)(1) overlap nets FTF to 4.5%/mo, §6651(a) >60-day minimum = lesser of the INDEXED floor (passed in, absent → loud degrade) or 100% of net tax; §6601/§6651(f) caveated). Form 2210 §6654 underpayment (required annual payment = lesser 90%-current/100-110%-prior, $75K-MFS AGI, §6654(e)(1) $1,000 de-minimis on tax-minus-withholding, §6654(e)(2) prior-zero exception, §6654(b)(3) FIFO cascade, §6654(g) even-withholding, §6621 quarterly accrual, §6654(b)(2) Apr-15 clamp, §6654(h)/Schedule AI caveated; required-annual-payment parity with `compute_safe_harbor` pinned). `compute_underpayment_interest` (§6601/§6622 DAILY-compounded on a late-paid balance, shares the §6621 cache + `_f2210` validators with Form 2210 but compounds daily with 365/366-leap denominators per Rev. Proc. 95-17 and REFUSES on any contiguous-quarter gap — `_quarter_start_on_or_before` / `_next_quarter_start` walk quarters from the due date; opening-gap or end-of-span → PARTIAL-through-a-date, never a guessed rate; §6601(e)(2)/§6404 not modeled).
  - **OBBBA TY2026 (Epic I):** `compute_schedule_1a` (tips §224 / overtime §225 / car-loan §70203 / senior §70103 BELOW-AGI deductions → 1040 line 13b, reduces the QBI base but NEVER AGI; caps/phase-outs from `federal.schedule_1a`; tips/overtime phase-outs round DOWN per §224(b)(2)(A)/§224(c)/§225 (no "or fraction thereof" in §224(c)/§225(c) — verified vs statute), car-loan rounds UP per §70203; `has_qualified_tips` fed by W-2 box-7 reported tips / Form 4070 monthly total / 1099 SE tips, while unreported/employer-allocated tips are a MANUAL scope-out (`has_unreported_or_allocated_tips` → Form 4137 → 1040 line 1c + Schedule 2 line 5, included in `qualified_tips` ONLY after that paired income + FICA is hand-entered); `active_qualified_trade` gated on `has_self_employment`/`has_k1_income`; §70103 senior 6% phase-out PER ELIGIBLE PERSON; QSS→SINGLE caps; MFS $0 for the joint-required provisions INCLUDING car-loan (`_provision(…, jointly_required=True)` zeros MFS — §70203 married-filing-separately ineligible, verified vs IRS/Federal Register); self-employed §224 tips SUBTRACTED from Schedule C before QBI; effective TY2025-2028; projection `forecast_overrides.schedule_1a` gated + `_sch1a_limits_incomplete`/`_sch1a_limit_bad` fail-closed). `compute_charitable_non_itemizer` (§170(p)/§70424 permanent below-AGI cash deduction for STANDARD-deduction filers TY2026+ → line 13c, CASH gifts only (no DAF/§509(a)(3)), mutually exclusive with Schedule A charitable, caps from `federal.charitable_non_itemizer`, §170(f)(8) ≥$250 substantiation, no §70425 0.5% floor; §63(c)(6)(A) mandatory-MFS-itemization exception → $0). §70105 minimum QBI (`federal.qbi_minimum` floor for an `active_qualified_trade` at/above threshold, re-capped by the §199A(a)(2) overall ceiling, loud-degrades unfetched). Statutory-sweep riders: gambling §70114 90%-loss TY2026+, §25D/§25C and §30D/§25E/§70505 clean-energy termination keyed to PLACED-IN-SERVICE date, Form 1099-K/NEC/MISC TY2026 thresholds, §1202 QSBS tiered-exclusion caveat.
  - **Equity comp (Epic J):** Form 8949 code-B employer-stock basis adjustment (broker 1099-B basis EXCLUDES W-2-included comp → unadjusted sale DOUBLE-TAXES; col-(f) code-B + signed `adjustment`, `cost` stays the broker figure; applied ONLY from a documented source, never inferred → `possible_double_taxation` warning; tie-out `employer_stock_basis_adjustment` via `_adjustments_total` (mirrors `_proceeds_total`, scoped to the `employer_stock` row marker) fails the hard gate on a dropped supplemental statement; `_note_8949_code_b` box→line map; W-2 tie-out maps to `line_1a_wages` not the 1a-1h sum). `compute_espp_disposition` (§423 qualifying iff > 2yr grant AND > 1yr purchase; DISQUALIFYING ordinary = full box4−box5 bargain element regardless of sale price (Pub 525 — "not limited to your gain"); QUALIFYING ordinary = lesser-of(§423(c)(2) grant discount, actual gain) floored $0; ordinary → W-2 box 1 or 1040 line 1h for BOTH dispositions). `has_rsu_vesting`/`has_espp` flags trigger on a current-year SALE (any vesting year).
  - **Integrity (Epic K):** /tax-verify override hardening (user-declared overrides move to an `OVERRIDDEN (unverified)` section, excluded from the ERROR count but PASS is UNAVAILABLE while any remain). Tax-law source pinning (`_sources` block, `_source_host_authoritative` accepts any `.gov` host (registry-restricted) + `law.cornell.edu`, requires http/https scheme, rejects a `.gov` lookalike like `irs.gov.evil.com`; recorded non-authoritative URL → INCOMPLETE, unpinned core block → advisory that never flips FRESH; `_fetched_federal_blocks` derives audited blocks from the schema (fetched iff differs from `create_empty_tax_law` seed) incl. exchange_rates; single state normalizes to the `state.<code>` pin). PFIC/Form 8621 tripwire (`has_foreign_mutual_funds` MANUAL rule, §1291-1298 EVENT-DRIVEN incl. §1298(f), keyed to the fund's domicile, asked independently of foreign-account questions).
  - **Deferred-engineering (Epic L):** §7503 next-business-day rollover wired through `tax_calendar.py` + `ptet.py` together — federal deadlines roll past weekends + DC holidays via `irs_dates.next_business_day`, state deadlines (CA 540-ES, CA/NY/NJ PTET) weekend-only (a state date isn't pushed past DC Emancipation Day — 2023-Apr-15-Saturday: federal→Apr 18, CA→Apr 17), RMD Dec 31 does NOT roll; `ptet._election_deadline` / `_obs_date` / `_state_due` render the same weekend-rolled string. Dedicated `schedule_e_k1_income` field (GROSS POSITIVE qualifying K-1 distributive income, NOT netted against other entities' losses, excludes Schedule E rental/royalty) — the /tax-ptet phantom-income guard PREFERS it over `line_8_schedule1_other`, fails CLOSED on a corrupt Infinity/NaN.
  - **Season readiness (Epic M):** the TY2026 golden-path integration fixture drives every Epic-I block through its compute function at a reachable year (schedule_1a/charitable fail-closed on nonzero input, qbi_minimum loud-degrades, `federal.salt_cap`/`federal.amt.phaseout_rate` fall back to statutory, §603 fail-closed at TY2027); the season-open runbook covers the live-fetch + K2-pin operational acceptance a fixture can't.
  - **In-persona tier 2 (Epic N):** `compute_ca_schedule_p` (above), `compute_form_8995a` (above), `compute_form_8889` (Form 8889, §223(b)(1)-(3) HSA limit from ACTUAL eligible MONTHS not full-year — the RETURN-side bias inversion that catches a partial-year excess; §223(b)(5) family split via `family_limit_share`, $1,000 catch-up monthly-prorated, §223(b)(8) last-month rule attested with a testing-period trap, §4973 6% excise; MANUAL when `eligible_months` unavailable), `compute_form_2441` (above).
  - **Planning keystone & anchored skills:** `compute_scenario` / `compute_niit` / `compute_form_8606` (pro-rata) anchor every what-if to `result.json` line 24. Roth ladder (Form 8606 pro-rata sweep). Tax-loss harvesting — `_apply_wash_sale_prorate` share-prorated §1091 block, account-aware self-acquisition exclusion (Rev. Rul. 2008-5 tax-advantaged-account exclusion), FIFO same-ticker matching, §1091(d) deferred-loss basis-shift + §1223 tacking DISCLOSED (future-year, not modeled). PTET — `ptet_tax` CA §19900/§19904(a) "greater of" flat 9.3% + NY §860 / NJ graduated via `_resolve_schedule`/`_validate_fetched_schedule`/`_schedule_for` (fetched `state.ptet` wins over statutory), `_est_marginal_fed` local $100 probe with an AUTO-ESTIMATED near-cliff caveat, `_parse_state_amounts`/`_normalize_state` STATE=VALUE parsing, `federal.salt_cap` cache preference. Retirement dashboard — IRA pool capped by taxable compensation (§408A(c)(2)), `_phaseout_fraction` full-precision + ceil-$10/$200-floor rounding, §414(v)-excluded catch-up vs §603-mandated-Roth split (§414(v)(7)(A) controlled-group §414(b)/(c)/(m)/(o) aggregation deferred to the plan; §109 super-catch-up effective TY2025 SUPERSEDES the 50+ standard catch-up), §415(c)-correct EPCRS over-contribution guidance, HSA spouse catch-up MFJ-only (`age_55_plus_spouse`), `age_65_plus_` §63(f) vs §70103 senior split.
  - **Amend / package / crypto / carryforward:** `compute` 1040-X three-column (Form 1040-X columns A/B/C incl. line-4b/4c for Schedule 1-A/13c, §6511 Schedule 1-A misplacement scan, §6601 interest-on-additional-tax). Filing package — assembly by Attachment Sequence, DERIVED Schedule 1/2/3/B/SE/8812 rows value-gated, line-13b hard-stop reconciles the per-provision sum, compute-evidence guard via `meta.limitations`. Crypto importer — FIFO, crypto-to-crypto trades taxable, `_unsign_qty` signed-quantity normalization, needs_basis $0-cost degrade, no wash-sale. Excess-SS (per-person multi-employer, wage-aware per-employer clamp §31(b), Form 843 employer-error path). CA excess-SDI (year-aware SB 951 not_applicable TY2024+, pre-2024 null hard-errors). Capital-loss carryforward prefers `schedule_d.loss_carryforward_to_next_year` over the legacy line_16−$3K derivation; projection forecasts an ST/LT split (`capital_gains_short`/`_long`, legacy combined field = LONG-term). `_ca_section_computed` / `_section_present` gate CA-block presence on computed evidence. `_main` CLI entrypoints self-test each module.
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
- CA SDI has NO taxable wage ceiling since TY2024 (SB 951) — the excess-SDI credit (540 line 74) cannot arise for 2024+; `state.sdi_wage_limit` stays null and `compute_excess_sdi` reports not-applicable. Only pre-2024 returns have a cap to exceed.
- Tax-exempt (muni) interest raises PROVISIONAL income for taxable Social Security even though it's untaxed — line 2a is a required input to `compute_taxable_ss`
- Excess SS withholding (Sch 3 line 11) is PER PERSON and MULTI-employer only — never pool spouses' Box 4; a single employer that over-withheld corrects via the employer, not the return
- FTC simplified: ≤$600 MFJ ($300 single) from qualified passive sources — no Form 1116 needed
- QBI deduction (Sec 199A) applies even with standard deduction
- Form 8863 education credits: QSS phases out on the SINGLE $80K–$90K MAGI range (grouped with Single/HoH — NOT the usual QSS→MFJ mapping); MFS gets neither credit; 40% of the AOTC is refundable (1040 line 29) even at $0 tax, and qualified expenses must be netted of tax-free scholarships (Pub 970) BEFORE compute_education_credits
- Form 8949 boxes: A/B/C = short-term (basis reported/not reported/no 1099-B), D/E/F = long-term (same pattern). Foreign brokerages = Box B or E (basis not reported)
- Schedule D Part I line 1c field is `line_1c_1099b_box_c` (not `box_f` — was renamed during accuracy audit)
- Employer stock (RSU/ESPP/NQSO): the 1099-B cost basis EXCLUDES the compensation already in W-2 box 1 (for RSUs, typically $0 basis) — reporting it verbatim DOUBLE-TAXES the W-2 income. Fix = Form 8949 col-(f) code B + col-(g) signed `adjustment` (negative reduces gain); `cost` stays the broker figure, gain = proceeds − cost + adjustment. Apply ONLY from a documented source (broker supplemental "adjusted cost basis" statement / Form 3922 / vest confirmation) — NEVER infer (an invented adjustment understates tax). Handled by the J1 flow; tie-out `employer_stock_basis_adjustment` category fails the hard gate if a ledgered supplemental statement's adjustment is dropped.
- ESPP §423 (Form 3922): a QUALIFYING disposition (sold > 2yr from grant AND > 1yr from purchase) taxes the lesser of the grant-date discount or actual gain as ordinary income (rest LTCG); a DISQUALIFYING disposition taxes the full purchase-date bargain element (box 4 − box 5) as ordinary REGARDLESS of sale price. The ordinary component belongs in W-2 box 1 — if the (often former) employer omitted it, it goes to 1040 line 1h (don't double-add). Handled by `compute_espp_disposition`.
- PFIC (§1291-1298): non-US mutual funds/ETFs held in a foreign brokerage are almost always PFICs requiring Form 8621 PER FUND — the default §1291 regime is punitive (excess-distribution spreading + compounding interest charge) unless a timely QEF/mark-to-market election was made. NOT computed. The test is the FUND's domicile, not its holdings (a US fund of foreign stocks is not a PFIC). **Two-tier gate:** `has_foreign_mutual_funds` stays a MANUAL note (buy-and-hold reporting-only obligation, §1298(f) with a $25K/$50K-MFJ de-minimis); a foreign fund with a DISTRIBUTION or DISPOSITION sets `has_pfic_taxable_event` → **UNSUPPORTED BLOCK** (`pfic_taxable_event`), because routing the §1291 income through the qualified-dividend/LTCG path would UNDERSTATE tax. **/tax-compute DOES compute the §1291 tax** (`compute_pfic_excess_distribution` = the §1291(b) 125%-of-prior-3-year excess-distribution test, ZERO in the first holding year; `compute_pfic_1291` = ratable per-day holding-period allocation (holding period = DAY AFTER acquisition through the event, §1223), current-year portion ordinary now, each prior year taxed at THAT year's highest ordinary rate §1291(c)(2) from `federal.historical_top_rate` (a 0/malformed rate hard-errors) + a §6621 daily interest charge §1291(c)(3) reusing `compute_underpayment_interest`; the §1291(b) base uses `preceding_holding_years` so a short/incomplete distribution history flags `complete=False`; the CALLER routes only `pfic_election==none` funds — QEF/§1295 & MTM/§1296 funds stay blocked (hand-prepared, recorded but NOT §1291-computed); v1 computes PFIC-FROM-ACQUISITION only (`pfic_start_date` defaults to `acquisition_date`) — a fund that became a PFIC AFTER acquisition (a mid-holding transition, `pfic_start_date` after acquisition) is BLOCKED: `compute_pfic_1291` RAISES and the fund is hand-prepared; the Step 12.7 tie-out MACHINE-checks that every taxable no-election fund has a COMPLETE computed result matching its `fund_id`, that `form_8621_pfic.all_complete` holds, AND that a nonzero `total_1291_addon` was reconciled into Form 1040 TOTAL TAX line 24 (`line_24_addon`/`line_24_with_addon`, the deferred tax to the LEFT of line 24 + §6621 interest via Schedule 2 line 17p → line 23 — added AFTER nonrefundable credits, with line 16 held to `line_16_regular_tax` since folding the §1291 charge into line 16 would let the CTC erode it and understate tax), FAILing exit-2 otherwise so an acknowledged event can't file understated or with a fund silently omitted) — but the rule stays a hard stop by default (punitive regime, easy to under-state): acknowledge it (recording the computed result) to proceed, and a `complete=False` (missing rate / uncovered quarter / no distribution history) keeps the return INCOMPLETE. `has_foreign_financial_account` → Form 8938 MANUAL surface (`foreign_asset_reporting`); `has_sanctioned_blocked_asset` → blocked-income + §165(g) worthlessness MANUAL (`sanctioned_blocked_asset` — do NOT auto-claim a loss on a frozen asset; frozen ≠ worthless). Structured facts live in `foreign_holdings_detail`.
- §988 foreign-currency bond (directly-held debt, NOT a PFIC): a non-USD bond disposition splits into an ORDINARY exchange component (§988(a)(1), Schedule 1, any holding period) and a CAPITAL market component (Schedule D) via `compute_section_988` (Reg. §1.988-2(b): exchange = fc_cost × Δrate; market = (fc_proceeds − fc_cost) × disposition_rate; they reconcile to proceeds_usd − basis_usd). DO NOT net them — an FX gain and a market loss of opposite sign have different character and land on different lines. The exchange component is US-SOURCE for a US resident (§988(a)(3) residence rule) — NOT Form 1116 foreign-source income; capital term is the >1-year calendar-anniversary rule (§1222), not a 365-day count; total_gain is derived from the two rounded reportables so they reconcile. Directly-held foreign bonds ALSO have annual §1272 OID + FC coupon interest to Schedule B every year held (the `foreign_bond_oid_interest` MANUAL rule, keyed on `has_foreign_bond`, surfaces it — not just on disposition). A foreign-fund holder (`has_foreign_mutual_funds`) MUST answer `has_pfic_taxable_event` explicitly — absence fails closed (`unanswered_pfic_event`). Foreign-currency FTC status: a suspended treaty removes treaty relief, and a §901(j) country denies the FTC entirely — verify per year (Russia is NOT §901(j) as of 2026-07; the US-Russia treaty is suspended eff. 2024-08-16).
- Crypto is PROPERTY (Notice 2014-21): crypto-to-crypto trades ARE taxable dispositions at FMV; staking/rewards/mining/airdrops are ORDINARY income at FMV on receipt (and that FMV is the basis); the wash-sale rule (§1091) does NOT apply (it covers "stock or securities"). The 1040 page-1 digital-asset question is "Yes" for any disposal OR income receipt — buy-and-hold-with-USD only is "No". (Handled by `/tax-parse-crypto`.)
