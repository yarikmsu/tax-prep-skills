#!/usr/bin/env python3
"""Tests for agents/lib/archive.py — file moves for /tax-archive."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agents.lib.archive as archive_module
from agents.lib.archive import archive_year, ArchiveError


@pytest.fixture
def permissive_umask():
    """Pin umask 022 for the duration of a directory-mode assertion.

    Asserting 0700 is VACUOUS under a restrictive umask: `mkdir` asks for 0777,
    and umask 077 already subtracts its way to 0700 with no chmod involved. 022
    is the common default, and the one under which an unset directory mode comes
    out world-readable — which is the condition being defended against.
    """
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


@pytest.fixture
def workspace(tmp_path):
    """Build a synthetic root workspace with TY2024 files."""
    (tmp_path / "result.json").write_text(json.dumps({"meta": {"tax_year": 2024}}))
    (tmp_path / "taxpayer_profile.json").write_text(json.dumps({"tax_year": 2024}))
    (tmp_path / "answers.md").write_text("# Answers\n")
    (tmp_path / "tax_law_2024.json").write_text(json.dumps({"year": 2024}))
    (tmp_path / "summaries").mkdir()
    (tmp_path / "summaries" / "w2.md").write_text("W-2 summary")
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "w2.pdf").write_bytes(b"%PDF-1.4 fake")
    return tmp_path


def test_happy_path_moves_files(workspace):
    archive_year(2024, root=workspace)
    archive = workspace / "archive" / "2024"
    assert (archive / "result.json").exists()
    assert (archive / "taxpayer_profile.json").exists()
    assert (archive / "answers.md").exists()
    assert (archive / "tax_law_2024.json").exists()
    assert (archive / "summaries" / "w2.md").exists()
    assert (archive / "documents" / "w2.pdf").exists()
    # Root left clean
    assert not (workspace / "result.json").exists()
    assert not (workspace / "summaries").exists()


def test_reviews_and_parse_ledger_archived(workspace):
    """C3/A1 artifacts belong to the year: the persisted /tax-review
    workpaper and the parse ledger must ride the year-end cutover."""
    (workspace / "reviews").mkdir()
    (workspace / "reviews" / "2024-review.md").write_text("# Review")
    (workspace / "parse_ledger.json").write_text(json.dumps({"records": []}))
    archive_year(2024, root=workspace)
    archive = workspace / "archive" / "2024"
    assert (archive / "reviews" / "2024-review.md").exists()
    assert (archive / "parse_ledger.json").exists()
    assert not (workspace / "reviews").exists()
    assert not (workspace / "parse_ledger.json").exists()


def test_refuses_if_archive_exists(workspace):
    # A real prior archive (has result.json etc.) still refuses.
    (workspace / "archive" / "2024").mkdir(parents=True)
    (workspace / "archive" / "2024" / "result.json").write_text("{}")
    with pytest.raises(ArchiveError, match="already exists"):
        archive_year(2024, root=workspace)
    # Root untouched
    assert (workspace / "result.json").exists()


def test_merges_alongside_tax_amend_snapshot(workspace):
    """F3: /tax-amend writes archive/{year}/as_filed/ during the season;
    the year-end archive must MERGE the final files in alongside it, not
    refuse."""
    snap = workspace / "archive" / "2024" / "as_filed"
    snap.mkdir(parents=True)
    (snap / "result.json").write_text(json.dumps({"meta": {"tax_year": 2024}}))
    archive_year(2024, root=workspace)
    archive = workspace / "archive" / "2024"
    # Final files moved in; the as-filed snapshot preserved untouched.
    assert (archive / "result.json").exists()
    assert (archive / "as_filed" / "result.json").exists()
    assert not (workspace / "result.json").exists()


def test_refuses_if_archive_has_more_than_snapshot(workspace):
    arch = workspace / "archive" / "2024"
    (arch / "as_filed").mkdir(parents=True)
    (arch / "documents").mkdir()  # a non-snapshot child
    with pytest.raises(ArchiveError, match="more than"):
        archive_year(2024, root=workspace)
    assert (workspace / "result.json").exists()


def test_refuses_if_no_result_json(tmp_path):
    with pytest.raises(ArchiveError, match="No result.json"):
        archive_year(2024, root=tmp_path)


def test_refuses_if_year_mismatch(workspace):
    (workspace / "result.json").write_text(json.dumps({"meta": {"tax_year": 2023}}))
    with pytest.raises(ArchiveError, match="TY2023.*not TY2024"):
        archive_year(2024, root=workspace)
    assert (workspace / "result.json").exists()


def test_partial_file_set_succeeds(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps({"meta": {"tax_year": 2024}}))
    archive_year(2024, root=tmp_path)
    assert (tmp_path / "archive" / "2024" / "result.json").exists()


def test_chmod_600_on_archived_files(workspace):
    archive_year(2024, root=workspace)
    mode = (workspace / "archive" / "2024" / "result.json").stat().st_mode
    assert (mode & 0o777) == 0o600


def test_chmod_700_on_archive_directories(permissive_umask, workspace):
    """600 on the files leaves a world-readable listing of their NAMES, and under
    archive/ the names are the disclosure: `documents/W2_Acme.pdf` says where the
    taxpayer works before anyone opens it. Every directory this function creates or
    relocates is restricted — `archive/`, `archive/{year}/`, and the subtrees moved
    beneath it, which arrive carrying whatever mode they had at the root."""
    archive_year(2024, root=workspace)
    for rel in ("archive", "archive/2024", "archive/2024/documents",
                "archive/2024/summaries"):
        mode = (workspace / rel).stat().st_mode & 0o777
        assert mode == 0o700, f"{rel} is {oct(mode)}, listing its entries to the host"


def test_rejects_non_integer_year(workspace):
    with pytest.raises(ArchiveError, match="4-digit"):
        archive_year("2024; rm -rf /", root=workspace)


def test_rejects_out_of_range_year(workspace):
    with pytest.raises(ArchiveError, match="2000.*2099"):
        archive_year(1999, root=workspace)


def test_year_mismatch_error_names_both_years(workspace):
    (workspace / "result.json").write_text(json.dumps({"meta": {"tax_year": 2023}}))
    with pytest.raises(ArchiveError) as exc_info:
        archive_year(2024, root=workspace)
    assert "2023" in str(exc_info.value)
    assert "2024" in str(exc_info.value)


def test_dry_run_does_not_move(workspace):
    archive_year(2024, root=workspace, dry_run=True)
    assert (workspace / "result.json").exists()
    assert not (workspace / "archive" / "2024").exists()


def test_atomicity_rollback_on_mid_move_failure(workspace, monkeypatch):
    """Inject a failure on the second shutil.move; verify the first already-moved
    file is returned to root and no partial archive remains."""
    import shutil as _shutil
    real_move = _shutil.move
    calls = {"n": 0}

    def flaky_move(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated mid-move failure")
        return real_move(*args, **kwargs)

    monkeypatch.setattr("agents.lib.archive.shutil.move", flaky_move)
    with pytest.raises(ArchiveError, match="rolled back"):
        archive_year(2024, root=workspace)
    # First file was moved then rolled back; second move raised so was never moved
    assert (workspace / "result.json").exists()
    assert (workspace / "taxpayer_profile.json").exists()


def test_atomicity_rollback_on_chmod_failure(workspace, monkeypatch):
    """If os.chmod fails after a successful move, the file must still be rolled back.

    Targets the first chmod on a FILE, not simply the first chmod: the directory
    modes are set before anything moves, so failing on call #1 would exercise the
    nothing-was-moved path and this test would pass while proving nothing about
    rollback."""
    real_chmod = os.chmod
    fired = {"on": None}

    def flaky_chmod(path, *args, **kwargs):
        if fired["on"] is None and Path(path).is_file():
            fired["on"] = Path(path).name
            raise OSError("simulated chmod failure")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr("agents.lib.archive.os.chmod", flaky_chmod)
    with pytest.raises(ArchiveError):
        archive_year(2024, root=workspace)
    assert fired["on"] is not None, "never reached a post-move file chmod"
    # The moved file must be back at root despite the chmod failure
    assert (workspace / "result.json").exists()


def test_scandir_failure_during_nested_walk_rolls_back(workspace, monkeypatch):
    """os.walk() silently swallows a scandir() failure (permission denied, a
    transient NFS/I/O error) unless given onerror — without it, archive_year()
    would report success while the unreadable subtree stays at its pre-move
    mode and any symlink inside it goes unchecked. Simulates a scandir
    failure while walking the moved documents/ subtree and asserts it
    propagates into the same rollback path a move or chmod failure does."""
    real_scandir = os.scandir
    target = str((workspace / "archive" / "2024" / "documents").resolve())

    def flaky_scandir(path="."):
        if str(path) == target:
            raise OSError("simulated scandir failure")
        return real_scandir(path)

    monkeypatch.setattr("agents.lib.archive.os.scandir", flaky_scandir)
    with pytest.raises(ArchiveError, match="rolled back"):
        archive_year(2024, root=workspace)
    assert (workspace / "result.json").exists(), "must roll back on scandir failure"
    assert (workspace / "documents" / "w2.pdf").exists(), "moved subtree must roll back whole"
    assert not (workspace / "archive" / "2024").exists(), "empty archive dir left behind"


def test_refuses_symlinked_top_level_directory(workspace, tmp_path_factory):
    """documents/ replaced with a symlink to an external directory must be
    refused, not archived — shutil.move relocates the link itself, and the
    os.chmod that follows would resolve through it and change permissions on
    whatever the link points at, not on anything inside the workspace."""
    external = tmp_path_factory.mktemp("external_docs")
    (external / "private.txt").write_bytes(b"not the taxpayer's workspace")
    external_mode_before = external.stat().st_mode & 0o777

    import shutil as _shutil
    _shutil.rmtree(workspace / "documents")
    (workspace / "documents").symlink_to(external, target_is_directory=True)

    with pytest.raises(ArchiveError, match="symlink"):
        archive_year(2024, root=workspace)

    assert external.stat().st_mode & 0o777 == external_mode_before, \
        "chmod followed the symlink and mutated a directory outside the workspace"
    assert (workspace / "result.json").exists(), "nothing may move on refusal"
    assert not (workspace / "archive" / "2024").exists(), "empty archive dir left behind"


def test_refuses_dangling_symlinked_top_level_directory(workspace):
    """documents/ replaced with a symlink to a target that does NOT exist —
    e.g. a temporarily unmounted external volume — must be refused too, not
    silently skipped. Path.exists() follows the link and returns False for a
    dangling symlink, so a check ordered exists-then-is_symlink would never
    reach the refusal at all: the entry would be reported merely "not
    present", success returned, and the symlink left sitting at the
    workspace root — reconnecting the target later would feed those old
    files into next year's workflow instead of this year's archive."""
    import shutil as _shutil
    _shutil.rmtree(workspace / "documents")
    (workspace / "documents").symlink_to(workspace / "does-not-exist",
                                          target_is_directory=True)

    with pytest.raises(ArchiveError, match="symlink"):
        archive_year(2024, root=workspace)

    assert (workspace / "documents").is_symlink(), \
        "the dangling symlink must be refused, not silently treated as absent"
    assert (workspace / "result.json").exists(), "nothing may move on refusal"
    assert not (workspace / "archive" / "2024").exists(), "empty archive dir left behind"


def test_refuses_dangling_symlinked_top_level_file(workspace):
    """Same guarantee for a dangling symlinked FILE (e.g. answers.md pointing
    at an unmounted volume) — a dangling target must not read as "not
    present" and slip past the refusal."""
    (workspace / "answers.md").unlink()
    (workspace / "answers.md").symlink_to(workspace / "does-not-exist")

    with pytest.raises(ArchiveError, match="symlink"):
        archive_year(2024, root=workspace)

    assert (workspace / "answers.md").is_symlink(), \
        "the dangling symlink must be refused, not silently treated as absent"
    assert (workspace / "result.json").exists(), "nothing may move on refusal"
    assert not (workspace / "archive" / "2024").exists(), "empty archive dir left behind"


