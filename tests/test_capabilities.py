"""Tests for the capability matrix / scope guard (agents/lib/capabilities.py).

C1: unsupported situations block /tax-compute unless explicitly acknowledged.
C2: triggered items + state scope become the meta.limitations manifest.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import capabilities as C  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _profile(flags=None, acknowledged=None, state="CA"):
    """A post-migration profile: the five gate-era flags are explicitly
    present (False) as /tax-setup writes them — absence fails closed."""
    merged = {f: False for f in C._REQUIRED_GATE_FLAGS}
    merged.update(flags or {})
    p = {
        "tax_year": 2025,
        "filing_status": "MFJ",
        "flags": merged,
        "address": {"state": state},
    }
    if acknowledged is not None:
        p["acknowledged_limitations"] = acknowledged
    return p


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------

def test_clean_profile_not_blocked():
    r = C.evaluate(_profile())
    assert r.blocking == ()
    assert r.manual == ()
    assert r.acknowledged == ()


def test_iso_exercise_is_manual_not_blocking():
    """D9 shipped federal Form 6251 — iso_exercise no longer blocks; it
    surfaces the CA Schedule P (CA AMT not computed) manifest note."""
    r = C.evaluate(_profile({"has_iso_exercise": True}))
    assert r.blocking == ()
    assert [f.rule_id for f in r.manual] == ["iso_exercise"]
    assert "Schedule P" in r.manual[0].form


@pytest.mark.parametrize("flag,rule_id", [
    ("has_rental_income", "rental_income"),
    ("has_marketplace_insurance", "marketplace_insurance"),
    ("moved_states_this_year", "part_year_residency"),
    ("nyc_resident", "nyc_resident"),
    ("has_household_employees", "household_employees"),
    ("has_dependent_unearned_income", "kiddie_tax"),
])
def test_each_unsupported_flag_blocks(flag, rule_id):
    r = C.evaluate(_profile({flag: True}))
    assert [f.rule_id for f in r.blocking] == [rule_id]


@pytest.mark.parametrize("flag,rule_id", [
    ("has_education_expenses", "education_credits"),
    ("has_dependent_care", "dependent_care"),
    ("has_energy_credits", "energy_credits"),
    ("has_ev_purchase", "ev_credit"),
    ("has_gambling_income", "gambling"),
    ("lives_abroad", "feie"),
])
def test_manual_flags_do_not_block(flag, rule_id):
    r = C.evaluate(_profile({flag: True}))
    assert r.blocking == ()
    assert [f.rule_id for f in r.manual] == [rule_id]


def test_acknowledged_unblocks_and_lands_in_manifest():
    r = C.evaluate(_profile({"has_rental_income": True},
                            acknowledged=["rental_income"]))
    assert r.blocking == ()
    assert [f.rule_id for f in r.acknowledged] == ["rental_income"]
    assert any("NOT COMPUTED (user acknowledged)" in line and "Schedule E" in line
               for line in r.limitations)


def test_acknowledgment_is_per_rule_not_global():
    """Acknowledging one gap must not unblock a different one."""
    r = C.evaluate(_profile(
        {"has_marketplace_insurance": True, "has_rental_income": True},
        acknowledged=["marketplace_insurance"]))
    assert [f.rule_id for f in r.blocking] == ["rental_income"]


def test_unknown_acknowledged_id_hard_errors():
    """A typo in acknowledged_limitations silently unblocks nothing — reject."""
    with pytest.raises(C.CapabilityError, match="unknown acknowledged"):
        C.evaluate(_profile({"has_rental_income": True},
                            acknowledged=["rental_incom"]))


def test_stale_but_valid_acknowledged_id_is_harmless():
    """An acknowledgment for a rule whose flag is off is ignored, not an error
    (the situation may have been corrected after acknowledgment)."""
    r = C.evaluate(_profile({}, acknowledged=["rental_income"]))
    assert r.blocking == () and r.acknowledged == ()


def test_extra_acknowledged_via_parameter():
    r = C.evaluate(_profile({"has_rental_income": True}),
                   extra_acknowledged={"rental_income"})
    assert r.blocking == ()
    assert [f.rule_id for f in r.acknowledged] == ["rental_income"]


# --------------------------------------------------------------------------
# legacy residency-answer migration guard (fail closed)
# --------------------------------------------------------------------------

def _legacy_profile(residency_value, with_new_flag=None):
    """A profile shaped like one created BEFORE the gate-era flags existed:
    all five keys absent; moved_states_this_year re-added when with_new_flag
    is a bool (a partially migrated profile)."""
    p = _profile({})
    for f in C._REQUIRED_GATE_FLAGS:
        p["flags"].pop(f, None)
    if with_new_flag is not None:
        p["flags"]["moved_states_this_year"] = with_new_flag
    p["prior_year"] = {"state_residency_changes": residency_value}
    return p


def _blocking_ids(report):
    return [f.rule_id for f in report.blocking]


def test_missing_gate_flags_fail_closed():
    """Codex P2 round 3: ALL gate-era flags must be explicitly present — a
    legacy profile (keys absent) blocks until the questions are answered,
    because absence-as-false would wave through an existing ISO/NYC/household/
    kiddie situation the old interview never asked about."""
    r = C.evaluate(_legacy_profile(None))
    assert "unanswered_gate_questions" in _blocking_ids(r)
    blocker = next(f for f in r.blocking if f.rule_id == "unanswered_gate_questions")
    for flag in C._REQUIRED_GATE_FLAGS:
        assert flag in blocker.note


def test_partially_migrated_profile_lists_only_missing_flags():
    r = C.evaluate(_legacy_profile(None, with_new_flag=False))
    blocker = next(f for f in r.blocking if f.rule_id == "unanswered_gate_questions")
    assert "moved_states_this_year" not in blocker.note
    assert "has_iso_exercise" in blocker.note


def test_fully_answered_profile_has_no_migration_block():
    r = C.evaluate(_profile())
    assert "unanswered_gate_questions" not in _blocking_ids(r)


def test_migration_block_is_not_acknowledgeable():
    """The remedy is answering five yes/no questions, not accepting a gap —
    the migration id is outside the registry so acknowledging it hard-errors."""
    with pytest.raises(C.CapabilityError, match="unknown acknowledged"):
        C.evaluate(_legacy_profile(None,), extra_acknowledged={"unanswered_gate_questions"})


def test_legacy_residency_move_blocks_when_flag_absent():
    """Codex P2 round 2: a pre-flag workspace with a recorded move must not
    sail through the gate just because the new flag doesn't exist yet."""
    r = C.evaluate(_legacy_profile("moved NY -> CA in June"))
    assert "part_year_residency" in _blocking_ids(r)
    move = next(f for f in r.blocking if f.rule_id == "part_year_residency")
    assert "re-answer" in move.situation


