"""AA107 task 1: table boundaries must not split identifier protection."""
import csv

import pytest

from agents.lib.parse_utils import extract_csv, extract_excel
from agents.lib.secure_fs import mask_ssn, mask_table
from tests.test_identifier_residuals import _prints
from tests.test_identifier_spellings import _cid_pdf, _wide
from tests.test_parse_utils import MASKED, ROUTING, _ssn, _workbook
from tests.test_typographic_identifiers import _read


@pytest.mark.parametrize("split", range(1, 9))
@pytest.mark.parametrize("runs", [None, (), "known"])
@pytest.mark.parametrize("wide", [False, True])
def test_a_labelled_comb_masks_across_cells_without_positional_evidence(split, runs, wide):
    digits = _wide(_ssn("")) if wide else _ssn("")
    cells = ["SSN " + " ".join(digits[:split]), " ".join(digits[split:]), None, "Wages 850.00", ROUTING]
    evidence = {digits} if runs == "known" else runs
    out = mask_table([cells], evidence)[0]
    expected = "X" * 5 + digits[5:]
    assert out == ["SSN " + " ".join(expected[:split]), " ".join(expected[split:]), *cells[2:]], \
        "a table split must mask the first five digits and retain each cell"


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "identity_words",
                                    "extract_tables", "extract_csv", "extract_excel"])
def test_a_real_table_with_a_seven_two_comb_split_masks(tmp_path, route):
    digits = _ssn("")
    cells = ["SSN " + " ".join(digits[:7]), " ".join(digits[7:])]
    if route == "extract_csv":
        path = tmp_path / "split.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows([cells, ["Wages", "850.00"]])
    elif route == "extract_excel":
        path = _workbook(tmp_path / "split.xlsx", [cells, ["Wages", "850.00"]])
    else:
        # The comb starts outside the label's positional beside reach. Text
        # still governs it, while the grid cuts seven digits from the last two.
        items = [(36, 700, "SSN"), (180, 700, " ".join(digits[:7])),
                 (280, 700, " ".join(digits[7:])), (36, 680, "Wages"), (280, 680, "850.00")]
        path = _cid_pdf(tmp_path / "split.pdf", mapped=set("".join(t for _, _, t in items)), items=items)
    out = _read(route, path, tmp_path)
    assert "850.00" in out
    assert "0 0 0 0 0" not in out, "the first five identifier digits escaped across cells"


def test_an_unlabelled_split_comb_and_other_figures_stay_readable():
    digits = _ssn("")
    rows = [["Routing " + " ".join(digits[:7]), " ".join(digits[7:])],
            ["SSN Dividends " + " - ".join(("250", "15", "1200")), "Wages 850.00", ROUTING]]
    assert mask_table(rows) == rows


def test_a_label_cannot_join_amount_columns_into_an_identifier():
    # A table preserves the boundary between an amount, its cents and a year.
    # Only a comb, whose digits are individual boxes, may span those columns.
    rows = [["SSN", "850", "00", "2025"], ["Payer", "125", "50", "2026"]]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("runs", [None, (), "known"])
@pytest.mark.parametrize("wide", [False, True])
@pytest.mark.parametrize("layout", ["beside", "right", "column"])
def test_a_completed_comb_cannot_absorb_the_next_numeric_cell(runs, wide, layout):
    digits = _wide(_ssn("")) if wide else _ssn("")
    comb = " ".join(digits)
    if layout == "beside":
        rows, expected = [["SSN " + comb, "2"]], [["SSN " + MASKED, "2"]]
    elif layout == "right":
        rows, expected = [["SSN", comb, "2"]], [["SSN", MASKED, "2"]]
    else:
        rows, expected = [["SSN", "Count"], [comb, "2"]], [["SSN", "Count"], [MASKED, "2"]]
    evidence = {digits} if runs == "known" else runs
    assert mask_table(rows, evidence) == expected, "a completed cell must leave the next count readable"


@pytest.mark.parametrize("wide", [False, True])
@pytest.mark.parametrize("layout", ["beside", "right", "column"])
def test_complete_cells_in_a_known_long_comb_keep_each_last_four(wide, layout):
    first, second = _ssn(""), "000" + "00" + "1111"
    digits = first + second
    if wide:
        digits = _wide(digits)
    first_cell, second_cell = " ".join(digits[:9]), " ".join(digits[9:])
    expected = [MASKED, "XXX-XX-1111", "Wages 850.00"]
    if layout == "beside":
        rows = [["SSN " + first_cell, second_cell, "Wages 850.00"]]
        expected[0] = "SSN " + MASKED
    elif layout == "right":
        rows = [["SSN", first_cell, second_cell, "Wages 850.00"]]
        expected.insert(0, "SSN")
    else:
        rows = [["SSN", "Other", "Wages"], [first_cell, second_cell, "Wages 850.00"]]
    out = mask_table(rows, {" ".join(digits)})
    assert out[-1] == expected, "the cell pass must preserve both known last fours"
    if layout == "column":
        assert out[0] == rows[0]


