# `/tax-project` — Design Spec

**Status:** Draft, awaiting user approval
**Date:** 2026-05-25
**Author:** Brainstorming session — mid-year projection feature
**Roadmap source:** `docs/roadmap/2026-tax-planning-features.md` (item #2 of 12)
**Depends on:** `agents/lib/rollover.py` `load_prior` (merged, PR #8). Stylistic precedent: `agents/lib/yoy_diff.py` (PR #10).

## Purpose

Move the toolkit from a post-mortem return preparer toward a proactive planning tool. `/tax-project` takes year-to-date (YTD) income and withholding figures partway through the tax year, projects the full-year federal (and, when possible, state) tax liability, and tells the taxpayer — while there is still time to act — whether they are on track to owe a penalty and what to do about it.

It answers three questions a taxpayer asks mid-year:

1. *What will I owe (or get refunded) if the rest of the year looks like the year so far?*
2. *Am I withholding/paying enough to avoid an underpayment penalty (safe harbor)?*
3. *If not, how much extra should I withhold per paycheck (W-4 line 4(c)) or pay in remaining 1040-ES quarters?*

It is **re-runnable** — a taxpayer runs it each quarter (or after any material income change) and gets an updated projection.

## Scope

### In scope (v1)

- New Python module `agents/lib/projection.py` — pure compute (annualize → project tax → safe harbor → W-4/vouchers) + markdown renderer + CLI.
- New skill `agents/skills/tax-project.md` — gathers/annualizes inputs, calls projection.py, renders the report.
- New template `agents/templates/projection_inputs_template.json` — YTD actuals + forecast overrides (paste/JSON input path).
- Federal projection: annualized wages + lumpy forecast overrides + recurring non-wage income → taxable income → federal tax → refund/balance.
- Safe-harbor analysis: 90%-of-current vs 100%/110%-of-prior; required annual payment; remaining-quarter 1040-ES schedule.
- W-4 line 4(c) per-paycheck adjustment (two targets: avoid penalty, zero balance).
- State projection **when** a prior-year state baseline exists and `tax_law_{year}.json` carries state brackets; otherwise skipped with an explicit note.
- Re-runnable, timestamped reports saved under `projections/TY{year}_asof_{MM-DD}.md`.
- Test suite: ~30 tests in `tests/test_projection.py`.

### Out of scope (deferred)

- **Paystub PDF parsing.** v1 uses the paste/JSON input path (roadmap's recommended cheap path). The `ytd` block in `projection_inputs.json` is the stable interface a future `/tax-parse-paystub` feature would populate. A `_paystub_parser_hook` field documents this.
- **Versioned artifacts** (`baseline.json` / `scenarios/` / `priors/`) — roadmap prerequisite #1. Deferred to scenario modeling (#3) where it is actually exercised. Projection reuses rollover's `load_prior` to read the prior-year baseline directly from `archive/{year-1}/`.
- **`compute_scenario(baseline, delta)` wrapper** — roadmap prerequisite #3. Projection builds a fresh full-year estimate from projected inputs; it is not a delta on a baseline, so it does not need this wrapper. Deferred to #3.
- **`/tax-watchlist` artifact** — roadmap prerequisite #4. Not needed for projection.
- **Multi-state / part-year state projection.** v1 projects a single state, only when a prior-year state section anchors it.
- **AMT, NIIT, IRMAA projection.** v1 projects regular tax + SE tax + Additional Medicare + CTC. NIIT/AMT/IRMAA are documented as v1.1 follow-ups.
- **Mutating the real return.** `/tax-project` never writes `result.json` or `taxpayer_profile.json` — it is read-only toward the filed return, exactly like `yoy_diff.py`.

## User-facing flow

### Mid-year run (returning user, prior year archived)

```
/tax-project --as-of 06-30

1. Validate prerequisites
   ├─ taxpayer_profile.json + tax_law_{year}.json exist (else: "run /tax-setup {year} first")
   └─ projection_inputs.json exists? If not → create from template + interview
        (YTD wages, fed/state withholding, pay frequency, as-of date, lumpy items)

2. Resolve prior-year baseline
   ├─ archive/{year-1}/ exists → load_prior() ; pre-fill recurring_from_prior
   │    (interest/dividends) as editable defaults ; capture prior total tax + AGI
   └─ absent → degraded mode (90%-only safe harbor ; recurring defaults to 0)

3. Confirm annualization inputs
   └─ show elapsed fraction + annualized-vs-full-year split ; user corrects lumpy overrides

4. Compute
   └─ python3 -m agents.lib.projection --inputs projection_inputs.json \
        --workspace . --year {year} --as-of 06-30 --json

5. Render report  →  print + save projections/TY{year}_asof_06-30.md (chmod 600)

6. Append one-line audit note to answers.md (no dollar PII duplicated)
```

### First-time / no-archive user (degraded mode)

`/tax-project` still runs. With no `archive/{year-1}/`:

- Safe harbor offers **only** the 90%-of-projected-current-year test (the 100%/110%-of-prior test needs last year's tax).
- Recurring non-wage income (`recurring_from_prior`) defaults to 0; user may enter manually.
- The report carries a degraded-mode banner naming the missing baseline.

This mirrors how `yoy_diff.py` degrades to a skip-note when no prior archive exists.

### Re-run (quarterly)

Re-running with a later `--as-of` produces a fresh timestamped report under `projections/`. Prior reports are not overwritten — the taxpayer keeps a quarterly trail.

## Architecture

### Directory layout

```
Repo root (during TY2026 preparation)
├── archive/
│   └── 2025/                              ← prior-year baseline (from /tax-archive)
│       ├── result.json                    ← prior total tax + AGI + recurring payers
│       └── taxpayer_profile.json
├── taxpayer_profile.json                  ← current year (from /tax-setup)
├── tax_law_2026.json                      ← current-year brackets/rates
├── projection_inputs.json                 ← NEW — YTD actuals + forecast overrides
├── projections/                           ← NEW — timestamped reports (gitignored, chmod 600)
│   └── TY2026_asof_06-30.md
├── answers.md                             ← one-line audit note appended
└── agents/
    ├── lib/
    │   ├── projection.py                  ← NEW
    │   ├── rollover.py                     ← reused (load_prior, _get_path, _validate_subpath)
    │   └── tax_computations.py             ← reused (bracket/QDCG/SE/CTC/Add'l Medicare)
    ├── skills/
    │   └── tax-project.md                  ← NEW
    └── templates/
        └── projection_inputs_template.json ← NEW
```

### New artifacts summary

| Artifact | Type | Approx. size |
|---|---|---|
| `agents/lib/projection.py` | Python module | ~350 LOC |
| `agents/skills/tax-project.md` | Markdown skill | ~140 lines |
| `agents/templates/projection_inputs_template.json` | JSON template | ~40 lines |
| `tests/test_projection.py` | New test file | ~30 tests |
| `tests/fixtures/projection/` | New fixtures dir | 1 synthetic archive + input variants |
| `CLAUDE.md` | Existing, modified | pipeline diagram + test count |
| `.gitignore` | Existing, modified | `projections/`, `projection_inputs.json` |

`/tax-project` is **additive** — it does not modify the `/tax-setup → … → /tax-review` core pipeline.

## Components

### `projection_inputs.json`

```json
{
  "_comment": "YTD actuals + full-year forecast overrides for /tax-project. Read-only input; never holds computed results.",
  "tax_year": null,
  "as_of_date": null,
  "pay_frequency": "biweekly",
  "ytd": {
    "wages": null,
    "federal_withholding": null,
    "state_withholding": null,
    "ss_wages": null,
    "medicare_wages": null,
    "medicare_withheld": null
  },
  "forecast_overrides": {
    "_comment": "Lumpy/known items entered as FULL-YEAR amounts; NOT annualized.",
    "year_end_bonus": 0,
    "rsu_vesting": 0,
    "capital_gains": 0,
    "self_employment_net": 0,
    "other_income": 0
  },
  "recurring_from_prior": {
    "_comment": "Defaults pulled from archive/{year-1}/ by the skill; user-editable. Treated as full-year.",
    "taxable_interest": null,
    "ordinary_dividends": null,
    "qualified_dividends": null
  },
  "estimated_payments_made": { "federal": 0, "state": 0 },
  "_paystub_parser_hook": "Future: a /tax-parse-paystub feature may populate the ytd block automatically. Keep ytd as the stable interface."
}
```

**Validation** (matches `taxpayer_profile.json` policy): numeric fields coerced via `tax_computations._coerce_numeric` (rejects bool and unparseable); `pay_frequency` ∈ {weekly, biweekly, semimonthly, monthly}; `as_of_date` is `MM-DD`, after Jan 1, not in the future, a real calendar date; reject shell metacharacters in any string field.

### `agents/lib/projection.py`

Pure functions with thin I/O wrappers at the edges (same shape as `yoy_diff.py`). Public API:

```python
load_prior_baseline(archive_dir: Path, prior_year: int) -> PriorReturn | None
    # reuse rollover.load_prior; return None (not raise) if archive absent → degraded mode

annualize(ytd: dict, as_of: date, pay_frequency: str,
          overrides: dict, recurring: dict) -> ProjectedInputs
    # steady items (wages, withholding) scaled by elapsed fraction;
    # overrides added as full-year; recurring treated as full-year.

project_federal_tax(proj: ProjectedInputs, profile: dict, tax_law: dict) -> FederalProjection
    # taxable income → compute_qdcg_tax / compute_bracket_tax → CTC / SE / Add'l Medicare → total tax

project_state_tax(proj: ProjectedInputs, prior: PriorReturn | None,
                  tax_law: dict) -> StateProjection | None
    # only when prior baseline carries a state section AND tax_law has state brackets

compute_safe_harbor(projected_tax: float, prior_tax: float | None, prior_agi: float | None,
                    filing_status: str, withholding: float, estimates_paid: float) -> SafeHarbor
    # required = min(0.90*projected, (1.10 if prior_agi>threshold else 1.00)*prior_tax)
    # prior None → 90%-only (degraded)

compute_w4_4c(shortfall: float, paychecks_remaining: int) -> W4Adjustment
compute_estimated_vouchers(required_annual: float, withholding: float, paid: float,
                           as_of: date) -> list[Voucher]

render_markdown(report: ProjectionReport) -> str

# CLI
python3 -m agents.lib.projection --inputs projection_inputs.json --workspace . \
        --year 2026 [--as-of 06-30] [--json]
```

Dataclasses are `@dataclass(frozen=True)` (mirroring `yoy_diff.py`): `ProjectedInputs`, `FederalProjection`, `StateProjection`, `SafeHarbor`, `W4Adjustment`, `Voucher`, `ProjectionReport`. A `_report_to_dict` helper supports `--json`.

### `agents/skills/tax-project.md`

Per the user-facing flow above: validate prerequisites → resolve/degrade baseline → interview or load `projection_inputs.json` → confirm annualization split → call CLI `--json` → render + save report → append audit note. Untrusted-content and PII rules per `CLAUDE.md`. Re-runnable; no mutation of the filed return.

## Projection methodology (the core math)

The accuracy of the projection rests on splitting inputs into **annualized** (steady) and **full-year** (lumpy) buckets.

- **Elapsed fraction** = (ordinal day of `as_of_date`) ÷ (365 or 366 for leap years).
- **Steady items** (`ytd.wages`, `ytd.federal_withholding`, `ytd.state_withholding`, Medicare wages/withheld): `full_year = ytd / elapsed_fraction`.
- **Forecast overrides** (bonus, RSU, capital gains, SE net, other): added as full-year amounts, **not** annualized. They represent known/expected lumpy events the user enters directly.
- **Recurring-from-prior** (interest, dividends): treated as full-year defaults sourced from the prior return; user-editable.
- **Paychecks remaining** = periods-in-year − periods-elapsed, by `pay_frequency` (weekly 52, biweekly 26, semimonthly 24, monthly 12). Used for W-4 4(c).

### Federal tax projection

1. Projected wages = annualized base wages + bonus + RSU.
2. Projected ordinary income, qualified dividends, net LTCG assembled from the buckets.
3. Taxable income = projected gross − standard deduction (from `tax_law`) − QBI (if SE) − adjustments.
4. Tax via `compute_qdcg_tax` (engages preferential rates when cap gains/qualified dividends present) else `compute_bracket_tax`.
5. SE override > 0 → `compute_se_tax`; high wages → `compute_additional_medicare`.
6. CTC via `compute_child_tax_credit` from `taxpayer_profile.dependents`.
7. Projected balance = projected total tax − projected withholding − estimates paid.

### Safe harbor

`required_annual_payment = min(0.90 × projected_tax, factor × prior_tax)` where `factor = 1.10` if prior-year AGI > $150,000 ($75,000 MFS) else `1.00`. These AGI thresholds are statutorily fixed (IRC §6654(d)(1)(C), unchanged since 1993) and are hardcoded as structural constants — like `SE_TAX_THRESHOLD` — rather than sourced from the year's `tax_law` cache, which holds inflation-adjusted rates/brackets. `shortfall = required − projected_withholding − estimates_paid`. Spread the shortfall across remaining 1040-ES due dates (Apr 15 / Jun 15 / Sep 15 / Jan 15 of next year); quarters already past as of `as_of_date` fold into the next open quarter.

### W-4 line 4(c)

Two per-paycheck targets, each = its shortfall ÷ `paychecks_remaining`:
- **Avoid penalty:** shortfall to reach the safe-harbor required payment.
- **Zero balance:** shortfall to fully cover projected liability.

### State projection

Only when `prior` carries a state section (e.g. `california_540`) and `tax_law` has state brackets: project state taxable income from the federal buckets and apply state brackets via `compute_bracket_tax`. State estimated-payment scheduling is reported as informational (state rules vary); no per-state voucher math in v1. Otherwise the state section is skipped with an explicit note.

## Report layout (markdown)

```
# Mid-Year Tax Projection — TY{year} (as of {MM-DD})
## Assumptions          elapsed fraction, pay frequency, baseline source, degraded-mode flag
## Projected full-year income     annualized wages + overrides + recurring (with source tags)
## Projected federal tax & balance      taxable income → tax → withholding → refund/(owed)
## Safe harbor analysis   90% current vs 100/110% prior; required annual; covered?
## Recommended actions
   ├─ 1040-ES voucher schedule (remaining quarters, $ each, due dates)
   └─ W-4 line 4(c): $X/paycheck to avoid penalty | $Y/paycheck for zero balance
## State projection ({state})   [or: "skipped — no prior-year state baseline"]
## Caveats   projection ≠ filed return; rerun after material income changes
```

## Error handling & edge cases

| Scenario | Behavior |
|---|---|
| `as_of_date` in future / not `MM-DD` / impossible date (Feb 30) | Reject with message |
| `as_of_date` = Jan 1 (elapsed fraction 0) | Reject: "as-of must be after Jan 1 — no YTD to annualize" |
| YTD wages present, withholding null | Treat withholding as 0; flag loudly in Assumptions |
| No `archive/{year-1}/` | Degraded mode: 90%-only safe harbor; recurring → 0; banner in report |
| Prior baseline year mismatch | Reuse `load_prior`'s existing guard (refuses inconsistent TY) |
| `pay_frequency` unknown | Reject with allowed list |
| Negative / non-numeric / bool inputs | `_coerce_numeric` rejects |
| Path traversal in `--inputs` / `--workspace` | Reuse rollover's `_validate_subpath` |
| State brackets absent from `tax_law` | Skip state section with note; federal completes |
| `--year` malformed / out of range | Reuse rollover's `_validate_year_arg` |

### Atomicity / safety

`/tax-project` writes only to `projections/` (a fresh timestamped file per run) and appends one audit line to `answers.md`. It never edits `result.json` or `taxpayer_profile.json`, so there is no multi-file atomic-write concern. Report files get `chmod 600`.

## Testing — `tests/test_projection.py` (~30 tests)

**Fixtures** under `tests/fixtures/projection/`: a synthetic `archive/2025/` baseline (MFJ, with a `california_540` section) plus `projection_inputs.json` variants (with/without overrides; with/without prior baseline; mid-year vs Q3 as-of).

| Group | Tests |
|---|---|
| `annualize` | Half-year wages → 2× full-year. Overrides added full-year, NOT scaled. Recurring full-year. Leap-year fraction. Paychecks-remaining per frequency (52/26/24/12). |
| `project_federal_tax` | Wages-only matches hand-computed bracket tax. Cap-gains override → QDCG path. SE override → `compute_se_tax`. CTC applied from dependents. |
| `compute_safe_harbor` | 90%-current chosen when lower. 110% when prior AGI > 150K. 100% under threshold. MFS $75K boundary. Degraded (no prior) → 90%-only. |
| `compute_w4_4c` | Shortfall ÷ paychecks; zero shortfall → $0; both targets distinct. |
| `compute_estimated_vouchers` | Shortfall split across remaining quarters by as-of; all quarters past → folds into final. |
| `project_state_tax` | Projects when baseline + brackets present; returns None + note otherwise. |
| Edge / security | Future / malformed / Jan-1 as-of rejected; path traversal rejected; non-numeric / bool rejected; null withholding flagged. |
| Render | Degraded-mode banner present; state-skipped note present; no crash on all-zero input. |
| Integration | Full CLI subprocess: inputs + fixture archive → `--json` → assert projected tax, safe harbor, vouchers, W-4 against hand-computed expected values. |

### CI updates

- `tests/fixtures/projection/` committed (synthetic, no PII; under allowlisted `tests/fixtures/` path).
- `CLAUDE.md` test count bumped 155 → ~185 in the same PR.
- `.gitignore` gains `projections/` and `projection_inputs.json`.
- `.github/workflows/ci.yml` unchanged (already runs `pytest tests/ -v` recursively).

## Open follow-ups (post-v1)

- **Paystub PDF parser** (`/tax-parse-paystub`) populating the `ytd` block — the deferred rich input path.
- **NIIT / AMT / IRMAA** projection layers.
- **Per-state estimated-payment scheduling** (state-specific due dates and safe-harbor rules).
- **Scenario modeling (#3)** introduces versioned artifacts + `compute_scenario`; projection can later read a `baseline.json` instead of `archive/{year-1}/` if that lands.
- **Backlog #5 (withholding true-up)** overlaps with the W-4 4(c) output here and may absorb or extend it.
