"""AA64: published source fields preserve income and deduction inputs separately."""

import json
from pathlib import Path

import pytest

from agents.lib import answers as A, parse_records as P, return_builder as B, tie_out as T
from agents.lib.secure_fs import secure_path, write_secure
from agents.lib.tax_computations import classify_misc_income, compute_schedule_1a
from tests.skill_text import SKILLS_DIR, skill_text
from tests.test_misc_income import source as misc_source
from tests.test_return_builder import _law, _template_1040


REFERENCE = SKILLS_DIR / 'tax-parse/references/tips-overtime.md'


def source_fields():
    rows = [line.split('|')[1:-1] for line in REFERENCE.read_text().splitlines()
            if line.startswith(('| W-2 |', '| 1099-NEC |', '| 1099-MISC |'))]
    assert len(rows) == 3
    return {row[0].strip(): [cell.strip() for cell in row[1:]] for row in rows}


@pytest.mark.parametrize('form,expected', [
    ('W-2', ['box 1', 'box 12 code TP', 'box 14b', 'box 12 code TT']),
    ('1099-NEC', ['box 1a', 'box 1b', 'box 1c', 'box 1d']),
    ('1099-MISC', ['box 3', 'box 13a', 'box 13b', 'box 14']),
])
def test_published_2026_fields_are_in_the_linked_source_contract(form, expected):
    # IRS2026 W-2 and Rev12/2026 NEC/MISC distinguish income, cash tips,
    # occupation CODE and ALREADY-qualified overtime premium, in this order.
    assert source_fields()[form] == expected
    assert '(references/tips-overtime.md)' in (SKILLS_DIR/'tax-parse/SKILL.md').read_text()


def test_canonical_rows_use_2026_fields_and_keep_the_2025_source_separate():
    text = (SKILLS_DIR/'tax-parse/SKILL.md').read_text()
    tips, = [r for r in text.splitlines() if '| `qualified_tips` ' in r]
    overtime, = [r for r in text.splitlines() if '| `qualified_overtime_premium` ' in r]
    gross, = [r for r in text.splitlines() if '| `self_employment_gross` ' in r]
    assert all(s in tips for s in ('TY2026', 'TP', 'NEC 1b', 'MISC 13a', 'TY2025'))
    assert all(s in overtime for s in ('TY2026', 'TT', 'NEC 1d', 'MISC 14', 'TY2025'))
    assert 'box 1 (TY2025)' in gross and 'box 1a (TY2026, already includes 1b/1d)' in gross


@pytest.mark.parametrize('form', ['W-2', '1099-NEC', '1099-MISC'])
def test_reviewed_source_subamounts_do_not_increase_income_or_withholding(form):
    # Synthetic reviewed source: gross40k includes tips8k + premium3k;
    # withheld1,234 is separate. Adding the subamounts would falsely make51k.
    fields = source_fields()[form]
    raw = dict(zip(fields, (40000, 8000, 'reviewed eligible occupation', 3000)))
    boxes = {'qualified_tips': raw[fields[1]], 'qualified_overtime_premium': raw[fields[3]]}
    income = 'wages' if form == 'W-2' else 'self_employment_gross'
    withheld = 'federal_withholding_w2' if form == 'W-2' else 'federal_withholding_1099'
    boxes.update({income: raw[fields[0]], withheld: 1234})
    rec = dict(doc_id='source', file='documents/source.pdf', form_type=form,
        payer='Synthetic payer', person='primary', key_boxes=boxes)
    if form == '1099-MISC':
        src = misc_source(2026, box_3=40000, box_3_business=40000, box_3_other=0,
            box_4=1234, box_5=0, box_6=0, box_7=False)
        rec['misc_income'] = src
        assert classify_misc_income(src) == {income: 40000, withheld: 1234}
    ledger = T.load_ledger({'records': [rec]})
    result = {'meta': {'tax_year': 2026}, 'form_1040': {
        'line_1a_wages': 40000 if form == 'W-2' else 0,
        'line_25a_w2_withholding': 1234 if form == 'W-2' else 0,
        'line_25b_1099_withholding': 0 if form == 'W-2' else 1234},
        'schedule_c': {'line_1_gross_receipts': 0 if form == 'W-2' else 40000}}
    report = T.evaluate(ledger, result, ['documents/source.pdf'])
    assert not report.failed
    totals = {check.category: check.ledger_total for check in report.sum_checks}
    assert totals[income] == 40000
    assert totals[withheld] == 1234
    assert any('qualified_tips' in n for n in report.advisories)
    assert any('qualified_overtime_premium' in n for n in report.advisories)


