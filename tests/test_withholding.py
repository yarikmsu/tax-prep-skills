"""Tests for agents/lib/withholding.py — federal withholding true-up."""
import datetime
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import withholding as wh


def test_read_liability_happy_path():
    result = {"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": 42000}}
    assert wh.read_liability(result, 2026) == 42000.0


def test_read_liability_wrong_year_raises():
    result = {"meta": {"tax_year": 2025}, "form_1040": {"line_24_total_tax": 42000}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)


def test_read_liability_missing_line_raises():
    result = {"meta": {"tax_year": 2026}, "form_1040": {}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)


def test_read_liability_negative_raises():
    result = {"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": -5}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)


def test_read_liability_zero_is_valid():
    # A genuine $0-tax return (e.g. liability fully offset) is valid, not an error.
    # The tax_year guard already rejects uncomputed/blank templates (tax_year is null).
    result = {"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": 0}}
    assert wh.read_liability(result, 2026) == 0.0


def test_build_inputs_projects_at_current_pace():
    # as_of June 30, 2026 -> day 181 of 365 -> frac ~0.4959
    as_of = datetime.date(2026, 6, 30)
    proj_inputs = {
        "pay_frequency": "biweekly",
        "ytd": {"federal_withholding": 10000},
        "estimated_payments_made": {"federal": 2000},
    }
    inp = wh.build_inputs(proj_inputs, as_of, "biweekly")
    frac = as_of.timetuple().tm_yday / 365
    assert inp.ytd_withholding == 10000.0
    assert inp.estimates_paid == 2000.0
    assert inp.projected_full_year_withholding == round(10000 / frac, 2)
    assert inp.projected_paid == round(round(10000 / frac, 2) + 2000, 2)
    assert inp.paychecks_remaining == max(0, round(26 * (1 - frac)))
    assert inp.pay_frequency == "biweekly"


def test_build_inputs_missing_blocks_default_zero():
    as_of = datetime.date(2026, 6, 30)
    inp = wh.build_inputs({"pay_frequency": "monthly"}, as_of, "monthly")
    assert inp.ytd_withholding == 0.0
    assert inp.estimates_paid == 0.0
    assert inp.projected_full_year_withholding == 0.0


class _FakePrior:
    """Stand-in for projection.load_prior_baseline's PriorReturn."""
    def __init__(self, total_tax, agi):
        self.result = {"form_1040": {"line_24_total_tax": total_tax, "line_11_agi": agi}}


def _proj_inputs(ytd_wh, est=0):
    return {
        "pay_frequency": "biweekly",
        "ytd": {"federal_withholding": ytd_wh},
        "estimated_payments_made": {"federal": est},
    }


def test_build_report_underpaid_both_targets():
    # Liability 40000; prior tax 30000, prior AGI 120000 (factor 1.00) -> safe harbor
    # required = min(0.9*40000=36000, 1.0*30000=30000) = 30000.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    frac = as_of.timetuple().tm_yday / 365
    # YTD withholding 10000 -> projected full-year = 10000/frac (~20165), no estimates.
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000), prior, 2026, as_of)
    proj_wh = round(10000 / frac, 2)
    assert r.liability == 40000.0
    assert r.degraded is False
    assert r.safe_harbor.required_annual == 30000.0
    assert r.penalty_target.name == "safe_harbor"
    assert r.penalty_target.target_amount == 30000.0
    assert r.penalty_target.shortfall == round(max(0.0, 30000 - proj_wh), 2)
    assert r.zero_target.name == "zero_balance"
    assert r.zero_target.target_amount == 40000.0
    assert r.zero_target.shortfall == round(max(0.0, 40000 - proj_wh), 2)
    # 4(c) per paycheck = shortfall / paychecks_remaining
    pr = max(0, round(26 * (1 - frac)))
    assert r.w4_penalty.per_paycheck == round(r.penalty_target.shortfall / pr, 2)
    assert r.zero_target.shortfall > r.penalty_target.shortfall


def test_build_report_covered_when_pace_exceeds_target():
    # High YTD withholding so projected full-year covers even the zero-balance target.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(30000), prior, 2026, as_of)
    assert r.penalty_target.covered is True
    assert r.penalty_target.shortfall == 0.0
    assert r.zero_target.covered is True
    assert r.w4_penalty.per_paycheck == 0.0


def test_build_report_degraded_mode_uses_90pct_only():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    r = wh.build_report(result, _proj_inputs(5000), None, 2026, as_of)
    assert r.degraded is True
    assert r.baseline_source is None
    # required = 0.90 * 40000 = 36000 (no prior-year test available)
    assert r.safe_harbor.required_annual == 36000.0


