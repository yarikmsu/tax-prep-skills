"""Live-read TY2025 IT-203/IT-360.1 worksheets; all expected tax hand computed."""

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from agents.lib import capabilities, filing_package, tax_calendar, tie_out, withholding
from agents.lib.tax_computations import compute_ny_it203, compute_nyc_part_year, ny_it203_plan
from agents.lib.tax_computations.state.registry import selected_ny_return
from tests.ny_fixtures import ny_it203_cache
from tests.test_capabilities import _profile

ROOT = Path(__file__).resolve().parents[1]


def profile(city=False, residence="part_year"):
    p = _profile(
        state="FL",
        flags={
            "moved_states_this_year": residence == "part_year",
            "has_nonresident_source_income": True,
            "nyc_resident": city,
        },
    )
    p["filing_status"] = "Single"
    p["residency"] = {"domicile_state": "FL"}
    p["state_filings"] = {
        "NY": dict(
            form="IT203",
            tax_year=2025,
            residency=residence,
            resident_period=["2025-01-01", "2025-06-30"] if residence == "part_year" else None,
            nyc_resident_period=["2025-01-01", "2025-06-30"] if city else None,
            resident_states=["NY", "FL"] if residence == "part_year" else ["FL"],
            source_states=["NY"],
            other_state_obligations=[dict(state="FL", return_required=False)],
            obligation_inventory_complete=True,
            allocation_reviewed=True,
            convenience_rule_reviewed=True,
            special_accruals_reviewed=True,
            yonkers_tax_required=False,
            special_condition_a6=False,
        )
    }
    return p


def row(amount=100000, resident=40000, ny=0, line=1, ident="wage"):
    return dict(
        id=ident,
        line=line,
        federal=amount,
        resident=resident,
        nonresident_ny=ny,
        nonresident_elsewhere=amount - resident - ny,
        method="actual",
    )


def inputs(city=False):
    return dict(
        plan=profile(city)["state_filings"]["NY"],
        income_rows=[row()],
        adjustment_rows=[],
        modifications={
            str(n): dict(federal=0, ny_source=0) for n in (20, 21, 22, 24, 25, 26, 27, 28, 29)
        },
        modification_worksheets_verified=True,
        allocation_complete=True,
        dependents=0,
        claimed_as_dependent=False,
        itemized=None,
        credits={
            k: 0
            for k in (
                "child_care",
                "earned_income",
                "nonrefundable",
                "other_ny_taxes",
                "nyc_child_care",
                "nyc_school_fixed",
                "nyc_school_rate",
                "nyc_elimination",
                "other_refundable",
            )
        },
        credit_worksheets_verified=True,
        payments={
            k: 0
            for k in (
                "state_withholding",
                "city_withholding",
                "yonkers_withholding",
                "estimates_and_extension",
            )
        },
        payments_verified=True,
        use_tax=0,
        voluntary_contributions=0,
        penalty=0,
        interest=0,
        applied_to_next_year=0,
        mctmt_zone1=0,
        mctmt_zone2=0,
        mctmt_allocations_verified=True,
        city_inputs=city_args() if city else None,
        special_it230_income_percentage=False,
    )


def city_args():
    return dict(
        resident_nyagi=50000,
        resident_full_months=6,
        resident_itemized_deduction=None,
        charitable_trust_addback=0,
        household_and_accumulation_credit=0,
        other_city_taxes=0,
        ubt_credit=0,
        worksheets_verified=True,
    )


def compute(agi=100000, city=False, law=None, status="Single", **kw):
    return compute_ny_it203(
        2025, status, agi, law or dict(tax_year=2025, state=ny_it203_cache()), **(inputs(city) | kw)
    )


def test_annual_deduction_then_source_percentage():
    # IT-203-I Schedule C: 4271 + (92000-80650)*.06 = 4952; .4000 => 1981.
    r = compute()
    assert r["line_33_deduction"] == 8000
    assert r["line_37_taxable_income"] == 92000
    assert r["line_38_tax"] == 4952
    assert r["line_45_income_percentage"] == 0.4
    assert r["line_50_state_tax"] == 1981
    assert r["line_70_amount_owed"] == 1981


