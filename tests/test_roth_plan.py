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
    # Form 8606 line 6 is the Dec 31 value. Year 1: (100,000 - 20,000) x 1.1 =
    # 88,000; line 9 = 88,000 + 20,000 = 108,000; 20,000 x 20,000 / 108,000 =
    # 3,703.70 nontaxable, 16,296.30 taxable and 16,296.30 basis left. Roth
    # 20,000 grows to 22,000.
    assert (a["taxable_conversion"], a["basis_remaining"], a["ira_next_year"], a["roth_next_year"]) == (
        16296.30, 16296.30, 88000, 22000)
    # Year 2: (88,000 - 22,000) x 1.1 = 72,600; line 9 = 94,600; 22,000 x
    # 16,296.30 / 94,600 = 3,789.84 nontaxable, 18,210.16 taxable, 12,506.46
    # basis left. Roth (22,000 + 22,000) x 1.1 = 48,400.
    assert (b["taxable_conversion"], b["basis_remaining"], b["ira_next_year"], b["roth_next_year"]) == (
        18210.16, 12506.46, 72600, 48400)
    # All in the 10% bracket: 1,629.63 + 1,821.02.
    assert a["federal_cost"] == 1629.63 and b["federal_cost"] == 1821.02
    assert output["totals"]["federal_cost"] == 3450.65
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


@pytest.mark.parametrize("recorded_status", ["HoH", "HOH"])
@pytest.mark.parametrize("benefits", [False, True])
def test_filing_year_accepts_existing_canonical_hoh_spelling(recorded_status, benefits):
    p = plan()
    p["years"] = p["years"][:1]
    p["years"][0]["filing_status"] = "HOH"
    r, prof = result(), profile()
    r["meta"]["filing_status"] = recorded_status
    prof["filing_status"] = "HoH"
    if benefits:
        p["years"][0].update(social_security=20000, social_security_inputs={
            "tax_exempt_interest": 0, "excluded_income_addbacks": 0, "regular_worksheet_confirmed": True})
        # Benefits are already at their 85% cap: 40k wages + 17k taxable SS,
        # less a 10k deduction = 47k. Conversion 8k spans 3k at 10% and 5k at 20%.
        r["form_1040"].update(line_6a_social_security=20000, line_6b_taxable_ss=17000,
            line_11_agi=57000, line_24_total_tax=4700)
        r["social_security_worksheet"] = {"regular_worksheet_confirmed": True, "tax_year": 2025,
            "inputs": {"ss_benefits": 20000, "other_income": 40000, "tax_exempt_interest": 0,
                       "adjustments": 0, "filing_status": "HoH", "lived_apart_from_spouse_all_year": None,
                       "lump_sum_prior_year_benefits": 0, "excluded_income_addbacks": 0}}
    row = build(p, r=r, prof=prof)["years"][0]
    assert row["federal_cost"] == (1300 if benefits else 800)
    assert row["calculation"] == "computed"


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
    # Ordinary taxable 30k leaves 20k to the first bracket edge at 50k. 80%
    # taxable means a 25k gross conversion, not 20k. The next cent crosses it.
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


def test_top_irmaa_boundary_is_inclusive_and_fill_stops_one_dollar_below():
    p = plan()
    p["ira"].update(traditional_balance=500000, basis=0)
    prof = profile()
    prof["prior_year"]["ira_basis_by_taxpayer"]["primary"] = 0
    p["years"] = p["years"][:1]
    row = p["years"][0]
    del row["conversion"]
    row.update(fill_to={"kind": "irmaa", "tier": 5}, premium_law={"mode": "fetched"})
    selected = build(p, prof=prof, laws={2025: law(), 2027: law(2027)})["years"][0]
    # A return rounded to whole dollars would carry 249,999.99 as 250,000.
    assert selected["conversion"] == 209999 and selected["magi"] == 249999


