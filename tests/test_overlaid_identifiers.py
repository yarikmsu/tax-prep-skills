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


@pytest.mark.parametrize('head', ['(2) Social security', '(2) Identifying'])
@pytest.mark.parametrize('top', [100, 109])
@pytest.mark.parametrize('sep', ['', '-', '/'])
def test_overlays_on_either_row_of_a_wrapped_heading(tmp_path, head, top, sep):
    pdf = _draw(tmp_path / 'wrapped.pdf', [(200, 100, 7, head), (200, 109, 7, 'number'),
                                          (200, top, 9, _ssn(sep)), (36, 300, 9, f'Routing {ROUTING}')])
    for out in (extract_pdf_text(str(pdf))[0], str(extract_pdf_words(str(pdf)))):
        assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))
        assert ROUTING in out


def test_a_wrapped_heading_keeps_its_possessive_in_the_overlap_index(tmp_path):
    import pdfplumber
    from agents.lib.parse_utils import _overlaid_digits
    pdf = _draw(tmp_path / 'wrapped-prefix.pdf', [(200, 100, 7, 'Your social security'),
                                                 (200, 109, 7, 'number'), (174, 100, 6, _ssn(''))])
    with pdfplumber.open(pdf) as document:
        chars = document.pages[0].chars
        hidden = _overlaid_digits(chars, lambda: None)
        assert ''.join(c['text'] for c in chars if id(c) in hidden) == _ssn('')
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', extract_pdf_text(str(pdf))[0]))


@pytest.mark.parametrize('angle', [-90, -45, -15, -1, 1, 15, 45, 90, 180, 270])
@pytest.mark.parametrize('sep', ['', '-', '/', '.', '\u2014', '_'])
def test_rotated_overlays_read_on_their_own_baseline(tmp_path, angle, sep):
    import pdfplumber
    from agents.lib.parse_utils import _overlaid_digits
    pdf = _draw(tmp_path / 'rotated.pdf', [(200, 100, 9, 'Your social security number'),
                                          (208, 95, 9, _ssn(sep), angle), (36, 200, 9, 'Other information'),
                                          (36, 300, 9, f'Routing {ROUTING}')])
    with pdfplumber.open(pdf) as document:
        chars = document.pages[0].chars
        hidden = _overlaid_digits(chars, lambda: None)
        assert ''.join(c['text'] for c in chars if id(c) in hidden) == _ssn(sep)
    for out in (extract_pdf_text(str(pdf))[0], str(extract_pdf_words(str(pdf)))):
        assert _ssn('') not in ''.join(re.findall(r'[0-9]', out))
        assert ROUTING in out


@pytest.mark.parametrize('angle', [-90, -45, 45, 90, 180])
def test_rotated_split_groups_and_the_source_page(tmp_path, angle):
    import pdfplumber
    from agents.lib.parse_utils import _safe_pdf_page
    pdf = _draw(tmp_path / 'split-rotated.pdf', [(200, 100, 9, 'Your social security number'),
                                                (208, 95, 9, '  '.join((_ssn('')[:4], _ssn('')[4:])), angle)])
    original = pdf.read_bytes()
    with pdfplumber.open(pdf) as document:
        page = document.pages[0]
        before = [dict(c) for c in page.chars]
        safe = _safe_pdf_page(page, lambda: None)
        assert not any(c['text'].isdigit() for c in safe.chars)
        assert page.chars == before
    assert pdf.read_bytes() == original


