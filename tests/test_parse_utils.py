"""Tests for agents/lib/parse_utils.py — document classification and the
reparse-point guards on the two paths that read taxpayer documents."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib.parse_utils import (  # noqa: E402
    _validate_file_path,
    classify_document,
    scan_workspace,
)


def _uncommented(path):
    """Source with `#` comments stripped — the prose in this codebase names
    is_symlink() constantly to explain why it is not used, so a raw substring
    scan for a CALL would match the explanation instead."""
    return "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())


def _fail_lstat(exc):
    """An os.lstat that raises — the case Path.is_symlink() silently converts
    to False on Python 3.14, which is one of the two interpreters CI runs."""
    def _lstat(path):
        raise exc
    return _lstat


def test_validate_file_path_fails_closed_when_the_path_cannot_be_inspected(
        tmp_path, monkeypatch):
    """A PermissionError from lstat must PROPAGATE, not read as "not a link".

    Path.is_symlink() turns every OSError into False, so the one case most
    likely to mean something IS wrong would hand back a safety certificate for
    a path nobody inspected — and _validate_file_path then resolves it and
    parses whatever it points at.
    """
    doc = tmp_path / "w2.pdf"
    doc.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr("agents.lib.secure_fs.os.lstat",
                        _fail_lstat(PermissionError(13, "denied")))
    with pytest.raises(OSError):
        _validate_file_path(str(doc), {".pdf"}, workspace=str(tmp_path))


def test_validate_file_path_still_refuses_a_symlink_out_of_the_workspace(tmp_path):
    """The pre-existing containment refusal survives the is_reparse_point()
    migration — the guard was widened, not replaced."""
    outside = tmp_path / "outside"
    outside.mkdir()
    real = outside / "secret.pdf"
    real.write_bytes(b"%PDF-1.4\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    link = workspace / "w2.pdf"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="points outside workspace"):
        _validate_file_path(str(link), {".pdf"}, workspace=str(workspace))


def test_document_guards_use_the_junction_aware_helper():
    """Both guards must resolve to secure_fs.is_reparse_point, not is_symlink().

    This is asserted structurally because it cannot be asserted behaviourally
    from POSIX CI: an NTFS junction cannot be created here, and simulating one
    end-to-end needs os.name == "nt", which makes pathlib construct WindowsPath
    for the whole process. is_symlink() passes every other test in this file,
    so a revert to it would be invisible — on a Windows clone it would silently
    stop refusing a junctioned document, and on 3.14 it would turn an
    inspection failure into "not a link".

    `from ... import` binds the name INDEPENDENTLY per module, so the identity
    is checked here rather than trusting a patch of secure_fs to reach it.
    """
    import agents.lib.parse_utils as pu
    import agents.lib.secure_fs as sfs

    assert pu.is_reparse_point is sfs.is_reparse_point
    assert ".is_symlink()" not in _uncommented(Path(pu.__file__)), (
        "parse_utils still calls Path.is_symlink() somewhere — it is blind to a "
        "junction and fails OPEN on an lstat error")


def test_scan_workspace_fails_closed_when_an_entry_cannot_be_inspected(
        tmp_path, monkeypatch):
    """Skipping an uninspectable entry would silently DROP a document, which
    understates income and so overstates the refund the user acts on — the
    direction AGENTS.md §4 makes a hard error. It raises instead."""
    (tmp_path / "w2.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr("agents.lib.secure_fs.os.lstat",
                        _fail_lstat(PermissionError(13, "denied")))
    with pytest.raises(OSError):
        scan_workspace(str(tmp_path))


def test_scan_workspace_still_skips_a_real_symlink(tmp_path):
    real = tmp_path / "real.pdf"
    real.write_bytes(b"%PDF-1.4\n")
    (tmp_path / "linked.pdf").symlink_to(real)
    names = {d["filename"] for d in scan_workspace(str(tmp_path))}
    assert "linked.pdf" not in names
    assert "real.pdf" in names


def test_classify_form_3921():
    """D9 review follow-up: Form 3921 (ISO exercise) must classify so the
    bargain element enters the parse ledger instead of staying a user
    assertion invisible to the tie-out."""
    assert classify_document(
        "Form 3921 — Exercise of an Incentive Stock Option Under "
        "Section 422(b)") == "3921"
    assert classify_document(
        "EXERCISE OF AN INCENTIVE STOCK OPTION\nCopy B for Employee") == "3921"


def test_classify_3921_beats_tax_return_fallthrough():
    # Some 3921 layouts mention Form 1040 in the filing instructions — the
    # 3921 pattern must win over the tax_return classification.
    text = ("FORM 3921 Exercise of an Incentive Stock Option — report any "
            "adjustment on Form 1040 (Form 6251)")
    assert classify_document(text) == "3921"


def test_classify_form_3922():
    """J2: Form 3922 (ESPP §423 purchase info) must classify so the
    purchase-date boxes enter the ledger for the disposition split and future
    basis math."""
    assert classify_document(
        "Form 3922 — Transfer of Stock Acquired Through an Employee Stock "
        "Purchase Plan Under Section 423(c)") == "3922"
    assert classify_document(
        "EMPLOYEE STOCK PURCHASE PLAN UNDER SECTION 423\nCopy B") == "3922"


def test_classify_3922_beats_1099b_cost_basis_shadow():
    # A broker-issued 3922 packet mentions "COST BASIS" (the 1099-b keyword) in
    # its cover text — the 3922 pattern must win, exactly as 3921 does.
    text = ("FORM 3922 Transfer of Stock Acquired Through an Employee Stock "
            "Purchase Plan — see your COST BASIS records")
    assert classify_document(text) == "3922"


def test_classify_existing_types_unchanged():
    assert classify_document("FORM W-2 Wage and Tax Statement") == "w2"
    assert classify_document("Form 1099-INT Interest Income") == "1099-int"
    assert classify_document("") == "unknown"
    assert classify_document(None) == "unknown"


def test_classify_3921_beats_cost_basis_shadow():
    """Review round: broker-issued 3921 packets mention 'cost basis' in
    cover text — the generic 1099-b keyword must not shadow the specific
    3921 classification."""
    text = ("EXERCISE OF AN INCENTIVE STOCK OPTION Under Section 422(b). "
            "Your cost basis information is included for your records.")
    assert classify_document(text) == "3921"


# --- The XXE guard is a guard, not an announcement ---------------------------

def _workbook(path):
    """A real .xlsx, so a refusal that was really a parse failure would be
    visible as one."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active["A1"] = "Wages"
    wb.active["B1"] = 1234
    wb.save(path)
    return path


