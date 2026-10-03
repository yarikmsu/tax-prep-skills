"""Tests for agents/lib/retirement_dashboard.py — /tax-retirement.

Read-only mid-year planning lib. Reads YTD contribution figures from
projection_inputs.json + age flags from taxpayer_profile.json + statutory
limits from tax_law_<year>.json, and reports remaining capacity per account
type plus marginal tax savings per remaining dollar.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import retirement_dashboard as rd
from agents.lib.tax_law_fetcher import create_retirement_conformity_template


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _tax_law_2026():
    """Skeleton tax_law matching tax_law_fetcher.create_empty_tax_law +
    prepare_tax_law_for_save output: top-level `tax_year` (from
    create_empty_tax_law line 123) plus `_meta.tax_year` (from
    prepare_tax_law_for_save line 88-89). Both fields should agree on a
    real cached file."""
    return {
        "tax_year": 2026,
        "_meta": {"tax_year": 2026, "fetched": "2026-01-01"},
        # Synthetic account treatments exercise pricing, not current tax law.
        "retirement_conformity": {code: dict(tax_year=2026,
            employee_401k=code != "PA", traditional_ira=code not in {"PA", "NJ", "MA"},
            hsa=code not in {"CA", "NJ"}) for code in create_retirement_conformity_template()},
        "_sources": {f"retirement_conformity.{code}": "https://www.irs.gov/"
                     for code in create_retirement_conformity_template()},
        "federal": {
            "retirement_401k": {
                "elective_deferral": 23500,
                "secure_2_0_high_earner_threshold": 150000,
                "catch_up_50_plus": 7500,
                "catch_up_60_63": 11250,
                "total_annual_limit": 70000,
            },
            "ira_limits": {
                "contribution_limit": 7000,
                "catch_up_50_plus": 1000,
                "traditional_deduction_phaseout": {
                    "mfj_covered": {"start": 126000, "end": 146000},
                    "mfj_spouse_not_covered": {"start": 236000, "end": 246000},
                    "single_covered": {"start": 79000, "end": 89000},
                },
                "roth_income_phaseout": {
                    "mfj": {"start": 236000, "end": 246000},
                    "single": {"start": 150000, "end": 165000},
                },
            },
            "hsa_limits": {
                "self_only": 4300,
                "family": 8550,
                "catch_up_55_plus": 1000,
            },
        },
    }


def _profile(**flag_overrides):
    """Default profile: Single, no catch-up ages, no special flags."""
    flags = {
        "age_50_plus_primary": False,
        "age_55_plus_primary": False,
        "age_60_63_primary": False,
    }
    flags.update(flag_overrides)
    return {
        "tax_year": 2026,
        "filing_status": "Single",
        "address": {"state": "TX"},
        "flags": flags,
    }


def _inputs(**overrides):
    """Default projection_inputs retirement_ytd block: all zeros."""
    base = {
        "tax_year": 2026,
        "retirement_ytd": {
            "employee_401k": 0,
            "employer_match": 0,
            "ira_traditional": 0,
            "ira_roth": 0,
            "hsa_employee": 0,
            "hsa_employer": 0,
            "hsa_coverage": "none",
            "hsa_eligible_months": 12,
            "hsa_december_eligible": True,
            "hsa_spouse_eligible_months": 12,
            "hsa_spouse_december_eligible": True,
            "after_tax_401k": 0,
            "plan_allows_after_tax_401k": False,
        },
    }
    if overrides.get("hsa_monthly_coverage") is not None:
        base["retirement_ytd"].update(hsa_eligible_months=None,hsa_december_eligible=None)
    base["retirement_ytd"].update(overrides)
    return base


# ---------------------------------------------------------------------------
# 401(k) limits and remaining capacity
# ---------------------------------------------------------------------------

def test_compute_401k_limit_under_50_uses_base():
    law = _tax_law_2026()
    limit = rd.compute_401k_employee_limit(
        law, age_50_plus=False, age_60_63=False
    )
    assert limit == 23500


def test_compute_401k_limit_age_50_adds_standard_catchup():
    law = _tax_law_2026()
    limit = rd.compute_401k_employee_limit(
        law, age_50_plus=True, age_60_63=False
    )
    # 23500 + 7500
    assert limit == 31000


def test_compute_401k_limit_age_60_63_supersedes_standard_catchup():
    """Per SECURE 2.0, the 60-63 super catch-up supersedes the standard
    50+ catch-up if both apply."""
    law = _tax_law_2026()
    limit = rd.compute_401k_employee_limit(
        law, age_50_plus=True, age_60_63=True
    )
    # 23500 + 11250 (NOT 23500 + 7500 + 11250)
    assert limit == 34750


def test_compute_401k_limit_age_60_63_alone_uses_super_catchup():
    law = _tax_law_2026()
    limit = rd.compute_401k_employee_limit(
        law, age_50_plus=False, age_60_63=True
    )
    assert limit == 34750


# ---------------------------------------------------------------------------
# IRA limits
# ---------------------------------------------------------------------------

def test_compute_ira_limit_under_50():
    law = _tax_law_2026()
    assert rd.compute_ira_limit(law, age_50_plus=False) == 7000


def test_compute_ira_limit_age_50_adds_catchup():
    law = _tax_law_2026()
    assert rd.compute_ira_limit(law, age_50_plus=True) == 8000


# ---------------------------------------------------------------------------
# HSA limits
# ---------------------------------------------------------------------------

def test_compute_hsa_limit_self_only_under_55():
    law = _tax_law_2026()
    assert rd.compute_hsa_limit(law, coverage="self_only",
                                age_55_plus=False) == 4300


def test_compute_hsa_limit_family_under_55():
    law = _tax_law_2026()
    assert rd.compute_hsa_limit(law, coverage="family",
                                age_55_plus=False) == 8550


def test_compute_hsa_limit_self_only_age_55_adds_catchup():
    law = _tax_law_2026()
    assert rd.compute_hsa_limit(law, coverage="self_only",
                                age_55_plus=True) == 5300


def test_compute_hsa_limit_no_hdhp_returns_zero():
    law = _tax_law_2026()
    assert rd.compute_hsa_limit(law, coverage="none",
                                age_55_plus=False) == 0


def test_compute_hsa_limit_rejects_unknown_coverage():
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="hsa_coverage"):
        rd.compute_hsa_limit(law, coverage="bogus", age_55_plus=False)


# ---------------------------------------------------------------------------
# AccountStatus dataclass invariants (frozen, derived fields)
# ---------------------------------------------------------------------------

def test_account_status_remaining_clamps_at_zero():
    """Over-contribution shouldn't produce negative remaining (the user
    has an excess-contribution problem, not a planning opportunity)."""
    s = rd.AccountStatus(
        name="401(k) employee",
        ytd_contributed=25000,
        annual_limit=23500,
        marginal_fed_rate=0.24,
        marginal_state_rate=0.0,
        kind="pre_tax",
    )
    assert s.remaining == 0
    assert s.over_contributed == 1500


def test_account_status_marginal_saved_zero_for_roth():
    """Roth contributions yield no current-year deduction — marginal $
    saved should be exactly $0 even though they have remaining capacity."""
    s = rd.AccountStatus(
        name="Roth IRA",
        ytd_contributed=0,
        annual_limit=7000,
        marginal_fed_rate=0.24,
        marginal_state_rate=0.05,
        kind="roth",
    )
    assert s.remaining == 7000
    assert s.marginal_fed_saved == 0
    assert s.marginal_state_saved == 0


def test_account_status_marginal_saved_pre_tax():
    """Pre-tax 401(k) — federal marginal × remaining."""
    s = rd.AccountStatus(
        name="401(k) employee",
        ytd_contributed=10000,
        annual_limit=23500,
        marginal_fed_rate=0.24,
        marginal_state_rate=0.05,
        kind="pre_tax",
    )
    assert s.remaining == 13500
    assert s.marginal_fed_saved == pytest.approx(13500 * 0.24, abs=0.01)
    # State piggybacks unless explicitly non-conforming (HSA-specific case).
    assert s.marginal_state_saved == pytest.approx(13500 * 0.05, abs=0.01)


def test_account_status_hsa_kind_uses_state_rate():
    s = rd.AccountStatus(
        name="HSA",
        ytd_contributed=0,
        annual_limit=4300,
        marginal_fed_rate=0.24,
        marginal_state_rate=0.05,
        kind="hsa_conforming",
    )
    assert s.marginal_fed_saved == pytest.approx(4300 * 0.24, abs=0.01)
    assert s.marginal_state_saved == pytest.approx(4300 * 0.05, abs=0.01)


def test_account_status_hsa_non_conforming_state_zero_state_saved():
    """CA/NJ don't conform to federal HSA treatment — HSA contributions
    are state-taxable. Marginal state saved should be $0 even though the
    federal benefit applies."""
    s = rd.AccountStatus(
        name="HSA",
        ytd_contributed=0,
        annual_limit=4300,
        marginal_fed_rate=0.24,
        marginal_state_rate=0.05,
        kind="hsa_non_conforming",
    )
    assert s.marginal_fed_saved == pytest.approx(4300 * 0.24, abs=0.01)
    assert s.marginal_state_saved == 0


def test_account_status_after_tax_kind_zero_current_year_savings():
    """Mega backdoor / after-tax 401(k) contributions don't yield a
    current-year deduction (post-tax dollars in); tax-free growth is
    the v2+ benefit but not modeled in this dashboard."""
    s = rd.AccountStatus(
        name="Mega Backdoor",
        ytd_contributed=0,
        annual_limit=20000,
        marginal_fed_rate=0.24,
        marginal_state_rate=0.05,
        kind="after_tax",
    )
    assert s.remaining == 20000
    assert s.marginal_fed_saved == 0
    assert s.marginal_state_saved == 0


# ---------------------------------------------------------------------------
# IRA deduction / Roth contribution phase-outs
# ---------------------------------------------------------------------------

def test_ira_traditional_deductible_below_phaseout():
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=50000, filing_status="Single",
        covered_by_employer_plan=True,
    )
    assert deductible == 1.0


def test_ira_traditional_zero_deductible_above_phaseout():
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=200000, filing_status="Single",
        covered_by_employer_plan=True,
    )
    assert deductible == 0.0


def test_ira_traditional_partial_deductible_within_phaseout():
    law = _tax_law_2026()
    # Single covered phaseout: 79000-89000 → MAGI 84000 = exactly midpoint
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=84000, filing_status="Single",
        covered_by_employer_plan=True,
    )
    assert deductible == pytest.approx(0.5, abs=0.001)


def test_ira_traditional_full_deduction_if_neither_spouse_covered():
    """Per IRS Pub 590-A Table 1-3: if NEITHER spouse is covered by an
    employer plan, the deduction is unlimited regardless of MAGI."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=500000, filing_status="MFJ",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=False,
    )
    assert deductible == 1.0


def test_ira_traditional_spouse_covered_uses_higher_phaseout():
    """Pub 590-A Table 1-3 row 4: NOT covered, spouse IS covered, MFJ.
    Separate higher phaseout window (mfj_spouse_not_covered:
    $236k-$246k in 2026). Below $236k → full deduction."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=200000, filing_status="MFJ",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,
    )
    assert deductible == 1.0


def test_ira_traditional_spouse_covered_above_phaseout_zero():
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=300000, filing_status="MFJ",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,
    )
    assert deductible == 0.0


def test_ira_traditional_spouse_covered_partial_phaseout():
    """Midpoint of $236k-$246k window = $241k → 50% deductible."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=241000, filing_status="MFJ",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,
    )
    assert deductible == pytest.approx(0.5, abs=0.001)


def test_ira_traditional_spouse_covered_doesnt_affect_single():
    """Spouse coverage is irrelevant for Single filers — no spouse to
    have coverage."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=200000, filing_status="Single",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,  # ignored
    )
    assert deductible == 1.0


def test_ira_traditional_spouse_covered_excludes_qss():
    """A QSS filer has a DECEASED spouse who cannot be covered by a
    current-year employer plan, so a stale
    has_employer_retirement_plan_spouse flag must NOT route them into the
    spouse-covered ($236k-$246k) phase-out. At a high MAGI that would zero
    an MFJ filer's deduction, a not-personally-covered QSS still gets the
    full deduction (case 7, unlimited). Same living-spouse principle as the
    round-18 §219(c) fix, different site (Codex P2)."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=300000, filing_status="QSS",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,  # stale flag — must be ignored
    )
    assert deductible == 1.0  # NOT 0.0 (which is what MFJ would get here)


def test_ira_traditional_spouse_covered_mfj_still_phases_out_regression():
    """Regression guard for the QSS fix: a genuine MFJ filer with a covered
    spouse at $300k MAGI must STILL be fully phased out — the fix narrows
    only QSS, not MFJ."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=300000, filing_status="MFJ",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,
    )
    assert deductible == 0.0


def test_ira_traditional_qss_own_coverage_still_uses_mfj_window():
    """The QSS fix touches only the spouse-covered branch. A QSS who is
    PERSONALLY covered still uses the mfj_covered window (Pub 590-A groups
    'MFJ or QSS' for the taxpayer's own coverage) — full deduction below
    the $126k start."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=100000, filing_status="QSS",
        covered_by_employer_plan=True,
    )
    assert deductible == 1.0  # below mfj_covered $126k start


def test_build_dashboard_reads_spouse_employer_plan_flag():
    """build_dashboard should consult flags.has_employer_retirement_plan_spouse
    and apply the spouse-covered phaseout when MFJ + primary not covered
    + spouse covered."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    profile["flags"]["has_employer_retirement_plan"] = False
    profile["flags"]["has_employer_retirement_plan_spouse"] = True
    inputs = _inputs()
    law = _tax_law_2026()
    # MAGI in the spouse-covered phaseout range
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=241000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # 50% deductible cap = $7000 × 0.5 = $3500; $0 YTD → $3500 eligible.
    # marginal_fed_saved = $3500 × 0.24 = $840 (rate stays at full
    # 0.24 — phase-out caps eligible dollars, not the rate per dollar).
    assert trad.marginal_fed_rate == 0.24
    assert trad.marginal_fed_saved == pytest.approx(840, abs=0.01)


def test_build_dashboard_rejects_string_spouse_employer_plan_flag():
    """The new flag must be strict-parsed too (same lesson as round 1)."""
    profile = _profile()
    profile["flags"]["has_employer_retirement_plan_spouse"] = "yes"
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError,
                       match="has_employer_retirement_plan_spouse"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_ira_traditional_full_deduction_if_not_covered():
    """Backward-compat test for legacy callers without the spouse arg —
    if neither flag is set the function should still return 1.0 in the
    fully-unconvered case."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=500000, filing_status="MFJ",
        covered_by_employer_plan=False,
    )
    assert deductible == 1.0


def test_roth_contribution_allowed_below_phaseout():
    law = _tax_law_2026()
    frac = rd.compute_roth_contribution_fraction(
        law, modified_agi=100000, filing_status="Single"
    )
    assert frac == 1.0


def test_roth_contribution_zero_above_phaseout():
    law = _tax_law_2026()
    frac = rd.compute_roth_contribution_fraction(
        law, modified_agi=200000, filing_status="Single"
    )
    assert frac == 0.0


# ---------------------------------------------------------------------------
# build_dashboard orchestration
# ---------------------------------------------------------------------------

def test_build_dashboard_empty_ytd_full_capacity_remaining():
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    # Should include 401(k), Traditional IRA, Roth IRA at minimum
    names = {a.name for a in report.accounts}
    assert "401(k) employee" in names
    assert "Traditional IRA" in names
    assert "Roth IRA" in names


def test_build_dashboard_hsa_only_when_coverage_present():
    profile = _profile(age_55_plus_primary=False)
    inputs = _inputs(hsa_coverage="none")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    assert all(a.name != "HSA" for a in report.accounts)


