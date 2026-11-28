"""Tests for agents/lib/filing_package.py — assembly order + checklist (F1)."""
import json
import subprocess
import sys
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import capabilities, filing_package as FP, material_evidence, receipt  # noqa: E402
from agents.lib import parse_records, return_builder, tie_out  # noqa: E402
from tests.filing_law import write_synthetic_filing_inputs, write_synthetic_filing_law  # noqa: E402


def _result(**sections):
    base = {
        "meta": {"limitations": ["State: California includes manual worksheets and capability gates."]},
        "form_1040": {"line_9_total_income": 100000},
    }
    base.update(sections)
    return base


def test_1040_always_first_no_sequence():
    order = FP.assembly_order(_result())
    assert order[0] == {"key": "form_1040", "title": "Form 1040",
                        "sequence": None}


def test_sequence_ordering_12_before_12a_before_19():
    r = _result(form_1116_ftc={"credit": 300},
                form_8949={"rows": [1]},
                schedule_d={"line_16": 5000})
    keys = [e["key"] for e in FP.assembly_order(r)]
    assert keys == ["form_1040", "schedule_d", "form_8949", "form_1116_ftc"]


def test_full_table_ordering_pins_every_sequence():
    """Review round: pin the WHOLE table so any single sequence typo fails.
    Every mapped section present at once -> exact expected order."""
    r = _result(**{k: {"x": 1} for k in FP._FEDERAL_SEQUENCE})
    r["form_8938_fatca"] = {"total": 1}
    r["california_540"] = {"ca_total_tax": 1}
    r["schedule_ca_itemized"] = {"total": 1}
    seqs = [e["sequence"] for e in FP.assembly_order(r)]
    # Derived rows in this fixture: 01 (Schedule 1, from schedule_c
    # presence) and 08 (Schedule B Part III, from the form_8938 foreign
    # signal). 02/03/17/47 correctly absent — their value gates are 0 here.
    assert seqs == [None, "01", "06", "07", "08", "09", "12", "12A",
                    "19", "32", "48", "50", "52", "55", "71", "175",
                    "STATE", "STATE"]


def test_every_federal_sequence_has_digits():
    for _, (title, seq) in FP._FEDERAL_SEQUENCE.items():
        assert any(c.isdigit() for c in seq), (title, seq)
    with pytest.raises(FP.FilingPackageError):
        FP._seq_sort_key("STATE")


def test_derived_schedule_1_2_3(  ):
    """Review P1: Schedules 1/2/3 must appear when the 1040 fields encode
    them — a Schedule C return without Schedule 1/2/SE is unfilable."""
    r = _result(schedule_c={"line_31_net_profit": 50000, "se_tax": 7065})
    r["form_1040"]["line_8_schedule1_other"] = 50000
    r["form_1040"]["line_23_other_taxes"] = 7065
    r["form_1040"]["line_20_schedule3_line8"] = 300
    seqs = [e["sequence"] for e in FP.assembly_order(r)]
    assert ["01", "02", "03"] == [s for s in seqs if s in ("01", "02", "03")]
    assert "17" in seqs  # Schedule SE from se_tax > 0
    titles = "\n".join(e["title"] for e in FP.assembly_order(r))
    assert "Schedule SE" in titles


def test_derived_schedule_b_triggers():
    # >$1,500 interest
    r = _result()
    r["form_1040"]["line_2b_taxable_interest"] = 2000
    assert any(e["sequence"] == "08" for e in FP.assembly_order(r))
    # Part III via foreign signals even under $1,500
    r2 = _result(interest_detail={"foreign_accounts_yes": True,
                                  "total": 500})
    assert any(e["sequence"] == "08" for e in FP.assembly_order(r2))
    # Neither -> no Schedule B
    assert not any(e["sequence"] == "08"
                   for e in FP.assembly_order(_result()))


def test_derived_schedule_1a_gated_on_line_13b():
    """OBBBA Schedule 1-A appears (Attachment Seq. 1A, after Schedule 1)
    when line 13b > 0, and is absent otherwise — a taxpayer with tips/
    overtime/car-loan/senior deductions can't mail an incomplete package."""
    r = _result()
    assert not any(e["sequence"] == "1A" for e in FP.assembly_order(r))
    r["form_1040"]["line_13b_schedule_1a_deductions"] = 12000
    r["schedule_1a"] = {"tips": {"allowed": 12000}, "total": 12000}
    seqs = [e["sequence"] for e in FP.assembly_order(r)]
    assert "1A" in seqs
    # 1A sorts after 01 and before 02.
    r["form_1040"]["line_8_schedule1_other"] = 5000  # forces Schedule 1 (01)
    ordered = [e["sequence"] for e in FP.assembly_order(r)]
    assert ordered.index("01") < ordered.index("1A")


def test_schedule_1a_derived_row_keys_the_real_section():
    """Unlike the reconstructed schedules (key None), Schedule 1-A has a real
    result section — the assembly entry must carry key='schedule_1a' so the
    filing package renders the per-provision worksheet detail, not just a bare
    attachment line. With line 13b > 0 but the section missing/scaffold, the
    result is inconsistent and packaging HARD-STOPS (a bare row can't attach)."""
    # line 13b set, no schedule_1a section -> incomplete result -> hard error.
    r = _result()
    r["form_1040"]["line_13b_schedule_1a_deductions"] = 9000
    with pytest.raises(FP.FilingPackageError):
        FP.assembly_order(r)
    # A total-only section (no per-provision detail) is ALSO rejected — a bare
    # total can't populate the attached Schedule 1-A worksheet.
    r["schedule_1a"] = {"total": 9000}
    with pytest.raises(FP.FilingPackageError):
        FP.assembly_order(r)
    # Provision detail that does NOT reconcile to line 13b is rejected too — a
    # scaffold (allowed:0) or stale section can't support the packaged total.
    r["schedule_1a"] = {"tips": {"allowed": 5000}, "total": 5000}  # 5000 != 9000
    with pytest.raises(FP.FilingPackageError):
        FP.assembly_order(r)
    # Real schedule_1a section present -> keyed so the section renders.
    r["schedule_1a"] = {"tips": {"allowed": 9000}, "total": 9000}
    entry2 = next(e for e in FP.assembly_order(r) if e["sequence"] == "1A")
    assert entry2["key"] == "schedule_1a"
    # Other derived schedules stay key None (they have no result section).
    r["form_1040"]["line_8_schedule1_other"] = 5000
    sch1 = next(e for e in FP.assembly_order(r) if e["sequence"] == "01")
    assert sch1["key"] is None


def test_schedule_1a_car_loan_requires_vehicle_detail():
    """Codex round-14 P2: a nonzero car-loan deduction needs a Part IV vehicle
    row (VIN + interest_paid) to attach Schedule 1-A — a claim with no
    car_loan_detail hard-stops; a complete row packages."""
    r = _result()
    r["form_1040"]["line_13b_schedule_1a_deductions"] = 4000
    r["schedule_1a"] = {"car_loan": {"allowed": 4000}, "total": 4000}
    with pytest.raises(FP.FilingPackageError):
        FP.assembly_order(r)
    # A complete vehicle row lets it package.
    r["schedule_1a"]["car_loan_detail"] = [
        {"vin": "1FTFW1E50NKE00000", "interest_paid": 4200}]
    assert any(e["sequence"] == "1A" for e in FP.assembly_order(r))


def test_derived_schedule_8812_value_gated():
    r = _result()
    r["form_1040"]["line_19_ctc"] = 4000
    assert any(e["sequence"] == "47" for e in FP.assembly_order(r))
    r["form_1040"]["line_19_ctc"] = 0
    assert not any(e["sequence"] == "47" for e in FP.assembly_order(r))


def test_state_forms_after_all_federal_incl_schedule_ca():
    r = _result(california_540={"ca_total_tax": 8000},
                schedule_ca_itemized={"total_ca_itemized": 30000},
                form_8959_additional_medicare={"line_18": 450})
    entries = FP.assembly_order(r)
    assert [e["sequence"] for e in entries[-2:]] == ["STATE", "STATE"]
    assert "Schedule CA (540)" in entries[-1]["title"] \
        or "Schedule CA (540)" in entries[-2]["title"]


def test_scaffold_sections_never_generate_attachment_lines():
    r = _result(schedule_a={"_include_if": "itemizing", "total_itemized": 0},
                form_6251={"amt": 0, "amti": 0})
    keys = [e["key"] for e in FP.assembly_order(r)]
    assert keys == ["form_1040"]


def test_missing_1040_or_compute_evidence_hard_errors():
    with pytest.raises(FP.FilingPackageError):
        FP.assembly_order({"meta": {"limitations": ["x"]},
                           "schedule_d": {"line_16": 1}})
    # Parse-stage-only result (income lines filled, no compute marker):
    with pytest.raises(FP.FilingPackageError):
        FP.assembly_order({"form_1040": {"line_9_total_income": 100000},
                           "meta": {"limitations": []}})


