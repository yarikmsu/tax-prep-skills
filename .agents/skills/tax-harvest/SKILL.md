---
name: tax-harvest
description: Tax-loss harvesting scanner — reads brokerage lot data, ranks losing positions by harvestable loss, checks the §1091 wash-sale window (±30 days, all accounts pooled), and reports federal + state $ savings. Planning-only, never mutates the baseline.
---

# /tax-harvest — Tax-Loss Harvesting Scanner

Scans `holdings.json` for unrealized losses, separates wash-sale-blocked lots
from harvestable ones, computes the post-harvest Schedule D netting against the
baseline gains in `result.json`, and reports the federal + state tax savings of
executing the eligible harvest **this tax year**.

Federal savings is **anchored** to the real `result.json` line 24 (same contract
as `/tax-scenario` and `/tax-roth-convert`): only the marginal effect of the
harvest is computed against the keystone, so the rendered savings is the actual
$ delta around your real tax. State savings is a simplified estimate
(`state_ordinary_marginal_rate × federal_taxable_income_reduction`), and is one
of three states depending on `tax_law.state`:
- **Modeled** — `state.brackets` is populated: state savings is the estimate above.
- **No state income tax** — `state.type == "none"` (FL, TX, ...) AND the full
  residence chain holds (a KNOWN resident state matching the cache's code and
  year): state savings is reported as **$0 — correct and complete**, not a gap
  to fill; there is no state tax to save. An UNKNOWN resident state (no
  `address.state`) degrades to "not modeled" instead of affirming — the claim
  needs a resident to attribute it to. When
  `flags.has_nonresident_source_income` is set, the claim degrades to
  "$0 for the RESIDENT state — NOT the complete picture" and a nonresident
  income exposure warning renders: the source state's unmodeled tax can
  change both the value and the ranking of harvested lots.
- **Not fetched** — `state.brackets` is empty and `state.type` is not `"none"`:
  degrades to "state savings not modeled" with a warning to run `/tax-setup`.

Read-only with respect to the baseline: never modifies `result.json` or
`taxpayer_profile.json`. Writes a single `scenarios/<name>.json` (chmod 600) via
the same guarded writer `/tax-scenario` uses.

## Prerequisites
- `result.json` with a computed `form_1040.line_24_total_tax` (else: run `/tax-compute`).
- `taxpayer_profile.json` (filing status, state).
- `tax_law_{year}.json` with **populated `federal.capital_gains_rates`** (both
  `zero_pct_limit` and `fifteen_pct_limit` for the filing status). The scanner REFUSES
  to run when these are missing AND the baseline has LT cap gains or qualified dividends —
  the keystone would otherwise silently price preferential income at ordinary rates,
  inflating fed_savings. Run `/tax-setup` if `/tax-harvest` errors out with this message.
  State brackets are optional: absent AND `state.type == "none"` (FL, TX, ...) reports
  "$0 — correct and complete" when the resident state is known and matches the cache
  (degraded when unknown, or when `flags.has_nonresident_source_income` is set);
  absent otherwise degrades to "state savings not modeled".
- `holdings.json` in the workspace — see schema below.

## Schema: `holdings.json`

```json
{
  "as_of": "2026-12-15",
  "lots": [
    {
      "id": "L1",
      "ticker": "VTSAX",
      "account": "Schwab Brokerage",
      "acquired": "2024-01-15",
      "quantity": 100,
      "cost_basis": 12000,
      "current_value": 10500,
      "tax_advantaged": false
    },
    {
      "id": "L2",
      "ticker": "META",
      "account": "Fidelity IRA",
      "acquired": "2022-05-01",
      "quantity": 10,
      "cost_basis": 4000,
      "current_value": 2500,
      "tax_advantaged": true
    }
  ],
  "recent_transactions": [
    {
      "ticker": "VTSAX",
      "account": "Spouse Schwab",
      "date": "2026-12-01",
      "kind": "buy",
      "quantity": 5,
      "amount": 525
    }
  ]
}
```

- **`as_of`** (optional): date used for holding-period and wash-sale window calculations.
  Defaults to today.
