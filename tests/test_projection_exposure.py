"""AA42: hand-worked AMT, payment propagation and premium-year isolation."""

import dataclasses
import datetime
import math
import json
import subprocess

import pytest

from tests import test_projection as old
from agents.lib import projection as P
from agents.lib.projection.exposure import project_irmaa
from agents.lib.secure_fs import secure_path, write_secure


def amt_case(**changes):
    profile = {"tax_year": 2026, "filing_status": "MFJ", "dependents": []}
    law = old._tax_law_2026()
    # Synthetic law: AMTI350K - exemption50K =300K; 26% of100K +28% of200K =82K.
    law["federal"]["amt"] = {"exemption": {"mfj": 50000, "single": 30000, "mfs": 20000},
        "phaseout_start": {"mfj": 500000, "single": 500000, "mfs": 500000},
        "rate_28_breakpoint": 100000, "phaseout_rate": None}
    raw = {"tax_year": 2026, "filing_status": "MFJ", "full_year_inputs_confirmed": True,
        "federal_agi_at_review": 200000, "unsupported_items": [], "iso_bargain_element": 150000,
        "private_activity_bond_interest": 0, "salt_deducted": 0, "senior_deduction": 0,
        "section_68_reduction": 0, "regular_foreign_tax_credit": 0,
        "amt_foreign_tax_credit": 0, "prior_minimum_tax_credit": 0}
    raw.update(changes)
    proj = old._proj(wages=200000, amt_projection=raw)
    return proj, profile, law


def price(proj, profile, law):
    return P.project_federal_tax(proj, profile, law, requested_year=2026)


def test_iso_amt_reaches_total_and_balance():
    f = price(*amt_case())
    # Regular tax=2K +20%*(170K-20K)=32K;82K TMT-32K=50K AMT.
    assert f.tax_before_credits == 32000
    assert f.amt_details["amti"] == 350000
    assert f.amt_details["tmt"] == 82000
    assert f.amt == 50000
    assert f.total_tax == 82000
    assert f.balance == 70000  # 12K withholding from synthetic projected inputs


def test_amt_reaches_current_prong_vouchers_and_w4():
    proj, profile, law = amt_case()
    inputs = old._declared_standard({"tax_year": 2026, "amt_projection": proj.amt_projection,
        "ytd": {"wages": 200000, "federal_withholding": 12000}})
    report = P.build_report(inputs, profile, law, None, 2026, datetime.date(2026,12,31))
    assert report.federal.amt == 50000
    assert report.safe_harbor.pct90_current == 73800
    assert report.safe_harbor.shortfall == 61800
    assert sum(v.amount for v in report.vouchers) == 61800
    assert report.w4_zero.shortfall == report.federal.balance
    payload = P._report_to_dict(report)
    assert payload["federal"]["amt"] == 50000
    assert "AMT (Form 6251) | $50,000" in P.render_markdown(report)


def test_missing_amended_inputs_are_unmodeled_never_zero():
    proj, profile, law = amt_case()
    f = price(dataclasses.replace(proj,amt_projection=None),profile,law)
    assert f.amt is None and f.amt_details is None
    assert "exclude AMT" in f.amt_note and "may be too low" in f.amt_note


def test_missing_amt_with_known_opening_credit_cannot_overstate_payments():
    proj,profile,law=amt_case(iso_bargain_element=0,prior_minimum_tax_credit=1000)
    profile["prior_year"]={"amt_credit_carryforward":1000}
    law["federal"]["amt"]["exemption"]["mfj"]=150000
    assert price(proj,profile,law).total_tax==31000  #32K regular less1K usable credit
    with pytest.raises(P.ProjectionError,match="omitting a known credit"):
        price(dataclasses.replace(proj,amt_projection=None),profile,law)


def test_missing_amt_with_known_foreign_tax_needs_reviewed_credit_worksheet():
    proj,profile,law=amt_case(iso_bargain_element=0,regular_foreign_tax_credit=500,amt_foreign_tax_credit=500)
    profile["flags"]={"has_foreign_tax_paid":True}
    law["federal"]["amt"]["exemption"]["mfj"]=150000
    assert price(proj,profile,law).total_tax==31500
    with pytest.raises(P.ProjectionError,match="omitting a known credit"):
        price(dataclasses.replace(proj,amt_projection=None),profile,law)


