"""Synthetic law provenance for tests exercising final export."""

import json
from datetime import datetime

from agents.lib import material_evidence
from agents.lib.tax_law_fetcher import _figure_digests, create_empty_tax_law, create_state_template, source_pins


def pin_synthetic_law(law: dict, *, retrieved: datetime | None = None) -> dict:
    timestamp = (retrieved or datetime.now()).replace(microsecond=0).isoformat()
    primary = "https://www.irs.gov/filing"
    pins = {f"federal.{name}": f"federal.{name}" for name in law.get("federal", {})} | source_pins(law)
    law["_sources"] = {key: primary for key in pins}
    law["_provenance"] = {key: {"source": primary, "retrieved": timestamp,
                               "applies_to": law["tax_year"], "figure_digests": _figure_digests(law, path)}
                          for key, path in pins.items()}
    return law


def write_synthetic_filing_law(workspace) -> dict:
    result_path = workspace / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    profile_path = workspace / "taxpayer_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    meta = result.setdefault("meta", {})
    if "tax_year" not in meta or "filing_status" not in meta:
        meta.setdefault("tax_year", profile.get("tax_year", 2025))
        meta.setdefault("filing_status", profile.get("filing_status", "MFJ"))
        result_path.write_text(json.dumps(result), encoding="utf-8")
    year = meta["tax_year"]
    if not profile_path.exists():
        profile = {"tax_year": year, "filing_status": meta["filing_status"]}
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
    law_path = workspace / f"tax_law_{year}.json"
    law = json.loads(law_path.read_text(encoding="utf-8")) if law_path.exists() else {}
    law["tax_year"] = year
    federal = law.setdefault("federal", {})
    federal.setdefault("filing_statuses", {}).setdefault(meta["filing_status"].lower(), {
        "standard_deduction": 30000, "brackets": [{"rate": 0.1, "min": 0, "max": None}],
    })
    if (profile.get("address") or {}).get("state") == "FL":
        law.setdefault("state", create_state_template("FL", year))
    if result.get("schedule_c", {}).get("activities"):
        federal.setdefault("qbi_threshold", {"mfj": 300000})
    # Statutory blocks `create` ships complete: wages and investment income
    # require them, and an unchanged seed needs no fetch provenance.
    seed = create_empty_tax_law(year)["federal"]
    for block in ("niit", "additional_medicare_tax"):
        federal.setdefault(block, seed[block])
    pin_synthetic_law(law)
    law_path.write_text(json.dumps(law), encoding="utf-8")
    return law


def write_synthetic_filing_inputs(workspace) -> None:
    result_path = workspace / "result.json"
    profile_path = workspace / "taxpayer_profile.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    law_name = material_evidence._law_name(result, profile)
    law = json.loads((workspace / law_name).read_text(encoding="utf-8"))
    status = (result["meta"].get("filing_status") or profile["filing_status"]).lower()
    base = law["federal"]["filing_statuses"][status]["standard_deduction"]
    form = result["form_1040"]
    if "line_12_standard_deduction" not in form:
        form["line_12_standard_deduction"] = base
        result_path.write_text(json.dumps(result), encoding="utf-8")
    worksheet = workspace / "synthetic-filing-workpaper.json"
    worksheet.write_text('{"review": "Synthetic worksheet fixtures"}', encoding="utf-8")
    sources = ["result.json", "taxpayer_profile.json", law_name]
    for item_id in material_evidence._required_filing_inputs(result, profile):
        method = None
        if item_id == material_evidence.DEDUCTION_ITEM:
            if (result.get("schedule_a") or {}).get("use_itemized") is True:
                method = "itemized"
            elif form["line_12_standard_deduction"] == base:
                method = "standard"
            else:
                method = "standard_worksheet"
        material_evidence.record(workspace, item_id, "Synthetic review", "reviewed",
                                 sources + [worksheet.name], deduction_method=method)
