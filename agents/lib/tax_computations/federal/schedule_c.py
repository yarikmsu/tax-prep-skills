"""Settlement of reviewed Schedule C and Schedule SE figures on Form 1040."""

from typing import Iterable

from agents.lib.tax_computations._shared import _amount, _normalize_filing_status


def schedule_c_net(result: dict, profile: dict) -> float | None:
    """The Schedule 1 line 3 input, or None when Schedule C is inapplicable."""
    if not isinstance(result.get("form_1040"), dict):
        return None
    section = result.get("schedule_c")
    if section is not None and not isinstance(section, dict):
        raise ValueError("schedule_c must be an object")
    section = section or {}
    active = any(section.get(field) for field in (
        "line_1_gross_receipts", "line_7_gross_income", "line_28_total_expenses", "line_30_home_office",
        "line_31_net", "line_31_net_profit", "line_31_net_loss", "se_tax", "activities"))
    if not active and (profile.get("flags") or {}).get("has_self_employment") is not True:
        return None
    return round(_amount(section.get("line_31_net"), "Schedule C line 31", allow_negative=True), 2)


def _schedule_c_owners(result: dict, net: float, owners: tuple[str, ...]) -> tuple[dict[str, float], set[str]]:
    rows = result["schedule_c"].get("activities")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Record each reviewed Schedule C activity with its owner, line 31 and SE treatment")
    total = 0.0
    taxable: dict[str, float] = {}
    exemptions: set[str] = set()
    activity_ids: set[str] = set()
    for index, row in enumerate(rows, 1):
        where = f"Schedule C activity #{index}"
        if not isinstance(row, dict) or row.get("person") not in owners:
            raise ValueError(f"{where} requires the owner on this return")
        activity_id = row.get("activity_id")
        if not isinstance(activity_id, str) or not activity_id or activity_id != activity_id.strip() or activity_id in activity_ids:
            raise ValueError(f"{where} needs a unique local reference to its reviewed business worksheet")
        activity_ids.add(activity_id)
        owner = row["person"]
        share = _amount(row.get("line_31_net"), f"{where} line 31", allow_negative=True)
        total += share
        taxable.setdefault(owner, 0.0)
        if row.get("se_treatment") == "regular":
            taxable[owner] += share
        elif row.get("se_treatment") == "approved_form_4361" and row.get("approval_confirmed") is True:
            exemptions.add(owner)
        else:
            raise ValueError(f"{where} requires regular SE treatment or reviewed IRS Form 4361 approval")
    if round(total, 2) != net:
        raise ValueError("The reviewed Schedule C activities must add up to Schedule C line 31")
    return taxable, exemptions


def _covered_wage_floor(ledger: Iterable, owner: str) -> float:
    return sum(dict(record.key_boxes).get("ss_wages", 0) + dict(record.key_boxes).get("ss_tips", 0)
               for record in ledger if record.person == owner and record.skipped is None)


def _rates(tax_law: dict) -> dict[str, float]:
    federal = tax_law.get("federal")
    ss = federal.get("social_security") if isinstance(federal, dict) else None
    se = federal.get("se_tax") if isinstance(federal, dict) else None
    if not isinstance(ss, dict) or not isinstance(se, dict):
        raise ValueError("The covered-year Social Security and self-employment tax cache is required")
    base = _amount(ss.get("wage_base"), "cached federal.social_security.wage_base")
    ss_rate = 2 * _amount(ss.get("employee_rate"), "cached federal.social_security.employee_rate")
    combined = _amount(se.get("rate"), "cached federal.se_tax.rate")
    factor = _amount(se.get("net_earnings_factor"), "cached federal.se_tax.net_earnings_factor")
    if not (base > 0 and 0 < ss_rate < combined < 1 and 0 < factor < 1):
        raise ValueError("Check the covered-year Social Security and self-employment tax cache")
    return {"ss_wage_base": base, "ss_rate": ss_rate, "medicare_rate": combined - ss_rate,
            "deductible_factor": factor}


