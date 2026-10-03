"""TY2025 IT-203 and NYC IT-360.1 from explicitly allocated worksheets.

All annual figures come from the fetched NY cache. No automatic domicile,
convenience-of-employer test, special accrual or other-state credit inference.
"""

import datetime as dt
from decimal import InvalidOperation
import math

from agents.lib.tax_computations._shared import _normalize_filing_status, _round_half_up
from agents.lib.tax_computations.state.ny import (
    _ny_schedule_tax,
    compute_ny_household_credit,
    compute_ny_itemized,
    compute_ny_tax,
    compute_mctmt,
)


def _num(value, name, *, signed=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (not signed and value < 0)
    ):
        raise ValueError(f"{name}: require a finite {'signed' if signed else 'nonnegative'} number")
    return float(value)


def _money(value):
    try:
        return float(_round_half_up(_num(value, "derived amount", signed=True), 2))
    except InvalidOperation:
        raise ValueError("Derived amount exceeds supported numeric precision") from None


def _whole(value):
    try:
        return float(_round_half_up(_num(value, "derived amount", signed=True)))
    except InvalidOperation:
        raise ValueError("Derived amount exceeds supported numeric precision") from None


def _yes(value, name):
    if value is not True:
        raise ValueError(f"{name}: explicit worksheet confirmation required")


