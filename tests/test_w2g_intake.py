"""AA66: winnings reach income; W-2G box4 reaches OTHER-FORMS withholding."""

import pytest

from agents.lib.tax_computations.federal.gambling_income import (
    classify_w2g_income, compute_w2g_totals, compute_other_forms_withholding,
    is_w2g_form, validate_w2g_income,
)


def source(year=2025, **changes):
    raw = dict(tax_year=year,form_revision='12/2023' if year==2025 else '01/2026',
        reviewed=True,ownership_confirmed=True,box_1=1200,box_4=200,box_7=0,
        identical_wagers_reviewed=False,schedule_1_income=1200,schedule_c_gross=0,
        income_method='reported',worksheet_confirmed=False,worksheet_id=None,
        other_business_gross=0,ca_lottery_income=0)
    raw.update(changes)
    return raw


def record(raw):
    return {'key_boxes':classify_w2g_income(raw),'w2g_income':raw}


@pytest.mark.parametrize('year',[2025,2026])
def test_source_gross_casual_business_and_other_forms_withholding_stay_separate(year):
    raw=source(year,schedule_1_income=700,schedule_c_gross=500)
    assert classify_w2g_income(raw)=={'gambling_winnings':1200,'federal_withholding_w2g':200,
                                    'self_employment_gross':500}
    totals=compute_w2g_totals(tax_year=year,records=[record(raw)])
    assert totals['gambling_income']==700 and totals['gambling_business_gross']==500
    # Existing other-form/Medicare withholding300 + W-2G200 =line25c500;
    # neither credit belongs to 1099 withholding or reduces winnings.
    out=compute_other_forms_withholding(tax_year=year,records=[record(raw)],
        remaining_withholding=300,remaining_withholding_confirmed=True)
    assert out['total']==500 and out['w2g_withholding']==200


@pytest.mark.parametrize('value',['W-2G','Form W2G','W-2G (corrected)',
    'Consolidated return (1099-MISC / W-2G)','Form W‐2G'])
def test_w2g_detection_handles_real_labels(value):
    assert is_w2g_form(value)


@pytest.mark.parametrize('value',['W-2','W-2GU','W2GU (Guam)','Form W-2GS','AW2G',None])
def test_guam_wage_forms_are_not_gambling(value):
    assert not is_w2g_form(value)


@pytest.mark.parametrize('bad',[None,True,-1,float('nan'),float('inf'),'200',10**400])
def test_bad_money_cannot_become_a_credit(bad):
    with pytest.raises(ValueError,match='finite'):
        classify_w2g_income(source(box_4=bad))


def test_identical_wager_winnings_are_additional_to_box_1():
    # W-2G instructions, box 7: "the amount of additional winnings from
    # identical wagers", so 1,200 + 500 = 1,700 is reported and taxable.
    with pytest.raises(ValueError,match='identical'):
        classify_w2g_income(source(box_7=500))
    with pytest.raises(ValueError,match='box1 plus box7'):
        classify_w2g_income(source(box_7=500,identical_wagers_reviewed=True))
    raw=source(box_7=500,identical_wagers_reviewed=True,schedule_1_income=1700)
    assert classify_w2g_income(raw)['gambling_winnings']==1700
    assert compute_w2g_totals(tax_year=2025,records=[record(raw)])['gambling_income']==1700


def test_a_2025_w2g_may_use_the_continuous_use_2026_revision():
    assert classify_w2g_income(source(form_revision='01/2026'))['gambling_winnings']==1200
    with pytest.raises(ValueError,match='revision'):
        classify_w2g_income(source(2026,form_revision='12/2023'))


def test_california_lottery_income_is_bounded_by_the_federal_inclusion():
    raw=source(ca_lottery_income=1200)
    assert compute_w2g_totals(tax_year=2025,records=[record(raw)])['ca_lottery_income']==1200
    with pytest.raises(ValueError,match='California lottery'):
        classify_w2g_income(source(ca_lottery_income=1300))


