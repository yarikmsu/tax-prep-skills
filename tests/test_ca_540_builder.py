"""TY2025 printed Form 540 arithmetic, using explicit synthetic law inputs."""

import copy
import json
from pathlib import Path

import pytest

from agents.lib import return_builder as B
from agents.lib import receipt
from agents.lib import filing_package
from agents.lib import tie_out as T
from agents.lib.secure_fs import secure_path, write_secure
from agents.lib.tax_computations import compute_ca_540, compute_ca_schedule_p
from agents.lib.tax_computations.state.ca import CA_540_COMPONENT_LINES
from agents.lib.tax_law_fetcher import validate_cache


STATUSES = ("single", "mfj", "mfs", "hoh", "qss")


def law():
    # Synthetic parameters make each printed-form operation independently
    # checkable; these are not filing-year rates or engine defaults.
    return {"code": "CA", "type": "graduated_income",
        "standard_deduction": {"single": 10000, "mfj": 20000, "hoh": 20000},
        "personal_exemption": {"amount": 10}, "dependent_exemption": {"amount": 30},
        "exemption_phaseout": {"threshold": dict.fromkeys(STATUSES, 1000000),
            "band": dict.fromkeys(STATUSES, 1000), "reduction_per_step": 1},
        "amt": {"exemption": dict.fromkeys(STATUSES[:-1], 100000),
                "phaseout_start": dict.fromkeys(STATUSES[:-1], 1000000)},
        "underpayment_screen": {"minimum_balance": dict.fromkeys(STATUSES, 500), "tax_fraction": .1},
        "estimated_tax_safe_harbor": {
            "current_year_share": .9,
            "prior_year_share": 1, "prior_year_share_high_income": 1.1,
            "prior_agi_threshold": dict.fromkeys(STATUSES, 10000),
            "current_agi_override": dict.fromkeys(STATUSES, 1000000),
            "installment_shares": [.3, .4, 0, .3],
            "due_dates": ["2025-04-15", "2025-06-16", "2025-09-15", "2026-01-15"],
            "penalty_end": "2026-04-15", "early_filing_deadline": "2026-02-02"},
        "underpayment_rates": [{"effective_from": "2025-01-01", "annual_rate": .08},
            {"effective_from": "2025-07-01", "annual_rate": .07},
            {"effective_from": "2026-01-01", "annual_rate": .06}],
        "schedule_ca": {"fund_asset_minimum": .5},
        "mental_health_surcharge": {"threshold": 1000000, "rate": .01},
        "tax_table": {"max_taxable_income": 10000,
            "rows": [{"min": 0, "max": 10001, "tax": dict.fromkeys(STATUSES, 777)}]},
        "brackets": [{"min": 0, "max": None, "rate": .05}]}


def inputs(**changes):
    data = dict(tax_year=2025, filing_status="Single", state_law=law(), federal_agi=100000,
        schedule_ca_additions=0, schedule_ca_subtractions=0, deduction=10000,
        deduction_method="standard", personal_count=1, blind_count=0, senior_count=0,
        dependent_count=0, ca_preference_adjustments=0, iso_bargain_element=0,
        lump_sum_tax=0,
        components={f"line_{n}": 0 for n in CA_540_COMPONENT_LINES})
    data["components"]["line_71"] = 5000
    data.update(changes)
    return data


def test_printed_income_tax_and_refund_chain():
    # Form 540: 100,000 AGI - 10,000 deduction = 90,000; 5%=4,500;
    # line32=10; line48/64=4,490; withholding5,000 -> refund510.
    out = compute_ca_540(**inputs())
    assert out["line_19"] == 90000
    assert out["line_31"] == 4500
    assert out["line_32"] == 10
    assert out["line_64"] == 4490
    assert out["line_115"] == out["ca_refund"] == 510
    assert out["line_114"] == 0
    assert out["schedule_p"]["ca_amti"] == 100000  # standard deduction comes back
    assert out["computed_tax_year"] == 2025


@pytest.mark.parametrize("taxable,expected", [(0, 0), (10000, 777), (10001, 500.05)])
def test_table_inclusive_ceiling_and_rate_schedule_above_it(taxable, expected):
    # Published table amount is deliberately different from bracket math.
    out = compute_ca_540(**inputs(federal_agi=taxable + 10000))
    assert out["line_31"] == expected


