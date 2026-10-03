"""RCW 82.87 and DOR TY2025 worksheets, hand computed; no live test fetches."""
import copy
import json
import re
import subprocess
from pathlib import Path

import pytest

from agents.lib import tax_law_fetcher as fetcher, tax_calendar, filing_package
from agents.lib.tax_computations import compute_wa_capital_gains, state_brackets_for_status

ROOT = Path(__file__).resolve().parents[1]


def config():
    # DOR's TY2025 deductions and June 30, 2025 tier notice, live-read 2026-10-03.
    c = fetcher.create_state_template("WA", 2025)
    c["wa_capital_gains"] = dict(tax_year=2025, standard_deduction=278000,
        charitable_minimum=278000, charitable_cap=111000,
        rates=[dict(min=0, max=1000000, rate=.07), dict(min=1000000, max=None, rate=.099)])
    return c


def inputs():
    return dict(federal_lt_carryforward_used=0, excluded_lt_carryforward=0,
        adjustments=[], classification_complete=True, full_year_wa_domicile=True,
        registered_domestic_partner=False, special_federal_gain_rules=False, household_is_separate=False,
        qualifying_business_deduction=0, business_eligibility_confirmed=False,
        qualifying_charitable_contributions=0, charity_eligibility_confirmed=False,
        other_jurisdiction_credits=[], credits_verified=False,
        b_o_tax_paid_on_same_gains=0, b_o_eligibility_confirmed=False,
        payments=0, payments_confirmed=True, extension_requested=False, payment_made=False)


def compute(gain=500000, *, cfg=None, status="Single", **kw):
    return compute_wa_capital_gains(2025, status, gain, cfg or config(), **(inputs() | kw))


@pytest.mark.parametrize("gain,tax", [(0, 0), (278000, 0), (278001, .07),
    (500000,15540), (1278000,70000), (1278001,70000.10), (1500000,91978)])
def test_dor_worksheet(gain, tax):
    # LT minus 278k. First million × .07; excess × .099, rounded half-up.
    assert compute(gain)["total_tax"] == tax


def test_short_term_loss_is_not_an_input_or_a_tax_offset():
    assert compute()["total_tax"] == 15540
    with pytest.raises(TypeError):
        compute(short_term_loss=500000)


def test_remove_gains_and_losses_and_excluded_carryforwards():
    # 500k - 100k exempt gain + 50k nonallocated loss + 20k old CF = 470k.
    r=compute(adjustments=[dict(id="real-estate", reason="exempt", gain_loss=100000),
        dict(id="outside-wa", reason="nonallocated", gain_loss=-50000)],
        federal_lt_carryforward_used=30000, excluded_lt_carryforward=20000)
    assert r["adjusted_wa_gain"] == 470000
    assert r["total_tax"] == 13440  # (470k - 278k) × .07


def test_negative_federal_lt_can_still_produce_wa_tax():
    # Remove a 500k exempt loss from -100k federal LT => 400k WA LT.
    assert compute(-100000,adjustments=[dict(id="exempt-loss",reason="exempt",gain_loss=-500000)])["total_tax"] == 8540


def test_mfj_deduction_is_not_doubled():
    assert compute(status="MFJ")["total_tax"] == 15540


def test_every_annual_figure_is_a_parameter():
    c=config();c["wa_capital_gains"]["standard_deduction"]=300000
    c["wa_capital_gains"]["rates"][0]["rate"]=.08
    assert compute(cfg=c)["total_tax"] == 16000  # (500k - 300k) × .08
    with pytest.raises(ValueError):
        compute(status="unknown")
    with pytest.raises(ValueError):
        compute(household_is_separate=True)


@pytest.mark.parametrize("kw", [dict(status="MFS"), dict(registered_domestic_partner=True),
    dict(special_federal_gain_rules=True), dict(full_year_wa_domicile=False),
    dict(classification_complete=False), dict(payments_confirmed=False),
    dict(excluded_lt_carryforward=1), dict(qualifying_business_deduction=100),
    dict(qualifying_charitable_contributions=300000),
    dict(b_o_tax_paid_on_same_gains=10), dict(payments=10),
    dict(adjustments=[dict(id="same",reason="exempt",gain_loss=1)]*2)])
