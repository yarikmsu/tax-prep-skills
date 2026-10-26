"""The prior-return import's position readers, on TY2025 forms filled and flattened.

`tests/fixtures/prior_return/official_tables_2025.json` holds each page's text
and word positions as pdfplumber reads the official form after its own fields
were filled with synthetic values and flattened; its builder sits beside it.
The rest of the return is `official_2025.json`, as `test_prior_return.py` reads
it. No real return or identity data is involved.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from agents.lib import prior_return as P
from agents.lib.rollover import CurrentState, compute_diff, load_prior

FIXTURES = Path(__file__).parent / "fixtures/prior_return"
MAIN = json.loads((FIXTURES / "official_2025.json").read_text(encoding="utf-8"))
TABLES = json.loads((FIXTURES / "official_tables_2025.json").read_text(encoding="utf-8"))["pages"]
SHA = hashlib.sha256(b"synthetic flattened source").hexdigest()
KEYS = ("text", "x0", "x1", "top", "bottom")
ALL = ("1116B_page1", "1116B_page2", "P540_side1", "P540_side2", "P540_side3", "P540_side4")


def words_of(name):
    return [dict(zip(KEYS, word)) for word in TABLES[name]["words"]]


def document(*names, drop=()):
    """Official text for the whole return, Form 1040 page 1 flattened, then
    `names` appended in order. Returns pages, words and each name's index."""
    pages = {int(k): v for k, v in MAIN["pages"].items() if int(k) not in drop}
    pages[0] = TABLES["1040_page1"]["text"]
    words, where = {0: words_of("1040_page1")}, {"1040_page1": 0}
    for name in names:
        index = max(pages) + 1
        pages[index], words[index] = TABLES[name]["text"], words_of(name)
        where.setdefault(name, index)
    return pages, words, where


def seed(*names, edit=None, drop=()):
    pages, words, where = document(*names, drop=drop)
    if edit:
        edit(pages, words, where)
    return P.extract_seed(pages, 2025, SHA, words)


def word(words, text, x=None, top=None):
    """The word printed as `text`, nearest the given position."""
    found = [w for w in words if w["text"] == text]
    assert found, text
    return min(found, key=lambda w: abs(w["x0"] - (x if x is not None else w["x0"]))
               + abs(w["top"] - (top if top is not None else w["top"])))


def set_text(name, text, new, x=None, top=None):
    def edit(pages, words, where):
        word(words[where[name]], text, x, top)["text"] = new
    return edit


def remove(name, text, x=None, top=None):
    def edit(pages, words, where):
        words[where[name]].remove(word(words[where[name]], text, x, top))
    return edit


def move(name, text, dx=0.0, dy=0.0, x=None, top=None):
    def edit(pages, words, where):
        target = word(words[where[name]], text, x, top)
        for key, delta in (("x0", dx), ("x1", dx), ("top", dy), ("bottom", dy)):
            target[key] = round(target[key] + delta, 1)
    return edit


PASSIVE = {"2016": 0.0, "2017": 0.0, "2018": 0.0, "2019": 0.0, "2020": 0.0, "2021": 0.0,
           "2022": 300.0, "2023": 0.0, "2024": 600.0, "2025": 700.0}
CREDITS = {"234": 500.0, "188": 1700.0, "180": 400.0, "242": 3800.0}


def credits(parsed):
    return {c["code"]: c["carryforward"] for c in parsed["table_evidence"]["ca_credits"]}


def test_the_flattened_official_tables_are_read_by_position():
    parsed = seed(*ALL)
    (basket,) = parsed["table_evidence"]["ftc_baskets"]
    assert basket["basket"] == "passive" and basket["carryforward_by_origin_year"] == PASSIVE
    assert basket["provenance"]["pages"] == [13, 14]
    # Columns (vii)/(viii) carry page 1's subtotal; (xiv) totals both pages.
    assert basket["reconciliation"]["3"]["(vii)"] == basket["reconciliation"]["3"]["(viii)"] == 500
    assert basket["reconciliation"]["8"]["(xiv)"] == 1600
    assert credits(parsed) == CREDITS
    solar = next(c for c in parsed["table_evidence"]["ca_credits"] if c["code"] == "180")
    assert [(row["line"], row["section"], row["carryover"]) for row in solar["lines"]] == [
        ("16", "B2", 500.0), ("23", "C", 400.0)]
    assert parsed["marked_filing_status"]["value"] == "MFJ"
    assert not {"ftc_schedule_b", "ca_credit_carryovers", "filing_status_mark"} & set(parsed["unresolved"])
    assert P.table_confirmation_ids(parsed) == sorted(
        [f"ftc_passive_{year}" for year in PASSIVE] + [f"ca_credit_{code}" for code in CREDITS])


def test_the_official_lines_read_from_a_flattened_form_1040_page():
    """#69's line reader, on Form 1040 page 1 as the flattened form prints it."""
    parsed = seed()
    assert {k: v["value"] for k, v in parsed["fields"].items()}["capital_loss_carryforward_long_term"] == 7000


def test_without_word_positions_the_tables_stay_questions():
    pages, _, _ = document(*ALL)
    parsed = P.extract_seed(pages, 2025, SHA)
    assert "table_evidence" not in parsed and "marked_filing_status" not in parsed
    assert "word positions" in parsed["unresolved"]["ftc_schedule_b"]
    assert "word positions" in parsed["unresolved"]["ca_credit_carryovers"]


@pytest.mark.parametrize("names", [
    ("1116B_page1",), ("1116B_page2", "1116B_page1"), ("1116B_page1", "P540_side1", "1116B_page2")])
