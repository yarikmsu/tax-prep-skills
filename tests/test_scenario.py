"""Tests for scenario modeling — tax_computations.compute_scenario + agents/lib/scenario.py."""
import dataclasses
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import scenario as sc
from agents.lib.tax_computations import compute_scenario

FIX = Path(__file__).resolve().parent / "fixtures" / "scenario"
REPO = Path(__file__).resolve().parent.parent

# MFJ brackets matching the fixture tax_law: 10% to 20k, 20% to 200k, 30% above.
MFJ_BRACKETS = [
    {"rate": 0.10, "min": 0, "max": 20000},
    {"rate": 0.20, "min": 20000, "max": 200000},
    {"rate": 0.30, "min": 200000, "max": None},
]
# The deduction a return took, as the planning tools read it from line 12.
_RETURN = {"form_1040": {"line_12_standard_deduction": 30000}}
RATE_KW = dict(brackets=MFJ_BRACKETS, deduction=30000,
               cg_zero_limit=90000, cg_fifteen_limit=550000)


def _load_fixture_workspace(tmp_path) -> Path:
    for f in ("result.json", "taxpayer_profile.json", "tax_law_2026.json"):
        shutil.copy(FIX / f, tmp_path / f)
    return tmp_path


# --------------------------------------------------------------------------
# compute_scenario (the keystone)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["Married Filing Jointly",
                                    "Married Filing Separately",
                                    "Head of Household", None, ""])
def test_rate_kwargs_refuses_a_non_canonical_status_for_all_three_skills(status):
    """`_rate_kwargs` is the chokepoint /tax-scenario, /tax-roth-convert and
    /tax-harvest all pass their status through, so pinning it here pins the
    three skills at once.

    All three read `filing_status` off result.json meta (falling back to the
    profile) and hand it straight here with no check of their own. Before the
    guard moved INTO `_full_status_key`/`_binary_status_key`, a spelled-out
    status resolved to the single column and the skills priced a conversion or
    a harvest on single brackets and the single standard deduction — the
    understating direction — while /tax-project and /tax-withholding, whose
    call sites had been given the check individually, refused the same file.

    It must refuse BEFORE reading tax_law, or a workspace missing the status
    reports a missing-brackets problem instead of the missing status.
    """
    with pytest.raises(sc.ProjectionError):
        sc._rate_kwargs({}, {}, status, _RETURN)


def test_compute_scenario_zero_delta_is_baseline():
    base = {"wages": 150000}
    out = compute_scenario(base, {}, **RATE_KW)
    out2 = compute_scenario(base, {"wages": 0}, **RATE_KW)
    assert out["total_federal_tax"] == out2["total_federal_tax"]


def test_compute_scenario_wages_delta_moves_ordinary():
    base = {"wages": 150000}
    out0 = compute_scenario(base, {}, **RATE_KW)
    out1 = compute_scenario(base, {"wages": 10000}, **RATE_KW)
    # +$10K wages at the 20% ordinary bracket → +$2,000 tax.
    assert round(out1["total_federal_tax"] - out0["total_federal_tax"], 2) == 2000.0
    assert out1["agi"] - out0["agi"] == 10000.0


def test_compute_scenario_schedule_1a_lowers_taxable_income():
    """Codex round-2 P2: a below-AGI Schedule 1-A deduction (line 13b) is a
    constant that the keystone subtracts before QBI — it lowers taxable income
    (so marginal deltas price from the correct position) but leaves AGI
    untouched."""
    base = {"wages": 150000}
    with_1a = {"wages": 150000, "schedule_1a_deduction": 20000}
    out = compute_scenario(base, {}, **RATE_KW)
    out_1a = compute_scenario(with_1a, {}, **RATE_KW)
    assert out_1a["agi"] == out["agi"]
    assert out_1a["taxable_income"] == out["taxable_income"] - 20000
    assert out_1a["total_federal_tax"] < out["total_federal_tax"]
    # A negative, bool, NaN, or Infinity value is rejected (Codex round-18 P2:
    # json.loads accepts NaN/Infinity, and they slip past a bare `< 0` check).
    import pytest
    for bad in ({"wages": 1, "schedule_1a_deduction": -5},
                {"wages": 1, "schedule_1a_deduction": True},
                {"wages": 1, "schedule_1a_deduction": float("nan")},
                {"wages": 1, "schedule_1a_deduction": float("inf")}):
        with pytest.raises(ValueError):
            compute_scenario(bad, {}, **RATE_KW)


def test_compute_scenario_capital_gains_uses_preferential_rate():
    base = {"wages": 150000}
    out0 = compute_scenario(base, {}, **RATE_KW)
    out1 = compute_scenario(base, {"capital_gains": 20000}, **RATE_KW)
    # LTCG taxed at 15% preferential, not the 20% ordinary bracket.
    assert round(out1["total_federal_tax"] - out0["total_federal_tax"], 2) == 3000.0
    assert out1["used_qdcg"] is True


def test_compute_scenario_refuses_preferential_income_without_cg_limits():
    """X2 (09-05 review, E5): with either limit None the keystone used to fall
    through to `compute_bracket_tax`, pricing a $20K LTCG at the 20% ordinary
    bracket instead of 0/15% — an OVER-statement of the number the user acts
    on, the direction AGENTS.md §4 makes a hard error. It refuses now, naming
    the fix. Only `loss_harvest` guarded this; scenario, roth_ladder, ptet and
    projection's own worksheet branch all fell through."""
    base = {"wages": 150000}
    kw = dict(brackets=MFJ_BRACKETS, deduction=30000)
    with pytest.raises(ValueError, match="tax-setup"):
        compute_scenario(base, {"capital_gains": 20000}, **kw)
    with pytest.raises(ValueError, match="tax-setup"):
        compute_scenario(base, {"qualified_dividends": 1, "ordinary_dividends": 1}, **kw)
    # One limit present is not enough: the worksheet needs both breakpoints.
    with pytest.raises(ValueError, match="tax-setup"):
        compute_scenario(base, {"capital_gains": 1},
                         **dict(kw, cg_zero_limit=90000))


def test_compute_scenario_ordinary_only_still_runs_without_cg_limits():
    """The ordinary-income-only case stays usable: nothing preferential means
    nothing the worksheet would price, so a cache without the limits is not
    a defect for a wages-only what-if. A net capital LOSS is ordinary too
    (it is capped and deducted, never worksheet-priced)."""
    kw = dict(brackets=MFJ_BRACKETS, deduction=30000)
    out = compute_scenario({"wages": 150000}, {"ira_distribution": 20000}, **kw)
    assert out["used_qdcg"] is False and out["total_federal_tax"] > 0
    out = compute_scenario({"wages": 150000}, {"capital_gains": -20000}, **kw)
    assert out["gross_income"] == 147000.0


def test_rate_kwargs_reads_the_cg_limits_through_the_shared_accessor():
    """X1 lands on the same accessor (status-specific keys), so every read
    site has to BE the accessor, not a look-alike `.get` chain beside it.
    Proven behaviourally: only the accessor refuses a NaN breakpoint."""
    from agents.lib.projection import ProjectionError
    b = [{"rate": 0.10, "min": 0, "max": None}]
    tax_law = {"federal": {
        "filing_statuses": {"mfj": {"standard_deduction": 30000, "brackets": b}},
        "capital_gains_rates": {"zero_pct_limit": {"mfj": 11.0},
                                "fifteen_pct_limit": {"mfj": 22.0}}}}
    kw = sc._rate_kwargs({}, tax_law, "MFJ", _RETURN)
    assert (kw["cg_zero_limit"], kw["cg_fifteen_limit"]) == (11.0, 22.0)
    tax_law["federal"]["capital_gains_rates"]["zero_pct_limit"]["mfj"] = float("nan")
    with pytest.raises(ProjectionError, match="positive finite"):
        sc._rate_kwargs({}, tax_law, "MFJ", _RETURN)


def test_compute_scenario_se_delta_adds_se_tax():
    base = {"wages": 150000}
    out = compute_scenario(base, {"self_employment_net": 20000}, **RATE_KW)
    assert out["se_tax"] > 0


