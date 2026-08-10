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


def test_transform_capital_loss_prefers_explicit_block():
    """The Capital Loss Carryover Worksheet block written by /tax-compute wins
    over the line_16 derivation (which assumes the full $3K was used)."""
    from agents.lib.rollover import _transform_capital_loss
    raw = {"line_16_combined": -10000,
           "loss_carryforward_to_next_year": {"short_term": 4000, "long_term": 3500}}
    assert _transform_capital_loss(raw) == 7500


def test_transform_capital_loss_zero_block_falls_back_to_derivation():
    """All-zero explicit block = template scaffolding, not a computed result —
    fall back to abs(loss) - 3000 so a real carryforward is not dropped."""
    from agents.lib.rollover import _transform_capital_loss
    raw = {"line_16_combined": -10000,
           "loss_carryforward_to_next_year": {"short_term": 0, "long_term": 0}}
    assert _transform_capital_loss(raw) == 7000


def test_transform_capital_loss_dict_without_block_derives():
    from agents.lib.rollover import _transform_capital_loss
    assert _transform_capital_loss({"line_16_combined": -5000}) == 2000
    assert _transform_capital_loss({"line_16_combined": 5000}) == 0
    assert _transform_capital_loss({}) == 0


def test_transform_capital_loss_legacy_number_still_works():
    """Older archives resolved source_path to the bare line_16 number."""
    from agents.lib.rollover import _transform_capital_loss
    assert _transform_capital_loss(-5000) == 2000
    assert _transform_capital_loss(-2000) == 0  # within the $3K used
    assert _transform_capital_loss(None) == 0
    assert _transform_capital_loss(True) == 0  # bool is not an amount


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


def test_cli_categories_trim_spaces(tmp_path):
    """The documented comma-space spelling must select every category."""
    import shutil
    shutil.copytree(FIXTURES / "prior_2024_clean", tmp_path / "archive" / "2024")
    shutil.copy(FIXTURES / "current_2025_empty" / "result.json", tmp_path / "result.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "taxpayer_profile.json", tmp_path / "taxpayer_profile.json")
    shutil.copy(FIXTURES / "current_2025_empty" / "tax_law_2025.json", tmp_path / "tax_law_2025.json")

    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "diff",
         "--from", str(tmp_path / "archive" / "2024"),
         "--to", str(tmp_path), "--year", "2025", "--json",
         "--categories", "carryforwards, dependents"],
        capture_output=True, text=True, cwd=str(repo),
    )

    assert proc.returncode == 0, proc.stderr
    assert set(json.loads(proc.stdout)) == {"carryforwards", "dependents"}


@pytest.mark.parametrize("categories", ["carryforwards,,dependents", "made_up"])
def test_cli_rejects_invalid_categories(tmp_path, categories):
    """Invalid tokens must fail instead of producing a partial clean-looking diff."""
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        ["python3", "-m", "agents.lib.rollover", "diff",
         "--from", str(tmp_path), "--to", str(tmp_path), "--year", "2025",
         "--categories", categories],
        capture_output=True, text=True, cwd=str(repo),
    )

    assert proc.returncode != 0
    assert "categor" in proc.stderr.lower()


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