def test_build_dashboard_hsa_self_only_emitted():
    profile = _profile()
    inputs = _inputs(hsa_coverage="self_only", hsa_employee=1000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 4300
    assert hsa.ytd_contributed == 1000
    assert hsa.remaining == 3300


def test_build_dashboard_mega_backdoor_only_when_plan_allows():
    profile = _profile()
    inputs = _inputs(plan_allows_after_tax_401k=False)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    assert all(a.name != "Mega Backdoor" for a in report.accounts)


def test_build_dashboard_mega_backdoor_when_plan_allows():
    profile = _profile()
    inputs = _inputs(
        plan_allows_after_tax_401k=True,
        employee_401k=10000,
        employer_match=5000,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    # total_annual_limit (70000) - employee_401k (10000) - employer_match (5000)
    # - after_tax_ytd (0) = 55000
    assert mega.annual_limit == 55000
    assert mega.remaining == 55000


def test_build_dashboard_hsa_non_conforming_state_zero_state_saved():
    """CA is non-conforming for HSA. The dashboard must flag this."""
    profile = _profile()
    profile["address"]["state"] = "CA"
    inputs = _inputs(hsa_coverage="self_only")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.093,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.kind == "hsa_non_conforming"
    assert hsa.marginal_state_saved == 0


def test_build_dashboard_total_potential_saved():
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.05,
        modified_agi=80000,
    )
    # Sum of per-account marginal_fed_saved equals report total
    assert report.total_potential_fed_saved == pytest.approx(
        sum(a.marginal_fed_saved for a in report.accounts), abs=0.01
    )


# ---------------------------------------------------------------------------
# Strict input validation (lessons from PR #19)
# ---------------------------------------------------------------------------

def test_build_dashboard_rejects_truthy_string_plan_allows():
    """Round-2 lesson: bool fields must be strict — "yes"/"no" silently
    coerced to True/False would mis-fire mega backdoor recommendations."""
    profile = _profile()
    inputs = _inputs(plan_allows_after_tax_401k="yes")
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="plan_allows_after_tax_401k"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_negative_ytd():
    profile = _profile()
    inputs = _inputs(employee_401k=-100)
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="employee_401k"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_negative_marginal_rate():
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="marginal_fed_rate"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=-0.1, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_marginal_rate_above_one():
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="marginal_fed_rate"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=1.5, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_non_dict_profile():
    """Round-3 lesson: validate profile root before .get()."""
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError):
        rd.build_dashboard(
            [], _inputs(), law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_year_mismatch():
    """Profile is TY2025 but caller asked for TY2026 — guard against
    accidentally pricing prior year's limits."""
    profile = _profile()
    profile["tax_year"] = 2025
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="tax_year"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
            tax_year=2026,
        )


# ---------------------------------------------------------------------------
# Negative-direction pins (round-7/8 lesson)
# ---------------------------------------------------------------------------

