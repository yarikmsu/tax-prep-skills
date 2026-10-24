"""Part-year selection through downstream consumers; all sources synthetic."""

import datetime as dt
import json

import pytest

from agents.lib import capabilities as C, filing_package as F, pdf_render as PDF
from agents.lib import projection as P, rollover as R, tax_calendar as CAL, withholding as W
from agents.lib import return_builder as B
from agents.lib.tax_computations.state.planning import price_state_points
from agents.lib.tax_computations.state.registry import ca_section_key
from agents.lib.secure_fs import write_secure
from tests.test_ca_540nr_builder import nr_workspace, bind_review
from tests.test_ca_part_year import profile, row, federal, _schedule_ca
from tests.test_projection import _tax_law_2026, _declared_standard
from tests.test_withholding import _proj_inputs


def checked(ws):
    assert B._main(["--workspace", str(ws)]) == 0
    bind_review(ws)
    assert B._main(["--workspace", str(ws)]) == 0
    return json.loads((ws / "result.json").read_text())


def test_outbound_and_inbound_select_ca_despite_mailing_address(tmp_path):
    ws = nr_workspace(tmp_path)
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    assert C._filing_state(p) == "CA"
    assert "Form540NR" in C._state_note(p)
    assert "CDTFA" in C._state_note(p)
    p["address"]["state"] = "CA"
    p["residency"]["domicile_state"] = "CA"
    p["state_filings"]["CA"]["resident_period"] = ["2025-06-01", "2025-12-31"]
    write_secure(ws / "taxpayer_profile.json", json.dumps(p))
    assert checked(ws)[ca_section_key("540NR")]["ca_total_tax"] == 2245


def test_nr_package_has_actual_variant_workpapers_and_cdtfa_note(tmp_path):
    result = checked(nr_workspace(tmp_path))
    forms = F.assembly_order(result)
    titles = [r["title"] for r in forms]
    assert "California Form 540NR" in titles
    assert "Schedule CA (540NR) allocation worksheet" in titles
    assert "Schedule P (540NR) AMT worksheet" in titles
    assert "California Form 540" not in titles
    text = "\n".join(F.attachment_checklist(result))
    assert "CDTFA" in text and "line91 is the ISR" in text
    del result[ca_section_key("540NR")]
    with pytest.raises(F.FilingPackageError, match="540NR"):
        F.assembly_order(result)
    with pytest.raises(PDF.PdfRenderError, match="540NR"):
        PDF.render(result, tmp_path / "missing.pdf")


def test_nr_cannot_be_packaged_with_a_stale_allocation_review(tmp_path):
    ws = nr_workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    with pytest.raises(F.FilingPackageError, match="review"):
        F.assembly_order(json.loads((ws / "result.json").read_text()))


def test_nr_pdf_ratios_keep_four_decimals_without_changing_credit_units(tmp_path):
    result = checked(nr_workspace(tmp_path))
    key = ca_section_key("540NR")
    nr = result[key]
    nr["line_36"] = .0543
    nr["schedule_p"]["line_42"] = .0917
    nr["schedule_p"]["ftb_3510"]["line_38"] = 123
    rows = PDF._rows(nr, section=key)
    assert ("Line 36", "0.0543") in rows
    assert ("Line 38", "0.5000") in rows
    assert ("        Line 3", "0.5000") in rows  # ScheduleCA PartIV.
    assert ("    Line 42", "0.0917") in rows
    assert ("        Line 38", "$123.00") in rows  # FTB3510 is monetary.
    assert PDF._title(key) == "California 540NR"
    assert PDF._rows({"line_38": 123}, section=ca_section_key()) == [("Line 38", "$123.00")]
    ordered = PDF._rows({"line_81": 10, "line_7": 20, "line_12": 30}, section=key)
    assert [r[0] for r in ordered] == ["Line 7", "Line 12", "Line 81"]


def test_nr_blocks_both_full_year_planners_and_withholding_forecast(tmp_path):
    result = checked(nr_workspace(tmp_path))
    # The result variant alone must block a resident planner, even if a caller
    # fails to pass the move profile or stale resident planning inputs remain.
    state = price_state_points(result, {}, 2025, "single", {}, {}, [])
    assert state["modeled"] is False and "540NR" in state["limitations"][0]
    report = W.build_report(result, _proj_inputs(0), None, 2025, dt.date(2025, 6, 30))
    assert report.ca is None and report.ca_540nr_manual
    assert "No CA target or refund" in W.render_markdown(report)
    law = _tax_law_2026(); law["tax_year"] = 2025
    p = profile()
    forecast = P.build_report(_declared_standard({"ytd": {"wages": 50000}}),
        p, law, None, 2025, dt.date(2025, 6, 30))
    assert forecast.state is None and not forecast.state_no_income_tax
    assert forecast.ca_safe_harbor is None
    assert "540NR" in forecast.state_unsupported_note


def test_calendar_uses_declared_nr_filing_state_and_extension_form():
    p = profile(); p["flags"]["filed_extension"] = True
    events = CAL.build_events(p, 2025)
    assert any(e.category == "state_estimated_tax" for e in events)
    assert any("Form 540NR" in e.description for e in events)
    with pytest.raises(CAL.CalendarError, match="TY2025"):
        CAL.build_events(p, 2026)


def test_nr_rollover_preserves_qualified_credit_vintages_and_forward_payment(tmp_path):
    result = checked(nr_workspace(tmp_path))
    nr = result[ca_section_key("540NR")]
    nr["schedule_p"]["carryforward_to_next_year"] = 321
    nr["ftb_3804_cr"]["carryovers_to_next_year"] = [{"credit_id": "owner", "origin_year": 2025, "amount": 123}]
    nr.update(line_101=500, line_102=250)
    prior = R.PriorReturn(2025, result, {"filing_status": "Single", "flags": {"ca_applies_overpayment_forward": True}})
    current = R.CurrentState({}, {"tax_year": 2026, "filing_status": "Single", "prior_year": {}, "estimated_payments": {}}, {})
    changes = R.compute_diff(prior, current, {"carryforwards"}, {})["carryforwards"]
    amounts = {c.json_path: c.prior_value for c in changes}
    assert amounts["prior_year.ca_amt_credit_carryforward"] == 321
    assert amounts["prior_year.ca_ptet_credit_carryovers"] == nr["ftb_3804_cr"]["carryovers_to_next_year"]
    assert amounts["prior_year.ca_estimated_payments_applied"] == 250
    assert amounts["estimated_payments.ca_verified"] is False
    del nr["line_101"]
    with pytest.raises(R.RolloverError, match="line101"):
        R.compute_diff(prior, current, {"carryforwards"}, {})


@pytest.mark.parametrize("line", ["A6b", "B1", "A4b"])
def test_protected_receipts_cannot_be_ca_source_while_nonresident(line):
    p = profile(); plan = p["state_filings"]["CA"]
    plan["income_rows"] = [row(line, federal=1000, resident=0, nonresident_ca=1000, nonresident_elsewhere=0)]
    plan["adjustment_rows"] = []
    with pytest.raises(ValueError, match="excluded|protected"):
        _schedule_ca(plan, federal())
