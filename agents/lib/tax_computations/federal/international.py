#!/usr/bin/env python3
"""§988, PFIC §1291, Form 8938 and the §904 foreign tax credit.

The four things a return does with assets held abroad, and the group with the
least to do with the rest of the federal engine: it reaches outside itself only
for the amount validators and for the §6601 interest machinery in
`penalties.py`, which §1291's deferred-tax interest is computed with rather
than beside.
"""

import datetime
import math

import agents.lib  # noqa: F401 — the host-interpreter guard
from agents.lib.irs_dates import next_business_day
from agents.lib.tax_computations._shared import (
    _amount,
    _coerce_numeric,
    _normalize_filing_status,
)
from agents.lib.tax_computations.federal.government_benefits import _money
from agents.lib.tax_computations.federal.penalties import (
    compute_underpayment_interest,
)

# Form 8938 reporting thresholds (the instructions' four rows; stable since
# FATCA inception). ONE table: the tax-law seed is built from it, and the
# `mfj_us` row carried the foreign-resident single pair in three hand-mirrored
# copies until the 2026-09-06 auditor replay caught one of them.
FORM_8938_THRESHOLDS = {
    "mfj_us": {"year_end": 100000, "any_time": 150000},
    "single_us": {"year_end": 50000, "any_time": 75000},
    "mfj_foreign": {"year_end": 400000, "any_time": 600000},
    "single_foreign": {"year_end": 200000, "any_time": 300000},
}

FTC_CREDIT_FIELD = "schedule_3_line_1_foreign_tax_credit"
FTC_STATEMENT_TAX = "foreign_tax_paid_on_statements_usd"
# How the return treats foreign tax: a credit figured on Form 1116, the §904(j)
# credit without it, an itemized deduction (Schedule A line 6), or neither. A
# workpaper with no method is refused, because Form 1040 line 20 has no
# decomposition here that could show a credit typed straight into it.
FTC_METHODS = ("form_1116", "simplified", "deduction", "not_claimed")
QUALIFIED_PAYEE_STATEMENTS = ("Form 1099-DIV, Form 1099-INT, Schedule K-1 (Form 1041), "
                              "Schedule K-3 (Form 1065 or 1120-S), or a substitute statement for one")


def schedule_2_line_1z(result: dict) -> float:
    """Schedule 2 Part I line 1z, which is Form 1040 line 17 less the line 2 AMT.

    Line 17 is Schedule 2 line 3 (lines 1z and 2), and the Form 6251 AMT on
    line 2 is not regular tax liability under §26(b), so it never enters Form
    1116 line 20 or the simplified credit's cap.
    """
    form = result.get("form_1040") or {}
    line_17 = _money(form.get("line_17_schedule2_part1", 0), "Form 1040 line 17")
    form_6251 = result.get("form_6251")
    if form_6251 is not None and not isinstance(form_6251, dict):
        raise ValueError("Form 6251 must be an object.")
    amt = _money(form_6251.get("amt"), "Form 6251 AMT") if form_6251 is not None else 0.0
    return max(0.0, round(line_17 - amt, 2))


def regular_tax_for_credit(result: dict) -> float:
    """The regular tax that caps the credit (Form 1116 line 20, individuals).

    The 2025 Form 1116 instructions: "Enter the total of Form 1040, 1040-SR, or
    1040-NR, line 16, and Schedule 2 (Form 1040), Part I, line 1z, less any tax
    included on line 16 from Form 4972." return_builder derives line 16 from
    the brackets, so it never holds Form 4972 tax.
    """
    form = result.get("form_1040") or {}
    line_16 = _money(form.get("line_16_tax", 0), "Form 1040 line 16")
    return round(line_16 + schedule_2_line_1z(result), 2)


def check_foreign_tax_credit(result: dict, profile: dict, law: dict, statement_tax: float) -> None:
    """Check Schedule 3 line 1 against the foreign tax treatment the return records."""
    form = result.get("form_1040") or {}
    credit = _money(form.get(FTC_CREDIT_FIELD, 0), "Schedule 3 line 1 foreign tax credit")
    section = result.get("form_1116_ftc")
    if section is None:
        if credit:
            raise ValueError("Schedule 3 line 1 requires Form 1116 or a verified simplified election.")
        if statement_tax:
            raise ValueError("Record the form_1116_ftc workpaper and its method for the foreign tax on the statements.")
        return
    if not isinstance(section, dict):
        raise ValueError("The foreign tax credit workpaper must be an object.")
    method = section.get("method")
    if method not in FTC_METHODS:
        raise ValueError("Record how the return treats foreign tax paid: form_1116, simplified, deduction or "
                         "not_claimed.")
    paid = _money(section.get("foreign_tax_paid_usd", 0), "Total foreign tax paid")
    recorded = _money(section.get(FTC_STATEMENT_TAX, 0), "Foreign tax paid on statements")
    if recorded != round(statement_tax, 2) or paid < recorded:
        raise ValueError("Reconcile foreign tax paid to the full amount reported on the payee statements.")
    allowed = _money(section.get("ftc_allowed", 0), "Reviewed foreign tax credit")
    if credit != allowed or credit > _money(form.get("line_20_schedule3_line8", 0), "Form 1040 line 20"):
        raise ValueError("Schedule 3 line 1 must match the reviewed credit and be included in Form 1040 line 20.")
    if method == "form_1116":
        verify_form_1116(section, result, profile)
        return
    # §904(j)(1) bars carrying foreign tax to or from an election year. A
    # deduction year and a year claiming nothing have no limitation to exceed.
    if any(_money(section.get(field, 0), f"Foreign tax {field}") > 0
           for field in ("carryover_applied_usd", "carryforward_created_usd")):
        year = {"simplified": "A simplified election year", "deduction": "A deduction year"}.get(
            method, "A year claiming no foreign tax")
        raise ValueError(f"{year} cannot apply a carryover or create a carryforward. Only Form 1116 can.")
    if method == "simplified":
        _check_simplified_election(section, result, profile, law, statement_tax)
    elif credit:
        raise ValueError("A foreign tax deduction, or foreign tax not claimed, leaves Schedule 3 line 1 at zero.")


