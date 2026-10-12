# Crypto account FIFO and transfers

AA40 adds a single-import inventory engine keyed by `(account_id, asset)`.
FIFO remains the only selection method. Exchange names identify formats, not
accounts: two accounts at the same exchange require different opaque IDs.
IDs and evidence references must contain no identity values or credentials.

## Reviewed input contract

Every event carries `account_id` and an account-unique `txn_id`. Adapt with
`--account-id` to receive deterministic export-hash/row IDs, or supply reviewed
exchange transaction IDs. Re-importing the same normalized IDs hard-errors.
Overlapping exports can have different generated IDs: deduplicate economic events
before merging them. Do not infer ownership from matching coin quantities.

Include all acquisition and disposal history needed by the opening inventory.
For pre-2025 universal records, review the wallet allocation under Rev. Proc.
2024-28 first; tagging records is the assertion of that reviewed allocation,
not an automatic safe-harbor allocation algorithm. Keep the allocation evidence
in the secured workspace. Missing history cannot borrow another account's lots.

```json
{
  "transactions": [
    {"account_id": "exchange-A", "txn_id": "buy-1", "date": "2023-01-01",
     "kind": "buy", "asset": "BTC", "quantity": "2", "usd_amount": 200},
    {"account_id": "exchange-A", "txn_id": "send-1", "date": "2025-02-01",
     "kind": "send", "asset": "BTC", "quantity": "1", "transfer_id": "move-1"},
    {"account_id": "wallet-B", "txn_id": "receive-1", "date": "2025-02-01",
     "kind": "receive", "asset": "BTC", "quantity": "1", "transfer_id": "move-1"},
    {"account_id": "wallet-B", "txn_id": "sell-1", "date": "2025-03-01",
     "kind": "sell", "asset": "BTC", "quantity": "1", "usd_amount": 150}
  ],
  "transfer_links": {
    "move-1": {"ownership_confirmed": true, "evidence": "reviewed-movement-1"}
  }
}
```

Both transfer legs must reconcile exactly: one send, one receive, different
accounts, same asset and quantity, arrival no earlier than departure, and
explicit reviewed ownership evidence. Linked receives cannot override basis or
acquisition dates. Partial transfers move actual FIFO slices with their original
basis and dates; sending consumes source inventory, so a later source sale cannot
reuse the moved coins. Destination FIFO orders by original acquisition time.

ISO timestamps with offsets are ordered in UTC; dates on forms and year filters
retain the declared source calendar dates. Confirm the export's date convention.
At equal timestamps, input order within each account is the caller's declared
reviewed order; linked sends precede their receives across accounts. If exports
lack reliable ordering, obtain sequence evidence before importing. Optional
`sequence` values must cover every event at that timestamp and be unique
nonnegative integers. Conflicting local order and transfer causality hard-error.

An owned wallet outside this import may use `external_transfer_confirmed: true`.
An external receive additionally requires `basis_usd` and original `acquired`
date. These are reviewed opening lot inputs, not inferred transfer-date basis.
Represent separate original lots as separate receives. Gifts and unknown
ownership are outside this contract. A confirmed external send consumes lots
without creating a taxable disposal. Transfer fees paid in digital assets need
separate reviewed taxable `spend` events with FMV and quantity; a fee on a
send/receive hard-errors rather than disappearing from the return.

## Output and completion

`compute --account-aware` enforces the contract even on an empty input. Tagged
rows select it automatically; mixed tagged/untagged records are rejected.
`account_fifo` contains final inventory, exact rational quantity/basis records,
transfer slices and provenance, unresolved inputs, and a boolean `complete`.
Amounts on forms round to cents; exact fractions preserve transfer conservation.
Only filing-year sales and income enter return totals; historical events still
consume inventory and unresolved historical inventory blocks completion.

Incomplete imports return diagnostic output with CLI exit 2 and a BLOCKED
heading. Never merge them into a return or waive transfer evidence through a
missing-basis acknowledgement. Copy `crypto_summary`, including `basis_mode`,
`complete` and `unresolved_inputs`, and retain `account_fifo` in the secured
workpaper. Tie-out fails incomplete/missing account metadata independently of
whether reported dollars happen to match. Account IDs in Form 8949 rows also
trigger this check if the summary was removed.

