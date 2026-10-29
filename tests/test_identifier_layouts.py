"""Official blank IRS, FTB and NY words and field rectangles; no taxpayer documents.

identifier_layouts.json is rebuilt by build_identifier_layouts.py with the
locked pdfplumber from the source URL and page named in each record. Words
are [text,x0,top,x1,bottom]; `fields`, where a test types an SSN, are [field
name,x0,top,x1,bottom]; `typed` is [text,x0,top,x1,bottom], what else the
filer types. Synthetic values sit inside the form's own fields, without
changing any heading or instruction geometry.
"""
import json
from pathlib import Path

import pytest

from agents.lib.parse_utils import _governed_runs, extract_pdf_text, extract_pdf_words
from agents.lib.secure_fs import mask_ssn, mask_table
from tests.test_parse_utils import _draw, ROUTING

LAYOUTS = json.loads((Path(__file__).parent / 'fixtures/prior_return/identifier_layouts.json').read_text(encoding='utf-8'))
BY_FORM = {Path(record['source']).stem: record for record in LAYOUTS}
FIELDS = {'f4506c': 2, 'it213_fill_in': 6, 'f1095a--2025': 5, 'f8332': 3, 'f1040sei--2025': 3, 'f1095c--2025': 2, '2025-593': 1}


def _words(record: dict) -> list[dict]:
    return [dict(zip(('text', 'x0', 'top', 'x1', 'bottom'), word)) for word in record['words']]


def _type(words: list[dict], text: str, box: list, top_aligned: bool = False) -> None:
    """Type `text` in a field: centred in its height, or at its top in a
    smaller font; nine digits 50 points wide, other words 5 a character."""
    _, x0, top, _, bottom = box
    y0, y1 = (top + 0.5, top + 6.5) if top_aligned else ((top + bottom) / 2 - 4, (top + bottom) / 2 + 5)
    x = x0 + 2
    for part in text.split(' '):
        width = 50 if part.isdigit() else 5 * len(part)
        words.append({'text': part, 'x0': x, 'x1': x + width, 'top': y0, 'bottom': y1})
        x += width + 3


def _value(i: int) -> str:
    return ''.join(('000', '00', str(1000 + i)))


@pytest.mark.parametrize('form', list(BY_FORM))
def test_every_identifier_in_the_official_layouts(form):
    record = BY_FORM[form]
    words = _words(record)
    for text, *box in record.get('typed', []):
        _type(words, text, [text, *box])
    values = [_value(i) for i in range(len(record['fields']))]
    for value, box in zip(values, record['fields']):
        _type(words, value, box)
    assert len(values) == FIELDS[form]
    assert _governed_runs(words) == set(values), 'every SSN, and no printed form number'


@pytest.mark.parametrize('form', ['f4506c', 'it213_fill_in'])
def test_each_field_alone_in_the_official_layout(form):
    """The sixth child is the first with an SSN; earlier rows stay blank.
    A repeated value elsewhere must not mask a positional miss by value."""
    record = BY_FORM[form]
    for i, box in enumerate(record['fields']):
        words = _words(record)
        _type(words, _value(i), box)
        assert _governed_runs(words) == {_value(i)}, f'field {i + 1}'


@pytest.mark.parametrize('label', ["1b. First taxpayer identification number",
                                   "2b. Spouse's taxpayer identification number",
                                   "2b. Spouse’s taxpayer identification number"])
def test_4506c_labels_in_text_and_cells(label):
    assert mask_ssn(f'{label}: {_value(0)}') == f'{label}: XXX-XX-1000'
    assert mask_table([[label, 'Amount'], [_value(0), 1234.56]])[1] == ['XXX-XX-1000', 1234.56]


@pytest.mark.parametrize('label,offset', [("1b. First taxpayer identification number", 18),
                                        ("2b. Spouse's taxpayer identification number", 18),
                                        ('SSN or ITIN', 140)])
def test_official_label_layouts_at_both_pdf_boundaries(tmp_path, label, offset):
    pdf = _draw(tmp_path / 'identifiers.pdf', [(200, 100, 9, label), (200, 100 + offset, 9, _value(0)),
                                             (36, 400, 9, f'Routing {ROUTING}')])
    for text in (extract_pdf_text(str(pdf))[0], ' '.join(w['text'] for w in extract_pdf_words(str(pdf))[0])):
        assert _value(0) not in text and 'XXX-XX-1000' in text
        assert ROUTING in text


@pytest.mark.parametrize('label', ['Employer taxpayer identification number', 'Payer taxpayer identification number',
                                   'Business taxpayer identification number', 'SSN or ITIN for reference'])
def test_unrelated_headings_keep_their_numbers(tmp_path, label):
    pdf = _draw(tmp_path / 'other.pdf', [(200, 100, 9, label), (36, 180, 9, 'Other information'),
                                       (200, 240, 9, ROUTING)])
    assert ROUTING in extract_pdf_text(str(pdf))[0]


def test_an_intervening_heading_stops_the_late_child_column(tmp_path):
    pdf = _draw(tmp_path / 'boundary.pdf', [(200, 100, 9, 'SSN or ITIN'), (200, 200, 9, 'Account number'),
                                          (200, 240, 9, ROUTING)])
    assert ROUTING in extract_pdf_text(str(pdf))[0]


def test_a_child_who_died_leaves_the_next_childrens_reach():
    """Schedule EIC line 2 asks for "Died" in the box of a child born and
    died without an SSN; the row's reach steps over it to the next child's."""
    record = BY_FORM['f1040sei--2025']
    words = _words(record)
    first, *rest = record['fields']
    _type(words, 'Died', first)
    values = [_value(i) for i in range(len(rest))]
    for value, box in zip(values, rest):
        _type(words, value, box)
    assert _governed_runs(words) == set(values)


def test_a_signature_line_value_typed_at_the_top_of_its_field():
    """Form 8332's field is taller than its value: one typed small at its top
    ends more than a label's height above the label under the line."""
    record = BY_FORM['f8332']
    words = _words(record)
    values = [_value(i) for i in range(len(record['fields']))]
    for value, box in zip(values, record['fields']):
        _type(words, value, box, top_aligned=True)
    assert _governed_runs(words) == set(values)
