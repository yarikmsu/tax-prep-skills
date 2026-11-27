"""AA109: shifted, wrapped and interrupted table introductions."""
import csv

import pytest

from agents.lib.secure_fs import mask_table
from tests.test_identifier_residuals import _prints
from tests.test_identifier_spellings import _comb, _wide
from tests.test_parse_utils import _SECOND, MASKED, ROUTING, _ssn, _workbook
from tests.test_typographic_identifiers import _read


@pytest.mark.parametrize("surname", ["Wages", "Interest", "Date"])
@pytest.mark.parametrize("mark", [" ", "-", "\u00ad"])
@pytest.mark.parametrize("split", [2, 5, 7])
def test_a_surname_matching_a_caption_keeps_existing_comb_masks(surname, mark, split):
    digits = _ssn("")
    value = _comb(mark) if mark == "-" else mark.join(digits)
    at = [i for i, c in enumerate(value) if c.isdecimal()][split]
    row = ["SSN", surname, value[:at].rstrip(), value[at:]]
    out = mask_table([row])[0]
    assert out[:2] == row[:2]
    assert "".join(c for cell in out[2:] for c in cell if c.isdecimal()) == digits[-4:]


@pytest.mark.parametrize("label", ["Has SSN", "ITIN verified", "SSN"])
@pytest.mark.parametrize("space", [" ", "\u00a0"])
@pytest.mark.parametrize("padding", [[], [""], ["Note"]])
@pytest.mark.parametrize("leading", [["Person"], [""], []])
def test_a_grouped_amount_cannot_borrow_digits_across_named_columns(label, space, padding, leading):
    header = ["Name", label, "Wages", "Year"]
    rows = [header, [*leading, "1", space.join(("25", "50")), "2025", *padding]]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("document", ["SSN", "ITIN"])
@pytest.mark.parametrize("missing", [False, True])
def test_grouped_wages_keep_column_ownership_under_document_type_records(document, missing):
    rows = [["Document", "Count", "Wages", "Year"], [document, "1", "25 50", "2025"]]
    rows.extend(([[document, "N/A", "", ""]] if missing else []) + rows[1:])
    assert mask_table(rows) == rows


def test_a_named_neighbour_cannot_withdraw_an_established_comb_mask():
    digits = _ssn("")
    rows = [["SSN", "Note"], [" ".join(digits[:5]), " ".join(digits[5:])]]
    assert mask_table(rows)[1] == ["X X X X X", rows[1][1]]


def test_a_shifted_grouped_identifier_keeps_its_shifted_wages_boundary():
    # An inserted cell moves the blank continuation and the wages column.
    digits = _ssn("")
    rows = [["SSN", "", "Wages"], ["", digits[:3] + " " + digits[3:5], digits[5:], "850.00"]]
    assert mask_table(rows)[1] == ["", "XXX XX", digits[5:], "850.00"]


@pytest.mark.parametrize("own", [MASKED, "N/A", _ssn("")], ids=["masked", "empty", "bare"])
@pytest.mark.parametrize("heading", ["Wages", "Amount", "Dividends", "Routing number",
                                     "Box 1: Wages", "Box 1a. Dividends", "(1) Wages", "1a. Amount"])
@pytest.mark.parametrize("space", [" ", "\u00a0", "\t"])
@pytest.mark.parametrize("wide", [False, True])
def test_same_row_identifier_reach_stops_at_a_financial_caption(own, heading, space, wide):
    row = ["SSN", own, heading, space.join(("850", "00")), "2025"]
    expected = ["SSN", MASKED if own == _ssn("") else own, *row[2:]]
    if wide:
        row = [_wide(cell) for cell in row]
        expected = [_wide(cell) if column != 1 or own != _ssn("") else MASKED
                    for column, cell in enumerate(expected)]
    assert mask_table([row]) == [expected]


@pytest.mark.parametrize("name", ["Dependent", "Person Example", "Wageson"])
@pytest.mark.parametrize("split", [2, 5, 7])
def test_a_financial_caption_does_not_withdraw_a_later_identifiers_own_label(name, split):
    value = _ssn(" ")
    at = [i for i, c in enumerate(value) if c.isdigit()][split]
    row = ["SSN", MASKED, "Wages", "850 00", "2025", "SSN", name, value[:at], value[at:]]
    out = mask_table([row])[0]
    assert out[:7] == row[:7]
    assert "".join(c for cell in out[7:] for c in cell if c.isdecimal()) == _ssn("")[-4:]


