"""Regular Pub 915 worksheet effects use synthetic brackets and explicit evidence."""

import copy
import dataclasses
import datetime
import json
import subprocess
from pathlib import Path

import pytest

from agents.lib import roth_ladder, scenario
from agents.lib.tax_computations import compute_scenario, compute_taxable_ss, compute_se_tax
from agents.lib.secure_fs import write_secure

TODAY = datetime.date(2026, 10, 1)
FIXTURES = Path(__file__).parent / "fixtures/scenario"
LAW = json.loads((FIXTURES / "tax_law_2026.json").read_text())
# Reuse the synthetic MFJ bands for MFS; these cases test SS treatment, not rates.
LAW["federal"]["filing_statuses"]["mfs"] = copy.deepcopy(LAW["federal"]["filing_statuses"]["mfj"])
PROFILE = json.loads((FIXTURES / "taxpayer_profile.json").read_text())


def baseline(other=40000, *, exempt=0, status="MFJ", apart=None):
    worksheet = dict(ss_benefits=30000, other_income=other, tax_exempt_interest=exempt,
                     adjustments=0, filing_status=status,
                     lived_apart_from_spouse_all_year=apart,
                     lump_sum_prior_year_benefits=0, excluded_income_addbacks=0)
    taxable = compute_taxable_ss(**worksheet)["taxable_ss"]
    return {"meta": {"tax_year": 2026, "filing_status": status},
            "form_1040": {"line_4b_taxable_ira": other, "line_2a_tax_exempt_interest": exempt,
                          "line_6a_social_security": 30000, "line_6b_taxable_ss": taxable,
                          "line_10_adjustments": 0, "line_11_agi": other + taxable,
                          "line_12_standard_deduction": 30000, "line_24_total_tax": 5000},
            "social_security_worksheet": {"tax_year": 2026, "regular_worksheet_confirmed": True,
                                          "inputs": worksheet}}


def report(result=None, delta=None, base=None):
    return scenario.build_report(result or baseline(), PROFILE, LAW, 2026, "ss-test",
                                 delta or {"ira_distribution": 1000}, base, TODAY)


@pytest.mark.parametrize("other,delta,ss_change", [(10000, 1000, 0), (17000, 1000, 500),
                                                  (40000, 1000, 850), (80000, 1000, 0),
                                                  (40000, -1000, -850)])
def test_regular_worksheet_regions_and_negative_changes(other, delta, ss_change):
    # MFJ, benefits30k: half15k + other;32k/44k bands,25.5k maximum.
    # At17k, the first1k raises taxable benefits500; at40k by850; at80k capped.
    r = report(baseline(other), {"ira_distribution": delta})
    assert r.scenario_outcome.taxable_ss - r.base_outcome.taxable_ss == ss_change
    assert r.diff.delta_agi == delta + ss_change
    assert r.diff.delta_income == delta


def test_tax_anchor_and_marginal_denominator_include_full_cost():
    r = report()
    # Provisional55k ->56k makes850more taxable;1,850income at synthetic20%=370.
    # Real line24=5k retained despite simplified baseline tax3,070.
    assert r.base_outcome.taxable_ss == 15350
    assert r.scenario_outcome.taxable_ss == 16200
    assert r.diff.baseline_anchored_total_tax == 5000
    assert r.diff.anchored_scenario_total_tax == 5370
    assert r.diff.delta_total_tax == 370
    assert r.diff.marginal_rate == .37


def test_stacks_and_inverse_preserve_original_evidence():
    first = report()
    stacked = report(delta={"ira_distribution": 1000}, base=dataclasses.asdict(first))
    one_step = report(delta={"ira_distribution": 2000})
    assert stacked.scenario_outcome == one_step.scenario_outcome
    assert stacked.diff.anchored_scenario_total_tax == one_step.diff.anchored_scenario_total_tax
    inverse = report(delta={"ira_distribution": -1000}, base=dataclasses.asdict(first))
    assert inverse.diff.anchored_scenario_total_tax == 5000
    assert inverse.scenario_outcome.taxable_ss == 15350


