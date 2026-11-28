"""Reviewed federal payments shared by Form 1040 line 26 and Form 2210."""

import math
from dataclasses import dataclass
from datetime import date, timedelta

from agents.lib.irs_dates import next_business_day
from agents.lib.tax_computations._shared import _amount, _normalize_filing_status
from agents.lib.tax_computations.federal.penalties import compute_form_2210

LINE_26 = "line_26_estimated_payments"
LINE_38 = "line_38_estimated_tax_penalty"
EXTENSION_PAYMENT = "schedule_3_line_10_extension_payment"
JOINT_EXTENSION = "schedule_3_line_10_joint_4868"
EXTENSION_ALLOCATION = "schedule_3_line_10_allocation_confirmed"


@dataclass(frozen=True)
class FederalPayments:
    year: int
    status: str
    rows: tuple[dict, ...]

    @property
    def line_26(self) -> float:
        return round(sum(row["amount"] for row in self.rows if row["kind"] != "balance_due"), 2)


def _object(value, where: str) -> dict:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{where} must be an object")


def _money(value, where: str) -> float:
    try:
        return _amount(value, where)
    except (ValueError, OverflowError):
        raise ValueError(f"{where} requires a finite non-negative amount") from None


def check_extension_allocation(form: dict, filing_status: str | None,
                               recorded: tuple[object, ...] = ()) -> None:
    """Require a reviewed claim to the Form 4868 payment on a non-joint return.

    The return is joint only when every recorded status says MFJ, as the
    tie-out reads one: result meta saying MFJ beside a profile saying MFS
    skipped this check, and a joint payment was claimed whole on a separate
    return with no payment review to catch the disagreement.
    """
    extension = form.get(EXTENSION_PAYMENT)
    if extension is None or _money(extension, "Schedule 3 line 10") == 0:
        return
    statuses = [status for status in (filing_status, *recorded) if isinstance(status, str) and status.strip()]
    # With no status anywhere this returns: that is the caller's missing input
    # (exit 1, nothing recorded), never a verdict of this check.
    if all(_normalize_filing_status(status) == "MFJ" for status in statuses):
        return
    joint = form.get(JOINT_EXTENSION)
    if type(joint) is not bool:
        raise ValueError(f"Schedule 3 line 10 requires form_1040.{JOINT_EXTENSION} "
                         "to say whether Form 4868 was joint")
    if joint and form.get(EXTENSION_ALLOCATION) is not True:
        raise ValueError(f"Schedule 3 line 10 requires form_1040.{EXTENSION_ALLOCATION} "
                         "for this return's reviewed share of the joint Form 4868 payment")


def _in_early_settlement(effective: date, year: int) -> bool:
    return date(year + 1, 1, 1) <= effective <= date(year + 1, 4, 15)


def _payment_row(raw, index: int, year: int, status: str) -> tuple[str, dict]:
    where = f"estimated_payments.federal[{index}]"
    row = _object(raw, where)
    ref = row.get("payment_id")
    if not isinstance(ref, str) or not ref or ref != ref.strip():
        raise ValueError(f"{where} needs an opaque payment_id tied to its source")
    if type(row.get("tax_year")) is not int or row["tax_year"] != year:
        raise ValueError(f"{where} is credited to the wrong federal tax year")
    owner = row.get("person")
    if owner not in ("primary", "spouse", "joint"):
        raise ValueError(f"{where}.person must identify the taxpayer, spouse or joint account")
    if status != "MFJ" and (owner == "spouse" or
            (owner == "joint" and row.get("allocation_confirmed") is not True)):
        raise ValueError(f"{where} needs the reviewed individual allocation for this return")
    kind = row.get("kind")
    if kind not in ("estimate", "carry_in", "balance_due"):
        raise ValueError(f"{where} must be a federal estimate, applied overpayment or early balance-due payment")
    raw_date = row.get("date")
    try:
        effective = date.fromisoformat(raw_date) if isinstance(raw_date, str) else None
    except ValueError:
        effective = None
    if effective is None or raw_date != effective.isoformat():
        raise ValueError(f"{where} requires an effective date in YYYY-MM-DD format")
    if not year <= effective.year <= year + 1:
        raise ValueError(f"{where}.date must fall in the return year or the following year")
    # The next year's first installment is due April 15 itself, so a bound at
    # April 15 let an on-time next-year voucher raise this year's line 26.
    if (kind == "estimate" and effective > next_business_day(date(year + 1, 1, 15))
            and row.get("late_credit_confirmed") is not True):
        raise ValueError(f"{where} needs late_credit_confirmed for an estimate after the fourth "
                         "installment due date: the IRS account must show it credited to this "
                         "return year as an estimated tax payment")
    if kind == "carry_in" and effective < date(year, 4, 15):
        raise ValueError(f"{where}.date precedes the first installment date for an applied overpayment")
    if kind == "balance_due" and not _in_early_settlement(effective, year):
        raise ValueError(f"{where}.date must be in the early settlement period for this return")
    amount = _money(row.get("amount"), f"{where}.amount")
    if amount <= 0 or amount != round(amount, 2):
        raise ValueError(f"{where}.amount must be a positive amount to the cent")
    item = {"kind": kind, "date": effective.isoformat(), "amount": amount}
    if kind == "balance_due" and row.get("not_extension_payment_confirmed") is True:
        item["not_extension_payment_confirmed"] = True
    return ref, item