def test_excel_extraction_refuses_when_defusedxml_is_absent(tmp_path,
                                                            monkeypatch):
    """A warning on stderr is not a guard.

    This module caught the `defusedxml` ImportError, printed a line, and went
    on to open workbooks with the stdlib XML parser restored — on the one input
    class AGENTS.md §7 names defusedxml to defend, an untrusted `.xlsx` a
    taxpayer downloaded from a payer. The preflight gate reasons FROM this:
    it warns rather than blocks on a missing package because the absence
    "surfaces loudly at first use", which was true of the other three and false
    of this one alone.
    """
    from agents.lib import parse_utils

    book = _workbook(tmp_path / "statement.xlsx")
    monkeypatch.setattr(parse_utils, "_XXE_GUARD_ACTIVE", False)
    with pytest.raises(RuntimeError, match="defusedxml"):
        parse_utils.extract_excel(str(book))


def test_the_same_workbook_parses_when_the_guard_is_active(tmp_path):
    """The counterweight. A refusal that fires on every workbook is not a
    guard either — it is the cry-wolf shape the preflight module's `blocking`
    comment argues against, and it would be invisible from the test above."""
    from agents.lib import parse_utils

    book = _workbook(tmp_path / "statement.xlsx")
    assert parse_utils._XXE_GUARD_ACTIVE, (
        "defusedxml is pinned in requirements.txt and is not installed here, "
        "so this test is measuring the wrong branch")
    sheets = parse_utils.extract_excel(str(book))
    assert [row for rows in sheets.values() for row in rows] == [
        ["Wages", 1234]]


def test_a_real_import_failure_reaches_the_refusal(tmp_path):
    """End to end, in a child interpreter where the import genuinely fails.

    Monkeypatching the flag proves the refusal; it does not prove the flag is
    wired to the ImportError branch. A `None` entry in `sys.modules` makes
    `import defusedxml` raise for real, so this exercises the path a workspace
    without the package actually takes — and asserts the workbook is REFUSED
    rather than parsed, which is the whole of the defect.
    """
    import subprocess

    book = _workbook(tmp_path / "statement.xlsx")
    repo = str(Path(__file__).resolve().parent.parent)
    child = subprocess.run(
        [sys.executable, "-c",
         "import sys\n"
         "sys.modules['defusedxml'] = None\n"
         f"sys.path.insert(0, {repo!r})\n"
         "from agents.lib import parse_utils\n"
         "assert parse_utils._XXE_GUARD_ACTIVE is False\n"
         f"print(parse_utils.extract_excel({str(book)!r}))\n"],
        capture_output=True, text=True)
    assert child.returncode != 0, (
        "the workbook was parsed with no XXE guard: " + child.stdout)
    assert "RuntimeError" in child.stderr and "defusedxml" in child.stderr
    assert "Wages" not in child.stdout, "the refusal came after the parse"


