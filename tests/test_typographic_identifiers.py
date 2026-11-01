"""AA101: typographic separators, with no real taxpayer documents or values."""
import csv
from itertools import product
from pathlib import Path

import pytest

from agents.lib import parse_utils as P
from agents.lib.secure_fs import identifier_spans, mask_ssn, mask_table, names_an_identifier, redact, split_spans
from tests.test_parse_utils import LAST4, MASKED, ROUTING, _ssn, _workbook

HYPHENS = ("\u00ad", "\u2010", "\u2011", "\ufe63", "\uff0d")
AMBIGUOUS = ("\u02d7", "\u2012", "\u2013", "\u2014", "\u2015", "\u2043", "\u2212", "\u2796", "\ufe58")
MARKS = HYPHENS + AMBIGUOUS


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("runs", [None, ()])
def test_typographic_separators_mask_under_labels(mark, runs):
    # Exercise both the plain path (CSV/Excel) and the positional PDF path.
    for gaps in product(("", " ", "\u00a0"), repeat=4):
        value = f"000{gaps[0]}{mark}{gaps[1]}00{gaps[2]}{mark}{gaps[3]}{LAST4}"
        for label in ("SSN", "Recipient's TIN", "Employee's social security number"):
            for text in (f"{label}: {value}", f"{label}\n{value}"):
                assert mask_ssn(text, runs).endswith(MASKED)
            assert mask_table([[label, value]], runs) == [[label, MASKED]]
            assert mask_table([[label], [value]], runs) == [[label], [MASKED]]


@pytest.mark.parametrize("mark", HYPHENS)
@pytest.mark.parametrize("runs", [None, ()])
def test_typographic_hyphens_mask_without_labels(mark, runs):
    assert mask_ssn(f"Reference {_ssn(mark)}", runs) == f"Reference {MASKED}"


@pytest.mark.parametrize("mark", AMBIGUOUS)
@pytest.mark.parametrize("runs", [None, ()])
def test_ranges_and_subtractions_need_a_label(mark, runs):
    for gap in ("", " ", "\u00a0"):
        value = f"125{gap}{mark}{gap}50{gap}{mark}{gap}2025"
        for text in (f"Range {value}", f"Loss ${value}") + ((f"SSN Dividends {value}",) if gap else ()):
            assert mask_ssn(text, runs) == text
            assert mask_table([[text]], runs) == [[text]]
        # Even straight after SSN this ambiguous figure cannot close the box.
        text = f"SSN {value}\n{_ssn('')}"
        assert mask_ssn(text, runs).endswith(f"\n{MASKED}")
        assert identifier_spans(f"SSN {value}")[0] == [(0, 3, False)]
        assert mask_table([[f"SSN {value}"], [_ssn("")]], runs)[1] == [MASKED]


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("runs", [None, ()])
@pytest.mark.parametrize("label", ["Social security number (SSN): ", "SSN (primary): ", "SSN no.: ", "Taxpayer SSN - ",
                                   "SSN is ", "Recipient's TIN - ", "ITIN #: ", "SSN on file: "])
def test_a_tight_mark_reaches_as_far_as_the_bare_spelling(mark, runs, label):
    # Label wording before the value is the commonest same-line layout.
    assert mask_ssn(f"{label}{_ssn(mark)}", runs) == f"{label}{MASKED}"
    assert mask_table([[f"{label}{_ssn(mark)}"]], runs) == [[f"{label}{MASKED}"]]


@pytest.mark.parametrize("mark", ["-", *MARKS])
@pytest.mark.parametrize("runs", [None, ()])
@pytest.mark.parametrize("label", ["Social security number (SSN): ", "Social security number (SSN) ", "ITIN #: ", "SSN.: ",
                                   "SSN no.: ", "SSN (primary): ",
                                   "SSN on file: ", "SSN = ", "SSN is "])
def test_a_split_value_passes_wording_that_introduces_it(mark, runs, label):
    assert mask_ssn(f"{label}{_ssn(' ' + mark + ' ')}", runs) == f"{label}{MASKED}"


@pytest.mark.parametrize("runs", [None, ()])
@pytest.mark.parametrize("text", ["SSN Dividends 250 - 15 - 1200", "SSN ($100- 25- 1000)", "fee per ITIN: $125.50. 2025",
                                  "SSN this 250 - 15 - 1200", "Taxpayer SSN - 600 - 10 - 2000",
                                  "SSN and other wording that runs far too long: 600 - 10 - 2000",
                                  "Taxpayer SSN \u2014 600 \u2013 10 \u2013 2000"])
def test_a_figure_a_label_does_not_introduce_stays(runs, text):
    assert mask_ssn(text, runs) == text


