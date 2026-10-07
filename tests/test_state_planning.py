"""State marginal effects: published NC/NY worksheets and synthetic CA law."""

import copy
import dataclasses
import datetime
import json
import subprocess
from pathlib import Path

import pytest

from agents.lib import roth_ladder, scenario
from agents.lib.secure_fs import write_secure
from agents.lib.tax_computations import compute_nc_d400
from agents.lib.tax_computations.state import planning
from tests.ny_fixtures import ny_it203_cache, _NY_2025_TAX_TABLE
from tests.test_nc_state import cfg, _NO_SCHED_S
from tests.test_tax_computations import _NY_2025_ITEMIZED_REDUCTION, _NY_2025_CHARITABLE_LIMIT

REPO = Path(__file__).parents[1]
TODAY = datetime.date(2026, 10, 7)
LAW = json.loads((REPO / "tests/fixtures/scenario/tax_law_2026.json").read_text())
PROFILE = json.loads((REPO / "tests/fixtures/scenario/taxpayer_profile.json").read_text())


def case(code="NC", agi=60000, status="MFJ"):
    result = {"meta": {"tax_year": 2025, "filing_status": status},
              "form_1040": {"line_1z_total_wages": agi, "line_11_agi": agi,
                            "line_12_standard_deduction": 30000, "line_24_total_tax": 5000}}
    plan = dict(tax_year=2025, state=code, filing_status=status,
                residency="full_year", scope_confirmed=True,
                income_treatment={k: "taxable" for k in planning._INCOME_KEYS},
                retirement_basis_matches_federal=True)
    law = copy.deepcopy(LAW)
    law["tax_year"] = 2025
    if code == "NC":
        law["state"] = cfg()
        plan["nc"] = dict(schedule_s=copy.deepcopy(_NO_SCHED_S), ctc_qualifying_children=1,
                          itemized=None, spouse_itemizes_nc=None, claimed_as_dependent=False,
                          eligible_for_federal_standard_deduction=True)
        result["north_carolina_d400"] = compute_nc_d400(2025, status, agi, law["state"], **plan["nc"])
    elif code == "CA":
        # Synthetic (not filing figures):10%flat,10kstandard,200/headcredit,
        # phaseout100k in1k steps at10/head,AMTscreen1m,table below20k.
        law["state"] = dict(code="CA", type="graduated_income",
             brackets=[dict(min=0, max=None, rate=.1)],
             standard_deduction={s: 10000 for s in ("single", "mfj", "mfs", "hoh", "qss")},
             itemized_phaseout={s: 100000 for s in ("single", "mfj", "hoh")},
             personal_exemption={"amount": 200}, dependent_exemption={"amount": 400},
             exemption_phaseout={"threshold": {status.lower(): 100000},
                                  "band": {status.lower(): 1000}, "reduction_per_step": 10},
             amt_screening={status.lower(): 1000000},
             mental_health_surcharge={"threshold": 2000000, "rate": .01},
             tax_table={"max_taxable_income": 20000, "rows": [
                 {"min": 0, "max": 10000, "tax": {status.lower(): 500}},
                 {"min": 10000, "max": 20001, "tax": {status.lower(): 1500}}]})
        plan["ca"] = dict(additions=0, subtractions_except_ss=0,
                         no_amt_preferences_confirmed=True, itemized={},
                         personal_blind_senior_count=2, dependent_count=0)
        result["california_540"] = {"ca_total_tax": max(0, (agi - 10000) * .1 - 400)}
    else:
        law["state"] = ny_it203_cache()
        law["state"].update(itemized_reduction=copy.deepcopy(_NY_2025_ITEMIZED_REDUCTION),
                             charitable_limit=copy.deepcopy(_NY_2025_CHARITABLE_LIMIT))
        law["state"]["nyc_tax"]["tax_table"] = copy.deepcopy(_NY_2025_TAX_TABLE)
        law["state"]["nyc_tax"]["tax_table"]["table_bases"] = []
        law["state"]["nyc_tax"]["tax_table"]["row_segments"][:2] = [
            {"over": 0, "not_over": 18, "row_width": 18},
            {"over": 18, "not_over": 25, "row_width": 7}]
        law["state"]["nyc_tax"]["household_credit"] = copy.deepcopy(law["state"]["credits"]["household"])
        plan["ny"] = dict(additions={}, subtractions={}, dependent_count=0,
                         claimed_as_dependent=False, it196_fixed_base_confirmed=True,
                         itemized=dict(itemized_before_adjustment=0, charitable_contributions=0),
                         local=dict(nyc_resident=False, yonkers_resident=False,
                                    yonkers_nonresident_earnings=False,
                                    mctmt=dict(zone_1_net_earnings=0, zone_2_net_earnings=0)))
        base = planning._ny(agi, 0, 0, {**plan}, law["state"], status.lower())
        result["new_york_it201"] = {"line_60_total_taxes": base["total_tax"], "line_59_sales_or_use_tax": 0}
    result["state_planning_inputs"] = plan
    return result, law