def test_premium_increment_is_separate_and_prorated_for_enrollment_months():
    p = plan()
    p["years"] = p["years"][:1]
    row = p["years"][0]
    row.update(filing_status="MFJ", conversion=25000, premium_law={"mode": "hypothetical", "basis": "held_at_2025"},
        medicare_enrollees_two_years_later=2, medicare_months_two_years_later=[12, 6],
        medicare_parts_b_and_d_confirmed=True)
    r, prof = result(), profile()
    r["meta"]["filing_status"] = prof["filing_status"] = "MFJ"
    output = build(p, r=r, prof=prof)
    selected = output["years"][0]
    # AGI 60k crosses the first 50k tier: 1,000 per beneficiary-year. 18 months
    # = 1.5 beneficiary-years -> 1,500 premium cost, apart from 2,000 federal.
    assert selected["irmaa_cost"] == 1500 and selected["federal_cost"] == 2000
    assert output["totals"]["federal_cost"] == 2000


@pytest.mark.parametrize("status", ["Single", "HOH", "MFS", "QSS"])
def test_one_return_cannot_price_a_second_nonjoint_beneficiary(status):
    p = plan()
    row = p["years"][0]
    row.update(filing_status=status, medicare_enrollees_two_years_later=2,
               medicare_months_two_years_later=[12, 12], medicare_parts_b_and_d_confirmed=True)
    if status == "MFS":
        row.update(spouse_itemizes=False, medicare_lived_apart_all_year=True)
    with pytest.raises(P.PlanError, match="joint lookback return"):
        P.validate_plan(p, 2025)


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
    # Single regular worksheet: provisional 40k -> taxable SS 9,600; after an
    # 8k taxable conversion, provisional 48k -> taxable SS 16,400. Federal 10%
    # on 8k + 6,800 extra taxable benefits = 1,480, not 800.
    assert row["federal_cost"] == 1480
    assert row["rate_inputs"]["deduction"] == 10000
    assert row["baseline_income_inputs"]["social_security"]["worksheet"]["ss_benefits"] == 20000


def test_self_employment_and_qbi_remain_in_the_shared_keystone():
    p = future_plan()
    p["years"][0]["self_employment_net"] = 20000
    row = build(p)["years"][0]
    # SE 2,825.91, half 1,412.96, QBI (20k - 1,412.96) x 20% = 3,717.41.
    # Taxable 44,869.63 -> 52,869.63: regular 4,486.96 -> 5,573.93;
    # SE does not change with the IRA conversion. Delta 1,086.97.
    assert row["federal_cost"] == 1086.97


def test_niit_magi_and_irmaa_magi_are_independent_concepts():
    p = future_plan()
    row = p["years"][0]
    row.update(wages=190000, other_ordinary_income=20000, net_investment_income=20000,
               irmaa_magi_addbacks=50000)
    output = build(p)["years"][0]
    # Baseline NIIT 380 -> 684, in addition to 8k x 30% regular = 2,400.
    assert output["federal_cost"] == 2704
    assert output["magi"] == 268000
    row["niit_magi_addbacks"] = 20000
    # NIIT is already saturated at the baseline, so incremental NIIT = 0.
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
    # CA synthetic 10% rate, fixed 10k deduction and 400 exemption credit:
    # 2,600 -> 3,400 after an 8k taxable conversion; no federal anchor for 2025.
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


def statutory_irmaa(year=2026):
    # The 2026 CMS boundaries, shaped as 42 USC 1395r(i)(3)(C) prints them:
    # joint twice the unmarried amounts but 150% at the top, cohabiting MFS the
    # unmarried threshold and the top less it. Surcharges are synthetic.
    single = [109000, 137000, 171000, 205000, 500000]
    tiers = {"single": single, "mfj": [218000, 274000, 342000, 410000, 750000], "mfs": [109000, 391000]}
    cfg = law(year)
    cfg["federal"]["irmaa"]["tiers"] = {status: [
        {"magi_threshold": amount, "annual_surcharge_per_person": 1000 * (i + 1 + (3 if status == "mfs" else 0))}
        for i, amount in enumerate(amounts)] for status, amounts in tiers.items()}
    return cfg