def reviewed_payments(result: dict, profile: dict, filing_status: str | None = None) -> FederalPayments | None:
    """Reconcile posted estimates against an independent count/total and carry-in."""
    payments = _object(profile.get("estimated_payments") or {}, "estimated_payments")
    prior = _object(profile.get("prior_year") or {}, "prior_year")
    opening = prior.get("estimated_payments_applied")
    opening = _money(0 if opening is None else opening, "prior_year.estimated_payments_applied")
    rows = payments.get("federal", [])
    if not isinstance(rows, list):
        raise ValueError("estimated_payments.federal must be a list of payment records")
    review = payments.get("federal_review")
    form = result.get("form_1040") or {}
    meta = result.get("meta") or {}
    return_status = filing_status or meta.get("filing_status") or profile.get("filing_status")
    check_extension_allocation(form, filing_status, (meta.get("filing_status"), profile.get("filing_status")))
    recorded = form.get(LINE_26)
    flags = _object(profile.get("flags") or {}, "flags")
    ensure_review = (rows or opening or recorded or
                     flags.get("has_estimated_payments") is True or payments.get("federal_2210") is not None)
    if review is None:
        if ensure_review:
            raise ValueError("Form 1040 line 26 needs reviewed federal payments and any applied overpayment")
        if recorded is not None:
            _money(recorded, "Form 1040 line 26")
        return None
    review = _object(review, "estimated_payments.federal_review")
    if review.get("confirmed") is not True:
        raise ValueError("Confirm federal payment sources, credited year, timing and allocation before building line 26")
    year = meta.get("tax_year", profile.get("tax_year"))
    if (type(year) is not int or not 2000 <= year <= 2099
            or profile.get("tax_year", year) != year
            or type(review.get("tax_year")) is not int or review["tax_year"] != year):
        raise ValueError("Federal payment review must name the tax year of this return")
    status = _normalize_filing_status(return_status)
    if profile.get("filing_status") and _normalize_filing_status(profile["filing_status"]) != status:
        raise ValueError("Federal payment ownership requires agreement on the return's filing status")

    parsed = []
    refs = set()
    has_late_estimate = False
    balance_count = 0
    has_unconfirmed_balance = False
    third_due = next_business_day(date(year, 9, 15))
    for index, raw in enumerate(rows, 1):
        ref, item = _payment_row(raw, index, year, status)
        if ref in refs:
            raise ValueError(f"estimated_payments.federal[{index}] duplicates a payment source")
        refs.add(ref)
        parsed.append(item)
        # A fourth-installment or later estimate, not only one inside the
        # settlement window: a December 31 estimate beside a January 1 payment,
        # or a late April 16 one beside April 15, was one remittance typed
        # twice a day apart. An earlier installment is months from any
        # balance-due date.
        if item["kind"] == "estimate" and date.fromisoformat(item["date"]) > third_due:
            has_late_estimate = True
        if item["kind"] == "balance_due":
            balance_count += 1
            if raw.get("distinct_payment_confirmed") is not True:
                has_unconfirmed_balance = True
    if has_unconfirmed_balance and (balance_count > 1 or has_late_estimate):
        raise ValueError("Multiple balance-due payments, or a balance-due payment beside an estimate paid "
                         "after the third installment's due date, require confirmation of separate remittances. "
                         "Set distinct_payment_confirmed on every balance-due row after review")
    _check_extension_not_an_estimate(result, rows, parsed, year)
    if not math.isfinite(sum(row["amount"] for row in parsed)):
        raise ValueError("Federal payment records exceed the finite range for a total")
    estimates = [row["amount"] for row in parsed if row["kind"] == "estimate"]
    count = review.get("estimate_count")
    total = _money(review.get("estimate_total"), "federal_review.estimate_total")
    if type(count) is not int or count != len(estimates) or total != round(sum(estimates), 2):
        raise ValueError("Federal estimate records disagree with the reviewed payment count or total")
    if flags.get("has_estimated_payments") is True and not estimates:
        raise ValueError("The profile reports federal estimates but the reviewed records contain none")
    if opening != round(sum(row["amount"] for row in parsed if row["kind"] == "carry_in"), 2):
        raise ValueError("Federal applied-overpayment records disagree with the reviewed prior-year credit")
    return FederalPayments(year, status, tuple(sorted(parsed, key=lambda row: (row["date"], row["kind"], row["amount"]))))