def test_field_num_strictness():
    r = _result()
    r["form_1040"]["line_37_amount_owed"] = float("nan")
    with pytest.raises(FP.FilingPackageError):
        FP.attachment_checklist(r)
    r["form_1040"]["line_37_amount_owed"] = "1200"
    with pytest.raises(FP.FilingPackageError):
        FP.attachment_checklist(r)


def test_checklist_signature_and_w2_always_present():
    lines = "\n".join(FP.attachment_checklist(_result()))
    assert "Sign and date" in lines
    assert "W-2, Copy B" in lines
    assert "ONLY when it shows federal income tax withheld" in lines


def test_balance_checklist_refuses_unreviewed_extension_and_excess_credit():
    balance_row = {"kind": "balance_due", "date": "2026-04-15", "amount": 400}
    result = _result(form_2210={"payment_table": [balance_row], "balance_due_paid": 400})
    form = result["form_1040"]
    form.update(line_37_amount_owed=700, line_31_schedule3_part2=1000)
    with pytest.raises(FP.FilingPackageError, match="Record form_1040.schedule_3_line_10"):
        FP.attachment_checklist(result)
    form["schedule_3_line_10_extension_payment"] = 1000
    with pytest.raises(FP.FilingPackageError, match="not_extension_payment_confirmed"):
        FP.attachment_checklist(result)
    balance_row["not_extension_payment_confirmed"] = True
    checklist = FP.attachment_checklist(result)
    assert any("$300.00 of Form 1040 line 37 remains" in line for line in checklist)
    assert any("include Form 1040-V" in line for line in checklist)
    form["line_37_amount_owed"] = 400
    assert all("include Form 1040-V" not in line for line in FP.attachment_checklist(result))
    form["line_37_amount_owed"] = 399.99
    with pytest.raises(FP.FilingPackageError, match="exceed Form 1040 line 37"):
        FP.attachment_checklist(result)
    form["line_37_amount_owed"] = 700
    result["form_2210"]["balance_due_paid"] = 300
    with pytest.raises(FP.FilingPackageError, match="disagree with the reviewed payment table"):
        FP.attachment_checklist(result)
    result["form_2210"]["balance_due_paid"] = 400
    form["line_31_schedule3_part2"] = 800
    with pytest.raises(FP.FilingPackageError, match="exceed Form 1040 line 31"):
        FP.attachment_checklist(result)
    form["line_31_schedule3_part2"] = 1000
    form["schedule_3_line_10_extension_payment"] = None
    with pytest.raises(FP.FilingPackageError, match="schedule_3_line_10_extension_payment, 0 when"):
        FP.attachment_checklist(result)


def test_checklist_conditional_branches():
    r = _result(form_8949={"rows": [1]},
                california_540={"ca_total_tax": 8000,
                                "ca_withholding_box17": 5000},
                form_8606={"basis_remaining": 500},
                fbar_fincen_114={"accounts": [{"max": 1}]})
    r["form_1040"]["line_37_amount_owed"] = 1200
    lines = "\n".join(FP.attachment_checklist(r))
    assert "NO 8949 and NO attachment at all" in lines  # 1a/8a exception
    assert "United \nStates Treasury" in lines or "United" in lines
    assert "do NOT staple" in lines
    assert "1040-ES" in lines and "/tax-project" in lines
    assert "OWN envelope to the FTB" in lines
    assert "other than A or B" in lines
    assert "Franchise Tax" in lines  # CA balance-due payment mechanics
    assert "PER INDIVIDUAL" in lines  # 8606
    assert "BSA e-filing" in lines


def test_checklist_refund_direct_deposit():
    r = _result()
    r["form_1040"]["line_35a_refund"] = 900
    lines = "\n".join(FP.attachment_checklist(r))
    assert "35b-35d" in lines
    assert "1040-V" not in lines


def test_fbar_three_way_gating():
    """Review P1: absence of the fbar section is NOT an all-clear."""
    # Section present -> files-separately line.
    r1 = _result(fbar_fincen_114={"accounts": [{"max": 1}]})
    assert "files \nseparately" in "\n".join(FP.attachment_checklist(r1)) \
        or "BSA" in "\n".join(FP.attachment_checklist(r1))
    # Foreign signals but no section -> PENDING warning, never all-clear.
    r2 = _result(interest_detail={"fbar_required": True, "total": 100})
    lines2 = "\n".join(FP.attachment_checklist(r2))
    assert "PENDING" in lines2 and "/tax-fbar" in lines2
    assert "No foreign-account signals" not in lines2
    # Truly no signals -> neutral wording, no categorical negative.
    lines3 = "\n".join(FP.attachment_checklist(_result()))
    assert "No foreign-account signals" in lines3
    assert "nothing files outside" not in lines3


def test_render_markdown_carries_not_efile_banner():
    md = FP.render_markdown(_result(schedule_d={"line_16": 1}))
    assert "NOT an e-file artifact" in md
    assert "Form 8879" in md
    assert "| 1 | Form 1040 | — |" in md
    assert "- [ ]" in md


FIX_REPO = Path(__file__).resolve().parent.parent


def _prepare_export(tmp_path):
    write_synthetic_filing_law(tmp_path)
    write_synthetic_filing_inputs(tmp_path)


def _package(tmp_path, *flags):
    p = tmp_path / "result.json"
    if not p.exists():
        p.write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    return subprocess.run(
        [sys.executable, "-m", "agents.lib.filing_package",
         "--result", str(p), "--workspace", str(tmp_path), *flags],
        capture_output=True, text=True, cwd=FIX_REPO)


def test_cli_json_and_markdown(tmp_path):
    """`--draft`, because V8 refuses an unvalidated package: this workspace has
    no receipt, so the shape assertions run on the draft the escape hatch
    produces."""
    out = _package(tmp_path, "--json", "--draft")
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["assembly_order"][0]["key"] == "form_1040"
    assert any("Sign and date" in x for x in data["attachment_checklist"])
    assert data["validated"] is False
    out2 = _package(tmp_path, "--draft")
    assert "Filing package" in out2.stdout
    assert out2.stdout.startswith("# NOT VALIDATED — DRAFT ONLY")


def test_the_package_is_refused_when_the_gates_did_not_run(tmp_path):
    """V8: a filing package assembled on gates that never ran, or that ran
    against other inputs, does not describe the return it is packaging. The
    refusal names which gate and prints nothing on stdout — a caller piping
    this into a file gets an empty file, not half a package."""
    out = _package(tmp_path)
    assert out.returncode == 2
    assert out.stdout == ""
    assert "REFUSED" in out.stderr and "validation_receipt.json" in out.stderr
    assert "--draft" in out.stderr


def test_a_valid_receipt_assembles_with_no_banner(tmp_path):
    from agents.lib import receipt
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    (tmp_path / "parse_ledger.json").write_text('{"records": []}')
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    _prepare_export(tmp_path)
    for gate in receipt.REQUIRED_GATES:
        receipt.record(tmp_path, gate, receipt.PASS)
    out = _package(tmp_path)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("# Filing package") or "Filing package" in out.stdout
    assert "NOT VALIDATED" not in out.stdout


def test_untouched_standard_deduction_is_an_open_item_even_with_passing_arithmetic(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_result(form_1040={"line_9_total_income": 100000,
                                                "line_12_standard_deduction": 0})), encoding="utf-8")
    law = write_synthetic_filing_law(tmp_path)

    def certify():
        for gate in receipt.REQUIRED_GATES:
            receipt.record(tmp_path, gate, receipt.PASS)

    certify()
    denied = _package(tmp_path)
    assert denied.returncode == 2
    assert "Open material item: Form 1040 line 12 deduction election." in denied.stderr
    draft = _package(tmp_path, "--draft")
    assert draft.returncode == 0 and "Form 1040 line 12 deduction election" in draft.stdout

    def record_basic_deduction():
        record = _material_cli(tmp_path, "--id", "input.deduction_election", "--label", "Deduction election",
                               "--outcome", "computed", "--deduction-method", "standard",
                               "--input", "result.json", "--input", "taxpayer_profile.json",
                               "--input", "tax_law_2025.json")
        assert record.returncode == 0, record.stderr
        certify()

    record_basic_deduction()
    assert "Line 12 must equal the covered-year basic standard deduction" in _package(tmp_path).stderr
    result = json.loads(path.read_text(encoding="utf-8"))
    result["form_1040"]["line_12_standard_deduction"] = law["federal"]["filing_statuses"]["mfj"]["standard_deduction"]
    path.write_text(json.dumps(result), encoding="utf-8")
    record_basic_deduction()
    assert _package(tmp_path).returncode == 0