def _check_simplified_election(section: dict, result: dict, profile: dict, law: dict, statement_tax: float) -> None:
    """§904(j): passive income and its tax on payee statements, paid tax within the limit."""
    paid = _money(section.get("foreign_tax_paid_usd", 0), "Total foreign tax paid")
    credit = _money(section.get("ftc_allowed", 0), "Reviewed foreign tax credit")
    if (section.get("all_foreign_source_income_passive_confirmed") is not True
            or section.get("all_foreign_income_and_taxes_on_payee_statements_confirmed") is not True
            or not statement_tax or paid != round(statement_tax, 2)):
        raise ValueError("The simplified election needs all foreign-source income to qualify as passive and all income "
                         "and tax on qualified payee statements.")
    status = _normalize_filing_status((result.get("meta") or {}).get("filing_status") or profile.get("filing_status"))
    if profile.get("filing_status") and _normalize_filing_status(profile["filing_status"]) != status:
        raise ValueError("The return and profile must agree on filing status for the simplified election.")
    limits = (law.get("federal") or {}).get("foreign_tax_credit_simplified") or {}
    # The joint figure is for a joint return only; a surviving spouse uses the other.
    key = "max_mfj" if status == "MFJ" else "max_single"
    limit = _money(limits.get(key), "Covered-year simplified foreign tax credit limit")
    if limit <= 0:
        raise ValueError("Fetch the covered-year simplified foreign tax credit limit.")
    if paid > limit:
        raise ValueError("Foreign tax paid exceeds the simplified election limit for this filing status. Use Form 1116.")
    # "the smaller of (a) your total foreign tax, or (b) your regular tax" (2025
    # Form 1116 instructions); creditability reductions only lower it.
    if credit > paid or credit > regular_tax_for_credit(result):
        raise ValueError("The simplified credit exceeds foreign tax paid or regular income tax.")


def compute_ftc_limitation(
    foreign_source_income: float,
    worldwide_income: float,
    us_tax: float,
    foreign_tax_paid: float,
) -> dict:
    """Compute Foreign Tax Credit limitation (Form 1116).

    Args:
        foreign_source_income: Total foreign source income.
        worldwide_income: Total worldwide income (AGI).
        us_tax: US tax before FTC.
        foreign_tax_paid: Total foreign taxes paid in USD.

    Returns:
        Dict with FTC limitation details.

    Raises:
        ValueError: If any parameter is None or negative (where applicable).
    """
    if foreign_source_income is None:
        raise ValueError("foreign_source_income cannot be None. Check Form 1116 Part I.")
    if worldwide_income is None:
        raise ValueError("worldwide_income cannot be None. Check Form 1040 line 11 (AGI).")
    if us_tax is None:
        raise ValueError("us_tax cannot be None. Compute federal tax before FTC limitation.")
    if us_tax < 0:
        raise ValueError(f"us_tax cannot be negative (got {us_tax}). Check federal tax computation.")
    if foreign_tax_paid is None:
        raise ValueError("foreign_tax_paid cannot be None. Check Form 1116 foreign tax amounts.")
    if foreign_tax_paid < 0:
        raise ValueError(f"foreign_tax_paid cannot be negative (got {foreign_tax_paid}).")

    if worldwide_income <= 0:
        limitation = 0.0
    else:
        # FTC limitation ratio: capped at 1.0 (upper) and 0.0 (lower, net foreign loss)
        ratio = min(1.0, max(0.0, foreign_source_income / worldwide_income))
        limitation = ratio * us_tax

    ftc_allowed = min(foreign_tax_paid, limitation)

    return {
        "foreign_source_income": round(foreign_source_income, 2),
        "worldwide_income": round(worldwide_income, 2),
        "us_tax": round(us_tax, 2),
        "foreign_tax_paid": round(foreign_tax_paid, 2),
        "limitation": round(limitation, 2),
        "ftc_allowed": round(ftc_allowed, 2),
        "limited_by": "tax_paid" if foreign_tax_paid <= limitation else "limitation",
    }


def form_8938_account_totals(accounts: list[dict]) -> tuple[float, float]:
    """Validate and sum the year-end and maximum values for the Form 8938 screen."""
    if accounts is None:
        raise ValueError("accounts cannot be None. Pass an empty list [] if no foreign accounts.")
    if not isinstance(accounts, list):
        raise ValueError("Form 8938 accounts must be a list of account records.")
    fields = ("year_end_value_usd", "max_value_usd")
    totals = dict.fromkeys(fields, 0.0)
    for number, account in enumerate(accounts, 1):
        if not isinstance(account, dict):
            raise ValueError(f"Form 8938 account #{number} must be an object.")
        for field in fields:
            label = f"Form 8938 account #{number} {field}"
            raw = account.get(field)
            if raw is None:
                raise ValueError(f"{label} is missing.")
            try:
                amount = _coerce_numeric(raw)
            except (TypeError, OverflowError):
                amount = None
            if amount is None:
                raise ValueError(f"{label} has a non-numeric or out-of-range value.")
            if not math.isfinite(amount) or amount < 0:
                raise ValueError(f"{label} must be finite and nonnegative.")
            totals[field] += amount
            if not math.isfinite(totals[field]):
                raise ValueError(f"{label} exceeds the finite range for the total.")
    return totals["year_end_value_usd"], totals["max_value_usd"]


