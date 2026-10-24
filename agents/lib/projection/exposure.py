"""Reviewed AMT inputs and Medicare exposure for the mid-year projection.

These are full-year worksheets, never annualized YTD or archived preferences.
The return and the law cache remain read-only.
"""

import math
from dataclasses import replace

from agents.lib.projection.types import FederalProjection, ProjectedInputs, ProjectionError
from agents.lib.tax_computations.federal.amt import compute_form_6251, compute_form_8801_credit


AMT_NOT_MODELED = (
    "AMT NOT MODELED: complete amt_projection with reviewed full-year Form 6251 "
    "preferences and credits. Federal tax, balance, current-year safe harbor and "
    "payment recommendations exclude AMT and may be too low. This is not a zero-AMT finding."
)

_AMT_MONEY = {
    "iso_bargain_element", "private_activity_bond_interest", "salt_deducted",
    "senior_deduction", "section_68_reduction", "regular_foreign_tax_credit",
    "amt_foreign_tax_credit", "prior_minimum_tax_credit",
}
_AMT_KEYS = _AMT_MONEY | {
    "tax_year", "filing_status", "full_year_inputs_confirmed",
    "federal_agi_at_review", "unsupported_items",
}


def _number(value, where: str, *, signed=False) -> float:
    try:
        number = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else math.nan
    except OverflowError:
        number = math.nan
    if not math.isfinite(number) or (not signed and number < 0):
        raise ProjectionError(f"{where} needs an explicit finite {'signed ' if signed else 'nonnegative '}number, including zero")
    return number


def _year(value, expected: int, where: str) -> None:
    if type(value) is not int or value != expected:
        raise ProjectionError(f"{where} must name the covered year")


def _unmodeled_credit_screen(profile: dict) -> None:
    """Omitting a known credit can turn the nominally low fallback high."""
    prior = profile.get("prior_year")
    flags = profile.get("flags")
    prior = {} if prior is None else prior
    flags = {} if flags is None else flags
    if not isinstance(prior, dict) or not isinstance(flags, dict):
        raise ProjectionError("Review the profile's prior-year credit and foreign-tax records")
    foreign = flags.get("has_foreign_tax_paid", False)
    if type(foreign) is not bool:
        raise ProjectionError("Confirm has_foreign_tax_paid before using an AMT-excluding estimate")
    opening = _number(prior.get("amt_credit_carryforward", 0), "profile prior AMT credit")
    if opening > 0 or foreign is True:
        raise ProjectionError(
            "Complete amt_projection with the reviewed opening minimum-tax credit and "
            "FTC worksheets before projecting payments; omitting a known credit could "
            "overstate the estimate, so the AMT-excluding fallback is unavailable.")


