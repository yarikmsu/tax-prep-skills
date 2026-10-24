"""AA71: hand-worked TY2025 ScheduleCA line2/6 and SectionB line1."""

import copy
import json

import pytest

from agents.lib import parse_records as P, return_builder as B, tie_out as T, receipt
from agents.lib.secure_fs import write_secure
from agents.lib.tax_computations import compute_ca_schedule_ca
from agents.lib.tax_computations.state.ca import CA_INVESTMENT_COMPONENTS, validate_ca_investments
from tests.test_ca_540_builder import case, law, workspace


def fund(identifier='eligible', **changes):
    row = dict(fund_id=identifier, is_us_ric=True, ordinary_dividends=1000, exempt_interest_dividends=600,
        us_obligation_fraction=.8, ca_exempt_fraction=.75,
        asset_test={'quarterly_fractions': [.5, .7, .6, .9]})
    row.update(changes)
    return row


def investment(**changes):
    boxes = dict(taxable_interest=1200, tax_exempt_interest=1500, ordinary_dividends=3000,
        us_obligation_interest=200, municipal_interest=500, exempt_interest_dividends=1000,
        fund_ordinary_dividends=3000)
    detail = dict(tax_year=2025, reviewed=True,
        municipal_interest=[{'issuer_state':'CA', 'amount':100}, {'issuer_state':'NY', 'amount':400}],
        funds=[fund(), fund('ineligible', ordinary_dividends=2000, exempt_interest_dividends=400,
            us_obligation_fraction=.9, ca_exempt_fraction=1,
            asset_test={'quarterly_fractions':[.9, .9, .49, .9]})])
    row = dict(doc_id='investments', file='documents/investments.pdf', form_type='1099 consolidated',
        payer='Synthetic payer', person='primary', key_boxes=boxes, ca_investments=detail)
    row.update(changes)
    return row


def inputs(record=None, **changes):
    row = investment() if record is None else record
    data = dict(tax_year=2025, investment_records=[{k:row[k] for k in ('key_boxes','ca_investments')}],
        fund_asset_minimum=.5, taxable_social_security=1700, taxable_state_refund=300,
        hsa_employer_addback=10, hsa_deduction_addback=0, hsa_interest_addback=20,
        hsa_dividend_addback=30, taxable_unemployment=0, taxable_ca_family_leave=0,
        ca_family_line_7=0, ca_lottery_winnings=0, other_additions=40, other_subtractions=50)
    data.update(changes)
    return data


def test_printed_schedule_ca_columns_and_totals():
    # Line2B: Treasury200 + eligible fund1000*.8 =1000. The second
    # fund misses a single quarter and contributes no subtraction.
    # Line2C: NY400 + eligible600*.25 + ineligible400 + HSA earnings 20+30 =1000.
    # Additions1000+HSA employer10+other40=1050; subtractions1000+SS1700+refund300+other50=3050.
    out = compute_ca_schedule_ca(**inputs())
    assert out['line_2_column_b'] == 1000
    assert out['line_2_column_c'] == 1000
    assert out['total_additions'] == 1050 and out['total_subtractions'] == 3050
    assert [r['qualified'] for r in out['investment_workpaper']] == [True, False]


def test_asset_threshold_is_fetched_and_each_quarter_matters():
    assert compute_ca_schedule_ca(**inputs(fund_asset_minimum=.6))['us_obligation_fund_subtraction'] == 0
    row = investment()
    row['ca_investments']['funds'][0]['asset_test'] = {'provider_qualified':True, 'threshold':.5}
    assert compute_ca_schedule_ca(**inputs(row))['us_obligation_fund_subtraction'] == 800
    with pytest.raises(ValueError, match='certification'):
        compute_ca_schedule_ca(**inputs(row, fund_asset_minimum=.6))


def test_non_ric_fund_gets_no_passthrough_exemption():
    row = investment()
    row['ca_investments']['funds'][0].update(is_us_ric=False, asset_test=None)
    out = compute_ca_schedule_ca(**inputs(row))
    assert out['us_obligation_fund_subtraction'] == 0
    assert out['exempt_interest_dividend_addition'] == 1000


@pytest.mark.parametrize('bad', [None, 0, 1, 'true'])
def test_unknown_or_type_coerced_ric_status_refuses(bad):
    row = investment()
    row['ca_investments']['funds'][0]['is_us_ric'] = bad
    with pytest.raises(T.TieOutError, match='is_us_ric'):
        T.load_ledger({'records':[row]})


