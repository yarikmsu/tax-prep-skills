"""Reviewed full-year NY/NC forecasts and their annual payment requirements.

The interview contract is docs/contributing/state-planning.md. These inputs
are forecasts for the requested year, never copied return totals or YTD state
adjustments. Form engines own the math; this module owns forecast boundaries.
"""

import copy
import math

from agents.lib.projection.types import ProjectionError, StatePaymentSchedule, StateProjection
from agents.lib.tax_computations.state import planning
from agents.lib.tax_computations.state.ny import (
    _NY_ADDITION_LINES,
    _NY_SUBTRACTION_LINES,
    compute_mctmt,
    compute_ny_required_annual_payment,
    compute_ny_settlement,
)


def _object(value, name, allowed=None):
    if not isinstance(value, dict):
        raise ProjectionError(f"Record {name} as an object")
    if allowed is not None and set(value) - set(allowed):
        raise ProjectionError(f"Unrecognized fields in {name}; check the forecast contract")
    return value


def _number(value, name, *, signed=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (not signed and value < 0)):
        raise ProjectionError(f"{name} must be an explicit finite {'signed' if signed else 'nonnegative'} number")
    return round(float(value), 2)


def _year(value, expected, name):
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ProjectionError(f"{name} must match the covered year")


def _profile_flag(flags, key):
    value = flags.get(key)
    if value is not None and not isinstance(value, bool):
        raise ProjectionError(f"profile.flags.{key} must be true, false or unanswered")
    return value


def validate_forecast_profile(profile, year):
    """The shared profile must be complete before either tax forecast runs."""
    profile = _object(profile, "taxpayer_profile")
    _year(profile.get("tax_year"), year, "taxpayer_profile.tax_year")
    dependents = profile.get("dependents")
    if not isinstance(dependents, list):
        raise ProjectionError("Record taxpayer_profile.dependents explicitly, [] when none are claimed")
    if any(not isinstance(d, dict) or not isinstance(d.get("ctc_eligible"), bool) for d in dependents):
        raise ProjectionError("Record each federal dependent's covered-year ctc_eligible as true or false")


def _fraction(cfg, key):
    value = cfg.get(key)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 < value <= (2 if key == "prior_year_share_high_income" else 1)):
        raise ProjectionError(f"Fetch a valid state estimated-tax {key}")
    return float(value)


