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
from agents.lib.parse_utils import extract_pdf_text, _validate_file_path  # noqa: E402
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
}
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


def _form(text: str) -> tuple[str, int] | None:
    """The page's form and year from its running header or footer; None for a
    page of any form this reader does not map."""
    headers = {(form, year) for form, year in _running_headers(text) if form in LINES}
    if not headers:
        return None
    if len({form for form, _ in headers}) != 1 or len({year for _, year in headers}) != 1:
        raise ImportError("Official page needs one form and one unambiguous covered tax year")
    form, year = headers.pop()
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
              "8995": set(LINES["8995"])}.get(form, set())
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


def extract_seed(pages: dict[int, str], year: int, source_digest: str) -> dict:
    """Pure masked-text extraction; the caller never persists extracted text."""
    if type(year) is not int or year not in REVIEWED_YEARS:
        raise ImportError("Prior-return form mappings have not been reviewed for this year")
    if not re.fullmatch(r"[a-f0-9]{64}", source_digest):
        raise ImportError("Source needs a SHA-256 digest")
    forms: dict[str, dict] = {}
    ira = []
    ira_owner_ssn: dict[int, str] = {}
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
        if page_year != year:
            raise ImportError("Official form page belongs to a different tax year")
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
    if qbi is not None:
        add("qbi_loss_carryforward", abs(lines["16"]["value"]), qbi)
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
    for key, reason in {
        "foreign_tax_credit": "Read Form 1116 Schedule B by basket and vintage; no paid-minus-allowed reconstruction",
        "charitable_carryovers": "Obtain federal/CA charitable vintage worksheets, or confirm none",
        "passive_rental_carryovers": "Obtain passive/rental losses and depreciation worksheets, or confirm none",
        "other_carryovers": "Review NOLs, credit worksheets and other carryovers missing from official lines",
        "identity_dependents": "Confirm filing status, masked identity and dependents against page 1 and this year's setup",
        "return_scope": "Confirm an original full-year return; amended, part-year and nonresident imports are outside v1",
    }.items():
        question(key, reason)
    return {"schema": SCHEMA, "tax_year": year, "source_sha256": source_digest,
            "parse_boundary": BOUNDARY, "fields": fields, "unresolved": unresolved,
            "worksheets": worksheets, "sources": copy.deepcopy(SOURCES)}


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
    if not path.is_relative_to(allowed):
        raise ImportError("Prior return PDF must be inside documents/prior_return")
    year = current_year - 1
    archive = workspace / "archive" / str(year)
    if archive.exists():
        raise ImportError("Archive already exists; import never replaces an archived return")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    pages = extract_pdf_text(str(path), workspace=str(workspace))
    seed = extract_seed(pages, year, before)
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
    lines += ["", "## Derived worksheets", "", json.dumps(seed["worksheets"], indent=2), "",
              "## Unresolved questions", ""]
    lines += [f"- {key}: {reason}" for key, reason in seed["unresolved"].items()]
    return "\n".join(lines) + "\n"


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
    return seed


def verify_source(workspace: Path, result: dict) -> None:
    """Find the reviewed PDF by its digest. The seed stores no path: a file
    name can hold a name or an SSN in any spelling, and none is needed."""
    from agents.lib.workspace import resolve_under_workspace
    seed = result["prior_return_import"]
    allowed = resolve_under_workspace("documents/prior_return", workspace)
    for candidate in sorted(allowed.iterdir()) if allowed.is_dir() else ():
        if candidate.suffix.lower() != ".pdf":
            continue
        try:
            checked = _validate_file_path(str(candidate), {".pdf"}, workspace=str(workspace))
        except (OSError, ValueError):
            continue  # a link or oversized file is never the reviewed source
        if (checked.is_relative_to(allowed)
                and hashlib.sha256(checked.read_bytes()).hexdigest() == seed["source_sha256"]):
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
        person = current.get(owner) if owner in {"primary", "spouse"} else None
        last4 = person.get("ssn_last4") if isinstance(person, dict) else None
        # The profile may hold "1234" or "XXX-XX-1234"; compare the digits.
        digits = re.search(r"(\d{4})$", last4) if isinstance(last4, str) else None
        if key in printed and digits and printed[key] != f"XXX-XX-{digits[1]}":
            raise ImportError("A Form 8606 page prints a different SSN than the person it is assigned to")


def confirm_import(workspace: Path, year: int, decisions: dict) -> None:
    """Confirm exact figures, ownership and unanswered questions; log only ids."""
    from agents.lib.workspace import resolve_under_workspace
    archive = resolve_under_workspace(f"archive/{year}", workspace)
    result, profile = _read(archive / "result.json"), _read(archive / "taxpayer_profile.json")
    seed = result.get("prior_return_import", {})
    if result.get("meta", {}).get("imported_review") != "pending" or seed.get("tax_year") != year:
        raise ImportError("Only a pending import for this year can be confirmed")
    if set(decisions) != {"seed_sha256", "confirmed_fields", "questions", "filing_status", "ira_owners"}:
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
                      "ira_owners": decisions["ira_owners"]}
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
