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
                 "self_employment_net": 0, "other_income": 1000}
    recurring = {"taxable_interest": 800, "ordinary_dividends": 1200, "qualified_dividends": 900}
    proj = P.annualize(_ytd_half_year(), as_of, "biweekly", overrides, recurring,
                       {"federal": 0, "state": 0})
    assert proj.wages == pytest.approx(round(50000 / frac, 2) + 15000, abs=0.01)
    assert proj.federal_withholding == pytest.approx(round(6000 / frac, 2), abs=0.01)
    assert proj.capital_gains == 3000
    assert proj.other_income == 1000
    assert proj.taxable_interest == 800
    assert proj.qualified_dividends == 900
    assert proj.paychecks_remaining == P.paychecks_remaining("biweekly", frac)


def test_annualize_carries_estimates_and_defaults_medicare_wages_to_wages():
    as_of = datetime.date(2026, 7, 2)
    proj = P.annualize({"wages": 50000, "federal_withholding": 6000}, as_of, "monthly",
                       {}, {}, {"federal": 1500, "state": 300})
    assert proj.estimated_fed_paid == 1500
    assert proj.estimated_state_paid == 300
    assert proj.medicare_wages == proj.wages


def _tax_law_2026():
    return {
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
