"""AA44 final readers: official flattened TY2025 forms, synthetic source facts."""
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agents.lib import prior_return as P
from agents.lib.rollover import CurrentState, compute_diff, load_prior
from tests.test_prior_return import workspace as workspace
from tests.test_prior_return import write_json

FIXTURES = Path(__file__).parent / 'fixtures/prior_return'
FINAL = json.loads((FIXTURES / 'final_readers_2025.json').read_text(encoding='utf-8'))
BASE = json.loads((FIXTURES / 'official_2025.json').read_text(encoding='utf-8'))


def document():
    pages = {int(k): v for k, v in BASE['pages'].items()}
    words = {}
    for index, name in [(0, '1040_page1'), (9, '540_side3'), (10, '540_side4')]:
        pages[index] = FINAL[name]['text']
        words[index] = copy.deepcopy(FINAL[name]['words'])
    return pages, words


def seed(edit=None):
    pages, words = document()
    if edit:
        edit(pages, words)
    return P.extract_seed(pages, 2025, 'a' * 64, words)


def pending(workspace, monkeypatch, current=None):
    write_json(workspace / 'taxpayer_profile.json', current or {
        'tax_year': 2026, 'filing_status': 'MFJ', 'primary': {'ssn_last4': '6789'}, 'spouse': {'ssn_last4': 'XXX-XX-1111'}})
    pages, words = document()
    monkeypatch.setattr(P, 'extract_pdf_text', lambda *a, **kw: pages)
    monkeypatch.setattr(P, 'extract_pdf_words', lambda *a, **kw: words)
    archive = P.import_pdf(workspace, 'documents/prior_return/synthetic.pdf', 2026)
    result = json.loads((archive / 'result.json').read_text(encoding='utf-8'))
    parsed = result['prior_return_import']
    decisions = {'seed_sha256': P.digest(parsed), 'confirmed_fields': list(parsed['fields']),
                 'confirmed_tables': P.table_confirmation_ids(parsed), 'filing_status': 'MFJ',
                 'ira_owners': {k: 'primary' for k in parsed['fields'] if k.startswith('ira_basis_page_')},
                 'questions': {k: 'not_applicable' for k in parsed['unresolved']},
                 'confirmed_identity': P.identity_confirmation_ids(parsed)}
    return archive, decisions


def test_official_detached_ca_amounts_are_read_and_reconciled():
    parsed = seed()
    field = parsed['fields']['ca_estimated_payments_applied']
    assert field['value'] == 200
    assert {p['line'] for p in field['provenance']} == {'97', '98', '99'}
    assert {p['page'] for p in field['provenance']} == {10, 11}


def test_amounts_equal_to_line_numbers_are_not_mistaken_for_labels():
    def edit(pages, words):
        pages[9] = re.sub(r'\b700\b', '197', pages[9])
        pages[10] = re.sub(r'\b200\b', '98', re.sub(r'\b500\b', '99', pages[10]))
        for page, changes in [(9, {'700': '197'}), (10, {'200': '98', '500': '99'})]:
            for w in words[page]:
                if w['text'] in changes:
                    w['text'] = changes[w['text']]
    assert seed(edit)['fields']['ca_estimated_payments_applied']['value'] == 98


@pytest.mark.parametrize('text', ['NaN', 'Infinity', 'unreadable'])
def test_an_unreadable_ca_cell_remains_a_question(text):
    def edit(pages, words):
        for w in words[10]:
            if w['text'] == '200':
                w['text'] = text
    parsed = seed(edit)
    assert 'ca_estimated_payments_applied' not in parsed['fields']
    assert 'ca_estimated_payments_applied' in parsed['unresolved']


def test_missing_payment_label_cannot_establish_a_zero():
    def edit(pages, words):
        words[10] = [w for w in words[10] if not (w['text'] == '98' and w['x0'] > 400)]
    assert 'ca_estimated_payments_applied' not in seed(edit)['fields']


