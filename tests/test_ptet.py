"""Tests for the PTET election analyzer — agents/lib/ptet.py (Backlog #9).

Expected dollar values are hand-computed from the statutory PTET rate schedules
and a deterministic synthetic baseline (MFJ brackets: 10% to 20k, 20% to 200k,
30% above; standard deduction 30k). Update the comments if you change them.
"""
import json
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
RATE_KW = dict(brackets=MFJ_BRACKETS, deduction=30000,
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


def test_analyze_auto_estimated_marginal_carries_agi_cliff_caveat():
    # O3: when the SALT-offset marginal rate is auto-estimated from the small
    # probe, the report must surface the AGI-cliff caveat pointing at
    # --marginal-fed; an EXPLICIT marginal rate must NOT emit that caveat.
    r_auto = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                     state_incomes={"CA": 200000}, tax_year=2026,
                     filing_status="MFJ", salt_room=5000)
    assert any("AUTO-ESTIMATED" in n and "--marginal-fed" in n for n in r_auto.notes)

    r_explicit = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                         state_incomes={"CA": 200000}, tax_year=2026,
                         filing_status="MFJ", salt_room=5000, marginal_fed=0.30)
    assert not any("AUTO-ESTIMATED" in n for n in r_explicit.notes)


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


def test_the_salt_schedule_is_the_one_the_statute_states():
    """§164(b)(7)(A), every arm, against the section text (verified 2026-09-13).

    This asserted a FLAT $40,000 across TY2025-2029 and so agreed with a
    `_salt_cap` that returned one — while (A)(ii) names $40,400 for TY2026 and
    (A)(iii) steps 101% a year after it. Under-stating the cap over-states the
    benefit of the PTET election this whole module prices, and the skill text
    repeated the flat figure to taxpayers (X6). Q10, ruled 2026-09-13: the
    anchors are statutory and may be constants, pinned HERE.
    """
    assert ptet._salt_cap(2025, "MFJ") == 40000.0          # (A)(i)
    assert ptet._salt_cap(2026, "MFJ") == 40400.0          # (A)(ii)
    assert ptet._salt_cap(2027, "MFJ") == 40804.0          # (A)(iii), 101%
    assert ptet._salt_cap(2028, "MFJ") == 41212.04
    assert ptet._salt_cap(2029, "MFJ") == 41624.16
    assert ptet._salt_cap(2030, "MFJ") == 10000.0          # (A)(iv)
    assert ptet._salt_cap(2024, "MFJ") == 10000.0          # pre-OBBBA §164(b)(6)


def test_the_cap_label_reads_the_rules_own_window(monkeypatch):
    """The label kept its own `2025 <= year <= 2029` after AA16 moved the window
    into `federal/salt.py`, so a change there would leave it naming the old
    regime. Moving the rule's window moves the label."""
    from agents.lib.tax_computations.federal import salt
    assert ptet._salt_cap_label(2029) == "OBBBA TY2025-2029"
    assert ptet._salt_cap_label(2030) == "statutory $10K cap"
    monkeypatch.setattr(salt, "SALT_LAST_OBBBA_YEAR", 2030)
    assert ptet._salt_cap_label(2030) == "OBBBA TY2025-2030"


def test_mfs_is_half_the_limitation_amount_not_a_second_schedule():
    """§164(b)(6)(B): "half the applicable limitation amount in the case of a
    married individual filing a separate return". It was a second hardcoded
    pair of figures, which is two things to keep in step where the statute has
    one rule — and the pair was already out of step for TY2026."""
    for year in (2024, 2025, 2026, 2027, 2028, 2029, 2030):
        joint = ptet._salt_cap(year, "MFJ")
        assert ptet._salt_cap(year, "MFS") == round(joint / 2, 2), year


def test_salt_cap_prefers_tax_law_cache():
    """A fetched federal.salt_cap block wins over the built-in schedule — the
    cache tracks the indexed 2026-2029 amounts the schedule cannot."""
    cfg = {"amount": 40400, "mfs_amount": 20200}
    assert ptet._salt_cap(2026, "MFJ", cfg) == 40400.0
    assert ptet._salt_cap(2026, "MFS", cfg) == 20200.0


def test_salt_cap_cache_partial_falls_back():
    """Unfetched (None) cache values fall back to the statutory schedule."""
    assert ptet._salt_cap(2026, "MFJ", {"amount": None, "mfs_amount": None}) == 40400.0
    assert ptet._salt_cap(2026, "MFS", {"amount": 40400}) == 20200.0
    assert ptet._salt_cap(2026, "MFJ", None) == 40400.0


