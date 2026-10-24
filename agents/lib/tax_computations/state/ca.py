#!/usr/bin/env python3
"""California — Form 540 and Schedule P (540), full-year resident.

The second member of `state/`, and the one the split was really about:
California is the priority state in AGENTS.md §4, and
its law had been sitting in the federal file since before there was a `state/`
package to put it in.

The original three blocks were never contiguous — excess SDI/VPDI next to the
federal excess-Social-Security credit it shares an allocator with, Schedule CA
itemized next to federal Schedule A, and Schedule P (540) next to Form 6251.
Each sat beside the FEDERAL form it mirrors rather than beside the other two,
which is exactly why the file read as one subject and was three.

Tax law comes from covered-year fetched parameters. Statutory-fixed amounts
are not exempt from fetching unless AGENTS.md §2 explicitly enumerates them.
"""

import math
import calendar
import datetime as dt


from agents.lib.tax_computations._shared import (
    _CHARITY_APPRECIATED_AGI_CAP,
    _amount,
    _coerce_numeric,
    _medical_deductible,
    _multi_employer_excess,
    _normalize_filing_status,
    _prorated_mortgage_interest,
    _round_half_up,
    _split_remainder_last,
    _validated_withholding_list,
)


_CA_540_REVIEWED_YEARS = {2025}
CA_INVESTMENT_COMPONENTS = (
    "us_obligation_interest", "municipal_interest", "exempt_interest_dividends",
    "fund_ordinary_dividends",
)
# Worksheet inputs, not derived lines. Names follow the reviewed printed form.
CA_540_COMPONENT_LINES = (12, 34, 40, 43, 44, 45, 46, 63, 71, 72, 73, 74,
                          75, 76, 77, 91, 92, 98, 110, 112, 113)


def _ca_input(value, name: str, *, fraction: bool = False) -> float:
    """Reject malformed facts without putting source values in an error."""
    try:
        number = _amount(value, name)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite nonnegative number") from None
    if fraction and number > 1:
        raise ValueError(f"{name} must be a fraction in [0, 1]")
    return number if fraction else round(number, 2)


def validate_ca_investments(raw, boxes: dict) -> dict:
    """Per-document intake contract, shared by parse-time and both gates."""
    from agents.lib.capabilities import _USPS_STATES, _TERRITORIES
    if not isinstance(boxes, dict):
        raise ValueError("CA investment key_boxes must be an object")
    keys = {"tax_year", "reviewed", "municipal_interest", "funds"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError("ca_investments requires tax_year, reviewed, municipal_interest and funds")
    year = raw["tax_year"]
    if type(year) is not int or not 2000 <= year <= 2099 or raw["reviewed"] is not True:
        raise ValueError("ca_investments needs the source tax year and an explicit reviewed true")
    if not isinstance(raw["municipal_interest"], list) or not isinstance(raw["funds"], list):
        raise ValueError("ca_investments municipal_interest and funds must be lists")
    values = {k: _ca_input(boxes.get(k), k) for k in CA_INVESTMENT_COMPONENTS}
    muni: list[dict] = []
    funds: list[dict] = []
    ids: set[str] = set()
    for row in raw["municipal_interest"]:
        if not isinstance(row, dict):
            raise ValueError("municipal interest rows must be objects")
        state = row.get("issuer_state")
        if not isinstance(state, str) or state not in _USPS_STATES:
            raise ValueError("municipal interest needs a valid uppercase issuer_state")
        special = state in _TERRITORIES or state == "DC"
        required = {"issuer_state", "amount"} | ({"ca_exempt", "exemption_confirmed"} if special else set())
        if set(row) != required:
            raise ValueError("municipal interest needs its issuer and amount; DC/territories also need a confirmed exemption decision")
        if special and (type(row["ca_exempt"]) is not bool or row["exemption_confirmed"] is not True):
            raise ValueError("confirm the special issuer's CA exemption from its source worksheet")
        muni.append({"issuer_state": state, "amount": _ca_input(row["amount"], "municipal interest"),
                     **({"ca_exempt": row["ca_exempt"], "exemption_confirmed": True} if special else {})})
    fund_keys = {"fund_id", "is_us_ric", "ordinary_dividends", "exempt_interest_dividends",
                 "us_obligation_fraction", "ca_exempt_fraction", "asset_test"}
    for row in raw["funds"]:
        if not isinstance(row, dict) or set(row) != fund_keys:
            raise ValueError("fund rows require their ID, both dividends, both income fractions and asset_test")
        identifier = row["fund_id"]
        if (not isinstance(identifier, str) or not identifier.strip() or identifier != identifier.strip()
                or identifier in ids):
            raise ValueError("fund_id must be nonempty and unique within its document")
        ids.add(identifier)
        is_ric = row["is_us_ric"]
        if type(is_ric) is not bool:
            raise ValueError("fund is_us_ric must be an explicit source-confirmed boolean")
        test = row["asset_test"]
        if not is_ric and test is not None:
            raise ValueError("a non-RIC fund has no passthrough asset test; record explicit null")
        if is_ric and not isinstance(test, dict):
            raise ValueError("fund asset_test must be an object")
        if not is_ric:
            test = None
        elif set(test) == {"quarterly_fractions"}:
            quarters = test["quarterly_fractions"]
            if not isinstance(quarters, list) or len(quarters) != 4:
                raise ValueError("fund asset_test requires all four quarter-end fractions")
            test = {"quarterly_fractions": [_ca_input(q, "quarter-end assets", fraction=True) for q in quarters]}
        elif set(test) == {"provider_qualified", "threshold"}:
            if type(test["provider_qualified"]) is not bool:
                raise ValueError("fund provider qualification must be an explicit boolean")
            threshold = _ca_input(test["threshold"], "provider asset threshold", fraction=True)
            if threshold <= 0:
                raise ValueError("provider asset threshold must be positive")
            test = {"provider_qualified": test["provider_qualified"], "threshold": threshold}
        else:
            raise ValueError("fund asset_test needs four quarter-end fractions or an explicit provider certification and threshold")
        funds.append({"fund_id": identifier, "is_us_ric": is_ric,
            "ordinary_dividends": _ca_input(row["ordinary_dividends"], "fund ordinary dividends"),
            "exempt_interest_dividends": _ca_input(row["exempt_interest_dividends"], "fund exempt-interest dividends"),
            "us_obligation_fraction": _ca_input(row["us_obligation_fraction"], "fund US income fraction", fraction=True),
            "ca_exempt_fraction": _ca_input(row["ca_exempt_fraction"], "fund CA-exempt income fraction", fraction=True),
            "asset_test": test})
    expected = {
        "municipal_interest": sum(r["amount"] for r in muni),
        "exempt_interest_dividends": sum(r["exempt_interest_dividends"] for r in funds),
        "fund_ordinary_dividends": sum(r["ordinary_dividends"] for r in funds),
    }
    for key, total in expected.items():
        if not math.isclose(total, values[key], rel_tol=0, abs_tol=.005):
            raise ValueError(f"ca_investments does not reconcile to {key}; re-read the source")
    taxable = _ca_input(boxes.get("taxable_interest", 0), "inclusive taxable interest")
    exempt = _ca_input(boxes.get("tax_exempt_interest", 0), "inclusive exempt interest")
    ordinary = _ca_input(boxes.get("ordinary_dividends", 0), "inclusive ordinary dividends")
    if values["us_obligation_interest"] > taxable or values["fund_ordinary_dividends"] > ordinary:
        raise ValueError("CA investment components cannot exceed their inclusive federal totals")
    if not math.isclose(values["municipal_interest"] + values["exempt_interest_dividends"], exempt,
                        rel_tol=0, abs_tol=.005):
        raise ValueError("municipal interest plus DIV box12 must reconcile to inclusive tax_exempt_interest")
    return {"tax_year": year, "reviewed": True, "municipal_interest": muni, "funds": funds}


def compute_ca_schedule_ca(
    *, tax_year: int, investment_records: list, fund_asset_minimum,
    taxable_social_security: float, taxable_state_refund: float,
    taxable_unemployment: float, taxable_ca_family_leave: float, ca_family_line_7: float,
    ca_lottery_winnings: float,
    hsa_employer_addback: float, hsa_deduction_addback: float,
    hsa_interest_addback: float, hsa_dividend_addback: float,
    other_additions: float, other_subtractions: float,
) -> dict:
    """TY2025 ScheduleCA Part I lines2/6 and SectionB line1, without defaults."""
    if type(tax_year) is not int or tax_year not in _CA_540_REVIEWED_YEARS:
        raise ValueError("Schedule CA form year has not been reviewed")
    if not isinstance(investment_records, list):
        raise ValueError("CA investment records must be a list")
    us = muni_add = fund_sub = fund_add = 0.0
    raw_us = 0.0
    workpaper, municipal_workpaper = [], []
    source_notes = set()
    for record in investment_records:
        if not isinstance(record, dict) or set(record) != {"key_boxes", "ca_investments"}:
            raise ValueError("CA investment records need their key_boxes and ca_investments")
        boxes = record["key_boxes"]
        if not isinstance(boxes, dict):
            raise ValueError("CA investment key_boxes must be an object")
        detail = validate_ca_investments(record["ca_investments"], boxes)
        if detail["tax_year"] != tax_year:
            raise ValueError("CA investment source year does not match the return")
        us += _ca_input(boxes["us_obligation_interest"], "US interest")
        for row in detail["municipal_interest"]:
            exempt = row.get("ca_exempt", row["issuer_state"] == "CA")
            if not exempt:
                muni_add += row["amount"]
            municipal_workpaper.append({"issuer_state": row["issuer_state"], "amount": row["amount"],
                                       "ca_exempt": exempt, "source_worksheet": "exemption_confirmed" in row})
            if "exemption_confirmed" in row:
                source_notes.add("Special-issuer exemption decisions use separately confirmed source worksheets.")
        for row in detail["funds"]:
            test = row["asset_test"]
            method = "non_ric"
            if not row["is_us_ric"]:
                qualified = False
            else:
                threshold = _ca_input(fund_asset_minimum, "state.schedule_ca.fund_asset_minimum", fraction=True)
                if threshold <= 0:
                    raise ValueError("state.schedule_ca.fund_asset_minimum must be positive")
            if row["is_us_ric"] and "quarterly_fractions" in test:
                method = "quarterly_assets"
                qualified = all(q >= threshold for q in test["quarterly_fractions"])
            elif row["is_us_ric"]:
                method = "provider_certification"
                if test["threshold"] != threshold:
                    raise ValueError("fund provider certification does not match the fetched asset threshold")
                qualified = test["provider_qualified"]
                source_notes.add("Some fund qualifications use annual provider certifications.")
            eligible_us = row["ordinary_dividends"] * row["us_obligation_fraction"]
            raw_us += eligible_us
            subtraction = round(eligible_us, 2) if qualified else 0.0
            addition = round(row["exempt_interest_dividends"] *
                             (1 - row["ca_exempt_fraction"] if qualified else 1), 2)
            fund_sub += subtraction
            fund_add += addition
            workpaper.append({"fund_id": row["fund_id"], "qualified": qualified, "qualification_method": method,
                              "us_subtraction": subtraction, "municipal_addition": addition})
    out: dict = {"hsa_employer_addback": _ca_input(hsa_employer_addback, "HSA employer addback"),
           "hsa_deduction_addback": _ca_input(hsa_deduction_addback, "HSA deduction addback"),
           "hsa_interest_addback": _ca_input(hsa_interest_addback, "HSA interest addback"),
           "hsa_dividend_addback": _ca_input(hsa_dividend_addback, "HSA dividend addback"),
           "us_obligation_interest_subtraction": round(us, 2),
           "us_obligation_fund_subtraction": round(fund_sub, 2),
           "nonca_municipal_interest_addition": round(muni_add, 2),
           "exempt_interest_dividend_addition": round(fund_add, 2),
           "taxable_social_security_subtraction": _ca_input(taxable_social_security, "taxable Social Security"),
           "taxable_state_refund_subtraction": _ca_input(taxable_state_refund, "taxable state refund"),
           "unemployment_subtraction": _ca_input(taxable_unemployment, "federally taxable unemployment"),
           "paid_family_leave_subtraction": _ca_input(taxable_ca_family_leave, "federally taxable CA-exempt family benefits"),
           # Section B line 8b, column B: California lottery winnings in federal income.
           "ca_lottery_subtraction": _ca_input(ca_lottery_winnings, "federally included California lottery winnings"),
           "other_additions": _ca_input(other_additions, "other CA additions"),
           "other_subtractions": _ca_input(other_subtractions, "other CA subtractions"),
           "investment_workpaper": workpaper,
           "municipal_workpaper": municipal_workpaper,
           "source_note": ("CA investment worksheets: " + " ".join(sorted(source_notes))) if source_notes else None,
           "has_us_obligation_income": us + raw_us > 0,
           "has_nonca_muni_interest": muni_add + fund_add > 0}
    out["line_2_column_b"] = round(us + fund_sub, 2)
    # Line 2 column C also takes "interest or other earnings earned from an
    # HSA"; Section C line 13 column B adds back the federal HSA deduction.
    out["line_2_column_c"] = round(muni_add + fund_add + out["hsa_interest_addback"]
                                   + out["hsa_dividend_addback"], 2)
    family7 = _ca_input(ca_family_line_7, "CA-exempt family income on Schedule1 line7")
    if family7 > out["paid_family_leave_subtraction"]:
        raise ValueError("CA family line7 subtraction cannot exceed total exempt family income")
    out["section_b_line_7_column_b"] = round(out["unemployment_subtraction"] + family7, 2)
    out["section_b_other_income_subtraction"] = round(out["paid_family_leave_subtraction"] - family7, 2)
    out["total_additions"] = round(sum(out[k] for k in ("hsa_employer_addback", "hsa_deduction_addback",
        "other_additions")) + out["line_2_column_c"], 2)
    out["total_subtractions"] = round(sum(out[k] for k in ("taxable_social_security_subtraction",
        "taxable_state_refund_subtraction", "other_subtractions", "unemployment_subtraction",
        "paid_family_leave_subtraction", "ca_lottery_subtraction")) + out["line_2_column_b"], 2)
    for key in ("line_2_column_b", "line_2_column_c", "total_additions", "total_subtractions"):
        _ca_input(out[key], key)  # a finite record must not overflow in aggregate
    return out


def _ca_5805_date(value, name: str) -> dt.date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a canonical ISO date")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a canonical ISO date") from None
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be a canonical ISO date")
    return parsed