def test_each_schedule_b_page_1_needs_its_page_2_right_after_it(names):
    assert "page 2 right after it" in seed(*names)["unresolved"]["ftc_schedule_b"]


def general(pages, words, where):
    """The second copy of page 1 marks General instead of Passive."""
    second = max(index for index, text in pages.items() if text == TABLES["1116B_page1"]["text"])
    mark = word(words[second], "4", x=240, top=152)
    mark["top"], mark["bottom"] = mark["top"] + 12.1, mark["bottom"] + 12.1


def general_of_its_own(pages, words, where):
    """The general pair carried 250 into 2018 and used it, where passive
    carried and used 200: each page 1's subtotal is its own."""
    general(pages, words, where)
    page_1, page_2 = (max(index for index, text in pages.items() if text == TABLES[name]["text"])
                      for name in ("1116B_page1", "1116B_page2"))
    for page, old, new, x, top in ((page_1, "200", "250", 502, 418), (page_1, "500", "550", 741, 418),
                                   (page_1, "-200", "-250", 500, 442), (page_1, "-200", "-250", 738, 442),
                                   (page_2, "500", "550", 265, 280), (page_2, "1,500", "1,550", 734, 280),
                                   (page_2, "-200", "-250", 262, 304), (page_2, "-300", "-350", 738, 304)):
        word(words[page], old, x, top)["text"] = new


def test_two_categories_each_read_from_their_own_pair():
    parsed = seed("1116B_page1", "1116B_page2", "1116B_page1", "1116B_page2", edit=general_of_its_own)
    baskets = {b["basket"]: b["carryforward_by_origin_year"] for b in parsed["table_evidence"]["ftc_baskets"]}
    assert baskets == {"passive": PASSIVE, "general": PASSIVE}


def test_a_page_2_after_another_categorys_page_1_is_a_question():
    """Passive page 1, general's page 2, general page 1, passive's page 2: each
    page 2 repeats the other page 1's subtotal."""
    def swapped(pages, words, where):
        general_of_its_own(pages, words, where)
        first, second = sorted(index for index, text in pages.items() if text == TABLES["1116B_page2"]["text"])
        words[first], words[second] = words[second], words[first]
    parsed = seed("1116B_page1", "1116B_page2", "1116B_page1", "1116B_page2", edit=swapped)
    assert "does not carry page 1's subtotal" in parsed["unresolved"]["ftc_schedule_b"]


def test_two_categories_with_the_same_subtotals_are_a_question():
    """Page 2 binds to its page 1 by the subtotal it repeats: two pairs
    repeating the same one could be printed out of order and swap unseen."""
    parsed = seed("1116B_page1", "1116B_page2", "1116B_page1", "1116B_page2", edit=general)
    assert "subtotal of its own" in parsed["unresolved"]["ftc_schedule_b"]


def test_a_page_1_mentioning_page_2_is_still_a_page_1():
    def mention(pages, words, where):
        pages[where["1116B_page1"]] += "\nContinued on Page 2"
    (basket,) = seed("1116B_page1", "1116B_page2", edit=mention)["table_evidence"]["ftc_baskets"]
    assert basket["carryforward_by_origin_year"] == PASSIVE


def test_interleaved_categories_are_not_paired_by_position_alone():
    """Passive page 1, general page 1, passive page 2, general page 2: the
    general page 1 is followed by a page 1, never bound to passive's page 2."""
    parsed = seed("1116B_page1", "1116B_page1", "1116B_page2", "1116B_page2", edit=general)
    assert "table_evidence" not in parsed or "ftc_baskets" not in parsed["table_evidence"]
    assert "page 2 right after it" in parsed["unresolved"]["ftc_schedule_b"]


def test_two_schedules_for_one_category_are_a_question():
    parsed = seed("1116B_page1", "1116B_page2", "1116B_page1", "1116B_page2")
    assert "two Schedules B for one category" in parsed["unresolved"]["ftc_schedule_b"]


@pytest.mark.parametrize("edit, message", [
    (remove("1116B_page1", "4", x=240, top=152), "exactly one category box"),
    (move("1116B_page1", "4", dx=150.4, x=240, top=152), "needs its country"),
    (set_text("1116B_page1", "4", "Paid", x=240, top=152), "text where a category box"),
    (remove("1116B_page1", "25", x=238, top=56), "calendar year"),
    (set_text("1116B_page1", "Passive", "Pasive"), "category boxes are not where"),
])
def test_the_category_and_year_are_read_or_asked(edit, message):
    assert message in seed("1116B_page1", "1116B_page2", edit=edit)["unresolved"]["ftc_schedule_b"]


def test_a_second_marked_category_is_a_question():
    def both(pages, words, where):
        mark = dict(word(words[where["1116B_page1"]], "4", x=240, top=152))
        mark["top"], mark["bottom"] = mark["top"] + 12.1, mark["bottom"] + 12.1
        words[where["1116B_page1"]].append(mark)
    assert "exactly one category box" in seed("1116B_page1", "1116B_page2", edit=both)["unresolved"]["ftc_schedule_b"]


def test_a_fiscal_year_schedule_is_a_question():
    def fiscal(pages, words, where):
        beginning = word(words[where["1116B_page1"]], "beginning")
        words[where["1116B_page1"]].append({**beginning, "text": "07/01", "x0": 360.0, "x1": 380.0})
    assert "fiscal year" in seed("1116B_page1", "1116B_page2", edit=fiscal)["unresolved"]["ftc_schedule_b"]


