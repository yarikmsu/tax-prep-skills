"""Hand-worked independent 8606 pools and the intake/completion/rollover chain."""
import copy
import dataclasses
import json
from pathlib import Path

import pdfplumber
import pytest

from agents.lib import filing_package, parse_records, pdf_render, return_builder, roth_ladder, rollover, tie_out
from agents.lib.tax_computations import compute_individual_8606
from agents.lib.tax_computations.federal.ira_basis import (
    opening_basis, tie_individual_8606, validate_individual_8606,
)


def allocation(doc, *, ordinary=0, converted=0, excluded=0, taxable=0, reason=None):
    return {"doc_id": doc, "ordinary": ordinary, "converted": converted,
            "excluded": excluded, "excluded_taxable": taxable, "exclusion_reason": reason}


def worksheet(owner, *, balance=0, contribution=0, ordinary=0, converted=0):
    return {"accounts_complete": True, "regular_worksheet_confirmed": True,
            "accounts": [{"account_id": f"IRA-{owner}", "person": owner,
                          "kind": "traditional_ira", "year_end_value": balance}],
            "nondeductible_contrib_current": contribution, "contrib_made_by_deadline": 0,
            "outstanding_rollovers": 0,
            "distributions": [allocation(f"R-{owner}", ordinary=ordinary, converted=converted)]
            if ordinary or converted else []}


def case():
    profile = {"tax_year": 2025, "filing_status": "MFJ",
               "prior_year": {"ira_basis_by_taxpayer": {"primary": 6000, "spouse": 2000}}}
    inputs = {"tax_year": 2025, "by_taxpayer": {
        "primary": worksheet("primary", converted=6000),
        "spouse": worksheet("spouse", balance=18000, ordinary=2000)}}
    section = compute_individual_8606(inputs, profile)
    result = {"meta": {"tax_year": 2025, "filing_status": "MFJ", "limitations": ["synthetic worksheet"]},
              "form_8606": section,
              "form_1040": {"line_4a_ira_distributions": section["gross_to_1040"],
                            "line_4b_taxable_ira": section["taxable_to_1040"]}}
    scaffold = json.loads((Path(__file__).resolve().parents[1] / "agents/templates/result_template.json").read_text())
    result["form_1040"] = {**scaffold["form_1040"], **result["form_1040"]}
    return profile, inputs, result


def record(owner, gross, *, kind="traditional_ira", doc=None):
    doc = doc or f"R-{owner}"
    return {"doc_id": doc, "file": f"documents/{doc}.pdf", "form_type": "1099-R",
            "payer": "Synthetic institution", "person": owner, "retirement_kind": kind,
            "key_boxes": {"retirement_gross": gross}, "skipped": None}


def records():
    return tie_out.load_ledger({"records": [record("primary", 6000), record("spouse", 2000)]})


def test_each_spouse_has_independent_pool_and_carryforward():
    profile, _, result = case()
    people = result["form_8606"]["by_taxpayer"]
    # Primary: 6000 basis / 6000 conversion = 1.0: conversion tax-free.
    # Spouse: 2000 basis / (18000 FMV + 2000 distribution) = .1:
    # 200 excluded, 1800 taxable, 1800 basis remains. Household pooling would
    # incorrectly tax primary and consume the spouse's basis on that conversion.
    assert people["primary"]["line_10_ratio"] == 1
    assert people["primary"]["taxable_conversion"] == 0
    assert people["primary"]["basis_remaining"] == 0
    assert people["spouse"]["line_10_ratio"] == .1
    assert people["spouse"]["taxable_distributions"] == 1800
    assert people["spouse"]["basis_remaining"] == 1800
    assert result["form_8606"]["taxable_to_1040"] == 1800
    assert "basis_remaining" not in result["form_8606"]
    validate_individual_8606(result, profile)
    return_builder.check(result, profile)
    tie_individual_8606(records(), result)