def test_compute_scenario_qbi_capped_by_taxable_income():
    # SE-only income: uncapped QBI would be 20% x 40000 = 8000, but taxable
    # income before QBI is only ~7174 (after the standard deduction), so the
    # Form 8995 overall cap limits the deduction to 20% of that — taxable
    # income stays at 80% of the pre-QBI figure instead of being wiped to $0
    # (the old uncapped behavior, which understated tax).
    out = compute_scenario({"self_employment_net": 40000}, {}, **RATE_KW)
    ti_before = out["agi"] - 30000
    assert ti_before > 0
    assert out["taxable_income"] == pytest.approx(0.8 * ti_before, abs=0.02)
    assert out["tax_before_credits"] > 0


def test_compute_scenario_qbi_uncapped_when_income_high():
    # With wages on top, taxable income before QBI far exceeds the QBI base →
    # the QBI deduction survives the overall cap (regression guard). Per
    # §199A(c)(4) / Reg. §1.199A-3(b)(1)(vi) the QBI base is the SE net REDUCED
    # by the deductible half of SE tax (= ½ SE tax, IRC §164(f)), so the
    # deduction is 20% x (40000 - ½ SE tax), NOT 20% x gross 40000.
    out = compute_scenario({"wages": 150000, "self_employment_net": 40000}, {}, **RATE_KW)
    ti_before = out["agi"] - 30000
    half_se_tax = out["se_tax"] / 2
    expected_qbi = 0.20 * (40000 - half_se_tax)
    assert out["taxable_income"] == pytest.approx(ti_before - expected_qbi, abs=0.02)


def test_compute_scenario_se_respects_w2_wage_base():
    # Codex P1: when W-2 wages already exceed the SS wage base, an SE delta owes only
    # the 2.9% Medicare portion (the SS base is fully consumed) — not the full 15.3%.
    kw = dict(RATE_KW, ss_wage_base=176100)
    out = compute_scenario({"wages": 200000}, {"self_employment_net": 20000}, **kw)
    # 20000 * 0.9235 * 0.029 = 535.63, no SS component.
    assert out["se_tax"] == 535.63


def test_compute_scenario_ctc_phases_out_on_large_agi_delta():
    base = {"wages": 150000}
    kw = dict(RATE_KW, num_ctc_children=1, ctc_per_child=2000, ctc_phase_out_start=400000)
    small = compute_scenario(base, {}, **kw)
    assert small["ctc"] == 2000.0
    big = compute_scenario(base, {"wages": 300000}, **kw)  # AGI ~450k > 400k
    assert big["ctc"] < 2000.0


def test_compute_scenario_additional_medicare_applies_over_threshold():
    base = {"wages": 300000}
    kw = dict(RATE_KW, addl_medicare_threshold=250000, addl_medicare_rate=0.009)
    out = compute_scenario(base, {}, **kw)
    # (300000 - 250000) * 0.9% = 450
    assert out["additional_medicare"] == 450.0


def test_compute_scenario_taxable_income_floored_at_zero():
    out = compute_scenario({"wages": 10000}, {"wages": -50000}, **RATE_KW)
    assert out["taxable_income"] == 0.0
    assert out["total_federal_tax"] == 0.0


@pytest.mark.parametrize("bad", ["N/A", None, True])
def test_compute_scenario_rejects_non_numeric_baseline(bad):
    # Codex P2: a corrupted baseline value must error, not silently coerce to 0.
    with pytest.raises(ValueError):
        compute_scenario({"wages": bad}, {}, **RATE_KW)


def test_compute_scenario_negative_wage_delta_with_addl_medicare_threshold():
    # Review: a wage-reducing delta drives merged wages negative; the Form 8959 call
    # must not crash on negative medicare wages (it floors to 0).
    kw = dict(RATE_KW, addl_medicare_threshold=250000)
    out = compute_scenario({"wages": 150000}, {"wages": -200000}, **kw)
    assert out["additional_medicare"] == 0.0
    assert out["total_federal_tax"] == 0.0


def test_compute_scenario_bracket_boundary_blended_marginal():
    # Crossing the 20%->30% boundary (taxable 200k) yields a blended marginal rate.
    base = {"wages": 220000}  # taxable 190k, inside 20% band
    out0 = compute_scenario(base, {}, **RATE_KW)
    out1 = compute_scenario(base, {"wages": 20000}, **RATE_KW)  # taxable 210k
    # 10k @ 20% + 10k @ 30% = 5000 over a 20000 delta = 25% blended.
    assert round(out1["total_federal_tax"] - out0["total_federal_tax"], 2) == 5000.0


def test_compute_scenario_capital_gains_capped_at_taxable_income():
    # Codex: $100k LTCG with a $30k std deduction → $70k taxable, all under the 90k
    # 0% MFJ band → $0 tax (the std deduction shielded the rest), not $1,500.
    out = compute_scenario({}, {"capital_gains": 100000}, **RATE_KW)
    assert out["taxable_income"] == 70000.0
    assert out["tax_before_credits"] == 0.0


def test_compute_scenario_capital_gains_in_zero_pct_band():
    # Low ordinary income → an LTCG delta can land entirely in the 0% QDCG band.
    base = {"wages": 40000}  # taxable 10k ordinary, well under the 90k mfj 0% limit
    out0 = compute_scenario(base, {}, **RATE_KW)
    out1 = compute_scenario(base, {"capital_gains": 20000}, **RATE_KW)
    assert out1["total_federal_tax"] == out0["total_federal_tax"]  # gain taxed at 0%
    assert out1["used_qdcg"] is True


def test_compute_scenario_se_loss_reduces_gross_without_se_tax():
    # A self-employment LOSS reduces gross income but owes no SE tax.
    out = compute_scenario({"wages": 150000}, {"self_employment_net": -20000}, **RATE_KW)
    assert out["gross_income"] == 130000.0
    assert out["se_tax"] == 0.0


def test_compute_scenario_net_capital_loss_capped_at_3000():
    # Codex P2: a net capital loss offsets ordinary income only up to $3,000/yr.
    out = compute_scenario({"wages": 150000}, {"capital_gains": -20000}, **RATE_KW)
    assert out["gross_income"] == 147000.0  # 150000 - 3000 (not - 20000)
    assert out["used_qdcg"] is False        # a net loss yields no preferential income


def test_compute_scenario_partial_capital_loss_within_limit_flows_full():
    # A loss smaller than the cap flows in full.
    out = compute_scenario({"wages": 150000}, {"capital_gains": -1500}, **RATE_KW)
    assert out["gross_income"] == 148500.0


def test_compute_scenario_mfs_capital_loss_limit_is_1500():
    # Codex P2: the MFS net-capital-loss cap is $1,500, not $3,000.
    kw = dict(RATE_KW, capital_loss_limit=1500)
    out = compute_scenario({"wages": 150000}, {"capital_gains": -20000}, **kw)
    assert out["gross_income"] == 148500.0  # 150000 - 1500


def test_rate_kwargs_sets_mfs_capital_loss_limit():
    b = [{"rate": 0.10, "min": 0, "max": None}]
    tax_law = {"federal": {"filing_statuses": {
        "mfs": {"standard_deduction": 15000, "brackets": b},
        "mfj": {"standard_deduction": 30000, "brackets": b}}}}
    profile = {"dependents": []}
    assert sc._rate_kwargs(profile, tax_law, "MFS", _RETURN)["capital_loss_limit"] == 1500
    assert sc._rate_kwargs(profile, tax_law, "MFJ", _RETURN)["capital_loss_limit"] == 3000


@pytest.mark.parametrize("net_st,net_lt,exp_capgains,exp_ordinary", [
    (3000, 8000, 8000, 3000),     # both gains: LT preferential, ST ordinary
    (20000, -10000, 0, 10000),    # ST gain absorbs LT loss → +10k ordinary short-term
    (-5000, 20000, 15000, 0),     # LT gain absorbs ST loss → +15k preferential long-term
    (-2000, -5000, -7000, 0),     # net loss → routed through capital_gains (to be capped)
    (0, 0, 0, 0),
])
def test_net_capital_gains_character(net_st, net_lt, exp_capgains, exp_ordinary):
    # Codex P2: short- and long-term must be netted before modeling.
    assert sc._net_capital_gains(net_st, net_lt) == (exp_capgains, exp_ordinary)


def test_extract_schedule_d_combined_only_uses_line16():
    # Codex P2: a schedule_d with only the combined line (no part_i/part_ii detail)
    # must fall back to line_16_combined, not silently drop the gain.
    result = {"form_1040": {"line_7_capital_gain": 999},
              "schedule_d": {"line_16_combined": 12000}}
    inp = sc.extract_baseline_inputs(result)
    assert inp["capital_gains"] == 12000.0  # combined used, not the stale line 7


