"""Synthetic law throughout; worksheets compute expected dollars by hand."""
import copy
import datetime
import json
import math
import stat

import pytest

from agents.lib import roth_plan as P
from agents.lib import roth_ladder
from agents.lib.projection import ProjectionError
from agents.lib.secure_fs import secure_path, write_secure


TODAY = datetime.date(2026, 10, 25)


def law(year=2025):
    # Synthetic, NOT covered-year tax law: three brackets and a base deduction
    # chosen for transparent worksheets. Premiums and gates also synthetic.
    status = {"standard_deduction": 10000, "brackets": [
        {"min": 0, "max": 50000, "rate": .1},
        {"min": 50000, "max": 100000, "rate": .2},
        {"min": 100000, "max": None, "rate": .3}]}
    tiers = [{"magi_threshold": 50000 * i, "annual_surcharge_per_person": 1000 * i} for i in range(1, 6)]
    return {"tax_year": year, "_meta": {"tax_year": year}, "federal": {
        "filing_statuses": {s: copy.deepcopy(status) for s in ("single", "mfj", "mfs", "hoh")},
        "capital_gains_rates": {"zero_pct_limit": {s: 60000 for s in ("single", "mfj", "mfs", "hoh")},
                                "fifteen_pct_limit": {s: 150000 for s in ("single", "mfj", "mfs", "hoh")}},
        "child_tax_credit": {"amount_per_child": 2000, "phase_out_start": {"single": 300000, "mfj": 600000}},
        "additional_medicare_tax": {"rate": .009, "threshold": {"single": 200000, "mfj": 400000, "mfs": 100000}},
        "niit": {"rate": .038, "threshold": {"single": 200000, "mfj": 400000, "mfs": 100000}},
        "social_security": {"wage_base": 200000}, "qbi_deduction_rate": .2,
        "irmaa": {"lookback_years": 2, "tiers": {"single": tiers, "mfj": copy.deepcopy(tiers), "mfs": copy.deepcopy(tiers[:2])}}}}


def result(year=2025):
    return {"meta": {"tax_year": year, "filing_status": "Single"}, "form_1040": {
        "line_1z_total_wages": 40000, "line_11_agi": 40000,
        "line_12_standard_deduction": 10000, "line_24_total_tax": 3000}}


def profile(year=2025):
    return {"tax_year": year, "filing_status": "Single", "dependents": [], "address": {"state": "TX"},
            "prior_year": {"ira_basis_by_taxpayer": {"primary": 20000}}}


def annual(year=2025, conversion=10000):
    return {"year": year, "filing_status": "Single", "wages": 40000, "other_ordinary_income": 0,
        "self_employment_net": 0, "qualified_dividends": 0, "capital_gains": 0,
        "social_security": 0, "social_security_inputs": None, "adjustments": 0,
        "deduction": "standard", "additional_standard_deduction": 0, "state": None,
        "state_inputs": None, "spouse_itemizes": None, "ctc_children": 0,
        "net_investment_income": 0, "niit_magi_addbacks": 0, "irmaa_magi_addbacks": 0,
        "medicare_enrollees_two_years_later": 0, "medicare_months_two_years_later": [],
        "medicare_parts_b_and_d_confirmed": False, "medicare_lived_apart_all_year": None,
        "law": {"mode": "fetched"} if year == 2025 else {"mode": "hypothetical", "basis": "held_at_2025"},
        "premium_law": None, "rmd_not_required_confirmed": True,
        "other_federal_effects_absent_confirmed": True, "conversion": conversion}


def plan():
    return {"schema": P.SCHEMA, "name": "reviewed", "start_year": 2025,
        "assumptions_reviewed": True, "ira": {"owner": "primary", "traditional_balance": 100000,
        "roth_balance": 0, "basis": 20000, "growth_rate": 0, "basis_source": "opening_profile",
        "basis_confirmed": True, "regular_worksheet_confirmed": True, "no_other_ira_flows_confirmed": True},
        "years": [annual(), annual(2026)]}


def build(p=None, r=None, prof=None, laws=None):
    return P.build_plan(p or plan(), r or result(), prof or profile(), laws or {2025: law()}, today=TODAY)


