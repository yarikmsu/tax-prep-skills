"""Reviewed residential Schedule E, Form4562 and passive-loss arithmetic.

The maintained scope and source contracts live in rental-income.md. All law
figures are caller-supplied; source/property identifiers are opaque labels.
"""

import calendar
from datetime import date
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
import re

from agents.lib.tax_computations._shared import _normalize_filing_status
from agents.lib.tax_computations.federal.government_benefits import _money


RENTAL_RENTS = "rental_gross_rents"
RENTAL_REPORTED = "rental_reported_rents"
RENTAL_EXPENSES = "rental_operating_expenses"
RENTAL_EXPENSE_LINES = tuple(str(n) for n in (*range(5, 18), 19))
RENTAL_SCOPE_FACTS = (
    "calendar_year_cash_method", "direct_ownership", "long_term_residential", "held_for_rental_income",
    "no_personal_use", "no_self_rental_or_services", "no_other_passive_activities",
    "no_dispositions_grouping_or_credits", "not_real_estate_professional",
    "basis_at_risk_unlimited", "no_other_loss_limit", "no_depreciation_exceptions",
    "ordinary_expenses_conform",
)
RENTAL_NOTE = ("Residential rentals: ScheduleE, building/structural GDS depreciation and passive-loss "
    "limits are computed for the reviewed scope. Basis/at-risk, other loss limits, QBI, NIIT, AMT and "
    "income-dependent forms require their confirmed worksheets. Equipment, ADS, bonus/section179, "
    "amortization, personal/short-term/self-rental, dispositions and special passive cases are not computed.")


def _label(value, what):
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z_-]{0,47}", value) is None:
        raise ValueError(f"{what} must be an opaque alphabetic label, never identity numbers")
    return value


def _year(value):
    if type(value) is not int or value != 2025:
        raise ValueError("residential rental forms have only been reviewed for TY2025")
    return value


def _status(value):
    try:
        return _normalize_filing_status(value)
    except ValueError:
        raise ValueError("rental filing status must be Single/MFJ/MFS/HOH/QSS") from None


def _fraction(value, what, *, positive=False):
    number = _money(value, what)
    # Do not round a rate/ownership share to cents.
    number = float(value)
    if number > 1 or (positive and number <= 0):
        raise ValueError(f"{what} must be a finite fraction")
    return number


def create_rental_law_template() -> dict:
    return {"residential_recovery_years": None, "macrs_first_year": None,
            "minimum_active_ownership": None, "phaseout_rate": None,
            "special_allowance": dict.fromkeys(("single", "mfj", "mfs", "hoh", "qss")),
            "phaseout_ceiling": dict.fromkeys(("single", "mfj", "mfs", "hoh", "qss")),
            "gds_percentages": []}


def validate_rental_percentages(rows) -> list:
    if not isinstance(rows, list) or not rows:
        raise ValueError("fetch the residential GDS month/age percentage table")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"month", "rates"}:
            raise ValueError("GDS percentage rows require month and rates")
        month = row["month"]
        if type(month) is not int or not 1 <= month <= 12 or month in seen:
            raise ValueError("GDS table months must be distinct calendar months")
        seen.add(month)
        rates = row["rates"]
        if not isinstance(rates, list) or not rates or len(rates) > 100:
            raise ValueError("GDS table requires a finite recovery-age rate list")
        for value in rates:
            _fraction(value, "GDS published percentage")
    return rows


def validate_rental_law(law) -> dict:
    if not isinstance(law, dict):
        raise ValueError("fetch the covered-year rental law block")
    life = _money(law.get("residential_recovery_years"), "residential recovery years")
    first = law.get("macrs_first_year")
    if not 0 < life <= 100 or type(first) is not int or not 1900 <= first <= 2025:
        raise ValueError("fetch residential recovery years and the MACRS starting year")
    _fraction(law.get("minimum_active_ownership"), "minimum active ownership", positive=True)
    _fraction(law.get("phaseout_rate"), "rental phaseout rate", positive=True)
    for field in ("special_allowance", "phaseout_ceiling"):
        block = law.get(field)
        if not isinstance(block, dict) or set(block) != {"single", "mfj", "mfs", "hoh", "qss"}:
            raise ValueError(f"fetch all filing-status columns of rental {field}")
        for amount in block.values():
            if _money(amount, f"rental {field}") <= 0:
                raise ValueError(f"rental {field} must be fetched and positive")
    rows = law.get("gds_percentages")
    if rows != []:
        validate_rental_percentages(rows)
    return law


def validate_rental_scope(plan) -> dict:
    if not isinstance(plan, dict) or plan.get("method") != "residential_rental_v1":
        raise ValueError("record the reviewed residential_rental_v1 scope; other rentals require external worksheets")
    _year(plan.get("tax_year"))
    facts = plan.get("scope")
    if not isinstance(facts, dict) or set(facts) != set(RENTAL_SCOPE_FACTS):
        raise ValueError("answer every residential rental scope question explicitly")
    if any(facts[field] is not True for field in RENTAL_SCOPE_FACTS):
        raise ValueError("rental scope requires ScheduleE/4562/8582 and CA3885A/3801 support outside this reviewed path; do not waive missing facts")
    return plan