@pytest.mark.parametrize('angle', [0, 45, 90])
def test_wrapped_or_rotated_overlays_leave_pdf_tables(tmp_path, angle):
    from reportlab.pdfgen import canvas
    pdf = tmp_path / 'wrapped-table.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(612, 792))
    for y in (600, 630, 680):
        c.line(100, y, 500, y)
    for x in (100, 300, 500):
        c.line(x, 600, x, 680)
    c.setFont('Helvetica', 7)
    c.drawString(120, 660, '(2) Social security')
    c.drawString(120, 651, 'number')
    c.saveState()
    c.translate(125, 651)
    c.rotate(angle)
    c.setFont('Helvetica', 9)
    c.drawString(0, 0, _ssn('-'))
    c.restoreState()
    c.setFont('Helvetica', 9)
    c.drawString(310, 610, ROUTING)
    c.save()
    tables = extract_tables(str(pdf))
    assert tables and ROUTING in str(tables)
    assert _ssn('') not in ''.join(re.findall(r'[0-9]', str(tables)))


def test_dense_statements_keep_cusips_and_accounts_without_indexing_their_letters(tmp_path, monkeypatch):
    import pdfplumber
    from agents.lib import parse_utils
    # Enough for the actual identifier label; indexing all the statement's
    # unrelated letters would exceed this budget long before its last row.
    monkeypatch.setattr(parse_utils, 'MAX_OVERLAY_INDEX_ENTRIES', 128)
    visits = []
    for count in (30, 60):
        rows = [(200, 25, 7, '(2) Social security'), (200, 34, 7, 'number'), (200, 34, 9, _ssn(''))]
        for n in range(count):
            top = 80 + n * 10
            rows.extend([(36, top, 7, 'CUSIP'), (36, top + 4.5, 9, ROUTING),
                         (300, top, 7, 'Account number'), (300, top + 4.5, 9, ROUTING)])
        pdf = _draw(tmp_path / f'dense{count}.pdf', rows)
        calls = []
        with pdfplumber.open(pdf) as document:
            page = document.pages[0]
            hidden = parse_utils._overlaid_digits(page.chars, lambda: calls.append(1))
            assert ''.join(c['text'] for c in page.chars if id(c) in hidden) == _ssn('')
        visits.append(len(calls))
        text = extract_pdf_text(str(pdf))[0]
        assert _ssn('') not in ''.join(re.findall(r'[0-9]', text))
        assert text.count(ROUTING) == 2 * count
    assert visits[1] < 3 * visits[0]


def _digits(text: str) -> str:
    return ''.join(re.findall(r'[0-9]', text))


@pytest.mark.parametrize('tilt', [0.3, 1, 2, 3])
@pytest.mark.parametrize('value', ['1234.56', '04/15/2025'])
def test_a_tilted_page_keeps_the_value_under_a_label(tmp_path, tilt, value):
    """A scan's text layer, drawn a few degrees off level: the value on the
    line under a label touches its letters without sharing its line. An
    SSN drawn over the same label is still masked."""
    label = [(200, 100, 9, 'Your social security number', tilt), (36, 300, 9, f'Routing {ROUTING}')]
    kept = _draw(tmp_path / 'kept.pdf', label + [(205, 108, 9, value, tilt)])
    assert _digits(value) in _digits(extract_pdf_text(str(kept))[0])
    masked = _draw(tmp_path / 'masked.pdf', label + [(205, 100, 9, _ssn(''), tilt)])
    assert _ssn('') not in _digits(extract_pdf_text(str(masked))[0])


@pytest.mark.parametrize('angle,dx,dy', [(-90, 0, -2), (180, 115, 0), (45, 110, 10)])
@pytest.mark.parametrize('value', ['04/15/2025', '1545-0008'])
def test_a_rotated_date_or_code_touching_a_label_keeps_its_digits(tmp_path, angle, dx, dy, value):
    """Only nine digits running on together are a label's SSN; a rotated
    date or form code that touches its letters reads as main read it."""
    def read(text):
        pdf = _draw(tmp_path / 'rotated.pdf', [(200, 100, 9, 'Your social security number'),
                                              (200 + dx, 100 + dy, 9, text, angle), (36, 200, 9, 'Other information')])
        return _digits(extract_pdf_text(str(pdf))[0])
    assert sorted(read(value)) == sorted(_digits(value))
    assert _ssn('') not in read(_ssn('-')) and _ssn('')[::-1] not in read(_ssn('-'))