@pytest.mark.parametrize("evidence", ["column", "position"])
def test_financial_captions_do_not_withdraw_independent_identifier_evidence(evidence):
    digits = _ssn("")
    row = ["Wages", " ".join(digits[:5]), " ".join(digits[5:])]
    rows = [["Name", "SSN", "Other"], row] if evidence == "column" else [row]
    out = mask_table(rows, {digits} if evidence == "position" else None)[-1]
    assert "".join(c for cell in out[1:] for c in cell if c.isdecimal()) == digits[-4:]


@pytest.mark.parametrize("heading", ["Routing", "Account number", "Wages"])
@pytest.mark.parametrize("own", ["", None, "N/A"])
@pytest.mark.parametrize("lower", [["", "number", ""], ["", "number"]])
@pytest.mark.parametrize("space", [" ", "\u00a0"])
def test_wrapped_identifier_headers_retain_companion_column_ownership(heading, own, lower, space):
    rows = [["Name", "Social security", heading], lower, ["Person", own, space.join(ROUTING)]]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("numeric", [int, float])
def test_wrapping_another_heading_does_not_hide_a_filled_numeric_identifier(numeric):
    rows = [["SSN", numeric("912" + "34" + "6789"), "Social security"],
            ["", "", "number"], ["Routing", " ".join(ROUTING)]]
    assert mask_table(rows) == [["SSN", MASKED, "Social security"], *rows[1:]]


@pytest.mark.parametrize("heading", ["Date", "Birthdate", "DOB", "D.O.B."])
@pytest.mark.parametrize("mark", ["/", "-", "."])
@pytest.mark.parametrize("count", ["0", "1"])
@pytest.mark.parametrize("blank", [False, True])
def test_date_headings_do_not_need_an_identifier_in_the_header(heading, mark, count, blank):
    rows = [["Document", "Count", heading]]
    if blank:
        rows.append(["", "", ""])
    row = ["SSN", count, mark.join(("05", "05", "2026"))]
    rows.extend([row, row])
    assert mask_table(rows) == rows


@pytest.mark.parametrize("wrapped", [False, True])
def test_blank_rows_keep_dates_and_wrapped_date_companions(wrapped):
    rows = [["Name", "Social security" if wrapped else "SSN", "DOB"]]
    if wrapped:
        rows.append(["", "number", ""])
    rows.extend([["", "", ""], ["Person", "1", "05/05/2026"]])
    assert mask_table(rows) == rows


@pytest.mark.parametrize("heading", ["DOB", "Birthdate", "Date"])
@pytest.mark.parametrize("hint", ["(MM/DD/YYYY)", "(month, day, year)"])
def test_a_wrapped_date_heading_keeps_its_caption_and_continuation(heading, hint):
    rows = [["Name", "Social security", heading], ["", "number", hint],
            ["Person", "1", "05/05/2026"]]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("missing", [["N/A", ""], [None, None], ["—", None], ["N/A"]])
@pytest.mark.parametrize("filled_first", [False, True])
def test_independent_date_ownership_survives_records_with_missing_values(missing, filled_first):
    row = ["SSN", "1", "05/05/2026"]
    rows = [["Document", "Count", "Date"], *([row] if filled_first else []), ["SSN", *missing], row]
    assert mask_table(rows) == rows


def test_a_missing_value_record_cannot_shift_the_date_exemption():
    # The synthetic identifier has a date-shaped tail in a different column.
    rows = [["Document", "Count", "Date"], ["SSN", "", ""], ["9", "", "", "12-31-2025"]]
    assert mask_table(rows)[2] == ["X", "", "", "XX-XX-2025"]