def report(result, law, delta=None, base=None):
    return scenario.build_report(result, PROFILE, law, 2025, "state", delta or {"ira_distribution": 1000}, base, TODAY)


def test_nc_child_cliff_is_repriced_not_delta_times_rate():
    result, law = case()
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    before, after = r.state_effect["points"]
    # NC TY2025 MFJ 60k: child2,500;61k:child2,000. Taxable32k->33.5k;
    # 4.25% yields1,360->1,423.75, delta63.75 rather than42.50.
    assert [p["child_deduction"] for p in (before, after)] == [2500, 2000]
    assert [p["anchored_total_tax"] for p in (before, after)] == [1360, 1423.75]
    assert r.state_effect["delta_total_tax"] == 63.75
    assert r.diff.anchored_scenario_total_tax == 5200  # federal anchor independent
    assert r.state_effect["combined_delta_tax"] == 263.75
    assert r.state_effect["combined_marginal_rate"] == .2637


def test_nc_medical_floor_and_election_move():
    result, law = case(agi=80000)
    raw = result["state_planning_inputs"]["nc"]
    raw["itemized"] = dict(medical=32000)
    # 32k-6k=26k beats25.5k standard. At90k medical25,250 loses toelection.
    result["north_carolina_d400"] = compute_nc_d400(2025, "MFJ", 80000, law["state"], **raw)
    r = report(result, law, {"ira_distribution": 10000})
    assert r.state_effect["modeled"], r.state_effect
    assert [p["deduction"] for p in r.state_effect["points"]] == [26000, 25500]
    # Childdeduction2k->1.5k:taxable52k->63k,11k*.0425=467.50.
    assert r.state_effect["delta_total_tax"] == 467.50


def test_actual_return_agi_frame_and_separation_from_simplified_baseline():
    result, law = case()
    result["form_1040"]["line_1z_total_wages"] = 80000  # simplified model's AGI differs
    r = report(result, law)
    assert r.base_outcome.agi == 80000
    assert [p["agi"] for p in r.state_effect["points"]] == [60000, 61000]
    assert r.state_effect["delta_total_tax"] == 63.75


@pytest.mark.parametrize("code", ["CA", "NC", "NY"])
def test_states_remove_extra_taxable_social_security(code):
    result, law = case(code, agi=55350)
    from tests.test_scenario_social_security import baseline
    ss = baseline()
    ss["meta"]["tax_year"] = 2025
    ss["social_security_worksheet"]["tax_year"] = 2025
    ss["state_planning_inputs"] = result["state_planning_inputs"]
    if code == "CA":
        ss["california_540"] = {"ca_total_tax": 2600}  # (40k-10k)*10%-400
    elif code == "NC":
        raw = result["state_planning_inputs"]["nc"]
        raw["schedule_s"]["taxable_social_security"] = 15350
        ss["north_carolina_d400"] = compute_nc_d400(2025, "MFJ", 55350, law["state"], **raw)
    else:
        base = planning._ny(55350, 15350, 0, result["state_planning_inputs"], law["state"], "mfj")
        ss["new_york_it201"] = {"line_60_total_taxes": base["total_tax"], "line_59_sales_or_use_tax": 0}
    r = report(ss, law)
    assert r.state_effect["modeled"], r.state_effect
    assert r.diff.delta_agi == 1850
    assert round(r.state_effect["points"][1]["agi"] - r.state_effect["points"][0]["agi"], 2) == 1000


def test_ca_exemption_credit_steps_and_medical_floor():
    result, law = case("CA", 100000)
    raw = result["state_planning_inputs"]["ca"]["itemized"]
    raw["medical_expenses"] = 30000
    result["california_540"]["ca_total_tax"] = 7350  # (100k-22.5k)*10%-400
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    assert [p["deduction"] for p in r.state_effect["points"]] == [22500, 22425]
    assert [p["exemption_credit"] for p in r.state_effect["points"]] == [400, 380]
    assert r.state_effect["delta_total_tax"] == 127.5  # 1,075*.1 +20lostcredits


