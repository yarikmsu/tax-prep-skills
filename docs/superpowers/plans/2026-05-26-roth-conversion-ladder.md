# `/tax-roth-convert` — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-26-roth-conversion-ladder-design.md`
**Date:** 2026-05-26 · **Backlog:** #6 · **Branch:** `feat/roth-conversion-ladder`

## Build order (TDD)

### 1. `compute_niit` keystone (`agents/lib/tax_computations.py`)
- Signature: `compute_niit(magi, net_investment_income, threshold, *, rate=0.038) -> float`
- 3.8% × `min(NII, max(0, MAGI − threshold))`. Returns 0 when `threshold is None`,
  `NII <= 0`, or MAGI below threshold. Rejects bool/non-numeric via `_coerce_numeric`.
- Add a self-test stanza in `__main__`. Tests in `test_tax_computations.py`.

### 2. `irmaa` schema (`agents/lib/tax_law_fetcher.py`)
- Add to `create_empty_tax_law().federal`:
  ```json
  "irmaa": {
    "lookback_years": 2,
    "tiers": {"single": [], "mfj": []}
  }
  ```
  Each tier: `{"magi_threshold": <lower bound>, "annual_surcharge_per_person": <$>}`.
  Empty lists until `/tax-setup` fetches — consumer degrades to "IRMAA not modeled".

### 3. `agents/lib/roth_ladder.py`
- Reuse from `scenario.py`: `extract_baseline_inputs`, `_rate_kwargs`, `read_tax_year`,
  `save_scenario`, `_validate_name`, `_resolve_under`, `ScenarioError`.
- Reuse from `projection.py`: `_money`, `_num`, `_read_json`, `_full_status_key`,
  `_binary_status_key`, `ProjectionError`. From `rollover.py`: `_validate_year_arg`,
  `RolloverError`. From `tax_computations.py`: `compute_scenario`, `compute_niit`.
- `RothLadderError(Exception)`.
- Frozen dataclasses: `LadderRung` (amount, agi, taxable_income, fed_tax_anchored,
  niit_addon, total_tax, cumulative_cost, marginal_rate, flags: list[str]) and
  `LadderReport` (schema, name, tax_year, created, filing_status, max_amount, step,
  anchor, baseline_nii, rungs, threshold_flags).
- `parse_sweep(max_amount, step) -> list[int]` — validates and returns `[0, step, …, max]`.
- `_net_investment_income(inputs) -> float` — interest + ordinary dividends + max(0, capital_gains).
- `_irmaa_tiers(tax_law, status)` / `_irmaa_surcharge(magi, tiers)` — degrade to None.
- `build_ladder(result, profile, tax_law, year, *, max_amount, step, name, today)` —
  anchor zero-delta to line 24, sweep `ira_distribution` deltas, layer incremental NIIT,
  detect first-crossing flags for NIIT onset / each IRMAA tier / LTCG 0→15 & 15→20.
- `render_markdown` — "What this models", ladder table, "Threshold crossings", caveats.
- `_report_to_dict`, `_main(argv)` CLI mirroring `scenario._main` (+ `--max`, `--step`).

### 4. `/tax-roth-convert` skill (`agents/skills/tax-roth-convert.md`)
- Mirror `tax-scenario.md`: prerequisites, CLI invocation, render+save+chmod, audit note,
  security. Note IRMAA needs `irmaa` tiers in tax_law (else flagged "not modeled").

### 5. Fixtures + tests
- `tests/fixtures/roth/` — `result.json` (computed, line 24 set), `taxpayer_profile.json`,
  `tax_law_{year}.json` with populated `niit`, `irmaa.tiers`, and cg limits.
- `tests/test_roth_ladder.py` — sweep math, anchoring ($0 rung == line 24), NIIT increment,
  IRMAA + LTCG + NIIT flag steps, degrade-without-irmaa, save guard, CLI smoke, validation errors.

### 6. Docs
- CLAUDE.md: architecture diagram (planning branch), lib list (`roth_ladder.py`), read-only
  planning skills note (it WRITES like scenario), test-count line, tax_law schema key list
  (add `niit`/`irmaa`). Roadmap: mark #6 ✅. Update auto-memory.

## Non-negotiables
- Never mutate `result.json` / `taxpayer_profile.json`. Writes only under `scenarios/`.
- `compute_niit` rates-parameterized — no hardcoded thresholds (statutory $ live in tax_law).
- `$0` rung must equal `result.json` line 24 exactly (anchoring invariant).
- Full suite green; `python3 agents/lib/tax_computations.py` self-test passes.
