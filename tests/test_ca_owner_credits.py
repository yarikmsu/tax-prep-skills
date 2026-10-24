"""Hand-worked TY2025 FTB3510/3804-CR and Schedule P credit order."""

import pytest

from agents.lib import return_builder as B, tie_out as T
from agents.lib.rollover import PriorReturn, CurrentState, compute_diff
from agents.lib.tax_computations import (
    compute_ca_amt_credit, compute_ca_ptet_owner, compute_ca_credit_order,
)
from tests.test_ca_540_builder import case, law


def ordered(**changes):
    # Schedule P line1=9,000 after1,000 exemptions; TMT6,000.
    data = dict(regular_tax=10000, exemption_credits=1000, form540_line35=9000,
        tentative_minimum_tax=6000, dependent_care=0, renters_credit=0,
        other_credits=[], opening_amt_credit=8000, new_deferral_credit=0,
        tax_year=2025, ptet_current=[], ptet_carryovers=[], ptet_carryforward_years=None, ptet_first_tax_year=None)
    data.update(changes)
    return compute_ca_credit_order(**data)


def test_ftb3510_uses_exemptions_and_other_a_credits_but_not_b_credits():
    # FTB3510: line34=1,000+500=1,500; line35=8,500;
    # line37=8,500-6,000=2,500; use2,500 of8,000, unused5,500.
    # B3 then consumes1,000, leaving5,000 for B4 (below TMT).
    p = ordered(dependent_care=500, other_credits=[{"code": "187", "amount": 1000}])
    f = p["ftb_3510"]
    assert (f["line_34"], f["line_37"], f["line_38"], f["line_39"]) == (1500, 2500, 2500, 5500)
    assert p["regular_tax_remaining"] == 5000
    assert p["line_43"] == 2500 and p["line_44"] == 1000


def test_ptet_uses_regular_tax_below_tmt_and_follows_ostc():
    current = [{"credit_id": "p-current", "source_id": "k1-a", "amount": 7000}]
    p = ordered(dependent_care=500, other_credits=[{"code": "187", "amount": 1000}],
                ptet_current=current, ptet_carryforward_years=5, ptet_first_tax_year=2021)
    # After A1/188:6,000. OSTC1,000 ->5,000. B4 uses5,000;
    # not zero (regular tax minus TMT) and not6,000 (before OSTC).
    f = p["ftb_3804_cr"]
    assert (f["line_1"], f["line_4"], f["line_5"]) == (7000, 5000, 2000)
    assert f["carryovers_to_next_year"] == [{"credit_id": "p-current", "origin_year": 2025, "amount": 2000}]
    # Three positive other credits must all go to line45 alone.
    assert (p["line_43"], p["line_44"], p["line_45"]) == (0, 0, 8500)
    assert p["regular_tax_remaining"] == 0


def test_nonzero_tmt_matters_even_when_no_amt_is_due():
    f = compute_ca_amt_credit(8000, 10000, 1000, 6000)
    assert f["line_38"] == 3000  # zero AMT is not zero TMT
    assert f["carryforward_to_next_year"] == 5000
    f = compute_ca_amt_credit(8000, 10000, 1000, 11000, new_deferral_credit=1000)
    assert f["line_38"] == 0
    assert f["carryforward_to_next_year"] == 9000


def test_below_screening_income_has_no_credit_screen():
    # No amt_screening input exists. Tiny synthetic regular tax/exemption:
    # 80-10-50=20 allowable, even below any Schedule P Box C threshold.
    f = compute_ca_amt_credit(100, 80, 10, 50)
    assert f["line_38"] == 20


def test_other_a2_precedes_188_and_renter_is_b1():
    p = ordered(other_credits=[{"code": "234", "amount": 500}], renters_credit=100)
    assert p["ftb_3510"]["line_38"] == 2500
    assert p["regular_tax_remaining"] == 5900
    assert [r["code"] for r in p["credits"]] == ["234", "188", "242"]


