"""Tests for the PTET election analyzer — agents/lib/ptet.py (Backlog #9).

Expected dollar values are hand-computed from the statutory PTET rate schedules
and a deterministic synthetic baseline (MFJ brackets: 10% to 20k, 20% to 200k,
30% above; standard deduction 30k). Update the comments if you change them.
"""
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import ptet
from agents.lib.ptet import PtetError, analyze, ptet_tax, render_markdown

FIX = Path(__file__).resolve().parent / "fixtures" / "ptet"
REPO = Path(__file__).resolve().parent.parent

# Synthetic MFJ rate kwargs matching the scenario-test brackets.
MFJ_BRACKETS = [
    {"rate": 0.10, "min": 0, "max": 20000},
    {"rate": 0.20, "min": 20000, "max": 200000},
    {"rate": 0.30, "min": 200000, "max": None},
]
RATE_KW = dict(brackets=MFJ_BRACKETS, standard_deduction=30000,
               cg_zero_limit=90000, cg_fifteen_limit=550000)
# wages + $400k of K-1 pass-through income (other_income) → taxable income deep
# in the 30% top bracket so a deduction is priced at exactly 30%. The large
# other_income bucket is the pass-through income PTET is computed against (and
# clears the phantom-income guard for multi-state qualifying amounts).
BASELINE = {"wages": 150000, "other_income": 400000}


# --------------------------------------------------------------------------
# ptet_tax — graduated schedules and overrides
# --------------------------------------------------------------------------

def test_ptet_tax_ca_flat():
    tax, note = ptet_tax("CA", 200000)
    assert tax == 18600.0          # 200,000 × 9.3%
    assert "9.300%" in note


def test_ptet_tax_ny_first_bracket():
    tax, _ = ptet_tax("NY", 200000)
    assert tax == 13700.0          # 200,000 × 6.85%


def test_ptet_tax_ny_second_bracket_marginal():
    tax, note = ptet_tax("NY", 3_000_000)
    # 137,000 base + 9.65% × (3M − 2M) = 137,000 + 96,500
    assert tax == 233500.0
    assert "9.650%" in note and "graduated" in note


def test_ptet_tax_nj_first_bracket():
    tax, _ = ptet_tax("NJ", 200000)
    assert tax == 11350.0          # 200,000 × 5.675%


def test_ptet_tax_nj_second_bracket():
    tax, _ = ptet_tax("NJ", 300000)
    # 14,187.50 base + 6.52% × (300,000 − 250,000) = 14,187.50 + 3,260
    assert tax == 17447.5


def test_ptet_tax_nj_over_1m_uses_2022_top_rate():
    # TY2022+ (P.L. 2021 c.419): income over $1M is taxed at 10.9%, NOT the
    # legacy 9.12% $1M–$5M bracket. $3M → 63,087.50 + 10.9% × 2,000,000.
    tax, _ = ptet_tax("NJ", 3_000_000)
    assert tax == 281087.50


def test_ptet_tax_nj_legacy_schedule_pre_2022():
    # Pre-2022 the 9.12% $1M–$5M bracket applied: 63,087.50 + 9.12% × 2,000,000.
    tax, _ = ptet_tax("NJ", 3_000_000, tax_year=2021)
    assert tax == 245487.50
    # Current schedule is $35,600 higher (the bug Codex P1 flagged).
    cur, _ = ptet_tax("NJ", 3_000_000, tax_year=2022)
    assert round(cur - tax, 2) == 35600.0


def test_ptet_tax_rate_override_is_flat():
    tax, note = ptet_tax("NY", 100000, rate_override=0.05)
    assert tax == 5000.0
    assert "override" in note


def test_ptet_tax_rejects_negative_income():
    with pytest.raises(PtetError):
        ptet_tax("CA", -1)


def test_ptet_tax_rejects_rate_above_one():
    with pytest.raises(PtetError):
        ptet_tax("CA", 100000, rate_override=1.5)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "100"])
