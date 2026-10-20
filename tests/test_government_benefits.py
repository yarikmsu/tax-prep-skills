"""AA65: source boxes -> taxable benefits -> actual federal/CA income."""

import copy
import json

import pytest

from agents.lib import return_builder as B, tie_out as T, parse_records as P, receipt
from agents.lib.secure_fs import write_secure
from agents.lib.tax_computations import compute_government_benefits
from tests.test_ca_540_builder import workspace


def source(year=2025, ui=1000, family=500, **changes):
    row = dict(doc_id='benefits', file='documents/benefits.pdf', form_type='1099-G',
        payer='Synthetic program',person='primary',key_boxes={
            'unemployment_compensation':ui,'government_family_leave':family,
            'federal_withholding_1099':100,'ca_1099_withholding':45},
        government_benefits=dict(tax_year=year,form_revision='03/2024' if year==2025 else '12/2026',
            box_1=ui+family if year==2025 else ui,box_10=None if year==2025 else family,
            unemployment_repaid=100 if ui>=100 else 0,
            unemployment_contribution_reduction=50 if ui>=100 else 0,reviewed=True,
            family_programs=[dict(program='ca_edd_pfl',amount=family,
                schedule1_route='7' if year==2025 else 'other')] if family else []))
    row.update(changes)
    return row


def inputs(row=None, **changes):
    row=source() if row is None else row
    data=dict(tax_year=row['government_benefits']['tax_year'],
        records=[{k:row[k] for k in ('key_boxes','government_benefits')}],
        other_income=-200,other_income_confirmed=True,require_ca=True)
    data.update(changes)
    return data


@pytest.mark.parametrize('year',[2025,2026])
def test_both_revisions_keep_ui_family_and_nonbenefit_income_separate(year):
    # UI1000 -same-year repayment100 -eligible UI reduction50 =850.
    # PFL500 is fully included. Other Schedule1 loss200 ->1040 line8=1150.
    out=compute_government_benefits(**inputs(source(year)))
    assert out['unemployment_gross']==1000 and out['family_leave_gross']==500
    assert out['unemployment_taxable']==850 and out['family_leave_taxable']==500
    assert out['total']==1150
    assert out['schedule1_line_7']==(1350 if year==2025 else 850)
    assert out['family_leave_other_income']==(0 if year==2025 else 500)


def test_cent_precision_allows_a_fully_reduced_ui_payment():
    row=source(ui=.3,family=0)
    row['government_benefits'].update(unemployment_repaid=.1,unemployment_contribution_reduction=.2)
    assert compute_government_benefits(**inputs(row))['unemployment_taxable']==0


@pytest.mark.parametrize('field',['box_1','unemployment_repaid','unemployment_contribution_reduction'])
@pytest.mark.parametrize('bad',[None,True,-1,float('nan'),float('inf'),'100',10**400])
def test_bad_money_never_hides_in_a_positive_benefit_total(field,bad):
    row=source()
    row['government_benefits'][field]=bad
    with pytest.raises(T.TieOutError):
        T.load_ledger({'records':[row]})


def test_revision_box_split_and_program_inventory_must_reconcile():
    for year in (2025,2026):
        row=source(year)
        row['government_benefits']['box_1']+=1
        with pytest.raises(T.TieOutError,match='reconcile'):
            T.load_ledger({'records':[row]})
    row=source(2026)
    row['government_benefits']['form_revision']='03/2024'
    with pytest.raises(T.TieOutError,match='revision'):
        T.load_ledger({'records':[row]})
    row=source()
    row['government_benefits']['family_programs']=[]
    with pytest.raises(T.TieOutError,match='reconcile'):
        T.load_ledger({'records':[row]})


def test_pfl_cannot_take_the_old_nonitemizer_contribution_reduction():
    row=source(ui=0,family=500)
    row['government_benefits']['unemployment_contribution_reduction']=100
    with pytest.raises(T.TieOutError,match='UI reductions'):
        T.load_ledger({'records':[row]})


def test_missing_program_identity_does_not_turn_medical_or_private_leave_into_family_income():
    row=source()
    row['government_benefits']['family_programs'][0]['program']='medical_leave'
    with pytest.raises(T.TieOutError,match='medical'):
        T.load_ledger({'records':[row]})


def test_non_edd_ca_treatment_must_be_resolved_without_blocking_other_states():
    row=source()
    row['government_benefits']['family_programs'][0].update(program='other_government',
        ca_exempt=None,ca_exemption_confirmed=False)
    assert compute_government_benefits(**inputs(row,require_ca=False))['ca_family_leave_exempt'] is None
    with pytest.raises(ValueError,match='California treatment'):
        compute_government_benefits(**inputs(row))
    row['government_benefits']['family_programs'][0].update(ca_exempt=False,ca_exemption_confirmed=True)
    out=compute_government_benefits(**inputs(row))
    assert out['ca_family_leave_exempt']==0
    assert 'source worksheets' in out['source_note']


