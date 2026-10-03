"""Z4 integrations; amounts hand-computed from D-401 and G.S. 105-163.15."""

import copy
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agents.lib import capabilities, filing_package, projection, tax_calendar, tie_out, withholding
from agents.lib.secure_fs import write_secure
from agents.lib.tax_computations import compute_nc_d400
from tests.test_nc_state import cfg, _NO_SCHED_S
from tests.test_tie_out import _ledger, _record, _result

REPO = Path(__file__).resolve().parents[1]
DAY = dt.date(2025, 12, 31)


def _inputs():
    return {"schedule_s": copy.deepcopy(_NO_SCHED_S), "ctc_qualifying_children": 0,
            "itemized": None, "spouse_itemizes_nc": None, "claimed_as_dependent": False,
            "eligible_for_federal_standard_deduction": True,
            "use_tax": 100, "withholding": 1000, "federal_extension_circle": False}


def _return():
    # D-401: (52,750 AGI - 12,750 standard) * .0425 = 1,700;
    # line 19 = 1,700 + 100 use tax; 1,000 withheld leaves 800 due.
    return {"meta": {"tax_year": 2025, "filing_status": "SINGLE", "limitations": []},
            "form_1040": {"line_11_agi": 52750, "line_24_total_tax": 5000},
            "north_carolina_d400": compute_nc_d400(2025, "SINGLE", 52750, cfg(), **_inputs())}


def _law():
    # G.S. 105-163.15(d), (f); fetched values are fixture arguments only.
    return {"tax_year": 2025, "state": cfg(estimated_tax={"required_threshold": 1000,
                "current_year_rate": .90, "prior_year_rate": 1.00, "farmer_rate": 2 / 3})}


def _projection(**extra):
    return {"nc_farmer_fisherman": False, "ytd": {"state_withholding": 0,
            "federal_withholding": 0}, "estimated_payments_made": {"state": 0, "federal": 0},
            **extra}


def _true_up(inputs=None, prior=None, law=None):
    return withholding.build_nc_true_up(_return(), inputs or _projection(), prior,
                                        "SINGLE", DAY, 0, 2025, law or _law())


def test_nc_computed_output_reaches_all_registered_consumers():
    result = _return()
    nc = result["north_carolina_d400"]
    assert (nc["line_15_nc_tax"], nc["line_27_amount_due"]) == (1700, 800)
    assert projection._nc_section_computed(result)
    assert "state_withholding_nc" in tie_out.CATEGORY_TARGETS
    assert tie_out.CATEGORY_TARGETS["state_withholding_nc"] == (
        "north_carolina_d400", ["line_20a_withholding", "line_20b_spouse_withholding"])
    assert any("North Carolina Form D-400" in title for _key, title
               in filing_package._STATE_SECTIONS.items())
    checklist = " ".join(filing_package.attachment_checklist(result))
    assert all(text in checklist for text in ("D-400V", "Bailey", "DD-214", "PO Box R"))
    assert not projection._nc_section_computed({"north_carolina_d400": {"line_20a_withholding": 1000}})


def test_nc_current_target_excludes_use_tax_but_balance_includes_it():
    report = _true_up()
    # 90% * 1,700 = 1,530, vs 1,800 balance; Q4 holds each annual gap.
    assert report.required_annual_payment == report.shortfall == 1530
    assert report.zero_shortfall == 1800
    assert sum(v.amount for v in report.vouchers_penalty) == 1530
    assert report.vouchers_penalty[0].due_date == "2026-01-15"


def test_nc4_whole_dollar_entry_covers_the_shortfall():
    # NC-4 line 2: 1,530 / 7 = 218.571..., so request 219 whole dollars.
    report = withholding.build_nc_true_up(_return(), _projection(), None, "SINGLE", DAY, 7, 2025, _law())
    assert report.nc4_penalty.per_paycheck == 219
    assert report.nc4_zero.per_paycheck == 258


def test_nc_ledger_ties_both_spouses_and_detects_a_dropped_amount():
    ledger = _ledger(_record(key_boxes={"state_withholding_nc": 1000}))
    result = _result()
    result["north_carolina_d400"] = {"line_20a_withholding": 600, "line_20b_spouse_withholding": 400}
    assert not tie_out.evaluate(ledger, result, ["documents/W2_Acme.pdf"]).failed
    result["north_carolina_d400"]["line_20b_spouse_withholding"] = 0
    assert any(c.category == "state_withholding_nc" and not c.ok
               for c in tie_out.evaluate(ledger, result, ["documents/W2_Acme.pdf"]).sum_checks)


def test_nc_prior_tax_requires_filed_twelve_month_confirmation():
    prior = SimpleNamespace(result=_return())
    prior.result["north_carolina_d400"]["line_17"] = 1000
    assert _true_up(prior=prior).required_annual_payment == 1530
    assert _true_up(_projection(nc_prior_year_return_filed_12_months_confirmed=True),
                    prior).required_annual_payment == 1000
    prior.result["north_carolina_d400"]["line_17"] = 0
    assert _true_up(prior=prior).required_annual_payment == 1530
    assert _true_up(_projection(nc_prior_year_no_liability_confirmed=True), prior).shortfall == 0