def test_ptet_tax_rejects_non_finite_and_bool_and_str(bad):
    with pytest.raises(PtetError):
        ptet_tax("CA", bad)


# --------------------------------------------------------------------------
# analyze — anchored federal benefit
# --------------------------------------------------------------------------

def test_analyze_ca_full_benefit_when_cap_exhausted():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ")
    assert r.total_ptet_paid == 18600.0
    ca = r.states[0]
    # $18,600 deduction entirely inside the 30% top bracket → $5,580 federal.
    assert ca.gross_fed_benefit == 5580.0
    assert ca.lost_salt_offset == 0.0
    assert ca.net_fed_savings == 5580.0
    assert r.total_net_fed_savings == 5580.0


def test_analyze_salt_room_reduces_benefit():
    # $10k of unused SALT cap at a 30% marginal rate was deductible anyway →
    # net benefit = 5,580 − (0.30 × 10,000) = 2,580.
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                salt_room=10000, marginal_fed=0.30)
    ca = r.states[0]
    assert ca.lost_salt_offset == 3000.0
    assert ca.net_fed_savings == 2580.0


def test_analyze_salt_room_auto_estimates_marginal():
    # Without an explicit marginal rate, analyze probes the keystone (the
    # baseline is in the 30% bracket) → offset 0.30 × 5,000 = 1,500.
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                salt_room=5000)
    assert r.est_marginal_fed == 0.30
    assert r.states[0].lost_salt_offset == 1500.0


def test_analyze_two_states_aggregate_and_allocate():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000, "NY": 100000}, tax_year=2026,
                filing_status="MFJ")
    # CA 18,600 + NY 6,850 = 25,450 PTET. Per-state benefit allocated pro-rata
    # of the anchored total; the sum reconciles to the aggregate.
    assert r.total_ptet_paid == 25450.0
    assert round(sum(s.net_fed_savings for s in r.states), 2) == r.total_net_fed_savings


def test_analyze_zero_income_is_deadline_reminder_mode():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 0.0}, tax_year=2026, filing_status="MFJ")
    ca = r.states[0]
    assert ca.ptet_paid == 0.0
    assert ca.net_fed_savings == 0.0
    assert ca.election_deadline == "June 15, 2026"
    assert any("--income" in c for c in ca.caveats)


def test_analyze_unmodeled_state_flagged_not_silently_zeroed():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"TX": 50000}, tax_year=2026, filing_status="MFJ")
    tx = r.states[0]
    assert tx.modeled is False
    assert tx.net_fed_savings == 0.0
    assert any("no PTET regime modeled" in c for c in tx.caveats)


def test_analyze_election_deadlines_per_state():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 1, "NY": 1, "NJ": 1}, tax_year=2026,
                filing_status="MFJ")
    by = {s.state: s.election_deadline for s in r.states}
    # L1: §7503 weekend rollover. June 15 2026 = Mon (no roll); NJ Mar 15 2027 =
    # Mon (no roll); NY statutory Mar 15 2026 = Sunday → observed Mar 16.
    assert by["CA"] == "June 15, 2026"      # current-year prepayment
    assert by["NY"] == "March 16, 2026"     # election due IN the tax year (Mar 15 = Sun)
    assert by["NJ"] == "March 15, 2027"     # on the entity return


def test_analyze_graduated_multiowner_caveat_present():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"NY": 100000}, tax_year=2026, filing_status="MFJ")
    assert any("multi-owner" in c for c in r.states[0].caveats)
    # ...but suppressed when the user overrode the rate.
    r2 = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                 state_incomes={"NY": 100000}, tax_year=2026, filing_status="MFJ",
                 rate_overrides={"NY": 0.0685})
    assert not any("multi-owner" in c for c in r2.states[0].caveats)