def check_form_8938_required(
    accounts: list[dict],
    filing_status: str,
    lives_abroad: bool = False,
    form_8938_thresholds: dict | None = None,
) -> dict:
    """Check if Form 8938 filing is required.

    Per IRS rules, MFJ has its own thresholds; all other statuses (single,
    MFS, HoH) use the "single" thresholds.

    Args:
        accounts: List of {"year_end_value_usd": float, "max_value_usd": float}.
                  Values can be strings like "$5,000" — they will be coerced to float.
        filing_status: "single", "mfj", "mfs", "hoh".
        lives_abroad: Whether taxpayer lives outside the US.
        form_8938_thresholds: Thresholds from tax_law JSON (federal.form_8938_thresholds).
                              If None, uses IRS default thresholds as fallback.

    Returns:
        Dict with threshold analysis.

    Raises:
        ValueError: If either account value is missing or invalid, or if the
                    threshold dictionary has wrong keys or None values.
    """
    # X8: this had its own lowercase-only set, so it REFUSED the canonical
    # "MFJ" that `_normalize_filing_status` returns — a fifth spelling of the
    # same rule, and the one that rejected the engine's own output. It
    # validated (nothing silently mis-branched), so the fix is consistency
    # rather than a defect: one validator, both spellings accepted, and QSS
    # reaches the Single threshold row the same way every non-MFJ status does.
    filing_status = _normalize_filing_status(filing_status).lower()

    # IRS Form 8938 thresholds: MFJ has its own row; every other status
    # (single, MFS, HoH) takes "single". The cache's block when it has one,
    # else the instructions' table, which the seed is built from too.
    thresholds = (form_8938_thresholds if form_8938_thresholds is not None
                  else FORM_8938_THRESHOLDS)
    threshold_key = ("mfj" if filing_status == "mfj" else "single") + \
        ("_foreign" if lives_abroad else "_us")
    threshold = thresholds.get(threshold_key, {})
    if form_8938_thresholds is not None:
        # Validate the cache's structure; the built-in table needs no check.
        if "year_end" not in threshold or "any_time" not in threshold:
            raise ValueError(
                f"form_8938_thresholds['{threshold_key}'] must contain 'year_end' and 'any_time' keys. "
                f"Got keys: {list(threshold.keys())}. "
                f"Available top-level keys: {list(form_8938_thresholds.keys())}. "
                "Expected structure: {'mfj_us': {'year_end': 100000, 'any_time': 150000}, ...}"
            )
        if threshold["year_end"] is None or threshold["any_time"] is None:
            raise ValueError(
                f"Form 8938 thresholds for '{threshold_key}' contain None values — "
                "not yet populated from tax_law JSON. Run /tax-setup to fetch."
            )

    total_year_end, total_max = form_8938_account_totals(accounts)
    required = total_year_end > threshold["year_end"] or total_max > threshold["any_time"]

    return {
        "filing_status": filing_status,
        "lives_abroad": lives_abroad,
        "threshold_year_end": threshold["year_end"],
        "threshold_any_time": threshold["any_time"],
        "total_year_end": round(total_year_end, 2),
        "total_max": round(total_max, 2),
        "required": required,
    }


def _fx_rate(value, what: str) -> float:
    """A spot exchange rate: finite, strictly positive (USD per 1 unit of the
    foreign currency). A zero/negative rate is a data error, not a valid rate."""
    v = _amount(value, what)
    if v == 0:
        raise ValueError(f"{what} must be > 0 (a spot exchange rate), got 0")
    return v


def compute_section_988(
    fc_cost: float,
    fc_proceeds: float,
    rate_acquisition: float,
    rate_disposition: float,
    long_term: "bool | None" = None,
) -> dict:
    """§988 foreign-currency debt (bond) disposition: split the total USD
    gain/loss into its ORDINARY exchange component (§988(a)(1)) and its CAPITAL
    market component (Reg. §1.988-2(b)).

    A directly-held non-USD bond — a bond is DEBT, never a PFIC — bought for
    `fc_cost` units of foreign currency and sold/redeemed for `fc_proceeds`
    units, at spot rates `rate_acquisition` / `rate_disposition` (USD per 1 unit
    of the foreign currency on each date).

    Reg. §1.988-2(b) bifurcation (reconciles EXACTLY to the total):
        basis_usd    = fc_cost     × rate_acquisition
        proceeds_usd = fc_proceeds × rate_disposition
        exchange gain/loss (ORDINARY §988) = fc_cost × (rate_disposition − rate_acquisition)
        market   gain/loss (CAPITAL)       = (fc_proceeds − fc_cost) × rate_disposition
        total = proceeds_usd − basis_usd  = exchange + market

    The exchange component is ORDINARY (→ 1040 Schedule 1) regardless of holding
    period; the market component is CAPITAL (→ Schedule D, short- or long-term
    per `long_term`). They are NOT netted — an exchange gain and a market loss of
    opposite sign land on different lines with different character and do not
    cancel. That non-netting is the whole reason this split exists: reporting the
    lump total as one capital figure would misclassify (and often understate) the
    ordinary FX piece.

    Sourcing (§988(a)(3)): for a US-resident taxpayer the exchange gain/loss is
    sourced by the taxpayer's RESIDENCE, so it is generally US-SOURCE — it must
    NOT be added to Form 1116 foreign-source income (doing so would overstate the
    FTC and understate US tax). The market component's source follows the normal
    capital-gain sourcing.

    `long_term` (the caller derives it from the holding dates) routes the capital
    piece. Long-term means held MORE THAN ONE YEAR by the calendar anniversary
    (§1222) — NOT a 365-day count: a bond bought 2023-07-01 and sold 2024-07-01
    is held exactly one year → SHORT-term (even though 366 days elapsed across the
    leap day). None → term-unknown note.

    v1 scope (caveated, not silently wrong): the FX component on ACCRUED INTEREST
    / OID is not modeled — this is the principal disposition, the common case
    (annual §1272 OID accrual while held is surfaced by the foreign_bond_oid_
    interest capability rule, not computed here).
    """
    cost = _amount(fc_cost, "fc_cost")
    proceeds = _amount(fc_proceeds, "fc_proceeds")
    r_acq = _fx_rate(rate_acquisition, "rate_acquisition")
    r_disp = _fx_rate(rate_disposition, "rate_disposition")
    if long_term is not None and not isinstance(long_term, bool):
        raise ValueError(f"long_term must be a bool or None, got {long_term!r}")

    basis_usd = cost * r_acq
    proceeds_usd = proceeds * r_disp
    exchange = cost * (r_disp - r_acq)
    market = (proceeds - cost) * r_disp

    # Round the two REPORTABLE components (each is an independently-rounded form
    # line), then derive the total FROM the rounded reportables so the returned
    # contract "total = exchange + market" holds to the cent (never an off-by-one
    # reconciliation drift that would accumulate across dispositions).
    exchange_r = round(exchange, 2)
    market_r = round(market, 2)
    total_r = round(exchange_r + market_r, 2)

    if long_term is None:
        term = None
    else:
        term = "long" if long_term else "short"

    notes = [
        "§988 disposition: exchange gain/loss is ORDINARY (Schedule 1), market "
        "gain/loss is CAPITAL (Schedule D) — they are reported separately, not "
        "netted (Reg. §1.988-2(b)).",
        "Exchange gain/loss is US-SOURCE for a US resident (§988(a)(3) residence "
        "rule) — do NOT add it to Form 1116 foreign-source income.",
    ]
    if term is None:
        notes.append(
            "Capital term not specified — supply long_term (held MORE THAN ONE "
            "YEAR by calendar anniversary, §1222, not a 365-day count) so the "
            "market component routes to the correct Schedule D section.")
    notes.append(
        "v1: FX on accrued interest/OID is not modeled (principal disposition "
        "only); annual §1272 OID while held is surfaced separately. Verify a "
        "complex §988 position.")

    return {
        "basis_usd": round(basis_usd, 2),
        "proceeds_usd": round(proceeds_usd, 2),
        "exchange_gain_ordinary": exchange_r,
        "market_gain_capital": market_r,
        "capital_term": term,
        "total_gain": total_r,
        "notes": notes,
    }


