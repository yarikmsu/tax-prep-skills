#!/usr/bin/env python3
"""The tracked summary-PDF renderer (V9).

Until this module, `/tax-generate` INSTRUCTED the agent to write a
`generate_tax_pdf.py` into the workspace and then run it. That generator was a
per-workspace artifact: gitignored, blocked by name at the PII gate because it
carried the taxpayer's name in its own source, and — being source code written
fresh per return — the one file in the pipeline with no test, no review and no
way to tell a good one from a bad one except by reading it. AGENTS.md §8 says as
much: "check the generator by hand".

This renderer is the same job, written once. It takes the values from
`result.json` and NOTHING from anywhere else, so the taxpayer's name reaches the
page as data and never as source. What it renders is decided by the SHAPE of the
result, not by a per-section layout: the sections a return has vary by return,
and a renderer with thirty-four hand-written layouts is thirty-four places for
the next section to be forgotten.

WHAT IT REFUSES. A result whose gates did not pass on it — the V8 validation
receipt — unless `--draft`, which stamps NOT VALIDATED on page 1 beside the
limitations box. Generation and validation consume the same input generation or
the PDF is marked as what it is.

NOT AN E-FILE ARTIFACT: no MeF XML, no Form 8879. The filing package
(`filing_package.py`) says so on its own page 1 and so does this.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib import receipt  # noqa: E402
from agents.lib.rollover import RolloverError  # noqa: E402
from agents.lib.secure_fs import (  # noqa: E402
    atomic_path, first_non_finite, redact)
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_argument,
    resolve_under_workspace,
    resolve_workspace,
)


class PdfRenderError(Exception):
    """Raised when the summary PDF cannot be rendered."""


# The document's own identity. Never an assistant, never a vendor: every
# generated return inherits this metadata, and a return that names the tool that
# typed it is a return that names something no taxpayer authorised.
PRODUCER = "tax-prep-skills"

# Keys that are commentary, not figures: the UNDERSCORED ones. `_include_if`
# says when a section applies and `_note_*` explains the JSON to whoever is
# reading it, and neither belongs on a taxpayer's summary.
#
# `note` and `notes` are NOT in that class, and treating them as commentary was
# a silent degrade: `compute_form_2210` returns a zero penalty with a note
# saying a penalty applies but the rate is unfetched, so dropping the note left
# `$0.00` standing alone — a loud degrade turned quiet, which AGENTS.md §4 calls
# the direction that must never be silent. Some of these are template labels
# ("Schedule B Part I") and printing one is harmless; dropping a warning is not.
def _is_commentary(key: str) -> bool:
    return key.startswith("_")


# Keys a planning tool reads back and no reader of the summary should: Schedule
# A's recorded `inputs` (AA23) restate figures already on the page under other
# meanings, and print its tax year as $2,026.00.
_MACHINE_KEYS = frozenset({"inputs"})


# Sections whose absence is normal and whose presence is decided by the skill
# (`_include_if`). A section that is present but EMPTY — every figure null —
# prints nothing rather than a page of em dashes.
# `meta` is rendered as the header and the Scope & Limitations box, not as a
# table: printing it twice put "Tax year" and the whole manifest a second time,
# under a heading that means nothing to a taxpayer. Its `exchange_rates` are the
# exception — see `_exchange_rates`: they are the only figures in that block, and
# a foreign-currency return cannot be reconciled without them.
_SKIP_SECTIONS = {"_comment", "_optional_sections_below", "meta"}

# A number is an AMOUNT unless its key says otherwise. Years, codes, counts,
# ratios and rates are numbers a dollar sign makes nonsense of — "Tax year
# $2,026.00" was the first thing the renderer printed. The rule is keyed on the
# field NAME because the schema names its fields; a misclassification prints a
# figure without its dollar sign, which is legible, rather than inventing one.
_NOT_MONEY = frozenset((
    "year", "years", "code", "codes", "count", "counts", "ratio", "ratios",
    "rate", "rates", "pct", "percent", "age", "ages", "quarter", "quarters",
    "student", "students", "number", "num", "tin", "digit", "digits", "id",
    "ids", "disposal", "disposals", "month", "months", "day", "days", "type",
))

# An amount recorded in the payer's OWN currency. `form_1116_ftc` carries both
# `foreign_tax_paid_local_currency` and the converted `foreign_tax_paid_usd`;
# printing the first with a dollar sign put euros beside dollars under one
# symbol. It is still an AMOUNT — dropping the symbol must not drop the cents,
# and `:g` rounded 12345.67 to 12345.7.
_OTHER_CURRENCY = frozenset(("currency",))


# ...and keys whose LAST word is a unit word while the field is plainly an
# amount: `carryforward_to_next_year` and `form_8801_carryforward_to_next_year`
# are dollars, and the suffix rule printed them bare and rounded.
_AMOUNT_ANYWAY = ("carryforward", "basis", "balance", "amount", "deduction",
                  "credit", "tax", "income", "loss", "gain", "payment")


def _is_money(key: str) -> bool:
    low = key.lower()
    if low.endswith("_percentage") or low == "tax_year" or low.endswith("_tax_year"):
        return False
    if any(word in low for word in _AMOUNT_ANYWAY):
        return not any(word in low for word in ("currency",))
    last = low.rsplit("_", 1)[-1]
    return last not in _NOT_MONEY and last not in _OTHER_CURRENCY


def _is_amount(key: str) -> bool:
    """Money or another currency — either way, two decimal places."""
    low = key.lower()
    if low.endswith("_percentage") or low == "tax_year" or low.endswith("_tax_year"):
        return False
    if any(word in low for word in _AMOUNT_ANYWAY):
        return True
    return low.rsplit("_", 1)[-1] not in _NOT_MONEY


def _title(key: str) -> str:
    """`form_8949` -> "Form 8949"; `california_540` -> "California 540".

    The key IS the title, because the alternative is a second table of names to
    keep in step with the template — and the one that goes stale is the one
    nobody sees go stale, since a missing entry just prints the key.
    """
    words = []
    for word in key.split("_"):
        if word.isdigit() or (word[:1].isdigit() and word[-1:].isalpha()):
            words.append(word)
        elif word in ("hsa", "qbi", "ftc", "fatca", "fbar", "pfic", "ira",
                      "agi", "amt", "fx", "ca", "us",
                      # New York's forms and acronyms: `new_york_it201` reads
                      # "New York It201" through the generic capitalisation,
                      # and a form number is not a word.
                      "ny", "nyc", "nyagi", "mctmt", "ptet",
                      "it196", "it201", "it203", "it225", "it3601"):
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


# Field names that do NOT match the official line, per /tax-generate's own
# caveat list. A filer transcribing by our labels would put the figure on the
# wrong line of a real form, which is a worse outcome than an unlabelled row —
# so these say what they are instead of claiming a number they do not have.
# Keyed by SECTION as well as field: `line_15_taxable_distributions` exists in
# `form_8606` too, where an IRA distribution labelled "Form 8889 line 16" sends
# the reader to an unrelated HSA form.
_NOT_THE_OFFICIAL_LINE = {
    ("schedule_d", "line_1c_1099b_box_c"): "1099-B Box C rows (Schedule D line 3)",
    ("schedule_d", "line_11_1099b"):
        "1099-B (internal name — place per the Schedule D instructions)",
    ("schedule_d", "line_12_not_reported"):
        "not reported to the IRS (internal name — place per the Schedule D "
        "instructions)",
    ("form_8889_hsa", "line_14c_qualified_medical"):
        "qualified medical (Form 8889 line 15)",
    ("form_8889_hsa", "line_15_taxable_distributions"):
        "taxable distributions (Form 8889 line 16)",
    # Box A and Box D transactions go to Schedule D lines 1a/8a only when they
    # carry NO adjustment; an adjusted one routes through Form 8949 to 1b/8b.
    # The label is neutral rather than adjustment-aware because the section does
    # not record which transactions were adjusted — a label that states one
    # destination is wrong half the time, and this one is right either way.
    ("schedule_d", "line_1a_1099b_box_a"):
        "1099-B Box A (Schedule D line 1a, or 1b via Form 8949 if adjusted)",
    ("schedule_d", "line_8a_1099b_box_d"):
        "1099-B Box D (Schedule D line 8a, or 8b via Form 8949 if adjusted)",
}


def _label(key: str, section: str = "") -> str:
    """`line_9_total_income` -> "Line 9 — total income". The line numbers are
    encoded in the field names (filing_package reads them the same way), and a
    summary that drops them makes every figure unfindable on the real form."""
    override = _NOT_THE_OFFICIAL_LINE.get((section, key))
    if override:
        return override
    parts = key.split("_")
    if len(parts) >= 2 and parts[0] in ("line", "box") and parts[1][:1].isdigit():
        rest = " ".join(parts[2:])
        head = f"{parts[0].capitalize()} {parts[1]}"
        return f"{head} — {rest}" if rest else head
    return key.replace("_", " ").capitalize()


# reportlab BUNDLES Bitstream Vera, which is used here for a stable, embedded
# face rather than relying on the viewer's Helvetica. Its coverage is Latin and
# Greek: a Cyrillic or CJK name still prints as empty boxes, and the only fix
# for that is a font this repository would have to ship — a decision with a
# licence and a repository-size to it, ruled as Q6 in
# docs/roadmap/BACKLOG.md rather than taken here.
#
# What is NOT acceptable is losing a name in silence, so every character the
# face cannot draw is counted and declared in the page-1 box. AGENTS.md §4: a
# degrade is loud, and a name replaced by boxes is the quietest possible way to
# lose one.
_FONT = "Vera"


def _register_fonts() -> None:
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fonts = Path(reportlab.__file__).resolve().parent / "fonts"
    for name, file in ((_FONT, "Vera.ttf"), (f"{_FONT}-Bold", "VeraBd.ttf"),
                       (f"{_FONT}-Italic", "VeraIt.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    pdfmetrics.registerFontFamily(_FONT, normal=_FONT, bold=f"{_FONT}-Bold",
                                  italic=f"{_FONT}-Italic")


def _unrenderable(text: str) -> set:
    """The characters this document's font has no glyph for."""
    from reportlab.pdfbase import pdfmetrics

    face = pdfmetrics.getFont(_FONT).face
    return {ch for ch in str(text)
            if ord(ch) > 127 and ord(ch) not in face.charToGlyph}