def test_munis_and_exclusion_addbacks_enter_provisional_income():
    result = baseline(17000, exempt=15000)
    # Half15k+IRA17k+munis15k=47k;85%band rather than the first50%band.
    r = report(result)
    assert r.base_outcome.taxable_ss == 8550
    assert r.scenario_outcome.taxable_ss == 9400
    result = baseline(17000)
    result["social_security_worksheet"]["inputs"]["excluded_income_addbacks"] = 15000
    result["form_1040"]["line_6b_taxable_ss"] = 8550
    assert report(result).scenario_outcome.taxable_ss == 9400


@pytest.mark.parametrize("apart,change", [(True, 850), (False, 0)])
def test_mfs_living_arrangement_changes_the_path(apart, change):
    # At35k other+15k half benefits, lived-apart taxable18,100; together capped25,500.
    r = report(baseline(35000, status="MFS", apart=apart))
    assert r.scenario_outcome.taxable_ss - r.base_outcome.taxable_ss == change


def test_gains_loss_cap_and_qualified_subset_are_not_double_counted():
    result = baseline()
    result["form_1040"].update(line_4b_taxable_ira=43000, line_7_capital_gain=-3000)
    result["schedule_d"] = {"line_16_combined": -10000}
    r = report(result, {"capital_gains": 1000})
    assert r.diff.delta_income == 0  # loss remains beyond the deductible limit
    assert r.diff.delta_agi == 0
    assert r.diff.delta_total_tax == 0
    r = report(delta={"ordinary_dividends": 1000, "qualified_dividends": 1000})
    assert r.scenario_outcome.taxable_ss == 16200  #850, not1,700


def test_se_half_deduction_reprices_the_social_security_worksheet():
    result = baseline()
    half = compute_se_tax(40000, ss_wage_base=100000)["deductible_half"]
    result["schedule_c"] = {"line_31_net": 40000}
    result["form_1040"].update(line_4b_taxable_ira=0, line_8_schedule1_other=40000,
                               line_10_adjustments=half)
    worksheet = result["social_security_worksheet"]["inputs"]
    worksheet["adjustments"] = half
    result["form_1040"]["line_6b_taxable_ss"] = compute_taxable_ss(**worksheet)["taxable_ss"]
    r = report(result, {"self_employment_net": 1000})
    # Incremental halfSE=70.65. Rounded worksheet totals12,947.98 and13,737.92
    # differ789.94 (round each worksheet before subtracting, not the raw delta).
    assert round(r.scenario_outcome.taxable_ss - r.base_outcome.taxable_ss, 2) == 789.94


def test_roth_sweep_shares_recompute_and_keeps_conversion_denominator():
    r = roth_ladder.build_ladder(baseline(), PROFILE, LAW, 2026,
                                max_amount=2000, step=1000, name="ss", today=TODAY)
    assert [row.total_tax for row in r.rungs] == [5000, 5370, 5740]
    assert r.rungs[1].marginal_rate == .37
    assert "Taxable Social Security is recomputed" in roth_ladder.render_markdown(r)


def test_schedule_a_repricing_uses_agi_including_additional_taxable_benefits():
    from agents.lib.tax_computations import compute_schedule_a
    result = baseline()
    result["schedule_a"] = compute_schedule_a(55350, "MFJ", 2026, 30000, 40400,
                                               medical_expenses=10000, mortgage_interest=30000,
                                               top_bracket_income=0)
    result["form_1040"]["line_12_standard_deduction"] = result["schedule_a"]["itemized_total"]
    r = report(result)
    # AGI+1,850 raises the7.5%medicalfloor138.75; taxableincome rises1,988.75.
    assert r.schedule_a_repriced
    assert r.diff.delta_taxable_income == 1988.75


