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

Legacy untagged universal API output remains available for historical diagnostics;
from 2025 it is marked incomplete and exits 2. `--per-account` remains available
for one isolated account with independently reviewed destination lot inputs.
Account `complete` certifies inventory evidence only. C/F output boxes are legacy
placeholders: review current-year Form 8949/Schedule D instructions and actual
broker forms before merging rows. TY2025 digital assets use G/H/I and J/K/L,
with the selection depending on reporting evidence. The skill blocks unresolved
reporting review; deterministic 1099-DA reconciliation is AA41. Source: [TY2025
Form 8949 instructions](https://www.irs.gov/instructions/i8949), verified live
on 2026-10-07. Future-year routing requires its own reviewed instructions.
Specific identification, gifts, NFTs, DeFi and futures remain outside this engine.

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