def _escape(text) -> str:
    """Text for reportlab's markup parser.

    Not only for table cells: the taxpayer HEADING goes through the same parser,
    and a name containing `<Jr>` was either swallowed as an unknown tag or — on
    a tag reportlab knows — raised a ValueError carrying the whole name into a
    traceback, which is identity data in terminal output.
    """
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _money(value) -> str:
    return f"${value:,.2f}" if value >= 0 else f"(${abs(value):,.2f})"


def _display(value, key: str = "") -> "str | None":
    """A value as it prints, or None when it is absent.

    NULL IS ABSENT, not a value. `result.json` ships null placeholders for every
    unpopulated field, and a renderer that treats them as present prints a page
    of em dashes on a fresh template — or crashes on the first `:,` format. The
    skill has warned about this since the generator was prose; here it is one
    function.
    """
    if value is None or value == "" or value == [] or value == {}:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        if _is_money(key):
            return _money(value)
        return f"{value:,.2f}" if _is_amount(key) else f"{value:g}"
    return str(value)


def _rows(node, prefix: str = "", section: str = "") -> "list[tuple[str, str]]":
    """(label, value) for every figure under `node`, nested keys indented.

    A list of objects becomes one indented block per entry, which is what a
    Schedule B item list or a PFIC fund list is; a list of scalars is joined.
    """
    out: "list[tuple[str, str]]" = []
    if isinstance(node, list):
        # A section can BE a list — `dependents` is one — and a walker that only
        # descended into dicts dropped the whole section without a word.
        return _rows({"": node}, prefix, section)[1:] if node else []
    if isinstance(node, dict):
        for key, value in node.items():
            if _is_commentary(key) or key in _MACHINE_KEYS:
                continue
            label = f"{prefix}{_label(key, section)}"
            if isinstance(value, dict):
                nested = _rows(value, prefix + "    ", section)
                if nested:
                    out.append((label, ""))
                    out.extend(nested)
            elif isinstance(value, list):
                entries = [v for v in value if v not in (None, "", {}, [])]
                if not entries:
                    continue
                out.append((label, ""))
                for i, entry in enumerate(entries, 1):
                    if isinstance(entry, dict):
                        nested = _rows(entry, prefix + "    ", section)
                        if nested:
                            out.append((f"{prefix}    #{i}", ""))
                            out.extend(_rows(entry, prefix + "        ", section))
                    else:
                        shown = _display(entry, key)
                        if shown is not None:
                            out.append((f"{prefix}    #{i}", shown))
            else:
                shown = _display(value, key)
                if shown is not None:
                    out.append((label, shown))
    return out