def test_sep_simple_and_outstanding_rollovers_belong_only_to_owner():
    profile, inputs, _ = case()
    primary = inputs["by_taxpayer"]["primary"]
    primary["accounts"] += [
        {"account_id": "SEP", "person": "primary", "kind": "sep_ira", "year_end_value": 4000},
        {"account_id": "SIMPLE", "person": "primary", "kind": "simple_ira", "year_end_value": 8000}]
    primary["outstanding_rollovers"] = 2000
    out = compute_individual_8606(inputs, profile)["by_taxpayer"]
    # 6000 / (14000 + 6000) = .3; conversion excludes1800, taxes4200.
    assert out["primary"]["line_6_year_end_value"] == 14000
    assert out["primary"]["taxable_conversion"] == 4200
    assert out["primary"]["basis_remaining"] == 4200
    assert out["spouse"]["line_6_year_end_value"] == 18000


def test_late_contribution_and_loss_ratio_do_not_pool_spouse():
    profile, inputs, _ = case()
    raw = inputs["by_taxpayer"]["primary"]
    raw["nondeductible_contrib_current"] = 2000
    raw["contrib_made_by_deadline"] = 2000
    # Line3 8000, line5 6000, ratio1; remaining basis2000 (late contrib).
    out = compute_individual_8606(inputs, profile)["by_taxpayer"]["primary"]
    assert out["taxable_conversion"] == 0 and out["basis_remaining"] == 2000
    profile["prior_year"]["ira_basis_by_taxpayer"]["primary"] = 10000
    out = compute_individual_8606(inputs, profile)["by_taxpayer"]["primary"]
    assert out["line_10_ratio"] == 1 and out["basis_remaining"] == 6000


@pytest.mark.parametrize("reason,taxable", [("qcd", 0), ("rollover", 0), ("hsa_funding", 0),
                                           ("recharacterization", 0), ("returned_contribution", 100)])
def test_reviewed_exclusions_route_without_consuming_basis(reason, taxable):
    profile, inputs, _ = case()
    inputs["by_taxpayer"]["primary"]["distributions"][0].update(
        excluded=500, excluded_taxable=taxable, exclusion_reason=reason)
    out = compute_individual_8606(inputs, profile)
    assert out["gross_to_1040"] == 8500
    assert out["taxable_to_1040"] == 1800 + taxable
    assert out["by_taxpayer"]["primary"]["basis_remaining"] == 0


@pytest.mark.parametrize("mutation,match", [
    (lambda p, i: i["by_taxpayer"]["primary"].update(accounts_complete=False), "all of this owner"),
    (lambda p, i: i["by_taxpayer"]["primary"].update(regular_worksheet_confirmed=False), "regular"),
    (lambda p, i: i["by_taxpayer"]["primary"]["accounts"][0].update(person="spouse"), "individual"),
    (lambda p, i: i["by_taxpayer"]["primary"]["accounts"][0].update(kind="roth_ira"), "individual"),
    (lambda p, i: i["by_taxpayer"]["primary"]["accounts"][0].update(kind="inherited_ira"), "individual"),
    (lambda p, i: i["by_taxpayer"]["spouse"]["accounts"][0].update(account_id="IRA-primary"), "Duplicate"),
    (lambda p, i: i["by_taxpayer"].pop("spouse"), "opening IRA basis"),
    (lambda p, i: i.update(tax_year=2024), "year differs"),
    (lambda p, i: i["by_taxpayer"]["spouse"]["distributions"][0].update(doc_id="R-primary"), "Duplicate"),
    (lambda p, i: i["by_taxpayer"]["primary"]["distributions"][0].update(excluded=1), "supported reason"),
    (lambda p, i: i["by_taxpayer"]["primary"]["distributions"][0].update(excluded=1, excluded_taxable=2), "exceeds"),
    (lambda p, i: i["by_taxpayer"]["primary"].update(contrib_made_by_deadline=1), "subset"),
    (lambda p, i: p["prior_year"].update(ira_basis_unresolved={"status": "unresolved"}), "unresolved"),
])
def test_invalid_or_incomplete_inputs_refuse(mutation, match):
    profile, inputs, _ = case()
    mutation(profile, inputs)
    with pytest.raises(ValueError, match=match):
        compute_individual_8606(inputs, profile)


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -1, "100"])
@pytest.mark.parametrize("field", ["year_end_value", "outstanding_rollovers", "opening_basis", "ordinary"])
def test_numeric_boundary_refuses_nonfinite_boolean_negative_and_strings(field, bad):
    profile, inputs, _ = case()
    raw = inputs["by_taxpayer"]["primary"]
    if field == "year_end_value":
        raw["accounts"][0][field] = bad
    elif field == "opening_basis":
        profile["prior_year"]["ira_basis_by_taxpayer"]["primary"] = bad
    elif field == "ordinary":
        raw["distributions"][0][field] = bad
    else:
        raw[field] = bad
    with pytest.raises(ValueError, match="finite"):
        compute_individual_8606(inputs, profile)


