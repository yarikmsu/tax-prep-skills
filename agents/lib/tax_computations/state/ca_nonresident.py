"""Reviewed TY2025 California part-year allocation and Form540NR arithmetic.

See state-module-ca-part-year.md. Source allocations are reviewed inputs;
neither dates nor a mailing address decide what California can tax.
"""

import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import re

from agents.lib.tax_computations._shared import _normalize_filing_status


CA_NR_INCOME_LINES = (
    "A1z", "A2b", "A3b", "A4b", "A5b", "A6b", "A7a",
    *(f"B{n}" for n in (1, "2a", 3, 4, 5, 6, 7)),
    *(f"B8{letter}" for letter in "abcdefghijklmnopqrstuvz"),
)
CA_NR_ADJUSTMENT_LINES = (
    *(f"C{n}" for n in range(11, 19)), "C19a", "C20", "C21", "C23",
    *(f"C24{letter}" for letter in "abcdefghijkz"),
)
_FEDERAL_INCOME = {
    "A1z": "line_1z_total_wages", "A2b": "line_2b_taxable_interest",
    "A3b": "line_3b_ordinary_dividends", "A4b": "line_4b_taxable_ira",
    "A5b": "line_5b_taxable_pensions", "A6b": "line_6b_taxable_ss",
    "A7a": "line_7_capital_gain",
}
_SCOPE_FACTS = (
    "residency_reviewed", "obligation_inventory_complete", "allocation_reviewed",
    "conformity_reviewed", "special_accruals_reviewed", "capital_allocation_reviewed",
    "no_military_or_rdp_elections", "no_amt_nol_or_business_exclusion",
    "no_special_amt_child_exemption", "no_special_distribution_tax",
    "no_move_year_rentals",
    "credit_rounding_reviewed",
    "use_tax_obligation_reviewed",
)
CA_NR_COMPONENT_LINES = (12, 41, 50, 51, 52, 53, 61, 73, 81, 82, 83, 84, 85, 86, 87,
                         91, 102, 120, 122, 123)
CA_NR_NOTE = (
    "Form540NR: reviewed TY2025 part-year return using actual source allocations, "
    "separate worldwide/California tax and AMT worksheets, and input-bound review. "
    "Residency, stock/business/capital-loss sourcing, conformity, deductions and credit "
    "eligibility require reviewed worksheets. Other-state returns, mixed joint periods, "
    "MFS/RDP, military elections, move-year rental PAL/depreciation, AMT NOL/business "
    "exclusions and special child exemptions are outside this computed path. "
    "Use tax is addressed separately with CDTFA, not on this income-tax return."
)


def _number(value, name, *, signed=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (not signed and value < 0)):
        raise ValueError(f"{name} requires a finite {'signed' if signed else 'nonnegative'} amount")
    return float(value)