def test_salt_cap_cache_invalid_value_hard_errors():
    """A present-but-corrupt cache value must not silently price the wrong cap."""
    with pytest.raises(PtetError):
        ptet._salt_cap(2026, "MFJ", {"amount": float("nan")})
    with pytest.raises(PtetError):
        ptet._salt_cap(2026, "MFJ", {"amount": "forty thousand"})
    with pytest.raises(PtetError):
        ptet._salt_cap(2026, "MFJ", {"amount": -1})


def test_analyze_other_salt_derives_headroom_from_cap():
    # TY2026's cap is §164(b)(7)(A)(ii)'s $40,400, not the flat $40,000 this
    # said when the schedule was wrong. With $25k of other SALT, $15,400 of
    # headroom was deductible anyway → offset 30% × min(15400, 18600) = 4,620;
    # net 5,580 − 4,620. MAGI $400k is under TY2026's $505,000 threshold, so
    # (B) does not bite and the cap is the full amount.
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                other_salt=25000, modified_agi=400000, marginal_fed=0.30)
    assert r.salt_room == 15400.0
    assert r.states[0].lost_salt_offset == 4620.0
    assert r.states[0].net_fed_savings == 960.0


def test_analyze_other_salt_zero_headroom_when_salt_exceeds_cap():
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
                other_salt=50000, modified_agi=400000, marginal_fed=0.30)
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
    assert any("$40,400 SALT cap" in n for n in r.notes)
    # No --other-salt here, so nothing was derived from the cap and there is
    # nothing for a phasedown banner to warn about.
    assert not any("phasedown was NOT applied" in n for n in r.notes), r.notes


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


def test_cli_reports_a_missing_result_json_as_an_error_not_a_traceback(tmp_path):
    """Running /tax-ptet before /tax-compute is the most ordinary way to get
    this wrong, and it went through `_read_json` — which is projection's, and
    raises `ProjectionError`, a type `_main`'s except tuple did not list. The
    CLI exited with a raw Python traceback instead of the `Error: …` line the
    skill instructs the agent to read, so the agent saw a crash rather than a
    prerequisite it could satisfy.

    Mutation-verified: dropping ProjectionError from the tuple fails this.
    """
    res = _run_cli(["--workspace", str(tmp_path)])
    assert res.returncode == 1, res.stdout + res.stderr
    assert "Traceback" not in res.stderr, res.stderr
    assert res.stderr.startswith("Error: "), res.stderr
    assert "result.json" in res.stderr


def test_cli_reports_a_missing_tax_law_cache_the_same_way(tmp_path):
    """The second file the same read path opens — same handler, and the one a
    user hits after /tax-compute has run but the cache was never fetched."""
    ws = _ws(tmp_path)
    (ws / "tax_law_2026.json").unlink()
    res = _run_cli(["--workspace", str(ws), "--year", "2026"])
    assert res.returncode == 1, res.stdout + res.stderr
    assert "Traceback" not in res.stderr, res.stderr
    assert res.stderr.startswith("Error: ")


def test_cli_reports_missing_cg_limits_as_an_error_not_a_traceback(tmp_path):
    """X2: the fixture return has qualified dividends and a LTCG, and the
    keystone now refuses to price them without the 0%/15% breakpoints. That
    refusal is a ValueError, a type this CLI's except tuple did not list —
    the same shape as the ProjectionError gap above."""
    ws = _ws(tmp_path)
    law_path = ws / "tax_law_2026.json"
    law = json.loads(law_path.read_text())
    del law["federal"]["capital_gains_rates"]
    law_path.write_text(json.dumps(law))
    res = _run_cli(["--workspace", str(ws), "--year", "2026", "--income", "CA=200000"])
    assert res.returncode == 1, res.stdout + res.stderr
    assert "Traceback" not in res.stderr, res.stderr
    assert res.stderr.startswith("Error: ") and "tax-setup" in res.stderr