@pytest.mark.parametrize("base,target,single,joint,separate", [
    # Before 2027 too, only the lower amounts move.
    (2025, 2026, [112000, 141000, 176000, 211000, 500000], [224000, 282000, 352000, 422000, 750000], [112000, 388000]),
    # 1.03: 112,270 -> 112,000; 141,110 -> 141,000; 176,130 -> 176,000;
    # 211,150 -> 211,000. The $500,000 top is not indexed before 2028.
    (2026, 2027, [112000, 141000, 176000, 211000, 500000], [224000, 282000, 352000, 422000, 750000], [112000, 388000]),
    # 1.03^2 = 1.0609: 115,638 -> 116,000; 145,343 -> 145,000; 181,414 ->
    # 181,000; 217,485 -> 217,000. From 2028 the top is indexed from August
    # 2026, one year here: 515,000, so the joint top is 772,500.
    (2026, 2028, [116000, 145000, 181000, 217000, 515000], [232000, 290000, 362000, 434000, 772500], [116000, 399000]),
])
def test_indexed_irmaa_follows_the_statutory_tables(base, target, single, joint, separate):
    indexed, _ = P.resolve_law({"mode": "hypothetical", "basis": f"indexed_from_{base}_at_0.03"}, target,
                               {base: statutory_irmaa(base)})
    tiers = indexed["federal"]["irmaa"]["tiers"]
    assert [t["magi_threshold"] for t in tiers["single"]] == single
    assert [t["magi_threshold"] for t in tiers["mfj"]] == joint
    assert [t["magi_threshold"] for t in tiers["mfs"]] == separate
    for status in ("single", "mfj", "mfs"):
        assert P.read_irmaa_tiers(indexed, {"single": "Single", "mfj": "MFJ", "mfs": "MFS"}[status],
                                  lived_apart=False if status == "mfs" else None)


def test_federal_only_plan_never_declares_a_zero_state_liability():
    output = build()
    assert all(row["state_cost"] is None for row in output["years"])
    assert output["totals"]["state_cost"] is None


def closing_8606(year_end_value=300000, converted=0, contribution=0, after_year_end=0, prof=None):
    from agents.lib.tax_computations.federal.ira_basis import compute_individual_8606
    distributions = ([{"doc_id": "doc-1", "ordinary": 0, "converted": converted, "excluded": 0,
                       "excluded_taxable": 0, "exclusion_reason": None}] if converted else [])
    inputs = {"tax_year": 2025, "by_taxpayer": {"primary": {
        "regular_worksheet_confirmed": True, "accounts_complete": True,
        "accounts": [{"account_id": "acct-1", "person": "primary", "kind": "traditional_ira",
                      "year_end_value": year_end_value}],
        "distributions": distributions, "nondeductible_contrib_current": contribution,
        "contrib_made_by_deadline": after_year_end, "outstanding_rollovers": 0}}}
    return compute_individual_8606(inputs, prof or profile())


def test_a_next_year_plan_starts_from_the_closing_8606_balance():
    r = result()
    r["form_8606"] = closing_8606()
    r["form_1040"].update(line_4a_ira_distributions=0, line_4b_taxable_ira=0)
    p = plan()
    p["start_year"], p["years"] = 2026, [annual(2026, conversion=50000)]
    p["ira"].update(basis_source="closing_form_8606", basis=20000, traditional_balance=100000)
    with pytest.raises(P.PlanError, match="line 6"):
        build(p, r=r)
    p["ira"]["traditional_balance"] = 300000
    # Line 9 = 250,000 + 50,000; 50,000 x 20,000 / 300,000 = 3,333.33 nontaxable.
    assert build(p, r=r)["years"][0]["taxable_conversion"] == 46666.67


@pytest.mark.parametrize("flows", [{"converted": 50000}, {"contribution": 7000}])
def test_the_filing_year_refuses_ira_flows_the_return_already_records(flows):
    r = result()
    r["form_8606"] = closing_8606(50000, **flows)
    with pytest.raises(P.PlanError, match="already records"):
        build(r=r)