def test_refuses_symlinked_top_level_file(workspace, tmp_path_factory):
    """result.json replaced with a symlink to an external file must be
    refused for the same reason as a symlinked directory."""
    external_dir = tmp_path_factory.mktemp("external_result")
    external = external_dir / "result.json"
    external.write_text(json.dumps({"meta": {"tax_year": 2024}}))
    external_mode_before = external.stat().st_mode & 0o777

    (workspace / "result.json").unlink()
    (workspace / "result.json").symlink_to(external)

    with pytest.raises(ArchiveError, match="symlink"):
        archive_year(2024, root=workspace)

    assert external.stat().st_mode & 0o777 == external_mode_before, \
        "chmod followed the symlink and mutated a file outside the workspace"


def test_refuses_nested_symlinked_directory(workspace, tmp_path_factory):
    """A symlink buried inside a moved subtree (not the top-level entry
    itself) must refuse the same way — the per-file/per-dir chmod walk over
    the moved directory would otherwise resolve through it and mutate
    permissions on whatever it points at."""
    external = tmp_path_factory.mktemp("external_nested_dir")
    (external / "private.txt").write_bytes(b"not the taxpayer's workspace")
    external_mode_before = external.stat().st_mode & 0o777

    (workspace / "documents" / "cloud_sync").symlink_to(external, target_is_directory=True)

    with pytest.raises(ArchiveError, match="symlink"):
        archive_year(2024, root=workspace)

    assert external.stat().st_mode & 0o777 == external_mode_before, \
        "chmod followed the nested symlink and mutated a directory outside the workspace"
    assert (workspace / "result.json").exists(), "nothing may move on refusal"
    assert (workspace / "documents" / "w2.pdf").exists(), "moved subtree must roll back whole"
    assert not (workspace / "archive" / "2024").exists(), "empty archive dir left behind"