def test_obs_date_honours_the_conforming_holiday_set():
    """`_obs_date` delegates, and the delegation is the point.

    It used to hold a FOURTH inlined copy of the weekend-only roll, which is
    how a rule wrong for California came to be repeated in four places. It now
    calls `irs_dates.next_business_day_state` with the state it is dating, so
    CA gets the federal holiday set (R&TC §18410) and NY/NJ keep the
    weekend-only degrade.

    Asserted on January 15, a date NO PTET deadline falls on. That is
    deliberate and is stated so a reader does not "simplify" it away: the real
    PTET deadlines are March 15 and June 15, and neither lands on a federal
    holiday in any year from 2020 to 2040 — so no PTET date can distinguish
    the two rules today, and a test built from one would pass just as happily
    on the inlined copy. This asserts the CONTRACT rather than a coincidence,
    which is what will still be right if a deadline moves or a second state is
    added to the conforming set.
    """
    assert ptet._obs_date(2024, 1, 15, "CA") == "January 16, 2024", (
        "January 15 2024 was MLK Day; CA adopts the federal set")
    assert ptet._obs_date(2024, 1, 15, "NY") == "January 15, 2024", (
        "NY's statute is unread here, so it keeps the weekend-only degrade")

    # The real deadlines still render, and still agree with tax_calendar.
    assert ptet._obs_date(2026, 3, 15, "NY") == "March 16, 2026", "Sunday"
    assert ptet._obs_date(2026, 6, 15, "CA") == "June 15, 2026", "Monday"


def test_the_magi_phasedown_is_modeled_not_caveated():
    """§164(b)(7)(B), against the section text (verified 2026-09-13).

    Not modeling it OVER-stated the cap for exactly this tool's population —
    pass-through owners are frequently over the threshold. The benefit moves
    the OTHER way from the cap, and the direction has to be run rather than
    reasoned: a higher cap leaves the filer more apparent SALT headroom, so
    shifting the tax to the entity buys less — asserted below rather than
    argued, because this is the claim that came out backwards. An over-stated
    cap therefore UNDER-states the benefit, the direction AGENTS.md §2 as
    amended by Q8 lets an ADVISORY output degrade with a banner — the degrade
    path was open here and a caveat would have been allowed.

    It is modeled anyway, because §164(b)(7)(B) names the rate, both
    thresholds and the floor: there is nothing left to approximate, and a
    banner saying the number may be low is a worse answer than the number.
    The claim in the other direction stood in this docstring for a round; it
    is recorded because it is the one that would have made a caveat look
    forbidden, and §2 turns on the direction being right.
    """
    # TY2025: threshold $500,000, 30% of the excess, floor $10,000.
    assert ptet._salt_cap(2025, "MFJ", None, 500000.0) == 40000.0
    assert ptet._salt_cap(2025, "MFJ", None, 550000.0) == 25000.0
    # The OBBBA increase is fully spent at threshold + $100,000...
    assert ptet._salt_cap(2025, "MFJ", None, 600000.0) == 10000.0
    # ...and the floor holds however high MAGI goes.
    assert ptet._salt_cap(2025, "MFJ", None, 5000000.0) == 10000.0
    # TY2026's threshold is $505,000, not TY2025's — the note used to quote
    # $500,000 for every year in the range.
    assert ptet._salt_cap(2026, "MFJ", None, 505000.0) == 40400.0
    assert ptet._salt_cap(2026, "MFJ", None, 555000.0) == 25400.0

    # The direction itself, run rather than reasoned. The benefit is
    # NON-INCREASING in the cap, so over-stating the cap can only under-state
    # it — and the spread is the whole recommendation, not a rounding.
    savings = [
        analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2025,
                filing_status="MFJ", other_salt=25000, marginal_fed=0.30,
                salt_cap_cfg={"amount": cap, "mfs_amount": cap / 2},
                ).total_net_fed_savings
        for cap in (10000.0, 25000.0, 40000.0, 60000.0)]
    assert savings == sorted(savings, reverse=True), savings
    assert savings[0] > savings[-1], savings
    assert savings[0] == 5580.0 and savings[-1] == 0.0, savings


def test_the_phasedown_threshold_halves_for_a_separate_return():
    """§164(b)(7)(B)(i) halves the THRESHOLD in its own words, and
    §164(b)(6)(B) halves the resulting amount. Both apply: an MFS filer's
    reduction starts at $250,000 of MAGI for TY2025, and the $10,000 floor
    reaches them as $5,000."""
    assert ptet._salt_cap(2025, "MFS", None, 250000.0) == 20000.0
    assert ptet._salt_cap(2025, "MFS", None, 300000.0) == 12500.0
    assert ptet._salt_cap(2025, "MFS", None, 400000.0) == 5000.0


