"""Tests for agents/lib/filing_package.py — assembly order + checklist (F1)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import filing_package as FP  # noqa: E402


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


def _package(tmp_path, *flags):
    result = _result(schedule_d={"line_16": 5000})
    p = tmp_path / "result.json"
    p.write_text(json.dumps(result))
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
    (tmp_path / "parse_ledger.json").write_text("{}")
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
    for gate in receipt.REQUIRED_GATES:
        receipt.record(tmp_path, gate, receipt.PASS)
    out = _package(tmp_path)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("# Filing package") or "Filing package" in out.stdout
    assert "NOT VALIDATED" not in out.stdout


def test_a_package_needs_the_derived_lines_gate_too(tmp_path):
    """Step 12.6 is a REQUIRED gate. Before it was receipted an agent could
    skip it — or pass it and then edit line 24 — and nothing downstream could
    tell; the tie-out's income checks cannot see a hand-edited derived line,
    because six of its category sums now produce the lines they compare."""
    from agents.lib import receipt
    (tmp_path / "taxpayer_profile.json").write_text("{}")
    (tmp_path / "parse_ledger.json").write_text("{}")
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
    (tmp_path / "parse_ledger.json").write_text("{}")
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
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
    (tmp_path / "parse_ledger.json").write_text("{}")
    (tmp_path / "result.json").write_text(json.dumps(_result(schedule_d={"line_16": 5000})))
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
    (tmp_path / "parse_ledger.json").write_text("{}")
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
