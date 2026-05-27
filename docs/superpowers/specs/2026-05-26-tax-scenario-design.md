# `/tax-scenario` — Design Spec

**Status:** Approved for autonomous execution (user: "implement #3 … autoapprove all and execute")
**Date:** 2026-05-26
**Author:** Scenario-modeling design session — TY2026 planning backlog
**Roadmap source:** `docs/roadmap/2026-tax-planning-features.md` (item #3 of 12)
**Implements:** The `compute_scenario` keystone (architectural prereq #3) that backlog item #6 (Roth conversion ladder) also depends on.

## Purpose

Answer the planning question "**what happens to my tax if I do X?**" — where X is a
single hypothetical income change layered on top of an already-computed return: a
$40K Roth conversion, $20K of realized capital gains, a year-end bonus, an extra
contribution that lowers AGI, etc.

The toolkit already produces a real, computed `result.json`. Scenario modeling forks
that baseline, applies **one signed income delta**, recomputes the federal tax effect,
and reports the marginal cost (Δ tax per Δ income), the projected scenario tax, and the
movement in AGI / taxable income / bracket / effective rate. Scenarios are **stackable**:
a second delta can be layered on a saved scenario to model a sequence of moves.

## Scope

### In scope (v1)

- New pure function `compute_scenario(baseline, delta, …)` in `tax_computations.py` —
  the keystone. Rates-parameterized, no I/O, returns a federal outcome dict.
- New module `agents/lib/scenario.py` — baseline extraction from `result.json`, delta
  parsing, anchored diff, markdown renderer, guarded `scenarios/<name>.json` writer, CLI.
- New skill `/tax-scenario <name> <delta…>` (`agents/skills/tax-scenario.md`).
- Stacking via `--base scenarios/<other>.json` (default base = `result.json`).
- Test suite: ~30 tests in `tests/test_scenario.py` + `tests/fixtures/scenario/`.
- `scenarios/` added to `.gitignore` (and the `install.sh` append list) — runtime
  scenario files hold financial PII.

### Out of scope (deferred)

- **State scenario tax** — federal only in v1 (mirrors `/tax-withholding` v1). State is a
  caveat + a documented follow-up. `compute_scenario` is federal; a future `state=` outcome
  can reuse `compute_bracket_tax`.
- **NIIT / IRMAA / AMT threshold flags** — these belong to backlog #6 (Roth ladder), which
  builds on this keystone. v1 reports marginal rate but does not flag 3.8% NIIT or IRMAA
  cliffs.
- **Delta keys beyond the income set below** (no per-credit deltas, no filing-status change
  scenarios — change filing status by re-running `/tax-compute`).
- **Status-specific capital-gains brackets for MFS/HOH** — the `tax_law` schema keys
  `capital_gains_rates` only `single`/`mfj` (CLAUDE.md gotcha), and `_rate_kwargs` maps every
  non-MFJ status to `single`, consistent with `/tax-compute` and `/tax-project`. So MFS/HOH
  scenarios use the single-status CG breakpoints; an MFS gain near the 15%/20% boundary may
  read low. Correcting this is a toolkit-wide schema change (add mfs/hoh CG limits to
  `tax_law_fetcher` + the `/tax-setup` fetch + all consumers), not a scenario-only fix.
- **Mutating `result.json`** — never. The baseline is sacred; scenarios are derived siblings.

## Why anchor instead of pure recompute

`compute_scenario` is a *simplified* federal recompute: it cannot reproduce every credit and
schedule captured in a real `result.json`. So the absolute number it produces for the
baseline will generally differ from the filed `form_1040.line_24_total_tax`.

The fix is to report only the **marginal effect** of the delta and add it to the real filed
number:

```
Δtax                 = compute_scenario(inputs, delta).total − compute_scenario(inputs, {}).total
anchored_scenario_tax = result.json line_24  +  Δtax
```

This isolates the delta's effect (which the simplified model captures well) and anchors the
absolute figure to the actual return (which the model cannot fully reproduce). The report
shows the real baseline, the modeled Δ, and the anchored projection — and states the model
is a simplified recompute.

## Delta grammar

Each delta is `KEY=AMOUNT` with an optional leading sign on the value. All deltas are
**signed additive adjustments** to a normalized income input (never absolute-set):

```
/tax-scenario roth40k roth_conversion=40000
/tax-scenario gains    capital_gains=20000
/tax-scenario trim     wages=-10000
/tax-scenario combo    roth_conversion=40000 capital_gains=20000   # two deltas, one scenario
```

Recognized keys (normalized income inputs):

| Delta key | Effect on the federal recompute |
|---|---|
| `wages` | ordinary income |
| `taxable_interest` | ordinary income |
| `ordinary_dividends` | ordinary income (the non-qualified portion is taxed at ordinary rates) |
| `qualified_dividends` | the qualified SPLIT of ordinary dividends (1040 line 3a ⊆ line 3b). Does NOT change gross on its own — it only shifts how much of `ordinary_dividends` gets the preferential QDCG rate, and the keystone clamps it to `≤ ordinary_dividends`. To ADD dividend income, raise `ordinary_dividends` (optionally with a matching `qualified_dividends` portion). Applied verbatim, so every delta is exactly reversible when stacking. |
| `capital_gains` | net LTCG → preferential QDCG rate |
| `ira_distribution` | ordinary income |
| `roth_conversion` | alias → `ira_distribution` (a conversion is taxable ordinary income) |
| `self_employment_net` | ordinary income + SE tax + QBI |
| `other_income` | ordinary income (Schedule 1) |

Unknown keys are rejected with the list of valid keys. Non-numeric amounts are rejected.
Withholding / estimated-payment deltas are **out of scope in v1**: they shift the balance
due, not the tax, and the report has no balance section — accepting them would be a silent
no-op. A future balance-aware revision can reintroduce them.

## Baseline extraction (result.json → normalized inputs)

| Normalized input | Source path in `result.json` |
|---|---|
| `wages` | `form_1040.line_1z_total_wages` (fallback `line_1a_wages`) |
| `taxable_interest` | `form_1040.line_2b_taxable_interest` |
| `ordinary_dividends` | `form_1040.line_3b_ordinary_dividends` |
| `qualified_dividends` | `form_1040.line_3a_qualified_dividends` |
| `capital_gains` (net LTCG) | `schedule_d.part_ii_long_term.line_15_net_long_term` if Schedule D present, else `form_1040.line_7_capital_gain` |
| `ira_distribution` | `form_1040.line_4b_taxable_ira` + `line_5b_taxable_pensions` |
| `self_employment_net` | `schedule_c.line_31_net` (if present) |
| `other_income` | `form_1040.line_8_schedule1_other` **+ net short-term capital gain** (`schedule_d.part_i_short_term.line_7_net_short_term`, when Schedule D present) **+ taxable Social Security** (`form_1040.line_6b_taxable_ss`) — all ordinary income, so they fold into the ordinary bucket while LTCG stays preferential. A net capital LOSS contributes to gross only up to the $3,000 annual limit. |

`filing_status` ← `result.meta.filing_status` (fallback `taxpayer_profile.filing_status`).
`num_ctc_children` ← count of `taxpayer_profile.dependents[].ctc_eligible == true`.
Rates ← `tax_law_{year}.json` (same accessors as `projection.py`).

**v1 simplification (documented in the report):** capital-gains delta is treated entirely as
net LTCG at preferential rates; a short-term gain would be ordinary. Use the `wages` /
`other_income` keys to model an ordinary short-term gain instead.

## Stacking

`scenarios/<name>.json` stores the **resolved post-delta normalized inputs**, the cumulative
`applied_deltas`, and a constant `anchor` block `{line_24, baseline_recompute_total}` carried
from the original `result.json`. A stacked scenario (`--base scenarios/<other>.json`) reads
the base file's `inputs` as *its* baseline, layers the new delta, and carries `anchor`
unchanged. The diff shown is incremental (vs the immediate base); the anchored absolute tax
is always `anchor.line_24 + (scenario_total − anchor.baseline_recompute_total)`. The report
also anchors the immediate base the same way (`baseline_anchored_total_tax`), so the rendered
table reconciles for stacked runs (Baseline + Δ == Scenario); for an unstacked run that figure
collapses to `anchor.line_24`.

## Artifacts

| Artifact | Type | Approx. size |
|---|---|---|
| `tax_computations.py::compute_scenario` | new function | ~55 LOC |
| `agents/lib/scenario.py` | new module | ~260 LOC |
| `agents/skills/tax-scenario.md` | new skill | ~70 lines |
| `tests/test_scenario.py` | new tests | ~30 tests |
| `tests/fixtures/scenario/` | new fixtures | 1 workspace (result, profile, tax_law) |
| `CLAUDE.md` | modified | architecture + lib list + test count |
| `docs/roadmap/2026-tax-planning-features.md` | modified | mark #3 done |
| `.gitignore`, `agents/install.sh` | modified | add `scenarios/` |

## scenarios/<name>.json schema (v1)

```json
{
  "schema": "tax-scenario/v1",
  "name": "roth40k",
  "tax_year": 2026,
  "created": "2026-05-26",
  "base": "result.json",
  "filing_status": "MFJ",
  "applied_deltas": [{"key": "roth_conversion", "amount": 40000.0}],
  "anchor": {"line_24": 42000.0, "baseline_recompute_total": 39850.0},
  "inputs": { "...resolved post-delta normalized inputs..." },
  "base_outcome": { "...zero-delta recompute of the base inputs..." },
  "scenario_outcome": { "...recompute after this delta..." },
  "diff": {
    "delta_income": 40000.0,
    "delta_agi": 40000.0,
    "delta_taxable_income": 40000.0,
    "delta_total_tax": 8800.0,
    "marginal_rate": 0.22,
    "baseline_anchored_total_tax": 42000.0,
    "anchored_scenario_total_tax": 50800.0,
    "baseline_effective_rate": 0.20,
    "scenario_effective_rate": 0.214
  }
}
```

## Error handling

| Scenario | Behavior |
|---|---|
| `result.json` missing / `meta.tax_year != --year` | Refuse (reuse the `read_liability`-style guard). |
| `result.json` line 24 absent / uncomputed template | Refuse — "run `/tax-compute` first." |
| No federal brackets for status in `tax_law` | Refuse — "run `/tax-setup` to fetch." |
| Unknown delta key / non-numeric amount / no deltas given | Refuse with the valid-key list. |
| `--base` outside workspace or not under `scenarios/` | Refuse (path-traversal guard). |
| Output path resolves outside `workspace/scenarios/` | Refuse before any write. |
| `--name` contains path separators / `..` | Refuse — names are a single path segment. |

## Security

- Reuses `rollover._validate_subpath` / `_validate_year_arg`. The output name is validated to
  a single safe path segment; the write target is forced under `workspace/scenarios/`.
- Never writes `result.json` or `taxpayer_profile.json`; a guard asserts this.
- Every written file → `chmod 600`.
- `scenarios/` added to `.gitignore`. Fixtures are synthetic and live under `tests/fixtures/`.

## Open follow-ups (post-v1)

- **State scenario tax** — add a `state` outcome to `compute_scenario`/`scenario.py`.
- **Backlog #6 (Roth ladder)** — sweep `compute_scenario` across $0–$100K in $5K steps;
  add NIIT / IRMAA / LTCG-bracket-bump threshold flags on top of this keystone.
- **Short-term vs long-term gains** — split the `capital_gains` delta key.