@pytest.mark.parametrize("bad",[None,"true",1,0,[],{}])
def test_unmodeled_amt_foreign_credit_screen_rejects_unresolved_or_bad_flag(bad):
    proj,profile,law=amt_case()
    profile["flags"]={"has_foreign_tax_paid":bad}
    with pytest.raises(P.ProjectionError,match="has_foreign_tax_paid"):
        price(dataclasses.replace(proj,amt_projection=None),profile,law)


@pytest.mark.parametrize("field", ["iso_bargain_element", "private_activity_bond_interest", "salt_deducted",
    "senior_deduction", "section_68_reduction", "regular_foreign_tax_credit", "amt_foreign_tax_credit",
    "prior_minimum_tax_credit"])
@pytest.mark.parametrize("bad", [None, True, "0", -1, math.nan, math.inf, -math.inf, 10**1000])
def test_amt_amounts_refuse_unknown_or_invalid(field,bad):
    with pytest.raises(P.ProjectionError):
        price(*amt_case(**{field:bad}))


@pytest.mark.parametrize("change", [{"tax_year":2025},{"tax_year":True},
    {"filing_status":"SINGLE"},{"full_year_inputs_confirmed":False},
    {"full_year_inputs_confirmed":"true"},{"unsupported_items":["depreciation"]},
    {"unsupported_items":None},{"federal_agi_at_review":199999},{"unknown_preference":0}])
def test_amt_scope_and_bound_inputs_refuse(change):
    with pytest.raises(P.ProjectionError):
        price(*amt_case(**change))


def test_negative_form6251_line1_preserves_deduction_shortfall():
    proj, profile, law = amt_case(federal_agi_at_review=10000)
    f = price(dataclasses.replace(proj,wages=10000),profile,law)
    assert f.taxable_income == 0
    assert f.amt_details["line_1_refigure"] == -20000
    assert f.amt_details["amti"] == 160000  # -20K +30K std +150K ISO
    assert f.amt_details["tmt"] == 28800  #26K+28%*10K


def test_ctc_offsets_amt_instead_of_being_discarded_at_regular_floor():
    proj, profile, law = amt_case(federal_agi_at_review=50000)
    profile["dependents"] = [{"ctc_eligible":True},{"ctc_eligible":True}]
    f = price(dataclasses.replace(proj,wages=50000),profile,law)
    # Regular2K, TMT40K, AMT38K, CTC4K. 2+38-4=36K, not38K.
    assert f.amt == 38000 and f.ctc == 4000 and f.total_tax == 36000


def test_credits_are_reflected_without_overstating_current_payment_target():
    proj, profile, law = amt_case(iso_bargain_element=0,
        regular_foreign_tax_credit=500,amt_foreign_tax_credit=500,prior_minimum_tax_credit=1000)
    profile["prior_year"] = {"amt_credit_carryforward":1000}
    law["federal"]["amt"]["exemption"]["mfj"] = 150000
    f = price(proj,profile,law)
    # TMT=26%*(200K-150K)-500=12.5K; regular32K-FTC.5K, AMT0;8801credit1K.
    assert f.amt == 0 and f.minimum_tax_credit == 1000
    assert f.total_tax == 30500


def test_section68_is_reversed_for_amt():
    proj, profile, law = amt_case(salt_deducted=10000,section_68_reduction=2000)
    f = price(dataclasses.replace(proj,itemized_deductions=40000),profile,law)
    assert f.amt_details["amti"] == 318000  #160K line1 +10K SALT +150K ISO -2K section68


def test_only_applied_senior_deduction_is_added_back():
    proj,profile,law=amt_case(senior_deduction=6000)
    f=price(dataclasses.replace(proj,schedule_1a_total=6000),profile,law)
    # Line1=164K; +30K std +150K ISO +6K senior =350K AMTI. Regular30.8K.
    assert f.amt_details["amti"]==350000 and f.amt==51200
    with pytest.raises(P.ProjectionError,match="senior addback"):
        price(*amt_case(senior_deduction=6000))