def test_reviewed_zero_credit_clears_export_until_its_manual_worksheet_changes(tmp_path):
    flags = {"has_education_expenses": True}
    profile = {"tax_year": 2025, "filing_status": "MFJ", "flags": flags}
    (tmp_path / "taxpayer_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_result(form_1040={"line_9_total_income": 100000,
                                                "line_12_standard_deduction": 30000,
                                                "line_20_schedule3_line8": 0})), encoding="utf-8")
    write_synthetic_filing_law(tmp_path)
    common_sources = ["result.json", "taxpayer_profile.json", "tax_law_2025.json"]
    material_evidence.record(tmp_path, "input.deduction_election", "Deduction election", "computed",
                             common_sources, deduction_method="standard")
    for gate in receipt.REQUIRED_GATES:
        receipt.record(tmp_path, gate, receipt.PASS)
    denied = _package(tmp_path)
    assert denied.returncode == 2 and "nonrefundable credit worksheets" in denied.stderr

    worksheet = tmp_path / "synthetic-education-worksheet.json"
    worksheet.write_text('{"eligible_paid_expenses": 0, "reason": "All expenses covered by grants"}', encoding="utf-8")
    material_evidence.record(tmp_path, "input.nonrefundable_credits", "Education credit", "reviewed",
                             common_sources + [worksheet.name])
    receipt.record(tmp_path, "tie_out", receipt.PASS)
    assert _package(tmp_path).returncode == 0

    worksheet.write_text('{"eligible_paid_expenses": 1000}', encoding="utf-8")
    receipt.record(tmp_path, "tie_out", receipt.PASS)
    changed = _package(tmp_path)
    assert changed.returncode == 2 and "nonrefundable credit worksheets. A source changed" in changed.stderr


def test_applicable_amt_and_child_credit_law_block_final_even_with_passing_gates(tmp_path):
    result = _result(form_1040={"line_9_total_income": 100000},
                     form_6251={"iso_bargain_element": 8000})
    result_path = tmp_path / "result.json"
    profile_path = tmp_path / "taxpayer_profile.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    profile_path.write_text(json.dumps({"tax_year": 2025, "filing_status": "MFJ",
                                        "dependents": [{"relationship": "child"}]}), encoding="utf-8")
    law = write_synthetic_filing_law(tmp_path)
    law_path = tmp_path / "tax_law_2025.json"

    def recertify():
        write_synthetic_filing_inputs(tmp_path)
        for gate in receipt.REQUIRED_GATES:
            receipt.record(tmp_path, gate, receipt.PASS)

    recertify()
    blocked = _package(tmp_path)
    assert blocked.returncode == 2 and blocked.stdout == ""
    assert "federal.amt.exemption.mfj" in blocked.stderr
    assert "federal.child_tax_credit.refundable_limit" in blocked.stderr
    draft = _package(tmp_path, "--draft")
    assert draft.returncode == 0 and "federal.amt.exemption.mfj" in draft.stdout

    law["federal"].update(amt={"exemption": {"mfj": 140000},
                              "phaseout_start": {"mfj": 1000000}, "rate_28_breakpoint": 240000},
                          child_tax_credit={"amount_per_child": 2000, "phase_out_start": {"mfj": 390000},
                                            "refundable_limit": 1500})
    law_path.write_text(json.dumps(law), encoding="utf-8")
    recertify()
    assert "_sources.federal.amt" in _package(tmp_path).stderr

    law = write_synthetic_filing_law(tmp_path)
    assert "tax_law_2025.json changed" in _package(tmp_path).stderr
    recertify()
    assert _package(tmp_path).returncode == 0

    law["_provenance"]["federal.amt"]["applies_to"] = 2024
    law_path.write_text(json.dumps(law), encoding="utf-8")
    recertify()
    assert "covered year and retrieval date in _provenance.federal.amt" in _package(tmp_path).stderr

    verified = write_synthetic_filing_law(tmp_path)
    verified["federal"]["amt"]["exemption"]["mfj"] += 1000
    law_path.write_text(json.dumps(verified), encoding="utf-8")
    recertify()
    assert "federal.amt.exemption.mfj: record the exact verified figure" in _package(tmp_path).stderr

    from agents.lib.tax_law_fetcher import set_figure
    set_figure(2025, "federal.amt.exemption.mfj", 140000, "https://www.irs.gov/filing", str(tmp_path))
    recertify()
    assert _package(tmp_path).returncode == 0


def test_a_leaf_keeps_its_own_provenance_but_cannot_verify_its_sibling(tmp_path):
    from agents.lib.tax_computations.federal.applicable_law import filing_issues

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result()), encoding="utf-8")
    law = write_synthetic_filing_law(tmp_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    now = datetime.now()
    entry = law["_provenance"]["federal.filing_statuses"]
    law["_provenance"]["federal.filing_statuses.mfj.brackets"] = dict(entry)
    law["federal"]["filing_statuses"]["mfj"]["standard_deduction"] += 1000  # edited after `set`
    issues = filing_issues(result, {}, law, now=now)
    assert issues == ["federal.filing_statuses.mfj.standard_deduction: record the exact verified figure "
                      "with tax_law_fetcher.py set."]


def test_a_retrieval_date_is_not_a_freshness_window(tmp_path):
    """The stamp says when `set` ran. A 14-day window on it was cleared by
    re-running `set` on an unchanged value, with no fetch at all."""
    from agents.lib.tax_computations.federal.applicable_law import filing_issues

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result()), encoding="utf-8")
    law = write_synthetic_filing_law(tmp_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    now = datetime.now()
    for entry in law["_provenance"].values():
        entry["retrieved"] = (now - timedelta(days=45)).isoformat()
    assert filing_issues(result, {}, law, now=now) == []
    law["_provenance"]["federal.filing_statuses"]["retrieved"] = (now + timedelta(days=2)).isoformat()
    assert any("in the future" in issue for issue in filing_issues(result, {}, law, now=now))


@pytest.mark.parametrize(("form_1040", "block"), [
    ({"line_2b_taxable_interest": 70000}, "federal.niit"),
    ({"line_1a_wages": 400000}, "federal.additional_medicare_tax"),
])
def test_law_a_return_uses_is_required_from_its_inputs(tmp_path, form_1040, block):
    """Keyed on its output line, a missing NIIT or Additional Medicare figure
    produced no line, so nothing required it and the tax was left out."""
    from agents.lib.tax_computations.federal.applicable_law import filing_issues

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result(form_1040=form_1040)), encoding="utf-8")
    law = write_synthetic_filing_law(tmp_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert filing_issues(result, {}, law) == []
    law["federal"][block.split(".")[1]] = None
    assert any(issue.startswith(block) and "missing" in issue for issue in filing_issues(result, {}, law))


def test_a_cache_built_by_create_and_set_passes_for_a_schedule_c_return(tmp_path):
    """The seeded SE factor and rates were required with provenance nothing can
    give them, so every Schedule C cache built the documented way was blocked."""
    from agents.lib.tax_computations.federal.applicable_law import filing_issues
    from agents.lib.tax_law_fetcher import create_empty_tax_law

    seed = create_empty_tax_law(2025)["federal"]
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result(form_1040={"line_1a_wages": 50000})), encoding="utf-8")
    law = write_synthetic_filing_law(tmp_path)
    law["federal"]["se_tax"] = seed["se_tax"]
    law["federal"]["social_security"] = dict(seed["social_security"], wage_base=176100)
    for block in ("se_tax", "social_security", "niit", "additional_medicare_tax"):
        law["_sources"].pop(f"federal.{block}", None)
        law["_provenance"].pop(f"federal.{block}", None)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["schedule_se"] = {"schedule_2_line_4_se_tax": 100}
    issues = filing_issues(result, {}, law)
    assert issues == ["federal.social_security.wage_base: pin an authoritative source at _sources.federal.social_security."]


def test_empty_exchange_rates_only_blocks_a_return_using_currency_conversion(tmp_path):
    from agents.lib.tax_computations.federal.applicable_law import filing_issues

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result()), encoding="utf-8")
    law = write_synthetic_filing_law(tmp_path)
    law["exchange_rates"] = {}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert filing_issues(result, {}, law) == []
    assert filing_issues(result, {"foreign_currencies": ["EUR"]}, law) == [
        "exchange_rates.EUR: required covered-year figure is missing."]