def apply_projected_amt(federal: FederalProjection, proj: ProjectedInputs,
                        profile: dict, tax_law: dict, fs: str, year: int | None,
                        net_ltcg: float, cg_zero, cg_fifteen,
                        schedule_a: dict | None) -> FederalProjection:
    raw = proj.amt_projection
    if raw is None:
        _unmodeled_credit_screen(profile)
        return replace(federal, amt_note=AMT_NOT_MODELED)
    if not isinstance(raw, dict) or set(raw) - _AMT_KEYS:
        raise ProjectionError("amt_projection must be the documented Form 6251 worksheet object; unknown fields are refused")
    if type(year) is not int:
        raise ProjectionError("AMT requires covered-year fetched federal.amt law")
    _year(raw.get("tax_year"), year, "amt_projection.tax_year")
    _year(profile.get("tax_year"), year, "taxpayer profile tax_year")
    if raw.get("filing_status") != fs or raw.get("full_year_inputs_confirmed") is not True:
        raise ProjectionError("Confirm the AMT worksheet's filing status and full-year inputs")
    unsupported = raw.get("unsupported_items")
    if not isinstance(unsupported, list) or unsupported:
        raise ProjectionError(
            "Form 6251 unsupported preference items require a separate reviewed model: "
            "refunds of deducted taxes, investment-interest/depletion/NOL adjustments, "
            "QSBS, AMT basis or capital-gain differences, depreciation/passive losses, "
            "business preferences, foreign earned income, kiddie tax, Schedule J, "
            "Form 4972/8978 or other adjustments. Confirm an empty unsupported_items list only when none applies.")
    signed_agi = round(federal.gross_income - federal.adjustments, 2)
    reviewed_agi = _number(raw.get("federal_agi_at_review"), "AMT federal_agi_at_review", signed=True)
    if round(reviewed_agi, 2) != signed_agi:
        raise ProjectionError("Rework AMT deduction and credit worksheets at the projected federal AGI")
    amounts = {key: _number(raw.get(key), f"amt_projection.{key}") for key in _AMT_MONEY}
    salt, haircut = amounts["salt_deducted"], amounts["section_68_reduction"]
    if not federal.itemized and (salt or haircut):
        raise ProjectionError("A standard-deduction AMT projection must record zero SALT and section 68 reduction")
    if year < 2026 and haircut:
        raise ProjectionError("The section 68 reduction is not applied before TY2026")
    if salt > federal.deduction + haircut:
        raise ProjectionError("AMT SALT cannot exceed the reviewed pre-section-68 itemized total")
    if schedule_a is not None and federal.itemized:
        if (round(salt, 2) != schedule_a["salt_deductible"]
                or round(haircut, 2) != schedule_a["obbba_70111_reduction"]):
            raise ProjectionError("AMT SALT and section 68 reduction must reconcile to the repriced Schedule A")
    senior = amounts["senior_deduction"]
    if senior > federal.schedule_1a_deduction:
        raise ProjectionError("AMT senior addback cannot exceed the applied Schedule 1-A deduction")
    ftc = amounts["regular_foreign_tax_credit"]
    if ftc > federal.tax_before_credits:
        raise ProjectionError("Reviewed regular foreign tax credit cannot exceed regular income tax")
    prior = profile.get("prior_year") or {}
    if not isinstance(prior, dict):
        raise ProjectionError("The profile prior_year credit record must be an object")
    if "amt_credit_carryforward" in prior:
        opening = _number(prior["amt_credit_carryforward"], "profile prior AMT credit")
        if round(opening, 2) != round(amounts["prior_minimum_tax_credit"], 2):
            raise ProjectionError("Resolve the prior minimum-tax credit with the profile carryforward")
    fed = tax_law.get("federal")
    cfg = fed.get("amt") if isinstance(fed, dict) else None
    if not isinstance(cfg, dict):
        raise ProjectionError("Fetch covered-year federal.amt exemptions, phase-outs and breakpoint")
    key = "mfj" if fs in {"MFJ", "QSS"} else "mfs" if fs == "MFS" else "single"
    indexed = {}
    for name in ("exemption", "phaseout_start"):
        block = cfg.get(name)
        value = _number(block.get(key) if isinstance(block, dict) else None, f"federal.amt.{name}.{key}")
        if value <= 0:
            raise ProjectionError(f"Fetch a positive covered-year federal.amt.{name}.{key}")
        indexed[name] = value
    breakpoint = _number(cfg.get("rate_28_breakpoint"), "federal.amt.rate_28_breakpoint")
    if breakpoint <= 0:
        raise ProjectionError("Fetch a positive covered-year AMT breakpoint")
    # Form 6251 refigures line 1 below zero when deductions exceed income.
    # Section 68 does not apply for AMT (§56(b)(1)(E)); reverse the regular
    # haircut before the engine adds the full deducted SALT back.
    line1 = round(signed_agi - federal.deduction - federal.schedule_1a_deduction
                  - federal.charitable_non_itemizer_deduction - federal.qbi_deduction, 2)
    try:
        detail = compute_form_6251(
            taxable_income=line1, filing_status=fs, tax_year=year,
            regular_tax=round(federal.tax_before_credits - ftc, 2),
            **indexed, rate_28_breakpoint=breakpoint,
            salt_deducted=salt,
            standard_deduction_taken=0.0 if federal.itemized else federal.deduction,
            iso_bargain_element=amounts["iso_bargain_element"],
            private_activity_bond_interest=amounts["private_activity_bond_interest"],
            other_adjustments=-haircut, senior_deduction=senior,
            qualified_dividends=proj.qualified_dividends, net_ltcg=net_ltcg,
            zero_pct_limit=cg_zero, fifteen_pct_limit=cg_fifteen,
            amt_ftc=amounts["amt_foreign_tax_credit"],
            exemption_phaseout_rate=cfg.get("phaseout_rate"))
        credit = compute_form_8801_credit(
            amounts["prior_minimum_tax_credit"], federal.tax_before_credits,
            detail["tmt"], nonrefundable_credits=ftc + federal.ctc)
    except ValueError:
        raise ProjectionError("Cannot model AMT from the reviewed worksheet and covered-year federal.amt inputs") from None
    # CTC may offset both regular tax and AMT; flooring regular tax BEFORE
    # adding AMT would discard the part of that credit available against AMT.
    total = round(max(0.0, federal.tax_before_credits + detail["amt"] - ftc
                      - federal.ctc - credit["credit_allowed"])
                  + federal.se_tax + federal.additional_medicare + federal.niit, 2)
    detail = {**detail, "line_1_refigure": line1, "section_68_reversed": round(haircut, 2),
              "private_activity_bond_interest": round(amounts["private_activity_bond_interest"], 2),
              "credit_8801": credit, "scope": "reviewed full-year ISO/PAB, deduction and credit worksheets"}
    detail["notes"] = ["Projected AMT and credit carryforward are planning estimates; do not write them into the filed-return or rollover records."]
    return replace(federal, amt=detail["amt"], amt_details=detail, amt_note=None,
                   foreign_tax_credit=round(ftc, 2), minimum_tax_credit=credit["credit_allowed"],
                   total_tax=total,
                   balance=round(total - federal.withholding - federal.estimates_paid, 2))