def test_analyze_salt_assumption_note_toggles():
    r0 = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                 state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ")
    assert any("already fully consumed" in n for n in r0.notes)
    r1 = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                 state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                 salt_room=8000, marginal_fed=0.30)
    assert any("unused SALT cap headroom" in n for n in r1.notes)


def test_analyze_qbi_eligible_reduces_benefit():
    # Codex P2: for §199A-eligible K-1 income the PTET shrinks the QBI base, so
    # the owner loses 20% × PTET of QBI deduction. Modeled as a haircut to the
    # income reduction: 18,600 × (1 − 0.20) = 14,880 → 14,880 × 30% = 4,464,
    # vs 5,580 without the clawback.
    r_no = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                   state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ")
    r_qbi = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                    state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                    qbi_eligible_states={"CA"})
    assert r_no.states[0].net_fed_savings == 5580.0
    assert r_qbi.states[0].net_fed_savings == 4464.0
    assert any("QBI clawback modeled" in c for c in r_qbi.states[0].caveats)


def test_analyze_qbi_caveat_warns_when_not_flagged():
    # When the user does NOT mark the income QBI-eligible, a caveat warns the
    # savings may be overstated (the safe direction — don't silently overstate).
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ")
    assert any("QBI-eligible" in c and "--qbi-eligible" in c
               for c in r.states[0].caveats)


def test_ca_deadline_note_is_year_aware():
    # Codex P2: AB 150 (2022-2025) — June 15 is a hard election prerequisite.
    # SB 132 (2026-2030) — election survives a missed payment; 12.5% credit hit.
    d25, n25 = ptet._election_deadline("CA", 2025)
    d26, n26 = ptet._election_deadline("CA", 2026)
    # L1: June 15 2025 is a Sunday → observed June 16; June 15 2026 is a Monday.
    assert d25 == "June 16, 2025" and d26 == "June 15, 2026"
    assert "REQUIRED" in n25 and "lost if missed" in n25
    assert "12.5%" in n26 and "REMAINS AVAILABLE" in n26


def test_ca_deadline_ty2021_has_no_june_prepayment():
    # Codex P2 (round 8): AB 150's first year (TY2021) had NO June prepayment
    # requirement — elect on the timely-filed return (due March 15, 2022).
    d21, n21 = ptet._election_deadline("CA", 2021)
    assert d21 == "March 15, 2022"
    assert "NO June 15 prepayment" in n21 and "first year" in n21


def test_analyze_rejects_nan_salt_room():
    with pytest.raises(PtetError):
        analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 1}, tax_year=2026, filing_status="MFJ",
                salt_room=float("nan"))


def test_salt_cap_is_year_and_status_aware():
    # OBBBA: $40k ($20k MFS) for TY2025-2029; $10k ($5k MFS) otherwise.
    assert ptet._salt_cap(2026, "MFJ") == 40000.0
    assert ptet._salt_cap(2026, "MFS") == 20000.0
    assert ptet._salt_cap(2024, "MFJ") == 10000.0
    assert ptet._salt_cap(2030, "MFJ") == 10000.0
    assert ptet._salt_cap(2030, "MFS") == 5000.0


def test_salt_cap_prefers_tax_law_cache():
    """A fetched federal.salt_cap block wins over the built-in schedule — the
    cache tracks the indexed 2026-2029 amounts the schedule cannot."""
    cfg = {"amount": 40400, "mfs_amount": 20200}
    assert ptet._salt_cap(2026, "MFJ", cfg) == 40400.0
    assert ptet._salt_cap(2026, "MFS", cfg) == 20200.0


def test_salt_cap_cache_partial_falls_back():
    """Unfetched (None) cache values fall back to the statutory schedule."""
    assert ptet._salt_cap(2026, "MFJ", {"amount": None, "mfs_amount": None}) == 40000.0
    assert ptet._salt_cap(2026, "MFS", {"amount": 40400}) == 20000.0
    assert ptet._salt_cap(2026, "MFJ", None) == 40000.0


