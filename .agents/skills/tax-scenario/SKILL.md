---
name: tax-scenario
description: Scenario modeling — fork the computed result.json, apply one or more signed income deltas (e.g. roth_conversion=40000), recompute the federal tax effect, and report the marginal cost. Stackable. Use when the user asks a what-if question about extra income, a bonus, a conversion or a deduction change and wants the marginal tax effect.
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
- `result.json` with a computed `form_1040.line_24_total_tax` (else: run `/tax-compute`).
- `tax_law_{year}.json` (federal brackets for the filing status).
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

1. **Validate prerequisites.** Confirm `result.json`, `taxpayer_profile.json`, and
   `tax_law_{year}.json` exist. If `result.json` lacks a computed line 24, stop and tell
   the user to run `/tax-compute`.

2. **Pick a name and deltas.** Default name is `scenario`; pass `--name` for a meaningful
   label (a single path segment — letters, digits, `-`, `_`). To stack on a prior scenario,
   pass `--base scenarios/<other>.json`.

3. **Compute.** Run:
   ```bash
   python3 -m agents.lib.scenario --result result.json --workspace . --year {year} \
       --name {name} [--base scenarios/{other}.json] --json {KEY=AMOUNT ...}
   ```
   This writes `scenarios/{name}.json` (chmod 600) and emits the JSON report.

4. **Render.** Run the same command without `--json` to print the markdown report. Save it
   to `scenarios/{name}.md` and `chmod 600`. Present the bottom-line table and marginal rate.

5. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Scenario "{name}" TY{year} — base {base}, deltas {keys}. Run {today}.
   ```

## Security & safety
- Writes ONLY under `scenarios/`; a path guard refuses any other target and never
  overwrites `result.json` / `taxpayer_profile.json`.
- Scenario names are validated to a single safe path segment (no `/`, `\`, `..`, leading dot).
- `scenarios/` is gitignored — scenario files hold financial figures.
- All written files (`scenarios/*`) → `chmod 600`.
- Any delegated sub-task must have NO network access and must NOT write outside `scenarios/`.
- Never extract or store full SSNs.