def _is_unfilled_optional(section, rows) -> bool:
    """An OPTIONAL section nobody filled in.

    `/tax-setup` removes the sections a taxpayer does not need, and one still
    carrying its `_include_if` marker is the template's own scaffolding: every
    figure zero, every flag No. Printing it puts a page of $0.00 between the
    reader and the figures that matter. A section WITHOUT the marker prints even
    when it is all zeros — `balance_due_summary` saying the refund is $0.00 is
    the answer, not the absence of one.
    """
    if not isinstance(section, dict) or "_include_if" not in section:
        return False
    return all(value in ("", "$0.00", "No") for _label, value in rows)


def _exchange_rates(result: dict) -> dict:
    """The rates a foreign-currency return was converted at.

    They live in `meta`, which is otherwise rendered as the header and the
    limitations box rather than as a table — and skipping the block whole took
    these with it. Nothing else in the PDF states them, so a reader could not
    reconcile a foreign amount with its USD figure at all.
    """
    meta = result.get("meta")
    rates = meta.get("exchange_rates") if isinstance(meta, dict) else None
    return {k: v for k, v in rates.items() if v not in (None, "")} \
        if isinstance(rates, dict) else {}


def _limitations(result: dict) -> "list[str]":
    meta = result.get("meta")
    limits = meta.get("limitations") if isinstance(meta, dict) else None
    return [str(x) for x in limits if x] if isinstance(limits, list) else []


