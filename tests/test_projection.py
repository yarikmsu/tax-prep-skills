import datetime
import json
import subprocess
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import projection as P  # noqa: E402


def test_status_key_helpers():
    assert P._full_status_key("MFJ") == "mfj"
    assert P._full_status_key("QSS") == "mfj"
    assert P._full_status_key("MFS") == "mfs"
    assert P._full_status_key("HOH") == "hoh"
    assert P._full_status_key("single") == "single"
    assert P._full_status_key(None) == "single"
    assert P._binary_status_key("QSS") == "mfj"
    assert P._binary_status_key("HOH") == "single"


def test_num_coerces_and_rejects():
    assert P._num(None) == 0.0
    assert P._num("$1,250.50") == 1250.50
    assert P._num(42) == 42.0
    with pytest.raises(P.ProjectionError):
        P._num("abc", "wages")
    with pytest.raises(P.ProjectionError):
        P._num(True, "wages")


def test_input_template_is_valid_and_complete():
    tmpl = json.loads(
        (Path(__file__).resolve().parent.parent
         / "agents/templates/projection_inputs_template.json").read_text()
    )
    for section in ("ytd", "forecast_overrides", "recurring_from_prior", "estimated_payments_made"):
        assert section in tmpl
    assert tmpl["pay_frequency"] in P.PAY_PERIODS
    assert set(tmpl["ytd"]) >= {"wages", "federal_withholding", "state_withholding"}


def test_parse_as_of_valid():
    assert P.parse_as_of("06-30", 2026) == datetime.date(2026, 6, 30)


def test_parse_as_of_rejects_jan_1():
    with pytest.raises(P.ProjectionError):
        P.parse_as_of("01-01", 2026)


def test_parse_as_of_rejects_impossible_date():
    with pytest.raises(P.ProjectionError):
        P.parse_as_of("02-30", 2026)
    with pytest.raises(P.ProjectionError):
        P.parse_as_of("6/30", 2026)


def test_elapsed_fraction_midyear_and_leap():
    assert P.elapsed_fraction(datetime.date(2026, 7, 1)) == pytest.approx(182 / 365, abs=1e-6)
    assert P.elapsed_fraction(datetime.date(2028, 12, 31)) == pytest.approx(1.0, abs=1e-6)


def test_paychecks_remaining_by_frequency():
    assert P.paychecks_remaining("biweekly", 0.5) == 13
    assert P.paychecks_remaining("weekly", 0.5) == 26
    assert P.paychecks_remaining("monthly", 0.5) == 6
    assert P.paychecks_remaining("semimonthly", 0.5) == 12
    assert P.paychecks_remaining("biweekly", 1.0) == 0


def test_validate_pay_frequency_rejects_unknown():
    with pytest.raises(P.ProjectionError):
        P.validate_pay_frequency("annually")


def _ytd_half_year():
    return {
        "wages": 50000, "federal_withholding": 6000, "state_withholding": 2000,
        "ss_wages": 50000, "medicare_wages": 51000, "medicare_withheld": 740,
    }


def test_annualize_scales_steady_items_and_adds_overrides_full_year():
    as_of = datetime.date(2026, 7, 2)  # day 183 of 365
    frac = P.elapsed_fraction(as_of)
    overrides = {"year_end_bonus": 10000, "rsu_vesting": 5000, "capital_gains": 3000,
                 "self_employment_net": 0, "other_income": 1000, "schedule_1a": 8000}
    recurring = {"taxable_interest": 800, "ordinary_dividends": 1200, "qualified_dividends": 900}
    proj = P.annualize(_ytd_half_year(), as_of, "biweekly", overrides, recurring,
                       {"federal": 0, "state": 0})
    assert proj.wages == pytest.approx(round(50000 / frac, 2) + 15000, abs=0.01)
    assert proj.federal_withholding == pytest.approx(round(6000 / frac, 2), abs=0.01)
    assert proj.capital_gains == 3000
    assert proj.schedule_1a_total == 8000  # full-year override, not annualized
    assert proj.other_income == 1000
    assert proj.taxable_interest == 800
    assert proj.qualified_dividends == 900
    assert proj.paychecks_remaining == P.paychecks_remaining("biweekly", frac)


def test_annualize_rejects_negative_schedule_1a():
    """Codex round-5 P2: a negative Schedule 1-A override must hard-error, not
    silently floor to $0 (which would drop the deduction and overstate tax)."""
    with pytest.raises(P.ProjectionError, match="non-negative"):
        P.annualize({"wages": 50000}, datetime.date(2026, 7, 1), "biweekly",
                    {"schedule_1a": -5000}, {}, {"federal": 0, "state": 0})


def test_annualize_carries_estimates_and_defaults_medicare_wages_to_wages():
    as_of = datetime.date(2026, 7, 2)
    proj = P.annualize({"wages": 50000, "federal_withholding": 6000}, as_of, "monthly",
                       {}, {}, {"federal": 1500, "state": 300})
    assert proj.estimated_fed_paid == 1500
    assert proj.estimated_state_paid == 300
    assert proj.medicare_wages == proj.wages


def _sch1a_full_limits():
    """A fully-populated federal.schedule_1a block (all 7 caps/thresholds)."""
    return {
        "tips_cap": 25000, "car_loan_cap": 10000,
        "senior_amount_per_person": 6000,
        "overtime_cap": {"single": 12500, "mfj": 25000},
        "tips_overtime_phaseout_start": {"single": 150000, "mfj": 300000},
        "car_loan_phaseout_start": {"single": 100000, "mfj": 200000},
        "senior_phaseout_start": {"single": 75000, "mfj": 150000},
    }