def test_elsewhere_loss_allows_percentage_above_one():
    # NY gains 25k plus elsewhere -10k => 15k. Table on 7k =>281; HH45.
    # (281-45)*1.6667 =393.3412 =>393, not236.
    r = compute(15000, income_rows=[row(25000, 25000), row(-10000, 0, line=9, ident="loss")])
    assert r["line_45_income_percentage"] == 1.6667
    assert r["line_39_household_credit"] == 45
    assert r["line_46_allocated_tax"] == 393


@pytest.mark.parametrize("agi,source", [(-5000, 10000), (10000, -5000), (0, 10000), (10000, 0)])
def test_nonpositive_columns_skip_allocated_tax(agi, source):
    r = compute(agi, income_rows=[row(agi, source)])
    assert r["line_46_allocated_tax"] == 0
    assert r["line_38_tax_method"] == "nonpositive_agi_skip"


def test_credit_order_and_line49_is_other_new_york_tax():
    c = inputs()["credits"] | dict(
        child_care=1000, earned_income=200, nonrefundable=100, other_ny_taxes=50
    )
    r = compute(credits=c)
    # (4952-1000-200)*.4=1500.8=>1501; -100 nonref +50 other NY tax=1451.
    assert r["line_44_base_tax"] == 3752
    assert r["line_48_after_nonrefundable"] == 1401
    assert r["line_50_state_tax"] == 1451


def test_city_uses_resident_income_and_rate_schedule():
    r = compute(city=True)
    city = r["nyc_it3601"]
    # 50k-8k*6/12=46k; 858+(46000-25000)*.03819=1659.99 =>1660.
    assert city["line_44_deduction"] == 4000
    assert city["line_48_tax"] == 1660
    assert city["method"] == "rate_schedule_part_year"
    assert r["line_58_total_taxes"] == 3641


def test_city_exemption_rounds_one_before_multiplying():
    a = city_args() | dict(resident_full_months=7)
    r = compute_nyc_part_year(
        2025, "Single", dependents=3, state_cfg=ny_it203_cache(), claimed_as_dependent=False, **a
    )
    # IT-360.1 chart: 1000*7/12 =>583 per dependent; 3*583=1749.
    assert r["line_42_exemptions"] == 1749


def test_city_credits_cannot_offset_mctmt():
    c = inputs(True)["credits"] | dict(nyc_child_care=10000)
    r = compute(city=True, credits=c, mctmt_zone1=70000)
    assert r["line_52a_net_nyc_tax"] == 0
    assert r["line_52d_zone1_tax"] == 420  # 70k*.006, entire base once over threshold.
    assert r["line_55_city_and_mctmt"] == 420


def test_payments_refund_penalty_and_next_year_application():
    p = inputs()["payments"] | dict(
        state_withholding=3000, yonkers_withholding=100, estimates_and_extension=500
    )
    r = compute(payments=p, penalty=20, interest=10, applied_to_next_year=100)
    assert r["line_66_payments"] == 3600
    assert r["line_67_overpayment"] == 1599  # 3600-1981-20; other interest never nets line67.
    assert r["line_68_refund"] == 1499
    assert r["line_70_amount_owed"] == 10
    with pytest.raises(ValueError, match="exceeds"):
        compute(applied_to_next_year=1)


def test_each_annual_amount_is_fetched_and_pension_cap_is_annual():
    law = dict(tax_year=2025, state=ny_it203_cache())
    law["state"]["standard_deduction"]["single"] = 10000
    assert compute(law=law)["line_33_deduction"] == 10000
    mods = inputs()["modifications"]
    mods["28"] = dict(federal=20001, ny_source=10000)
    with pytest.raises(ValueError, match="Annual pension"):
        compute(modifications=mods)


@pytest.mark.parametrize(
    "key",
    [
        "allocation_complete",
        "modification_worksheets_verified",
        "credit_worksheets_verified",
        "payments_verified",
        "mctmt_allocations_verified",
    ],
)
def test_unverified_worksheets_refuse(key):
    with pytest.raises(ValueError, match="confirmation"):
        compute(**{key: False})