def _count(value, name, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{name}: require a nonnegative whole count")
    return value


def _period(value, year, name):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name}: require [start,end] ISO dates")
    try:
        start, end = (dt.date.fromisoformat(v) for v in value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: invalid ISO dates") from None
    if value != [start.isoformat(), end.isoformat()]:
        raise ValueError(f"{name}: use canonical YYYY-MM-DD dates")
    if start.year != year or end.year != year or start > end:
        raise ValueError(f"{name}: dates must be ordered within the covered year")
    return start, end


def ny_it203_plan(profile):
    """Validate an optional declared NY filing plan; never use mail as domicile.

    A complete plan selects NY law, but only a complete no-tax-state inventory
    can clear the old move/source blocks. Other income-tax returns stay gated.
    """
    if not isinstance(profile, dict):
        raise ValueError("Profile must be an object")
    filings = profile.get("state_filings")
    if filings is None or filings == {}:
        return None
    if not isinstance(filings, dict):
        raise ValueError("state_filings must be an object")
    plan = filings.get("NY")
    if plan is None:
        return None
    if not isinstance(plan, dict) or plan.get("form") != "IT203":
        raise ValueError("state_filings.NY must declare form IT203")
    year = profile.get("tax_year")
    if (
        type(year) is not int
        or year != 2025
        or type(plan.get("tax_year")) is not int
        or plan["tax_year"] != year
    ):
        raise ValueError("IT203 plan must name the source-verified TY2025")
    status = _normalize_filing_status(profile.get("filing_status"))
    if status == "MFS":
        raise ValueError("IT203 MFS spouse-AGI household worksheets are not modeled")
    if status == "MFJ":
        _yes(
            plan.get("joint_residency_identical"),
            "Joint spouses' NY and NYC residence periods must be identical",
        )
    residence = plan.get("residency")
    period = plan.get("resident_period")
    if residence == "part_year":
        start, end = _period(period, year, "NY resident_period")
        if start == dt.date(year, 1, 1) and end == dt.date(year, 12, 31):
            raise ValueError("A whole-year NY resident uses IT201")
    elif residence == "nonresident":
        if period is not None:
            raise ValueError("Nonresident NY plan must have null resident_period")
    else:
        raise ValueError("IT203 residency must be part_year or nonresident")
    city = plan.get("nyc_resident_period")
    if city is not None:
        cs, ce = _period(city, year, "NYC resident_period")
        if residence != "part_year" or cs < start or ce > end:
            raise ValueError("NYC period must be within the NY resident period")
    if plan.get("yonkers_tax_required") is not False:
        raise ValueError("Explicitly confirm no IT203 Yonkers tax is required")
    if plan.get("special_condition_a6") is not False:
        raise ValueError("Build America Bond A6 household-credit adjustment is not modeled")
    flags = profile.get("flags") or {}
    if not isinstance(flags, dict):
        raise ValueError("Profile flags must be an object")
    if city is not None and flags.get("nyc_resident") is not True:
        raise ValueError("NYC period contradicts nyc_resident flag")
    if city is not None and flags.get("nyc_resident_full_year") is True:
        raise ValueError("IT203 NYC period contradicts the full-year City flag")
    if flags.get("nyc_resident") is True and city is None:
        raise ValueError("Flagged NYC residency requires an explicit NYC period")
    if flags.get("yonkers_resident") is True or flags.get("yonkers_nonresident_earnings") is True:
        raise ValueError(
            "IT203 Yonkers tax requires IT360.1/Y203 worksheets not modeled by this variant"
        )
    for key in (
        "obligation_inventory_complete",
        "allocation_reviewed",
        "convenience_rule_reviewed",
        "special_accruals_reviewed",
    ):
        _yes(plan.get(key), key)
    others = plan.get("other_state_obligations")
    if not isinstance(others, list) or not others:
        raise ValueError("List every other resident/source state, even where no return is due")
    from agents.lib.capabilities import _NO_INCOME_TAX_STATES, _TN_HALL_REPEAL_YEAR
    from agents.lib.tax_computations._shared import _STATE_NAME_TO_CODE

    valid_codes = set(_STATE_NAME_TO_CODE.values()) | {"DC"}
    residents, sources = plan.get("resident_states"), plan.get("source_states")
    if (
        not isinstance(residents, list)
        or not residents
        or not isinstance(sources, list)
        or any(not isinstance(code, str) or code not in valid_codes for code in residents + sources)
        or len(set(residents)) != len(residents)
        or len(set(sources)) != len(sources)
    ):
        raise ValueError("List distinct real resident_states and source_states explicitly")
    if (residence == "part_year" and "NY" not in residents) or (
        residence == "nonresident" and "NY" in residents
    ):
        raise ValueError("Resident-state inventory contradicts NY residence type")
    residency = profile.get("residency") or {}
    if not isinstance(residency, dict):
        raise ValueError("Profile residency must be an object")
    domicile = residency.get("domicile_state")
    if domicile and domicile not in residents:
        raise ValueError("Resident-state inventory omits the declared domicile")
    seen = set()
    fully_covered = True
    for row in others:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("state"), str)
            or row.get("state") not in valid_codes
            or row.get("state") in seen
            or row.get("state") == "NY"
        ):
            raise ValueError(
                "Other-state inventory requires distinct real state codes other than NY"
            )
        seen.add(row["state"])
        if type(row.get("return_required")) is not bool:
            raise ValueError("Confirm return_required for every other state")
        if row["state"] not in _NO_INCOME_TAX_STATES or (
            row["state"] == "TN" and year < _TN_HALL_REPEAL_YEAR
        ):
            fully_covered = False
        if row["return_required"]:
            fully_covered = False
    if seen != (set(residents) | set(sources)) - {"NY"}:
        raise ValueError("Other-state obligations must cover the entire resident/source inventory")
    return dict(plan, fully_covered=fully_covered)


def _allocations(rows, allowed_lines, name):
    if not isinstance(rows, list):
        raise ValueError(f"{name}: provide reviewed rows or a confirmed empty list")
    federal = {n: 0.0 for n in allowed_lines}
    source = dict(federal)
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{name}: rows must be objects")
        ident, line = row.get("id"), row.get("line")
        if not isinstance(ident, str) or not ident.strip() or ident in seen:
            raise ValueError(f"{name}: unique source/activity ids required")
        seen.add(ident)
        if type(line) is not int or line not in allowed_lines:
            raise ValueError(f"{name}: invalid IT203 line")
        amounts = [
            _num(row.get(k), f"{name}.{k}", signed=True)
            for k in ("federal", "resident", "nonresident_ny", "nonresident_elsewhere")
        ]
        total, resident, ny, elsewhere = amounts
        if abs(_money(resident + ny + elsewhere) - _money(total)) > 0.001:
            raise ValueError(f"{name}: disjoint allocations must sum to the federal amount")
        if row.get("method") not in {
            "actual",
            "ny_workdays",
            "entity_statement",
            "special_accrual",
        }:
            raise ValueError(
                "Record an actual source allocation method, never calendar-day proration"
            )
        federal[line] += total
        source[line] += resident + ny
    for d in (federal, source):
        for key in d:
            d[key] = _money(_num(d[key], "allocated line total", signed=True))
    return federal, source