def _tax_law_2026():
    return {
        "tax_year": 2026,
        "federal": {
            "filing_statuses": {
                "mfj": {"standard_deduction": 30000,
                        "brackets": [{"rate": 0.10, "min": 0, "max": 20000},
                                     {"rate": 0.20, "min": 20000, "max": 200000},
                                     {"rate": 0.30, "min": 200000, "max": None}]},
                "single": {"standard_deduction": 15000,
                           "brackets": [{"rate": 0.10, "min": 0, "max": 10000},
                                        {"rate": 0.20, "min": 10000, "max": None}]},
            },
            "capital_gains_rates": {"zero_pct_limit": {"mfj": 90000, "single": 45000},
                                    "fifteen_pct_limit": {"mfj": 550000, "single": 490000}},
            "child_tax_credit": {"amount_per_child": 2000, "phase_out_start": {"mfj": 400000, "single": 200000}},
            "additional_medicare_tax": {"rate": 0.009, "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}},
            "social_security": {"wage_base": 176100},
            "qbi_deduction_rate": 0.20,
        },
        "state": {"code": "CA", "standard_deduction": {"mfj": 11000, "single": 5500},
                  "brackets": [{"rate": 0.01, "min": 0, "max": 20000},
                               {"rate": 0.05, "min": 20000, "max": None}]},
    }


def _profile_mfj(deps=None):
    return {"filing_status": "MFJ", "dependents": deps or []}


def _proj(**overrides):
    base = dict(
        wages=100000, federal_withholding=12000, state_withholding=0, medicare_wages=100000,
        medicare_withheld=1450, ss_wages=100000, capital_gains=0, qualified_dividends=0,
        ordinary_dividends=0, taxable_interest=0, self_employment_net=0, other_income=0,
        estimated_fed_paid=0, estimated_state_paid=0, elapsed_fraction=0.5, paychecks_remaining=13)
    base.update(overrides)
    return P.ProjectedInputs(**base)


def test_project_federal_wages_only_matches_bracket_tax():
    fed = P.project_federal_tax(_proj(), _profile_mfj(), _tax_law_2026())
    # taxable = 100000 - 30000 std = 70000; tax = 10%*20000 + 20%*50000 = 2000 + 10000 = 12000
    assert fed.taxable_income == 70000
    assert fed.tax_before_credits == 12000
    assert fed.total_tax == 12000
    assert fed.balance == 0  # 12000 tax - 12000 withholding
    assert fed.used_qdcg is False


def test_project_federal_capital_gains_engages_qdcg():
    fed = P.project_federal_tax(_proj(federal_withholding=0, capital_gains=40000),
                                _profile_mfj(), _tax_law_2026())
    assert fed.used_qdcg is True
    assert fed.total_tax > 0


def test_project_federal_short_term_gain_taxed_ordinary():
    """A short-term gain is ORDINARY income — it must not enter the QDCG bucket.
    100000 wages + 10000 ST = 80000 taxable → 2000 + 20% x 60000 = 14000."""
    fed = P.project_federal_tax(_proj(capital_gains_short=10000),
                                _profile_mfj(), _tax_law_2026())
    assert fed.taxable_income == 80000
    assert fed.used_qdcg is False
    assert fed.total_tax == 14000


def test_project_federal_legacy_capital_gains_treated_long_term():
    """The legacy combined field keeps its historical preferential treatment:
    10000 LT stacks 70000→80000, entirely under the 90000 0% limit → tax 12000
    (vs 14000 if it were ordinary)."""
    fed = P.project_federal_tax(_proj(capital_gains=10000),
                                _profile_mfj(), _tax_law_2026())
    assert fed.used_qdcg is True
    assert fed.total_tax == 12000


def test_project_federal_split_lt_matches_legacy():
    legacy = P.project_federal_tax(_proj(capital_gains=10000),
                                   _profile_mfj(), _tax_law_2026())
    split = P.project_federal_tax(_proj(capital_gains_long=10000),
                                  _profile_mfj(), _tax_law_2026())
    assert split.total_tax == legacy.total_tax


def test_project_federal_st_loss_absorbed_by_lt_gain_stays_preferential():
    """-5000 ST + 15000 LT → net 10000 keeps LT character (net_capital_gains)."""
    fed = P.project_federal_tax(
        _proj(capital_gains_short=-5000, capital_gains_long=15000),
        _profile_mfj(), _tax_law_2026())
    assert fed.used_qdcg is True
    assert fed.total_tax == 12000  # same as the 10000-LT case


def test_project_federal_net_capital_loss_capped_at_3000():
    """A 20000 net loss offsets ordinary income only up to $3,000 — the old
    uncapped flow understated taxable income by 17000.
    100000 - 3000 - 30000 std = 67000 → 2000 + 20% x 47000 = 11400."""
    fed = P.project_federal_tax(_proj(capital_gains_long=-20000),
                                _profile_mfj(), _tax_law_2026())
    assert fed.taxable_income == 67000
    assert fed.total_tax == 11400


def test_project_federal_niit_added_when_threshold_fetched():
    """MFJ, AGI 320000, NII 20000, threshold 250000 → NIIT = 3.8% x min(20000,
    70000) = 760. taxable 290000 → 2000 + 36000 + 27000 = 65000 bracket tax;
    Additional Medicare (300000-250000) x 0.9% = 450; total 66210."""
    law = _tax_law_2026()
    law["federal"]["niit"] = {"rate": 0.038,
                              "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}}
    fed = P.project_federal_tax(
        _proj(wages=300000, ordinary_dividends=20000, medicare_wages=300000),
        _profile_mfj(), law)
    assert fed.niit == 760.00
    assert fed.additional_medicare == 450.00
    assert fed.total_tax == 66210.00


def test_project_schedule_1a_reduces_taxable_income_not_agi_or_niit():
    """I2 projection parity: a Schedule 1-A forecast override is a BELOW-AGI
    deduction — it drops taxable income but leaves AGI, NIIT, and Additional
    Medicare (all MAGI/wage-based) untouched. MFJ AGI 320000, NII 20000,
    threshold 250000; a $15,000 Schedule 1-A cuts taxable income by 15,000
    while NIIT (760) and Add'l Medicare (450) are identical to the base case."""
    law = _tax_law_2026()
    law["federal"]["niit"] = {"rate": 0.038,
                              "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}}
    args = dict(wages=300000, ordinary_dividends=20000, medicare_wages=300000)
    base = P.project_federal_tax(_proj(**args), _profile_mfj(), law)
    with_1a = P.project_federal_tax(_proj(schedule_1a_total=15000, **args),
                                    _profile_mfj(), law)
    assert with_1a.agi == base.agi
    assert with_1a.niit == base.niit == 760.00
    assert with_1a.additional_medicare == base.additional_medicare == 450.00
    assert with_1a.taxable_income == base.taxable_income - 15000


def test_project_schedule_1a_gated_off_outside_effective_years():
    """Codex P2: compute_schedule_1a returns $0 outside TY2025-2028, so a stale
    forecast override must not shave taxable income for TY2029+ (which would
    understate tax and the safe-harbor target)."""
    law = _tax_law_2026()
    law["tax_year"] = 2029
    gated = P.project_federal_tax(_proj(schedule_1a_total=15000), _profile_mfj(), law)
    base = P.project_federal_tax(_proj(), _profile_mfj(), law)
    assert gated.taxable_income == base.taxable_income  # override ignored in 2029


def test_project_schedule_1a_flags_unfetched_caps():
    """Codex round-13 P2: a nonzero schedule_1a override is subtracted raw
    (uncapped/unphased) when federal.schedule_1a is unfetched — surface a
    warning flag rather than silently trusting a possibly-raw figure."""
    law = _tax_law_2026()
    law["federal"].pop("schedule_1a", None)  # caps/thresholds not fetched
    unfetched = P.project_federal_tax(
        _proj(schedule_1a_total=9000), _profile_mfj(), law)
    assert unfetched.schedule_1a_unfetched is True
    # FULLY-populated caps -> no warning.
    law["federal"]["schedule_1a"] = _sch1a_full_limits()
    fetched = P.project_federal_tax(
        _proj(schedule_1a_total=9000), _profile_mfj(), law)
    assert fetched.schedule_1a_unfetched is False
    # A truthy-but-all-None scaffold (create_empty_tax_law) reads as unfetched.
    law["federal"]["schedule_1a"] = {
        "tips_cap": None, "car_loan_cap": None,
        "overtime_cap": {"single": None, "mfj": None},
        "senior_amount_per_person": None}
    scaffold = P.project_federal_tax(
        _proj(schedule_1a_total=9000), _profile_mfj(), law)
    assert scaffold.schedule_1a_unfetched is True
    # A PARTIAL top-level block (only tips_cap present) reads as unfetched —
    # the other caps/thresholds can't validate the override.
    law["federal"]["schedule_1a"] = {"tips_cap": 25000}
    partial_top = P.project_federal_tax(
        _proj(schedule_1a_total=9000), _profile_mfj(), law)
    assert partial_top.schedule_1a_unfetched is True
    # A nested cap map missing a status key (single present, mfj absent) too.
    law["federal"]["schedule_1a"] = dict(_sch1a_full_limits(),
                                         overtime_cap={"single": 12500})
    partial_nested = P.project_federal_tax(
        _proj(schedule_1a_total=9000), _profile_mfj(), law)
    assert partial_nested.schedule_1a_unfetched is True
    # A malformed scalar (string / negative / NaN) reads as unfetched too — the
    # compute path rejects those, so a corrupt cache can't validate the override.
    for bad in ("25000", -1, float("nan")):
        law["federal"]["schedule_1a"] = dict(_sch1a_full_limits(), tips_cap=bad)
        got = P.project_federal_tax(
            _proj(schedule_1a_total=9000), _profile_mfj(), law)
        assert got.schedule_1a_unfetched is True, bad
    # No override -> no warning even when the block is absent.
    law["federal"].pop("schedule_1a", None)
    none = P.project_federal_tax(_proj(), _profile_mfj(), law)
    assert none.schedule_1a_unfetched is False


def test_project_schedule_1a_override_fails_closed_on_bad_cache_year():
    """Codex round-16 P2: the effective-year gate must FAIL CLOSED — a cache
    whose tax_year is missing, a string, or out of the TY2025-2028 window must
    NOT let a raw override shave taxable income (only a genuine in-window int
    applies it)."""
    base_law = _tax_law_2026()
    base = P.project_federal_tax(_proj(), _profile_mfj(), base_law)
    for bad in (None, "2026", 2029, 2024, True):
        law = _tax_law_2026()
        if bad is None:
            law.pop("tax_year", None)
        else:
            law["tax_year"] = bad
        got = P.project_federal_tax(
            _proj(schedule_1a_total=15000), _profile_mfj(), law)
        assert got.taxable_income == base.taxable_income, bad  # override cleared
    # A genuine in-window int DOES apply the override.
    ok = P.project_federal_tax(
        _proj(schedule_1a_total=15000), _profile_mfj(), _tax_law_2026())
    assert ok.taxable_income == base.taxable_income - 15000
    # Cache year in-window but != the requested projection year -> cleared
    # (a 2026 cache can't validate a 2027 projection's override).
    mism = P.project_federal_tax(
        _proj(schedule_1a_total=15000), _profile_mfj(), _tax_law_2026(),
        requested_year=2027)
    assert mism.taxable_income == base.taxable_income


def test_project_schedule_1a_se_tips_excluded_from_qbi():
    """Codex round-17 P2: self-employed §224 tips in the Schedule 1-A override
    are removed from the QBI base (Form 8995 excludes deducted tip income). With
    high wages so the QBI overall cap is not binding, $20K SE tips in a $100K
    Schedule C business yields QBI on $80K, not $100K — a 20% × $20K = $4,000
    smaller QBI deduction, so taxable income is $4,000 higher with the split."""
    law = _tax_law_2026()
    law["federal"]["schedule_1a"] = _sch1a_full_limits()
    common = dict(wages=300000, self_employment_net=100000, schedule_1a_total=20000)
    base = P.project_federal_tax(_proj(**common), _profile_mfj(), law)
    split = P.project_federal_tax(
        _proj(schedule_1a_se_tips=20000, **common), _profile_mfj(), law)
    assert split.taxable_income == base.taxable_income + 4000
    # SE tax is unchanged — the tip deduction is income-tax only, not SE tax.
    assert split.se_tax == base.se_tax


def test_project_se_tips_qbi_exclusion_bounded_by_applied_deduction():
    """Codex round-18 P2: the SE-tip QBI exclusion can't exceed the APPLIED
    Schedule 1-A deduction. An inconsistent override (se_tips 20000 but the
    schedule_1a total only 1000) excludes at most $1,000 from the QBI base, not
    $20,000 — so QBI drops by 20% × $1,000 = $200, not $4,000."""
    law = _tax_law_2026()
    law["federal"]["schedule_1a"] = _sch1a_full_limits()
    common = dict(wages=300000, self_employment_net=100000, schedule_1a_total=1000)
    base = P.project_federal_tax(_proj(**common), _profile_mfj(), law)
    got = P.project_federal_tax(
        _proj(schedule_1a_se_tips=20000, **common), _profile_mfj(), law)
    assert got.taxable_income == base.taxable_income + 200


def test_project_qbi_base_netted_by_half_se_tax():
    """Codex PR #51 P1: the projection nets the QBI base by the deductible half
    of SE tax (§199A(c)(4) / Reg. §1.199A-3(b)(1)(vi)). With high wages so the
    Form 8995 overall cap is not binding and no Schedule 1-A, QBI is
    20% × (SE net − ½ SE tax), NOT 20% × gross SE net — if the ½-SE-tax netting
    were dropped the deduction would be 20% × $100K = $20K and taxable income
    would be 20% × ½-SE-tax lower."""
    law = _tax_law_2026()
    f = P.project_federal_tax(
        _proj(wages=300000, medicare_wages=300000, self_employment_net=100000),
        _profile_mfj(), law)
    half_se_tax = f.se_tax / 2
    netted_qbi = 0.20 * (100000 - half_se_tax)
    ti_before = f.agi - 30000  # MFJ std, no Schedule 1-A
    assert f.taxable_income == pytest.approx(ti_before - netted_qbi, abs=0.02)
    # Sanity: the netting genuinely shaved the QBI below the gross 20% × $100K.
    assert netted_qbi < 20000


def test_project_schedule_1a_zeroed_for_mfs():
    """Codex round-14 P2: all four Schedule 1-A provisions are $0 for MFS
    (tips/overtime/senior joint-required + the §70203 car-loan MFS bar), so a
    raw MFS override must not shave projected taxable income."""
    law = _tax_law_2026()
    law["federal"]["filing_statuses"]["mfs"] = {
        "standard_deduction": 15000,
        "brackets": [{"rate": 0.10, "min": 0, "max": 10000},
                     {"rate": 0.20, "min": 10000, "max": None}]}
    prof = {"filing_status": "MFS", "dependents": []}
    with_1a = P.project_federal_tax(
        _proj(schedule_1a_total=9000, wages=80000), prof, law)
    base = P.project_federal_tax(_proj(wages=80000), prof, law)
    assert with_1a.taxable_income == base.taxable_income  # override ignored
    assert with_1a.schedule_1a_unfetched is False  # zeroed → no warning


def test_project_federal_niit_reduced_by_capped_capital_loss():
    """Codex P2 round 3: a net capital loss reduces NII by the capped allowed
    amount (Form 8960 line 5a is the gain/loss AS INCLUDED IN AGI, not floored
    at 0). $20K interest + $10K net loss → NII = 20000 - 3000 = 17000;
    AGI = 300000 + 20000 - 3000 = 317000, excess over 250000 = 67000 →
    NIIT = 3.8% x min(17000, 67000) = 646."""
    law = _tax_law_2026()
    law["federal"]["niit"] = {"rate": 0.038,
                              "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}}
    fed = P.project_federal_tax(
        _proj(wages=300000, taxable_interest=20000, medicare_wages=300000,
              capital_gains_long=-10000),
        _profile_mfj(), law)
    assert fed.niit == 646.00


def test_project_federal_niit_degrades_to_zero_without_block():
    """No niit block in tax_law → threshold None → NIIT 0, no crash."""
    fed = P.project_federal_tax(
        _proj(wages=300000, ordinary_dividends=20000, medicare_wages=300000),
        _profile_mfj(), _tax_law_2026())
    assert fed.niit == 0.0


def test_annualize_rejects_legacy_and_split_capital_gains_together():
    with pytest.raises(P.ProjectionError, match="legacy"):
        P.annualize({"wages": 50000}, datetime.date(2026, 7, 1), "biweekly",
                    {"capital_gains": 5000, "capital_gains_long": 5000}, {}, {})


def test_project_federal_qbi_over_threshold_flagged_and_rendered():
    """Codex P2 round 2: the fetched §199A threshold must reach the projection's
    QBI call so the over-threshold warning (8995-A limits unmodeled) surfaces
    instead of silently understating projected tax for SSTB taxpayers."""
    law = _tax_law_2026()
    law["federal"]["qbi_threshold"] = {"single": 100000, "mfj": 200000}
    fed = P.project_federal_tax(
        _proj(wages=250000, medicare_wages=250000, self_employment_net=100000),
        _profile_mfj(), law)
    # AGI ≈ 350000 - half-SE; ti_before_qbi far exceeds the 200000 mfj threshold
    assert fed.qbi_over_threshold is True

    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    report = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 125000, "federal_withholding": 15000, "medicare_wages": 125000},
         "forecast_overrides": {"self_employment_net": 100000},
         "estimated_payments_made": {}},
        _profile_mfj(), law, prior, 2026, datetime.date(2026, 7, 2))
    assert report.federal.qbi_over_threshold is True
    assert "8995-A" in P.render_markdown(report)


def test_project_federal_qbi_threshold_absent_no_flag():
    """No fetched qbi_threshold → flag stays False (degrade, don't crash)."""
    fed = P.project_federal_tax(
        _proj(wages=250000, medicare_wages=250000, self_employment_net=100000),
        _profile_mfj(), _tax_law_2026())
    assert fed.qbi_over_threshold is False


def test_project_federal_applies_ctc():
    fed = P.project_federal_tax(_proj(federal_withholding=0),
                                _profile_mfj([{"ctc_eligible": True}, {"ctc_eligible": True}]),
                                _tax_law_2026())
    # 2 kids * 2000 = 4000 credit; tax_before_credits 12000 -> total 8000
    assert fed.ctc == 4000
    assert fed.total_tax == 8000


def test_project_federal_se_income_adds_se_tax():
    fed = P.project_federal_tax(
        _proj(wages=0, federal_withholding=0, medicare_wages=0, medicare_withheld=0,
              ss_wages=0, self_employment_net=50000),
        _profile_mfj(), _tax_law_2026())
    assert fed.se_tax > 0
    assert fed.adjustments == pytest.approx(fed.se_tax / 2, abs=0.5)


def test_project_federal_raises_without_brackets():
    with pytest.raises(P.ProjectionError):
        P.project_federal_tax(_proj(wages=1), _profile_mfj(), {"federal": {"filing_statuses": {}}})


def test_safe_harbor_picks_lower_of_90pct_and_prior():
    sh = P.compute_safe_harbor(20000, 15000, 120000, "MFJ", withholding=10000, estimates_paid=0)
    assert sh.pct90_current == 18000
    assert sh.prior_factor == 1.00
    assert sh.prior_based == 15000
    assert sh.required_annual == 15000
    assert sh.shortfall == 5000
    assert sh.covered is False
    assert sh.degraded is False


def test_safe_harbor_110pct_when_high_prior_agi():
    sh = P.compute_safe_harbor(50000, 30000, 200000, "MFJ", withholding=0, estimates_paid=0)
    assert sh.prior_factor == 1.10
    assert sh.prior_based == 33000
    assert sh.required_annual == 33000  # min(45000, 33000)


def test_safe_harbor_mfs_75k_threshold():
    sh = P.compute_safe_harbor(50000, 30000, 80000, "MFS", withholding=0, estimates_paid=0)
    assert sh.prior_factor == 1.10  # 80k > 75k MFS threshold


def test_safe_harbor_degraded_when_no_prior():
    sh = P.compute_safe_harbor(20000, None, None, "single", withholding=21000, estimates_paid=0)
    assert sh.degraded is True
    assert sh.required_annual == 18000  # 90%-only
    assert sh.shortfall == 0
    assert sh.covered is True


def test_w4_4c_divides_shortfall_over_paychecks():
    w4 = P.compute_w4_4c(5200, 13)
    assert w4.per_paycheck == 400.0
    assert w4.paychecks_remaining == 13


def test_w4_4c_zero_shortfall_and_no_paychecks():
    assert P.compute_w4_4c(0, 13).per_paycheck == 0.0
    assert P.compute_w4_4c(5000, 0).per_paycheck == 0.0


def test_vouchers_split_remaining_quarters():
    as_of = datetime.date(2026, 5, 1)
    vouchers = P.compute_estimated_vouchers(required_annual=12000, withholding=3000,
                                            paid=0, as_of=as_of)
    assert len(vouchers) == 3
    assert vouchers[0].quarter == 2
    assert vouchers[-1].due_date == "2027-01-15"
    assert sum(v.amount for v in vouchers) == pytest.approx(9000, abs=0.05)


def test_vouchers_zero_when_covered():
    as_of = datetime.date(2026, 5, 1)
    vouchers = P.compute_estimated_vouchers(10000, 12000, 0, as_of)
    assert all(v.amount == 0.0 for v in vouchers)


def test_vouchers_all_past_folds_into_final():
    as_of = datetime.date(2026, 12, 20)
    vouchers = P.compute_estimated_vouchers(8000, 0, 0, as_of)
    assert len(vouchers) == 1
    assert vouchers[0].due_date == "2027-01-15"
    assert vouchers[0].amount == pytest.approx(8000, abs=0.01)


class _Prior:
    def __init__(self, result):
        self.result = result


def test_state_projection_when_baseline_and_brackets_present():
    proj = _proj(state_withholding=4000)
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    st = P.project_state_tax(proj, prior, _tax_law_2026(), filing_status="MFJ")
    # state taxable = 100000 - 11000 std = 89000; tax = 1%*20000 + 5%*69000 = 200 + 3450 = 3650
    assert st is not None
    assert st.taxable_income == 89000
    assert st.tax == 3650
    assert st.balance == round(3650 - 4000, 2)  # -350 refund


def test_state_projection_includes_split_capital_gains():
    """Codex P2: the split ST/LT fields must reach state income too — a 10000
    LT gain adds to state taxable income (states tax gains as ordinary).
    100000 + 10000 - 11000 std = 99000 → 200 + 5% x 79000 = 4150."""
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    st = P.project_state_tax(_proj(capital_gains_long=10000), prior,
                             _tax_law_2026(), filing_status="MFJ")
    assert st.taxable_income == 99000
    assert st.tax == 4150


def test_state_projection_caps_net_capital_loss():
    """A 20000 net loss is capped at $3,000 in state income as well.
    100000 - 3000 - 11000 = 86000 → 200 + 5% x 66000 = 3500."""
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    st = P.project_state_tax(_proj(capital_gains_short=-20000), prior,
                             _tax_law_2026(), filing_status="MFJ")
    assert st.taxable_income == 86000
    assert st.tax == 3500


def test_state_projection_none_without_prior():
    assert P.project_state_tax(_proj(), None, _tax_law_2026(), "MFJ") is None


def test_state_projection_none_without_state_brackets():
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    law = _tax_law_2026()
    law["state"] = {}
    assert P.project_state_tax(_proj(), prior, law, "MFJ") is None


def _build_sample_report(degraded=False, with_state=True):
    law = _tax_law_2026()
    inputs = {
        "pay_frequency": "biweekly",
        "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
        "forecast_overrides": {"capital_gains": 0},
        "recurring_from_prior": {"taxable_interest": 0},
        "estimated_payments_made": {"federal": 0, "state": 0},
    }
    prior = None if degraded else _Prior(
        {"california_540": {"ca_total_tax": 5000},
         "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    if not with_state:
        law["state"] = {}
    return P.build_report(inputs=inputs, profile=_profile_mfj(), tax_law=law,
                          prior=prior, year=2026, as_of=datetime.date(2026, 7, 2))


def test_build_report_assembles_all_sections():
    r = P.build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile=_profile_mfj(),
        tax_law=_tax_law_2026(),
        prior=_Prior({"form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}}),
        year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.year == 2026
    assert r.federal.total_tax > 0
    assert r.safe_harbor.prior_tax == 15000
    assert r.w4_penalty.per_paycheck >= 0
    assert r.w4_zero.per_paycheck >= 0


def test_render_markdown_degraded_banner_and_state_skip_note():
    r = _build_sample_report(degraded=True, with_state=False)
    md = P.render_markdown(r)
    assert "Mid-Year Tax Projection" in md
    assert "degraded" in md.lower()
    assert "skipped" in md.lower()


def test_render_markdown_no_crash_on_zeros():
    r = P.build_report(
        inputs={"pay_frequency": "monthly", "ytd": {"wages": 1},
                "estimated_payments_made": {}},
        profile=_profile_mfj(), tax_law=_tax_law_2026(), prior=None,
        year=2026, as_of=datetime.date(2026, 7, 2))
    md = P.render_markdown(r)
    assert isinstance(md, str) and len(md) > 0


def test_report_to_dict_is_json_serializable():
    r = _build_sample_report()
    json.dumps(P._report_to_dict(r))  # must not raise


FIXTURE_WS = Path(__file__).resolve().parent / "fixtures" / "projection"


def test_load_prior_baseline_absent_returns_none(tmp_path):
    assert P.load_prior_baseline(tmp_path / "archive" / "2025", 2025) is None


def test_load_prior_baseline_loads_when_present():
    prior = P.load_prior_baseline(FIXTURE_WS / "archive" / "2025", 2025)
    assert prior is not None
    assert prior.result["form_1040"]["line_24_total_tax"] == 14000


def test_load_prior_baseline_year_mismatch_raises(tmp_path):
    arch = tmp_path / "archive" / "2025"
    arch.mkdir(parents=True)
    (arch / "result.json").write_text(json.dumps({"meta": {"tax_year": 2099}}))
    (arch / "taxpayer_profile.json").write_text(json.dumps({}))
    with pytest.raises(P.ProjectionError):
        P.load_prior_baseline(arch, 2025)


def test_cli_integration_json_output():
    cmd = [sys.executable, "-m", "agents.lib.projection",
           "--inputs", str(FIXTURE_WS / "projection_inputs.json"),
           "--workspace", str(FIXTURE_WS), "--year", "2026", "--as-of", "06-30",
           "--today", "2026-06-30", "--json"]
    repo_root = Path(__file__).resolve().parent.parent
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["year"] == 2026
    assert data["degraded"] is False
    assert data["safe_harbor"]["prior_tax"] == 14000
    assert data["federal"]["total_tax"] > 0
    assert data["state"] is not None
    assert data["state"]["code"] == "CA"


def test_cli_rejects_future_as_of():
    cmd = [sys.executable, "-m", "agents.lib.projection",
           "--inputs", str(FIXTURE_WS / "projection_inputs.json"),
           "--workspace", str(FIXTURE_WS), "--year", "2026", "--as-of", "12-31",
           "--today", "2026-06-30", "--json"]
    repo_root = Path(__file__).resolve().parent.parent
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    assert out.returncode == 1
    assert "future" in out.stderr.lower()


def test_cli_rejects_path_traversal():
    cmd = [sys.executable, "-m", "agents.lib.projection",
           "--inputs", "/etc/passwd",
           "--workspace", str(FIXTURE_WS), "--year", "2026", "--as-of", "06-30", "--json"]
    repo_root = Path(__file__).resolve().parent.parent
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    assert out.returncode == 1


# ---------------------------------------------------------------------------
# E7: CA safe harbor (FTB 5805) + CA 540-ES 30/40/0/30 voucher schedule
# ---------------------------------------------------------------------------

def test_ca_safe_harbor_min_of_90_and_prior():
    # pct90 = 0.9*20000 = 18000; prior_agi 100000 <= 150000 -> factor 1.00
    # -> prior_based 15000 -> required 15000; paid 12000 -> shortfall 3000.
    sh = P.compute_ca_safe_harbor(20000, 15000, 100000, 400000, "MFJ",
                                  withholding=10000, estimates_paid=2000)
    assert sh.pct90_current == 18000.0
    assert sh.prior_factor == 1.00
    assert sh.required_annual == 15000.0
    assert sh.shortfall == 3000.0
    assert sh.covered is False
    assert sh.high_income_90_only is False
    assert sh.degraded is False


def test_ca_safe_harbor_110_factor_when_prior_agi_over_150k():
    # prior_agi 200000 > 150000 -> 1.10*15000 = 16500 < 18000 -> required 16500
    sh = P.compute_ca_safe_harbor(20000, 15000, 200000, 400000, "MFJ",
                                  withholding=17000, estimates_paid=0)
    assert sh.prior_factor == 1.10
    assert sh.required_annual == 16500.0
    assert sh.covered is True


def test_ca_safe_harbor_prior_agi_exactly_150k_uses_100pct():
    # The 110% factor is strictly ABOVE $150K (FTB 5805 mirrors §6654).
    sh = P.compute_ca_safe_harbor(20000, 15000, 150000, 400000, "Single",
                                  withholding=0, estimates_paid=0)
    assert sh.prior_factor == 1.00


def test_ca_safe_harbor_mfs_75k_threshold():
    sh = P.compute_ca_safe_harbor(20000, 15000, 80000, 200000, "MFS",
                                  withholding=0, estimates_paid=0)
    assert sh.prior_factor == 1.10


def test_ca_safe_harbor_high_income_forces_90pct():
    # R&TC §19136.3: current-year CA AGI >= $1M -> prior-year harbor OFF even
    # when it would be far cheaper (prior tax 1000). Boundary: exactly $1M.
    sh = P.compute_ca_safe_harbor(200000, 1000, 100000, 1_000_000, "MFJ",
                                  withholding=0, estimates_paid=0)
    assert sh.high_income_90_only is True
    assert sh.prior_based is None
    assert sh.required_annual == 180000.0
    assert sh.degraded is False


def test_ca_safe_harbor_just_under_1m_keeps_prior_harbor():
    sh = P.compute_ca_safe_harbor(200000, 1000, 100000, 999_999.99, "MFJ",
                                  withholding=0, estimates_paid=0)
    assert sh.high_income_90_only is False
    assert sh.required_annual == 1000.0


def test_ca_safe_harbor_mfs_high_income_500k():
    sh = P.compute_ca_safe_harbor(50000, 1000, 100000, 500_000, "MFS",
                                  withholding=0, estimates_paid=0)
    assert sh.high_income_90_only is True
    assert sh.required_annual == 45000.0


def test_ca_safe_harbor_degraded_without_prior():
    sh = P.compute_ca_safe_harbor(20000, None, None, 400000, "MFJ",
                                  withholding=0, estimates_paid=0)
    assert sh.degraded is True
    assert sh.required_annual == 18000.0


def test_ca_safe_harbor_high_income_wins_over_degraded():
    # High income makes the 90% answer statutorily EXACT — not a degraded
    # guess — even with no prior baseline.
    sh = P.compute_ca_safe_harbor(20000, None, None, 2_000_000, "MFJ",
                                  withholding=0, estimates_paid=0)
    assert sh.high_income_90_only is True
    assert sh.degraded is False


def test_ca_vouchers_full_year_30_40_0_30():
    # shortfall = 20000 - 8000 - 2000 = 10000, all four installments open.
    vs = P.compute_ca_estimated_vouchers(20000, 8000, 2000,
                                         datetime.date(2026, 3, 1))
    assert [(v.quarter, v.due_date, v.amount) for v in vs] == [
        (1, "2026-04-15", 3000.0),
        (2, "2026-06-15", 4000.0),
        (3, "2026-09-15", 0.0),   # visible $0 row — no September CA payment
        (4, "2027-01-15", 3000.0),
    ]


def test_ca_vouchers_after_q1_renormalize_weights():
    # Open: Q2 (.40), Q3 (0), Q4 (.30) -> Q2 = 10000*4/7 = 5714.29,
    # Q4 takes the remainder 4285.71 so the column sums exactly.
    vs = P.compute_ca_estimated_vouchers(10000, 0, 0, datetime.date(2026, 5, 1))
    assert [(v.quarter, v.amount) for v in vs] == [
        (2, 5714.29), (3, 0.0), (4, 4285.71)]
    assert round(sum(v.amount for v in vs), 2) == 10000.0


def test_ca_vouchers_after_jun_all_on_january():
    # After Jun 15 only the (zero-weight) Q3 and Q4 remain -> everything
    # lands on the January installment.
    vs = P.compute_ca_estimated_vouchers(10000, 0, 0, datetime.date(2026, 7, 2))
    assert [(v.quarter, v.amount) for v in vs] == [(3, 0.0), (4, 10000.0)]


def test_ca_vouchers_after_sep_only_q4():
    vs = P.compute_ca_estimated_vouchers(10000, 0, 0, datetime.date(2026, 10, 1))
    assert [(v.quarter, v.due_date, v.amount) for v in vs] == [
        (4, "2027-01-15", 10000.0)]


def test_ca_vouchers_covered_all_zero():
    vs = P.compute_ca_estimated_vouchers(5000, 8000, 0, datetime.date(2026, 3, 1))
    assert [v.amount for v in vs] == [0.0, 0.0, 0.0, 0.0]


def test_ca_vouchers_rounding_remainder_lands_on_last():
    # shortfall 100.01: Q1 30.0, Q2 40.0, Q3 0, Q4 = 100.01 - 70.00 = 30.01.
    vs = P.compute_ca_estimated_vouchers(100.01, 0, 0, datetime.date(2026, 3, 1))
    assert [v.amount for v in vs] == [30.0, 40.0, 0.0, 30.01]
    assert round(sum(v.amount for v in vs), 2) == 100.01


def test_prior_ca_tax_agi_extraction_and_bool_rejection():
    assert P._prior_ca_tax_agi(None) == (None, None)
    assert P._prior_ca_tax_agi(_Prior({})) == (None, None)
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 120000}})
    assert P._prior_ca_tax_agi(prior) == (5000.0, 120000.0)
    # JSON true must not become a $1.00 prior-year tax.
    prior_bool = _Prior({"california_540": {"ca_total_tax": True, "ca_agi": 120000}})
    assert P._prior_ca_tax_agi(prior_bool) == (None, 120000.0)


def test_state_projection_reports_agi():
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    st = P.project_state_tax(_proj(), prior, _tax_law_2026(), filing_status="MFJ")
    assert st.agi == 100000.0  # income before the 11000 standard deduction


def test_build_report_ca_block_present_for_ca_state():
    r = _build_sample_report()
    assert r.ca_safe_harbor is not None
    assert r.ca_vouchers
    # Vouchers reconcile to the CA shortfall.
    assert round(sum(v.amount for v in r.ca_vouchers), 2) == r.ca_safe_harbor.shortfall
    md = P.render_markdown(r)
    assert "CA safe harbor (FTB 5805)" in md
    assert "CA 540-ES voucher schedule (30% / 40% / 0% / 30%)" in md
    assert "there is no September (Q3) payment" in md
    assert "R&TC §19011.5" in md  # mandatory e-pay caveat


def test_build_report_no_ca_block_without_state():
    r = _build_sample_report(with_state=False)
    assert r.ca_safe_harbor is None
    assert r.ca_vouchers == []
    md = P.render_markdown(r)
    assert "540-ES voucher" not in md
    assert "State estimated-payment scheduling is informational" in md


def test_build_report_no_ca_block_for_non_ca_state_code():
    law = _tax_law_2026()
    law["state"]["code"] = "NY"
    prior = _Prior({"california_540": {"ca_total_tax": 5000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        _profile_mfj(), law, prior, 2026, datetime.date(2026, 7, 2))
    assert r.state is not None
    assert r.ca_safe_harbor is None


def test_build_report_ca_high_income_note_rendered():
    # YTD wages 600000 over half a year -> projected CA AGI well over $1M.
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 900000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 600000, "federal_withholding": 150000,
                 "state_withholding": 50000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        _profile_mfj(), _tax_law_2026(), prior, 2026, datetime.date(2026, 7, 2))
    assert r.state.agi >= 1_000_000
    assert r.ca_safe_harbor.high_income_90_only is True
    assert "R&TC §19136.3" in P.render_markdown(r)


def test_report_to_dict_includes_ca_fields():
    d = P._report_to_dict(_build_sample_report())
    json.dumps(d, default=str)  # JSON-serializable
    assert d["ca_safe_harbor"] is not None
    assert isinstance(d["ca_vouchers"], list) and d["ca_vouchers"]
    d2 = P._report_to_dict(_build_sample_report(with_state=False))
    assert d2["ca_safe_harbor"] is None
    assert d2["ca_vouchers"] == []


def test_num_rejects_non_finite():
    # json.loads accepts NaN/Infinity; a NaN ca_agi would silently disable
    # the R&TC §19136.3 high-income check (understating direction).
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(P.ProjectionError):
            P._num(bad, "x")


# ---------------------------------------------------------------------------
# E7 review-round fixes (reviewer P1/P2 findings)
# ---------------------------------------------------------------------------

def test_ca_block_fires_without_state_code_via_resident_state():
    """P1: the canonical tax_law schema had no state.code — the CA block must
    fall back to the profile's address.state instead of silently never firing."""
    law = _tax_law_2026()
    del law["state"]["code"]
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 120000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    profile = {"filing_status": "MFJ", "dependents": [],
               "address": {"state": "CA"}}
    r = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        profile, law, prior, 2026, datetime.date(2026, 7, 2))
    assert r.state.code == "CA"
    assert r.ca_safe_harbor is not None


def test_no_state_code_and_no_address_stays_generic():
    law = _tax_law_2026()
    del law["state"]["code"]
    prior = _Prior({"california_540": {"ca_total_tax": 5000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        _profile_mfj(), law, prior, 2026, datetime.date(2026, 7, 2))
    assert r.state.code == "STATE"
    assert r.ca_safe_harbor is None


def test_prior_ca_tax_agi_rejects_nan_negative_and_scaffold():
    # NaN prior AGI would silently drop the 110% factor (understating).
    p1 = _Prior({"california_540": {"ca_total_tax": 50000, "ca_agi": float("nan")}})
    assert P._prior_ca_tax_agi(p1) == (50000.0, None)
    # Negative prior tax would make min(pct90, -5000) mark anything covered.
    p2 = _Prior({"california_540": {"ca_total_tax": -5000, "ca_agi": 100000}})
    assert P._prior_ca_tax_agi(p2) == (None, 100000.0)
    # All-zero template scaffold is NOT a computed $0 return.
    p3 = _Prior({"california_540": {"_include_if": "state is CA",
                                    "ca_total_tax": 0, "ca_agi": 0}})
    assert P._prior_ca_tax_agi(p3) == (None, None)
    # Negative AGI (loss year) is legitimate and makes the section present.
    p4 = _Prior({"california_540": {"ca_total_tax": 0, "ca_agi": -5000}})
    assert P._prior_ca_tax_agi(p4) == (0.0, -5000.0)


def test_prior_tax_agi_federal_rejects_bool_nan_negative():
    # float(True) == 1.0 must not become a $1 prior-year tax.
    p1 = _Prior({"form_1040": {"line_24_total_tax": True, "line_11_agi": 100000}})
    assert P._prior_tax_agi(p1) == (None, 100000.0)
    p2 = _Prior({"form_1040": {"line_24_total_tax": float("nan"), "line_11_agi": 100000}})
    assert P._prior_tax_agi(p2) == (None, 100000.0)
    p3 = _Prior({"form_1040": {"line_24_total_tax": -1, "line_11_agi": 100000}})
    assert P._prior_tax_agi(p3) == (None, 100000.0)


def test_safe_harbor_prior_agi_missing_flagged_and_rendered():
    """Prior tax known but prior AGI unknown → 100% factor + loud
    may-be-UNDERSTATED note (the Form 2210 precedent)."""
    sh = P.compute_safe_harbor(50000, 30000, None, "MFJ",
                               withholding=0, estimates_paid=0)
    assert sh.prior_agi_missing is True
    assert sh.prior_factor == 1.00
    csh = P.compute_ca_safe_harbor(20000, 15000, None, 400000, "MFJ",
                                   withholding=0, estimates_paid=0)
    assert csh.prior_agi_missing is True
    assert csh.prior_factor == 1.00
    # Rendered: prior with CA tax but no ca_agi and no line_11_agi.
    prior = _Prior({"california_540": {"ca_total_tax": 5000},
                    "form_1040": {"line_24_total_tax": 15000}})
    r = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        _profile_mfj(), _tax_law_2026(), prior, 2026, datetime.date(2026, 7, 2))
    assert r.safe_harbor.prior_agi_missing is True
    assert r.ca_safe_harbor.prior_agi_missing is True
    md = P.render_markdown(r)
    assert md.count("may be UNDERSTATED") >= 2  # federal + CA notes


def test_ca_safe_harbor_rejects_non_canonical_filing_status():
    # "Married Filing Separately" silently taking the $1M (not $500K) cliff
    # and the $150K (not $75K) threshold is the understating direction.
    for bad in ("Married Filing Separately", "married", "", None):
        with pytest.raises(P.ProjectionError):
            P.compute_ca_safe_harbor(20000, 15000, 100000, 400000, bad,
                                     withholding=0, estimates_paid=0)


def test_ca_safe_harbor_de_minimis_flag():
    # tax minus withholding under $500 ($250 MFS) → no CA penalty (strict <).
    assert P.compute_ca_safe_harbor(400, None, None, 50000, "Single",
                                    withholding=0, estimates_paid=0).de_minimis is True
    assert P.compute_ca_safe_harbor(500, None, None, 50000, "Single",
                                    withholding=0, estimates_paid=0).de_minimis is False
    assert P.compute_ca_safe_harbor(300, None, None, 50000, "MFS",
                                    withholding=0, estimates_paid=0).de_minimis is False
    assert P.compute_ca_safe_harbor(200, None, None, 50000, "MFS",
                                    withholding=0, estimates_paid=0).de_minimis is True
    # Withholding counts: tax 10000 with 9700 withheld → gap 300 → de minimis.
    assert P.compute_ca_safe_harbor(10000, None, None, 50000, "Single",
                                    withholding=9700, estimates_paid=0).de_minimis is True


def test_ca_status_renders_de_minimis():
    # Under the de minimis no CA penalty can arise, so the PENALTY target is
    # covered (shortfall zeroed — DE 4 / penalty vouchers demand nothing);
    # the zero-balance target still shows the tax due.
    csh = P.compute_ca_safe_harbor(400, None, None, 50000, "Single",
                                   withholding=0, estimates_paid=0)
    assert csh.de_minimis is True
    assert csh.shortfall == 0.0
    assert csh.covered is True
    s = P._ca_status(csh)
    assert "de minimis" in s and "no CA underpayment penalty" in s


def test_ca_voucher_table_shows_renormalized_share():
    """P2: printing the statutory 30% next to a 100%-of-shortfall Q4 amount
    misleads — the table must show the share of the REMAINING shortfall."""
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 120000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 100},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        _profile_mfj(), _tax_law_2026(), prior, 2026, datetime.date(2026, 7, 2))
    md = P.render_markdown(r)
    assert "Share of remaining" in md
    assert "| Q4 | 2027-01-15 | 100% |" in md  # only weighted installment left
    assert "Earlier installments have closed" in md
    assert "pay catch-up amounts NOW" in md
    # Proxy caveat always present on the projection CA block.
    assert "federal-composition proxy" in md


def test_ca_near_boundary_warning_rendered():
    # Projected CA AGI ~980K (MFJ) — within 10% of the $1M cliff but not over.
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 900000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 490000, "federal_withholding": 120000,
                 "state_withholding": 40000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        _profile_mfj(), _tax_law_2026(), prior, 2026, datetime.date(2026, 7, 2))
    assert 900000 <= r.state.agi < 1000000
    assert r.ca_safe_harbor.high_income_90_only is False
    md = P.render_markdown(r)
    assert "within 10% of the R&TC §19136.3 threshold" in md


