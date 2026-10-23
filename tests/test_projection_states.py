"""AA43: full-year form forecasts, covered-year harbors and state calendars.

NY/NC law comes from existing published TY2025 worksheet fixtures. Federal
parameters in these tests are synthetic; no runtime tax-law cache is committed.
"""

import copy
import datetime as dt
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agents.lib import projection as P
from agents.lib.projection.states import project_resident_state
from agents.lib.secure_fs import write_secure
from tests.test_projection import _proj, _tax_law_2026
from tests.test_state_planning import case


def forecast(code="NC", agi=60000, status="MFJ"):
    result, law = case(code, agi, status)
    plan = copy.deepcopy(result["state_planning_inputs"])
    plan.pop("income_treatment")
    plan.pop("retirement_basis_matches_federal")
    plan.update(filing_status=status.upper(), federal_agi_at_review=agi,
                self_employment_net_at_review=0, taxable_social_security=0,
                farmer_fisherman=False, calendar_year_confirmed=True, use_tax=0,
                prior_year={"tax_year": 2024, "method": "ineligible"})
    if code == "NY":
        plan["ny"]["mctmt_by_owner"] = {k: {"zone_1_net_earnings": 0, "zone_2_net_earnings": 0}
                          for k in (["primary", "spouse"] if status == "MFJ" else ["primary"])}
        plan["ny"]["credit_worksheets"] = dict(resident_credit=0, other_nonrefundable_credits=0,
            net_other_state_taxes=0, other_nyc_taxes=0, nyc_nonrefundable_credits=0, refundable={})
        plan["ny"]["star_credit_payment"] = 0
        plan["ny"]["all_withholding_confirmed"] = True
        law["state"]["estimated_tax_safe_harbor"]["de_minimis"] = 300  # IT-2105.9-I2025p2
    else:
        # NC-40(2025)p2 and105-163.15(d),(f), passed as fetched law.
        law["state"]["estimated_tax"] = dict(required_threshold=1000, current_year_rate=.9,
                                              prior_year_rate=1, farmer_rate=2/3)
    profile = {"tax_year": 2025, "filing_status": status, "address": {"state": code},
               "dependents": [{"ctc_eligible": True}] if code == "NC" else []}
    inputs = dict(tax_year=2025, ytd={"state_withholding": 0}, estimated_payments_made={"state": 0})
    proj = _proj(wages=agi, state_withholding=0)
    return plan, proj, SimpleNamespace(agi=agi), inputs, profile, law


def run(args):
    return project_resident_state(*args, args[4]["filing_status"].upper(), 2025)


def test_nc_child_cliff_raw_deductions_and_current_share():
    state, harbor = run(forecast())
    # MFJ60k -2,500child -25,500std =32k; *.0425=1,360;90%=1,224.
    assert (state.agi, state.taxable_income, state.tax, state.balance) == (60000, 32000, 1360, 1360)
    assert harbor.options == {"current_year": 1224}
    assert harbor.required_annual == harbor.shortfall == 1224
    assert state.form_modeled and not state.itemized
    args = list(forecast(agi=80000))
    args[0]["nc"]["itemized"] = {"medical": 32000}
    state, _ = run(args)
    # Medical32k - .075*80k=26k beats25.5kstd; child2k;52k*.0425=2,210.
    assert state.deduction == 26000 and state.itemized and state.tax == 2210


def test_nc_prior_no_high_income_factor_and_use_tax_separate():
    args = list(forecast())
    args[0]["prior_year"] = dict(tax_year=2024, method="filed_return", tax_base=1000,
                                  filed_12_month_return_confirmed=True)
    args[0]["use_tax"] = 200
    args[1] = replace(args[1], state_withholding=100, estimated_state_paid=50)
    args[3]["ytd"]["state_withholding"] = 100
    args[3]["estimated_payments_made"]["state"] = 50
    state, h = run(args)
    assert (h.required_annual, h.shortfall, state.tax, state.balance) == (1000, 850, 1560, 1410)
    assert h.current_year_base == 1360