def test_age_50_flag_off_uses_base_limits():
    """Round-7 lesson: companion to 'age_50 raises limit', pin that
    age_50=False does NOT silently raise it."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    ira = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert ira.annual_limit == 7000  # not 8000


def test_age_55_flag_off_no_hsa_catchup():
    profile = _profile(age_55_plus_primary=False)
    inputs = _inputs(hsa_coverage="self_only")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 4300  # not 5300


# ---------------------------------------------------------------------------
# Tax law year-window completeness (round-13 lesson)
# ---------------------------------------------------------------------------

def test_build_dashboard_rejects_missing_401k_limit():
    """If tax_law has no elective_deferral populated (a half-fetched
    tax_law file), fail loudly rather than reporting $0 limit silently."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    law["federal"]["retirement_401k"]["elective_deferral"] = None
    with pytest.raises(rd.RetirementError, match="elective_deferral"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_missing_ira_phaseout_map():
    """A cache fetched far enough to populate retirement_401k but missing
    the ira_limits traditional_deduction_phaseout map must raise a clean
    RetirementError, not a raw KeyError that escapes the CLI handler. The
    phase-out read is reached only when the filer is covered by an
    employer plan (here Single + covered → single_covered key)."""
    profile = _profile()
    profile["flags"]["has_employer_retirement_plan"] = True
    inputs = _inputs()
    law = _tax_law_2026()
    law["federal"]["ira_limits"].pop("traditional_deduction_phaseout")
    with pytest.raises(rd.RetirementError, match="traditional_deduction_phaseout"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=85000,  # inside the single_covered phase-out window
        )


# ---------------------------------------------------------------------------
# Codex round 1 (PR #20): IRA shared-pool math
# ---------------------------------------------------------------------------

def test_traditional_ira_full_ytd_leaves_zero_roth_capacity():
    """Per IRS Pub 590-A, Traditional + Roth IRA share the annual limit.
    A user with $7000 already in Traditional has $0 Roth capacity left,
    not $7000."""
    profile = _profile()
    inputs = _inputs(ira_traditional=7000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    assert roth.remaining == 0


def test_roth_ira_full_ytd_leaves_zero_traditional_capacity():
    profile = _profile()
    inputs = _inputs(ira_roth=7000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.remaining == 0


def test_partial_split_caps_each_bucket_at_pool_remaining():
    """$3500 Traditional + $0 Roth → per-bucket "additional capacity"
    is $3500 each (you can put it in either bucket), but the POOL
    remaining is only $3500, so total_remaining must not over-count."""
    profile = _profile()
    inputs = _inputs(ira_traditional=3500)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    assert trad.remaining == 3500
    assert roth.remaining == 3500
    # But the IRA pool only has $3500 total remaining capacity.
    # total_remaining must dedupe shared pools.
    ira_pool_contribution = trad.remaining  # = roth.remaining = pool_remaining
    # Total should NOT include both Trad+Roth = $7000 from the IRA pool;
    # it should include the pool's $3500 once + 401(k) remaining + ...
    # The 401(k) remaining is $23,500 (full base), HSA is 0 (none coverage)
    expected_total = 23500 + ira_pool_contribution
    assert report.total_remaining == expected_total


def test_ira_pool_id_marked_on_both_buckets():
    """For test introspection: both IRA accounts must carry the same
    shared_pool_id so the dedupe logic can pair them."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    assert trad.shared_pool_id == roth.shared_pool_id
    assert trad.shared_pool_id == "ira"


def test_total_potential_fed_saved_dedupes_ira_pool():
    """For the IRA shared pool, total potential federal savings should
    be `pool_remaining × max(per-bucket fed rate)` — not the sum of
    per-bucket marginal_fed_saved which double-counts the pool capacity."""
    profile = _profile()
    inputs = _inputs()  # $0 / $0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    # Pool remaining = $7000. Best marginal saved is Traditional (fully
    # deductible at this MAGI) → $7000 × 0.24 = $1680. Roth has $0.
    # The dedup should NOT add $1680 (Trad) + $0 (Roth) = $1680 — happens
    # to be correct because Roth is $0. But the test still pins the pool
    # contribution is exactly $1680 once.
    # Now construct a case where double-counting would matter:
    # If both buckets had non-zero marginal saved, summing would over-count.
    # Here we just pin the pool's contribution is $1680.
    # 401(k) at $23500 limit × 0.24 = $5640
    # Pool IRA × 0.24 = $1680
    # Total should be $5640 + $1680 = $7320
    assert report.total_potential_fed_saved == pytest.approx(5640 + 1680, abs=0.01)


# ---------------------------------------------------------------------------
# Codex round 2 (PR #20): catch-up deferrals outside §415(c)
# ---------------------------------------------------------------------------

def test_mega_backdoor_space_excludes_catch_up_portion():
    """Per IRC §415(c) + §414(v): catch-up contributions are EXCLUDED
    from the §415(c) annual-additions limit. A 50-year-old with
    $31,000 YTD 401(k) (= $23,500 regular + $7,500 catch-up) should
    have $46,500 of Mega Backdoor space (= $70k - $23.5k - $0 - $0),
    NOT $39,000 (= $70k - $31k - $0 - $0)."""
    profile = _profile(age_50_plus_primary=True)
    inputs = _inputs(
        employee_401k=31000,  # includes $7500 catch-up
        employer_match=0,
        plan_allows_after_tax_401k=True,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    # 70000 - 23500 (base only, catch-up excluded) - 0 - 0 = 46500
    assert mega.annual_limit == 46500
    assert mega.remaining == 46500


def test_mega_backdoor_capped_by_415c_compensation():
    """§415(c) limits annual additions to the LESSER of the dollar limit or
    100% of compensation. With $40k comp, $0 deferrals/match, and a $70k
    total_annual_limit, the after-tax Mega Backdoor room is $40k (not $70k)
    — else a low-comp participant is led into an excess annual addition
    (Codex P2)."""
    profile = _profile()
    inputs = _inputs(
        plan_allows_after_tax_401k=True, employee_401k=0, employer_match=0
    )
    inputs["retirement_ytd"]["taxable_compensation"] = 40000
    law = _tax_law_2026()  # total_annual_limit = 70000
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=40000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 40000.0  # capped at 100% of compensation
    assert "100% of compensation" in mega.note


def test_mega_backdoor_not_capped_when_comp_exceeds_dollar_limit():
    """Regression: when compensation exceeds the dollar limit, the §415(c)
    cap is the dollar limit (compensation doesn't bind)."""
    profile = _profile()
    inputs = _inputs(
        plan_allows_after_tax_401k=True, employee_401k=0, employer_match=0
    )
    inputs["retirement_ytd"]["taxable_compensation"] = 200000  # > $70k
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 70000.0  # dollar limit


def test_mega_backdoor_warns_when_comp_absent_for_415c():
    """When taxable_compensation is absent, the §415(c) 100%-of-comp ceiling
    can't be applied — fall back to the dollar limit but warn it may be
    overstated for a low-comp / partial-year participant."""
    profile = _profile()
    inputs = _inputs(
        plan_allows_after_tax_401k=True, employee_401k=0, employer_match=0
    )
    inputs["retirement_ytd"].pop("taxable_compensation", None)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 70000.0  # dollar limit (no comp cap applied)
    assert "§415(c)" in mega.note and "OVERSTATED" in mega.note


def test_mega_backdoor_unavailable_catchup_consumes_415c_space():
    """When a §603 high earner's plan has no Roth source, dollars above the
    base elective limit are excess deferrals (catch-up UNAVAILABLE), not
    legal catch-up — so they CONSUME §415(c) annual-additions space and
    reduce Mega Backdoor room. $31k YTD on a $23.5k base → after-tax room is
    reduced by the full $31k, not just $23.5k (Codex P2)."""
    profile = _profile(age_50_plus_primary=True)
    inputs = _inputs(
        plan_allows_after_tax_401k=True,
        employee_401k=31000,  # 23.5k base + 7.5k that can't be catch-up
        employer_match=0,
    )
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000  # high earner
    inputs["retirement_ytd"]["plan_offers_roth_401k"] = False  # no Roth source
    inputs["retirement_ytd"]["taxable_compensation"] = 200000  # uncap §415 comp
    law = _tax_law_2026()  # total_annual_limit = 70000
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    # catch-up unavailable → full $31k consumes §415(c): 70000 - 31000 = 39000
    assert mega.annual_limit == 39000.0


def test_mega_backdoor_available_catchup_excluded_from_415c():
    """Contrast: when the plan HAS a Roth source (catch-up available), the
    $7.5k catch-up is §414(v)-excluded from §415(c) → after-tax room =
    70000 - 23500 = 46500."""
    profile = _profile(age_50_plus_primary=True)
    inputs = _inputs(
        plan_allows_after_tax_401k=True, employee_401k=31000, employer_match=0
    )
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    inputs["retirement_ytd"]["plan_offers_roth_401k"] = True
    inputs["retirement_ytd"]["taxable_compensation"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 46500.0


def test_mega_backdoor_space_excludes_super_catch_up_portion():
    """SECURE 2.0 60-63 super catch-up is also §414(v) — also excluded
    from §415(c). A 62-year-old with $34,750 YTD 401(k) (= $23,500
    regular + $11,250 super catch-up) should have $46,500 of Mega
    Backdoor space."""
    profile = _profile(age_60_63_primary=True)
    inputs = _inputs(
        employee_401k=34750,
        employer_match=0,
        plan_allows_after_tax_401k=True,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 46500


def test_mega_backdoor_space_under_50_no_catch_up_exclusion():
    """Under-50 has no catch-up, so behavior is unchanged: full YTD
    401(k) consumes §415(c) space."""
    profile = _profile()
    inputs = _inputs(
        employee_401k=23500,
        employer_match=0,
        plan_allows_after_tax_401k=True,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    # 70000 - 23500 - 0 - 0 = 46500
    assert mega.annual_limit == 46500


def test_mega_backdoor_space_50_plus_under_base_limit():
    """If a 50-year-old hasn't yet exceeded the base elective limit
    (e.g. YTD $10,000 of regular contributions), there's no catch-up
    portion to exclude — full YTD consumes §415(c) space."""
    profile = _profile(age_50_plus_primary=True)
    inputs = _inputs(
        employee_401k=10000,
        employer_match=5000,
        plan_allows_after_tax_401k=True,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    # 70000 - 10000 (no catch-up to exclude) - 5000 - 0 = 55000
    assert mega.annual_limit == 55000


# ---------------------------------------------------------------------------
# Codex round 3 (PR #20): strict bool for profile flags
# ---------------------------------------------------------------------------

def test_build_dashboard_rejects_string_age_50_flag():
    """Round-2 lesson from PR #19, generalized: profile bool flags need
    the same strict parsing as retirement_ytd. A hand-edited "false"
    would otherwise coerce to True and inflate the 401(k) limit by the
    catch-up amount."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = "false"
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="age_50_plus_primary"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_string_age_60_63_flag():
    profile = _profile()
    profile["flags"]["age_60_63_primary"] = "yes"
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="age_60_63_primary"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_string_age_55_flag():
    profile = _profile()
    profile["flags"]["age_55_plus_primary"] = "1"
    inputs = _inputs(hsa_coverage="self_only")
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="age_55_plus_primary"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_string_employer_plan_flag():
    profile = _profile()
    profile["flags"]["has_employer_retirement_plan"] = "yes"
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="has_employer_retirement_plan"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_accepts_missing_profile_flags():
    """Missing flag should default to False (not error) — the strict
    rule is 'wrong type rejected', not 'must be present'."""
    profile = _profile()
    del profile["flags"]["age_50_plus_primary"]
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    k401 = next(a for a in report.accounts if a.name == "401(k) employee")
    assert k401.annual_limit == 23500  # base, no catch-up


def test_build_dashboard_accepts_null_profile_flag():
    """Explicit JSON null should also default to False."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = None
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    k401 = next(a for a in report.accounts if a.name == "401(k) employee")
    assert k401.annual_limit == 23500


# ---------------------------------------------------------------------------
# Codex round 9 (PR #20): MFS filing status has its own IRA phase-out
# windows ($0-$10,000) per IRS Pub 590-A. Treating MFS as Single produced
# silently wrong numbers for every MFS-with-spouse filer.
# ---------------------------------------------------------------------------

def test_traditional_ira_mfs_covered_lived_with_spouse_phases_at_low_magi():
    """Pub 590-A: MFS + covered + lived with spouse → phase-out at
    $0-$10,000. $50,000 MAGI is way above the window → fully phased
    out (0% deductible). Was 100% deductible under the wrong Single
    window."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=50000, filing_status="MFS",
        covered_by_employer_plan=True,
        spouse_covered_by_employer_plan=False,
        lived_apart_from_spouse_all_year=False,
    )
    assert deductible == 0.0


def test_traditional_ira_mfs_covered_midpoint_phaseout():
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=5000, filing_status="MFS",
        covered_by_employer_plan=True,
        spouse_covered_by_employer_plan=False,
        lived_apart_from_spouse_all_year=False,
    )
    # Midpoint of $0-$10k → 50% deductible
    assert deductible == pytest.approx(0.5, abs=0.001)


def test_traditional_ira_mfs_lived_apart_treated_as_single():
    """MFS + lived apart all year → use Single phase-out instead of
    the MFS $0-$10k window (per Pub 590-A footnote)."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=50000, filing_status="MFS",
        covered_by_employer_plan=True,
        spouse_covered_by_employer_plan=False,
        lived_apart_from_spouse_all_year=True,
    )
    # Single phase-out is $79k-$89k; $50k is well below → full deduction
    assert deductible == 1.0


def test_traditional_ira_mfs_not_covered_full_deduction():
    """MFS + not covered → unlimited deduction (no joint test applies
    to MFS for the not-covered case)."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=500000, filing_status="MFS",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=False,
        lived_apart_from_spouse_all_year=False,
    )
    assert deductible == 1.0


def test_roth_ira_mfs_lived_with_spouse_phases_at_low_magi():
    """MFS + lived with spouse: Roth phase-out $0-$10k. $50k MAGI →
    fully phased out. Was fully eligible under wrong Single window."""
    law = _tax_law_2026()
    frac = rd.compute_roth_contribution_fraction(
        law, modified_agi=50000, filing_status="MFS",
        lived_apart_from_spouse_all_year=False,
    )
    assert frac == 0.0


def test_roth_ira_mfs_lived_with_spouse_partial_phaseout():
    law = _tax_law_2026()
    frac = rd.compute_roth_contribution_fraction(
        law, modified_agi=5000, filing_status="MFS",
        lived_apart_from_spouse_all_year=False,
    )
    assert frac == pytest.approx(0.5, abs=0.001)


def test_roth_ira_mfs_lived_apart_uses_single_window():
    """MFS + lived apart all year → use Single phase-out ($150k-$165k)."""
    law = _tax_law_2026()
    frac = rd.compute_roth_contribution_fraction(
        law, modified_agi=50000, filing_status="MFS",
        lived_apart_from_spouse_all_year=True,
    )
    # $50k is well below Single $150k start → full Roth allowed
    assert frac == 1.0


def test_roth_ira_mfs_lived_apart_above_single_window():
    law = _tax_law_2026()
    frac = rd.compute_roth_contribution_fraction(
        law, modified_agi=200000, filing_status="MFS",
        lived_apart_from_spouse_all_year=True,
    )
    assert frac == 0.0


def test_build_dashboard_reads_lived_apart_flag():
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"]["lived_apart_from_spouse_all_year"] = False
    inputs = _inputs()
    law = _tax_law_2026()
    # $50k MAGI: MFS+with-spouse → Roth fully phased, Trad fully phased
    # if covered, full if not
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    # Roth limit should be $0 (fully phased out for MFS-with-spouse at $50k)
    assert roth.annual_limit == 0


def test_build_dashboard_mfs_default_now_hard_errors_post_round_16():
    """ROUND-16 BEHAVIOR CHANGE: previously this test asserted that a
    missing lived_apart flag silently defaulted to lived-with-spouse
    for MFS filers. Codex round 16 correctly flagged that as a silent-
    wrong-numbers bug — the flag is load-bearing for IRA computations,
    and silent default produced the wrong phase-out for actually-
    lived-apart taxpayers. The new behavior is hard-error; this test
    pins the behavior change so a future revert would fail loudly."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    # flag deliberately absent
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="lived_apart_from_spouse_all_year"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=50000,
        )


def test_build_dashboard_rejects_string_lived_apart_flag():
    profile = _profile()
    profile["flags"]["lived_apart_from_spouse_all_year"] = "yes"
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="lived_apart_from_spouse_all_year"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_single_filer_unaffected_by_mfs_change():
    """Regression pin: the MFS-specific fix must not change Single
    semantics."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=84000, filing_status="Single",
        covered_by_employer_plan=True,
    )
    # Still 50% (midpoint of $79k-$89k Single window)
    assert deductible == pytest.approx(0.5, abs=0.001)


def test_mfj_filer_unaffected_by_mfs_change():
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=136000, filing_status="MFJ",
        covered_by_employer_plan=True,
    )
    # Still 50% (midpoint of $126k-$146k MFJ window)
    assert deductible == pytest.approx(0.5, abs=0.001)


# ---------------------------------------------------------------------------
# Codex round 8 (PR #20): emission gates should fire on data presence, not
# just configuration presence — when a user reports YTD but the
# corresponding flag is OFF, that's an excess scenario (corrective action
# required), not a silent skip.
# ---------------------------------------------------------------------------

def test_hsa_coverage_none_with_ytd_flags_excess():
    """Codex round 8: hsa_coverage='none' + hsa_employee=$3000 means
    the user contributed without HDHP eligibility — every dollar is
    excess. Dashboard must emit the HSA row with limit=$0 + the YTD as
    over_contributed, not silently skip it."""
    profile = _profile()
    inputs = _inputs(hsa_coverage="none", hsa_employee=3000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 0
    assert hsa.ytd_contributed == 3000
    assert hsa.over_contributed == 3000


def test_hsa_coverage_none_with_employer_ytd_flags_excess():
    """Same but with employer-side YTD contribution."""
    profile = _profile()
    inputs = _inputs(hsa_coverage="none", hsa_employer=2000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 0
    assert hsa.over_contributed == 2000


def test_hsa_coverage_none_no_ytd_skips_row():
    """Regression pin: coverage='none' AND YTD=0 → no HSA row.
    This is the normal 'taxpayer has no HSA' case."""
    profile = _profile()
    inputs = _inputs(hsa_coverage="none")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    assert all(a.name != "HSA" for a in report.accounts)


def test_hsa_coverage_none_with_ytd_has_corrective_note():
    """The excess scenario needs explicit guidance — verify HDHP
    eligibility or withdraw before April 15."""
    profile = _profile()
    inputs = _inputs(hsa_coverage="none", hsa_employee=3000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    note_lower = hsa.note.lower()
    assert "hsa_coverage" in note_lower or "coverage" in note_lower
    assert "verify" in note_lower or "withdraw" in note_lower
    assert "april 15" in note_lower or "excise" in note_lower


# --- Proactive symmetric extension: same pattern for Mega Backdoor ---

def test_mega_backdoor_disallowed_with_ytd_flags_excess():
    """If the plan doesn't allow after-tax contributions but the user
    has after_tax_401k YTD, that's a data-vs-flag contradiction —
    every after-tax dollar is excess against an unauthorized account
    type. Emit the row so the user sees the corrective-action signal."""
    profile = _profile()
    inputs = _inputs(
        plan_allows_after_tax_401k=False,
        after_tax_401k=5000,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 0
    assert mega.ytd_contributed == 5000
    assert mega.over_contributed == 5000


def test_mega_backdoor_disallowed_no_ytd_skips_row():
    """Regression pin: plan_allows=False AND ytd=0 → no Mega Backdoor
    row. Normal case for taxpayers without after-tax-allowing plans."""
    profile = _profile()
    inputs = _inputs(plan_allows_after_tax_401k=False, after_tax_401k=0)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    assert all(a.name != "Mega Backdoor" for a in report.accounts)


def test_mega_backdoor_disallowed_with_ytd_has_corrective_note():
    profile = _profile()
    inputs = _inputs(plan_allows_after_tax_401k=False, after_tax_401k=5000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    note_lower = mega.note.lower()
    assert "plan_allows_after_tax_401k" in note_lower or "plan does not allow" in note_lower
    assert "excess" in note_lower or "withdraw" in note_lower or "verify" in note_lower


def test_mega_backdoor_excess_uses_415c_not_ira_hsa_6pct_excise():
    """An after-tax 401(k) excess (plan disallows after-tax but YTD>0) is a
    §415(c) annual addition corrected via EPCRS (returned amount taxable),
    NOT the IRA/HSA §4973 6%-excise / April-15 model. The note and the
    render banner must give the §415(c) path, not the wrong penalty (Codex
    P2)."""
    profile = _profile()
    inputs = _inputs(plan_allows_after_tax_401k=False, after_tax_401k=5000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert "§415(c)" in mega.note
    assert "EPCRS" in mega.note
    # The wrong model must be explicitly negated, not asserted:
    assert "NOT subject to the §4973 6% excise" in mega.note
    # Render banner for the over-contributed after_tax row uses §415(c) too:
    md = rd.render_markdown(report)
    assert "§415(c) annual addition" in md


# ---------------------------------------------------------------------------
# Codex round 7 (PR #20): aggregate IRA pool excess contribution detection
# — symmetric inverse of round-1 Finding 1 (pool-aware remaining cap), now
# applied to the over-contribution direction (corrective distribution flag).
# ---------------------------------------------------------------------------

def test_ira_pool_excess_codex_exact_example():
    """Codex's exact case: $7000 Trad + $7000 Roth against $7000 IRA pool
    cap. Each bucket reads at-cap (no per-bucket excess), but the pool
    is $7000 over and the user needs a corrective distribution."""
    profile = _profile()  # under-50, IRA limit = $7000
    inputs = _inputs(ira_traditional=7000, ira_roth=7000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    # Both buckets surface the pool excess (the user needs to see it
    # regardless of which row they look at first).
    assert trad.pool_over_contributed == 7000
    assert roth.pool_over_contributed == 7000


def test_ira_pool_excess_partial_overage():
    """$5000 Trad + $3000 Roth = $8000 total against $7000 cap → $1000
    pool excess. Each bucket individually under bucket cap."""
    profile = _profile()
    inputs = _inputs(ira_traditional=5000, ira_roth=3000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.pool_over_contributed == 1000


def test_ira_pool_no_excess_when_under_limit():
    """$3000 Trad + $3000 Roth = $6000 → under $7000 → pool excess 0."""
    profile = _profile()
    inputs = _inputs(ira_traditional=3000, ira_roth=3000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    assert trad.pool_over_contributed == 0
    assert roth.pool_over_contributed == 0


def test_ira_pool_no_excess_at_exact_limit():
    """$4000 Trad + $3000 Roth = $7000 → exactly at cap → no excess."""
    profile = _profile()
    inputs = _inputs(ira_traditional=4000, ira_roth=3000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.pool_over_contributed == 0


def test_ira_pool_excess_with_50_plus_catchup():
    """With age 50+ catch-up, IRA limit = $8000. $5000 Trad + $5000 Roth =
    $10,000 → $2000 pool excess against the elevated cap."""
    profile = _profile(age_50_plus_primary=True)
    inputs = _inputs(ira_traditional=5000, ira_roth=5000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.pool_over_contributed == 2000


def test_ira_pool_excess_note_appended_when_over():
    """When pool excess > 0, both IRA notes should warn about the
    corrective-distribution requirement."""
    profile = _profile()
    inputs = _inputs(ira_traditional=7000, ira_roth=7000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    for acct, label in ((trad, "Traditional"), (roth, "Roth")):
        note_lower = acct.note.lower()
        assert "pool over-contribut" in note_lower or "excess" in note_lower, (
            f"{label} IRA note should warn about pool excess, got: {acct.note!r}"
        )
        # The corrective-distribution timing is important UX.
        assert "april 15" in note_lower or "corrective" in note_lower


def test_ira_pool_excess_note_absent_when_no_excess():
    """Regression pin: no pool excess → no warning."""
    profile = _profile()
    inputs = _inputs(ira_traditional=3000, ira_roth=3000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert "over-contribut" not in trad.note.lower()
    assert "excess" not in trad.note.lower()


# ---------------------------------------------------------------------------
# Codex round 5 (PR #20): tax_law year validation (third instance of the
# strict-validation generalization gap — already caught for profile and
# projection_inputs; missed for tax_law)
# ---------------------------------------------------------------------------

def test_build_dashboard_rejects_stale_tax_law_year_via_top_level():
    """A user passing tax_law_2025.json with --year 2026 would silently
    price every limit and phase-out with 2025 law. The check must read
    the REAL persisted field (top-level `tax_year`) — round-5 had a
    hypothetical `meta.year` field that doesn't exist in the cache."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    law["tax_year"] = 2025  # top-level field (real cache schema)
    law["_meta"]["tax_year"] = 2025  # mirror — both should agree
    with pytest.raises(rd.RetirementError, match="tax_law"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
            tax_year=2026,
        )


def test_build_dashboard_rejects_stale_tax_law_year_via_meta():
    """If only the _meta.tax_year mismatches, that's also a hard error.
    Defensive: catch a writer that drifts and updates one field but not
    the other."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    law.pop("tax_year", None)  # remove top-level so only _meta has it
    law["_meta"]["tax_year"] = 2025
    with pytest.raises(rd.RetirementError, match="tax_law"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
            tax_year=2026,
        )


def test_build_dashboard_accepts_missing_tax_law_meta():
    """If tax_law has no _meta block, fall back to top-level tax_year.
    Absence is OK, mismatch is not."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    law.pop("_meta", None)
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
        tax_year=2026,
    )
    assert report.year == 2026


def test_build_dashboard_accepts_missing_top_level_tax_year():
    """If only _meta carries the year, that's also fine."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    law.pop("tax_year", None)
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
        tax_year=2026,
    )
    assert report.year == 2026


def test_build_dashboard_accepts_both_year_fields_absent():
    """No year info anywhere → treat as unknown, not mismatched."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    law.pop("tax_year", None)
    law.pop("_meta", None)
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
        tax_year=2026,
    )
    assert report.year == 2026


def test_build_dashboard_accepts_null_tax_law_year():
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    law["tax_year"] = None
    law["_meta"]["tax_year"] = None
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
        tax_year=2026,
    )
    assert report.year == 2026


def test_build_dashboard_rejects_non_dict_tax_law_meta():
    """Defense in depth: a tax_law with a wrong-type _meta block should
    hard-error rather than crash later with AttributeError."""
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    law["_meta"] = "2026"  # string, not object
    with pytest.raises(rd.RetirementError):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
            tax_year=2026,
        )


# --- Codex round 6 finding 2: spouse HSA catch-up for family coverage ---

def test_hsa_family_dual_55_plus_includes_both_catchups():
    """Per IRS Pub 969: HSA catch-up is per-individual. For MFJ + family
    HDHP + both 55+, the household total = $8550 (family) + $1000
    (primary) + $1000 (spouse) = $10,550 (must be in two separate
    HSAs but the household-level dashboard tracks the combined limit)."""
    profile = _profile(age_55_plus_primary=True)
    profile["filing_status"] = "MFJ"
    profile["flags"]["age_55_plus_spouse"] = True
    inputs = _inputs(hsa_coverage="family")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    # $8550 family + $1000 + $1000 spouse catch-up
    assert hsa.annual_limit == 10550


def test_hsa_family_only_primary_55_plus():
    """Family coverage, only primary is 55+: only primary catch-up."""
    profile = _profile(age_55_plus_primary=True)
    profile["filing_status"] = "MFJ"
    profile["flags"]["age_55_plus_spouse"] = False
    inputs = _inputs(hsa_coverage="family")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 9550  # 8550 + 1000


def test_hsa_family_only_spouse_55_plus():
    """Family coverage, only spouse is 55+: only spouse catch-up. The
    spouse's catch-up still bumps the household total even though the
    primary is under 55."""
    profile = _profile(age_55_plus_primary=False)
    profile["filing_status"] = "MFJ"
    profile["flags"]["age_55_plus_spouse"] = True
    inputs = _inputs(hsa_coverage="family")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 9550


def test_hsa_self_only_ignores_spouse_55_flag():
    """Self-only HDHP: spouse doesn't have HDHP coverage and can't
    contribute, so spouse catch-up is irrelevant."""
    profile = _profile(age_55_plus_primary=True)
    profile["filing_status"] = "MFJ"
    profile["flags"]["age_55_plus_spouse"] = True
    inputs = _inputs(hsa_coverage="self_only")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    # 4300 + 1000 primary, no spouse catchup for self-only
    assert hsa.annual_limit == 5300


def test_hsa_limit_prices_confirmed_partial_year_eligibility():
    # Pub 969 monthly worksheet: six self-only eligible months × 4300/12.
    report = rd.build_dashboard(_profile(),
        _inputs(hsa_coverage="self_only", hsa_eligible_months=6), _tax_law_2026(),
        marginal_fed_rate=.24, marginal_state_rate=0, modified_agi=80000)
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 2150
    assert "first-of-month eligibility" in hsa.note


def test_hsa_partial_year_caveat_absent_when_no_coverage():
    """The partial-year caveat is for active HDHP coverage only — a
    coverage='none' + YTD>0 contradiction row carries the excess warning,
    not the proration caveat (which would be irrelevant there)."""
    profile = _profile()
    inputs = _inputs(hsa_coverage="none")
    inputs["retirement_ytd"]["hsa_employee"] = 1000  # excess (no coverage)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert "full-year hdhp coverage" not in hsa.note.lower()
    assert "excess" in hsa.note.lower()  # contradiction warning still present


def test_hsa_family_neither_55_no_catchups():
    """Family + neither 55+ → base family limit, no catch-ups."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    inputs = _inputs(hsa_coverage="family")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 8550


def test_build_dashboard_rejects_string_age_55_spouse_flag():
    """Same strict-bool rule applied uniformly across ALL profile bool
    flags (round-1 finding 3 lesson + the generalization-gap routine)."""
    profile = _profile()
    profile["flags"]["age_55_plus_spouse"] = "yes"
    inputs = _inputs(hsa_coverage="family")
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="age_55_plus_spouse"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


# ---------------------------------------------------------------------------
# Codex round 4 (PR #20): IRA phase-out is a CAP on deductible dollars,
# not a per-dollar rate scalar, + spouse-covered phase-out note coverage
# ---------------------------------------------------------------------------

def test_traditional_ira_partial_phaseout_caps_deductible_dollars():
    """IRS Pub 590-A Worksheet 1-2: phase-out reduces the DEDUCTIBLE
    CAP, not the per-dollar rate. At 50% phase-out with $7000 limit,
    deductible cap = $3500. If $3000 already contributed (all
    deductible — under the cap), remaining deductible dollars = $500
    — NOT $4000 × 50%.

    For Codex's example, marginal_fed_saved on the remaining $4000
    should be $500 × 0.24 = $120, NOT $4000 × 0.12 = $480.
    """
    profile = _profile()
    profile["filing_status"] = "Single"
    profile["flags"]["has_employer_retirement_plan"] = True
    # Set MAGI in the middle of single_covered phase-out ($79k-$89k = $84k)
    # so trad_frac = 0.5
    inputs = _inputs(ira_traditional=3000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=84000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # deductible cap = $7000 × 0.5 = $3500
    # already deducted = $3000 (under cap)
    # remaining deductible = $500
    # marginal_fed_saved on remaining = $500 × 0.24 = $120
    assert trad.marginal_fed_saved == pytest.approx(120, abs=0.01)


def test_traditional_ira_fully_deductible_cap_unchanged():
    """100% deductible (no phase-out) — marginal_fed_saved should equal
    remaining × full marginal rate. Regression pin that the new
    deductible-cap logic doesn't break the simple case."""
    profile = _profile()
    inputs = _inputs(ira_traditional=3000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # Full deduction, $0 Roth → trad pool_remaining = $4000
    # marginal_fed_saved = $4000 × 0.24 = $960
    assert trad.marginal_fed_saved == pytest.approx(960, abs=0.01)


def test_traditional_ira_ytd_exceeds_deductible_cap_no_remaining_saved():
    """If YTD already at or above the deductible cap, no more tax
    savings available — even though pool_remaining > 0 (the user can
    still contribute nondeductibly)."""
    profile = _profile()
    profile["filing_status"] = "Single"
    profile["flags"]["has_employer_retirement_plan"] = True
    # 50% phaseout, deductible cap = $3500
    # Contribute $4000 traditional (over deductible cap, under $7000
    # contribution cap)
    inputs = _inputs(ira_traditional=4000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=84000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # YTD $4000 > deductible cap $3500 → no remaining deductible dollars
    assert trad.marginal_fed_saved == 0


def test_traditional_ira_zero_phaseout_no_savings():
    """100% phased out (MAGI above end). No tax savings on any
    remaining dollar."""
    profile = _profile()
    profile["filing_status"] = "Single"
    profile["flags"]["has_employer_retirement_plan"] = True
    inputs = _inputs(ira_traditional=0)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=120000,  # Above $89k phase-out end for single
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.marginal_fed_saved == 0


def test_traditional_ira_partial_phaseout_capped_by_ira_pool():
    """When Roth has consumed shared IRA pool, the deductible
    capacity is the lesser of pool_remaining and remaining
    deductible-cap dollars."""
    profile = _profile()
    profile["filing_status"] = "Single"
    profile["flags"]["has_employer_retirement_plan"] = True
    # 50% phaseout, $0 Trad YTD, $5000 Roth YTD → pool_remaining = $2000
    # Deductible cap = $3500, but pool_remaining caps additional to $2000
    inputs = _inputs(ira_traditional=0, ira_roth=5000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=84000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # min(pool_remaining=$2000, deductible_remaining=$3500) = $2000
    # marginal_fed_saved = $2000 × 0.24 = $480
    assert trad.marginal_fed_saved == pytest.approx(480, abs=0.01)


# --- Finding 2: phase-out notes fire for spouse-covered case ---

def test_traditional_ira_note_explains_spouse_covered_partial_phaseout():
    """MFJ + primary not covered + spouse covered at MAGI in the
    $236k-$246k window → partial deduction. The note should explain
    the phase-out, not just say 'shares limit with Roth'."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    profile["flags"]["has_employer_retirement_plan"] = False
    profile["flags"]["has_employer_retirement_plan_spouse"] = True
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=241000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # Note should explain the partial deduction, not just be silent
    note_lower = trad.note.lower()
    assert "phase" in note_lower or "deduct" in note_lower
    assert "spouse" in note_lower  # The trigger reason should be explicit


def test_traditional_ira_note_explains_spouse_covered_fully_phased_out():
    """MFJ + primary not covered + spouse covered at MAGI above
    $246k → non-deductible. Note should say so."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    profile["flags"]["has_employer_retirement_plan"] = False
    profile["flags"]["has_employer_retirement_plan_spouse"] = True
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=300000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    note_lower = trad.note.lower()
    assert "non-deductible" in note_lower or "phased out" in note_lower


def test_traditional_ira_note_unchanged_when_no_employer_plan_coverage():
    """Regression pin: when neither spouse is covered (fully deductible),
    the phase-out note should NOT fire — only the shared-limit note."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    profile["flags"]["has_employer_retirement_plan"] = False
    profile["flags"]["has_employer_retirement_plan_spouse"] = False
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=500000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # Should ONLY say it shares the limit with Roth — no phase-out language
    note_lower = trad.note.lower()
    assert "phase" not in note_lower
    assert "non-deductible" not in note_lower


# ---------------------------------------------------------------------------
# Codex round 2 (PR #20): stale projection_inputs tax_year + Mega Backdoor
# over-contribution masking
# ---------------------------------------------------------------------------

def test_build_dashboard_rejects_stale_projection_inputs_year():
    """Same strict-validation rule applied to projection_inputs as to
    profile: a TY2025 inputs file copied to a TY2026 workspace would
    silently combine 2025 YTD figures with 2026 limits."""
    profile = _profile()
    inputs = _inputs()
    inputs["tax_year"] = 2025
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="projection_inputs"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
            tax_year=2026,
        )


def test_the_year_mismatch_errors_never_echo_a_malformed_year():
    """Same rule as `projection`'s year guard, on the same hand-edited files.

    Both comparisons here refused a non-year already — `not in (None,
    tax_year)` catches a dict as surely as a wrong int — but the refusal was
    built by interpolating the raw value, and /tax-retirement prints it. A
    taxpayer's name in a mistyped `tax_year` would have gone straight to the
    terminal: `redact` masks SSN and credential shapes only (AGENTS.md #1).
    A genuine wrong YEAR still prints, because which year the file is for is
    the entire content of the message.
    """
    secret = "Jane Q Taxpayer"
    law = _tax_law_2026()
    for key, holder in (("profile", "taxpayer_profile"),
                        ("inputs", "projection_inputs")):
        for bad in (secret, {"name": secret}, True):
            profile, inputs = _profile(), _inputs()
            (profile if key == "profile" else inputs)["tax_year"] = bad
            with pytest.raises(rd.RetirementError) as e:
                rd.build_dashboard(
                    profile, inputs, law, marginal_fed_rate=0.24,
                    marginal_state_rate=0.0, modified_agi=80000, tax_year=2026)
            assert "Jane" not in str(e.value), (
                f"{holder}.tax_year was echoed into the refusal: {e.value}")
            assert holder in str(e.value) and "TY2026" in str(e.value)

    # A real wrong year is still named — that is the message.
    profile, inputs = _profile(), _inputs()
    inputs["tax_year"] = 2025
    with pytest.raises(rd.RetirementError, match="TY2025"):
        rd.build_dashboard(profile, inputs, law, marginal_fed_rate=0.24,
                           marginal_state_rate=0.0, modified_agi=80000,
                           tax_year=2026)


def test_build_dashboard_accepts_missing_projection_inputs_year():
    """A projection_inputs file without an explicit tax_year is fine
    (the field is optional in the template). Only mismatched values
    should hard-error — same rule as for the profile."""
    profile = _profile()
    inputs = _inputs()
    inputs.pop("tax_year", None)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
        tax_year=2026,
    )
    assert report.year == 2026


def test_build_dashboard_accepts_null_projection_inputs_year():
    profile = _profile()
    inputs = _inputs()
    inputs["tax_year"] = None
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
        tax_year=2026,
    )
    assert report.year == 2026


def test_mega_backdoor_over_contributed_signals_excess():
    """Codex round-2 case: $23.5k regular + $50k employer match + $10k
    after-tax against a $70k §415(c) cap. The after-tax bucket is OVER
    by $10k. The dashboard must surface this — annual_limit must be
    the AVAILABLE cap ($0), not the YTD ratched back up to itself."""
    profile = _profile()
    inputs = _inputs(
        employee_401k=23500,
        employer_match=50000,
        after_tax_401k=10000,
        plan_allows_after_tax_401k=True,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    # Available cap = max(0, 70000 - 23500 - 50000) = 0
    assert mega.annual_limit == 0
    # YTD already at $10k, cap at $0 → excess = $10k
    assert mega.over_contributed == 10000
    assert mega.remaining == 0


def test_mega_backdoor_exactly_at_cap_no_excess():
    """Boundary: $23.5k regular + $0 match + $46.5k after-tax = $70k
    total. Exactly at §415(c) cap, no excess, no remaining."""
    profile = _profile()
    inputs = _inputs(
        employee_401k=23500,
        employer_match=0,
        after_tax_401k=46500,
        plan_allows_after_tax_401k=True,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 46500
    assert mega.over_contributed == 0
    assert mega.remaining == 0


def test_alabama_hsa_is_conforming():
    """Codex round-2 catch: Ala. Code §40-18-15.6 (effective 2018-01-01)
    allows AL residents to deduct HSA contributions from state income
    tax aligned with federal limits. AL conforms — the original
    non-conforming list was based on stale guidance."""
    profile = _profile()
    profile["address"]["state"] = "AL"
    inputs = _inputs(hsa_coverage="self_only")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.05,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.kind == "hsa_conforming"
    # State savings should NOT be zeroed
    assert hsa.marginal_state_saved > 0


def test_california_hsa_still_non_conforming():
    """Regression pin: CA non-conformity is unchanged."""
    profile = _profile()
    profile["address"]["state"] = "CA"
    inputs = _inputs(hsa_coverage="self_only")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.093,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.kind == "hsa_non_conforming"
    assert hsa.marginal_state_saved == 0


def test_new_jersey_hsa_still_non_conforming():
    """Regression pin: NJ non-conformity is unchanged."""
    profile = _profile()
    profile["address"]["state"] = "NJ"
    inputs = _inputs(hsa_coverage="self_only")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0637,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.kind == "hsa_non_conforming"
    assert hsa.marginal_state_saved == 0


def test_mega_backdoor_under_cap_shows_correct_remaining():
    """Regression pin for the normal case: under-cap should report
    remaining = cap - YTD, not be affected by the over-contributed fix."""
    profile = _profile()
    inputs = _inputs(
        employee_401k=20000,
        employer_match=10000,
        after_tax_401k=5000,
        plan_allows_after_tax_401k=True,
    )
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    # cap = max(0, 70000 - 20000 - 10000) = 40000
    # remaining = max(0, 40000 - 5000) = 35000
    assert mega.annual_limit == 40000
    assert mega.remaining == 35000
    assert mega.over_contributed == 0


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def test_render_markdown_includes_account_headers():
    profile = _profile()
    inputs = _inputs(employee_401k=10000)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.05,
        modified_agi=80000,
    )
    md = rd.render_markdown(report)
    assert "Retirement Contribution Dashboard" in md
    assert "401(k) employee" in md
    assert "Traditional IRA" in md
    assert "$10,000" in md or "10,000" in md


def test_render_markdown_surfaces_nonpooled_overcontribution():
    """A non-pooled account over its limit (e.g. a 401(k) elective deferral
    above $23,500) must get a corrective-action banner — not be silently
    shown as Remaining $0 / Used 100%, indistinguishable from being exactly
    maxed (Codex P2). The pool banner already covers IRA; this covers the
    non-pooled accounts (401(k) / HSA / Mega Backdoor)."""
    profile = _profile()  # age_50_plus False → single $23,500 401(k) limit
    inputs = _inputs(employee_401k=30000)  # $6,500 over the elective limit
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    row = next(a for a in report.accounts if a.name == "401(k) employee")
    assert row.over_contributed == 6500.0
    assert row.shared_pool_id is None  # non-pooled — not covered by pool banner
    md = rd.render_markdown(report)
    assert "over its limit" in md.lower()
    assert "$6,500" in md


def test_render_markdown_surfaces_roth_bucket_excess_under_pool_cap():
    """A direct Roth contribution above the MAGI-reduced Roth limit is an
    excess contribution even when the combined IRA pool is UNDER the annual
    cap (so the pool banner stays silent). Single + MAGI above the Roth
    phase-out end → $0 Roth limit; $1,000 Roth YTD, $0 Traditional → pool
    $1k < $7k cap, but the Roth bucket is $1k over. Must surface a
    corrective-action warning (Codex P2)."""
    profile = _profile()  # Single
    inputs = _inputs(ira_roth=1000, ira_traditional=0)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,  # > single Roth phase-out end ($165k) → $0 limit
    )
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    assert roth.annual_limit == 0.0  # fully phased out
    assert roth.over_contributed == 1000.0
    assert roth.pool_over_contributed == 0.0  # pool still under the $7k cap
    md = rd.render_markdown(report)
    assert "Roth IRA over its limit" in md
    assert "$1,000" in md


def test_render_markdown_no_double_report_for_traditional_pool_excess():
    """Regression guard for the Roth-bucket fix: a pure Traditional pool
    over-contribution must produce ONLY the pool banner, not a duplicate
    per-bucket banner (Traditional's bucket cap IS the pool cap, so its
    over_contributed never exceeds pool_over_contributed)."""
    profile = _profile()
    inputs = _inputs(ira_traditional=8000, ira_roth=0)  # $1k over the $7k pool
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    md = rd.render_markdown(report)
    assert "pool over-contributed" in md.lower()  # pool banner present
    # No separate per-bucket "over its limit" banner for the IRA buckets.
    assert "over its limit" not in md.lower()


def test_render_markdown_includes_total_potential_saved():
    profile = _profile()
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.05,
        modified_agi=80000,
    )
    md = rd.render_markdown(report)
    assert "Total potential" in md or "total potential" in md.lower()


def test_render_markdown_flags_hsa_non_conforming_state():
    profile = _profile()
    profile["address"]["state"] = "CA"
    inputs = _inputs(hsa_coverage="self_only")
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.093,
        modified_agi=80000,
    )
    md = rd.render_markdown(report)
    md_lower = md.lower()
    assert "non-conforming" in md_lower or "state-taxable" in md_lower


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_main_writes_markdown(tmp_path, capsys):
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    inputs_path = tmp_path / "projection_inputs.json"
    inputs_path.write_text(json.dumps(_inputs()))
    law_path = tmp_path / "tax_law_2026.json"
    law_path.write_text(json.dumps(_tax_law_2026()))

    rc = rd._main([
        "--profile", str(profile_path),
        "--inputs", str(inputs_path),
        "--tax-law", str(law_path),
        "--workspace", str(tmp_path),
        "--year", "2026",
        "--marginal-fed", "0.24",
        "--marginal-state", "0.0",
        "--magi", "80000",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Retirement Contribution Dashboard" in out


def test_main_rejects_invalid_marginal_rate(tmp_path, capsys):
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(_profile()))
    inputs_path = tmp_path / "projection_inputs.json"
    inputs_path.write_text(json.dumps(_inputs()))
    law_path = tmp_path / "tax_law_2026.json"
    law_path.write_text(json.dumps(_tax_law_2026()))

    rc = rd._main([
        "--profile", str(profile_path),
        "--inputs", str(inputs_path),
        "--tax-law", str(law_path),
        "--workspace", str(tmp_path),
        "--year", "2026",
        "--marginal-fed", "2.0",  # too high
        "--marginal-state", "0.0",
        "--magi", "80000",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:")


# ---------- Round-10: IRS Pub 590-A Worksheet 1-2 phase-out rounding ----------
# Steps 5-6: after multiplying limit × phase-out fraction, round UP to the
# next $10, then if the result is greater than $0 but less than $200, set
# it to $200. (At the upper bound, fraction is 0 → raw is 0 → no floor.)

def test_apply_irs_phaseout_rounding_applies_floor_to_tiny_amounts():
    """Pub 590-A Worksheet 1-2 step 6: any positive amount below $200 is
    raised to $200. Without this, an MFS-with-spouse taxpayer at MAGI
    $9,999 in a $0-$10k window sees $7000 × 0.0001 = $0.70 reported as
    their allowed contribution — 285× understatement of the $200 statute."""
    assert rd._apply_irs_phaseout_rounding(0.70) == 200.0
    assert rd._apply_irs_phaseout_rounding(0.01) == 200.0
    assert rd._apply_irs_phaseout_rounding(199.0) == 200.0


def test_apply_irs_phaseout_rounding_ceils_to_next_ten():
    """Pub 590-A Worksheet 1-2 step 5: round UP (ceil), not banker's
    rounding. $1,237 → $1,240; $1,750.70 → $1,760. Already-on-$10 amounts
    are unchanged."""
    assert rd._apply_irs_phaseout_rounding(1237.0) == 1240.0
    assert rd._apply_irs_phaseout_rounding(1750.70) == 1760.0
    assert rd._apply_irs_phaseout_rounding(1750.0) == 1750.0
    assert rd._apply_irs_phaseout_rounding(7000.0) == 7000.0


def test_apply_irs_phaseout_rounding_zero_unchanged_no_floor():
    """At/above the upper bound of the phase-out window, the IRS worksheet
    yields $0 and the $200 floor does NOT apply — the contribution is
    fully phased out. Helper must distinguish 0 from any positive amount."""
    assert rd._apply_irs_phaseout_rounding(0.0) == 0.0
    assert rd._apply_irs_phaseout_rounding(-0.0) == 0.0


def test_phaseout_fraction_unrounded():
    """_phaseout_fraction must return FULL precision — rounding it before
    the dollar multiply (combined with ceil-to-$10) can shift the cap a
    whole $10. Single Roth window $150k-$165k at MAGI $150,064: the exact
    fraction is 14936/15000, not the old 4-dp 0.9957 (Codex P2)."""
    frac = rd._phaseout_fraction(150064, 150000, 165000)
    assert frac == (165000 - 150064) / (165000 - 150000)
    assert frac != round(frac, 4)  # would have been the buggy value


def test_roth_phaseout_rounds_dollars_not_fraction():
    """End-to-end: IRS Pub 590-A rounds the reduced DOLLAR limit (ceil to
    $10), not the fraction. Single Roth, $7,000 limit, MAGI $150,064 in the
    $150k-$165k window → exact $6,970.13 → $6,980. Rounding the fraction to
    0.9957 first wrongly gave $6,970, understating capacity by $10 and
    risking a false excess flag (Codex P2)."""
    profile = _profile()  # Single, no catch-up → $7,000 IRA limit
    inputs = _inputs(ira_roth=0, ira_traditional=0)
    inputs["retirement_ytd"]["taxable_compensation"] = 200000  # uncap IRA
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=150064,
    )
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    assert roth.annual_limit == 6980.0  # not 6970.0


def test_roth_ira_mfs_near_upper_edge_floor_via_build_dashboard():
    """Codex round-10 example: MFS + lived with spouse + MAGI $9,999 lands
    1 dollar inside the $0-$10k Roth window. Raw fraction = 0.0001 means
    raw cap = $0.70; IRS floor lifts to $200. Without the fix the dashboard
    reports the user has essentially no Roth capacity left."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"]["lived_apart_from_spouse_all_year"] = False
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=9999,
    )
    roth = next(a for a in report.accounts if a.name == "Roth IRA")
    assert roth.annual_limit == 200.0


def test_traditional_ira_mfs_near_upper_edge_floor_via_build_dashboard():
    """Same Codex example but for the Traditional IRA deductible cap:
    MFS + covered + lived with spouse + MAGI $9,999 → deductible cap must
    be lifted to $200 by the Pub 590-A floor. Both IRA-cap call sites need
    the rounding helper (one for Roth eligibility, one for Trad
    deductibility) — and the pool-aware `marginal_eligible_dollars` must
    reflect the rounded cap, not the raw 0.0001-scaled cap."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"]["lived_apart_from_spouse_all_year"] = False
    profile["flags"]["has_employer_retirement_plan"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=9999,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # IRA pool limit is the full $7000 (or whatever 2026 ships); the
    # deductible CAP within that pool is what the floor lifts to $200.
    # marginal_eligible_dollars = min(pool_remaining, deductible_cap - ytd)
    # With ytd=0 and pool_remaining=$7000, the cap of $200 dominates.
    assert trad.marginal_eligible_dollars == 200.0


# ---------- Round-11 finding 1: MFS + spouse-covered + lived-with-spouse ----
# Pub 590-A Table 1-3 row: "You are not covered, your spouse is covered" +
# "MFS lived with spouse" → $0-$10,000 phase-out window (same as the row
# where the primary is covered). Round-3 fix added MFJ + spouse-covered,
# round-9 fix added MFS + lived-with-spouse + primary-covered. The
# intersection — MFS + lived-with-spouse + ONLY spouse covered — was the
# remaining gap. MFS + lived-apart + spouse-covered correctly stays at
# full deduction (Pub 590-A footnote: treated as Single for IRA purposes).

def test_traditional_ira_mfs_not_covered_spouse_covered_lived_with_spouse_uses_10k_window():
    """Codex round-11 finding 1: missing branch in
    compute_ira_traditional_deductible_fraction. With MFS + lived-with-
    spouse + NOT covered + spouse covered, Pub 590-A Table 1-3 applies
    the $0-$10K window. $5,000 MAGI → 50% deductible (was 100% pre-fix
    because the branch fell through to 'neither covered → unlimited')."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=5000, filing_status="MFS",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,
        lived_apart_from_spouse_all_year=False,
    )
    assert deductible == pytest.approx(0.5, abs=0.001)


def test_traditional_ira_mfs_not_covered_spouse_covered_lived_with_spouse_phases_at_high_magi():
    """Same MFS + lived-with + spouse-covered case, but MAGI above the
    $10K window → fully phased out. Pre-fix: 100% deductible (wrong)."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=50000, filing_status="MFS",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,
        lived_apart_from_spouse_all_year=False,
    )
    assert deductible == 0.0


def test_traditional_ira_mfs_not_covered_spouse_covered_lived_apart_full_deduction():
    """MFS + lived APART all year + NOT covered + spouse covered: per
    Pub 590-A footnote, lived-apart MFS is treated as Single for IRA
    purposes, and a Single not-covered taxpayer faces no phase-out
    regardless of spouse. Verifies the new branch is narrowly scoped
    to lived-with-spouse and does not regress lived-apart."""
    law = _tax_law_2026()
    deductible = rd.compute_ira_traditional_deductible_fraction(
        law, modified_agi=500000, filing_status="MFS",
        covered_by_employer_plan=False,
        spouse_covered_by_employer_plan=True,
        lived_apart_from_spouse_all_year=True,
    )
    assert deductible == 1.0


# ---------- Round-11 finding 2: strict input-block typing ------------------
# `or {}` collapses falsy non-dict values ([], "", 0) to {} BEFORE the
# isinstance check fires — silently treats malformed workspace files as
# empty objects, may produce materially wrong remaining-capacity numbers.
# Three sites in build_dashboard: profile.flags (line 570), profile.address
# (line 576), projection_inputs.retirement_ytd (line 592).

def test_build_dashboard_rejects_malformed_flags_list():
    """profile.flags = [] (falsy non-dict) was silently coerced to {} via
    `or {}`. A 50+ taxpayer's age flag would be lost, understating their
    401(k) limit by the $7,500 catch-up. Must hard-error instead."""
    profile = _profile()
    profile["flags"] = []
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="profile.flags"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_malformed_address_empty_string():
    """profile.address = '' was silently coerced to {} via `or {}`,
    erasing the state code. For CA/NJ residents this would silently
    flip HSA from non-conforming to conforming, overstating state
    savings. Must hard-error instead."""
    profile = _profile()
    profile["address"] = ""
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="profile.address"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_malformed_retirement_ytd_zero():
    """projection_inputs.retirement_ytd = 0 was silently coerced to {}
    via `or {}`, zeroing all YTD contributions. A taxpayer who maxed
    their 401(k) by mid-year would see full $23,500 capacity remaining
    and a phantom $5,640 fed-savings recommendation. Must hard-error."""
    profile = _profile()
    inputs = _inputs()
    inputs["retirement_ytd"] = 0
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="retirement_ytd"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


# ---------- Proactive: filing_status strict typing (same pattern as f2) ----
# Same `value or default` generalization-gap as round-11 finding 2:
# `profile.get("filing_status") or "Single"` silently coerces falsy
# non-string values (`0`, `[]`, `""`) to "Single". Since filing_status
# drives nearly every downstream computation (brackets, IRA phase-out
# windows, MFS-specific case handling), a malformed value is the
# HIGHEST-impact silent failure mode in the file. Codex flagged 3 of 4
# `or default` sites in round 11; this proactive fix closes the 4th.

def test_build_dashboard_rejects_malformed_filing_status_zero():
    """profile.filing_status = 0 was silently coerced to 'Single' via
    `or "Single"`. An MFS taxpayer with a hand-edit bug would silently
    get Single brackets — wrong tax computation, wrong IRA phase-out
    windows, wrong 401(k) catch-up handling. Must hard-error."""
    profile = _profile()
    profile["filing_status"] = 0
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="filing_status"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_malformed_filing_status_list():
    """profile.filing_status = [] is the same falsy-non-string class as
    the round-11 finding-2 sites. Must hard-error rather than coerce."""
    profile = _profile()
    profile["filing_status"] = []
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="filing_status"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_accepts_missing_filing_status_default_single():
    """Regression guard: missing/None filing_status SHOULD default to
    'Single' (the original benign reason for the `or` pattern). Only
    falsy NON-string values should hard-error. Verifies the fix is
    narrowly scoped — doesn't break the common workspace where
    filing_status is implicitly Single."""
    profile = _profile()
    profile.pop("filing_status", None)
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    # Should run cleanly; default Single brackets visible in report header
    assert report.filing_status == "Single"


def test_build_dashboard_rejects_noncanonical_filing_status():
    """profile.filing_status that is type-valid (a string) but not a
    recognized status — e.g. a hand-entered full name or empty string —
    must hard-error. Downstream only matches MFJ/MFS/QSS and falls back to
    Single, so 'Married Filing Jointly' would silently apply Single
    phase-out windows AND skip the MFS lived-apart hard-error (Codex P2)."""
    law = _tax_law_2026()
    inputs = _inputs()
    for bad in ("Married Filing Jointly", "", "Jointly", "MFJX"):
        profile = _profile()
        profile["filing_status"] = bad
        with pytest.raises(rd.RetirementError, match="filing_status"):
            rd.build_dashboard(
                profile, inputs, law,
                marginal_fed_rate=0.24, marginal_state_rate=0.0,
                modified_agi=80000,
            )


def test_build_dashboard_accepts_filing_status_case_and_whitespace():
    """A canonical status with surrounding whitespace / non-canonical case
    is accepted and normalized so downstream `.upper()` comparisons (which
    do not strip) still match — i.e. ' MFJ ' is treated as MFJ, not
    silently mis-bracketed to Single."""
    profile = _profile()
    profile["filing_status"] = " MFJ "
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    assert report.filing_status == "MFJ"  # whitespace stripped


def test_profile_template_has_no_duplicate_json_keys():
    """The taxpayer_profile template must not contain duplicate keys in any
    object. JSON parsers keep only the LAST occurrence, so a duplicate
    has_employer_retirement_plan would let a later default-False silently
    override a user's True — treating a covered taxpayer as NOT covered and
    overstating Traditional IRA deductibility (Codex P2). Detected at the
    raw-pairs level since json.load() silently dedupes."""
    template_path = (
        Path(__file__).resolve().parent.parent
        / "agents" / "templates" / "taxpayer_profile_template.json"
    )
    dupes: list[str] = []

    def _detect_dupes(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen:
                dupes.append(key)
            seen.add(key)
        return dict(pairs)

    json.loads(template_path.read_text(), object_pairs_hook=_detect_dupes)
    assert dupes == [], f"duplicate JSON keys in profile template: {dupes}"


# ---------- Round-12 finding 1: Mega Backdoor §415(c) accounting -----------
# Per IRC §414(v), catch-up contributions are EXCLUDED from the §415(c)
# annual-additions limit. The pre-fix `min(ytd_401k, base_elective)`
# excluded EVERYTHING above the base elective limit as if it were catch-up
# — regardless of whether the taxpayer was age-eligible. For an under-50
# taxpayer who's somehow contributed above base (excess deferral pending
# correction), this overstates Mega Backdoor capacity precisely in the
# scenario the dashboard exists to surface. Correct rule: only the
# AGE-ELIGIBLE catch-up portion is excluded; any excess above eligible
# catch-up DOES consume §415(c) space until corrected.

def test_mega_backdoor_under_50_excess_deferral_consumes_415c_space():
    """Under-50 taxpayer with ytd_401k = $30k has $6.5k excess deferral
    (no catch-up eligibility). That $6.5k IS still an annual addition
    until withdrawn → consumes §415(c) space. Pre-fix bug: dashboard
    reported after_tax_cap = $70k - $23.5k = $46.5k (treats excess as
    catch-up). Correct: $70k - $30k = $40k (excess deferral consumes
    415c)."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = False
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["employee_401k"] = 30000
    inputs["retirement_ytd"]["employer_match"] = 0
    inputs["retirement_ytd"]["after_tax_401k"] = 0
    inputs["retirement_ytd"]["plan_allows_after_tax_401k"] = True
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    # $70k total annual addition - $30k full ytd_401k (all 415c-consuming
    # since no catch-up eligibility) - $0 match = $40k
    assert mega.annual_limit == 40000.0


def test_mega_backdoor_age_50_excludes_eligible_catchup_from_415c():
    """Age-50 taxpayer with ytd_401k = $30k: $6.5k above base IS legal
    catch-up (≤ $7.5k catch_up_50_plus). Per §414(v) catch-up is
    excluded from §415(c). non_catchup = $23.5k, after_tax_cap =
    $70k - $23.5k = $46.5k. This is the case the pre-fix logic
    happened to compute correctly — locked in as a regression guard."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["employee_401k"] = 30000
    inputs["retirement_ytd"]["employer_match"] = 0
    inputs["retirement_ytd"]["after_tax_401k"] = 0
    inputs["retirement_ytd"]["plan_allows_after_tax_401k"] = True
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 46500.0


def test_mega_backdoor_age_50_excess_above_catchup_consumes_415c():
    """Age-50 taxpayer with ytd_401k = $35k: $11.5k above base, but
    catch_up_50_plus is $7.5k → $7.5k is legal catch-up (excluded),
    $4k is excess deferral (consumes 415c). Pre-fix bug: clamps to
    $23.5k, reports $46.5k cap. Correct: non_catchup = $27.5k,
    cap = $70k - $27.5k = $42.5k."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["employee_401k"] = 35000
    inputs["retirement_ytd"]["employer_match"] = 0
    inputs["retirement_ytd"]["after_tax_401k"] = 0
    inputs["retirement_ytd"]["plan_allows_after_tax_401k"] = True
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    mega = next(a for a in report.accounts if a.name == "Mega Backdoor")
    assert mega.annual_limit == 42500.0


# ---------- Round-12 finding 2: HSA spouse catch-up requires actual spouse --
# `age_55_plus_spouse` is an entity-property flag (about the spouse).
# Currently the code adds the $1,000 spouse HSA catch-up whenever the
# flag is True + family coverage, without verifying the filer actually
# HAS a spouse. A Single/HoH/QSS filer with family HDHP (legitimately
# possible — unmarried HoH with dependents on plan) + stale-from-divorce
# age_55_plus_spouse flag silently gets a phantom $1,000. Only filing
# statuses with an actual living spouse (MFJ, MFS) should trigger the
# spouse catch-up. QSS = Qualifying Surviving Spouse → spouse is
# deceased → no spouse contribution possible.

def test_hsa_skips_spouse_catchup_for_single_filer():
    """Single + family HDHP + stale age_55_plus_spouse=True: no actual
    spouse exists → spouse catch-up MUST be skipped. Pre-fix bug adds
    phantom $1,000 capacity."""
    profile = _profile()
    profile["filing_status"] = "Single"
    profile["flags"]["age_55_plus_spouse"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["hsa_coverage"] = "family"
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    # Family HSA limit only (no spouse $1k added)
    family_limit = law["federal"]["hsa_limits"]["family"]
    assert hsa.annual_limit == family_limit


def test_hsa_skips_spouse_catchup_for_hoh_filer():
    """HoH + family HDHP + stale age_55_plus_spouse=True: HoH has no
    spouse by definition → spouse catch-up MUST be skipped."""
    profile = _profile()
    profile["filing_status"] = "HoH"
    profile["flags"]["age_55_plus_spouse"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["hsa_coverage"] = "family"
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    family_limit = law["federal"]["hsa_limits"]["family"]
    assert hsa.annual_limit == family_limit


def test_hsa_includes_spouse_catchup_for_mfj():
    """Regression guard: MFJ + family HDHP + age_55_plus_spouse=True is
    the LEGITIMATE case for adding the spouse $1k catch-up. Per IRS
    Pub 969 a 55+ spouse can make a separate $1k catch-up into their
    own HSA. Verifies the fix doesn't over-restrict."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    profile["flags"]["age_55_plus_spouse"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["hsa_coverage"] = "family"
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    family_limit = law["federal"]["hsa_limits"]["family"]
    spouse_catchup = law["federal"]["hsa_limits"]["catch_up_55_plus"]
    assert hsa.annual_limit == family_limit + spouse_catchup


def test_hsa_spouse_catchup_from_age_60_63_spouse_flag_only():
    """The template defines age_60_63_spouse (symmetric with the primary),
    and a 60-63 spouse is unambiguously 55+ — the HSA catch-up is a flat
    $1k at 55+ with no 60-63 enhancement. A profile that sets ONLY
    age_60_63_spouse (leaving age_55_plus_spouse at the False default) must
    still get the spouse's $1k HSA catch-up. Pre-fix the code read only
    age_55_plus_spouse and silently dropped it (Codex P2)."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    profile["flags"]["age_55_plus_spouse"] = False
    profile["flags"]["age_60_63_spouse"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["hsa_coverage"] = "family"
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    family_limit = law["federal"]["hsa_limits"]["family"]
    spouse_catchup = law["federal"]["hsa_limits"]["catch_up_55_plus"]
    # Primary is not 55+, so only the spouse catch-up is added.
    assert hsa.annual_limit == family_limit + spouse_catchup


def test_hsa_skips_spouse_catchup_for_mfs():
    """MFS + family HDHP + age_55_plus_spouse=True: the spouse has a living
    HSA, but each MFS spouse files a SEPARATE return, so the spouse's $1k
    catch-up belongs on the spouse's OWN return — it must NOT inflate the
    MFS primary's own capacity. Only the primary's own catch-up (if 55+)
    applies here. Primary is not 55+ in the default profile, so the limit
    is the bare family limit."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"]["lived_apart_from_spouse_all_year"] = True  # MFS prereq
    profile["flags"]["age_55_plus_spouse"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["hsa_coverage"] = "family"
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    hsa = next(a for a in report.accounts if a.name == "HSA")
    family_limit = law["federal"]["hsa_limits"]["family"]
    # No spouse catch-up on an MFS return (spouse files separately).
    assert hsa.annual_limit == family_limit


# ---------- Round-13 finding 1: SECURE 2.0 §603 mandatory Roth catch-up ----
# Per IRC §414(v)(7)(A) (added by SECURE 2.0 §603, effective TY2026 per
# IRS Notice 2023-62): participants with prior-year FICA wages above
# $145k (indexed; projected $150k for TY2026) MUST make 401(k) catch-up
# contributions as designated Roth. No current-year federal/state
# deduction on catch-up dollars for high earners. Dashboard splits the
# 401(k) row into a pre-tax base + Roth catch-up row when applicable.

def test_compute_401k_catchup_amount_under_50():
    """No catch-up eligibility → 0."""
    law = _tax_law_2026()
    assert rd.compute_401k_catchup_amount(
        law, age_50_plus=False, age_60_63=False
    ) == 0.0


def test_compute_401k_catchup_amount_age_50():
    law = _tax_law_2026()
    assert rd.compute_401k_catchup_amount(
        law, age_50_plus=True, age_60_63=False
    ) == 7500.0


def test_compute_401k_catchup_amount_age_60_63_supersedes_standard():
    """SECURE 2.0: 60-63 super catch-up REPLACES 50+ standard (does not
    stack). Even when both age flags are True, only super applies."""
    law = _tax_law_2026()
    assert rd.compute_401k_catchup_amount(
        law, age_50_plus=True, age_60_63=True
    ) == 11250.0


def test_age_60_63_only_flag_still_grants_ira_and_hsa_catchups():
    """Age windows are NESTED: 60-63 implies 55+ implies 50+. A taxpayer who
    sets ONLY age_60_63_primary (leaving the broader flags at the template's
    False default) must still get the IRA ($1k) and HSA ($1k) catch-ups —
    not just the 401(k) super catch-up. Pre-fix the IRA/HSA sections read
    age_50/age_55 directly and silently omitted them, understating capacity
    (Codex P2). build_dashboard now propagates the implication."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = False
    profile["flags"]["age_55_plus_primary"] = False
    profile["flags"]["age_60_63_primary"] = True
    inputs = _inputs(hsa_coverage="self_only")
    inputs["retirement_ytd"]["taxable_compensation"] = 100000  # uncap IRA
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=100000,
    )
    # 401(k): super catch-up (unchanged — supersession preserved)
    k401 = next(a for a in report.accounts if a.name == "401(k) employee")
    assert k401.annual_limit == 23500.0 + 11250.0
    # Traditional IRA: $7,000 + $1,000 catch-up (was $7,000 pre-fix)
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.annual_limit == 8000.0
    # HSA self-only: $4,300 + $1,000 catch-up (was $4,300 pre-fix)
    hsa = next(a for a in report.accounts if a.name == "HSA")
    assert hsa.annual_limit == 5300.0


def test_401k_high_earner_splits_into_base_and_roth_catchup_rows():
    """Codex round-13 finding 1: age-50+ + prior_year_fica_wages > $150k
    → 401(k) row splits into 'base' (pre-tax) + 'catch-up' (Roth, $0
    current-year deduction). Pre-fix: single row with kind='pre_tax'
    on full $31k limit overstated the catch-up's marginal saved."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["employee_401k"] = 0
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.05,
        modified_agi=200000,
    )
    base = next(
        a for a in report.accounts if a.name == "401(k) employee (base)"
    )
    catchup = next(
        a for a in report.accounts
        if a.name == "401(k) catch-up (Roth required)"
    )
    # Base row: pre-tax, $23.5k limit, full marginal savings
    assert base.annual_limit == 23500.0
    assert base.kind == "pre_tax"
    assert base.marginal_fed_saved == round(23500 * 0.24, 2)
    # Catch-up row: Roth, $7.5k limit, $0 current-year deduction
    assert catchup.annual_limit == 7500.0
    assert catchup.kind == "roth"
    assert catchup.marginal_fed_saved == 0.0
    assert catchup.marginal_state_saved == 0.0


def test_high_earner_catchup_unavailable_without_roth_source():
    """SECURE 2.0 §603: if a high earner's catch-up MUST be Roth but the
    plan has no designated Roth source (plan_offers_roth_401k=False), the
    catch-up cannot be made at all. The dashboard must show base-only — NOT
    a phantom positive catch-up row that overstates capacity by the full
    catch-up amount (Codex P2)."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    inputs["retirement_ytd"]["plan_offers_roth_401k"] = False
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1  # no split, no catch-up row
    assert rows_401k[0].name == "401(k) employee"
    assert rows_401k[0].annual_limit == 23500.0  # base only, NO catch-up
    assert "Catch-up UNAVAILABLE" in rows_401k[0].note


def test_high_earner_catchup_splits_when_roth_source_true():
    """Regression guard: plan_offers_roth_401k=True (or absent → default
    True) still splits into base + Roth catch-up."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    inputs["retirement_ytd"]["plan_offers_roth_401k"] = True
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    assert any(
        a.name == "401(k) catch-up (Roth required)" for a in report.accounts
    )


def test_high_earner_catchup_note_caveats_roth_source_when_absent():
    """When plan_offers_roth_401k is absent (default True → split shown),
    the catch-up note must caveat that it requires a Roth source and point
    to the flag, so a non-Roth-plan user knows to set it false."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    inputs["retirement_ytd"].pop("plan_offers_roth_401k", None)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    catchup = next(
        a for a in report.accounts
        if a.name == "401(k) catch-up (Roth required)"
    )
    assert "plan_offers_roth_401k" in catchup.note


def test_401k_high_earner_note_caveats_plan_sponsor_wages():
    """SECURE 2.0 §603 is keyed to FICA wages from the plan-sponsoring
    employer (IRC §414(v)(7)(A)), not aggregate across employers. The
    catch-up note must surface that caveat so a job-changer / multi-
    employer filer who supplied aggregate wages isn't silently
    misclassified into the Roth row (Codex P2)."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.05,
        modified_agi=200000,
    )
    catchup = next(
        a for a in report.accounts
        if a.name == "401(k) catch-up (Roth required)"
    )
    note = catchup.note.lower()
    assert "sponsor" in note  # caveat present
    assert "aggregate" in note


def test_catchup_roth_note_caveats_prior_roth_deferrals():
    """Per the final regs Treas. Reg. §1.414(v)-2(b)(1), the catch-up must
    be Roth only to the extent prior designated-Roth deferrals are BELOW the
    catch-up limit. The dashboard treats the whole catch-up as Roth ($0
    savings); it must caveat that a saver who already made enough Roth
    deferrals may make pre-tax catch-up, so the $0 savings may be
    understated (Codex P2)."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.05,
        modified_agi=200000,
    )
    catchup = next(
        a for a in report.accounts
        if a.name == "401(k) catch-up (Roth required)"
    )
    assert "1.414(v)-2(b)(1)" in catchup.note
    assert "UNDERSTATE" in catchup.note


def test_pa_401k_and_ira_show_zero_state_savings():
    """PA taxes pre-tax 401(k)/403(b) deferrals and disallows the IRA
    deduction, so a PA resident gets $0 STATE savings on those rows even
    though the federal benefit still applies. Pre-fix the dashboard treated
    every pre_tax row as state-deductible, showing phantom PA state savings
    (Codex P2)."""
    profile = _profile()
    profile["address"]["state"] = "PA"
    inputs = _inputs(employee_401k=0, ira_traditional=0)
    inputs["retirement_ytd"]["taxable_compensation"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0307,  # PA flat PIT
        modified_agi=100000,
    )
    k401 = next(a for a in report.accounts if a.name == "401(k) employee")
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert k401.marginal_state_saved == 0.0
    assert trad.marginal_state_saved == 0.0
    assert k401.marginal_fed_saved > 0  # federal benefit still applies
    assert "State PA does not allow" in k401.note


def test_conforming_state_still_shows_401k_state_savings():
    """Regression guard: a state NOT in the retirement-non-conforming set
    (e.g. NY, which allows the 401(k) deferral exclusion) still shows
    positive 401(k) state savings on the pre-tax row."""
    profile = _profile()
    profile["address"]["state"] = "NY"
    inputs = _inputs(employee_401k=0)
    inputs["retirement_ytd"]["taxable_compensation"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0685,
        modified_agi=100000,
    )
    k401 = next(a for a in report.accounts if a.name == "401(k) employee")
    assert k401.marginal_state_saved > 0
    assert "taxes pre-tax retirement" not in k401.note


def test_nj_ma_disallow_ira_deduction_but_allow_401k_state_savings():
    """Per-account state conformity differs: NJ and MA disallow the
    Traditional IRA deduction (IRA row → $0 state savings) but ALLOW the
    401(k) deferral exclusion (401(k) row → state savings present). A single
    state set would wrongly zero or wrongly credit both; they need separate
    handling (Codex P2)."""
    for state, rate in (("NJ", 0.0637), ("MA", 0.05)):
        profile = _profile()
        profile["address"]["state"] = state
        inputs = _inputs(employee_401k=0, ira_traditional=0)
        inputs["retirement_ytd"]["taxable_compensation"] = 200000
        law = _tax_law_2026()
        report = rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=rate,
            modified_agi=100000,
        )
        k401 = next(a for a in report.accounts if a.name == "401(k) employee")
        trad = next(a for a in report.accounts if a.name == "Traditional IRA")
        assert k401.marginal_state_saved > 0, state  # 401(k) deferral allowed
        assert trad.marginal_state_saved == 0.0, state  # IRA deduction disallowed
        assert "does not allow the federal treatment of Traditional IRA deduction" in trad.note


def test_401k_missing_wage_warning_caveats_plan_sponsor():
    """The missing-wage warning must also point the user to supply the
    plan-sponsor figure (not aggregate), so they provide the statutorily
    correct number (Codex P2)."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["retirement_ytd"].pop("prior_year_fica_wages", None)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=100000,
    )
    row = next(a for a in report.accounts if a.name == "401(k) employee")
    note = row.note.lower()
    assert "sponsoring employer" in note or "this plan's sponsoring" in note
    assert "aggregate" in note


def test_401k_high_earner_under_50_no_split():
    """Under-50 + FICA wages > $150k: no catch-up eligibility, so no
    split — single row as before. SECURE 2.0 §603 only affects
    catch-up dollars; under-50 has no catch-up to convert."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = False
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    # Single row with base elective limit only
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1
    assert rows_401k[0].name == "401(k) employee"
    assert rows_401k[0].annual_limit == 23500.0


def test_401k_below_high_earner_threshold_no_split():
    """Age-50 + FICA wages BELOW $150k: catch-up is still pre-tax →
    existing single-row behavior preserved (regression guard)."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 100000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=100000,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1
    assert rows_401k[0].name == "401(k) employee"
    # Combined limit: base + catch-up, single pre-tax row
    assert rows_401k[0].annual_limit == 23500.0 + 7500.0
    assert rows_401k[0].kind == "pre_tax"


def test_401k_high_earner_at_exactly_threshold_no_split_boundary():
    """FICA wages EXACTLY at the $150k threshold: the §603 gate is a
    strict `>` (prior_year_fica_wages > high_earner_threshold), so a
    taxpayer landing precisely on the threshold is NOT a high earner and
    keeps the single pre-tax catch-up row. Pins the boundary so a future
    indexing change flipping `>` to `>=` (or vice versa) is caught."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 150000  # == threshold
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=150000,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1  # no split at exactly $150k
    assert rows_401k[0].name == "401(k) employee"
    assert rows_401k[0].kind == "pre_tax"


def test_401k_catchup_eligible_no_wage_data_emits_warning():
    """Age-50 + prior_year_fica_wages NOT provided: cannot determine
    high-earner status. Existing behavior preserved (assume pre-tax
    catch-up, may overstate savings) but emit a warning note pointing
    the user to provide the field for accurate accounting."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"].pop("prior_year_fica_wages", None)
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=100000,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1
    assert "SECURE 2.0" in rows_401k[0].note
    assert "prior_year_fica_wages" in rows_401k[0].note


def test_401k_high_earner_age_60_63_uses_super_catchup_for_split():
    """Age 60-63 + high-earner: split row uses the SUPER catch-up
    ($11,250) not the standard ($7,500). SECURE 2.0 60-63 super
    catch-up SUPERSEDES (does not stack with) 50+ standard."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    catchup = next(
        a for a in report.accounts
        if a.name == "401(k) catch-up (Roth required)"
    )
    assert catchup.annual_limit == 11250.0
    assert catchup.kind == "roth"


def test_401k_high_earner_ytd_correctly_allocated_across_rows():
    """When ytd_401k = $26k for age-50 high-earner: base row shows
    ytd=$23.5k (full base), catch-up row shows ytd=$2.5k (overflow).
    Correctly attributes the over-base portion to the catch-up bucket."""
    profile = _profile()
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["employee_401k"] = 26000
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    base = next(
        a for a in report.accounts if a.name == "401(k) employee (base)"
    )
    catchup = next(
        a for a in report.accounts
        if a.name == "401(k) catch-up (Roth required)"
    )
    assert base.ytd_contributed == 23500.0
    assert catchup.ytd_contributed == 2500.0


def test_build_dashboard_rejects_malformed_prior_year_fica_wages():
    """The new prior_year_fica_wages field follows the strict parse-
    boundary rule: bool / non-numeric / negative all hard-error."""
    profile = _profile()
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = "200000"  # str not num
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="prior_year_fica_wages"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


# ---------- Round-13 finding 2: address.state strict typing -----------------
# `address.get("state") or ""` collapses falsy non-string values BEFORE
# the isinstance check fires — silently flips CA/NJ HSA conformity to
# "conforming" and overstates state savings. Same fix pattern as
# round-11 finding 2 (profile.flags / profile.address / retirement_ytd).
# This is the site I dismissed as "lower impact" in the round-11
# proactive audit insight — empirical refutation of that risk
# prioritization.

def test_build_dashboard_rejects_malformed_address_state_zero():
    """address.state = 0 was silently coerced to '' via `or ""`,
    causing CA/NJ residents' HSA contributions to silently flip from
    'non-conforming' to 'conforming', overstating state savings."""
    profile = _profile()
    profile["address"]["state"] = 0
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="address.state"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_malformed_address_state_list():
    """address.state = [] is the same falsy-non-string class."""
    profile = _profile()
    profile["address"]["state"] = []
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="address.state"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_accepts_missing_address_state_default_empty():
    """Regression guard: missing/None address.state correctly defaults
    to no state (HSA non-conformity not applicable). Only falsy
    NON-string values should hard-error."""
    profile = _profile()
    profile["address"].pop("state", None)
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    assert report.state == ""


def test_build_dashboard_rejects_full_state_name():
    """address.state as a full name ('California', 'New Jersey') is
    type-valid but uppercases to 'CALIFORNIA'/'NEW JERSEY', misses
    _HSA_NON_CONFORMING_STATES, and would report CA/NJ HSA as state-
    deductible — overstating state savings. Must hard-error; the user
    should supply the 2-letter postal code (Codex P2)."""
    law = _tax_law_2026()
    inputs = _inputs()
    inputs["retirement_ytd"]["hsa_coverage"] = "family"
    for bad in ("California", "New Jersey", "Calif", "C"):
        profile = _profile()
        profile["address"]["state"] = bad
        with pytest.raises(rd.RetirementError, match="address.state"):
            rd.build_dashboard(
                profile, inputs, law,
                marginal_fed_rate=0.24, marginal_state_rate=0.10,
                modified_agi=80000,
            )


def test_build_dashboard_accepts_two_letter_state_with_whitespace():
    """A 2-letter postal code with surrounding whitespace / lower case is
    normalized and honored — ' nj ' must still trigger NJ HSA
    non-conformity, not be rejected or treated as conforming."""
    profile = _profile()
    profile["address"]["state"] = " nj "
    inputs = _inputs()
    inputs["retirement_ytd"]["hsa_coverage"] = "family"
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.10,
        modified_agi=80000,
    )
    assert report.state == "NJ"
    hsa = next(a for a in report.accounts if a.name == "HSA")
    # NJ is non-conforming → $0 state savings on HSA dollars.
    assert hsa.marginal_state_saved == 0.0