# IRS-wide filing/payment postponements of the UNEXTENDED individual return due
# date (the date §1291(c)(3) interest runs from). Modeled so PFIC interest does
# not start too early (which would OVERSTATE the charge) for these years:
#   TY2019 → 2020-07-15 (Notice 2020-18, COVID); TY2020 → 2021-05-17 (Notice 2021-21).
# Other/future IRS-wide postponements are NOT modeled — interest for such a year
# may be slightly overstated (the safe direction); verify against the IRS notice.
_RETURN_DUE_POSTPONED = {
    2019: (2020, 7, 15),
    2020: (2021, 5, 17),
}


def _return_due_date(tax_year: int) -> "datetime.date":
    """The UNEXTENDED individual return due date for `tax_year` — April 15 of the
    next year rolled per §7503 (weekend / DC holiday), OR the IRS-wide postponed
    date when one applied (COVID TY2019/TY2020)."""
    if tax_year in _RETURN_DUE_POSTPONED:
        return datetime.date(*_RETURN_DUE_POSTPONED[tax_year])
    return next_business_day(datetime.date(tax_year + 1, 4, 15))


def _iso_date(value, what: str) -> "datetime.date":
    """Parse an ISO YYYY-MM-DD string (or accept a date) — strict."""
    # datetime.datetime is a SUBCLASS of datetime.date, so it passes the isinstance
    # check below but keeps its time component; a later `date(y, m, d) < that`
    # comparison then raises TypeError (can't compare date and datetime). Normalize
    # to a pure date first, as the underpayment-interest parser does (Codex round 27).
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{what} must be an ISO date string, got {value!r}")
    try:
        return datetime.date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"{what} must be an ISO date (YYYY-MM-DD), got {value!r}")


def compute_pfic_excess_distribution(
    current_distribution: float,
    prior_distributions: list,
    is_first_holding_year: bool = False,
    preceding_holding_years: int | None = None,
) -> dict:
    """§1291(b) excess-distribution threshold for a §1291 fund distribution.

    The EXCESS distribution is the current-year distribution over 125% of the
    AVERAGE distribution over the 3 preceding tax years (or, if the fund was held
    fewer than 3 preceding years, the average over that shorter prior holding
    period). Years in that base window with NO distribution count as $0. The
    non-excess portion is an ordinary dividend taxed normally this year; only the
    excess portion runs through the §1291 allocation (compute_pfic_1291).

    **First-year rule (§1291(b)(2)(B)):** in the tax year the holding period
    BEGINS, the excess distribution is ZERO — the whole distribution is ordinary
    income taxed at normal rates (there is no prior base period to exceed).

    **Ordering:** `prior_distributions` MUST be CHRONOLOGICAL, oldest→newest. Only
    the MOST RECENT min(3, ...) entries form the base (§1291(b)(2)(A) — the three
    tax years immediately preceding the distribution), so a longer list is trimmed
    from the TAIL, not the head.

    **Complete history (`preceding_holding_years`):** pass the number of tax years
    the fund was held BEFORE the current year so the average denominator is the
    correct min(3, that) — with a $0 entry for any base-window year that had no
    distribution. If FEWER than min(3, preceding_holding_years) amounts are
    supplied, the history is incomplete: the result carries `complete=False` (and
    computes conservatively, treating the shortfall as $0 → a higher excess) so
    the caller keeps the §1291 BLOCK rather than filing a threshold built from an
    unknown base. When `preceding_holding_years` is None the count cannot be
    verified from the list length alone, so the result is `complete=False` too
    (Codex round 18) — pass the count for a non-first-year distribution.

    Returns {excess_distribution, threshold, ordinary_now, complete, notes}.
    Verified vs IRS Instructions for Form 8621 (Part V) + §1291(b).
    """
    dist = _amount(current_distribution, "current_distribution")
    if not isinstance(is_first_holding_year, bool):
        raise ValueError(
            f"is_first_holding_year must be a bool, got {is_first_holding_year!r}")
    if not isinstance(prior_distributions, (list, tuple)):
        raise ValueError("prior_distributions must be a list of the prior years' amounts")
    if preceding_holding_years is not None:
        if (not isinstance(preceding_holding_years, int)
                or isinstance(preceding_holding_years, bool)
                or preceding_holding_years < 0):
            raise ValueError(
                f"preceding_holding_years must be a non-negative int, "
                f"got {preceding_holding_years!r}")
    # Reject a contradictory first-year signal: a stale is_first_holding_year=True
    # against a multi-year holding would silently zero the excess (Codex P1).
    if is_first_holding_year and preceding_holding_years not in (None, 0):
        raise ValueError(
            f"is_first_holding_year=True contradicts preceding_holding_years="
            f"{preceding_holding_years} (>0) — a first year has no preceding years. "
            "Pass one consistently (or preceding_holding_years=0 for a true first year).")
    priors = [_amount(p, "prior_distribution") for p in prior_distributions]

    if is_first_holding_year or preceding_holding_years == 0:
        # A genuine first holding year has NO preceding base period, so supplying a
        # NONZERO prior distribution contradicts the first-year claim. Stale
        # first-year metadata alongside real history (e.g. [0, 0, 6000]) would
        # otherwise be silently zeroed here, omitting the §1291 excess allocation
        # for a multi-year fund and understating tax (Codex round 23). Reject it
        # rather than trust the contradictory flag/count.
        if any(p != 0 for p in priors):
            raise ValueError(
                "is_first_holding_year/preceding_holding_years=0 claims a first "
                f"holding year, but prior_distributions carries nonzero history "
                f"({prior_distributions!r}) — a first year has no preceding "
                "distributions. Pass the real preceding_holding_years so the §1291 "
                "excess is allocated, or drop the stale first-year signal.")
        return {
            "excess_distribution": 0.0,
            "threshold": 0.0,
            "ordinary_now": round(dist, 2),
            "complete": True,
            "notes": ["First year of the holding period (§1291(b)(2)(B)): NO excess "
                      "distribution — the entire distribution is ordinary income "
                      "taxed at normal rates this year."],
        }

    # Denominator = the number of preceding base-period years (capped at 3). When
    # the count is OMITTED, the list length alone cannot prove completeness — a
    # caller who supplies only the nonzero prior years (e.g. a lone $6,000 for a
    # 3-year holding) would get a 1-year base and a too-high threshold. So an
    # absent count marks the result INCOMPLETE (keep the §1291 BLOCK) rather than
    # trusting the length (Codex round 18).
    if preceding_holding_years is not None:
        denom = min(3, preceding_holding_years)
        complete = len(priors) >= denom
    else:
        denom = len(priors)
        complete = False  # count required to verify the zero-inclusive base
    # §1291(b)(2)(A): the base is the THREE TAX YEARS IMMEDIATELY PRECEDING the
    # distribution — the MOST RECENT `denom`, not the oldest. `prior_distributions`
    # is chronological oldest→newest, so take the tail. Taking the head (`[:denom]`)
    # would let an old large distribution + recent zero years inflate the threshold
    # and understate the current excess (Codex round 15).
    used = priors[-denom:] if denom else priors

    avg = (sum(used) / denom) if denom else 0.0  # shortfall years count as $0
    threshold = 1.25 * avg
    excess = max(0.0, dist - threshold)
    ordinary_now = round(dist - excess, 2)
    notes = [
        f"Excess distribution = current ${dist:,.2f} − 125% × ${avg:,.2f} avg over "
        f"{denom} preceding base year(s) (${threshold:,.2f}). The non-excess "
        f"${ordinary_now:,.2f} is an ordinary dividend this year; the excess runs "
        "through the §1291 holding-period allocation.",
    ]
    if preceding_holding_years is None:
        notes.append(
            "preceding_holding_years NOT supplied — the base period cannot be "
            "verified from the list length alone (supplying only the nonzero prior "
            "years would understate the threshold), so the result is INCOMPLETE. "
            "Pass preceding_holding_years (and a $0 entry for every base-period year "
            "with no distribution), or is_first_holding_year=True for a true first "
            "year. Keep the §1291 BLOCK until then.")
    elif not complete:
        notes.append(
            f"INCOMPLETE prior-distribution history — {len(priors)} amount(s) supplied "
            f"for a {denom}-year base period. The shortfall is treated as $0 "
            "(conservatively raising the excess); keep the §1291 BLOCK and supply "
            "the full base-period distributions (0 for years with none).")
    return {
        "excess_distribution": round(excess, 2),
        "threshold": round(threshold, 2),
        "ordinary_now": ordinary_now,
        "complete": complete,
        "notes": notes,
    }


