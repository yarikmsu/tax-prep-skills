"""Tests for the crypto exchange importer — agents/lib/crypto_importer.py (backlog #8)."""
import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import crypto_importer as ci

FIX = Path(__file__).resolve().parent / "fixtures" / "crypto"
REPO = Path(__file__).resolve().parent.parent


def _csv(name):
    import csv
    with open(FIX / name, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def _txns(name="transactions.json"):
    return json.loads((FIX / name).read_text())["transactions"]


# --------------------------------------------------------------------------
# Strict validators
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), -float("inf")])
def test_num_rejects_bool_and_non_finite(bad):
    with pytest.raises(ci.CryptoImportError):
        ci._num(bad, "x")


def test_num_rejects_negative_by_default():
    with pytest.raises(ci.CryptoImportError):
        ci._num(-5, "x")
    assert ci._num(-5, "x", allow_negative=True) == -5.0


def test_num_parses_money_string():
    assert ci._num("$1,234.50", "x") == 1234.50


def test_parse_date_accepts_iso_timestamp():
    assert ci._parse_date("2024-03-01T14:05:00Z", "d") == datetime.date(2024, 3, 1)
    assert ci._parse_date("2024-03-01 14:05:00", "d") == datetime.date(2024, 3, 1)


def test_parse_date_rejects_garbage():
    with pytest.raises(ci.CryptoImportError):
        ci._parse_date("not-a-date", "d")


# --------------------------------------------------------------------------
# Transaction coercion
# --------------------------------------------------------------------------

def test_unknown_kind_rejected():
    with pytest.raises(ci.CryptoImportError):
        ci._coerce_transaction({"date": "2024-01-01", "kind": "wat", "asset": "BTC",
                                "quantity": 1, "usd_amount": 1}, 0)


def test_trade_requires_received_legs():
    with pytest.raises(ci.CryptoImportError):
        ci._coerce_transaction({"date": "2024-01-01", "kind": "trade", "asset": "ETH",
                                "quantity": 1, "usd_amount": 100}, 0)


@pytest.mark.parametrize("kind,extra", [
    ("sell", {"usd_amount": 1000}),
    ("spend", {"usd_amount": 1000}),
    ("trade", {"usd_amount": 1000, "received_asset": "SOL", "received_quantity": 5}),
    ("income", {"usd_amount": 1000, "income_type": "staking"}),
])
def test_zero_quantity_taxable_event_rejected(kind, extra):
    # [Codex P2 round 7] A 0-quantity taxable event would be silently dropped → reject.
    raw = {"date": "2024-01-01", "kind": kind, "asset": "BTC", "quantity": 0, **extra}
    with pytest.raises(ci.CryptoImportError):
        ci._coerce_transaction(raw, 0)


def test_zero_quantity_zero_cost_buy_or_receive_allowed():
    # A genuinely $0-cost, 0-qty buy/receive is a harmless no-op (no basis to lose).
    ci._coerce_transaction({"date": "2024-01-01", "kind": "buy", "asset": "BTC",
                            "quantity": 0, "usd_amount": 0}, 0)
    ci._coerce_transaction({"date": "2024-01-01", "kind": "receive", "asset": "BTC",
                            "quantity": 0}, 0)


@pytest.mark.parametrize("raw", [
    {"kind": "buy", "quantity": 0, "usd_amount": 100},                       # cost dropped
    {"kind": "buy", "quantity": 0, "usd_amount": 0, "fee_usd": 5},            # fee-only cost
    {"kind": "receive", "quantity": 0, "basis_usd": 100},                     # basis dropped
    {"kind": "trade", "quantity": 1, "usd_amount": 100,
     "received_asset": "SOL", "received_quantity": 0},                       # acquired leg basis dropped
])
def test_zero_quantity_acquisition_with_cost_rejected(raw):
    # [Codex P2 round 18] A 0-qty acquisition leg carrying cost/basis would silently drop it.
    raw = {"date": "2024-01-01", "asset": "BTC", **raw}
    with pytest.raises(ci.CryptoImportError):
        ci._coerce_transaction(raw, 0)


def test_send_needs_no_usd_amount():
    t = ci._coerce_transaction({"date": "2024-01-01", "kind": "send", "asset": "BTC",
                                "quantity": 0.1}, 0)
    assert t.kind == "send" and t.usd_amount == 0.0


# --------------------------------------------------------------------------
# Event expansion (the two crypto gotchas)
# --------------------------------------------------------------------------

def test_buy_expands_to_acquisition_with_fee_in_basis():
    t = ci._coerce_transaction({"date": "2024-01-01", "kind": "buy", "asset": "BTC",
                                "quantity": 1, "usd_amount": 20000, "fee_usd": 50}, 0)
    acq, dis, inc = ci.expand_transaction(t)
    assert len(acq) == 1 and not dis and not inc
    assert acq[0].cost_basis == 20050.0


def test_sell_expands_to_disposal_net_of_fee():
    t = ci._coerce_transaction({"date": "2024-01-01", "kind": "sell", "asset": "BTC",
                                "quantity": 1, "usd_amount": 25000, "fee_usd": 25}, 0)
    acq, dis, inc = ci.expand_transaction(t)
    assert dis[0].proceeds == 24975.0


def test_trade_is_disposal_plus_acquisition():
    t = ci._coerce_transaction({"date": "2024-09-01", "kind": "trade", "asset": "ETH",
                                "quantity": 2, "usd_amount": 5000, "received_asset": "SOL",
                                "received_quantity": 100}, 0)
    acq, dis, inc = ci.expand_transaction(t)
    assert dis[0].asset == "ETH" and dis[0].proceeds == 5000.0
    assert acq[0].asset == "SOL" and acq[0].cost_basis == 5000.0


def test_income_is_ordinary_plus_basis_setting_acquisition():
    t = ci._coerce_transaction({"date": "2024-03-01", "kind": "income", "asset": "ETH",
                                "quantity": 2, "usd_amount": 4000,
                                "income_type": "staking"}, 0)
    acq, dis, inc = ci.expand_transaction(t)
    assert inc[0].fmv == 4000.0 and inc[0].income_type == "staking"
    assert acq[0].cost_basis == 4000.0  # FMV becomes basis