def test_signed_schedule_p_and_tax_before_credits():
    # Schedule P: 90k taxable +10k standard +200k preference -100k
    # exemption=200k; TMT14k - line31 4,500 =9,500 AMT, even with
    # exemption/special credits. A negative preference must reduce AMTI.
    out = compute_ca_540(**inputs(ca_preference_adjustments=200000))
    assert out["line_61"] == 9500
    assert out["line_64"] == 13990
    assert out["line_100"] == out["line_114"] == 8990
    assert "NOT COMPUTED" in out["line_113_note"]
    out = compute_ca_540(**inputs(ca_preference_adjustments=-200000))
    assert out["schedule_p"]["ca_amti"] == -100000
    assert out["line_61"] == 0


def test_schedule_p_uses_unfloored_taxable_income():
    # Part I line15 instructions: 1k AGI-10k deduction=-9k; adding back
    # 10k standard produces1k AMTI, not10k from a floored starting point.
    out = compute_ca_540(**inputs(federal_agi=1000))
    assert out["line_19"] == 0
    assert out["schedule_p"]["ca_amti"] == 1000


def test_mfs_schedule_p_recapture_worksheet():
    # TY2025 worksheet structure, synthetic indexed amounts: elimination
    # ceiling=1,000+100/.25=1,400. At1,500, add min(100,.25*100)=25.
    out = compute_ca_schedule_p(1500, 0, "MFS", 100, 1000)
    assert out["ca_amti"] == 1525
    assert out["ca_tmt"] == 106.75


def test_credits_floor_and_payment_waterfall_with_penalties():
    data = inputs()
    data["components"].update(line_40=10000, line_74=50, line_91=100, line_92=200,
        line_98=300, line_110=100, line_112=10, line_113=20)
    # 5,050 payments -100 use tax -200 ISR=4,750; zero tax;
    # 300 applied leaves4,450;100 contribution+30 penalties ->4,320 refund.
    out = compute_ca_540(**data)
    assert out["line_48"] == out["line_64"] == 0
    assert out["line_95"] == 4750
    assert out["line_99"] == 4450
    assert out["line_115"] == 4320
    data["components"].update(line_71=0, line_74=0, line_98=0)
    out = compute_ca_540(**data)
    assert out["line_94"] == 100
    assert out["line_96"] == 200
    assert out["line_114"] == 430
    assert out["line_115"] == 0


def test_form_constructor_cannot_attest_a_penalty_without_the_checked_workpaper():
    # Even an apparent refund may hide an earlier installment underpayment.
    # The bare form constructor must not present a balance screen as proof.
    data = inputs()
    assert 'NOT COMPUTED' in compute_ca_540(**data)['line_113_note']
    data['components']['line_34'] = 1000
    data['lump_sum_tax'] = 600
    assert 'checked FTB5805 workpaper' in compute_ca_540(**data)['line_113_note']


@pytest.mark.parametrize('bad', [False, float('nan'), float('inf'), '0'])
def test_check_rejects_non_numeric_money_even_when_false_equals_zero(bad):
    p, result = case()
    out = B.build_california(result, {'state': law()}, [], p)
    result['california_540'].update(out)
    result['meta']['limitations'] = [out['line_113_note']]
    result['california_540']['line_61'] = bad
    assert 'line_61' in B.california_differences(result, out)


@pytest.mark.parametrize("missing", ["amt", "tax_table", "underpayment_screen", "schedule_ca"])
def test_required_law_cannot_be_skipped_on_zero_income(missing):
    data = inputs(federal_agi=0)
    del data["state_law"][missing]
    with pytest.raises(ValueError, match=missing):
        compute_ca_540(**data)
    assert any(missing in p for p in validate_cache({"state": data["state_law"]}, 2025))


@pytest.mark.parametrize("field", ["federal_agi", "deduction", "ca_preference_adjustments", "iso_bargain_element"])
@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), "1"])
def test_malformed_component_cannot_produce_a_plausible_return(field, bad):
    with pytest.raises(ValueError):
        compute_ca_540(**inputs(**{field: bad}))