def _round(value, places=2):
    number = _number(value, "derived amount", signed=True)
    try:
        return float(Decimal(str(number)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError("derived amount exceeds supported numeric precision") from None


def _whole(value):
    return _round(value, 0)


def _whole_product(left, right, *, offset=0):
    """Whole-dollar HALF_UP after decimal multiplication, never a float tie."""
    values = [_number(v, "derived product", signed=True) for v in (left, right, offset)]
    try:
        a, b, c = (Decimal(str(v)) for v in values)
        return float((a * b + c).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError("derived product exceeds supported numeric precision") from None


def _status(value):
    try:
        status = _normalize_filing_status(value)
    except ValueError:
        raise ValueError("Form540NR requires a canonical filing status") from None
    if status == "MFS":
        raise ValueError("Form540NR separate-spouse/community-property worksheets are not modeled")
    return status.lower()


def _year(value):
    if type(value) is not int or value != 2025:
        raise ValueError("Form540NR has only been reviewed for TY2025")
    return value


def _identifier(value, name):
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z_-]{0,79}", value) is None:
        raise ValueError(f"{name} requires an opaque alphabetic label")
    return value


def _period(value, year):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("CA resident_period requires two ISO dates")
    try:
        start, end = (dt.date.fromisoformat(v) for v in value)
    except (TypeError, ValueError):
        raise ValueError("CA resident_period requires valid ISO dates") from None
    if value != [start.isoformat(), end.isoformat()] or start.year != year or end.year != year or start > end:
        raise ValueError("CA resident_period must be ordered canonical dates within the covered year")
    first, last = dt.date(year, 1, 1), dt.date(year, 12, 31)
    if start == first and end == last:
        raise ValueError("a whole-year California resident uses Form540")
    return start, end


def ca_540nr_plan(profile):
    """Optional explicit filing plan; mailing address does not select this form."""
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")
    filings = profile.get("state_filings")
    if filings is None or filings == {}:
        return None
    if not isinstance(filings, dict):
        raise ValueError("state_filings must be an object")
    plan = filings.get("CA")
    if plan is None:
        return None
    if filings.get("NY") is not None:
        raise ValueError("simultaneous CA and NY allocation plans require unmodeled other-state returns")
    if not isinstance(plan, dict) or plan.get("form") != "540NR":
        raise ValueError("state_filings.CA must explicitly declare form540NR")
    year = _year(profile.get("tax_year"))
    if type(plan.get("tax_year")) is not int or plan["tax_year"] != year:
        raise ValueError("CA filing plan and profile tax years disagree")
    status = _status(profile.get("filing_status"))
    if plan.get("residency") != "part_year":
        raise ValueError("this Form540NR variant requires part_year residency")
    _period(plan.get("resident_period"), year)
    if status == "mfj" and plan.get("joint_residency_identical") is not True:
        raise ValueError("confirm identical joint spouses' CA residence periods")
    if any(plan.get(k) is not True for k in _SCOPE_FACTS):
        raise ValueError("complete every Form540NR residency/source/worksheet and scope review")
    flags = profile.get("flags")
    if not isinstance(flags, dict) or flags.get("moved_states_this_year") is not True:
        raise ValueError("the part-year CA plan requires the confirmed move screen")
    if any(flags.get(k) is not False for k in ("moved_abroad_this_year", "statutory_resident_of_another_state")):
        raise ValueError("foreign moves and statutory second residency are outside this Form540NR path")
    prior = profile.get("prior_year", {})
    if not isinstance(prior, dict):
        raise ValueError("prior_year must be an object")
    if (profile.get("rental_return") is not None or flags.get("has_rental_income") is not False
            or prior.get("rental_carryforward") is not None):
        raise ValueError("move-year rental PAL/depreciation needs separate Form540NR worksheets")
    from agents.lib.capabilities import _NO_INCOME_TAX_STATES, _TN_HALL_REPEAL_YEAR, _USPS_STATES, _TERRITORIES
    valid = _USPS_STATES - _TERRITORIES
    residents, sources = plan.get("resident_states"), plan.get("source_states")
    for name, values in (("resident_states", residents), ("source_states", sources)):
        if (not isinstance(values, list) or any(not isinstance(v, str) or v not in valid for v in values)
                or len(set(values)) != len(values)):
            raise ValueError(f"Form540NR {name} requires distinct real U.S. state codes")
    if "CA" not in residents or not (set(residents) - {"CA"}):
        raise ValueError("part-year resident inventory must include CA and the other resident state")
    residency = profile.get("residency", {})
    if not isinstance(residency, dict):
        raise ValueError("profile residency must be an object")
    domicile = residency.get("domicile_state")
    if domicile and domicile not in residents:
        raise ValueError("resident inventory omits the declared domicile")
    others = plan.get("other_state_obligations")
    if not isinstance(others, list) or not others:
        raise ValueError("list every other resident/source state's return obligation")
    seen, covered = set(), True
    for row in others:
        if (not isinstance(row, dict) or set(row) != {"state", "return_required"}
                or not isinstance(row["state"], str) or row["state"] not in valid
                or row["state"] == "CA" or row["state"] in seen or type(row["return_required"]) is not bool):
            raise ValueError("other-state obligations require distinct real states and explicit return decisions")
        code = row["state"]
        seen.add(code)
        covered &= (not row["return_required"] and code in _NO_INCOME_TAX_STATES
                    and not (code == "TN" and year < _TN_HALL_REPEAL_YEAR))
    if seen != (set(residents) | set(sources)) - {"CA"}:
        raise ValueError("other-state obligations must cover the entire resident/source inventory")
    return {**plan, "fully_covered": bool(covered)}


def _allocation_rows(raw, allowed, name):
    if not isinstance(raw, list):
        raise ValueError(f"{name} requires reviewed rows or an explicit empty list")
    columns = ("federal", "subtractions", "additions", "resident", "nonresident_ca", "nonresident_elsewhere")
    totals = {line: dict.fromkeys((*columns, "all_source", "ca_source"), 0.0) for line in allowed}
    rows, seen = [], set()
    fields = {"id", "line", "method", *columns}
    for row in raw:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError(f"{name} rows require ID, printed line, method and all six amount columns")
        identifier = _identifier(row["id"], "source row ID")
        if identifier in seen or not isinstance(row["line"], str) or row["line"] not in totals:
            raise ValueError(f"{name} requires distinct IDs and supported printed lines")
        seen.add(identifier)
        if row["method"] not in ("actual", "payroll_workdays", "entity_statement", "reviewed_worksheet"):
            raise ValueError("record actual source allocation; calendar-day proration is not supported")
        values = {key: _round(_number(row[key], "allocation amount", signed=True)) for key in columns}
        all_source = _round(values["federal"] - values["subtractions"] + values["additions"])
        if (row["line"].startswith("A") and row["line"] != "A7a" or row["line"] == "B7") and (
                values["federal"] < 0 or all_source < 0
                or any(values[k] < 0 for k in ("resident", "nonresident_ca", "nonresident_elsewhere"))):
            raise ValueError("ordinary gross-income allocation columns cannot contain negative amounts")
        if _round(sum(values[k] for k in ("resident", "nonresident_ca", "nonresident_elsewhere"))) != all_source:
            raise ValueError("disjoint CA-law source allocations must sum to columnD before form rounding")
        if row["line"] in ("A6b", "B1") and (all_source or any(values[k] for k in ("resident", "nonresident_ca", "nonresident_elsewhere"))):
            raise ValueError("Social Security and state refunds are excluded from both CA-law income columns")
        if row["line"] == "A4b" and values["nonresident_ca"]:
            raise ValueError("IRA distributions received while nonresident are protected retirement income, not CA-source income")
        values.update(all_source=all_source, ca_source=_round(values["resident"] + values["nonresident_ca"]))
        for key, value in values.items():
            totals[row["line"]][key] = _round(totals[row["line"]][key] + value)
        rows.append({**row, **values})
    return rows, totals


def _schedule_ca(plan, federal):
    """Keep law differences and resident/source allocation in separate columns."""
    if not isinstance(federal, dict):
        raise ValueError("Form540NR requires the checked federal income components")
    income_rows, income = _allocation_rows(plan.get("income_rows"), CA_NR_INCOME_LINES, "income_rows")
    adjustment_rows, adjustments = _allocation_rows(plan.get("adjustment_rows"), CA_NR_ADJUSTMENT_LINES, "adjustment_rows")
    ids = [row["id"] for row in income_rows + adjustment_rows]
    if len(ids) != len(set(ids)):
        raise ValueError("source row IDs must be distinct across income and adjustments")
    for line, key in _FEDERAL_INCOME.items():
        expected = _round(_number(federal.get(key), "checked federal income", signed=line == "A7a"))
        if income[line]["federal"] != expected:
            raise ValueError(f"ScheduleCA {line} columnA disagrees with the checked federal line")
    other = _round(sum(row["federal"] for line, row in income.items() if line.startswith("B")))
    adjusted = _round(sum(row["federal"] for row in adjustments.values()))
    if other != _round(_number(federal.get("line_8_schedule1_other"), "federal Schedule1 income", signed=True)):
        raise ValueError("all ScheduleCA additional-income rows must sum to federal line8")
    if adjusted != _round(_number(federal.get("line_10_adjustments"), "federal adjustments")):
        raise ValueError("all ScheduleCA adjustment rows must sum to federal line10")
    keys = ("federal", "subtractions", "additions", "resident", "nonresident_ca", "nonresident_elsewhere", "all_source", "ca_source")
    income_sum = {k: _round(sum(row[k] for row in income.values())) for k in keys}
    adjustment_sum = {k: _round(sum(row[k] for row in adjustments.values())) for k in keys}
    agi = {k: _round(income_sum[k] - adjustment_sum[k]) for k in keys}
    if agi["federal"] != _round(_number(federal.get("line_11_agi"), "checked federal AGI", signed=True)):
        raise ValueError("ScheduleCA columnA AGI disagrees before whole-dollar rounding")
    return {"income_rows": income_rows, "adjustment_rows": adjustment_rows,
            "income_lines": income, "adjustment_lines": adjustments,
            "income_totals": income_sum, "adjustment_totals": adjustment_sum, "agi": agi}


def _ratio(numerator, denominator, name, *, cap=True):
    numerator = _number(numerator, name, signed=True)
    denominator = _number(denominator, name, signed=True)
    if denominator <= 0 and numerator > 0:
        raise ValueError(f"{name}: nonpositive total with positive CA amount requires an alternate worksheet")
    if denominator == 0:
        return 0.0
    value = _round(numerator / denominator, 4)
    return min(1.0, max(0.0, value)) if cap else max(0.0, value)


def _review_digest(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def compute_ca_540nr_amt(
    *, tax_year: int, filing_status: str, state_law: dict,
    all_source_agi: float, ca_agi: float, deduction: float, deduction_method: str,
    itemized_before_limitation: float, itemized_after_limitation: float,
    regular_tax: float, inputs: dict,
) -> dict:
    """ScheduleP(540NR), with distinct worldwide and CA-source AMT bases.

    Non-ISO adjustments are confirmed worksheet NETS: PartI2–13 excluding
    ISO and PartII29 excluding ISO. They are not interchangeable. The itemized
    disallowance net is PartII34. NOL/exclusion/child worksheets stay gated by
    the filing plan. New deferral attribution is ISO-only, repriced in BOTH
    source columns rather than multiplied by the regular-tax ratio.
    """
    _year(tax_year)
    status = _status(filing_status)
    if not isinstance(state_law, dict) or state_law.get("code") != "CA" or state_law.get("type") != "graduated_income":
        raise ValueError("ScheduleP540NR requires the covered CA law block")
    cfg = state_law.get("amt")
    if not isinstance(cfg, dict):
        raise ValueError("fetch the CA AMT law block")
    key = "mfj" if status == "qss" else status
    exemption = _number((cfg.get("exemption") or {}).get(key), "CA AMT exemption")
    phaseout_start = _number((cfg.get("phaseout_start") or {}).get(key), "CA AMT phaseout start")
    rate = _number(cfg.get("tmt_rate"), "CA TMT rate")
    phaseout = _number(cfg.get("phaseout_rate"), "CA AMT phaseout rate")
    if exemption <= 0 or phaseout_start <= 0 or not 0 < rate <= 1 or not 0 < phaseout <= 1:
        raise ValueError("CA AMT law must be fetched, positive and internally valid")
    fields = {"world_adjustments", "ca_adjustments", "iso_world", "iso_ca", "itemized_amt_disallowances"}
    if not isinstance(inputs, dict) or set(inputs) != fields:
        raise ValueError("record separate worldwide/source AMT adjustments, ISO and itemized disallowances")
    world_adj = _whole(_number(inputs["world_adjustments"], "worldwide AMT adjustments", signed=True))
    ca_adj = _whole(_number(inputs["ca_adjustments"], "CA-source AMT adjustments", signed=True))
    iso_world = _whole(_number(inputs["iso_world"], "worldwide ISO spread"))
    iso_ca = _whole(_number(inputs["iso_ca"], "CA-source ISO spread"))
    disallowed = _whole(_number(inputs["itemized_amt_disallowances"], "AMT itemized disallowances", signed=True))
    if iso_ca > iso_world:
        raise ValueError("CA ISO source exceeds the worldwide spread; separate basis/CQSO worksheets are required")
    agi = _whole(_number(all_source_agi, "worldwide CA-law AGI", signed=True))
    source = _whole(_number(ca_agi, "CA-source AGI", signed=True))
    elected = _whole(_number(deduction, "elected annual deduction"))
    before = _whole(_number(itemized_before_limitation, "itemized deduction before limitation"))
    after = _whole(_number(itemized_after_limitation, "itemized deduction after limitation"))
    regular = _whole(_number(regular_tax, "Form540NR line37 regular tax"))
    if before < after:
        raise ValueError("itemized deduction limitation cannot increase the deduction")
    if deduction_method not in ("standard", "itemized", "dependent_worksheet"):
        raise ValueError("identify the elected deduction method before ScheduleP540NR")
    itemized = deduction_method == "itemized"
    if not itemized and disallowed:
        raise ValueError("a standard-deduction AMT return cannot also deduct itemized AMT amounts")
    if itemized and elected != after:
        raise ValueError("ScheduleP540NR itemized deduction must match the elected post-limitation amount")
    amt_itemized = _whole(before - disallowed) if itemized else 0.0
    if amt_itemized < 0:
        raise ValueError("negative AMT itemized deductions require their own reviewed worksheet")

    def calculate(world_iso, source_iso):
        # Line15 preserves the signed17−18 when regular taxable income floors
        # at zero. Otherwise the automatic standard addback invents AMTI.
        p = {"line_1": elected if not itemized else 0.0,
             "line_14": _whole(world_adj + world_iso + (0 if itemized else elected)),
             "line_15": _whole(agi - elected),
             "line_16": 0.0, "line_17": 0.0,
             "line_18": _whole(before - after) if itemized else 0.0,
             "line_20": 0.0}
        p["line_19"] = _whole(p["line_14"] + p["line_15"] - p["line_18"])
        p["line_21"] = p["line_19"]
        p["line_22"] = max(0.0, _whole_product(max(0.0, p["line_21"] - phaseout_start), -phaseout, offset=exemption))
        p["line_23"] = max(0.0, _whole(p["line_21"] - p["line_22"]))
        p["line_24"] = _whole_product(p["line_23"], rate)
        p.update(line_25=source, line_26=0.0, line_27=0.0, line_28=source,
                 line_29=_whole(ca_adj + source_iso), line_31=0.0)
        p["line_30"] = _whole(p["line_28"] + p["line_29"])
        p["line_32"] = p["line_30"]
        p.update(line_33=before if itemized else 0.0,
                 line_34=disallowed if itemized else 0.0, line_35=amt_itemized,
                 line_36=p["line_21"])
        p["line_37"] = _whole(p["line_35"] + p["line_36"])
        p["line_38"] = _ratio(p["line_32"], p["line_37"], "AMT itemized percentage") if itemized else 0.0
        p["line_39"] = _whole_product(amt_itemized, p["line_38"])
        p["line_40"] = _whole(p["line_32"] - p["line_39"])
        p["line_41"] = p["line_24"]
        if p["line_21"] <= 0 and p["line_40"] > 0:
            raise ValueError("positive CA AMTI with nonpositive worldwide AMTI requires an alternate worksheet")
        p["line_42"] = _ratio(p["line_41"], p["line_21"], "CA AMT rate", cap=False)
        p["line_43"] = _whole_product(max(0.0, p["line_40"]), p["line_42"])
        p["line_44"] = regular
        p["line_45"] = max(0.0, _whole(p["line_43"] - regular))
        return p

    paper = calculate(iso_world, iso_ca)
    without_iso = calculate(0.0, 0.0)
    generated = max(0.0, _whole(paper["line_45"] - without_iso["line_45"]))
    return {**paper, "computed_tax_year": tax_year, "worldwide_amti": paper["line_21"],
            "ca_amti": paper["line_40"], "ca_tmt": paper["line_43"],
            "ca_regular_tax": regular, "ca_amt": paper["line_45"],
            "inputs": inputs.copy(), "without_iso": without_iso,
            "iso_credit_generated": generated, "schedule_p_carryforward": generated}


def _credit_form_inputs(raw, tax_year):
    """Retain source cents while deriving auditable whole-dollar credit forms.

    PTET current amounts and eligible carryovers are separate form lines.
    Allocate each line's rounding by largest remainder, retaining every ID
    and vintage. Expired lots never participate in eligible-line rounding.
    """
    from agents.lib.tax_computations.state.ca_credits import validate_ca_ptet_carryovers
    credits = {**raw, "other_credits": []}
    proof = {"method": "whole_dollar_half_up_largest_remainder", "other_credits": []}

    def amount(value, name):
        source = _round(_number(value, name))
        form = _whole(source)
        return {"source_amount": source, "form_amount": form,
                "rounding_difference": _round(form - source)}

    proof["opening_amt_credit"] = amount(raw["opening_amt_credit"], "qualified opening AMT credit")
    credits["opening_amt_credit"] = proof["opening_amt_credit"]["form_amount"]
    for row in raw["other_credits"]:
        values = amount(row["amount"], "allowed NR credit")
        proof["other_credits"].append({"code": row["code"], **values})
        credits["other_credits"].append({"code": row["code"], "amount": values["form_amount"]})

    def lots(rows):
        source = [_round(_number(r["amount"], "PTET source amount")) for r in rows]
        floors = [math.floor(v) for v in source]
        remainder = int(_whole(sum(source)) - sum(floors))
        order = sorted(range(len(rows)), key=lambda i: (-(Decimal(str(source[i])) - floors[i]),
                       rows[i].get("origin_year", tax_year), rows[i]["credit_id"]))
        for i in order[:remainder]:
            floors[i] += 1
        normalized = [{**r, "amount": float(v)} for r, v in zip(rows, floors)]
        witness = [{**r, "source_amount": s, "form_amount": float(v),
                    "rounding_difference": _round(v - s)} for r, s, v in zip(rows, source, floors)]
        return normalized, witness

    current = raw["ptet_current"]
    if not isinstance(current, list) or any(not isinstance(r, dict)
            or set(r) != {"credit_id", "source_id", "amount"}
            or not isinstance(r["credit_id"], str) or not isinstance(r["source_id"], str) for r in current):
        raise ValueError("PTET current credits require source IDs and amounts")
    carry = validate_ca_ptet_carryovers(raw["ptet_carryovers"], tax_year)
    years = raw["ptet_carryforward_years"]
    if carry and (type(years) is not int or years <= 0):
        raise ValueError("fetch the PTET carry period before rounding vintage balances")
    eligible, expired = [], []
    for row in carry:
        (expired if tax_year > row["origin_year"] + years else eligible).append(row)
    credits["ptet_current"], proof["ptet_current"] = lots(current)
    formed_eligible, eligible_proof = lots(eligible)
    formed_expired, expired_proof = lots(expired)
    credits["ptet_carryovers"] = formed_eligible + formed_expired
    proof["ptet_carryovers"] = [*({**r, "expired_before_this_year": False} for r in eligible_proof),
                               *({**r, "expired_before_this_year": True} for r in expired_proof)]
    proof["reported_ptet_source_total"] = _round(sum(_number(r["amount"], "reported PTET credit") for r in current))
    proof["reported_ptet_form_total"] = _whole(sum(r["amount"] for r in credits["ptet_current"]))
    return credits, proof


def _deduction_inputs(raw, status, state):
    fields = {"method", "itemized_before_limitation", "itemized_after_limitation",
              "dependent_filer", "dependent_standard_deduction"}
    if not isinstance(raw, dict) or set(raw) != fields or type(raw["dependent_filer"]) is not bool:
        raise ValueError("record the annual itemized/standard election and dependent-deduction facts")
    key = "mfj" if status == "qss" else status
    standard = _whole(_number((state.get("standard_deduction") or {}).get(key), "fetched CA standard deduction"))
    if standard <= 0:
        raise ValueError("fetch a positive CA standard deduction")
    before = _whole(_number(raw["itemized_before_limitation"], "itemized before limitation"))
    after = _whole(_number(raw["itemized_after_limitation"], "itemized after limitation"))
    if after > before:
        raise ValueError("itemized limitation cannot increase deductions")
    method = raw["method"]
    dependent = raw["dependent_filer"]
    if dependent:
        eligible_standard = _whole(_number(raw["dependent_standard_deduction"], "worked dependent standard deduction"))
        if eligible_standard > standard:
            raise ValueError("dependent standard deduction exceeds the fetched annual standard amount")
    else:
        if raw["dependent_standard_deduction"] is not None:
            raise ValueError("a nondependent filer must not retain an unrelated dependent deduction")
        eligible_standard = standard
    if method == "itemized":
        if after < eligible_standard:
            raise ValueError("itemized election understates the available deduction")
        elected = after
    elif method == ("dependent_worksheet" if dependent else "standard"):
        if after > eligible_standard:
            raise ValueError("standard election understates the available reviewed itemized deduction")
        elected = eligible_standard
    else:
        raise ValueError("deduction method must match the reviewed dependent status and annual election")
    return {"method": method, "elected": elected, "annual_standard": standard,
            "itemized_before_limitation": before, "itemized_after_limitation": after,
            "dependent_filer": dependent}


def compute_ca_540nr(
    *, tax_year: int, filing_status: str, state_law: dict, profile: dict,
    federal_components: dict, exemption_counts: dict, deductions: dict,
    amt_inputs: dict, credit_inputs: dict, components: dict, source_witness: dict,
) -> dict:
    """Derive Form540NR from checked federal lines and reviewed allocation rows.

    Source witness is supplied by the builder from ledger/payment inputs for
    the review digest. No entered final tax or AGI is accepted. Credit inputs
    retain source cents with an explicit whole-dollar form reconciliation;
    ledger ownership/source proof is independently checked by the caller.
    """
    _year(tax_year)
    status = _status(filing_status)
    plan = ca_540nr_plan(profile)
    if plan is None or profile["tax_year"] != tax_year or _status(profile["filing_status"]) != status:
        raise ValueError("Form540NR needs a matching declared profile/year/status")
    from agents.lib.tax_computations.state.ca import validate_ca_return_law, compute_ca_exemption_credit
    from agents.lib.tax_computations.state.planning import _ca_tax
    from agents.lib.tax_computations.state.ca_credits import compute_ca_credit_order
    validate_ca_return_law(state_law)
    expected = {f"line_{n}" for n in CA_NR_COMPONENT_LINES} | {"claim_of_right_credit"}
    if not isinstance(components, dict) or set(components) != expected:
        raise ValueError("Form540NR requires exactly its reviewed component-line inputs")
    out = {key: _whole(_number(value, "Form540NR component")) for key, value in components.items()}
    if out["line_41"]:
        raise ValueError("Form540NR ScheduleG1/FTB5870A distribution tax needs separate reviewed support")
    if out["line_52"]:
        raise ValueError("dependent-parent credit requires the unsupported MFS household")
    # Booklet line 51: "You may not claim this credit if you used the head of
    # household, married/RDP filing jointly, or qualifying surviving spouse/RDP".
    if out["line_51"] and status not in ("single", "mfs"):
        raise ValueError("Form540NR line51 is not available to HOH, MFJ or QSS filers")
    count_fields = {"personal", "blind", "senior", "dependent"}
    if not isinstance(exemption_counts, dict) or set(exemption_counts) != count_fields:
        raise ValueError("record all four California exemption counts")
    for key, count in exemption_counts.items():
        maximum = 2 if status == "mfj" or (key == "personal" and status == "qss") else 1
        if type(count) is not int or count < 0 or (key != "dependent" and count > maximum):
            raise ValueError("California exemption counts exceed the supported household")
    personal_count = exemption_counts["personal"]
    if any(exemption_counts[key] > personal_count for key in ("blind", "senior")):
        raise ValueError("blind/senior exemptions require eligible personal exemptions")
    deduction = _deduction_inputs(deductions, status, state_law)
    if deduction["dependent_filer"] and (status == "qss" or personal_count > (1 if status == "mfj" else 0)):
        raise ValueError("a dependent-filer deduction contradicts the claimed personal exemptions")
    schedule = _schedule_ca(plan, federal_components)
    agi = schedule["agi"]
    fed_agi, world_agi, ca_agi = (_whole(agi[k]) for k in ("federal", "all_source", "ca_source"))
    subs, adds = (_whole(agi[k]) for k in ("subtractions", "additions"))
    # Signed adjustment totals transfer to the opposite form line when
    # negative; source allocation never becomes an adjustment in this step.
    out.update(line_13=fed_agi, line_14=max(0.0, subs) + max(0.0, -adds),
               line_16=max(0.0, adds) + max(0.0, -subs), line_18=deduction["elected"])
    out["line_15"] = _whole(out["line_13"] - out["line_14"])
    out["line_17"] = _whole(out["line_15"] + out["line_16"])
    rounding_difference = _whole(out["line_17"] - world_agi)
    world_agi = out["line_17"]
    out["line_19"] = max(0.0, _whole(world_agi - deduction["elected"]))
    part4 = {"line_1": ca_agi, "line_2": deduction["elected"],
             "line_3": _ratio(ca_agi, world_agi, "deduction percentage")}
    part4["line_4"] = _whole_product(part4["line_2"], part4["line_3"])
    part4["line_5"] = max(0.0, _whole(ca_agi - part4["line_4"]))
    out.update(line_31=_whole(_ca_tax(out["line_19"], status, state_law)),
               line_32=ca_agi, line_35=part4["line_5"])
    if out["line_19"] <= 0 and out["line_35"] > 0:
        raise ValueError("positive CA taxable income with zero worldwide taxable income needs an alternate worksheet")
    out["line_36"] = _ratio(out["line_31"], out["line_19"], "CA effective tax rate", cap=False)
    out["line_37"] = _whole_product(out["line_35"], out["line_36"])
    out["line_38"] = _ratio(out["line_35"], out["line_19"], "exemption credit percentage")
    personal = _number((state_law.get("personal_exemption") or {}).get("amount"), "fetched personal exemption")
    dependent = _number((state_law.get("dependent_exemption") or {}).get("amount"), "fetched dependent exemption")
    phaseout = state_law.get("exemption_phaseout")
    if personal <= 0 or dependent <= 0 or not isinstance(phaseout, dict):
        raise ValueError("fetch positive California exemptions and the complete phaseout worksheet")
    for key, line in (("personal", 7), ("blind", 8), ("senior", 9), ("dependent", 10)):
        out[f"line_{line}"] = _whole_product(exemption_counts[key], dependent if key == "dependent" else personal)
    out["line_11"] = _whole(sum(out[f"line_{n}"] for n in (7, 8, 9, 10)))
    exemption = compute_ca_exemption_credit(fed_agi, filing_status,
        sum(exemption_counts[k] for k in ("personal", "blind", "senior")), exemption_counts["dependent"],
        personal, dependent, phaseout)
    out["line_39"] = _whole_product(exemption["exemption_credit"], out["line_38"])
    out["line_40"] = max(0.0, _whole(out["line_37"] - out["line_39"]))
    out["line_42"] = _whole(out["line_40"] + out["line_41"])
    amt = compute_ca_540nr_amt(tax_year=tax_year, filing_status=filing_status,
        state_law=state_law, all_source_agi=world_agi, ca_agi=ca_agi,
        deduction=deduction["elected"], deduction_method=deduction["method"],
        itemized_before_limitation=deduction["itemized_before_limitation"],
        itemized_after_limitation=deduction["itemized_after_limitation"],
        regular_tax=out["line_37"], inputs=amt_inputs)
    credit_fields = {"opening_amt_credit", "other_credits", "ptet_current", "ptet_carryovers",
                     "ptet_carryforward_years", "ptet_first_tax_year"}
    if (not isinstance(credit_inputs, dict) or set(credit_inputs) != credit_fields
            or not isinstance(credit_inputs.get("other_credits"), list)
            or any(not isinstance(row, dict) or set(row) != {"code", "amount"} for row in credit_inputs["other_credits"])):
        raise ValueError("record reviewed NR owner-credit inputs and the explicit other-credit list")
    for row in credit_inputs["other_credits"]:
        if row.get("code") in ("170", "173", "163"):
            raise ValueError("use NR lines51–53 for credits requiring once-only source proration")
        # Schedule S's credit for tax paid to another state belongs to the other-
        # state returns this path excludes, even on a fully covered plan.
        if row.get("code") == "187":
            raise ValueError("the other-state tax credit (Schedule S, code 187) is outside the computed Form540NR path")
    credits, credit_rounding = _credit_form_inputs(credit_inputs, tax_year)
    out["line_54"] = out["line_38"]
    prorated = []
    for line, code in ((51, "170"), (53, "163")):
        amount = _whole_product(out[f"line_{line}"], out["line_54"])
        if amount:
            prorated.append({"code": code, "amount": amount})
    out["line_55"] = _whole(sum(row["amount"] for row in prorated))
    credits["other_credits"].extend(prorated)
    credit_paper = compute_ca_credit_order(regular_tax=out["line_37"], exemption_credits=out["line_39"],
        tentative_minimum_tax=amt["ca_tmt"], form540_line35=out["line_42"],
        dependent_care=out["line_50"], renters_credit=out["line_61"],
        new_deferral_credit=amt["iso_credit_generated"], tax_year=tax_year, **credits)
    ptet = credit_paper["ftb_3804_cr"]
    ptet["reported_form_total"] = ptet["reported_credit_total"]
    ptet["reported_credit_total"] = credit_rounding["reported_ptet_source_total"]
    ptet["source_current_credits"] = [dict(r) for r in credit_inputs["ptet_current"]]
    general = [row for row in credit_paper["credits"] if row["amount"] > 0 and row["code"] not in ("170", "173", "163")]
    out.update(line_58=0.0, line_59=0.0, line_60=0.0)
    if len(general) > 2:
        out["line_60"] = _whole(sum(row["amount"] for row in general))
    else:
        for line, row in zip((58, 59), general):
            out[f"line_{line}"] = _whole(row["amount"])
    out["line_62"] = _whole(sum(out[f"line_{n}"] for n in (50, 55, 58, 59, 60, 61)))
    out["line_63"] = max(0.0, _whole(out["line_42"] - out["line_62"]))
    out["line_71"] = amt["ca_amt"]
    surcharge = state_law.get("mental_health_surcharge")
    if not isinstance(surcharge, dict):
        raise ValueError("fetch the California surcharge block")
    threshold = _number(surcharge.get("threshold"), "CA surcharge threshold")
    rate = _number(surcharge.get("rate"), "CA surcharge rate")
    if not 0 <= rate <= 1:
        raise ValueError("CA surcharge rate must be a finite fraction")
    out["line_72"] = _whole_product(max(0.0, out["line_35"] - threshold), rate)
    out["line_74"] = _whole(sum(out[f"line_{n}"] for n in (63, 71, 72, 73)))
    out["line_88"] = _whole(sum(out[f"line_{n}"] for n in range(81, 88)) + out["claim_of_right_credit"])
    out["line_92"] = max(0.0, _whole(out["line_88"] - out["line_91"]))
    out["line_93"] = max(0.0, _whole(out["line_91"] - out["line_88"]))
    out["line_101"] = max(0.0, _whole(out["line_92"] - out["line_74"]))
    if out["line_102"] > out["line_101"]:
        raise ValueError("Form540NR line102 exceeds the available overpayment")
    out["line_103"] = _whole(out["line_101"] - out["line_102"])
    out["line_104"] = max(0.0, _whole(out["line_74"] - out["line_92"]))
    settlement = _whole(out["line_104"] + out["line_93"] + out["line_120"] + out["line_122"] + out["line_123"] - out["line_103"])
    out.update(line_121=max(0.0, _whole(out["line_104"] + out["line_93"] + out["line_120"] - out["line_103"])),
               line_124=max(0.0, settlement), line_125=max(0.0, -settlement))
    if not isinstance(source_witness, dict):
        raise ValueError("bind the source ledger/payment evidence to the NR review")
    digest = _review_digest({"plan": {k: v for k, v in plan.items() if k != "worksheet_review"},
        "tax_year": tax_year, "filing_status": status, "state_law": state_law,
        "federal": {k: federal_components[k] for k in (*_FEDERAL_INCOME.values(), "line_8_schedule1_other", "line_10_adjustments", "line_11_agi")},
        "exemption_counts": exemption_counts, "deductions": deductions, "amt_inputs": amt_inputs,
        "credit_inputs": credit_inputs, "components": components, "source_witness": source_witness})
    review_fields = {"input_digest", "sources_reviewed", "conformity_reviewed", "deductions_reviewed", "amt_reviewed", "credits_reviewed"}
    review = plan.get("worksheet_review")
    current = (isinstance(review, dict) and set(review) == review_fields
        and review.get("input_digest") == digest and all(review.get(k) is True for k in review_fields - {"input_digest"}))
    amt["ftb_3510"] = credit_paper["ftb_3510"]
    amt.update({k: v for k, v in credit_paper["ftb_3510"].items() if not k.startswith("line_")})
    return {**out, "computed_tax_year": tax_year, "return_form": "540NR",
            "ca_agi": out["line_32"], "ca_all_source_agi": out["line_17"],
            "federal_agi": out["line_13"], "ca_standard_deduction": deduction["annual_standard"],
            "ca_taxable_income": out["line_35"], "ca_total_tax": out["line_74"],
            "ca_refund": out["line_125"], "ca_amount_owed": out["line_124"],
            "schedule_ca": {**schedule, "part_iv": part4,
                "form_agi": {"federal": out["line_13"], "subtractions": subs,
                             "additions": adds, "all_source": out["line_17"], "ca_source": out["line_32"]},
                "form_rounding_difference": rounding_difference}, "schedule_p": amt,
            "exemption_worksheet": exemption, "deduction_worksheet": deduction,
            "credit_workpaper": {k: v for k, v in credit_paper.items() if k not in ("line_43", "line_44", "line_45")},
            "ftb_3804_cr": credit_paper["ftb_3804_cr"],
            "credit_rounding_workpaper": credit_rounding,
            "allocation_input_digest": digest, "allocation_review_current": bool(current),
            "line_123_note": "Form540NR line123: penalty NOT COMPUTED by the form constructor; a checked FTB5805 workpaper or explicit manual/FTB-billing election is required.",
            "source_note": CA_NR_NOTE}
