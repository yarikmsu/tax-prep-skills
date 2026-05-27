"""Tests for the Roth conversion ladder — agents/lib/roth_ladder.py (backlog #6)."""
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import roth_ladder as rl

FIX = Path(__file__).resolve().parent / "fixtures" / "roth"
REPO = Path(__file__).resolve().parent.parent
TODAY = datetime.date(2026, 5, 26)


def _load(name):
    return json.loads((FIX / name).read_text())


def _build(max_amount=100000, step=10000, name="roth-ladder", tax_law=None):
    return rl.build_ladder(
        _load("result.json"), _load("taxpayer_profile.json"),
        tax_law if tax_law is not None else _load("tax_law_2026.json"),
        2026, max_amount=max_amount, step=step, name=name, today=TODAY)


def _ws(tmp_path):
    for f in ("result.json", "taxpayer_profile.json", "tax_law_2026.json"):
        shutil.copy(FIX / f, tmp_path / f)
    return tmp_path


# --------------------------------------------------------------------------
# parse_sweep
# --------------------------------------------------------------------------

def test_parse_sweep_basic_multiples():
    assert rl.parse_sweep(50000, 10000) == [0, 10000, 20000, 30000, 40000, 50000]


def test_parse_sweep_partial_final_rung():
    # max not a step multiple → append exact max as a final rung
    assert rl.parse_sweep(25000, 10000) == [0, 10000, 20000, 25000]


def test_parse_sweep_max_zero():
    assert rl.parse_sweep(0, 5000) == [0.0]


def test_parse_sweep_step_exceeds_max_raises():
    with pytest.raises(rl.RothLadderError, match="cannot exceed"):
        rl.parse_sweep(5000, 10000)


def test_parse_sweep_nonpositive_step_raises():
    with pytest.raises(rl.RothLadderError, match="positive"):
        rl.parse_sweep(50000, 0)


def test_parse_sweep_negative_max_raises():
    with pytest.raises(rl.RothLadderError, match="non-negative"):
        rl.parse_sweep(-1, 1000)


def test_parse_sweep_too_many_rungs_raises():
    with pytest.raises(rl.RothLadderError, match="limit"):
        rl.parse_sweep(100000, 1)  # 100001 rungs


def test_parse_sweep_bool_raises():
    with pytest.raises(rl.RothLadderError, match="numeric"):
        rl.parse_sweep(True, 1000)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def test_net_investment_income():
    # interest 5000 + ord div 10000 + cap gain 20000 = 35000 (excludes wages/IRA)
    inputs = {"taxable_interest": 5000, "ordinary_dividends": 10000,
              "capital_gains": 20000, "wages": 165000, "ira_distribution": 99999}
    assert rl._net_investment_income(inputs) == 35000.00


def test_net_investment_income_loss_floored():
    inputs = {"taxable_interest": 1000, "ordinary_dividends": 0, "capital_gains": -5000}
    assert rl._net_investment_income(inputs) == 1000.00


def test_irmaa_tiers_sorted():
    law = {"federal": {"irmaa": {"tiers": {"mfj": [
        {"magi_threshold": 266000, "annual_surcharge_per_person": 2800},
        {"magi_threshold": 212000, "annual_surcharge_per_person": 1000},
    ]}}}}
    tiers = rl._irmaa_tiers(law, "MFJ")
    assert [t["magi_threshold"] for t in tiers] == [212000, 266000]


def test_irmaa_tiers_malformed_threshold_raises():
    # A present-but-bad tier must NOT be silently dropped (would vanish a real crossing).
    law = {"federal": {"irmaa": {"tiers": {"mfj": [
        {"magi_threshold": 212000, "annual_surcharge_per_person": 1000},
        {"magi_threshold": "bad", "annual_surcharge_per_person": 2800},
    ]}}}}
    with pytest.raises(rl.RothLadderError, match="magi_threshold"):
        rl._irmaa_tiers(law, "MFJ")


