"""Synthetic rental books through the real source/build/check/rollover paths."""

import copy
import datetime
import json

import pytest

from agents.lib import parse_records as P, receipt, return_builder as B, tie_out as T
from agents.lib.secure_fs import write_secure
from tests.test_ca_540_builder import workspace
from tests.test_residential_rentals import rental_case


def read(ws, name):
    return json.loads((ws / name).read_text())


def save(ws, name, data):
    write_secure(ws / name, json.dumps(data))


def rental_workspace(tmp_path, *, benefits=False, misc=False):
    from tests.test_government_benefits import government_workspace
    ws = government_workspace(tmp_path) if benefits else workspace(tmp_path)
    data = rental_case()
    profile = read(ws, 'taxpayer_profile.json')
    profile['rental_return'] = data['plan']
    profile['flags']['has_rental_income'] = True
    save(ws, 'taxpayer_profile.json', profile)
    law = read(ws, 'tax_law_2025.json')
    law['federal']['rental'] = data['federal_law']
    law['state']['rental'] = data['ca_law']
    save(ws, 'tax_law_2025.json', law)
    ledger = read(ws, 'parse_ledger.json')
    next(r for r in ledger['records'] if 'wages' in r['key_boxes'])['key_boxes']['wages'] = 120000
    source = data['source_records'][0]
    source.update(file='documents/rental-book.pdf', form_type='Rental books', payer='Synthetic source', person='primary')
    ledger['records'].append(source)
    if misc:
        from tests.test_misc_income import source as misc_source
        from agents.lib.tax_computations.federal.misc_income import classify_misc_income
        raw = misc_source(box_1=8000, box_3=0, box_3_business=0, box_3_other=0, box_4=0, box_5=0, box_6=0, box_7=False)
        info = copy.deepcopy(source['rental_source'])
        info['kind'] = 'information_return'
        info['entries'][0]['rents'] = 8000
        info['entries'][0]['expenses'] = dict.fromkeys(info['entries'][0]['expenses'], 0)
        ledger['records'].append(dict(doc_id='reported-rents', file='documents/reported.pdf',
            form_type='1099-MISC', payer='Synthetic source', person='primary',
            key_boxes=classify_misc_income(raw), misc_income=raw, rental_source=info))
        profile['rental_return']['properties'][0]['source_ids'].append('reported-rents')
        save(ws, 'taxpayer_profile.json', profile)
    for row in ledger['records']:
        write_secure(ws / row['file'], b'%PDF synthetic source')
        P.write_record(ws, row)
    save(ws, 'parse_ledger.json', P.build_ledger(ws))
    result = read(ws, 'result.json')
    result['schedule_1_other_income'] = dict(remaining_income=0, remaining_income_confirmed=True,
        rentals_excluded_from_remaining=True)
    save(ws, 'result.json', result)
    return ws


def confirm_related(ws):
    result = read(ws, 'result.json')
    profile = read(ws, 'taxpayer_profile.json')
    profile['rental_return']['related_forms_review'] = dict(
        input_digest=result['schedule_e_rentals']['related_forms_input_digest'],
        qbi_reviewed=True, niit_reviewed=True, amt_reviewed=True, income_dependent_forms_reviewed=True)
    save(ws, 'taxpayer_profile.json', profile)
    assert B._main(['--workspace', str(ws)]) == 0


@pytest.mark.parametrize('benefits,misc', [(False, False), (True, False), (False, True), (True, True)])
def test_derived_rental_net_settles_once_and_ca_gets_only_allowed_difference(tmp_path, benefits, misc):
    ws = rental_workspace(tmp_path, benefits=benefits, misc=misc)
    assert B._main(['--workspace', str(ws)]) == 0
    out = read(ws, 'result.json')
    rental = out['schedule_e_rentals']
    assert rental['gross_rents'] == 10000  #8k overlapping1099 is evidence, not extra cash.
    assert rental['reported_rents'] == (8000 if misc else 0)
    assert rental['federal']['schedule_e_line26'] == -12500
    # With benefits, MAGI121350 gives allowance14325, leavingCA4675.
    adjustment = 1825 if benefits else 2500
    assert rental['ca_subtraction'] == adjustment
    assert out['california_540']['schedule_ca_adjustments']['section_b_line_5_column_b'] == adjustment
    assert out['form_1040']['line_8_schedule1_other'] == -12500 + (1350 if benefits else 0)
    assert out['california_540']['line_17'] == 120000 - 12500 - adjustment
    assert B._main(['--workspace', str(ws), '--check']) == 2  #Related worksheets still need review.
    confirm_related(ws)
    assert B._main(['--workspace', str(ws), '--check']) == 0
    assert receipt.verify(ws, required=(B.GATE,)).valid
    result, profile, ledger, law, _ = B.load(ws)
    assert not T._rental_checks(ledger, result, profile, law)
    before = (ws / 'result.json').read_bytes()
    assert B._main(['--workspace', str(ws)]) == 0
    assert before == (ws / 'result.json').read_bytes()


