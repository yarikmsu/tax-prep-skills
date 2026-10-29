"""Tests for agents/lib/parse_utils.py — document classification and the
reparse-point guards on the two paths that read taxpayer documents."""
import csv
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib.parse_utils import (  # noqa: E402
    _validate_file_path,
    classify_document,
    scan_workspace,
)
from tests.skill_text import PARSING_SKILLS, skill_text  # noqa: E402


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

def _workbook(path, rows=(("Wages", 1234),), title=None):
    """A real .xlsx, so a refusal that was really a parse failure would be
    visible as one."""
    wb = openpyxl.Workbook()
    if title is not None:
        wb.active.title = title
    for row in rows:
        wb.active.append(list(row))
    wb.save(path)
    return path


def _run_cli(*args, workspace=None):
    """Invoke the CLI the way a parse worker does — from inside a workspace.

    Every CLI read is confined to a workspace, and the DEFAULT is the working
    directory. So the workspace is expressed by RUNNING there, which is what
    every skill invocation does and is therefore the path worth covering; an
    explicit `--workspace` is passed only by the tests that name an escape.
    Inferring it from the arguments instead left the default path — the one in
    production — exercised by nothing but a grep of the module source.
    """
    target = next((a for a in args[1:] if a and Path(a).exists()), None)
    cwd = workspace or (
        str(Path(target) if Path(target).is_dir() else Path(target).parent)
        if target else str(Path(__file__).resolve().parent.parent))
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent
                             / "agents" / "lib" / "parse_utils.py"), *args],
        capture_output=True, text=True, cwd=cwd)


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


# ---------------------------------------------------------------------------
# V0 — SSN/ITIN masking at the extraction boundary.
#
# `parse_utils.py extract` / `tables` print page text to stdout, and the parse
# worker is told to mask the SSN AFTER reading it — by which point the full
# number sits in the session transcript, the "intermediate file" AGENTS.md §1
# forbids. Every string an extraction route returns now passes through
# `secure_fs.mask_ssn`, which preserves `XXX-XX-{last4}` and nothing else.
#
# No full identifier is persisted as a fixture: every synthetic SSN below is
# assembled at runtime from area 000 (never issued) so no test line carries a
# nine-digit identifier the PII gate would have to allowlist.
# ---------------------------------------------------------------------------

from agents.lib.secure_fs import mask_ssn, mask_table, redact  # noqa: E402

LAST4 = "6789"
MASKED = f"XXX-XX-{LAST4}"
# Nine bare digits is also the shape the PII gate's token rule blocks, so the
# synthetic routing number is assembled at runtime for the same reason.
ROUTING = "".join(("021", "000", "021"))


_SECOND = "".join(("000", "00", "1111"))   # a second synthetic SSN


def _ssn(sep: str) -> str:
    """A synthetic SSN in one spelling, built at runtime (see the header)."""
    return sep.join(("000", "00", LAST4))


def _itin() -> str:
    """ITINs share the SSN shape: 9XX-(50-65,70-88,90-92,94-99)-XXXX."""
    return "-".join(("900", "70", LAST4))


def _with_separator_gaps(groups, sep, gaps):
    """Draw the four independently spaced edges of two separators."""
    return f"{groups[0]}{gaps[0]}{sep}{gaps[1]}{groups[1]}{gaps[2]}{sep}{gaps[3]}{groups[2]}"


# Everything on a W-2 or 1099 that is NOT a taxpayer identifier and must
# therefore survive extraction byte-for-byte. Routing numbers are exactly nine
# digits — the one shape that cannot be told from a bare SSN by digits alone,
# which is why the bare form is masked only under an identifier label. The
# cents-column rows are the whitespace spelling to the digit (three digits, a
# two-digit cents column, a four-digit figure), which is why that spelling is
# masked only under a label too.
SURVIVORS = (
    "Acme Widgets LLC",
    "1234 Market St, San Francisco, CA 94105-1234",
    "EIN 12-3456789",
    f"Routing {ROUTING}",
    f"Awaiting wire {ROUTING}",
    f"Crediting account {ROUTING}",
    f"Homeowners Assn. acct {ROUTING}",
    f"Itinerary {ROUTING}",
    f"PAYER'S TIN {ROUTING}",
    "Account 1234567890",
    "Box 1 100,000.00",
    "Box 2 12,345.67",
    " ".join(("Interest income 850", "00", "2026")),
    " ".join(("Wages 42,150", "00", "1234")),
    "Phone 415-555-0100",
    "Date 2026-01-31",
    "Prior XXX-XX-1111",
)