def test_an_independent_date_header_does_not_exempt_a_new_identifier_header():
    rows = [["Document", "Count", "Date"], ["SSN", "Other", "Other"], ["9", "12-31-2025"]]
    out = mask_table(rows)
    assert out[:2] == rows[:2] and out[2][0] == "X" and out[2][1].startswith("XX")


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel"])
@pytest.mark.parametrize("case", ["caption", "wrapped", "date", "surname", "wrap_date", "missing_date", "grouped_flag"])
def test_independent_table_fields_through_extraction(tmp_path, route, case):
    if case == "caption":
        rows = [["SSN", MASKED, "Box 1: Wages", "850 00", "2025"]]
    elif case == "wrapped":
        rows = [["Name", "Social security", "Routing"], ["", "number", ""],
                ["Person", "", " ".join(ROUTING)]]
    elif case == "date":
        rows = [["Document", "Count", "Date"], ["SSN", "1", "05/05/2026"]]
    elif case == "wrap_date":
        rows = [["Name", "Social security", "DOB"], ["", "number", "(MM/DD/YYYY)"],
                ["Person", "1", "05/05/2026"]]
    elif case == "missing_date":
        rows = [["Document", "Count", "Date"], ["SSN", "N/A", ""], ["SSN", "1", "05/05/2026"]]
    elif case == "grouped_flag":
        rows = [["Has SSN", "Wages", "Year"], ["1", "25 50", "2025"]]
    else:
        digits = _ssn("")
        rows = [["SSN", "Wages", " ".join(digits[:5]), " ".join(digits[5:])]]
    if route == "extract_csv":
        path = tmp_path / "fields.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    else:
        path = _workbook(tmp_path / "fields.xlsx", rows)
    # Compare the actual table so preserving one repeated value cannot hide
    # corruption in another row or column.
    from agents.lib import parse_utils
    result = getattr(parse_utils, route)(str(path), workspace=str(tmp_path))
    table = result if route == "extract_csv" else next(iter(result.values()))
    if case == "surname":
        rows = [["SSN", "Wages", "X X X X X", rows[0][-1]]]
    if route == "extract_excel":
        rows = [[None if cell == "" else cell for cell in row] for row in rows]
    assert table == rows


@pytest.mark.parametrize("numeric", [int, float], ids=["integer", "float"])
@pytest.mark.parametrize("routing", [" ".join(ROUTING), " ".join((ROUTING[:3], ROUTING[3:5], ROUTING[5:]))])
def test_a_numeric_identifier_fills_its_row_before_routing_data(numeric, routing):
    rows = [["SSN", numeric("912" + "34" + "6789")], ["Routing", routing], ["Wages", "850.00"]]
    out = mask_table(rows)
    assert out[0] == ["SSN", MASKED] and out[1:] == rows[1:]


@pytest.mark.parametrize("number", [850.0, 12345678.9, float("nan"), float("inf"), float("-inf")])
def test_governed_numeric_cells_keep_nonidentifier_values_and_types(number):
    assert mask_table([["SSN", number]])[0][1] is number


@pytest.mark.parametrize("spelling", ["decimal", "exponent"])
@pytest.mark.parametrize("routing", [" ".join(ROUTING), " ".join((ROUTING[:3], ROUTING[3:5], ROUTING[5:]))])
def test_excel_numeric_float_identifier_does_not_mask_following_routing_data(tmp_path, spelling, routing):
    from zipfile import ZipFile

    import openpyxl

    number = "912" + "34" + "6789"
    path = _workbook(tmp_path / "numeric.xlsx", [["SSN", int(number)], ["Routing", routing]])
    with ZipFile(path) as workbook:
        contents = {name: workbook.read(name) for name in workbook.namelist()}
    replacement = number + (".0" if spelling == "decimal" else "e0")
    sheet = "xl/worksheets/sheet1.xml"
    contents[sheet] = contents[sheet].replace(f"<v>{number}</v>".encode(), f"<v>{replacement}</v>".encode())
    with ZipFile(path, "w") as workbook:
        for name, content in contents.items():
            workbook.writestr(name, content)
    workbook = openpyxl.load_workbook(path, read_only=True)
    try:
        assert isinstance(workbook.active["B1"].value, float)
    finally:
        workbook.close()
    out = _read("extract_excel", path, tmp_path)
    assert MASKED in out and routing in out


