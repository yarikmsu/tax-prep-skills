"""Reviewed MISC income boxes and the non-benefit Schedule 1 component (AA67)."""

import agents.lib  # noqa: F401 — the host-interpreter guard
from agents.lib.tax_computations.federal.government_benefits import _money
from agents.lib.tax_computations.federal.gambling_income import compute_w2g_totals, WORKSHEET_NOTE, GAMBLING_NOTE

_REVISIONS = {2025: "04/2025", 2026: "12/2026"}
MISC_OTHER = "miscellaneous_other_income"


def classify_misc_income(raw) -> dict:
    """Map reviewed amounts, never a broad range of numbered form boxes."""
    fields = {"tax_year", "form_revision", "reviewed", "box_1", "box_2", "box_3", "box_3_business",
              "box_3_other", "box_3_unresolved", "box_4", "box_5", "box_6",
              "box_7", "box_8", "box_9", "box_10", "box_11", "box_15",
              "other_business_gross", "other_federal_withholding"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("misc_income requires the covered year/revision, every reviewed income box and explicit box3 split")
    year = raw["tax_year"]
    if type(year) is not int or year not in _REVISIONS:
        raise ValueError("1099-MISC form year has not been reviewed")
    if raw["form_revision"] != _REVISIONS[year] or raw["reviewed"] is not True:
        raise ValueError("confirm the covered-year MISC revision and source treatment, including other business receipts")
    if type(raw["box_7"]) is not bool:
        raise ValueError("1099-MISC box7 is a checkbox, not an amount; confirm checked or unchecked")
    amounts = {k: _money(raw[k], f"1099-MISC {k}") for k in fields
               if k not in {"tax_year", "form_revision", "reviewed", "box_7"}}
    if round(sum(amounts[k] for k in ("box_3_business", "box_3_other", "box_3_unresolved")), 2) != amounts["box_3"]:
        raise ValueError("1099-MISC box3 source split must reconcile to the reported gross amount")
    # Income boxes with no route here (box 1 rents go to the rental inventory):
    # a reviewed inventory must not let them
    # pass as an informational form. Box 15 also carries the section 409A tax.
    for box, label in (("box_2", "royalties"), ("box_9", "crop insurance proceeds"),
                       ("box_10", "gross proceeds paid to an attorney"),
                       ("box_15", "nonqualified deferred compensation")):
        if amounts[box]:
            raise ValueError(f"1099-MISC {box} ({label}) is not computed here; prepare that income outside this toolkit")
    if amounts["box_3_unresolved"]:
        raise ValueError("resolve 1099-MISC box3 treatment; Schedule F, exclusions and unknown payments cannot default to Schedule C or taxable other income")
    business = _money(round(sum(amounts[k] for k in
        ("box_3_business", "box_5", "box_6", "box_11", "other_business_gross")), 2), "MISC business gross total")
    # Omit zero categories: a checkbox-only notice has no monetary ledger sum.
    return {k: v for k, v in {
        "rental_reported_rents": amounts["box_1"],
        "self_employment_gross": business, MISC_OTHER: _money(amounts["box_3_other"] + amounts["box_8"], "MISC other income"),
        "federal_withholding_1099": _money(amounts["box_4"] + amounts["other_federal_withholding"], "MISC and other document withholding"),
    }.items() if v}


def validate_misc_income(raw, boxes: dict, *, skipped: bool = False) -> dict:
    mapped = classify_misc_income(raw)
    if not isinstance(boxes, dict):
        raise ValueError("MISC key_boxes must be an object")
    if skipped and mapped:
        raise ValueError("a skipped MISC record cannot carry reportable income or withholding")
    for category in ("self_employment_gross", MISC_OTHER, "federal_withholding_1099", "rental_reported_rents"):
        if _money(boxes.get(category, 0), category) != mapped.get(category, 0):
            raise ValueError("MISC canonical income/withholding categories disagree with reviewed source boxes")
    # Copy source facts; no caller can mutate a validated record through `raw`.
    return {k: (_money(v, f"1099-MISC {k}") if k not in
        {"tax_year", "form_revision", "reviewed", "box_7"} else v) for k, v in raw.items()}


def compute_schedule1_other(*, tax_year: int, records: list, remaining_income,
                            remaining_income_confirmed: bool, w2g_records: list | None = None,
                            w2g_excluded_from_remaining: bool = False) -> dict:
    """The non-benefit Schedule1 component; UI/PFL are added by their workpaper."""
    if type(tax_year) is not int or tax_year not in _REVISIONS:
        raise ValueError("1099-MISC return year has not been reviewed")
    if not isinstance(records, list) or remaining_income_confirmed is not True:
        raise ValueError("confirm remaining Schedule1 income EXCLUDING tracked MISC and government benefits, including zero")
    if type(w2g_excluded_from_remaining) is not bool:
        raise ValueError("the W-2G exclusion from remaining income must be an explicit boolean")
    gambling = compute_w2g_totals(tax_year=tax_year, records=[] if w2g_records is None else w2g_records)
    gambling_active = (gambling["gambling_winnings"] or gambling["gambling_income"]
                       or gambling["gambling_business_gross"] or gambling["worksheet_used"])
    if gambling_active:
        if w2g_excluded_from_remaining is not True:
            raise ValueError("reconfirm remaining income EXCLUDING separately mapped casual W-2G income; keep reviewed Schedule C NET income, including zero")
    remaining = _money(remaining_income, "remaining Schedule1 income", signed=True)
    miscellaneous = 0.0
    for rec in records:
        if not isinstance(rec, dict) or set(rec) != {"key_boxes", "misc_income"}:
            raise ValueError("MISC records require source facts and key_boxes")
        source = validate_misc_income(rec["misc_income"], rec["key_boxes"])
        if source["tax_year"] != tax_year:
            raise ValueError("MISC source year does not match the return")
        miscellaneous += source["box_3_other"] + source["box_8"]
    miscellaneous = _money(miscellaneous, "MISC Schedule1 total")
    return {"remaining_income": remaining, "remaining_income_confirmed": True,
            MISC_OTHER: miscellaneous,
            "gambling_winnings": gambling["gambling_winnings"],
            "gambling_income": gambling["gambling_income"],
            "gambling_business_gross": gambling["gambling_business_gross"],
            "w2g_excluded_from_remaining": w2g_excluded_from_remaining,
            "source_note": (WORKSHEET_NOTE if gambling["worksheet_used"] else GAMBLING_NOTE) if gambling_active else None,
            "total": _money(round(remaining + miscellaneous + gambling["gambling_income"], 2), "non-benefit Schedule1 total", signed=True),
            "computed_tax_year": tax_year}