def test_tax_exempt_interest_is_part_of_irmaa_magi():
    p = future_plan()
    p["years"][0].update(social_security=20000, social_security_inputs={
        "tax_exempt_interest": 40000, "excluded_income_addbacks": 0, "regular_worksheet_confirmed": True})
    with pytest.raises(P.PlanError, match="tax-exempt interest"):
        P.validate_plan(p, 2025)
    p["years"][0]["irmaa_magi_addbacks"] = 40000
    P.validate_plan(p, 2025)
    r = result()
    r["form_1040"]["line_2a_tax_exempt_interest"] = 30000
    with pytest.raises(P.PlanError, match="line 2a"):
        build(r=r)


def test_child_credit_years_say_the_refundable_credit_is_not_repriced():
    p = future_plan()
    p["years"][0]["ctc_children"] = 2
    assert any("additional child tax credit" in note for note in build(p)["years"][0]["notes"])
    assert any("additional child tax credit" in line for line in P.LIMITATIONS)


def test_a_forecast_loss_cannot_ride_in_other_ordinary_income():
    p = future_plan()
    p["years"][0]["other_ordinary_income"] = -5000
    with pytest.raises(P.PlanError, match="cannot be negative"):
        P.validate_plan(p, 2025)


def test_the_filing_year_keeps_the_returns_schedule_1_loss():
    r = result()
    r["form_1040"].update(line_8_schedule1_other=-5000, line_11_agi=35000, line_24_total_tax=2500)
    p = plan()
    p["years"] = p["years"][:1]
    p["years"][0]["other_ordinary_income"] = -5000
    # Taxable 25,000 + 8,000 taxable conversion stays in the 10% bracket.
    assert build(p, r=r)["years"][0]["federal_cost"] == 800


def test_an_unreviewed_california_year_prices_no_state_cost():
    from tests.test_state_planning import case
    ca_result, ca_law = case("CA", agi=40000, status="Single")
    p = future_plan()
    inputs = copy.deepcopy(ca_result["state_planning_inputs"])
    inputs["tax_year"] = 2026
    p["years"][0].update(state="CA", state_inputs=inputs)
    synthetic = law()
    synthetic["state"] = ca_law["state"]
    row = build(p, laws={2025: synthetic})["years"][0]
    assert row["state_cost"] is None
    assert any("not been reviewed" in note for note in row["notes"])


@pytest.mark.parametrize("lived_apart,surcharge", [(False, 2000), (True, 3000)])
def test_cohabiting_mfs_prices_from_its_own_premium_table(lived_apart, surcharge):
    p = future_plan()
    p["years"][0].update(filing_status="MFS", spouse_itemizes=False, medicare_lived_apart_all_year=lived_apart,
        wages=140000, conversion=20000, premium_law={"mode": "hypothetical", "basis": "held_at_2025"},
        medicare_enrollees_two_years_later=1, medicare_months_two_years_later=[12],
        medicare_parts_b_and_d_confirmed=True)
    # AGI 140,000 + 16,000 taxable = 156,000: the cohabiting table's second
    # (top) tier at 100,000, but the unmarried table's third tier at 150,000.
    assert build(p)["years"][0]["irmaa_surcharge"] == surcharge


def test_each_filing_year_anchor_refuses_a_contradiction():
    p = plan()
    p["years"][0]["law"] = {"mode": "hypothetical", "basis": "held_at_2025"}
    with pytest.raises(P.PlanError, match="fetched covered-year law"):
        P.validate_plan(p, 2025)
    prof = profile()
    prof["address"]["state"] = "CA"
    with pytest.raises(P.PlanError, match="California return"):
        build(prof=prof)
    r = result()
    r["form_1040"]["line_10_adjustments"] = 500
    with pytest.raises(P.PlanError, match="non-SE above-AGI adjustments"):
        build(r=r)
    r = result()
    r["meta"]["source"] = "imported_prior_return"
    with pytest.raises(P.PlanError, match="imported seed"):
        build(r=r)
    prof = profile()
    prof["prior_year"]["import_unresolved"] = {"source_year": 2024, "questions": ["capital_loss"]}
    with pytest.raises(P.PlanError, match="imported carryovers"):
        build(prof=prof)


