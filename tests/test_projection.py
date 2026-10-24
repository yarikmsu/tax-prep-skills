import datetime
import inspect
import types
import json
import re
import shutil
import subprocess
import sys

import pytest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_text import skill_text  # noqa: E402
from engine_tree import LIB as ENGINE_LIB, engine_files  # noqa: E402
from ny_fixtures import ny_state_cache  # noqa: E402
from agents.lib import projection as P  # noqa: E402
from agents.lib import tax_computations as TC  # noqa: E402
from agents.lib import irs_dates as I  # noqa: E402
from agents.lib.tax_computations import (  # noqa: E402
    DE_MINIMIS_BALANCE,
    compute_form_2210,
)


def test_status_key_helpers():
    assert P._full_status_key("MFJ") == "mfj"
    assert P._full_status_key("QSS") == "mfj"
    assert P._full_status_key("MFS") == "mfs"
    assert P._full_status_key("HOH") == "hoh"
    assert P._full_status_key("single") == "single"
    assert P._binary_status_key("QSS") == "mfj"
    assert P._binary_status_key("HOH") == "single"


@pytest.mark.parametrize("helper", ["_full_status_key", "_binary_status_key",
                                    "_capital_loss_limit"])
@pytest.mark.parametrize("status", [None, "", "   ", "Married Filing Jointly",
                                    "Married Filing Separately", "MFJ ", "s"])
def test_the_status_helpers_refuse_rather_than_defaulting_to_single(helper, status):
    """The guard belongs in the helper, not at whichever caller remembered it.

    `_full_status_key` used to end in `.get(fs, "single")` and
    `_binary_status_key`/the inline capital-loss tests in
    `(filing_status or "").upper() == ...`, so every non-canonical spelling
    resolved to the single column in silence. The value check was added at the
    projection and withholding call sites only, which left three skills —
    /tax-scenario, /tax-roth-convert, /tax-harvest — reading the status
    straight off result.json meta and through `_rate_kwargs` into these
    helpers unchecked. Single brackets and the single standard deduction
    UNDER-state the tax on an MFJ or HoH filer, which is the direction
    AGENTS.md calls a hard error.

    "MFJ " is here because `_status_text` strips before checking: trailing
    whitespace out of a hand-edited JSON file is canonical, not a refusal.
    """
    if status == "MFJ ":
        assert getattr(P, helper)(status) in ("mfj", P.CAPITAL_LOSS_LIMIT)
        return
    with pytest.raises(P.ProjectionError):
        getattr(P, helper)(status)


def test_an_absent_filing_status_is_refused_as_missing_not_unrecognized():
    """Absent and unrecognized send the reader to different files.

    Both used to raise "got an unrecognized value (0 chars)", which describes
    a value that is not there and names no remedy — a reader hunts for a bad
    spelling in a file where the key is simply gone.
    """
    for absent in (None, "", "  "):
        with pytest.raises(P.ProjectionError) as exc:
            P._canonical_status(absent, "federal projection")
        assert "is missing" in str(exc.value)
        assert "meta.filing_status" in str(exc.value)
        assert "unrecognized" not in str(exc.value)

    with pytest.raises(P.ProjectionError) as exc:
        P._canonical_status("Married Filing Jointly", "federal projection")
    assert "unrecognized" in str(exc.value)
    assert "is missing" not in str(exc.value)
    # Never the raw content: a malformed profile can carry a name here.
    assert "Married" not in str(exc.value)


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
    # AA21, AA22: unanswered is null, never 0 — a 0 placeholder would read as
    # a declared standard deduction, or as no gifts, and over-state the tax.
    for key in ("itemized_deductions", "state_itemized_deductions",
                "charitable_cash_gifts"):
        assert tmpl["forecast_overrides"][key] is None, key


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


@pytest.mark.parametrize("recurring, reason", [
    ({"ordinary_dividends": 1200, "qualified_dividends": -6000}, "non-negative"),
    ({"ordinary_dividends": -1200, "qualified_dividends": 0}, "non-negative"),
    ({"ordinary_dividends": 1200, "qualified_dividends": 1500}, "cannot exceed"),
])
def test_annualize_refuses_a_dividend_pair_that_is_not_a_split(recurring, reason):
    """`qualified_dividends` went through `_num`, so a sign slip of -6000
    beside a $5,000 LTCG summed to a negative `preferential_income`, which
    the X2 guard read as "nothing to price" — and the gain was taxed at
    ordinary rates with no refusal, the fall-through by the input door. Box 1b
    is the qualified part of box 1a, so it is non-negative and never larger."""
    as_of = datetime.date(2026, 7, 2)
    with pytest.raises(P.ProjectionError, match=reason):
        P.annualize(_ytd_half_year(), as_of, "biweekly",
                    {"capital_gains_long": 5000}, recurring, {"federal": 0, "state": 0})


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
            "capital_gains_rates": {
                "zero_pct_limit": {"mfj": 90000, "single": 45000, "mfs": 45000, "hoh": 60000},
                "fifteen_pct_limit": {"mfj": 550000, "single": 490000, "mfs": 275000, "hoh": 520000}},
            "child_tax_credit": {"amount_per_child": 2000, "phase_out_start": {"mfj": 400000, "single": 200000}},
            "additional_medicare_tax": {"rate": 0.009, "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}},
            "social_security": {"wage_base": 176100},
            "qbi_deduction_rate": 0.20,
        },
        "state": {"code": "CA", "standard_deduction": {"mfj": 11000, "single": 5500},
                  "brackets": [{"rate": 0.01, "min": 0, "max": 20000},
                               {"rate": 0.05, "min": 20000, "max": None}]},
    }


def _legacy(law):
    """The cache shape before X1: breakpoints keyed single/mfj only."""
    for limit in law["federal"]["capital_gains_rates"].values():
        limit.pop("mfs", None)
        limit.pop("hoh", None)
    return law


def _profile_mfj(deps=None):
    return {"filing_status": "MFJ", "dependents": deps or []}


def _declared_standard(inputs: dict) -> dict:
    """AA21 made the year's deductions a required declaration, and AA22 the
    year's cash gifts. Every test written before them priced on the standard
    deduction and no gifts, so this declares 0 for each wherever a test leaves
    the question out."""
    overrides = dict(inputs.get("forecast_overrides") or {})
    overrides.setdefault("itemized_deductions", 0)
    overrides.setdefault("state_itemized_deductions", 0)
    overrides.setdefault("charitable_cash_gifts", 0)
    return dict(inputs, forecast_overrides=overrides)


def _build_report(inputs, *args, **kwargs):
    """`P.build_report` with `_declared_standard` applied to its inputs."""
    return P.build_report(_declared_standard(inputs), *args, **kwargs)


def _report(overrides=None, profile=None, tax_law=None, prior=None):
    """A TY2026 report on $50,000 of wages to July 2, through `_build_report`."""
    return _build_report(
        {"pay_frequency": "biweekly", "ytd": {"wages": 50000},
         "forecast_overrides": overrides or {}},
        profile or _profile_mfj(), tax_law or _tax_law_2026(), prior,
        2026, datetime.date(2026, 7, 2))


def _proj(**overrides):
    base = dict(
        wages=100000, federal_withholding=12000, state_withholding=0, medicare_wages=100000,
        medicare_withheld=1450, ss_wages=100000, capital_gains=0, qualified_dividends=0,
        ordinary_dividends=0, taxable_interest=0, self_employment_net=0, other_income=0,
        estimated_fed_paid=0, estimated_state_paid=0, elapsed_fraction=0.5, paychecks_remaining=13,
        itemized_deductions=0, state_itemized_deductions=0, charitable_cash_gifts=0)
    base.update(overrides)
    return P.ProjectedInputs(**base)


def test_project_federal_refuses_preferential_income_without_cg_limits():
    """X2: `project_federal_tax` has its own worksheet branch (it never calls
    `compute_scenario`) with the same fall-through — qualified dividends or a
    net LTCG priced at ordinary rates when a limit is None. Over-states the
    projected tax and every safe-harbor figure derived from it."""
    law = _tax_law_2026()
    law["federal"]["capital_gains_rates"]["fifteen_pct_limit"] = {}
    with pytest.raises(P.ProjectionError, match="tax-setup"):
        P.project_federal_tax(_proj(qualified_dividends=5000),
                              _profile_mfj(), law)
    del law["federal"]["capital_gains_rates"]
    with pytest.raises(P.ProjectionError, match="tax-setup"):
        P.project_federal_tax(_proj(capital_gains=5000), _profile_mfj(), law)


def test_project_federal_ordinary_only_still_runs_without_cg_limits():
    law = _tax_law_2026()
    del law["federal"]["capital_gains_rates"]
    fed = P.project_federal_tax(_proj(), _profile_mfj(), law)
    assert fed.tax_before_credits == 12000


def test_capital_gains_limits_accessor_reads_the_full_status_key():
    """X1: §1(h) gives head of household its own 0%/15% breakpoints and
    married filing separately half of joint, so the accessor reads the FULL
    status key (QSS → mfj). A legacy cache keyed single/mfj reads None for
    HoH and MFS — never the single figure — and X2's guard then refuses.
    An absent block reads as (None, None), never a KeyError."""
    law = _tax_law_2026()
    assert P.capital_gains_limits(law, "MFJ") == (90000, 550000)
    assert P.capital_gains_limits(law, "QSS") == (90000, 550000)
    assert P.capital_gains_limits(law, "Single") == (45000, 490000)
    assert P.capital_gains_limits(law, "HoH") == (60000, 520000)
    assert P.capital_gains_limits(law, "MFS") == (45000, 275000)
    legacy = _legacy(_tax_law_2026())
    assert P.capital_gains_limits(legacy, "HoH") == (None, None)
    assert P.capital_gains_limits(legacy, "MFS") == (None, None)
    assert P.capital_gains_limits({"federal": {}}, "Single") == (None, None)
    assert P.capital_gains_limits({}, "MFJ") == (None, None)
    with pytest.raises(P.ProjectionError):
        P.capital_gains_limits(law, "Married Filing Jointly")


@pytest.mark.parametrize("zero, fifteen", [
    (float("nan"), 490000), (45000, float("nan")),
    (float("inf"), 490000), (45000, float("-inf")),
    (0, 490000), (-45000, 490000), (True, 490000), ("45000", 490000),
])
def test_capital_gains_limits_refuses_a_breakpoint_that_is_not_a_positive_number(
        zero, fifteen):
    """The X2 guard asks `is None`; `load_tax_law` is a bare `json.loads`, so
    NaN and Infinity reach the accessor and pass it. In the worksheet
    `min(qdcg, nan - ordinary)` keeps qdcg (every NaN comparison is False), so
    the whole preferential stack is taxed at 0% — the under-statement door.
    Zero or a negative pushes it out of the 0% band: the over-statement door.
    Both are refused at the one read site, so every keystone inherits it."""
    law = _tax_law_2026()
    law["federal"]["capital_gains_rates"]["zero_pct_limit"]["single"] = zero
    law["federal"]["capital_gains_rates"]["fifteen_pct_limit"]["single"] = fifteen
    with pytest.raises(P.ProjectionError, match="positive finite"):
        P.capital_gains_limits(law, "Single")
    assert P.capital_gains_limits(law, "MFJ") == (90000, 550000)


def test_capital_gains_limits_refuses_an_inverted_pair():
    law = _tax_law_2026()
    law["federal"]["capital_gains_rates"]["fifteen_pct_limit"]["single"] = 40000
    with pytest.raises(P.ProjectionError, match="below"):
        P.capital_gains_limits(law, "Single")


def test_capital_gains_limits_refuses_a_scalar_where_the_status_map_belongs():
    """A cache that stores the breakpoint as a scalar instead of the per-status
    map used to raise AttributeError, a class no CLI boundary lists. It is a
    corrupt cache, so it fails closed like a corrupt value; only an ABSENT
    block or key reads as `None`."""
    law = _tax_law_2026()
    law["federal"]["capital_gains_rates"]["zero_pct_limit"] = 45000
    with pytest.raises(P.ProjectionError, match="per-status map"):
        P.capital_gains_limits(law, "Single")
    del law["federal"]["capital_gains_rates"]["zero_pct_limit"]
    assert P.capital_gains_limits(law, "Single") == (None, 490000)


def test_a_nan_breakpoint_reaches_the_projection_as_a_refusal_not_a_zero_rate():
    law = _tax_law_2026()
    law["federal"]["capital_gains_rates"]["zero_pct_limit"]["mfj"] = float("nan")
    with pytest.raises(P.ProjectionError, match="positive finite"):
        P.project_federal_tax(_proj(qualified_dividends=5000), _profile_mfj(), law)


