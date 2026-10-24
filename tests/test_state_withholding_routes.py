"""AA60: every supported withheld payment reaches its own return line."""

import copy
import json
from pathlib import Path

import pytest

from agents.lib import filing_package, parse_records, parse_utils, tie_out as T
from agents.lib.secure_fs import secure_path, write_secure
from tests.skill_text import skill_text


def record(boxes, *, doc_id="withholding", form="1099-R", breakdown=None):
    raw = {"doc_id": doc_id, "file": f"documents/{doc_id}.pdf", "form_type": form,
           "payer": "Synthetic payer", "person": "primary", "key_boxes": boxes}
    if breakdown is not None:
        raw["other_state_withholding_by_state"] = breakdown
    return raw


def report(records, result):
    return T.evaluate(T.load_ledger({"records": records}), result,
                      [r["file"] for r in records], option_exercise_answer=False)


def it203(**payments):
    # A computed zero-tax IT-203; its State/City/MCTMT/use-tax sum is zero.
    return {"computed_tax_year": 2025, "line_51_nyc_tax": 0, "line_52_nyc_child_care": 0,
            "line_52f_mctmt": 0, "line_53_yonkers_nonresident_tax": 0,
            "line_54_yonkers_part_year_tax": 0, "line_55_city_and_mctmt": 0,
            "line_50_state_tax": 0, "line_56_use_tax": 0, "line_57_voluntary": 0,
            "line_58_total_taxes": 0, **payments}


def test_ca_line71_components_and_line73_are_separate_and_all_claimed():
    # TY2025 Form 540: line 71 = W-2 600 + 1099-R 300 = 900;
    # line 73 = recipient's Form 592-B 100. No amount goes on both lines.
    records = [record({"state_withholding_ca": 600}, doc_id="w2", form="W-2"),
               record({"ca_1099_withholding": 300}, doc_id="retirement"),
               record({"ca_592b_593_withholding": 100}, doc_id="ftb", form="592-B")]
    result = {"california_540": {"ca_withholding_box17": 600,
              "ca_withholding_1099": 300, "ca_withholding_592b_593": 100}}
    assert not report(records, result).failed
    for field in result["california_540"]:
        bad = copy.deepcopy(result)
        bad["california_540"][field] = 0
        assert report(records, bad).failed, field
    result["california_540"].update(ca_withholding_1099=100, ca_withholding_592b_593=300)
    assert report(records, result).failed  # same total, wrong form lines


@pytest.mark.parametrize("form", ["1099-R", "1099-DA", "1099-B", "1099-DIV", "1099-INT",
                                  "1099-G", "1099-NEC", "1099-MISC", "1099-K", "W-2G"])
def test_ca_nonwage_withholding_ties_without_a_w2(form):
    records = [record({"ca_1099_withholding": 300}, form=form)]
    result = {"california_540": {"ca_withholding_1099": 300}}
    if form == "1099-G":
        records[0]["key_boxes"].update(unemployment_compensation=0,government_family_leave=0)
        records[0]["government_benefits"] = {
            "tax_year":2025,"form_revision":"03/2024","box_1":0,"box_10":None,
            "unemployment_repaid":0,"unemployment_contribution_reduction":0,
            "reviewed":True,"family_programs":[]}
        result.update(meta={"tax_year":2025},form_1040={"line_8_schedule1_other":0})
        result["schedule_1_government_benefits"] = T.derive_government_workpaper(
            T.load_ledger({"records":records}),result,{"tax_year":2025})
    if form == "1099-MISC":
        from tests.test_misc_income import source
        records[0]["misc_income"] = source(box_3=0,box_3_business=0,box_3_other=0,
            box_4=0,box_5=0,box_6=0,box_7=False)
        result["meta"] = {"tax_year":2025}
    assert not report(records, result).failed
    bad = copy.deepcopy(result)
    bad["california_540"] = {"ca_withholding_box17":300}
    assert report(records, bad).failed


@pytest.mark.parametrize("variant", ["IT201", "IT203"])
def test_ny_state_city_and_yonkers_include_nonwage_sources(variant):
    # TY2025 IT-201 lines 72-74 / IT-203 lines 62-64: W-2 + R + W-2G.
    records = [record({"state_withholding_ny": 400, "city_withholding_nyc": 100,
                       "city_withholding_yonkers": 50}, doc_id="w2", form="W-2"),
               record({"state_withholding_ny": 300, "city_withholding_nyc": 80,
                       "city_withholding_yonkers": 20}, doc_id="pension"),
               record({"state_withholding_ny": 200, "city_withholding_nyc": 50,
                       "city_withholding_yonkers": 30}, doc_id="w2g", form="W-2G")]
    if variant == "IT201":
        values = {"line_72_state_withholding": 900, "line_73_city_withholding": 230,
                  "line_74_yonkers_withholding": 100}
        result = {"new_york_it201": {"refundable_credits_and_payments": values}}
    else:
        values = {"line_62_state_withholding": 900, "line_63_city_withholding": 230,
                  "line_64_yonkers_withholding": 100}
        result = {"meta": {"ny_return_type": "IT203"}, "new_york_it203": it203(**values)}
        values = result["new_york_it203"]
    assert not report(records, result).failed
    key = "line_73_city_withholding" if variant == "IT201" else "line_63_city_withholding"
    values[key] = 100  # only the W-2 reached the City total
    assert report(records, result).failed