# ---------- Round-14: SECURE 2.0 §603 effective-year gate ------------------
# My round-13 fix (commit 329ac22) added the split logic + the constant
# _SECURE_2_0_EFFECTIVE_YEAR = 2026 but did NOT use the constant in the
# predicate. For TY2024 or TY2025 dashboards with an age-50+ high earner,
# the row was incorrectly split into Roth-only catch-up — but per IRS
# Notice 2023-62 the rule was DELAYED from 2024 to TY2026 (taxable years
# beginning after Dec 31, 2025). Pre-2026 catch-up is still pre-tax.
# Codex round-14 finding: gate the predicate on tax_year >= 2026.

def test_401k_high_earner_tax_year_2025_does_not_split():
    """TY2025 + age-50 high earner: per IRS Notice 2023-62 the SECURE
    2.0 §603 Roth catch-up requirement was delayed and does NOT apply
    until 2026. Catch-up is still PRE-TAX for 2025. Pre-fix bug: split
    row reported $0 catch-up savings → understated TY2025 dashboards."""
    profile = _profile()
    profile["tax_year"] = 2025
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["tax_year"] = 2025
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    law["tax_year"] = 2025
    law["_meta"]["tax_year"] = 2025
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
        tax_year=2025,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1
    assert rows_401k[0].name == "401(k) employee"
    assert rows_401k[0].kind == "pre_tax"
    # Combined limit: base + catch-up, single pre-tax row, full savings
    assert rows_401k[0].annual_limit == 23500.0 + 7500.0