def test_contradictory_ca_payment_identity_refuses():
    def edit(pages, words):
        for w in words[10]:
            if w['text'] == '500':
                w['text'] = '501'
        pages[10] = pages[10].replace('500', '501')
    with pytest.raises(P.ImportError, match='arithmetic'):
        seed(edit)


def test_page_one_proposals_have_masked_identifiers_and_no_eligibility_guesses():
    parsed = seed()
    values = {k: r['value'] for k, r in parsed['identity_proposals'].items()}
    assert values['primary.first_name'] == 'JOHN' and values['primary.middle_name'] == 'A'
    assert values['spouse.last_name'] == 'EXAMPLE'
    assert values['primary.ssn_last4'] == 'XXX-XX-6789' and values['spouse.ssn_last4'] == 'XXX-XX-1111'
    assert values['address'] == {'street': '100 SAMPLE ST Apt 12', 'city': 'TESTVILLE', 'state': 'CA', 'zip': '90000'}
    assert len(values['dependents']) == 2
    assert [p['ssn_last4'] for p in values['dependents']] == ['XXX-XX-2222', 'XXX-XX-3333']
    assert not any({'dob', 'months_lived_with_you', 'ctc_eligible'} & p.keys() for p in values['dependents'])
    assert all(r['provenance']['page'] == 1 for r in parsed['identity_proposals'].values())
    assert not re.search(r'(?<![0-9])[0-9]{9}(?![0-9])', json.dumps(parsed['identity_proposals']))
    assert 'identity_dependents' in parsed['unresolved']


def test_missing_one_dependent_identifier_never_shifts_the_remaining_one():
    def edit(pages, words):
        for w in words[0]:
            w['text'] = w['text'].replace('XXX-XX-2222', '')
    dep = seed(edit)['identity_proposals']['dependents']['value']
    assert len(dep) == 2 and 'ssn_last4' not in dep[0]
    assert dep[1]['ssn_last4'] == 'XXX-XX-3333'


def test_invalid_state_is_not_proposed_as_an_address():
    def edit(pages, words):
        for w in words[0]:
            if w['text'] == 'CA':
                w['text'] = 'ZZ'
    assert 'address' not in seed(edit)['identity_proposals']


@pytest.mark.parametrize('decision', [None, [], ['primary.first_name'], ['bogus']])
def test_each_identity_proposal_must_be_confirmed(workspace, monkeypatch, decision):
    _, decisions = pending(workspace, monkeypatch)
    if decision is None:
        decisions.pop('confirmed_identity')
    else:
        decisions['confirmed_identity'] = decision
    with pytest.raises(P.ImportError, match='confirmation|Confirm every identity'):
        P.confirm_import(workspace, 2025, decisions)


def test_reviewed_proposals_roll_as_evidence_without_overwriting_current_identity(workspace, monkeypatch):
    archive, decisions = pending(workspace, monkeypatch)
    P.confirm_import(workspace, 2025, decisions)
    prior = load_prior(archive, 2025, allow_imported=True)
    current = CurrentState(profile={'tax_year': 2026, 'primary': {'first_name': 'CURRENT'},
                                    'dependents': [{'first_name': 'CURRENT CHILD'}]},
                           result={'meta': {'tax_year': 2026}}, tax_law={})
    changes = compute_diff(prior, current, {'identity', 'dependents'}, {})
    assert set(changes) == {'identity', 'dependents'}
    assert all(c.action_suggested == 'ask' and c.json_path.startswith('prior_year.imported_')
               for category in changes.values() for c in category)
    assert current.profile['primary']['first_name'] == 'CURRENT'
    assert current.profile['dependents'][0]['first_name'] == 'CURRENT CHILD'
    report = P.review_report(prior.result)
    assert 'JOHN' in report and 'confirmed_identity' in report
    # Validation checks the exact reviewed proposal set even if all digests
    # are maliciously regenerated to agree with a shortened list.
    result = copy.deepcopy(prior.result)
    review = result['prior_return_import']['review']
    review['confirmed_identity'] = []
    result['meta']['imported_review_sha256'] = P.digest(review)
    with pytest.raises(P.ImportError, match='identity proposal'):
        P.validate_reviewed(result, prior.profile)