# --- A document is not parsed by a stack the suite never ran ------------------
#
# The preflight gate WARNS that the installed versions are off `requirements.txt`
# and lets every skill proceed, on the argument that blocking would refuse
# /tax-calendar and every other skill that opens no document. That argument holds
# for the gate and not for a parse: this is where an untested parser turns into a
# wrong figure on a return, in a direction nobody can predict, and no downstream
# check can catch it — tax-review audits the aggregated result, and the tie-out
# compares the ledger to the result, so a misparsed box ties out perfectly.

DOCUMENT_ENTRY_POINTS = [
    ("extract_pdf_text", ".pdf"),
    ("extract_tables", ".pdf"),
    ("pdf_page_count", ".pdf"),
    ("extract_excel", ".xlsx"),
]


@pytest.fixture
def lock_state(monkeypatch):
    """Fake the lock comparison, with the parse boundary's cache cleared.

    `_off_lock_parsers` is memoised — it is consulted at every entry point and
    walks the installed distributions — so a fake left in the cache would leak
    into whatever test ran next, and one computed before the fake was installed
    would silently measure the real workspace instead. Cleared on both sides.
    """
    from agents.lib import parse_utils

    def _set(drift=(), lock_error=""):
        monkeypatch.setattr(parse_utils.preflight, "_drifted_dependencies",
                            lambda: (drift, lock_error))
        parse_utils._off_lock_parsers.cache_clear()

    parse_utils._off_lock_parsers.cache_clear()
    yield _set
    parse_utils._off_lock_parsers.cache_clear()


@pytest.mark.parametrize("entry, suffix", DOCUMENT_ENTRY_POINTS)
def test_every_document_entry_point_refuses_an_off_lock_parser(
        tmp_path, lock_state, entry, suffix):
    """One drifted transitive package, four doors into a document.

    The path does not exist, which is the ordering assertion: the refusal comes
    before the file is validated and before pdfplumber or openpyxl is even
    imported, so a document that is going to be refused is never opened.
    """
    from agents.lib import parse_utils

    lock_state(drift=(("pdfminer-six", "20200101", "20260107"),))
    with pytest.raises(RuntimeError, match="pdfminer-six"):
        getattr(parse_utils, entry)(str(tmp_path / f"w2{suffix}"))


def test_the_scan_refuses_rather_than_classifying_with_it(tmp_path, lock_state):
    """The fifth door, and the one that swallows a refusal if it is put one
    line lower.

    `scan documents/` is the FIRST command /tax-parse runs and it opens every
    PDF to classify it — inside a `try` that turns any exception into a
    `doc_type: "error"` entry and a stderr line. A refusal routed through that
    handler degrades into a warning and the scan returns a document list built
    by the parser it was refusing, which is the shape the defusedxml guard in
    this same file was rewritten to stop being.
    """
    from agents.lib import parse_utils

    (tmp_path / "w2.pdf").write_bytes(b"%PDF-1.4\n")
    lock_state(drift=(("pdfplumber", "0.0.1", "0.11.10"),))
    with pytest.raises(RuntimeError, match="pdfplumber"):
        parse_utils.scan_workspace(str(tmp_path))


def test_the_scan_still_lists_a_workspace_holding_no_pdf(tmp_path, lock_state):
    """Per-PDF, not at the top of the scan. /tax-parse-crypto works on exchange
    CSVs, which open no locked parser at all."""
    from agents.lib import parse_utils

    (tmp_path / "coinbase.csv").write_text("Timestamp,Asset\n")
    lock_state(drift=(("pdfplumber", "0.0.1", "0.11.10"),))
    assert [doc["doc_type"] for doc in parse_utils.scan_workspace(
        str(tmp_path))] == ["csv"]


def test_a_locked_parser_that_is_absent_is_named_as_absent(tmp_path,
                                                           lock_state):
    """The transitive tail nothing imports until it needs it — `cryptography`
    on an encrypted PDF — is gone, not merely old. "cryptography None" would be
    the sentinel printed at the reader instead of the finding."""
    from agents.lib import parse_utils

    lock_state(drift=(("cryptography", None, "50.0.0"),))
    with pytest.raises(RuntimeError, match="cryptography is NOT INSTALLED"):
        parse_utils.pdf_page_count(str(tmp_path / "w2.pdf"))


