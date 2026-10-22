"""TY2025 Form540NR worksheets; figures below are synthetic, not cached law."""

from copy import deepcopy

import pytest

from agents.lib.tax_computations.state.ca_nonresident import (
    CA_NR_COMPONENT_LINES,
    _SCOPE_FACTS,
    _ratio,
    _schedule_ca,
    ca_540nr_plan,
    compute_ca_540nr_amt,
    compute_ca_540nr,
)
from tests.test_ca_540_builder import law
from agents.lib.tax_computations.state.registry import selected_ca_return


def profile():
    return {
        "tax_year": 2025, "filing_status": "Single", "address": {"state": "FL"},
        "flags": {"moved_states_this_year": True, "moved_abroad_this_year": False,
                  "statutory_resident_of_another_state": False, "has_rental_income": False},
        "residency": {"domicile_state": "FL"}, "prior_year": {},
        "state_filings": {"CA": {
            "form": "540NR", "tax_year": 2025, "residency": "part_year",
            "resident_period": ["2025-01-01", "2025-05-31"],
            "joint_residency_identical": None,
            "resident_states": ["CA", "FL"], "source_states": ["CA"],
            "other_state_obligations": [{"state": "FL", "return_required": False}],
            **dict.fromkeys(_SCOPE_FACTS, True),
        }},
    }


def federal(**changes):
    return {"line_1z_total_wages": 100000, "line_2b_taxable_interest": 0,
            "line_3b_ordinary_dividends": 0, "line_4b_taxable_ira": 0,
            "line_5b_taxable_pensions": 0, "line_6b_taxable_ss": 0,
            "line_7_capital_gain": 0, "line_8_schedule1_other": 0,
            "line_10_adjustments": 0, "line_11_agi": 100000, **changes}


def row(line="A1z", **changes):
    return {"id": "wages", "line": line, "method": "actual", "federal": 100000,
            "subtractions": 0, "additions": 0, "resident": 40000,
            "nonresident_ca": 10000, "nonresident_elsewhere": 50000, **changes}


def test_declared_plan_selects_ca_despite_florida_mailing_address():
    assert ca_540nr_plan(profile())["fully_covered"] is True
    p = profile()
    p["state_filings"] = {}
    p["address"]["state"] = "CA"
    assert ca_540nr_plan(p) is None


@pytest.mark.parametrize("period", [
    ["2025-01-01", "2025-12-31"],
    ["2024-12-31", "2025-05-31"], ["2025-06-01", "2025-05-31"],
    ["2025-1-1", "2025-05-31"], None,
])
def test_unsupported_periods_refuse(period):
    p = profile(); p["state_filings"]["CA"]["resident_period"] = period
    with pytest.raises(ValueError, match="period|resident"):
        ca_540nr_plan(p)


@pytest.mark.parametrize("fact", _SCOPE_FACTS)
def test_every_scope_answer_is_required(fact):
    p = profile(); del p["state_filings"]["CA"][fact]
    with pytest.raises(ValueError, match="scope review"):
        ca_540nr_plan(p)


def test_joint_spouses_need_identical_period_confirmation():
    p = profile(); p["filing_status"] = "MFJ"
    with pytest.raises(ValueError, match="identical"):
        ca_540nr_plan(p)
    p["state_filings"]["CA"]["joint_residency_identical"] = True
    assert ca_540nr_plan(p)["fully_covered"] is True


@pytest.mark.parametrize("status", ["MFS", "a_single_filer", None, True])
def test_bad_or_unsupported_status_refuses(status):
    p = profile(); p["filing_status"] = status
    with pytest.raises(ValueError, match="status|spouse"):
        ca_540nr_plan(p)


def test_another_taxable_state_is_never_declared_covered():
    p = profile(); plan = p["state_filings"]["CA"]
    plan["source_states"].append("NY")
    plan["other_state_obligations"].append({"state": "NY", "return_required": False})
    assert ca_540nr_plan(p)["fully_covered"] is False
    p["state_filings"]["NY"] = {"form": "IT203"}
    with pytest.raises(ValueError, match="simultaneous"):
        ca_540nr_plan(p)


