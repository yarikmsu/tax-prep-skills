#!/usr/bin/env python3
"""North Carolina — Form D-400, full-year resident, with Schedule S (Parts A-B),
the NC Schedule A and D-400TC Parts 1 and 3.

Contract and sources: `docs/contributing/state-module-nc.md`
(raw inputs and computation); cache keys:
`create_state_template("NC")` in `agents/lib/tax_law_fetcher.py`.

EVERY FIGURE IS AN ARGUMENT, read from the fetched `state` block: the flat rate,
the standard deductions, the three child-deduction tables, the Schedule A caps
and floors. Nothing NC publishes is inline (AGENTS.md §2). What IS inline is
form STRUCTURE — which lines exist, how they add, which instruction years this
file was read against — and the four-decimal ratio D-400TC prescribes.

Every refusal is a `ValueError`, as in `ca.py` and `ny.py`, and each one sits
where the alternative would OVER-state a deduction or credit (under-state the
tax), which AGENTS.md §4 makes a hard error. Amounts are carried in CENTS; the
D-400 is filed in whole dollars (D-401 p.4), and the result section's `_note`
says so.
"""

import math
import re
from decimal import Decimal

from agents.lib.tax_computations._shared import (
    _amount,
    _STATE_NAME_TO_CODE,
    _normalize_filing_status,
    _round_half_up,
)


# The D-401 instruction years whose LINE NUMBERING and line arithmetic this file
# was written against. Structural, not a tax figure: Schedule S line numbers and
# the conformity date move by year, and "Schedule S is exhaustive" holds per
# year only (maintained contract). A year not listed is refused rather than mapped by last
# year's lines — read that year's D-401, then add it here with its tests.
_NC_D401_YEARS_READ = frozenset({2025})

# The three child-deduction tables G.S. 105-153.5(a1) has — THREE, not five.
# Reading `qss` or `mfs` from the cache directly finds nothing (the keying trap
# in docs/contributing/tax-law-cache.md), so the mapping is explicit.
_NC_CHILD_TABLE_KEY = {"SINGLE": "single_mfs", "MFS": "single_mfs",
                       "MFJ": "mfj_qss", "QSS": "mfj_qss", "HOH": "hoh"}

# The NC Schedule A items an MFS filer must say who paid (D-401 p.5, p.20).
_NC_SCHED_A_ITEMS = ("mortgage_interest", "real_estate_tax", "charitable",
                     "medical", "claim_of_right_repayment")
_NC_PAID_BY = ("filer", "spouse", "joint_account")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _nc_year(tax_year, what: str) -> int:
    if isinstance(tax_year, bool) or not isinstance(tax_year, int):
        raise ValueError(f"{what}: tax_year must be an int, got {tax_year!r}")
    return tax_year


def _nc_form_year(tax_year, what: str) -> int:
    """Refuse a year whose D-401 this module was not read against."""
    year = _nc_year(tax_year, what)
    if year not in _NC_D401_YEARS_READ:
        raise ValueError(
            f"{what}: the North Carolina module was written against the D-401 "
            f"for {sorted(_NC_D401_YEARS_READ)}, not TY{year}. Schedule S line "
            "numbers and the IRC conformity date are year-gated, so mapping "
            f"TY{year} by an earlier year's lines can drop an addition or "
            "invent a deduction. Read that year's D-401 first.")
    return year


def _nc_bool(value, name: str, *, allow_none: bool = False):
    if value is None and allow_none:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be True or False, got {value!r}")
    return value


def _nc_cfg_node(state_cfg, path: "tuple[str, ...]"):
    node = state_cfg
    for key in path:
        if not isinstance(node, dict):
            node = None
            break
        node = node.get(key)
    return node


def _nc_cfg_figure(state_cfg, path: "tuple[str, ...]", *,
                   rate: bool = False) -> float:
    """A fetched North Carolina figure, refused when it was never fetched.

    A missing figure is never a zero: every caller would read a zero standard
    deduction or cap as a settled answer. `rate` additionally requires a
    decimal share strictly between zero and one: a percentage written as `7.5`
    for `0.075` refuses instead of wiping out a deduction, and a ZERO rate
    refuses because a zero is what an unfetched template field holds — every
    rate read here is a floor, and a zero floor over-states the deduction."""
    where = "state." + ".".join(path)
    value = _nc_cfg_node(state_cfg, path)
    if value is None:
        raise ValueError(
            f"{where} is not in the tax_law cache — it is fetched per year "
            "(AGENTS.md §2). Re-run /tax-setup step 4 for North Carolina; no "
            "default stands in for it.")
    v = _amount(value, where)
    if rate and not 0 < v < 1:
        raise ValueError(
            f"{where} must be a decimal share between 0 and 1 (exclusive), got "
            f"{v!r} — a percentage written as a whole number, or a zero, which "
            "is the unfetched state, not a rate. Re-run /tax-setup.")
    return v


def _nc_conformity_date(state_cfg) -> str:
    """The Code NC's Schedules S and A read (G.S. 105-228.90(b)(7)). An
    unfetched date refuses the year: which Code governs decides whether a
    federal-law change reaches the NC return at all."""
    value = _nc_cfg_node(state_cfg, ("irc_conformity_date",))
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        raise ValueError(
            "state.irc_conformity_date is not fetched (or not a YYYY-MM-DD "
            f"date), got {value!r}. North Carolina reads the Code as of a "
            "fixed date, and without it nothing here knows which Code the "
            "return is computed under. Re-run /tax-setup.")
    return value


def _nc_flat_rate(state_cfg) -> float:
    """The ONE band of `state.brackets` — a flat rate is a one-band schedule."""
    brackets = _nc_cfg_node(state_cfg, ("brackets",))
    if not isinstance(brackets, list) or len(brackets) != 1 \
            or not isinstance(brackets[0], dict):
        raise ValueError(
            "state.brackets must be ONE band for North Carolina's flat rate, "
            f"got {brackets!r}")
    band = brackets[0]
    low = band.get("min")
    # bool first: `False == 0`, so a flag in the `min` slot would pass `!= 0`.
    if isinstance(low, bool) or low != 0 or band.get("max") is not None:
        raise ValueError(
            "North Carolina's single band must run from 0 with a null max, got "
            f"{band!r}")
    if band.get("rate") is None:
        raise ValueError(
            "state.brackets[0].rate is not fetched. The rate is year-gated "
            "(G.S. 105-153.7) — re-run /tax-setup.")
    rate = _amount(band["rate"], "state.brackets[0].rate")
    if not 0 < rate < 1:
        raise ValueError(
            f"state.brackets[0].rate must be a decimal between 0 and 1, got {rate!r}")
    return rate


# ---------------------------------------------------------------------------
# Line 15 — tax
# ---------------------------------------------------------------------------

