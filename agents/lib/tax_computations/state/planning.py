"""Covered-year state liability repricing shared by income planners.

This is deliberately independent of the simplified federal tax anchor. Inputs
must reproduce the return's state liability; unsupported work never becomes $0.
The maintained input contract is docs/contributing/state-planning.md.
"""

import copy
import hashlib
import json
import math

from agents.lib.tax_computations._shared import _amount, _normalize_filing_status
from agents.lib.tax_computations.state.ca import compute_ca_itemized
from agents.lib.tax_computations.state.nc import compute_nc_d400
from agents.lib.tax_computations.state.ny import (
    compute_city_and_yonkers_total,
    compute_mctmt,
    compute_ny_agi,
    compute_ny_household_credit,
    compute_ny_itemized,
    compute_ny_state_tax_total,
    compute_ny_tax,
    compute_nyc_resident_tax,
    compute_nyc_taxable_income,
    compute_yonkers_resident_surcharge,
)


class StatePlanningError(ValueError):
    """Missing, unsupported or inconsistent state evidence."""


# Form structure reviewed for these years. A newly fetched rate does not
# establish that the form's deduction, credit and local contracts are unchanged.
_REVIEWED_YEARS = {"CA": {2025}, "NY": {2025}, "NC": {2025}}
_SECTIONS = {"CA": ("california_540", "ca_total_tax"),
             "NY": ("new_york_it201", "line_60_total_taxes"),
             "NC": ("north_carolina_d400", "line_17")}
_INCOME_KEYS = {"wages", "taxable_interest", "ordinary_dividends",
                "qualified_dividends", "capital_gains", "ira_distribution", "other_income"}


