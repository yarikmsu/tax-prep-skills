"""Tests for the tax-loss harvesting scanner — agents/lib/loss_harvest.py (backlog #4)."""
import dataclasses
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import loss_harvest as lh
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ny_fixtures import ny_state_cache  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "harvest"
REPO = Path(__file__).resolve().parent.parent
TODAY = datetime.date(2026, 12, 15)


def _load(name):
    return json.loads((FIX / name).read_text())


def _build(holdings=None, tax_law=None, name="harvest", profile=None, today=TODAY):
    return lh.build_harvest(
        _load("result.json"),
        profile if profile is not None else _load("taxpayer_profile.json"),
        tax_law if tax_law is not None else _load("tax_law_2026.json"),
        holdings if holdings is not None else _load("holdings.json"),
        2026, name=name, today=today,
    )


def _fl_resident_profile():
    # Round-22 P2: the affirmative "$0 — correct and complete" claim now
    # requires the FULL residence chain (known resident + code-matching
    # type-none cache) — the default fixture profile has no address, which
    # deliberately degrades instead of affirming.
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "FL"}
    return profile


def _ws(tmp_path):
    for f in ("result.json", "taxpayer_profile.json", "tax_law_2026.json", "holdings.json"):
        shutil.copy(FIX / f, tmp_path / f)
    return tmp_path


# --------------------------------------------------------------------------
# holding-period and wash-sale primitives
# --------------------------------------------------------------------------

def test_holding_period_long_over_one_year():
    assert lh.derive_holding_period(
        datetime.date(2024, 1, 15), datetime.date(2026, 12, 15)) == "long"


def test_holding_period_short_under_one_year():
    assert lh.derive_holding_period(
        datetime.date(2026, 6, 1), datetime.date(2026, 12, 15)) == "short"


def test_holding_period_exact_365_days_is_short():
    # IRS rule: must hold MORE than one year — exactly 365 days is short-term.
    acq = datetime.date(2025, 12, 15)
    sell = datetime.date(2026, 12, 15)
    assert lh.derive_holding_period(acq, sell) == "short"


def test_holding_period_one_day_past_year_is_long():
    acq = datetime.date(2025, 12, 14)
    sell = datetime.date(2026, 12, 15)
    assert lh.derive_holding_period(acq, sell) == "long"


import math


def test_wash_sale_within_30_days_past_returns_qty_and_reason():
    # No quantity given → +inf pool (full-block fallback for sparse data).
    txns = [{"ticker": "TSLA", "date": "2026-12-01", "kind": "buy"}]
    qty, reasons = lh.check_wash_sale("TSLA", txns, TODAY)
    assert math.isinf(qty)
    assert any("TSLA" in r and "2026-12-01" in r for r in reasons)


def test_wash_sale_aggregates_quantity_when_provided():
    # Two same-ticker buys inside the window sum into one replacement pool.
    txns = [
        {"ticker": "AAPL", "date": "2026-12-01", "kind": "buy", "quantity": 3},
        {"ticker": "AAPL", "date": "2026-12-10", "kind": "buy", "quantity": 2},
    ]
    qty, reasons = lh.check_wash_sale("AAPL", txns, TODAY)
    assert qty == 5
    assert len(reasons) == 2


def test_wash_sale_within_30_days_future():
    # A buy planned 21 days AFTER the sale also poisons the loss (§1091 is bilateral).
    txns = [{"ticker": "META", "date": "2027-01-05", "kind": "buy", "quantity": 2}]
    qty, reasons = lh.check_wash_sale("META", txns, TODAY)
    assert qty == 2 and any("2027-01-05" in r for r in reasons)


def test_wash_sale_outside_window():
    txns = [{"ticker": "AAPL", "date": "2026-11-10", "kind": "buy", "quantity": 5}]
    qty, reasons = lh.check_wash_sale("AAPL", txns, TODAY)
    assert qty == 0 and reasons == []


def test_wash_sale_ignores_sells():
    txns = [{"ticker": "TSLA", "date": "2026-12-01", "kind": "sell", "quantity": 100}]
    qty, _ = lh.check_wash_sale("TSLA", txns, TODAY)
    assert qty == 0


def test_wash_sale_case_insensitive_ticker():
    txns = [{"ticker": "tsla", "date": "2026-12-01", "kind": "buy", "quantity": 5}]
    qty, _ = lh.check_wash_sale("TSLA", txns, TODAY)
    assert qty == 5


def test_wash_sale_pools_across_accounts():
    # Spousal / cross-account same-ticker buys sum into one replacement pool.
    txns = [
        {"ticker": "TSLA", "account": "Spouse Schwab",
         "date": "2026-12-01", "kind": "buy", "quantity": 3},
        {"ticker": "TSLA", "account": "My Roth IRA",
         "date": "2026-12-05", "kind": "buy", "quantity": 4},
    ]
    qty, _ = lh.check_wash_sale("TSLA", txns, TODAY)
    assert qty == 7


def test_wash_sale_rejects_negative_quantity():
    txns = [{"ticker": "X", "date": "2026-12-01", "kind": "buy", "quantity": -1}]
    with pytest.raises(lh.LossHarvestError, match="invalid quantity"):
        lh.check_wash_sale("X", txns, TODAY)


def test_wash_sale_rejects_bool_quantity():
    # Bool is never a valid amount (mirrors the keystones).
    txns = [{"ticker": "X", "date": "2026-12-01", "kind": "buy", "quantity": True}]
    with pytest.raises(lh.LossHarvestError, match="numeric"):
        lh.check_wash_sale("X", txns, TODAY)


def test_wash_sale_ignores_zero_quantity_buys():
    # Per §1091 ("stock or securities acquired"), a 0-share buy isn't an
    # acquisition. Don't pollute the pool or the reasons list.
    txns = [
        {"ticker": "X", "date": "2026-12-01", "kind": "buy", "quantity": 0},
        {"ticker": "X", "date": "2026-12-05", "kind": "buy", "quantity": 3},
    ]
    qty, reasons = lh.check_wash_sale("X", txns, TODAY)
    assert qty == 3
    # Only the real 3-share buy is in reasons; the 0-qty buy is skipped.
    assert len(reasons) == 1
    assert "qty 3" in reasons[0]


# --------------------------------------------------------------------------
# parse_holdings — input validation
# --------------------------------------------------------------------------

