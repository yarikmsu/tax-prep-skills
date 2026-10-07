"""Synthetic worksheet arithmetic; no examples supply filing-law defaults."""

import copy
import json
from pathlib import Path

import pytest

from agents.lib.rollover import CurrentState, PriorReturn, WritePaths, apply_changes, compute_diff
from agents.lib.return_builder import Blocked, _check_charitable_vintages, check
from agents.lib.tax_computations import compute_ca_itemized, compute_schedule_a, rerun_schedule_a
from agents.lib.tax_computations.charitable import (
    settle_charitable_ledger, validate_charitable_ledger,
)


def vintage(ident="old", origin=2023, amount=10000, jurisdiction="federal", category="cash_public"):
    return {"id": ident, "origin_year": origin, "category": category,
            "jurisdiction": jurisdiction, "expires_after_year": origin + 5,
            "eligibility_verified": True, "source_ref": "Synthetic reviewed worksheet",
            "original_amount": amount, "used_amount": 0, "lapsed_amount": 0,
            "remaining_amount": amount}


def opening(jurisdiction="federal"):
    return {"as_of_year": 2024, "verified": True, "worksheet_ref": "Synthetic prior worksheet",
            "entries": [vintage(jurisdiction=jurisdiction)]}


def worksheet(jurisdiction="federal", itemizes=True):
    # Synthetic worksheet: 3,000 current cash + 4,000 old cash consumed,
    # leaves 10,000 - 4,000 = 6,000 in the 2023 vintage.
    return {"tax_year": 2025, "jurisdiction": jurisdiction, "agi": 100000,
            "itemizes": itemizes, "verified": True, "worksheet_ref": "Synthetic TY2025 worksheet",
            "law_source_url": "https://www.irs.gov/publications/p526", "law_source_year": 2025,
            "new_entries": [vintage("new", 2025, 3000, jurisdiction)],
            "allocations": [{"id": "old", "used_this_year": 4000, "lapsed_this_year": 0},
                            {"id": "new", "used_this_year": 3000, "lapsed_this_year": 0}],
            "deductible": 7000}


def settle(prior=None, work=None, jurisdiction="federal"):
    return settle_charitable_ledger(
        opening(jurisdiction) if prior is None else prior,
        worksheet(jurisdiction) if work is None else work,
        tax_year=2025, jurisdiction=jurisdiction, agi=100000,
        charitable_cash=3000, charitable_noncash=0)


@pytest.mark.parametrize("jurisdiction", ["federal", "CA"])
def test_settlement_preserves_vintage_and_does_not_mutate(jurisdiction):
    prior, work = opening(jurisdiction), worksheet(jurisdiction)
    before = copy.deepcopy((prior, work))
    out = settle(prior, work, jurisdiction)
    assert (prior, work) == before
    assert out["charitable_deductible"] == 7000
    assert out["charitable_carryforward"] == 6000
    old, new = out["charitable_ledger"]["entries"]
    assert (old["origin_year"], old["used_amount"], old["remaining_amount"]) == (2023, 4000, 6000)
    assert new["remaining_amount"] == 0


@pytest.mark.parametrize("key,value", [
    ("original_amount", float("nan")), ("remaining_amount", float("inf")),
    ("used_amount", True), ("lapsed_amount", -1), ("remaining_amount", 0.001),
    ("origin_year", True), ("category", "unknown"), ("jurisdiction", "CA"),
    ("eligibility_verified", False), ("source_ref", ""), ("used_amount", 1),
])
def test_bad_vintage_refuses(key, value):
    prior = opening()
    prior["entries"][0][key] = value
    with pytest.raises(ValueError):
        settle(prior)


@pytest.mark.parametrize("key,value", [
    ("verified", False), ("tax_year", 2026), ("jurisdiction", "CA"),
    ("agi", 90000), ("itemizes", None), ("law_source_year", 2024),
    ("law_source_url", "https://irs.gov.evil.example/x"), ("deductible", 7001),
    ("new_entries", []), ("allocations", []),
])
def test_bad_worksheet_refuses(key, value):
    work = worksheet()
    work[key] = value
    with pytest.raises(ValueError):
        settle(work=work)