def test_project_federal_prices_hoh_on_its_own_breakpoints_or_refuses():
    """The collapse onto `single` under-stated an HoH filer's 0% band. With
    the HoH breakpoint fetched, a $60K-taxable HoH filer's qualified
    dividends sit inside the 0% band that the single breakpoint would have
    put at 15%; on a legacy cache the same filer is refused, not priced on
    the single figure."""
    law = _tax_law_2026()
    law["federal"]["filing_statuses"]["hoh"] = law["federal"]["filing_statuses"]["single"]
    profile = {"filing_status": "HoH", "dependents": []}
    inputs = _proj(wages=65000, qualified_dividends=10000, ordinary_dividends=10000)
    hoh = P.project_federal_tax(inputs, profile, law)
    law["federal"]["capital_gains_rates"]["zero_pct_limit"]["hoh"] = 45000
    as_single = P.project_federal_tax(inputs, profile, law)
    assert hoh.tax_before_credits < as_single.tax_before_credits
    legacy = _legacy(_tax_law_2026())
    legacy["federal"]["filing_statuses"]["hoh"] = legacy["federal"]["filing_statuses"]["single"]
    with pytest.raises(P.ProjectionError, match="tax-setup"):
        P.project_federal_tax(inputs, profile, legacy)


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
    report = _build_report(
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
    vouchers = P.compute_estimated_vouchers(shortfall=12000 - 3000, as_of=as_of)
    assert len(vouchers) == 3
    assert vouchers[0].quarter == 2
    assert vouchers[-1].due_date == "2027-01-15"
    assert sum(v.amount for v in vouchers) == pytest.approx(9000, abs=0.05)


def test_vouchers_zero_when_covered():
    as_of = datetime.date(2026, 5, 1)
    vouchers = P.compute_estimated_vouchers(10000 - 12000, as_of)
    assert all(v.amount == 0.0 for v in vouchers)


def test_vouchers_all_past_folds_into_final():
    as_of = datetime.date(2026, 12, 20)
    vouchers = P.compute_estimated_vouchers(8000, as_of)
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
    if not degraded:
        inputs["prior_year_form_2210_tax_base"] = 15000
    if not with_state:
        law["state"] = {}
    return _build_report(inputs=inputs, profile=_profile_mfj(), tax_law=law,
                          prior=prior, year=2026, as_of=datetime.date(2026, 7, 2))


def test_build_report_assembles_all_sections():
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "prior_year_form_2210_tax_base": 15000,
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


def test_render_markdown_federal_voucher_schedule_carries_the_catchup_note():
    """The federal schedule under-funds the earliest open deadline, silently.

    `compute_estimated_vouchers` splits the ANNUAL shortfall evenly across the
    deadlines still open, while Form 2210 requires 25% of the required annual
    payment cumulatively by each date — so from July the September voucher is
    smaller than September itself requires, and an instalment already missed
    keeps accruing until it is PAID rather than until the next date arrives.
    The CA block has warned about this since it shipped; the federal block
    printed the table bare, which is the same understatement reaching more
    users. AGENTS.md: a fallback that UNDER-states degrades with a banner.
    """
    md = _render_at(datetime.date(2026, 7, 2))
    es = md.split("### 1040-ES voucher schedule", 1)
    assert len(es) == 2, "the 1040-ES section is no longer rendered under that heading"
    section = es[1].split("### W-4", 1)[0]
    assert P._ES_VOUCHER_CATCHUP_NOTE in section, (
        "the 1040-ES voucher table renders without its catch-up note, so a "
        "reader who never opens /tax-safe-harbor sees an under-stated earliest "
        "voucher with nothing marking it as one")
    assert "catch-up" in P._ES_VOUCHER_CATCHUP_NOTE, (
        "the note no longer tells the reader to pay NOW — naming the "
        "simplification without the remedy leaves the accrual running")
    doc = P.compute_estimated_vouchers.__doc__ or ""
    assert "UNDERSTATEMENT" in doc, (
        "`compute_estimated_vouchers` no longer documents its even-split "
        "simplification. /tax-safe-harbor step 0 tells a departing workspace "
        "user that each voucher function's own docstring admits it")


def _render_at(as_of, withholding=2000, state_withholding=700):
    """A rendered report at `as_of`. The default YTD withholding leaves a
    real federal AND California shortfall, so the voucher schedules carry
    non-zero amounts and the catch-up notes have something to be true of."""
    return P.render_markdown(_report_at(as_of, withholding, state_withholding))


def _report_at(as_of, withholding=2000, state_withholding=700, today=None):
    """The same fixture unrendered, for assertions about the OBJECT.

    Raising the withholding far enough puts the run under one or both de
    minimis floors, which is the only way to pin what the report hands the
    voucher builders now that the builders take a shortfall and cannot
    re-derive one.

    `today` defaults to None — the calendar notes stay silent unless a test
    names a date, so the fixture's other assertions are not coupled to when
    the suite runs.
    """
    return (_build_report(
        inputs={"pay_frequency": "biweekly",
                "prior_year_form_2210_tax_base": 15000,
                "ytd": {"wages": 50000, "federal_withholding": withholding,
                        "state_withholding": state_withholding},
                "forecast_overrides": {"capital_gains": 0},
                "recurring_from_prior": {"taxable_interest": 0},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile=_profile_mfj(), tax_law=_tax_law_2026(),
        prior=_Prior({"california_540": {"ca_total_tax": 5000},
                      "form_1040": {"line_24_total_tax": 15000,
                                    "line_11_agi": 120000}}),
        year=2026, as_of=as_of, today=today))


def test_the_report_hands_the_voucher_schedule_the_harbors_own_shortfall():
    """The guarantee the `de_minimis` flag used to give, moved up a level.

    `compute_estimated_vouchers` took a REQUIREMENT and re-derived the gap
    from it, so the harbor's §6654(e)(1) suppression had to be forwarded as a
    separate boolean and applied a second time. Both builders now take the
    shortfall itself, which makes the suppression propagate by construction —
    and moves the thing that can break from "the flag was passed" to "the
    caller passed the harbor's own figure".

    That is what this pins, on the report rather than on the function: raise
    the withholding until both floors apply and no schedule may print a cent.
    A caller reverting to `max(0, required - withholding - paid)` computes a
    positive number here and fails.
    """
    rep = _report_at(datetime.date(2026, 7, 2), withholding=6000,
                     state_withholding=3000)
    assert rep.safe_harbor.de_minimis and rep.ca_safe_harbor.de_minimis, (
        "the fixture no longer lands under both floors, so a schedule that "
        "re-derived its own shortfall would look identical")
    assert rep.safe_harbor.shortfall == 0.0 == rep.ca_safe_harbor.shortfall
    assert [v.amount for v in rep.vouchers] == [0.0, 0.0], (
        "the federal penalty schedule prints an instalment under the floor — "
        "the harbor's suppressed shortfall is not what reached it")
    assert all(v.amount == 0.0 for v in rep.ca_vouchers), (
        "the CA penalty schedule prints an instalment under R&TC §19136")
    # The counterweight, on the same fixture: drop the withholding back and
    # both schedules have to come alive, or the assertions above pin plumbing
    # rather than the floors.
    live = _report_at(datetime.date(2026, 7, 2))
    assert not live.safe_harbor.de_minimis
    assert any(v.amount > 0 for v in live.vouchers) and any(
        v.amount > 0 for v in live.ca_vouchers), (
        "no schedule pays anything even off the floor, so this test would "
        "pass against a builder that always returns zeros")


def test_the_de_minimis_cliff_is_only_claimed_when_something_attaches():
    """"$X more tax and it attaches" is FALSE wherever a prong already covers.

    $5,000 of tax against $4,500 withheld and a $4,000 prior-year prong is
    under the floor by $500 — and at $5,500 of tax the prior prong still binds
    at $4,000 against $4,500 paid, so nothing attaches there either. The cell
    asserted otherwise in every de-minimis case, which is AGENTS.md's
    hard-error direction arriving as a warning rather than as a total.

    The counterweight is the same figures with no prior year, where the
    warning is true and both of the paths it names bite: $5,500 of tax puts
    the 90% prong at $4,950 against $4,500 paid ($450), and $500 less
    withholding leaves $4,000 against a requirement that has not moved ($500).
    One field per path, and the cell may name both only when both are
    positive — see the test below for the case where exactly one is. Both
    directions matter here — silence where a penalty really is one
    projection-miss away is the under-stating failure.
    """
    covered = P.compute_safe_harbor(5000.0, 4000.0, 100000.0, "single",
                                    withholding=4500.0, estimates_paid=0.0)
    assert covered.de_minimis and covered.de_minimis_margin == 500.0
    assert (covered.de_minimis_cliff_more_tax
            == covered.de_minimis_cliff_less_withholding == 0.0), (
        "the prior-year prong binds at $4,000 against $4,500 paid and does "
        "not move with the current year, so nothing attaches past the floor "
        "on EITHER path")
    cell = P._federal_status(covered)
    assert "it attaches" not in cell and "nothing attaches" in cell, (
        f"the status tells a fully-covered taxpayer a penalty is $500 of "
        f"income away: {cell!r}")
    # And the report agrees with itself one step past the cliff.
    past = P.compute_safe_harbor(5500.0, 4000.0, 100000.0, "single",
                                 withholding=4500.0, estimates_paid=0.0)
    assert not past.de_minimis and past.covered and past.shortfall == 0.0, (
        "past the floor this fixture is meant to be plainly covered — that "
        "contradiction is what made the warning detectable")

    degraded = P.compute_safe_harbor(5000.0, None, None, "single",
                                     withholding=4500.0, estimates_paid=0.0)
    assert degraded.de_minimis, "the fixture is meant to sit under the floor"
    assert (degraded.de_minimis_cliff_more_tax == 450.0
            and degraded.de_minimis_cliff_less_withholding == 500.0), (
        "with no prior-year prong both cliff paths bite — $450 on the tax "
        "side, $500 on the withholding side — and each is reported as its own "
        "path, because the sentence may only claim the ones that fire")
    assert "it attaches" in P._federal_status(degraded), (
        "the cliff warning went silent where a penalty IS one projection-miss "
        "away — the under-stating direction")


def test_each_cliff_path_is_named_only_by_the_report_that_can_take_it():
    """One worse-of-the-two field could not say WHICH way the floor lapses.

    $500 of computed tax against $0 withheld, no prior year. The floor cannot
    lapse through withholding at all: the margin is $500 and there is no
    withholding to give up — below a $1,000 tax no pace reaches the floor,
    which is the same statement as `withholding < de_minimis_margin`. The only
    path is more tax, and it is worth $900, the 90% prong at the $1,000 of tax
    where the floor goes, against nothing paid.

    Carried as a maximum, that $900 reached /tax-withholding's renderer, which
    attributes every positive to withholding — so a taxpayer with no
    withholding was told $500 less of it would trigger a penalty, about a
    figure that cannot fall, against a tax the same report calls actual.
    """
    sh = P.compute_safe_harbor(500.0, None, None, "single",
                               withholding=0.0, estimates_paid=0.0)
    assert sh.de_minimis and sh.de_minimis_margin == 500.0
    assert sh.de_minimis_cliff_more_tax == 900.0, (
        "the floor lapses at $1,000 of tax, where the 90% prong asks $900 "
        "against nothing paid")
    assert sh.de_minimis_cliff_less_withholding == 0.0, (
        "$0 of withholding cannot fall by the $500 margin, so this path is "
        "not open — counting it anyway is what the max() did")
    project = P._federal_status(sh)
    assert "more tax than projected" in project, (
        f"/tax-project projects the tax and this path IS open to it: {project!r}")
    assert "less withholding" not in project, (
        f"/tax-project names a withholding path that cannot be taken: {project!r}")
    trueup = P._federal_status(sh, tax_is_actual=True)
    assert "it attaches" not in trueup, (
        f"/tax-withholding warns about a cliff neither of its inputs reaches: "
        f"{trueup!r}")
    assert "under the floor on its own" in trueup, (
        f"and the generic 'nothing attaches' tail gives the WRONG reason here "
        f"— the requirement is NOT met, the floor simply cannot lapse: "
        f"{trueup!r}")


def test_the_current_year_tax_is_disclosed_as_unnetted_wherever_it_asks_for_money():
    """§6654 measures Form 2210 line 4; both callers hand over line 24.

    `compute_form_2210` states that contract on its own argument and neither
    caller here can meet it: `project_federal_tax` applies the child tax credit
    as a nonrefundable credit only and models no EIC or ACTC, and result.json
    carries a field for neither. Line 4 can only be LOWER than line 24, so the
    90% prong and the $1,000 floor both come out too high — $5,000 of tax,
    $1,000 of refundable AOTC and $3,500 withheld is a true $500 balance the
    floor excuses, shown here as $1,500 with a payment to make. Over-stating
    what the taxpayer must pay is the hard-error direction, so it is
    disclosed rather than left silent.

    Gated on a shortfall actually being asserted. Netting cannot take a run
    OUT of the floor — line 4 <= line 24 — so warning under it would be noise,
    and a covered year is not asking for anything to over-state.
    """
    asks = P.compute_safe_harbor(5000.0, None, None, "single",
                                 withholding=3500.0, estimates_paid=0.0)
    assert asks.shortfall == 1000.0 and asks.current_tax_unnetted, (
        "the run that prints a shortfall off a line-24 figure is exactly the "
        "one the disclosure exists for")
    under_floor = P.compute_safe_harbor(5000.0, None, None, "single",
                                        withholding=4500.0, estimates_paid=0.0)
    assert under_floor.de_minimis and not under_floor.current_tax_unnetted, (
        "netting only lowers the balance, so a run already under the floor "
        "cannot be taken out of it")
    covered = P.compute_safe_harbor(20000.0, None, None, "single",
                                    withholding=0.0, estimates_paid=20000.0)
    assert covered.covered and not covered.de_minimis
    assert not covered.current_tax_unnetted, (
        "a year asking for nothing has no figure to over-state")
    csh = P.compute_ca_safe_harbor(20000.0, None, None, 200000.0, "single",
                                   withholding=1000.0, estimates_paid=0.0)
    assert csh.current_tax_unnetted, (
        "R&TC §19136 conforms to §6654 and the CA harbor runs on the computed "
        "Form 540 total the same way — the gap is not federal-only")

    md = _render_at(datetime.date(2026, 7, 2))
    assert P._CURRENT_TAX_UNNETTED_NOTE in md, (
        "/tax-project prints a shortfall off a line-24 figure with nothing "
        "saying so — the prior-year half of the same §6654(f) gap has carried "
        "a note since it shipped")
    assert "Form 2210 line 4" in md and "OVERSTATED" in md, (
        "the note must name the figure §6654 actually measures and give the "
        "direction of the error, or a reader cannot act on it")
    assert P._CA_CURRENT_TAX_UNNETTED_NOTE in md, (
        "the CA block prints its own shortfall off the computed Form 540 "
        "total with the same gap and no disclosure")
    for federal_only in ("earned income credit", "additional child tax credit",
                         "Schedule 3 line 11", "Form 2210"):
        assert federal_only not in P._CA_CURRENT_TAX_UNNETTED_NOTE, (
            f"the CA note names {federal_only!r} — that is the FEDERAL list, "
            f"and which credits FTB 5805 nets is not something this engine "
            f"has checked")


def test_the_two_6654_floor_implementations_cannot_disagree():
    """One statute, two copies — and the filed-return copy is the costly one.

    `compute_form_2210` prices the penalty on the return actually filed;
    `compute_safe_harbor` prices the planning target. Both apply §6654(e)(1)'s
    strict `< $1,000` to (tax - withholding), and raw float subtraction of two
    already-rounded dollar figures reaches 999.9999999999982 on balances that
    are exactly $1,000.00 — clearing a penalty that attaches, under a message
    that reads "$1,000.00 is under the $1,000 de minimis". Rounding was added
    to the planning copy first, which is what made the two disagree.
    """
    from agents.lib.tax_computations import compute_form_2210 as f2210
    exact = []
    x = 0.01
    while x < 9000 and len(exact) < 3:
        a, b = round(x, 2), round(x - 1000, 2)
        if b > 0 and (a - b) != 1000.0:
            exact.append((a, b))
        x = round(x + 0.01, 2)
    assert exact, "no float-imprecise exact-$1,000 pair found; fixture stale"
    for tax, wh in exact:
        assert f2210(tax, wh, "single", 2026)["no_penalty"] is False, (
            f"tax={tax} wh={wh}: an exact $1,000.00 balance cleared the "
            f"§6654(e)(1) floor on the FILED-return path")
        assert P.compute_safe_harbor(
            tax, None, None, "single", withholding=wh,
            estimates_paid=0.0).de_minimis is False

    # A cent under really is under, in both — or the guard above is vacuous.
    for tax, wh in ((5000.0, 4000.01),):
        assert f2210(tax, wh, "single", 2026)["no_penalty"] is True
        assert P.compute_safe_harbor(
            tax, None, None, "single", withholding=wh,
            estimates_paid=0.0).de_minimis is True


def test_the_prior_zero_exception_states_all_three_of_its_conditions():
    """§6654(e)(2) remains a three-part test in the computation result.

    Named for three and asserting two is how this test passed while the string
    it guards enumerated the 12-month and citizenship halves and called them
    "the §6654(e)(2) preconditions". The missing one is (B), no LIABILITY —
    the only one of the three a $0 `prior_year_tax` does not establish, since
    that argument is net of the refundable credits Form 2210 line 8 subtracts
    and Form 1040's total-tax line sits above them.

    The result string also has to say the engine did not VERIFY it. Nothing
    here can see a line 24, so the finding rests entirely on the caller's
    confirmation, and a reason that reads as a derivation invites the next
    reader to trust it as one.
    """
    from agents.lib.tax_computations import compute_form_2210
    reason = compute_form_2210(
        50000.0, 0.0, "single", 2026, prior_year_tax=0,
        prior_year_zero_liability_confirmed=True)["no_penalty_reason"]
    for fragment in ("12-month prior year", "NO LIABILITY",
                     "citizenship or residence"):
        assert fragment in reason, (
            f"the engine's §6654(e)(2) branch no longer states {fragment!r}, "
            f"so this note has nothing left to agree with: {reason}")
    assert "TOTAL TAX line" in reason, (
        "the reason names the liability condition without saying what it "
        "reads off, which is the whole distinction between it and the $0 "
        "base that reached this branch")
    assert "nothing in this engine verified it" in reason, (
        "the reason reads as a finding this engine made. It cannot — there is "
        "no line 24 here — and presenting a caller's confirmation as a "
        "computation is how it stops being questioned")


def test_a_year_covered_by_an_undated_estimate_still_gets_the_timing_caveat():
    """Nothing outstanding is not nothing was late.

    $20,000 required, no withholding, a single $20,000 estimate: every voucher
    prints $0.00 and the cumulative test finds nothing outstanding, because
    nothing IS — the ANNUAL requirement is met. April, June and September were
    not. Estimated payments arrive here as a TOTAL with no dates, so this
    report cannot tell that case from four timely payments, and returning None
    deleted the pointer to the dated test in exactly the run that needs it.
    Silence about an accrued penalty is the under-stating direction.

    The counterweight is the same year paid by WITHHOLDING, where silence is
    the right answer: §6654(g)(1) deems withholding paid in equal parts on
    each due date, so a year it covers is covered at every instalment. And
    under a de minimis, where no penalty arises at any pace and a timing
    caveat would be a claim about nothing.
    """
    as_of = datetime.date(2026, 10, 1)
    vouchers = P.compute_estimated_vouchers(0.0, as_of)
    assert vouchers and all(v.amount == 0.0 for v in vouchers), (
        "the fixture no longer produces the zeroed schedule this pins")
    assert P._voucher_catchup_note(
        P._FEDERAL_CATCHUP, vouchers,
        _harbor(20000.0, 0.0, 20000.0), as_of) == P._ES_VOUCHER_TIMING_SETTLED, (
        "a $20,000 estimate that could have landed on September 30 leaves the "
        "report saying nothing at all about April, June and September")
    assert P._voucher_catchup_note(
        P._FEDERAL_CATCHUP, vouchers,
        _harbor(20000.0, 20000.0, 0.0), as_of) is None, (
        "§6654(g)(1) spreads withholding across the instalments, so a year it "
        "covers needs no timing caveat — firing here would over-warn")
    assert P._voucher_catchup_note(
        P._FEDERAL_CATCHUP, vouchers,
        _harbor(20000.0, 0.0, 20000.0, de_minimis=True), as_of) is None, (
        "under §6654(e)(1) no penalty arises at any pace, so a timing caveat "
        "there is a claim about nothing")
    ca = P.compute_ca_estimated_vouchers(0.0, as_of)
    assert P._voucher_catchup_note(
        P._CA_CATCHUP, ca,
        _harbor(20000.0, 0.0, 20000.0), as_of) == P._CA_VOUCHER_TIMING_SETTLED, (
        "the CA reader is told about four equal federal instalments — CA has "
        "three, weighted 30/40/0/30 on both lines of Worksheet II")


def test_the_timing_caveat_never_claims_a_funded_schedule_is_settled():
    """Two ways one text over-stated: the lede, and the date it fired on.

    It opened "Nothing further is outstanding against the ANNUAL requirement"
    in every case the gate reached, including the one it was not written for —
    nothing OVERDUE, with the year's remaining instalments printed directly
    below it. A $20,000 requirement with $15,000 of estimates in by July 1
    prints two $2,500 vouchers under a sentence saying the annual requirement
    is met. Over-stating what has been paid is the hard-error direction, and
    here it arrived attached to an under-statement warning.

    And before the FIRST deadline passes there is no timing question at all:
    instalment 1 is the earliest one that can run late and it cannot do so
    before its own due date, so every payment made by then is timely for every
    instalment. The note fired in March all the same, at a taxpayer who cannot
    yet be late — a gate firing on correct data is the one that gets read past.

    The sweep is the general form of the first half: whichever schedule and
    date produce the settled lede, the schedule it sits under must be
    all-zero.
    """
    for tag, build, catchup, settled in (
            ("fed", P.compute_estimated_vouchers, P._FEDERAL_CATCHUP,
             P._ES_VOUCHER_TIMING_SETTLED),
            ("ca", P.compute_ca_estimated_vouchers, P._CA_CATCHUP,
             P._CA_VOUCHER_TIMING_SETTLED)):
        for as_of in (datetime.date(2026, 1, 2), datetime.date(2026, 3, 1),
                      datetime.date(2026, 4, 15)):
            vouchers = build(5000.0, as_of)
            assert vouchers[0].quarter == 1, (
                f"the {tag} fixture at {as_of} no longer has April 15 open, "
                f"so it pins nothing about the window before it")
            assert P._voucher_catchup_note(
                catchup, vouchers,
                _harbor(20000.0, 0.0, 15000.0), as_of) is None, (
                f"the {tag} timing caveat fires at {as_of}, before any "
                f"deadline has passed — every estimate paid by then is timely "
                f"for every instalment, so there is no late instalment to "
                f"warn about")

        checked = 0
        for req in (5000.0, 20000.0, 41000.0):
            for wh in (0.0, 6000.0):
                for paid in (2500.0, 15000.0, 41000.0):
                    for as_of in (datetime.date(2026, 4, 16),
                                  datetime.date(2026, 7, 1),
                                  datetime.date(2026, 10, 1),
                                  datetime.date(2026, 12, 31)):
                        vouchers = build(max(0.0, req - wh - paid), as_of)
                        note = P._voucher_catchup_note(
                            catchup, vouchers, _harbor(req, wh, paid), as_of)
                        checked += 1
                        if note != settled:
                            continue
                        assert all(v.amount == 0.0 for v in vouchers), (
                            f"{tag} at {as_of}: req={req} wh={wh} paid={paid} "
                            f"— the note says nothing further is outstanding "
                            f"against the ANNUAL requirement while the "
                            f"schedule below it still asks for "
                            f"{[v.amount for v in vouchers]}")
        assert checked == 72, (
            f"the {tag} grid stopped producing the 72 cases this sweeps")

    for body, ledes in (
            (P._ES_TIMING_BODY, (P._ES_VOUCHER_TIMING_SETTLED,
                                 P._ES_VOUCHER_TIMING_SCHEDULED)),
            (P._CA_TIMING_BODY, (P._CA_VOUCHER_TIMING_SETTLED,
                                 P._CA_VOUCHER_TIMING_SCHEDULED))):
        for lede in ledes:
            assert lede.endswith(body), (
                "the two ledes no longer share one body, so the statute, the "
                "dated test and the withholding carve-out can now be right in "
                "one of them and stale in the other")


def test_the_gap_the_floor_suppressed_survives_on_the_harbor():
    """`shortfall` is zeroed under the floor; something has to keep the gap.

    It was overwritten IN PLACE, so the payload carried no record of what the
    harbor actually asked and the rendered table printed Required $4,500 /
    Paid $4,001 / Shortfall $0 — three numbers that do not reconcile. It is
    also the input the cliff test above needs, which is why destroying it left
    the warning with nothing to be conditional on.
    """
    sh = P.compute_safe_harbor(5000.0, None, None, "single",
                               withholding=4001.0, estimates_paid=0.0)
    assert sh.shortfall == 0.0 and sh.de_minimis
    assert sh.shortfall_before_de_minimis == 499.0, (
        f"$4,500 required against $4,001 paid is a $499 gap; the harbor "
        f"reports {sh.shortfall_before_de_minimis} for what it suppressed")
    off = P.compute_safe_harbor(5000.0, None, None, "single",
                                withholding=4000.0, estimates_paid=0.0)
    assert not off.de_minimis, "a $1,000 balance is not under a $1,000 floor"
    assert off.shortfall == off.shortfall_before_de_minimis == 500.0, (
        "off the floor the two must agree, or the new field is a second "
        "quantity rather than the same one before suppression")


def test_both_status_cells_quote_the_floor_their_arithmetic_used():
    """The CA cell hardcoded "$500/$250-MFS" where the federal interpolated.

    An R&TC amendment to `CA_ES_DE_MINIMIS` would have moved every figure the
    section computes and left the sentence quoting the old floor — and the
    margin assertions elsewhere would all have stayed green, because none of
    them reads the quoted number.
    """
    csh = P.compute_ca_safe_harbor(1000.0, None, None, 20000.0, "single",
                                   withholding=700.0, estimates_paid=0.0)
    assert (f"${P.CA_ES_DE_MINIMIS:,}/${P.CA_ES_DE_MINIMIS_MFS:,}-MFS"
            in P._ca_status(csh)), (
        "the CA status quotes a floor that is not the one "
        "`compute_ca_safe_harbor` applied")
    sh = P.compute_safe_harbor(5000.0, None, None, "single",
                               withholding=4001.0, estimates_paid=0.0)
    assert f"${P.DE_MINIMIS_BALANCE:,} de minimis" in P._federal_status(sh)
    # /tax-project keeps the PROJECTION framing: here the tax IS annualized,
    # which is the half /tax-withholding's `tax_is_actual=True` overrides.
    assert "more tax than projected" in P._federal_status(sh)
    assert "more CA tax than projected" in P._ca_status(csh)


def test_the_federal_projection_value_checks_its_filing_status_too():
    """The harbor's check sits in a branch a first-year workspace never enters.

    `compute_safe_harbor` validates inside its `else:` — the prior-year prong —
    so a workspace with no `archive/{year-1}/` passes `prior_tax=None`, takes
    the degraded path, and never reaches it. Everything the harbor is later
    handed has by then been computed from the same unchecked status:
    `_full_status_key` resolves anything non-canonical to "single" through a
    `.get(fs, "single")` default, single brackets always exist, so an MFJ filer
    spelled "Married Filing Jointly" is taxed on single brackets and the single
    standard deduction with no error anywhere. The guard has to sit where the
    key is USED, not only where the 110% factor is.
    """
    loose = {"filing_status": "Married Filing Jointly", "dependents": []}
    with pytest.raises(P.ProjectionError, match="filing_status"):
        P.project_federal_tax(_proj(), loose, _tax_law_2026())
    # The same profile through the whole report, which is the path a real
    # first-year run takes — and the one that reaches the state projection and
    # the harbor before the federal call would have raised.
    with pytest.raises(P.ProjectionError, match="filing_status"):
        _build_report({"pay_frequency": "biweekly", "ytd": {}}, loose,
                       _tax_law_2026(), None, 2026, datetime.date(2026, 6, 30))
    # What that spelling silently produced: single brackets, not MFJ ones.
    single = P.project_federal_tax(_proj(), {"filing_status": "SINGLE"},
                                   _tax_law_2026())
    mfj = P.project_federal_tax(_proj(), _profile_mfj(), _tax_law_2026())
    assert single.total_tax != mfj.total_tax, (
        "this test cannot show what the fallback cost if the two statuses "
        "compute the same tax on these inputs")


def test_a_closed_year_defaults_its_as_of_to_december_31():
    """Today's MM-DD is the right default for the current year and no other.

    Read inside a year that has already closed it is a date in that year's
    PAST, so the `as_of > today` guard both CLIs carry cannot fire — 2026-01-05
    is not in the future of 2025 — and the finished year is annualized from
    however many days had elapsed on the day of the run.
    """
    jan5 = datetime.date(2026, 1, 5)
    assert P.default_as_of(2026, jan5) == "01-05", (
        "the OPEN year still annualizes from today — this default is not "
        "supposed to freeze the current year at year-end")
    assert P.default_as_of(2025, jan5) == "12-31"
    # The arithmetic that makes it matter: 5/365 of a year against all of it.
    stale = P.elapsed_fraction(P.parse_as_of("01-05", 2025))
    fixed = P.elapsed_fraction(P.parse_as_of(P.default_as_of(2025, jan5), 2025))
    assert fixed == 1.0 and stale < 0.02, (
        f"a closed year must annualize by 1.0, not {stale} — the old default "
        f"multiplied a full year's withholding by {1 / stale:.0f}")


def test_resolve_as_of_preserves_a_valid_stored_date_on_a_closed_year():
    """The stored date is provenance for the YTD figures beside it.

    Reopening a June 30 snapshot after year-end does not turn its six months of
    actuals into full-year figures. An explicit override may replace the pair,
    but the resolver must never relabel stored figures on its own.
    """
    jan5 = datetime.date(2026, 1, 5)
    closed, open_year = 2025, 2026

    date, note = P.resolve_as_of("06-30", "03-01", closed, jan5)
    assert (date, note) == ("06-30", None), (
        "an explicit --as-of stopped outranking the stored stamp, so a "
        "deliberate mid-year read of a finished year is no longer possible")

    date, note = P.resolve_as_of(None, "06-30", closed, jan5)
    assert (date, note) == ("06-30", None), (
        "the stored date was replaced without replacing the YTD figures; "
        "that silently treats half-year actuals as full-year actuals")

    for stored in (None, "12-31"):
        date, note = P.resolve_as_of(None, stored, closed, jan5)
        assert (date, note) == ("12-31", None), (
            f"stored {stored!r} on a closed year gave {date!r} with "
            f"note={note!r} — nothing was overridden, so nothing should warn")

    date, note = P.resolve_as_of(None, "06-30", open_year, jan5)
    assert (date, note) == ("06-30", None), (
        "the OPEN year lost its stored stamp too — there it pairs with the "
        "YTD figures beside it, which is what storing it is for")
    date, note = P.resolve_as_of(None, None, open_year, jan5)
    assert (date, note) == ("01-05", None), (
        f"the open year no longer defaults to today ({date}), which would "
        f"freeze the current year at 12-31 the moment this override landed")

    for malformed in (False, 0, "", {}, []):
        date, note = P.resolve_as_of(None, malformed, closed, jan5)
        assert date == "12-31"
        assert note is not None and "Invalid stored as-of date omitted" in note


def test_resolve_as_of_rejects_every_present_invalid_value_in_an_open_year():
    today = datetime.date(2026, 6, 30)
    for malformed in (False, 0, "", {}, []):
        with pytest.raises(P.ProjectionError, match="as_of"):
            P.resolve_as_of(None, malformed, 2026, today)
    sensitive = "-".join(("123", "45", "6789"))
    with pytest.raises(P.ProjectionError) as exc:
        P.resolve_as_of(None, sensitive, 2026, today)
    assert sensitive not in str(exc.value)


def test_the_open_year_as_of_refusal_names_its_field_and_its_remedy():
    """Refusing is right; refusing anonymously is what reads as a tool bug.

    This is a deliberate break with workspaces written before it — the old
    `args.as_of or stored or today` chain skipped a present-but-falsy
    `as_of_date` in silence, which stamps today's date onto YTD figures
    entered as of some other one. A taxpayer meeting the refusal has to be
    able to act on it without reading the source.
    """
    today = datetime.date(2026, 6, 30)
    for placeholder in ("", 0, False, "6/30"):
        with pytest.raises(P.ProjectionError) as exc:
            P.resolve_as_of(None, placeholder, 2026, today)
        message = str(exc.value)
        assert "projection_inputs.json" in message and "as_of_date" in message
        assert "null" in message and "--as-of" in message, (
            "the refusal states no remedy, so the only way out of it is the "
            f"source: {message}")

    # The value is never echoed — the field can hold anything. Checked with a
    # distinctive marker rather than each placeholder's repr, since "0" occurs
    # in the year the message legitimately prints.
    marker = "-".join(("123", "45", "6789"))
    with pytest.raises(P.ProjectionError) as exc:
        P.resolve_as_of(None, marker, 2026, today)
    assert marker not in str(exc.value)

    # The template documents the same rule, so the break is announced where
    # the field is edited and not only where it fires.
    template = json.loads(
        (Path(__file__).resolve().parent.parent / "agents" / "templates" /
         "projection_inputs_template.json").read_text())
    comment = template["_comment_as_of_date"]
    assert "null" in comment and "REFUSED" in comment


def test_resolve_as_of_omits_malformed_stored_metadata_from_the_banner():
    """Invalid metadata needs a loud degrade, but never its raw contents."""
    sensitive = "-".join(("123", "45", "6789"))
    date, note = P.resolve_as_of(
        None, sensitive, 2025, datetime.date(2026, 1, 5))
    assert date == "12-31" and "Invalid stored as-of date omitted" in note
    assert sensitive not in note


def test_cli_preserves_a_stored_as_of_on_a_closed_year(tmp_path):
    """The CLI keeps the valid date attached to the stored YTD actuals."""
    shutil.copytree(FIXTURE_WS, tmp_path / "ws")
    ws = tmp_path / "ws"
    law = json.loads((ws / "tax_law_2026.json").read_text())
    law["tax_year"] = 2025
    (ws / "tax_law_2025.json").write_text(json.dumps(law))
    (ws / "tax_law_2026.json").unlink()
    inputs = json.loads((ws / "projection_inputs.json").read_text())
    assert inputs["as_of_date"] == "06-30", (
        "the fixture no longer carries a stored mid-year stamp, so this test "
        "passes without exercising the override it exists for")
    inputs["tax_year"] = 2025
    (ws / "projection_inputs.json").write_text(json.dumps(inputs))
    shutil.rmtree(ws / "archive")

    base = [sys.executable, "-m", "agents.lib.projection",
            "--inputs", str(ws / "projection_inputs.json"),
            "--workspace", str(ws), "--year", "2025", "--today", "2026-01-05"]
    repo_root = Path(__file__).resolve().parent.parent
    out = subprocess.run(base + ["--json"], capture_output=True, text=True,
                         cwd=str(repo_root))
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["as_of"] == "06-30"
    assert data["stale_as_of_note"] is None
    # The projection stays a June projection. Relabeling the same figures
    # 12-31 produced $2,140.00 by treating six months of actuals as the year.
    assert data["federal"]["total_tax"] == 14458.9, (
        f"federal tax is {data['federal']['total_tax']} — relabeling the "
        "stored figures as 12-31 gave $2,140.00")
    assert data["proj"]["paychecks_remaining"] == 0
    assert data["w4_penalty"]["paychecks_remaining"] == 0
    assert data["w4_penalty"]["per_paycheck"] == 0.0
    assert data["w4_zero"]["paychecks_remaining"] == 0
    assert data["w4_zero"]["per_paycheck"] == 0.0
    assert "No paychecks remain in TY2025" in data["w4_lever_note"]
    md = subprocess.run(base, capture_output=True, text=True, cwd=str(repo_root))
    assert "Stored as-of date overridden" not in md.stdout

    # The malformed-value branch must be safe in both output modes. A closed
    # year never passes the stored value through parse_as_of, so the renderer
    # itself has to omit it rather than trusting the later CLI error redactor.
    sensitive = "-".join(("123", "45", "6789"))
    inputs["as_of_date"] = sensitive
    (ws / "projection_inputs.json").write_text(json.dumps(inputs))
    for output_args in (["--json"], []):
        emitted = subprocess.run(
            base + output_args, capture_output=True, text=True,
            cwd=str(repo_root))
        assert emitted.returncode == 0, emitted.stderr
        assert sensitive not in emitted.stdout and sensitive not in emitted.stderr
        assert "Invalid stored as-of date omitted" in emitted.stdout


class _Harbor:
    """The four fields `_voucher_catchup_note` duck-types over."""

    def __init__(self, estimates_paid):
        self.required_annual = 20000.0
        self.withholding = 6000.0
        self.estimates_paid = estimates_paid
        self.de_minimis = False


def test_the_catchup_note_names_undated_estimates_only_where_there_are_some():
    """The catch-up note and the timing note are mutually exclusive.

    `_voucher_catchup_note` returns the catch-up text as soon as something is
    outstanding, so the `estimates_paid > 0` timing branch below it — the one
    carrying "no dates" and "no later payment undoes it" — is unreachable in
    exactly the case where a payment is already late. The catch-up note is
    therefore the ONLY note that reader sees, and it has to carry the warning
    itself.

    But only for a reader it is TRUE of. This gate fires on withholding alone
    — with no estimated payments `_something_outstanding` reduces to
    (requirement - withholding) x weight — so the clause announced "the
    estimates behind this schedule arrived as an undated TOTAL" to taxpayers
    who had made no estimated payment, and sent them to re-run a dated test
    with nothing to date. Both directions are asserted here, on both
    jurisdictions and through the real gate rather than against the
    constants: a clause spliced into the base text would satisfy a
    substring check on `_ES_VOUCHER_CATCHUP_NOTE` while still reaching the
    zero-estimates reader.
    """
    for catchup, step in ((P._FEDERAL_CATCHUP, "step 4"),
                          (P._CA_CATCHUP, "step 5")):
        as_of = datetime.date(2026, 7, 1)
        vouchers = P.compute_estimated_vouchers(14000.0, as_of)
        paid = P._voucher_catchup_note(
            catchup, vouchers, _Harbor(1000.0), as_of)
        none = P._voucher_catchup_note(
            catchup, vouchers, _Harbor(0.0), as_of)
        assert paid is not None and none is not None, (
            "the catch-up gate stopped firing on an outstanding cumulative "
            "balance — these two cases no longer test what they claim to")
        assert "undated TOTAL" in paid and "DATES" in paid, (
            f"a catch-up note reaching a taxpayer who HAS paid estimates does "
            f"not say they arrived undated, so they re-run the cumulative "
            f"test on the same figure and reach the same answer: {paid!r}")
        assert "undated TOTAL" not in none and "DATES" not in none, (
            f"a catch-up note tells a taxpayer with NO estimated payments "
            f"that the estimates behind the schedule were an undated total: "
            f"{none!r}")
        assert step in none, (
            f"the half of the note that is true either way lost its pointer "
            f"to the cumulative test along with the clause: {none!r}")
        assert none in paid, (
            "the two notes have diverged into separate texts — the gated "
            "clause is meant to APPEND to the one base note, so that a fix "
            "to the shared half cannot reach only one of the two readers")


@pytest.mark.parametrize("wh,est", [(5000, 1000.005), (5000.004, 1000.0),
                                    (5000.005, 1000.005), (0.005, 0.005)])
def test_both_harbors_reconcile_the_triple_they_print(wh, est):
    """Required - (withholding + estimates paid) must equal the shortfall.

    Both fields are printed side by side in the payload and in the markdown
    table, so a reader adds them. They used to be rounded INTO the dataclass
    while the arithmetic ran on the raw floats, so a fractional-cent estimate
    made the printed shortfall a cent away from the difference of the two
    figures printed above it. The CA path was given the round-once fix in an
    earlier round and the federal twin was not; both are pinned here so the
    next divergence is a red suite rather than a cent.

    `compute_estimated_vouchers` reconstructs `shortfall + estimates`, so this
    is also what keeps the voucher column reconciling.
    """
    for harbor in (
        P.compute_safe_harbor(projected_tax=20000, withholding=wh,
                              estimates_paid=est, prior_tax=None,
                              prior_agi=None, filing_status="Single"),
        P.compute_ca_safe_harbor(projected_tax=20000, withholding=wh,
                                 estimates_paid=est, prior_tax=None,
                                 prior_agi=None, current_agi=100000,
                                 filing_status="Single"),
    ):
        assert harbor.withholding == round(harbor.withholding, 2)
        assert harbor.estimates_paid == round(harbor.estimates_paid, 2)
        expected = round(max(0.0, harbor.required_annual
                             - (harbor.withholding + harbor.estimates_paid)), 2)
        assert harbor.shortfall == expected


def test_the_federal_voucher_column_sums_to_the_shortfall():
    """A rounded even share, reused for every voucher, need not add up.

    `round(shortfall / len(open_q), 2)` for all of them splits $100.00 across
    three open quarters as $33.33 x 3 = $99.99. The CA twin has always landed
    its remainder on the last weighted instalment so the column sums exactly,
    and both docstrings describe the two as one design taking one pre-derived
    shortfall. A taxpayer paying the printed federal column was a cent under
    a harbor that has no de minimis on the instalment itself.
    """
    for as_of in ("2026-01-02", "2026-05-01", "2026-07-01"):
        d = datetime.date.fromisoformat(as_of)
        for shortfall in (100.0, 1000.0, 3333.33, 0.07):
            for fn in (P.compute_estimated_vouchers, P.compute_ca_estimated_vouchers):
                total = round(sum(v.amount for v in fn(shortfall, d)), 2)
                assert total == shortfall, (
                    f"{fn.__name__} at {as_of} splits ${shortfall} into a "
                    f"column summing to ${total} — the difference is what the "
                    f"taxpayer is left short of the harbor")


def test_the_federal_harbor_value_checks_its_filing_status_like_the_ca_one():
    """"Married Filing Separately" is not "MFS", and only one side said so.

    The federal harbor took `(filing_status or "").upper() == "MFS"`, so a
    non-canonical spelling took the $150,000 window and the 100% factor
    instead of $75,000 and 110% — the required annual payment comes out 10%
    low, which is the direction that ends in the penalty. The CA twin raised
    on the same input.
    """
    with pytest.raises(P.ProjectionError, match="filing_status"):
        P.compute_safe_harbor(50000.0, 30000.0, 100000.0,
                              "Married Filing Separately",
                              withholding=0.0, estimates_paid=0.0)
    # The figures that spelling silently produced, and the ones it should:
    loose = P.compute_safe_harbor(50000.0, 30000.0, 100000.0, "single",
                                  withholding=0.0, estimates_paid=0.0)
    strict = P.compute_safe_harbor(50000.0, 30000.0, 100000.0, "MFS",
                                   withholding=0.0, estimates_paid=0.0)
    assert loose.prior_factor == 1.0 and strict.prior_factor == 1.1, (
        "the MFS prior-AGI threshold no longer separates these two, so the "
        "refusal above guards nothing")
    # Degraded mode never reaches the prong, so it must not start refusing a
    # profile it has always accepted.
    assert P.compute_safe_harbor(5000.0, None, None, None,
                                 withholding=0.0, estimates_paid=0.0).degraded


def test_the_harbor_refuses_a_filing_status_that_is_not_a_string_at_all():
    """The TYPE half of the same parse boundary, which was missing.

    `(filing_status or "").strip()` reached `.strip()` on any truthy
    non-string a malformed profile or result.json carried and raised
    AttributeError — which neither CLI catches, both catching
    ProjectionError/ValueError, so a data problem surfaced as a traceback
    rather than the refusal the value check exists to give.
    `withholding.build_report` reads the status straight off result.json with
    no check of its own, which is the path that reaches it unexamined.
    """
    for bad in (7, True, [], {}, 0, 0.0):
        with pytest.raises(P.ProjectionError, match="filing_status"):
            P._canonical_status(bad, "unit test")
        with pytest.raises(P.ProjectionError, match="filing_status"):
            P.compute_safe_harbor(50000.0, 30000.0, 100000.0, bad,
                                  withholding=0.0, estimates_paid=0.0)
        with pytest.raises(P.ProjectionError, match="filing_status"):
            P.compute_ca_safe_harbor(9000.0, 8000.0, 100000.0, 120000.0, bad,
                                     withholding=0.0, estimates_paid=0.0)
    # ABSENT is still refused by the VALUE check, with the message that names
    # the canonical set — the type check must not swallow that case.
    for absent in (None, "", "   "):
        with pytest.raises(P.ProjectionError, match="Single/MFJ/MFS"):
            P._canonical_status(absent, "unit test")


def test_the_ca_unnetted_flag_stays_live_while_federal_is_completed_input():
    """CA still discloses its raw prior intake; federal no longer has one.

    Under R&TC §19136.3 a prior-year tax exists and the prior prong is
    unavailable: `degraded` is False and `prior_based` is None. Federally the
    two agree today, and would stop agreeing the moment any carve-out of that
    shape lands (§6654(i) farmers and fishermen is the one the skill already
    names), leaving the note claiming a prior-year prong the report never used.
    """
    high = P.compute_ca_safe_harbor(20000.0, 15000.0, 120000.0, 2_000_000.0,
                                    "MFJ", withholding=0.0, estimates_paid=0.0)
    assert high.high_income_90_only and not high.degraded, (
        "the fixture no longer exercises the §19136.3 branch")
    assert high.prior_based is None and high.prior_tax_unnetted is False, (
        "the CA note claims a prior-year prong on a run that could not use "
        "one — which is what `not degraded` would have said")
    # The federal side carries no such field at all — see the CA field's own
    # comment. It was always False and read by nothing, so one name meant a
    # live flag here and a constant there; a payload consumer handling both
    # read that constant as "the federal prior base IS netted".
    for sh in (P.compute_safe_harbor(30000.0, 18400.0, 162000.0, "single",
                                     withholding=0.0, estimates_paid=0.0),
               P.compute_safe_harbor(30000.0, None, None, "single",
                                     withholding=0.0, estimates_paid=0.0)):
        assert not hasattr(sh, "prior_tax_unnetted"), (
            "SafeHarbor grew a federal `prior_tax_unnetted` back — the "
            "federal prong only ever accepts a completed Form 2210 line-8 "
            "composition, so the question it answers cannot arise")


def test_the_federal_catchup_note_is_silent_while_every_deadline_is_still_open():
    """Before Apr 15 the even FEDERAL split is what each installment requires.

    Form 2210 asks 25% of the required annual payment cumulatively by each
    date and credits withholding a quarter at a time, so with all four
    deadlines open the even quarter lands exactly. The note's claim — the
    earliest needs more than its printed share — is false there, and "pay
    catch-up amounts NOW" is then a payment directive with nothing behind
    it. AGENTS.md: OVER-stating what the taxpayer must pay is the
    hard-error direction.

    This holds because the two sides of the federal test use the SAME
    fraction. California's do not, which is the test below.
    """
    md = _render_at(datetime.date(2026, 3, 2))
    assert "### 1040-ES voucher schedule" in md, "the schedule stopped rendering"
    assert P._ES_VOUCHER_CATCHUP_NOTE not in md, (
        "the catch-up note renders in March, when no deadline has passed and "
        "the even split matches the statutory 25% exactly")


def test_the_ca_catchup_note_is_silent_in_march_too_because_both_sides_weight():
    """California is not short at April 15 either, and the weights are why.

    This assertion has been wrong in BOTH directions across two rounds. What
    FTB 5805 actually does, and the $5,000/$4,189 case a wrong reading of it
    called $452.75 short where $243.30 is owed, are on
    `projection._ca_instalment_fraction` — one statement, where the weights
    are applied. Over-stating what the taxpayer must pay is the hard error;
    that it arrived through a banner rather than a total does not soften it.
    """
    md = _render_at(datetime.date(2026, 3, 2))
    assert "### CA 540-ES voucher schedule" in md or "540-ES" in md, (
        "the CA schedule stopped rendering, so this pins nothing")
    assert P._CA_VOUCHER_CATCHUP_NOTE not in md, (
        "the CA catch-up note renders in March, when no deadline has passed "
        "and the weighted voucher is exactly what its instalment requires — "
        "FTB 5805 Worksheet II weights the withholding on line 2 the same "
        "30/40/0/30 it weights the requirement on line 1")
    vouchers = P.compute_ca_estimated_vouchers(5000.0 - 4189.0,
                                               datetime.date(2026, 3, 2))
    april = next(v for v in vouchers if v.quarter == 1)
    assert april.amount == round((5000.0 - 4189.0) * 0.30, 2), (
        f"April prints ${april.amount:,.2f} where 30% of the ${5000.0 - 4189.0:,.2f} "
        f"the withholding leaves is what that instalment requires")


def test_the_catchup_note_is_silent_when_the_harbor_is_already_covered():
    """An all-zero schedule cannot under-fund anything.

    `compute_estimated_vouchers` returns $0 vouchers once withholding alone
    meets the required annual payment. Telling that taxpayer to pay catch-up
    amounts NOW asks for money that avoids no penalty.
    """
    md = _render_at(datetime.date(2026, 7, 2), withholding=30000,
                    state_withholding=20000)
    # Prefix, not the whole cell: this fixture over-withholds so far that both
    # de-minimis floors also apply, and each renders its statute into the
    # status. "covered" is the part this test is about — the schedule is all
    # zeros and the note has nothing to be true of, whether the harbor was met
    # by paying it or excused by §6654(e)(1).
    assert "| Status | covered" in md, (
        "the fixture no longer covers the harbor, so it cannot pin the "
        "all-zero schedule case")
    assert P._ES_VOUCHER_CATCHUP_NOTE not in md, (
        "the catch-up note renders against an all-zero voucher schedule")
    assert P._CA_VOUCHER_CATCHUP_NOTE not in md, (
        "the CA catch-up note renders against an all-zero voucher schedule")


def test_the_ca_catchup_note_still_fires_where_a_deadline_has_closed_underfunded():
    """The case the CA note exists for, kept alive against the suppressions.

    The federal side is pinned by the 1040-ES test above.
    """
    md = _render_at(datetime.date(2026, 7, 2))
    assert P._CA_VOUCHER_CATCHUP_NOTE in md, (
        "the July CA schedule reallocates the whole shortfall onto January "
        "and the note is gone — the gating suppressed the case it was for")


def _harbor(required_annual, withholding, estimates_paid, de_minimis=False):
    """The four fields `_voucher_catchup_note` reads off a safe harbor.

    A stub rather than a real `compute_safe_harbor` call: these cases pin the
    gate against hand-chosen cumulative figures, and reaching them through the
    harbor would mean solving for a projected tax that produces them.
    """
    return types.SimpleNamespace(required_annual=required_annual,
                                 withholding=withholding,
                                 estimates_paid=estimates_paid,
                                 de_minimis=de_minimis)


def test_the_catchup_note_is_silent_where_payments_already_meet_the_requirement():
    """A closed deadline is not the same thing as an underpayment.

    `compute_estimated_vouchers` splits whatever is left of the ANNUAL
    requirement across the deadlines still open, so a taxpayer who has
    already paid enough to satisfy the cumulative test still gets non-zero
    vouchers for the rest of the year — the remaining requirement, not a
    shortfall. Gating the note on "a deadline closed and something is still
    to pay" therefore told them an installment was accruing and to pay the
    next voucher NOW, when nothing was overdue at all.

    $20,000 required, no withholding, $15,000 of estimates already made, as
    of July 1: September 15 needs 75% x $20,000 = $15,000 and $15,000 is in.
    """
    vouchers = P.compute_estimated_vouchers(20000.0 - 15000.0,
                                            datetime.date(2026, 7, 1))
    assert [v.amount for v in vouchers] == [2500.0, 2500.0], (
        "the fixture no longer produces the two non-zero vouchers this case "
        "is about")
    note = P._voucher_catchup_note(
        P._FEDERAL_CATCHUP, vouchers,
        _harbor(20000.0, 0.0, 15000.0), datetime.date(2026, 7, 1))
    assert note != P._ES_VOUCHER_CATCHUP_NOTE, (
        "the catch-up note fires where the cumulative requirement at the "
        "earliest open deadline is already met — it directs an immediate "
        "payment against nothing owed")
    assert note == P._ES_VOUCHER_TIMING_SCHEDULED, (
        "the $15,000 arrived on a date this engine never saw, so what "
        "replaces the catch-up demand is the pointer to the dated test — not "
        "silence, which is the under-stating half of the same simplification")
    assert note != P._ES_VOUCHER_TIMING_SETTLED, (
        "$5,000 of the requirement is still unpaid and printed as two "
        "vouchers right below this note, which opens by saying nothing is "
        "outstanding against the ANNUAL requirement — the settled lede "
        "belongs to an all-zero schedule and nowhere else")


def test_the_catchup_note_fires_on_an_instalment_that_is_already_overdue():
    """One open deadline left does not mean nothing is late.

    After September 15 the CA schedule has a single instalment still open and
    it carries the WHOLE annual requirement, so the printed amount equals what
    the cumulative test asks by January 15 and the open-side comparison passes
    exactly. Meanwhile 70% of that requirement (R&TC §19136.1's 30% + 40%)
    fell due on June 15 and has been accruing penalty ever since. Testing only
    the earliest still-open instalment therefore suppressed the note precisely
    where the amount overdue was largest — the UNDER-stating direction
    AGENTS.md makes a hard error, arriving as silence.

    $20,000 required, nothing withheld, nothing paid, as of October 1: the
    schedule prints $20,000 at January 15 and $14,000 has been overdue since
    June. The federal schedule hides 75% at the same date the same way.
    """
    as_of = datetime.date(2026, 10, 1)
    for label, build, catchup, overdue in (
            ("CA", P.compute_ca_estimated_vouchers, P._CA_CATCHUP, 14000.0),
            ("federal", P.compute_estimated_vouchers, P._FEDERAL_CATCHUP, 15000.0),
    ):
        vouchers = build(20000.0, as_of)
        assert [v.amount for v in vouchers] == [20000.0], (
            f"the {label} fixture no longer puts the whole requirement on the "
            f"one remaining instalment, which is the case this pins")
        assert 20000.0 * catchup.instalment_fraction(vouchers[0].quarter - 1) == overdue, (
            f"the {label} cumulative requirement at the last closed deadline "
            f"moved; the amount this test calls overdue is stale")
        assert P._voucher_catchup_note(
            catchup, vouchers, _harbor(20000.0, 0.0, 0.0), as_of), (
            f"the {label} catch-up note is silent in October with ${overdue:,.0f} "
            f"overdue since an earlier deadline. The printed voucher matches "
            f"the January requirement, so only the LAST PASSED instalment can "
            f"see it")


def test_an_overdue_instalment_since_paid_does_not_reopen_the_note():
    """The test asks what is outstanding NOW, not what was ever late.

    Money already in cures an earlier instalment going forward: the penalty on
    a June underpayment covered by September is fixed and unavoidable, so
    directing a catch-up payment for it asks for money that avoids nothing —
    the OVER-stating direction. Same October date and requirement as the test
    above, with $20,000 of estimates paid.
    """
    as_of = datetime.date(2026, 10, 1)
    vouchers = P.compute_ca_estimated_vouchers(20000.0 - 20000.0, as_of)
    assert [v.amount for v in vouchers] == [0.0], (
        "the fixture no longer produces the all-zero schedule this pins")
    for label, schedule, paid, expected in (
            ("all-zero", vouchers, 20000.0, P._CA_VOUCHER_TIMING_SETTLED),
            ("partly paid",
             P.compute_ca_estimated_vouchers(20000.0 - 14000.0, as_of),
             14000.0, P._CA_VOUCHER_TIMING_SCHEDULED)):
        note = P._voucher_catchup_note(
            P._CA_CATCHUP, schedule, _harbor(20000.0, 0.0, paid), as_of)
        assert note != P._CA_VOUCHER_CATCHUP_NOTE, (
            f"the {label} schedule fires the catch-up note with the "
            f"requirement at the last closed deadline already met — $6,000 "
            f"may remain, but it is not due until January 15 and the printed "
            f"voucher says so")
        assert note == expected, (
            f"the {label} schedule goes silent, or claims the wrong thing "
            f"about the annual requirement. The estimates came in undated, so "
            f"whether June's 70% was met ON June 15 is exactly what this "
            f"report cannot see and has to say — and only the all-zero "
            f"schedule may say the year's requirement is settled")


def test_the_voucher_advice_leads_with_the_retroactive_cure():
    """§6654(g)(1) is the remedy the catch-up note steers away from.

    An estimated payment credits on the day it is made and cannot cure April
    or June; withholding is deemed paid in equal parts on every due date, so a
    W-4 4(c) bump cures an already-missed instalment where a voucher only
    stops further accrual. `/tax-withholding` has led its recommendations with
    that sentence since it shipped. Here the catch-up note said "pay catch-up
    amounts NOW rather than at the printed dates" immediately ABOVE the 4(c)
    figure, pointing a taxpayer with paychecks left at the weaker of the two
    remedies at the one moment both are on screen.
    """
    md = _render_at(datetime.date(2026, 7, 2))
    actions = md.split("## Recommended actions", 1)
    assert len(actions) == 2, "the recommended-actions section stopped rendering"
    preface = actions[1].split("### 1040-ES", 1)[0]
    assert "retroactively" in preface, (
        "the voucher advice no longer names the retroactive cure, so the "
        "catch-up note below it is the only remedy the reader is offered")
    assert "4(c)" in preface, (
        "the preface does not name the lever that does the curing")
    assert P._ES_VOUCHER_CATCHUP_NOTE in actions[1], (
        "the fixture no longer fires the catch-up note, so this does not pin "
        "the two appearing together")


def test_the_federal_de_minimis_excuses_the_penalty_target_entirely():
    """§6654(e)(1): under $1,000 of balance after WITHHOLDING, no penalty.

    `compute_form_2210` has applied this floor since it shipped and returns
    `no_penalty` with the reason. `compute_safe_harbor` modeled no floor at
    all, so on the SAME figures /tax-project reported a shortfall, printed
    vouchers for it and (once the catch-up note landed) told the taxpayer to
    pay them NOW — a payment directive against a penalty the engine's own
    Form 2210 module reports as impossible. Two modules, one statute,
    opposite answers; AGENTS.md makes the over-stating one the hard error.

    $5,000 of tax against $4,001 withheld leaves $999, a dollar under the
    floor. The required annual payment is unchanged — it is what the harbor
    IS — and only the shortfall driving the payment advice is excused.
    """
    sh = P.compute_safe_harbor(5000.0, None, None, "SINGLE",
                               withholding=4001.0, estimates_paid=0.0)
    assert sh.de_minimis, (
        "the $999 balance after withholding is under §6654(e)(1)'s $1,000 "
        "floor and the harbor does not say so")
    assert sh.required_annual == 4500.0, (
        "the de minimis moved the required annual payment — it excuses the "
        "penalty, it does not change what the harbor requires")
    assert sh.shortfall == 0.0 and sh.covered, (
        "the harbor still reports a shortfall under the de minimis, which is "
        "what drives the W-4 figure and the voucher schedule")
    f2210 = compute_form_2210(5000.0, 4001.0, "SINGLE", 2026)
    assert f2210.get("no_penalty"), (
        "the two engine modules disagree about §6654(e)(1) on one set of "
        "figures — this test exists because they did")


def test_the_federal_de_minimis_zeroes_the_penalty_vouchers_and_the_note():
    """The floor has to reach the schedule, not just the harbor's field.

    `compute_ca_estimated_vouchers` took `required_annual` and recomputed a
    shortfall of its own, which is how the CA schedule kept printing an
    instalment inside a section whose status line said no penalty applies.
    The federal schedule was built the same way.

    Both now take the harbor's `shortfall` — the suppression is applied ONCE,
    where the statute is read, so what this pins is the half that can still
    break: that `compute_safe_harbor` reports zero under the floor and that a
    zero shortfall produces a zero schedule. That the report hands the harbor's
    own figure over is `test_the_report_hands_the_voucher_schedule_the_harbors_
    own_shortfall`, which is where a caller reverting to a second derivation
    would show up.
    """
    sh = P.compute_safe_harbor(5000.0, None, None, "SINGLE",
                               withholding=4001.0, estimates_paid=0.0)
    as_of = datetime.date(2026, 7, 1)
    vouchers = P.compute_estimated_vouchers(sh.shortfall, as_of)
    assert all(v.amount == 0 for v in vouchers), (
        "the penalty voucher schedule still demands payments under the "
        "§6654(e)(1) floor — money that avoids nothing")
    assert P._voucher_catchup_note(
        P._FEDERAL_CATCHUP, vouchers, sh, as_of) is None, (
        "the catch-up note fires under the de minimis, telling a taxpayer "
        "who cannot be penalised to pay catch-up amounts NOW")
    # The counterweight: one dollar more of tax clears the floor and every
    # one of those turns back on, so this pins the floor and not the plumbing.
    over = P.compute_safe_harbor(5001.0, None, None, "SINGLE",
                                 withholding=4001.0, estimates_paid=0.0)
    assert not over.de_minimis and over.shortfall > 0, (
        "a $1,000 balance is NOT under a $1,000 floor — §6654(e)(1) reads "
        "'less than', and the boundary is where an off-by-one hides")
    assert any(v.amount > 0 for v in P.compute_estimated_vouchers(
        over.shortfall, as_of)), (
        "the schedule stayed silent a dollar above the floor, so the test "
        "above pins nothing")


def test_the_de_minimis_reads_withholding_only_not_estimates():
    """§6654(e)(1) reduces the tax by tax WITHHELD, not by estimates paid.

    The two are interchangeable for the harbor's shortfall and are not
    interchangeable here, which is the kind of implicit-universal assumption
    this codebase keeps paying for. A taxpayer who covers the same $999 with
    an estimated payment instead of withholding is NOT excused.
    """
    by_estimates = P.compute_safe_harbor(5000.0, None, None, "SINGLE",
                                         withholding=0.0, estimates_paid=4001.0)
    assert not by_estimates.de_minimis, (
        "estimates were counted toward the §6654(e)(1) floor; the statute "
        "reduces the tax shown by tax withheld only")


def test_the_ca_de_minimis_reaches_its_voucher_schedule_and_note():
    """The section that said "no CA underpayment penalty applies" and "pay now".

    R&TC §19136 excuses the penalty entirely under $500 ($250 MFS) of CA tax
    minus withholding, and `compute_ca_safe_harbor` zeroed its shortfall for
    exactly that reason — so the DE 4 figure would not demand payments that
    avoid nothing. The voucher schedule recomputed its own shortfall from
    `required_annual` and never saw the flag, so one CA block printed the
    de-minimis status line, a non-zero instalment and a note directing an
    immediate payment, all at once. The schedule now takes the harbor's own
    `shortfall`, so there is one derivation and nothing left to keep in step.
    """
    csh = P.compute_ca_safe_harbor(1000.0, None, None, 20000.0, "SINGLE",
                                   withholding=700.0, estimates_paid=0.0)
    assert csh.de_minimis and csh.shortfall == 0.0, (
        "the fixture no longer lands under the CA de minimis")
    as_of = datetime.date(2026, 7, 1)
    vouchers = P.compute_ca_estimated_vouchers(csh.shortfall, as_of)
    assert all(v.amount == 0 for v in vouchers), (
        "the CA penalty schedule still prints an instalment under §19136")
    assert P._voucher_catchup_note(
        P._CA_CATCHUP, vouchers, csh, as_of) is None, (
        "the CA catch-up note fires under the de minimis, contradicting the "
        "status line printed a few rows above it")


def test_the_de_minimis_says_why_it_reads_covered():
    """A bare "covered" against a visible balance owed reads as a bug.

    The status cell is the only place the report can explain that the harbor
    was EXCUSED rather than met, and the zero-balance figures a few rows away
    still show tax due. `_ca_status` has carried its reason since it shipped.
    """
    sh = P.compute_safe_harbor(5000.0, None, None, "SINGLE",
                               withholding=4001.0, estimates_paid=0.0)
    status = P._federal_status(sh)
    assert status.startswith("covered") and "6654(e)(1)" in status, (
        "the federal status cell reports the de minimis as a bare 'covered', "
        "with nothing saying a balance is still owed at filing")
    assert f"${DE_MINIMIS_BALANCE:,}" in status, (
        "the status quotes a floor that is not the engine's constant")


def test_the_catchup_notes_reach_the_json_payload_too():
    """Both consuming skills read `--json` BEFORE they render markdown.

    /tax-project step 5 takes the JSON and step 6 re-runs for the report, and
    /tax-withholding has the same shape — so a warning that exists only in the
    renderer reaches the agent second, if at all. Every other renderer-only
    warning on this report (`nonresident_income_warning`,
    `state_unsupported_note`) is a field for that reason.
    """
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 2000,
                        "state_withholding": 700},
                "forecast_overrides": {"capital_gains": 0},
                "recurring_from_prior": {"taxable_interest": 0},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile=_profile_mfj(), tax_law=_tax_law_2026(),
        prior=_Prior({"california_540": {"ca_total_tax": 5000},
                      "form_1040": {"line_24_total_tax": 15000,
                                    "line_11_agi": 120000}}),
        year=2026, as_of=datetime.date(2026, 7, 2))
    payload = P._report_to_dict(r)
    assert payload["voucher_catchup_note"] == r.voucher_catchup_note
    assert payload["ca_voucher_catchup_note"] == r.ca_voucher_catchup_note
    assert r.ca_voucher_catchup_note, (
        "the fixture no longer fires the CA note, so this cannot show the "
        "payload carries it")
    # The markdown is the same string, not a second copy of the sentence.
    assert r.ca_voucher_catchup_note in P.render_markdown(r), (
        "the renderer prints a note the payload does not carry — the drift "
        "this field exists to prevent")


