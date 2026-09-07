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
    """X3: the clean fixture archive carries only `line_16_combined = -4000`
    — no Capital Loss Carryover Worksheet block — so the split is UNKNOWN.
    The derived lower bound (abs(loss) − $3,000 = 1000) lands on the LEGACY
    combined field as an `ask`, never split by guess; /tax-compute then stops
    for the short/long split before Schedule D lines 6 and 14."""
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    changes = diff["carryforwards"]
    cap_loss = [c for c in changes if c.json_path == "prior_year.capital_loss_carryforward"]
    assert len(cap_loss) == 1
    assert cap_loss[0].prior_value == 1000
    assert cap_loss[0].action_suggested == "ask"
    assert "split" in cap_loss[0].reason
    assert not [c for c in changes if c.json_path.startswith(
        "prior_year.capital_loss_carryforward_")]


def _split(raw, status="MFJ"):
    from agents.lib.rollover import _capital_loss_carryforward
    return _capital_loss_carryforward(raw, status)


ZEROS = {"short_term": 0, "long_term": 0}


def test_capital_loss_explicit_block_keeps_its_character():
    """The worksheet block written by /tax-compute is the answer, per side —
    a combined figure cannot be placed on Schedule D (line 6 is short-term,
    line 14 is long-term)."""
    raw = {"line_16_combined": -10000,
           "loss_carryforward_to_next_year": {"short_term": 4000, "long_term": 3500}}
    assert _split(raw) == {"short_term": 4000, "long_term": 3500}
    # More than the bound is fine: the worksheet used less than the cap.
    raw["loss_carryforward_to_next_year"] = {"short_term": 0, "long_term": 9000}
    assert _split(raw) == {"short_term": 0, "long_term": 9000}


def test_capital_loss_block_totalling_less_than_the_bound_is_a_contradiction():
    """A correct worksheet total is never below abs(line 16) − cap, so a block
    below it is the template's scaffolding (all zero), a half-filled one, or
    a sign slip — one comparison catches all three. The lower bound is
    offered on the legacy field for RESOLUTION, never split by guess."""
    raw = {"line_16_combined": -10000,
           "loss_carryforward_to_next_year": {"short_term": 0, "long_term": 0}}
    assert _split(raw) == {"combined": 7000, "unresolved": "contradiction", "cap_assumed": False}
    raw["loss_carryforward_to_next_year"] = {"short_term": 0, "long_term": 3500}
    assert _split(raw) == {"combined": 7000, "unresolved": "contradiction", "cap_assumed": False}
    raw["loss_carryforward_to_next_year"] = {"short_term": -4000, "long_term": 3000}
    assert _split(raw) == {"combined": 7000, "unresolved": "contradiction", "cap_assumed": False}
    # ...and an explicit zero beside a loss INSIDE the cap is a computed zero.
    raw["line_16_combined"] = -2000
    raw["loss_carryforward_to_next_year"] = {"short_term": 0, "long_term": 0}
    assert _split(raw) == ZEROS


def test_capital_loss_a_gain_year_is_an_answer_not_silence():
    """The archive saying "nothing carries" must retire a stale figure in the
    target profile; only an archive with no Schedule D figure at all is silent."""
    assert _split({"line_16_combined": 5000}) == ZEROS
    assert _split(5000) == ZEROS
    # A loss INSIDE the cap with no block: the worksheet uses less than the
    # cap when taxable income is low, so a carryforward may remain — a zero
    # bound offered for resolution, loud, not proof that nothing carries.
    assert _split(-2000) == {"combined": 0, "unresolved": "legacy", "cap_assumed": False}
    assert _split({}) == {}
    assert _split(None) == {}