def test_unverified_or_unsupported_inputs_refuse(kw):
    with pytest.raises(ValueError):
        compute(**kw)


@pytest.mark.parametrize("bad", [None, True, -1, float("nan"), float("inf"), "0"])
def test_invalid_money_refuses(bad):
    with pytest.raises(ValueError):
        compute(payments=bad)


def test_business_charity_and_nonrefundable_credit_worksheets():
    # .070 business 10k; .080 qualified gifts 500k => min(222k,111k)=111k.
    # Tax (500k-278k-10k-111k)*.07 = 7070. Credit min(4k,3k)+2k = 5k.
    r=compute(qualifying_business_deduction=10000,business_eligibility_confirmed=True,
        qualifying_charitable_contributions=500000,charity_eligibility_confirmed=True,
        other_jurisdiction_credits=[dict(jurisdiction="NY",tax_paid=4000,wa_tax_on_same_gains=3000)],
        b_o_tax_paid_on_same_gains=2000,b_o_eligibility_confirmed=True,credits_verified=True)
    assert r["charitable_deduction"] == 111000
    assert r["tax_before_credits"] == 7070
    assert r["total_tax"] == 2070
    assert compute(b_o_tax_paid_on_same_gains=20000,b_o_eligibility_confirmed=True,credits_verified=True)["total_tax"] == 0


def test_zero_due_still_requires_filing_after_extension_or_payment():
    assert not compute(0)["return_required"]
    assert compute(0,extension_requested=True)["return_required"]
    r=compute(0,payments=100,payment_made=True)
    assert r["return_required"] and r["refund"] == 100
    assert r["computed_tax_year"] == 2025


def test_cache_year_and_corrupt_wage_brackets_refuse():
    c=config();c["wa_capital_gains"]["tax_year"]=2024
    with pytest.raises(ValueError,match="year"):
        compute(cfg=c)
    c=config();c["brackets"]=c["wa_capital_gains"]["rates"]
    assert state_brackets_for_status(c,"Single") == []
    with pytest.raises(ValueError,match="EMPTY"):
        compute(cfg=c)
    with pytest.raises(ValueError,match="TY2025"):
        compute_wa_capital_gains(2026,"Single",0,config(),**inputs())


def test_freshness_understands_the_excise_shape(tmp_path):
    d=fetcher.create_empty_tax_law(2025);d["state"]=config()
    fetcher.save_tax_law(2025,d,tmp_path)
    check=fetcher.check_freshness(2025,tmp_path)
    assert not [p for p in check["null_fields"] if p.startswith("state.")]
    d["state"]["type"]="none";fetcher.save_tax_law(2025,d,tmp_path)
    assert any("state.type" in p for p in fetcher.check_freshness(2025,tmp_path)["null_fields"])


def test_cache_setter_binds_complete_parameters_to_year(tmp_path):
    d=fetcher.create_empty_tax_law(2025);d["state"]=fetcher.create_state_template("WA")
    fetcher.save_tax_law(2025,d,tmp_path)
    # Source period applies to this tax year, not the page retrieval year.
    kw=dict(source="https://dor.wa.gov/taxes-rates/other-taxes/capital-gains-tax", applies_to=2025)
    fetcher.set_figure(2025,"state.wa_capital_gains",copy.deepcopy(config()["wa_capital_gains"]),workspace=tmp_path,**kw)
    assert fetcher.load_tax_law(2025,tmp_path)["state"]["wa_capital_gains"]["tax_year"] == 2025
    with pytest.raises(fetcher.TaxLawSetError):
        fetcher.set_figure(2025,"state.wa_capital_gains.tax_year",2025,workspace=tmp_path,**kw)