def test_render_markdown_degraded_banner_and_state_skip_note():
    r = _build_sample_report(degraded=True, with_state=False)
    md = P.render_markdown(r)
    assert "Mid-Year Tax Projection" in md
    assert "degraded" in md.lower()
    assert "skipped" in md.lower()


def test_render_markdown_no_income_tax_state_note():
    # A type="none" state (FL) must render a "no income tax" note, not the
    # generic "no baseline / no brackets" skip note — the two mean very
    # different things to the taxpayer (there's nothing to fetch for FL).
    law = dict(_tax_law_2026())
    law["state"] = {"type": "none", "code": "FL"}
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile={"filing_status": "MFJ", "dependents": [],
                 "address": {"state": "FL"}},
        tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.state is None
    assert r.state_no_income_tax is True
    assert r.no_income_tax_state_code == "FL"
    assert r.state_cache_mismatch is None
    md = P.render_markdown(r)
    assert ('State projection: none — FL levies no individual income tax '
            '(state.type = "none").') in md
    assert "no prior-year state baseline" not in md


def test_build_report_state_cache_mismatch_blocks_no_income_tax_claim():
    # Codex round-5 P1: a stale FL type="none" cache left beside a CA
    # taxpayer must NOT claim "no state tax required" — build_report must
    # compare tax_law.state.code against the resident state (both known,
    # both known-different) and keep the flag False + surface a loud
    # mismatch note instead.
    law = dict(_tax_law_2026())
    law["state"] = {"type": "none", "code": "FL"}
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile={"filing_status": "MFJ", "dependents": [],
                 "address": {"state": "CA"}},
        tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.state is None
    assert r.state_no_income_tax is False
    assert r.no_income_tax_state_code is None
    assert r.state_cache_mismatch is not None
    assert "FL" in r.state_cache_mismatch and "CA" in r.state_cache_mismatch
    md = P.render_markdown(r)
    assert "State cache mismatch" in md
    assert 'levies no individual income tax' not in md
    d = P._report_to_dict(r)
    assert d["state_no_income_tax"] is False
    assert d["state_cache_mismatch"] == r.state_cache_mismatch


def test_build_report_state_year_mismatch_hard_errors():
    # Codex round-9 P2 introduced this check inside _resolve_state_no_income
    # _tax; Codex round-11 P1 found that project_federal_tax/project_state_
    # tax already consume the (possibly wrong-year) cache's brackets BEFORE
    # that check ever ran, silently publishing a wrong federal number with
    # only a STATE-labeled warning. build_report now hard-errors on a
    # tax_year mismatch BEFORE any computation runs — e.g. a tax_law_2020
    # .json body that actually says "tax_year": 2026 must not silently
    # price a TY2020 projection off 2026 rates.
    law = dict(_tax_law_2026())
    law["state"] = {"type": "none", "code": "FL"}
    with pytest.raises(P.ProjectionError, match="tax_year"):
        _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile={"filing_status": "MFJ", "dependents": [],
                     "address": {"state": "FL"}},
            tax_law=law, prior=None, year=2020, as_of=datetime.date(2020, 7, 2))


def test_build_report_missing_tax_year_hard_errors():
    # A cache with no top-level tax_year key at all (missing, not just
    # mismatched) must be rejected the same way — "no year info" is not
    # evidence the year is right.
    law = dict(_tax_law_2026())
    del law["tax_year"]
    law["state"] = {"type": "none", "code": "FL"}
    with pytest.raises(P.ProjectionError, match="tax_year"):
        _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile={"filing_status": "MFJ", "dependents": [],
                     "address": {"state": "FL"}},
            tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))


def test_build_report_bool_tax_year_hard_errors():
    # json.loads never produces a bool for a numeric-looking field on its
    # own, but a hand-edited/corrupted cache could — isinstance(x, int)
    # is True for bool in Python, so this must be checked explicitly.
    law = dict(_tax_law_2026())
    law["tax_year"] = True
    law["state"] = {"type": "none", "code": "FL"}
    with pytest.raises(P.ProjectionError, match="tax_year"):
        _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile={"filing_status": "MFJ", "dependents": [],
                     "address": {"state": "FL"}},
            tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))


def test_build_report_corrupted_ca_none_type_blocks_no_income_tax_claim():
    # Codex round-10 P1: a same-year, code-matching CA cache with a
    # corrupted "type": "none" (hand-edited or a fetch bug) must NOT pass
    # the code/year cross-checks and claim California has no individual
    # income tax — CA is graduated_income, never "none", for any year.
    law = dict(_tax_law_2026())
    law["state"] = {"type": "none", "code": "CA"}
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile={"filing_status": "MFJ", "dependents": [],
                 "address": {"state": "CA"}},
        tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.state_no_income_tax is False
    assert r.no_income_tax_state_code is None
    assert r.state_cache_mismatch is not None
    assert "CA" in r.state_cache_mismatch and "none" in r.state_cache_mismatch
    md = P.render_markdown(r)
    assert "State cache mismatch" in md
    assert 'levies no individual income tax' not in md


def test_build_report_pre_repeal_tn_none_type_blocks_no_income_tax_claim():
    # TN is only a legitimate "none" state for TY2021+ (Hall tax repeal) —
    # a TY2020 cache claiming TN "none" is exactly as corrupted as CA
    # "none" and must be rejected the same way.
    law = dict(_tax_law_2026())
    law["tax_year"] = 2020
    law["state"] = {"type": "none", "code": "TN"}
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile={"filing_status": "MFJ", "dependents": [],
                 "address": {"state": "TN"}},
        tax_law=law, prior=None, year=2020, as_of=datetime.date(2020, 7, 2))
    assert r.state_no_income_tax is False
    assert r.state_cache_mismatch is not None


def test_build_report_unknown_resident_state_no_false_no_tax_claim():
    # Codex round-5 P1 (safe-direction branch): when the resident state
    # can't be determined at all, the flag must stay False (can't verify
    # the cache against anything) AND no mismatch warning should fire —
    # this is legacy/degraded behavior, not a new false-positive.
    law = dict(_tax_law_2026())
    law["state"] = {"type": "none", "code": "FL"}
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile={"filing_status": "MFJ", "dependents": []},  # no address
        tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.state is None
    assert r.state_no_income_tax is False
    assert r.no_income_tax_state_code is None
    assert r.state_cache_mismatch is None
    md = P.render_markdown(r)
    assert "State cache mismatch" not in md
    assert 'levies no individual income tax' not in md
    assert "Skipped — no prior-year state baseline" in md


def test_project_state_tax_blocks_known_mismatch_despite_prior_state_section():
    # Codex round-6 P1: FL resident + FL prior-year state section (so
    # has_prior_state is True) + a stale CA cache with real graduated
    # brackets must NOT project CA tax onto FL income — has_prior_state is
    # only a "files a state return" proxy, not proof the cache matches the
    # taxpayer's actual resident state. Unlike the round-5 fixtures (a
    # type="none" cache with no brackets, which already returns None via
    # the `if not brackets` guard), this cache has real CA brackets, so the
    # bracket-projection path itself must reject the mismatch.
    prior = _Prior({"florida_state": {"state_return_required": False}})
    law = _tax_law_2026()  # state.code == "CA", graduated brackets present
    out = P.project_state_tax(_proj(), prior, law, "MFJ", resident_state="FL")
    assert out is None


def test_project_state_tax_unaffected_when_cache_matches_resident():
    # Regression: CA resident + matching CA cache + CA prior section still
    # projects normally — the new guard only fires on a known mismatch.
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    law = _tax_law_2026()
    out = P.project_state_tax(_proj(), prior, law, "MFJ", resident_state="CA")
    assert out is not None
    assert out.code == "CA"


def test_project_state_tax_legacy_no_code_cache_with_prior_section_unchanged():
    # A legacy cache with no `state.code` field can't be proven to mismatch
    # anything — the pre-existing resident-state fallback still applies,
    # since the new guard only fires when BOTH the cache code and the
    # resident state are known.
    prior = _Prior({"florida_state": {"state_return_required": False}})
    law = _tax_law_2026()
    del law["state"]["code"]
    out = P.project_state_tax(_proj(), prior, law, "MFJ", resident_state="FL")
    assert out is not None
    assert out.code == "FL"


def test_detect_prior_state_code_maps_section_to_code():
    from agents.lib.projection import _detect_prior_state_code
    assert _detect_prior_state_code({"florida_state": {}}) == "FL"
    assert _detect_prior_state_code({"california_540": {}}) == "CA"
    assert _detect_prior_state_code({}) is None
    assert _detect_prior_state_code(None) is None


def test_prior_florida_section_does_not_authorize_unrelated_ca_cache():
    # Codex round-15 P2: adding florida_state to STATE_SECTION_KEYS made
    # "has_prior_state" treat ANY known section as interchangeable proof
    # of residency. A prior FL section is evidence for FL, not for a
    # completely unrelated current-year CA cache — with no usable
    # resident_state, this must NOT bypass the residency check and publish
    # CA's liability.
    prior = _Prior({"florida_state": {"state_return_required": False}})
    law = _tax_law_2026()  # state.code == "CA", real graduated brackets
    out = P.project_state_tax(_proj(), prior, law, "MFJ", resident_state=None)
    assert out is None


def test_prior_california_section_still_authorizes_matching_ca_cache():
    # Regression: the ORIGINAL, still-valid case — a prior CA section
    # matching the current CA cache's code still projects, even with no
    # resident_state passed (the pre-FL, pre-round-15 behavior for CA).
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    law = _tax_law_2026()  # state.code == "CA"
    out = P.project_state_tax(_proj(), prior, law, "MFJ", resident_state=None)
    assert out is not None
    assert out.code == "CA"


def test_build_report_state_none_on_known_mismatch_despite_prior_state_section():
    # End-to-end companion to the unit test above: build_report must
    # surface the mismatch note (not a CA tax table) for the same
    # FL-resident/FL-prior-section/stale-CA-cache scenario, and the existing
    # state_cache_mismatch plumbing (round-5) must actually be visible now
    # that project_state_tax returns None instead of a CA StateProjection.
    law = _tax_law_2026()  # code "CA", graduated brackets
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile={"filing_status": "MFJ", "dependents": [],
                 "address": {"state": "FL"}},
        tax_law=law,
        prior=_Prior({"florida_state": {"state_return_required": False}}),
        year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.state is None
    assert r.ca_safe_harbor is None
    assert r.state_no_income_tax is False
    assert r.state_cache_mismatch is not None
    assert "CA" in r.state_cache_mismatch and "FL" in r.state_cache_mismatch
    md = P.render_markdown(r)
    assert "State cache mismatch" in md
    assert "| State (" not in md  # no CA tax table rendered
    d = P._report_to_dict(r)
    assert d["state"] is None
    assert d["state_cache_mismatch"] == r.state_cache_mismatch


def test_render_markdown_no_crash_on_zeros():
    r = _build_report(
        inputs={"pay_frequency": "monthly", "ytd": {"wages": 1},
                "estimated_payments_made": {}},
        profile=_profile_mfj(), tax_law=_tax_law_2026(), prior=None,
        year=2026, as_of=datetime.date(2026, 7, 2))
    md = P.render_markdown(r)
    assert isinstance(md, str) and len(md) > 0


def test_report_to_dict_is_json_serializable():
    r = _build_sample_report()
    json.dumps(P._report_to_dict(r))  # must not raise


def test_report_to_dict_includes_no_income_tax_fields():
    # False direction: a CA report (state configured, graduated brackets).
    d_ca = P._report_to_dict(_build_sample_report())
    assert "state_no_income_tax" in d_ca
    assert "no_income_tax_state_code" in d_ca
    assert "state_cache_mismatch" in d_ca
    assert d_ca["state_no_income_tax"] is False
    assert d_ca["no_income_tax_state_code"] is None
    assert d_ca["state_cache_mismatch"] is None

    # True direction: a type="none" (FL) report — --json output must not
    # stay ambiguous about why `state` is null (same defect class fixed in
    # loss_harvest.state_no_income_tax).
    law = dict(_tax_law_2026())
    law["state"] = {"type": "none", "code": "FL"}
    r_fl = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile={"filing_status": "MFJ", "dependents": [],
                 "address": {"state": "FL"}},
        tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
    d_fl = P._report_to_dict(r_fl)
    assert d_fl["state_no_income_tax"] is True
    assert d_fl["no_income_tax_state_code"] == "FL"


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


