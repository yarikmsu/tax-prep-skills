# Skill catalog — per-skill design notes

Design rationale, invariants and scope limits for each `/tax-*` skill.
Relocated verbatim from CLAUDE.md so the agent instruction files stay under
the truncation limits documented in `docs/contributing/agents-compat.md`.
AGENTS.md points here; nothing else should duplicate it.

## Pipeline and optional branches

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


### Read-only planning skills
- `/tax-project` and the YoY diff in `/tax-review` share one pattern: a pure-compute lib module (frozen dataclasses + markdown renderer + CLI `_main`) that reuses `rollover.load_prior` for the prior-year baseline and NEVER mutates `result.json`/`taxpayer_profile.json`. Follow `agents/lib/projection.py` / `yoy_diff.py` / `withholding.py` for future planning features (backlog #6).
- `/tax-scenario` (`agents/lib/scenario.py`) extends the pattern: it still never mutates the baseline, but it *does* write derived `scenarios/<name>.json` siblings via a guarded `save_scenario` (forces the target under `workspace/scenarios/`, validates the name is a single path segment, refuses `result.json`/`taxpayer_profile.json`, `chmod 600`). It anchors the absolute scenario tax to the real `result.json` line 24 and models only the marginal effect of the delta via the `compute_scenario` keystone in `tax_computations.py`. The keystone is rates-parameterized like every other function there — backlog #6 (Roth ladder) sweeps it.
- `/tax-roth-convert` (`agents/lib/roth_ladder.py`, backlog #6) reuses scenario.py wholesale (`extract_baseline_inputs`, `_rate_kwargs`, `read_tax_year`, `save_scenario`, `_validate_name`) and SWEEPS the `compute_scenario` keystone across `ira_distribution` deltas $0..--max. Two things the keystone can't express are layered on in the ladder: (1) **NIIT** via the new `compute_niit` in `tax_computations.py` (rates-parameterized; threshold None → 0.0 so it degrades), added OUTSIDE the keystone because the keystone deliberately omits it and the baseline NIIT is already inside line 24 — so only the *increment* is added and the $0 rung still equals line 24 exactly; (2) **IRMAA** tier flags from `federal.irmaa.tiers` (new schema block), which is a Medicare *premium* surcharge, NOT income tax, so it is flagged as a side cost and never folded into the tax total. LTCG bracket bumping needs no extra code — the QDCG worksheet inside the keystone already prices it into the marginal-rate column; the ladder only *names* where it happens. MAGI≈AGI. Federal-only v1. **Form 8606 basis (item #7) is now wired in**: when `--ira-value` (+ `--ira-basis`, default from `prior_year.ira_basis`) are supplied, each rung calls `compute_form_8606(year_end_value=B−C, amount_converted=C)` and feeds only the taxable portion (line 18) into the keystone — the taxable fraction `1−basis/B` is constant across the sweep, the $0 rung still equals line 24 (taxable conversion at C=0 is 0), and a conversion exceeding the balance B is capped at B and flagged once. Degrades to full-taxable when basis/value are absent (backward compatible — all 45 prior ladder tests unchanged).
- `/tax-harvest` (`agents/lib/loss_harvest.py`, backlog #4) reuses scenario.py wholesale (`extract_baseline_inputs`, `_rate_kwargs`, `_net_capital_gains`, `read_tax_year`, `save_scenario`, `_validate_name`, `_resolve_under`) and is the FIRST anchored skill that takes a separate user-supplied input file (`holdings.json`, lots + recent_transactions, validated against the workspace boundary via the reused `_resolve_under`). Per-lot logic adds three primitives: (1) `derive_holding_period` — IRS rule is strictly *more than* 365 days for LT, so exactly-365 lots are short-term; (2) `check_wash_sale` — returns `(replacement_qty, reasons)` aggregating §1091's ±30-day bilateral pool per ticker, with all accounts in the file pooled because the user controls account inclusion (a `quantity`-less buy contributes `+math.inf` for the conservative full-block fallback); (3) `_apply_wash_sale_prorate` — distributes the per-ticker pool **FIFO by acquired date** across losing lots of the ticker, computing per-lot `disallowed_loss = matched_qty/lot.quantity × unrealized_loss`. **The proration matters:** a 1-share DRIP buy against a 100-share losing lot disallows 1% of the loss, not 100% (a binary block would wrongly forfeit the whole lot — `partial`-status lots stay harvestable). Status is `clear` / `partial` / `fully_blocked`; only `fully_blocked` is excluded from the eligible set, and `wash_sale_blocked_loss` is now the sum of the *disallowed* slices (so `total_loss + blocked = all unrealized losses` reconciles). Same-ticker is the "substantially identical" proxy and different-CUSIP ETF swaps are NOT flagged; basis-shift on disallowed losses (the §1091 deferral to replacement-share basis) is documented but NOT modeled in v1 (affects future-year Schedule D only). Aggregate logic re-does the Schedule D netting via `_net_capital_gains(new_st, new_lt)` and expresses the harvest as a delta to `compute_scenario` (`capital_gains` for LT-loss flow, `other_income` for the recovered net ST), so the $3K/$1.5K-MFS loss cap, character preservation, and LTCG bracket positioning come "for free" from the keystone. **State savings** is a simplified estimate (state ordinary marginal rate × federal taxable-income reduction, where federal taxable-income reduction is the keystone's actual `Δ taxable_income` so second-order effects like QDCG shifts flow through) under the v1 assumption that most states tax cap gains as ordinary; degrades to $0 with a loud banner when `tax_law.state.brackets` is empty. Per-lot `est_value_per_dollar` is a ranking heuristic only (ord_mtr for ST, ltcg_mtr for LT, plus state_mtr if modeled); ranking key is `value_per_dollar × allowed_loss` so partial lots rank by their harvestable portion, not their gross loss. The aggregate `fed_savings` does the real anchored math.
- "Section present" for diff/presence logic = holds a non-zero number or non-empty list; template scaffolding (strings, booleans, `_`-prefixed keys) never counts.
- `/tax-calendar` (`agents/lib/tax_calendar.py`, backlog #10) is the "minimal write-only" sibling: pure compute (frozen `CalendarEvent` dataclass + per-category builders + `build_events` orchestrator + `to_ics` RFC-5545 serializer) + CLI `_main` + workspace-bounded `write_calendar`. Reuses `rollover._validate_subpath` + `_validate_year_arg` (the same boundary check every other write-derived skill uses), catches the `RolloverError` and re-raises as `CalendarError` to keep this module's error surface consistent. Writes only `tax_calendar_{year}.ics` at the workspace root (NOT under `scenarios/` — it's a top-level deliverable for the taxpayer, not a planning artifact). UIDs use the deterministic `{category}-{key}-{year}@tax-prep-skills` slug so re-importing is idempotent. **Key schema decision: PTET reminders are driven by `flags.k1_states` (entity-state list — where each K-1 partnership/S-corp files), NOT by `address.state` (taxpayer's resident state). A TX resident with a CA K-1 needs CA PTET reminders.** RMD reminders are driven by user-asserted `subject_to_rmd_*` booleans, NOT a derived age check, because RMD start age is birth-year-cohort-dependent under SECURE 2.0. **§7503 next-business-day rollover IS applied (L1, PRODID v4):** federal deadlines (1040-ES, extension, FBAR) roll past weekends + DC legal holidays via `irs_dates.next_business_day`; state deadlines (CA 540-ES, CA/NY/NJ PTET) roll past WEEKENDS ONLY (DC holidays don't bind state authorities — rolling a state date past DC Emancipation Day would push it a day LATE; state-specific holidays not modeled, caveated in the description); RMD Dec 31 does NOT roll. The `.ics` DTSTART is the observed date; descriptions note the statutory-vs-observed shift. CA/NY/NJ PTET only; first-year RMD April-1 deferral not modeled. Federal + 3-state.
- `/tax-ptet` (`agents/lib/ptet.py`, backlog #9) is the FIRST **read-only-AND-writes-nothing** planning skill — advisory output (a `/tax-review`-style section), so unlike scenario/roth-ladder/harvest it has no `save_scenario` and no `scenarios/` artifact. Reuses `scenario.extract_baseline_inputs` + `_rate_kwargs` + `read_tax_year` and `rollover._validate_subpath`/`_validate_year_arg`, and ANCHORS the federal benefit like the other three: the entity-level PTET deduction is a negative `other_income` delta through the `compute_scenario` keystone, so the reported $ is the actual marginal effect around `result.json` line 24 (tapers as a large deduction falls through brackets). Three primitives: (1) `ptet_tax(state, income, rate_override=)` — CA flat 9.3% vs NY/NJ graduated `(lower, rate, base_tax)` schedules; NJ BAIT year-aware via `_schedule_for` (TY2022+ single 10.9% over $1M vs pre-2022 4-bracket `_NJ_SCHEDULE_LEGACY_2020`); fetched `state.ptet` cache wins over the statutory schedule via `_resolve_schedule`/`_validate_fetched_schedule`. (2) `_election_deadline(state, year)` — reuses the `/tax-calendar` statutory facts (NY's election due March 15 *of the tax year*, can't be made on the return — the trap; CA year-aware: AB 150 ≤2025 June-15 prepayment is an election PREREQUISITE, SB 132 2026-2030 the election survives a missed payment but the credit drops 12.5%, TY2021 had no prepayment requirement) — wired through the rollover with `/tax-calendar` so both print the same weekend-rolled date (`_obs_date`/`_state_due` weekend-only; `ptet._election_deadline` ↔ `irs_dates.next_business_day`). (3) the **forgone-SALT offset** = `marginal_fed × min(salt_room, total_ptet)`, `salt_room` defaulting 0 (cap exhausted) but OBBBA-aware via `_salt_cap(year,status)` ($40K/$20K-MFS TY2025-2029, reverts $10K 2030; `federal.salt_cap` cache preferred; `--other-salt` derives headroom, `--salt-room` wins). **Bias:** cap-exhausted default OVERSTATES savings → loud `ASSUMPTION` note; offset valued at the top marginal rate can only *reduce* net savings (safe); offset gated on `itemizes` (`--standard-deduction`). §199A QBI clawback (`--qbi-eligible STATE`, priced `PTET × (1 − qbi_rate)`, default-off overstatement caveat). Gross benefit allocated pro-rata by each state's *income reduction*, SALT offset by *PTET paid* (both reconcile). `_PTET_WINDOWS` gating (CA 2021-2030 / NY 2021+ / NJ 2020+) → out-of-window `modeled=False`; non-CA/NY/NJ also `modeled=False` (never a silent $0). Phantom-income guard PREFERS `schedule_e_k1_income` over `line_8_schedule1_other`/`other_income` and hard-errors when `--income` exceeds that pass-through bucket. `_est_marginal_fed` probes `other_income` (not wages — avoids folding the 0.9% Medicare tax) with a local $100 probe + AUTO-ESTIMATED near-cliff caveat. `_parse_state_amounts` `combine="sum"` for `--income` / `"reject"` for `--ptet-rate`, `_normalize_state` validates codes at the parse boundary + an orphan-override guard; `--marginal-fed` via `_validate_rate`; all numerics via `_strict_non_negative`. State savings hardcoded $0 (the owner's PTET credit offsets the entity tax). Full round-by-round review history in `docs/changelog/pre-epic.md`.