def _ny_forecast(plan, cfg, status, agi, proj):
    p = copy.deepcopy(_object(plan.get("ny"), "state_projection.ny", {
        "additions", "subtractions", "dependent_count", "claimed_as_dependent",
        "it196_fixed_base_confirmed", "itemized", "local", "mctmt_by_owner", "credit_worksheets",
        "star_credit_payment", "all_withholding_confirmed"}))
    if p.get("all_withholding_confirmed") is not True:
        raise ProjectionError("Confirm ALL NY State/City/Yonkers withholding before a combined forecast")
    _object(p.get("additions"), "NY additions", {name for name, _ in _NY_ADDITION_LINES} | {"it225"})
    _object(p.get("subtractions"), "NY subtractions",
            {name for name, _ in _NY_SUBTRACTION_LINES} | {"pension_annuity", "college_savings_contributions", "it225"})
    local = _object(p.get("local"), "NY local evidence", {
        "nyc_resident", "yonkers_resident", "yonkers_nonresident_earnings",
        "nyc_resident_spouses", "nyc_resident_full_year", "yonkers_resident_spouses",
        "yonkers_resident_full_year", "charitable_gifts_trust_deducted",
        "yonkers_worksheet_credits", "star_reconciliation", "mctmt"})
    for flag in ("nyc_resident", "yonkers_resident", "yonkers_nonresident_earnings"):
        if not isinstance(local.get(flag), bool):
            raise ProjectionError("Answer all NYC/Yonkers residency and earnings questions explicitly")
    if local["yonkers_nonresident_earnings"]:
        raise ProjectionError("Yonkers Y-203 earnings allocations require a separate projection")
    if local["nyc_resident"] and local["yonkers_resident"]:
        raise ProjectionError("NY IT-360.1/mixed local residency requires a separate projection")
    for flag, prefix in (("nyc_resident", "nyc"), ("yonkers_resident", "yonkers")):
        spouses = local.get(prefix + "_resident_spouses")
        if local[flag] and ((status == "mfj" and (type(spouses) is not int or spouses != 2))
                            or (status != "mfj" and local.get(prefix + "_resident_full_year") is not True)):
            raise ProjectionError("NY IT-360.1/mixed-spouse local residency requires a separate projection")
    # One input pair PER PERSON. Applying a cliff to joint earnings can invent
    # MCTMT when both people are individually below it.
    owners = _object(p.get("mctmt_by_owner"), "NY mctmt_by_owner")
    if "mctmt" in local and any(_object(local["mctmt"], "legacy MCTMT earnings").values()):
        raise ProjectionError("Remove combined local.mctmt earnings; use the per-owner forecast only")
    if set(owners) != ({"primary", "spouse"} if status == "mfj" else {"primary"}):
        raise ProjectionError("NY MCTMT requires explicit zone earnings for each filing spouse")
    mobility = 0.0
    earnings = 0.0
    for amounts in owners.values():
        amounts = _object(amounts, "MCTMT owner zones", {"zone_1_net_earnings", "zone_2_net_earnings"})
        for key in ("zone_1_net_earnings", "zone_2_net_earnings"):
            earnings += _number(amounts.get(key), "MCTMT zone earnings")
        mobility += compute_mctmt(**amounts, mctmt=cfg.get("mctmt"))["mctmt"]
    if round(earnings, 2) > round(proj.self_employment_net, 2):
        raise ProjectionError("MCTMT earnings exceed projected self-employment; reconcile partnership/zone inputs")
    credits = _object(p.get("credit_worksheets"), "NY credit_worksheets", {
        "resident_credit", "other_nonrefundable_credits", "net_other_state_taxes",
        "other_nyc_taxes", "nyc_nonrefundable_credits", "refundable"})
    for key in ("resident_credit", "other_nonrefundable_credits", "net_other_state_taxes",
                "other_nyc_taxes", "nyc_nonrefundable_credits"):
        _number(credits.get(key), "NY credit/tax worksheet amount")
    if credits["resident_credit"]:
        raise ProjectionError("NY resident credit allocations need a separate projection")
    if not local["nyc_resident"] and (credits["other_nyc_taxes"] or credits["nyc_nonrefundable_credits"]):
        raise ProjectionError("NYC credits/taxes contradict non-City residency")
    refundable = _object(credits.get("refundable"), "NY refundable credit worksheets")
    for value in refundable.values():
        _number(value, "NY refundable credit worksheet amount")
    # The settlement engine validates the closed set and incompatible tuition
    # elections. Every omitted key means none ONLY after scope review.
    settled = compute_ny_settlement(
        0, refundable_credits=refundable,
        college_tuition_deduction_claimed=bool(p.get("itemized", {}).get("college_tuition_deduction")))
    total_credits = settled["total_payments"]
    taxable_ss = _number(plan.get("taxable_social_security"), "projected taxable Social Security")
    # The federal projection carries taxable retirement/SS in other_income.
    pensions = _object(p.get("subtractions"), "NY subtractions")
    retirement = sum(_number(e.get("qualifying_amount"), "NY qualifying pension")
                     for e in pensions.get("pension_annuity", []))
    retirement += _number(pensions.get("government_pensions", 0), "NY government pension")
    refunds = _number(pensions.get("taxable_refunds", 0), "NY taxable refunds")
    if round(retirement + taxable_ss + refunds, 2) > proj.other_income:
        raise ProjectionError("NY pension/SS/refund exclusions exceed projected other_income; reconcile the forecast")
    if _number(pensions.get("us_government_bond_interest", 0), "NY US-government interest") > round(proj.taxable_interest + proj.ordinary_dividends, 2):
        raise ProjectionError("NY US-government interest exceeds projected interest/dividends")
    if "taxable_social_security" in pensions and pensions["taxable_social_security"] != taxable_ss:
        raise ProjectionError("NY Social Security worksheet differs from the forecast")
    out = planning._ny(agi, taxable_ss, 0, {"ny": p}, cfg, status,
                       forecast_credits=credits, forecast_mctmt=mobility)
    # Yonkers worksheet credits are reviewed at this forecast, rather than
    # reusing a fixed scenario worksheet. Existing engine validates each line.
    return out, total_credits, 1 + int(local["nyc_resident"]) + int(local["yonkers_resident"]), _number(p.get("star_credit_payment"), "projected STAR credit payment")


