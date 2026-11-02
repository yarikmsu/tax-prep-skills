"""AA102: source-preserving recognition of extracted identifier variants."""
import csv
from pathlib import Path
import re

import pytest

from agents.lib import parse_utils as P
from agents.lib.secure_fs import (
    identifier_spans, mask_row_runs, mask_runs, mask_ssn, mask_table, names_an_identifier, redact, split_spans)
from tests.test_parse_utils import LAST4, MASKED, ROUTING, _ssn, _workbook
from tests.test_typographic_identifiers import _pdf, _read


NEW_MARKS = ("\u2500", "\u2501", "\u2550", "\u30fc", "\u23af", "\u058a", "\u2e3a", "--", "(cid:17)")
INVISIBLE = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u200e", "\u202a", "\u2066", "\u034f", "\ufe0f")


def _wide(text):
    return text.translate(str.maketrans("0123456789", "０１２３４５６７８９"))


def _comb(mark="-"):
    return (" " + mark + " ").join(" ".join(part) for part in ("000", "00", LAST4))


VALUES = [_comb(), *(_ssn(mark) for mark in NEW_MARKS),
          *(char.join(_ssn("-")) for char in INVISIBLE),
          _wide(_ssn("-")), _wide(_ssn("")), _wide(_comb())]


@pytest.mark.parametrize("value", VALUES, ids=[f"variant-{i}" for i in range(len(VALUES))])
@pytest.mark.parametrize("runs", [None, ()])
def test_text_and_table_boundaries_mask_variants(value, runs):
    for prefix in ("SSN: ", "SSN\n", "Your social security number"):
        assert mask_ssn(prefix + value, runs) == prefix + MASKED
    assert mask_table([["SSN", value]], runs) == [["SSN", MASKED]]
    assert mask_table([["SSN"], [value]], runs) == [["SSN"], [MASKED]]
    assert mask_runs(value, {_ssn("")}) == MASKED
    assert names_an_identifier(value + ".csv")
    assert redact("documents/" + value + ".csv") == "documents/[REDACTED].csv"


def test_glued_ascii_label_uses_source_spans_without_an_unrelated_dash():
    text = f"Your social security number{_ssn('-')} / {' '.join('000' + '33' + '4444')}"
    assert mask_ssn(text) == "Your social security numberXXX-XX-" + LAST4 + " / XXX-XX-4444"


@pytest.mark.parametrize("value", VALUES, ids=[f"variant-{i}" for i in range(len(VALUES))])
def test_new_spellings_cannot_withdraw_an_existing_labels_reach(value):
    assert mask_ssn(f"SSN {value}\n{_ssn('')}").endswith("\n" + MASKED)


@pytest.mark.parametrize("value", [_comb(), _wide(_comb()), _ssn("(cid:17)")])
def test_positional_evidence_masks_variants_across_table_cells(value):
    cells = value.split(" ") if " " in value else [value[:3], value[3:]]
    result = mask_row_runs(cells, {_ssn("")})
    # The row keeps cell boundaries; its first five actual digits are gone.
    assert result != cells
    assert "000" not in " ".join(result)
    assert mask_table([["SSN", value]], {_ssn("")}) == [["SSN", MASKED]]


@pytest.mark.parametrize("value", [_comb(), _wide(_comb()), _ssn("(cid:17)")])
def test_source_offsets_after_normalization_do_not_touch_neighbouring_values(value):
    text = f"Café \u200e SSN: {value}; Wages 850.00; Routing {ROUTING}; ＡＢＣ １２３.４５"
    assert mask_ssn(text) == text.replace(value, MASKED)