def test_opening_minimum_tax_credit_must_match_profile():
    proj,profile,law=amt_case()
    profile["prior_year"]={"amt_credit_carryforward":1000}
    with pytest.raises(P.ProjectionError,match="carryforward"):
        price(proj,profile,law)


def test_amt_salt_reconciles_to_repriced_schedule_a():
    proj,profile,law=amt_case(salt_deducted=10000)
    law["federal"].update({k:old._forecast_law()["federal"][k]
                           for k in ("salt_cap","charitable_non_itemizer")})
    inputs=old._schedule_a_forecast(agi=200000,state_local_income_taxes=10000,mortgage_interest=35000)
    proj=dataclasses.replace(proj,schedule_a_inputs=inputs)
    f=price(proj,profile,law)
    assert f.itemized and f.deduction==45000
    assert f.amt_details["amti"]==315000  #200-45+10+150
    bad={**proj.amt_projection,"salt_deducted":9999}
    with pytest.raises(P.ProjectionError,match="reconcile"):
        price(dataclasses.replace(proj,amt_projection=bad),profile,law)


def irmaa_inputs(**changes):
    out = {"tax_year":2026,"filing_status":"MFJ","full_year_inputs_confirmed":True,
        "federal_agi_at_review":200000,"tax_exempt_interest":10000,"excluded_income_addbacks":5000,
        "beneficiaries":2,"lived_apart_all_year":None,"full_year_parts_b_and_d_confirmed":True}
    return {**out,**changes}


def premium_law(year=2028):
    # Synthetic complete five-tier fetched-style table; every threshold is printed, not first dollar.
    return {"tax_year":year,"federal":{"irmaa":{"lookback_years":2,"tiers":{
        "mfj":[{"magi_threshold":t,"annual_surcharge_per_person":s}
               for t,s in [(210000,1000),(260000,2000),(330000,3000),(400000,4000),(750000,5000)]],
        "single":[{"magi_threshold":t,"annual_surcharge_per_person":s}
               for t,s in [(100000,1000),(130000,2000),(160000,3000),(200000,4000),(500000,5000)]],
        "mfs":[{"magi_threshold":100000,"annual_surcharge_per_person":4000},
               {"magi_threshold":400000,"annual_surcharge_per_person":5000}]}}}}


def test_irmaa_is_per_beneficiary_and_uses_income_plus_two_premium_year():
    f=price(*amt_case())
    out=project_irmaa(irmaa_inputs(),f,"MFJ",2026,premium_law())
    assert out["projected_magi"]==215000 and out["premium_year"]==2028
    assert out["annual_surcharge_per_beneficiary"]==1000
    assert out["annual_household_surcharge"]==2000
    assert f.total_tax==82000  #premium never joins federal income tax


def test_unpublished_future_premium_law_is_not_invented():
    f=price(*amt_case())
    out=project_irmaa(irmaa_inputs(),f,"MFJ",2026,None)
    assert not out["modeled"] and out["annual_surcharge_per_beneficiary"] is None
    assert out["premium_year"]==2028 and "unavailable" in out["note"]
    with pytest.raises(P.ProjectionError,match="premium law tax_year"):
        project_irmaa(irmaa_inputs(),f,"MFJ",2026,premium_law(2026))


def test_irmaa_aggregate_overflow_is_refused_even_without_premium_law():
    f=dataclasses.replace(price(*amt_case()),gross_income=1e308,agi=1e308)
    with pytest.raises(P.ProjectionError,match="projected IRMAA MAGI.*finite"):
        project_irmaa(irmaa_inputs(federal_agi_at_review=1e308,tax_exempt_interest=1e308),f,"MFJ",2026,None)


def test_irmaa_household_product_overflow_is_refused():
    law=premium_law()
    for i,tier in enumerate(law["federal"]["irmaa"]["tiers"]["mfj"]):
        tier["annual_surcharge_per_person"]=1e308+i*1e307
    with pytest.raises(P.ProjectionError,match="annual household IRMAA.*finite"):
        project_irmaa(irmaa_inputs(),price(*amt_case()),"MFJ",2026,law)