def test_state_regime_uses_the_filing_state(tmp_path):
    from agents.lib.tax_computations.federal.applicable_law import filing_issues

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result()), encoding="utf-8")
    law = write_synthetic_filing_law(tmp_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    profile = {"address": {"state": "FL"}}
    assert filing_issues(result, profile, law) == ["The cache needs the covered-year state law for FL."]
    (tmp_path / "taxpayer_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    assert filing_issues(result, profile, write_synthetic_filing_law(tmp_path)) == []


def _material_cli(tmp_path, *args):
    return subprocess.run(
        [sys.executable, "-m", "agents.lib.material_evidence", "--workspace", str(tmp_path), *args],
        capture_output=True, text=True, cwd=FIX_REPO)


@pytest.mark.parametrize("outcome", ["computed", "reviewed", "not_applicable"])
def test_material_outcome_controls_export_and_reopens_on_source_change(tmp_path, outcome):
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    _prepare_export(tmp_path)
    source = tmp_path / "reviewed-worksheet.json"
    source.write_text("{}")
    args = ("--id", "estimated-payments", "--label", "Form 1040 estimated payments")
    recorded = _material_cli(tmp_path, *args, "--outcome", "open")
    assert recorded.returncode == 0, recorded.stderr
    material_evidence.record(tmp_path, "source-field", "W-2 Box 12", "open", [])
    for gate in receipt.REQUIRED_GATES:
        receipt.record(tmp_path, gate, receipt.PASS)

    final = _package(tmp_path)
    assert final.returncode == 2 and final.stdout == ""
    assert "Open material item: Form 1040 estimated payments" in final.stderr
    draft = _package(tmp_path, "--draft")
    assert draft.returncode == 0, draft.stderr
    assert "NOT VALIDATED" in draft.stdout
    assert "Open material item: Form 1040 estimated payments" in draft.stdout
    assert "Open material item: W-2 Box 12" in draft.stdout

    recorded = _material_cli(tmp_path, *args, "--outcome", outcome, "--input", source.name)
    assert recorded.returncode == 0, recorded.stderr
    material_evidence.record(tmp_path, "source-field", "W-2 Box 12", "reviewed", [source.name])
    assert "material_evidence.json changed" in _package(tmp_path).stderr
    receipt.record(tmp_path, "tie_out", receipt.PASS)
    assert _package(tmp_path).returncode == 0

    source.write_text('{"revised": true}')
    receipt.record(tmp_path, "tie_out", receipt.PASS)
    stale = _package(tmp_path)
    assert stale.returncode == 2
    assert "Form 1040 estimated payments. A source changed or is missing." in stale.stderr


def test_acknowledged_form_8962_stays_open_while_a_manual_worksheet_is_advisory(tmp_path):
    flags = dict.fromkeys(capabilities._REQUIRED_GATE_FLAGS, False)
    flags.update(has_marketplace_insurance=True, may_qualify_for_eic=True)
    profile = {"tax_year": 2025, "address": {"state": "FL"}, "filing_status": "MFJ", "flags": flags,
               "acknowledged_limitations": ["marketplace_insurance"]}
    profile_path = tmp_path / "taxpayer_profile.json"
    profile_path.write_text(json.dumps(profile))
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    write_synthetic_filing_law(tmp_path)

    def run_gates():
        write_synthetic_filing_inputs(tmp_path)
        gate = subprocess.run([sys.executable, "-m", "agents.lib.capabilities",
                               "--workspace", str(tmp_path)], capture_output=True, text=True,
                              cwd=FIX_REPO)
        assert gate.returncode == 0, gate.stderr
        assert "MANUAL WORKSHEET" in gate.stdout
        for name in ("return_builder", "tie_out"):
            receipt.record(tmp_path, name, receipt.PASS)

    run_gates()
    evidence = tmp_path / material_evidence.FILENAME
    assert json.loads(evidence.read_text())["items"]["form.marketplace_insurance"]["outcome"] == "open"
    assert "Open material item: Form 8962" in _package(tmp_path).stderr
    draft = _package(tmp_path, "--draft", "--json")
    assert draft.returncode == 0, draft.stderr
    details = json.loads(draft.stdout)
    assert not details["validated"]
    assert any("Open material item: Form 8962" in reason for reason in details["receipt"]["reasons"])
    failed_record = _material_cli(tmp_path, "--id", "form.marketplace_insurance",
                                  "--label", "Form 8962", "--outcome", "reviewed",
                                  "--input", "result.json")
    assert failed_record.returncode == 1
    assert "required forms must be completed through their filing workflow" in failed_record.stderr

    profile["flags"]["has_marketplace_insurance"] = False
    profile_path.write_text(json.dumps(profile))
    run_gates()
    assert _package(tmp_path).returncode == 0


@pytest.mark.parametrize("wage, box1_status", [(None, None), (None, "unread"), (0, None), (38000, None)])
def test_w2_box_outcomes_reach_the_filing_gate(tmp_path, wage, box1_status):
    documents = tmp_path / "documents"
    documents.mkdir()
    wage_doc = documents / "w2.pdf"
    interest_doc = documents / "interest.pdf"
    wage_doc.write_bytes(b"%PDF-1.4 synthetic W-2")
    interest_doc.write_bytes(b"%PDF-1.4 synthetic 1099-INT")
    withholding = 500 if wage is None else 0
    source = {"doc_id": "w2-sample", "file": "documents/w2.pdf", "form_type": "Form W-2",
              "payer": "Example employer", "person": "spouse",
              "source_sha256": sha256(wage_doc.read_bytes()).hexdigest(),
              "key_boxes": {"federal_withholding_w2": withholding}}
    if wage is not None:
        source["key_boxes"]["wages"] = wage
    if box1_status:
        source["w2_box_status"] = {"1": box1_status}
    parse_records.write_record(tmp_path, {"doc_id": "interest", "file": "documents/interest.pdf",
                                         "form_type": "1099-INT", "payer": "Example bank",
                                         "source_sha256": sha256(interest_doc.read_bytes()).hexdigest(),
                                         "key_boxes": {"taxable_interest": 200}})

    def run(module, *args):
        return subprocess.run([sys.executable, "-m", f"agents.lib.{module}", "--workspace",
                               str(tmp_path), *args], capture_output=True, text=True, cwd=FIX_REPO)

    def build_and_check():
        parse_records.write_record(tmp_path, source)
        ledger_path = parse_records.write_ledger(tmp_path)
        ledger = tie_out.load_ledger(json.loads(ledger_path.read_text()))
        assert next(record for record in ledger if record.doc_id == "w2-sample").person == "spouse"
        lines = return_builder.ledger_income(ledger)
        data = _result(form_1040={"line_1a_wages": lines["line_1a_wages"],
                                  "line_2b_taxable_interest": lines["line_2b_taxable_interest"],
                                  "line_25a_w2_withholding": withholding})
        (tmp_path / "result.json").write_text(json.dumps(data))
        _prepare_export(tmp_path)
        for gate in ("capabilities", "return_builder"):
            receipt.record(tmp_path, gate, receipt.PASS)
        tying = run("tie_out", "--json")
        assert tying.returncode == 0, tying.stderr
        assert receipt.verify(tmp_path, required=(return_builder.GATE,)).valid
        return lines["line_1a_wages"]

    assert build_and_check() == (wage or 0)
    final = run("filing_package", "--result", "result.json")
    if wage is None:
        assert final.returncode == 2 and "Open material item: W-2 #1 Box 1" in final.stderr
        draft = run("filing_package", "--result", "result.json", "--draft")
        assert draft.returncode == 0 and "Open material item: W-2 #1 Box 1" in draft.stdout
    else:
        assert final.returncode == 0, final.stderr
    if box1_status == "unread":
        # Local review confirms an amount from the original for the same owner.
        source["key_boxes"]["wages"] = 38000
        del source["w2_box_status"]
        assert build_and_check() == 38000
        repaired = run("filing_package", "--result", "result.json")
        assert repaired.returncode == 0, repaired.stderr


def test_w2_blank_and_verified_zero_are_distinct_and_cannot_conflict():
    raw = {"doc_id": "w2", "file": "documents/w2.pdf", "form_type": "W-2", "payer": "Example employer",
           "key_boxes": {"wages": 0}, "w2_box_status": {"2": "blank"}}
    ledger = tie_out.load_ledger({"records": [raw]})
    assert dict(ledger[0].key_boxes) == {"wages": 0, "federal_withholding_w2": 0}
    items = tie_out._w2_outcomes(ledger, "a" * 64)
    assert [item["outcome"] for item in items.values()] == ["reviewed", "not_applicable"]
    with pytest.raises(tie_out.TieOutError, match="reconcile Form W-2c blanks"):
        tie_out.load_ledger({"records": [dict(raw, form_type="Form W-2c")]})
    raw["key_boxes"]["federal_withholding_w2"] = 0
    with pytest.raises(tie_out.TieOutError, match="box 2 has both"):
        tie_out.load_ledger({"records": [raw]})


def _w2(form_type="W-2", **boxes):
    return {"doc_id": "w2", "file": "documents/w2.pdf", "form_type": form_type,
            "payer": "Example employer", "key_boxes": boxes}


def _w2_items(*records):
    ledger = tie_out.load_ledger({"records": list(records)})
    return [(item["label"], item["outcome"])
            for item in tie_out._w2_outcomes(ledger, "a" * 64).values()]


def test_w2_item_ids_survive_the_reader_for_any_doc_id(tmp_path):
    """The id embeds a hex digest, and about one digest in four holds a run of
    nine decimal digits that `redact` reads as an identifier. The reader
    refused those ids, so tie-out failed for an ordinary W-2 and stayed
    wedged: the next sync read the bad file before it could replace it."""
    from agents.lib.secure_fs import redact
    doc_id = next(f"w2-{n}" for n in range(1000)
                  if redact(receipt._entry_key(f"w2-{n}")) != receipt._entry_key(f"w2-{n}"))
    ledger = tie_out.load_ledger({"records": [dict(_w2(wages=38000, federal_withholding_w2=500),
                                                   doc_id=doc_id)]})
    items = tie_out._w2_outcomes(ledger, "a" * 64)
    material_evidence.sync_group(tmp_path, material_evidence.W2_PREFIX, items)
    assert material_evidence.read(tmp_path)[1] == items


def test_the_writer_refuses_a_record_its_reader_would_refuse(tmp_path):
    bad = {"note." + "1" * 9: {"label": "Form 1040 line 1", "outcome": "open", "inputs": {}}}
    with pytest.raises(material_evidence.MaterialEvidenceError, match="invalid material item"):
        material_evidence.sync_group(tmp_path, "note.", bad)
    assert not (tmp_path / material_evidence.FILENAME).exists()


def test_w2_ids_cannot_be_recorded_by_hand(tmp_path):
    with pytest.raises(material_evidence.MaterialEvidenceError, match="parse ledger"):
        material_evidence.record(tmp_path, "w2." + "a" * 64 + ".1", "W-2 Box 1", "open", [])


@pytest.mark.parametrize("form_type", ["Form W-2 (Copy B)", "W-2 2025", "IRS Form W-2",
                                       "W-2 Wage and Tax Statement", "Wage and Tax Statement"])
def test_a_w2_withholding_only_record_is_open_whatever_its_spelling(form_type):
    """An exact "W2" code let every longer spelling through with no items, so
    the AA128 record (withholding, no wages) reached a final package."""
    items = _w2_items(_w2(form_type, federal_withholding_w2=500, ss_wages=38000))
    assert items[0] == ("W-2 #1 Box 1 (wages)", "open")


@pytest.mark.parametrize("form_type", ["W-2G", "W2-G", "Form W-2G", "W-2GU", "1099-INT"])
def test_other_forms_carry_no_w2_items(form_type):
    assert not tie_out._is_w2(form_type, {"taxable_interest": 10})


@pytest.mark.parametrize("form_type", ["Form W-2c (Corrected)", "Corrected W-2", "W-2C"])
def test_a_corrected_w2_cannot_declare_a_blank_whatever_its_spelling(form_type):
    """A blank on a W-2c means "no change", not zero: read the original."""
    raw = dict(_w2(form_type, federal_withholding_w2=600), w2_box_status={"1": "blank"})
    with pytest.raises(tie_out.TieOutError, match="reconcile Form W-2c blanks"):
        tie_out.load_ledger({"records": [raw]})


@pytest.mark.parametrize("boxes, outcome", [({"federal_withholding_w2": 500}, "open"),
                                            ({"ss_wages": 38000}, "open"),
                                            ({"ss_tips": 900}, "open"),
                                            ({"ss_tax_withheld": 2356}, "open"),
                                            ({"medicare_tax_withheld": 551}, "open"),
                                            ({}, "not_applicable")])
def test_a_blank_box_1_beside_other_w2_amounts_stays_open(boxes, outcome):
    """The AA128 shape again, declared rather than dropped. A verified zero is
    the amount 0 and stays the route for a genuine one."""
    raw = dict(_w2("W-2", **boxes), w2_box_status={"1": "blank"})
    assert _w2_items(raw)[0][1] == outcome
    zero = _w2("W-2", wages=0, **boxes)
    assert _w2_items(zero)[0][1] == "reviewed"


def test_source_item_ids_survive_the_reader_for_any_path(tmp_path):
    """The W-2 ids' defect again: a path whose digest holds a run of nine
    decimal digits made the source item unreadable and tie-out wedged."""
    from agents.lib.secure_fs import redact
    path = next(f"documents/interest{n}.csv" for n in range(1000)
                if redact(receipt._entry_key(f"documents/interest{n}.csv"))
                != receipt._entry_key(f"documents/interest{n}.csv"))
    ledger = tie_out.load_ledger({"records": [{"doc_id": "interest", "file": path, "form_type": "1099-INT",
                                               "payer": "Example bank", "key_boxes": {"taxable_interest": 200}}]})
    items, _ = tie_out._source_outcomes(ledger, {receipt._entry_key(path): {"digest": "a" * 64}})
    assert len(items) == 1
    material_evidence.sync_group(tmp_path, material_evidence.SOURCE_PREFIX, items)
    assert material_evidence.read(tmp_path)[1] == items


def test_same_path_source_correction_requires_reparsing_before_final_export(tmp_path):
    source_file = tmp_path / "documents" / "interest.csv"
    source_file.parent.mkdir()
    source_file.write_text("Taxable interest\n200\n")
    source = {"doc_id": "interest", "file": "documents/interest.csv",
              "form_type": "1099-INT", "payer": "Example bank",
              "key_boxes": {"taxable_interest": 200}}

    def run(module, *args):
        return subprocess.run([sys.executable, "-m", f"agents.lib.{module}", "--workspace",
                               str(tmp_path), *args], capture_output=True, text=True, cwd=FIX_REPO)

    def publish_and_check():
        parse_records.write_record(tmp_path, source)
        ledger = parse_records.write_ledger(tmp_path)
        amount = source["key_boxes"]["taxable_interest"]
        (tmp_path / "result.json").write_text(json.dumps(_result(form_1040={"line_2b_taxable_interest": amount})))
        _prepare_export(tmp_path)
        for gate in ("capabilities", "return_builder"):
            receipt.record(tmp_path, gate, receipt.PASS)
        checked = run("tie_out", "--json")
        assert checked.returncode == 0, checked.stderr
        return json.loads(ledger.read_text())["records"][0]

    def reparse():
        extracted = subprocess.run(
            [sys.executable, "-m", "agents.lib.parse_utils", "--workspace", str(tmp_path),
             "csv", source["file"], "--with-source-digest"], capture_output=True, text=True, cwd=FIX_REPO)
        assert extracted.returncode == 0, extracted.stderr
        data = json.loads(extracted.stdout)
        source["source_sha256"] = data["source_sha256"]
        source["key_boxes"]["taxable_interest"] = int(data["data"][1][0])
        return publish_and_check()

    publish_and_check()
    legacy = run("filing_package", "--result", "result.json")
    assert legacy.returncode == 2 and "re-parse required to bind source bytes" in legacy.stderr
    legacy_draft = run("filing_package", "--result", "result.json", "--draft")
    assert legacy_draft.returncode == 0 and "re-parse required to bind source bytes" in legacy_draft.stdout

    bound = reparse()
    assert bound["source_sha256"] == source["source_sha256"]
    assert publish_and_check() == bound  # The carried-over record keeps its original digest.
    assert run("filing_package", "--result", "result.json").returncode == 0

    source_file.write_text("Taxable interest\n275\n")
    assert run("filing_package", "--result", "result.json").returncode == 2
    retied = run("tie_out", "--json")
    assert retied.returncode == 2 and json.loads(retied.stdout)["changed_sources"]
    assert run("filing_package", "--result", "result.json").returncode == 2
    assert reparse()["key_boxes"]["taxable_interest"] == 275
    assert run("filing_package", "--result", "result.json").returncode == 0


def test_validated_package_shows_reviewed_source_dispositions(tmp_path):
    documents = tmp_path / "documents"
    documents.mkdir()
    content = b"%PDF-1.4 synthetic duplicate source"
    for name in ("retained.pdf", "copy.pdf"):
        (documents / name).write_bytes(content)
    source = {"doc_id": "kept", "file": "documents/retained.pdf", "form_type": "1099-INT",
              "payer": "Example bank", "person": "primary", "source_sha256": sha256(content).hexdigest(),
              "key_boxes": {"taxable_interest": 100}}
    parse_records.write_record(tmp_path, source)
    parse_records.write_record(tmp_path, dict(source, doc_id="copy", file="documents/copy.pdf", key_boxes={},
                                              skipped="exact copy", source_form={"duplicate_of": "kept"}))
    parse_records.write_ledger(tmp_path)
    (tmp_path / "result.json").write_text(json.dumps(_result(form_1040={"line_2b_taxable_interest": 100})))
    _prepare_export(tmp_path)
    for gate in ("capabilities", "return_builder"):
        receipt.record(tmp_path, gate, receipt.PASS)
    command = [sys.executable, "-m"]
    tying = subprocess.run([*command, "agents.lib.tie_out", "--workspace", str(tmp_path)], cwd=FIX_REPO,
                            capture_output=True, text=True)
    assert tying.returncode == 0, tying.stderr
    package = subprocess.run([*command, "agents.lib.filing_package", "--workspace", str(tmp_path),
                              "--result", "result.json"], cwd=FIX_REPO, capture_output=True, text=True)
    assert package.returncode == 0, package.stderr
    assert "Document #1 was an exact copy of retained document #2." in package.stdout
    assert "NOT FULLY VERIFIED" not in package.stdout


def test_a_package_needs_the_derived_lines_gate_too(tmp_path):
    """Step 12.6 is a REQUIRED gate. Before it was receipted an agent could
    skip it — or pass it and then edit line 24 — and nothing downstream could
    tell; the tie-out's income checks cannot see a hand-edited derived line,
    because six of its category sums now produce the lines they compare."""
    from agents.lib import receipt
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    (tmp_path / "parse_ledger.json").write_text('{"records": []}')
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    receipt.record(tmp_path, "capabilities", receipt.PASS)
    receipt.record(tmp_path, "tie_out", receipt.PASS)
    out = _package(tmp_path)
    assert out.returncode == 2
    assert "return_builder gate has not run" in out.stderr


def test_a_gate_with_no_verdict_stamps_the_package_but_does_not_refuse_it(tmp_path):
    """A return whose Form 1040 lines 1-38 the builder does not govern is
    correct and complete, so refusing it would leave `--draft` — NOT VALIDATED
    across a good return — as the only way to a package. It assembles, and it
    says on page 1 that one gate verified nothing."""
    from agents.lib import receipt
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    (tmp_path / "parse_ledger.json").write_text('{"records": []}')
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    _prepare_export(tmp_path)
    receipt.record(tmp_path, "capabilities", receipt.PASS)
    receipt.record(tmp_path, "tie_out", receipt.PASS)
    receipt.record(tmp_path, "return_builder", receipt.NOT_APPLICABLE)
    out = _package(tmp_path)
    assert out.returncode == 0, out.stderr
    assert "NOT FULLY VERIFIED" in out.stdout
    assert "return_builder" in out.stdout
    # ...and NOT the draft banner: the package does describe this workspace.
    assert "NOT VALIDATED" not in out.stdout


def test_a_document_that_arrives_after_the_tie_out_refuses_the_package(tmp_path):
    """The Thursday 1099. The gate passed on Wednesday's documents, and nothing
    bound its verdict to that set until the receipt did."""
    from agents.lib import receipt
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    (tmp_path / "parse_ledger.json").write_text('{"records": []}')
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    _prepare_export(tmp_path)
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "w2.pdf").write_bytes(b"%PDF-1.4 first")
    for gate in receipt.REQUIRED_GATES:
        receipt.record(tmp_path, gate, receipt.PASS)
    assert _package(tmp_path).returncode == 0
    (tmp_path / "documents" / "1099int.pdf").write_bytes(b"%PDF-1.4 second")
    late = _package(tmp_path)
    assert late.returncode == 2
    assert "1099int.pdf appeared after that gate ran" in late.stderr


def test_cli_path_traversal_rejected(tmp_path):
    outside = tmp_path.parent / "outside.json"
    outside.write_text("{}")
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.filing_package",
         "--result", str(outside), "--workspace", str(tmp_path)],
        capture_output=True, text=True, cwd=FIX_REPO)
    assert out.returncode == 1