def _taxpayer_line(result: dict) -> str:
    """The filer, from the result and nowhere else."""
    people = result.get("taxpayers")
    people = people if isinstance(people, dict) else {}
    names = []
    for who in ("primary", "spouse"):
        person = people.get(who)
        name = person.get("name") if isinstance(person, dict) else None
        if name:
            names.append(str(name))
    return " and ".join(names)


def build_story(result: dict, styles, *,
                draft_reasons: "list[str] | None" = None,
                notes: "list[str] | None" = None) -> list:
    """The flowables, in order. Separated from the write so a test can read the
    document's structure without a file, and so the null-safety rule above is
    checkable on `result_template.json` directly."""
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    def cell(text, style="BodyText"):
        """A wrapping, escaped cell.

        reportlab draws a plain string in a table cell on ONE line and lets it
        run past the frame: a real limitations sentence, or Florida's
        no-income-tax statement, walked off the right edge of the page and took
        the figure beside it with it. A `Paragraph` wraps inside the column, and
        `_escape` is what keeps an ampersand in an employer's name from being
        read as markup.
        """
        missing_glyphs.update(_unrenderable(text))
        return Paragraph(_escape(text), styles[style])

    _register_fonts()
    for name in ("Title", "Heading2", "Normal", "BodyText"):
        styles[name].fontName = _FONT
    meta = result.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    year = meta.get("tax_year")
    draft_reasons = list(draft_reasons or ())
    notes = list(notes or ())
    missing_glyphs: set = set()
    story = [Paragraph(_escape(f"Tax Return Summary — {year}") if year else
                       "Tax Return Summary", styles["Title"])]
    who = _taxpayer_line(result)
    if who:
        story.append(Paragraph(_escape(who), styles["Heading2"]))
    status = meta.get("filing_status")
    if status:
        story.append(Paragraph(_escape(f"Filing status: {status}"), styles["Normal"]))
    story.append(Spacer(1, 12))

    # The SECTIONS are built first so that anything they cannot render is known
    # before page 1 is written — the loud line about a missing alphabet belongs
    # in the same box as the rest of what this return does not cover, and that
    # box is above them.
    body = []
    sections = []
    for key, section in result.items():
        if key == "form_8606" and isinstance(section, dict) and section.get("schema") == "individual-8606/v1":
            people = section.get("by_taxpayer")
            if not isinstance(people, dict) or not people:
                raise PdfRenderError("Individual Form 8606 worksheets are missing")
            for owner, worksheet in people.items():
                if owner not in {"primary", "spouse"} or not isinstance(worksheet, dict):
                    raise PdfRenderError("Invalid individual Form 8606 owner/worksheet")
                sections.append((key, {k: v for k, v in worksheet.items() if k != "inputs"},
                                 f"Form 8606 — {owner.title()}"))
            sections.append((key, {"gross_to_1040": section.get("gross_to_1040"),
                                   "taxable_to_1040": section.get("taxable_to_1040")},
                             "IRA totals to Form 1040"))
        else:
            sections.append((key, section, _title(key)))
    for key, section, heading in sections:
        if key in _SKIP_SECTIONS or _is_commentary(key):
            continue
        rows = _rows(section, section=key) if isinstance(section, (dict, list)) else []
        if not rows or _is_unfilled_optional(section, rows):
            continue
        # `splitInRow`: a table splits BETWEEN rows by default, so one narrative
        # longer than a page — a worksheet note pasted into `notes` — made a row
        # taller than the frame and took the WHOLE document down with a
        # LayoutError. Splitting inside the row costs nothing when no row is
        # that tall.
        table = Table([[cell(label), cell(value)] for label, value in rows],
                      colWidths=[4.2 * inch, 2.3 * inch], splitInRow=1)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ]))
        # NOT KeepTogether: a section whose table has to split across pages
        # cannot also be kept whole, and reportlab answers that contradiction
        # with a LayoutError rather than a choice.
        body.append(Paragraph(_escape(heading), styles["Heading2"]))
        body.append(table)
        body.append(Spacer(1, 10))

    # PAGE ONE, before any dollar figure: what this return does not cover. The
    # draft banner sits in the same box, because a reader who reads one box
    # reads this one.
    boxed = []
    if draft_reasons:
        boxed.append(["NOT VALIDATED — DRAFT ONLY"])
        boxed.extend([[f"- {reason}"] for reason in draft_reasons])
    # A gate that RAN and had no verdict to give — Step 12.6 on a return whose
    # Form 1040 lines 1-38 it does not govern — belongs in this box and not in
    # the draft banner: the return is validated, and one thing about it was
    # checked by nothing. Appended to the manifest for the same reason the
    # glyph note below is: this box is what page 1 says the return does not
    # cover, and a second box is a second place to not look.
    limits = _limitations(result) + list(notes)
    if missing_glyphs:
        # The characters themselves are NOT listed: they are the ones this font
        # cannot draw, so printing them would print more boxes. The count and
        # the remedy are what a reader can act on.
        limits = limits + [
            f"{len(missing_glyphs)} character(s) in this return are outside the "
            "built-in font and print as empty boxes — a name in an alphabet it "
            "does not carry, most often. The figures are unaffected; read those "
            "fields from result.json and check them before filing."]
    if limits:
        boxed.append(["Scope & Limitations"])
        boxed.extend([[f"- {line}"] for line in limits])
    if boxed:
        table = Table([[cell(line[0])] for line in boxed], colWidths=[6.5 * inch],
                      splitInRow=1)
        table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, colors.black),
            ("INNERGRID", (0, 0), (-1, -1), 0, colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(table)
        story.append(Spacer(1, 12))

    story.append(Paragraph(
        "NOT an e-file artifact: no MeF submission and no Form 8879. This is a "
        "summary of the computed return.", styles["Normal"]))
    story.append(Spacer(1, 12))

    rates = _exchange_rates(result)
    if rates:
        story.append(Paragraph("Exchange rates", styles["Heading2"]))
        table = Table([[cell(_escape(code)), cell(_display(rate, "rate"))]
                       for code, rate in sorted(rates.items())],
                      colWidths=[4.2 * inch, 2.3 * inch], splitInRow=1)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ]))
        story.append(table)
        story.append(Spacer(1, 10))
    story.extend(body)
    return story