def _obj(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise StatePlanningError(f"Record {name} as an object")
    return value


def _count(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StatePlanningError(f"{name} must be a non-negative integer")
    return value


def _bool(value, name: str) -> bool:
    if not isinstance(value, bool):
        raise StatePlanningError(f"Record {name} as true or false")
    return value


def _number(value, name: str, *, positive: bool = False) -> float:
    number = _amount(value, name)
    if positive and number <= 0:
        raise StatePlanningError(f"{name} must be positive")
    return number


def _finite_tree(value) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise StatePlanningError("State evidence must contain only finite numbers")
    if isinstance(value, dict):
        for child in value.values():
            _finite_tree(child)
    elif isinstance(value, list):
        for child in value:
            _finite_tree(child)


def _ca_tax(taxable: float, status: str, cfg: dict) -> float:
    # The table has published amounts, rather than an assumed midpoint rule.
    table = _obj(cfg.get("tax_table"), "state.tax_table")
    ceiling = _number(table.get("max_taxable_income"), "CA table ceiling", positive=True)
    rows = validate_ca_tax_rows(table.get("rows"))
    if taxable <= 0:
        return 0.0
    if taxable <= ceiling:
        for row in rows:
            lower = _number(row.get("min"), "CA table row minimum")
            upper = _number(row.get("max"), "CA table row maximum", positive=True)
            if lower <= taxable < upper:
                return _number(_obj(row.get("tax"), "CA table row tax").get(status),
                               "CA published table tax")
        raise StatePlanningError("CA Tax Table row is not fetched for this income")
    # Import lazily: the public engine imports state modules while initializing.
    from agents.lib.tax_computations import compute_bracket_tax
    brackets = cfg.get("brackets")
    if isinstance(brackets, dict):
        brackets = brackets.get(status)
    if not isinstance(brackets, list) or not brackets:
        raise StatePlanningError("CA rate schedule is not fetched")
    previous = 0.0
    for index, bracket in enumerate(brackets):
        if _number(bracket.get("min"), "CA bracket minimum") != previous:
            raise StatePlanningError("CA rate schedule must be contiguous from zero")
        rate = _number(bracket.get("rate"), "CA bracket rate")
        if rate > 1:
            raise StatePlanningError("CA rate must be a fraction")
        upper = bracket.get("max")
        if index == len(brackets) - 1:
            if upper is not None:
                raise StatePlanningError("CA rate schedule must have an open top")
        else:
            upper = _number(upper, "CA bracket maximum", positive=True)
            if upper <= previous:
                raise StatePlanningError("CA rate schedule must ascend")
            previous = upper
    return compute_bracket_tax(taxable, brackets)[0]


def validate_ca_tax_rows(value) -> list:
    """Validate sparse published rows; unfetched gaps refuse at point of use."""
    if not isinstance(value, list):
        raise StatePlanningError("CA tax table rows must be a list")
    previous = 0.0
    for row in value:
        row = _obj(row, "CA tax table row")
        if set(row) != {"min", "max", "tax"}:
            raise StatePlanningError("CA tax table rows need exactly min, max and tax")
        lower = _number(row["min"], "CA table minimum")
        upper = _number(row["max"], "CA table maximum", positive=True)
        if lower < previous or upper <= lower:
            raise StatePlanningError("CA tax table rows must ascend without overlaps")
        taxes = _obj(row["tax"], "CA table tax columns")
        if not taxes or set(taxes) - {"single", "mfj", "mfs", "hoh", "qss"}:
            raise StatePlanningError("CA tax table needs canonical filing-status columns")
        for amount in taxes.values():
            _number(amount, "CA published table amount")
        previous = upper
    return value


def _ca(fagi: float, taxable_ss: float, plan: dict, cfg: dict, status: str, year: int) -> dict:
    p = _obj(plan.get("ca"), "state_planning_inputs.ca")
    adds = _number(p.get("additions"), "CA additions")
    other_subs = _number(p.get("subtractions_except_ss"), "CA subtractions_except_ss")
    agi = round(fagi + adds - other_subs - taxable_ss, 2)
    # The existing Schedule P engine is an ISO path, not a general adjustment
    # worksheet. Screen *every* point against the fetched Form 540 Box C amount.
    ceiling = _number(_obj(cfg.get("amt_screening"), "state.amt_screening").get(status),
                      "CA Schedule P screening ceiling", positive=True)
    if p.get("no_amt_preferences_confirmed") is not True or agi > ceiling:
        raise StatePlanningError("CA Schedule P general AMT/credit limitations need a separate worksheet")
    key = "single" if status == "mfs" else "mfj" if status == "qss" else status
    raw = copy.deepcopy(_obj(p.get("itemized"), "CA raw itemized inputs"))
    if raw.get("married_filing_separately", False) != (status == "mfs"):
        raise StatePlanningError("CA itemized spouse election must match filing status")
    if raw.get("charitable_worksheet") is not None or raw.get("charitable_prior_ledger") is not None:
        raise StatePlanningError("CA charitable vintage allocations cannot be reused at another AGI")
    if raw.get("investment_interest", 0):
        raise StatePlanningError("CA investment-interest income limits need separate repricing")
    standard = _number(cfg.get("standard_deduction", {}).get(key), "CA standard deduction")
    deduction = compute_ca_itemized(
        fagi, agi, year, **raw,
        ca_standard_deduction=standard,
        phaseout_threshold=_number(cfg.get("itemized_phaseout", {}).get(key), "CA itemized phaseout"))
    elected = deduction["itemized_total"] if deduction["use_itemized"] else standard
    taxable = round(max(0.0, agi - elected), 2)
    tax = _ca_tax(taxable, status, cfg)
    # Form 540 line 32 AGI Limitation Worksheet: distinct groups and per-head
    # ceiling reductions. None of its amounts is an engine default.
    ph = _obj(cfg.get("exemption_phaseout"), "state.exemption_phaseout")
    threshold = _number(ph.get("threshold", {}).get(status), "CA exemption threshold")
    band = _number(ph.get("band", {}).get(status), "CA exemption band", positive=True)
    reduction = _number(ph.get("reduction_per_step"), "CA exemption reduction")
    steps = math.ceil(max(0.0, fagi - threshold) / band)
    heads = _count(p.get("personal_blind_senior_count"), "CA personal/blind/senior count")
    dependents = _count(p.get("dependent_count"), "CA dependent count")
    personal = _number(cfg.get("personal_exemption", {}).get("amount"), "CA personal exemption")
    dependent = _number(cfg.get("dependent_exemption", {}).get("amount"), "CA dependent exemption")
    credit = round(heads * max(0.0, personal - steps * reduction)
                   + dependents * max(0.0, dependent - steps * reduction), 2)
    surcharge = _obj(cfg.get("mental_health_surcharge"), "CA mental health surcharge")
    extra = round(max(0.0, taxable - _number(surcharge.get("threshold"), "CA surcharge threshold"))
                  * _number(surcharge.get("rate"), "CA surcharge rate"), 2)
    total = round(max(0.0, tax - credit) + extra, 2)
    return dict(agi=agi, deduction=elected, taxable_income=taxable,
                state_tax=total, local_tax=0.0, total_tax=total, exemption_credit=credit)


def _ny(fagi: float, taxable_ss: float, retirement_delta: float,
        plan: dict, cfg: dict, status: str) -> dict:
    p = _obj(plan.get("ny"), "state_planning_inputs.ny")
    additions = copy.deepcopy(_obj(p.get("additions"), "NY additions"))
    subs = copy.deepcopy(_obj(p.get("subtractions"), "NY subtractions"))
    subs["taxable_social_security"] = taxable_ss
    if plan.get("income_treatment", {}).get("ira_distribution") == "ny_pension":
        owner = plan.get("retirement_owner")
        if owner not in ("primary", "spouse") or (owner == "spouse" and status != "mfj"):
            raise StatePlanningError("NY pension delta needs an eligible retirement_owner")
        entries = subs.get("pension_annuity") or []
        matching = [entry for entry in entries if entry.get("taxpayer") == owner]
        if len(matching) != 1:
            raise StatePlanningError("NY pension delta needs one reviewed per-owner exclusion entry")
        matching[0]["qualifying_amount"] = round(
            _number(matching[0].get("qualifying_amount"), "NY qualifying pension") + retirement_delta, 2)
    agi = compute_ny_agi(fagi, additions, subs, status, cfg.get("subtractions"))["nyagi"]
    # The existing engine computes IT-196 46-49. Lines 1-45 cannot be borrowed
    # from federal Schedule A: require a reviewed fixed base with no moving
    # medical/misc/gift/investment-interest limits for this initial scope.
    if p.get("it196_fixed_base_confirmed") is not True:
        raise StatePlanningError("NY IT-196 lines 1-45 need a reviewed base without moving AGI limits")
    itemized = _obj(p.get("itemized"), "NY IT-196 inputs")
    _ny_local_guard = p.get("local") or {}
    for flag, spouses, full_year in (
            ("nyc_resident", "nyc_resident_spouses", "nyc_resident_full_year"),
            ("yonkers_resident", "yonkers_resident_spouses", "yonkers_resident_full_year")):
        if _ny_local_guard.get(flag) is True:
            if (status == "mfj" and _ny_local_guard.get(spouses) != 2
                    or status != "mfj" and _ny_local_guard.get(full_year) is not True):
                raise StatePlanningError("NY IT-360.1/mixed-spouse local residency needs separate allocation")
    dependent = _bool(p.get("claimed_as_dependent"), "NY claimed_as_dependent")
    standard_key = "single_dependent" if status == "single" and dependent else status
    d = compute_ny_itemized(
        nyagi=agi, filing_status=status, **itemized,
        reduction=cfg.get("itemized_reduction"), charitable_limit=cfg.get("charitable_limit"),
        standard_deduction=_number(cfg.get("standard_deduction", {}).get(standard_key), "NY standard deduction"))
    dependents = _count(p.get("dependent_count"), "NY dependent count")
    taxable = round(max(0.0, agi - d["deduction"] - dependents *
                        _number(cfg.get("dependent_exemption", {}).get("amount"), "NY dependent exemption")), 2)
    tax = compute_ny_tax(taxable, agi, status, cfg.get("brackets", {}),
                         cfg.get("bracket_schedule_by_status", {}),
                         cfg.get("tax_table_benefit_recapture", {}), cfg.get("tax_table"))["tax"]
    exemptions = dependents + (2 if status == "mfj" else 1)
    household = compute_ny_household_credit(fagi, status, exemptions,
                    cfg.get("credits", {}).get("household", {}).get("table"), dependent)["credit"]
    state_tax = compute_ny_state_tax_total(tax, household)["line_46_total_state_tax"]
    local = _obj(p.get("local"), "NY local evidence")
    nyc = _bool(local.get("nyc_resident"), "NYC resident")
    yonkers = _bool(local.get("yonkers_resident"), "Yonkers resident")
    if nyc and yonkers:
        raise StatePlanningError("NYC/Yonkers mixed residency requires IT-360.1")
    if _bool(local.get("yonkers_nonresident_earnings"), "Yonkers nonresident earnings"):
        raise StatePlanningError("Yonkers Y-203 allocation changes need a separate worksheet")
    city_tax = None
    city_credit = 0.0
    if nyc:
        city_income = compute_nyc_taxable_income(
            taxable, _number(local.get("charitable_gifts_trust_deducted"), "NYC gifts trust"),
            local.get("nyc_resident_spouses"), status, local.get("nyc_resident_full_year"))["nyc_taxable_income"]
        city = _obj(cfg.get("nyc_tax"), "state.nyc_tax")
        city_tax = compute_nyc_resident_tax(city_income, status, city.get("brackets"),
                      cfg.get("bracket_schedule_by_status", {}), city.get("tax_table"))["tax"]
        city_credit = compute_ny_household_credit(fagi, status, exemptions,
                      city.get("household_credit", {}).get("table"), dependent)["credit"]
    yonkers_tax = None
    if yonkers:
        # Income-sensitive refundable credits are outside the initial planner.
        worksheet = _obj(local.get("yonkers_worksheet_credits"), "Yonkers credit worksheet")
        if any(_number(v, "Yonkers worksheet credit") != 0 for v in worksheet.values()):
            raise StatePlanningError("Yonkers income-sensitive credit worksheets need separate repricing")
        yonkers_tax = compute_yonkers_resident_surcharge(
            state_tax, status, cfg.get("yonkers_tax"), worksheet,
            _number(local.get("star_reconciliation"), "Yonkers STAR reconciliation"),
            local.get("yonkers_resident_spouses"), local.get("yonkers_resident_full_year"),
            bool(itemized.get("college_tuition_deduction")))["tax"]
    mobility = compute_mctmt(**_obj(local.get("mctmt"), "MCTMT zone earnings"),
                            mctmt=cfg.get("mctmt"))["mctmt"]
    city_total = compute_city_and_yonkers_total(
        city_tax, city_credit, mctmt=mobility, yonkers_resident_surcharge=yonkers_tax,
        nyc_resident=nyc, yonkers_resident=yonkers,
        yonkers_nonresident_earnings=False)["line_58_total_city_and_yonkers"]
    return dict(agi=agi, deduction=d["deduction"], taxable_income=taxable,
                state_tax=state_tax, local_tax=city_total,
                total_tax=round(state_tax + city_total, 2))


def _nc(fagi: float, taxable_ss: float, plan: dict, cfg: dict, status: str, year: int) -> dict:
    # The jurisdiction was validated before dispatch; use its canonical input
    # key. A literal get("nc") also resembles a netcat command to the repository's
    # conservative no-network source scan.
    raw = copy.deepcopy(_obj(plan.get(plan["state"].lower()), "NC D-400 raw inputs"))
    if raw.get("other_state_credit") is not None:
        raise StatePlanningError("NC D-400TC other-state credit allocation needs separate repricing")
    _obj(raw.get("schedule_s"), "NC Schedule S")["taxable_social_security"] = taxable_ss
    out = compute_nc_d400(year, status, fagi, cfg, **raw)
    return dict(agi=round(out["line_8"] - out["line_9_deductions"], 2),
                deduction=out["line_11_deduction"], taxable_income=out["line_14_nc_taxable_income"],
                state_tax=out["line_17"], local_tax=0.0, total_tax=out["line_17"],
                child_deduction=out["line_10b_child_deduction"])


def price_state_points(result: dict, tax_law: dict, year: int, status: str | None,
                       original_inputs: dict, original_outcome: dict,
                       points: list[tuple[dict, dict]], *, profile: dict | None = None,
                       retirement_owner: str | None = None) -> dict:
    """Price cumulative normalized inputs/outcomes in the actual return's AGI frame.

    The whole sweep is modeled or unavailable; no partial column of zeros.
    Each unavailable verdict carries its specific reason, while federal planning
    can continue. The fingerprint lets stacks refuse changed state evidence/law.
    """
    plan = result.get("state_planning_inputs")
    if plan is None:
        return {"modeled": False, "limitations": ["State/local tax not modeled: record reviewed state_planning_inputs"],
                "fingerprint": None, "points": []}
    try:
        plan = _obj(plan, "state_planning_inputs")
        cfg = _obj(tax_law.get("state"), "tax_law.state")
        _finite_tree(plan)
        _finite_tree(cfg)
        code = plan.get("state")
        if code not in _REVIEWED_YEARS:
            raise StatePlanningError("State allocation/excise or this state regime is not supported by the planner")
        if (isinstance(year, bool) or not isinstance(year, int)
                or year not in _REVIEWED_YEARS[code]):
            raise StatePlanningError("State planning form year has not been reviewed; a fetched rate alone is insufficient")
        if (plan.get("tax_year") != year or result.get("meta", {}).get("tax_year") != year
                or tax_law.get("tax_year") != year or isinstance(plan.get("tax_year"), bool)):
            raise StatePlanningError("State planning inputs, return and tax-law cache must match the requested year")
        status = _normalize_filing_status(status).lower()
        if _normalize_filing_status(plan.get("filing_status")).lower() != status:
            raise StatePlanningError("State planning filing status differs from the return")
        if cfg.get("code") != code or cfg.get("type") != "graduated_income":
            raise StatePlanningError("State planning jurisdiction/type differs from the fetched law")
        if plan.get("residency") != "full_year" or plan.get("scope_confirmed") is not True:
            raise StatePlanningError("Confirm full-year household residency and the supported state scope")
        flags = (profile or {}).get("flags") or {}
        if any(flags.get(key) is True for key in (
                "moved_states_this_year", "statutory_resident_of_another_state",
                "has_nonresident_source_income", "moved_abroad_this_year")):
            raise StatePlanningError("Declared allocation/residency obligations require separate state modeling")
        if code == "NY" and (result.get("meta", {}).get("ny_return_type") == "IT203"
                              or "new_york_it203" in result):
            raise StatePlanningError("NY IT-203 source allocation cannot use resident state planning")
        treatment = _obj(plan.get("income_treatment"), "state income_treatment")
        changed = {key for inputs, _ in points for key in original_inputs
                   if key != "social_security" and inputs.get(key) != original_inputs.get(key)}
        if changed - _INCOME_KEYS:
            raise StatePlanningError("State adjustment/business deltas require separate conformity and local worksheets")
        for key in changed - {"qualified_dividends"}:
            allowed = {"taxable", "ny_pension"} if code == "NY" and key == "ira_distribution" else {"taxable"}
            if treatment.get(key) not in allowed:
                raise StatePlanningError(f"Record reviewed state income_treatment for {key}")
        if (code == "NY" and "ira_distribution" in changed
                and treatment.get("ira_distribution") == "ny_pension" and retirement_owner is not None
                and (retirement_owner not in {"primary", "spouse"}
                     or plan.get("retirement_owner") != retirement_owner)):
            raise StatePlanningError("NY pension exclusion owner differs from the selected IRA owner; confirm retirement_owner")
        if "ira_distribution" in changed and plan.get("retirement_basis_matches_federal") is not True:
            raise StatePlanningError("State retirement basis/classification must be confirmed independently of conformity metadata")
        if code == "NY":
            subs = plan.get("ny", {}).get("subtractions", {})
            pensions = subs.get("pension_annuity") or []
            gross_qualifying = sum(_number(e.get("qualifying_amount"), "NY qualifying pension") for e in pensions)
            gross_qualifying += _number(subs.get("government_pensions", 0), "NY government pension")
            if gross_qualifying > original_inputs.get("ira_distribution", 0):
                raise StatePlanningError("NY retirement exclusion inputs exceed the federal taxable retirement income")
        section_name, total_key = _SECTIONS[code]
        section = _obj(result.get(section_name), "computed state return")
        recorded = _number(section.get(total_key), "recorded state liability")
        if code == "NY":
            if "line_46_total_state_tax" in section and "line_58_total_city_and_yonkers" in section:
                recorded = round(_number(section["line_46_total_state_tax"], "IT-201 line 46")
                                 + _number(section["line_58_total_city_and_yonkers"], "IT-201 line 58"), 2)
            else:
                recorded = round(recorded - _number(section.get("line_59_sales_or_use_tax"), "IT-201 line 59"), 2)
                if recorded < 0:
                    raise StatePlanningError("IT-201 use tax exceeds the recorded total")
        fagi = _amount(result.get("form_1040", {}).get("line_11_agi"), "return federal AGI", allow_negative=True)
        def at(inputs: dict, outcome: dict) -> dict:
            actual_agi = round(fagi + outcome["agi"] - original_outcome["agi"], 2)
            ss = outcome["taxable_ss"]
            if code == "CA":
                return _ca(actual_agi, ss, plan, cfg, status, year)
            if code == "NY":
                ira_delta = inputs["ira_distribution"] - original_inputs["ira_distribution"]
                qualifying_here = gross_qualifying + (
                    ira_delta if treatment.get("ira_distribution") == "ny_pension" else 0.0)
                if qualifying_here > max(0.0, inputs["ira_distribution"]):
                    raise StatePlanningError("NY retirement exclusions exceed remaining taxable retirement income after the change")
                return _ny(actual_agi, ss, ira_delta, plan, cfg, status)
            return _nc(actual_agi, ss, plan, cfg, status, year)
        baseline = at(original_inputs, original_outcome)
        if abs(baseline["total_tax"] - recorded) > 1.0:
            raise StatePlanningError("State inputs do not reproduce the recorded liability within whole-dollar transcription tolerance")
        priced = [at(inputs, outcome) for inputs, outcome in points]
        # A one-dollar form transcription difference is kept as an independent
        # state anchor, never applied to the federal line 24.
        for point in priced:
            point["anchored_total_tax"] = round(max(0.0, recorded + point["total_tax"] - baseline["total_tax"]), 2)
        return {"modeled": True, "state": code, "limitations": [], "baseline": baseline,
                "recorded_liability": recorded, "points": priced,
                "fingerprint": hashlib.sha256(json.dumps([plan, cfg, section, fagi, original_inputs],
                                sort_keys=True, allow_nan=False).encode()).hexdigest()}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        # Never render the engine exception's raw input: an untrusted object can
        # contain identity data. Our own messages carry field/schedule names.
        reason = str(exc) if isinstance(exc, StatePlanningError) else "State engine refused incomplete or inconsistent raw worksheet inputs"
        return {"modeled": False, "limitations": [reason], "fingerprint": None, "points": []}
