"""AA88: digit glyphs interleaved with label letters must never leave a PDF read."""
import re

import pytest

from agents.lib.parse_utils import extract_pdf_text, extract_pdf_words, extract_tables
from tests.test_parse_utils import _SECOND, ROUTING, _draw, _ssn


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


def test_a_separate_nearby_run_is_split_off_the_overlay(tmp_path):
    """The run beside the overlay is cut off at its distinct gap and kept.
    Once the overlay goes, the label reads whole and governs it as its row's
    value, masked to its last four like any other."""
    import pdfplumber
    from agents.lib.parse_utils import _overlaid_digits
    pdf = _draw(tmp_path / 'nearby.pdf', [(200, 100, 9, 'SSN'), (200, 100, 9, _ssn('')),
                                          (260, 100, 9, ROUTING)])
    with pdfplumber.open(pdf) as document:
        chars = document.pages[0].chars
        hidden = _overlaid_digits(chars, lambda: None)
    assert ''.join(c['text'] for c in chars if id(c) in hidden) == _ssn('')
    text = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', text)) and ROUTING[-4:] in text


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


@pytest.mark.parametrize('label, value', [
    ('SSN', ''.join(('123', '456', '789', '012'))),            # twelve digits in one run
    ('SSN', ' '.join(('12', '3456', '7890'))),                 # one word to pdfplumber
    ('SSN', ' '.join((_ssn(''), '2025'))),                     # an SSN run into a year
    ('Your social security number', '94105'),                   # a ZIP
    ("RECIPIENT'S TIN", ','.join(('1', '234.56'))),            # a misregistered amount
])
def test_other_digits_interleaved_with_a_label_go_with_it(tmp_path, label, value):
    """pdfplumber garbles them into the label's words already: they leave the
    page, nothing is refused, and the label reads whole again."""
    pdf = _draw(tmp_path / 'other.pdf', [(200, 100, 7, label), (205, 100, 9, value)])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert label in out
    assert ''.join(re.findall(r'[0-9]', value)) not in ''.join(re.findall(r'[0-9]', out))


@pytest.mark.parametrize('value', [
    '   '.join((_ssn(''), '2025')),             # an SSN, then a year a word apart
    '  '.join(_ssn('')),                        # comb digits, each a word of its own
])
def test_an_ssn_among_other_digits_on_its_label(tmp_path, value):
    """The overlay's digits read as pdfplumber's words of their own find the
    SSN wherever it sits among them, and leave the rest as printed."""
    pdf = _draw(tmp_path / 'among.pdf', [(200, 100, 7, 'Your social security number'), (200, 100, 9, value)])
    for out in (extract_pdf_text(str(pdf), workspace=str(tmp_path))[0],
                ' '.join(w['text'] for w in extract_pdf_words(str(pdf), workspace=str(tmp_path))[0])):
        assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


@pytest.mark.parametrize('label, value', [
    ("RECIPIENT'S TIN", 'XXX-XX-1001'),             # a payee copy's truncated TIN
    ('Your social security number', '01/15/2026'),  # a date
])
def test_values_grazing_a_label_from_the_next_line_print(tmp_path, label, value):
    """Set tight under its label, a value sits on a line of its own."""
    pdf = _draw(tmp_path / 'short.pdf', [(200, 100, 7, label), (205, 104.5, 9, value)])
    for out in (extract_pdf_text(str(pdf), workspace=str(tmp_path))[0],
                ' '.join(w['text'] for w in extract_pdf_words(str(pdf), workspace=str(tmp_path))[0])):
        assert ''.join(re.findall(r'[0-9]', value)) in ''.join(re.findall(r'[0-9]', out))


@pytest.mark.parametrize('sep', ['-', '/', '.', '\u2014', '_', ':'])
@pytest.mark.parametrize('dx', [0, 16])
def test_a_spelled_overlay_leaves_no_digit_behind(tmp_path, sep, dx):
    """Whatever glyph separates the overlay's digits is the overlay's, not
    the label's: left with the letters, it splits the label's words."""
    pdf = _draw(tmp_path / 'spelled.pdf', [(200, 100, 7, 'Your social security number'), (200 + dx, 100, 9, _ssn(sep))])
    for out in (extract_pdf_text(str(pdf), workspace=str(tmp_path))[0],
                ' '.join(w['text'] for w in extract_pdf_words(str(pdf), workspace=str(tmp_path))[0]),
                str(extract_tables(str(pdf), workspace=str(tmp_path)))):
        assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


@pytest.mark.parametrize('sep', ['', '-'])
def test_the_label_reads_whole_and_governs_its_own_value(tmp_path, sep):
    """The overlay's glyphs go, its delimiters with its digits: the label's
    own value printed beside it is masked by position like any other."""
    pdf = _draw(tmp_path / 'whole.pdf', [(100, 100, 7, 'Your social security number'), (100, 100, 9, sep.join((_SECOND[:3], _SECOND[3:5], _SECOND[5:]))),
                                         (200, 100, 9, _ssn(''))])
    text = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    digits = ''.join(re.findall(r'[0-9]', text))
    assert 'Your social security number' in text
    assert _SECOND not in digits and _ssn('') not in digits