@pytest.mark.parametrize("runs", [None, ()])
@pytest.mark.parametrize("first, second", [("-", "\u2010"), ("\u2011", "-"), ("\u2010", "\u2011"), ("\u00ad", "\uff0d")])
def test_two_different_hyphens_mask_without_a_label(runs, first, second):
    value = f"000{first}00{second}{LAST4}"
    assert mask_ssn(f"Reference {value}", runs) == f"Reference {MASKED}"
    assert mask_table([[f"Reference {value}"]], runs) == [[f"Reference {MASKED}"]]


@pytest.mark.parametrize("runs", [None, ()])
@pytest.mark.parametrize("first, second", [("\u2013", "-"), ("-", "\u2013"), ("\u2013", "\u2014"),
                                           ("\u2011", "-"), ("\u2212", "\u2013"), ("\u00ad", "\u2010")])
def test_two_different_dashes_mask_under_a_label(runs, first, second):
    # An OCR layer reads each edge on its own.
    for gap in ("", " "):
        value = f"000{gap}{first}{gap}00{gap}{second}{gap}{LAST4}"
        assert mask_ssn(f"SSN: {value}", runs) == f"SSN: {MASKED}"
        assert mask_ssn(f"SSN\n{value}", runs) == f"SSN\n{MASKED}"
        assert mask_table([["SSN", value]], runs) == [["SSN", MASKED]]
        assert redact(value) == "[REDACTED]"


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("runs", [None, ()])
def test_new_spellings_do_not_close_a_labels_reach(mark, runs):
    text = f"SSN {_ssn(mark)}\n{_ssn('')}"
    assert mask_ssn(text, runs).endswith(f"\n{MASKED}")
    assert identifier_spans(f"SSN {_ssn(mark)}")[0] == [(0, 3, False)]
    # Neither is it spelled for a positional reader, which reads it as a run instead.
    assert identifier_spans(f"SSN {_ssn(mark)}")[2] == []
    assert split_spans(f"SSN {_ssn(mark)}") == [(4, 4 + len(_ssn(mark)))]
    assert mask_table([[f"SSN {_ssn(mark)}"], [_ssn("")]], runs)[1] == [MASKED]


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("runs", [None, ()])
@pytest.mark.parametrize("prefix", ["", "SSN\n"])
def test_masking_a_typographic_value_cannot_hide_its_label(mark, runs, prefix):
    text = f"{prefix}Your social security number{_ssn(mark)}\t{' '.join(_ssn(''))}"
    assert mask_ssn(text, runs).endswith(f"\t{MASKED}")
    assert mask_table([[text]], runs)[0][0].endswith(f"\t{MASKED}")


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("runs", [None, ()])
def test_other_numeric_shapes_survive(mark, runs):
    for groups in (("01", "31", "2026"), ("2026", "01", "31"), ("415", "555", "0100")):
        for gap in ("", " "):
            value = (gap + mark + gap).join(groups)
            assert mask_ssn(f"SSN: {value}", runs) == f"SSN: {value}"
    for value in ("1" + _ssn(mark), _ssn(mark) + "1"):
        assert mask_ssn(f"SSN: {value}", runs) == f"SSN: {value}"


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("gap", ["", " ", "\u00a0"])
def test_errors_and_paths_redact_the_new_separators(mark, gap):
    assert redact(f"documents/{_ssn(gap + mark + gap)}.pdf") == "documents/[REDACTED].pdf"


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("gap", ["", " ", "\u00a0"])
def test_a_scan_does_not_disclose_an_identifier_filename(tmp_path, capsys, mark, gap):
    value = _ssn(gap + mark + gap)
    path = tmp_path / f"{value}.csv"
    path.write_text("Wages,850.00\n", encoding="utf-8")
    assert names_an_identifier(path.name)
    assert P.scan_workspace(str(tmp_path)) == []
    warning = capsys.readouterr().err
    assert value not in warning and "[REDACTED]" in warning
    assert "Rename" in warning


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("gap", ["", " "])
def test_a_sheet_name_masks_without_a_label(tmp_path, mark, gap):
    value = _ssn(gap + mark + gap)
    path = _workbook(tmp_path / "sheets.xlsx", title=value)
    assert list(P.extract_excel(str(path))) == [MASKED]


@pytest.mark.parametrize("mark", MARKS)
def test_colliding_sheet_names_refuse_instead_of_dropping_a_sheet(tmp_path, mark):
    import openpyxl
    path = _workbook(tmp_path / "sheets.xlsx", title=_ssn(mark))
    workbook = openpyxl.load_workbook(path)
    other = mark.join(("111", "11", LAST4))
    workbook.create_sheet(other).append(["Wages", 1234])
    workbook.save(path)
    workbook.close()
    with pytest.raises(ValueError, match="mask to the same name") as failure:
        P.extract_excel(str(path))
    assert _ssn(mark) not in str(failure.value) and other not in str(failure.value)


