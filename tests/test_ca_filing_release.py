"""Exercise the checked California resident filing path without a model."""

import json
import subprocess

import pytest

from agents.lib import parse_utils, receipt
from agents.lib.workspace import ABANDONED_NAME
from evals import ca_filing_2025 as release


# Independent worksheet results. The derivation and source forms are in
# evals/README.md. None of these expectations are computed by the toolkit.
FEDERAL = {
    "line_1a_wages": 138000, "line_7_capital_gain": 4500, "line_11_agi": 142500,
    "line_12_standard_deduction": 15750, "line_15_taxable_income": 126750,
    "line_16_tax": 22862, "line_24_total_tax": 22862, "line_25a_w2_withholding": 24000,
    "line_26_estimated_payments": 0, "line_33_total_payments": 24000,
    "line_35a_refund": 1138, "line_37_amount_owed": 0, "line_38_estimated_tax_penalty": 0,
}
CALIFORNIA = {
    "line_13": 142500, "line_17": 142500, "line_18": 5706, "line_19": 136794,
    "line_31": 9160.48, "line_32": 153, "line_61": 0, "line_64": 9007.48,
    "line_71": 9800, "line_78": 9800, "line_113": 0, "line_114": 0, "line_115": 792.52,
}


@pytest.fixture
def workspace(tmp_path):
    release.make_workspace(tmp_path)
    return tmp_path


def _package(workspace, *args, expected=0):
    return release.run(workspace, "filing_package", "--result", "result.json", *args, expected=expected)


def _assert_ready(workspace, forms=("form_1040", "schedule_d", "california_540")):
    verified = json.loads(release.run(workspace, "receipt", "--for-filing", "--json", subcommand="verify"))
    assert verified["valid"] and not verified["reasons"] and not verified["notes"]
    gates = release.read_json(workspace / receipt.RECEIPT_NAME)["gates"]
    assert {name: entry["outcome"] for name, entry in gates.items()} == dict.fromkeys(receipt.REQUIRED_GATES, "PASS")
    package = json.loads(_package(workspace, "--json"))
    assert package["validated"] is True and package["receipt"] == verified
    assert [row["key"] for row in package["assembly_order"]] == list(forms)
    # Each recovery ends in a fresh export of BOTH artifacts, not the package alone.
    assert _pdf_lines(workspace)
    return package


def _assert_lines(actual, expected):
    assert {key: actual[key] for key in expected} == expected


def _pdf_lines(workspace):
    release.run(workspace, "pdf_render", "--result", "result.json", "--out", "release.pdf")
    pages = parse_utils.extract_pdf_text(str(workspace / "release.pdf"), workspace=str(workspace))
    return set(line for text in pages.values() for line in text.splitlines())


def _assert_refused(workspace, reason):
    assert reason in _package(workspace, expected=2)
    assert reason in release.run(workspace, "pdf_render", "--result", "result.json", "--out", "denied.pdf", expected=2)
    assert not (workspace / "denied.pdf").exists()


def test_w2_and_brokerage_release(workspace):
    release.compute(workspace)
    result = release.read_json(workspace / "result.json")
    _assert_lines(result["form_1040"], FEDERAL)
    _assert_lines(result["california_540"], CALIFORNIA)
    _assert_lines(result["schedule_d"]["part_ii_long_term"]["line_8a_1099b_box_d"],
                  {"proceeds": 10000, "cost": 5500, "adjustment": 0, "gain": 4500})
    assert result["california_540"]["schedule_p"]["ca_tmt"] == 3482.57
    _assert_ready(workspace)
    lines = _pdf_lines(workspace)
    assert {"Line 16 — tax $22,862.00", "Line 35a — refund $1,138.00", "Line 31 $9,160.48",
            "Line 64 $9,007.48", "Line 115 $792.52"} <= lines


def test_two_w2s_and_posted_estimate_release(workspace):
    release.add_second_w2_and_estimate(workspace)
    release.compute(workspace)
    result = release.read_json(workspace / "result.json")
    _assert_lines(result["form_1040"], FEDERAL | {"line_26_estimated_payments": 600,
                  "line_33_total_payments": 24600, "line_35a_refund": 1738})
    _assert_lines(result["california_540"], CALIFORNIA)
    package = _assert_ready(workspace)
    assert any("line 26 uses reviewed federal estimates" in row for row in package["attachment_checklist"])
    lines = _pdf_lines(workspace)
    assert {"Line 26 — estimated payments $600.00", "Line 35a — refund $1,738.00", "Line 115 $792.52"} <= lines