def rental_in_computed_scope(profile: dict, state, ny_plan) -> bool:
    """Whether this return's rental is the reviewed residential path the engine
    computes. The capability gate and the tie-out both ask, so they agree."""
    from agents.lib.capabilities import _NO_INCOME_TAX_STATES
    from agents.lib.tax_computations.state.ca_nonresident import ca_540nr_plan
    plan = profile.get("rental_return")
    if plan is None:
        return False
    try:
        validate_rental_scope(plan)
        opening = (profile.get("prior_year") or {}).get("rental_carryforward")
        properties = plan.get("properties")
        return (isinstance(properties, list) and bool(properties)
                and profile.get("tax_year") == plan["tax_year"] and not ny_plan
                and not ca_540nr_plan(profile)
                and state in _NO_INCOME_TAX_STATES | {"CA", "WA"}
                and (state == "CA" or not rental_has_ca_history(opening)))
    except ValueError:
        return False


def rental_planning_block(profile: dict, result: dict | None = None) -> bool:
    """PAL and depreciation must be repriced rather than frozen in projections."""
    return (profile.get("rental_return") is not None
        or (profile.get("flags") or {}).get("has_rental_income") is True
        or (profile.get("prior_year") or {}).get("rental_carryforward") is not None
        or ((result or {}).get("schedule_e_rentals") or {}).get("computed_tax_year") is not None)


def rental_has_ca_history(opening) -> bool:
    """Known CA records need a CA closing worksheet, including worked zeros."""
    if opening is None:
        return False
    if not isinstance(opening, dict) or not isinstance(opening.get("properties"), list):
        raise ValueError("rental opening history requires its property inventory")
    for row in opening["properties"]:
        if not isinstance(row, dict) or not isinstance(row.get("assets"), list) or any(not isinstance(a, dict) for a in row["assets"]):
            raise ValueError("rental opening history requires property and asset records")
    return any(row.get("ca_passive_losses") is not None
        or any(a.get("ca_accumulated") is not None for a in row["assets"])
        for row in opening["properties"])


def _asset_facts(asset) -> dict:
    fields = {"asset_id", "kind", "placed_in_service", "federal_method", "ca_method", "federal_basis", "ca_basis", "converted_from_personal", "conversion_fmv"}
    if not isinstance(asset, dict) or set(asset) != fields:
        raise ValueError("rental assets require service/method, separate building bases and conversion facts")
    _label(asset["asset_id"], "rental asset_id")
    if asset["kind"] not in ("building", "structural_improvement"):
        raise ValueError("only buildings and structural improvements are supported rental assets")
    try:
        placed = date.fromisoformat(asset["placed_in_service"])
    except (ValueError, TypeError):
        raise ValueError("rental service date must be an ISO date") from None
    if placed.isoformat() != asset["placed_in_service"]:
        raise ValueError("rental service date must use the canonical ISO format")
    if asset["federal_method"] not in ("formula", "table") or asset["ca_method"] not in (None, "formula", "table"):
        raise ValueError("confirm each jurisdiction's rental depreciation method")
    _money(asset["federal_basis"], "federal building basis")
    if asset["ca_basis"] is not None:
        _money(asset["ca_basis"], "CA building basis")
    if type(asset["converted_from_personal"]) is not bool or asset["converted_from_personal"] != (asset["conversion_fmv"] is not None):
        raise ValueError("confirm conversion and the building-only conversion FMV")
    if asset["conversion_fmv"] is not None:
        _money(asset["conversion_fmv"], "conversion building FMV")
    return asset.copy()