def test_ca_uses_published_table_and_refuses_missing_row():
    result, law = case("CA", 29000)
    result["california_540"]["ca_total_tax"] = 1100  # fetchedtable1,500-credit400
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    assert r.state_effect["delta_total_tax"] == 0  # bothinpublishedsame row
    law["state"]["tax_table"]["rows"] = []
    r = report(result, law)
    assert not r.state_effect["modeled"]
    assert "Tax Table row" in r.state_effect["limitations"][0]


def test_ny_pension_exclusion_per_owner_cap_reprices():
    result, law = case("NY", 80000)
    plan = result["state_planning_inputs"]
    plan["income_treatment"]["ira_distribution"] = "ny_pension"
    plan["retirement_owner"] = "primary"
    plan["ny"]["subtractions"]["pension_annuity"] = [
        dict(taxpayer="primary", entitled=True, qualifying_amount=19500),
        dict(taxpayer="spouse", entitled=True, qualifying_amount=1000)]
    result["form_1040"].update(line_1z_total_wages=59500, line_4b_taxable_ira=20500)
    before = planning._ny(80000, 0, 0, plan, law["state"], "mfj")
    result["new_york_it201"]["line_60_total_taxes"] = before["total_tax"]
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    assert [p["agi"] for p in r.state_effect["points"]] == [59500, 60000]
    # Primaryonly500capremaining; spouse's19kunusedcannot shelterrest.
    assert r.state_effect["delta_total_tax"] > 0
    # An independently taxable negative delta cannot leave the existing eligible
    # pension input greater than the remaining total taxable retirement income.
    plan["income_treatment"]["ira_distribution"] = "taxable"
    r = report(result, law, {"ira_distribution": -1000})
    assert not r.state_effect["modeled"]
    assert "remaining taxable retirement income" in r.state_effect["limitations"][0]


def test_ny_reduction_election_and_recapture_move():
    result, law = case("NY", 239000)
    plan = result["state_planning_inputs"]
    plan["ny"]["itemized"] = dict(itemized_before_adjustment=20000, charitable_contributions=10000)
    before = planning._ny(239000, 0, 0, plan, law["state"], "mfj")
    result["new_york_it201"]["line_60_total_taxes"] = before["total_tax"]
    r = report(result, law, {"ira_distribution": 11000})
    assert r.state_effect["modeled"], r.state_effect
    # IT19639k/50k*.25*20k=3.9k reduction;16,100wins at239k.
    # At250k15k loses to16,050standard; taxable+11,050 at6%flat=663.
    assert [p["deduction"] for p in r.state_effect["points"]] == [16100, 16050]
    assert r.state_effect["delta_total_tax"] == 663


def test_nyc_city_tax_repriced_separately():
    result, law = case("NY", 80000)
    plan = result["state_planning_inputs"]
    plan["ny"]["local"].update(nyc_resident=True, nyc_resident_spouses=2,
                                charitable_gifts_trust_deducted=0)
    before = planning._ny(80000, 0, 0, plan, law["state"], "mfj")
    result["new_york_it201"]["line_60_total_taxes"] = before["total_tax"]
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    assert r.state_effect["points"][1]["local_tax"] > r.state_effect["points"][0]["local_tax"]
    assert r.state_effect["delta_total_tax"] > 55  # state55pluscity~38


def test_stacks_inverse_and_changed_state_evidence():
    result, law = case()
    first = report(result, law)
    stacked = report(result, law, base=dataclasses.asdict(first))
    direct = report(result, law, {"ira_distribution": 2000})
    assert stacked.state_effect["points"][1] == direct.state_effect["points"][1]
    inverse = report(result, law, {"ira_distribution": -1000}, dataclasses.asdict(first))
    assert inverse.state_effect["points"][1]["anchored_total_tax"] == 1360
    law["state"]["standard_deduction"]["mfj"] += 1
    with pytest.raises(scenario.ScenarioError, match="State evidence/law"):
        report(result, law, base=dataclasses.asdict(first))


