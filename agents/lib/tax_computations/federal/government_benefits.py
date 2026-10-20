"""Reviewed 1099-G benefit sources and their Schedule 1 workpaper (AA65).

Source facts are never inferred from a payer's address or a withholding box.
The caller supplies the separately reviewed non-benefit Schedule 1 amount.
"""

import math


BENEFIT_CATEGORIES = ("unemployment_compensation", "government_family_leave")
_REVISIONS = {2025: "03/2024", 2026: "12/2026"}


def _money(value, field: str, *, signed: bool = False) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid or (not signed and value < 0):
        raise ValueError(f"{field} must be a finite {'signed' if signed else 'nonnegative'} amount")
    return round(float(value), 2)


def validate_government_benefits(raw, boxes: dict) -> dict:
    """Validate a physical document's source split without exposing its data."""
    keys = {"tax_year", "form_revision", "box_1", "box_10", "unemployment_repaid",
            "unemployment_contribution_reduction", "reviewed", "family_programs"}
    if not isinstance(raw, dict) or set(raw) != keys or not isinstance(boxes, dict):
        raise ValueError("government_benefits requires the reviewed year/revision, boxes, UI reductions and family programs")
    year = raw["tax_year"]
    if type(year) is not int or year not in _REVISIONS:
        raise ValueError("1099-G benefits form year has not been reviewed; use the covered-year source worksheet")
    if raw["form_revision"] != _REVISIONS[year] or raw["reviewed"] is not True:
        raise ValueError("confirm the covered-year 1099-G revision, program identity and all benefit adjustments")
    gross = {k: _money(boxes.get(k), k) for k in BENEFIT_CATEGORIES}
    b1 = _money(raw["box_1"], "1099-G box1")
    b10 = None if year == 2025 else _money(raw["box_10"], "1099-G box10")
    if year == 2025 and raw["box_10"] is not None:
        raise ValueError("the TY2025 revision has state box10a/10b, not a family-benefit box10")
    ui = gross["unemployment_compensation"]
    family = gross["government_family_leave"]
    expected1 = round(ui + family, 2) if year == 2025 else ui
    if b1 != expected1 or (year == 2026 and b10 != family):
        raise ValueError("1099-G gross benefit categories do not reconcile to the revision's source boxes")
    repaid = _money(raw["unemployment_repaid"], "same-year UI repayments not already netted")
    reduction = _money(raw["unemployment_contribution_reduction"], "reviewed eligible UI contribution reduction")
    if round(repaid + reduction, 2) > ui:
        raise ValueError("UI reductions exceed this year's reported UI; prior-year repayments need their own worksheet")
    programs = raw["family_programs"]
    if not isinstance(programs, list):
        raise ValueError("family_programs must be a source-reviewed list")
    normalized = []
    for row in programs:
        if not isinstance(row, dict):
            raise ValueError("each family program must be an object")
        program = row.get("program")
        if program not in ("ca_edd_pfl", "other_government"):
            raise ValueError("identify the governmental family program; medical, private and unknown benefits need separate resolution")
        required = {"program", "amount", "schedule1_route"}
        if program == "other_government":
            required |= {"ca_exempt", "ca_exemption_confirmed"}
        if set(row) != required:
            raise ValueError("family programs need amount/reporting route and other programs need an explicit CA exemption decision or unanswered null")
        route = row["schedule1_route"]
        if route not in ("7", "other"):
            raise ValueError("confirm the source-reviewed family reporting route: legacy Schedule1 line7 or other income")
        if year == 2026 and route != "other":
            raise ValueError("new 1099-G box10 governmental family benefits are other income, not UI under section85")
        item = {"program": program, "amount": _money(row["amount"], "governmental family benefits"),
                "schedule1_route": route}
        if program == "other_government":
            exempt, confirmed = row["ca_exempt"], row["ca_exemption_confirmed"]
            if ((exempt is not None and type(exempt) is not bool) or type(confirmed) is not bool
                    or (exempt is None and confirmed) or (exempt is not None and not confirmed)):
                raise ValueError("other-program CA exemption must be a confirmed boolean or explicit unanswered null")
            item.update(ca_exempt=exempt, ca_exemption_confirmed=confirmed)
        normalized.append(item)
    if round(sum(r["amount"] for r in normalized), 2) != family:
        raise ValueError("family program amounts do not reconcile to government_family_leave")
    return {"tax_year": year, "form_revision": _REVISIONS[year], "box_1": b1, "box_10": b10,
            "unemployment_repaid": repaid, "unemployment_contribution_reduction": reduction,
            "reviewed": True, "family_programs": normalized}