@pytest.mark.parametrize("negative", ["no", "None", " N/A ", "", None, False, "0"])
def test_legacy_explicit_negatives_do_not_trigger_residency_rule(negative):
    """Explicit negatives don't raise the residency rule (the generic
    unanswered-questions blocker still fires until the flags are set)."""
    r = C.evaluate(_legacy_profile(negative))
    assert "part_year_residency" not in _blocking_ids(r)


def test_legacy_true_boolean_blocks():
    r = C.evaluate(_legacy_profile(True))
    assert "part_year_residency" in _blocking_ids(r)


def test_new_flag_false_is_authoritative_over_legacy():
    """Once the user re-answered via the new flag, the legacy text is ignored."""
    r = C.evaluate(_legacy_profile("moved NY -> CA in June", with_new_flag=False))
    assert "part_year_residency" not in _blocking_ids(r)


def test_legacy_block_is_acknowledgeable_like_the_flag_path():
    p = _legacy_profile("moved NY -> CA in June")
    p["acknowledged_limitations"] = ["part_year_residency"]
    r = C.evaluate(p)
    assert "part_year_residency" not in _blocking_ids(r)
    assert [f.rule_id for f in r.acknowledged] == ["part_year_residency"]


# --------------------------------------------------------------------------
# strict input validation
# --------------------------------------------------------------------------