def test_two_year_basis_and_balance_worksheet():
    p = plan()
    p["ira"]["growth_rate"] = .1
    p["years"][0]["conversion"] = 20000
    p["years"][1]["conversion"] = 22000
    output = build(p)
    a, b = output["years"]
    # Year1:20k basis /100k pool ->4k nontaxable of20k. Basis16k,
    # remaining80k grows to88k; Roth20k grows to22k.
    assert (a["taxable_conversion"], a["basis_remaining"], a["ira_next_year"], a["roth_next_year"]) == (16000, 16000, 88000, 22000)
    # Year2:16k/88k *22k=4k recovered. Taxable18k, basis12k,
    # IRA66k*1.1=72.6k; Roth(22k+22k)*1.1=48.4k.
    assert (b["taxable_conversion"], b["basis_remaining"], b["ira_next_year"], b["roth_next_year"]) == (18000, 12000, 72600, 48400)
    assert a["federal_cost"] == 1600 and b["federal_cost"] == 1800
    assert output["totals"]["federal_cost"] == 3400
    assert a["calculation"] == "computed" and b["calculation"] == "forecast"
    assert "hypothetical held_at_2025" in b["law"]


def test_filing_year_reuses_existing_ladder_exactly():
    p = plan()
    p["years"] = p["years"][:1]
    ladder = roth_ladder.build_ladder(result(), profile(), law(), 2025, max_amount=10000, step=10000,
        name="reviewed", today=TODAY, ira_basis=20000, ira_value=100000, ira_owner="primary")
    row = build(p)["years"][0]
    assert row["federal_tax"] == ladder.rungs[-1].total_tax
    assert row["federal_cost"] == ladder.rungs[-1].cumulative_cost
    assert row["taxable_conversion"] == ladder.rungs[-1].taxable_conversion


@pytest.mark.parametrize("status,medicare,niit", [
    ("Single", 200000, 200000), ("HOH", 200000, 200000),
    ("MFJ", 400000, 400000), ("MFS", 100000, 100000),
    ("QSS", 200000, 400000),
])
def test_additional_medicare_and_niit_have_distinct_status_groupings(status, medicare, niit):
    # Synthetic thresholds distinguish each grouping; QSS is single for
    # Form 8959 but joint for Form 8960 (TY2025 instructions, pages 1 and 2).
    row = annual()
    row["filing_status"] = status
    _, kwargs, _ = P._year_inputs(row, law())
    assert kwargs["addl_medicare_threshold"] == medicare
    assert roth_ladder._niit_params(law(), status)[0] == niit


def test_bracket_fill_prices_taxable_conversion_not_gross():
    p = plan()
    p["years"] = p["years"][:1]
    del p["years"][0]["conversion"]
    p["years"][0]["fill_to"] = {"kind": "bracket", "rate": .1}
    row = build(p)["years"][0]
    # Ordinary taxable30k leaves20k to first bracket edge50k. 80% taxable
    # means25k gross conversion, not20k. The next cent crosses the edge.
    assert row["conversion"] == 25000 and row["taxable_conversion"] == 20000
    assert row["federal_cost"] == 2000


def test_open_top_bracket_fills_remaining_balance():
    p = plan()
    p["years"] = p["years"][:1]
    del p["years"][0]["conversion"]
    p["years"][0]["fill_to"] = {"kind": "bracket", "rate": .3}
    row = build(p)["years"][0]
    assert row["conversion"] == 100000 and row["ira_next_year"] == 0
    assert row["basis_remaining"] == 0


def test_irmaa_fill_uses_premium_year_law_and_strict_lower_boundary():
    p = plan()
    p["years"] = p["years"][:1]
    row = p["years"][0]
    del row["conversion"]
    row["fill_to"] = {"kind": "irmaa", "tier": 1}
    row["premium_law"] = {"mode": "fetched"}
    row["medicare_enrollees_two_years_later"] = 1
    row["medicare_months_two_years_later"] = [12]
    row["medicare_parts_b_and_d_confirmed"] = True
    premium = law(2027)
    premium["federal"]["irmaa"]["tiers"]["single"][0]["magi_threshold"] = 60000
    output = build(p, laws={2025: law(), 2027: premium})["years"][0]
    assert output["conversion"] == 25000 and output["magi"] == 60000
    assert output["irmaa_cost"] == 0 and output["premium_year"] == 2027