@pytest.mark.parametrize('omission', ['section', 'source', 'line8', 'carryforward', 'review', 'note'])
def test_rental_omissions_fail_independently_even_with_federal_scope_waiver(tmp_path, monkeypatch, omission):
    ws = rental_workspace(tmp_path)
    assert B._main(['--workspace', str(ws)]) == 0
    confirm_related(ws)
    result, profile, ledger, law, _ = B.load(ws)
    if omission == 'section':
        del result['schedule_e_rentals']
    elif omission == 'source':
        ledger = [r for r in ledger if r.rental_source is None]
    elif omission == 'line8':
        result['form_1040']['line_8_schedule1_other'] = 0
    elif omission == 'carryforward':
        del result['schedule_e_rentals']['carryforward']
    elif omission == 'review':
        del profile['rental_return']['related_forms_review']
    else:
        result['meta']['limitations'] = []
    assert T._rental_checks(ledger, result, profile, law)
    if omission != 'source':
        save(ws, 'result.json', result)
        save(ws, 'taxpayer_profile.json', profile)
        def waiver(*args, **kwargs):
            raise B.OutOfScope('synthetic federal waiver')
        monkeypatch.setattr(B, 'build', waiver)
        assert B._main(['--workspace', str(ws), '--check']) == 2


def test_remaining_aggregate_and_stale_related_review_cannot_survive_input_changes(tmp_path):
    ws = rental_workspace(tmp_path)
    result = read(ws, 'result.json')
    result['schedule_1_other_income']['rentals_excluded_from_remaining'] = False
    save(ws, 'result.json', result)
    assert B._main(['--workspace', str(ws)]) == 2
    result['schedule_1_other_income']['rentals_excluded_from_remaining'] = True
    save(ws, 'result.json', result)
    assert B._main(['--workspace', str(ws)]) == 0
    confirm_related(ws)
    assert B._main(['--workspace', str(ws), '--check']) == 0
    ledger = read(ws, 'parse_ledger.json')
    next(r for r in ledger['records'] if 'wages' in r['key_boxes'])['key_boxes']['wages'] += 1
    save(ws, 'parse_ledger.json', ledger)
    assert not receipt.verify(ws, required=(B.GATE,)).valid
    assert B._main(['--workspace', str(ws)]) == 0
    assert B._main(['--workspace', str(ws), '--check']) == 2


def test_main_tieout_recomputes_rentals_when_the_result_section_was_dropped(tmp_path):
    ws = rental_workspace(tmp_path)
    assert B._main(['--workspace', str(ws)]) == 0
    confirm_related(ws)
    result, profile, ledger, law, _ = B.load(ws)
    del result['schedule_e_rentals']
    report = T.evaluate(ledger, result, [r.file for r in ledger], profile=profile, tax_law=law)
    assert report.failed
    assert any(e.startswith('Residential rentals disagree: schedule_e_rentals') for e in report.missing_result_targets)


def test_rent_box_cannot_route_to_business_or_hide_without_property_evidence():
    from tests.test_misc_income import source
    from agents.lib.tax_computations.federal.misc_income import classify_misc_income
    raw = source(box_1=100)
    boxes = classify_misc_income(raw)
    assert boxes['rental_reported_rents'] == 100
    assert boxes['self_employment_gross'] == 1200  #200box3+400box5+600box6; no rent.
    row = dict(doc_id='misc', file='documents/misc.pdf', form_type='1099-MISC',
        payer='Synthetic source', person='primary', key_boxes=boxes, misc_income=raw)
    with pytest.raises(T.TieOutError, match='rental_source'):
        T.load_ledger({'records': [row]})
    del raw['box_1']
    with pytest.raises(ValueError, match='every reviewed income box'):
        classify_misc_income(raw)