def compute_nc_tax(nc_taxable_income: float, tax_year: int, state_cfg: dict) -> dict:
    """D-400 line 15: line 14 × the flat rate, zero when line 14 is zero or less
    (D-401 p.14).

    The rate is YEAR-GATED — G.S. 105-153.7(a) "In 2025 4.25%", "After 2025
    3.99%", and (a1)'s revenue triggers cut it further — so the cache's
    `rate_tax_year` must name the year being filed. A TY2025 rate carried onto
    a TY2026 return over-states by 0.26 points; a later year's rate carried
    back under-states. Either way it refuses.
    """
    year = _nc_year(tax_year, "compute_nc_tax")
    line_14 = _amount(nc_taxable_income, "nc_taxable_income", allow_negative=True)
    rate = _nc_flat_rate(state_cfg)
    stamped = _nc_cfg_node(state_cfg, ("rate_tax_year",))
    if isinstance(stamped, bool) or stamped != year:
        raise ValueError(
            f"state.rate_tax_year is {stamped!r} but the return is TY{year}. "
            "North Carolina's rate changes by year (G.S. 105-153.7), so a rate "
            "fetched for another year is not this year's rate. Re-fetch "
            "state.brackets for this year.")
    return {
        "line_14_nc_taxable_income": round(line_14, 2),
        "line_15_nc_tax": round(rate * max(0.0, line_14), 2),
        "rate": rate,
        "notes": [],
    }


# ---------------------------------------------------------------------------
# Line 10 — child deduction
# ---------------------------------------------------------------------------

