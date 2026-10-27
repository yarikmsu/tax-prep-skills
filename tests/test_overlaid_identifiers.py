"""AA88: digit glyphs interleaved with label letters must never leave a PDF read."""
import re

import pytest

from agents.lib.parse_utils import extract_pdf_text, extract_pdf_words, extract_tables
from tests.test_parse_utils import ROUTING, _draw, _ssn


@pytest.mark.parametrize('size', [6, 7, 8, 9, 10, 12])
@pytest.mark.parametrize('dx', [-8, 0, 8, 16, 24])
@pytest.mark.parametrize('dy', [-6, -3, 0, 3, 6])
def test_overlapping_label_grid(tmp_path, size, dx, dy):
    value = _ssn('')
    pdf = _draw(tmp_path / 'overlay.pdf', [(200, 100, 7, 'Your social security number'),
                                           (200 + dx, 100 + dy, size, value), (36, 200, 9, 'Other information'), (36, 300, 9, f'Routing {ROUTING}')])
    text = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    words = extract_pdf_words(str(pdf), workspace=str(tmp_path))[0]
    for out in (text, ' '.join(w['text'] for w in words)):
        assert value not in ''.join(re.findall(r'[0-9]', out))
        assert ROUTING in out


@pytest.mark.parametrize('label', ["Recipient's TIN", 'SSN', 'ITIN', 'Your identifying number'])
def test_other_identifier_labels_and_source_page_are_protected(tmp_path, label):
    import pdfplumber
    from agents.lib.parse_utils import _safe_pdf_page
    pdf = _draw(tmp_path / 'overlay.pdf', [(200, 100, 9, label), (200, 100, 9, _ssn(''))])
    original = pdf.read_bytes()
    with pdfplumber.open(pdf) as document:
        page = document.pages[0]
        before = [dict(c) for c in page.chars]
        safe = _safe_pdf_page(page, lambda: None)
        assert safe is not page
        assert page.chars == before
        assert sum(c['text'].isdigit() for c in safe.chars) == 0
    assert pdf.read_bytes() == original


def test_a_business_identifier_or_wage_label_is_not_an_ssn(tmp_path):
    pdf = _draw(tmp_path / 'business.pdf', [(36, 100, 9, 'Employer identification number'),
                                            (36, 100, 9, ROUTING), (300, 100, 9, 'Social security wages'),
                                            (300, 100, 9, '85000')])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert ROUTING in ''.join(re.findall(r'[0-9]', out))
    assert '85000' in ''.join(re.findall(r'[0-9]', out))


def test_tables_use_the_sanitized_characters_too(tmp_path):
    from reportlab.pdfgen import canvas
    pdf = tmp_path / 'table.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(612, 792))
    for y in (600, 630, 660):
        c.line(100, y, 500, y)
    for x in (100, 300, 500):
        c.line(x, 600, x, 660)
    c.setFont('Helvetica', 9)
    c.drawString(110, 640, 'Your social security number')
    c.drawString(110, 640, _ssn(''))
    c.drawString(310, 610, ROUTING)
    c.save()
    tables = extract_tables(str(pdf), workspace=str(tmp_path))
    out = str(tables)
    assert tables and _ssn('') not in ''.join(re.findall(r'[0-9]', out))
    assert ROUTING in out


def test_a_separate_nearby_financial_run_is_preserved(tmp_path):
    pdf = _draw(tmp_path / 'nearby.pdf', [(200, 100, 9, 'SSN'), (200, 100, 9, _ssn('')),
                                          (260, 100, 9, ROUTING)])
    text = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', text))
    assert ROUTING in text


def test_repeated_labels_index_each_word_once(tmp_path):
    import pdfplumber
    from agents.lib.parse_utils import _overlaid_digits
    visits = []
    for n in (20, 40):
        pdf = _draw(tmp_path / f'labels{n}.pdf', [(36, 100, 6, '/'.join(['SSN'] * n)),
                                                 (36, 100, 6, _ssn(''))])
        calls = []
        with pdfplumber.open(pdf) as document:
            assert _overlaid_digits(document.pages[0].chars, lambda: calls.append(1))
        visits.append(len(calls))
    assert visits[1] < 3 * visits[0]


def test_the_overlap_index_refuses_before_exceeding_its_memory_budget(tmp_path, monkeypatch):
    import pdfplumber
    from agents.lib import parse_utils
    monkeypatch.setattr(parse_utils, 'MAX_OVERLAY_INDEX_ENTRIES', 1)
    pdf = _draw(tmp_path / 'index.pdf', [(36, 100, 9, 'SSN'), (36, 100, 9, _ssn(''))])
    with pdfplumber.open(pdf) as document, pytest.raises(parse_utils.ParseBudgetError, match='index exceeds'):
        parse_utils._overlaid_digits(document.pages[0].chars, lambda: None)


def test_a_normal_copy_cannot_exempt_an_overlapping_copy(tmp_path):
    pdf = _draw(tmp_path / 'copies.pdf', [(200, 100, 7, 'Your social security number'),
                                          (200, 100, 9, _ssn('')), (36, 200, 9, 'Other information'),
                                          (200, 300, 9, 'SSN'), (230, 300, 9, _ssn(''))])
    for out in (extract_pdf_text(str(pdf), workspace=str(tmp_path))[0],
                str(extract_pdf_words(str(pdf), workspace=str(tmp_path)))):
        assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


def test_a_preceding_financial_amount_is_preserved(tmp_path):
    pdf = _draw(tmp_path / 'preceding.pdf', [(200, 100, 9, 'SSN'), (200, 100, 9, _ssn('')),
                                             (170, 100, 9, '1234')])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert '1234' in out
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


def test_an_ambiguous_overlay_refuses_instead_of_erasing_an_amount(tmp_path):
    pdf = _draw(tmp_path / 'ambiguous.pdf', [(200, 100, 9, 'SSN'), (200, 100, 9, '12345')])
    with pytest.raises(ValueError, match='ambiguous numeric overlay'):
        extract_pdf_text(str(pdf), workspace=str(tmp_path))


def test_overlap_with_the_possessive_part_of_a_caption(tmp_path):
    pdf = _draw(tmp_path / 'caption.pdf', [(200, 100, 7, 'Your social security number'),
                                           (174, 100, 6, _ssn(''))])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))