def test_inventory_must_include_all_states_and_the_declared_domicile():
    p = profile(); plan = p["state_filings"]["CA"]
    plan["source_states"].append("TX")
    with pytest.raises(ValueError, match="entire"):
        ca_540nr_plan(p)
    p = profile(); p["residency"]["domicile_state"] = "TX"
    with pytest.raises(ValueError, match="domicile"):
        ca_540nr_plan(p)


def test_inbound_move_is_supported_and_rental_history_is_not_lost():
    p = profile()
    p["state_filings"]["CA"]["resident_period"] = ["2025-06-01", "2025-12-31"]
    assert ca_540nr_plan(p)["fully_covered"]
    p["prior_year"]["rental_carryforward"] = {"properties": []}
    with pytest.raises(ValueError, match="rental"):
        ca_540nr_plan(p)


def test_one_continuous_midyear_ca_period_is_supported():
    p = profile()
    p["state_filings"]["CA"]["resident_period"] = ["2025-03-01", "2025-06-30"]
    assert ca_540nr_plan(p)["fully_covered"] is True


def test_outside_income_is_allocated_without_becoming_a_law_subtraction():
    # ScheduleCA columnD remains100000; E is40000 resident+10000 CA-source.
    plan = {"income_rows": [row()], "adjustment_rows": []}
    built = _schedule_ca(plan, federal())
    assert built["agi"]["all_source"] == 100000
    assert built["agi"]["ca_source"] == 50000
    assert built["agi"]["subtractions"] == 0


def test_wrong_source_split_and_dropped_federal_income_refuse():
    plan = {"income_rows": [row(resident=40001)], "adjustment_rows": []}
    with pytest.raises(ValueError, match="disjoint"):
        _schedule_ca(plan, federal())
    plan["income_rows"] = [row()]
    with pytest.raises(ValueError, match="A2b"):
        _schedule_ca(plan, federal(line_2b_taxable_interest=100, line_11_agi=100100))


def test_cents_reconcile_before_form_rounding():
    plan = {"income_rows": [row(federal=100000.01, resident=40000.01)], "adjustment_rows": []}
    with pytest.raises(ValueError, match="columnA"):
        _schedule_ca(plan, federal())


def test_negative_wage_source_is_not_a_capital_loss_allocation():
    plan = {"income_rows": [row(resident=160000, nonresident_ca=0,
                                nonresident_elsewhere=-60000)], "adjustment_rows": []}
    with pytest.raises(ValueError, match="gross-income"):
        _schedule_ca(plan, federal())


def test_social_security_never_becomes_ca_source_taxable_income():
    ss = row("A6b", id="benefits", federal=2000, subtractions=2000,
             resident=0, nonresident_ca=0, nonresident_elsewhere=0)
    plan = {"income_rows": [row(), ss], "adjustment_rows": []}
    f = federal(line_6b_taxable_ss=2000, line_11_agi=102000)
    assert _schedule_ca(plan, f)["agi"]["all_source"] == 100000
    plan = deepcopy(plan); plan["income_rows"][1].update(subtractions=0, resident=2000)
    with pytest.raises(ValueError, match="Social Security"):
        _schedule_ca(plan, f)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True])
def test_allocation_values_reject_nonfinite_and_boolean(bad):
    plan = {"income_rows": [row(resident=bad)], "adjustment_rows": []}
    with pytest.raises(ValueError, match="finite"):
        _schedule_ca(plan, federal())


def test_ratio_rounding_caps_and_undefined_positive_amount():
    # ScheduleCA PartIV ratio:50000/120000=.416666..., four places=.4167.
    assert _ratio(50000, 120000, "deduction") == .4167
    assert _ratio(150000, 100000, "deduction") == 1
    assert _ratio(0, 0, "deduction") == 0
    with pytest.raises(ValueError, match="alternate worksheet"):
        _ratio(5000, 0, "deduction")


