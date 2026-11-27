"""Offline fixtures for the checked TY2025 California filing release tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from agents.lib import capabilities, parse_records, parse_utils
from agents.lib.secure_fs import secure_path, write_secure
from agents.lib.tax_computations import compute_qbi_deduction
from agents.lib.tax_law_fetcher import create_empty_tax_law, create_state_template, save_tax_law, set_figure
from evals.fixture import _document

ROOT = Path(__file__).resolve().parents[1]
YEAR = 2025
LAW = f"tax_law_{YEAR}.json"
BUSINESS_REMITTANCE = "business-remittance.csv"
IRS = "https://www.irs.gov/pub/irs-drop/rp-24-40.pdf"
FTB = "https://www.ftb.ca.gov/forms/2025/2025-540-booklet.html"
SCHEDULE_X = "https://www.ftb.ca.gov/forms/2025/2025-540-tax-rate-schedules.pdf"
SCHEDULE_P = "https://www.ftb.ca.gov/forms/2025/2025-540-p-instructions.html"
FTB_5805 = "https://www.ftb.ca.gov/forms/2025/2025-5805-instructions.html"
FORM_540_ES = "https://www.ftb.ca.gov/forms/2025/2025-540-es-instructions.html"
STATUSES = ("single", "mfj", "mfs", "hoh", "qss")
W2_FIELDS = {
    "Box 1 Wages": "wages",
    "Box 2 Federal income tax withheld": "federal_withholding_w2",
    "Box 3 Social Security wages": "ss_wages",
    "Box 5 Medicare wages": "medicare_wages",
    "Box 17 CA income tax withheld": "state_withholding_ca",
}


def _brackets(limits: tuple, rates: tuple) -> list[dict]:
    return [{"min": start, "max": end, "rate": rate}
            for start, end, rate in zip((0, *limits), (*limits, None), rates, strict=True)]


def seed_law(workspace: Path) -> None:
    """Replay a fixed, cited slice of TY2025 law through the cache writer."""
    seed = create_empty_tax_law(YEAR)
    seed["state"] = create_state_template("CA", YEAR)
    save_tax_law(YEAR, seed, str(workspace))
    figures = [
        ("federal.filing_statuses.single.brackets", _brackets(
            (11925, 48475, 103350, 197300, 250525, 626350), (.10, .12, .22, .24, .32, .35, .37)), IRS),
        ("federal.filing_statuses.single.standard_deduction", 15750, "https://www.irs.gov/irb/2025-45_IRB"),
        ("federal.capital_gains_rates.zero_pct_limit.single", 48350, IRS),
        ("federal.capital_gains_rates.fifteen_pct_limit.single", 533400, IRS),
        ("state.type", "graduated_income", SCHEDULE_X),
        ("state.standard_deduction.single", 5706, FTB),
        ("state.brackets", _brackets((11079, 26264, 41452, 57542, 72724, 371479, 445771, 742953),
                                    (.01, .02, .04, .06, .08, .093, .103, .113, .123)), SCHEDULE_X),
        ("state.personal_exemption.amount", 153, "https://www.ftb.ca.gov/forms/2025/2025-540.pdf"),
        ("state.dependent_exemption.amount", 475, "https://www.ftb.ca.gov/forms/2025/2025-540.pdf"),
        ("state.exemption_phaseout", {
            "threshold": dict(zip(STATUSES, (252203, 504411, 252203, 378310, 504411), strict=True)),
            "band": dict.fromkeys(STATUSES, 2500) | {"mfs": 1250}, "reduction_per_step": 6,
        }, FTB),
        ("state.amt_screening", dict(zip(STATUSES, (92749, 123667, 61830, 92749, 123667), strict=True)), FTB),
        ("state.amt", {"exemption": {"single": 92749, "mfj": 123667, "mfs": 61830, "hoh": 92749},
                       "phaseout_start": {"single": 347808, "mfj": 463745, "mfs": 231868, "hoh": 347808},
                       "tmt_rate": .07, "phaseout_rate": .25}, SCHEDULE_P),
        ("state.underpayment_screen", {"minimum_balance": dict.fromkeys(STATUSES, 500) | {"mfs": 250},
                                       "tax_fraction": .1}, FTB),
        ("state.estimated_tax_safe_harbor", {
            "current_year_share": .9, "prior_year_share": 1, "prior_year_share_high_income": 1.1,
            "prior_agi_threshold": dict.fromkeys(STATUSES, 150000) | {"mfs": 75000},
            "current_agi_override": dict.fromkeys(STATUSES, 1000000) | {"mfs": 500000},
            "installment_shares": [.3, .4, 0, .3],
            "due_dates": ["2025-04-15", "2025-06-16", "2025-09-15", "2026-01-15"],
            "penalty_end": "2026-04-15", "early_filing_deadline": "2026-01-31",
        }, FORM_540_ES),
        ("state.estimated_tax_safe_harbor.penalty_end", "2026-04-15", FTB_5805),
        ("state.schedule_ca.fund_asset_minimum", .5, "https://www.ftb.ca.gov/forms/2025/2025-540-ca-instructions.html"),
        # These households use Schedule X. The checked CA law also requires a
        # tax-table block. Supply the published first row without claiming
        # that this offline slice can price a tax-table household.
        ("state.tax_table", {"max_taxable_income": 100000,
                             "rows": [{"min": 1, "max": 50, "tax": dict.fromkeys(STATUSES, 0)}]},
         "https://www.ftb.ca.gov/forms/2025/2025-540-taxtable.pdf"),
    ]
    for path, value, source in figures:
        set_figure(YEAR, path, value, source, str(workspace), applies_to=YEAR)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    write_secure(path, json.dumps(value, indent=2) + "\n")


def run(workspace: Path, module: str, *args: str, subcommand: str | None = None,
        expected: int = 0) -> str:
    command = ["sh", "agents/lib/run.sh", "-m", f"agents.lib.{module}"]
    if subcommand:
        command.append(subcommand)
    done = subprocess.run([*command, "--workspace", str(workspace), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == expected, (module, done.returncode, done.stdout, done.stderr)
    return done.stdout if expected == 0 or "--json" in args else done.stderr


def make_workspace(workspace: Path) -> None:
    secure_path(workspace)
    documents = workspace / "documents"
    documents.mkdir()
    secure_path(documents)
    profile = {
        "tax_year": YEAR, "filing_status": "Single", "address": {"state": "CA"},
        "residency": {"domicile_state": "CA", "days_present_by_state": {"CA": 365}}, "dependents": [],
        "flags": dict.fromkeys(capabilities._REQUIRED_GATE_FLAGS, False) | dict.fromkeys((
            "age_65_plus_primary", "ca_has_blind_exemption", "ca_has_nonw2_payments",
            "ca_applies_overpayment_forward", "ca_has_us_obligation_income", "ca_has_nonca_muni_interest",
            "ca_has_other_schedule_ca_adjustments", "ca_has_special_distribution_tax", "ca_has_other_taxes",
            "ca_has_ptet_credit", "ca_has_amt_credit", "ca_has_other_credit_carryover", "ca_owes_use_tax",
            "ca_has_coverage_penalty",
        ), False),
        "ca_exemptions": {"primary_can_be_claimed": False, "primary_blind": False},
        "ca_5805": {"method": "regular", "regular_method_confirmed": True,
                    "current_tax_components_confirmed": True, "section_453a_interest": 0,
                    "accumulation_distribution_tax": 0, "withholding_method": "deemed",
                    "actual_withholding": [], "balance_payments": [], "early_return": None,
                    "prior_return": {"tax_year": 2024, "filed": True, "full_year": True,
                                     "tax": 0, "agi": 0, "withholding": 0, "confirmed": True}},
    }
    write_json(workspace / "taxpayer_profile.json", profile)
    seed_law(workspace)
    write_w2(documents / "w2-one.pdf", 138000, 24000, 9800)
    _document(documents / "broker.pdf", "Synthetic Form 1099-B", [
        ("Box 1d Proceeds", "10,000.00"), ("Box 1e Cost basis", "5,500.00"),
        ("Holding period", "Long-term"), ("Basis reported to IRS", "Yes"),
    ])


def write_w2(path: Path, wages: int, federal: int, california: int, *, medicare: int | None = None) -> None:
    medicare_wages = wages if medicare is None else medicare
    _document(path, "Synthetic Form W-2", [
        (label, f"{amount:,.2f}")
        for label, amount in zip(W2_FIELDS, (wages, federal, wages, medicare_wages, california), strict=True)
    ])


def add_second_w2_and_estimate(workspace: Path) -> None:
    documents = workspace / "documents"
    write_w2(documents / "w2-one.pdf", 81000, 14000, 5100)
    write_w2(documents / "w2-two.pdf", 57000, 10000, 4700)
    set_figure(YEAR, "federal.social_security", {"wage_base": 176100, "employee_rate": .062},
               "https://www.ssa.gov/news/en/cola/factsheets/2025.html", str(workspace), applies_to=YEAR)
    path = workspace / "taxpayer_profile.json"
    profile = read_json(path)
    profile["estimated_payments"] = {
        "federal": [{"payment_id": "estimate-confirmation", "kind": "estimate", "amount": 600,
                     "date": "2025-04-15", "person": "primary", "tax_year": YEAR}],
        "federal_review": {"confirmed": True, "tax_year": YEAR, "estimate_count": 1, "estimate_total": 600},
        "federal_2210": {"method": "regular", "schedule_3_refundable_credits": 0,
                         "excluded_current_year_tax": 0, "prior_year_tax": 0, "prior_year_agi": 0},
    }
    write_json(path, profile)


def add_overlapping_business(workspace: Path) -> None:
    documents = workspace / "documents"
    write_w2(documents / "w2-one.pdf", 138000, 27500, 11000)
    for form in ("1099-NEC", "1099-K"):
        _document(documents / f"{form.lower()}.pdf", f"Synthetic Form {form}", [("Box 1 Gross", "12,000.00")])
    _document(documents / "business-book.pdf", "Synthetic business books", [
        ("Payment ID", "synthetic-receipt"), ("Reported by", "1099-nec,1099-k"),
        ("Gross payment", "12,000.00"), ("Business expenses", "2,000.00"),
        ("Evidence file", BUSINESS_REMITTANCE),
    ])
    write_secure(workspace / BUSINESS_REMITTANCE, "payment,reporters,amount\n"
                 "synthetic-receipt,1099-nec and 1099-k,12000.00\n")
    path = workspace / "taxpayer_profile.json"
    profile = read_json(path)
    profile["flags"]["has_self_employment"] = True
    write_json(path, profile)
    schedule_se = "https://www.irs.gov/pub/irs-prior/f1040sse--2025.pdf"
    for key, value, source in (
        ("social_security", {"wage_base": 176100, "employee_rate": .062},
         "https://www.ssa.gov/news/en/cola/factsheets/2025.html"),
        ("se_tax", {"rate": .153, "net_earnings_factor": .9235}, schedule_se),
        ("qbi_threshold.single", 197300, IRS),
        ("qbi_deduction_rate", .20, "https://www.irs.gov/pub/irs-prior/f8995--2025.pdf"),
    ):
        set_figure(YEAR, f"federal.{key}", value, source, str(workspace), applies_to=YEAR)


def _read_boxes(path: Path, workspace: Path) -> tuple[dict, str]:
    snapshot = parse_utils.read_source_snapshot(str(path), {".pdf"}, str(workspace))
    tables = parse_utils.extract_tables(str(path), workspace=str(workspace), _source=snapshot)
    (table,) = tables[0]
    return dict(table), hashlib.sha256(snapshot).hexdigest()


def _amount(boxes: dict, label: str) -> float:
    return float(boxes[label].replace(",", ""))


def parse_sources(workspace: Path, *, unread_box_2: bool = False) -> list[dict]:
    """Classify just the simulated layouts from the same masked bytes as the ledger."""
    records = []
    for source in sorted((workspace / "documents").glob("*.pdf")):
        boxes, digest = _read_boxes(source, workspace)
        record: dict = {"doc_id": source.stem, "file": source.relative_to(workspace).as_posix(),
                        "source_sha256": digest, "person": "primary", "source_form": {"tax_year": YEAR}}
        if source.stem.startswith("w2-"):
            unread = unread_box_2 and source.stem == "w2-one"
            if unread:
                boxes.pop("Box 2 Federal income tax withheld")
                record["w2_box_status"] = {"2": "unread"}
            record.update(form_type="W-2", payer=f"Synthetic employer {source.stem}",
                          key_boxes={category: _amount(boxes, label) for label, category in W2_FIELDS.items()
                                     if label in boxes})
        elif source.stem == "broker":
            assert boxes["Holding period"] == "Long-term"
            assert boxes["Basis reported to IRS"] == "Yes"
            record.update(form_type="1099-B", payer="Synthetic broker",
                          key_boxes={"capital_proceeds": _amount(boxes, "Box 1d Proceeds")})
        elif source.stem in ("1099-nec", "1099-k"):
            gross = _amount(boxes, "Box 1 Gross")
            key_boxes = {"self_employment_gross": gross}
            if source.stem == "1099-k":
                key_boxes["form_1099k_box_1a"] = gross
            record.update(form_type=source.stem.upper(), payer=f"Synthetic payer {source.stem}",
                          key_boxes=key_boxes)
        else:
            assert source.stem == "business-book"
            gross = _amount(boxes, "Gross payment")
            record.update(form_type="business books", payer="Synthetic proprietor",
                          key_boxes={"self_employment_book_gross": gross},
                          business_receipts=[{"payment_id": boxes["Payment ID"], "amount": gross,
                                              "reported_by": boxes["Reported by"].split(","),
                                              "evidence_file": boxes["Evidence file"]}])
        record["boxes"] = boxes
        parse_records.write_record(workspace, record)
        records.append(record)
    run(workspace, "parse_records", "--build-ledger")
    return records


def prepare_return(workspace: Path, records: list[dict]) -> None:
    """Supply the elections and brokerage classification normally set by the skill."""
    template = read_json(ROOT / "agents" / "templates" / "result_template.json")
    result = {name: template[name] for name in ("meta", "form_1040", "schedule_d", "california_540")}
    result["meta"] = {"filing_status": "Single", "tax_year": YEAR, "limitations": []}
    law = read_json(workspace / LAW)
    (broker,) = [record for record in records if record["form_type"] == "1099-B"]
    proceeds = broker["key_boxes"]["capital_proceeds"]
    basis = _amount(broker["boxes"], "Box 1e Cost basis")
    long_term = result["schedule_d"]["part_ii_long_term"]
    long_term["line_8a_1099b_box_d"].update(proceeds=proceeds, cost=basis, gain=proceeds - basis)
    long_term["line_15_net_long_term"] = proceeds - basis
    form = result["form_1040"]
    result["schedule_d"]["line_16_combined"] = proceeds - basis
    form["line_7_capital_gain"] = proceeds - basis
    form["line_12_standard_deduction"] = law["federal"]["filing_statuses"]["single"]["standard_deduction"]
    result["california_540"].update(ca_filing_status="Single", ca_deduction=law["state"]["standard_deduction"]["single"],
                                    ca_deduction_method="standard", ca_preference_adjustments=0,
                                    ca_preference_adjustments_confirmed=True)
    books = next((record for record in records if record["form_type"] == "business books"), None)
    if books:
        gross = books["key_boxes"]["self_employment_book_gross"]
        expenses = _amount(books["boxes"], "Business expenses")
        net = gross - expenses
        wages = sum(row["key_boxes"].get("ss_wages", 0) for row in records)
        result["schedule_c"] = {**template["schedule_c"], "line_1_gross_receipts": gross,
                                "line_7_gross_income": gross, "line_28_total_expenses": expenses,
                                "line_29_tentative_profit": gross - expenses,
                                "line_31_net": net, "activities": [{"person": "primary",
                                    "activity_id": "synthetic-trade", "line_31_net": net, "se_treatment": "regular"}]}
        result["schedule_1_other_income"] = {"remaining_income": 0, "remaining_income_confirmed": True,
                                             "schedule_c_excluded_from_remaining": True}
        result["schedule_se"] = {"inputs": {"tax_year": YEAR,
            "by_owner": {"primary": {"confirmed": True, "ss_wages": wages, "other_net_profit": 0}},
            "remaining_adjustments": 0, "adjustments_exclude_se_deduction": True,
            "remaining_other_taxes": 0, "other_taxes_exclude_se_tax": True}}
    write_json(workspace / "result.json", result)


def _apply_qbi(workspace: Path, result: dict) -> None:
    law = read_json(workspace / LAW)["federal"]
    form = result["form_1040"]
    qbi = compute_qbi_deduction(result["schedule_c"]["line_31_net"], 0, rate=law["qbi_deduction_rate"],
                                taxable_income_before_qbi=form["line_15_taxable_income"],
                                net_capital_gain=form["line_7_capital_gain"], threshold=law["qbi_threshold"]["single"],
                                half_se_tax=result["schedule_se"]["schedule_1_line_15_deductible_se_tax"], tax_year=YEAR)
    assert not qbi["over_threshold"]
    form["line_13a_qbi_deduction"] = qbi["total_deduction"]
    result["form_8995_qbi"] = {"line_2_total_qbi": qbi["qbi_income"], "line_5_qbi_component": qbi["qbi_component"],
                               "line_15_qbi_deduction": qbi["total_deduction"]}
    write_json(workspace / "result.json", result)


def validate(workspace: Path) -> None:
    run(workspace, "return_builder", "--check")
    run(workspace, "tie_out", "--json")


def record_deduction(workspace: Path) -> None:
    run(workspace, "material_evidence", "--id", "input.deduction_election", "--label", "Basic standard deduction",
        "--outcome", "computed", "--deduction-method", "standard", "--input", "result.json",
        "--input", "taxpayer_profile.json", "--input", LAW)


def record_business_overlap(workspace: Path) -> None:
    run(workspace, "material_evidence", "--id", "input.business_receipt_overlap",
        "--label", "Business overlap review", "--outcome", "reviewed",
        "--input", "parse_ledger.json", "--input", BUSINESS_REMITTANCE)


def compute(workspace: Path, *, unread_box_2: bool = False) -> None:
    records = parse_sources(workspace, unread_box_2=unread_box_2)
    run(workspace, "capabilities", "--json")
    prepare_return(workspace, records)
    run(workspace, "return_builder")
    result = read_json(workspace / "result.json")
    if "schedule_c" in result:
        _apply_qbi(workspace, result)
        run(workspace, "return_builder")
        record_business_overlap(workspace)
    record_deduction(workspace)
    validate(workspace)