def _check_extension_not_an_estimate(result: dict, rows: list, parsed: list[dict], year: int) -> None:
    """Line 31 already counts the Form 4868 payment; line 26 must not count it again."""
    extension = (result.get("form_1040") or {}).get(EXTENSION_PAYMENT)
    if extension is None:
        return
    extension = _money(extension, "Schedule 3 line 10")
    # Every estimate paid after the return year, not one of the same amount:
    # a cent's difference counted the Form 4868 payment on line 26 and line 31,
    # as a balance-due row beside line 10 already needs its own confirmation.
    for raw, item in zip(rows, parsed, strict=True):
        if (extension > 0 and item["kind"] == "estimate"
                and item["date"] >= date(year + 1, 1, 1).isoformat()
                and raw.get("distinct_payment_confirmed") is not True):
            raise ValueError("An estimate paid after the return year is recorded beside the Schedule 3 "
                             "line 10 extension payment. Confirm separate remittances with "
                             "distinct_payment_confirmed on each such estimate")


def reviewed_balance_due(form: dict, rows: list[dict] | tuple[dict, ...]) -> float:
    """Keep extension payments already credited on Form 1040 out of settlement."""
    if not isinstance(rows, (list, tuple)) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Form 2210 payment table must contain payment records")
    balance_rows = [row for row in rows if row.get("kind") == "balance_due"]
    if not balance_rows:
        return 0.0
    other_payments = _money(form.get("line_31_schedule3_part2", 0), "Form 1040 line 31")
    excess_ss = _money(form.get("schedule_3_line_11_excess_ss", 0), "Schedule 3 line 11")
    extension = form.get(EXTENSION_PAYMENT)
    if extension is None and other_payments > excess_ss:
        raise ValueError("Record form_1040.schedule_3_line_10_extension_payment, 0 when no "
                         "extension payment was made, before crediting any balance-due payments")
    extension = _money(0 if extension is None else extension, "Schedule 3 line 10")
    if round(extension + excess_ss, 2) > other_payments:
        raise ValueError("The extension payment and excess Social Security exceed Form 1040 line 31")
    if extension > 0 and any(row.get("not_extension_payment_confirmed") is not True
                             for row in balance_rows):
        raise ValueError("Schedule 3 line 10 and a balance-due payment both appear. "
                         "Confirm separate remittances with not_extension_payment_confirmed")
    return _money(round(sum(_money(row.get("amount"), "Balance-due payment")
                            for row in balance_rows), 2), "Total balance-due payments")


def _check_january_waiver(payments: FederalPayments, balance: float) -> None:
    """§6654(h) waives the fourth installment only for a return filed and paid
    in full by January 31. The flag alone zeroed it with the balance unpaid,
    so the penalty was under-stated and the amount owed with it."""
    if balance <= 0:
        return
    deadline = date(payments.year + 1, 1, 31)
    while deadline.weekday() >= 5:  # a weekend deadline moves to Monday
        deadline += timedelta(days=1)
    paid = round(sum(row["amount"] for row in payments.rows if row["kind"] == "balance_due"
                     and date.fromisoformat(row["date"]) <= deadline), 2)
    if paid < balance:
        raise ValueError(f"The January 31 exception needs balance-due payments dated by {deadline.isoformat()} "
                         "that pay the return in full")