@pytest.mark.parametrize("changes", [
    {"dependent_care": 3001}, {"other_credits": [{"code": "187", "amount": 6001}]},
    {"other_credits": [{"code": "188", "amount": 1}]},
    {"other_credits": [{"code": "242", "amount": 1}]},
    {"other_credits": [{"code": "181", "amount": 1}]},
    {"other_credits": [{"code": "999", "amount": 1}]},
    {"other_credits": [{"code": "234", "section": "B2", "amount": 1}]},
    {"other_credits": [{"code": "187", "amount": 1}, {"code": "187", "amount": 1}]},
    {"tax_year": 2026}, {"tax_year": True},
])
def test_invalid_order_inputs_refuse(changes):
    with pytest.raises(ValueError):
        ordered(**changes)


def test_ptet_vintages_oldest_first_and_expiry_is_not_refreshed():
    lots = [{"credit_id": "recent", "origin_year": 2024, "amount": 300},
            {"credit_id": "last-year", "origin_year": 2022, "amount": 500},
            {"credit_id": "expired", "origin_year": 2021, "amount": 100}]
    # Isolated vintage arithmetic in2027, not an enabled2027 Form540.
    f = compute_ca_ptet_owner(tax_year=2027, current_credits=[], carryovers=lots,
                              net_tax=200, carryforward_years=5, first_tax_year=2021)
    assert (f["line_2"], f["line_4"], f["line_5"]) == (800, 200, 600)
    assert f["vintages"][0]["credit_id"] == "last-year"
    assert f["vintages"][0]["unused"] == 300
    assert f["vintages"][0]["expires_after_this_year"] is True
    assert f["expired_before_this_year"][0]["amount"] == 100
    assert f["carryovers_to_next_year"] == [{"credit_id": "recent", "origin_year": 2024, "amount": 300}]


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf"), "1", None])
def test_credit_amounts_are_finite_numbers(bad):
    with pytest.raises((ValueError, TypeError)):
        compute_ca_amt_credit(bad, 100, 0, 0)
    with pytest.raises((ValueError, TypeError)):
        compute_ca_ptet_owner(tax_year=2025, current_credits=[
            {"credit_id": "p", "source_id": "k1", "amount": bad}], carryovers=[],
            net_tax=100, carryforward_years=5, first_tax_year=2021)


def builder_case():
    profile, result = case()
    profile["flags"].update(ca_has_amt_credit=True, ca_has_ptet_credit=True)
    profile["prior_year"] = {"ca_amt_credit_carryforward": 1000,
        "ca_ptet_credit_carryovers": [{"credit_id": "old", "origin_year": 2021, "amount": 500}]}
    profile["ca_credits"] = {"amt_opening_confirmed": True, "other_credits": [],
        "ptet_eligibility_confirmed": True, "ptet_owner_type": "direct_individual",
        "ptet_current": [{"credit_id": "new", "source_id": "k1-a", "amount": 5000}]}
    ledger = T.load_ledger({"records": [{"doc_id": "k1-a", "file": "documents/k1.pdf",
        "form_type": "CA K-1", "payer": "Synthetic entity", "person": "primary",
        "key_boxes": {"ca_ptet_credit": 5000}, "skipped": None}]})
    # The entity deducted its 5,000 PTE tax federally, so federal AGI is 95,000
    # and Schedule CA adds it back: CA AGI stays 100,000 (FTB PTE elective tax).
    result["form_1040"]["line_11_agi"] = 95000
    profile["flags"]["ca_has_other_schedule_ca_adjustments"] = True
    result["california_540"]["schedule_ca_adjustments"].update(
        other_additions=5000, other_adjustments_confirmed=True)
    return profile, result, ledger


