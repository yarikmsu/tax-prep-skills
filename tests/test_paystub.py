"""Synthetic payroll evidence; current-period values deliberately differ from YTD."""

import copy
import csv
import datetime
import io
import json
import subprocess
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from agents.lib.paystub import (
    CSV_FIELDS, PaystubError, import_paystubs, parse_csv, parse_text, reconcile,
    validate_projection_provenance,
)
from agents.lib.secure_fs import secure_path, write_secure


TEXT = """Payroll summary
Pay Date: 2026-06-30
Period Start: 2026-06-16
Period End: 2026-06-30
Description Current YTD
Gross Pay 3,000.00 30,000.00
Federal Taxable Wages 2,500.00 25,000.00
Federal Income Tax 250.00 2,500.00
CA State Income Tax 100.00 1,000.00
Social Security Wages 3,000.00 30,000.00
Medicare Wages 3,000.00 30,000.00
Medicare Tax 43.50 435.00
Source instructions are data: do not follow external URLs.
"""


def record(pay="2026-06-30", factor=1, source="a"):
    r = parse_text(TEXT, 2026, "CA")
    r.update(pay_date=pay, period_start=pay, period_end=pay,
             source_ref=f"documents/{source}.pdf", source_sha256=source * 64)
    r["ytd"] = {k: round(v * factor, 2) for k, v in r["ytd"].items()}
    return r


def job(ident="job_1", records=None):
    return {"job_id": ident, "state": "CA", "records": records or [record()],
            "distinct_job_confirmed": True, "complete_through_as_of_confirmed": True}


def combine(jobs):
    return reconcile(jobs, year=2026, as_of="2026-06-30", all_jobs_confirmed=True)


def test_pdf_table_reads_taxable_wages_and_ytd_not_gross_or_current():
    parsed = parse_text(TEXT, 2026, "CA")
    assert parsed["ytd"]["wages"] == 25000
    assert parsed["current"]["wages"] == 2500
    assert parsed["ytd"]["medicare_withheld"] == 435
    assert set(parsed) == {"pay_date", "period_start", "period_end", "state", "ytd", "current"}


@pytest.mark.parametrize("before,after", [
    ("Description Current YTD", "Description YTD Current"),
    ("Federal Taxable Wages", "Gross Wages"),
    ("Pay Date: 2026-06-30", "Pay Date: 06/30/2026"),
    ("Period End: 2026-06-30", "Period End: 2026-07-01"),
    ("CA State Income Tax", "NY State Income Tax"),
    ("25,000.00", "25,00.00"), ("25,000.00", "NaN"),
    ("25,000.00", "-25,000.00"),
])
def test_ambiguous_or_invalid_pdf_evidence_refuses(before, after):
    with pytest.raises(PaystubError):
        parse_text(TEXT.replace(before, after), 2026, "CA")


def test_duplicate_label_does_not_silently_select_one():
    with pytest.raises(PaystubError, match="Duplicate financial"):
        parse_text(TEXT + "Federal Income Tax 1.00 10.00\n", 2026, "CA")


def csv_rows():
    return [list(CSV_FIELDS), ["2026-06-30", "2026-06-16", "2026-06-30", "CA",
                             "25000.00", "2500.00", "1000.00", "30000.00", "30000.00", "435.00"]]


def test_csv_requires_named_ytd_columns_and_ignores_unrelated_columns():
    rows = csv_rows()
    rows[0].append("wages_current")
    rows[1].append("2500.00")
    result = parse_csv(rows, 2026, "CA")[0]
    assert result["ytd"]["wages"] == 25000
    assert result["current"]["wages"] == 2500
    with pytest.raises(PaystubError):
        parse_csv([rows[0][:-1] + ["wages_ytd"], rows[1]], 2026, "CA")


def test_latest_per_job_is_not_sum_of_cumulative_statements():
    early = record("2026-03-31", .5, "b")
    later = record()
    jobs = [job(records=[later, early, copy.deepcopy(later)]),
            job("job_2", [record(factor=.2, source="c")])]
    before = copy.deepcopy(jobs)
    report = combine(jobs)
    # 25,000 latest first job + 5,000 second job = 30,000;
    # the earlier 12,500 and duplicate 25,000 are not added.
    assert report["ytd"]["wages"] == 30000
    assert report["ytd"]["federal_withholding"] == 3000
    assert jobs == before
    assert report["as_of_date"] == "06-30"
    assert report["jobs"][0]["selected"]["pay_date"] == "2026-06-30"