@pytest.mark.parametrize("wide", [False, True])
def test_a_subsequent_cell_pass_cannot_restore_an_already_masked_last_four_digit(wide):
    digits = _wide(_ssn("")) if wide else _ssn("")
    original = "SSN " + " ".join(digits)
    surviving = list("X" * 5 + digits[5:])
    surviving[-2] = "X"
    masked = "SSN " + " ".join(surviving)
    assert mask_ssn(masked, {" ".join(digits)}, _original=original) == "SSN " + MASKED[:-2] + "X" + MASKED[-1], \
        "source boundaries must never restore a digit that a row mask removed"


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel"])
def test_a_completed_comb_keeps_the_next_count_through_extraction(tmp_path, route):
    rows = [["SSN " + " ".join(_ssn("")), "2"], ["Wages", "850.00"]]
    if route == "extract_csv":
        path = tmp_path / "complete.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    else:
        path = _workbook(tmp_path / "complete.xlsx", rows)
    reader = extract_csv if route == "extract_csv" else extract_excel
    value = reader(str(path), workspace=str(tmp_path))
    table = value if route == "extract_csv" else next(iter(value.values()))
    assert table == [["SSN " + MASKED, "2"], ["Wages", "850.00"]], \
        "extraction must mask the identifier and leave the next count readable"


def test_only_a_row_a_comb_or_known_run_crosses_takes_the_row_pass(monkeypatch):
    # A spreadsheet has no positional runs, and most of its rows hold no comb:
    # reading each as one line would only repeat every cell's own reading.
    from agents.lib import secure_fs
    calls = []
    real = secure_fs.mask_row_runs
    monkeypatch.setattr(secure_fs, "mask_row_runs", lambda cells, *a, **k: calls.append(cells) or real(cells, *a, **k))
    digits = _ssn("")
    plain = [["SSN", " ".join(digits)], ["Wages", "850.00", "2025"], ["SSN", "850", "00", "2025"],
             ["Count", " ".join("1234"), " ".join("56789")]]
    for runs in (None, ()):
        assert mask_table(plain, runs) == [["SSN", MASKED], *plain[1:]]
    assert calls == []
    split = ["SSN " + " ".join(digits[:7]), " ".join(digits[7:])]
    mask_table([split])
    mask_table([["Wages", "850.00"]], {digits})
    assert calls == [split, ["Wages", "850.00"]]


def _flat(rows):
    return "\n".join(" | ".join("" if cell is None else str(cell) for cell in row) for row in rows)


def _second():
    """A dependent's synthetic number, distinct from `_ssn("")`."""
    return "000" + "52" + "1111"


@pytest.mark.parametrize("split", [None, *range(1, 9)])
@pytest.mark.parametrize("label", ["beside", "own cell"])
@pytest.mark.parametrize("runs", [None, ()])
def test_a_dependents_comb_after_a_whole_one_masks(split, label, runs):
    # Form 1040 prints every dependent's comb on one row. A whole identifier
    # in one cell keeps its boundary, and the comb after it is still read,
    # cut across cells or whole in a cell of its own.
    first, second = _ssn(""), _second()
    pieces = [" ".join(second)] if split is None else [" ".join(second[:split]), " ".join(second[split:])]
    row = ["SSN " + " ".join(first), *pieces] if label == "beside" else ["SSN", " ".join(first), *pieces]
    out = _flat(mask_table([row + ["Wages 850.00"]], runs))
    assert not _prints(second[:5], out) and not _prints(first[:5], out), "a dependent's comb escaped"
    assert "850.00" in out


def test_a_whole_comb_and_a_figure_after_it_in_a_longer_cell_mask_together():
    # Ten digits in one cell are no whole identifier: the row reads them on
    # into the next cell, as the text does, rather than printing a dependent.
    first, second = _ssn(""), _second()
    row = ["SSN " + " ".join(first) + " " + " ".join(second[:2]), " ".join(second[2:])]
    assert not _prints(second[2:], _flat(mask_table([row])))