def test_a_valid_receipt_does_not_certify_another_result_file(tmp_path):
    """`--result` takes any path inside the workspace, and the gates only ever
    validate result.json: verifying the receipt while packaging some other file
    is a valid receipt on a return no gate has seen."""
    from agents.lib import receipt
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    (tmp_path / "parse_ledger.json").write_text('{"records": []}')
    result = json.dumps(_result(schedule_d={"line_16": 5000}))
    (tmp_path / "result.json").write_text(result)
    for gate in receipt.REQUIRED_GATES:
        receipt.record(tmp_path, gate, receipt.PASS)
    other = tmp_path / "result_draft.json"
    other.write_text(result)
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.filing_package",
         "--result", str(other), "--workspace", str(tmp_path)],
        capture_output=True, text=True, cwd=FIX_REPO)
    assert out.returncode == 2
    assert "no gate has seen this file" in out.stderr



def test_a_new_york_return_assembles_its_it201():
    """W10: the state table listed California alone, so a NY package had no
    state form at all. It comes from the registry now."""
    r = _result(new_york_it201={"line_46_total_state_tax": 4200})
    entries = FP.assembly_order(r)
    assert entries[-1] == {"key": "new_york_it201",
                           "title": "New York Form IT-201",
                           "sequence": "STATE"}