def compute_pfic_1291(
    amount: float,
    acquisition_date,
    event_date,
    current_year: int,
    highest_ordinary_rate: dict,
    underpayment_rates: list | None = None,
    pfic_start_date=None,
    is_disposition: bool = False,
) -> dict:
    """§1291 excess-distribution / disposition tax for a PFIC with no QEF/MTM
    election (the punitive default regime).

    `amount` is the excess distribution (from compute_pfic_excess_distribution)
    OR, when `is_disposition`, the ENTIRE GAIN on the sale/redemption (§1291(a)(2)
    treats it all as an excess distribution; a disposition at a LOSS is NOT a
    §1291 event — a negative amount is rejected, the loss goes to Schedule D). It
    is allocated **ratably per day** over the holding period — the DAY AFTER
    `acquisition_date` through `event_date` (§1291(a)(1)(A)/§1223; the acquisition
    day itself is excluded) — then:

    - the CURRENT-year portion is ORDINARY income this year at normal rates
      (`ordinary_income_current`, for the caller to add to 1040 ordinary income);
    - each PRIOR-year portion is the "deferred tax amount": taxed at that year's
      HIGHEST ordinary rate (§1291(c)(2)) with a §6621 interest charge
      (§1291(c)(3)) compounded daily from that year's return due date to the
      current year's return due date (reusing compute_underpayment_interest — the
      M3 machinery + underpayment_rates cache). NOT re-run through the brackets.

    **v1 supports only the PFIC-FROM-ACQUISITION case** (`pfic_start_date` ≤
    `acquisition_date`, the default) — the whole holding period is a PFIC period.
    A fund that became a PFIC AFTER acquisition (a mid-holding transition, with a
    pre-PFIC period) is NOT modeled: `pfic_start_date` after `acquisition_date`
    RAISES so the caller blocks it. Assumes NO QEF/§1295 or MTM/§1296 election —
    the CALLER routes only `pfic_election == none` funds here.

    `total_1291_addon` = Σ deferred tax + Σ interest — the add-on to Form 1040
    TOTAL TAX line 24 (Form 8621 Part V lines 16b/16e/16f): the deferred tax is
    entered to the LEFT of line 24 and the §6621 interest via Schedule 2 line 17p →
    line 23, both AFTER nonrefundable credits (per IRS Instructions for Form 8621,
    Rev. Dec 2025). Do NOT add it to line 16 — line 16 feeds the pre-credit base, so
    a §1291 charge there would be eroded by the CTC/other nonrefundable credits and
    understate the tax. `ordinary_income_current` is SEPARATE (taxed via brackets by
    the caller).

    **Bias (punitive regime → never under-compute):** if a needed prior-year
    highest rate is missing OR malformed (≤0 or >1), or the §6621 interest is
    incomplete for any year, `complete` is False (a malformed rate hard-errors) —
    the caller MUST keep the capability BLOCK rather than treat a partial figure
    as final. Verified vs IRS Instructions for Form 8621 (Part V) + §1291(a)-(c).
    """
    amt = _amount(amount, "amount")
    acq = _iso_date(acquisition_date, "acquisition_date")
    event = _iso_date(event_date, "event_date")
    if event < acq:
        raise ValueError(f"event_date {event} precedes acquisition_date {acq}")
    if not isinstance(current_year, int) or isinstance(current_year, bool):
        raise ValueError(f"current_year must be an int, got {current_year!r}")
    if event.year != current_year:
        raise ValueError(
            f"event_date year {event.year} must equal current_year {current_year} "
            "(the taxable event falls in the current tax year)")
    if not isinstance(highest_ordinary_rate, dict):
        raise ValueError("highest_ordinary_rate must be a {year: rate} dict")
    if not isinstance(is_disposition, bool):
        raise ValueError(f"is_disposition must be a bool, got {is_disposition!r}")
    pfic_start = acq if pfic_start_date is None else _iso_date(pfic_start_date, "pfic_start_date")
    if pfic_start > acq:
        # v1 supports only the COMMON case — the fund was a PFIC from acquisition
        # (pfic_start_date ≤ acquisition_date), so every holding day is a PFIC day.
        # A fund that became a PFIC AFTER acquisition (a mid-holding transition)
        # carries a pre-PFIC period whose treatment interacts with §1298(b)(1),
        # purging/unpedigreed-QEF elections, and the §1298(b)(2) start-up exception
        # — out of scope here. Fail closed (block / prepare Form 8621 manually)
        # rather than compute a transition case either direction.
        raise ValueError(
            f"pfic_start_date {pfic_start} is after acquisition_date {acq} — a "
            "mid-holding PFIC transition (fund became a PFIC after acquisition) is "
            "NOT supported in v1. Block the return (acknowledge) / prepare Form "
            "8621 manually. Only PFIC-from-acquisition is computed.")

    if amt == 0:
        # A below-threshold (non-excess) distribution has NOTHING to allocate —
        # no deferred tax and no interest — so historical top rates and §6621
        # coverage are not needed and their absence must NOT mark the return
        # incomplete (Codex P2). The ordinary (non-excess) portion was already
        # routed by the caller as a normal dividend (compute_pfic_excess_
        # distribution's `ordinary_now`).
        return {
            "ordinary_income_current": 0.0,
            "deferred_tax_total": 0.0,
            "interest_total": 0.0,
            "total_1291_addon": 0.0,
            "per_year": [],
            "complete": True,
            "is_disposition": bool(is_disposition),
            "notes": ["No excess distribution / gain to allocate (amount = 0) — no "
                      "§1291 deferred tax or interest; historical rates not required."],
        }

    def _rate_for(year: int):
        r = highest_ordinary_rate.get(year, highest_ordinary_rate.get(str(year)))
        if r is None:
            return None
        rr = _amount(r, f"highest_ordinary_rate[{year}]")  # finite, ≥0
        if rr <= 0 or rr > 1:
            # A highest ordinary rate is always strictly positive; a 0 (or >1) is
            # malformed data, not a meaningful rate — hard-error rather than let
            # it zero out the deferred tax while the result reads complete.
            raise ValueError(
                f"highest_ordinary_rate[{year}] must be a fraction in (0,1], got {rr}")
        return rr

    # The holding period begins the DAY AFTER acquisition and runs through the
    # disposition/event date (§1223) — the acquisition day itself is excluded.
    one_day = datetime.timedelta(days=1)
    holding_start = acq + one_day
    if holding_start > event:
        holding_start = event  # same-day acquire+dispose → a 1-day holding period
    total_days = (event - holding_start).days + 1

    ordinary_current = 0.0
    deferred_tax_total = 0.0
    interest_total = 0.0
    per_year = []
    missing_rate_years = []
    incomplete_interest_years = []

    # Transitions are blocked above, so the fund is a PFIC for the ENTIRE holding
    # period: each PRIOR year is a prior PFIC year (deferred tax + interest), and
    # the CURRENT year is ordinary income now (§1291(a)(1)(B)-(C)).
    for y in range(holding_start.year, event.year + 1):
        ys = max(holding_start, datetime.date(y, 1, 1))
        ye = min(event, datetime.date(y, 12, 31))
        days_y = (ye - ys).days + 1
        portion = amt * days_y / total_days

        if y == current_year:
            ordinary_current += portion
            per_year.append({"year": y, "days": days_y,
                             "allocated": round(portion, 2), "kind": "ordinary_now"})
            continue

        rate = _rate_for(y)
        if rate is None:
            missing_rate_years.append(y)
            per_year.append({"year": y, "days": days_y, "allocated": round(portion, 2),
                             "kind": "prior_pfic", "deferred_tax": None,
                             "interest": None,
                             "note": "highest ordinary rate for this year not supplied"})
            continue
        deferred = round(portion * rate, 2)
        deferred_tax_total += deferred
        # §1291(c)(3): interest runs from the due date of year y's return to the
        # due date of the CURRENT year's return. §1291(c)(3) flush language defines
        # "due date" as the date prescribed by law **DETERMINED WITHOUT REGARD TO
        # EXTENSIONS** — so the end date is the current year's UNEXTENDED due date,
        # NOT the actual (possibly October-extended) filing date. `_return_due_date`
        # gives the unextended date (April 15 §7503-rolled, or the IRS-wide §7508A
        # postponement when one applied). Verified vs the §1291(c)(3) definition.
        due = _return_due_date(y)
        pay = _return_due_date(current_year)
        ires = compute_underpayment_interest(deferred, due, pay, underpayment_rates)
        interest_y = ires.get("interest")
        if not ires.get("computed") or not ires.get("complete") or interest_y is None:
            incomplete_interest_years.append(y)
            interest_y = interest_y or 0.0
        interest_total += interest_y
        per_year.append({"year": y, "days": days_y, "allocated": round(portion, 2),
                         "kind": "prior_pfic", "rate": rate,
                         "deferred_tax": deferred, "interest": round(interest_y, 2),
                         "interest_complete": bool(ires.get("complete"))})

    complete = not missing_rate_years and not incomplete_interest_years
    notes = [
        ("Disposition — the ENTIRE gain is treated as an excess distribution "
         "(§1291(a)(2)) and " if is_disposition else "Excess distribution ")
        + "allocated ratably per day over the holding period (day after "
        "acquisition through the event, §1291(a)(1)(A)/§1223). The current-year "
        "portion is ordinary income now; each prior year is taxed at that year's "
        "highest ordinary rate + a §6621 interest charge (§1291(c)).",
    ]
    if missing_rate_years:
        notes.append(
            "INCOMPLETE — highest ordinary rate missing for prior year(s): "
            f"{missing_rate_years}. The deferred tax for those years is NOT "
            "computed; keep the return BLOCKED (do not treat this as final) and "
            "fetch/pin the historical top rate.")
    if incomplete_interest_years:
        notes.append(
            "INCOMPLETE — §6621 interest could not be fully computed for year(s): "
            f"{incomplete_interest_years} (the underpayment_rates timeline does not "
            "cover the full span). Extend the rate fetch; keep the return BLOCKED.")

    return {
        "ordinary_income_current": round(ordinary_current, 2),
        "deferred_tax_total": round(deferred_tax_total, 2),
        "interest_total": round(interest_total, 2),
        "total_1291_addon": round(deferred_tax_total + interest_total, 2),
        "per_year": per_year,
        "complete": complete,
        "is_disposition": bool(is_disposition),
        "notes": notes,
    }