def test_a_header_label_reads_no_comb_across_the_rows_under_it():
    # A header governs its column all the way down, so a row of flags or
    # counts beneath it is no identifier its label introduces.
    rows = [["Name", "Has SSN", "Note"], ["Alice", "XXX-XX-" + _ssn("")[5:], "x"],
            ["Month", *range(1, 13)], ["Flags", *(n % 2 for n in range(14))]]
    assert mask_table(rows) == rows


def test_a_dependents_comb_whole_in_its_cell_masks_only_itself():
    first, second = _ssn(""), _second()
    # An amount with its cents and a year, then an account number.
    figures = " Wages " + " ".join(("850", "00", "2025")) + " " + "".join(("000", "12", "3456"))
    row = ["SSN " + " ".join(first), " ".join(second) + figures]
    out = mask_table([row])[0]
    assert not _prints(second[:5], out[1]) and out[1].endswith(figures)


def test_a_flag_or_a_date_between_fragments_parts_them():
    from datetime import date
    digits = _ssn("")
    for between in (False, date(2025, 1, 31)):
        row = ["SSN", " ".join(digits[:4]), " ".join(digits[4:]), between, 2]
        assert mask_table([row]) == [["SSN", "X X X X", "X " + " ".join(digits[5:]), between, 2]]


def test_a_comb_inside_a_cell_joins_the_crossing_one_it_overlaps():
    # The row reads a spaced comb into the first digit of a soft-hyphen comb
    # in the next cell. Masking only the crossing comb left the other eight.
    digits = _ssn("")
    row = ["Recipient's TIN 1 2 3 4", " ".join("1234567"), "­".join(digits)]
    out = _flat(mask_table([row]))
    assert not _prints(digits[1:], out), "eight digits of an overlapping comb printed"


def test_an_irregular_known_spelling_never_restores_a_digit_the_row_masked():
    # A long run's spelling can carry digits other than each nine's last four.
    # The cell pass must not print a digit the row pass masked.
    first, second, third = _ssn(""), _second(), "000" + "11" + "2222"
    spaced = " ".join(first[:5]) + "  " + first[5] + "  " + " ".join(first[6:])
    run = spaced + " " + second[:2] + " " + second[2] + " " + second[3:6] + " " + second[6] + " " + second[7:]
    runs = {" ".join((third[:3], third[3:5], third[5:])) + " " + run, run}
    row = [" ".join((third[:3], third[3:5], third[5:7])), third[7:] + " " + run]
    count = iter(range(27))
    expected = ["".join(c if not c.isdigit() or next(count) % 9 >= 5 else "X" for c in cell) for cell in row]
    assert mask_table([row], runs) == [expected], "a masked digit was restored"


def test_a_comb_typed_one_number_per_cell_masks_and_a_number_parts_fragments():
    digits = _ssn("")
    row = ["SSN", *(int(c) for c in digits), 850.5]
    assert mask_table([row]) == [["SSN", *"XXXXX", *(int(c) for c in digits[5:]), 850.5]]
    # A two-digit figure between fragments is no comb box, on a page or here.
    parted = ["SSN " + " ".join("1234"), 55, " ".join("67890")]
    assert mask_table([parted]) == [parted]


def test_a_workbook_comb_typed_one_number_per_cell_masks_through_extraction(tmp_path):
    digits = _ssn("")
    path = _workbook(tmp_path / "comb.xlsx", [["SSN", *(int(c) for c in digits)], ["Wages", 850]])
    table = next(iter(extract_excel(str(path), workspace=str(tmp_path)).values()))
    assert table[0] == ["SSN", *"XXXXX", *(int(c) for c in digits[5:])]
    assert table[1][:2] == ["Wages", 850]


def test_a_label_on_the_row_reads_only_the_comb_it_introduces():
    # A label after a comb introduces nothing before it, as in the text.
    row = ["Wages 850.00", " ".join("1234"), " ".join("56789"), "SSN XXX-XX-" + _ssn("")[5:]]
    assert mask_table([row]) == [row]


def test_a_known_run_starting_where_the_row_pass_wrote_still_masks():
    # The row pass masks into the middle of a group; the next known run
    # starts there, mid-group in the original cell, but not in the masked one.
    first, second, third = "000" + "01" + "1111", "000" + "22" + "2222", "000" + "33" + "3333"
    run = first + second + third
    row = ["SSN", " ".join(run[1:10]), run[10:12], run[12:14], run[14:]]
    out = _flat(mask_table([row], {" ".join(run)}))
    assert not _prints(third[:5], out), "a known run printed after the row pass"