def test_irmaa_tiers_malformed_surcharge_raises():
    # A valid threshold with a bad surcharge would otherwise flag a tier at $0/yr.
    law = {"federal": {"irmaa": {"tiers": {"mfj": [
        {"magi_threshold": 212000},  # missing surcharge
    ]}}}}
    with pytest.raises(rl.RothLadderError, match="annual_surcharge_per_person"):
        rl._irmaa_tiers(law, "MFJ")


def test_irmaa_tiers_empty_degrades():
    assert rl._irmaa_tiers({"federal": {"irmaa": {"tiers": {"mfj": []}}}}, "MFJ") == []
    assert rl._irmaa_tiers({}, "MFJ") == []


def test_irmaa_surcharge_picks_highest_crossed_tier():
    tiers = [{"magi_threshold": 212000, "annual_surcharge_per_person": 1000},
             {"magi_threshold": 266000, "annual_surcharge_per_person": 2800}]
    assert rl._irmaa_surcharge(200000, tiers) == 0.0
    assert rl._irmaa_surcharge(220000, tiers) == 1000.0
    assert rl._irmaa_surcharge(300000, tiers) == 2800.0


def test_niit_params_full_then_binary_key():
    law = {"federal": {"niit": {"rate": 0.038, "threshold": {"single": 200000, "mfj": 250000}}}}
    assert rl._niit_params(law, "MFJ") == (250000, 0.038)
    # HOH absent in map → falls back to binary 'single'
    assert rl._niit_params(law, "HOH") == (200000, 0.038)


def test_niit_params_missing_threshold():
    assert rl._niit_params({}, "MFJ") == (None, 0.038)


# --------------------------------------------------------------------------
# build_ladder — anchoring + sweep
# --------------------------------------------------------------------------

def test_zero_rung_equals_line_24():
    r = _build()
    assert r.rungs[0].amount == 0
    assert r.rungs[0].total_tax == 30000.00  # result.json line_24_total_tax
    assert r.rungs[0].cumulative_cost == 0.0
    assert r.rungs[0].marginal_rate is None
    assert r.anchor["line_24"] == 30000.00


def test_rung_count_and_amounts():
    r = _build(max_amount=100000, step=10000)
    assert [x.amount for x in r.rungs] == [0, 10000, 20000, 30000, 40000,
                                           50000, 60000, 70000, 80000, 90000, 100000]


def test_total_tax_monotonic_increasing():
    r = _build()
    totals = [x.total_tax for x in r.rungs]
    assert all(b >= a for a, b in zip(totals, totals[1:]))


def test_partial_final_rung_marginal_rate_uses_actual_delta():
    # --max not a step multiple → final rung delta is $5K, not the $10K step.
    # Marginal rate must divide by the real $5K spend, not the nominal step.
    r = _build(max_amount=25000, step=10000)
    assert [x.amount for x in r.rungs] == [0, 10000, 20000, 25000]
    last, prev = r.rungs[-1], r.rungs[-2]
    expected = round((last.total_tax - prev.total_tax) / (last.amount - prev.amount), 4)
    assert last.marginal_rate == expected
    # And it must be in a sane bracket range, not diluted by /step (the old bug → ~half).
    assert last.marginal_rate >= prev.marginal_rate - 0.0001


def test_cumulative_cost_tracks_total_minus_line24():
    r = _build()
    for rung in r.rungs:
        assert rung.cumulative_cost == round(rung.total_tax - 30000.00, 2)


def test_niit_addon_grows_after_threshold_and_caps_at_nii():
    r = _build(max_amount=100000, step=10000)
    by_amt = {x.amount: x for x in r.rungs}
    assert by_amt[40000].niit_addon == 0.0          # MAGI 210000 < 250000
    assert by_amt[60000].niit_addon == 380.00       # MAGI 260000: 3.8%×10000
    assert by_amt[70000].niit_addon == 760.00       # MAGI 270000: 3.8%×20000
    # NII is 35000, so the add-on caps once exposure hits 35000 (MAGI 285000).
    assert by_amt[90000].niit_addon == 1330.00      # 3.8%×35000
    assert by_amt[100000].niit_addon == 1330.00     # capped, no further growth