def compute_nyc_part_year(
    tax_year,
    filing_status,
    resident_nyagi,
    resident_full_months,
    dependents,
    state_cfg,
    *,
    claimed_as_dependent,
    resident_itemized_deduction,
    charitable_trust_addback,
    household_and_accumulation_credit,
    other_city_taxes,
    ubt_credit,
    worksheets_verified,
):
    """IT360.1 Parts 3–4. Prorate deductions, not income or tax rates.

    Full months are the reviewed line-39 count (more than half a month rounds
    up). Itemized deductions are the verified Part 2 result. City household,
    accumulation and UBT credits and IT230 amounts are separate worksheets.
    """
    status = _normalize_filing_status(filing_status).lower()
    if type(tax_year) is not int or tax_year != 2025 or status == "mfs":
        raise ValueError(
            "IT360.1 variant covers TY2025 with matching spouse residence only; MFS refuses"
        )
    _yes(worksheets_verified, "IT360.1 deduction and credit worksheets")
    months = _count(resident_full_months, "IT360.1 full months", 12)
    count = _count(dependents, "dependents")
    if type(claimed_as_dependent) is not bool:
        raise ValueError("Confirm claimed_as_dependent")
    key = "single_dependent" if status == "single" and claimed_as_dependent else status
    if (
        not isinstance(state_cfg, dict)
        or state_cfg.get("code") != "NY"
        or state_cfg.get("type") != "graduated_income"
    ):
        raise ValueError("IT360.1 requires NY law")
    std = _num(state_cfg.get("standard_deduction", {}).get(key), "fetched NY standard deduction")
    dependent = _num(
        state_cfg.get("dependent_exemption", {}).get("amount"), "fetched dependent exemption"
    )
    prorated_std = _whole(std * months / 12)
    # Chart rounds ONE exemption first, then multiplies by number of children.
    exemption = _whole(dependent * months / 12) * count
    deduction = (
        prorated_std
        if resident_itemized_deduction is None
        else _num(resident_itemized_deduction, "IT360.1 Part 2 itemized deduction")
    )
    addback = _num(charitable_trust_addback, "resident charitable trust addback")
    deduction = _whole(deduction)
    agi = _whole(_num(resident_nyagi, "resident NYAGI", signed=True) + addback)
    taxable = max(0.0, agi - deduction - exemption)
    city = state_cfg.get("nyc_tax") or {}
    schedule = state_cfg.get("bracket_schedule_by_status", {}).get(status)
    bands = city.get("brackets", {}).get(schedule)
    if not bands:
        raise ValueError("Fetch NYC rate schedules with published bases")
    exact, _ = _ny_schedule_tax(taxable, bands, schedule)
    gross = _whole(exact)  # IT360.1 uses the RATE SCHEDULE even below table ceiling.
    credit = _whole(_num(household_and_accumulation_credit, "IT360.1 line49 credits"))
    other = _whole(_num(other_city_taxes, "IT360.1 lines51/52 other city taxes"))
    ubt = _whole(_num(ubt_credit, "IT360.1 line54 UBT credit"))
    tax = max(0.0, max(0.0, gross - credit) + other - ubt)
    return dict(
        computed_tax_year=tax_year,
        line_39_full_months=months,
        line_40_prorated_exemption=_whole(dependent * months / 12),
        line_42_exemptions=exemption,
        line_43_nyc_agi=_money(agi),
        line_44_deduction=_money(deduction),
        line_47_taxable_income=_money(taxable),
        line_48_tax=gross,
        line_49_credits=_money(credit),
        line_53_before_ubt=_money(max(0.0, gross - credit) + other),
        line_54_ubt_credit=_money(ubt),
        line_55_nyc_tax=_money(tax),
        method="rate_schedule_part_year",
    )