def test_refuses_nested_symlinked_file(workspace, tmp_path_factory):
    """Same guarantee for a symlinked FILE nested inside a moved subtree."""
    external_dir = tmp_path_factory.mktemp("external_nested_file")
    external = external_dir / "secret.txt"
    external.write_bytes(b"not the taxpayer's workspace")
    external_mode_before = external.stat().st_mode & 0o777

    (workspace / "documents" / "link.pdf").symlink_to(external)

    with pytest.raises(ArchiveError, match="symlink"):
        archive_year(2024, root=workspace)

    assert external.stat().st_mode & 0o777 == external_mode_before, \
        "chmod followed the nested symlink and mutated a file outside the workspace"
    assert (workspace / "documents" / "w2.pdf").exists(), "moved subtree must roll back whole"


def test_refuses_symlinked_archive_directory(workspace, tmp_path_factory):
    """archive/ itself replaced with a symlink to an external directory must
    be refused before mkdir/chmod ever touch it. mkdir(..., exist_ok=True)
    accepts a symlinked directory silently (it checks is_dir(), which
    resolves through the link), and the chmod on archive_dir.parent that
    follows would then land on the external target instead of the
    workspace, with the moves after it landing taxpayer files there too."""
    external = tmp_path_factory.mktemp("external_archive_root")
    (external / "private.txt").write_bytes(b"not the taxpayer's workspace")
    external_mode_before = external.stat().st_mode & 0o777

    (workspace / "archive").symlink_to(external, target_is_directory=True)

    with pytest.raises(ArchiveError, match="symlink"):
        archive_year(2024, root=workspace)

    assert external.stat().st_mode & 0o777 == external_mode_before, \
        "chmod followed the symlinked archive/ and mutated an external directory"
    assert {p.name for p in external.iterdir()} == {"private.txt"}, \
        "taxpayer files were moved into the external directory through the symlink"
    assert (workspace / "result.json").exists(), "nothing may move on refusal"