def test_rollover_keeps_independent_closing_balances_and_inventory(tmp_path):
    from agents.lib import rollover as V
    ws = rental_workspace(tmp_path)
    assert B._main(['--workspace', str(ws)]) == 0
    result = read(ws, 'result.json')
    prior = V.PriorReturn(2025, result, {})
    current = V.CurrentState({}, {}, {})
    changes = V._diff_carryforwards(prior, current)
    carried = next(c for c in changes if c.json_path == 'prior_year.rental_carryforward')
    property_balance = carried.prior_value['properties'][0]
    assert property_balance['federal_passive_losses']['active_origin'] == 0
    assert property_balance['ca_passive_losses']['active_origin'] == 4000
    assert property_balance['assets'][0] == dict(asset_id='building', federal_accumulated=6500, ca_accumulated=13000)
    assert 'address' not in str(carried.prior_value)
    assert 'source_ids' not in str(carried.prior_value)
    assert carried.action_suggested == 'ask'  #Missing current status cannot silently allocate spouse balances.
    del prior.result['schedule_e_rentals']['carryforward']
    with pytest.raises(V.RolloverError, match='closing carryforward'):
        V._diff_carryforwards(prior, current)


@pytest.mark.parametrize('omission', ['property', 'passive_balance', 'asset_balance', 'asset_inventory'])
def test_rollover_cannot_trust_a_zeroed_closing_record_over_retained_schedules(omission):
    from agents.lib.tax_computations.federal.rentals import compute_residential_rental_return, rental_carryforward_from_workpaper
    paper = compute_residential_rental_return(**rental_case())
    if omission == 'property':
        paper['carryforward']['properties'] = []
        paper['carryforward']['asset_inventory'] = []
    elif omission == 'passive_balance':
        paper['carryforward']['properties'][0]['ca_passive_losses']['active_origin'] = 0
    elif omission == 'asset_balance':
        paper['carryforward']['properties'][0]['assets'][0]['federal_accumulated'] = 0
    else:
        paper['carryforward']['asset_inventory'][0]['assets'][0]['federal_basis'] += 1
    with pytest.raises(ValueError):
        rental_carryforward_from_workpaper(paper, 2025)


@pytest.mark.parametrize('state,covered', [('CA', True), ('FL', True), ('WA', True), ('NY', False), ('NC', False)])
def test_capability_downgrade_is_conditional_and_keeps_source_state_obligations(state, covered):
    from agents.lib import capabilities as C
    from tests.test_capabilities import _profile
    profile = _profile(state=state)
    profile['rental_return'] = rental_case()['plan']
    #Plan is positive evidence even when the intake flag is false.
    report = C.evaluate(profile)
    findings = report.blocking + report.acknowledged + report.manual
    finding = next(f for f in findings if f.rule_id == 'rental_income')
    assert (finding.level == C.MANUAL) is covered
    if state != 'CA':
        assert any(f.rule_id == 'nonresident_source_income' for f in report.blocking)
    profile['rental_return']['scope']['no_personal_use'] = False
    report = C.evaluate(profile)
    assert any(f.rule_id == 'rental_income' for f in report.blocking)


def test_known_ca_history_retains_the_named_gate_on_a_federal_only_return():
    from agents.lib import capabilities as C
    from tests.test_capabilities import _profile
    from tests.test_residential_rentals import existing_case
    args = existing_case()
    profile = _profile(state='FL')
    profile['rental_return'] = args['plan']
    profile['prior_year'] = {'rental_carryforward': args['opening']}
    report = C.evaluate(profile)
    assert any(f.rule_id == 'rental_income' for f in report.blocking)
    opening = profile['prior_year']['rental_carryforward']['properties'][0]
    opening['ca_passive_losses'] = None
    opening['assets'][0]['ca_accumulated'] = None
    assert any(f.rule_id == 'rental_income' for f in C.evaluate(profile).manual)


@pytest.mark.parametrize('state,regime', [('FL', 'none'), ('WA', 'capital_gains_excise')])
def test_nonca_federal_rental_path_does_not_fabricate_ca_balances(tmp_path, state, regime):
    ws = rental_workspace(tmp_path)
    result, profile, ledger, law, _ = B.load(ws)
    profile['address']['state'] = state
    law['state'] = {'code': state, 'type': regime}
    built = T.derive_rental_workpaper(ledger, result, profile, law)
    assert built['federal']['schedule_e_line26'] == -12500
    assert built['california'] is None
    assert built['carryforward']['properties'][0]['ca_passive_losses'] is None
    law['state']['type'] = 'graduated_income'
    with pytest.raises(ValueError, match='regime'):
        T.derive_rental_workpaper(ledger, result, profile, law)