def test_overlapping_business_receipts_release(workspace):
    release.add_overlapping_business(workspace)
    release.compute(workspace)
    result = release.read_json(workspace / "result.json")
    _assert_lines(result["schedule_c"], {"line_1_gross_receipts": 12000, "line_31_net": 10000})
    _assert_lines(result["schedule_se"], {"schedule_1_line_15_deductible_se_tax": 706.48,
                                         "schedule_2_line_4_se_tax": 1412.96})
    _assert_lines(result["form_1040"], FEDERAL | {"line_8_schedule1_other": 10000, "line_10_adjustments": 706.48,
                  "line_11_agi": 151793.52, "line_13a_qbi_deduction": 1858.70, "line_15_taxable_income": 134184.82,
                  "line_16_tax": 24646.36, "line_23_other_taxes": 1412.96, "line_24_total_tax": 26059.32,
                  "line_25a_w2_withholding": 27500, "line_33_total_payments": 27500, "line_35a_refund": 1440.68})
    _assert_lines(result["california_540"], CALIFORNIA | {"line_13": 151793.52, "line_17": 151793.52,
                  "line_19": 146087.52, "line_31": 10024.78, "line_64": 9871.78, "line_71": 11000,
                  "line_78": 11000, "line_115": 1128.22})
    package = _assert_ready(workspace, ("form_1040", "schedule_1_other_income", "schedule_se", "schedule_c",
                                       "schedule_d", "schedule_se.by_owner.primary", "form_8995_qbi", "california_540"))
    assert any("overlapping business forms were counted once" in row for row in package["receipt"]["disclosures"])
    lines = _pdf_lines(workspace)
    assert {"Line 23 — other taxes $1,412.96", "Line 24 — total tax $26,059.32",
            "Line 35a — refund $1,440.68", "Line 115 $1,128.22"} <= lines


def test_same_path_correction_requires_new_intake(workspace):
    release.compute(workspace)
    _assert_ready(workspace)
    release.write_w2(workspace / "documents" / "w2-one.pdf", 138000, 24200, 9800)
    _assert_refused(workspace, "changed since")
    report = json.loads(release.run(workspace, "tie_out", "--json", expected=2))
    assert report["changed_sources"] == ["Document #2 in the parse ledger"]
    _assert_refused(workspace, "changed since intake")
    release.compute(workspace)
    _assert_ready(workspace)
    result = release.read_json(workspace / "result.json")
    _assert_lines(result["form_1040"], FEDERAL | {"line_25a_w2_withholding": 24200,
                  "line_33_total_payments": 24200, "line_35a_refund": 1338})
    _assert_lines(result["california_540"], CALIFORNIA)
    assert "Line 35a — refund $1,338.00" in _pdf_lines(workspace)


def test_unread_w2_box_blocks_final_until_recovered(workspace):
    release.compute(workspace, unread_box_2=True)
    note = "Open material item: W-2 #1 Box 2"
    _assert_refused(workspace, note)
    assert note in _package(workspace, "--draft")
    release.compute(workspace)
    _assert_ready(workspace)
    _assert_lines(release.read_json(workspace / "result.json")["form_1040"], FEDERAL)


def test_open_deduction_blocks_even_when_arithmetic_passes(workspace):
    release.compute(workspace)
    release.run(workspace, "material_evidence", "--id", "input.deduction_election", "--label", "Basic standard deduction",
                "--outcome", "open")
    release.validate(workspace)
    note = "Open material item: Form 1040 line 12 deduction election"
    _assert_refused(workspace, note)
    assert note in _package(workspace, "--draft")
    release.record_deduction(workspace)
    release.validate(workspace)
    _assert_ready(workspace)


def test_unverified_applicable_law_blocks_even_after_gates_are_refreshed(workspace):
    release.compute(workspace)
    path = workspace / release.LAW
    law = release.read_json(path)
    figure = "federal.capital_gains_rates.fifteen_pct_limit.single"
    del law["_provenance"][figure]
    release.write_json(path, law)
    release.record_deduction(workspace)
    release.validate(workspace)
    _assert_refused(workspace, f"{figure}: record covered-year live source provenance")
    release.seed_law(workspace)
    release.record_deduction(workspace)
    release.validate(workspace)
    _assert_ready(workspace)


def test_interrupted_compute_blocks_final_until_validated_retry(workspace):
    release.compute(workspace)
    _assert_ready(workspace)
    # Simulate process loss after publishing one intermediate result, before
    # the context manager or the evidence gates have a chance to run.
    interrupted = subprocess.run([
        "sh", "agents/lib/run.sh", "-c",
        "import os, sys\nfrom pathlib import Path\n"
        "from agents.lib.workspace import workspace_lock\n"
        "from evals.ca_filing_2025 import read_json, write_json\n"
        "root = Path(sys.argv[1])\n"
        "with workspace_lock(root, 'release-compute'):\n"
        "    path = root / 'result.json'\n"
        "    data = read_json(path)\n"
        "    data['form_1040']['line_35a_refund'] = 0\n"
        "    write_json(path, data)\n"
        "    os._exit(7)\n", str(workspace),
    ], cwd=release.ROOT, capture_output=True, text=True, timeout=60)
    assert interrupted.returncode == 7, interrupted.stderr
    _assert_refused(workspace, "result.json changed")
    release.run(workspace, "parse_records", "--check")
    assert (workspace / ABANDONED_NAME).is_file()
    _assert_refused(workspace, "release-compute")
    release.compute(workspace)
    assert not (workspace / ABANDONED_NAME).exists()
    _assert_ready(workspace)
    _assert_lines(release.read_json(workspace / "result.json")["form_1040"], FEDERAL)