class TestMaskSsn:
    """The masker is SSN/ITIN-only: it is not `redact`, which substitutes
    `[REDACTED]` and masks any nine-plus digit run, erasing routing and account
    numbers and the last four digits alike."""

    @pytest.mark.parametrize("sep", ["-", ".", "/", " ", "\t", "  ", "\u00a0"])
    def test_every_spelling_masks_to_last4_under_a_label(self, sep):
        text = f"Employee's SSN {_ssn(sep)} Wages 50,000.00"
        out = mask_ssn(text)
        assert _ssn(sep) not in out
        assert out == f"Employee's SSN {MASKED} Wages 50,000.00"

    @pytest.mark.parametrize("sep", ["-", ".", "/"])
    def test_the_hyphen_dot_and_slash_spellings_mask_without_a_label(self, sep):
        assert mask_ssn(f"Ref {_ssn(sep)}") == f"Ref {MASKED}"

    @pytest.mark.parametrize("sep", ["-", ".", "/"])
    @pytest.mark.parametrize("gap", [" ", "\t", "\u00a0", "\u2009", " \t\u00a0"])
    @pytest.mark.parametrize("prefix", ["Reference", "Employee's SSN"])
    def test_horizontal_gaps_beside_ssn_separators(self, sep, gap, prefix):
        from itertools import product
        from agents.lib.secure_fs import names_an_identifier
        for gaps in product(("", gap), repeat=4):
            for groups in (("000", "00", LAST4), ("900", "70", LAST4)):
                value = _with_separator_gaps(groups, sep, gaps)
                text = f"{prefix}: {value} Wages 850.00"
                expected = f"{prefix}: {MASKED} Wages 850.00"
                assert mask_ssn(text) == expected
                assert mask_ssn(expected) == expected
                # A label carrying this spelling has its value already;
                # it must not claim the next row's routing number.
                assert mask_ssn(f"{text}\nRouting {ROUTING}") == f"{expected}\nRouting {ROUTING}"
                # A newly refused filename must also be safe in its error.
                assert names_an_identifier(f"form-{value}.pdf")
                assert redact(f"form-{value}.pdf") == "form-[REDACTED].pdf"

    @pytest.mark.parametrize("sep", ["-", ".", "/"])
    @pytest.mark.parametrize("gap", ["", " ", "\t", "\u00a0"])
    def test_separator_gaps_do_not_turn_dates_or_phones_into_ssns(self, sep, gap):
        from itertools import product
        for gaps in product(("", gap), repeat=4):
            for groups in (("01", "31", "2026"), ("415", "555", "0100")):
                value = _with_separator_gaps(groups, sep, gaps)
                for prefix in ("Reference", "SSN"):
                    text = f"{prefix}: {value}"
                    assert mask_ssn(text) == text

    @pytest.mark.parametrize("gap", [" ", "\t", "\u00a0"])
    def test_spaced_ssn_separators_must_agree_and_digits_stay_bounded(self, gap):
        from itertools import permutations
        for first, second in permutations("-./", 2):
            mixed = f"000{gap}{first}{gap}00{gap}{second}{gap}{LAST4}"
            assert mask_ssn(mixed) == mixed
        value = _with_separator_gaps(("000", "00", LAST4), "-", [gap] * 4)
        for text in ("1" + value, value + "1"):
            assert mask_ssn(text) == text

    @pytest.mark.parametrize("gap", ["\n", "\r", "\v", "\f", "\u0085", "\u2028", "\u2029"])
    def test_separator_spacing_does_not_join_vertical_whitespace(self, gap):
        for position in range(4):
            gaps = [""] * 4
            gaps[position] = gap
            value = _with_separator_gaps(("000", "00", LAST4), "-", gaps)
            assert mask_ssn(value) == value

    def test_an_itin_is_masked_the_same_way(self):
        assert mask_ssn(f"ITIN {_itin()}") == f"ITIN {MASKED}"

    @pytest.mark.parametrize("sep", ["", " ", "\t", "\u00a0"])
    def test_the_bare_and_whitespace_spellings_are_masked_only_under_a_label(
            self, sep):
        """Nine bare digits is a routing number as often as an SSN, and
        `850 00` followed by `2026` is a sub-$1,000 amount with its cents
        in their own column and then a year — the whitespace spelling to the digit. A
        comma lookbehind protected only amounts of $1,000 and up; below that
        line the masker erased the box amount the return is computed from."""
        value = _ssn(sep)
        assert mask_ssn(f"SSN: {value}") == f"SSN: {MASKED}"
        assert mask_ssn(f"Social Security Number {value}") == \
            f"Social Security Number {MASKED}"
        assert mask_ssn(f"social security no. {value}") == \
            f"social security no. {MASKED}"
        assert mask_ssn(f"Ref {value}") == f"Ref {value}"
        assert mask_ssn(f"Routing {ROUTING}") == f"Routing {ROUTING}"
        row = " ".join(("Interest income 850", "00", "2026", "Form 1099-INT"))
        assert mask_ssn(row) == row

    @pytest.mark.parametrize("label", [
        "RECIPIENT'S TIN", "Recipient\u2019s TIN", "RECIPIENTS TIN",
        "EMPLOYEE'S TIN", "PAYEE'S TIN", "BORROWER'S TIN", "STUDENT'S TIN",
        "Partner's TIN", "WINNER'S TIN", "DEBTOR'S TIN", "TRANSFEROR'S TIN",
        "PARTICIPANT'S TIN", "Beneficiary's Social Security Number",
        "Spouse's identification number", "Shareholder's identifying number",
    ])
    def test_the_labels_irs_forms_print_beside_the_taxpayers_number(self, label):
        """Every 1099 prints the recipient's number under "RECIPIENT'S TIN",
        not "SSN"; a W-2G says winner, a 1099-C debtor, a 1099-S transferor,
        a 5498 participant, a K-1 "identifying number". The possessor is NOT
        enumerated on this side: a form family the list had not met would be
        an SSN in the transcript."""
        assert mask_ssn(f"{label} {_ssn('')}") == f"{label} {MASKED}"

    @pytest.mark.parametrize("possessive", ["Recipient's", "Recipient\u2019s", "RECIPIENTS"])
    @pytest.mark.parametrize("sep", ["", " ", "-"])
    @pytest.mark.parametrize("itin", [False, True])
    @pytest.mark.parametrize("layout", ["{label} {value}", "{label}\n{value}"])
    def test_form_1042s_recipient_us_tin(self, possessive, sep, itin, layout):
        # TY2026 https://www.irs.gov/pub/irs-pdf/f1042s.pdf, box 13i.
        # Keep the form's words, including the qualifier after TIN.
        label = f"13i {possessive} U.S. TIN, if any"
        value = sep.join(_itin().split("-")) if itin else _ssn(sep)
        text = layout.format(label=label, value=value)
        assert mask_ssn(text) == layout.format(label=label, value=MASKED)

    @pytest.mark.parametrize("label", [
        "16b Payer's TIN", "16b Payer\u2019s TIN", "Payer's U.S. TIN",
        "12e Withholding agent's global intermediary identification number (GIIN)",
        "13l Recipient's GIIN", "13m Recipient's FTIN, if any", "U.S. TIN",
    ])
    def test_form_1042s_other_identifiers_are_not_ssn_labels(self, label):
        from agents.lib.secure_fs import identifier_spans
        assert identifier_spans(label)[0] == []
        for gap in (" ", "\n"):
            text = f"{label}{gap}{ROUTING}"
            assert mask_ssn(text) == text

    @pytest.mark.parametrize("label", [
        "PAYER'S TIN", "TIN", "Employer identification number",
        "FILER'S TIN", "ISSUER'S TIN", "LENDER'S TIN", "TRUSTEE'S TIN",
        "Creditor's TIN", "Account identification number",
    ])
    def test_a_payer_side_number_is_an_ein_and_survives(self, label):
        """The enumerated side is the payer's, where a miss masks an EIN the
        forms print hyphenated anyway. No possessive, no label."""
        assert mask_ssn(f"{label} {ROUTING}") == f"{label} {ROUTING}"

    @pytest.mark.parametrize("prose", [
        "Awaiting wire", "Crediting account", "Homeowners Assn. acct",
        "Itinerary", "Auditing ref",
    ])
    def test_a_label_inside_a_word_is_not_a_label(self, prose):
        """`ITIN` sits inside "Awaiting" and "Crediting", `SSN` inside "Assn.";
        under IGNORECASE an unbounded label masks the account number a bank
        statement prints right after them — the routing/account corruption
        the bare rule was narrowed to avoid."""
        assert mask_ssn(f"{prose} {ROUTING}") == f"{prose} {ROUTING}"

    def test_a_label_above_its_value_masks_the_value_line(self):
        """A W-2 box and every 1099 print the label on one line and the number
        on the next; pdfplumber keeps that layout. The W-2 also prints "OMB
        No. 1545-0008" to the right of the box label, so the rule is "no
        identifier-shaped run after the label", not "no digits"."""
        page = f"a Employee's social security number OMB No. 1545-0008\n{_ssn('')} 50,000.00"
        assert mask_ssn(page) == \
            f"a Employee's social security number OMB No. 1545-0008\n{MASKED} 50,000.00"
        boxed = f"PAYER'S TIN RECIPIENT'S TIN\n{ROUTING} {_ssn('')}\nBox 1 100.00"
        out = mask_ssn(boxed)
        assert _ssn("") not in out and out.endswith("\nBox 1 100.00")
        assert out.split("\n")[1] == f"XXX-XX-{ROUTING[-4:]} {MASKED}"

    def test_a_label_line_that_carries_its_value_leaves_the_next_line_alone(self):
        """A statement line "SSN: XXX-XX-1234", or the hyphenated SSN, is the
        same-line layout; the routing number under it is not the value."""
        for value in (_ssn(""), _ssn("-"), "XXX-XX-1234", "***-**-1234"):
            out = mask_ssn(f"SSN {value}\nRouting {ROUTING}")
            assert out.endswith(f"\nRouting {ROUTING}"), out
        out = mask_ssn(f"Employer identification number\n{ROUTING}")
        assert out == f"Employer identification number\n{ROUTING}"

    @pytest.mark.parametrize("box", [
        "3 Social security wages", "4 Social security tax withheld",
        "7 Social security tips",
    ])
    def test_a_social_security_box_label_is_not_an_identifier_label(self, box):
        """The three W-2 boxes named after the program hold AMOUNTS. While the
        noun after "social security" was optional they read as identifier
        labels, and box 3's amount printed with its cents in their own column
        — `850 00` then a year — was masked to XXX-XX-2026, erasing the wage
        the return is computed from."""
        row = " ".join(("850", "00", "2026"))
        assert mask_ssn(f"{box}\n{row}") == f"{box}\n{row}"
        assert mask_ssn(f"{box} {row}") == f"{box} {row}"

    def test_the_box_that_does_carry_the_number_still_says_number(self):
        """Requiring the noun costs nothing on the box that holds the SSN:
        every form that prints one prints "number" with it."""
        for label in ("Employee's social security number",
                      "Social Security No.", "social security #",
                      "SOCIAL SECURITY NUMBERS"):
            assert mask_ssn(f"{label} {_ssn('')}") == f"{label} {MASKED}"

    def test_a_comb_field_spelling_is_masked_under_its_label(self):
        """The official fillable forms print an SSN in a comb field, nine
        boxes of one digit each. Flattened, pdfplumber extracts the digits
        apart, a spelling no other pattern matched: Form 1040's name row put
        the whole number in the transcript. Its label row also carries the
        name labels; the row below holds the name and the nine digits."""
        comb = " ".join("00000" + LAST4)
        page = ("Your first name and middle initial Last name Your social security number\n"
                f"JANE Q DOE {comb}\nHome address")
        assert mask_ssn(page) == page.replace(comb, MASKED)

    def test_every_comb_run_after_a_same_line_label_is_masked(self):
        """Form 1040 prints up to four dependents' SSNs on the row that
        starts "(3) SSN", and their comb fields extract as ONE run of spaced
        digits. Masking only the first nine left the other dependents'."""
        other = " ".join("00000" + "1111")
        row = f"than four (3) SSN {' '.join('00000' + LAST4)} {other}"
        assert mask_ssn(row) == f"than four (3) SSN {MASKED} XXX-XX-1111"

    def test_a_run_that_is_not_whole_nines_keeps_no_digit(self):
        """A comb holding eight digits beside a full one, or a box number
        beside a comb, has no boundary to trust: splitting from either end
        printed eight digits of the other dependent's number."""
        comb, short = " ".join("00000" + LAST4), " ".join("0000" + "1111")
        assert mask_ssn(f"SSN 4 {comb}") == "SSN XXX-XX-XXXX"
        assert mask_ssn(f"than four (3) SSN {comb} {short}") == "than four (3) SSN XXX-XX-XXXX"
        assert mask_ssn(f"SSN {comb} 1") == "SSN XXX-XX-XXXX"

    def test_a_comb_digit_is_one_digit(self):
        """A run of single digits ends at a longer number, which no comb
        box prints."""
        assert mask_ssn("SSN 0 0 0 0 0 6 7 8 912") == "SSN 0 0 0 0 0 6 7 8 912"

    def test_a_same_line_label_governs_only_what_follows_it(self):
        routing, comb = " ".join(ROUTING), " ".join("00000" + LAST4)
        row = f"Routing {routing} Spouse's SSN {comb}"
        assert mask_ssn(row) == f"Routing {routing} Spouse's SSN {MASKED}"

    def test_a_comb_run_without_a_label_survives(self):
        """Form 1040 prints the refund routing number in a comb field too,
        and a row of line numbers is nine single digits apart."""
        for row in (f"b Routing number {' '.join(ROUTING)} c Type: Checking",
                    "1 2 3 4 5 6 7 8 9"):
            assert mask_ssn(row) == row

    def test_the_last_label_on_a_line_decides_whether_it_carries_its_value(self):
        """The taxpayer's number beside its label, the spouse's label last on
        the line and the spouse's number on the row below."""
        page = f"Your SSN {_ssn('-')} Spouse's SSN\nJOHN R DOE {_ssn('')}"
        assert mask_ssn(page) == f"Your SSN {MASKED} Spouse's SSN\nJOHN R DOE {MASKED}"

    @pytest.mark.parametrize("value", ["xxx-xx-6789", "XXX-XX-6789", "***-**-6789", "XXX-XX-XXXX"])
    def test_a_masked_value_in_any_case_is_the_labels_value(self, value):
        """A statement that prints its TIN pre-masked, in lower case, or this
        masker's own output for a run with no boundary: either way the line
        below is something else, here an amount."""
        row = " ".join(("1 Wages 850", "00", "2026"))
        assert mask_ssn(f"Employee's SSN {value}\n{row}") == f"Employee's SSN {value}\n{row}"

    def test_a_masked_comb_still_counts_as_the_labels_value(self):
        row = " ".join(("1a Proceeds 850", "00", "2026"))
        page = f"Spouse's SSN\nYour SSN {' '.join('00000' + LAST4)} 4\n{row}"
        assert mask_ssn(page) == f"Spouse's SSN\nYour SSN XXX-XX-XXXX\n{row}"

    def test_a_comb_run_starts_on_a_single_digit(self):
        """The 0 of a 10 is not a comb box: the run after it is eight digits."""
        assert mask_ssn("SSN 10 0 0 0 6 7 8 9 1") == "SSN 10 0 0 0 6 7 8 9 1"

    def test_your_spellings_are_labels_only_ending_their_row(self):
        """"Your identifying number" heads Form 1040-NR's box; in a sentence
        it sits over whatever the page prints next."""
        row = " ".join(("1a Proceeds 850", "00", "2026"))
        for prose in ("Enter your taxpayer identification number on each page",
                      "you must provide your identifying number."):
            assert mask_ssn(f"{prose}\n{row}") == f"{prose}\n{row}"
        assert mask_ssn(f"Your taxpayer identification number\n{_ssn('')}") == \
            f"Your taxpayer identification number\n{MASKED}"

    def test_a_long_line_of_labels_is_masked_in_linear_time(self):
        """Only the line's last label is tested for a value after it. A
        pattern tempered past every label took time quadratic in the line, and
        the parse budget is checked only between pages."""
        import time
        line = ("SSN " * 10_000)[:39_988] + _ssn("-")
        started = time.perf_counter()
        assert mask_ssn(line + "\nX").endswith(f"{MASKED}\nX")
        assert time.perf_counter() - started < 2

    def test_a_label_line_carrying_a_comb_value_leaves_the_next_line_alone(self):
        comb = " ".join("00000" + LAST4)
        assert mask_ssn(f"SSN {comb}\nRouting {ROUTING}") == f"SSN {MASKED}\nRouting {ROUTING}"

    @pytest.mark.parametrize("label", [
        "Name(s) shown on return Your taxpayer identification number",
        "Name(s) shown on return Identifying number",
        "Your first name and middle initial Last name Your identifying number",
        "Name Identifying number as shown on page 1 of your tax return",
    ])
    def test_the_attachment_forms_labels_govern_the_row_below(self, label):
        """Forms 8995 and 8995-A with its schedules say "Your taxpayer
        identification number", Form 8801 "Identifying number", Form 1116
        "Identifying number as shown on page 1 of your tax return". None was
        a label, so an SSN typed there without hyphens reached the transcript."""
        for value in (_ssn(""), _ssn(" "), " ".join(_ssn(""))):
            page = f"{label}\nJANE Q DOE {value}"
            assert mask_ssn(page) == f"{label}\nJANE Q DOE {MASKED}"

    @pytest.mark.parametrize("label, continued", [
        ("Name Identifying number as shown", "on page 1 of your tax return"),
        ("Your first name and middle initial Last name Your identifying number", "(see instructions)"),
    ])
    def test_a_label_wrapped_onto_a_second_row_governs_the_row_after_it(self, label, continued):
        """Schedule B (Form 1116) and Form 1040-NR print the number on the row
        under the label's continuation."""
        for value in (_ssn(""), " ".join(_ssn(""))):
            page = f"{label}\n{continued}\nJANE Q DOE {value}"
            assert mask_ssn(page) == page.replace(value, MASKED)

    @pytest.mark.parametrize("text", [
        f"Identifying number or other designation\nXYZ FUND ORD SHS {ROUTING}",
        f"Name: ACME FAMILY LP Identifying number: {ROUTING}",
        " ".join(("enter the last four digits of your TIN\n1a Proceeds 850", "00", "2026")),
        f"Business taxpayer identification number\n{ROUTING}",
        f"Partnership's identifying number\n{ROUTING}",
        f"CUSIP or other\u00a0identifying number\n{ROUTING}",
        f"CUSIP or other  identifying number\n{ROUTING}",
        f"CUSIP or other\nidentifying number\n{ROUTING} XYZ FUND",
    ])
    def test_an_identifying_number_that_is_not_the_taxpayers_survives(self, text):
        """Form 8938 names a foreign security's identifier, Form 8283 a
        partnership's EIN; "your TIN" in prose sits over an amount."""
        assert mask_ssn(text) == text

    def test_a_business_tin_column_is_not_the_taxpayers_label(self):
        """Form 8995-A Part I's column "(d) Taxpayer identification number"
        holds each business's EIN; without a possessive it governs nothing."""
        for row in (f"(d) Taxpayer identification number {ROUTING}",
                    f"specified service aggregation identification number patron\n{ROUTING}"):
            assert mask_ssn(row) == row

    def test_a_labeled_bare_run_must_be_exactly_nine_digits(self):
        """A ten-digit account number under an SSN label is not an SSN, and
        truncating it to a masked last-four would hide the mismatch."""
        assert mask_ssn("SSN 0123456789") == "SSN 0123456789"
        assert mask_ssn("SSN 12345678") == "SSN 12345678"

    def test_only_the_identifier_moves(self):
        page = "\n".join(SURVIVORS + (f"Employee SSN {_ssn('-')}",))
        out = mask_ssn(page)
        for survivor in SURVIVORS:
            assert survivor in out, f"{survivor!r} did not survive masking"
        assert _ssn("-") not in out
        assert out == page.replace(_ssn("-"), MASKED)

    def test_a_delimited_ssn_inside_a_longer_digit_run_is_left_alone(self):
        """Digit boundaries on both sides: the middle of a longer number is
        not an SSN, and a masker that fires there corrupts an amount."""
        raw = "1" + _ssn("-") + "1"
        assert mask_ssn(raw) == raw

    def test_every_occurrence_is_masked(self):
        text = f"{_ssn('-')} and spouse {_ssn('.')}"
        assert mask_ssn(text) == f"{MASKED} and spouse {MASKED}"

    def test_masking_is_idempotent_and_leaves_masked_input_alone(self):
        once = mask_ssn(f"SSN {_ssn('-')}")
        assert mask_ssn(once) == once
        assert mask_ssn(MASKED) == MASKED
        assert mask_ssn("***-**-1234") == "***-**-1234"

    def test_non_strings_pass_through_untouched(self):
        """Table and workbook cells can be None, ints or dates — an SSN typed
        as a number has no delimiter and no label, so it is outside the
        masker's shape; it is not coerced or lost."""
        assert mask_ssn(None) is None
        assert mask_ssn(123) == 123

    def test_redact_is_unchanged(self):
        """`redact` stays the error-message redactor: it still erases the
        whole identifier, last four included, and still masks nine-digit runs
        — a path or a message never needs the last four."""
        assert redact(f"W2_{_ssn('-')}.pdf") == "W2_[REDACTED].pdf"
        assert redact("statement_0000000000.pdf") == "statement_[REDACTED].pdf"


