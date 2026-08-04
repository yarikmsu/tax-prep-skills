---
name: tax-retirement
description: Mid-year retirement contribution dashboard — YTD vs max + marginal $ saved per remaining dollar across 401(k) / IRA / HSA / Mega Backdoor. Use when the user asks mid-year how much more they can contribute to a 401(k), IRA or HSA, or which account saves the most tax per additional dollar.
---

# /tax-retirement

Mid-year planning aid. Reads YTD contribution figures from
`projection_inputs.json` + age flags from `taxpayer_profile.json` + statutory
limits from `tax_law_<year>.json`, and reports per-account remaining capacity
plus the marginal federal/state tax savings per remaining dollar.

Read-only: never mutates `result.json` or `taxpayer_profile.json`. Re-runnable
quarterly as YTD figures evolve.

## Usage

```
/tax-retirement
/tax-retirement {year}
```

Resolve a default year (the **CURRENT calendar year**) yourself before
invoking the CLI. The CLI itself requires `--year` explicitly.

This is the EXCEPTION to the rest of the toolkit's default of "current
calendar year − 1": `/tax-setup`, `/tax-compute`, `/tax-verify` etc.
default to the prior year because they prepare a return for a completed
year. `/tax-retirement` is forward-looking mid-year planning — the user
wants limits and phase-outs for the CURRENT tax year, not the one they're
filing a return for. Same logic applies to `/tax-project` and
`/tax-withholding`.

## Prerequisites

- `taxpayer_profile.json` exists with `filing_status`, `address.state`, and
  the age-flag set (`age_50_plus_primary`, `age_55_plus_primary`,
  `age_60_63_primary` — these are pure age windows, NOT cohort-dependent
  unlike RMD; the existing flags work fine)
- **For MFS filers ONLY:** `flags.lived_apart_from_spouse_all_year` MUST
  be explicitly set to `true` or `false`. This flag is load-bearing for
  IRA computations — `true` triggers Single-equivalent treatment per Pub
  590-A footnote, `false` triggers the MFS-with-spouse $0-$10K phase-out
  window. The dashboard HARD-ERRORS if MFS + flag missing/null; cannot
  guess. (For Single / MFJ / HoH / QSS the flag is unused and may be
  absent.)
- `projection_inputs.json` has a `retirement_ytd` block (see template) with
  YTD contributions and HSA coverage type
- `tax_law_<year>.json` has `federal.retirement_401k`, `federal.ira_limits`,
  `federal.hsa_limits` populated (fetched by `/tax-setup`)

## Steps

### 1. Resolve the marginal rates and MAGI

The dashboard needs three numbers the lib does NOT derive automatically in
v1:

- **Federal marginal rate** (decimal, e.g. `0.24` for the 24% bracket)
- **State marginal rate** (decimal, e.g. `0.093` for CA)
- **Modified AGI** (for IRA Traditional deduction + Roth contribution
  phase-out lookups)

Resolution order (pick the cheapest reliable source):

- If `result.json` is fully computed for the same `tax_year`, read its
  taxable income / AGI and look up brackets in
  `tax_law_<year>.json.federal.filing_statuses.<status>.brackets`
- Else, if `projection_inputs.json` has full-year forecast figures
  (`forecast_overrides` + `recurring_from_prior` + `ytd` annualized),
  compute the projected taxable income + bracket
- Else, ask the user for the three values, one topic at a time

In a non-interactive run, read the answers from `answers.md` and hard-stop on
any missing REQUIRED answer — never guess. A guessed marginal rate or MAGI
silently produces wrong remaining-capacity and phase-out figures.

### 2. Verify the K-1 / employer-plan flags

Confirm with the user (or read from profile):

- Is the primary covered by an employer retirement plan? (drives
  `flags.has_employer_retirement_plan` — affects Traditional IRA
  deductibility phase-out)
- For MFJ: is the **spouse** covered by an employer retirement plan?
  (drives `flags.has_employer_retirement_plan_spouse` — when the
  primary is NOT covered but the spouse IS, IRS Pub 590-A Table 1-3
  applies a SEPARATE wider phase-out window —
  the `mfj_spouse_not_covered` window, which is wider than the regular
  `mfj_covered` window — both are indexed annually and read from
  `tax_law_<year>.json`, not hardcoded here)
- Does the 401(k) plan permit after-tax contributions + in-service
  rollover? (drives `retirement_ytd.plan_allows_after_tax_401k` — gates
  Mega Backdoor emission)

### 3. Run the dashboard

```bash
python3 -m agents.lib.retirement_dashboard \
  --profile taxpayer_profile.json \
  --inputs projection_inputs.json \
  --tax-law tax_law_{year}.json \
  --workspace . \
  --year {year} \
  --marginal-fed {fed_decimal} \
  --marginal-state {state_decimal} \
  --magi {magi}
```

Add `--json` for machine-readable output.

### 4. Surface the report + recommendation

Print the rendered markdown. Then summarize for the user:

- Largest remaining capacity bucket (where the most $ saved per dollar lives)
- Any account where YTD has gone OVER the limit (excess contribution — needs
  corrective distribution before the return is filed)
- Phase-out flags (Traditional IRA partial-deductibility, Roth phase-out,
  HSA non-conforming state)

## Account types modeled