@pytest.mark.parametrize("withheld,exempt", [(360.01, True), (360, False)])
def test_nc_floor_excludes_estimates_and_is_strict(withheld, exempt):
    args = list(forecast())
    args[1] = replace(args[1], state_withholding=withheld, estimated_state_paid=10000)
    _, h = run(args)
    assert h.de_minimis is exempt
    assert (h.required_annual == 0) is exempt


def city(args):
    p = args[0]["ny"]
    p["local"].update(nyc_resident=True, nyc_resident_spouses=2, charitable_gifts_trust_deducted=0)


def test_nyc_whole_liability_withholding_credits_and_use_tax():
    args = list(forecast("NY", 200000))
    city(args)
    args[0]["use_tax"] = 250
    args[0]["ny"]["credit_worksheets"]["refundable"] = {"nyc_school_tax_fixed": 125}
    args[1] = replace(args[1], state_withholding=17500)
    state, h = run(args)
    # NY WS2:9897+333+807*((200000-161550)/50000)=10850.583->10851;
    # NYC3264+(183950-90000)*.03876=6905.502->6906.
    # Statefixture dependent_count0 and NYstandard16,050 (ny_it203_cache).
    assert state.state_income_tax == 10851
    assert state.local_income_tax == 6906
    assert state.tax == 18007
    assert state.balance == 382  #18007-125-17500
    assert h.current_year_base == 17632
    assert h.options["current_year"] == 15868.8
    assert not state.balance < -5000  # historic state-only refund regression


def test_ny_prior_base_uses_nyagi_and_mctd_not_federal_agi():
    args = list(forecast("NY", 200000))
    args[0]["prior_year"] = dict(tax_year=2024, method="filed_return", tax_base=8000,
        nyagi=160000, mctd_net_earnings=0, filed_12_month_return_confirmed=True)
    _, h = run(args)
    assert h.options["prior_year"] == h.required_annual == 8800
    args[0]["prior_year"]["nyagi"] = 140000
    _, h = run(args)
    assert h.options["prior_year"] == 8000


def test_yonkers_full_year_surcharge_and_nonzero_reviewed_credit_worksheet():
    args = list(forecast("NY", 200000))
    local = args[0]["ny"]["local"]
    local.update(yonkers_resident=True, yonkers_resident_spouses=2,
                 yonkers_worksheet_credits={}, star_reconciliation=0)
    args[5]["state"]["yonkers_tax"] = {"resident_surcharge_rate": .1675}  #IT-201-I2025p17
    state, _ = run(args)
    # IT-20155=round(10851*.1675)=1818, distinct from NYC rates.
    assert state.local_income_tax == 1818


def test_mctmt_threshold_is_per_owner_and_zone():
    args = list(forecast("NY", 200000))
    args[0]["self_employment_net_at_review"] = 80000
    args[1] = replace(args[1], self_employment_net=80000)
    owners = args[0]["ny"]["mctmt_by_owner"]
    for amounts in owners.values():
        amounts["zone_1_net_earnings"] = 40000
    state, _ = run(args)
    assert state.local_income_tax == 0  #80kjoint must not cross50k/personcliff
    owners["primary"]["zone_1_net_earnings"] = 60000
    owners["spouse"]["zone_1_net_earnings"] = 20000
    state, _ = run(args)
    assert state.local_income_tax == 360  #60k*.006,whole zone


@pytest.mark.parametrize("code", ["NY", "NC"])
@pytest.mark.parametrize("field,value", [("federal_agi_at_review", 1), ("tax_year", 2026),
    ("tax_year", 2025.0), ("scope_confirmed", False), ("residency", "part_year"),
    ("filing_status", "HOH"), ("farmer_fisherman", True), ("use_tax", None),
    ("use_tax", float("nan")), ("use_tax", True), ("calendar_year_confirmed", None)])
def test_incomplete_or_stale_forecast_refuses(code, field, value):
    args = list(forecast(code))
    args[0][field] = value
    with pytest.raises(P.ProjectionError):
        run(args)