def test_refuses_symlinked_archive_year_directory(workspace, tmp_path_factory):
    """archive/{year}/ itself replaced with a symlink to an external, EMPTY
    directory must be refused too. An empty external target would otherwise
    pass the 'no real contents besides as_filed/' exists() check and fall
    through to mkdir/chmod — the case a symlinked archive/ alone doesn't
    exercise, since that check never runs against the real archive_dir."""
    external = tmp_path_factory.mktemp("external_archive_year")
    external_mode_before = external.stat().st_mode & 0o777

    (workspace / "archive").mkdir()
    (workspace / "archive" / "2024").symlink_to(external, target_is_directory=True)

    with pytest.raises(ArchiveError, match="symlink"):
        archive_year(2024, root=workspace)

    assert external.stat().st_mode & 0o777 == external_mode_before, \
        "chmod followed the symlinked archive/{year} and mutated an external directory"
    assert not any(external.iterdir()), \
        "taxpayer files were moved into the external directory through the symlink"
    assert (workspace / "result.json").exists(), "nothing may move on refusal"


def test_refuses_windows_junction_archive_destination(workspace, monkeypatch):
    """archive/ standing in for an NTFS junction must be refused the same
    way a symlink is — Path.is_symlink() returns False for a junction (see
    secure_fs.py's module docstring), which real symlink tests above cannot
    exercise on POSIX. is_reparse_point() is patched at archive.py's own
    imported name (a `from X import Y` binding, decoupled from secure_fs.py's
    own name once imported — unlike the shared `os` module object other
    tests in this file patch through) to simulate one on a real, plain
    directory."""
    real_is_reparse_point = archive_module.is_reparse_point

    def fake(path):
        if Path(path).name == "archive":
            return True
        return real_is_reparse_point(path)

    monkeypatch.setattr("agents.lib.archive.is_reparse_point", fake)
    with pytest.raises(ArchiveError, match="symlink or junction"):
        archive_year(2024, root=workspace)
    assert (workspace / "result.json").exists(), "nothing may move on refusal"