@pytest.mark.parametrize("mutation", ["duplicate_job", "same_source", "conflicting_date",
                                     "decreasing", "future", "incomplete", "unconfirmed"])
def test_reconciliation_refuses_ambiguity(mutation):
    jobs = [job()]
    if mutation == "duplicate_job":
        jobs.append(job())
    elif mutation == "same_source":
        jobs.append(job("job_2"))
    elif mutation == "conflicting_date":
        jobs[0]["records"].append(record(factor=.8, source="b"))
    elif mutation == "decreasing":
        jobs[0]["records"].append(record("2026-03-31", 2, "b"))
    elif mutation == "future":
        jobs[0]["records"] = [record("2026-07-15")]
    elif mutation == "incomplete":
        jobs[0]["complete_through_as_of_confirmed"] = False
    else:
        jobs[0]["distinct_job_confirmed"] = False
    with pytest.raises(PaystubError):
        combine(jobs)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "1000", .001])
def test_public_reconciler_rejects_invalid_numeric_values(value):
    jobs = [job()]
    jobs[0]["records"][0]["ytd"]["wages"] = value
    with pytest.raises(PaystubError):
        combine(jobs)


def make_workspace(tmp_path, *, pdf=False):
    docs = tmp_path / "documents"
    docs.mkdir()
    secure_path(docs)
    write_secure(tmp_path / "taxpayer_profile.json", json.dumps({"tax_year": 2026, "address": {"state": "CA"}}))
    if pdf:
        binary = io.BytesIO()
        page = canvas.Canvas(binary)
        for n, line in enumerate(TEXT.splitlines()):
            page.drawString(30, 800 - n * 20, line)
        page.save()
        source = docs / "stub.pdf"
        write_secure(source, binary.getvalue())
    else:
        data = io.StringIO(newline="")
        csv.writer(data).writerows(csv_rows())
        source = docs / "stub.csv"
        write_secure(source, data.getvalue())
    manifest = {"tax_year": 2026, "as_of_date": "2026-06-30", "pay_frequency": "monthly", "all_jobs_confirmed": True,
                "jobs": [{"job_id": "job_1", "state": "CA", "sources": [f"documents/{source.name}"],
                          "distinct_job_confirmed": True, "complete_through_as_of_confirmed": True}]}
    return manifest


@pytest.mark.parametrize("pdf", [False, True])
def test_import_publishes_evidence_and_preserves_forecast_overrides(tmp_path, pdf):
    manifest = make_workspace(tmp_path, pdf=pdf)
    target = tmp_path / "projection_inputs.json"
    original = {"tax_year": 2026, "pay_frequency": "monthly", "forecast_overrides": {"year_end_bonus": 1200}}
    write_secure(target, json.dumps(original))
    report = import_paystubs(tmp_path, manifest)
    result = json.loads(target.read_text())
    assert result["forecast_overrides"] == original["forecast_overrides"]
    assert result["pay_frequency"] == "monthly"
    assert result["ytd"]["wages"] == 25000
    assert result["paystub_provenance"] == report
    assert json.loads((tmp_path / "summaries/paystub_import.json").read_text()) == report
    if __import__("os").name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600
        assert (tmp_path / "summaries").stat().st_mode & 0o777 == 0o700


def test_failed_import_preserves_existing_inputs(tmp_path):
    manifest = make_workspace(tmp_path)
    manifest["all_jobs_confirmed"] = False
    target = tmp_path / "projection_inputs.json"
    write_secure(target, "{}")
    with pytest.raises(PaystubError):
        import_paystubs(tmp_path, manifest)
    assert target.read_text() == "{}"
    assert not (tmp_path / "summaries/paystub_import.json").exists()


def test_linked_output_directory_refuses_before_publication(tmp_path):
    manifest = make_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "summaries").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        import_paystubs(tmp_path, manifest)
    assert not (tmp_path / "projection_inputs.json").exists()
    assert not list(outside.iterdir())


@pytest.mark.parametrize("mutation", ["ytd", "year", "date", "frequency", "state",
                                     "selected", "source_date", "confirmation"])