def test_build_report_prior_factor_110_above_threshold():
    # prior AGI 200000 (single) > 150000 -> factor 1.10 -> prior_based = 1.10*30000 = 33000
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 100000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 200000)
    r = wh.build_report(result, _proj_inputs(1000), prior, 2026, as_of)
    # min(0.9*100000=90000, 1.10*30000=33000) = 33000
    assert r.safe_harbor.required_annual == 33000.0


def test_render_markdown_contains_key_sections():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000), prior, 2026, as_of)
    md = wh.render_markdown(r)
    assert "# Withholding True-Up — TY2026 (as of 06-30)" in md
    assert "## Liability & payments" in md
    assert "## Gap to targets" in md
    assert "## Recommended actions" in md
    assert "W-4 line 4(c)" in md
    assert "1040-ES" in md


def test_render_markdown_degraded_note():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    r = wh.build_report(result, _proj_inputs(5000), None, 2026, as_of)
    md = wh.render_markdown(r)
    assert "DEGRADED MODE" in md


def test_report_to_dict_roundtrips_json():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000), prior, 2026, as_of)
    d = wh._report_to_dict(r)
    s = json.dumps(d, default=str)  # must be JSON-serializable
    back = json.loads(s)
    assert back["liability"] == 40000.0
    assert back["penalty_target"]["name"] == "safe_harbor"
    assert back["zero_target"]["name"] == "zero_balance"
    assert isinstance(back["vouchers_penalty"], list)
    assert isinstance(back["vouchers_zero"], list)


def test_vouchers_zero_exceeds_penalty_when_liability_above_safe_harbor():
    # Prior tax 30000 < liability 40000 -> safe harbor required (30000) < full liability.
    # The zero-balance voucher schedule must demand more per quarter than the
    # penalty-avoidance one, so the report offers an actionable path to zero balance.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(1000), prior, 2026, as_of)
    assert r.safe_harbor.required_annual < r.liability
    pen_total = sum(v.amount for v in r.vouchers_penalty)
    zero_total = sum(v.amount for v in r.vouchers_zero)
    assert zero_total > pen_total
    # The two schedules share identical quarters/due dates (same as_of).
    assert [v.due_date for v in r.vouchers_penalty] == [v.due_date for v in r.vouchers_zero]


def test_vouchers_zero_covers_full_gap_when_no_paychecks_remain():
    # as_of late in year -> 0 paychecks remaining -> W-4 4(c) is $0 for both targets,
    # so vouchers are the only lever. The zero-balance schedule must sum to the
    # zero-balance shortfall (the full gap), not merely the safe-harbor gap.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 12, 31)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000), prior, 2026, as_of)
    assert r.inputs.paychecks_remaining == 0
    assert r.w4_zero.per_paycheck == 0.0
    zero_total = round(sum(v.amount for v in r.vouchers_zero), 2)
    assert zero_total == r.zero_target.shortfall


FIX = Path(__file__).resolve().parent / "fixtures" / "withholding"


def test_main_json_output_ok(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "06-30", "--today", "2026-07-01", "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["year"] == 2026
    assert out["liability"] == 40000.0
    assert out["degraded"] is False  # archive/2025 present
    assert out["baseline_source"] == "archive/2025"


def test_main_markdown_output_ok(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "06-30", "--today", "2026-07-01",
    ])
    assert rc == 0
    assert "# Withholding True-Up — TY2026" in capsys.readouterr().out


def test_main_wrong_year_returns_1(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2025",
        "--as-of", "06-30", "--today", "2026-07-01",
    ])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err


def test_main_future_as_of_returns_1(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "12-31", "--today", "2026-07-01",
    ])
    assert rc == 1
    assert "future" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# E7: California true-up (Form 540 + FTB 5805 + 540-ES 30/40/0/30)
# ---------------------------------------------------------------------------

class _FakeCaPrior:
    def __init__(self, total_tax, agi, ca_tax, ca_agi):
        self.result = {
            "form_1040": {"line_24_total_tax": total_tax, "line_11_agi": agi},
            "california_540": {"ca_total_tax": ca_tax, "ca_agi": ca_agi},
        }


def _ca_result(ca_tax=8000, ca_agi=200000, fed_tax=40000):
    return {"meta": {"tax_year": 2026, "filing_status": "single"},
            "form_1040": {"line_24_total_tax": fed_tax},
            "california_540": {"ca_total_tax": ca_tax, "ca_agi": ca_agi}}


