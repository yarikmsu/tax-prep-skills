# Tax Planning Features — TY2026 Roadmap

Source: multi-perspective review (tax agent, CPA, IRS officer, product owner) conducted 2026-05-24.

This backlog moves the toolkit from a **post-mortem return preparer** (TY{N} reconciliation after Dec 31) toward a **proactive planning tool** (optimize TY{N+1} while there's still time to act).

## Architectural prerequisites

These unlock most of the features below and should land before or alongside the first planning skill:

1. **Promote `result.json` to versioned artifacts** — `result/baseline.json` + `scenarios/<name>.json` + `priors/{year}.json` history.
2. **Add `projection_inputs.json`** — sibling to `taxpayer_profile.json` holding YTD/forecast data (paystubs, expected bonuses, expected vesting).
3. **Add `compute_scenario(baseline, delta)` wrapper** in `tax_computations.py` — the keystone all scenario-modeling skills share.
4. **Optional: `/tax-watchlist` artifact** — Markdown file listing time-sensitive actions ranked by $ impact and deadline.

## Feature backlog (ordered by leverage)

| # | Feature | Skill name | Why it matters | Effort | Status |
|---|---------|-----------|----------------|--------|--------|
| 1 | **Roll-forward last year's return** | `/tax-rollover <prior_year>` | Reads prior `result.json`, copies carryforwards into new `taxpayer_profile.json` and `result.json`. Eliminates the #1 source of "I forgot I had a $3K cap loss carryover" errors. Foundation for every multi-year feature below. | S | ✅ Done (PR #8) |
| 2 | **Mid-year projection** | `/tax-project [--as-of MM-DD]` | Takes YTD paystubs + last year's pattern, projects full-year tax, computes safe-harbor estimates, suggests W-4 step 4(c) adjustment. Rerunnable quarterly. | M | ✅ Done (PR #11) |
| 3 | **Scenario modeling** | `/tax-scenario <name>` | Forks `result.json` → `scenarios/<name>.json`, applies one delta (e.g. `+roth_conversion=40000`), recomputes, diffs against baseline. Stackable. | M | ✅ Done (PR #15) |
| 4 | **Tax-loss harvesting scanner** | `/tax-harvest` | Reads brokerage lot data, ranks losing positions by harvestable loss, checks 30-day wash-sale window against your own buys, outputs sell list with $ federal+state savings. | M | ✅ Done (PR #18) |
| 5 | **Withholding true-up** | `/tax-withholding` | Compares YTD federal withholding vs projected liability; outputs revised W-4 step 4(c) amount or 1040-ES voucher schedule. | S | ✅ Done (PR #13) |
| 6 | **Roth conversion ladder modeler** | `/tax-roth-convert` | Sweeps conversion amounts $0–$100K in $5K steps; charts marginal cost per $; flags IRMAA, NIIT, LTCG bracket-bumping thresholds. | M | ✅ Done (PR #16) |
| 7 | **Form 8606 basis tracker** | (template + compute path) | Persists non-deductible IRA basis across years; auto-applies pro-rata formula on conversions. Closes the backdoor-Roth pro-rata trap. | S | ✅ Done (PR #17) |
| 8 | **Crypto importer** | `/tax-parse-crypto` | Parses Coinbase/Kraken/Gemini CSVs into Form 8949 rows; flags any digital-asset activity for the 1040 page-1 question. | M | Planned |
| 9 | **PTET election analyzer** (CA/NY/NJ) | section in `/tax-review` + new check in `/tax-project` | Detects K-1 from passthrough; estimates SALT-cap workaround savings; reminds of state election deadlines (CA is March 15 prior-year). | S | Planned |
| 10 | **Quarterly reminder calendar** | output of `/tax-setup` + standalone `/tax-calendar` | Generates `.ics` file with 1040-ES due dates, FBAR (Apr 15 → auto-extended Oct 15), state PTET windows (CA/NY/NJ), RMD deadlines for age 73+. | XS | ✅ Done |
| 11 | **Retirement contribution dashboard** | `/tax-retirement` | One screen: 401(k) / IRA / HSA / Mega Backdoor — YTD vs max, with marginal-rate $ saved per remaining dollar. Action-oriented. | S | ✅ Done |
| 12 | **What-changed report YoY** | output of `/tax-review` | Diffs current return against prior `result.json`: AGI Δ, bracket Δ, effective rate Δ, line-item movers > $1K. The single artifact a client most wants to see. | S | ✅ Done (PR #10) |

## Open product questions

- **Scope.** Items 2, 3, 5, 6 collectively become a "tax planning suite" — competing with HRB's "Tax Pro Review" and apps like Origin. Decision needed: stay narrowly focused on preparation excellence, or expand?
- **YTD data ingestion fidelity.** Mid-year features need YTD paystubs / brokerage screenshots. Cheap path: "paste your YTD wage and withholding numbers." Expensive path: paystub PDF parsers. Recommend cheap path first.

## Build order

Brainstorming started with **#1 `/tax-rollover`** — the foundation that unlocks #2, #3, #6, #7, and #12.

**Progress (2026-05-26):** #1, #12, #2, #5, #3, and #6 are shipped. #3 introduced the
`compute_scenario(baseline, delta)` keystone in `tax_computations.py` (architectural prereq #3)
plus `agents/lib/scenario.py`, which writes derived `scenarios/<name>.json` siblings without
ever mutating `result.json`. #6 (`/tax-roth-convert`, `agents/lib/roth_ladder.py`) sweeps that
keystone across conversion amounts, added a `compute_niit` helper + an `irmaa` tax_law block,
and layers NIIT/IRMAA/LTCG threshold flags onto the anchored ladder.

**Progress (2026-05-27):** #7 (Form 8606 basis tracker) is shipped. Added the
`compute_form_8606` pro-rata keystone, a `form_8606` result block + `prior_year.ira_basis`
profile field, a `/tax-rollover` carryforward of basis, a `/tax-compute` Form 8606 step, and
`--ira-basis`/`--ira-value` on `/tax-roth-convert` (each rung's taxable conversion diluted by
the basis ratio). This closes the "full conversion is taxable" simplification #6 deferred and
the backdoor-Roth pro-rata trap.

**Progress (2026-05-28):** #4 (tax-loss harvesting scanner) is shipped. Added
`agents/lib/loss_harvest.py` + `/tax-harvest` skill + `tests/fixtures/harvest/`. Reuses
`scenario.py` wholesale (the FIRST anchored skill that consumes a separate user input file,
`holdings.json`) and adds three primitives: `derive_holding_period` (IRS strict >365-day
rule), `check_wash_sale` (§1091 ±30-day bilateral, all accounts pooled, same-ticker proxy),
and a per-lot ranking heuristic using ord_mtr (ST losses) and ltcg_mtr (LT losses). Aggregate
savings re-does Schedule D netting via `_net_capital_gains(new_st, new_lt)` and expresses the
harvest as a `compute_scenario` delta, so the $3K/$1.5K-MFS cap, character preservation, and
LTCG bracket positioning come "for free" from the keystone. State savings = state_mtr ×
keystone's Δ taxable_income (degrades to $0 when state.brackets empty). Federal anchored;
state simplified. Recommended next: **#11 retirement contribution dashboard** (S) or
**#10 quarterly reminder calendar** (XS).

**Progress (2026-05-28 part 2):** #10 (quarterly reminder calendar) is shipped.
Added `agents/lib/tax_calendar.py` + `/tax-calendar` skill + `/tax-setup` step
6.5 + `age_73_plus_primary/spouse` flags + 39 new tests (537 total). Pure-
compute lib + CLI + workspace-bounded writer mirrors the established
"derived-artifact" pattern but writes at the workspace root rather than under
`scenarios/` because the `.ics` is a top-level deliverable for the taxpayer.
RFC 5545 compliant (CRLF, all-day VALUE=DATE form, line folding, escaped
TEXT specials, 7-day VALARM). UIDs are stable so re-importing the regenerated
calendar updates existing events rather than duplicating them. v1 caveats:
statutory dates only (no IRS weekend/holiday rollover), PTET coverage limited
to CA/NY/NJ, RMD uses Dec 31 only (first-year April-1 deferral not modeled —
rarely advantageous). Recommended next: **#11 retirement contribution
dashboard** (S), **#9 PTET election analyzer** (S), or **#8 crypto importer**
(M).
