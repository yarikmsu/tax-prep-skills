---
name: tax-ptet
description: PTET election analyzer — estimates the federal SALT-cap-workaround savings of a CA/NY/NJ pass-through-entity tax election from K-1 income, and surfaces the state election deadlines. Read-only, federal-only, advisory. Use when the taxpayer has K-1 pass-through income from a CA, NY or NJ entity, or the user mentions PTET, the SALT cap workaround, or an entity-level state tax election.
license: Apache-2.0
---

# /tax-ptet — Pass-Through Entity Tax Election Analyzer

The TCJA capped the federal SALT itemized deduction at $10,000. A partnership or
S-corp can elect to pay its owners' state income tax **at the entity level**,
where it's fully deductible against the federal pass-through income — bypassing
the individual cap. The owner then takes a state credit for the PTET paid, so the
state isn't double-taxed. This skill estimates the resulting **federal** savings
for the three states the toolkit models (CA / NY / NJ) and reminds you of each
state's election deadline (NY's is the trap — it's due *during* the tax year and
cannot be made on the return after the fact).

Federal savings is **anchored** to the real `result.json` line 24 (same contract
as `/tax-scenario`, `/tax-roth-convert`, `/tax-harvest`): the entity-level
deduction is modeled as a reduction in pass-through ordinary income through the
`compute_scenario` keystone, so bracket positioning / QBI / additional-Medicare
interactions are priced automatically. Only the marginal effect around your real
tax is reported.

