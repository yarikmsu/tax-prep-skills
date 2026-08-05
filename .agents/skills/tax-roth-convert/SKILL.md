---
name: tax-roth-convert
description: Roth conversion ladder modeler — sweep conversion amounts $0–$100K, chart the marginal cost per dollar, and flag exactly which conversion trips the LTCG 0/15/20% breakpoints, NIIT, and IRMAA tiers. Federal, planning-only. Use when the user asks whether or how much to convert to a Roth, or mentions a Roth conversion ladder, filling a bracket, or IRMAA.
license: Apache-2.0
---

# /tax-roth-convert — Roth Conversion Ladder Modeler

Sweeps Roth conversion amounts from `$0` to `--max` (default `$100,000`) in `--step`
increments (default `$5,000`), recomputing the **federal** tax effect of each rung and
reporting the marginal cost per dollar. A Roth conversion is taxable **ordinary income**,
modeled as an extra traditional-IRA distribution. The output flags exactly which rung
first trips each planning threshold:

- **LTCG bracket bumping** — conversion stacks under long-term gains / qualified
  dividends and pushes them over the 0%/15%/20% preferential breakpoints.
- **NIIT** — conversion lifts MAGI past the Net Investment Income Tax threshold, hitting
  existing investment income with the 3.8% surtax.
- **IRMAA** — conversion crosses a Medicare Part B/D premium surcharge tier (2-year
  lookback).

The absolute tax at each rung is **anchored** to your real `result.json` line 24 (same
contract as `/tax-scenario`) — only the marginal effect of the conversion is modeled.
Read-only with respect to the baseline: never modifies `result.json` or
`taxpayer_profile.json`. Federal only in v1.

## Prerequisites
- `result.json` with a computed `form_1040.line_24_total_tax` (else: run `/tax-compute`).
- `tax_law_{year}.json` (federal brackets, `niit`, `capital_gains_rates` for the status).
- `taxpayer_profile.json` (for CTC-eligible dependent count).
- **For IRMAA flags:** `tax_law_{year}.json` must have `federal.irmaa.tiers.{single|mfj}`
  populated (MAGI thresholds + per-beneficiary annual surcharge). If empty, the ladder
  still runs but reports "IRMAA not modeled" — re-run `/tax-setup` to fetch the tiers.
- **For Form 8606 basis pro-rata (optional):** pass `--ira-value` (total beginning-of-year
  traditional/SEP/SIMPLE IRA balance) to dilute each rung's taxable conversion by the basis
  ratio. `--ira-basis` defaults to `taxpayer_profile.json prior_year.ira_basis`. Without
  `--ira-value` the ladder treats the full conversion as taxable (v1 default).

## Steps

1. **Validate prerequisites.** Confirm `result.json`, `taxpayer_profile.json`, and
   `tax_law_{year}.json` exist. If `result.json` lacks a computed line 24, stop and tell
   the user to run `/tax-compute`. If `federal.irmaa.tiers` is empty, note IRMAA will be
   skipped and offer to fetch it via `/tax-setup`.

2. **Pick the sweep.** Default `--max 100000 --step 5000`. Use a finer `--step` near a
   bracket the user cares about, or a larger `--max` for big conversions. The sweep is
   capped at 200 rungs.

3. **Compute.** Run:
   ```bash
   python3 -m agents.lib.roth_ladder --result result.json --workspace . --year {year} \
       --name {name} --max {max} --step {step} [--ira-value {balance} --ira-basis {basis}] --json
   ```
   Add `--ira-value` (and optionally `--ira-basis`) to apply the Form 8606 pro-rata rule so
   each rung's *taxable* conversion is diluted by the after-tax basis fraction.
   This writes `scenarios/{name}.json` (chmod 600, default name `roth-ladder`) and emits
   the JSON report.

4. **Render.** Run the same command without `--json` to print the markdown report. Save it
   to `scenarios/{name}.md` and `chmod 600`. Present the ladder table (marginal rate per
   rung) and the "Threshold crossings" section so the user can pick a conversion amount
   that fills a bracket without spilling into the next penalty zone.

5. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Roth ladder "{name}" TY{year} — swept $0..{max} step {step}. Run {today}.
   ```

## How to read it
- **Marginal rate** column already reflects LTCG bracket bumping (the QDCG worksheet
  stacks the conversion under preferential income), so a jump in marginal rate at a rung
  with no flag usually means an ordinary-bracket step.
- **NIIT add** column is the incremental 3.8% surtax the conversion creates (baseline NIIT
  is already inside line 24); it caps once exposed investment income equals total NII.
- **IRMAA** is a Medicare *premium* surcharge, NOT income tax — it is shown as a flagged
  side cost and is never folded into the tax total. The surcharge is per beneficiary;
  double it if both spouses are on Medicare.

## Caveats (v1)
- Federal only — state tax on the conversion is not modeled.
- Non-deductible IRA **basis** (Form 8606 pro-rata) is applied when `--ira-value` (and
  `--ira-basis`/profile basis) are supplied; otherwise the full conversion is taxable. Basis
  aggregates all traditional/SEP/SIMPLE IRAs (per IRS rules) and is per-individual.
- MAGI approximated as AGI (no tax-exempt-interest / foreign-EI add-backs).
- Second-order Social Security taxability is not re-computed.
- MFS/HOH use single-status capital-gains and IRMAA schedules (toolkit-wide simplification).
- A planning estimate, not a filed return.

## Security & safety
- Writes ONLY under `scenarios/`; a path guard (reused from `/tax-scenario`) refuses any
  other target and never overwrites `result.json` / `taxpayer_profile.json`.
- Scenario names are validated to a single safe path segment (no `/`, `\`, `..`, leading dot).
- `scenarios/` is gitignored — ladder files hold financial figures.
- All written files (`scenarios/*`) → `chmod 600`.
- Any delegated sub-task must have NO network access and must NOT write outside `scenarios/`.
- Never extract or store full SSNs.
