#!/usr/bin/env python3
"""Tests for agents/lib/rollover.py — diff/merge engine for /tax-rollover."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.rollover import (
    FIELD_MAP,
    Change,
    PriorReturn,
    RolloverError,
    load_prior,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rollover"


def test_field_map_entries_have_required_keys():
    for key, entry in FIELD_MAP.items():
        assert "category" in entry, f"FIELD_MAP[{key!r}] missing 'category'"
        assert "target_file" in entry, f"FIELD_MAP[{key!r}] missing 'target_file'"
        assert "target_path" in entry, f"FIELD_MAP[{key!r}] missing 'target_path'"


def test_field_map_categories_are_valid():
    valid = {"carryforwards", "identity", "dependents", "expected_sources"}
    for key, entry in FIELD_MAP.items():
        assert entry["category"] in valid, f"FIELD_MAP[{key!r}] has invalid category"


def test_field_map_no_duplicate_target_paths():
    paths = [(e["target_file"], e["target_path"]) for e in FIELD_MAP.values()]
    assert len(paths) == len(set(paths)), "FIELD_MAP has duplicate target paths"


def test_load_prior_reads_clean_fixture():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    assert isinstance(prior, PriorReturn)
    assert prior.year == 2024
    assert prior.result["meta"]["tax_year"] == 2024
    assert prior.profile["filing_status"] == "MFJ"


def test_load_prior_refuses_year_mismatch():
    with pytest.raises(RolloverError, match="TY2024.*not TY2025"):
        load_prior(FIXTURES / "prior_2024_clean", year=2025)


def test_load_prior_refuses_missing_archive():
    with pytest.raises(RolloverError, match="does not exist"):
        load_prior(FIXTURES / "nonexistent", year=2024)


def test_load_prior_refuses_malformed_json(tmp_path):
    (tmp_path / "result.json").write_text("not json{{")
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    with pytest.raises(RolloverError, match="malformed"):
        load_prior(tmp_path, year=2024)


from agents.lib.rollover import compute_diff, CurrentState


def _load_current(fixture_name: str) -> CurrentState:
    base = FIXTURES / fixture_name
    return CurrentState(
        result=json.loads((base / "result.json").read_text()),
        profile=json.loads((base / "taxpayer_profile.json").read_text()),
        tax_law=json.loads((base / "tax_law_2025.json").read_text()) if (base / "tax_law_2025.json").exists() else {},
    )


def test_compute_diff_capital_loss_carryforward():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    changes = diff["carryforwards"]
    cap_loss = [c for c in changes if c.json_path == "prior_year.capital_loss_carryforward"]
    assert len(cap_loss) == 1
    # Prior schedule_d.line_16_combined = -4000; carryforward = abs(-4000) - 3000 = 1000
    assert cap_loss[0].prior_value == 1000
    assert cap_loss[0].action_suggested == "apply"


def test_compute_diff_qbi_loss_carryforward():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    qbi = [c for c in diff["carryforwards"] if c.json_path == "prior_year.qbi_loss_carryforward"]
    assert len(qbi) == 1
    assert qbi[0].prior_value == 800  # abs(-800)


def test_compute_diff_ftc_unused():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    ftc = [c for c in diff["carryforwards"] if c.json_path == "prior_year.unused_ftc_carryforward"]
    assert len(ftc) == 1
    # foreign_tax_paid_usd 1200 - ftc_allowed 900 = 300 unused
    assert ftc[0].prior_value == 300


def test_compute_diff_ira_basis_carryforward():
    """Form 8606 line 14 basis_remaining rolls into prior_year.ira_basis."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    basis = [c for c in diff["carryforwards"] if c.json_path == "prior_year.ira_basis"]
    assert len(basis) == 1
    assert basis[0].prior_value == 6140.35
    assert basis[0].action_suggested == "apply"