@pytest.mark.parametrize('bad', [None, True, -1, float('nan'), float('inf'), '0.5', 2, 10**400])
def test_unfetched_or_malformed_fund_threshold_refuses(bad):
    with pytest.raises(ValueError):
        compute_ca_schedule_ca(**inputs(fund_asset_minimum=bad))


@pytest.mark.parametrize('field', ['ordinary_dividends','exempt_interest_dividends',
    'us_obligation_fraction','ca_exempt_fraction'])
@pytest.mark.parametrize('bad', [True, -1, None, float('nan'), float('inf'), '1', 10**400])
def test_bad_fund_facts_cannot_hide_behind_positive_aggregates(field, bad):
    row = investment()
    row['ca_investments']['funds'][0][field] = bad
    with pytest.raises(T.TieOutError):
        T.load_ledger({'records':[row]})


@pytest.mark.parametrize('bad', [None, [], [.5]*3, [.5]*5, [True]*4, [.5,.5,float('nan'),.5]])
def test_every_quarter_must_be_known(bad):
    row = investment()
    row['ca_investments']['funds'][0]['asset_test'] = {'quarterly_fractions':bad}
    with pytest.raises(T.TieOutError):
        T.load_ledger({'records':[row]})


def test_per_document_components_reconcile_and_duplicate_funds_refuse():
    for field in CA_INVESTMENT_COMPONENTS:
        row = investment()
        row['key_boxes'][field] += 2000
        with pytest.raises(T.TieOutError):
            T.load_ledger({'records':[row]})
    row = investment()
    row['ca_investments']['funds'][1]['fund_id'] = 'eligible'
    with pytest.raises(T.TieOutError, match='unique'):
        T.load_ledger({'records':[row]})


@pytest.mark.parametrize('component,inclusive', [('municipal_interest','tax_exempt_interest'),
    ('exempt_interest_dividends','tax_exempt_interest'),('fund_ordinary_dividends','ordinary_dividends')])
def test_missing_detail_cannot_hide_in_balanced_federal_totals(component, inclusive):
    row = investment()
    row['key_boxes'][component] += 100
    row['key_boxes'][inclusive] += 100
    # Aggregate bounds still pass: the missing100 needs an issuer/fund row.
    with pytest.raises(T.TieOutError, match='reconcile'):
        T.load_ledger({'records':[row]})


def test_special_issuer_exemption_is_a_confirmed_source_fact():
    row = investment()
    muni = row['ca_investments']['municipal_interest'][0]
    muni['issuer_state'] = 'PR'
    with pytest.raises(ValueError, match='DC/territories'):
        compute_ca_schedule_ca(**inputs(row))
    muni.update(ca_exempt=True, exemption_confirmed=True)
    assert compute_ca_schedule_ca(**inputs(row))['nonca_municipal_interest_addition'] == 400
    muni['ca_exempt'] = False
    assert compute_ca_schedule_ca(**inputs(row))['nonca_municipal_interest_addition'] == 500


def test_year_and_source_completeness_cannot_be_inferred():
    row = investment()
    del row['key_boxes']['us_obligation_interest']
    with pytest.raises(ValueError):
        validate_ca_investments(row['ca_investments'], row['key_boxes'])
    row = investment()
    row['ca_investments']['tax_year'] = 2024
    with pytest.raises(ValueError, match='source year'):
        compute_ca_schedule_ca(**inputs(row))
    with pytest.raises(ValueError, match='reviewed'):
        compute_ca_schedule_ca(**inputs(tax_year=2026))


def prepared():
    p, result = case()
    p['flags'].update(ca_has_us_obligation_income=True, ca_has_nonca_muni_interest=True,
                     ca_has_other_schedule_ca_adjustments=True, has_1099g_state_refund=True)
    result['form_1040'].update(line_6b_taxable_ss=1700, taxable_state_refund=300,
                              taxable_state_refund_worksheet_confirmed=True)
    result['california_540']['schedule_ca_adjustments'].update(
        hsa_employer_addback=10,hsa_interest_addback=20,hsa_dividend_addback=30,
        other_additions=40,other_subtractions=50,other_adjustments_confirmed=True)
    ledger = T.load_ledger({'records':[investment()]})
    return p, result, ledger