def test_salt_cap_cache_invalid_value_hard_errors():
    """A present-but-corrupt cache value must not silently price the wrong cap."""
    with pytest.raises(PtetError):
        ptet._salt_cap(2026, "MFJ", {"amount": float("nan")})
    with pytest.raises(PtetError):
        ptet._salt_cap(2026, "MFJ", {"amount": "forty thousand"})
    with pytest.raises(PtetError):
        ptet._salt_cap(2026, "MFJ", {"amount": -1})


def test_analyze_other_salt_derives_headroom_from_cap():
    # Codex P1: TY2026 cap is $40k. With $25k of other SALT, $15k of headroom was
    # deductible anyway → offset 30% × min(15000, 18600) = 4,500; net 5,580 − 4,500.
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                other_salt=25000, marginal_fed=0.30)
    assert r.salt_room == 15000.0
    assert r.states[0].lost_salt_offset == 4500.0
    assert r.states[0].net_fed_savings == 1080.0


def test_analyze_other_salt_zero_headroom_when_salt_exceeds_cap():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                other_salt=50000, marginal_fed=0.30)
    assert r.salt_room == 0.0
    assert r.states[0].net_fed_savings == 5580.0


def test_analyze_standard_deduction_suppresses_salt_offset():
    # Codex P2 (round 8): a standard-deduction filer gets no SALT itemized
    # deduction, so the forgone-SALT offset must be $0 even with headroom — the
    # full PTET is incremental. (itemizer baseline would net out the offset.)
    r_item = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                     state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                     salt_room=10000, marginal_fed=0.30, itemizes=True)
    r_std = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                    state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                    salt_room=10000, marginal_fed=0.30, itemizes=False)
    assert r_item.states[0].lost_salt_offset == 3000.0
    assert r_std.states[0].lost_salt_offset == 0.0
    assert r_std.states[0].net_fed_savings == 5580.0   # full gross, no offset
    assert any("STANDARD deduction" in n for n in r_std.notes)


def test_analyze_default_note_cites_current_cap_not_10k():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ")
    assert any("$40,000 SALT cap" in n for n in r.notes)
    assert any("phases DOWN" in n for n in r.notes)  # MAGI phasedown caveat


def test_analyze_phantom_income_guard_uses_passthrough_bucket():
    # Codex P2 (round 7): the guard compares against the pass-through bucket
    # (other_income = $400k here), NOT total gross — so wages can't mask a missing
    # K-1. $500k of CA K-1 exceeds the $400k bucket → refuse.
    with pytest.raises(PtetError):
        analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 500000}, tax_year=2026, filing_status="MFJ")
    # ...income within the pass-through bucket is fine.
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 300000}, tax_year=2026, filing_status="MFJ")
    assert r.total_ptet_paid == round(300000 * 0.093, 2)


def test_analyze_passthrough_income_overrides_composite_bucket():
    # Codex P2 (round 9): when the caller supplies the raw Schedule 1 line 8
    # (pure K-1), the guard uses it instead of the composite other_income (which
    # also holds taxable SS + short-term gains and would overstate the K-1 bucket).
    # BASELINE other_income=$400k, but only $30k is actual K-1.
    with pytest.raises(PtetError):
        analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 50000}, tax_year=2026, filing_status="MFJ",
                passthrough_income=30000)
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 20000}, tax_year=2026, filing_status="MFJ",
                passthrough_income=30000)
    assert r.total_ptet_paid == round(20000 * 0.093, 2)


def test_analyze_phantom_guard_ignores_wages_in_bucket():
    # Wage-heavy return with NO K-1 in the pass-through bucket: a $100k claim must
    # be refused even though total gross ($300k wages) would have masked it.
    wage_only = {"wages": 300000, "other_income": 0}
    with pytest.raises(PtetError):
        analyze(baseline_inputs=wage_only, rate_kwargs=RATE_KW,
                state_incomes={"CA": 100000}, tax_year=2026, filing_status="MFJ")