def test_compute_diff_ira_basis_zero_skipped():
    """A prior return with no Form 8606 basis produces no ira_basis diff entry."""
    prior = PriorReturn(
        year=2024,
        result={"meta": {"tax_year": 2024}, "form_8606": {"basis_remaining": 0}},
        profile=json.loads((FIXTURES / "prior_2024_clean" / "taxpayer_profile.json").read_text()),
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    basis = [c for c in diff["carryforwards"] if c.json_path == "prior_year.ira_basis"]
    assert basis == []


def test_compute_diff_ira_basis_missing_section_skipped():
    """No form_8606 section at all (forward-compatible prior) → no entry, no crash."""
    prior = PriorReturn(
        year=2024,
        result={"meta": {"tax_year": 2024}},
        profile=json.loads((FIXTURES / "prior_2024_clean" / "taxpayer_profile.json").read_text()),
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    basis = [c for c in diff["carryforwards"] if c.json_path == "prior_year.ira_basis"]
    assert basis == []


def test_compute_diff_ira_basis_conflict_asks():
    """A differing current ira_basis surfaces as 'ask', like other carryforwards."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    current.profile["prior_year"]["ira_basis"] = 999
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    basis = [c for c in diff["carryforwards"] if c.json_path == "prior_year.ira_basis"]
    assert len(basis) == 1
    assert basis[0].current_value == 999
    assert basis[0].action_suggested == "ask"


def test_compute_diff_skips_zero_prior_values():
    """Prior with no capital loss → no diff entry for capital_loss_carryforward."""
    prior_dir = FIXTURES / "prior_2024_drift_backward"
    # Use clean fixture profile + drift_backward result (missing QBI section)
    prior = PriorReturn(
        year=2024,
        result=json.loads((prior_dir / "result.json").read_text()),
        profile=json.loads((FIXTURES / "prior_2024_clean" / "taxpayer_profile.json").read_text()),
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    # form_8995_qbi missing → qbi entry should not appear (or appear as skip)
    qbi = [c for c in diff["carryforwards"] if c.json_path == "prior_year.qbi_loss_carryforward"]
    assert qbi == [] or qbi[0].action_suggested == "skip"


def test_compute_diff_handles_forward_schema_drift():
    """Prior result.json has an unknown extra top-level field — diff should still complete."""
    prior_dir = FIXTURES / "prior_2024_drift_forward"
    prior = PriorReturn(
        year=2024,
        result=json.loads((prior_dir / "result.json").read_text()),
        profile=json.loads((FIXTURES / "prior_2024_clean" / "taxpayer_profile.json").read_text()),
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    # Capital loss still rolls correctly despite the unknown extra field
    cap_loss = [c for c in diff["carryforwards"] if c.json_path == "prior_year.capital_loss_carryforward"]
    assert len(cap_loss) == 1
    assert cap_loss[0].prior_value == 1000


def test_compute_diff_conflict_flags_ask():
    """Re-rollover against current_2025_partial: $3K already filled → action_suggested == 'ask'."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_partial")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    cap_loss = [c for c in diff["carryforwards"] if c.json_path == "prior_year.capital_loss_carryforward"]
    assert len(cap_loss) == 1
    assert cap_loss[0].current_value == 3000
    assert cap_loss[0].prior_value == 1000
    assert cap_loss[0].action_suggested == "ask"


def test_compute_diff_identity_names_roll():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"identity"}, tax_law_new=current.tax_law)
    first = [c for c in diff["identity"] if c.json_path == "primary.first_name"]
    assert len(first) == 1
    assert first[0].prior_value == "Test"
    assert first[0].action_suggested == "apply"


def test_compute_diff_identity_filing_status_always_asks():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"identity"}, tax_law_new=current.tax_law)
    fs = [c for c in diff["identity"] if c.json_path == "filing_status"]
    assert len(fs) == 1
    assert fs[0].action_suggested == "ask"


def test_compute_diff_identity_drops_spouse_when_single():
    """If new filing_status is Single, spouse block should not roll forward."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    current.profile["filing_status"] = "Single"
    diff = compute_diff(prior, current, categories={"identity"}, tax_law_new=current.tax_law)
    spouse = [c for c in diff["identity"] if c.json_path == "spouse"]
    # Either no diff entry, OR entry with action_suggested == "skip" and a warning reason
    if spouse:
        assert spouse[0].action_suggested == "skip"
        assert "filing status" in spouse[0].reason.lower()


def test_age_tick_16_to_17_flips_ctc_eligible():
    """Alice DOB 2008-03-15: age 16 at end of 2024, age 17 at end of 2025."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"dependents"}, tax_law_new=current.tax_law)
    alice = [c for c in diff["dependents"] if c.prior_value.get("first_name") == "Alice"][0]
    assert alice.prior_value["ctc_eligible"] is False  # already flipped by age tick
    assert "CTC" in alice.reason or "17" in alice.reason
    assert alice.action_suggested == "ask"  # surfaces because of warning