def _ca_5805_scalar(value, name: str, *, signed: bool = False) -> float:
    try:
        return _amount(value, name, allow_negative=signed)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite number in its allowed domain") from None


def validate_ca_underpayment_rates(rates) -> list[dict]:
    """Half-years expire independently; an absent successor is not carryable."""
    if not isinstance(rates, list):
        raise ValueError("state.underpayment_rates must be a list")
    checked: list[dict] = []
    for row in rates:
        if not isinstance(row, dict) or set(row) != {"effective_from", "annual_rate"}:
            raise ValueError("CA underpayment rates require effective_from and annual_rate")
        start = _ca_5805_date(row["effective_from"], "CA rate effective_from")
        if start.day != 1 or start.month not in (1, 7):
            raise ValueError("CA rates must begin on January1 or July1")
        if checked and start <= checked[-1]["start"]:
            raise ValueError("CA rate half-years must be ordered without duplicates")
        end = dt.date(start.year, 6, 30) if start.month == 1 else dt.date(start.year, 12, 31)
        checked.append({"start": start, "end": end,
            "rate": _ca_input(row["annual_rate"], "CA annual_rate", fraction=True)})
    return checked


def validate_ca_installment_shares(shares) -> list:
    if not isinstance(shares, list):
        raise ValueError("CA installment_shares must be four finite fractions summing to one")
    try:
        valid = (isinstance(shares, list) and len(shares) == 4 and all(
            type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in shares)
            and math.isclose(sum(shares), 1, abs_tol=1e-9))
    except (TypeError, OverflowError):
        valid = False
    if not valid:
        raise ValueError("CA installment_shares must be four finite fractions summing to one")
    return shares


def _ca_5805_events(records, name: str) -> list[dict]:
    if not isinstance(records, list):
        raise ValueError(f"{name} must be a reviewed payment list")
    events: list[dict] = []
    ids = set()
    for row in records:
        if not isinstance(row, dict) or set(row) != {"payment_id", "date", "amount"}:
            raise ValueError(f"{name} requires payment_id, date and amount")
        identity = row["payment_id"]
        if (not isinstance(identity, str) or not identity.strip()
                or identity != identity.strip() or identity in ids):
            raise ValueError(f"{name} payment identifiers must be nonempty and unique")
        ids.add(identity)
        events.append({"date": _ca_5805_date(row["date"], name + " date"),
                       "amount": _ca_input(row["amount"], name + " amount")})
    _ca_5805_scalar(sum(p["amount"] for p in events), name + " aggregate")
    return events


def _ca_5805_allocate(amount: float, shares: list) -> list[float]:
    return _split_remainder_last(amount, shares)


def _ca_5805_intervals(required: float, shares: list, dues: list, end: dt.date,
                       withholding: list, payments: list, opening: float) -> tuple[list, list]:
    """Apply credits oldest-first; due-day payments accrue no penalty."""
    requirements = _ca_5805_allocate(required, shares)
    cash: dict[dt.date, float] = {dues[0]: opening}
    for item in payments + withholding:
        date = item["date"]
        if date <= end:
            cash[date] = cash.get(date, 0) + item["amount"]
            _ca_input(cash[date], "CA same-date payment total")
    dates = sorted(set(dues + [end] + list(cash)))
    balances, intervals = [0.0] * 4, []
    available, previous = 0.0, dates[0]
    for date in dates:
        if date > previous:
            for i, principal in enumerate(balances):
                if principal > 0:
                    intervals.append({"installment": i + 1, "principal": principal,
                        "start": previous + dt.timedelta(days=1), "end": date})
        for i, due in enumerate(dues):
            if date == due:
                balances[i] = round(balances[i] + requirements[i], 2)
        available = _ca_input(available + cash.get(date, 0), "CA available payment total")
        for i, balance in enumerate(balances):
            used = min(balance, available)
            balances[i] = round(balance - used, 2)
            available = round(available - used, 2)
        previous = date
    return intervals, requirements


def _ca_5805_price(intervals: list, rates: list, waive_fourth: bool) -> tuple[list, float]:
    details, total = [], 0.0
    for interval in intervals:
        if waive_fourth and interval["installment"] == 4:
            continue
        cursor = interval["start"]
        while cursor <= interval["end"]:
            row = next((r for r in rates if r["start"] <= cursor <= r["end"]), None)
            if row is None:
                raise ValueError("CA underpayment rate coverage is incomplete; fetch every half-year actually priced")
            stop = min(interval["end"], row["end"], dt.date(cursor.year, 12, 31))
            days = (stop - cursor).days + 1
            denominator = 366 if calendar.isleap(cursor.year) else 365
            charge = interval["principal"] * row["rate"] * days / denominator
            if not math.isfinite(charge) or not math.isfinite(total + charge):
                raise ValueError("CA penalty calculation overflowed")
            total += charge
            details.append({"installment": interval["installment"], "principal": interval["principal"],
                "from": cursor.isoformat(), "through": stop.isoformat(), "days": days,
                "annual_rate": row["rate"], "year_days": denominator, "penalty": round(charge, 2)})
            cursor = stop + dt.timedelta(days=1)
    return details, round(total, 2)