@pytest.mark.parametrize("year", [2024, 2026, True, 2025.0])
def test_unreviewed_form_year_refuses(year):
    with pytest.raises(ValueError, match="reviewed"):
        compute_ca_540(**inputs(tax_year=year))


def case():
    template = json.loads((Path(__file__).resolve().parents[1] / 'agents/templates/result_template.json').read_text())
    ca = template["california_540"]
    ca.update(ca_filing_status="Single", ca_deduction=10000, ca_deduction_method="standard", ca_preference_adjustments=0,
              ca_preference_adjustments_confirmed=True)
    profile = {"tax_year": 2025, "filing_status": "Single", "address": {"state": "CA"},
        "flags": {"age_65_plus_primary": True, "ca_has_blind_exemption": True,
                  "ca_has_nonw2_payments": False, "ca_applies_overpayment_forward": False},
        "dependents": [{"relationship": "child"}],
        "ca_exemptions": {"primary_can_be_claimed": False, "primary_blind": True}}
    result = {"meta": {"tax_year": 2025, "filing_status": "Single"},
              "form_1040": {"line_11_agi": 100000}, "california_540": ca}
    profile['flags'].update(ca_has_us_obligation_income=False, ca_has_nonca_muni_interest=False,
                            ca_has_other_schedule_ca_adjustments=False)
    profile["ca_5805"] = {"method": "regular", "regular_method_confirmed": True,
        "current_tax_components_confirmed": True, "section_453a_interest": 0,
        "accumulation_distribution_tax": 0, "withholding_method": "deemed",
        "actual_withholding": [], "balance_payments": [], "early_return": None,
        "prior_return": {"tax_year": 2024, "filed": True, "full_year": True,
                         "tax": 0, "agi": 0, "withholding": 0, "confirmed": True}}
    return profile, result


def test_builder_reads_senior_blind_and_dependent_facts():
    p, result = case()
    out = B.build_california(result, {"state": law()}, [], p)
    # 1 personal+1 blind+1 senior, each10, plus1 dependent at30.
    assert out["line_11"] == out["line_32"] == 60
    p["ca_exemptions"].update(primary_can_be_claimed=True, primary_schedule_p_child=False)
    p["flags"]["ca_has_blind_exemption"] = False  # a claimable filer is not eligible
    result["california_540"]["ca_deduction_method"] = "dependent_worksheet"
    out = B.build_california(result, {"state": law()}, [], p)
    assert out["line_7"] == out["line_8"] == out["line_9"] == 0


@pytest.mark.parametrize("changes,message", [
    # Line 18 is the larger of the two: a 2,000 itemized total under the
    # 10,000 standard deduction would tax 98,000 instead of 90,000.
    (dict(deduction=2000, deduction_method="itemized"), "larger of"),
    # The dependent worksheet's line 5 is the smaller of its line 3 and the
    # standard deduction, so it can never reach 60,000.
    (dict(deduction=60000, deduction_method="dependent_worksheet", personal_count=0),
     "cannot exceed the CA standard deduction"),
    # Only a filer someone else can claim (no line 7 exemption) uses it.
    (dict(deduction=5000, deduction_method="dependent_worksheet"), "only for a filer"),
    (dict(personal_count=0), "dependent deduction worksheet"),
])
def test_the_deduction_method_cannot_misstate_line_18(changes, message):
    with pytest.raises(ValueError, match=message):
        compute_ca_540(**inputs(**changes))


def test_a_separate_filer_and_a_claimable_filer_may_itemize_below_standard():
    # A spouse who itemizes binds an MFS filer whatever the totals, and a
    # claimable filer compares itemized with the smaller worksheet amount.
    assert compute_ca_540(**inputs(filing_status="MFS", deduction=2000,
                                   deduction_method="itemized"))["line_18"] == 2000
    assert compute_ca_540(**inputs(personal_count=0, deduction=2000,
                                   deduction_method="itemized"))["line_18"] == 2000


@pytest.mark.parametrize("flag", [False, None])
def test_the_blind_flag_must_agree_with_the_eligible_blind_filers(flag):
    # The case's single filer is blind and unclaimable, so line 8 has one box.
    p, result = case()
    p["flags"]["ca_has_blind_exemption"] = flag
    with pytest.raises(B.Blocked, match="ca_has_blind_exemption"):
        B.build_california(result, {"state": law()}, [], p)


