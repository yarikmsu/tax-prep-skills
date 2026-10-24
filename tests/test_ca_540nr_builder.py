"""Source-bound Form540NR builds and the receipted CLI, with synthetic inputs."""

import json
import pytest

from agents.lib import return_builder as B
from agents.lib import receipt
from agents.lib import tie_out as T
from agents.lib.secure_fs import secure_path, write_secure
from agents.lib.tax_computations.state.registry import ca_section_key
from tests.test_ca_540_builder import workspace
from tests.test_ca_part_year import profile, return_case


def nr_workspace(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    secure_path(tmp_path)
    ws = workspace(tmp_path)
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    result = json.loads((ws / "result.json").read_text())
    source_profile = profile()
    p.update({k: source_profile[k] for k in ("state_filings", "residency", "address")})
    p["flags"].update(source_profile["flags"])
    p["flags"].update(age_65_plus_primary=False, ca_has_amt_credit=False, ca_has_ptet_credit=False)
    p["ca_exemptions"]["primary_blind"] = False
    p["dependents"] = []
    p.setdefault("ca_credits", {}).update(other_credits=[], ptet_current=[])
    data = return_case()
    p["state_filings"]["CA"] = data["profile"]["state_filings"]["CA"]
    manual = {k: v for k, v in data["components"].items() if k not in
              {"line_12", "line_81", "line_82", "line_83", "line_123"}}
    old = result.pop(ca_section_key())
    result[ca_section_key("540NR")] = {
        "ca_filing_status": "Single", "form_inputs": manual,
        "deduction_inputs": data["deductions"], "amt_inputs": data["amt_inputs"],
        "iso_basis_reconciliation": {"adjustment": 0, "confirmed": True, "source_ids": []},
        "conformity_manual_rows": [],
        "w2_ca_wages": {"confirmed": True, "sources": [{"source_id": "wage", "amount": 50000}]},
        "schedule_ca_adjustments": old["schedule_ca_adjustments"],
    }
    result["meta"]["ca_return_type"] = "540NR"
    write_secure(ws / "taxpayer_profile.json", json.dumps(p))
    write_secure(ws / "result.json", json.dumps(result))
    return ws


def bind_review(ws):
    result = json.loads((ws / "result.json").read_text())
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    digest = result[ca_section_key("540NR")]["allocation_input_digest"]
    p["state_filings"]["CA"]["worksheet_review"] = {
        "input_digest": digest, **dict.fromkeys(("sources_reviewed", "conformity_reviewed",
            "deductions_reviewed", "amt_reviewed", "credits_reviewed"), True)}
    write_secure(ws / "taxpayer_profile.json", json.dumps(p))


def test_cli_nr_draft_review_check_and_unchanged_write(tmp_path):
    ws = nr_workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / "result.json").read_text())
    nr = result[ca_section_key("540NR")]
    assert nr["line_81"] == nr["ca_withholding_box17"] == 5000
    assert nr["line_13"] == 100000
    assert nr["line_12"] == 50000
    assert nr["ca_total_tax"] == 2245
    assert nr["ca_refund"] == 2755
    assert nr["allocation_review_current"] is False
    assert B._main(["--workspace", str(ws), "--check"]) == 2
    bind_review(ws)
    assert B._main(["--workspace", str(ws)]) == 0
    assert B._main(["--workspace", str(ws), "--check"]) == 0
    assert T._main(["--workspace", str(ws)]) == 0
    assert receipt.verify(ws, required=(B.GATE,)).valid
    before = (ws / "result.json").read_bytes()
    assert B._main(["--workspace", str(ws)]) == 0
    assert (ws / "result.json").read_bytes() == before
    assert receipt.verify(ws, required=(B.GATE,)).valid


def test_nr_rate_edit_below_a_cent_fails_the_check(tmp_path):
    ws = nr_workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    bind_review(ws)
    assert B._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / "result.json").read_text())
    result[ca_section_key("540NR")]["line_36"] += .0001
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws), "--check"]) == 2
    assert T._main(["--workspace", str(ws)]) == 2


def test_nr_raw_manual_inputs_stay_separate_from_printed_rounding(tmp_path):
    ws = nr_workspace(tmp_path)
    result = json.loads((ws / "result.json").read_text())
    result[ca_section_key("540NR")]["form_inputs"]["line_120"] = 10.49
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws)]) == 0
    first = json.loads((ws / "result.json").read_text())[ca_section_key("540NR")]
    assert first["line_120"] == 10
    assert first["form_inputs"]["line_120"] == 10.49
    assert B._main(["--workspace", str(ws)]) == 0
    second = json.loads((ws / "result.json").read_text())[ca_section_key("540NR")]
    assert first["allocation_input_digest"] == second["allocation_input_digest"]


def test_nr_missing_section_or_plan_cannot_fall_back_to_resident_form(tmp_path):
    ws = nr_workspace(tmp_path)
    result = json.loads((ws / "result.json").read_text())
    del result[ca_section_key("540NR")]
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws)]) == 2
    assert T._main(["--workspace", str(ws)]) == 2
    ws = nr_workspace(tmp_path / "fresh")
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    p["state_filings"] = {}
    write_secure(ws / "taxpayer_profile.json", json.dumps(p))
    assert B._main(["--workspace", str(ws)]) == 2