def test_send_is_noop_in_universal_pool():
    # [Codex P2 round 10] Default (universal) pool: a confirmed self-transfer keeps basis in
    # the pool, so send expands to NO events.
    t = ci._coerce_transaction({"date": "2024-01-01", "kind": "send", "asset": "BTC",
                                "quantity": 0.1}, 0)
    assert ci.expand_transaction(t) == ([], [], [])


def test_send_is_nontaxable_disposal_in_per_account_mode():
    # [Codex P1 round 8] Per-account mode: send removes coins as a NON-taxable disposal.
    t = ci._coerce_transaction({"date": "2024-01-01", "kind": "send", "asset": "BTC",
                                "quantity": 0.1}, 0)
    acq, dis, inc = ci.expand_transaction(t, per_account=True)
    assert acq == [] and inc == []
    assert len(dis) == 1 and dis[0].taxable is False and dis[0].quantity == 0.1


# --------------------------------------------------------------------------
# FIFO lot engine
# --------------------------------------------------------------------------

def test_fifo_single_lot_long_term():
    acq = [ci.Acquisition("BTC", 1.0, 20000, datetime.date(2023, 1, 1))]
    dis = [ci.Disposal("BTC", 1.0, 30000, datetime.date(2024, 6, 1))]
    rows = ci.match_lots(acq, dis)
    assert len(rows) == 1
    assert rows[0].holding_period == "long" and rows[0].box == "F"
    assert rows[0].gain_loss == 10000.0


def test_fifo_exactly_365_days_is_short_term():
    acq = [ci.Acquisition("BTC", 1.0, 100, datetime.date(2023, 1, 1))]
    dis = [ci.Disposal("BTC", 1.0, 200, datetime.date(2024, 1, 1))]  # 365 days
    rows = ci.match_lots(acq, dis)
    assert rows[0].holding_period == "short"


def test_leap_year_exact_one_year_is_short_term():
    # [Codex P2 round 19] 2024-01-01 → 2025-01-01 is 366 days but EXACTLY one calendar year,
    # so it's short-term (long-term needs MORE than one year), not long via a >365-day count.
    acq = [ci.Acquisition("BTC", 1.0, 10000, datetime.date(2024, 1, 1))]
    dis = [ci.Disposal("BTC", 1.0, 15000, datetime.date(2025, 1, 1))]
    rows = ci.match_lots(acq, dis)
    assert rows[0].holding_period == "short" and rows[0].box == "C"
    # One day later IS long-term.
    dis2 = [ci.Disposal("BTC", 1.0, 15000, datetime.date(2025, 1, 2))]
    assert ci.match_lots(acq, dis2)[0].holding_period == "long"


def test_holding_period_feb29_acquisition():
    # Acquired Feb 29 (leap) → holding starts Mar 1; long-term iff sold on/after Mar 1 next yr.
    assert ci._holding_period(datetime.date(2024, 2, 29), datetime.date(2025, 2, 28)) == "short"
    assert ci._holding_period(datetime.date(2024, 2, 29), datetime.date(2025, 3, 1)) == "long"


def test_fifo_splits_across_two_lots_oldest_first():
    acq = [
        ci.Acquisition("BTC", 1.0, 20050, datetime.date(2023, 1, 10)),
        ci.Acquisition("BTC", 1.0, 28050, datetime.date(2023, 5, 1)),
    ]
    dis = [ci.Disposal("BTC", 1.5, 74925, datetime.date(2024, 6, 1))]
    rows = ci.match_lots(acq, dis)
    assert len(rows) == 2
    # First (oldest) lot: 1.0 BTC, proceeds 74925*1/1.5=49950, basis 20050 → 29900.
    assert rows[0].cost_basis == 20050.0 and rows[0].gain_loss == 29900.0
    # Second lot: 0.5 BTC, proceeds 24975, basis 14025 → 10950.
    assert rows[1].cost_basis == 14025.0 and rows[1].gain_loss == 10950.0


def test_fifo_insufficient_basis_flags_needs_basis_with_zero_cost():
    dis = [ci.Disposal("LTC", 10, 900, datetime.date(2024, 10, 1))]
    rows = ci.match_lots([], dis)
    assert len(rows) == 1
    assert rows[0].needs_basis is True
    assert rows[0].cost_basis == 0.0 and rows[0].gain_loss == 900.0
    assert rows[0].date_acquired == "UNKNOWN"