| Account | Limit source | Marginal saved kind |
|---|---|---|
| 401(k) employee deferral | `retirement_401k.elective_deferral` + catch-ups | pre-tax (fed + state) |
| Traditional IRA | `ira_limits.contribution_limit` + 50+ catch-up; deductibility per MAGI + employer-plan-coverage | pre-tax × deductible-fraction |
| Roth IRA | shared IRA limit × Roth-MAGI fraction | $0 current-year |
| HSA | `hsa_limits.self_only` or `family` + 55+ catch-up | pre-tax (fed + state, except CA / NJ) |
| Mega Backdoor | `total_annual_limit` − non-catchup portion of employee deferral − employer match (per IRC §415(c) / §414(v)) | $0 current-year (tax-free growth not modeled) |

## Caveats (v1)

- **Marginal rates are user-supplied** in v1 — the lib doesn't auto-derive
  brackets from `result.json` or annualized projection inputs. v2 could add
  automatic bracket lookup.
- **Single-bracket assumption** — the marginal-saved math assumes the next
  dollar stays inside the current bracket. Large contributions may straddle.
- **MFS filers must specify `lived_apart_from_spouse_all_year`** — see
  Prerequisites. The dashboard DOES differentiate MFS-with-spouse from
  MFS-lived-apart-all-year per Pub 590-A footnote: with-spouse uses the
  $0-$10K phase-out window for both Traditional IRA deduction (when
  covered) and Roth IRA contribution; lived-apart uses Single's wider
  windows. The flag is load-bearing — hard-error if missing for MFS.
- **HSA non-conforming states are CA / NJ only** — Alabama was historically non-conforming but Ala. Code §40-18-15.6 (effective 2018-01-01) allows the deduction; other states (e.g.
  TN, WI) have had non-conformity in the past; if the user lives in a
  questionable state, cross-check with state DOR.
- **No employer-match-vesting math** — `employer_match` is treated as
  contributed dollars; the user should verify whether matched dollars are
  vested before counting on them.
- **No SEP/SIMPLE/Solo-401(k)** — v1 covers W-2-employee 401(k) and IRA
  only. Self-employed retirement vehicles deferred to v2.

## projection_inputs.json `retirement_ytd` block

```json
"retirement_ytd": {
  "employee_401k": 0,
  "employer_match": 0,
  "ira_traditional": 0,
  "ira_roth": 0,
  "hsa_employee": 0,
  "hsa_employer": 0,
  "hsa_coverage": "none",
  "after_tax_401k": 0,
  "plan_allows_after_tax_401k": false,
  "prior_year_fica_wages": null,
  "taxable_compensation": null,
  "spouse_taxable_compensation": null
}
```

`hsa_coverage` is one of `none` / `self_only` / `family`. All numeric fields
are strictly validated (truthy strings, negatives, and `bool` for numeric
fields all hard-error).

`prior_year_fica_wages` (TY2026+) drives **SECURE 2.0 §603 mandatory Roth
catch-up detection**. Per IRC §414(v)(7)(A), participants whose prior-year
FICA wages exceeded the high-earner threshold (an annually-indexed amount
read from `tax_law_<year>.json`; the §603 statutory base is $145k indexed
from 2023)
MUST make 401(k) catch-up contributions as designated Roth — no
current-year federal/state deduction. **Supply the FICA wages your PLAN
uses for its Roth-catch-up determination** — generally the wages from the
employer SPONSORING this 401(k) plan, though the final §414(v)(7) regs let
a plan aggregate wages across related common-law employers in the same
controlled group (§414(b)/(c)/(m)/(o)). So a taxpayer with $200k total but
only $100k from this (unrelated) sponsor is not a high earner for this
plan, whereas a controlled-group participant may have wages aggregated —
**confirm with the plan administrator** rather than assuming either your
single-employer figure or your total W-2 wages. v1 takes a single
plan-determined figure; the catch-up and warning notes restate this caveat.
When applicable, the dashboard splits the 401(k) row into:

- `401(k) employee (base)` — pre-tax, base elective deferral limit
- `401(k) catch-up (Roth required)` — Roth, $0 current-year deduction,
  per-age-eligibility (the standard 50+ catch-up `catch_up_50_plus`, or the
  SECURE 2.0 super catch-up `catch_up_60_63` at age 60-63 — both read from
  `tax_law_<year>.json`, never hardcoded here; the 60-63 super supersedes,
  not stacks)

When the field is **missing or null** AND the taxpayer is catch-up eligible,
the dashboard emits a warning note pointing the user to provide the field —
rather than guess. When the field is provided and BELOW the threshold,
behavior is unchanged (single pre-tax row with combined limit).

Effective TY2026 per **IRS Notice 2023-62** (delayed from the original
2024 statutory effective date).

`taxable_compensation` and `spouse_taxable_compensation` (optional) cap
the **IRA pool** per **IRC §219(b)(1)(A)** (Traditional) and §408A(c)(2)
(Roth). The legal IRA contribution cap is the LESSER of the statutory
dollar limit (the base IRA limit `contribution_limit`, plus the 50+
catch-up `catch_up_50_plus` if age-eligible, both from
`tax_law_<year>.json` — never hardcoded here) and the
taxpayer's taxable compensation for the year. A retiree with $0 wages
+ only dividends/cap gains has **$0** IRA capacity regardless of
statutory limit.

`spouse_taxable_compensation` enables the **§219(c) MFJ spousal IRA
exception**: a non-working spouse can use the working spouse's
compensation when filing jointly. Combined household compensation is
used for the cap. Does NOT extend to MFS / Single / HoH / QSS.

When `taxable_compensation` is **missing or null** AND the IRA pool
has nonzero remaining capacity, the dashboard emits a warning so the
user knows capacity may be overstated. A retiree following the
dashboard's wrong number could trigger the **IRS 6%-per-year
contribution-limit excise tax** on excess contributions.