def test_nine_digits_in_a_glyph_token_are_no_glyph_and_stay_text():
    """A glyph number fits 16 bits. Longer, it reads as the text it is."""
    token = "(cid:" + _ssn("") + ")"
    assert names_an_identifier(token + ".csv")
    assert _ssn("") not in redact(token)
    expected = "(cid:" + MASKED + ")"
    assert mask_ssn("SSN " + token) == "SSN " + expected
    assert mask_table([["SSN", token]]) == [["SSN", expected]]
    assert mask_table([["SSN", token + "; Reference " + _ssn("-")]], ()) == [
        ["SSN", expected + "; Reference " + MASKED]]
    assert mask_runs(token, {_ssn("")}) == expected


@pytest.mark.parametrize("text", ["Wages(cid:1)850(cid:1)00(cid:1)2025(cid:1)Box",
                                  "Rent(cid:1)$1,250(cid:1)00(cid:1)2025", _ssn("(cid:17)")])
def test_an_unmapped_glyph_needs_a_label(text):
    """As often the page's own space as a mark, so it reads as a mark set apart."""
    assert mask_ssn(text) == text
    assert mask_table([[text]]) == [[text]]


def test_an_unmapped_glyph_masks_with_a_label_or_a_known_run():
    value = _ssn("(cid:17)")
    assert mask_ssn("SSN: " + value) == "SSN: " + MASKED
    assert mask_runs("Box " + value, {_ssn("")}) == "Box " + MASKED


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables", "extract_csv", "extract_excel"])
def test_literal_cid_tokens_do_not_return_nine_digits(tmp_path, variant_font, route):
    token = "(cid:" + _ssn("") + ")"
    rows = [["SSN", token], ["Wages", "850.00"]]
    if route == "extract_csv":
        path = tmp_path / "literal.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
    elif route == "extract_excel":
        path = _workbook(tmp_path / "literal.xlsx", rows=rows)
    else:
        path = _pdf(tmp_path / "literal.pdf", rows, variant_font)
    out = _read(route, path, tmp_path)
    assert _ssn("") not in out and MASKED in out
    assert "850.00" in out


@pytest.mark.parametrize("value", [_comb(), _wide(ROUTING), "\u200b".join(ROUTING)])
def test_unlabelled_spaced_and_bare_numbers_keep_their_original_text(value):
    text = f"Routing {value}; Wages 850.00; Date 01--31--2026"
    assert mask_ssn(text) == text
    assert mask_table([[text]]) == [[text]]


WRAPPED = [_ssn("-\n"), "000\n00\n" + LAST4, "\n".join(_ssn("")),
           "0 0 0 -\n0 0 -\n" + " ".join(LAST4)]


@pytest.mark.parametrize("value", WRAPPED, ids=[f"wrapped-{i}" for i in range(len(WRAPPED))])
def test_wrapped_values_mask_without_changing_page_row_count(value):
    text = "SSN: " + value + "\nWages 850.00"
    out = mask_ssn(text)
    assert MASKED in out and out.count("\n") == text.count("\n")
    assert out.endswith("\nWages 850.00")
    assert mask_table([["SSN", value]])[0][1].startswith(MASKED)
    assert mask_runs(value, {_ssn("")}).startswith(MASKED)
    assert names_an_identifier(value + ".csv")
    assert redact(value) == "[REDACTED]"