def _prior(plan, year, code):
    prior = _object(plan.get("prior_year"), "state_projection.prior_year",
                    {"tax_year", "method", "tax_base", "nyagi", "mctd_net_earnings",
                     "filed_12_month_return_confirmed", "no_liability_exception_confirmed"})
    _year(prior.get("tax_year"), year - 1, "state_projection.prior_year.tax_year")
    method = prior.get("method")
    if not isinstance(method, str) or method not in {"filed_return", "no_liability", "ineligible"}:
        raise ProjectionError("Resolve prior_year.method: filed_return, no_liability or ineligible; unknown refuses")
    if method == "ineligible":
        if prior.get("tax_base") is not None:
            raise ProjectionError("An ineligible prior-year prong cannot carry a tax base")
        return None, None, False
    base = _number(prior.get("tax_base"), "prior-year completed estimated-tax base")
    if method == "no_liability":
        if base != 0 or prior.get("no_liability_exception_confirmed") is not True:
            raise ProjectionError("Confirm the state's no-liability exception and a zero prior base")
        return 0.0, 0.0, True
    if prior.get("filed_12_month_return_confirmed") is not True:
        raise ProjectionError("The prior-year prong requires a filed twelve-month return")
    threshold_income = None
    if code == "NY":
        threshold_income = max(_number(prior.get("nyagi"), "prior NYAGI", signed=True),
                               _number(prior.get("mctd_net_earnings"), "prior MCTD net earnings", signed=True))
    return base, threshold_income, False


