"""Reviewed W-2G income and other-form withholding settlements (AA66)."""

import re

import agents.lib  # noqa: F401 — the host-interpreter guard
from agents.lib.tax_computations.federal.government_benefits import _money

# The January 2026 form is continuous-use, so a 2025 W-2G furnished on it is valid.
_REVISIONS = {2025: ("12/2023", "01/2026"), 2026: ("01/2026",)}
GAMBLING_REPORTED = "gambling_winnings"
GAMBLING_WITHHOLDING = "federal_withholding_w2g"
WORKSHEET_NOTE = "W-2G income: source contributions use separately reviewed gambling worksheets; wagering deductions are not computed."
GAMBLING_NOTE = "W-2G income: source winnings and withholding are reconciled; gambling loss and business worksheets are not computed."


def is_w2g_form(value) -> bool:
    """Recognize composite/corrected forms without treating W-2GU as gambling."""
    return isinstance(value, str) and re.search(
        r"(?<![A-Z0-9])W[\s\-‐‑–—]*2[\s\-‐‑–—]*G(?![A-Z0-9])", value.upper()) is not None


def _source(raw) -> dict:
    fields = {"tax_year", "form_revision", "reviewed", "ownership_confirmed",
              "box_1", "box_4", "box_7", "identical_wagers_reviewed",
              "schedule_1_income", "schedule_c_gross", "income_method",
              "worksheet_confirmed", "worksheet_id", "other_business_gross", "ca_lottery_income"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("w2g_income requires the reviewed year/revision, source boxes, ownership and income components")
    year = raw["tax_year"]
    if type(year) is not int or year not in _REVISIONS:
        raise ValueError("W-2G covered year has not been reviewed")
    if (raw["form_revision"] not in _REVISIONS[year] or raw["reviewed"] is not True
            or raw["ownership_confirmed"] is not True):
        raise ValueError("confirm the covered-year W-2G revision and this return's winnings/withholding ownership; resolve Form5754 allocations first")
    if type(raw["identical_wagers_reviewed"]) is not bool or type(raw["worksheet_confirmed"]) is not bool:
        raise ValueError("W-2G source review decisions must be booleans")
    amounts = {k: _money(raw[k], f"W-2G {k}") for k in
        ("box_1", "box_4", "box_7", "schedule_1_income", "schedule_c_gross", "other_business_gross",
         "ca_lottery_income")}
    if amounts["box_7"] and raw["identical_wagers_reviewed"] is not True:
        raise ValueError("reconcile identical-wager box7 across forms and books; never automatically add or discard it")
    method = raw["income_method"]
    if method == "reported":
        # Box 7 is "additional winnings from identical wagers", not part of box 1.
        if round(amounts["schedule_1_income"] + amounts["schedule_c_gross"], 2) != round(
                amounts["box_1"] + amounts["box_7"], 2):
            raise ValueError("W-2G income components must reconcile to box1 plus box7 or use an explicitly reviewed source worksheet")
        if raw["worksheet_confirmed"] is not False or raw["worksheet_id"] is not None:
            raise ValueError("a reported-amount W-2G uses no separate income worksheet")
    elif method == "reviewed_worksheet":
        identifier = raw["worksheet_id"]
        if (raw["worksheet_confirmed"] is not True or not isinstance(identifier, str)
                or re.fullmatch(r"[a-z][a-z_-]{0,47}", identifier) is None):
            raise ValueError("confirm the gambling source worksheet and an opaque alphabetic worksheet label, never identity numbers")
    else:
        raise ValueError("confirm W-2G income_method: reported or reviewed_worksheet; unknown tax treatment cannot default to income")
    # California excludes California State Lottery winnings: only this form's
    # federally included amount can be subtracted on Schedule CA.
    if amounts["ca_lottery_income"] > round(amounts["schedule_1_income"] + amounts["schedule_c_gross"], 2):
        raise ValueError("California lottery income cannot exceed this W-2G's federally included income")
    return {**raw, **amounts}


def _categories(source: dict) -> dict:
    business = _money(source["schedule_c_gross"] + source["other_business_gross"], "W-2G document business receipts")
    return {k: v for k, v in {GAMBLING_REPORTED: _money(source["box_1"] + source["box_7"], "W-2G reported winnings"),
        GAMBLING_WITHHOLDING: source["box_4"], "self_employment_gross": business}.items() if v}


def classify_w2g_income(raw) -> dict:
    return _categories(_source(raw))


def validate_w2g_income(raw, boxes: dict, *, skipped: bool = False) -> dict:
    source = _source(raw)
    if not isinstance(boxes, dict) or type(skipped) is not bool:
        raise ValueError("W-2G key_boxes and skipped decision must be typed")
    expected = _categories(source)
    if skipped and (expected or source["schedule_1_income"] or source["schedule_c_gross"]):
        raise ValueError("a skipped W-2G cannot carry winnings, income contributions or withholding")
    for key in (GAMBLING_REPORTED, GAMBLING_WITHHOLDING, "self_employment_gross"):
        if _money(boxes.get(key, 0), key) != expected.get(key, 0):
            raise ValueError("W-2G canonical winnings, business receipts or withholding disagree with the reviewed source")
    return source


def compute_w2g_totals(*, tax_year: int, records: list) -> dict:
    if type(tax_year) is not int or tax_year not in _REVISIONS or not isinstance(records, list):
        raise ValueError("W-2G totals require a reviewed return year and source records")
    reported = casual = business = withheld = lottery = 0.0
    worksheet = False
    for rec in records:
        if not isinstance(rec, dict) or set(rec) != {"key_boxes", "w2g_income"}:
            raise ValueError("W-2G records need key_boxes and source inventory")
        source = validate_w2g_income(rec["w2g_income"], rec["key_boxes"])
        if source["tax_year"] != tax_year:
            raise ValueError("W-2G source year must match the return")
        reported += source["box_1"] + source["box_7"]
        lottery += source["ca_lottery_income"]
        casual += source["schedule_1_income"]
        business += source["schedule_c_gross"]
        withheld += source["box_4"]
        worksheet = worksheet or source["income_method"] == "reviewed_worksheet"
    return {GAMBLING_REPORTED: _money(reported, "W-2G reported winnings total"),
            "gambling_income": _money(casual, "W-2G Schedule1 contribution"),
            "gambling_business_gross": _money(business, "W-2G business contribution"),
            "w2g_withholding": _money(withheld, "W-2G withholding total"),
            "ca_lottery_income": _money(lottery, "W-2G California lottery income"),
            "worksheet_used": worksheet}


def compute_other_forms_withholding(*, tax_year: int, records: list,
                                    remaining_withholding, remaining_withholding_confirmed: bool) -> dict:
    if remaining_withholding_confirmed is not True:
        raise ValueError("confirm remaining line25c withholding EXCLUDING W-2Gs, W-2s and 1099s, including zero")
    totals = compute_w2g_totals(tax_year=tax_year, records=records)
    remaining = _money(remaining_withholding, "remaining other-form federal withholding")
    return {"remaining_withholding": remaining, "remaining_withholding_confirmed": True,
            "w2g_withholding": totals["w2g_withholding"],
            "total": _money(remaining + totals["w2g_withholding"], "Form1040 line25c"),
            "computed_tax_year": tax_year}