def test_parse_holdings_rejects_negative_quantity():
    bad = {"lots": [{"id": "x", "ticker": "X", "account": "A",
                     "acquired": "2024-01-01", "quantity": -1,
                     "cost_basis": 100, "current_value": 50, "tax_advantaged": False}]}
    with pytest.raises(lh.LossHarvestError, match="quantity"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_rejects_zero_quantity():
    # A closed/empty lot (qty=0) would otherwise enter the harvest plan with
    # `unrealized_loss = basis - value` and inflate fed_savings for shares the
    # taxpayer no longer owns. Must be rejected at parse time.
    bad = {"lots": [{"id": "ghost", "ticker": "X", "account": "A",
                     "acquired": "2024-01-01", "quantity": 0,
                     "cost_basis": 10000, "current_value": 5000, "tax_advantaged": False}]}
    with pytest.raises(lh.LossHarvestError, match="must be > 0"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_allows_zero_cost_basis():
    # Legitimate: gift shares, employer match (basis 0 is acceptable).
    ok = {"lots": [{"id": "g", "ticker": "X", "account": "A",
                    "acquired": "2024-01-01", "quantity": 10,
                    "cost_basis": 0, "current_value": 500, "tax_advantaged": False}]}
    lots, _, _ = lh.parse_holdings(ok, today=TODAY)
    assert lots[0].unrealized_loss == 0  # this lot is a gain, not a loss


def test_parse_holdings_allows_zero_current_value():
    # Legitimate: worthless security harvest (§165(g)).
    ok = {"lots": [{"id": "w", "ticker": "BUST", "account": "A",
                    "acquired": "2024-01-01", "quantity": 100,
                    "cost_basis": 5000, "current_value": 0, "tax_advantaged": False}]}
    lots, _, _ = lh.parse_holdings(ok, today=TODAY)
    assert lots[0].unrealized_loss == 5000


def test_parse_holdings_rejects_negative_basis():
    bad = {"lots": [{"id": "x", "ticker": "X", "account": "A",
                     "acquired": "2024-01-01", "quantity": 1,
                     "cost_basis": -100, "current_value": 50, "tax_advantaged": False}]}
    with pytest.raises(lh.LossHarvestError, match="basis"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_rejects_malformed_date():
    bad = {"lots": [{"id": "x", "ticker": "X", "account": "A",
                     "acquired": "not-a-date", "quantity": 1,
                     "cost_basis": 100, "current_value": 50, "tax_advantaged": False}]}
    with pytest.raises(lh.LossHarvestError, match="acquired"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_rejects_future_acquired_date():
    bad = {"lots": [{"id": "x", "ticker": "X", "account": "A",
                     "acquired": "2030-01-01", "quantity": 1,
                     "cost_basis": 100, "current_value": 50, "tax_advantaged": False}]}
    with pytest.raises(lh.LossHarvestError, match="future"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_requires_unique_lot_ids():
    bad = {"lots": [
        {"id": "dup", "ticker": "A", "account": "A", "acquired": "2024-01-01",
         "quantity": 1, "cost_basis": 100, "current_value": 50, "tax_advantaged": False},
        {"id": "dup", "ticker": "B", "account": "A", "acquired": "2024-01-01",
         "quantity": 1, "cost_basis": 100, "current_value": 50, "tax_advantaged": False},
    ]}
    with pytest.raises(lh.LossHarvestError, match="unique"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_empty_lots_raises():
    with pytest.raises(lh.LossHarvestError, match="at least one lot"):
        lh.parse_holdings({"lots": []}, today=TODAY)


# Codex P2: parse-boundary symmetry — each txn must be a JSON object so a
# hand-edited malformed entry produces a clean LossHarvestError, not an
# uncaught AttributeError deep in _apply_wash_sale_prorate.

def _valid_lot():
    return {"id": "L", "ticker": "X", "account": "A",
            "acquired": "2024-01-01", "quantity": 10,
            "cost_basis": 100, "current_value": 50, "tax_advantaged": False}


def test_parse_holdings_rejects_string_transaction():
    bad = {"lots": [_valid_lot()],
           "recent_transactions": ["buy ABC 10"]}
    with pytest.raises(lh.LossHarvestError,
                       match="recent_transactions\\[0\\].*JSON object"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_rejects_null_transaction():
    bad = {"lots": [_valid_lot()],
           "recent_transactions": [None]}
    with pytest.raises(lh.LossHarvestError,
                       match="recent_transactions\\[0\\].*JSON object"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_rejects_mixed_list_with_invalid_entry():
    # First two valid, third is a list (not a dict). The index in the error
    # message must identify exactly which entry failed.
    bad = {"lots": [_valid_lot()],
           "recent_transactions": [
               {"ticker": "X", "account": "A", "date": "2026-12-01",
                "kind": "buy", "quantity": 5},
               {"ticker": "Y", "account": "A", "date": "2026-12-02",
                "kind": "buy", "quantity": 3},
               ["not", "a", "dict"],
           ]}
    with pytest.raises(lh.LossHarvestError,
                       match="recent_transactions\\[2\\].*JSON object"):
        lh.parse_holdings(bad, today=TODAY)


# --------------------------------------------------------------------------
# build_harvest — end-to-end against the fixture
# --------------------------------------------------------------------------

def test_build_harvest_returns_report_with_schema_and_anchor():
    r = _build()
    assert r.schema == "tax-harvest/v1"
    assert r.tax_year == 2026
    # Anchor's line_24 must match the real result.json line 24 EXACTLY (the contract
    # shared with /tax-scenario and /tax-roth-convert — federal savings is a Δ around
    # the real number).
    assert r.anchor["line_24"] == 50000.0
    assert r.baseline_fed_tax == 50000.0


def test_build_harvest_separates_clear_partial_and_tax_advantaged():
    # Under v1.5 (share-prorated + tax-advantaged exclusion):
    #  - L1, L2 in taxable Schwab Brokerage → clear, fully eligible.
    #  - L4 TSLA (taxable): 5-share spouse wash buy vs 20 lot → partial 25%.
    #  - L5 META (Fidelity IRA): tax-advantaged → EXCLUDED from harvest
    #    regardless of any wash-sale signal (no §165 deductible loss to disallow,
    #    so the matcher leaves status="clear" and allowed_loss = unrealized_loss).
    r = _build()
    ids = {l.id: l for l in r.lots}

    # L1 (LT $1500), L2 (ST $2500) — taxable, clear, fully eligible.
    assert ids["L1"].wash_sale_status == "clear"
    assert ids["L1"].tax_advantaged is False
    assert ids["L1"].allowed_loss == 1500
    assert ids["L2"].tax_advantaged is False
    assert ids["L2"].allowed_loss == 2500

    # L3 is a GAIN, no harvest action.
    assert ids["L3"].unrealized_loss == 0
    assert ids["L3"].id not in r.eligible_lot_ids

    # L4 TSLA taxable: 5-share spouse wash buy vs 20 lot → 25% disallowed,
    # allowed $2250. status=partial → ELIGIBLE.
    assert ids["L4"].wash_sale_status == "partial"
    assert ids["L4"].tax_advantaged is False
    assert ids["L4"].disallowed_loss == 750
    assert ids["L4"].allowed_loss == 2250
    assert "L4" in r.eligible_lot_ids

    # L5 META Fidelity IRA: tax-advantaged. Status stays "clear" because the
    # matcher skips proration for non-deductible losses. NOT in eligible.
    assert ids["L5"].tax_advantaged is True
    assert ids["L5"].wash_sale_status == "clear"
    assert ids["L5"].disallowed_loss == 0
    assert "L5" not in r.eligible_lot_ids


def test_build_harvest_aggregates_only_taxable_allowed_losses():
    # ST=$2500 (L2 clear); LT=$1500 (L1 clear) + $2250 (L4 partial)
    # = $3750 LT. Total $6250 — L5's $1500 IRA loss is NOT included.
    r = _build()
    assert r.st_loss_harvested == 2500
    assert r.lt_loss_harvested == 3750
    assert r.total_loss_harvested == 6250


def test_build_harvest_wash_blocked_loss_excludes_tax_advantaged():
    # wash_sale_blocked_loss is just L4's $750 disallowed slice now.
    # L5's $1500 is reported under tax_advantaged_loss instead.
    r = _build()
    assert r.wash_sale_blocked_loss == 750
    assert r.tax_advantaged_loss == 1500
    # Reconciliation invariant: total_loss + blocked + tax_advantaged =
    # all unrealized losses across L1+L2+L4+L5 (L3 is a gain).
    assert (r.total_loss_harvested
            + r.wash_sale_blocked_loss
            + r.tax_advantaged_loss) == 8500


def test_build_harvest_schedule_d_netting_with_tax_advantaged_excluded():
    r = _build()
    # baseline ST gain 3000 - 2500 = 500
    # baseline LT gain 15000 - 3750 = 11250 (L5's $1500 is NOT harvested)
    assert r.new_net_st_gain == 500
    assert r.new_net_lt_gain == 11250
    assert r.capital_loss_used_against_ordinary == 0
    assert r.capital_loss_carryforward == 0


def test_build_harvest_federal_savings_is_positive_and_anchored():
    r = _build()
    assert 0 < r.fed_savings < r.total_loss_harvested
    # harvested_fed_tax == line_24 - fed_savings within the anchor (rounding)
    assert abs(r.baseline_fed_tax - r.harvested_fed_tax - r.fed_savings) < 0.01


def test_build_harvest_federal_savings_reasonable_magnitude():
    # Regular-tax relief: 24% × $2500 ST + 15% × $3750 LT ≈ $1,162.50.
    # NIIT relief: fixture is MFJ at AGI $276K (above $250K threshold). MAGI excess
    # drops $26K → $19,750, NIIT base drops from $26K to $19,750, surtax drops
    # ~$237.50. Total fed_savings ≈ $1,400.
    r = _build()
    assert 1300 <= r.fed_savings <= 1500


def test_build_harvest_state_savings_uses_ca_marginal_rate():
    # CA top bracket at federal-taxable-income ~$246K is 9.3% (141,212–721,318).
    # State savings ≈ 9.3% × $6250 = $581.25; QDCG can shift TI delta a touch.
    r = _build()
    assert r.state_modeled is True
    assert r.state_mtr == 0.093
    assert 520 <= r.state_savings <= 640


def test_build_harvest_total_savings_sums_components():
    r = _build()
    assert abs(r.total_savings - (r.fed_savings + r.state_savings)) < 0.01


def test_build_harvest_state_not_modeled_when_brackets_empty():
    law = _load("tax_law_2026.json")
    law["state"] = {"name": "TX", "brackets": []}  # no income tax
    r = _build(tax_law=law)
    assert r.state_modeled is False
    assert r.state_mtr is None
    assert r.state_savings == 0
    assert r.total_savings == r.fed_savings


def test_build_harvest_lots_sorted_by_value_descending():
    # L5 META is now tax_advantaged → excluded entirely. Eligible order is:
    #   L2 (ST $2500, 33.3%)   → $832
    #   L4 (LT-partial $2250)  → $547
    #   L1 (LT $1500, 24.3%)   → $364
    r = _build()
    eligible_in_order = [l for l in r.lots if l.id in r.eligible_lot_ids]
    assert [l.id for l in eligible_in_order] == ["L2", "L4", "L1"]


# --------------------------------------------------------------------------
# CG-threshold guard (the v1.5 fix for Codex P2: refuse to compute when
# capital_gains_rates is incomplete and preferential income would be silently
# priced at ordinary rates, INFLATING fed_savings)
# --------------------------------------------------------------------------

def test_build_harvest_raises_when_cg_rates_missing_and_lt_cap_gain_present():
    # Fixture baseline has $15K LT cap gain. Stripping capital_gains_rates means
    # the keystone would silently price it at 24% ordinary instead of 15% LTCG,
    # inflating fed_savings by ~$337 on this fixture. The scanner refuses.
    law = _load("tax_law_2026.json")
    law["federal"].pop("capital_gains_rates", None)
    with pytest.raises(lh.LossHarvestError,
                       match="capital_gains_rates|/tax-setup"):
        _build(tax_law=law)


def test_build_harvest_raises_when_only_zero_pct_limit_missing():
    # Either threshold missing trips the guard — partial tax_law is still wrong.
    law = _load("tax_law_2026.json")
    law["federal"]["capital_gains_rates"].pop("zero_pct_limit", None)
    with pytest.raises(lh.LossHarvestError, match="zero_pct_limit"):
        _build(tax_law=law)


def test_build_harvest_raises_when_only_fifteen_pct_limit_missing():
    law = _load("tax_law_2026.json")
    law["federal"]["capital_gains_rates"].pop("fifteen_pct_limit", None)
    with pytest.raises(lh.LossHarvestError, match="fifteen_pct_limit"):
        _build(tax_law=law)


def test_build_harvest_raises_when_only_qualified_div_present_and_cg_missing():
    # Even with NO LT cap gain, qualified dividends alone trigger the QDCG
    # worksheet and would be mispriced at ordinary rates. Guard must fire.
    res = _load("result.json")
    res["form_1040"]["line_7_capital_gain"] = 0
    res["form_1040"]["line_3a_qualified_dividends"] = 8000  # still positive
    res["schedule_d"] = {
        "part_i_short_term": {"line_7_net_short_term": 0},
        "part_ii_long_term": {"line_15_net_long_term": 0},
        "line_16_combined": 0,
    }
    law = _load("tax_law_2026.json")
    law["federal"].pop("capital_gains_rates", None)
    with pytest.raises(lh.LossHarvestError, match="qualified dividends|preferential"):
        lh.build_harvest(res, _load("taxpayer_profile.json"), law,
                         _load("holdings.json"), 2026, name="x", today=TODAY)


def test_build_harvest_allows_missing_cg_when_no_preferential_income():
    # Wages + interest + ST-only lots (no LT cap gain, no qualified div).
    # No preferential income → keystone never enters QDCG worksheet → CG
    # thresholds genuinely don't matter → guard MUST allow the run.
    res = _load("result.json")
    res["form_1040"]["line_3a_qualified_dividends"] = 0
    res["form_1040"]["line_7_capital_gain"] = 0
    res["schedule_d"] = {
        "part_i_short_term": {"line_7_net_short_term": 0},
        "part_ii_long_term": {"line_15_net_long_term": 0},
        "line_16_combined": 0,
    }
    h = _holdings_with([{"id": "ST", "ticker": "X", "account": "A",
                         "acquired": "2026-06-01", "quantity": 10,  # short-term lot
                         "cost_basis": 1000, "current_value": 500, "tax_advantaged": False}])
    law = _load("tax_law_2026.json")
    law["federal"].pop("capital_gains_rates", None)
    # Must succeed — no exception. Returns a real report.
    r = lh.build_harvest(res, _load("taxpayer_profile.json"), law, h, 2026,
                         name="no-pref", today=TODAY)
    assert r.total_loss_harvested == 500
    assert r.fed_savings >= 0


# --------------------------------------------------------------------------
# NIIT relief (§1411) layered outside the keystone (the v1.5 fix for Codex P2)
# --------------------------------------------------------------------------

def test_build_harvest_niit_modeled_when_threshold_present():
    r = _build()  # fixture tax_law has federal.niit.threshold populated
    assert r.niit_modeled is True
    assert r.baseline_niit > 0
    assert r.niit_relief > 0
    assert r.harvested_niit < r.baseline_niit


def test_build_harvest_niit_relief_quantified_for_fixture():
    # MFJ, keystone-computed AGI $276K above $250K threshold:
    #   baseline NII (Roth-ladder formula: interest + ord_div + max(0, LT))
    #     = $2K + $6K + $15K = $23K
    #   baseline MAGI excess = $26K → base = min($23K, $26K) = $23K → NIIT $874
    #   harvested AGI = $269,750 (gross drops by $6,250 of harvested loss),
    #     NII = $2K + $6K + $11,250 = $19,250,
    #     MAGI excess = $19,750 → base = min($19,250, $19,750) = $19,250
    #     → NIIT = $731.50
    #   relief = $874 − $731.50 = $142.50
    r = _build()
    assert abs(r.baseline_niit - 874.00) < 0.5
    assert abs(r.harvested_niit - 731.50) < 0.5
    assert abs(r.niit_relief - 142.50) < 0.5


def test_build_harvest_fed_savings_includes_niit_relief():
    # Anchor invariant + NIIT layering: fed_savings should equal
    # (line24 - harvested_anchored), and harvested_anchored should incorporate
    # the NIIT delta. So fed_savings ≈ regular_relief + niit_relief.
    r = _build()
    regular_relief = r.fed_savings - r.niit_relief
    # Sanity bounds on the regular-tax-only component
    assert 1000 <= regular_relief <= 1300


def test_build_harvest_no_niit_relief_below_threshold():
    # Lower-income fixture: synthesize a low-AGI result and verify NIIT is $0
    # both baseline and harvested (below $250K MFJ threshold).
    res = _load("result.json")
    res["form_1040"]["line_1a_wages"] = 80000
    res["form_1040"]["line_1z_total_wages"] = 80000
    res["form_1040"]["line_11_agi"] = 106000   # well under $250K
    res["form_1040"]["line_24_total_tax"] = 6000
    r = lh.build_harvest(
        res, _load("taxpayer_profile.json"), _load("tax_law_2026.json"),
        _load("holdings.json"), 2026, name="low-income", today=TODAY)
    assert r.niit_modeled is True  # threshold is in tax_law
    assert r.baseline_niit == 0
    assert r.harvested_niit == 0
    assert r.niit_relief == 0


def test_build_harvest_niit_not_modeled_when_threshold_missing():
    # tax_law without niit.threshold → compute_niit returns 0; relief is 0;
    # niit_modeled is False so the renderer shows a banner.
    law = _load("tax_law_2026.json")
    law["federal"].pop("niit", None)
    r = _build(tax_law=law)
    assert r.niit_modeled is False
    assert r.baseline_niit == 0
    assert r.harvested_niit == 0
    assert r.niit_relief == 0


def test_build_harvest_anchor_invariant_with_niit_modeled():
    # No eligible losses → harvested_anchored MUST equal line_24 EXACTLY,
    # even when NIIT layering is wired in. NIIT delta is 0 in this case.
    h = {"as_of": "2026-12-15",
         "lots": [{"id": "g", "ticker": "X", "account": "A",
                   "acquired": "2024-01-01", "quantity": 10,
                   "cost_basis": 100, "current_value": 500, "tax_advantaged": False}]}  # gain, not loss
    r = _build(holdings=h)
    assert r.total_loss_harvested == 0
    assert r.harvested_fed_tax == r.baseline_fed_tax  # == line_24
    assert r.fed_savings == 0
    assert r.harvested_niit == r.baseline_niit  # no marginal effect


def test_render_markdown_names_the_salt_phasedown_it_holds_fixed():
    """A harvest lowers modified AGI, and for an itemizer above the
    §164(b)(7)(B) threshold that raises the SALT cap the keystone holds at the
    baseline, so the harvest can save more than shown. Said while the
    phasedown runs, and only then."""
    r = _build()
    # No Schedule A, and a harvest lowers AGI: not known, so said.
    assert r.salt_cap_may_move is True
    assert lh._report_to_dict(r)["salt_cap_may_move"] is True
    assert "SALT cap is held at the baseline" in lh.render_markdown(r)
    assert "SALT cap is held at the baseline" not in lh.render_markdown(
        dataclasses.replace(r, salt_cap_may_move=False))
    # An itemizer whose MAGI stays far under the threshold: the cap is whole
    # before and after, so there is nothing to say.
    result = _load("result.json")
    agi = result["form_1040"]["line_11_agi"]
    result["schedule_a"] = {"use_itemized": True, "salt_before_cap": 30000,
                            "modified_agi": agi}
    r = lh.build_harvest(result, _load("taxpayer_profile.json"),
                         _load("tax_law_2026.json"), _load("holdings.json"),
                         2026, name="harvest", today=TODAY)
    assert r.salt_cap_may_move is False


def test_render_markdown_splits_fed_savings_when_niit_relief_present():
    r = _build()
    md = lh.render_markdown(r)
    assert "Federal regular-tax relief" in md
    assert "NIIT relief" in md
    assert "Federal tax savings (total)" in md


def test_render_markdown_collapses_fed_savings_when_no_niit_relief():
    # Below-threshold taxpayer → no NIIT relief → single "Federal tax savings" row.
    # extract_baseline_inputs reads wages from line_1z_total_wages, so we must
    # drop the wages too (not just line_11_agi) to push keystone AGI under $250K.
    res = _load("result.json")
    res["form_1040"]["line_1a_wages"] = 80000
    res["form_1040"]["line_1z_total_wages"] = 80000
    res["form_1040"]["line_11_agi"] = 106000
    res["form_1040"]["line_24_total_tax"] = 6000
    r = lh.build_harvest(
        res, _load("taxpayer_profile.json"), _load("tax_law_2026.json"),
        _load("holdings.json"), 2026, name="low", today=TODAY)
    assert r.niit_relief == 0  # sanity: below threshold, no relief
    md = lh.render_markdown(r)
    # The table row split is what we're checking — the caveats section ALSO
    # mentions "NIIT relief" when explaining the concept, so use the table-row
    # marker as the precise signal.
    assert "Federal NIIT relief (§1411" not in md
    assert "Federal regular-tax relief" not in md
    assert "Federal tax savings" in md  # collapsed row still present
    # And the warning banner should not show (threshold IS in tax_law)
    assert "NIIT NOT MODELED" not in md


def test_render_markdown_shows_niit_banner_when_not_modeled():
    law = _load("tax_law_2026.json")
    law["federal"].pop("niit", None)
    r = _build(tax_law=law)
    md = lh.render_markdown(r)
    assert "NIIT NOT MODELED" in md


# --------------------------------------------------------------------------
# Share-level wash-sale proration (the v1.5 fix for Codex P2)
# --------------------------------------------------------------------------

def _holdings_with(lots, txns=None):
    # Proration tests state complete history for every account they name, so
    # what they measure is the matcher, not AA127's hold for missing history.
    accounts = sorted({l["account"] for l in lots}
                      | {t["account"] for t in (txns or []) if t.get("account")})
    return {"as_of": "2026-12-15", "lots": lots,
            "recent_transactions": (txns or []),
            "transaction_coverage": {"accounts": accounts, "from": "2026-11-15",
                                     "through": "2026-12-15"}}


def test_proration_1_share_drip_against_100_share_lot():
    # DRIP buys 1 share of VTSAX inside the window; user wants to harvest the
    # 100-share losing lot. §1091 should disallow 1% of the loss only.
    h = _holdings_with(
        lots=[{"id": "BIG", "ticker": "VTSAX", "account": "A",
               "acquired": "2024-01-01", "quantity": 100,
               "cost_basis": 12000, "current_value": 10000, "tax_advantaged": False}],   # loss $2000
        txns=[{"ticker": "VTSAX", "account": "DRIP",
               "date": "2026-12-01", "kind": "buy", "quantity": 1}],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["BIG"]
    # disallowed = (1/100) × $2000 = $20; allowed = $1980
    assert lot.wash_sale_status == "partial"
    assert lot.disallowed_loss == 20
    assert lot.allowed_loss == 1980
    assert "BIG" in r.eligible_lot_ids


def test_proration_exact_match_quantity_fully_blocks():
    # When replacement_qty == lot.quantity, 100% of the loss is disallowed.
    h = _holdings_with(
        lots=[{"id": "X", "ticker": "ABC", "account": "A",
               "acquired": "2024-01-01", "quantity": 50,
               "cost_basis": 5000, "current_value": 3000, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-10", "kind": "buy", "quantity": 50}],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["X"]
    assert lot.wash_sale_status == "fully_blocked"
    assert lot.disallowed_loss == 2000
    assert lot.allowed_loss == 0
    assert lot.wash_sale_blocked is True
    assert "X" not in r.eligible_lot_ids


def test_proration_oversize_replacement_still_full_block():
    # Replacement qty > lot.quantity → capped at lot.quantity (matched=quantity);
    # no double-disallowance.
    h = _holdings_with(
        lots=[{"id": "X", "ticker": "ABC", "account": "A",
               "acquired": "2024-01-01", "quantity": 50,
               "cost_basis": 5000, "current_value": 3000, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-10", "kind": "buy", "quantity": 200}],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["X"]
    assert lot.wash_sale_status == "fully_blocked"
    assert lot.disallowed_loss == 2000  # capped at full loss; not 4x


def test_proration_missing_quantity_treated_as_inf_full_block():
    # Sparse data fallback: a buy without 'quantity' is +inf in the pool.
    h = _holdings_with(
        lots=[{"id": "X", "ticker": "ABC", "account": "A",
               "acquired": "2024-01-01", "quantity": 50,
               "cost_basis": 5000, "current_value": 3000, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-10", "kind": "buy"}],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["X"]
    assert lot.wash_sale_status == "fully_blocked"
    assert lot.disallowed_loss == 2000


def test_proration_fifo_match_across_two_losing_lots():
    # 50-share replacement pool against two losing lots of the same ticker:
    # 30 shares (older, sold first under FIFO) + 80 shares (newer).
    # FIFO consumes: 30 from lot A (fully blocks $900), 20 from lot B (partial 25%).
    h = _holdings_with(
        lots=[
            {"id": "A", "ticker": "ABC", "account": "A",
             "acquired": "2020-01-01", "quantity": 30,
             "cost_basis": 3000, "current_value": 2100, "tax_advantaged": False},  # loss $900, LT
            {"id": "B", "ticker": "ABC", "account": "A",
             "acquired": "2025-01-01", "quantity": 80,
             "cost_basis": 8000, "current_value": 6000, "tax_advantaged": False},  # loss $2000, ST
        ],
        txns=[{"ticker": "ABC", "date": "2026-12-10", "kind": "buy", "quantity": 50}],
    )
    r = _build(holdings=h)
    ids = {l.id: l for l in r.lots}
    assert ids["A"].wash_sale_status == "fully_blocked"
    assert ids["A"].disallowed_loss == 900
    # B is partial: 20/80 = 25% matched
    assert ids["B"].wash_sale_status == "partial"
    assert ids["B"].disallowed_loss == 500   # 0.25 * $2000
    assert ids["B"].allowed_loss == 1500


def test_proration_no_double_counting_with_multiple_lots_same_ticker():
    # v1 BUG (now fixed): two losing TSLA lots would each see the same 5-share
    # buy and each be fully-blocked, double-counting the disallowance. The fix
    # aggregates the pool ONCE per ticker and distributes it FIFO.
    h = _holdings_with(
        lots=[
            {"id": "old", "ticker": "TSLA", "account": "A",
             "acquired": "2022-01-01", "quantity": 3,
             "cost_basis": 1500, "current_value": 1000, "tax_advantaged": False},   # loss $500, qty 3
            {"id": "new", "ticker": "TSLA", "account": "A",
             "acquired": "2025-01-01", "quantity": 100,
             "cost_basis": 30000, "current_value": 25000, "tax_advantaged": False}, # loss $5000, qty 100
        ],
        txns=[{"ticker": "TSLA", "date": "2026-12-01", "kind": "buy", "quantity": 5}],
    )
    r = _build(holdings=h)
    ids = {l.id: l for l in r.lots}
    # FIFO: 3 shares match against `old` first (fully blocks it), 2 left over
    # match against `new` (2/100 = 2% disallowance).
    assert ids["old"].wash_sale_status == "fully_blocked"
    assert ids["old"].disallowed_loss == 500
    assert ids["new"].wash_sale_status == "partial"
    assert ids["new"].disallowed_loss == 100  # 0.02 * $5000


# --------------------------------------------------------------------------
# Tax-advantaged-account handling (the v1.5 fix for Codex P1, Rev. Rul. 2008-5)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("flag", [{}, {"tax_advantaged": None}])
def test_parse_holdings_requires_tax_advantaged(flag):
    # Account type is unknown even though the buy and the Roth lot share a key.
    # Assuming taxable would both offer the Roth loss and hide its purchase.
    h = _holdings_with(
        lots=[{"id": "roth", "ticker": "X", "account": "Roth",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1100, "current_value": 100, **flag}],
        txns=[{"ticker": "X", "account": "Roth", "date": "2026-12-01",
               "quantity": 10, "account_type": None}],
    )
    with pytest.raises(lh.LossHarvestError, match="lot roth.*tax_advantaged.*boolean"):
        _build(holdings=h)


def test_parse_holdings_rejects_non_bool_tax_advantaged():
    # A truthy string like "yes" must NOT silently become True (or False via
    # Python falsiness on empty string) — this field gates phantom-savings.
    bad = {"lots": [{"id": "x", "ticker": "X", "account": "A",
                     "acquired": "2024-01-01", "quantity": 10,
                     "cost_basis": 100, "current_value": 50,
                     "tax_advantaged": "yes"}]}
    with pytest.raises(lh.LossHarvestError, match="boolean"):
        lh.parse_holdings(bad, today=TODAY)


def test_tax_advantaged_lot_excluded_from_eligible():
    # An IRA lot at a clean unrealized loss (no wash signal) is STILL excluded.
    # The exclusion is unconditional, not contingent on §1091.
    h = _holdings_with(
        lots=[
            {"id": "ira", "ticker": "XYZ", "account": "My Roth IRA",
             "acquired": "2024-01-01", "quantity": 50,
             "cost_basis": 10000, "current_value": 7000,
             "tax_advantaged": True},
            {"id": "tax", "ticker": "ABC", "account": "Schwab",
             "acquired": "2024-01-01", "quantity": 50,
             "cost_basis": 5000, "current_value": 3000, "tax_advantaged": False},  # control: taxable, eligible
        ],
    )
    r = _build(holdings=h)
    assert "ira" not in r.eligible_lot_ids
    assert "tax" in r.eligible_lot_ids
    # The IRA's $3000 loss is accounted for under tax_advantaged_loss, not
    # silently dropped.
    assert r.tax_advantaged_loss == 3000
    assert r.total_loss_harvested == 2000  # only the taxable ABC lot


def test_wash_sale_basis_plan_separates_taxable_and_ira_replacements():
    h = _holdings_with(
        lots=[{"id": "sale", "ticker": "ABC", "account": "Broker",
               "acquired": "2024-01-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 500, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-02", "quantity": 2,
               "account": "Retirement", "account_type": "ira", "owner_is_taxpayer": True},
              {"ticker": "ABC", "date": "2026-12-01", "quantity": 3,
               "account": "Broker", "account_type": "taxable", "cost_basis": 240}])
    before = json.loads(json.dumps(h))
    r = _build(holdings=h)
    lot = next(l for l in r.lots if l.id == "sale")
    # $500 / 10 shares = $50 loss/share; 3 taxable shares defer $150,
    # 2 IRA shares permanently lose $100. Current allowed loss stays $250.
    assert (lot.disallowed_loss, lot.allowed_loss) == (250, 250)
    taxable, ira = lot.wash_sale_adjustments
    assert taxable["replacement_purchase_index"] == 1  # earlier buy matched first
    assert taxable["basis_increase"] == 150
    assert taxable["matched_basis_before"] == 240
    assert taxable["matched_basis_after"] == 390
    assert ira["basis_increase"] == 0 and ira["permanent_loss"] == 100
    assert all(a["complete"] for a in lot.wash_sale_adjustments)
    assert h == before
    assert "replacement basis plan" in lh.render_markdown(r)
    assert lh._report_to_dict(r)["lots"][0]["wash_sale_adjustments"]


def test_basis_plan_consumes_replacement_shares_once_across_lots():
    h = _holdings_with(
        lots=[{"id": "first", "ticker": "ABC", "account": "Broker",
               "acquired": "2023-01-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 500, "tax_advantaged": False},
              {"id": "second", "ticker": "ABC", "account": "Broker",
               "acquired": "2024-01-01", "quantity": 10,
               "cost_basis": 2000, "current_value": 1000, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-01", "quantity": 15,
               "account_type": "taxable", "cost_basis": 1200}])
    r = _build(holdings=h)
    plans = {l.id: l.wash_sale_adjustments[0] for l in r.lots}
    # First 10 shares defer $500; next 5 defer $500. The $1,200 purchase
    # basis splits $800/$400, so matched bases become $1,300/$900.
    assert plans["first"]["matched_basis_after"] == 1300
    assert plans["second"]["matched_basis_after"] == 900
    assert sum(a["matched_quantity"] for a in plans.values()) == 15
    assert sum(a["basis_increase"] for a in plans.values()) == 1000


@pytest.mark.parametrize("kind,quantity", [(None, 5), ("taxable", None),
                                        ("other_tax_advantaged", 5), ("ira", 5)])
def test_missing_replacement_facts_never_claim_future_basis(kind, quantity):
    h = _holdings_with(
        lots=[{"id": "sale", "ticker": "ABC", "account": "Broker",
               "acquired": "2024-01-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 500, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-01", "quantity": quantity,
               "account_type": kind}])
    r = _build(holdings=h)
    a = r.lots[0].wash_sale_adjustments[0]
    assert a["basis_increase"] is None and a["permanent_loss"] is None
    assert not a["complete"]
    assert "Replacement basis is unresolved" in lh.render_markdown(r)


def test_basis_plan_conserves_cents_across_replacement_purchases():
    h = _holdings_with(
        lots=[{"id": "sale", "ticker": "ABC", "account": "Broker",
               "acquired": "2024-01-01", "quantity": 3,
               "cost_basis": 1, "current_value": 0, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": f"2026-12-0{day}", "quantity": 1,
               "account_type": "taxable"} for day in (1, 2, 3)])
    a = _build(holdings=h).lots[0].wash_sale_adjustments
    # Three shares divide a $1 disallowance: carry the rounding cent rather
    # than independently rounding thirds and dropping a cent.
    assert [p["basis_increase"] for p in a] == [.33, .34, .33]


@pytest.mark.parametrize("field,value", [("account_type", "guessed"),
                                        ("cost_basis", True), ("cost_basis", -1),
                                        ("cost_basis", float("nan")),
                                        ("cost_basis", float("inf"))])
def test_invalid_replacement_basis_metadata_refuses(field, value):
    h = _holdings_with(
        lots=[{"id": "sale", "ticker": "ABC", "account": "Broker",
               "acquired": "2024-01-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 500, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-01", "quantity": 5,
               field: value}])
    with pytest.raises(lh.LossHarvestError):
        _build(holdings=h)


def test_replacement_classification_must_agree_with_the_listed_account():
    h = _holdings_with(
        lots=[{"id": "sale", "ticker": "ABC", "account": "Broker",
               "acquired": "2024-01-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 500, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-01", "quantity": 5,
               "account": "Broker", "account_type": "ira"}])
    with pytest.raises(lh.LossHarvestError, match="conflicts"):
        _build(holdings=h)


def test_tax_advantaged_lot_excluded_even_with_wash_signal():
    # A wash buy of the same IRA-held ticker does NOT bring the IRA lot into
    # blocked or eligible — it stays "clear" because the loss isn't deductible
    # to begin with (no §1091 disallowance is meaningful).
    h = _holdings_with(
        lots=[{"id": "ira", "ticker": "XYZ", "account": "Trad IRA",
               "acquired": "2024-01-01", "quantity": 50,
               "cost_basis": 10000, "current_value": 7000,
               "tax_advantaged": True}],
        txns=[{"ticker": "XYZ", "date": "2026-12-01", "kind": "buy",
               "quantity": 10}],
    )
    r = _build(holdings=h)
    ids = {l.id: l for l in r.lots}
    assert ids["ira"].wash_sale_status == "clear"
    assert ids["ira"].disallowed_loss == 0
    assert r.wash_sale_blocked_loss == 0
    # The full IRA loss flows to tax_advantaged_loss.
    assert r.tax_advantaged_loss == 3000


def test_rev_rul_2008_5_ira_buy_poisons_taxable_harvest():
    # Rev. Rul. 2008-5: a buy in an IRA within ±30 days of a taxable sale at
    # a loss IS a wash sale (even though the IRA's own losses aren't deductible).
    # The pool must include IRA buys; the disallowance applies to the TAXABLE lot.
    h = _holdings_with(
        lots=[{"id": "tax", "ticker": "ZZZ", "account": "Schwab",
               "acquired": "2024-01-01", "quantity": 20,
               "cost_basis": 4000, "current_value": 2000, "tax_advantaged": False}],   # $2000 LT loss, taxable
        txns=[
            # IRA buy of 5 shares 10d before the harvest — STILL trips §1091
            # on the taxable lot per Rev. Rul. 2008-5.
            {"ticker": "ZZZ", "account": "Vanguard IRA",
             "date": "2026-12-05", "kind": "buy", "quantity": 5},
        ],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["tax"]
    # 5/20 = 25% matched → disallowed $500; allowed $1500. Partial → eligible.
    assert lot.wash_sale_status == "partial"
    assert lot.disallowed_loss == 500
    assert lot.allowed_loss == 1500
    assert "tax" in r.eligible_lot_ids


def test_render_markdown_includes_tax_advantaged_section_when_present():
    r = _build()  # fixture has L5 in Fidelity IRA
    md = lh.render_markdown(r)
    assert "Tax-advantaged account positions" in md
    assert "no deductible loss" in md
    assert "META" in md
    assert "Fidelity IRA" in md


def test_render_markdown_omits_tax_advantaged_section_when_no_such_lots():
    # All-taxable holdings → the tax-advantaged section should not appear at all.
    h = _holdings_with(
        lots=[{"id": "x", "ticker": "X", "account": "Schwab",
               "acquired": "2024-01-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 500, "tax_advantaged": False}],
    )
    r = _build(holdings=h)
    md = lh.render_markdown(r)
    assert "Tax-advantaged account positions" not in md


# --------------------------------------------------------------------------
# Lot-aware pool aggregation (v1.5 fix for Codex P2: a lot's own acquisition
# is NOT a replacement for the lot itself — the shares being sold can't be
# their own §1091 replacement)
# --------------------------------------------------------------------------

def test_self_acquisition_in_window_not_counted_as_replacement():
    # The textbook simple harvest: buy 10 ABC on 12-01 in Schwab, harvest on 12-15.
    # The brokerage CSV's record of the 12-01 buy (same account) must NOT block
    # the lot — those 10 shares ARE the lot being sold.
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
               "kind": "buy", "quantity": 10}],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "clear"
    assert lot.disallowed_loss == 0
    assert lot.allowed_loss == 300
    assert "A" in r.eligible_lot_ids


def test_buy_qty_exceeds_lot_qty_excess_blocks():
    # 15-share buy on 12-01 in Schwab: lot A=10 (harvest) + B=5 (held at gain),
    # both in Schwab. The 5 excess shares ARE replacements for A's sale.
    h = _holdings_with(
        lots=[
            {"id": "A", "ticker": "ABC", "account": "Schwab",
             "acquired": "2026-12-01", "quantity": 10,
             "cost_basis": 1000, "current_value": 700, "tax_advantaged": False},
            {"id": "B", "ticker": "ABC", "account": "Schwab",
             "acquired": "2026-12-01", "quantity": 5,
             "cost_basis": 500, "current_value": 600, "tax_advantaged": False},  # at a GAIN, still held
        ],
        txns=[{"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
               "kind": "buy", "quantity": 15}],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "partial"
    assert lot.disallowed_loss == 150  # 50% of $300
    assert lot.allowed_loss == 150


def test_separate_post_acquisition_buy_still_blocks():
    # Both buys in Schwab: lot's own 10-share buy + separate 3-share buy 7 days
    # later. Lot is partial-blocked by the 3 genuine replacement shares.
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 10},
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-08",
             "kind": "buy", "quantity": 3},
        ],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "partial"
    assert lot.disallowed_loss == 90   # 30% of $300
    assert lot.allowed_loss == 210


def test_inf_qty_buy_matching_lot_acquisition_assumed_self():
    # Sparse-data heuristic, NARROWED by account match: a qty-less buy whose
    # (date, ticker, account) matches a harvest lot's acquisition is assumed
    # to BE that lot's acquisition (contribution 0).
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "account": "Schwab",
               "date": "2026-12-01", "kind": "buy"}],  # qty None
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "clear"
    assert lot.allowed_loss == 300


def test_inf_qty_buy_not_matching_lot_still_full_blocks():
    # Quantity-less buy whose date does NOT match any harvest lot — genuine
    # unknown replacement. Stays inf → full block.
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2024-01-01", "quantity": 10,  # OUT of window
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "account": "Schwab",
               "date": "2026-12-05", "kind": "buy"}],  # qty None
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "fully_blocked"


def test_self_acquisition_does_not_exclude_other_tickers():
    # Per-(ticker, date, account): a buy of DEF on the same date+account as
    # ABC lot's acquisition still blocks DEF lots normally.
    h = _holdings_with(
        lots=[
            {"id": "A", "ticker": "ABC", "account": "Schwab",
             "acquired": "2026-12-01", "quantity": 10,
             "cost_basis": 1000, "current_value": 700, "tax_advantaged": False},
            {"id": "D", "ticker": "DEF", "account": "Schwab",
             "acquired": "2020-01-01", "quantity": 5,  # acquired out of window
             "cost_basis": 1000, "current_value": 700, "tax_advantaged": False},
        ],
        txns=[
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 10},
            {"ticker": "DEF", "account": "Schwab", "date": "2026-12-05",
             "kind": "buy", "quantity": 5},  # genuine replacement for D
        ],
    )
    r = _build(holdings=h)
    ids = {l.id: l for l in r.lots}
    assert ids["A"].wash_sale_status == "clear"   # self-acquisition excluded
    assert ids["D"].wash_sale_status == "fully_blocked"  # 5/5 = 100% blocked


def test_tax_advantaged_lot_does_not_earn_self_acquisition_exclusion():
    # IRA buy with matching account as IRA lot, but IRA lot is tax_advantaged
    # → excluded from own_qty filter. Buy fully contributes to pool against
    # the taxable lot (Rev. Rul. 2008-5: IRA buys ARE replacements for taxable).
    h = _holdings_with(
        lots=[
            {"id": "ira", "ticker": "ABC", "account": "Fidelity IRA",
             "acquired": "2026-12-01", "quantity": 5,
             "cost_basis": 500, "current_value": 300,
             "tax_advantaged": True},
            {"id": "tax", "ticker": "ABC", "account": "Schwab",
             "acquired": "2024-01-01", "quantity": 10,   # acquired out of window
             "cost_basis": 1000, "current_value": 700, "tax_advantaged": False},
        ],
        txns=[{"ticker": "ABC", "account": "Fidelity IRA",
               "date": "2026-12-01", "kind": "buy", "quantity": 5}],
    )
    r = _build(holdings=h)
    ids = {l.id: l for l in r.lots}
    assert ids["tax"].wash_sale_status == "partial"
    assert ids["tax"].disallowed_loss == 150  # 50% of $300


# --- Account-aware self-exclusion (v1.5 fix for Codex P1) ---

def test_cross_account_buy_on_same_date_does_not_earn_self_exclusion():
    # Codex's canonical P1 case: taxable lot in Schwab + spouse buy of 5 ABC
    # on the SAME day in Spouse Schwab. The spouse buy is a GENUINE §1091
    # replacement (Rev. Rul. 2008-5 + spousal-account rules); it MUST NOT be
    # absorbed by the taxable lot's own-acquisition exclusion.
    h = _holdings_with(
        lots=[{"id": "tax", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 10},  # taxable lot's own
            {"ticker": "ABC", "account": "Spouse Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 5},   # cross-account replacement
        ],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["tax"]
    # Schwab buy excluded (matches account); spouse 5 shares are the genuine
    # replacement → 5/10 = 50% disallowed.
    assert lot.wash_sale_status == "partial"
    assert lot.disallowed_loss == 150  # 50% of $300
    assert lot.allowed_loss == 150


def test_inf_qty_cross_account_buy_stays_full_block():
    # Qty-less buy in a DIFFERENT account on the same date as a Schwab lot's
    # acquisition must NOT trigger the sparse-data self heuristic — the
    # heuristic is scoped to same-account buys.
    h = _holdings_with(
        lots=[{"id": "tax", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "account": "Spouse Schwab",
               "date": "2026-12-01", "kind": "buy"}],  # qty None
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["tax"]
    assert lot.wash_sale_status == "fully_blocked"


def test_buy_without_account_does_not_earn_self_exclusion():
    # The schema requires non-empty `account` on lots, so a buy txn without an
    # `account` field can NEVER be matched to a lot for self-exclusion (lot
    # account != "" by construction). Conservative behavior: buy fully
    # contributes to the pool, blocking the lot. Users wanting self-exclusion
    # MUST record account on buy txns — brokerage CSV exports always have it.
    # This direction is correct per the bias-direction taxonomy: an unmatched
    # buy that's actually self over-blocks (under-recommends, recoverable);
    # an unmatched buy that's a real cross-account replacement correctly blocks.
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-01",
               "kind": "buy", "quantity": 10}],  # no account field
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "fully_blocked"
    assert lot.disallowed_loss == 300


# --- Budget consumption across same-(date, account) buys (v1.5 fix for Codex P2#10) ---

def test_same_date_split_fill_consumes_budget_once():
    # Codex's canonical case: lot's own 10-share buy + a separate same-day
    # 5-share buy in the same account. Pre-fix both buys independently
    # subtracted the full 10-share lot quantity → pool=0 → false-clear.
    # Post-fix: budget=10 consumed by the 10-share buy → 5-share buy now
    # contributes its full quantity → 50% disallowed.
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 10},  # lot's own acquisition
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 5},   # separate same-day replacement
        ],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "partial"
    assert lot.disallowed_loss == 150  # 50% of $300
    assert lot.allowed_loss == 150


def test_same_date_known_then_inf_blocks_fully():
    # When known-qty buys exhaust the budget, a same-group qty-less buy
    # is a genuine unknown-qty replacement → full block. Order-independent
    # because group is sorted qty-known-first.
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 10},
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy"},  # qty unknown
        ],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "fully_blocked"


def test_same_date_inf_beside_a_known_buy_is_an_unknown_replacement():
    # Budget 10, a known buy of 5, then a quantity-less buy. Filling the rest
    # of the budget with it guessed which buy was the lot: beside an older lot,
    # the known buy's shares were the replacement and the plan over-stated.
    # The quantity is the filer's to supply.
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 5},
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy"},  # qty unknown
        ],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "fully_blocked" and "qty unknown" in lot.wash_sale_reason
    h["recent_transactions"][1]["quantity"] = 5
    assert {l.id: l for l in _build(holdings=h).lots}["A"].wash_sale_status == "clear"


def test_multi_lot_same_group_shared_budget():
    # Two harvest lots share the same (date, account) — budget = A.qty + B.qty.
    # Single 20-share buy: contribution = 20 - 15 = 5 → pool 5 distributed FIFO
    # across A (oldest order, here same date so by lot.id semantics; A=10 →
    # consumes 5, B=5 → consumes 0). A 50% blocked, B clear.
    h = _holdings_with(
        lots=[
            {"id": "A", "ticker": "ABC", "account": "Schwab",
             "acquired": "2026-12-01", "quantity": 10,
             "cost_basis": 1000, "current_value": 700, "tax_advantaged": False},  # loss $300
            {"id": "B", "ticker": "ABC", "account": "Schwab",
             "acquired": "2026-12-01", "quantity": 5,
             "cost_basis": 500, "current_value": 350, "tax_advantaged": False},   # loss $150
        ],
        txns=[{"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
               "kind": "buy", "quantity": 20}],  # 15 self + 5 replacement
    )
    r = _build(holdings=h)
    ids = {l.id: l for l in r.lots}
    # Budget=15 consumed by 20-share buy → contribution=5; FIFO distributes
    # 5 against A first (qty 10) → A 50% blocked; B clear.
    assert ids["A"].wash_sale_status == "partial"
    assert ids["A"].disallowed_loss == 150  # 50% of $300
    assert ids["B"].wash_sale_status == "clear"


def test_account_match_case_insensitive():
    # Schwab Brokerage vs schwab brokerage should be treated as the same
    # account for self-exclusion purposes (strip+lowercase normalization).
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab Brokerage",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "account": "  schwab brokerage  ",
               "date": "2026-12-01", "kind": "buy", "quantity": 10}],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "clear"


def test_a_fully_blocked_lot_holds_back_a_later_lot_its_purchase_could_replace():
    h = _holdings_with(
        lots=[
            {"id": "first",  "ticker": "ZZZ", "account": "A",
             "acquired": "2020-01-01", "quantity": 10,
             "cost_basis": 1000, "current_value": 500, "tax_advantaged": False},
            {"id": "second", "ticker": "ZZZ", "account": "A",
             "acquired": "2025-01-01", "quantity": 10,
             "cost_basis": 1000, "current_value": 500, "tax_advantaged": False},
        ],
        # 10-share replacement pool exactly absorbed by the first lot.
        txns=[{"ticker": "ZZZ", "date": "2026-12-10", "kind": "buy", "quantity": 10}],
    )
    r = _build(holdings=h)
    ids = {l.id: l for l in r.lots}
    assert ids["first"].wash_sale_status == "fully_blocked"
    assert ids["second"].wash_sale_status == "clear"
    assert ids["second"].disallowed_loss == 0
    # The plan does not sell the blocked lot, so the ten shares it absorbed
    # would replace the second lot's shares instead. Selling only the second
    # lot, as the plan says, would lose its loss: hold it, never harvest it.
    assert r.eligible_lot_ids == []
    assert r.unverified_loss == 500 and r.total_savings == 0
    assert "also matched lot first" in ids["second"].classification_gaps[0]


def test_build_harvest_excludes_gains_from_eligible_set():
    r = _build()
    # L3 is the only gain lot
    assert "L3" not in r.eligible_lot_ids


def test_build_harvest_zero_eligible_when_no_losses():
    # All gains, no losses
    h = {
        "as_of": "2026-12-15",
        "lots": [{"id": "G1", "ticker": "X", "account": "A",
                  "acquired": "2024-01-01", "quantity": 10,
                  "cost_basis": 100, "current_value": 500, "tax_advantaged": False}],
    }
    r = _build(holdings=h)
    assert r.total_loss_harvested == 0
    assert r.fed_savings == 0
    assert r.state_savings == 0
    assert r.eligible_lot_ids == []


def test_build_harvest_loss_exceeds_gains_triggers_3k_cap_and_carryforward():
    # No baseline gains — synthesize a result.json fixture with zero Sched D.
    h = _holdings_with([{"id": "BIG", "ticker": "ZZZ", "account": "A",
                         "acquired": "2020-01-01", "quantity": 100,
                         "cost_basis": 60000, "current_value": 50000, "tax_advantaged": False}])  # LT loss 10000
    res = _load("result.json")
    res["form_1040"]["line_7_capital_gain"] = 0
    res["schedule_d"] = {
        "part_i_short_term": {"line_7_net_short_term": 0},
        "part_ii_long_term": {"line_15_net_long_term": 0},
        "line_16_combined": 0,
    }
    r = lh.build_harvest(
        res, _load("taxpayer_profile.json"), _load("tax_law_2026.json"),
        h, 2026, name="cap-test", today=TODAY)
    # Net loss 10000: $3000 used against ordinary, $7000 carries forward
    assert r.capital_loss_used_against_ordinary == 3000
    assert r.capital_loss_carryforward == 7000


def test_build_harvest_mfs_uses_1500_loss_cap():
    h = _holdings_with([{"id": "BIG", "ticker": "ZZZ", "account": "A",
                         "acquired": "2020-01-01", "quantity": 100,
                         "cost_basis": 60000, "current_value": 50000,
                         "tax_advantaged": False, "owner": "taxpayer"}])
    res = _load("result.json")
    res["meta"]["filing_status"] = "MFS"
    res["form_1040"]["line_7_capital_gain"] = 0
    res["schedule_d"] = {
        "part_i_short_term": {"line_7_net_short_term": 0},
        "part_ii_long_term": {"line_15_net_long_term": 0},
        "line_16_combined": 0,
    }
    profile = _load("taxpayer_profile.json")
    profile["filing_status"] = "MFS"
    law = _load("tax_law_2026.json")
    r = lh.build_harvest(res, profile, law, h, 2026, name="mfs-cap", today=TODAY)
    assert r.capital_loss_used_against_ordinary == 1500
    assert r.capital_loss_carryforward == 8500


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

def test_build_harvest_rejects_wrong_tax_year():
    # Year mismatch is caught by the reused scenario.read_tax_year guard.
    from agents.lib import scenario as scn
    res = _load("result.json")
    res["meta"]["tax_year"] = 2025
    with pytest.raises((lh.LossHarvestError, scn.ScenarioError), match="2025"):
        lh.build_harvest(res, _load("taxpayer_profile.json"),
                         _load("tax_law_2026.json"), _load("holdings.json"),
                         2026, name="x", today=TODAY)


def test_build_harvest_rejects_missing_line_24():
    from agents.lib import scenario as scn
    res = _load("result.json")
    del res["form_1040"]["line_24_total_tax"]
    with pytest.raises((lh.LossHarvestError, scn.ScenarioError),
                       match="line_24|run /tax-compute"):
        lh.build_harvest(res, _load("taxpayer_profile.json"),
                         _load("tax_law_2026.json"), _load("holdings.json"),
                         2026, name="x", today=TODAY)


def test_build_harvest_rejects_invalid_name():
    with pytest.raises(Exception):  # ScenarioError or LossHarvestError
        lh.build_harvest(_load("result.json"), _load("taxpayer_profile.json"),
                         _load("tax_law_2026.json"), _load("holdings.json"),
                         2026, name="../bad", today=TODAY)


# --------------------------------------------------------------------------
# markdown rendering
# --------------------------------------------------------------------------

def test_render_markdown_includes_key_sections():
    r = _build()
    md = lh.render_markdown(r)
    assert "# Tax-Loss Harvest" in md
    assert "harvest" in md.lower()
    assert "Wash sale" in md or "Wash-sale" in md
    assert "Bottom line" in md
    assert "Caveats" in md


def test_render_markdown_warns_when_state_not_modeled():
    law = _load("tax_law_2026.json")
    law["state"] = {"brackets": []}
    r = _build(tax_law=law)
    md = lh.render_markdown(r)
    assert "state" in md.lower()
    assert "not modeled" in md.lower()


def test_render_markdown_flags_wash_blocked_lots():
    r = _build()
    md = lh.render_markdown(r)
    # The blocked lots should be visible to the user as a discrete section / row
    assert "TSLA" in md
    assert "META" in md


# --------------------------------------------------------------------------
# CLI integration
# --------------------------------------------------------------------------

def test_cli_writes_scenario_and_chmod_600(tmp_path):
    ws = _ws(tmp_path)
    out = subprocess.run(
    # `sys.executable`, never a bare "python3" off PATH. The engine refuses
    # to import on an interpreter that is not the workspace venv, so a PATH
    # lookup made these pass or fail on whether the shell had been
    # activated: ten of them failed under `.venv/bin/python -m pytest` on a
    # tree that was green under an activated shell.
        [sys.executable, "-m", "agents.lib.loss_harvest",
         "--result", "result.json", "--workspace", str(ws),
         "--year", "2026", "--holdings", "holdings.json",
         "--name", "cli-test", "--today", "2026-12-15", "--json"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["schema"] == "tax-harvest/v1"
    saved = ws / "scenarios" / "cli-test.json"
    assert saved.exists()
    mode = saved.stat().st_mode & 0o777
    assert mode == 0o600


def test_cli_refuses_path_traversal_name(tmp_path):
    ws = _ws(tmp_path)
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.loss_harvest",
         "--result", "result.json", "--workspace", str(ws),
         "--year", "2026", "--holdings", "holdings.json",
         "--name", "../escape", "--today", "2026-12-15"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert out.returncode != 0
    assert "Invalid" in out.stderr or "name" in out.stderr.lower()


def test_cli_no_save_skips_write(tmp_path):
    ws = _ws(tmp_path)
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.loss_harvest",
         "--result", "result.json", "--workspace", str(ws),
         "--year", "2026", "--holdings", "holdings.json",
         "--name", "no-save-test", "--no-save", "--today", "2026-12-15"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr
    assert not (ws / "scenarios" / "no-save-test.json").exists()


def test_cli_markdown_output(tmp_path):
    ws = _ws(tmp_path)
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.loss_harvest",
         "--result", "result.json", "--workspace", str(ws),
         "--year", "2026", "--holdings", "holdings.json",
         "--name", "md-test", "--today", "2026-12-15"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr
    assert "# Tax-Loss Harvest" in out.stdout


def test_cli_rejects_bad_year(tmp_path):
    ws = _ws(tmp_path)
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.loss_harvest",
         "--result", "result.json", "--workspace", str(ws),
         "--year", "abc", "--holdings", "holdings.json",
         "--today", "2026-12-15"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert out.returncode != 0


def test_cli_rejects_holdings_outside_workspace(tmp_path):
    ws = _ws(tmp_path)
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.loss_harvest",
         "--result", "result.json", "--workspace", str(ws),
         "--year", "2026", "--holdings", "/etc/passwd",
         "--today", "2026-12-15"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert out.returncode != 0


def test_render_surfaces_1091d_basis_shift_for_fully_blocked():
    """O3: a fully wash-sale-blocked lot's render must say the disallowed loss
    is DEFERRED (added to replacement-share basis per §1091(d), holding period
    tacks per §1223), not lost — with the IRA permanent-loss exception."""
    h = _holdings_with(
        lots=[{"id": "X", "ticker": "ABC", "account": "A",
               "acquired": "2024-01-01", "quantity": 50,
               "cost_basis": 5000, "current_value": 3000, "tax_advantaged": False}],
        txns=[{"ticker": "ABC", "date": "2026-12-10", "kind": "buy", "quantity": 50}],
    )
    md = lh.render_markdown(_build(holdings=h))
    assert "DEFERRED, not lost" in md
    assert "§1091(d)" in md
    assert "§1223" in md
    assert "PERMANENTLY lost" in md  # the IRA exception


# --------------------------------------------------------------------------
# state_no_income_tax — type-aware harvest banner (no-tax != not-fetched)
# --------------------------------------------------------------------------

class TestStateNoIncomeTaxHelper:
    def test_type_none_is_true(self):
        from agents.lib.loss_harvest import state_no_income_tax
        assert state_no_income_tax({"state": {"type": "none"}}) is True

    def test_missing_type_is_false(self):
        # Absence of the discriminator must NOT read as "no tax" — every
        # pre-existing cache lacks the field.
        from agents.lib.loss_harvest import state_no_income_tax
        assert state_no_income_tax({"state": {"brackets": []}}) is False
        assert state_no_income_tax({"state": {}}) is False
        assert state_no_income_tax({}) is False

    def test_graduated_income_is_false(self):
        from agents.lib.loss_harvest import state_no_income_tax
        assert state_no_income_tax({"state": {"type": "graduated_income"}}) is False


def test_render_markdown_shows_no_income_tax_banner():
    # Round-22 P2: reaching the affirmative banner requires the full
    # residence chain — FL resident + code-matching type-none cache.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL"}
    r = _build(tax_law=law, profile=_fl_resident_profile())
    md = lh.render_markdown(r)
    assert "no individual income tax" in md
    assert "correct and complete" in md
    assert "State savings not modeled" not in md


def test_report_to_dict_serializes_state_no_income_tax():
    # FL-type law: the JSON artifact must carry the discriminator so a
    # JSON-only consumer (not just the rendered markdown) can distinguish
    # "no income tax, correctly $0" from "state data never fetched".
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL"}
    r = _build(tax_law=law, profile=_fl_resident_profile())
    d = lh._report_to_dict(r)
    assert "state_no_income_tax" in d
    assert d["state_no_income_tax"] is True

    # Absent type (pre-existing unfetched cache) must serialize False.
    r2 = _build()  # default fixture tax_law has no "type" key
    d2 = lh._report_to_dict(r2)
    assert "state_no_income_tax" in d2
    assert d2["state_no_income_tax"] is False


def test_render_markdown_bottom_line_no_income_tax_suffix():
    # Round-22 P2: full residence chain needed for the affirmative suffix.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL"}
    r = _build(tax_law=law, profile=_fl_resident_profile())
    md = lh.render_markdown(r)
    assert "(not modeled)" not in md
    assert "(no state income tax)" in md


def test_render_markdown_bottom_line_still_shows_not_modeled_when_unfetched():
    # Regression guard: an unfetched (brackets-empty, no type) cache must
    # keep the original "(not modeled)" bottom-line suffix.
    law = _load("tax_law_2026.json")
    law["state"] = {"brackets": []}
    r = _build(tax_law=law)
    md = lh.render_markdown(r)
    assert "(not modeled)" in md
    assert "(no state income tax)" not in md


# --------------------------------------------------------------------------
# round-6 P2: state_no_income_tax cross-checked against resident state
# --------------------------------------------------------------------------

def test_state_no_income_tax_false_on_known_mismatch():
    # A stale FL type="none" cache left beside a CA taxpayer's workspace
    # must NOT claim "$0 — correct and complete": the flag stays False and
    # a loud state_cache_mismatch note appears instead.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL"}
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "CA"}
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="mismatch",
                         today=TODAY)
    assert r.state_no_income_tax is False
    assert r.state_cache_mismatch is not None
    assert "FL" in r.state_cache_mismatch and "CA" in r.state_cache_mismatch
    md = lh.render_markdown(r)
    assert "State cache mismatch" in md
    assert "correct and complete" not in md
    d = lh._report_to_dict(r)
    assert d["state_no_income_tax"] is False
    assert d["state_cache_mismatch"] == r.state_cache_mismatch


def test_state_no_income_tax_true_when_cache_matches_resident():
    # Regression: FL taxpayer + matching FL type="none" cache still gets the
    # true no-tax banner, with no mismatch note.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL"}
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "FL"}
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="match",
                         today=TODAY)
    assert r.state_no_income_tax is True
    assert r.state_cache_mismatch is None
    md = lh.render_markdown(r)
    assert "correct and complete" in md
    assert "State cache mismatch" not in md


def test_state_marginal_rate_ignores_leftover_brackets_when_type_none():
    # Codex round-12 P1 (projection's project_state_tax twin): check_
    # freshness (round 11) flags a type-none cache carrying real leftover
    # brackets as a contradiction, but this function reads the cache
    # directly and never calls check_freshness — a code: "FL", type: "none"
    # cache with leftover CA brackets must NOT have _ordinary_marginal_rate
    # derive a rate from them. type == "none" wins unconditionally.
    from agents.lib.loss_harvest import _state_marginal_rate
    law = {"state": {"type": "none", "code": "FL",
                     "brackets": [{"rate": 0.093, "min": 0, "max": None}]}}
    modeled, rate = _state_marginal_rate(law, "MFJ", 100000)
    assert modeled is False
    assert rate is None


def test_state_marginal_rate_ignores_leftover_brackets_when_type_graduated_income():
    # Codex round-15 P2: the INVERSE contradiction — a code that IS a
    # legitimate no-tax state (FL) but typed "graduated_income" while
    # retaining real leftover brackets must not have this function derive
    # a rate from them either. Round-12 only guarded the type == "none"
    # direction.
    from agents.lib.loss_harvest import _state_marginal_rate
    law = {"tax_year": 2026, "state": {"type": "graduated_income", "code": "FL",
                                       "brackets": [{"rate": 0.093, "min": 0, "max": None}]}}
    modeled, rate = _state_marginal_rate(law, "MFJ", 100000)
    assert modeled is False
    assert rate is None


def test_resolve_state_no_income_tax_inverse_contradiction_is_mismatch():
    # End-to-end companion via the resolver: code says FL (legitimate
    # no-tax) but type says graduated_income with real brackets -> must
    # surface a loud mismatch, not a silent no_income_tax=False/None.
    from agents.lib.loss_harvest import _resolve_state_no_income_tax
    law = {"tax_year": 2026, "state": {"type": "graduated_income", "code": "FL",
                                       "brackets": [{"rate": 0.093, "min": 0, "max": None}]}}
    no_income_tax, mismatch = _resolve_state_no_income_tax(law, "FL", 2026)
    assert no_income_tax is False
    assert mismatch is not None
    assert "FL" in mismatch


def test_build_harvest_fl_with_leftover_brackets_stays_no_income_tax():
    # End-to-end companion: build_harvest must still resolve to "$0 —
    # correct and complete" (a legitimate FL no-tax code/year combo), not
    # fabricated state savings from the contradictory leftover brackets.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL",
                    "brackets": [{"rate": 0.093, "min": 0, "max": None}],
                    "standard_deduction": {"mfj": 11080}}
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "FL"}
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="flleftover",
                         today=TODAY)
    assert r.state_modeled is False
    assert r.state_no_income_tax is True
    assert r.state_cache_mismatch is None
    assert r.state_mtr is None
    md = lh.render_markdown(r)
    assert "correct and complete" in md
    assert "State cache mismatch" not in md