def test_refuses_windows_junction_top_level_directory(workspace, monkeypatch):
    """documents/ standing in for an NTFS junction must be refused the same
    way a symlinked documents/ is (see test_refuses_symlinked_top_level_
    directory above) — simulated the same way as the archive/ destination
    case just above, since a real junction can't be created on POSIX."""
    real_is_reparse_point = archive_module.is_reparse_point

    def fake(path):
        if Path(path).name == "documents":
            return True
        return real_is_reparse_point(path)

    monkeypatch.setattr("agents.lib.archive.is_reparse_point", fake)
    with pytest.raises(ArchiveError, match="symlink or junction"):
        archive_year(2024, root=workspace)
    assert (workspace / "result.json").exists(), "nothing may move on refusal"
    assert not (workspace / "archive" / "2024").exists(), "empty archive dir left behind"


def test_directory_chmod_failure_fails_the_archive_rather_than_half_running(
        workspace, monkeypatch):
    """A directory whose mode cannot be set is a hard failure, reported as
    ArchiveError like every other one, with nothing moved and no half-created
    archive left behind. The alternative — carry on and move the documents in — is
    the disclosure the 700 exists to prevent."""
    real_chmod = os.chmod

    def refuse_dirs(path, *args, **kwargs):
        if Path(path).is_dir():
            raise OSError("simulated chmod failure")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr("agents.lib.archive.os.chmod", refuse_dirs)
    with pytest.raises(ArchiveError, match="rolled back"):
        archive_year(2024, root=workspace)
    assert (workspace / "result.json").exists(), "nothing may move"
    assert not (workspace / "archive" / "2024").exists(), "empty archive dir left behind"


def test_uninspectable_destination_raises_archive_error_not_a_bare_oserror(
        workspace, monkeypatch):
    """is_reparse_point() fails CLOSED — every OSError but FileNotFoundError
    propagates rather than certifying an uninspected path as real. The
    archive/ and archive/{year} destination checks, and the whole dry-run
    loop, sit OUTSIDE archive_year's try block, so without translation that
    OSError escapes as a bare traceback past the `except ArchiveError` in
    __main__ (which prints the 'ERROR: …' line the skill tells the agent to
    read) and in every caller."""
    def raising(path):
        raise PermissionError("simulated access denied")

    monkeypatch.setattr("agents.lib.archive.is_reparse_point", raising)
    with pytest.raises(ArchiveError, match="could not be inspected"):
        archive_year(2024, root=workspace)
    assert (workspace / "result.json").exists(), "nothing may move on refusal"