# --------------------------------------------------------------------------
# threshold flags
# --------------------------------------------------------------------------

def test_niit_onset_flag_at_first_crossing_only():
    r = _build()
    onset = [x for x in r.rungs if any("NIIT" in f for f in x.flags)]
    assert len(onset) == 1
    assert onset[0].amount == 50000  # MAGI 250000 >= threshold


def test_irmaa_tier_flags_at_both_crossings():
    r = _build()
    irmaa_amounts = [x.amount for x in r.rungs if any("IRMAA" in f for f in x.flags)]
    assert irmaa_amounts == [20000, 70000]  # MAGI 220000 (>=212K), 270000 (>=266K)


def test_ltcg_15_to_20_flag_but_not_0_to_15():
    # Baseline taxable income (170000) is already above the 0% ceiling (96000),
    # so 0%→15% is NOT a crossing; 15%→20% (200000) trips mid-sweep.
    r = _build()
    flags_flat = [f for x in r.rungs for f in x.flags]
    assert any("15%→20%" in f for f in flags_flat)
    assert not any("0%→15%" in f for f in flags_flat)
    bump = [x for x in r.rungs if any("15%→20%" in f for f in x.flags)][0]
    assert bump.amount == 40000  # taxable income 210000 > 200000


def test_crossings_summary_has_all_kinds():
    r = _build()
    kinds = {c.kind for c in r.crossings}
    assert kinds == {"irmaa", "ltcg_15_20", "niit"}


def test_irmaa_crossing_cost_is_incremental_over_previous_tier():
    # Two tiers crossed in the sweep: $1,000 then $2,800 cumulative. The crossings must
    # report the INCREMENT each ($1,000 then $1,800) so the list sums to the $2,800 total.
    r = _build()
    irmaa = [c for c in r.crossings if c.kind == "irmaa"]
    assert [c.est_annual_cost for c in irmaa] == [1000.00, 1800.00]


def test_irmaa_two_tiers_in_one_rung_still_incremental():
    # A single huge rung crosses both tiers at once; each still reports its increment.
    r = _build(max_amount=100000, step=100000)
    irmaa = [c for c in r.crossings if c.kind == "irmaa"]
    assert len(irmaa) == 2
    assert [c.est_annual_cost for c in irmaa] == [1000.00, 1800.00]
    assert sum(c.est_annual_cost for c in irmaa) == 2800.00  # total surcharge at top


def test_niit_onset_cost_is_full_exposure_not_zero():
    # Onset lands at the exact threshold (excess $0), but the reported cost must be the
    # fully-phased-in annual NIIT (3.8% × baseline NII = 0.038 × 35000 = 1330), not $0.
    r = _build()
    niit = [c for c in r.crossings if c.kind == "niit"]
    assert len(niit) == 1
    assert niit[0].est_annual_cost == 1330.00


def test_ltcg_crossings_have_no_dollar_cost():
    r = _build()
    for c in r.crossings:
        if c.kind.startswith("ltcg"):
            assert c.est_annual_cost is None


def test_modeled_flags_true_with_full_fixture():
    r = _build()
    assert r.niit_modeled is True
    assert r.irmaa_modeled is True


def test_niit_not_modeled_renders_banner():
    law = _load("tax_law_2026.json")
    law["federal"]["niit"]["threshold"] = {}
    r = _build(tax_law=law)
    assert r.niit_modeled is False
    md = rl.render_markdown(r)
    assert "NIIT NOT MODELED" in md


def test_irmaa_not_modeled_renders_banner():
    law = _load("tax_law_2026.json")
    law["federal"]["irmaa"]["tiers"]["mfj"] = []
    r = _build(tax_law=law)
    assert r.irmaa_modeled is False
    assert "IRMAA not modeled" in rl.render_markdown(r)