def test_the_federal_hsa_deduction_is_added_back_from_form_8889():
    # Schedule CA Section C line 13: CA does not conform, so Form 8889 line 13's
    # 4,000 deduction goes to column B in full; 1050 + 4000 = 5050 additions.
    p, result, ledger = prepared()
    result['form_8889_hsa'] = {'line_9_employer_contributions': 10, 'line_13_hsa_deduction': 4000}
    built = B.build_california(result, {'state': law()}, ledger, p)
    assert built['line_16'] == 5050
    # Line 9 is the W-2 box 12 code W amount the employer add-back restates.
    result['form_8889_hsa']['line_9_employer_contributions'] = 3000
    with pytest.raises(B.Blocked, match='line 9'):
        B.build_california(result, {'state': law()}, ledger, p)


def test_builder_derives_common_adjustments_and_checks_the_workpaper():
    p, result, ledger = prepared()
    built = B.build_california(result, {'state':law()}, ledger, p)
    # Form540: AGI100k +1050 -3050 =98k; less10k deduction gives88k;
    # synthetic5% tax4400, exemption60 ->4340.
    assert built['line_14'] == 3050 and built['line_16'] == 1050
    assert built['line_17'] == 98000 and built['line_64'] == 4340
    result['california_540'].update(built)
    result['meta']['limitations'] = [built['line_113_note']]
    assert B.california_differences(result, built) == []
    assert not T._ca_schedule_checks(ledger, result, p, {'state':law()})
    result['california_540']['schedule_ca_adjustments']['investment_workpaper'][0]['qualified'] = 1
    # A mutable result cannot mutate the expected workpaper in this assertion.
    fresh = B.build_california(result, {'state':law()}, ledger, p)
    assert 'schedule_ca_adjustments' in B.california_differences(result, fresh)
    assert T._ca_schedule_checks(ledger, result, p, {'state':law()})


def test_legacy_totals_missing_qualification_and_wrong_screens_block():
    p, result, ledger = prepared()
    raw = investment()
    del raw['ca_investments']
    for screened in (False, True):
        legacy_profile = copy.deepcopy(p)
        legacy_profile['flags'].update(ca_has_us_obligation_income=screened, ca_has_nonca_muni_interest=screened)
        with pytest.raises(B.Blocked, match='legacy'):
            B.build_california(result, {'state':law()}, T.load_ledger({'records':[raw]}), legacy_profile)
    for flag in ('ca_has_us_obligation_income','ca_has_nonca_muni_interest'):
        bad = copy.deepcopy(p)
        bad['flags'][flag] = False
        with pytest.raises(B.Blocked, match=flag):
            B.build_california(result, {'state':law()}, ledger, bad)
    result['form_1040']['taxable_state_refund_worksheet_confirmed'] = False
    with pytest.raises(B.Blocked, match='tax-benefit'):
        B.build_california(result, {'state':law()}, ledger, p)


def test_social_security_and_refund_use_only_the_federally_taxable_amounts():
    p, result, ledger = prepared()
    refund = dict(doc_id='refund',file='documents/refund.pdf',payer='Synthetic state',
                  form_type='1099-G',person='primary',key_boxes={'state_refund_1099g':1000})
    refund['key_boxes'].update(unemployment_compensation=0,government_family_leave=0)
    refund['government_benefits']={'tax_year':2025,'form_revision':'03/2024','box_1':0,'box_10':None,
        'unemployment_repaid':0,'unemployment_contribution_reduction':0,'reviewed':True,'family_programs':[]}
    result['form_1040']['line_8_schedule1_other']=300
    ledger += T.load_ledger({'records':[refund]})
    # Gross refund1000 is not the confirmed federal inclusion300. SS's
    # subtraction is federal6b1700, never a source gross-benefit amount.
    built = B.build_california(result, {'state':law()}, ledger, p)
    assert built['schedule_ca_adjustments']['taxable_state_refund_subtraction'] == 300
    assert built['schedule_ca_adjustments']['taxable_social_security_subtraction'] == 1700
    result['form_1040']['taxable_state_refund'] = 0
    assert B.build_california(result,{'state':law()},ledger,p)['schedule_ca_adjustments']['taxable_state_refund_subtraction'] == 0
    result['form_1040']['taxable_state_refund'] = 1001
    with pytest.raises(B.Blocked,match='exceeds'):
        B.build_california(result,{'state':law()},ledger,p)


def test_other_worksheets_need_explicit_confirmation_and_cannot_reuse_old_totals():
    p, result, ledger = prepared()
    result['california_540']['schedule_ca_adjustments']['other_adjustments_confirmed'] = False
    with pytest.raises(B.Blocked,match='separately worked'):
        B.build_california(result,{'state':law()},ledger,p)
    del result['california_540']['schedule_ca_adjustments']['other_subtractions']
    with pytest.raises(B.Blocked,match='other CA subtractions'):
        B.build_california(result,{'state':law()},ledger,p)