def test_uninspectable_path_raises_archive_error_in_dry_run(workspace, monkeypatch):
    """The dry-run loop is the other out-of-try site."""
    real_is_reparse_point = archive_module.is_reparse_point
    calls = {"n": 0}

    def fake(path):
        # Let the two destination components through so the failure lands in
        # the dry-run loop below them, not in the destination check above it.
        if Path(path).name in ("archive", "2024"):
            return real_is_reparse_point(path)
        raise PermissionError("simulated access denied")

    monkeypatch.setattr("agents.lib.archive.is_reparse_point", fake)
    with pytest.raises(ArchiveError, match="could not be inspected"):
        archive_year(2024, root=workspace, dry_run=True)


def test_dry_run_refuses_a_nested_symlink_the_real_run_would_refuse(workspace):
    """The preview exists to surface refusals BEFORE anything moves. Checking
    only the ten top-level entries would report 'will move documents/' for a
    tree the real run refuses — the user confirms, and the real run then fails
    partway through after five files have already moved and been rolled back.
    test_refuses_nested_symlinked_directory covers the real run's half."""
    external = workspace.parent / "external_target"
    external.mkdir()
    (workspace / "documents" / "cloud_sync").symlink_to(external)
    with pytest.raises(ArchiveError, match="symlink or junction"):
        archive_year(2024, root=workspace, dry_run=True)


def test_dry_run_still_reports_a_clean_tree(workspace):
    """The walk added above must not turn an ordinary nested directory into a
    refusal — only a reparse point is refused."""
    nested = workspace / "documents" / "2024_scans"
    nested.mkdir()
    (nested / "W2_Acme.pdf").write_text("x")
    report = archive_year(2024, root=workspace, dry_run=True)
    assert report.dry_run
    assert "documents" in report.moved


def test_dry_run_translates_an_unreadable_nested_directory(workspace, monkeypatch):
    """os.walk swallows a scandir failure unless given onerror; with it, the
    re-raised OSError sits outside the real run's try, so it needs the same
    translation the reparse checks got — a preview that silently skipped part
    of the tree is exactly the false 'clean' this walk exists to prevent."""
    nested = workspace / "documents" / "2024_scans"
    nested.mkdir()
    real_scandir = os.scandir

    def failing_scandir(path):
        if Path(path).name == "2024_scans":
            raise PermissionError("simulated access denied")
        return real_scandir(path)

    monkeypatch.setattr("agents.lib.archive.os.scandir", failing_scandir)
    with pytest.raises(ArchiveError, match="Could not inspect"):
        archive_year(2024, root=workspace, dry_run=True)


# --- Every path this module reports is a runtime path -------------------------

