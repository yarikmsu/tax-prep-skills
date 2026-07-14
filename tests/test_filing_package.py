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
        "meta": {"limitations": ["State: California computed in depth."]},
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


def test_cli_json_and_markdown(tmp_path):
    result = _result(schedule_d={"line_16": 5000})
    p = tmp_path / "result.json"
    p.write_text(json.dumps(result))
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.filing_package",
         "--result", str(p), "--workspace", str(tmp_path), "--json"],
        capture_output=True, text=True, cwd=FIX_REPO)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["assembly_order"][0]["key"] == "form_1040"
    assert any("Sign and date" in x for x in data["attachment_checklist"])
    out2 = subprocess.run(
        [sys.executable, "-m", "agents.lib.filing_package",
         "--result", str(p), "--workspace", str(tmp_path)],
        capture_output=True, text=True, cwd=FIX_REPO)
    assert "Filing package" in out2.stdout


def test_cli_path_traversal_rejected(tmp_path):
    outside = tmp_path.parent / "outside.json"
    outside.write_text("{}")
    out = subprocess.run(
        [sys.executable, "-m", "agents.lib.filing_package",
         "--result", str(outside), "--workspace", str(tmp_path)],
        capture_output=True, text=True, cwd=FIX_REPO)
    assert out.returncode == 1
