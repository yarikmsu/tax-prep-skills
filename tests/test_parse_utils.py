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