def project_resident_state(plan, proj, federal, inputs, profile, law, status, year):
    """Return (liability, annual requirement). Missing/unsupported evidence refuses.

    Calendar and timing notes are attached by build_report using its existing
    voucher helpers, keeping this module independent of the package entrypoint.
    """
    plan = _object(plan, "state_projection", {
        "tax_year", "state", "filing_status", "residency", "scope_confirmed",
        "federal_agi_at_review", "self_employment_net_at_review", "taxable_social_security",
        "farmer_fisherman", "calendar_year_confirmed", "use_tax", "prior_year", "ny", "nc"})
    code = plan.get("state")
    if not isinstance(code, str) or code not in {"NY", "NC"}:
        raise ProjectionError("Dedicated state_projection supports NY/NC only")
    cfg = _object(law.get("state"), "tax_law.state")
    _year(year, year, "requested year")
    _year(law.get("tax_year"), year, "tax-law year")
    _year(inputs.get("tax_year"), year, "projection_inputs.tax_year")
    _year(plan.get("tax_year"), year, "state_projection.tax_year")
    validate_forecast_profile(profile, year)
    if year not in planning._REVIEWED_YEARS[code]:
        raise ProjectionError("State forecast form year has not been reviewed; fetched rates alone are insufficient")
    if cfg.get("code") != code or cfg.get("type") != "graduated_income":
        raise ProjectionError("Declared state forecast jurisdiction and fetched law must agree")
    if plan.get("filing_status") != status or status not in {"SINGLE", "MFJ", "MFS", "HOH", "QSS"}:
        raise ProjectionError("State forecast filing_status must match the canonical profile status")
    status_key = status.lower()
    if (plan.get("residency") != "full_year" or plan.get("scope_confirmed") is not True
            or plan.get("calendar_year_confirmed") is not True):
        raise ProjectionError("Confirm full-year household residency, calendar year and the complete state forecast scope")
    flags = _object(profile.get("flags", {}), "profile.flags")
    if any(_profile_flag(flags, k) is True for k in ("moved_states_this_year", "statutory_resident_of_another_state",
                                                    "has_nonresident_source_income", "moved_abroad_this_year")):
        raise ProjectionError("Residency/source allocation obligations need separate state forecasts")
    if code == "NC" and any(_profile_flag(flags, k) is True for k in (
            "nc_has_business_adjustments", "nc_has_recovery_deductions", "nc_has_nol",
            "nc_has_taxed_pte", "nc_has_other_schedule_s_deductions", "nc_has_other_credits")):
        raise ProjectionError("Uncomputed NC Schedule S/credit provisions require a separate forecast")
    if code == "NY":
        local = _object(_object(plan.get("ny"), "NY forecast").get("local"), "NY local evidence")
        if (inputs.get("paystub_provenance") is not None
                and (local.get("nyc_resident") is True or local.get("yonkers_resident") is True)):
            raise ProjectionError("The paystub importer binds State withholding only; review combined NY local actuals, retain the import summary, record the manual resolution and clear provenance explicitly")
        for key in ("nyc_resident", "yonkers_resident", "yonkers_nonresident_earnings"):
            value = _profile_flag(flags, key)
            if value is not None and value != local.get(key):
                raise ProjectionError("NY local forecast contradicts household residency/earnings evidence")
        for prefix in ("nyc", "yonkers"):
            if local.get(prefix + "_resident") is not True:
                continue
            key = prefix + ("_resident_spouses" if status == "MFJ" else "_resident_full_year")
            value = flags.get(key)
            if value is not None:
                if status == "MFJ":
                    if type(value) is not int or value not in {0, 1, 2}:
                        raise ProjectionError("NY local resident spouse counts must be integers from zero to two")
                else:
                    _profile_flag(flags, key)
                if value != local.get(key):
                    raise ProjectionError("NY local forecast contradicts household residency/earnings evidence")
    if plan.get("farmer_fisherman") is not False:
        raise ProjectionError("Answer farmer_fisherman; qualifying households require the separate annual worksheet")
    if (_number(plan.get("federal_agi_at_review"), "reviewed federal AGI", signed=True) != federal.agi
            or _number(plan.get("self_employment_net_at_review"), "reviewed SE net") != proj.self_employment_net):
        raise ProjectionError("State worksheets were reviewed at a different income forecast; update the review")
    taxable_ss = _number(plan.get("taxable_social_security"), "projected taxable Social Security")
    if taxable_ss > proj.other_income:
        raise ProjectionError("Taxable Social Security must be included in projected other_income")
    if proj.state_modification_amounts is not None or proj.state_local_applicability is not None:
        raise ProjectionError("Use the dedicated NY/NC worksheets instead of generic state modification/local answers")
    ytd = _object(inputs.get("ytd"), "ytd")
    estimates = _object(inputs.get("estimated_payments_made"), "estimated_payments_made")
    _number(ytd.get("state_withholding"), "ytd.state_withholding (all applicable state/local withholding)")
    _number(estimates.get("state"), "estimated_payments_made.state")
    use_tax = _number(plan.get("use_tax"), "projected sales/use tax")
    prior_tax, prior_income, no_liability = _prior(plan, year, code)
    try:
        planning._finite_tree(plan)
        planning._finite_tree(cfg)
        if code == "NY":
            out, credits, count, star = _ny_forecast(plan, cfg, status_key, federal.agi, proj)
            harbor = _object(cfg.get("estimated_tax_safe_harbor"), "NY estimated-tax law")
            for key in ("current_year_share", "prior_year_share", "prior_year_share_high_income"):
                _fraction(harbor, key)
            # IT-2105.9 lines1-17 combine all four taxes, credits and withholding.
            # Per-tax IT-2105 voucher columns answer a different question.
            base = round(max(0.0, out["total_tax"] - credits - star), 2)
            required = compute_ny_required_annual_payment(base, prior_tax, prior_income, status_key, harbor)
            options = required["options"]
            threshold = _number(harbor.get("de_minimis"), "fetched NY de_minimis") * count
        else:
            raw = _object(plan.get(code.lower()), "NC raw forecast inputs", {
                "schedule_s", "ctc_qualifying_children", "itemized", "spouse_itemizes_nc",
                "claimed_as_dependent", "eligible_for_federal_standard_deduction", "other_state_credit"})
            if _object(raw.get("schedule_s"), "NC Schedule S").get("taxable_social_security") != taxable_ss:
                raise ProjectionError("NC Schedule S Social Security differs from the forecast")
            sched = raw["schedule_s"]
            if _number(sched.get("us_obligation_interest"), "NC US-obligation interest") > proj.taxable_interest:
                raise ProjectionError("NC US-obligation interest exceeds projected taxable_interest")
            if _number(sched.get("us_government_fund_dividends"), "NC government fund dividends") > proj.ordinary_dividends:
                raise ProjectionError("NC government fund dividends exceed projected ordinary_dividends")
            retirement = sched.get("government_retirement_taxable")
            other_parts = (taxable_ss + _number(sched.get("state_local_refund_in_agi"), "NC taxable refund")
                           + _number(sched.get("railroad_tier2_in_agi"), "NC railroad retirement")
                           + (0 if retirement is None else _number(retirement, "NC government retirement taxable")))
            if round(other_parts, 2) > proj.other_income:
                raise ProjectionError("NC SS/refund/retirement inputs exceed projected other_income")
            out = planning._nc(federal.agi, taxable_ss, plan, cfg, status_key, year,
                               forecast_use_tax=use_tax)
            credits = 0.0
            base = out["total_tax"]
            harbor = _object(cfg.get("estimated_tax"), "NC estimated-tax law")
            options = {"current_year": round(base * _fraction(harbor, "current_year_rate"), 2)}
            if prior_tax is not None:
                options["prior_year"] = round(prior_tax * _fraction(harbor, "prior_year_rate"), 2)
            threshold = _number(harbor.get("required_threshold"), "fetched NC required_threshold")
    except ProjectionError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        # Core worksheet errors sometimes include the rejected object/value.
        # Do not turn a misplaced taxpayer field into a terminal diagnostic.
        raise ProjectionError(f"{code} forecast worksheet or fetched law is incomplete/invalid; review the dedicated inputs") from None
    if threshold <= 0:
        raise ProjectionError("Fetch a positive state estimated-tax threshold")
    de_minimis = round(base - proj.state_withholding, 2) < threshold
    annual = 0.0 if de_minimis or no_liability else round(min(options.values()), 2)
    shortfall = round(max(0.0, annual - proj.state_withholding - proj.estimated_state_paid), 2)
    total = round(out["total_tax"] + use_tax, 2)
    state = StateProjection(code, out["taxable_income"], total, proj.state_withholding,
                            proj.estimated_state_paid,
                            round(total - credits - proj.state_withholding - proj.estimated_state_paid, 2),
                            out["agi"], out["deduction"], out["itemized"], True,
                            out["state_tax"], out["local_tax"], credits, use_tax)
    notes = ["Reviewed full-year form forecast; rework credits, modifications and local worksheets after income changes.",
             "Refundable credits reduce the estimated income-tax base; sales/use tax is included only in the balance.",
             "Prior-year tax base is the completed estimated-tax worksheet amount, not gross archived tax.",
             "Dates roll weekends only; confirm state legal holidays and disaster relief with the department.",
             "Regular cumulative equal quarters only. Annualized-income, farmer/fisherman and special extensions require separate worksheets."]
    notes.extend(out.get("notes", []))
    if code == "NY":
        notes.append("The primary annual target follows combined Form IT-2105.9 lines 1-17, not separate IT-2105 voucher-column minima. Allocate the payment on the official voucher; no component's liability was omitted.")
        notes.append("The reviewed STAR credit payment reduces the underpayment tax base, not the filing balance. The fetched floor multiplies by applicable State/City/Yonkers taxes; MCTMT adds zero to that count.")
    if de_minimis:
        notes.append("Income tax less projected withholding is below the fetched threshold; no additional estimates targeted.")
    if no_liability:
        notes.append("The prior-year no-liability exception was explicitly confirmed under this state's conditions.")
    if prior_tax is None:
        notes.append("Prior-year prong is confirmed ineligible; only the current-year share applies.")
    return state, StatePaymentSchedule(code, "IT-2105" if code == "NY" else "NC-40",
                  base, options, annual, state.withholding, state.estimates_paid,
                  shortfall, de_minimis, no_liability, [], notes)