def test_per_account_send_removes_lot_so_later_sale_needs_basis():
    # [Codex P1 round 8] Per-account mode: buy, send away, then a later sale must NOT borrow
    # the sent-away lot's basis — the lot is gone, so the sale surfaces missing basis.
    txns = [
        {"date": "2024-01-01", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 20000},
        {"date": "2024-02-01", "kind": "send", "asset": "BTC", "quantity": 1},
        {"date": "2024-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 25000},
    ]
    r = ci.build_crypto_import(txns, per_account=True)
    assert r["crypto_summary"]["needs_basis_count"] == 1
    assert r["schedule_d"]["net_gain"] == 25000.0          # $0 basis, not 5000
    assert r["crypto_summary"]["disposals"] == 1            # the sale; send is not counted


def test_universal_pool_self_transfer_preserves_basis():
    # [Codex P2 round 10] Default (universal) pool: buy, self-transfer (send), sell on another
    # exchange — the send is a no-op so the original buy lot supplies basis (no needs_basis).
    txns = [
        {"date": "2024-01-01", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 20000, "exchange": "coinbase"},
        {"date": "2024-02-01", "kind": "send", "asset": "BTC", "quantity": 1,
         "exchange": "coinbase"},
        {"date": "2024-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 25000, "exchange": "kraken"},
    ]
    r = ci.build_crypto_import(txns)  # universal default
    assert r["crypto_summary"]["needs_basis_count"] == 0
    assert r["schedule_d"]["net_gain"] == 5000.0


def test_per_account_send_then_receive_with_basis_preserves_basis():
    # Per-account mode: recording BOTH legs nets to one lot (send removes the buy lot, receive
    # re-adds a basis-carrying lot, the sale consumes it).
    txns = [
        {"date": "2024-01-01", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 20000},
        {"date": "2024-02-01", "kind": "send", "asset": "BTC", "quantity": 1},
        {"date": "2024-02-01", "kind": "receive", "asset": "BTC", "quantity": 1,
         "basis_usd": 20000},
        {"date": "2024-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 25000},
    ]
    r = ci.build_crypto_import(txns, per_account=True)
    assert r["crypto_summary"]["needs_basis_count"] == 0
    assert r["schedule_d"]["net_gain"] == 5000.0


def test_fifo_does_not_consume_future_acquisition():
    # Disposal dated BEFORE the acquisition: the future lot must not supply basis
    # (it is not yet held). The sale becomes a $0-basis needs_basis row instead of
    # silently subtracting the later basis (which would understate the gain). [Codex P1]
    acq = [ci.Acquisition("BTC", 1.0, 18000, datetime.date(2024, 2, 1))]
    dis = [ci.Disposal("BTC", 1.0, 25000, datetime.date(2024, 1, 1))]
    rows = ci.match_lots(acq, dis)
    assert len(rows) == 1
    assert rows[0].needs_basis is True
    assert rows[0].cost_basis == 0.0 and rows[0].gain_loss == 25000.0
    assert rows[0].date_acquired == "UNKNOWN"


def test_fifo_future_lot_preserved_for_later_disposal():
    # A future-dated lot skipped by an early disposal is still available to a later one.
    acq = [ci.Acquisition("BTC", 1.0, 18000, datetime.date(2024, 2, 1))]
    dis = [
        ci.Disposal("BTC", 1.0, 25000, datetime.date(2024, 1, 1)),  # before acq → needs_basis
        ci.Disposal("BTC", 1.0, 26000, datetime.date(2024, 6, 1)),  # after acq → matches lot
    ]
    rows = ci.match_lots(acq, dis)
    assert len(rows) == 2
    early = next(r for r in rows if r.date_sold == "2024-01-01")
    later = next(r for r in rows if r.date_sold == "2024-06-01")
    assert early.needs_basis is True and early.cost_basis == 0.0
    assert later.needs_basis is False and later.cost_basis == 18000.0
    assert later.gain_loss == 8000.0


def test_same_day_sale_before_buy_is_needs_basis():
    # [Codex P2 round 3] Intraday order matters: a 09:00 sell must NOT borrow basis from a
    # 12:00 same-day buy. With timestamp ordering the sell is a $0-basis needs_basis row.
    txns = [
        {"date": "2024-06-01T09:00:00Z", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 25000},
        {"date": "2024-06-01T12:00:00Z", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 18000},
    ]
    r = ci.build_crypto_import(txns)
    assert r["crypto_summary"]["needs_basis_count"] == 1
    row = r["form_8949"]["part_i_short_term"][0]
    assert row["needs_basis"] is True and row["cost_basis"] == 0.0
    assert row["date_sold"] == "2024-06-01"  # rendered as a date, not a timestamp


def test_same_day_buy_before_sale_matches():
    # The reverse intraday order: a 09:00 buy DOES supply basis to a 12:00 sell.
    txns = [
        {"date": "2024-06-01T09:00:00Z", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 18000},
        {"date": "2024-06-01T12:00:00Z", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 25000},
    ]
    r = ci.build_crypto_import(txns)
    assert r["crypto_summary"]["needs_basis_count"] == 0
    assert r["schedule_d"]["short_term"]["gain"] == 7000.0


def test_fifo_partial_then_unmatched_remainder():
    acq = [ci.Acquisition("ADA", 5, 50, datetime.date(2024, 1, 1))]
    dis = [ci.Disposal("ADA", 8, 160, datetime.date(2024, 6, 1))]
    rows = ci.match_lots(acq, dis)
    # 5 matched (basis 50) + 3 unmatched (basis 0, needs_basis).
    assert len(rows) == 2
    assert rows[0].needs_basis is False and rows[1].needs_basis is True


# --------------------------------------------------------------------------
# Aggregator
# --------------------------------------------------------------------------

def test_build_end_to_end_from_fixture():
    r = ci.build_crypto_import(_txns())
    assert r["digital_asset_question"] == "Yes"
    # BTC LT gain = 29900 + 10950 = 40850.
    assert r["schedule_d"]["long_term"]["gain"] == 40850.0
    # ST = ETH trade gain 1000 + LTC needs-basis 895 = 1895.
    assert r["schedule_d"]["short_term"]["gain"] == 1895.0
    assert r["schedule_d"]["net_gain"] == 42745.0
    assert r["ordinary_income"]["total"] == 4000.0
    assert r["crypto_summary"]["needs_basis_count"] == 1


def test_digital_asset_no_when_only_acquire_and_hold():
    txns = [
        {"date": "2024-01-01", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 20000},
        {"date": "2024-02-01", "kind": "receive", "asset": "ETH", "quantity": 1,
         "basis_usd": 2000},
        {"date": "2024-03-01", "kind": "send", "asset": "BTC", "quantity": 0.1},
    ]
    r = ci.build_crypto_import(txns)
    assert r["digital_asset_question"] == "No"
    assert r["schedule_d"]["net_gain"] == 0.0


def test_receive_acquired_date_drives_holding_period():
    # [Codex P2 round 26] A receive's ORIGINAL acquisition date (not the transfer date) sets
    # the holding period: a coin bought 2022 and transferred in 2024 then sold is long-term.
    txns = [
        {"date": "2024-05-01", "kind": "receive", "asset": "BTC", "quantity": 1,
         "basis_usd": 20000, "acquired": "2022-01-01"},
        {"date": "2024-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 30000},
    ]
    r = ci.build_crypto_import(txns)
    assert r["schedule_d"]["long_term"]["gain"] == 10000.0
    assert r["schedule_d"]["short_term"]["rows"] == 0


def test_receive_acquired_date_does_not_make_lot_available_before_transfer():
    # [Codex P2 round 29] An original `acquired` (2022) drives the holding period but must NOT
    # make the lot available before its TRANSFER date — a 2024-03 sale can't consume a coin
    # received 2024-05, even though its original acquisition predates the sale.
    txns = [
        {"date": "2024-03-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 30000},
        {"date": "2024-05-01", "kind": "receive", "asset": "BTC", "quantity": 1,
         "basis_usd": 20000, "acquired": "2022-01-01"},
    ]
    r = ci.build_crypto_import(txns)
    assert r["crypto_summary"]["needs_basis_count"] == 1
    assert r["schedule_d"]["short_term"]["gain"] == 30000.0  # $0 basis, not 10000 long-term


def test_receive_without_acquired_uses_transfer_date():
    # Without `acquired`, the transfer date is used (short-term here) — documented fallback.
    txns = [
        {"date": "2024-05-01", "kind": "receive", "asset": "BTC", "quantity": 1,
         "basis_usd": 20000},
        {"date": "2024-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 30000},
    ]
    r = ci.build_crypto_import(txns)
    assert r["schedule_d"]["short_term"]["gain"] == 10000.0


def test_received_lot_basis_used_on_later_sale():
    txns = [
        {"date": "2024-01-01", "kind": "receive", "asset": "ETH", "quantity": 1,
         "basis_usd": 2000},
        {"date": "2024-06-01", "kind": "sell", "asset": "ETH", "quantity": 1,
         "usd_amount": 3000},
    ]
    r = ci.build_crypto_import(txns)
    assert r["crypto_summary"]["needs_basis_count"] == 0
    assert r["schedule_d"]["short_term"]["gain"] == 1000.0


def test_loss_is_negative_gain():
    txns = [
        {"date": "2024-01-01", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 30000},
        {"date": "2024-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 20000},
    ]
    r = ci.build_crypto_import(txns)
    assert r["schedule_d"]["short_term"]["gain"] == -10000.0


def test_build_rejects_non_list():
    with pytest.raises(ci.CryptoImportError):
        ci.build_crypto_import({"not": "a list"})


def test_out_of_year_disposal_excluded_from_totals():
    # [Codex P1 round 2] An out-of-year sale must NOT land on the filing year's totals,
    # even though its prior-year acquisition is kept for basis matching.
    txns = [
        {"date": "2022-01-01", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 10000},   # prior-year acquisition — kept for basis
        {"date": "2023-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 15000},   # 2023 disposal — excluded from a 2024 return
    ]
    r = ci.build_crypto_import(txns, tax_year=2024)
    assert any("EXCLUDED from the totals" in c for c in r["caveats"])
    assert r["schedule_d"]["net_gain"] == 0.0          # excluded, not 5000
    assert r["digital_asset_question"] == "No"          # no 2024 activity
    assert r["crypto_summary"]["disposals"] == 0


def test_filing_year_sale_reported_prior_year_sale_excluded():
    # Chronological FIFO across years, but only the filing-year row is reported.
    txns = [
        {"date": "2022-01-01", "kind": "buy", "asset": "BTC", "quantity": 2,
         "usd_amount": 20000},                                   # 2 lots' worth, $10k/unit
        {"date": "2023-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 15000},                                   # 2023 — excluded
        {"date": "2024-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 18000},                                   # 2024 — reported
    ]
    r = ci.build_crypto_import(txns, tax_year=2024)
    # Only the 2024 sale: proceeds 18000, basis 10000 → 8000 LT gain.
    assert r["schedule_d"]["net_gain"] == 8000.0
    assert r["schedule_d"]["long_term"]["rows"] == 1


def test_no_year_filter_reports_all_years():
    txns = [
        {"date": "2022-01-01", "kind": "buy", "asset": "BTC", "quantity": 1,
         "usd_amount": 10000},
        {"date": "2023-06-01", "kind": "sell", "asset": "BTC", "quantity": 1,
         "usd_amount": 15000},
    ]
    r = ci.build_crypto_import(txns)  # no tax_year → no filtering
    assert r["schedule_d"]["net_gain"] == 5000.0


def test_basisless_receive_then_sale_flags_needs_basis():
    # [Codex P2 round 2] A receive with no basis_usd → $0-basis lot; selling it must raise
    # needs_basis so the skill stops, not silently file a $0-basis gain.
    txns = [
        {"date": "2024-01-01", "kind": "receive", "asset": "ETH", "quantity": 1},
        {"date": "2024-06-01", "kind": "sell", "asset": "ETH", "quantity": 1,
         "usd_amount": 3000},
    ]
    r = ci.build_crypto_import(txns)
    assert r["crypto_summary"]["needs_basis_count"] == 1
    row = r["form_8949"]["part_i_short_term"][0]
    assert row["needs_basis"] is True and row["cost_basis"] == 0.0


def test_explicit_zero_basis_receive_is_not_flagged():
    # An EXPLICIT basis_usd (even 0) is a user assertion → not a needs_basis case.
    txns = [
        {"date": "2024-01-01", "kind": "receive", "asset": "ETH", "quantity": 1,
         "basis_usd": 0},
        {"date": "2024-06-01", "kind": "sell", "asset": "ETH", "quantity": 1,
         "usd_amount": 3000},
    ]
    r = ci.build_crypto_import(txns)
    assert r["crypto_summary"]["needs_basis_count"] == 0
    assert r["schedule_d"]["short_term"]["gain"] == 3000.0


# --------------------------------------------------------------------------
# Exchange adapters
# --------------------------------------------------------------------------

def test_coinbase_adapter():
    txns = ci.adapt_coinbase(_csv("coinbase.csv"), assume_self_transfers=True)
    kinds = [t["kind"] for t in txns]
    assert kinds == ["buy", "sell", "income", "trade", "receive", "send"]
    trade = next(t for t in txns if t["kind"] == "trade")
    assert trade["received_asset"] == "SOL" and trade["received_quantity"] == "100"
    income = next(t for t in txns if t["kind"] == "income")
    assert income["income_type"] == "staking"


def test_coinbase_adapter_feeds_build():
    txns = ci.adapt_coinbase(_csv("coinbase.csv"), assume_self_transfers=True)
    r = ci.build_crypto_import(txns)
    # BTC sell 0.5 of the 1.0 lot: proceeds 24975, basis 0.5*20050=10025 → 14950 LT.
    assert r["schedule_d"]["long_term"]["gain"] == 14950.0
    # ETH staking → trade: 1000 ST gain. Plus the DOGE receive (no disposal) is held.
    assert r["schedule_d"]["short_term"]["gain"] == 1000.0
    assert r["ordinary_income"]["total"] == 4000.0


def test_coinbase_total_fallback_does_not_double_count_fee():
    # [Codex P2 round 3] No Subtotal column → fall back to Total (inclusive of fees) and
    # zero the fee so expand_transaction doesn't re-apply it. Buy basis must be 20050,
    # not 20100.
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted",
             "Total (inclusive of fees and/or spread)", "Fees and/or Spread"],
            ["2024-01-01", "Buy", "BTC", "1", "20050", "50"]]
    txns = ci.adapt_coinbase(rows)
    assert txns[0]["usd_amount"] == "20050" and txns[0]["fee_usd"] == "0"
    r = ci.build_crypto_import(txns)
    assert r["crypto_summary"]["per_asset"] == {}  # no disposal yet
    # Verify the lot basis via a follow-on sale at cost → zero gain.
    txns.append({"date": "2024-02-01", "kind": "sell", "asset": "BTC", "quantity": 1,
                 "usd_amount": 20050})
    r2 = ci.build_crypto_import(txns)
    assert r2["schedule_d"]["short_term"]["gain"] == 0.0


def test_coinbase_subtotal_present_applies_fee():
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal",
             "Total (inclusive of fees and/or spread)", "Fees and/or Spread"],
            ["2024-01-01", "Buy", "BTC", "1", "20000", "20050", "50"]]
    txns = ci.adapt_coinbase(rows)
    assert txns[0]["usd_amount"] == "20000" and txns[0]["fee_usd"] == "50"


def test_coinbase_send_without_flag_is_rejected():
    # [Codex P1 round 9] A Coinbase 'Send' is ambiguous (self-transfer vs taxable payment).
    # Default → hard-fail rather than silently treat as non-taxable and under-report.
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal"],
            ["2024-01-01", "Send", "BTC", "0.1", "6000"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_coinbase(rows)
    # With the explicit confirmation, it imports as a non-taxable send.
    txns = ci.adapt_coinbase(rows, assume_self_transfers=True)
    assert txns[0]["kind"] == "send"


def test_coinbase_receive_without_flag_is_rejected():
    # [Codex P2 round 14] A Coinbase 'Receive' is ambiguous (own-wallet transfer vs income
    # receipt) — default hard-fails rather than silently dropping possible ordinary income.
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal"],
            ["2024-01-01", "Receive", "BTC", "0.1", "6000"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_coinbase(rows)
    txns = ci.adapt_coinbase(rows, assume_self_transfers=True)
    assert txns[0]["kind"] == "receive"


def test_gemini_debit_without_flag_is_rejected():
    rows = [["Date", "Type", "Symbol", "USD Amount", "Crypto Amount"],
            ["2024-01-01", "Debit", "BTCUSD", "0", "0.05"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_gemini(rows)
    txns = ci.adapt_gemini(rows, assume_self_transfers=True)
    assert txns[0]["kind"] == "send"


def test_coinbase_usd_prefixed_amount_headers():
    # [Codex P1 round 6] Some exports prefix amount columns with the currency.
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted",
             "USD Subtotal", "USD Total (inclusive of fees)", "USD Fees"],
            ["2024-01-01", "Buy", "BTC", "1", "20000", "20050", "50"]]
    txns = ci.adapt_coinbase(rows)
    assert txns[0]["usd_amount"] == "20000" and txns[0]["fee_usd"] == "50"


def test_coinbase_missing_amount_column_hard_fails():
    # No recognizable amount column for a buy → must refuse, not import $0 (would wipe the
    # Form 8949/Schedule D figures). [Codex P1 round 6]
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Notes"],
            ["2024-01-01", "Buy", "BTC", "1", "bought some"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_coinbase(rows)


def test_coinbase_non_usd_currency_rejected():
    # [Codex P2 round 21] A EUR/GBP-denominated Coinbase export must not have its Subtotal
    # read as USD — reject (USD-only importer).
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted",
             "Spot Price Currency", "Subtotal"],
            ["2024-01-01", "Buy", "BTC", "1", "EUR", "20000"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_coinbase(rows)
    # USD currency is fine.
    rows_usd = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted",
                 "Spot Price Currency", "Subtotal"],
                ["2024-01-01", "Buy", "BTC", "1", "USD", "20000"]]
    assert ci.adapt_coinbase(rows_usd)[0]["usd_amount"] == "20000"


def test_coinbase_crypto_quoted_market_in_notes_rejected():
    # [Codex P1 round 27] An Advanced Trade fill on a crypto-quoted market named only in the
    # Notes (XLM-BTC) must be rejected, not imported as a USD trade that drops the BTC leg.
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal",
             "Notes"],
            ["2024-01-01", "Sell", "XLM", "100", "500", "Sold 100 XLM on XLM-BTC"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_coinbase(rows)


def test_coinbase_size_column_negative_normalized():
    # [Codex P2 round 29] Advanced-trade exports put the filled amount in a 'Size' column
    # (possibly negative for sells); read it and normalize the sign.
    rows = [["Timestamp", "Transaction Type", "Asset", "Size", "Subtotal"],
            ["2024-06-01", "Sell", "BTC", "-0.5", "25000"]]
    txns = ci.adapt_coinbase(rows)
    assert txns[0]["kind"] == "sell" and txns[0]["quantity"] == "0.5"


def test_coinbase_signed_disposal_quantity_normalized():
    # [Codex P1 round 28] A Coinbase Sell with a NEGATIVE Quantity Transacted must import as a
    # positive-quantity disposal (the compute path rejects negatives).
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal"],
            ["2024-06-01", "Sell", "BTC", "-0.5", "25000"]]
    txns = ci.adapt_coinbase(rows)
    assert txns[0]["kind"] == "sell" and txns[0]["quantity"] == "0.5"
    # And it builds without the negative-quantity rejection.
    ci.build_crypto_import(txns)


def test_coinbase_crypto_quoted_product_column_rejected():
    # [Codex P1 round 28] Market encoded in a Product column (ETH-BTC), no spot-currency
    # column → must reject (crypto-quoted), not import as USD.
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal",
             "Product"],
            ["2024-01-01", "Sell", "ETH", "2", "5000", "ETH-BTC"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_coinbase(rows)


def test_coinbase_usd_product_column_accepted():
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal",
             "Product"],
            ["2024-01-01", "Buy", "BTC", "1", "20000", "BTC-USD"]]
    assert ci.adapt_coinbase(rows)[0]["usd_amount"] == "20000"


def test_coinbase_usd_market_in_notes_accepted():
    # A USD-quoted market (or a date in Notes) must NOT trip the crypto-market guard.
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal",
             "Notes"],
            ["2024-01-01", "Buy", "BTC", "1", "20000", "Bought 1.00 BTC on BTC-USD 2024-01-01"]]
    assert ci.adapt_coinbase(rows)[0]["usd_amount"] == "20000"


def test_gemini_bare_stablecoin_symbol_intact():
    # [Codex P2 round 27] A bare 'GUSD' symbol is the asset itself, not base 'G' / quote 'USD'.
    assert ci._gemini_split("GUSD") == ("GUSD", "")
    assert ci._gemini_split("GUSDUSD") == ("GUSD", "USD")


def test_coinbase_unknown_type_rejected():
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal"],
            ["2024-01-01", "Borrow", "BTC", "1", "100"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_coinbase(rows)


def test_coinbase_convert_unparseable_notes_rejected():
    rows = [["Timestamp", "Transaction Type", "Asset", "Quantity Transacted", "Subtotal",
             "Notes"],
            ["2024-01-01", "Convert", "ETH", "2", "5000", "swapped some stuff"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_coinbase(rows)


def test_kraken_adapter_aliases_and_pairs():
    txns = ci.adapt_kraken(_csv("kraken.csv"))
    assert txns[0]["asset"] == "BTC" and txns[0]["kind"] == "buy"
    assert txns[1]["asset"] == "ETH" and txns[1]["kind"] == "sell"


@pytest.mark.parametrize("pair,base", [
    ("XLTCZUSD", "LTC"), ("XXRPZUSD", "XRP"), ("XXDGZUSD", "DOGE"), ("XXMRZUSD", "XMR"),
])
def test_kraken_legacy_asset_codes_normalized(pair, base):
    # [Codex P2 round 23] Kraken legacy X-prefixed codes must normalize so they match
    # Coinbase/normalized lots for the same coin in a universal import.
    assert ci._split_kraken_pair(pair) == (base, "USD")


def test_kraken_missing_cost_hard_fails():
    # [Codex P2 round 11] No 'cost' (USD) value → refuse, not import a $0 trade.
    rows = [["pair", "time", "type", "fee", "vol"],
            ["XXBTZUSD", "2024-01-01 00:00:00", "buy", "5", "0.1"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_kraken(rows)


def test_kraken_xbtusd_parses_as_btc_usd():
    # [Codex P2 round 15] XBTUSD must split as XBT/USD (BTC/USD), not XB/TUSD — true fiat is
    # matched before stablecoin suffixes.
    assert ci._split_kraken_pair("XBTUSD") == ("BTC", "USD")
    rows = [["pair", "time", "type", "cost", "fee", "vol"],
            ["XBTUSD", "2024-01-01 00:00:00", "buy", "30000", "5", "1.0"]]
    txns = ci.adapt_kraken(rows)
    assert txns[0]["asset"] == "BTC" and txns[0]["kind"] == "buy"


@pytest.mark.parametrize("pair,base", [
    ("XBT/USD", "BTC"),    # slash-delimited BASE/QUOTE — strip the separator first
    ("ADA/USD", "ADA"),
    ("XBT-USD", "BTC"),    # dash-delimited variant
])
def test_kraken_separator_delimited_pairs_normalized(pair, base):
    # [Codex P2 round 31] Slash/dash-delimited Kraken pairs (XBT/USD, ADA/USD) must have the
    # separator stripped before suffix-splitting; otherwise the USD branch returns base "XBT/"
    # or "ADA/", which fails to match lots from other exchanges in a universal import (phantom
    # $0-basis needs_basis rows / invalid Form 8949 descriptions).
    assert ci._split_kraken_pair(pair) == (base, "USD")
    rows = [["pair", "time", "type", "cost", "fee", "vol"],
            [pair, "2024-01-01 00:00:00", "buy", "30000", "5", "1.0"]]
    txns = ci.adapt_kraken(rows)
    assert txns[0]["asset"] == base and txns[0]["kind"] == "buy"


@pytest.mark.parametrize("pair,base,quote", [
    ("ETHTUSD", "ETH", "TUSD"),    # ETH/TUSD — base recognized → stablecoin
    ("ETHBUSD", "ETH", "BUSD"),    # ETH/BUSD
    ("BTCPYUSD", "BTC", "PYUSD"),  # BTC/PYUSD
])
def test_kraken_end_usd_stablecoin_recognized_base_rejected(pair, base, quote):
    # [Codex P1 round 20] A stablecoin whose ticker ends in 'USD', with a recognized base, is
    # a crypto-to-crypto disposition — must reject, not emit a bogus-base USD buy.
    assert ci._split_kraken_pair(pair) == (base, quote)
    rows = [["pair", "time", "type", "cost", "fee", "vol"],
            [pair, "2024-01-01 00:00:00", "buy", "3000", "5", "1.0"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_kraken(rows)


@pytest.mark.parametrize("pair,base", [
    ("XBTUSD", "BTC"),    # XB is not a real base → fiat USD
    ("DOTUSD", "DOT"),    # DO is not a real base → fiat USD
    ("BNBUSD", "BNB"),    # BN is not a real base → fiat USD
])
def test_kraken_end_usd_collision_unknown_base_is_fiat(pair, base):
    # The flip side: a fiat-USD pair whose base ends in T/B must stay USD (round-15 guard).
    assert ci._split_kraken_pair(pair) == (base, "USD")


def test_kraken_stablecoin_pair_rejected():
    # [Codex P2 round 13] A stablecoin is a digital asset, so a BTCUSDT trade is a
    # crypto-to-crypto disposition — reject (don't treat USDT as fiat and drop the leg).
    rows = [["pair", "time", "type", "cost", "fee", "vol"],
            ["XBTUSDT", "2024-01-01 00:00:00", "buy", "30000", "5", "1.0"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_kraken(rows)


def test_kraken_crypto_to_crypto_pair_rejected():
    rows = [["pair", "time", "type", "cost", "fee", "vol"],
            ["ETHXBT", "2024-01-01 00:00:00", "sell", "1.5", "0.01", "1.0"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_kraken(rows)


def test_gemini_signed_sell_quantity_normalized():
    # [Codex P2 round 32] Gemini's outflow-sign convention can put a NEGATIVE 'Crypto Amount'
    # on a Sell; the kind already carries direction and the compute path rejects negatives
    # (allow_negative=False), so the adapter must emit the magnitude — mirroring Coinbase.
    rows = [["Date", "Time (UTC)", "Type", "Symbol", "USD Amount", "Fee (USD)", "Crypto Amount"],
            ["2024-06-01", "12:00:00", "Sell", "BTCUSD", "25000", "25", "-0.5"]]
    txns = ci.adapt_gemini(rows)
    assert txns[0]["kind"] == "sell" and txns[0]["quantity"] == "0.5"
    # And it builds without the negative-quantity rejection.
    ci.build_crypto_import(txns)


def test_gemini_adapter():
    txns = ci.adapt_gemini(_csv("gemini.csv"), assume_self_transfers=True)
    kinds = [t["kind"] for t in txns]
    assert kinds == ["buy", "sell", "income", "send"]
    assert txns[0]["asset"] == "BTC"


def test_gemini_combines_date_and_time_for_intraday_order():
    # [Codex P2 round 5] Gemini splits Date + Time (UTC); the adapter must combine them so
    # the intraday FIFO guard works. A 09:00 sell before a 12:00 same-day buy → needs_basis.
    rows = [
        ["Date", "Time (UTC)", "Type", "Symbol", "USD Amount", "Fee (USD)", "Crypto Amount"],
        ["2024-06-01", "09:00:00", "Sell", "BTCUSD", "25000", "0", "1"],
        ["2024-06-01", "12:00:00", "Buy", "BTCUSD", "18000", "0", "1"],
    ]
    txns = ci.adapt_gemini(rows)
    assert txns[0]["date"] == "2024-06-01 09:00:00"
    r = ci.build_crypto_import(txns)
    assert r["crypto_summary"]["needs_basis_count"] == 1
    assert r["form_8949"]["part_i_short_term"][0]["needs_basis"] is True


def test_gemini_bare_credit_without_flag_is_rejected():
    # [Codex P1 round 16] A bare 'Credit'/'Deposit' is ambiguous (own-wallet transfer vs
    # Earn income) — default hard-fails rather than over-reporting a transfer as income.
    rows = [["Date", "Type", "Symbol", "USD Amount", "Crypto Amount"],
            ["2024-01-01", "Credit", "BTCUSD", "6000", "0.1"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_gemini(rows)
    # Confirmed own-wallet → non-taxable receive (NOT income).
    txns = ci.adapt_gemini(rows, assume_self_transfers=True)
    assert txns[0]["kind"] == "receive"


@pytest.mark.parametrize("symbol", ["BTCUSDT", "BTCGUSD", "ETHUSDC"])
def test_gemini_stablecoin_quoted_pair_rejected(symbol):
    # [Codex P2 round 17] A stablecoin-quoted Gemini market is a crypto-to-crypto
    # disposition, not a USD trade — reject so the stablecoin leg isn't dropped.
    rows = [["Date", "Type", "Symbol", "USD Amount", "Crypto Amount"],
            ["2024-01-01", "Buy", symbol, "5000", "0.1"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_gemini(rows)


@pytest.mark.parametrize("symbol", ["ETHBTC", "BTCETH", "SOLBTC"])
def test_gemini_crypto_cross_pair_rejected(symbol):
    # [Codex P1 round 24] A Gemini ActiveTrader crypto-cross pair (ETH/BTC) leaves
    # _gemini_split quote == "" — must reject, not emit a USD buy of fake asset "ETHBTC".
    rows = [["Date", "Type", "Symbol", "USD Amount", "Crypto Amount"],
            ["2024-01-01", "Buy", symbol, "5000", "1"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_gemini(rows)


@pytest.mark.parametrize("symbol,fiat", [("BTCEUR", "EUR"), ("ETHGBP", "GBP")])
def test_gemini_non_usd_fiat_gets_conversion_message(symbol, fiat):
    # [Codex P2 round 25] A non-USD FIAT pair must be rejected with currency-conversion
    # guidance (mentioning the fiat), not the crypto-to-crypto 'trade' message.
    rows = [["Date", "Type", "Symbol", "USD Amount", "Crypto Amount"],
            ["2024-01-01", "Buy", symbol, "5000", "1"]]
    with pytest.raises(ci.CryptoImportError) as e:
        ci.adapt_gemini(rows)
    msg = str(e.value)
    assert fiat in msg and "non-USD fiat" in msg


def test_gemini_usd_pair_still_accepted():
    rows = [["Date", "Type", "Symbol", "USD Amount", "Crypto Amount"],
            ["2024-01-01", "Buy", "BTCUSD", "5000", "0.1"]]
    assert ci.adapt_gemini(rows)[0]["kind"] == "buy"


def test_gemini_split_recognizes_quotes():
    assert ci._gemini_split("BTCUSD") == ("BTC", "USD")
    assert ci._gemini_split("BTCGUSD") == ("BTC", "GUSD")
    assert ci._gemini_split("BTCUSDT") == ("BTC", "USDT")


def test_gemini_interest_credit_is_income():
    rows = [["Date", "Type", "Symbol", "USD Amount", "Crypto Amount"],
            ["2024-01-01", "Interest Credit", "BTCUSD", "50", "0.001"]]
    txns = ci.adapt_gemini(rows)  # no flag needed — clearly income
    assert txns[0]["kind"] == "income" and txns[0]["income_type"] == "interest"


def test_gemini_missing_usd_amount_hard_fails():
    # [Codex P2 round 11] A buy with no USD Amount → refuse, not import $0.
    rows = [["Date", "Type", "Symbol", "Crypto Amount"],
            ["2024-01-01", "Buy", "BTCUSD", "0.2"]]
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_gemini(rows)


def test_adapt_csv_unknown_exchange_rejected():
    with pytest.raises(ci.CryptoImportError):
        ci.adapt_csv([["a"]], "binance")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _run(*args):
    return subprocess.run(
        [sys.executable, str(REPO / "agents/lib/crypto_importer.py"), *args],
        capture_output=True, text=True, cwd=str(REPO))


def test_cli_self_test():
    p = _run()
    assert p.returncode == 0 and "self-test: OK" in p.stdout


def test_cli_adapt_coinbase():
    p = _run("adapt", str(FIX / "coinbase.csv"), "--exchange", "coinbase", "--assume-self-transfers")
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert len(data["transactions"]) == 6


def test_cli_compute_from_normalized_json():
    p = _run("compute", str(FIX / "transactions.json"))
    assert p.returncode == 0
    r = json.loads(p.stdout)
    assert r["schedule_d"]["net_gain"] == 42745.0


def test_cli_compute_markdown():
    p = _run("compute", str(FIX / "transactions.json"), "--markdown")
    assert p.returncode == 0 and "Digital-asset question" in p.stdout


def test_cli_adapt_flag_without_operand_is_clean_error():
    # [Codex P2 round 12] Trailing --exchange (no operand) must fail cleanly, not traceback.
    p = _run("adapt", str(FIX / "coinbase.csv"), "--exchange")
    assert p.returncode == 1 and "ERROR:" in p.stderr and "Traceback" not in p.stderr


def test_cli_compute_year_without_operand_is_clean_error():
    p = _run("compute", str(FIX / "transactions.json"), "--year")
    assert p.returncode == 1 and "ERROR:" in p.stderr and "Traceback" not in p.stderr


def test_cli_unknown_command():
    p = _run("frobnicate")
    assert p.returncode == 2


def test_cli_compute_missing_file():
    p = _run("compute", str(FIX / "nope.json"))
    assert p.returncode == 1


def test_cli_compute_malformed_json_is_clean_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not json")
    p = _run("compute", str(bad))
    # Clean ERROR, not a Python traceback. [Codex P2]
    assert p.returncode == 1
    assert p.stdout == "" and "ERROR:" in p.stderr
    assert "Traceback" not in p.stderr


def test_cli_compute_empty_file_is_clean_error():
    p = _run("compute", "/dev/null")
    assert p.returncode == 1 and "ERROR:" in p.stderr and "Traceback" not in p.stderr


def test_cli_bad_year_is_clean_error():
    p = _run("compute", str(FIX / "transactions.json"), "--year", "notayear")
    assert p.returncode == 1 and "ERROR:" in p.stderr


def test_read_csv_masks_an_ssn_shaped_cell_and_leaves_the_rest(tmp_path):
    """V0: the importer reads through `parse_utils.extract_csv` — an adapter
    error quotes the row it rejected and `adapt` prints the notes column
    through, so the read masks. The value is built at runtime; no full
    identifier is persisted as a fixture."""
    import csv
    ssn = "-".join(("000", "00", "4321"))
    routing = "".join(("021", "000", "021"))
    doc = tmp_path / "coinbase.csv"
    with doc.open("w", newline="") as f:
        csv.writer(f).writerows([
            ["Timestamp", "Transaction Type", "Notes"],
            ["2024-06-01T00:00:00Z", "Sell", f"ref {ssn} acct {routing}"]])
    rows = ci._read_csv(str(doc))
    assert rows[1][2] == f"ref XXX-XX-4321 acct {routing}"
    assert rows[0] == ["Timestamp", "Transaction Type", "Notes"]


def test_read_csv_is_the_parse_utils_boundary(tmp_path):
    """A second reader re-implemented the read minus `_validate_file_path`'s
    guards, and `main` caught only CryptoImportError — so a Latin-1 export
    reached the terminal as a UnicodeDecodeError traceback, the class of
    output the parse boundary exists to keep out of the transcript. The
    reader now IS `extract_csv`, and `main`'s boundary is every exception."""
    doc = tmp_path / "coinbase.csv"
    doc.write_bytes("Timestamp,Notes\n2024-06-01T00:00:00Z,caf\xe9\n".encode("latin-1"))
    with pytest.raises(ValueError):
        ci._read_csv(str(doc))
    p = _run("adapt", str(doc), "--exchange", "coinbase")
    assert p.returncode == 1
    assert "Traceback" not in p.stderr and "ERROR: UnicodeDecodeError" in p.stderr
    with pytest.raises(FileNotFoundError):
        ci._read_csv(str(tmp_path / "missing.csv"))
    (tmp_path / "export.txt").write_text("a,b\n")
    with pytest.raises(ValueError, match="Unexpected file type"):
        ci._read_csv(str(tmp_path / "export.txt"))