def test_deriving_headroom_without_magi_degrades_with_the_direction_named():
    """AGENTS.md §2 as amended by Q8 allows a degrade where the output is
    ADVISORY and the fallback UNDER-states — and this is that case, which took
    a review to establish. An un-reduced cap over-states HEADROOM, and more
    headroom SHRINKS the modeled benefit, because what a PTET election rescues
    is the state tax the cap blocks. So omitting the phasedown errs toward not
    electing. The first draft made it a STOP, which is stricter than the rule
    it was written to implement.
    """
    r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                state_incomes={"CA": 200000}, tax_year=2026,
                filing_status="MFJ", other_salt=25000, marginal_fed=0.30)
    banner = [n for n in r.notes if "phasedown was NOT applied" in n]
    assert len(banner) == 1, r.notes
    assert "CEILING" in banner[0] and "FLOOR" in banner[0], banner
    assert "$505,000" in banner[0], banner


def test_the_phasedown_banner_does_not_fire_where_it_cannot_matter():
    """Cry-wolf, on three paths where the missing MAGI cannot move the answer.

    A banner on any of them is noise about a figure that changes nothing, which
    is how an agent learns to read past the one that is real. All three were
    reproduced against a first draft that refused outright.
    """
    def notes_for(**over):
        kw = dict(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                  state_incomes={"CA": 200000}, tax_year=2026,
                  filing_status="MFJ", other_salt=25000, marginal_fed=0.30)
        kw.update(over)
        return analyze(**kw).notes

    for label, over in (
        ("a pre-OBBBA year, where the cap IS the floor", {"tax_year": 2024}),
        ("a post-reversion year, same reason", {"tax_year": 2030}),
        ("a standard-deduction filer, who gets no SALT offset at all",
         {"itemizes": False}),
    ):
        assert not any("phasedown was NOT applied" in n for n in notes_for(**over)), (
            f"the phasedown banner fired for {label}")


def test_the_mfs_reading_rests_on_the_text_because_the_bias_runs_the_other_way():
    """§164(b)(7)(B) admits two readings and they differ FOURFOLD.

    Taken: (B)(i) halves the threshold, (B)(iii) floors at $10,000, then
    §164(b)(6)(B) halves the result. The alternative computes the whole of (7)
    on a joint basis and halves only at the end.

    Pinned because the divergence is large, real and silent — both readings
    return a plausible number, and nothing downstream compares them. What
    decides it is the text: this is the only reading under which (B)(i)'s
    explicit MFS threshold does anything at all.

    That is the WHOLE argument, and it has to be, because the bias runs
    against the reading rather than behind it. The benefit moves opposite to
    the cap, so the taken reading's $5,000 advertises a LARGER PTET benefit
    than the alternative's $20,000 at the same MAGI. This docstring claimed
    the reverse — that the taken reading "cannot over-state" — which would
    have let a textual near-miss pass as the safe choice. It is the one that
    needs the statute to be read correctly, so the citation is load-bearing.
    """
    def joint_basis_reading(magi):
        amount = max(10000.0, 40000.0 - 0.30 * max(0.0, magi - 500000.0))
        return round(amount / 2, 2)

    # The band where the two readings disagree, and by how much.
    assert ptet._salt_cap(2025, "MFS", None, 350000.0) == 5000.0
    assert joint_basis_reading(350000.0) == 20000.0

    # An MFS filer is exactly half a joint filer at the same distance above
    # their own threshold — which is what makes the taken reading coherent.
    for excess in (0.0, 50000.0, 100000.0, 250000.0):
        joint = ptet._salt_cap(2025, "MFJ", None, 500000.0 + excess)
        separate = ptet._salt_cap(2025, "MFS", None, 250000.0 + excess)
        assert separate == round(joint / 2, 2), excess


def test_a_fetched_cap_is_a_BASE_and_still_takes_the_phasedown():
    """The cache carries the YEAR's statutory amount, the same for every
    taxpayer; §164(b)(7)(B) reduces it per RETURN. Applying the phasedown only
    on the fallback path made a populated cache the one way to get the
    unreduced cap — so fetching the figure the cache exists for made the answer
    worse, and silently."""
    magi = 600000.0
    assert ptet._salt_cap(2026, "MFJ", {"amount": 40400}, magi) == \
        ptet._salt_cap(2026, "MFJ", None, magi) == 11900.0

    # ...and a cached `mfs_amount` is already a separate return's figure, so it
    # must not be halved a second time.
    assert ptet._salt_cap(2026, "MFS", {"mfs_amount": 20200}, None) == 20200.0
    assert ptet._salt_cap(2026, "MFS", {"mfs_amount": 20200}, 300000.0) == \
        ptet._salt_cap(2026, "MFS", None, 300000.0)