# ---- Form 1116 workpapers and their Schedule B balances (AA124 step 2) ----
_BASKETS = {"section_951a", "foreign_branch", "passive", "general",
            "section_901j", "treaty_resourced", "lump_sum"}
# "No foreign tax carryovers are allowed for foreign taxes paid or accrued on
# section 951A category income" (2025 Form 1116 instructions, line 10).
_NO_CARRYOVER = "section_951a"
CARRYOVER_PATH = "prior_year.imported_ftc_baskets"


def _origins(raw, oldest: int, latest: int) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("Form 1116 carryovers need amounts by origin year.")
    amounts = {}
    for origin, amount in raw.items():
        if not isinstance(origin, str) or not origin.isascii() or not origin.isdigit() or len(origin) != 4:
            raise ValueError("Form 1116 carryover origin years must be four-digit years.")
        value = _money(amount, "Form 1116 carryover amount")
        if value and not oldest <= int(origin) <= latest:
            raise ValueError("A Form 1116 carryover falls outside the reviewed origin years.")
        if value:
            amounts[origin] = value
    return amounts


def carryover_baskets(rows: object, year: int) -> dict[str, dict[str, float]]:
    """The shared prior-return importer shape, for balances entering this year."""
    if not isinstance(rows, list):
        raise ValueError("Form 1116 carryover categories must be listed separately.")
    by_basket: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each Form 1116 carryover category needs an object.")
        basket = row.get("basket")
        if not isinstance(basket, str) or basket not in _BASKETS or basket in by_basket:
            raise ValueError("Form 1116 carryover categories must be recognized and unique.")
        by_basket[basket] = _origins(row.get("carryforward_by_origin_year"), year - 10, year - 1)
        if basket == _NO_CARRYOVER and by_basket[basket]:
            raise ValueError("Section 951A category foreign taxes have no carryover.")
    return by_basket