def test_reviewed_source_worksheet_does_not_force_raw_reported_winnings_into_income():
    raw=source(income_method='reviewed_worksheet',schedule_1_income=400,
        worksheet_id='session_alpha',worksheet_confirmed=True)
    totals=compute_w2g_totals(tax_year=2025,records=[record(raw)])
    assert totals['gambling_winnings']==1200 and totals['gambling_income']==400
    assert totals['worksheet_used'] is True
    raw['worksheet_confirmed']=False
    with pytest.raises(ValueError,match='worksheet'):
        classify_w2g_income(raw)


def test_reported_income_needs_a_reviewed_split_and_proper_ownership():
    with pytest.raises(ValueError,match='reconcile'):
        classify_w2g_income(source(schedule_1_income=100))
    with pytest.raises(ValueError,match='ownership'):
        classify_w2g_income(source(ownership_confirmed=False))
    with pytest.raises(ValueError,match='year'):
        classify_w2g_income(source(2024))


def test_source_amount_cannot_be_removed_by_rerouting_it_to_1099_withholding():
    raw=source()
    boxes=classify_w2g_income(raw)
    boxes['federal_withholding_1099']=boxes.pop('federal_withholding_w2g')
    with pytest.raises(ValueError,match='disagree'):
        validate_w2g_income(raw,boxes)
    with pytest.raises(ValueError,match='EXCLUDING'):
        compute_other_forms_withholding(tax_year=2025,records=[record(raw)],
            remaining_withholding=9999,remaining_withholding_confirmed=False)


def w2g_workspace(tmp_path, *, context='none', remaining=-200, raw=None):
    import json
    from agents.lib import parse_records as P
    from agents.lib.secure_fs import write_secure
    from tests.test_ca_540_builder import workspace
    from tests.test_government_benefits import government_workspace
    from tests.test_misc_income import misc_workspace
    if context in ('misc','both'):
        ws=misc_workspace(tmp_path,benefits=context=='both')
    else:
        ws=government_workspace(tmp_path) if context=='benefits' else workspace(tmp_path)
    raw=source() if raw is None else raw
    row={'doc_id':'w2g','file':'documents/w2g.pdf','form_type':'W-2G','payer':'Synthetic payer',
        'person':'primary','key_boxes':dict(classify_w2g_income(raw),ca_1099_withholding=50),'w2g_income':raw}
    write_secure(ws/row['file'],b'%PDF synthetic W-2G source')
    ledger=json.loads((ws/'parse_ledger.json').read_text())
    ledger['records'].append(row)
    for rec in ledger['records']:
        P.write_record(ws,rec)
    built=P.build_ledger(ws)
    assert next(r for r in built['records'] if r['doc_id']=='w2g')['w2g_income']==raw
    write_secure(ws/'parse_ledger.json',json.dumps(built))
    result=json.loads((ws/'result.json').read_text())
    result['schedule_1_other_income']={'remaining_income':remaining,'remaining_income_confirmed':True,
                                     'w2g_excluded_from_remaining':True}
    result['other_forms_withholding']={'remaining_withholding':300,'remaining_withholding_confirmed':True}
    # Old aggregates are deliberately unrelated; the explicit source components own both settlements.
    result['form_1040']['line_8_schedule1_other']=99999
    result['form_1040']['line_25c_other_withholding']=99999
    profile=json.loads((ws/'taxpayer_profile.json').read_text())
    profile['flags'].update(has_w2g=True,has_gambling_income=True)
    write_secure(ws/'taxpayer_profile.json',json.dumps(profile))
    write_secure(ws/'result.json',json.dumps(result))
    return ws


def test_california_lottery_winnings_stay_federal_but_leave_ca_income(tmp_path):
    # Schedule CA Section B line 8b, column B: "California excludes California
    # lottery winnings"; federal AGI keeps them (1,200 - separate loss 200).
    import json
    from agents.lib import return_builder as B
    ws=w2g_workspace(tmp_path,raw=source(ca_lottery_income=1200))
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['form_1040']['line_11_agi']==101000
    assert result['california_540']['schedule_ca_adjustments']['ca_lottery_subtraction']==1200
    assert result['california_540']['line_17']==101000-1200
    assert B._main(['--workspace',str(ws),'--check'])==0


