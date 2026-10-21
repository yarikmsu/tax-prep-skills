"""California owner credits: FTB3510 Part II and FTB3804-CR.

Opening AMT credit is the reconciled qualified balance, not prior AMT paid.
PTET inputs are reported owner credits, never entity income or payments.
"""

import re

from agents.lib.tax_computations._shared import _amount


# TY2025 Schedule P credit-table structure, not dollar limits or tax rates.
_SECTIONS = {
    "A1": {"162"},
    "A2": {"223", "247", "205", "204", "246", "244", "237", "238", "234",
           "175", "224", "194", "190", "189", "191", "192", "193", "182",
           "203", "218", "207", "198", "160", "240", "241", "211", "200", "184",
           "174", "186", "171", "179", "178", "161"},
    "B1": {"173", "170", "163"},
    "B2": {"233", "197", "235", "172", "213", "239", "248", "183", "245",
           "243", "196", "176", "210", "185"},
    "B3": {"187"},
}


def _money(value, name):
    try:
        return round(_amount(value, name), 2)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{name} must be a finite nonnegative number") from None


def _identifier(value, name):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value) is None:
        raise ValueError(f"{name} must be an opaque local identifier")
    return value


def compute_ca_amt_credit(
    opening_credit: float, regular_tax: float, exemption_credits: float,
    tentative_minimum_tax: float, other_a_credits: float = 0,
    new_deferral_credit: float = 0,
) -> dict:
    """FTB3510 lines32–39, plus the next-year qualified opening balance.

    Line34 includes exemptions and OTHER allowed A1/A2 credits. B credits
    follow this credit and do not reduce its limit. No Box C screen applies.
    The opening balance must already reflect Part I's exclusion refigure.
    """
    opening = _money(opening_credit, "qualified opening CA AMT credit")
    tax = _money(regular_tax, "Form540 line31")
    exemptions = _money(exemption_credits, "Form540 line32")
    other = _money(other_a_credits, "other Schedule P A1/A2 credits")
    tmt = _money(tentative_minimum_tax, "Schedule P TMT")
    generated = _money(new_deferral_credit, "new deferral-attributable CA AMT")
    net = round(tax - exemptions - other, 2)
    limit = round(max(0, net - tmt), 2)
    allowed = min(opening, limit)
    unused = round(opening - allowed, 2)
    return {"line_32": opening, "line_33": tax, "line_34": round(exemptions + other, 2),
            "line_35": net, "line_36": tmt, "line_37": limit,
            "line_38": allowed, "line_39": unused,
            "ftb3510_credit_available": opening, "ftb3510_credit_allowable": allowed,
            "ftb3510_worksheet_confirmed": True, "iso_credit_generated": generated,
            "carryforward_to_next_year": round(unused + generated, 2)}


def validate_ca_ptet_carryovers(raw, tax_year: int) -> list[dict]:
    """Preserve origin years; a total alone cannot establish expiry."""
    if type(tax_year) is not int or not 2000 <= tax_year <= 2099:
        raise ValueError("CA PTET tax_year must be a supported calendar year")
    if not isinstance(raw, list):
        raise ValueError("CA PTET carryovers must be a vintage list")
    out, seen = [], set()
    for row in raw:
        if not isinstance(row, dict) or set(row) != {"credit_id", "origin_year", "amount"}:
            raise ValueError("CA PTET carryover requires credit_id, origin_year and amount")
        key = _identifier(row["credit_id"], "CA PTET credit_id")
        origin = row["origin_year"]
        if type(origin) is not int or not 2000 <= origin < tax_year:
            raise ValueError("CA PTET carryover origin_year must precede the return year")
        if key in seen:
            raise ValueError("duplicate CA PTET credit_id")
        seen.add(key)
        out.append({"credit_id": key, "origin_year": origin,
                    "amount": _money(row["amount"], "CA PTET carryover amount")})
    return sorted(out, key=lambda row: (row["origin_year"], row["credit_id"]))