def test_identity_layout_keeps_name_positions_without_returning_numeric_source_tokens(tmp_path):
    from agents.lib.parse_utils import extract_pdf_words
    from tests.test_parse_utils import _draw, _ssn
    path = _draw(tmp_path / 'names.pdf', [(36, 100, 9, 'JOHN EXAMPLE'), (300, 100, 9, 'SSN ' + _ssn(''))])
    out = extract_pdf_words(str(path), workspace=str(tmp_path), identity_layout=True)[0]
    assert any(w['text'] == 'JOHN' and w['x0'] == 36 for w in out)
    assert any(w.get('masked_row') and 'XXX-XX-6789' in w['text'] for w in out)
    assert _ssn('') not in json.dumps(out)


@pytest.mark.parametrize('json_mode', [False, True])
def test_rollover_cli_does_not_print_private_proposal_values(workspace, monkeypatch, json_mode):
    _, decisions = pending(workspace, monkeypatch)
    P.confirm_import(workspace, 2025, decisions)
    command = [sys.executable, '-m', 'agents.lib.rollover', 'diff', '--from', 'archive/2025', '--workspace', str(workspace),
               '--year', '2026', '--categories', 'identity,dependents']
    if json_mode:
        command.append('--json')
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert 'JOHN' not in run.stdout and 'EXAMPLE' not in run.stdout and '90000' not in run.stdout
    assert 'owner-only' in run.stdout


def test_an_orphan_identifier_does_not_shift_dependent_ownership():
    def edit(pages, words):
        for w in words[0]:
            if 'identity_identifiers' in w and 'XXX-XX-2222' in w['text']:
                w['text'] = w['text'].replace('XXX-XX-2222', '') + ' XXX-XX-4444'
                identifiers = [v for v in w['identity_identifiers'] if v['text'] != 'XXX-XX-2222']
                orphan = dict(identifiers[0], text='XXX-XX-4444', x0=370, x1=420)
                w['identity_identifiers'] = identifiers + [orphan]
    dep = seed(edit)['identity_proposals']['dependents']['value']
    assert 'ssn_last4' not in dep[0] and dep[1]['ssn_last4'] == 'XXX-XX-3333'


@pytest.mark.parametrize("shift", [7.2, 8, 24])
def test_a_numeric_overlay_outside_any_payment_row_is_not_a_blank(shift):
    def edit(pages, words):
        pages[10] = pages[10].replace('500', '700')
        for w in words[10]:
            if w['text'] == '500':
                w['text'] = '700'
            elif w['text'] == '200':
                w['top'] -= shift
                w['bottom'] -= shift
    parsed = seed(edit)
    assert 'ca_estimated_payments_applied' not in parsed['fields']
    assert 'ca_estimated_payments_applied' in parsed['unresolved']


def test_an_invalid_masked_identifier_is_not_truncated_into_a_proposal():
    def edit(pages, words):
        for w in words[0]:
            w['text'] = w['text'].replace('XXX-XX-6789', 'XXX-XX-67895')
            for value in w.get('identity_identifiers', []):
                value['text'] = value['text'].replace('XXX-XX-6789', 'XXX-XX-67895')
    assert 'primary.ssn_last4' not in seed(edit)['identity_proposals']


def test_a_crafted_page_without_cents_markers_exits_in_linear_work():
    class Measured(list):
        visits = 0
        def __iter__(self):
            for value in super().__iter__():
                self.visits += 1
                yield value
    words = Measured([{'text': '98', 'x0': 400, 'x1': 409, 'top': 10 + i * 20, 'bottom': 19 + i * 20}
                      for i in range(100)])
    with pytest.raises(P._Unreadable, match='cents markers'):
        P._ca_payment_lines(words, {'98', '99'}, 1, 'a' * 64)
    assert words.visits == len(words)


def test_stale_position_metadata_cannot_truncate_a_malformed_masked_token():
    def edit(pages, words):
        for w in words[0]:
            w['text'] = w['text'].replace('XXX-XX-6789', 'XXX-XX-67895')
    assert 'primary.ssn_last4' not in seed(edit)['identity_proposals']