def test_extract_schedule_d_combined_only_loss():
    result = {"form_1040": {}, "schedule_d": {"line_16_combined": -8000}}
    # A combined net loss flows through capital_gains (keystone caps it downstream).
    assert sc.extract_baseline_inputs(result)["capital_gains"] == -8000.0


def test_extract_empty_schedule_d_falls_back_to_line7():
    result = {"form_1040": {"line_7_capital_gain": 4444}, "schedule_d": {}}
    assert sc.extract_baseline_inputs(result)["capital_gains"] == 4444.0


def test_extract_baseline_combines_line_13b_and_13c():
    # Codex (PR #46): both the Schedule 1-A total (13b) AND the §170(p)
    # charitable deduction (13c) are fixed below-AGI deductions — the scenario
    # baseline must subtract BOTH before QBI/taxable income, so they combine
    # into the single schedule_1a_deduction input.
    result = {"form_1040": {"line_13b_schedule_1a_deductions": 9000,
                            "line_13c_charitable_non_itemizer": 2000}}
    inp = sc.extract_baseline_inputs(result)
    assert inp["schedule_1a_deduction"] == 11000.0
    # 13c alone (no 13b) still flows through.
    only_13c = sc.extract_baseline_inputs(
        {"form_1040": {"line_13c_charitable_non_itemizer": 1500}})
    assert only_13c["schedule_1a_deduction"] == 1500.0


def test_extract_short_term_gain_long_term_loss_nets():
    # +20k ST with -10k LT → +10k ordinary, no preferential gain (Codex P2 example).
    result = {"form_1040": {}, "schedule_d": {
        "part_i_short_term": {"line_7_net_short_term": 20000},
        "part_ii_long_term": {"line_15_net_long_term": -10000}}}
    inp = sc.extract_baseline_inputs(result)
    assert inp["capital_gains"] == 0.0
    assert inp["other_income"] == 10000.0


# --------------------------------------------------------------------------
# parse_deltas
# --------------------------------------------------------------------------

def test_parse_deltas_basic():
    assert sc.parse_deltas(["wages=10000"]) == {"wages": 10000.0}


def test_parse_deltas_signed_and_plus_prefix():
    assert sc.parse_deltas(["+wages=10000"]) == {"wages": 10000.0}
    assert sc.parse_deltas(["wages=-5000"]) == {"wages": -5000.0}


def test_parse_deltas_roth_alias_maps_to_ira():
    assert sc.parse_deltas(["roth_conversion=40000"]) == {"ira_distribution": 40000.0}


def test_parse_deltas_repeated_keys_sum():
    assert sc.parse_deltas(["wages=10000", "wages=5000"]) == {"wages": 15000.0}


def test_parse_deltas_alias_and_explicit_sum():
    assert sc.parse_deltas(["roth_conversion=40000", "ira_distribution=10000"]) == {
        "ira_distribution": 50000.0
    }


def test_parse_deltas_currency_string_amount():
    assert sc.parse_deltas(["wages=$1,500"]) == {"wages": 1500.0}


def test_parse_deltas_unknown_key_raises():
    with pytest.raises(sc.ScenarioError):
        sc.parse_deltas(["mortgage=1000"])


@pytest.mark.parametrize("key", ["federal_withholding", "estimated_payments"])
def test_parse_deltas_rejects_payment_keys(key):
    # v1 models tax, not balance due — payment deltas are not valid keys.
    with pytest.raises(sc.ScenarioError):
        sc.parse_deltas([f"{key}=5000"])


def test_parse_deltas_non_numeric_raises():
    with pytest.raises(sc.ScenarioError):
        sc.parse_deltas(["wages=lots"])


def test_parse_deltas_missing_equals_raises():
    with pytest.raises(sc.ScenarioError):
        sc.parse_deltas(["wages"])


def test_parse_deltas_empty_raises():
    with pytest.raises(sc.ScenarioError):
        sc.parse_deltas([])


# --------------------------------------------------------------------------
# extract_baseline_inputs + read_tax_year
# --------------------------------------------------------------------------

def test_extract_baseline_inputs_line_mapping():
    result = json.loads((FIX / "result.json").read_text())
    inp = sc.extract_baseline_inputs(result)
    assert inp["wages"] == 150000.0
    assert inp["taxable_interest"] == 2000.0
    assert inp["qualified_dividends"] == 5000.0
    # Net LTCG comes from Schedule D, not line 7, when Schedule D is present.
    assert inp["capital_gains"] == 10000.0
    # Payments are not part of the income vector (v1 models tax, not balance due).
    assert "federal_withholding" not in inp
    assert "estimated_payments" not in inp


def test_extract_baseline_inputs_capital_gains_fallback_to_line7():
    result = {"form_1040": {"line_7_capital_gain": 7777}}
    assert sc.extract_baseline_inputs(result)["capital_gains"] == 7777.0


def test_extract_baseline_inputs_short_term_folds_into_ordinary():
    # Codex P1: LTCG is preferential (capital_gains); STCG is ordinary (other_income).
    result = {
        "form_1040": {"line_8_schedule1_other": 1000},
        "schedule_d": {
            "part_ii_long_term": {"line_15_net_long_term": 8000},
            "part_i_short_term": {"line_7_net_short_term": 3000},
        },
    }
    inp = sc.extract_baseline_inputs(result)
    assert inp["capital_gains"] == 8000.0       # long-term only → preferential rate
    assert inp["other_income"] == 4000.0        # 1000 Schedule 1 + 3000 short-term (ordinary)


def test_extract_baseline_inputs_ira_plus_pension():
    result = {"form_1040": {"line_4b_taxable_ira": 1000, "line_5b_taxable_pensions": 500}}
    assert sc.extract_baseline_inputs(result)["ira_distribution"] == 1500.0


def test_extract_baseline_inputs_taxable_ss_folds_into_ordinary():
    # Codex P1: taxable Social Security (line 6b) is ordinary income and must be in
    # the baseline so deltas stack at the right bracket for retirees.
    result = {"form_1040": {"line_6b_taxable_ss": 12000, "line_8_schedule1_other": 1000}}
    assert sc.extract_baseline_inputs(result)["other_income"] == 13000.0


def test_read_tax_year_wrong_year_raises():
    with pytest.raises(sc.ScenarioError):
        sc.read_tax_year({"meta": {"tax_year": 2025}, "form_1040": {"line_24_total_tax": 1}}, 2026)


def test_read_tax_year_missing_line24_raises():
    with pytest.raises(sc.ScenarioError):
        sc.read_tax_year({"meta": {"tax_year": 2026}, "form_1040": {}}, 2026)


def test_read_tax_year_zero_line24_ok():
    sc.read_tax_year({"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": 0}}, 2026)


# --------------------------------------------------------------------------
# build_report — anchoring, marginal rate, stacking
# --------------------------------------------------------------------------

def _build(deltas, base_scenario=None):
    result = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())
    return sc.build_report(result, profile, tax_law, 2026, "t",
                           sc.parse_deltas(deltas), base_scenario,
                           datetime.date(2026, 5, 26))


def test_build_report_anchors_to_line24():
    r = _build(["roth_conversion=40000"])
    assert r.anchor["line_24"] == 25000.0
    # +$40K ordinary at the 20% bracket → +$8,000 over the real line 24.
    assert r.diff.delta_total_tax == 8000.0
    assert r.diff.anchored_scenario_total_tax == 33000.0


def test_build_report_marginal_rate():
    r = _build(["roth_conversion=40000"])
    assert r.diff.marginal_rate == 0.20


def test_build_report_marginal_rate_none_when_no_income_change():
    # A zero-amount delta changes nothing → delta_income is 0, marginal rate undefined.
    r = _build(["wages=0"])
    assert r.diff.delta_income == 0.0
    assert r.diff.marginal_rate is None


def test_build_report_effective_rates_present():
    r = _build(["wages=10000"])
    assert r.diff.baseline_effective_rate is not None
    assert r.diff.scenario_effective_rate is not None


def test_build_report_negative_delta_marginal_rate_sign():
    # A wage cut yields negative Δincome and Δtax but a positive marginal rate
    # (sign cancellation), and an anchored tax below the original line 24.
    r = _build(["wages=-30000"])
    assert r.diff.delta_income == -30000.0
    assert r.diff.delta_total_tax < 0
    assert r.diff.marginal_rate is not None and r.diff.marginal_rate > 0
    assert r.diff.anchored_scenario_total_tax < r.anchor["line_24"]