def test_provider_and_special_issuer_source_disclosures_are_checked_and_retired():
    p, result, _ = prepared()
    row = investment()
    row['ca_investments']['funds'][0]['asset_test'] = {'provider_qualified':True,'threshold':.5}
    ledger = T.load_ledger({'records':[row]})
    built = B.build_california(result,{'state':law()},ledger,p)
    result['california_540'].update(copy.deepcopy(built))
    result['meta']['limitations'] = [built['line_113_note']]
    assert 'meta.limitations (CA investment worksheets)' in B.california_differences(result,built)
    note = built['schedule_ca_adjustments']['source_note']
    result['meta']['limitations'].append(note)
    assert not B.california_differences(result,built)
    result['meta']['limitations'].append('CA investment worksheets: stale qualification')
    assert 'meta.limitations (stale CA investment disclosure)' in B.california_differences(result,built)


def test_no_ca_return_does_not_claim_an_investment_adjustment_and_wrong_law_refuses():
    p, result, ledger = prepared()
    p['address']['state'] = 'NY'
    assert not T._ca_schedule_checks(ledger,result,p,{'state':{'code':'NY'}})
    p['address']['state'] = 'CA'
    with pytest.raises(B.Blocked,match='California law'):
        B.build_california(result,{'state':{'code':'NY'}},ledger,p)
    assert T._ca_schedule_checks(ledger,result,p,{'state':{'code':'NY'}})


@pytest.mark.parametrize('state', ['CA','ca',' CA '])
def test_standalone_state_selection_uses_the_existing_normalized_profile_codes(state):
    p, result, ledger = prepared()
    p['address']['state'] = state
    assert T._ca_schedule_checks(ledger,result,p,{'state':{'code':'NY'}})


def test_profile_only_year_is_supported_but_type_invalid_years_cannot_match_by_equality():
    p, result, ledger = prepared()
    del result['meta']['tax_year']
    assert B.build_california(result,{'state':law()},ledger,p)['computed_tax_year'] == 2025
    result['meta']['tax_year'] = 2025
    p['tax_year'] = 2025.0
    with pytest.raises(B.Blocked,match='year'):
        B.build_california(result,{'state':law()},ledger,p)


def test_tie_out_detects_dropped_adjustments_without_relying_on_a_builder_pass():
    p, result, ledger = prepared()
    report = T.evaluate(ledger,result,['documents/investments.pdf'],profile=p,tax_law={'state':law()})
    assert report.failed
    assert any('Schedule CA' in s for s in report.missing_result_targets)


def test_parse_records_preserves_the_detail_and_federal_totals_stay_inclusive(tmp_path):
    ws = workspace(tmp_path)
    row = investment()
    write_secure(ws/row['file'], b'%PDF synthetic investment document')
    P.write_record(ws, row)
    P.write_record(ws, json.loads((ws/'parse_ledger.json').read_text())['records'][0])
    raw = P.build_ledger(ws)
    assert next(r for r in raw['records'] if r['doc_id']=='investments')['ca_investments'] == row['ca_investments']
    write_secure(ws/'parse_ledger.json', json.dumps(raw))
    p = json.loads((ws/'taxpayer_profile.json').read_text())
    p['flags'].update(ca_has_us_obligation_income=True, ca_has_nonca_muni_interest=True)
    write_secure(ws/'taxpayer_profile.json', json.dumps(p))
    assert B._main(['--workspace',str(ws)]) == 0
    out = json.loads((ws/'result.json').read_text())
    # US200 stays INCLUDED in federal1200; DIV12 adds1000 to INT8's500.
    assert out['form_1040']['line_2b_taxable_interest'] == 1200
    assert out['form_1040']['line_2a_tax_exempt_interest'] == 1500
    assert out['form_1040']['line_3b_ordinary_dividends'] == 3000
    assert B._main(['--workspace',str(ws),'--check']) == 0
    assert receipt.verify(ws,required=(B.GATE,)).valid
    next(r for r in raw['records'] if r['doc_id']=='investments')['ca_investments']['funds'][0]['asset_test']['quarterly_fractions'][0] = .49
    write_secure(ws/'parse_ledger.json',json.dumps(raw))
    assert not receipt.verify(ws,required=(B.GATE,)).valid
