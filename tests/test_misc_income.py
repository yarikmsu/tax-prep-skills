"""AA67: current source boxes never treat withholding or a checkbox as income."""

import pytest

from agents.lib.tax_computations.federal.misc_income import (
    classify_misc_income, compute_schedule1_other, validate_misc_income,
)


def source(year=2025, **changes):
    raw = dict(tax_year=year, form_revision='04/2025' if year == 2025 else '12/2026',
        reviewed=True, box_3=1200, box_3_business=200, box_3_other=1000,
        box_3_unresolved=0, box_4=300, box_5=400, box_6=600, box_7=True,box_8=0,other_federal_withholding=0,
        other_business_gross=0)
    raw.update(changes)
    return raw


@pytest.mark.parametrize('year', [2025, 2026])
def test_both_source_revisions_split_other_income_receipts_withholding_and_checkbox(year):
    # Business200+fishing400+medical600=1200, other1000, withholding300.
    # Box7 is a statement about products sold TO this recipient, no income amount.
    raw=source(year)
    boxes=classify_misc_income(raw)
    assert boxes == {'self_employment_gross':1200,'miscellaneous_other_income':1000,
                     'federal_withholding_1099':300}
    validate_misc_income(raw, boxes)
    out=compute_schedule1_other(tax_year=year,records=[{'key_boxes':boxes,'misc_income':raw}],
        remaining_income=-1500,remaining_income_confirmed=True)
    assert out['total']==-500  # other1000 - unrelated loss1500, UI/PFL excluded.


def test_old_range_sum_cannot_be_reused_as_business_gross():
    raw=source()
    boxes=classify_misc_income(raw)
    boxes['self_employment_gross']+=raw['box_4']+raw['box_3_other']
    with pytest.raises(ValueError,match='disagree'):
        validate_misc_income(raw,boxes)


@pytest.mark.parametrize('bad',[None,False,-1,float('nan'),float('inf'),'200',10**400])
def test_bad_source_money_is_rejected(bad):
    with pytest.raises(ValueError,match='finite'):
        classify_misc_income(source(box_4=bad))


@pytest.mark.parametrize('bad',[None,0,1,5000,'X'])
def test_direct_sales_box_is_a_boolean_only(bad):
    with pytest.raises(ValueError,match='checkbox'):
        classify_misc_income(source(box_7=bad))


def test_unknown_source_treatment_and_unreviewed_years_refuse():
    with pytest.raises(ValueError,match='Schedule F'):
        classify_misc_income(source(box_3_other=0,box_3_unresolved=1000))
    with pytest.raises(ValueError,match='reconcile'):
        classify_misc_income(source(box_3=1500))
    with pytest.raises(ValueError,match='year'):
        classify_misc_income(source(2024))
    with pytest.raises(ValueError,match='confirm'):
        compute_schedule1_other(tax_year=2025,records=[],remaining_income=0,
                               remaining_income_confirmed=False)


def test_checkbox_only_notice_does_not_invent_sales_or_taxable_income():
    raw=source(box_3=0,box_3_business=0,box_3_other=0,box_4=0,box_5=0,box_6=0)
    assert classify_misc_income(raw)=={}
    assert validate_misc_income(raw,{},skipped=True)==raw
    with pytest.raises(ValueError,match='skipped'):
        validate_misc_income(source(),{},skipped=True)


def test_substitute_payments_are_other_income_and_composite_withholding_stays_separate():
    raw=source(box_8=150,other_federal_withholding=200)
    boxes=classify_misc_income(raw)
    assert boxes['miscellaneous_other_income']==1150
    assert boxes['federal_withholding_1099']==500
    assert boxes['self_employment_gross']==1200
    validate_misc_income(raw,boxes)