def test_the_filing_year_social_security_worksheet_must_be_the_returns():
    p = plan()
    p["years"] = p["years"][:1]
    r = result()
    p["years"][0].update(social_security=20000, social_security_inputs={
        "tax_exempt_interest": 0, "excluded_income_addbacks": 100, "regular_worksheet_confirmed": True})
    r["form_1040"].update(line_6a_social_security=20000, line_6b_taxable_ss=17000,
                          line_11_agi=57000, line_24_total_tax=4700)
    r["social_security_worksheet"] = {"regular_worksheet_confirmed": True, "tax_year": 2025,
        "inputs": {"ss_benefits": 20000, "other_income": 40000, "tax_exempt_interest": 0,
                   "adjustments": 0, "filing_status": "Single", "lived_apart_from_spouse_all_year": None,
                   "lump_sum_prior_year_benefits": 0, "excluded_income_addbacks": 0}}
    with pytest.raises(P.PlanError, match="original reviewed worksheet"):
        build(p, r=r)


@pytest.mark.parametrize("edit,message", [
    (lambda row: row.update(net_investment_income=1), "cannot exceed"),
    (lambda row: row.update(deduction=9000, additional_standard_deduction=1000), "already includes"),
    (lambda row: row.update(medicare_enrollees_two_years_later=1, medicare_months_two_years_later=[12]),
     "Part B and D"),
])
def test_annual_assumptions_refuse_contradictions(edit, message):
    p = future_plan()
    edit(p["years"][0])
    with pytest.raises(P.PlanError, match=message):
        P.validate_plan(p, 2025)


def test_a_tampered_closing_8606_is_not_a_basis_source():
    r = result()
    r["form_8606"] = closing_8606()
    r["form_8606"]["by_taxpayer"]["primary"]["basis_remaining"] = 25000
    p = plan()
    p["start_year"], p["years"] = 2026, [annual(2026, conversion=50000)]
    p["ira"].update(basis_source="closing_form_8606", basis=25000, traditional_balance=300000)
    with pytest.raises(ValueError):
        build(p, r=r)


