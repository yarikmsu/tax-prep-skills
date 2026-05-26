# Withholding True-Up (`/tax-withholding`) — Design

**Status:** Approved 2026-05-25
**Backlog item:** #5 (Withholding true-up, effort S)
**Author:** brainstorming session 2026-05-25

## Problem

After a current-year return has been computed (`result.json` exists), the taxpayer
wants to know whether their year-to-date federal withholding plus estimated payments
will satisfy the IRS safe harbor (avoid a Form 2210 underpayment penalty) and/or fully
cover the liability, and — if not — exactly how to close the gap before year-end via a
W-4 line 4(c) per-paycheck adjustment or a 1040-ES voucher schedule.

## Why this is distinct from `/tax-project`

`/tax-project` already emits a safe-harbor analysis, a W-4 4(c) amount, and a 1040-ES
schedule — but it derives the full-year liability by **annualizing YTD income** (an
estimate). `/tax-withholding` instead reads the **actual computed liability** from
`result.json` (`form_1040.line_24_total_tax`). The target is a known number, not a guess.
This is the one and only structural difference; all downstream math (safe harbor,
4(c), vouchers) is shared with `projection.py` by import.

This follows the read-only planning-module pattern (`projection.py`, `yoy_diff.py`):
pure-compute lib (frozen dataclasses + markdown renderer + CLI `_main`) that reuses
`rollover.load_prior` for the prior-year baseline and **never mutates** `result.json` or
`taxpayer_profile.json`.

## Scope

**In scope (v1):** federal withholding true-up only.

**Out of scope (deferred):** state withholding true-up. Rationale: the roadmap line for
#5 specifies "federal withholding"; state estimated-payment rules vary by state, and
`/tax-project` already shows a conditional state projection. A `/tax-withholding` state
mode is a clean v1.1 follow-up.

## Inputs

| Source | Fields used | Purpose |
|---|---|---|
| `result.json` | `meta.tax_year`; `form_1040.line_24_total_tax` | Year guard + liability **target** `L` |
| `projection_inputs.json` | `tax_year`, `as_of_date`, `pay_frequency`, `ytd.federal_withholding`, `estimated_payments_made.federal` | Withheld-to-date, estimates paid, pace |
| `archive/{year-1}/` (optional) | prior `form_1040.line_24_total_tax`, `line_11_agi` via `load_prior` | Prior-year safe harbor; absent → degraded mode |
| `tax_law_{year}.json` | (read for parity / future use; not strictly required for the gap math) | — |

**Year guards:** `result.json` `meta.tax_year` must equal `--year` (mirrors yoy_diff
line 374–377). `projection_inputs.json` `tax_year`, when present and non-null, must also
equal `--year`; mismatch is an error.

## Compute logic

Let:
- `L` = `result.json` `form_1040.line_24_total_tax` (must exist and be ≥ 0; a genuine $0 liability is valid — the `meta.tax_year` guard already rejects blank/uncomputed templates whose `tax_year` is null).
- `frac` = `elapsed_fraction(as_of)` (day-of-year / days-in-year), reused from `projection.py`.
- `ytd_wh` = `projection_inputs.ytd.federal_withholding`.
- `estimates_paid` = `projection_inputs.estimated_payments_made.federal`.
- `projected_full_year_withholding` = `round(ytd_wh / frac, 2)` — withholding continued at the current pace, no change.
- `projected_paid` = `projected_full_year_withholding + estimates_paid`.
- `paychecks_remaining` = `paychecks_remaining(pay_frequency, frac)`, reused from `projection.py`.

**Two targets:**
1. **Penalty-avoidance (safe harbor).** `safe_harbor = compute_safe_harbor(projected_tax=L, prior_tax, prior_agi, filing_status, withholding=projected_full_year_withholding, estimates_paid=estimates_paid)`.
   - `required = min(0.90·L, prior_factor·prior_tax)` where `prior_factor` is 1.10 if prior AGI > $150k ($75k MFS) else 1.00. Degraded (no archive) → `required = 0.90·L`.
   - `penalty_shortfall = safe_harbor.shortfall`.
2. **Zero-balance (full liability).** `zero_target = L`; `zero_shortfall = max(0, L − projected_paid)`.

**Recommendations** (lead with 4(c), since withholding is deemed paid evenly across the
year and thus cures an underpayment retroactively):
- `w4_penalty = compute_w4_4c(penalty_shortfall, paychecks_remaining)`
- `w4_zero    = compute_w4_4c(zero_shortfall,    paychecks_remaining)`
- `vouchers   = compute_estimated_vouchers(required_annual=safe_harbor.required_annual, withholding=projected_full_year_withholding, paid=estimates_paid, as_of=as_of)` — fallback, and the only actionable path when `paychecks_remaining == 0`.

All three (`compute_safe_harbor`, `compute_w4_4c`, `compute_estimated_vouchers`) are
imported unchanged from `projection.py`.

## Components — `agents/lib/withholding.py`

**Imported from `projection.py`:** `compute_safe_harbor`, `compute_w4_4c`,
`compute_estimated_vouchers`, `parse_as_of`, `elapsed_fraction`, `paychecks_remaining`,
`validate_pay_frequency`, `load_prior_baseline`, `_num`, `_money`, and the
`SafeHarbor` / `Voucher` / `W4Adjustment` dataclasses.
**Imported from `rollover.py`:** `RolloverError`, `_validate_subpath`, `_validate_year_arg`.