@pytest.mark.parametrize("mutation", ["duplicate_vintage", "duplicate_allocation", "overuse", "expired"])
def test_invalid_consumption_refuses(mutation):
    prior, work = opening(), worksheet()
    if mutation == "duplicate_vintage":
        work["new_entries"][0]["id"] = "old"
    elif mutation == "duplicate_allocation":
        work["allocations"][1]["id"] = "old"
    elif mutation == "overuse":
        work["allocations"][0]["used_this_year"] = 10001
    else:
        prior["entries"][0]["expires_after_year"] = 2024
    with pytest.raises(ValueError):
        settle(prior, work)


def test_expiry_requires_explicit_lapse_without_using_the_vintage():
    prior, work = opening(), worksheet()
    prior["entries"][0]["expires_after_year"] = 2024
    work["allocations"][0].update(used_this_year=0, lapsed_this_year=10000)
    work["deductible"] = 3000
    out = settle(prior, work)
    assert out["charitable_carryforward"] == 0
    assert out["charitable_ledger"]["entries"][0]["lapsed_amount"] == 10000


def test_no_opening_requires_positive_confirmation():
    work = worksheet()
    work["allocations"] = work["allocations"][1:]
    work["deductible"] = 3000
    args = dict(tax_year=2025, jurisdiction="federal", agi=100000,
                charitable_cash=3000, charitable_noncash=0)
    with pytest.raises(ValueError, match="Confirm no opening"):
        settle_charitable_ledger(None, work, **args)
    work["no_opening_carryover_confirmed"] = True
    assert settle_charitable_ledger(None, work, **args)["charitable_carryforward"] == 0


def federal(work=None):
    return compute_schedule_a(
        100000, "single", 2025, 1000, 40000, charitable_cash=3000,
        charitable_prior_ledger=opening(), charitable_worksheet=work or worksheet())


def result_and_profile():
    return ({"meta": {"tax_year": 2025}, "form_1040": {"line_11_agi": 100000},
             "schedule_a": federal()},
            {"prior_year": {"charitable_carryforward_ledger": {"federal": opening()}}})


def test_federal_and_ca_elections_use_separate_worksheets():
    fed = federal()
    ca_work = worksheet("CA")
    ca_work["deductible"] = 5000
    ca_work["allocations"][0]["used_this_year"] = 2000
    ca = compute_ca_itemized(100000, 95000, 2025, charitable_cash=3000,
                            ca_standard_deduction=1000, phaseout_threshold=999999,
                            charitable_prior_ledger=opening("CA"), charitable_worksheet=ca_work)
    assert fed["charitable_deductible"] == 7000
    assert ca["charitable_deductible"] == 5000
    assert ca["charitable_carryforward"] == 8000
    result, profile = result_and_profile()
    result["schedule_ca_itemized"] = ca
    profile["prior_year"]["charitable_carryforward_ledger"]["CA"] = opening("CA")
    _check_charitable_vintages(result, profile)


def test_standard_election_still_records_absorption():
    work = worksheet(itemizes=False)
    out = compute_schedule_a(100000, "single", 2025, 20000, 40000,
                             charitable_cash=3000, charitable_prior_ledger=opening(),
                             charitable_worksheet=work)
    assert not out["use_itemized"]
    assert out["charitable_carryforward"] == 6000
    with pytest.raises(ValueError, match="election differs"):
        federal(work)


def test_scenario_cannot_reuse_agi_bound_allocation():
    out = federal()
    assert rerun_schedule_a(out["inputs"], 100000)["charitable_ledger"] == out["charitable_ledger"]
    with pytest.raises(ValueError, match="AGI"):
        rerun_schedule_a(out["inputs"], 90000)


@pytest.mark.parametrize("mutation", ["drop_section", "drop_ledger", "drop_inputs", "edit_balance",
                                     "edit_opening", "edit_agi", "edit_total", "edit_election"])
def test_completion_gate_catches_tampering(mutation):
    result, profile = result_and_profile()
    _check_charitable_vintages(result, profile)
    if mutation == "drop_section":
        result.pop("schedule_a")
    elif mutation == "drop_ledger":
        result["schedule_a"].pop("charitable_ledger")
    elif mutation == "drop_inputs":
        result["schedule_a"].pop("inputs")
    elif mutation == "edit_balance":
        result["schedule_a"]["charitable_ledger"]["entries"][0]["remaining_amount"] += 1
    elif mutation == "edit_opening":
        profile["prior_year"]["charitable_carryforward_ledger"]["federal"]["entries"] = []
    elif mutation == "edit_agi":
        result["form_1040"]["line_11_agi"] += 1
    elif mutation == "edit_total":
        result["schedule_a"]["charitable_carryforward"] += 1
    else:
        result["schedule_a"]["use_itemized"] = False
    with pytest.raises(Blocked):
        _check_charitable_vintages(result, profile)


