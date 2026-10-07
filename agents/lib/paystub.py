#!/usr/bin/env python3
"""Local, deterministic paystub intake into the projection's stable YTD block."""

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agents.lib.capabilities import _USPS_STATES  # noqa: E402
from agents.lib.parse_utils import (  # noqa: E402
    _validate_file_path, extract_csv, extract_pdf_text,
)
from agents.lib.secure_fs import atomic_paths, redact, secure_path  # noqa: E402
from agents.lib.projection import ProjectionError, validate_pay_frequency  # noqa: E402
from agents.lib.workspace import (  # noqa: E402
    WorkspaceError, add_workspace_argument, resolve_workspace, workspace_lock,
)

FIELDS = ("wages", "federal_withholding", "state_withholding", "ss_wages",
          "medicare_wages", "medicare_withheld")
LABELS = {"Federal Taxable Wages": "wages", "Federal Income Tax": "federal_withholding",
          "Social Security Wages": "ss_wages", "Medicare Wages": "medicare_wages",
          "Medicare Tax": "medicare_withheld"}
CSV_FIELDS = ("pay_date", "period_start", "period_end", "state") + tuple(f"{k}_ytd" for k in FIELDS)
_MONEY = r"-?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?"


class PaystubError(ValueError):
    """Evidence is missing, ambiguous, or inconsistent; never publish partial YTD."""


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise PaystubError("Paystub dates must explicitly use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PaystubError("Paystub date is invalid") from None


def _money(value, *, current=False):
    if not isinstance(value, str) or not re.fullmatch(_MONEY, value.strip()):
        raise PaystubError("Paystub amounts must be explicit USD amounts; resolve ambiguous labels")
    number = float(Decimal(value.strip().replace("$", "").replace(",", "")))
    if not math.isfinite(number) or (number < 0 and not current):
        raise PaystubError("Paystub YTD figures must be finite and nonnegative")
    return round(number, 2)


def _validate_record(record, year):
    if not isinstance(record, dict):
        raise PaystubError("Paystub record must be an object")
    if isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2099:
        raise PaystubError("Paystub year must be a covered integer year")
    allowed = {"pay_date", "period_start", "period_end", "state", "ytd", "current",
               "source_ref", "source_sha256"}
    required = {"pay_date", "period_start", "period_end", "state", "ytd", "current"}
    if set(record) - allowed or not required <= set(record):
        raise PaystubError("Paystub records may contain only documented evidence fields")
    pay, start, end = (_date(record[k]) for k in ("pay_date", "period_start", "period_end"))
    if not 2000 <= year <= 2099 or pay.year != year or start > end or end > pay:
        raise PaystubError("Paystub pay year or pay-period evidence is inconsistent")
    if not isinstance(record.get("ytd"), dict) or set(record["ytd"]) != set(FIELDS):
        raise PaystubError("Paystub is missing explicit YTD wage/withholding evidence")
    if not isinstance(record.get("state"), str) or record["state"] not in _USPS_STATES:
        raise PaystubError("Paystub state must be a real USPS code")
    for key in FIELDS:
        number = record["ytd"][key]
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or number < 0 or round(number, 2) != number):
            raise PaystubError("Paystub YTD figures must be finite nonnegative cents")
    current = record.get("current")
    if not isinstance(current, dict) or set(current) - set(FIELDS):
        raise PaystubError("Paystub current-period fields are malformed")
    for number in current.values():
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or round(number, 2) != number):
            raise PaystubError("Paystub current-period figures must be finite cents")
    return record