def _ca_proj_inputs(fed_wh=10000, state_wh=1000, state_est=0):
    return {
        "pay_frequency": "biweekly",
        "ytd": {"federal_withholding": fed_wh, "state_withholding": state_wh},
        "estimated_payments_made": {"federal": 0, "state": state_est},
    }


def test_read_ca_inputs_happy_and_absent():
    assert wh.read_ca_inputs(_ca_result()) == (8000.0, 200000.0)
    assert wh.read_ca_inputs({"meta": {}}) is None


def test_read_ca_inputs_requires_tax_and_agi():
    with pytest.raises(wh.WithholdingError):
        wh.read_ca_inputs({"california_540": {"ca_agi": 100000}})
    # Missing ca_agi would silently disable the R&TC §19136.3 high-income
    # check — the understating direction — so it hard-errors too.
    with pytest.raises(wh.WithholdingError):
        wh.read_ca_inputs({"california_540": {"ca_total_tax": 8000}})
    with pytest.raises(wh.WithholdingError):
        wh.read_ca_inputs({"california_540": "corrupt"})
    with pytest.raises(wh.WithholdingError):
        wh.read_ca_inputs({"california_540": {"ca_total_tax": -1, "ca_agi": 0}})


def test_read_ca_inputs_negative_agi_valid():
    # A loss year: negative CA AGI is legitimate (just not high-income).
    assert wh.read_ca_inputs(
        {"california_540": {"ca_total_tax": 0, "ca_agi": -5000}}) == (0.0, -5000.0)


def test_build_report_ca_block_hand_computed():
    # as_of Jun 30: frac = 181/365. YTD CA withholding 1000 ->
    # projected = round(1000 * 365/181, 2) = 2016.57; paid = 2016.57.
    # Prior CA tax 6000, prior CA AGI 120000 (<=150K -> 1.00) -> prior_based
    # 6000; pct90 = 7200 -> required 6000 -> shortfall 3983.43.
    # Zero shortfall = 8000 - 2016.57 = 5983.43.
    as_of = datetime.date(2026, 6, 30)
    frac = as_of.timetuple().tm_yday / 365
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), prior, 2026, as_of)
    assert r.ca is not None
    proj_wh = round(1000 / frac, 2)
    assert r.ca.liability == 8000.0
    assert r.ca.projected_full_year_withholding == proj_wh
    assert r.ca.safe_harbor.required_annual == 6000.0
    assert r.ca.safe_harbor.shortfall == round(6000 - proj_wh, 2)
    assert r.ca.zero_shortfall == round(8000 - proj_wh, 2)
    # Jun 30 -> Q2 gone; open Q3 (0 weight) + Q4 -> whole shortfall on Q4.
    assert [(v.quarter, v.amount) for v in r.ca.vouchers_penalty] == [
        (3, 0.0), (4, r.ca.safe_harbor.shortfall)]
    assert [(v.quarter, v.amount) for v in r.ca.vouchers_zero] == [
        (3, 0.0), (4, r.ca.zero_shortfall)]
    # DE 4 per-paycheck over the same remaining-paychecks base as federal.
    pr = r.inputs.paychecks_remaining
    assert r.ca.de4_penalty.per_paycheck == round(r.ca.safe_harbor.shortfall / pr, 2)
    assert r.ca.de4_zero.per_paycheck == round(r.ca.zero_shortfall / pr, 2)


def test_build_report_ca_none_without_section():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r = wh.build_report(result, _ca_proj_inputs(), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca is None
    md = wh.render_markdown(r)
    assert "California true-up" not in md
    assert "covered for CA returns only" in md


def test_build_report_ca_high_income_90_only():
    # Current-year CA AGI $1.2M -> R&TC §19136.3 forbids the prior-year
    # harbor even though prior CA tax (1000) would be far cheaper.
    prior = _FakeCaPrior(30000, 120000, 1000, 900000)
    r = wh.build_report(_ca_result(ca_tax=100000, ca_agi=1_200_000),
                        _ca_proj_inputs(state_wh=20000), prior, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca.safe_harbor.high_income_90_only is True
    assert r.ca.safe_harbor.required_annual == 90000.0
    assert "R&TC §19136.3" in wh.render_markdown(r)


def test_build_report_ca_degraded_without_prior():
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca.safe_harbor.degraded is True
    assert r.ca.safe_harbor.required_annual == round(0.9 * 8000, 2)


def test_render_markdown_ca_sections():
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), prior, 2026,
                        datetime.date(2026, 6, 30))
    md = wh.render_markdown(r)
    assert "## California true-up (Form 540)" in md
    assert "CA safe harbor (FTB 5805)" in md
    assert "DE 4 per-paycheck extra CA withholding" in md
    assert "CA 540-ES voucher schedule (30% / 40% / 0% / 30%)" in md
    assert "there is no September (Q3) payment" in md
    assert "R&TC §19011.5" in md  # mandatory e-pay caveat