@pytest.mark.parametrize("code", ["NY", "NC"])
def test_prior_year_not_gross_archive_and_missing_confirmation_refuses(code):
    args = list(forecast(code))
    args[0]["prior_year"] = dict(tax_year=2024, method="filed_return", tax_base=0)
    with pytest.raises(P.ProjectionError, match="twelve-month"):
        run(args)
    args[0]["prior_year"] = dict(tax_year=2024, method="no_liability", tax_base=0,
                                  no_liability_exception_confirmed=True)
    _, h = run(args)
    assert h.required_annual == h.shortfall == 0 and h.prior_no_liability


def test_incomplete_city_yonkers_or_owner_allocation_refuses():
    for change in (lambda p: p["local"].pop("nyc_resident"),
                   lambda p: p["local"].update(nyc_resident=True),
                   lambda p: p["local"].update(yonkers_nonresident_earnings=True),
                   lambda p: p["mctmt_by_owner"].pop("spouse")):
        args = list(forecast("NY"))
        change(args[0]["ny"])
        with pytest.raises(P.ProjectionError):
            run(args)


def build(code="NC", as_of=dt.date(2025, 7, 1), today=None):
    plan, _, _, _, profile, law = forecast(code)
    law["federal"] = _tax_law_2026()["federal"]
    inputs = dict(tax_year=2025, pay_frequency="biweekly", state_projection=plan,
        ytd={"wages": 60000 * P.elapsed_fraction(as_of), "state_withholding": 0},
        forecast_overrides={"itemized_deductions": 0, "charitable_cash_gifts": 0},
        estimated_payments_made={"federal": 0, "state": 0})
    return P.build_report(inputs, profile, law, None, 2025, as_of, today=today), inputs, profile, law


@pytest.mark.parametrize("code,form", [("NC", "NC-40"), ("NY", "IT-2105")])
def test_build_report_json_markdown_and_q3_deadline(code, form):
    report, *_ = build(code)
    assert report.state is not None, report.state_unsupported_note
    h = report.state_payment_schedule
    assert h is not None and h.form == form
    assert [v.quarter for v in h.vouchers] == [3, 4]
    assert [v.due_date for v in h.vouchers] == ["2025-09-15", "2026-01-15"]
    assert sum(v.amount for v in h.vouchers) == h.shortfall
    assert h.voucher_catchup_note and "ANNUAL" in h.voucher_catchup_note
    payload = P._report_to_dict(report)
    assert payload["state_payment_schedule"]["vouchers"][0]["quarter"] == 3
    md = P.render_markdown(report)
    assert form in md and "four EQUAL quarters" in md and "bracket ESTIMATE" not in md
    assert "State estimates paid" in md
    assert report.state_bracket_estimate_note is None


def test_report_refuses_state_only_and_preserves_federal_on_missing_evidence():
    _, inputs, profile, law = build("NY")
    inputs["state_projection"]["ny"]["local"].pop("nyc_resident")
    report = P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))
    assert report.federal and report.state is None and report.state_payment_schedule is None
    assert "NYC/Yonkers" in report.state_unsupported_note
    assert "No state/local balance" in P.render_markdown(report)