def render(result: dict, out_path: Path, *,
           draft_reasons: "list[str] | None" = None,
           notes: "list[str] | None" = None) -> Path:
    """Write the summary PDF, owner-only. Returns the path.

    Refuses a result carrying a non-finite number. `json.loads` admits the NaN
    and Infinity literals, both pass `isinstance(x, (int, float))`, and this
    module would format them — `($nan)`, `$inf` — into a document that looks
    like every other one, receipt and all, because the receipt checks freshness
    and not arithmetic. AGENTS.md §8's rule for every parse boundary applies to
    the boundary where a figure is PRINTED too.
    """
    from agents.lib.tax_computations.state.registry import selected_ny_return
    try:
        selected_ny_return(result)
    except ValueError as exc:
        raise PdfRenderError(str(exc)) from None
    bad = first_non_finite(result)
    if bad is not None:
        raise PdfRenderError(
            f"{bad} is not a finite number, so this return cannot be rendered: "
            "a summary carrying $nan or $inf reads as a computed figure. Fix "
            "the value in result.json and re-run /tax-compute.")
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate
    except ImportError as e:   # pragma: no cover - the lock pins reportlab
        raise PdfRenderError(
            f"reportlab is not installed: {redact(e)} — install the pinned tree "
            "with sh agents/lib/run.sh -m pip install -r requirements.txt") from e
    styles = getSampleStyleSheet()
    story = build_story(result, styles, draft_reasons=list(draft_reasons or ()),
                        notes=list(notes or ()))
    meta = result.get("meta")
    year = meta.get("tax_year") if isinstance(meta, dict) else None
    # Built into a SECURED temp file beside the target and renamed
    # (`secure_fs.atomic_path`, V10). reportlab creates the file and then writes
    # it, so a failure part way through — an exhausted quota is the ordinary one
    # — used to leave a partial PDF of return data at whatever the umask
    # allowed, with the securing step at the end never reached.
    try:
        with atomic_path(out_path) as tmp:
            SimpleDocTemplate(
                str(tmp), pagesize=letter,
                title=f"Tax Return Summary {year}" if year else "Tax Return Summary",
                # The metadata every generated return inherits. Never an
                # assistant and never a vendor — see PRODUCER.
                author=PRODUCER, creator=PRODUCER,
                subject="Computed tax return summary",
            ).build(story)
    except Exception as e:
        if isinstance(e, PdfRenderError):
            raise
        raise PdfRenderError(f"could not write {redact(out_path)}: {redact(e)}") from e
    return out_path