def test_top_irmaa_boundary_is_inclusive_and_fill_stops_one_cent_below():
    p = plan()
    p["ira"].update(traditional_balance=500000, basis=0)
    prof = profile()
    prof["prior_year"]["ira_basis_by_taxpayer"]["primary"] = 0
    p["years"] = p["years"][:1]
    row = p["years"][0]
    del row["conversion"]
    row.update(fill_to={"kind": "irmaa", "tier": 5}, premium_law={"mode": "fetched"})
    selected = build(p, prof=prof, laws={2025: law(), 2027: law(2027)})["years"][0]
    assert selected["conversion"] == 209999.99 and selected["magi"] == 249999.99


def test_premium_increment_is_separate_and_prorated_for_enrollment_months():
    p = plan()
    p["years"] = p["years"][:1]
    row = p["years"][0]
    row.update(conversion=25000, premium_law={"mode": "hypothetical", "basis": "held_at_2025"},
        medicare_enrollees_two_years_later=2, medicare_months_two_years_later=[12, 6],
        medicare_parts_b_and_d_confirmed=True)
    selected = build(p)["years"][0]
    # AGI60k crosses first50k tier1000 per beneficiary-year. 18 months=1.5
    # beneficiary-years ->1500 premium cost, separate from2000 federal cost.
    assert selected["irmaa_cost"] == 1500 and selected["federal_cost"] == 2000
    assert build(p)["totals"]["federal_cost"] == 2000


def test_missing_premium_and_state_costs_remain_null_not_zero():
    p = plan()
    p["start_year"] = 2026
    p["ira"]["basis_source"] = "reviewed_worksheet"
    p["years"] = [annual(2026)]
    p["years"][0].update(state="CA", medicare_enrollees_two_years_later=1,
                        medicare_months_two_years_later=[12], medicare_parts_b_and_d_confirmed=True)
    output = build(p)
    row = output["years"][0]
    assert row["state_cost"] is None and row["irmaa_cost"] is None
    assert output["totals"]["state_cost"] is None and output["totals"]["irmaa_cost"] is None
    text = P.render_markdown(output)
    assert "not modeled" in text and "totals exclude" in text


def test_held_law_is_unchanged_and_indexed_policy_holds_rates_and_statutory_gates():
    original = law()
    untouched = copy.deepcopy(original)
    held, _ = P.resolve_law({"mode": "hypothetical", "basis": "held_at_2025"}, 2027, {2025: original})
    assert held["federal"] == original["federal"] and held["tax_year"] == 2027
    indexed, _ = P.resolve_law({"mode": "hypothetical", "basis": "indexed_from_2025_at_0.10"}, 2027, {2025: original})
    assert indexed["federal"]["filing_statuses"]["single"]["standard_deduction"] == 12100
    assert indexed["federal"]["filing_statuses"]["single"]["brackets"][0] == {"min": 0, "max": 60500, "rate": .1}
    assert indexed["federal"]["niit"] == original["federal"]["niit"]
    assert indexed["federal"]["child_tax_credit"] == original["federal"]["child_tax_credit"]
    assert original == untouched


def test_missing_fetched_year_never_becomes_a_hypothetical():
    p = plan()
    p["years"][1]["law"] = {"mode": "fetched"}
    with pytest.raises(P.PlanError, match="missing"):
        build(p)


def test_negative_growth_and_basis_exceeding_balance_are_preserved():
    p = plan()
    p["ira"].update(traditional_balance=10000, growth_rate=-.5)
    p["years"][0]["conversion"] = 0
    p["years"][1]["conversion"] = 5000
    a, b = build(p)["years"]
    assert a["basis_remaining"] == 20000 and a["ira_next_year"] == 5000
    assert b["taxable_conversion"] == 0 and b["basis_remaining"] == 15000
    assert b["ira_next_year"] == 0


@pytest.mark.parametrize("key", sorted(P._ANNUAL | {"conversion"}))
def test_every_annual_input_is_required(key):
    p = plan()
    del p["years"][0][key]
    with pytest.raises(P.PlanError):
        P.validate_plan(p, 2025)


@pytest.mark.parametrize("key", sorted(P._IRA))
def test_every_ira_fact_is_required(key):
    p = plan()
    del p["ira"][key]
    with pytest.raises(P.PlanError):
        P.validate_plan(p, 2025)


@pytest.mark.parametrize("bad", [True, math.nan, math.inf, -math.inf, "40000", None])
@pytest.mark.parametrize("key", ["wages", "other_ordinary_income", "qualified_dividends", "capital_gains", "social_security",
    "self_employment_net", "adjustments", "additional_standard_deduction", "net_investment_income", "niit_magi_addbacks", "irmaa_magi_addbacks", "conversion"])