def test_cli_carries_same_schedule_and_no_source_mutation(tmp_path):
    _, inputs, profile, law = build()
    for name, obj in (("projection_inputs.json", inputs), ("taxpayer_profile.json", profile),
                      ("tax_law_2025.json", law)):
        write_secure(tmp_path / name, json.dumps(obj))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    proc = subprocess.run(["sh", "agents/lib/run.sh", "-m", "agents.lib.projection", "--workspace",
        str(tmp_path), "--inputs", "projection_inputs.json", "--year", "2025", "--as-of", "07-01", "--today", "2025-07-01", "--json"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["state_payment_schedule"]["form"] == "NC-40"
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("code", ["NY", "NC"])
def test_bad_worksheet_value_is_not_echoed(code):
    args = list(forecast(code))
    if code == "NY":
        args[0]["ny"]["itemized"] = {"itemized_before_adjustment": "PRIVATE_NAME", "charitable_contributions": 0}
    else:
        args[0]["nc"]["schedule_s"]["exempt_interest"] = "PRIVATE_NAME"
    with pytest.raises(P.ProjectionError) as exc:
        run(args)
    assert "PRIVATE_NAME" not in str(exc.value)


def test_ny_local_profile_cannot_be_overridden_by_a_zero_tax_forecast():
    args = list(forecast("NY"))
    args[4]["flags"] = {"nyc_resident": True}
    with pytest.raises(P.ProjectionError, match="contradicts"):
        run(args)
    args = list(forecast("NC"))
    args[4]["flags"] = {"nc_has_taxed_pte": True}
    with pytest.raises(P.ProjectionError, match="Uncomputed NC"):
        run(args)


@pytest.mark.parametrize("code", ["NY", "NC"])
def test_wrong_year_missing_payments_and_allocations_refuse(code):
    for change in (lambda a: a[3].pop("tax_year"),
                   lambda a: a[3]["ytd"].pop("state_withholding"),
                   lambda a: a[3]["estimated_payments_made"].pop("state"),
                   lambda a: a[4].update(flags={"moved_states_this_year": True}),
                   lambda a: a[5]["state"].update(code="CA")):
        args = list(forecast(code))
        change(args)
        with pytest.raises(P.ProjectionError):
            run(args)


def test_ny_combined_floor_counts_taxes_and_uses_current_base_before_estimates():
    args = list(forecast("NY", 200000))
    city(args)
    args[1] = replace(args[1], state_withholding=17157, estimated_state_paid=50000)
    _, h = run(args)
    assert not h.de_minimis  #17757-17157=600,two-taxthreshold;estimatesexcluded
    args[1] = replace(args[1], state_withholding=17157.01)
    _, h = run(args)
    assert h.de_minimis and h.required_annual == 0


@pytest.mark.parametrize("code", ["NY", "NC"])
def test_required_payment_reads_fetched_shares_not_federal_constants(code):
    args = list(forecast(code))
    if code == "NY":
        args[5]["state"]["estimated_tax_safe_harbor"]["current_year_share"] = .5
    else:
        args[5]["state"]["estimated_tax"]["current_year_rate"] = .5
    state, h = run(args)
    assert h.required_annual == round(state.tax * .5, 2)


def test_ny_credit_caps_and_refundables_apply_in_different_places():
    args = list(forecast("NY", 200000))
    city(args)
    c = args[0]["ny"]["credit_worksheets"]
    c.update(other_nonrefundable_credits=50000, net_other_state_taxes=100,
             nyc_nonrefundable_credits=20000, other_nyc_taxes=200, refundable={"other_refundable": 250})
    state, h = run(args)
    assert (state.state_income_tax, state.local_income_tax, state.balance) == (100, 0, -150)
    assert h.current_year_base == 0
    # A null/false refundable amount is unanswered/wrong-type, not a zero.
    c["refundable"]["other_refundable"] = False
    with pytest.raises(P.ProjectionError):
        run(args)


def test_ny_yonkers_reprices_reviewed_nonzero_worksheet_credits():
    args = list(forecast("NY", 200000))
    args[5]["state"]["yonkers_tax"] = {"resident_surcharge_rate": .1675}
    args[0]["ny"]["local"].update(yonkers_resident=True, yonkers_resident_spouses=2,
        yonkers_worksheet_credits={"empire_state_child": 1000}, star_reconciliation=0)
    args[0]["ny"]["credit_worksheets"]["refundable"] = {"empire_state_child": 1000}
    state, _ = run(args)
    assert state.local_income_tax == 1650  #round((10851-1000)*.1675)


@pytest.mark.parametrize("code", ["NY", "NC"])
def test_covered_schedule_deadlines_and_closed_year_notes(code):
    r, *_ = build(code, dt.date(2025, 3, 1), dt.date(2025, 3, 1))
    h = r.state_payment_schedule
    assert [v.due_date for v in h.vouchers] == ["2025-04-15", "2025-06-16", "2025-09-15", "2026-01-15"]
    assert h.voucher_catchup_note is None
    r, *_ = build(code, dt.date(2025, 12, 31), dt.date(2026, 10, 22))
    h = r.state_payment_schedule
    assert h.voucher_dates_note and h.form in h.voucher_dates_note


def test_missing_dedicated_forecast_stays_unavailable():
    _, inputs, profile, law = build("NY")
    inputs.pop("state_projection")
    report = P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))
    assert report.state is None and report.state_payment_schedule is None