def test_resolve_state_no_income_tax_malformed_code_is_mismatch():
    # Codex round-14 P2 (projection's twin fix): a PRESENT-but-INVALID
    # state.code ("FLA") must not collapse to the same "unknown, safe to
    # let brackets through" None a genuinely ABSENT code produces.
    from agents.lib.loss_harvest import _resolve_state_no_income_tax
    law = {"tax_year": 2026, "state": {
        "code": "FLA", "brackets": [{"rate": 0.093, "min": 0, "max": None}]}}
    no_income_tax, mismatch = _resolve_state_no_income_tax(law, "FL", 2026)
    assert no_income_tax is False
    assert mismatch is not None
    assert "FLA" in mismatch


def test_resolve_state_no_income_tax_explicit_null_code_is_mismatch():
    # Codex round-15 P2: an EXPLICIT "code": null (key present, value None)
    # must not collapse to the same "absent, safe to let brackets through"
    # path a genuinely code-less legacy cache gets.
    from agents.lib.loss_harvest import _resolve_state_no_income_tax
    law = {"tax_year": 2026, "state": {
        "code": None, "brackets": [{"rate": 0.093, "min": 0, "max": None}]}}
    no_income_tax, mismatch = _resolve_state_no_income_tax(law, "FL", 2026)
    assert no_income_tax is False
    assert mismatch is not None