def test_a_negative_magi_is_a_real_return_and_not_a_refusal():
    """MAGI is AGI plus the §911/§931/§933 exclusions (§164(b)(7)(B)(iv)) and
    can be negative — a loss year. The validator rejected it, which refused the
    taxpayer furthest from the phasedown."""
    assert ptet._salt_cap(2026, "MFJ", None, -1000.0) == 40400.0
    assert ptet._salt_cap(2026, "MFJ", None, 0.0) == 40400.0
    for bad in (float("nan"), float("inf"), "40000", True, None):
        if bad is None:
            continue                      # None means "not supplied"
        with pytest.raises(PtetError):
            ptet._salt_cap(2026, "MFJ", None, bad)


def test_magi_is_validated_outside_the_phasedown_years_too():
    """The return's Schedule A validates MAGI in every year, and this validated
    it only inside TY2025-2029: `--year 2030 --magi nan` ran clean here while
    the same figure refused there. The rule ignores a MAGI outside those years,
    so a valid one still changes nothing."""
    for year in (2024, 2030):
        with pytest.raises(PtetError, match="modified_agi must be finite"):
            ptet._salt_cap(year, "MFJ", None, float("nan"))
        assert ptet._salt_cap(year, "MFJ", None, 900000.0) == 10000.0


def test_the_applied_note_says_the_election_lowers_magi():
    """The cap is priced at the MAGI WITHOUT the election, and electing takes
    the PTET out of the owner's income. A lower MAGI can only raise the cap, so
    wherever the phasedown bites the modeled benefit is a floor, and the note
    says so; where it does not bite there is nothing to raise."""
    def notes_at(magi):
        return analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                       state_incomes={"CA": 200000}, tax_year=2026,
                       filing_status="MFJ", other_salt=25000,
                       modified_agi=magi, marginal_fed=0.30).notes
    applied = [n for n in notes_at(600000.0) if "APPLIED" in n]
    assert len(applied) == 1, applied
    assert "modeled benefit is a FLOOR" in applied[0], applied
    below = notes_at(400000.0)
    assert not any("FLOOR" in n for n in below), below


def test_the_applied_note_claims_a_floor_only_where_headroom_was_derived():
    """The election's own MAGI cut can only add to the benefit, but the
    benefit is a floor only where the headroom came from the cap. Assumed
    zero, the default, it may be too high, and the note called it a floor
    beside the ASSUMPTION note saying so."""
    for kwargs in ({}, {"salt_room": 5000}):
        notes = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                        state_incomes={"CA": 200000}, tax_year=2026,
                        filing_status="MFJ", modified_agi=600000.0,
                        marginal_fed=0.30, **kwargs).notes
        applied = [n for n in notes if "APPLIED" in n]
        assert len(applied) == 1, notes
        assert "FLOOR" not in applied[0], (kwargs, applied)
        assert "the benefit is not a floor" in applied[0], (kwargs, applied)


def test_the_applied_note_prints_the_cut_to_the_cent():
    """A dollar over the threshold cuts thirty cents, and whole dollars
    printed "cut the cap by $0, to $40,000"."""
    notes = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                    state_incomes={"CA": 200000}, tax_year=2025,
                    filing_status="MFJ", other_salt=20000,
                    modified_agi=500001.0, marginal_fed=0.30).notes
    applied = [n for n in notes if "APPLIED" in n]
    assert len(applied) == 1, notes
    assert "cut the cap by $0.30, to $39,999.70" in applied[0], applied


def test_a_salt_room_above_the_whole_cap_refuses():
    """Unused headroom is part of the cap. $30,000 of it beside an $11,900
    cap printed as modeled headroom and priced the benefit on it."""
    def run(room, magi):
        return analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                       state_incomes={"CA": 200000}, tax_year=2026,
                       filing_status="MFJ", salt_room=room,
                       modified_agi=magi, marginal_fed=0.30)
    with pytest.raises(PtetError, match="more than the whole SALT cap"):
        run(30000, 600000.0)
    assert run(11900, 600000.0).salt_room == 11900.0   # the whole cap stands
    assert run(30000, None).salt_room == 30000.0       # under the unreduced cap
    # A standard-deduction filer's headroom prices nothing, so it is not checked.
    analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
            state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ",
            salt_room=30000, modified_agi=600000.0, itemizes=False,
            marginal_fed=0.30)