@pytest.mark.parametrize("bad", [None, True, -1, float("nan"), float("inf"), "0"])
def test_invalid_money_refuses(bad):
    with pytest.raises(ValueError):
        compute(use_tax=bad)


@pytest.mark.parametrize(
    "change",
    [dict(method="calendar_days"), dict(resident=40001), dict(line=12), dict(federal=float("nan"))],
)
def test_bad_allocations_refuse(change):
    with pytest.raises(ValueError):
        compute(income_rows=[row() | change])


def test_missing_income_duplicate_rows_and_nonresident_period_refuse():
    with pytest.raises(ValueError, match="AGI"):
        compute(income_rows=[])
    with pytest.raises(ValueError, match="unique"):
        compute(income_rows=[row(), row()])
    with pytest.raises(ValueError, match="resident period"):
        compute(plan=profile(residence="nonresident")["state_filings"]["NY"])


def test_nonresident_actual_workday_allocation():
    r = compute(
        plan=profile(residence="nonresident")["state_filings"]["NY"],
        income_rows=[row(100000, 0, 40000) | dict(method="ny_workdays")],
    )
    assert r["line_46_allocated_tax"] == 1981


def test_cache_year_status_and_unsupported_special_ratio_refuse():
    with pytest.raises(ValueError, match="cache"):
        compute(law=dict(tax_year=2024, state=ny_it203_cache()))
    with pytest.raises(ValueError):
        compute(status="MFS")
    with pytest.raises(ValueError):
        compute(status="unknown")
    with pytest.raises(ValueError, match="IT230"):
        compute(special_it230_income_percentage=True)


def test_city_month_count_requires_actual_dates():
    with pytest.raises(ValueError, match="month count"):
        compute(city=True, city_inputs=city_args() | dict(resident_full_months=12))
    plan = profile(True)["state_filings"]["NY"]
    plan["nyc_resident_period"] = ["2025-01-17", "2025-06-30"]
    assert (
        compute(city=True, plan=plan, city_inputs=city_args() | dict(resident_full_months=5))[
            "nyc_it3601"
        ]["line_39_full_months"]
        == 5
    )


def test_complete_plan_clears_only_covered_move_and_source_blocks():
    p = profile()
    assert ny_it203_plan(p)["fully_covered"]
    assert not capabilities.evaluate(p).blocking
    p["state_filings"]["NY"]["resident_states"] = ["NY", "FL", "NJ"]
    p["state_filings"]["NY"]["other_state_obligations"].append(
        dict(state="NJ", return_required=True)
    )
    assert not ny_it203_plan(p)["fully_covered"]
    assert capabilities.evaluate(p).blocking


@pytest.mark.parametrize(
    "key,value",
    [
        ("allocation_reviewed", False),
        ("other_state_obligations", []),
        ("resident_states", ["NY"]),
        ("special_condition_a6", True),
        ("yonkers_tax_required", True),
        ("resident_period", ["2025-01-01", "2025-12-31"]),
        ("tax_year", 2026),
    ],
)
def test_invalid_or_incomplete_plan_refuses(key, value):
    p = profile()
    p["state_filings"]["NY"][key] = value
    with pytest.raises(ValueError):
        ny_it203_plan(p)
    with pytest.raises(capabilities.CapabilityError):
        capabilities.evaluate(p)


def test_city_flag_and_joint_spouse_periods_cannot_be_inferred():
    p = profile(True)
    p["flags"]["nyc_resident"] = False
    with pytest.raises(ValueError, match="contradicts"):
        ny_it203_plan(p)
    p = profile()
    p["filing_status"] = "MFJ"
    with pytest.raises(ValueError, match="spouses"):
        ny_it203_plan(p)
    p["state_filings"]["NY"]["joint_residency_identical"] = True
    assert ny_it203_plan(p)


def test_tie_out_routes_actual_state_city_and_yonkers_withholding():
    r = dict(meta=dict(ny_return_type="IT203"), new_york_it203=compute(city=True))
    for category, field in [
        ("state_withholding_ny", "line_62_state_withholding"),
        ("city_withholding_nyc", "line_63_city_withholding"),
        ("city_withholding_yonkers", "line_64_yonkers_withholding"),
    ]:
        assert tie_out._category_target(r, category) == ("new_york_it203", [field])
    assert not tie_out._ny_city_and_yonkers_findings(r, profile(True)["flags"], 1)
    r["new_york_it203"]["line_55_city_and_mctmt"] = 0
    assert any(
        "drops" in x for x in tie_out._ny_city_and_yonkers_findings(r, profile(True)["flags"], 1)
    )