def test_non_bool_flag_value_hard_errors():
    """The string \"false\" is truthy — silently coercing it would invert the
    user's answer. Reject any present non-bool value."""
    with pytest.raises(C.CapabilityError, match="must be true/false"):
        C.evaluate(_profile({"has_iso_exercise": "false"}))


def test_null_registry_flag_is_false_for_non_gate_rules():
    """None → False is fine for ordinary registry flags (rental predates the
    gate-era strictness); the rule simply doesn't fire."""
    r = C.evaluate(_profile({"has_rental_income": None}))
    assert "rental_income" not in _blocking_ids(r)


def test_null_gate_flag_counts_as_unanswered():
    """Codex P2 round 4: a present-but-null gate-era flag is an UNANSWERED
    question, not an explicit 'no' — the migration blocker must fire."""
    r = C.evaluate(_profile({"has_iso_exercise": None}))
    assert "unanswered_gate_questions" in _blocking_ids(r)
    blocker = next(f for f in r.blocking if f.rule_id == "unanswered_gate_questions")
    assert "has_iso_exercise" in blocker.note
    assert "nyc_resident" not in blocker.note  # only the unanswered one listed


def test_non_dict_flags_hard_errors():
    with pytest.raises(C.CapabilityError, match="flags must be an object"):
        C.evaluate({"flags": [], "address": {"state": "CA"}})


def test_non_dict_profile_hard_errors():
    with pytest.raises(C.CapabilityError, match="JSON object"):
        C.evaluate(["not", "a", "profile"])


def test_non_list_acknowledged_hard_errors():
    with pytest.raises(C.CapabilityError, match="list of rule-id strings"):
        C.evaluate(_profile({}, acknowledged="iso_exercise"))


def test_invalid_state_value_hard_errors():
    """'California' silently missing the CA note is the exact VALUE-validation
    trap the repo standard exists for."""
    p = _profile()
    p["address"]["state"] = "California"
    with pytest.raises(C.CapabilityError, match="2-letter USPS"):
        C.evaluate(p)


def test_non_usps_two_letter_code_hard_errors():
    """Codex P2: 'ZZ' (corrupt) or 'CN' (typo for CA) must not get a confident
    bracket-estimate note for a nonexistent state — membership is enforced."""
    for bad in ("ZZ", "CN", "XX"):
        with pytest.raises(C.CapabilityError, match="not a USPS"):
            C.evaluate(_profile(state=bad))


def test_usps_territory_codes_accepted():
    r = C.evaluate(_profile(state="PR"))
    assert "(PR)" in r.state_note
    assert "ESTIMATE only" in r.state_note


# --------------------------------------------------------------------------
# state note + manifest (C2)
# --------------------------------------------------------------------------

def test_state_ca_noted_in_depth():
    r = C.evaluate(_profile(state="CA"))
    assert "Form 540" in r.state_note
    assert r.limitations[0] == r.state_note


def test_state_other_noted_estimate_only():
    r = C.evaluate(_profile(state="TX"))
    assert "ESTIMATE only" in r.state_note
    assert "(TX)" in r.state_note


def test_state_missing_noted():
    p = _profile()
    p["address"] = {"state": None}
    r = C.evaluate(p)
    assert "none configured" in r.state_note
    r2 = C.evaluate({"tax_year": 2025, "flags": {}})
    assert "none configured" in r2.state_note


def test_manifest_contains_manual_items():
    r = C.evaluate(_profile({"has_education_expenses": True}))
    assert any("MANUAL WORKSHEET" in line and "8863" in line
               for line in r.limitations)