@pytest.mark.parametrize('context',['none','misc','benefits','both'])
def test_actual_income_other_forms_withholding_and_state_payments_settle_once(tmp_path,context):
    import json
    from agents.lib import return_builder as B, receipt
    ws=w2g_workspace(tmp_path,context=context)
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    misc=1200 if context in ('misc','both') else 0
    benefits=1350 if context in ('benefits','both') else 0
    # W-2G1200 + MISC when present - separate loss200, then UI850/PFL500 if present.
    nonbenefit=1200+misc-200
    assert result['schedule_1_other_income']['gambling_winnings']==1200
    assert result['form_1040']['line_8_schedule1_other']==nonbenefit+benefits
    assert result['form_1040']['line_11_agi']==100000+nonbenefit+benefits
    assert result['california_540']['line_17']==100000+nonbenefit
    # W-2G200 + separately confirmed other-form withholding300 =25c500.
    # MISC300/Gov100 stay on 25b; neither is overwritten or duplicated.
    other1099=(300 if misc else 0)+(100 if benefits else 0)
    assert result['form_1040']['line_25b_1099_withholding']==other1099
    assert result['form_1040']['line_25c_other_withholding']==500
    assert result['form_1040']['line_25d_total_withholding']==500+other1099
    assert result['form_1040']['line_33_total_payments']==500+other1099
    assert result['california_540']['line_71']==5050+(45 if benefits else 0)
    assert B._main(['--workspace',str(ws),'--check'])==0
    assert receipt.verify(ws,required=(B.GATE,)).valid
    before=(ws/'result.json').read_bytes()
    assert B._main(['--workspace',str(ws)])==0
    assert (ws/'result.json').read_bytes()==before


@pytest.mark.parametrize('field,wrong',[('line_8_schedule1_other',10000),('line_25c_other_withholding',300)])
def test_other_aggregates_and_federal_scope_waiver_cannot_hide_a_dropped_w2g(tmp_path,monkeypatch,field,wrong):
    import json
    from agents.lib import return_builder as B, tie_out as T
    from agents.lib.secure_fs import write_secure
    ws=w2g_workspace(tmp_path,remaining=10000)
    assert B._main(['--workspace',str(ws)])==0
    def waiver(*args,**kwargs):
        raise B.OutOfScope('synthetic federal PFIC waiver')
    monkeypatch.setattr(B,'build',waiver)
    assert B._main(['--workspace',str(ws),'--check'])==0
    result=json.loads((ws/'result.json').read_text())
    result['form_1040'][field]=wrong
    write_secure(ws/'result.json',json.dumps(result))
    assert B._main(['--workspace',str(ws),'--check'])==2
    ledger=T.load_ledger(json.loads((ws/'parse_ledger.json').read_text()))
    report=T.evaluate(ledger,result,[r.file for r in ledger],profile=json.loads((ws/'taxpayer_profile.json').read_text()))
    assert any(field in e for e in report.missing_result_targets)


def test_new_w2g_cannot_reuse_old_remaining_income_or_withholding_confirmation(tmp_path):
    import json
    from agents.lib import return_builder as B
    from agents.lib.secure_fs import write_secure
    ws=w2g_workspace(tmp_path)
    result=json.loads((ws/'result.json').read_text())
    del result['other_forms_withholding']
    write_secure(ws/'result.json',json.dumps(result))
    assert B._main(['--workspace',str(ws)])==2
    result['other_forms_withholding']={'remaining_withholding':300,'remaining_withholding_confirmed':True}
    del result['schedule_1_other_income']['w2g_excluded_from_remaining']
    write_secure(ws/'result.json',json.dumps(result))
    assert B._main(['--workspace',str(ws)])==2