@pytest.fixture(scope="module")
def dash_font():
    """Embed a ToUnicode map for every test mark, including Vera's missing
    hyphens. Their glyph uses ASCII hyphen; extraction must keep its codepoint.
    This needs no installed system font and works on CI too."""
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    path = str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf")
    # Reproduce a full run's ordering: the PDF renderer registers Vera first.
    # ReportLab aliases fonts by face name, even with different fontName keys,
    # so our modified face must have its own identity to retain the new cmap.
    pdfmetrics.registerFont(TTFont("AA101UnmodifiedVera", path))
    font = TTFont("AA101Dash", path)
    font.face.name = b"AA101DashFace"
    for mark in MARKS:
        code = ord(mark)
        if code not in font.face.charToGlyph:
            font.face.charToGlyph[code] = font.face.charToGlyph[ord("-")]
            font.face.charWidths[code] = font.face.charWidths[ord("-")]
    pdfmetrics.registerFont(font)
    assert pdfmetrics.getFont(font.fontName) is font
    return font.fontName


def _pdf(path, rows, font):
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    table = Table(rows, colWidths=[240, 240])
    table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font),
                              ("GRID", (0, 0), (-1, -1), 0.5, "black")]))
    SimpleDocTemplate(str(path)).build([table])
    return path


def _read(route, path, workspace):
    options = {"identity_layout": True} if route == "identity_words" else {}
    reader = P.extract_pdf_words if route == "identity_words" else getattr(P, route)
    result = reader(str(path), workspace=str(workspace), **options)
    if route in {"extract_pdf_words", "identity_words"}:
        return "\n".join(" ".join(word["text"] for word in words) for words in result.values())
    return "\n".join(_strings(result))


def _strings(node):
    # Not str(result): a list's repr escapes U+00AD, so a whole SSN would pass as absent.
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        yield from (text for child in node.values() for text in _strings(child))
    elif isinstance(node, (list, tuple)):
        yield from (text for child in node for text in _strings(child))


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("gap", ["", " "])
@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words",
                                    "extract_tables", "extract_csv", "extract_excel"])
def test_every_extraction_route_masks_typographic_separators(tmp_path, dash_font, mark, gap, route):
    value = _ssn(gap + mark + gap)
    date = (gap + mark + gap).join(("01", "31", "2026"))
    rows = [["SSN", value], ["Date", date], ["Routing", ROUTING], ["Wages", "850.00"]]
    if route == "extract_csv":
        path = tmp_path / "dashes.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
    elif route == "extract_excel":
        path = _workbook(tmp_path / "dashes.xlsx", rows=rows)
    else:
        path = _pdf(tmp_path / "dashes.pdf", rows, dash_font)
        import pdfplumber
        with pdfplumber.open(path) as document:
            raw = document.pages[0].extract_text()
        assert value in raw, "the PDF fixture must preserve the Unicode separator"
    out = _read(route, path, tmp_path)
    assert value not in out
    assert MASKED in out
    assert all(value in out for value in (date, ROUTING, "850.00"))


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("gap", ["", " "])
@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables"])
def test_a_typographic_value_governed_only_by_pdf_position(tmp_path, dash_font, mark, gap, route):
    from reportlab.pdfgen import canvas
    import pdfplumber
    path = tmp_path / "positioned.pdf"
    page = canvas.Canvas(str(path), pagesize=(612, 792))
    page.setFont(dash_font, 7)
    page.drawString(36, 685, "SSN")
    # An intervening text row stops the text-only label-above rule, while the
    # value stays in the label's column and in its own table cell.
    page.setFont(dash_font, 5)
    page.drawString(570, 678, "Other")
    page.setFont(dash_font, 9)
    value = _ssn(gap + mark + gap)
    page.drawString(36, 667, value)
    page.drawString(180, 667, ROUTING)
    for x in (32, 164, 298):
        page.line(x, 648, x, 680)
    for y in (648, 680):
        page.line(32, y, 298, y)
    page.save()
    with pdfplumber.open(path) as document:
        raw = document.pages[0].extract_text()
    # Except a hyphen set tight, which masks with no label at all.
    assert (mark in HYPHENS and not gap) or value in mask_ssn(raw), "this fixture must need the positional reader"
    out = _read(route, path, tmp_path)
    assert value not in out and MASKED in out
    assert ROUTING in out


@pytest.mark.parametrize("mark", MARKS)
def test_positional_evidence_masks_other_copies_with_typographic_separators(mark):
    # A positionally governed bare copy must also mask its delimited twin.
    value = _ssn(mark)
    assert mask_ssn(f"Reference {value}", {_ssn("")}) == f"Reference {MASKED}"


@pytest.mark.parametrize("mark", MARKS)
def test_mismatched_marks_and_vertical_gaps_stay_out_of_scope(mark):
    for text in (f"SSN 000{mark}00/{LAST4}", f"SSN 000{mark}\n00{mark}{LAST4}", f"SSN 000.00/{LAST4}"):
        assert mask_ssn(text) == text