@pytest.mark.parametrize("others", [
    # Row grouping joins "calendar" to a "year" drawn lower, which does not
    # overlap it: nothing lies to the right of "calendar".
    [{"text": "x", "x0": 10, "x1": 15, "top": 53.0, "bottom": 54.0},
     {"text": "y", "x0": 20, "x1": 25, "top": 56.0, "bottom": 57.0},
     {"text": "year", "x0": 205, "x1": 220, "top": 59.0, "bottom": 68.0}],
    # A mark of another row sits between "calendar" and its "year".
    [{"text": "year", "x0": 205, "x1": 220, "top": 52.0, "bottom": 61.0},
     {"text": "q", "x0": 203, "x1": 204, "top": 56.0, "bottom": 57.0}],
])
def test_a_calendar_year_split_across_rows_is_a_question(others):
    words = [{"text": "calendar", "x0": 172, "x1": 202, "top": 50.0, "bottom": 59.0}, *others]
    assert len(P._phrases(words, ("calendar", "year"))) == 1
    with pytest.raises(P._Unreadable, match="calendar-year entry is missing"):
        P._schedule_b_year(words)


def test_a_schedule_b_for_another_year_refuses():
    with pytest.raises(P.ImportError, match="different tax year"):
        seed("1116B_page1", "1116B_page2", edit=set_text("1116B_page1", "25", "24", x=238, top=56))


@pytest.mark.parametrize("edit, message", [
    # Line 3, column (vii) on page 1, carried to page 2's (viii): no longer
    # the sum of (i)-(vi).
    (lambda *a: (set_text("1116B_page1", "500", "600", x=742, top=416)(*a),
                 set_text("1116B_page2", "500", "600", x=265, top=278)(*a)), "printed subtotals"),
    # Page 2's (viii) must carry page 1's (vii), even where its own total holds.
    (lambda *a: (set_text("1116B_page2", "500", "600", x=265, top=278)(*a),
                 set_text("1116B_page2", "1,500", "1,600", x=734, top=278)(*a)), "does not carry page 1's subtotal"),
    # Line 8, column (x): 400 carried in and 100 used leave 300.
    (lambda *a: (set_text("1116B_page2", "300", "400", x=423, top=409)(*a),
                 set_text("1116B_page2", "1,600", "1,700", x=734, top=409)(*a)),
     "combination of lines 3 through 7"),
    # A blank is zero only while the sums hold: the 100 used is unreadable.
    (remove("1116B_page2", "-100", x=420, top=302), "printed subtotals"),
    (set_text("1116B_page2", "-100", "1OO", x=420, top=302), "cell holds text"),
])
def test_schedule_b_figures_must_reconcile(edit, message):
    assert message in seed("1116B_page1", "1116B_page2", edit=edit)["unresolved"]["ftc_schedule_b"]


def test_two_figures_in_one_cell_are_a_question():
    def crowd(pages, words, where):
        source = word(words[where["1116B_page2"]], "300", x=423, top=409)
        words[where["1116B_page2"]].append({**source, "text": "5", "x0": source["x0"] - 9, "x1": source["x0"] - 4})
    assert "more than one figure" in seed("1116B_page1", "1116B_page2", edit=crowd)["unresolved"]["ftc_schedule_b"]


def zeros():
    return {(line, label): 0.0 for line in P._SB_LINES for label in P._SB_COLUMNS}


def consistent(values, line, label, amount):
    """Put `amount` in one cell and carry it through every printed total."""
    page = 0 if label in P._SB_HEADINGS[0] else 1
    cells = [label, "(vii)", "(viii)", "(xiv)"] if page == 0 else [label, "(xiv)"]
    for column in cells:
        values[line, column] += amount
        values["8", column] += amount


@pytest.mark.parametrize("line, label, amount", [
    ("5", "(ii)", -100.0),   # only the 10th preceding year expires
    ("6", "(x)", 100.0),     # only the current year generates
    ("7", "(xii)", -100.0),  # or carries back
    ("3", "(xiii)", 100.0),  # the current year carries nothing in
])
def test_a_figure_in_a_shaded_cell_is_a_question(line, label, amount):
    values = zeros()
    consistent(values, line, label, amount)
    if line in ("5", "7"):  # what expires or carries back was carried in first
        consistent(values, "3", label, -amount)
    with pytest.raises(P._Unreadable, match="shades"):
        P._schedule_b_checks(values)
    allowed = zeros()
    consistent(allowed, "3", "(ii)", 100.0)
    P._schedule_b_checks(allowed)


def test_a_used_amount_printed_positive_is_a_question():
    values = zeros()
    consistent(values, "3", "(ii)", 100.0)
    consistent(values, "4", "(ii)", 50.0)
    consistent(values, "3", "(ii)", -100.0)
    consistent(values, "3", "(ii)", 100.0)
    values["8", "(ii)"] = values["8", "(vii)"] = values["8", "(viii)"] = values["8", "(xiv)"] = 150.0
    with pytest.raises(P._Unreadable, match="wrong sign"):
        P._schedule_b_checks(values)


@pytest.mark.parametrize("entries", [[("3", -100.0)], [("3", 100.0), ("4", -150.0)]])
def test_a_negative_carryover_is_a_question(entries):
    """Line 3 carries in a positive amount, and line 8 is what is left: 100
    carried in and 150 used would leave line 8 negative."""
    values = zeros()
    for line, amount in entries:
        consistent(values, line, "(ii)", amount)
    with pytest.raises(P._Unreadable, match="wrong sign"):
        P._schedule_b_checks(values)


def test_the_tenth_year_cannot_carry_forward():
    values = zeros()
    consistent(values, "3", "(i)", 100.0)
    with pytest.raises(P._Unreadable, match="shades"):
        P._schedule_b_checks(values)