@pytest.mark.parametrize("change", [{"beneficiaries":True},{"beneficiaries":3},
    {"tax_exempt_interest":None},{"excluded_income_addbacks":math.inf},
    {"full_year_parts_b_and_d_confirmed":False},{"federal_agi_at_review":0},
    {"lived_apart_all_year":False},{"tax_year":2025},{"full_year_inputs_confirmed":"true"}])
def test_irmaa_input_boundary(change):
    with pytest.raises(P.ProjectionError):
        project_irmaa(irmaa_inputs(**change),price(*amt_case()),"MFJ",2026,premium_law())


def test_incomplete_or_legacy_threshold_tables_refuse():
    f=price(*amt_case())
    law=premium_law()
    law["federal"]["irmaa"]["tiers"]["mfj"].pop()
    with pytest.raises(P.ProjectionError,match="complete statutory"):
        project_irmaa(irmaa_inputs(),f,"MFJ",2026,law)
    law=premium_law()
    law["federal"]["irmaa"]["tiers"]["mfj"][0]["magi_threshold"]+=1
    with pytest.raises(P.ProjectionError,match="first-dollar"):
        project_irmaa(irmaa_inputs(),f,"MFJ",2026,law)


def test_empty_table_means_unmodeled():
    law=premium_law()
    law["federal"]["irmaa"]["tiers"]["mfj"]=[]
    out=project_irmaa(irmaa_inputs(),price(*amt_case()),"MFJ",2026,law)
    assert not out["modeled"] and out["annual_household_surcharge"] is None


def test_projection_premium_json_and_markdown_keep_tax_total_separate():
    proj,profile,law=amt_case()
    inputs=old._declared_standard({"tax_year":2026,"ytd":{"wages":200000},
        "amt_projection":proj.amt_projection,"irmaa_projection":irmaa_inputs()})
    report=P.build_report(inputs,profile,law,None,2026,datetime.date(2026,12,31),irmaa_tax_law=premium_law())
    assert report.federal.total_tax==82000 and report.safe_harbor.pct90_current==73800
    assert P._report_to_dict(report)["irmaa"]["annual_household_surcharge"]==2000
    rendered=P.render_markdown(report)
    assert "premium year 2028" in rendered and "per beneficiary: $1,000" in rendered


@pytest.mark.parametrize("fs,apart,expected",[("QSS",None,4000),("HOH",None,4000),
    ("SINGLE",None,4000),("MFS",True,4000),("MFS",False,4000)])
def test_irmaa_statuses_use_their_medicare_not_income_tax_table(fs,apart,expected):
    raw=irmaa_inputs(filing_status=fs,beneficiaries=1,lived_apart_all_year=apart)
    out=project_irmaa(raw,price(*amt_case()),fs,2026,premium_law())
    assert out["annual_surcharge_per_beneficiary"]==expected


def test_mfs_irmaa_requires_lived_apart_answer():
    with pytest.raises(P.ProjectionError,match="lived-apart"):
        project_irmaa(irmaa_inputs(filing_status="MFS",beneficiaries=1),price(*amt_case()),"MFS",2026,premium_law())


def test_mfs_cohabiting_table_cannot_fall_back_to_unmarried():
    f=dataclasses.replace(price(*amt_case()),gross_income=120000,agi=120000)
    raw=irmaa_inputs(filing_status="MFS",federal_agi_at_review=120000,beneficiaries=1,
        lived_apart_all_year=False,tax_exempt_interest=0,excluded_income_addbacks=0)
    together=project_irmaa(raw,f,"MFS",2026,premium_law())
    apart=project_irmaa({**raw,"lived_apart_all_year":True},f,"MFS",2026,premium_law())
    assert together["annual_surcharge_per_beneficiary"]==4000
    assert apart["annual_surcharge_per_beneficiary"]==1000


def test_irmaa_exempt_interest_includes_known_pab_preference():
    f=price(*amt_case(iso_bargain_element=0,private_activity_bond_interest=150000))
    with pytest.raises(P.ProjectionError,match="include the known private-activity-bond"):
        project_irmaa(irmaa_inputs(),f,"MFJ",2026,premium_law())
    raw=irmaa_inputs(tax_exempt_interest=150000,excluded_income_addbacks=0)
    out=project_irmaa(raw,f,"MFJ",2026,premium_law())
    assert out["projected_magi"]==350000 and out["annual_surcharge_per_beneficiary"]==3000