def compute_ny_it203(
    tax_year,
    filing_status,
    federal_agi,
    tax_law,
    *,
    plan,
    income_rows,
    adjustment_rows,
    modifications,
    modification_worksheets_verified,
    allocation_complete,
    dependents,
    claimed_as_dependent,
    itemized,
    credits,
    credit_worksheets_verified,
    payments,
    payments_verified,
    use_tax,
    voluntary_contributions,
    penalty,
    interest,
    applied_to_next_year,
    mctmt_zone1,
    mctmt_zone2,
    mctmt_allocations_verified,
    city_inputs,
    special_it230_income_percentage,
):
    """IT203 columns/lines 33–71; verified credits in the form's own order.

    `modifications` is the reviewed two-column IT203 lines20–29 worksheet,
    including allocated IT225 modifications and the single annual pension cap.
    `credits` has required `child_care`, `earned_income`, `nonrefundable`,
    `other_ny_taxes`, `nyc_child_care`, `nyc_school_fixed`, `nyc_school_rate`,
    `nyc_elimination`, `other_refundable`. All are separately verified worksheet
    amounts, not eligibility inferred by this function.
    """
    status = _normalize_filing_status(filing_status)
    if type(tax_year) is not int or tax_year != 2025 or status == "MFS":
        raise ValueError("IT203 variant covers TY2025; MFS requires separate spouse worksheets")
    if (
        not isinstance(tax_law, dict)
        or type(tax_law.get("tax_year")) is not int
        or tax_law["tax_year"] != tax_year
    ):
        raise ValueError("IT203 law cache must explicitly name this tax year")
    cfg = tax_law.get("state")
    if (
        not isinstance(cfg, dict)
        or cfg.get("code") != "NY"
        or cfg.get("type") != "graduated_income"
    ):
        raise ValueError("Fetch NY law for the selected NY filing, regardless of mailing address")
    if not isinstance(plan, dict):
        raise ValueError("Provide the validated IT203 filing plan")
    # Validate again inside the pure computation, not just at the capability gate.
    p = ny_it203_plan(
        dict(
            tax_year=tax_year,
            filing_status=status,
            state_filings={"NY": plan},
            flags={"nyc_resident": plan.get("nyc_resident_period") is not None},
        )
    )
    _yes(allocation_complete, "Complete income, convenience and special-accrual allocation")
    _yes(modification_worksheets_verified, "All IT203/IT225 modifications and annual pension caps")
    _yes(credit_worksheets_verified, "All IT203/ATT credit worksheets")
    _yes(payments_verified, "All NY-specific payments")
    _yes(mctmt_allocations_verified, "MCTMT zone allocations")
    if special_it230_income_percentage is not False:
        raise ValueError("IT230 special income-percentage worksheet not modeled")
    if type(claimed_as_dependent) is not bool:
        raise ValueError("Confirm claimed_as_dependent")
    count = _count(dependents, "dependents")
    federal, source = _allocations(income_rows, (*range(1, 12), 13, 14, 15, 16), "income_rows")
    adj_f, adj_s = _allocations(adjustment_rows, (18,), "adjustment_rows")
    actual_agi = sum(federal.values()) - adj_f[18]
    if (
        abs(_money(actual_agi) - _money(_num(federal_agi, "computed federal AGI", signed=True)))
        > 0.001
    ):
        raise ValueError(
            "All-source allocation AGI must equal the computed federal return; an income/adjustment is missing"
        )
    if p["residency"] == "nonresident" and any(
        row["resident"] for row in income_rows + adjustment_rows
    ):
        raise ValueError("Nonresident plan cannot allocate income to a NY resident period")
    # NY requires whole-dollar form boxes. Tie raw amounts to federal first,
    # then round each AGGREGATED income/adjustment line, never each document.
    federal = {n: _whole(v) for n, v in federal.items()}
    source = {n: _whole(v) for n, v in source.items()}
    federal[17], source[17] = _money(sum(federal.values())), _money(sum(source.values()))
    federal[18], source[18] = _whole(adj_f[18]), _whole(adj_s[18])
    federal[19], source[19] = _money(federal[17] - federal[18]), _money(source[17] - source[18])
    if not isinstance(modifications, dict) or set(modifications) != {
        str(n) for n in (20, 21, 22, 24, 25, 26, 27, 28, 29)
    }:
        raise ValueError(
            "Explicit federal/source modification amounts required for IT203 modification line20–22/24–29"
        )
    for n in (20, 21, 22, 24, 25, 26, 27, 28, 29):
        row = modifications[str(n)]
        if not isinstance(row, dict):
            raise ValueError("Modification rows must hold federal and ny_source amounts")
        federal[n] = _num(row.get("federal"), f"modification {n} federal")
        source[n] = _num(row.get("ny_source"), f"modification {n} NY source")
        if source[n] > federal[n]:
            raise ValueError("A source modification cannot exceed its all-source amount")
        federal[n], source[n] = _whole(federal[n]), _whole(source[n])
    for column in (federal, source):
        column[23] = _money(sum(column[n] for n in (19, 20, 21, 22)))
        column[30] = _money(sum(column[n] for n in range(24, 30)))
        column[31] = _money(column[23] - column[30])
    if federal[28]:
        cap = _num(
            cfg.get("subtractions", {}).get("pension_annuity_exclusion", {}).get("amount"),
            "fetched annual pension exclusion cap",
        ) * (2 if status == "MFJ" else 1)
        if federal[28] > cap:
            raise ValueError(
                "Annual pension exclusion exceeds the household cap; do not repeat it for each period"
            )
    nyagi, source_agi = federal[31], source[31]
    key = status.lower()
    std_key = "single_dependent" if key == "single" and claimed_as_dependent else key
    std = _num(cfg.get("standard_deduction", {}).get(std_key), "fetched NY standard deduction")
    if itemized is None:
        deduction, regime = std, "standard"
    elif isinstance(itemized, dict):
        d = compute_ny_itemized(
            nyagi=nyagi,
            filing_status=status,
            reduction=cfg.get("itemized_reduction"),
            charitable_limit=cfg.get("charitable_limit"),
            standard_deduction=std,
            **itemized,
        )
        deduction, regime = d["deduction"], "itemized" if d["use_itemized"] else "standard"
    else:
        raise ValueError("Provide reviewed IT196 inputs or confirmed null")
    exemption = (
        _num(cfg.get("dependent_exemption", {}).get("amount"), "fetched dependent exemption")
        * count
    )
    deduction, exemption = _whole(deduction), _whole(exemption)
    taxable = max(0.0, nyagi - deduction - exemption)
    tax = compute_ny_tax(
        taxable,
        nyagi,
        status,
        cfg.get("brackets"),
        cfg.get("bracket_schedule_by_status"),
        cfg.get("tax_table_benefit_recapture"),
        cfg.get("tax_table"),
    )
    household = compute_ny_household_credit(
        federal[19],
        status,
        count + 1 + (status == "MFJ"),
        cfg.get("credits", {}).get("household", {}).get("table"),
        claimed_as_dependent,
    )["credit"]
    if nyagi <= 0 or source_agi <= 0:
        tax = {"tax": 0.0, "regime": "nonpositive_agi_skip"}
        household = 0.0
    if not isinstance(credits, dict) or set(credits) != {
        "child_care",
        "earned_income",
        "nonrefundable",
        "other_ny_taxes",
        "nyc_child_care",
        "nyc_school_fixed",
        "nyc_school_rate",
        "nyc_elimination",
        "other_refundable",
    }:
        raise ValueError("Provide every named credit worksheet amount, including confirmed zeros")
    c = {k: _whole(_num(v, f"credit.{k}")) for k, v in credits.items()}
    base = max(
        0.0, max(0.0, max(0.0, tax["tax"] - household) - c["child_care"]) - c["earned_income"]
    )
    ratio = float(_round_half_up(source_agi / nyagi, 4)) if nyagi > 0 and source_agi > 0 else 0.0
    allocated = _whole(base * ratio)  # NO clamp at one (IT203-I p.21).
    state_tax = max(0.0, allocated - c["nonrefundable"]) + c["other_ny_taxes"]
    if p["nyc_resident_period"] is not None:
        if not isinstance(city_inputs, dict):
            raise ValueError("NYC resident period requires an IT360.1 worksheet")
        city_itemized = city_inputs.get("resident_itemized_deduction")
        if (regime == "standard") != (city_itemized is None):
            raise ValueError(
                "IT360.1 deduction election must match IT203; provide Part 2 for itemizers"
            )
        cs, ce = _period(p["nyc_resident_period"], tax_year, "NYC period")
        import calendar

        rounded_months = sum(
            (
                min(ce, dt.date(tax_year, m, calendar.monthrange(tax_year, m)[1]))
                - max(cs, dt.date(tax_year, m, 1))
            ).days
            + 1
            > calendar.monthrange(tax_year, m)[1] / 2
            for m in range(cs.month, ce.month + 1)
        )
        months = _count(city_inputs.get("resident_full_months"), "NYC full months", 12)
        if months != rounded_months:
            raise ValueError("IT360.1 month count contradicts the actual City period")
        city = compute_nyc_part_year(
            tax_year,
            status,
            dependents=count,
            state_cfg=cfg,
            claimed_as_dependent=claimed_as_dependent,
            **city_inputs,
        )
        city_tax = max(0.0, city["line_55_nyc_tax"] - c["nyc_child_care"])
    else:
        if city_inputs is not None or any(
            c[k]
            for k in ("nyc_child_care", "nyc_school_fixed", "nyc_school_rate", "nyc_elimination")
        ):
            raise ValueError("NYC worksheet/credits require an actual NYC resident period")
        city, city_tax = None, 0.0
    mctmt = compute_mctmt(
        _whole(_num(mctmt_zone1, "MCTMT zone1")),
        _whole(_num(mctmt_zone2, "MCTMT zone2")),
        cfg.get("mctmt"),
    )
    for key in ("line_54c_zone_1_tax", "line_54d_zone_2_tax"):
        mctmt["lines"][key] = _whole(mctmt["lines"][key])
    mctmt["mctmt"] = _money(
        mctmt["lines"]["line_54c_zone_1_tax"] + mctmt["lines"]["line_54d_zone_2_tax"]
    )
    if not isinstance(payments, dict) or set(payments) != {
        "state_withholding",
        "city_withholding",
        "yonkers_withholding",
        "estimates_and_extension",
    }:
        raise ValueError(
            "Provide NY/NYC/Yonkers withholding and NY estimates/extension amounts separately"
        )
    pay = {k: _whole(_num(v, f"payment.{k}")) for k, v in payments.items()}
    use = _whole(_num(use_tax, "confirmed use tax"))
    voluntary = _whole(_num(voluntary_contributions, "voluntary contributions"))
    total = _money(state_tax + city_tax + mctmt["mctmt"] + use + voluntary)
    refundable = sum(
        c[k] for k in ("nyc_school_fixed", "nyc_school_rate", "nyc_elimination", "other_refundable")
    )
    paid = _money(sum(pay.values()) + refundable)
    estimated_penalty = _whole(_num(penalty, "estimated penalty"))
    other_charges = _whole(_num(interest, "other penalties/interest"))
    # IT203-I line71: estimated penalty reduces line67; line72 never does.
    balance = _money(total + estimated_penalty - paid)
    over = max(0.0, -balance)
    carry = _whole(_num(applied_to_next_year, "applied_to_next_year"))
    if carry > over:
        raise ValueError("Next-year application exceeds overpayment after penalties")
    return dict(
        computed_tax_year=tax_year,
        federal_amount={f"line_{n}": _money(v) for n, v in federal.items()},
        ny_source_amount={f"line_{n}": _money(v) for n, v in source.items()},
        line_31_federal_nyagi=nyagi,
        line_31_ny_source_agi=source_agi,
        line_32_nyagi=nyagi,
        line_33_deduction=_money(deduction),
        deduction_regime=regime,
        line_34_after_deduction=_money(max(0.0, nyagi - deduction)),
        line_36_taxable_income=_money(taxable),
        line_35_dependent_exemptions=_money(exemption),
        line_37_taxable_income=_money(taxable),
        line_38_tax=tax["tax"],
        line_38_tax_method=tax["regime"],
        line_39_household_credit=household,
        line_40_after_household=_money(max(0.0, tax["tax"] - household)),
        line_41_child_care=c["child_care"],
        line_42_after_child_care=_money(
            max(0.0, max(0.0, tax["tax"] - household) - c["child_care"])
        ),
        line_43_earned_income=c["earned_income"],
        line_44_base_tax=_money(base),
        line_45_income_percentage=ratio,
        line_46_allocated_tax=allocated,
        line_47_nonrefundable=c["nonrefundable"],
        line_48_after_nonrefundable=_money(max(0.0, allocated - c["nonrefundable"])),
        line_49_other_ny_taxes=c["other_ny_taxes"],
        line_50_state_tax=_money(state_tax),
        line_51_nyc_tax=city["line_55_nyc_tax"] if city else 0.0,
        line_52_nyc_child_care=c["nyc_child_care"],
        line_52a_net_nyc_tax=_money(city_tax),
        line_52b_zone1_earnings=_whole(mctmt_zone1),
        line_52c_zone2_earnings=_whole(mctmt_zone2),
        line_52d_zone1_tax=mctmt["lines"]["line_54c_zone_1_tax"],
        line_52e_zone2_tax=mctmt["lines"]["line_54d_zone_2_tax"],
        line_52f_mctmt=mctmt["mctmt"],
        line_53_yonkers_nonresident_tax=0.0,
        line_54_yonkers_part_year_tax=0.0,
        line_55_city_and_mctmt=_money(city_tax + mctmt["mctmt"]),
        line_56_use_tax=use,
        line_57_voluntary=voluntary,
        line_58_total_taxes=total,
        line_59_total_taxes=total,
        line_60_nyc_school_fixed=c["nyc_school_fixed"],
        line_60a_nyc_school_rate=c["nyc_school_rate"],
        line_60b_nyc_elimination=c["nyc_elimination"],
        line_61_other_refundable=c["other_refundable"],
        line_62_state_withholding=pay["state_withholding"],
        line_63_city_withholding=pay["city_withholding"],
        line_64_yonkers_withholding=pay["yonkers_withholding"],
        line_65_estimates_and_extension=pay["estimates_and_extension"],
        line_66_payments=paid,
        line_67_overpayment=_money(over),
        line_68_refund=_money(over - carry),
        line_69_next_year=carry,
        line_70_amount_owed=_money(max(0.0, balance) + other_charges),
        line_71_penalty=estimated_penalty,
        line_72_interest=other_charges,
        nyc_it3601=city,
        notes=[
            "TY2025 IT203 uses reviewed actual income/modification/credit worksheets; source tax may exceed 100% of all-source base tax.",
            "IT360.1 uses resident-period income and the City rate schedule. NY form lines are rounded to whole dollars after federal intake tie-out.",
            "Other-state returns/credits, mixed spouses, MFS, IT230 special ratios and IT203 Yonkers tax require manual preparation. IT203 withholding forecasts are not modeled.",
        ],
    )