def _line_18(workpaper: dict, result: dict, year: int) -> float:
    """Form 1116 line 18 from its worksheet, refusing an unsupported adjustment."""
    form = result.get("form_1040") or {}
    base = _money(workpaper.get("line_18_base"), "Form 1116 line 18 worksheet base", signed=True)
    if year == 2025:
        # "the sum of (i) Form 1040 ... line 11b, minus Form 1040 ... line 14;
        # and (ii) Schedule 1-A (Form 1040), line 37" — line 37 is the senior
        # deduction alone, not the Schedule 1-A total on Form 1040 line 13b.
        agi = _money(form.get("line_11_agi"), "Form 1040 line 11b", signed=True)
        deductions = _money(form.get("line_14_total_deductions"), "Form 1040 line 14")
        if base != round(agi - deductions + _schedule_1a_line_37(result), 2):
            raise ValueError("Form 1116 line 18 starts with Form 1040 line 11b minus line 14 plus Schedule 1-A line 37.")
    adjustment = _money(workpaper.get("line_18_adjustment"), "Reviewed worldwide qualified dividend and gain adjustments")
    # Worksheet line 11 adds lines 3, 5, 7, 9 and 10: each a share, at most all,
    # of worldwide qualified dividends and net capital gain (lines 2-10), so it
    # can never exceed Form 1040 line 3a plus a positive line 7.
    qualified = _money(form.get("line_3a_qualified_dividends", 0), "Form 1040 line 3a")
    gain = _money(form.get("line_7_capital_gain", 0), "Form 1040 line 7", signed=True)
    if adjustment > round(qualified + max(0.0, gain), 2):
        raise ValueError("The Form 1116 line 18 adjustment exceeds the qualified dividends and capital gain on the return.")
    line_18 = _money(workpaper.get("line_18"), "Form 1116 line 18")
    if line_18 != max(0.0, round(base - adjustment, 2)):
        raise ValueError("Form 1116 line 18 must match its adjusted worldwide income worksheet.")
    return line_18


def _schedule_1a_line_37(result: dict) -> float:
    form = result.get("form_1040") or {}
    total = _money(form.get("line_13b_schedule_1a_deductions", 0), "Form 1040 line 13b")
    block = (result.get("schedule_1a") or {}).get("senior") if isinstance(result.get("schedule_1a"), dict) else None
    senior = block.get("allowed") if isinstance(block, dict) else None
    if senior is None:
        if total:
            raise ValueError("Record schedule_1a.senior so Form 1116 line 18 adds back Schedule 1-A line 37 alone.")
        return 0.0
    senior = _money(senior, "Schedule 1-A line 37")
    if senior > total:
        raise ValueError("Schedule 1-A line 37 exceeds the Schedule 1-A total on Form 1040 line 13b.")
    return senior


def _line_20(workpaper: dict, result: dict, method: str) -> float:
    """Form 1116 line 20: regular tax only, never the AMT on Schedule 2 line 2."""
    line_20 = _money(workpaper.get("line_20"), "Form 1116 line 20")
    if method == "checked":
        schedule_2 = _money(workpaper.get("schedule_2_line_1z"), "Schedule 2 line 1z")
        line_16 = _money((result.get("form_1040") or {}).get("line_16_tax"), "Form 1040 line 16")
        if schedule_2 > schedule_2_line_1z(result) or line_20 != round(line_16 + schedule_2, 2):
            raise ValueError("Form 1116 line 20 must reconcile to Form 1040 line 16 and Schedule 2 line 1z.")
    elif line_20 > regular_tax_for_credit(result):
        raise ValueError("Form 1116 line 20 exceeds Form 1040 line 16 plus Schedule 2 line 1z.")
    return line_20