def test_nonfinite_or_nonnumeric_annual_figures_refuse(key, bad):
    p = plan()
    p["years"][0][key] = bad
    with pytest.raises(P.PlanError):
        P.validate_plan(p, 2025)


@pytest.mark.parametrize("key", ["traditional_balance", "roth_balance", "basis", "growth_rate"])
@pytest.mark.parametrize("bad", [True, math.nan, math.inf, "0.02", None])
def test_bad_ira_numbers_refuse(key, bad):
    p = plan()
    p["ira"][key] = bad
    with pytest.raises(P.PlanError):
        P.validate_plan(p, 2025)


def test_conversion_over_balance_refuses_without_clamping():
    p = plan()
    p["years"][0]["conversion"] = 100000.01
    with pytest.raises(P.PlanError, match="exceeds"):
        build(p)


@pytest.mark.parametrize("mutation", ["horizon", "gap", "old_start", "two_choices", "bad_status", "bad_state", "rmd", "other_flows", "unreviewed", "basis_conflict"])
def test_scope_and_boundary_refusals(mutation):
    p = plan()
    if mutation == "horizon":
        p["years"] = [annual(2025 + i) for i in range(11)]
    elif mutation == "gap":
        p["years"][1]["year"] = 2027
    elif mutation == "old_start":
        p["start_year"] = 2024
    elif mutation == "two_choices":
        p["years"][0]["fill_to"] = {"kind": "bracket", "rate": .1}
    elif mutation == "bad_status":
        p["years"][0]["filing_status"] = "Married"
    elif mutation == "bad_state":
        p["years"][0]["state"] = "NY"
    elif mutation == "rmd":
        p["years"][0]["rmd_not_required_confirmed"] = False
    elif mutation == "other_flows":
        p["ira"]["no_other_ira_flows_confirmed"] = False
    elif mutation == "unreviewed":
        p["assumptions_reviewed"] = False
    else:
        p["ira"]["basis"] = 0
    with pytest.raises(P.PlanError):
        build(p)


def test_first_year_income_and_deduction_cannot_be_ignored():
    p = plan()
    p["years"][0]["wages"] += 1
    with pytest.raises(P.PlanError, match="income assumptions"):
        build(p)
    p = plan()
    p["years"][0]["deduction"] = 9000
    with pytest.raises(P.PlanError, match="deduction"):
        build(p)


def test_irmaa_target_without_premium_law_refuses():
    p = plan()
    del p["years"][0]["conversion"]
    p["years"][0]["fill_to"] = {"kind": "irmaa", "tier": 1}
    with pytest.raises(P.PlanError, match="premium-year"):
        build(p)


def test_baseline_and_plan_inputs_are_never_mutated():
    p, r, prof, laws = plan(), result(), profile(), {2025: law()}
    before = copy.deepcopy((p, r, prof, laws))
    build(p, r, prof, laws)
    assert (p, r, prof, laws) == before


def workspace(tmp_path):
    secure_path(tmp_path)
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    secure_path(scenarios)
    for name, data in (("result.json", result()), ("taxpayer_profile.json", profile()), ("tax_law_2025.json", law())):
        write_secure(tmp_path / name, json.dumps(data))
    write_secure(scenarios / "roth_plan_reviewed.input.json", json.dumps(plan()))
    return tmp_path


def test_cli_writes_only_secured_scenarios(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    before = {name: (root / name).read_bytes() for name in ("result.json", "taxpayer_profile.json", "tax_law_2025.json")}
    monkeypatch.setattr("agents.lib.tax_law_fetcher.check_freshness", lambda *a: {"status": "FRESH"})
    assert P._main(["--workspace", str(root), "--name", "reviewed"]) == 0
    for name in ("roth_plan_reviewed.json", "roth_plan_reviewed.md"):
        path = root / "scenarios" / name
        assert path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "scenarios").stat().st_mode) == 0o700
    assert before == {name: (root / name).read_bytes() for name in before}