def test_build_report_job_loss_agi_zero_renders():
    # A large wage loss drives scenario AGI to 0: effective rate is None (undefined),
    # and the report still renders without ZeroDivisionError. Also exercises the
    # Form 8959 negative-wage floor via the fixture's additional_medicare threshold.
    r = _build(["wages=-200000"])
    assert r.scenario_outcome.agi == 0.0
    assert r.diff.scenario_effective_rate is None
    md = sc.render_markdown(r)
    assert "Bottom line" in md
    # Codex round-4 P2: the Schedule 1-A second-order caveat is user-facing.
    assert "Schedule 1-A" in md and "phase OUT" in md


def test_the_salt_phasedown_caveat_is_owed_only_while_the_phasedown_runs():
    """The keystone holds deductions at the baseline, and since AA16 the
    return's SALT cap moves with modified AGI, so for an itemizer above the
    threshold a delta can cost or save more than shown. A report that does not
    say so under-states in silence, which AGENTS.md §4 does not allow. Outside
    the phasedown's years there is nothing to say, and saying it anyway is a
    caveat about a rule not in force."""
    assert len(sc.salt_phasedown_caveat(True, "x.")) == 1
    assert sc.salt_phasedown_caveat(False, "x.") == []
    r = _build(["wages=10000"])
    assert "SALT cap is held at the baseline" in sc.render_markdown(
        dataclasses.replace(r, salt_cap_may_move=True))
    assert "SALT cap is held at the baseline" not in sc.render_markdown(
        dataclasses.replace(r, salt_cap_may_move=False))


def _itemizer(salt_before_cap=30000.0, modified_agi=600000.0, agi=600000.0,
              status="MFJ", use_itemized=True, line_13c=0):
    return {"meta": {"filing_status": status},
            "form_1040": {"line_11_agi": agi,
                          "line_13c_charitable_non_itemizer": line_13c},
            "schedule_a": {"use_itemized": use_itemized,
                           "salt_before_cap": salt_before_cap,
                           "modified_agi": modified_agi}}


def test_the_salt_caveat_is_owed_only_where_the_deduction_can_move():
    """Gated on the year, then on the threshold, the caveat fired where no
    MAGI could change the deduction. Measured from the immediate base, it
    dropped out of a stacked chain that had already moved MAGI far from the
    return; measured in keystone LEVELS, it was off by every line-10
    adjustment the keystone does not model. It now compares the deduction at
    each priced AGI, carried to the return's frame, with the return's own,
    and says it wherever that is not known. The fourth argument is the
    keystone's AGI for the return itself."""
    may = sc.salt_cap_may_move
    law = {}                                  # the statute's amount
    assert not may(_itemizer(), law, 2030, "MFJ", 600000, [610000])
    # $30,000 of tax under a cap of $35,900 at $520,000 and $23,900 at $560,000.
    assert may(_itemizer(modified_agi=520000, agi=520000), law, 2026, "MFJ",
               520000, [560000])
    # A cap at the $10,000 floor throughout moves nothing.
    assert not may(_itemizer(modified_agi=1_000_000, agi=1_000_000), law, 2026,
                   "MFJ", 1_000_000, [1_100_000])
    # Nor does one above a $20,000 tax throughout ($35,900 to $29,900).
    assert not may(_itemizer(salt_before_cap=20000, modified_agi=520000,
                             agi=520000), law, 2026, "MFJ", 520000, [540000])
    # Nor a delta that leaves AGI where it was.
    assert not may(_itemizer(), law, 2026, "MFJ", 600000, [600000])
    # The cache's amount, as the return used it: cached at $10,000 the cap
    # cannot move, where the statute's phases from $11,900 to the floor.
    cached = {"federal": {"salt_cap": {"amount": 10000}}}
    assert not may(_itemizer(), cached, 2026, "MFJ", 600000, [650000])
    assert may(_itemizer(), law, 2026, "MFJ", 600000, [650000])
    # A separate return's threshold is halved: $300,000 is past $252,500 on
    # one and nowhere near $505,000 on a joint return.
    assert may(_itemizer(modified_agi=300000, agi=300000, status="MFS"), law,
               2026, "MFS", 300000, [330000])
    assert not may(_itemizer(modified_agi=300000, agi=300000), law, 2026,
                   "MFJ", 300000, [330000])
    # The return's add-back rides along: $80,000 excluded puts $480,000 of
    # AGI at $560,000 of MAGI, past the threshold, where $10,000 more moves
    # the cap; on AGI alone both would sit under it.
    assert may(_itemizer(modified_agi=560000, agi=480000), law, 2026, "MFJ",
               480000, [490000])
    # The keystone's levels sit $20,000 off line 11 here (line-10
    # adjustments it does not model); a delta of zero is still zero.
    assert not may(_itemizer(modified_agi=520000, agi=520000), law, 2026,
                   "MFJ", 540000, [540000])
    # The standard deduction, by the section or by a positive line 13c,
    # matters only where the deduction would RISE; an itemizer's either way.
    for standard in (_itemizer(use_itemized=False), _itemizer(line_13c=1000)):
        assert not may(standard, law, 2026, "MFJ", 600000, [610000])
        assert may(standard, law, 2026, "MFJ", 600000, [560000])
    assert may(_itemizer(), law, 2026, "MFJ", 600000, [610000])
    # No Schedule A: only a lower AGI, measured from the RETURN — a stacked
    # base $200,000 under it is lower whichever way the last delta goes.
    none = {"form_1040": {"line_11_agi": 700000}}
    assert not may(none, law, 2026, "MFJ", 700000, [700000, 710000])
    assert may(none, law, 2026, "MFJ", 700000, [700000, 690000])
    assert may(none, law, 2026, "MFJ", 700000, [500000, 501000])
    # The same with a section: a close-call standard return at $700,000.
    close = _itemizer(modified_agi=700000, agi=700000, use_itemized=False)
    assert may(close, law, 2026, "MFJ", 700000, [500000, 501000])
    # Not known, or corrupt: say it wherever AGI moves.
    assert may(_itemizer(modified_agi=None), law, 2026, "MFJ", 600000, [610000])
    assert not may(_itemizer(modified_agi=None), law, 2026, "MFJ", 600000, [600000])
    assert may(_itemizer(salt_before_cap=-5), law, 2026, "MFJ", 600000, [610000])


def test_the_scenario_report_carries_the_gate():
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())
    def build(result):
        return sc.build_report(result, profile, tax_law, 2026, "t",
                               sc.parse_deltas(["wages=10000"]), None,
                               datetime.date(2026, 5, 26))
    # The fixture has no Schedule A and the delta raises AGI.
    assert build(base).salt_cap_may_move is False
    high = dict(base, schedule_a=_itemizer()["schedule_a"])
    assert build(high).salt_cap_may_move is True
    assert "SALT cap is held at the baseline" in sc.render_markdown(build(high))
    # The saved scenario keeps it: only the JSON is written to disk.
    assert sc._report_to_dict(build(high))["salt_cap_may_move"] is True


def test_three_deep_stacking_carries_anchor_and_accumulates():
    s1 = sc._report_to_dict(_build(["wages=10000"]))
    s2 = sc._report_to_dict(_build(["wages=10000"], base_scenario=s1))
    third = _build(["wages=10000"], base_scenario=s2)
    assert third.anchor == _build(["wages=10000"]).anchor  # anchor constant across stack
    assert [d["key"] for d in third.applied_deltas] == ["wages", "wages", "wages"]
    assert third.inputs["wages"] == 180000.0  # 150k baseline + 3 x 10k
    d = third.diff
    assert round(d.baseline_anchored_total_tax + d.delta_total_tax, 2) == d.anchored_scenario_total_tax


def test_build_report_resolved_inputs_fold_in_delta():
    r = _build(["wages=10000"])
    assert r.inputs["wages"] == 160000.0


def test_stacking_layers_delta_and_carries_anchor():
    first = _build(["roth_conversion=40000"])
    base_scenario = sc._report_to_dict(first)
    second = _build(["capital_gains=20000"], base_scenario=base_scenario)
    # Anchor is carried unchanged from the original result.json.
    assert second.anchor == first.anchor
    # Cumulative deltas accumulate.
    keys = [d["key"] for d in second.applied_deltas]
    assert keys == ["ira_distribution", "capital_gains"]
    # The stacked cap-gains delta is taxed at the 15% preferential rate.
    assert second.diff.delta_total_tax == 3000.0
    assert second.diff.anchored_scenario_total_tax == 36000.0