def test_401k_high_earner_tax_year_2024_does_not_split():
    """TY2024 + age-50 high earner: same as 2025, in the Notice 2023-62
    transition window. No split, pre-tax catch-up."""
    profile = _profile()
    profile["tax_year"] = 2024
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["tax_year"] = 2024
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    law["tax_year"] = 2024
    law["_meta"]["tax_year"] = 2024
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
        tax_year=2024,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1
    assert rows_401k[0].kind == "pre_tax"


def test_401k_super_catchup_gated_by_effective_year_pre_2025():
    """The §109 age-60-63 super catch-up AMOUNT is effective TY2025+ (NOT
    delayed like §603). A TY2024 dashboard with age_60_63_primary must NOT
    use catch_up_60_63 — which is absent from a pre-2025 tax-law cache and
    would otherwise hard-error — the participant gets the standard 50+
    catch-up instead (Codex P2)."""
    profile = _profile()
    profile["tax_year"] = 2024
    profile["flags"]["age_50_plus_primary"] = False
    profile["flags"]["age_60_63_primary"] = True
    inputs = _inputs()
    inputs["tax_year"] = 2024
    law = _tax_law_2026()
    law["tax_year"] = 2024
    law["_meta"]["tax_year"] = 2024
    del law["federal"]["retirement_401k"]["catch_up_60_63"]  # absent pre-2025
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
        tax_year=2024,
    )
    k401 = next(a for a in report.accounts if a.name == "401(k) employee")
    # Standard 50+ catch-up ($7,500), NOT the absent super amount — and no
    # hard-error despite catch_up_60_63 being missing from the cache.
    assert k401.annual_limit == 23500.0 + 7500.0