class TestATableCarriesItsLabelInAnotherCell:
    """The label that makes a bare nine-digit run an identifier sits in a
    NEIGHBOURING cell — a column header, or the cell to its left — and a
    per-cell masker cannot see it. A CSV exported with an `SSN` column reached
    the worker unmasked through the reader built to prevent exactly that."""

    def test_a_column_header_governs_the_column_below_it(self):
        rows = [["Holder", "SSN", "Routing"],
                ["Ann", _ssn(""), ROUTING],
                ["Bo", _ssn(" "), ROUTING]]
        out = mask_table(rows)
        assert [row[1] for row in out[1:]] == [MASKED, MASKED]
        assert [row[2] for row in out[1:]] == [ROUTING, ROUTING], (
            "an unlabelled nine-digit column is a routing number")
        assert [row[0] for row in out] == ["Holder", "Ann", "Bo"]

    def test_a_label_cell_governs_the_cell_to_its_right(self):
        """The left-column layout a PDF table and a hand-built workbook print."""
        out = mask_table([["Employee's SSN", _ssn(" ")],
                          ["Routing", ROUTING],
                          ["Box 1", "100,000.00"]])
        assert out == [["Employee's SSN", MASKED],
                       ["Routing", ROUTING],
                       ["Box 1", "100,000.00"]]

    def test_an_identifier_stored_as_a_number_is_masked_under_its_label(self):
        """`mask_ssn` leaves a numeric cell alone because an SSN typed as a
        number has neither delimiter nor label. In a labelled column it has
        one, and a workbook stores what looks like an integer as an integer."""
        stored = int(_itin().replace("-", ""))   # area 000 loses its zeros
        out = mask_table([["SSN", "Amount"],
                          [stored, 85000],
                          [None, 1234.56]])
        assert out[1] == [MASKED, 85000], "the amount column is not governed"
        assert out[2] == [None, 1234.56]

    def test_a_cell_that_carries_its_own_value_governs_nothing_after_it(self):
        """The rule the text layout uses: a label that already has its value
        beside it is the same-line layout, and what follows is something
        else."""
        out = mask_table([[f"SSN {_ssn('-')}"], [ROUTING], [_ssn("")]])
        assert out == [[f"SSN {MASKED}"], [ROUTING], [_ssn("")]]

    def test_a_label_governs_only_from_its_own_row_down(self):
        out = mask_table([["Prior balance", ROUTING],
                          ["Holder", "SSN"],
                          ["Ann", _ssn("")]])
        assert out[0] == ["Prior balance", ROUTING]
        assert out[2] == ["Ann", MASKED]

    def test_a_comb_cell_under_a_label_is_masked(self):
        rows = [["SSN", "Wages"], [" ".join("00000" + LAST4), "50,000.00"]]
        assert mask_table(rows)[1] == [MASKED, "50,000.00"]

    def test_a_cusip_column_is_not_the_taxpayers_number(self):
        for heading in ("CUSIP or other identifying number", "CUSIP or other\nidentifying number"):
            rows = [[heading, "Proceeds"], [ROUTING, "850 00"]]
            assert mask_table(rows) == rows

    def test_a_label_on_an_earlier_line_of_a_cell_governs_nothing(self):
        """Only a cell's last line can end in its label, as in page text."""
        rows = [["Employee SSN\nWages", "Other"], [ROUTING, " ".join(("850", "00", "2026"))]]
        assert mask_table(rows) == rows

    def test_a_row_that_is_not_a_sequence_is_still_masked(self):
        assert mask_table([f"Employee SSN {_ssn('-')}", None]) == \
            [f"Employee SSN {MASKED}", None]


def _page_lines():
    return list(SURVIVORS) + [f"Employee SSN {_ssn('-')}",
                              f"Spouse ITIN {_itin()}",
                              f"Old-style SSN {_ssn(' ')}"]


def _build_pdf(path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)
    styles = getSampleStyleSheet()
    story = [Paragraph(line, styles["Normal"]) for line in _page_lines()]
    story.append(Spacer(1, 12))
    table = Table([["Field", "Value"],
                   ["Employer EIN", "12-3456789"],
                   ["Routing", ROUTING],
                   ["Employee SSN", _ssn("-")],
                   ["Box 1", "100,000.00"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    story.append(table)
    SimpleDocTemplate(str(path), pagesize=letter).build(story)
    return path


def _raw_pdf(path):
    """The UNMASKED pdfplumber read of the fixture."""
    import pdfplumber
    text, tables = {}, {}
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages):
            text[i] = page.extract_text() or ""
            if found := page.extract_tables():
                tables[i] = found
    return text, tables


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "extract_tables",
                                    "extract_csv", "extract_excel"])
@pytest.mark.parametrize("sep", ["-", ".", "/"])
@pytest.mark.parametrize("label", ["Reference", "Employee SSN"])
def test_split_delimited_ssns_at_extraction_boundary(tmp_path, route, sep, label):
    from agents.lib import parse_utils
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    gaps = (" ", "", " ", " ")
    value = _with_separator_gaps(("000", "00", LAST4), sep, gaps)
    date = _with_separator_gaps(("01", "31", "2026"), sep, gaps)
    phone = _with_separator_gaps(("415", "555", "0100"), sep, gaps)
    rows = [[label, value], ["Date", date], ["Phone", phone],
            ["Employer EIN", "12-3456789"], ["Wages", "850.00"]]
    if route == "extract_csv":
        path = tmp_path / "split.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
    elif route == "extract_excel":
        path = _workbook(tmp_path / "split.xlsx", rows=rows)
    else:
        path = tmp_path / "split.pdf"
        table = Table(rows)
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "black")]))
        SimpleDocTemplate(str(path)).build([table])
    extracted = getattr(parse_utils, route)(str(path), workspace=str(tmp_path))
    out = ("\n".join(" ".join(word["text"] for word in page) for page in extracted.values())
           if route == "extract_pdf_words" else repr(extracted))
    assert value not in out
    assert MASKED in out
    assert all(v in out for v in (date, phone, "12-3456789", "850.00"))


def _draw(path, items):
    """A one-page PDF of (x, top, size, text[, angle]) items, drawn in the
    order given, which is the order pdfplumber meets them in the stream."""
    from reportlab.pdfgen import canvas
    page = canvas.Canvas(str(path), pagesize=(612, 792))
    for x, top, size, text, *angle in items:
        page.saveState()
        page.setFont("Helvetica", size)
        page.translate(x, 792 - top - size)
        page.rotate(angle[0] if angle else 0)
        page.drawString(0, 0, text)
        page.restoreState()
    page.save()
    return path


@pytest.mark.parametrize("route", ["extract_pdf_text", "extract_pdf_words", "extract_tables"])
@pytest.mark.parametrize("sep", ["", " ", "-"])
def test_form_1042s_tin_through_pdf_routes(tmp_path, route, sep):
    from agents.lib import parse_utils
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
    path = tmp_path / "form-1042s.pdf"
    # Headings read from the TY2026 blank form, with synthetic field values.
    # Separate boxes, rather than a continuous identifier column governing
    # every row of one table (mask_table's header contract).
    story = []
    for label, value in [("13i Recipient's U.S. TIN, if any", _ssn(sep)),
                         ("16b Payer's TIN", ROUTING),
                         ("12e Withholding agent's global intermediary identification number (GIIN)", ROUTING)]:
        table = Table([[label], [value]], colWidths=[540])
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "black"),
                                   ("FONTSIZE", (0, 0), (-1, -1), 6)]))
        story.extend([table, Spacer(1, 18)])
    SimpleDocTemplate(str(path)).build(story)
    out = repr(getattr(parse_utils, route)(str(path), workspace=str(tmp_path)))
    assert _ssn(sep) not in out
    assert MASKED in out
    assert out.count(ROUTING) == 2