@pytest.mark.parametrize("edit, message", [
    (remove("P540_side3", "234"), "without its credit code"),
    (set_text("P540_side3", "188", "189"), "without its credit code"),
    # Line 10, column (d): more than (a) less (b) is never carried.
    (set_text("P540_side3", "1,700", "1,800", x=544, top=409), "at most (a) less (b)"),
    # The solar credit's B2 carryover (500) must be Section C's credit amount.
    (set_text("P540_side4", "500", "600", x=164, top=590), "does not reach its next section"),
    (set_text("P540_side3", "234", "188"), "entered twice in one section"),
    (move("P540_side4", "3,800", dx=60, x=544, top=488), "outside its columns"),
    (set_text("P540_side4", "400", "4OO", x=552, top=590), "cell holds text"),
])
def test_schedule_p_lines_must_reconcile(edit, message):
    parsed = seed("P540_side3", "P540_side4", edit=edit)
    assert message in parsed["unresolved"]["ca_credit_carryovers"]
    assert "table_evidence" not in parsed


def shift(name, dy, tops):
    """The filled figures on the rows printed at `tops`, moved by `dy`."""
    def edit(pages, words, where):
        for w in words[where[name]]:
            if P._VALUE.fullmatch(w["text"]) and w["x0"] > 150 and any(abs(w["top"] - top) < 1.5 for top in tops):
                w["top"], w["bottom"] = round(w["top"] + dy, 1), round(w["bottom"] + dy, 1)
    return edit


def both(*edits):
    return lambda *a: [edit(*a) for edit in edits]


@pytest.mark.parametrize("edit, message", [
    # Line 21's figures drawn up into its column headings, then into its name.
    (shift("P540_side4", -14, [482]), "line 21 prints a figure among its column headings"),
    (shift("P540_side4", -26, [482]), "line 21 prints a figure beside its name"),
    # Every figure 13pt high: each lands in its own line's headings.
    (both(shift("P540_side3", -13, [134, 409]), shift("P540_side4", -13, [134, 422, 482, 543, 590])),
     "among its column headings"),
    # Above line 6, and in line 20's column (d), where it has no box.
    (move("P540_side3", "500", dy=-54, x=552, top=134), "in no line's columns"),
    (lambda pages, words, where: words[where["P540_side4"]].append(
        {"text": "400", "x0": 552.0, "x1": 566.0, "top": 422.0, "bottom": 431.0}), "line 20 prints a figure in a column"),
    # A line number unread: its figures belong to no line.
    (remove("P540_side3", "6", x=41, top=102), "line number on Side 3 is missing"),
    (remove("P540_side3", "10", x=36, top=378), "line number on Side 3 is missing"),
    (lambda pages, words, where: [w.update(text="(e)") for w in words[where["P540_side4"]] if w["text"] == "(d)"],
     "the column headings are missing"),
])
def test_every_figure_on_sides_3_and_4_is_accounted_for(edit, message):
    parsed = seed("P540_side3", "P540_side4", edit=edit)
    assert message in parsed["unresolved"]["ca_credit_carryovers"]
    assert "table_evidence" not in parsed


@pytest.mark.parametrize("edit", [
    # Line 21: column (a) blank beside a carryover, and a used or carried amount negative.
    remove("P540_side4", "5,000", x=155, top=482),
    set_text("P540_side4", "1,200", "-1,200", x=286, top=482),
    set_text("P540_side4", "3,800", "(3,800)", x=544, top=482),
])
def test_a_carryover_line_needs_its_amount_and_no_negative_figure(edit):
    parsed = seed("P540_side3", "P540_side4", edit=edit)
    assert "line 21 fails (d) at most (a) less (b)" in parsed["unresolved"]["ca_credit_carryovers"]


@pytest.mark.parametrize("name", ["3.0", "3523", "(234)", "2023"])
def test_a_credit_name_holding_a_number_is_read(name):
    """Credit names may hold numbers: the FTB's own "Program 3.0 California
    Motion Picture..." (code 239), a form number, the code itself, a year."""
    parsed = seed("P540_side3", "P540_side4", edit=set_text("P540_side3", "Employment", name))
    assert credits(parsed) == CREDITS


@pytest.mark.parametrize("text, x", [("1,500", 155), ("500", 552)])
def test_a_figure_drawn_up_beside_the_credit_name_is_a_question(text, x):
    """Columns (a) and (d) sit outside the name: either drawn up into the
    number row is a question."""
    parsed = seed("P540_side3", "P540_side4", edit=move("P540_side3", text, dy=-36, x=x, top=134))
    assert "line 6 prints a figure beside its name" in parsed["unresolved"]["ca_credit_carryovers"]


def test_a_line_whose_figures_all_sit_in_its_credit_name_is_a_question():
    """Nothing in the columns and every figure in the name: the line would
    drop out, and a credit chained from an earlier section would carry that
    section's figure instead."""
    edits = [move("P540_side3", text, dx=dx, dy=-36, x=x, top=134)
             for text, x, dx in (("1,500", 155, 60), ("1,000", 285, 0), ("4,000", 415, 0), ("500", 552, -200))]
    parsed = seed("P540_side3", "P540_side4", edit=both(*edits))
    assert "line 6 has a figure in its credit name" in parsed["unresolved"]["ca_credit_carryovers"]


def test_a_used_figure_drawn_up_into_the_credit_name_blocks_as_not_carried():
    parsed = seed("P540_side3", "P540_side4", edit=move("P540_side3", "1,000", dy=-36, x=285, top=134))
    line_6 = next(c for c in parsed["table_evidence"]["ca_credits"] if c["code"] == "234")
    assert line_6["carryforward"] == 500 and line_6["not_carried"] == 1000