def amt_case(**changes):
    # Synthetic ScheduleP parameters isolate the worksheet, not annual law.
    return {"tax_year": 2025, "filing_status": "Single",
            "state_law": {"code": "CA", "type": "graduated_income", "amt": {
                "exemption": dict.fromkeys(("single", "mfj", "hoh"), 10000),
                "phaseout_start": dict.fromkeys(("single", "mfj", "hoh"), 1000000),
                "tmt_rate": .1, "phaseout_rate": .25}},
            "all_source_agi": 100000, "ca_agi": 50000, "deduction": 10000,
            "deduction_method": "standard", "itemized_before_limitation": 0,
            "itemized_after_limitation": 0, "regular_tax": 2000,
            "inputs": {"world_adjustments": 0, "ca_adjustments": 0,
                       "iso_world": 0, "iso_ca": 0, "itemized_amt_disallowances": 0},
            **changes}


def test_nr_amt_has_its_own_source_base_and_effective_rate():
    # P21=100000;22=10000;24=9000;42=.0900;43=50000*.09=4500.
    paper = compute_ca_540nr_amt(**amt_case())
    assert paper["line_21"] == 100000
    assert paper["line_43"] == 4500
    assert paper["ca_amt"] == 2500
    assert paper["schedule_p_carryforward"] == 0


def test_iso_deferral_attribution_reprices_both_columns():
    case = amt_case()
    case["inputs"].update(iso_world=20000, iso_ca=5000)
    # P21=120000;24=11000;42=.0917;40=55000;43=5043.5→5044.
    # Full AMT3044 minus without-ISO AMT2500 leaves544 deferral credit.
    paper = compute_ca_540nr_amt(**case)
    assert paper["line_42"] == .0917
    assert paper["line_43"] == 5044
    assert paper["ca_amt"] == 3044
    assert paper["iso_credit_generated"] == 544


def test_nr_amt_itemized_allocation_and_limitation_reversal():
    case = amt_case(deduction=18000, deduction_method="itemized",
                    itemized_before_limitation=20000, itemized_after_limitation=18000)
    case["inputs"].update(world_adjustments=5000, itemized_amt_disallowances=5000)
    # P21=(100000−18000)+5000−2000=85000;35=15000;37=100000.
    # Source:38=.5;39=7500;40=42500;42=7500/85000=.0882;
    # 43=3748.5→3749;45=1749.
    paper = compute_ca_540nr_amt(**case)
    assert paper["line_18"] == 2000
    assert paper["line_21"] == 85000
    assert paper["line_38"] == .5
    assert paper["line_40"] == 42500
    assert paper["line_43"] == 3749
    assert paper["ca_amt"] == 1749


def test_amt_signed_taxable_base_does_not_invent_standard_deduction_income():
    paper = compute_ca_540nr_amt(**amt_case(all_source_agi=4000, ca_agi=2000))
    assert paper["line_15"] == -6000
    assert paper["line_21"] == 4000
    assert paper["ca_tmt"] == 0


def test_nr_amt_phaseout_uses_worldwide_amti():
    paper = compute_ca_540nr_amt(**amt_case(all_source_agi=1100000))
    assert paper["line_22"] == 0
    assert paper["line_43"] == 5000


@pytest.mark.parametrize("key", ["world_adjustments", "ca_adjustments", "iso_world", "iso_ca", "itemized_amt_disallowances"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True])
def test_nr_amt_rejects_nonfinite_inputs(key, bad):
    case = amt_case(); case["inputs"][key] = bad
    with pytest.raises(ValueError, match="finite"):
        compute_ca_540nr_amt(**case)


def test_nr_amt_refuses_stale_year_and_unmodeled_source_basis():
    with pytest.raises(ValueError, match="TY2025"):
        compute_ca_540nr_amt(**amt_case(tax_year=2026))
    case = amt_case(); case["inputs"].update(iso_world=100, iso_ca=101)
    with pytest.raises(ValueError, match="source exceeds"):
        compute_ca_540nr_amt(**case)


def return_case(**changes):
    p = profile()
    p["state_filings"]["CA"].update(income_rows=[row()], adjustment_rows=[])
    return {"tax_year": 2025, "filing_status": "Single", "state_law": law(), "profile": p,
            "federal_components": federal(),
            "exemption_counts": {"personal": 1, "blind": 0, "senior": 0, "dependent": 0},
            "deductions": {"method": "standard", "itemized_before_limitation": 0,
                "itemized_after_limitation": 0, "dependent_filer": False,
                "dependent_standard_deduction": None},
            "amt_inputs": amt_case()["inputs"],
            "credit_inputs": {"opening_amt_credit": 0, "other_credits": [],
                "ptet_current": [], "ptet_carryovers": [],
                "ptet_carryforward_years": None, "ptet_first_tax_year": None},
            "components": {**dict.fromkeys((f"line_{n}" for n in CA_NR_COMPONENT_LINES), 0),
                           "line_81": 5000, "claim_of_right_credit": 0},
            "source_witness": {}, **changes}