def test_ny_combined_penalty_requirement_is_not_sum_of_voucher_column_minima():
    args = list(forecast("NY", 200000))
    city(args)
    args[0]["prior_year"] = dict(tax_year=2024, method="filed_return", tax_base=20500,
        nyagi=100000, mctd_net_earnings=0, filed_12_month_return_confirmed=True)
    _, h = run(args)
    #IT-2105.9 lines13/16/17:min(90%*(10851+6906),20500)=15981.3.
    #Separate IT-2105 column minima (State500+City6215.4=6715.4) answer a
    #different worksheet question and UNDERstate this combined safe harbor.
    assert h.required_annual == h.shortfall == 15981.3


def test_combined_payments_do_not_change_computed_mctmt_liability():
    args = list(forecast("NY", 200000))
    args[0]["self_employment_net_at_review"] = 80000
    args[1] = replace(args[1], self_employment_net=80000, state_withholding=20000)
    args[0]["ny"]["mctmt_by_owner"]["primary"]["zone_1_net_earnings"] = 60000
    args[0]["ny"]["credit_worksheets"]["refundable"] = {"other_refundable": 15000}
    state, h = run(args)
    #MCTMT tax remains360; IT-2105.9 combines taxes/credits/payments and
    #finds no additional estimate needed, rather than deleting the tax.
    assert state.local_income_tax == 360
    assert h.current_year_base == h.required_annual == h.shortfall == 0


def test_mctd_prior_income_can_select_higher_combined_share():
    args = list(forecast("NY", 200000))
    args[0]["self_employment_net_at_review"] = 80000
    args[1] = replace(args[1], self_employment_net=80000)
    args[0]["ny"]["mctmt_by_owner"]["primary"]["zone_1_net_earnings"] = 60000
    args[0]["prior_year"] = dict(tax_year=2024, method="filed_return", tax_base=8200,
        nyagi=140000, mctd_net_earnings=160000, filed_12_month_return_confirmed=True)
    _, h = run(args)
    #IT-2105.9 prior worksheet applies110% to8200 if the applicable prior
    #NYAGI or MCTD earnings measure exceeds the fetched150k threshold.
    assert h.required_annual == 9020


def test_star_payment_reduces_penalty_base_without_double_refunding_it():
    args = list(forecast("NY", 200000))
    args[0]["ny"]["star_credit_payment"] = 1000
    state, h = run(args)
    #IT-2105.9 line7a reduces10851 to9851;90%=8865.9. The STAR check was
    #already a separate payment to the homeowner, not another IT-201 refund.
    assert state.balance == 10851
    assert h.current_year_base == 9851
    assert h.required_annual == 8865.9
    args[0]["ny"].pop("star_credit_payment")
    with pytest.raises(P.ProjectionError):
        run(args)


def test_prior_net_loss_does_not_invalidate_a_filed_zero_tax_base():
    args = list(forecast("NY", 200000))
    args[0]["prior_year"] = dict(tax_year=2024, method="filed_return", tax_base=0,
        nyagi=-10000, mctd_net_earnings=-5000, filed_12_month_return_confirmed=True)
    _, h = run(args)
    assert h.required_annual == h.shortfall == 0


@pytest.mark.parametrize("code", ["NY", "NC"])
@pytest.mark.parametrize("status", ["SINGLE", "MFJ", "MFS", "HOH", "QSS"])
def test_dedicated_forecast_keeps_each_status_deduction_and_spouse_election(code, status):
    args = list(forecast(code))
    args[0]["filing_status"] = args[4]["filing_status"] = status
    if code == "NY":
        if status != "MFJ":
            args[0]["ny"]["mctmt_by_owner"].pop("spouse")
        if status == "MFS":
            args[0]["ny"]["itemized"]["mfs_spouse_itemizes"] = False
    else:
        args[0]["nc"]["spouse_itemizes_nc"] = False if status == "MFS" else None
    state, h = run(args)
    # Each state's published status deduction is applied before its tax;
    # HOH/QSS cannot silently read the single or joint column by accident.
    assert state.deduction == args[5]["state"]["standard_deduction"][status.lower()]
    assert state.form_modeled and h.form in {"IT-2105", "NC-40"}