Legacy untagged universal output and `--per-account` remain for diagnostics.
Neither carries the account and transaction IDs a broker form is matched to, so
neither routes a Form 8949 box: with any reported row both exit 2. Account
`complete` certifies inventory evidence only; Form 8949 routing is the separate
`reporting_complete` gate below. Specific identification, gifts, NFTs, DeFi and
futures remain outside this engine.

## Broker-form reconciliation

AA41 routes each reported row to its Form 8949 box from explicit evidence, never
from a form's absence in the import. `broker_reporting` sits beside
`transfer_links` in the normalized JSON, and needs `--year`:

```json
"broker_reporting": {
  "accounts": {
    "exchange-A": {"forms_received": true, "evidence": "reviewed-1099da-exchange-A",
                   "not_reported": ["swap-7"]},
    "wallet-B": {"forms_received": false, "evidence": "self-custody-no-broker"}
  },
  "forms": [
    {"form_id": "da-1", "form_type": "1099-DA", "account_id": "exchange-A",
     "txn_id": "sell-1", "asset": "BTC", "quantity": "1", "date_sold": "2025-03-01",
     "proceeds": 150.00, "cost_basis": null, "basis_reported_to_irs": false,
     "term": null, "ordinary": false, "accrued_market_discount": null,
     "wash_sale_loss_disallowed": null, "loss_not_allowed": false}
  ],
  "resolutions": {
    "da-1": {"proceeds": "taxpayer_records", "evidence": "trade-confirmation-1"}
  }
}
```

- **accounts** — every account with a filing-year disposal declares whether it
  received broker forms for that year. `forms_received: true` asserts the listed
  forms are all of them, and names any disposal they do not report in
  `not_reported`, which must each be a filing-year disposal of that account. An
  undeclared account blocks; it is never presumed formless.
- **forms** — one entry per reported transaction, linked by `txn_id` to the
  reviewed disposal it reports. The asset and units must match it, and the date
  sold must be the import's date or the day either side in the same year:
  §1.6045-1(d)(2)(i)(B)(3) asks the broker only for "the sale date", naming no
  time zone, and no UTC offset reaches a full day. The row keeps the import's
  date; the workpaper records both.
  Every key is required. `cost_basis` and `term` are null where the form
  leaves them blank (1099-DA box 1g and box 6; 1099-B box 1e and box 2), never
  absent, because an omitted term would pass a form whose box disagrees with
  FIFO without code T. `basis_reported_to_irs` is 1099-DA
  box 2 or 1099-B box 12. A disposal split across forms must mirror the FIFO
  split by holding period, one form each; when their total agrees, the forms'
  own figures replace a one-cent apportionment difference in proceeds. Basis
  apportionment requires both forms to report basis to the IRS; otherwise each
  basis difference requires an evidenced resolution preserving FIFO gain.
  Four boxes this engine does not model are likewise REQUIRED,
  null or false when blank: `ordinary` (1099-DA box 6, 1099-B box 2),
  `accrued_market_discount` (1h / 1f), `wash_sale_loss_disallowed` (1i / 1g)
  and `loss_not_allowed` (box 5 / box 7). Filled in, each refuses: report that
  transaction manually. Backup withholding (box 4) belongs to the parse ledger,
  not here.
- **resolutions** — a difference stays unresolved until a resolution with its
  own evidence names the treatment. Each keeps the FIFO gain in column (h):

| Difference | Resolution | Form 8949 |
|---|---|---|
| Proceeds above the form | `proceeds: taxpayer_records` | (d) the correct, higher proceeds; reported as `proceeds_above_forms` |
| Proceeds below the form, by no more than the recorded fee | `proceeds: transaction_costs` | (d) the form's proceeds; code E; the unreflected costs in (g) |
| Basis reported to the IRS differs | `basis: taxpayer_records` | (e) the reported basis; code B; (g) = reported − FIFO basis |
| Basis shown but not reported differs | `basis: taxpayer_records` | (e) the FIFO basis; code B; (g) zero |
| Holding period differs | `term: taxpayer_records` | the correct part; code T; (g) zero |

The only costs a form can leave unreflected are the fees the import recorded,
so a larger gap below the form is a wrong price, never code E: obtain a corrected
form or correct the import. A basis-reported form spanning both holding periods
stays unresolved when its basis differs, whether or not it states a term, and
when it states a term the FIFO split contradicts, even at an equal basis: the
broker identified other lots, so review its lot identification. Only a
blank-term form whose basis equals FIFO's routes each part with its own FIFO
basis. So do a resolution that matches no difference, a form naming no
filing-year disposal — including a transfer the broker reported as a sale — a
2025 digital asset on Form 1099-B, and a date sold that differs from the
import's declared date.

