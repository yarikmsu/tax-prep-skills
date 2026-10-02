"""Tests for the capability matrix / scope guard (agents/lib/capabilities.py).

C1: unsupported situations block /tax-compute unless explicitly acknowledged.
C2: triggered items + state scope become the meta.limitations manifest.
"""
import importlib.util
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import capabilities as C  # noqa: E402
from agents.lib.capabilities import (  # noqa: E402
    _NY_ABODE_MONTHS_BEFORE_2022, _NY_ABODE_MONTHS_FROM_2022,
)
from agents.lib.tax_law_fetcher import (  # noqa: E402
    _source_host_authoritative,
)
from tests.skill_text import (  # noqa: E402
    SKILLS_DIR, skill_names, skill_text,
)

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
    ("moved_abroad_this_year", "move_abroad"),
    ("bona_fide_territory_resident", "territory_resident"),
    ("statutory_resident_of_another_state", "statutory_residency_second_state"),
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


def test_a_profile_that_answered_only_the_states_question_blocks():
    """AA17: every profile written before the abroad question answered only
    whether the taxpayer moved STATES, and an emigrant answers that one "no"
    truthfully. So the new flag is required, not defaulted: a profile holding
    every other gate answer, and no answer to this one, is stopped until it is
    asked. Were it defaulted to False, the June emigrant this item is about
    would pass on a stale answer to a narrower question."""
    p = _profile({})
    del p["flags"]["moved_abroad_this_year"]
    r = C.evaluate(p)
    assert _blocking_ids(r) == ["unanswered_gate_questions"]
    blocker = r.blocking[0]
    assert "moved_abroad_this_year" in blocker.note
    assert "moved_states_this_year" not in blocker.note


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
    """A valid code, not a malformed one: it reaches the territory rule (AA18)
    and its own note, rather than hard-erroring as unknown."""
    r = C.evaluate(_profile(state="PR"))
    assert "(PR)" in r.state_note
    assert _blocking_ids(r) == ["territory_resident"]


# --------------------------------------------------------------------------
# state note + manifest (C2)
# --------------------------------------------------------------------------

def test_state_ca_noted_in_depth():
    r = C.evaluate(_profile(state="CA"))
    assert "Form 540" in r.state_note
    assert r.limitations[0] == r.state_note


