# Crypto Importer — Design Spec

**Status:** Draft, autonomously approved (user: "implement #8; autoapprove all and execute")
**Date:** 2026-05-31
**Author:** Implementation session — TY2026 planning backlog item #8 (the LAST backlog item)
**Roadmap source:** `docs/roadmap/2026-tax-planning-features.md` (item #8 of 12)
**Builds on:** the `/tax-parse` document-parsing pattern (subagent dispatch, summaries/), the
`derive_holding_period` primitive + strict validators from `agents/lib/loss_harvest.py` (#4,
PR #18), and the `form_8949` / `schedule_d` blocks in `result_template.json`.

## Purpose

Crypto exchange activity is the single most error-prone input on a modern return. The IRS
treats virtual currency as **property** (Notice 2014-21), so *every* disposal is a capital
event — including the two that taxpayers routinely miss:

1. **Crypto-to-crypto trades.** Swapping ETH for SOL is a taxable disposal of the ETH at its
   fair market value, even though no US dollars changed hands. This is the #1 omitted event.
2. **Income receipts.** Staking rewards, interest, mining, and airdrops are **ordinary
   income** at FMV on receipt — *not* capital gain — and that FMV becomes the coin's cost
   basis for a later disposal.

A second trap is structural: because crypto is property and **not** "stock or securities,"
the **wash-sale rule (§1091) does NOT apply** to it. This is the exact inverse of
`/tax-harvest` (#4), which spends 200 lines enforcing §1091 on equities. The crypto importer
must *loudly* state this so a user who just ran `/tax-harvest` does not assume the same rule.

Finally, every disposal or income receipt makes the **Form 1040 page-1 digital-asset
question** a mandatory **"Yes."** Buying and merely *holding* crypto with USD is a **"No."**
Getting this checkbox wrong is a perjury-attestation error independent of the dollar math.

`/tax-parse-crypto` ingests Coinbase / Kraken / Gemini transaction CSVs, computes per-disposal
capital gains via FIFO lot matching, separates ordinary-income receipts, and emits Form 8949
rows + Schedule D totals + the digital-asset answer for `/tax-compute` to consume.

## Scope

### In scope

- New `agents/lib/crypto_importer.py` — pure-compute lib + CLI, mirroring the lib discipline of
  `loss_harvest.py` (frozen dataclasses, strict numeric validators that reject bool/NaN/Inf,
  workspace-bounded I/O, a `__main__` self-test + subcommands).
  - **Exchange CSV adapters** for Coinbase, Kraken, and Gemini — each maps that exchange's
    raw columns + transaction-type vocabulary into one **normalized transaction** model.
    Deterministic and fixture-testable (the project's standard), unlike free-form subagent
    parsing. An explicit `--exchange` selects the adapter; unknown formats fall back to the
    normalized-JSON entry path (subagent-produced).
  - **Normalized transaction → primitive events** expansion: each transaction becomes some
    combination of `Acquisition`, `Disposal`, and `IncomeEvent`.
  - **FIFO lot engine** (`match_lots`) matching disposals against acquisition lots per asset,
    producing Form 8949 rows with proceeds, allocated cost basis, gain/loss, and short/long
    holding period (reusing `derive_holding_period`).
  - **Aggregator** (`build_crypto_import`) → `form_8949` (ST + LT rows), `schedule_d` totals,
    `ordinary_income` total + per-event list, `digital_asset_question`, and a prominent
    `needs_basis` list for disposals whose acquisition could not be located.
- New `/tax-parse-crypto` skill (`agents/skills/tax-parse-crypto.md`) — orchestration:
  scan `documents/` for exchange CSVs, dispatch security-constrained normalization subagents,
  run the lib, merge results into `result.json`, set `flags.has_crypto` +
  `digital_asset_question`.
- `result_template.json` additions — a `digital_asset_question` field on `form_1040` and a
  `crypto_summary` block (per-asset realized gain, ordinary income, needs-basis count).
- Test suite: `tests/test_crypto_importer.py` + `tests/fixtures/crypto/` (one CSV per exchange
  + a normalized-JSON fixture + a workspace triple).
- Docs: CLAUDE.md skill entry + lib entry + testing line; roadmap progress note; MEMORY.md
  index line; `install.sh` (skill auto-symlinks — verify no change needed).

### Out of scope (deferred / caveated, not silently wrong)

- **Cost-basis methods other than FIFO.** v1 is FIFO-only — the IRS default when a taxpayer
  has not made a contemporaneous specific-identification election (Treas. Reg. §1.1012-1(j)).
  HIFO/LIFO/specific-ID can *reduce* gains, so choosing them silently would risk **under-
  reporting** tax → forbidden by the bias-direction taxonomy. Caveated as a v1.1 follow-up.
- **Per-wallet/per-account basis segregation (Rev. Proc. 2024-28).** From 2025, universal
  (cross-wallet) basis is no longer permitted — basis must be tracked per account. v1 pools
  all provided transactions into **one per-asset FIFO queue** (universal). This is a
  *simplification*, loudly caveated; the user segregates by running the importer per account
  if they need strict per-wallet treatment. (Direction is not systematically biased; flagged.)
- **§1091 wash-sale on crypto.** Deliberately NOT applied — crypto is property. Headline caveat.
- **NFTs / collectible 28% rate** — not modeled (the §408(m) collectible question for certain
  NFTs is unsettled). Flagged.
- **DeFi / liquidity-pool / wrapping events, margin/futures (§1256), lending** — not modeled.
- **Form 1099-DA reconciliation** — the new broker form (TY2025+) is not cross-checked in v1;
  Box C/F ("no 1099-B / 1099-DA basis reported") is the default Form 8949 box. Caveated.
- **State tax** — federal Form 8949 only, consistent with the parse → compute split (state cap-
  gains treatment is handled downstream by `/tax-compute`).
- **Self-employment / mining-as-a-business** — income receipts default to Schedule 1 line 8
  (other income). Mining/staking conducted as a trade or business (Schedule C + SE tax) is
  flagged for the user to route manually; v1 does not auto-classify.

## Core tax model

### Normalized transaction → primitive events

| Normalized `kind` | Meaning | Expands to |
|---|---|---|
| `buy`   | USD → crypto | `Acquisition(asset, qty, basis = usd + fee, date)` |
| `sell`  | crypto → USD | `Disposal(asset, qty, proceeds = usd − fee, date)` |
| `trade` | crypto A → crypto B | `Disposal(A, sold_qty, proceeds = usd_value − fee)` **and** `Acquisition(B, recv_qty, basis = usd_value, date)` |
| `income`| staking/reward/interest/mining/airdrop | `IncomeEvent(asset, fmv = usd, date)` **and** `Acquisition(asset, qty, basis = usd, date)` |
| `spend` | crypto → goods/services | `Disposal(asset, qty, proceeds = usd − fee)` (FMV of goods = usd) |
| `receive` | transfer IN (not a purchase) | `Acquisition(asset, qty, basis = provided basis, date)`; if basis absent → basis 0 + `needs_basis` |
| `send`  | transfer OUT to own wallet | **no taxable event** (v1 ignores for the lot engine; caveat: ensure the matching receive on the other side is not double-counted) |

A `trade`'s `usd_value` is the FMV of the legs at the moment of the swap (proceeds of the sold
side = basis of the bought side). Fees reduce proceeds on disposals and add to basis on
acquisitions.

### FIFO lot engine

1. Collect all `Acquisition`s and `Disposal`s; sort each by date (stable).
2. Per asset, maintain a FIFO queue of open lots `(qty_remaining, basis_per_unit, acquired)`.
3. For each disposal (date order), consume lots oldest-first. Each lot slice produces one
   Form 8949 row: proceeds allocated `proceeds × slice_qty/disposal_qty`, cost basis allocated
   `slice_qty × basis_per_unit`, gain/loss = proceeds − basis, holding period from
   `derive_holding_period(acquired, sold)`.
4. **Insufficient basis** (disposal qty exceeds available lots): the unmatched units form a
   row with `cost_basis = 0`, `needs_basis = True`, and the disposal is added to the
   prominently-surfaced `needs_basis` list. A $0 basis *overstates* the gain → the SAFE
   (over-reporting) direction per the bias taxonomy → banner + degrade rather than hard error
   (a hard error would block the whole import on a single missing transfer-in record).

### Holding period & boxes

- Short-term iff `(sold − acquired).days ≤ 365` (strictly-more-than-1-year is long-term —
  identical rule to `/tax-harvest`, reusing `derive_holding_period`).
- Default Form 8949 box: **C** (short-term) / **F** (long-term) — "transaction not reported on
  a 1099-B." Most exchanges historically issued no 1099-B; 1099-DA (TY2025+) reconciliation is
  a v1.1 item. Box is a field on each row so the skill/user can override.

### Digital-asset question

`"Yes"` if there is **any** `Disposal` or **any** `IncomeEvent`; otherwise `"No"`. A file of
only `buy`/`receive`/`send` (acquire-and-hold, no disposals, no rewards) yields `"No"`.

## Key design decisions (bias-direction taxonomy applied)

- **FIFO default, not HIFO.** HIFO minimizes gains and would *under-report* tax if chosen
  without a specific-ID election → hard-forbidden. FIFO is the IRS default → safe v1.
- **$0 basis for unmatched disposals.** Over-reports gain (safe direction) → banner + degrade,
  with every affected disposal listed so the user can supply the missing acquisition.
- **No wash-sale.** Applying §1091 would *understate* allowable gains/overstate disallowed
  losses incorrectly for property → wrong in both directions → simply not applied + caveat.
- **Income at FMV → ordinary, and sets basis.** Omitting the basis step would double-tax the
  reward on later sale (over-report) — handled correctly by emitting the paired `Acquisition`.
- **Strict input validation at the parse boundary.** All numeric fields go through validators
  that reject bool / NaN / Infinity / negative (NaN/Inf pass `isinstance` and defeat `<0`
  checks — the recurring project lesson). Unknown `kind`, unknown `--exchange`, malformed
  dates → clean `CryptoImportError`, never a raw traceback.

## CLI surface

```
python3 agents/lib/crypto_importer.py adapt <file.csv> --exchange coinbase   # CSV → normalized JSON
python3 agents/lib/crypto_importer.py compute <transactions.json>            # normalized → 8949/Sch D
python3 agents/lib/crypto_importer.py                                        # self-test
```

## Test plan

`tests/test_crypto_importer.py` covers: each exchange adapter (CSV → normalized), the
buy→sell FIFO round-trip (ST + LT split), crypto-to-crypto trade producing a disposal + a
new lot, income receipt → ordinary income + basis, partial-lot FIFO splitting across multiple
acquisitions, insufficient-basis → needs_basis + $0 cost, digital-asset Yes/No logic, fee
handling, strict-validation rejections (bool/NaN/Inf/negative/unknown kind/unknown exchange),
and the CLI end-to-end against fixtures. Target: keep the suite green and grow it materially.