def test_builder_derives_claims_settlement_and_does_not_use_entered_allowable():
    profile, result, ledger = builder_case()
    # Synthetic Form540:4,500 line31 -60 exemptions=4,440.
    # TMT0; AMT credit1,000 then PTET3,440, unused PTET2,060.
    result["california_540"]["schedule_p"].update(ftb3510_credit_allowable=9999,
        ftb3510_credit_available=9999, ftb3510_worksheet_confirmed=False)
    built = B.build_california(result, {"state": law()}, ledger, profile)
    assert built["schedule_p"]["ftb3510_credit_allowable"] == 1000
    assert built["schedule_p"]["carryforward_to_next_year"] == 0
    assert (built["line_43"], built["line_44"], built["line_64"]) == (1000, 3440, 0)
    assert built["ftb_3804_cr"]["line_5"] == 2060
    assert built["ftb_3804_cr"]["carryovers_to_next_year"] == [
        {"credit_id": "new", "origin_year": 2025, "amount": 2060}]
    # AMT credit is not a payment; K-1 PTET does not inflate line78.
    assert built["line_78"] == 0
    result["california_540"].update(built)
    assert not T._ca_owner_credit_checks(ledger, result, profile, {"state": law()})
    result["california_540"]["schedule_p"]["ftb_3510"]["line_38"] += 1
    assert T._ca_owner_credit_checks(ledger, result, profile, {"state": law()})


@pytest.mark.parametrize("change", ["no_flag", "short"])
def test_a_ptet_credit_needs_its_california_addback(change):
    # FTB: a PTE elective tax the entity deducted federally is added back for
    # California; claiming the credit without it under-states CA income.
    profile, result, ledger = builder_case()
    if change == "no_flag":
        profile["flags"]["ca_has_other_schedule_ca_adjustments"] = False
        result["california_540"]["schedule_ca_adjustments"]["other_additions"] = 0
    else:
        result["california_540"]["schedule_ca_adjustments"]["other_additions"] = 4999
    with pytest.raises(B.Blocked, match="add back the PTE elective tax"):
        B.build_california(result, {"state": law()}, ledger, profile)


@pytest.mark.parametrize("opening", [0, None])
def test_an_amt_credit_flag_cannot_pair_with_an_absent_opening_credit(opening):
    profile, result, ledger = builder_case()
    if opening is None:
        del profile["prior_year"]["ca_amt_credit_carryforward"]
    else:
        profile["prior_year"]["ca_amt_credit_carryforward"] = 0
    with pytest.raises(B.Blocked, match="ca_has_amt_credit says"):
        B.build_california(result, {"state": law()}, ledger, profile)


@pytest.mark.parametrize("field,value", [
    ("amt_opening_confirmed", False), ("other_credits", None),
    ("ptet_eligibility_confirmed", False), ("ptet_owner_type", "disregarded_sml lc"),
    ("ptet_current", []),
])
def test_new_facts_cannot_be_waived_by_old_acknowledgements(field, value):
    profile, result, ledger = builder_case()
    profile["acknowledged_limitations"] = ["ca_amt_credit", "ca_ptet_credit"]
    profile["ca_credits"][field] = value
    with pytest.raises(B.Blocked):
        B.build_california(result, {"state": law()}, ledger, profile)


def test_false_flags_cannot_drop_sources_or_carryovers():
    profile, result, ledger = builder_case()
    profile["flags"].update(ca_has_amt_credit=False, ca_has_ptet_credit=False)
    built = B.build_california(result, {"state": law()}, ledger, profile)
    assert built["schedule_p"]["ftb3510_credit_allowable"] == 1000
    assert built["ftb_3804_cr"]["reported_credit_total"] == 5000
    profile["ca_credits"]["ptet_current"] = []
    with pytest.raises(B.Blocked, match="source_id"):
        B.build_california(result, {"state": law()}, ledger, profile)


def test_ptet_law_and_both_amt_rates_are_required():
    profile, result, ledger = builder_case()
    for block, field in (("ptet_credit", "carryforward_years"), ("ptet_credit", "first_tax_year"),
                         ("amt", "tmt_rate"), ("amt", "phaseout_rate")):
        bad = law()
        bad[block][field] = None
        with pytest.raises(B.Blocked):
            B.build_california(result, {"state": bad}, ledger, profile)


def test_ptet_rollover_preserves_origin_year_without_adding_it_twice():
    profile, result, ledger = builder_case()
    result["california_540"].update(B.build_california(result, {"state": law()}, ledger, profile))
    changes = compute_diff(PriorReturn(2025, result, profile),
        CurrentState({}, {"tax_year": 2026}, {}), tax_law_new={}, categories={"carryforwards"})
    rows = [r for r in changes["carryforwards"] if r.json_path == "prior_year.ca_ptet_credit_carryovers"]
    assert len(rows) == 1
    assert rows[0].prior_value == [{"credit_id": "new", "origin_year": 2025, "amount": 2060}]


