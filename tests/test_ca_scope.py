"""California's uncomputed situations stay named; acknowledged credits survive."""

import copy
import json
from pathlib import Path

import pytest

from agents.lib import answers, capabilities as C
from agents.lib import tie_out
from agents.lib.rollover import CurrentState, PriorReturn, compute_diff
from agents.lib.secure_fs import secure_path, write_secure
from scripts import who_it_fits as sizing
from tests.test_capabilities import _profile


CA_BLOCKS = tuple(r for r in C.RULES if r.state == "CA" and r.level == C.UNSUPPORTED)


@pytest.mark.parametrize("rule", CA_BLOCKS, ids=lambda r: r.rule_id)
def test_each_ca_situation_blocks_and_can_be_acknowledged(rule):
    p = _profile({rule.flag: True})
    assert rule.rule_id in {f.rule_id for f in C.evaluate(p).blocking}
    p["acknowledged_limitations"] = [rule.rule_id]
    report = C.evaluate(p)
    assert not report.blocking
    assert rule.rule_id in {f.rule_id for f in report.acknowledged}


@pytest.mark.parametrize("state", ["FL", "NY", "NC", "WA"])
def test_ca_flags_never_apply_to_another_state(state):
    p = _profile({r.flag: True for r in CA_BLOCKS}, state=state)
    report = C.evaluate(p)
    assert not any(f.rule_id.startswith("ca_") for f in report.blocking + report.manual)


def test_missing_ca_answer_is_not_no_and_cannot_be_acknowledged():
    p = _profile()
    del p["flags"]["ca_has_other_taxes"]
    report = C.evaluate(p)
    assert any("ca_has_other_taxes" in f.note for f in report.blocking)
    with pytest.raises(C.CapabilityError):
        C.evaluate(p, {C._MIGRATION_RULE_ID})


def test_nonzero_prior_credit_overrides_a_false_screening_answer():
    p = _profile({"ca_has_amt_credit": False})
    p["prior_year"] = {"ca_amt_credit_carryforward": 1000}
    assert "ca_amt_credit" in {f.rule_id for f in C.evaluate(p).blocking}


@pytest.mark.parametrize("bad", [True, -1, "1000", float("nan"), float("inf")])
def test_malformed_carried_credit_refuses(bad):
    p = _profile()
    p["prior_year"] = {"ca_amt_credit_carryforward": bad}
    with pytest.raises(C.CapabilityError, match="ca_amt_credit_carryforward"):
        C.evaluate(p)


def test_rule_scope_comes_from_metadata_not_an_id_prefix(monkeypatch):
    rule = C.Rule("ordinary_name", "test_ca_situation", C.UNSUPPORTED,
                  "Synthetic CA case", "Form 540", "Manual worksheet", state="CA")
    monkeypatch.setattr(C, "RULES", C.RULES + (rule,))
    assert "ordinary_name" in {f.rule_id for f in C.evaluate(_profile({rule.flag: True})).blocking}
    assert "ordinary_name" not in {f.rule_id for f in C.evaluate(_profile({rule.flag: True}, state="FL")).blocking}
    with pytest.raises(C.CapabilityError, match="rule state"):
        C.Rule("bad", "bad", C.UNSUPPORTED, "Bad", "Bad", "Bad", state="ZZ")


def test_ca_worksheet_credits_have_their_own_manual_disclosures():
    report = C.evaluate(_profile({"has_dependent_care": True, "may_qualify_for_eic": True,
                                 "ca_paid_rent": True, "ca_claims_special_credits": True}))
    notes = {f.rule_id: f.form + " " + f.note for f in report.manual}
    assert "FTB 3506" in notes["dependent_care"]
    assert "Young Child" in notes["earned_income_credit"]
    assert "Foster Youth" in notes["earned_income_credit"]
    assert "ca_renters_credit" in notes and "ca_special_credits" in notes
    assert "5805" in report.state_note and "NOT COMPUTED" in report.state_note


