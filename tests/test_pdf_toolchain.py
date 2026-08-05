"""Tests for the PDF toolchain pins in requirements.txt — reportlab + pdfplumber.

Why this file exists: NOTHING else in the suite imports either library, so a
dependency bump used to reach `main` on a green CI that had proved only that
pip could resolve it. `generate_tax_pdf.py` — reportlab's sole consumer — is a
per-workspace runtime artifact and is deliberately not committed (AGENTS.md §6),
so CI structurally cannot exercise it; pdfplumber is imported lazily inside
`parse_utils`, and no test reached those branches either. Reviewing the
reportlab 4.5.1 -> 5.0.0 bump (PR #24) turned that gap up: the entire release
delta is one INVERTED DEFAULT — `trustedHosts=None` flipped from "trust every
host" to "trust none" — a change no signature diff, type checker, or linter can
see, arriving under a major version this project's own tests could not defend
against.

These tests mirror the generator's API surface rather than importing it, so
they run on a fresh clone. `test_surface_still_mirrors_the_generator` is what
keeps the mirror honest when the generator IS present.
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib.parse_utils import (  # noqa: E402
    extract_pdf_text,
    extract_tables,
    pdf_page_count,
)

GENERATOR = Path(__file__).resolve().parent.parent / "generate_tax_pdf.py"
BUILDER = "_build_reference_pdf"


def _reportlab_imports(tree):
    """Every reportlab symbol or module a syntax tree binds.

    Handles BOTH statement forms deliberately: `from reportlab.x import Name`
    binds "Name", while `import reportlab.x.y` binds the dotted module path.
    An earlier version walked only `ast.ImportFrom` and was blind to the
    second — which is how reportlab's graphics/renderPDF modules are
    conventionally imported, and the emptiness check below could not catch it
    because the other lines kept the set non-empty.
    """
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("reportlab"):
                found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names
                         if a.name.startswith("reportlab"))
    return found


def _names_used(tree):
    """Identifiers actually referenced in a syntax tree.

    Importing a symbol is not exercising it. Without this, the cheapest way
    to silence the mirror guard would be a dead import in the builder —
    quieter than the list it replaced, and just as untested.
    """
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def _build_reference_pdf(path):
    """Build a PDF the way generate_tax_pdf.py builds one.

    Mirrors `main()`: a BaseDocTemplate with a single Frame, a PageTemplate
    carrying an onPage footer callback, and a story using the same flowables
    and the same TableStyle command vocabulary as `_line_table`.

    This function's reportlab imports ARE the covered surface — they are read
    back out by `test_surface_still_mirrors_the_generator`, so adding one here
    without using it will not satisfy that guard.

    Returns:
        (path, page_count) — the written file and how many pages reportlab
        reported rendering.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer,
        Table, TableStyle,
    )

    base = getSampleStyleSheet()["Normal"]
    cell = ParagraphStyle("cell", parent=base, fontSize=8.5, leading=10)
    page_w, page_h = letter
    margin = 0.6 * inch

    def on_page(canvas, doc):
        # The generator's footer: every canvas call _on_page makes.
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawString(margin, 0.45 * inch, "Synthetic Taxpayer - TY2025")
        canvas.drawRightString(page_w - margin, 0.45 * inch,
                               f"Page {doc.page}")
        canvas.restoreState()

    rows = [[Paragraph("Line item", cell), Paragraph("$1,234.56", cell)],
            [Paragraph("Total", cell), Paragraph("$2,345.67", cell)]]
    table = Table(rows, colWidths=[4.0 * inch, 1.5 * inch], repeatRows=0,
                  style=TableStyle([
                      ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                      ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                      ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                      ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                      ("TOPPADDING", (0, 0), (-1, -1), 3),
                      ("LEFTPADDING", (0, 0), (-1, -1), 6),
                      ("RIGHTPADDING", (1, 0), (1, -1), 6),
                      ("GRID", (0, 0), (-1, -1), 0.25,
                       colors.HexColor("#CCCCCC")),
                      ("ROWBACKGROUNDS", (0, 0), (-1, -1),
                       [colors.white, colors.HexColor("#F5F5F5")]),
                  ]))

    story = [Paragraph("Federal Tax Return Summary", base),
             Spacer(1, 0.2 * inch), table, PageBreak(),
             Paragraph("Continuation page", base)]

    frame = Frame(margin, margin, page_w - 2 * margin, page_h - 2 * margin,
                  leftPadding=0, bottomPadding=0.3 * inch, rightPadding=0,
                  topPadding=0)
    doc = BaseDocTemplate(str(path), pagesize=letter, leftMargin=margin,
                          rightMargin=margin, topMargin=margin,
                          bottomMargin=0.6 * inch, title="Smoke",
                          subject="Smoke")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=on_page)])
    doc.build(story)
    # doc.page is reportlab's own count of what it rendered, so the build test
    # can check pagination on reportlab's terms instead of borrowing
    # pdfplumber from the round-trip test.
    return path, doc.page


