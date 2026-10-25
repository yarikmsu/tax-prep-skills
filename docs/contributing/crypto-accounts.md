# Crypto account FIFO and transfers

AA40 adds a single-import inventory engine keyed by `(account_id, asset)`.
FIFO remains the only selection method. Exchange names identify formats, not
accounts: two accounts at the same exchange require different opaque IDs.
IDs and evidence references must contain no identity values or credentials.

Before matching lots, ask about specific identification at every broker and wallet,
including standing instructions and prior sales/transfers affecting opening
inventory. Record `flags.has_crypto_specific_identification` and the answer in
`answers.md`; leave an unasked question null. The normalized input and pure API
take `specific_identification`: only explicit `false` permits FIFO lot removal.
True requires external selected-lot/Form 8949 preparation, even after a capability
acknowledgement. The CLI never supplies an answer on the taxpayer's behalf.
Regulations [§1.1012-1(j)](https://www.ecfr.gov/current/title-26/section-1.1012-1)
cover both broker and unhosted-wallet identification, including standing orders.
A broker offering only one method makes it a standing order ((j)(3)(ii)).
Include identification recorded in the taxpayer's books under applicable relief
([Notice 2025-7](https://www.irs.gov/irb/2025-05_IRB#NOT-2025-7), broker-held
units disposed of in 2025 only); broker reports
can therefore disagree with the taxpayer's lot choice without reporting a basis.

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
  "specific_identification": false,
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
Records in different offsets can therefore fund a sale from a lot whose own
calendar dates it after the sale; such a row refuses, naming the sale and the
lot, until both are restated in one calendar (AA85).
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
     "wash_sale_loss_disallowed": null, "loss_not_allowed": false,
     "net_proceeds": false, "qof": false,
     "customer_acquisition_info": false,
     "optional_method": null, "primary_sale_proceeds": null, "cusip": null}
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
  reviewed disposal it reports, or to the `group_id` of
  [linked partial fills](#linked-partial-fills-aa47). The asset and units must match it, and the date
  sold must be the import's date or the day either side in the same year:
  §1.6045-1(d)(2)(i)(B)(3) asks the broker only for "the sale date", naming no
  time zone, and no UTC offset reaches a full day. The row keeps the import's
  date; the workpaper records both.
  Every key is required. Each key's 1099-DA box is in
  [1099-DA input table](#reviewed-form-1099-da-inputs-aa55) or
  [1099-B input table](#reviewed-form-1099-b-inputs-aa61). `cost_basis` and `term` are null where the form leaves
  them blank (1099-B box 1e and box 2), never absent, because an omitted term
  would pass a form whose box disagrees with FIFO without code T.
  `basis_reported_to_irs` is 1099-B box 12. A disposal split across forms must
  mirror the FIFO split by holding period, one form each; when their total
  agrees, the forms' own figures replace a one-cent apportionment difference in proceeds. Basis
  apportionment requires both forms to report basis to the IRS; otherwise each
  basis difference requires an evidenced resolution preserving FIFO gain.
  The adopted cent goes on the form's largest row; re-spreading the form's
  figure by units would reprice lots of different basis, or fills of one
  order at different prices.
  Four boxes this engine does not model are likewise REQUIRED,
  null or false when blank: `ordinary` (1099-B box 2),
  `accrued_market_discount` (1f), `wash_sale_loss_disallowed` (1g)
  and `loss_not_allowed` (box 7). Filled in, each refuses the import unless
  that disposal is listed under [manual disposals](#manual-disposals-aa47),
  which leaves it to be prepared by hand. Each form carries additional required keys under the
  same rule, listed in its reviewed table below. A form naming a key outside
  its type's set is refused.
  Backup and state withholding (both forms' boxes 4 and 16) belong to the parse
  ledger, not here.
- **resolutions** — a difference stays unresolved until a resolution with its
  own evidence names the treatment. Each keeps the FIFO gain in column (h):

| Difference | Resolution | Form 8949 |
|---|---|---|
| Proceeds above the form | `proceeds: taxpayer_records` | (d) the correct, higher proceeds; reported as `proceeds_above_forms` |
| Proceeds below the form, by no more than the recorded fee | `proceeds: transaction_costs` | (d) the form's proceeds; code E; the unreflected costs in (g) |
| Proceeds above the form, by no more than a reviewed written call's premium | `proceeds: option_premium` | (d) the form's proceeds; code E; the unreflected premium in (g) |
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

Merged crypto, or an unskipped 1099-DA in the parse ledger whatever rows its
proceeds reached, also requires an explicit boolean answer to
`flags.has_digital_asset_option_exercise`. The tie-out API's omitted answer and
the CLI's absent profile both leave it unanswered and fail the gate. Ask about
assets sold, exchanged or spent, including lots an option exercise acquired in
an earlier year: [TY2025 Pub. 550, Options](https://www.irs.gov/pub/irs-prior/p550--2025.pdf)
adjusts acquired basis for the option premium, and the
[IRS digital-asset guidance](https://www.irs.gov/filing/digital-assets) treats
exchanges and spending as dispositions too. A yes blocks at the capability gate
unless explicitly acknowledged. Acknowledgement alone cannot complete the
return: the [reviewed option-premium path](#reviewed-option-premiums-aa47)
requires source-bound evidence and tie-out recomputation of the merged gains.

`/tax-parse` ledgers a 1099-DA in `documents/` with its box 1f total as
`capital_proceeds`. The tie sums the form-backed digital-asset rows with the
1099-B rows and leaves out the no-form rows of lines 3 and 10, so it compares
like with like. Each routed Form 8949 row retains `broker_proceeds` allocated
from its own form in whole cents, and each Schedule D digital row sums those
figures. These are audit amounts, separate from Form 8949 column (d): a correct,
higher disposal price stays in column (d), and code E still uses the form's
gross proceeds there. A form spanning several lot slices is allocated once;
the broker allocations never reprice those slices. No-form rows carry null.

Active form-backed rows without `broker_proceeds` must be rebuilt from the
reviewed import and source forms. Never infer a missing figure by subtracting
the old scalar. Broker figures must be finite, nonnegative whole-cent amounts;
an explicit zero is meaningful. Rebuild all digital rows from the complete
normalized input on a rerun so old tax amounts and new audit amounts cannot
be merged separately. Equity rows stay separate.

The tie sums the digital rows' own broker figures alongside equity proceeds;
it never subtracts `crypto_summary.reporting_proceeds_above_forms`, so a stale
scalar cannot offset an equity over-merge. Column (d) is held to the forms
separately: the form-backed digital rows' column (d) may exceed their broker
figures only by exactly that scalar, the excess the import reported with
reviewed evidence. A column (d) merged twice, or a broker figure that went
stale, leaves a difference no import reported and fails, as does column (d)
below its broker figures. The scalar must be a finite, nonnegative whole-cent
amount; absent, it reads as zero. A row's broker figure is allocated from its
form in proportion to its own column (d), so fills of one order at different
prices each keep their own. Where the merged Form 8949 account rows are
present, each Schedule D digital row must also equal their sum for its box, in
proceeds, cost, adjustment, gain and broker figure, to the cent: the totals
alone cannot see column (d) and gain moved from a short-term row to a
long-term one, or a $0 loss merged twice. A stale import whose own figures
still agree with each other is the one case these checks cannot see; only a
rebuild from the current forms shows it. A non-zero
no-form row keeps the tie verifiable, so
a form wrongly declared "not received" fails it instead of warning.

### Linked partial fills (AA47)

One broker sale can cover several imported fill IDs. Link them explicitly in
the optional `broker_reporting.fill_groups` list, then set the form's `txn_id`
to that group's `group_id`:

```json
{"account_id": "exchange-A", "group_id": "order-1",
 "txn_ids": ["fill-1", "fill-2"], "evidence": "single-sale-confirmation"}
```

The evidence must establish that the listed fills belong to that one reported
sale. Never infer a group from similar amounts or nearby timestamps. Each
member must be a filing-year disposal in the same account, of the same asset,
on the same calendar day. At least two distinct IDs are required. Groups
cannot overlap, reuse a transaction's ID as their own ID, or contradict a
member's `not_reported` declaration. Every group needs a broker form; a member
cannot also have a separate direct form. A holding-period split uses the
existing separate-form rules, with each form naming the same group.

FIFO still matches each original fill in chronological order before reporting
groups are collected. Intervening purchases and transfers keep their original
position. Output rows retain their own transaction IDs, dates and FIFO basis;
the form workpaper names the group and `fill_groups` retains its membership
and evidence. Fees are summed once for the group's reconciliation. Units and
amounts must reconcile under the existing exact checks, with one exact
exception: each fill rounds to cents on its own, while a broker reporting the
order rounds it once. A single form whose proceeds equal the fills' exact
total, net of fees, rounded once takes the form's figure, the difference
landing on the largest row, when every fill's rows share one holding period;
moving those cents between short- and long-term rows would move gain between
parts, so a mixed-term group, and any other difference, stays unresolved. Group-level
`resolutions` are refused: spreading an adjustment across differently priced
fills could move gain between holding periods. Correct the underlying records
from evidence or prepare that case externally.

This handles linked partial fills only. An export's lost precision or wrong
USD value goes through [reviewed source corrections](#reviewed-source-corrections-aa47).
Optional annual stablecoin/NFT reports, manual special treatments and
cross-year date corrections remain AA47 follow-ups. An optional-method box
still refuses; this grouping cannot clear an unreviewed reporting year.

### Manual disposals (AA47)

A broker form that fills a box this engine does not model refuses the whole
import, though only its own disposal needs the special treatment. List that
disposal instead in the optional `broker_reporting.manual_disposals`:

```json
{"account_id": "exchange-A", "txn_id": "sale-7", "evidence": "ordinary-gain-workpaper"}
```

Each entry names a filing-year disposal (or a fill group's `group_id`), and at
least one of its forms must fill an unmodeled box: the path never stands in
for reconciling a form the engine can model. Three boxes refuse it, because
their treatment reaches beyond the one disposal: a reported wash sale
(§1091(d) moves the disallowed loss into a replacement lot's basis), a CUSIP
(a tokenized security, whose other sales face the wash-sale rules too), and
box 11c's first-sale NFT proceeds (a ledger category tie-out blocks on by
itself); prepare such an import externally. FIFO still consumes the
disposal's lots, so later sales and inventory keep their FIFO basis. Its rows leave the
import's Form 8949 rows, its Schedule D totals and its box routing, and
`broker_reporting.manual_disposals` records its form ids, the unmodeled boxes,
the forms' proceeds and the FIFO slices to start the preparation from. The
output opens with a `MANUAL` caveat, and `crypto_summary.manual_disposals`
lists the disposals.

Prepare each by hand under the form's own instructions, then merge its Form
8949 rows with their `account_id`, `txn_id`, `box` and `broker_proceeds`
(their share of the form's proceeds), adding the same amounts into each box's
Schedule D digital row. `crypto_summary.manual_disposals` keeps each
disposal's forms' proceeds, and tie-out fails until the merged rows' broker
figures add to exactly that, whether or not the parse ledger still holds the
form: one slice of a two-lot sale is not the sale. An option-reviewed import cannot
also leave a disposal manual, since its tie-out rebuilds every account row from
the source.

### Reviewed Form 1099-B inputs (AA61)

Live review on 2026-10-24 compared every box of the
[TY2023 form](https://www.irs.gov/pub/irs-prior/f1099b--2023.pdf) with its
[TY2023 instructions](https://www.irs.gov/pub/irs-prior/i1099b--2023.pdf), and the
[TY2024 form](https://www.irs.gov/pub/irs-prior/f1099b--2024.pdf) with its
[TY2024 instructions](https://www.irs.gov/pub/irs-prior/i1099b--2024.pdf).
The numbered meanings agree; box 9 names the preceding year-end and box 10
the covered year-end. This is the spot-crypto import contract, not a general
securities or barter importer. Required unsupported keys must be copied as
null, false or 0 when blank; a filled value refuses the import, including a
negative contract amount, unless its disposal is a
[manual disposal](#manual-disposals-aa47). An older summary cannot establish those blanks.

| Input key | TY2023 box | TY2024 box | Reviewed meaning and treatment |
|---|---|---|---|
| `asset` | 1a | 1a | Match the described property to the imported symbol. |
| `quantity` | 1a | 1a | Units within the description; reconcile to the disposal. |
| `date_sold` | 1c | 1c | Broker sale date; reconcile under the existing date rule. |
| `proceeds` | 1d | 1d | Reported sale amount; reconcile to the imported proceeds. |
| `cost_basis` | 1e | 1e | Reported basis, null when blank; never invent zero. |
| `accrued_market_discount` | 1f | 1f | Debt adjustment; nonzero refuses. |
| `wash_sale_loss_disallowed` | 1g | 1g | Broker loss adjustment; nonzero refuses. |
| `term` | 2 | 2 | Broker holding-period classification; reconcile against FIFO. |
| `ordinary` | 2 | 2 | Ordinary treatment; checked refuses. |
| `collectibles` | 3 | 3 | Collectible treatment is outside the capital-gain pricing here; checked refuses. |
| `qof` | 3 | 3 | Opportunity-fund disposition; checked refuses. |
| `net_proceeds` | 6 | 6 | Option-premium reduction; checked refuses. Gross has no key: it cannot establish that no option affected basis or proceeds. The option-exercise capability screen still applies. |
| `loss_not_allowed` | 7 | 7 | Broker-disallowed loss; checked refuses. |
| `contract_profit_loss` | 8 | 8 | Closed-contract result; nonzero refuses. |
| `prior_unrealized_profit_loss` | 9 | 9 | Prior year-end open-contract result; nonzero refuses. |
| `current_unrealized_profit_loss` | 10 | 10 | Current year-end open-contract result; nonzero refuses. |
| `aggregate_contract_profit_loss` | 11 | 11 | Annual contract result; nonzero refuses. Boxes 8-11 never become disposal proceeds. |
| `basis_reported_to_irs` | 12 | 12 | Actual basis-reporting flag, independent of covered status. |
| `bartering` | 13 | 13 | Barter-exchange receipts; nonzero refuses. |
| `cusip` | CUSIP | CUSIP | Security identifier; filled refuses because this importer cannot clear the associated securities treatment. |
| `federal_withholding_1099` (parse ledger only) | 4 | 4 | Backup withholding belongs to the parse ledger. |
| `state_withholding_nc` and the other state categories (parse ledger only) | 16 | 16 | Use box 14's state: `ca_1099_withholding`, `state_withholding_ny`, `state_withholding_nc`, or `other_state_withholding` with `other_state_withholding_by_state`. Each also reaches Schedule A once. |
| — | 1b | 1b | Acquisition date; does not replace the original FIFO lot date. |
| — | 5 | 5 | Noncovered status; box 12 answers whether basis was reported. |
| — | 14 | 14 | State selecting the withholding route. |
| — | 15 | 15 | Payer's state identifier, outside the import. |
| — | Applicable checkbox | Applicable checkbox | Broker routing code; basis reporting and the reconciled holding period determine the output box. |
| — | CORRECTED | CORRECTED | Use the replacement, never both versions. |
| — | Account number | Account number | Confirm `account_id`; do not copy the raw number. |
| — | FATCA | FATCA | Broker reporting metadata; taxpayer foreign-account and asset intake still applies. |
| — | Parties | Parties | Identify the correct document; never copy a full TIN. |
| — | 2nd TIN not. | 2nd TIN not. | Administrative notice marker on Copy A; no pricing effect. |
| — | VOID | VOID | A void Copy A is not a reportable customer transaction. |

This review covers TY2023-2024 only. The 1099-DA contract and the refusal of
unreviewed reporting years remain as documented below. No manual-disposal
route is added here; AA47 tracks that extension.

### Reviewed Form 1099-DA inputs (AA55)

Every box was compared by live, cache-bypassing IRS HTTPS fetches: the
numbered boxes on 2026-10-15 against the year-named [TY2025 instructions, pages 7-10](https://www.irs.gov/pub/irs-prior/i1099da--2025.pdf)
and [TY2026 instructions, pages 7-10](https://www.irs.gov/pub/irs-prior/i1099da--2026.pdf),
and the unnumbered ones on 2026-10-16 against the recipient's Copy B of the
[TY2025 form](https://www.irs.gov/pub/irs-prior/f1099da--2025.pdf) and the
[TY2026 form](https://www.irs.gov/pub/irs-prior/f1099da--2026.pdf).
Each box keeps its number and meaning in both years, so the normalized input
contract stays shared between them. TY2026 splits the filer's and recipient's
addresses into separate fields, Country among them, which changes no input.
Two labels change without changing their
input: box 1e's from "Date Sold" to "Date Sold or Disposed", still the sale
date, and box 1i's from "Wash Sales Loss Disallowed" to "Wash Sale Loss
Disallowed". Box 4 now cites Notice 2025-33 for transitional backup-withholding
relief, and the box 1g and box 9 changes are below. A later year's box change
needs a per-year input map, preserving TY2025.

The table has a row for every box, numbered or not, keyed or not. Each says
how the box is copied, that a filled box refuses the import (unless its
disposal is a [manual disposal](#manual-disposals-aa47)), or why a box with no
key cannot change the row.

| Input key | TY2025 box | TY2026 box | Reviewed meaning and treatment |
|---|---|---|---|
| `asset` | 1b | 1b | Not copied: the import's own symbol for the asset box 1b names, "BTC" for "Bitcoin". The importer compares it exactly with the disposal's, and no resolution clears a mismatch. |
| `quantity` | 1c | 1c | Units sold, exchanged or otherwise disposed of. |
| `date_sold` | 1e | 1e | Sale date, normalized to YYYY-MM-DD. |
| `proceeds` | 1f | 1f | Proceeds after the broker's reduction for transaction costs; preserve the form's amount for reconciliation. The parse ledger also records its total as `capital_proceeds`. |
| `cost_basis` | 1g | 1g | Adjusted basis; a blank is null, while an actual zero basis is zero. |
| `basis_reported_to_irs` | 2 | 2 | Whether basis was reported to the IRS; copy the checkbox even when basis is shown. |
| `term` | 6 | 6 | Short-term or long-term; null when blank. |
| `ordinary` | 6 | 6 | Ordinary gain/loss checkbox; filled in, refuses the import. |
| `accrued_market_discount` | 1h | 1h | Accrued market discount for covered digital assets that are debt instruments; a nonzero amount refuses the import. |
| `wash_sale_loss_disallowed` | 1i | 1i | Disallowed wash-sale loss for tokenized stock or securities; a nonzero amount refuses the import. |
| `loss_not_allowed` | 5 | 5 | Loss not allowed based on box 1f; checked, refuses the import. |
| `net_proceeds` | 3a | 3a | "Net proceeds" checked: box 1f was reduced by option premiums. Refuses without a source-bound purchased-put exercise under the reviewed option-premium path below; a blank 3a is false. "Gross proceeds" is no key, because it cannot clear a sale either way. The instructions complete box 3a "if the digital assets were sold because of the exercise of an option" yet leave it off the boxes "completed only if applicable", and box 1f says "Otherwise, check 'Gross proceeds' in box 3a", so a broker may check it on every sale; and on an option's sale it means the premium was NOT netted, which §1.6045-1(m) does not require for an option granted or acquired before 2026, or held outside the broker's account. Refusing it would refuse ordinary forms, and accepting it as "no option" would pass that unadjusted sale. An option exercise affecting a disposal (sale, exchange or spending), through its proceeds or the lot's basis, requires the capability review (`digital_asset_option_exercise`), whichever box shows. |
| `qof` | 3b | 3b | Disposition of an interest in a qualified opportunity fund; checked, refuses the import, since gain previously deferred into the fund needs its own code-Y row and Form 8997. |
| `customer_acquisition_info` | 8 | 8 | The broker took customer-provided acquisition information into account when identifying units (§1.6045-1(d)(2)(ii)(B)(4)). Copy a boolean or null, retaining the field when blank. It can supply dates for FIFO or support specific identification; the form cannot establish the method. A checked box is accepted only after the import's explicit FIFO confirmation. Specific identification at any broker or wallet blocks FIFO pricing, whether this box is checked or blank. |
| `optional_method` | 11a | 11a | Qualifying stablecoins or specified NFTs reported under an optional aggregate method; either box checked refuses the import, since an aggregate is not one disposal's figures (AA47). |
| `primary_sale_proceeds` | 11c | 11c | Specified NFTs' first-sale proceeds, kept out of box 1f; a nonzero amount refuses the import. `/tax-parse` also records it as `nft_first_sale_proceeds`, which blocks the tie-out gate, so a form that never reaches `broker_reporting` cannot lose it. |
| `cusip` | CUSIP | CUSIP | CUSIP or other identifying number of a tokenized security, which the wash-sale rules reach (box 1i) while the import models none; filled in, refuses the import. A tokenized security with no applicable number leaves it blank and passes; box 1i still refuses a wash sale the broker reports. |
| `federal_withholding_1099` (parse ledger only) | 4 | 4 | Backup withholding; handled by `/tax-parse`, outside `broker_reporting.forms`. |
| `state_withholding_nc` and the other state categories (parse ledger only) | 16 | 16 | State income tax withheld, recorded by `/tax-parse` per box 14's state. When this run computes that state's return: `ca_1099_withholding` for CA, `state_withholding_ny` for NY, or `state_withholding_nc` for NC. Otherwise `other_state_withholding` with `other_state_withholding_by_state`, an advisory to claim it on that state's own return. A computed state hidden in that breakdown fails. Each amount also counts once toward Schedule A line 5a. Outside `broker_reporting.forms`. |
| — | 1a | 1a | Code for the digital asset; with box 1b, confirms which asset the `asset` symbol stands for, and is not a key. |
| — | 1d | 1d | Date acquired; never replaces an original acquisition date in the FIFO inventory. |
| — | 7 | 7 | Only cash proceeds were paid. Not read: proceeds paid in property are its value, which the import prices for trades too, and the amounts are compared either way. |
| — | 9 | 9 | Noncovered security; not read, since box 2 answers whether basis was reported (below). |
| — | 10 | 10 | Reserved. |
| — | 11b | 11b | Number of transactions under an optional method; completed only with box 11a, which refuses. |
| — | 12a | 12a | Units transferred into the account. Not read: transfers come from the reviewed transfer links, and the original lot keeps its dates and basis. |
| — | 12b | 12b | Transfer-in date; not read, as box 12a. |
| — | 13 | 13 | Reserved. |
| — | 14 | 14 | State; picks box 16's ledger category. |
| — | 15 | 15 | The filer's state identification number; with box 14, identifies box 16's withholding. |
| — | CORRECTED | CORRECTED | Checked when the form replaces one the broker issued earlier; copy the corrected form instead of the original, never both. |
| — | Filer, recipient | Filer, recipient | Names, addresses, TINs and the filer's telephone; they identify the parties, not the sale. Never copy the recipient's TIN (AGENTS.md §1). |
| — | Account number | Account number | The broker's number for the customer's account, not a wallet address; helps confirm the form's `account_id`, and is not a key. |
| — | Applicable checkbox | Applicable checkbox | The broker's Form 8949 code: G, H, J or K, or Y when it cannot tell box H from box K. Not read: box 2 and the FIFO holding period choose the box. This Y is not Form 8949's own code Y (box 3b). |
| — | Reserved | Reserved | Reserved for future use, beside the CUSIP box. |

Which keys a `broker_reporting.forms` entry needs, and how a blank is copied,
is the forms rule above, with each 1099-DA key's box in this table; neither
adds a manual-disposal path (AA47). `form_id`, `form_type`, `account_id` and `txn_id`
identify and link the reviewed record, rather than copying numbered boxes.
TY2026's box 1g instructions explicitly clarify that brokers may use
customer-provided acquisition information for lot selection, but not for
reporting basis or acquisition dates. Keep the form's figures separate from
the reviewed original lot records; any difference still needs reconciliation.

The TY2026 instructions' General Instructions and Information Required sections
(pages 1 and 3-4) require basis reporting for covered digital assets, while
basis reporting for noncovered assets remains voluntary. Box 9 alone does not
answer whether basis was reported: a noncovered form may still report basis in
1g and check box 2, which TY2026's box 9 text now names among the boxes a broker
may complete voluntarily. That text also ends, for sales after 2025, the penalty
relief for basis given without box 9 checked; the box 2 input stays the same.
Neither a shown basis nor a year-wide assumption replaces
the explicit box-2 input. Blank basis and term remain possible for noncovered
assets; optional aggregate reporting is still outside the per-disposal matching
contract. Box 1i still concerns tokenized securities, without applying wash-sale
rules to ordinary cryptocurrency.

This review establishes the TY2026 **input-box** meanings only. It does not add
TY2026 to `_REPORTING_REGIMES`: AA48 still needs that year's Form 8949
instructions, including the no-form boxes. Form 1099-DA's own Form 8949 code
list cannot substitute for that review. The existing TY2026 refusal remains.

## Reviewed source corrections (AA47)

If an export lost unit precision or contains an incorrect USD value, obtain
the full source figure and the taxpayer's confirmation before changing it.
Record that fact in the optional `broker_reporting.source_corrections` list:

```json
{"account_id": "exchange-A", "txn_id": "sale-1",
 "expected": {"date": "2025-03-01", "asset": "BTC", "kind": "sell", "usd_amount": 150},
 "corrected": {"usd_amount": 150.01}, "evidence": "reviewed-trade-confirmation"}
```

Each entry requires exactly those five keys. `expected` copies the original
date, asset, kind and every financial field being corrected. `corrected`
changes `quantity` and/or `usd_amount`, retaining full decimal precision
where the source supplies it: a corrected `quantity` is a decimal string, since
a JSON number reaches the engine already rounded. A `date` change requires the
separate calendar review below. Account and transaction IDs must be explicit and
unique. Missing, stale, repeated, unchanged, nonfinite or unsupported changes
refuse. A transfer does not use `usd_amount`; its basis and ownership evidence
must be prepared through the existing transfer path.

This replaces a source fact before FIFO and recomputes inventory, acquisition
basis and disposal gain; it is not a broker-form adjustment or rounding
tolerance. The engine checks the original values and context before applying
copies, leaves the source array and export intact, and retains the complete
correction record in its JSON workpaper. Broker units, proceeds, basis and
holding periods must still reconcile afterward. A difference alone cannot
establish that the export or broker is wrong. Never fill the correction with
the broker's figure solely to make reconciliation pass.

A correction can reach an earlier year: the corrected event is an earlier
sale, or an acquisition an earlier sale consumed. The output then opens with a
`PRIOR-YEAR RETURN` caveat naming each such year, whose filed return used the
uncorrected history. Review it for Form 1040-X through `/tax-amend`, so both
years carry the same basis.

Review and preserve `source_corrections` with the import's secured workpaper in
`summaries/`. Account identities, fees, lot selection and special form
treatments are outside this correction path. It does not clear the option
exercise screen or unsupported boxes. The separate option review below handles
plain USD premiums; other manual treatments and optional annual reporting
remain AA47 follow-ups.
Corrections live inside the broker-review object so an older engine's closed
key set refuses them instead of silently ignoring a new top-level input.

### Reviewed filing dates

An import and a broker can put the same instant on opposite sides of year-end.
The [TY2025 Form 8949 instructions, column (c)](https://www.irs.gov/pub/irs-prior/i8949--2025.pdf)
ask for the disposition date; a broker date alone does not establish the
filing calendar. Obtain an externally reviewed workpaper establishing the
correct dates for this return, including every account's acquisitions,
disposals and income, and original acquisition dates carried by transfers.
Record the taxpayer's confirmation and evidence.

Under `broker_reporting`, supply that review and the precise source correction:

```json
{"tax_calendar_review": {"calendar_label": "reviewed-filing-calendar",
  "all_account_dates_reviewed": true, "evidence": "all-account-tax-date-workpaper"},
 "source_corrections": [{"account_id": "exchange-A", "txn_id": "sale-1",
  "expected": {"date": "2026-01-01T01:00:00+08:00", "asset": "BTC", "kind": "sell"},
  "corrected": {"date": "2025-12-31T17:00:00Z"}, "evidence": "reviewed-sale-timestamp"}]}
```

The label identifies the workpaper's calendar; the engine never derives a
timezone or chooses a year from it. Original and corrected timestamps must
represent exactly the same UTC instant, with a calendar date one day apart.
The instant check retains fractional-second digits beyond microseconds.
The corrected timestamp must have an explicit offset. An original timestamp
without an offset requires documented confirmation that its source used UTC,
recorded as the additional boolean `naive_timestamps_are_utc: true` in the
review. A date without a time cannot establish an instant; never invent
midnight or choose a timestamp merely to match a form or obtain a preferred year.

FIFO still orders the actual instants. The reviewed date also controls
acquisition holding periods, income and filing-year selection. Reconcile the
original acquisition dates as well: as on every import, a known acquisition
date after its disposal refuses, and missing inventory still blocks. An unused
calendar review, a changed instant, an unconfirmed UTC assumption, an offset
outside -12:00 to +14:00 or an unreviewed form year refuses. Preserve
`tax_calendar_review` and the before/after `source_corrections` with the
secured import workpaper. Source arrays and exports remain unchanged.

A correction re-reads one record in another calendar, and a row's two dates
can then come from different calendars. Within three days of the one-year line
that can flip a sale between short and long term, so a corrected row there
must have both dates hold in one calendar: re-read in the offset of either
end, each end's instant must give the date the row uses. A purchase at noon
in +08:00 is the same day in UTC and needs nothing; one that is not must be
corrected too. A bare date or an unconfirmed naive time has no instant to
re-read, so such a row refuses there; prepare it externally.

A correction may move a disposal or income event back a year, the AA47 case:
the import dates a sale January 1, the broker December 31. The output then
opens with a `YEAR MOVE` caveat naming both years. Every import covering
either year must carry the same review and corrections, or the later year
reports the event again; until AA48 adds TY2026, a TY2026 import cannot carry
`broker_reporting` at all, so record the move in `answers.md`. A move into a
later year refuses outright: the earlier import dates the event in its own
year, so the event would be reported twice, or not at all if that import
carried the correction. Report it in the year its import dates it, or prepare
that case externally.

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


## Reviewed option premiums (AA47)

The exercise rules in Pub. 550 Table 4-3 were reviewed for
[TY2023](https://www.irs.gov/pub/irs-prior/p550--2023.pdf),
[TY2024](https://www.irs.gov/pub/irs-prior/p550--2024.pdf) and
[TY2025](https://www.irs.gov/pub/irs-prior/p550--2025.pdf). The input below
handles an externally confirmed plain USD premium on the underlying asset:

| `treatment` | Source event | Effect before FIFO |
|---|---|---|
| `purchased_call` | `buy` | Add the call's cost to acquisition basis. |
| `written_put` | `buy` | Reduce acquisition basis by the premium received. |
| `written_call` | `sell` | Add the premium received to proceeds. |
| `purchased_put` | `sell` | Reduce proceeds by the put's cost. |

Obtain an external workpaper establishing that this treatment applies to every
option-affected source event, including prior-year acquisitions supplying any
current or remaining lot. It must review compensation, section 1256, straddles,
short sales, holding-period changes, deferred losses, non-USD premiums and
other special treatments. This path requires that none applies; a nonempty
`unsupported_items` list refuses. Do not infer that scope from a broker checkbox.
The engine does not classify an option, price option contracts themselves, or
allocate one exercise's premium across source events. Normalize the original
exercise confirmation into one event with its reviewed whole-cent premium.
Cash premium receipts are not separate digital-asset ordinary-income receipts.

Put this object inside `broker_reporting`:

```json
{"option_exercise_review": {
  "scope": "plain_usd_premiums_no_other_tax_effects",
  "all_accounts_and_prior_lots_reviewed": true,
  "unsupported_items": [], "evidence": "option-scope-workpaper",
  "events": [{"account_id": "exchange-A", "txn_id": "exercise-buy",
    "expected": {"date": "2024-07-01", "asset": "BTC", "kind": "buy",
      "quantity": "1", "usd_amount": "100", "fee_usd": "0"},
    "treatment": "purchased_call", "option_opened": "2024-06-01",
    "premium_usd": "10", "source_includes_premium": false,
    "fees_exclude_premium": true, "evidence": "exercise-confirmation"}]
}}
```

Every key is required. Copy `expected` from the normalized source after any
separately reviewed source/date corrections; use zero for an absent `fee_usd`.
Context, exact quantities, USD values and fees must agree. A repeated event,
stale snapshot, unsupported kind, missing evidence or negative adjusted amount
refuses. `source_includes_premium` must explicitly establish whether the source
USD amount already incorporates that premium; true preserves that amount.
`fees_exclude_premium: true` separately confirms the premium is not charged
again in `fee_usd`. Preserve the source export and record both answers with
their evidence. Do not change an inclusion answer to clear a mismatch.

The adjustment precedes FIFO across all years. Acquired basis follows partial
sales, spending, exchanges, transfers and remaining inventory. An option-caused
disposal itself must be a direct USD `sell`; a `trade` also creates a new asset's
basis and is outside this path. A purchased put additionally requires every
sold lot to have been held long-term already on `option_opened`, and no lot of
the same asset in any account, a lot sent to the taxpayer's own outside
wallet included, to be held one year or less on that date or bought after it
and no later than the exercise. Pub. 550's married-put rule
makes such holdings short-term and restarts their holding period, which the
engine does not model, so the import refuses and that case is prepared
externally. Opening inventory with
an externally supplied basis still needs the original exercise and transfer
history to use this review path.

Broker forms, lot selection and missing-inventory checks remain in force.
A direct purchased-put sale can reconcile a form's `net_proceeds: true`.
That form cannot deduct the put cost again through code E. A gross-proceeds
form can use the evidenced put cost plus separately recorded commissions to
explain code E; the existing resolution and evidence are still required.
A written call's premium raises the amount realized above a gross form's
proceeds. The Form 8949 instructions (TY2023-2025) put the form's figure in
column (d) and an option premium the form does not reflect in (g) under code
E, so `proceeds: option_premium` does that, for no more than the reviewed
premium; `taxpayer_records` still enters the higher figure in (d) instead.
Other unsupported filled boxes refuse. Grouped option-caused disposals refuse
pending external premium allocation; ordinary later sales from an
option-acquired lot may use reviewed fill groups.

Keep `crypto_summary.option_exercise_review` with the merged summary and save
the complete JSON workpaper in secured `summaries/`. Keep normalized inputs in
`crypto_transactions.json`. Tie-out rebuilds that source, verifies the review,
compares each account-tagged Form 8949 row, and checks each Schedule D digital
row's part, amounts and adjustment codes at exact cents. A true option flag
fails without this evidence, or when no reviewed event is a filing-year row's
own sale or the acquisition its lot came from: a review naming only a lot sold
in an earlier year cannot price this year's sale. A false flag fails only when
a reviewed event does reach a filing-year row; a review kept to price held
inventory does not contradict it. A reviewed premium that changes an earlier
year's sale, directly or through its lot, opens the import's output with the
same `PRIOR-YEAR RETURN` caveat as a source correction.
The validation receipt binds the normalized source so later edits require a
new tie-out. The default capability block still requires explicit
acknowledgement and its decision in secured `answers.md`; it never substitutes
for the review. TY2026 remains unreviewed for both routing and option pricing.