def test_nr_review_binds_conformity_worksheet_inputs(tmp_path):
    ws = nr_workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    bind_review(ws)
    assert B._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / "result.json").read_text())
    nr = result[ca_section_key("540NR")]
    nr["schedule_ca_adjustments"]["other_adjustments_confirmed"] = not nr["schedule_ca_adjustments"]["other_adjustments_confirmed"]
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws), "--check"]) == 2


def test_nr_penalty_uses_source_agi_not_worldwide_agi(tmp_path):
    ws = nr_workspace(tmp_path)
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    raw = json.loads((ws / "parse_ledger.json").read_text())
    cache = json.loads((ws / "tax_law_2025.json").read_text())
    income = p["state_filings"]["CA"]["income_rows"][0]
    income.update(federal=2000000, resident=50000, nonresident_ca=0, nonresident_elsewhere=1950000)
    raw["records"][0]["key_boxes"].update(wages=2000000, state_withholding_ca=0)
    raw["records"][0]["second_read"] = "match"
    cache["state"]["underpayment_screen"]["tax_fraction"] = .9
    p["ca_5805"] = {"method": "regular", "regular_method_confirmed": True,
        "current_tax_components_confirmed": True, "section_453a_interest": 0,
        "accumulation_distribution_tax": 0, "withholding_method": "deemed",
        "actual_withholding": [], "balance_payments": [],
        "prior_return": {"tax_year": 2024, "filed": True, "full_year": True,
            "tax": 1000, "agi": 20000, "withholding": 0, "confirmed": True}}
    for name, data in (("taxpayer_profile.json", p), ("parse_ledger.json", raw), ("tax_law_2025.json", cache)):
        write_secure(ws / name, json.dumps(data))
    assert B._main(["--workspace", str(ws)]) == 0
    nr = json.loads((ws / "result.json").read_text())[ca_section_key("540NR")]
    # SourceAGI50k is below override1m; worldAGI2m must not suppress prior
    # 1000*1.1=1100 safe harbor (current90% would be2239.20).
    assert nr["ftb_5805"]["current_agi"] == 50000
    assert nr["ftb_5805"]["required_annual_payment"] == 1100


@pytest.mark.parametrize("path", [("line_38",), ("schedule_ca", "part_iv", "line_3"),
                                  ("schedule_p", "line_38"), ("schedule_p", "line_42")])
def test_all_four_decimal_ratios_are_checked_exactly(tmp_path, path):
    ws = nr_workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    bind_review(ws)
    assert B._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / "result.json").read_text())
    node = result[ca_section_key("540NR")]
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] += .0001
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws), "--check"]) == 2
    assert T._main(["--workspace", str(ws)]) == 2


def test_iso_source_omission_refuses_and_ca_basis_delta_is_review_bound(tmp_path):
    ws = nr_workspace(tmp_path)
    raw = json.loads((ws / "parse_ledger.json").read_text())
    raw["records"][0]["key_boxes"]["iso_bargain"] = 1000
    write_secure(ws / "parse_ledger.json", json.dumps(raw))
    assert B._main(["--workspace", str(ws)]) == 2
    result = json.loads((ws / "result.json").read_text())
    nr = result[ca_section_key("540NR")]
    # Federal spread1000 plus reviewed CA-basis difference200 => world1200;
    # actual source worksheet assigns600. No residence-month shortcut.
    nr["amt_inputs"].update(iso_world=1200, iso_ca=600)
    nr["iso_basis_reconciliation"].update(adjustment=200, source_ids=["wage"])
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws)]) == 0
    bind_review(ws)
    assert B._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / "result.json").read_text())
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    law = json.loads((ws / "tax_law_2025.json").read_text())
    assert T._ca_5805_checks(T.load_ledger(raw), result, p, law) == []
    nr = result[ca_section_key("540NR")]
    nr["iso_basis_reconciliation"]["adjustment"] = 201
    nr["amt_inputs"]["iso_world"] = 1201
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws), "--check"]) == 2


def test_known_conformity_cannot_be_shifted_to_another_income_line(tmp_path):
    ws = nr_workspace(tmp_path)
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    result = json.loads((ws / "result.json").read_text())
    nr = result[ca_section_key("540NR")]
    nr["schedule_ca_adjustments"]["hsa_employer_addback"] = 100
    from tests.test_ca_part_year import row
    # Same total AGI, wrong printed component: add HSA wages to interest.
    p["state_filings"]["CA"]["income_rows"].append(row("A2b", id="hsa", federal=0,
        additions=100, resident=100, nonresident_ca=0, nonresident_elsewhere=0))
    write_secure(ws / "taxpayer_profile.json", json.dumps(p))
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws)]) == 2
    p["state_filings"]["CA"]["income_rows"][-1]["line"] = "A1z"
    write_secure(ws / "taxpayer_profile.json", json.dumps(p))
    assert B._main(["--workspace", str(ws)]) == 0