@pytest.mark.parametrize("code,agi,required,voucher", [("NC", 80000, "$2,008.12", "$1,004.06"),
                                                       ("NY", 200000, "$4,868.80", "$2,434.40")])
def test_displayed_voucher_cents_reconcile_with_the_displayed_shortfall(code, agi, required, voucher):
    _, inputs, profile, law = build(code)
    as_of = dt.date(2025, 7, 1)
    inputs["ytd"]["wages"] = agi * P.elapsed_fraction(as_of)
    inputs["state_projection"]["federal_agi_at_review"] = agi
    if code == "NY":
        city([inputs["state_projection"]])
        inputs["ytd"]["state_withholding"] = 10000 * P.elapsed_fraction(as_of)
        inputs["estimated_payments_made"]["state"] = 1000
        inputs["state_projection"]["ny"]["credit_worksheets"]["refundable"] = {"nyc_school_tax_fixed": 125}
    report = P.build_report(inputs, profile, law, None, 2025, as_of)
    md = P.render_markdown(report)
    #NC:(80k-2kchild-25.5kstd)*.0425*.9=2008.12. NY:(10851+6906-125)*.9
    #less10kwithholding/1kestimates=4868.80. Two printed payments must fund it.
    assert f"| **Shortfall** | {required} |" in md
    assert f"| Q3 | 2025-09-15 | {voucher} |" in md
    assert f"| Q4 | 2026-01-15 | {voucher} |" in md


@pytest.mark.parametrize("code", ["NY", "NC"])
def test_state_subtractions_require_matching_federal_forecast_income(code):
    args = list(forecast(code))
    if code == "NY":
        args[0]["ny"]["subtractions"]["us_government_bond_interest"] = 5000
    else:
        args[0]["nc"]["schedule_s"]["us_obligation_interest"] = 5000
    with pytest.raises(P.ProjectionError, match="interest"):
        run(args)
    #A matched interest forecast is allowed:55kwages+5kinterest=60kFAGI.
    args[1] = replace(args[1], wages=55000, taxable_interest=5000)
    state, _ = run(args)
    assert state.agi == 55000


@pytest.mark.parametrize("field", ["us_government_fund_dividends", "state_local_refund_in_agi",
                                   "railroad_tier2_in_agi", "government_retirement_taxable"])
def test_nc_source_components_cannot_be_subtracted_from_unrelated_income(field):
    args = list(forecast())
    args[0]["nc"]["schedule_s"][field] = 1000
    if field == "us_government_fund_dividends":
        args[0]["nc"]["schedule_s"]["us_government_share"] = .5
    with pytest.raises(P.ProjectionError):
        run(args)


def test_nc_matched_government_dividend_forecast_is_allowed():
    args = list(forecast())
    args[0]["nc"]["schedule_s"].update(us_government_fund_dividends=1000, us_government_share=.5)
    args[1] = replace(args[1], wages=59000, ordinary_dividends=1000)
    state, _ = run(args)
    #The500USshare is included federally and then excluded:NCAGI59500.
    assert state.agi == 59500


def test_ny_refund_and_unknown_modification_fields_refuse():
    args = list(forecast("NY"))
    args[0]["ny"]["subtractions"]["taxable_refunds"] = 1000
    with pytest.raises(P.ProjectionError, match="other_income"):
        run(args)
    args[0]["ny"]["subtractions"] = {"taxable_refund": 1000}
    with pytest.raises(P.ProjectionError, match="Unrecognized"):
        run(args)
    args[0]["ny"]["subtractions"] = {}
    args[0]["ny"]["additions"] = {"taxable_refund": 1000}
    with pytest.raises(P.ProjectionError, match="Unrecognized"):
        run(args)