def misc_workspace(tmp_path, *, benefits=False, remaining=-1500):
    import json
    from agents.lib import parse_records as P
    from agents.lib.secure_fs import write_secure
    from tests.test_ca_540_builder import workspace
    from tests.test_government_benefits import government_workspace
    ws=government_workspace(tmp_path) if benefits else workspace(tmp_path)
    raw=source(box_3_business=0,box_3_other=1200,box_5=0,box_6=0)
    row={'doc_id':'misc','file':'documents/misc.pdf','form_type':'1099-MISC',
        'payer':'Synthetic payer','person':'primary','key_boxes':classify_misc_income(raw),'misc_income':raw}
    write_secure(ws/row['file'],b'%PDF synthetic MISC source')
    ledger=json.loads((ws/'parse_ledger.json').read_text())
    ledger['records'].append(row)
    for rec in ledger['records']:
        P.write_record(ws,rec)
    built=P.build_ledger(ws)
    assert next(r for r in built['records'] if r['doc_id']=='misc')['misc_income']==raw
    write_secure(ws/'parse_ledger.json',json.dumps(built))
    result=json.loads((ws/'result.json').read_text())
    result['schedule_1_other_income']={'remaining_income':remaining,'remaining_income_confirmed':True}
    write_secure(ws/'result.json',json.dumps(result))
    return ws


@pytest.mark.parametrize('benefits',[False,True])
def test_actual_income_and_withholding_settle_once_with_signed_other_income(tmp_path,benefits):
    import json
    from agents.lib import return_builder as B, receipt
    ws=misc_workspace(tmp_path,benefits=benefits)
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    # Nonbusiness MISC1200 - remaining loss1500 =non-benefit(-300).
    # With UI/PFL: add netUI850 + family500 ->line8=1050, CA subtracts1350.
    assert result['schedule_1_other_income']['total']==-300
    assert result['form_1040']['line_8_schedule1_other']==(1050 if benefits else -300)
    assert result['form_1040']['line_11_agi']==(101050 if benefits else 99700)
    assert result['california_540']['line_17']==99700
    assert result['form_1040']['line_25b_1099_withholding']==(400 if benefits else 300)
    if benefits:
        assert result['schedule_1_government_benefits']['other_income']==-300
    assert B._main(['--workspace',str(ws),'--check'])==0
    assert receipt.verify(ws,required=(B.GATE,)).valid
    before=(ws/'result.json').read_bytes()
    assert B._main(['--workspace',str(ws)])==0
    assert (ws/'result.json').read_bytes()==before


def test_dropped_misc_income_cannot_hide_behind_a_large_other_component_or_scope_waiver(tmp_path,monkeypatch):
    import json
    from agents.lib import return_builder as B, tie_out as T
    from agents.lib.secure_fs import write_secure
    ws=misc_workspace(tmp_path,remaining=10000)
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['form_1040']['line_8_schedule1_other']==11200
    def waiver(*args,**kwargs):
        raise B.OutOfScope('synthetic federal PFIC waiver')
    monkeypatch.setattr(B,'build',waiver)
    assert B._main(['--workspace',str(ws),'--check'])==0
    result['form_1040']['line_8_schedule1_other']=10000
    write_secure(ws/'result.json',json.dumps(result))
    assert B._main(['--workspace',str(ws),'--check'])==2
    ledger=T.load_ledger(json.loads((ws/'parse_ledger.json').read_text()))
    report=T.evaluate(ledger,result,[r.file for r in ledger],profile={'tax_year':2025})
    assert any('form_1040.line_8' in e for e in report.missing_result_targets)


def test_positive_misc_income_needs_an_explicit_remaining_answer_and_source_year(tmp_path):
    import json
    from agents.lib import return_builder as B
    from agents.lib.secure_fs import write_secure
    ws=misc_workspace(tmp_path)
    result=json.loads((ws/'result.json').read_text())
    result['form_1040']['line_8_schedule1_other']=99999
    del result['schedule_1_other_income']
    write_secure(ws/'result.json',json.dumps(result))
    assert B._main(['--workspace',str(ws)])==2
    raw=source()
    with pytest.raises(ValueError,match='year'):
        compute_schedule1_other(tax_year=2026,records=[{'misc_income':raw,'key_boxes':classify_misc_income(raw)}],
            remaining_income=0,remaining_income_confirmed=True)


def test_withholding_only_legacy_misc_does_not_prove_income_boxes_were_empty():
    from agents.lib import tie_out as T
    with pytest.raises(T.TieOutError,match='misc_income'):
        T.load_ledger({'records':[{'doc_id':'misc','file':'documents/misc.pdf',
            'form_type':'1099-MISC','payer':'Synthetic payer','person':'primary',
            'key_boxes':{'federal_withholding_1099':300}}]})


