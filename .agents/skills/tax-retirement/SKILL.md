---
name: tax-retirement
description: Mid-year retirement contribution dashboard — YTD vs max + marginal $ saved per remaining dollar across 401(k) / IRA / HSA / Mega Backdoor. Use when the user asks mid-year how much more they can contribute to a 401(k), IRA or HSA, or which account saves the most tax per additional dollar.
license: Apache-2.0
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
  YTD contributions and confirmed HSA first-of-month eligibility
- `tax_law_<year>.json` has `federal.retirement_401k`, `federal.ira_limits`,
  `federal.hsa_limits` populated (fetched by `/tax-setup`)

## Steps

### Engine preflight (HARD GATE — runs before every other step)

Run: `sh agents/lib/run.sh agents/lib/preflight.py`

- **Exit 0** — the engine is present and usable; continue. If the verdict says
  `OK, with N warning(s)`, show the user those lines first: the engine is fine,
  but something a later step needs is not — a missing package, or a workspace
  with no virtualenv — and it surfaces at first use instead of here.
- **Anything else — including the command failing because the file does not
  exist — STOP.** Do not parse, compute, or emit anything. Show the user the
  command's output and stop there.

The gates and computation this skill names are scripts under `agents/lib/`, a
sibling of `.agents/skills/` that does not travel with a copied skill — which
then still reads as a complete procedure while every script it names is absent.
So a failure to RUN this check IS the blocking condition, never a step to skip.

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
  (`forecast_overrides` + `recurring_from_prior` + `ytd` annualized), run
  `sh agents/lib/run.sh -m agents.lib.projection --inputs projection_inputs.json --year {year} --json`
  and read `federal.agi`, `federal.taxable_income` and `state.taxable_income`
  (ask for the state rate when `state` is null), then look up each bracket.
  Never re-derive them by hand: the engine takes half of SE tax off before AGI
  and prices every deduction it models between AGI and taxable income, from the
  figures the taxpayer declares. Where it refuses for an unanswered one, ask the
  taxpayer as `/tax-project` step 2 does and never fill one in: a 0 written in
  its place reads the bracket, and the savings, high. A file holding
  only `retirement_ytd`, its income left null, does not qualify: the
  projection reads that income as $0, which overstates every phase-out
  eligibility the dashboard reports
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
- Collect the aggregate and designated Roth employee YTD amounts from the
  sponsoring plan, plus its permission for pre-tax catch-up after satisfying
  the Roth requirement. Collect HSA first-of-month eligibility and December
  coverage (or a monthly tier schedule), and the separate spouse catch-up
  inputs where applicable. Explain the last-month testing period before
  recording its election and acknowledgement. Use the detailed schema below;
  missing required eligibility is unanswered, never a full-year default.
- Fetch and source-pin the current-year per-account state conformity row below
  before quoting state savings. The federal dashboard may continue with a
  visible unverified state note when that research cannot be completed.

### 3. Run the dashboard

```bash
sh agents/lib/run.sh -m agents.lib.retirement_dashboard \
 --profile taxpayer_profile.json \
 --inputs projection_inputs.json \
 --tax-law tax_law_{year}.json \
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
| HSA | fetched tier limits and catch-ups × eligible months; confirmed last-month election | federal + verified per-account state savings |
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
- **State conformity is per account and per year** — fetch the current DOR
  treatment in the matrix above. No state deduction is assumed from a state's
  general federal conformity or its absence from a historical exception list.
- **No employer-match-vesting math** — `employer_match` is treated as
  contributed dollars; the user should verify whether matched dollars are
  vested before counting on them.
- **No SEP/SIMPLE/Solo-401(k)** — v1 covers W-2-employee 401(k) and IRA
  only. Self-employed retirement vehicles deferred to v2.

## projection_inputs.json `retirement_ytd` block

```json
"retirement_ytd": {
  "employee_401k": 0,
  "roth_401k_ytd": null,
  "plan_allows_pretax_catchup_after_roth": false,
  "employer_match": 0,
  "ira_traditional": 0,
  "ira_roth": 0,
  "hsa_employee": 0,
  "hsa_employer": 0,
  "hsa_coverage": "none",
  "hsa_eligible_months": null,
  "hsa_december_eligible": null,
  "hsa_monthly_coverage": null,
  "hsa_last_month_rule": false,
  "hsa_testing_period_acknowledged": false,
  "hsa_spouse_eligible_months": null,
  "hsa_spouse_december_eligible": null,
  "hsa_spouse_monthly_coverage": null,
  "hsa_spouse_last_month_rule": false,
  "hsa_spouse_testing_period_acknowledged": false,
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

For HSA capacity, collect **eligible months on the first of the month** (integer
0–12) and explicit December eligibility for an unchanged tier. For tier changes,
instead supply `hsa_monthly_coverage`, twelve January–December values from
`none|self_only|family` reflecting eligibility, including Medicare/disqualifying
coverage. No full-year assumption is made. If electing the last-month rule,
confirm December eligibility and set `hsa_testing_period_acknowledged: true`
after explaining eligibility must continue through the following tax year;
failure requires income inclusion and additional tax. The engine uses the
greater of the monthly calculation and December's annual limit. A joint
spouse's separate catch-up needs independently confirmed spouse eligibility
fields and its own last-month election/acknowledgement; contribute it to the
spouse's separate HSA. No shared family base is counted twice.

`roth_401k_ytd` is included in `employee_401k`, never added to it. Obtain it from
the sponsoring plan's records. Confirm with payroll that the plan permits
pre-tax catch-up once prior designated Roth deferrals meet the catch-up
requirement, then set `plan_allows_pretax_catchup_after_roth: true`. The final
regulations generally apply from 2027 and allow early application; plan terms
may still require Roth. The dashboard prices only the legally available,
plan-confirmed pre-tax portion of remaining catch-up. Without either input,
it retains conservative Roth-only savings with a named caveat.

State savings require an explicit current-year per-account conformity entry.
The fetched `tax_law.retirement_conformity` matrix covers all states plus DC;
null/absent never means conforming. Fetch the resident state's current-year
DOR return instructions for elective 401(k) deferrals, Traditional IRA and HSA
contributions. Only set true when the federal treatment applies to this
taxpayer's account; partial, capped or unresolved state treatment stays null
with zero state savings and a visible unverified note. False means the federal
treatment is unavailable. Pin the source and year with the normal setter:

```bash
sh agents/lib/run.sh -m agents.lib.tax_law_fetcher set retirement_conformity.{STATE} '{"tax_year": {year}, "employee_401k": true, "traditional_ira": false, "hsa": null}' --source {verified_DOR_URL} --year {year}
```

The example is a shape, not a state's legal treatment: replace every value
from live current-year primary guidance, and confirm the page names the year.
If live access is unavailable, stop the fetch and ask the user to paste the
current guidance with its source URL; the federal dashboard may continue with
state savings visibly unpriced. Keep public-employee 414(h) pick-ups separate
from elective deferrals; their NY add-back does not establish generic conformity.

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