class TestRefusalsRedactTheNamesTheyReport:
    """A document's FILENAME is the disclosure AGENTS.md §1 restricts
    documents/ and archive/{year}/ to prevent, and it is untrusted input
    besides — whatever the taxpayer saved the file as, up to and including an
    SSN, which §1 forbids emitting under any circumstance. The /tax-archive CLI
    prints these messages straight to the terminal.

    Area number 000 is never issued, so the SSN below is synthetic and
    allowlisted; it is the SHAPE that matters.
    """

    SSN = "000-00-0000"

    def test_a_refused_entry_name_is_redacted(self, workspace, tmp_path_factory):
        external = tmp_path_factory.mktemp("external_named")
        (workspace / "documents" / f"W2_{self.SSN}.pdf").symlink_to(
            external / "x.pdf")
        with pytest.raises(ArchiveError) as excinfo:
            archive_year(2024, root=workspace)
        assert self.SSN not in str(excinfo.value)
        assert "[REDACTED]" in str(excinfo.value)

    def test_a_refused_entry_name_is_redacted_in_the_preview_too(
            self, workspace, tmp_path_factory):
        """The dry run walks the same subtrees and raises the same refusal —
        and it is the run a user is most likely to read on screen."""
        external = tmp_path_factory.mktemp("external_named_dry")
        (workspace / "documents" / f"1099_{self.SSN}.pdf").symlink_to(
            external / "x.pdf")
        with pytest.raises(ArchiveError) as excinfo:
            archive_year(2024, root=workspace, dry_run=True)
        assert self.SSN not in str(excinfo.value)

    def test_a_refused_destination_component_is_redacted(
            self, tmp_path, tmp_path_factory):
        """`archive` and `archive/{year}` are fixed names, but the message
        prints the FULL path — so the disclosure rides in on the workspace
        directory the taxpayer chose, which is as likely to be named after them
        as any document is."""
        ws = tmp_path / f"Taxes_{self.SSN}"
        ws.mkdir()
        external = tmp_path_factory.mktemp("external_dest")
        (ws / "archive").symlink_to(external, target_is_directory=True)
        with pytest.raises(ArchiveError) as excinfo:
            archive_year(2024, root=ws)
        assert "symlink or junction" in str(excinfo.value)
        assert self.SSN not in str(excinfo.value)
        assert "[REDACTED]" in str(excinfo.value)

    def test_an_uninspectable_path_reports_a_redacted_message(
            self, workspace, monkeypatch):
        """os.lstat's own error text carries the RAW path, so redacting only
        the interpolated Path would leave it disclosed by the half of the
        message that came from the OS."""
        def raising(path):
            raise PermissionError(13, "Permission denied", f"documents/W2_{self.SSN}.pdf")

        monkeypatch.setattr("agents.lib.archive.is_reparse_point", raising)
        with pytest.raises(ArchiveError) as excinfo:
            archive_year(2024, root=workspace)
        assert self.SSN not in str(excinfo.value)
        assert "[REDACTED]" in str(excinfo.value)

    def test_a_failed_move_reports_a_redacted_message(self, workspace, monkeypatch):
        """shutil.move names BOTH endpoints in its own error text, and every
        one of them is a document path."""
        def raising(src, dst):
            raise OSError(
                f"[Errno 28] No space left: 'documents/W2_{self.SSN}.pdf'")

        monkeypatch.setattr("agents.lib.archive.shutil.move", raising)
        with pytest.raises(ArchiveError) as excinfo:
            archive_year(2024, root=workspace)
        assert self.SSN not in str(excinfo.value)
        assert "rolled back" in str(excinfo.value)

    def test_an_ordinary_name_still_prints(self, workspace, tmp_path_factory):
        """The counterweight: a refusal that cannot say WHICH entry it refused
        is not actionable, and the user cannot fix what it will not name."""
        external = tmp_path_factory.mktemp("external_ordinary")
        (workspace / "documents" / "cloud_sync").symlink_to(
            external, target_is_directory=True)
        with pytest.raises(ArchiveError) as excinfo:
            archive_year(2024, root=workspace)
        assert "cloud_sync" in str(excinfo.value)


# --- The retained /tax-amend snapshot ---------------------------------------