def test_nr_regular_tax_and_refund_from_actual_source_income():
    # Worldwide100k−10k=90k, tax4500, effective rate.05.
    # CA50k−5k=45k, tax2250, exemption10*.5=5, tax2245, refund2755.
    out = compute_ca_540nr(**return_case())
    assert out["line_17"] == 100000
    assert out["line_32"] == 50000
    assert out["line_35"] == 45000
    assert out["line_37"] == 2250
    assert out["line_39"] == 5
    assert out["ca_total_tax"] == 2245
    assert out["ca_refund"] == 2755
    assert out["schedule_p"]["line_32"] == 50000  # FTB3510 keys cannot overwrite P.
    assert out["allocation_review_current"] is False
    assert "NOT COMPUTED" in out["line_123_note"]


def test_nr_deduction_and_credit_ratios_are_separate_rounded_operations():
    case = return_case()
    case["state_law"]["standard_deduction"]["single"] = 100
    case["federal_components"] = federal(line_1z_total_wages=1001, line_11_agi=1001)
    case["profile"]["state_filings"]["CA"]["income_rows"] = [row(
        federal=1001, resident=500, nonresident_ca=0, nonresident_elsewhere=501)]
    # D1001,E500: deduction ratio.4995 gives50; TI450/901 gives.4994.
    out = compute_ca_540nr(**case)
    assert out["schedule_ca"]["part_iv"]["line_3"] == .4995
    assert out["line_35"] == 450
    assert out["line_38"] == .4994


def test_nr_effective_tax_rate_rounds_before_multiplication():
    case = return_case()
    case["state_law"]["brackets"][0]["rate"] = .054321
    case["federal_components"] = federal(line_1z_total_wages=110000, line_11_agi=110000)
    case["profile"]["state_filings"]["CA"]["income_rows"] = [row(
        federal=110000, resident=55000, nonresident_ca=0, nonresident_elsewhere=55000)]
    # WorldTI100000: table/rate tax5432.10→5432; rate.0543; CA TI50000→2715.
    # Allocating5432 directly by.5 would instead produce2716.
    out = compute_ca_540nr(**case)
    assert out["line_31"] == 5432
    assert out["line_36"] == .0543
    assert out["line_37"] == 2715


def test_nr_decimal_product_preserves_a_half_dollar_tie():
    case = return_case()
    case["state_law"]["tax_table"]["rows"][0]["tax"] = dict.fromkeys(("single", "mfj", "mfs", "hoh", "qss"), 29)
    case["federal_components"] = federal(line_1z_total_wages=10100, line_11_agi=10100)
    case["profile"]["state_filings"]["CA"]["income_rows"] = [row(
        federal=10100, resident=5050, nonresident_ca=0, nonresident_elsewhere=5050)]
    case["exemption_counts"]["personal"] = 0
    out = compute_ca_540nr(**case)
    # WorldTI100/table29=.2900; sourceTI50*.29=14.50, HALF_UP to15.
    # A binary float product gives14.499999999999998 and wrongly prints14.
    assert out["line_36"] == .29 and out["line_35"] == 50
    assert out["line_37"] == 15


def test_nr_owner_credit_order_uses_source_tmt_and_prorated_exemptions():
    case = return_case()
    case["state_law"]["amt"]["exemption"]["single"] = 90000
    case["credit_inputs"].update(opening_amt_credit=2500,
        ptet_current=[{"credit_id": "owner", "source_id": "entity", "amount": 500}],
        ptet_carryforward_years=5, ptet_first_tax_year=2021)
    # WorldTMT700, source350. AMT-credit limit2250−5−350=1895.
    # Remaining350 is offset by PTET; carry605 AMT and150 PTET.
    out = compute_ca_540nr(**case)
    assert out["line_58"] == 1895
    assert out["line_59"] == 350
    assert out["ca_total_tax"] == 0
    assert out["schedule_p"]["carryforward_to_next_year"] == 605
    assert out["ftb_3804_cr"]["carryovers_to_next_year"][0]["amount"] == 150