def parse_text(text, year, state):
    """Supported PDF layout: ISO dates and a Description / Current / YTD table.

    Gross pay is never a federal-taxable-wages substitute. Only anchored
    financial labels are read; all other source text is ignored as data.
    """
    if not isinstance(text, str) or len(text) > 200000:
        raise PaystubError("Paystub text exceeds the supported single-statement format")
    raw_lines = text.splitlines()
    if any(len(line) > 500 for line in raw_lines):
        raise PaystubError("Paystub line exceeds the supported table format")
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_lines]
    if sum(line in {"Description Current YTD", "Description Current Year to Date"}
           for line in lines) != 1:
        raise PaystubError("Unsupported paystub columns; require explicit Current then YTD labels")
    record = {"ytd": {}, "current": {}, "state": state}
    for line in lines:
        metadata = re.fullmatch(r"(Pay Date|Period Start|Period End): (\d{4}-\d{2}-\d{2})", line)
        if metadata:
            key = {"Pay Date": "pay_date", "Period Start": "period_start", "Period End": "period_end"}[metadata[1]]
            if key in record:
                raise PaystubError("Duplicate paystub date label; resolve the source")
            record[key] = metadata[2]
            continue
        row = re.fullmatch(rf"(.+?) ({_MONEY}) ({_MONEY})", line)
        if not row:
            continue
        label = row[1]
        if label.endswith(" State Income Tax"):
            if label != f"{state} State Income Tax":
                raise PaystubError("Paystub includes another state's withholding; resolve state allocation")
            field = "state_withholding"
        else:
            field = LABELS.get(label)
        if field is None:
            continue
        if field in record["ytd"]:
            raise PaystubError("Duplicate financial label; resolve ambiguous paystub rows")
        record["current"][field] = _money(row[2], current=True)
        record["ytd"][field] = _money(row[3])
    if not all(key in record for key in ("pay_date", "period_start", "period_end")):
        raise PaystubError("Paystub lacks pay-date/pay-period evidence")
    return _validate_record(record, year)


def parse_csv(rows, year, state):
    """Supported payroll CSV: named ISO metadata and six explicit *_ytd columns."""
    if not rows or len(rows[0]) != len(set(rows[0])) or not set(CSV_FIELDS) <= set(rows[0]):
        raise PaystubError("Unsupported payroll CSV; use the documented explicit YTD columns")
    out = []
    for cells in rows[1:]:
        if not cells or all(not cell for cell in cells):
            continue
        if len(cells) != len(rows[0]):
            raise PaystubError("Payroll CSV row does not match its columns")
        row = dict(zip(rows[0], cells))
        if row["state"] != state:
            raise PaystubError("Payroll CSV state differs from the confirmed projection state")
        record = {k: row[k] for k in ("pay_date", "period_start", "period_end", "state")}
        record["ytd"] = {k: _money(row[f"{k}_ytd"]) for k in FIELDS}
        record["current"] = {k: _money(row[f"{k}_current"], current=True)
                             for k in FIELDS if f"{k}_current" in row}
        out.append(_validate_record(record, year))
    if not out:
        raise PaystubError("Payroll CSV has no paystub records")
    return out