def test_state_other_noted_estimate_only():
    # CO: not a no-income-tax state, not one of the two modeled in depth.
    # This test used NY until the IT-201 module shipped, at which point NY
    # started returning its own depth note — the state that "falls through"
    # is a moving target and the comment naming one is the whole reason this
    # keeps having to be re-pointed.
    r = C.evaluate(_profile(state="CO"))
    assert "ESTIMATE only" in r.state_note
    assert "(CO)" in r.state_note


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
    with the literal flag name, so this greps for it.

    Reads the whole tax-setup tree: the question groups live in
    `references/interview.md`, one link-hop from SKILL.md. Scoping this to
    SKILL.md after that move would pass because the questions are gone, which
    is indistinguishable from their never having existed."""
    setup_md = skill_text("tax-setup")
    for rule in C.RULES:
        assert rule.flag in setup_md, (
            f"rule {rule.rule_id!r} keys on flags.{rule.flag}, but the "
            ".agents/skills/tax-setup/ tree never mentions that flag — the "
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
        """The whole tax-setup tree — the state question lives in
        `references/interview.md` since progressive disclosure."""
        return skill_text("tax-setup")

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


# --- Residency guardrails (Z7): the figure registry -------------------------
#
# AGENTS.md §2 forbids a hardcoded rate, bracket, cap or threshold. A day count
# and an abode period ARE thresholds, and the skills have to state them — a
# taxpayer cannot answer "am I a statutory resident of New York?" without being
# told what the test is. They ride the same narrow §2 exception as
# `tax-safe-harbor`'s §6654 restatement, on the same terms: a closed set, every
# member sourced and dated where it is stated once.
#
# ONE POSITIVE ASSERTION, not a list of exemptions. The first version of this
# registry had two scopes and an eleven-entry `NOT_A_THRESHOLD` mask, and it was
# the enumeration-with-no-stopping-rule the PII gate had just been rebuilt to
# escape: four of the eleven masks were dead on arrival, the sentence scope only
# fired on three literal doctrine phrases, and the ten-month figure could be
# reintroduced in three of the five files that state it with every test green.
#
# What replaces it is the shape `test_portability.py`'s safe-harbor lint already
# uses — trigger on a POSITIVE marker rather than exempt everything that is not
# one. There, a `$` beside a limit word. Here, a number with a UNIT (days or
# months) within a short span of a residency NOUN. Every legitimate figure in
# these files carries both; a wash-sale window, a §121 holding period and an
# extension deadline carry the unit and never the noun, so they fall out for
# free instead of needing an exemption apiece. It covers everything that can
# state a figure — every skill, the engine's rule text, and the profile template
# — rather than two hand-picked files.
GUARDRAILS = REPO / "docs" / "contributing" / "residency-guardrails.md"

# The closed set: the literal spellings a residency figure may take.
RESIDENCY_FIGURES = {
    # Not "183 days": the statute says "more than 183" and the Department says
    # "184 or more", and they are the same rule — but registering both would let
    # an off-by-one edit of the skills' own sentence pass as a spelling change.
    # The doc's table carries the statutory phrasing; the skills state one.
    "184 days": "NY statutory residency, day prong",
    "10 months": "abode period, tax year 2022 onward (Audit Division policy)",
    "11 months": "abode period, before tax year 2022",
}

# A residency NOUN near a number-with-unit is what makes it a residency figure.
# A residency-TEST noun, not merely `residen`: `/tax-compute` states §6654's
# prior-year precondition as "the prior year covered a full 12 months AND the
# taxpayer was a US citizen/resident all year", and a bare `residen` made that
# a residency figure. A check that cries wolf on correct prose gets deleted
# rather than fixed, so the trigger names the doctrine.
_RESIDENCY_NOUN = (r"statutory|abode|domicile|physically in|New York|NYC|"
                   r"Yonkers|residency test|days present")
_NUMBER_WITH_UNIT = r"(?:\d+|ten|eleven|twelve)\+?\s*(?:or more\s+)?(?:days?|months?)"
_FIGURE_NEAR_NOUN = re.compile(
    rf"(?:{_RESIDENCY_NOUN})[^.\n]{{0,80}}?({_NUMBER_WITH_UNIT})"
    rf"|({_NUMBER_WITH_UNIT})[^.\n]{{0,80}}?(?:{_RESIDENCY_NOUN})", re.I)


def _residency_figures_in(text: str) -> "list[str]":
    """Every number-with-unit standing near a residency noun.

    Whitespace is flattened FIRST. The span between the noun and the figure is
    bounded by `[^.\n]`, and markdown wraps a sentence across lines — a
    mutation proved it: changing the abode period to nine months in the
    interview went green purely because the wrap fell between `abode` and the
    figure. Sentences still bound the span; line breaks no longer do.
    """
    flat = re.sub(r"\s+", " ", text)
    return [(a or b).strip() for a, b in _FIGURE_NEAR_NOUN.findall(flat)]


def _normalised(figure: str) -> str:
    """`184+ NY days` and `184 or more days` are the same figure as `184 days`.

    Spelling variation is not what this registry is for; a DIFFERENT NUMBER is.
    Without this the set would have to carry every phrasing, which is the
    enumeration again, one level down.
    """
    word = {"ten": "10", "eleven": "11", "twelve": "12"}
    figure = re.sub(r"\s+", " ", figure.lower()).replace("+", "")
    figure = figure.replace(" or more", "").replace("day", "days").rstrip("s")
    figure = figure.replace("dayss", "days").replace("monthss", "months")
    number, _, unit = figure.partition(" ")
    unit = "days" if unit.startswith("day") else "months"
    return f"{word.get(number, number)} {unit}"


_ALLOWED_FIGURES = {_normalised(f) for f in RESIDENCY_FIGURES}

# Everywhere a residency figure could be stated. Not a curated subset: the
# two copies that regressed silently under the first registry were the engine's
# rule text and the profile template, neither of which was a skill file.
def _residency_scanned() -> "dict[str, str]":
    out = {f"skill:{name}": skill_text(name) for name in skill_names()}
    out["agents/lib/capabilities.py"] = (
        REPO / "agents" / "lib" / "capabilities.py").read_text()
    out["agents/templates/taxpayer_profile_template.json"] = (
        REPO / "agents" / "templates" / "taxpayer_profile_template.json"
    ).read_text()
    return out


@pytest.mark.parametrize("source", sorted(_residency_scanned()))
def test_no_unregistered_residency_figure(source):
    """The half that catches an ADDITION, and the half that caught nothing in
    the first version: the ten-month figure could come back in `tax-review`'s
    own SKILL.md, in the engine's rule text, or in the template comment, with
    every test green. All three are scanned now."""
    for figure in _residency_figures_in(_residency_scanned()[source]):
        assert _normalised(figure) in _ALLOWED_FIGURES, (
            f"{source} states {figure!r} beside a residency word and it is not "
            "in RESIDENCY_FIGURES. Add it to the closed set AND to the table in "
            "docs/contributing/residency-guardrails.md with its source, or fix "
            "the sentence")


def test_every_registered_figure_is_sourced_and_dated():
    """A figure with no source is one nobody can re-verify at season open, and
    §2's whole contract is that a stale number never passes as fresh. The date
    is PARSED, not shape-matched: `2000-99-99` is not a date."""
    doc = GUARDRAILS.read_text()
    # Any table row carrying a link, NOT rows from a named domain: filtering by
    # `tax.ny.gov` would stop seeing a figure sourced from another state's
    # department and report it as missing from a table it is in.
    rows = [ln for ln in doc.splitlines()
            if ln.startswith("|") and "](http" in ln]
    assert rows, "the guardrails doc no longer carries a sourced figure table"
    for figure, what in RESIDENCY_FIGURES.items():
        number = figure.split()[0]
        matching = [r for r in rows if number in r]
        assert matching, (
            f"{figure!r} ({what}) is in the closed set and the guardrails doc's "
            "table does not carry it — nothing states where it came from")
    for row in rows:
        url = re.search(r"\]\((https?://[^)\s]+)", row)
        assert url, f"a figure row names no source URL:\n  {row}"
        assert _source_host_authoritative(url.group(1)), (
            "a residency figure is sourced from a host AGENTS.md §2 does not "
            f"treat as authoritative:\n  {row}")
        stamp = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", row)
        assert stamp, (
            f"a figure row has no date it was read:\n  {row}")
        date.fromisoformat(stamp.group(1))


def test_the_abode_period_is_the_audit_division_figure():
    """The regression this registry exists for, and it went the OTHER way from
    the first write-up. `more than 10 months` was CORRECT: the Department's
    Nonresident Audit Guidelines moved Audit Division policy from eleven months
    to ten beginning with tax year 2022, while its published bulletin still
    says eleven. Stating eleven tells a taxpayer with a 10.5-month abode and 190
    New York days that New York does not have them — it does, and the flag then
    stays False so the gate never fires either. Both figures are stated, with
    the year that separates them."""
    text = skill_text("tax-review") + skill_text("tax-setup")
    assert "10 months" in text and "11 months" in text, (
        "the skills no longer state BOTH abode periods — the ten-month figure "
        "is the operative one and the eleven-month figure is what an amended "
        "return for a pre-2022 year needs")
    assert "2022" in text, (
        "the skills state the abode period with no year gating it, which makes "
        "one of the two figures silently wrong for every return")
    assert _NY_ABODE_MONTHS_FROM_2022 == 10 and _NY_ABODE_MONTHS_BEFORE_2022 == 11


def test_the_review_reaches_residency_on_every_return():
    """It lived inside the `type: "none"` branch, so a taxpayer leaving New York
    for any income-tax state reached none of it. Residency is a question about
    the state being LEFT; the destination's regime does not decide whether it
    gets asked."""
    body = (SKILLS_DIR / "tax-review" / "SKILL.md").read_text()
    # To the next SAME-LEVEL numbered step, or end of file. Stopping at the next
    # `###` of any depth let a `### 3.8.1. When to skip` subheading re-impose
    # the regime condition below the part this reads.
    step = re.search(r"^### 3\.8\..*?(?=^### \d+\. |\Z)", body, re.M | re.S)
    assert step, "tax-review lost its step 3.8 residency guardrails"
    region = step.group(0) + (SKILLS_DIR / "tax-review" / "references"
                              / "residency-review.md").read_text()
    assert "references/residency-review.md" in step.group(0), (
        "step 3.8 no longer links the residency reference")
    assert "EVERY return" in step.group(0), (
        "step 3.8 no longer says it is unconditional — the heading is where a "
        "skimming agent decides whether the step applies to the return in hand")
    assert not re.search(r'state\.type|type: "none"', region), (
        "the residency step has acquired a regime condition, which is the "
        "defect it was written to undo")


def test_the_interview_asks_for_the_counts_the_gate_reads():
    """A count nothing collects is a gate that never fires. The DOTTED key, not
    its last segment: `residency.days_present_by_state_v2` would satisfy a
    suffix match while orphaning the field the engine reads."""
    setup = skill_text("tax-setup")
    for key in ("residency.domicile_state",
                "residency.days_present_by_state",
                "residency.abode_months_by_state",
                "residency.telecommutes_for_out_of_state_employer"):
        assert key in setup, (
            f"the /tax-setup interview never names {key}, so the taxpayer is "
            "asked to conclude they are a statutory resident with nothing "
            "recorded behind the conclusion")
    review = skill_text("tax-review")
    for key in ("residency.days_present_by_state",
                "residency.abode_months_by_state"):
        assert key in review, (
            f"/tax-review never cross-checks {key} against the flag, so a "
            "taxpayer whose counts meet a test with the flag left False is not "
            "a finding anywhere — the dangerous direction")


def test_the_nyc_question_reaches_a_taxpayer_addressed_elsewhere():
    """It was asked only `If state is NY`, which structurally cannot reach the
    taxpayer this whole epic is about: a Florida address and a Manhattan
    apartment."""
    setup = skill_text("tax-setup")
    assert "If state is NY: are you a New York City resident" not in setup, (
        "the NYC question is address-gated again")
    assert re.search(r"If NY appears anywhere.*?nyc_resident", setup, re.S), (
        "the NYC question no longer keys on NY appearing in the residency "
        "counts, so an out-of-state statutory resident is never asked it")


# --- the residency counts are validated at the parse boundary ---------------


def _profile_with(residency, **extra):
    profile = {"tax_year": 2026, "filing_status": "single",
               "address": {"state": "CA"},
               "flags": {f: False for f in C._REQUIRED_GATE_FLAGS}}
    profile.update(extra)
    if residency is not None:
        profile["residency"] = residency
    return profile


@pytest.mark.parametrize("bad", [
    {"days_present_by_state": {"NewYork": 200}},      # not a USPS code
    {"days_present_by_state": {"NY": "a lot"}},       # type-valid, not a number
    {"days_present_by_state": {"NY": float("nan")}},  # every comparison is False
    {"days_present_by_state": {"NY": 400}},           # out of domain
    {"days_present_by_state": {"NY": -1}},
    {"abode_months_by_state": {"NY": 13}},
    {"abode_months_by_state": {"NY": True}},          # bool is an int subclass
    {"domicile_state": "Nu York"},
    {"days_present_by_state": [("NY", 200)]},
])
def test_a_malformed_residency_count_hard_errors(bad):
    """AGENTS.md §8: validate the VALUE at the parse boundary, not just the
    type. A misspelled key is the dangerous one — it makes the counts for the
    state that matters look ABSENT, so the reviewer finds nothing meeting a
    test and a flag-False return sails through."""
    with pytest.raises(C.CapabilityError):
        C.evaluate(_profile_with(bad))


def test_an_absent_residency_block_does_not_read_as_a_negative_answer():
    """It cannot BLOCK on absence — a profile with no second state is the
    ordinary case and blocking it is cry-wolf — so what has to hold is that
    nothing concludes from the silence either."""
    assert C.evaluate(_profile_with(None)).blocking == ()
    assert C.evaluate(_profile_with({})).blocking == ()


def test_the_counts_decide_new_yorks_test_not_the_taxpayers_arithmetic():
    """Collecting numbers buys nothing if the threshold is then applied in
    somebody's head. The flag is left FALSE in every case here."""
    meets = {"days_present_by_state": {"NY": 200},
             "abode_months_by_state": {"NY": 11}}
    report = C.evaluate(_profile_with(meets))
    assert any(f.rule_id == "statutory_residency_second_state"
               for f in report.blocking), (
        "counts that meet New York's test with the flag False produced no "
        "block — the return computes one resident state and is silently wrong "
        "by the whole of New York's")

    # BOTH prongs. Days in hotels is not statutory residency, and an empty
    # apartment is not either.
    for half in ({"days_present_by_state": {"NY": 200}},
                 {"abode_months_by_state": {"NY": 12}},
                 {"days_present_by_state": {"NY": 183},
                  "abode_months_by_state": {"NY": 12}},
                 {"days_present_by_state": {"NY": 200},
                  "abode_months_by_state": {"NY": 10}}):
        assert C.evaluate(_profile_with(half)).blocking == (), (
            f"{half} is not New York's test and it blocked anyway — a gate "
            "that fires on correct data teaches the taxpayer to reason past "
            "the next one")


