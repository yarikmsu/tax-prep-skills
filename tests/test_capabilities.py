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
    """A post-migration profile: the gate-era flags (incl.
    has_nonresident_source_income, Codex round 3) are explicitly present
    (False) as /tax-setup writes them — absence fails closed."""
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
    ("has_nonresident_source_income", "nonresident_source_income"),
    ("has_household_employees", "household_employees"),
    ("has_dependent_unearned_income", "kiddie_tax"),
    ("has_pfic_taxable_event", "pfic_taxable_event"),
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
    ("has_unreported_or_allocated_tips", "unreported_allocated_tips"),
    ("lives_abroad", "feie"),
    # has_foreign_mutual_funds → pfic is MANUAL, but it now REQUIRES the
    # has_pfic_taxable_event answer (else the fail-closed guard blocks) — so it
    # can't ride the bare-single-flag parametrization; see
    # test_buy_and_hold_pfic_does_not_block / test_pfic_manifest_line_carries_*.
    ("has_foreign_financial_account", "foreign_asset_reporting"),
    ("has_sanctioned_blocked_asset", "sanctioned_blocked_asset"),
    ("has_foreign_bond", "foreign_bond_oid_interest"),
    # H1 life-event rules — all MANUAL, none block.
    ("married_this_year", "marriage_year"),
    ("divorced_this_year", "divorce_year"),
    ("sold_primary_home", "home_sale"),
    ("received_inheritance", "inheritance"),
    ("has_pre2019_alimony", "alimony_pre2019"),
    ("has_post2018_alimony", "alimony_post2018_state"),
    ("has_adoption_expenses", "adoption_credit"),
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


# --------------------------------------------------------------------------
# Foreign funds / PFIC + sanctioned holdings (open-question #3 slice)
# --------------------------------------------------------------------------

def test_pfic_taxable_event_is_acknowledgeable():
    r = C.evaluate(_profile({"has_pfic_taxable_event": True},
                            acknowledged=["pfic_taxable_event"]))
    assert r.blocking == ()
    assert [f.rule_id for f in r.acknowledged] == ["pfic_taxable_event"]
    assert any("Form 8621" in line for line in r.limitations)


def test_buy_and_hold_pfic_does_not_block():
    """The load-bearing distinction: holding a foreign fund (has_foreign_mutual_
    funds) with the event question EXPLICITLY answered no (buy-and-hold) is the
    reporting-only case — it must stay a MANUAL note (pfic), never a block. Only a
    taxable EVENT blocks. This is what keeps a small buy-and-hold holder from
    being over-blocked."""
    r = C.evaluate(_profile({"has_foreign_mutual_funds": True,
                             "has_pfic_taxable_event": False}))
    assert r.blocking == ()
    assert "pfic" in [f.rule_id for f in r.manual]
    assert "pfic_taxable_event" not in [f.rule_id for f in r.manual]


def test_foreign_funds_without_event_answer_fails_closed():
    """Codex P1: a profile holding foreign funds (has_foreign_mutual_funds) whose
    has_pfic_taxable_event is UNANSWERED (a pre-flag profile) must BLOCK — absence
    is not 'no', or a PFIC distribution/sale would slip through with only the
    manual note and understate §1291 tax."""
    r = C.evaluate(_profile({"has_foreign_mutual_funds": True}))  # event flag absent
    assert "unanswered_pfic_event" in [f.rule_id for f in r.blocking]


def test_unanswered_pfic_event_is_not_acknowledgeable():
    # Like the other migration guards, the remedy is answering the question — an
    # acknowledgment must not unblock it. (unanswered_pfic_event is not a RULES id,
    # so it cannot even appear in acknowledged_limitations without hard-erroring.)
    with pytest.raises(C.CapabilityError):
        C.evaluate(_profile({"has_foreign_mutual_funds": True},
                            acknowledged=["unanswered_pfic_event"]))


def test_no_foreign_funds_no_pfic_event_block():
    # The guard only fires when foreign funds are held — a clean profile is unaffected.
    r = C.evaluate(_profile())
    assert "unanswered_pfic_event" not in [f.rule_id for f in r.blocking]


def test_foreign_asset_and_sanctioned_are_manual_not_block():
    r = C.evaluate(_profile({"has_foreign_financial_account": True,
                             "has_sanctioned_blocked_asset": True}))
    assert r.blocking == ()
    ids = {f.rule_id for f in r.manual}
    assert {"foreign_asset_reporting", "sanctioned_blocked_asset"} <= ids
    # The sanctioned note must WITHHOLD the loss (never auto-claim) and carry the
    # verified §901(j)/treaty guidance.
    assert any("worthless" in line.lower() and "§901(j)" in line
               for line in r.limitations)