def test_positive_nr_amt_compares_tax_before_exemption_credits():
    case = return_case()
    case["state_law"]["amt"].update(
        exemption=dict.fromkeys(("single", "mfj", "mfs", "hoh", "qss"), 10000), tmt_rate=.1)
    out = compute_ca_540nr(**case)
    # WorldAMTI100k less exemption10k at10%=9000; sourceAMTI50k
    # at.09=4500. NR regular before exemption2250 => AMT2250, not2255.
    assert out["line_37"] == out["schedule_p"]["line_44"] == 2250
    assert out["line_40"] == 2245
    assert out["line_71"] == 2250
    assert out["ca_total_tax"] == 4495


def test_nr_prorated_special_credit_is_counted_once():
    case = return_case()
    case["components"]["line_51"] = 100
    case["credit_inputs"]["other_credits"] = [{"code": "223", "amount": 100}]
    out = compute_ca_540nr(**case)
    assert out["line_55"] == 50
    assert out["line_58"] == 100
    assert out["line_62"] == 150
    assert out["line_63"] == 2095
    case["credit_inputs"]["other_credits"] = [{"code": "170", "amount": 100}]
    with pytest.raises(ValueError, match="once-only"):
        compute_ca_540nr(**case)


def test_nr_penalties_settle_after_forward_election_and_can_reverse_refund():
    case = return_case()
    case["components"].update(line_91=100, line_102=2000, line_120=100,
                               line_122=600, line_123=100)
    # Payments5000−ISR100−tax2245=2655. Forward2000 leaves655;
    # contributions100+penalties700 leave amount due145, preserving forward.
    out = compute_ca_540nr(**case)
    assert out["line_102"] == 2000
    assert out["line_103"] == 655
    assert out["line_124"] == 145
    assert out["ca_refund"] == 0


def test_nr_review_is_bound_to_inputs_and_excludes_its_own_confirmation():
    case = return_case()
    first = compute_ca_540nr(**case)
    review = {"input_digest": first["allocation_input_digest"],
        **dict.fromkeys(("sources_reviewed", "conformity_reviewed", "deductions_reviewed", "amt_reviewed", "credits_reviewed"), True)}
    case["profile"]["state_filings"]["CA"]["worksheet_review"] = review
    second = compute_ca_540nr(**case)
    assert second["allocation_input_digest"] == first["allocation_input_digest"]
    assert second["allocation_review_current"] is True
    case["source_witness"]["payment_dates_changed"] = True
    assert compute_ca_540nr(**case)["allocation_review_current"] is False


def test_nr_qualified_credit_cents_keep_source_and_filed_form_proof():
    case = return_case(); case["credit_inputs"]["opening_amt_credit"] = 1000.5
    out = compute_ca_540nr(**case)
    assert out["credit_rounding_workpaper"]["opening_amt_credit"] == {
        "source_amount": 1000.5, "form_amount": 1001, "rounding_difference": .5}
    assert out["schedule_p"]["ftb_3510"]["line_32"] == 1001
    assert out["schedule_p"]["ftb_3510"]["line_38"] == 1001
    assert out["schedule_p"]["carryforward_to_next_year"] == 0


def test_nr_ptet_rounding_preserves_eligible_vintages_and_source_total():
    case = return_case()
    case["credit_inputs"].update(ptet_carryforward_years=5, ptet_first_tax_year=2010,
        ptet_current=[{"credit_id": "current", "source_id": "entity", "amount": 200.5}],
        ptet_carryovers=[{"credit_id": "expired", "origin_year": 2019, "amount": .49},
            {"credit_id": "old", "origin_year": 2020, "amount": .49},
            {"credit_id": "new", "origin_year": 2021, "amount": .49}])
    # Current200.50→201; eligible carry.98→1; expired.49 cannot steal that
    # eligible dollar. The tied fractional eligible dollar goes to oldest lot.
    out = compute_ca_540nr(**case)
    ptet = out["ftb_3804_cr"]
    assert ptet["reported_credit_total"] == 200.5
    assert ptet["reported_form_total"] == 201
    assert ptet["line_2"] == 1
    assert ptet["line_4"] == 202
    proof = out["credit_rounding_workpaper"]["ptet_carryovers"]
    assert next(r for r in proof if r["credit_id"] == "old")["form_amount"] == 1
    assert next(r for r in proof if r["credit_id"] == "expired")["expired_before_this_year"]
    case["credit_inputs"]["ptet_carryovers"].reverse()
    reordered = compute_ca_540nr(**case)
    assert reordered["ftb_3804_cr"] == ptet


