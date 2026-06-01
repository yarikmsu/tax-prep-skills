# Crypto Importer — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-31-crypto-importer-design.md`
**Backlog:** item #8 (`/tax-parse-crypto`), the last TY2026 backlog item.

## Build order (TDD — test first per unit)

1. **Scaffold `agents/lib/crypto_importer.py`**
   - `CryptoImportError`, constants (`SCHEMA`, reuse `LT_THRESHOLD_DAYS` via `derive_holding_period`).
   - Strict validators: `_req_num`, `_opt_num` (reject bool/NaN/Inf), `_req_str`, `_parse_date`.
   - Reuse `loss_harvest.derive_holding_period`.

2. **Normalized model + event expansion** (`CryptoTransaction`, `Acquisition`, `Disposal`,
   `IncomeEvent`, `expand_transaction`). Tests: each `kind` → correct primitives; unknown kind
   raises; trade two-leg; income paired acquisition; fee handling.

3. **FIFO lot engine** (`match_lots`) → `Form8949Row`s. Tests: buy→sell single lot ST/LT;
   partial split across two acquisitions; insufficient basis → needs_basis + $0; date ordering.

4. **Exchange adapters** (`adapt_coinbase`, `adapt_kraken`, `adapt_gemini`, dispatch
   `adapt_csv(rows, exchange)`). Tests: one fixture CSV per exchange → expected normalized txns.

5. **Aggregator** (`build_crypto_import`) → schedule_d totals, ordinary income, digital-asset
   Q, crypto_summary, needs_basis. Tests: end-to-end on normalized fixture; Yes/No logic.

6. **CLI** (`adapt`, `compute`, self-test). Tests: subprocess against fixtures.

7. **Templates**: add `form_1040.digital_asset_question` + `crypto_summary` block to
   `result_template.json`.

8. **Skill** `agents/skills/tax-parse-crypto.md`: prereqs, scan, subagent normalization
   (security constraints), run lib, merge into result.json, set flags, caveats, next-step.

9. **Docs**: CLAUDE.md (architecture diagram note, skills list, lib list, testing count),
   roadmap progress note, MEMORY.md index line.

10. **Verify**: full pytest green + lib self-test; update test-count references.

## Reuse map

| Need | Reuse from |
|---|---|
| holding period | `loss_harvest.derive_holding_period` |
| numeric coercion | `tax_computations._coerce_numeric` |
| json read / money | `projection._read_json`, `_money` |
| workspace-bounded path | `scenario._resolve_under` (for the CSV/JSON inputs) |
| year/subpath validation | `rollover._validate_year_arg`, `_validate_subpath` |

## Non-goals (caveated in output, see spec): non-FIFO methods, per-wallet basis, wash-sale,
NFTs/collectibles, DeFi, §1256, 1099-DA reconciliation, state tax, mining-as-business.