class TestTheRetainedSnapshotIsHardenedToo:
    """`archive/{year}/as_filed/` is the one child `archive_year` tolerates
    rather than moves — and nothing was reaching it.

    The two `secure_path` calls on the destination stop at `archive/{year}`
    itself, and the per-move walk covers only what THIS run moved, so a
    snapshot already in place kept whatever modes `/tax-amend` left it with:
    0755 directories listing a taxpayer's as-filed document NAMES under a
    normal umask, and on Windows a snapshot written before the ACL hardening
    whose "chmod" only ever flipped the read-only DOS attribute. `archive_year`
    then reported success over it, which is the shape AGENTS.md §1 forbids —
    an owner-only file inside a listable directory still discloses its name.
    """

    def _snapshot(self, workspace):
        snap = workspace / "archive" / "2024" / "as_filed"
        (snap / "documents").mkdir(parents=True)
        (snap / "result.json").write_text(json.dumps({"meta": {"tax_year": 2024}}))
        (snap / "documents" / "w2.pdf").write_bytes(b"%PDF-1.4 fake")
        # The modes an older run, or a Windows run predating the ACL work,
        # leaves behind. Set explicitly so the assertion cannot pass on a
        # umask that happened to be restrictive.
        os.chmod(snap, 0o755)
        os.chmod(snap / "documents", 0o755)
        os.chmod(snap / "result.json", 0o644)
        os.chmod(snap / "documents" / "w2.pdf", 0o644)
        return snap

    def test_an_existing_snapshot_is_secured_top_to_bottom(
            self, workspace, permissive_umask):
        snap = self._snapshot(workspace)
        archive_year(2024, root=workspace)
        for d in (snap, snap / "documents"):
            assert oct(os.stat(d).st_mode & 0o777) == "0o700", (
                f"{d.name}/ still lists its contents to every local account")
        for f in (snap / "result.json", snap / "documents" / "w2.pdf"):
            assert oct(os.stat(f).st_mode & 0o777) == "0o600", f

    def test_the_snapshot_survives_the_hardening(self, workspace):
        """Securing it must not disturb it — the merge contract from
        test_merges_alongside_tax_amend_snapshot still holds."""
        snap = self._snapshot(workspace)
        archive_year(2024, root=workspace)
        assert (snap / "result.json").exists()
        assert (snap / "documents" / "w2.pdf").read_bytes() == b"%PDF-1.4 fake"
        assert (workspace / "archive" / "2024" / "result.json").exists()

    def test_a_link_inside_the_snapshot_is_refused_and_rolls_back(
            self, workspace, tmp_path_factory):
        """A chmod resolves THROUGH a link, so a link inside the snapshot
        would re-permission whatever it points at — the same failure the moved
        subtrees are walked for, one directory over."""
        external = tmp_path_factory.mktemp("external_snapshot")
        (external / "secret.txt").write_text("x")
        os.chmod(external, 0o755)
        snap = self._snapshot(workspace)
        (snap / "documents" / "cloud_sync").symlink_to(
            external, target_is_directory=True)

        with pytest.raises(ArchiveError, match="symlink or junction"):
            archive_year(2024, root=workspace)
        assert oct(os.stat(external).st_mode & 0o777) == "0o755", \
            "the refusal came too late — an external directory was re-permissioned"
        assert (workspace / "result.json").exists(), "the run did not roll back"

    def test_a_symlinked_snapshot_is_refused(self, workspace, tmp_path_factory):
        """`as_filed` itself. The guard that tolerates it matches on the NAME,
        so a link wearing that name was tolerated and then chmod'd through."""
        external = tmp_path_factory.mktemp("external_as_filed")
        os.chmod(external, 0o755)
        (workspace / "archive" / "2024").mkdir(parents=True)
        (workspace / "archive" / "2024" / "as_filed").symlink_to(
            external, target_is_directory=True)

        with pytest.raises(ArchiveError, match="symlink or junction"):
            archive_year(2024, root=workspace)
        assert oct(os.stat(external).st_mode & 0o777) == "0o755"
        assert (workspace / "result.json").exists()

    def test_the_dry_run_surfaces_the_same_refusal(
            self, workspace, tmp_path_factory):
        """A preview that skipped the snapshot would report a clean archive
        for a tree the real run stops on — the user confirms a preview that
        then fails partway through, after files have moved and rolled back."""
        external = tmp_path_factory.mktemp("external_preview")
        snap = self._snapshot(workspace)
        (snap / "documents" / "cloud_sync").symlink_to(
            external, target_is_directory=True)

        with pytest.raises(ArchiveError, match="symlink or junction"):
            archive_year(2024, root=workspace, dry_run=True)

    def test_the_dry_run_still_previews_a_clean_snapshot(self, workspace):
        """The counterweight: previewing the snapshot must not make an
        ordinary merge look like a refusal."""
        self._snapshot(workspace)
        report = archive_year(2024, root=workspace, dry_run=True)
        assert "result.json" in report.moved
        assert (workspace / "result.json").exists(), "a dry run moved something"