def test_401k_super_catchup_applies_ty2026():
    """Regression guard: TY2026 (>= the 2025 super-catchup effective year)
    age_60_63 still gets the super catch-up amount."""
    profile = _profile()
    profile["flags"]["age_60_63_primary"] = True
    inputs = _inputs()
    law = _tax_law_2026()  # tax_year 2026
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    k401 = next(a for a in report.accounts if a.name == "401(k) employee")
    assert k401.annual_limit == 23500.0 + 11250.0  # super catch-up


def test_high_earner_threshold_post_2026_requires_indexed_override():
    """The built-in $150k threshold is the TY2026 value and the §603
    threshold indexes annually. A TY2027 run on a cache lacking
    secure_2_0_high_earner_threshold must hard-error rather than silently
    reuse $150k (which would misclassify $150k-$155k earners into a
    mandatory-Roth catch-up row) (Codex P2)."""
    profile = _profile()
    profile["tax_year"] = 2027
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["tax_year"] = 2027
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 152000
    law = _tax_law_2026()
    law["tax_year"] = 2027
    law["_meta"]["tax_year"] = 2027
    del law["federal"]["retirement_401k"]["secure_2_0_high_earner_threshold"]
    # The indexed value is required for every covered year.
    with pytest.raises(
        rd.RetirementError, match="secure_2_0_high_earner_threshold"
    ):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=152000, tax_year=2027,
        )