def test_build_harvest_malformed_cache_code_surfaces_mismatch():
    # End-to-end companion: build_harvest must NOT fabricate state harvest
    # savings from CA's real brackets under a "FLA"-corrupted code — the
    # cache-code-mismatch check must fire before _state_marginal_rate runs.
    law = _load("tax_law_2026.json")  # real CA graduated brackets
    law["state"]["code"] = "FLA"
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "FL"}
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="malformedcode",
                         today=TODAY)
    assert r.state_modeled is False
    assert r.state_no_income_tax is False
    assert r.state_cache_mismatch is not None
    assert "FLA" in r.state_cache_mismatch
    md = lh.render_markdown(r)
    assert "correct and complete" not in md


def test_state_no_income_tax_unknown_resident_degrades_not_affirms():
    # Codex round-22 P2 (REPLACES the pre-round-22 "legacy cache-only"
    # behavior this test used to pin): "$0 — correct and complete" is an
    # affirmative claim about the TAXPAYER's state, and with no
    # address.state there is no one to attribute the cache's type-none
    # declaration to — a stale FL cache beside a profile whose address
    # block was lost must NOT produce the confident claim (the taxpayer
    # may live in CA). Mirrors projection._resolve_state_no_income_tax,
    # which returns (False, None, None) in the same scenario. The degrade
    # stays QUIET (no mismatch warning) — a missing address is not
    # evidence of cache corruption.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL"}
    profile = _load("taxpayer_profile.json")
    assert profile.get("address") is None  # fixture precondition
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="unknown",
                         today=TODAY)
    assert r.state_no_income_tax is False
    assert r.state_cache_mismatch is None
    md = lh.render_markdown(r)
    assert "correct and complete" not in md
    assert "State cache mismatch" not in md