def test_blocking_items_do_not_appear_in_manifest():
    """A blocking item must gate, not slide into the manifest as if handled."""
    r = C.evaluate(_profile({"has_rental_income": True}))
    assert not any("Schedule E" in line for line in r.limitations)


# --------------------------------------------------------------------------
# renderer
# --------------------------------------------------------------------------

def test_render_blocked_shows_options_and_ids():
    md = C.render_markdown(C.evaluate(_profile({"has_rental_income": True})))
    assert "⛔ BLOCKED" in md
    assert "`rental_income`" in md
    assert "acknowledged_limitations" in md  # the unblock instructions


def test_render_clean_says_proceed():
    md = C.render_markdown(C.evaluate(_profile()))
    assert "No blocking situations" in md


def test_registry_rule_ids_unique():
    ids = [r.rule_id for r in C.RULES]
    assert len(ids) == len(set(ids))


def test_registry_flags_exist_in_profile_template():
    """Every rule flag must be a real interview flag — a rule keyed on a flag
    /tax-setup never sets is dead code (the expected_sources lesson)."""
    template = json.loads(
        (REPO / "agents/templates/taxpayer_profile_template.json").read_text())
    for rule in C.RULES:
        assert rule.flag in template["flags"], rule.flag


def test_registry_flags_asked_by_setup_interview():
    """Template presence is not enough — the /tax-setup interview must actually
    ASK the question that sets each rule flag (Codex P2: lives_abroad existed
    in the template but no interview prompt ever set it, making the FEIE rule
    dead code for the standard workflow). The skill annotates each question
    with the literal flag name, so this greps for it."""
    setup_md = (REPO / "agents/skills/tax-setup.md").read_text()
    for rule in C.RULES:
        assert rule.flag in setup_md, (
            f"rule {rule.rule_id!r} keys on flags.{rule.flag}, but "
            "agents/skills/tax-setup.md never mentions that flag — the "
            "interview cannot set it and the rule is dead code")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _run_cli(workspace, *extra):
    return subprocess.run(
        [sys.executable, str(REPO / "agents/lib/capabilities.py"),
         "--workspace", str(workspace), *extra],
        capture_output=True, text=True)


def _write_profile(tmp_path, profile):
    (tmp_path / "taxpayer_profile.json").write_text(json.dumps(profile))


def test_cli_exit_zero_when_clean(tmp_path):
    _write_profile(tmp_path, _profile())
    res = _run_cli(tmp_path)
    assert res.returncode == 0
    assert "No blocking situations" in res.stdout


def test_cli_exit_two_when_blocked(tmp_path):
    _write_profile(tmp_path, _profile({"has_rental_income": True}))
    res = _run_cli(tmp_path)
    assert res.returncode == 2
    assert "BLOCKED" in res.stdout


def test_cli_acknowledge_flag_unblocks(tmp_path):
    _write_profile(tmp_path, _profile({"has_rental_income": True}))
    res = _run_cli(tmp_path, "--acknowledge", "rental_income")
    assert res.returncode == 0


def test_cli_json_output_carries_manifest(tmp_path):
    _write_profile(tmp_path, _profile({"has_education_expenses": True}, state="CA"))
    res = _run_cli(tmp_path, "--json")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert any("8863" in line for line in data["limitations"])
    assert data["blocking"] == []


def test_cli_missing_profile_errors(tmp_path):
    res = _run_cli(tmp_path)
    assert res.returncode == 1
    assert "does not exist" in res.stderr


def test_cli_malformed_profile_errors(tmp_path):
    (tmp_path / "taxpayer_profile.json").write_text("{not json")
    res = _run_cli(tmp_path)
    assert res.returncode == 1
    assert "malformed" in res.stderr


def test_cli_unknown_acknowledge_errors(tmp_path):
    _write_profile(tmp_path, _profile({"has_iso_exercise": True}))
    res = _run_cli(tmp_path, "--acknowledge", "not_a_rule")
    assert res.returncode == 1
    assert "unknown acknowledged" in res.stderr