def test_competing_return_variants_refuse():
    r = dict(
        meta=dict(ny_return_type="IT203"),
        new_york_it203=compute(),
        new_york_it201=dict(line_37_taxable_income=1000),
    )
    with pytest.raises(ValueError, match="Competing"):
        selected_ny_return(r)
    with pytest.raises(tie_out.TieOutError):
        tie_out._category_target(r, "state_withholding_ny")
    with pytest.raises(filing_package.FilingPackageError):
        filing_package._refuse_uncomputed_states(r)


def test_calendar_selects_ny_despite_florida_mail():
    events = tax_calendar.build_events(profile(), 2025)
    assert any(e.category == "state_estimated_tax" for e in events)
    ext = tax_calendar.events_extension(2025, state="NY", ny_return_type="IT203")
    assert "IT-203 line 70" in ext[0].description
    assert "IT-203" in ext[1].description


def test_package_and_manual_trueup_note():
    r = dict(
        meta=dict(tax_year=2025, filing_status="Single", ny_return_type="IT203"),
        form_1040=dict(line_24_total_tax=10000),
        new_york_it203=compute(),
    )
    checklist = filing_package.attachment_checklist(r)
    assert any("IT-203-B" in x for x in checklist)
    report = withholding.build_report(r, {}, None, 2025, dt.date(2025, 6, 30))
    assert report.ny_it203_manual and report.ny is None
    assert "IT-203" in withholding.render_markdown(report)