def test_the_commercial_solar_credit_carries_through_line_24():
    """Code 181 on line 16 carries its B2 figure into Section C's line 24, not
    line 23, which is the solar energy credit's (180)."""
    to_181 = set_text("P540_side4", "180", "181", x=93, top=98)
    parsed = seed("P540_side3", "P540_side4", edit=both(to_181, shift("P540_side4", 48, [590])))
    assert credits(parsed) == {**{k: v for k, v in CREDITS.items() if k != "180"}, "181": 400.0}
    parsed = seed("P540_side3", "P540_side4", edit=to_181)
    assert "credit code 181's Section B2 carryover has no Section C line" in parsed["unresolved"]["ca_credit_carryovers"]


def test_a_credit_elected_refundable_is_carried_as_printed():
    """Column (d) is (a) less (b) less any amount elected refundable on FTB
    3870; the difference is shown for review, never added back."""
    parsed = seed("P540_side3", "P540_side4", edit=set_text("P540_side4", "3,800", "3,000", x=544, top=488))
    ptet = next(c for c in parsed["table_evidence"]["ca_credits"] if c["code"] == "242")
    assert ptet["carryforward"] == 3000 and ptet["not_carried"] == 800


def test_a_blank_carryover_column_still_blocks_rollover():
    """An unread column (d) is zero carried and the rest shown as not carried;
    the not-carried amount blocks like a balance would."""
    parsed = seed("P540_side3", "P540_side4", edit=remove("P540_side4", "3,800", x=544, top=488))
    ptet = next(c for c in parsed["table_evidence"]["ca_credits"] if c["code"] == "242")
    assert ptet["carryforward"] == 0 and ptet["not_carried"] == 3800


@pytest.mark.parametrize("names", [("P540_side3", "P540_side4", "P540_side4"), ("P540_side3",), ("P540_side4",),
                                   ("P540_side1", "P540_side2")])
def test_schedule_p_needs_sides_3_and_4_once_each(names):
    """Every carryover line is on Side 3 or 4: one missing is a partial
    table, two copies of one are two answers. Parts I and II, on Sides 1
    and 2, reuse the line numbers for AMT lines and are never read as credits."""
    parsed = seed(*names)
    assert "table_evidence" not in parsed
    assert "Sides 3 and 4, once each" in parsed["unresolved"]["ca_credit_carryovers"]


def test_schedule_p_and_ftb_3510_must_agree_on_the_amt_credit():
    def less_carried(pages, words, where):
        word(words[where["P540_side3"]], "1,700", x=544, top=409)["text"] = "1,600"
    with pytest.raises(P.ImportError, match="disagree on the prior-year AMT credit"):
        seed("P540_side3", "P540_side4", edit=less_carried)


def test_an_unmarked_return_proposes_no_status_and_two_marks_are_a_question():
    unmarked = seed(edit=remove("1040_page1", "4", x=99, top=219))
    assert "marked_filing_status" not in unmarked
    def second(pages, words, where):
        mark = dict(word(words[0], "4", x=99, top=219))
        mark["top"], mark["bottom"] = mark["top"] - 12, mark["bottom"] - 12
        words[0].append(mark)
    assert "more than one" in seed(edit=second)["unresolved"]["filing_status_mark"]


def schedule_c(pages, words, where):
    """Form 8995 replaced by Form 8995-A and its Schedule C."""
    del pages[8]
    for name in ("8995A_page1", "8995C"):
        pages[max(pages) + 1] = TABLES[name]["text"]


def test_schedule_c_carries_its_line_6_loss():
    parsed = seed(edit=schedule_c)
    field = parsed["fields"]["qbi_loss_carryforward"]
    assert field["value"] == 4000
    assert [source["line"] for source in field["provenance"]] == ["3", "4", "5", "6"]
    assert {source["form"] for source in field["provenance"]} == {"8995C"}


def test_schedule_c_needs_its_covered_year_parent():
    def orphan(pages, words, where):
        del pages[8]
        pages[max(pages) + 1] = TABLES["8995C"]["text"]
    parsed = seed(edit=orphan)
    assert "qbi_loss_carryforward" not in parsed["fields"]
    assert "covered-year Form 8995-A" in parsed["unresolved"]["qbi_loss"]


def test_schedule_c_and_form_8995_must_agree():
    def both(pages, words, where):
        for name in ("8995A_page1", "8995C"):
            pages[max(pages) + 1] = TABLES[name]["text"]
    with pytest.raises(P.ImportError, match="disagree on the QBI loss carryforward"):
        seed(edit=both)


def test_an_unreviewed_schedule_c_revision_is_not_read():
    def revised(pages, words, where):
        schedule_c(pages, words, where)
        pages[max(pages)] = pages[max(pages)].replace("(Rev. 12-2022)", "(Rev. 12-2025)")
    parsed = seed(edit=revised)
    assert "qbi_loss_carryforward" not in parsed["fields"]
    assert "Rev. 12-2022" in parsed["unresolved"]["8995C_revision"]


@pytest.mark.parametrize("old, new, message", [
    ("6 ( 4,000)", "6 4,000", "loss line as a gain"),
    ("5 ( 1,000)", "5 ( 2,000)", "arithmetic identity"),
])
def test_schedule_c_lines_follow_its_printed_arithmetic(old, new, message):
    def changed(pages, words, where):
        schedule_c(pages, words, where)
        assert old in pages[max(pages)]
        pages[max(pages)] = pages[max(pages)].replace(old, new)
    with pytest.raises(P.ImportError, match=message):
        seed(edit=changed)