def test_the_return_supplies_magi_and_the_itemize_choice():
    """/tax-review passed these, while /tax-ptet's own procedure and
    /tax-project's call did not, so one election was priced against two caps
    depending on which skill ran it. A flag given still wins; a MISSING
    section is not read as the standard deduction, since dropping the SALT
    offset over-states the benefit; a corrupt one refuses."""
    position = ptet._return_salt_position
    sched = {"schedule_a": {"modified_agi": 600000, "use_itemized": True}}
    assert position(sched, None, False) == (600000.0, True)
    assert position(sched, 400000.0, False) == (400000.0, True)
    assert position(sched, None, True) == (600000.0, False)
    standard = {"schedule_a": {"modified_agi": None, "use_itemized": False}}
    assert position(standard, None, False) == (None, False)
    assert position({}, None, False) == (None, True)
    assert position({"schedule_a": None}, None, False) == (None, True)
    with pytest.raises(PtetError, match="use_itemized must be true or false"):
        position({"schedule_a": {"use_itemized": "yes"}}, None, False)
    with pytest.raises(PtetError, match="schedule_a.modified_agi must be"):
        position({"schedule_a": {"modified_agi": "600000"}}, None, False)
    # A positive line 13c is the standard deduction, section or no section:
    # a result.json from before AA23's in-engine election came from a by-hand
    # §170(p) tilt that could take a return there after `use_itemized` was
    # recorded.
    donor = {"form_1040": {"line_13c_charitable_non_itemizer": 1000},
             "schedule_a": {"modified_agi": 600000, "use_itemized": True}}
    assert position(donor, None, False) == (600000.0, False)
    assert position({"form_1040": {"line_13c_charitable_non_itemizer": 1000}},
                    None, False) == (None, False)
    assert position({"form_1040": {"line_13c_charitable_non_itemizer": 0}},
                    None, False) == (None, True)


def test_the_phasedown_note_never_puts_magi_under_a_threshold_it_is_over():
    """With nothing cut, the note said MAGI was "at or under" the threshold —
    at $600,000 against a cap already cached at its floor, and a cent over
    the threshold, where the cut rounds to nothing."""
    def notes(magi, cfg=None):
        return analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                       state_incomes={"CA": 200000}, tax_year=2026,
                       filing_status="MFJ", other_salt=5000,
                       modified_agi=magi, salt_cap_cfg=cfg,
                       marginal_fed=0.30).notes
    for magi, cfg in ((600000.0, {"amount": 10000}), (505000.01, None)):
        found = [n for n in notes(magi, cfg) if "phasedown" in n]
        assert len(found) == 1, (magi, found)
        assert "leaves the cap unchanged" in found[0], (magi, found)
        assert "at or under" not in found[0], (magi, found)
    assert any("does not bite" in n for n in notes(505000.0))


def test_cli_prices_the_cap_the_return_applied(tmp_path):
    ws = _ws(tmp_path)
    res = json.loads((ws / "result.json").read_text())
    res["schedule_a"] = {"modified_agi": 600000, "use_itemized": True}
    (ws / "result.json").write_text(json.dumps(res))
    out = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000", "--other-salt", "5000"])
    assert out.returncode == 0, out.stderr
    assert "cut the cap by $28,500.00, to $11,900.00" in out.stdout


def test_the_render_intro_states_no_flat_cap():
    """The intro told every taxpayer PTET dodges "the $10,000 federal
    itemized-deduction cap": the pre-OBBBA figure, printed on TY2025-2029
    analyses whose cap is four times that before any phasedown."""
    md = render_markdown(analyze(
        baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
        state_incomes={"CA": 200000}, tax_year=2026, filing_status="MFJ"))
    assert "dodges the federal SALT itemized-deduction cap" in md
    assert "$10,000 federal" not in md


def test_the_applied_banner_compares_like_with_like_for_MFS():
    """The note computed its reduction against the UNHALVED statutory base, so
    every MFS return read as a phasedown of half its cap — an ordinary
    §164(b)(6)(B) halving misreported as a MAGI reduction, on returns whose
    MAGI was nowhere near the threshold. It also promised a $10,000 floor to a
    filer whose floor is $5,000."""
    below = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                    state_incomes={"CA": 200000}, tax_year=2026,
                    filing_status="MFS", other_salt=5000,
                    modified_agi=200000.0, marginal_fed=0.30)
    assert any("does not bite" in n for n in below.notes), below.notes
    assert not any("APPLIED" in n for n in below.notes), below.notes

    above = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                    state_incomes={"CA": 200000}, tax_year=2026,
                    filing_status="MFS", other_salt=5000,
                    modified_agi=300000.0, marginal_fed=0.30)
    applied = [n for n in above.notes if "APPLIED" in n]
    assert len(applied) == 1, above.notes
    assert "$5,000" in applied[0], applied      # the MFS floor, not $10,000