def project_irmaa(raw, federal: FederalProjection, fs: str, year: int,
                  premium_law: dict | None) -> dict:
    """Premium year is the income year plus the statutory two-year lookback.

    Read that year's independently fetched cache. An income-year table is
    never substituted for unpublished future premiums, even as a zero cost.
    """
    from agents.lib.tax_computations.federal.irmaa import irmaa_surcharge, irmaa_status_key, read_irmaa_tiers

    premium_year = year + 2
    out: dict = {"income_year": year, "premium_year": premium_year, "lookback_years": 2,
           "modeled": False, "projected_magi": None, "beneficiaries": None,
           "annual_surcharge_per_beneficiary": None, "annual_household_surcharge": None,
           "note": "IRMAA not modeled: supply reviewed irmaa_projection inputs; Medicare premiums are separate from income tax."}
    if raw is None:
        return out
    keys = {"tax_year", "filing_status", "full_year_inputs_confirmed", "federal_agi_at_review",
            "tax_exempt_interest", "excluded_income_addbacks", "beneficiaries",
            "lived_apart_all_year", "full_year_parts_b_and_d_confirmed"}
    if not isinstance(raw, dict) or set(raw) - keys:
        raise ProjectionError("irmaa_projection must be the documented MAGI and beneficiary worksheet object")
    _year(raw.get("tax_year"), year, "irmaa_projection.tax_year")
    if raw.get("filing_status") != fs or raw.get("full_year_inputs_confirmed") is not True:
        raise ProjectionError("Confirm the IRMAA filing status and full-year MAGI inputs")
    reviewed_agi = _number(raw.get("federal_agi_at_review"), "IRMAA federal_agi_at_review", signed=True)
    agi = round(federal.gross_income - federal.adjustments, 2)
    if round(reviewed_agi, 2) != agi:
        raise ProjectionError("Review IRMAA MAGI at the projected federal AGI")
    exempt = _number(raw.get("tax_exempt_interest"), "IRMAA tax_exempt_interest")
    exclusions = _number(raw.get("excluded_income_addbacks"), "IRMAA excluded_income_addbacks")
    if federal.amt_details is not None:
        pab = _number(federal.amt_details.get("private_activity_bond_interest"), "known AMT private-activity-bond interest")
        if exempt < pab:
            raise ProjectionError("IRMAA tax-exempt interest must include the known private-activity-bond interest")
    beneficiaries = raw.get("beneficiaries")
    if type(beneficiaries) is not int or not 0 <= beneficiaries <= (2 if fs == "MFJ" else 1):
        raise ProjectionError("IRMAA beneficiaries must be an explicit whole count for this return")
    magi = _number(round(agi + exempt + exclusions, 2), "projected IRMAA MAGI", signed=True)
    out.update(projected_magi=magi, beneficiaries=beneficiaries)
    if beneficiaries == 0:
        return {**out, "note": "No Medicare beneficiary declared for the premium year; no premium estimate requested."}
    if raw.get("full_year_parts_b_and_d_confirmed") is not True:
        raise ProjectionError("IRMAA combined surcharges require full-year Parts B and D coverage; partial-year or one-part premiums need a separate worksheet")
    lived_apart = raw.get("lived_apart_all_year")
    if fs != "MFS" and lived_apart is not None:
        raise ProjectionError("IRMAA lived_apart_all_year must be null outside MFS")
    try:
        key = irmaa_status_key(fs, lived_apart)
    except ValueError as exc:
        raise ProjectionError(str(exc)) from None
    if premium_law is None:
        return {**out, "note": f"IRMAA not modeled: TY{year} MAGI generally affects {premium_year} premiums. That premium year's law is unavailable; no current-year table is reused as a verified future figure."}
    if not isinstance(premium_law, dict):
        raise ProjectionError("IRMAA premium law must be the separately fetched cache object")
    _year(premium_law.get("tax_year"), premium_year, "IRMAA premium law tax_year")
    fed = premium_law.get("federal")
    block = fed.get("irmaa") if isinstance(fed, dict) else None
    if not isinstance(block, dict) or type(block.get("lookback_years")) is not int or block["lookback_years"] != 2:
        raise ProjectionError("Confirm the premium-year IRMAA cache and two-year lookback")
    try:
        tiers = read_irmaa_tiers(premium_law, fs, lived_apart=lived_apart)
        if not tiers:
            return {**out, "note": f"IRMAA not modeled: fetch the complete {premium_year} Medicare table for this filing status; an empty table is not zero surcharge."}
        annual = irmaa_surcharge(out["projected_magi"], tiers, separate=key == "mfs", premium_year=premium_year)
    except ValueError as exc:
        raise ProjectionError(str(exc)) from None
    return {**out, "modeled": True, "annual_surcharge_per_beneficiary": annual,
            "annual_household_surcharge": _number(round(annual * beneficiaries, 2), "annual household IRMAA"),
            "note": "Full-year Part B plus Part D surcharge above base/plan premiums, per beneficiary. Separate from income tax; SSA may use another income year or grant a life-event redetermination."}