@pytest.mark.parametrize("child,message", [
    (True, "child exemption limit is not computed"),
    (None, "primary_schedule_p_child"),
])
def test_a_claimable_child_cannot_take_the_full_amt_exemption(child, message):
    # Booklet line 61: a child under 19 or a full-time student under 24 may
    # owe AMT above 9,750 plus earned income, a limit the builder lacks.
    p, result = case()
    p["ca_exemptions"].update(primary_can_be_claimed=True, primary_schedule_p_child=child)
    p["flags"]["ca_has_blind_exemption"] = False
    result["california_540"]["ca_deduction_method"] = "dependent_worksheet"
    with pytest.raises(B.Blocked, match=message):
        B.build_california(result, {"state": law()}, [], p)


def test_a_worked_family_credit_is_one_field_the_builder_and_tie_out_share():
    p, result = case()
    result["california_540"]["line_75"] = 1000
    out = B.build_california(result, {"state": law()}, [], p)
    assert out["line_78"] == 1000 + out["line_71"]
    result["california_540"].update(out)
    assert not any("CA Form 540 line 75" in line for line in T._eic_lines_still_zero(result))
    assert "line_75_caleitc" not in result["california_540"]


def test_builder_requires_explicit_preferences_and_cannot_drop_the_ca_section():
    p, result = case()
    for field in ("ca_preference_adjustments", "ca_preference_adjustments_confirmed"):
        bad = copy.deepcopy(result)
        del bad["california_540"][field]
        with pytest.raises(B.Blocked):
            B.build_california(bad, {"state": law()}, [], p)
    del result["california_540"]
    with pytest.raises(B.Blocked, match="missing california_540"):
        B.build_california(result, {"state": law()}, [], p)


def test_ca_status_must_be_confirmed_and_cannot_reuse_a_different_federal_return():
    p, result = case()
    result['california_540']['ca_filing_status'] = None
    with pytest.raises(B.Blocked):
        B.build_california(result, {'state': law()}, [], p)
    result['california_540']['ca_filing_status'] = 'MFJ'
    with pytest.raises(B.Blocked, match='pro-forma'):
        B.build_california(result, {'state': law()}, [], p)


def test_california_check_detects_each_derived_edit_and_a_dropped_disclosure():
    p, result = case()
    out = B.build_california(result, {"state": law()}, [], p)
    result["california_540"].update(out)
    result["meta"]["limitations"] = [out["line_113_note"]]
    assert not B.california_differences(result, out)
    for field in out:
        bad = copy.deepcopy(result)
        del bad["california_540"][field]
        assert field in B.california_differences(bad, out)
    result["meta"]["limitations"] = []
    assert "meta.limitations (Form 540 line 113)" in B.california_differences(result, out)


def workspace(tmp_path):
    from tests.test_return_builder import _law
    p, result = case()
    template = json.loads((Path(__file__).resolve().parents[1] / 'agents/templates/result_template.json').read_text())
    result["form_1040"] = template["form_1040"]
    result["schedule_d"] = template["schedule_d"]
    fed = _law()["federal"]
    fed["filing_statuses"]["single"] = fed["filing_statuses"].pop("mfj")
    for field in ("zero_pct_limit", "fifteen_pct_limit"):
        fed["capital_gains_rates"][field]["single"] = fed["capital_gains_rates"][field].pop("mfj")
    secure_path(tmp_path)
    documents = tmp_path / 'documents'
    documents.mkdir()
    secure_path(documents)
    write_secure(documents / 'wage.pdf', b'%PDF synthetic wage document')
    raw = {"records": [{"doc_id": "wage", "file": "documents/wage.pdf", "form_type": "W-2",
        "payer": "Synthetic payer", "person": "primary",
        "key_boxes": {"wages": 100000, "state_withholding_ca": 5000}}]}
    for name, data in (("result.json", result), ("taxpayer_profile.json", p),
                       ("parse_ledger.json", raw), ("tax_law_2025.json", {"tax_year": 2025, "federal": fed, "state": law()})):
        write_secure(tmp_path / name, json.dumps(data))
    return tmp_path