def _credit_case():
    profile = _profile({"ca_has_amt_credit": True}, acknowledged=["ca_amt_credit"])
    profile["prior_year"] = {"ca_amt_credit_carryforward": 1000}
    # FTB 3510: available 1,000 - allowable 200 = 800. A current AMT
    # liability would leave no regular-tax excess to use a prior-year credit.
    result = {"form_1040": {}, "california_540": {"schedule_p": {
        "ftb3510_credit_available": 1000, "ftb3510_credit_allowable": 200,
        "ftb3510_worksheet_confirmed": True, "iso_credit_generated": 0,
        "ca_amt": 0, "iso_bargain_element": 0,
        "carryforward_to_next_year": 800}}}
    return profile, result


def test_acknowledged_credit_has_a_complete_arithmetic_chain():
    profile, result = _credit_case()
    assert not tie_out.evaluate([], result, [], profile=profile).failed


@pytest.mark.parametrize("missing", ["california_540", "schedule_p", "ftb3510_credit_allowable",
                                     "ftb3510_worksheet_confirmed", "carryforward_to_next_year"])
def test_acknowledgement_never_waives_a_missing_credit_workpaper(missing):
    profile, result = _credit_case()
    if missing == "california_540":
        del result[missing]
    elif missing == "schedule_p":
        del result["california_540"][missing]
    else:
        del result["california_540"]["schedule_p"][missing]
    assert tie_out.evaluate([], result, [], profile=profile).failed


@pytest.mark.parametrize("field,value", [("carryforward_to_next_year", 1100),
    ("ftb3510_credit_available", 0), ("ftb3510_credit_allowable", 1001),
    ("ftb3510_credit_allowable", None), ("iso_credit_generated", float("nan"))])
def test_wrong_credit_arithmetic_refuses(field, value):
    profile, result = _credit_case()
    result["california_540"]["schedule_p"][field] = value
    assert tie_out.evaluate([], result, [], profile=profile).failed


@pytest.mark.parametrize("update,message", [
    # Internally consistent (900 - 200 = 700) but not the 1,000 carried in.
    (dict(ftb3510_credit_available=900, carryforward_to_next_year=700), "opening credit"),
    # The sum guard also fires (1,000 - 1,200 is negative); the ceiling is
    # still the error a preparer needs to read.
    (dict(ftb3510_credit_allowable=1200, carryforward_to_next_year=0), "exceeds the available"),
])
def test_each_credit_guard_names_its_own_failure(update, message):
    profile, result = _credit_case()
    result["california_540"]["schedule_p"].update(update)
    report = tie_out.evaluate([], result, [], profile=profile)
    assert report.failed
    assert any(message in m for m in report.missing_result_targets)


def test_a_typed_refund_still_needs_its_confirmed_worksheet():
    result = {"form_1040": {"taxable_state_refund": 100},
              "california_540": {"schedule_ca_adjustments": {
                  "taxable_state_refund_subtraction": 100, "total_subtractions": 100}}}
    profile = _profile({"has_1099g_state_refund": True})
    assert tie_out.evaluate([], result, [], profile=profile).failed
    result["form_1040"]["taxable_state_refund_worksheet_confirmed"] = True
    assert not tie_out.evaluate([], result, [], profile=profile).failed


def test_worked_zero_allowable_preserves_the_entire_opening_credit_without_iso():
    profile, result = _credit_case()
    p = result["california_540"]["schedule_p"]
    p.update(ftb3510_credit_allowable=0, iso_credit_generated=0, ca_amt=0,
             iso_bargain_element=0, carryforward_to_next_year=1000)
    assert not tie_out.evaluate([], result, [], profile=profile).failed


def test_current_amt_can_generate_a_credit_but_cannot_use_the_prior_credit():
    profile, result = _credit_case()
    p = result["california_540"]["schedule_p"]
    # FTB 3510 lines 33-38: TMT exceeds regular tax, so no old credit is
    # allowable; 1,000 opening + 100 new ISO AMT = 1,100 for next year.
    p.update(ftb3510_credit_allowable=0, iso_credit_generated=100, ca_amt=100,
             iso_bargain_element=20000, carryforward_to_next_year=1100)
    assert not tie_out.evaluate([], result, [], profile=profile).failed
    p.update(ftb3510_credit_allowable=200, carryforward_to_next_year=900)
    assert tie_out.evaluate([], result, [], profile=profile).failed