def test_under_50_post_2026_no_threshold_error_when_no_catchup():
    """An under-50 taxpayer (catchup_amount==0) never uses the §603
    high-earner threshold, so a TY2027 cache lacking the indexed threshold
    must NOT block their dashboard — the lookup is deferred until catch-up
    classification is actually needed (Codex P2)."""
    profile = _profile()  # under 50 — no catch-up
    profile["tax_year"] = 2027
    inputs = _inputs()
    inputs["tax_year"] = 2027
    law = _tax_law_2026()
    law["tax_year"] = 2027
    law["_meta"]["tax_year"] = 2027
    del law["federal"]["retirement_401k"]["secure_2_0_high_earner_threshold"]
    # The indexed value is required for every covered year. — must NOT error here
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000, tax_year=2027,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1  # renders cleanly, single row, no split


def test_tax_law_schema_includes_secure_2_0_high_earner_threshold():
    """The post-2026 hard-error tells users to re-fetch via /tax-setup, so
    the tax_law schema MUST include a slot for the §603 high-earner
    threshold — else a fresh 2027 workspace can't populate it and the
    dashboard dead-ends (Codex P2)."""
    from agents.lib.tax_law_fetcher import create_empty_tax_law
    law = create_empty_tax_law(2027)
    assert (
        "secure_2_0_high_earner_threshold"
        in law["federal"]["retirement_401k"]
    )


def test_high_earner_threshold_post_2026_uses_cache_override():
    """TY2027 WITH the indexed override in the cache uses it (no error); an
    earner below the indexed threshold ($152k < $155k) is NOT split into a
    mandatory-Roth row."""
    profile = _profile()
    profile["tax_year"] = 2027
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["tax_year"] = 2027
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 152000  # below 155k
    law = _tax_law_2026()
    law["tax_year"] = 2027
    law["_meta"]["tax_year"] = 2027
    law["federal"]["retirement_401k"]["secure_2_0_high_earner_threshold"] = 155000
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=152000, tax_year=2027,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1  # not split — $152k below the $155k threshold


def test_high_earner_threshold_ty2026_default_still_works():
    """Regression guard: TY2026 (the year the $150k default is valid for)
    with no override does NOT error — it uses the built-in default."""
    profile = _profile()  # tax_year 2026
    profile["flags"]["age_50_plus_primary"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    # $200k > $150k default → high earner → split row present
    assert any(
        a.name == "401(k) catch-up (Roth required)" for a in report.accounts
    )


def test_401k_high_earner_tax_year_2026_still_splits_regression_guard():
    """Regression guard: TY2026 (the effective year) MUST still trigger
    the split. The year gate is `>= 2026`, not `> 2026` — Notice
    2023-62 effective date is taxable years beginning AFTER Dec 31,
    2025 ⟺ TY2026 onward."""
    profile = _profile()
    profile["tax_year"] = 2026
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["retirement_ytd"]["prior_year_fica_wages"] = 200000
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
        tax_year=2026,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 2
    catchup = next(a for a in rows_401k if "catch-up" in a.name)
    assert catchup.kind == "roth"


def test_401k_high_earner_pre_2026_no_warning_when_wage_data_missing():
    """Inverse case: TY2025 + age-50 + NO prior_year_fica_wages should
    NOT emit the SECURE 2.0 warning either — for pre-effective-date
    years, the field is statutorily irrelevant. Warning is noise."""
    profile = _profile()
    profile["tax_year"] = 2025
    profile["flags"]["age_50_plus_primary"] = True
    profile["flags"]["age_60_63_primary"] = False
    inputs = _inputs()
    inputs["tax_year"] = 2025
    inputs["retirement_ytd"].pop("prior_year_fica_wages", None)
    law = _tax_law_2026()
    law["tax_year"] = 2025
    law["_meta"]["tax_year"] = 2025
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=100000,
        tax_year=2025,
    )
    rows_401k = [a for a in report.accounts if "401(k)" in a.name]
    assert len(rows_401k) == 1
    # Warning should NOT appear for pre-2026 tax years
    assert "SECURE 2.0" not in rows_401k[0].note


# ---------- Round-15: IRA pool capped by taxable compensation ---------------
# Per IRC §219(b)(1)(A) (Traditional) and §408A(c)(2) (Roth), IRA
# contributions cannot exceed the taxpayer's TAXABLE COMPENSATION for
# the year. A retiree with $0 wages + only dividends/cap gains has $0
# IRA capacity regardless of statutory dollar limit. Per §219(c) MFJ
# spousal IRA exception: a non-working spouse can use the working
# spouse's compensation when filing jointly. Dashboard previously used
# compute_ira_limit() as the pool cap without checking compensation —
# silently overstating capacity for retirees and triggering 6%-per-year
# excess-contribution penalties if the user followed the dashboard's
# recommendation.

def test_ira_pool_capped_by_taxable_compensation_below_limit():
    """Part-time worker, $3,000 wages: IRA pool capped at $3,000 not
    the $7,000 statutory limit. Codex round-15 finding — the most
    severe of the PR because it leads directly to IRS-penaltied
    over-contribution if the user follows the wrong number."""
    profile = _profile()
    profile["filing_status"] = "Single"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 3000
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=3000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # Pool capped at compensation, not statutory $7K
    assert trad.pool_remaining == 3000.0


def test_ira_pool_zero_when_no_taxable_compensation_retiree():
    """Retiree with $0 wages + only dividends/cap gains: IRA pool
    capacity is $0. Dashboard previously reported $7,000 (statutory
    limit) — would trigger 6%-per-year excess-contribution penalty
    if the retiree contributed based on the wrong number."""
    profile = _profile()
    profile["filing_status"] = "Single"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 0
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=100000,  # high MAGI from dividends/cap gains
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.pool_remaining == 0.0
    assert trad.annual_limit == 0.0
    # Marginal-saved must be $0 — no capacity means no savings
    assert trad.marginal_fed_saved == 0.0


def test_ira_pool_unchanged_when_compensation_above_limit():
    """High earner with $200K wages: IRA pool unchanged at statutory
    $7K — compensation cap doesn't apply when compensation exceeds
    the dollar limit. Regression guard for the no-cap case."""
    profile = _profile()
    profile["filing_status"] = "Single"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 200000
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=200000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.pool_remaining == 7000.0


def test_ira_pool_mfj_spousal_exception_allows_full_limit():
    """MFJ spousal IRA per IRC §219(c): a non-working spouse can use
    the working spouse's compensation. Filer with $0 own comp +
    spouse with $50K → filer's IRA capacity = full $7K (not capped
    at $0). Without this exception, modeling MFJ households would be
    wrong for the common single-earner case."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 0
    inputs["retirement_ytd"]["spouse_taxable_compensation"] = 50000
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # Spousal IRA: combined comp $50K > $7K → full pool available
    assert trad.pool_remaining == 7000.0


def test_ira_pool_mfs_no_spousal_exception():
    """§219(c) spousal exception is MFJ-only — MFS taxpayers cannot
    combine compensation. MFS + filer $0 own comp → IRA capacity is
    still $0 regardless of spouse's earnings. Regression guard
    against accidentally extending the spousal exception to MFS."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"]["lived_apart_from_spouse_all_year"] = True
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 0
    inputs["retirement_ytd"]["spouse_taxable_compensation"] = 50000
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # MFS doesn't combine compensation → still $0
    assert trad.pool_remaining == 0.0


def test_ira_pool_warns_when_compensation_data_missing():
    """When taxable_compensation is not provided AND the IRA pool
    has capacity, emit a warning so the user knows the dashboard
    may overstate capacity for retirees / no-wage years. Same
    degraded-mode pattern as SECURE 2.0 wage data."""
    profile = _profile()
    profile["filing_status"] = "Single"
    inputs = _inputs()
    inputs["retirement_ytd"].pop("taxable_compensation", None)
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert "taxable_compensation" in trad.note


def test_ira_pool_no_warning_when_compensation_provided():
    """If taxable_compensation IS provided, no warning should appear —
    the dashboard has enough data to compute the cap correctly."""
    profile = _profile()
    profile["filing_status"] = "Single"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 100000
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=100000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert "taxable_compensation" not in trad.note


def test_ira_pool_mfj_warns_when_spouse_compensation_missing():
    """MFJ single-earner couple whose dashboard subject is the $0-wage
    spouse: taxable_compensation=0 is PROVIDED (so the round-15 missing-data
    warning doesn't fire) but spouse_taxable_compensation is null. Without a
    warning the dashboard would show a CONFIDENT $0 capacity even though
    §219(c) lets the non-earner use the working spouse's compensation. Must
    warn that capacity may be understated (Codex P2). The conservative cap
    is kept (not lifted to statutory) so we never overstate."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 0  # provided $0
    inputs["retirement_ytd"].pop("spouse_taxable_compensation", None)
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=80000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert "UNDERSTATED" in trad.note
    assert "spouse_taxable_compensation" in trad.note
    assert trad.annual_limit == 0.0  # conservative cap kept, not overstated


def test_ira_pool_mfj_no_understate_warning_when_primary_comp_sufficient():
    """Regression guard: MFJ with primary taxable_compensation >= statutory
    limit and spouse comp null — the primary's own comp already covers the
    full limit, so no §219(c) understatement is possible and no warning
    should fire."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 100000  # >= statutory
    inputs["retirement_ytd"].pop("spouse_taxable_compensation", None)
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=100000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert "UNDERSTATED" not in trad.note


def test_strict_validators_reject_non_finite_numbers():
    """json.loads accepts NaN/Infinity; they pass isinstance(float) and slip
    past `< 0` / `> 1` range checks (NaN/Inf comparisons are False), then
    poison capacity/savings math. The strict validators must reject
    non-finite values at the parse boundary across every numeric surface
    (Codex P2)."""
    nan, inf = float("nan"), float("inf")
    law = _tax_law_2026()
    # retirement_ytd numeric (_strict_num)
    for bad in (nan, inf):
        with pytest.raises(rd.RetirementError, match="finite"):
            rd.build_dashboard(
                _profile(), _inputs(employee_401k=bad), law,
                marginal_fed_rate=0.24, marginal_state_rate=0.0,
                modified_agi=80000,
            )
    # optional retirement_ytd numeric (_optional_non_negative_num)
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = nan
    with pytest.raises(rd.RetirementError, match="finite"):
        rd.build_dashboard(
            _profile(), inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0, modified_agi=80000,
        )
    # marginal rate (_validate_rate) — NaN slips past the [0,1] range check
    with pytest.raises(rd.RetirementError, match="finite"):
        rd.build_dashboard(
            _profile(), _inputs(), law,
            marginal_fed_rate=nan, marginal_state_rate=0.0, modified_agi=80000,
        )
    # modified_agi
    with pytest.raises(rd.RetirementError, match="finite"):
        rd.build_dashboard(
            _profile(), _inputs(), law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0, modified_agi=inf,
        )
    # tax_law amount (_require_tax_law_amount)
    bad_law = _tax_law_2026()
    bad_law["federal"]["retirement_401k"]["elective_deferral"] = inf
    with pytest.raises(rd.RetirementError, match="finite"):
        rd.build_dashboard(
            _profile(), _inputs(), bad_law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0, modified_agi=80000,
        )


def test_build_dashboard_rejects_malformed_taxable_compensation():
    """Strict parse-boundary typing for the new field — bool, str,
    negative all hard-error (same rule as other numeric fields)."""
    profile = _profile()
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = "50000"  # str
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="taxable_compensation"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )


def test_build_dashboard_rejects_malformed_spouse_taxable_compensation():
    """Same strict rule for the spousal field."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 0
    inputs["retirement_ytd"]["spouse_taxable_compensation"] = True  # bool
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="spouse_taxable_compensation"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=50000,
        )


# ---------- Round-16: MFS lived_apart flag is load-bearing — require it ----
# Per IRS Pub 590-A footnote, MFS lived-apart-all-year is treated as
# Single for IRA purposes. The flag is LOAD-BEARING for any MFS filer's
# IRA computations: lived-with-spouse uses the $0-$10K phase-out;
# lived-apart-all-year uses Single's wider window (or unlimited).
# Silently defaulting a missing flag to False (= lived-with) silently
# applies the MORE RESTRICTIVE phase-out — wrong for actually-lived-
# apart MFS taxpayers. Codex round 16: hard-error when MFS + missing.
# (For Single/MFJ/HoH/QSS the flag is unused, so default-False is fine.)

def test_build_dashboard_mfs_without_lived_apart_flag_hard_errors():
    """MFS taxpayer with `lived_apart_from_spouse_all_year` flag
    MISSING from profile: must hard-error. The flag is load-bearing
    for IRA Traditional deductibility + Roth eligibility — silently
    defaulting to lived-together would apply the wrong phase-out."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"].pop("lived_apart_from_spouse_all_year", None)
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="lived_apart_from_spouse_all_year"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=50000,
        )