def test_no_crossings_note_names_unmodeled_thresholds():
    # Tiny sweep that trips nothing + NIIT unmodeled → the "none tripped" line must say so.
    law = _load("tax_law_2026.json")
    law["federal"]["niit"]["threshold"] = {}
    law["federal"]["irmaa"]["tiers"]["mfj"] = []
    r = _build(max_amount=1000, step=1000, tax_law=law)
    md = rl.render_markdown(r)
    assert "not modeled" in md.lower()


# --------------------------------------------------------------------------
# graceful degradation
# --------------------------------------------------------------------------

def test_degrade_without_irmaa_tiers():
    law = _load("tax_law_2026.json")
    law["federal"]["irmaa"]["tiers"]["mfj"] = []
    r = _build(tax_law=law)
    assert not any(c.kind == "irmaa" for c in r.crossings)
    assert not any("IRMAA" in f for x in r.rungs for f in x.flags)


def test_degrade_without_niit_threshold():
    law = _load("tax_law_2026.json")
    law["federal"]["niit"]["threshold"] = {}
    r = _build(tax_law=law)
    assert r.niit_threshold is None
    assert all(x.niit_addon == 0.0 for x in r.rungs)
    assert not any(c.kind == "niit" for c in r.crossings)


# --------------------------------------------------------------------------
# rendering + serialization
# --------------------------------------------------------------------------

def test_render_markdown_has_sections():
    md = rl.render_markdown(_build())
    for header in ("# Roth Conversion Ladder", "## Ladder", "## Threshold crossings",
                   "## Caveats", "Marginal rate"):
        assert header in md


def test_report_to_dict_roundtrips_json():
    data = rl._report_to_dict(_build())
    assert data["schema"] == "tax-roth-ladder/v1"
    assert json.loads(json.dumps(data, default=str))["rungs"][0]["total_tax"] == 30000.00


# --------------------------------------------------------------------------
# write guard
# --------------------------------------------------------------------------

def test_save_writes_under_scenarios(tmp_path):
    out = rl.save_scenario(tmp_path, "roth-ladder", rl._report_to_dict(_build()))
    assert out.parent == (tmp_path / "scenarios").resolve()
    assert out.exists()
    assert oct(out.stat().st_mode)[-3:] == "600"


def test_save_rejects_traversal_name(tmp_path):
    with pytest.raises(rl.ScenarioError):
        rl.save_scenario(tmp_path, "../evil", rl._report_to_dict(_build()))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cli(args, cwd):
    return subprocess.run([sys.executable, "-m", "agents.lib.roth_ladder", *args],
                          cwd=cwd, capture_output=True, text=True)


def test_cli_json_and_save(tmp_path):
    ws = _ws(tmp_path)
    p = _cli(["--result", "result.json", "--workspace", str(ws), "--year", "2026",
              "--max", "60000", "--step", "20000", "--today", "2026-05-26", "--json"], REPO)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["schema"] == "tax-roth-ladder/v1"
    assert out["rungs"][0]["total_tax"] == 30000.00
    assert (ws / "scenarios" / "roth-ladder.json").exists()


def test_cli_no_save(tmp_path):
    ws = _ws(tmp_path)
    p = _cli(["--result", "result.json", "--workspace", str(ws), "--year", "2026",
              "--max", "20000", "--step", "10000", "--no-save", "--json"], REPO)
    assert p.returncode == 0, p.stderr
    assert not (ws / "scenarios").exists()


def test_cli_year_mismatch_errors(tmp_path):
    ws = _ws(tmp_path)
    p = _cli(["--result", "result.json", "--workspace", str(ws), "--year", "2025"], REPO)
    assert p.returncode == 1
    assert "TY2026" in p.stderr


def test_cli_bad_step_errors(tmp_path):
    ws = _ws(tmp_path)
    p = _cli(["--result", "result.json", "--workspace", str(ws), "--year", "2026",
              "--max", "1000", "--step", "5000"], REPO)
    assert p.returncode == 1
    assert "exceed" in p.stderr
