# Form 8606 Basis Tracker — Design Spec

**Status:** Draft, awaiting user approval
**Date:** 2026-05-27
**Author:** Implementation session — TY2026 planning backlog item #7
**Roadmap source:** `docs/roadmap/2026-tax-planning-features.md` (item #7 of 12)
**Builds on:** `compute_scenario` keystone (#3, PR #15), the Roth ladder `agents/lib/roth_ladder.py` (#6, PR #16), and the `/tax-rollover` carryforward engine (#1, PR #8)

## Purpose

When a traditional IRA holds **after-tax basis** (nondeductible contributions reported on
prior-year Form 8606) alongside ordinary **pre-tax** money, the IRS does not let you
withdraw or convert the after-tax dollars selectively. Form 8606's **pro-rata rule**
treats every distribution and Roth conversion as a blended withdrawal:

```
nontaxable_portion = amount_converted × (total_basis ÷ total_IRA_value)
```

This closes two gaps:

1. **The backdoor-Roth pro-rata trap.** A taxpayer makes a $7,000 nondeductible
   contribution and converts it, assuming the conversion is tax-free — but if a $50,000
   pre-tax IRA sits alongside it, only `7000 ÷ 57000 ≈ 12.3%` of the conversion is
   return of basis; the other ~88% is taxable. People routinely get this wrong.
2. **The "full conversion is taxable" simplification** that the Roth ladder (#6)
   explicitly deferred to this item. With basis tracked, each ladder rung's taxable
   conversion is correctly diluted by the basis ratio.

The "tracker" half is that **basis remaining** (Form 8606 line 14) must persist across
years — next year's line 2 is this year's line 14. That is exactly the carryforward
pattern `/tax-rollover` already implements for capital-loss / QBI / FTC carryforwards.

## Scope

### In scope

- New `compute_form_8606(...)` keystone in `tax_computations.py` — a pure, structural
  Form 8606 Part I worksheet (no tax rates; pro-rata is arithmetic). Mirrors the
  validation discipline of the existing worksheets (`compute_qdcg_tax`, `compute_se_tax`).
- New `form_8606` block in `result_template.json` (conditional include) holding the
  computed Part I lines + `basis_remaining` + `ratio` + `pro_rata_triggered`.
- New `prior_year.ira_basis` field in `taxpayer_profile_template.json` — the line-2
  carryforward, sitting alongside the other `prior_year.*` carryforwards.
- New `/tax-rollover` FIELD_MAP entry pulling `form_8606.basis_remaining` →
  `prior_year.ira_basis` (zero basis produces no diff entry, per the existing
  "non-zero number or non-empty list" presence rule).
- `/tax-compute` wiring — a Form 8606 computation step that gathers the inputs, calls the
  keystone, populates the `form_8606` block, and routes the taxable portion to 1040 line 4b.
- `/tax-roth-convert` wiring — optional `--ira-basis` (default from
  `taxpayer_profile.json prior_year.ira_basis`) and `--ira-value` (total beginning-of-year
  traditional IRA balance `B`) arguments. Each rung calls `compute_form_8606` to obtain the
  **taxable** conversion and feeds only that into `compute_scenario`.
- Test suite: keystone cases in `tests/test_tax_computations.py`, carryforward case in
  `tests/test_rollover.py`, pro-rata cases in `tests/test_roth_ladder.py`.

### Out of scope (deferred)

- **State tax** — federal only, consistent with #3/#6.
- **SEP/SIMPLE separate tracking** — all traditional/SEP/SIMPLE IRAs are aggregated into
  one `year_end_value`, as Form 8606 itself requires; no per-account modeling.
- **Roth IRA basis / ordering rules (Form 8606 Part III)** — only Part I (traditional IRA
  basis and conversions) is modeled. Roth distribution ordering is a separate feature.
- **Spouse's separate Form 8606** — Form 8606 is per-individual. v1 tracks a single
  `ira_basis` figure; a married couple each doing backdoor Roths needs two runs / a
  future per-spouse split. Flagged in caveats.
- **Inherited IRA basis** — not modeled.

## Key design decision — the constant taxable fraction

Form 8606 line 9 (the pro-rata denominator) = line 6 (year-end FMV of all traditional
IRAs) + line 7 (distributions) + line 8 (conversions). When a taxpayer converts `C` out
of a beginning-of-year total balance `B` (with no other distributions), the year-end FMV
is `B − C`, so:

```
line 9 = (B − C) + 0 + C = B          (the BEGINNING balance, independent of C)
ratio  = basis ÷ B                    (constant — does not depend on how much is converted)
taxable_conversion(C) = C × (1 − basis ÷ B)
```

This is why the Roth ladder can pro-rate **every rung with one fixed ratio**: the taxable
fraction `1 − basis/B` is constant across the sweep. The ladder obtains each rung's
taxable conversion by calling `compute_form_8606(year_end_value = B − C, amount_converted = C)`
and reading line 18 — reusing the keystone as the single source of pro-rata truth rather
than duplicating the arithmetic.

## The keystone: `compute_form_8606`

```python
def compute_form_8606(
    nondeductible_contrib_current: float,   # line 1
    prior_basis: float,                      # line 2 (carryforward from last year's line 14)
    year_end_value: float,                   # line 6 (FMV of ALL trad/SEP/SIMPLE IRAs Dec 31)
    distributions_not_converted: float,      # line 7
    amount_converted: float,                 # line 8
    contrib_made_by_deadline: float = 0.0,   # line 4 (next-Jan–Apr contributions for this year; usually 0)
) -> dict
```

Computes:

| Line | Formula |
|------|---------|
| 3 | line 1 + line 2 |
| 5 | line 3 − line 4 |
| 9 | line 6 + line 7 + line 8 |
| 10 | `line 5 ÷ line 9`, capped at 1.0; **0.0 when line 9 == 0** (nothing distributed, no balance) |
| 11 | line 8 × line 10 (nontaxable portion of conversion) |
| 12 | line 7 × line 10 (nontaxable portion of distributions) |
| 13 | line 11 + line 12 |
| 14 | line 3 − line 13 (**basis remaining → next-year carryforward**) |
| 15 | line 7 − line 12 (taxable distributions) |
| 18 | line 8 − line 11 (taxable conversion) |

Returns a dict of all lines plus:
- `basis_remaining` = line 14
- `taxable_conversion` = line 18
- `taxable_distributions` = line 15
- `taxable_to_1040` = line 15 + line 18 (the amount flowing to 1040 line 4b)
- `ratio` = line 10
- `pro_rata_triggered` = `amount_converted > 0 and line_3 (total basis) > 0 and
  year_end_value > 0` — i.e. after-tax basis AND pre-tax money both coexist at a conversion,
  so the conversion is only partly tax-free (the trap). An all-pre-tax conversion (no basis)
  is fully taxable but not a *surprise*, so it does not trip the flag.

**Precision decision.** The IRS form instructs rounding line 10 "to at least 3 decimal
places." Rounding the ratio to exactly 3 places injects error into the dollar lines; tax
software (which `/tax-verify` cross-references against) carries full internal precision.
So the keystone keeps the **ratio at full float precision** for the dollar computations and
rounds only the dollar outputs to cents. The reported `ratio` field is rounded to 5
decimals for display. This is documented in a code comment and in the caveats.

**Validation.** All numeric inputs run through `_coerce_numeric` (rejecting `bool` and
non-numeric strings, consistent with the other keystones). Negative inputs raise
`ValueError` (basis, balances, and distributions are never negative). `amount_converted`
greater than what is available is *not* rejected here — the keystone is pure arithmetic;
the **caller** (Roth ladder) caps a conversion at the available balance and flags it.

### Edge cases

| Case | Inputs | Result |
|------|--------|--------|
| Clean backdoor (no pre-tax) | basis 7000, year_end 0, converted 7000 | ratio 1.0 → taxable 0 |
| Pro-rata trap | basis 7000, year_end 50000, converted 7000 | ratio ≈0.1228 → taxable ≈$6,140 |
| All pre-tax (no basis) | basis 0, year_end 50000, converted 10000 | ratio 0 → taxable 10000 |
| Nothing happened | basis 5000, year_end 0, converted 0, dist 0 | line 9 = 0 → ratio 0, line 14 = line 3 (basis carries unchanged) |
| Ratio cap | basis 100, year_end 0, converted 50 | ratio capped at 1.0 → taxable 0, line 14 = 50 leftover basis |

## Persistence & rollover

- **Current-year computed state** lives in `result.json → form_8606` (created by
  `/tax-compute`).
- **Carryforward** lives in `taxpayer_profile.json → prior_year.ira_basis`.
- `/tax-rollover` FIELD_MAP entry:

  ```python
  "ira_basis": {
      "category": "carryforwards",
      "source_path": "form_8606.basis_remaining",
      "target_file": "taxpayer_profile.json",
      "target_path": "prior_year.ira_basis",
      "transform": "ira_basis",   # returns the value when a positive number, else 0
  }
  ```

  A new `_transform_ira_basis` returns the figure when it is a positive number and 0
  otherwise, so a zero/absent basis creates no diff noise (consistent with the existing
  carryforward presence rule). A differing current value surfaces as `ask`, like the
  other carryforwards.

## Roth ladder integration

New CLI args on `agents.lib.roth_ladder`:

- `--ira-basis FLOAT` — total after-tax basis. Defaults to
  `taxpayer_profile.json prior_year.ira_basis` when omitted.
- `--ira-value FLOAT` — total beginning-of-year traditional/SEP/SIMPLE IRA balance `B`
  (pre-tax + basis). Required to pro-rate; no default.

Behavior:

- **Both present and `ira_value > 0`:** for each rung amount `C`, call
  `compute_form_8606(prior_basis = basis, year_end_value = max(0, B − min(C, B)),
  amount_converted = min(C, B))` and feed `line 18` (taxable conversion) into
  `compute_scenario` as `ira_distribution`, instead of the full `C`. A rung where
  `C > B` is **capped at `B`** and flagged ("conversion exceeds IRA balance").
- **Absent / `ira_value` unset / basis 0:** degrade to the current v1 behavior (full
  conversion taxable). This keeps every existing ladder run and test unchanged — the
  feature is purely additive and backward compatible.
- The `$0` rung still equals `result.json` line 24 exactly (taxable conversion at `C=0`
  is 0 regardless of basis), preserving the anchoring invariant.

New report fields (added additively — schema stays `tax-roth-ladder/v1` since no consumer
branches on the version and JSON readers ignore unknown keys):
`basis_modeled: bool`, `ira_basis: float | None`, `ira_value: float | None`, and per-rung
`taxable_conversion: float` (alongside the existing gross `amount`). The render adds a
"Basis (Form 8606 pro-rata)" note and a per-rung column showing taxable vs. gross
conversion when basis is modeled. The "full conversion treated as taxable / basis not
applied (backlog #7)" caveat is replaced with the basis-modeled description.

**Marginal-rate semantics.** Marginal rate stays Δtax ÷ Δ**gross** conversion (the lever
the user pulls). With basis modeled it naturally reads lower, because part of each
converted dollar is return of basis — which is the correct planning signal.

## Write behavior

- `compute_form_8606` is pure (no I/O).
- `/tax-compute` writes `result.json` (its existing source-of-truth responsibility).
- `/tax-rollover` writes `taxpayer_profile.json` via its existing atomic-write path.
- `/tax-roth-convert` remains read-only w.r.t. baseline files and writes only
  `scenarios/<name>.json` via the reused `save_scenario` guard — unchanged.

## Testing

- **Keystone** (`tests/test_tax_computations.py`): clean backdoor → $0 taxable; pro-rata
  trap → hand-computed taxable amount; all-pre-tax → full taxable; nothing-happened →
  basis carries; ratio cap at 1.0; zero denominator; negative input rejection; bool
  rejection; `pro_rata_triggered` flag. Add a `compute_form_8606` block to the module
  self-test.
- **Rollover** (`tests/test_rollover.py`): `basis_remaining` → `prior_year.ira_basis`
  carryforward; zero basis produces no diff entry; differing current value → `ask`.
- **Roth ladder** (`tests/test_roth_ladder.py`): degrades to full-taxable when basis/value
  absent (existing behavior preserved); taxable conversion diluted by the ratio when basis
  present; `$0` rung still equals line 24; `C > B` capped + flagged; `--ira-basis` defaults
  from profile.

## Caveats (documented in skill output)

- Federal only.
- Pro-rata aggregates all traditional/SEP/SIMPLE IRAs into one balance (per IRS rules);
  Roth IRA basis (Part III) and inherited IRAs are not modeled.
- Form 8606 is per-individual; v1 tracks a single basis figure — a couple each running
  backdoor Roths needs separate tracking.
- Ratio uses full internal precision (the IRS 3-decimal-place minimum would inject
  rounding error); matches tax-software behavior for `/tax-verify`.
- A planning estimate, not a filed return.