def test_marginal_probe_excludes_additional_medicare():
    # Codex P2 (round 7): the SALT-offset marginal probe must use ordinary non-wage
    # income so a high earner's 0.9% Additional Medicare Tax isn't folded in.
    kw = dict(RATE_KW, addl_medicare_threshold=200000, addl_medicare_rate=0.009)
    base = {"wages": 300000, "other_income": 60000}  # wages above the threshold
    r = analyze(baseline_inputs=base, rate_kwargs=kw, state_incomes={"CA": 50000},
                tax_year=2026, filing_status="MFJ", salt_room=5000)
    # Top ordinary bracket is 30%; a wage probe would have read 30.9%.
    assert r.est_marginal_fed == 0.30


@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), float("inf"), True])
def test_analyze_rejects_bad_marginal_fed(bad):
    # Codex P2: argparse type=float accepts -1/nan/inf/1.5, corrupting the offset.
    with pytest.raises(PtetError):
        analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                salt_room=1000, marginal_fed=bad)


def test_analyze_ca_after_sunset_not_modeled():
    # Codex P2: CA's regime sunsets after TY2030 (SB 132). TY2031 must not be
    # priced as a live election — mirrors tax_calendar's out-of-window note.
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2031, filing_status="MFJ")
    assert r.states[0].modeled is False
    assert r.total_net_fed_savings == 0.0
    assert any("outside that window" in c for c in r.states[0].caveats)


def test_analyze_ca_before_window_not_modeled():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2019, filing_status="MFJ")
    assert r.states[0].modeled is False


def test_analyze_nj_window_start_is_modeled():
    # NJ BAIT starts TY2020 — boundary year must still be modeled.
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"NJ": 200000}, tax_year=2020, filing_status="MFJ")
    assert r.states[0].modeled is True
    assert r.states[0].ptet_paid == 11350.0


# --------------------------------------------------------------------------
# render_markdown
# --------------------------------------------------------------------------

def test_render_markdown_has_sections_and_totals():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ")
    md = render_markdown(r)
    assert "PTET Election Analysis — TY2026" in md
    assert "## CA — election due June 15, 2026" in md
    assert "Total net federal savings" in md
    assert "$5,580" in md


# --------------------------------------------------------------------------
# validation helpers
# --------------------------------------------------------------------------

def test_normalize_state_uppercases_and_validates():
    assert ptet._normalize_state("ca") == "CA"
    with pytest.raises(PtetError):
        ptet._normalize_state("California")


def test_parse_state_amounts_rejects_malformed():
    with pytest.raises(PtetError):
        ptet._parse_state_amounts(["CA"], what="income")
    with pytest.raises(PtetError):
        ptet._parse_state_amounts(["CA=abc"], what="income")
    assert ptet._parse_state_amounts(["ca=200000"], what="income") == {"CA": 200000.0}


def test_parse_state_amounts_sums_repeated_income():
    # Codex P2: two K-1 entities in the same state accumulate, not overwrite.
    assert ptet._parse_state_amounts(
        ["CA=100000", "CA=50000", "NY=20000"], what="income", combine="sum"
    ) == {"CA": 150000.0, "NY": 20000.0}


def test_parse_state_amounts_rejects_repeated_rate():
    # A duplicate rate override is ambiguous — reject rather than silently pick one.
    with pytest.raises(PtetError):
        ptet._parse_state_amounts(["CA=0.09", "CA=0.05"], what="ptet-rate",
                                  combine="reject")


def test_parse_state_amounts_rejects_malformed_state_code():
    # Codex P2: a typo'd override key must fail fast, not silently no-op.
    with pytest.raises(PtetError):
        ptet._parse_state_amounts(["California=0.05"], what="ptet-rate",
                                  combine="reject")