def test_legacy_total_cannot_pass_completion_as_a_vintage():
    result, profile = result_and_profile()
    result["schedule_a"] = {"charitable_carryforward": 6000}
    with pytest.raises(Blocked):
        _check_charitable_vintages(result, profile)


def test_public_completion_check_calls_the_vintage_gate():
    result = json.loads((Path(__file__).parents[1] / "agents/templates/result_template.json").read_text())
    result["schedule_a"]["charitable_carryforward"] = 1000
    with pytest.raises(Blocked, match="charitable carryover unresolved"):
        check(result, {})


@pytest.mark.parametrize("mutation", [None, "missing", "wrong_total", "changed_status"])
def test_rollover_preserves_verified_ledger_or_requires_resolution(mutation, tmp_path):
    output = federal()
    result = {"meta": {"tax_year": 2025}, "schedule_a": output}
    if mutation == "missing":
        output.pop("charitable_ledger")
    elif mutation == "wrong_total":
        output["charitable_carryforward"] += 1
    profile = {"filing_status": "MFJ"}
    current = CurrentState({}, {"filing_status": "SINGLE" if mutation == "changed_status" else "MFJ"}, {})
    changes = compute_diff(PriorReturn(2025, result, profile), current, {"carryforwards"}, {})
    change = changes["carryforwards"][0]
    assert change.prior_value["verified"] is (mutation is None)
    assert change.action_suggested == ("apply" if mutation is None else "ask")
    paths = WritePaths(tmp_path / "result.json", tmp_path / "taxpayer_profile.json")
    paths.result.write_text("{}")
    paths.profile.write_text(json.dumps(current.profile))
    apply_changes(changes, {"carryforwards": "apply"}, paths)
    rolled = json.loads(paths.profile.read_text())["prior_year"]["charitable_carryforward_ledger"]["federal"]
    if mutation is None:
        validate_charitable_ledger(rolled, 2025, "federal")
        assert rolled["entries"][0]["origin_year"] == 2023
    else:
        with pytest.raises(ValueError):
            validate_charitable_ledger(rolled, 2025, "federal")


def test_zero_ledger_retires_existing_balance_but_silence_does_not():
    zero = {"as_of_year": 2025, "verified": True, "worksheet_ref": "Reviewed zero", "entries": []}
    current = CurrentState({}, {"filing_status": "MFJ", "prior_year": {
        "charitable_carryforward_ledger": {"federal": opening()}}}, {})
    prior = PriorReturn(2025, {"schedule_a": {"charitable_ledger": zero, "charitable_carryforward": 0}},
                        {"filing_status": "MFJ"})
    changes = compute_diff(prior, current, {"carryforwards"}, {})["carryforwards"]
    assert changes[0].prior_value == zero
    assert changes[0].action_suggested == "ask"
    prior.result = {}
    assert compute_diff(prior, current, {"carryforwards"}, {})["carryforwards"] == []


def test_next_year_consumption_keeps_origin_and_cumulative_usage():
    closing = settle()["charitable_ledger"]
    work = worksheet()
    work.update(tax_year=2026, law_source_year=2026, new_entries=[], deductible=1500)
    work["allocations"][0]["used_this_year"] = 1500
    work["allocations"][1]["used_this_year"] = 0
    out = settle_charitable_ledger(closing, work, tax_year=2026, jurisdiction="federal",
                                   agi=100000, charitable_cash=0, charitable_noncash=0)
    old = out["charitable_ledger"]["entries"][0]
    # 2025 uses 4,000; 2026 uses 1,500; 10,000 - 5,500 = 4,500 remains.
    assert (old["origin_year"], old["used_amount"], old["remaining_amount"]) == (2023, 5500, 4500)


def test_noncash_and_cash_sources_are_reconciled_separately():
    work = worksheet()
    work["new_entries"][0]["category"] = "noncash_appreciated_public"
    with pytest.raises(ValueError, match="source gift amounts"):
        settle(work=work)
    out = settle_charitable_ledger(opening(), work, tax_year=2025, jurisdiction="federal",
                                   agi=100000, charitable_cash=0, charitable_noncash=3000)
    assert out["charitable_deductible"] == 7000
