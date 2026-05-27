# `/tax-scenario` — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-26-tax-scenario-design.md`
**Date:** 2026-05-26
**Branch:** `feat/scenario-modeling`

## Build sequence

### 1. `tax_computations.py::compute_scenario` (keystone)

Pure, rates-parameterized, no I/O. Mirrors `projection.project_federal_tax`'s math so
scenario numbers are internally consistent with the rest of the toolkit.

```python
def compute_scenario(baseline: dict, delta: dict, *, brackets, standard_deduction,
                     cg_zero_limit=None, cg_fifteen_limit=None, ss_wage_base=None,
                     qbi_rate=0.20, num_ctc_children=0, ctc_per_child=None,
                     ctc_phase_out_start=None, addl_medicare_threshold=None,
                     addl_medicare_rate=0.009) -> dict
```

- `inputs = baseline + delta` per recognized key (signed add); negatives clamp where the
  underlying form clamps (e.g. taxable income floored at 0).
- ordinary gross = wages + taxable_interest + ordinary_dividends + ira_distribution +
  other_income + self_employment_net + capital_gains (QDCG worksheet then carves out the
  preferential portion = qualified_dividends + net LTCG, exactly as `projection.py` does).
- SE tax + half-SE adjustment via `compute_se_tax`; QBI via `compute_qbi_deduction`.
- QDCG via `compute_qdcg_tax` when qualified/LTCG present and limits supplied, else
  `compute_bracket_tax`.
- CTC via `compute_child_tax_credit` (recomputed on the new AGI).
- Additional Medicare via `compute_additional_medicare` when threshold supplied.
- Returns `{gross_income, adjustments, agi, taxable_income, tax_before_credits, ctc,
  se_tax, additional_medicare, total_federal_tax, used_qdcg}`, all rounded.
- Add a `=== Scenario Test ===` block to the module self-test.

### 2. `agents/lib/scenario.py`

Frozen dataclasses (`ScenarioInputs`, `Outcome`, `ScenarioDiff`, `ScenarioReport`) +
pure builders + markdown renderer + guarded writer + CLI. Reuses:
- `tax_computations.compute_scenario`, `_coerce_numeric`
- `projection._money`, `_num`, `_read_json`, `_full_status_key`, `_binary_status_key`
- `rollover._validate_subpath`, `_validate_year_arg`, `RolloverError`

Key functions:
- `DELTA_KEYS` set + `DELTA_ALIASES = {"roth_conversion": "ira_distribution"}`.
- `parse_deltas(list[str]) -> dict` — split `KEY=AMOUNT`, alias, validate, sum repeats.
- `extract_baseline_inputs(result) -> dict` — per the spec mapping table.
- `read_tax_year(result, year)` — reuse the `meta.tax_year` guard pattern.
- `_rate_kwargs(profile, tax_law, filing_status, agi_for_ctc)` — pull rate params.
- `build_report(result, profile, tax_law, year, name, base, deltas, today)` — resolves
  base inputs + anchor (from `result` for base=result.json, or from a loaded base
  scenario file), applies deltas, calls `compute_scenario` twice (base & scenario),
  computes anchored diff + marginal rate + effective rates.
- `render_markdown(report)` and `_report_to_dict(report)`.
- `save_scenario(workspace, name, data)` — force path under `workspace/scenarios/`,
  validate name is a single segment, refuse `result.json`/`taxpayer_profile.json`,
  `mkdir -p scenarios/`, write, `chmod 600`.
- `_main(argv)` — args `--result --workspace --year [--name] [--base] [--as-of/today]
  [--json] [--no-save]` + positional `deltas…`. Federal-only.

### 3. `agents/skills/tax-scenario.md`

Mirror `tax-withholding.md`: prerequisites (computed `result.json`, `tax_law_{year}.json`),
steps (validate → run `python3 -m agents.lib.scenario …` JSON then markdown → save under
`scenarios/` + `chmod 600` → one-line `answers.md` audit note), security block.

### 4. Tests + fixtures

`tests/fixtures/scenario/` — one synthetic workspace: `result.json` (computed MFJ return
with wages/interest/dividends/cap gains + line 24 + line 11 AGI), `taxpayer_profile.json`
(one CTC child), `tax_law_2026.json` (copy the projection fixture rates).

`tests/test_scenario.py` (~30):
- `compute_scenario`: zero delta == baseline; +wages moves ordinary; +capital_gains uses
  QDCG; +roth_conversion alias == +ira_distribution; SE delta adds SE tax; CTC phase-out
  on big AGI delta; additional-medicare delta.
- `parse_deltas`: signed values, repeats summed, alias resolves, unknown key raises,
  non-numeric raises, empty raises.
- `extract_baseline_inputs`: line mapping incl. Schedule D net-LTCG preference + fallback.
- `read_tax_year`: wrong year raises, missing line 24 raises.
- `build_report`: anchored tax = line_24 + Δ; marginal rate sign; effective rates.
- stacking: base scenario file → second delta layers; anchor carried; cumulative deltas.
- `save_scenario`: writes under scenarios/, chmod 600, refuses `..`/separators in name,
  refuses path outside workspace, refuses result.json/profile targets.
- CLI subprocess: end-to-end JSON output for the fixture; `--no-save` skips write;
  security (`--base ../../etc`, year `'2026; rm -rf /'`) rejected.

### 5. Doc + ignore updates

- `CLAUDE.md`: add `/tax-scenario` to the planning branch in the architecture diagram;
  add `scenario.py` to the lib list; bump the test-count line; add a note to the
  read-only-planning-skills section clarifying scenario writes ONLY `scenarios/`.
- `docs/roadmap/2026-tax-planning-features.md`: mark #3 ✅ Done; update progress note.
- `.gitignore` + `agents/install.sh`: add `scenarios/`.

### 6. Verify + PR

- `source .venv/bin/activate && python3 -m pytest tests/ -v` (expect 219 + ~30).
- `python3 agents/lib/tax_computations.py` self-test passes.
- `python3 -m agents.lib.scenario … --json` smoke test on the fixture.
- Commit on `feat/scenario-modeling`; print the `git push --no-verify` command; open PR.

## Test-count bookkeeping

Main is at 219. This PR adds ~30 → ~249. Update the `CLAUDE.md` test-count line to the
exact number after the suite is green.