def compute_ca_5805(*, tax_year: int, filing_status: str, state_law: dict,
                    current_tax, current_agi, withholding, prior_tax, prior_agi,
                    prior_withholding, prior_full_year, payments: list, opening_credit,
                    withholding_method: str, actual_withholding: list,
                    regular_method_confirmed: bool, early_return: dict | None = None,
                    prior_return_filed=None) -> dict:
    """FTB5805 regular method, from reviewed tax bases and fetched CA law.

    Payments include separately identified return-balance payments for timing;
    those do not become Form540 line72 estimated payments. The caller owns the
    Form540/PartII tax-base derivation and the explicit eligibility decisions.
    """
    status = _normalize_filing_status(filing_status).lower()
    if type(tax_year) is not int or tax_year not in _CA_540_REVIEWED_YEARS:
        raise ValueError("FTB5805 form year has not been reviewed")
    if regular_method_confirmed is not True:
        raise ValueError("confirm the regular method; annualized, farmer, waiver and special-deadline cases need their own worksheet")
    if not isinstance(state_law, dict) or state_law.get("code") != "CA":
        raise ValueError("FTB5805 requires California law")
    screen, policy = state_law.get("underpayment_screen"), state_law.get("estimated_tax_safe_harbor")
    if not isinstance(screen, dict) or not isinstance(policy, dict):
        raise ValueError("fetch CA underpayment_screen and estimated_tax_safe_harbor")
    minimum = _ca_input((screen.get("minimum_balance") or {}).get(status), "CA minimum_balance")
    # Part II line 2's share is its own fetched figure: underpayment_screen.
    # tax_fraction is Form 540 line 113's 10% screen, and reusing it would
    # price the required payment at a tenth of the tax.
    fraction = _ca_input(policy.get("current_year_share"), "CA estimated_tax_safe_harbor.current_year_share", fraction=True)
    low = _ca_input(policy.get("prior_year_share"), "CA prior_year_share", fraction=True)
    high = _ca_5805_scalar(policy.get("prior_year_share_high_income"), "CA prior_year_share_high_income")
    threshold = _ca_input((policy.get("prior_agi_threshold") or {}).get(status), "CA prior_agi_threshold")
    override = _ca_input((policy.get("current_agi_override") or {}).get(status), "CA current_agi_override")
    if not minimum or not fraction or not low or high < low or not threshold or not override:
        raise ValueError("CA safe-harbor law must be fetched and internally ordered")
    shares = validate_ca_installment_shares(policy.get("installment_shares"))
    raw_dues = policy.get("due_dates")
    if not isinstance(raw_dues, list) or len(raw_dues) != 4:
        raise ValueError("fetch four CA installment due_dates")
    dues = [_ca_5805_date(v, "CA due_dates") for v in raw_dues]
    end = _ca_5805_date(policy.get("penalty_end"), "CA penalty_end")
    cutoff = _ca_5805_date(policy.get("early_filing_deadline"), "CA early_filing_deadline")
    if (any(a >= b for a, b in zip(dues, dues[1:])) or any(d.year != tax_year for d in dues[:3])
            or dues[3].year != tax_year + 1 or end.year != tax_year + 1
            or not dues[3] < cutoff < end):
        raise ValueError("CA installment and cutoff dates must match the covered calendar year")
    current = _ca_input(current_tax, "CA current-year PartII tax")
    agi = _ca_5805_scalar(current_agi, "CA current-year AGI", signed=True)
    withheld = _ca_input(withholding, "CA withholding lines71+73")
    opening = _ca_input(opening_credit, "CA opening applied credit")
    pay = _ca_5805_events(payments, "CA payments")
    held: list[dict]
    if withholding_method == "deemed":
        if actual_withholding != []:
            raise ValueError("deemed withholding must not also carry actual-date credits")
        held = [{"date": d, "amount": a} for d, a in zip(dues, _ca_5805_allocate(withheld, shares))]
    elif withholding_method == "actual":
        held = _ca_5805_events(actual_withholding, "CA actual withholding")
        if round(sum(p["amount"] for p in held), 2) != withheld:
            raise ValueError("actual-date withholding must reconcile to Form540 lines71+73")
    else:
        raise ValueError("confirm deemed or actual-date CA withholding")
    early = early_return is not None
    if early:
        if not isinstance(early_return, dict) or set(early_return) != {"filed_date", "fully_paid_date", "verified"} or early_return["verified"] is not True:
            raise ValueError("confirm the early return's filing and full-payment facts")
        filed = _ca_5805_date(early_return["filed_date"], "CA early filed_date")
        paid = _ca_5805_date(early_return["fully_paid_date"], "CA early fully_paid_date")
        if filed > cutoff or paid > cutoff or filed.year != tax_year + 1 or paid.year != tax_year + 1:
            raise ValueError("early-return exception is outside the fetched filing/payment deadline")
        if opening + withheld + sum(p["amount"] for p in pay if p["date"] <= paid) < current:
            raise ValueError("early-return payment records do not cover even the PartII tax; reconcile the full return payment")
    def output(required, reason, intervals, requirements):
        checked_rates = validate_ca_underpayment_rates(state_law.get("underpayment_rates", []))
        details, penalty = _ca_5805_price(intervals, checked_rates, early)
        return {"computed_tax_year": tax_year, "method": "regular", "basis": reason,
            "current_tax": current, "current_agi": round(agi, 2), "withholding": withheld,
            "opening_credit": opening, "withholding_method": withholding_method,
            "required_annual_payment": required,
            "required_installments": requirements if required is not None else None,
            "tested_current_year_installments": requirements if required is None else None,
            "installment_due_dates": [d.isoformat() for d in dues],
            "penalty_end": end.isoformat(),
            "dated_payments": [{"date": p["date"].isoformat(), "amount": p["amount"]} for p in pay],
            "fourth_installment_exception": early, "penalty_details": details, "total": penalty,
            "notes": ["FTB5805 regular method: the total uses unrounded daily charges; displayed detail rows are rounded."]}
    if current - withheld < minimum:
        return output(0.0, "current_year_floor", [], [0.0] * 4)
    upper = round(current * fraction, 2)
    upper_intervals, upper_requirements = _ca_5805_intervals(upper, shares, dues, end, held, pay, opening)
    if not any(not early or row["installment"] != 4 for row in upper_intervals):
        return output(None, "current_year_requirement_sufficient", [], upper_requirements)
    # The IMPORTANT box exempts a below-floor liability in EITHER year.
    # That fact is independent of whether a full prior return supports the
    # prior-year annual-payment prong. Do not guess a positive penalty from
    # an unfiled/short return while its floor remains unknown.
    prior = _ca_input(prior_tax, "prior-year CA PartII tax")
    if prior < minimum:
        return output(0.0, "prior_year_floor", [], [0.0] * 4)
    previous_withheld = _ca_input(prior_withholding, "prior-year CA withholding")
    if prior - previous_withheld < minimum:
        return output(0.0, "prior_year_floor", [], [0.0] * 4)
    if type(prior_return_filed) is not bool:
        raise ValueError("confirm whether a prior California return was filed")
    if prior_return_filed and type(prior_full_year) is not bool:
        raise ValueError("confirm the prior return's full-year status")
    required, reason = upper, "current_year"
    if prior_return_filed and prior_full_year and agi < override:
        previous_agi = _ca_5805_scalar(prior_agi, "prior-year CA AGI", signed=True)
        prior_payment = _ca_5805_scalar(prior * (high if previous_agi > threshold else low), "CA prior-year annual payment")
        required = min(upper, round(prior_payment, 2))
        reason = "lower_of_current_and_prior_year"
    intervals, requirements = _ca_5805_intervals(required, shares, dues, end, held, pay, opening)
    return output(required, reason, intervals, requirements)


def settle_ca_5805(form: dict, workpaper: dict) -> dict:
    """Settle the checked penalty before publishing either balance alias."""
    penalty = _ca_input(workpaper.get("total"), "FTB5805 total")
    settlement = _ca_5805_scalar(sum(form[f"line_{n}"] for n in (100, 94, 96, 110, 112))
        + penalty - form["line_99"], "CA final settlement", signed=True)
    return {"line_113": penalty, "line_114": max(0.0, round(settlement, 2)),
        "line_115": max(0.0, round(-settlement, 2)),
        "ca_amount_owed": max(0.0, round(settlement, 2)),
        "ca_refund": max(0.0, round(-settlement, 2))}


def validate_ca_return_law(state: dict) -> None:
    """Required fetched blocks for the checked Form 540, not opt-in ISO law."""
    from agents.lib.tax_computations.state.planning import validate_ca_tax_rows
    if (not isinstance(state, dict) or state.get("code") != "CA"
            or state.get("type") != "graduated_income"):
        raise ValueError("state must be the covered-year CA law block")
    table = state.get("tax_table")
    if not isinstance(table, dict):
        raise ValueError("state.tax_table is required for every CA return")
    if _amount(table.get("max_taxable_income"), "state.tax_table.max_taxable_income") <= 0:
        raise ValueError("state.tax_table.max_taxable_income must be positive")
    validate_ca_tax_rows(table.get("rows"))
    amt = state.get("amt")
    screen = state.get("underpayment_screen")
    for block, fields, statuses in (
        (amt, ("exemption", "phaseout_start"), ("single", "mfj", "mfs", "hoh")),
        (screen, ("minimum_balance",), ("single", "mfj", "mfs", "hoh", "qss")),
    ):
        name = "state.amt" if block is amt else "state.underpayment_screen"
        if not isinstance(block, dict):
            raise ValueError(f"{name} is required for every CA return")
        for field in fields:
            values = block.get(field)
            if not isinstance(values, dict):
                raise ValueError(f"{name}.{field} is unfetched")
            for status in statuses:
                if _amount(values.get(status), f"{name}.{field}.{status}") <= 0:
                    raise ValueError(f"{name}.{field}.{status} must be positive")
    if not isinstance(screen, dict):
        raise ValueError("state.underpayment_screen is required")
    assert isinstance(amt, dict)  # validated with its per-status fields above
    for field in ("tmt_rate", "phaseout_rate"):
        if not 0 < _ca_input(amt.get(field), f"state.amt.{field}", fraction=True) <= 1:
            raise ValueError(f"state.amt.{field} must be fetched and positive")
    fraction = _amount(screen.get("tax_fraction"), "state.underpayment_screen.tax_fraction")
    if not 0 < fraction <= 1:
        raise ValueError("state.underpayment_screen.tax_fraction must be in (0, 1]")
    schedule = state.get("schedule_ca")
    if not isinstance(schedule, dict) or _ca_input(schedule.get("fund_asset_minimum"),
            "state.schedule_ca.fund_asset_minimum", fraction=True) <= 0:
        raise ValueError("state.schedule_ca.fund_asset_minimum must be fetched and in (0, 1]")