def test_roth_partial_rung_combined_cost_and_basis():
    result, law = case()
    r = roth_ladder.build_ladder(result, PROFILE, law, 2025, max_amount=1500, step=1000,
                                name="state", today=TODAY, ira_basis=5000, ira_value=10000, ira_owner="primary")
    assert r.state_effect["modeled"], r.state_effect
    # Taxableconversions0,500,750;childcliffonce atfirststep.
    assert [p.taxable_conversion for p in r.rungs] == [0, 500, 750]
    assert [p.state_local_cost for p in r.rungs] == [0, 42.5, 53.12]
    assert r.rungs[1].combined_cost == 142.5
    assert r.rungs[2].combined_marginal_rate == .1212  # (50fed+10.62state)/500gross
    text = roth_ladder.render_markdown(r)
    assert "Combined marginal rate" in text
    assert "Federal total" in text


@pytest.mark.parametrize("field,value,reason", [
    ("residency", "part_year", "full-year"), ("scope_confirmed", None, "scope"),
    ("tax_year", 2024, "year"), ("filing_status", "SINGLE", "status"),
    ("state", "WA", "excise"), ("retirement_basis_matches_federal", None, "basis")])
def test_missing_or_unsupported_evidence_is_named_and_never_zero(field, value, reason):
    result, law = case()
    result["state_planning_inputs"][field] = value
    r = report(result, law)
    assert not r.state_effect["modeled"]
    assert r.state_effect["points"] == []
    assert reason in r.state_effect["limitations"][0]
    assert "combined_delta_tax" not in r.state_effect


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "1"])
def test_invalid_financial_evidence_cannot_price(value):
    result, law = case("CA")
    result["state_planning_inputs"]["ca"]["additions"] = value
    assert not report(result, law).state_effect["modeled"]


@pytest.mark.parametrize("code", ["CA", "NY", "NC"])
def test_unreviewed_form_year_is_not_enabled_by_new_rates(code):
    result, law = case(code)
    result["meta"]["tax_year"] = law["tax_year"] = result["state_planning_inputs"]["tax_year"] = 2026
    r = scenario.build_report(result, PROFILE, law, 2026, "state", {"ira_distribution": 1000}, None, TODAY)
    assert not r.state_effect["modeled"]
    assert "form year" in r.state_effect["limitations"][0]


def test_baseline_mismatch_and_unsupported_business_change():
    result, law = case()
    result["north_carolina_d400"]["line_17"] += 20
    assert "reproduce" in report(result, law).state_effect["limitations"][0]
    result, law = case()
    assert "business deltas" in report(result, law, {"self_employment_net": 1000}).state_effect["limitations"][0]


def test_ca_amt_boundary_and_ny_mixed_residency_are_unavailable():
    result, law = case("CA", 999000)
    result["california_540"]["ca_total_tax"] += 400  # credit completely phasedout
    r = report(result, law, {"ira_distribution": 2000})
    assert not r.state_effect["modeled"]
    assert "Schedule P" in r.state_effect["limitations"][0]
    result, law = case("NY")
    result["state_planning_inputs"]["ny"]["local"].update(nyc_resident=True, nyc_resident_spouses=1,
                                                          charitable_gifts_trust_deducted=0)
    assert not report(result, law).state_effect["modeled"]