def test_the_pre_2022_abode_period_is_the_longer_one():
    """An amended return for an earlier year is a real thing, and Audit policy
    was eleven months before tax year 2022."""
    counts = {"days_present_by_state": {"NY": 200},
              "abode_months_by_state": {"NY": 10.5}}
    assert C.evaluate(_profile_with(counts, tax_year=2026)).blocking, (
        "10.5 months in 2026 is more than the ten-month Audit policy and did "
        "not block")
    assert C.evaluate(_profile_with(counts, tax_year=2021)).blocking == (), (
        "10.5 months in 2021 is less than the eleven-month policy that applied "
        "then, and it blocked anyway")


def test_a_new_york_filer_is_not_blocked_by_their_own_state():
    """The rule is about a SECOND resident state. A New York filer is already
    computing a New York return."""
    counts = {"days_present_by_state": {"NY": 300},
              "abode_months_by_state": {"NY": 12}}
    profile = _profile_with(counts)
    profile["address"] = {"state": "NY"}
    assert C.evaluate(profile).blocking == ()


def test_the_statutory_residency_rule_is_distinct_from_its_neighbours():
    """Each catches a case the others cannot: `part_year_residency` a mid-year
    MOVE, `nonresident_source_income` income SOURCED elsewhere.

    `nyc_resident` was the third and is no longer a RULE at all — the New York
    module computes the city tax, so the flag became the city-tax switch. It
    is still checked below, from the required-gate list rather than the
    registry, because a flag that DRIVES a computation has to be answered for
    the same reason one that blocked did."""
    by_id = {r.rule_id: r for r in C.RULES}
    new = by_id["statutory_residency_second_state"]
    assert new.level == C.UNSUPPORTED, (
        "two resident states taxing the same income is not a MANUAL worksheet "
        "item — it changes the return")
    for other in ("part_year_residency", "nonresident_source_income"):
        assert by_id[other].flag != new.flag, (
            f"{other} and the statutory-residency rule share a flag, so one of "
            "them can never fire on its own")
    assert "nyc_resident" not in by_id, (
        "the NYC rule is back in the registry — it was deleted when the IT-201 "
        "module started computing the city tax, and a rule that blocks a "
        "return the engine can now produce is a gate crying wolf")
    assert "nyc_resident" in C._REQUIRED_GATE_FLAGS
    assert new.flag != "nyc_resident"