def test_stacking_rejects_non_scenario_base():
    with pytest.raises(sc.ScenarioError):
        _build(["wages=1"], base_scenario={"schema": "not-a-scenario"})


def test_stacking_rejects_mismatched_tax_year():
    # Codex P1: stacking a prior-year scenario would mix its baseline with current
    # tax law. _build runs at year 2026; a TY2025 base must be refused.
    base = sc._report_to_dict(_build(["wages=10000"]))
    base["tax_year"] = 2025
    with pytest.raises(sc.ScenarioError):
        _build(["wages=1"], base_scenario=base)


@pytest.mark.parametrize("bad", ["oops", None, True])
def test_stacking_rejects_non_numeric_anchor(bad):
    # Codex P2: a hand-edited anchor must fail with a controlled error, not a TypeError.
    base = sc._report_to_dict(_build(["wages=10000"]))
    base["anchor"]["line_24"] = bad
    with pytest.raises(sc.ScenarioError):
        _build(["wages=1"], base_scenario=base)


def test_stacking_rejects_mismatched_filing_status():
    # Codex P2: a base built under a different filing status mixes incompatible
    # brackets with the current recompute. _build runs MFJ (the fixture).
    base = sc._report_to_dict(_build(["wages=10000"]))
    base["filing_status"] = "single"
    with pytest.raises(sc.ScenarioError):
        _build(["wages=1"], base_scenario=base)


def test_resolve_base_allows_mfj_qss_equivalence():
    # MFJ and QSS share the same brackets, so stacking across them is compatible.
    base = {"schema": "tax-scenario/v1", "tax_year": 2026, "filing_status": "MFJ",
            "inputs": {}, "anchor": {"line_24": 1.0, "baseline_recompute_total": 1.0}}
    sc.resolve_base({}, base, 2026, "QSS")  # no raise
    with pytest.raises(sc.ScenarioError):
        sc.resolve_base({}, base, 2026, "single")


def test_stacking_rejects_non_numeric_base_input():
    # Codex P2: a corrupted income value in a stacked base must error (caught by the
    # CLI), not silently compute a wrong tax by coercing to 0.
    base = sc._report_to_dict(_build(["wages=10000"]))
    base["inputs"]["wages"] = "N/A"
    with pytest.raises(ValueError):
        _build(["wages=1"], base_scenario=base)


def test_unstacked_baseline_anchored_equals_line24():
    r = _build(["wages=10000"])
    assert r.diff.baseline_anchored_total_tax == r.anchor["line_24"]


def test_stacked_table_is_internally_consistent():
    # Codex P1: for a stacked run, Baseline + Δ must equal Scenario in the rendered
    # table. The baseline column now uses the immediate base's anchored tax.
    first = _build(["roth_conversion=40000"])
    second = _build(["capital_gains=20000"], base_scenario=sc._report_to_dict(first))
    d = second.diff
    assert round(d.baseline_anchored_total_tax + d.delta_total_tax, 2) == d.anchored_scenario_total_tax
    # The immediate base's anchored tax is the first scenario's anchored tax (33000), not
    # the original line 24 (25000).
    assert d.baseline_anchored_total_tax == first.diff.anchored_scenario_total_tax == 33000.0


# --------------------------------------------------------------------------
# qualified-dividend coupling (Codex P1)
# --------------------------------------------------------------------------

def test_qualified_dividends_delta_is_split_only():
    # qualified_dividends is the qualified SPLIT of ordinary dividends; alone it does
    # not change gross income (the keystone clamps qualified <= ordinary). Adding
    # income requires ordinary_dividends. With the fixture baseline (ordinary 5000),
    # a +10000 qualified delta is clamped and leaves AGI/tax unchanged.
    r = _build(["qualified_dividends=10000"])
    assert r.diff.delta_agi == 0.0
    assert r.diff.delta_total_tax == 0.0


def test_dividend_income_added_via_ordinary_with_qualified_portion():
    # To add all-qualified dividend income, raise ordinary AND qualified (lines 3b/3a).
    r = _build(["ordinary_dividends=10000", "qualified_dividends=10000"])
    assert r.diff.delta_agi == 10000.0  # gross rises by the ordinary amount only
    assert r.diff.delta_total_tax >= 0


def test_stacked_dividend_delta_is_reversible():
    # Codex round 12: with no derived coupling, stacking a delta and its exact negation
    # returns to the baseline. Add all-qualified dividend income, then fully reverse it.
    base_ord = sc.extract_baseline_inputs(json.loads((FIX / "result.json").read_text()))["ordinary_dividends"]
    s1 = _build(["ordinary_dividends=10000", "qualified_dividends=10000"])
    s2 = _build(["ordinary_dividends=-10000", "qualified_dividends=-10000"],
                base_scenario=sc._report_to_dict(s1))
    assert s2.inputs["ordinary_dividends"] == base_ord  # ordinary returns to baseline
    assert s2.diff.anchored_scenario_total_tax == s2.anchor["line_24"]  # back to filed tax


def test_compute_scenario_clamps_lone_qualified_delta():
    # Keystone defense: qualified can't exceed ordinary. A lone qualified bump (no
    # matching ordinary) must not lower tax below baseline.
    base = {"wages": 150000, "ordinary_dividends": 5000, "qualified_dividends": 5000}
    base_tax = compute_scenario(base, {}, **RATE_KW)["total_federal_tax"]
    bumped = compute_scenario(base, {"qualified_dividends": 10000}, **RATE_KW)["total_federal_tax"]
    assert bumped == base_tax


# --------------------------------------------------------------------------
# save_scenario — write guard
# --------------------------------------------------------------------------

def test_save_scenario_writes_under_scenarios_chmod600(tmp_path):
    out = sc.save_scenario(tmp_path, "ok", {"schema": "tax-scenario/v1"})
    assert out == (tmp_path / "scenarios" / "ok.json").resolve()
    assert out.exists()
    assert (out.stat().st_mode & 0o777) == 0o600


def test_save_scenario_restricts_the_scenarios_directory(tmp_path):
    """600 on the file leaves `scenarios/` itself readable, and a scenario NAME is
    disclosure on its own — `divorce-2027.json` in a shared-host listing. Pinned at
    umask 022, without which mkdir's 0777 request lands on 0700 unaided and the
    assertion proves nothing."""
    old = os.umask(0o022)
    try:
        sc.save_scenario(tmp_path, "ok", {"schema": "tax-scenario/v1"})
    finally:
        os.umask(old)
    mode = (tmp_path / "scenarios").stat().st_mode & 0o777
    assert mode == 0o700, f"scenarios/ is {oct(mode)}, listing scenario names"


def test_save_scenario_fails_closed_when_directory_chmod_fails(tmp_path, monkeypatch):
    """A scenarios/ that can't be tightened to 700 must abort the write, not
    drop a scenario file into a listable directory — a scenario NAME alone
    (divorce-2027.json) is a disclosure, and financial data is in the file.

    save_scenario() secures paths via secure_path() (agents/lib/secure_fs.py),
    which calls os.chmod() directly rather than the Path.chmod() method, so
    the failure is injected there — os is a singleton module, so patching it
    through any importer's `os` name (here scenario.py's) reaches the same
    call secure_fs.py makes."""
    real_chmod = os.chmod

    def refuse(path, *args, **kwargs):
        if Path(path).name == "scenarios":
            raise OSError("simulated chmod failure")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr("agents.lib.secure_fs.os.chmod", refuse)
    with pytest.raises(sc.ScenarioError, match="scenarios"):
        sc.save_scenario(tmp_path, "ok", {"schema": "tax-scenario/v1"})
    assert not (tmp_path / "scenarios" / "ok.json").exists()