def test_schedule_c_without_income_nets_nothing():
    """Line 4 is blank without income and line 5 then nets nothing; a line 5
    the reader missed would break line 6's subtraction instead."""
    def no_income(pages, words, where):
        schedule_c(pages, words, where)
        text = pages[max(pages)].replace(". . 4 1,000", ". . 4").replace("5 ( 1,000)", "5 ( )")
        pages[max(pages)] = text.replace("6 ( 4,000)", "6 ( 5,000)")
    assert seed(edit=no_income)["fields"]["qbi_loss_carryforward"]["value"] == 5000
    def unread_netting(pages, words, where):
        schedule_c(pages, words, where)
        pages[max(pages)] = pages[max(pages)].replace("5 ( 1,000)", "5 ( )")
    assert "qbi_loss_carryforward" not in seed(edit=unread_netting)["fields"]


# --- Review, confirmation and rollover -------------------------------------

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from agents.lib.secure_fs import secure_path
    secure_path(tmp_path)
    for directory in (tmp_path / "documents", tmp_path / "documents/prior_return"):
        directory.mkdir()
        secure_path(directory)
    source = tmp_path / "documents/prior_return/synthetic.pdf"
    source.write_bytes(b"%PDF synthetic")
    secure_path(source)
    for name, value in (("result.json", {"meta": {"tax_year": 2026}}),
                        ("taxpayer_profile.json", {"tax_year": 2026, "filing_status": "MFJ"})):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
        secure_path(tmp_path / name)
    pages, words, _ = document(*ALL)
    monkeypatch.setattr(P, "extract_pdf_text", lambda *a, **kw: pages)
    monkeypatch.setattr(P, "extract_pdf_words", lambda *a, **kw: words)
    return tmp_path


def pending(workspace):
    archive = P.import_pdf(workspace, "documents/prior_return/synthetic.pdf", 2026)
    parsed = json.loads((archive / "result.json").read_text(encoding="utf-8"))["prior_return_import"]
    return archive, {"seed_sha256": P.digest(parsed), "confirmed_fields": list(parsed["fields"]),
                     "confirmed_tables": P.table_confirmation_ids(parsed),
                     "questions": {k: "not_applicable" for k in parsed["unresolved"]},
                     "filing_status": "MFJ", "ira_owners": {"ira_basis_page_5": "primary"}}


def test_the_review_report_lists_every_table_id(workspace):
    archive, _ = pending(workspace)
    report = P.review_report(json.loads((archive / "result.json").read_text(encoding="utf-8")))
    assert "| 2024 | 600.00 | ftc_passive_2024 |" in report
    assert "| 180 | 16, 23 | 400.00 | 0.00 | ca_credit_180 |" in report
    assert "marks filing status MFJ" in report


@pytest.mark.parametrize("change", [
    lambda review: review["confirmed_tables"].pop(),
    lambda review: review["confirmed_tables"].append(review["confirmed_tables"][0]),
    lambda review: review["confirmed_tables"].__setitem__(0, "ftc_general_2024"),
])
def test_every_basket_year_and_credit_code_is_confirmed_once(workspace, change):
    _, review = pending(workspace)
    change(review)
    with pytest.raises(P.ImportError, match="exactly once"):
        P.confirm_import(workspace, 2025, review)


def test_the_confirmed_status_must_match_the_marked_box(workspace):
    _, review = pending(workspace)
    review["filing_status"] = "Single"
    with pytest.raises(P.ImportError, match="contradicts the box marked"):
        P.confirm_import(workspace, 2025, review)


def test_rollover_keeps_each_table_apart_and_blocks_on_a_balance(workspace):
    archive, review = pending(workspace)
    P.confirm_import(workspace, 2025, review)
    prior = load_prior(archive, 2025, allow_imported=True)
    current = CurrentState({"meta": {"tax_year": 2026}}, {"filing_status": "MFJ"}, {})
    changes = {c.json_path: c.prior_value for c in compute_diff(prior, current, {"carryforwards"}, {})["carryforwards"]}
    (basket,) = changes["prior_year.imported_ftc_baskets"]
    assert basket["basket"] == "passive" and basket["carryforward_by_origin_year"] == PASSIVE
    assert "reconciliation" not in basket
    assert {c["code"]: c["carryforward"] for c in changes["prior_year.imported_ca_credits"]} == CREDITS
    blocked = changes["prior_year.import_unresolved"]["questions"]
    assert {"foreign_tax_credit", "ca_credit_carryovers"} <= set(blocked)


def test_a_confirmed_review_without_its_tables_is_refused(workspace):
    archive, review = pending(workspace)
    P.confirm_import(workspace, 2025, review)
    result = json.loads((archive / "result.json").read_text(encoding="utf-8"))
    profile = json.loads((archive / "taxpayer_profile.json").read_text(encoding="utf-8"))
    stripped = copy.deepcopy(result)
    stripped["prior_return_import"]["review"].pop("confirmed_tables")
    stripped["meta"]["imported_review_sha256"] = P.digest(stripped["prior_return_import"]["review"])
    with pytest.raises(P.ImportError, match="carryover table requires individual confirmation"):
        P.validate_reviewed(stripped, profile)
    assert P.validate_reviewed(result, profile)