@pytest.mark.parametrize('base,turns,x,decoy', [
    (0, [0, 0.0001] * 5, 160, None),
    (30, [0, 0.5] * 5, 200, None),
    (90, [0, 0.4] * 5, 200, None),
    (180, [0, 0.4] * 5, 245, None),
    (30, [0, 0.5] * 5, 200, -0.8),
    (30, [0.2 * i for i in range(9)], 200, None),
])
def test_glyphs_drawn_a_hair_apart_read_as_one_run(tmp_path, base, turns, x, decoy):
    """Digits drawn a fraction of a degree off their neighbours, or drifting
    1.6 degrees in all, are still one SSN, across a whole-degree boundary
    and the turn from 180 to -180 too, and a rotated digit elsewhere on the
    page just under their angle does not split them. A float's noise off
    level is the page's own, its first digits printed left of the label."""
    import pdfplumber
    from reportlab.pdfgen import canvas
    from agents.lib.parse_utils import _overlaid_digits
    pdf = tmp_path / 'jitter.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(612, 792))
    c.setFont('Helvetica', 9)
    c.drawString(200, 683, 'Your social security number')
    if decoy is not None:
        c.saveState()
        c.translate(400, 300)
        c.rotate(base + decoy)
        c.drawString(0, 0, '1')
        c.restoreState()
    c.translate(x, 685)
    c.rotate(base)
    for i, digit in enumerate(_ssn('')):
        c.saveState()
        c.translate(5 * i, 0)
        c.rotate(turns[i])
        c.drawString(0, 0, digit)
        c.restoreState()
    c.save()
    with pdfplumber.open(pdf) as document:
        chars = document.pages[0].chars
        hidden = _overlaid_digits(chars, lambda: None)
        assert sorted(c['text'] for c in chars if id(c) in hidden) == sorted(_ssn(''))


def test_a_rotated_value_garbling_a_label_leaves_it_to_govern_its_ssn(tmp_path):
    """Only the rotated date's glyph on the label's line goes: the label
    reads whole again and masks the SSN beside it."""
    pdf = _draw(tmp_path / 'garbled.pdf', [(200, 100, 9, 'Your social security number'), (220, 112, 9, '04/15/2025', 45),
                                          (330, 100, 9, _ssn('')), (36, 200, 9, 'Other information')])
    assert _ssn('') not in _digits(extract_pdf_text(str(pdf))[0])



@pytest.mark.parametrize('tilt', [1, 2, 3])
@pytest.mark.parametrize('split', [True, False])
def test_a_tilted_page_reads_a_grazing_split_run_as_a_level_page_does(tmp_path, tilt, split):
    """Digits on the line under a tilted label touch its letters without
    joining its words: the overlay pass leaves them as it would level."""
    import pdfplumber
    from agents.lib.parse_utils import _overlaid_digits
    value = '  '.join((_ssn('')[:4], _ssn('')[4:])) if split else _ssn(' ')

    def hidden(angle):
        pdf = _draw(tmp_path / f'graze{angle}.pdf', [(200, 100, 9, 'Your social security number', angle),
                                                    (205, 108, 9, value, angle)])
        with pdfplumber.open(pdf) as document:
            chars = document.pages[0].chars
            return ''.join(c['text'] for c in chars if id(c) in _overlaid_digits(chars, lambda: None))
    assert hidden(tilt) == hidden(0)