@pytest.mark.parametrize("status", ["STALE", "INCOMPLETE", "NOT_FOUND"])
def test_cli_stops_on_unfresh_law_without_writing_report(tmp_path, monkeypatch, status):
    root = workspace(tmp_path)
    monkeypatch.setattr("agents.lib.tax_law_fetcher.check_freshness", lambda *a: {"status": status})
    assert P._main(["--workspace", str(root), "--name", "reviewed"]) == 2
    assert not (root / "scenarios/roth_plan_reviewed.json").exists()


def test_scenario_symlink_and_unsafe_name_refuse(tmp_path):
    root = workspace(tmp_path)
    assert P._main(["--workspace", str(root), "--name", "../result"]) == 2
    scenarios = root / "scenarios"
    renamed = root / "elsewhere"
    scenarios.rename(renamed)
    scenarios.symlink_to(renamed, target_is_directory=True)
    assert P._main(["--workspace", str(root), "--name", "reviewed"]) == 2


def future_plan():
    p = plan()
    p["start_year"] = 2026
    p["ira"]["basis_source"] = "reviewed_worksheet"
    p["years"] = [annual(2026)]
    return p


def test_social_security_feedback_is_recomputed_for_each_conversion():
    p = future_plan()
    p["years"][0].update(wages=0, other_ordinary_income=30000, social_security=20000,
        social_security_inputs={"tax_exempt_interest": 0, "excluded_income_addbacks": 0,
                                "regular_worksheet_confirmed": True})
    row = build(p)["years"][0]
    # Single regular worksheet: provisional40k -> taxableSS9600; after
    # taxableconversion8k provisional48k -> taxableSS16400. Federal10%
    # on8k+6800 extra taxable benefits =1480, not800.
    assert row["federal_cost"] == 1480
    assert row["rate_inputs"]["deduction"] == 10000
    assert row["baseline_income_inputs"]["social_security"]["worksheet"]["ss_benefits"] == 20000


def test_self_employment_and_qbi_remain_in_the_shared_keystone():
    p = future_plan()
    p["years"][0]["self_employment_net"] = 20000
    row = build(p)["years"][0]
    # SE2825.91, half1412.96, QBI(20k-1412.96)*20%=3717.41.
    # Taxable44869.63 ->52869.63: regular4486.96 ->5573.93;
    # SE does not change with the IRA conversion. Delta1086.97.
    assert row["federal_cost"] == 1086.97


def test_niit_magi_and_irmaa_magi_are_independent_concepts():
    p = future_plan()
    row = p["years"][0]
    row.update(wages=190000, other_ordinary_income=20000, net_investment_income=20000,
               irmaa_magi_addbacks=50000)
    output = build(p)["years"][0]
    # BaselineNIIT380 ->684, in addition to8k*30%regular=2400.
    assert output["federal_cost"] == 2704
    assert output["magi"] == 268000
    row["niit_magi_addbacks"] = 20000
    # NIIT already saturated at the baseline, so incrementalNIIT=0.
    assert build(p)["years"][0]["federal_cost"] == 2400


def test_forecast_ca_uses_reviewed_contract_when_year_and_inputs_exist():
    from tests.test_state_planning import case
    ca_result, ca_law = case("CA", agi=40000, status="Single")
    p = plan()
    p["ira"]["basis_source"] = "reviewed_worksheet"
    p["years"] = [annual(2025)]
    p["years"][0].update(state="CA", state_inputs=ca_result["state_planning_inputs"])
    synthetic = law()
    synthetic["state"] = ca_law["state"]
    row = build(p, r=result(2024), prof=profile(2024), laws={2025: synthetic})["years"][0]
    # CA synthetic10%rate, fixed10k deduction and400 exemption credit:
    # 2600 ->3400 after8k taxable conversion, no federal anchor for2025.
    assert row["calculation"] == "forecast" and row["state_cost"] == 800
    p["years"][0]["state_inputs"]["retirement_basis_matches_federal"] = False
    assert build(p, r=result(2024), prof=profile(2024), laws={2025: synthetic})["years"][0]["state_cost"] is None


def test_mfs_election_and_individual_owner_cannot_be_assumed():
    p = future_plan()
    p["years"][0].update(filing_status="MFS", medicare_lived_apart_all_year=True, spouse_itemizes=True)
    with pytest.raises(P.PlanError, match="must itemize"):
        build(p)
    p["years"][0]["deduction"] = 0
    assert build(p)["years"][0]["federal_tax"] >= 0
    p["ira"]["owner"] = "spouse"
    with pytest.raises(P.PlanError, match="separate return"):
        build(p)