@pytest.mark.parametrize("state,result", [
    ("CA", {"california_540": {"ca_standard_deduction": 5000}}),
    ("NY", {"new_york_it201": {"line_34_deduction": 8000}}),
    ("NY", {"meta": {"ny_return_type": "IT203"}, "new_york_it203": it203()}),
    ("NC", {"north_carolina_d400": {"line_11_deduction": 12000}}),
])
def test_a_computed_states_payment_cannot_hide_in_the_advisory(state, result):
    records = [record({"other_state_withholding": 125}, breakdown={state: 125})]
    checked = report(records, result)
    assert checked.failed
    assert any("return is selected or computed" in message for message in checked.missing_result_targets)


@pytest.mark.parametrize("result,breakdown", [
    ({"form_1040": {}}, {"CA": 100, "NY": 25}),
    ({"california_540": {"ca_withholding_box17": 0}}, {"CA": 125}),
    ({"colorado_state": {"taxable_income": 100000}}, {"CO": 125}),
])
def test_external_and_generic_estimate_states_remain_advisory(result, breakdown):
    checked = report([record({"other_state_withholding": 125}, breakdown=breakdown)], result)
    assert not checked.failed
    assert any(a.startswith("other_state_withholding") for a in checked.advisories)


@pytest.mark.parametrize("breakdown", [None, {}, [], {"ca": 125}, {"ZZ": 125},
    {"CA": True}, {"CA": -125}, {"CA": float("nan")}, {"CA": float("inf")}, {"CA": 124},
    # Sums to 125, so only the per-state sign check can refuse it.
    {"CO": 250, "NJ": -125}])
def test_other_state_breakdowns_require_valid_states_finite_amounts_and_a_complete_sum(breakdown):
    with pytest.raises(T.TieOutError):
        T.load_ledger({"records": [record({"other_state_withholding": 125}, breakdown=breakdown)]})


def test_other_state_breakdown_survives_record_to_ledger_assembly(tmp_path):
    documents = tmp_path / "documents"
    documents.mkdir()
    secure_path(documents)
    write_secure(documents / "withholding.pdf", "synthetic document placeholder")
    raw = record({"other_state_withholding": 150}, breakdown={"CO": 100, "NJ": 50})
    parse_records.write_record(tmp_path, raw)
    path = parse_records.write_ledger(tmp_path)
    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert ledger["records"][0]["other_state_withholding_by_state"] == {"CO": 100, "NJ": 50}
    assert dict(T.load_ledger(ledger)[0].other_state_withholding_by_state) == {"CO": 100, "NJ": 50}


def test_a_typed_record_cannot_bypass_the_missing_breakdown_check():
    rec = T.LedgerRecord("synthetic", "documents/form.pdf", "1099-R", "Synthetic payer", "primary",
                         (("other_state_withholding", 100),), None)
    assert T.evaluate([rec], {}, [rec.file]).failed


@pytest.mark.parametrize("state", ["CA", "NY", "NC", "CO", "TX"])
def test_a_selected_state_cannot_be_made_external_by_dropping_its_result(state):
    recs = T.load_ledger({"records": [record({"other_state_withholding": 125},
                                            breakdown={state: 125})]})
    checked = T.evaluate(recs, {"form_1040": {}}, [r.file for r in recs],
                         profile={"address": {"state": state}})
    assert checked.failed == (state in {"CA", "NY", "NC"})


def test_a_declared_ny_plan_selects_ny_independently_of_the_mailing_address():
    from tests.test_ny_it203 import profile
    p = profile()
    p["address"]["state"] = "CA"
    recs = T.load_ledger({"records": [record({"other_state_withholding": 125},
                                            breakdown={"NY": 125})]})
    checked = T.evaluate(recs, {"form_1040": {}}, [r.file for r in recs], profile=p)
    assert any("NY return is selected or computed" in message for message in checked.missing_result_targets)
    # The same mailing address does not select a second CA return.
    recs = T.load_ledger({"records": [record({"other_state_withholding": 125},
                                            breakdown={"CA": 125})]})
    assert not T.evaluate(recs, {"form_1040": {}}, [r.file for r in recs], profile=p).failed


@pytest.mark.parametrize("text,kind", [
    ("Form 592-B Resident and Nonresident Withholding Tax Statement", "ca-592b"),
    ("Form 593 Real Estate Withholding Statement", "ca-593"),
    ("Form 593 Real Estate Withholding Statement. Do not use Form 592-B.", "ca-593"),
    ("Form 592-B Resident and Nonresident Withholding Tax Statement. See Form 593.", "ca-592b"),
    # The W-2 pattern is checked before 592-B, so only the title rule keeps this.
    ("Form 592-B Resident and Nonresident Withholding Tax Statement. Wages belong on Form W-2.", "ca-592b"),
])
def test_ca_statements_are_classified_for_the_parse_route(text, kind):
    assert parse_utils.classify_document(text) == kind


def test_filing_package_counts_nonwage_and_real_estate_withholding_as_payments():
    result = {"form_1040": {"line_37_amount_owed": 0}, "california_540": {
        "ca_total_tax": 900, "ca_withholding_box17": 100,
        "ca_withholding_1099": 700, "ca_withholding_592b_593": 100}}
    # All 900 of tax is paid, so the checklist must not tell this filer to pay.
    lines = filing_package.attachment_checklist(result)
    assert not any(line.startswith("CA balance due:") for line in lines)
    assert any("592-B/593" in line for line in lines)


def test_parse_and_compute_contracts_name_each_separate_ca_component():
    repo = Path(__file__).resolve().parents[1]
    parse = skill_text("tax-parse")
    compute = (repo / ".agents/skills/tax-compute/references/state-return.md").read_text(encoding="utf-8")
    for category in ("state_withholding_ca", "ca_1099_withholding", "ca_592b_593_withholding"):
        assert f"`{category}`" in parse
        for field in T.CATEGORY_TARGETS[category][1]:
            assert f"`{field}`" in compute