def test_build_harvest_year_mismatch_hard_errors():
    # Round-9 P2 introduced this check inside _resolve_state_no_income_tax;
    # round-11 P2 found that _rate_kwargs/compute_scenario already consume
    # the (possibly wrong-year) cache's FEDERAL brackets/thresholds before
    # that check ever runs, so fed_savings would be silently wrong while
    # only a STATE-labeled warning appeared. build_harvest now hard-errors
    # on a tax_year mismatch immediately after read_tax_year, before any
    # rate derivation — e.g. a tax_law_2026.json body that actually says
    # "tax_year": 2020 must not silently price a TY2026 harvest off 2020
    # rates.
    law = _load("tax_law_2026.json")
    law["tax_year"] = 2020
    law["state"] = {"type": "none", "code": "FL"}
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "FL"}
    with pytest.raises(lh.LossHarvestError, match="tax_year"):
        lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="yearmismatch",
                         today=TODAY)


def test_build_harvest_year_mismatch_hard_errors_unknown_resident():
    # The resident-unknown branch inside _resolve_state_no_income_tax
    # normally trusts cache_says_none on its own (legacy behavior) — but
    # build_harvest's top-level guard fires before that function is ever
    # reached, regardless of whether the resident state is known.
    law = _load("tax_law_2026.json")
    law["tax_year"] = 2020
    law["state"] = {"type": "none", "code": "FL"}
    profile = _load("taxpayer_profile.json")
    assert profile.get("address") is None  # fixture precondition
    with pytest.raises(lh.LossHarvestError, match="tax_year"):
        lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="yearmismatch2",
                         today=TODAY)


def test_build_harvest_missing_tax_year_hard_errors():
    law = _load("tax_law_2026.json")
    del law["tax_year"]
    law["state"] = {"type": "none", "code": "FL"}
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "FL"}
    with pytest.raises(lh.LossHarvestError, match="tax_year"):
        lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="notaxyear",
                         today=TODAY)


def test_state_no_income_tax_false_on_corrupted_ca_none_type():
    # Codex round-10 P2 (projection's twin fix): a same-year, code-matching
    # CA cache with a corrupted "type": "none" must not claim "$0 —
    # correct and complete" — CA is graduated_income, never "none".
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "CA"}
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "CA"}
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="canonetype",
                         today=TODAY)
    assert r.state_no_income_tax is False
    assert r.state_cache_mismatch is not None
    md = lh.render_markdown(r)
    assert "correct and complete" not in md


def test_state_no_income_tax_false_on_corrupted_none_type_unknown_resident():
    # The type-validity guard must fire even when the resident state is
    # unknown — that branch otherwise trusts cache_says_none on its own.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "CA"}
    profile = _load("taxpayer_profile.json")
    assert profile.get("address") is None  # fixture precondition
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="canonetype2",
                         today=TODAY)
    assert r.state_no_income_tax is False
    assert r.state_cache_mismatch is not None


# --------------------------------------------------------------------------
# round-7 P1: a known state-cache mismatch must zero state savings entirely,
# not just flip the flag next to stale dollars.
# --------------------------------------------------------------------------

def test_state_cache_mismatch_zeroes_state_savings_not_just_flag():
    # Round-6 recorded state_cache_mismatch as a warning, but
    # _state_marginal_rate/state_savings had ALREADY consumed the stale
    # brackets before that check ran: an FL resident with a leftover CA
    # cache (real graduated brackets, not a type="none" stub) showed real
    # CA dollars in state_savings/total_savings sitting right next to the
    # mismatch warning. The cache-vs-resident check must run BEFORE any
    # state-rate derivation so a known mismatch forces state_modeled=False,
    # state_mtr=None, state_savings=0.0 — total_savings = fed only.
    law = _load("tax_law_2026.json")  # real CA graduated brackets
    law["state"]["code"] = "CA"
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "FL"}
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="mismatch-brackets",
                         today=TODAY)
    assert r.state_cache_mismatch is not None
    assert "CA" in r.state_cache_mismatch and "FL" in r.state_cache_mismatch
    assert r.state_modeled is False
    assert r.state_mtr is None
    assert r.state_savings == 0.0
    assert r.total_savings == r.fed_savings
    md = lh.render_markdown(r)
    assert "State cache mismatch" in md
    assert "correct and complete" not in md


def test_state_cache_match_with_real_brackets_still_prices_savings():
    # Regression: a MATCHING cache (same real CA brackets, resident is CA)
    # must be entirely unaffected by the round-7 reordering — state savings
    # still prices from the cache as before.
    law = _load("tax_law_2026.json")
    law["state"]["code"] = "CA"
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "CA"}
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="match-brackets",
                         today=TODAY)
    assert r.state_cache_mismatch is None
    assert r.state_modeled is True
    assert r.state_mtr == 0.093
    assert 520 <= r.state_savings <= 640


# --------------------------------------------------------------------------
# round-7 P2: a code-less none-cache no longer affirms for a KNOWN resident.
# --------------------------------------------------------------------------