class TestTheNewYorkModuleIsDone:
    """The last Z1 slice: the block becomes a switch, and the depth note is
    allowed to say what it now says."""

    REPO = Path(__file__).resolve().parents[1]

    # --- the rule is gone, the flag is load-bearing -----------------------

    def test_the_nyc_rule_no_longer_blocks_a_return(self):
        """Owner ruling: a New York City taxpayer must never see the
        capability gate. A rule that blocks a return the engine can now
        produce is a gate crying wolf."""
        r = C.evaluate(_profile({"nyc_resident": True}))
        assert r.blocking == ()
        assert "nyc_resident" not in [rule.rule_id for rule in C.RULES]

    def test_the_flag_is_still_required_present(self):
        """It matters MORE than it did, not less: it used to block loudly and
        would now silently omit the city tax from a return that owes it."""
        assert "nyc_resident" in C._REQUIRED_GATE_FLAGS
        p = _profile()
        p["flags"].pop("nyc_resident")
        blocker = next(f for f in C.evaluate(p).blocking
                       if f.rule_id == "unanswered_gate_questions")
        assert "nyc_resident" in blocker.note

    def test_yonkers_is_two_flags_because_it_is_two_taxes(self):
        """The resident surcharge and the nonresident earnings tax have
        different populations — a taxpayer can owe either without the other —
        so one flag cannot stand for both."""
        for flag in ("yonkers_resident", "yonkers_nonresident_earnings"):
            assert flag in C._REQUIRED_GATE_FLAGS, flag
        template = json.loads(
            (self.REPO / "agents/templates/taxpayer_profile_template.json")
            .read_text())
        for flag in ("yonkers_resident", "yonkers_nonresident_earnings"):
            assert flag in template["flags"], flag

    def test_neither_yonkers_flag_blocks(self):
        """For the household the engine computes: a single filer, a whole-year
        resident. A joint Y-203 and a part-year resident are AA30's blocks."""
        for flag in ("yonkers_resident", "yonkers_nonresident_earnings"):
            p = _profile({flag: True, "yonkers_resident_full_year": True})
            p["filing_status"] = "single"
            assert C.evaluate(p).blocking == (), flag

    def test_every_required_gate_flag_is_asked_by_the_interview(self):
        """The registry test covers rule flags; these three have no rule, so
        nothing covered them — a required flag the interview never asks is a
        gate nobody can clear."""
        setup = skill_text("tax-setup")
        for flag in C._REQUIRED_GATE_FLAGS:
            assert flag in setup, (
                f"flags.{flag} must be answered explicitly, and the "
                ".agents/skills/tax-setup/ tree never mentions it")

    def test_the_interview_asks_both_yonkers_questions_separately(self):
        setup = skill_text("tax-setup")
        assert "Did you live in Yonkers?" in setup
        assert "yonkers_nonresident_earnings" in setup
        assert "two DIFFERENT taxes" in setup

    # --- the depth note ---------------------------------------------------

    def test_new_york_gets_a_depth_note_not_a_bracket_estimate(self):
        note = C.evaluate(_profile(state="NY")).state_note
        assert "computed in depth" in note
        assert "ESTIMATE only" not in note

    def test_the_note_names_what_is_computed(self):
        note = C.evaluate(_profile(state="NY")).state_note
        for claim in ("IT-201", "recapture", "IT-225", "IT-196", "§615",
                      "household credit", "New York City taxable income",
                      "New York City resident tax on line 47a",
                      "Yonkers resident surcharge and nonresident earnings "
                      "tax (lines 55 and 56)", "MCTMT"):
            assert claim in note, claim

    def test_the_note_names_what_is_NOT_computed(self):
        """As load-bearing as what it claims: a reader deciding how far to
        trust the return needs the inputs named, and the ones they are most
        likely to assume away are the City tax, the credits and the part-year
        return.

        The note once claimed "lines 47-58 for New York City, Yonkers and the
        MCTMT", and this test pinned that claim while nothing computed the City
        resident tax or the Yonkers lines. AA13 computes the City tax and AA12
        the Yonkers lines, so no IT-201 tax line is named as not computed, and
        the compute step must call both Yonkers functions rather than tell the
        agent to leave the lines out."""
        note = C.evaluate(_profile(state="NY")).state_note
        assert "IT-213" in note and "not computed" in note
        assert "IT-203" in note and "not modeled" in note
        assert "ASKED" in note and "zero" in note
        assert "NOT computed" not in note, (
            "a tax line is back on the not-computed list")
        ny_ref = (self.REPO / ".agents/skills/tax-compute/references"
                  / "state-return-ny.md").read_text()
        assert "compute_yonkers_resident_surcharge(" in ny_ref
        assert "compute_yonkers_nonresident_earnings_tax(" in ny_ref
        assert "Neither Yonkers amount is computed yet" not in ny_ref

    def test_every_form_the_note_names_as_an_input_really_is_one(self):
        """A depth note is only worth having if its list of inputs matches the
        skill's. The compute reference is where the non-goals are enumerated
        for the agent that writes the return."""
        note = C.evaluate(_profile(state="NY")).state_note
        ny_ref = (self.REPO / ".agents/skills/tax-compute/references"
                  / "state-return-ny.md").read_text()
        for form in ("IT-213", "IT-216", "IT-215", "IT-214", "IT-272",
                     "IT-203"):
            assert form in note and form in ny_ref, form

    def test_the_fallback_note_names_both_deep_states(self):
        note = C.evaluate(_profile(state="CO")).state_note
        assert "CA and NY" in note

    def test_california_is_untouched(self):
        note = C.evaluate(_profile(state="CA")).state_note
        assert "Form 540 computed in depth" in note

    # --- the /tax-verify line map ----------------------------------------

    def test_tax_verify_carries_an_it201_line_map(self):
        body = skill_text("tax-verify")
        for line in ("line_33_nyagi", "line_46_total_state_tax",
                     "line_54e_total_mctmt",
                     "line_58_total_city_and_yonkers",
                     "line_60_total_taxes", "line_80_amount_owed"):
            assert line in body, line

    def test_the_line_map_refuses_line_46_as_a_tie_out(self):
        """Lines 47-58 are a SEPARATE stack, and for a City resident they are
        often the larger half. A return can agree on line 46 to the cent and
        be thousands of dollars wrong on line 60."""
        body = skill_text("tax-verify")
        assert "Do not accept a line 46 match as a New York tie-out" in body
        assert "Line 39 is not the state tax" in body

    def test_the_line_map_says_line_59_null_is_not_agreement(self):
        body = skill_text("tax-verify")
        assert "never read the null as agreement" in body



# --------------------------------------------------------------------------
# The Earned Income Credit disclosure (AA1)
# --------------------------------------------------------------------------

def _eic_rule():
    return C._RULES_BY_ID["earned_income_credit"]


def test_the_eic_is_disclosed_and_does_not_block():
    """MANUAL, not UNSUPPORTED, and the level is the whole decision: the gap
    UNDER-states, which AGENTS.md §4 degrades loudly rather than blocking, and
    a worksheet that legitimately comes out at $0 is a correct return."""
    r = C.evaluate(_profile({"may_qualify_for_eic": True}))
    assert r.blocking == ()
    assert [f.rule_id for f in r.manual] == ["earned_income_credit"]


def test_the_eic_reaches_the_limitations_manifest():
    """The manifest is what /tax-generate prints on page 1. Before this rule
    the only statements of the gap were a `_comment` in result_template.json
    and a sentence in a compute reference — read by an agent, not a taxpayer."""
    r = C.evaluate(_profile({"may_qualify_for_eic": True}))
    hits = [line for line in r.limitations if "Earned Income Credit" in line]
    assert len(hits) == 1
    assert hits[0].startswith("MANUAL WORKSHEET:")


def test_the_eic_is_silent_when_the_taxpayer_screened_negative():
    r = C.evaluate(_profile({"may_qualify_for_eic": False}))
    assert not [line for line in r.limitations if "Earned Income Credit" in line]