def test_launcher_preserves_baseline_and_saves_state_evidence(tmp_path):
    result, law = case()
    for name, data in [("result.json", result), ("tax_law_2025.json", law), ("taxpayer_profile.json", PROFILE)]:
        write_secure(tmp_path / name, json.dumps(data))
    original = (tmp_path / "result.json").read_bytes()
    done = subprocess.run(["sh", "agents/lib/run.sh", "-m", "agents.lib.scenario", "--workspace", str(tmp_path),
                           "--result", "result.json", "--year", "2025", "--json", "roth_conversion=1000"],
                          cwd=REPO, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    saved = json.loads((tmp_path / "scenarios/scenario.json").read_text())
    assert saved["state_effect"]["delta_total_tax"] == 63.75
    assert (tmp_path / "result.json").read_bytes() == original
    assert (tmp_path / "scenarios/scenario.json").stat().st_mode & 0o777 == 0o600


def test_inputs_not_mutated_and_missing_plan_is_explicit():
    result, law = case()
    originals = copy.deepcopy((result, law))
    report(result, law)
    assert (result, law) == originals
    del result["state_planning_inputs"]
    r = report(result, law)
    assert not r.state_effect["modeled"]
    assert "record reviewed" in scenario.render_markdown(r)


def test_yonkers_surcharge_reruns_on_state_tax():
    result, law = case("NY", 80000)
    law["state"]["yonkers_tax"] = {"resident_surcharge_rate": .1675}
    plan = result["state_planning_inputs"]
    plan["ny"]["local"].update(yonkers_resident=True, yonkers_resident_spouses=2,
                                yonkers_worksheet_credits={}, star_reconciliation=0)
    before = planning._ny(80000, 0, 0, plan, law["state"], "mfj")
    result["new_york_it201"]["line_60_total_taxes"] = before["total_tax"]
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    # MFJ taxable63,950->64,950:midpoints63,975/64,975 produce3,186/3,241.
    # Yonkers rounds3,186*.1675=533.655->534,3,241*.1675=542.8675->543.
    assert [p["local_tax"] for p in r.state_effect["points"]] == [534, 543]
    assert r.state_effect["delta_total_tax"] == 64


def test_ny_household_credit_federal_agi_cliff_reprices():
    result, law = case("NY", 5000, status="SINGLE")
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    assert r.state_effect["delta_total_tax"] == 0  # both fullyoffset/notaxable
    # Force a synthetic zero standard and addition30k so state tax is positive,
    # while actualFAGI crosses5k->6k: household75->60, delta40tax+15creditloss.
    law["state"]["standard_deduction"]["single"] = 0
    plan = result["state_planning_inputs"]
    plan["ny"]["additions"]["state_local_bond_interest"] = 30000
    before = planning._ny(5000, 0, 0, plan, law["state"], "single")
    result["new_york_it201"]["line_60_total_taxes"] = before["total_tax"]
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    assert r.state_effect["delta_total_tax"] == 70  #1k*.055+15creditloss


def test_ny_income_liability_excludes_use_tax():
    result, law = case("NY", 80000)
    result["new_york_it201"]["line_60_total_taxes"] += 100
    result["new_york_it201"]["line_59_sales_or_use_tax"] = 100
    r = report(result, law)
    assert r.state_effect["modeled"], r.state_effect
    # Rowmidpoint63,975:1,202+.055*(63,975-27,900)=3,186.125->3,186.
    assert r.state_effect["recorded_liability"] == 3186


@pytest.mark.parametrize("mutate,reason", [
    (lambda r, l: r["state_planning_inputs"]["nc"].update(other_state_credit={}), "D-400TC"),
    (lambda r, l: r["state_planning_inputs"]["income_treatment"].pop("ira_distribution"), "income_treatment"),
    (lambda r, l: l["state"].update(code="NY"), "jurisdiction"),
    (lambda r, l: l.update(tax_year=2024), "year")])
def test_additional_scope_and_evidence_guards(mutate, reason):
    result, law = case()
    mutate(result, law)
    assert reason in report(result, law).state_effect["limitations"][0]


def test_profile_residency_contradiction_cannot_price():
    result, law = case()
    profile = copy.deepcopy(PROFILE)
    profile.setdefault("flags", {})["moved_states_this_year"] = True
    r = scenario.build_report(result, profile, law, 2025, "state", {"ira_distribution": 1000}, None, TODAY)
    assert "allocation/residency" in r.state_effect["limitations"][0]


@pytest.mark.parametrize("bad", [
    [dict(min=100, max=200, tax={"Single": 10})],
    [dict(min=100, max=200, tax={"single": True})],
    [dict(min=100, max=200, tax={"single": 10}), dict(min=150, max=250, tax={"single": 20})],
    [dict(min=100, max=100, tax={"single": 10})]])
def test_ca_table_validation_shared_with_cache_ingest(bad):
    from agents.lib.tax_law_fetcher import _check_table, TaxLawSetError
    with pytest.raises(TaxLawSetError):
        _check_table("ca_tax_rows", "state.tax_table.rows", bad)
    result, law = case("CA")
    law["state"]["tax_table"]["rows"] = bad
    assert not report(result, law).state_effect["modeled"]


def test_optional_ca_planning_law_has_no_defaults_and_is_settable():
    from agents.lib.tax_law_fetcher import create_state_template, _check_against
    state = create_state_template("CA", 2025)
    assert state["exemption_phaseout"]["reduction_per_step"] is None
    assert state["tax_table"]["max_taxable_income"] is None
    _, law = case("CA")
    _check_against("state.tax_table", state["tax_table"], law["state"]["tax_table"])