def _nc_check_child_figure(value, dotted: str) -> None:
    """Validate without formatting cache contents, even in an exception.

    Freshness reports publish these diagnostics verbatim. `_amount` includes
    rejected values in its errors, so it cannot be used at this boundary.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{dotted} must be a number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{dotted} must be finite")
    if value < 0:
        raise ValueError(f"{dotted} cannot be negative")


def _nc_check_child_table(dotted: str, value) -> None:
    """One North Carolina child-deduction table (G.S. 105-153.5(a1)).

    Bands are `{agi_up_to, per_child}`, ascending by `agi_up_to`, whose bound
    is INCLUSIVE ("Up to X"); the top band's is null, because a closed table
    leaves every AGI above it with no row at all. The amount per child never
    RISES with AGI — it is a phase-down — so a rising row is a transposed one.
    """
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError(f"{dotted}: expected at least two "
                         "{agi_up_to, per_child} bands — the deduction "
                         "phases down to an open top band of zero")
    prev_cap = prev_amount = None
    for i, band in enumerate(value):
        if not isinstance(band, dict) or set(band) != {"agi_up_to", "per_child"}:
            raise ValueError(
                f"{dotted}[{i}]: each band is exactly {{agi_up_to, per_child}}")
        cap, amount = band["agi_up_to"], band["per_child"]
        last = i == len(value) - 1
        if cap is None:
            if not last:
                raise ValueError(
                    f"{dotted}[{i}]: only the top band may have agi_up_to null")
        else:
            if last:
                raise ValueError(
                    f"{dotted}[{i}]: the top band's agi_up_to must be null — a "
                    "closed table leaves every AGI above it with no row")
            _nc_check_child_figure(cap, f"{dotted}[{i}].agi_up_to")
            if cap == 0:  # `state.*` admits zero generally; "Up to $0" is no row
                raise ValueError(
                    f"{dotted}[{i}].agi_up_to: zero is not a band's upper bound")
            if prev_cap is not None and not cap > prev_cap:
                raise ValueError(
                    f"{dotted}[{i}]: bands must be ascending by agi_up_to")
            prev_cap = cap
        _nc_check_child_figure(amount, f"{dotted}[{i}].per_child")
        if prev_amount is not None and amount > prev_amount:
            raise ValueError(
                f"{dotted}[{i}]: per_child rises with AGI — the deduction "
                "phases DOWN; a row was misread")
        prev_amount = amount
    # The open top band is where the statute phases the deduction OUT. A
    # non-zero amount there is a truncated table: everyone above the last
    # band read keeps a deduction G.S. 105-153.5(a1) has taken to zero, which
    # UNDER-states the tax.
    if value[-1]["per_child"] != 0:
        raise ValueError(
            f"{dotted}[{len(value) - 1}]: the open top band's per_child must be "
            "0 — a non-zero top is a table read short")


def _nc_child_table(state_cfg, key: str) -> list:
    where = f"state.child_deduction.{key}"
    table = _nc_cfg_node(state_cfg, ("child_deduction", key))
    _nc_check_child_table(where, table)
    return table


def compute_nc_child_deduction(filing_status: str, federal_agi: float,
                               ctc_qualifying_children: int,
                               state_cfg: dict) -> dict:
    """D-400 lines 10a and 10b — the Child Deduction Worksheet (D-401 p.13).

    `ctc_qualifying_children` is the number of children for whom a FEDERAL
    child tax credit was allowed (IRC §24) — never the dependent count: an
    "other dependent" is not a §24 child, and counting one over-states the
    deduction.

    The table is keyed by FEDERAL AGI (line 6), and each band's upper bound is
    INCLUSIVE ("Up to $40,000", "Over $40,000 - Up to $60,000"). A negative AGI
    lands in the first band.
    """
    status = _normalize_filing_status(filing_status)
    agi = _amount(federal_agi, "federal_agi", allow_negative=True)
    n = ctc_qualifying_children
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError(
            "ctc_qualifying_children must be a non-negative int (the §24 "
            f"child count from the federal child tax credit), got {n!r}")
    key = _NC_CHILD_TABLE_KEY[status]
    table = _nc_child_table(state_cfg, key)
    per_child = 0.0
    for band in table:
        top = band["agi_up_to"]
        if top is None or agi <= top:
            per_child = float(band["per_child"])
            break
    return {
        "line_10a_children": n,
        "line_10b_child_deduction": round(n * per_child, 2),
        "per_child": per_child,
        "table": key,
        "notes": [],
    }


# ---------------------------------------------------------------------------
# Schedule S — Parts A and B, the v1 lines
# ---------------------------------------------------------------------------

def _nc_share(value, name: str) -> float:
    v = _amount(value, name)
    if v > 1:
        raise ValueError(f"{name} must be a decimal share between 0 and 1, got "
                         f"{value!r} (a percentage written as a whole number?)")
    return v


def compute_nc_schedule_s(
    tax_year: int,
    state_cfg: dict,
    *,
    government_retirement_1099r: bool,
    uniformed_services_retirement_1099r: bool,
    exempt_interest: float,
    nc_obligation_share: "float | None" = None,
    state_local_refund_in_agi: float,
    us_obligation_interest: float,
    us_government_fund_dividends: float,
    us_government_share: "float | None" = None,
    taxable_social_security: float,
    railroad_tier2_in_agi: float,
    bailey_retirement: "float | None" = None,
    uniformed_services_retirement: "float | None" = None,
    government_retirement_taxable: "float | None" = None,
) -> dict:
    """Schedule S lines 1, 16, 17-21 and 41 (D-401 pp.17-19).

    The six AMOUNTS (`exempt_interest`, `state_local_refund_in_agi`,
    `us_obligation_interest`, `us_government_fund_dividends`,
    `taxable_social_security`, `railroad_tier2_in_agi`) are REQUIRED keyword
    arguments with no default — pass 0 for none. A default of zero is an
    answer nobody gave: an omitted deduction over-states NC tax, and an omitted
    line-1 addition under-states it with nothing to say so.

    - **Line 1** — exempt interest from non-NC state and local obligations,
      exempt-interest dividends included "to the extent such dividends do not
      represent interest from obligations of North Carolina". The NC SHARE is
      a required input whenever there is exempt interest: treating all of it as
      non-NC over-states tax, treating all of it as NC under-states it, and only
      the caller's fund statements can say which.
    - **Line 17** — the state or local refund included in federal AGI.
    - **Line 18** — US-obligation interest: `us_obligation_interest` is the
      direct figure (1099-INT box 3); `us_government_fund_dividends` are fund
      dividends whose US-government share must be supplied with them.
    - **Line 19** — taxable Social Security (Form 1040 line 6b, which carries
      RRB-1099 tier 1) plus tier 2 railroad retirement in AGI.
    - **Lines 20 and 21** — Bailey and uniformed-services retirement. Their
      eligibility is the taxpayer's assertion (spec §8), so they are inputs —
      but the two PRESENCE flags are REQUIRED: a government or military 1099-R
      with no answer refuses, because omitting a deduction the taxpayer is
      owed over-states NC tax. A military 1099-R needs both answers (a retiree
      vested by 12 Aug 1989 deducts under Bailey, others under line 21, never
      both on the same income). Any amount claimed needs
      `government_retirement_taxable` to bound it: the TAXABLE amount (box 2a)
      of the government and uniformed-services 1099-Rs ONLY. Not Form 1040
      lines 4b + 5b, which carry private IRA and 401(k) income these two lines
      can never deduct — bounding by them lets a Bailey claim spill onto
      private-plan income.

    Every other Schedule S line is out of v1 scope (spec §8) and is gated at
    the capability step, not here.
    """
    _nc_form_year(tax_year, "compute_nc_schedule_s")
    conformity = _nc_conformity_date(state_cfg)
    gov = _nc_bool(government_retirement_1099r, "government_retirement_1099r")
    mil = _nc_bool(uniformed_services_retirement_1099r,
                   "uniformed_services_retirement_1099r")
    notes: "list[str]" = []

    exempt = _amount(exempt_interest, "exempt_interest")
    if exempt > 0 and nc_obligation_share is None:
        raise ValueError(
            f"${exempt:,.2f} of exempt interest needs nc_obligation_share — the "
            "share from North Carolina obligations (Schedule S line 1). Treating "
            "all of it as non-NC over-states NC tax and treating all of it as NC "
            "under-states it; the fund statements say which.")
    nc_share = (_nc_share(nc_obligation_share, "nc_obligation_share")
                if nc_obligation_share is not None else 0.0)
    line_1 = round(exempt * (1 - nc_share), 2)

    refund = _amount(state_local_refund_in_agi, "state_local_refund_in_agi")
    us_direct = _amount(us_obligation_interest, "us_obligation_interest")
    fund_divs = _amount(us_government_fund_dividends, "us_government_fund_dividends")
    if fund_divs > 0 and us_government_share is None:
        raise ValueError(
            f"${fund_divs:,.2f} of fund dividends were passed for Schedule S "
            "line 18 without us_government_share — the fund's published share "
            "of income from US obligations. Without it the deduction is a "
            "guess in one direction or the other.")
    us_share = (_nc_share(us_government_share, "us_government_share")
                if us_government_share is not None else 0.0)
    line_18 = round(us_direct + fund_divs * us_share, 2)

    ss = _amount(taxable_social_security, "taxable_social_security")
    tier2 = _amount(railroad_tier2_in_agi, "railroad_tier2_in_agi")
    line_19 = round(ss + tier2, 2)

    if (gov or mil) and bailey_retirement is None:
        raise ValueError(
            "A government or military 1099-R is present but bailey_retirement "
            "is unanswered. Schedule S line 20 (Bailey) excludes benefits of "
            "retirees vested by 12 Aug 1989; omitting it over-states NC tax. "
            "Ask the taxpayer and pass the amount, or 0 if not eligible.")
    if mil and uniformed_services_retirement is None:
        raise ValueError(
            "A uniformed-services 1099-R is present but "
            "uniformed_services_retirement is unanswered. Schedule S line 21 "
            "deducts it for 20-year or medically retired members (and Survivor "
            "Benefit Plan payments); omitting it over-states NC tax. Pass the "
            "amount, or 0 if not eligible.")
    line_20 = (_amount(bailey_retirement, "bailey_retirement")
               if bailey_retirement is not None else 0.0)
    line_21 = (_amount(uniformed_services_retirement,
                       "uniformed_services_retirement")
               if uniformed_services_retirement is not None else 0.0)
    if line_20 > 0 and not (gov or mil):
        raise ValueError(
            "bailey_retirement is claimed with no government or military "
            "1099-R recorded; the deduction is only for retirement benefits "
            "those plans pay (D-401 p.18), and the 1099-R must be attached.")
    if line_21 > 0 and not mil:
        raise ValueError(
            "uniformed_services_retirement is claimed with no uniformed-services "
            "1099-R recorded (D-401 pp.18-19).")
    gov_taxable = (_amount(government_retirement_taxable,
                           "government_retirement_taxable")
                   if government_retirement_taxable is not None else None)
    if gov_taxable is not None and gov_taxable > 0 and not (gov or mil):
        raise ValueError(
            "government_retirement_taxable is non-zero but neither a "
            "government nor a uniformed-services 1099-R is recorded; one of "
            "the two answers is wrong, and a wrong presence flag drops the "
            "Bailey and line 21 questions.")
    if line_20 + line_21 > 0:
        if gov_taxable is None:
            raise ValueError(
                "Schedule S lines 20-21 need government_retirement_taxable — "
                "the taxable amount (box 2a) of the government and "
                "uniformed-services 1099-Rs only — to bound them. A deduction "
                "larger than that income over-states it, and Form 1040 lines "
                "4b + 5b are no bound: they include private IRA and 401(k) "
                "income.")
        if round(line_20 + line_21, 2) > gov_taxable:
            raise ValueError(
                f"Schedule S lines 20 + 21 (${line_20 + line_21:,.2f}) exceed "
                f"the taxable government and uniformed-services retirement "
                f"income (${gov_taxable:,.2f}). The same income may not be "
                "deducted twice (D-401 p.19), and none beyond what those "
                "1099-Rs put in AGI.")
        notes.append(
            "Schedule S lines 20/21 rest on the taxpayer's eligibility "
            "assertion (Bailey vesting by 12 Aug 1989; 20-year or medical "
            "uniformed-services retirement) — attach the 1099-R.")

    line_16 = line_1
    line_41 = round(refund + line_18 + line_19 + line_20 + line_21, 2)
    return {
        "line_1_non_nc_obligation_interest": line_1,
        "line_16_total_additions": round(line_16, 2),
        "line_17_state_local_refund": round(refund, 2),
        "line_18_us_obligation_interest": line_18,
        "line_19_taxable_social_security": line_19,
        "line_20_bailey_retirement": round(line_20, 2),
        "line_21_uniformed_services_retirement": round(line_21, 2),
        "line_41_total_deductions": line_41,
        "irc_conformity_date": conformity,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# NC Schedule A
# ---------------------------------------------------------------------------

def compute_nc_itemized(
    filing_status: str,
    federal_agi: float,
    state_cfg: dict,
    *,
    mortgage_interest: float = 0.0,
    real_estate_tax: float = 0.0,
    charitable: float = 0.0,
    medical: float = 0.0,
    claim_of_right_repayment: float = 0.0,
    claim_of_right_eligible: "bool | None" = None,
    mortgage_insurance_premiums: float = 0.0,
    mortgage_over_acquisition_limit: "bool | None" = None,
    home_equity_debt: "bool | None" = None,
    charitable_agi_limit_may_bind: "bool | None" = None,
    paid_by: "dict | None" = None,
    spouse_mortgage_and_property_tax: "float | None" = None,
) -> dict:
    """NC Schedule A lines 1-10 (D-401 p.20), from RAW inputs.

    Never from the federal Schedule A: NC reads the Code as of
    `state.irc_conformity_date`, and a federal figure computed under later law
    is wrong in BOTH directions (spec §2). The raw figures are: 1098 mortgage
    interest, real-estate tax BEFORE any federal SALT cap, gross gifts, and
    medical expenses.

    REFUSES where a raw input cannot settle the conformity-date Code answer:
    any 1098 box 5 premium (`mortgage_insurance_premiums`), mortgage debt over
    that Code's acquisition limit, home-equity debt, and gifts the §170 AGI
    percentage limits may reach. The last three are the CALLER's answers,
    REQUIRED whenever the matching amount is non-zero: the engine holds no
    copy of the Code's dollar or percentage limits, and an unanswered one is
    not a "no".

    A non-zero `claim_of_right_repayment` also requires
    `claim_of_right_eligible=True`: the caller certifies the amount was
    included in an earlier year's federal AGI under an unrestricted right,
    excludes every dollar already deducted in the current federal AGI, and
    no federal §1341(a)(5) credit was claimed. False or unanswered refuses;
    an ineligible repayment must never become a second deduction (D-401
    p.20; G.S. 105-153.5(a)(2)d). Mixed eligible/ineligible repayments need
    a manual line 8 calculation rather than passing the gross repayment.

    MARRIED FILING SEPARATELY: an item is claimable only by the spouse who was
    obligated for it AND paid it (D-401 p.5, p.20), so `paid_by` names, for
    every non-zero item, "filer" — the only value that is claimed. "spouse"
    refuses (the item is on the spouse's return), and "joint_account" refuses
    because the income-share proration D-401 p.20 prescribes for it is not
    modelled. The line 4 limit is HOUSEHOLD-WIDE even for MFS
    (G.S. 105-153.5(a2)), so `spouse_mortgage_and_property_tax` is required
    when this filer has either item: the home mortgage interest and real-estate
    tax the SPOUSE actually PAID, before the line 2 real-estate cap. D-401 p.20
    line 4: "If the amount of home mortgage interest and real estate property
    taxes paid by both spouses exceeds twenty thousand dollars ($20,000), the
    deductions must be prorated based on the percentage paid by each spouse."
    Both the test and the share are on amounts PAID, so this filer's share is
    (line 1 + real-estate tax paid) over the household's, never line 3 after
    the cap.

    Every boolean answer and the MFS spouse figure are validated whenever
    passed, not only when the amount they qualify is non-zero.
    """
    status = _normalize_filing_status(filing_status)
    conformity = _nc_conformity_date(state_cfg)
    agi = _amount(federal_agi, "federal_agi", allow_negative=True)
    amounts = {
        "mortgage_interest": _amount(mortgage_interest, "mortgage_interest"),
        "real_estate_tax": _amount(real_estate_tax, "real_estate_tax"),
        "charitable": _amount(charitable, "charitable"),
        "medical": _amount(medical, "medical"),
        "claim_of_right_repayment": _amount(claim_of_right_repayment,
                                            "claim_of_right_repayment"),
    }
    premiums = _amount(mortgage_insurance_premiums, "mortgage_insurance_premiums")
    notes: "list[str]" = []
    answers = {
        name: _nc_bool(value, name, allow_none=True)
        for name, value in (
            ("mortgage_over_acquisition_limit", mortgage_over_acquisition_limit),
            ("home_equity_debt", home_equity_debt),
            ("charitable_agi_limit_may_bind", charitable_agi_limit_may_bind),
            ("claim_of_right_eligible", claim_of_right_eligible))}
    spouse_paid = None
    if spouse_mortgage_and_property_tax is not None:
        if status != "MFS":
            raise ValueError(
                "spouse_mortgage_and_property_tax applies to married filing "
                "separately only")
        spouse_paid = _amount(spouse_mortgage_and_property_tax,
                              "spouse_mortgage_and_property_tax")

    # --- the cases the raw inputs cannot settle -----------------------------
    if premiums > 0:
        raise ValueError(
            f"A 1098 box 5 mortgage insurance premium (${premiums:,.2f}) is "
            f"present. Whether it is §163(h) interest depends on the Code as of "
            f"{conformity}, not on current federal law, and this module does "
            "not decide it. Compute Schedule A line 1 by hand or acknowledge "
            "the limitation.")
    if amounts["mortgage_interest"] > 0:
        for name in ("mortgage_over_acquisition_limit", "home_equity_debt"):
            answer = answers[name]
            if answer is None:
                raise ValueError(
                    f"Mortgage interest is claimed but {name} is unanswered. "
                    f"Schedule A line 1 is interest allowed under §163(h) as of "
                    f"{conformity}; an unanswered limit is not a 'no' — taking "
                    "the 1098 figure whole can over-state the deduction.")
            if answer:
                raise ValueError(
                    f"{name} is True: the §163(h) limit as of {conformity} "
                    "cuts the deductible interest, and the proration is not "
                    "modelled here. Compute Schedule A line 1 by hand.")
    if amounts["charitable"] > 0:
        answer = answers["charitable_agi_limit_may_bind"]
        if answer is None or answer:
            raise ValueError(
                "Charitable gifts are claimed and charitable_agi_limit_may_bind "
                f"is {answer!r}. The §170(b) AGI percentage limits as of "
                f"{conformity} differ from current federal law, and this module "
                "holds no copy of them; pass False only when the gifts are "
                "plainly below every percentage limit, otherwise compute "
                "Schedule A line 6 by hand.")

    # --- MFS: who paid ---------------------------------------------------------
    if status == "MFS":
        given = paid_by or {}
        if not isinstance(given, dict):
            raise ValueError(f"paid_by must be a dict, got {paid_by!r}")
        unknown = sorted(set(given) - set(_NC_SCHED_A_ITEMS))
        if unknown:
            raise ValueError(f"paid_by names unknown items {unknown}; known: "
                             f"{list(_NC_SCHED_A_ITEMS)}")
        for item in _NC_SCHED_A_ITEMS:
            if amounts[item] <= 0:
                continue
            who = given.get(item)
            if who not in _NC_PAID_BY:
                raise ValueError(
                    f"Married filing separately: paid_by[{item!r}] is {who!r}. "
                    "Only the spouse obligated for an item who actually paid it "
                    "may claim it (D-401 p.5, p.20); say 'filer', 'spouse' or "
                    "'joint_account'.")
            if who == "spouse":
                raise ValueError(
                    f"paid_by[{item!r}] is 'spouse': the item belongs on the "
                    "spouse's return, and claiming it here over-states this "
                    "filer's deduction. Pass 0 for it.")
            if who == "joint_account":
                raise ValueError(
                    f"paid_by[{item!r}] is 'joint_account': D-401 p.20 prorates "
                    "jointly paid items by each spouse's income, which is not "
                    "modelled. Split the item by hand and pass this filer's "
                    "part as paid by 'filer'.")
    elif paid_by is not None:
        raise ValueError("paid_by applies to married filing separately only")

    # --- lines 1-5: mortgage interest and real-estate tax ---------------------
    re_cap = _nc_cfg_figure(state_cfg, ("itemized", "real_estate_tax_cap",
                                        "mfs" if status == "MFS" else "default"))
    combined_cap = _nc_cfg_figure(state_cfg,
                                  ("itemized", "mortgage_plus_property_tax_cap"))
    line_1 = round(amounts["mortgage_interest"], 2)
    line_2 = round(min(amounts["real_estate_tax"], re_cap), 2)
    line_3 = round(line_1 + line_2, 2)
    line_4 = combined_cap
    if status == "MFS" and line_3 > 0:
        if spouse_paid is None:
            raise ValueError(
                "Married filing separately: the Schedule A line 4 limit is "
                "HOUSEHOLD-WIDE (G.S. 105-153.5(a2)), so "
                "spouse_mortgage_and_property_tax — the mortgage interest and "
                "real-estate tax the spouse actually paid, before any cap — is "
                "required. Assuming the spouse paid none can give this filer "
                "the whole limit twice.")
        # D-401 p.20 line 4: the test and the percentage are both on amounts
        # PAID by each spouse, so this filer's real-estate tax enters before
        # the line 2 cap.
        filer_paid = amounts["mortgage_interest"] + amounts["real_estate_tax"]
        household = filer_paid + spouse_paid
        if household > combined_cap:
            line_4 = round(combined_cap * filer_paid / household, 2)
            notes.append(
                f"MFS: mortgage interest and real-estate tax paid by both "
                f"spouses (${household:,.2f}) exceed the household limit, "
                f"prorated by the percentage each paid (D-401 p.20) — this "
                f"filer's limit is ${line_4:,.2f}.")
    line_5 = round(min(line_3, line_4), 2)

    # --- line 6: charitable ---------------------------------------------------
    line_6 = round(amounts["charitable"], 2)

    # --- line 7: medical over the floor on FEDERAL AGI ------------------------
    floor_rate = _nc_cfg_figure(state_cfg, ("itemized", "medical_agi_floor_rate"),
                                rate=True)
    line_7a = round(amounts["medical"], 2)
    line_7b = round(agi, 2)
    line_7c = round(max(0.0, line_7b * floor_rate), 2)
    line_7d = round(max(0.0, line_7a - line_7c), 2)

    # --- line 8: claim of right -----------------------------------------------
    repaid = amounts["claim_of_right_repayment"]
    line_8 = 0.0
    if repaid > 0:
        if answers["claim_of_right_eligible"] is not True:
            raise ValueError(
                "claim_of_right_eligible must be explicitly True for a "
                "non-zero repayment: certify prior-year claim-of-right "
                "income, no repayment deducted in current federal AGI, "
                "and no federal section 1341(a)(5) credit. Otherwise "
                "prepare Schedule A line 8 by hand; claiming the raw "
                "repayment can deduct it twice.")
        threshold = _nc_cfg_figure(state_cfg, ("itemized", "claim_of_right",
                                               "repayment_threshold"))
        cor_rate = _nc_cfg_figure(state_cfg, ("itemized", "claim_of_right",
                                              "agi_rate"), rate=True)
        if repaid > threshold:
            line_8 = round(repaid, 2)
        else:
            line_8 = round(max(0.0, repaid - round(max(0.0, agi) * cor_rate, 2)), 2)

    line_9 = 0.0     # reserved for future use (D-401 p.20)
    line_10 = round(line_5 + line_6 + line_7d + line_8 + line_9, 2)
    if not math.isfinite(line_10):
        raise ValueError("NC itemized deductions sum to a non-finite total")
    notes.append(
        f"NC Schedule A computed from raw inputs under the Code as of "
        f"{conformity}; North Carolina allows no state or local income or "
        "sales tax deduction.")
    return {
        "line_1_mortgage_interest": line_1,
        "line_2_real_estate_tax": line_2,
        "line_3": line_3,
        "line_4_mortgage_and_property_tax": round(line_4, 2),
        "line_5": line_5,
        "line_6_charitable": line_6,
        "line_7a": line_7a,
        "line_7b": line_7b,
        "line_7c": line_7c,
        "line_7d_medical": line_7d,
        "line_8_claim_of_right": line_8,
        "line_9": line_9,
        "line_10": line_10,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Line 11 — standard or itemized
# ---------------------------------------------------------------------------

def compute_nc_deduction_choice(
    filing_status: str,
    itemized_total: float,
    state_cfg: dict,
    *,
    spouse_itemizes_nc: "bool | None",
    claimed_as_dependent: bool,
    eligible_for_federal_standard_deduction: bool,
) -> dict:
    """D-400 line 11: the larger of the NC standard deduction and the NC
    itemized total (D-401 p.14).

    The standard deduction is ZERO for an MFS filer whose spouse claims NC
    itemized deductions (the chart's own row, read from
    `state.standard_deduction.mfs_spouse_itemizes`) and for anyone "not
    eligible for the federal standard deduction". There is no age-65 or blind
    addition.

    Every input the election turns on is REQUIRED. `spouse_itemizes_nc` for MFS:
    assuming "no" over-states the deduction by the whole standard amount, and
    assuming "yes" under-states it. A DEPENDENT filer refuses: G.S.
    105-153.5(a)(1) states no dependent limit and the D-401 is silent, so the
    full amount stands on the statute's face — but that is unconfirmed by NCDOR
    guidance (spec §2), and granting it wrongly under-states tax while refusing
    costs nothing.
    """
    status = _normalize_filing_status(filing_status)
    itemized = _amount(itemized_total, "itemized_total")
    dependent = _nc_bool(claimed_as_dependent, "claimed_as_dependent")
    eligible = _nc_bool(eligible_for_federal_standard_deduction,
                        "eligible_for_federal_standard_deduction")
    spouse = _nc_bool(spouse_itemizes_nc, "spouse_itemizes_nc", allow_none=True)
    if dependent:
        raise ValueError(
            "A filer who can be claimed as a dependent is refused: G.S. "
            "105-153.5(a)(1) names no dependent limit for the NC standard "
            "deduction and the D-401 is silent, so the amount is unconfirmed. "
            "Confirm NCDOR guidance before computing this return.")
    if status == "MFS" and spouse is None:
        raise ValueError(
            "Married filing separately needs spouse_itemizes_nc: the NC "
            "standard deduction is zero when the spouse claims NC itemized "
            "deductions (D-401 p.14). Unknown is not 'no'.")
    if status != "MFS" and spouse is not None:
        raise ValueError("only married filing separately takes "
                         "spouse_itemizes_nc")
    notes: "list[str]" = []
    if not eligible:
        standard = 0.0
        notes.append("Not eligible for the federal standard deduction — the NC "
                     "standard deduction is zero (D-401 p.14).")
    elif status == "MFS" and spouse:
        standard = _nc_cfg_figure(state_cfg, ("standard_deduction",
                                              "mfs_spouse_itemizes"))
        notes.append("MFS with a spouse claiming NC itemized deductions — the "
                     "chart's spouse-itemizes amount applies (D-401 p.14).")
    else:
        standard = _nc_cfg_figure(state_cfg, ("standard_deduction", status.lower()))
    method = "itemized" if itemized > standard else "standard"
    return {
        "line_11_deduction": round(max(itemized, standard), 2),
        "line_11_method": method,
        "standard_deduction": round(standard, 2),
        "itemized_total": round(itemized, 2),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Line 16 — D-400TC Part 1 (credit for tax paid to another state) and Part 3
# ---------------------------------------------------------------------------

# Supported national jurisdictions: ISO alpha-2 identifiers for UN members
# and observers, excluding the US. Identity data, never tax figures; reference:
# https://unstats.un.org/unsd/methodology/m49/overview/
# A country/area code alone does not establish eligibility: ISO also assigns
# codes to dependencies. Those and other unlisted jurisdictions require manual
# review rather than assuming they are a qualifying country (D-401 p.22).
_NC_CREDIT_COUNTRIES = frozenset("""
    AD AE AF AG AL AM AO AR AT AU AZ BA BB BD BE BF BG BH BI BJ BN BO BR BS BT
    BW BY BZ CA CD CF CG CH CI CL CM CN CO CR CU CV CY CZ DE DJ DK DM DO DZ EC
    EE EG ER ES ET FI FJ FM FR GA GB GD GE GH GM GN GQ GR GT GW GY HN HR HT HU
    ID IE IL IN IQ IR IS IT JM JO JP KE KG KH KI KM KN KP KR KW KZ LA LB LC LI
    LK LR LS LT LU LV LY MA MC MD ME MG MH MK ML MM MN MR MT MU MV MW MX MY MZ
    NA NE NG NI NL NO NP NR NZ OM PA PE PG PH PK PL PS PT PW PY QA RO RS RU RW
    SA SB SC SD SE SG SI SK SL SM SN SO SR SS ST SV SY SZ TD TG TH TJ TL TM TN
    TO TR TT TV TZ UA UG UY UZ VA VC VE VN VU WS YE ZA ZM ZW
""".split())
_NC_CREDIT_STATE_CODES = frozenset(_STATE_NAME_TO_CODE.values())


def _nc_credit_jurisdiction(value, where: str) -> str:
    """Resolve identities before granting or deduplicating any credit.

    Bare codes and full names mean US states/DC; `country:CA` means Canada,
    while `CA` means California. Foreign country names/aliases are refused so
    the same country cannot acquire multiple worksheets. No city, county or
    province can be smuggled in as an arbitrary non-empty label.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} has no jurisdiction")
    key = value.strip().upper()
    state = _STATE_NAME_TO_CODE.get(key.lower(), key)
    if state == "NC":
        raise ValueError("North Carolina is not 'another state' (D-400TC Part 1)")
    if state in _NC_CREDIT_STATE_CODES:
        return state
    if key.startswith("COUNTRY:") and key[8:] in _NC_CREDIT_COUNTRIES:
        return f"country:{key[8:]}"
    raise ValueError(
        f"{where}: jurisdiction must identify another US state/DC by code "
        "or full name, or a supported national jurisdiction as country:XX "
        "(ISO alpha-2). City, county, province and federal taxes do not "
        "qualify; other jurisdictions require a manual D-400TC review.")