def test_save_scenario_fails_closed_when_file_chmod_fails(tmp_path, monkeypatch):
    """Same guarantee on the scenario file itself: a chmod 600 that can't be
    applied (an ACL/NFS filesystem, say) must not leave financial data behind
    at the ambient mode — the file is created at 600 directly, but a chmod
    failure removes it rather than trusting whatever mode creation landed at."""
    real_chmod = os.chmod

    def refuse(path, *args, **kwargs):
        if "ok.json" in Path(path).name:   # the temp beside it, too
            raise OSError("simulated chmod failure")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr("agents.lib.secure_fs.os.chmod", refuse)
    with pytest.raises(sc.ScenarioError, match="ok.json"):
        sc.save_scenario(tmp_path, "ok", {"schema": "tax-scenario/v1"})
    assert not (tmp_path / "scenarios" / "ok.json").exists(), \
        "chmod failure must not leave an unsecured scenario file behind"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", ".", ".hidden", ""])
def test_save_scenario_rejects_unsafe_names(tmp_path, bad):
    with pytest.raises(sc.ScenarioError):
        sc.save_scenario(tmp_path, bad, {})


def test_validate_name_rejects_backslash():
    with pytest.raises(sc.ScenarioError):
        sc._validate_name("a\\b")


# --------------------------------------------------------------------------
# CLI (subprocess) — end-to-end + security
# --------------------------------------------------------------------------

def _run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "agents.lib.scenario", *args],
        cwd=str(REPO), capture_output=True, text=True,
    )


def test_cli_end_to_end_json(tmp_path):
    ws = _load_fixture_workspace(tmp_path)
    res = _run_cli(["--result", str(ws / "result.json"), "--workspace", str(ws),
                    "--year", "2026", "--name", "roth", "--today", "2026-05-26",
                    "--json", "roth_conversion=40000"], cwd=ws)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["diff"]["anchored_scenario_total_tax"] == 33000.0
    assert (ws / "scenarios" / "roth.json").exists()


def test_cli_resolves_relative_paths_under_workspace(tmp_path):
    # Codex P2: _run_cli launches from REPO, not the workspace. A relative --result
    # must resolve under --workspace, not the process CWD.
    ws = _load_fixture_workspace(tmp_path)
    res = _run_cli(["--result", "result.json", "--workspace", str(ws),
                    "--year", "2026", "--no-save", "--today", "2026-05-26", "wages=1000"], cwd=ws)
    assert res.returncode == 0, res.stderr


def test_cli_no_save_skips_write(tmp_path):
    ws = _load_fixture_workspace(tmp_path)
    res = _run_cli(["--result", str(ws / "result.json"), "--workspace", str(ws),
                    "--year", "2026", "--name", "x", "--no-save", "--today", "2026-05-26",
                    "wages=1000"], cwd=ws)
    assert res.returncode == 0, res.stderr
    assert not (ws / "scenarios").exists()


def test_cli_rejects_unknown_delta(tmp_path):
    ws = _load_fixture_workspace(tmp_path)
    res = _run_cli(["--result", str(ws / "result.json"), "--workspace", str(ws),
                    "--year", "2026", "mortgage=1000"], cwd=ws)
    assert res.returncode == 1
    assert "Unknown delta key" in res.stderr


def test_cli_rejects_base_path_traversal(tmp_path):
    ws = _load_fixture_workspace(tmp_path)
    res = _run_cli(["--result", str(ws / "result.json"), "--workspace", str(ws),
                    "--year", "2026", "--base", "/etc/passwd", "wages=1"], cwd=ws)
    assert res.returncode == 1
    assert "outside workspace" in res.stderr or "ERROR" in res.stderr


def test_cli_rejects_bad_year(tmp_path):
    ws = _load_fixture_workspace(tmp_path)
    res = _run_cli(["--result", str(ws / "result.json"), "--workspace", str(ws),
                    "--year", "2026; rm -rf /", "wages=1"], cwd=ws)
    assert res.returncode == 1


def test_cli_wrong_year_refuses(tmp_path):
    ws = _load_fixture_workspace(tmp_path)
    res = _run_cli(["--result", str(ws / "result.json"), "--workspace", str(ws),
                    "--year", "2025", "wages=1"], cwd=ws)
    assert res.returncode == 1
    assert "TY2026" in res.stderr


def test_cli_malformed_json_yields_error_not_traceback(tmp_path):
    # Codex P2: _read_json/_num raise ProjectionError; the CLI must surface a clean
    # "ERROR: ..." line, not an uncaught traceback.
    ws = _load_fixture_workspace(tmp_path)
    (ws / "result.json").write_text("{ not valid json")
    res = _run_cli(["--result", str(ws / "result.json"), "--workspace", str(ws),
                    "--year", "2026", "wages=1"], cwd=ws)
    assert res.returncode == 1
    assert res.stderr.startswith("ERROR:")
    assert "Traceback" not in res.stderr


def test_a_bad_base_scenario_status_names_the_base_file_not_result_json():
    """The strict helper's remedy is right for every caller but this one.

    `_canonical_status` names result.json's `meta.filing_status` and
    taxpayer_profile.json, which is where four of the five callers read the
    status from. `resolve_base` reads it out of the stored
    `scenarios/<name>.json` passed as `--base`, so the shared message sent the
    reader to two files that were both fine. Before the guard moved into the
    helper this path silently returned "single" and compared on it, so the
    refusal is the improvement — misdirecting the remedy is not.
    """
    current = {"meta": {"filing_status": "MFJ"}}
    for bad in ({}, {"filing_status": None}, {"filing_status": ""},
                {"filing_status": "Married Filing Jointly"}):
        base = {"schema": "tax-scenario/v1", "tax_year": 2026, "inputs": {},
                "anchor": {}, "name": "b", **bad}
        with pytest.raises(sc.ScenarioError) as exc:
            sc.resolve_base(current, base, 2026, "MFJ")
        msg = str(exc.value)
        assert "--base scenario" in msg
        assert "not at fault" in msg, (
            "the message must clear result.json / taxpayer_profile.json, "
            "which is where a reader would otherwise start looking")
        # Never the raw value: a hand-edited scenario can carry misplaced data.
        assert "Married Filing Jointly" not in msg

    # A genuine mismatch still reports as a mismatch, not as a bad file.
    base = {"schema": "tax-scenario/v1", "tax_year": 2026, "inputs": {},
            "anchor": {}, "name": "b", "filing_status": "MFS"}
    with pytest.raises(sc.ScenarioError) as exc:
        sc.resolve_base(current, base, 2026, "MFJ")
    assert "Cannot stack across filing statuses" in str(exc.value)


@pytest.mark.parametrize("name", ["tax-scenario", "tax-roth-convert", "tax-project",
                                  "tax-ptet", "tax-withholding", "tax-harvest"])
def test_every_keystone_skill_states_the_cg_limit_refusal(name):
    """X2: each skill that prices a return through the keystone or the
    projection worksheet now refuses instead of falling through to ordinary
    rates; the prerequisite that says so has to be in every one of them, or
    an agent hits the refusal with no instruction pointing at /tax-setup."""
    from tests.skill_text import skill_text
    text = skill_text(name)
    assert "capital_gains_rates" in text and "/tax-setup" in text, name
    assert "REFUSES" in text, (
        f"{name} does not tell the agent the engine refuses to price "
        "preferential income without the 0%/15% breakpoints")


def test_return_deduction_is_the_returns_line_12():
    """AA20: the keystone subtracted the cache's standard deduction for every
    filer. It takes the deduction the return took, and refuses rather than
    fall back to the cache, since that fallback is the over-statement. Zero is
    the template's placeholder, refused unless the return was forced to
    itemize with nothing to itemize (§63(c)(6)(A))."""
    line = "line_12_standard_deduction"
    assert sc.return_deduction({"form_1040": {line: 48250.5}}) == 48250.5
    for form in ({}, {line: None}):
        with pytest.raises(sc.ScenarioError, match=f"has no form_1040.{line}"):
            sc.return_deduction({"form_1040": form})
    with pytest.raises(sc.ScenarioError, match=f"has no form_1040.{line}"):
        sc.return_deduction({})
    for bad in ("30000", True, float("nan"), float("inf"), -1):
        with pytest.raises(sc.ScenarioError, match="finite, non-negative"):
            sc.return_deduction({"form_1040": {line: bad}})
    forced = {"use_itemized": True, "itemized_total": 0}
    assert sc.return_deduction({"form_1040": {line: 0},
                                "schedule_a": forced}) == 0.0
    for sched in (None, {"use_itemized": True, "itemized_total": 1200},
                  {"use_itemized": False, "itemized_total": 0}):
        result = {"form_1040": {line: 0}}
        if sched is not None:
            result["schedule_a"] = sched
        with pytest.raises(sc.ScenarioError, match="template's placeholder"):
            sc.return_deduction(result)