def test_reviewed_income_method_is_disclosed_and_is_not_forced_to_reported_gross(tmp_path):
    import json
    from agents.lib import return_builder as B
    from agents.lib.secure_fs import write_secure
    raw=source(income_method='reviewed_worksheet',schedule_1_income=400,
               worksheet_confirmed=True,worksheet_id='session_alpha')
    ws=w2g_workspace(tmp_path,raw=raw)
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['schedule_1_other_income']['gambling_winnings']==1200
    assert result['schedule_1_other_income']['gambling_income']==400
    assert result['form_1040']['line_8_schedule1_other']==200  # reviewed400 -other loss200
    note=result['schedule_1_other_income']['source_note']
    assert 'reviewed gambling worksheets' in note and note in result['meta']['limitations']
    result['meta']['limitations'].remove(note)
    write_secure(ws/'result.json',json.dumps(result))
    assert B._main(['--workspace',str(ws),'--check'])==2


def test_positive_w2g_alone_implies_the_manual_gambling_capability_note():
    from agents.lib import capabilities as C
    report=C.evaluate({'tax_year':2025,'filing_status':'single','address':{'state':'FL'},
                       'flags':{'has_w2g':True,'has_gambling_income':False}})
    assert any(f.rule_id=='gambling' for f in report.manual)


def test_professional_contribution_uses_business_receipts_and_reviewed_net_income():
    from agents.lib.tax_computations import compute_schedule1_other
    raw=source(schedule_1_income=0,schedule_c_gross=1200)
    assert classify_w2g_income(raw)['self_employment_gross']==1200
    out=compute_schedule1_other(tax_year=2025,records=[],w2g_records=[record(raw)],
        remaining_income=900,remaining_income_confirmed=True,w2g_excluded_from_remaining=True)
    # The separate business worksheet supplied net900. Do not add reported gross1200 again.
    assert out['gambling_business_gross']==1200 and out['gambling_income']==0
    assert out['total']==900


def test_source_changes_invalidate_receipt_and_refresh_income_and_payments(tmp_path):
    import json
    from agents.lib import return_builder as B, receipt
    from agents.lib.secure_fs import write_secure
    ws=w2g_workspace(tmp_path)
    assert B._main(['--workspace',str(ws)])==0
    assert B._main(['--workspace',str(ws),'--check'])==0
    ledger=json.loads((ws/'parse_ledger.json').read_text())
    row=next(r for r in ledger['records'] if r['doc_id']=='w2g')
    row['w2g_income'].update(box_1=1500,schedule_1_income=1500,box_4=250)
    row['key_boxes'].update(gambling_winnings=1500,federal_withholding_w2g=250)
    write_secure(ws/'parse_ledger.json',json.dumps(ledger))
    assert not receipt.verify(ws,required=(B.GATE,)).valid
    assert B._main(['--workspace',str(ws),'--check'])==2
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['form_1040']['line_8_schedule1_other']==1300  # winnings1500 -other loss200
    assert result['form_1040']['line_25c_other_withholding']==550  # W-2G250 +other300
    assert result['form_1040']['line_33_total_payments']==550
    assert B._main(['--workspace',str(ws),'--check'])==0


def test_source_proven_zero_w2g_withholding_preserves_the_legacy_other_forms_component(tmp_path):
    import json
    from agents.lib import return_builder as B
    from agents.lib.secure_fs import write_secure
    ws=w2g_workspace(tmp_path,raw=source(box_4=0))
    result=json.loads((ws/'result.json').read_text())
    del result['other_forms_withholding']
    result['form_1040']['line_25c_other_withholding']=777
    write_secure(ws/'result.json',json.dumps(result))
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['form_1040']['line_25c_other_withholding']==777
    assert result['form_1040']['line_25d_total_withholding']==777
    assert 'other_forms_withholding' not in result


def test_withholding_only_legacy_w2g_and_false_intake_inventory_cannot_pass():
    from agents.lib import tie_out as T
    with pytest.raises(T.TieOutError,match='w2g_income'):
        T.load_ledger({'records':[{'doc_id':'w2g','file':'documents/w2g.pdf','form_type':'W-2G',
            'payer':'Synthetic payer','person':'primary','key_boxes':{'ca_1099_withholding':50}}]})
    with pytest.raises(ValueError,match='intake'):
        T.derive_other_forms_withholding([],{}, {'flags':{'has_w2g':True}})