def test_zero_income_nr_is_computed_and_cannot_lose_its_section(tmp_path):
    ws = nr_workspace(tmp_path)
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    result = json.loads((ws / "result.json").read_text())
    ledger = json.loads((ws / "parse_ledger.json").read_text())
    p["state_filings"]["CA"]["income_rows"] = []
    result[ca_section_key("540NR")]["w2_ca_wages"]["sources"][0]["amount"] = 0
    ledger["records"][0]["key_boxes"] = {"wages": 0, "state_withholding_ca": 0}
    for name, value in (("taxpayer_profile.json", p), ("result.json", result), ("parse_ledger.json", ledger)):
        write_secure(ws / name, json.dumps(value))
    assert B._main(["--workspace", str(ws)]) == 0
    bind_review(ws)
    assert B._main(["--workspace", str(ws)]) == 0
    assert B._main(["--workspace", str(ws), "--check"]) == 0
    assert T._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / "result.json").read_text())
    nr = result.pop(ca_section_key("540NR"))
    assert nr["computed_tax_year"] == 2025
    assert nr["ca_total_tax"] == nr["ca_refund"] == nr["ca_amount_owed"] == 0
    write_secure(ws / "result.json", json.dumps(result))
    assert T._main(["--workspace", str(ws)]) == 2


def test_federal_waiver_does_not_waive_nr_allocation_review(tmp_path, monkeypatch):
    ws = nr_workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    def waived(*args, **kwargs):
        raise B.OutOfScope("synthetic federal PFIC waiver")
    monkeypatch.setattr(B, "build", waived)
    assert B._main(["--workspace", str(ws), "--check"]) == 2


def test_nr_family_credit_advisory_names_nr_lines(tmp_path):
    ws = nr_workspace(tmp_path)
    assert B._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / "result.json").read_text())
    labels = T._eic_lines_still_zero(result)
    assert any("540NR line85" in line for line in labels)
    assert not any("540 line 75" in line for line in labels)


@pytest.mark.parametrize("sources", [[], [{"source_id": "wrong", "amount": 100}],
    [{"source_id": "wage", "amount": -1}], [{"source_id": "wage", "amount": True}],
    [{"source_id": "wage", "amount": float("nan")}],
    [{"source_id": "wage", "amount": 1}, {"source_id": "wage", "amount": 2}]])
def test_nr_box16_source_reconciliation_cannot_skip_or_misstate_a_w2(tmp_path, sources):
    ws = nr_workspace(tmp_path)
    result = json.loads((ws / "result.json").read_text())
    result[ca_section_key("540NR")]["w2_ca_wages"]["sources"] = sources
    write_secure(ws / "result.json", json.dumps(result))
    assert B._main(["--workspace", str(ws)]) == 2


@pytest.mark.parametrize("ca_exempt", [True, False])
def test_first_nr_build_reconciles_mixed_ui_and_legacy_line7_family_benefits(tmp_path, ca_exempt):
    from tests.test_government_benefits import source
    from tests.test_ca_part_year import row
    ws = nr_workspace(tmp_path)
    p = json.loads((ws / "taxpayer_profile.json").read_text())
    result = json.loads((ws / "result.json").read_text())
    raw = json.loads((ws / "parse_ledger.json").read_text())
    benefit = source()  # UI1000-repaid100-contribution50=850; family500.
    if not ca_exempt:
        benefit["government_benefits"]["family_programs"][0].update(
            program="other_government", ca_exempt=False, ca_exemption_confirmed=True)
    raw["records"].append(benefit)
    write_secure(ws / benefit["file"], b"%PDF synthetic benefits source")
    p["flags"]["has_1099g_benefits"] = True
    # B7 federal1350 includes BOTH benefits, even for source-reviewed legacy
    # family reporting. UI always excluded; EDD PFL excluded; the explicitly
    # taxable other program retains500 worldwide and actual250 resident.
    p["state_filings"]["CA"]["income_rows"].append(row("B7", id="benefits", federal=1350,
        subtractions=1350 if ca_exempt else 850, resident=0 if ca_exempt else 250,
        nonresident_ca=0, nonresident_elsewhere=0 if ca_exempt else 250))
    result["schedule_1_government_benefits"] = {"other_income": 0, "other_income_confirmed": True}
    for name, data in (("taxpayer_profile.json", p), ("result.json", result), ("parse_ledger.json", raw)):
        write_secure(ws / name, json.dumps(data))
    assert B._main(["--workspace", str(ws)]) == 0  # No hand-typed federal AGI or line8.
    bind_review(ws)
    assert B._main(["--workspace", str(ws)]) == 0
    assert B._main(["--workspace", str(ws), "--check"]) == 0
    assert T._main(["--workspace", str(ws)]) == 0
    result = json.loads((ws / "result.json").read_text())
    nr = result[ca_section_key("540NR")]
    assert result["form_1040"]["line_11_agi"] == 101350
    assert nr["line_81"] == 5045
    assert nr["ca_all_source_agi"] == (100000 if ca_exempt else 100500)
    assert nr["ca_agi"] == (50000 if ca_exempt else 50250)
    assert nr["ca_total_tax"] == (2245 if ca_exempt else 2258)