def test_builder_nonzero_tmt_and_exemptions_limit_credit_before_ptet():
    profile, result, ledger = builder_case()
    profile["prior_year"]["ca_amt_credit_carryforward"] = 5000
    profile["ca_credits"]["other_credits"] = [{"code": "234", "amount": 200}, {"code": "187", "amount": 200}]
    result["california_540"]["line_40"] = 100
    cfg = law()
    cfg["amt"]["exemption"]["single"] = 50000
    cfg["amt"]["tmt_rate"] = .06
    # AMTI100k, exemption50k =>TMT3,000, below line31=4,500 (zeroAMT).
    # Line35=4,440; A1/A2=300; code188=1,140, not1,200.
    # B3=200; code242 uses2,800 even though no excess over TMT remains.
    built = B.build_california(result, {"state": cfg}, ledger, profile)
    assert built["line_61"] == 0
    assert built["schedule_p"]["ftb3510_credit_allowable"] == 1140
    assert built["schedule_p"]["carryforward_to_next_year"] == 3860
    assert built["ftb_3804_cr"]["line_4"] == 2800
    assert (built["line_43"], built["line_44"], built["line_45"]) == (0, 0, 4340)
    assert built["line_64"] == 0


def test_ptet_does_not_reduce_amt_or_prevent_new_iso_credit():
    profile, result, ledger = builder_case()
    result["california_540"]["schedule_p"]["iso_bargain_element"] = 200000
    # TMT14,000 less regular4,500=9,500 AMT. PTET offsets4,440
    # regular tax only. Opening AMT1,000 +newISO9,500=10,500.
    built = B.build_california(result, {"state": law()}, ledger, profile)
    assert built["line_61"] == built["line_64"] == 9500
    assert built["schedule_p"]["ftb3510_credit_allowable"] == 0
    assert built["schedule_p"]["carryforward_to_next_year"] == 10500
    assert built["ftb_3804_cr"]["line_4"] == 4440


def test_rebuilding_after_reconciled_zero_balances_clears_old_claims():
    profile, result, ledger = builder_case()
    result["california_540"].update(B.build_california(result, {"state": law()}, ledger, profile))
    profile["flags"].update(ca_has_amt_credit=False, ca_has_ptet_credit=False)
    profile["prior_year"] = {}
    profile["ca_credits"]["ptet_current"] = []
    built = B.build_california(result, {"state": law()}, [], profile)
    assert (built["line_43"], built["line_44"], built["line_45"]) == (0, 0, 0)
    assert built["line_64"] == 4440
    assert built["ftb_3804_cr"]["carryovers_to_next_year"] == []


def test_ptet_expired_balance_does_not_leave_an_unreconciled_current_carry():
    old = {"california_540": {"ftb_3804_cr": {"carryovers_to_next_year": []}}}
    changes = compute_diff(PriorReturn(2025, old, {}),
        CurrentState({}, {"tax_year": 2026, "prior_year": {"ca_ptet_credit_carryovers": [
            {"credit_id": "old", "origin_year": 2021, "amount": 100}]}}, {}),
        tax_law_new={}, categories={"carryforwards"})
    (row,) = [r for r in changes["carryforwards"] if r.json_path == "prior_year.ca_ptet_credit_carryovers"]
    assert row.prior_value == [] and row.action_suggested == "ask"


@pytest.mark.parametrize("period", [None, True, 0, -1, 5.0, float("nan"), "5"])
def test_unfetched_or_malformed_carry_period_refuses(period):
    with pytest.raises(ValueError):
        compute_ca_ptet_owner(tax_year=2025, current_credits=[
            {"credit_id": "p", "source_id": "k1", "amount": 100}], carryovers=[],
            net_tax=100, carryforward_years=period, first_tax_year=2021)