@pytest.mark.parametrize('raised', [6.5, 10, 12])
def test_a_refund_typed_high_in_its_box_is_never_read_as_applied(raised):
    """Line 98 blank, the whole overpayment refunded on line 99 and typed high
    in its box, nearer 98's margin label than its own: read by the label it
    would become next year's estimated payments, which 97 = 98 + 99 cannot
    catch. Its own printed cents place it; read nowhere else, it is asked."""
    def edit(pages, words):
        words[10] = [w for w in words[10] if w['text'] != '200']
        for w in words[10]:
            if w['text'] == '500':
                w.update(text='700', top=w['top'] - raised, bottom=w['bottom'] - raised)
        pages[10] = re.sub(r'\b200\b', '', re.sub(r'\b500\b', '700', pages[10]))
    parsed = seed(edit)
    assert parsed['fields'].get('ca_estimated_payments_applied', {}).get('value') != 700
    assert 'ca_estimated_payments_applied' in parsed['unresolved']


@pytest.mark.parametrize('change', ['negative', 'two_cents', 'crossing'])
def test_a_payment_box_guard_leaves_the_line_a_question(change):
    def edit(pages, words):
        page = words[10]
        figure = next(w for w in page if w['text'] == '200')
        if change == 'negative':
            figure['text'] = '-200'
            pages[10] = re.sub(r'\b200\b', '-200', pages[10])
        elif change == 'two_cents':
            cents = min((w for w in page if w['text'] in {'.00', '00'} and w['x0'] > figure['x1']),
                        key=lambda w: abs(w['top'] - figure['top']))
            page.append({**cents, 'x0': cents['x0'] + 6, 'x1': cents['x1'] + 6})
        else:
            cents = min((w for w in page if w['text'] in {'.00', '00'} and w['x0'] > figure['x1']),
                        key=lambda w: abs(w['top'] - figure['top']))
            figure['x1'] = cents['x1'] + 2
    parsed = seed(edit)
    assert 'ca_estimated_payments_applied' not in parsed['fields']
    assert 'ca_estimated_payments_applied' in parsed['unresolved']


def test_the_import_reads_page_one_in_identity_layout(workspace, monkeypatch):
    """Without it, page 1's masked rows hide every name and identifier, and
    the proposals vanish while every other figure still reads."""
    calls = []
    pages, words = document()
    monkeypatch.setattr(P, 'extract_pdf_text', lambda *a, **kw: pages)
    monkeypatch.setattr(P, 'extract_pdf_words', lambda *a, **kw: calls.append(kw) or words)
    P.import_pdf(workspace, 'documents/prior_return/synthetic.pdf', 2026)
    assert calls and all(kw.get('identity_layout') is True for kw in calls)


@pytest.mark.parametrize('current, message', [
    ({'primary': {'ssn_last4': '0001'}, 'spouse': {'ssn_last4': '1111'}}, "different SSN than the current primary"),
    ({'spouse': {'ssn_last4': '1111'}}, "record the current primary"),
    ({'primary': {'ssn_last4': '6789'}, 'spouse': {'ssn_last4': '0002'}}, "different SSN than the current spouse"),
])
def test_page_one_says_whose_return_is_confirmed(workspace, monkeypatch, current, message):
    """Another filer's return, or a joint return with another spouse, is
    refused before its carryforwards reach this year's profile."""
    archive, decisions = pending(workspace, monkeypatch, {'tax_year': 2026, 'filing_status': 'MFJ', **current})
    with pytest.raises(P.ImportError, match=message):
        P.confirm_import(workspace, 2025, decisions)


def test_a_filer_no_longer_married_confirms_last_years_joint_return(workspace, monkeypatch):
    archive, decisions = pending(workspace, monkeypatch, {'tax_year': 2026, 'filing_status': 'Single',
                                                          'primary': {'ssn_last4': '6789'}})
    P.confirm_import(workspace, 2025, decisions)
    assert json.loads((archive / 'result.json').read_text(encoding='utf-8'))['meta']['imported_review'] == 'confirmed'
