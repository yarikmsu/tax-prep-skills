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
FIELDS = {'f2848': 2, 'f8821': 1, 'f4506c': 2, 'it213_fill_in': 6, 'f1095a--2025': 5, 'f8332': 3, 'f1040sei--2025': 3, 'f1095c--2025': 2, '2025-593': 1}


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


@pytest.mark.parametrize('form', ['f2848', 'f8821', 'f4506c', 'it213_fill_in'])
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
                                   "2b. Spouse’s taxpayer identification number",
                                   "2b. Spouses taxpayer identification number"])
def test_4506c_labels_in_text_and_cells(label):
    assert mask_ssn(f'{label}: {_value(0)}') == f'{label}: XXX-XX-1000'
    assert mask_table([[label, 'Amount'], [_value(0), 1234.56]])[1] == ['XXX-XX-1000', 1234.56]


@pytest.mark.parametrize('label', ['Taxpayer identification number(s)', 'TAXPAYER IDENTIFICATION NUMBER(S)'])
@pytest.mark.parametrize('value', [_value(0), ' '.join(('000', '00', '1000')), ' '.join(_value(0))])
def test_authorization_labels_in_text_and_cells(label, value):
    assert mask_ssn(f'{label}: {value}') == f'{label}: XXX-XX-1000'
    assert mask_ssn(f'{label}\n{value}') == f'{label}\nXXX-XX-1000'
    assert mask_table([[label, 'Amount'], [value, 1234.56]])[1] == ['XXX-XX-1000', 1234.56]


def test_the_authorization_heading_in_a_punctuation_normalized_label_view():
    from agents.lib.secure_fs import identifier_spans
    label = 'Taxpayer identification number s'
    assert identifier_spans(label)[0] == [(0, len(label), False)]


def test_an_overlay_on_the_authorization_heading(tmp_path):
    import pdfplumber
    from agents.lib.parse_utils import _safe_pdf_page
    pdf = _draw(tmp_path / 'authorization-overlay.pdf', [(200, 100, 9, 'Taxpayer identification number(s)'),
                                                       (200, 100, 9, _value(0))])
    with pdfplumber.open(pdf) as document:
        assert not any(c['text'].isdigit() for c in _safe_pdf_page(document.pages[0], lambda: None)[0].chars)


@pytest.mark.parametrize('form', ['f2848', 'f8821'])
def test_authorization_layouts_at_the_pdf_boundaries(tmp_path, form):
    from reportlab.pdfbase.pdfmetrics import getDescent, stringWidth
    from reportlab.pdfgen import canvas
    record = BY_FORM[form]
    words = _words(record)
    for i, box in enumerate(record['fields']):
        _type(words, _value(i), box)
    # A 2-7 EIN remains as printed under the same heading, on Form 2848.
    for text, *box in record.get('typed', []):
        _type(words, text, [text, *box])
    # Reconstruct the official word positions as glyphs so the extraction
    # boundary, rather than just the positional helper, must mask the fields.
    pdf = tmp_path / 'authorization.pdf'
    page = canvas.Canvas(str(pdf), pagesize=(612, 792))
    for word in words:
        size = word['bottom'] - word['top']
        text = page.beginText(word['x0'], 792 - word['bottom'] - getDescent('Helvetica') * size / 1000)
        text.setFont('Helvetica', size)
        text.setHorizScale(100 * (word['x1'] - word['x0']) / stringWidth(word['text'], 'Helvetica', size))
        # Official forms contain explicit space glyphs even when their
        # geometric gap is below pdfplumber's word tolerance.
        text.textOut(word['text'] + ' ')
        page.drawText(text)
    page.save()
    for out in (extract_pdf_text(str(pdf))[0], ' '.join(w['text'] for w in extract_pdf_words(str(pdf))[0])):
        for i in range(len(record['fields'])):
            assert _value(i) not in out and f'XXX-XX-{1000 + i}' in out
        assert all(text in out for text, *box in record.get('typed', []))


def _layout(label: str, offset: int, value: str) -> list[tuple]:
    """`value` `offset` points under `label`, a line off its column between
    them: page text's label-above rule stops there, so a late child's entry
    is masked by its column's reach or not at all."""
    return [(200, 100, 9, label), (36, 100 + offset / 2, 9, 'Other information'), (200, 100 + offset, 9, value)]


@pytest.mark.parametrize('label,offset', [("1b. First taxpayer identification number", 18),
                                        ("2b. Spouse's taxpayer identification number", 18),
                                        ('Taxpayer identification number(s)', 18),
                                        ('SSN or ITIN', 140)])
def test_official_label_layouts_at_both_pdf_boundaries(tmp_path, label, offset):
    pdf = _draw(tmp_path / 'identifiers.pdf', _layout(label, offset, _value(0)) + [(36, 400, 9, f'Routing {ROUTING}')])
    for text in (extract_pdf_text(str(pdf))[0], ' '.join(w['text'] for w in extract_pdf_words(str(pdf))[0])):
        assert _value(0) not in text and 'XXX-XX-1000' in text
        assert ROUTING in text


@pytest.mark.parametrize('label,offset', [('Employer taxpayer identification number', 18),
                                        ('Payer taxpayer identification number', 18),
                                        ('Business taxpayer identification number', 18),
                                        ('Taxpayer identification number', 18),
                                        ('Taxpayer identification numbers', 18),
                                        ('Taxpayer identification number(s)XYZ', 18),
                                        ('SSN or ITIN for reference', 140),
                                        ('Payer SSN or ITIN', 140)])
def test_unrelated_headings_keep_their_numbers(tmp_path, label, offset):
    """The layouts above, where their labels mask the value: only the heading differs."""
    pdf = _draw(tmp_path / 'other.pdf', _layout(label, offset, ROUTING))
    for text in (extract_pdf_text(str(pdf))[0], ' '.join(w['text'] for w in extract_pdf_words(str(pdf))[0])):
        assert ROUTING in text


def test_authorization_heading_keeps_a_hyphenated_ein():
    label, ein = 'Taxpayer identification number(s)', '00-0001003'
    assert mask_ssn(f'{label}\n{ein}') == f'{label}\n{ein}'
    assert mask_table([[label], [ein]]) == [[label], [ein]]


def test_an_authorization_heading_in_a_pdf_table(tmp_path):
    from reportlab.pdfgen import canvas
    from agents.lib.parse_utils import extract_tables
    pdf = tmp_path / 'authorization-table.pdf'
    page = canvas.Canvas(str(pdf), pagesize=(612, 792))
    for y in (600, 630, 660):
        page.line(100, y, 500, y)
    for x in (100, 350, 500):
        page.line(x, 600, x, 660)
    page.setFont('Helvetica', 9)
    page.drawString(110, 640, 'Taxpayer identification number(s)')
    page.drawString(360, 640, 'Amount')
    page.drawString(110, 610, _value(0))
    page.drawString(360, 610, '1234.56')
    page.save()
    tables = extract_tables(str(pdf))
    assert tables and _value(0) not in str(tables)
    assert 'XXX-XX-1000' in str(tables) and '1234.56' in str(tables)


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