def schedule_se_workpaper(result: dict, profile: dict, ledger: Iterable, tax_law: dict,
                          *, require_other_taxes: bool) -> dict | None:
    net = schedule_c_net(result, profile)
    if net is None:
        return None
    meta = result.get("meta") or {}
    year = meta.get("tax_year") or profile.get("tax_year")
    if type(year) is not int or profile.get("tax_year", year) != year:
        raise ValueError("Schedule C and Schedule SE need the covered tax year")
    status = _normalize_filing_status(meta.get("filing_status") or profile.get("filing_status"))
    if profile.get("filing_status") and _normalize_filing_status(profile["filing_status"]) != status:
        raise ValueError("Schedule SE ownership requires agreement on the filing status")
    owners = ("primary", "spouse") if status == "MFJ" else ("primary",)
    taxable, exemptions = _schedule_c_owners(result, net, owners)
    workpaper = result.get("schedule_se")
    inputs = workpaper.get("inputs") if isinstance(workpaper, dict) else None
    if not isinstance(inputs, dict) or type(inputs.get("tax_year")) is not int or inputs["tax_year"] != year:
        raise ValueError("Review Schedule SE inputs for this tax year")
    return _settle(inputs, owners, taxable, exemptions, ledger, tax_law, year, net, require_other_taxes)


def _settle(inputs, owners, taxable, exemptions, ledger, tax_law, year, net, require_other_taxes) -> dict:
    from agents.lib.tax_computations import compute_se_tax

    by_owner = inputs.get("by_owner")
    if not isinstance(by_owner, dict) or set(by_owner) - set(owners) or set(taxable) - set(by_owner):
        raise ValueError("Review Schedule SE wages and other SE earnings for each owner")
    if inputs.get("adjustments_exclude_se_deduction") is not True:
        raise ValueError("Confirm remaining Schedule 1 adjustments excluding the Schedule SE deduction")
    adjustments = _amount(inputs.get("remaining_adjustments"), "remaining Schedule 1 adjustments")
    other_taxes = inputs.get("remaining_other_taxes")
    if other_taxes is None and not require_other_taxes:
        taxes = None
    else:
        if inputs.get("other_taxes_exclude_se_tax") is not True:
            raise ValueError("Confirm remaining Schedule 2 Part II taxes excluding Schedule SE")
        taxes = _amount(other_taxes, "remaining Schedule 2 Part II taxes")

    calculated = {}
    for owner in owners:
        if owner not in by_owner:
            continue
        item = by_owner[owner]
        if not isinstance(item, dict) or item.get("confirmed") is not True:
            raise ValueError(f"Confirm Schedule SE wages and additional earnings for the {owner}")
        wages = _amount(item.get("ss_wages"), f"{owner} Schedule SE line 8d")
        if wages < round(_covered_wage_floor(ledger, owner), 2):
            raise ValueError(f"{owner} Schedule SE line 8d is below the parsed Social Security wages and tips")
        supplemental = _amount(item.get("other_net_profit"), f"{owner} other Schedule SE profit or loss", allow_negative=True)
        business_net = taxable.get(owner, 0.0)
        net_subject = round(business_net + supplemental, 2)
        priced = compute_se_tax(net_subject, w2_ss_wages=wages, **(_rates(tax_law) if net_subject > 0 else {}))
        calculated[owner] = {
            "schedule_c_net_subject_to_se": round(business_net, 2),
            "other_net_profit": supplemental,
            "line_8d_social_security_wages": wages,
            "net_earnings": priced["se_earnings"],
            "line_12_se_tax": priced["se_tax"],
            "line_13_deductible_half": priced["deductible_half"],
            "approved_form_4361": owner in exemptions,
        }
    se_tax = round(sum(item["line_12_se_tax"] for item in calculated.values()), 2)
    deduction = round(sum(item["line_13_deductible_half"] for item in calculated.values()), 2)
    return {
        "inputs": inputs, "computed_tax_year": year, "by_owner": calculated,
        "schedule_1_line_3_business_income": net,
        "schedule_1_line_15_deductible_se_tax": deduction,
        "schedule_2_line_4_se_tax": se_tax,
        "line_10_adjustments": round(adjustments + deduction, 2),
        "line_23_other_taxes": round(taxes + se_tax, 2) if taxes is not None else None,
    }


def flow_differences(result: dict, workpaper: dict | None) -> list[str]:
    if workpaper is None:
        return []
    differences = []
    if result.get("schedule_se") != workpaper:
        differences.append("Schedule SE owner workpapers")
    if (result.get("schedule_c") or {}).get("se_tax") != workpaper["schedule_2_line_4_se_tax"]:
        differences.append("Schedule C summary and Schedule 2 line 4 SE tax")
    form = result.get("form_1040") or {}
    for field in ("line_10_adjustments", "line_23_other_taxes"):
        if form.get(field) != workpaper[field]:
            differences.append(f"Form 1040 {field}")
    return differences