def test_ca_notes_include_farmers_fishermen_caveat():
    csh = P.compute_ca_safe_harbor(20000, None, None, 50000, "MFJ",
                                   withholding=0, estimates_paid=0)
    joined = "\n".join(P._ca_notes(csh))
    assert "5805F" in joined and "66" in joined


# ---------------------------------------------------------------------------
# Codex auto-review round (PR #35 P2s)
# ---------------------------------------------------------------------------

def test_ca_block_fires_degraded_without_prior_archive():
    """Codex P2: a first-year CA resident (no archive/) must still get the
    state projection + CA 540-ES schedule in degraded mode — the
    prior-state-section gate was only a "files this state" proxy."""
    profile = {"filing_status": "MFJ", "dependents": [],
               "address": {"state": "CA"}}
    r = P.build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        profile, _tax_law_2026(), None, 2026, datetime.date(2026, 7, 2))
    assert r.degraded is True
    assert r.state is not None
    assert r.ca_safe_harbor is not None
    assert r.ca_safe_harbor.degraded is True
    assert "CA 540-ES voucher schedule" in P.render_markdown(r)


def test_state_projection_still_none_without_prior_and_no_resident_match():
    # No prior AND no resident-state signal -> unchanged legacy behavior.
    assert P.project_state_tax(_proj(), None, _tax_law_2026(), "MFJ") is None
    # No prior and a NON-matching resident state -> still None.
    assert P.project_state_tax(_proj(), None, _tax_law_2026(), "MFJ",
                               resident_state="TX") is None