@pytest.mark.parametrize('label', ['Your social security number', 'Your favourite colour here'])
def test_a_chain_of_rotated_decoys_does_not_carry_off_an_ssn(tmp_path, label):
    """Digits elsewhere on the page, each a degree under the next from just
    off level up to a 90-degree SSN, put it in a frame read along their
    first baseline. A delimited SSN must stay masked with or without a label."""
    decoys = [(300 + 9 * (k % 30), 400 + 14 * (k // 30), 9, '1', 1 + 0.9 * k) for k in range(98)]
    pdf = _draw(tmp_path / 'decoys.pdf', [(200, 100, 9, label), (200, 112, 9, _ssn('-'), 90),
                                         (36, 200, 9, 'Other information')] + decoys)
    out = _digits(extract_pdf_text(str(pdf))[0])
    assert _ssn('') not in out and _ssn('')[::-1] not in out


@pytest.mark.parametrize('label_tilt,ssn_tilt,other', [(0, 1, (400, 500, 9, '7', 0.4)), (0, 1, (400, 500, 9, 'p.', 0.4)),
                                                       (0.5, 0.8, (400, 760, 9, 'Page 1.', 0.2)),
                                                       (0, -15, (400, 500, 9, '7', -15.5))])
def test_a_tilted_ssn_keeps_its_separators_beside_another_tilted_line(tmp_path, label_tilt, ssn_tilt, other):
    """An OCR text layer tilts each line its own way: a line elsewhere a
    fraction of a degree under the SSN's shares its frame, and the SSN,
    drifting off that frame's first baseline, still reads with its hyphens."""
    pdf = _draw(tmp_path / 'ocr.pdf', [(200, 100, 9, 'Your social security number', label_tilt),
                                      (230, 100, 9, _ssn('-'), ssn_tilt), other, (36, 200, 9, 'Other information')])
    for out in (extract_pdf_text(str(pdf))[0], ' '.join(w['text'] for w in extract_pdf_words(str(pdf))[0])):
        assert _ssn('') not in _digits(out)


@pytest.mark.parametrize('tilt', [0, 1])
@pytest.mark.parametrize('heading', ['wrapped', 'slash'])
def test_a_labels_own_punctuation_is_no_separator_on_a_tilted_page(tmp_path, tilt, heading):
    """A heading's ")" or a label's "/" a point and a half off the SSN's
    baseline, between two of its digits, stays with the label's letters."""
    items = ([(200, 100, 7, '(2) Social security', tilt), (200, 109, 7, 'number', tilt), (210, 101.5, 7, _ssn(''), tilt)]
             if heading == 'wrapped' else [(200, 100, 9, 'SSN/ITIN', tilt), (204, 101.5, 9, _ssn('-'), tilt)])
    pdf = _draw(tmp_path / 'punctuation.pdf', items + [(36, 200, 9, 'Other information')])
    for out in (extract_pdf_text(str(pdf))[0], ' '.join(w['text'] for w in extract_pdf_words(str(pdf))[0])):
        assert _ssn('') not in _digits(out)


def _assert_masked(pdf):
    """All PDF entry points see the same sanitized characters."""
    for out in (extract_pdf_text(str(pdf))[0],
                ' '.join(w['text'] for w in extract_pdf_words(str(pdf))[0]),
                str(extract_tables(str(pdf)))):
        assert _ssn('') not in _digits(out)
        assert _ssn('')[::-1] not in _digits(out)


@pytest.mark.parametrize('sep', ['', '-'])
def test_a_wrapped_heading_with_an_overlay_in_its_own_size(tmp_path, sep):
    pdf = _draw(tmp_path / 'caption-size.pdf', [(200, 100, 7, '(2) Social security'),
                                               (200, 109, 7, 'number'), (210, 100, 7, _ssn(sep))])
    _assert_masked(pdf)


@pytest.mark.parametrize('sep', ['-', '/', '_'])
@pytest.mark.parametrize('edge', ['upper', 'lower'])
def test_an_overlay_split_across_a_wrapped_headings_rows(tmp_path, sep, edge):
    first, rest = _ssn('')[:3], _ssn('')[3:]
    pdf = _draw(tmp_path / 'two-rows.pdf', [(200, 100, 7, '(2) Social security'),
                                          (200, 109, 7, 'number'),
                                          (210, 100, 9, first + (sep if edge == 'upper' else '')),
                                          (210, 109, 9, (sep if edge == 'lower' else '') + rest),
                                          (200, 125, 9, _SECOND)])
    _assert_masked(pdf)
    assert _SECOND not in _digits(extract_pdf_text(str(pdf))[0])


@pytest.mark.parametrize('angle', [-90, -30, 30, 90, 180])
@pytest.mark.parametrize('sep', ['', '-', '  '])
def test_a_label_and_overlay_rotated_together(tmp_path, angle, sep):
    import pdfplumber
    from agents.lib.parse_utils import _safe_pdf_page
    from reportlab.pdfgen import canvas
    pdf = tmp_path / 'turned-label.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(612, 792))
    c.translate(250, 400)
    c.rotate(angle)
    c.setFont('Helvetica', 9)
    c.drawString(0, 0, 'Your social security number')
    c.drawString(0, 0, _ssn(sep))
    c.save()
    _assert_masked(pdf)
    original = pdf.read_bytes()
    with pdfplumber.open(pdf) as document:
        page = document.pages[0]
        before = [dict(g) for g in page.chars]
        assert not any(g['text'].isdigit() for g in _safe_pdf_page(page, lambda: None).chars)
        assert page.chars == before
    assert pdf.read_bytes() == original


@pytest.mark.parametrize('skew', [-30, -15, 15, 30])
def test_an_oblique_overlay(tmp_path, skew):
    from reportlab.pdfgen import canvas
    pdf = tmp_path / 'oblique.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(612, 792))
    c.setFont('Helvetica', 9)
    c.drawString(200, 600, 'Your social security number')
    c.saveState()
    c.translate(205, 600)
    c.skew(0, skew)
    c.drawString(0, 0, _ssn('-'))
    c.restoreState()
    c.save()
    _assert_masked(pdf)


@pytest.mark.parametrize('angle', [-90, 90, 180])
def test_a_rotated_delimited_ssn_needs_no_label(tmp_path, angle):
    pdf = _draw(tmp_path / 'unlabelled.pdf', [(200, 300, 9, _ssn('-'), angle),
                                            (36, 500, 9, f'Routing {ROUTING}')])
    _assert_masked(pdf)
    assert ROUTING in extract_pdf_text(str(pdf))[0]


def test_a_wrapped_headings_value_beside_its_lower_word(tmp_path):
    pdf = _draw(tmp_path / 'lower-word.pdf', [(200, 100, 7, '(2) Social security'),
                                           (200, 109, 7, 'number'), (233, 109, 7, _ssn(''))])
    _assert_masked(pdf)


@pytest.mark.parametrize('sep', ['', '-'])
@pytest.mark.parametrize('start', [160, 170, 180])
def test_alternately_tilted_digits_beginning_left_of_the_label(tmp_path, sep, start):
    import pdfplumber
    from agents.lib.parse_utils import _overlaid_digits
    from reportlab.pdfgen import canvas
    pdf = tmp_path / 'alternating.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(612, 792))
    c.setFont('Helvetica', 9)
    c.drawString(200, 600, 'Your social security number')
    x = float(start)
    for i, glyph in enumerate(_ssn(sep)):
        c.saveState()
        c.translate(x, 600)
        c.rotate(0.4 if i % 2 else 0)
        c.drawString(0, 0, glyph)
        c.restoreState()
        x += c.stringWidth(glyph, 'Helvetica', 9)
    c.save()
    _assert_masked(pdf)
    with pdfplumber.open(pdf) as document:
        chars = document.pages[0].chars
        hidden = _overlaid_digits(chars, lambda: None)
        assert ''.join(g['text'] for g in chars if id(g) in hidden) == _ssn(sep)


@pytest.mark.parametrize('angle', [-90, -0.4, 0.4, 90, 180])
@pytest.mark.parametrize('value', [ROUTING, '12-3456789', '04/15/2025', '1234.56'])
def test_rotated_unlabelled_numbers_keep_their_digits(tmp_path, angle, value):
    pdf = _draw(tmp_path / 'kept-number.pdf', [(200, 300, 9, value, angle)])
    assert sorted(_digits(extract_pdf_text(str(pdf))[0])) == sorted(_digits(value))


@pytest.mark.parametrize('sep', ['', '-', ' '])
@pytest.mark.parametrize('label', ['Social security #', 'Your social security number'])
@pytest.mark.parametrize('ladder', [0, 6, 10])
def test_a_level_label_read_as_printed(tmp_path, label, sep, ladder):
    """Read as spacing, "#" is no label; and a ladder of glyphs a degree
    apart, from just under level, frames a level heading with them and reads
    it along their first baseline, its words on several rows. Read as
    printed, the label holds whichever way the spaced view misses it."""
    steps = [(380 + 15 * k, 600, 9, glyph, -0.9 * (k + 1)) for k in range(ladder) for glyph in 'Q-']
    pdf = _draw(tmp_path / 'printed.pdf', [(200, 100, 9, label), (206, 100, 9, _ssn(sep))] + steps)
    _assert_masked(pdf)


@pytest.mark.parametrize('sep', ['', ' '])
@pytest.mark.parametrize('size', [6, 7])
def test_a_captions_parentheses_hold_its_phrase(tmp_path, size, sep):
    """Spacing "(1)" and "(2)" opens gaps wider than the heading is tall, so
    "number" pairs with "Dependents" alone. As printed, the parentheses keep
    one phrase over it, and the overlay crossing "Dependents" goes."""
    pdf = _draw(tmp_path / 'caption.pdf', [(200, 100, 7, 'Dependents (1) First name (2) Social security'),
                                         (200, 109, 7, 'number'), (190, 96, size, _ssn(sep))])
    _assert_masked(pdf)


def _width(text: str) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    return stringWidth(text, 'Helvetica', 9)


@pytest.mark.parametrize('angle', [0.3, -2, 45, 90, 180])
@pytest.mark.parametrize('line', ['Voucher 1 04/15/2026', 'Pay date: 1 12.31.2025'])
def test_a_space_in_the_stream_ends_an_unlabelled_run(tmp_path, angle, line):
    """Overlay rows hold digits and marks only, so "1" runs into its date's
    first group, a delimited SSN's shape. The space between them in
    the PDF stream keeps them two words, rotated or not."""
    pdf = _draw(tmp_path / 'dated.pdf', [(200, 300, 9, line, angle), (36, 600, 9, 'Other information')])
    assert sorted(_digits(extract_pdf_text(str(pdf))[0])) == sorted(_digits(line))


@pytest.mark.parametrize('angle', [0.3, -2])
def test_a_tilted_ssn_on_one_line_keeps_its_last_four(tmp_path, angle):
    """Page text reads an SSN tilted along one of its lines whole and masks
    it; removing it as unreadable would drop its last four too."""
    pdf = _draw(tmp_path / 'tilted.pdf', [(60, 80, 9, 'Your social security number', angle),
                                        (60, 92, 9, _ssn('-'), angle)])
    text = extract_pdf_text(str(pdf))[0]
    assert _ssn('') not in _digits(text) and 'XXX-XX-6789' in text


@pytest.mark.parametrize('angle', [90, 180])
def test_a_page_holding_only_a_rotated_ssn(tmp_path, angle):
    """Every glyph leaves as the overlay: an empty page, not a refusal."""
    _assert_masked(_draw(tmp_path / 'lone.pdf', [(200, 300, 9, _ssn('-'), angle)]))


@pytest.mark.parametrize('label,value', [("Employee's SSN:", '12-3456789'), ('SSN:', '1,234.56'),
                                         ('Social security no.', '12/31/2025')])
def test_a_labels_own_punctuation_does_not_carry_its_value(tmp_path, label, value):
    """The ":" abuts both the label's last letter and, in the stream, the
    value's first digit, a row-edge separator's place. Crossing is the
    digits' to decide, so the value stays beside the label."""
    for k in range(12):
        x = 50 + 7.37 * k
        pdf = _draw(tmp_path / f'abutting{k}.pdf', [(x, 100, 9, label), (x + _width(label) + 1, 100, 9, value)])
        assert _digits(value) in _digits(extract_pdf_text(str(pdf))[0])


@pytest.mark.parametrize('label', ['SSN', 'Your social security number'])
@pytest.mark.parametrize('value,mark', [('$1,234.56', 'lead'), ('(1,234.56)', 'trail'), ('1,234.56%', 'trail')])
def test_a_values_own_mark_on_a_label_letter(tmp_path, label, value, mark):
    """Only the amount's "$" or ")" touches the label: its digits do not, so
    the amount stays, garbled into the label's word as main prints it."""
    core = value.strip('$()%')
    if mark == 'lead':
        x = 200 + _width(label) - 0.7 * _width(value[0])
    else:
        x = 200 - _width(value[:value.index(core) + len(core)]) - 0.3 * _width(value[-1])
    pdf = _draw(tmp_path / 'mark.pdf', [(200, 100, 9, label), (x, 100, 9, value)])
    assert _digits(core) in _digits(extract_pdf_text(str(pdf))[0])


@pytest.mark.parametrize('angle', [0.3, -2])
@pytest.mark.parametrize('dx', [0, 12])
def test_a_tilted_ssn_drawn_across_words_that_label_nothing(tmp_path, angle, dx):
    """"Payer's TIN" labels an EIN, so no label governs the SSN drawn over
    it; on its row, page text garbles it into the letters, where no mask
    finds it, so it goes as page text cannot read it. (Six points on, the
    label's apostrophe falls between two of its digits and reads as one of
    its separators, so the run is no SSN: a limit main shares.)"""
    pdf = _draw(tmp_path / 'across.pdf', [(200, 100, 7, "Payer's TIN", angle), (200 + dx, 100, 7, _ssn('-'), angle),
                                        (36, 300, 9, f'Routing {ROUTING}')])
    _assert_masked(pdf)
    assert ROUTING in extract_pdf_text(str(pdf))[0]


@pytest.mark.parametrize('dx', [0, 10, 30])
def test_a_steep_ssn_across_a_heading_that_labels_nothing(tmp_path, dx):
    """Thirty degrees off level, its glyphs rise further than a row:
    page text prints them a few to a line among the heading's words."""
    pdf = _draw(tmp_path / 'steep.pdf', [(200, 100, 7, "Recipient's taxpayer"), (200, 109, 7, 'identification number'),
                                       (200 + dx, 104, 9, _ssn('-'), -30)])
    _assert_masked(pdf)


_FILL = 'Interest income reported by the payer for the calendar year shown above, including amounts credited'


@pytest.mark.parametrize('angle', [1.5, 3])
@pytest.mark.parametrize('sep', ['-', '/'])
def test_a_tilted_ssn_among_full_width_lines(tmp_path, angle, sep):
    """Long lines tilted a degree or two run into one of page text's rows
    with their neighbours, and an SSN alone on its line prints among their
    letters, though nothing crosses its own box: its row decides."""
    items = [(40, 100 + 11 * k, 9, _FILL, angle) for k in range(6) if k != 3]
    _assert_masked(_draw(tmp_path / 'scan.pdf', items + [(40, 133, 9, _ssn(sep), angle), (200, 133, 9, 'Box 1', angle)]))


@pytest.mark.parametrize('angle', [0.5, 90, 180])
@pytest.mark.parametrize('split', [3, 4, 6, 7, 'every'])
def test_a_rotated_ssn_split_by_spaces(tmp_path, angle, split):
    """A space beside a separator is how OCR splits an SSN, and one between
    every glyph is letter spacing: neither makes two words of it, as a
    space between some of its digits but not others does."""
    value = _ssn('-')
    spelled = ' '.join(value) if split == 'every' else f'{value[:split]} {value[split:]}'
    _assert_masked(_draw(tmp_path / 'split.pdf', [(300, 400, 9, spelled, angle), (36, 600, 9, 'Other information')]))


@pytest.mark.parametrize('angle', [0.3, 1])
@pytest.mark.parametrize('line', ['Interest 125.50 $1000.00', 'Interest 125.50 -1000.00',
                                  'Interest 125.50 (1000.00)', 'Balance 125.50 /2026'])
def test_tilted_amounts_side_by_side_stay(tmp_path, angle, line):
    """Overlay rows spell every mark "-", so an amount and the "$" of the
    next read as an SSN split beside its separator. Page text masks that
    split only with the same hyphen, dot or slash twice, and so does the
    overlay: on a tilted scan these are two amounts."""
    pdf = _draw(tmp_path / 'amounts.pdf', [(72, 140, 11, line, angle), (36, 600, 9, 'Other information')])
    for out in (extract_pdf_text(str(pdf))[0], ' '.join(w['text'] for w in extract_pdf_words(str(pdf))[0])):
        assert _digits(out) == _digits(line)


@pytest.mark.parametrize('angle', [0.3, 15, 90])
@pytest.mark.parametrize('gap', [' ', '   '])
@pytest.mark.parametrize('split', [3, 4, 6, 7])
@pytest.mark.parametrize('sep', ['-', '.', '/'])
def test_a_tilted_ssn_split_beside_a_separator_goes(tmp_path, angle, gap, split, sep):
    """Across words, split wide enough to be two, the overlay reads the
    SSN as page text does, the same separator twice. Tilted 15 degrees
    page text prints a split's words a line apart, where no mask finds
    them, so a space in the stream still leaves the run unreadable: it
    goes whole."""
    value = _ssn(sep)
    _assert_masked(_draw(tmp_path / 'split.pdf', [(300, 400, 9, f'{value[:split]}{gap}{value[split:]}', angle),
                                                (36, 600, 9, 'Other information')]))


@pytest.mark.parametrize('lead', [9.5, 11])
def test_a_tilted_reference_under_tight_leading_keeps_its_last_four(tmp_path, lead):
    """Lines above and below overlap its box, not its row: page text reads
    it whole and masks it, its last four kept."""
    items = [(100, 100, 9, f'Reference {_ssn("-")} issued', 1),
             (100, 100 + lead, 9, 'Account summary and transaction detail', 1),
             (100, 100 - lead, 9, 'Statement period January through December', 1)]
    text = extract_pdf_text(str(_draw(tmp_path / 'lead.pdf', items)))[0]
    assert _ssn('') not in _digits(text) and 'XXX-XX-6789' in text


@pytest.mark.parametrize('angle', [35, -40])
@pytest.mark.parametrize('tracking', [2, 3])
def test_an_unlabelled_tracked_ssn_rising_across_rows(tmp_path, angle, tracking):
    """Tracked wide, a steep run keeps reading order and nothing crosses
    it, but each glyph rises past page text's row: a glyph or two to a
    row, where no mask finds it. (Tracked past a word's gap, each glyph is
    a word of its own and nothing reads as an SSN, as on a level page.)"""
    from reportlab.pdfgen import canvas
    pdf = tmp_path / 'tracked.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(612, 792))
    c.drawString(36, 100, 'Other information')
    c.translate(300, 400)
    c.rotate(angle)
    text = c.beginText(0, 0)
    text.setFont('Helvetica', 9)
    text.setCharSpace(tracking)
    text.textOut(_ssn('-'))
    c.drawText(text)
    c.save()
    _assert_masked(pdf)