def test_official_attachment_sequences_and_zero_net_rental_workpapers_survive_packaging(tmp_path):
    from agents.lib import filing_package as F
    from agents.lib.yoy_diff import OPTIONAL_SECTIONS, _section_present
    ws = rental_workspace(tmp_path)
    ledger = read(ws, 'parse_ledger.json')
    next(r for r in ledger['records'] if 'wages' in r['key_boxes'])['key_boxes']['wages'] = 200000
    save(ws, 'parse_ledger.json', ledger)
    assert B._main(['--workspace', str(ws)]) == 0
    with pytest.raises(F.FilingPackageError, match='review'):
        F.assembly_order(read(ws, 'result.json'))
    confirm_related(ws)
    result = read(ws, 'result.json')
    assert result['form_1040']['line_8_schedule1_other'] == 0
    order = F.assembly_order(result)
    assert any(r['key'] == 'schedule_e_rentals' and r['sequence'] == '13' for r in order)
    assert any(r['title'].startswith('Form 4562') and r['sequence'] == '179' for r in order)
    assert any(r['title'].startswith('Form 8582') and r['sequence'] == '858' for r in order)
    assert any(r['title'].startswith('FTB 3801') for r in order)
    assert any(r['title'].startswith('FTB 3885A') for r in order)
    assert 'schedule_e_rentals' in OPTIONAL_SECTIONS and _section_present(result, 'schedule_e_rentals')
    checklist = F.attachment_checklist(result)
    assert any('not completed official forms' in r for r in checklist)


@pytest.mark.parametrize('surface,evidence', [(s, e) for s in ('scenario', 'roth', 'projection')
    for e in ('plan', 'flag', 'carryforward', 'result') if (s, e) != ('projection', 'result')])
def test_planners_refuse_to_freeze_income_dependent_rental_losses(surface, evidence):
    from agents.lib import scenario, roth_ladder
    from agents.lib.projection import ProjectionError, project_federal_tax
    profile, result = {}, {}
    if evidence == 'plan':
        profile['rental_return'] = {}
    elif evidence == 'flag':
        profile['flags'] = {'has_rental_income': True}
    elif evidence == 'carryforward':
        profile['prior_year'] = {'rental_carryforward': {}}
    else:
        result['schedule_e_rentals'] = {'computed_tax_year': 2025}
    if surface == 'scenario':
        with pytest.raises(scenario.ScenarioError, match='passive-loss'):
            scenario.build_report(result, profile, {}, 2025, 'example', {}, None, datetime.date(2025, 1, 1))
    elif surface == 'roth':
        with pytest.raises(roth_ladder.RothLadderError, match='passive-loss'):
            roth_ladder.build_ladder(result, profile, {}, 2025, max_amount=1000, step=100, name='example', today=datetime.date(2025, 1, 1))
    else:
        with pytest.raises(ProjectionError, match='passive-loss'):
            project_federal_tax(None, profile, {})


@pytest.mark.parametrize('block', ['missing', 'null'])
def test_a_california_rental_refuses_without_its_state_rental_block(tmp_path, block):
    # Without CA basis, method and FTB 3801 limits the federal-only path would
    # copy federal results onto Form 540 and over-state CA income by 2,500.
    ws = rental_workspace(tmp_path)
    law = read(ws, 'tax_law_2025.json')
    if block == 'missing':
        del law['state']['rental']
    else:
        law['state']['rental'] = None
    save(ws, 'tax_law_2025.json', law)
    assert B._main(['--workspace', str(ws)]) == 2
    result, profile, ledger, law, _ = B.load(ws)
    assert any('state.rental' in m for m in T._rental_checks(ledger, result, profile, law))


@pytest.mark.parametrize('acknowledged', [True, False])
def test_an_acknowledged_out_of_scope_rental_finishes_on_external_worksheets(tmp_path, acknowledged):
    # No reviewed residential plan: acknowledging the named limitation lets
    # the return finish with the rental net typed in remaining income.
    ws = workspace(tmp_path)
    profile = read(ws, 'taxpayer_profile.json')
    profile['flags']['has_rental_income'] = True
    if acknowledged:
        profile['acknowledged_limitations'] = ['rental_income']
    save(ws, 'taxpayer_profile.json', profile)
    result = read(ws, 'result.json')
    result['schedule_1_other_income'] = dict(remaining_income=5000, remaining_income_confirmed=True)
    save(ws, 'result.json', result)
    assert (B._main(['--workspace', str(ws)]) == 0) is acknowledged
