# Skill catalog — per-skill design notes

Design rationale, invariants and scope limits for each `/tax-*` skill.
Relocated verbatim from CLAUDE.md so the agent instruction files stay under
the truncation limits documented in `docs/contributing/agents-compat.md`.
AGENTS.md points here; nothing else should duplicate it.

## Auditing which skills call the engine

The engine is invoked in TWO forms, and any audit of which skills reach it has
to match both:

- **By PATH** — `agents/lib/tie_out.py`, `agents/lib/preflight.py`.
- **By MODULE** — `python3 -m agents.lib.projection`.

A path-only search reads the module-form skills — every planning skill plus
`tax-archive` and `tax-rollover` — as calling no engine at all, which is the
reading that lets a roster of engine callers ship with two thirds of its
entries missing.

**Do not try to reproduce that by grepping the tree, and do not conclude from
grepping it that the module form no longer matters.** Every skill now opens
with `sh agents/lib/run.sh agents/lib/preflight.py`, a PATH form, so the path
spelling appears in every skill whatever the rest of it does — the gate hides the very
asymmetry this section is about.

A grep cannot even tell you how many skills there are. It counts FILES, and the
count is always higher than the number of skills: `references/*.md` match too,
and the two contributor skills (`codex-loop`, `state-module`) name the directory
while being exempt from the gate by design. No figure is quoted here on purpose
— it moves whenever a reference is added, and a stale absolute in the section
telling you not to trust a grep would be its own counter-example.

Check the predicate against command UNITS with the preflight command excluded,
which is what `test_the_launcher_is_what_makes_the_roster_universal` does. It
replaced a test whose premise the launcher retired: every invocation is a PATH
form now, so the roster is universal for a different reason than it was, and
what is load-bearing is the launcher's own universality.

AGENTS.md §4 states the rule this detail belongs to: a skill separated from
`agents/lib/` must refuse to compute rather than run every gate as a silent
no-op.

One skill is outside that rule rather than in violation of it: `/tax-safe-harbor`
calls no engine module at all and is declared in `ENGINE_FREE_SKILLS`, which is
TESTED rather than trusted. See "The one engine-free skill" below before adding a
second — the declaration is the obvious escape hatch, and what keeps it from
being one is that membership is a claim about the skill's text.

## The capability gate's manifest — what BLOCKS, what is MANUAL

Relocated out of AGENTS.md §4 by Y5 (PR #113); `agents/lib/capabilities.py` is the
registry, and this is the map. AGENTS.md keeps the rules — the gate blocks with the
named form unless acknowledged, an unknown id hard-errors, and every rule's flag must
exist in `taxpayer_profile_template.json` AND be asked by the `tax-setup` interview.

**Blocking** (exit 2, clearable only by an explicit `acknowledged_limitations` entry
with the decision recorded in `answers.md`): rental Schedule E, Form 8962, part-year
residency, residency in a second state, income sourced to another state, kiddie tax,
Schedule H, and PFIC with a taxable event. New York City's full-year resident tax is
computed, not blocked; a City resident for only part of the year is refused at compute
time.

**Manual-worksheet items** never block. They join the state-scope note in the
`meta.limitations` manifest, which is copied VERBATIM into `result.json` and printed on
page 1 of the generated PDF: Forms 8863, 2441, 5695/8936, gambling, FEIE, and the
life-event set — marriage or divorce year, §121 home sale with its 1099-S rule,
inheritance basis step-up, pre-2019-decree alimony (post-2018 decrees carry the CA
nonconformity: CA still taxes and deducts via Schedule CA), and Form 8839 adoption.

**The Earned Income Credit needed a rule because it had none**, and that — not
being uncomputed — is what was distinctive about it. Half the manual-worksheet
items above are uncomputed too (5695, 8936, 8839, 2555, gambling losses, Form
4137, Form 8938); every one of them already had a rule, and the EIC did not, so
its gap reached no manifest and no taxpayer. `earned_income_credit` fires on
`flags.may_qualify_for_eic` and covers FOUR lines — Form 1040 line 27, IT-201
line 65 (Form IT-215), line 66 (Form IT-209, the noncustodial parent credit) and
line 70 (the NYC EIC). Each has its OWN form and its own eligibility test, and
the rule says so: none of them is a copy of another, and a single federal remedy
over the four would put a federal figure on three New York lines, which is the
over-statement §4 calls a hard error.

MANUAL and not UNSUPPORTED is the severity decision: the direction is an
UNDER-statement, which AGENTS.md §4 degrades loudly rather than blocking, and a
worksheet that legitimately comes out at $0 is a correct return no gate may
refuse. For the same reason it is NOT registered in `answers.REQUIRED_ANSWERS` —
a registered answer blocks, and the interview asks for a TRUE on a MAYBE, so
coupling the two would make the liberal answer the expensive one.

The manifest line is the disclosure; the SECOND half lives in the tie-out gate,
which is the only place that can see whether the lines are still 0, since
`capabilities.py` deliberately does not read `result.json` (it runs before that
file exists, and its receipt input set is the profile alone — a gate that read
the result would go stale on every compute write). The tie-out's advisory names
only the lines that apply, gates the City line on the `nyc` block's presence,
fires on an UNANSWERED flag as well as a yes, and never fails the gate.

**Form 8958 is the exception, and getting it backwards drops the line entirely: it is
STATUS+STATE-driven, not flag-driven.** Every MFS filer domiciled in a
community-property state gets the manifest line with no flag involved —
AZ/CA/ID/LA/NV/NM/TX/WA/WI. AK/SD/TN are ELECTIVE community-property states and are not
auto-flagged.

Life-event flags read False when absent, so a legacy profile still passes.

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
  would under-report → forbidden in v1). AA40 segregates FIFO by explicit account
  IDs in one import and moves reviewed original lot slices through linked transfers.
  Missing acquisition/basis/ownership/transfer evidence blocks completion, including
  when there are no taxable disposals. Output remains diagnostic until complete;
  tie-out enforces the summary gate. From 2025 universal basis is diagnostic only.
  Legacy `--per-account` supports isolated histories. Contract and evidence schema:
  `docs/contributing/crypto-accounts.md`. AA41 routes each reported row to its
  Form 8949 box only from reconciled broker-form evidence — 1099-DA (TY2025:
  G-L) or 1099-B (TY2023-TY2024: A-F) — or a reviewed no-form declaration; any
  difference blocks until evidenced, and its B/E/T adjustment keeps the FIFO
  gain. Other years and both legacy pools route nothing. NFTs/collectible 28% rate, DeFi, §1256, margin, mining-as-business
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
  Priced on the deductions the taxpayer declares for the year, federal and state
  (`forecast_overrides.itemized_deductions`, `state_itemized_deductions`; 0 is the
  standard deduction, and an unanswered one refuses — AA21), and from TY2026 on the
  year's cash gifts to charity (`charitable_cash_gifts`, required alike — AA22), which
  earn a standard-deduction filer §170(p)'s deduction and count in the choice between
  the standard and itemized deductions; a separate filer is warned
  that the spouse's choice can force the other deduction, and an itemizer that the
  declared total is held as entered while parts of it move with income. When the state projection
  runs and the state is CA, adds the FTB 5805 CA safe harbor
  (incl. the R&TC §19136.3 $1M/$500K-MFS 90%-only rule on CURRENT-year CA AGI) and a CA
  540-ES voucher schedule under the 30/40/0/30 weighting (E7). Read-only (never mutates
  `result.json`); re-runnable quarterly. Uses `archive/{year-1}/`
  for CA prior-return data; the FEDERAL prior-year prong activates only from an
  explicit, completed `projection_inputs.json.prior_year_form_2210_tax_base`
  (never derived from archived Form 1040 line 24), else degrades to the
  90%-of-current test — see "Step 0's handoff" below.
- **`/tax-withholding`** — withholding true-up: after `/tax-compute`, compares YTD federal
  withholding (projected to year-end at current pace) against the actual computed liability
  in `result.json`, and recommends a W-4 4(c) and/or 1040-ES schedule to hit the
  safe-harbor and zero-balance targets. Read-only; federal + a CA true-up block (E7) when
  `result.json` carries a computed `california_540` section — actual Form 540 tax vs YTD CA
  withholding, FTB 5805 safe harbor, DE 4 per-paycheck figure, and the 30/40/0/30 540-ES
  voucher schedule; other states not covered. Uses `archive/{year-1}/`
  for CA prior-return data; the FEDERAL prior-year prong activates only from an
  explicit, completed `projection_inputs.json.prior_year_form_2210_tax_base`
  (never derived from archived Form 1040 line 24), else degrades to the
  90%-of-current test — see "Step 0's handoff" below.
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
  default from the selected individual opening profile balance) are supplied: each rung's taxable conversion is diluted
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
  individual SALT cap. Federal savings is **anchored** to `result.json` line 24: the
  entity deduction is a negative `other_income` delta through `compute_scenario` (same contract
  as scenario/roth-convert/harvest), so bracket positioning is priced automatically. CA flat
  9.3%; NY/NJ graduated schedules (applied to the owner's share — `--ptet-rate` overrides for
  multi-owner entity-pool cases). The **forgone-SALT offset** nets out any unused SALT cap
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
`tax_computations` is the pro-rata keystone (Part I worksheet — basis ratio capped at 1.0,
`basis_remaining` line-14 carryforward, `taxable_to_1040` = line 15 + line 18, a
`pro_rata_triggered` trap flag). `compute_individual_8606` wraps it independently for
primary/spouse and persists `result.form_8606.by_taxpayer`; basis lives in
`prior_year.ira_basis_by_taxpayer`. `/tax-rollover` carries each verified line 14,
including zero, to the same owner. See [individual IRA basis](individual-ira-basis.md)
for legacy scalar migration and blocking unresolved ownership. Joint Roth pro-rata
planning requires `--ira-owner`; the reported basis and balance belong only to that owner.
`/tax-compute` computes it (routing the taxable portion to 1040 line 4b) and `/tax-roth-convert`
sweeps it. MAGI≈AGI; basis aggregates all traditional/SEP/SIMPLE IRAs; per-individual; ratio
kept at full precision (the IRS 3-dp form rule injects error — matches tax software for
`/tax-verify`). Federal-only v1.