@pytest.mark.parametrize('benefits',[False,True])
def test_filing_package_keeps_other_income_detail_even_when_schedule1_nets_to_zero(tmp_path,benefits):
    import json
    from agents.lib import return_builder as B, filing_package as F
    # MISC1200 + optional UI/PFL1350 exactly offset by the remaining loss.
    ws=misc_workspace(tmp_path,benefits=benefits,remaining=-2550 if benefits else -1200)
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['form_1040']['line_8_schedule1_other']==0
    keys=[e['key'] for e in F.assembly_order(result)]
    assert 'schedule_1_other_income' in keys
    assert ('schedule_1_government_benefits' in keys) is benefits


@pytest.mark.parametrize('bad',[None,True,float('nan'),float('inf'),'0'])
def test_remaining_income_is_a_finite_signed_source_amount(bad):
    with pytest.raises(ValueError,match='finite'):
        compute_schedule1_other(tax_year=2025,records=[],remaining_income=bad,
            remaining_income_confirmed=True)


def test_derived_workpaper_year_and_writer_cannot_be_bypassed(tmp_path):
    from agents.lib import return_builder as B, tie_out as T
    built=compute_schedule1_other(tax_year=2025,records=[],remaining_income=0,
        remaining_income_confirmed=True)
    result={'form_1040':{'line_8_schedule1_other':0},
        'schedule_1_other_income':dict(built,computed_tax_year=2025.0)}
    assert T.schedule1_other_differences(result,built,None)
    with pytest.raises(B.Blocked,match='checked line8'):
        B.write(tmp_path,{'form_1040':{},'schedule_1_other_income':{'miscellaneous_other_income':1200}}, {})


def test_withholding_change_invalidates_receipt_without_changing_income(tmp_path):
    import json
    from agents.lib import return_builder as B, receipt
    from agents.lib.secure_fs import write_secure
    ws=misc_workspace(tmp_path)
    assert B._main(['--workspace',str(ws)])==0
    assert B._main(['--workspace',str(ws),'--check'])==0
    ledger=json.loads((ws/'parse_ledger.json').read_text())
    row=next(r for r in ledger['records'] if r['doc_id']=='misc')
    row['misc_income']['box_4']=400
    row['key_boxes']['federal_withholding_1099']=400
    write_secure(ws/'parse_ledger.json',json.dumps(ledger))
    assert not receipt.verify(ws,required=(B.GATE,)).valid
    assert B._main(['--workspace',str(ws),'--check'])==2
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    assert result['form_1040']['line_8_schedule1_other']==-300
    assert result['form_1040']['line_25b_1099_withholding']==400
    assert B._main(['--workspace',str(ws),'--check'])==0


def test_business_only_misc_still_must_belong_to_the_return_year():
    from agents.lib import tie_out as T
    raw=source(2026,box_3_other=0,box_3_business=1200)
    ledger=T.load_ledger({'records':[{'doc_id':'misc','file':'documents/misc.pdf',
        'form_type':'1099-MISC','payer':'Synthetic payer','person':'primary',
        'key_boxes':classify_misc_income(raw),'misc_income':raw}]})
    with pytest.raises(ValueError,match='year'):
        T.derive_schedule1_other_workpaper(ledger,{'meta':{'tax_year':2025}}, {})


def test_intake_yes_without_a_reviewed_misc_inventory_is_not_zero():
    from agents.lib import tie_out as T
    with pytest.raises(ValueError,match='intake'):
        T.derive_schedule1_other_workpaper([],{}, {'flags':{'has_1099_misc':True}})


@pytest.mark.parametrize('key,component',[
    ('schedule_1_other_income','remaining_income'),
    ('schedule_1_government_benefits','other_income')])
@pytest.mark.parametrize('year',[None,2025.0,True])
def test_filing_package_cannot_ignore_an_active_workpaper_with_a_bad_year_witness(key,component,year):
    from agents.lib import filing_package as F
    result={'meta':{'limitations':['Synthetic checked return']},
        'form_1040':{'line_8_schedule1_other':100},key:{component:100,'total':100,'computed_tax_year':year}}
    with pytest.raises(F.FilingPackageError,match='witness'):
        F.assembly_order(result)