def test_cli_defaults_a_closed_year_to_december_31(tmp_path):
    """The same default as the withholding CLI, pinned on this one too.

    Both `_main`s built the fallback inline off `today.strftime("%m-%d")`, and
    the two are one bug: read for a year that has closed, that string is a date
    in that year's PAST, so the `as_of > today` guard beside it cannot fire and
    a finished year is annualized from a handful of days. Sharing the helper is
    what makes them one FIX; without a test on each, deleting the call from
    either leaves the other's test green.
    """
    shutil.copytree(FIXTURE_WS, tmp_path / "ws")
    ws = tmp_path / "ws"
    law = json.loads((ws / "tax_law_2026.json").read_text())
    law["tax_year"] = 2025
    (ws / "tax_law_2025.json").write_text(json.dumps(law))
    (ws / "tax_law_2026.json").unlink()
    inputs = json.loads((ws / "projection_inputs.json").read_text())
    # With `as_of_date` present the default chain never runs at all.
    inputs.pop("as_of_date")
    inputs["tax_year"] = 2025
    (ws / "projection_inputs.json").write_text(json.dumps(inputs))
    shutil.rmtree(ws / "archive")

    cmd = [sys.executable, "-m", "agents.lib.projection",
           "--inputs", str(ws / "projection_inputs.json"),
           "--workspace", str(ws), "--year", "2025",
           "--today", "2026-01-05", "--json"]
    repo_root = Path(__file__).resolve().parent.parent
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["as_of"] == "12-31"


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
    vs = P.compute_ca_estimated_vouchers(20000 - 8000 - 2000,
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
    vs = P.compute_ca_estimated_vouchers(10000, datetime.date(2026, 5, 1))
    assert [(v.quarter, v.amount) for v in vs] == [
        (2, 5714.29), (3, 0.0), (4, 4285.71)]
    assert round(sum(v.amount for v in vs), 2) == 10000.0


def test_ca_vouchers_after_jun_all_on_january():
    # After Jun 15 only the (zero-weight) Q3 and Q4 remain -> everything
    # lands on the January installment.
    vs = P.compute_ca_estimated_vouchers(10000, datetime.date(2026, 7, 2))
    assert [(v.quarter, v.amount) for v in vs] == [(3, 0.0), (4, 10000.0)]


def test_ca_vouchers_after_sep_only_q4():
    vs = P.compute_ca_estimated_vouchers(10000, datetime.date(2026, 10, 1))
    assert [(v.quarter, v.due_date, v.amount) for v in vs] == [
        (4, "2027-01-15", 10000.0)]


def test_ca_vouchers_covered_all_zero():
    vs = P.compute_ca_estimated_vouchers(5000 - 8000, datetime.date(2026, 3, 1))
    assert [v.amount for v in vs] == [0.0, 0.0, 0.0, 0.0]


def test_ca_vouchers_rounding_remainder_lands_on_last():
    # shortfall 100.01: Q1 30.0, Q2 40.0, Q3 0, Q4 = 100.01 - 70.00 = 30.01.
    vs = P.compute_ca_estimated_vouchers(100.01, datetime.date(2026, 3, 1))
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
    assert "CA 540-ES voucher schedule" in md
    # The statutory split belongs in the prose, NOT in the heading: the heading
    # cannot qualify itself, and the Share column below it does not always
    # print 30/40/0/30.
    assert "CA 540-ES voucher schedule (30%" not in md
    assert "the statutory weighting is 30% / 40% / 0% / 30%" in md
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
    # Codex round-15 P2: a resident-matching code is the legitimate trigger
    # here (not a leftover prior california_540 section, which — post-fix —
    # is evidence for CA, not NY, and would no longer bypass the residency
    # check on its own for an unrelated NY cache with no usable address).
    law = _tax_law_2026()
    law["state"]["code"] = "NY"
    prior = _Prior({"california_540": {"ca_total_tax": 5000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    profile = {"filing_status": "MFJ", "dependents": [],
               "address": {"state": "NY"}}
    r = _build_report(
        {"pay_frequency": "biweekly",
         "prior_year_form_2210_tax_base": 15000,
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        profile, law, prior, 2026, datetime.date(2026, 7, 2))
    assert r.state is not None
    assert r.ca_safe_harbor is None


def test_build_report_ca_high_income_note_rendered():
    # YTD wages 600000 over half a year -> projected CA AGI well over $1M.
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 900000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = _build_report(
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
    r = _build_report(
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
    r = _build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        _profile_mfj(), law, prior, 2026, datetime.date(2026, 7, 2))
    assert r.state.code == "STATE"
    assert r.ca_safe_harbor is None


def test_the_zero_liability_keys_the_engine_reads_are_ones_a_user_can_set():
    """A gate keyed on a name that exists nowhere else can never be cleared.

    The $0 gate shipped reading a key absent from the template and from both
    skills — so a taxpayer whose prior-year tax really was $0 had no route to
    say so, the harbor degraded to 90%-of-current for ever, and the rendered
    reason named a missing archive that was in fact present. Engine, template
    and both skills must name every gating key.

    BOTH federal keys are pinned, because they gate the same $0 base by
    independent statutory tests and the one that shipped alone
    (§6654(e)(2)'s) is not the one that covers the ordinary filer.
    """
    template = json.loads(
        (REPO / "agents" / "templates" /
         "projection_inputs_template.json").read_text())
    for key in ("prior_year_zero_liability_confirmed",
                "prior_year_return_filed_12_months_confirmed"):
        assert key in template, (
            f"the engine gates on `{key}` but nothing offers it to the user")
        assert template[key] is False, f"{key} must default to unconfirmed"
        assert f"_comment_{key}" in template, f"{key} ships without its rubric"
        for skill in ("tax-project", "tax-withholding"):
            assert key in skill_text(skill), (
                f"/{skill} never tells the user to set `{key}`")


def _engine_source(name: str) -> str:
    """Every line of one engine module, whether it is a file or a PACKAGE.

    W2 made `projection` a directory. A test reading `agents/lib/projection.py`
    raises FileNotFoundError, which is loud — but the near miss is the one that
    is not: reading only `projection/__init__.py` would keep passing while
    covering strictly less, and a forbidden string that moved into
    `projection/render.py` would come back absent. Concatenate the tree.
    """
    leaf = ENGINE_LIB / f"{name}.py"
    if leaf.is_file():
        return leaf.read_text()
    return "\n".join(p.read_text()
                      for p in engine_files(ENGINE_LIB / name))


def test_no_ca_confirmation_flag_survives_anywhere():
    """The CA $0 gate is gone, and must not come back half-wired.

    R&TC §19136(c)(2) is a California rule in its own right, not a conformity
    modification of §6654(e)(2): either year's tax less credits under
    $500/$250-MFS means no CA underpayment penalty, with no citizenship, no
    residency and no 12-month condition. FTB 5805's own no-penalty list reads
    it for "either" year and states none of them. Gating an archived $0 CA
    prior-year tax behind a federal-shaped confirmation billed 90% of the
    current year's CA tax to a Californian the state had already cleared.

    Pinned as an ABSENCE across engine, template and skills together — a key
    reintroduced in only one of the three is the failure the test above
    exists for, in reverse.
    """
    dead = "prior_year_ca_zero_liability_confirmed"
    template = json.loads(
        (REPO / "agents" / "templates" /
         "projection_inputs_template.json").read_text())
    assert not [k for k in template if dead in k], (
        f"`{dead}` is back in the planning template — nothing reads it")
    for name in ("projection", "withholding"):
        assert dead not in _engine_source(name), (
            f"`{dead}` is back in agents/lib/{name}")
    for skill in ("tax-project", "tax-withholding"):
        assert dead not in skill_text(skill), (
            f"/{skill} still tells the user to set `{dead}`, which nothing reads")

    # No flag, and the archived computed $0 is used: required annual payment
    # $0, and a note naming the rule rather than a bare "covered".
    prior = _Prior({"california_540": {"ca_standard_deduction": 5540,
                                       "ca_total_tax": 0, "ca_agi": 12000}})
    assert P._prior_ca_tax_agi(prior) == (0.0, 12000.0)
    csh = P.compute_ca_safe_harbor(20000.0, 0.0, 12000.0, 200000.0, "MFJ",
                                   withholding=0.0, estimates_paid=0.0)
    assert csh.required_annual == 0.0 and csh.covered and not csh.degraded
    note = "\n".join(P._ca_notes(csh))
    assert "19136(c)(2)" in note, (
        "a $0 required CA payment is printed with no rule beside it — it "
        "reads as a bug, and the reader cannot check it")
    # Prior CA AGI absent is the case the factor caveat fires on, and at a $0
    # prior tax that caveat is false: 110% of $0 and 100% of $0 are both $0.
    csh_no_agi = P.compute_ca_safe_harbor(20000.0, 0.0, None, 200000.0, "MFJ",
                                          withholding=0.0, estimates_paid=0.0)
    assert csh_no_agi.required_annual == 0.0
    assert not csh_no_agi.prior_agi_missing, (
        "the 100%/110% factor cannot change 0, so flagging a possible "
        "understatement there is a caveat that is false where it fires")


def test_the_ca_notes_hand_over_the_floors_prior_year_half():
    """R&TC §19136(c)(2) has TWO prongs; this engine can only test one.

    Verbatim from the section: no addition to tax where the tax "for the
    PRECEDING taxable year, minus the sum of any credits ... OR the tax
    computed ... upon the ESTIMATED INCOME FOR THE TAXABLE YEAR, minus the sum
    of any credits ..., is less than five hundred dollars". `compute_ca_safe_
    harbor` runs the second — CA tax less withholding — and cannot run the
    first, which needs the PRIOR year's own credits and the engine carries only
    its total tax.

    So a taxpayer the first prong clears outright was shown a CA required
    annual payment, a shortfall and a voucher schedule, with nothing on the
    page saying the rule existed. That is the over-stating direction, and the
    §6654 reader is the one most likely to miss it — the federal floor has no
    prior-year half at all, so the federal section beside it looks like
    confirmation.

    Handed over, never applied: whether "credits ... provided by Part 10 or
    this part" reaches the §19002 withholding credit decides how much of the
    prior year it clears, and deciding that wrong the other way CLEARS a
    penalty that attaches.
    """
    # Ordinary under-withheld CA year: the current-year prong does not clear it,
    # so the prior-year prong is the reader's remaining question.
    csh = P.compute_ca_safe_harbor(20000.0, 18000.0, 90000.0, 200000.0, "MFJ",
                                   withholding=2000.0, estimates_paid=0.0)
    assert not csh.de_minimis and csh.shortfall > 0
    note = "\n".join(P._ca_notes(csh))
    assert "EITHER year" in note and "19136(c)(2)" in note, (
        "the CA section reports a shortfall without naming the prior-year half "
        "of the $500 floor — the rule that may remove it entirely")
    assert "6654(e)(1)" in note, (
        "a reader who knows the federal floor reads the federal section as "
        "evidence about this one; it has no prior-year half and must say so")

    # NOT under the de minimis, where the current-year prong already answered
    # and a second floor sentence is noise on a covered harbor.
    covered = P.compute_ca_safe_harbor(400.0, 18000.0, 90000.0, 200000.0,
                                       "MFJ", withholding=100.0,
                                       estimates_paid=0.0)
    assert covered.de_minimis
    assert "EITHER year" not in "\n".join(P._ca_notes(covered))

    # NOT at a $0 prior year either: the note above already clears that by this
    # same subdivision, and two notes on one rule read as a contradiction.
    zero = P.compute_ca_safe_harbor(20000.0, 0.0, 12000.0, 200000.0, "MFJ",
                                    withholding=0.0, estimates_paid=0.0)
    assert zero.required_annual == 0.0
    zero_note = "\n".join(P._ca_notes(zero))
    assert "19136(c)(2)" in zero_note and "EITHER year" not in zero_note


def test_the_degraded_ca_note_no_longer_offers_a_confirmation_route():
    """Degraded now has exactly ONE cause, and the note must say that one.

    While the $0 gate existed, "no usable prior-year CA baseline" had two
    causes and the note named the second. With the gate gone a computed $0 is
    used, so degraded means the baseline really is absent — and a note still
    offering a confirmation flag would send a taxpayer to set a key nothing
    reads.
    """
    csh = P.compute_ca_safe_harbor(20000.0, None, None, 200000.0, "MFJ",
                                   withholding=0.0, estimates_paid=0.0)
    assert csh.degraded
    note = "\n".join(P._ca_notes(csh))
    assert "confirm" not in note.lower(), (
        "the degraded CA note still asks for a confirmation; nothing reads one")
    assert "archive/{year-1}/" in note and "template scaffold" in note, (
        "the degraded CA note must name what is actually missing")


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
    # Negative AGI (loss year) is legitimate and makes the section present —
    # and a genuinely COMPUTED $0 CA prior tax is passed through unasked,
    # because R&TC §19136(c)(2) clears it with no federal-style precondition.
    # `_ca_section_computed` is the guard that separates it from p3's
    # untouched scaffold, and it is the only one this figure needs.
    p4 = _Prior({"california_540": {"ca_total_tax": 0, "ca_agi": -5000}})
    assert P._prior_ca_tax_agi(p4) == (0.0, -5000.0)


def _cli_workspace(tmp_path, inputs):
    """Minimal /tax-project workspace: inputs + profile + a TY2026 cache."""
    (tmp_path / "projection_inputs.json").write_text(
        json.dumps(_declared_standard(inputs)))
    (tmp_path / "taxpayer_profile.json").write_text(json.dumps(_profile_mfj()))
    (tmp_path / "tax_law_2026.json").write_text(json.dumps(_tax_law_2026()))
    return ["--inputs", str(tmp_path / "projection_inputs.json"),
            "--workspace", str(tmp_path), "--as-of", "06-30",
            "--today", "2026-07-01", "--json"]


def test_the_projection_cli_refuses_a_declared_wrong_year(tmp_path, capsys):
    """A prior-year inputs file run under --year is the WRONG return's base.

    The withholding CLI has compared `inputs["tax_year"]` with `--year` since
    it shipped; this one never did. That was survivable while the file held
    only YTD figures — the run was mislabelled and no more. It stopped being
    survivable when `prior_year_form_2210_tax_base` and its paired AGI moved
    into the same file: those are composed under the COVERED year's own Form
    2210 line-8 instructions, whose enumerated inclusions and exclusions
    change year to year. A TY2025 file run with `--year 2026` therefore feeds
    the 2025 return's base to the 2026 prong and reports a requirement built
    from a return that is not the preceding one, which AGENTS.md §2's
    stale-figure contract forbids outright.

    ABSENT still runs: `tax_year` is nullable in the template, and refusing
    its absence would break every file written before the field existed.
    """
    inputs = {"tax_year": 2025, "pay_frequency": "biweekly",
              "ytd": {"wages": 100000, "federal_withholding": 12000},
              "estimated_payments_made": {"federal": 0}}
    rc = P._main(_cli_workspace(tmp_path, inputs) + ["--year", "2026"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "TY2025" in err and "TY2026" in err, (
        "the refusal must name both years — which file is wrong is the whole "
        f"content of the message: {err!r}")

    # Matching declaration runs.
    inputs["tax_year"] = 2026
    assert P._main(_cli_workspace(tmp_path, inputs) + ["--year", "2026"]) == 0

    # Absent declaration still runs, unchanged.
    del inputs["tax_year"]
    assert P._main(_cli_workspace(tmp_path, inputs) + ["--year", "2026"]) == 0


def test_a_yearless_file_that_answers_a_prior_year_field_is_refused(tmp_path, capsys):
    """The nullable escape hatch does not cover the year-bound fields.

    `tax_year` ships null in the template, so "declared mismatch refuses,
    absent degrades" left the common case wide open: a taxpayer fills in
    `prior_year_form_2210_tax_base` from the template, never touches
    `tax_year`, and next season the same file runs under `--year` + 1. The
    base was composed under the OLD covered year's Form 2210 line-8
    instructions, and it is silently applied as the new year's prior-return
    base — the exact stale-figure failure AGENTS.md §2 forbids, with nothing
    left in the output to catch it afterwards.

    So the absence is only tolerated while nothing in the file is bound to a
    year. Answer one of the four and the year becomes required. Both CLIs
    share the one guard (`_require_inputs_tax_year`); its twin in
    tests/test_withholding.py pins the other caller.
    """
    base = {"pay_frequency": "biweekly",
            "ytd": {"wages": 100000, "federal_withholding": 12000},
            "estimated_payments_made": {"federal": 0}}

    # The template's own defaults are NOT an answer: two nulls and two
    # falses. A guard that fired on key PRESENCE would refuse every file
    # written from the template, which is the gate that cries wolf.
    template_shaped = dict(base, **{
        "prior_year_form_2210_tax_base": None,
        "prior_year_form_2210_agi": None,
        "prior_year_zero_liability_confirmed": False,
        "prior_year_return_filed_12_months_confirmed": False})
    assert P._main(_cli_workspace(tmp_path, template_shaped)
                   + ["--year", "2026"]) == 0, (
        "the template's year-bound defaults declare no answer and must still "
        "run")

    for field, value in (("prior_year_form_2210_tax_base", 40000.0),
                         ("prior_year_form_2210_agi", 200000.0),
                         ("prior_year_zero_liability_confirmed", True),
                         ("prior_year_return_filed_12_months_confirmed", True)):
        answered = dict(base, **{field: value})
        rc = P._main(_cli_workspace(tmp_path, answered) + ["--year", "2026"])
        assert rc == 1, f"{field} answered without a tax_year must refuse"
        err = capsys.readouterr().err
        assert field in err and "tax_year" in err, (
            "the refusal must name the field that made the year required and "
            f"the field that fixes it: {err!r}")

        # ... and declaring the year clears it.
        assert P._main(_cli_workspace(tmp_path, dict(answered, tax_year=2026))
                       + ["--year", "2026"]) == 0, (
            f"{field} with a matching tax_year must run")


def test_a_filed_12_month_zero_reaches_the_ordinary_prior_year_prong():
    """§6654(d)(1)(B)(ii) has no citizenship condition. §6654(e)(2) does.

    The two tests are independent and neither implies the other: (e)(2) needs
    a 12-month prior year plus US citizenship/residency throughout it and no
    filed return; (d)(1)(B)'s closing text needs a 12-month prior year plus a
    filed return and says nothing about citizenship. One flag standing for
    both refused a $0 base to every taxpayer who could not affirm the
    citizenship half — a first-year resident or dual-status filer holding a
    full 12-month $0 return — and demanded 90% of the current year from
    someone the ordinary prong already covers at 100%/110% of $0.

    tax-safe-harbor SKILL.md has listed them as separate preconditions since
    it shipped, so this was also the engine contradicting the engine-free
    skill on the same taxpayer.
    """
    # Neither confirmed: still refused, and the message names BOTH routes.
    with pytest.raises(P.ProjectionError) as exc:
        P._prior_tax_agi(0)
    msg = str(exc.value)
    assert "6654(e)(2)" in msg and "6654(d)(1)(B)(ii)" in msg
    assert "prior_year_return_filed_12_months_confirmed" in msg, (
        "the refusal offers one way out of two, so the taxpayer the other "
        "one covers is never told it exists")

    # Either flag alone is enough, and they are genuinely independent.
    assert P._prior_tax_agi(0, 95000, zero_liability_confirmed=True) == \
        (0.0, 95000.0)
    assert P._prior_tax_agi(
        0, 95000, return_filed_12_months_confirmed=True) == (0.0, 95000.0)

    # And it carries through the harbor to a $0 requirement. Prior AGI is
    # deliberately None here: that is the case the caveat fires on, and at a
    # $0 base the caveat is FALSE — 110% of $0 and 100% of $0 are both $0, so
    # nothing about the missing AGI can understate the figure.
    sh = P.compute_safe_harbor(50000.0, 0.0, None, "Single",
                               withholding=0.0, estimates_paid=0.0)
    assert sh.required_annual == 0.0 and sh.covered
    assert not sh.prior_agi_missing, (
        "the 100%/110% factor cannot change 0, so no caveat may claim the "
        "figure might be understated for want of a prior-year AGI")


def test_prior_tax_agi_federal_requires_and_validates_completed_line8_base():
    # No explicit base -> unresolved on BOTH figures. `compute_safe_harbor`
    # never reads `prior_agi` once `prior_tax` is None, so there is no AGI to
    # hand back that anything could consume.
    assert P._prior_tax_agi() == (None, None), (
        "the federal prior-year prong resolved without a completed line-8 base")
    # An explicit base without a paired AGI stays unpaired — tax-amend can
    # overwrite archive/{year}/result.json with an amended return's figures,
    # mismatching an original-return tax base, so no other AGI is substituted.
    assert P._prior_tax_agi(14400) == (14400.0, None)
    assert P._prior_tax_agi(14400, 95000) == (14400.0, 95000.0)
    for bad in (True, float("nan"), -1, "not-a-number"):
        with pytest.raises(P.ProjectionError,
                           match="prior_year_form_2210_tax_base"):
            P._prior_tax_agi(bad)


def test_prior_tax_agi_reads_the_paired_agi_at_the_same_parse_boundary():
    """A hand-typed AGI is the AGI, and an unreadable one is not silence.

    Both figures come from the same hand-edited `projection_inputs.json`, so
    both take the same coercion. Reading the AGI through the ARCHIVE sanitizer
    dropped a STRING one to None: the 110% factor was never selected, the
    required annual payment came out 10% low — the understating direction —
    and the report announced "Prior-year AGI unavailable" about a figure the
    taxpayer had supplied.
    """
    for typed in ("162000", "$162,000", "162,000.00", 162000, 162000.0):
        assert P._prior_tax_agi(18400, typed) == (18400.0, 162000.0), (
            f"{typed!r} was not read as the prior-year AGI")
    # The whole point of reading it: at $162,000 the prong takes 110%.
    prior_tax, prior_agi = P._prior_tax_agi("18400", "162000")
    harbor = P.compute_safe_harbor(30000.0, prior_tax, prior_agi, "Single",
                                   withholding=0.0, estimates_paid=0.0)
    assert harbor.prior_factor == 1.10 and harbor.prior_based == 20240.0, (
        "a string prior-year AGI no longer selects the 110% factor")
    assert harbor.prior_agi_missing is False

    # A LOSS year is a real negative AGI, below every threshold — allowed.
    assert P._prior_tax_agi(14400, -5000) == (14400.0, -5000.0)
    # Absent degrades (100% factor, flagged); present-but-unreadable raises.
    assert P._prior_tax_agi(14400, None) == (14400.0, None)
    for bad in (True, float("nan"), float("inf"), "not-a-number", {}):
        with pytest.raises(P.ProjectionError,
                           match="prior_year_form_2210_agi"):
            P._prior_tax_agi(14400, bad)


def test_a_catchup_note_refuses_the_other_jurisdictions_safe_harbor():
    """The note and the harbor must name the same instalment calendar.

    Both harbors are duck-typed over the four fields the gate reads, so the
    federal note with a CaSafeHarbor type-checks and runs 25/50/75/100 against
    CA figures. FTB 5805 Worksheet II weights BOTH the requirement and the
    withholding at 30/40/0/30, so a $5,000 CA requirement met by $4,189 of CA
    withholding owes nothing at April 15 — and the federal weights called it
    short. Over-warning is the hard-error direction.
    """
    as_of = datetime.date(2026, 7, 2)
    ca = P.compute_ca_safe_harbor(5555.56, None, None, 50000, "Single",
                                  withholding=4189.0, estimates_paid=0.0)
    assert ca.required_annual == 5000.0
    vouchers = P.compute_ca_estimated_vouchers(ca.shortfall, as_of, 0.0)

    # Correctly paired: answers, whatever the answer is.
    P._voucher_catchup_note(P._CA_CATCHUP, vouchers, ca, as_of)

    with pytest.raises(P.ProjectionError, match="30/70/70/100"):
        P._voucher_catchup_note(P._FEDERAL_CATCHUP, vouchers, ca, as_of)

    fed = P.compute_safe_harbor(5555.56, None, None, "Single",
                                withholding=4189.0, estimates_paid=0.0)
    with pytest.raises(P.ProjectionError, match="25/50/75/100"):
        P._voucher_catchup_note(P._CA_CATCHUP, vouchers, fed, as_of)

    # The seam stays open: a stub declaring no jurisdiction is still accepted,
    # which is what keeps this gate testable without constructing a harbor.
    stub = types.SimpleNamespace(required_annual=5000.0, withholding=4189.0,
                                 estimates_paid=0.0, de_minimis=False)
    P._voucher_catchup_note(P._CA_CATCHUP, vouchers, stub, as_of)


def test_estimates_paid_is_validated_before_the_covered_year_early_return():
    """A covered year must not swallow an invalid figure, and the error must
    name the key in `projection_inputs.json` rather than the parameter.

    Below the early return the check ran only on a year that happened to be
    under-withheld, so a negative or NaN estimate returned a clean all-zero
    schedule; and `estimates_paid` is a name the reader cannot find in the
    file they edit — the defect `annualize` had already fixed on its side.
    """
    as_of = datetime.date(2026, 3, 1)
    cases = ((P.compute_estimated_vouchers, "estimated_payments_made.federal"),
             (P.compute_ca_estimated_vouchers, "estimated_payments_made.state"))
    for schedule, field in cases:
        for covered_shortfall in (0.0, -25.0):
            with pytest.raises(P.ProjectionError, match=re.escape(field)):
                schedule(covered_shortfall, as_of, -5000)
        for bad in (float("nan"), float("inf"), True, "not-a-number"):
            with pytest.raises(P.ProjectionError, match=re.escape(field)):
                schedule(1000.0, as_of, bad)
        # A covered year with a VALID figure still returns the zero schedule.
        assert all(v.amount == 0.0 for v in schedule(0.0, as_of, 300.0))


def test_prior_tax_agi_zero_base_requires_zero_liability_confirmation():
    """A $0 base only zeroes the prong once Sec. 6654(e)(2)'s two preconditions
    are confirmed; unconfirmed, it must not silently zero the required payment."""
    with pytest.raises(P.ProjectionError, match="zero_liability_confirmed"):
        P._prior_tax_agi(0)
    assert P._prior_tax_agi(0, zero_liability_confirmed=True) == (0.0, None)


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
    r = _build_report(
        {"pay_frequency": "biweekly",
         "prior_year_form_2210_tax_base": 15000,
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


def test_ca_voucher_share_column_matches_the_amounts_beside_it():
    """The share column is derived from the AMOUNTS, so it cannot contradict them.

    Renormalizing the statutory weights agreed with the amounts on the
    `_split_remainder_last` path and nowhere else. Before the first deadline
    the schedule allocates against the cumulative 30/70/70/100 checkpoints NET
    of estimates already paid, and the two columns then disagreed outright: a
    $700 CA shortfall with $300 of estimates already in prints
    $0 / $400 / $0 / $300 — 0/57/0/43 — under a weight column reading
    30/40/0/30, with "30%" sitting beside $0.00 at April 15.
    """
    as_of = datetime.date(2026, 3, 1)
    vouchers = P.compute_ca_estimated_vouchers(700.0, as_of, 300.0)
    assert [(v.quarter, v.amount) for v in vouchers] == [
        (1, 0.0), (2, 400.0), (3, 0.0), (4, 300.0)]
    total = round(sum(v.amount for v in vouchers), 2)
    assert [round(v.amount / total, 4) for v in vouchers] == [
        0.0, 0.5714, 0.0, 0.4286], (
        "the amounts moved; this test's whole point is that the printed share "
        "tracks them rather than a weight computed independently")

    ca_weights = {q: w for q, _, w in P.CA_ES_SCHEDULE}
    open_total = sum(ca_weights[v.quarter] for v in vouchers)
    renormalized = [ca_weights[v.quarter] / open_total for v in vouchers]
    assert [f"{s:.0%}" for s in renormalized] == ["30%", "40%", "0%", "30%"], (
        "the statutory renormalization no longer disagrees with the amounts "
        "on this input, so this test no longer pins anything")


def test_the_ca_table_explains_an_early_estimate_split_the_old_gate_missed():
    """The other way the Share column stops being 30/40/0/30.

    Before the first deadline `compute_ca_estimated_vouchers` allocates
    against the cumulative 30/70/70/100 checkpoints NET of estimates already
    paid, so $700 short with $300 already in schedules 0/400/0/300 — printed
    0% / 57% / 0% / 43%. All four installments are still open, so the old
    `len(r.ca_vouchers) < len(CA_ES_SCHEDULE)` gate did not fire and nothing
    explained the divergence; the heading one line above asserted the
    statutory split outright.
    """
    vouchers = P.compute_ca_estimated_vouchers(700.0, datetime.date(2026, 3, 1), 300.0)
    assert [v.amount for v in vouchers] == [0.0, 400.0, 0.0, 300.0]
    assert len(vouchers) == len(P.CA_ES_SCHEDULE), (
        "all four installments are open — this is exactly the case the "
        "closed-installment gate cannot see")
    shares = P._percent_shares([v.amount for v in vouchers])
    assert shares == [0, 57, 0, 43]
    assert shares != [round(100 * w) for _, _, w in P.CA_ES_SCHEDULE]

    # And the rendered report explains it, on a path where all four
    # installments are open and an estimate has already been paid.
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 120000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = _build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 20000, "federal_withholding": 2000, "state_withholding": 100},
         "estimated_payments_made": {"federal": 0, "state": 300}},
        _profile_mfj(), _tax_law_2026(), prior, 2026, datetime.date(2026, 3, 1))
    assert len(r.ca_vouchers) == len(P.CA_ES_SCHEDULE)
    md = P.render_markdown(r)
    assert "The instalments above are not in the statutory" in md
    assert "cumulative 30% / 70% / 70% / 100% checkpoints" in md
    assert "earlier installments have closed" not in md, (
        "the closed-installment cause is the OTHER branch — naming it here "
        "would explain the split with something that did not happen")


def test_the_ca_share_column_sums_to_one_hundred_percent():
    """A "Share of remaining" column that adds to 101% contradicts its own
    total, the same defect one column over as the heading did.

    Rounding each share independently is what does it: the early-estimate
    path allocates 796 / 1461 / 0 / 1096 of $3,353, whose exact shares are
    23.7 / 43.6 / 0 / 32.7 and round to 24 / 44 / 0 / 33.
    """
    assert sum(P._percent_shares([796.0, 1461.0, 0.0, 1096.0])) == 100
    assert P._percent_shares([0.0, 0.0, 0.0, 0.0]) == [0, 0, 0, 0]
    for amounts in ([1.0, 1.0, 1.0], [700.0], [0.0, 400.0, 0.0, 300.0],
                    [1.0, 2.0, 3.0, 994.0], [333.0, 333.0, 333.0]):
        shares = P._percent_shares(amounts)
        assert sum(shares) == 100
        total = sum(amounts)
        for amount, share in zip(amounts, shares):
            assert abs(share - 100.0 * amount / total) < 1.0


def test_ca_voucher_table_shows_renormalized_share():
    """P2: printing the statutory 30% next to a 100%-of-shortfall Q4 amount
    misleads — the table must show the share of the REMAINING shortfall."""
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 120000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = _build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 100},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        _profile_mfj(), _tax_law_2026(), prior, 2026, datetime.date(2026, 7, 2))
    md = P.render_markdown(r)
    assert "Share of remaining" in md
    assert "| Q4 | 2027-01-15 | 100% |" in md  # only weighted installment left
    assert "earlier installments have closed" in md
    assert "The instalments above are not in the statutory" in md
    assert "pay catch-up amounts NOW" in md
    # Proxy caveat always present on the projection CA block.
    assert "federal-composition proxy" in md


def test_ca_near_boundary_warning_rendered():
    # Projected CA AGI ~980K (MFJ) — within 10% of the $1M cliff but not over.
    prior = _Prior({"california_540": {"ca_total_tax": 5000, "ca_agi": 900000},
                    "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    r = _build_report(
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
    r = _build_report(
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


class TestFloridaStateSection:
    def test_florida_in_state_section_keys(self):
        from agents.lib.projection import STATE_SECTION_KEYS
        assert "california_540" in STATE_SECTION_KEYS  # regression pin
        assert "florida_state" in STATE_SECTION_KEYS

    def test_fl_prior_section_never_projects_state_tax(self):
        # FL prior section detected, but tax_law has no brackets → None
        # (silently correct for a no-income-tax state), never an exception.
        from agents.lib.projection import project_state_tax
        class _Prior:
            result = {"florida_state": {"state_return_required": False}}
        out = project_state_tax(
            _proj(), _Prior(),
            {"state": {"type": "none", "code": "FL"}}, "MFJ",
            resident_state="FL")
        assert out is None

    def test_fl_with_leftover_brackets_still_returns_none(self):
        # Codex round-12 P1: check_freshness (round 11) flags a type-none
        # cache carrying real leftover brackets as a contradiction, but this
        # function reads the cache directly and never calls check_freshness
        # — a code: "FL", type: "none" cache with leftover CA brackets
        # (copied/partially replaced, never stripped) must NOT have this
        # function invent a Florida tax liability from them. type == "none"
        # wins over brackets content unconditionally.
        from agents.lib.projection import project_state_tax
        class _Prior:
            result = {"florida_state": {"state_return_required": False}}
        law = {"state": {"type": "none", "code": "FL",
                         "brackets": [{"rate": 0.093, "min": 0, "max": None}],
                         "standard_deduction": {"mfj": 11080}}}
        out = project_state_tax(_proj(), _Prior(), law, "MFJ",
                                resident_state="FL")
        assert out is None

    def test_build_report_fl_with_leftover_brackets_stays_no_income_tax(self):
        # End-to-end companion: build_report must still resolve to the
        # affirmative "no individual income tax" outcome (state is a
        # legitimate FL no-tax code/year combo), not a fabricated FL tax
        # table, even with the contradictory leftover brackets present.
        law = dict(_tax_law_2026())
        law["state"] = {"type": "none", "code": "FL",
                        "brackets": [{"rate": 0.093, "min": 0, "max": None}],
                        "standard_deduction": {"mfj": 11080}}
        r = _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile={"filing_status": "MFJ", "dependents": [],
                     "address": {"state": "FL"}},
            tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
        assert r.state is None
        assert r.state_no_income_tax is True
        assert r.state_cache_mismatch is None
        md = P.render_markdown(r)
        assert "levies no individual income tax" in md
        assert "| State (" not in md  # no fabricated state tax table

    def test_fl_typed_graduated_income_with_brackets_still_returns_none(self):
        # Codex round-15 P1: the INVERSE contradiction — a code that IS a
        # legitimate no-tax state (FL) but typed "graduated_income" while
        # retaining real leftover brackets/standard_deduction must not have
        # this function invent a Florida tax liability either. Round-12
        # only guarded the type == "none" direction.
        from agents.lib.projection import project_state_tax
        class _Prior:
            result = {"florida_state": {"state_return_required": False}}
        law = {"tax_year": 2026,
              "state": {"type": "graduated_income", "code": "FL",
                        "brackets": [{"rate": 0.093, "min": 0, "max": None}],
                        "standard_deduction": {"mfj": 11080}}}
        out = project_state_tax(_proj(), _Prior(), law, "MFJ",
                                resident_state="FL")
        assert out is None

    def test_build_report_fl_typed_graduated_income_surfaces_mismatch(self):
        # End-to-end companion: build_report must surface the specific
        # contradiction note (not a fabricated FL tax table, and not a
        # false affirmative "no individual income tax" claim either, since
        # cache_says_none is False here).
        law = dict(_tax_law_2026())
        law["state"] = {"type": "graduated_income", "code": "FL",
                        "brackets": [{"rate": 0.093, "min": 0, "max": None}],
                        "standard_deduction": {"mfj": 11080}}
        r = _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile={"filing_status": "MFJ", "dependents": [],
                     "address": {"state": "FL"}},
            tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
        assert r.state is None
        assert r.state_no_income_tax is False
        assert r.state_cache_mismatch is not None
        assert "FL" in r.state_cache_mismatch
        md = P.render_markdown(r)
        assert "levies no individual income tax" not in md
        assert "| State (" not in md

    def test_malformed_cache_code_never_backfills_or_projects(self):
        # Codex round-14 P2: a PRESENT-but-INVALID state.code ("FLA") must
        # not collapse to the same "unknown, safe to backfill" None a
        # genuinely ABSENT code produces. A CA-bracket cache whose code was
        # corrupted to "FLA" beside an FL profile must not have this
        # function backfill "FL" from the resident and project CA's real
        # brackets as Florida tax.
        from agents.lib.projection import project_state_tax
        class _Prior:
            result = {"florida_state": {"state_return_required": False}}
        law = {"state": {"code": "FLA",
                         "brackets": [{"rate": 0.093, "min": 0, "max": None}],
                         "standard_deduction": {"mfj": 11080}}}
        out = project_state_tax(_proj(), _Prior(), law, "MFJ",
                                resident_state="FL")
        assert out is None

    def test_build_report_malformed_cache_code_surfaces_mismatch(self):
        # End-to-end companion: build_report must surface a loud mismatch
        # note (not a fabricated state tax table, and not a false "no
        # individual income tax" claim either) for a malformed cache code.
        law = dict(_tax_law_2026())
        law["state"]["code"] = "FLA"
        r = _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000,
                            "state_withholding": 2000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile={"filing_status": "MFJ", "dependents": [],
                     "address": {"state": "FL"}},
            tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
        assert r.state is None
        assert r.state_no_income_tax is False
        assert r.state_cache_mismatch is not None
        assert "FLA" in r.state_cache_mismatch
        md = P.render_markdown(r)
        assert "levies no individual income tax" not in md
        assert "| State (" not in md

    def test_explicit_null_code_is_invalid_not_absent(self):
        # Codex round-15 P2: the round-14 predicate checked
        # `raw is not None`, so an EXPLICIT "code": null (key present,
        # value None) slipped through as if the key had never existed —
        # exactly like the "FLA" case, a CA-bracket cache with code: null
        # beside an FL profile must not be backfilled to "FL" and have CA's
        # real brackets projected as Florida tax.
        from agents.lib.projection import (
            _cache_state_code_present_but_invalid, project_state_tax,
        )
        assert _cache_state_code_present_but_invalid({"code": None}) is True
        assert _cache_state_code_present_but_invalid({}) is False
        assert _cache_state_code_present_but_invalid({"code": "FL"}) is False

        class _Prior:
            result = {"florida_state": {"state_return_required": False}}
        law = {"state": {"code": None,
                         "brackets": [{"rate": 0.093, "min": 0, "max": None}],
                         "standard_deduction": {"mfj": 11080}}}
        out = project_state_tax(_proj(), _Prior(), law, "MFJ",
                                resident_state="FL")
        assert out is None

    def test_a_shape_valid_code_that_names_no_state_is_malformed_too(self):
        """The refusal already SAID "USPS"; the test underneath was the shape.

        Both this module and `loss_harvest` decline a bad cache code with "not
        a valid 2-letter USPS code", and `_normalize_state_code` checked only
        that it was two letters — so "ZZ" was accepted as a valid code by the
        very predicate whose message promises it is not one. That is the lying
        caveat this repo ranks worse than a missing one, and it is the same
        shape-for-membership substitution that let a two-letter typo invent a
        state deduction in `retirement_dashboard`.

        Both consequences move the SAFE way, which is why the fix is a
        tightening and not a trade: an unreal CACHE code becomes the loud
        malformed-code note instead of a code trusted for a comparison, and an
        unreal RESIDENT code becomes the already-modeled "resident unknown"
        degrade. It degrades rather than raising — `capabilities` hard-errors
        on the same field, but state modelling here is optional and already
        has a labelled fall-through.
        """
        from agents.lib.projection import (
            _cache_state_code_present_but_invalid, _normalize_state_code,
        )
        for real in ("CA", "fl", " tx ", "DC", "PR"):
            assert _normalize_state_code(real) == real.strip().upper()
            assert _cache_state_code_present_but_invalid({"code": real}) is False
        for unreal in ("ZZ", "CN", "BC", "XX"):
            assert _normalize_state_code(unreal) is None, (
                f"{unreal!r} is two letters and no state; normalizing it "
                "makes the USPS refusal message a false claim")
            assert _cache_state_code_present_but_invalid({"code": unreal}) is True

        # End to end, on the message that was making the claim.
        law = dict(_tax_law_2026())
        law["state"]["code"] = "ZZ"
        r = _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000,
                            "state_withholding": 2000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile={"filing_status": "MFJ", "dependents": [],
                     "address": {"state": "FL"}},
            tax_law=law, prior=None, year=2026,
            as_of=datetime.date(2026, 7, 2))
        assert r.state is None
        assert r.state_cache_mismatch is not None and "ZZ" in r.state_cache_mismatch
        assert "USPS" in r.state_cache_mismatch

    def test_build_report_null_code_surfaces_mismatch(self):
        law = dict(_tax_law_2026())
        law["state"]["code"] = None
        r = _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000,
                            "state_withholding": 2000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile={"filing_status": "MFJ", "dependents": [],
                     "address": {"state": "FL"}},
            tax_law=law, prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
        assert r.state is None
        assert r.state_no_income_tax is False
        assert r.state_cache_mismatch is not None
        md = P.render_markdown(r)
        assert "levies no individual income tax" not in md


class TestNonresidentIncomeWarning:
    """Codex round-8 finding F: build_report affirmed no-state-projection
    from residence+cache alone, ignoring flags.has_nonresident_source_income
    — a resident-state projection (or a resident no-tax note) says nothing
    about a SEPARATE state's nonresident liability on out-of-state income.
    The warning must fire regardless of which state-projection path ran."""

    def _fl_report(self, nonresident_flag):
        law = dict(_tax_law_2026())
        law["state"] = {"type": "none", "code": "FL"}
        profile = {"filing_status": "MFJ", "dependents": [],
                   "address": {"state": "FL"}}
        if nonresident_flag is not None:
            profile["flags"] = {"has_nonresident_source_income": nonresident_flag}
        return _build_report(
            inputs={"pay_frequency": "biweekly",
                    "ytd": {"wages": 50000, "federal_withholding": 6000},
                    "estimated_payments_made": {"federal": 0, "state": 0}},
            profile=profile, tax_law=law, prior=None, year=2026,
            as_of=datetime.date(2026, 7, 2))

    def _ca_report(self, nonresident_flag):
        law = _tax_law_2026()
        inputs = {
            "pay_frequency": "biweekly",
            "ytd": {"wages": 50000, "federal_withholding": 6000, "state_withholding": 2000},
            "forecast_overrides": {"capital_gains": 0},
            "recurring_from_prior": {"taxable_interest": 0},
            "estimated_payments_made": {"federal": 0, "state": 0},
        }
        prior = _Prior(
            {"california_540": {"ca_total_tax": 5000},
             "form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
        profile = {"filing_status": "MFJ", "dependents": []}
        if nonresident_flag is not None:
            profile["flags"] = {"has_nonresident_source_income": nonresident_flag}
        return _build_report(inputs=inputs, profile=profile, tax_law=law,
                              prior=prior, year=2026, as_of=datetime.date(2026, 7, 2))

    def test_fl_path_warning_present_when_flag_true(self):
        r = self._fl_report(True)
        assert r.state is None and r.state_no_income_tax is True  # FL no-tax path
        assert r.nonresident_income_warning == P._NONRESIDENT_INCOME_WARNING
        md = P.render_markdown(r)
        assert "Nonresident income exposure" in md
        assert P._NONRESIDENT_INCOME_WARNING in md
        d = P._report_to_dict(r)
        assert d["nonresident_income_warning"] == P._NONRESIDENT_INCOME_WARNING

    def test_ca_path_warning_present_when_flag_true(self):
        r = self._ca_report(True)
        assert r.state is not None and r.state.code == "CA"  # CA graduated path
        assert r.nonresident_income_warning == P._NONRESIDENT_INCOME_WARNING
        md = P.render_markdown(r)
        assert "Nonresident income exposure" in md
        assert P._NONRESIDENT_INCOME_WARNING in md
        d = P._report_to_dict(r)
        assert d["nonresident_income_warning"] == P._NONRESIDENT_INCOME_WARNING

    def test_fl_path_warning_absent_when_flag_false(self):
        r = self._fl_report(False)
        assert r.nonresident_income_warning is None
        md = P.render_markdown(r)
        assert "Nonresident income exposure" not in md
        assert P._report_to_dict(r)["nonresident_income_warning"] is None

    def test_ca_path_warning_absent_when_flag_false(self):
        r = self._ca_report(False)
        assert r.nonresident_income_warning is None
        md = P.render_markdown(r)
        assert "Nonresident income exposure" not in md
        assert P._report_to_dict(r)["nonresident_income_warning"] is None

    def test_warning_absent_when_flag_key_missing_entirely(self):
        # No flags block at all (legacy profile) — advisory read defaults to
        # False, unlike the capabilities gate's fail-closed rule.
        r = self._fl_report(None)
        assert r.nonresident_income_warning is None
        r2 = self._ca_report(None)
        assert r2.nonresident_income_warning is None

    def test_flag_value_helper_rejects_non_bool_truthy_as_false(self):
        # Advisory read: only a literal True triggers the warning — a
        # non-bool truthy value (e.g. a stray string) does not silently
        # turn it on, mirroring the "value not just type" caution used
        # elsewhere in this codebase, but staying non-blocking (no error).
        from agents.lib.projection import _nonresident_income_flag
        assert _nonresident_income_flag(
            {"flags": {"has_nonresident_source_income": "true"}}) is False
        assert _nonresident_income_flag({"flags": {}}) is False
        assert _nonresident_income_flag({}) is False
        assert _nonresident_income_flag(
            {"flags": {"has_nonresident_source_income": True}}) is True


def test_project_state_tax_refuses_pre_repeal_tn_hall_brackets():
    # Codex round-23 P2: pre-2021 TN's Hall tax applied ONLY to interest
    # and dividends, but project_state_tax builds taxable income from
    # wages/SE/capital gains too — routing a TY2020 TN cache (expected
    # regime graduated_income, Hall rate stored as a bracket) through the
    # generic bracket path would fabricate a liability for a TN wage
    # earner. Pre-repeal TN is UNSUPPORTED: skip, never mismodel.
    # (Post-repeal TN is type "none" and already short-circuits via the
    # round-15 _none_type_allowed inverse-contradiction guard.)
    law = _tax_law_2026()
    law["tax_year"] = 2020
    law["state"] = {"type": "graduated_income", "code": "TN",
                    "standard_deduction": {"mfj": 0},
                    "brackets": [{"rate": 0.01, "min": 0, "max": None}]}
    assert P.project_state_tax(_proj(), None, law, "MFJ",
                               resident_state="TN") is None


def test_project_state_tax_refuses_tn_resident_backfill_on_codeless_hall_cache():
    # The TN guard runs AFTER the resident backfill: a code-less Hall-rate
    # cache beside a TN resident must be caught too (code = cache_code or
    # resident). Control: the identical code-less cache beside a GA
    # resident still computes — proving the skip is the TN guard, not an
    # earlier gate.
    law = _tax_law_2026()
    law["tax_year"] = 2020
    law["state"] = {"type": "graduated_income",
                    "standard_deduction": {"mfj": 0},
                    "brackets": [{"rate": 0.01, "min": 0, "max": None}]}
    assert P.project_state_tax(_proj(), None, law, "MFJ",
                               resident_state="TN") is None
    control = P.project_state_tax(_proj(), None, law, "MFJ",
                                  resident_state="GA")
    assert control is not None


def test_build_report_surfaces_hall_tax_skip_note():
    # Codex round-24 P2: the round-23 TN guard skipped silently, so
    # render_markdown fell through to the generic "no prior-year state
    # baseline or no state brackets" message — concealing that a real
    # Hall-tax liability on interest/dividends needs manual handling. The
    # skip must be NAMED. (Reachable only when the requested year itself
    # is pre-2021 — the round-11/13 year guards reject a 2020 cache
    # against any other requested year.)
    law = _tax_law_2026()
    law["tax_year"] = 2020
    law["state"] = {"type": "graduated_income", "code": "TN",
                    "standard_deduction": {"mfj": 0},
                    "brackets": [{"rate": 0.01, "min": 0, "max": None}]}
    inputs = {"pay_frequency": "biweekly",
              "ytd": {"wages": 50000, "federal_withholding": 6000},
              "estimated_payments_made": {"federal": 0, "state": 0}}
    r = _build_report(inputs=inputs, profile=_profile_mfj(), tax_law=law,
                       prior=None, year=2020, as_of=datetime.date(2020, 7, 2))
    assert r.state is None
    assert r.state_unsupported_note is not None
    assert "INC 250" in r.state_unsupported_note
    assert "interest & dividends ONLY" in r.state_unsupported_note
    md = P.render_markdown(r)
    assert "State not modeled" in md
    assert "no prior-year state baseline" not in md
    d = P._report_to_dict(r)
    assert d["state_unsupported_note"] == r.state_unsupported_note


def test_build_report_hall_note_absent_for_non_tn_skip():
    # Control: a generic bracket-less skip keeps the original message and
    # NO Hall note — the note is TN-specific, never a blanket replacement.
    law = _tax_law_2026()
    law["state"] = {}
    inputs = {"pay_frequency": "biweekly",
              "ytd": {"wages": 50000, "federal_withholding": 6000},
              "estimated_payments_made": {"federal": 0, "state": 0}}
    r = _build_report(inputs=inputs, profile=_profile_mfj(), tax_law=law,
                       prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.state is None
    assert r.state_unsupported_note is None
    md = P.render_markdown(r)
    assert "State not modeled" not in md


def test_build_report_no_hall_note_for_post_repeal_codeless_tn_cache():
    # Codex round-27 P2: a TY2021+ TN resident with a legacy code-less
    # bracket cache reaches the TN skip via the resident backfill, but
    # labeling that skip "predates the Hall-tax repeal" asserts a false
    # fact about a post-repeal year — its real problem is an unverifiable
    # cache. The Hall note requires year < 2021; post-repeal keeps the
    # generic skip message.
    law = _tax_law_2026()
    law["state"] = {"type": "graduated_income",
                    "standard_deduction": {"mfj": 0},
                    "brackets": [{"rate": 0.01, "min": 0, "max": None}]}
    inputs = {"pay_frequency": "biweekly",
              "ytd": {"wages": 50000, "federal_withholding": 6000},
              "estimated_payments_made": {"federal": 0, "state": 0}}
    profile = _profile_mfj()
    profile["address"] = {"state": "TN"}
    r = _build_report(inputs=inputs, profile=profile, tax_law=law,
                       prior=None, year=2026, as_of=datetime.date(2026, 7, 2))
    assert r.state is None
    assert r.state_unsupported_note is None
    md = P.render_markdown(r)
    assert "State not modeled" not in md
    assert "Hall" not in md


def test_the_only_test_left_in_the_catchup_gate_decides_every_verdict():
    """The open-side comparison was removed because it was provably dead.

    `_voucher_catchup_note` used to run two tests and render the note if
    either fired: what is outstanding at the latest PASSED instalment, and
    whether the earliest STILL-OPEN one needs more than its printed share. The
    second could never fire alone. Writing X for requirement - withholding and
    E for estimates paid, federally the printed share is (X - E)/(5 - n), so
    the open side asks X*n/4 - E > (X - E)/(5 - n): at n=1 that is E < 0, at
    n=4 it is X - E > X - E, and at n=2 and n=3 it reduces to X/4 > E and
    X/2 > E — which ARE `outstanding_at(n-1) > 0`. The CA weights collapse the
    same way.

    This sweeps both schedules across nine as-of dates and asserts the removed
    comparison adds nothing, so re-adding it has to be a deliberate act with a
    counter-example in hand rather than a plausible-looking safety net. It
    fails if a future weighting makes the two genuinely diverge, which is the
    signal to bring the second test back.
    """
    dates = [datetime.date(2026, m, d) for (m, d) in
             ((1, 10), (3, 1), (4, 15), (4, 16), (6, 15), (6, 16),
              (9, 15), (9, 16), (12, 31))]
    seen = {"fed": set(), "ca": set()}
    checked = 0
    for req in (0.0, 5000.0, 12500.0, 20000.0, 41000.0, 60000.0):
        for wh in (0.0, 1234.56, 19000.0, 60000.0):
            for paid in (0.0, 300.0, 2500.0, 6000.0, 19000.0):
                for as_of in dates:
                    for tag, build, catchup in (
                            ("fed", P.compute_estimated_vouchers, P._FEDERAL_CATCHUP),
                            ("ca", P.compute_ca_estimated_vouchers, P._CA_CATCHUP)):
                        vouchers = build(max(0.0, req - wh - paid), as_of)
                        if not vouchers or not any(v.amount > 0 for v in vouchers):
                            continue
                        n = vouchers[0].quarter
                        seen[tag].add(n)
                        checked += 1

                        def outstanding(m):
                            return round((req - wh) * catchup.instalment_fraction(m)
                                         - paid, 2)

                        open_side = outstanding(n) > round(vouchers[0].amount, 2)
                        kept = n > 1 and outstanding(n - 1) > 0
                        assert not (open_side and not kept), (
                            f"{tag} at n={n}: req={req} wh={wh} paid={paid} "
                            f"as_of={as_of} — the removed open-side comparison "
                            f"fires where the one remaining test does not, so "
                            f"it was load-bearing after all and its removal "
                            f"silences a real catch-up warning")
    assert checked > 500, (
        f"only {checked} non-trivial schedules reached the comparison; the "
        f"fixture grid stopped producing them and this pins nothing")
    for tag, quarters in seen.items():
        assert quarters >= {1, 2, 3, 4}, (
            f"{tag} never reached instalments {sorted({1, 2, 3, 4} - quarters)}, "
            f"and n=2/n=3 are the only ones where the two tests coincide "
            f"rather than one being trivially false")


def test_the_federal_voucher_schedule_can_never_have_an_empty_open_list():
    """Q4 is appended unconditionally, which is why the fallback is gone.

    `compute_estimated_vouchers` carried `if not open_q: open_q = [(4, ...)]`
    after a loop that appends Q4 on every path. The CA twin states the same
    fact in a comment and never had the branch. Removing dead code is only
    safe while the premise holds, so the premise is what gets pinned: every
    day of the year yields at least one voucher, and January 15 of the
    following year is always among them.
    """
    day = datetime.date(2026, 1, 1)
    while day.year == 2026:
        for build in (P.compute_estimated_vouchers, P.compute_ca_estimated_vouchers):
            vouchers = build(20000.0, day)
            assert vouchers, (
                f"{build.__name__} returns an empty schedule at {day} — the "
                f"removed empty-list fallback was reachable after all")
            assert vouchers[-1].due_date == "2027-01-15", (
                f"{build.__name__} at {day} does not end on the Q4 instalment "
                f"the unconditional append is supposed to guarantee")
        day += datetime.timedelta(days=1)


def test_the_de_minimis_status_states_how_far_under_the_floor_it_is():
    """The floor is a cliff on a PROJECTION, so the margin is the disclosure.

    Both floors zero the shortfall, the W-4 penalty figure and the whole
    penalty voucher schedule. Reported as a bare "no penalty applies" that is
    an UNDER-statement of what the taxpayer may have to pay, which AGENTS.md
    requires to degrade loudly — and the loud part cannot be a "near the
    cliff" flag, because any threshold for near would be invented. The
    continuous quantity is carried instead: how much the projection has to be
    wrong by before the excuse evaporates.
    """
    sh = P.compute_safe_harbor(5000.0, None, None, "single",
                               withholding=4001.0, estimates_paid=0.0)
    assert sh.de_minimis and sh.covered and sh.shortfall == 0.0
    assert sh.de_minimis_margin == 1.0, (
        f"$5,000 tax against $4,001 withholding leaves $999, which is $1 "
        f"under the $1,000 floor; the margin reads {sh.de_minimis_margin}")
    status = P._federal_status(sh)
    assert "$1.00 " in status and "PROJECTION" in status, (
        f"the federal status cell states the excuse without the margin that "
        f"has to be wrong for it to vanish: {status!r}")

    far = P.compute_safe_harbor(5000.0, None, None, "single",
                                withholding=5000.0, estimates_paid=0.0)
    assert far.de_minimis_margin == 1000.0, (
        "fully withheld, the margin is the whole floor — a report that cannot "
        "tell $1 of headroom from $1,000 discloses nothing by stating it")

    over = P.compute_safe_harbor(5000.0, None, None, "single",
                                 withholding=4000.0, estimates_paid=0.0)
    assert not over.de_minimis and over.de_minimis_margin == 0.0, (
        "$1,000 exactly is NOT under the floor (§6654(e)(1) is a strict less-"
        "than), and the margin must not carry a stale value once it lapses")

    # The margin is rendered to the CENT while every other figure in these
    # reports is whole dollars, because the floor is a strict `<`: a $0.40
    # margin rounded to "$0" states the opposite of what the sentence exists
    # to say.
    thin = P.compute_safe_harbor(5000.0, None, None, "single",
                                 withholding=4000.6, estimates_paid=0.0)
    assert thin.de_minimis and thin.de_minimis_margin == 0.6
    assert "$0.60" in P._federal_status(thin), (
        f"a 60-cent margin renders as {P._federal_status(thin)!r} — rounded to "
        f"whole dollars the thinnest possible headroom reads as none at all")

    csh = P.compute_ca_safe_harbor(1000.0, None, None, 20000.0, "single", 700.0, 0.0)
    assert csh.de_minimis and csh.de_minimis_margin == 200.0
    assert "$200.00 " in P._ca_status(csh) and "PROJECTION" in P._ca_status(csh), (
        "the CA floor shipped before the federal one and went the whole time "
        "without the margin; fixing one renderer and not the other is the "
        "failure this pair exists to stop")


def test_the_prior_year_prong_uses_only_a_completed_line8_base():
    """Raw line 24 stays out even when a prior archive is available."""
    prior = _Prior({"form_1040": {"line_24_total_tax": 18400,
                                   "line_11_agi": 162000}})
    base_inputs = {
        "pay_frequency": "biweekly",
        "ytd": {"wages": 50000, "federal_withholding": 6000},
        "estimated_payments_made": {"federal": 0, "state": 0},
    }
    unresolved = _build_report(
        base_inputs, _profile_mfj(), _tax_law_2026(), prior, 2026,
        datetime.date(2026, 7, 2))
    assert unresolved.safe_harbor.degraded is True
    assert unresolved.safe_harbor.prior_tax is None
    assert unresolved.baseline_source is None
    assert "Do not substitute raw Form 1040 line 24" in \
        P.render_markdown(unresolved)

    # An explicit base WITHOUT a paired AGI must not borrow the archive's —
    # tax-amend can leave that AGI belonging to a different return than the
    # one the base was composed from — so it degrades to the flagged
    # "prior AGI unknown" 100% factor rather than guessing 110%.
    completed_inputs = dict(base_inputs)
    completed_inputs["prior_year_form_2210_tax_base"] = 14400
    completed = _build_report(
        completed_inputs, _profile_mfj(), _tax_law_2026(), prior, 2026,
        datetime.date(2026, 7, 2))
    assert completed.safe_harbor.degraded is False
    assert completed.safe_harbor.prior_tax == 14400.0
    assert completed.safe_harbor.prior_agi_missing is True
    assert completed.safe_harbor.prior_based == 14400.0, (
        "an explicit base without its paired AGI must not silently borrow "
        "the archive's, which may belong to a different (amended) return")

    # Supplying the paired AGI from the SAME return applies the 110% factor.
    paired_inputs = dict(completed_inputs)
    paired_inputs["prior_year_form_2210_agi"] = 162000
    paired = _build_report(
        paired_inputs, _profile_mfj(), _tax_law_2026(), prior, 2026,
        datetime.date(2026, 7, 2))
    assert paired.safe_harbor.prior_agi_missing is False
    assert paired.safe_harbor.prior_based == 15840.0, (
        "MFJ uses its $150,000 threshold against the explicitly paired "
        "$162,000 AGI and should apply the 110% factor to the completed base")


def test_the_ca_prior_year_note_does_not_invent_a_credit_list():
    """The CA note names the netting gap without guessing which credits it is.

    CA conforms to §6654 through R&TC §19136, so a netting question arises
    for `ca_total_tax` the same way it does for a federal prior-year base.
    WHICH CA credits FTB 5805 nets is not something this engine has verified
    against the FTB, and AGENTS.md §2 forbids printing an unverified figure
    or list. Naming the gap is honest; naming a guessed list would be the
    lying caveat the repo ranks worse than a missing one. The note must not
    cross-reference a federal note, since the federal prior-year prong only
    ever accepts a completed Form 2210 line-8 composition and so never
    renders one — `SafeHarbor` carries no `prior_tax_unnetted` at all.
    """
    csh = P.compute_ca_safe_harbor(30000.0, 12000.0, 200000.0, 200000.0,
                                   "single", 0.0, 0.0)
    assert csh.prior_tax_unnetted is True
    notes = "\n".join(P._ca_notes(csh))
    assert P._CA_PRIOR_TAX_UNNETTED_NOTE in notes
    assert "FTB 5805" in notes and "R&TC §19136" in notes
    assert "federal note" not in P._CA_PRIOR_TAX_UNNETTED_NOTE, (
        "the federal prior-year note (`_PRIOR_TAX_UNNETTED_NOTE`) was deleted "
        "— `SafeHarbor` carries no `prior_tax_unnetted` field — so "
        "cross-referencing it here points at a note that can never render")
    for federal_only in ("earned income credit", "additional child tax credit",
                         "American opportunity", "premium tax credit"):
        assert federal_only not in P._CA_PRIOR_TAX_UNNETTED_NOTE, (
            f"the CA note names {federal_only!r} — that is the FEDERAL list, "
            f"and asserting it applies to Form 540 is a claim nothing here "
            f"has checked")

    high = P.compute_ca_safe_harbor(30000.0, 12000.0, 200000.0, 2_000_000.0,
                                    "single", 0.0, 0.0)
    assert high.high_income_90_only and high.prior_tax_unnetted is False, (
        "R&TC §19136.3 removes the prior-year prong entirely above $1M, so "
        "there is nothing left for the netting note to be about")


# --- The voucher schedules' DATES ------------------------------------------
#
# The catch-up note is about the AMOUNT on a row; this one is about whether the
# DATE beside it is still ahead. Independently true — a schedule can be
# correctly split and entirely obsolete — which is why they are two notes and
# not one, and why `_voucher_dates_note` reads no safe harbor at all.


def test_the_balance_target_note_does_not_deny_a_funded_penalty_column():
    """"No PASSED penalty row is funded" is not "the penalty column is $0".

    The three balance-target notes are reached whenever no passed penalty row
    carries an amount, and all three used to assert the penalty column was $0
    outright. The early-estimate path zeroes exactly the instalments an
    estimate already covered, so a schedule reads $0 at April and stays funded
    at June, September and January: a $1,200 shortfall with $1,000 of
    estimates in by March 1 does it. On May 1 only the April balance row has
    passed, and the note told that reader the report "identifies no
    underpayment amount" with $1,200 outstanding in the table beside it — the
    UNDER-stating direction, in taxpayer-facing copy, in both reports.

    Only /tax-withholding supplies `balance_schedules`, so nothing here
    exercised this branch at all; that is how the false claim shipped green.
    """
    as_of, today = datetime.date(2026, 3, 1), datetime.date(2026, 5, 1)
    penalty = P.compute_estimated_vouchers(1200.0, as_of, 1000.0)
    balance = P.compute_estimated_vouchers(1500.0, as_of, 0.0)
    assert penalty[0].amount == 0.0 and any(v.amount > 0 for v in penalty), (
        "the fixture no longer produces the zeroed-then-funded penalty column "
        "this case is about")
    assert balance[0].amount > 0, "the passed balance row is no longer funded"

    note = P._voucher_dates_note(P._FEDERAL_CALENDAR, [penalty], 2026, today,
                                 balance_schedules=[balance])
    assert note is not None
    assert "penalty-target column is $0" not in note, (
        "the balance-target note still declares the penalty column $0 while "
        "$1,200 of it is funded on instalments not yet due")
    assert "DOES identify" in note, (
        "the note no longer tells the reader an underpayment amount exists — "
        "silence about it is the same understatement in a quieter form")

    # The counterweight: a genuinely all-zero penalty column must keep the
    # strong claim, or the fix above has merely deleted a true statement.
    empty = P.compute_estimated_vouchers(0.0, as_of, 0.0)
    cleared = P._voucher_dates_note(P._FEDERAL_CALENDAR, [empty], 2026, today,
                                    balance_schedules=[balance])
    assert "penalty-target column is $0" in cleared


def test_the_ca_balance_note_qualifies_its_federal_balance_due_date():
    """`_return_due_date` is federal; the CA note must not present it as CA's.

    It reads an IRS-wide postponement table with no FTB source, so on the CA
    side the printed balance-due date is an assumption of conformity rather
    than a looked-up state date — and an unqualified date reads as looked up.
    """
    as_of, today = datetime.date(2026, 3, 1), datetime.date(2026, 5, 1)
    balance = P.compute_ca_estimated_vouchers(1500.0, as_of, 0.0)
    empty = P.compute_ca_estimated_vouchers(0.0, as_of, 0.0)
    ca = P._voucher_dates_note(P._CA_CALENDAR, [empty], 2026, today,
                               balance_schedules=[balance])
    assert ca is not None and "FEDERAL one" in ca and "FTB" in ca, (
        "the CA note prints a federal balance-due date with nothing marking "
        "it as one")
    fed = P._voucher_dates_note(
        P._FEDERAL_CALENDAR, [P.compute_estimated_vouchers(0.0, as_of, 0.0)],
        2026, today,
        balance_schedules=[P.compute_estimated_vouchers(1500.0, as_of, 0.0)])
    assert "FEDERAL one" not in fed, (
        "the federal note carries the CA caveat, which is about a lookup that "
        "does cover it")


def test_a_deadline_gate_observes_even_a_bare_statutory_date():
    """The note remains safe if a direct caller supplies a statutory date."""
    v = [P.Voucher(4, "2023-01-15", 500.0)]
    for day, expected in ((15, None), (16, None), (17, None), (18, str)):
        note = P._voucher_dates_note(P._FEDERAL_CALENDAR, [v], 2022,
                                     datetime.date(2023, 1, day))
        if expected is None:
            assert note is None, f"2023-01-{day} is on or before the observed date"
        else:
            assert note is not None and "2023-01-15" in note


def test_the_dates_note_rolls_each_jurisdiction_on_its_own_rule():
    """Each calendar rolls a bare statutory row on its OWN jurisdiction's set.

    `passed_dates` re-normalizes every row so a caller handing it a BARE
    statutory date still gets its §7503 window (the test above). It did that
    with the FEDERAL rule for both calendars; the fix made the rule data on
    the calendar. The example that motivated it — CA April 2023 — is no longer
    a divergence, because CA turned out to adopt the federal holiday set
    outright (R&TC §18410), which is a second bug found underneath the first.

    The live case is now MLK Day, and it is the one that matters most: a CA
    row's statutory January 15 is due the 16th, so a gate that rolls weekends
    only calls it passed on the day the taxpayer is paying it. A warning that
    fires on a deadline that has NOT passed is the gate taxpayers learn to
    read past — and the same rule left the genuinely-late case unwarned.

    Federal rows are asserted alongside so a future collapse back to one
    shared rule cannot pass this again.
    """
    # A BARE STATUTORY January 15 2024 — MLK Day, and FTB's published TY2023
    # Q4 date is the 16th. This is the case Codex's report named: on the 16th,
    # the day the payment is actually due, the row must not read as passed.
    ca = [P.Voucher(4, "2024-01-15", 5000.0)]
    for day in (15, 16):
        assert P._voucher_dates_note(P._CA_CALENDAR, [ca], 2023,
                                     datetime.date(2024, 1, day)) is None, (
            f"2024-01-{day} is within the CA row's §7503 window — the "
            "deadline is the 16th, not the 15th")
    note = P._voucher_dates_note(P._CA_CALENDAR, [ca], 2023,
                                 datetime.date(2024, 1, 17))
    assert note is not None and "2024-01-15" in note

    fed = [P.Voucher(1, "2023-04-18", 5000.0)]
    assert P._voucher_dates_note(P._FEDERAL_CALENDAR, [fed], 2023,
                                 datetime.date(2023, 4, 18)) is None
    assert P._voucher_dates_note(P._FEDERAL_CALENDAR, [fed], 2023,
                                 datetime.date(2023, 4, 19)) is not None

    # The normalizer is per-calendar DATA, not a branch on the form name.
    assert P._FEDERAL_CALENDAR.observed_due is P._observed_due
    assert P._CA_CALENDAR.observed_due is P._state_observed_due
    # CA and the federal rule agree on every date, because CA adopts the
    # federal SET (R&TC §18410) — they stay separate because the choice of set
    # belongs to the jurisdiction, and a state outside the conforming set
    # still diverges. That divergence is what keeps this pair meaningful.
    assert P._state_observed_due(datetime.date(2024, 1, 15)) == \
        P._observed_due(datetime.date(2024, 1, 15)) == datetime.date(2024, 1, 16)
    assert I.next_business_day_state(datetime.date(2024, 1, 15), "NJ") == \
        datetime.date(2024, 1, 15), (
        "an unread state still rolls weekends only; if this ever matches CA, "
        "the conforming set has silently swallowed every jurisdiction")


def test_the_prior_prong_discloses_its_own_statutory_precondition():
    """§6654(d)(1)(B)'s closing text is a condition of the PRONG, not of $0.

    "Clause (ii) shall not apply if the preceding taxable year was not a
    taxable year of 12 months or if the individual did not file a return for
    such preceding taxable year." The engine gates the confirmation flags only
    at a $0 base, where the prong would zero the requirement outright. A
    POSITIVE base is accepted as supplied — the flags default false in every
    template, and refusing the ordinary filed-a-normal-return case is the gate
    that gets reasoned past — so the precondition is DISCLOSED beside the row
    instead of enforced.

    That is the under-statement direction, the one this module must not take
    silently: 100%/110% of a small prior-year tax, for someone whose first
    filing year was short or who never filed, sits far below the 90% of the
    current year that is the only prong they actually have.

    Gated on the prong being SHOWN. In degraded mode there is no prior-year
    row for the sentence to qualify, and a caveat about a figure that is not
    on screen is the noise that gets the live ones skimmed past. Both
    renderers carry it — /tax-project and /tax-withholding print the same row
    off the same object, and a caveat on one of them is a caveat with a hole.
    """
    marker = "assumes a filed, full 12-month prior year"

    shown = _render_at(datetime.date(2026, 6, 30))
    assert "of prior-year tax |" in shown, (
        "fixture drift: this report must SHOW a prior-year row, or the test "
        "proves nothing")
    assert marker in shown and "6654(d)(1)(B)" in shown, (
        "a report showing a prior-year row must carry the prong's own "
        "statutory precondition")

    # Degraded: no base supplied, so no prior-year row and nothing to qualify.
    degraded = P.render_markdown(_build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 2000},
                "estimated_payments_made": {"federal": 0}},
        profile=_profile_mfj(), tax_law=_tax_law_2026(), prior=None,
        year=2026, as_of=datetime.date(2026, 6, 30)))
    assert "of prior-year tax |" not in degraded, (
        "fixture drift: the degraded report must not render a prior-year row")
    assert marker not in degraded, (
        "no prior-year row is shown in degraded mode, so the caveat has "
        "nothing to qualify and is pure noise")


def test_the_prong_precondition_note_stops_at_a_confirmed_zero_base():
    """The caveat above must not fire on the one prong that DISPROVES it.

    §6654(e)(2) excuses the penalty outright when the prior year was 12 months,
    carried no liability, and the taxpayer was a US citizen or resident
    throughout it. Filing a return is NOT among those three — Form 2210's
    instructions say the prior year need only have been 12 months "or would
    have been had you been required to file", and someone under last year's
    filing threshold is the ordinary case. Printing "no return was filed for
    it ... would then be too LOW" beside that taxpayer's correct $0 target
    contradicts the result on the same page, and the remedy it points at is
    90% of the current year: money §6654(e)(2) says they do not owe.

    A $0 base reaches a report ONLY through a confirmation — `_prior_tax_agi`
    refuses an unconfirmed one — so both routes to it have already asked and
    answered the precondition question. Both are pinned, because the note is
    equally untrue of each, and a positive control rides along: suppressing the
    caveat everywhere would satisfy the two negatives on its own.
    """
    marker = "assumes a filed, full 12-month prior year"

    def render(**flags):
        return P.render_markdown(_build_report(
            inputs={"pay_frequency": "biweekly",
                    "prior_year_form_2210_tax_base": 0,
                    "ytd": {"wages": 50000, "federal_withholding": 2000},
                    "estimated_payments_made": {"federal": 0}, **flags},
            profile=_profile_mfj(), tax_law=_tax_law_2026(), prior=None,
            year=2026, as_of=datetime.date(2026, 6, 30)))

    for flag, statute in (
            ("prior_year_zero_liability_confirmed", "§6654(e)(2)"),
            ("prior_year_return_filed_12_months_confirmed",
             "§6654(d)(1)(B)(ii) at a $0 base")):
        md = render(**{flag: True})
        assert "of prior-year tax |" in md, (
            f"fixture drift: {flag} must leave the prong RESOLVED, or the "
            "suppression below is just degraded mode")
        assert marker not in md, (
            f"{statute} carries no unverified filed-return condition, so the "
            f"caveat is false beside a $0 target confirmed via {flag}")

    # Positive control: the case the caveat IS true of, and the only one
    # nothing verifies.
    assert marker in _render_at(datetime.date(2026, 6, 30)), (
        "a POSITIVE base is taken as supplied and must still carry the "
        "precondition — suppressing it everywhere passes the two cases above")

    # The premise the suppression rests on: $0 is never reached UNconfirmed.
    with pytest.raises(P.ProjectionError):
        render()


def test_no_skill_demands_a_filed_return_for_a_zero_prior_year_base():
    """The intake instruction must not out-scope the statute it cites.

    §6654(d)(1)(B)'s closing text withdraws the ORDINARY prior-year prong when
    the preceding year was short or unfiled, and that prong is the only route
    to a POSITIVE base — §6654(e)(2) is a no-LIABILITY exception and cannot
    produce one. Extending the demand to EVERY base, as both workspace skills
    briefly did, reaches the one taxpayer the statute exempts outright: no
    liability last year, 12 months, citizen or resident throughout, and no
    return because none was required. Told to leave the base null, both CLIs
    fall back to 90% of the current year and bill someone §6654(e)(2) says
    owes nothing at all.

    `tax-safe-harbor` has carried the carve-out since it shipped — "it does
    **not** require that a return was filed, so run it BEFORE the filing
    question below" — and `_prior_tax_agi` splits the two flags for the same
    reason. This pins all three together: the engine, the engine-free skill
    and the two intake skills disagreeing about one taxpayer is what the
    instruction shipped as.
    """
    for skill in ("tax-project", "tax-withholding", "tax-safe-harbor"):
        # Whitespace-normalized and markdown-stripped: all three wrap the
        # sentence mid-phrase and one bolds the "not", so a raw substring
        # search reports absence for a paragraph that is present and correct.
        flat = " ".join(skill_text(skill).replace("**", "").split())
        assert "not require that a return was filed" in flat, (
            f"{skill} must state that §6654(e)(2) needs no filed return — it "
            "is the whole difference between the two routes to a $0 base, and "
            "the ordinary case is someone under last year's filing threshold")
        assert "storing ANY base, positive or zero" not in flat, (
            f"{skill} demands a filed return for EVERY base, which denies "
            "§6654(e)(2) to the taxpayer it exists for and bills them 90% of "
            "the current year")


def test_no_voucher_row_is_ever_negative():
    """A sub-cent-per-quarter shortfall drove Q4 to -$0.01, in both schedules.

    `_split_remainder_last` gives the LAST positive-weight entry whatever is
    left after the earlier shares, and each earlier share rounds
    independently. On a shortfall smaller than a cent per open quarter every
    one of them rounds UP, their sum overshoots, and "whatever is left" goes
    negative: `_split_remainder_last(0.02, [1, 1, 1, 1])` returned
    `[0.01, 0.01, 0.01, -0.01]` and both report paths printed a negative
    voucher. The de minimis floor does not stop it — that floor tests the
    BALANCE (tax less withholding) against $1,000, not the shortfall, so a
    two-cent gap sits behind a five-figure balance quite legitimately.

    Swept rather than spot-checked: the failure lives in the interaction
    between the cent grid and the number of open quarters, so the cases that
    break are exactly the ones nobody thinks to write down.
    """
    for cents in range(0, 40):
        shortfall = round(cents / 100, 2)
        for weights in ([1.0], [1.0] * 2, [1.0] * 3, [1.0] * 4,
                        [0.30, 0.40, 0.00, 0.30], [0.40, 0.00, 0.30],
                        [0.00, 0.30], [0.30]):
            shares = P._split_remainder_last(shortfall, weights)
            assert min(shares) >= 0, (
                f"negative share splitting {shortfall} across {weights}: "
                f"{shares}")
            assert round(sum(shares), 2) == shortfall, (
                f"{shares} does not sum to {shortfall} — the exact-sum "
                "contract is what the remainder-on-the-last design is FOR")

    # The regression case by name, and the ordinary splits it must not have
    # disturbed: the cap only bites where the rounding overshoots.
    assert P._split_remainder_last(0.02, [1, 1, 1, 1]) == [0.01, 0.01, 0.0, 0.0]
    assert P._split_remainder_last(100.0, [1, 1, 1]) == [33.33, 33.33, 33.34]
    assert P._split_remainder_last(1000.0, [0.30, 0.40, 0.00, 0.30]) == \
        [300.0, 400.0, 0.0, 300.0]

    # End to end, through both builders — the helper is not what a reader of
    # the report sees.
    for build in (P.compute_estimated_vouchers, P.compute_ca_estimated_vouchers):
        for cents in range(0, 12):
            vouchers = build(round(cents / 100, 2), datetime.date(2026, 5, 1))
            assert min(v.amount for v in vouchers) >= 0, (
                f"{build.__name__} emitted a negative voucher at "
                f"${cents / 100:.2f}: {[v.amount for v in vouchers]}")


def test_the_line4_note_names_every_credit_form_2210_line_3_does():
    """The enumeration is Form 2210's, and it is not a closed set.

    Line 3's own instruction lists the refundable adoption credit (Form 8839
    line 13) alongside the EIC, ACTC, refundable AOTC, net PTC and fuel
    credit, and OBBBA made part of the adoption credit refundable for tax
    years beginning after 2024. Omitting it while closing with "if this year's
    return claims none of them, the figures above are already correct" told a
    filer whose only refundable credit is that one that an OVERSTATED required
    payment was correct — and the same branch's Form 2210 input instructions
    already require subtracting it.

    The amount is deliberately absent: the refundable portion is INDEXED, and
    quoting an indexed cap in prose is what the commit before this branch's
    first review round removed.
    """
    note = P._CURRENT_TAX_UNNETTED_NOTE
    assert "adoption credit" in note, (
        "Form 2210 line 3 enumerates the refundable adoption credit; a filer "
        "claiming only that one is told an overstated figure is correct")
    # The condition must ride WITH the enumeration entry, not merely appear
    # somewhere in the note: the closing sentence mentions the year too, so a
    # bare `"after 2024" in note` stays green with the entry unconditional.
    entry = note[note.index("adoption credit"):]
    entry = entry[:entry.index(")") + 1]
    assert "8839" in entry, "name the form line, or the reader cannot check it"
    assert "after 2024" in entry, (
        "the credit is refundable only for tax years beginning after 2024, "
        f"and the entry itself must say so — got {entry!r}. Unconditional, it "
        "sends a TY2024 filer subtracting a nonrefundable credit and "
        "UNDERSTATING the requirement, which is the direction that ends in a "
        "penalty")
    for indexed in ("$5,000", "$17,280"):
        assert indexed not in note, (
            f"{indexed} is INDEXED and goes stale in prose (AGENTS.md #2)")
    assert "not closed" in note or "is not a closed" in note, (
        "the list is illustrative; without saying so, its closing sentence "
        "reads as an exhaustive checklist and the next credit added to line 3 "
        "repeats this exactly")


def test_the_template_names_both_routes_to_a_zero_base():
    """The base field's comment named one of the two flags beside it.

    `_prior_tax_agi` takes EITHER `prior_year_zero_liability_confirmed`
    (§6654(e)(2)) or `prior_year_return_filed_12_months_confirmed`
    (§6654(d)(1)(B)'s closing text), and the two flags' own comments say so.
    The tax-base comment still said a $0 base "requires
    prior_year_zero_liability_confirmed" — so an agent reading the field it is
    filling in asks only the citizenship question, and a taxpayer who filed a
    full 12-month $0 return but cannot affirm citizenship or residency
    throughout the year is refused a prong they have. The base is then left
    null and the report demands 90% of the current year.
    """
    tmpl = json.loads((Path(__file__).resolve().parent.parent /
                       "agents/templates/projection_inputs_template.json"
                       ).read_text())
    comment = tmpl["_comment_prior_year_form_2210_tax_base"]
    for flag in ("prior_year_zero_liability_confirmed",
                 "prior_year_return_filed_12_months_confirmed"):
        assert flag in comment, (
            f"the tax-base comment does not name {flag}, so an agent filling "
            "in the base asks about only one of the two independent routes")
    assert "EITHER" in comment, (
        "naming both is not enough — they must read as alternatives, or the "
        "agent demands both and refuses a taxpayer who has one")


def test_every_statement_of_6654e2_names_the_no_liability_condition():
    """(e)(2)(B) is the condition a $0 base does NOT establish, and six copies dropped it.

    §6654(e)(2) has three: a 12-month preceding year, NO LIABILITY for it, and
    US citizenship or residence throughout. Six places told a reader to confirm
    the first and third and called that the whole test — the two skills that
    populate the flag, the contract /tax-compute passes it through, the
    template comment on the flag itself, the engine's own refusal message, and
    tax-safe-harbor's precondition bullet. Each of them reached the subject
    only when the Form 2210 line-8 base came out at $0, so the base was
    standing in for the liability.

    It does not stand in for it. Line 8 SUBTRACTS the refundable credits by
    name; Form 1040's total-tax line sits above them. A prior year with $500 of
    SE tax and $2,000 of EIC has a $0 base and a $500 total tax, so (e)(2)(B)
    fails on it. Where a return WAS filed that costs nothing — the ordinary
    prong pays 100% of $0 — but §6654(d)(1)(B)'s closing text withdraws that
    prong on an unfiled or short prior year, and there the mis-set flag is the
    only thing zeroing the required annual payment. The taxpayer owes 90% of
    the current year and is told they owe nothing: silent, and in the
    understating direction AGENTS.md ranks as the hard error.

    Documentation is the whole available lever — nothing here can see a line 24
    — and the flag was already NAMED for the liability. So no fourth flag: the
    fix is that every place asking for it now asks for what it is named after.
    """
    repo = Path(__file__).resolve().parent.parent
    sites = {
        "projection_inputs_template.json":
            (repo / "agents/templates/projection_inputs_template.json").read_text(),
        "projection.py _prior_tax_agi refusal":
            inspect.getsource(P._prior_tax_agi),
        "tax-compute references/tax-and-credits.md":
            (repo / ".agents/skills/tax-compute/references"
                    "/tax-and-credits.md").read_text(),
        "tax_computations.py compute_form_2210 $0 branches":
            inspect.getsource(TC.compute_form_2210),
        "tax-withholding": skill_text("tax-withholding"),
        "tax-project": skill_text("tax-project"),
        # SKILL.md alone, not `skill_text`: the defect is in the precondition
        # bullet an agent reads before it decides whether to open a reference
        # at all, and references/no-return-year.md already states the same
        # distinction correctly. Reading both together lets the reference
        # answer for the body, which is exactly how the body stayed wrong
        # through the round that WROTE the reference.
        "tax-safe-harbor SKILL.md":
            (repo / ".agents/skills/tax-safe-harbor/SKILL.md").read_text(),
    }
    for name, text in sites.items():
        flat = " ".join(text.split())
        hits = [m.start() for m in re.finditer(r"6654\(e\)\(2\)", flat)]
        assert hits, f"{name} no longer states the exception at all"
        # Windowed on the CITATION, not on the whole document: a stray "no
        # liability" three sections away does not tell the reader confirming
        # this flag anything, and matching the whole text would let one.
        windows = [flat[max(0, i - 900):i + 900] for i in hits]
        # CASE-SENSITIVE, and that is the point: every one of these already
        # contained "no liability" somewhere in lower case, as narration. What
        # none of them contained was the condition stated as a thing to
        # CONFIRM. Matching case-insensitively passed all six unchanged.
        assert any("NO LIABILITY" in w for w in windows), (
            f"{name} states §6654(e)(2) without its no-LIABILITY condition "
            f"beside it, as a condition. The 12-month and citizenship halves "
            f"are the two a $0 base does not bear on; the one it silently "
            f"stands in for is the one missing")
        assert any("NO LIABILITY" in w and "TOTAL TAX line" in w
                   for w in windows), (
            f"{name} names the liability condition without saying what it "
            f"reads off. Form 1040's TOTAL TAX line sits ABOVE the refundable "
            f"credits that Form 2210 line 8 subtracts, so a $0 base and a $0 "
            f"liability are different facts and the reader has to be told "
            f"which one is being asked for")

    # And the design note the six inherited it from. It read "the netted
    # figure is $0, which is §6654(e)(2)" — the substitution itself, written
    # down as a rationale, which is why correcting the instruction sites alone
    # would leave the reasoning that regenerates them.
    catalog = (repo / "docs/contributing/skill-catalog.md").read_text()
    assert "which is §6654(e)(2) — no penalty can attach at all" not in catalog, (
        "the skill catalogue still equates a netted $0 with §6654(e)(2). That "
        "sentence is where the six instruction sites came from")


def test_the_engine_refuses_a_zero_base_naming_all_three_conditions():
    """The refusal message is the only one of the six a user reads under load.

    It fires exactly when someone has typed a $0 base and neither flag, which
    is the moment the three conditions decide the answer. It enumerated two of
    them, so a reader confirming what it asked for set the flag on a prior year
    that had liability — and the message is what they were confirming against.
    """
    with pytest.raises(P.ProjectionError) as e:
        P._prior_tax_agi(0)
    msg = " ".join(str(e.value).split())
    assert "NO LIABILITY" in msg and "TOTAL TAX" in msg, (
        f"the $0-base refusal does not say what §6654(e)(2)'s liability "
        f"condition reads off: {msg}")
    assert "never this base" in msg, (
        "the refusal does not rule the base itself OUT as the liability "
        "figure, which is the substitution the message exists to stop")
    assert "prior_year_return_filed_12_months_confirmed" in msg, (
        "the second, independent route out of a $0 base is unnamed, so a "
        "taxpayer who filed a 12-month $0 return is refused a prong they hold")


def test_the_skill_sizes_withholding_against_a_passed_instalment():
    """Route A's figure is the YEAR's gap, which can be $0 while April accrues.

    Withholding credits ratably (§6654(g)(1)), so raising it now also raises
    what counts at every deadline already passed — the skill's own step 4 says
    so. But route A sized 4(c) from `still to pay` alone, and a late estimated
    payment that meets the year's total while leaving April short makes that
    figure zero. The skill then answered $0 to the one question it exists to
    answer, and its timing check went on to report a shortfall as one nothing
    undoes, when a larger 4(c) undoes it exactly.

    Pinned on the SKILL TEXT including its references, since the detail lives
    one hop out: `skill_text` reads both, which is what stops a fix from being
    "solved" by moving the paragraph somewhere nothing links.
    """
    flat = " ".join(skill_text("tax-safe-harbor").split())
    assert "cumulative fraction at n" in flat, (
        "route A carries no second figure — 4(c) sized from the annual gap "
        "alone answers $0 to a taxpayer with an instalment still accruing")
    assert "still to pay is not the whole of what withholding can fix" in         flat.replace("`", ""), (
            "the body must SAY the annual gap is incomplete; a formula "
            "reachable only by reading the reference is not reached")
    assert "references/withholding-cure.md" in skill_text("tax-safe-harbor"), (
        "the detail must be linked from SKILL.md — an unlinked reference is "
        "text the agent never opens")
    # The verdict that contradicted it.
    assert "recomputation, say so: penalty has accrued and nothing undoes it" \
        not in flat, (
            "the timing check still pronounces a surviving shortfall "
            "incurable without re-sizing route A against it first")


def test_the_year_guard_never_echoes_a_malformed_tax_year(tmp_path, capsys):
    """A refusal about a hand-edited field must not print the field.

    projection_inputs.json is edited by hand, so a misplaced name, address or
    whole nested object lands in `tax_year` as readily as a wrong year does,
    and this refusal is printed to the terminal. `redact` is not the backstop:
    it masks SSN and credential SHAPES by design, and a taxpayer's name has
    neither (AGENTS.md #1). `_canonical_status` settled the same trade for
    filing_status — report the domain and the value's shape, never content.

    A non-int was refused before this, but by the year COMPARISON, which made
    nonsense of it: the string "2026" is `!= 2026`, so a file that was for the
    right year was turned away as "for TY2026, not TY2026".
    """
    base = {"pay_frequency": "biweekly",
            "ytd": {"wages": 100000, "federal_withholding": 12000},
            "estimated_payments_made": {"federal": 0}}
    secret = "Jane Q Taxpayer, 12 Elm St"
    for bad in (secret, {"name": secret}, [secret], 2026.0, True):
        inputs = dict(base, tax_year=bad)
        rc = P._main(_cli_workspace(tmp_path, inputs) + ["--year", "2026"])
        assert rc == 1, f"tax_year={type(bad).__name__} must refuse"
        err = capsys.readouterr().err
        assert "Jane" not in err and "Elm" not in err, (
            f"the refusal echoed the field it must not print: {err!r}")
        assert "tax_year" in err and "2026" in err, (
            "shape-only is not content-free — the message still has to name "
            f"the field and the year expected: {err!r}")

    # An integer year is safe to print, and printing it is the whole message.
    inputs = dict(base, tax_year=2025)
    assert P._main(_cli_workspace(tmp_path, inputs) + ["--year", "2026"]) == 1
    assert "TY2025" in capsys.readouterr().err

    # But int-ness is not year-ness, and the MISMATCH branch is where that
    # mattered: an account number hand-edited into the field passes the type
    # check above, falls through to `declared != year`, and was interpolated
    # there in full. `redact` masks a nine-digit run and nothing shorter, so
    # eight digits reached the terminal intact.
    inputs = dict(base, tax_year=87654321)
    assert P._main(_cli_workspace(tmp_path, inputs) + ["--year", "2026"]) == 1
    err = capsys.readouterr().err
    assert "87654321" not in err, (
        f"the year-mismatch refusal printed a non-year integer: {err!r}")
    assert "8 digits" in err and "TY2026" in err, (
        f"shape-only still has to say what was expected: {err!r}")


def test_the_accrual_cutoff_stays_on_statutory_april_15():
    """§6654(b)(2)'s endpoint does not roll with installment deadlines."""
    assert P.accrual_period_closed(2022, datetime.date(2023, 4, 15)) is True
    assert P.accrual_period_closed(2022, datetime.date(2023, 4, 16)) is True
    assert P.accrual_period_closed(2022, datetime.date(2023, 4, 18)) is True
    note = P._voucher_dates_note(
        P._FEDERAL_CALENDAR,
        [[P.Voucher(4, "2023-01-15", 500.0)]], 2022,
        datetime.date(2023, 4, 15))
    assert "closed on 2023-04-15" in note
    assert "not due until 2023-04-18" in note, (
        "the statutory penalty cutoff and the observed balance-due deadline "
        "are distinct; rolling the latter must not roll the former")
    assert P.accrual_period_closed(2026, None) is False, (
        "a report built without a date must not assert the alarming half"
    )


def test_a_rolled_april_installment_stays_open_until_its_observed_date():
    """TY2023 Q1 printed April 15 and BOTH jurisdictions observed April 18.

    April 15 2023 was a Saturday; Monday the 17th was observed DC Emancipation
    Day; the federal date became Tuesday April 18. This test used to assert
    that California's stayed Monday the 17th, on the premise that a state does
    not observe a DC holiday. California does: R&TC §18410 makes a legal
    holiday include "a federal legal holiday recognized by the Internal
    Revenue Service under Section 7503", and FTB's own 540-ES instructions say
    payments "submitted on April 18, 2023, will be considered timely" for
    exactly this reason. The old expectation published a CA payment date a day
    EARLY and, once the passed-deadline gate read the same rule, reported that
    row as missed on the day it was actually due.

    The two schedules therefore agree on this date now. They are still built
    by different functions on different weightings, so both are asserted.
    """
    harbor = _harbor(8000.0, 0.0, 0.0)
    for build, catchup in ((P.compute_estimated_vouchers, P._FEDERAL_CATCHUP),
                           (P.compute_ca_estimated_vouchers, P._CA_CATCHUP)):
        for as_of in (datetime.date(2023, 4, 16), datetime.date(2023, 4, 18)):
            vouchers = build(8000.0, as_of)
            assert vouchers[0].quarter == 1
            assert vouchers[0].due_date == "2023-04-18"
            assert P._voucher_catchup_note(
                catchup, vouchers, harbor, as_of) is None

        late = datetime.date(2023, 4, 19)
        vouchers = build(8000.0, late)
        assert vouchers[0].quarter == 2
        assert P._voucher_catchup_note(catchup, vouchers, harbor, late)

    # The two rules are still SEPARATE functions, and a state outside the
    # conforming set still rolls weekends only. Without this a collapse back
    # to one shared rule would look identical to correct behaviour.
    assert I.next_business_day_state(datetime.date(2023, 4, 15), "CA") == \
        datetime.date(2023, 4, 18)
    assert I.next_business_day_state(datetime.date(2023, 4, 15), "NY") == \
        datetime.date(2023, 4, 17)


def test_voucher_rows_serialize_observed_section_7503_deadlines():
    """The user-facing row must agree with the deadline gate's calendar.

    TY2022 Q4 was Sunday January 15 2023. Federal rolls to Monday the 16th,
    which is MLK Day, and lands on Tuesday the 17th. This test used to expect
    CA on Monday the 16th, under the weekend-only rule, defended as the safe
    direction because publishing a payment date EARLY can never make a payment
    late. Both halves of that were wrong for California: R&TC §18410 adopts
    the federal holiday set, so CA's date is Tuesday the 17th too, and an
    early date stopped being harmless once a gate started reporting the row as
    passed on the day it was due. FTB's TY2023 quarters make the same point
    one year on — its Q4 is January 16 2024, MLK Day again.
    """
    as_of = datetime.date(2022, 12, 31)
    for build in (P.compute_estimated_vouchers, P.compute_ca_estimated_vouchers):
        vouchers = build(500.0, as_of)
        assert vouchers[0].quarter == 4
        assert vouchers[0].due_date == "2023-01-17"

    # FTB's published TY2023 Q4 date, which is the one Codex's report named.
    ca = P.compute_ca_estimated_vouchers(500.0, datetime.date(2023, 12, 31))
    assert ca[0].quarter == 4 and ca[0].due_date == "2024-01-16", (
        "January 15 2024 was MLK Day; FTB publishes January 16, and a "
        "weekend-only roll leaves the Monday alone")


def test_the_dates_note_is_gated_on_money_not_merely_on_a_passed_date():
    zeros = [P.Voucher(4, "2026-01-15", 0.0)]
    assert P._voucher_dates_note(P._FEDERAL_CALENDAR, [zeros], 2025,
                                 datetime.date(2026, 3, 1)) is None
    funded = [P.Voucher(4, "2026-01-15", 1.0)]
    assert P._voucher_dates_note(P._FEDERAL_CALENDAR, [funded], 2025,
                                 datetime.date(2026, 3, 1)) is not None


def test_the_note_inverts_once_the_accrual_period_has_closed():
    v = [P.Voucher(4, "2026-01-15", 900.0)]
    open_note = P._voucher_dates_note(P._FEDERAL_CALENDAR, [v], 2025,
                                      datetime.date(2026, 3, 1))
    closed = P._voucher_dates_note(P._FEDERAL_CALENDAR, [v], 2025,
                                   datetime.date(2026, 6, 1))
    assert "pay NOW" in open_note and "obsolete" not in open_note
    assert "obsolete" in closed and "FIXED" in closed
    assert "Do NOT send a 1040-ES voucher for TY2025" in closed
    assert "BALANCE-DUE target for TY2025" in closed
    assert "Read this BEFORE any schedule note below it" in closed, (
        "the catch-up note printed below still says an unpaid instalment keeps "
        "accruing — which stopped on the cutoff this note names"
    )


def test_the_fixed_penalty_verdict_is_qualified_on_every_closed_year():
    """A table of NATIONWIDE postponements cannot clear an individual one.

    `_RETURN_DUE_POSTPONED` holds the two IRS-wide COVID postponements and
    nothing else, and the §7508A caveat used to be keyed on membership in it.
    So for every other year the note read that table's silence as "not
    postponed" when the only thing it established was "not in this table".
    §7508A relief is overwhelmingly the other kind — a declaration covering
    named counties, dozens of notices a year, postponing estimated-tax
    instalments along with the return — and §7508A(a)(2) disregards the
    postponed period in determining "the amount of any interest, penalty,
    additional amount, or addition to the tax", which is what §6654(a) adds.
    Nothing reaching this function says whether the taxpayer was inside one,
    so the strongest claim either report makes went out unqualified: a
    taxpayer who reads "no payment made now reduces it by a cent" and waits
    goes on accruing a penalty they were told was already settled.
    """
    v = [P.Voucher(4, "2026-01-15", 900.0)]
    note = P._voucher_dates_note(P._FEDERAL_CALENDAR, [v], 2025,
                                 datetime.date(2026, 6, 1))
    assert "§7508A" in note, (
        "TY2025's absence from the nationwide table is not the taxpayer's "
        "absence from a disaster area, and this note calls the penalty FIXED"
    )
    assert "NOT KNOWN here" in note, (
        "the caveat states an absence of relief rather than an absence of "
        "knowledge — the first is a verdict this engine has no input for"
    )
    assert "may still shorten it" in note, (
        "the doubt is named without what it costs to act on the verdict"
    )

    # Adjacent to the sentence it qualifies, not at the end of a note this
    # long. A reader who stops at "FIXED ... by a cent" must not have stopped
    # before the qualification: that sentence is the one they act on.
    end = note.index("no payment made now reduces it by a cent.")
    end += len("no payment made now reduces it by a cent.")
    assert note[end:end + 3] == " **", (
        "the qualification no longer follows the claim immediately; it was "
        f"moved to {note.index('§7508A') - end} characters further on"
    )


def test_the_nationwide_table_picks_the_lead_not_whether_to_warn():
    """What the table still decides, and what it no longer decides."""
    nationwide = P._voucher_dates_note(
        P._FEDERAL_CALENDAR, [[P.Voucher(4, "2021-01-15", 900.0)]], 2020,
        datetime.date(2021, 6, 1))
    assert "NATIONWIDE" in nationwide and "2021-05-17" in nationwide, (
        "the one year the engine DOES hold a postponement date for lost it — "
        "naming Notice 2021-21's date is the table's whole remaining job"
    )
    assert "NOT KNOWN here" not in nationwide, (
        "a year the engine holds the answer for is told the answer is unknown"
    )

    ordinary = P._voucher_dates_note(
        P._FEDERAL_CALENDAR, [[P.Voucher(4, "2026-01-15", 900.0)]], 2025,
        datetime.date(2026, 6, 1))
    assert "NATIONWIDE" not in ordinary and "2021-05-17" not in ordinary

    # The half that is actually about §6654(b)(2) exists ONCE. A later round
    # may resolve whether a postponement reaches its endpoint, and resolving
    # that in one of two copies is how the other goes on hedging for ever.
    shared = "if that reaches §6654(b)(2), the period closed later than"
    assert shared in nationwide and shared in ordinary


def test_a_postponed_instalment_is_not_told_the_schedule_is_spent():
    """Round fifteen: the caveat hedged the penalty and not the voucher.

    The round before widened the §7508A caveat past the two nationwide years,
    so the FIXED-penalty verdict is now qualified on every closed year. The
    two sentences after it were not. "This schedule is obsolete" and "Do NOT
    send a 1040-ES voucher ... its instalments belong to dates that are all
    behind us" went out flat, and for the one taxpayer the caveat exists for
    they are false.

    False for a reason that is NOT the caveat's open question, which is why
    hedging one sentence did not hedge the next. Whether §7508A(a)(2) reaches
    §6654(b)(2)'s accrual endpoint is unresolved here and the caveat says so.
    Whether §7508A moves the INSTALMENT is not unresolved at all:
    §7508A(a)(1) disregards the period in determining whether the acts in
    §7508(a)(1) were performed in time, and §7508(a)(1)(B) is "payment of any
    income ... tax or any installment thereof". A covered instalment is
    payable on its postponed date and paying it then is TIMELY. Telling that
    taxpayer the schedule is spent costs them the whole §6654 charge on the
    one instalment still free to fix — the report's own advice, in the
    direction AGENTS.md forbids guessing in.
    """
    v = [P.Voucher(4, "2026-01-15", 900.0)]
    closed = P._voucher_dates_note(P._FEDERAL_CALENDAR, [v], 2025,
                                   datetime.date(2026, 6, 1))
    assert "§7508(a)(1)(B)" in closed, (
        "the caveat hedges only the accrual cutoff. The instalment DATE is "
        "the half that needs no hedging to be wrong — it is postponed "
        "outright, and the sentences below still say otherwise")
    assert "still OPEN" in closed and "timely rather than late" in closed, (
        "the doubt is named without the fact it turns on: a postponed "
        "instalment is not late, so the voucher is still the instrument")

    # Leading, not trailing. Round fourteen's lesson in the sibling skill: an
    # agent reads an instruction as an instruction and stops, so a
    # qualification that arrives after the imperative is never reached.
    instruction = closed.index("Do NOT send a 1040-ES voucher")
    condition = closed.index("Where no such postponement reached one of them")
    assert condition < instruction, (
        "the voucher-suppression instruction is categorical again and its "
        f"condition trails it by {condition - instruction} characters")

    assert "obsolete on the STATUTORY calendar" in closed, (
        "the header asserts obsolescence with no calendar named, and the "
        "header is the line a summarising agent lifts whole. Every date this "
        "function holds is statutory; the §7508A one it cannot see is not")

    # The scoping half, and it is deliberate — see `_voucher_dates_note`'s
    # docstring. A postponement makes every printed date EARLIER than the one
    # that governs, so the other branches over-state urgency, which is the
    # direction this file may err in. A disaster caveat on advice that is
    # already safe is the cry-wolf shape, and this reader has several notes.
    open_note = P._voucher_dates_note(P._FEDERAL_CALENDAR, [v], 2025,
                                      datetime.date(2026, 3, 1))
    assert "pay NOW" in open_note and "§7508A" not in open_note, (
        "the open branch says pay NOW about an instalment whose real deadline "
        "a postponement can only push LATER — hedging that tells a taxpayer "
        "to wait, which is the failure this whole class is about")
    balance = P._voucher_dates_note(
        P._FEDERAL_CALENDAR, [[P.Voucher(4, "2026-01-15", 0.0)]], 2025,
        datetime.date(2026, 6, 1),
        balance_schedules=[[P.Voucher(4, "2026-01-15", 900.0)]])
    assert balance and "§7508A" not in balance, (
        "the balance branches name the unextended federal due date, no later "
        "than any postponed one, about a target that is optional anyway")

    # And the design note that records which half was the doubt, so a later
    # round resolving §6654(b)(2) cannot quietly take the instalment half
    # with it — they are two statements and only one of them is open.
    catalog = re.sub(r"\s+", " ", (
        REPO / "docs" / "contributing" / "skill-catalog.md").read_text())
    assert "only one of them is a doubt" in catalog, (
        "the note behind this fix is gone, and the next reader sees a caveat "
        "hedging two claims with no record that they are not equally hedged")


def test_each_jurisdiction_is_explained_in_its_own_instrument():
    v = [P.Voucher(4, "2026-01-15", 900.0)]
    today = datetime.date(2026, 6, 1)
    fed = P._voucher_dates_note(P._FEDERAL_CALENDAR, [v], 2025, today)
    ca = P._voucher_dates_note(P._CA_CALENDAR, [v], 2025, today)
    assert "1040-ES" in fed and "540-ES" not in fed
    assert "540-ES" in ca and "1040-ES" not in ca
    assert "FTB 5805" in ca and "§6654(b)(2)" in fed


def test_the_note_reaches_the_markdown_the_payload_and_the_ca_schedule():
    r = _report_at(datetime.date(2026, 12, 31), withholding=200,
                   state_withholding=100, today=datetime.date(2027, 6, 1))
    assert r.voucher_dates_note and r.ca_voucher_dates_note
    md = P.render_markdown(r)
    assert r.voucher_dates_note in md and r.ca_voucher_dates_note in md
    payload = P._report_to_dict(r)
    assert payload["voucher_dates_note"] == r.voucher_dates_note
    assert payload["ca_voucher_dates_note"] == r.ca_voucher_dates_note, (
        "the caveat does not reach --json, which /tax-project reads FIRST"
    )
    if r.voucher_catchup_note:
        assert md.index(r.voucher_dates_note) < md.index(r.voucher_catchup_note), (
            "the override has to precede the claim it overrides"
        )


def test_no_calendar_note_while_the_printed_dates_are_still_ahead():
    r = _report_at(datetime.date(2026, 6, 30), withholding=200,
                   today=datetime.date(2026, 6, 30))
    assert r.safe_harbor.shortfall > 0
    assert r.voucher_dates_note is None and r.ca_voucher_dates_note is None


def test_an_as_of_in_the_past_of_today_is_read_as_such():
    """`--as-of` and `today` are different questions and come apart in both
    directions: a March snapshot pulled in August prints April and June
    deadlines that are months gone."""
    r = _report_at(datetime.date(2026, 3, 1), withholding=200,
                   today=datetime.date(2026, 8, 19))
    assert r.voucher_dates_note is not None
    assert "2026-04-15" in r.voucher_dates_note
    assert "2026-06-15" in r.voucher_dates_note
    assert "2 printed 1040-ES dates already passed" in r.voucher_dates_note


def test_build_report_without_a_today_asserts_nothing_about_the_calendar():
    r = _report_at(datetime.date(2026, 12, 31), withholding=200)
    assert r.voucher_dates_note is None and r.ca_voucher_dates_note is None
    assert P._report_to_dict(r)["voucher_dates_note"] is None


# --- Which lever the covered year still has --------------------------------


def test_the_recommended_actions_lead_names_the_lever_that_exists():
    """It recommended the W-4 route unconditionally, directly above a 4(c)
    figure of $0.00/paycheck over 0 remaining paychecks. `elapsed_fraction` is
    exactly 1.0 at 12-31, so the closed-year default guarantees that state."""
    spent = _report_at(datetime.date(2026, 12, 31), withholding=200)
    assert spent.proj.paychecks_remaining == 0
    assert "No paychecks remain in TY2026" in spent.w4_lever_note
    assert "prefer it over uneven estimates" not in spent.w4_lever_note
    assert "not because nothing is owed" in spent.w4_lever_note, (
        "$0.00/paycheck is also what a covered year prints — the note has to "
        "separate 'no lever left' from 'nothing owed'"
    )

    live = _report_at(datetime.date(2026, 6, 30), withholding=200)
    assert live.proj.paychecks_remaining > 0
    assert "prefer it over uneven estimates when paychecks remain" in live.w4_lever_note
    assert "No paychecks remain" not in live.w4_lever_note


def test_the_spent_lede_names_a_state_balance_on_both_of_its_branches():
    """A state balance due is not disclosed only when federal is $0.

    `_W4_LEVER_SPENT_CLEAR_STATE_DUE` closed this on the branch where the
    federal balance is zero, and the DUE branch beside it went on calling the
    federal figure "The payment target" with a larger state balance in the
    same report — the same defect, one branch over. Federal-first is right
    (the 4(c) figures and voucher table below it are federal); "the" payment
    target is not.
    """
    both = P._w4_lever_note(0, 2026, 500.0, 3000.0)
    assert "$500" in both and "$3,000" in both, (
        "the closed-year lede names one jurisdiction's balance while the "
        "other is also due")
    assert "FEDERAL payment target" in both, (
        "the federal figure is presented as THE payment target again")

    fed_only = P._w4_lever_note(0, 2026, 500.0, 0.0)
    assert "$500" in fed_only and "ALSO projected due" not in fed_only, (
        "the state sentence fires with no state balance — a caveat false of "
        "its reader is one they learn to skip")
    state_only = P._w4_lever_note(0, 2026, 0.0, 3000.0)
    assert "$3,000" in state_only
    assert "no remaining payment target" in P._w4_lever_note(0, 2026, 0.0, 0.0)


def test_a_non_ca_state_balance_is_qualified_where_it_is_stated():
    """CA has a dedicated projection block; other taxed states use a bracket
    estimate, and both closed-year ledes bold the figure.

    The only qualification the report carried for it — "State
    estimated-payment scheduling is informational; state rules vary" — sits in
    Caveats at the very bottom, past a **$4,120** that reads as an amount
    owed. AGENTS.md's bias rule makes over-stating the number the taxpayer
    acts on the hard-error direction, and unearned confidence over-states it
    as surely as a wrong figure does.
    """
    for note in (P._w4_lever_note(0, 2026, 500.0, 4120.0,
                                  state_is_bracket_estimate=True),
                 P._w4_lever_note(0, 2026, 0.0, 4120.0,
                                  state_is_bracket_estimate=True)):
        assert "$4,120" in note
        assert "bracket ESTIMATE" in note
        assert "not modeled in depth" in note

    # California has a dedicated projection block, so the same figure carries no such hedge —
    # a caveat false of its reader is one they learn to skip.
    for note in (P._w4_lever_note(0, 2026, 500.0, 4120.0),
                 P._w4_lever_note(0, 2026, 0.0, 4120.0)):
        assert "$4,120" in note
        assert "bracket ESTIMATE" not in note


def test_estimated_payments_are_range_checked_at_the_parse_boundary():
    """A negative estimate is refused where the user can see the field name.

    It used to pass `_num` at intake, inflate the shortfall, and then be
    refused inside `compute_estimated_vouchers` — but only when the year was
    under-withheld, since the check sat after that function's `shortfall <= 0`
    early return. Same input, two outcomes, decided by an unrelated quantity;
    and the refusal named `estimates_paid`, which appears nowhere in
    projection_inputs.json. AGENTS.md §8: validate the VALUE at the parse
    boundary.
    """
    with pytest.raises(P.ProjectionError,
                       match="estimated_payments_made.federal"):
        P.annualize({"wages": 1000}, datetime.date(2026, 6, 30), "biweekly",
                    {}, {}, {"federal": -2500})
    with pytest.raises(P.ProjectionError,
                       match="estimated_payments_made.state"):
        P.annualize({"wages": 1000}, datetime.date(2026, 6, 30), "biweekly",
                    {}, {}, {"state": -1})
    ok = P.annualize({"wages": 1000}, datetime.date(2026, 6, 30), "biweekly",
                     {}, {}, {"federal": 2500})
    assert ok.estimated_fed_paid == 2500.0


def test_a_closed_year_leads_with_the_balance_when_the_harbor_is_covered():
    """The penalty target can be $0 while a materially larger balance is due."""
    r = _build_report(
        inputs={"pay_frequency": "biweekly",
                "prior_year_form_2210_tax_base": 10000,
                "ytd": {"wages": 200000, "federal_withholding": 12000},
                "forecast_overrides": {"capital_gains": 0},
                "recurring_from_prior": {"taxable_interest": 0},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile=_profile_mfj(), tax_law=_tax_law_2026(),
        prior=_Prior({"form_1040": {"line_24_total_tax": 10000,
                                    "line_11_agi": 120000}}),
        year=2026, as_of=datetime.date(2026, 12, 31))
    assert r.safe_harbor.shortfall == 0.0
    assert all(v.amount == 0 for v in r.vouchers)
    assert r.federal.balance > 0
    assert P._money(r.federal.balance) in r.w4_lever_note
    assert "not the safe-harbor shortfall" in r.w4_lever_note
    assert "can be smaller or $0" in r.w4_lever_note


def test_the_lever_note_leads_the_section_and_reaches_the_payload():
    r = _report_at(datetime.date(2026, 12, 31), withholding=200)
    md = P.render_markdown(r)
    assert r.w4_lever_note in md
    assert md.index("## Recommended actions") < md.index(r.w4_lever_note)
    assert md.index(r.w4_lever_note) < md.index("### 1040-ES voucher schedule")
    assert P._report_to_dict(r)["w4_lever_note"] == r.w4_lever_note


def test_the_lever_note_reads_paychecks_not_a_zero_per_paycheck_figure():
    """A covered mid-year run also prints $0.00/paycheck. Keying the note on
    the figure would tell a taxpayer with payroll left that their W-4 route is
    gone."""
    covered = _report_at(datetime.date(2026, 6, 30), withholding=40000)
    assert covered.w4_penalty.per_paycheck == 0.0
    assert covered.proj.paychecks_remaining > 0
    assert covered.w4_lever_note == P._W4_LEVER_LEAD


@pytest.mark.parametrize("skill", ["tax-project", "tax-withholding"])
def test_the_payload_field_count_matches_the_bullets_that_follow_it(skill):
    """"Read these N things" has to be the N things actually listed.

    The sentence said "eight" while nine bullets followed it, because
    `has_prior_archive` was added to the list without touching the count. An
    agent that takes the count literally stops at the eighth and drops
    `stale_as_of_note` — the ninth, and the one the same paragraph says must
    be surfaced verbatim and confirmed with the user "before reporting any
    number from this run".

    Pinned rather than deleted: the count is what tells an agent whether the
    list it can see is the whole list, which is worth having in a skill that
    is read in pieces. It just has to be true.
    """
    words = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
             "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
             "seventeen": 17}
    text = skill_text(skill)
    stated = re.search(
        r"Read these (\w+) things out of the payload", text)
    assert stated, f"{skill} no longer states a payload-field count"
    assert stated.group(1) in words, (
        f"{skill} states the count as {stated.group(1)!r}, which this test "
        f"cannot check — add it to `words`")

    after = text[stated.end():]
    # The list ends at the next numbered step.
    end = re.search(r"^\d+\. \*\*", after, re.M)
    listed = re.findall(r"^   - \*\*`", after[:end.start() if end else len(after)],
                        re.M)
    assert len(listed) == words[stated.group(1)], (
        f"{skill} says {stated.group(1)} ({words[stated.group(1)]}) payload "
        f"fields and then lists {len(listed)}")


def test_the_prior_baseline_clarifier_has_exactly_one_copy():
    """Two renderers, one sentence.

    `projection.render_markdown` and `withholding.render_markdown` each spelled
    out the clarifier and the degraded-mode bullet independently, including the
    same `archive/{year-1}/` interpolation. A correction — admitting the
    §19136.3 high-income case, where `prior_based` is None but the archive DID
    supply a baseline — had to be made twice with nothing relating the copies.
    """
    proj_src = _engine_source("projection")
    wh_src = _engine_source("withholding")
    fragment = "supplies the CA prior-year harbor below"
    assert proj_src.count(fragment) == 1, (
        "the clarifier is spelled out more than once in projection/")
    assert wh_src.count(fragment) == 0, (
        "withholding.py spells the clarifier out again instead of calling "
        "`_prior_baseline_lines`")

    degraded = "no completed covered-year Form 2210 line-8"
    assert proj_src.count(degraded) == 1
    assert wh_src.count(degraded) == 0

    # And the one copy still produces both bullets for both callers.
    lines = P._prior_baseline_lines(None, 2026, ca_prior_resolved=True,
                                    degraded=True)
    assert len(lines) == 2
    assert "archive/2025/" in lines[0]
    assert "Degraded mode" in lines[1]
    assert P._prior_baseline_lines(None, 2026, ca_prior_resolved=False,
                                   degraded=False) == [
        "- Prior-year baseline: none — DEGRADED MODE"]


def test_a_non_ca_state_balance_is_qualified_in_the_table_too():
    """Both surfaces, not one.

    The bracket-estimate hedge first landed only on the W-4 lede, whose own
    text says "see the state section below" — sending the reader from the
    qualified sentence to a bolded, unqualified restatement of the same
    figure in the State projection table. Of the two presentations the table
    row is the one that reads as authoritative. The Caveats line that used to
    be the only qualification ("State estimated-payment scheduling is
    informational") qualifies the SCHEDULE, not the amount.
    """
    prior = _Prior({"form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    profile = _profile_mfj()
    profile["address"] = {"state": "NY"}
    tax_law = _tax_law_2026()
    tax_law["state"] = {"code": "NY", "type": "graduated_income",
                        "brackets": [{"rate": 0.05, "min": 0, "max": None}],
                        "standard_deduction": {"single": 8000, "mfj": 16000}}
    r = _build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 200000, "federal_withholding": 20000,
                 "state_withholding": 200},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        profile, tax_law, prior, 2026, datetime.date(2026, 12, 31))
    assert r.state is not None and r.state.code == "NY"
    assert r.ca_safe_harbor is None, "the premise: not the modeled state"
    assert r.state.balance > 0

    md = P.render_markdown(r)
    assert "**State balance**" in md
    assert "NY is a bracket ESTIMATE" in md
    assert "not modeled in depth" in md
    # And the lede still carries its own, so neither surface relies on the other.
    assert "bracket ESTIMATE" in r.w4_lever_note

    # California, having a dedicated projection block, gets no such banner in the table.
    ca = _build_sample_report()
    assert ca.ca_safe_harbor is not None
    assert "bracket ESTIMATE" not in P.render_markdown(ca)


def test_the_ca_share_note_does_not_blame_estimates_that_were_never_paid():
    """A caveat false of the reader's own return is one they learn to skip.

    With no estimate paid and all four instalments open, the only thing that
    can move a share off its statutory weight is cents rounding on a shortfall
    small enough for one cent to matter — reachable just above the $500 CA
    floor. The note asserted "estimates already paid are credited against the
    cumulative 30/70/70/100 checkpoints" unconditionally, telling a taxpayer
    who had paid nothing that their payments caused the split.
    """
    harbor = P.compute_ca_safe_harbor(
        projected_tax=5000.0, withholding=4499.97, estimates_paid=0.0,
        prior_tax=None, prior_agi=None, current_agi=100000,
        filing_status="Single")
    assert not harbor.de_minimis, "must escape the $500 floor to reach a schedule"
    assert harbor.estimates_paid == 0.0
    vouchers = P.compute_ca_estimated_vouchers(
        harbor.shortfall, datetime.date(2026, 1, 5), 0.0)
    assert len(vouchers) == len(P.CA_ES_SCHEDULE)
    shares = P._percent_shares([v.amount for v in vouchers])
    assert shares != [round(100 * w) for _, _, w in P.CA_ES_SCHEDULE], (
        "the premise: the shares do diverge on this input")
    assert sum(shares) == 100

    # The CONTRACT, not just the premises above. This test asserted only the
    # arithmetic and stayed green with the renderer's `estimates_paid > 0`
    # gate removed — it described a behaviour it never checked.
    note = P._ca_split_note(vouchers, harbor.estimates_paid)
    assert note, "a divergent split must be explained, not left bare"
    assert "cents rounding" in note[0]
    assert "estimates already paid" not in note[0], (
        "the reader paid none — naming credited estimates here is a caveat "
        "false of their own return")
    assert "earlier installments have closed" not in note[0]

    # And each of the other two causes still selects its own sentence.
    early = P._ca_split_note(
        P.compute_ca_estimated_vouchers(700.0, datetime.date(2026, 3, 1), 300.0),
        300.0)
    assert "estimates already paid" in early[0]
    closed = P._ca_split_note(
        P.compute_ca_estimated_vouchers(1000.0, datetime.date(2026, 7, 2), 0.0),
        0.0)
    assert "earlier installments have closed" in closed[0]

    # A statutory split gets no note at all.
    assert P._ca_split_note(
        P.compute_ca_estimated_vouchers(1000.0, datetime.date(2026, 1, 5), 0.0),
        0.0) == []


def test_the_payload_qualifies_a_non_ca_state_balance_on_an_open_year():
    """The payload is what the skill says to read FIRST.

    Both SKILL.md files open the summarize step with "Read these N things out
    of the payload before you summarize anything". The bracket-estimate hedge
    first existed only in the markdown and in the CLOSED-year
    `w4_lever_note` — on an open year that note is `_W4_LEVER_LEAD`, which has
    no state clause at all, so an agent following the documented flow read a
    bare `state.balance` and reported a bracket estimate as an amount owed.
    Third instance of one concern fixed at one of its surfaces.
    """
    prior = _Prior({"form_1040": {"line_24_total_tax": 15000, "line_11_agi": 120000}})
    profile = _profile_mfj()
    profile["address"] = {"state": "NY"}
    tax_law = _tax_law_2026()
    tax_law["state"] = {"code": "NY", "type": "graduated_income",
                        "brackets": [{"rate": 0.05, "min": 0, "max": None}],
                        "standard_deduction": {"single": 8000, "mfj": 16000}}
    r = _build_report(
        {"pay_frequency": "biweekly",
         "ytd": {"wages": 200000, "federal_withholding": 20000,
                 "state_withholding": 200},
         "estimated_payments_made": {"federal": 0, "state": 0}},
        profile, tax_law, prior, 2026, datetime.date(2026, 6, 30))
    assert r.proj.paychecks_remaining > 0, "the premise: an OPEN year"
    assert "bracket ESTIMATE" not in r.w4_lever_note, (
        "the open-year lede carries no state clause — which is why the "
        "payload field has to exist")
    assert r.state is not None and r.state.balance > 0

    payload = P._report_to_dict(r)
    assert "state_bracket_estimate_note" in payload
    note = payload["state_bracket_estimate_note"]
    assert note and "NY is a bracket ESTIMATE" in note
    assert "not modeled in depth" in note
    # Plain prose: an agent reading --json must not meet raw markdown.
    assert "**" not in note and "⚠" not in note

    # California has a dedicated projection block, so its payload carries no hedge.
    ca_payload = P._report_to_dict(_build_sample_report())
    assert ca_payload["state_bracket_estimate_note"] is None


def test_a_nonzero_amount_never_prints_as_zero_dollars():
    """"$0" means nothing is owed. A real obligation must not share the string.

    `_money` formatted whole dollars unconditionally, so every non-zero figure
    below fifty cents printed as "$0" — indistinguishable from the CA de
    minimis path's genuinely-zero vouchers, which mean exactly that. It
    surfaced as a voucher row reading "| Q4 | 2027-01-15 | 100% | $0 |", a
    hundred percent of nothing, reachable whenever a shortfall clears the $500
    CA floor by cents.

    Cents appear ONLY in that window — every ordinary figure keeps whole
    dollars, which is what makes this safe to fix in the one formatter both
    reports share rather than at the table that happened to expose it.
    """
    assert P._money(0) == "$0"
    assert P._money(0.0) == "$0"
    assert P._money(None) == "—"
    for v in (0.01, 0.49, 0.5, 0.99):
        assert P._money(v) == f"${v:.2f}", f"{v} printed as {P._money(v)}"
        assert P._money(-v) == f"(${v:.2f})"
    # Anything that rounds to a dollar or more is unchanged.
    assert P._money(1.0) == "$1"
    assert P._money(1.4) == "$1"
    assert P._money(1234.56) == "$1,235"
    assert P._money(-1234.56) == "($1,235)"

    # End to end: the row that exposed it.
    vouchers = P.compute_ca_estimated_vouchers(0.01, datetime.date(2026, 10, 1), 0.0)
    shares = P._percent_shares([v.amount for v in vouchers])
    rendered = [f"| Q{v.quarter} | {v.due_date} | {s}% | {P._money(v.amount)} |"
                for v, s in zip(vouchers, shares)]
    assert any("100%" in r for r in rendered), "the premise: a 100% share"
    for row in rendered:
        assert not ("100%" in row and "| $0 |" in row), (
            f"a hundred percent of nothing: {row}")


def test_the_form_2210_zero_year_gate_requires_a_literal_boolean():
    """§6654(e)(2) clears the WHOLE year, so its gate cannot be truthiness.

    `bool("false")` is True and so is `bool(1)`. The confirmed branch sets
    `no_penalty=True` and returns, so a JSON string or an int reaching it
    suppresses a penalty that may well apply — the over-clearing direction
    AGENTS.md makes a hard error. The planning twin
    (`projection._confirmed_flag`) has rejected non-booleans since it shipped;
    this is the copy that prices the penalty on the FILED return, and an agent
    assembles its arguments out of answers.md, which is exactly where a
    hand-typed "false" comes from.
    """
    from agents.lib.tax_computations import compute_form_2210
    base = dict(current_year_tax=20000.0, withholding=5000.0,
                prior_year_tax=0.0, filing_status="Single", tax_year=2024)

    # BOTH flags, because either one alone zeroes the required annual
    # payment: §6654(e)(2) returns no_penalty outright, and
    # §6654(d)(1)(B)(ii) keeps the $0 base and min(90%, 110% x $0) is $0.
    # A truthy string reaching either is the same over-clearing failure.
    for flag in ("prior_year_zero_liability_confirmed",
                 "prior_year_return_filed_12_months_confirmed"):
        for bad in ("false", "true", "False", 1, 0, 1.0, None, [], {}):
            with pytest.raises(ValueError) as exc:
                compute_form_2210(**base, **{flag: bad})
            assert flag in str(exc.value)
            assert "bool" in str(exc.value)

    # Real booleans still decide it, all three ways.
    assert compute_form_2210(
        **base, prior_year_zero_liability_confirmed=True)["no_penalty"] is True
    ordinary = compute_form_2210(
        **base, prior_year_return_filed_12_months_confirmed=True)
    assert ordinary["required_annual_payment"] == 0.0
    assert "6654(e)(2)" not in (ordinary["no_penalty_reason"] or ""), (
        "the ordinary prong was reported as the citizenship exception")
    unconfirmed = compute_form_2210(
        **base, prior_year_zero_liability_confirmed=False,
        prior_year_return_filed_12_months_confirmed=False)
    assert not unconfirmed.get("no_penalty")
    assert unconfirmed["required_annual_payment"] == 18000.0
    assert any("6654(d)(1)(B)" in n and "6654(e)(2)" in n
               for n in unconfirmed["notes"]), (
        "the dropped-$0 note names one escape route out of the two, so the "
        "taxpayer the other one covers is never told about it")


def test_a_rejected_numeric_value_is_never_echoed_back():
    """A misplaced name or email in a numeric field must not reach the terminal.

    `_num` embedded `{value!r}` in its ProjectionError. Both CLIs print
    `redact(e)`, and redact() masks SSN and credential SHAPES — not names, not
    email addresses, not street addresses. AGENTS.md §1 forbids echoing a
    sensitive value at all, so the message names the field and the type and
    stops, the rule `_canonical_status` already follows.
    """
    from agents.lib.secure_fs import redact
    # Names, emails and addresses — deliberately NOT an SSN-shaped string.
    # redact() masks that shape already; these are the ones it cannot, which
    # is the whole finding. (An SSN literal here would also, correctly, be
    # blocked by the pre-commit PII gate.)
    sensitive = ("Jane Q. Taxpayer", "jane@example.com",
                 "123 Main St, Apt 4")
    for bad in sensitive:
        with pytest.raises(P.ProjectionError) as exc:
            P._num(bad, "prior_year_form_2210_agi")
        printed = redact(str(exc.value))
        assert bad not in printed
        for word in bad.replace(",", " ").split():
            assert word not in printed, (
                f"{word!r} from the rejected value survived into {printed!r}")
        # The half that actually helps is still there.
        assert "prior_year_form_2210_agi" in printed
        assert "str" in printed


def test_a_rejected_negative_figure_is_never_echoed_back():
    """The sibling branch of the same helper, and the one this branch widened.

    `_nonneg_num` guarded the Schedule 1-A override alone when it embedded
    `{n!r}`; this branch routed every YTD figure `annualize` reads — wages,
    the three withholding fields, ss_wages, medicare_wages — and both
    estimates-paid fields through it, in `projection` and in `withholding`
    both. So the value that message prints stopped being a deduction override
    and became the taxpayer's payroll.

    The argument is NOT that a dollar figure is secret from this terminal: the
    report the refusal precedes prints wages and withholding to the same
    stdout. It is that an error line is the piece of a run that gets pasted
    into a bug report, which AGENTS.md §1 forbids carrying taxpayer data, and
    a whole projection is not. Nothing diagnostic is lost — `n < 0` has one
    shape, so the field name says all of it.
    """
    from agents.lib.secure_fs import redact
    # Distinctive enough that any rendering of it is unmistakable, and checked
    # in every spelling a message might reach for.
    amount = -48213.57
    fields = ("ytd.wages", "ytd.federal_withholding", "ytd.state_withholding",
              "ytd.medicare_withheld", "ytd.ss_wages", "schedule_1a",
              "estimated_payments_made.federal")
    for field in fields:
        with pytest.raises(P.ProjectionError) as exc:
            P._nonneg_num(amount, field)
        printed = redact(str(exc.value))
        for rendering in (repr(amount), str(amount), f"{amount:,.2f}",
                          f"{abs(amount):,.2f}", "48213", "48,213"):
            assert rendering not in printed, (
                f"{rendering!r} from the rejected figure survived into "
                f"{printed!r}")
        # The half that actually helps is still there.
        assert field in printed
        assert "non-negative" in printed


def test_the_annualized_ytd_path_refuses_a_negative_without_printing_it():
    """Pinned at the call site, not only at the helper.

    `annualize` is where the widening happened, and a later round that
    reintroduced the value — in this function's own message, or by routing a
    YTD field through some third validator — would leave the helper test
    green. A sign typo in withholding is the failure this path exists to
    catch, so it is the one worth walking end to end.
    """
    from agents.lib.secure_fs import redact
    with pytest.raises(P.ProjectionError) as exc:
        P.annualize({"wages": 120000.0, "federal_withholding": -9876.54},
                    datetime.date(2026, 7, 1), "biweekly", {}, {},
                    {"federal": 0, "state": 0})
    printed = redact(str(exc.value))
    assert "ytd.federal_withholding" in printed
    for rendering in ("9876", "9,876", "-9876.54"):
        assert rendering not in printed, (
            f"{rendering!r} reached the terminal from the YTD intake: "
            f"{printed!r}")


class TestTheCaPriorYearFloorIsProvedByTheFigureAlreadyHeld:
    """R&TC §19136(c)(2)'s prior-year prong, in the subset that needs no credits.

    The subdivision reads on EITHER year — preceding-year CA tax less credits,
    **or** the covered year's — and the engine tested only the covered-year
    half, handing the other over because "which credits FTB 5805 nets is not
    something this engine has verified". True in general, and false for one
    subset: `prior_tax` is Form 540 total tax as filed, which BOUNDS the
    statutory quantity from above (other taxes only add to a Form 540 total,
    the statute only ever subtracts credits from it). A total already under the
    floor therefore proves the prong however the credits fall, and the open
    question about §19002 withholding never has to be answered.

    The bound is one-directional, which is the whole shape of these tests: under
    the floor proves no penalty, at or above it proves nothing either way.

    The one thing it is NOT limited by is the taxpayer's income. §19136.3
    withdraws the prior-year prong from the required annual payment and leaves
    §19136(c)(2) untouched, so the proof holds at $2,000,000 exactly as it does
    at $200,000 — which is what the §19136.3 tests here pin, after a round in
    which the engine printed that reading in a note and acted on its opposite.

    Three states, not two, and the third is the one that costs money: the prior
    figure can be UNDER the floor (proved), AT OR ABOVE it (nothing proved
    either way, credits still to come off), or ABSENT. Absent had no note at
    all, so a report with no prior-year CA figure printed a 540-ES schedule
    without ever saying that the figure nobody had looked up could zero every
    row of it.
    """

    FLOOR = 500.0

    def _harbor(self, prior_tax, status="single", current_agi=200_000.0):
        # Under-withheld on purpose: without the prong there is a real CA
        # shortfall here, so a suppressed one cannot be mistaken for an
        # already-covered year.
        return P.compute_ca_safe_harbor(
            projected_tax=20_000.0, prior_tax=prior_tax, prior_agi=100_000.0,
            current_agi=current_agi, filing_status=status,
            withholding=2_000.0, estimates_paid=0.0)

    def test_a_prior_total_under_the_floor_clears_the_year(self):
        csh = self._harbor(400.0)
        assert csh.prior_year_floor is True
        assert csh.shortfall == 0.0, (
            "the CA schedule still asks for money in a year §19136(c)(2) says "
            "no penalty can arise in — the overstatement PR #35 already "
            "settled for the covered-year prong of the same subdivision")
        assert csh.covered is True
        notes = "\n".join(P._ca_notes(csh))
        assert "PRIOR-year floor is already met" in notes
        assert "still due with the return" in notes, (
            "clearing the PENALTY must not read as clearing the TAX")

    def test_the_bound_only_works_downwards(self):
        """At or above the floor the credits have not come off yet, so nothing
        is proved — and the note must not read as the prong having failed."""
        csh = self._harbor(6_000.0)
        assert csh.prior_year_floor is False
        assert csh.shortfall > 0
        notes = "\n".join(P._ca_notes(csh))
        assert "not settled here" in notes and "could still carry it under" in notes
        assert "PRIOR-year floor is already met" not in notes

    def test_the_mfs_floor_is_the_lower_one(self):
        """$250 for a separate return. A single floor for both would clear a
        penalty that attaches for exactly the filers the statute halves it for."""
        assert self._harbor(400.0, status="MFS").prior_year_floor is False
        assert self._harbor(200.0, status="MFS").prior_year_floor is True

    def test_a_degraded_year_proves_nothing_and_says_exactly_that(self):
        """`prior_tax is None` is no prior figure at all, not a small one.

        The fire condition was `prior_tax != 0`, which is True of None, so the
        either-year note fired at a taxpayer with nothing to check. Silencing it
        was right and stopping there was not: the run went on to bill them with
        no mention of the rule, which is the reverse error and the expensive
        one. It gets its own note now, and the either-year note stays off — that
        one tells a reader their known total is above the floor.
        """
        csh = self._harbor(None)
        assert csh.prior_year_floor is False
        assert csh.prior_year_floor_unknown is True
        assert csh.degraded is True
        assert csh.shortfall > 0
        notes = "\n".join(P._ca_notes(csh))
        assert "floor reads on EITHER year" not in notes
        assert "PRIOR-year floor is already met" not in notes
        assert "UNTESTED here" in notes, (
            "no prior-year CA figure, a live 540-ES schedule, and nothing said "
            "about the rule that could zero it")
        assert notes.count("UNTESTED here") == 1

    def test_the_unresolved_floor_outlives_the_million_dollar_limitation(self):
        """The band Codex cited, and the one `degraded` cannot cover.

        `degraded` carries `and not high` because at the §19136.3 limitation the
        prior-year PRONG is withdrawn by statute rather than missing, and its
        note offers to restore the prong — an offer the statute refuses up here.
        That correct exclusion left the FLOOR unmentioned too, and the floor
        survives §19136.3 intact, so the taxpayer with the largest bill on the
        page was the one told least about the rule that might cancel it.
        """
        csh = self._harbor(None, current_agi=2_000_000.0)
        assert csh.high_income_90_only is True
        assert csh.degraded is False, (
            "the prong is withdrawn here, not missing — a degraded note would "
            "offer to restore something §19136.3 has taken away")
        assert csh.prior_year_floor_unknown is True
        assert csh.shortfall > 0
        notes = "\n".join(P._ca_notes(csh))
        assert "UNTESTED here" in notes
        assert "does not reach the floor" in notes, (
            "the note must say why the limitation above does not settle this")
        assert "**Degraded:**" not in notes

    def test_nothing_is_flagged_where_nothing_is_being_asked_for(self):
        """The note is loud, so it fires only where something is unresolved.

        Two ways the year is already settled with no prior figure: the
        covered-year floor clears it outright, or WITHHOLDING alone meets the
        requirement. In both the prior-year half is moot, and a note urging a
        Form 540 lookup would be the gate crying wolf.

        Withholding, specifically — not "the payments". §6654(g)(1) and FTB
        5805 Worksheet II line 2 deem it paid ratably at every deadline, so it
        cannot leave an instalment late and there is no accrual for the floor
        to clear; `_voucher_catchup_note` is silent here for the same reason,
        and that silence is asserted below as the paired fact. Estimates carry
        dates and are the opposite case — the sibling test below.
        """
        de_minimis = P.compute_ca_safe_harbor(
            projected_tax=2_000.0, prior_tax=None, prior_agi=None,
            current_agi=200_000.0, filing_status="single",
            withholding=1_600.0, estimates_paid=0.0)
        assert de_minimis.de_minimis is True
        assert de_minimis.shortfall_before_de_minimis > 0
        assert de_minimis.prior_year_floor_unknown is False
        assert "UNTESTED here" not in "\n".join(P._ca_notes(de_minimis))

        covered = P.compute_ca_safe_harbor(
            projected_tax=20_000.0, prior_tax=None, prior_agi=None,
            current_agi=200_000.0, filing_status="single",
            withholding=19_000.0, estimates_paid=0.0)
        assert covered.de_minimis is False and covered.shortfall == 0.0
        assert covered.prior_year_floor_unknown is False
        assert "UNTESTED here" not in "\n".join(P._ca_notes(covered))
        # The paired fact. The flag must be open exactly where the timing
        # warning can fire, so pin the silence here rather than only the
        # flag: if withholding ever starts producing a late instalment, this
        # goes red beside the assertion that says the floor stays shut.
        as_of = datetime.date(2026, 7, 1)
        vouchers = P.compute_ca_estimated_vouchers(covered.shortfall, as_of,
                                                   covered.estimates_paid)
        assert P._voucher_catchup_note(
            P._CA_CATCHUP, vouchers, covered, as_of) is None

    def test_a_requirement_met_LATE_leaves_the_floor_open(self):
        """Round eighteen: "already paid" and "paid on time" are not one fact.

        The flag was gated on a positive annual gap, so estimates meeting the
        requirement closed it. FTB 5805 measures each instalment CUMULATIVELY,
        and an estimate arriving after a deadline meets the annual figure while
        leaving that instalment's penalty accruing — which the report already
        says, `_voucher_catchup_note` returning its timing warning on exactly
        these inputs. What it did not say is that R&TC §19136(c)(2) bars the
        addition to tax outright on either year's figure, dates and all, so a
        single lookup on the prior Form 540 can end the question. At or above
        the §19136.3 limitation nothing else pointed there either: `degraded`
        is False up there by design, so the warning arrived with no route out.
        """
        csh = P.compute_ca_safe_harbor(
            projected_tax=120_000.0, prior_tax=None, prior_agi=None,
            current_agi=2_000_000.0, filing_status="single",
            withholding=10_000.0, estimates_paid=98_000.0)
        assert csh.high_income_90_only is True and csh.degraded is False
        assert csh.shortfall == 0.0, (
            "this case is only itself while the annual gap reads zero — a "
            "positive one reaches the note through the old gate and proves "
            "nothing about the new one")
        assert csh.prior_year_floor_unknown is True

        as_of = datetime.date(2026, 7, 1)
        vouchers = P.compute_ca_estimated_vouchers(csh.shortfall, as_of,
                                                   csh.estimates_paid)
        assert all(v.amount == 0.0 for v in vouchers), (
            "the schedule must be all-zero here, or the pair below selects "
            "the other note and this test stops being about anything")
        timing = P._voucher_catchup_note(P._CA_CATCHUP, vouchers, csh, as_of)
        assert timing, (
            "the premise is gone: with no timing warning there is no open "
            "verdict for the floor to settle")
        assert "before concluding no CA penalty applies" in timing, (
            "the warning must be the CUMULATIVE one — the floor answers that "
            "question, and nothing else the schedule can say")

        notes = "\n".join(P._ca_notes(csh))
        assert "UNTESTED here" in notes
        assert "payment PACE may already have caused" in notes, (
            "the schedule-worded note reached a reader with no schedule")
        assert "schedule below" not in notes, (
            "every voucher row is $0, so a note pointing at the schedule "
            "describes a page this reader does not have — and the sentence "
            "that was true gets skipped along with it")
        assert "neither test asks WHEN anything was paid" in notes, (
            "the lookup is worth doing BECAUSE the floor outranks the timing "
            "question; without that the reader does both")
        assert "**Degraded:**" not in notes

    def test_the_degraded_note_offers_only_a_remedy_that_exists(self):
        """It advertised a manual CA baseline that nothing reads.

        The federal degraded line names `prior_year_form_2210_tax_base`, a real
        `projection_inputs.json` key. The CA one told the user to "supply the
        prior Form 540 total tax and CA AGI" — `_prior_ca_tax_agi` takes the
        archive and nothing else, there is no such key, and supplying the
        figures could not change a rerun. A remedy that does nothing is worse
        than none: it reads as an answer and costs the user the lookup.
        """
        notes = "\n".join(P._ca_notes(self._harbor(None)))
        assert "supply the prior" not in notes
        assert "/tax-archive" in notes
        assert "no `projection_inputs.json` field" in notes, (
            "the note must say the manual route does not exist, not merely "
            "stop offering it — a reader who tried it deserves the reason")
        assert "computed outside this toolkit cannot supply the prong" in notes

        # The premise, pinned where it lives. The note's claim is not about
        # wording — it is that the CA prong has exactly one source. Give
        # `_prior_ca_tax_agi` an override parameter and the note starts lying
        # in the other direction, with every assertion above still green.
        params = list(inspect.signature(P._prior_ca_tax_agi).parameters)
        assert params == ["prior"], (
            f"_prior_ca_tax_agi now takes {params} — if a hand-entered CA "
            "baseline has become an input, the degraded note above says it "
            "has not and must be rewritten to name the field")

    def test_the_bound_the_prong_rests_on_is_a_property_of_the_template(self):
        """The suppression above is only sound while `ca_total_tax` is composed
        the way `result_template.json` composes it.

        Everything else in this class tests the CONSEQUENCE. This tests the
        premise, because the premise lives in another file and nothing else
        would notice it moving: redefine `ca_total_tax` to exclude CA AMT, or
        to be net of withholding, and the bound inverts — a taxpayer whose AMT
        or withholding carried them over the floor gets told no penalty can
        arise, silently, in the understating direction, with every assertion
        above still green because they only ever pass it a number.

        Pinned on the template rather than on an engine function because there
        is no engine function: `ca_total_tax` is assembled by /tax-compute
        writing the section, and the template is where its composition is
        specified.
        """
        section = json.loads(
            (Path(__file__).resolve().parent.parent / "agents" / "templates"
             / "result_template.json").read_text())["california_540"]

        # The three addends the bound needs on the ENGINE's side of the
        # inequality. Missing any one makes `ca_total_tax` smaller than the
        # statutory quantity it is supposed to dominate.
        assert "ca_tax_after_credits" in section
        assert "ca_mental_health_surcharge" in section
        assert "ca_amt" in section["schedule_p"], (
            "CA AMT has left the Schedule P block — §19136(c)(2) counts §17062 "
            "tax, so a ca_total_tax without it no longer bounds the statute")
        assert "ADDED to ca_total_tax" in section["schedule_p"]["_note"], (
            "the template no longer says CA AMT is added to ca_total_tax, so "
            "the prior-year floor may now be tested against a figure that "
            "excludes it")

        # ...and GROSS of withholding. A netted total would be SMALLER than
        # the statutory figure, which is the direction that clears a penalty
        # that attaches.
        assert "ca_withholding_box17" in section, (
            "CA withholding is no longer a sibling field — if it has been "
            "folded into ca_total_tax, that total is net of payments and "
            "cannot bound a rule written about tax less CREDITS")

    def test_the_high_income_limitation_does_not_reach_the_floor(self):
        """§19136.3 modifies the REQUIRED PAYMENT; (c)(2) bars the PENALTY.

        Its whole operative text is "Section 6654(d)(1)(B) ... is modified to
        additionally provide that clause (ii) shall not apply" above $1,000,000
        ($500,000 MFS) — clause (ii) being the prior-year component of the
        required annual payment. R&TC §19136(c)(2) is in a different section
        and says "an addition to tax shall not be imposed under this section
        if...", and §19136.3 never mentions it. FTB 5805's General Information
        keeps the same order: the floor sentence is unconditional, and the
        $1,000,000 sentence is the one that reads "must use the tax on their
        ... return to figure their penalty IF THEY ARE SUBJECT to the
        underpayment of estimated tax penalty".

        This engine withheld the proof here for one round, while printing a
        note beside the schedule saying the floor "is a separate rule that
        §19136.3 does not reach". A 540-ES demand and that sentence in the same
        report cannot both be right, and the pair collected money California
        cannot penalise not having — the over-statement direction AGENTS.md
        ranks as the hard error.
        """
        csh = self._harbor(400.0, current_agi=2_000_000.0)
        assert csh.high_income_90_only is True
        assert csh.prior_year_floor is True, "the statutory fact does not change"
        assert csh.required_annual == csh.pct90_current, (
            "the limitation still decides the REQUIREMENT — 90% of the current "
            "year, the prior-year prong being gone from it")
        assert csh.shortfall == 0.0 and csh.covered is True, (
            "...and the floor still decides the PENALTY, so the schedule must "
            "not ask for a payment that avoids nothing")
        notes = "\n".join(P._ca_notes(csh))
        assert "PRIOR-year floor is already met" in notes
        assert "90% of the current year" in notes, (
            "a $0 penalty target beside a 90% required payment reads as a bug "
            "unless the note says which rule produced which figure")
        assert "still due with the return" in notes, (
            "clearing the PENALTY must not read as clearing the TAX")
        assert "CA preparer" not in notes, (
            "the engine no longer hands over a prong its own figure proves")

    def test_a_zero_prior_year_clears_at_high_income_too(self):
        """The sibling of the case above, and the one Codex did not cite.

        `prior_year_floor` carries the `0 < prior_tax` guard, so a $0 prior
        year reaches `no_penalty` only through the property's second term.
        BELOW the limitation it needed nothing — `prior_year_prong` already
        zeroes the required annual payment off a $0 base — which is precisely
        why the gap was invisible: the one AGI band where the $0 does not
        travel through the requirement is the one band where it was dropped.
        A $0 prior-year tax is the strongest case (c)(2) has.
        """
        csh = self._harbor(0.0, current_agi=2_000_000.0)
        assert csh.high_income_90_only is True
        assert csh.prior_year_floor is False, (
            "the $0 belongs to its own note pair, not to this flag")
        assert csh.prior_year_floor_applied is True
        assert csh.required_annual == csh.pct90_current
        assert csh.shortfall == 0.0 and csh.covered is True
        notes = "\n".join(P._ca_notes(csh))
        assert "$0 prior-year CA tax is on file" in notes
        assert "no CA underpayment penalty can arise" in notes
        assert "CA preparer" not in notes
        assert notes.count("§19136(c)(2)") == 1, (
            "the $0 pair and the under-the-floor note both fired — two notes "
            "for one fact is the duplication the `> 0` guard exists to stop")

    def test_a_negative_prior_year_proves_nothing(self):
        """Nonsense input must land in the OVER-stating direction.

        Neither term of the property admits it: `prior_year_floor` needs
        `0 < prior_tax` and the $0 term is an exact compare. A `< ca_floor`
        test written on its own would have cleared the penalty outright for a
        figure that cannot exist on a Form 540.
        """
        csh = self._harbor(-1_000.0, current_agi=2_000_000.0)
        assert csh.prior_year_floor is False
        assert csh.prior_year_floor_applied is False
        assert csh.shortfall > 0

    def test_the_applied_flag_and_the_property_cannot_drift(self):
        """`compute_ca_safe_harbor` spells the property's expression out again
        to reach `no_penalty` before the object exists. That duplication is
        what went out of step last time, so it is pinned rather than trusted —
        across both floors, both AGI bands and the degraded case.
        """
        for prior_tax in (None, -1.0, 0.0, 0.01, 249.0, 250.0, 400.0,
                          499.99, 500.0, 6_000.0):
            for status in ("single", "MFS"):
                for agi in (200_000.0, 2_000_000.0):
                    csh = self._harbor(prior_tax, status=status,
                                       current_agi=agi)
                    floor = (P.CA_ES_DE_MINIMIS_MFS if status == "MFS"
                             else P.CA_ES_DE_MINIMIS)
                    expected = (prior_tax is not None
                                and 0 <= prior_tax < floor)
                    assert csh.prior_year_floor_applied is expected, (
                        f"prior_tax={prior_tax!r} status={status} agi={agi}: "
                        f"the property and the statutory rule disagree")
                    if expected:
                        assert csh.shortfall == 0.0 and csh.covered is True


class TestTheProvedPriorYearFloorReachesEveryWarningItSettles:
    """The prong from the class above, at the two consumers it did not reach.

    `compute_ca_safe_harbor` folded it straight into `no_penalty`, which zeroes
    the shortfall and the vouchers — and stopped there. Two gates downstream
    still asked `de_minimis` alone, so a taxpayer R&TC §19136(c)(2) clears
    outright was told, in the same report that shows a $0 required payment,
    that $100 more tax than projected makes a penalty attach and that a late
    estimate may have one accruing. Both are impossible on a year the prior-year
    prong carries: it is measured on a CLOSED year, so neither this year's
    projection nor this year's payment dates can move it.

    Direction is why these are defects rather than surplus caution. The repo's
    rule is that a gate firing on correct data teaches its reader to read past
    it — and these two fire on precisely the taxpayers with the least to gain
    from reading on.
    """

    def _cliff(self, prior_tax, current_agi=200_000.0):
        """De minimis AND the prior-year floor, with a live cliff underneath.

        $400 of CA tax, nothing withheld: $400 is under the $500 floor, and
        past it the 90% prong would ask 0.9 x $500 = $450 against $0 paid. So
        the cliff is genuinely positive on these figures and its suppression
        cannot be an artefact of there being nothing to suppress.
        """
        return P.compute_ca_safe_harbor(
            projected_tax=400.0, prior_tax=prior_tax, prior_agi=100_000.0,
            current_agi=current_agi, filing_status="single",
            withholding=0.0, estimates_paid=0.0)

    def _timing(self, prior_tax):
        """The other gate: NOT de minimis, estimates paid, undated.

        $20,000 of CA tax against $100 withheld is far over the floor, so the
        current-year prong is unavailable and only the prior-year one is left.
        The $5,000 estimate is what opens the timing branch at all.
        """
        as_of = datetime.date(2026, 10, 1)
        csh = P.compute_ca_safe_harbor(
            projected_tax=20_000.0, prior_tax=prior_tax, prior_agi=100_000.0,
            current_agi=200_000.0, filing_status="single",
            withholding=100.0, estimates_paid=5_000.0)
        vouchers = P.compute_ca_estimated_vouchers(csh.shortfall, as_of, 5_000.0)
        return csh, P._voucher_catchup_note(P._CA_CATCHUP, vouchers, csh, as_of)

    def test_the_cliff_is_not_reported_where_crossing_it_changes_nothing(self):
        csh = self._cliff(400.0)
        assert csh.de_minimis is True and csh.prior_year_floor_applied is True, (
            "the fixture no longer holds BOTH floors at once, which is the "
            "only case this pins")
        assert (csh.de_minimis_cliff_more_tax,
                csh.de_minimis_cliff_less_withholding) == (0.0, 0.0), (
            "the stored cliff says what attaches once the floor lapses, and "
            "nothing does — leaving a live figure there leaves the same bug "
            "waiting on the next consumer to print it")
        cell = P._ca_status(csh)
        assert "and it attaches" not in cell, (
            "the status cell warns that more tax makes a penalty attach, on a "
            "year §19136(c)(2) has already cleared on the PRIOR year")
        assert "separate exemption clears the year" in cell

    def test_the_suppressed_cliff_does_not_borrow_the_wrong_reason(self):
        """`_CLIFF_NOTHING_ATTACHES` reaches the same conclusion and states a
        REASON that is false here — the required annual payment is not met on
        its own in this run, a different rule simply makes that irrelevant.
        The engine already split the `withholding < margin` case out for
        exactly this; reusing the tail would have re-made the mistake."""
        cell = P._ca_status(self._cliff(400.0))
        assert P._CLIFF_NOTHING_ATTACHES not in cell
        assert P._CLIFF_SECOND_EXEMPTION in cell
        notes = "\n".join(P._ca_notes(self._cliff(400.0)))
        assert "PRIOR-year floor is already met" in notes, (
            "the cell defers the citation to this note rather than carrying a "
            "third copy of §19136(c)(2) — so the note has to be there")

    def test_a_prior_year_over_the_floor_keeps_its_cliff(self):
        """The control. $600 leaves the prior-year prong unproved, so the
        de-minimis excuse is all that is carrying the year and the warning
        about losing it is true and has to survive."""
        csh = self._cliff(600.0)
        assert csh.prior_year_floor_applied is False
        assert csh.de_minimis_cliff_more_tax > 0
        assert "and it attaches" in P._ca_status(csh)

    def test_the_high_income_limitation_does_not_revive_the_cliff(self):
        """§19136.3 withdraws the PRONG from the required annual payment and
        leaves §19136(c)(2) alone, so the year is cleared here for the same
        reason as below the limitation and the cliff is moot for the same
        reason. Reviving it at $1M would warn the taxpayer that $100 more tax
        makes a penalty attach, in the same report that shows $0 needed to
        avoid one."""
        csh = self._cliff(400.0, current_agi=1_500_000.0)
        assert csh.prior_year_floor is True
        assert csh.prior_year_floor_applied is True
        assert (csh.de_minimis_cliff_more_tax,
                csh.de_minimis_cliff_less_withholding) == (0.0, 0.0)
        assert "and it attaches" not in P._ca_status(csh)
        assert P._CLIFF_SECOND_EXEMPTION in P._ca_status(csh)

    def test_the_status_cell_carries_a_reason_beside_a_visible_gap(self):
        """A bare "covered" is only readable while the requirement is small.

        The floor applying at §19136.3 is what made the two figures diverge:
        `required_annual` stays at 90% of the CURRENT year up there, so the
        table shows a six-figure requirement, a fraction of it paid, and
        "covered" — the shape `_de_minimis_cell` was already given a reason to
        avoid. Below the limitation the prong shrinks the requirement too, so
        there was never anything to reconcile and the bare cell was fine.
        """
        csh = P.compute_ca_safe_harbor(
            projected_tax=120_000.0, prior_tax=400.0, prior_agi=900_000.0,
            current_agi=2_000_000.0, filing_status="single",
            withholding=30_000.0, estimates_paid=0.0)
        assert csh.de_minimis is False, (
            "the fixture no longer isolates the prior-year floor — the "
            "de-minimis cell has carried its own reason since it shipped")
        cell = P._ca_status(csh)
        assert cell != "covered", (
            f"${csh.required_annual:,.0f} required against "
            f"${csh.withholding:,.0f} paid, reported as a bare 'covered'")
        assert "separate exemption clears the year" in cell
        assert "§19136(c)(2)" not in cell, (
            "the citation belongs in the note, which every run reaching here "
            "prints — a second copy is a second thing to drift")

    def test_a_zero_prior_year_settles_both_gates_as_well(self):
        """The $0 sibling, at both gates and in both AGI bands.

        Below the limitation the $0 already zeroed the requirement, so the
        shortfall was right and only these two gates were wrong — the cliff
        warned about a floor lapsing on a year the prior-year prong carries,
        and the timing note sent a taxpayer to re-date estimates against a
        prong measured on a CLOSED year. Neither can be moved by anything the
        covered year does.
        """
        for agi in (200_000.0, 1_500_000.0):
            csh = self._cliff(0.0, current_agi=agi)
            assert csh.prior_year_floor_applied is True
            assert (csh.de_minimis_cliff_more_tax,
                    csh.de_minimis_cliff_less_withholding) == (0.0, 0.0)
            assert "and it attaches" not in P._ca_status(csh)
        csh, note = self._timing(0.0)
        assert csh.prior_year_floor_applied is True
        assert note is None

    def test_the_timing_caveat_is_silent_where_no_timing_can_matter(self):
        csh, note = self._timing(400.0)
        assert csh.de_minimis is False and csh.prior_year_floor_applied is True, (
            "the fixture no longer isolates the prior-year prong — with the "
            "de minimis also holding, the gate this pins was already closed")
        assert note is None, (
            "the report tells a taxpayer to re-run the dated cumulative test "
            "'before concluding no CA penalty applies', on a year the "
            "subdivision has already concluded it for. No payment date can "
            "change a prong measured on the prior year")

    def test_a_prior_year_over_the_floor_keeps_its_timing_caveat(self):
        """The control, one dollar figure apart. $600 leaves the prong
        unproved, the undated estimate is genuinely un-checkable, and the
        pointer to the dated test is the honest answer."""
        csh, note = self._timing(600.0)
        assert csh.prior_year_floor_applied is False
        assert note == P._CA_VOUCHER_TIMING_SETTLED

    def test_the_federal_harbor_and_the_test_stubs_still_reach_both_gates(self):
        """Both gates are duck-typed over harbors that carry no such field —
        `SafeHarbor` deliberately (see `CaSafeHarbor.prior_tax_unnetted` for
        what a permanent False on the sibling class cost once), and the stubs
        these gates are pinned with incidentally. A direct attribute read would
        have turned every federal run into an AttributeError."""
        assert P._prior_year_floor_applied(types.SimpleNamespace()) is False
        fed = P.compute_safe_harbor(
            projected_tax=400.0, prior_tax=None, prior_agi=None,
            filing_status="single", withholding=0.0, estimates_paid=0.0)
        assert not hasattr(fed, "prior_year_floor_applied")
        assert P._prior_year_floor_applied(fed) is False
        assert "and it attaches" in P._federal_status(fed)


def test_state_standard_deduction_reads_the_full_status_and_refuses_an_unfetched_one():
    """California gives head of household the joint amount; `_binary_status_key`
    read `single` for HoH, halving the deduction and over-stating the state
    estimate. A key the map carries at None is an unfetched cache, refused."""
    law = _tax_law_2026()
    law["state"]["standard_deduction"] = {"mfj": 11000, "single": 5500, "hoh": 11000}
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    hoh = P.project_state_tax(_proj(), prior, law, filing_status="HoH")
    single = P.project_state_tax(_proj(), prior, law, filing_status="Single")
    mfs = P.project_state_tax(_proj(), prior, law, filing_status="MFS")
    assert hoh is not None and single is not None and mfs is not None
    assert hoh.taxable_income == single.taxable_income - 5500
    assert mfs.taxable_income == single.taxable_income
    law["state"]["standard_deduction"]["hoh"] = None
    with pytest.raises(P.ProjectionError, match="unfetched"):
        P.project_state_tax(_proj(), prior, law, filing_status="HoH")


def test_state_standard_deduction_falls_to_single_where_the_template_has_no_hoh_row():
    """The full-status read gave head of household a ZERO deduction whenever
    the map had no `hoh` key — the generic bracket-estimate states, and this
    module's own fixture. The stand-in is single, as before, never zero."""
    law = _tax_law_2026()
    assert "hoh" not in law["state"]["standard_deduction"]
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})
    hoh = P.project_state_tax(_proj(), prior, law, filing_status="HoH")
    single = P.project_state_tax(_proj(), prior, law, filing_status="Single")
    assert hoh.taxable_income == single.taxable_income


class TestAStateWhoseBracketsAreKeyedByRateSchedule:
    """New York's `state.brackets` is a dict of §601 rate schedules, and a
    non-empty dict is TRUTHY — so `state_cfg.get("brackets") or []` waved it
    into compute_bracket_tax, which iterates a list of dicts and got the
    schedule keys as strings instead."""

    def _law(self, **state_over):
        law = _tax_law_2026()
        law["state"] = ny_state_cache(**state_over)
        return law

    def test_the_old_gate_crashed_and_no_figure_is_published_now(self):
        """The crash is gone, and so is the number. See the class below for
        why a projection of New York's LINE 39 is worse than none."""
        law = self._law()
        assert bool(law["state"]["brackets"] or [])   # the gate that passed it
        with pytest.raises(AttributeError):
            TC.compute_bracket_tax(50000.0, law["state"]["brackets"])
        assert P.project_state_tax(_proj(), None, law, "MFJ",
                                   resident_state="NY") is None

    def test_a_flat_list_state_is_untouched(self):
        """California regression pin: the accessor must not change the shape
        every other state still uses."""
        st = P.project_state_tax(_proj(state_withholding=4000),
                                 _Prior({"california_540": {"ca_total_tax": 5000}}),
                                 _tax_law_2026(), filing_status="MFJ")
        assert (st.taxable_income, st.tax) == (89000, 3650)

    def test_the_accessor_still_resolves_the_schedule_for_other_readers(self):
        """`loss_harvest` and the single-reader sweep still need it; the
        projection refusing is about the BALANCE, not about the brackets."""
        cfg = self._law()["state"]
        assert TC.state_brackets_for_status(cfg, "mfj") is not None
        assert TC.state_brackets_for_status(cfg, "mfj")[0]["rate"] == 0.04


class TestARecaptureStateGetsNoProjectedBalance:
    """The severest finding of the review pass, reproduced before it was
    believed: `/tax-project` priced New York as a STATE-ONLY return while
    subtracting a withholding figure defined across FOUR taxes."""

    def _law(self, **over):
        law = _tax_law_2026()
        law["state"] = ny_state_cache(**over)
        return law

    def test_no_projection_at_all(self):
        assert P.project_state_tax(_proj(), None, self._law(), "MFJ",
                                   resident_state="NY") is None

    def test_the_measured_case_that_produced_a_phantom_refund(self):
        """A Manhattan single filer on $200,000 of wages, entering $17,500 of
        combined withholding as `/tax-withholding` instructs, was shown a state
        BALANCE of -$5,800 — a projected REFUND — against a city tax this
        function never computed. Over-stating a refund is the hard-error
        direction, and a banner does not turn it into a degrade."""
        st = P.project_state_tax(_proj(wages=200000, state_withholding=17500),
                                 None, self._law(), "Single",
                                 resident_state="NY")
        assert st is None, (
            f"a balance of {st.balance} was published against a line-39 tax of "
            f"{st.tax} and all-four-taxes withholding of {st.withholding}")

    def test_the_refusal_is_keyed_on_the_BLOCK_not_a_state_name(self):
        """A cache declaring a recapture is a cache whose plain bracket math is
        incomplete, whoever it belongs to. Strip the block and the same cache
        projects again — which is what makes this a property of the DATA."""
        law = self._law()
        del law["state"]["tax_table_benefit_recapture"]
        st = P.project_state_tax(_proj(), None, law, "MFJ", resident_state="NY")
        assert st is not None and st.tax > 0

    def test_an_unfetched_recapture_does_not_raise_either(self):
        """It used to hard-error through `compute_ny_tax`. There is nothing to
        refuse now — the branch never computes — and a ProjectionError on a
        workspace that simply has no New York projection would be a gate firing
        on correct data."""
        law = self._law(tax_table_benefit_recapture={"phase_in_band": 50000})
        assert P.project_state_tax(_proj(), None, law, "MFJ",
                                   resident_state="NY") is None

    def test_a_state_it_never_prices_asks_no_deduction(self):
        """AA21's state-deduction refusal ran before this branch, so a New York
        run that left the state figure out refused the whole report, federal
        included, on an answer the branch never reads. Every early return is
        swept at the function level below."""
        report = P.build_report(
            {"pay_frequency": "biweekly", "ytd": {"wages": 50000},
             "forecast_overrides": {"itemized_deductions": 0,
                                    "charitable_cash_gifts": 0}},
            {"filing_status": "MFJ", "address": {"state": "NY"}},
            self._law(), None, 2026, datetime.date(2026, 7, 2))
        assert report.state is None
        assert "is not projected here" in report.state_unsupported_note


class TestTheNewYorkSection:
    """`new_york_it201` is the third state section, and it is registered only
    now — the spec's own rule, because a key registered before anything
    computes into it is a scaffold that reads as coverage."""

    def test_registered_in_both_directions(self):
        assert "new_york_it201" in P.STATE_SECTION_KEYS
        assert P._STATE_SECTION_CODES["new_york_it201"] == "NY"
        # regression pins: the two that were there first
        assert "california_540" in P.STATE_SECTION_KEYS
        assert "florida_state" in P.STATE_SECTION_KEYS

    def test_a_prior_section_identifies_new_york_specifically(self):
        """`_detect_prior_state_code` exists because collapsing every modeled
        state into one "some state section exists" signal let an unrelated
        prior section vouch for a completely different current-year cache."""
        prior = {"new_york_it201": {"line_46_total_state_tax": 4000}}
        assert P._detect_prior_state_code(prior) == "NY"
        assert P._detect_prior_state(prior) == "new_york_it201"

    def test_parsed_withholding_does_not_vouch_for_template_zeros(self):
        """W-2 box 17 lands in line 72 before Form IT-201 is computed at all,
        so line 72 proves nothing about lines 33, 39 or 46. The California
        twin of this check was a review finding on PR #35."""
        scaffold = {"new_york_it201": {
            "_include_if": "state is NY",
            "line_33_nyagi": 0, "line_34_deduction": 0,
            "line_39_ny_tax": 0, "line_46_total_state_tax": 0,
            "refundable_credits_and_payments": {
                "line_72_state_withholding": 6200,
                "line_73_city_withholding": 1800}}}
        assert P._ny_section_computed(scaffold) is False

    def test_a_computed_zero_tax_return_is_still_evidence(self):
        """New York's standard deduction has a floor for every filing status,
        including the filer who can be claimed as a dependent, so a genuinely
        computed return carries a non-zero line 34 even at zero income."""
        computed = {"new_york_it201": {
            "line_33_nyagi": 0, "line_39_ny_tax": 0,
            "line_46_total_state_tax": 0, "line_34_deduction": 16050}}
        assert P._ny_section_computed(computed) is True

    def test_a_negative_new_york_agi_is_evidence_too(self):
        """Line 33 has NO floor — a Schedule C loss, a K-1 loss or an NOL
        carryforward reaches it routinely — and a negative number is non-zero,
        so a loss year must not read as an uncomputed section."""
        loss_year = {"new_york_it201": {"line_33_nyagi": -18000}}
        assert P._ny_section_computed(loss_year) is True

    def test_the_shared_helper_did_not_change_californias_answer(self):
        """`_ca_section_computed` is now a thin call on `_section_computed`;
        the behaviour it guards is the same one PR #35 landed."""
        scaffold_with_wh = {"california_540": {
            "ca_total_tax": 0, "ca_agi": 0, "ca_standard_deduction": 0,
            "ca_withholding_box17": 4500}}
        assert P._ca_section_computed(scaffold_with_wh) is False
        assert P._ca_section_computed(
            {"california_540": {"ca_standard_deduction": 11080}}) is True

    def test_only_a_finite_non_zero_NUMBER_counts_as_evidence(self):
        """`if v:` would pass every one of these. `json.loads` accepts NaN and
        Infinity, both of which are numbers and neither of which is an amount;
        a bool is an int in Python and a scaffold string is truthy. Each would
        vouch for a section nothing computed."""
        for value in (True, float("nan"), "16,050", [16050], {"amount": 16050}):
            assert P._ny_section_computed(
                {"new_york_it201": {"line_34_deduction": value}}) is False, value
        # Infinity IS non-zero and finite-looking to a truth test; it is not a
        # deduction, and the same guard catches it.
        assert P._ny_section_computed(
            {"new_york_it201": {"line_34_deduction": float("inf")}}) is False
        assert P._ny_section_computed(
            {"new_york_it201": {"line_34_deduction": 16050}}) is True

    def test_a_non_dict_section_is_not_evidence(self):
        for junk in (None, [], "computed", 4000):
            assert P._section_computed({"new_york_it201": junk},
                                       "new_york_it201",
                                       P._NY_COMPUTED_EVIDENCE_FIELDS) is False

    def test_the_evidence_fields_are_all_real_template_fields(self):
        """A field name that does not exist in the template can never be
        non-zero, so the whole check would quietly answer False forever."""
        template = json.loads(
            (REPO / "agents/templates/result_template.json").read_text())
        section = template["new_york_it201"]
        for field in P._NY_COMPUTED_EVIDENCE_FIELDS:
            assert field in section, field

    def test_no_evidence_field_can_be_filled_by_a_parser(self):
        """The rule that picks these: an output of the COMPUTATION, never a
        figure that can arrive from parsing a W-2."""
        assert not [f for f in P._NY_COMPUTED_EVIDENCE_FIELDS
                    if "withholding" in f or "payment" in f]


class TestTheNewYorkVoucherSchedule:
    """IT-2105: four EQUAL quarters on the federal statutory dates, observed
    under New York's own rule."""

    def test_it_is_not_californias_split(self):
        """30/40/0/30 with no September instalment would tell a New York
        filer to skip a payment they owe."""
        as_of = datetime.date(2026, 1, 2)
        ny = P.compute_ny_estimated_vouchers(40000.0, as_of)
        assert [v.quarter for v in ny] == [1, 2, 3, 4]
        assert [v.amount for v in ny] == [10000.0] * 4
        ca = P.compute_ca_estimated_vouchers(40000.0, as_of)
        assert [v.amount for v in ca] != [v.amount for v in ny]

    def test_new_york_rolls_weekends_only_and_the_federal_rule_rolls_further(self):
        """April 15 2022 was a FRIDAY — no weekend to roll — but the federal
        deadline moved to April 18 for DC's Emancipation Day. New York is not
        in the conforming set, because General Construction Law §§24 and 25-a
        have not been read here, so the state helper leaves it on the 15th.

        A date published EARLIER than the statute allows can never make a
        payment late, which is why the degrade is the safe direction — and
        why `_NY_CALENDAR` says so on the schedule rather than leaving the
        reader to find out."""
        as_of = datetime.date(2022, 1, 2)
        ny = {v.quarter: v.due_date
              for v in P.compute_ny_estimated_vouchers(4000.0, as_of)}
        fed = {v.quarter: v.due_date
               for v in P.compute_estimated_vouchers(4000.0, as_of)}
        assert ny[1] == "2022-04-15"
        assert fed[1] == "2022-04-18"
        assert ny[1] != fed[1]

    def test_a_weekend_still_rolls(self):
        """The degrade is weekends-ONLY, not no-rollover: June 15 2025 was a
        Sunday and a payment date on a Sunday is not a payment date."""
        as_of = datetime.date(2025, 1, 2)
        ny = {v.quarter: v.due_date
              for v in P.compute_ny_estimated_vouchers(4000.0, as_of)}
        assert ny[2] == "2025-06-16"

    def test_the_calendar_names_the_form_and_the_rollover_caveat(self):
        assert P._NY_CALENDAR.form == "IT-2105"
        assert P._NY_CALENDAR.observed_due is P._ny_observed_due
        assert "WEEKENDS ONLY" in P._NY_CALENDAR.due_date_caveat
        assert "§§24 and 25-a" in P._NY_CALENDAR.due_date_caveat


def test_the_declared_deductions_are_read_as_answers():
    """AA21. Absent or null is unanswered, which the projection refuses; 0 is
    the standard deduction. `_nonneg_num` reads None as 0, which here would
    be the silent standard deduction, so the two are kept apart."""
    def read(overrides):
        return P.annualize({"wages": 50000}, datetime.date(2026, 7, 1),
                           "biweekly", overrides, {}, {})
    p = read({})
    assert (p.itemized_deductions, p.state_itemized_deductions) == (None, None)
    p = read({"itemized_deductions": None, "state_itemized_deductions": 0})
    assert (p.itemized_deductions, p.state_itemized_deductions) == (None, 0.0)
    assert read({"itemized_deductions": 45000.5}).itemized_deductions == 45000.5
    # A numeric string parses, as every projection input's does (`_num`).
    assert read({"itemized_deductions": "45000"}).itemized_deductions == 45000.0
    for bad in (-1, True, float("nan"), "forty-five"):
        with pytest.raises(P.ProjectionError, match="itemized_deductions"):
            read({"itemized_deductions": bad})
    # AA22's cash gifts are the same kind of answer: absent is not none.
    assert read({}).charitable_cash_gifts is None
    assert read({"charitable_cash_gifts": 0}).charitable_cash_gifts == 0.0
    with pytest.raises(P.ProjectionError, match="charitable_cash_gifts"):
        read({"charitable_cash_gifts": -1})


def test_the_projection_prices_from_the_declared_deduction():
    """AA21: the projection subtracted the cache's standard deduction for
    every filer. `_proj` is $100,000 of wages on a joint return, a $30,000
    standard deduction and brackets of 10% to $20,000 and 20% to $200,000:
    - declared 0: $70,000 taxable, $2,000 + $10,000 = $12,000;
    - declared $45,000 of itemized deductions: $55,000 taxable, $2,000 +
      $7,000 = $9,000. On the standard deduction that itemizer's tax, and
      every payment it drives, read $3,000 high;
    - declared $25,000, under the standard deduction: the standard, $12,000.
    """
    def federal(declared):
        return P.project_federal_tax(_proj(itemized_deductions=declared),
                                     _profile_mfj(), _tax_law_2026())
    standard = federal(0)
    assert (standard.total_tax, standard.deduction, standard.itemized) == (12000.0, 30000.0, False)
    itemized = federal(45000)
    assert (itemized.total_tax, itemized.deduction, itemized.itemized) == (9000.0, 45000.0, True)
    under = federal(25000)
    assert (under.total_tax, under.deduction, under.itemized) == (12000.0, 30000.0, False)
    with pytest.raises(P.ProjectionError,
                       match="forecast_overrides.itemized_deductions is not set"):
        federal(None)


def test_the_state_projection_prices_from_the_declared_state_deduction():
    """AA21, the state side. $100,000 of state AGI, an $11,000 standard
    deduction, 1% to $20,000 and 5% above:
    - declared 0: $89,000 taxable, $200 + $3,450 = $3,650;
    - declared $30,000 of state itemized deductions: $70,000 taxable, $200 +
      $2,500 = $2,700."""
    prior = _Prior({"california_540": {"ca_total_tax": 5000}})

    def state(declared, prior=prior):
        return P.project_state_tax(_proj(state_itemized_deductions=declared),
                                   prior, _tax_law_2026(), filing_status="MFJ")
    standard = state(0)
    assert (standard.tax, standard.deduction, standard.itemized) == (3650.0, 11000.0, False)
    itemized = state(30000)
    assert (itemized.tax, itemized.deduction, itemized.itemized) == (2700.0, 30000.0, True)
    with pytest.raises(P.ProjectionError,
                       match="forecast_overrides.state_itemized_deductions is not set"):
        state(None)


def test_the_report_names_the_deduction_it_priced_from():
    report = _build_report(
        inputs={"pay_frequency": "biweekly",
                "ytd": {"wages": 50000, "federal_withholding": 6000},
                "forecast_overrides": {"itemized_deductions": 45000,
                                       "state_itemized_deductions": 30000},
                "estimated_payments_made": {"federal": 0, "state": 0}},
        profile=_profile_mfj(), tax_law=_tax_law_2026(),
        prior=_Prior({"california_540": {"ca_total_tax": 5000},
                      "form_1040": {"line_24_total_tax": 15000,
                                    "line_11_agi": 120000}}),
        year=2026, as_of=datetime.date(2026, 7, 2))
    md = P.render_markdown(report)
    assert "| Deduction (itemized) | $45,000 |" in md
    assert "| State (CA) deduction (itemized) | $30,000 |" in md


def test_an_unfetched_federal_standard_deduction_refuses():
    """`_num` reads None as 0 and the template seeds the standard deduction
    None, so a half-fetched cache priced a declared-standard filer on no
    deduction: $100,000 taxable, $2,000 + $16,000 = $18,000 of tax against
    the $12,000 a $30,000 standard deduction gives. Refused absent or null,
    as the state side is in the test below."""
    law = _tax_law_2026()
    law["federal"]["filing_statuses"]["mfj"]["standard_deduction"] = None
    with pytest.raises(P.ProjectionError,
                       match="mfj.standard_deduction is unfetched"):
        P.project_federal_tax(_proj(), _profile_mfj(), law)
    del law["federal"]["filing_statuses"]["mfj"]["standard_deduction"]
    with pytest.raises(P.ProjectionError,
                       match="mfj.standard_deduction is unfetched"):
        P.project_federal_tax(_proj(), _profile_mfj(), law)


def test_a_separate_filer_is_told_the_spouse_can_force_the_other_deduction():
    """A spouse's choice can force a separate filer onto the smaller
    deduction, which the projection prices as the larger: under-stated
    advisory output, which AGENTS.md §4 lets degrade only with a banner.
    Federal: only where the standard deduction was taken, since §63(c)(6)(A)
    denies it once the spouse itemizes. State: wherever one is projected,
    since California's both-or-neither rule binds either way."""
    ns = types.SimpleNamespace
    std = ns(itemized=False, charitable_non_itemizer_deduction=0.0)
    item = ns(itemized=True)
    ca, va = ns(code="CA"), ns(code="VA")
    assert P._mfs_deduction_notes("MFJ", std, ca) == (None, None)
    federal, state = P._mfs_deduction_notes("MFS", std, ca)
    assert "§63(c)(6)(A)" in federal
    assert "read low by the tax on the difference" in federal
    assert "non-itemizers" not in federal
    # Gifts can make the standard deduction win against a larger itemized
    # total, so the note no longer calls that total the smaller.
    assert "the smaller" not in federal
    # AA22: itemizing gives up the §170(p) deduction the projection took too.
    gave = ns(itemized=False, charitable_non_itemizer_deduction=1000.0)
    assert ("includes the charitable deduction for non-itemizers taken here"
            in P._mfs_deduction_notes("MFS", gave, None)[0])
    assert "California makes separate filers match" in state
    assert P._mfs_deduction_notes("MFS", item, None) == (None, None)
    assert "tie that choice" in P._mfs_deduction_notes("MFS", item, va)[1]

    # Through `build_report`, with gifts: the namespaces above stand in for
    # the projection, and the sentence has to reach the real one too.
    law = _gift_law()
    law["federal"]["filing_statuses"]["mfs"] = {
        "standard_deduction": 15000,
        "brackets": [{"rate": 0.10, "min": 0, "max": None}]}
    report = _report({"charitable_cash_gifts": 500},
                     profile={"filing_status": "MFS"}, tax_law=law,
                     prior=_Prior({"california_540": {"ca_total_tax": 5000}}))
    assert "§63(c)(6)(A)" in report.mfs_deduction_note
    assert ("includes the charitable deduction for non-itemizers taken here"
            in report.mfs_deduction_note)
    assert "California makes" in report.state_mfs_deduction_note
    md = P.render_markdown(report)
    assert f"> **⚠ {report.mfs_deduction_note}**" in md
    assert f"> **⚠ {report.state_mfs_deduction_note}**" in md
    payload = P._report_to_dict(report)
    assert payload["mfs_deduction_note"] == report.mfs_deduction_note
    assert payload["state_mfs_deduction_note"] == report.state_mfs_deduction_note


def test_the_federal_table_adds_up_with_every_deduction():
    """Schedule 1-A, QBI and AA22's gifts sit between AGI and taxable income
    beside the one AA21 added, and the table named only that one. `_proj` on
    the joint cache:
    - a $10,000 Schedule 1-A override: $100,000 - $30,000 - $10,000 =
      $60,000 taxable;
    - $20,000 of SE income: SE tax $20,000 x 92.35% x 15.3% = $2,825.91,
      half $1,412.96, AGI $118,587.04; QBI 20% x ($20,000 - $1,412.96) =
      $3,717.41; $118,587.04 - $30,000 - $3,717.41 = $84,869.63."""
    law = _gift_law()
    one_a = P.project_federal_tax(_proj(schedule_1a_total=10000),
                                  _profile_mfj(), law, requested_year=2026)
    assert (one_a.schedule_1a_deduction, one_a.taxable_income) == (10000.0, 60000.0)
    se = P.project_federal_tax(_proj(self_employment_net=20000),
                               _profile_mfj(), law, requested_year=2026)
    assert (se.agi, se.qbi_deduction, se.taxable_income) == (
        118587.04, 3717.41, 84869.63)
    report = _report({"schedule_1a": 10000, "self_employment_net": 20000,
                      "charitable_cash_gifts": 1500}, tax_law=law)
    f = report.federal
    assert f.schedule_1a_deduction == 10000.0 and f.qbi_deduction > 0
    assert f.charitable_non_itemizer_deduction == 1500.0
    assert round(f.agi - f.deduction - f.schedule_1a_deduction
                 - f.charitable_non_itemizer_deduction - f.qbi_deduction,
                 2) == f.taxable_income
    md = P.render_markdown(report)
    assert "| Schedule 1-A deduction | $10,000 |" in md
    assert "| Charitable deduction for non-itemizers | $1,500 |" in md
    assert f"| QBI deduction | {P._money(f.qbi_deduction)} |" in md
    assert (P._report_to_dict(report)["federal"]
            ["charitable_non_itemizer_deduction"] == 1500.0)
    plain = P.render_markdown(_report(tax_law=law))
    assert "Schedule 1-A deduction" not in plain and "QBI deduction |" not in plain
    assert "non-itemizers" not in plain


def _state_law(**state):
    """`_tax_law_2026` with a flat 5% California section, `state` overriding
    its keys."""
    law = _tax_law_2026()
    law["state"] = dict({"code": "CA", "type": "graduated_income",
                         "brackets": [{"rate": 0.05, "min": 0, "max": None}],
                         "standard_deduction": {"mfj": 11000}}, **state)
    return law


def test_an_unfetched_state_standard_deduction_refuses():
    """The state side refused only a status key present at None: an absent
    map, or a status the map leaves out even after its stand-in, reached
    `_num` as 0. On `_proj`'s $100,000 at 5% that is $5,000 of state tax
    against $4,450 on the $11,000 joint deduction, an over-stated 540-ES
    schedule."""
    ok = P.project_state_tax(_proj(), None, _state_law(), "MFJ",
                             resident_state="CA")
    assert (ok.deduction, ok.tax) == (11000.0, 4450.0)
    absent = _state_law()
    del absent["state"]["standard_deduction"]
    for law in (absent, _state_law(standard_deduction={}),
                _state_law(standard_deduction={"single": 5500}),
                _state_law(standard_deduction={"mfj": None})):
        with pytest.raises(P.ProjectionError,
                           match="state.standard_deduction.mfj is unfetched"):
            P.project_state_tax(_proj(), None, law, "MFJ", resident_state="CA")


@pytest.mark.parametrize("law, resident", [
    (_state_law(code="FL", type="none"), "FL"),
    (_state_law(code="FLA"), "FL"),
    (_state_law(code="FL"), "FL"),
    (_state_law(brackets=[]), "CA"),
    (_state_law(), "NY"),
    (_state_law(code="TN"), "TN"),
    (_state_law(), None),
    (dict(_tax_law_2026(), state=ny_state_cache()), "NY"),
], ids=["type-none", "invalid-code", "no-tax-code", "no-brackets",
        "cache-mismatch", "tennessee", "no-resident-or-prior", "recapture"])
def test_a_state_it_never_prices_is_not_refused_for_its_deduction(law, resident):
    """Every early return before the state deduction is read: moving
    `_projected_deduction` above any of them refuses the whole report,
    federal included, on an answer that state never reads."""
    assert P.project_state_tax(_proj(state_itemized_deductions=None), None,
                               law, "MFJ", resident_state=resident) is None


def test_the_shipped_template_refuses_until_every_declaration_is_answered(
        tmp_path, capsys):
    """`_cli_workspace` declares the deductions and the gifts for the tests
    that predate them, so no CLI test ran the template itself: a change
    reading its nulls as 0 would have left every test green. No state is
    projected here, so the state figure is never read."""
    template = json.loads((REPO / "agents" / "templates"
                           / "projection_inputs_template.json").read_text())
    template["ytd"]["wages"] = 50000
    args = _cli_workspace(tmp_path, {})
    for key in ("charitable_cash_gifts", "itemized_deductions"):
        (tmp_path / "projection_inputs.json").write_text(json.dumps(template))
        assert P._main(args + ["--year", "2026"]) == 1
        assert (f"forecast_overrides.{key} is not set"
                in capsys.readouterr().err)
        template["forecast_overrides"][key] = 0
    (tmp_path / "projection_inputs.json").write_text(json.dumps(template))
    assert P._main(args + ["--year", "2026"]) == 0


def _schedule_a_forecast(agi=167000, **expenses):
    return TC.compute_schedule_a(
        agi, "MFJ", 2026, 30000, 40400, modified_agi=agi,
        top_bracket_income=0, non_itemizer_charitable=0, **expenses)["inputs"]


def _forecast_law():
    law = _tax_law_2026()
    law["federal"]["salt_cap"] = {"amount": 40400, "mfs_amount": 20200}
    law["federal"]["charitable_non_itemizer"] = {"amount": 1000, "joint_amount": 2000}
    return law


@pytest.mark.parametrize("wages,expected", [(167000, 30475), (177000, 30000)])
def test_schedule_a_forecast_prices_a_standard_election(wages, expected):
    # Schedule A: medical $20,000 less 7.5% AGI, plus $23,000 mortgage.
    # At $177,000 the itemized $29,725 loses to standard $30,000.
    inputs = _schedule_a_forecast(medical_expenses=20000, mortgage_interest=23000)
    f = P.project_federal_tax(_proj(wages=wages, itemized_deductions=None,
                                    schedule_a_inputs=inputs), _profile_mfj(), _forecast_law())
    assert f.deduction == expected
    assert f.taxable_income == wages - expected
    assert f.schedule_a_repriced
    assert P._itemized_deduction_note(f, None) is None


def test_schedule_a_forecast_prices_qbi_before_the_haircut():
    # $80k SE × .9235 × .153 / 2 = $5,651.82 half-SE adjustment.
    # QBI ($80k - $5,651.82) × .2 = $14,869.64; §68 base
    # $224,348.18 - $14,869.64 - $200k = $9,478.54.
    # Haircut $9,478.54 × 2/37 = $512.35 off $45k mortgage.
    inputs = _schedule_a_forecast(mortgage_interest=45000)
    f = P.project_federal_tax(
        _proj(wages=150000, ss_wages=0, self_employment_net=80000, schedule_a_inputs=inputs),
        _profile_mfj(), _forecast_law())
    assert f.agi == 224348.18
    assert f.qbi_deduction == 14869.64
    assert f.deduction == 44487.65
    assert f.taxable_income == 164990.89


def test_schedule_a_forecast_rebuilds_magi_with_its_addback():
    inputs = _schedule_a_forecast(state_local_income_taxes=30000, mortgage_interest=30000)
    inputs["magi_addback"] = 500000
    # At AGI $100k, MAGI $600k: $40,400 - .30 × ($600k-$505k) = $11,900.
    f = P.project_federal_tax(_proj(schedule_a_inputs=inputs), _profile_mfj(), _forecast_law())
    assert f.deduction == 41900
    assert any("phasedown" in n for n in f.schedule_a_notes)


@pytest.mark.parametrize("field,value", [("tax_year", 2025), ("filing_status", "Single"),
                                        ("standard_deduction", 31000), ("salt_cap", 40000)])
def test_schedule_a_forecast_refuses_wrong_year_status_or_law(field, value):
    inputs = _schedule_a_forecast(mortgage_interest=45000)
    inputs[field] = value
    with pytest.raises(P.ProjectionError):
        P.project_federal_tax(_proj(schedule_a_inputs=inputs), _profile_mfj(), _forecast_law())


def test_schedule_a_forecast_requires_explicit_binding_gift_classifications():
    inputs = _schedule_a_forecast(charitable_cash=150000)
    p = _proj(schedule_a_inputs=inputs, charitable_cash_gifts=150000)
    with pytest.raises(P.ProjectionError, match="Binding charitable caps"):
        P.project_federal_tax(p, _profile_mfj(), _forecast_law())
    from dataclasses import replace
    f = P.project_federal_tax(replace(p, schedule_a_charitable_limits_confirmed=True),
                              _profile_mfj(), _forecast_law())
    # Schedule A's public-charity cash cap $100k × .60, less .5% AGI floor.
    assert f.deduction == 59500


def test_a_declared_itemized_total_is_flagged_as_held():
    """The declared total is priced as entered, while its SALT phasedown and
    medical floor move with the projected AGI: a total worked out at another
    income reads the tax high or low. Set wherever either side itemized."""
    ns = types.SimpleNamespace
    std, item = ns(itemized=False), ns(itemized=True)
    assert P._itemized_deduction_note(std, None) is None
    assert P._itemized_deduction_note(std, std) is None
    assert "SALT cap's phasedown" in P._itemized_deduction_note(item, None)
    assert "medical-expense floor" in P._itemized_deduction_note(std, item)

    report = _report({"itemized_deductions": 45000})
    assert report.federal.itemized and report.itemized_deduction_note
    assert (f"- **Declared itemized deductions:** "
            f"{report.itemized_deduction_note}") in P.render_markdown(report)
    assert (P._report_to_dict(report)["itemized_deduction_note"]
            == report.itemized_deduction_note)
    assert _report().itemized_deduction_note is None


def test_a_state_estimate_names_a_deduction_the_federal_choice_can_deny():
    """Some states require the federal choice, and the projection prices each
    side's larger deduction on its own, so where the two differ the state
    figure can be one the state would not allow. California lets them differ
    and is modeled, so it gets no such sentence."""
    ns = types.SimpleNamespace
    va_item, va_std = ns(code="VA", itemized=True), ns(code="VA", itemized=False)
    note = P._state_bracket_estimate_note(va_item, None, federal_itemized=False)
    assert "took the itemized deduction where the federal projection took the standard" in note
    assert "require the federal choice" in note
    note = P._state_bracket_estimate_note(va_std, None, federal_itemized=True)
    assert "took the standard deduction where the federal projection took the itemized" in note
    for state, fed in ((va_item, True), (va_std, False)):
        assert "federal choice" not in P._state_bracket_estimate_note(
            state, None, federal_itemized=fed)
    assert P._state_bracket_estimate_note(va_item, object(),
                                          federal_itemized=False) is None

    report = _report({"state_itemized_deductions": 20000},
                     profile={"filing_status": "MFJ", "address": {"state": "VA"}},
                     tax_law=_state_law(code="VA", standard_deduction={
                         "single": 8000, "mfj": 16000}))
    assert report.state.itemized and not report.federal.itemized
    assert "require the federal choice" in report.state_bracket_estimate_note


def _gift_law():
    """`_tax_law_2026` with §170(p)'s caps fetched: $1,000, $2,000 joint."""
    law = _tax_law_2026()
    law["federal"]["charitable_non_itemizer"] = {"amount": 1000,
                                                 "joint_amount": 2000}
    return law


def _gift_federal(gifts, itemized=0, law=None, profile=None):
    """`project_federal_tax` on `_proj` with the year's gifts declared."""
    return P.project_federal_tax(
        _proj(itemized_deductions=itemized, charitable_cash_gifts=gifts),
        profile or _profile_mfj(), law or _gift_law())


def test_a_standard_deduction_filer_deducts_cash_gifts_from_ty2026():
    """AA22: the projection subtracted only the larger of the standard and
    the declared itemized deduction, so §170(p)'s cash gifts stayed in
    taxable income. `_proj` is $100,000 of wages, $30,000 joint standard
    deduction, 10% to $20,000 and 20% above; $15,000 single, 10% to $10,000:
    - joint, $1,500 of gifts: $68,500 taxable, $2,000 + $9,700 = $11,700,
      where no gifts is $12,000;
    - joint, $5,000: capped at $2,000, $68,000 taxable, $11,600;
    - single, $5,000: capped at $1,000, $84,000 taxable, $1,000 + $14,800
      = $15,800."""
    some = _gift_federal(1500)
    assert (some.charitable_non_itemizer_deduction, some.taxable_income,
            some.total_tax) == (1500.0, 68500.0, 11700.0)
    capped = _gift_federal(5000)
    assert (capped.charitable_non_itemizer_deduction, capped.total_tax) == (
        2000.0, 11600.0)
    single = _gift_federal(5000, profile={"filing_status": "Single"})
    assert (single.charitable_non_itemizer_deduction, single.taxable_income,
            single.total_tax) == (1000.0, 84000.0, 15800.0)
    assert _gift_federal(0).total_tax == 12000.0
    with pytest.raises(P.ProjectionError,
                       match="forecast_overrides.charitable_cash_gifts is not set"):
        _gift_federal(None)
    # Before TY2026 there is no such deduction: the figure is not read, so
    # neither refused nor applied, through `build_report` too, which no
    # helper here backfills.
    for gifts in (None, 5000):
        before = _gift_federal(gifts, law=dict(_gift_law(), tax_year=2025))
        assert (before.charitable_non_itemizer_deduction,
                before.taxable_income) == (0.0, 70000.0)
    report = P.build_report(
        {"pay_frequency": "biweekly", "ytd": {"wages": 50000},
         "forecast_overrides": {"itemized_deductions": 0,
                                "state_itemized_deductions": 0}},
        _profile_mfj(), dict(_gift_law(), tax_year=2025), None, 2025,
        datetime.date(2025, 7, 2))
    assert report.federal.charitable_non_itemizer_deduction == 0.0
    # A cache that cannot show the year being projected, having none, a
    # string or another year than the one requested, refuses gifts other
    # than 0, unanswered ones included, rather than read them as 0, which is
    # the over-statement. No gifts have nothing to read.
    yearless = _gift_law()
    del yearless["tax_year"]
    for law, requested in ((yearless, 2026), (yearless, None),
                           (dict(_gift_law(), tax_year="2026"), None),
                           (dict(_gift_law(), tax_year=2025), 2026)):
        for gifts in (1500, None):
            with pytest.raises(P.ProjectionError,
                               match="does not show that it prices the year being projected"):
                P.project_federal_tax(_proj(charitable_cash_gifts=gifts),
                                      _profile_mfj(), law, requested_year=requested)
        assert P.project_federal_tax(
            _proj(charitable_cash_gifts=0), _profile_mfj(), law,
            requested_year=requested).total_tax == 12000.0, (law, requested)


def test_cash_gifts_can_tip_the_choice_to_the_standard_deduction():
    """/tax-compute weighs the itemized total against the standard deduction
    plus §170(p), and the projection now does too. On the $30,000 joint
    standard deduction:
    - $31,000 itemized, $1,500 of gifts: $31,500 standard side wins,
      $68,500 taxable where itemizing is $69,000;
    - $31,000 itemized, $500 of gifts: $30,500, so itemize, and an
      itemizer's gifts earn nothing below the line;
    - $33,000 itemized, $5,000 of gifts capped at $2,000: $32,000, so
      itemize, $67,000 taxable. The cap is read here, since all $5,000 could
      have tipped it."""
    for itemized, gifts, expected in (
            (31000, 1500, (False, 30000.0, 1500.0, 68500.0)),
            (31000, 500, (True, 31000.0, 0.0, 69000.0)),
            (33000, 5000, (True, 33000.0, 0.0, 67000.0))):
        f = _gift_federal(gifts, itemized)
        assert (f.itemized, f.deduction, f.charitable_non_itemizer_deduction,
                f.taxable_income) == expected, (itemized, gifts)


def test_the_cap_is_read_only_where_it_can_decide():
    """An older cache fetched §170(p)'s cap only for a standard-deduction
    filer. $45,000 itemized beats $30,000 plus all $5,000 of gifts whatever
    the cap, and the engine prices no gifts at 0 before it reads the cap, so
    neither reads it. Anywhere else the cap is read, since short of the
    figure any cap up to the gifts could decide the choice: at $31,000, at
    $45,000 with $20,000 of gifts, and on the standard deduction, an
    unfetched one refuses, as the engine does, and names the one block to
    fetch rather than the whole setup."""
    law = _tax_law_2026()
    big = _gift_federal(5000, 45000, law=law)
    assert (big.itemized, big.charitable_non_itemizer_deduction) == (True, 0.0)
    assert _gift_federal(0, law=law).total_tax == 12000.0
    for itemized, gifts in ((31000, 5000), (45000, 20000), (0, 5000)):
        with pytest.raises(P.ProjectionError,
                           match=r"charitable_non_itemizer\.joint_amount is unfetched"
                                 r".*Fetch only federal\.charitable_non_itemizer"):
            _gift_federal(gifts, itemized, law=law)
    with pytest.raises(P.ProjectionError,
                       match="charitable_non_itemizer.amount is unfetched"):
        _gift_federal(500, law=law, profile={"filing_status": "Single"})
    # A block that is not an object is an unfetched one, not a traceback.
    scalar = _tax_law_2026()
    scalar["federal"]["charitable_non_itemizer"] = 2000
    with pytest.raises(P.ProjectionError,
                       match="charitable_non_itemizer.joint_amount is unfetched"):
        _gift_federal(500, law=scalar)


def test_cash_gifts_come_off_the_qbi_cap_base():
    """Line 13c sits above QBI, so it narrows the 20%-of-taxable-income cap.
    $40,000 of SE income alone, joint: SE tax $40,000 x 92.35% x 15.3% =
    $5,651.82, half $2,825.91, AGI $37,174.09; less $30,000 and $2,000 of
    gifts, $5,174.09 before QBI; QBI capped at 20% of it, $1,034.82;
    $4,139.27 taxable. Taking the gifts after QBI instead reads $3,739.27."""
    f = P.project_federal_tax(
        _proj(wages=0, ss_wages=0, medicare_wages=0, self_employment_net=40000,
              charitable_cash_gifts=2000),
        _profile_mfj(), _gift_law())
    assert (f.agi, f.charitable_non_itemizer_deduction, f.qbi_deduction,
            f.taxable_income) == (37174.09, 2000.0, 1034.82, 4139.27)


def test_a_charitable_deduction_says_which_gifts_qualify():
    """The engine's notes on §170(p), cash only, no donor-advised fund, a
    written acknowledgment from $250, reached no reader of the projection, so
    a declared gift that does not qualify read the tax low with nothing saying
    so. Set wherever the deduction was taken, and only there. Where the gifts
    tipped the choice, it says the itemized total was set aside, against the
    deduction the gifts were given and how little of them need qualify for
    itemizing to deduct more, the itemized total's margin over the standard
    deduction:
    - $31,000 itemized, $1,500 of gifts against $30,000: the $1,500, and
      under $1,000;
    - $31,500 itemized, $5,000 of gifts capped at $2,000: the $2,000, and
      under $1,500, where the gifts themselves would say $5,000;
    - $30,000, which the standard deduction alone matches: nothing was."""
    report = _report({"charitable_cash_gifts": 1500}, tax_law=_gift_law())
    note = report.charitable_gifts_note
    assert "donor-advised fund" in note and "written acknowledgment" in note
    assert "set aside" not in note
    for itemized, gifts, deducted, margin in ((31000, 1500, "$1,500", "$1,000"),
                                              (31500, 5000, "$2,000", "$1,500"),
                                              (30000, 1500, None, None)):
        tipped = _report({"charitable_cash_gifts": gifts,
                          "itemized_deductions": itemized},
                         tax_law=_gift_law()).charitable_gifts_note
        assert tipped.startswith(note), itemized
        set_aside = (" The itemized total you declared was set aside: it is "
                     "larger than the standard deduction, but not than the "
                     f"standard deduction plus the {deducted} deducted for "
                     f"these gifts. If under {margin} of them qualifies, "
                     "itemizing deducts more.")
        assert tipped == (note + set_aside if deducted else note), itemized
    assert f"- **Declared cash gifts:** {note}" in P.render_markdown(report)
    assert P._report_to_dict(report)["charitable_gifts_note"] == note
    assert _report(tax_law=_gift_law()).charitable_gifts_note is None
    itemizer = _report({"charitable_cash_gifts": 1500,
                        "itemized_deductions": 45000}, tax_law=_gift_law())
    assert itemizer.charitable_gifts_note is None