def test_single_and_mfs_legacy_basis_migrate_only_to_primary():
    for status in ("Single", "MFS", "HOH", "QSS"):
        assert opening_basis({"filing_status": status, "prior_year": {"ira_basis": 700}}) == {
            "primary": 700, "spouse": 0}
    with pytest.raises(ValueError, match="separate return"):
        opening_basis({"filing_status": "MFS", "prior_year": {"ira_basis_by_taxpayer": {"spouse": 700}}})


def test_joint_legacy_scalar_needs_confirmed_owner_and_consistent_migration():
    profile = {"filing_status": "MFJ", "prior_year": {"ira_basis": 700}}
    with pytest.raises(ValueError, match="confirmed"):
        opening_basis(profile)
    profile["prior_year"]["ira_basis_owner"] = "spouse"
    assert opening_basis(profile) == {"primary": 0, "spouse": 700}
    profile["prior_year"]["ira_basis_by_taxpayer"] = {"spouse": 701}
    with pytest.raises(ValueError, match="conflicts"):
        opening_basis(profile)


@pytest.mark.parametrize("mutation", [
    lambda r: r["form_8606"]["by_taxpayer"]["primary"].update(basis_remaining=100),
    lambda r: r["form_8606"].update(taxable_to_1040=0),
    lambda r: r["form_1040"].update(line_4b_taxable_ira=0),
    lambda r: r["form_1040"].update(line_4a_ira_distributions=9000),
    lambda r: r["meta"].update(tax_year=2024),
    lambda r: r["meta"].update(filing_status="Single"),
    lambda r: r["form_8606"]["inputs"]["by_taxpayer"]["primary"].update(outstanding_rollovers=1000),
])
def test_completion_reproduces_owner_worksheets_and_ira_only_routing(mutation):
    profile, _, result = case()
    mutation(result)
    with pytest.raises(return_builder.Blocked, match="Individual IRA"):
        return_builder.check(result, profile)


def test_completion_refuses_to_skip_individual_opening_basis():
    profile, _, result = case()
    result.pop("form_8606")
    with pytest.raises(return_builder.Blocked, match="requires individual"):
        return_builder.check(result, profile)
    with pytest.raises(return_builder.Blocked, match="profile"):
        return_builder.check(case()[2])


@pytest.mark.parametrize("mutation", [
    lambda rows: rows[0].update(person="spouse"),
    lambda rows: rows[0].update(person="joint"),
    lambda rows: rows[0].pop("retirement_kind"),
    lambda rows: rows[0].update(retirement_kind="roth_ira"),
    lambda rows: rows[0].update(retirement_kind="inherited_ira"),
    lambda rows: rows[0]["key_boxes"].update(retirement_gross=6001),
    lambda rows: rows.pop(),
    lambda rows: rows.append(record("primary", 10, doc="R-other")),
])
def test_intake_gate_blocks_owner_swap_omission_unclassified_and_unsupported(mutation):
    result = case()[2]
    rows = [record("primary", 6000), record("spouse", 2000)]
    mutation(rows)
    ledger = tie_out.load_ledger({"records": rows})
    report = tie_out.evaluate(ledger, result, [r.file for r in ledger])
    assert report.failed
    assert any("Individual IRA" in finding for finding in report.missing_result_targets)