def test_state_no_income_tax_known_resident_codeless_cache_stays_unaffirmed():
    # A KNOWN resident (FL) paired with a type="none" cache that lacks a
    # `code` field is fresh evidence the cache can't be verified against —
    # projection.py already requires a resolvable code before the
    # affirmative "$0 — correct and complete" claim; loss_harvest's
    # fallthrough previously affirmed anyway. The flag must stay False (the
    # ordinary "not modeled" banner), with no fabricated mismatch warning
    # either (there's nothing concrete to accuse the cache of).
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none"}  # no "code" key
    profile = _load("taxpayer_profile.json")
    profile["address"] = {"state": "FL"}
    r = lh.build_harvest(_load("result.json"), profile, law,
                         _load("holdings.json"), 2026, name="codeless",
                         today=TODAY)
    assert r.state_no_income_tax is False
    assert r.state_cache_mismatch is None
    assert r.state_modeled is False
    assert r.state_savings == 0.0
    md = lh.render_markdown(r)
    assert "correct and complete" not in md
    assert "State cache mismatch" not in md
    assert "State savings not modeled" in md


def test_state_marginal_rate_refuses_tn_hall_cache():
    # Codex round-23 P2 (projection.project_state_tax's twin): a pre-2021
    # TN cache carries the Hall-tax rate as brackets, but the Hall tax
    # applied ONLY to interest and dividends — applying its rate to
    # harvested capital losses would fabricate state savings Tennessee law
    # never offered. TN never gets generic bracket math here.
    from agents.lib.loss_harvest import _state_marginal_rate
    law = {"tax_year": 2020,
           "state": {"type": "graduated_income", "code": "TN",
                     "brackets": [{"rate": 0.01, "min": 0, "max": None}]}}
    modeled, rate = _state_marginal_rate(law, "MFJ", 100000)
    assert modeled is False
    assert rate is None


def test_harvest_nonresident_flag_degrades_completeness_claim():
    # Codex round-23 P2: with flags.has_nonresident_source_income set, the
    # resident-state no-tax fact still holds but "complete" does NOT — the
    # source state's unmodeled tax can change both the value and ranking
    # of harvested lots. The banner must degrade and the exposure surface.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL"}
    profile = _fl_resident_profile()
    profile.setdefault("flags", {})["has_nonresident_source_income"] = True
    r = _build(tax_law=law, profile=profile)
    assert r.state_no_income_tax is True
    assert r.nonresident_income_warning is not None
    md = lh.render_markdown(r)
    assert "correct and complete" not in md
    assert "NOT the complete picture" in md
    assert "Nonresident income exposure" in md
    d = lh._report_to_dict(r)
    assert d["nonresident_income_warning"] == r.nonresident_income_warning


def test_harvest_nonresident_flag_absent_keeps_complete_claim():
    # Advisory read: an absent flag reads False — the affirmative claim is
    # unchanged for a taxpayer who never answered the screening question.
    law = _load("tax_law_2026.json")
    law["state"] = {"type": "none", "code": "FL"}
    r = _build(tax_law=law, profile=_fl_resident_profile())
    assert r.nonresident_income_warning is None
    md = lh.render_markdown(r)
    assert "correct and complete" in md
    assert "Nonresident income exposure" not in md


class TestAStateWhoseBracketsAreKeyedByRateSchedule:
    """The harvest twin of projection's fix. `state.brackets` is a flat list
    for California and a dict of §601 rate schedules for New York, and a
    non-empty dict is truthy — so `_ordinary_marginal_rate` used to be handed
    the schedule KEYS and raise `AttributeError: 'str' object has no attribute
    'get'` from inside the rate loop."""

    def _law(self, **state_over):
        return {"tax_year": 2026, "state": ny_state_cache(**state_over)}

    def test_the_rate_comes_back_instead_of_an_exception(self):
        """190,000 of taxable income sits in schedule (a)'s 161,550-323,200
        band, IT-201-I (2025) p.33: 6%."""
        modeled, rate = lh._state_marginal_rate(self._law(), "MFJ", 190000)
        assert (modeled, rate) == (True, 0.06)

    def test_the_schedule_follows_the_filing_status(self):
        """The same income, two statuses, two schedules. At 100,000 a single
        filer is in (c)'s 80,650-215,400 band at 6%; a married couple is still
        in (a)'s 27,900-161,550 band at 5.5%. This function took a
        `filing_status` it never read until now, which is exactly how one
        schedule could have stood in for three."""
        law = self._law()
        assert lh._state_marginal_rate(law, "MFJ", 100000)[1] == 0.055
        assert lh._state_marginal_rate(law, "Single", 100000)[1] == 0.06

    def test_an_unfetched_schedule_stays_not_modeled(self):
        law = self._law(brackets={"a": [], "b": [], "c": []})
        assert lh._state_marginal_rate(law, "MFJ", 100000) == (False, None)

    def test_a_recapture_state_says_its_rate_is_only_a_floor(self):
        """Inside the phase-in band and above the top of it, a harvested
        dollar saves MORE than the schedule's marginal rate. Under-stating a
        BENEFIT is the degrade direction, so the figure stands — but it is
        labelled, because a planner who reads it as the answer leaves real
        savings on the table."""
        law = json.loads((FIX / "tax_law_2026.json").read_text())
        law["state"] = ny_state_cache()
        profile = {**_load("taxpayer_profile.json"),
                   "address": {"state": "NY"}}
        r = _build(tax_law=law, profile=profile)
        assert r.state_modeled
        assert r.state_rate_floor_note and "floor" in r.state_rate_floor_note
        assert "State rate is a floor" in lh.render_markdown(r)

    def test_a_state_with_no_recapture_carries_no_such_note(self):
        """California's cache has no recapture block, and a note that fires
        on every state is one nobody reads."""
        r = _build()
        assert r.state_rate_floor_note is None
        assert "State rate is a floor" not in lh.render_markdown(r)


def test_the_harvest_prices_from_the_returns_line_12():
    """AA20. The fixture harvest takes $2,500 of short-term loss off ordinary
    income, $3,750 of long-term loss at 15% ($562.50) and $142.50 of NIIT:
    - line 12 $30,000: taxable income $246,000, the 24% bracket, $600;
      $1,305 in all;
    - line 12 $150,000: $126,000, the 22% bracket, $550; $1,255.
    On the cache's standard deduction that itemizer's saving read $50 high,
    the direction AGENTS.md §4 makes a hard error."""
    def savings(line_12):
        result = _load("result.json")
        result["form_1040"]["line_12_standard_deduction"] = line_12
        return lh.build_harvest(result, _load("taxpayer_profile.json"),
                                _load("tax_law_2026.json"),
                                _load("holdings.json"), 2026, name="harvest",
                                today=TODAY).fed_savings
    assert savings(30000) == 1305.0
    assert savings(150000) == 1255.0


def test_the_harvest_report_carries_the_medical_gate():
    # No Schedule A, and a harvest lowers AGI: not known, so said.
    r = _build()
    assert r.medical_floor_may_move is True
    assert lh._report_to_dict(r)["medical_floor_may_move"] is True
    assert "medical-expense deduction is held" in lh.render_markdown(r)
    assert "the return's own line 12" in lh.render_markdown(r)
    # $1,000 of expenses under a floor of about $20,000 before and after.
    result = _load("result.json")
    result["schedule_a"] = {"use_itemized": True, "medical_expenses": 1000}
    r = lh.build_harvest(result, _load("taxpayer_profile.json"),
                         _load("tax_law_2026.json"), _load("holdings.json"),
                         2026, name="harvest", today=TODAY)
    assert r.medical_floor_may_move is False


def test_the_harvest_refuses_a_capped_charitable_deduction():
    """A harvest lowers AGI, which lowers a binding charitable cap while the
    keystone holds the deduction: the saving would read high."""
    result = _load("result.json")
    result["schedule_a"] = {"use_itemized": True, "charitable_carryforward": 5000}
    with pytest.raises(lh.ScenarioError, match="capped at a share of AGI"):
        lh.build_harvest(result, _load("taxpayer_profile.json"),
                         _load("tax_law_2026.json"), _load("holdings.json"),
                         2026, name="harvest", today=TODAY)


def test_an_itemizer_with_recorded_inputs_is_repriced_at_the_harvested_agi():
    """AA23: the federal side priced on Schedule A at the harvested AGI. A
    harvest LOWERS AGI, which lowers the 7.5% medical floor and raises the
    deduction, so it saves MORE than the held figure showed — the
    under-statement the medical-floor caveat used to disclose."""
    from agents.lib.tax_computations import compute_schedule_a
    result = _load("result.json")
    agi = result["form_1040"]["line_11_agi"]
    sched = compute_schedule_a(agi, "MFJ", 2026, 30000, 40400,
                               medical_expenses=40000, mortgage_interest=20000,
                               top_bracket_income=0)
    result["schedule_a"] = sched
    result["form_1040"]["line_12_standard_deduction"] = sched["itemized_total"]

    def harvest(r):
        return lh.build_harvest(r, _load("taxpayer_profile.json"),
                                _load("tax_law_2026.json"),
                                _load("holdings.json"), 2026, name="harvest",
                                today=TODAY)
    priced = harvest(result)
    held = harvest(dict(result, schedule_a={k: v for k, v in sched.items()
                                            if k != "inputs"}))
    assert priced.schedule_a_repriced is True and held.schedule_a_repriced is False
    assert priced.medical_floor_may_move is False and held.medical_floor_may_move
    assert priced.fed_savings > held.fed_savings
    assert "Schedule 1-A (line 13b) deductions" in lh.render_markdown(priced)
    if priced.state_modeled:
        assert "state estimate keeps the held deduction" in lh.render_markdown(
            priced)
    assert lh._report_to_dict(priced)["schedule_a_repriced"] is True
    # The state estimate stays on the held federal taxable-income change.
    assert priced.state_savings == held.state_savings


# --------------------------------------------------------------------------
# AA127: no lot reads as clear without confirmed transaction history
# --------------------------------------------------------------------------
#
# Fixture losses, by hand: L1 VTSAX $1,500 (LT), L2 AAPL $2,500 (ST), L4 TSLA
# $3,000 (LT; the spouse's 5-share buy disallows 5/20 of it, $750), L5 META
# $1,500 inside an IRA (never deductible). $8,500 unrealized in all.
_TAXABLE_LOSERS = {"L1", "L2", "L4"}

def _without_coverage():
    h = _load("holdings.json")
    h.pop("transaction_coverage", None)
    return h


def test_missing_history_holds_every_taxable_loss_back():
    # The reproduced finding: with no history the scanner read every lot as
    # clear, printed "Wash-sale disallowed | $0" and counted the whole loss.
    h = _without_coverage()
    h.pop("recent_transactions")
    r = _build(holdings=h)
    assert r.eligible_lot_ids == []
    assert r.total_loss_harvested == 0
    assert r.total_savings == 0
    assert r.unverified_loss == 7000.0          # 1,500 + 2,500 + 3,000
    assert {l.id for l in r.lots if l.history_gap} == _TAXABLE_LOSERS
    # The reason names the fix: the missing statement, not a missing account.
    assert all("no transaction_coverage" in l.history_gap
               for l in r.lots if l.history_gap)


def test_listed_buys_still_disallow_while_the_rest_is_held_back():
    r = _build(holdings=_without_coverage())
    tsla = next(l for l in r.lots if l.id == "L4")
    assert tsla.disallowed_loss == 750.0
    assert r.wash_sale_blocked_loss == 750.0
    assert r.unverified_loss == 6250.0          # 1,500 + 2,500 + (3,000 - 750)
    assert r.total_savings == 0
    # Every loss lands in exactly one bucket.
    assert round(r.total_loss_harvested + r.wash_sale_blocked_loss
                 + r.tax_advantaged_loss + r.unverified_loss, 2) == 8500.0


def test_confirmed_history_restores_the_plan():
    r = _build()
    assert set(r.eligible_lot_ids) == _TAXABLE_LOSERS
    assert r.unverified_loss == 0
    assert all(l.history_gap is None for l in r.lots)


def test_confirmed_empty_history_clears_lots():
    # An explicit "no buys in the window" is a decision; an absent list is not.
    h = _load("holdings.json")
    h["recent_transactions"] = []
    r = _build(holdings=h)
    assert set(r.eligible_lot_ids) == _TAXABLE_LOSERS
    assert r.wash_sale_blocked_loss == 0
    assert r.unverified_loss == 0


@pytest.mark.parametrize("field,value,gap", [
    ("from", "2026-11-16", "starts 2026-11-16"),    # one day short of as_of - 30
    ("through", "2026-12-14", "ends 2026-12-14"),   # stops before the sale date
])
def test_history_that_misses_the_window_holds_lots_back(field, value, gap):
    h = _load("holdings.json")
    h["transaction_coverage"][field] = value
    r = _build(holdings=h)
    assert r.eligible_lot_ids == []
    assert all(gap in l.history_gap for l in r.lots if l.id in _TAXABLE_LOSERS)


def test_history_through_today_covers_a_planned_sale():
    # A sale planned for 12-15 and checked on 12-01 cannot attest the future:
    # history through today is the most it can hold.
    h = _load("holdings.json")
    h["transaction_coverage"]["through"] = "2026-12-01"
    r = _build(holdings=h, today=datetime.date(2026, 12, 1))
    assert set(r.eligible_lot_ids) == _TAXABLE_LOSERS


def test_a_lot_in_an_account_the_history_omits_is_held_back():
    h = _load("holdings.json")
    h["transaction_coverage"]["accounts"] = ["Spouse Schwab", "Fidelity IRA"]
    r = _build(holdings=h)
    assert r.eligible_lot_ids == []
    assert "Schwab Brokerage" in next(l for l in r.lots if l.id == "L1").history_gap


def test_coverage_accounts_match_like_transaction_accounts():
    h = _load("holdings.json")
    h["transaction_coverage"]["accounts"] = ["  schwab BROKERAGE ", "spouse schwab",
                                             "FIDELITY IRA"]
    assert set(_build(holdings=h).eligible_lot_ids) == _TAXABLE_LOSERS


@pytest.mark.parametrize("coverage,match", [
    ("all accounts", "transaction_coverage must be a JSON object"),
    ({"from": "2026-11-15", "through": "2026-12-15"}, "transaction_coverage.accounts"),
    ({"accounts": [], "from": "2026-11-15", "through": "2026-12-15"},
     "transaction_coverage.accounts"),
    ({"accounts": ["A", " "], "from": "2026-11-15", "through": "2026-12-15"},
     "transaction_coverage.accounts"),
    ({"accounts": ["A"], "from": "2026-11-15"}, "transaction_coverage.through"),
    ({"accounts": ["A"], "from": "11/15/2026", "through": "2026-12-15"},
     "transaction_coverage.from"),
    ({"accounts": ["A"], "from": "2026-12-20", "through": "2026-12-15"},
     "transaction_coverage.from is after"),
])
def test_malformed_coverage_is_refused(coverage, match):
    h = _load("holdings.json")
    h["transaction_coverage"] = coverage
    with pytest.raises(lh.LossHarvestError, match=match):
        _build(holdings=h)


def test_report_says_the_history_is_missing():
    md = lh.render_markdown(_build(holdings=_without_coverage()))
    assert "Wash-sale history not confirmed" in md
    assert "| Held back: wash-sale history not confirmed | $6,250 |" in md
    assert "| Total unrealized losses (all lots) | $8,500 |" in md
    assert "nothing to harvest" not in md


def test_json_carries_the_held_back_loss_and_the_coverage():
    data = lh._report_to_dict(_build(holdings=_without_coverage()))
    assert data["unverified_loss"] == 6250.0
    assert data["transaction_coverage"] is None
    assert lh._report_to_dict(_build())["transaction_coverage"] == {
        "accounts": ["Schwab Brokerage", "Spouse Schwab", "Fidelity IRA"],
        "from": "2026-11-15", "through": "2026-12-15"}


