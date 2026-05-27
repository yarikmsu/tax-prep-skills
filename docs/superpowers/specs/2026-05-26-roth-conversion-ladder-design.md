# `/tax-roth-convert` — Design Spec

**Status:** Draft, awaiting user approval
**Date:** 2026-05-26
**Author:** Implementation session — TY2026 planning backlog item #6
**Roadmap source:** `docs/roadmap/2026-tax-planning-features.md` (item #6 of 12)
**Builds on:** `compute_scenario` keystone + `agents/lib/scenario.py` (#3, PR #15)

## Purpose

A Roth conversion moves money from a traditional (pre-tax) IRA into a Roth IRA and
recognizes the converted amount as ordinary income *this year* in exchange for
tax-free growth and withdrawals later. The planning question is always **"how much
should I convert?"** — and the answer is rarely "all of it" because each marginal
dollar of conversion can:

1. climb into a higher ordinary bracket,
2. push **long-term capital gains / qualified dividends** over the 0%/15%/20%
   preferential breakpoints (stacking effect),
3. lift **MAGI** past the **NIIT** threshold so existing investment income gets hit
   with the 3.8% surtax, and
4. cross an **IRMAA** tier, raising Medicare Part B/D premiums two years out.

This skill sweeps conversion amounts across a range and produces a per-step table of
marginal cost plus a flagged list of exactly which step trips each of those four
thresholds — so the user can pick the conversion amount that fills a bracket without
spilling into the next penalty zone.

## Scope

### In scope (v1)

- New skill `/tax-roth-convert` — sweeps conversions `$0 … --max` in `--step` increments
- New Python module `agents/lib/roth_ladder.py` — pure sweep + flag detection + markdown
  renderer + guarded writer + CLI, mirroring `scenario.py`
- New `compute_niit(magi, net_investment_income, threshold, *, rate)` helper in
  `tax_computations.py` — the only federal-tax component the `compute_scenario` keystone
  omits that a conversion can trigger; pure and rates-parameterized like its siblings
- New `irmaa` block in `tax_law_fetcher.create_empty_tax_law` — MAGI tier thresholds +
  per-beneficiary annual surcharge, fetched by `/tax-setup` (degrades gracefully if absent)
- Reuses `scenario.extract_baseline_inputs`, `scenario._rate_kwargs`, `scenario.save_scenario`,
  the `compute_scenario` keystone, and projection/rollover helpers — no duplication
- Test suite: ~30 new tests across `tests/test_roth_ladder.py` + `compute_niit` cases in
  `tests/test_tax_computations.py`

### Out of scope (deferred)

- **State tax** — federal only in v1 (same boundary as `/tax-scenario`)
- **Form 8606 pro-rata basis** — a conversion with non-deductible basis is partly
  tax-free; v1 treats the full conversion as taxable. Closed by backlog item #7.
- **Second-order Social Security taxability** — a conversion that pushes more SS into
  the taxable range is not re-computed (inherited limitation from `compute_scenario`).
- **Multi-year ladders** — v1 models a single tax year. The intended multi-year ladder
  ("convert $X/yr for N years to fill the 24% bracket before RMDs") is a v2 wrapper that
  calls this single-year engine per year.
- **MFS/HOH capital-gains brackets** — collapse to single-status CG limits (documented
  toolkit-wide `tax_law` simplification; see CLAUDE.md cross-cutting follow-up).

## Key design decision — anchoring (inherited from #3)

Like `/tax-scenario`, the **absolute** tax at each rung is anchored to the real
`result.json` line 24; only the *marginal* effect of the conversion is modeled:

```
anchored_tax(amount) = line_24
                     + (keystone_recompute(amount) − keystone_recompute(0))   # ordinary/QDCG/CTC/SE/medicare
                     + (niit(MAGI(amount)) − niit(MAGI(0)))                    # NIIT add-on
```

The NIIT add-on is layered on **outside** the keystone (the keystone deliberately omits
NIIT). Baseline NIIT is already inside line 24, so only the *increment* is added — the
$0 rung therefore equals line 24 exactly, keeping the table self-consistent.

NIIT uses **net investment income from the baseline only** (taxable interest + ordinary
dividends + net positive capital gains). A Roth conversion is *not* itself investment
income, so NII is fixed across the sweep — the conversion raises NIIT only by lifting
MAGI past the threshold, which is exactly the planning signal we want to surface.

MAGI is approximated as AGI for planning (v1 has no tax-exempt-interest / foreign-EI line
to add back); documented in caveats.

## Threshold flags

| Flag | Source | Trips when | Estimated cost shown |
|------|--------|-----------|----------------------|
| **LTCG bump 0%→15%** | `cg_zero_limit` (tax_law) | taxable income crosses the 0% ceiling **and** preferential income > 0 | (already inside marginal rate via QDCG worksheet) |
| **LTCG bump 15%→20%** | `cg_fifteen_limit` (tax_law) | taxable income crosses the 15% ceiling **and** preferential income > 0 | (already inside marginal rate) |
| **NIIT onset** | `federal.niit.threshold` | MAGI crosses the threshold **and** baseline NII > 0 | incremental 3.8% × exposed NII at year end |
| **IRMAA tier** | `federal.irmaa.tiers[status]` | MAGI crosses a tier threshold not already crossed at $0 | annual surcharge per beneficiary (×lookback note) |

LTCG bumping needs no separate cost column — it is already inside the keystone's QDCG
worksheet, so the marginal-rate column already reflects it. The flag just names *where*
it happens. NIIT and IRMAA are surfaced as explicit add-ons because the keystone omits
them. IRMAA is **not** added into the tax total (it is a Medicare premium, not income
tax) — it is a flagged side cost.

## Write behavior

Read-only with respect to `result.json` / `taxpayer_profile.json` (never mutated).
Writes a single `scenarios/<name>.json` (default `roth-ladder`) via the **reused**
`scenario.save_scenario` guard (target forced under `workspace/scenarios/`, single-segment
name, refuses source-of-truth files, `chmod 600`). Schema tag `tax-roth-ladder/v1`.
`scenarios/` is already gitignored.

## CLI

```bash
python3 -m agents.lib.roth_ladder --result result.json --workspace . --year {year} \
    --name roth-ladder --max 100000 --step 5000 [--json] [--no-save]
```

Defaults: `--max 100000`, `--step 5000` (the roadmap's "$0–$100K in $5K steps").
`--max` and `--step` are validated: step > 0, max ≥ 0, step ≤ max, capped step count
(≤ 200 rungs) to bound output.