def test_reported_premium_is_not_divided_again_and_deductions_stay_below_agi(tmp_path):
    # Synthetic limits avoid any dependence on a year's fetched caps.
    limits = {'tips_cap': 50000, 'overtime_cap': {'mfj': 50000},
              'tips_overtime_phaseout_start': {'mfj': 1000000}}
    sheet = compute_schedule_1a('MFJ', 2026, 40000, limits,
        qualified_tips=8000, overtime_premium=3000)
    assert sheet['tips']['allowed'] == 8000
    assert sheet['overtime']['allowed'] == 3000  # TT already IS the premium
    assert sheet['total'] == 11000
    secure_path(tmp_path)
    for folder in ('documents', 'summaries'):
        (tmp_path/folder).mkdir()
        secure_path(tmp_path/folder)
    write_secure(tmp_path/'documents/source.pdf', b'%PDF synthetic wage source')
    P.write_record(tmp_path, dict(doc_id='source', file='documents/source.pdf',
        form_type='W-2', payer='Synthetic payer', person='primary',
        key_boxes={'wages': 40000, 'federal_withholding_w2': 1234,
                   'qualified_tips': 8000, 'qualified_overtime_premium': 3000}))
    P.write_ledger(tmp_path)
    lines = _template_1040()
    # Leave the other deduction components zero to isolate the below-AGI input.
    lines['line_13b_schedule_1a_deductions'] = sheet['total']
    result = {'meta': {'tax_year': 2026, 'filing_status': 'MFJ'},
        'form_1040': lines, 'schedule_1a': sheet, 'schedule_d': {
            'part_i_short_term': {'line_7_net_short_term': 0},
            'part_ii_long_term': {'line_15_net_long_term': 0}}}
    for name, data in (('result.json', result), ('tax_law_2026.json', _law()),
            ('taxpayer_profile.json', {'tax_year': 2026, 'filing_status': 'MFJ'})):
        write_secure(tmp_path/name, json.dumps(data))
    assert B._main(['--workspace', str(tmp_path)]) == 0
    built = json.loads((tmp_path/'result.json').read_text())['form_1040']
    assert built['line_1a_wages'] == built['line_9_total_income'] == built['line_11_agi'] == 40000
    assert built['line_10_adjustments'] == 0
    assert built['line_14_total_deductions'] == 11000
    assert built['line_15_taxable_income'] == 29000
    assert built['line_25a_w2_withholding'] == built['line_25d_total_withholding'] == 1234


@pytest.mark.parametrize('provision,question,flag', [
    ('tips', 'tips_source_review', 'has_qualified_tips'),
    ('overtime', 'overtime_source_review', 'has_qualified_overtime'),
])
@pytest.mark.parametrize('field', ['raw', 'allowed', 'disallowed_by_cap', 'disallowed_by_phaseout'])
def test_a_workpaper_source_review_cannot_disappear_with_a_false_intake_flag(provision, question, flag, field):
    entry = next(a for a in A.REQUIRED_ANSWERS if a.id == question)
    # Even a zero allowed deduction can contain a source requiring review.
    inputs = A._Inputs(Path('.'), {'flags': {flag: False}},
                       {'schedule_1a': {provision: {field: 100}}})
    assert entry.when(inputs)


def test_overtime_only_run_does_not_owe_a_tip_occupation_answer(tmp_path):
    entries = {a.id: a for a in A.REQUIRED_ANSWERS}
    inputs = A._Inputs(tmp_path, {'flags': {'has_qualified_overtime': True}}, {})
    assert entries['overtime_source_review'].when(inputs)
    assert not entries['tips_source_review'].when(inputs)
    assert entries['overtime_source_review'].kind == A.FACT
    assert 'does not require a tipped-occupation code' in entries['overtime_source_review'].question


@pytest.mark.parametrize('evidence', ['intake', 'workpaper'])
def test_source_review_gate_blocks_unanswered_facts_and_accepts_recorded_reviews(tmp_path, evidence):
    secure_path(tmp_path)
    write_secure(tmp_path/'taxpayer_profile.json', json.dumps({'tax_year': 2026,
        'flags': {'has_qualified_tips': evidence == 'intake', 'has_qualified_overtime': evidence == 'intake'}}))
    if evidence == 'workpaper':
        write_secure(tmp_path/'result.json', json.dumps({'schedule_1a': {
            'tips': {'raw': 100, 'allowed': 0}, 'overtime': {'raw': 100, 'allowed': 0}}}))
    text = ('- **[document_set_complete]** yes\n- **[corrected_documents]** none\n'
            '- **[charitable_gifts]** none\n- **[state_local_taxes_paid_outside_withholding]** none\n')
    write_secure(tmp_path/'answers.md', text)
    args = ['--workspace', str(tmp_path), '--check-required', '--for', 'tax-compute']
    assert A._main(args) == 2
    write_secure(tmp_path/'answers.md', text + '- **[tips_source_review]** reviewed\n'
                 '- **[overtime_source_review]** reviewed\n')
    assert A._main(args) == 0


def test_qualification_and_manual_limits_remain_part_of_the_loaded_skill():
    text = skill_text('tax-parse')
    for invariant in ('Sole `000`', 'mixed eligible/ineligible occupations', 'Missing/unknown codes',
        'Mandatory service charges', 'Form4137', 'NET-income cap', 'se_tips_deducted',
        'TY2025 fallback only', 'do not divide these amounts again', 'code TA',
        'income already captured on the return', 'overtime does not require a TTOC',
        'tip regulations reserve the SSTB issue'):
        assert invariant in text, invariant
