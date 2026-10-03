---
name: tax-scenario
description: Scenario modeling — fork the computed result.json, apply one or more signed income deltas (e.g. roth_conversion=40000), recompute the federal tax effect, and report the marginal cost. Stackable. Use when the user asks a what-if question about extra income, a bonus, a conversion or a deduction change and wants the marginal tax effect.
license: Apache-2.0
---

# /tax-scenario — What-If Scenario Modeling

Forks the **computed** baseline in `result.json`, applies one or more signed income
deltas, recomputes the federal tax effect, and reports the marginal cost (Δ tax per
Δ income), the projected scenario tax, and the movement in AGI / taxable income /
effective rate. Saves each scenario to `scenarios/<name>.json` so scenarios can be
**stacked** (`--base scenarios/<other>.json`). Read-only with respect to the baseline:
never modifies `result.json` or `taxpayer_profile.json`. Federal only in v1.

The absolute scenario tax is **anchored** to the real `result.json` line 24 — only the
marginal effect of the delta is modeled, since the recompute is a simplified federal
model that does not reproduce every credit/schedule of the filed return.

## Prerequisites
- `result.json` with a computed `form_1040.line_24_total_tax` and
  `line_12_standard_deduction` — the deduction the return took, standard or
  itemized, which the engine prices from and refuses without (else: run `/tax-compute`).
- `tax_law_{year}.json` (federal brackets for the filing status), with populated `federal.capital_gains_rates` (both `zero_pct_limit`
  and `fifteen_pct_limit` for the filing status) whenever the return has qualified
  dividends or a net long-term gain: the engine REFUSES to price that income at ordinary
  rates and names `/tax-setup` as the fix. A wages-only return runs without them.
- `taxpayer_profile.json` (for CTC-eligible dependent count).

## Delta grammar
`KEY=AMOUNT` (signed; a leading `+` on the key or value is allowed). Repeated keys sum.
Recognized keys: `wages`, `taxable_interest`, `ordinary_dividends`, `qualified_dividends`,
`capital_gains` (net LTCG), `ira_distribution`, `roth_conversion` (alias → `ira_distribution`),
`self_employment_net`, `other_income`. (Withholding/estimated-payment deltas are out of scope
in v1 — this skill reports the tax effect, not the balance due.)

`qualified_dividends` is the qualified SPLIT of `ordinary_dividends` (1040 line 3a ⊆ 3b): it
shifts how much of ordinary gets the preferential rate but does not change gross income on its
own (clamped to ≤ ordinary). To add dividend income, raise `ordinary_dividends` and, if it's
qualified, add a matching `qualified_dividends`. Deltas apply verbatim, so any delta is exactly
reversible when stacking.

## Steps

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

1. **Validate prerequisites.** Confirm `result.json`, `taxpayer_profile.json`, and
   `tax_law_{year}.json` exist. If `result.json` lacks a computed line 24 or line 12,
   stop and tell the user to run `/tax-compute`.

2. **Pick a name and deltas.** Default name is `scenario`; pass `--name` for a meaningful
   label (a single path segment — letters, digits, `-`, `_`). To stack on a prior scenario,
   pass `--base scenarios/<other>.json`. A base priced on a different line 12, or saved
   before the anchor recorded one, is refused: re-run it from `result.json` first. A
   return whose charitable deduction hit its AGI limit (`schedule_a.charitable_carryforward`
   above zero) refuses unless complete inputs re-price it and
   `schedule_a.charitable_limits_confirmed` explicitly confirms public-charity
   cash gifts and appreciated-property noncash gifts. Re-run `/tax-compute` to
   record those classifications; never set the flag to bypass a refusal.

3. **Compute.** Run:
   ```bash
   sh agents/lib/run.sh -m agents.lib.scenario --result result.json --year {year} \
 --name {name} [--base scenarios/{other}.json] --json {KEY=AMOUNT ...}
   ```
   This writes `scenarios/{name}.json` with owner-only permissions through
   `agents.lib.secure_fs` and emits the JSON report.

4. **Render.** Run the same command without `--json` to print the markdown report. Save it
   to `scenarios/{name}.md`, immediately run
   `sh agents/lib/run.sh -m agents.lib.secure_fs scenarios/{name}.md`, and present the
   bottom-line table and marginal rate.

5. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Scenario "{name}" TY{year} — base {base}, deltas {keys}. Run {today}.
   ```
   Then run `sh agents/lib/run.sh -m agents.lib.secure_fs answers.md` in case the write
   replaced the file rather than preserving its existing permissions.

## Security & safety
- Writes ONLY under `scenarios/`; a path guard refuses any other target and never
  overwrites `result.json` / `taxpayer_profile.json`.
- Scenario names are validated to a single safe path segment (no `/`, `\`, `..`, leading dot).
- `scenarios/` is gitignored — scenario files hold financial figures.
- Restrict every manually written file with `agents.lib.secure_fs`; the library
  already applies the same enforcement to its JSON output and directory.
- Any delegated sub-task must have NO network access and must NOT write outside `scenarios/`.
- Never extract or store full SSNs.