def test_build_dashboard_mfs_with_lived_apart_null_hard_errors():
    """MFS with explicit `null` for the flag is the same as missing —
    user has not specified the load-bearing answer."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"]["lived_apart_from_spouse_all_year"] = None
    inputs = _inputs()
    law = _tax_law_2026()
    with pytest.raises(rd.RetirementError, match="lived_apart_from_spouse_all_year"):
        rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=50000,
        )


def test_build_dashboard_mfs_with_lived_apart_true_works():
    """MFS + lived_apart=True works (regression guard — fix must not
    block legitimate MFS filers who provided the flag)."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"]["lived_apart_from_spouse_all_year"] = True
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    assert report.filing_status == "MFS"


def test_build_dashboard_mfs_with_lived_apart_false_works():
    """MFS + lived_apart=False (explicitly together) also works — the
    fix only rejects MISSING values, not False ones."""
    profile = _profile()
    profile["filing_status"] = "MFS"
    profile["flags"]["lived_apart_from_spouse_all_year"] = False
    inputs = _inputs()
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    assert report.filing_status == "MFS"


def test_build_dashboard_non_mfs_without_lived_apart_flag_works():
    """For Single / MFJ / HoH / QSS filers the flag is unused — the
    hard-error must be MFS-only. Default-False is safe for non-MFS
    statuses because no IRA computation reads the flag for them."""
    for status in ("Single", "MFJ", "HoH", "QSS"):
        profile = _profile()
        profile["filing_status"] = status
        profile["flags"].pop("lived_apart_from_spouse_all_year", None)
        inputs = _inputs()
        law = _tax_law_2026()
        # Should NOT raise — flag is not load-bearing for non-MFS
        report = rd.build_dashboard(
            profile, inputs, law,
            marginal_fed_rate=0.24, marginal_state_rate=0.0,
            modified_agi=80000,
        )
        assert report.filing_status == status


# ---------- Round-18: §219(c) MFJ spousal exception does NOT extend to QSS --
# Same bug-class as round-12 finding 2 (HSA spouse catch-up adding $1k
# for non-spouse filers): `_binary_status_key()` collapses MFJ + QSS into
# "mfj" for capital-gains / phase-out lookups, but for the §219(c)
# spousal IRA exception the semantics differ — QSS has a DECEASED
# spouse who cannot have current-year taxable compensation. Stratum 14
# recurrence ("auxiliary entity flag without context guard"): the
# `spouse_taxable_compensation` field is an entity-property (about the
# spouse), and using it for QSS treats a deceased spouse as if they
# could still earn. Skill markdown already says §219(c) is MFJ-only.

def test_ira_pool_qss_does_not_combine_spouse_taxable_compensation():
    """QSS + filer $0 own + spouse_taxable_compensation $50K should
    NOT combine — IRA pool cap stays at $0 (filer's own comp). Per
    skill docs: '§219(c) does NOT extend to MFS / Single / HoH / QSS.'
    Pre-fix bug: _binary_status_key collapsed QSS into 'mfj', so the
    spouse exception applied to deceased spouses."""
    profile = _profile()
    profile["filing_status"] = "QSS"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 0
    inputs["retirement_ytd"]["spouse_taxable_compensation"] = 50000
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # Filer's own comp = $0 → pool capped at $0; spouse field ignored
    assert trad.pool_remaining == 0.0


def test_ira_pool_qss_with_own_compensation_works_normally():
    """QSS with own taxable comp $50K still gets full IRA capacity —
    the bug is only about IGNORING the spouse field for QSS, not
    about restricting QSS more than other statuses."""
    profile = _profile()
    profile["filing_status"] = "QSS"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 50000
    inputs["retirement_ytd"]["spouse_taxable_compensation"] = 100000  # ignored
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    # Own comp $50K > statutory $7K → full pool
    assert trad.pool_remaining == 7000.0


def test_ira_pool_mfj_still_applies_spousal_exception_regression_guard():
    """Regression guard: MFJ + filer $0 + spouse $50K STILL gets
    full IRA capacity via §219(c). The fix scope is narrow to QSS
    (and Single/MFS/HoH which the original code already excluded);
    MFJ must remain unchanged."""
    profile = _profile()
    profile["filing_status"] = "MFJ"
    inputs = _inputs()
    inputs["retirement_ytd"]["taxable_compensation"] = 0
    inputs["retirement_ytd"]["spouse_taxable_compensation"] = 50000
    inputs["retirement_ytd"]["ira_traditional"] = 0
    inputs["retirement_ytd"]["ira_roth"] = 0
    law = _tax_law_2026()
    report = rd.build_dashboard(
        profile, inputs, law,
        marginal_fed_rate=0.24, marginal_state_rate=0.0,
        modified_agi=50000,
    )
    trad = next(a for a in report.accounts if a.name == "Traditional IRA")
    assert trad.pool_remaining == 7000.0


def test_a_two_letter_typo_for_a_non_conforming_state_is_refused():
    """The shape check guarded the wrong thing, and its own comment said so.

    `address.state` is read for exactly one purpose here: membership of the
    small non-conformity sets — CA/NJ for the HSA, PA/NJ/MA for the IRA and
    401(k) rates. The guard rejected "California" and accepted every two-letter
    value, which is the miss that matters: "CN" for CA and "NU" for NJ are the
    ordinary typos, and both fell outside the set the same way a full name
    does. The result is not a refusal but a WRONG NUMBER — the HSA row flips
    from `hsa_non_conforming` to `hsa_conforming` and a state deduction is
    invented at the taxpayer's own marginal rate, silently. Over-stating the
    figure the user acts on is the direction AGENTS.md ranks as the hard error.

    Replayed rather than asserted about: the conforming pair below is what the
    typo used to produce, so a regression that widens the domain again fails
    here rather than at the raise.
    """
    law = _tax_law_2026()

    def hsa_kind(state):
        profile = _profile()
        profile["address"]["state"] = state
        inputs = _inputs(hsa_coverage="self_only", hsa_employee=1000)
        report = rd.build_dashboard(
            profile, inputs, law, marginal_fed_rate=0.32,
            marginal_state_rate=0.093, modified_agi=120000,
        )
        return next(a for a in report.accounts if a.name == "HSA").kind

    assert hsa_kind("CA") == "hsa_non_conforming"
    assert hsa_kind("NJ") == "hsa_non_conforming"
    assert hsa_kind("TX") == "hsa_conforming", (
        "a real conforming state must still be accepted — the domain check "
        "is about codes that name no state, not about the two sets"
    )
    for typo in ("CN", "NU", "ZZ", "BC"):
        with pytest.raises(rd.RetirementError, match="address.state"):
            hsa_kind(typo)


def test_the_state_domain_is_the_shared_usps_set_not_a_local_copy():
    """A fourth copy of the 56 codes is a fourth thing to drift.

    capabilities owns the set, tax_calendar and irs_dates already read it from
    there, and this module is the third consumer. Pinned by identity so a
    later hand-written frozenset here fails rather than diverging quietly —
    DC and the five inhabited territories are the entries a hand copy drops.
    """
    from agents.lib.capabilities import _USPS_STATES as CANONICAL
    assert rd._USPS_STATES is CANONICAL
    assert {"DC", "PR", "GU", "VI", "AS", "MP"} <= rd._USPS_STATES


@pytest.mark.parametrize('roth,already_catch,pretax', [
    (0,0,0), (2500,0,2500), (7500,0,7500), (10000,0,7500),
    (7500,3000,4500), (3000,3000,0),
])
def test_prior_roth_prices_only_remaining_permitted_pretax_catchup(roth, already_catch, pretax):
    # §1.414(v)-2(b)(1): remaining C-U minus mandatory Roth max(0,C-R).
    p = _profile(age_50_plus_primary=True)
    y = _inputs(employee_401k=23500+already_catch, roth_401k_ytd=roth,
        prior_year_fica_wages=200000, plan_allows_pretax_catchup_after_roth=True)
    report = rd.build_dashboard(p,y,_tax_law_2026(),marginal_fed_rate=.24,
                               marginal_state_rate=.05,modified_agi=100000)
    catch = next(a for a in report.accounts if 'catch-up' in a.name)
    assert catch.remaining == 7500-already_catch
    assert catch.marginal_fed_saved == pretax*.24
    assert catch.marginal_state_saved == pretax*.05
    assert catch.marginal_eligible_dollars == pretax


def test_roth_split_requires_plan_permission_and_valid_aggregate():
    p = _profile(age_50_plus_primary=True)
    y = _inputs(employee_401k=23500,roth_401k_ytd=7500,prior_year_fica_wages=200000)
    report = rd.build_dashboard(p,y,_tax_law_2026(),marginal_fed_rate=.24,
                               marginal_state_rate=0,modified_agi=100000)
    catch = next(a for a in report.accounts if 'catch-up' in a.name)
    assert catch.marginal_fed_saved == 0
    for bad in (23501,True,-1,float('nan'),float('inf')):
        y['retirement_ytd']['roth_401k_ytd'] = bad
        with pytest.raises(rd.RetirementError):
            rd.build_dashboard(p,y,_tax_law_2026(),marginal_fed_rate=.24,
                               marginal_state_rate=0,modified_agi=100000)


@pytest.mark.parametrize('monthly,last,expected', [
    (['none']*6+['self_only']*6,False,2150),
    (['none']*6+['self_only']*6,True,4300),
    (['family']*6+['self_only']*6,False,6425),
    (['family']*6+['self_only']*6,True,6425),  # Greater monthly limit retained.
    (['self_only']*6+['family']*6,True,8550),
])
def test_hsa_monthly_tiers_and_last_month_greater_limit(monthly,last,expected):
    # Pub 969 monthly worksheet, with synthetic 4300 self / 8550 family limits.
    y = _inputs(hsa_coverage='family',hsa_monthly_coverage=monthly,
                hsa_last_month_rule=last,hsa_testing_period_acknowledged=last)
    report = rd.build_dashboard(_profile(),y,_tax_law_2026(),marginal_fed_rate=.24,
                               marginal_state_rate=0,modified_agi=100000)
    hsa = next(a for a in report.accounts if a.name == 'HSA')
    assert hsa.annual_limit == expected
    if last:
        assert 'following tax year' in hsa.note


def test_hsa_catchups_use_independent_spouse_months():
    p = _profile(age_55_plus_primary=True,age_55_plus_spouse=True)
    p['filing_status'] = 'MFJ'
    y = _inputs(hsa_coverage='family',hsa_eligible_months=6,
                hsa_spouse_eligible_months=3)
    report = rd.build_dashboard(p,y,_tax_law_2026(),marginal_fed_rate=.24,
                               marginal_state_rate=0,modified_agi=100000)
    hsa = next(a for a in report.accounts if a.name == 'HSA')
    assert hsa.annual_limit == 5025  # 8550/2 + 1000/2 + 1000/4; no second base.
    assert 'SECOND HSA' in hsa.note


@pytest.mark.parametrize('kw', [
    dict(hsa_eligible_months=None),dict(hsa_eligible_months=True),
    dict(hsa_eligible_months=13),dict(hsa_eligible_months=0),
    dict(hsa_eligible_months=12,hsa_december_eligible=False),
    dict(hsa_last_month_rule=True),
    dict(hsa_monthly_coverage=['self_only']*11),
    dict(hsa_monthly_coverage=['none']*12,hsa_last_month_rule=True,
         hsa_testing_period_acknowledged=True),
])
def test_hsa_missing_or_contradictory_eligibility_refuses(kw):
    y = _inputs(hsa_coverage='self_only',**kw)
    with pytest.raises(rd.RetirementError):
        rd.build_dashboard(_profile(),y,_tax_law_2026(),marginal_fed_rate=.24,
                           marginal_state_rate=0,modified_agi=100000)


@pytest.mark.parametrize('code', sorted(create_retirement_conformity_template()))
def test_each_jurisdiction_requires_explicit_account_treatment(code):
    # Matrix values are synthetic test inputs; this makes no legal finding.
    law = _tax_law_2026()
    for account in ('employee_401k','traditional_ira','hsa'):
        row = law['retirement_conformity'][code]
        row[account] = True
        assert rd._retirement_state_treatment(law,code,2026,account) == (True,'')
        row[account] = False
        assert rd._retirement_state_treatment(law,code,2026,account)[0] is False
        row[account] = None
        ok,note = rd._retirement_state_treatment(law,code,2026,account)
        assert ok is False and 'unverified' in note


@pytest.mark.parametrize('bad', ['year','source','missing','string','nan'])
def test_unverified_matrix_cannot_claim_state_savings(bad):
    p = _profile();p['address']['state']='NY'
    law = _tax_law_2026()
    row = law['retirement_conformity']['NY']
    if bad == 'year': row['tax_year']=2025
    elif bad == 'source': law['_sources']['retirement_conformity.NY']='https://example.com/'
    elif bad == 'missing': del law['retirement_conformity']['NY']
    elif bad == 'string': row['employee_401k']='true'
    else: row['employee_401k']=float('nan')
    kwargs=dict(marginal_fed_rate=.24,marginal_state_rate=.06,modified_agi=100000)
    if bad in {'string','nan'}:
        with pytest.raises(rd.RetirementError): rd.build_dashboard(p,_inputs(),law,**kwargs)
    else:
        report=rd.build_dashboard(p,_inputs(),law,**kwargs)
        assert report.total_potential_state_saved == 0
        assert 'unverified' in rd.render_markdown(report)


def test_hsa_previous_coverage_is_not_hidden_by_current_none():
    # First six months self-only, no December eligibility: 4300 × 6/12.
    y = _inputs(hsa_coverage='none',hsa_monthly_coverage=['self_only']*6+['none']*6)
    report = rd.build_dashboard(_profile(),y,_tax_law_2026(),marginal_fed_rate=.24,
                               marginal_state_rate=0,modified_agi=100000)
    assert next(a for a in report.accounts if a.name=='HSA').annual_limit == 2150


def test_covered_year_cannot_use_an_annual_default_threshold():
    law = _tax_law_2026()
    del law['federal']['retirement_401k']['secure_2_0_high_earner_threshold']
    with pytest.raises(rd.RetirementError,match='secure_2_0_high_earner_threshold'):
        rd.build_dashboard(_profile(age_50_plus_primary=True),_inputs(),law,
            marginal_fed_rate=.24,marginal_state_rate=0,modified_agi=100000)


def test_conflicting_hsa_answers_and_invalid_prior_catchup_refuse():
    with pytest.raises(rd.RetirementError,match='conflicts'):
        rd.build_dashboard(_profile(),_inputs(hsa_coverage='self_only',
            hsa_monthly_coverage=['none']*6+['self_only']*6,hsa_eligible_months=12),
            _tax_law_2026(),marginal_fed_rate=.24,marginal_state_rate=0,modified_agi=100000)
    with pytest.raises(rd.RetirementError,match='already-used mandatory catch-up'):
        rd.build_dashboard(_profile(age_50_plus_primary=True),
            _inputs(employee_401k=26500,roth_401k_ytd=2500,prior_year_fica_wages=200000,
                    plan_allows_pretax_catchup_after_roth=True),_tax_law_2026(),
            marginal_fed_rate=.24,marginal_state_rate=0,modified_agi=100000)


def test_spouse_catchup_after_family_coverage_ended_uses_own_eligibility():
    # Six family months base (4275), three spouse catch-up months (250).
    p=_profile(age_55_plus_spouse=True);p['filing_status']='MFJ'
    y=_inputs(hsa_coverage='none',hsa_monthly_coverage=['family']*6+['none']*6,
              hsa_spouse_eligible_months=3,hsa_spouse_december_eligible=False)
    report=rd.build_dashboard(p,y,_tax_law_2026(),marginal_fed_rate=.24,
                             marginal_state_rate=0,modified_agi=100000)
    assert next(a for a in report.accounts if a.name=='HSA').annual_limit == 4525


def test_unknown_residency_cannot_claim_a_forged_conformity_entry():
    law=_tax_law_2026()
    law['retirement_conformity']['']=dict(tax_year=2026,employee_401k=True,
        traditional_ira=True,hsa=True)
    law['_sources']['retirement_conformity.']='https://www.irs.gov/'
    p=_profile();p['address']['state']=None
    report=rd.build_dashboard(p,_inputs(),law,marginal_fed_rate=.24,
                             marginal_state_rate=.05,modified_agi=100000)
    assert report.total_potential_state_saved == 0
    assert 'no supported jurisdiction' in rd.render_markdown(report)