### Read-only planning skills
- `/tax-project` and the YoY diff in `/tax-review` share one pattern: a pure-compute lib module (frozen dataclasses + markdown renderer + CLI `_main`) that reuses `rollover.load_prior` for the prior-year baseline and NEVER mutates `result.json`/`taxpayer_profile.json`. Follow `agents/lib/projection/` / `yoy_diff.py` / `withholding.py` for future planning features (backlog #6).
- `/tax-scenario` (`agents/lib/scenario.py`) extends the pattern: it still never mutates the baseline, but it *does* write derived `scenarios/<name>.json` siblings via a guarded `save_scenario` (forces the target under `workspace/scenarios/`, validates the name is a single path segment, refuses `result.json`/`taxpayer_profile.json`, `chmod 600`). It anchors the absolute scenario tax to the real `result.json` line 24 and models only the marginal effect of the delta via the `compute_scenario` keystone in `tax_computations`, priced from the return's own line 12 (`scenario.return_deduction`, AA20): the cache's standard deduction put an itemizer's delta a bracket too high. The keystone is rates-parameterized like every other function there — backlog #6 (Roth ladder) sweeps it.
- `/tax-roth-convert` (`agents/lib/roth_ladder.py`, backlog #6) reuses scenario.py wholesale (`extract_baseline_inputs`, `_rate_kwargs`, `read_tax_year`, `save_scenario`, `_validate_name`) and SWEEPS the `compute_scenario` keystone across `ira_distribution` deltas $0..--max. Two things the keystone can't express are layered on in the ladder: (1) **NIIT** via the new `compute_niit` in `tax_computations` (rates-parameterized; threshold None → 0.0 so it degrades), added OUTSIDE the keystone because the keystone deliberately omits it and the baseline NIIT is already inside line 24 — so only the *increment* is added and the $0 rung still equals line 24 exactly; (2) **IRMAA** tier flags from `federal.irmaa.tiers` (new schema block), which is a Medicare *premium* surcharge, NOT income tax, so it is flagged as a side cost and never folded into the tax total. LTCG bracket bumping needs no extra code — the QDCG worksheet inside the keystone already prices it into the marginal-rate column; the ladder only *names* where it happens. MAGI≈AGI. Federal-only v1. **Form 8606 basis (item #7) is now wired in**: when `--ira-value` (+ `--ira-basis`, default from the selected individual opening profile balance) are supplied, each rung calls `compute_form_8606(year_end_value=B−C, amount_converted=C)` and feeds only the taxable portion (line 18) into the keystone — the taxable fraction `1−basis/B` is constant across the sweep, the $0 rung still equals line 24 (taxable conversion at C=0 is 0), and a conversion exceeding the balance B is capped at B and flagged once. Degrades to full-taxable when basis/value are absent (backward compatible — all 45 prior ladder tests unchanged).
- `/tax-harvest` (`agents/lib/loss_harvest.py`, backlog #4) reuses scenario.py wholesale (`extract_baseline_inputs`, `_rate_kwargs`, `_net_capital_gains`, `read_tax_year`, `save_scenario`, `_validate_name`, `_resolve_under`) and is the FIRST anchored skill that takes a separate user-supplied input file (`holdings.json`, lots + recent_transactions, validated against the workspace boundary via the reused `_resolve_under`). Per-lot logic adds three primitives: (1) `derive_holding_period` — IRS rule is strictly *more than* 365 days for LT, so exactly-365 lots are short-term; (2) `check_wash_sale` — returns `(replacement_qty, reasons)` aggregating §1091's ±30-day bilateral pool per ticker, with all accounts in the file pooled because the user controls account inclusion (a `quantity`-less buy contributes `+math.inf` for the conservative full-block fallback); (3) `_apply_wash_sale_prorate` — distributes the per-ticker pool **FIFO by acquired date** across losing lots of the ticker, computing per-lot `disallowed_loss = matched_qty/lot.quantity × unrealized_loss`. **The proration matters:** a 1-share DRIP buy against a 100-share losing lot disallows 1% of the loss, not 100% (a binary block would wrongly forfeit the whole lot — `partial`-status lots stay harvestable). Status is `clear` / `partial` / `fully_blocked`; only `fully_blocked` is excluded from the eligible set, and `wash_sale_blocked_loss` is now the sum of the *disallowed* slices (so `total_loss + blocked = all unrealized losses` reconciles). Same-ticker is the "substantially identical" proxy and different-CUSIP ETF swaps are NOT flagged; basis-shift on disallowed losses (the §1091 deferral to replacement-share basis) is documented but NOT modeled in v1 (affects future-year Schedule D only). Aggregate logic re-does the Schedule D netting via `_net_capital_gains(new_st, new_lt)` and expresses the harvest as a delta to `compute_scenario` (`capital_gains` for LT-loss flow, `other_income` for the recovered net ST), so the $3K/$1.5K-MFS loss cap, character preservation, and LTCG bracket positioning come "for free" from the keystone. **State savings** is a simplified estimate (state ordinary marginal rate × federal taxable-income reduction, where federal taxable-income reduction is the keystone's actual `Δ taxable_income` so second-order effects like QDCG shifts flow through) under the v1 assumption that most states tax cap gains as ordinary; degrades to $0 with a loud banner when `tax_law.state.brackets` is empty. Per-lot `est_value_per_dollar` is a ranking heuristic only (ord_mtr for ST, ltcg_mtr for LT, plus state_mtr if modeled); ranking key is `value_per_dollar × allowed_loss` so partial lots rank by their harvestable portion, not their gross loss. The aggregate `fed_savings` does the real anchored math.
- "Section present" for diff/presence logic = holds a non-zero number or non-empty list; template scaffolding (strings, booleans, `_`-prefixed keys) never counts.
- `/tax-calendar` (`agents/lib/tax_calendar.py`, backlog #10) is the "minimal write-only" sibling: pure compute (frozen `CalendarEvent` dataclass + per-category builders + `build_events` orchestrator + `to_ics` RFC-5545 serializer) + CLI `_main` + workspace-bounded `write_calendar`. Reuses `rollover._validate_subpath` + `_validate_year_arg` (the same boundary check every other write-derived skill uses), catches the `RolloverError` and re-raises as `CalendarError` to keep this module's error surface consistent. Writes only `tax_calendar_{year}.ics` at the workspace root (NOT under `scenarios/` — it's a top-level deliverable for the taxpayer, not a planning artifact). UIDs use the deterministic `{category}-{key}-{year}@tax-prep-skills` slug so re-importing is idempotent. **Key schema decision: PTET reminders are driven by `flags.k1_states` (entity-state list — where each K-1 partnership/S-corp files), NOT by `address.state` (taxpayer's resident state). A TX resident with a CA K-1 needs CA PTET reminders.** RMD reminders are driven by user-asserted `subject_to_rmd_*` booleans, NOT a derived age check, because RMD start age is birth-year-cohort-dependent under SECURE 2.0. **§7503 next-business-day rollover IS applied (L1, PRODID v4):** federal deadlines (1040-ES, extension, FBAR) roll past weekends + DC legal holidays via `irs_dates.next_business_day`; state deadlines (CA 540-ES, CA/NY/NJ PTET) roll under the JURISDICTION's own holiday set via `irs_dates.next_business_day_state`, which now requires a state code. **CA adopts the FEDERAL set** (R&TC §18410: "a legal holiday includes a federal legal holiday recognized by the Internal Revenue Service under Section 7503"), which FTB applies by name in the 540-ES instructions — its published TY2023 quarters are Apr 18 2023 / Jun 15 2023 / Jan 16 2024, rolling past observed DC Emancipation Day and MLK Day respectively. The former blanket "WEEKENDS ONLY, DC holidays don't bind state authorities" rule was wrong for CA and published its dates up to three days EARLY. NY/NJ keep weekend-only as a labelled DEGRADE (statutes unread), and the description caveat is now jurisdiction-aware — it was a lying caveat on CA); RMD Dec 31 does NOT roll. The `.ics` DTSTART is the observed date; descriptions note the statutory-vs-observed shift. CA/NY/NJ PTET only; first-year RMD April-1 deferral not modeled. Federal + 3-state.
- `/tax-ptet` (`agents/lib/ptet.py`, backlog #9) is the FIRST **read-only-AND-writes-nothing** planning skill — advisory output (a `/tax-review`-style section), so unlike scenario/roth-ladder/harvest it has no `save_scenario` and no `scenarios/` artifact. Reuses `scenario.extract_baseline_inputs` + `_rate_kwargs` + `read_tax_year` and `rollover._validate_subpath`/`_validate_year_arg`, and ANCHORS the federal benefit like the other three: the entity-level PTET deduction is a negative `other_income` delta through the `compute_scenario` keystone, so the reported $ is the actual marginal effect around `result.json` line 24 (tapers as a large deduction falls through brackets). Three primitives: (1) `ptet_tax(state, income, rate_override=)` — CA flat 9.3% vs NY/NJ graduated `(lower, rate, base_tax)` schedules; NJ BAIT year-aware via `_schedule_for` (TY2022+ single 10.9% over $1M vs pre-2022 4-bracket `_NJ_SCHEDULE_LEGACY_2020`); fetched `state.ptet` cache wins over the statutory schedule via `_resolve_schedule`/`_validate_fetched_schedule`. (2) `_election_deadline(state, year)` — reuses the `/tax-calendar` statutory facts (NY's election due March 15 *of the tax year*, can't be made on the return — the trap; CA year-aware: AB 150 ≤2025 June-15 prepayment is an election PREREQUISITE, SB 132 2026-2030 the election survives a missed payment but the credit drops 12.5%, TY2021 had no prepayment requirement) — wired through the rollover with `/tax-calendar` so both print the same rolled date, now under the JURISDICTION's own holiday set (`_obs_date`/`_state_due` both take a required state code and delegate to `irs_dates.next_business_day_state`; CA resolves to the FEDERAL set per R&TC §18410 — so a CA PTET date rolls past MLK Day and observed DC Emancipation Day, matching FTB's published 540-ES dates — while NY and NJ keep the weekend-only DEGRADE, their statutes being unread here. `ptet._obs_date` previously held a fourth inlined copy of the weekend-only loop). (3) the **forgone-SALT offset** = `marginal_fed × min(salt_room, total_ptet)`, `salt_room` defaulting 0 (cap exhausted) but OBBBA-aware via `_salt_cap(year,status,cfg,magi)`, a wrapper over `tax_computations/federal/salt.py` — the §164(b)(7) schedule and its MAGI phasedown (`--magi`), the one rule the return's Schedule A also applies; `federal.salt_cap` cache preferred for the amount; `--other-salt` derives headroom, `--salt-room` wins). **Bias:** cap-exhausted default OVERSTATES savings → loud `ASSUMPTION` note; offset valued at the top marginal rate can only *reduce* net savings (safe); offset gated on `itemizes` (`--standard-deduction`). §199A QBI clawback (`--qbi-eligible STATE`, priced `PTET × (1 − qbi_rate)`, default-off overstatement caveat). Gross benefit allocated pro-rata by each state's *income reduction*, SALT offset by *PTET paid* (both reconcile). `PTET_WINDOWS` gating, from the state registry and shared with `/tax-calendar` (CA 2021-2030 / NY 2021+ / NJ 2020+) → out-of-window `modeled=False`; non-CA/NY/NJ also `modeled=False` (never a silent $0). Phantom-income guard PREFERS `schedule_e_k1_income` over `line_8_schedule1_other`/`other_income` and hard-errors when `--income` exceeds that pass-through bucket. `_est_marginal_fed` probes `other_income` (not wages — avoids folding the 0.9% Medicare tax) with a local $100 probe + AUTO-ESTIMATED near-cliff caveat. `_parse_state_amounts` `combine="sum"` for `--income` / `"reject"` for `--ptet-rate`, `_normalize_state` validates codes at the parse boundary + an orphan-override guard; `--marginal-fed` via `_validate_rate`; all numerics via `_strict_non_negative`. State savings hardcoded $0 (the owner's PTET credit offsets the entity tax). Full round-by-round review history in `docs/changelog/pre-epic.md`.

### The one engine-free skill

- `/tax-safe-harbor` is the only skill in the toolkit that calls NO engine module, and
  the only one that needs no workspace at all — no `result.json`, no
  `tax_law_<year>.json`, no `taxpayer_profile.json`. It answers one question ("will an
  underpayment penalty attach, and what closes it?") from figures the user reads off
  their own prior-year Form 1040, and its FEDERAL answer is built to be executable when
  `SKILL.md` has been copied somewhere on its own. Its California half is the one thing
  that is not: it outgrew the 500-line body and sits in `references/california.md`, behind
  a step-5 gate that REFUSES the CA figure when the file cannot be opened. That refusal is
  the point — the federal answer stands alone, and a California one improvised from the
  federal weighting invents instalments the state does not have.

  A second reference joined it for the same reason: `references/withholding-cure.md`
  holds route A's SECOND figure — the 4(c) amount that cures an instalment already
  passed, which the year's own gap does not size. Withholding credits ratably
  (§6654(g)(1)), so a late estimate meeting the annual requirement while leaving April
  short makes `still to pay` $0 against an instalment still accruing, and 4(c) sized
  from it alone answers $0 to the question the skill exists to answer. Its gate degrades
  rather than refusing, unlike California's: the federal answer without it is the year's
  gap, which is right whenever no deadline has passed and understated otherwise, so the
  body says that in one sentence rather than withholding a figure that is usually
  correct.

  **Why it is allowed to restate tax law in prose, when AGENTS.md §2 forbids
  hardcoding a figure.** The prior-year prong of IRC §6654(d)(1)(B)(ii) is the one
  useful calculation here whose parameters are STATUTORY AND UNINDEXED — the
  $150,000/$75,000 prior-AGI threshold and the 110%/100% factor do not move with
  inflation the way a bracket does. Every other planning calculation needs current-year
  law, and in a channel with no trustworthy fetch that means it either lies or refuses.
  This one needs none, so there is no current-year figure available to go stale.

  **Bias direction.** The law allows the LESSER of the prior-year amount and 90% of the
  current year's actual tax. The skill computes only the prior-year side, so its answer
  can be too high and never too low. Overpaying the harbor credits against the tax
  actually due and comes back only insofar as it passes it; an underpayment accrues a
  penalty instead. That asymmetry is the whole licence for the skill to operate outside
  the engine, and the body states it to the reader rather than only here.

  **The exemption is a declaration, not an absence.** `ENGINE_FREE_SKILLS` in
  `tests/skill_text.py` names it, and `test_preflight.py` asserts of every member that
  its text reaches the engine by neither path nor module — so membership cannot silence
  the gate on a skill that needs it, and a skill that GROWS an engine call fails there
  instead of quietly losing its gate. Without the declaration, "engine-free by design"
  and "lost its engine call in an edit" are the same observation: both simply fall out
  of the derived roster. The declaration alone was not enough, though, because every
  assertion about it ranges over a set a regression SHRINKS — delete the name and nothing
  requires the skill to be gated OR declared; delete a skill's last engine call and the
  derived roster's floor of 20 absorbs it. `test_the_three_rosters_partition_the_skills_tree`
  is the statement about the whole directory that closes both: engine-calling,
  engine-free and contributor are exhaustive and disjoint, so a skill can only leave one
  roster by joining another, and every such move is an edit to `tests/skill_text.py`.

  **Two statements of one rule, pinned.** The prose restates parameters the engine also
  applies, so `test_portability.py::TestTheAdvisorySkillRestatesTheEnginesLawExactly`
  derives the expected strings from the constants in the module that OWNS each one —
  `PRIOR_YEAR_AGI_THRESHOLD{,_MFS}`, `PRIOR_YEAR_FACTOR{,_HIGH}` and `DE_MINIMIS_BALANCE`
  from `agents/lib/tax_computations/`, which holds the §6654 figures and which
  `projection` deliberately does NOT re-export, and `CA_ES_SCHEDULE`,
  `CA_HIGH_INCOME_AGI_LIMIT{,_MFS}`, `CA_ES_DE_MINIMIS{,_MFS}` from
  `agents/lib/projection/` — and additionally runs
  the skill's own worked example back through `compute_safe_harbor` and
  `compute_w4_4c`. Prose can state every threshold correctly and still show a
  multiplication whose product is wrong; only the second half catches that.

  **Citations verified live 2026-08-17** per AGENTS.md §2, and unindexed is not immortal —
  re-verify when the skill's figures are next touched. §6654(d)(1)(C) ($150,000/$75,000 →
  110%), §6654(g)(1) (an equal part of the year's withholding deemed paid on each due
  date, unless the taxpayer establishes the actual dates — the skill states that
  election), §6654(c)(2) (Apr 15 / Jun 15 / Sep 15 / Jan 15) and §6654(e)(1) (the $1,000
  no-penalty floor) against `law.cornell.edu` and IRS Topic 306; CA's 30/40/0/30 with no
  third installment, the 110% at $150,000/$75,000 prior-year CA AGI, the
  $1,000,000/$500,000 §19136.3 limitation and the $500/$250 floor against FTB's 5805 and
  540-ES instructions. The §6654(g) claim came out of that pass: it had been stated
  flatly, without the taxpayer's election to establish the actual withholding dates,
  which AGENTS.md's "a lying caveat is worse than a missing one" rule makes a defect
  rather than a simplification. A second pass the same day added §6654(h) (the January 31
  file-and-pay rule, fourth instalment only), §6654(i) with §6654(i)(2) (farmers and
  fishermen: one January 15 instalment, 66⅔%, subparagraph (C) disregarded), §6654(e)(3)
  and Form 2210 Part II's waiver grounds, and the Form 2210 line 8 instruction for the
  refundable-credit netting — the statute against `law.cornell.edu`, the rest against the
  Instructions for Form 2210 and IRS penalty-relief guidance, which is where First Time
  Abate's exclusion of the estimated-tax penalty is stated. A third pass read the 2025
  Form 5805 itself for the withholding-credit question above, and picked up two things
  while there: the $500/$250 floor is stated on the form as reading EITHER year, and
  filing and paying before February 1 clears the January instalment — CA's own §6654(h).
  Note that `ftb.ca.gov` answers 403 to the repo's fetch tooling; the form and its
  instructions come back to a plain browser-agent request, so a CA citation is verifiable
  and the 403 is not evidence of anything. A fourth pass read the Instructions for Form
  2210 line by line for the intake: line 8's refundable-credit roster including Form 8839
  line 13, line 6's "withheld federal income tax and excess social security or tier 1
  RRTA ... one-fourth of these amounts on each payment due date", and line 11's
  credit-elected overpayment with both dating rules. The same pass fixed the accrual END
  against §6654(b)(2) — the instructions' own penalty worksheet stops at Rate Period 4
  (January 1 to April 15) and dates a balance-due payment "the date you filed ... or
  April 15, whichever is earlier". CA's farmer branch is FTB 5805 General Information G
  (two-thirds for either year, one instalment due January 15, March 1 file-and-pay) plus
  Form 5805F itself, read for STRUCTURE rather than figures: line 4 is 66⅔% of the
  current year, line 9 the prior year's tax with no factor applied anywhere on the form,
  and line 10 "the smaller of line 4 or line 9" with the $1,000,000/$500,000 parenthetical
  forcing line 4. Only the 2023 5805F was retrievable — the 2024 and 2025 PDFs 404 at the
  same path pattern while the 2025 5805 instructions are up — so the year-specific figures
  on it were not used and General Information G was taken as the current statement.
  A fifth pass settled four things a review round put in doubt. Line 8's instruction reads
  "enter 110% of the amount of the tax computed earlier", which is what makes line 8 the
  OUTPUT of step 2 rather than the input to it. Its refundable-credit roster names EIC,
  ACTC, the refundable AOTC, net PTC and the fuel credit and does NOT name the adoption
  credit — correct for a form whose prior year is 2024, because OBBBA made up to $5,000 of
  it refundable (Form 8839 line 13, 1040 line 30) only for tax years beginning after
  2024, which is where the year gate on that roster entry comes from. §6654(e)(2)'s text
  at `law.cornell.edu` has exactly three conditions — 12-month prior year, no liability for
  it, US citizen or resident throughout — and no filing requirement, which is the ordering
  fix. And the applicability floors: the amendment notes to §6654(d)(1)(C) carry the former
  percentage schedule (105% for a prior year of 1998, 108.6% for 1999, 110% for 2000, 112%
  for 2001, 110% for 2002 and thereafter), R&TC §19136.3's $1M limitation took effect
  for taxable years beginning on or after January 1, 2009, and §19136.1 set 30/30/20/20 for
  taxable years beginning on or after January 1, 2009 and before January 1, 2010, reaching
  30/40/0/30 only from January 1, 2010. Line 11's dating rule was
  re-read in the same pass and is about the MONEY, not the filing: April 15 where the prior
  year was fully paid before then, the payment's own date where the overpayment arose from
  a later payment, and no separate late-filing rule — `/tax-compute`'s contract carried one
  and was corrected.

  A sixth pass re-read the three provisions this round's fixes rest on, all at
  `law.cornell.edu` except the last. §6654(e)(2) again, for its noun: the condition is that
  the individual "did not have any liability for tax for the preceding taxable year", and
  the Instructions for Form 2210 render it "You had no tax liability for 2024 ... (or would
  have been had you been required to file)" — a statement about the year's TAX, not about
  the balance settled with the return, which is what makes "I owed nothing" an inference
  rather than an answer. §6654(b)(2) for the catch-up: the period of the underpayment runs
  from the instalment's due date to the earlier of the following April 15 and "the date on
  which such portion is paid", so payment is what stops it, and the same instructions say
  the penalty "is figured separately for each installment due date" with the days each
  underpayment "remained unpaid". And R&TC §19136(a) for California: "Section 6654 of the
  Internal Revenue Code, relating to failure by an individual to pay estimated income tax,
  applies, except as otherwise provided" — §19136.1's weighting and §19136.3's limitation
  are what is otherwise provided, and neither touches (b)(2), so the accrual-until-paid
  rule is California's too. `ftb.ca.gov` still answers 403 to the repo's fetch tooling, so
  the CA half of this pass is the statute rather than the form.

  A seventh pass verified the one rule the intake had been silent about, and it is not in
  the statute: which RETURN "the tax shown on the return of the individual for the
  preceding taxable year" means once that return has been amended. IRM 20.1.3.3.1.1
  ("Determining the Required Annual Payment") states it — "Preceding taxable year's tax
  refers to the tax shown on the taxpayer's original return, or shown on an amended return
  for the previous year if filed prior to the due date for that year, including
  extensions." So the ordinary late 1040-X does not move figure 1, and asking for "last
  year's filed return" collects the copy that does. A downward amendment adopted into
  figure 1 lowers the guaranteed payment, which is the failure direction the skill's
  licence rules out. The same pass confirmed the CA accrual end on FTB's own 5805
  instructions rather than only through §19136(a) conformity: the penalty runs "from the
  due date of the installment to the date of payment or the due date of your tax return,
  whichever is earlier", which is what lets step 0 say a January 15 voucher stops being an
  action for either jurisdiction on the same day.

  Step 0's handoff gained one carve-out with the gate: both notes point at the cumulative
  test, and a reader who follows that pointer was bounced straight back by the workspace
  gate to the table the note had told them to distrust. A reader arriving FOR step 4 (step
  5 in California) now gets it and is handed back, which is the only thing the other tools
  cannot give them. The §6654(e)(1) $1,000 floor also stopped being a second copy:
  `_F2210_DE_MINIMIS` was left private and form-named by the revision that made its
  neighbours public — inside the comment block explaining why neither is right — while the
  engine's own no-penalty message retyped "$1,000" two lines below it. It is now
  `DE_MINIMIS_BALANCE`, interpolated into that message, and the skill's step 6 figure is
  derived from it by the same parity test that covers the factors and thresholds. AGENTS.md
  §2's governing exception list gained its second and last member at the same time: the
  list says in terms that adding an exception means changing it, and the skill had been
  shipping statutory figures in prose against a list of one.

  Two more came from the PR review, both scope defects rather than arithmetic. The farmer
  branch ended "skip step 4", which takes away the timing check and, with it, the only
  instruction to pay a missed January 15 — correct only for the taxpayer who can use the
  March 1 escape. §6654(i)(1)(D) applies §6654(h) with "March 1" for "January 31", so
  filing and paying in full by then removes that installment's penalty; once it has passed,
  §6654(b)(2) leaves the underpayment accruing until PAID (or the following April 15).
  Step 4 now runs against that ONE deadline — the year's whole withholding deemed paid on
  January 15, being the only installment date under §6654(g)(1) — and the shortfall is
  given as an amount to pay now. And the CA branch keyed on RESIDENCE, so a taxpayer with
  California-source income living elsewhere collected no CA figures and read the
  every-other-state paragraph about their home state, with the state they actually owe
  unmentioned. Form 540NR is out of scope for the toolkit, and AGENTS.md's rule is that out
  of scope is SAID with the form named rather than falling through quietly.

  A seventh pass read the FORMS rather than the law, at `irs.gov/pub/irs-prior/`: the 2019
  Form 1040 (AGI line 8b, taxable income 11b, total tax line 16, **estimated tax penalty
  line 24**), 2018 (line 7 and line 15), 2017 (line 37 and line 63), 2013 (line 37 and line 61 — total
  tax moves again below 2017, which is why the skill stops naming numbers there), 2020 (line
  11 and line 24, the layout still current) and 2025 (AGI printed at line 11a and repeated at
  11b, total tax still line 24). The same pass took the joint/separate rules from Pub 505 chapter 2
  ("your 2025 tax is the total of the tax shown on your separate returns", and the
  no-liability definition "your total tax ... was zero or you didn't have to file an income
  tax return") and the per-return ordering of the factor from the Instructions for Form 2210
  line 8. Neither §6654(d)(1)(C) nor Reg. §1.6654-2(e), which states the tax rule for both
  status changes, says anything about which AGI applies — line 8's ordering is the only
  authority for it, which is why the skill cites the instruction rather than reasoning from
  the statute.

  **The $1,000 floor is a REFUSAL, not a test this skill applies, and the reason is the
  base.** §6654(e)(1) reads CURRENT-year tax reduced by withholding — `compute_form_2210`
  applies exactly that (`tax - wh`, withholding only) — and the only figure this skill has
  is a shortfall against the PRIOR-year prong. A draft applied the floor to that
  shortfall, which would have told a user with a $600 gap that no penalty attaches while
  their current-year tax sat tens of thousands over withholding: an UNDER-statement, in
  the one direction this skill's whole licence says is impossible, and one that triggered
  twice as easily besides, because the shortfall also nets estimated payments. Step 6 now
  hands the user the rule and the two figures it needs. The lesson generalises to anything
  added here later: a statutory test is importable into this skill only if its BASE is one
  of the prior-year figures.

  **Preconditions, for the same reason.** §6654(d)(1)(B)'s closing text withdraws the
  prior-year prong entirely when the prior year was not 12 months or no return was filed,
  and a filing-status change between the years means the prior-year tax must be summed
  (MFS→MFJ) or allocated (MFJ→MFS) per Pub 505. Each failure makes the figure too LOW, so
  step 1 screens for them before anything is computed. §6654(e)(2)'s prior-year-tax-of-$0
  case is a complete answer and short-circuits.

  **The prior-year figure is what Form 2210 line 8 is computed FROM, not line 8 itself.**
  Line 8's own instruction is "enter 110% of the amount of the tax computed earlier", so it
  already carries the §6654(d)(1)(C) factor that step 2 is about to apply — a user holding
  an old 2210 who reads line 8 off it and hands that over gets the factor twice, a 10%
  over-statement of exactly the cry-wolf shape argued against below. An earlier draft
  labelled figure 1 "the Form 2210 line 8 figure" and had no way to catch that;
  `test_figure_one_is_what_line_8_is_computed_from_not_line_8` now asserts the distinction
  in both directions. What figure 1 IS: the prior
  year's tax NET of its refundable credits (EIC, ACTC, the refundable part of the AOTC,
  the refundable part of the ADOPTION credit, net PTC, the fuel credit, §1341(a)(5)(B)),
  floored at zero — the same intake contract `/tax-compute` already states for
  `prior_year_tax` in `references/tax-and-credits.md`, and the Form 2210 line 8
  instruction ("first add the amounts listed in (1), then subtract the refundable credits
  listed in (2)"). A draft asked for raw line 24, which reads as harmlessly conservative
  and is not: on a return where refundable credits met or exceeded the tax, the netted
  figure is $0, so 100%/110% of it is a required annual payment of $0, and the raw figure
  instead demands 110% of a tax the taxpayer never owed. That $0 is the ORDINARY prong, not
  §6654(e)(2) — an earlier revision of this paragraph said §6654(e)(2), and six instruction
  sites downstream inherited it. The exception needs NO LIABILITY, which reads off the
  TOTAL TAX line ABOVE those credits; the netted figure reaches $0 while that line is
  positive, so for THIS taxpayer the exception fails on LIABILITY, and where no return was
  filed the ordinary prong is gone as well, leaving 90% of the current year as the whole
  test. Read the other way round — filing deciding the exception — that second clause is
  false, and step 0 read it that way for a round: §6654(e)(2) asks nothing about a return.
  An over-statement that turns "you are safe" into "pay $3,300" is the cry-wolf shape,
  not the safe direction.

  The adoption credit arrived a draft later, and it is why the roster is now pinned to
  `/tax-compute`'s rather than written out beside it: Form 8839 line 13 became refundable
  in part, the contract already carried it, and this list was a hand-copy that predated
  it. `TestTheAdvisorySkillsIntakeIsTheForm2210Intake` asserts every credit in BOTH
  documents, matching per-credit spellings so neither is forced into the other's house
  style ("EIC" against "earned income credit").

  **And it is the one roster entry with a YEAR on it.** OBBBA made the adoption credit
  partly refundable — Form 8839 line 13, up to $5,000 — only for tax years beginning after
  2024. On any earlier PRIOR return the credit was wholly nonrefundable and already inside
  line 24, so subtracting line 13 there removes it twice: figure 1 falls, the harbor falls
  with it, and the taxpayer under-pays. That is not a remote case — a covered year of 2025
  has a prior year of 2024, and the skill's own description invites exactly that window. So
  the netting instruction is gated on the prior year rather than stated flat, and the test
  asserts the gate and the "was nonrefundable" explanation beside it, since a reader with a
  2024 Form 8839 in hand needs to know which side of the line it falls on. The published
  Form 2210 line-8 credit list does not name the adoption credit at all, which is CORRECT
  for a form whose prior year is 2024 and will stop being so for the next one — the roster
  here is §6654(f)'s "reduced by the credits against tax", not a copy of that enumeration.

  **The other two intake figures are Form 2210 lines 6 and 11, and both were short.**
  Excess social security / tier 1 RRTA withheld by two employers (Schedule 3 line 11) is a
  §31(b) credit for tax WITHHELD, so line 6 counts it with the withholding that spreads
  ratably — the contract flags the same trap from the other side ("excess SS/RRTA is NOT
  here"), and the test asserts the asymmetry by slicing figure 1 and figure 4 apart: in
  figure 1 it would shrink the harbor, absent from figure 4 it inflates the gap. A
  prior-year overpayment the taxpayer elected to apply is likewise an estimated payment
  with no voucher behind it, dated April 15 where the prior return was paid in full before
  then and otherwise the date of the payment that created it (Form 2210 line 11). Both
  omissions overstate what is owed, and the skill's "too high, never too low" licence
  covers its choice of PRONG — not arithmetic on an intake missing a line.

  That dating rule is when the MONEY went in, not when the return was filed, and the
  contract said otherwise ("if it was filed LATE, use the actual filing date"). Form 2210's
  line 11 instruction is explicit: treat it as paid on April 15 where the prior year was
  fully paid before then, and on the payment's own date where the overpayment arose from a
  payment made after it. A return on extension whose tax was already covered by withholding
  therefore credits on April 15; dating it to an October filing would overstate every
  earlier instalment's shortfall. The contract was corrected rather than the skill.

  **Farmers and fishermen are routed OUT, and the schedule is why.** §6654(i) gives them
  one instalment, due January 15, and applies (d)(1) *without regard to* subparagraph (C)
  — so the 110% factor never applies to them however high the prior-year AGI, the
  current-year prong drops to 66⅔%, and filing and paying in full by March 1 removes the
  penalty outright. The dangerous half is the calendar: step 4's four-deadline timing
  check would report April, June and September underpayments that do not exist, which is
  the same cry-wolf failure as above rather than a conservative one. Step 1 screens on
  the two-thirds-of-gross-income test for EITHER year (§6654(i)(2)), and names Form
  2210-F rather than attempting it.

  **California routes them out too, and `_ca_notes` was already saying so.** FTB puts a
  farmer or fisherman on Form 5805F: one instalment due January 15, the whole tax paid by
  March 1 removing the penalty, and — read off the form's own lines rather than inferred
  — the prior-year prong at 100% with no 110% step-up at any AGI, against a current-year
  prong of 66⅔% this skill cannot compute. §19136.3's $1M/$500K limitation survives (line
  10's parenthetical sends those filers to the 66⅔% line), so above it there is no CA
  answer at all. The engine prints the same exclusion beside every CA voucher run ("this
  30/40/0/30 schedule does not apply to them"), which is what
  `test_the_california_farmer_is_routed_off_the_weighted_schedule` derives from — and it
  asserts the branch appears BEFORE the weights, since a branch stated after them is read
  after the wrong schedule has been applied.

  **Instalments are allocated CUMULATIVELY, which is the one place the skill declines to
  follow the engine.** `compute_estimated_vouchers` and `compute_ca_estimated_vouchers`
  spread the annual shortfall over the deadlines still open, and each docstring now names
  the simplification — the federal one did not have a docstring at all until step 0 claimed
  it did, which is the shape of claim this repo treats as a defect in itself. Each
  instalment is tracked cumulatively, so an even split puts less at the earliest open
  deadline than that deadline requires. In `/tax-project` that is a documented
  approximation with a computed return behind it; in a skill whose whole licence is "too
  high, never too low" it is an UNDER-statement, so step 4 walks the deadlines instead —
  `required x n/4` against `expected full-year withholding x n/4` plus estimates dated on
  or before, floored at zero — 30/70/70/100 on BOTH sides for CA, per FTB 5805 Worksheet II
  lines 1 and 2. Crediting the full year's withholding at n/4 is not an approximation but
  §6654(g)(1) itself, which is also why raising withholding in September cures April. `TestTheAdvisorySkillAllocatesInstalmentsCumulatively`
  pins the worked figures against that identity, deliberately NOT against the engine: a
  later reader harmonizing the prose with `compute_estimated_vouchers` would be putting
  the understatement back, and the numbers stop satisfying the identity when they do.
  The engine's own even split is left as a finding — it moves two other skills' output —
  but it no longer ships SILENT: the federal voucher table now renders
  `_ES_VOUCHER_CATCHUP_NOTE` the way the CA table has always rendered its twin, so a
  `/tax-project` user who never opens this skill still sees the understatement marked.
  AGENTS.md's rule is that an under-stating fallback degrades with a banner; a warning
  that existed only inside the skill step 0 exists to redirect people AWAY from was not
  one.

  Both notes are gated on being TRUE of the schedule beneath them
  (`_voucher_catchup_note`), because a banner is a claim and a claim that fires where it
  is false is the OVER-stating direction AGENTS.md treats as a hard error. Rendered
  unconditionally they fired on an all-zero schedule, where the harbor is already covered
  and "pay catch-up amounts NOW" asks for money that avoids nothing. That same all-zero
  guard is what carries BOTH de-minimis floors: under §6654(e)(1) or R&TC §19136 the
  harbor zeroes its shortfall, the schedule prints zeros and the note goes quiet without
  the gate needing to know which statute excused it — which is why the fix went in at the
  harbor-to-voucher boundary rather than as a second condition on the note.

  That gate then tested only the earliest STILL-OPEN instalment, and so went silent exactly
  where the overdue amount was largest. After September 15 the CA schedule has ONE
  instalment left and it carries the whole annual requirement, so the printed amount equals
  what January asks and the open-side comparison passes — while 70% of it (30% + 40%) fell
  due in June. A $20,000 requirement with nothing paid printed $20,000 at January 15 and
  said nothing about the $14,000 accruing since; the federal schedule hid 75% at the same
  date the same way. The LAST PASSED instalment is tested too now, and it asks what is
  outstanding NOW rather than what was ever late: money already in cures an earlier
  instalment going forward, so a June gap covered by September sits behind a penalty that
  is already fixed and directing a payment for it would be the over-stating direction
  again. Both sides are mutation-pinned, the check removed and the check made
  unconditional.

  **The first gate was a PROXY for the claim, and proxies were wrong in both directions.**
  "Some deadline has closed and something is still owed" reads as "the earliest still-open
  instalment must be short", and it is neither necessary nor sufficient. Not sufficient:
  the schedule targets what remains of the ANNUAL requirement, so a taxpayer whose earlier
  estimated payments already satisfy the cumulative test still gets non-zero vouchers for
  the rest of the year — $20,000 required, $15,000 paid by July 1, nothing whatever overdue
  at September 15 — and the proxy directed an immediate catch-up against it. Not necessary,
  and this is the half that matters more: the schedule can be short at a deadline that has
  ALREADY closed, which the proxy also missed whenever the remaining vouchers happened to
  be zero. The gate now RUNS the cumulative test — the requirement due by a deadline, less
  what counts by then, against the amount printed beside it — at the earliest still-open
  instalment AND at the last passed one. What the engine still cannot see is WHEN `paid`
  went in, so both notes send the reader to the dated test rather than printing a figure of
  their own.

  **And "nothing outstanding" is not "nothing was late", which is where the all-zero guard
  went too far.** A $20,000 requirement, no withholding and a single $20,000 estimate meets
  the ANNUAL test, zeroes every voucher and returned None — deleting the pointer to the
  dated test in exactly the run that needs it, since that estimate may have landed on
  September 30 with April, June and September all short and their penalty already accrued.
  The gate now returns a timing note there instead of silence, gated on
  `estimates_paid > 0` (withholding cannot produce the case — §6654(g)(1) and FTB 5805
  Worksheet II line 2 both spread it across the instalments, so a year it covers is covered
  at every one) and gated OUT under a de minimis, which remains the one case where quiet is
  the whole answer. That makes `voucher_catchup_note` ONE OF TWO notes, and both skills say
  so: the second must never be summarized as "nothing to do".

  **Every one of those notes is about the AMOUNT on a row, and none of them looks at the
  DATE beside it.** `_voucher_dates_note` is the other half, and the two are independently
  true — a schedule can be correctly split and entirely obsolete. It matters most where the
  catch-up note structurally cannot reach: /tax-withholding prints TWO columns against ONE
  set of dates, and the catch-up note is scoped to the penalty column, which is all $0.00
  whenever the harbor is covered or under a de-minimis floor. The zero-balance column beside
  it still carried the whole balance under the same passed deadline, with the blurb above
  the table pointing the reader at exactly that column once no paychecks remain — so the
  dates note takes a LIST of schedules and reads the TABLE, not a column. `resolve_as_of`'s
  closed-year default is what made that the ordinary run rather than an odd one: as-of 12-31
  leaves one instalment printed at January 15 of the following year, `--as-of` is MM-DD
  INSIDE the covered year and so can never reach it, and only a real `today` can close it.
  It carries TWO texts and the second INVERTS the first. Up to the covered year's return due
  date a passed instalment is to pay NOW; past it §6654(b)(2) has closed the accrual period,
  the penalty is FIXED, no voucher reduces it and what remains is a balance due — so the
  closed text opens by overriding the catch-up note printed below it in as many words, and
  both renderers print dates before amounts. That fixed-penalty sentence is qualified on
  EVERY closed year rather than on the two in `_RETURN_DUE_POSTPONED`, and the caveat sits
  immediately after it rather than at the end of the note. The table holds the IRS-WIDE
  postponements; §7508A relief is overwhelmingly the county-scoped kind, dozens of notices a
  year, and nothing reaching the note says whether this taxpayer was inside one — so keying
  the hedge on the table printed "not postponed" where the truth was "not known", on the
  strongest claim either report makes, for a taxpayer whose own notice may still be running.
  The table now chooses the LEAD only. It is not collected as an input either: the only
  person who can answer is the one the caveat sends to the notice, and a flag reading "no
  disaster" while unanswered deletes the hedge for everyone who never saw the question. Both comparisons run through §7503
  (`_observed_due`), because the schedules print BARE statutory dates and a bare comparison
  calls an instalment missed for up to four days during which it was not.

  **That caveat covers TWO claims, and only one of them is a doubt.** Hedging "the penalty
  is FIXED" left "this schedule is obsolete" and "Do NOT send a voucher — its instalments
  belong to dates that are all behind us" standing flat one sentence later, and both are
  false for exactly the taxpayer the caveat was written for. Whether §7508A(a)(2) reaches
  §6654(b)(2)'s accrual endpoint is genuinely open; whether §7508A moves the INSTALMENT is
  not, §7508A(a)(1) reaching §7508(a)(1)(B), "payment of any income ... tax or any
  installment thereof". A covered instalment is payable on its postponed date and paying it
  then is TIMELY — so the note argued its reader out of the one action still open to them,
  at the full §6654 rate. The condition now LEADS that instruction instead of trailing it,
  and the header says "obsolete on the STATUTORY calendar", the header being the line a
  summarising agent lifts whole. The other three branches stay unhedged deliberately: a
  postponement can only push a printed date LATER, so "pay NOW" and both balance deadlines
  over-state urgency, and a disaster caveat on advice that is already safe is the cry-wolf
  shape this note otherwise exists to avoid.

  **`today` is OPTIONAL on both `build_report`s and absent means SILENT.** Reading the clock
  inside them would make a pure function non-deterministic and break the resume the CLI's
  own `--today` override exists for; asserting the alarming half without a date to justify
  it is the over-stating direction.

  **And "prefer withholding" is the wrong lead once there is no payroll left.** Both
  renderers opened "Recommended actions" with the §6654(g)(1) cure unconditionally, directly
  above a 4(c) figure reading $0.00/paycheck over 0 remaining paychecks. The clause "when
  paychecks remain" makes that defensible rather than false, which is worse in a heading —
  the reader takes the recommendation and finds the number blank. `elapsed_fraction` is
  exactly 1.0 at 12-31, so the closed-year default guarantees it. `_w4_lever_note` is one
  shared pair returning the lead or its inversion, keyed on `paychecks_remaining` and never
  on a per-paycheck figure of $0.00, which a fully covered mid-year run also prints.

  **That timing note then over-stated in its first sentence**, which is the shape this
  module keeps producing: a text written for one state of the schedule, emitted in every
  state the gate reaches. It opened "Nothing further is outstanding against the ANNUAL
  requirement" on a schedule still printing instalments — $20,000 required with $15,000 in
  by July 1 leaves two $2,500 vouchers directly beneath that sentence. So there are two
  ledes over one body per jurisdiction (`timing_settled` / `timing_scheduled` on
  `_CatchupNote`), the settled one reserved for an all-zero schedule; and the gate now
  returns nothing at all while `vouchers[0].quarter == 1`, because before the first deadline
  passes every payment made is timely for every instalment and the caveat warns about a
  window that has not opened.

  **The same round then got California's fractions backwards, and that correction is the
  one to read.** It reasoned that CA's 30/40/0/30 governs only what is REQUIRED and that
  withholding still credits in quarters, making April short by 5% of the year's withholding
  before any deadline closes — and taught the note to say so. FTB 5805 Worksheet II says
  otherwise, in its own line-2 instruction: "Enter in column (a) and column (d) the result
  of the amount shown on form FTB 5805, Part II, line 3 multiplied by 30%, enter -0- in
  column (c) and 40% for column (b)." Part II line 3 is headed "Withholding taxes". Both
  sides carry the SAME weights, Part I question 3 is the election OUT of that default
  ("was your California withholding NOT withheld in equal installments..."), and the form's
  own Short Method — total withholding and estimates against the required annual payment,
  no per-column allocation at all — is only coherent because they are aligned. So the whole
  test collapses to `(requirement - withholding) x weight`, a taxpayer paid only by
  withholding can never be short of the weighting, and the $5,000/$4,189 case the wrong
  reading called $452.75 short is the schedule's own $243.30 with nothing owed. Two names
  for one quantity is what let it ship: `_voucher_catchup_note` now takes ONE
  `instalment_fraction`, and `_ca_required_fraction` / `_federal_required_fraction` are
  renamed `_ca_instalment_fraction` / `_federal_instalment_fraction` because "required" was
  the half-truth that made two parameters look necessary.

  **And the fix reached one renderer of two.** `withholding.py` prints the same schedules
  from the same two functions: it had the CA note unconditionally and the federal note not
  at all, so `/tax-withholding` went on directing catch-up payments `/tax-project` had
  stopped directing, and its 1040-ES table shipped bare. Step 0 hands a workspace user to
  BOTH tools. Both renderers now call the same predicate, and `test_withholding.py` pins
  each half — a note imported by two modules is one place for the fix and two for the bug.

  **Two of the fixes were defects of ORDER, with every word already correct.**
  §6654(e)(2) — prior year of 12 months, no liability for it, US citizen or resident
  throughout — is a complete no-penalty answer, and none of its three conditions is "filed
  a return". It sat two bullets BELOW the "a return must have been filed" STOP, so someone
  under the filing threshold last year was refused and told only the 90% prong remained.
  Likewise step 4's timing check reported which past deadlines were missed and then
  scheduled the next voucher, never computing what was outstanding TODAY: §6654(b)(2) runs
  an instalment's underpayment to the earlier of payment and the following April 15, so
  holding April's shortfall until September buys ten weeks of accrual for nothing — and
  `_CA_VOUCHER_CATCHUP_NOTE` was already telling CA users the opposite. Both now sit in
  `TestTheAdvisorySkillReachesTheAnswersItAlreadyHolds`, whose assertions are POSITIONAL
  on purpose: a survival check ("is the rule still stated?") passes on both defects, which
  is how they survived three review rounds. Reordering the two bullets was not enough on
  its own, either: "Without figure 1, stop" is a refusal of its own and sits one level
  ABOVE the preconditions, and a taxpayer who filed nothing has no line 24 to hand over —
  so that paragraph now says "I owed nothing last year" is not a MISSING figure 1, and the
  test pins that forward reference too. A gate reordered without checking what gates it is
  nested inside is half a fix.

  It is not a CONFIRMED $0 either, and the first draft of the forward reference said it
  was. "I owed nothing" is ordinarily a sentence about the balance due: the taxpayer whose
  withholding covered a $15,000 liability says it, and so does the one who got a refund.
  Read as figure 1 at $0 it hands both of them §6654(e)(2) and the answer "no penalty can
  attach this year at all", against a prior year with a large line 24 — an UNDER-statement
  reached by inference, which AGENTS.md forbids outright ("never guess a value that changes
  a return"). The paragraph now says what the phrase usually means and sends the user back
  to line 24 and the credits, and the §6654(e)(2) bullet's $0 is labelled as the COMPUTED
  figure, "never taken from a phrase" — the same inference one paragraph further down,
  where the ordering fix would otherwise have delivered it.

  **The CA paragraph then took the catch-up back.** Its closing sentence — "a CA payment is
  still made only on the three dates that carry weight" — is true of an INSTALMENT and false
  of a catch-up: R&TC §19136(a) conforms to §6654 "except as otherwise provided", and what
  §19136.1 and §19136.3 otherwise provide is the weighting and the $1M limitation — not
  (b)(2), so a CA underpayment runs until it is PAID exactly as a federal one does, so a Californian who missed April or June has principal accruing today, and a rule
  saying CA payments happen on three dates sends it to January 15. One correct sentence
  about scheduling, one paragraph away from the timing check that computes the catch-up, is
  enough to defer it: the fix distinguishes the scheduled instalment from the catch-up
  explicitly and dates the catch-up to the day it is computed.

  **And the timing check itself has to run on the withholding the year will END with.** It
  reported which past deadlines were missed off step 3's PRE-adjustment figure and called
  the result unfixable — two paragraphs after recommending route A, whose entire rationale
  is that §6654(g)(1) credits withholding back across the year it was withheld in. Extra
  withholding arranged in July therefore raises what April is deemed to have been credited
  and can erase the spring shortfall outright; reporting it as accrued anyway collects a
  catch-up payment for money that is not owed, the OVER-stating direction that AGENTS.md
  makes a hard error. The estimated-payment contrast has to survive the correction, though,
  and the paragraph keeps it in the same breath: a voucher credits on the date it is made,
  so a September payment still cures nothing in April.

  **Route A is dead in the window the skill most expects to be used in.** Raising
  withholding beats a voucher because §6654(g)(1) credits it back across the whole year —
  the year it is WITHHELD in. Invoked on January 5 about the January 15 instalment, the
  covered year has ended: a 4(c) change lands entirely on the year that just began. Route A
  leads step 4 and is called "not close", so it was the recommendation a January reader
  acted on. It is now gated on the covered year still having payroll, naming January 1 to
  April 15 as the window where only route B and the §6654(h) file-and-pay escape are live.

  **The covered year must also be a year the skill states the law FOR.** "Statutory and
  not indexed" is a claim about
  inflation, not about amendment, and both parameters have been amended: §6654(d)(1)(C) ran
  a schedule of different applicable percentages (112% for a prior year of 2001) before
  settling at 110% for prior years from 2002, and California's two arrived a year apart —
  R&TC §19136.3's $1,000,000 limitation for taxable years beginning on or after January 1,
  2009, §19136.1's 30/40/0/30 weighting only from January 1, 2010, TY2009 itself running
  30/30/20/20 WITH a September instalment. The CA floor follows the later of the two, since
  the skill states one weighting and it is that one. Step 1 therefore
  refuses a covered year before 2003, or before 2010 for California, and the refusal has to
  come BEFORE the intake — by the time figures are collected they are already keyed to a
  year whose rules the skill does not state. The older-year case is not hypothetical:
  `/tax-amend` and an unfiled-return catch-up both reach back.

  **And the same argument runs FORWARD, which the first version of it did not.** A floor
  with no ceiling answers 2031 out of 2026's law with the same confidence: nothing in a
  copy of this skill can notice a later amendment, there being no cache to go visibly stale
  and no engine to disagree with. So the window is closed at the top too — refuse a covered
  year after the last one these figures were read from source, and degrade the way AGENTS.md
  §2 degrades everywhere else, by asking the user to confirm the factor and both AGI
  thresholds against a current source rather than guessing or refusing outright.

  **The instalment CALENDAR is a third applicability question, and step 4 assumed it.**
  n/4 across April 15, June 15, September 15 and January 15 is the calendar-year Form 1040
  case and nothing else. §6654(k) substitutes the corresponding months for a taxable year
  beginning on any date other than January 1, so a fiscal-year filer's dates are the 15th
  of the 4th, 6th and 9th months of THEIR year and the 1st after it ends. §6654(j) gives a
  nonresident alien three required instalments — no April, 50 percent substituted for 25
  at June 15, withholding credited in thirds (the Form 2210 instructions render it as
  "Skip column (a)" and "enter one-half of the amount on line 9"). Run on either, step 4
  reports instalments that do not exist and sizes every catch-up against the wrong
  fraction, and nothing in the intake would have caught it because nothing asked. Step 1
  asks; step 6 refuses both with the citation.

  **And the FORM those figures sit on moved twice inside that same span.** "Form 1040 line
  24" is total tax only from the 2020 form. On the 2019 form total tax is line 16 and line
  24 is the ESTIMATED-TAX PENALTY — verified against `irs.gov/pub/irs-prior/f1040--2019.pdf`
  — so a covered year of 2020, which the 2003 floor accepts, reads a figure that is
  normally zero and multiplies it by 1.10. Nothing downstream can see it: the intake
  succeeded, the arithmetic is right, the target is a fraction of the real one. AGI moved
  with it (line 8b on 2019, line 7 on 2018, line 37 on 2017, and 11a repeated at 11b on
  2025), and 2019's line 11b is TAXABLE income — smaller than AGI, so able to drop the
  factor from 110% to 100% as well. Both misreads run the understating way. Step 1 now
  binds the numbers to the 2020-and-later layout, names the 2019 trap, and tells a reader
  with an older return to match the printed LABEL instead.

  **And the AGI half was bound in the CAVEAT and nowhere a reader fills in.** Figure 2 went
  on asking for "Form 1040, **line 11**" a dozen lines below the paragraph that says line 11
  is a 2020-and-later number — and an intake item is what gets filled in, so the request is
  what is read and the essay behind it is not. What sits at line 11 on the older forms is
  not AGI but is a plausible number: the standard-or-itemized-plus-QBI subtotal on 2019 (AGI
  is 8b), the TAX itself on 2018 (AGI is 7), alimony received on 2017 (AGI is 37), each read
  off that year's own Form 1040. Every one of them is small enough to pass the
  $150,000/$75,000 test and select 100% where 110% is owed, which is the required payment a
  tenth low on the filed return. `/tax-project`, `/tax-withholding` and `/tax-compute`'s intake contract
  had the same categorical sentence pointed at the same field, so the fix is a SWEEP rather
  than a correction: all four now bound line 11 to 2020 in the sentence that names it, give
  2019's 8b as the value just outside the bound, and state the direction. A rule stated
  where the reader is not is not stated.

  **And that sweep stopped at the directory the skills live in.** The fifth document
  saying where prior-year AGI is has no skill around it: `projection_inputs_template.json`
  carries its own `_comment_prior_year_form_2210_agi`, which said "Form 1040 line 11 AGI"
  flat, and AGENTS.md §6 makes the file it templates the headless input path — so it is
  the one copy a reader reaches without opening a skill at all, and it kept the exact
  sentence the four skills had just lost. The sweep could not have caught it either: it
  was spelled `SKILLS_DIR / skill / rel` and structurally could not name a path outside
  `.agents/skills/`. It takes repository-relative paths now, which is what lets the
  template be an entry rather than a second test that drifts from the first.

  **The status-change bullet summed the TAX and left one AGI to pick the factor.** Two
  people who filed separately last year and file jointly this year hand over two prior
  returns; the bullet added both figure 1s, and step 2 then applied a single factor read
  off a single AGI. Where one spouse cleared $150,000 and the other did not, the low AGI
  charges 100% on tax that owed 110% and the harbor is short by a tenth of the high
  earner's tax. Neither §6654(d)(1)(C) — "the adjusted gross income shown on the return of
  the individual" — nor Pub 505, which gives the summing rule for the tax and says nothing
  about the AGI, resolves which return to read. The Instructions for Form 2210 do, by
  ORDER: line 8 applies the AGI test to "the AGI on your 2024 return", and only then says
  to "add your 2024 tax (as figured earlier) to your spouse's 2024 tax (as figured
  earlier)" — each return grossed up by the factor its own AGI earned, then summed. Figure
  2 now collects BOTH AGIs and step 2 applies the table to each return separately. The
  converse case gets its own clause: joint last year and separate this year tests the WHOLE
  joint AGI against the $75,000 threshold, not a half share, because that is the AGI SHOWN
  on the prior return.

  **The confirmation rule then locked out the population it was written for.** Requiring
  figure 1's $0 to be COMPUTED from line 24 and the credits is right against "I owed
  nothing" said of a refund — and fatal to the taxpayer who genuinely owed nothing, since
  a year that required no return has no line 24 to compute it from. "Without figure 1,
  stop" fires first, and §6654(e)(2) — the exception whose ordinary case the skill itself
  calls "no return, no liability, no penalty" — is never reached. Pub 505 chapter 2 supplies
  the second route in terms: "You had no tax liability for 2025 if your total tax ... was
  zero or you didn't have to file an income tax return." The carve-out now names it, and
  keeps it apart from the return that WAS required and simply never filed —
  §6654(d)(1)(B)'s closing text withdraws the prong from that taxpayer anyway, so merging
  the two would hand them a harbor they do not have.

  **Step 0's handoff must not reintroduce a weaker intake.** A workspace user is sent to
  `/tax-withholding` for an actual computed liability, while `/tax-project` is only a
  separate annualized forecast. Neither federal report may derive the prior-year prong
  from archived Form 1040 line 24: the covered-year Form 2210 line-8 chart enumerates
  Schedule 2 inclusions and exclusions in addition to its refundable-credit treatment,
  so subtracting credits from line 24 is still incomplete. `_prior_tax_agi` now reads
  archived AGI only when explicitly paired via `prior_year_form_2210_agi` from the SAME
  return — never borrowed automatically, since `/tax-amend` can leave an archive holding a
  different return's figures than the one the tax base was composed from; unpaired, prior
  AGI is flagged unknown (100% factor) rather than guessed. The prong itself activates
  solely from the explicit `projection_inputs.json.prior_year_form_2210_tax_base`; absent
  that completed pre-factor composition, the reports leave it unresolved and show loud
  90%-current degraded mode.
  `TestTheAdvisorySkillCorrectsTheEngineItHandsOffTo` pins the engine, template, and Step-0
  contract together. The CA raw-prior disclosure remains separate because FTB 5805's
  composition has not been modeled.

  **A NO to the 12-month scope question is not a null base.** That question — a full 12-month
  prior year for which a return was actually filed — IS §6654(d)(1)(B)'s closing text, and
  that text withdraws the PRONG and nothing else. §6654(e)(2) asks about liability rather
  than filing and needs no return at all; the Form 2210 instructions put its 12-month test
  as the year the return "was (or would have been had you been required to file) for a full
  12 months", so a prior year under the filing thresholds is its ORDINARY case. Both routes
  then reach the SAME field: the prong through a positive `prior_year_form_2210_tax_base`,
  the exception through a $0 one paired with `prior_year_zero_liability_confirmed`. So
  "never populate that field for a withdrawn prong", written as a rule about the FIELD
  rather than about the prong, deleted the exception with it — and silently, because
  `_prior_tax_agi` consults that flag only at a $0 base, so a null one discards a
  confirmation the user gave without a word and the handoff reports 90% of the current
  year to a taxpayer §6654(e)(2) exempts outright. Step 0 now works the first precondition,
  and `references/no-return-year.md` with it, BEFORE the handoff, and transmits its
  answer — rather than carrying a third copy of a three-part test that already exists in
  step 1 and in both engine-calling skills.

  **And the calendar gate had to move to where the handoff is.** Step 6 refuses a fiscal year and a §6072(c) 1040-NR filer outright — §6654(k)(1) substitutes the corresponding months of a non-January year, §6654(j) gives three instalments at June 15 / September 15 / January 15 with 50% at the first — but the check sat in step 1, and step 0 hands a workspace user off BEFORE step 1 runs. `compute_estimated_vouchers` models neither case: `ES_DUE_DATES` is fixed, and `/tax-withholding` has no input that could move it. So the two filers the skill promises to refuse were the two who reached the engine first, and got back dates and amounts from the wrong schedule. The NR half is the one that runs the wrong way: four equal instalments where the statute weights the first at 50% UNDERSTATES the June payment. The gate carries the §6072(c) narrowing with it, because an NR filer whose wages ARE withheld on is on the ordinary quarters and computable here — the fix for a gap must not ship an over-refusal in its place. Same shape as the three rounds before it: a rule stated once, and not at the place an agent acts on.
  **And round fifteen's voucher fix never crossed into the skill.** The engine's closed-schedule note was split in two that round: the FIXED-penalty verdict stays hedged, because whether §7508A(a)(2) reaches §6654(b)(2)'s accrual endpoint is genuinely unresolved here — but the instalment half never reached the skill, and it is not in doubt at all. §7508A(a)(1) disregards the postponed period in determining whether the acts in §7508(a)(1) were performed in time, and §7508(a)(1)(B) is "payment of any income ... tax or any installment thereof", so an instalment a notice covers is payable on its postponed date and paying it then is TIMELY. Step 4 went on saying "do not send a 1040-ES" flat, two sentences after naming the same notice, and that is the copy which runs when the engine is gone — the skill's whole licence. Following it forfeits the §6654 charge on the one instalment still free to fix: under-statement, on the strongest claim in the file. The condition now LEADS each imperative, the way the engine's does, because an agent reads an instruction as an instruction and stops. Fourth round running of one shape — a rule fixed in one of its two copies.


  The voucher correction still travels with the handoff. Both schedules split the ANNUAL
  gap over the deadlines still open, so a July run can underfund the cumulative September
  requirement; Step 0 names both functions and routes the reader to the cumulative test.

  **Annualization date and action date are different clocks.** A valid stored
  `as_of_date` remains attached to its YTD figures in open and closed years; relabeling a
  June snapshot as 12-31 would treat half-year actuals as finals. Closed years default to
  12-31 only when no date is stored, while malformed stored metadata is omitted with a
  redacted banner. The real current date independently decides whether covered-year
  withholding can still change: after year-end every W-4 and DE 4 count is zero even when
  a valid June stamp is preserved for annualization. The remaining calendar notes compare
  voucher dates against that same real date, including January 15 and the statutory April
  15 accrual endpoint.

  **A remedy that does not exist is worse than no remedy.** A draft offered "a first-time
  or reasonable-cause waiver (Form 2210, Part II)". IRS First Time Abate does not cover
  the §6654 penalty at all (it reaches §6651(a)(1)/(2) and §6656), and there is no
  general reasonable-cause waiver either: §6654(e)(3) and Part II's boxes reach a
  casualty, disaster or other unusual circumstance where imposing the penalty would be
  inequitable, or retirement after age 62 / disability in the tax year or the one before,
  with reasonable cause. A user told a waiver is available may under-pay counting on it,
  which is why this counts as an under-statement rather than a cosmetic overstatement of
  the options. The genuine escape hatch is §6654(h): filing the return and paying it in
  full by January 31 (March 1 for a farmer) kills the FOURTH instalment's penalty
  outright, and step 4 gives it instead — bounded to the fourth, because the unqualified
  "no later payment erases it" it replaced was false in exactly that window.

  **YTD is not the year.** Step 3 subtracts EXPECTED FULL-YEAR withholding, not the
  YTD figure — the engine annualizes (`ann("federal_withholding")`) for the same reason.
  A draft subtracted YTD from an annual requirement and produced $8,340 where $3,965 was
  owed, then asked for that on top of a paycheck already withholding. Over-statement is
  the safe direction only until it is 2x, at which point it is the cry-wolf shape that
  gets the whole skill disregarded.

  **California needs California figures — FOUR of them.** Step 1 asks for prior-year CA
  total tax and prior-year CA AGI (Form 540), expected full-year CA withholding with any
  dated 540-ES payments, and an estimate of CURRENT-year CA AGI; the engine passes
  `_prior_ca_tax_agi(prior)` and `state.agi` for the same reason. CA AGI differs from
  federal (no HSA deduction among others), so near $150,000 the 110% test can land on the
  other side. Two drafts got this progressively less wrong: the first described CA's three
  departures without saying the inputs change at all, inviting federal line 24 into a CA
  calculation; the second said "two more figures", which silently left the state gap
  netted against FEDERAL withholding and left §19136.3's $1M test — the only test in the
  skill reading a CURRENT-year figure — with nothing to read. An unknown current-year CA
  AGI is a refusal, not an assumption that the taxpayer is below $1M: assuming below is
  the direction that keeps the answer usable and makes it too low.

  **CA's weights govern the REQUIREMENT and the WITHHOLDING alike.** This paragraph has
  now been written both ways, so it is pinned against the form rather than either reading.
  FTB 5805 Worksheet II line 1 takes the required annual payment at 30/40/0/30 and line 2
  takes Part II line 3 — "Withholding taxes" — at the same 30/40/0/30; Part I question 3
  ("was your California withholding NOT withheld in equal installments and are you able to
  show the actual amounts withheld per period") is the election OUT. Reading the credited
  side as 25/50/75/100 makes April 15 need 30% of the requirement against 25% of the
  withholding and reports a shortfall of 5% of the year's withholding for a taxpayer who
  owes nothing — an over-statement, which AGENTS.md makes the hard error. The engine's own
  docstrings were a source for that mistake and had to be corrected with it, which is the
  useful lesson in the other direction: a docstring is not a citation, and the two rounds
  that read one as though it were are why the test named for this invariant now asserts the
  required sequence PRESENT, equal quarters ABSENT, and runs `_voucher_catchup_note` on
  the one case where the two readings disagree.

  **Form 2210 line 6 has a third member, and figure 4 claims to be all of line 6.** After
  the withholding lines the instructions add: "Credit for U.S. tax paid to the USVI. You
  can take a credit on your U.S. return for the U.S. tax allocated to the USVI only if you
  actually paid the tax to the USVI... You can include that credit here." Figure 4 already
  carried the other non-obvious member — excess social security under §31(b) — and says in
  terms that nothing else is needed, which is what makes an omission here a wrong answer
  rather than a gap: the credit is money already paid in, so leaving it out inflates the
  gap and buys a payment that was never owed. Same direction as the excess-SS omission that
  preceded it, and the test bounds its match on the form NUMBER, since `"8689" in text` is
  satisfied by any string that merely starts with those digits.

  **Ordering is load-bearing.** The "there is a workspace, use `/tax-project` instead"
  check is step 0, ahead of data collection, because a cruder number already in the
  conversation is one the user may act on — the same argument that puts the engine gate
  before the first engine invocation rather than wherever it reads well.

  **One covered year, fixed at both ends.** "This year" names two different years on the
  day the skill is most likely to run: between January 1 and April 15 the instalment
  falling due belongs to the year that ENDED while the calendar reads the year that began,
  and January 15 is one of the two dates the description invites invocation on. Every
  figure is year-keyed — the prior return, the filing status, the factor, the deadlines —
  so step 1 fixes the covered year before asking for anything and defines "current year"
  as that year; pairing one year's return with another's status is silently wrong in both.
  The window has a far end too: §6654(b)(2) runs each instalment only to the earlier of
  payment or the 15th day of the fourth month after the year closes, which an extension
  does not move, so past that date the penalty is FIXED and a voucher buys nothing —
  the one recommendation this skill could make that is not conservative but inert, and the
  wrong payment channel besides (the balance belongs on the return). §7508A is the one case
  where that far end is not the far end, and step 4 carries the caveat; step 0's window table
  and `references/withholding-cure.md` route to step 4 rather than restate the categorical
  form, which is what each of them used to do. Both ends are pinned
  by `TestTheAdvisorySkillIsAnchoredToOneCoveredYear`, the near end positionally: the year
  question has to precede the first figure, not merely appear somewhere.

  **The engine models the federal $1,000 floor as of this change.** `compute_safe_harbor`
  had no equivalent of `CaSafeHarbor.de_minimis`, so `/tax-project` and `/tax-withholding`
  told a user with a small balance to pay for protection §6654(e)(1) already gave them —
  while `compute_form_2210`, one import away, returned `no_penalty` on the same figures.
  It now carries `SafeHarbor.de_minimis`, computed from tax minus WITHHOLDING (estimates
  do not count toward this test), which zeroes the penalty shortfall, the W-4 4(c)
  penalty figure and the penalty voucher schedule, and states the reason in the status
  cell. The zero-balance target is untouched: the floor excuses the penalty, not the tax.

  **The floor is a CLIFF, and the status cell may only say so when something is behind
  it.** `de_minimis_margin` carries how far under the floor the run sits — the continuous
  quantity, not an invented "near the cliff" threshold — and the cell used to follow it
  with "and it attaches" in every case. That is false wherever a prong already covers the
  year: $5,000 of tax against $4,500 withheld and a $4,000 prior-year prong sits $500
  under the floor, and at $5,500 the prong still binds at $4,000 against $4,500 paid, so
  nothing attaches. `de_minimis_cliff_more_tax` and `de_minimis_cliff_less_withholding`
  decide it, ONE FIELD PER PATH — the floor lapses at tax = withholding + floor, which
  raises the 90% prong but not the prior-year one, and it lapses equally at that much less
  withholding, which lowers what is paid and leaves the requirement alone. Zero means the
  warning is silent on that path. They were a single worse-of-the-two field, which is the
  same error a level down: /tax-withholding's tax is the computed liability, so a positive
  coming from the MORE-TAX path was rendered there as a warning about withholding falling
  — for $500 of tax against $0 withheld, "$500 less withholding and it attaches", on a
  figure that cannot fall and where no pace lapses the floor at all (`withholding <
  de_minimis_margin` is the same statement as tax under the floor, and the generic
  "nothing attaches" tail gives the wrong REASON there, since the requirement is not met
  either). Over-stating what the taxpayer must pay is the hard error whether it arrives as
  a total or, as here, as a warning — and suppressing `shortfall` IN PLACE is what left
  the warning nothing to be conditional on, which is why the pre-suppression gap now
  survives as `shortfall_before_de_minimis`.

  **The current-year tax is a line-24 total, and §6654 measures Form 2210 line 4.**
  `compute_form_2210` states that contract on its own argument — line 24 MINUS the part-IV
  refundable credits — and neither caller of `compute_safe_harbor` can meet it:
  `project_federal_tax` applies the CTC as a nonrefundable credit only and models no EIC or
  ACTC, and result.json carries a field for neither. Line 4 can only be LOWER, and the
  floor's other side is understated the same way (§6654(e)(1) subtracts the whole §31
  credit, which includes excess social security on Schedule 3 line 11), so the 90% prong
  and the floor test both come out too high: $5,000 of tax, $1,000 of refundable AOTC and
  $3,500 withheld is a true $500 balance the floor excuses, reported as $1,500 to pay.
  `current_tax_unnetted` records that remaining federal limitation and fires only where a
  shortfall is actually being asserted, since netting cannot take a run OUT
  of the floor and a covered year has nothing to over-state. Both renderers print it; the
  CA twin is deliberately as narrow as the CA prior-year one.

  **Both harbors VALUE-check the filing status, which only one of them used to.**
  `compute_ca_safe_harbor` raised on a non-canonical spelling; `compute_safe_harbor`
  read `(filing_status or "").upper() == "MFS"` and took whatever came back, so
  "Married Filing Separately" silently drew the $150,000 prior-AGI window and the 100%
  factor instead of $75,000 and 110% — a required annual payment 10% low, the direction
  that ends in the penalty, on the federal side of a report whose CA side refused the
  same profile outright. One shared `_canonical_status` now does both.

  **A floor has to reach the SCHEDULE, not just the harbor's field.** CA's de minimis
  shipped a year earlier and was applied only to `CaSafeHarbor.shortfall`, while
  `compute_ca_estimated_vouchers` took `required_annual` and recomputed a shortfall of
  its own — so one CA block printed "no CA underpayment penalty applies", a $200.25
  January instalment and a note directing an immediate catch-up payment, together. This
  is the general shape worth remembering: a suppression applied at one field of a computed
  object does not propagate to a consumer that recomputes the same quantity from the
  object's inputs. The first fix forwarded the flag to both builders, which left the
  recomputation in place with an override bolted on top; both now take the SHORTFALL
  itself and derive nothing, so the next statutory suppression propagates without a second
  flag. Each caller owns which quantity it is splitting — `safe_harbor.shortfall` for the
  penalty schedule, `liability - withholding - estimates` for the zero-balance one — which
  is what the flag was standing in for.

  **Deliberately out of scope**, each for a reason that would break the property above:
  the 90%-of-current prong and Form 2210 Schedule AI (both need current-year law and a
  worksheet); any interest or penalty RATE (§6621 resets it quarterly, so a stated rate
  decays); the §6651 failure-to-file minimum (indexed). It is NOT in `WEB_DEPENDENT`,
  because it must not fetch, and NOT in `INTERVIEW_SKILLS`, because it has no
  `answers.md` to read — its questions change no return.

### Contributor skills (not part of the return pipeline)

Two skills in `.agents/skills/` are for people working ON the toolkit rather
than filing with it. They are labelled in `test_portability.py` by
`CONTRIBUTOR_SKILLS` and held to every rule the `tax-*` skills are held to —
frontmatter, the neutralization lint, the line cap, and a `.claude/skills`
symlink each. They ship in the portable location on purpose: `state-module` is
the entry point for the community state contributions AGENTS.md invites, and a
contributor skill only one vendor's agent could read would undo the point of
the multi-agent move.

- `codex-loop` — the commands behind CLAUDE.md's review-loop bullets, which are
  prose because the always-loaded budget is tight, and which were re-derived
  from scratch every round (28 rounds on PR #65, 19 on #23, 16 on #20). Carries
  the three traps that silently end the loop early: a clean pass arrives as a
  top-level ISSUE COMMENT rather than a review, so polling `reviewThreads` alone
  waits forever; inline comments must be filtered by `created_at` against the
  trigger time, because GitHub re-anchors un-outdated ones to current HEAD and a
  stale finding then carries today's SHA; and a clean pass only ends the loop if
  the HEAD it reviewed is still HEAD. Thread resolution is the GraphQL
  `resolveReviewThread` mutation — there is no REST endpoint.
  `disable-model-invocation: true` — but only some agents honour that key, so it
  is a Claude-Code-level safety property and not a portable one. The skill body
  therefore states the degrade outright: where the key is not honoured, every
  step that posts to GitHub is one to confirm with the user first. The
  neutralization lint cannot see either of these, since it matches bare tool
  names; a Claude-only *path* or *frontmatter key* passes it untouched.
- `state-module` — gates and ordering only; the detail stays in
  `state-module-playbook.md` and `state-module-template.md`, so there is one
  copy to keep current rather than two that drift. Registered in
  `WEB_DEPENDENT`: filling a fetched-law block means looking up a state's
  current schedules, and AGENTS.md §2's STOP-and-paste rule governs a state
  figure exactly as it governs a federal one. Model-invocable on purpose — it
  has no side effects, and a contributor asking "add Washington" should get it
  without knowing its name.

NY IT203's declared TY2025 plan is the reviewed exception to the general
part-year/nonresident block: all other state obligations remain gated unless
covered no-income-tax states require no return. City IT360.1, variant payment
tie-out and refusal of competing NY returns are included; credit/modification
worksheets remain inputs. IT203 NY forecasts are skipped explicitly.