def test_ny_all_withholding_confirmation_and_imported_city_actuals_boundary():
    args = list(forecast("NY"))
    args[0]["ny"].pop("all_withholding_confirmed")
    with pytest.raises(P.ProjectionError, match="ALL NY"):
        run(args)
    args[0]["ny"]["all_withholding_confirmed"] = True
    city(args)
    args[3]["paystub_provenance"] = {}
    with pytest.raises(P.ProjectionError, match="State withholding only"):
        run(args)
    #Explicit manual resolution is the documented import contract.
    args[3]["paystub_provenance"] = None
    state, _ = run(args)
    assert state.local_income_tax > 0


@pytest.mark.parametrize("code", ["NY", "NC"])
def test_reviewed_full_year_residence_is_independent_of_mailing_address(code):
    _, inputs, profile, law = build(code)
    profile["address"]["state"] = "FL"
    report = P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))
    assert report.state.code == code
    assert report.state_payment_schedule is not None
    assert report.state_cache_mismatch is None and not report.state_no_income_tax


def test_incomplete_declared_forecast_names_its_missing_evidence_not_the_mail_state():
    _, inputs, profile, law = build("NY")
    profile["address"]["state"] = "FL"
    inputs["state_projection"]["ny"].pop("all_withholding_confirmed")
    report = P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))
    assert report.state is None and "ALL NY" in report.state_unsupported_note
    assert not report.state_no_income_tax and report.state_cache_mismatch is None


@pytest.mark.parametrize("code", ["NY", "NC"])
@pytest.mark.parametrize("bad_year", [2026, 2025.0, True, "2025", None])
def test_a_declared_profile_year_must_match_the_forecast(code, bad_year):
    args = list(forecast(code))
    args[4]["tax_year"] = bad_year
    with pytest.raises(P.ProjectionError, match="taxpayer_profile.tax_year"):
        run(args)
    _, inputs, profile, law = build(code)
    profile["tax_year"] = bad_year
    #Refuse before publishing even the federal portion from a wrong-year profile.
    with pytest.raises(P.ProjectionError, match="taxpayer_profile.tax_year"):
        P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))


@pytest.mark.parametrize("dependents", [None, [{"ctc_eligible": "false"}], [{}]])
def test_incomplete_dependent_evidence_blocks_both_forecast_portions(dependents):
    _, inputs, profile, law = build("NC")
    profile["dependents"] = dependents
    with pytest.raises(P.ProjectionError, match="dependent"):
        P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))


@pytest.mark.parametrize("missing", ["amount_per_child", "phase_out_start"])
@pytest.mark.parametrize("value", [None, -1, True, float("nan")])
def test_qualifying_children_cannot_silently_lose_credit_when_federal_law_is_unfetched(missing, value):
    _, inputs, profile, law = build("NC")
    if missing == "phase_out_start" and value is not None:
        law["federal"]["child_tax_credit"][missing]["mfj"] = value
    else:
        law["federal"]["child_tax_credit"][missing] = value
    with pytest.raises(P.ProjectionError, match="child_tax_credit"):
        P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))


@pytest.mark.parametrize("code", ["NY", "NC"])
def test_as_of_year_cannot_schedule_another_years_state_vouchers(code):
    _, inputs, profile, law = build(code)
    with pytest.raises(P.ProjectionError, match="as_of year"):
        P.build_report(inputs, profile, law, None, 2025, dt.date(2026, 7, 1))


@pytest.mark.parametrize("code", ["NY", "NC"])
@pytest.mark.parametrize("bad_year", [2026, 2025.0, True, "2025", None])
def test_input_year_is_bound_before_either_forecast_portion(code, bad_year):
    _, inputs, profile, law = build(code)
    inputs["tax_year"] = bad_year
    with pytest.raises(P.ProjectionError, match="projection_inputs.tax_year"):
        P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))


@pytest.mark.parametrize("code,key", [
    *[(c, k) for c in ("NY", "NC") for k in (
        "moved_states_this_year", "statutory_resident_of_another_state",
        "has_nonresident_source_income", "moved_abroad_this_year")],
    *[("NC", k) for k in ("nc_has_business_adjustments", "nc_has_recovery_deductions",
        "nc_has_nol", "nc_has_taxed_pte", "nc_has_other_schedule_s_deductions", "nc_has_other_credits")],
    *[("NY", k) for k in ("nyc_resident", "yonkers_resident", "yonkers_nonresident_earnings")]])