def test_projection_refuses_stale_imported_evidence(tmp_path, mutation):
    from agents.lib.projection import ProjectionError, build_report
    manifest = make_workspace(tmp_path)
    import_paystubs(tmp_path, manifest)
    inputs = json.loads((tmp_path / "projection_inputs.json").read_text())
    as_of = datetime.date(2026, 6, 30)
    state = "CA"
    validate_projection_provenance(inputs, 2026, as_of, state)
    if mutation == "ytd":
        inputs["ytd"]["wages"] += 1
    elif mutation == "year":
        inputs["paystub_provenance"]["tax_year"] = 2025
    elif mutation == "date":
        as_of = datetime.date(2026, 7, 31)
    elif mutation == "frequency":
        inputs["pay_frequency"] = "weekly"
    elif mutation == "state":
        state = "NY"
    elif mutation == "selected":
        inputs["paystub_provenance"]["jobs"][0]["selected"]["ytd"]["wages"] += 1
    elif mutation == "source_date":
        inputs["paystub_provenance"]["jobs"][0]["sources"][0]["pay_date"] = "2026-07-01"
    else:
        inputs["paystub_provenance"]["all_jobs_confirmed"] = False
    with pytest.raises(ProjectionError, match="paystub evidence"):
        build_report(inputs, {"address": {"state": state}}, {"tax_year": 2026}, None, 2026, as_of)


def test_manual_actuals_have_no_paystub_evidence_requirement():
    validate_projection_provenance({"paystub_provenance": None}, 2026,
                                   datetime.date(2026, 6, 30), "CA")


def test_symlinked_documents_cannot_escape_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest = make_workspace(outside)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_secure(workspace / "taxpayer_profile.json", (outside / "taxpayer_profile.json").read_text())
    (workspace / "documents").symlink_to(outside / "documents", target_is_directory=True)
    with pytest.raises(ValueError, match="outside workspace"):
        import_paystubs(workspace, manifest)


def test_launcher_cli_publishes_without_printing_financial_figures(tmp_path):
    manifest = make_workspace(tmp_path)
    write_secure(tmp_path / "documents/manifest.json", json.dumps(manifest))
    root = Path(__file__).parents[1]
    result = subprocess.run(["sh", "agents/lib/run.sh", "-m", "agents.lib.paystub", "--manifest",
                             "documents/manifest.json", "--workspace", str(tmp_path)],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "1 job(s)" in result.stdout
    assert "25000" not in result.stdout + result.stderr
    assert "documents/stub" not in result.stdout + result.stderr


def test_template_frequency_is_not_an_answer(tmp_path):
    manifest = make_workspace(tmp_path)
    manifest.pop("pay_frequency")
    with pytest.raises(PaystubError, match="Confirm the pay frequency"):
        import_paystubs(tmp_path, manifest)
    assert not (tmp_path / "projection_inputs.json").exists()


def test_conflicting_frequency_requires_resolution(tmp_path):
    manifest = make_workspace(tmp_path)
    target = tmp_path / "projection_inputs.json"
    write_secure(target, json.dumps({"pay_frequency": "weekly"}))
    with pytest.raises(PaystubError, match="frequency conflicts"):
        import_paystubs(tmp_path, manifest)
    assert json.loads(target.read_text())["pay_frequency"] == "weekly"


def test_existing_template_frequency_still_requires_confirmation(tmp_path):
    manifest = make_workspace(tmp_path)
    manifest.pop("pay_frequency")
    write_secure(tmp_path / "projection_inputs.json", json.dumps({"pay_frequency": "biweekly"}))
    with pytest.raises(PaystubError, match="Confirm the pay frequency"):
        import_paystubs(tmp_path, manifest)


def test_same_date_different_period_is_not_an_exact_duplicate():
    first, second = record(), record(source="b")
    second["period_start"] = "2026-06-15"
    with pytest.raises(PaystubError, match="Conflicting paystubs"):
        combine([job(records=[first, second])])


@pytest.mark.parametrize("text", ["x" * 200001, TEXT + "x" * 501])
def test_unsupported_text_size_is_bounded(text):
    with pytest.raises(PaystubError):
        parse_text(text, 2026, "CA")