def test_a_prior_ca_credit_does_not_require_a_ca_return_from_a_florida_filer():
    profile, _ = _credit_case()
    profile["address"]["state"] = "FL"
    assert not tie_out.evaluate([], {"florida_state": {}}, [], profile=profile).failed


def test_the_unused_credit_reaches_the_next_year_profile_without_an_iso_exercise():
    profile, result = _credit_case()
    result["california_540"]["schedule_p"].update(
        iso_credit_generated=0, ca_amt=0, iso_bargain_element=0,
        carryforward_to_next_year=800)
    # FTB 3510: 1,000 available - 200 allowable = 800 left for next year.
    assert not tie_out.evaluate([], result, [], profile=profile).failed
    prior = PriorReturn(year=2025, result=result, profile=profile)
    current = CurrentState(result={}, profile={"tax_year": 2026}, tax_law={})
    changes = compute_diff(prior, current, tax_law_new=current.tax_law,
                           categories={"carryforwards"})
    (credit,) = [c for c in changes["carryforwards"]
                 if c.json_path == "prior_year.ca_amt_credit_carryforward"]
    assert credit.prior_value == 800
    assert credit.action_suggested == "apply"


@pytest.mark.parametrize("from_flag", [False, True])
def test_a_headless_run_requires_the_worked_credit_answer(tmp_path, from_flag):
    profile = _profile({"ca_has_amt_credit": from_flag}, acknowledged=["ca_amt_credit"])
    if not from_flag:
        profile["prior_year"] = {"ca_amt_credit_carryforward": 1000}
    write_secure(tmp_path / "taxpayer_profile.json", json.dumps(profile))
    assert "ca_amt_credit_worksheet" in {a.id for a in answers.applicable(tmp_path)}
    write_secure(tmp_path / "answers.md",
                 "- **[ca_amt_credit_worksheet]** FTB 3510 worked: available 1000, allowable 0.\n")
    missing, _ = answers.missing(tmp_path)
    assert "ca_amt_credit_worksheet" not in {a.id for a in missing}


@pytest.mark.parametrize("state", ["CA", "FL"])
@pytest.mark.parametrize("federal", [
    {"line_6b_taxable_ss": 2300},
    {"taxable_state_refund": 100, "taxable_state_refund_worksheet_confirmed": True},
])
def test_dropping_the_whole_ca_section_cannot_waive_a_required_subtraction(state, federal):
    report = tie_out.evaluate([], {"form_1040": federal}, [], profile=_profile(state=state))
    assert report.failed == (state == "CA")


def test_taxable_social_security_and_refund_must_be_subtracted_regardless_of_flags():
    result = {"form_1040": {"line_6b_taxable_ss": 2300, "taxable_state_refund": 100,
              "taxable_state_refund_worksheet_confirmed": True}, "california_540": {}}
    assert tie_out.evaluate([], result, [], profile=_profile()).failed
    result["california_540"]["schedule_ca_adjustments"] = {
        "taxable_social_security_subtraction": 2300,
        "taxable_state_refund_subtraction": 100, "total_subtractions": 2400}
    assert not tie_out.evaluate([], result, [], profile=_profile()).failed
    bad = copy.deepcopy(result)
    bad["california_540"]["schedule_ca_adjustments"]["total_subtractions"] = 2300
    assert tie_out.evaluate([], bad, [], profile=_profile()).failed


def test_gross_refund_cannot_replace_a_worked_taxable_amount():
    result = {"form_1040": {"taxable_state_refund": None}, "california_540": {}}
    profile = _profile({"has_1099g_state_refund": True})
    assert tie_out.evaluate([], result, [], profile=profile).failed
    result["form_1040"].update(taxable_state_refund=0, taxable_state_refund_worksheet_confirmed=True)
    assert not tie_out.evaluate([], result, [], profile=profile).failed