def test_clean_profile_has_no_foreign_fund_findings():
    """US-domiciled funds / US-broker custody / no sanctions → the interview
    leaves all three flags False → none of the new rules fire."""
    r = C.evaluate(_profile())
    ids = {f.rule_id for f in r.blocking + r.manual}
    assert ids.isdisjoint(
        {"pfic_taxable_event", "foreign_asset_reporting", "sanctioned_blocked_asset"})


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
    # NY (not TX): TX is now in _NO_INCOME_TAX_STATES and gets the
    # affirmative no-tax note (see TestNoIncomeTaxStateNote) — this test
    # needs a state that still falls through to the bracket-based estimate.
    r = C.evaluate(_profile(state="NY"))
    assert "ESTIMATE only" in r.state_note
    assert "(NY)" in r.state_note


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
    setup_md = (REPO / ".agents/skills/tax-setup/SKILL.md").read_text()
    for rule in C.RULES:
        assert rule.flag in setup_md, (
            f"rule {rule.rule_id!r} keys on flags.{rule.flag}, but "
            ".agents/skills/tax-setup/SKILL.md never mentions that flag — the "
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


# --------------------------------------------------------------------------
# H1 life-event rules
# --------------------------------------------------------------------------

def test_life_event_flags_absent_on_legacy_profile_is_harmless():
    """H1 flags are deliberately NOT gate-era-required: a legacy profile
    that predates the life-event interview must still pass (a missed
    manual-worksheet line is a manifest omission, not a materially wrong
    return)."""
    p = _profile()
    for f in ("married_this_year", "divorced_this_year", "sold_primary_home",
              "received_inheritance", "has_pre2019_alimony",
              "has_adoption_expenses"):
        assert f not in p["flags"]
        assert f not in C._REQUIRED_GATE_FLAGS
    r = C.evaluate(p)
    assert r.blocking == ()
    assert r.manual == ()


def test_home_sale_manifest_line_carries_121_and_1099s():
    r = C.evaluate(_profile({"sold_primary_home": True}))
    lines = "\n".join(r.limitations)
    assert "MANUAL WORKSHEET" in lines
    assert "§121" in lines or "121" in lines
    assert "1099-S" in lines


def test_pfic_manifest_line_carries_8621_and_1291():
    # K3 (G3): a foreign-fund holder's manifest must name Form 8621 and the
    # punitive §1291 default so the PFIC obligation isn't silently missed.
    # (Event question answered no → buy-and-hold reporting-only case.)
    r = C.evaluate(_profile({"has_foreign_mutual_funds": True,
                             "has_pfic_taxable_event": False}))
    assert r.blocking == ()
    lines = "\n".join(r.limitations)
    assert "8621" in lines
    assert "1291" in lines


def test_marriage_year_notes_community_property_8958():
    r = C.evaluate(_profile({"married_this_year": True}))
    assert any("8958" in line for line in r.limitations)


def test_pre2019_alimony_notes_decree_date_rule():
    r = C.evaluate(_profile({"has_pre2019_alimony": True}))
    lines = "\n".join(r.limitations)
    assert "pre-2019" in lines.lower() or "Pre-2019" in lines
    assert "recipient" in lines and "payer" in lines


def test_multiple_life_events_all_surface():
    r = C.evaluate(_profile({"divorced_this_year": True,
                             "received_inheritance": True,
                             "has_adoption_expenses": True}))
    ids = {f.rule_id for f in r.manual}
    assert {"divorce_year", "inheritance", "adoption_credit"} <= ids
    assert r.blocking == ()


def test_life_event_notes_carry_reviewer_corrections():
    """Review round: the manifest text itself is the deliverable for MANUAL
    rules — pin the statements a reviewer corrected so they can't regress."""
    # Marriage: married-at-year-end can still be HoH via §7703(b).
    r = C.evaluate(_profile({"married_this_year": True}))
    marriage = "\n".join(r.limitations)
    assert "considered-unmarried" in marriage
    assert "7703" in marriage
    # Inheritance: IRD (§691) is taxable with no §1014 step-up.
    r = C.evaluate(_profile({"received_inheritance": True}))
    inherit = "\n".join(r.limitations)
    assert "691" in inherit and "1014" in inherit
    # Alimony: post-2018 modification can flip treatment — decree date alone
    # does not decide.
    r = C.evaluate(_profile({"has_pre2019_alimony": True}))
    alimony = "\n".join(r.limitations)
    assert "MODIFIED after 2018" in alimony
    # Home sale: once-every-2-years lookback + $500K joint conditions.
    r = C.evaluate(_profile({"sold_primary_home": True}))
    home = "\n".join(r.limitations)
    assert "once every 2 years" in home
    assert "both spouses" in home


# --------------------------------------------------------------------------
# Codex round (PR #36): community-property MFS + post-2018 alimony
# --------------------------------------------------------------------------

def test_mfs_community_property_state_flagged_without_any_flag():
    """Form 8958 applies to EVERY MFS filer in a community-property state —
    a long-married MFS couple (no married_this_year) must still get the
    manifest line."""
    p = _profile(state="CA")
    p["filing_status"] = "MFS"
    r = C.evaluate(p)
    assert r.blocking == ()
    ids = [f.rule_id for f in r.manual]
    assert "community_property_mfs" in ids
    assert any("8958" in line and "BOTH returns" in line
               for line in r.limitations)


def test_mfs_common_law_state_not_flagged():
    p = _profile(state="NY")
    p["filing_status"] = "MFS"
    r = C.evaluate(p)
    assert "community_property_mfs" not in [f.rule_id for f in r.manual]


def test_mfj_community_property_state_not_flagged():
    r = C.evaluate(_profile(state="CA"))  # default MFJ
    assert "community_property_mfs" not in [f.rule_id for f in r.manual]


def test_mfs_without_address_not_flagged():
    p = _profile()
    p["filing_status"] = "MFS"
    del p["address"]
    r = C.evaluate(p)
    assert "community_property_mfs" not in [f.rule_id for f in r.manual]


def test_post2018_alimony_notes_ca_nonconformity():
    r = C.evaluate(_profile({"has_post2018_alimony": True}))
    lines = "\n".join(r.limitations)
    assert "CA does" in lines and "NOT conform" in lines
    assert "Schedule CA" in lines


class TestNonresidentSourceIncome:
    """A full-year resident (esp. a no-tax-state resident like FL) with
    out-of-state W-2/K-1/rental income has real nonresident exposure that
    `moved_states_this_year`/`part_year_residency` does not catch."""

    def test_flag_true_blocks(self):
        r = C.evaluate(_profile({"has_nonresident_source_income": True}))
        assert "nonresident_source_income" in [f.rule_id for f in r.blocking]

    def test_flag_false_not_present(self):
        r = C.evaluate(_profile({"has_nonresident_source_income": False}))
        assert "nonresident_source_income" not in [f.rule_id for f in r.blocking]
        assert "nonresident_source_income" not in [f.rule_id for f in r.manual]
        assert "unanswered_gate_questions" not in [f.rule_id for f in r.blocking]

    def test_flag_absent_fails_closed(self):
        """Codex round 3 (state-engine-florida PR #65, P1): has_nonresident_
        source_income now joins _REQUIRED_GATE_FLAGS — reversing the earlier
        judgment call for consistency with moved_states_this_year et al. An
        ABSENT key (not the helper's default-False) must block via the
        generic unanswered_gate_questions migration guard with a fix-the-
        profile message, exactly like the original gate-era five — a full-
        year no-tax-state resident's nonresident exposure is the same class
        of load-bearing unsupported situation, not a benign default."""
        p = _profile()
        del p["flags"]["has_nonresident_source_income"]
        r = C.evaluate(p)
        ids = [f.rule_id for f in r.blocking]
        assert "unanswered_gate_questions" in ids
        assert "nonresident_source_income" not in ids
        blocker = next(f for f in r.blocking if f.rule_id == "unanswered_gate_questions")
        assert "has_nonresident_source_income" in blocker.note

    def test_flag_null_fails_closed(self):
        """A present-but-null value is an unanswered question too, not an
        explicit 'no' — mirrors test_null_gate_flag_counts_as_unanswered."""
        r = C.evaluate(_profile({"has_nonresident_source_income": None}))
        assert "unanswered_gate_questions" in [f.rule_id for f in r.blocking]

    def test_distinct_from_part_year_residency(self):
        # A full-year FL resident (no move) with nonresident source income
        # must still block — part_year_residency alone would miss this.
        r = C.evaluate(_profile({"moved_states_this_year": False,
                                 "has_nonresident_source_income": True},
                                state="FL"))
        ids = [f.rule_id for f in r.blocking]
        assert "nonresident_source_income" in ids
        assert "part_year_residency" not in ids


class TestNoIncomeTaxStateNote:
    def _profile(self, code, tax_year=2025):
        return {"flags": {}, "address": {"state": code}, "tax_year": tax_year}

    def test_fl_gets_affirmative_no_tax_note(self):
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("FL"))
        assert "no individual income tax" in note
        assert "ESTIMATE" not in note
        # Round-9 P2: FL's sales-tax note must say the election is
        # AUTOMATED (compute_sales_tax_deduction actually runs for FL) —
        # distinct from the manual instruction non-FL no-tax states get.
        assert "computed automatically" in note
        assert "MANUALLY" not in note

    def test_tx_gets_manual_sales_tax_note_not_fl_automated_wording(self):
        # Round-9 P2: reusing FL's "handled on the federal side" wording for
        # a non-FL no-tax state implied the sales-tax election was taken
        # care of, when /tax-compute explicitly SKIPS the automated
        # computation for every no-tax state other than FL. TX must get an
        # explicit MANUAL-computation instruction instead.
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("TX"))
        assert "no individual income tax" in note
        assert "MANUALLY" in note
        assert "computed automatically" not in note

    def test_tx_note_does_not_name_florida_forms(self):
        # Codex round-17 P2: the property/use-tax parenthetical named FL's
        # specific forms (DR-405/DR-15MO) for EVERY no-tax state — a TX or
        # NV taxpayer's manifest note is misleading when it cites Florida
        # forms they have no nexus with. Non-FL no-tax states get a
        # state-neutral caveat instead.
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("TX"))
        assert "DR-405" not in note
        assert "DR-15MO" not in note
        note_nv = _state_note(self._profile("NV"))
        assert "DR-405" not in note_nv
        assert "DR-15MO" not in note_nv

    def test_fl_note_still_names_florida_forms(self):
        # Regression: FL keeps its specific form citations. Round-23 P2:
        # the use-tax form differs by dealer registration, so the static
        # manifest line names BOTH (DR-15MO, or DR-15 for registered
        # dealers) — it must never contradict the registration-tailored
        # use_tax_note in the same PDF.
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("FL"))
        assert "DR-405" in note
        assert "DR-15MO" in note
        assert "DR-15 for registered dealers" in note

    def test_ak_gets_local_table_note_not_state_table_wording(self):
        # Codex round-23 P2: Alaska has NO statewide sales tax but IS on
        # the IRS Optional LOCAL Sales Tax Table list (Schedule A
        # instructions) — the generic state-table wording would have an AK
        # filer look up a statewide table that doesn't exist and omit the
        # locality amount, understating line 5a.
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("AK"))
        assert "no individual income tax" in note
        assert "MANUALLY" in note
        assert "Optional LOCAL Sales Tax Tables" in note
        assert "Optional State Sales Tax Tables" not in note
        assert "computed automatically" not in note

    def test_tn_gets_affirmative_no_tax_note(self):
        # TN's Hall income tax (interest & dividends only) was fully
        # repealed effective TY2021 — TN now gets the same affirmative
        # no-tax note as FL, not the bracket-based ESTIMATE text, for
        # TY2021 and later.
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("TN", tax_year=2025))
        assert "no individual income tax" in note
        assert "ESTIMATE" not in note

    def test_tn_2021_boundary_gets_affirmative_no_tax_note(self):
        # TY2021 is the FIRST repeal year (Hall tax fully gone) — >= boundary.
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("TN", tax_year=2021))
        assert "no individual income tax" in note

    def test_tn_post_repeal_note_references_both_state_and_local_tables(self):
        # Codex round-26 P2 (AK's twin): post-repeal TN is on the IRS
        # Optional LOCAL Sales Tax Table list too, but unlike AK it also
        # has a statewide sales tax — the worksheet path is state table
        # PLUS local table, so a state-table-only note omits the locality
        # amount and understates line 5a.
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("TN", tax_year=2025))
        assert "MANUALLY" in note
        assert "Optional State Sales Tax Tables" in note
        assert "Optional LOCAL Sales Tax Tables" in note
        assert "computed automatically" not in note
        assert "ESTIMATE" not in note

    def test_tn_2020_gets_hall_specific_note_not_estimate(self):
        # Pre-repeal: TN's Hall tax on interest/dividends was still live in
        # TY2020, so a confident "no state return exists" note would
        # silently UNDERSTATE a real filing obligation. But the generic
        # bracket-based ESTIMATE note over-corrects the other way (the
        # note-level twin of the round-23 consumer guards): it invites
        # estimating tax on ALL income when the Hall regime taxed ONLY
        # interest and dividends — a TY2020 TN wage earner would see a
        # fabricated liability. Pre-repeal TN gets a Hall-specific
        # not-modeled note naming Form INC 250 instead.
        from agents.lib.capabilities import _state_note
        note = _state_note(self._profile("TN", tax_year=2020))
        assert "ESTIMATE" not in note
        assert "interest and dividends" in note
        assert "INC 250" in note
        assert "NOT modeled" in note

    def test_tn_missing_tax_year_gets_hall_specific_note(self):
        # An unknown/missing tax_year must NOT default to "repealed" —
        # that would risk understating a pre-2021 Hall-tax obligation. It
        # falls to the same Hall-specific note (never the all-income
        # bracket ESTIMATE).
        from agents.lib.capabilities import _state_note
        p = {"flags": {}, "address": {"state": "TN"}}
        note = _state_note(p)
        assert "ESTIMATE" not in note
        assert "interest and dividends" in note

    def test_nh_keeps_estimate_note(self):
        # NH's interest & dividends tax was repealed effective TY2025, but
        # v1 has not confirmed there's no residual filing obligation for
        # every target tax year, so NH deliberately stays out of
        # _NO_INCOME_TAX_STATES and keeps the safer estimate note.
        from agents.lib.capabilities import _state_note
        assert "ESTIMATE" in _state_note(self._profile("NH"))

    def test_wa_keeps_estimate_note(self):
        # WA is deliberately NOT in the no-income-tax set: the capital-gains
        # excise (RCW 82.87) means "no tax at all" would be a false promise.
        from agents.lib.capabilities import _state_note
        assert "ESTIMATE" in _state_note(self._profile("WA"))

    def test_ca_note_unchanged(self):
        from agents.lib.capabilities import _state_note
        assert "Form 540" in _state_note(self._profile("CA"))