def test_nc_no_liability_answer_cannot_contradict_archive():
    with pytest.raises(withholding.WithholdingError, match="contradicts"):
        _true_up(_projection(nc_prior_year_no_liability_confirmed=True),
                 SimpleNamespace(result=_return()))


@pytest.mark.parametrize("block", ["ytd", "estimated_payments_made"])
def test_nc_missing_payment_input_is_not_assumed_zero(block):
    inputs = _projection()
    del inputs[block]
    with pytest.raises(withholding.WithholdingError, match="Record"):
        _true_up(inputs)


def test_nc_inconsistent_balance_refuses_instead_of_understating_payment():
    result = _return()
    result["north_carolina_d400"]["line_19"] = 1600
    with pytest.raises(withholding.WithholdingError, match="line 19"):
        withholding.build_nc_true_up(result, _projection(), None, "SINGLE", DAY, 0, 2025, _law())


@pytest.mark.parametrize("wh,required", [(700, 1530), (700.01, 0)])
def test_nc_threshold_is_strict_and_does_not_subtract_estimates(wh, required):
    report = _true_up(_projection(ytd={"state_withholding": wh},
                                  estimated_payments_made={"state": 200}))
    # 1,700 - 700 = 1,000 does not meet the strictly-less-than floor.
    assert report.required_annual_payment == required


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf"), "0.9", None, 90])
def test_nc_shares_must_be_fetched_finite_decimal_values(bad):
    law = _law()
    law["state"]["estimated_tax"]["current_year_rate"] = bad
    with pytest.raises((withholding.WithholdingError, projection.ProjectionError)):
        _true_up(law=law)


@pytest.mark.parametrize("farmer", [None, True, "false"])
def test_nc_farmer_schedule_is_never_assumed_to_be_ordinary(farmer):
    with pytest.raises(withholding.WithholdingError):
        _true_up(_projection(nc_farmer_fisherman=farmer))


def test_nc_report_surfaces_state_cache_and_closed_year_target():
    report = withholding.build_report(_return(), _projection(), None, 2025, DAY,
                                      today=dt.date(2026, 5, 1), tax_law=_law())
    assert report.nc is not None
    assert "North Carolina true-up" in withholding.render_markdown(report)
    assert withholding._report_to_dict(report)["nc"]["zero_shortfall"] == 1800
    assert "no remaining payment target" not in (report.w4_lever_note or "").lower()
    absent = withholding.build_report(_return(), _projection(), None, 2025, DAY)
    assert absent.nc_cache_missing
    assert "No NC true-up computed" in withholding.render_markdown(absent)


def test_nc_calendar_has_four_equal_quarters_and_conditional_extension():
    events = tax_calendar.build_events({"address": {"state": "NC"}, "flags": {}}, 2025)
    assert len([e for e in events if e.category == "state_estimated_tax"]) == 4
    extension = " ".join(e.description for e in tax_calendar.events_extension(2025, state="NC"))
    assert all(text in extension for text in ("Form D-400", "D-410", "circle", "Neither extends payment"))


def test_nc_gate_requires_all_nc_answers_and_names_unsupported_deductions():
    template = json.loads((REPO / "agents/templates/taxpayer_profile_template.json").read_text())
    template.update(tax_year=2025, filing_status="SINGLE", address={"state": "NC"})
    template["flags"] = {name: False for name in template["flags"] if not name.startswith("_")}
    template["flags"]["nc_has_recovery_deductions"] = None
    assert any(f.rule_id == "unanswered_gate_questions" for f in capabilities.evaluate(template).blocking)
    template["flags"]["nc_has_recovery_deductions"] = True
    assert any(f.rule_id == "nc_recovery_deductions" for f in capabilities.evaluate(template).blocking)


def test_nc_skill_launcher_computes_and_secures_a_synthetic_workspace(tmp_path):
    profile = {"tax_year": 2025, "filing_status": "SINGLE", "address": {"state": "NC"},
               "north_carolina_inputs": _inputs()}
    for name, data in (("taxpayer_profile.json", profile), ("result.json", _return()),
                       ("tax_law_2025.json", _law())):
        write_secure(tmp_path / name, json.dumps(data))
    skill = (REPO / ".agents/skills/tax-compute/references/state-return-nc.md").read_text()
    script = re.search(r"sh agents/lib/run.sh -c '\n(.*?)\n'", skill, re.S).group(1)
    run = subprocess.run([sys.executable, "-c", script], cwd=REPO,
                         env={**os.environ, "TAX_WORKSPACE": str(tmp_path)}, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads((tmp_path / "result.json").read_text())["north_carolina_d400"]["line_27_amount_due"] == 800


def test_nc_forecast_never_omits_state_deductions_in_generic_bracket_math():
    assert projection.project_state_tax(SimpleNamespace(), None, _law(), "SINGLE", "NC") is None