@pytest.mark.parametrize('bad',[None,2025.0,True])
def test_other_forms_workpaper_year_witness_is_not_a_money_field(bad):
    from agents.lib import tie_out as T
    built=compute_other_forms_withholding(tax_year=2025,records=[record(source())],
        remaining_withholding=300,remaining_withholding_confirmed=True)
    result={'other_forms_withholding':dict(built,computed_tax_year=bad),
        'form_1040':{'line_25c_other_withholding':500}}
    assert T.other_forms_withholding_differences(result,built)


@pytest.mark.parametrize('bad',[None,True,-1,float('nan'),float('inf'),'300'])
def test_other_form_credit_cannot_be_negative_nonfinite_or_boolean(bad):
    with pytest.raises(ValueError,match='finite'):
        compute_other_forms_withholding(tax_year=2025,records=[record(source())],
            remaining_withholding=bad,remaining_withholding_confirmed=True)


def test_composite_misc_w2g_document_keeps_the_two_federal_withholding_columns_distinct(tmp_path):
    import json
    from agents.lib import return_builder as B, parse_records as P
    from agents.lib.secure_fs import write_secure
    from tests.test_misc_income import source as misc_source
    from agents.lib.tax_computations import classify_misc_income
    ws=w2g_workspace(tmp_path)
    ledger=json.loads((ws/'parse_ledger.json').read_text())
    row=next(r for r in ledger['records'] if r['doc_id']=='w2g')
    misc=misc_source(box_3=1000,box_3_business=0,box_3_other=1000,box_5=0,box_6=0)
    row['form_type']='Consolidated W-2G / 1099-MISC'
    row['misc_income']=misc
    row['key_boxes'].update(classify_misc_income(misc))
    P.write_record(ws,row)
    write_secure(ws/'parse_ledger.json',json.dumps(P.build_ledger(ws)))
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['form_1040']['line_8_schedule1_other']==2000  # MISC1000+W2G1200-other200
    assert result['form_1040']['line_25b_1099_withholding']==300
    assert result['form_1040']['line_25c_other_withholding']==500
    assert B._main(['--workspace',str(ws),'--check'])==0


def test_filing_package_includes_other_form_credit_reconciliation_and_required_w2g_copy(tmp_path):
    import json
    from agents.lib import return_builder as B, filing_package as F
    ws=w2g_workspace(tmp_path,remaining=-1200)
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['form_1040']['line_8_schedule1_other']==0  # W-2G1200-other loss1200
    keys=[e['key'] for e in F.assembly_order(result)]
    assert 'schedule_1_other_income' in keys and 'other_forms_withholding' in keys
    assert any('W-2G, Copy B' in s for s in F.attachment_checklist(result))
    result['other_forms_withholding']['total']=300
    with pytest.raises(F.FilingPackageError,match='line25c'):
        F.assembly_order(result)
    result['other_forms_withholding']['total']=500
    result['other_forms_withholding']['computed_tax_year']=2025.0
    with pytest.raises(F.FilingPackageError,match='witness'):
        F.assembly_order(result)
    # A positive active workpaper also cannot be published without the checked source component.
    with pytest.raises(B.Blocked,match='line25c'):
        B.write(ws,{'form_1040':{},'other_forms_withholding':{'w2g_withholding':200}}, {})


@pytest.mark.parametrize('raw', [source(), source(box_1=0,box_4=0,schedule_1_income=0)])
def test_all_w2g_sources_match_the_return_year_even_when_both_federal_boxes_are_zero(raw):
    from agents.lib import tie_out as T
    row=dict(record(raw),doc_id='w2g',file='documents/w2g.pdf',
        form_type='W-2G',payer='Synthetic payer',person='primary')
    row['key_boxes']['ca_1099_withholding']=50
    ledger=T.load_ledger({'records':[row]})
    with pytest.raises(ValueError,match='source year'):
        T.derive_other_forms_withholding(ledger,{'meta':{'tax_year':2026}}, {})


def test_a_separately_reviewed_income_worksheet_requires_its_explicit_confirmation():
    with pytest.raises(ValueError,match='worksheet'):
        classify_w2g_income(source(income_method='reviewed_worksheet',schedule_1_income=400,
            worksheet_id='session_alpha',worksheet_confirmed=False))