def reconcile(jobs, *, year, as_of, all_jobs_confirmed):
    """Use the latest cumulative record per confirmed distinct job, then sum jobs."""
    if all_jobs_confirmed is not True or not jobs:
        raise PaystubError("Confirm the complete list of distinct jobs before populating YTD")
    as_of = _date(as_of)
    if as_of.year != year:
        raise PaystubError("Paystub as-of date differs from the projection year")
    ids, sources, evidence, states = set(), {}, [], set()
    total = {k: 0.0 for k in FIELDS}
    for job in jobs:
        if not isinstance(job, dict):
            raise PaystubError("Paystub job must be an object")
        ident = job.get("job_id")
        if (not isinstance(ident, str) or not re.fullmatch(r"job_[a-zA-Z0-9_]+", ident)
                or ident in ids or job.get("distinct_job_confirmed") is not True
                or job.get("complete_through_as_of_confirmed") is not True):
            raise PaystubError("Resolve job identity, duplication and completeness through the as-of date")
        ids.add(ident)
        records = job.get("records")
        if not isinstance(records, list) or not records:
            raise PaystubError("Every confirmed job needs paystub evidence")
        by_date = {}
        for record in records:
            _validate_record(record, year)
            if record["state"] != job.get("state"):
                raise PaystubError("Paystub job state differs from its records")
            states.add(record["state"])
            if len(states) != 1:
                raise PaystubError("Different withholding jurisdictions need an explicit allocation")
            pay = _date(record["pay_date"])
            if pay > as_of:
                raise PaystubError("Paystub postdates the requested as-of date")
            digest = record["source_sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise PaystubError("Paystub evidence needs its source digest")
            source_ref = record.get("source_ref")
            if not isinstance(source_ref, str) or not source_ref.strip() or redact(source_ref) != source_ref:
                raise PaystubError("Paystub evidence requires a masked source reference")
            if digest in sources and sources[digest] != ident:
                raise PaystubError("One source was assigned to multiple jobs; refuse duplicate income")
            sources[digest] = ident
            if pay in by_date and any(by_date[pay][k] != record[k] for k in
                                      ("ytd", "current", "period_start", "period_end")):
                raise PaystubError("Conflicting paystubs share a pay date; resolve corrected/supplemental records")
            by_date[pay] = record
        ordered = [by_date[d] for d in sorted(by_date)]
        for earlier, later in zip(ordered, ordered[1:]):
            if any(later["ytd"][k] < earlier["ytd"][k] for k in FIELDS):
                raise PaystubError("Cumulative paystub figures decreased; explicitly resolve payroll corrections")
        latest = ordered[-1]
        for key in FIELDS:
            total[key] = round(total[key] + latest["ytd"][key], 2)
            if not math.isfinite(total[key]):
                raise PaystubError("Combined paystub figure is not finite")
        evidence.append({"job_id": ident, "state": job["state"],
                         "distinct_job_confirmed": True, "selected": latest,
                         "sources": [{"source_ref": r["source_ref"], "source_sha256": r["source_sha256"],
                                      "pay_date": r["pay_date"], "period_start": r["period_start"],
                                      "period_end": r["period_end"]} for r in records],
                         "complete_through_as_of_confirmed": True})
    return {"tax_year": year, "as_of_date": as_of.strftime("%m-%d"),
            "as_of_full_date": as_of.isoformat(), "ytd": total, "jobs": evidence,
            "all_jobs_confirmed": True}


def validate_projection_provenance(inputs, year, as_of, state):
    """Bind imported actuals to the evidence before any annualization."""
    evidence = inputs.get("paystub_provenance")
    if evidence is None:
        return  # Manual YTD remains the existing contract.
    message = "Imported paystub evidence no longer matches projection inputs; re-import or explicitly resolve manual YTD"
    try:
        if (not isinstance(evidence, dict) or evidence.get("tax_year") != year
                or evidence.get("as_of_full_date") != as_of.isoformat()
                or evidence.get("as_of_date") != inputs.get("as_of_date")
                or evidence.get("pay_frequency") != inputs.get("pay_frequency")):
            raise PaystubError(message)
        jobs = []
        for job in evidence["jobs"]:
            selected = job["selected"]
            sources = job["sources"]
            if (job["state"] != state or not sources
                    or max(_date(s["pay_date"]) for s in sources) != _date(selected["pay_date"])
                    or not any(all(s[k] == selected[k] for k in
                                   ("source_ref", "source_sha256", "pay_date", "period_start", "period_end"))
                               for s in sources)):
                raise PaystubError(message)
            jobs.append({**job, "records": [selected]})
        rebuilt = reconcile(jobs, year=year, as_of=evidence["as_of_full_date"],
                            all_jobs_confirmed=evidence["all_jobs_confirmed"])
        if rebuilt["ytd"] != evidence["ytd"] or rebuilt["ytd"] != inputs.get("ytd"):
            raise PaystubError(message)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PaystubError(message) from None


def import_paystubs(workspace, manifest):
    """Read only declared local sources; preserve forecasts; publish both files securely."""
    workspace = Path(workspace)
    year = manifest.get("tax_year")
    if isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2099:
        raise PaystubError("Paystub manifest needs an explicit tax year")
    profile_path = _validate_file_path(str(workspace / "taxpayer_profile.json"), {".json"}, str(workspace))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if (not isinstance(profile, dict) or isinstance(profile.get("tax_year"), bool)
            or not isinstance(profile.get("tax_year"), int) or profile["tax_year"] != year):
        raise PaystubError("Paystub manifest differs from the taxpayer profile's year")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list):
        raise PaystubError("Paystub manifest jobs must be a list")
    parsed = []
    state = (profile.get("address") or {}).get("state")
    if not isinstance(state, str) or state not in _USPS_STATES:
        raise PaystubError("Profile needs a confirmed projection state")
    for job in jobs:
        if not isinstance(job, dict) or job.get("state") != state:
            raise PaystubError("Every job must confirm the same projection state; resolve allocations")
        filenames = job.get("sources")
        if not isinstance(filenames, list) or not filenames or not all(isinstance(s, str) for s in filenames):
            raise PaystubError("Every job must declare its local source files")
        records = []
        for filename in filenames:
            path = _document_path(workspace, filename, {".pdf", ".csv"})
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.suffix.lower() == ".pdf":
                pages = extract_pdf_text(str(path), workspace=str(workspace))
                found = [parse_text("\n".join(pages.values()), year, state)]
            else:
                found = parse_csv(extract_csv(str(path), workspace=str(workspace)), year, state)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise PaystubError("Paystub source changed during extraction; retry from stable evidence")
            for record in found:
                record.update(source_ref=redact(str(path.relative_to(workspace))), source_sha256=digest)
                records.append(record)
        parsed.append({**job, "records": records})
    report = reconcile(parsed, year=year, as_of=manifest.get("as_of_date"),
                       all_jobs_confirmed=manifest.get("all_jobs_confirmed"))
    target = workspace / "projection_inputs.json"
    if target.exists():
        checked = _validate_file_path(str(target), {".json"}, str(workspace))
        inputs = json.loads(checked.read_text(encoding="utf-8"))
        if not isinstance(inputs, dict):
            raise PaystubError("Existing projection inputs must be an object")
        if inputs.get("tax_year") not in (None, year):
            raise PaystubError("Existing projection inputs belong to another year")
    else:
        template = Path(__file__).resolve().parents[1] / "templates/projection_inputs_template.json"
        inputs = json.loads(template.read_text(encoding="utf-8"))
    # A fresh template's biweekly default is not evidence of the chosen job's frequency.
    frequency = manifest.get("pay_frequency")
    try:
        frequency = validate_pay_frequency(frequency)
    except ProjectionError:
        raise PaystubError("Confirm the pay frequency for the job receiving the withholding adjustment") from None
    if target.exists() and manifest.get("pay_frequency") is not None and inputs.get("pay_frequency") != frequency:
        raise PaystubError("Paystub pay frequency conflicts with existing projection inputs; resolve it explicitly")
    report["pay_frequency"] = frequency
    inputs = copy.deepcopy(inputs)
    inputs.update(tax_year=year, as_of_date=report["as_of_date"], ytd=report["ytd"],
                  pay_frequency=frequency, paystub_provenance=report)
    summaries = workspace / "summaries"
    summaries.mkdir(exist_ok=True)
    secure_path(summaries)
    summary = summaries / "paystub_import.json"
    with atomic_paths(target, summary) as (projection_tmp, summary_tmp):
        projection_tmp.write_text(json.dumps(inputs, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        summary_tmp.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def _document_path(workspace, filename, suffixes):
    checked = _validate_file_path(str(workspace / filename), suffixes, str(workspace))
    if not checked.is_relative_to((workspace / "documents").resolve()):
        raise PaystubError("Paystub sources and manifest must stay under documents/")
    return checked


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="local paystub manifest under documents/")
    add_workspace_argument(parser)
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        path = _document_path(workspace, args.manifest, {".json"})
        with workspace_lock(workspace, "paystub import"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise PaystubError("Paystub manifest must be an object")
            report = import_paystubs(workspace, manifest)
        print(f"Paystub import complete: {len(report['jobs'])} job(s); projection inputs and evidence secured")
        return 0
    except (ValueError, OSError, RuntimeError, WorkspaceError, KeyError, TypeError) as exc:
        print(f"Paystub import BLOCKED: {redact(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