def test_legacy_withholding_only_1099g_cannot_earn_coverage_without_a_benefit_inventory():
    row=source()
    del row['government_benefits']
    row['key_boxes']={'federal_withholding_1099':100}
    with pytest.raises(T.TieOutError,match='government_benefits'):
        T.load_ledger({'records':[row]})


def test_positive_benefits_need_an_explicit_nonbenefit_answer_even_if_line8_was_already_positive():
    row=source()
    ledger=T.load_ledger({'records':[row]})
    result={'meta':{'tax_year':2025},'form_1040':{'line_8_schedule1_other':99999}}
    with pytest.raises(ValueError,match='EXCLUDING'):
        T.derive_government_workpaper(ledger,result,{'tax_year':2025})
    result['schedule_1_government_benefits']={'other_income':0,'other_income_confirmed':False}
    with pytest.raises(ValueError,match='confirm'):
        T.derive_government_workpaper(ledger,result,{'tax_year':2025})


def test_refund_only_zero_benefits_preserves_existing_schedule1_income_without_guessing():
    row=source(ui=0,family=0)
    row['key_boxes']['state_refund_1099g']=1000
    ledger=T.load_ledger({'records':[row]})
    result={'meta':{'tax_year':2025},'form_1040':{'line_8_schedule1_other':300}}
    out=T.derive_government_workpaper(ledger,result,{'tax_year':2025})
    assert out['total']==out['other_income']==300 and out['other_income_confirmed'] is False


def government_workspace(tmp_path):
    ws=workspace(tmp_path)
    row=source()
    write_secure(ws/row['file'],b'%PDF synthetic benefits source')
    raw=json.loads((ws/'parse_ledger.json').read_text())
    raw['records'].append(row)
    for rec in raw['records']:
        P.write_record(ws,rec)
    assert next(r for r in P.build_ledger(ws)['records'] if r['doc_id']=='benefits')['government_benefits']==row['government_benefits']
    write_secure(ws/'parse_ledger.json',json.dumps(P.build_ledger(ws)))
    result=json.loads((ws/'result.json').read_text())
    result['schedule_1_government_benefits']={'other_income':-200,'other_income_confirmed':True}
    write_secure(ws/'result.json',json.dumps(result))
    return ws


def test_builder_includes_benefits_once_and_ca_subtracts_only_the_federal_inclusion(tmp_path):
    ws=government_workspace(tmp_path)
    raw=json.loads((ws/'parse_ledger.json').read_text())
    assert B._main(['--workspace',str(ws)])==0
    out=json.loads((ws/'result.json').read_text())
    assert out['form_1040']['line_8_schedule1_other']==1150
    assert out['form_1040']['line_11_agi']==101150
    ca=out['california_540']
    assert ca['schedule_ca_adjustments']['unemployment_subtraction']==850
    assert ca['schedule_ca_adjustments']['paid_family_leave_subtraction']==500
    assert ca['line_17']==99800  # federal101150 -benefits1350
    assert B._main(['--workspace',str(ws),'--check'])==0
    assert receipt.verify(ws,required=(B.GATE,)).valid
    before=(ws/'result.json').read_bytes()
    assert B._main(['--workspace',str(ws)])==0
    assert (ws/'result.json').read_bytes()==before
    # A large unrelated component cannot mask a dropped benefit in line8.
    altered=copy.deepcopy(out)
    altered['form_1040']['line_8_schedule1_other']-=500
    assert 'form_1040.line_8_schedule1_other' in T.government_workpaper_differences(
        altered,T.derive_government_workpaper(T.load_ledger(raw),altered,
            json.loads((ws/'taxpayer_profile.json').read_text())))
    write_secure(ws/'result.json',json.dumps(altered))
    assert B._main(['--workspace',str(ws),'--check'])==2


def test_tie_out_requires_actual_income_even_when_gross_benefit_targets_agree():
    row=source()
    ledger=T.load_ledger({'records':[row]})
    result={'meta':{'tax_year':2025},'form_1040':{'line_8_schedule1_other':10000},
        'schedule_1_government_benefits':{'other_income':10000,'other_income_confirmed':True}}
    built=T.derive_government_workpaper(ledger,result,{'tax_year':2025})
    result['schedule_1_government_benefits']=built
    report=T.evaluate(ledger,result,[row['file']],profile={'tax_year':2025})
    assert any('form_1040.line_8' in e for e in report.missing_result_targets)


def test_federal_scope_waiver_cannot_waive_the_benefit_check(tmp_path,monkeypatch):
    ws=government_workspace(tmp_path)
    assert B._main(['--workspace',str(ws)])==0
    def waiver(*args,**kwargs):
        raise B.OutOfScope('synthetic federal PFIC waiver')
    monkeypatch.setattr(B,'build',waiver)
    assert B._main(['--workspace',str(ws),'--check'])==0
    result=json.loads((ws/'result.json').read_text())
    result['form_1040']['line_8_schedule1_other']-=500
    write_secure(ws/'result.json',json.dumps(result))
    assert B._main(['--workspace',str(ws),'--check'])==2
    assert not receipt.verify(ws,required=(B.GATE,)).valid