def test_age_tick_21_to_22_no_change():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"dependents"}, tax_law_new=current.tax_law)
    bob = [c for c in diff["dependents"] if c.prior_value.get("first_name") == "Bob"][0]
    assert bob.prior_value["ctc_eligible"] is False
    assert bob.action_suggested == "apply"


def test_age_tick_malformed_dob_skips_without_crash():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    prior.profile["dependents"][0]["dob"] = "not-a-date"
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"dependents"}, tax_law_new=current.tax_law)
    alice = [c for c in diff["dependents"] if c.prior_value.get("first_name") == "Alice"]
    # Either skipped entirely OR included with action_suggested == "skip"
    if alice:
        assert alice[0].action_suggested == "skip"
        assert "DOB" in alice[0].reason or "date" in alice[0].reason.lower()


def test_expected_sources_w2_employer_with_full_ein():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"expected_sources"}, tax_law_new=current.tax_law)
    w2 = [c for c in diff["expected_sources"] if c.json_path == "expected_sources.w2_employers"][0]
    assert w2.prior_value == [{"name": "Acme Corp", "ein": "12-3456789"}]


def test_expected_sources_foreign_account_dedup():
    """8938 and FBAR fixtures both contain the same Foreign Bank EU account — only one row out."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"expected_sources"}, tax_law_new=current.tax_law)
    fa = [c for c in diff["expected_sources"] if c.json_path == "expected_sources.foreign_accounts"][0]
    assert len(fa.prior_value) == 1
    assert fa.prior_value[0]["institution"] == "Foreign Bank EU"


from agents.lib.rollover import apply_changes, WritePaths


def test_apply_changes_writes_carryforward(tmp_path):
    # Copy current_2025_empty into tmp
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(
        result=tmp_path / "ws" / "result.json",
        profile=tmp_path / "ws" / "taxpayer_profile.json",
    )
    changes = {
        "carryforwards": [Change(
            category="carryforwards",
            json_path="prior_year.capital_loss_carryforward",
            target_file="taxpayer_profile.json",
            prior_value=1000,
            current_value=0,
            action_suggested="apply",
            reason="test",
        )]
    }
    apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)
    profile = json.loads(targets.profile.read_text())
    assert profile["prior_year"]["capital_loss_carryforward"] == 1000


def test_apply_changes_skipped_category_writes_nothing(tmp_path):
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(result=tmp_path / "ws" / "result.json", profile=tmp_path / "ws" / "taxpayer_profile.json")
    original = targets.profile.read_text()
    changes = {"carryforwards": [Change(
        category="carryforwards", json_path="prior_year.capital_loss_carryforward",
        target_file="taxpayer_profile.json", prior_value=1000, current_value=0,
        action_suggested="apply", reason="test")]}
    apply_changes(changes, category_decisions={"carryforwards": "skip"}, targets=targets)
    assert targets.profile.read_text() == original


def test_apply_changes_atomic_rollback_on_failure(tmp_path, monkeypatch):
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(result=tmp_path / "ws" / "result.json", profile=tmp_path / "ws" / "taxpayer_profile.json")
    original = targets.profile.read_text()

    real_replace = os.replace
    calls = {"n": 0}
    def flaky_replace(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise OSError("simulated failure")
        return real_replace(*args, **kwargs)
    monkeypatch.setattr("os.replace", flaky_replace)

    changes = {"carryforwards": [Change(
        category="carryforwards", json_path="prior_year.capital_loss_carryforward",
        target_file="taxpayer_profile.json", prior_value=1000, current_value=0,
        action_suggested="apply", reason="test")]}
    with pytest.raises(RolloverError):
        apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)
    assert targets.profile.read_text() == original
    # No .tmp files left behind
    tmp_files = list(tmp_path.rglob("*.tmp"))
    assert tmp_files == [], f"Found leftover tmp files: {tmp_files}"


def test_apply_changes_chmod_600(tmp_path):
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(result=tmp_path / "ws" / "result.json", profile=tmp_path / "ws" / "taxpayer_profile.json")
    changes = {"carryforwards": [Change(
        category="carryforwards", json_path="prior_year.capital_loss_carryforward",
        target_file="taxpayer_profile.json", prior_value=1000, current_value=0,
        action_suggested="apply", reason="test")]}
    apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)
    mode = targets.profile.stat().st_mode
    assert (mode & 0o777) == 0o600


# --- CLI integration tests ---------------------------------------------------

def test_cli_diff_json_output(tmp_path):
    """Full pipeline: synthetic archive + empty current → run diff via subprocess → parse JSON."""
    import shutil
    shutil.copytree(FIXTURES / "prior_2024_clean", tmp_path / "archive" / "2024")
    shutil.copy(FIXTURES / "current_2025_empty" / "result.json", tmp_path / "result.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "taxpayer_profile.json", tmp_path / "taxpayer_profile.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "tax_law_2025.json", tmp_path / "tax_law_2025.json")

    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "diff",
         "--from", str(tmp_path / "archive" / "2024"),
         "--to", str(tmp_path),
         "--year", "2025",
         "--json"],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "carryforwards" in payload
    assert any(c["json_path"] == "prior_year.capital_loss_carryforward" for c in payload["carryforwards"])


def test_cli_apply_writes_files(tmp_path):
    import shutil
    shutil.copytree(FIXTURES / "prior_2024_clean", tmp_path / "archive" / "2024")
    shutil.copy(FIXTURES / "current_2025_empty" / "result.json", tmp_path / "result.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "taxpayer_profile.json", tmp_path / "taxpayer_profile.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "tax_law_2025.json", tmp_path / "tax_law_2025.json")

    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "apply",
         "--from", str(tmp_path / "archive" / "2024"),
         "--to", str(tmp_path),
         "--year", "2025",
         "--categories", "carryforwards"],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode == 0, proc.stderr
    profile = json.loads((tmp_path / "taxpayer_profile.json").read_text())
    assert profile["prior_year"]["capital_loss_carryforward"] == 1000


def test_cli_rejects_path_traversal(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "diff",
         "--from", "../../../etc",
         "--to", str(tmp_path),
         "--year", "2025"],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode != 0
    assert "path" in proc.stderr.lower() or "outside" in proc.stderr.lower() or "does not exist" in proc.stderr.lower()


def test_cli_rejects_invalid_year(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "diff",
         "--from", str(tmp_path), "--to", str(tmp_path),
         "--year", "2025; rm -rf /"],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert proc.returncode != 0


def test_apply_changes_two_file_atomicity(tmp_path, monkeypatch):
    """If the second os.replace fails after the first succeeds, both target files
    must remain in their pre-rollover state (no half-applied workspace).

    Latent in v1 because FIELD_MAP only targets taxpayer_profile.json today,
    but real the moment a result.json target lands in FIELD_MAP.
    """
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(
        result=tmp_path / "ws" / "result.json",
        profile=tmp_path / "ws" / "taxpayer_profile.json",
    )
    original_profile = targets.profile.read_bytes()
    original_result = targets.result.read_bytes()

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated second-replace failure")
        return real_replace(*args, **kwargs)

    monkeypatch.setattr("agents.lib.rollover.os.replace", flaky_replace)

    changes = {"carryforwards": [Change(
        category="carryforwards",
        json_path="prior_year.capital_loss_carryforward",
        target_file="taxpayer_profile.json",
        prior_value=1000, current_value=0,
        action_suggested="apply", reason="test",
    )]}
    with pytest.raises(RolloverError):
        apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)

    # Both files must be exactly as they were before
    assert targets.profile.read_bytes() == original_profile
    assert targets.result.read_bytes() == original_result
    # No leftover .tmp files
    assert list(tmp_path.rglob("*.tmp")) == []