**Read-only and advisory.** Never mutates `result.json` or `taxpayer_profile.json`,
and — unlike the scenario/harvest/roth-ladder skills — writes **no artifact**: the
output is a `/tax-review`-style section. The backlog (#9) calls for this analysis
to appear in `/tax-review` and `/tax-project`; run `/tax-ptet` standalone for a
focused estimate or mid-year deadline check.

## Prerequisites
- `result.json` with a computed `form_1040.line_24_total_tax` and
  `line_12_standard_deduction` — the deduction the return took, standard or
  itemized, which the engine prices from and refuses without (else: run `/tax-compute`).
- `taxpayer_profile.json` (filing status; `flags.k1_states` lists each K-1 entity's
  state — the same field `/tax-calendar` uses for PTET deadlines).
- `tax_law_{year}.json` with populated `federal.filing_statuses.<status>.brackets`
  (run `/tax-setup` if absent).
- `tax_law_{year}.json` with populated `federal.capital_gains_rates` (both `zero_pct_limit`
  and `fifteen_pct_limit` for the filing status) whenever the return has qualified
  dividends or a net long-term gain: the engine REFUSES to price that income at ordinary
  rates and names `/tax-setup` as the fix. A wages-only return runs without them.

## How it works

### Engine preflight (HARD GATE — runs before every other step)

Run: `sh agents/lib/run.sh agents/lib/preflight.py`

- **Exit 0** — the engine is present and usable; continue. If the verdict says
  `OK, with N warning(s)`, show the user those lines first: the engine is fine,
  but something a later step needs is not — a missing package, or a workspace
  with no virtualenv — and it surfaces at first use instead of here.
- **Anything else — including the command failing because the file does not
  exist — STOP.** Do not parse, compute, or emit anything. Show the user the
  command's output and stop there.

The gates and computation this skill names are scripts under `agents/lib/`, a
sibling of `.agents/skills/` that does not travel with a copied skill — which
then still reads as a complete procedure while every script it names is absent.
So a failure to RUN this check IS the blocking condition, never a step to skip.

### Procedure

Steps:
1. Confirm prerequisites exist. If `flags.has_k1_income` is false and the user has
   no K-1 income, tell them PTET doesn't apply and stop.
2. Ask the user for the **qualifying pass-through income per entity state** (the
   amount the entity would elect to pay PTET on). Map each to `--income STATE=AMT`.
3. Ask for their other state/local taxes, the deductible personal SALT remaining
   in the modeled year if the entity elects. Start with property tax + every
   state and local income tax line 5a takes, per `/tax-compute`'s
   `state_local_income_taxes` recipe, and subtract only **personal payments actually displaced**
   by the election. Confirm credit usability and payment timing: the tax
   attributable to the step-2 income need not equal the usable PTET credit,
   and the credit need not reduce personal payments in this year. Do not reduce
   already-paid withholding or estimates just because a refund or credit is
   expected, or subtract credit carried forward to a later year. If that
   displacement is unknown, stop and obtain the payment comparison before
   quoting savings. For example, $44,000 of pre-election SALT with $8,000 of
   tax attributable to the K-1 and a usable $10,000 credit that actually reduces
   this year's personal payments by $10,000 leaves `--other-salt 34000`.
   The [TY2025 Schedule A line 5a instructions](https://www.irs.gov/instructions/i1040sca)
   count payments in the year made and do not net expected refunds;
   [TY2025 FTB 3804-CR instructions](https://www.ftb.ca.gov/forms/2025/2025-3804-cr-instructions.html)
   distinguish credit claimed from unused carryover. Pass the remaining SALT
   as `--other-salt`; the analyzer derives the unused headroom
   against the year's SALT cap, which moves every year and with modified AGI
   (step 3 of "What the analyzer computes" below). With the larger OBBBA cap,
   don't assume the cap is exhausted — get the number. The analyzer reads the
   return's modified AGI and itemize choice from `result.json`'s `schedule_a`,
   so it prices the cap the return applied. A return with no `schedule_a`
   section took the standard deduction: pass `--standard-deduction` then,
   because a missing section alone is not read as one. Prefer this to
   `--salt-room`: a headroom figure from memory is usually priced against an
   older or unreduced cap, and one larger than the cap itself is refused.
4. Ask whether the K-1 income is **§199A QBI-eligible** (operating businesses
   usually yes; investment/specified-service partnerships at high income often
   no). If yes, pass `--qbi-eligible STATE` so the QBI clawback is priced — the
   PTET deduction shrinks the QBI base, so the federal savings is *lower* than
   the raw marginal-rate benefit. If unsure, the default output warns that the
   savings may be overstated.
5. Run the analyzer and present the rendered markdown. Highlight the **earliest
   election deadline** relative to today's date.


### What the analyzer computes

1. **Per-state PTET paid.** For each K-1 entity state you supply income for, the
   entity-level tax is computed from the rate schedule. A **fetched**
   `ptet.<STATE>` schedule in `tax_law_{year}.json` (keyed by K-1 entity state,
   populated by `/tax-setup`) is PREFERRED — version-controlled per year like
   federal rates. If a state's schedule was not fetched, the built-in statutory
   schedule below is used with a **loud unfetched-schedule warning** naming the
   vintage (the NJ BAIT overhaul is the drift precedent); a corrupt fetched
   block hard-errors. The built-in schedules:
   - **CA** (R&TC §19900): flat **9.3%**.
   - **NY** (Tax Law §860/§864): graduated — 6.85% ≤$2M, 9.65% $2M–$5M,
     10.30% $5M–$25M, 10.90% >$25M.
   - **NJ** (BAIT, P.L. 2021 c.419, TY2022+): graduated — 5.675% ≤$250K, 6.52%
     $250K–$1M, **10.9% >$1M** (the 2022 revision collapsed the legacy 9.12%
     $1M–$5M and 10.9% >$5M brackets; pre-2022 returns use the legacy schedule).

   The graduated NY/NJ rate is applied to **your supplied share** as a per-owner
   approximation. For multi-owner entities whose entity-level pool sits in a
   different bracket, pass `--ptet-rate STATE=0.0xx` to override. Repeated
   `--income` for the same state **accumulate** (two K-1 entities in CA →
   `--income CA=100000 --income CA=50000` analyzes $150K); a repeated
   `--ptet-rate` for one state is rejected as ambiguous.

2. **Anchored federal benefit.** The PTET paid is fed into `compute_scenario` as a
   negative `other_income` delta; the federal tax saved is the difference around
   the real `result.json` line 24.

3. **Forgone-SALT offset.** Only state tax that would have fit under your remaining
   SALT cap was federally deductible anyway. The cap is year/status-aware and is
   READ FROM THE CACHE (`federal.salt_cap`, `amount`/`mfs_amount`), because
   §164(b)(7) as amended by OBBBA steps it up every year: **$40,000 for TY2025,
   $40,400 for TY2026**, then 101% of the prior year's figure through TY2029,
   reverting to $10,000 in TY2030. MFS is HALF the amount, whatever it is
   (§164(b)(6)(B)) — not a second schedule. A flat "$40,000 for TY2025–2029"
   stood here and was wrong from TY2026 on — and under-stating the cap makes the
   PTET election look BETTER than it is, since it is the portion of state tax
   the cap blocks that the election rescues.
   **§164(b)(7)(B) then phases the cap DOWN** by 30% of MAGI over $500,000
   (TY2025; $505,000 for TY2026, then 101% a year), halved for MFS, never below
   $10,000 — so the OBBBA increase is fully spent by about $100,000 of excess
   MAGI. This IS modeled, from `result.json`'s `schedule_a.modified_agi` or
   `--magi`. Without either, headroom derived from
   `--other-salt` is priced against the unreduced cap, and the output says so:
   the headroom is a ceiling and the benefit a floor.
   By default the estimate **assumes the cap is fully exhausted** — but with the
   larger OBBBA cap that's less likely, so pass `--other-salt <deductible personal
   SALT remaining in the modeled year, after personal payments actually displaced
   by the election>` using step 3's payment comparison to derive the unused
   headroom from the cap, or `--salt-room
   <unused $>` to set it directly (`--salt-room` wins, and is refused above
   the cap). The offset is valued at your
   marginal rate (auto-estimated or `--marginal-fed`).

4. **Phantom-income guard.** If the supplied `--income` exceeds the pass-through
   income (Schedule 1 line 8) in `result.json`, the K-1 isn't in the return — the
   analyzer refuses rather than "saving" tax on untaxed income. Recompute with
   `/tax-compute` (K-1 included) first.

5. **Itemize vs standard deduction.** The forgone-SALT offset only applies if the
   taxpayer itemizes, which is read from `schedule_a.use_itemized`. Pass
   `--standard-deduction` for a non-itemizer whose return has no `schedule_a`
   section — they get no SALT deduction to displace, so the full PTET is
   incremental (offset suppressed).

## Usage

```bash
# Estimate CA + NY PTET savings (SALT cap assumed exhausted):
sh agents/lib/run.sh -m agents.lib.ptet --year 2025 \
 --income CA=200000 --income NY=100000

# Derive SALT headroom from the cap given your other state/local taxes:
sh agents/lib/run.sh -m agents.lib.ptet --year 2025 \
 --income CA=200000 --other-salt 28000 --marginal-fed 0.37

# ...or set the unused headroom directly:
sh agents/lib/run.sh -m agents.lib.ptet --year 2025 \
 --income CA=200000 --salt-room 8000 --marginal-fed 0.37

# Override a multi-owner entity's graduated rate:
sh agents/lib/run.sh -m agents.lib.ptet --year 2025 \
 --income NY=3000000 --ptet-rate NY=0.0965

# Mark QBI-eligible (§199A) K-1 income so the QBI clawback is priced in:
sh agents/lib/run.sh -m agents.lib.ptet --year 2025 \
 --income CA=200000 --qbi-eligible CA

# Deadline-reminder mode (no income → reads flags.k1_states, lists deadlines):
sh agents/lib/run.sh -m agents.lib.ptet --year 2025
```

## Caveats (v1)

- **A charitable deduction at its AGI limit refuses.** Where
  `schedule_a.charitable_carryforward` is above zero the gift hit its share-of-AGI
  cap, and moving AGI moves the cap while the tool holds the deduction, so the
  figure would read high. The tool stops; price the change with `/tax-compute`.
- **The deduction is the return's own line 12**, held at the baseline, so the
  election is priced from where the return actually sits. Electing lowers AGI, and
  with it an itemizer's medical-expense floor, which is not re-computed: there the
  benefit reads low, and the output says so.

- **Federal-only.** State savings is reported as $0: the owner's PTET credit
  generally offsets the entity tax (CA credit nonrefundable, 5-yr carryforward;
  NJ/NY refundable). The whole benefit of PTET is the federal deduction.
- **SALT-offset approximation.** The offset uses `min(headroom, total PTET)`;
  if fewer personal payments are actually displaced in the modeled year, it
  can overstate the lost deduction and understate federal savings. A nonzero
  offset carries a **SALT-OFFSET CONSERVATIVE FLOOR** notice. Recompute the full
  payment and deduction comparison to price that case precisely; this floor
  describes only the SALT-offset approximation, and other caveats still apply.
- **CA / NY / NJ only.** Other states with PTET regimes (CT, IL, MA, NC, OR, …)
  are surfaced as a "not modeled" note rather than silently ignored.
- **SALT-cap-exhausted assumption** defaults on; if it's wrong the estimate
  *overstates* savings — the skill loudly caveats this and accepts `--other-salt`
  / `--salt-room`. The cap itself follows §164(b)(7)'s schedule, reverting to
  $10K in 2030, and the MAGI phasedown is applied from the return's
  `schedule_a.modified_agi` or `--magi` (step above); without either the output
  says the cap may be too high. That MAGI is the one without the election, and
  electing lowers it, so where the phasedown applies to headroom derived from
  `--other-salt` the benefit reads low, and the output says that too.
- **Graduated rate on your share** approximates the per-owner tax; verify against
  the entity's PTE return for multi-owner entities (use `--ptet-rate`).
- **QBI clawback is simplified.** When `--qbi-eligible` is set, the lost §199A
  deduction is modeled as `qbi_rate × PTET`, ignoring the §199A taxable-income and
  W-2-wage/UBIA limits — which often bind for high earners. Treat it as an upper
  bound on the clawback.
- **Statutory deadline dates only** (no weekend/holiday rollover), matching
  `/tax-calendar`. The CA June-15 note is year-aware: under AB 150 (TY2021–2025)
  the prepayment is a hard election prerequisite; under SB 132 (TY2026–2030) the
  election survives a missed payment but the credit drops 12.5% of the unpaid
  amount. The election is generally **annual and irrevocable** — confirm
  mechanics with the state authority before electing.