The boxes for each reviewed year come from that year's instructions, verified
live on 2026-10-11:

| Year | Broker form | Short-term: basis reported / not reported / no form | Long-term | Source |
|---|---|---|---|---|
| TY2023 | 1099-B | A / B / C | D / E / F | [Form 8949 instructions (2023)](https://www.irs.gov/pub/irs-prior/i8949--2023.pdf) |
| TY2024 | 1099-B | A / B / C | D / E / F | [Form 8949 instructions (2024)](https://www.irs.gov/pub/irs-prior/i8949--2024.pdf) |
| TY2025 | 1099-DA | G / H / I | J / K / L | [Form 8949 instructions (2025)](https://www.irs.gov/pub/irs-prior/i8949--2025.pdf) |

The TY2025 instructions say digital asset transactions "should not be reported
using box C or F". A broker need not report basis for a 2025 sale ([Form 1099-DA
instructions (2025)](https://www.irs.gov/pub/irs-prior/i1099da--2025.pdf)), so
most 2025 forms route to H or K. Any other year blocks every reported row until
its own reviewed entry joins `_REPORTING_REGIMES` and this table; a test holds
the two together.

Each row's proceeds and basis are the disposal's rounded running totals less
the cents already posted, a tie away from zero, so a disposal's rows add up to
its rounded figures and the FIFO side of a reconciliation is the very cents the
return carries: a form that agrees leaves column (h) unchanged.

The result's `broker_reporting` records each form's broker and FIFO figures,
codes, evidence, status and reason — an unresolved form included — the
unresolved items, and `schedule_d_routing`. A form's status is `reconciled`,
`unresolved` or `blocked`; `blocked` means the form agrees but another form for
the same disposal does not, since a disposal routes all its rows or none. Split
forms that cannot be paired with the FIFO parts are each set against the whole
disposal's figures. `schedule_d_routing` gives each box's `result.json`
Schedule D row, line, totals and joined column (f) codes, its amounts keyed as
that row is (`cost`, not `cost_basis`) so they add in key for key. Schedule D
pairs the boxes on one line (A/G 1b, B/H 2, C/I 3, D/J 8b, E/K
9, F/L 10), and digital assets take rows of their own on each,
`line_1b_digital_assets` through `line_10_digital_assets`, never the 1099-B rows.
Form 8949 code B is not unique to employer stock: the employer-stock tie reads a
whole 1099-B row's column (g), so a crypto correction merged into a row an RSU
correction flags `employer_stock` would read as employer stock. Tie-out fails a
digital-asset row that claims the flag. Rows carry `adjustment_code`,
`adjustment`, `fifo_cost_basis` and `form_id`; a row without evidence carries
`box: null`. `crypto_summary` gains `reporting_complete`, `reporting_unresolved`
and `reporting_proceeds_above_forms`. Any unresolved item exits 2 under a
BLOCKED heading, and tie-out fails it independently of the dollars — including
for a summary from before AA41, whose only mark of crypto is a non-zero
`disposals`, and for merged digital-asset rows with no summary at all.

`/tax-parse` ledgers a 1099-DA in `documents/` with its box 1f total as
`capital_proceeds`. The tie sums the form-backed digital-asset rows with the
1099-B rows and leaves out the no-form rows of lines 3 and 10, so it compares
like with like; the documented `proceeds_above_forms`
excess comes off the result side, which it can only lower, and the key alone
meets the reporting gate. A non-zero no-form row keeps that tie verifiable, so
a form wrongly declared "not received" fails it instead of warning.

## Primary authorities

The IRS treats transfers between owned wallets as non-taxable, except digital
assets used for transfer fees, and default FIFO follows original acquisition
order within the wallet. See [digital asset transaction FAQs, questions 81 and
87](https://www.irs.gov/individuals/international-taxpayers/frequently-asked-questions-on-digital-asset-transactions).
Wallet/account basis allocation applies from 2025; the transition safe harbor
requires a reviewed allocation, not continued universal matching. See
[Rev. Proc. 2024-28](https://www.irs.gov/pub/irs-drop/rp-24-28.pdf) and the
[IRS digital assets guidance](https://www.irs.gov/filing/digital-assets).
Sources verified by live primary-authority HTTPS on 2026-10-07. This contract
contains no annual rates or indexed thresholds.