@pytest.fixture(scope="module")
def variant_font():
    """Real PDF ToUnicode mappings; no installed font or downloaded fixture."""
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font = TTFont("AA102Variants", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
    font.face.name = b"AA102VariantsFace"
    for char in "".join(NEW_MARKS) + "".join(INVISIBLE) + "０１２３４５６７８９":
        code = ord(char)
        ascii_char = str(code - ord("０")) if "０" <= char <= "９" else "-"
        if code not in font.face.charToGlyph:
            font.face.charToGlyph[code] = font.face.charToGlyph[ord(ascii_char)]
            font.face.charWidths[code] = font.face.charWidths[ord(ascii_char)]
    pdfmetrics.registerFont(font)
    return font.fontName


@pytest.mark.parametrize("value", VALUES, ids=[f"variant-{i}" for i in range(len(VALUES))])
@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables", "extract_csv", "extract_excel"])
def test_variants_are_masked_on_every_extraction_route(tmp_path, variant_font, value, route):
    rows = [["SSN", value], ["Date", "01/31/2026"], ["Routing", ROUTING], ["Wages", "850.00"]]
    if route == "extract_csv":
        path = tmp_path / "variants.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
    elif route == "extract_excel":
        path = _workbook(tmp_path / "variants.xlsx", rows=rows)
    else:
        path = _pdf(tmp_path / "variants.pdf", rows, variant_font)
        import pdfplumber
        with pdfplumber.open(path) as doc:
            assert value in doc.pages[0].extract_text(), "fixture must extract its original spelling"
    out = _read(route, path, tmp_path)
    assert value not in out and MASKED in out
    assert ROUTING in out and "850.00" in out


@pytest.mark.parametrize("value", VALUES, ids=[f"variant-{i}" for i in range(len(VALUES))])
def test_variant_filenames_refuse_without_disclosing_the_name(tmp_path, capsys, value):
    path = tmp_path / (value + ".csv")
    path.write_text("Wages,850.00\n", encoding="utf-8")
    assert P.scan_workspace(str(tmp_path)) == []
    assert value not in capsys.readouterr().err


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables"])
def test_an_unmapped_pdf_glyph_extracts_as_cid_and_masks(tmp_path, variant_font, monkeypatch, route):
    """Drop a real PDF's mapping, rather than drawing the text `(cid:N)`."""
    from reportlab.pdfbase import ttfonts
    import pdfplumber
    original = ttfonts.makeToUnicodeCMap

    def unmapped(name, subset):
        cmap = original(name, subset)
        if ord("\u2500") in subset:
            cmap = re.sub(r"<[0-9A-Fa-f]+> <2500>\n", "", cmap)
            cmap = cmap.replace(f"{len(subset)} beginbfchar", f"{len(subset) - 1} beginbfchar")
        return cmap

    monkeypatch.setattr(ttfonts, "makeToUnicodeCMap", unmapped)
    path = _pdf(tmp_path / "unmapped.pdf", [["SSN", _ssn("\u2500")], ["Wages", "850.00"]], variant_font)
    with pdfplumber.open(path) as document:
        text = document.pages[0].extract_text()
    assert "(cid:" in text, "the real unmapped glyph must reach pdfminer's fallback"
    out = _read(route, path, tmp_path)
    assert MASKED in out and "850.00" in out


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables"])
def test_marked_comb_governed_only_by_position_masks(tmp_path, variant_font, route):
    from reportlab.pdfgen import canvas
    import pdfplumber
    path = tmp_path / "positioned.pdf"
    page = canvas.Canvas(str(path), pagesize=(612, 792))
    page.setFont(variant_font, 7)
    page.drawString(36, 685, "SSN")
    page.setFont(variant_font, 5)
    page.drawString(570, 678, "Other")
    page.setFont(variant_font, 9)
    value = _comb()
    page.drawString(36, 667, value)
    page.drawString(230, 667, "850.00")
    for x in (32, 216, 298):
        page.line(x, 648, x, 680)
    for y in (648, 680):
        page.line(32, y, 298, y)
    page.save()
    with pdfplumber.open(path) as document:
        text = document.pages[0].extract_text()
    assert value in mask_ssn(text), "this layout must require PDF position"
    out = _read(route, path, tmp_path)
    assert value not in out and MASKED in out and "850.00" in out


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words"])
def test_wrapped_pdf_value_masks_without_losing_row_alignment(tmp_path, variant_font, route):
    from reportlab.pdfgen import canvas
    path = tmp_path / "wrapped.pdf"
    page = canvas.Canvas(str(path))
    page.setFont(variant_font, 9)
    page.drawString(36, 700, "SSN: 000-")
    page.drawString(36, 684, "00-" + LAST4)
    page.drawString(36, 650, "Wages 850.00")
    page.save()
    out = _read(route, path, tmp_path)
    assert MASKED in out and "850.00" in out


@pytest.mark.parametrize("value", VALUES, ids=[f"variant-{i}" for i in range(len(VALUES))])
def test_identity_metadata_keeps_only_the_last_four_and_original_bounds(tmp_path, variant_font, value):
    path = _pdf(tmp_path / "identity.pdf", [["SSN", value], ["Date", "01/31/2026"]], variant_font)
    result = P.extract_pdf_words(str(path), workspace=str(tmp_path), identity_layout=True)
    identifiers = [identifier for row in result[0] for identifier in row.get("identity_identifiers", [])]
    assert identifiers and all(identifier["text"] == MASKED for identifier in identifiers)
    assert all(identifier["x0"] > 250 and identifier["x1"] > identifier["x0"] for identifier in identifiers)


# The view is additive. Reading "2222" and an invisible character and "3" as
# 22223 must not cost what the printed text shows: an identifier with a boundary.
TOUCHED = [_ssn("-"), _ssn("."), _ssn("/"), _ssn(""), f"000 00 {LAST4}", " ".join(_ssn(""))]
TOUCHES = ["​3", "﻿2025", "‍7", "３", "٣"]


@pytest.mark.parametrize("value", TOUCHED, ids=[f"value-{i}" for i in range(len(TOUCHED))])
@pytest.mark.parametrize("touch", TOUCHES, ids=[f"touch-{i}" for i in range(len(TOUCHES))])
def test_a_character_touching_a_value_does_not_hide_it(value, touch):
    for prefix in ("SSN ", "SSN\n"):
        assert mask_ssn(prefix + value + touch) == prefix + MASKED + touch
    assert mask_table([["SSN", value + touch]]) == [["SSN", MASKED + touch]]
    assert mask_table([["SSN"], [value + touch]]) == [["SSN"], [MASKED + touch]]
    assert mask_runs(value + touch, {_ssn("")}) == MASKED + touch
    assert mask_ssn(value + touch, {_ssn("")}) == MASKED + touch


@pytest.mark.parametrize("value", [_ssn("-"), _ssn("."), _ssn("/")], ids=["hyphen", "dot", "slash"])
@pytest.mark.parametrize("touch", ["2025​", "３", "٣"], ids=["zwsp", "fullwidth", "arabic"])
def test_a_character_touching_a_delimited_value_from_before_does_not_hide_it(value, touch):
    assert mask_ssn(touch + value) == touch + MASKED


def test_a_reader_still_sees_a_value_the_view_would_join():
    bare = _ssn("")
    _, values, _, _ = identifier_spans(f"SSN {bare}​5")
    assert (4, 4 + len(bare)) in values
    split = _ssn(" - ")
    assert (4, 4 + len(split)) in split_spans(f"SSN {split}​5")


@pytest.mark.parametrize("value", TOUCHED, ids=[f"value-{i}" for i in range(len(TOUCHED))])
@pytest.mark.parametrize("touch", TOUCHES, ids=[f"touch-{i}" for i in range(len(TOUCHES))])
def test_a_diagnostic_redacts_a_touched_value_and_a_filename_refuses(value, touch):
    assert value not in redact(f"documents/{value}{touch}.csv")
    assert names_an_identifier(f"{value}{touch}.csv")


def test_a_position_only_run_is_found_past_a_joining_character():
    """The view reads 2222 and 5 as one group, so the run needs the printed text."""
    spaced = f"000 00 {LAST4}"
    assert mask_runs(f"Box {spaced}​5", {_ssn("")}) == f"Box {MASKED}​5"
    assert mask_row_runs([f"{spaced}​5", "850.00"], {_ssn("")}) == [f"XXX XX {LAST4}​5", "850.00"]


def test_a_label_only_the_view_reads_labels_its_cell():
    bare = _ssn("")
    assert mask_table([["S\u200bSN", bare]]) == [["S\u200bSN", MASKED]]
    assert mask_table([["S\u200bSN"], [bare]]) == [["S\u200bSN"], [MASKED]]


def test_a_known_run_in_any_digits_reads_as_its_digits():
    """A reader hands over the source's spelling, so the run must read through it."""
    wide = _wide(_ssn(""))
    assert mask_runs(wide, {wide}) == MASKED
    assert mask_runs(_ssn(""), {wide}) == MASKED
    assert mask_runs(f"Box {_ssn(' ')}", {"\u200b".join(_ssn(""))}) == f"Box {MASKED}"


def test_a_label_the_view_would_join_to_a_word_still_labels():
    bare = _ssn("")
    assert mask_ssn(f"A​SSN {bare}") == f"A​SSN {MASKED}"
    assert mask_table([["A​SSN"], [bare]]) == [["A​SSN"], [MASKED]]
    assert mask_table([["A​SSN", bare]]) == [["A​SSN", MASKED]]
    labels, _, _, _ = identifier_spans(f"A​SSN {bare}")
    assert [(start, end) for start, end, _ in labels] == [(2, 5)]


def test_a_mask_ends_at_its_digit_and_keeps_an_invisible_character_after_it():
    """Taking it would print the next digit straight after the last four."""
    assert mask_ssn(f"SSN {_ssn('-')}​") == f"SSN {MASKED}​"
    assert mask_ssn(f"​SSN ​{_ssn('-')}​2025") == f"​SSN ​{MASKED}​2025"


def test_a_run_inside_a_glyph_token_reaches_no_text_past_it():
    """The printed reading's span sits inside the token the view folds to one."""
    for tail in (" Wages 850.00 and enough further words", "xx850.00 Wages"):
        assert mask_runs("(cid:12345)" + tail, {"12345"}) == "(cid:12345)" + tail


# Nine digits across a line break, each piece a figure of its own.
NOT_WRAPPED = ["SSN ending 1234\n12345 Main St", "SSN: XXXXX1234\n12345 Main St",
               f"SSN: *****{LAST4}\n94105", "SSN\n1040\n12345", "SSN\n1234\n12345",
               "Your social security number\n2025\n12345 Main St",
               "Employee's social security number 12345\n6789 Main St",
               "Recipient's TIN Wages 1000\n20000.50 Fed", "Spouse's SSN\n20250\n1040",
               "SSN\n1,100\n20\n3000", "SSN\n$100\n20\n3000", "SSN\n100\n20\n3000.50",
               "SSN\n100\n20\n3000,50", "SSN: XX123\n45\n6789", "SSN: ***123\n45\n6789",
               # Past a label's reach on its own row, a figure wraps onto the next.
               f"SSN {MASKED} Box 1 Wages 850\n00\n2025",
               "SSN and a run of words that goes on past any label's reach 100\n20\n3000",
               # Sharing a line, an unmarked break is an amount over the next row.
               "PAYER'S TIN RECIPIENT'S TIN 1 Interest income\n12-3456789 $ 850 00\n2025 Form 1099-INT",
               "Your social security number 850 00\n1234 Main St", "Recipient's TIN\n$ 850 00\n1099-INT",
               "RECIPIENT'S TIN\n1 2 3\n4 5 6\n7 8 9", "SSN\n100\n20\n3000 Main St",
               "SSN\n" + "\n".join(map(str, range(1, 11))),
               # Each piece ends its line and one line holds two of them.
               "Your social security number 850 00\n2025", "SSN\n850 00\n2025", "SSN\n850\n00 2025",
               # The ninth box shares its line.
               "SSN\n" + "\n".join(map(str, range(1, 9))) + "\n9 Main St"]


@pytest.mark.parametrize("text", NOT_WRAPPED, ids=[f"figures-{i}" for i in range(len(NOT_WRAPPED))])
def test_figures_across_a_line_break_are_no_identifier(text):
    assert mask_ssn(text) == text
    assert mask_table([[text]]) == [[text]]


@pytest.mark.parametrize("value", ["1040\n12345", "12345\n6789", "1234\n12345"])
def test_figures_in_a_labels_cell_are_no_wrapped_identifier(value):
    assert mask_table([["SSN", value]]) == [["SSN", value]]
    assert mask_table([["SSN"], [value]]) == [["SSN"], [value]]


@pytest.mark.parametrize("value", [f"000\n00\n{LAST4}", f"000 -\n00 - {LAST4}", f"000.\n00.{LAST4}", f"000/00/\n{LAST4}",
                                   "0 0 0 -\n0 0 -\n" + " ".join(LAST4), "\n".join(_ssn(""))])
def test_a_break_beside_a_mark_or_with_each_group_alone_masks(value):
    rows = "\n" * value.count("\n")
    assert mask_ssn("SSN: " + value) == "SSN: " + MASKED + rows
    assert mask_ssn("SSN\n" + value) == "SSN\n" + MASKED + rows


def test_a_digit_ending_a_row_does_not_take_a_combs_place():
    comb = " ".join(_ssn(""))
    assert mask_ssn(f"Employee's social security number Box 1\n{comb}").endswith(f"Box 1\n{MASKED}")
    assert mask_table([["SSN"], [f"Box 1\n{comb}"]])[1][0] == f"Box 1\n{MASKED}"
    for text in (f"SSN\n1\n{comb}", f"Spouse's SSN\nForm 1040 Page 2\n{comb} Jane"):
        assert set(re.findall(r"XXX-XX-[0-9X]+", mask_ssn(text))) <= {MASKED}, "no other last four"


def test_a_value_past_a_short_comb_is_still_the_labels():
    """The comb keeps no digit, so nothing stops the label's reach at it."""
    comb = " ".join(_ssn("") + "5")
    assert mask_ssn(f"Spouse's SSN: {comb} # {_ssn(' - ')}") == f"Spouse's SSN: XXX-XX-XXXX # {MASKED}"


def test_a_value_only_the_view_reads_is_a_target_and_fills_no_box():
    wide = _wide(_ssn(""))
    assert identifier_spans(wide)[1] == []
    assert split_spans(wide) == [(0, 9)]


def test_a_unicode_figure_under_a_label_leaves_the_value_on_its_row_masked(tmp_path, variant_font):
    from reportlab.pdfgen import canvas
    path = tmp_path / "under.pdf"
    page = canvas.Canvas(str(path), pagesize=(612, 792))
    page.setFont(variant_font, 9)
    for x, top, text in ((36, 100, "SSN"), (60, 100, "850.00"), (110, 100, _ssn("")), (36, 112, _wide("000" + "33" + "4444"))):
        page.drawString(x, 792 - top, text)
    page.save()
    for route in ("extract_pdf_text", "extract_pdf_words"):
        out = _read(route, path, tmp_path)
        assert _ssn("") not in out and "850.00" in out


@pytest.mark.parametrize("name", [f"5\u200b{_comb()}.csv", f"{_ssn('-' + chr(10))}\u200b5.csv",
                                  f"000-\n\uff10\uff10-{LAST4}.csv"])
def test_a_filename_refuses_a_touched_comb_or_wrapped_value(name):
    assert names_an_identifier(name)


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words", "extract_tables",
                                   "extract_csv", "extract_excel"])
@pytest.mark.parametrize("touch", ["​3", "３"], ids=["zwsp", "fullwidth"])
def test_a_touched_value_masks_on_every_route(tmp_path, variant_font, route, touch):
    rows = [["SSN", _ssn("-") + touch], ["Wages", "850.00"]]
    if route == "extract_csv":
        path = tmp_path / "touched.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
    elif route == "extract_excel":
        path = _workbook(tmp_path / "touched.xlsx", rows=rows)
    else:
        path = _pdf(tmp_path / "touched.pdf", rows, variant_font)
    out = _read(route, path, tmp_path)
    assert _ssn("-") not in out and MASKED in out and "850.00" in out