def form_2210_workpaper(form: dict, payments: FederalPayments, config: dict, tax_law: dict) -> dict:
    """Reprice the reported penalty and settlement using the same payment events."""
    config = _object(config, "estimated_payments.federal_2210")
    mode = config.get("method")
    if mode not in ("regular", "irs_calculates"):
        raise ValueError("Choose the reviewed Form 2210 regular method or confirm that the IRS may calculate it")
    balance_due_paid = reviewed_balance_due(form, payments.rows)
    refundable = sum(_money(form.get(field, 0), field)
                     for field in ("line_27_eic", "line_28_refundable_actc", "line_29_refundable_aotc"))
    excess_ss = _money(form.get("schedule_3_line_11_excess_ss", 0), "Schedule 3 line 11")
    schedule_3 = _money(form.get("line_31_schedule3_part2", 0), "Form 1040 line 31")
    additional = _money(config.get("schedule_3_refundable_credits"), "Form 2210 Schedule 3 refundable credits")
    extension = _money(form.get(EXTENSION_PAYMENT) or 0, "Schedule 3 line 10")
    if additional > round(schedule_3 - excess_ss - extension, 2):
        raise ValueError("Form 2210 additional refundable credits exceed the remaining Schedule 3 payments")
    total_tax = _money(form.get("line_24_total_tax"), "Form 1040 line 24")
    withholding = _money(form.get("line_25d_total_withholding"), "Form 1040 line 25d")
    excluded = _money(config.get("excluded_current_year_tax"), "Form 2210 excluded current-year taxes")
    if excluded > total_tax:
        raise ValueError("Form 2210 excluded current-year taxes exceed Form 1040 line 24")
    early_filing = config.get("filed_and_paid_by_jan31", False)
    if type(early_filing) is not bool:
        raise ValueError("Form 2210 early filing and payment confirmation must be a boolean")
    if early_filing:
        _check_january_waiver(payments, round(total_tax - withholding - payments.line_26
                                              - refundable - schedule_3, 2))
    no_prior_return = config.get("no_prior_year_return_confirmed", False)
    if type(no_prior_return) is not bool:
        raise ValueError("Form 2210 no-prior-year-return confirmation must be a boolean")
    if mode == "regular" and config.get("prior_year_tax") is None and not no_prior_return:
        # The prior-year prong can only LOWER the required payment, so leaving
        # it out over-states the penalty written on line 38 (AGENTS.md §4).
        raise ValueError("Form 2210 regular method needs the prior-year tax, or confirmation that no "
                         "12-month prior-year return was filed")
    workpaper = compute_form_2210(
        current_year_tax=round(total_tax - excluded - refundable - additional, 2),
        withholding=round(withholding + excess_ss, 2),
        filing_status=payments.status,
        tax_year=payments.year,
        prior_year_tax=config.get("prior_year_tax"),
        prior_year_agi=config.get("prior_year_agi"),
        prior_year_zero_liability_confirmed=config.get("prior_year_zero_liability_confirmed", False),
        prior_year_return_filed_12_months_confirmed=config.get("prior_year_return_filed_12_months_confirmed", False),
        estimated_payments=list(payments.rows),
        underpayment_rates=(tax_law.get("federal") or {}).get("underpayment_rates"),
        fourth_installment_waived=early_filing,
    )
    if mode == "regular" and not (workpaper["penalty_computed"] or workpaper["no_penalty"]):
        raise ValueError("Form 2210 penalty needs the applicable rate quarters or a reviewed decision to let the IRS calculate it")
    return {**workpaper, "computed_tax_year": payments.year, "payment_table": list(payments.rows),
            "line_26_total": payments.line_26, "balance_due_paid": balance_due_paid, "method": mode,
            "line_38": (workpaper["penalty"] or 0) if mode == "regular" else 0}


def filing_differences(result: dict, profile: dict, tax_law: dict) -> list[str]:
    """Check the filed line and every dated event even when 1040 arithmetic is manual."""
    try:
        payments = reviewed_payments(result, profile)
        recorded_workpaper = result.get("form_2210")
        dated_workpaper = isinstance(recorded_workpaper, dict) and "payment_table" in recorded_workpaper
        if payments is None:
            if dated_workpaper:
                return ["Form 2210 has a payment workpaper without reviewed federal payment sources"]
            return []
        form = result.get("form_1040") or {}
        if _money(form.get(LINE_26), "Form 1040 line 26") != payments.line_26:
            return ["Form 1040 line 26 disagrees with the reviewed federal payment records"]
        config = profile["estimated_payments"].get("federal_2210")
        if config is None and not payments.rows and not dated_workpaper:
            return []
        expected = form_2210_workpaper(form, payments, config, tax_law)
        if recorded_workpaper != expected or _money(form.get(LINE_38), "Form 1040 line 38") != expected["line_38"]:
            return ["Form 2210 or Form 1040 line 38 disagrees with the reviewed federal payment schedule"]
    except ValueError as exc:
        return [str(exc)]
    return []