def validate_rental_source(raw, boxes: dict) -> dict:
    fields = {"tax_year", "kind", "reviewed", "allocations_confirmed", "entries"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("rental_source requires the source year, review, allocations and property entries")
    year = raw["tax_year"]
    if raw["kind"] not in ("book", "information_return"):
        raise ValueError("identify rental books separately from overlapping information returns")
    if type(year) is not int or not 2000 <= year <= 2099 or raw["reviewed"] is not True or raw["allocations_confirmed"] is not True:
        raise ValueError("confirm the rental source year and deductible ownership allocations")
    if not isinstance(raw["entries"], list) or not raw["entries"]:
        raise ValueError("rental sources require an explicit property entry, including a worked zero")
    entries, seen = [], set()
    rents = expenses = 0.0
    for row in raw["entries"]:
        if not isinstance(row, dict) or set(row) != {"property_id", "rents", "expenses"}:
            raise ValueError("rental source rows require property_id, rents and reviewed expenses")
        key = _label(row["property_id"], "rental property_id")
        if key in seen:
            raise ValueError("combine duplicate property rows within one source document")
        seen.add(key)
        amounts = row["expenses"]
        if not isinstance(amounts, dict) or set(amounts) != set(RENTAL_EXPENSE_LINES):
            raise ValueError("record every ScheduleE expense line5–17 and19; depreciation is computed separately")
        amounts = {k: _money(v, "reviewed rental expense") for k, v in amounts.items()}
        rent = _money(row["rents"], "rental receipts")
        entries.append({"property_id": key, "rents": rent, "expenses": amounts})
        rents += rent
        expenses += sum(amounts.values())
    info = raw["kind"] == "information_return"
    if info and expenses:
        raise ValueError("an information return cannot replace the complete property expense book")
    expected = {RENTAL_RENTS: 0 if info else rents, RENTAL_EXPENSES: expenses,
                RENTAL_REPORTED: rents if info else 0}
    if not isinstance(boxes, dict) or any(_money(boxes.get(k, 0), k) != round(v, 2)
            for k, v in expected.items()):
        raise ValueError("rental ledger gross receipts/expenses must reconcile to every property source row")
    return {**raw, "entries": entries}


def compute_residential_depreciation(
    *, tax_year: int, placed_in_service: str, basis: float, method: str,
    prior_accumulated: float, law: dict, conversion_fmv: float | None = None,
) -> dict:
    """Form4562 lines17/19i/22: building GDS, no disposition or method change."""
    _year(tax_year)
    cfg = validate_rental_law(law)
    if not isinstance(placed_in_service, str):
        raise ValueError("rental asset service date must be an ISO date")
    try:
        placed = date.fromisoformat(placed_in_service)
    except ValueError:
        raise ValueError("rental asset service date must be an ISO date") from None
    if placed.isoformat() != placed_in_service or not cfg["macrs_first_year"] <= placed.year <= tax_year:
        raise ValueError("asset needs a reviewed MACRS service date no later than the return year")
    cost = _money(basis, "rental depreciable building basis")
    if conversion_fmv is not None:
        cost = min(cost, _money(conversion_fmv, "building FMV at rental conversion"))
    prior = _money(prior_accumulated, "prior allowed/allowable rental depreciation")
    if prior > cost:
        raise ValueError("prior depreciation cannot exceed the reviewed building basis")
    life = Decimal(str(cfg["residential_recovery_years"]))
    start = Decimal(placed.year * 12 + placed.month - 1) + Decimal("0.5")
    end = start + life * 12
    rates = None
    if method == "table":
        rows = validate_rental_percentages(cfg["gds_percentages"])
        rates = next((r["rates"] for r in rows if r["month"] == placed.month), None)
        if rates is None or len(rates) != math.ceil(float(life)) + 1:
            raise ValueError("fetch the complete published month/age row for this rental asset")
    elif method != "formula":
        raise ValueError("confirm the established GDS formula/table method; ADS and other methods require separate support")

    def annual(year):
        age = year - placed.year
        if rates is not None:
            return round(cost * rates[age], 2) if 0 <= age < len(rates) else 0.0
        months = max(Decimal(0), min(end, Decimal((year + 1) * 12)) - max(start, Decimal(year * 12)))
        return round(float(Decimal(str(cost)) * months / (life * 12)), 2)

    expected = round(min(cost, sum(annual(year) for year in range(placed.year, tax_year))), 2)
    # Imported annual schedules can round each year's deduction to whole
    # dollars. Bound only that representational difference, not a missed year.
    rounding_bound = max(0.005, (tax_year - placed.year) / 2)
    if abs(prior - expected) > rounding_bound:
        raise ValueError("reconcile prior depreciation; a basis/method change or missed deduction needs the Form3115/amended-return worksheet")
    current = round(min(annual(tax_year), max(0.0, cost - prior)), 2)
    return {"depreciable_basis": cost, "method": method, "placed_in_service": placed_in_service,
            "prior_expected": expected, "opening_accumulated": prior,
            "current_depreciation": current, "closing_accumulated": round(prior + current, 2),
            "remaining_basis": round(cost - prior - current, 2),
            "form_4562_line": "19i" if placed.year == tax_year else "17"}


def _allocate(amount: float, weights: list[float]) -> list[float]:
    """Largest remainder at cents, preserving both total and each capacity."""
    total = int(round(amount * 100))
    units = [int(round(w * 100)) for w in weights]
    denominator = sum(units)
    if total < 0 or total > denominator:
        raise ValueError("passive-loss allocation exceeds the available property losses")
    if not denominator:
        return [0.0] * len(weights)
    exact = [Decimal(total) * w / denominator for w in units]
    allocated = [int(v.to_integral_value(rounding=ROUND_FLOOR)) for v in exact]
    for i in sorted(range(len(weights)), key=lambda j: (-(exact[j] - allocated[j]), j))[:total - sum(allocated)]:
        allocated[i] += 1
    return [v / 100 for v in allocated]


def _loss_origins(raw, what):
    if not isinstance(raw, dict) or set(raw) != {"active_origin", "other_origin"}:
        raise ValueError(f"{what} requires separate active-origin and other-origin losses")
    return {k: _money(raw[k], what) for k in ("active_origin", "other_origin")}


def compute_rental_passive_losses(
    *, tax_year: int, filing_status: str, lived_apart_all_year: bool | None,
    properties: list, magi: float, law: dict,
) -> dict:
    """8582/3801 PartsI–III and VI–VIII, retaining origin-year eligibility.

    Prior losses qualify for PartIV only when participation qualified BOTH
    when they arose and now (2025 instructions, PartIV). Current participation
    cannot promote an old nonactive loss into the special allowance.
    """
    _year(tax_year)
    status = _status(filing_status).lower()
    cfg = validate_rental_law(law)
    income = max(0.0, _money(magi, "Form8582 federal MAGI", signed=True))
    if status == "mfs" and type(lived_apart_all_year) is not bool:
        raise ValueError("answer whether separate spouses lived apart at all times during the year")
    if not isinstance(properties, list) or not properties:
        raise ValueError("passive-loss worksheet requires the complete property pool")
    rows, components, seen = [], [], set()
    for row in properties:
        if not isinstance(row, dict) or set(row) != {"property_id", "current_net", "prior_losses", "active_eligible"}:
            raise ValueError("PAL property rows require current net, prior loss origins and active eligibility")
        key = _label(row["property_id"], "rental property_id")
        if key in seen or type(row["active_eligible"]) is not bool:
            raise ValueError("PAL properties must be distinct and active eligibility must be explicit")
        seen.add(key)
        net = _money(row["current_net"], "current rental net", signed=True)
        prior = _loss_origins(row["prior_losses"], "prior rental passive losses")
        item = {"property_id": key, "current_net": net, "prior_losses": prior,
                "active_eligible": row["active_eligible"]}
        rows.append(item)
        if row["active_eligible"] and not (status == "mfs" and lived_apart_all_year is not True):
            components.extend([
                {"property_id": key, "part": "IV", "current_net": net,
                 "origins": {"active_origin": round(prior["active_origin"] + max(0.0, -net), 2), "other_origin": 0.0},
                 "prior": prior["active_origin"]},
                {"property_id": key, "part": "V", "current_net": 0.0,
                 "origins": {"active_origin": 0.0, "other_origin": prior["other_origin"]},
                 "prior": prior["other_origin"]}])
        else:
            origins = prior.copy()
            origin = "active_origin" if row["active_eligible"] else "other_origin"
            origins[origin] = round(origins[origin] + max(0.0, -net), 2)
            components.append({"property_id": key, "part": "V", "current_net": net,
                               "origins": origins, "prior": round(sum(prior.values()), 2)})
    for item in components:
        item["overall"] = round(item["current_net"] - item["prior"], 2)

    def group(part):
        selected = [r for r in components if r["part"] == part]
        return {"income": round(sum(max(0, r["current_net"]) for r in selected), 2),
                "loss": round(sum(max(0, -r["current_net"]) for r in selected), 2),
                "prior": round(sum(r["prior"] for r in selected), 2),
                "net": round(sum(r["overall"] for r in selected), 2)}

    a, b = group("IV"), group("V")
    overall = round(a["net"] + b["net"], 2)
    line4 = min(max(0.0, -a["net"]), max(0.0, -overall))
    ceiling = cfg["phaseout_ceiling"][status]
    allowance = round(min(cfg["special_allowance"][status],
                          max(0.0, ceiling - income) * cfg["phaseout_rate"]), 2)
    applies = line4 > 0 and not (status == "mfs" and lived_apart_all_year is not True)
    if not applies:
        allowance = 0.0
    special = round(min(line4, allowance), 2)
    unallowed = round(max(0.0, -overall - special), 2)
    losses = [max(0.0, -r["overall"]) for r in components]
    special_rows = _allocate(special, [v if r["part"] == "IV" else 0 for r, v in zip(components, losses)])
    remaining = [round(v - v_special, 2) for v, v_special in zip(losses, special_rows)]
    closing = _allocate(unallowed, remaining)
    for component, extra, denied in zip(components, special_rows, closing):
        origin_amounts = _allocate(denied, list(component["origins"].values()))
        component.update(special_allowance=extra, carryforward=denied,
                         carryforward_by_origin=dict(zip(component["origins"], origin_amounts)))
    for row in rows:
        selected = [r for r in components if r["property_id"] == row["property_id"]]
        denied = round(sum(r["carryforward"] for r in selected), 2)
        allowed = round(max(0.0, -row["current_net"]) + sum(row["prior_losses"].values()) - denied, 2)
        row.update(special_allowance=round(sum(r["special_allowance"] for r in selected), 2),
                   carryforward=denied, deductible_loss=allowed,
                   carryforward_by_origin={k: round(sum(r["carryforward_by_origin"][k] for r in selected), 2)
                                           for k in ("active_origin", "other_origin")},
                   taxable_net=round(max(0.0, row["current_net"]) - allowed, 2))
    profits = round(sum(max(0.0, r["current_net"]) for r in rows), 2)
    deductions = round(sum(r["deductible_loss"] for r in rows), 2)
    return {"line_1a": a["income"], "line_1b": a["loss"], "line_1c": a["prior"], "line_1d": a["net"],
            "line_2a": b["income"], "line_2b": b["loss"], "line_2c": b["prior"], "line_2d": b["net"],
            "line_3": overall, "line_4": line4, "line_5": ceiling, "line_6": income,
            "part_ii_applies": applies, "line_8": allowance, "line_9": special, "unallowed_total": unallowed,
            "components": components, "properties": rows, "schedule_e_line24": profits, "schedule_e_line25": deductions,
            "schedule_e_line26": round(profits - deductions, 2), "notes": [RENTAL_NOTE]}


def validate_rental_carryforward(raw, tax_year: int) -> dict:
    """Closing records are opening records only for the immediately next year."""
    if not isinstance(raw, dict) or set(raw) != {"tax_year", "properties", "asset_inventory"}:
        raise ValueError("rental carryforward requires its tax year, property balances and asset inventory")
    if type(raw["tax_year"]) is not int or raw["tax_year"] != tax_year:
        raise ValueError("rental carryforward year does not match the source return")
    if not isinstance(raw["properties"], list) or not isinstance(raw["asset_inventory"], list):
        raise ValueError("rental closing properties and inventory must be lists")
    out, seen = [], set()
    for row in raw["properties"]:
        fields = {"property_id", "federal_passive_losses", "ca_passive_losses", "assets"}
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("rental closing property requires separate passive balances and asset accumulation")
        key = _label(row["property_id"], "rental property_id")
        if key in seen or not isinstance(row["assets"], list):
            raise ValueError("rental closing properties must be distinct with an asset list")
        seen.add(key)
        assets, asset_ids = [], set()
        for asset in row["assets"]:
            if not isinstance(asset, dict) or set(asset) != {"asset_id", "federal_accumulated", "ca_accumulated"}:
                raise ValueError("rental closing asset requires separate accumulated depreciation")
            aid = _label(asset["asset_id"], "rental asset_id")
            if aid in asset_ids:
                raise ValueError("duplicate closing rental asset")
            asset_ids.add(aid)
            assets.append({"asset_id": aid, "federal_accumulated": _money(asset["federal_accumulated"], "federal accumulated depreciation"),
                           "ca_accumulated": None if asset["ca_accumulated"] is None else _money(asset["ca_accumulated"], "CA accumulated depreciation")})
        out.append({"property_id": key, "federal_passive_losses": _loss_origins(row["federal_passive_losses"], "federal suspended rental losses"),
                    "ca_passive_losses": None if row["ca_passive_losses"] is None else _loss_origins(row["ca_passive_losses"], "CA suspended rental losses"), "assets": assets})
    # Inventory has no addresses or document filenames; ownership/asset facts
    # are proposals to re-review, not next year's confirmed inputs.
    inventories, inventory_ids = [], set()
    for row in raw["asset_inventory"]:
        if not isinstance(row, dict) or set(row) != {"property_id", "assets"}:
            raise ValueError("rental inventory requires property_id and source asset facts")
        key = _label(row["property_id"], "rental inventory property")
        if key not in seen or key in inventory_ids or not isinstance(row["assets"], list):
            raise ValueError("rental inventory does not match its closing property balances")
        inventory_ids.add(key)
        facts = [_asset_facts(a) for a in row["assets"]]
        ids = [a["asset_id"] for a in facts]
        balances = next(p for p in out if p["property_id"] == key)
        if len(set(ids)) != len(ids) or set(ids) != {a["asset_id"] for a in balances["assets"]}:
            raise ValueError("rental closing asset facts and balances must have the same distinct inventory")
        for asset in facts:
            balance = next(a for a in balances["assets"] if a["asset_id"] == asset["asset_id"])
            if date.fromisoformat(asset["placed_in_service"]).year > tax_year:
                raise ValueError("closing rental inventory cannot contain a future asset")
            for prefix in ("federal", "ca"):
                accumulated, basis = balance[f"{prefix}_accumulated"], asset[f"{prefix}_basis"]
                if prefix == "ca" and (accumulated is None) != (balances["ca_passive_losses"] is None):
                    raise ValueError("closing CA rental assets and passive balances must be present together")
                if accumulated is not None:
                    cost = _money(basis, "closing rental building basis")
                    if asset["conversion_fmv"] is not None:
                        cost = min(cost, asset["conversion_fmv"])
                    if accumulated > cost:
                        raise ValueError("closing accumulated rental depreciation exceeds depreciable basis")
        inventories.append({"property_id": key, "assets": facts})
    if inventory_ids != seen:
        raise ValueError("a closing rental property inventory was dropped")
    return {"tax_year": tax_year, "properties": out, "asset_inventory": inventories}


def rental_carryforward_from_workpaper(paper, tax_year: int) -> dict:
    """Rollover checks closing records against the retained computed schedules."""
    if not isinstance(paper, dict) or type(paper.get("computed_tax_year")) is not int or paper["computed_tax_year"] != tax_year:
        raise ValueError("rental closing workpaper needs its covered-year computed witness")
    carry = validate_rental_carryforward(paper.get("carryforward"), tax_year)
    rows = paper.get("properties")
    if not isinstance(rows, list) or not rows:
        raise ValueError("computed rental workpaper has no property inventory")
    try:
        ids = [r["property_id"] for r in rows]
        federal = {r["property_id"]: r for r in paper["federal"]["properties"]}
        ca = {r["property_id"]: r for r in paper["california"]["properties"]} if paper["california"] is not None else None
        closing = {r["property_id"]: r for r in carry["properties"]}
        inventory = {r["property_id"]: r["assets"] for r in carry["asset_inventory"]}
        if len(ids) != len(set(ids)) or set(ids) != set(closing) or set(ids) != set(federal) or (ca is not None and set(ca) != set(ids)):
            raise ValueError("a rental property closing balance was dropped")
        for row in rows:
            key = row["property_id"]
            expected_assets = [{"asset_id": a["asset_id"], "federal_accumulated": a["federal"]["closing_accumulated"],
                "ca_accumulated": a["california"]["closing_accumulated"] if ca is not None else None} for a in row["assets"]]
            if (closing[key]["federal_passive_losses"] != federal[key]["carryforward_by_origin"]
                    or closing[key]["ca_passive_losses"] != (ca[key]["carryforward_by_origin"] if ca is not None else None)
                    or closing[key]["assets"] != expected_assets
                    or inventory[key] != [a["source"] for a in row["assets"]]):
                raise ValueError("rental closing balances disagree with the retained passive/depreciation schedules")
    except (KeyError, TypeError, AttributeError):
        raise ValueError("rental closing workpaper is incomplete; restore every property and asset schedule") from None
    return carry


def compute_residential_rental_return(
    *, tax_year: int, filing_status: str, plan: dict, source_records: list,
    opening: dict | None, federal_law: dict, ca_law: dict | None,
    income_components: dict, nonrental_schedule1_income: float,
) -> dict:
    """Source books → depreciation/PAL → ScheduleE and ScheduleCA difference."""
    _year(tax_year)
    status = _status(filing_status)
    validate_rental_scope(plan)
    if plan["tax_year"] != tax_year:
        raise ValueError("rental plan and return tax years disagree")
    fed = validate_rental_law(federal_law)
    ca = validate_rental_law(ca_law) if ca_law is not None else None
    properties = plan.get("properties")
    if not isinstance(properties, list) or not properties or not isinstance(source_records, list):
        raise ValueError("record the complete rental property and source inventory")
    prior = validate_rental_carryforward(opening, tax_year - 1) if opening is not None else {"properties": [], "asset_inventory": []}
    if ca is None and rental_has_ca_history(prior):
        raise ValueError("known CA rental history needs separate CA closing worksheets; a federal-only path cannot advance or discard those balances")
    previous = {row["property_id"]: row for row in prior["properties"]}
    sources, source_ids = [], set()
    for rec in source_records:
        if not isinstance(rec, dict) or set(rec) != {"doc_id", "key_boxes", "rental_source"}:
            raise ValueError("rental records require a ledger identifier, canonical totals and source facts")
        did = rec["doc_id"]
        if not isinstance(did, str) or not did.strip() or did in source_ids:
            raise ValueError("rental source identifiers must be distinct")
        source_ids.add(did)
        source = validate_rental_source(rec["rental_source"], rec["key_boxes"])
        if source["tax_year"] != tax_year:
            raise ValueError("rental books must cover the return year")
        sources.append({"doc_id": did, **source})
    rows, seen, inventory = [], set(), []
    for prop in properties:
        fields = {"property_id", "address", "property_type", "days_rented", "personal_use_days",
                  "new_activity_this_year", "active_participation", "minimum_ownership_fraction",
                  "source_ids", "assets", "basis_and_history_confirmed"}
        if not isinstance(prop, dict) or set(prop) != fields:
            raise ValueError("rental property requires the complete address, type/days, ownership, sources, assets and basis review")
        key = _label(prop["property_id"], "rental property_id")
        if key in seen:
            raise ValueError("duplicate rental property")
        seen.add(key)
        from agents.lib.capabilities import _USPS_STATES, _TERRITORIES
        address = prop["address"]
        if (not isinstance(address, dict) or set(address) != {"street", "city", "state", "zip"}
                or any(not isinstance(v, str) or not v.strip() for v in address.values())
                or address["state"] not in _USPS_STATES - _TERRITORIES
                or re.fullmatch(r"\d{5}(?:-\d{4})?", address["zip"]) is None):
            raise ValueError("rental property requires a reviewed U.S. street/city/state/ZIP address")
        if prop["property_type"] not in ("single_family", "multi_family"):
            raise ValueError("only long-term residential buildings are in the reviewed rental scope")
        if type(prop["days_rented"]) is not int or not 0 <= prop["days_rented"] <= 365 + calendar.isleap(tax_year):
            raise ValueError("rental days must fit the covered calendar year")
        if type(prop["personal_use_days"]) is not int or prop["personal_use_days"] != 0:
            raise ValueError("personal-use rentals require the separate vacation-home worksheet")
        if (type(prop["new_activity_this_year"]) is not bool or type(prop["active_participation"]) is not bool
                or prop["basis_and_history_confirmed"] is not True):
            raise ValueError("confirm activity history, active participation and basis/allowed depreciation")
        ownership = _fraction(prop["minimum_ownership_fraction"], "minimum ownership share", positive=True)
        expected_sources = prop["source_ids"]
        if not isinstance(expected_sources, list) or not expected_sources or any(not isinstance(v, str) for v in expected_sources) or len(set(expected_sources)) != len(expected_sources):
            raise ValueError("rental property requires distinct, reconciled source_ids, including a zero-income book")
        contributions = [(rec["doc_id"], entry) for rec in sources for entry in rec["entries"] if entry["property_id"] == key]
        if {did for did, _entry in contributions} != set(expected_sources):
            raise ValueError("rental property source inventory disagrees with the ledger; re-read the missing books")
        books = [(rec["doc_id"], entry) for rec in sources if rec["kind"] == "book"
                 for entry in rec["entries"] if entry["property_id"] == key]
        if not books:
            raise ValueError("every rental property needs its complete book, including zero; a 1099 is not a cash book")
        rents = round(sum(entry["rents"] for _did, entry in books), 2)
        reported = round(sum(entry["rents"] for rec in sources if rec["kind"] == "information_return"
                             for entry in rec["entries"] if entry["property_id"] == key), 2)
        if reported > rents:
            raise ValueError("information-return rents exceed the reconciled property book; resolve corrected/duplicate or disputed forms before computation")
        expenses = {line: round(sum(entry["expenses"][line] for _did, entry in books), 2) for line in RENTAL_EXPENSE_LINES}
        old = previous.get(key)
        if prop["new_activity_this_year"]:
            if old is not None:
                raise ValueError("new rental activity contradicts an opening carryforward")
            old = {"federal_passive_losses": {"active_origin": 0.0, "other_origin": 0.0}, "ca_passive_losses": {"active_origin": 0.0, "other_origin": 0.0}, "assets": []}
        elif old is None:
            raise ValueError("reconcile the existing rental's separate opening passive and depreciation balances")
        if ca is not None and old["ca_passive_losses"] is None:
            raise ValueError("reconstruct CA opening losses separately; do not copy the federal balance")
        old_assets = {asset["asset_id"]: asset for asset in old["assets"]}
        assets = prop["assets"]
        if not isinstance(assets, list) or not assets:
            raise ValueError("record the building and every structural improvement, even fully depreciated assets")
        papers, aids = [], set()
        for asset in assets:
            asset = _asset_facts(asset)
            aid = asset["asset_id"]
            if aid in aids:
                raise ValueError("rental assets must be distinct")
            aids.add(aid)
            placed = date.fromisoformat(asset["placed_in_service"])
            old_asset = old_assets.get(aid)
            if placed.year == tax_year:
                if old_asset is not None:
                    raise ValueError("new rental asset contradicts prior accumulated depreciation")
                old_asset = {"federal_accumulated": 0.0, "ca_accumulated": 0.0}
            elif old_asset is None or prop["new_activity_this_year"]:
                raise ValueError("existing rental asset needs its prior allowed/allowable depreciation schedule")
            if old_asset is not None and placed.year < tax_year:
                old_inventory = next(p["assets"] for p in prior["asset_inventory"] if p["property_id"] == key)
                if asset != next(a for a in old_inventory if a["asset_id"] == aid):
                    raise ValueError("rental basis, service or method changed; reconcile Form3115/amendment and the correction worksheet before computation")
            common = {"tax_year": tax_year, "placed_in_service": asset["placed_in_service"],
                      "conversion_fmv": asset["conversion_fmv"]}
            f = compute_residential_depreciation(**common, method=asset["federal_method"], basis=asset["federal_basis"], prior_accumulated=old_asset["federal_accumulated"], law=fed)
            c = compute_residential_depreciation(**common, method=asset["ca_method"], basis=asset["ca_basis"], prior_accumulated=old_asset["ca_accumulated"], law=ca) if ca is not None else None
            papers.append({"asset_id": aid, "source": asset.copy(), "federal": f, "california": c})
        if set(old_assets) - aids:
            raise ValueError("a prior rental asset was dropped; dispositions/method changes require their own worksheet")
        amount = round(sum(expenses.values()), 2)
        net_f = round(rents - amount - sum(a["federal"]["current_depreciation"] for a in papers), 2)
        net_c = round(rents - amount - sum(a["california"]["current_depreciation"] for a in papers), 2) if ca is not None else None
        rows.append({"property_id": key, "address": address.copy(), "property_type": prop["property_type"],
            "days_rented": prop["days_rented"], "personal_use_days": 0, "rents": rents, "expenses": expenses,
            "operating_expenses": amount, "assets": papers, "federal_current_net": net_f, "ca_current_net": net_c,
            "federal_prior_loss": old["federal_passive_losses"], "ca_prior_loss": old["ca_passive_losses"],
            "active_participation": prop["active_participation"], "minimum_ownership_fraction": ownership})
        inventory.append({"property_id": key, "assets": [a.copy() for a in assets]})
    if set(previous) - seen or any(entry["property_id"] not in seen for rec in sources for entry in rec["entries"]):
        raise ValueError("a rental activity was dropped from the property/source/opening inventory")
    adjustments = plan.get("magi_adjustments")
    if not isinstance(adjustments, dict) or set(adjustments) != {"reviewed", "included_agi_adjustments", "excluded_agi_adjustments", "excluded_income_addbacks"} or adjustments["reviewed"] is not True:
        raise ValueError("confirm every Form8582 line6 adjustment/exclusion component; never enter final MAGI")
    included = _money(adjustments["included_agi_adjustments"], "included MAGI adjustments")
    excluded = _money(adjustments["excluded_agi_adjustments"], "excluded MAGI adjustments")
    addbacks = _money(adjustments["excluded_income_addbacks"], "Form8582 excluded-income addbacks")
    if not isinstance(income_components, dict):
        raise ValueError("rental MAGI requires the return's non-rental income components")
    if round(included + excluded, 2) != _money(income_components.get("line_10_adjustments"), "Form1040 adjustments"):
        raise ValueError("Form8582 adjustment components must reconcile to Form1040 line10; refresh income-dependent deductions")
    nonrental = _money(nonrental_schedule1_income, "non-rental Schedule1 income", signed=True)
    gross = sum(_money(income_components.get(field), "Form8582 income component", signed=field == "line_7_capital_gain") for field in
                ("line_1z_total_wages", "line_2b_taxable_interest", "line_3b_ordinary_dividends", "line_4b_taxable_ira", "line_5b_taxable_pensions", "line_7_capital_gain"))
    passive_gain = max(0.0, round(sum(row["federal_current_net"] - sum(row["federal_prior_loss"].values()) for row in rows), 2))
    magi = round(max(0.0, gross + nonrental + passive_gain - included + addbacks), 2)

    def limit(jurisdiction, cfg):
        prefix = "federal" if jurisdiction == "federal" else "ca"
        return compute_rental_passive_losses(tax_year=tax_year, filing_status=status,
            lived_apart_all_year=plan.get("lived_apart_all_year"), magi=magi, law=cfg,
            properties=[{"property_id": row["property_id"], "current_net": row[f"{prefix}_current_net"],
                "prior_losses": row[f"{prefix}_prior_loss"], "active_eligible": row["active_participation"] and row["minimum_ownership_fraction"] >= cfg["minimum_active_ownership"]} for row in rows])

    fpal = limit("federal", fed)
    cpal = limit("california", ca) if ca is not None else None
    closing = []
    for i, row in enumerate(rows):
        closing.append({"property_id": row["property_id"], "federal_passive_losses": fpal["properties"][i]["carryforward_by_origin"].copy(),
            "ca_passive_losses": cpal["properties"][i]["carryforward_by_origin"].copy() if cpal is not None else None,
            "assets": [{"asset_id": asset["asset_id"], "federal_accumulated": asset["federal"]["closing_accumulated"],
                        "ca_accumulated": asset["california"]["closing_accumulated"] if ca is not None else None} for asset in row["assets"]]})
        fdep = round(sum(a["federal"]["current_depreciation"] for a in row["assets"]), 2)
        cdep = round(sum(a["california"]["current_depreciation"] for a in row["assets"]), 2) if ca is not None else None
        row["form_4562"] = {"line_17": round(sum(a["federal"]["current_depreciation"] for a in row["assets"] if a["federal"]["form_4562_line"] == "17"), 2),
            "line_19i": round(sum(a["federal"]["current_depreciation"] for a in row["assets"] if a["federal"]["form_4562_line"] == "19i"), 2), "line_22": fdep}
        row["ftb_3885a"] = ({"line_4": round(sum(a["california"]["current_depreciation"] for a in row["assets"] if a["california"]["form_4562_line"] == "19i"), 2),
            "line_5": round(sum(a["california"]["current_depreciation"] for a in row["assets"] if a["california"]["form_4562_line"] == "17"), 2),
            "line_6": cdep, "line_7": fdep, "line_8a": round(max(0.0, cdep - fdep), 2), "line_8b": round(max(0.0, fdep - cdep), 2)} if ca is not None else None)
    diff = round(cpal["schedule_e_line26"] - fpal["schedule_e_line26"], 2) if cpal is not None else 0.0
    used_income = {field: income_components.get(field) for field in
        ("line_1z_total_wages", "line_2b_taxable_interest", "line_3b_ordinary_dividends", "line_4b_taxable_ira",
         "line_5b_taxable_pensions", "line_7_capital_gain", "line_10_adjustments")}
    digest_inputs = {"plan": {k: v for k, v in plan.items() if k != "related_forms_review"},
        "sources": source_records, "opening": opening, "federal_law": federal_law,
        "ca_law": ca_law, "income": used_income, "nonrental_schedule1_income": nonrental,
        "tax_year": tax_year, "filing_status": status}
    digest = hashlib.sha256(json.dumps(digest_inputs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    review = plan.get("related_forms_review")
    reviewed = (isinstance(review, dict) and set(review) == {"input_digest", "qbi_reviewed", "niit_reviewed", "amt_reviewed", "income_dependent_forms_reviewed"}
        and review["input_digest"] == digest and all(review[k] is True for k in
            ("qbi_reviewed", "niit_reviewed", "amt_reviewed", "income_dependent_forms_reviewed")))
    return {"computed_tax_year": tax_year, "properties": rows, "sources": sources,
        "gross_rents": round(sum(row["rents"] for row in rows), 2),
        "reported_rents": round(sum(entry["rents"] for rec in sources if rec["kind"] == "information_return" for entry in rec["entries"]), 2),
        "operating_expenses": round(sum(row["operating_expenses"] for row in rows), 2),
        "federal_magi": magi, "federal": fpal, "california": cpal,
        "ca_addition": max(0.0, diff), "ca_subtraction": max(0.0, -diff),
        "nonrental_schedule1_income": nonrental, "total_schedule1": round(nonrental + fpal["schedule_e_line26"], 2),
        "carryforward": {"tax_year": tax_year, "properties": closing, "asset_inventory": inventory},
        "related_forms_input_digest": digest, "related_forms_review_current": reviewed,
        "source_note": RENTAL_NOTE}
