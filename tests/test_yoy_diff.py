#!/usr/bin/env python3
"""Tests for agents/lib/yoy_diff.py — YoY What-Changed diff for /tax-review."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.yoy_diff import (
    HeadlineMetric,
    LineMover,
    SectionChange,
    YoYReport,
    compute_headlines,
    compute_line_movers,
    compute_section_changes,
    compute_yoy_diff,
    render_markdown,
)

# --- Inline fixtures -------------------------------------------------------

TAX_LAW = {
    "federal": {
        "filing_statuses": {
            "mfj": {"brackets": [
                {"rate": 0.10, "min": 0, "max": 23850},
                {"rate": 0.12, "min": 23851, "max": 96950},
                {"rate": 0.22, "min": 96951, "max": 206700},
                {"rate": 0.24, "min": 206701, "max": 394600},
                {"rate": 0.32, "min": 394601, "max": 501050},
                {"rate": 0.35, "min": 501051, "max": 751600},
                {"rate": 0.37, "min": 751601, "max": None},
            ]},
        }
    }
}

def _prior():
    return {
        "meta": {"tax_year": 2025, "filing_status": "MFJ"},
        "form_1040": {
            "line_11_agi": 180000, "line_15_taxable_income": 152500,
            "line_24_total_tax": 30000, "line_33_total_payments": 31200,
            "line_35a_refund": 1200, "line_37_amount_owed": 0,
            "line_1z_total_wages": 160000, "line_7_capital_gain": 4000,
        },
        "form_8889_hsa": {"line_13_hsa_deduction": 3850},
    }

def _current():
    return {
        "meta": {"tax_year": 2026, "filing_status": "MFJ"},
        "form_1040": {
            "line_11_agi": 215200, "line_15_taxable_income": 186100,
            "line_24_total_tax": 43000, "line_33_total_payments": 42420,
            "line_35a_refund": 0, "line_37_amount_owed": 580,
            "line_1z_total_wages": 190000, "line_7_capital_gain": 14000,
        },
        "schedule_c": {"line_31_net": 12000},
    }


def test_headlines_compute_all_metrics():
    h = compute_headlines(_prior(), _current(), TAX_LAW)
    by_label = {m.label: m for m in h}
    assert by_label["AGI"].current == 215200
    assert by_label["AGI"].prior == 180000
    assert by_label["AGI"].delta == 35200
    assert round(by_label["AGI"].delta_pct, 1) == 19.6
    assert by_label["Marginal bracket"].current == 22.0
    assert by_label["Marginal bracket"].unit == "pts"
    assert by_label["Refund / (Owed)"].prior == 1200
    assert by_label["Refund / (Owed)"].current == -580
    assert by_label["Refund / (Owed)"].delta == -1780


def test_headlines_zero_prior_agi_gives_none_pct():
    p = _prior()
    p["form_1040"]["line_11_agi"] = 0
    h = compute_headlines(p, _current(), TAX_LAW)
    agi = next(m for m in h if m.label == "AGI")
    assert agi.delta_pct is None


def test_headlines_empty_tax_law_marginal_bracket_none():
    h = compute_headlines(_prior(), _current(), {})
    mb = next(m for m in h if m.label == "Marginal bracket")
    assert mb.current is None
    assert mb.prior is None


def test_line_movers_threshold_filters():
    movers = compute_line_movers(_prior(), _current(), threshold=1000)
    labels = {m.label for m in movers}
    assert "Wages (1040 line 1z)" in labels      # +30000
    assert "Capital gain (1040 line 7)" in labels  # +10000
    assert "HSA deduction (Form 8889)" in labels   # -3850 (3850 -> 0)
    assert "Schedule C net" in labels              # 0 -> 12000


def test_line_movers_sorted_by_abs_delta_desc():
    movers = compute_line_movers(_prior(), _current(), threshold=1000)
    deltas = [abs(m.delta) for m in movers]
    assert deltas == sorted(deltas, reverse=True)
    assert movers[0].label == "Wages (1040 line 1z)"  # 30000 largest


def test_line_movers_custom_threshold_excludes_small():
    movers = compute_line_movers(_prior(), _current(), threshold=11000)
    labels = {m.label for m in movers}
    assert "Wages (1040 line 1z)" in labels       # 30000 >= 11000
    assert "Capital gain (1040 line 7)" not in labels  # 10000 < 11000


def test_line_movers_missing_section_treated_as_zero():
    movers = compute_line_movers(_prior(), _current(), threshold=1000)
    sc = next(m for m in movers if m.label == "Schedule C net")
    assert sc.prior == 0
    assert sc.current == 12000
    assert sc.delta == 12000


def test_line_movers_includes_state_only_when_both_years_have_ca():
    # Neither fixture has california_540 by default -> no state movers.
    base_prior = _prior()
    base_current = _current()
    movers = compute_line_movers(base_prior, base_current, threshold=1000)
    assert not any(m.label.startswith("State ") for m in movers)

    # Both years carry CA with a large state-tax swing -> state mover appears.
    p = _prior(); p["california_540"] = {"ca_total_tax": 5000}
    c = _current(); c["california_540"] = {"ca_total_tax": 9000}
    movers = compute_line_movers(p, c, threshold=1000)
    assert any(m.label == "State total tax" and m.delta == 4000 for m in movers)

    # Only current year has CA -> state fields excluded (no phantom delta).
    p2 = _prior()  # no california_540
    c2 = _current(); c2["california_540"] = {"ca_total_tax": 9000}
    movers = compute_line_movers(p2, c2, threshold=1000)
    assert not any(m.label.startswith("State ") for m in movers)


def test_line_movers_includes_delta_exactly_at_threshold():
    p = _prior()
    c = _current()
    # Force capital gain delta to exactly 1000: prior 4000 -> current 5000.
    p["form_1040"]["line_7_capital_gain"] = 4000
    c["form_1040"]["line_7_capital_gain"] = 5000
    movers = compute_line_movers(p, c, threshold=1000)
    assert any(m.label == "Capital gain (1040 line 7)" and m.delta == 1000 for m in movers)


def test_section_appeared():
    changes = compute_section_changes(_prior(), _current())
    appeared = [c for c in changes if c.kind == "appeared"]
    assert any(c.section == "schedule_c" for c in appeared)


def test_section_disappeared():
    changes = compute_section_changes(_prior(), _current())
    disappeared = [c for c in changes if c.kind == "disappeared"]
    assert any(c.section == "form_8889_hsa" for c in disappeared)


def test_section_no_change_when_identical():
    same = _prior()
    changes = compute_section_changes(same, dict(same))
    assert changes == []


def test_section_all_zero_values_treated_as_absent():
    p = _prior()
    c = _current()
    # schedule_d present in current but all-zero -> NOT counted as appeared.
    c["schedule_d"] = {"line_16_combined": 0, "part_i": {"net": 0.0}}
    changes = compute_section_changes(p, c)
    assert not any(ch.section == "schedule_d" for ch in changes)


def test_section_scaffold_only_block_treated_as_absent():
    # A retained template block carrying only scaffold defaults (a string field,
    # _-prefixed keys, zero numbers) must NOT count as an appeared section.
    p = _prior()  # no schedule_c
    c = _current()
    c["schedule_c"] = {"_include_if": "has_self_employment flag is true",
                       "accounting_method": "Cash", "_note_line_31": "note",
                       "line_31_net": 0}
    changes = compute_section_changes(p, c)
    assert not any(ch.section == "schedule_c" for ch in changes)


def test_state_movers_suppressed_when_one_year_scaffold_only():
    # Prior keeps a zeroed/scaffold california_540; current has real CA tax.
    # No artificial state delta should be reported (key presence alone is not enough).
    p = _prior(); p["california_540"] = {"_include_if": "state is CA", "ca_total_tax": 0}
    c = _current(); c["california_540"] = {"ca_total_tax": 9000}
    movers = compute_line_movers(p, c, threshold=1000)
    assert not any(m.label.startswith("State ") for m in movers)


def test_section_boolean_scaffold_flag_not_counted_present():
    # fbar_fincen_114 template defaults include filed_separately=true. A block with
    # only boolean/scaffold flags and empty accounts must NOT count as present...
    p = _prior()  # no fbar_fincen_114
    c = _current()
    c["fbar_fincen_114"] = {"_include_if": "...", "required": False,
                            "filed_separately": True, "note": "x", "accounts": []}
    changes = compute_section_changes(p, c)
    assert not any(ch.section == "fbar_fincen_114" for ch in changes)
    # ...but real accounts make FBAR appear, so genuine transitions aren't missed.
    c["fbar_fincen_114"]["accounts"] = [{"institution": "Foreign Bank", "account_last4": "1234"}]
    changes = compute_section_changes(p, c)
    assert any(ch.section == "fbar_fincen_114" and ch.kind == "appeared" for ch in changes)


def test_cli_rejects_non_finite_threshold(tmp_path):
    _write_workspace(tmp_path, 2026, _current())
    _write_archive(tmp_path, 2025, _prior())
    for bad in ("nan", "inf"):
        res = _run_cli(["--prior", str(tmp_path / "archive" / "2025"),
                        "--current", str(tmp_path), "--year", "2026", "--threshold", bad])
        assert res.returncode == 1, bad
        assert "ERROR" in res.stderr


def test_compute_yoy_diff_wires_together():
    report = compute_yoy_diff(_prior(), _current(), TAX_LAW)  # default threshold 1000
    assert report.prior_year == 2025
    assert report.current_year == 2026
    assert len(report.headlines) == 7
    assert any(m.label == "Wages (1040 line 1z)" for m in report.line_movers)
    assert any(c.section == "schedule_c" for c in report.sections_changed)


def test_render_markdown_contains_expected_sections():
    report = compute_yoy_diff(_prior(), _current(), TAX_LAW)
    md = render_markdown(report)
    assert "## YoY What-Changed — TY2025 → TY2026" in md
    assert "### Headlines" in md
    assert "| AGI " in md
    assert "### Line movers" in md
    assert "Wages (1040 line 1z)" in md
    assert "### Sections that appeared" in md


def test_render_markdown_zero_movers_note():
    same = _prior()
    report = compute_yoy_diff(same, dict(same), TAX_LAW, threshold=1000)
    md = render_markdown(report)
    assert "No line items moved" in md


def test_render_markdown_agi_row_exact_format():
    report = compute_yoy_diff(_prior(), _current(), TAX_LAW)
    md = render_markdown(report)
    # AGI: prior 180000 -> current 215200, delta +35200, +19.6%
    assert "| AGI | $180,000 | $215,200 | +$35,200 | +19.6% |" in md


# --- CLI tests ----------------------------------------------------------------

def _write_workspace(tmp_path, year, result, with_tax_law=True):
    """Create a minimal workspace dir with result.json (+ tax_law) and an archive."""
    (tmp_path / "result.json").write_text(json.dumps(result))
    if with_tax_law:
        (tmp_path / f"tax_law_{year}.json").write_text(json.dumps(TAX_LAW))
    return tmp_path


def _write_archive(tmp_path, prior_year, prior_result):
    arch = tmp_path / "archive" / str(prior_year)
    arch.mkdir(parents=True)
    (arch / "result.json").write_text(json.dumps(prior_result))
    (arch / "taxpayer_profile.json").write_text(json.dumps(
        {"filing_status": prior_result["meta"]["filing_status"]}))
    return arch


def _run_cli(args):
    script = Path(__file__).parent.parent / "agents" / "lib" / "yoy_diff.py"
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True)


def test_cli_json_output_parseable(tmp_path):
    _write_workspace(tmp_path, 2026, _current())
    _write_archive(tmp_path, 2025, _prior())
    res = _run_cli(["--prior", str(tmp_path / "archive" / "2025"),
                    "--current", str(tmp_path), "--year", "2026", "--json"])
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["prior_year"] == 2025
    assert any(m["label"] == "AGI" for m in payload["headlines"])


def test_cli_markdown_output(tmp_path):
    _write_workspace(tmp_path, 2026, _current())
    _write_archive(tmp_path, 2025, _prior())
    res = _run_cli(["--prior", str(tmp_path / "archive" / "2025"),
                    "--current", str(tmp_path), "--year", "2026"])
    assert res.returncode == 0, res.stderr
    assert "## YoY What-Changed — TY2025 → TY2026" in res.stdout


def test_cli_missing_archive_exits_nonzero(tmp_path):
    _write_workspace(tmp_path, 2026, _current())
    res = _run_cli(["--prior", str(tmp_path / "archive" / "2025"),
                    "--current", str(tmp_path), "--year", "2026"])
    assert res.returncode == 1
    assert "ERROR" in res.stderr


def test_cli_year_mismatch_exits_nonzero(tmp_path):
    _write_workspace(tmp_path, 2026, _current())
    bad = _prior(); bad["meta"]["tax_year"] = 2024
    _write_archive(tmp_path, 2025, bad)
    res = _run_cli(["--prior", str(tmp_path / "archive" / "2025"),
                    "--current", str(tmp_path), "--year", "2026"])
    assert res.returncode == 1
    assert "ERROR" in res.stderr


def test_cli_threshold_override_changes_movers(tmp_path):
    _write_workspace(tmp_path, 2026, _current())
    _write_archive(tmp_path, 2025, _prior())
    # Default threshold 1000 includes capital gain (delta 10000); a 20000
    # threshold should exclude it while still including wages (delta 30000).
    res = _run_cli(["--prior", str(tmp_path / "archive" / "2025"),
                    "--current", str(tmp_path), "--year", "2026",
                    "--threshold", "20000", "--json"])
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    labels = {m["label"] for m in payload["line_movers"]}
    assert "Wages (1040 line 1z)" in labels
    assert "Capital gain (1040 line 7)" not in labels


def test_cli_missing_current_result_exits_nonzero(tmp_path):
    # Archive exists, but no result.json in the current workspace.
    _write_archive(tmp_path, 2025, _prior())
    res = _run_cli(["--prior", str(tmp_path / "archive" / "2025"),
                    "--current", str(tmp_path), "--year", "2026"])
    assert res.returncode == 1
    assert "ERROR" in res.stderr


def test_headlines_prior_bracket_uses_prior_year_tax_law():
    # Prior year used a different schedule than the current year. The prior
    # marginal bracket must come from prior_tax_law, not the current tax_law.
    prior_law = {"federal": {"filing_statuses": {"mfj": {"brackets": [
        {"rate": 0.10, "min": 0, "max": 100000},
        {"rate": 0.50, "min": 100001, "max": None},
    ]}}}}
    h = compute_headlines(_prior(), _current(), TAX_LAW, prior_tax_law=prior_law)
    mb = next(m for m in h if m.label == "Marginal bracket")
    assert mb.prior == 50.0    # 152,500 taxable → prior_law top bracket
    assert mb.current == 22.0  # 186,100 → current TAX_LAW (96,951–206,700)
    # Without prior_tax_law the prior bracket falls back to the current schedule.
    mb_fb = next(m for m in compute_headlines(_prior(), _current(), TAX_LAW)
                 if m.label == "Marginal bracket")
    assert mb_fb.prior == 22.0


def test_cli_current_year_mismatch_exits_nonzero(tmp_path):
    # Current result.json carries the wrong tax year for the requested --year.
    cur = _current(); cur["meta"]["tax_year"] = 2025  # should be 2026
    _write_workspace(tmp_path, 2026, cur)
    _write_archive(tmp_path, 2025, _prior())
    res = _run_cli(["--prior", str(tmp_path / "archive" / "2025"),
                    "--current", str(tmp_path), "--year", "2026"])
    assert res.returncode == 1
    assert "ERROR" in res.stderr