def test_history_that_omits_an_account_the_file_names_holds_every_lot_back():
    # §1091 reaches every account at once: naming the lots' own account is not
    # enough when the file's own buys and lots show accounts the statement omits
    # (the spouse's brokerage, the IRA).
    h = _load("holdings.json")
    h["transaction_coverage"]["accounts"] = ["Schwab Brokerage"]
    r = _build(holdings=h)
    assert r.eligible_lot_ids == []
    assert r.total_savings == 0
    gap = next(l for l in r.lots if l.id == "L1").history_gap
    assert "Spouse Schwab" in gap and "Fidelity IRA" in gap


def test_a_statement_without_a_transaction_list_holds_lots_back():
    # A misspelled or missing list beside a coverage statement is not "no
    # purchases": the spouse's TSLA buy would silently vanish.
    h = _load("holdings.json")
    h["transactions"] = h.pop("recent_transactions")
    r = _build(holdings=h)
    assert r.eligible_lot_ids == []
    assert "recent_transactions" in next(l for l in r.lots if l.id == "L1").history_gap


@pytest.mark.parametrize("value", [{}, False, "", 0])
def test_a_transaction_list_that_is_not_a_list_is_refused(value):
    h = _load("holdings.json")
    h["recent_transactions"] = value
    with pytest.raises(lh.LossHarvestError, match="'recent_transactions' must be a list"):
        _build(holdings=h)


def _with_lot_bought_in_window(listed):
    # L6: 10 VTSAX bought 2026-12-01 in the window, at a gain. It is a
    # replacement purchase for L1's loss whether or not its buy is listed.
    h = _load("holdings.json")
    h["lots"].append({"id": "L6", "ticker": "VTSAX", "account": "Schwab Brokerage",
                      "acquired": "2026-12-01", "quantity": 10,
                      "cost_basis": 1000, "current_value": 1100, "tax_advantaged": False})
    if listed:
        h["recent_transactions"].append({"ticker": "VTSAX", "account": "Schwab Brokerage",
                                         "date": "2026-12-01", "kind": "buy", "quantity": 10})
    return h


def test_a_lot_bought_in_the_window_without_its_buy_holds_lots_back():
    r = _build(holdings=_with_lot_bought_in_window(listed=False))
    assert r.eligible_lot_ids == []
    assert "L6" in next(l for l in r.lots if l.id == "L1").history_gap


def test_a_lot_bought_in_the_window_with_its_buy_listed_is_matched():
    r = _build(holdings=_with_lot_bought_in_window(listed=True))
    l1 = next(l for l in r.lots if l.id == "L1")
    assert l1.history_gap is None
    assert l1.disallowed_loss == 150.0          # 10 of 100 shares × 1,500


@pytest.mark.parametrize("through,cleared", [("2026-12-01", False), ("2026-12-20", True)])
def test_a_past_sale_needs_history_through_what_is_knowable(through, cleared):
    # Sale on 12-01, checked on 12-20: purchases 12-02..12-20 have happened and
    # fall inside the 30 days after the sale, so the history must reach today.
    h = _load("holdings.json")
    h["as_of"] = "2026-12-01"
    h["transaction_coverage"].update({"from": "2026-11-01", "through": through})
    r = _build(holdings=h, today=datetime.date(2026, 12, 20))
    assert bool(r.eligible_lot_ids) is cleared


def test_the_banner_names_what_is_wrong_with_a_stated_history():
    h = _load("holdings.json")
    h["transaction_coverage"]["accounts"] = ["Schwab Brokerage", "Fidelity IRA"]
    md = lh.render_markdown(_build(holdings=h))
    banner = next(line for line in md.splitlines() if "history not confirmed —" in line)
    assert "Spouse Schwab is not in transaction_coverage.accounts" in banner
    assert "Add `transaction_coverage`" not in banner


def _cross_ticker_holdings():
    return _holdings_with(
        lots=[{"id": "fund", "ticker": "VTSAX", "account": "Brokerage",
               "acquired": "2024-01-01", "quantity": 100,
               "cost_basis": 12000, "current_value": 10000, "tax_advantaged": False}],
        txns=[{"ticker": "VTI", "account": "Spouse Plan", "date": "2026-12-01",
               "quantity": 25}],
    )


def _review_pair(ticker, other_ticker, classification):
    return {"ticker": ticker, "other_ticker": other_ticker, "classification": classification}


def test_cross_ticker_unreviewed_buy_holds_loss_and_names_the_purchase():
    holdings = _cross_ticker_holdings()
    report = _build(holdings=holdings)
    assert report.eligible_lot_ids == []
    assert report.unverified_loss == 2000
    assert report.total_savings == 0
    assert report.wash_sale_blocked_loss == 0
    md = lh.render_markdown(report)
    assert "Wash-sale review incomplete" in md
    row = next(line for line in md.splitlines() if line.startswith("| fund | VTSAX |"))
    assert "buy of VTI on 2026-12-01" in row
    assert "Spouse Plan" in row
    with pytest.raises(lh.LossHarvestError, match="needs a reviewed wash_sale_classifications"):
        lh.check_wash_sale("VTSAX", holdings["recent_transactions"], TODAY)


def test_unreviewed_cross_ticker_buy_holds_only_the_loss_left_after_matching():
    holdings = _cross_ticker_holdings()
    holdings["recent_transactions"].append(
        {"ticker": "VTSAX", "account": "Spouse Plan", "date": "2026-12-01", "quantity": 10},
    )
    # Ten same-ticker shares disallow $200 of the $2,000 loss. The unrelated
    # decision is still open, so the other $1,800 cannot enter the estimate.
    report = _build(holdings=holdings)
    assert report.wash_sale_blocked_loss == 200
    assert report.unverified_loss == 1800
    assert report.eligible_lot_ids == []


@pytest.mark.parametrize("classification,disallowed", [("not_identical", 0), ("identical", 500)])
def test_cross_ticker_review_drives_the_prorated_loss(classification, disallowed):
    holdings = _cross_ticker_holdings()
    holdings["wash_sale_classifications"] = [_review_pair(" vti ", "vtsax", classification)]
    report = _build(holdings=holdings)
    assert report.unverified_loss == 0
    assert report.eligible_lot_ids == ["fund"]
    # The 25-share purchase is 25% of the 100-share lot's $2,000 loss.
    assert report.wash_sale_blocked_loss == disallowed
    assert report.total_loss_harvested == 2000 - disallowed
    if disallowed:
        assert report.lots[0].wash_sale_adjustments[0]["replacement_ticker"] == "VTI"
    decisions = lh._wash_sale_classifications(holdings)
    qty, _ = lh.check_wash_sale("VTSAX", holdings["recent_transactions"], TODAY,
                               classifications=decisions)
    assert qty == (25 if disallowed else 0)


@pytest.mark.parametrize("date,held", [("2026-11-15", True), ("2026-11-14", False),
                                      ("2027-01-14", True), ("2027-01-15", False)])
def test_cross_ticker_buy_review_observes_both_window_boundaries(date, held):
    holdings = _cross_ticker_holdings()
    holdings["recent_transactions"][0]["date"] = date
    assert _build(holdings=holdings).unverified_loss == (2000 if held else 0)


def test_employer_stock_vest_in_plan_account_uses_same_ticker_pool():
    holdings = _cross_ticker_holdings()
    holdings["lots"][0]["ticker"] = "EMPLOYER"
    holdings["recent_transactions"][0].update(ticker="EMPLOYER", kind="buy",
                                             account_type="other_tax_advantaged")
    report = _build(holdings=holdings)
    assert report.wash_sale_blocked_loss == 500
    assert report.total_loss_harvested == 1500
    assert report.unverified_loss == 0


@pytest.mark.parametrize("rows", [
    None, "identical", [{"ticker": "VTI", "other_ticker": "VTSAX"}],
    [_review_pair("VTI", "VTSAX", "different")],
    [_review_pair("VTI", " vti ", "identical")],
    [_review_pair("VTI", "VTSAX", "identical"), _review_pair("vtsax", "vti", "not_identical")],
    [{**_review_pair("VTI", "VTSAX", "identical"), "note": "reviewed"}],
    ["VTI/VTSAX identical"],
])
def test_malformed_wash_sale_classification_refused(rows):
    holdings = _cross_ticker_holdings()
    holdings["wash_sale_classifications"] = rows
    with pytest.raises(lh.LossHarvestError, match="wash_sale_classifications"):
        _build(holdings=holdings)


def test_reviewed_purchase_shares_are_only_allocated_once_across_tickers():
    holdings = _cross_ticker_holdings()
    holdings["lots"].append({**holdings["lots"][0], "id": "etf", "ticker": "VTI",
                              "acquired": "2025-01-01"})
    holdings["wash_sale_classifications"] = [_review_pair("VTSAX", "VTI", "identical")]
    report = _build(holdings=holdings)
    assert report.wash_sale_blocked_loss == 500
    # The VTSAX lot used 25 VTI shares. Unless it is sold first, those shares
    # replace the VTI lot instead, so the VTI loss is held, not harvested.
    assert report.total_loss_harvested == 1500
    assert report.unverified_loss == 2000


def test_reviewed_buy_matched_to_a_blocked_other_ticker_cannot_clear_a_sale():
    holdings = _cross_ticker_holdings()
    holdings["lots"].append({**holdings["lots"][0], "id": "etf", "ticker": "VTI",
                              "acquired": "2020-01-01", "quantity": 25})
    holdings["wash_sale_classifications"] = [_review_pair("VTSAX", "VTI", "identical")]
    report = _build(holdings=holdings)
    assert report.wash_sale_blocked_loss == 2000
    assert report.unverified_loss == 2000
    assert report.eligible_lot_ids == []


def _losing_lot(lot_id, ticker, acquired, quantity, account="Brokerage"):
    # $20 of loss per share.
    return {"id": lot_id, "ticker": ticker, "account": account, "acquired": acquired,
            "quantity": quantity, "cost_basis": 120 * quantity, "current_value": 100 * quantity, "tax_advantaged": False}


def _in_window_buy(ticker, quantity, account="Spouse Plan", date="2026-12-01"):
    return {"ticker": ticker, "account": account, "date": date, "quantity": quantity}


def _scan(lots, txns, pairs=()):
    holdings = _holdings_with(lots=lots, txns=txns)
    holdings["wash_sale_classifications"] = [_review_pair(*pair) for pair in pairs]
    report = _build(holdings=holdings)
    return report, {lot.id: lot for lot in report.lots}


def _scan_mfs(lots, txns=()):
    result = _load("result.json")
    result["meta"]["filing_status"] = "MFS"
    profile = _load("taxpayer_profile.json")
    profile["filing_status"] = "MFS"
    return lh.build_harvest(result, profile, _load("tax_law_2026.json"),
                            _holdings_with(lots, list(txns)), 2026, name="mfs", today=TODAY)


@pytest.mark.parametrize("owner", [{}, {"owner": None}, {"owner": "joint"}])
def test_mfs_requires_lot_owner(owner):
    lot = _losing_lot("S1", "VTI", "2025-03-01", 50, "Spouse Schwab")
    with pytest.raises(lh.LossHarvestError, match="lot S1.*owner.*taxpayer.*spouse"):
        _scan_mfs([{**lot, **owner}])


def test_mfs_excludes_spouse_losses_but_keeps_spouse_replacement_purchases():
    lots = [dict(_losing_lot("T1", "VTI", "2025-03-01", 50), owner="taxpayer"),
            dict(_losing_lot("S1", "VTI", "2026-12-01", 50, "Spouse Schwab"), owner="spouse"),
            dict(_losing_lot("S2", "VTI", "2025-03-01", 5, "Spouse IRA"),
                 owner="spouse", tax_advantaged=True)]
    buy = _in_window_buy("VTI", 50, account="Spouse Schwab")
    report = _scan_mfs(lots, [buy])
    assert report.eligible_lot_ids == []
    assert report.fed_savings == 0
    assert report.wash_sale_blocked_loss == 1000
    assert report.spouse_loss == 1100
    assert report.tax_advantaged_loss == 0
    assert lh._report_to_dict(report)["spouse_loss"] == 1100
    markdown = lh.render_markdown(report)
    assert "Spouse's lots excluded from this return | $1,100 |" in markdown
    assert "Total unrealized losses (all lots) | $2,100 |" in markdown


@pytest.mark.parametrize("owner", [None, False])
def test_mfs_does_not_use_an_ambiguous_or_spouse_purchase_as_own(owner):
    lots = [dict(_losing_lot("T1", "VTI", "2025-03-01", 50), owner="taxpayer"),
            dict(_losing_lot("T2", "VTI", "2026-12-01", 5), owner="taxpayer"),
            dict(_losing_lot("S1", "VTI", "2026-12-01", 50), owner="spouse")]
    buy = dict(_in_window_buy("VTI", 50, account="Brokerage"), owner_is_taxpayer=owner)
    report = _scan_mfs(lots, [buy])
    assert next(lot for lot in report.lots if lot.id == "T1").disallowed_loss == 1000


@pytest.mark.parametrize("quantity", [None, 100])
def test_buy_matching_a_losing_and_a_gaining_lot_blocks_the_old_loss(quantity):
    lots = [dict(_losing_lot("X", "VTI", "2025-03-01", 50), cost_basis=8000),
            _losing_lot("L1", "VTI", "2026-12-01", 10),
            dict(_losing_lot("L2", "VTI", "2026-12-01", 90), cost_basis=8000)]
    report, by_id = _scan(lots, [_in_window_buy("VTI", quantity, account="Brokerage")])
    assert by_id["X"].disallowed_loss == 3000
    assert report.total_loss_harvested == 0


def test_same_day_lots_match_the_highest_loss_per_share_first_in_both_file_orders():
    a = dict(_losing_lot("A", "VTI", "2024-01-01", 10), cost_basis=1010)
    b = dict(_losing_lot("B", "VTI", "2024-01-01", 10, "Other"), cost_basis=2000)
    for lots in ([a, b], [b, a]):
        report, by_id = _scan(lots, [_in_window_buy("VTI", 5)])
        assert by_id["B"].disallowed_loss == 500
        assert report.total_loss_harvested == 510
        assert "highest loss per share first" in lh.render_markdown(report)


def test_an_identical_purchase_absorbed_by_another_ticker_holds_the_later_lot():
    # The VTI buy fully blocks the older VTSAX lot only because the pair was
    # reviewed identical. Without the hold, the newer VTSAX lot would clear.
    report, lots = _scan([_losing_lot("A", "VTSAX", "2023-01-01", 10),
                          _losing_lot("B", "VTSAX", "2024-01-01", 100)],
                         [_in_window_buy("VTI", 10)], [("VTSAX", "VTI", "identical")])
    assert lots["A"].wash_sale_status == "fully_blocked"
    assert lots["B"].held_back and report.eligible_lot_ids == []


def test_a_purchase_matched_across_tickers_holds_its_own_tickers_later_lot():
    report, lots = _scan([_losing_lot("A", "VTSAX", "2023-01-01", 10),
                          _losing_lot("B", "VTI", "2024-01-01", 100)],
                         [_in_window_buy("VTI", 10)], [("VTSAX", "VTI", "identical")])
    assert lots["B"].held_back and report.eligible_lot_ids == []


def test_an_identical_decision_never_raises_the_harvest():
    # Reviewing a pair as identical only adds replacements, so it can never
    # leave more loss to harvest than not_identical does.
    lots = [{"id": "A", "ticker": "VTSAX", "account": "Brokerage", "acquired": "2020-01-01",
             "quantity": 200, "cost_basis": 2200, "current_value": 2000, "tax_advantaged": False},
            {"id": "B", "ticker": "VTI", "account": "Brokerage", "acquired": "2024-01-01",
             "quantity": 100, "cost_basis": 15000, "current_value": 10000, "tax_advantaged": False}]
    harvested = {decision: _scan(lots, [_in_window_buy("VTI", 100)],
                                 [("VTSAX", "VTI", decision)])[0].total_loss_harvested
                 for decision in ("not_identical", "identical")}
    assert harvested == {"not_identical": 200, "identical": 100}