@pytest.mark.parametrize("field", ["taxable_social_security_subtraction",
                                  "taxable_state_refund_subtraction"])
def test_a_worked_federal_zero_cannot_become_a_gross_ca_subtraction(field):
    # Schedule CA subtracts only what federal AGI includes, even when gross
    # benefits/refunds are positive and their federally taxable portion is zero.
    result = {"form_1040": {"line_6b_taxable_ss": 0, "taxable_state_refund": 0,
                           "taxable_state_refund_worksheet_confirmed": True},
              "california_540": {"schedule_ca_adjustments": {
                  field: 1000, "total_subtractions": 1000}}}
    assert tie_out.evaluate([], result, [], profile=_profile()).failed
    result["california_540"]["schedule_ca_adjustments"].update({field: 0, "total_subtractions": 0})
    assert not tie_out.evaluate([], result, [], profile=_profile()).failed


def test_cli_reads_the_credit_from_the_profile_even_if_the_ca_result_is_missing(tmp_path, capsys):
    profile, result = _credit_case()
    documents = tmp_path / "documents"
    documents.mkdir()
    secure_path(documents)
    write_secure(tmp_path / "parse_ledger.json", json.dumps({"records": []}))
    write_secure(tmp_path / "taxpayer_profile.json", json.dumps(profile))
    write_secure(tmp_path / "result.json", json.dumps({"form_1040": {}}))
    assert tie_out._main(["--workspace", str(tmp_path)]) == 2
    assert "CA AMT credit" in capsys.readouterr().out
    write_secure(tmp_path / "result.json", json.dumps(result))
    assert tie_out._main(["--workspace", str(tmp_path)]) == 0


@pytest.mark.parametrize("federal", [False, True])
def test_ca_family_credit_advisory_names_each_distinct_line_and_its_worksheet(federal):
    result = {"california_540": {}}
    if federal:
        result["form_1040"] = {}
    report = tie_out.evaluate([], result, [], eic_screening=True)
    note = " ".join(report.advisories)
    assert "CalEITC" in note and "Young Child" in note and "Foster Youth" in note
    assert "FTB 3514 eligibility and credit worksheets" in note
    assert "California's family credits" in note
    assert "take ." not in note
    assert ("Pub 596" in note) == federal


def test_ca_public_statistics_use_total_and_high_income_brackets(tmp_path):
    # Total 1,000 returns, 100 taxable-SS, 20 refunds, 50 unemployment, 80 exempt interest.
    # Brackets 8-10 total 200 returns: 40/10/20/60, or 20/5/10/30 percent.
    path = tmp_path / "23in55cmcsv.csv"
    path.write_text("STATE,AGI_STUB,N1,N02500,N00700,N02300,N00400\n"
                    "CA,0,1000,100,20,50,80\nCA,7,500,500,500,500,500\n"
                    "CA,8,100,20,5,10,30\nCA,9,60,12,3,6,18\nCA,10,40,8,2,4,12\n")
    result = sizing.ca_scope_shares(tmp_path)
    assert result == {"taxable_ss_all": 10, "taxable_refund_all": 2,
        "unemployment_all": 5, "muni_upper_bound_all": 8,
        "taxable_ss_200k": 20, "taxable_refund_200k": 5,
        "unemployment_200k": 10, "muni_upper_bound_200k": 30}
    path.write_text(path.read_text().replace("CA,10,40,8,2,4,12\n", ""))
    with pytest.raises(ValueError, match="missing brackets"):
        sizing.ca_scope_shares(tmp_path)


def test_ca_scope_prose_matches_the_measured_shares_and_does_not_claim_the_bound_is_exact():
    repo = Path(__file__).resolve().parents[1]
    figures = json.loads(sizing.FIGURES.read_text())
    text = " ".join((repo / "docs/WHO-IT-FITS.md").read_text().split())
    assert all(p in text for p in sizing.ca_scope_phrases(figures))
    assert "upper bound, not a measured blocking share" in text
    assert figures["ca_scope"]["taxable_ss_200k"] > figures["income"]["California"]["rental_200k"]