def verify_form_1116(section: dict, result: dict, profile: dict) -> list[dict]:
    """Check reviewed source amounts and form arithmetic; return closing baskets."""
    year = (result.get("meta") or {}).get("tax_year") or profile.get("tax_year")
    workpaper = section.get("review")
    if (type(year) is not int or profile.get("tax_year", year) != year
            or not isinstance(workpaper, dict) or workpaper.get("tax_year") != year):
        raise ValueError("Form 1116 needs the reviewed workpaper for this return's tax year.")
    method = workpaper.get("method")
    if method not in ("checked", "external") or method == "checked" and year != 2025:
        raise ValueError("Use a reviewed external Form 1116 when this year's form arithmetic is unavailable.")
    categories = workpaper.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("Form 1116 needs a reviewed workpaper for each separate category.")
    prior = profile.get("prior_year") or {}
    opening = carryover_baskets(prior.get("imported_ftc_baskets", []), year)
    if prior.get("unused_ftc_carryforward") and not any(opening.values()):
        raise ValueError("Reconcile the prior foreign tax carryover by category and origin year before using Form 1116: "
                         "import the filed Schedule B into prior_year.imported_ftc_baskets, or set "
                         "prior_year.unused_ftc_carryforward to 0 when none survives.")
    lines = {"year": year, "method": method, "line_18": _line_18(workpaper, result, year),
             "line_20": _line_20(workpaper, result, method)}

    reviewed: set[str] = set()
    paid: list[float] = []
    credits: list[float] = []
    applied: list[float] = []
    created: list[float] = []
    closing = []
    for number, row in enumerate(categories, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Form 1116 category #{number} needs a reviewed object.")
        basket = row.get("basket")
        if not isinstance(basket, str) or basket not in _BASKETS or basket in reviewed:
            raise ValueError("Form 1116 workpaper categories must be recognized and unique.")
        reviewed.add(basket)
        tax, credit, used, balances = _category(number, row, lines, opening.get(basket, {}))
        if balances:
            closing.append({"basket": basket, "carryforward_by_origin_year": balances,
                            "provenance": {"tax_year": year, "source_ref": row["source_ref"]}})
        paid.append(tax)
        credits.append(credit)
        applied.append(used)
        created.append(balances.get(str(year), 0))

    if any(balances and basket not in reviewed for basket, balances in opening.items()):
        raise ValueError("Reconcile each opening Form 1116 carryover category, including any balance not used this year.")
    if round(math.fsum(paid), 2) != _money(section.get("foreign_tax_paid_usd"), "Total foreign tax paid"):
        raise ValueError("Form 1116 category amounts must reconcile to total foreign tax paid.")
    if (round(math.fsum(applied), 2) != _money(section.get("carryover_applied_usd"), "Schedule B line 4 total")
            or round(math.fsum(created), 2) != _money(section.get("carryforward_created_usd"), "Form 1116 new carryforward")):
        raise ValueError("Form 1116 must reconcile the carryover applied and carryforward created by category and year.")
    reduction = _money(workpaper.get("line_34_boycott_reduction"), "Form 1116 line 34")
    allowed = round(min(lines["line_20"], math.fsum(credits)) - reduction, 2)
    if allowed < 0 or allowed != _money(section.get("ftc_allowed"), "Form 1116 line 35"):
        raise ValueError("Form 1116 Part IV must reconcile to the credit on Schedule 3 line 1.")
    return closing


def _category(number: int, row: dict, lines: dict, available: dict[str, float]):
    """One separate category: Part III and its Schedule B. Returns tax, credit, carryover used, closing."""
    year, label = lines["year"], f"Form 1116 category #{number}"
    ref = row.get("source_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError(f"{label} needs the reviewed form or workpaper source.")
    tax = _money(row.get("line_9_tax_paid"), "Form 1116 line 9")
    carry = _origins(row.get("line_10_carryover_by_origin_year"), year - 10, year - 1)
    if any(amount > available.get(origin, 0) for origin, amount in carry.items()):
        raise ValueError(f"{label} exceeds the available carryover for an origin year.")
    eligible = _money(row.get("line_14_eligible_tax"), "Form 1116 line 14")
    if eligible > round(tax + math.fsum(carry.values()), 2):
        raise ValueError(f"{label} overstates eligible foreign tax.")
    income = _money(row.get("line_17_foreign_income"), "Form 1116 line 17", signed=True)
    limitation = _money(row.get("line_23_limitation"), "Form 1116 line 23")
    credit = _money(row.get("line_24_credit"), "Form 1116 line 24")
    if credit != min(eligible, limitation) or (not lines["line_18"] or income <= 0) and credit:
        raise ValueError(f"{label} must reconcile its eligible tax, limitation and credit.")
    if lines["method"] == "checked":
        _check_limitation(label, row, income, limitation, lines)
    used = _origins(row.get("used_carryover_by_origin_year", {}), year - 10, year - 1)
    if any(amount > carry.get(origin, 0) for origin, amount in used.items()):
        raise ValueError(f"{label} overstates carryover used.")
    # Schedule B: current-year taxes available for credit are lines 9, 12 and
    # 13, which is line 14 less line 10. Line 4 uses carryovers only against an
    # excess limitation, oldest origin year first; line 6 is the excess of
    # those taxes over line 23. Both methods follow it.
    current_tax = round(eligible - math.fsum(carry.values()), 2)
    if current_tax < 0:
        raise ValueError(f"{label} needs the reviewed external reconciliation.")
    remaining = max(0.0, round(credit - current_tax, 2))
    for origin, amount in sorted(carry.items()):
        expected = min(amount, remaining)
        if used.get(origin, 0) != expected:
            raise ValueError(f"{label} must use the oldest carryover first, and only against an excess limitation.")
        remaining = round(remaining - expected, 2)
    current_unused = max(0.0, round(current_tax - limitation, 2))
    balances = _origins(row.get("closing_carryforward_by_origin_year"), year - 9, year)
    if row.get("basket") == _NO_CARRYOVER and (carry or balances):
        raise ValueError(f"{label} is section 951A category income, which has no foreign tax carryover.")
    if balances.get(str(year), 0) > current_unused or any(
        balances.get(origin, 0) != round(amount - used.get(origin, 0), 2)
        for origin, amount in carry.items() if int(origin) >= year - 9
    ) or any(origin not in carry and int(origin) != year for origin in balances):
        raise ValueError(f"{label} must reconcile the closing carryforward to Schedule B.")
    return tax, credit, round(math.fsum(used.values()), 2), balances


def _check_limitation(label: str, row: dict, income: float, limitation: float, lines: dict) -> None:
    """Lines 19-23 for the ordinary passive, general and foreign branch categories."""
    if row.get("basket") not in ("foreign_branch", "passive", "general"):
        raise ValueError(f"{label} needs the reviewed external form.")
    line_18 = lines["line_18"]
    ratio = min(1.0, max(0.0, income) / line_18) if line_18 else 0.0
    # The instructions allow a ratio rounded to at least four decimal places.
    # Validate its rounding before checking the recorded tax.
    recorded_ratio = row.get("line_19_ratio")
    if (not isinstance(recorded_ratio, (int, float)) or isinstance(recorded_ratio, bool)
            or not 0 <= recorded_ratio <= 1
            or not math.isfinite(recorded_ratio)
            or not math.isclose(recorded_ratio, ratio, rel_tol=0, abs_tol=0.00005)):
        raise ValueError(f"{label} has a line 19 ratio inconsistent with lines 17 and 18.")
    if abs(limitation - round(recorded_ratio * lines["line_20"], 2)) > 0.50:
        raise ValueError(f"{label} has a line 23 limitation inconsistent with line 21.")