def compute_government_benefits(*, tax_year: int, records: list, other_income,
                                other_income_confirmed: bool, require_ca: bool = False) -> dict:
    """Compute the benefits and actual 1040 line8, preserving signed other income."""
    if type(tax_year) is not int or tax_year not in _REVISIONS:
        raise ValueError("government benefits return year has not been reviewed")
    if (not isinstance(records, list) or type(other_income_confirmed) is not bool
            or type(require_ca) is not bool):
        raise ValueError("government benefits records and an explicit other-income confirmation are required")
    other = _money(other_income, "Schedule1 income excluding these UI/family benefits", signed=True)
    ui = family = repaid = reduction = family7 = family_other = ca_family = ca_family7 = 0.0
    unknown_ca = manual_ca = False
    for record in records:
        if not isinstance(record, dict) or set(record) != {"key_boxes", "government_benefits"}:
            raise ValueError("benefit records need their key_boxes and government_benefits")
        boxes = record["key_boxes"]
        source = validate_government_benefits(record["government_benefits"], boxes)
        if source["tax_year"] != tax_year:
            raise ValueError("government benefit source year does not match this return")
        ui += _money(boxes["unemployment_compensation"], "UI gross")
        family += _money(boxes["government_family_leave"], "family gross")
        repaid += source["unemployment_repaid"]
        reduction += source["unemployment_contribution_reduction"]
        for row in source["family_programs"]:
            if row["schedule1_route"] == "7":
                family7 += row["amount"]
            else:
                family_other += row["amount"]
            if row["program"] == "ca_edd_pfl":
                ca_family += row["amount"]
                if row["schedule1_route"] == "7":
                    ca_family7 += row["amount"]
            elif row["amount"]:
                if row["ca_exempt"] is None:
                    unknown_ca = True
                else:
                    manual_ca = True
                    if row["ca_exempt"]:
                        ca_family += row["amount"]
                        if row["schedule1_route"] == "7":
                            ca_family7 += row["amount"]
    if (ui or family) and not other_income_confirmed:
        raise ValueError("confirm Schedule1 non-benefit income separately, including zero; never reuse an old aggregate containing benefits")
    if require_ca and unknown_ca:
        raise ValueError("resolve California treatment of non-EDD governmental family benefits from a source worksheet")
    taxable_ui = max(0.0, round(ui - repaid - reduction, 2))
    out = {"other_income": other, "other_income_confirmed": other_income_confirmed,
           "unemployment_gross": round(ui, 2), "family_leave_gross": round(family, 2),
           "unemployment_repaid": round(repaid, 2), "unemployment_contribution_reduction": round(reduction, 2),
           "unemployment_taxable": taxable_ui, "family_leave_taxable": round(family, 2),
           "schedule1_line_7": round(taxable_ui + family7, 2),
           "family_leave_other_income": round(family_other, 2),
           "total": round(other + taxable_ui + family, 2),
           "ca_family_leave_exempt": None if unknown_ca else round(ca_family, 2),
           "ca_family_leave_line_7": None if unknown_ca else round(ca_family7, 2),
           "source_note": ("Government benefits: CA treatment of some family programs uses separately confirmed source worksheets."
                           if require_ca and manual_ca else None)}
    for key, value in out.items():
        if type(value) is float:
            _money(value, key, signed=key in ("other_income", "total"))
    return out