def test_cli_refuses_a_name_its_input_does_not_carry(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    renamed = plan()
    renamed["name"] = "other"
    write_secure(root / "scenarios/roth_plan_reviewed.input.json", json.dumps(renamed))
    monkeypatch.setattr("agents.lib.tax_law_fetcher.check_freshness", lambda *a: {"status": "FRESH"})
    assert P._main(["--workspace", str(root), "--name", "reviewed"]) == 2
    assert not (root / "scenarios/roth_plan_reviewed.json").exists()


def test_cli_refuses_a_law_that_changed_during_its_freshness_check(tmp_path, monkeypatch):
    root = workspace(tmp_path)

    def fresh(year, workspace):
        changed = law()
        changed["federal"]["filing_statuses"]["single"]["standard_deduction"] = 1
        write_secure(root / "tax_law_2025.json", json.dumps(changed))
        return {"status": "FRESH"}
    monkeypatch.setattr("agents.lib.tax_law_fetcher.check_freshness", fresh)
    assert P._main(["--workspace", str(root), "--name", "reviewed"]) == 2
    assert not (root / "scenarios/roth_plan_reviewed.json").exists()


@pytest.mark.parametrize("source", ["closing_form_8606", "reviewed_worksheet"])
def test_a_next_year_plan_cannot_start_below_the_closing_record(source):
    prof = profile()
    prof["prior_year"]["ira_basis_by_taxpayer"]["primary"] = 0
    r = result()
    r["form_8606"] = closing_8606(10000, contribution=7000, after_year_end=7000, prof=prof)
    r["form_1040"].update(line_4a_ira_distributions=0, line_4b_taxable_ira=0)
    p = plan()
    p["start_year"], p["years"] = 2026, [annual(2026, conversion=7000)]
    p["ira"].update(basis_source=source, basis=7000, traditional_balance=10000)
    with pytest.raises(P.PlanError, match="below"):
        build(p, r=r, prof=prof)
    # A deposit after the year closed only adds, pricing more of the conversion.
    p["ira"]["traditional_balance"] = 20000
    # Line 9 = 13,000 + 7,000; 7,000 x 7,000 / 20,000 = 2,450 nontaxable.
    assert build(p, r=r, prof=prof)["years"][0]["taxable_conversion"] == 4550


def test_a_legacy_record_floor_includes_its_january_to_april_contributions():
    from agents.lib.tax_computations import compute_form_8606
    flat = compute_form_8606(7000, 0, 10000, 0, 0, contrib_made_by_deadline=7000)
    r = result()
    r["form_8606"] = {"by_taxpayer": {}, **flat}
    p = plan()
    p["start_year"], p["years"] = 2026, [annual(2026, conversion=10000)]
    p["ira"].update(basis_source="reviewed_worksheet", basis=7000, traditional_balance=10000)
    prof = profile()
    prof["prior_year"] = {"ira_basis": 0}
    with pytest.raises(P.PlanError, match="below"):
        build(p, r=r, prof=prof)


def test_a_next_year_plan_starts_with_january_to_april_contributions():
    # Backdoor: 7,000 nondeductible for 2025 deposited in February 2026 is in
    # line 14's basis and in the account, though not in line 6 (10,000).
    prof = profile()
    prof["prior_year"]["ira_basis_by_taxpayer"]["primary"] = 0
    r = result()
    r["form_8606"] = closing_8606(10000, contribution=7000, after_year_end=7000, prof=prof)
    r["form_1040"].update(line_4a_ira_distributions=0, line_4b_taxable_ira=0)
    p = plan()
    p["start_year"], p["years"] = 2026, [annual(2026, conversion=10000)]
    p["ira"].update(basis_source="closing_form_8606", basis=7000, traditional_balance=10000)
    with pytest.raises(P.PlanError, match="line 4"):
        build(p, r=r, prof=prof)
    p["ira"]["traditional_balance"] = 17000
    # Line 9 = 7,000 + 10,000; 10,000 x 7,000 / 17,000 = 4,117.65 nontaxable.
    assert build(p, r=r, prof=prof)["years"][0]["taxable_conversion"] == 5882.35


def test_the_spouses_own_distribution_does_not_block_the_primarys_plan():
    r = result()
    r["meta"]["filing_status"] = "MFJ"
    prof = profile()
    prof["filing_status"] = "MFJ"
    # The household total repeats the spouse's own record.
    r["form_8606"] = {"by_taxpayer": {"spouse": {"line_7_distributions": 5000}}, "line_7_distributions": 5000}
    r["form_1040"]["line_4a_ira_distributions"] = 5000
    p = plan()
    p["years"] = p["years"][:1]
    p["years"][0]["filing_status"] = "MFJ"
    P._basis_check(p, r, prof, 2025)


def test_the_filing_year_keeps_its_interest_beside_a_schedule_1_loss():
    p = plan()
    p["years"] = p["years"][:1]
    p["years"][0].update(other_ordinary_income=-5000, net_investment_income=1000)
    P.validate_plan(p, 2025)


@pytest.mark.parametrize("layout", ["flat", "line_4a"])
def test_the_filing_year_reads_household_lines_without_an_owner_record(layout):
    r = result()
    if layout == "flat":
        r["form_8606"] = {"by_taxpayer": {}, "line_8_converted": 50000}
    else:
        r["form_1040"]["line_4a_ira_distributions"] = 50000
    with pytest.raises(P.PlanError, match="already records"):
        build(r=r)


def recorded_dec_31(value):
    r = result()
    r["form_8606"] = closing_8606(value)
    r["form_1040"].update(line_4a_ira_distributions=0, line_4b_taxable_ira=0)
    return r


def test_the_filing_year_prices_from_its_recorded_dec_31_value():
    p = plan()
    p["years"] = p["years"][:1]
    p["years"][0]["conversion"] = 20000
    row = build(p, r=recorded_dec_31(120000))["years"][0]
    # An up year: a year-end conversion leaves more, 120,000 - 20,000 =
    # 100,000 against 120,000 x (1 - 20,000 / 100,000) = 96,000. Line 9 =
    # 120,000; 20,000 x 20,000 / 120,000 = 3,333.33 nontaxable.
    assert (row["taxable_conversion"], row["ira_next_year"]) == (16666.67, 100000)
    assert any("Form 8606 line 6" in note for note in row["notes"])


def test_a_deposit_line_6_holds_is_never_read_as_growth():
    # Last year's 7,000 January contribution: in line 6 (13,000) and in the
    # opening basis, though not in the 6,000 Jan 1 balance entered.
    prof = profile()
    prof["prior_year"]["ira_basis_by_taxpayer"]["primary"] = 7000
    r = result()
    r["form_8606"] = closing_8606(13000, prof=prof)
    r["form_1040"].update(line_4a_ira_distributions=0, line_4b_taxable_ira=0)
    p = plan()
    p["years"] = p["years"][:1]
    p["ira"].update(traditional_balance=6000, basis=7000)
    p["years"][0]["conversion"] = 6000
    # Line 6 = 13,000 - 6,000 = 7,000; line 9 = 13,000; 6,000 x 7,000 /
    # 13,000 = 3,230.77 nontaxable.
    assert build(p, r=r, prof=prof)["years"][0]["taxable_conversion"] == 2769.23


def test_a_tampered_filing_year_record_is_not_a_dec_31_value():
    r = recorded_dec_31(120000)
    r["form_8606"]["by_taxpayer"]["primary"]["basis_remaining"] = 25000
    with pytest.raises(ValueError):
        build(r=r)


def test_a_down_year_is_not_forecast_at_its_own_loss():
    p = plan()
    p["ira"]["growth_rate"] = .06
    p["years"] = [annual(2025), annual(2026), annual(2027)]
    for row in p["years"]:
        row["conversion"] = 10000
    years = build(p, r=recorded_dec_31(80000))["years"]
    # 2025 lost 20%, so a Jan 1 conversion leaves more: line 6 = 80,000 x
    # (1 - 10,000 / 100,000) = 72,000 against 70,000, line 9 = 82,000,
    # 10,000 x 20,000 / 82,000 = 2,439.02 nontaxable. Then the 6% forecast:
    # 62,000 x 1.06 = 65,720, 10,000 x 17,560.98 / 75,720 = 2,319.20; and
    # 55,720 x 1.06 = 59,063.20, 10,000 x 15,241.78 / 69,063.20 = 2,206.93.
    assert [y["taxable_conversion"] for y in years] == [7560.98, 7680.80, 7793.07]
    assert [y["ira_next_year"] for y in years] == [72000, 65720, 59063.20]


def test_a_legacy_single_filer_form_8606_records_the_dec_31_value():
    prof = profile()
    prof["prior_year"] = {"ira_basis": 20000}
    r = result()
    r["form_8606"] = {"by_taxpayer": {}, "line_6_year_end_value": 120000}
    p = plan()
    p["years"] = p["years"][:1]
    p["years"][0]["conversion"] = 20000
    assert build(p, r=r, prof=prof)["years"][0]["taxable_conversion"] == 16666.67


def test_a_falling_index_never_lowers_the_top_below_its_2027_amount():
    indexed, _ = P.resolve_law({"mode": "hypothetical", "basis": "indexed_from_2026_at_-0.02"}, 2028,
                               {2026: statutory_irmaa()})
    tiers = indexed["federal"]["irmaa"]["tiers"]
    assert tiers["single"][-1]["magi_threshold"] == 500000 and tiers["mfj"][-1]["magi_threshold"] == 750000