# SB 951 removed the CA SDI taxable wage ceiling starting TY2024 — from that
# year there is no per-person maximum and therefore no excess-SDI credit.
_SDI_NO_CEILING_FROM = 2024


def validate_ca_exemption_phaseout(value) -> None:
    """Require the fetched Form 540 line-32 worksheet for every status."""
    prefix = "state.exemption_phaseout"
    if not isinstance(value, dict):
        raise ValueError(f"{prefix} is unfetched or malformed — fetch via /tax-setup")
    for field in ("threshold", "band"):
        entries = value.get(field)
        if not isinstance(entries, dict):
            raise ValueError(f"{prefix}.{field} is unfetched — fetch via /tax-setup")
        for status in ("single", "mfj", "mfs", "hoh", "qss"):
            name = f"{prefix}.{field}.{status}"
            amount = _amount(entries.get(status), name)
            if not amount.is_integer():
                raise ValueError(f"{name} must use whole dollars")
            if field == "band" and amount <= 0:
                raise ValueError(f"{name} must be positive")
    reduction = _amount(value.get("reduction_per_step"), f"{prefix}.reduction_per_step")
    if not reduction.is_integer():
        raise ValueError(f"{prefix}.reduction_per_step must use whole dollars")
    if reduction <= 0:
        raise ValueError(f"{prefix}.reduction_per_step must be positive")


def compute_ca_exemption_credit(
    federal_agi: float,
    filing_status: str,
    personal_blind_senior_count: int,
    dependent_count: int,
    personal_exemption: float,
    dependent_exemption: float,
    exemption_phaseout: dict,
) -> dict:
    """Form 540 line 32, including its AGI Limitation Worksheet.

    Pass FEDERAL AGI (Form 540 line 13), the eligible box counts from lines
    7-9 and 10, and fetched exemption amounts and phase-out parameters. The
    personal/blind/senior and dependent groups each floor at zero separately.
    The worksheet uses whole-dollar AGI, rounded half up before counting steps.
    This worksheet applies above the Schedule P screening ceiling too; it
    neither screens nor computes AMT. No annual figure has an engine default.
    """
    status = _normalize_filing_status(filing_status).lower()
    agi = float(_round_half_up(_amount(federal_agi, "federal_agi", allow_negative=True)))
    for name, count in (("personal_blind_senior_count", personal_blind_senior_count),
                        ("dependent_count", dependent_count)):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    personal = _amount(personal_exemption, "CA personal exemption")
    dependent = _amount(dependent_exemption, "CA dependent exemption")
    validate_ca_exemption_phaseout(exemption_phaseout)
    threshold = exemption_phaseout["threshold"][status]
    band = exemption_phaseout["band"][status]
    steps = math.ceil(max(0.0, agi - threshold) / band)
    reduction = steps * exemption_phaseout["reduction_per_step"]
    personal_credit = round(personal_blind_senior_count * max(0.0, personal - reduction), 2)
    dependent_credit = round(dependent_count * max(0.0, dependent - reduction), 2)
    return dict(exemption_credit=round(personal_credit + dependent_credit, 2),
                personal_blind_senior_credit=personal_credit,
                dependent_credit=dependent_credit, phaseout_steps=steps,
                reduction_per_head=round(reduction, 2))


def compute_excess_sdi(
    sdi_by_employer: list,
    sdi_wages_by_employer: list,
    sdi_rate: float,
    sdi_wage_limit: float | None,
    tax_year: int,
) -> dict:
    """Historical CA excess-SDI/VPDI credit — per person, like the
    federal excess-SS credit.

    TY2025 Form 540 line 74 is the refundable Motion Picture credit. This
    historical calculation must never populate that line on a TY2025 return.

    The tax year DISAMBIGUATES a None wage limit: for TY2024+ (SB 951) None
    is CORRECT — no ceiling exists, so the credit is not applicable. For
    pre-2024 years a ceiling DID exist, so a None limit means the cache is
    unfetched and the function HARD-ERRORS instead of silently reporting $0
    for a refundable credit the taxpayer is owed (fail closed, not fail-$0).

    Args:
        sdi_by_employer: One SDI/VPDI figure (W-2 Box 14) per employer for ONE
            person.
        sdi_wages_by_employer: The matching CA wages subject to SDI per
            employer, parallel to sdi_by_employer (lengths must match).
        sdi_rate: CA SDI rate (tax_law state.sdi_rate).
        sdi_wage_limit: SDI taxable wage ceiling (tax_law state.sdi_wage_limit);
            None is valid ONLY for tax_year >= 2024.
        tax_year: The return's tax year (4-digit int).

    Per EDD DE 1964, the amount any individual employer withheld beyond what
    that employer's SDI wages support (min(wages, limit) × rate) is recovered
    from that employer, not via the excess-SDI claim — the same wage-aware
    per-employer clamp as the federal excess-SS credit applies (erroneous
    over-withholding on below-limit wages is never creditable).

    Returns:
        Dict with max_sdi, total_withheld, excess_credit, not_applicable,
        employer_error_amount + employer_over_withheld.
    """
    if isinstance(tax_year, bool) or not isinstance(tax_year, int) \
            or not (2000 <= tax_year <= 2099):
        raise ValueError(f"tax_year must be a 4-digit year integer, got {tax_year!r}")
    amounts = _validated_withholding_list(sdi_by_employer, "sdi_by_employer")
    wages = _validated_withholding_list(sdi_wages_by_employer, "sdi_wages_by_employer")
    if len(wages) != len(amounts):
        raise ValueError(
            f"sdi_wages_by_employer must parallel sdi_by_employer "
            f"({len(amounts)} employers, got {len(wages)} wage figures)."
        )
    total = round(sum(amounts), 2)
    try:
        rate = _coerce_numeric(sdi_rate, default=None)
    except TypeError:
        raise ValueError("sdi_rate must be numeric, got bool")
    if rate is None or not math.isfinite(rate) or rate <= 0:
        raise ValueError("sdi_rate must be a finite positive number")

    # The YEAR decides applicability, not the cache: for TY2024+ SB 951 means
    # no ceiling regardless of what the cache carries — a stale pre-2024
    # sdi_wage_limit left in a newer cache must not invent a line 74 credit.
    if tax_year >= _SDI_NO_CEILING_FROM:
        # No ceiling ≠ no employer errors: each employer should still withhold
        # exactly wages × rate, and anything above that is recovered from the
        # employer — hiding it here would contradict the guidance in the note.
        employer_error = round(
            sum(max(0.0, a - wg * rate) for a, wg in zip(amounts, wages)), 2)
        note = ("No SDI wage ceiling (SB 951, TY2024+) — no excess-SDI "
                "credit exists; an employer that over-withheld must "
                "refund it directly.")
        if sdi_wage_limit is not None:
            note += (" A stale state.sdi_wage_limit in the tax_law cache was "
                     "IGNORED — computing against it would invent a credit; "
                     "clear the field for TY2024+.")
        return {
            "max_sdi": None, "total_withheld": total, "num_employers": len(amounts),
            "excess_credit": 0.0, "employer_error_amount": employer_error,
            "employer_over_withheld": employer_error > 0,
            "not_applicable": True,
            "note": note,
        }
    if sdi_wage_limit is None:
        raise ValueError(
            f"TY{tax_year} had an SDI wage ceiling, but state.sdi_wage_limit "
            "is missing from tax_law — fetch it. Treating a missing pre-2024 "
            "cap as 'no ceiling' would silently drop a refundable credit."
        )
    try:
        limit = _coerce_numeric(sdi_wage_limit, default=None)
    except TypeError:
        raise ValueError("sdi_wage_limit must be numeric, got bool")
    if limit is None or not math.isfinite(limit) or limit <= 0:
        raise ValueError("sdi_wage_limit must be a finite positive number")

    max_sdi = round(rate * limit, 2)
    credit, employer_error = _multi_employer_excess(amounts, wages, rate, limit)
    return {
        "max_sdi": max_sdi, "total_withheld": total, "num_employers": len(amounts),
        "excess_credit": credit,
        "employer_error_amount": employer_error,
        "employer_over_withheld": employer_error > 0,
        "not_applicable": False,
    }


# CA Schedule CA: mortgage limit stays $1M (+$100K equity, unmodeled); cash
# charitable cap stays 50% (CA never conformed to the TCJA 60%); the
# high-AGI itemized phase-out reduces itemized by the LESSER of 6% of
# FEDERAL AGI over the (indexed, cached) threshold or 80% of the
# phase-out-subject deductions (R&TC §17077 — exempt: medical, investment
# interest, and — if ever modeled — casualty and gambling losses).
_CA_MORTGAGE_LIMIT = 1000000.0
_CA_CHARITY_CASH_AGI_CAP = 0.50
_CA_ITEMIZED_PHASEOUT_RATE = 0.06
_CA_ITEMIZED_PHASEOUT_MAX = 0.80