def test_source_changes_invalidate_receipts_and_rebuild_income_not_just_gross_targets(tmp_path):
    ws=government_workspace(tmp_path)
    assert B._main(['--workspace',str(ws)])==0
    assert B._main(['--workspace',str(ws),'--check'])==0
    raw=json.loads((ws/'parse_ledger.json').read_text())
    row=next(r for r in raw['records'] if r['doc_id']=='benefits')
    row['government_benefits']['unemployment_repaid']=200
    write_secure(ws/'parse_ledger.json',json.dumps(raw))
    assert not receipt.verify(ws,required=(B.GATE,)).valid
    assert B._main(['--workspace',str(ws),'--check'])==2
    assert B._main(['--workspace',str(ws)])==0
    result=json.loads((ws/'result.json').read_text())
    # UI1000 - repayment200 - contribution50 + PFL500 - other loss200=1050.
    assert result['form_1040']['line_8_schedule1_other']==1050
    assert result['california_540']['schedule_ca_adjustments']['unemployment_subtraction']==750
    assert result['form_1040']['line_25b_1099_withholding']==100
    assert result['california_540']['line_71']==5045  # W-2 withholding5000 + 1099-G45


@pytest.mark.parametrize('bad',[True,2025.0,None])
def test_derived_year_witness_is_not_an_optional_number(bad):
    built=compute_government_benefits(**inputs())
    built['computed_tax_year']=2025
    result={'schedule_1_government_benefits':dict(built,computed_tax_year=bad),
        'form_1040':{'line_8_schedule1_other':built['total']}}
    assert T.government_workpaper_differences(result,built)


def test_source_year_missing_intake_and_unknown_ca_program_cannot_be_defaulted():
    row=source()
    row['government_benefits']['tax_year']=2024
    with pytest.raises(T.TieOutError,match='year'):
        T.load_ledger({'records':[row]})
    with pytest.raises(ValueError,match='intake'):
        T.derive_government_workpaper([],{}, {'flags':{'has_1099g_benefits':True}})
    row=source()
    row['government_benefits']['family_programs'][0].update(program='other_government',
        ca_exempt=None,ca_exemption_confirmed=False)
    ledger=T.load_ledger({'records':[row]})
    result={'meta':{'tax_year':2025},'schedule_1_government_benefits':{
        'other_income':0,'other_income_confirmed':True}}
    with pytest.raises(ValueError,match='California treatment'):
        T.derive_government_workpaper(ledger,result,{'address':{'state':' ca '}})


def test_manual_program_disclosure_is_required_and_retired_when_it_no_longer_applies():
    row=source()
    row['government_benefits']['family_programs'][0].update(program='other_government',
        ca_exempt=True,ca_exemption_confirmed=True)
    built=compute_government_benefits(**inputs(row))
    built['computed_tax_year']=2025
    result={'schedule_1_government_benefits':built,
        'form_1040':{'line_8_schedule1_other':built['total']}}
    assert 'meta.limitations (government benefits)' in T.government_workpaper_differences(result,built)
    result['meta']={'limitations':[built['source_note']]}
    assert not T.government_workpaper_differences(result,built)
    clean=compute_government_benefits(**inputs())
    clean['computed_tax_year']=2025
    result['schedule_1_government_benefits']=clean
    assert 'meta.limitations (stale government benefits disclosure)' in T.government_workpaper_differences(result,clean)


def test_ca_subtraction_tracks_the_reviewed_family_route_and_does_not_duplicate_ui():
    from agents.lib.tax_computations import compute_ca_schedule_ca
    from tests.test_ca_schedule_ca import inputs as ca_inputs
    row=source()
    row['government_benefits']['family_programs'][0]['schedule1_route']='other'
    gov=compute_government_benefits(**inputs(row))
    ca=compute_ca_schedule_ca(**ca_inputs(taxable_unemployment=gov['unemployment_taxable'],
        taxable_ca_family_leave=gov['ca_family_leave_exempt'],ca_family_line_7=gov['ca_family_leave_line_7']))
    assert ca['section_b_line_7_column_b']==850
    assert ca['section_b_other_income_subtraction']==500
    assert ca['total_subtractions']==4400  # existing US/fund1000 + SS1700 + refund300 + other50 + UI/PFL1350


def test_direct_writer_cannot_publish_a_benefit_sheet_without_its_checked_income(tmp_path):
    result={'form_1040':{},'schedule_1_government_benefits':{'unemployment_gross':1000}}
    with pytest.raises(B.Blocked,match='checked line8'):
        B.write(tmp_path,result,{})