def compute_nc_other_state_credit(line_15: float,
                                  income_all_sources: "float | None" = None,
                                  other_states: "list | None" = None) -> dict:
    """D-400TC Part 1 (D-401 pp.22-23), one Out-of-State Tax Credit Worksheet
    per state or country, and Part 3's lines 16-20 into D-400 line 16.

    Per worksheet: line 3 = line 2 / line 1 rounded to FOUR decimals (as the
    form says), line 5 = D-400 line 15 × line 3, line 7 = the lesser of line 5
    and line 6 (net tax paid). Inputs are the taxpayer's other-state return
    figures; nothing is inferred, and a withholding statement alone does not
    support the credit.

    `income_all_sources` is worksheet line 1 — income from all sources while an
    NC resident, modified by the Schedule S items that relate to gross income —
    and is the same on every worksheet; it is validated whenever passed. Each
    `other_states` row is `{"jurisdiction", "income_taxed", "net_tax_paid"}`.
    `jurisdiction` accepts another US state's/DC's code or full name, or
    `country:XX` for a supported country's ISO alpha-2 code. Only national
    country taxes qualify, never a province/city/county levy. The result
    carries canonical identities, and duplicate aliases refuse. Unlisted
    national jurisdictions require manual review.
    `other_states` is a LIST: `None` and `[]` both mean "no other state" (no
    worksheet, no credit), and anything else — `""`, `{}`, a dict for one
    state — refuses rather than being read as an empty claim.

    Line 3 rounds HALF-UP, as the form's "round to four decimal places" reads,
    from the exact quotient: Python's `round` is half-even on the binary value
    and turns 9 / 4,000 into 0.0022 where the form says 0.0023.

    Part 3 in v1: lines 8-13 (historic rehabilitation), 14 (carryovers) and 19
    (NC-478) are out of scope (spec §8), so line 16 = line 7a, line 18 = the
    lesser of it and D-400 line 15, and line 20 = line 18 — the credit can
    never exceed the tax.
    """
    tax = _amount(line_15, "line_15")
    if other_states is None:
        other_states = []
    if not isinstance(other_states, list):
        raise ValueError(
            "other_states must be a list of worksheet rows (None or [] for no "
            f"other state), got {other_states!r}")
    rows = other_states
    part_1: "list[dict]" = []
    credit_total = 0.0
    notes: "list[str]" = []
    seen: "set[str]" = set()
    total_income = (None if income_all_sources is None else
                    _amount(income_all_sources, "income_all_sources",
                            allow_negative=True))
    if rows and total_income is None:
        raise ValueError(
            "income_all_sources (D-400TC line 1) is required to claim a "
            "credit for tax paid to another state")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"other_states[{i}] must be an object")
        name = _nc_credit_jurisdiction(row.get("jurisdiction"),
                                       f"other_states[{i}].jurisdiction")
        if name in seen:
            raise ValueError(
                f"{name!r} appears twice; one worksheet per state or country, "
                "and a second one double-counts the credit")
        seen.add(name)
        taxed = _amount(row.get("income_taxed"), f"other_states[{i}].income_taxed")
        paid = _amount(row.get("net_tax_paid"), f"other_states[{i}].net_tax_paid")
        assert total_income is not None
        # Also covers a line 1 of zero or less with income taxed elsewhere:
        # the ratio has no meaning there, and taxed > line 1 already holds.
        if taxed > total_income:
            raise ValueError(
                f"other_states[{i}]: the income taxed by {name} "
                f"({taxed:,.2f}) exceeds D-400TC line 1 ({total_income:,.2f}); "
                "a ratio above one over-states the credit.")
        ratio = (float(_round_half_up(
            Decimal(repr(taxed)) / Decimal(repr(total_income)), 4))
            if taxed > 0 else 0.0)
        line_5 = round(tax * ratio, 2)
        credit = round(min(line_5, paid), 2)
        credit_total += credit
        part_1.append({"jurisdiction": name, "line_1": round(total_income, 2),
                       "line_2": round(taxed, 2), "line_3": ratio,
                       "line_4": round(tax, 2), "line_5": line_5,
                       "line_6": round(paid, 2), "line_7": credit})
    line_7a = round(credit_total, 2)
    line_16 = line_7a
    line_18 = round(min(line_16, tax), 2)
    if line_18 < line_16:
        notes.append(
            f"D-400TC Part 3: credits of ${line_16:,.2f} are capped at the "
            f"North Carolina tax of ${tax:,.2f} (line 18).")
    if part_1:
        notes.append("D-400TC Part 1: attach each other state's return and "
                     "proof of payment (D-401 p.23).")
    return {
        "part_1": part_1,
        "line_7a": line_7a,
        "line_7b": len(part_1),
        "line_16": line_16,
        "line_17": round(tax, 2),
        "line_18": line_18,
        "line_19": 0.0,
        "line_20": line_18,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Form D-400, lines 6-34
# ---------------------------------------------------------------------------

def compute_nc_d400(
    tax_year: int,
    filing_status: str,
    federal_agi: float,
    state_cfg: dict,
    *,
    schedule_s: dict,
    ctc_qualifying_children: int,
    itemized: "dict | None",
    spouse_itemizes_nc: "bool | None",
    claimed_as_dependent: bool,
    eligible_for_federal_standard_deduction: bool,
    other_state_credit: "dict | None" = None,
    use_tax: "float | None" = None,
    use_tax_certified_none: bool = False,
    withholding: float = 0.0,
    spouse_withholding: float = 0.0,
    estimated_payments: float = 0.0,
    extension_payment: float = 0.0,
    penalties: float = 0.0,
    interest: float = 0.0,
    underpayment_interest: float = 0.0,
    applied_to_next_year: float = 0.0,
    wildlife_fund: float = 0.0,
    education_fund: float = 0.0,
    cancer_program: float = 0.0,
    federal_extension_circle: "bool | None" = None,
    out_of_country_circle: "bool | None" = None,
) -> dict:
    """Form D-400 lines 6-34 for a FULL-YEAR resident (D-401 pp.12-16), in the
    `north_carolina_d400` field names.

    `schedule_s` is the keyword arguments of `compute_nc_schedule_s` (its
    presence flags included), `itemized` those of `compute_nc_itemized` or None
    when no NC Schedule A is computed, and `other_state_credit`
    `{"income_all_sources", "other_states"}` for D-400TC Part 1.

    LINE 18 (use tax) is an INPUT: `use_tax` an amount, or
    `use_tax_certified_none=True` to certify none is due. Neither → line 18 and
    `use_tax_none_due_circle` are both `None` (unanswered, never 0 or False)
    with a loud note, and the payment lines are computed as if it were nothing
    — the amount due may then be UNDER-stated, which the note says.

    Settlement (D-401 pp.15-16): line 23 = 20a + 20b + 21a + 21b, line 25 =
    line 23 (lines 21c, 21d, 22 and 24 are nonresident or amended-only). Tax due
    26a = 19 − 25 when 25 is less; 26d = 26b + 26c; 27 = 26a + 26d + 26e. An
    overpayment, line 28, is 25 − 19 − 26e, and any 26e it cannot absorb moves
    to line 27. Lines 29-32 are passed through, 33 is their total and cannot
    exceed line 28, and 34 = 28 − 33.
    """
    year = _nc_form_year(tax_year, "compute_nc_d400")
    status = _normalize_filing_status(filing_status)
    agi = _amount(federal_agi, "federal_agi", allow_negative=True)
    notes: "list[str]" = []

    if not isinstance(schedule_s, dict):
        raise ValueError("schedule_s must be the compute_nc_schedule_s keyword "
                         "arguments, as a dict")
    sched_s = compute_nc_schedule_s(year, state_cfg, **schedule_s)
    notes += sched_s["notes"]
    line_7 = sched_s["line_16_total_additions"]
    line_8 = round(agi + line_7, 2)
    line_9 = sched_s["line_41_total_deductions"]

    child = compute_nc_child_deduction(status, agi, ctc_qualifying_children,
                                       state_cfg)

    sched_a = None
    itemized_total = 0.0
    if itemized is not None:
        if not isinstance(itemized, dict):
            raise ValueError("itemized must be the compute_nc_itemized keyword "
                             "arguments, as a dict, or None")
        sched_a = compute_nc_itemized(status, agi, state_cfg, **itemized)
        notes += sched_a["notes"]
        itemized_total = sched_a["line_10"]
    choice = compute_nc_deduction_choice(
        status, itemized_total, state_cfg,
        spouse_itemizes_nc=spouse_itemizes_nc,
        claimed_as_dependent=claimed_as_dependent,
        eligible_for_federal_standard_deduction=eligible_for_federal_standard_deduction)
    notes += choice["notes"]
    line_11 = choice["line_11_deduction"]

    line_12a = round(line_9 + child["line_10b_child_deduction"] + line_11, 2)
    line_12b = round(line_8 - line_12a, 2)
    line_14 = line_12b
    tax = compute_nc_tax(line_14, year, state_cfg)
    line_15 = tax["line_15_nc_tax"]

    osc = {} if other_state_credit is None else other_state_credit
    if not isinstance(osc, dict) or set(osc) - {"income_all_sources", "other_states"}:
        raise ValueError("other_state_credit must be {'income_all_sources', "
                         f"'other_states'}}, got {other_state_credit!r}")
    credits = compute_nc_other_state_credit(line_15, osc.get("income_all_sources"),
                                            osc.get("other_states"))
    notes += credits["notes"]
    line_16 = credits["line_20"]
    line_17 = round(line_15 - line_16, 2)

    certified = _nc_bool(use_tax_certified_none, "use_tax_certified_none")
    if use_tax is not None:
        use_amount = _amount(use_tax, "use_tax")
        if certified and use_amount > 0:
            raise ValueError(
                "use_tax is an amount and use_tax_certified_none certifies that "
                "none is due; the two contradict each other")
        if use_amount == 0 and not certified:
            raise ValueError(
                "use_tax of 0 must be certified: pass use_tax_certified_none="
                "True, which fills the D-400 circle (D-401 p.14)")
        line_18: "float | None" = round(use_amount, 2)
        circle: "bool | None" = certified
    elif certified:
        line_18 = 0.0
        circle = True
    else:
        line_18 = None
        circle = None
        notes.append(
            "LINE 18 USE TAX NOT ANSWERED — left null, not 0. North Carolina "
            "collects consumer use tax on untaxed out-of-state and online "
            "purchases on this return; the amount due below EXCLUDES it and may "
            "be under-stated. Ask for the amount or a certification of none.")
    line_19 = round(line_17 + (line_18 or 0.0), 2)

    line_20a = round(_amount(withholding, "withholding"), 2)
    line_20b = round(_amount(spouse_withholding, "spouse_withholding"), 2)
    if line_20b and status != "MFJ":
        raise ValueError(
            "spouse_withholding (line 20b) is for a joint return only (D-401 "
            "p.14); on any other return it is the spouse's payment and "
            "counting it here over-states this filer's payments")
    line_21a = round(_amount(estimated_payments, "estimated_payments"), 2)
    line_21b = round(_amount(extension_payment, "extension_payment"), 2)
    line_23 = round(line_20a + line_20b + line_21a + line_21b, 2)
    line_25 = line_23

    line_26b = round(_amount(penalties, "penalties"), 2)
    line_26c = round(_amount(interest, "interest"), 2)
    line_26d = round(line_26b + line_26c, 2)
    line_26e = round(_amount(underpayment_interest, "underpayment_interest"), 2)
    if line_25 < line_19:
        line_26a = round(line_19 - line_25, 2)
        line_27 = round(line_26a + line_26d + line_26e, 2)
        line_28 = 0.0
    else:
        line_26a = 0.0
        excess = round(line_25 - line_19, 2)
        line_28 = round(max(0.0, excess - line_26e), 2)
        line_27 = round(line_26d + max(0.0, line_26e - excess), 2)
        if line_26e > excess:
            notes.append(
                "Line 26e interest exceeds the overpayment; the part it cannot "
                "absorb is carried to line 27 as an amount due.")

    contributions = {}
    for line, name, value in (("line_29", "applied_to_next_year", applied_to_next_year),
                              ("line_30", "wildlife_fund", wildlife_fund),
                              ("line_31", "education_fund", education_fund),
                              ("line_32", "cancer_program", cancer_program)):
        contributions[line] = round(_amount(value, name), 2)
    line_33 = round(sum(contributions.values()), 2)
    if line_33 > line_28:
        raise ValueError(
            f"Lines 29-32 total ${line_33:,.2f} but the overpayment on line 28 "
            f"is ${line_28:,.2f}. The refund cannot be less than zero (D-401 "
            "p.16): an amount applied or contributed beyond the overpayment "
            "manufactures a payment out of nothing.")
    line_34 = round(line_28 - line_33, 2)

    fed_ext = _nc_bool(federal_extension_circle, "federal_extension_circle",
                       allow_none=True)
    abroad = _nc_bool(out_of_country_circle, "out_of_country_circle",
                      allow_none=True)
    if fed_ext is None:
        notes.append(
            "Federal Extension circle unanswered: an automatic federal "
            "extension extends the NC return ONLY when that circle is filled "
            "(D-401 p.7, p.12).")

    result = {
        "line_6_federal_agi": round(agi, 2),
        "line_7_additions": line_7,
        "line_8": line_8,
        "line_9_deductions": line_9,
        "line_10a_children": child["line_10a_children"],
        "line_10b_child_deduction": child["line_10b_child_deduction"],
        "line_11_deduction": line_11,
        "line_11_method": choice["line_11_method"],
        "line_12a": line_12a,
        "line_12b": line_12b,
        "line_14_nc_taxable_income": tax["line_14_nc_taxable_income"],
        "line_15_nc_tax": line_15,
        "line_16_credits": line_16,
        "line_17": line_17,
        "line_18_use_tax": line_18,
        "use_tax_none_due_circle": circle,
        "line_19": line_19,
        "line_20a_withholding": line_20a,
        "line_20b_spouse_withholding": line_20b,
        "line_21a_estimated": line_21a,
        "line_21b_extension_paid": line_21b,
        "line_23": line_23,
        "line_25": line_25,
        "line_26a_tax_due": line_26a,
        "line_26b": line_26b,
        "line_26c": line_26c,
        "line_26d": line_26d,
        "line_26e": line_26e,
        "line_27_amount_due": line_27,
        "line_28_overpayment": line_28,
        **contributions,
        "line_33": line_33,
        "line_34_refund": line_34,
        "federal_extension_circle": fed_ext,
        "out_of_country_circle": abroad,
        "schedule_s": {k: v for k, v in sched_s.items()
                       if k.startswith("line_")},
        "notes": notes,
    }
    if sched_a is not None:
        result["schedule_a"] = {k: v for k, v in sched_a.items()
                                if k.startswith("line_")}
    return result
