"""AA108: table labels introduce a row without consuming the later table."""
import csv

import pytest

from agents.lib.secure_fs import mask_ssn, mask_table
from tests.test_identifier_residuals import _prints
from tests.test_identifier_spellings import _cid_pdf, _comb, _wide
from tests.test_parse_utils import MASKED, ROUTING, _ssn, _workbook
from tests.test_typographic_identifiers import _read


@pytest.mark.parametrize("runs", [None, ()])
@pytest.mark.parametrize("split", range(1, 9))
@pytest.mark.parametrize("mark", [None, "-"])
def test_a_header_introduces_only_the_next_rows_crossing_comb(runs, split, mark):
    value = " ".join(_ssn("")) if mark is None else _comb(mark)
    # Split by digit count, including boundaries next to printed marks.
    boundary = [i for i, c in enumerate(value) if c.isdigit()][split]
    cells = [value[:boundary].rstrip(), value[boundary:]]
    flags = ["Flags", *(i % 2 for i in range(14))]
    rows = [["Name", "Has SSN", "Other"], ["Person", *cells], flags, ["Wages", "850.00", ROUTING]]
    out = mask_table(rows, runs)
    assert not _prints(_ssn("")[:5], " ".join(out[1]))
    assert out[0] == rows[0] and out[2:] == rows[2:]


@pytest.mark.parametrize("gap", [[""], [None], ["Dependent"], ["", "Dependent"]])
@pytest.mark.parametrize("value", [_comb(), " ".join(_ssn("")), _wide(_comb())])
def test_a_whole_comb_past_other_cells_still_belongs_to_its_label(gap, value):
    row = ["SSN", *gap, value, "Wages 850.00", ROUTING]
    out = mask_table([row])[0]
    assert out == [*row[:1 + len(gap)], MASKED, *row[2 + len(gap):]]


@pytest.mark.parametrize("split", range(1, 11))
@pytest.mark.parametrize("mark", ["-", "\u2013", "/", "."])
def test_a_printed_group_separator_can_cross_a_cell(split, mark):
    value = _ssn(mark)
    row = ["SSN " + value[:split], value[split:], "Wages 850.00", ROUTING]
    out = mask_table([row])[0]
    assert not _prints(_ssn("")[:5], " ".join(out))
    assert out[2:] == row[2:]


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel", "extract_tables"])
def test_above_value_comb_layout_through_table_extraction(tmp_path, route):
    digits = _ssn("")
    rows = [["SSN", ""], [" ".join(digits[:7]), " ".join(digits[7:])], ["Wages", "850.00"]]
    if route == "extract_csv":
        path = tmp_path / "header.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    elif route == "extract_excel":
        path = _workbook(tmp_path / "header.xlsx", rows)
    else:
        items = [(36, 705, "SSN"), (36, 680, rows[1][0]), (280, 680, rows[1][1]),
                 (400, 680, "850.00")]
        path = _cid_pdf(tmp_path / "header.pdf", mapped=set("".join(t for _, _, t in items)), items=items)
    out = _read(route, path, tmp_path)
    assert not _prints(digits[:5], out) and "850.00" in out


def test_a_header_cannot_consume_later_flags_or_unrelated_amount_columns():
    flags = [["Flags", *(i % 2 for i in range(14))] for _ in range(20000)]
    rows = [["Name", "Has SSN"], ["Person", "XXX-XX-1234"], *flags,
            ["SSN", "850", "00", "2025"]]
    assert mask_table(rows) == rows


def test_grouped_digits_need_an_introduction_inside_the_first_cell():
    first, second = _ssn("")[:3], _ssn("")[3:5] + " " + _ssn("")[5:]
    assert mask_table([["SSN " + first, second]]) == [["SSN XXX", "XX " + _ssn("")[5:]]]


@pytest.mark.parametrize("own", [_ssn("-"), _ssn(""), "XXX-XX-1234"], ids=["dashed", "plain", "masked"])
@pytest.mark.parametrize("value", [_wide(_ssn("")), _wide(_comb()), "\u200b".join(_ssn(""))])
def test_a_filled_label_still_protects_variant_text_and_table_values(own, value):
    label = "SSN " + own
    assert mask_ssn(label + "\n" + value).endswith(MASKED)
    assert mask_table([[label], [value], [ROUTING]])[-2:] == [[MASKED], [ROUTING]]