def test_documented_launcher_executes_and_removes_it201(tmp_path):
    from agents.lib.secure_fs import secure_path, write_secure

    secure_path(tmp_path)
    p = profile()
    p["ny_it203_inputs"] = inputs()
    p["ny_it203_inputs"].pop("plan")
    r = dict(
        meta=dict(tax_year=2025, filing_status="Single", limitations=[]),
        form_1040=dict(line_11_agi=100000),
        new_york_it201={},
    )
    for name, data in [
        ("taxpayer_profile.json", p),
        ("result.json", r),
        ("tax_law_2025.json", dict(tax_year=2025, state=ny_it203_cache())),
    ]:
        write_secure(tmp_path / name, json.dumps(data))
    text = (
        ROOT / ".agents/skills/tax-compute/references/state-return-ny-nonresident.md"
    ).read_text()
    script = re.search(r"sh agents/lib/run.sh -c '(.*?)'\n", text, re.S)[1]
    proc = subprocess.run(
        ["sh", "agents/lib/run.sh", "-c", script],
        cwd=ROOT,
        env=os.environ | dict(TAX_WORKSPACE=str(tmp_path)),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    actual = json.loads((tmp_path / "result.json").read_text())
    assert "new_york_it201" not in actual
    assert actual["new_york_it203"]["line_50_state_tax"] == 1981
    assert actual["meta"]["ny_return_type"] == "IT203"
    assert set(compute()) <= set(
        json.loads((ROOT / "agents/templates/result_template.json").read_text())["new_york_it203"]
    )


def test_nonfinite_aggregate_refuses():
    p = {k: 1e308 for k in inputs()["payments"]}
    with pytest.raises(ValueError):
        compute(payments=p)


def test_projection_skips_source_tax_without_false_florida_zero():
    from agents.lib import projection
    from tests.test_projection import _tax_law_2026, _declared_standard

    law = _tax_law_2026()
    law["tax_year"] = 2025
    law["state"] = ny_it203_cache()
    r = projection.build_report(
        _declared_standard(dict(ytd=dict(wages=50000))),
        profile(),
        law,
        None,
        2025,
        dt.date(2025, 6, 30),
    )
    assert r.state is None and not r.state_no_income_tax
    assert r.state_cache_mismatch is None
    assert "IT-203" in r.state_unsupported_note


def test_zero_return_has_computation_evidence_and_city_evidence_is_required():
    r = compute(0, income_rows=[])
    assert r["computed_tax_year"] == 2025 and r["line_58_total_taxes"] == 0
    filing_package._refuse_uncomputed_states(dict(new_york_it203=r))
    r = compute(city=True)
    r["nyc_it3601"]["method"] = "typed"
    findings = tie_out._ny_city_and_yonkers_findings(
        dict(new_york_it203=r), profile(True)["flags"], 1
    )
    assert any("IT-360.1" in f for f in findings)


def test_tie_out_catches_dropped_total_and_missing_selected_return():
    r = compute()
    r["line_58_total_taxes"] = 0
    assert tie_out._ny_city_and_yonkers_findings(dict(new_york_it203=r), {}, 1)
    assert tie_out._ny_city_and_yonkers_findings(dict(meta=dict(ny_return_type="IT203")), {}, 1)


def test_summary_pdf_carries_selected_form_and_refuses_competing_variants(tmp_path):
    from agents.lib import pdf_render
    from agents.lib.secure_fs import secure_path
    import pdfplumber

    secure_path(tmp_path)
    result = dict(
        meta=dict(
            tax_year=2025,
            filing_status="Single",
            ny_return_type="IT203",
            limitations=compute()["notes"],
        ),
        new_york_it203=compute(),
    )
    path = pdf_render.render(result, tmp_path / "summary.pdf")
    with pdfplumber.open(path) as doc:
        text = "\n".join(page.extract_text() or "" for page in doc.pages)
    assert "1,981.00" in text
    assert not pdf_render._is_money("line_45_income_percentage")
    assert "New York IT203" in text
    result["new_york_it201"] = dict(line_34_deduction=8000)
    with pytest.raises(pdf_render.PdfRenderError, match="Competing"):
        pdf_render.render(result, tmp_path / "invalid.pdf")
    assert not (tmp_path / "invalid.pdf").exists()


def test_source_percentage_uses_whole_form_lines_after_raw_federal_tie():
    # IT-203-I p.2: round the total for each box, then derive column AGIs.
    # A net business activity has 25000.51 NY and -10000.02 elsewhere.
    r = compute(15000.49, income_rows=[row(15000.49, 25000.51, line=6)])
    assert r["federal_amount"]["line_6"] == 15000
    assert r["ny_source_amount"]["line_6"] == 25001
    assert r["line_45_income_percentage"] == 1.6667
    # Two documents in the same box round AFTER aggregation: .49+.49=>1.
    r = compute(100000.98, income_rows=[row(100000.49, 40000.49), row(0.49, 0.49, ident="second")])
    assert r["federal_amount"]["line_1"] == 100001
    assert r["ny_source_amount"]["line_1"] == 40001


def test_city_deduction_election_and_whole_mctmt_boxes():
    with pytest.raises(ValueError, match="election"):
        compute(city=True, city_inputs=city_args() | dict(resident_itemized_deduction=10000))
    # IT203 zone 1: 70100*.006=420.60, rounded as box52d before adding.
    r = compute(mctmt_zone1=70100, mctmt_zone2=70000)
    assert r["line_52d_zone1_tax"] == 421
    assert r["line_52f_mctmt"] == 659  # 421+238, not 658.60.


def test_it203_eic_disclosure_names_preallocation_and_refundable_worksheets():
    p = profile()
    p["flags"]["may_qualify_for_eic"] = True
    r = capabilities.evaluate(p)
    finding = next(f for f in r.manual if f.rule_id == "earned_income_credit")
    assert "IT-203 line 43" in finding.form
    assert "pre-allocation" in finding.note


def test_legacy_city_acknowledgement_cannot_waive_it203_city_evidence():
    p = profile(True)
    p["acknowledged_limitations"] = ["ny_city_or_yonkers_household_unmodeled"]
    flags = tie_out._flags_for_city_and_yonkers(p, p["flags"])
    assert flags["nyc_resident"] is True
    r = compute(city=True)
    r["nyc_it3601"] = None
    assert tie_out._ny_city_and_yonkers_findings(dict(new_york_it203=r), flags, 1)