def _main(argv: list) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="agents.lib.pdf_render",
        description="Render the summary PDF from a validated result.json.")
    parser.add_argument("--result", required=True)
    add_workspace_argument(parser)
    parser.add_argument("--out", default=None,
                        help="output path (default Tax_Return_<year>_Summary.pdf "
                             "in the workspace)")
    parser.add_argument("--draft", action="store_true",
                        help="render anyway when the validation receipt is "
                             "stale, stamped NOT VALIDATED on page 1")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace).resolve()
        result_path = resolve_under_workspace(args.result, workspace)
        raw = result_path.read_bytes()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise PdfRenderError(f"{redact(result_path)} is not a result object")
        # The same check /tax-generate's filing package makes, for the same
        # reason: a PDF rendered from a return no gate validated is a document
        # that looks exactly like one that was.
        checked = receipt.verify(workspace)
        reasons = list(checked.reasons)
        packaged = receipt.covers_result(workspace, result_path, contents=raw)
        if packaged:
            reasons.append(packaged)
        if reasons and not args.draft:
            print("REFUSED: this PDF would not describe a validated return.",
                  file=sys.stderr)
            for reason in reasons:
                print(f"  - {reason}", file=sys.stderr)
            print("\nRe-run /tax-compute (Step 0.7, 12.6 and 12.7 rewrite the "
                  "receipt), or pass --draft for a NOT VALIDATED copy.",
                  file=sys.stderr)
            return 2
        meta = result.get("meta")
        year = meta.get("tax_year") if isinstance(meta, dict) else None
        # ONE call: `resolve_under_workspace` resolves against the workspace
        # AND confines to it, so the follow-up `_validate_subpath` the old
        # `Path(args.out).resolve()` needed became a second check of the same
        # thing the moment the first line changed.
        out = resolve_under_workspace(
            args.out or f"Tax_Return_{year}_Summary.pdf", workspace)
        render(result, out, draft_reasons=reasons if args.draft else None,
               notes=checked.notes)
        print(f"wrote {redact(out)}")
        return 0
    except (WorkspaceError, PdfRenderError, RolloverError, receipt.ReceiptError, OSError,
            json.JSONDecodeError) as e:
        print(f"ERROR: {redact(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