def test_a_lock_that_cannot_be_read_refuses_too(tmp_path, lock_state):
    """Unverifiable is not verified-clean. `requirements.txt` is tracked and
    sits two levels above `parse_utils`, so a workspace that cannot read it is
    one where `agents/lib/` was moved away from its clone — AGENTS.md §4's
    "a skill without its engine must not compute", arrived at from the other
    side."""
    from agents.lib import parse_utils

    lock_state(lock_error="requirements.txt could not be read")
    with pytest.raises(RuntimeError, match="could not be compared"):
        parse_utils.pdf_page_count(str(tmp_path / "w2.pdf"))


def test_the_refusal_is_wired_to_the_real_lock_comparison(tmp_path,
                                                          monkeypatch):
    """The tests above fake the comparison; this one fakes only the LOCK.

    Monkeypatching `_drifted_dependencies` proves the refusal reads whatever it
    returns — not that it is connected to the file the workspace is actually
    measured against. Here the drift is computed from the distributions really
    installed, against a lock pinning a `pdfminer.six` that no venv holds.
    """
    from agents.lib import parse_utils

    (tmp_path / "requirements.txt").write_text(
        "pdfminer-six==0.0.1 \\\n    --hash=sha256:" + "a" * 64 + "\n")
    monkeypatch.setattr(parse_utils.preflight, "_ENGINE_ROOT", tmp_path)
    parse_utils._off_lock_parsers.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="pdfminer-six"):
            parse_utils.pdf_page_count(str(tmp_path / "w2.pdf"))
    finally:
        parse_utils._off_lock_parsers.cache_clear()


def test_a_package_no_parse_reaches_does_not_stop_one(tmp_path, lock_state):
    """The cry-wolf counterweight, and the reason the tree is split rather than
    "the whole lock". `reportlab` writes the summary PDF in /tax-generate,
    downstream of every figure this module produces — refusing to read a 1099
    because the PDF generator was upgraded is how an agent learns that these
    refusals are noise."""
    from agents.lib import parse_utils

    book = _workbook(tmp_path / "statement.xlsx")
    lock_state(drift=(("reportlab", "1.0", "5.0.0"),))
    assert parse_utils.extract_excel(str(book)) == {"Sheet": [["Wages", 1234]]}


def test_csv_extraction_is_not_gated(tmp_path, lock_state):
    """`extract_csv` reads with the stdlib `csv` module and no locked package
    at all, so a drifted parsing tree says nothing about it. /tax-parse-crypto
    runs on exchange CSVs and would be refused for a library it never loads."""
    from agents.lib import parse_utils

    doc = tmp_path / "coinbase.csv"
    doc.write_text("Timestamp,Asset\n2026-01-01,BTC\n")
    lock_state(drift=(("pdfminer-six", "20200101", "20260107"),))
    assert parse_utils.extract_csv(str(doc)) == [["Timestamp", "Asset"],
                                                 ["2026-01-01", "BTC"]]


def test_a_workspace_on_the_lock_refuses_nothing():
    """Against the REAL environment, with nothing faked.

    Every other test here proves the refusal fires; this one proves it is not
    firing on a correct workspace — and a contributor whose venv has drifted
    off `requirements.txt` should see it here, once, rather than as an
    unexplained refusal in the middle of a parse.
    """
    from agents.lib import parse_utils

    parse_utils._off_lock_parsers.cache_clear()
    try:
        assert parse_utils._off_lock_parsers() == "", (
            "this workspace is not the locked tree — reinstall it with "
            "`sh agents/lib/run.sh -m pip install -r requirements.txt`")
    finally:
        parse_utils._off_lock_parsers.cache_clear()


def test_every_locked_distribution_is_classified():
    """The two sets are a PARTITION of the lock, checked here rather than
    trusted.

    A pin in neither set is one the refusal silently ignores, and that is what
    an ordinary dependency bump produces: `uv pip compile` pulls in a new
    transitive package, nobody edits `parse_utils`, and the check quietly stops
    covering it. Failing on the commit that changes the lock puts the decision
    in front of the person already reading the lock diff.
    """
    from agents.lib import parse_utils, preflight

    lock = (Path(__file__).resolve().parent.parent
            / "requirements.txt").read_text()
    pinned = {preflight._canonical_dist(match.group(1))
              for match in preflight._LOCK_PIN.finditer(lock)}
    assert pinned, "the lock parsed to no pins at all"
    assert not parse_utils._PARSING_TREE & parse_utils._GENERATION_TREE
    assert pinned == parse_utils._PARSING_TREE | parse_utils._GENERATION_TREE, (
        "requirements.txt and the parse-boundary classification have diverged: "
        "put each new pin in _PARSING_TREE if a document parse runs on it, in "
        "_GENERATION_TREE if nothing under `documents/` ever reaches it")