@pytest.mark.parametrize('gaps', [[0.6] * 8, [0, 0, 0, 2.9, 0, 0, 0, 0], [1.5, 0, 2, 0, 0, 2.5, 0, 0]])
def test_an_overlay_reads_in_pdfplumbers_words(tmp_path, gaps):
    """Gaps narrower than pdfplumber's tolerance join one word on the page,
    so they join one word here: split anywhere else, the digits would print
    in order and read as no SSN to the masker."""
    from reportlab.pdfgen import canvas
    pdf = tmp_path / 'gaps.pdf'
    page = canvas.Canvas(str(pdf), pagesize=(612, 792))
    page.setFont('Helvetica', 7)
    page.drawString(200, 692, 'Your social security number')
    page.setFont('Helvetica', 9)
    x = 200.0
    for digit, gap in zip(_ssn(''), gaps + [0]):
        page.drawString(x, 692, digit)
        x += page.stringWidth(digit, 'Helvetica', 9) + gap
    page.save()
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


@pytest.mark.parametrize('label', ['Your social security number', 'SSN'])
def test_an_ssn_split_wider_than_a_word_on_its_label(tmp_path, label):
    """Two words to pdfplumber, neither an SSN: nine digits between them, over
    the label or running on past its end ("SSN" ends under the first), are
    the label's SSN."""
    pdf = _draw(tmp_path / 'split.pdf', [(200, 100, 7, label),
                                         (205, 100, 9, _ssn('')[:4]), (232, 100, 9, _ssn('')[4:])])
    digits = ''.join(re.findall(r'[0-9]', extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]))
    assert _ssn('')[:4] not in digits and _ssn('')[4:] not in digits


def test_an_amount_running_on_past_a_short_value_on_a_label_stays(tmp_path):
    """A ZIP on the label goes with it; the amount after the label, clear of
    its letters, stays, eleven digits together being no SSN."""
    amount = ','.join(('1', '234.56'))
    pdf = _draw(tmp_path / 'runon.pdf', [(200, 100, 7, 'SSN'), (205, 100, 9, '94105'), (240, 100, 9, amount)])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert amount in out and '94105' not in ''.join(re.findall(r'[0-9]', out))


def test_a_word_crossing_its_label_takes_its_ssn_along(tmp_path):
    """The SSN ends before "SSN" begins; the year after it lands on the label.
    One word to pdfplumber, it breaks the label, so the SSN in it goes too."""
    pdf = _draw(tmp_path / 'year.pdf', [(258, 100, 9, 'SSN'), (200, 100, 9, '-'.join((_ssn(''), '2025')))])
    for out in (extract_pdf_text(str(pdf), workspace=str(tmp_path))[0],
                ' '.join(w['text'] for w in extract_pdf_words(str(pdf), workspace=str(tmp_path))[0])):
        assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


def test_a_line_joined_through_another_glyph_is_one_line(tmp_path):
    """pdfplumber chains a line's tops: digits more than its tolerance below
    the label's top still join the label's line through a glyph between, and
    the nine of them over it, however spaced, are its SSN."""
    pdf = _draw(tmp_path / 'chain.pdf', [(200, 100, 7, 'Your social security number'), (50, 102.5, 7, 'x'),
                                         (205, 105, 9, _ssn('')[:4]), (232, 105, 9, _ssn('')[4:])])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


def test_digits_grazing_a_label_from_the_next_line_print_apart(tmp_path):
    """Set tight under its label, an amount's glyphs touch the label's but
    sit on a line of their own, which pdfplumber never interleaves with it."""
    amount = ','.join(('12', '345', '678.90'))
    pdf = _draw(tmp_path / 'graze.pdf', [(200, 100, 7, "RECIPIENT'S TIN"), (205, 104.5, 9, amount)])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert amount in out


def test_a_label_keeps_its_own_slash(tmp_path):
    """"SSN/ITIN" after a line number: the slash has digits either side on
    its baseline, but too far off to be an overlay's, so the label still
    reads and the overlay on "ITIN" goes."""
    pdf = _draw(tmp_path / 'slash.pdf', [(200, 100, 9, '1 SSN/ITIN'), (228, 100, 9, _ssn(''))])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


def test_an_overlay_on_the_rest_of_its_caption(tmp_path):
    """1095-B/C's "(b) SSN or other TIN": the label's phrase runs on past
    "SSN", and an overlay on "or other TIN" is the label's."""
    pdf = _draw(tmp_path / 'tail.pdf', [(200, 100, 7, '(b) SSN or other TIN'), (232, 100, 9, _ssn(''))])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))


def test_overlap_with_the_possessive_part_of_a_caption(tmp_path):
    pdf = _draw(tmp_path / 'caption.pdf', [(200, 100, 7, 'Your social security number'),
                                           (174, 100, 6, _ssn(''))])
    out = extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))