def test_nr_surcharge_uses_source_taxable_income():
    case = return_case()
    case["federal_components"] = federal(line_1z_total_wages=2000000, line_11_agi=2000000)
    case["profile"]["state_filings"]["CA"]["income_rows"] = [row(
        federal=2000000, resident=500000, nonresident_ca=0, nonresident_elsewhere=1500000)]
    assert compute_ca_540nr(**case)["line_72"] == 0
    # Source1.1m/world2m=.55; annual deduction10k*.55=5500.
    # SourceTI1,094,500 less synthetic surcharge threshold1m, at1%=945.
    case["profile"]["state_filings"]["CA"]["income_rows"][0].update(resident=1100000, nonresident_elsewhere=900000)
    assert compute_ca_540nr(**case)["line_72"] == 945


def test_fractional_credits_are_formed_before_limiting_to_small_net_tax():
    case = return_case()
    case["state_law"]["tax_table"]["rows"][0]["tax"] = dict.fromkeys(("single", "mfj", "mfs", "hoh", "qss"), 5)
    case["federal_components"] = federal(line_1z_total_wages=10100, line_11_agi=10100)
    case["profile"]["state_filings"]["CA"]["income_rows"] = [row(
        federal=10100, resident=5050, nonresident_ca=0, nonresident_elsewhere=5050)]
    case["exemption_counts"]["personal"] = 0
    case["credit_inputs"].update(opening_amt_credit=1.5, ptet_carryforward_years=5, ptet_first_tax_year=2010,
        ptet_current=[{"credit_id": "current", "source_id": "entity", "amount": 1.5}])
    out = compute_ca_540nr(**case)
    # WorldTI100 has synthetic table tax5; sourceTI50*.05=2.50->3. Opening1.50->2,
    # currentPTET1.50->2, only1 PTET used after2 AMT-credit, carry1.
    assert out["line_37"] == out["line_62"] == 3
    assert out["ca_total_tax"] == 0
    assert out["schedule_p"]["ftb_3510"]["line_38"] == 2
    assert out["ftb_3804_cr"]["carryovers_to_next_year"][0]["amount"] == 1


def test_nr_raw_cents_and_printed_agi_keep_rounding_reconciliation():
    case = return_case()
    case["federal_components"] = federal(line_1z_total_wages=100000.5, line_11_agi=100000.5)
    case["profile"]["state_filings"]["CA"]["income_rows"] = [row(
        federal=100000.5, subtractions=.49, resident=50000, nonresident_ca=0,
        nonresident_elsewhere=50000.01)]
    out = compute_ca_540nr(**case)
    assert out["schedule_ca"]["agi"]["all_source"] == 100000.01
    assert out["line_13"] == out["line_17"] == 100001
    assert out["schedule_ca"]["form_rounding_difference"] == 1


def test_ca_variant_selection_refuses_competing_computations_and_metadata():
    assert selected_ca_return({"california_540nr": {"computed_tax_year": 2025}}) == "540NR"
    result = {"california_540": {"ca_standard_deduction": 10000},
              "california_540nr": {"computed_tax_year": 2025}}
    with pytest.raises(ValueError, match="competing"):
        selected_ca_return(result)
    with pytest.raises(ValueError, match="competing"):
        selected_ca_return({"meta": {"ca_return_type": "540"},
                            "california_540nr": {"computed_tax_year": 2025}})


@pytest.mark.parametrize("year", [True, 2025.0, "2025", -1])
def test_ca_variant_computation_witness_cannot_be_boolean_or_float(year):
    with pytest.raises(ValueError, match="integer tax year"):
        selected_ca_return({"california_540nr": {"computed_tax_year": year}})