def compute_ca_itemized(
    federal_agi: float,
    ca_agi: float,
    tax_year: int,
    mfs_spouse_itemizes: bool | None = None,
    married_filing_separately: bool = False,
    medical_expenses: float = 0.0,
    state_local_income_taxes: float = 0.0,
    real_estate_taxes: float = 0.0,
    personal_property_taxes: float = 0.0,
    mortgage_interest: float = 0.0,
    mortgage_points: float = 0.0,
    average_mortgage_balance: float | None = None,
    investment_interest: float = 0.0,
    charitable_cash: float = 0.0,
    charitable_noncash: float = 0.0,
    misc_2pct_expenses: float = 0.0,
    *,
    # KEYWORD-ONLY, and added here rather than beside its siblings: inserted
    # into the positional block it shifted `married_filing_separately` by one
    # for every caller that passed it by position, turning a $1,000 deduction
    # into a ValueError.
    mfs_spouse_is_a_separate_filer: bool | None = None,
    ca_standard_deduction: float,
    phaseout_threshold: float | None = None,
    charitable_prior_ledger: dict | None = None,
    charitable_worksheet: dict | None = None,
) -> dict:
    """CA Schedule CA itemized deductions — computed from the SOURCE figures
    (not by adjusting the federal Schedule A total), because the differences
    are structural:

    - State/local INCOME taxes (incl. CA income tax and SDI) and sales taxes
      are disallowed entirely, but property taxes have NO cap (no SALT cap).
    - Mortgage limit stays $1M regardless of origination date — interest the
      federal $750K limit prorated away comes BACK for CA.
    - Cash charitable cap is 50% of AGI (CA never conformed to the TCJA
      60%); the OBBBA 0.5% floor is federal-only.
    - Misc 2%-floor deductions (unreimbursed employee expenses, investment
      fees, tax prep) are STILL allowed for CA (no TCJA suspension).
    - High-AGI itemized phase-out (R&TC §17077): reduce by the lesser of 6%
      of FEDERAL AGI over the (indexed, cached) threshold or 80% of the
      phase-out-subject amount — exempt: medical, investment interest (and,
      when ever modeled, casualty and gambling losses — the FTB worksheet
      excludes those too). phaseout_threshold None REFUSES where the return
      has deductions subject to it (Q8, 2026-09-13): skipping the phase-out
      over-states a deduction on a filed return, and AGENTS.md §2 allows a
      degrade only where the output is advisory AND under-states. It was a
      loud note, which is what the other direction is owed.

    EVERY AGI-anchored floor/cap here (medical 7.5%, charitable 50%/30%,
    misc 2%) uses FEDERAL AGI — Schedule CA Part II lines reference federal
    Form 1040 line 11, not CA AGI. ca_agi is accepted for future CA-AGI-
    based lines but is currently unused in the math; a CA-AGI base would
    overstate a retiree's medical deduction (CA subtracts Social Security,
    so CA AGI < federal AGI).

    mfs_spouse_itemizes: the spouse's election, which binds this return in
    BOTH directions — a yes forces itemizing (their itemizing zeroes this
    filer's standard deduction under §63(c)(6)(A), incorporated by R&TC
    §17073.5), and a no bars it (FTB: "both itemize ... or both take the
    standard deduction"). REQUIRED whenever married_filing_separately is
    set, and the two are refused apart: unknown is not "no", and a default
    would assert something about a return nobody asked about. The engine
    cannot see whether the spouse is CONSIDERED UNMARRIED and filing as head
    of household, in which case the coupling does not bind at all; the note
    it emits says so.

    ca_standard_deduction is REQUIRED and keyword-only (Q7, ruled
    2026-09-13). It was optional, and without it this returned
    `use_itemized: None` with no `election_margin` — which a caller
    testing for truth reads as "do not itemize", under-stating the
    deduction for anyone whose CA standard deduction is the larger.
    """
    if isinstance(tax_year, bool) or not isinstance(tax_year, int):
        raise ValueError(f"tax_year must be an int, got {tax_year!r}")
    for name, v in (("federal_agi", federal_agi), ("ca_agi", ca_agi)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            raise ValueError(f"{name} must be a finite number, got {v!r}")
    if not isinstance(married_filing_separately, bool):
        raise ValueError("married_filing_separately must be a boolean, got "
                         f"{married_filing_separately!r}")
    if mfs_spouse_itemizes is not None \
            and not isinstance(mfs_spouse_itemizes, bool):
        raise ValueError("mfs_spouse_itemizes must be a boolean or None, got "
                         f"{mfs_spouse_itemizes!r}")
    # UNKNOWN is not "no". The flag used to default False and False used to be
    # NEUTRAL — it meant "run the ordinary comparison". Now that the coupling
    # binds both ways, False is an assertion about the SPOUSE's return, and a
    # caller who simply did not ask would have that assertion made for them:
    # the standard deduction taken while the spouse itemizes, over-stating by
    # up to the whole standard deduction on the commonest separate-return
    # shape — one spouse holding the mortgage and property tax, the other
    # holding almost nothing. The tax-setup interview records this answer, so
    # a run that has it can pass it; a run that cannot is a run that must not
    # guess. One symbol stopped meaning two things.
    if married_filing_separately and mfs_spouse_itemizes is None:
        raise ValueError(
            "married_filing_separately=True needs mfs_spouse_itemizes — the "
            "CA election is the spouse's to make as much as this filer's, and "
            "defaulting it silently picks one. Ask whether the spouse "
            "itemizes (tax-setup records it) and pass True or False.")
    if mfs_spouse_itemizes is not None and not married_filing_separately:
        raise ValueError(
            "mfs_spouse_itemizes needs married_filing_separately=True — a "
            "spouse's election only binds this return when it is filed "
            "separately, and IGNORING the flag would restore the ordinary "
            "itemized-vs-standard comparison for a filer the coupling "
            "governs.")
    if mfs_spouse_is_a_separate_filer is not None \
            and not isinstance(mfs_spouse_is_a_separate_filer, bool):
        raise ValueError("mfs_spouse_is_a_separate_filer must be a boolean or "
                         f"None, got {mfs_spouse_is_a_separate_filer!r}")
    fed_agi = float(federal_agi)     # unfloored §17077 trigger
    # FTB anchors the medical/charitable/misc floors on FEDERAL AGI
    # (Schedule CA Part II references 1040 line 11) — floored for the same
    # negative-AGI reason as the federal function. ca_agi intentionally
    # unused in the current math.
    agi_v = max(0.0, fed_agi)
    med = _amount(medical_expenses, "medical_expenses")
    _amount(state_local_income_taxes, "state_local_income_taxes")
    re_tax = _amount(real_estate_taxes, "real_estate_taxes")
    pp_tax = _amount(personal_property_taxes, "personal_property_taxes")
    mort_int = _amount(mortgage_interest, "mortgage_interest")
    points = _amount(mortgage_points, "mortgage_points")
    inv_int = _amount(investment_interest, "investment_interest")
    cash = _amount(charitable_cash, "charitable_cash")
    noncash = _amount(charitable_noncash, "charitable_noncash")
    misc = _amount(misc_2pct_expenses, "misc_2pct_expenses")

    notes = ["CA disallows state/local income taxes (incl. CA tax + SDI) "
             "and sales taxes — property taxes deductible WITHOUT cap"]

    medical_deductible = _medical_deductible(med, agi_v)
    taxes_deductible = round(re_tax + pp_tax, 2)
    # CA keeps the pre-TCJA acquisition-debt limit, halved for MFS/RDP-
    # separate filers ($500K) per the Schedule CA line 8 instructions.
    ca_limit = (_CA_MORTGAGE_LIMIT / 2 if married_filing_separately
                else _CA_MORTGAGE_LIMIT)
    mortgage_deductible = round(
        _prorated_mortgage_interest(mort_int + points,
                                    average_mortgage_balance,
                                    ca_limit, notes,
                                    f"CA (${ca_limit:,.0f} limit)"),
        2)

    cash_cap = _CA_CHARITY_CASH_AGI_CAP * agi_v
    cash_allowed = min(cash, cash_cap)
    noncash_cap = min(_CHARITY_APPRECIATED_AGI_CAP * agi_v,
                      max(0.0, 0.50 * agi_v - cash_allowed))
    noncash_allowed = min(noncash, noncash_cap)
    charitable_deductible = round(cash_allowed + noncash_allowed, 2)
    ca_charitable_carryforward = round(
        (cash - cash_allowed) + (noncash - noncash_allowed), 2)
    if ca_charitable_carryforward > 0:
        notes.append(
            "CA cash charitable cap is 50% of FEDERAL AGI (no TCJA 60% "
            f"conformity) — ${ca_charitable_carryforward:,.2f} carries "
            "forward for CA (tracked separately from the federal "
            "carryforward; enter manually next year)")

    charity_settlement = {}
    if charitable_prior_ledger is not None or charitable_worksheet is not None:
        from agents.lib.tax_computations.charitable import settle_charitable_ledger
        charity_settlement = settle_charitable_ledger(
            charitable_prior_ledger, charitable_worksheet, tax_year=tax_year,
            jurisdiction="CA", agi=federal_agi, charitable_cash=cash,
            charitable_noncash=noncash)
        charitable_deductible = charity_settlement["charitable_deductible"]
        ca_charitable_carryforward = charity_settlement["charitable_carryforward"]
        notes = [n for n in notes if not n.startswith("CA cash charitable cap")]
        notes.append("CA charitable vintages use the separate reviewed CA worksheet")

    misc_deductible = round(max(0.0, misc - 0.02 * agi_v), 2)
    if misc > 0:
        notes.append("misc 2%-floor deductions allowed for CA only "
                     "(federal TCJA suspension does not apply)")

    subtotal = round(medical_deductible + taxes_deductible
                     + mortgage_deductible + inv_int
                     + charitable_deductible + misc_deductible, 2)

    # What §17077 can reduce: medical and investment interest are outside it.
    # ROUNDED, because `subtotal` already is and subtracting unrounded exempt
    # amounts from it leaves float residue — 1e-13 of "deductions subject to
    # the phase-out" on a return made entirely of the two categories the FTB
    # worksheet exempts. That residue is > 0, so it armed the refusal below and
    # then printed "$0.00" as the amount at stake.
    # Each component is finite — `_amount` rejects NaN and infinity at the
    # boundary — and their SUM need not be. Two deductions near the float
    # maximum overflow to infinity here, and infinity defeats the endpoint
    # comparison below the same way NaN defeats a range check: both ends
    # return `inf`, they compare EQUAL, and the refusal passes a return whose
    # itemized total is `inf`. The enumerated guard refused it by accident,
    # through `subject > 0`; this says so on purpose.
    if not math.isfinite(subtotal):
        raise ValueError(
            "CA itemized deductions sum to a non-finite total — the "
            "individual amounts are each valid but their sum overflows. "
            "Check the source figures; no return has deductions this large.")
    subject = round(max(0.0, subtotal - medical_deductible - inv_int), 2)
    std = _amount(ca_standard_deduction, "ca_standard_deduction")
    # What actually reaches the return, given a phase-out reduction. ONE
    # expression, used both by the refusal below and by the election further
    # down. The refusal used to restate this rule instead of reading it, and
    # the two drifted in the OVER-stating direction.
    #
    # FTB Form 540, married/RDP filing separately (verified 2026-09-14): "you
    # and your spouse/RDP must either both itemize your deductions (even if
    # the itemized deductions of one spouse/RDP are less than the standard
    # deduction) or both take the standard deduction." The coupling binds in
    # BOTH directions and it is not an election either spouse makes alone, so
    # for a separate filer the spouse's choice decides it outright and the
    # comparison never runs.
    #
    # Only the spouse-ITEMIZES half was implemented. A separate filer whose
    # spouse took the STANDARD deduction still itemized whenever itemizing was
    # larger — $12,000 against the $5,706 allowed on the case in the test,
    # $6,294 OVER-stated on a filed return, which is §2's hard-error
    # direction. `compute_ny_itemized` has forced both halves since #156, and its
    # comment said this function "has forced the mirror-image federal
    # §63(c)(6)(A) case since it was written" — true of the half it was
    # looking at. The citation stood for ONE DAY — #156 landed 2026-09-13 and
    # this 2026-09-14 — while the half it cited had been missing since #33 on
    # 2026-07-13, two months. Inspecting a sibling closely enough to quote it
    # is not the same as sweeping it.
    def elects_itemized(total: float) -> bool:
        if (married_filing_separately and mfs_spouse_itemizes is False
                and mfs_spouse_is_a_separate_filer is False):
            # The spouse is considered unmarried, files as head of household
            # and does NOT itemize. They do file their own return — what they
            # are not is a MARRIED individual filing SEPARATELY, which is the
            # status §63(c)(6)(A) and FTB's both-or-neither both key on. So
            # the coupling does not bar this filer and the ordinary
            # comparison governs.
            #
            # ONE DIRECTION ONLY. §63(c)(6)(A) zeroes the standard deduction
            # of "a married individual filing a separate return where EITHER
            # spouse itemizes" — THIS filer is such an individual whatever
            # their spouse's own filing status, so a spouse who itemizes still
            # forces itemizing here. Written symmetrically it handed that
            # filer `max(total, std)`, which takes a standard deduction
            # §63(c)(6)(A) has already zeroed for them whenever their own
            # itemized total is the smaller — "may not have" understated it:
            # they do not have one at all.
            return total > std
        if married_filing_separately:
            # `is True`, not a bare return: None is refused above, but that
            # guard narrows for a READER and not for mypy, which still sees
            # `bool | None` here. A guard a person can follow and the checker
            # cannot is exactly how a float-minus-None shipped two rounds ago,
            # so the narrowing is spelled where the value is used.
            return mfs_spouse_itemizes is True
        return total > std

    def reaches_the_return(reduction: float) -> float:
        """The figure that reaches the return, given a phase-out reduction.

        The ELECTED figure, and deliberately not also the published
        `itemized_total`. For a separate filer whose spouse takes the standard
        deduction the election is constant, so no threshold can change what is
        filed and refusing would be a STOP on a return the missing figure
        cannot affect — the cry-wolf case five rounds were spent removing.

        `itemized_total` and `election_margin` DO still move with the
        threshold for that filer, and come back un-reduced. They are not
        filed; they are the counterfactual a preparer reads when advising the
        couple whether the spouse should itemize. So they are LABELLED rather
        than refused: see the note below, which says the figure is before an
        uncomputed §17077 reduction instead of printing it as an alternative.
        """
        total = round(subtotal - reduction, 2)
        return total if elects_itemized(total) else std

    phaseout = 0.0
    if phaseout_threshold is None:
        # Q8, ruled 2026-09-13 and now AGENTS.md §2: a fallback may DEGRADE
        # only when the output is advisory AND it under-states. This is a
        # figure on a filed CA return and skipping the phase-out OVER-states
        # the deduction, so it STOPS. It was a banner, which is what the
        # under-statement direction is owed and not this one.
        #
        # WHEN it stops is derived, not enumerated. Five review rounds each
        # added one more hand-proved exemption — "at a zero subject amount no
        # threshold matters", "at a zero AGI none matters", "when the standard
        # deduction already wins none matters" — and the fifth of them was
        # wrong, because it open-coded one branch of an election computed forty
        # lines below. A gate is a claim about which inputs cannot matter, and
        # a hand-written claim is only ever as complete as the cases somebody
        # thought of.
        #
        # So price BOTH ENDS of the range the missing threshold spans instead.
        # The reduction is monotone non-increasing in the threshold and reaches
        # the return through one expression, so two evaluations settle it:
        # zero at a threshold at or above this AGI, and its largest value at a
        # threshold of zero. `thr >= 0` is not a tax figure — it is "a dollar
        # threshold is not negative" — so nothing indexed is hardcoded here.
        # If the same figure reaches the return at both ends, no threshold in
        # between can change it.
        #
        # `test_the_derived_gate_matches_the_conditions_it_replaced` holds this
        # to those conditions AS CORRECTED — with the `not mfs_spouse_itemizes`
        # the shipped version lacked — and against that it adds no STOP. Say it
        # that way round: measured against what actually SHIPPED it adds 116
        # stops over a grid, every one of them an MFS filer whose spouse
        # itemizes, which is the defect. "Adds no STOP" about the shipped
        # guard would be false and would hide the fix.
        largest = round(min(_CA_ITEMIZED_PHASEOUT_RATE * max(0.0, fed_agi),
                            _CA_ITEMIZED_PHASEOUT_MAX * subject), 2)
        if reaches_the_return(0.0) != reaches_the_return(largest):
            raise ValueError(
                f"CA itemized phase-out threshold (R&TC §17077, indexed) is "
                f"not in the tax_law cache, and this return has "
                f"${subject:,.2f} of deductions subject to it at a federal AGI "
                f"of ${fed_agi:,.2f} — up to ${largest:,.2f} of reduction "
                f"turns on the missing figure. Computing without it OVER-states "
                f"the CA deduction. Run /tax-setup to fetch "
                f"state.itemized_phaseout, or pass phaseout_threshold "
                f"explicitly.")
    else:
        thr = _amount(phaseout_threshold, "phaseout_threshold")
        if fed_agi > thr:
            phaseout = round(min(_CA_ITEMIZED_PHASEOUT_RATE * (fed_agi - thr),
                                 _CA_ITEMIZED_PHASEOUT_MAX * subject), 2)
            notes.append(
                f"CA itemized phase-out applied: −${phaseout:,.2f} (lesser "
                "of 6% of AGI over threshold or 80% of subject deductions)")
    itemized_total = round(subtotal - phaseout, 2)

    # The election is UNCONDITIONAL now (Q7, ruled 2026-09-13). It used to be
    # skipped when no standard deduction was supplied, and `use_itemized` came
    # back None with `election_margin` absent — both of which read as "no" to a
    # caller testing them for truth, so a taxpayer whose CA standard deduction
    # was the larger of the two took `itemized_total` instead: UNDER-stating
    # the deduction and over-stating the tax. A banner was what that direction
    # was owed, and it was added; requiring the figure is what stops there
    # being an election to warn about.
    # The coupling bars itemizing only when the spouse is an ordinary separate
    # filer. A spouse who is CONSIDERED UNMARRIED and files as head of
    # household is not a MARRIED individual filing SEPARATELY — they file
    # their own return under a different status — so §63(c)(6) never reaches
    # them and FTB's both-or-neither does not bind this filer either —
    # they may itemize. Barring them anyway UNDER-states line 18 by the whole
    # margin: $12,000 against $5,706 on the case in the test.
    #
    # This STOPS rather than degrading, and the earlier draft of this change
    # got that wrong. AGENTS.md §2 allows a banner INSTEAD of a stop only when
    # the output is ADVISORY as well as under-stating, and line 18 is filed.
    # An under-statement is not a safe harbour on a return.
    #
    # It asks only where the answer changes the number: the question is moot
    # unless the coupling would actually bar a larger itemized total. That is
    # NOT "only an unusual return sees it" — an ordinary separate-filing
    # couple with itemized deductions worth more than the standard reaches it
    # every time, and should, because that is precisely where the two answers
    # differ. What it spares is the return where they do not. Both ends of the
    # unknown are priced, the same shape the §17077 gate above uses.
    if (married_filing_separately and mfs_spouse_itemizes is False
            and itemized_total > std
            and mfs_spouse_is_a_separate_filer is None):
        # Asked only on the NO branch, and only when itemizing wins: those are
        # exactly the returns where the spouse's status changes the figure.
        raise ValueError(
            f"This filer's ${itemized_total:,.2f} of CA itemized deductions "
            f"beat their ${std:,.2f} standard deduction, and whether they may "
            f"take them turns on the SPOUSE. If the spouse files their own "
            f"separate return, FTB's both-or-neither rule bars itemizing here "
            f"and the answer is ${std:,.2f}; if the spouse is CONSIDERED "
            f"UNMARRIED and files as head of household, they are not a "
            f"separate filer, the coupling does not bind, and the answer is "
            f"${itemized_total:,.2f}. Pass mfs_spouse_is_a_separate_filer. "
            f"The two differ by ${itemized_total - std:,.2f} on a filed "
            f"return, so this is not a figure to default.")

    use_itemized = elects_itemized(itemized_total)
    if married_filing_separately and mfs_spouse_itemizes:
        notes.append(
            "MFS/RDP-separate with an itemizing spouse MUST itemize for "
            "CA too — the CA standard deduction is unavailable"
            + (f", and the itemized deduction is the SMALLER of the two: "
               f"${itemized_total:,.2f} against a ${std:,.2f} standard "
               f"deduction this filer may not take"
               if itemized_total < std else ""))
    elif married_filing_separately and mfs_spouse_is_a_separate_filer is False:
        notes.append(
            "MFS/RDP-separate, but the spouse is CONSIDERED UNMARRIED and "
            "files as head of household — they file their own return, but "
            "not as a married person filing separately, which is the status "
            "FTB's both-or-neither rule keys on. It does not bind this filer, "
            "so the ordinary itemized-vs-standard comparison governs.")
    elif married_filing_separately:
        # The counterfactual is only printed when it was actually COMPUTED.
        # Without the threshold the phase-out never ran for this filer — the
        # election does not depend on it, so nothing refused — and the
        # itemized total is un-reduced by up to 80% of the subject amount.
        # Printing it as "itemizing would have been $X" put an over-stated
        # alternative in front of a preparer deciding whether the spouse
        # should itemize instead. Said as what it is, or not said.
        alternative = ""
        if itemized_total > std and phaseout_threshold is not None:
            alternative = (f", though itemizing would have been "
                           f"${itemized_total:,.2f}")
        elif itemized_total > std:
            alternative = (
                f", and itemizing would have been ${itemized_total:,.2f} "
                f"BEFORE an §17077 phase-out that could not be computed — "
                f"fetch state.itemized_phaseout before acting on that figure")
        notes.append(
            "MFS/RDP-separate: BOTH spouses must itemize or NEITHER may (FTB "
            "Form 540), and the spouse is NOT itemizing — the CA standard "
            "deduction is taken here" + alternative
            + ". If the spouse is CONSIDERED UNMARRIED and files as head of "
            "household, they are not an MFS filer and this coupling does not "
            "bind: say so and re-run, because this filer may then itemize.")
    if use_itemized and itemized_total > std:
        notes.append(
            "CA itemizes even when the federal return takes the "
            "standard deduction — the elections are independent")

    if charity_settlement:
        assert charitable_worksheet is not None
        if charitable_worksheet["itemizes"] != use_itemized:
            raise ValueError("CA charitable worksheet election differs; resolve absorption for the actual election")
    return {
        **charity_settlement,
        "charitable_inputs": ({"charitable_prior_ledger": charitable_prior_ledger,
                               "charitable_worksheet": charitable_worksheet,
                               "agi": federal_agi, "charitable_cash": cash,
                               "charitable_noncash": noncash} if charity_settlement else None),
        "medical_deductible": medical_deductible,
        "taxes_deductible": taxes_deductible,
        "mortgage_deductible": mortgage_deductible,
        "investment_interest": round(inv_int, 2),
        "charitable_deductible": charitable_deductible,
        "misc_deductible": misc_deductible,
        "charitable_carryforward": ca_charitable_carryforward,
        "phaseout_reduction": phaseout,
        "itemized_total": itemized_total,
        "use_itemized": use_itemized,
        "election_margin": round(itemized_total - std, 2),
        "notes": notes,
    }


def compute_ca_schedule_p(
    ca_taxable_income: float,
    ca_regular_tax: float,
    filing_status: str,
    exemption: float,
    phaseout_start: float,
    ca_preference_adjustments: float = 0.0,
    iso_bargain_element: float = 0.0,
    tmt_rate: float | None = None,
    phaseout_rate: float | None = None,
) -> dict:
    """California Schedule P (540) alternative minimum tax (N1).

    The CA analogue of federal Form 6251, taking the signed net of the
    separately worked Part I adjustments. ISO spread stays separate so its
    deferral-attributable credit can be tracked. CA differs from the federal computation in three ways,
    all reflected here: a flat tentative-minimum-tax rate, no federal-style
    capital-gains coordination, and CA's own exemption phaseout. Rates and
    indexed amounts must come from the covered-year CA cache, never federal law.

    Args:
        ca_taxable_income: Form 540 line 19 (CA regular taxable income) — the
            Schedule P Part I line 1 starting point. May be negative (accepted).
        ca_regular_tax: Form 540 line 31, regular tax BEFORE exemption and other
            credits (Schedule P line 25). CA AMT is the excess of TMT over this.
        exemption / phaseout_start: the CA AMT exemption and its phase-out
            threshold for the filing status, from state.amt in the tax_law
            cache (INDEXED annually — REQUIRED, never hardcoded; an unfetched
            table hard-errors rather than zeroing a CA ISO exerciser's AMT).
        ca_preference_adjustments: net CA Schedule P Part I adjustments and
            preference items OTHER than the ISO spread (signed) — scoped v1 to
            what the workspace already carries. Excludes the ISO spread, which
            is passed separately so the deferral/exclusion carryforward split
            can be computed.
        iso_bargain_element: the ISO exercise spread (FMV − strike for shares
            not sold in the same year) — the CA deferral item that generates the
            Schedule P AMT credit carryforward.

    Returns dict: ca_amti, ca_exemption_allowed, ca_tmt, ca_regular_tax,
    ca_amt (added to Form 540 tax), schedule_p_carryforward (deferral-
    attributable CA AMT — persists to next year via /tax-rollover, the Form
    8801 pattern), iso_bargain_element echo, notes.
    """
    fs = _normalize_filing_status(filing_status)
    for what, v in (("exemption", exemption), ("phaseout_start", phaseout_start)):
        if v is None:
            raise ValueError(
                f"state.amt.{what} is unfetched — refusing to compute CA "
                "Schedule P without the indexed table (a guessed exemption "
                "would silently zero the CA AMT for an ISO exerciser); fetch "
                "via /tax-setup")
    ti = _amount(ca_taxable_income, "ca_taxable_income", allow_negative=True)
    rtax = _amount(ca_regular_tax, "ca_regular_tax")
    ex = _amount(exemption, "exemption")
    ph_start = _amount(phaseout_start, "phaseout_start")
    adj = _amount(ca_preference_adjustments,
                        "ca_preference_adjustments", allow_negative=True)
    iso = _amount(iso_bargain_element, "iso_bargain_element")
    for what, r in (("tmt_rate", tmt_rate), ("phaseout_rate", phaseout_rate)):
        if isinstance(r, bool) or not isinstance(r, (int, float)) \
                or not math.isfinite(float(r)) or not (0 < float(r) <= 1):
            raise ValueError(f"{what} must be a finite fraction in (0, 1], got {r!r}")
    assert tmt_rate is not None and phaseout_rate is not None
    tmt_rate = float(tmt_rate)
    phaseout_rate = float(phaseout_rate)

    def _amti(iso_amount: float) -> float:
        amti = ti + adj + iso_amount
        # TY2025 Schedule P Part I line 21 MFS worksheet. Its threshold is
        # the exemption-elimination ceiling and its cap is one exemption;
        # both derive from the fetched exemption/phaseout amounts.
        if fs == "MFS":
            amti += min(ex, phaseout_rate * max(0.0, amti - (ph_start + ex / phaseout_rate)))
        return amti

    def _tmt(iso_amount: float) -> float:
        amti = _amti(iso_amount)
        exemption_allowed = max(0.0, ex - phaseout_rate * max(0.0, amti - ph_start))
        return tmt_rate * max(0.0, amti - exemption_allowed)

    # Round the TMT and the regular tax at source so the returned breakdown
    # reconciles: ca_amt == round(ca_tmt − ca_regular_tax) exactly (the §6651
    # breakdown-reconciliation lesson — deriving ca_amt from the UNROUNDED tmt
    # while displaying a rounded ca_tmt can drift a cent at a boundary). The tax
    # form itself works with the rounded TMT, so this is also more form-accurate.
    rtax_r = round(rtax, 2)
    tmt_r = round(_tmt(iso), 2)
    ca_amt = round(max(0.0, tmt_r - rtax_r), 2)
    # Schedule P AMT credit: like Form 8801, the credit equals CA AMT
    # attributable to DEFERRAL items — refigure with exclusion items only (v1's
    # only deferral item is the ISO spread), so the split is the with/without-
    # ISO delta. Both AMT figures come from rounded TMTs for the same reason.
    amt_exclusion_only = round(max(0.0, round(_tmt(0.0), 2) - rtax_r), 2)
    carryforward = round(max(0.0, ca_amt - amt_exclusion_only), 2)

    amti_full = _amti(iso)
    exemption_allowed = max(0.0, ex - phaseout_rate * max(0.0, amti_full - ph_start))
    notes = [
        "CA Schedule P uses the supplied signed Part I preferences and adjustments "
        "and the fetched CA AMT rates. Source worksheets "
        "are the caller's to supply; deferral-credit attribution is ISO-only. "
        "CA taxes capital gains as ordinary income, so there is no "
        "federal-style Part III coordination.",
    ]
    if ca_amt > 0:
        notes.append(
            f"CA AMT of ${ca_amt:,.2f} applies (added to the Form 540 tax). "
            f"${carryforward:,.2f} of it is deferral-attributable (ISO) and "
            "becomes a Schedule P AMT credit carryforward — record it via "
            "/tax-rollover; losing it forfeits the money permanently.")
    return {
        "ca_amti": round(amti_full, 2),
        "ca_exemption_allowed": round(exemption_allowed, 2),
        "ca_tmt": tmt_r,
        "ca_regular_tax": rtax_r,
        "ca_amt": ca_amt,
        "schedule_p_carryforward": carryforward,
        "iso_bargain_element": round(iso, 2),
        "notes": notes,
    }


def compute_ca_540(
    *, tax_year: int, filing_status: str, state_law: dict,
    federal_agi: float, schedule_ca_additions: float, schedule_ca_subtractions: float,
    deduction: float, deduction_method: str, personal_count: int, blind_count: int,
    senior_count: int, dependent_count: int, ca_preference_adjustments: float,
    lump_sum_tax: float,
    iso_bargain_element: float, components: dict, credits: dict | None = None,
) -> dict:
    """Derive the reviewed Form 540 from separately worked components.

    Components are the closed CA_540_COMPONENT_LINES set, keyed line_<n>.
    Credits are ALLOWED worksheet amounts, not eligibility calculations.
    The signed preference net excludes the automatically added standard
    deduction and the separately supplied ISO spread. See state-module-ca.md.
    """
    normalized_status = _normalize_filing_status(filing_status)
    from agents.lib.tax_computations.state.planning import _ca_tax
    if isinstance(tax_year, bool) or not isinstance(tax_year, int) or tax_year not in _CA_540_REVIEWED_YEARS:
        raise ValueError("Form 540 line map has not been reviewed for this tax year")
    status = normalized_status.lower()
    validate_ca_return_law(state_law)
    if deduction_method not in ("standard", "itemized", "dependent_worksheet"):
        raise ValueError("deduction_method must identify the reviewed deduction worksheet")
    counts = (personal_count, blind_count, senior_count, dependent_count)
    for name, count in zip(("personal", "blind", "senior", "dependent"), counts):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{name}_count must be a non-negative integer")
        limit = 2 if status == "mfj" or (name == "personal" and status == "qss") else 1
        if name != "dependent" and count > limit:
            raise ValueError(f"{name}_count exceeds the filing status's eligible filers")
    if blind_count > personal_count or senior_count > personal_count:
        raise ValueError("blind/senior counts cannot exceed eligible personal exemptions")
    if not isinstance(components, dict) or set(components) != {f"line_{n}" for n in CA_540_COMPONENT_LINES}:
        raise ValueError("Form 540 components must name exactly CA_540_COMPONENT_LINES")
    out: dict = {key: round(_amount(value, key), 2) for key, value in components.items()}
    lump_sum = _amount(lump_sum_tax, "lump_sum_tax")
    if lump_sum > out["line_34"]:
        raise ValueError("lump_sum_tax cannot exceed Form 540 line 34")
    agi = round(_amount(federal_agi, "federal_agi", allow_negative=True), 2)
    adds = round(_amount(schedule_ca_additions, "schedule_ca_additions", allow_negative=True), 2)
    subs = round(_amount(schedule_ca_subtractions, "schedule_ca_subtractions", allow_negative=True), 2)
    elected = round(_amount(deduction, "deduction"), 2)
    deduction_key = "single" if status == "mfs" else "mfj" if status == "qss" else status
    standard = _amount(state_law.get("standard_deduction", {}).get(deduction_key), "CA standard deduction")
    if standard <= 0:
        raise ValueError("CA standard deduction must be fetched and positive")
    # Line 7's box-6 rule drops a personal exemption exactly when someone else
    # can claim that filer, so a short personal count is the claimable case.
    claimable = personal_count < (2 if status in ("mfj", "qss") else 1)
    if deduction_method == "standard" and claimable:
        raise ValueError("a filer who can be claimed needs the dependent deduction worksheet")
    if deduction_method == "standard" and elected != standard:
        raise ValueError("elected standard deduction differs from the fetched CA amount")
    if deduction_method == "dependent_worksheet":
        if not claimable:
            raise ValueError("the dependent deduction worksheet is only for a filer someone else can claim")
        # Its line 5 is the smaller of the earned-income amount and line 4,
        # the filing status's standard deduction.
        if elected > standard:
            raise ValueError("the dependent worksheet deduction cannot exceed the CA standard deduction")
    # Line 18 takes the larger of the two. Only a separate filer, bound by a
    # spouse who itemizes, or a claimable filer measured against the smaller
    # worksheet amount can itemize below the standard deduction.
    if deduction_method == "itemized" and status != "mfs" and not claimable and elected < standard:
        raise ValueError("Form 540 line 18 is the larger of itemized and standard deductions; "
                         "an itemized amount below the standard deduction understates line 18")
    personal = _amount(state_law.get("personal_exemption", {}).get("amount"), "state.personal_exemption.amount")
    dependent = _amount(state_law.get("dependent_exemption", {}).get("amount"), "state.dependent_exemption.amount")
    out.update(line_7=round(personal_count * personal, 2), line_8=round(blind_count * personal, 2),
               line_9=round(senior_count * personal, 2), line_10=round(dependent_count * dependent, 2),
               line_13=agi, line_14=subs, line_15=round(agi - subs, 2), line_16=adds, line_18=elected)
    out["line_11"] = round(sum(out[f"line_{n}"] for n in (7, 8, 9, 10)), 2)
    out["line_17"] = round(out["line_15"] + adds, 2)
    signed_taxable = round(out["line_17"] - elected, 2)
    out["line_19"] = max(0.0, signed_taxable)
    out["line_31"] = _ca_tax(out["line_19"], status, state_law)
    validate_ca_exemption_phaseout(state_law.get("exemption_phaseout"))
    exemption = compute_ca_exemption_credit(agi, filing_status, sum(counts[:3]), dependent_count,
        personal, dependent, state_law["exemption_phaseout"])
    out["line_32"] = exemption["exemption_credit"]
    out["line_33"] = round(max(0.0, out["line_31"] - out["line_32"]), 2)
    out["line_35"] = round(out["line_33"] + out["line_34"], 2)
    adj = _amount(ca_preference_adjustments, "ca_preference_adjustments", allow_negative=True)
    if deduction_method in ("standard", "dependent_worksheet"):
        adj += elected
    key = "mfj" if status == "qss" else status
    amt = state_law["amt"]
    schedule_p = compute_ca_schedule_p(signed_taxable, out["line_31"], filing_status,
        amt["exemption"][key], amt["phaseout_start"][key], adj, iso_bargain_element,
        tmt_rate=amt["tmt_rate"], phaseout_rate=amt["phaseout_rate"])
    if credits is not None:
        from agents.lib.tax_computations.state.ca_credits import compute_ca_credit_order
        paper = compute_ca_credit_order(regular_tax=out["line_31"],
            exemption_credits=out["line_32"], tentative_minimum_tax=schedule_p["ca_tmt"],
            form540_line35=out["line_35"],
            dependent_care=out["line_40"], renters_credit=out["line_46"], tax_year=tax_year,
            new_deferral_credit=schedule_p["schedule_p_carryforward"], **credits)
        out.update({key: paper[key] for key in ("line_43", "line_44", "line_45")})
        out["credit_workpaper"] = paper
        out["ftb_3804_cr"] = paper["ftb_3804_cr"]
        schedule_p.update({key: value for key, value in paper["ftb_3510"].items()
                           if not key.startswith("line_")})
        schedule_p["ftb_3510"] = paper["ftb_3510"]
    else:
        out.update(credit_workpaper=None, ftb_3804_cr=None)
        schedule_p["ftb_3510"] = None
    out["line_47"] = round(sum(out[f"line_{n}"] for n in (40, 43, 44, 45, 46)), 2)
    out["line_48"] = round(max(0.0, out["line_35"] - out["line_47"]), 2)
    out["line_61"] = schedule_p["ca_amt"]
    surcharge = state_law.get("mental_health_surcharge")
    if not isinstance(surcharge, dict):
        raise ValueError("state.mental_health_surcharge is missing")
    rate = _amount(surcharge.get("rate"), "state.mental_health_surcharge.rate")
    if rate > 1:
        raise ValueError("state.mental_health_surcharge.rate must be a fraction")
    out["line_62"] = round(max(0, out["line_19"] - _amount(surcharge.get("threshold"),
        "state.mental_health_surcharge.threshold")) * rate, 2)
    out["line_64"] = round(sum(out[f"line_{n}"] for n in (48, 61, 62, 63)), 2)
    out["line_78"] = round(sum(out[f"line_{n}"] for n in range(71, 78)), 2)
    out["line_93"] = round(max(0, out["line_78"] - out["line_91"]), 2)
    out["line_94"] = round(max(0, out["line_91"] - out["line_78"]), 2)
    out["line_95"] = round(max(0, out["line_93"] - out["line_92"]), 2)
    out["line_96"] = round(max(0, out["line_92"] - out["line_93"]), 2)
    out["line_97"] = round(max(0, out["line_95"] - out["line_64"]), 2)
    if out["line_98"] > out["line_97"]:
        raise ValueError("Form 540 line 98 cannot exceed line 97 overpayment")
    out["line_99"] = round(out["line_97"] - out["line_98"], 2)
    out["line_100"] = round(max(0, out["line_64"] - out["line_95"]), 2)
    # Line 111 also settles unpaid use tax/ISR and voluntary contributions.
    out["line_111"] = round(max(0, out["line_100"] + out["line_94"] + out["line_96"]
                                  + out["line_110"] - out["line_99"]), 2)
    settlement = round(out["line_100"] + out["line_94"] + out["line_96"] + out["line_110"]
                       + out["line_112"] + out["line_113"] - out["line_99"], 2)
    out["line_114"] = max(0.0, settlement)
    out["line_115"] = max(0.0, -settlement)
    note = ("Form 540 line 113: penalty NOT COMPUTED by the form constructor; "
            "the checked FTB5805 workpaper must determine the penalty and final settlement.")
    out.update(computed_tax_year=tax_year, ca_agi=out["line_17"], ca_taxable_income=out["line_19"],
        ca_standard_deduction=standard,
        federal_agi=agi, ca_tax_from_table=out["line_31"], ca_tax_after_credits=out["line_33"],
        ca_mental_health_surcharge=out["line_62"], ca_total_tax=out["line_64"],
        ca_refund=out["line_115"], ca_amount_owed=out["line_114"],
        schedule_p=schedule_p, exemption_worksheet=exemption, line_113_note=note)
    out["ca_exemption_credits"] = {
        "personal": {"count": sum(counts[:3]), "per_person": personal,
                     "total": exemption["personal_blind_senior_credit"]},
        "dependent": {"count": dependent_count, "per_dependent": dependent,
                      "total": exemption["dependent_credit"]}, "total": out["line_32"]}
    return out