def test_an_itemizer_is_priced_from_its_own_deduction():
    """$90,000 of other income on the fixture, whose $15,000 of qualified
    dividends and long-term gain stacks above ordinary income:
    - line 12 $30,000: ordinary taxable income $122,000 to $212,000, so
      $78,000 at 20% and $12,000 at 30% = $19,200;
    - line 12 $50,000, an itemizer: $102,000 to $192,000, all at 20% = $18,000.
    Priced on the cache's standard deduction, the itemizer paid $1,200 of a
    bracket it never reaches."""
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())

    def delta(line_12):
        result = dict(base, form_1040=dict(base["form_1040"],
                                           line_12_standard_deduction=line_12))
        return sc.build_report(result, profile, tax_law, 2026, "t",
                               sc.parse_deltas(["other_income=90000"]), None,
                               datetime.date(2026, 5, 26)).diff.delta_total_tax
    assert delta(30000) == 19200.0
    assert delta(50000) == 18000.0


def test_the_medical_caveat_is_owed_only_where_the_floor_can_move():
    """The keystone holds line 12, and the medical floor is a share of AGI in
    every year, so a delta moves an itemizer's deduction without a word.
    Priced on the gross the return records ($20,000 against a $15,000 floor
    at $200,000 of AGI is $5,000; $4,250 at $210,000), in the return's frame,
    and said wherever it is not known. The second argument is the keystone's
    AGI for the return itself."""
    may = sc.medical_floor_may_move

    def ret(expenses=20000.0, agi=200000.0, use_itemized=True, line_13c=0,
            section=True):
        r = {"form_1040": {"line_11_agi": agi,
                           "line_13c_charitable_non_itemizer": line_13c}}
        if section:
            r["schedule_a"] = {"use_itemized": use_itemized,
                               "medical_expenses": expenses}
        return r
    assert may(ret(), 200000, [210000])
    assert may(ret(), 200000, [190000])
    assert not may(ret(), 200000, [200000])
    # Under the floor throughout: $10,000 against $15,000, and $14,250 at
    # $190,000. At $120,000 the floor is $9,000 and $1,000 clears it.
    assert not may(ret(expenses=10000), 200000, [210000])
    assert not may(ret(expenses=10000), 200000, [190000])
    assert may(ret(expenses=10000), 200000, [120000])
    # The standard deduction, by the section or by a positive line 13c:
    # only where the deduction would RISE.
    for standard in (ret(use_itemized=False), ret(line_13c=1000)):
        assert not may(standard, 200000, [210000])
        assert may(standard, 200000, [190000])
    # No section: only a lower AGI, measured from the return.
    none = ret(section=False)
    assert not may(none, 200000, [210000])
    assert may(none, 200000, [190000])
    assert may(none, 200000, [150000, 151000])
    # From the return, not a stacked base $50,000 under it.
    assert may(ret(), 200000, [150000, 150000])
    # $20,000 of line-10 adjustments put the keystone at $167,000 against a
    # line 11 of $147,000. A standard filer with $14,000 of expenses whose AGI
    # falls $5,000 goes from $2,975 deductible to $3,350; in keystone levels
    # the check saw neither. And an itemizer's zero delta stays zero.
    assert may(ret(expenses=14000, agi=147000, use_itemized=False),
               167000, [162000])
    assert not may(ret(agi=147000), 167000, [167000])
    # Recorded before the gross was, or corrupt: any move that could matter.
    assert may(ret(expenses=None), 200000, [210000])
    assert not may(ret(expenses=None), 200000, [200000])
    assert not may(ret(expenses=None, use_itemized=False), 200000, [210000])
    assert may(ret(expenses=None, use_itemized=False), 200000, [190000])
    assert may(ret(expenses=-5), 200000, [120000])
    assert len(sc.medical_floor_caveat(True, "x.")) == 1
    assert sc.medical_floor_caveat(False, "x.") == []


def test_the_scenario_report_carries_the_medical_gate():
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())

    def build(result):
        return sc.build_report(result, profile, tax_law, 2026, "t",
                               sc.parse_deltas(["wages=10000"]), None,
                               datetime.date(2026, 5, 26))
    # No Schedule A, and the delta raises AGI.
    assert build(base).medical_floor_may_move is False
    # $20,000 over a $12,525 floor at $167,000; the delta raises the floor.
    r = build(dict(base, schedule_a={"use_itemized": True,
                                     "medical_expenses": 20000}))
    assert r.medical_floor_may_move is True
    assert "medical-expense deduction is held" in sc.render_markdown(r)
    assert "the return's own line 12" in sc.render_markdown(r)
    assert sc._report_to_dict(r)["medical_floor_may_move"] is True


def test_the_keystone_refuses_a_deduction_that_is_not_a_number():
    """`deduction` was used unchecked: NaN kept `max(0.0, agi - nan)` at 0.0,
    a silent $0 of tax, and a negative one raised taxable income."""
    for bad in (float("nan"), float("inf"), -1.0, True, "30000"):
        with pytest.raises(ValueError, match="deduction must be a finite"):
            compute_scenario({"wages": 100000}, {}, brackets=MFJ_BRACKETS,
                             deduction=bad)


def test_a_stacked_base_must_be_priced_on_the_same_deduction():
    """The anchor carries the deduction its base was priced on. Stacked after
    line 12 moved, a saved baseline read $15,800 where it had been $27,000:
    two deductions in one `line_24 + (base - anchor)`. A base saved before
    AA20 records none, and was priced on the cache's standard deduction."""
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())

    def build(result, stacked_on=None):
        return sc.build_report(result, profile, tax_law, 2026, "t",
                               sc.parse_deltas(["wages=1000"]), stacked_on,
                               datetime.date(2026, 5, 26))
    saved = sc._report_to_dict(build(base))
    assert saved["anchor"]["deduction"] == 30000.0
    build(base, saved)                                     # the same line 12 stacks
    moved = dict(base, form_1040=dict(base["form_1040"],
                                      line_12_standard_deduction=80000))
    with pytest.raises(sc.ScenarioError, match="priced on a \\$30,000.00 deduction"):
        build(moved, saved)
    legacy = json.loads(json.dumps(saved))
    del legacy["anchor"]["deduction"]
    with pytest.raises(sc.ScenarioError, match="saved before the planning tools"):
        build(base, legacy)
    legacy["anchor"]["deduction"] = "30000"
    with pytest.raises(sc.ScenarioError, match="anchor.deduction is not numeric"):
        build(base, legacy)


def test_a_capped_charitable_deduction_refuses_where_agi_moves():
    """§170(b) caps a gift at a share of AGI, and a carryforward above zero is
    the cap binding. Any priced AGI other than the return's moves the cap
    while the keystone holds the deduction, so the figure reads high either
    way — AGENTS.md §4's hard error, which no caveat may stand in for."""
    refuse = sc.refuse_where_the_charity_cap_moves
    capped = {"form_1040": {"line_11_agi": 200000},
              "schedule_a": {"charitable_carryforward": 5000}}
    with pytest.raises(sc.ScenarioError, match="capped at a share of AGI"):
        refuse(capped, 200000, [190000])
    with pytest.raises(sc.ScenarioError, match="capped at a share of AGI"):
        refuse(capped, 200000, [210000])
    refuse(capped, 200000, [200000])                       # AGI unmoved
    refuse(capped, 220000, [220000])                       # measured in deltas
    refuse({"schedule_a": {"charitable_carryforward": 0}}, 200000, [190000])
    refuse({"form_1040": {"line_11_agi": 200000}}, 200000, [190000])
    for bad in ("5000", True, float("nan"), -1):
        with pytest.raises(sc.ScenarioError, match="charitable_carryforward must be"):
            refuse({"schedule_a": {"charitable_carryforward": bad}}, 200000, [190000])
    # The scenario report refuses before it prices.
    result = json.loads((FIX / "result.json").read_text())
    result["schedule_a"] = {"use_itemized": True, "charitable_carryforward": 5000}
    with pytest.raises(sc.ScenarioError, match="capped at a share of AGI"):
        sc.build_report(result, json.loads((FIX / "taxpayer_profile.json").read_text()),
                        json.loads((FIX / "tax_law_2026.json").read_text()), 2026,
                        "t", sc.parse_deltas(["wages=1000"]), None,
                        datetime.date(2026, 5, 26))


