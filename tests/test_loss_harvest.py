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


def _build(holdings=None, tax_law=None, name="harvest", profile=None):
    return lh.build_harvest(
        _load("result.json"),
        profile if profile is not None else _load("taxpayer_profile.json"),
        tax_law if tax_law is not None else _load("tax_law_2026.json"),
        holdings if holdings is not None else _load("holdings.json"),
        2026, name=name, today=TODAY,
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
                     "cost_basis": 100, "current_value": 50}]}
    with pytest.raises(lh.LossHarvestError, match="quantity"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_rejects_zero_quantity():
    # A closed/empty lot (qty=0) would otherwise enter the harvest plan with
    # `unrealized_loss = basis - value` and inflate fed_savings for shares the
    # taxpayer no longer owns. Must be rejected at parse time.
    bad = {"lots": [{"id": "ghost", "ticker": "X", "account": "A",
                     "acquired": "2024-01-01", "quantity": 0,
                     "cost_basis": 10000, "current_value": 5000}]}
    with pytest.raises(lh.LossHarvestError, match="must be > 0"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_allows_zero_cost_basis():
    # Legitimate: gift shares, employer match (basis 0 is acceptable).
    ok = {"lots": [{"id": "g", "ticker": "X", "account": "A",
                    "acquired": "2024-01-01", "quantity": 10,
                    "cost_basis": 0, "current_value": 500}]}
    lots, _, _ = lh.parse_holdings(ok, today=TODAY)
    assert lots[0].unrealized_loss == 0  # this lot is a gain, not a loss


def test_parse_holdings_allows_zero_current_value():
    # Legitimate: worthless security harvest (§165(g)).
    ok = {"lots": [{"id": "w", "ticker": "BUST", "account": "A",
                    "acquired": "2024-01-01", "quantity": 100,
                    "cost_basis": 5000, "current_value": 0}]}
    lots, _, _ = lh.parse_holdings(ok, today=TODAY)
    assert lots[0].unrealized_loss == 5000


def test_parse_holdings_rejects_negative_basis():
    bad = {"lots": [{"id": "x", "ticker": "X", "account": "A",
                     "acquired": "2024-01-01", "quantity": 1,
                     "cost_basis": -100, "current_value": 50}]}
    with pytest.raises(lh.LossHarvestError, match="basis"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_rejects_malformed_date():
    bad = {"lots": [{"id": "x", "ticker": "X", "account": "A",
                     "acquired": "not-a-date", "quantity": 1,
                     "cost_basis": 100, "current_value": 50}]}
    with pytest.raises(lh.LossHarvestError, match="acquired"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_rejects_future_acquired_date():
    bad = {"lots": [{"id": "x", "ticker": "X", "account": "A",
                     "acquired": "2030-01-01", "quantity": 1,
                     "cost_basis": 100, "current_value": 50}]}
    with pytest.raises(lh.LossHarvestError, match="future"):
        lh.parse_holdings(bad, today=TODAY)


def test_parse_holdings_requires_unique_lot_ids():
    bad = {"lots": [
        {"id": "dup", "ticker": "A", "account": "A", "acquired": "2024-01-01",
         "quantity": 1, "cost_basis": 100, "current_value": 50},
        {"id": "dup", "ticker": "B", "account": "A", "acquired": "2024-01-01",
         "quantity": 1, "cost_basis": 100, "current_value": 50},
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
            "cost_basis": 100, "current_value": 50}


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
    h = {"as_of": "2026-12-15",
         "lots": [{"id": "ST", "ticker": "X", "account": "A",
                   "acquired": "2026-06-01", "quantity": 10,  # short-term lot
                   "cost_basis": 1000, "current_value": 500}]}
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
                   "cost_basis": 100, "current_value": 500}]}  # gain, not loss
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
    return {"as_of": "2026-12-15", "lots": lots,
            "recent_transactions": (txns or [])}


def test_proration_1_share_drip_against_100_share_lot():
    # DRIP buys 1 share of VTSAX inside the window; user wants to harvest the
    # 100-share losing lot. §1091 should disallow 1% of the loss only.
    h = _holdings_with(
        lots=[{"id": "BIG", "ticker": "VTSAX", "account": "A",
               "acquired": "2024-01-01", "quantity": 100,
               "cost_basis": 12000, "current_value": 10000}],   # loss $2000
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
               "cost_basis": 5000, "current_value": 3000}],
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
               "cost_basis": 5000, "current_value": 3000}],
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
               "cost_basis": 5000, "current_value": 3000}],
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
             "cost_basis": 3000, "current_value": 2100},  # loss $900, LT
            {"id": "B", "ticker": "ABC", "account": "A",
             "acquired": "2025-01-01", "quantity": 80,
             "cost_basis": 8000, "current_value": 6000},  # loss $2000, ST
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
             "cost_basis": 1500, "current_value": 1000},   # loss $500, qty 3
            {"id": "new", "ticker": "TSLA", "account": "A",
             "acquired": "2025-01-01", "quantity": 100,
             "cost_basis": 30000, "current_value": 25000}, # loss $5000, qty 100
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