def test_the_order_of_purchases_in_the_file_does_not_change_the_harvest():
    lots = [_losing_lot("old", "VTSAX", "2022-06-01", 10),
            _losing_lot("new", "VTSAX", "2024-01-01", 100)]
    same, cross = _in_window_buy("VTSAX", 20), _in_window_buy("VTI", 5)
    results = []
    for txns in ([cross, same], [same, cross]):
        report, _ = _scan(lots, txns, [("VTSAX", "VTI", "identical")])
        results.append((report.eligible_lot_ids, report.total_loss_harvested,
                        report.unverified_loss))
    # 15 of the newer lot's 100 shares are replaced; the rest is held because
    # the older, fully blocked lot is not in the plan.
    assert results[0] == results[1] == ([], 0, 1700)


def test_a_held_lot_holds_back_a_later_lot_sharing_its_purchase():
    # Lot A is held for its unreviewed ABC purchase and is not sold, so the ten
    # VTSAX shares it absorbed may replace the VTI lot instead.
    report, lots = _scan([_losing_lot("A", "VTSAX", "2023-01-01", 100),
                          _losing_lot("B", "VTI", "2024-01-01", 100)],
                         [_in_window_buy("VTSAX", 10), _in_window_buy("ABC", 5)],
                         [("VTSAX", "VTI", "identical"), ("VTI", "ABC", "not_identical")])
    assert lots["A"].held_back and lots["B"].held_back
    assert report.eligible_lot_ids == []


def test_an_identical_purchase_that_bought_another_losing_lot_holds_the_loss():
    # The VTI buy acquired the VTI lot; those shares are substantially
    # identical to VTSAX and may replace it even though the VTI lot is sold.
    _, lots = _scan([_losing_lot("fund", "VTSAX", "2024-01-01", 100),
                     _losing_lot("etf", "VTI", "2026-12-01", 25)],
                    [_in_window_buy("VTI", 30, account="Brokerage")],
                    [("VTSAX", "VTI", "identical")])
    assert lots["fund"].held_back
    assert "also bought another losing lot" in lots["fund"].classification_gaps[0]


@pytest.mark.parametrize("field,value", [
    ("quantity", float("inf")), ("quantity", float("nan")), ("quantity", "inf"),
    ("quantity", 10 ** 400), ("cost_basis", float("nan")), ("current_value", float("inf")),
])
def test_non_finite_lot_numbers_refused(field, value):
    holdings = _cross_ticker_holdings()
    holdings["lots"][0][field] = value
    with pytest.raises(lh.LossHarvestError, match="must be a finite number"):
        _build(holdings=holdings)


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), "inf", 10 ** 400])
def test_non_finite_replacement_quantity_refused(quantity):
    holdings = _cross_ticker_holdings()
    holdings["recent_transactions"][0]["quantity"] = quantity
    with pytest.raises(lh.LossHarvestError, match=r"recent_transactions\[0\].*quantity"):
        _build(holdings=holdings)


def test_in_window_buy_without_a_ticker_refused():
    holdings = _cross_ticker_holdings()
    holdings["recent_transactions"][0]["ticker"] = " "
    with pytest.raises(lh.LossHarvestError, match=r"recent_transactions\[0\]"):
        _build(holdings=holdings)


@pytest.mark.parametrize("kind", ["reinvest", "drip", "", 1])
def test_unknown_transaction_kind_refused(kind):
    holdings = _cross_ticker_holdings()
    holdings["recent_transactions"][0]["kind"] = kind
    with pytest.raises(lh.LossHarvestError, match="kind must be buy or sell"):
        _build(holdings=holdings)


def test_a_transaction_kind_is_read_whatever_its_case_and_spacing():
    holdings = _cross_ticker_holdings()
    holdings["recent_transactions"][0].update(ticker="VTSAX", kind=" Buy ")
    assert _build(holdings=holdings).wash_sale_blocked_loss == 500


def test_unreviewed_pair_error_masks_an_identifier_shaped_ticker():
    # Nine digits, built here so the committed text holds no identifier shape.
    ticker = "1234" + "56789"
    with pytest.raises(lh.LossHarvestError) as exc:
        lh.check_wash_sale(ticker, [_in_window_buy("VTI", 5)], TODAY)
    assert ticker not in str(exc.value)


def test_each_hold_banner_states_the_loss_its_own_reason_holds():
    holdings = _holdings_with(lots=[_losing_lot("A", "VTSAX", "2023-01-01", 100),
                                    _losing_lot("B", "AAPL", "2023-01-01", 100)],
                              txns=[_in_window_buy("VTI", 10)])
    holdings["wash_sale_classifications"] = [_review_pair("AAPL", "VTI", "not_identical"),
                                             _review_pair("AAPL", "VTSAX", "not_identical")]
    holdings["transaction_coverage"]["from"] = "2026-11-20"
    md = lh.render_markdown(_build(holdings=holdings))
    assert "Wash-sale history not confirmed — $4,000 of losses held back" in md
    assert "Wash-sale review incomplete — $2,000 of losses held back" in md
    assert "are not in the plan or the savings until `holdings.json` fixes that" in md
    assert "## Held back for wash-sale review" in md


def test_held_table_escapes_labels_and_keeps_the_holding_period():
    report, _ = _scan([_losing_lot("A", "VTSAX", "2023-01-01", 100, account="Joint | Spouse")],
                      [_in_window_buy("VTI", 10, account="Plan | Spouse")])
    row = next(line for line in lh.render_markdown(report).splitlines()
               if line.startswith("| A | VTSAX |"))
    assert r"Joint \| Spouse" in row and r"Plan \| Spouse" in row
    # Six cells: every unescaped pipe is a column boundary.
    assert row.replace(r"\|", "").count("|") == 7
    assert "| LONG |" in row


def test_the_basis_plan_names_the_replacement_ticker():
    holdings = _cross_ticker_holdings()
    holdings["wash_sale_classifications"] = [_review_pair("VTSAX", "VTI", "identical")]
    md = lh.render_markdown(_build(holdings=holdings))
    assert "| Sold lot | Replacement ticker |" in md
    assert any(line.startswith("| fund | VTI | 2026-12-01 |") for line in md.splitlines())


def test_an_unknown_kind_is_refused_outside_the_window_too():
    # The file is checked as a whole: a row the window does not reach today
    # reaches it on the next run with a later as_of.
    holdings = _cross_ticker_holdings()
    holdings["recent_transactions"].append({"ticker": "VTI", "account": "Spouse Plan",
                                            "date": "2025-06-01", "kind": "reinvest"})
    del holdings["transaction_coverage"]  # the coverage check reads kinds too
    with pytest.raises(lh.LossHarvestError, match=r"recent_transactions\[1\] kind"):
        _build(holdings=holdings)


def _scan_as(status, lots, txns=(), *, state=None, pairs=()):
    result, profile = _load("result.json"), _load("taxpayer_profile.json")
    result["meta"]["filing_status"] = profile["filing_status"] = status
    if state is not None:
        profile["address"] = {"state": state}
    holdings = _holdings_with(lots, list(txns))
    holdings["wash_sale_classifications"] = [_review_pair(*pair) for pair in pairs]
    return lh.build_harvest(result, profile, _load("tax_law_2026.json"), holdings, 2026,
                            name="status", today=TODAY)


def _owned(lot_id, owner, acquired="2025-03-01", account="Brokerage", quantity=50):
    return dict(_losing_lot(lot_id, "VTI", acquired, quantity, account), owner=owner)


@pytest.mark.parametrize("status", ["mfs", " MFS ", "Mfs"])
def test_a_separate_return_is_read_in_any_spelling(status):
    """The rates canonicalised "mfs" to the $1,500 cap while the spouse rule
    compared the raw string, so the spouse's loss doubled the harvest."""
    report = _scan_as(status, [_owned("T1", "taxpayer"), _owned("S1", "spouse", account="Spouse")])
    assert report.eligible_lot_ids == ["T1"] and report.spouse_loss == 1000
    with pytest.raises(lh.LossHarvestError, match="lot T1.*owner"):
        _scan_as(status, [_losing_lot("T1", "VTI", "2025-03-01", 50)])


@pytest.mark.parametrize("status,counted", [("HoH", False), ("Single", False), ("QSS", False),
                                            ("MFJ", True)])
def test_only_a_joint_return_counts_the_spouses_lots(status, counted):
    report = _scan_as(status, [_owned("T1", "taxpayer"), _owned("S1", "spouse", account="Spouse")])
    assert ("S1" in report.eligible_lot_ids) is counted
    assert report.spouse_loss == (0 if counted else 1000)
    # Other statuses may still omit owner; only a separate return requires it.
    assert _scan_as(status, [_losing_lot("T1", "VTI", "2025-03-01", 50)]).eligible_lot_ids == ["T1"]


def test_a_spouses_lot_earns_no_own_purchase_exclusion():
    """A spouse's lot at the buy's key cannot make the buy the taxpayer's own
    acquisition, even with the buy marked as the taxpayer's."""
    buy = dict(_in_window_buy("VTI", 50, account="Joint"), owner_is_taxpayer=True)
    report = _scan_as("MFS", [_owned("T1", "taxpayer"),
                              _owned("S1", "spouse", acquired="2026-12-01", account="Joint")], [buy])
    assert report.eligible_lot_ids == [] and report.wash_sale_blocked_loss == 1000


def test_a_spouses_tax_advantaged_lot_is_not_listed_as_the_filers():
    lots = [_owned("T1", "taxpayer"),
            dict(_owned("S2", "spouse", account="Spouse IRA"), tax_advantaged=True)]
    md = lh.render_markdown(_scan_as("MFS", lots))
    assert "## Tax-advantaged account positions" not in md


@pytest.mark.parametrize("order", [("A2", "B"), ("B", "A2")])
def test_lot_id_breaks_a_tie_in_loss_per_share(order):
    lots = {"A2": _losing_lot("A2", "VTI", "2024-01-01", 10),
            "B": _losing_lot("B", "VTI", "2024-01-01", 20)}
    report, by_id = _scan([lots[lot_id] for lot_id in order], [_in_window_buy("VTI", 10)])
    assert by_id["A2"].wash_sale_status == "fully_blocked"
    assert report.total_loss_harvested == 0 and report.unverified_loss == 400


def test_the_sale_order_is_stated_when_no_lot_is_eligible():
    lots = [_losing_lot("A", "VTI", "2024-01-01", 10),
            dict(_losing_lot("B", "VTI", "2024-01-01", 10, "Other"), cost_basis=11000, current_value=1000)]
    md = lh.render_markdown(_scan(lots, [_in_window_buy("VTI", 10)])[0])
    assert "No lot is cleared to harvest" in md
    assert "the one with the highest loss per share first" in md


def test_the_caveats_state_when_a_quantity_less_buy_is_the_lots_own():
    md = lh.render_markdown(_scan([_losing_lot("A", "VTI", "2024-01-01", 10)], [])[0])
    assert "only when that lot and that buy are the only ones at the key" in md
    assert "are assumed to BE that lot's acquisition" not in md


def test_an_unsold_lots_own_purchase_replaces_an_older_lot():
    """NEW is held back, so the plan keeps it: the purchase that created it
    replaces OLD's sale, which was shown clear."""
    old = dict(_losing_lot("OLD", "VTI", "2020-01-01", 10, "B"), cost_basis=2000, current_value=1000)
    new = dict(_losing_lot("NEW", "VTI", "2026-12-05", 10, "A"), cost_basis=1010, current_value=1000)
    txns = [_in_window_buy("VTI", 10, account="A", date="2026-12-05"),
            _in_window_buy("VOO", 3, account="C", date="2026-12-05")]
    report, by_id = _scan([old, new], txns, [("VTI", "VOO", "identical")])
    assert report.eligible_lot_ids == []
    assert by_id["OLD"].held_back
    assert any("bought lot NEW, which this plan does not sell" in gap
               for gap in by_id["OLD"].classification_gaps)


def test_a_lot_bought_in_the_same_purchase_is_replaced_by_its_unsold_sibling():
    first = dict(_losing_lot("L0", "VOO", "2026-12-01", 20, "B"), cost_basis=2020, current_value=2000)
    second = dict(_losing_lot("L1", "VOO", "2026-12-01", 20, "B"), cost_basis=4000, current_value=2000)
    txns = [_in_window_buy("VOO", 40, account="B"), _in_window_buy("VTI", 10, account="A"),
            _in_window_buy("VOO", 3, account="C")]
    report, by_id = _scan([first, second], txns, [("VTI", "VOO", "identical")])
    assert report.eligible_lot_ids == []
    assert by_id["L0"].held_back and by_id["L1"].held_back


def test_history_must_list_every_share_bought_in_the_window():
    """A 10-share buy beside 60 shares bought that day (the taxpayer's 10 and
    the spouse's 50) cleared the lot the unlisted 50 replace."""
    lots = [_owned("T1", "taxpayer"),
            _owned("T2", "taxpayer", acquired="2026-12-01", account="Joint", quantity=10),
            _owned("S1", "spouse", acquired="2026-12-01", account="Joint", quantity=50)]
    buy = dict(_in_window_buy("VTI", 10, account="Joint"), owner_is_taxpayer=True)
    report = _scan_as("MFS", lots, [buy])
    assert report.eligible_lot_ids == []
    assert all("lists fewer shares" in (lot.history_gap or "") for lot in report.lots if lot.held_back)
    report = _scan_as("MFS", lots, [buy, dict(_in_window_buy("VTI", 50, account="Joint"),
                                               owner_is_taxpayer=False)])
    assert not any(lot.history_gap for lot in report.lots)


@pytest.mark.parametrize("status,state,refused", [("MFS", "CA", True), ("MFS", "WA", True),
                                                  ("MFJ", "CA", False), ("MFS", "NY", False)])
def test_a_separate_return_in_a_community_property_state_is_refused(status, state, refused):
    lots = [_owned("T1", "taxpayer")]
    if refused:
        with pytest.raises(lh.LossHarvestError, match="Form 8958"):
            _scan_as(status, lots, state=state)
    else:
        _scan_as(status, lots, state=state)


def test_a_quantity_less_buy_beside_a_known_one_does_not_clear_an_older_lot():
    """The 11-20 rows were the lot's 20 shares, unrecorded, and 2.5 shares sold
    since: the 2.5 replace the old lot, which the guess showed clear."""
    old = _losing_lot("O", "VTI", "2020-01-01", 10)
    new = _losing_lot("N", "VTI", "2026-11-20", 20, "B")
    txns = [dict(_in_window_buy("VTI", None, account="B", date="2026-11-20")),
            _in_window_buy("VTI", 2.5, account="B", date="2026-11-20")]
    report, by_id = _scan([old, new], txns)
    assert "O" not in report.eligible_lot_ids


def test_the_form_8958_manifest_rule_reads_the_domicile_the_harvest_refusal_reads():
    # One predicate drives both, so the return's Form 8958 line and the
    # harvest refusal cannot disagree about the same profile.
    from agents.lib import capabilities

    profile = {"tax_year": 2025, "filing_status": "MFS", "address": {"state": "NY"},
               "residency": {"domicile_state": "CA"},
               "flags": {flag: False for flag in capabilities._REQUIRED_GATE_FLAGS}}
    profile["flags"].update({rule.flag: False for rule in capabilities.RULES if rule.state == "NY"})
    assert capabilities.in_community_property_state(profile)
    assert "community_property_mfs" in [f.rule_id for f in capabilities.evaluate(profile).manual]


def test_a_community_property_domicile_is_refused_whatever_the_mailing_address():
    """Domicile governs community property (Publication 555); a California
    domicile with a New York mailbox was counted whole."""
    result, profile = _load("result.json"), _load("taxpayer_profile.json")
    result["meta"]["filing_status"] = profile["filing_status"] = "MFS"
    profile["address"] = {"state": "NY"}
    profile["residency"] = {"domicile_state": "CA"}
    with pytest.raises(lh.LossHarvestError, match="Form 8958"):
        lh.build_harvest(result, profile, _load("tax_law_2026.json"),
                         _holdings_with([_owned("T1", "taxpayer")]), 2026, name="cp", today=TODAY)