def test_profile_known_rmd_and_basis_contradictions_cannot_be_overridden():
    p = plan()
    prof = profile()
    prof["flags"] = {"subject_to_rmd_primary": True}
    with pytest.raises(P.PlanError, match="already subject"):
        build(p, prof=prof)
    p["ira"].update(basis_source="reviewed_worksheet", basis=50000)
    with pytest.raises(P.PlanError, match="conflicts"):
        build(p)


def test_missing_fetched_premium_year_is_not_replaced_by_current_year():
    p = plan()
    p["years"][0]["premium_law"] = {"mode": "fetched"}
    with pytest.raises(P.PlanError, match="missing"):
        build(p)


@pytest.mark.parametrize("basis", ["held_at_2027", "indexed_from_2025_at_NaN", "indexed_from_2025_at_-1", "indexed_from_2025_at_1.1", "2025"])
def test_hypothetical_names_and_rates_are_strict(basis):
    p = future_plan()
    p["years"][0]["law"] = {"mode": "hypothetical", "basis": basis}
    with pytest.raises(P.PlanError):
        build(p)


def test_full_baseline_year_cache_metadata_is_checked():
    wrong = law(2024)
    with pytest.raises(ProjectionError, match="tax_year|cache"):
        build(laws={2025: wrong})


@pytest.mark.parametrize("bad", [True, math.nan, math.inf, -.01, 1.01, "0.1"])
def test_corrupt_bracket_rates_cannot_price_or_fill(bad):
    cfg = law()
    cfg["federal"]["filing_statuses"]["single"]["brackets"][0]["rate"] = bad
    with pytest.raises(P.PlanError):
        build(laws={2025: cfg})


def test_noncontiguous_and_closed_top_brackets_refuse():
    for mutation in ("gap", "closed"):
        cfg = law()
        brackets = cfg["federal"]["filing_statuses"]["single"]["brackets"]
        if mutation == "gap":
            brackets[1]["min"] = 51000
        else:
            brackets[-1]["max"] = 1000000
        with pytest.raises(P.PlanError):
            build(laws={2025: cfg})


def test_output_link_cannot_replace_the_reviewed_input(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    inputs = root / "scenarios/roth_plan_reviewed.input.json"
    before = inputs.read_bytes()
    (root / "scenarios/roth_plan_reviewed.json").symlink_to(inputs)
    monkeypatch.setattr("agents.lib.tax_law_fetcher.check_freshness", lambda *a: {"status": "FRESH"})
    assert P._main(["--workspace", str(root), "--name", "reviewed"]) == 2
    assert inputs.read_bytes() == before
    assert not (root / "scenarios/roth_plan_reviewed.md").exists()


def test_corrupt_cache_is_refused_without_deleting_it(tmp_path):
    root = workspace(tmp_path)
    cache = root / "tax_law_2025.json"
    write_secure(cache, "{not valid JSON")
    assert P._main(["--workspace", str(root), "--name", "reviewed"]) == 2
    assert cache.read_text(encoding="utf-8") == "{not valid JSON"


def test_report_preserves_rate_premium_and_basis_inputs_for_reproduction():
    p = plan()
    p["years"][0]["premium_law"] = {"mode": "hypothetical", "basis": "held_at_2025"}
    row = build(p)["years"][0]
    assert row["niit_inputs"]["threshold"] == 200000
    assert len(row["premium_inputs"]["tiers"]) == 5
    assert row["anchor_inputs"]["line_24"] == 3000
    assert len(row["source_law_sha256"]) == 64


def test_hypothetical_single_and_joint_top_tiers_use_their_own_rounding_grid():
    indexed, _ = P.resolve_law({"mode": "hypothetical", "basis": "indexed_from_2025_at_0.02"}, 2028, {2025: law()})
    # Synthetic250000*1.02**3=265302. The single grid is1000, while the
    # joint top grid from2028 is500. Do not share one status switch for both.
    assert indexed["federal"]["irmaa"]["tiers"]["single"][-1]["magi_threshold"] == 265000
    assert indexed["federal"]["irmaa"]["tiers"]["mfj"][-1]["magi_threshold"] == 265500


def test_federal_only_plan_never_declares_a_zero_state_liability():
    output = build()
    assert all(row["state_cost"] is None for row in output["years"])
    assert output["totals"]["state_cost"] is None