def compute_ca_ptet_owner(
    *, tax_year: int, current_credits: list, carryovers: list,
    net_tax: float, carryforward_years: int | None, first_tax_year: int | None,
) -> dict:
    """FTB3804-CR from reported, eligibility-reviewed owner dollar amounts.

    The checked Form540 caller enforces its reviewed year and owner scope.
    Input dollars already reflect the reviewed separate-spouse allocation.
    Net tax is B4's regular-tax balance AFTER B3, not regular tax minus TMT.
    """
    lots = validate_ca_ptet_carryovers(carryovers, tax_year)
    if not isinstance(current_credits, list):
        raise ValueError("CA PTET current credits must be a list")
    _money(net_tax, "Schedule P B4 net tax")
    if not lots and not current_credits and carryforward_years is None:
        return {"line_1": 0.0, "line_2": 0.0, "line_3": 0.0, "line_4": 0.0,
                "line_5": 0.0, "reported_credit_total": 0.0, "current_credits": [],
                "vintages": [], "expired_before_this_year": [], "carryovers_to_next_year": []}
    if type(carryforward_years) is not int or not 0 < carryforward_years <= 100:
        raise ValueError("state.ptet_credit.carryforward_years must be fetched and positive")
    if type(first_tax_year) is not int or not 2000 <= first_tax_year <= tax_year:
        raise ValueError("state.ptet_credit.first_tax_year must be fetched for the credit regime")
    if any(row["origin_year"] < first_tax_year for row in lots):
        raise ValueError("CA PTET carryover predates the fetched credit regime")
    seen = {row["credit_id"] for row in lots}
    current = []
    for row in current_credits:
        if not isinstance(row, dict) or set(row) != {"credit_id", "source_id", "amount"}:
            raise ValueError("CA PTET current credit requires credit_id, source_id and amount")
        key = _identifier(row["credit_id"], "CA PTET credit_id")
        source = _identifier(row["source_id"], "CA PTET source_id")
        if key in seen:
            raise ValueError("duplicate CA PTET credit_id")
        seen.add(key)
        current.append({"credit_id": key, "source_id": source,
                        "amount": _money(row["amount"], "reported CA PTET credit")})
    expired = [row for row in lots if tax_year > row["origin_year"] + carryforward_years]
    eligible = [row for row in lots if tax_year <= row["origin_year"] + carryforward_years]
    opening = round(sum(row["amount"] for row in eligible), 2)
    reported = round(sum(row["amount"] for row in current), 2)
    available = round(opening + reported, 2)
    remaining_tax = _money(net_tax, "Schedule P B4 net tax")
    used = min(remaining_tax, available)
    balance = used
    workpaper, next_lots = [], []
    for row in eligible + [{"credit_id": r["credit_id"], "origin_year": tax_year,
                            "amount": r["amount"]} for r in current]:
        taken = min(row["amount"], balance)
        balance = round(balance - taken, 2)
        left = round(row["amount"] - taken, 2)
        # A last-year credit remains on this year's form, but expires before
        # next year's opening balance. Never silently refresh its vintage.
        expires = row["origin_year"] + carryforward_years == tax_year
        workpaper.append({**row, "used": taken, "unused": left,
                          "expires_after_this_year": expires})
        if left and not expires:
            next_lots.append({**row, "amount": left})
    return {"line_1": reported, "line_2": opening, "line_3": available,
            "line_4": used, "line_5": round(available - used, 2),
            "reported_credit_total": reported, "current_credits": current,
            "vintages": workpaper, "expired_before_this_year": expired,
            "carryovers_to_next_year": next_lots}


def compute_ca_credit_order(
    *, regular_tax: float, exemption_credits: float, tentative_minimum_tax: float,
    form540_line35: float,
    dependent_care: float, renters_credit: float, other_credits: list,
    opening_amt_credit: float, new_deferral_credit: float,
    tax_year: int, ptet_current: list, ptet_carryovers: list,
    ptet_carryforward_years: int | None, ptet_first_tax_year: int | None,
) -> dict:
    """TY2025 Schedule P A1→A2/188→B1/B2→B3→B4/242 ordering."""
    if tax_year != 2025 or type(tax_year) is not int:
        raise ValueError("CA credit line map has only been reviewed for TY2025")
    if not isinstance(other_credits, list):
        raise ValueError("ca_credits.other_credits must list the separately allowed credits")
    rows, seen = [], set()
    for row in other_credits:
        if not isinstance(row, dict) or set(row) != {"code", "amount"}:
            raise ValueError("other CA credit requires code and allowed amount")
        code = row["code"]
        if not isinstance(code, str) or code in seen:
            raise ValueError("CA credit codes must be distinct strings")
        section = next((s for s, codes in _SECTIONS.items() if code in codes), None)
        if section is None:
            raise ValueError("CA credit code requires separate reviewed support; do not enter 188 or 242 manually")
        seen.add(code)
        rows.append({"code": code, "section": section, "amount": _money(row["amount"], "allowed CA special credit")})
    care = _money(dependent_care, "Form540 line40")
    rent = _money(renters_credit, "Form540 line46")
    net = _money(form540_line35, "Form540 line35")
    tmt = _money(tentative_minimum_tax, "Schedule P TMT")
    a = round(care + sum(r["amount"] for r in rows if r["section"].startswith("A")), 2)
    if a > round(max(0, net - tmt), 2):
        raise ValueError("other allowed A1/A2 credits exceed Schedule P's regular-tax excess over TMT")
    amt = compute_ca_amt_credit(opening_amt_credit, regular_tax, exemption_credits, tmt, a, new_deferral_credit)
    b = round(rent + sum(r["amount"] for r in rows if r["section"].startswith("B")), 2)
    net = round(net - a - amt["line_38"], 2)
    if b > net:
        raise ValueError("other allowed B1/B2/B3 credits exceed remaining regular tax")
    net = round(net - b, 2)
    ptet = compute_ca_ptet_owner(tax_year=tax_year, current_credits=ptet_current,
        carryovers=ptet_carryovers, net_tax=net, carryforward_years=ptet_carryforward_years,
        first_tax_year=ptet_first_tax_year)
    ordered = sorted(rows, key=lambda r: list(_SECTIONS).index(r["section"]))
    ordered.append({"code": "188", "section": "A2", "amount": amt["line_38"]})
    ordered.append({"code": "242", "section": "B4", "amount": ptet["line_4"]})
    ordered.sort(key=lambda r: (list(_SECTIONS).index(r["section"]) if r["section"] != "B4" else 5,
                                r["code"] == "188"))
    positive = [r for r in ordered if r["amount"] > 0]
    lines = {"line_43": 0.0, "line_44": 0.0, "line_45": 0.0}
    if len(positive) > 2:
        lines["line_45"] = round(sum(r["amount"] for r in positive), 2)
    else:
        for line, row in zip(("line_43", "line_44"), positive):
            lines[line] = row["amount"]
    return {**lines, "credits": ordered, "ftb_3510": amt, "ftb_3804_cr": ptet,
            "regular_tax_remaining": round(net - ptet["line_4"], 2)}
