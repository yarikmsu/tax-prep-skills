"""Washington individual LT capital-gains excise (RCW 82.87), TY2025.

No annual amount/rate is inline. Eligibility and sourcing are explicit inputs;
special federal gain rules and separate household allocations refuse.
"""

import copy
import math

from agents.lib.tax_computations._shared import _normalize_filing_status, _round_half_up as _decimal_round


def _round_half_up(value):
    return float(_decimal_round(value, 2))


def _number(value, name, *, signed=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (not signed and value < 0)):
        raise ValueError(f"{name}: require a finite {'signed' if signed else 'nonnegative'} amount")
    return float(value)


def _confirmed(value, name):
    if value is not True:
        raise ValueError(f"{name}: explicit confirmation required")


def compute_wa_capital_gains(
    tax_year, filing_status, federal_net_long_term, state_cfg, *,
    federal_lt_carryforward_used, excluded_lt_carryforward,
    adjustments, classification_complete, full_year_wa_domicile,
    registered_domestic_partner, special_federal_gain_rules, household_is_separate,
    qualifying_business_deduction, business_eligibility_confirmed,
    qualifying_charitable_contributions, charity_eligibility_confirmed,
    other_jurisdiction_credits, credits_verified,
    b_o_tax_paid_on_same_gains, b_o_eligibility_confirmed,
    payments, payments_confirmed, extension_requested, payment_made,
):
    """Price a confirmed household worksheet; never infer an absent zero.

    `adjustments`: unique asset/group `id`, signed `gain_loss`, and `reason`
    (`exempt` or `nonallocated`). Both gains and losses are removed. These
    amounts must already be INCLUDED in Schedule D line 15; excluded line 14
    carryforwards belong only in `excluded_lt_carryforward` (pre-2022,
    exempt or nonallocated, mutually exclusive and included federally).

    `other_jurisdiction_credits`: unique `jurisdiction`, `tax_paid`, and the
    independently substantiated `wa_tax_on_same_gains`; this is tax allocation,
    not simply gain × top marginal rate. Same-asset eligibility and absence
    of overlap with B&O must be confirmed via `credits_verified`.
    """
    if type(tax_year) is not int or tax_year != 2025:
        raise ValueError("WA module covers source-verified TY2025 only")
    status = _normalize_filing_status(filing_status)
    if status not in {"SINGLE", "MFJ", "MFS", "HOH", "QSS"}:
        raise ValueError("Invalid WA filing status")
    if type(registered_domestic_partner) is not bool:
        raise ValueError("Answer registered_domestic_partner explicitly")
    if household_is_separate is not False:
        raise ValueError("Confirm no separate household allocation is required")
    if status == "MFS" or registered_domestic_partner:
        raise ValueError("Separate spouses/RDPs require a household community allocation under RCW 82.87.120; not modeled")
    _confirmed(full_year_wa_domicile, "full_year_wa_domicile")
    _confirmed(classification_complete, "classification_complete (all exemptions and allocations reviewed)")
    if special_federal_gain_rules is not False:
        raise ValueError("Special federal gain rules (1256/QOF) require WA normalization; not modeled")
    if (not isinstance(state_cfg, dict) or state_cfg.get("code") != "WA"
            or state_cfg.get("type") != "capital_gains_excise"
            or state_cfg.get("brackets") != []):
        raise ValueError("Require WA capital_gains_excise cache with EMPTY ordinary brackets")
    cfg = state_cfg.get("wa_capital_gains")
    if not isinstance(cfg, dict) or type(cfg.get("tax_year")) is not int or cfg["tax_year"] != tax_year:
        raise ValueError("Fetch WA parameters explicitly naming this tax year")
    deduction = _number(cfg.get("standard_deduction"), "fetched WA standard_deduction")
    char_min = _number(cfg.get("charitable_minimum"), "fetched WA charitable_minimum")
    char_cap = _number(cfg.get("charitable_cap"), "fetched WA charitable_cap")
    if not isinstance(cfg.get("rates"), list) or not cfg["rates"]:
        raise ValueError("Fetch WA excise rate bands")
    net_lt = _number(federal_net_long_term, "Schedule D line 15", signed=True)
    cf_used = _number(federal_lt_carryforward_used, "Schedule D line 14 carryforward used")
    cf_excluded = _number(excluded_lt_carryforward, "excluded LT carryforward")
    if cf_excluded > cf_used:
        raise ValueError("Excluded carryforwards cannot exceed those included in line 14")
    if not isinstance(adjustments, list):
        raise ValueError("Provide classification rows, or a confirmed empty list")
    seen = set()
    removed = 0.0
    for row in adjustments:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise ValueError("Every classification row needs an asset/group id")
        if row["id"] in seen:
            raise ValueError("An asset cannot be excluded twice")
        seen.add(row["id"])
        if row.get("reason") not in {"exempt", "nonallocated"}:
            raise ValueError("Classification reason must be exempt or nonallocated")
        removed += _number(row.get("gain_loss"), "classified gain/loss", signed=True)
    removed = _number(removed, "total classified adjustments", signed=True)
    adjusted = _number(net_lt + cf_excluded - removed, "adjusted WA gains", signed=True)
    business = _number(qualifying_business_deduction, "qualifying_business_deduction")
    charity = _number(qualifying_charitable_contributions, "qualifying_charitable_contributions")
    if business:
        _confirmed(business_eligibility_confirmed, "RCW 82.87.070 family-business eligibility")
    if charity:
        _confirmed(charity_eligibility_confirmed, "RCW 82.87.080 WA-directed charity eligibility")
    if business > max(0.0, adjusted):
        raise ValueError("Business deduction cannot exceed adjusted gains")
    char_deduction = min(char_cap, max(0.0, charity - char_min))
    taxable = max(0.0, adjusted - deduction - business - char_deduction)
    from agents.lib.tax_computations import compute_bracket_tax
    from agents.lib.tax_law_fetcher import _check_brackets
    bands = copy.deepcopy(cfg["rates"])
    _check_brackets("state.wa_capital_gains.rates", bands)
    before_credits, _ = compute_bracket_tax(taxable, bands)
    _number(before_credits, "computed WA tax")
    if not isinstance(other_jurisdiction_credits, list):
        raise ValueError("Provide verified credit rows or a confirmed empty list")
    other_credit = 0.0
    seen_jurisdictions = set()
    for row in other_jurisdiction_credits:
        if not isinstance(row, dict):
            raise ValueError("Credit rows must be objects")
        jurisdiction = row.get("jurisdiction")
        if (not isinstance(jurisdiction, str) or not jurisdiction.strip()
                or jurisdiction.strip().upper() in {"WA", "WASHINGTON"}
                or jurisdiction.strip().casefold() in seen_jurisdictions):
            raise ValueError("Credit needs a distinct other taxing jurisdiction")
        seen_jurisdictions.add(jurisdiction.strip().casefold())
        other_credit += min(_number(row.get("tax_paid"), "other tax paid"),
                            _number(row.get("wa_tax_on_same_gains"), "WA tax on same gains"))
    bo_credit = _number(b_o_tax_paid_on_same_gains, "B&O paid on same gains")
    if other_jurisdiction_credits or bo_credit:
        _confirmed(credits_verified, "same-asset credit worksheets and overlap reviewed")
    if bo_credit:
        _confirmed(b_o_eligibility_confirmed, "RCW 82.87.130 B&O eligibility")
    credit = min(before_credits, _number(other_credit + bo_credit, "combined verified credits"))
    tax = _round_half_up(before_credits - credit)
    paid = _number(payments, "confirmed WA payments")
    _confirmed(payments_confirmed, "payments_confirmed")
    if type(extension_requested) is not bool or type(payment_made) is not bool:
        raise ValueError("Confirm WA extension_requested and payment_made")
    if paid and not payment_made:
        raise ValueError("Positive payments contradict payment_made false")
    deduction = _round_half_up(deduction)
    business = _round_half_up(business)
    paid = _round_half_up(paid)
    return {
        "computed_tax_year": tax_year,
        "federal_net_long_term": _round_half_up(net_lt),
        "excluded_carryforward_addback": _round_half_up(cf_excluded),
        "removed_gain_loss": _round_half_up(removed),
        "adjusted_wa_gain": _round_half_up(adjusted),
        "standard_deduction": deduction,
        "business_deduction": business,
        "charitable_deduction": _round_half_up(char_deduction),
        "taxable_wa_gain": _round_half_up(taxable),
        "tax_before_credits": before_credits,
        "credits": _round_half_up(credit), "total_tax": tax,
        "payments": paid, "balance_due": _round_half_up(max(0.0, tax - paid)),
        "refund": _round_half_up(max(0.0, paid - tax)),
        "return_required": bool(tax or extension_requested or payment_made),
        "no_wage_income_tax_statement": "Washington has no individual wage income tax; its separate LT capital-gains excise is computed here.",
        "notes": [
            "TY2025 WA excise computed from confirmed classifications and eligibility worksheets; separate My DOR filing with federal return copy.",
            "No required quarterly estimates. April payment is not extended; request a WA filing extension separately through My DOR with a federal extension.",
            "Separate spouse/RDP allocations, special federal gains and midyear domicile changes are not modeled. Harvest excise pricing and WFTC application remain separate.",
        ],
    }