def test_rendered_annual_premiums_reconcile_to_cents():
    proj,profile,law=amt_case();premium=premium_law()
    premium["federal"]["irmaa"]["tiers"]["mfj"][0]["annual_surcharge_per_person"]=1000.4
    inputs=old._declared_standard({"tax_year":2026,"ytd":{"wages":200000},
        "amt_projection":proj.amt_projection,"irmaa_projection":irmaa_inputs()})
    report=P.build_report(inputs,profile,law,None,2026,datetime.date(2026,12,31),irmaa_tax_law=premium)
    section=P.render_markdown(report).split("## Medicare IRMAA premium exposure")[1].split("## Safe harbor analysis")[0]
    assert "per beneficiary: $1,000.40" in section and "household surcharge: $2,000.80" in section


@pytest.mark.parametrize("fs,tmt",[("MFJ",82000),("QSS",82000),("SINGLE",87600),
    ("HOH",87600),("MFS",91400)])
def test_amt_status_mapping_and_mfs_breakpoint(fs,tmt):
    proj,profile,law=amt_case(filing_status=fs)
    profile["filing_status"]=fs
    law["federal"]["filing_statuses"]["hoh"]=law["federal"]["filing_statuses"]["single"]
    law["federal"]["filing_statuses"]["mfs"]=law["federal"]["filing_statuses"]["single"]
    # AMTI350K; exemption50/30/20K; breakpoint100K, halved to50K for MFS.
    f=price(proj,profile,law)
    assert f.amt_details["tmt"]==tmt and f.total_tax==tmt


@pytest.mark.parametrize("year,tmt",[(2025,85500),(2026,89000)])
def test_amt_covered_year_phaseout_changes(year,tmt):
    proj,profile,law=amt_case(tax_year=year)
    law["tax_year"]=profile["tax_year"]=year
    law["federal"]["amt"]["phaseout_start"]["mfj"]=300000
    # AMTI350K, phaseout excess50K:25% in2025,50% in2026. Allowed37.5K/25K.
    f=P.project_federal_tax(proj,profile,law,requested_year=year)
    assert f.amt_details["tmt"]==tmt


def test_pab_preference_adds_amt_without_inventing_iso_credit():
    f=price(*amt_case(iso_bargain_element=0,private_activity_bond_interest=150000))
    assert f.amt==50000 and f.amt_details["form_8801_carryforward"]==0


@pytest.mark.parametrize("field",["exemption","phaseout_start","rate_28_breakpoint"])
@pytest.mark.parametrize("bad",[None,True,0,-1,math.nan,math.inf,"50000"])
def test_missing_or_malformed_indexed_amt_law_refuses(field,bad):
    proj,profile,law=amt_case()
    if field=="rate_28_breakpoint":
        law["federal"]["amt"][field]=bad
    else:
        law["federal"]["amt"][field]["mfj"]=bad
    with pytest.raises(P.ProjectionError):
        price(proj,profile,law)


def test_projection_cli_reads_premium_year_cache_without_mutating_inputs(tmp_path):
    secure_path(tmp_path)
    proj,profile,law=amt_case()
    inputs=old._declared_standard({"tax_year":2026,"as_of_date":"12-31","ytd":{"wages":200000},
        "amt_projection":proj.amt_projection,"irmaa_projection":irmaa_inputs()})
    originals={}
    for name,data in [("projection_inputs.json",inputs),("taxpayer_profile.json",profile),
        ("tax_law_2026.json",law),("tax_law_2028.json",premium_law())]:
        text=json.dumps(data)
        write_secure(tmp_path/name,text)
        originals[name]=text
    result=subprocess.run(["sh","agents/lib/run.sh","-m","agents.lib.projection",
        "--inputs","projection_inputs.json",
        "--workspace",str(tmp_path),"--year","2026","--today","2026-12-31","--json"],
        cwd=old.REPO,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    out=json.loads(result.stdout)
    assert out["federal"]["amt"]==50000
    assert out["irmaa"]["premium_year"]==2028 and out["irmaa"]["annual_household_surcharge"]==2000
    assert {name:(tmp_path/name).read_text() for name in originals}==originals