def test_a_florida_statement_is_not_a_state_form():
    r = _result(florida_state={"no_income_tax_statement": "FL has none"})
    assert [e["key"] for e in FP.assembly_order(r)] == ["form_1040"]


def test_the_new_york_checklist():
    """IT-201 instructions, Step 11: no federal W-2 or 1099-R, Form IT-2
    instead; payment and no-payment returns mail to different PO boxes."""
    r = _result(new_york_it201={"line_46_total_state_tax": 4200,
                                "line_80_amount_owed": 0})
    lines = "\n".join(FP.attachment_checklist(r))
    assert "do NOT send federal Form W-2 or 1099-R" in lines
    assert "IT-2 (Summary" in lines and "Do not staple" in lines
    assert "DIFFERENT PO boxes" in lines
    assert "NY balance due" not in lines and "Y-203" not in lines
    assert "Franchise Tax" not in lines  # no CA line on a NY-only return
    r["new_york_it201"]["line_80_amount_owed"] = 350
    r["new_york_it201"]["yonkers"] = {
        "line_56_yonkers_nonresident_earnings_tax": 120}
    lines = "\n".join(FP.attachment_checklist(r))
    assert "IT-201-V" in lines and "New York State Income Tax" in lines
    assert "Form Y-203" in lines


def test_a_two_state_return_keeps_california_then_new_york():
    r = _result(california_540={"ca_total_tax": 8000},
                schedule_ca_itemized={"total_ca_itemized": 30000},
                new_york_it201={"line_46_total_state_tax": 4200})
    assert [e["key"] for e in FP.assembly_order(r)][-3:] == [
        "california_540", "schedule_ca_itemized", "new_york_it201"]


@pytest.mark.parametrize("section, block", [
    ("new_york_it201", {"line_80_amount_owed": 0,
                        "refundable_credits_and_payments": {
                            "line_72_state_withholding": 3000}}),
    ("california_540", {"ca_withholding_box17": 3000, "ca_total_tax": 0}),
    ("schedule_ca_itemized", {"total_ca_itemized": 30000}),
])
def test_a_state_section_with_only_parsed_data_refuses_the_package(
        section, block):
    """Bias review: parse-stage withholding makes a section PRESENT while its
    tax and line 80 are template zeros — packaged, it read as a $0 return with
    no balance-due line. Both entry points refuse."""
    r = _result(**{section: block})
    with pytest.raises(FP.FilingPackageError, match="no computed"):
        FP.assembly_order(r)
    with pytest.raises(FP.FilingPackageError, match="no computed"):
        FP.attachment_checklist(r)


def test_zero_net_schedule1_still_attaches_and_renders_government_benefit_detail():
    # UI850 + governmental PFL500 - unrelated Schedule1 loss1350 =0;
    # a zero aggregate does not erase the income and loss lines to be filed.
    r=_result(schedule_1_government_benefits={
        "computed_tax_year":2025,"unemployment_gross":1000,"family_leave_gross":500,
        "unemployment_repaid":100,"other_income":-1350,"total":0})
    r["form_1040"]["line_8_schedule1_other"]=0
    entry=next(e for e in FP.assembly_order(r) if e["sequence"]=="01")
    assert entry["key"]=="schedule_1_government_benefits"
    r["schedule_1_government_benefits"]["total"]=500
    with pytest.raises(FP.FilingPackageError,match="total must equal"):
        FP.assembly_order(r)


def test_inactive_government_scaffold_does_not_conjure_a_schedule1_attachment():
    r=_result(schedule_1_government_benefits={
        "computed_tax_year":None,"other_income":None,"total":0})
    assert not any(e["sequence"]=="01" for e in FP.assembly_order(r))


def test_ca_owner_credit_attachments_are_conditional_on_available_credit():
    r = _result(california_540={"ca_taxable_income": 10000,
        "schedule_p": {"ftb_3510": {"line_32": 100}},
        "ftb_3804_cr": {"line_3": 200}})
    lines = "\n".join(FP.attachment_checklist(r))
    assert "attach official FTB3510" in lines
    assert "attach official FTB3804-CR" in lines
    r["california_540"]["schedule_p"]["ftb_3510"]["line_32"] = 0
    r["california_540"]["ftb_3804_cr"]["line_3"] = 0
    lines = "\n".join(FP.attachment_checklist(r))
    assert "attach official FTB3510" not in lines
    assert "attach official FTB3804-CR" not in lines