def test_capital_loss_block_above_the_loss_or_beside_a_gain_is_a_contradiction():
    """The likeliest authoring slip is the PRE-deduction loss written where the
    carryover belongs; a block above abs(line 16), or any non-zero block in a
    gain year, was applied silently — the over-statement direction."""
    block = {"short_term": 4000, "long_term": 3000}
    assert _split({"line_16_combined": 5000, "loss_carryforward_to_next_year": block}) == \
        {"combined": 0, "unresolved": "contradiction", "cap_assumed": False}
    assert _split({"line_16_combined": -4000, "loss_carryforward_to_next_year": block}) == \
        {"combined": 1000, "unresolved": "contradiction", "cap_assumed": False}
    assert _split({"line_16_combined": -7000, "loss_carryforward_to_next_year": block}) == block


def test_capital_loss_absent_block_derives_a_combined_lower_bound():
    assert _split({"line_16_combined": -5000}) == {"combined": 2000, "unresolved": "legacy", "cap_assumed": False}
    # Older archives resolved source_path to the bare line_16 number.
    assert _split(-5000) == {"combined": 2000, "unresolved": "legacy", "cap_assumed": False}


def test_capital_loss_derivation_uses_the_engine_cap_and_assumes_3000_when_unreadable():
    """The cap is the engine's constant, by the PRIOR year's status in any
    spelling this module reads. A status it cannot read takes the $3,000 cap,
    the SMALLER bound: the bound is a value the ask lands, so it must be one
    no filer's true carryforward sits below (the MFS one is $1,500 higher)."""
    from agents.lib.tax_computations import CAPITAL_LOSS_LIMIT, CAPITAL_LOSS_LIMIT_MFS
    assert CAPITAL_LOSS_LIMIT_MFS < CAPITAL_LOSS_LIMIT
    for status in ("MFS", "Married Filing Separately", "married filing separately"):
        assert _split({"line_16_combined": -5000}, status)["combined"] == 5000 - CAPITAL_LOSS_LIMIT_MFS
    assert _split({"line_16_combined": -5000}, "Married Filing Jointly")["combined"] == 5000 - CAPITAL_LOSS_LIMIT
    unread = _split({"line_16_combined": -5000}, "married_filing_separately")
    assert unread == {"combined": 5000 - CAPITAL_LOSS_LIMIT,
                      "unresolved": "legacy", "cap_assumed": True}
    assert _split({"line_16_combined": -5000}, None)["cap_assumed"] is True


def test_compute_diff_splits_the_carryforward_and_retires_the_legacy_field():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    prior.result["schedule_d"] = {
        "line_16_combined": -10000,
        "loss_carryforward_to_next_year": {"short_term": 4000, "long_term": 3000}}
    current = _load_current("current_2025_partial")   # legacy field holds 3000
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    by_path = {c.json_path: c for c in diff["carryforwards"]}
    st = by_path["prior_year.capital_loss_carryforward_short_term"]
    lt = by_path["prior_year.capital_loss_carryforward_long_term"]
    assert (st.prior_value, st.action_suggested) == (4000, "apply")
    assert (lt.prior_value, lt.action_suggested) == (3000, "apply")
    legacy = by_path["prior_year.capital_loss_carryforward"]
    assert legacy.prior_value == 0 and legacy.current_value == 3000
    assert "superseded" in legacy.reason


def test_compute_diff_contradiction_asks_on_the_legacy_field():
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    prior.result["schedule_d"] = {
        "line_16_combined": -10000,
        "loss_carryforward_to_next_year": {"short_term": 0, "long_term": 0}}
    current = _load_current("current_2025_empty")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    legacy = [c for c in diff["carryforwards"]
              if c.json_path == "prior_year.capital_loss_carryforward"]
    assert len(legacy) == 1
    assert legacy[0].prior_value == 7000 and legacy[0].action_suggested == "ask"
    assert "worksheet" in legacy[0].reason.lower()


def _prior(result_extra, profile_status="MFJ", year=2025):
    from agents.lib.rollover import PriorReturn
    result = {"meta": {"filing_status": profile_status}}
    result.update(result_extra)
    return PriorReturn(year=year, result=result, profile={"filing_status": profile_status})


