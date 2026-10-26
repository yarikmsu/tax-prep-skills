#!/usr/bin/env python3
"""Reviewed, local-only import of official prior-return PDF form lines.

An import is an archive SEED, never a computed return. Unreadable lines and
unavailable worksheets remain questions. No source text, names, or unmasked
identifiers are persisted. The review is bound to the entire seed and PDF.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.parse_utils import (  # noqa: E402
    _validate_file_path, _word_rows, extract_pdf_text, extract_pdf_words,
)
from agents.lib.secure_fs import (  # noqa: E402
    atomic_paths, first_non_finite, mask_ssn, secure_path,
)

SCHEMA = "prior-return-import/v1"
# Form lines, not tax rates. Every additional covered year needs its own review.
REVIEWED_YEARS = {2025}
STATUSES = {"Single", "MFJ", "MFS", "HOH", "QSS"}
# An `ira_owners` value marking a Form 8606 page as a second copy of another.
DUPLICATE = "duplicate_of:"
BOUNDARY = ("Parse boundary NOT enforced — parsed in the main session under prompt-level "
            "rules, because this agent cannot give a sub-task a closed tool set.")
SOURCES = {
    "1040": "https://www.irs.gov/pub/irs-prior/f1040--2025.pdf",
    "D": "https://www.irs.gov/pub/irs-prior/f1040sd--2025.pdf",
    "8606": "https://www.irs.gov/pub/irs-prior/f8606--2025.pdf",
    "8801": "https://www.irs.gov/pub/irs-prior/f8801--2025.pdf",
    "8995": "https://www.irs.gov/pub/irs-prior/f8995--2025.pdf",
    "540": "https://www.ftb.ca.gov/forms/2025/2025-540.pdf",
    "3510": "https://www.ftb.ca.gov/forms/2025/2025-3510.pdf",
    # Worksheet 4-1. The 2025 edition prints it for the 2024-to-2025 carryover;
    # the TY2025 return's figures go through the same lines.
    "capital_worksheet": "https://www.irs.gov/pub/irs-prior/p550--2025.pdf",
    "8995-A": "https://www.irs.gov/pub/irs-prior/f8995a--2025.pdf",
    "8995C": "https://www.irs.gov/pub/irs-pdf/f8995ac.pdf",
    "1116B": "https://www.irs.gov/pub/irs-pdf/f1116sb.pdf",
    "P540": "https://www.ftb.ca.gov/forms/2025/2025-540-p.pdf",
}
# Only the lines needed by the seed and their printed arithmetic dependencies.
LINES = {
    "1040": {"7a", "9", "10", "11a", "11b", "12e", "13a", "13b", "14", "15",
             "24", "34", "35a", "36"},
    "D": {"7", "15", "16", "21"},
    "8606": {"1", "2", "3", "13", "14"},
    "8801": {"21", "25", "26"},
    "8995": {"2", "3", "6", "7", "16", "17"},
    "540": {"97", "98", "99"},
    "3510": {"32", "38", "39"},
    # Located for the year they bind or read by position, not by line label.
    "8995-A": set(),
    "8995C": {"2", "3", "4", "5", "6"},
    "1116B": set(),
    "P540": set(),
}
# Revision forms print a revision instead of a tax year: Schedule C (Form
# 8995-A) is bound to its covered-year Form 8995-A, Schedule B (Form 1116) to
# its own calendar-year entry. Only the reviewed revision is read.
_REVISIONS = {"8995C": "Rev. 12-2022", "1116B": "Rev. 12-2022"}
_MAGNITUDE = r"\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_NUMBER = rf"(?:-0-|-?{_MAGNITUDE}|\({_MAGNITUDE}\))"
_VALUE = re.compile(r"^" + _NUMBER + r"$")


class ImportError(ValueError):
    """An import cannot establish an unambiguous, reviewed prior-year fact."""


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _amount(token: str) -> float:
    if not _VALUE.fullmatch(token):
        raise ImportError("Unreadable official-form amount")
    if token == "-0-":
        return 0.0
    negative = token.startswith("(") and token.endswith(")")
    if token.startswith("(") != token.endswith(")"):
        raise ImportError("Unbalanced parentheses in official-form amount")
    value = float(token.strip("()$").replace(",", ""))
    if not math.isfinite(value):
        raise ImportError("Official-form amounts must be finite")
    return round(-value if negative else value, 2)


# Every official page repeats its form and year in a running header or footer
# ("Form 8606 (2025) Page 2", "Cat. No. 11338H Schedule D (Form 1040) 2025").
# The title block cannot identify a page: pdfplumber reads Form 1040's rotated
# "Form" label as "mroF", and instruction lines name other forms throughout
# ("1040-NR, line 7a", "2025 Schedule P (540), Part III").
_RUNNING_HEADERS = (
    (r"(?:^|\s)Form (\d{4}(?:-[A-Z]{1,2})?) \((20\d{2}|Rev\. [^)]+)\)(?: Page \d+)?(?: Created \S+)?$", None),
    (r"(?:^|\s)Schedule D \(Form 1040\) (20\d{2})(?: Page \d+)?(?: Created \S+)?$", "D"),
    (r"(?:^|\s)Form (540(?:NR)?) (20\d{2}) Side \d\b", None),
    (r"^Side \d Form (540(?:NR)?) (20\d{2})\b", None),
    (r"(?:^|\s)FTB 3510 (20\d{2})$", "3510"),
    (r"(?:^|\s)Schedule C \(Form 8995-A\) \((Rev\. [^)]+)\)$", "8995C"),
    (r"(?:^|\s)Schedule B \(Form 1116\) \((Rev\. [^)]+)\)(?: Page \d+)?$", "1116B"),
    (r"(?:^|\s)Schedule P \(540\) (20\d{2}) Side \d$", "P540"),
    (r"^Side \d Schedule P \(540\) (20\d{2})\b", "P540"),
)
# Form 1040-SR prints Form 1040's line numbers, labels and identities.
_SAME_LINES = {"1040-SR": "1040"}
_OUT_OF_SCOPE_FORMS = {"1040-NR", "1040-X", "540NR"}
_OUT_OF_SCOPE_TITLES = re.compile(r"U\.S\. Nonresident Alien Income Tax Return|Amended U\.S\. Individual Income "
                                  r"Tax Return|Nonresident or Part-Year Resident Income Tax Return", re.I)
# Forms printing a cents column after the amount box ("97 .00", "39 00").
_CENTS_COLUMN = {"540", "3510"}


def _running_headers(text: str) -> list[tuple[str, str]]:
    found = []
    for pattern, name in _RUNNING_HEADERS:
        for match in re.finditer(pattern, text, re.M):
            form = name or match[1]
            found.append((_SAME_LINES.get(form, form), match.groups()[-1]))
    return found


def _out_of_scope(text: str) -> bool:
    """A nonresident, part-year or amended return, by its own header or title.
    A checkbox label saying AMENDED is normal; only an affirmative mark counts."""
    return (any(form in _OUT_OF_SCOPE_FORMS for form, _ in _running_headers(text))
            or bool(_OUT_OF_SCOPE_TITLES.search(text))
            or bool(re.search(r"(?:\[X\]|\u2612|\bX)\s*(?:AMENDED|1040-X)", text, re.I))
            or bool(re.search(r"(?m)^(?=.*\bAMENDED return\b).*(?:^|\s)(?:X|\u2612|\[X\])(?:\s|$)", text))
            # Form 540's row starts with its label; a ticked box puts a glyph
            # before it, which a flattened checkbox extracts as "4", "n" or a cid.
            or bool(re.search(r"(?m)^\s*(?!(?:\u2610|\u25a1|o|q|\[\s?\])\s)(?:\S{1,3}|\(cid:\d+\))\s+"
                              r"Check here if this is an AMENDED return", text)))


def _form(text: str) -> tuple[str, int | str] | None:
    """The page's form and year from its running header or footer; None for a
    page of any form this reader does not map. A revision form returns its
    revision, which binds no year by itself."""
    headers = {(form, year) for form, year in _running_headers(text) if form in LINES}
    if not headers:
        return None
    if len({form for form, _ in headers}) != 1 or len({year for _, year in headers}) != 1:
        raise ImportError("Official page needs one form and one unambiguous covered tax year")
    form, year = headers.pop()
    if form in _REVISIONS:
        return form, year
    if not year.isdigit():
        raise ImportError("Official page needs one form and one unambiguous covered tax year")
    return form, int(year)


def _labelled_amount(row: str, form: str) -> tuple[str, str] | None:
    """The line label and amount printed in a row's right-hand box.

    On the official layouts a box label follows a dot leader or bullet
    (". . . 34", "• 98"), and a filled amount follows the label. A bullet
    whose font has no Unicode map extracts as "(cid:127)". Wrapped
    descriptions put the box on a continuation row, and sidebars print text or
    figures before the line number ("Refund 34", "$15,750 14"), so the row's
    start identifies nothing. Prose ending in a line number ("Don't complete
    lines 21") has no leader. Schedule D line 21 and Form 8995's loss lines
    pre-print the parentheses: "21 ( 3,000 )" is a loss.
    """
    row = row.rstrip()
    if form in _CENTS_COLUMN:
        row = re.sub(r"\s+\.?\s?00$", "", row)
    # A pre-printed "( )" is padded with spaces, and a flattened amount
    # inside it can extract with its characters apart: "( 3 , 0 0 0 )".
    row = re.sub(r"\(\s*((?:[\d,.] )+[\d,.])\s*\)$", lambda m: "(" + m[1].replace(" ", "") + ")", row)
    match = re.search(rf"(?:^|[.\u2022]|\(cid:\d+\))\s*(\d{{1,3}}[a-z]?)\s+"
                      rf"(?:\(\s*({_MAGNITUDE})\s*\)|({_NUMBER}))$", row)
    if not match:
        return None
    return match[1], f"({match[2]})" if match[2] else match[3]


def locate_lines(text: str, form: str, page: int, source_digest: str) -> dict:
    """Read each mapped line's amount from its labelled box.

    A blank box, a detached amount or an unreadable figure stays unresolved,
    never a positional guess. Conflicting copies refuse even when one of them
    would be useful.
    """
    text = mask_ssn(text)
    found: dict[str, dict] = {}
    for row in text.splitlines():
        labelled = _labelled_amount(row, form)
        if labelled is None or labelled[0] not in LINES[form]:
            continue
        line, token = labelled
        value = _amount(token)
        if line in found and found[line]["value"] != value:
            raise ImportError("Conflicting values for one official form line")
        found[line] = {"value": value, "provenance": {
            "sha256": source_digest, "page": page, "form": form, "line": line}}
    return found


def _check(lines: dict, output: str, inputs: tuple[str, ...], compute) -> None:
    if output in lines and all(key in lines for key in inputs):
        expected = round(compute(*(lines[key]["value"] for key in inputs)), 2)
        if abs(lines[output]["value"] - expected) > 0.01:
            raise ImportError("Official form fails its printed arithmetic identity")


def _checks(form: str, lines: dict) -> None:
    signed = {"1040": {"7a", "9", "11a", "11b"}, "D": set(LINES["D"]),
              "8995": set(LINES["8995"]), "8995C": {"2", "3", "5", "6"}}.get(form, set())
    if any(record["value"] < 0 for key, record in lines.items() if key not in signed):
        raise ImportError("A nonnegative official form line contains a negative amount")
    if form == "1040":
        _check(lines, "11a", ("9", "10"), lambda a, b: a - b)
        _check(lines, "11b", ("11a",), lambda a: a)
        _check(lines, "14", ("12e", "13a", "13b"), lambda a, b, c: a + b + c)
        _check(lines, "15", ("11b", "14"), lambda a, b: max(a - b, 0))
        _check(lines, "34", ("35a", "36"), lambda a, b: a + b)
    elif form == "D":
        _check(lines, "16", ("7", "15"), lambda a, b: a + b)
    elif form == "8606":
        _check(lines, "3", ("1", "2"), lambda a, b: a + b)
        _check(lines, "14", ("3", "13"), lambda a, b: a - b)
    elif form == "8801":
        _check(lines, "26", ("21", "25"), lambda a, b: a - b)
    elif form == "8995":
        _check(lines, "16", ("2", "3"), lambda a, b: min(a + b, 0))
        _check(lines, "17", ("6", "7"), lambda a, b: min(a + b, 0))
    elif form == "540":
        _check(lines, "99", ("97", "98"), lambda a, b: a - b)
    elif form == "3510":
        _check(lines, "39", ("32", "38"), lambda a, b: a - b)
    elif form == "8995C":
        # Lines 2, 3, 5 and 6 pre-print their parentheses: each is a loss.
        if any(lines[n]["value"] > 0 for n in ("2", "3", "5", "6") if n in lines):
            raise ImportError("Schedule C (Form 8995-A) prints a loss line as a gain")
        _check(lines, "5", ("3", "4"), lambda loss, income: -min(-loss, income))
        _check(lines, "6", ("3", "5"), lambda loss, netted: min(loss - netted, 0))


def _form_8606_owner(text: str) -> str | None:
    """The masked SSN Form 8606 prints for its own owner, from the rows
    between its name label and its address label. Software headers that
    repeat the primary filer's SSN on every page sit outside them."""
    rows = text.splitlines()
    start = next((i for i, row in enumerate(rows) if row.startswith("Name. If married, file a separate form")), None)
    end = next((i for i, row in enumerate(rows) if row.startswith("Home address")), None)
    if start is None or end is None or end <= start:
        return None
    # mask_ssn writes XXX-XX-; software may already print its own mask.
    found = set(re.findall(r"(?:XXX|\*{3})-(?:XX|\*{2})-(\d{4})\b", "\n".join(rows[start:end + 1])))
    return f"XXX-XX-{found.pop()}" if len(found) == 1 else None


def _with_blanks(lines: dict, output: str, inputs: tuple[str, ...], compute, blank: set) -> list | None:
    """The records proving `output`, or None. An input the form leaves blank
    when it is zero ("If you didn't..., enter the amount from line 3 on line
    14") counts as zero only where the printed identity still holds: an
    unreadable nonzero amount then breaks the identity and stays a question."""
    if output not in lines or any(n not in lines and n not in blank for n in inputs):
        return None
    values = [lines[n]["value"] if n in lines else 0.0 for n in inputs]
    if abs(lines[output]["value"] - round(compute(*values), 2)) > 0.01:
        return None
    return [lines[n] for n in (*inputs, output) if n in lines]


# Carryover TABLES. A figure in a grid belongs to the line and column it is
# printed under, which text order does not record: pdfplumber finds no table
# on Schedule P (540), and Schedule B (Form 1116) has no cell for a line number
# or a subtotal heading. These readers take word positions
# (`parse_utils.extract_pdf_words`) and find each cell from the form's own
# printed labels: columns under their headings, lines by the numbers in the
# left margin. No coordinate is fixed; a figure outside every cell, or two in
# one, leaves the table a question instead of a guess.


class _Unreadable(Exception):
    """A table whose cells cannot be located without guessing."""


# Glyphs a form prints for an EMPTY box, and punctuation that is no mark.
# Anything else in a box marks it: a flattened check extracts as "4", "8" or
# a cid, software prints "X".
_EMPTY_BOX = {"\u2610", "\u25a1", "o", "q", ".", ","}


def _mid(word: dict, low: str, high: str) -> float:
    return (word[low] + word[high]) / 2


def _overlaps(a: dict, b: dict) -> bool:
    return a["top"] < b["bottom"] and b["top"] < a["bottom"]


def _is_mark(word: dict) -> bool:
    return ((len(word["text"]) == 1 and word["text"] not in _EMPTY_BOX)
            or bool(re.fullmatch(r"\(cid:\d+\)", word["text"])))


def _right_of(words: list, word: dict) -> list:
    """The words after `word` on its row, nearest first."""
    return sorted((w for w in words if w is not word and _overlaps(w, word) and w["x0"] >= word["x1"] - 0.5),
                  key=lambda w: w["x0"])


def _left_of(words: list, word: dict) -> list:
    return sorted((w for w in words if w is not word and _overlaps(w, word) and w["x1"] <= word["x0"] + 0.5),
                  key=lambda w: -w["x1"])


def _phrases(words: list, phrase: tuple) -> list:
    """The first word of each place `phrase` prints along one row. One pass
    over the rows, however often a word repeats on the page."""
    size = len(phrase)
    return [row[i] for row in _word_rows(words) for i in range(len(row) - size + 1)
            if all(w["text"] == text for w, text in zip(row[i:i + size], phrase))]


def _bands(centers: list) -> list:
    """Column bounds halfway between printed headings; an outer column is as
    wide as its neighbour."""
    if len(centers) < 2 or any(b <= a for a, b in zip(centers, centers[1:])):
        raise _Unreadable("column headings are out of order")
    edges = [centers[0] - (centers[1] - centers[0]) / 2]
    edges += [(a + b) / 2 for a, b in zip(centers, centers[1:])]
    edges.append(centers[-1] + (centers[-1] - centers[-2]) / 2)
    return list(zip(edges, edges[1:]))


def _cell(words: list) -> float | None:
    """A cell's one figure. Blank is None here: whether it is zero is the
    caller's question, answered by the form's arithmetic."""
    tokens = [w["text"] for w in sorted(words, key=lambda w: w["x0"]) if w["text"] != "$"]
    if not tokens:
        return None
    if len(tokens) != 1 or not _VALUE.fullmatch(tokens[0]):
        raise _Unreadable("a cell holds text or more than one figure")
    return _amount(tokens[0])


def _margin_numbers(words: list, wanted: set, grid_left: float) -> dict:
    """Line numbers in the left margin. The same numerals recur in the line
    descriptions ("combine lines 1 and 2"); the margin's are right-aligned,
    so they share the leftmost right edge."""
    candidates = [w for w in words if w["text"] in wanted and w["x1"] <= grid_left]
    if not candidates:
        return {}
    edge = min(w["x1"] for w in candidates)
    found: dict[str, dict] = {}
    for word in candidates:
        if word["x1"] <= edge + 2:
            if word["text"] in found:
                raise _Unreadable("a line number is printed twice in the margin")
            found[word["text"]] = word
    return found


# Schedule B (Form 1116): columns (i)-(vi) are the 10th to 5th preceding tax
# years, (vii) their subtotal; page 2 carries it as (viii), then the 4th to 1st
# preceding years, the current year (xiii) and the total (xiv).
_SB_HEADINGS = (("(i)", "(ii)", "(iii)", "(iv)", "(v)", "(vi)", "(vii)"),
                ("(viii)", "(ix)", "(x)", "(xi)", "(xii)", "(xiii)", "(xiv)"))
_SB_COLUMNS = _SB_HEADINGS[0] + _SB_HEADINGS[1]
_SB_LINES = ("3", "4", "5", "6", "7", "8")
_SB_ORIGINS = dict(zip(_SB_HEADINGS[0][:6] + _SB_HEADINGS[1][1:6], range(10, -1, -1)))
_SB_CATEGORIES = {"a": ("Reserved",), "b": ("Foreign", "branch"), "c": ("Passive",), "d": ("General",),
                  "e": ("Section", "901(j)"), "f": ("Certain", "income"), "g": ("Lump-sum",)}
# Sections 901(j) and treaty re-sourcing need their country; lump sums their worksheet.
_SB_BASKETS = {"b": "foreign_branch", "c": "passive", "d": "general"}


def _headings(words: list, labels: tuple) -> list:
    """Each column heading once. The headings share a row; the same numerals
    recur in a note under them ("add columns (i) through (vi)")."""
    found = [w for w in words if w["text"] in labels]
    if not found:
        raise _Unreadable("the column headings are missing")
    tops = sorted(w["top"] for w in found)
    median = tops[len(tops) // 2]
    heads: dict[str, dict] = {}
    for word in found:
        if abs(word["top"] - median) <= 5:
            if word["text"] in heads:
                raise _Unreadable("a column heading is printed twice")
            heads[word["text"]] = word
    if set(heads) != set(labels):
        raise _Unreadable("a column heading is missing")
    return [heads[label] for label in labels]


def _line_bands(words: list, numbers: dict, grid_left: float) -> dict:
    """A line's figures print beside its description, which wraps over
    several rows. Each line's band runs to the next line's number; the last
    ends with its description, at the next row starting in the margin."""
    ordered = sorted(numbers.items(), key=lambda item: item[1]["top"])
    bands = {line: (number["top"] - 2, following["top"] - 2)
             for (line, number), (_, following) in zip(ordered, ordered[1:])}
    line, number = ordered[-1]
    labels = [w for w in words if w["x1"] <= grid_left]
    after = _right_of(labels, number)
    if not after:
        raise _Unreadable("a line number has no description")
    rows = _word_rows(words)
    index = next(i for i, row in enumerate(rows) if any(w is number for w in row))
    block = [w for w in rows[index] if w["x1"] <= grid_left]
    for row in rows[index + 1:]:
        if row[0]["x0"] < after[0]["x0"] - 1:
            break
        block += [w for w in row if w["x1"] <= grid_left]
    bands[line] = (number["top"] - 2, max(w["bottom"] for w in block) + 3)
    return bands


def _schedule_b_grid(words: list, labels: tuple) -> dict:
    """{(line, heading): figure or None} for lines 3-8 on one page. The bands
    and columns partition the grid, so every word in it lands in one cell."""
    columns = _bands([_mid(w, "x0", "x1") for w in _headings(words, labels)])
    numbers = _margin_numbers(words, set(_SB_LINES), columns[0][0])
    if set(numbers) != set(_SB_LINES):
        raise _Unreadable("a line number is missing")
    bands = _line_bands(words, numbers, columns[0][0])
    return {(line, label): _cell([w for w in words if left <= _mid(w, "x0", "x1") < right
                                  and bands[line][0] <= _mid(w, "top", "bottom") < bands[line][1]])
            for line in _SB_LINES for label, (left, right) in zip(labels, columns)}


def _schedule_b_category(words: list) -> str:
    """The one category box marked, between its letter and its label."""
    marked, seen = [], set()
    for letter, phrase in _SB_CATEGORIES.items():
        anchors = _phrases(words, phrase)
        if len(anchors) > 1:
            raise _Unreadable("a category label is printed twice")
        for anchor in anchors:
            before = _left_of(words, anchor)
            if before and before[0]["text"] == letter:
                box = None
            elif len(before) > 1 and before[1]["text"] == letter:
                box = before[0]
            else:
                continue
            seen.add(letter)
            if box is not None and box["text"] not in _EMPTY_BOX:
                if not _is_mark(box):
                    raise _Unreadable("text where a category box is printed")
                marked.append(letter)
    if seen != set(_SB_CATEGORIES):
        raise _Unreadable("the category boxes are not where the form prints them")
    if len(marked) != 1:
        raise _Unreadable("Schedule B needs exactly one category box marked")
    return marked[0]


def _schedule_b_year(words: list) -> int:
    """The calendar year entered after its printed "20". A fiscal-year
    schedule fills the dates after "beginning" and "ending" instead."""
    calendar = _phrases(words, ("calendar", "year"))
    if len(calendar) != 1:
        raise _Unreadable("the calendar-year entry is missing")
    row = _right_of(words, _right_of(words, calendar[0])[0])
    entry = []
    for word in row:
        if word["text"] == ",":
            break
        entry.append(word["text"])
    year = re.fullmatch(r"20(?:20)?(\d{2})", "".join(entry))
    beginning = next((w for w in row if w["text"] == "beginning"), None)
    dates = []
    for word in _right_of(words, beginning) if beginning else []:
        if word["text"] == ".":
            break  # the entry ends; the OMB number shares its row
        dates.append(word["text"])
    if year is None or any(text not in {",", "20", "and", "ending"} for text in dates):
        raise _Unreadable("Schedule B needs a filled calendar year, not a fiscal year")
    return 2000 + int(year[1])


def _schedule_b_checks(v: dict) -> None:
    """Every printed subtotal, the line 8 combination and the shaded cells."""
    def total(line, labels):
        return round(sum(v[line, label] for label in labels), 2)
    if any(v[line, "(viii)"] != v[line, "(vii)"] for line in _SB_LINES):
        raise _Unreadable("page 2's column (viii) does not carry page 1's subtotal (vii)")
    for line in _SB_LINES:
        if (v[line, "(vii)"] != total(line, _SB_HEADINGS[0][:6])
                or v[line, "(xiv)"] != total(line, _SB_HEADINGS[1][:6])):
            raise _Unreadable(f"line {line} fails its printed subtotals")
    for label in _SB_COLUMNS:
        if v["8", label] != round(sum(v[line, label] for line in _SB_LINES[:5]), 2):
            raise _Unreadable(f"column {label} fails line 8, the combination of lines 3 through 7")
        if any(v[line, label] > 0 for line in ("4", "5", "7")) or any(v[line, label] < 0 for line in ("3", "6", "8")):
            raise _Unreadable(f"column {label} prints a figure with the wrong sign")
    # Only the 10th preceding year expires; only the current year generates
    # or carries back, and it has nothing carried in or used.
    subtotals = {"(vii)", "(viii)", "(xiv)"}
    if (any(v["5", label] for label in _SB_COLUMNS if label not in subtotals | {"(i)"})
            or any(v[line, label] for line in ("6", "7") for label in _SB_COLUMNS if label not in subtotals | {"(xiii)"})
            or any(v[line, "(xiii)"] for line in ("3", "4")) or v["8", "(i)"]):
        raise _Unreadable("a figure in a cell the form shades")


def _ftc_baskets(schedule_pages: list, words: dict, year: int, source_digest: str) -> list:
    """Each category's carryover by origin year, from its page 1 and the page
    2 printed right after it. The pair must agree on its carried subtotal."""
    out: dict[str, dict] = {}
    index = 0
    while index < len(schedule_pages):
        page, second = schedule_pages[index]
        following = schedule_pages[index + 1] if index + 1 < len(schedule_pages) else None
        if second or following is None or not following[1] or following[0] != page + 1:
            raise _Unreadable("each Schedule B page 1 needs its page 2 right after it")
        if page not in words or page + 1 not in words:
            raise _Unreadable("Schedule B needs its word positions")
        category = _schedule_b_category(words[page])
        if _schedule_b_year(words[page]) != year:
            raise ImportError("Official form page belongs to a different tax year")
        if category not in _SB_BASKETS:
            raise _Unreadable(f"category {category} needs its country or worksheet evidence")
        grid = {**_schedule_b_grid(words[page], _SB_HEADINGS[0]),
                **_schedule_b_grid(words[page + 1], _SB_HEADINGS[1])}
        values = {key: 0.0 if value is None else value for key, value in grid.items()}
        _schedule_b_checks(values)
        basket = _SB_BASKETS[category]
        if basket in out:
            raise _Unreadable("two Schedules B for one category")
        out[basket] = {
            "basket": basket,
            "carryforward_by_origin_year": {str(year - offset): values["8", label]
                                            for label, offset in _SB_ORIGINS.items() if offset < 10},
            "reconciliation": {line: {label: values[line, label] for label in _SB_COLUMNS} for line in _SB_LINES},
            "provenance": {"sha256": source_digest, "pages": [page + 1, page + 2], "form": "1116B"},
        }
        index += 2
    # Page 2 prints no category. With one category the adjacent page 2 is its
    # own; with several, it binds by the subtotal (vii) it repeats, and two page
    # 1s carrying the same one (nothing, say) let their pairs, printed out of
    # order, swap unseen.
    subtotals = [tuple(b["reconciliation"][line]["(vii)"] for line in _SB_LINES) for b in out.values()]
    if len(set(subtotals)) < len(subtotals):
        raise _Unreadable("with several categories, each page 1 must carry a subtotal of its own that its page 2 repeats")
    return list(out.values())


# Schedule P (540) Part III lines that carry a credit forward, by section, and
# the codes the form prints itself. Side 3 prints lines 6-15 and Side 4 lines
# 16-25, and neither prints a Part I or II line. A solar credit's Section B2
# carryover is Section C's credit amount; its carryforward is Section C's
# column (d). Line 4, on Side 2, carries code 162 only above the business
# credit limit and is never read: a carryover there is a question.
_SP_SECTIONS = {"6": "A2", "7": "A2", "8": "A2", "9": "A2", "10": "A2", "16": "B2", "17": "B2",
                "18": "B2", "19": "B2", "21": "B4", "23": "C", "24": "C"}
_SP_PRINTED = {"10": "188", "21": "242", "23": "180", "24": "181"}
_SP_SOLAR = {"180", "181"}
_SP_SIDES = {3: {str(n) for n in range(6, 16)}, 4: {str(n) for n in range(16, 26)}}
# The columns a line has boxes in; every other line has all four.
_SP_BOXES = {"11": "c", "12": "abc", "13": "abc", "14": "abc", "15": "abc", "20": "abc", "22": "c", "25": "c"}
_SP_HEADINGS = (("(a)",), ("(b)", "(b)*"), ("(c)",), ("(d)",))


def _schedule_p_columns(row: list) -> list | None:
    """Columns (a)-(d) from one heading row, centred on each heading phrase."""
    found = [next((w for w in row if w["text"] in options), None) for options in _SP_HEADINGS]
    heads = [head for head in found if head is not None]
    if len(heads) != len(_SP_HEADINGS):
        return None
    ends = [h["x0"] for h in heads[1:]] + [max(w["x1"] for w in row) + 1]
    return _bands([(head["x0"] + max(w["x1"] for w in row if head["x0"] <= w["x0"] < end)) / 2
                   for head, end in zip(heads, ends)])


def _schedule_p_headings(words: list) -> tuple[list, list, float]:
    """The page's rows, its heading rows with their columns, and the margin
    the line numbers print in."""
    rows = _word_rows(words)
    headings = [(row, columns) for row in rows if (columns := _schedule_p_columns(row))]
    if not headings:
        raise _Unreadable("the column headings are missing")
    return rows, headings, min(columns[0][0] for _, columns in headings)


def _schedule_p_cells(words: list, page: tuple, number: dict, line: str) -> tuple[list, dict, float]:
    """One line's heading row and the band of figures under it, to the next
    row starting in the margin, with its cells (a)-(d) and where it stops."""
    rows, headings, margin = page
    following = next((row[0]["top"] for row in rows
                      if row[0]["top"] > number["bottom"] and row[0]["x0"] < margin), float("inf"))
    own = [(row, columns) for row, columns in headings if number["top"] < row[0]["top"] < following]
    if not own:
        raise _Unreadable(f"line {line} has no column headings")
    heading, columns = min(own, key=lambda item: item[0][0]["top"])
    if any(_VALUE.fullmatch(w["text"]) for w in heading):
        raise _Unreadable(f"line {line} prints a figure among its column headings")
    bottom = max(w["bottom"] for w in heading)
    stop = next((row[0]["top"] for row in rows if row[0]["top"] > bottom and row[0]["x0"] < margin), float("inf"))
    band = [w for w in words if w["top"] >= bottom - 1 and w["bottom"] <= stop + 1]
    if any(not columns[0][0] <= _mid(w, "x0", "x1") < columns[-1][1] for w in band):
        raise _Unreadable(f"line {line} prints a figure outside its columns")
    cells = {key: _cell([w for w in band if left <= _mid(w, "x0", "x1") < right])
             for key, (left, right) in zip("abcd", columns)}
    return heading + band, cells, stop


def _schedule_p_lines(words: list, side: int) -> dict:
    """{line: {"code", "a", "b", "c", "d"}} for each carryover line on Side 3
    or 4. Down to the last line, every figure is a line number, a credit code,
    a line a description cites ("line 3") or in a box of its line's; any other
    is a question, never ignored."""
    page = _schedule_p_headings(words)
    numbers = _margin_numbers(words, _SP_SIDES[side], page[2])
    if set(numbers) != _SP_SIDES[side]:
        raise _Unreadable(f"a line number on Side {side} is missing")
    found, placed, end = {}, set[int](), 0.0
    for line, number in sorted(numbers.items(), key=lambda item: item[1]["top"]):
        # The number's row holds the code, entered or printed, and the name.
        label = sorted((w for w in words if _overlaps(w, number)), key=lambda w: w["x0"])
        code = re.match(r"Code:?\s*(\d{3})\b", " ".join(w["text"] for w in label if w["x0"] > number["x0"]))
        # A description cites other lines by number: "line 3".
        cited = {id(after[0]) for w in label if w["text"] == "line" and (after := _right_of(label, w))}
        stray = [w for w in label if w is not number and id(w) not in cited and _VALUE.fullmatch(w["text"])]
        if stray and not (code and len(stray) == 1 and stray[0]["text"] == code[1]):
            raise _Unreadable(f"line {line} prints a figure beside its name")
        accounted, cells, stop = _schedule_p_cells(words, page, number, line)
        if any(cells[key] is not None for key in "abcd" if key not in _SP_BOXES.get(line, "abcd")):
            raise _Unreadable(f"line {line} prints a figure in a column it has no box in")
        placed.update(id(w) for w in label + accounted)
        end = max(end, stop)
        if line in _SP_SECTIONS and any(value is not None for value in cells.values()):
            if code is None or _SP_PRINTED.get(line, code[1]) != code[1]:
                raise _Unreadable(f"line {line} has figures without its credit code")
            found[line] = {"code": code[1], **cells}
    if any(id(w) not in placed and _VALUE.fullmatch(w["text"]) and w["top"] < end for w in words):
        raise _Unreadable("a figure sits in no line's columns")
    return found


def _check_side_2(words: list) -> None:
    """Side 2's line 4 carries code 162 forward only above the business
    credit limit, and is not read: a carryover there is a question."""
    page = _schedule_p_headings(words)
    line_4 = [row[0] for row in page[0] if row[0]["text"] == "4" and row[0]["x1"] <= page[2]
              and re.match(r"Code:?\s*162\b", " ".join(w["text"] for w in row[1:]))]
    if len(line_4) != 1:
        raise _Unreadable("line 4 is not where Side 2 prints it")
    if _schedule_p_cells(words, page, line_4[0], "4")[1]["d"]:
        raise _Unreadable("line 4 carries code 162 forward on Side 2, which is not read")


def _ca_credits(schedule_pages: list, words: dict, source_digest: str) -> list:
    """Each credit code's carryforward, kept apart from every other code.
    `schedule_pages` are (page, side); every carryover line read is on Side 3
    or 4, and Side 2, where printed, must carry nothing from line 4."""
    sides = sorted(side for _, side in schedule_pages if side in (3, 4))
    if sides != [3, 4]:
        raise _Unreadable("Schedule P needs Sides 3 and 4, once each")
    lines: dict[str, dict] = {}
    for page, side in schedule_pages:
        if side not in (2, 3, 4):
            continue
        if page not in words:
            raise _Unreadable("Schedule P needs its word positions")
        if side == 2:
            _check_side_2(words[page])
            continue
        for line, record in _schedule_p_lines(words[page], side).items():
            lines[line] = {**record, "page": page + 1}
    by_code: dict[str, list] = {}
    for line in sorted(lines, key=int):
        record = lines[line]
        amount, used, carried = record["a"], record["b"] or 0.0, record["d"] or 0.0
        # Column (d) is (a) less (b) and any credit elected refundable on FTB 3870.
        if amount is None or min(amount, used, carried) < 0 or used > amount or carried > round(amount - used, 2):
            raise _Unreadable(f"line {line} fails (d) at most (a) less (b)")
        by_code.setdefault(record["code"], []).append({"line": line, "section": _SP_SECTIONS[line], "page": record["page"],
                                                        "amount": amount, "used": used, "carryover": carried})
    out = []
    for code, rows in by_code.items():
        if len({row["section"] for row in rows}) != len(rows):
            raise _Unreadable(f"credit code {code} is entered twice in one section")
        if any(prior["carryover"] != row["amount"] for prior, row in zip(rows, rows[1:])):
            raise _Unreadable(f"credit code {code}'s carryover does not reach its next section")
        if code in _SP_SOLAR and rows[-1]["section"] != "C" and rows[-1]["carryover"]:
            raise _Unreadable(f"credit code {code}'s Section B2 carryover has no Section C line")
        last = rows[-1]
        out.append({"code": code, "carryforward": last["carryover"],
                    "not_carried": round(sum(row["amount"] - row["used"] - row["carryover"] for row in rows), 2),
                    "lines": rows, "provenance": [{"sha256": source_digest, "page": row["page"], "form": "P540",
                                                   "line": row["line"], "column": "d"} for row in rows]})
    return out


# Form 1040 page 1 filing-status boxes, each just left of its label.
_STATUS_LABELS = (("Single", ("Single",)), ("MFJ", ("Married", "filing", "jointly")),
                  ("MFS", ("Married", "filing", "separately")), ("HOH", ("Head", "of", "household")),
                  ("QSS", ("Qualifying", "surviving", "spouse")))


def _marked_status(words: list) -> str | None:
    heading = _phrases(words, ("Filing", "Status"))
    if len(heading) != 1:
        return None
    marked = set()
    for status, phrase in _STATUS_LABELS:
        # Only the labels beside the heading are its boxes.
        labels = [w for w in _phrases(words, phrase) if -3 <= w["top"] - heading[0]["top"] <= 30]
        if len(labels) > 1:
            raise _Unreadable("a filing status label is printed twice")
        for label in labels:
            box = [w for w in _left_of(words, label) if label["x0"] - 20 <= _mid(w, "x0", "x1") and _is_mark(w)]
            if box:
                marked.add(status)
    if len(marked) > 1:
        raise _Unreadable("more than one filing status box is marked")
    return marked.pop() if marked else None


def _qbi_schedule_c(lines: dict) -> tuple[float, list] | None:
    """Schedule C (Form 8995-A) line 6. Line 4 is blank without income, and
    line 5 then nets nothing; a blank counts as zero only where lines 5 and 6
    still follow from what was read."""
    if "3" not in lines or "6" not in lines:
        return None
    loss, income = lines["3"]["value"], lines.get("4", {}).get("value", 0.0)
    netted = lines.get("5", {}).get("value", 0.0)
    if abs(netted + min(-loss, income)) > 0.01 or abs(lines["6"]["value"] - min(loss - netted, 0)) > 0.01:
        return None
    return -lines["6"]["value"], [lines[n] for n in ("3", "4", "5", "6") if n in lines]



def _read_tables(schedule_b: list, schedule_p: list, first_pages: list, words: dict, year: int,
                 source_digest: str) -> tuple[dict, dict | None, dict]:
    """The carryover tables and the marked filing status, with a question for
    each one found but not read."""
    tables: dict[str, list] = {}
    problems: dict[str, str] = {}
    marked = None
    try:
        if schedule_b:
            tables["ftc_baskets"] = _ftc_baskets(sorted(schedule_b), words, year, source_digest)
    except _Unreadable as problem:
        problems["ftc_schedule_b"] = f"Schedule B (Form 1116) not read: {problem}; enter each category and year manually"
    try:
        if schedule_p and (credits := _ca_credits(sorted(schedule_p), words, source_digest)):
            tables["ca_credits"] = credits
    except _Unreadable as problem:
        problems["ca_credit_carryovers"] = f"Schedule P (540) not read: {problem}; enter each credit code manually"
    try:
        found = {page: status for page in first_pages if page in words and (status := _marked_status(words[page]))}
        if len(set(found.values())) > 1:
            raise _Unreadable("Form 1040 pages mark different filing statuses")
        if found:
            page, status = min(found.items())
            marked = {"value": status, "provenance": {
                "sha256": source_digest, "page": page + 1, "form": "1040", "line": "filing_status"}}
    except _Unreadable as problem:
        problems["filing_status_mark"] = f"Form 1040's filing status boxes not read: {problem}"
    return tables, marked, problems


def extract_seed(pages: dict[int, str], year: int, source_digest: str, words: dict | None = None) -> dict:
    """Pure masked-text extraction; the caller never persists extracted text.
    `words` are the pages' word positions, which the carryover tables and the
    filing-status boxes need; without them those stay questions."""
    if type(year) is not int or year not in REVIEWED_YEARS:
        raise ImportError("Prior-return form mappings have not been reviewed for this year")
    if not re.fullmatch(r"[a-f0-9]{64}", source_digest):
        raise ImportError("Source needs a SHA-256 digest")
    forms: dict[str, dict] = {}
    ira = []
    ira_owner_ssn: dict[int, str] = {}
    schedule_b: list[tuple[int, bool]] = []
    schedule_p: list[tuple[int, int]] = []
    unreviewed: set[str] = set()
    first_pages: list[int] = []
    for page, raw in pages.items():
        if type(page) is not int or page < 0 or not isinstance(raw, str):
            raise ImportError("Invalid extracted page")
        text = mask_ssn(raw)
        # No text in this input can select a command or path.
        if _out_of_scope(text):
            raise ImportError("Nonresident, part-year and amended returns are outside import scope")
        header = _form(text)
        if header is None:
            continue
        form, page_year = header
        if form in _REVISIONS:
            if page_year != _REVISIONS[form]:
                unreviewed.add(form)
                continue
            if form == "1116B":
                schedule_b.append((page, bool(re.search(r"(?m)\(Rev\. [^)]+\) Page 2$", text))))
                continue
        elif page_year != year:
            raise ImportError("Official form page belongs to a different tax year")
        if form == "P540":
            side = re.search(r"(?m)(?:Schedule P \(540\) 20\d{2} Side (\d)$|^Side (\d) Schedule P \(540\))", text)
            schedule_p.append((page, int(side[1] or side[2]) if side else 0))
        if form == "1040" and "Filing Status" in text:
            first_pages.append(page)
        lines = locate_lines(text, form, page + 1, source_digest)
        if form == "8606":
            if re.search(r"(?m)(?:^|\s)Form 8606 \(20\d{2}\) Page [2-9]$", text):
                continue  # Parts II and III: no line 14, and no second owner
            _checks(form, lines)
            owner = _form_8606_owner(text)
            if owner:
                ira_owner_ssn[page + 1] = owner
            # The two spouses can have identical line values. Page identity is
            # retained; the review must assign each worksheet to its owner.
            ira.append((page + 1, lines))
            continue
        merged = forms.setdefault(form, {})
        for line, record in lines.items():
            if line in merged and merged[line]["value"] != record["value"]:
                raise ImportError("Conflicting copies of an official form line")
            merged[line] = record
    if not forms.get("1040"):
        raise ImportError("No readable official Form 1040 lines; scanned PDFs need manual intake")
    # Schedule C's revision binds no year; its parent Form 8995-A does.
    orphan_schedule_c = "8995C" in forms and "8995-A" not in forms
    if orphan_schedule_c:
        del forms["8995C"]
    for form, lines in forms.items():
        _checks(form, lines)
    fields, unresolved, worksheets = {}, {}, {}

    def question(key, reason):
        unresolved[key] = reason

    def add(key, value, records):
        if not math.isfinite(value) or value < 0:
            raise ImportError("A closing carryforward/payment cannot be negative or non-finite")
        fields[key] = {"value": round(value, 2), "provenance": [r["provenance"] for r in records]}

    def line_field(key, form, line, total, parts):
        """`line` is one of `parts`, which add up to `total`. A blank part is
        a zero only where that sum still holds."""
        lines = forms.get(form, {})
        records = _with_blanks(lines, total, parts, lambda *v: sum(v), set(parts))
        if records is None:
            question(key, "Missing form line or its arithmetic dependencies; inspect the PDF/worksheet")
            return
        add(key, lines[line]["value"] if line in lines else 0.0, records)

    line_field("estimated_payments_applied", "1040", "36", "34", ("35a", "36"))
    if "540" in forms:
        line_field("ca_estimated_payments_applied", "540", "98", "97", ("98", "99"))
    else:
        question("california", "Confirm no California resident return, or enter its separate opening balances manually")
    d, f = forms.get("D", {}), forms["1040"]
    if all(n in d for n in ("7", "15", "16")) and d["16"]["value"] >= 0 and "21" not in d:
        # A net gain or zero skips line 21 and leaves nothing to carry; the
        # 1040 carries the gain itself on line 7a.
        if "7a" in f and f["7a"]["value"] != d["16"]["value"]:
            raise ImportError("Schedule D gain contradicts the filed Form 1040")
        records = [d[n] for n in ("7", "15", "16")]
        add("capital_loss_carryforward_short_term", 0.0, records)
        add("capital_loss_carryforward_long_term", 0.0, records)
    elif all(n in d for n in ("7", "15", "16", "21")) and all(n in f for n in ("7a", "11b", "14", "15")):
        net, reported = d["16"]["value"], d["21"]["value"]
        # Schedule D line 21 prints a loss; do not abs() a contradictory gain.
        if net < 0 and (reported > 0 or reported < net or f["7a"]["value"] != reported):
            raise ImportError("Schedule D loss deduction contradicts the filed Form 1040")
        if net >= 0 and reported != 0:
            raise ImportError("Schedule D reports a loss deduction beside a net gain")
        taxable_before_floor = f["11b"]["value"] - f["14"]["value"]
        used = min(-reported, max(taxable_before_floor - reported, 0)) if net < 0 else 0
        st, lt = d["7"]["value"], d["15"]["value"]
        short = max(-st - used - max(lt, 0), 0)
        long = max(-lt - max(st, 0) - max(used - max(-st, 0), 0), 0)
        records = [d[n] for n in ("7", "15", "16", "21")] + [f[n] for n in ("7a", "11b", "14", "15")]
        add("capital_loss_carryforward_short_term", short, records)
        add("capital_loss_carryforward_long_term", long, records)
        worksheets["capital_loss"] = {"taxable_income_before_zero_floor": round(taxable_before_floor, 2),
            "schedule_d_net": net, "schedule_d_deduction": reported,
            "deduction_used": round(used, 2), "short_term": round(short, 2), "long_term": round(long, 2),
            "source": SOURCES["capital_worksheet"]}
    else:
        question("capital_loss", "Schedule D/1040 worksheet inputs absent; confirm no carryover or resolve the split manually")
    for page, lines in ira:
        key = f"ira_basis_page_{page}"
        # Part I leaves lines 4-13 blank when nothing was distributed or
        # converted, and line 1 or 2 blank when it is zero.
        total = _with_blanks(lines, "3", ("1", "2"), lambda a, b: a + b, {"1", "2"})
        basis = _with_blanks(lines, "14", ("3", "13"), lambda a, b: a - b, {"13"})
        if total is not None and basis is not None:
            add(key, lines["14"]["value"], [lines[n] for n in ("1", "2", "3", "13", "14") if n in lines])
            # Kept so the review can tell a second copy of one worksheet from
            # two owners whose figures happen to match.
            worksheets.setdefault("ira_basis_pages", {})[key] = {n: lines[n]["value"] for n in sorted(lines)}
            owner = ira_owner_ssn.get(page)
            if owner:
                worksheets.setdefault("ira_basis_owners", {})[key] = owner
        else:
            question(key, "Individual Form 8606 line 14 or its cross-check is unreadable; resolve that owner's basis manually")
    if not ira:
        question("ira_basis", "No individual Form 8606 located; absence of this year's form does not establish zero historical basis")
    line_field("amt_credit_carryforward", "8801", "26", "21", ("25", "26"))
    # Form 8801's closing balance does not include a NEW current-year deferral
    # credit. It is never sufficient by itself for the aggregate rollover field.
    question("current_year_amt", "Reconcile current-year Form 6251 deferral AMT in addition to Form 8801's unused credit")
    lines = forms.get("8995", {})
    # Line 3 or 7 is blank when there was no prior-year loss to carry in.
    qbi = _with_blanks(lines, "16", ("2", "3"), lambda a, b: min(a + b, 0), {"3"})
    schedule_c = _qbi_schedule_c(forms.get("8995C", {}))
    if qbi is not None and schedule_c is not None and abs(abs(lines["16"]["value"]) - schedule_c[0]) > 0.01:
        raise ImportError("Form 8995 and Schedule C (Form 8995-A) disagree on the QBI loss carryforward")
    if qbi is not None:
        add("qbi_loss_carryforward", abs(lines["16"]["value"]), qbi)
    elif schedule_c is not None:
        add("qbi_loss_carryforward", *schedule_c)
    elif orphan_schedule_c:
        question("qbi_loss", "Schedule C (Form 8995-A) needs its covered-year Form 8995-A in the same PDF")
    else:
        question("qbi_loss", "Read Form 8995 lines 16/17 or Form 8995-A Schedule C; missing forms are not zero")
    reit = _with_blanks(lines, "17", ("6", "7"), lambda a, b: min(a + b, 0), {"7"})
    if reit is not None:
        add("reit_ptp_loss_carryforward", abs(lines["17"]["value"]), reit)
    else:
        question("reit_ptp_loss", "Resolve the separate REIT/PTP loss carryforward; never combine it with business QBI losses")
    if "540" in forms or "3510" in forms:
        line_field("ca_amt_credit_carryforward", "3510", "39", "32", ("38", "39"))
        question("ca_current_year_amt", "Reconcile current-year Schedule P deferral AMT and all separate credit carryovers")
    tables, marked, problems = _read_tables(schedule_b, schedule_p, first_pages, words or {}, year, source_digest)
    for key, reason in problems.items():
        question(key, reason)
    amt = next((c for c in tables.get("ca_credits", []) if c["code"] == "188"), None)
    if amt and "ca_amt_credit_carryforward" in fields and amt["carryforward"] != fields["ca_amt_credit_carryforward"]["value"]:
        raise ImportError("Schedule P and FTB 3510 disagree on the prior-year AMT credit carryover")
    for form in sorted(unreviewed):
        question(f"{form}_revision", f"Only {_REVISIONS[form]} of this schedule is reviewed; read it manually")
    for key, reason in {
        "foreign_tax_credit": "Read Form 1116 Schedule B by basket and vintage; no paid-minus-allowed reconstruction",
        "charitable_carryovers": "Obtain federal/CA charitable vintage worksheets, or confirm none",
        "passive_rental_carryovers": "Obtain passive/rental losses and depreciation worksheets, or confirm none",
        "other_carryovers": "Review NOLs, credit worksheets and other carryovers missing from official lines",
        "identity_dependents": "Confirm filing status, masked identity and dependents against page 1 and this year's setup",
        "return_scope": "Confirm an original full-year return; amended, part-year and nonresident imports are outside v1",
    }.items():
        question(key, reason)
    seed = {"schema": SCHEMA, "tax_year": year, "source_sha256": source_digest,
            "parse_boundary": BOUNDARY, "fields": fields, "unresolved": unresolved,
            "worksheets": worksheets, "sources": copy.deepcopy(SOURCES)}
    if tables:
        seed["table_evidence"] = tables
    if marked:
        seed["marked_filing_status"] = marked
    return seed


def _read(path: Path) -> dict:
    secure_path(path)  # inspect/restrict before opening, including junction ancestors
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or first_non_finite(data) is not None:
        raise ImportError("Import input must be a finite JSON object")
    return data


def _directory(path: Path) -> None:
    if not path.exists():
        path.mkdir()
    secure_path(path)  # also refuses every symlink/junction ancestor


def _archive(workspace: Path, year: int) -> Path:
    from agents.lib.workspace import resolve_under_workspace
    path = workspace / "archive" / str(year)
    resolve_under_workspace(str(path), workspace)
    _directory(workspace / "archive")
    _directory(path)
    return path


def import_pdf(workspace: Path, source: str, current_year: int) -> Path:
    """Create a pending seed without overwriting an existing archive."""
    from agents.lib.workspace import resolve_under_workspace
    if type(current_year) is not int or current_year - 1 not in REVIEWED_YEARS:
        raise ImportError("Only the immediately preceding reviewed return year can be imported")
    current = _read(workspace / "result.json")
    profile_now = _read(workspace / "taxpayer_profile.json")
    if (current.get("meta", {}).get("tax_year") != current_year
            or profile_now.get("tax_year") != current_year):
        raise ImportError("Initialize the matching current-year workspace before import")
    raw = Path(source)
    if not raw.is_absolute():
        raw = workspace / raw
    path = _validate_file_path(str(raw), {".pdf"}, workspace=str(workspace))
    allowed = resolve_under_workspace("documents/prior_return", workspace)
    if path.parent != allowed:
        raise ImportError("Prior return PDF must be directly inside documents/prior_return")
    year = current_year - 1
    archive = workspace / "archive" / str(year)
    if archive.exists():
        raise ImportError("Archive already exists; import never replaces an archived return")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    pages = extract_pdf_text(str(path), workspace=str(workspace))
    words = extract_pdf_words(str(path), workspace=str(workspace))
    seed = extract_seed(pages, year, before, words)
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise ImportError("Source PDF changed during import")
    result = {"meta": {"tax_year": year, "source": "imported_prior_return", "imported_review": "pending"},
              "prior_return_import": seed}
    profile = {"tax_year": year}
    archive = _archive(workspace, year)
    with atomic_paths(archive / "result.json", archive / "taxpayer_profile.json") as temps:
        for temp, value in zip(temps, (result, profile)):
            temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    return archive


def review_report(result: dict) -> str:
    """Local workpaper only; never printed to terminal or sent to a service."""
    seed = result["prior_return_import"]
    lines = [f"> {BOUNDARY}", "", f"# Prior-return import TY{seed['tax_year']}", "",
             f"Seed SHA-256: {digest({k: v for k, v in seed.items() if k != 'review'})}", "",
             "Every figure needs confirmation against the PDF. Missing amounts remain unresolved.", "",
             "| Field | Amount | Official source |", "|---|---:|---|"]
    for key, field in seed["fields"].items():
        refs = "; ".join(f"page {p['page']}, {p['form']} line {p['line']}" for p in field["provenance"])
        lines.append(f"| {key} | {field['value']:.2f} | {refs} |")
    pages = seed["worksheets"].get("ira_basis_pages", {})
    printed = seed["worksheets"].get("ira_basis_owners", {})
    keys = sorted(pages)
    for key in keys:
        lines += ["", f"{key} prints owner {printed[key]}; confirmation holds the assignment to it." if key in printed
                  else f"{key}: no owner SSN was read, so nothing checks its assignment; confirm the owner from "
                       "the name on that page."]
    for index, key in enumerate(keys):
        for other in keys[index + 1:]:
            if pages[key] == pages[other]:
                lines += ["", f"{key} and {other} print identical Form 8606 figures. If one is a second copy "
                          f"of the same worksheet, map it to `{DUPLICATE}<the other id>`; two owners with equal "
                          "figures are each assigned their own owner."]
    lines += _table_report(seed)
    lines += ["", "## Derived worksheets", "", json.dumps(seed["worksheets"], indent=2), "",
              "## Unresolved questions", ""]
    lines += [f"- {key}: {reason}" for key, reason in seed["unresolved"].items()]
    return "\n".join(lines) + "\n"


def _table_report(seed: dict) -> list[str]:
    evidence, lines = seed.get("table_evidence", {}), []
    for basket in evidence.get("ftc_baskets", []):
        first, second = basket["provenance"]["pages"]
        lines += ["", f"## Schedule B (Form 1116), {basket['basket']} category, pages {first}-{second}", "",
                  "| Origin year | Carryforward | Confirmation id |", "|---|---:|---|"]
        lines += [f"| {year} | {value:.2f} | ftc_{basket['basket']}_{year} |"
                  for year, value in basket["carryforward_by_origin_year"].items()]
        lines += ["", "Lines 3-8 by column, as read:", "", json.dumps(basket["reconciliation"], indent=2)]
    if evidence.get("ca_credits"):
        lines += ["", "## Schedule P (540) credit carryovers", "",
                  "| Code | Lines | Carryforward | Not carried | Confirmation id |", "|---|---|---:|---:|---|"]
        lines += [f"| {c['code']} | {', '.join(row['line'] for row in c['lines'])} | {c['carryforward']:.2f} "
                  f"| {c['not_carried']:.2f} | ca_credit_{c['code']} |" for c in evidence["ca_credits"]]
        lines += ["", "Not carried is column (a) less (b) less (d): a credit elected refundable on FTB 3870, "
                  "or a figure to check against the PDF."]
    marked = seed.get("marked_filing_status")
    if marked:
        lines += ["", f"Form 1040 page {marked['provenance']['page']} marks filing status {marked['value']}; "
                  "the confirmed status must match it."]
    return lines


def table_confirmation_ids(seed: dict) -> list[str]:
    """One id per basket and origin year, zeros included, and per credit code."""
    evidence = seed.get("table_evidence", {})
    return sorted([f"ftc_{basket['basket']}_{year}" for basket in evidence.get("ftc_baskets", [])
                   for year in basket["carryforward_by_origin_year"]]
                  + [f"ca_credit_{credit['code']}" for credit in evidence.get("ca_credits", [])])


def _confirms_every_table(confirmed, seed: dict) -> bool:
    expected = table_confirmation_ids(seed)
    return isinstance(confirmed, list) and len(confirmed) == len(expected) and set(confirmed) == set(expected)


def validate_reviewed(result: dict, profile: dict) -> dict:
    """Gate used by BOTH load_prior and pure compute_diff callers."""
    if result.get("meta", {}).get("source") != "imported_prior_return":
        if "prior_return_import" in result:
            raise ImportError("Imported provenance requires the imported-return source declaration")
        return {}
    seed = result.get("prior_return_import")
    if not isinstance(seed, dict) or seed.get("schema") != SCHEMA:
        raise ImportError("Imported archive has no supported provenance record")
    review = seed.get("review", {})
    unsigned = {key: value for key, value in seed.items() if key != "review"}
    if (result["meta"].get("imported_review") != "confirmed"
            or review.get("seed_sha256") != digest(unsigned)
            or review.get("profile_sha256") != digest(profile)
            or result["meta"].get("imported_review_sha256") != digest(review)):
        raise ImportError("Imported prior return is pending review or changed since confirmation")
    if (seed.get("tax_year") != result["meta"].get("tax_year")
            or profile.get("tax_year") != seed.get("tax_year")
            or profile.get("filing_status") not in STATUSES):
        raise ImportError("Imported archive year/status is inconsistent")
    if (not isinstance(review.get("confirmed_fields"), list)
            or len(review["confirmed_fields"]) != len(seed["fields"])
            or set(review["confirmed_fields"]) != set(seed["fields"])):
        raise ImportError("Every imported figure requires individual confirmation")
    if set(review.get("questions", {})) != set(seed["unresolved"]):
        raise ImportError("Every unresolved import question requires a decision")
    if any(value not in {"not_applicable", "manual_required"} for value in review["questions"].values()):
        raise ImportError("Invalid unresolved import decision")
    # A review confirmed before the table readers existed has no tables.
    if not _confirms_every_table(review.get("confirmed_tables", []), seed):
        raise ImportError("Every extracted carryover table requires individual confirmation")
    return seed


def verify_source(workspace: Path, result: dict) -> None:
    """Find the reviewed PDF by its digest. The seed stores no path: a file
    name can hold a name or an SSN in any spelling, and none is needed."""
    from agents.lib.workspace import WorkspaceError, resolve_under_workspace
    seed = result["prior_return_import"]
    try:
        allowed = resolve_under_workspace("documents/prior_return", workspace)
        candidates = sorted(allowed.iterdir()) if allowed.is_dir() else []
    except (OSError, ValueError, WorkspaceError):
        candidates = []  # a linked or unlistable directory names a path; refuse plainly
    for candidate in candidates:
        if candidate.suffix.lower() != ".pdf":
            continue
        # Any other file's error would carry its name, which may hold an SSN.
        try:
            checked = _validate_file_path(str(candidate), {".pdf"}, workspace=str(workspace))
            found = (checked.is_relative_to(allowed)
                     and hashlib.sha256(checked.read_bytes()).hexdigest() == seed["source_sha256"])
        except (OSError, ValueError):
            continue  # a link, an oversized or unreadable file is never the reviewed source
        if found:
            return
    raise ImportError("Source PDF changed or left the import directory; re-import and review it")


def _check_ira_owners(seed: dict, owners, filing_status: str) -> None:
    """Each Form 8606 page names its owner, or is a second copy of a page that
    does. A copy must print every figure its original does: two spouses whose
    figures match are two owners, never one owner twice."""
    ira_keys = {key for key in seed["fields"] if key.startswith("ira_basis_page_")}
    pages = seed.get("worksheets", {}).get("ira_basis_pages", {})
    if not isinstance(owners, dict) or set(owners) != ira_keys or not all(isinstance(v, str) for v in owners.values()):
        raise ImportError("Assign each individual IRA worksheet to a distinct eligible owner")
    printed = seed.get("worksheets", {}).get("ira_basis_owners", {})
    named = {key: owner for key, owner in owners.items() if not owner.startswith(DUPLICATE)}
    for key, owner in owners.items():
        original = owner[len(DUPLICATE):] if owner.startswith(DUPLICATE) else None
        if original is not None and (original not in named or pages.get(key) != pages.get(original)
                                     or pages.get(key) is None
                                     or None not in (printed.get(key), printed.get(original))
                                     and printed[key] != printed[original]):
            raise ImportError("A duplicate Form 8606 copy must match a named page figure for figure")
    keys = sorted(named)
    if any(key in printed and printed.get(other) == printed[key] and pages.get(key) == pages.get(other)
           for index, key in enumerate(keys) for other in keys[index + 1:]):
        raise ImportError("Two identical Form 8606 pages print one owner's SSN; mark the second a duplicate_of the first")
    # Owners are THIS year's roles, where the basis goes: last year's single
    # filer may be this year's spouse. A non-joint return has one filer.
    if (any(v not in {"primary", "spouse"} for v in named.values())
            or len(set(named.values())) != len(named)
            or (len(named) > 1 and filing_status != "MFJ")):
        raise ImportError("Assign each individual IRA worksheet to a distinct eligible owner")


def _check_owner_identity(seed: dict, owners: dict, current: dict) -> None:
    """A page whose masked SSN differs from the assigned person's is someone
    else's worksheet: last year's spouse, or the other spouse's copy."""
    printed = seed.get("worksheets", {}).get("ira_basis_owners", {})
    for key, owner in owners.items():
        if key not in printed or owner not in {"primary", "spouse"}:
            continue  # a copy follows its original; an unprinted page is the reviewer's
        person = current.get(owner)
        last4 = person.get("ssn_last4") if isinstance(person, dict) else None
        # The profile may hold "1234" or "XXX-XX-1234"; compare the digits.
        # A printed owner is checked, never assumed: without the assigned
        # person's digits nothing stops the other spouse's basis landing here.
        digits = re.search(r"(\d{4})$", last4) if isinstance(last4, str) else None
        if not digits:
            raise ImportError("A Form 8606 page prints its owner's SSN; record the assigned person's "
                              "ssn_last4 in the profile to confirm it")
        if printed[key] != f"XXX-XX-{digits[1]}":
            raise ImportError("A Form 8606 page prints a different SSN than the person it is assigned to")


def confirm_import(workspace: Path, year: int, decisions: dict) -> None:
    """Confirm exact figures, ownership and unanswered questions; log only ids."""
    from agents.lib.workspace import resolve_under_workspace
    archive = resolve_under_workspace(f"archive/{year}", workspace)
    result, profile = _read(archive / "result.json"), _read(archive / "taxpayer_profile.json")
    seed = result.get("prior_return_import", {})
    if result.get("meta", {}).get("imported_review") != "pending" or seed.get("tax_year") != year:
        raise ImportError("Only a pending import for this year can be confirmed")
    if set(decisions) != {"seed_sha256", "confirmed_fields", "confirmed_tables", "questions", "filing_status",
                          "ira_owners"}:
        raise ImportError("Review must match the documented confirmation schema")
    if decisions["seed_sha256"] != digest(seed):
        raise ImportError("Review was prepared for a different import")
    if (not isinstance(decisions["confirmed_fields"], list)
            or len(decisions["confirmed_fields"]) != len(seed["fields"])
            or set(decisions["confirmed_fields"]) != set(seed["fields"])):
        raise ImportError("Confirm every imported figure exactly once")
    questions = decisions["questions"]
    if (not isinstance(questions, dict) or set(questions) != set(seed["unresolved"])
            or any(v not in {"not_applicable", "manual_required"} for v in questions.values())):
        raise ImportError("Answer every unresolved question; manual_required remains blocking")
    if decisions["filing_status"] not in STATUSES:
        raise ImportError("Confirm the prior return's canonical filing status")
    marked = seed.get("marked_filing_status")
    if marked and decisions["filing_status"] != marked["value"]:
        raise ImportError("The confirmed filing status contradicts the box marked on Form 1040")
    if not _confirms_every_table(decisions["confirmed_tables"], seed):
        raise ImportError("Confirm every basket, origin year and credit code exactly once")
    capital = seed.get("worksheets", {}).get("capital_loss")
    if capital:
        from agents.lib.rollover import _capital_loss_cap
        cap, _ = _capital_loss_cap(decisions["filing_status"])
        net, reported = capital.get("schedule_d_net"), capital.get("schedule_d_deduction")
        if net is None or reported is None:
            raise ImportError("Re-import capital-loss inputs for the filing-status cross-check")
        allowable = -min(-net, cap) if net < 0 else 0
        if reported != allowable:
            raise ImportError("Schedule D line 21 does not match its allowable loss for the confirmed filing status")
    _check_ira_owners(seed, decisions["ira_owners"], decisions["filing_status"])
    _check_owner_identity(seed, decisions["ira_owners"], _read(workspace / "taxpayer_profile.json"))
    verify_source(workspace, result)
    profile["filing_status"] = decisions["filing_status"]
    seed["review"] = {"seed_sha256": digest(seed), "profile_sha256": digest(profile),
                      "confirmed_fields": decisions["confirmed_fields"], "questions": questions,
                      "ira_owners": decisions["ira_owners"], "confirmed_tables": decisions["confirmed_tables"]}
    result["meta"]["imported_review"] = "confirmed"
    result["meta"]["imported_review_sha256"] = digest(seed["review"])
    answers = workspace / "answers.md"
    if answers.exists():
        secure_path(answers)
    old = answers.read_text(encoding="utf-8") if answers.exists() else ""
    audit = (f"\n## Prior-return import TY{year}\n"
             f"Seed SHA-256: {seed['review']['seed_sha256']}\n"
             f"Confirmed fields: {len(seed['fields'])}; questions: {len(questions)}\n"
             + "".join(f"- {key}: {decision}\n" for key, decision in sorted(questions.items())))
    validate_reviewed(result, profile)
    with atomic_paths(archive / "result.json", archive / "taxpayer_profile.json", answers) as temps:
        for temp, value in zip(temps[:2], (result, profile)):
            temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
        temps[2].write_text(old + audit, encoding="utf-8")


def imported_changes(prior, current) -> list:
    """Reviewed seed → explicit rollover fields; preserve zero and ownership."""
    from agents.lib.rollover import Change, _get_path
    seed = validate_reviewed(prior.result, prior.profile)
    if not seed:
        return []
    if current.result.get("meta", {}).get("tax_year") != prior.year + 1:
        raise ImportError("Imported balances apply only to the immediately following year")
    if current.profile.get("tax_year", prior.year + 1) != prior.year + 1:
        raise ImportError("Current profile/result years disagree")
    out, basis, manual = [], {}, [k for k, v in seed["review"]["questions"].items() if v == "manual_required"]
    # The tables open next year's worksheets as evidence, each category,
    # vintage and credit code apart. Nothing here computes a credit from them,
    # so a nonzero balance blocks until its worksheet is done by hand.
    evidence = seed.get("table_evidence", {})
    for key, rows, carried in (
            ("ftc_baskets", [{"basket": b["basket"], "carryforward_by_origin_year": b["carryforward_by_origin_year"],
                              "provenance": b["provenance"]} for b in evidence.get("ftc_baskets", [])],
             lambda row: any(row["carryforward_by_origin_year"].values())),
            ("ca_credits", [{"code": c["code"], "carryforward": c["carryforward"], "not_carried": c["not_carried"],
                             "provenance": c["provenance"]} for c in evidence.get("ca_credits", [])],
             # A column (d) below (a) less (b) is an FTB 3870 election or an
             # unread figure; either way a person settles it. Code 188 rolls
             # as FTB 3510 line 39 when that was read, and is not counted twice.
             lambda row: (row["carryforward"] or row["not_carried"])
             and not (row["code"] == "188" and "ca_amt_credit_carryforward" in seed["fields"]))):
        if not rows:
            continue
        path = "prior_year.imported_" + key
        out.append(Change("carryforwards", path, "taxpayer_profile.json", rows, _get_path(current.profile, path),
                          "ask", "Reviewed official carryover table; keep each category, vintage and credit code apart"))
        if any(carried(row) for row in rows):
            manual.append("foreign_tax_credit" if key == "ftc_baskets" else "ca_credit_carryovers")
    for key, field in seed["fields"].items():
        if key.startswith("ira_basis_page_"):
            owner = seed["review"]["ira_owners"][key]
            if not owner.startswith(DUPLICATE):
                basis[owner] = field["value"]
            continue
        if key == "reit_ptp_loss_carryforward":
            if field["value"]:
                manual.append("reit_ptp_loss_carryforward")  # engine has no separate opening input
            continue
        if key in {"amt_credit_carryforward", "ca_amt_credit_carryforward"}:
            # An unreviewed CURRENT-year deferral amount cannot be omitted from
            # an aggregate closing balance. Preserve the read line as evidence.
            question = "current_year_amt" if key == "amt_credit_carryforward" else "ca_current_year_amt"
            if seed["review"]["questions"].get(question) != "not_applicable":
                manual.append(key)
                continue
        path = "prior_year." + key
        existing = _get_path(current.profile, path)
        out.append(Change("carryforwards", path, "taxpayer_profile.json", field["value"], existing,
            "ask", "Imported official form line: confirm current ownership/allocation and any existing conflict"))
        if key == "ca_estimated_payments_applied":
            out.append(Change("carryforwards", "estimated_payments.ca_verified", "taxpayer_profile.json",
                              False, _get_path(current.profile, "estimated_payments.ca_verified"), "apply",
                              "Reconcile the imported CA opening payment before claiming it"))
    if basis:
        path = "prior_year.ira_basis_by_taxpayer"
        if "spouse" in basis and current.profile.get("filing_status") != "MFJ":
            manual.append("ira_owner_allocation")
        else:
            out.append(Change("carryforwards", path, "taxpayer_profile.json", basis,
                              _get_path(current.profile, path), "ask", "Confirm each imported IRA owner's identity"))
    if manual:
        path = "prior_year.import_unresolved"
        marker = {"source_year": prior.year, "questions": sorted(set(manual))}
        out.append(Change("carryforwards", path, "taxpayer_profile.json", marker,
                          _get_path(current.profile, path), "apply",
                          "BLOCKED: resolve these import worksheets manually before computation"))
    return out


def _main(argv=None) -> int:
    from agents.lib.workspace import (
        WorkspaceError, add_workspace_argument, hold_workspace, resolve_workspace, resolve_under_workspace,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    add_workspace_argument(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import-pdf")
    imp.add_argument("--source", required=True)
    imp.add_argument("--current-year", type=int, required=True)
    confirm = sub.add_parser("confirm")
    confirm.add_argument("--year", type=int, required=True)
    confirm.add_argument("--review", required=True)
    report = sub.add_parser("report")
    report.add_argument("--year", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace).resolve()
        hold_workspace(workspace, "prior-return-import")
        if args.command == "import-pdf":
            import_pdf(workspace, args.source, args.current_year)
            print("Created pending prior-return seed. Review each figure and unresolved question before rollover.")
        elif args.command == "confirm":
            review = resolve_under_workspace(args.review, workspace)
            confirm_import(workspace, args.year, _read(review))
            print("Prior-return review recorded. Manual worksheets remain blocking where marked.")
        else:
            from agents.lib.secure_fs import write_secure
            archive = resolve_under_workspace(f"archive/{args.year}", workspace)
            result = _read(archive / "result.json")
            verify_source(workspace, result)
            _directory(workspace / "summaries")
            write_secure(workspace / "summaries" / f"prior_return_{args.year}.md", review_report(result))
            print("Wrote the owner-only prior-return review workpaper in summaries/.")
        return 0
    except WorkspaceError:
        print("Prior-return import refused: workspace unavailable or busy.", file=sys.stderr)
        return 2
    except Exception:
        # Source paths and free text are sensitive. Parser/JSON exceptions can
        # quote entire source tokens, so no exception text is emitted here.
        print("Prior-return import refused: invalid, unreadable, inconsistent or unreviewed input. "
              "Check the local review contract; no extracted document text is logged.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_main())