**New:**
- `WithholdingError(Exception)`.
- `@dataclass(frozen=True) WithholdingInputs`: `ytd_withholding`, `estimates_paid`, `elapsed_fraction`, `paychecks_remaining`, `projected_full_year_withholding`, `projected_paid`, `pay_frequency`.
- `@dataclass(frozen=True) TrueUpTarget`: `name` (`"safe_harbor"` / `"zero_balance"`), `target_amount`, `projected_paid`, `shortfall`, `covered`, `per_paycheck`.
- `@dataclass(frozen=True) TrueUpReport`: `year`, `as_of`, `pay_frequency`, `liability`, `baseline_source`, `degraded`, `inputs: WithholdingInputs`, `safe_harbor: SafeHarbor`, `penalty_target: TrueUpTarget`, `zero_target: TrueUpTarget`, `vouchers: list[Voucher]`, `w4_penalty: W4Adjustment`, `w4_zero: W4Adjustment`.
- `read_liability(result: dict, year: int) -> float`: validate `meta.tax_year == year`; return `line_24_total_tax`; raise `WithholdingError` if missing/negative/wrong year (a $0 value is allowed).
- `build_inputs(proj_inputs: dict, as_of, pay_frequency) -> WithholdingInputs`.
- `build_report(result, proj_inputs, prior, year, as_of) -> TrueUpReport`.
- `render_markdown(r: TrueUpReport) -> str`.
- `_report_to_dict(r) -> dict`.
- `_main(argv) -> int`.

**CLI:**
```
python3 -m agents.lib.withholding --result result.json --inputs projection_inputs.json \
    --workspace . --year {year} [--as-of MM-DD] [--today YYYY-MM-DD] [--json]
```
- `--workspace` trusted-by-contract; `--result` and `--inputs` constrained under it via `_validate_subpath`.
- `--as-of` defaults to `projection_inputs.as_of_date`, else today. `--today` overrides today (testing only). `as_of` in the future is an error.

## Markdown report structure

1. `# Withholding True-Up — TY{year} (as of {as_of})`
2. **Assumptions** — liability source = `result.json` (actual computed), elapsed fraction, pay frequency + paychecks remaining, prior-year baseline path or `DEGRADED MODE`, a `$0 withholding` warning if `ytd_withholding == 0`.
3. **Liability & payments** table — total tax (target `L`), YTD withholding, projected full-year withholding at current pace, estimates paid, projected total paid.
4. **Gap to targets** table — two rows: safe-harbor required vs projected paid → shortfall + covered/underpaid; zero-balance (full `L`) vs projected paid → shortfall.
5. **Recommended actions** — W-4 4(c) per-paycheck for both targets (lead), then the 1040-ES voucher schedule as fallback. One sentence on the withholding-deemed-even advantage.
6. **Caveats** — projection not a filed return; federal only (state deferred); re-run after material change.

## Error handling — `WithholdingError`

Raised for: missing/malformed `result.json`; missing/negative `line_24_total_tax`;
`result.json` year ≠ `--year`; `projection_inputs.json` year ≠ `--year`; missing
`projection_inputs.json`; invalid `pay_frequency`; invalid or future `as_of`;
non-numeric/non-finite input. CLI catches `WithholdingError`/`RolloverError`/`ValueError`,
prints `ERROR: {msg}` to stderr, returns 1.

## Skill — `agents/skills/tax-withholding.md`

**Prerequisites:** `result.json`, `projection_inputs.json`, `tax_law_{year}.json` (else
direct user to `/tax-compute` and/or `/tax-project` first).

**Steps:**
1. Validate prerequisites.
2. Resolve prior-year baseline from `archive/{year-1}/` (note degraded mode if absent).
3. Run the CLI with `--json` for structured data, then without `--json` for the report.
4. Save the report to `withholding/TY{year}_asof_{MM-DD}.md` and `chmod 600`.
5. Append a no-PII audit line to `answers.md`:
   `## Withholding true-up TY{year} as-of {MM-DD} — run {today}. Safe-harbor: {covered/underpaid}.`

**Security & safety:** read-only (never writes `result.json` / `taxpayer_profile.json`);
no full SSNs; any subagents have no network access and write only under `withholding/`;
written files `chmod 600`.

## Testing — `tests/test_withholding.py`

~16–18 tests (hand-computed expected values):
- `read_liability`: happy path; missing line; ≤0; wrong year.
- `build_inputs`: pace projection (`ytd/frac`), paychecks remaining, estimates passthrough.
- `build_report`: covered (both targets), underpaid (both targets), degraded mode (no archive → 90%-only), prior-factor 1.10 vs 1.00 boundary.
- 4(c) per-paycheck math; voucher fallback when `paychecks_remaining == 0`.
- CLI integration via `_main` with `--today` override (avoids future-date flakiness): JSON shape + markdown smoke; result missing / wrong-year / future-as_of error paths return 1.
- Fixtures under `tests/fixtures/withholding/` (filenames colliding with runtime names → add with `git add -f`).

Bump the CLAUDE.md test-count reference (199 → new total) on the documented line.

## Integration touchpoints

- `agents/install.sh` — `/tax-withholding` symlink picked up automatically if it globs skills; verify and add if the list is explicit.
- `CLAUDE.md` — add `/tax-withholding` to the architecture/flow notes and the read-only-planning-skills note; bump test count.
- `docs/roadmap/2026-tax-planning-features.md` — mark #5 done (optional, can be a follow-up).

## Open questions

None. Federal-only v1 and current-pace withholding projection (`ytd/frac`) confirmed
during brainstorming.