def test_analyze_orphan_rate_override_raises():
    # An override for a state with no --income would be silently dropped.
    with pytest.raises(PtetError):
        analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                rate_overrides={"NY": 0.05})


# --------------------------------------------------------------------------
# CLI (subprocess) — end-to-end + security
# --------------------------------------------------------------------------

def _ws(tmp_path) -> Path:
    for f in ("result.json", "taxpayer_profile.json", "tax_law_2026.json"):
        shutil.copy(FIX / f, tmp_path / f)
    return tmp_path


def _run_cli(args):
    return subprocess.run(
        [sys.executable, "-m", "agents.lib.ptet", *args],
        cwd=str(REPO), capture_output=True, text=True,
    )


def test_cli_end_to_end(tmp_path):
    ws = _ws(tmp_path)
    res = _run_cli(["--workspace", str(ws), "--year", "2026", "--income", "CA=200000"])
    assert res.returncode == 0, res.stderr
    assert "PTET Election Analysis" in res.stdout
    assert "CA" in res.stdout and "June 15, 2026" in res.stdout
    assert "Total net federal savings" in res.stdout


def test_cli_defaults_to_k1_states_when_no_income(tmp_path):
    # Fixture profile carries flags.k1_states = ["CA","NY"] → deadline-reminder mode.
    ws = _ws(tmp_path)
    res = _run_cli(["--workspace", str(ws), "--year", "2026"])
    assert res.returncode == 0, res.stderr
    assert "CA" in res.stdout and "NY" in res.stdout
    assert "--income" in res.stdout    # the "supply income" caveat


def test_cli_stale_k1_states_not_resurrected_without_has_k1_income(tmp_path):
    # Codex P2: a profile whose pass-through income is gone (has_k1_income False)
    # but still carries a stale k1_states must NOT get PTET advice.
    ws = _ws(tmp_path)
    prof = json.loads((ws / "taxpayer_profile.json").read_text())
    prof.setdefault("flags", {})
    prof["flags"]["has_k1_income"] = False
    prof["flags"]["k1_states"] = ["CA", "NY"]
    (ws / "taxpayer_profile.json").write_text(json.dumps(prof))
    res = _run_cli(["--workspace", str(ws), "--year", "2026"])
    assert res.returncode == 0, res.stderr
    assert "No modeled PTET states" in res.stdout
    assert "June 15" not in res.stdout


def test_cli_qbi_eligible_flag_priced(tmp_path):
    ws = _ws(tmp_path)
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000", "--qbi-eligible", "CA"])
    assert res.returncode == 0, res.stderr
    assert "QBI clawback modeled" in res.stdout


def _set_result_fields(ws, **fields):
    res = json.loads((ws / "result.json").read_text())
    res.setdefault("form_1040", {}).update(fields)
    (ws / "result.json").write_text(json.dumps(res))


def test_cli_phantom_guard_prefers_dedicated_k1_field(tmp_path):
    # L2: the fixture has line_8_schedule1_other=250000 (which would ADMIT a
    # $200K K-1 claim), but only $50K of that is actually K-1. With the dedicated
    # schedule_e_k1_income=50000 field present, a --income CA=200000 claim is a
    # phantom and the guard must FAIL — proving it compares against the K-1
    # field, not the aggregate line 8.
    ws = _ws(tmp_path)
    _set_result_fields(ws, schedule_e_k1_income=50000)
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000"])
    assert res.returncode != 0
    assert "K-1 / Schedule E pass-through income" in res.stderr
    assert "50,000" in res.stderr


def test_cli_phantom_guard_passes_when_dedicated_k1_field_matches(tmp_path):
    ws = _ws(tmp_path)
    _set_result_fields(ws, schedule_e_k1_income=200000)
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000"])
    assert res.returncode == 0, res.stderr
    assert "PTET Election Analysis" in res.stdout