@pytest.mark.parametrize("value", ["true", 1])
def test_profile_scope_flags_require_boolean_values(code, key, value):
    args = list(forecast(code))
    args[4]["flags"] = {key: value}
    with pytest.raises(P.ProjectionError, match="must be true, false"):
        run(args)


@pytest.mark.parametrize("status", ["MFJ", "SINGLE"])
def test_inactive_local_profile_details_do_not_block_upstate_forecast(status):
    args = list(forecast("NY", status=status))
    args[4]["flags"] = {"nyc_resident": False, "yonkers_resident": False,
        "nyc_resident_spouses": 0, "yonkers_resident_spouses": 0,
        "nyc_resident_full_year": False, "yonkers_resident_full_year": False}
    state, _ = run(args)
    assert state.local_income_tax == 0


@pytest.mark.parametrize("status,value", [("MFJ", 1), ("MFJ", "2"), ("MFJ", 2.0),
                                         ("SINGLE", False), ("SINGLE", "true")])
def test_applicable_local_profile_detail_must_match(status, value):
    args = list(forecast("NY", status=status))
    local = args[0]["ny"]["local"]
    local.update(nyc_resident=True, charitable_gifts_trust_deducted=0)
    key = "nyc_resident_spouses" if status == "MFJ" else "nyc_resident_full_year"
    local[key] = 2 if status == "MFJ" else True
    args[4]["flags"] = {"nyc_resident": True, key: value}
    with pytest.raises(P.ProjectionError):
        run(args)
    args[4]["flags"][key] = local[key]
    assert run(args)[0].local_income_tax > 0


@pytest.mark.parametrize("section", ["state", "city"])
def test_unfetched_ny_table_bases_warning_reaches_report(section):
    _, inputs, profile, law = build("NY")
    city([inputs["state_projection"]])
    cfg = law["state"] if section == "state" else law["state"]["nyc_tax"]
    cfg["tax_table"]["table_bases"] = None
    report = P.build_report(inputs, profile, law, None, 2025, dt.date(2025, 7, 1))
    assert report.state is not None, report.state_unsupported_note
    assert any("table_bases is not fetched" in n for n in report.state_payment_schedule.notes)
    assert "dollar off the published table" in P.render_markdown(report)
    assert "table_bases is not fetched" in json.dumps(P._report_to_dict(report))


def test_gifts_trust_addback_requires_elected_ny_itemizing():
    args = list(forecast("NY"))
    city(args)
    args[0]["ny"]["local"]["charitable_gifts_trust_deducted"] = 10000
    with pytest.raises(P.ProjectionError):
        run(args)
    # IT-201-I2025 line47: only a contribution deducted on IT-196 is added back.
    #40kitemized beats16,050standard. City:60k-40k+10ktrust=30k.
    args[0]["ny"]["itemized"] = dict(itemized_before_adjustment=40000,
                                      charitable_contributions=10000)
    state, _ = run(args)
    assert state.itemized
    from agents.lib.tax_computations.state.ny import compute_nyc_resident_tax
    cfg = args[5]["state"]
    expected = compute_nyc_resident_tax(30000, "mfj", cfg["nyc_tax"]["brackets"],
        cfg["bracket_schedule_by_status"], cfg["nyc_tax"]["tax_table"])["tax"]
    assert state.local_income_tax == expected


@pytest.mark.parametrize("use_tax", [0, 200])
def test_nc_notes_use_the_reviewed_forecast_use_tax(use_tax):
    args = list(forecast("NC"))
    args[0]["use_tax"] = use_tax
    state, harbor = run(args)
    assert state.tax == 1360 + use_tax
    assert harbor.current_year_base == 1360
    assert not any("USE TAX NOT ANSWERED" in n for n in harbor.notes)


def test_nc_spouse_election_note_is_preserved():
    args = list(forecast("NC"))
    args[0]["filing_status"] = args[4]["filing_status"] = "MFS"
    args[0]["nc"]["spouse_itemizes_nc"] = True
    state, harbor = run(args)
    assert state.deduction == 0
    assert any("MFS with a spouse claiming NC itemized deductions" in n for n in harbor.notes)