def test_launcher_uses_recorded_worksheet_and_preserves_baseline(tmp_path):
    for filename, value in [("result.json", baseline()), ("taxpayer_profile.json", PROFILE),
                            ("tax_law_2026.json", LAW)]:
        write_secure(tmp_path / filename, json.dumps(value))
    original = (tmp_path / "result.json").read_bytes()
    command = ["sh", "agents/lib/run.sh", "-m", "agents.lib.scenario", "--workspace", str(tmp_path),
               "--result", "result.json", "--year", "2026", "--name", "ss-cli", "--json", "roth_conversion=1000"]
    done = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    stored = json.loads((tmp_path / "scenarios/ss-cli.json").read_text())
    assert stored["diff"]["delta_total_tax"] == 370
    assert stored["scenario_outcome"]["taxable_ss"] == 16200
    assert (tmp_path / "result.json").read_bytes() == original


@pytest.mark.parametrize("mutation", ["missing", "gross", "exempt", "adjustments", "taxable",
                                     "year", "status", "addbacks_missing", "mfs_flag", "lump",
                                     "combined_ira", "nan", "infinity", "bool"])
def test_missing_inconsistent_or_unsupported_evidence_refuses(mutation):
    result = baseline()
    worksheet = result["social_security_worksheet"]["inputs"]
    if mutation == "missing":
        result.pop("social_security_worksheet")
    elif mutation == "gross":
        worksheet["ss_benefits"] += 1
    elif mutation == "exempt":
        worksheet["tax_exempt_interest"] = 1
    elif mutation == "adjustments":
        worksheet["adjustments"] = 1
    elif mutation == "taxable":
        result["form_1040"]["line_6b_taxable_ss"] += 1
    elif mutation == "year":
        result["social_security_worksheet"]["tax_year"] = None
    elif mutation == "status":
        worksheet["filing_status"] = "QSS"
    elif mutation == "addbacks_missing":
        worksheet.pop("excluded_income_addbacks")
    elif mutation == "mfs_flag":
        result = baseline(status="MFS", apart=True)
        result["social_security_worksheet"]["inputs"]["lived_apart_from_spouse_all_year"] = None
    elif mutation == "lump":
        worksheet["lump_sum_prior_year_benefits"] = 1
    elif mutation == "combined_ira":
        result["social_security_worksheet"]["regular_worksheet_confirmed"] = False
    else:
        worksheet["excluded_income_addbacks"] = {"nan": float("nan"), "infinity": float("inf"), "bool": True}[mutation]
    with pytest.raises((scenario.ScenarioError, scenario.ProjectionError)):
        report(result)


def test_legacy_or_changed_evidence_in_a_saved_stack_refuses():
    saved = dataclasses.asdict(report())
    saved["inputs"].pop("social_security")
    with pytest.raises(scenario.ScenarioError, match="Social Security evidence"):
        report(base=saved)
    saved = dataclasses.asdict(report())
    saved["inputs"]["social_security"]["origin"]["wages"] = 1
    with pytest.raises(scenario.ScenarioError, match="Social Security evidence"):
        report(base=saved)


def test_keystone_validates_evidence_and_leaves_inputs_unchanged():
    inputs = scenario.extract_baseline_inputs(baseline())
    before = copy.deepcopy(inputs)
    kwargs = scenario._rate_kwargs(PROFILE, LAW, "MFJ", baseline())
    assert compute_scenario(inputs, {}, **kwargs)["taxable_ss"] == 15350
    assert inputs == before
    inputs["social_security"]["origin"]["wages"] = True
    with pytest.raises(ValueError, match="numeric"):
        compute_scenario(inputs, {}, **kwargs)


@pytest.mark.parametrize("tool", ["scenario", "roth"])
@pytest.mark.parametrize("mutation", ["cache", "requested"])
def test_planning_year_and_declared_cache_year_must_match_worksheet(tool, mutation):
    law = copy.deepcopy(LAW)
    year = 2026
    if mutation == "cache":
        law["tax_year"] = 2025
    else:
        year = 2025
    with pytest.raises((scenario.ScenarioError, scenario.ProjectionError)):
        if tool == "scenario":
            scenario.build_report(baseline(), PROFILE, law, year, "year", {"ira_distribution": 1000}, None, TODAY)
        else:
            roth_ladder.build_ladder(baseline(), PROFILE, law, year, max_amount=1000,
                                    step=1000, name="year", today=TODAY)
