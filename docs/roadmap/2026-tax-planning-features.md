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

| # | Feature | Skill name | Why it matters | Effort |
|---|---------|-----------|----------------|--------|
| 1 | **Roll-forward last year's return** | `/tax-rollover <prior_year>` | Reads prior `result.json`, copies carryforwards into new `taxpayer_profile.json` and `result.json`. Eliminates the #1 source of "I forgot I had a $3K cap loss carryover" errors. Foundation for every multi-year feature below. | S |
| 2 | **Mid-year projection** | `/tax-project [--as-of MM-DD]` | Takes YTD paystubs + last year's pattern, projects full-year tax, computes safe-harbor estimates, suggests W-4 step 4(c) adjustment. Rerunnable quarterly. | M |
| 3 | **Scenario modeling** | `/tax-scenario <name>` | Forks `result.json` → `scenarios/<name>.json`, applies one delta (e.g. `+roth_conversion=40000`), recomputes, diffs against baseline. Stackable. | M |
| 4 | **Tax-loss harvesting scanner** | `/tax-harvest` | Reads brokerage lot data, ranks losing positions by harvestable loss, checks 30-day wash-sale window against your own buys, outputs sell list with $ federal+state savings. | M |
| 5 | **Withholding true-up** | `/tax-withholding` | Compares YTD federal withholding vs projected liability; outputs revised W-4 step 4(c) amount or 1040-ES voucher schedule. | S |
| 6 | **Roth conversion ladder modeler** | `/tax-roth-convert` | Sweeps conversion amounts $0–$100K in $5K steps; charts marginal cost per $; flags IRMAA, NIIT, LTCG bracket-bumping thresholds. | M |
| 7 | **Form 8606 basis tracker** | (template + compute path) | Persists non-deductible IRA basis across years; auto-applies pro-rata formula on conversions. Closes the backdoor-Roth pro-rata trap. | S |
| 8 | **Crypto importer** | `/tax-parse-crypto` | Parses Coinbase/Kraken/Gemini CSVs into Form 8949 rows; flags any digital-asset activity for the 1040 page-1 question. | M |
| 9 | **PTET election analyzer** (CA/NY/NJ) | section in `/tax-review` + new check in `/tax-project` | Detects K-1 from passthrough; estimates SALT-cap workaround savings; reminds of state election deadlines (CA is March 15 prior-year). | S |
| 10 | **Quarterly reminder calendar** | output of `/tax-setup` | Generates `.ics` file with 1040-ES due dates, FBAR (Apr 15 → auto-extended Oct 15), state PTET windows, RMD deadlines. | XS |
| 11 | **Retirement contribution dashboard** | `/tax-retirement` | One screen: 401(k) / IRA / HSA / Mega Backdoor — YTD vs max, with marginal-rate $ saved per remaining dollar. Action-oriented. | S |
| 12 | **What-changed report YoY** | output of `/tax-review` | Diffs current return against prior `result.json`: AGI Δ, bracket Δ, effective rate Δ, line-item movers > $1K. The single artifact a client most wants to see. | S |

## Open product questions

- **Scope.** Items 2, 3, 5, 6 collectively become a "tax planning suite" — competing with HRB's "Tax Pro Review" and apps like Origin. Decision needed: stay narrowly focused on preparation excellence, or expand?
- **YTD data ingestion fidelity.** Mid-year features need YTD paystubs / brokerage screenshots. Cheap path: "paste your YTD wage and withholding numbers." Expensive path: paystub PDF parsers. Recommend cheap path first.

## Build order

Brainstorming starts with **#1 `/tax-rollover`** — it is the foundation that unlocks #2, #3, #6, #7, and #12.