def test_cli_phantom_guard_falls_back_to_line_8_for_legacy_result(tmp_path):
    # A legacy result.json without schedule_e_k1_income still guards against the
    # aggregate line 8 (250000 here), so a $200K claim passes as before.
    ws = _ws(tmp_path)
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000"])
    assert res.returncode == 0, res.stderr


def test_cli_phantom_guard_fails_closed_on_non_finite_k1_field(tmp_path):
    # Codex P2 (PR #50): a corrupt schedule_e_k1_income = Infinity must NOT pass
    # every claim (float('inf') > anything) — it FAILS CLOSED rather than
    # silently admitting a phantom K-1.
    ws = _ws(tmp_path)
    _set_result_fields(ws, schedule_e_k1_income=float("inf"))
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000"])
    assert res.returncode != 0
    assert "must be finite" in res.stderr


def test_cli_phantom_guard_present_but_invalid_does_not_fall_back(tmp_path):
    # A present-but-non-numeric dedicated field must fail closed, NOT degrade to
    # the looser line-8 aggregate (which would admit the claim).
    ws = _ws(tmp_path)
    _set_result_fields(ws, schedule_e_k1_income="not-a-number")
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000"])
    assert res.returncode != 0
    assert "must be a number" in res.stderr


def test_cli_phantom_guard_fails_closed_on_null_k1_field(tmp_path):
    # Codex P2 (PR #50, SHdpp): a PRESENT-but-null schedule_e_k1_income is a
    # malformed CURRENT result — it must FAIL CLOSED (the field key is present,
    # so the current schema applies), NOT be treated as an absent key that
    # degrades to the looser line-8 aggregate fallback (which would admit the
    # $200K claim against line_8=250000). Only a truly ABSENT key is legacy.
    ws = _ws(tmp_path)
    _set_result_fields(ws, schedule_e_k1_income=None)
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000"])
    assert res.returncode != 0
    assert "must be a number" in res.stderr


def test_cli_sums_repeated_same_state_income(tmp_path):
    # Two CA K-1 entities → CA PTET base is the sum (150k × 9.3% = $13,950).
    ws = _ws(tmp_path)
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=100000", "--income", "CA=50000"])
    assert res.returncode == 0, res.stderr
    assert "$13,950" in res.stdout


def test_cli_rejects_malformed_ptet_rate_state(tmp_path):
    ws = _ws(tmp_path)
    res = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000", "--ptet-rate", "California=0.05"])
    assert res.returncode == 1


def test_cli_rejects_year_mismatch(tmp_path):
    ws = _ws(tmp_path)
    res = _run_cli(["--workspace", str(ws), "--year", "2025", "--income", "CA=1"])
    assert res.returncode == 1
    assert "TY2026" in res.stderr or "recompute" in res.stderr


def test_cli_blocks_path_traversal(tmp_path):
    ws = _ws(tmp_path)
    res = _run_cli(["--workspace", str(ws), "--year", "2026; rm -rf /",
                    "--income", "CA=1"])
    assert res.returncode == 1


# --------------------------------------------------------------------------
# N4 — fetched PTET schedules (tax_law.ptet.<entity-state>), fetched-wins,
# loud-degrade fallback, corrupt hard-error, per-entity-state keying.
# --------------------------------------------------------------------------
def test_fetched_schedule_wins_over_builtin():
    """A fetched tax_law.ptet.CA schedule is used verbatim (10% here, not the
    built-in 9.3%), with NO unfetched-schedule warning."""
    cfg = {"CA": {"schedule": [[0, 0.10, 0]], "vintage": "test 10%"}}
    tax, _ = ptet.ptet_tax("CA", 200000, tax_year=2026, ptet_cfg=cfg)
    assert tax == 20000.0  # 10% of 200k, the fetched rate
    sched, warn = ptet._resolve_schedule("CA", 2026, cfg)
    assert warn is None