class TestTaxSetupNoIncomeTaxAllowlistDoc:
    """Codex round-2 P1: the old open-ended tax-setup.md instruction ("if
    research shows the state levies NO individual income tax, use the FL
    pattern") would catch WASHINGTON — commonly (and wrongly) described as
    no-income-tax — and silently zero its real capital-gains excise (RCW
    82.87). The interview instruction must mirror the closed
    `capabilities._NO_INCOME_TAX_STATES` allowlist, not an LLM's own
    research judgment."""

    def _setup_md(self):
        return (REPO / ".agents/skills/tax-setup/SKILL.md").read_text()

    def test_open_ended_instruction_removed(self):
        setup_md = self._setup_md()
        assert "if research shows the state levies NO individual income tax" \
            not in setup_md

    def test_allowlist_states_all_documented(self):
        from agents.lib.capabilities import _NO_INCOME_TAX_STATES
        setup_md = self._setup_md()
        for code in _NO_INCOME_TAX_STATES:
            assert code in setup_md, (
                f"{code} is in capabilities._NO_INCOME_TAX_STATES but not "
                "documented in tax-setup.md's type:none allowlist")

    def test_wa_never_type_none(self):
        setup_md = self._setup_md()
        assert "WA is NEVER" in setup_md
        assert "RCW 82.87" in setup_md  # WA capital-gains excise citation
        # WA must never appear inside the closed allowlist itself
        from agents.lib.capabilities import _NO_INCOME_TAX_STATES
        assert "WA" not in _NO_INCOME_TAX_STATES

    def test_nh_excluded_pending_confirmation(self):
        setup_md = self._setup_md()
        assert "NH is excluded" in setup_md
        from agents.lib.capabilities import _NO_INCOME_TAX_STATES
        assert "NH" not in _NO_INCOME_TAX_STATES

    def test_tn_year_gate_documented(self):
        setup_md = self._setup_md()
        assert "TN, but only for tax_year >= 2021" in setup_md

    def test_source_of_truth_pointer_present(self):
        # Both the doc allowlist and the code constant must move together —
        # the doc names the constant so a future edit to one prompts a check
        # of the other.
        setup_md = self._setup_md()
        assert "_NO_INCOME_TAX_STATES" in setup_md
        assert "_tn_hall_repealed" in setup_md or "_TN_HALL_REPEAL_YEAR" in setup_md