def test_compute_diff_amt_credit_carryforward():
    """D9: form_6251.form_8801_carryforward_to_next_year rolls into
    prior_year.amt_credit_carryforward — losing the ISO AMT credit
    forfeits the money permanently."""
    prior = PriorReturn(
        year=2024,
        result={"meta": {"tax_year": 2024},
                "form_6251": {"form_8801_carryforward_to_next_year": 41750.0}},
        profile=json.loads((FIXTURES / "prior_2024_clean" / "taxpayer_profile.json").read_text()),
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    amt = [c for c in diff["carryforwards"]
           if c.json_path == "prior_year.amt_credit_carryforward"]
    assert len(amt) == 1
    assert amt[0].prior_value == 41750.0
    assert amt[0].action_suggested == "apply"


def test_compute_diff_amt_credit_zero_skipped():
    prior = PriorReturn(
        year=2024,
        result={"meta": {"tax_year": 2024},
                "form_6251": {"form_8801_carryforward_to_next_year": 0}},
        profile=json.loads((FIXTURES / "prior_2024_clean" / "taxpayer_profile.json").read_text()),
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    assert not [c for c in diff["carryforwards"]
                if c.json_path == "prior_year.amt_credit_carryforward"]


# --- foreign_holdings (PFIC / §988) history rollover -------------------------

from agents.lib.rollover import _roll_foreign_holdings_forward, _diff_foreign_holdings


def _pfic_holding(**over):
    h = {
        "kind": "fund",
        "fund_domicile": "RU",
        "custody": "foreign_broker",
        "currency": "RUB",
        "pfic_election": "none",
        "acquisition_date": "2021-03-01",
        "preceding_holding_years": 3,
        "distributions_by_year": {"2022": 0, "2023": 500, "2024": 700},
        "event_date": "2024-06-15",
        "received_distribution": True,
        "disposed": False,
        "disposition_gain_usd": None,
    }
    h.update(over)
    return h


def test_roll_foreign_holdings_increments_count_and_resets_events():
    rolled = _roll_foreign_holdings_forward([_pfic_holding()], prior_year=2024)
    assert len(rolled) == 1
    r = rolled[0]
    # preceding_holding_years incremented: the just-completed 2024 becomes a preceding year.
    assert r["preceding_holding_years"] == 4
    # Static facts + cumulative history preserved.
    assert r["acquisition_date"] == "2021-03-01"
    assert r["distributions_by_year"] == {"2022": 0, "2023": 500, "2024": 700}
    assert r["pfic_election"] == "none"
    # Per-year event fields RESET (a stale prior-year distribution/sale must not carry).
    assert r["event_date"] is None
    assert r["received_distribution"] is False
    assert r["disposed"] is False
    assert r["disposition_gain_usd"] is None


def test_roll_foreign_holdings_drops_full_close_only():
    # Codex round 9 P2 + round 18 P1: a FULLY-closed position is dropped, but a
    # PARTIAL disposition (or an unknown/absent full-close flag) is CARRIED FORWARD
    # so its retained shares + history are not silently lost.
    holdings = [
        _pfic_holding(disposed=True, position_fully_closed=True, fund_domicile="FULL"),
        _pfic_holding(disposed=True, position_fully_closed=False, fund_domicile="PARTIAL"),
        _pfic_holding(disposed=True, fund_domicile="UNKNOWN"),  # no flag → carried
        _pfic_holding(fund_domicile="IE"),
    ]
    rolled = _roll_foreign_holdings_forward(holdings, prior_year=2024)
    domiciles = {r["fund_domicile"] for r in rolled}
    assert domiciles == {"PARTIAL", "UNKNOWN", "IE"}  # FULL dropped, rest carried
    # A carried partial disposition has its `disposed` flag reset for the new year.
    partial = next(r for r in rolled if r["fund_domicile"] == "PARTIAL")
    assert partial["disposed"] is False


def test_roll_foreign_holdings_resets_per_event_dates():
    # Codex round 19 P2: the new per-event date fields must be reset on carry —
    # a stale distribution_date/disposition_date would misdate next year's event
    # (or be rejected as prior to current_year by compute_pfic_1291).
    h = _pfic_holding(distribution_date="2024-06-15", disposition_date="2024-09-01",
                      position_fully_closed=False)
    rolled = _roll_foreign_holdings_forward([h], prior_year=2024)
    assert len(rolled) == 1
    assert rolled[0]["distribution_date"] is None
    assert rolled[0]["disposition_date"] is None
    assert rolled[0]["position_fully_closed"] is None


def test_roll_foreign_holdings_adds_zero_distribution_for_carried_year():
    # Codex round 22: the fund was held THROUGH prior_year; that year is now a
    # preceding base-period year. If it had NO distribution, distributions_by_year
    # lacks an entry — a later distribution would then see a short zero-inclusive
    # history and compute_pfic_excess_distribution would mark the result INCOMPLETE.
    # An explicit {prior_year: 0} is added when absent.
    h = _pfic_holding(distributions_by_year={"2022": 0, "2023": 500},
                      received_distribution=False)
    rolled = _roll_foreign_holdings_forward([h], prior_year=2024)
    assert rolled[0]["distributions_by_year"] == {"2022": 0, "2023": 500, "2024": 0}
    # Idempotent: a year already present (even as 0) is not clobbered.
    h2 = _pfic_holding(distributions_by_year={"2024": 700})
    rolled2 = _roll_foreign_holdings_forward([h2], prior_year=2024)
    assert rolled2[0]["distributions_by_year"]["2024"] == 700


def test_roll_foreign_holdings_inits_absent_distribution_history():
    # Codex round 31: a fund with NO prior distributions can validly have no
    # distributions_by_year mapping at all. On rollover it must be INITIALIZED with
    # {prior_year: 0} so a later distribution has the zero-inclusive base (not left
    # empty, which would mark the §1291 result incomplete forever).
    h = _pfic_holding()
    del h["distributions_by_year"]  # no mapping at all
    rolled = _roll_foreign_holdings_forward([h], prior_year=2024)
    assert rolled[0]["distributions_by_year"] == {"2024": 0}


def test_roll_foreign_holdings_deepcopies_nested_history():
    src = _pfic_holding()
    rolled = _roll_foreign_holdings_forward([src], prior_year=2024)
    rolled[0]["distributions_by_year"]["2024"] = 99999
    # Mutating the rolled copy must not touch the source (deepcopy).
    assert src["distributions_by_year"]["2024"] == 700


def test_roll_foreign_holdings_skips_non_dict_and_bad_count():
    holdings = [_pfic_holding(preceding_holding_years=None), "junk", 42,
                _pfic_holding(preceding_holding_years=0)]
    rolled = _roll_foreign_holdings_forward(holdings, prior_year=2024)
    assert len(rolled) == 2
    # A None/absent count is left as-is (no crash); a valid 0 increments to 1.
    assert rolled[0]["preceding_holding_years"] is None
    assert rolled[1]["preceding_holding_years"] == 1


def test_roll_foreign_holdings_empty_or_missing():
    assert _roll_foreign_holdings_forward([], 2024) == []
    assert _roll_foreign_holdings_forward(None, 2024) == []
    assert _roll_foreign_holdings_forward("not a list", 2024) == []


def test_diff_foreign_holdings_surfaces_ask_change():
    prior = PriorReturn(
        year=2024,
        result={"meta": {"tax_year": 2024}},
        profile={"foreign_holdings_detail": {"holdings": [_pfic_holding()]}},
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"foreign_holdings"}, tax_law_new=current.tax_law)
    changes = diff["foreign_holdings"]
    assert len(changes) == 1
    c = changes[0]
    assert c.json_path == "foreign_holdings_detail.holdings"
    assert c.action_suggested == "ask"
    assert c.prior_value[0]["preceding_holding_years"] == 4
    assert c.prior_value[0]["event_date"] is None


def test_diff_foreign_holdings_no_prior_holdings_no_change():
    prior = PriorReturn(
        year=2024,
        result={"meta": {"tax_year": 2024}},
        profile={"foreign_holdings_detail": {"holdings": []}},
    )
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"foreign_holdings"}, tax_law_new=current.tax_law)
    assert diff["foreign_holdings"] == []


def test_apply_foreign_holdings_writes_rolled_list(tmp_path):
    """End-to-end: the carried holdings list lands in the new profile."""
    profile = {"foreign_holdings_detail": {"holdings": []}}
    result = {"meta": {"tax_year": 2025}}
    ppath = tmp_path / "taxpayer_profile.json"
    rpath = tmp_path / "result.json"
    ppath.write_text(json.dumps(profile))
    rpath.write_text(json.dumps(result))
    change = Change(
        category="foreign_holdings",
        json_path="foreign_holdings_detail.holdings",
        target_file="taxpayer_profile.json",
        prior_value=_roll_foreign_holdings_forward([_pfic_holding()], 2024),
        current_value=[],
        action_suggested="ask",
        reason="carry",
    )
    apply_changes({"foreign_holdings": [change]}, {"foreign_holdings": "apply"},
                  WritePaths(result=rpath, profile=ppath))
    written = json.loads(ppath.read_text())
    holdings = written["foreign_holdings_detail"]["holdings"]
    assert len(holdings) == 1
    assert holdings[0]["preceding_holding_years"] == 4