def test_pension_stays_out_of_individual_pool_and_parse_records_keep_kind(tmp_path):
    result = case()[2]
    result["form_1040"]["line_5a_pensions"] = 5000
    rows = [record("primary", 6000), record("spouse", 2000), record("joint", 5000, kind="pension")]
    rows[-1]["doc_id"] = "P-1"
    rows[-1]["file"] = "documents/P-1.pdf"
    ledger = tie_out.load_ledger({"records": rows})
    assert not tie_out.evaluate(ledger, result, [r.file for r in ledger]).failed
    path = parse_records.write_record(tmp_path, rows[0])
    assert json.loads(path.read_text())["retirement_kind"] == "traditional_ira"
    assert path.stat().st_mode & 0o077 == 0
    assert path.parent.stat().st_mode & 0o077 == 0
    bad = copy.deepcopy(rows[0]); bad["retirement_kind"] = "unknown"
    with pytest.raises(parse_records.ParseRecordError, match="retirement_kind"):
        parse_records.validate(bad)


def test_legacy_flat_joint_worksheet_is_blocked_but_single_is_preserved():
    section = {"basis_remaining": 100, "taxable_to_1040": 500}
    result = {"form_8606": section}
    with pytest.raises(ValueError, match="legacy joint"):
        validate_individual_8606(result, {"filing_status": "MFJ"})
    validate_individual_8606(result, {"filing_status": "Single"})


def test_rollover_preserves_both_closing_balances_including_zero():
    profile, _, result = case()
    prior = rollover.PriorReturn(2025, result, profile)
    current = rollover.CurrentState({}, {"filing_status": "MFJ", "prior_year": {}}, {})
    changes = rollover._ira_changes(prior, current)
    assert len(changes) == 1
    assert changes[0].json_path == "prior_year.ira_basis_by_taxpayer"
    assert changes[0].prior_value == {"primary": 0, "spouse": 1800}
    assert changes[0].action_suggested == "apply"
    current.profile["prior_year"]["ira_basis_by_taxpayer"] = {"primary": 6000, "spouse": 2000}
    assert rollover._ira_changes(prior, current)[0].action_suggested == "ask"


def test_rollover_owner_identity_and_separate_return_transition_block():
    profile, _, result = case()
    profile["spouse"] = {"dob": "1980-01-01"}
    prior = rollover.PriorReturn(2025, result, profile)
    for current_profile in ({"filing_status": "Single"},
                            {"filing_status": "MFJ", "spouse": {"dob": "1981-01-01"}}):
        current = rollover.CurrentState({}, current_profile, {})
        change = rollover._ira_changes(prior, current)[0]
        assert change.json_path == "prior_year.ira_basis_unresolved"
        assert change.action_suggested == "apply"
        current.profile["prior_year"] = {"ira_basis_unresolved": change.prior_value}
        with pytest.raises(ValueError, match="unresolved"):
            opening_basis(current.profile)


def test_rollover_ambiguous_joint_legacy_and_one_person_migration():
    prior = rollover.PriorReturn(2025, {"form_8606": {"basis_remaining": 700}},
                                 {"filing_status": "MFJ"})
    current = rollover.CurrentState({}, {"filing_status": "MFJ"}, {})
    assert rollover._ira_changes(prior, current)[0].json_path.endswith("ira_basis_unresolved")
    prior.profile["filing_status"] = "Single"
    change = rollover._ira_changes(prior, current)[0]
    assert change.json_path == "prior_year.ira_basis" and change.prior_value == 700
    prior.profile["filing_status"] = "MFJ"
    prior.result["form_8606"]["person"] = "spouse"
    change = rollover._ira_changes(prior, current)[0]
    assert change.prior_value == {"spouse": 700}


def test_pdf_and_filing_package_render_independent_forms(tmp_path):
    _, _, result = case()
    out = pdf_render.render(result, tmp_path / "summary.pdf")
    with pdfplumber.open(out) as pdf:
        text = " ".join(page.extract_text() or "" for page in pdf.pages)
    assert "Form 8606 — Primary" in text and "Form 8606 — Spouse" in text
    assert "IRA totals to Form 1040" in text
    entries = [entry for entry in filing_package.assembly_order(result) if entry["sequence"] == "48"]
    assert [entry["key"] for entry in entries] == ["form_8606.by_taxpayer.primary", "form_8606.by_taxpayer.spouse"]
    assert out.stat().st_mode & 0o077 == 0