class TestMaskingByPosition:
    """Page TEXT puts a row between a label and its value whenever something
    else is printed there: a rotated sidebar, a form-code row, a margin glyph.
    Each case below leaks under `mask_ssn` alone, which the first assertion
    shows on the library's own read, and is masked because the PDF routes
    also ask which label each run sits beside or under on the page."""

    @staticmethod
    def _read(tmp_path, items):
        import pdfplumber
        from agents.lib.parse_utils import extract_pdf_text
        pdf = _draw(tmp_path / "form.pdf", items)
        with pdfplumber.open(str(pdf)) as document:
            raw = document.pages[0].extract_text()
        return raw, extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]

    @pytest.mark.parametrize("between", [
        [(570, 108, 5, "R")],                         # Form 540: a margin glyph
        [(420, 108, 5, "22222")],                     # a W-2 copy's form code, in the value's column
    ])
    def test_a_value_just_under_its_label(self, tmp_path, between):
        items = between + [(380, 100, 7, "Spouse's/RDP's SSN or ITIN"), (380, 114, 10, f"JOHN DOE {_ssn('')}")]
        raw, out = self._read(tmp_path, items)
        assert _ssn("") in mask_ssn(raw), "the fixture does not separate label and value"
        assert _ssn("") not in out and f"JOHN DOE {MASKED}" in out

    def test_a_value_beside_its_label_on_a_raised_baseline(self, tmp_path):
        """Form 540's header from page 2: the value prints right of
        "Your SSN or ITIN:", its baseline a few points higher."""
        raw, out = self._read(tmp_path, [(36, 52, 9, "Special Credits", 90), (247, 55, 10, "Your SSN or ITIN:"),
                                         (310, 49, 10, _ssn(" "))])
        assert _ssn(" ") in mask_ssn(raw)
        assert _ssn(" ") not in out and MASKED in out

    @pytest.mark.parametrize("items", [
        # FTB 3532 line 3: the qualifying person's "SSN", one word of dots
        # far longer than a label's reach, the value on a raised baseline.
        [(47, 549, 9, "SSN " + "." * 150), (470, 545.8, 10, _ssn(""))],
        [(47, 549, 9, "SSN " + "\u2026" * 40), (470, 545.8, 10, _ssn(""))],   # an ellipsis character
        # Schedule 1 line 19b: "Recipient's SSN", then dots every 12 points,
        # the first 1.5 heights after the label.
        [(64.8, 194.4, 9, "Recipient's SSN"), *[(144 + 12 * k, 194.4, 9, ".") for k in range(22)],
         (404.7, 193.6, 9, _ssn(""))],
    ])
    def test_a_value_past_its_labels_dot_leader(self, tmp_path, items):
        raw, out = self._read(tmp_path, items)
        assert _ssn("") in mask_ssn(raw)
        assert _ssn("") not in out and MASKED in out

    @pytest.mark.parametrize("caption", ["B.", "(b)", "2", "12b"])
    def test_a_lettered_caption_heads_the_whole_phrase(self, tmp_path, caption):
        # 1095-A Part II: the number is under B., left of "SSN".
        raw, out = self._read(tmp_path, [(250, 100, 7, f"{caption} Covered individual SSN"),
                                         (36, 110, 5, "Part II"), (36, 122, 9, "Covered person"), (250, 122, 9, _ssn("")),
                                         (36, 250, 9, "Another person"), (252, 250, 9, _SECOND)])
        assert _ssn("") in mask_ssn(raw) and _SECOND in mask_ssn(raw)
        assert _ssn("") not in out and _SECOND not in out

    def test_a_signature_line_value_above_its_lone_label(self, tmp_path):
        # 8332: the value overlaps "Custodial", not the final "SSN".
        raw, out = self._read(tmp_path, [(300, 100, 10, _ssn("")), (36, 108, 5, "Signature"),
                                         (300, 115, 7, "Custodial parent's SSN")])
        assert _ssn("") in mask_ssn(raw)
        assert _ssn("") not in out and MASKED in out

    def test_a_numbered_question_lends_its_reach_to_its_instructions(self, tmp_path):
        # EIC: ten lines of instructions between the heading and the boxes.
        lines = ["If the child was born", "and died during the year,", "you do not need an SSN.",
                 "Instead attach a copy", "of a birth certificate", "and enter the information",
                 "required in these boxes", "for each qualifying child", "after reading the rules", "then enter the child information in the boxes beside this line"]
        raw, out = self._read(tmp_path, [(47, 100, 7, "2 Child's SSN"),
                                         *[(57, 112 + k * 10, 7, text) for k, text in enumerate(lines)],
                                         (300, 201, 9, _ssn("")), (420, 201, 9, _SECOND),
                                         (47, 230, 7, "3 Child's year of birth"), (300, 231, 9, ROUTING)])
        assert _ssn("") in mask_ssn(raw) and _SECOND in mask_ssn(raw)
        assert _ssn("") not in out and _SECOND not in out and ROUTING in out

    @pytest.mark.parametrize("placeholder", ["N/A", "NA", "Tax-Exempt", "Tax Exempt", "Applied for"])
    def test_a_placeholder_in_an_empty_box_does_not_end_a_column(self, tmp_path, placeholder):
        raw, out = self._read(tmp_path, [(238, 100, 7, "(b) SSN"), (218, 120, 9, _ssn("")),
                                         (218, 160, 9, placeholder), (218, 240, 9, _SECOND)])
        assert _SECOND in mask_ssn(raw) and _SECOND not in out and placeholder in out

    def test_a_lettered_column_starts_after_empty_rows(self, tmp_path):
        raw, out = self._read(tmp_path, [(238, 100, 7, "(b) SSN"),
                                         *[(300, 120 + 36 * k, 9, "01/15/2020") for k in range(3)],
                                         (218, 228, 9, _ssn(""))])
        assert _ssn("") in mask_ssn(raw) and _ssn("") not in out

    @pytest.mark.parametrize("shift", [-2, 2])
    def test_a_column_tolerates_a_two_point_alignment_change(self, tmp_path, shift):
        raw, out = self._read(tmp_path, [(238, 100, 7, "SSN"), (218, 120, 9, _ssn("")),
                                         (218 + shift, 300, 9, _SECOND)])
        assert _SECOND in mask_ssn(raw) and _SECOND not in out

    @pytest.mark.parametrize("caption", ["B.", "(b)"])
    def test_a_lettered_column_heading_opens_no_paragraph(self, tmp_path, caption):
        _, out = self._read(tmp_path, [(47, 100, 7, f"{caption} SSN"), (57, 112, 7, "instructions"),
                                        (57, 122, 7, "continue here"), (57, 132, 7, "on this line"),
                                        (160, 132, 9, ROUTING)])
        assert ROUTING in out

    @pytest.mark.parametrize("items", [
        # A filled next question still terminates the preceding paragraph.
        [(47, 100, 7, "2 SSN"), (57, 112, 7, "Instructions"), (57, 122, 7, "continue"),
         (57, 132, 7, "3 SSN"), (200, 130, 9, _ssn("")), (57, 142, 7, "Other"),
         (57, 152, 7, "field"), (130, 152, 9, ROUTING)],
        # A late first entry cannot cross a larger account/section heading.
        [(238, 100, 7, "(b) SSN"), (218, 300, 9, "Account number"), (218, 312, 9, ROUTING)],
        # A mid-sentence mention of SSN labels no signature line above it.
        [(36, 100, 9, ROUTING), (36, 115, 7, "Use your SSN when filing")],
        # Parenthesized words are not form letters or line numbers.
        [(250, 100, 7, "(Preparer) PTIN or SSN"), (250, 140, 9, ROUTING), (36, 125, 7, "Firm name")],
        # IT-201's unlettered preparer caption must not reach left to its EIN.
        [(250, 100, 7, "Preparer's PTIN or SSN"), (250, 140, 9, ROUTING), (36, 125, 7, "Firm name")],
        # Not lone: a raised-baseline number already sits beside the label.
        [(300, 100, 9, ROUTING), (300, 115, 7, "Custodial parent's SSN"), (450, 111, 9, _ssn(""))],
        # Not lone: the label's own value prints just under it.
        [(300, 100, 9, ROUTING), (300, 115, 7, "SSN"), (300, 124, 9, _ssn(""))],
        # A paragraph's lines are flush: an indented note ends it.
        [(47, 100, 7, "2 Child's SSN"), (57, 112, 7, "Instructions"), (63, 122, 7, "indented note"),
         (130, 122, 9, ROUTING)],
        # A paragraph starts at or just right of its question, not left of it.
        [(47, 100, 7, "2 Child's SSN"), (30, 112, 7, "Next item"), (30, 122, 7, "continues here"),
         (130, 122, 9, ROUTING)],
        # A paragraph ends at a gap wider than its question is tall,
        [(47, 100, 7, "2 Child's SSN"), (57, 112, 7, "Instructions"), (57, 134, 7, "after a gap"),
         (130, 134, 9, ROUTING)],
        # at a line indented further than a question's two heights,
        [(47, 100, 7, "2 Child's SSN"), (67, 112, 7, "Deep"), (67, 122, 7, "indent"), (130, 122, 9, ROUTING)],
        # and at a line that holds a number of its own.
        [(47, 100, 7, "2 Child's SSN"), (57, 112, 7, "Instructions"), (57, 122, 7, "continue"),
         (57, 132, 7, f"Box {_SECOND}"), (57, 142, 7, "more text"), (130, 142, 9, ROUTING)],
        # Only a row's first phrase is a numbered question,
        [(36, 100, 7, "Name"), (200, 100, 7, "2 Child's SSN"), (210, 112, 7, "Instructions"),
         (210, 122, 7, "continue"), (280, 122, 9, ROUTING)],
        # and only when the label ends it.
        [(47, 100, 7, "2 SSN of the child"), (57, 112, 7, "Instructions"), (57, 122, 7, "continue"),
         (130, 122, 9, ROUTING)],
        # A caption ends its phrase: 1095-C's "(c) DOB (if SSN or other" heads no column,
        [(296, 100, 7, "(c) DOB (if SSN or other"), (500, 180, 7, "filler"), (296, 260, 9, ROUTING)],
        # and starts at most 12 label heights left of the label.
        [(36, 100, 7, "(b) Name of the covered individual and SSN"), (300, 112, 7, "filler"),
         (36, 122, 9, ROUTING)],
        # A prose sentence ending in SSN must not open a paragraph.
        [(47, 100, 7, "Please enter an SSN"), (57, 112, 7, "Instructions"), (57, 122, 7, "continue here"),
         (57, 132, 7, "on this line"), (130, 132, 9, ROUTING)],
        # The label already has a value, so relationship fields are not SSNs.
        [(47, 100, 7, "2 Child's SSN"), (200, 98, 9, _ssn("")),
         (57, 112, 7, "Relationship"), (57, 122, 7, "to taxpayer"), (130, 122, 9, ROUTING)],
    ])
    def test_new_reaches_preserve_numbers_outside_the_labels_box(self, tmp_path, items):
        _, out = self._read(tmp_path, items)
        assert ROUTING in out

    def test_a_column_of_values_under_its_heading(self, tmp_path):
        """Form 2441's care providers: "(SSN or EIN)" heads a column whose
        cells sit a provider's name-and-address row apart."""
        other = "".join(("000", "00", "1111"))
        raw, out = self._read(tmp_path, [(318, 197, 7, "(c) Identifying number"), (330, 205, 7, "(SSN or EIN)"),
                                         (56, 242, 9, "KID CARE CENTER"), (319, 242, 9, _ssn("")),
                                         (56, 266, 9, "JANE SITTER"), (319, 266, 9, other)])
        assert _ssn("") not in out and other not in out
        assert f"{MASKED}" in out and "XXX-XX-1111" in out

    @pytest.mark.parametrize("first, size", [
        ("-".join(("00", "0555999")), 9),            # an EIN, hyphenated as the forms print one
        ("-".join(("00", "0555999")), 7),            # ... printed no larger than the heading
        ("-".join(("000", "00", "1111")), 9),       # an SSN masked in text already
        ("Tax-Exempt", 9), ("Applied for", 9),      # entries the instructions allow in place of a number
    ])
    def test_a_column_reads_past_other_providers_entries(self, tmp_path, first, size):
        """Form 2441: the first provider's column (c) holds something other
        than a bare run; the next provider's SSN is still under the heading."""
        raw, out = self._read(tmp_path, [(318, 197, 7, "(c) Identifying number"), (330, 205, 7, "(SSN or EIN)"),
                                         (56, 242, 9, "KID CARE CENTER"), (319, 242, size, first),
                                         (56, 266, 9, "JANE SITTER"), (319, 266, 9, _ssn(""))])
        assert _ssn("") in mask_ssn(raw)
        assert _ssn("") not in out and f"JANE SITTER {MASKED}" in out

    @pytest.mark.parametrize("items", [
        # One label, the taxpayer's value, then the spouse's after a word.
        [(36, 100, 8, f"SSN: {_ssn('')}"), (150, 100, 8, f"Spouse {_SECOND}")],
        # A dependents row with each relationship after its number.
        [(36, 100, 7, "SSN"), (60, 100, 9, f"{_ssn('')} Son"), (160, 100, 9, f"{_SECOND} Daughter")],
        # Form 540's dependents, the first typed with hyphens (the field takes eleven characters).
        [(78, 144, 7, "SSN. See"), (136, 146, 10, _ssn("-")), (287, 146, 10, _SECOND), (78, 152, 7, "instructions.")],
    ])
    def test_a_row_label_reaches_each_value_after_the_first(self, tmp_path, items):
        raw, out = self._read(tmp_path, items)
        assert _SECOND in mask_ssn(raw)
        assert _ssn("") not in out and _SECOND not in out

    @pytest.mark.parametrize("items", [
        # Form 2441 Part II: the first person's number just under the heading,
        # the next a row further down than "just under" reaches.
        [(300, 100, 7, "social security number"), (300, 110, 9, _ssn("")), (300, 134, 9, _SECOND)],
        # The first person's number typed with hyphens: still a heading's column.
        [(300, 100, 7, "social security number"), (300, 110, 9, _ssn("-")), (300, 134, 9, _SECOND)],
        # FTB 3506 Part II: "(See instructions)" printed under the heading.
        [(250, 408, 7, "social security number (SSN)"), (266, 416, 7, "(See instructions)"),
         (240, 437, 10, _ssn("")), (240, 458, 10, _SECOND)],
    ])
    def test_a_heading_governs_its_column_past_its_first_entry(self, tmp_path, items):
        raw, out = self._read(tmp_path, items)
        assert _SECOND in mask_ssn(raw)
        assert _ssn("") not in out and _SECOND not in out

    @pytest.mark.parametrize("pitch", [36, 21], ids=["1095-B rows, 5.1 heights", "IT-201 dependents, 3"])
    def test_a_column_runs_on_to_each_entry_lined_up_with_its_first(self, tmp_path, pitch):
        """Entries down the whole page, far past a heading's own reach and
        past a column's reach of its first, each lined up with the first
        (IT-201's first dependent prints 0.2 points right of the rest). Each
        is its own number: a run governed once is masked wherever the page
        prints it."""
        values = ["".join(("000", "00", str(1000 + k))) for k in range(700 // pitch)]
        raw, out = self._read(tmp_path, [(238, 20, 7, "SSN"), *[(218 - 0.2 * (k > 0), 38.1 + pitch * k, 9, value)
                                                                for k, value in enumerate(values)]])
        assert values[-1] in mask_ssn(raw)
        assert not any(value in out for value in values)

    @pytest.mark.parametrize("between", [
        [(300, 337.6 + 36 * k, 9, "01/15/2020") for k in (1, 2, 3)],    # a date of birth in the next column
        [(218, 337.6 + 36 * k, 9, "XXX-XX-1111") for k in (1, 2, 3)],   # entries the page masked itself
    ], ids=["dates of birth", "masked by the page"])
    def test_a_column_runs_on_past_rows_without_a_number(self, tmp_path, between):
        """Form 1095-C's covered individuals: a person with no SSN gets a
        date of birth instead, so three rows leave the column's box empty,
        or hold entries the page masked itself, and its next entry prints
        19 heights below the last."""
        raw, out = self._read(tmp_path, [(238, 319.5, 7, "SSN"), (218, 337.6, 9, _ssn("")), *between,
                                         (218, 337.6 + 36 * 4, 9, _SECOND)])
        assert _SECOND in mask_ssn(raw)
        assert _SECOND not in out

    def test_a_column_runs_on_past_a_page_of_rows_without_a_number(self, tmp_path):
        """A Form 1095-C page's thirteen rows, the eleven between the first
        and the last given a date of birth for a person with no SSN."""
        raw, out = self._read(tmp_path, [(238, 319.5, 7, "SSN"), (218, 337.6, 9, _ssn("")),
                                         *[(300, 337.6 + 36 * k, 9, "01/15/2020") for k in range(1, 12)],
                                         (218, 337.6 + 36 * 12, 9, _SECOND)])
        assert _SECOND in mask_ssn(raw)
        assert _SECOND not in out

    @pytest.mark.parametrize("first", [218, 215, 212], ids=["left", "centred (Form 1095-B's)", "right"])
    def test_a_column_runs_on_under_a_filers_hyphens(self, tmp_path, first):
        """The first entry typed with hyphens, a later one without: the two
        share only a left edge, a centre or a right edge, as the boxes align
        what is typed in them, the hyphens adding 3 points a side."""
        raw, out = self._read(tmp_path, [(238, 319.5, 7, "SSN"), (first, 337.6, 9, _ssn("-")),
                                         (218, 337.6 + 36 * 4, 9, _SECOND)])
        assert _SECOND in mask_ssn(raw)
        assert _SECOND not in out

    @pytest.mark.parametrize("head, overlay", [("(2) Social security", " " * 5), ("(2) Identifying", "")],
                             ids=["TY2024 1040", "1040-NR"])
    def test_a_heading_wrapped_over_two_rows(self, tmp_path, head, overlay):
        """The dependents' heading prints its last word on a row of its own,
        and the TY2024 Form 1040 draws spaces over it, splitting "number"
        into "num b e r"; a comb field of nine digits under it."""
        comb = [(268.3 + 9.7 * i, 379.6, 9, digit) for i, digit in enumerate(_ssn(""))]
        raw, out = self._read(tmp_path, [(281.8, 359.8, 7, head), (297.6, 368.2, 7, "number"),
                                         (309.6, 368.0, 7, overlay), *comb])
        assert " ".join(_ssn("")) in mask_ssn(raw)
        assert " ".join(_ssn("")) not in out

    @pytest.mark.parametrize("glyph", ["\u2022", "|"])   # a bullet the font cannot map, and a rule
    def test_a_row_label_governs_each_value_across_its_row(self, tmp_path, glyph):
        """Form 540's dependents: "SSN. See instructions." heads a row of
        numbers in columns, a glyph between each."""
        raw, out = self._read(tmp_path, [(78, 144, 7, "SSN. See"), (121, 145, 10, glyph), (136, 146, 10, _ssn(" ")),
                                         (272, 145, 10, glyph), (287, 146, 10, " ".join(("000", "00", "1111"))),
                                         (78, 152, 7, "instructions.")])
        assert " ".join(("000", "00", "1111")) in mask_ssn(raw)
        assert _ssn(" ") not in out and " ".join(("000", "00", "1111")) not in out
        assert MASKED in out and "XXX-XX-1111" in out

    @pytest.mark.parametrize("items", [
        # A TIN carrying its value, masked or bare, then an account number after its own label.
        [(36, 100, 8, "Recipient's TIN: XXX-XX-6789"), (330, 100, 8, f"Account Number: {ROUTING}")],
        [(36, 100, 8, f"Recipient's TIN: {_ssn('')}"), (330, 100, 8, f"Account Number: {ROUTING}")],
        # A ZIP+4 typed with a space on the TIN's row.
        [(36, 100, 8, "Recipient's TIN: XXX-XX-6789"), (250, 100, 8, "SAN FRANCISCO CA " + " ".join(("94105", "1234")))],
        # The next box's account number, beside a value-less TIN label's box.
        [(36, 100, 7, "RECIPIENT'S TIN"), (120, 100, 7, "Account no."), (570, 105, 5, "R"),
         (36, 110, 9, "XXX-XX-6789"), (120, 110, 9, ROUTING)],
        # An account number below a box whose label holds its own value.
        [(36, 60, 8, "RECIPIENT'S TIN"), (36, 70, 9, "XXX-XX-6789"), (36, 120, 8, "Account"), (80, 120, 8, ROUTING)],
        # Beside a TIN label whose value prints under it (page text masks the
        # row's copy as a same-line value; the page's other copy is kept), and
        # after a TIN's masked value with only a rule between.
        [(36, 100, 7, "RECIPIENT'S TIN"), (110, 100, 7, f"Account Number {ROUTING}"), (36, 110, 9, "XXX-XX-6789"),
         (300, 400, 9, f"1099-DIV Account {ROUTING}")],
        [(330, 90, 7, "Account number"), (36, 100, 8, "Recipient's TIN: XXX-XX-6789"), (320, 100, 8, "|"),
         (330, 100, 8, ROUTING)],
        [(36, 100, 8, "Recipient's TIN: XXX-XX-6789"), (160, 100, 8, "|"), (170, 100, 8, ROUTING)],
        [(36, 100, 8, "Recipient's TIN: ***-**-6789"), (160, 100, 8, "|"), (170, 100, 8, ROUTING)],
        [(36, 100, 8, "Recipient's TIN: xxx-xx-6789"), (160, 100, 8, "|"), (170, 100, 8, ROUTING)],
        # Beyond each reach, with nothing printed between: far along the row
        # (a raised baseline, so page text puts it on another line), and far
        # down the column (a row elsewhere between, so not the next line).
        [(36, 100, 7, "SSN"), (300, 96, 9, ROUTING)],
        [(36, 100, 7, "SSN " + "." * 40), (235, 96, 9, ROUTING)],          # 15 heights past a leader's end
        [(36, 100, 7, "SSN"), (69, 100, 7, "." * 120), (330, 96, 9, ROUTING)],      # dots 2.7 heights from the label
        [(36, 100, 7, "SSN of"), (63, 100, 7, "." * 120), (330, 96, 9, ROUTING)],   # a leader after other words
        [(36, 100, 7, "SSN"), (200, 100, 7, "." * 40), (300, 96, 9, ROUTING)],       # dots far from the label
        # A column's entry after the form prints across it, within the
        # heading's own reach or far past it.
        [(238, 319.5, 7, "SSN"), (218, 337.6, 9, _ssn("")), (220, 360, 7, "Part II"), (218, 380, 9, ROUTING)],
        [(238, 319.5, 7, "SSN"), (218, 337.6, 9, _ssn("")), (220, 600, 7, "Part III"), (218, 640, 9, ROUTING)],
        # A statement's column, its first entry masked by the page: no column
        # of the filer's to run on past the heading's reach.
        [(238, 319.5, 7, "SSN"), (218, 360, 9, "XXX-XX-1111"), (218, 600, 9, ROUTING)],
        # IT-201's header SSN box, then a column of amounts 3 points left of
        # its value: a box's value lines up with no column under it.
        [(328, 39, 7, "Social Security number"), (311.5, 51, 9, _ssn("")),
         *[(308.5, 75 + 12 * k, 9, "".join(("021", "000", f"{k:03d}"))) for k in range(8)], (308.5, 171, 9, ROUTING)],
        # A statement's TIN box, then far below, lined up with its value, an
        # account number under a heading printed larger than the label, and
        # one beside its own label with nothing printed above it.
        [(320, 60, 6, "RECIPIENT\u2019S TIN"), (320, 70, 9, _ssn("")), (36, 300, 8, "Summary of accounts"),
         (320, 300, 8, "Account number"), (36, 312, 9, "Brokerage account"), (320, 312, 9, ROUTING)],
        [(320, 60, 6, "RECIPIENT\u2019S TIN"), (320, 70, 9, _ssn("")), (200, 480, 6, "Account number:"),
         (320, 480, 9, ROUTING)],
        # A statement's box: the page's masked number, the run under it its
        # account's; numbers lined up far below are no column of the filer's.
        [(238, 319.5, 7, "SSN"), (218, 330, 9, "XXX-XX-1111"), (218, 341, 9, _ssn("")), (218, 600, 9, ROUTING)],
        # A heading's second row that is more than the label's end, is not
        # under it, or finishes a heading that is no identifier label; and a
        # number 17 heights under a wrapped heading, past a reach measured in
        # one row's height.
        [(36, 100, 7, "(2) Social security"), (36, 109, 7, "number of children"), (36, 120, 9, ROUTING)],
        # Form 1099-B's instructions end a sentence there: the period is the pattern's own refusal.
        [(36, 100, 7, "other applicable identifying"), (36, 109, 7, "number."), (36, 120, 9, ROUTING)],
        [(36, 100, 7, "(2) Social security"), (200, 109, 7, "number"), (200, 120, 9, ROUTING)],   # right of it
        [(200, 100, 7, "(2) Social security"), (36, 109, 7, "number"), (36, 120, 9, ROUTING)],    # left of it
        [(36, 100, 7, "(2) Social security"), (36, 130, 7, "number"), (36, 141, 9, ROUTING)],     # rows apart
        [(36, 100, 7, "(3) Account"), (36, 109, 7, "number"), (36, 120, 9, ROUTING)],             # no label
        [(36, 100, 7, "(2) Social security"), (36, 109, 7, "number"), (36, 240, 9, ROUTING)],     # far below
        [(330, 205, 4.5, "(SSN or EIN)"), (36, 240, 9, "Part II"), (330, 278, 9, ROUTING),
         (400, 700, 7, "Your SSN")],   # a taller label elsewhere widens no other label's reach
    ])
    def test_a_label_governs_only_its_own_box(self, tmp_path, items):
        raw, out = self._read(tmp_path, items)
        assert _ssn("") not in out
        assert ROUTING in out or " ".join(("94105", "1234")) in out

    def test_a_long_heading_phrase_is_read_once_and_only_at_its_end(self, monkeypatch):
        """One phrase of 2,000 words over 500 phrases of one word: reading
        the whole phrase for each would cost their product."""
        from agents.lib import parse_utils
        texts, reads, read = [], [], parse_utils._row_text
        monkeypatch.setattr(parse_utils, "identifier_spans", lambda text: texts.append(text) or ([], [], [], []))
        monkeypatch.setattr(parse_utils, "_row_text", lambda row: reads.append(len(row)) or read(row))
        upper = [{"text": "word", "x0": 4.0 * k, "x1": 4.0 * k + 3, "top": 100.0, "bottom": 107.0} for k in range(2000)]
        lower = [{"text": "number", "x0": 16.0 * k, "x1": 16.0 * k + 3, "top": 108.0, "bottom": 115.0} for k in range(500)]
        parse_utils._wrapped_labels([upper, lower], lambda: None)
        assert reads.count(2000) == 1 and len(texts) == 500
        assert max(len(text) for text in texts) <= 100, "the phrase's end, not its 2,000 words"

    def test_a_heading_lower_down_takes_its_column_over(self):
        """A column has no reach to end it, so the next lined up with it
        replaces it: a strip holds no more columns than it has places to
        line up, however long the page."""
        from agents.lib.parse_utils import _lined_columns, _lines_up, _start_column
        columns, lined = {}, {}
        entry = {"x0": 100.0, "x1": 145.0, "top": 10.0, "bottom": 19.0}
        _start_column(columns, lined, 0, entry, 7.0)
        _start_column(columns, lined, 1, {**entry, "x0": 110.0, "x1": 155.0}, 7.0)    # 10 points right: its own
        _start_column(columns, lined, 2, {**entry, "x0": 97.0, "top": 200.0, "bottom": 209.0}, 7.0)   # its right edge
        assert sorted(columns) == [1, 2] and not any(0 in found for found in lined.values())
        assert [k for k, _, column in _lined_columns(columns, lined, entry) if _lines_up(entry, column)] == [2]

    @pytest.mark.parametrize("edges", [(200.0, 225.0), (285.0, 305.0), (237.5, 267.5)], ids=["left", "right", "centre"])
    def test_a_column_is_found_by_the_edge_its_entries_share(self, edges):
        """A narrower entry shares one place with the column's first: its
        left edge, right edge or centre, each in a strip of its own here."""
        from agents.lib.parse_utils import _lined_columns, _start_column
        columns, lined = {}, {}
        _start_column(columns, lined, 0, {"x0": 200.0, "x1": 305.0, "top": 10.0, "bottom": 19.0}, 7.0)
        entry = {"x0": edges[0], "x1": edges[1], "top": 200.0, "bottom": 209.0}
        assert [k for k, *_ in _lined_columns(columns, lined, entry)] == [0]

    def test_a_column_out_of_reach_of_its_last_entry_is_dropped(self):
        """Entries come top to bottom, so a column past its reach of one is
        past it for every later entry: it leaves the lookup for good."""
        from agents.lib.parse_utils import _RUN, _lined_columns, _start_column
        columns, lined = {}, {}
        _start_column(columns, lined, 0, {"x0": 100.0, "x1": 145.0, "top": 10.0, "bottom": 19.0}, 7.0)
        near = {"x0": 100.0, "x1": 145.0, "top": 19.0 + _RUN * 7, "bottom": 28.0 + _RUN * 7}
        assert [k for k, *_ in _lined_columns(columns, lined, near)] == [0]
        far = {**near, "top": near["top"] + 1, "bottom": near["bottom"] + 1}
        assert _lined_columns(columns, lined, far) == [] and columns == {} and not any(lined.values())

    def test_a_word_of_many_labels_walks_its_leader_once(self, monkeypatch):
        """Labels in one word ("SSN/ITIN") share the leader after it: one
        walk, not one per label, or a crafted word costs labels times dots."""
        from agents.lib import parse_utils
        calls, walk = [], parse_utils._leader_end
        monkeypatch.setattr(parse_utils, "_leader_end", lambda *args: calls.append(1) or walk(*args))
        word = {"text": "/".join(["SSN"] * 50), "x0": 0.0, "x1": 14.0, "top": 100.0, "bottom": 107.0}
        dots = [{"text": ".", "x0": 16.0 + 4 * k, "x1": 18.0 + 4 * k, "top": 100.0, "bottom": 107.0} for k in range(200)]
        labels = parse_utils._page_identifiers([word, *dots], lambda: None)[0]
        assert len(labels) == 50 and {start for *_, start in labels} == {dots[-1]["x1"]}
        assert len(calls) == 1

    def test_a_word_of_many_labels_does_not_copy_each_remaining_suffix(self, monkeypatch):
        from agents.lib import parse_utils
        copied, read = [], parse_utils._row_text

        class Measured(str):
            def __getitem__(self, key):
                value = super().__getitem__(key)
                if isinstance(key, slice):
                    copied.append(len(value))
                return value

        def measured(row):
            text, starts = read(row)
            return Measured(text), starts

        monkeypatch.setattr(parse_utils, "_row_text", measured)
        text = "/".join(["SSN"] * 1000)
        word = {"text": text, "x0": 36.0, "x1": 50.0, "top": 100.0, "bottom": 107.0}
        assert len(parse_utils._page_identifiers([word], lambda: None)[0]) == 1000
        assert sum(copied) <= len(text) * 4, "work must grow with the word, not its labels times its length"

    def test_repeated_numbered_heads_do_not_walk_overlapping_paragraphs(self):
        from agents.lib.parse_utils import _page_identifiers, _paragraph_labels
        words = [{"text": "2 SSN", "x0": 36.0, "x1": 68.0, "top": 20.0 + 10 * k, "bottom": 27.0 + 10 * k}
                 for k in range(100)]
        labels = _page_identifiers(words, lambda: None)[0]
        visits = []
        assert _paragraph_labels(words, labels, lambda: visits.append(1)) == []
        assert len(visits) <= 2 * len(labels)

    def test_the_pass_checks_the_time_budget_as_it_reads(self):
        """A crafted page is refused when its time runs out, not after."""
        from agents.lib.parse_utils import _governed_runs
        words = []
        for box in range(50):   # 50 boxes: a label row, and its value's row under it
            words += [{"text": "SSN", "x0": 36.0, "x1": 50.0, "top": 20.0 * box, "bottom": 20.0 * box + 7},
                      {"text": _ssn(""), "x0": 36.0, "x1": 76.0, "top": 20.0 * box + 9, "bottom": 20.0 * box + 16}]
        calls = []
        assert _governed_runs(words, lambda: calls.append(1)) == {_ssn("")}
        # 100 rows, 50 labels in each of four passes, the 100 phrases the
        # labels' pass reads, the 99 pairs of rows and 99 phrases the
        # wrapped-heading pass reads, the 100 rows and 100 phrases searched
        # for an empty box's placeholder, 50 runs, and the 240 label-and-run
        # pairs the column search weighs.
        assert len(calls) == 1088, "every loop checks the budget"

        def expired():
            if len(calls) > 500:
                raise TimeoutError
            calls.append(1)
        with pytest.raises(TimeoutError):
            _governed_runs(words, expired)

    def test_the_signature_and_paragraph_passes_check_the_budget(self):
        """Signature lines over numbered questions, each with an instruction
        line, reach the passes the page above never does."""
        from agents.lib.parse_utils import _governed_runs
        words = []
        for box in range(20):   # a signature line's value, its lone numbered label, an instruction line
            y = 40.0 * box + 20
            words += [{"text": _ssn(""), "x0": 36.0, "x1": 76.0, "top": y, "bottom": y + 7},
                      {"text": "2 SSN", "x0": 36.0, "x1": 60.0, "top": y + 9, "bottom": y + 16},
                      {"text": "see", "x0": 40.0, "x1": 52.0, "top": y + 18, "bottom": y + 25}]
        calls = []
        assert _governed_runs(words, lambda: calls.append(1)) == {_ssn("")}
        # 60 rows, 60 phrases and 20 labels the labels' pass reads, the 59
        # pairs of rows and 59 phrases the wrapped-heading pass reads, 20
        # labels looked under, the 20 labels and 20 instruction lines read
        # beside, 20 runs and the 154 label-and-run pairs the column search
        # weighs, 60 rows and 60 phrases searched for a placeholder, 20 lone
        # labels, 20 labels and their 20 runs above, and 20 questions whose
        # paragraphs walk 39 rows.
        assert len(calls) == 731, "every loop checks the budget"

    def test_a_number_elsewhere_on_the_page_survives(self, tmp_path):
        """The employer's EIN in the box below and left of the SSN's, an
        amount down the SSN column after other text, a routing number far
        from any label: none sits beside or under the label."""
        cents = " ".join(("850", "00", "2026"))
        raw, out = self._read(tmp_path, [(150, 38, 7, "a Employee's social security number"),
                                         (155, 50, 9, _ssn("")), (20, 58, 9, ROUTING),
                                         (40, 66, 7, "b Employer identification number (EIN)"),
                                         (40, 74, 9, ROUTING), (150, 120, 7, "1 Wages, tips, other compensation"),
                                         (155, 132, 9, cents), (400, 300, 9, f"Routing {ROUTING}")])
        assert MASKED in out
        assert out.count(ROUTING) == 3 and cents in out

    def test_a_label_carrying_its_value_governs_nothing_under_it(self, tmp_path):
        raw, out = self._read(tmp_path, [(100, 100, 9, f"SSN {_ssn('-')}"), (100, 112, 9, f"Account {ROUTING}")])
        assert f"SSN {MASKED}" in out and f"Account {ROUTING}" in out
        raw, out = self._read(tmp_path, [(100, 100, 9, f"SSN {_ssn('-')}"), (100, 160, 9, ROUTING)])
        assert out.endswith(ROUTING), "a column under a label with its value is not that label's"

    def test_words_and_tables_are_masked_by_position_too(self, tmp_path):
        from agents.lib.parse_utils import extract_pdf_words, extract_tables
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table
        pdf = _draw(tmp_path / "words.pdf", [(570, 108, 5, "R"), (380, 100, 7, "Spouse's SSN"),
                                             (380, 114, 10, f"JOHN DOE {_ssn('')}")])
        words = extract_pdf_words(str(pdf), workspace=str(tmp_path))[0]
        assert _ssn("") not in " ".join(w["text"] for w in words)
        assert f"JOHN DOE {MASKED}" in [w["text"] for w in words]
        doc = tmp_path / "table.pdf"
        # The label prints above the table, outside every cell.
        from reportlab.platypus import Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        SimpleDocTemplate(str(doc), pagesize=letter).build([
            Paragraph("Your social security number", getSampleStyleSheet()["Normal"]), Spacer(1, 2),
            Table([[_ssn(""), "Wages"], ["Acme", "100.00"]], hAlign="LEFT",
                  style=[("GRID", (0, 0), (-1, -1), 0.5, "black")])])
        tables = extract_tables(str(doc), workspace=str(tmp_path))
        cells = [cell for table in tables.get(0, []) for row in table for cell in row]
        assert _ssn("") not in cells and MASKED in cells

    def test_a_run_split_across_table_cells_is_masked_in_place(self):
        """Form 1040's comb boxes come out of a table as one cell per group."""
        from agents.lib.secure_fs import mask_row_runs
        comb = " ".join(_ssn(""))
        assert mask_row_runs(["(3) SSN", "000", "00", LAST4, None, "Son"], {comb}) == [
            "(3) SSN", "XXX", "XX", LAST4, None, "Son"]
        assert mask_row_runs(["0 0 0", "0 0", " ".join(LAST4)], {comb}) == ["X X X", "X X", " ".join(LAST4)]
        assert mask_row_runs(["000", "00", LAST4 + "5"], {comb}) == ["000", "00", LAST4 + "5"]

    def test_a_row_the_table_cuts_short_still_masks_each_whole_nine(self):
        """Form 1040-SR's dependents: the table holds three dependents and the
        start of a fourth, whose last group falls outside it."""
        from agents.lib.secure_fs import mask_row_runs, prepare_runs
        comb = " ".join(_ssn("") + _SECOND + _ssn("") + _SECOND)
        runs = prepare_runs({comb})
        assert {_ssn(""), _SECOND} <= set(runs.spelling)
        row = mask_row_runs(["(2) SSN", "000", "00", LAST4, "000", "00", _SECOND[5:], "000", "00", LAST4, "000", "00"], runs)
        assert row == ["(2) SSN", "XXX", "XX", LAST4, "XXX", "XX", _SECOND[5:], "XXX", "XX", LAST4, "000", "00"]

    def test_a_glyph_stretched_across_the_page_costs_one_page_of_strips(self):
        from agents.lib.parse_utils import _STRIPS, _strips
        index = _strips([{"text": "x", "x0": -1e9, "x1": 1e9, "top": 0.0, "bottom": 1.0}])
        assert set(index) == set(range(_STRIPS + 1))

    def test_tables_mask_a_run_split_across_cells(self, tmp_path):
        from agents.lib.parse_utils import extract_tables
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
        doc = tmp_path / "split.pdf"
        SimpleDocTemplate(str(doc), pagesize=letter).build([
            Paragraph("Your social security number", getSampleStyleSheet()["Normal"]), Spacer(1, 2),
            Table([["000", "00", LAST4, "Wages"], ["Acme", "", "", "100.00"]], hAlign="LEFT",
                  style=[("GRID", (0, 0), (-1, -1), 0.5, "black")])])
        cells = [cell for table in extract_tables(str(doc), workspace=str(tmp_path)).get(0, [])
                 for row in table for cell in row]
        assert cells[:3] == ["XXX", "XX", LAST4]

    def test_the_longest_run_is_masked_first(self):
        """Form 1040's comb row is one run of every dependent; the first
        dependent alone is a run too, and must not split the row."""
        from agents.lib.secure_fs import mask_runs
        second = "".join(("000", "00", "1111"))
        row = " ".join(_ssn("") + second)
        assert mask_runs(row, {row, " ".join(_ssn(""))}) == f"{MASKED} XXX-XX-1111"

    def test_mask_runs_masks_a_run_however_it_is_spaced(self):
        from agents.lib.secure_fs import mask_runs
        comb = " ".join(_ssn(""))
        assert mask_runs(f"A {_ssn('')} B {_ssn(' ')} C {comb}", {_ssn(" ")}) == f"A {MASKED} B {MASKED} C {MASKED}"
        assert mask_runs(f"1{_ssn('')}", {_ssn("")}) == f"1{_ssn('')}"


class TestEveryExtractionRouteMasks:
    """Each route is compared against the UNMASKED library read of the same
    file, so the assertion is "the route applies `mask_ssn` and nothing else
    changed", not merely "the SSN is gone"."""

    def test_pdf_words(self, tmp_path):
        """Positions for a reader that finds a value by where the form prints
        it. Masking runs over the page's rows as text does; a row holding an
        identifier comes back as ONE masked word spanning it, and every other
        word keeps the library's position."""
        import pdfplumber
        from agents.lib.parse_utils import extract_pdf_words
        pdf = _build_pdf(tmp_path / "w2.pdf")
        out = extract_pdf_words(str(pdf), workspace=str(tmp_path))[0]
        joined = " ".join(w["text"] for w in out)
        assert _ssn("-") not in joined and _itin() not in joined and _ssn(" ") not in joined
        assert {w["text"] for w in out} >= {f"Employee SSN {MASKED}", f"Spouse ITIN {MASKED}",
                                            f"Old-style SSN {MASKED}", "Acme", "100,000.00"}
        with pdfplumber.open(str(pdf)) as document:
            raw = document.pages[0].extract_words()
        for found in (w for w in raw if w["text"] in {"Acme", "Routing", "100,000.00"}):
            assert {"text": found["text"], **{k: round(found[k], 1) for k in ("x0", "x1", "top", "bottom")}} in out

    def test_pdf_words_mask_a_comb_field_under_its_label(self, tmp_path):
        """The label row and the comb row are separate rows: the row ABOVE
        governs, as in page text, whatever order the stream drew them."""
        from reportlab.pdfgen import canvas
        from agents.lib.parse_utils import extract_pdf_words
        pdf = tmp_path / "f1040.pdf"
        page = canvas.Canvas(str(pdf))
        page.setFont("Helvetica", 10)
        for index, digit in enumerate("00000" + LAST4):  # drawn before its label
            page.drawCentredString(469 + 107 / 9 * (index + 0.5), 686, digit)
        page.drawString(40, 686, "JANE Q DOE")
        page.setFont("Helvetica", 7)
        page.drawString(472, 700, "Your social security number")
        page.save()
        out = extract_pdf_words(str(pdf), workspace=str(tmp_path))[0]
        assert [w["text"] for w in out] == ["Your", "social", "security", "number", f"JANE Q DOE {MASKED}"]

    def test_pdf_words_mask_a_row_that_steps_down_in_font_size(self, tmp_path):
        """A first name at 14pt, a last name at 11pt and the comb at 8pt on one
        baseline: their tops step down in small changes, and pdfplumber's
        text keeps them on one row under the label. So must the words."""
        from reportlab.pdfgen import canvas
        from agents.lib.parse_utils import extract_pdf_text, extract_pdf_words
        pdf = tmp_path / "f1040.pdf"
        page = canvas.Canvas(str(pdf))
        page.setFont("Helvetica", 7)
        page.drawString(472, 700, "Your social security number")
        for size, x, text in ((14, 40, "JANE"), (11, 90, "DOE")):
            page.setFont("Helvetica", size)
            page.drawString(x, 684, text)
        page.setFont("Helvetica", 8)
        for index, digit in enumerate("00000" + LAST4):
            page.drawCentredString(469 + 107 / 9 * (index + 0.5), 684, digit)
        page.save()
        assert MASKED in extract_pdf_text(str(pdf), workspace=str(tmp_path))[0]
        words = extract_pdf_words(str(pdf), workspace=str(tmp_path))[0]
        assert [w["text"] for w in words][-1] == f"JANE DOE {MASKED}"

    def test_pdf_text_masks_a_flattened_comb_field(self, tmp_path):
        """Drawn the way a flattened Form 1040 prints it: the label row, then
        each digit centred in its own box of the comb field (Form 1040 page 1,
        field f1_16: 107 points across nine boxes)."""
        from reportlab.pdfgen import canvas
        from agents.lib.parse_utils import extract_pdf_text
        pdf = tmp_path / "f1040.pdf"
        page = canvas.Canvas(str(pdf))
        page.setFont("Helvetica", 7)
        page.drawString(36, 700, "Your first name and middle initial")
        page.drawString(472, 700, "Your social security number")
        page.setFont("Helvetica", 10)
        page.drawString(40, 686, "JANE Q DOE")
        for index, digit in enumerate("00000" + LAST4):
            page.drawCentredString(469 + 107 / 9 * (index + 0.5), 686, digit)
        page.save()
        raw_text, _ = _raw_pdf(pdf)
        comb = " ".join("00000" + LAST4)
        assert comb in raw_text[0], "fixture does not extract the comb spelling"
        out = extract_pdf_text(str(pdf), workspace=str(tmp_path))
        assert comb not in out[0] and f"JANE Q DOE {MASKED}" in out[0]

    def test_pdf_text(self, tmp_path):
        from agents.lib.parse_utils import extract_pdf_text
        pdf = _build_pdf(tmp_path / "w2.pdf")
        raw_text, _ = _raw_pdf(pdf)
        assert _ssn("-") in raw_text[0], "fixture does not carry the SSN"
        out = extract_pdf_text(str(pdf), workspace=str(tmp_path))
        assert out == {i: mask_ssn(t) for i, t in raw_text.items()}
        assert _ssn("-") not in out[0] and _itin() not in out[0]
        # Three in the prose lines plus the table row's, which page text includes.
        assert out[0].count(MASKED) == 4
        for survivor in SURVIVORS:
            assert survivor in out[0]

    def test_pdf_text_page_subset(self, tmp_path):
        from agents.lib.parse_utils import extract_pdf_text
        pdf = _build_pdf(tmp_path / "w2.pdf")
        out = extract_pdf_text(str(pdf), pages=[0], workspace=str(tmp_path))
        assert list(out) == [0] and _ssn("-") not in out[0]

    def test_pdf_tables(self, tmp_path):
        from agents.lib.parse_utils import extract_tables
        pdf = _build_pdf(tmp_path / "w2.pdf")
        _, raw_tables = _raw_pdf(pdf)
        flat_raw = [c for page in raw_tables.values() for t in page
                    for r in t for c in r]
        assert _ssn("-") in flat_raw, "fixture table does not carry the SSN"
        out = extract_tables(str(pdf), workspace=str(tmp_path))
        assert out == {i: [[[mask_ssn(c) for c in row] for row in table]
                           for table in tables]
                       for i, tables in raw_tables.items()}
        flat = [c for page in out.values() for t in page for r in t for c in r]
        assert _ssn("-") not in flat and MASKED in flat
        assert ROUTING in flat and "12-3456789" in flat

    def test_excel(self, tmp_path):
        from agents.lib.parse_utils import extract_excel
        rows = [["Holder", "SSN", "Routing", "Balance"],
                ["Acme Widgets LLC", _ssn("-"), ROUTING, 1234.56],
                ["Spouse", f"SSN {_ssn('')}", 21000021, None]]
        book = _workbook(tmp_path / "bank.xlsx", rows, title="Accounts")
        out = extract_excel(str(book), workspace=str(tmp_path))
        assert out == {"Accounts": [[mask_ssn(c) for c in row] for row in rows]}
        assert out["Accounts"][1][1] == MASKED
        assert out["Accounts"][2][1] == f"SSN {MASKED}"
        assert out["Accounts"][1][2] == ROUTING
        assert out["Accounts"][1][3] == 1234.56
        assert out["Accounts"][2][2] == 21000021

    def test_csv(self, tmp_path):
        from agents.lib.parse_utils import extract_csv
        doc = tmp_path / "export.csv"
        rows = [["Holder", "SSN", "Routing", "Amount"],
                ["Acme Widgets LLC", _ssn("/"), ROUTING, "1,234.56"]]
        with doc.open("w", newline="") as f:
            csv.writer(f).writerows(rows)
        out = extract_csv(str(doc), workspace=str(tmp_path))
        assert out == [[mask_ssn(c) for c in row] for row in rows]
        assert out[1][1] == MASKED and out[1][2] == ROUTING

    def test_csv_with_a_bare_identifier_column(self, tmp_path):
        """The export that got through: an `SSN` header over bare nine-digit
        runs, which is the spelling masked only under a label — and the label
        is a cell away."""
        from agents.lib.parse_utils import extract_csv
        doc = tmp_path / "payroll.csv"
        rows = [["Employee", "SSN", "Routing", "Gross"],
                ["Ann", _ssn(""), ROUTING, "42,150.00"]]
        with doc.open("w", newline="") as f:
            csv.writer(f).writerows(rows)
        out = extract_csv(str(doc), workspace=str(tmp_path))
        assert _ssn("") not in [c for row in out for c in row]
        assert out[1] == ["Ann", MASKED, ROUTING, "42,150.00"]

    def test_excel_masks_the_sheet_name(self, tmp_path):
        """The sheet name is a string this module returns — `excel` prints it
        as a JSON key, and the refusal path beside it redacts the same name."""
        from agents.lib.parse_utils import extract_excel
        book = _workbook(tmp_path / "bank.xlsx", [["Balance", 1234.56]],
                         title=f"Owner {_ssn('-')}")
        out = extract_excel(str(book), workspace=str(tmp_path))
        assert list(out) == [f"Owner {MASKED}"]
        proc = _run_cli("excel", str(book))
        assert proc.returncode == 0, proc.stderr
        assert _ssn("-") not in proc.stdout

    def test_two_sheets_that_mask_to_one_name_are_a_refusal(self, tmp_path):
        """Silently keyed by the masked name, one sheet of a return would be
        dropped — the understatement direction AGENTS.md §4 calls a hard
        error."""
        from agents.lib.parse_utils import extract_excel
        book = _workbook(tmp_path / "bank.xlsx", [["Balance", 1234.56]],
                         title=f"Owner {_ssn('-')}")
        wb = openpyxl.load_workbook(book)
        wb.create_sheet(f"Owner {_ssn('.')}")
        wb.save(book)
        with pytest.raises(ValueError, match="mask to the same name"):
            extract_excel(str(book), workspace=str(tmp_path))


def _csv_at(path: Path, rows) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return path


class TestEveryCliReadIsConfinedToAWorkspace:
    """V6. The library functions have taken a `workspace=` since they were
    written and refuse a path — or a symlink target — outside it. The CLI passed
    None to every one of them, so the confinement was OFF for the only caller
    that matters, and "run only parse_utils" was permission to read the whole
    disk one suffix at a time.

    This is the half of the parse boundary that no tool grant can supply. A
    worker restricted to one command is only as confined as that command is: an
    allowed reader with an unrestricted path argument is an arbitrary reader.
    """

    def test_a_document_inside_the_workspace_is_read(self, tmp_path):
        doc = tmp_path / "documents"
        doc.mkdir()
        _csv_at(doc / "bank.csv", [["Date", "Amount"], ["2026-01-02", "10.00"]])
        proc = _run_cli("csv", str(doc / "bank.csv"), workspace=str(tmp_path))
        assert proc.returncode == 0 and "2026-01-02" in proc.stdout

    @pytest.mark.parametrize("command,suffix", [
        ("csv", ".csv"), ("extract", ".pdf"), ("tables", ".pdf"),
        ("excel", ".xlsx"), ("classify", ".pdf"),
    ])
    def test_a_path_outside_the_workspace_is_refused(self, tmp_path, command,
                                                     suffix):
        """One case per route, because the CLI passed the workspace through five
        separate call sites and four of them being right is a hole."""
        workspace = tmp_path / "workspace"
        (workspace / "documents").mkdir(parents=True)
        outside = tmp_path / "other-return"
        outside.mkdir()
        target = outside / f"statement{suffix}"
        target.write_bytes(b"%PDF-1.4 not really\n")
        proc = _run_cli(command, str(target), workspace=str(workspace))
        assert proc.returncode == 1
        assert "outside workspace" in proc.stderr
        assert proc.stdout.strip() == "", "a refused read printed something"

    def test_scan_is_confined_too(self, tmp_path):
        """`scan` takes a DIRECTORY, so `_validate_file_path` never sees it —
        and an unconfined `scan /` is a map of every tax document on the
        machine, which is the reconnaissance step before the reads."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "other-return"
        outside.mkdir()
        _csv_at(outside / "bank.csv", [["a"], ["1"]])
        proc = _run_cli("scan", str(outside), workspace=str(workspace))
        assert proc.returncode == 1 and "outside workspace" in proc.stderr
        assert "bank.csv" not in proc.stdout

    def test_a_symlink_out_of_the_workspace_is_refused(self, tmp_path):
        """The containment is on the RESOLVED path, so a link planted in
        `documents/` cannot be the way out."""
        if sys.platform.startswith("win"):  # pragma: no cover - needs a right
            pytest.skip("symlinks")
        workspace = tmp_path / "workspace"
        documents = workspace / "documents"
        documents.mkdir(parents=True)
        outside = tmp_path / "other-return"
        outside.mkdir()
        _csv_at(outside / "real.csv", [["secret"], ["1"]])
        (documents / "innocent.csv").symlink_to(outside / "real.csv")
        proc = _run_cli("csv", str(documents / "innocent.csv"),
                        workspace=str(workspace))
        assert proc.returncode == 1
        assert "outside workspace" in proc.stderr and "secret" not in proc.stdout

    def test_the_refusal_names_a_redacted_path(self, tmp_path):
        """The message names the path, and the path is redacted like every other
        printed path here — a workspace named after a taxpayer discloses one."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "000-00-0000"
        outside.mkdir()
        _csv_at(outside / "bank.csv", [["a"], ["1"]])
        proc = _run_cli("csv", str(outside / "bank.csv"), workspace=str(workspace))
        assert proc.returncode == 1
        assert "000-00-0000" not in proc.stderr and "REDACTED" in proc.stderr

    def test_the_workspace_flag_is_positional_so_a_later_one_cannot_widen_it(
            self, tmp_path):
        """`csv doc.csv --workspace /` READ THE FILE. The confinement was still
        on and applied to `/`, which is the off switch this class exists to say
        does not exist. Accepting the flag only in first position is what makes
        that unwritable; a trailing one is refused rather than ignored, so a
        caller who meant it is told."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "other-return"
        outside.mkdir()
        _csv_at(outside / "bank.csv", [["secret"], ["1"]])
        proc = subprocess.run(
            [sys.executable, "agents/lib/parse_utils.py", "csv",
             str(outside / "bank.csv"), "--workspace", "/"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent))
        assert proc.returncode == 1
        assert "must come first" in proc.stderr
        assert "secret" not in proc.stdout

    def test_a_second_workspace_flag_does_not_win(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "other-return"
        outside.mkdir()
        _csv_at(outside / "bank.csv", [["secret"], ["1"]])
        proc = subprocess.run(
            [sys.executable, "agents/lib/parse_utils.py",
             "--workspace", str(workspace), "--workspace", "/",
             "csv", str(outside / "bank.csv")],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent))
        assert proc.returncode == 1 and "secret" not in proc.stdout

    def test_a_sibling_directory_sharing_a_prefix_is_still_outside(self, tmp_path):
        """`/a/b` and `/a/bc` share a string prefix and share no directory. A
        containment check written on the string rather than the components
        admits the second."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        sibling = tmp_path / "wsc"
        sibling.mkdir()
        _csv_at(sibling / "bank.csv", [["secret"], ["1"]])
        proc = _run_cli("csv", str(sibling / "bank.csv"), workspace=str(workspace))
        assert proc.returncode == 1 and "secret" not in proc.stdout

    def test_the_default_workspace_is_where_the_command_was_run(self):
        """The production path: every skill invokes this from the workspace root
        and passes a relative path, with no flag at all. That default is what
        confines a real run, so it is what the escape below is measured against
        — a test suite that only ever passed `--workspace` would leave it
        covered by nothing."""
        import subprocess as sp
        cli = str(Path(__file__).resolve().parent.parent
                  / "agents" / "lib" / "parse_utils.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "documents").mkdir()
            _csv_at(root / "documents" / "in.csv", [["a"], ["1"]])
            outside = root.parent / f"{root.name}-sibling"
            outside.mkdir()
            _csv_at(outside / "out.csv", [["secret"], ["1"]])
            try:
                inside = sp.run([sys.executable, cli, "csv", "documents/in.csv"],
                                capture_output=True, text=True, cwd=str(root))
                escape = sp.run([sys.executable, cli, "csv",
                                 str(outside / "out.csv")],
                                capture_output=True, text=True, cwd=str(root))
            finally:
                shutil.rmtree(outside, ignore_errors=True)
        assert inside.returncode == 0, inside.stderr
        assert escape.returncode == 1 and "secret" not in escape.stdout

class TestTheCliNeverPrintsAnUnredactedFailure:
    """Only three exception classes were caught; an IndexError, a RuntimeError
    or a parser exception quoting document bytes propagated as a full
    traceback — into the same transcript the masking exists to keep clean."""

    def test_a_page_out_of_range_is_one_redacted_line_not_a_traceback(self, tmp_path):
        pdf = _build_pdf(
            tmp_path / f"W2_{_ssn('-')}.pdf")
        proc = _run_cli("extract", str(pdf), "7")
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr
        assert "IndexError" in proc.stderr
        assert _ssn("-") not in proc.stderr and _ssn("-") not in proc.stdout

    def test_extract_prints_masked_text(self, tmp_path):
        pdf = _build_pdf(tmp_path / "w2.pdf")
        proc = _run_cli("extract", str(pdf))
        assert proc.returncode == 0, proc.stderr
        assert _ssn("-") not in proc.stdout and MASKED in proc.stdout
        assert "Acme Widgets LLC" in proc.stdout

    def test_csv_and_excel_have_a_masked_cli_route(self, tmp_path):
        """A workbook or CSV had no CLI route at all: `extract` refuses anything
        but a PDF, so the Excel/CSV group could only be read by the worker's
        own file-read tool or an ad-hoc interpreter call — neither masks."""
        doc = tmp_path / "export.csv"
        with doc.open("w", newline="") as f:
            csv.writer(f).writerows([["SSN", "Routing"], [_ssn("-"), ROUTING]])
        proc = _run_cli("csv", str(doc))
        assert proc.returncode == 0, proc.stderr
        assert _ssn("-") not in proc.stdout
        assert MASKED in proc.stdout and ROUTING in proc.stdout

        book = _workbook(tmp_path / "bank.xlsx", [[f"SSN {_ssn('')}", 1234.56]],
                         title="Accounts")
        proc = _run_cli("excel", str(book), "Accounts")
        assert proc.returncode == 0, proc.stderr
        assert _ssn("") not in proc.stdout
        assert MASKED in proc.stdout and "1234.56" in proc.stdout
        assert "Accounts" in proc.stdout


class TestEveryStringTheModuleReturnsIsMasked:
    """The invariant is at the MODULE boundary, not the CLI: every string a
    public route returns is masked, so the CLI is a plain printer and a
    library importer (`crypto_importer`) gets the same guarantee."""

    @pytest.mark.parametrize("sep", ["-", ".", "", " "])   # never "/" in a name
    def test_scan_refuses_a_filename_that_carries_an_identifier(
            self, tmp_path, sep):
        """A file named after an SSN discloses it — the PII gate treats a
        pathname as content for the same reason, in every spelling and with no
        label to disambiguate the bare one. Masking the name kept the digits
        out and returned a `path` that opens nothing, and `scan` is the
        routing table /tax-parse dispatches from: the next step was a
        FileNotFoundError on a document sitting right there. The name is what
        has to change, so the file is refused and the remedy is stated."""
        docs = tmp_path / "documents"
        docs.mkdir()
        _build_pdf(docs / f"W2_{_ssn(sep)}.pdf")
        assert scan_workspace(str(docs)) == []
        proc = _run_cli("scan", str(docs))
        assert proc.returncode == 0, proc.stderr
        assert _ssn(sep) not in proc.stdout and _ssn(sep) not in proc.stderr
        assert "Rename the file" in proc.stderr

    def test_a_comb_spelled_identifier_names_a_file_too(self):
        """`names_an_identifier` stays wider than `mask_ssn`."""
        from agents.lib.secure_fs import names_an_identifier
        assert names_an_identifier(f"W2 {' '.join('00000' + LAST4)}.pdf")
        assert not names_an_identifier("W2 1 2 3 4.pdf")

    def test_a_scanned_path_is_one_the_extractor_can_open(self, tmp_path):
        """`scan` hands the next step a path, not a display name."""
        docs = tmp_path / "documents"
        docs.mkdir()
        _build_pdf(docs / "W2_Acme.pdf")
        (doc,) = scan_workspace(str(docs))
        assert Path(doc["path"]).is_file() and doc["filename"] == "W2_Acme.pdf"

    def test_json_output_keeps_non_ascii_readable(self, tmp_path):
        """A Russian bank's export has Cyrillic column labels; `\\u0414...`
        escapes make telling the interest column from the balance column a
        decode-by-hand exercise per cell."""
        doc = tmp_path / "export.csv"
        with doc.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([["Дата", "Проценты"], ["2026-01-31", "12.34"]])
        proc = _run_cli("csv", str(doc))
        assert proc.returncode == 0, proc.stderr
        assert "Проценты" in proc.stdout and "\\u041f" not in proc.stdout


class TestAnUnknownSheetIsARefusal:
    """`{}` with exit 0 was the answer for a misspelled, wrong-case or
    trailing-space sheet name — the one output that reads as "nothing in this
    workbook", for the worker whose only workbook reader this now is."""

    def _book(self, tmp_path):
        return _workbook(tmp_path / "bank.xlsx", [["Balance", 1234.56]],
                         title="Accounts")

    @pytest.mark.parametrize("name", ["Acounts", "accounts", "Accounts "])
    def test_the_library_names_the_sheets_that_exist(self, tmp_path, name):
        from agents.lib.parse_utils import extract_excel
        book = self._book(tmp_path)
        with pytest.raises(ValueError, match="No sheet named") as exc:
            extract_excel(str(book), sheet_names=[name], workspace=str(tmp_path))
        assert "Accounts" in str(exc.value)

    def test_the_cli_exits_1_with_the_reason(self, tmp_path):
        book = self._book(tmp_path)
        proc = _run_cli("excel", str(book), "Acounts")
        assert proc.returncode == 1
        assert proc.stdout == "" and "No sheet named" in proc.stderr

    def test_the_template_quotes_the_sheet_name(self):
        """A sheet name with a space is split by the shell into a wrong name
        plus a dropped argument unless it is quoted like the path."""
        flat = " ".join(skill_text("tax-parse").split())
        assert "excel '{filepath}' '{sheet}'" in flat


class TestTheSkillsReadDocumentsOnlyThroughTheMaskingCli:
    """The CLI is not the only route into the transcript: a worker's own
    file-read tool, a bare pdfplumber call, or an unnamed extractor in a step
    all bypass the masker. The skills close those by instruction (V6 closes
    them by enforcement)."""



    @pytest.mark.parametrize("name", PARSING_SKILLS)
    def test_the_worker_rules_name_the_cli_as_the_only_reader(self, name):
        # A sentence ends at `.`/`!`/`?` followed by whitespace and a
        # non-lowercase character: `parse_utils.py extract` must not split.
        sentences = [s.lower() for s in
                     re.split(r"(?<=[.!?])\s+(?=[^a-z])", skill_text(name))]
        assert any("file-read tool" in s and "parse_utils.py" in s
                   for s in sentences), (
            f"{name}: no single sentence forbids the worker's own file-read tool "
            "and names parse_utils.py as the only route — a direct read puts the "
            "full SSN in the transcript before any instruction to mask it")

    @pytest.mark.parametrize("name, read_scope", [
        ("tax-parse", "outside documents/, except `tax_law_{year}.json`"),
        ("tax-verify", "other than the return at '{pdf_path}'"),
    ])
    def test_the_read_scope_and_the_write_scope_are_stated_separately(
            self, name, read_scope):
        """E2: rule 4 forbade reading outside `summaries/` while step 1 ran the
        extractor on a file under `documents/`. A self-contradictory rule is
        one the agent learns to reason past. tax-verify's one input is the
        software return at whatever path the user gave, so its read scope is
        that file, not a directory."""
        text = skill_text(name)
        assert read_scope in text, (
            f"{name}: the worker template's file rule must state the read "
            "scope and the write scope separately")
        assert "outside the summaries/ directory" not in text, (
            f"{name}: the contradictory read-scope wording is still present")

    def test_the_foreign_document_step_reads_a_file_the_rule_allows(self):
        """Step 4.3 has the worker read the exchange rate from
        `tax_law_{year}.json`, so rule 4 and the security bullet must allow
        that one read — a rule the step contradicts is one the agent learns
        to reason past, and the next file it reaches for is the PDF."""
        text = skill_text("tax-parse")
        assert "exchange_rates.{CURRENCY}" in text
        assert text.count("except `tax_law_{year}.json`") >= 2

    def test_tax_verify_lets_the_worker_re_read_its_own_output(self):
        """The template accumulates every page into one file; a rule that
        forbids re-opening it forbids appending to it."""
        text = " ".join(skill_text("tax-verify").split())
        assert ("other than the return at '{pdf_path}' and your own output "
                "under summaries/") in text
        assert "secure_fs 'summaries/software_return_extraction.md'" in text

    @pytest.mark.parametrize("heading, next_heading", [
        ("### 4. Handle Foreign Documents", "### 5."),
        ("### 6.7. Second-read verification", "### 6.8."),
    ])
    def test_the_steps_that_re_read_a_document_name_the_extractor(
            self, heading, next_heading):
        text = skill_text("tax-parse")
        start = text.index(heading)
        section = text[start:text.index(next_heading, start)]
        assert "parse_utils.py" in section, (
            f"{heading!r} names no extractor — an agent free to choose one "
            "chooses its own file-read tool, which does not mask")
        assert "pdfplumber (it handles" not in section, (
            "Step 4 still tells the worker to call pdfplumber directly")


class TestTheParserBudgets:
    """V11: the 100 MB on-disk cap bounds the FILE, and nothing else. A `.xlsx`
    is a ZIP whose XML can be orders of magnitude larger; the extracted text,
    the page count and the time spent are all unbounded by the file's size.
    Source documents are untrusted input (AGENTS.md §1), so each budget refuses
    rather than truncating — a partial extraction that looks complete is what
    the tie-out gate structurally cannot see."""

    def _rows(self, n, width=4):
        return [[f"cell {r}-{c}" for c in range(width)] for r in range(n)]

    def test_a_workbook_that_expands_too_far_is_refused_before_it_opens(
            self, tmp_path, monkeypatch):
        """The ordinary zip bomb: small on disk, enormous decompressed. Refused
        BEFORE `load_workbook`, which decompresses the whole archive into memory
        before this module sees a cell."""
        from agents.lib import parse_utils
        book = _workbook(tmp_path / "bomb.xlsx", self._rows(200), title="Sheet1")
        monkeypatch.setattr(parse_utils, "MAX_UNCOMPRESSED_BYTES", 1024)
        with pytest.raises(ValueError, match="Refusing to decompress"):
            parse_utils.extract_excel(str(book), workspace=str(tmp_path))

    def test_a_workbook_with_an_absurd_ratio_is_refused(self, tmp_path, monkeypatch):
        """Both halves are checked: a bomb is SMALL on disk, which is the trick,
        so the absolute size alone would let a 100 KB file through."""
        from agents.lib import parse_utils
        book = _workbook(tmp_path / "ratio.xlsx",
                         [["A" * 200] * 4 for _ in range(400)], title="Sheet1")
        monkeypatch.setattr(parse_utils, "MAX_COMPRESSION_RATIO", 2)
        with pytest.raises(ValueError, match="expands"):
            parse_utils.extract_excel(str(book), workspace=str(tmp_path))

    def test_a_file_that_is_not_a_readable_archive_is_one_refusal(self, tmp_path):
        from agents.lib import parse_utils
        fake = tmp_path / "not.xlsx"
        fake.write_bytes(b"not a zip at all")
        with pytest.raises(ValueError, match="not a readable workbook"):
            parse_utils.extract_excel(str(fake), workspace=str(tmp_path))

    @pytest.mark.parametrize("route", ["csv", "excel"])
    def test_too_much_extracted_text_is_refused_not_truncated(
            self, tmp_path, monkeypatch, route):
        from agents.lib import parse_utils
        monkeypatch.setattr(parse_utils, "MAX_EXTRACTED_CHARS", 100)
        if route == "csv":
            doc = tmp_path / "big.csv"
            with doc.open("w", newline="") as f:
                csv.writer(f).writerows(self._rows(100))
            call = lambda: parse_utils.extract_csv(str(doc), workspace=str(tmp_path))
        else:
            doc = _workbook(tmp_path / "big.xlsx", self._rows(100), title="Sheet1")
            call = lambda: parse_utils.extract_excel(str(doc), workspace=str(tmp_path))
        with pytest.raises(ValueError, match="more than"):
            call()

    def test_the_page_budget_bounds_the_CALL_not_the_file(self, tmp_path, monkeypatch):
        """/tax-parse splits a long document into per-page sub-tasks, so a cap
        on the FILE would refuse every slice of a document the workflow is
        already handling correctly."""
        from agents.lib import parse_utils
        pdf = _build_pdf(tmp_path / "w2.pdf")
        monkeypatch.setattr(parse_utils, "MAX_PAGES", 0)
        with pytest.raises(ValueError, match="over the 0-page budget"):
            parse_utils.extract_pdf_text(str(pdf), workspace=str(tmp_path))
        with pytest.raises(ValueError, match="over the 0-page budget"):
            parse_utils.extract_tables(str(pdf), workspace=str(tmp_path))
        monkeypatch.setattr(parse_utils, "MAX_PAGES", 1)
        assert parse_utils.extract_pdf_text(str(pdf), pages=[0],
                                            workspace=str(tmp_path))

    def test_a_parse_that_runs_too_long_is_refused(self, tmp_path, monkeypatch):
        """Checked BETWEEN rows: the point is to stop a document that is still
        expanding, and a check that only runs at the end has already paid the
        cost it exists to avoid."""
        from agents.lib import parse_utils
        doc = tmp_path / "slow.csv"
        with doc.open("w", newline="") as f:
            csv.writer(f).writerows(self._rows(50))
        clock = iter([0.0] + [1000.0] * 200)
        monkeypatch.setattr(parse_utils.time, "monotonic", lambda: next(clock))
        with pytest.raises(ValueError, match="still running"):
            parse_utils.extract_csv(str(doc), workspace=str(tmp_path))

    def _text_only_pdf(self, path):
        """A PDF with prose and NO table, so `extract_tables` walks its pages
        and produces nothing at all."""
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate
        styles = getSampleStyleSheet()
        SimpleDocTemplate(str(path), pagesize=letter).build(
            [Paragraph("no tables here", styles["Normal"])])
        return path

    @pytest.mark.parametrize("route", ["excel", "tables"])
    def test_the_clock_is_checked_where_no_character_is_produced(
            self, tmp_path, monkeypatch, route):
        """A numeric-only worksheet and a page with no tables produce no
        CHARACTERS, and a deadline checked only when a string arrives is a
        deadline those documents never reach.

        `extract_pdf_text` is deliberately not in this list: it charges the
        page's text unconditionally — `or ""` — so it ticks on an empty page
        too, and a parameter that passes either way is not a test.
        """
        from agents.lib import parse_utils
        clock = iter([0.0] + [1000.0] * 500)
        monkeypatch.setattr(parse_utils.time, "monotonic", lambda: next(clock))
        if route == "excel":
            doc = _workbook(tmp_path / "numbers.xlsx",
                            [[1, 2, 3, 4] for _ in range(30)], title="Sheet1")
            call = lambda: parse_utils.extract_excel(str(doc), workspace=str(tmp_path))  # noqa: E731
        else:
            doc = self._text_only_pdf(tmp_path / "prose.pdf")
            call = lambda: parse_utils.extract_tables(str(doc), workspace=str(tmp_path))  # noqa: E731
        with pytest.raises(ValueError, match="still running"):
            call()

    def test_a_numeric_cell_costs_budget_too(self, tmp_path, monkeypatch):
        """A numeric or empty cell costs no CHARACTERS and still occupies
        memory and prints in the CLI's JSON, so charging strings alone let a
        numeric sheet through a budget it was many times over."""
        from agents.lib import parse_utils
        book = _workbook(tmp_path / "numbers.xlsx",
                         [[1, 2, 3, 4] for _ in range(100)], title="Sheet1")
        monkeypatch.setattr(parse_utils, "MAX_EXTRACTED_CHARS", 100)
        with pytest.raises(ValueError, match="more than"):
            parse_utils.extract_excel(str(book), workspace=str(tmp_path))

    def test_the_deadline_is_checked_after_the_last_page_too(
            self, tmp_path, monkeypatch):
        """A page that yields no table charges nothing, and the LAST such page
        had no check left after it: the pre-call tick passed, the charge was
        skipped, and the call returned `{}` having run past the budget. The
        clock advances DURING the extraction here, not before it."""
        from agents.lib import parse_utils
        doc = self._text_only_pdf(tmp_path / "prose.pdf")
        ticks = iter([0.0, 0.0, 1000.0, 1000.0, 1000.0, 1000.0])
        monkeypatch.setattr(parse_utils.time, "monotonic", lambda: next(ticks))
        with pytest.raises(ValueError, match="still running"):
            parse_utils.extract_tables(str(doc), workspace=str(tmp_path))

    def test_an_empty_row_costs_its_own_brackets(self, tmp_path, monkeypatch):
        """A blank line is `[]`: it charged nothing while still being retained
        and returned, so a file of nothing but newlines allocated a list per
        line under a budget that never noticed."""
        from agents.lib import parse_utils
        doc = tmp_path / "blank.csv"
        doc.write_text("\n" * 200)
        monkeypatch.setattr(parse_utils, "MAX_EXTRACTED_CHARS", 100)
        with pytest.raises(ValueError, match="more than"):
            parse_utils.extract_csv(str(doc), workspace=str(tmp_path))

    def test_the_csv_deadline_covers_the_masking_pass(self, tmp_path, monkeypatch):
        """Masking is the other substantial pass over the file, and a route
        that ticks and THEN does work has left that work outside its own
        budget."""
        from agents.lib import parse_utils
        doc = tmp_path / "small.csv"
        with doc.open("w", newline="") as f:
            csv.writer(f).writerows(self._rows(3))
        # The clock crosses the deadline exactly WHILE masking runs, so the
        # only check that can catch it is one after that pass.
        state = {"masking": False}
        real_mask = parse_utils.mask_table

        def mask(rows):
            state["masking"] = True
            return real_mask(rows)

        monkeypatch.setattr(parse_utils, "mask_table", mask)
        monkeypatch.setattr(parse_utils.time, "monotonic",
                            lambda: 1000.0 if state["masking"] else 0.0)
        with pytest.raises(ValueError, match="still running"):
            parse_utils.extract_csv(str(doc), workspace=str(tmp_path))

    def test_a_refusal_returns_nothing_at_all(self, tmp_path, monkeypatch):
        """"No partial record is accepted": the caller gets an exception, never
        the rows read before the budget ran out."""
        from agents.lib import parse_utils
        doc = tmp_path / "big.csv"
        with doc.open("w", newline="") as f:
            csv.writer(f).writerows(self._rows(100))
        monkeypatch.setattr(parse_utils, "MAX_EXTRACTED_CHARS", 100)
        try:
            parse_utils.extract_csv(str(doc), workspace=str(tmp_path))
        except ValueError as e:
            assert "cell 0-0" not in str(e)
        else:
            raise AssertionError("the budget did not refuse")

    def test_the_cli_reports_a_budget_refusal_as_one_line(self, tmp_path):
        """The budgets are module constants with no env knob on purpose — a
        budget a caller can raise is one the caller raises — so the refusal
        reached from the CLI is the archive check, on a file that is not a
        readable workbook at all."""
        fake = tmp_path / "not.xlsx"
        fake.write_bytes(b"not a zip at all")
        proc = _run_cli("excel", str(fake))
        assert proc.returncode == 1
        assert proc.stdout == "" and "Traceback" not in proc.stderr
        assert "not a readable workbook" in proc.stderr