def test_an_amount_not_carried_blocks_rollover_by_itself(workspace, monkeypatch):
    """Every credit reads zero carried: the solar credit is used up and the
    PTET credit's column (d) is unread. Its 3,800 not carried still blocks."""
    pages, words, where = document("P540_side3", "P540_side4")
    for edit in (set_text("P540_side3", "1,000", "1,500", x=293, top=134),
                 set_text("P540_side3", "500", "0", x=552, top=134),
                 set_text("P540_side4", "100", "500", x=295, top=590),
                 set_text("P540_side4", "400", "0", x=552, top=590),
                 remove("P540_side4", "3,800", x=544, top=488)):
        edit(pages, words, where)
    monkeypatch.setattr(P, "extract_pdf_text", lambda *a, **kw: pages)
    monkeypatch.setattr(P, "extract_pdf_words", lambda *a, **kw: words)
    archive, review = pending(workspace)
    P.confirm_import(workspace, 2025, review)
    prior = load_prior(archive, 2025, allow_imported=True)
    current = CurrentState({"meta": {"tax_year": 2026}}, {"filing_status": "MFJ"}, {})
    changes = {c.json_path: c.prior_value for c in compute_diff(prior, current, {"carryforwards"}, {})["carryforwards"]}
    assert {c["code"]: c["carryforward"] for c in changes["prior_year.imported_ca_credits"]} == {
        "234": 0, "188": 1700, "180": 0, "242": 0}
    assert "ca_credit_carryovers" in changes["prior_year.import_unresolved"]["questions"]


def test_a_line_without_its_own_headings_never_borrows_the_next_lines():
    parsed = seed("P540_side3", "P540_side4", edit=remove("P540_side4", "(a)", x=106, top=120))
    assert "line 16 has no column headings" in parsed["unresolved"]["ca_credit_carryovers"]


def test_an_amount_not_carried_in_an_earlier_section_is_still_shown():
    """The solar credit carries 450 of B2's 500 into Section C: the 50 left
    behind is reported even though Section C itself carries all it may."""
    def short(pages, words, where):
        for text, new, x, top in (("500", "450", 552, 134), ("500", "450", 164, 590), ("400", "350", 552, 590)):
            word(words[where["P540_side4"]], text, x, top)["text"] = new
    parsed = seed("P540_side3", "P540_side4", edit=short)
    solar = next(c for c in parsed["table_evidence"]["ca_credits"] if c["code"] == "180")
    assert solar["carryforward"] == 350 and solar["not_carried"] == 50


def test_only_the_labels_beside_the_filing_status_heading_are_boxes():
    def far(pages, words, where):
        words[0] += [{"text": "Single", "x0": 110.0, "x1": 135.0, "top": 307.5, "bottom": 316.5},
                     {"text": "X", "x0": 99.0, "x1": 105.0, "top": 307.5, "bottom": 316.5}]
    assert seed(edit=far)["marked_filing_status"]["value"] == "MFJ"
    def twice(pages, words, where):
        words[0].append({"text": "Single", "x0": 300.0, "x1": 325.0, "top": 213.0, "bottom": 222.0})
    assert "label is printed twice" in seed(edit=twice)["unresolved"]["filing_status_mark"]


def test_punctuation_beside_a_box_is_not_a_mark():
    def dotted(pages, words, where):
        single = word(words[0], "Single", x=110, top=207)
        words[0].append({**single, "text": ".", "x0": 100.0, "x1": 102.0})
    assert seed(edit=dotted)["marked_filing_status"]["value"] == "MFJ"


@pytest.mark.parametrize("edit", [
    set_text("P540_side4", "Code:", "Code", x=50, top=565),
    lambda pages, words, where: (word(words[where["P540_side4"]], "Code:", x=50, top=565).update(
        text="Code:180", x1=84.4), words[where["P540_side4"]].remove(word(words[where["P540_side4"]], "180", x=72, top=565))),
])
def test_a_code_printed_without_its_colon_or_space_is_still_read(edit):
    assert credits(seed("P540_side3", "P540_side4", edit=edit)) == CREDITS


@pytest.mark.parametrize("edit, message", [
    # Section C's figures 7pt high, above their own headings: never ignored.
    (lambda pages, words, where: [move("P540_side4", text, dy=-7, x=x, top=590)(pages, words, where)
                                  for text, x in (("500", 164), ("100", 295), ("0", 434), ("400", 552))],
     "in no line's columns"),
    # Section C left blank: B2's 500 would be carried as if it were final.
    (lambda pages, words, where: [remove("P540_side4", text, x=x, top=590)(pages, words, where)
                                  for text, x in (("500", 164), ("100", 295), ("0", 434), ("400", 552))],
     "has no Section C line"),
])
def test_a_solar_credit_never_carries_its_section_b2_figure_unchecked(edit, message):
    parsed = seed("P540_side3", "P540_side4", edit=edit)
    assert message in parsed["unresolved"]["ca_credit_carryovers"]
    assert "table_evidence" not in parsed


def blank_page_1s(pages, words, where):
    """Carryovers all under five years old: every page 1 prints nothing in
    its columns, and each page 2 carries nothing in (viii)."""
    for index, text in pages.items():
        if text == TABLES["1116B_page1"]["text"]:
            words[index] = [w for w in words[index] if not (
                w["x0"] > 200 and 400 < w["top"] < 560 and P._VALUE.fullmatch(w["text"]) and w["text"] != "-0-")]
        elif text == TABLES["1116B_page2"]["text"]:
            for old, new, x, top in (("500", "0", 265, 278), ("1,500", "1,000", 734, 278), ("-200", "0", 262, 302),
                                     ("-300", "-100", 738, 302), ("-300", "0", 262, 332), ("-300", "0", 738, 332)):
                word(words[index], old, x, top)["text"] = new


def test_several_categories_with_blank_page_1s_cannot_bind_their_page_2s():
    def edit(pages, words, where):
        general(pages, words, where)
        blank_page_1s(pages, words, where)
    parsed = seed("1116B_page1", "1116B_page2", "1116B_page1", "1116B_page2", edit=edit)
    assert "must carry a subtotal" in parsed["unresolved"]["ftc_schedule_b"]