def test_roth_planning_requires_individual_owner_and_reads_only_their_basis():
    profile = case()[0]
    with pytest.raises(roth_ladder.RothLadderError, match="ira-owner"):
        roth_ladder._resolve_basis(profile, None, 10000)
    assert roth_ladder._resolve_basis(profile, None, 10000, "primary") == (True, 6000, 10000)
    assert roth_ladder._resolve_basis(profile, None, 10000, "spouse") == (True, 2000, 10000)
    with pytest.raises(roth_ladder.RothLadderError, match="separate return"):
        roth_ladder._resolve_basis({"filing_status": "MFS"}, 0, 10000, "spouse")


def test_roth_report_persists_owner_and_uses_their_pro_rata():
    from tests.test_roth_ladder import TODAY, _load
    profile = _load("taxpayer_profile.json")
    profile["prior_year"] = {"ira_basis_by_taxpayer": {"primary": 2000, "spouse": 5000}}
    report = roth_ladder.build_ladder(_load("result.json"), profile, _load("tax_law_2026.json"),
                                     2026, max_amount=10000, step=10000, name="owner", today=TODAY,
                                     ira_value=10000, ira_owner="spouse")
    assert report.ira_owner == "spouse" and report.rungs[1].taxable_conversion == 5000
    assert dataclasses.asdict(report)["ira_owner"] == "spouse"
    assert "IRA owner: **spouse**" in roth_ladder.render_markdown(report)


def test_skill_contract_and_profile_template_keep_individual_inputs():
    root = Path(__file__).resolve().parents[1]
    profile = json.loads((root / "agents/templates/taxpayer_profile_template.json").read_text())
    assert "ira_basis_by_taxpayer" in profile["prior_year"]
    assert "ira_basis_unresolved" in profile["prior_year"]
    interview = (root / ".agents/skills/tax-setup/references/interview.md").read_text()
    assert "each individual" in interview and "ira_basis_by_taxpayer" in interview
    parse = (root / ".agents/skills/tax-parse/SKILL.md").read_text()
    assert "retirement_kind" in parse and "never joint" in parse


def test_ny_roth_state_exclusion_must_match_selected_individual():
    from tests.test_state_planning import TODAY, case as state_case, PROFILE
    from agents.lib.tax_computations.state import planning
    result, law = state_case("NY", 80000)
    plan = result["state_planning_inputs"]
    plan["income_treatment"]["ira_distribution"] = "ny_pension"
    plan["retirement_owner"] = "primary"
    plan["ny"]["subtractions"]["pension_annuity"] = [
        dict(taxpayer="primary", entitled=True, qualifying_amount=19500),
        dict(taxpayer="spouse", entitled=True, qualifying_amount=1000)]
    result["form_1040"].update(line_1z_total_wages=59500, line_4b_taxable_ira=20500)
    before = planning._ny(80000, 0, 0, plan, law["state"], "mfj")
    result["new_york_it201"]["line_60_total_taxes"] = before["total_tax"]
    for owner, modeled in (("primary", True), ("spouse", False)):
        report = roth_ladder.build_ladder(result, PROFILE, law, 2025,
                                         max_amount=1000, step=1000, name="ny-owner", today=TODAY,
                                         ira_basis=0, ira_value=10000, ira_owner=owner)
        assert report.state_effect["modeled"] is modeled
        if not modeled:
            assert "selected IRA owner" in report.state_effect["limitations"][0]
            assert all(rung.combined_cost is None for rung in report.rungs)


def test_rollover_writer_preserves_individual_zero_and_owner_only_files(tmp_path):
    profile, _, result = case()
    prior = rollover.PriorReturn(2025, result, profile)
    current_profile = {"tax_year": 2026, "filing_status": "MFJ", "prior_year": {}}
    current = rollover.CurrentState({}, current_profile, {})
    targets = rollover.WritePaths(profile=tmp_path / "taxpayer_profile.json", result=tmp_path / "result.json")
    targets.profile.write_text(json.dumps(current_profile))
    targets.result.write_text('{}')
    changes = {"carryforwards": rollover._ira_changes(prior, current)}
    rollover.apply_changes(changes, {"carryforwards": "apply"}, targets)
    written = json.loads(targets.profile.read_text())
    assert written["prior_year"]["ira_basis_by_taxpayer"] == {"primary": 0, "spouse": 1800}
    assert targets.profile.stat().st_mode & 0o077 == 0