def _cap_changes(result_extra, profile, prior_status="MFJ"):
    from agents.lib.rollover import _capital_loss_changes
    prior = _prior(result_extra, prior_status)
    return {c.json_path: c for c in _capital_loss_changes(
        prior, profile, result_extra.get("schedule_d"))}


def test_a_gain_year_retires_a_stale_split_in_the_profile_as_an_ask():
    """The interview runs before the chained rollover and asks for the split,
    so a figure can already sit in the profile; a gain year used to emit
    nothing, and $9,000 that no longer existed landed on Schedule D line 6."""
    profile = {"filing_status": "MFJ", "prior_year": {
        "capital_loss_carryforward_short_term": 9000,
        "capital_loss_carryforward_long_term": 0, "capital_loss_carryforward": 0}}
    changes = _cap_changes({"schedule_d": {"line_16_combined": 50000,
                                           "loss_carryforward_to_next_year": {"short_term": 0, "long_term": 0}}},
                           profile)
    st = changes["prior_year.capital_loss_carryforward_short_term"]
    assert st.action_suggested == "ask" and st.prior_value == 0 and st.current_value == 9000
    assert "no carryforward remains" in st.reason
    assert "prior_year.capital_loss_carryforward_long_term" not in changes


def test_a_joint_year_carryforward_is_never_applied_onto_a_separate_year_return():
    """Reg. §1.1212-1(c) allocates a joint carryover between the spouses by
    each one's own net loss; a decedent's dies with them (Rev. Rul. 74-175)."""
    block = {"schedule_d": {"line_16_combined": -50000,
                            "loss_carryforward_to_next_year": {"short_term": 40000, "long_term": 7000}}}
    for new_status in ("Single", "MFS", "QSS", "HOH"):
        profile = {"filing_status": new_status, "prior_year": {}}
        changes = _cap_changes(block, profile, prior_status="Married Filing Jointly")
        for side in ("short_term", "long_term"):
            c = changes[f"prior_year.capital_loss_carryforward_{side}"]
            assert c.action_suggested == "ask" and "1.1212-1(c)" in c.reason, new_status
    same = _cap_changes(block, {"filing_status": "MFJ", "prior_year": {}})
    assert same["prior_year.capital_loss_carryforward_short_term"].action_suggested == "apply"
    # Fails CLOSED: the template ships filing_status null, and null is not "MFJ".
    unset = _cap_changes(block, {"filing_status": None, "prior_year": {}})
    assert unset["prior_year.capital_loss_carryforward_short_term"].action_suggested == "ask"


def test_a_gain_year_retires_a_legacy_figure_as_an_ask_not_a_silent_apply():
    """The legacy field was retired with `apply` and a reason claiming a split
    superseded it — true when a split exists, false in a gain year."""
    profile = {"filing_status": "MFJ", "prior_year": {"capital_loss_carryforward": 3000}}
    changes = _cap_changes({"schedule_d": {"line_16_combined": 5000}}, profile)
    legacy = changes["prior_year.capital_loss_carryforward"]
    assert legacy.action_suggested == "ask" and legacy.prior_value == 0
    assert "no carryforward remains" in legacy.reason