def test_a_stacked_report_measures_its_checks_from_the_return():
    """A stacked base has already moved AGI away from the return, and the
    held-deduction checks are measured from the return's own keystone AGI,
    not the base's. On the fixture (no Schedule A), $1,000 more wages alone
    moves nothing a standard return holds; stacked on a base $50,000 lower,
    the chain sits $49,000 under the return, where a lower cap or medical
    floor can matter."""
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())

    def build(deltas, stacked_on=None):
        return sc.build_report(base, profile, tax_law, 2026, "t",
                               sc.parse_deltas(deltas), stacked_on,
                               datetime.date(2026, 5, 26))
    alone = build(["wages=1000"])
    assert (alone.salt_cap_may_move, alone.medical_floor_may_move) == (False, False)
    lowered = sc._report_to_dict(build(["other_income=-50000"]))
    stacked = build(["wages=1000"], lowered)
    assert (stacked.salt_cap_may_move, stacked.medical_floor_may_move) == (True, True)


def test_an_itemizer_with_recorded_inputs_is_repriced_not_held():
    """AA23: where Schedule A recorded its inputs, the scenario re-prices it at
    each AGI instead of holding line 12 and disclosing the drift.
    Hand-computed, TY2026 MFJ at the fixture's AGI of $167,000: medical
    $20,000 over a 7.5% floor of $12,525 = $7,475, plus mortgage interest
    $30,000 = $37,475 itemized against a $30,000 standard deduction.
    wages=10000 raises AGI to $177,000 and the floor to $13,275, so medical
    falls to $6,725 and line 12 to $36,725: taxable income rises by $10,750,
    where held it rose by $10,000."""
    from agents.lib.tax_computations import compute_schedule_a
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())
    sched = compute_schedule_a(167000, "MFJ", 2026, 30000, 40400,
                               medical_expenses=20000, mortgage_interest=30000,
                               top_bracket_income=0)
    assert (sched["use_itemized"], sched["itemized_total"]) == (True, 37475.0)
    result = dict(base, schedule_a=sched,
                  form_1040=dict(base["form_1040"],
                                 line_12_standard_deduction=37475.0))

    def build(r):
        return sc.build_report(r, profile, tax_law, 2026, "t",
                               sc.parse_deltas(["wages=10000"]), None,
                               datetime.date(2026, 5, 26))
    priced = build(result)
    assert priced.diff.delta_agi == 10000.0
    assert priced.diff.delta_taxable_income == 10750.0
    assert (priced.medical_floor_may_move, priced.salt_cap_may_move) == (
        False, False)
    assert priced.schedule_a_repriced is True
    assert sc._report_to_dict(priced)["schedule_a_repriced"] is True
    assert "re-priced at each AGI" in sc.render_markdown(priced)
    # At the return's own AGI the deduction is the return's line 12 exactly,
    # so the anchor still reproduces line 24.
    assert priced.base_outcome.taxable_income == build(dict(
        result, schedule_a={k: v for k, v in sched.items() if k != "inputs"}
    )).base_outcome.taxable_income

    # Without the inputs (a return written before them): held, disclosed.
    held = build(dict(result, schedule_a={k: v for k, v in sched.items()
                                          if k != "inputs"}))
    assert held.diff.delta_taxable_income == 10000.0
    assert held.medical_floor_may_move is True
    assert held.schedule_a_repriced is False
    assert "held at the baseline." in sc.render_markdown(held)


def test_schedule_a_is_held_wherever_re_pricing_could_over_state():
    """AA23's bias review: re-pricing stands down — the deduction held, the
    disclosures back — wherever it could over-state.

    - The election flips. Hand-computed, TY2026 MFJ: medical $20,000 less
      $12,525 = $7,475 plus mortgage $23,000 = $30,475, itemized by $475 over
      the $30,000 standard deduction; wages=10000 cuts medical to $6,725 and
      itemizing to $29,725, so the standard deduction would win. Line 12's
      offset from the re-run is an itemizer's, and carried across the flip it
      would land on a standard filer (an age-65 add-on, the other way).
    - An AGI above the top bracket's floor: the §70111 haircut's base is
      disputed, so it is priced only where it is zero under every reading.
    - A binding charitable cap: the re-run assumes a public charity and
      appreciated property, which the return does not record, so the refusal
      stands whether or not the rest re-prices."""
    from agents.lib.tax_computations import compute_schedule_a
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())

    def with_schedule_a(sched, line_12):
        return dict(base, schedule_a=sched,
                    form_1040=dict(base["form_1040"],
                                   line_12_standard_deduction=line_12))

    def build(result, deltas=("wages=10000",), law=tax_law):
        return sc.build_report(result, profile, law, 2026, "t",
                               sc.parse_deltas(list(deltas)), None,
                               datetime.date(2026, 5, 26))

    flip = compute_schedule_a(167000, "MFJ", 2026, 30000, 40400,
                              medical_expenses=20000, mortgage_interest=23000,
                              top_bracket_income=0)
    assert flip["itemized_total"] == 30475.0
    r = build(with_schedule_a(flip, 30475.0))
    assert (r.schedule_a_repriced, r.diff.delta_taxable_income) == (
        False, 10000.0)
    assert r.medical_floor_may_move is True

    stays = compute_schedule_a(167000, "MFJ", 2026, 30000, 40400,
                               medical_expenses=20000, mortgage_interest=30000,
                               top_bracket_income=0)
    result = with_schedule_a(stays, 37475.0)
    assert build(result).schedule_a_repriced is True
    # The fixture's MFJ top bracket starts at $200,000: a delta that carries
    # AGI past it holds.
    assert build(result, ("wages=40000",)).schedule_a_repriced is False

    capped = compute_schedule_a(167000, "MFJ", 2026, 30000, 40400,
                                charitable_cash=150000, top_bracket_income=0,
                                non_itemizer_charitable=0)
    assert capped["charitable_carryforward"] > 0
    with pytest.raises(sc.ScenarioError, match="capped at a share of AGI"):
        build(with_schedule_a(capped, capped["itemized_total"]))


def test_what_a_re_priced_schedule_a_still_holds_is_said():
    """AA23's bias review: the re-run holds state income tax at the return's
    figure (withholding moves with wages), and says so; the report must too,
    now that the SALT caveat stands down."""
    from agents.lib.tax_computations import compute_schedule_a
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())
    sched = compute_schedule_a(167000, "MFJ", 2026, 30000, 40400,
                               state_local_income_taxes=12000,
                               mortgage_interest=30000, modified_agi=167000,
                               top_bracket_income=0)
    result = dict(base, schedule_a=sched,
                  form_1040=dict(base["form_1040"],
                                 line_12_standard_deduction=sched[
                                     "itemized_total"]))
    r = sc.build_report(result, profile, tax_law, 2026, "t",
                        sc.parse_deltas(["wages=10000"]), None,
                        datetime.date(2026, 5, 26))
    assert r.schedule_a_repriced is True
    assert any("income taxes are held" in n for n in r.schedule_a_held)
    assert "income taxes are held" in sc.render_markdown(r)


def test_a_stack_prices_its_schedule_a_one_way_throughout():
    """AA23's review: a stack whose base re-priced Schedule A, extended by a
    change that holds it (here, one that carries AGI past the fixture's
    $200,000 top-bracket floor), mixed the two in one anchored total."""
    from agents.lib.tax_computations import compute_schedule_a
    base = json.loads((FIX / "result.json").read_text())
    profile = json.loads((FIX / "taxpayer_profile.json").read_text())
    tax_law = json.loads((FIX / "tax_law_2026.json").read_text())
    sched = compute_schedule_a(167000, "MFJ", 2026, 30000, 40400,
                               medical_expenses=20000, mortgage_interest=30000,
                               top_bracket_income=0)
    result = dict(base, schedule_a=sched,
                  form_1040=dict(base["form_1040"],
                                 line_12_standard_deduction=37475.0))

    def build(deltas, base_scenario=None):
        return sc.build_report(result, profile, tax_law, 2026, "t",
                               sc.parse_deltas(deltas), base_scenario,
                               datetime.date(2026, 5, 26))
    first = build(["wages=10000"])
    assert first.anchor["schedule_a_repriced"] is True
    saved = sc._report_to_dict(first)
    assert build(["wages=5000"], saved).schedule_a_repriced is True
    with pytest.raises(sc.ScenarioError, match="one way throughout"):
        build(["wages=40000"], saved)