def test_the_degrade_banner_is_silent_where_no_magi_could_change_it():
    """Cry-wolf, the fourth condition — found by a review of the MERGED change.

    The phasedown can only LOWER the cap, so where other state and local taxes
    already exhaust the UN-reduced one there is no headroom at any MAGI: the
    benefit is identical whether it is supplied or not. Asking for it there is
    noise about a figure that cannot move the answer.
    """
    def run(**over):
        kw = dict(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                  state_incomes={"CA": 200000}, tax_year=2026,
                  filing_status="MFJ", marginal_fed=0.30)
        kw.update(over)
        return analyze(**kw)

    exhausted = run(other_salt=50000)          # > the $40,400 TY2026 cap
    assert exhausted.salt_room == 0.0
    assert not any("phasedown was NOT applied" in n for n in exhausted.notes), \
        exhausted.notes
    # ...and it genuinely could not have mattered.
    assert run(other_salt=50000, modified_agi=600000.0).total_net_fed_savings \
        == exhausted.total_net_fed_savings

    # Where there IS headroom the banner is owed, because the true cap is lower.
    present = run(other_salt=25000)
    assert present.salt_room > 0
    assert any("phasedown was NOT applied" in n for n in present.notes)


def test_the_degrade_banner_quotes_the_rate_it_would_apply():
    """A separate return is reduced at 15% — §164(b)(6)(B)'s halving
    distributed through the reduction — and the banner said a flat 30%, so it
    overstated an MFS filer's exposure twofold. A caveat that misreports the
    rule it is warning about is the lying kind."""
    def banner(status):
        r = analyze(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                    state_incomes={"CA": 200000}, tax_year=2026,
                    filing_status=status, other_salt=5000, marginal_fed=0.30)
        return next(n for n in r.notes if "phasedown was NOT applied" in n)

    assert "30% of the excess" in banner("MFJ")
    mfs = banner("MFS")
    assert "15% of the excess" in mfs, mfs
    # ...and the figure the banner implies is the one the engine would produce.
    assert ptet._salt_cap(2026, "MFS", None, 300000.0) == round(
        ptet._salt_cap(2026, "MFS") - 0.15 * (300000.0 - 252500.0), 2)


def test_the_phasedown_floor_never_RAISES_a_cap():
    """§164(b)(7)(B)(iii) floors the RESULT OF THE REDUCTION, and a floor on a
    reduction cannot raise the thing it is reducing.

    Written as a bare `max(floor, ...)` it did: a cache carrying a cap BELOW
    $10,000 came back raised to it the moment a MAGI was supplied, so passing
    `--magi` INCREASED the modeled headroom. Reproduced by a review of the
    review-fix commit.
    """
    low = {"amount": 5000}
    assert ptet._salt_cap(2026, "MFJ", low) == 5000.0
    for magi in (0.0, 300000.0, 600000.0, 5000000.0):
        assert ptet._salt_cap(2026, "MFJ", low, magi) == 5000.0, magi
    # The floor still binds when the base is ABOVE it.
    assert ptet._salt_cap(2026, "MFJ", None, 5000000.0) == 10000.0
    assert ptet._salt_cap(2026, "MFS", None, 5000000.0) == 5000.0


def test_the_banner_reads_the_CREDITED_amount_not_the_headroom():
    """The fifth condition on this banner, and the fourth was still the wrong
    question. What a PTET election rescues is `min(salt_room, total_ptet)`, so
    headroom that is large at BOTH ends of the range a missing MAGI spans
    changes nothing — even though the headroom itself changes a lot."""
    def fired(**over):
        kw = dict(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                  state_incomes={"CA": 200000}, tax_year=2026,
                  filing_status="MFJ", marginal_fed=0.30)
        kw.update(over)
        r = analyze(**kw)
        return any("phasedown was NOT applied" in n for n in r.notes), r

    # Headroom swings from $39,400 to $9,000 across the range, and the PTET is
    # only $4,650 — so the credited amount is the PTET at both ends.
    quiet, r = fired(state_incomes={"CA": 50000}, other_salt=1000)
    assert not quiet, r.notes
    assert r.total_net_fed_savings == analyze(
        baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
        state_incomes={"CA": 50000}, tax_year=2026, filing_status="MFJ",
        other_salt=1000, modified_agi=5000000.0,
        marginal_fed=0.30).total_net_fed_savings

    # ...and where it DOES move the credited amount, the banner is owed.
    loud, _ = fired(other_salt=25000)
    assert loud