- **Date fields are TRADE dates, not settlement dates.** `acquired`, `recent_transactions[].date`,
  and `as_of` MUST all be trade dates per IRS Pub. 550 (gain/loss is recognized on trade
  date; §1091's ±30-day window is also measured from trade date). A Schwab/Fidelity CSV
  export typically has both columns — pick the "Trade Date" / "Transaction Date" column,
  not "Settlement Date". The 2-business-day skew between the two can shift wash-sale
  classification incorrectly. (Short-sale closes are the legitimate §1233 exception —
  see action plan.)
- **`lots`**: open positions. `cost_basis` and `current_value` are TOTAL (not per share).
  `acquired` is the lot's tax-basis date (per IRS lot-tracking rules). `quantity` MUST
  be > 0 — closed/empty lots (a brokerage export that still carries a row after a full
  sell) must be filtered out; otherwise the scanner refuses the file. A `cost_basis` of
  0 is fine (gift / employer-match shares); a `current_value` of 0 is fine (worthless
  security harvest under §165(g)).
- **`tax_advantaged`** (default `false`): set to `true` for lots inside an IRA, Roth IRA,
  401(k), 403(b), HSA, or any other tax-advantaged account. §165 does NOT allow Schedule D
  capital losses for sales inside such accounts, so the scanner EXCLUDES these lots from
  the harvest plan (recommending the sell would produce $0 of real tax savings). However,
  per **Rev. Rul. 2008-5**, BUY transactions in these accounts STILL trigger §1091 on
  harvests in your taxable accounts — and the disallowed loss in that case is permanently
  lost (no IRA basis to absorb it). So: include the IRA accounts in `holdings.json` (lots
  + transactions) so the wash-sale pool is correct, but mark the IRA lots
  `tax_advantaged: true` so their losses don't get recommended. Same goes for HSAs and
  Roth accounts.
- **`recent_transactions`**: only BUYS within ±30 days of `as_of` matter for wash-sale
  detection. Include ALL accounts §1091 reaches (your IRAs, your spouse's accounts,
  controlled trusts). Future-dated buys (≤ 30 days after `as_of`) also poison a loss —
  include any KNOWN planned trades so the scanner can warn. **You can (and typically
  should) leave the original acquisition buys of any open lots in this list** — the
  matcher is lot-aware and correctly excludes a buy whose `(date, ticker, account)`
  matches a harvest lot's `acquired` date, ticker, AND account from the replacement
  pool. **`account` MUST be populated on every buy txn for self-exclusion to work**:
  a buy without an `account` field, or with a different account name than the lot
  it acquired, is treated as a genuine §1091 replacement (a same-day same-ticker buy
  in your spouse's account IS a wash-sale trigger per Rev. Rul. 2008-5 + spousal-account
  rules — and the scanner correctly catches it). Account comparison is case-insensitive
  and whitespace-trimmed. Brokerage CSVs always include account info; paste the full
  transaction list and the scanner does the right thing.

## Steps

1. **Verify prerequisites.** Confirm `result.json`, `taxpayer_profile.json`,
   `tax_law_{year}.json`, and `holdings.json` exist. If `result.json` lacks a computed
   line 24, stop and tell the user to run `/tax-compute`. If `holdings.json` is missing,
   ask the user to export their lots from their brokerage (Schwab/Fidelity/Vanguard all
   offer "unrealized gain/loss by lot" CSV exports — you can help convert to the schema
   above).

2. **Sanity-check the holdings.**
   - Per-lot: `current_value < cost_basis` → at a loss; `acquired` is not in the future;
     `quantity > 0`. The library rejects malformed inputs.
   - Pool all accounts subject to §1091 reach (spouse + IRAs). If the user has accounts
     OUTSIDE the file (e.g. spouse's brokerage they forgot to include), warn — wash-sale
     blocking can only check what's in the file.

3. **Compute.** Run:
   ```bash
   python3 -m agents.lib.loss_harvest --result result.json --workspace . \
       --year {year} --holdings holdings.json --name {name} --json
   ```
   Writes `scenarios/{name}.json` (chmod 600, default name `harvest`) and emits JSON.

4. **Render.** Run the same command without `--json` to print the markdown report. Save
   it to `scenarios/{name}.md` and `chmod 600`. Walk the user through:
   - **Bottom-line table** — eligible loss, federal/state savings, carryforward.
   - **Schedule D after harvest** — baseline vs. post-harvest net ST/LT.
   - **Harvest plan** — ranked eligible lots with per-lot value-per-dollar (ST losses
     rank above LT losses when there is ordinary income to offset).
   - **Wash-sale blocked lots** — what's locked out, by which transaction, until when.

5. **Action plan.** Recommend the user:
   - **Trade by the final trading day of the tax year** (December 31, or the prior
     business day when 12/31 falls on a weekend or market holiday). Per IRS Pub. 550,
     gain/loss on securities traded on an established market is recognized on the
     **trade date**, not the settlement date — T+2 settlement timing affects when cash
     moves, not which tax year the trade counts in. A late-December harvest sale
     executed on Dec 31 is a Schedule D event for the current year even though
     settlement clears in early January.
   - **Short-sale exception (§1233):** if the harvest is closing a short position,
     gain/loss is recognized on the **settlement** date, not the trade date. Plan to
     close short positions earlier in December so settlement still falls in this tax year.
   - For the next **31 days from the latest harvest sell**, do NOT buy the same security
     in any account (yours, spouse's, IRA). A buy poisons the loss. §1091's ±30-day
     window is measured from the trade date, the same date Schedule D recognizes.
   - For wash-blocked lots, note the **unblocking date** (31 days past the blocking buy)
     and re-run `/tax-harvest` then.

6. **Audit note.** Append one line to `answers.md` (NO dollar PII):
   ```
   ## Tax-loss harvest "{name}" TY{year} as of {as_of} — {N} lots eligible, run {today}.
   ```

## How to read the savings

- **Federal savings** is anchored — it's the precise Δ around `result.json` line 24
  via the shared `compute_scenario` keystone, so it accounts for character preservation
  (ST loss → ordinary, LT loss → LTCG when offsetting LT gains) and the $3K loss cap.
- **State savings** is a *simplified* estimate: state ordinary marginal rate × federal
  taxable-income reduction. Most states (CA, NJ, NY, MA, etc.) tax capital gains as
  ordinary income, so this is close; states with preferential cap-gains treatment may
  differ.
- **Value-per-dollar** column on the per-lot table is a heuristic for ranking, not a
  precise per-lot savings — the actual marginal value of a lot depends on which lots
  before it have already consumed the $3K ordinary cap and the LT-gain headroom.

## Caveats (v1)
- **Wash sale §1091 is bilateral** — buys ±30 days from the sale poison the loss. The
  scanner can check the trailing window directly (in `recent_transactions`) and the
  forward window only if the user lists planned buys; after harvesting, the user must
  not buy the security in any pooled account for 31 days.
- **Same-ticker is the "substantially identical" proxy.** Different-CUSIP ETF swaps
  tracking the same index are NOT flagged (the very strategy users want), but the IRS
  has no bright-line rule on this — consult guidance for your situation.
- **Accounts are pooled across the file.** The user controls which accounts to include.
  Omitting a spouse account or an IRA hides §1091 reach.
- **QSBS §1202 (not modeled — do not assert the old rule).** OBBBA revised §1202 for
  qualified small-business stock ACQUIRED after 2025-07-04: a tiered exclusion of
  50% / 75% / 100% at 3 / 4 / 5-year holding (vs the old flat 5-year / 100%-only), with
  a raised per-issuer cap and gross-assets ceiling. The earliest affected sales ripen
  2028+. This scanner does NOT compute §1202 gain exclusion — if a losing/gaining lot is
  QSBS, the exclusion is hand-checked against the acquisition date; don't apply the
  pre-OBBBA 5-year/100% rule to post-2025-07-04 stock.
- **Federal only on the anchored side.** State is a marginal estimate.
- **Holding period uses fixed 365-day comparison.** Leap years may shift the boundary
  by 1 day on lots held very close to one year.
- **NIIT relief (§1411, 3.8%)** is layered OUTSIDE the keystone via `compute_niit` (the
  shared keystone deliberately omits NIIT). A high-MAGI taxpayer harvesting capital losses
  gets NIIT relief from a lower NII base AND from MAGI dropping further below (or even
  below) the §1411 threshold; both effects are reflected. NII = `interest + ordinary_dividends
  + max(0, LT capital_gains)` — net short-term gains routed to ordinary income are NOT added
  back (a v1 simplification shared with `/tax-roth-convert`). Effect: if you harvest large
  ST losses, NIIT relief shown may understate by ~3.8% × (ST losses harvested). Below the
  threshold, NIIT relief is $0 and the bottom-line table collapses to a single `Federal tax
  savings` row.
- **LTCG bracket down-shifting** (a harvest dropping AGI from the 20%→15% or 15%→0%
  preferential bracket) IS reflected automatically via the QDCG worksheet inside the
  keystone.
- A planning estimate, not a filed return.

## Security & safety
- Writes ONLY under `scenarios/`; the path guard refuses any other target and never
  overwrites `result.json` / `taxpayer_profile.json`.
- `--holdings` paths are validated against the workspace boundary (rejects absolute
  paths or `../` escapes outside the workspace).
- Scenario names must be a single safe path segment (no `/`, `\`, `..`, leading dot).
- `holdings.json` and `scenarios/` are gitignored — both hold lot-level financial data.
- All written files (`scenarios/*`) → `chmod 600`.
- Any delegated sub-task must have NO network access and must NOT write outside `scenarios/`.
- Never extract or store full SSNs or account numbers; the holdings schema uses
  human-readable account labels (e.g. "Schwab Brokerage"), not account numbers.