# AA113 review: triggers, deduction facts and the outcome checks, pinned
# directly so a fixture that records whatever is required cannot hide them.

_LAW_2025 = {"tax_year": 2025, "federal": {
    "filing_statuses": {"mfj": {"standard_deduction": 30000}, "single": {"standard_deduction": 15000}},
    "niit": {"rate": 0.038, "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}},
    "additional_medicare_tax": {"rate": 0.009, "threshold": {"single": 200000, "mfj": 250000, "mfs": 125000}}}}


def _inputs(form, profile=None, status="MFJ", **sections):
    result = {"meta": {"tax_year": 2025, "filing_status": status}, "form_1040": form, **sections}
    return material_evidence._required_filing_inputs(result, profile or {"filing_status": status}, _LAW_2025)


def test_a_prior_year_amt_credit_asks_for_the_credit_and_amt_reviews():
    """`form_8801_carryforward` was read and nothing writes it; rollover writes
    prior_year.amt_credit_carryforward."""
    required = _inputs({}, {"filing_status": "MFJ", "prior_year": {"amt_credit_carryforward": 12000}})
    assert "input.nonrefundable_credits" in required and "input.amt" in required
    from agents.lib.tax_computations.federal.applicable_law import _federal_figures
    fields = _federal_figures({"form_1040": {}}, {"prior_year": {"amt_credit_carryforward": 12000}}, {}, 2025, "mfj")
    assert "federal.amt.exemption.mfj" in fields


@pytest.mark.parametrize(("form", "required"), [
    ({"line_11_agi": 390000, "line_3b_ordinary_dividends": 30000}, True),   # NIIT applies
    ({"line_11_agi": 240000, "line_3b_ordinary_dividends": 30000}, False),  # under the threshold
    ({"line_1a_wages": 360000, "line_11_agi": 360000}, True),               # Additional Medicare
    ({"line_1a_wages": 240000, "line_11_agi": 240000}, False),
])
def test_niit_and_additional_medicare_ask_for_the_other_tax_review(form, required):
    """A line 23 at 0 left both taxes out of a validated $390,000 return."""
    items = _inputs(form)
    assert ("input.other_taxes" in items) is required
    if required:
        assert items["input.other_taxes"] is True  # not_applicable is refused


def test_box_5_wage_evidence_reaches_the_material_and_law_checks():
    from agents.lib.tax_computations.federal.applicable_law import filing_issues

    record = tie_out.LedgerRecord(
        "w2", "documents/w2.pdf", "W-2", "synthetic", "primary",
        (("wages", 195000), ("medicare_wages", 205000)), None,
    )
    result = {"meta": {"tax_year": 2025, "filing_status": "Single"},
              "form_1040": {"line_1a_wages": 195000}}
    assert "input.other_taxes" not in material_evidence._required_filing_inputs(result, {}, _LAW_2025)
    with_box_5 = material_evidence._required_filing_inputs(result, {}, _LAW_2025, ledger=[record])
    assert with_box_5["input.other_taxes"] is True
    # Applicability already requests the Medicare cache for positive box 1.
    # A return with no box 1 wages must also require it when box 5 is positive.
    result["form_1040"]["line_1a_wages"] = 0
    law = {"tax_year": 2025}
    without_box_5 = set(filing_issues(result, {}, law))
    with_box_5_law = set(filing_issues(result, {}, law, ledger=[record]))
    assert with_box_5_law - without_box_5 == {
        f"federal.additional_medicare_tax.{key}: required covered-year figure is missing."
        for key in ("threshold.single", "rate")
    }


def _box_w2(doc_id, **boxes):
    return tie_out.LedgerRecord(doc_id, f"documents/{doc_id}.pdf", "W-2", "synthetic", "primary",
                                tuple(boxes.items()), None)


def test_box_5_raises_the_medicare_screen_but_never_lowers_it():
    # Nothing ties box 5 to a source, so a misread box 5 cannot clear the
    # review line 1a requires.
    assert tie_out.filing_medicare_wages([_box_w2("a", wages=210000, medicare_wages=195000)],
                                         210000) == 210000
    # A W-2 without box 5 counts the larger of box 1 and boxes 3 and 7: the
    # 401(k) deferral that lowers box 1 does not lower box 3.
    with_box_5 = _box_w2("a", wages=130000, medicare_wages=150000)
    assert tie_out.filing_medicare_wages([with_box_5, _box_w2("b", wages=110000)], 240000) == 260000
    assert tie_out.filing_medicare_wages([_box_w2("c", wages=115000, ss_wages=130000)], 115000) == 130000


def test_a_surviving_spouse_screens_medicare_at_the_single_threshold():
    form = {"line_1a_wages": 220000, "line_11_agi": 230000, "line_2b_taxable_interest": 5000}
    # Form 8959 gives a qualifying surviving spouse the $200,000 threshold.
    assert _inputs(dict(form), status="QSS")["input.other_taxes"] is True
    # NIIT gives the same status the $250,000 joint threshold.
    assert "input.other_taxes" not in _inputs({**form, "line_1a_wages": 0}, status="QSS")


def test_a_surviving_spouse_needs_the_thresholds_its_taxes_use():
    from agents.lib.tax_computations.federal.applicable_law import filing_issues

    result = {"meta": {"tax_year": 2025, "filing_status": "QSS"},
              "form_1040": {"line_1a_wages": 100000, "line_2b_taxable_interest": 1000}}
    issues = set(filing_issues(result, {}, {"tax_year": 2025}))

    def missing(path):
        return f"{path}: required covered-year figure is missing."
    assert {missing("federal.additional_medicare_tax.threshold.single"),
            missing("federal.niit.threshold.mfj")} <= issues
    assert not {missing("federal.additional_medicare_tax.threshold.mfj"),
                missing("federal.niit.threshold.single")} & issues


def test_one_spouses_business_loss_does_not_hide_the_others_medicare_tax():
    # Schedule C nets the spouses to $40,000; Form 8959 line 8 counts only the
    # primary's $92,350, so wages plus earnings exceed the joint $250,000.
    owners = {"primary": {"net_earnings": 92350}, "spouse": {"net_earnings": 0}}
    sections = {"schedule_c": {"line_31_net": 40000}, "schedule_se": {"by_owner": owners}}
    assert _inputs({"line_1a_wages": 200000}, **sections)["input.other_taxes"] is True
    owners["primary"]["net_earnings"] = 36940
    assert "input.other_taxes" not in _inputs({"line_1a_wages": 200000}, **sections)


def test_a_symlinked_ledger_inside_the_workspace_reads_as_the_gates_read_it(tmp_path):
    from agents.lib import receipt
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    (tmp_path / "ledger-copy.json").write_text('{"records": []}')
    (tmp_path / "parse_ledger.json").symlink_to("ledger-copy.json")
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    _prepare_export(tmp_path)
    for gate in receipt.REQUIRED_GATES:
        receipt.record(tmp_path, gate, receipt.PASS)
    assert receipt.verify(tmp_path).valid
    filing = receipt.verify(tmp_path, for_filing=True)
    assert not any("parse_ledger.json" in reason for reason in filing.reasons), filing.reasons


def test_excess_social_security_needs_the_payment_review():
    """Nothing compares the excess with W-2 box 4, and it was excluded here."""
    items = _inputs({"line_31_schedule3_part2": 4000, "schedule_3_line_11_excess_ss": 4000})
    assert items["input.payments"] is True


def test_two_social_security_w2s_for_one_employee_require_the_wage_base():
    from agents.lib.tax_computations.federal.applicable_law import filing_issues

    first = tie_out.LedgerRecord("first", "documents/w2-a.pdf", "W-2", "Employer A", "primary",
                                 (("ss_wages", 1000),), None)
    other = tie_out.LedgerRecord("second", "documents/w2-b.pdf", "W-2", "Employer B", "primary",
                                 (("ss_wages", 1000),), None)
    spouse = tie_out.LedgerRecord("spouse", "documents/w2-c.pdf", "W-2", "Employer C", "spouse",
                                  (("ss_wages", 1000),), None)
    result = {"meta": {"tax_year": 2025, "filing_status": "MFJ"}, "form_1040": {}}
    law = {"tax_year": 2025}
    expected = "federal.social_security.wage_base: required covered-year figure is missing."
    assert expected in filing_issues(result, {}, law, ledger=[first, other])
    assert expected not in filing_issues(result, {}, law, ledger=[first, spouse])


def test_railroad_tax_source_requires_the_manual_combined_payment_review():
    record = tie_out.LedgerRecord("rrta", "documents/rrta-w2.pdf", "W-2", "Railroad", "primary",
                                  (("rrta_tier1_tax_withheld", 2000),), None,
                                  source_form={"employer_ein": "12-0000001"})
    result = {"meta": {"filing_status": "Single"}, "form_1040": {}}
    inputs = material_evidence._required_filing_inputs(result, {}, _LAW_2025, ledger=[record])
    assert inputs["input.payments"] is False   # A reviewed inapplicable credit is legitimate.
    law = {"federal": {"social_security": {"wage_base": 176100, "employee_rate": 0.062}}}

    def checked(records, claimed):
        return tie_out._excess_ss_checks(
            records, {"form_1040": {"schedule_3_line_11_excess_ss": claimed}}, {}, law)
    # Tier 1 tax shares the Social Security base and rate: $2,000 withheld
    # leaves no combined excess, so a claimed $400 is refused.
    assert checked([record], 400)
    # A second railroad employer: $12,000 withheld less the $10,918.20 maximum.
    second = tie_out.LedgerRecord("rrta-2", "documents/rrta-w2-2.pdf", "W-2", "Short Line", "primary",
                                  (("rrta_tier1_tax_withheld", 10000),), None,
                                  source_form={"employer_ein": "12-0000002"})
    assert not checked([record, second], 1081.80)
    assert checked([record, second], 1081.81)


_SS_LAW = {"federal": {"social_security": {"wage_base": 176100, "employee_rate": 0.062}}}


def _ss_w2(doc_id, payer, person, wages, withheld, *, employer_ein):
    return tie_out.LedgerRecord(doc_id, f"documents/{doc_id}.pdf", "W-2", payer, person,
                                (("ss_wages", wages), ("ss_tax_withheld", withheld)), None,
                                source_form={"employer_ein": employer_ein})


def _line_11(records, claimed, status="MFJ", profile=None):
    return tie_out._excess_ss_checks(
        records, {"meta": {"filing_status": status},
                  "form_1040": {"schedule_3_line_11_excess_ss": claimed}}, profile or {}, _SS_LAW)


def test_a_spouses_railroad_tax_leaves_the_other_spouses_credit_checked():
    # Two employers for the primary: $12,400 withheld less $10,918.20.
    primary = [_ss_w2("a", "Alpha", "primary", 100000, 6200, employer_ein="12-0000001"),
               _ss_w2("b", "Beta", "primary", 100000, 6200, employer_ein="12-0000002")]
    railroad = tie_out.LedgerRecord("rr", "documents/rr.pdf", "W-2", "Railroad", "spouse",
                                    (("rrta_tier1_tax_withheld", 2000),), None)
    assert not _line_11([*primary, railroad], 1481.80)
    assert _line_11([*primary, railroad], 5000)


@pytest.mark.parametrize("person,status", [("joint", "MFJ"), ("spouse", "Single")])
def test_railroad_only_w2_requires_an_employee_on_this_return(person, status):
    forms = [tie_out.LedgerRecord(
        doc_id, f"documents/{doc_id}.pdf", "W-2", payer, person,
        (("rrta_tier1_tax_withheld", 6200),), None, source_form={"employer_ein": ein})
        for doc_id, payer, ein in (("rr", "Railroad", "12-0000001"), ("sl", "Short Line", "12-0000002"))]
    assert any("needs the employee" in issue for issue in _line_11(forms, 1481.80, status=status))


def test_one_employer_under_two_names_is_one_employer():
    # A payroll change: "Acme" and "Acme Inc" are one employer, whose own
    # over-withholding the employer refunds. No credit.
    forms = [_ss_w2("a", "Acme", "primary", 150000, 9300, employer_ein="12-0000001"),
             _ss_w2("b", "Acme Inc", "primary", 50000, 3100, employer_ein="12-0000001")]
    assert not _line_11(forms, 0, status="Single")
    assert _line_11(forms, 1481.80, status="Single")


@pytest.mark.parametrize("status,profile", [("MFS", {}), (None, {}), ("MFJ", {"filing_status": "MFS"})])
def test_a_spouses_w2s_support_no_credit_off_a_joint_return(status, profile):
    forms = [_ss_w2("a", "Alpha", "spouse", 100000, 6200, employer_ein="12-0000001"),
             _ss_w2("b", "Beta", "spouse", 100000, 6200, employer_ein="12-0000002")]
    assert _line_11(forms, 1481.80, status=status, profile=profile)


@pytest.mark.parametrize(("profile", "method", "amount", "accepted"), [
    ({}, "standard", 30000, True),
    ({"ca_exemptions": {"primary_blind": True}}, "standard", 30000, False),
    ({"ca_exemptions": {"spouse_can_be_claimed": True}}, "standard", 30000, False),
    ({}, "standard_worksheet", 0, False),                                   # the AA113 zero
    ({}, "standard_worksheet", 32000, False),                               # no fact needs it
    ({"flags": {"age_65_plus_primary": True}}, "standard_worksheet", 31600, True),
    ({"flags": {"age_65_plus_primary": True}}, "standard_worksheet", 30000, False),
    ({"ca_exemptions": {"primary_can_be_claimed": True}}, "standard_worksheet", 4450, True),
    ({"ca_exemptions": {"primary_can_be_claimed": True}}, "standard_worksheet", 31000, False),
])
def test_the_deduction_election_follows_the_profile_facts(profile, method, amount, accepted):
    result = {"meta": {"filing_status": "MFJ"}, "form_1040": {"line_12_standard_deduction": amount}}
    problem = material_evidence._deduction_problem(result, {"filing_status": "MFJ", **profile}, _LAW_2025, method)
    assert (problem == "") is accepted, problem


def _captured(form, items, *, worksheet=b"reviewed worksheet"):
    result = json.dumps({"meta": {"tax_year": 2025, "filing_status": "MFJ"}, "form_1040": form}).encode()
    profile = json.dumps({"filing_status": "MFJ", "tax_year": 2025}).encode()
    law = json.dumps(_LAW_2025).encode()
    digests = {"result.json": receipt._sha256(result), "taxpayer_profile.json": receipt._sha256(profile),
               "tax_law_2025.json": receipt._sha256(law), "reviews/worksheet.json": receipt._sha256(worksheet)}
    built = {key: dict(item, inputs={name: digests[name] for name in item.pop("names")})
             for key, item in items.items()}
    evidence = json.dumps({"version": 1, "items": built}).encode()
    return {"result.json": result, "taxpayer_profile.json": profile, "tax_law_2025.json": law,
            material_evidence.FILENAME: evidence}


_ALL = ["result.json", "taxpayer_profile.json", "tax_law_2025.json"]


def _deduction(**extra):
    return {"input.deduction_election": {"label": "Form 1040 line 12 deduction election", "outcome": "reviewed",
                                         "deduction_method": "standard", "names": list(_ALL), **extra}}


def test_outcome_checks_that_fixtures_cannot_reach():
    form = {"line_12_standard_deduction": 30000, "line_31_schedule3_part2": 500}
    payments = {"label": "Form 1040 and Form 2210 payment worksheets", "outcome": "not_applicable",
                "names": [*_ALL, "reviews/worksheet.json"]}
    issues = material_evidence.filing_input_issues(_captured(form, {**_deduction(), "input.payments": payments}))
    assert any("still reports an applicable amount" in issue for issue in issues), issues
    payments.update(outcome="reviewed", names=list(_ALL))
    issues = material_evidence.filing_input_issues(_captured(form, {**_deduction(), "input.payments": dict(payments)}))
    assert any("Attach the reviewed worksheet" in issue for issue in issues), issues
    stale = _deduction(names=["result.json", "taxpayer_profile.json"])
    issues = material_evidence.filing_input_issues(_captured({"line_12_standard_deduction": 30000}, stale))
    assert any("needs fresh review" in issue for issue in issues), issues
    assert material_evidence.filing_input_issues(_captured({"line_12_standard_deduction": 30000}, _deduction())) == []


def test_a_separate_return_whose_spouse_itemizes_cannot_take_the_standard_deduction():
    result = {"meta": {"filing_status": "MFS"}, "form_1040": {"line_12_standard_deduction": 15000},
              "schedule_a": {"inputs": {"mfs_spouse_itemizes": True}}}
    law = {"federal": {"filing_statuses": {"mfs": {"standard_deduction": 15000}}}}
    assert "spouse itemizes" in material_evidence._deduction_problem(result, {"filing_status": "MFS"}, law, "standard")


def test_railroad_tax_counts_only_on_an_active_w2():
    primary = [_ss_w2("a", "Alpha", "primary", 100000, 6200, employer_ein="12-0000001"),
               _ss_w2("b", "Beta", "primary", 100000, 6200, employer_ein="12-0000002")]
    stray = tie_out.LedgerRecord("int", "documents/int.pdf", "1099-INT", "Bank", "primary",
                                 (("taxable_interest", 10), ("rrta_tier1_tax_withheld", 0.01)), None)
    # Checked exactly, not only bounded: the stray amount is no railroad W-2.
    assert _line_11([*primary, stray], 1000)
    assert _line_11([*primary, stray], 5000)
    assert not _line_11([*primary, stray], 1481.80)