def test_absent_schedule_falls_back_with_loud_vintage_warning():
    """No fetched block → built-in CA 9.3% + a LOUD warning naming the vintage."""
    tax, _ = ptet.ptet_tax("CA", 200000, tax_year=2026, ptet_cfg=None)
    assert tax == 18600.0  # 9.3% built-in
    _, warn = ptet._resolve_schedule("CA", 2026, None)
    assert warn is not None
    assert "not fetched" in warn and "R&TC §19900" in warn


def test_nj_legacy_vintage_named_in_fallback_pre_2022():
    _, warn = ptet._resolve_schedule("NJ", 2021, None)
    assert "legacy" in warn.lower()
    _, warn2022 = ptet._resolve_schedule("NJ", 2022, None)
    assert "P.L. 2021 c.419" in warn2022


@pytest.mark.parametrize("bad", [
    {"CA": {"schedule": "notalist"}},          # schedule not a list
    {"CA": {"schedule": []}},                   # empty schedule
    {"CA": {"schedule": [[0, 1.5, 0]]}},        # rate > 1
    {"CA": {"schedule": [[0, float("nan"), 0]]}},  # NaN rate
    {"CA": {"schedule": [[100, 0.09, 0]]}},     # first bracket lower != 0
    {"CA": {"schedule": [[0, 0.05, 0], [0, 0.09, 0]]}},  # non-increasing lowers
    {"CA": {"schedule": [[0, 0.09]]}},          # wrong tuple arity
    {"CA": "notadict"},                          # block not a dict
])
def test_corrupt_fetched_schedule_hard_errors(bad):
    with pytest.raises(PtetError):
        ptet._resolve_schedule("CA", 2026, bad)


def test_per_entity_state_keying_tx_resident_ca_and_nj_k1s():
    """A fetched cfg carrying CA + NJ (entity states) prices each from its own
    fetched schedule; a state ABSENT from cfg (NY) falls back with a warning."""
    cfg = {
        "CA": {"schedule": [[0, 0.10, 0]]},          # fetched 10%
        "NJ": {"schedule": [[0, 0.06, 0], [1_000_000, 0.11, 60000]]},
    }
    ca, _ = ptet.ptet_tax("CA", 100000, tax_year=2026, ptet_cfg=cfg)
    nj, _ = ptet.ptet_tax("NJ", 100000, tax_year=2026, ptet_cfg=cfg)
    assert ca == 10000.0  # 10% fetched
    assert nj == 6000.0   # 6% fetched first bracket
    _, ca_warn = ptet._resolve_schedule("CA", 2026, cfg)
    _, ny_warn = ptet._resolve_schedule("NY", 2026, cfg)  # not in cfg
    assert ca_warn is None          # CA fetched → no warning
    assert ny_warn is not None      # NY absent → loud fallback


def test_fetched_schedule_case_insensitive_key():
    cfg = {"ca": {"schedule": [[0, 0.10, 0]]}}  # lowercase key
    sched, warn = ptet._resolve_schedule("CA", 2026, cfg)
    assert warn is None
    assert sched == [(0.0, 0.10, 0.0)]


def test_analyze_surfaces_unfetched_warning_in_caveats():
    """End-to-end: analyze with no ptet_cfg attaches the loud unfetched-schedule
    warning to the state's caveats; a rate override suppresses it."""
    report = analyze(
        baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
        state_incomes={"CA": 100000}, tax_year=2026, filing_status="MFJ",
        ptet_cfg=None)
    ca = next(s for s in report.states if s.state == "CA")
    assert any("not fetched" in c for c in ca.caveats)
    # override bypasses the schedule → no unfetched warning
    report2 = analyze(
        baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
        state_incomes={"CA": 100000}, tax_year=2026, filing_status="MFJ",
        rate_overrides={"CA": 0.10}, ptet_cfg=None)
    ca2 = next(s for s in report2.states if s.state == "CA")
    assert not any("not fetched" in c for c in ca2.caveats)