def test_a_non_finite_workspace_file_is_one_error_line_from_the_cli(tmp_path):
    """`_load_current_state` sat outside the CLI's error boundary, so the
    refusal it now raises printed a traceback and blamed 'the archived file'."""
    import shutil, subprocess
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURES / "current_2025_empty", ws)
    (ws / "archive" / "2024").mkdir(parents=True)
    for f in ("result.json", "taxpayer_profile.json"):
        shutil.copy(FIXTURES / "prior_2024_clean" / f, ws / "archive" / "2024" / f)
    (ws / "result.json").write_text('{"meta": {"junk": NaN}}')
    proc = subprocess.run(
        [sys.executable, "-m", "agents.lib.rollover", "diff", "--from", str(ws / "archive" / "2024"),
         "--to", str(ws), "--year", "2025", "--categories", "carryforwards"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr and "ERROR:" in proc.stderr
    assert "non-finite" in proc.stderr and "archived" not in proc.stderr


def test_the_combined_bound_asks_to_zero_a_split_that_would_disarm_the_compute_stop():
    """/tax-compute STOPs on a combined-only figure only while both split
    fields are zero; a non-zero split beside the bound let it run past."""
    profile = {"filing_status": "MFJ", "prior_year": {
        "capital_loss_carryforward_short_term": 9000, "capital_loss_carryforward": 0}}
    changes = _cap_changes({"schedule_d": {"line_16_combined": -4000}}, profile)
    legacy = changes["prior_year.capital_loss_carryforward"]
    assert legacy.action_suggested == "ask" and legacy.prior_value == 1000
    st = changes["prior_year.capital_loss_carryforward_short_term"]
    assert st.action_suggested == "ask" and st.prior_value == 0 and "STOP" in st.reason
    assert "prior_year.capital_loss_carryforward_long_term" not in changes


def test_the_split_targets_do_not_collide_with_the_field_map():
    """The two split paths are written outside FIELD_MAP, so the registry's
    duplicate-target test cannot see them; this one can."""
    from agents.lib.rollover import FIELD_MAP, _CAPITAL_LOSS_SPLIT, _CAPITAL_LOSS_LEGACY
    targets = {e["target_path"] for e in FIELD_MAP.values()}
    assert _CAPITAL_LOSS_LEGACY in targets
    assert not set(_CAPITAL_LOSS_SPLIT.values()) & targets


def test_the_non_finite_walk_is_the_one_secure_fs_walk():
    """Two readers, one rule: rollover and the tax-law cache refuse through
    the same `secure_fs.first_non_finite`."""
    import inspect
    from agents.lib import rollover, tax_law_fetcher, secure_fs
    assert rollover.first_non_finite is secure_fs.first_non_finite
    assert tax_law_fetcher.first_non_finite is secure_fs.first_non_finite
    assert "first_non_finite(" in inspect.getsource(rollover._refuse_non_finite)
    assert secure_fs.first_non_finite({"a": [1, {"b": float("nan")}]}) == "a[1].b"
    assert secure_fs.first_non_finite({"a": [1.5, 2]}) is None


def test_apply_changes_writes_both_split_fields(tmp_path):
    import shutil
    shutil.copytree(FIXTURES / "current_2025_partial", tmp_path / "ws")
    targets = WritePaths(result=tmp_path / "ws" / "result.json",
                         profile=tmp_path / "ws" / "taxpayer_profile.json")
    prior = load_prior(FIXTURES / "prior_2024_clean", year=2024)
    prior.result["schedule_d"] = {
        "line_16_combined": -10000,
        "loss_carryforward_to_next_year": {"short_term": 4000, "long_term": 3000}}
    current = _load_current("current_2025_partial")
    diff = compute_diff(prior, current, categories={"carryforwards"}, tax_law_new=current.tax_law)
    apply_changes(diff, category_decisions={"carryforwards": "apply"}, targets=targets)
    prior_year = json.loads(targets.profile.read_text())["prior_year"]
    assert prior_year["capital_loss_carryforward_short_term"] == 4000
    assert prior_year["capital_loss_carryforward_long_term"] == 3000
    assert prior_year["capital_loss_carryforward"] == 0


class TestTheJsonBoundaryRefusesWhatNoTransformShouldSee:
    """AGENTS.md §8: `json.loads` accepts NaN and Infinity, both pass
    `isinstance(x, (int, float))`, and every comparison with NaN is False —
    so `+inf` passed `v > 0` in the explicit block, a NaN skipped it and fell
    to the legacy path where NaN and −inf passed `raw >= 0`, and came out as
    `nan` / `inf` carryforwards. Refused at `_read_json`, the one boundary."""

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_number_in_an_archived_file_is_refused(self, tmp_path, token):
        from agents.lib.rollover import _read_json
        f = tmp_path / "result.json"
        f.write_text('{"schedule_d": {"line_16_combined": ' + token + '}}')
        with pytest.raises(RolloverError, match="finite"):
            _read_json(f)

    def test_apply_reads_its_targets_through_the_same_boundary(self, tmp_path):
        import shutil
        shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
        targets = WritePaths(result=tmp_path / "ws" / "result.json",
                             profile=tmp_path / "ws" / "taxpayer_profile.json")
        targets.profile.write_text('{"prior_year": {"ira_basis": NaN}}')
        changes = {"carryforwards": [Change(
            category="carryforwards", json_path="prior_year.ira_basis",
            target_file="taxpayer_profile.json", prior_value=1000, current_value=0,
            action_suggested="apply", reason="test")]}
        with pytest.raises(RolloverError, match="finite"):
            apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)

    def test_no_transform_reads_a_boolean_as_an_amount(self):
        from agents.lib.rollover import (_transform_abs, _transform_ftc_unused,
                                         _transform_ira_basis)
        assert _transform_abs(True) == 0
        assert _transform_abs(-4000) == 4000
        assert _transform_ftc_unused({"foreign_tax_paid_usd": True, "ftc_allowed": 0}) == 0
        assert _transform_ftc_unused({"foreign_tax_paid_usd": 500, "ftc_allowed": False}) == 500
        assert _transform_ira_basis(True) == 0


class TestTheSplitRunsThroughProfileComputeAndSetup:
    ROOT = Path(__file__).resolve().parent.parent

    def test_the_profile_template_carries_the_split_and_the_legacy_field(self):
        prior_year = json.loads((self.ROOT / "agents" / "templates"
                                 / "taxpayer_profile_template.json").read_text())["prior_year"]
        assert prior_year["capital_loss_carryforward_short_term"] == 0
        assert prior_year["capital_loss_carryforward_long_term"] == 0
        assert prior_year["capital_loss_carryforward"] == 0
        assert "_comment_capital_loss_carryforward" in prior_year

    def test_the_result_template_has_schedule_d_lines_6_and_14(self):
        sd = json.loads((self.ROOT / "agents" / "templates"
                         / "result_template.json").read_text())["schedule_d"]
        assert sd["part_i_short_term"]["line_6_prior_year_carryover"] == 0
        assert sd["part_ii_long_term"]["line_14_prior_year_carryover"] == 0

    def test_compute_consumes_the_split_and_stops_on_a_combined_only_figure(self):
        text = (self.ROOT / ".agents" / "skills" / "tax-compute" / "references"
                / "income-and-agi.md").read_text()
        assert "capital_loss_carryforward_short_term" in text
        assert "capital_loss_carryforward_long_term" in text
        assert "line_6_prior_year_carryover" in text
        assert "line_14_prior_year_carryover" in text
        para = text[text.index("**Capital loss carryforward (Schedule D):**"):]
        para = para[:para.index("\n\n")]
        assert "STOP" in para and "never" in para.lower()

    def test_setup_asks_for_the_split_and_rollover_names_it(self):
        interview = (self.ROOT / ".agents" / "skills" / "tax-setup" / "references"
                     / "interview.md").read_text()
        group9 = interview[interview.index("**Group 9"):]
        nxt = group9.find("**Group ", 8)
        group9 = group9[:nxt] if nxt > 0 else group9
        assert "short" in group9.lower() and "long" in group9.lower()
        assert "capital_loss_carryforward_short_term" in group9
        rollover = (self.ROOT / ".agents" / "skills" / "tax-rollover" / "SKILL.md").read_text()
        assert "capital_loss_carryforward" in rollover and "split" in rollover


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


def test_apply_changes_tmp_file_is_600_before_replace(tmp_path, monkeypatch):
    """os.replace carries a file's mode over to the name it lands on, so the .tmp
    file must already be 600 the moment it's created — not write-then-chmod after
    the replace — or the real target (profile/result, both PII-bearing) sits at
    the shell's default mode for the window between the replace and the trailing
    chmod, and permanently if the second replace then fails and the restore path
    never tightens it."""
    import shutil
    shutil.copytree(FIXTURES / "current_2025_empty", tmp_path / "ws")
    targets = WritePaths(result=tmp_path / "ws" / "result.json", profile=tmp_path / "ws" / "taxpayer_profile.json")

    seen_modes = []
    real_replace = os.replace

    def spy_replace(src, dst):
        seen_modes.append(Path(src).stat().st_mode & 0o777)
        return real_replace(src, dst)

    monkeypatch.setattr("agents.lib.rollover.os.replace", spy_replace)

    changes = {"carryforwards": [Change(
        category="carryforwards", json_path="prior_year.capital_loss_carryforward",
        target_file="taxpayer_profile.json", prior_value=1000, current_value=0,
        action_suggested="apply", reason="test")]}
    apply_changes(changes, category_decisions={"carryforwards": "apply"}, targets=targets)
    assert seen_modes == [0o600, 0o600]


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
    # `sys.executable`, never a bare "python3" off PATH. The engine refuses
    # to import on an interpreter that is not the workspace venv, so a PATH
    # lookup made these pass or fail on whether the shell had been
    # activated: ten of them failed under `.venv/bin/python -m pytest` on a
    # tree that was green under an activated shell.
        [sys.executable, "-m", "agents.lib.rollover", "diff",
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
        [sys.executable, "-m", "agents.lib.rollover", "apply",
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
        [sys.executable, "-m", "agents.lib.rollover", "diff",
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
        [sys.executable, "-m", "agents.lib.rollover", "diff",
         "--from", str(tmp_path), "--to", str(tmp_path), "--year", "2025",
         "--categories", categories],
        capture_output=True, text=True, cwd=str(repo),
    )

    assert proc.returncode != 0
    assert "categor" in proc.stderr.lower()


def test_cli_rejects_path_traversal(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "agents.lib.rollover", "diff",
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
        [sys.executable, "-m", "agents.lib.rollover", "diff",
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


def test_an_empty_worksheet_block_is_not_a_computed_zero():
    """`_amount` reads a missing figure as 0, which is the right default for
    something being summed and the wrong answer to "did anybody write this
    down?". A worksheet object nobody filled in therefore read as "the return
    establishes that nothing carries", and the changes below retired a live
    carryforward out of the profile on it."""
    loss = {"line_16_combined": -2000}
    assert _split(dict(loss, loss_carryforward_to_next_year={})) == {
        "combined": 0, "unresolved": "contradiction", "cap_assumed": False}
    for half in ({"short_term": 0}, {"short_term": 0, "long_term": None},
                 {"short_term": "0", "long_term": 0}):
        assert _split(dict(loss, loss_carryforward_to_next_year=half)
                      ).get("unresolved") == "contradiction", half
    # ...and a pair of zeros actually written down is still the answer.
    assert _split(dict(loss, loss_carryforward_to_next_year=dict(ZEROS))) == ZEROS


def test_a_zero_bound_cannot_land_and_erase_what_the_profile_holds():
    """`apply_changes` writes an `ask`'s prior_value once its category is
    approved. A bound of ZERO establishes nothing — this change's own reason
    says a carryforward may remain — and landing it wrote "none remains" into
    the profile and zeroed a split the filer may have keyed from the paper
    worksheet, leaving all three fields zero and /tax-compute's STOP unarmed."""
    profile = {"filing_status": "MFJ", "prior_year": {
        "capital_loss_carryforward": 2500,
        "capital_loss_carryforward_short_term": 1200}}
    changes = _cap_changes({"schedule_d": {"line_16_combined": -2000}}, profile)
    legacy = changes["prior_year.capital_loss_carryforward"]
    assert legacy.action_suggested == "ask" and "may remain" in legacy.reason
    assert legacy.prior_value == 2500 == legacy.current_value   # a no-op write
    assert "prior_year.capital_loss_carryforward_short_term" not in changes