def test_report_to_dict_includes_ca_and_roundtrips():
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), prior, 2026,
                        datetime.date(2026, 6, 30))
    back = json.loads(json.dumps(wh._report_to_dict(r), default=str))
    assert back["ca"]["liability"] == 8000.0
    assert back["ca"]["safe_harbor"]["required_annual"] == 6000.0
    assert isinstance(back["ca"]["vouchers_penalty"], list)
    # Non-CA report serializes ca as null.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r2 = wh.build_report(result, _ca_proj_inputs(), None, 2026,
                         datetime.date(2026, 6, 30))
    assert wh._report_to_dict(r2)["ca"] is None


def test_read_ca_inputs_scaffold_section_skipped():
    """P1: result_template.json ships an ALL-ZERO california_540 scaffold and
    /tax-setup copies it before /tax-compute step 10 runs — it must NOT
    become a confident 'CA tax $0 / covered' block."""
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000},
              "california_540": {"_include_if": "state is CA",
                                 "ca_filing_status": None,
                                 "ca_standard_deduction": 0,
                                 "ca_agi": 0, "ca_total_tax": 0,
                                 "ca_withholding_box17": 0, "note": None}}
    assert wh.read_ca_inputs(result) is None
    r = wh.build_report(result, _ca_proj_inputs(), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca is None
    assert "covered for CA returns only" in wh.render_markdown(r)


def test_read_ca_inputs_zero_tax_computed_return_still_counts():
    # A genuine $0-tax CA return has non-zero ca_agi → present, computed.
    result = {"california_540": {"ca_total_tax": 0, "ca_agi": 15000}}
    assert wh.read_ca_inputs(result) == (0.0, 15000.0)


def test_render_markdown_ca_catchup_and_de_minimis_notes():
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), prior, 2026,
                        datetime.date(2026, 6, 30))
    md = wh.render_markdown(r)
    assert "pay catch-up amounts NOW" in md
    assert "5805F" in md  # farmers/fishermen not-modeled caveat
    # De minimis: CA tax 1000, projected withholding ~700 (347/frac) ->
    # required 900 (degraded 90%) leaves a raw gap, but tax minus
    # withholding ~300 < $500 -> penalty target COVERED (shortfall zeroed,
    # DE 4 penalty figure $0) while the zero-balance target still shows
    # the ~$300 due.
    r2 = wh.build_report(_ca_result(ca_tax=1000, ca_agi=20000),
                         _ca_proj_inputs(state_wh=347), None, 2026,
                         datetime.date(2026, 6, 30))
    assert r2.ca.safe_harbor.de_minimis is True
    assert r2.ca.safe_harbor.shortfall == 0.0
    assert r2.ca.safe_harbor.covered is True
    assert r2.ca.de4_penalty.per_paycheck == 0.0
    assert r2.ca.zero_shortfall > 0
    assert "no CA underpayment penalty applies" in wh.render_markdown(r2)


def test_build_report_federal_prior_agi_missing_note():
    class _PriorNoAgi:
        result = {"form_1040": {"line_24_total_tax": 30000}}
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r = wh.build_report(result, _proj_inputs(10000), _PriorNoAgi(), 2026,
                        datetime.date(2026, 6, 30))
    assert r.safe_harbor.prior_agi_missing is True
    assert "may be UNDERSTATED" in wh.render_markdown(r)


def test_read_ca_inputs_box17_only_scaffold_skipped():
    """Codex P2: a CA section whose only non-zero field is parsed W-2 Box 17
    withholding is NOT a computed return — must not print a confident
    '$0 / covered' CA block."""
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000},
              "california_540": {"_include_if": "state is CA",
                                 "ca_standard_deduction": 0,
                                 "ca_agi": 0, "ca_total_tax": 0,
                                 "ca_withholding_box17": 4500,
                                 "ca_sdi_box14": 1200}}
    assert wh.read_ca_inputs(result) is None
    r = wh.build_report(result, _ca_proj_inputs(), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca is None