def test_reportlab_builds_the_generator_surface(tmp_path):
    """A reportlab bump must still render every construct /tax-generate uses.

    Not a rendering-fidelity check — it is the missing liveness signal. Before
    this test, `pip install -r requirements.txt` succeeding WAS the whole
    reportlab test.

    Verified by mutation to catch a removed or renamed reportlab symbol (the
    actual breaking-upgrade mode) and a PageBreak that stops paginating.
    CONTENT loss is deliberately not this test's job — a story that renders
    empty still produces a well-formed two-page PDF, and mutation confirmed
    only the round-trip test below catches it.

    Known blind spot: reportlab silently IGNORES an unrecognized TableStyle
    command, so a renamed style command still passes — it changes the PDF's
    appearance without changing its text, which no assertion in this file
    covers. Visual regressions remain a manual /tax-generate check.
    """
    out, pages = _build_reference_pdf(tmp_path / "smoke.pdf")

    assert out.exists(), "reportlab produced no file"
    assert out.read_bytes().startswith(b"%PDF-"), "not a PDF"
    # Pagination, not file size. A byte-count floor looks like a content check
    # and is not one: the full document here is ~2.2 KB and a PDF holding a
    # single character is ~1.4 KB, so any threshold that admits the first
    # admits the second.
    assert pages == 2, (
        f"story spans a PageBreak but reportlab rendered {pages} page(s) — "
        "pagination broke, which would silently reflow a real return"
    )


def test_pdfplumber_reads_back_what_reportlab_wrote(tmp_path):
    """The two pins have to agree with each other, not just import.

    pdfplumber 0.11.10 shipped no pdfplumber code at all — it is purely a
    `pdfminer.six` bump, i.e. a swap of the text-extraction ENGINE that
    /tax-parse depends on. Reading back a PDF this repo just wrote exercises
    the three lazy `import pdfplumber` branches in parse_utils that nothing
    else in the suite reaches, and needs no committed binary fixture.
    """
    pdf, _ = _build_reference_pdf(tmp_path / "roundtrip.pdf")
    ws = str(tmp_path)

    assert pdf_page_count(str(pdf), workspace=ws) == 2

    text = extract_pdf_text(str(pdf), workspace=ws)
    assert set(text) == {0, 1}, "page indexing is 0-based and complete"
    assert "Federal Tax Return Summary" in text[0]
    assert "Continuation page" in text[1]
    # The onPage footer must survive extraction — it carries the taxpayer
    # name and page number on every page of a real return.
    assert "Synthetic Taxpayer" in text[0]

    # page -> list of tables -> rows -> cells
    tables = extract_tables(str(pdf), workspace=ws)
    flat = [cell
            for page in tables.values()
            for table in page
            for row in table
            for cell in row]
    assert any(cell and "1,234.56" in cell for cell in flat), (
        f"table cells did not survive extraction: {tables}"
    )
    assert any(cell and "Line item" in cell for cell in flat), (
        f"table labels did not survive extraction: {tables}"
    )


@pytest.mark.skipif(not GENERATOR.exists(),
                    reason="generate_tax_pdf.py is an untracked runtime "
                           "artifact; absent on a fresh clone and in CI")
def test_surface_still_mirrors_the_generator():
    """Guard the mirror: the generator may not outgrow the smoke test.

    Skipped in CI by construction, which is the point — it fires in the one
    place the generator is actually edited. Covers the IMPORTED surface only;
    canvas methods used inside `_on_page` are mirrored by hand in
    `_build_reference_pdf` and are not checked here.

    The covered set is DERIVED from what `_build_reference_pdf` actually
    imports, never from a hand-maintained list. A list would make appending a
    name the cheapest way to turn this test green — silencing the alarm while
    creating the exact untested surface it exists to prevent. Deriving it
    means the only way to satisfy the guard is to write the exercising code.
    """
    generator_uses = _reportlab_imports(ast.parse(GENERATOR.read_text()))
    assert generator_uses, (
        "parsed no reportlab imports from the generator — did it change shape?"
    )

    this_file = ast.parse(Path(__file__).read_text())
    builder = next((n for n in ast.walk(this_file)
                    if isinstance(n, ast.FunctionDef) and n.name == BUILDER),
                   None)
    assert builder is not None, f"{BUILDER} not found — was it renamed?"

    used = _names_used(builder)
    # A dotted `import reportlab.x.y` is referenced through its root binding.
    exercised = {name for name in _reportlab_imports(builder)
                 if name.split(".")[0] in used}

    uncovered = generator_uses - exercised
    assert not uncovered, (
        f"generate_tax_pdf.py imports {sorted(uncovered)}, which {BUILDER} "
        "does not. Import and exercise them there — appending to a list will "
        "not silence this, because the covered set is read back out of that "
        "function's own imports."
    )