def test_state_agi_subtracts_half_se_tax():
    """Codex P2: CA Form 540 starts from FEDERAL AGI, which already nets the
    deductible half of SE tax — the §19136.3 proxy must not read gross SE
    income. ~$1.01M SE income => AGI under the $1M cliff."""
    law = _tax_law_2026()
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 900000}})
    st = P.project_state_tax(
        _proj(wages=0, ss_wages=0, medicare_wages=0,
              self_employment_net=1_010_000),
        prior, law, filing_status="MFJ")
    fed = P.project_federal_tax(
        _proj(wages=0, ss_wages=0, medicare_wages=0,
              self_employment_net=1_010_000),
        _profile_mfj(), law)
    assert st.agi == fed.agi  # both net the deductible half of SE tax
    assert st.agi < 1_000_000
    csh = P.compute_ca_safe_harbor(50000, 5000, 900000, st.agi, "MFJ",
                                   withholding=0, estimates_paid=0)
    assert csh.high_income_90_only is False  # prior-year harbor stays valid


def test_state_tax_includes_mental_health_surcharge():
    """Codex P2: Form 540 total tax includes the 1% mental-health services
    tax over $1M taxable income. 2,000,000 wages: ti = 1,989,000;
    brackets = 200 + 5% x 1,969,000 = 98,650; MH = 1% x 989,000 = 9,890."""
    law = _tax_law_2026()
    law["state"]["mental_health_surcharge"] = {"rate": 0.01, "threshold": 1000000}
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    st = P.project_state_tax(_proj(wages=2_000_000), prior, law,
                             filing_status="MFJ")
    assert st.taxable_income == 1_989_000
    assert st.tax == 98650 + 9890
    # Absent/None block -> no surcharge, no crash.
    law2 = _tax_law_2026()
    law2["state"]["mental_health_surcharge"] = {"rate": None, "threshold": None}
    st2 = P.project_state_tax(_proj(wages=2_000_000), prior, law2,
                              filing_status="MFJ")
    assert st2.tax == 98650


def test_ca_section_computed_ignores_bookkeeping_fields():
    """Codex P2: parsed W-2 Box 17 withholding must not vouch for untouched
    template zeros in the compute-output fields."""
    scaffold_with_wh = {"california_540": {
        "_include_if": "state is CA", "ca_total_tax": 0, "ca_agi": 0,
        "ca_standard_deduction": 0, "ca_withholding_box17": 4500,
        "ca_sdi_box14": 1200}}
    assert P._ca_section_computed(scaffold_with_wh) is False
    assert P._prior_ca_tax_agi(_Prior(scaffold_with_wh)) == (None, None)
    # Computed $0-tax return: non-zero standard deduction is evidence.
    computed_zero = {"california_540": {
        "ca_total_tax": 0, "ca_agi": 0, "ca_standard_deduction": 11080}}
    assert P._ca_section_computed(computed_zero) is True