def test_the_reported_cap_is_qualified_even_when_the_benefit_is_not():
    """The cap is printed to the taxpayer whether or not the BENEFIT depends on
    a MAGI, and a review found the un-reduced figure presented flatly as "your
    $40,400 SALT cap" on a return whose real cap could be $10,000. The benefit
    banner is gated on the benefit and must stay that way, so the FIGURE
    carries its own qualification."""
    def cap_note(**over):
        kw = dict(baseline_inputs=BASELINE, rate_kwargs=RATE_KW,
                  state_incomes={"CA": 200000}, tax_year=2026,
                  filing_status="MFJ", other_salt=50000, marginal_fed=0.30)
        kw.update(over)
        return next(n for n in analyze(**kw).notes if "SALT cap" in n)

    unqualified = cap_note()
    assert "$40,400" in unqualified
    assert "BEFORE §164(b)(7)(B)" in unqualified, unqualified
    # Supplied, or already at the floor: nothing left to qualify.
    settled = cap_note(modified_agi=1000000.0)
    assert "$10,000" in settled and "BEFORE §164(b)(7)(B)" not in settled


def test_cli_prices_from_the_returns_line_12(tmp_path):
    """AA20. $18,600 of CA PTET on the fixture, whose $15,000 of qualified
    dividends and long-term gain stacks above ordinary income, plus $850 of
    child credit the lower AGI stops phasing out:
    - line 12 $30,000: ordinary taxable income $372,000, all 30%: $5,580, so
      $6,430;
    - line 12 $200,000: $202,000 to $183,400, $2,000 at 30% and $16,600 at
      20%: $3,920, so $4,770.
    Priced on the cache's standard deduction, that itemizer's saving read
    $1,660 high."""
    def savings(line_12):
        ws = tmp_path / str(line_12)
        ws.mkdir()
        _ws(ws)
        res = json.loads((ws / "result.json").read_text())
        res["form_1040"]["line_12_standard_deduction"] = line_12
        (ws / "result.json").write_text(json.dumps(res))
        out = _run_cli(["--workspace", str(ws), "--year", "2026",
                        "--income", "CA=200000"])
        assert out.returncode == 0, out.stderr
        return out.stdout
    assert "Total net federal savings: $6,430" in savings(30000)
    assert "Total net federal savings: $4,770" in savings(200000)


def test_the_medical_note_says_the_election_moves_the_floor(tmp_path):
    """The election lowers AGI by the PTET, and the medical floor with it;
    the keystone holds the deduction. $40,000 of expenses over a floor of
    $31,275 at $417,000 is $8,725, and $10,120 after $18,600 of PTET. The
    note is the shared sentence, "If you itemize" and all, and a missing
    line 11 is measured from the keystone's AGI rather than skipped."""
    note = ptet._medical_floor_note
    ret = {"form_1040": {"line_11_agi": 417000},
           "schedule_a": {"use_itemized": True, "medical_expenses": 40000}}
    said = note(ret, True, 417000.0, 18600.0)
    assert said and said.startswith("If you itemize, your medical-expense")
    assert note(ret, False, 417000.0, 18600.0) is None     # standard deduction
    assert note(ret, True, 417000.0, 0.0) is None          # nothing elected
    under = {"form_1040": {"line_11_agi": 417000},
             "schedule_a": {"use_itemized": True, "medical_expenses": 1000}}
    assert note(under, True, 417000.0, 18600.0) is None    # under the floor throughout
    assert note({"schedule_a": ret["schedule_a"]}, True, 417000.0, 18600.0)
    ws = _ws(tmp_path)
    res = json.loads((ws / "result.json").read_text())
    res["schedule_a"] = ret["schedule_a"]
    (ws / "result.json").write_text(json.dumps(res))
    out = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000"])
    assert out.returncode == 0, out.stderr
    assert "If you itemize, your medical-expense deduction is held" in out.stdout


def test_cli_refuses_a_capped_charitable_deduction(tmp_path):
    """The election lowers AGI by the PTET, which lowers a binding charitable
    cap while the keystone holds the deduction: the benefit would read high.
    With no income to elect on, AGI does not move and nothing refuses."""
    ws = _ws(tmp_path)
    res = json.loads((ws / "result.json").read_text())
    res["schedule_a"] = {"use_itemized": True, "charitable_carryforward": 5000}
    (ws / "result.json").write_text(json.dumps(res))
    out = _run_cli(["--workspace", str(ws), "--year", "2026",
                    "--income", "CA=200000"])
    assert out.returncode == 1
    assert "capped at a share of AGI" in out.stderr
    assert _run_cli(["--workspace", str(ws), "--year", "2026"]).returncode == 0