def test_one_blank_page_1_among_several_categories_still_binds():
    """Only passive's page 1 is blank: a page 2 printed after the other page 1
    would repeat the wrong subtotal, so each pair still binds by its own."""
    def edit(pages, words, where):
        general_of_its_own(pages, words, where)
        first = {index: words[index] for index in (where["1116B_page1"], where["1116B_page2"])}
        blank_page_1s({index: pages[index] for index in first}, first, where)
        words.update(first)
    parsed = seed("1116B_page1", "1116B_page2", "1116B_page1", "1116B_page2", edit=edit)
    baskets = {b["basket"]: b["carryforward_by_origin_year"] for b in parsed["table_evidence"]["ftc_baskets"]}
    assert baskets == {"passive": PASSIVE, "general": PASSIVE}


def test_one_category_with_a_blank_page_1_binds_by_position():
    parsed = seed("1116B_page1", "1116B_page2", edit=blank_page_1s)
    (basket,) = parsed["table_evidence"]["ftc_baskets"]
    assert basket["carryforward_by_origin_year"] == {**PASSIVE}


def test_the_amt_credit_blocks_only_where_ftb_3510_did_not_carry_it(workspace, monkeypatch):
    def rolled(drop):
        pages, words, _ = document("P540_side3", "P540_side4", drop=drop)
        for edit in (set_text("P540_side3", "1,000", "1,500", x=293, top=134),
                     set_text("P540_side3", "500", "0", x=552, top=134),
                     set_text("P540_side4", "900", "400", x=164, top=134),
                     remove("P540_side4", "500", x=552, top=134), remove("P540_side4", "500", x=164, top=590),
                     remove("P540_side4", "100", x=295, top=590), remove("P540_side4", "0", x=434, top=590),
                     remove("P540_side4", "400", x=552, top=590),
                     set_text("P540_side4", "1,200", "5,000", x=293, top=488), set_text("P540_side4", "3,800", "0", x=544, top=488)):
            edit(pages, words, _)
        monkeypatch.setattr(P, "extract_pdf_text", lambda *a, **kw: pages)
        monkeypatch.setattr(P, "extract_pdf_words", lambda *a, **kw: words)
        for path in (workspace / "archive",):
            if path.exists():
                import shutil
                shutil.rmtree(path)
        archive, review = pending(workspace)
        P.confirm_import(workspace, 2025, review)
        prior = load_prior(archive, 2025, allow_imported=True)
        current = CurrentState({"meta": {"tax_year": 2026}}, {"filing_status": "MFJ"}, {})
        return {c.json_path: c.prior_value for c in compute_diff(prior, current, {"carryforwards"}, {})["carryforwards"]}
    with_3510 = rolled(drop=())
    assert "ca_credit_carryovers" not in with_3510["prior_year.import_unresolved"]["questions"]
    without = rolled(drop=(11,))
    assert "ca_credit_carryovers" in without["prior_year.import_unresolved"]["questions"]


def test_two_form_1040_pages_marking_different_statuses_are_a_question():
    def second(pages, words, where):
        index = max(pages) + 1
        pages[index] = TABLES["1040_page1"]["text"]
        words[index] = words_of("1040_page1")
        mark = word(words[index], "4", x=99, top=219)
        mark["top"], mark["bottom"] = mark["top"] - 12, mark["bottom"] - 12
    assert "different filing statuses" in seed(edit=second)["unresolved"]["filing_status_mark"]


def test_a_line_number_printed_twice_in_the_margin_is_a_question():
    def twice(pages, words, where):
        five = word(words[where["1116B_page1"]], "5", x=39, top=453)
        words[where["1116B_page1"]].append({**five, "top": five["top"] + 6, "bottom": five["bottom"] + 6})
    assert "printed twice in the margin" in seed("1116B_page1", "1116B_page2", edit=twice)["unresolved"]["ftc_schedule_b"]


def test_a_category_printed_twice_is_a_question():
    def twice(pages, words, where):
        page = words[where["1116B_page1"]]
        for text in ("c", "Passive"):
            w = word(page, text, top=152)
            page.append({**w, "top": w["top"] + 300, "bottom": w["bottom"] + 300})
    assert "category label is printed twice" in seed("1116B_page1", "1116B_page2", edit=twice)["unresolved"]["ftc_schedule_b"]


@pytest.mark.parametrize("glyph", ["\u2610", "\u25a1", "o", "q"])
def test_an_empty_box_glyph_is_not_a_mark(glyph):
    def boxes(pages, words, where):
        single = word(words[0], "Single", x=110, top=207)
        words[0].append({**single, "text": glyph, "x0": 98.0, "x1": 104.0})
    assert seed(edit=boxes)["marked_filing_status"]["value"] == "MFJ"


def test_a_review_confirmed_before_the_table_readers_still_validates(workspace, monkeypatch):
    """A seed with no tables, confirmed by a review with no `confirmed_tables`."""
    pages, _, _ = document()
    monkeypatch.setattr(P, "extract_pdf_text", lambda *a, **kw: pages)
    monkeypatch.setattr(P, "extract_pdf_words", lambda *a, **kw: {})
    archive, review = pending(workspace)
    P.confirm_import(workspace, 2025, review)
    result = json.loads((archive / "result.json").read_text(encoding="utf-8"))
    profile = json.loads((archive / "taxpayer_profile.json").read_text(encoding="utf-8"))
    result["prior_return_import"]["review"].pop("confirmed_tables")
    result["meta"]["imported_review_sha256"] = P.digest(result["prior_return_import"]["review"])
    assert P.validate_reviewed(result, profile)