@pytest.mark.parametrize("shift", [-1, 0, 1])
@pytest.mark.parametrize("blank", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("value", [_comb(), _ssn("/"), _wide(_comb())])
def test_a_header_reaches_a_shifted_value_after_a_blank_or_wrap(shift, blank, wrapped, value):
    rows = [["Name", "Social security" if wrapped else "SSN", "Other"]]
    if wrapped:
        rows.append(["", "number", ""])
    if blank:
        rows.append([None, "", ""])
    row = ["", "", "", "850.00", ROUTING]
    row[1 + shift] = value
    rows.append(row)
    out = mask_table(rows)
    assert not _prints(_ssn("")[:5], " ".join(str(c) for c in out[-1]))
    assert out[:-1] == rows[:-1] and out[-1][-2:] == row[-2:]


@pytest.mark.parametrize("extra", [-1, 1])
@pytest.mark.parametrize("credit", ["0", "", None])
def test_a_dependents_row_may_have_one_cell_more_or_fewer_than_its_header(extra, credit):
    header = ["(1) First name", "Last name", "(2) Social security number", "(3) Relationship", "(4) Credits"]
    row = ["Person", "Example", _ssn(""), "Child", credit]
    if extra == 1:
        row.insert(0, "")
    else:
        row.pop(0)
    out = mask_table([header, row, row])[-1]
    assert MASKED in out and out[-2:] == row[-2:]


@pytest.mark.parametrize("value", [_ssn("/"), _wide(_ssn("/")), _comb()])
def test_a_whole_value_past_an_occupied_cell_is_still_introduced(value):
    row = ["SSN", "Dependent", value, "850.00", ROUTING]
    assert mask_table([row]) == [["SSN", "Dependent", MASKED, "850.00", ROUTING]]


@pytest.mark.parametrize("split", range(1, 9))
def test_a_whitespace_group_can_be_cut_inside_a_group(split):
    value = _ssn(" ")
    at = [i for i, c in enumerate(value) if c.isdigit()][split]
    row = ["SSN", value[:at], value[at:], "850.00", ROUTING]
    out = mask_table([row])[0]
    assert not _prints(_ssn("")[:5], " ".join(out))
    assert out[-2:] == row[-2:]


@pytest.mark.parametrize("split", range(1, 9))
@pytest.mark.parametrize("value", [_ssn("-"), _ssn("/"), _ssn(" "), "\u00ad".join(_ssn(""))])
@pytest.mark.parametrize("side", ["left", "right"])
def test_a_soft_hyphen_at_a_cell_edge_keeps_both_its_readings(split, value, side):
    at = [i for i, c in enumerate(value) if c.isdigit()][split]
    left, right = value[:at], value[at:]
    row = ["SSN", left + ("\u00ad" if side == "left" else ""),
           ("\u00ad" if side == "right" else "") + right, "850.00", ROUTING]
    out = mask_table([row])[0]
    assert not _prints(_ssn("")[:5], " ".join(out))
    assert out[-2:] == row[-2:]


def test_a_date_shaped_tail_does_not_exempt_a_definite_identifier_field():
    # Invalid synthetic prefix 912; the tail happens to read as a whole date.
    row = ["SSN", "9", "12-31-2025", "850.00"]
    out = mask_table([row])[0]
    assert out[1] == "X" and out[2].startswith("XX") and out[-1] == row[-1]


def test_shifted_headers_cannot_consume_later_flags_or_separate_amount_columns():
    rows = [["Name", "Has SSN", "Date"], ["Person", "1", "05/05/2026"],
            ["Flags", *(i % 2 for i in range(14))], ["SSN", "850", "00", "2025"]]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("column", [1, 2, 3])
@pytest.mark.parametrize("edge", ["left", "right"])
@pytest.mark.parametrize("padding", [" ", "\t", "\u00a0"])
@pytest.mark.parametrize("wide", [False, True])
def test_cell_padding_cannot_join_separate_amount_cents_and_year_columns(column, edge, padding, wide):
    row = ["SSN", "850", "00", "2025"]
    if wide:
        row = [_wide(cell) for cell in row]
    row[column] = padding + row[column] if edge == "left" else row[column] + padding
    assert mask_table([row]) == [row]


@pytest.mark.parametrize("space", [" ", "\u00a0", "\u202f", "\t"])
@pytest.mark.parametrize("groups, next_value", [(["12", "500"], "2025"),
                                              (["125", "000"], "125"),
                                              (["1", "250", "000"], "25")])
@pytest.mark.parametrize("wide", [False, True])
@pytest.mark.parametrize("layout", ["beside", "header", "adjacent"])
def test_thousands_grouping_cannot_join_separate_financial_cells(space, groups, next_value, wide, layout):
    amount = space.join(groups)
    if layout == "beside":
        rows = [["SSN", "N/A", "Wages", amount, next_value]]
    elif layout == "header":
        rows = [["SSN", "Wages", "Year"], ["N/A", amount, next_value]]
    else:
        rows = [["SSN", amount, next_value]]
    if wide:
        rows = [[_wide(cell) for cell in row] for row in rows]
    assert mask_table(rows) == rows


def test_unowned_three_cell_reading_still_masks_an_identifier():
    digits = _ssn("")
    fragments = [digits[:3], f"{digits[3:5]} {digits[5:8]}", digits[8:]]
    rows = [["SSN", "", ""], fragments]
    masked = mask_table(rows)[-1]
    assert not _prints(digits[:5], " ".join(masked))


@pytest.mark.parametrize("caption", ["Annual earnings", "Federal taxable wages", "W-2 Box 1"])
@pytest.mark.parametrize("place", ["same_row", "row_above"])
def test_a_labelled_identifier_between_count_columns_stays_masked(caption, place):
    """AA116 asked to free "850" | "12 500" | "1" under Count | amount | Copies.
    The first attempt also printed an identifier cut the same way under its own
    SSN label, in the row or directly above it, in layouts the masker already
    protected. A masked amount is recoverable by local transcription; a printed
    identifier is not, so this layout stays masked."""
    digits = _ssn("")
    fragments = [digits[:3], f"{digits[3:5]} {digits[5:8]}", digits[8:]]
    header = ["Document type", "Count", caption, "Copies"]
    if place == "same_row":
        rows = [header, ["SSN", *fragments]]
    else:
        rows = [header, ["", "SSN", "", ""], ["", *fragments]]
    assert not _prints(digits[:5], " ".join(str(c) for c in mask_table(rows)[-1]))


@pytest.mark.parametrize("space", [" ", "\u00a0", "\u202f"])
@pytest.mark.parametrize("groups, next_value", [(["12", "500"], "2025"),
                                              (["125", "000"], "125"),
                                              (["1", "250", "000"], "25")])
@pytest.mark.parametrize("hyphen", ["before", "inside", "after", "next"])
@pytest.mark.parametrize("wide", [False, True])
def test_soft_hyphens_do_not_make_thousands_grouping_an_identifier(space, groups, next_value, hyphen, wide):
    amount = space.join(groups)
    if hyphen == "before":
        amount = "\u00ad" + amount
    elif hyphen == "inside":
        amount = amount[:1] + "\u00ad" + amount[1:]
    elif hyphen == "after":
        amount += "\u00ad"
    else:
        next_value = "\u00ad" + next_value
    rows = [["SSN", "N/A", "Wages", amount, next_value]]
    if wide:
        rows = [[_wide(cell) for cell in row] for row in rows]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("space", [" ", "\u00a0"])
@pytest.mark.parametrize("split", range(1, 9))
@pytest.mark.parametrize("side", ["left", "right", "inside"])
def test_soft_hyphens_keep_valid_cross_cell_grouping(space, split, side):
    value = _ssn(space)
    at = [i for i, c in enumerate(value) if c.isdigit()][split]
    left, right = value[:at].rstrip(), value[at:].lstrip()
    if side == "left":
        left += "\u00ad"
    elif side == "right":
        right = "\u00ad" + right
    else:
        right = right[:-1] + "\u00ad" + right[-1:]
    out = mask_table([["SSN", left, right, "850.00"]])[0]
    assert not _prints(_ssn("")[:5], " ".join(out))
    assert "".join(c for cell in out[1:3] for c in cell if c.isdecimal()) == _ssn("")[-4:]
    assert out[-1] == "850.00"


@pytest.mark.parametrize("space", [" ", "\u00a0", "\t"])
@pytest.mark.parametrize("split", range(1, 9))
@pytest.mark.parametrize("layout", ["beside", "header"])
def test_cut_identifier_groups_keep_printed_group_spacing(space, split, layout):
    value = _ssn(space)
    at = [i for i, c in enumerate(value) if c.isdigit()][split]
    cells = [value[:at].rstrip(), value[at:].lstrip()]
    rows = [["SSN", *cells]] if layout == "beside" else [["SSN", ""], cells]
    out = mask_table(rows)
    assert not _prints(_ssn("")[:5], " ".join(out[-1]))
    assert "".join(c for cell in out[-1] for c in cell if c.isdecimal()) == _ssn("")[-4:]


@pytest.mark.parametrize("heading", ["Birthdate", "birthdate", "Birth date", "Date of birth",
                                    "DOB", "D.O.B.", "D. O. B.", "Birthday"])
@pytest.mark.parametrize("mark", ["/", "-", "."])
@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize("wide", [False, True])
def test_explicit_birth_date_columns_keep_dates_separate_from_flags(heading, mark, flag, wide):
    rows = [["SSN", heading], [flag, mark.join(("05", "05", "2026"))]]
    if wide:
        rows = [[_wide(cell) for cell in row] for row in rows]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("heading", ["Date", "Birthdate", "DOB", "D.O.B.", "Other"])
@pytest.mark.parametrize("label", ["Has SSN", "ITIN verified"])
@pytest.mark.parametrize("shift", [-1, 1])
@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize("mark", ["/", "-", "."])
def test_shifted_flag_and_date_columns_retain_their_ownership(heading, label, shift, flag, mark):
    row = [flag, mark.join(("05", "05", "2026"))]
    if shift == -1:
        header = ["Name", label, heading]
    else:
        header = [label, heading]
        row.insert(0, "Person")
    rows = [header, row, row]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel"])
def test_soft_hyphen_amounts_and_shifted_dates_through_extraction(tmp_path, route):
    amount = "12 500\u00ad"
    rows = [["SSN", "N/A", "Wages", amount, "2025"],
            ["Name", "Has SSN", "Date"], ["1", "05/05/2026"]]
    if route == "extract_csv":
        path = tmp_path / "soft-hyphen.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    else:
        path = _workbook(tmp_path / "soft-hyphen.xlsx", rows)
    out = _read(route, path, tmp_path)
    assert amount in out and "05/05/2026" in out


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel"])
@pytest.mark.parametrize("space", [" ", "\u00a0"])
@pytest.mark.parametrize("heading", ["Birthdate", "DOB", "D.O.B."])
def test_grouped_amounts_and_birth_dates_through_extraction(tmp_path, route, space, heading):
    amount = space.join(("12", "500"))
    rows = [["SSN", "N/A", "Wages", amount, "2025"], ["SSN", heading], ["1", "05/05/2026"]]
    if route == "extract_csv":
        path = tmp_path / "financial.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    else:
        path = _workbook(tmp_path / "financial.xlsx", rows)
    out = _read(route, path, tmp_path)
    assert amount in out and "05/05/2026" in out


@pytest.mark.parametrize("empty", ["", None, " ", "\t"])
@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("header_padding", [0, 1, 2])
def test_trailing_empty_fields_do_not_shift_aligned_identifier_and_routing_columns(
        empty, count, header_padding):
    header = ["SSN", "Routing", *([empty] * header_padding)]
    row = [_ssn(""), ROUTING, *([empty] * count)]
    assert mask_table([header, row, row]) == [header, [MASKED, *row[1:]], [MASKED, *row[1:]]]


@pytest.mark.parametrize("empty", ["", None, " ", "\t"])
@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("dependents", [False, True])
def test_empty_values_in_headered_columns_are_not_missing_cells(empty, count, dependents):
    header = ["SSN", "Routing", "Relationship" if dependents else "Note"]
    row = [_ssn(""), ROUTING, *([empty] * count)]
    assert mask_table([header, row, row]) == [header, [MASKED, *row[1:]], [MASKED, *row[1:]]]


@pytest.mark.parametrize("empty", ["", None, " ", "\t"])
def test_padding_cannot_bring_a_two_cell_difference_into_the_shift_window(empty):
    header = ["SSN", "Routing"]
    row = [_ssn(""), ROUTING, "Other", empty]
    assert mask_table([header, row]) == [header, [MASKED, *row[1:]]]


@pytest.mark.parametrize("own", [MASKED, "***-**-6789", _ssn(""), _ssn("-")],
                         ids=["masked", "stars", "bare", "dashed"])
@pytest.mark.parametrize("routing", [ROUTING, " ".join(ROUTING), " ".join((ROUTING[:3], ROUTING[3:5], ROUTING[5:]))],
                         ids=["bare", "comb", "grouped"])
@pytest.mark.parametrize("note", [[], ["Note"]])
@pytest.mark.parametrize("order", ["first", "last"])
def test_filled_identifier_columns_cannot_take_aligned_neighboring_values(own, routing, note, order):
    header, row = ["SSN", "Routing"], [own, routing]
    expected = [own if own.startswith("*") else MASKED, routing]
    if order == "last":
        header.reverse()
        row.reverse()
        expected.reverse()
    row.extend(note)
    expected.extend(note)
    assert mask_table([header, row, row]) == [header, expected, expected]


@pytest.mark.parametrize("own", ["", None, "N/A", "0"])
@pytest.mark.parametrize("heading, routing", [
    *((heading, value) for heading in ("Routing", "Account number") for value in (ROUTING, " ".join(ROUTING))),
    # A Wages column keeps a nine-digit figure; nine spaced digits there are
    # an identifier's comb (see the review cases at the end of this file).
    ("Wages", ROUTING)])
@pytest.mark.parametrize("note", [[], ["Note"]])
def test_an_empty_identifier_field_does_not_take_an_explicit_neighboring_column(own, heading, routing, note):
    rows = [["SSN", heading], [own, routing, *note]]
    assert mask_table(rows) == rows


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel", "extract_tables"])
def test_aligned_routing_beside_a_masked_identifier_through_extraction(tmp_path, route):
    routing = " ".join(ROUTING)
    rows = [["SSN", "Routing"], [MASKED, routing]]
    if route == "extract_csv":
        path = tmp_path / "aligned.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    elif route == "extract_excel":
        path = _workbook(tmp_path / "aligned.xlsx", rows)
    else:
        from tests.test_identifier_spellings import _cid_pdf
        items = [(36, 705, "SSN"), (280, 705, "Routing"),
                 (36, 680, MASKED), (280, 680, routing)]
        path = _cid_pdf(tmp_path / "aligned.pdf", mapped=set("".join(t for _, _, t in items)), items=items)
    out = _read(route, path, tmp_path)
    assert MASKED in out and routing in out


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel"])
def test_padded_financial_columns_keep_their_values_through_extraction(tmp_path, route):
    rows = [["SSN", "Routing"], [_ssn(""), ROUTING, ""], ["SSN", "850 ", " 00", "2025"]]
    if route == "extract_csv":
        path = tmp_path / "padded.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    else:
        path = _workbook(tmp_path / "padded.xlsx", rows)
    out = _read(route, path, tmp_path)
    assert MASKED in out and ROUTING in out and "850 " in out and " 00" in out and "2025" in out


def test_a_variant_value_in_the_headers_column_uses_the_recognition_view():
    assert mask_table([["SSN", "Amount"], [_wide(_ssn("")), "850.00"]]) == [
        ["SSN", "Amount"], [MASKED, "850.00"]]


@pytest.mark.parametrize("label", ["Has ITIN", "ITIN verified", "Has social security number",
                                   "Social security number verified", "Has S\u200bSN"])
@pytest.mark.parametrize("layout", ["beside", "header"])
def test_flag_labels_use_all_identifier_aliases(label, layout):
    rows = ([[label, "9", "12-31-2025", "850.00"]] if layout == "beside" else
            [[label, "Other"], ["9", "12-31-2025"], ["0", "09/04/2021"]])
    assert mask_table(rows) == rows


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel", "extract_tables"])
def test_a_wrapped_header_and_shifted_comb_through_table_extraction(tmp_path, route):
    rows = [["Name", "Social security"], ["", "number"], ["", ""],
            [_comb(), "850.00"]]
    if route == "extract_csv":
        path = tmp_path / "wrapped.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)
    elif route == "extract_excel":
        path = _workbook(tmp_path / "wrapped.xlsx", rows)
    else:
        from tests.test_identifier_spellings import _cid_pdf
        items = [(280, 705, "Social security"), (280, 696, "number"),
                 (36, 680, _comb()), (280, 680, "850.00")]
        path = _cid_pdf(tmp_path / "wrapped.pdf", mapped=set("".join(t for _, _, t in items)), items=items)
    out = _read(route, path, tmp_path)
    assert not _prints(_ssn("")[:5], out) and "850.00" in out


# Review of #124: each layout below printed digits main masked, or that its
# own AA109 rule was meant to mask.


@pytest.mark.parametrize("split", range(1, 9))
@pytest.mark.parametrize("layout", ["beside", "header"])
def test_a_second_identifier_starting_inside_the_first_ones_run_is_masked(split, layout):
    # The spaced run that starts inside the first comb takes the second
    # identifier's opening digits; it must not be dropped with them.
    second = " ".join(_SECOND)
    at = [i for i, c in enumerate(second) if c.isdecimal()][split]
    values = [_comb(), second[:at].rstrip(), second[at:].lstrip()]
    rows = [["SSN", *values]] if layout == "beside" else [["SSN", "", ""], values]
    out = mask_table(rows)[-1][-3:]
    assert "".join(c for c in out[0] if c.isdecimal()) == _ssn("")[-4:]
    assert "".join(c for cell in out[1:] for c in cell if c.isdecimal()) == _SECOND[-4:]


@pytest.mark.parametrize("heading", ["Spouse", "Note", "Wages", "Amount", "Date"])
@pytest.mark.parametrize("own", ["1", " ".join(_ssn(""))], ids=["count", "comb"])
def test_a_comb_beside_the_identifier_column_is_masked_unless_named_a_nine_digit_field(heading, own):
    # Only a routing or account heading makes nine digits that column's own.
    rows = [["SSN", heading], [own, " ".join(_SECOND)]]
    out = mask_table(rows)[1]
    assert not _prints(_SECOND[:5], out[1])
    assert not _prints(_ssn("")[:5], " ".join(out))


@pytest.mark.parametrize("heading", ["Dependent", "Spouse", "Wages", "Note"])
@pytest.mark.parametrize("amount", ["1,234.56", "1 234.56", "850.00"])
def test_a_named_columns_comb_is_masked_without_borrowing_the_next_amount(heading, amount):
    # The comb bounds the run, so its digits cannot take the amount's first.
    rows = [["SSN", heading, "Wages"], [" ".join(_ssn("")), " ".join(_SECOND), amount]]
    out = mask_table(rows)[1]
    assert out[2] == amount
    assert not _prints(_ssn("")[:5], out[0]) and not _prints(_SECOND[:5], out[1])


@pytest.mark.parametrize("heading", ["Routing", "Routing number", "Account number"])
@pytest.mark.parametrize("own", ["1", " ".join(_ssn(""))], ids=["count", "comb"])
def test_a_routing_or_account_column_keeps_its_comb_beside_the_identifier_column(heading, own):
    routing = " ".join(ROUTING)
    out = mask_table([["SSN", heading], [own, routing]])[1]
    assert out[1] == routing and not _prints(_ssn("")[:5], out[0])


def test_a_spouse_comb_is_masked_through_csv_extraction(tmp_path):
    from agents.lib import parse_utils
    path = tmp_path / "spouse.csv"
    with path.open("w", newline="") as stream:
        csv.writer(stream).writerows([["Taxpayer SSN", "Spouse"], [" ".join(_ssn("")), " ".join(_SECOND)]])
    table = parse_utils.extract_csv(str(path), workspace=str(tmp_path))
    assert not _prints(_ssn("")[:5], " ".join(table[1])) and not _prints(_SECOND[:5], table[1][1])


@pytest.mark.parametrize("route", ["extract_csv", "extract_excel"])
@pytest.mark.parametrize("extra", [-1, 1])
def test_a_shifted_dependents_row_is_masked_on_every_table_route(tmp_path, route, extra):
    # A workbook pads every row to the sheet width, so a lost or extra cell
    # shows only in how far a row is populated, not in how many cells it has.
    header = ["(1) First name", "Last name", "(2) Social security number", "(3) Relationship", "(4) Credits"]
    row = ["Person", "Example", _ssn(""), "Child", "0"]
    if extra == 1:
        row.insert(0, "")
    else:
        row.pop(0)
    if route == "extract_csv":
        path = tmp_path / "dependents.csv"
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows([header, row])
    else:
        path = _workbook(tmp_path / "dependents.xlsx", [header, row])
    out = _read(route, path, tmp_path)
    assert MASKED in out and _ssn("") not in out


@pytest.mark.parametrize("status", ["No SSN", "No ITIN", "ITIN pending"])
def test_a_dependent_without_an_identifier_keeps_later_shifted_rows_masked(status):
    rows = [["Name", "SSN", "Relationship"], ["Ann", status, "Daughter"], ["Bob", "Lee", _ssn(""), "Son"]]
    out = mask_table(rows)
    assert out[:2] == rows[:2] and out[2] == ["Bob", "Lee", MASKED, "Son"]