def test_an_absent_eic_flag_does_not_block_a_legacy_profile():
    """Deliberately NOT in _REQUIRED_GATE_FLAGS. The migration blocker cannot
    be acknowledged away, and turning a disclosure into a hard stop for every
    profile that predates the flag is the cry-wolf shape — the remedy for a
    legacy profile is re-running the interview, not a gate it cannot clear."""
    profile = _profile()
    profile["flags"].pop("may_qualify_for_eic", None)
    r = C.evaluate(profile)
    assert r.blocking == ()
    assert not [line for line in r.limitations if "Earned Income Credit" in line]


def test_the_eic_rule_names_every_line_the_tie_out_watches():
    """DERIVED from `tie_out._EIC_LINES`, not listed again. The first draft
    listed three and called the set complete, and a hand-written list in a
    second module is how the two come to disagree about which lines carry the
    credit — which is the defect, not a formatting choice."""
    from agents.lib import tie_out as T
    text = _eic_rule().form + " " + _eic_rule().note
    for _, path, _ in T._EIC_LINES:
        line_no = path[-1].split("_")[1]
        assert f"line {line_no}" in text, (
            f"the rule names no line {line_no}, which the tie-out watches")
    assert "IT-215" in text and "IT-209" in text and "NYC" in text


def test_the_eic_rule_sends_each_line_to_its_own_form():
    """The over-statement a single federal remedy would invite: the New York
    and City credits are not the federal credit, so 'work Pub 596 and write the
    figure to the lines' puts a federal figure on three New York lines."""
    note = _eic_rule().note
    assert "OWN FORM AND ITS OWN ELIGIBILITY TEST" in note
    assert "none of them is a copy of another" in note


def test_the_eic_text_states_no_year_specific_figure():
    """AGENTS.md §2. The credit amounts and the disqualified-income cap index
    annually; the childless age band does not but has moved by statute. Either
    way a figure written into this text reads as current while belonging to
    whatever year it was typed in — and this rule is a DISCLOSURE, not a step
    towards the computation that was ruled out.

    Covers the tie-out advisory and both JSON notes as well as the rule, since
    all four state the same gap and only one of them was guarded."""
    from agents.lib import tie_out as T
    text = " ".join([
        _eic_rule().situation, _eic_rule().form, _eic_rule().note,
        _eic_advisory_text(),
        json.loads((REPO / "agents/templates/taxpayer_profile_template.json")
                   .read_text())["flags"]["_comment_eic"],
        json.loads((REPO / "agents/templates/result_template.json")
                   .read_text())["form_1040"]["_note_line_27"],
    ])
    assert T  # the advisory text above comes from it
    # `$0` is exempt and nothing else is: a zero is not a figure that indexes,
    # so it cannot go stale, and the rule needs it to say that a worked $0 is
    # an answer rather than a skipped line. Any other dollar amount here would
    # read as current while belonging to whatever year it was typed in.
    amounts = [m for m in re.findall(r"\$\s?[\d,]*\d", text) if m != "$0"]
    assert amounts == [], amounts
    assert not re.search(r"\bage[sd]?\s+\d", text), text
    # A rate and an age BAND are the two spellings that dodge a dollar sign.
    assert not re.search(r"\d\s?%", text), text
    assert not re.search(r"\b\d{2}\s*(?:to|through|-|—|–)\s*\d{2}\b", text), text


def _eic_advisory_text() -> str:
    """The advisory string itself, produced rather than re-typed — a lint over
    a copy of the text is the defect it exists to catch."""
    from agents.lib import tie_out as T
    report = T.evaluate(
        [], {"form_1040": {"line_27_eic": 0},
             "new_york_it201": {
                 "refundable_credits_and_payments": {
                     "line_65_earned_income": 0,
                     "line_66_noncustodial_parent_eic": 0,
                     "line_70_nyc_earned_income": 0},
                 "nyc": {}}},
        [], eic_screening=True)
    hits = [a for a in report.advisories if "Earned Income Credit" in a]
    assert len(hits) == 1, report.advisories
    return hits[0]


def test_the_eic_rule_says_nothing_downstream_can_catch_it():
    """The fact that makes this rule load-bearing rather than decorative: the
    tie-out gate compares source documents to the return, and a credit has no
    source document, so the one check built to see a dropped figure cannot see
    this one."""
    assert "source document" in _eic_rule().note


def test_the_eic_screen_covers_the_noncustodial_parent_credit():
    """Form IT-209's eligibility is not the federal EIC's, so a flag worded as
    a purely federal screen would let a federal NO silence line 66. The
    interview and the rule both have to say the flag covers it."""
    assert "noncustodial parent" in _eic_rule().situation
    assert "noncustodial parent paying court-ordered child support" in \
        skill_text("tax-setup")


def test_the_eic_screen_does_not_make_the_half_year_home_rule_universal():
    """The filer's own more-than-half-year US home is the test WITHOUT a
    qualifying child; with one, the CHILD's residence with the filer is the
    test, with a birth-or-death exception. Stated as universal, it lets an
    agent screen an eligible filer who arrived mid-year to NO — and a NO
    silences both disclosures."""
    text = skill_text("tax-setup")
    assert "a US abode for more than half the year" not in text
    assert "WITHOUT a qualifying child" in text
    assert "born or who died during the year still counts" in text


WHO_IT_FITS = REPO / "docs" / "WHO-IT-FITS.md"
WHO_IT_FITS_SCRIPT = REPO / "scripts" / "who_it_fits.py"
# Backticked on the page and not a gate rule.
WHO_IT_FITS_NOT_RULES = frozenset({"acknowledged_limitations"})