def test_parse_holdings_defaults_tax_advantaged_to_false():
    # Holdings files written under v1 (before this field existed) must still
    # parse — every lot defaults to taxable. Forward-compat for older fixtures.
    h = _holdings_with(
        lots=[{"id": "x", "ticker": "X", "account": "A",
               "acquired": "2024-01-01", "quantity": 10,
               "cost_basis": 100, "current_value": 50}],
    )
    lots, _, _ = lh.parse_holdings(h, today=TODAY)
    assert lots[0].tax_advantaged is False


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
             "cost_basis": 5000, "current_value": 3000},  # control: taxable, eligible
        ],
    )
    r = _build(holdings=h)
    assert "ira" not in r.eligible_lot_ids
    assert "tax" in r.eligible_lot_ids
    # The IRA's $3000 loss is accounted for under tax_advantaged_loss, not
    # silently dropped.
    assert r.tax_advantaged_loss == 3000
    assert r.total_loss_harvested == 2000  # only the taxable ABC lot


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
               "cost_basis": 4000, "current_value": 2000}],   # $2000 LT loss, taxable
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
               "cost_basis": 1000, "current_value": 500}],
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
               "cost_basis": 1000, "current_value": 700}],
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
             "cost_basis": 1000, "current_value": 700},
            {"id": "B", "ticker": "ABC", "account": "Schwab",
             "acquired": "2026-12-01", "quantity": 5,
             "cost_basis": 500, "current_value": 600},  # at a GAIN, still held
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
               "cost_basis": 1000, "current_value": 700}],
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
               "cost_basis": 1000, "current_value": 700}],
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
               "cost_basis": 1000, "current_value": 700}],
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
             "cost_basis": 1000, "current_value": 700},
            {"id": "D", "ticker": "DEF", "account": "Schwab",
             "acquired": "2020-01-01", "quantity": 5,  # acquired out of window
             "cost_basis": 1000, "current_value": 700},
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
             "cost_basis": 1000, "current_value": 700},
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
               "cost_basis": 1000, "current_value": 700}],
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
               "cost_basis": 1000, "current_value": 700}],
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
               "cost_basis": 1000, "current_value": 700}],
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
               "cost_basis": 1000, "current_value": 700}],
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
               "cost_basis": 1000, "current_value": 700}],
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


def test_same_date_inf_consumes_remaining_budget_when_known_partial():
    # Sparse-data simple case preserved: budget=10, known buy of 5, then inf.
    # Sorted qty-known-first: 5 consumes 5 of budget (budget=5), inf claims
    # remaining 5 (contribution 0, budget=0). Pool=0 → harvestable.
    # Documents the inf-fills-remaining-budget heuristic.
    h = _holdings_with(
        lots=[{"id": "A", "ticker": "ABC", "account": "Schwab",
               "acquired": "2026-12-01", "quantity": 10,
               "cost_basis": 1000, "current_value": 700}],
        txns=[
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy", "quantity": 5},
            {"ticker": "ABC", "account": "Schwab", "date": "2026-12-01",
             "kind": "buy"},  # qty unknown
        ],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "clear"


def test_multi_lot_same_group_shared_budget():
    # Two harvest lots share the same (date, account) — budget = A.qty + B.qty.
    # Single 20-share buy: contribution = 20 - 15 = 5 → pool 5 distributed FIFO
    # across A (oldest order, here same date so by lot.id semantics; A=10 →
    # consumes 5, B=5 → consumes 0). A 50% blocked, B clear.
    h = _holdings_with(
        lots=[
            {"id": "A", "ticker": "ABC", "account": "Schwab",
             "acquired": "2026-12-01", "quantity": 10,
             "cost_basis": 1000, "current_value": 700},  # loss $300
            {"id": "B", "ticker": "ABC", "account": "Schwab",
             "acquired": "2026-12-01", "quantity": 5,
             "cost_basis": 500, "current_value": 350},   # loss $150
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
               "cost_basis": 1000, "current_value": 700}],
        txns=[{"ticker": "ABC", "account": "  schwab brokerage  ",
               "date": "2026-12-01", "kind": "buy", "quantity": 10}],
    )
    r = _build(holdings=h)
    lot = {l.id: l for l in r.lots}["A"]
    assert lot.wash_sale_status == "clear"


def test_proration_pool_exhausted_leaves_later_lots_clear():
    h = _holdings_with(
        lots=[
            {"id": "first",  "ticker": "ZZZ", "account": "A",
             "acquired": "2020-01-01", "quantity": 10,
             "cost_basis": 1000, "current_value": 500},
            {"id": "second", "ticker": "ZZZ", "account": "A",
             "acquired": "2025-01-01", "quantity": 10,
             "cost_basis": 1000, "current_value": 500},
        ],
        # 10-share replacement pool exactly absorbed by the first lot.
        txns=[{"ticker": "ZZZ", "date": "2026-12-10", "kind": "buy", "quantity": 10}],
    )
    r = _build(holdings=h)
    ids = {l.id: l for l in r.lots}
    assert ids["first"].wash_sale_status == "fully_blocked"
    assert ids["second"].wash_sale_status == "clear"
    assert ids["second"].disallowed_loss == 0


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
                  "cost_basis": 100, "current_value": 500}],
    }
    r = _build(holdings=h)
    assert r.total_loss_harvested == 0
    assert r.fed_savings == 0
    assert r.state_savings == 0
    assert r.eligible_lot_ids == []


def test_build_harvest_loss_exceeds_gains_triggers_3k_cap_and_carryforward():
    # No baseline gains — synthesize a result.json fixture with zero Sched D.
    h = {
        "as_of": "2026-12-15",
        "lots": [{"id": "BIG", "ticker": "ZZZ", "account": "A",
                  "acquired": "2020-01-01", "quantity": 100,
                  "cost_basis": 60000, "current_value": 50000}],  # LT loss 10000
    }
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
    h = {
        "as_of": "2026-12-15",
        "lots": [{"id": "BIG", "ticker": "ZZZ", "account": "A",
                  "acquired": "2020-01-01", "quantity": 100,
                  "cost_basis": 60000, "current_value": 50000}],
    }
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
               "cost_basis": 5000, "current_value": 3000}],
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