def test_numeric_totals_refuse_finite_inputs_whose_sum_overflows():
    profile, inputs, _ = case()
    raw = inputs["by_taxpayer"]["primary"]
    raw["distributions"] = [allocation("A", excluded=1e308, reason="rollover"),
                            allocation("B", excluded=1e308, reason="rollover")]
    with pytest.raises(ValueError, match="finite"):
        compute_individual_8606(inputs, profile)


@pytest.mark.parametrize("value", [[], {}, 1, True, "unknown"])
def test_retirement_kind_boundary_has_typed_errors(value):
    row = record("primary", 6000)
    row["retirement_kind"] = value
    with pytest.raises(tie_out.TieOutError, match="retirement_kind"):
        tie_out.load_ledger({"records": [row]})


def test_unknown_archived_schema_and_year_mismatch_refuse():
    profile, _, result = case()
    current = rollover.CurrentState({}, {"filing_status": "MFJ"}, {})
    prior = rollover.PriorReturn(2025, result, profile)
    result["form_8606"]["schema"] = "unknown"
    with pytest.raises(rollover.RolloverError, match="schema"):
        rollover._ira_changes(prior, current)
    result["form_8606"]["schema"] = "individual-8606/v1"
    prior = rollover.PriorReturn(2024, result, profile)
    with pytest.raises(rollover.RolloverError, match="year differs"):
        rollover._ira_changes(prior, current)


@pytest.mark.parametrize("people", [None, [], {"joint": {"basis_remaining": 1}}])
def test_package_refuses_malformed_individual_ownership(people):
    _, _, result = case()
    result["form_8606"]["by_taxpayer"] = people
    with pytest.raises(filing_package.FilingPackageError, match="individual|Individual"):
        filing_package.assembly_order(result)


def test_inactive_basis_record_is_preserved_without_phantom_attachment():
    profile, inputs, result = case()
    inputs["by_taxpayer"]["spouse"]["distributions"] = []
    section = compute_individual_8606(inputs, profile)
    result["form_8606"] = section
    result["form_1040"].update(line_4a_ira_distributions=section["gross_to_1040"],
                               line_4b_taxable_ira=section["taxable_to_1040"])
    # Basis2000 and no current contribution/distribution/conversion: keep the
    # individual basis record, but the IRS Who Must File rules require no8606.
    assert section["by_taxpayer"]["spouse"]["basis_remaining"] == 2000
    entries = [e for e in filing_package.assembly_order(result) if e["sequence"] == "48"]
    assert [e["key"] for e in entries] == ["form_8606.by_taxpayer.primary"]
    validate_individual_8606(result, profile)


@pytest.mark.parametrize("prior,flags", [
    ({"ira_basis": 6000, "ira_basis_owner": "primary"}, {}),
    ({}, {"has_roth_conversion": True}),
    ({}, {"has_backdoor_roth": True}),
])
def test_joint_known_basis_or_conversion_cannot_omit_individual_worksheets(prior, flags):
    profile, _, result = case()
    profile["prior_year"], profile["flags"] = prior, flags
    result["form_8606"] = {}  # missing/zero flat sections cannot waive the gate
    with pytest.raises(return_builder.Blocked, match="individual worksheets"):
        return_builder.check(result, profile)


def test_joint_ira_sources_cannot_hide_on_pension_lines_without_worksheets():
    _, _, result = case()
    result.pop("form_8606")
    result["form_1040"].update(line_4a_ira_distributions=0, line_4b_taxable_ira=0,
                               line_5a_pensions=8000, line_5b_taxable_pensions=1800)
    # The old household gross check still balances8000, but both documents
    # explicitly belong to individual IRAs, not the pension lines.
    ledger = records()
    report = tie_out.evaluate(ledger, result, [r.file for r in ledger])
    assert report.failed
    assert any("individual worksheets" in finding for finding in report.missing_result_targets)


@pytest.mark.parametrize("value", [1, "true"])
def test_conversion_screening_cannot_be_waived_by_non_boolean_flags(value):
    with pytest.raises(ValueError, match="boolean"):
        validate_individual_8606({}, {"filing_status": "MFJ", "flags": {"has_roth_conversion": value}})