def _who_it_fits_script():
    spec = importlib.util.spec_from_file_location("who_it_fits", WHO_IT_FITS_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_who_it_fits_sizes_exactly_the_blocking_rules():
    """The page sizes the UNSUPPORTED list for the named personas (AA6), so its
    list has to BE that list. A rule added to the gate and missing here is a
    block nobody has sized. A rule the page still names after it stopped
    blocking tells a reader they will be stopped by something that no longer
    stops them, whether it was demoted to MANUAL or deleted outright: the
    second review found a deleted rule passed, because the check read only the
    ids the gate still had. So every rule-shaped id in backticks has to be a
    blocking rule, and the page may name nothing else that way."""
    named = set(re.findall(r"`([a-z0-9_]+)`", WHO_IT_FITS.read_text()))
    blocking = {r.rule_id for r in C.RULES if r.level == C.UNSUPPORTED}
    assert blocking - named == set(), (
        f"{sorted(blocking - named)} block at the gate and docs/WHO-IT-FITS.md "
        "does not size them")
    assert named - WHO_IT_FITS_NOT_RULES - blocking == set(), (
        f"{sorted(named - WHO_IT_FITS_NOT_RULES - blocking)} are named in "
        "docs/WHO-IT-FITS.md as blocks and are not blocking rules")


def _collapsed(text):
    return " ".join(text.split())


def test_who_it_fits_prints_what_the_script_measured():
    """Every cell of the page's table, and every figure its prose quotes, is
    what scripts/who_it_fits.py renders from the figures it last measured. The
    first draft was copied by hand and four cells were wrong; the first fix
    checked only the derived row, so a measured cell edited by hand still
    passed. A refresh rewrites the figures file, and this names every sentence
    that then has to change."""
    script = _who_it_fits_script()
    figures = json.loads(script.FIGURES.read_text(encoding="utf-8"))
    page = WHO_IT_FITS.read_text()
    table = [line for line in page.splitlines() if line.startswith("|")]
    assert table == script.render_table(figures)
    missing = [p for p in script.phrases(figures) if p not in _collapsed(page)]
    assert missing == [], f"docs/WHO-IT-FITS.md no longer says: {missing}"


def test_who_it_fits_comparisons_still_hold():
    """The prose compares the personas ("the highest of the five"), and a
    newer year's figures can reverse a comparison while every number on the
    page stays right. Each comparison is checked against the saved figures."""
    script = _who_it_fits_script()
    figures = json.loads(script.FIGURES.read_text(encoding="utf-8"))
    failed = [c for c, holds in script.claims(figures).items() if not holds]
    assert failed == [], f"docs/WHO-IT-FITS.md claims what no longer holds: {failed}"


def _write_csv(path, header, rows):
    path.write_text("\n".join([header] + [",".join(map(str, r)) for r in rows]) + "\n",
                    encoding="latin-1")


INCOME_PERSONAS = {"Whole": ("90", None, False),
                   "Area": ("91", frozenset({"001", "002"}), False)}


def _write_income(tmp_path, drop=lambda row: False):
    """State ZZ is the whole-state persona, counties 001 and 002 of state YY
    the area; `drop` removes rows, to stand for a download missing them."""
    county = ([("90", "ZZ", "000", 1, 5, 5, 5), ("91", "YY", "000", 8, 999, 999, 999)]
              + [("91", "YY", "001", b, 100, 30 if b == 8 else 0, 0) for b in range(1, 9)]
              + [("91", "YY", "002", b, 100, 10 if b == 8 else 0, 40 if b == 1 else 0)
                 for b in range(1, 9)]
              + [("91", "YY", "003", 8, 100, 100, 100)])
    state = [("ZZ", 0, '"1,000"', 50, 100, 0), ("ZZ", 7, 500, 500, 500, 0),
             ("ZZ", 8, 100, 20, 5, 0), ("ZZ", 9, 60, 10, 0, 30),
             ("ZZ", 10, 40, 10, 0, 20)]
    _write_csv(tmp_path / "23incyallagi.csv",
               "STATEFIPS,STATE,COUNTYFIPS,agi_stub,N1,N25870,N85775",
               [r for r in county if not drop(r)])
    _write_csv(tmp_path / "23in55cmcsv.csv", "STATE,AGI_STUB,N1,N25870,N85775,N04470",
               [r for r in state if not drop(r)])


def test_who_it_fits_reads_income_by_the_right_brackets(tmp_path):
    """The state file's brackets 8-10 are $200,000+ and bracket 0 is every
    return; the county file has eight brackets, 8 being $200,000+, and no total,
    so every return is their sum. A county outside the area, and the state's
    own total row, stay out. Hand-computed from the rows in _write_income."""
    script = _who_it_fits_script()
    _write_income(tmp_path)
    personas = INCOME_PERSONAS
    fips_of, rows = script.read_county_file(tmp_path, personas)
    assert fips_of == {"ZZ": "90", "YY": "91"}
    assert script.income_shares(tmp_path, fips_of, rows, personas) == {
        "Whole": pytest.approx({"rental_all": 5.0, "marketplace_all": 10.0,
                                "rental_200k": 20.0, "marketplace_200k": 2.5}),
        "Area": pytest.approx({"rental_all": 2.5, "marketplace_all": 2.5,
                               "rental_200k": 20.0, "marketplace_200k": 0.0})}
    assert script.itemizing_500k(tmp_path, ("ZZ",)) == pytest.approx({"ZZ": 50.0})


STATE_OUT = "y1_statefips,y2_statefips,y2_state,y2_state_name,n1,n2,AGI"
STATE_IN = "y2_statefips,y1_statefips,y1_state,y1_state_name,n1,n2,AGI"
COUNTY_OUT = "y1_statefips,y1_countyfips,y2_statefips,y2_countyfips,y2_state,y2_countyname,n1,n2,agi"
COUNTY_IN = "y2_statefips,y2_countyfips,y1_statefips,y1_countyfips,y1_state,y1_countyname,n1,n2,agi"


def _county_flows(fips, county, total, us, same, different, foreign, stay, places):
    """One county's rows in a county flow file, summary rows first."""
    name = f"C{county} County"
    return ([(fips, county, "96", "000", "YY", f"{name} Total Migration-US and Foreign", total, 0, 0),
             (fips, county, "97", "000", "YY", f"{name} Total Migration-US", us, 0, 0),
             (fips, county, "97", "001", "YY", f"{name} Total Migration-Same State", same, 0, 0),
             (fips, county, "97", "003", "YY", f"{name} Total Migration-Different State",
              different, 0, 0),
             (fips, county, "98", "000", "YY", f"{name} Total Migration-Foreign", foreign, 0, 0),
             (fips, county, fips, county, "YY", f"{name} Non-migrants", stay, 0, 0)]
            + [(fips, county, s, c, "YY", f"C{c} County", n, 0, 0) for s, c, n in places])


def _write_moves(tmp_path, drop_us_row=False):
    out_rows = [("90", "96", "ZZ", "ZZ Total Migration-US and Foreign", 60, 0, 0),
                ("90", "97", "ZZ", "ZZ Total Migration-US", 50, 0, 0),
                ("90", "98", "ZZ", "ZZ Total Migration-Foreign", 10, 0, 0),
                ("90", "97", "ZZ", "ZZ Total Migration-Same State", 100, 0, 0),
                ("90", "90", "ZZ", "ZZ Non-migrants", 830, 0, 0),
                ("90", "12", "FL", "Florida", 50, 0, 0)]
    if drop_us_row:
        out_rows = [r for r in out_rows if not r[3].endswith("Migration-US")]
    _write_csv(tmp_path / "stateoutflow2223.csv", STATE_OUT, out_rows)
    _write_csv(tmp_path / "stateinflow2223.csv", STATE_IN, [
        ("90", "96", "ZZ", "ZZ Total Migration-US and Foreign", 40, 0, 0),
        ("90", "97", "ZZ", "ZZ Total Migration-US", 35, 0, 0),
        ("90", "98", "ZZ", "ZZ Total Migration-Foreign", 5, 0, 0),
        ("90", "36", "NY", "New York", 20, 0, 0),
        ("90", "13", "GA", "Georgia", 15, 0, 0),
        ("90", "57", "FR", "Foreign", 5, 0, 0)])
    # Area = counties 001 and 002 of state 91; 005, 007 and 009 are the rest
    # of the state, and 003 is outside the area and must not count.
    _write_csv(tmp_path / "countyoutflow2223.csv", COUNTY_OUT,
               _county_flows("91", "001", 30, 28, 18, 10, 2, 500,
                             [("91", "002", 8), ("91", "005", 10)])
               + _county_flows("91", "002", 20, 20, 12, 8, 0, 300,
                               [("91", "001", 5), ("91", "007", 7)])
               + _county_flows("91", "003", 999, 999, 0, 999, 0, 999, []))
    _write_csv(tmp_path / "countyinflow2223.csv", COUNTY_IN,
               _county_flows("91", "001", 25, 25, 15, 10, 0, 500,
                             [("91", "002", 5), ("91", "009", 10)])
               + _county_flows("91", "002", 16, 15, 8, 7, 1, 300, [("91", "001", 8)]))


def _flows(script, tmp_path):
    return {(level, side): script.read_flows(tmp_path, level, side, {"90", "91"})
            for level in ("state", "county") for side in ("out", "in")}


def test_who_it_fits_counts_moves_across_the_state_line(tmp_path):
    """Hand-computed. Whole state: households are non-migrants, moves within
    the state and both directions of moves out of it, 830 + 100 + 60 + 40 =
    1,030; 85 crossed a state line and 15 went abroad. Area: a move between its
    two counties (8 + 5 each way) is not a move out; leaving is 50 - 13 = 37,
    entering 41 - 13 = 28, households 800 + 13 + 37 + 28 = 878, of which 35
    crossed a state line, 3 went abroad and 27 moved to or from the rest of
    the state (10 + 7 out, 10 in)."""
    script = _who_it_fits_script()
    _write_moves(tmp_path)
    personas = {"Whole": ("90", None, False),
                "Area": ("91", frozenset({"001", "002"}), True)}
    flows = _flows(script, tmp_path)
    assert script.moves(flows, personas) == {
        "Whole": pytest.approx({"state_line": 100 * 85 / 1030,
                                "abroad": 100 * 15 / 1030, "in_state": 0.0}),
        "Area": pytest.approx({"state_line": 100 * 35 / 878, "abroad": 100 * 3 / 878,
                               "in_state": 100 * 27 / 878})}
    origins = script.arrivals(flows, personas)["Whole"]
    assert [name for name, _ in origins] == ["New York", "Georgia"]
    assert [share for _, share in origins] == pytest.approx([100 * 20 / 35, 100 * 15 / 35])


def _write_commuting(tmp_path, header=True):
    from openpyxl import Workbook
    script = _who_it_fits_script()
    book = Workbook()
    sheet = book.active
    sheet.append(("Table 1. Residence County to Workplace County Commuting Flows",))
    sheet.append((None,))
    if header:
        sheet.append(script.COMMUTING_HEADER + ("Margin of Error",))
    home = ("06", "001", "California", "A County")
    for work, n in [(("006", "001", "California", "A County"), 100),
                    (("032", "003", "Nevada", "B County"), 20),
                    (("053", "033", "Washington", "King County"), 5),
                    (("041", "005", "Oregon", "C County"), 3),
                    ((None, None, "Other workplace outside of the U.S.", None), 2),
                    (("072", "127", "Puerto Rico", "D Municipio"), 1)]:
        sheet.append(home + work + (n, 1))
    sheet.append(("12", "086", "Florida", "X County", "053", "033", "Washington",
                  "King County", 50, 1))
    book.save(tmp_path / "table1.xlsx")


def test_who_it_fits_commuting_blocks_what_the_gate_blocks(tmp_path):
    """A workplace blocks when its state is not on the gate's no-income-tax
    list, taken from the gate and turned into FIPS codes with the IRS file's
    own pairs, not typed a second time: Nevada does not block, and Washington
    and Oregon do. Abroad and Puerto Rico stay in the denominator and do not
    block, and a Florida resident's commute is not California's. 8 of 131."""
    script = _who_it_fits_script()
    _write_commuting(tmp_path)
    fips_of = {"AK": "02", "FL": "12", "NV": "32", "SD": "46", "TN": "47",
               "TX": "48", "WY": "56", "WA": "53", "OR": "41", "CA": "06"}
    no_tax = script.no_tax_fips(fips_of)
    assert "32" in no_tax and "53" not in no_tax
    work = script.commuting(tmp_path, no_tax, {"Cal": ("06", None, False)})
    assert work["blocking"] == pytest.approx({"Cal": 100 * 8 / 131})
    places = work["workplaces"]["Cal"]
    assert [(name, blocks) for name, _, blocks in places] == [
        ("Nevada", False), ("Washington", True), ("Oregon", True)]
    assert [share for _, share, _ in places] == pytest.approx(
        [100 * 20 / 28, 100 * 5 / 28, 100 * 3 / 28])


def test_who_it_fits_stops_on_a_changed_layout(tmp_path):
    """Run against a newer year, a renamed summary row or a moved header would
    otherwise read as zero and print a plausible 0.00%, and a missing bracket or
    county would still leave a denominator: the local review found California's
    $200,000+ rental share fell from 16.6% to 15.4% with its top bracket gone
    and nothing said. Each of them stops the run."""
    script = _who_it_fits_script()
    _write_income(tmp_path, drop=lambda r: r[:2] == ("ZZ", 10))
    fips_of, rows = script.read_county_file(tmp_path, INCOME_PERSONAS)
    with pytest.raises(ValueError, match="missing brackets"):
        script.income_shares(tmp_path, fips_of, rows, INCOME_PERSONAS)
    with pytest.raises(ValueError, match="missing brackets"):
        script.itemizing_500k(tmp_path, ("ZZ",))
    _write_income(tmp_path, drop=lambda r: r[:3] == ("91", "YY", "002"))
    fips_of, rows = script.read_county_file(tmp_path, INCOME_PERSONAS)
    with pytest.raises(ValueError, match="'002'"):
        script.income_shares(tmp_path, fips_of, rows, INCOME_PERSONAS)
    _write_moves(tmp_path, drop_us_row=True)
    with pytest.raises(ValueError, match="US"):
        script.moves(_flows(script, tmp_path), {"Whole": ("90", None, False)})
    _write_commuting(tmp_path, header=False)
    with pytest.raises(ValueError, match="header"):
        script.commuting(tmp_path, set(), {"Cal": ("06", None, False)})


# --------------------------------------------------------------------------
# AA18: a bona fide resident of a US territory. Excluded territory income adds
# back into modified AGI, and with nothing collecting it the SALT phasedown and
# Schedule 1-A read AGI alone: an over-stated deduction. The gate took a
# territory address with no rule at all.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("state", sorted(C._TERRITORIES))
def test_a_territory_address_blocks_whatever_the_flag_says(state):
    r = C.evaluate(_profile({"bona_fide_territory_resident": False},
                            state=state))
    assert _blocking_ids(r) == ["territory_resident"]


def test_the_territories_are_exactly_the_five_inhabited_ones():
    """A USPS code missing here passes the gate as a state; one added that is
    not a territory blocks a state's filer."""
    assert C._TERRITORIES == {"PR", "VI", "GU", "AS", "MP"}


def test_a_state_address_with_the_flag_false_does_not_block():
    assert "territory_resident" not in _blocking_ids(C.evaluate(_profile()))


def test_an_unanswered_territory_question_blocks_until_asked():
    """Absence is not a no: the question is a required gate flag."""
    p = _profile()
    del p["flags"]["bona_fide_territory_resident"]
    r = C.evaluate(p)
    assert _blocking_ids(r) == ["unanswered_gate_questions"]
    assert "bona_fide_territory_resident" in r.blocking[0].note


def test_the_territory_block_can_be_acknowledged():
    """Like part_year_residency: the return may proceed with the limitation
    recorded, which is the user's decision, not the gate's."""
    r = C.evaluate(_profile({"bona_fide_territory_resident": True},
                            acknowledged=["territory_resident"]))
    assert "territory_resident" not in _blocking_ids(r)


@pytest.mark.parametrize("state", sorted(C._TERRITORIES))
def test_a_territory_gets_no_state_bracket_estimate(state):
    """An acknowledged territory return carried "PR state tax is
    bracket-estimated" beside the block saying it is not modeled. A territory
    is not a state."""
    note = C._state_note({"address": {"state": state}})
    assert "ESTIMATE" not in note and "territory_resident" in note


def test_a_territory_domicile_blocks_behind_a_stateside_address():
    """The address is where the mail goes; `residency.domicile_state` is the
    residence. A Puerto Rico domicile with a Florida box passed."""
    p = _profile({"bona_fide_territory_resident": False}, state="FL")
    p["residency"] = {"domicile_state": "PR"}
    assert _blocking_ids(C.evaluate(p)) == ["territory_resident"]


def test_the_template_leaves_every_required_question_unanswered():
    """AA31: the template shipped them `false`, and /tax-setup builds the
    profile from it, so a question the interview skipped read as an answered
    no. Null is unanswered, and a profile straight from the template blocks
    until every one is asked."""
    template = json.loads((REPO / "agents/templates/taxpayer_profile_template.json")
                          .read_text())
    shipped = {f: template["flags"][f] for f in C._REQUIRED_GATE_FLAGS}
    assert set(shipped.values()) == {None}, shipped
    template["address"] = {"state": "CA"}
    template["tax_year"] = 2025
    template["filing_status"] = "single"
    assert "unanswered_gate_questions" in _blocking_ids(C.evaluate(template))


@pytest.mark.parametrize("flag", ["nyc_resident", "yonkers_resident",
                                  "yonkers_nonresident_earnings",
                                  "has_iso_exercise",
                                  "has_dependent_unearned_income"])
def test_a_conditionally_asked_gate_question_is_answered_when_skipped(flag):
    """A question asked only under a condition — NY appears somewhere, ISOs
    were exercised, there are dependents — used to be answered by the
    template's `false` when the condition failed. With the template null
    (AA31), the interview has to say so, or a correctly run interview leaves
    the flag null and the gate blocks every filer the condition skipped."""
    text = " ".join((REPO / ".agents/skills/tax-setup/references/interview.md")
                    .read_text().split())
    near_false = [i for i in range(len(text)) if text.startswith(flag, i)
                  and "`false`" in text[max(0, i - 250):i + 250]]
    assert near_false, (
        f"interview.md never says to set `{flag}` false when its condition "
        "skips the question")



# --------------------------------------------------------------------------
# AA30: the New York City and Yonkers households the engine refuses are
# blocked at the gate, where the form can be named before the work, rather
# than discovered at Step 10 and failed again at the Step 12.7 tie-out.
# --------------------------------------------------------------------------

def _ny_household(status="single", state="NY", acknowledged=None, **flags):
    p = _profile(flags, acknowledged=acknowledged, state=state)
    p["filing_status"] = status
    return C.evaluate(p)


# Loops, not parametrize: every test collected here counts against the skill
# hook's timeout budget (MEASURED COSTS), and these cases are one claim each.
_REFUSED_HOUSEHOLDS = (
    ("single", {"nyc_resident": True, "nyc_resident_full_year": False},
     "New York City: a resident for only part of the year"),
    ("MFJ", {"nyc_resident": True, "nyc_resident_spouses": 1},
     "New York City: a joint return with 1"),
    ("MFJ", {"nyc_resident": True, "nyc_resident_spouses": 0},
     "New York City: a joint return with 0"),
    ("single", {"yonkers_resident": True, "yonkers_resident_full_year": False},
     "Yonkers: a resident for only part of the year"),
    ("MFJ", {"yonkers_resident": True, "yonkers_resident_spouses": 1},
     "Yonkers: a joint return with 1"),
    ("MFJ", {"yonkers_nonresident_earnings": True}, "Form Y-203"),
    ("single", {"yonkers_nonresident_earnings": True, "yonkers_resident": True,
                "yonkers_resident_full_year": True}, "Form Y-203"),
)

# Not blocked at the gate: the households the engine computes, and the
# unanswered counts it refuses by name itself at Step 10.
_NOT_BLOCKED_HOUSEHOLDS = (
    ("single", {"nyc_resident": True, "nyc_resident_full_year": True}),
    ("MFJ", {"nyc_resident": True, "nyc_resident_spouses": 2}),
    ("single", {"yonkers_resident": True, "yonkers_resident_full_year": True}),
    ("MFJ", {"yonkers_resident": True, "yonkers_resident_spouses": 2}),
    ("single", {"yonkers_nonresident_earnings": True}),
    # Unanswered counts are the engine's to refuse, by name, not this block's.
    ("single", {"nyc_resident": True}),
    ("MFJ", {"nyc_resident": True}),
)


def test_a_household_the_engine_refuses_blocks_at_the_gate():
    for status, flags, named in _REFUSED_HOUSEHOLDS:
        r = _ny_household(status, **flags)
        assert _blocking_ids(r) == ["ny_city_or_yonkers_household_unmodeled"], \
            (status, flags)
        assert named in r.blocking[0].note, (status, flags)
        assert not r.blocking[0].acknowledged


def test_a_household_not_refused_is_not_blocked():
    for status, flags in _NOT_BLOCKED_HOUSEHOLDS:
        assert "ny_city_or_yonkers_household_unmodeled" not in _blocking_ids(
            _ny_household(status, **flags)), (status, flags)


def test_the_household_block_is_scoped_to_a_new_york_return_and_acknowledgeable():
    """Elsewhere the New York engine never runs: a Connecticut commuter's joint
    Y-203 is nonresident_source_income's, not this block's. On a New York
    return it can be acknowledged — the City or Yonkers tax is then prepared by
    hand — because the only other way past it is a false residency answer."""
    for status, flags, _named in _REFUSED_HOUSEHOLDS:
        assert "ny_city_or_yonkers_household_unmodeled" not in _blocking_ids(
            _ny_household(status, state="CT", **flags)), (status, flags)
    r = _ny_household("single", nyc_resident=True,
                      nyc_resident_full_year=False,
                      acknowledged=["ny_city_or_yonkers_household_unmodeled"])
    assert "ny_city_or_yonkers_household_unmodeled" not in _blocking_ids(r)
    assert any("prepared by hand" in line for line in r.limitations)