def test_calendar_and_package_are_separate_from_wage_tax():
    events=tax_calendar.build_events(dict(address=dict(state="WA"),flags={}),2025)
    wa=[e for e in events if "WA capital-gains" in e.summary]
    assert len(wa) == 1 and str(wa[0].date) == "2026-04-15"
    assert not any(e.category == "state_estimated_tax" for e in events)
    assert "My DOR" in tax_calendar.events_extension(2025,state="WA")[0].description
    result=dict(washington_ltcg_excise=compute())
    assert any("My DOR" in x for x in filing_package.attachment_checklist(result))


def test_documented_launcher_executes(tmp_path):
    from agents.lib.secure_fs import write_secure, secure_path
    secure_path(tmp_path)
    p=dict(tax_year=2025,filing_status="Single",address=dict(state="WA"),washington_inputs=inputs())
    r=dict(meta=dict(tax_year=2025,filing_status="Single",limitations=[]),schedule_d=dict(
        part_ii_long_term=dict(line_14_prior_year_carryover=0,line_15_net_long_term=500000)))
    for name,data in [("taxpayer_profile.json",p),("result.json",r),("tax_law_2025.json",dict(tax_year=2025,state=config()))]:
        write_secure(tmp_path/name,json.dumps(data))
    import os
    script=re.search(r"sh agents/lib/run.sh -c '(.*?)'\n",(ROOT/".agents/skills/tax-compute/references/state-return-wa.md").read_text(),re.S)[1]
    proc=subprocess.run(["sh","agents/lib/run.sh","-c",script],cwd=ROOT,
        env=os.environ|dict(TAX_WORKSPACE=str(tmp_path)),capture_output=True,text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((tmp_path/"result.json").read_text())["washington_ltcg_excise"]["total_tax"] == 15540


def harvest_case(gain=500000, loss=10000, *, short_loss=0, taxable=True):
    import datetime
    from agents.lib import loss_harvest as lh
    fix = ROOT / 'tests/fixtures/harvest'
    r = json.loads((fix / 'result.json').read_text())
    p = json.loads((fix / 'taxpayer_profile.json').read_text())
    law = json.loads((fix / 'tax_law_2026.json').read_text())
    r['meta']['tax_year'] = 2025
    law['tax_year'] = 2025
    law['state'] = config()
    p.update(address=dict(state='WA'), filing_status='MFJ', washington_inputs=inputs())
    r['form_1040']['line_7_capital_gain'] = gain
    r['schedule_d']['part_i_short_term']['line_7_net_short_term'] = 0
    r['schedule_d']['part_ii_long_term']['line_15_net_long_term'] = gain
    r['schedule_d']['line_16_combined'] = gain
    r['washington_ltcg_excise'] = compute(gain, status='MFJ')
    h = dict(as_of='2025-12-15', lots=[dict(id='LT', ticker='ABC', account='Taxable',
        acquired='2023-01-01', quantity=100, cost_basis=loss+100, current_value=100)],
        wa_harvest=dict(deductions_unchanged=True, taxable_lots=dict(LT=taxable)))
    if short_loss:
        h['lots'].append(dict(id='ST', ticker='DEF', account='Taxable', acquired='2025-06-01',
                             quantity=100, cost_basis=short_loss+100, current_value=100))
    def build():
        return lh.build_harvest(r, p, law, h, 2025, name='wa', today=datetime.date(2025,12,15))
    return build, r, p, law, h


@pytest.mark.parametrize('gain,loss,expected', [
    (500000,10000,700),  # 10k × .07, independent of federal netting.
    (277000,10000,0), (280000,10000,140),  # Only 2k above deduction.
    (1283000,10000,845),  # 5k × .099 + 5k × .07 across second band.
])
def test_wa_harvest_exact_excise_difference(gain, loss, expected):
    from agents.lib import loss_harvest as lh
    build, r, p, law, h = harvest_case(gain, loss)
    original = copy.deepcopy((r,p,law,h))
    report = build()
    assert report.state_savings == expected
    assert report.state_modeled and report.state_mtr is None
    assert not report.state_excise_unpriced
    assert 'ST losses save no WA tax' in lh.render_markdown(report)
    assert lh._report_to_dict(report)['state_excise_note']
    assert (r,p,law,h) == original


def test_wa_harvest_st_exempt_and_allowed_wash_loss():
    build, _, _, _, h = harvest_case(short_loss=100000)
    assert build().state_savings == 700  # ST loss does not change WA LT base.
    h['wa_harvest']['taxable_lots']['LT'] = False
    assert build().state_savings == 0  # Exempt/nonallocated loss removed.
    h['wa_harvest']['taxable_lots']['LT'] = True
    h['recent_transactions'] = [dict(ticker='ABC', account='Other',
        date='2025-12-01', kind='buy', quantity=50)]
    assert build().state_savings == 350  # 50/100 allowed shares × 10k × .07.


def test_wa_harvest_combined_loss_not_sum_of_independent_cliff_estimates():
    build, _, _, _, h = harvest_case(280000, 1500)
    h['lots'].append(dict(h['lots'][0], id='LT2', ticker='XYZ'))
    h['wa_harvest']['taxable_lots']['LT2'] = True
    report = build()
    assert report.state_savings == 140  # Combined 3k loss uses only 2k taxable base.
    assert report.lt_loss_harvested == 3000


@pytest.mark.parametrize('problem', ['missing', 'classification', 'deduction', 'credit',
                                   'baseline', 'year', 'scope', 'anniversary'])
def test_wa_harvest_missing_or_unverified_inputs_visibly_degrade(problem):
    from agents.lib import loss_harvest as lh
    build, r, p, law, h = harvest_case()
    if problem == 'missing':
        del p['washington_inputs']
    elif problem == 'classification':
        h['wa_harvest']['taxable_lots']['LT'] = 'true'
    elif problem == 'deduction':
        h['wa_harvest']['deductions_unchanged'] = False
    elif problem == 'credit':
        p['washington_inputs']['other_jurisdiction_credits'] = [dict(
            jurisdiction='OR',tax_paid=100,wa_tax_on_same_gains=100)]
    elif problem == 'baseline':
        r['washington_ltcg_excise']['total_tax'] += 1
    elif problem == 'year':
        law['state']['wa_capital_gains']['tax_year'] = 2024
    elif problem == 'scope':
        p['washington_inputs']['full_year_wa_domicile'] = False
    elif problem == 'anniversary':
        h['lots'][0]['acquired'] = '2024-01-15'
        h['as_of'] = '2025-01-15'  # 366 days but exactly one year: still ST.
    report = build()
    assert report.state_savings == 0 and report.state_excise_unpriced
    assert not report.state_modeled
    assert report.state_excise_note in lh.render_markdown(report)


def test_crypto_wa_domicile_workpaper_includes_signed_lt_only():
    from agents.lib import crypto_importer as ci
    # LT capital gain and loss already federally included; ST and staking excluded.
    rows = dict(form_8949=dict(part_ii_long_term=[
        dict(date_sold='2025-03-01',gain_loss=20000),
        dict(date_sold='2025-11-01',gain_loss=-5000)],
        part_i_short_term=[dict(date_sold='2025-03-01',gain_loss=99999)]),
        ordinary_income=dict(total=10000))
    out = ci.washington_crypto_allocation(rows, {'2025-03-01':'WA','2025-11-01':'TX'})
    assert out['allocated_lt_gain_loss'] == 20000
    assert out['adjustments'] == [dict(id='crypto-lt-1',reason='nonallocated',gain_loss=-5000)]
    # Federal LT 15k minus removed -5k = WA 20k (not federal combined + staking).
    assert 15000 - out['adjustments'][0]['gain_loss'] == 20000
    for bad in (None, {}, {'2025-03-01':'WA','2025-11-01':'ZZ'},
                {'2025-03-01':True,'2025-11-01':'WA'}):
        with pytest.raises(ci.CryptoImportError):
            ci.washington_crypto_allocation(rows, bad)


def test_wftc_is_separate_application_in_computed_notes():
    notes = ' '.join(compute(0)['notes'])
    assert 'workingfamiliescredit.wa.gov/apply' in notes
    assert 'ITIN' in notes and 'separate application' in notes
    assert 'No WFTC credit' in notes