def test_cli_writes_checks_receipts_and_refuses_a_post_build_edit(tmp_path):
    ws = workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / 'result.json').read_text())
    # CA is built from the newly derived federal AGI, not the original zero.
    assert result["form_1040"]["line_11_agi"] == result["california_540"]["line_13"] == 100000
    assert result["california_540"]["line_71"] == 5000
    assert B._main(["--workspace", str(ws), "--check"]) == 0
    assert receipt.verify(ws, required=(B.GATE,)).valid
    result["california_540"]["line_114"] += 1
    write_secure(ws / 'result.json', json.dumps(result))
    assert not receipt.verify(ws, required=(B.GATE,)).valid
    assert B._main(["--workspace", str(ws), "--check"]) == 2
    saved = json.loads((ws / receipt.RECEIPT_NAME).read_text())
    assert saved["gates"][B.GATE]["outcome"] == 'FAILED'


def test_checked_summary_and_payment_guidance_use_final_settlement(tmp_path):
    ws = workspace(tmp_path)
    result = json.loads((ws / 'result.json').read_text())
    result['balance_due_summary'] = {'federal_refund': 0, 'california_refund': 0, 'net_refund': 0}
    # 4,440 CA tax,5,000 withheld, plus a worked200 refundable credit.
    result['california_540']['line_75'] = 200
    write_secure(ws / 'result.json', json.dumps(result))
    assert B._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / 'result.json').read_text())
    assert result['balance_due_summary']['california_refund'] == 760
    assert B._main(["--workspace", str(ws), '--check']) == 0
    result['balance_due_summary']['california_refund'] = 0
    write_secure(ws / 'result.json', json.dumps(result))
    assert B._main(["--workspace", str(ws), '--check']) == 2
    # A checked refund stays a refund even if old withholding-only math
    # would have shown a balance, or historical SDI is present in a file.
    result['california_540'].update(ca_total_tax=900, ca_withholding_box17=800,
                                  ca_amount_owed=0, ca_refund=100)
    assert not any(line.startswith('CA balance due:') for line in filing_package.attachment_checklist(result))


def test_federal_out_of_scope_cannot_waive_the_ca_check(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    def waiver(*args, **kwargs):
        raise B.OutOfScope('synthetic federal PFIC waiver')
    monkeypatch.setattr(B, 'build', waiver)
    assert B._main(["--workspace", str(ws), "--check"]) == 0
    result = json.loads((ws / 'result.json').read_text())
    result["california_540"]["line_61"] += 1
    write_secure(ws / 'result.json', json.dumps(result))
    assert B._main(["--workspace", str(ws), "--check"]) == 2


def test_write_uses_new_federal_agi_before_validating_ca_settlement(tmp_path):
    ws = workspace(tmp_path)
    result = json.loads((ws / 'result.json').read_text())
    # The stale million-dollar AGI makes a valid100 forward election look
    # impossible. The ledger derives100k AGI,4,440 CA tax and560 overpaid;
    # the builder must replace the AGI before validating that election.
    result['form_1040']['line_11_agi'] = 1000000
    result['california_540']['line_98'] = 100
    profile = json.loads((ws / 'taxpayer_profile.json').read_text())
    profile['flags']['ca_applies_overpayment_forward'] = True
    write_secure(ws / 'taxpayer_profile.json', json.dumps(profile))
    write_secure(ws / 'result.json', json.dumps(result))
    assert B._main(["--workspace", str(ws)]) == 0
    written = json.loads((ws / 'result.json').read_text())
    assert written['california_540']['line_115'] == 460


def test_selected_unreviewed_ca_year_cannot_get_a_receipt(tmp_path):
    ws = workspace(tmp_path)
    profile = json.loads((ws / 'taxpayer_profile.json').read_text())
    result = json.loads((ws / 'result.json').read_text())
    profile['tax_year'] = result['meta']['tax_year'] = 2026
    cache = json.loads((ws / 'tax_law_2025.json').read_text())
    cache['tax_year'] = 2026
    for name, data in (('taxpayer_profile.json', profile), ('result.json', result),
                       ('tax_law_2026.json', cache)):
        write_secure(ws / name, json.dumps(data))
    assert B._main(["--workspace", str(ws)]) == 2
    assert B._main(["--workspace", str(ws), "--check"]) == 2
    assert not receipt.verify(ws, required=(B.GATE,)).valid
