#!/usr/bin/env python3
"""Tests for agents/lib/archive.py — file moves for /tax-archive."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.archive import archive_year, ArchiveError


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
    (workspace / "archive" / "2024").mkdir(parents=True)
    with pytest.raises(ArchiveError, match="already exists"):
        archive_year(2024, root=workspace)
    # Root untouched
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
    """If os.chmod fails after a successful move, the file must still be rolled back."""
    real_chmod = os.chmod
    calls = {"n": 0}

    def flaky_chmod(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated chmod failure")
        return real_chmod(*args, **kwargs)

    monkeypatch.setattr("agents.lib.archive.os.chmod", flaky_chmod)
    with pytest.raises(ArchiveError):
        archive_year(2024, root=workspace)
    # The moved file must be back at root despite the chmod failure
    assert (workspace / "result.json").exists()