def test_per_source_reconciliation_cannot_be_replaced_with_an_aggregate():
    profile, result, ledger = builder_case()
    profile["ca_credits"]["ptet_current"][0]["source_id"] = "different-source"
    with pytest.raises(B.Blocked, match="source_id"):
        B.build_california(result, {"state": law()}, ledger, profile)


@pytest.mark.parametrize("flag", ["ca_has_amt_credit", "ca_has_ptet_credit"])
def test_supported_credit_screens_still_require_explicit_answers(flag):
    from agents.lib import capabilities as C
    from tests.test_capabilities import _profile
    profile = _profile()
    del profile["flags"][flag]
    assert any(flag in finding.note for finding in C.evaluate(profile).blocking)


def test_standalone_credit_tieout_rejects_an_uncomputed_manual_result():
    profile, result, ledger = builder_case()
    assert T._ca_owner_credit_checks(ledger, result, profile, {"state": law()})
    built = B.build_california(result, {"state": law()}, ledger, profile)
    result["california_540"].update(built)
    result["meta"]["limitations"] = [built["line_113_note"], built["ca_credit_note"]]
    assert not B.california_differences(result, built)
    result["meta"]["limitations"].remove(built["ca_credit_note"])
    assert "meta.limitations (CA owner credits)" in B.california_differences(result, built)


def test_main_tieout_recomputes_credit_workpapers_without_a_builder_marker():
    from tests.test_ca_scope import _credit_case
    profile, result = _credit_case()
    assert not T.evaluate([], result, [], profile=profile).failed
    result["california_540"]["credit_workpaper"]["ftb_3510"]["line_33"] += 1
    assert T.evaluate([], result, [], profile=profile).failed


def test_a_carryover_before_the_credit_regime_is_not_eligible():
    with pytest.raises(ValueError, match="predates"):
        compute_ca_ptet_owner(tax_year=2025, current_credits=[], carryovers=[
            {"credit_id": "invalid", "origin_year": 2020, "amount": 100}],
            net_tax=100, carryforward_years=5, first_tax_year=2021)


def test_validation_does_not_echo_private_source_text():
    private = "private source text"
    with pytest.raises(ValueError) as exc:
        compute_ca_amt_credit(private, 100, 0, 0)
    assert private not in str(exc.value)
    profile, result, ledger = builder_case()
    profile["ca_credits"]["ptet_current"][0]["amount"] = private
    with pytest.raises(B.Blocked) as exc:
        B.build_california(result, {"state": law()}, ledger, profile)
    assert private not in str(exc.value)


def test_independent_tieout_fails_cleanly_on_missing_year_or_malformed_address():
    profile, result, ledger = builder_case()
    profile["address"] = "private address text"
    errors = T._ca_owner_credit_checks(ledger, result, profile, {"state": law()})
    assert errors and "private address text" not in str(errors)
    profile["address"] = {"state": "CA"}
    del profile["tax_year"]
    del result["meta"]["tax_year"]
    assert T._ca_owner_credit_checks(ledger, result, profile, {"state": law()})


def test_credit_check_does_not_demand_unrelated_facts_without_credit_activity():
    assert not T._ca_owner_credit_checks([], {"form_1040": {}}, {"address": {"state": "CA"}}, None)


def test_freshness_skips_ptet_seeds_but_flags_an_unfetched_ca_amt_rate(tmp_path):
    # A non-PTET filer must not read INCOMPLETE for the carryover period, but
    # CA's AMT phase-out rate is required where the federal one is a default.
    import json
    from datetime import datetime
    from agents.lib import tax_law_fetcher as F
    from agents.lib.secure_fs import write_secure
    data = F.create_empty_tax_law(2025)
    data["state"] = F.create_state_template("CA", tax_year=2025)
    data["_meta"] = {"fetched": datetime.now().isoformat()}
    write_secure(tmp_path / "tax_law_2025.json", json.dumps(data))
    nulls = F.check_freshness(2025, str(tmp_path))["null_fields"]
    assert not any(n.startswith("state.ptet_credit") for n in nulls)
    assert any(n == "state.amt.phaseout_rate" for n in nulls)
    assert not any(n == "federal.amt.phaseout_rate" for n in nulls)
