#!/usr/bin/env python3
"""Explicit multi-year Roth forecasts; never a return or a future-law cache.

The filing-year point reuses the existing anchored ladder. Subsequent years
price explicit assumptions through its federal keystone. IRA basis remains
with one owner; Medicare costs use the premium year, two years later.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import math
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib import roth_ladder  # noqa: E402
from agents.lib.projection import _canonical_status, _full_status_key, _validate_tax_law_year  # noqa: E402
from agents.lib.scenario import (  # noqa: E402
    _rate_kwargs, extract_baseline_inputs, read_tax_year, return_deduction, save_scenario,
)
from agents.lib.secure_fs import first_non_finite, secure_path, write_secure  # noqa: E402
from agents.lib.tax_computations import (  # noqa: E402
    SCENARIO_INPUT_KEYS, compute_form_8606, compute_niit, compute_scenario,
    compute_se_tax, compute_taxable_ss,
)
from agents.lib.tax_computations.federal.irmaa import (  # noqa: E402
    irmaa_status_key, irmaa_surcharge, read_irmaa_tiers,
)

SCHEMA = "roth-plan/v1"
STATUSES = {"Single", "MFJ", "MFS", "HOH", "QSS"}
MAX_YEARS = 10
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_ANNUAL = {"year", "filing_status", "wages", "other_ordinary_income", "self_employment_net",
    "qualified_dividends", "capital_gains", "social_security", "social_security_inputs",
    "adjustments", "deduction", "additional_standard_deduction", "state", "ctc_children",
    "net_investment_income", "niit_magi_addbacks", "irmaa_magi_addbacks",
    "medicare_enrollees_two_years_later", "medicare_months_two_years_later",
    "medicare_parts_b_and_d_confirmed", "medicare_lived_apart_all_year", "law", "premium_law",
    "rmd_not_required_confirmed", "other_federal_effects_absent_confirmed", "state_inputs", "spouse_itemizes"}
_IRA = {"owner", "traditional_balance", "roth_balance", "basis", "growth_rate", "basis_source",
        "basis_confirmed", "regular_worksheet_confirmed", "no_other_ira_flows_confirmed"}
LIMITATIONS = [
    "Forecasts are planning estimates; only the filing-year point is anchored to the computed return.",
    "Future federal tax uses the simplified scenario model. Other credits, AMT and special deductions require separate reviewed modeling.",
    "No RMDs, inherited IRAs, new IRA contributions, other IRA distributions, withholding from the IRA, multiple owners or Roth distribution five-year rules.",
    "Growth is an explicit forecast assumption applied after conversion; Form 8606 line 6 is the grown Dec 31 value, which starts the next year. Taxes are paid from outside the IRA. No investment tax or spending cash-flow model.",
    "Refundable credits, including the additional child tax credit, are not repriced.",
    "Annual net gains/losses must include separately reviewed carryforwards. Only IRA basis and balances carry automatically between plan years.",
    "Medicare costs cover the entered enrollment months for Parts B and D before any appeal or other relief. Premium costs are separate from income tax.",
]


class PlanError(ValueError):
    """Missing, ambiguous or unsupported planning assumptions."""


def _obj(raw, keys: set, where: str) -> dict:
    if not isinstance(raw, dict) or set(raw) != keys:
        raise PlanError(f"{where} must contain exactly its documented fields")
    return raw


def _number(value, where: str, *, signed=False) -> float:
    try:
        ok = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        ok = False
    if not ok or (not signed and value < 0):
        raise PlanError(f"{where} must be finite and numeric" + ("" if signed else " and nonnegative"))
    return float(value)


def _money(value, where: str, *, signed=False) -> float:
    number = _number(value, where, signed=signed)
    if round(number, 2) != number:
        raise PlanError(f"{where} must be expressed in whole cents")
    return number


def _integer(value, low: int, high: int, where: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise PlanError(f"{where} is outside its documented integer range")
    return value


def _law_ref(raw, target: int) -> tuple[int, float | None, str]:
    if raw == {"mode": "fetched"}:
        return target, None, f"fetched TY{target}"
    _obj(raw, {"mode", "basis"}, "hypothetical law")
    if raw["mode"] != "hypothetical" or not isinstance(raw["basis"], str):
        raise PlanError("Law must be fetched or an explicitly named hypothetical")
    held = re.fullmatch(r"held_at_(20\d{2})", raw["basis"])
    indexed = re.fullmatch(r"indexed_from_(20\d{2})_at_(-?(?:\d+(?:\.\d+)?))", raw["basis"])
    match = held or indexed
    if match is None:
        raise PlanError("Hypothetical law needs held_at_YEAR or indexed_from_YEAR_at_RATE")
    base = int(match[1])
    if base > target:
        raise PlanError("A hypothetical law cannot use a later-year basis")
    rate = 0.0 if held else _number(float(match[2]), "law indexing rate", signed=True)
    if not -1 < rate <= 1:
        raise PlanError("Law indexing rate must be greater than -1 and no greater than 1")
    return base, rate, f"hypothetical {raw['basis']} for TY{target}"


def validate_plan(raw: dict, return_year: int) -> dict:
    """Every amount, zero and personal fact is explicit; no template defaults."""
    _integer(return_year, 2000, 2099, "computed return year")
    _obj(raw, {"schema", "name", "start_year", "ira", "years", "assumptions_reviewed"}, "plan")
    if raw["schema"] != SCHEMA or raw["assumptions_reviewed"] is not True:
        raise PlanError("Use the supported plan schema and review all assumptions")
    if not isinstance(raw["name"], str) or not _NAME.fullmatch(raw["name"]):
        raise PlanError("Plan name must be a short letters/digits/underscore/hyphen segment starting with a letter")
    if type(raw["start_year"]) is not int or raw["start_year"] not in {return_year, return_year + 1}:
        raise PlanError("Plan must start in the computed return year or the following year")
    ira = _obj(raw["ira"], _IRA, "IRA assumptions")
    if not isinstance(ira["owner"], str) or not isinstance(ira["basis_source"], str) or ira["owner"] not in {"primary", "spouse"} or ira["basis_source"] not in {
            "opening_profile", "closing_form_8606", "reviewed_worksheet"}:
        raise PlanError("IRA needs one confirmed owner and a named basis source")
    for key in ("basis_confirmed", "regular_worksheet_confirmed", "no_other_ira_flows_confirmed"):
        if ira[key] is not True:
            raise PlanError("Confirm regular individual IRA scope, basis and absence of other IRA flows")
    for key in ("traditional_balance", "roth_balance", "basis"):
        _money(ira[key], key)
    growth = _number(ira["growth_rate"], "growth rate", signed=True)
    if not -1 <= growth <= 1:
        raise PlanError("Growth rate must be a fraction between -1 and 1")
    years = raw["years"]
    if not isinstance(years, list) or not 1 <= len(years) <= MAX_YEARS:
        raise PlanError("Plan needs one through ten consecutive years")
    for index, row in enumerate(years):
        if not isinstance(row, dict) or (set(row) - _ANNUAL) not in ({"conversion"}, {"fill_to"}):
            raise PlanError("Each year needs exactly one conversion amount or fill_to target")
        _obj(row, _ANNUAL | ({"conversion"} if "conversion" in row else {"fill_to"}), "annual assumptions")
        if type(row["year"]) is not int or row["year"] != raw["start_year"] + index:
            raise PlanError("Plan years must be explicit, consecutive and ordered")
        _integer(row["year"], 2000, 2099, "plan year")
        if (not isinstance(row["filing_status"], str) or row["filing_status"] not in STATUSES
                or (row["state"] is not None and row["state"] != "CA")):
            raise PlanError("Annual filing status and state must be canonical (CA or null)")
        if ira["owner"] == "spouse" and row["filing_status"] != "MFJ":
            raise PlanError("Plan the spouse's separate return in their own workspace")
        for key in ("wages", "qualified_dividends", "social_security", "adjustments",
                    "additional_standard_deduction", "net_investment_income", "niit_magi_addbacks", "irmaa_magi_addbacks"):
            _money(row[key], key)
        for key in ("other_ordinary_income", "capital_gains", "self_employment_net"):
            _money(row[key], key, signed=True)
        # The filing year copies the return, whose Schedule 1 may carry a loss.
        # A forecast loss belongs where its limits apply: a capital loss in
        # capital_gains (§1211), an ordinary one in adjustments.
        if row["year"] != return_year and row["other_ordinary_income"] < 0:
            raise PlanError("Forecast other ordinary income cannot be negative; enter an ordinary loss in "
                            "adjustments and a capital loss in capital_gains")
        if row["deduction"] != "standard":
            _money(row["deduction"], "annual deduction")
            if row["additional_standard_deduction"]:
                raise PlanError("An explicit deduction already includes any additional standard amount")
        if row["filing_status"] == "MFS":
            if type(row["spouse_itemizes"]) is not bool:
                raise PlanError("MFS requires the spouse's itemizing election")
            if row["spouse_itemizes"] and row["deduction"] == "standard":
                raise PlanError("MFS must itemize when the spouse itemizes")
        elif row["spouse_itemizes"] is not None:
            raise PlanError("Non-MFS spouse itemizing election must be null")
        if row["state_inputs"] is not None and not isinstance(row["state_inputs"], dict):
            raise PlanError("State inputs must be a reviewed object or explicit null")
        if row["state"] is None and row["state_inputs"] is not None:
            raise PlanError("A federal-only year cannot contain California inputs")
        if first_non_finite(row["state_inputs"]) is not None:
            raise PlanError("State forecast inputs must be finite")
        _integer(row["ctc_children"], 0, 100, "CTC child count")
        if row["rmd_not_required_confirmed"] is not True or row["other_federal_effects_absent_confirmed"] is not True:
            raise PlanError("Stop before RMDs; separately model other federal credits/taxes/special deductions")
        for key in ("medicare_lived_apart_all_year",):
            if row["filing_status"] == "MFS":
                if type(row[key]) is not bool:
                    raise PlanError("MFS requires an explicit lived-apart-all-year answer")
            elif row[key] is not None:
                raise PlanError("Non-MFS lived-apart answer must be null")
        enrollees = _integer(row["medicare_enrollees_two_years_later"], 0, 2, "Medicare enrollee count")
        if enrollees > 1 and row["filing_status"] != "MFJ":
            raise PlanError("Two beneficiaries require a joint lookback return; model separate returns in separate plans")
        months = row["medicare_months_two_years_later"]
        if not isinstance(months, list) or len(months) != enrollees:
            raise PlanError("Record enrollment months for each Medicare beneficiary")
        for month in months:
            _integer(month, 1, 12, "Medicare enrollment months")
        if (type(row["medicare_parts_b_and_d_confirmed"]) is not bool
                or (enrollees and row["medicare_parts_b_and_d_confirmed"] is not True)):
            raise PlanError("The combined surcharge needs confirmed Part B and D enrollment")
        ss = row["social_security_inputs"]
        if row["social_security"]:
            _obj(ss, {"tax_exempt_interest", "excluded_income_addbacks", "regular_worksheet_confirmed"}, "Social Security inputs")
            if ss["regular_worksheet_confirmed"] is not True:
                raise PlanError("Confirm the regular Social Security worksheet; lump-sum/repayment methods are outside scope")
            for key in ("tax_exempt_interest", "excluded_income_addbacks"):
                _money(ss[key], key)
            # IRMAA MAGI adds tax-exempt interest back (42 USC 1395r(i)(4)(A)).
            if row["irmaa_magi_addbacks"] < ss["tax_exempt_interest"]:
                raise PlanError("IRMAA MAGI addbacks must include the year's tax-exempt interest")
        elif ss is not None:
            raise PlanError("Zero benefits require null Social Security inputs")
        possible_nii = max(row["other_ordinary_income"], 0) + row["qualified_dividends"] + max(row["capital_gains"], 0)
        if row["net_investment_income"] > possible_nii:
            raise PlanError("Net investment income cannot exceed the entered non-wage investment-income candidates")
        _law_ref(row["law"], row["year"])
        if row["year"] == return_year and row["law"] != {"mode": "fetched"}:
            raise PlanError("The anchored filing year requires its fetched covered-year law")
        if row["premium_law"] is not None:
            _law_ref(row["premium_law"], row["year"] + 2)
        if "conversion" in row:
            _money(row["conversion"], "conversion")
        else:
            target = row["fill_to"]
            if not isinstance(target, dict):
                raise PlanError("fill_to needs a bracket rate or IRMAA tier")
            if target.get("kind") == "bracket":
                _obj(target, {"kind", "rate"}, "bracket target")
                if not 0 <= _number(target["rate"], "bracket rate") <= 1:
                    raise PlanError("Bracket rate must be a fraction")
            elif target.get("kind") == "irmaa":
                _obj(target, {"kind", "tier"}, "IRMAA target")
                _integer(target["tier"], 1, 5, "IRMAA target tier")
                if row["premium_law"] is None:
                    raise PlanError("An IRMAA target requires explicit premium-year law")
            else:
                raise PlanError("Unknown fill_to target")
    return copy.deepcopy(raw)


def _scale(value, factor: float) -> float:
    number = _number(value, "indexed law amount") * factor
    if not math.isfinite(number):
        raise PlanError("Indexed law overflowed")
    return round(number, 2)


def _thousand(amount: float) -> float:
    """42 USC 1395r(i)(5)(B): the nearest multiple of $1,000."""
    return float((Decimal(str(amount)) / 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 1000)


def _indexed_irmaa(tiers: dict, base: int, target: int, rate: float) -> dict:
    """The statute's tables, indexed from the base year's unmarried table.

    42 USC 1395r(i)(5) indexes the unmarried amounts, rounding each to $1,000,
    and leaves the $500,000 top alone until 2028, then indexes it from August
    2026 ((i)(5)(C)). Joint amounts are twice the unmarried ones and the top
    150% ((i)(3)(C)(ii)). Separate filers' are reduced by the unmarried
    threshold ((i)(3)(C)(iii)), leaving that threshold and the top less it.
    Scaling every table on its own drifts from all three.
    """
    single = sorted(tiers.get("single") or [], key=lambda tier: tier["magi_threshold"])
    if len(single) != 5:
        raise PlanError("An indexed hypothetical IRMAA table needs the base year's five-tier unmarried table")
    factor = (1 + rate) ** (target - base)
    lower = [_thousand(_scale(tier["magi_threshold"], factor)) for tier in single[:4]]
    top = _number(single[4]["magi_threshold"], "IRMAA top threshold")
    if target >= 2028:
        # Increased by the CPI rise "(if any)": never below the 2027 amount.
        indexed = _thousand(_scale(top, (1 + rate) ** (target - max(base, 2027))))
        top = max(top, indexed) if base <= 2027 else indexed
    bounds = {"single": [*lower, top], "mfj": [*(2 * amount for amount in lower), 1.5 * top],
              "mfs": [lower[0], top - lower[0]]}
    out = {}
    for status, rows in tiers.items():
        if not rows:
            out[status] = rows
            continue
        rows = sorted(rows, key=lambda tier: tier["magi_threshold"])
        if status not in bounds or len(rows) != len(bounds[status]):
            raise PlanError("An indexed hypothetical IRMAA table needs the statutory status tables")
        out[status] = [{"magi_threshold": amount, "annual_surcharge_per_person": _scale(
            row["annual_surcharge_per_person"], factor)} for amount, row in zip(bounds[status], rows)]
    return out


def resolve_law(ref: dict, target: int, laws: dict[int, dict]) -> tuple[dict, str]:
    """In-memory hypothetical only; rates and unindexed statutory blocks stay held."""
    base, rate, label = _law_ref(ref, target)
    if base not in laws:
        raise PlanError("The explicitly selected source law is missing; no implicit hypothetical fallback")
    original = laws[base]
    _validate_tax_law_year(original, base)
    if first_non_finite(original) is not None:
        raise PlanError("Source law contains a non-finite number")
    law = copy.deepcopy(original)
    if rate is None:
        return law, label
    if rate == 0:
        law["tax_year"] = target
        law["_meta"] = {"tax_year": target, "source": label, "hypothetical": True}
        return law, label
    factor = (1 + rate) ** (target - base)
    fed = law.get("federal", {})
    # Explicit forecast policy: index these monetary inputs only. No walk over
    # every number: that would change tax rates, NIIT/Medicare statutory gates,
    # CTC phaseouts and the statutory SALT schedule.
    for status in fed.get("filing_statuses", {}).values():
        status["standard_deduction"] = _scale(status["standard_deduction"], factor)
        for bracket in status["brackets"]:
            for key in ("min", "max"):
                if bracket[key] is not None:
                    bracket[key] = _scale(bracket[key], factor)
    for key in ("zero_pct_limit", "fifteen_pct_limit"):
        limits = fed.get("capital_gains_rates", {}).get(key, {})
        for status, amount in limits.items():
            if amount is not None:
                limits[status] = _scale(amount, factor)
    if fed.get("social_security", {}).get("wage_base") is not None:
        fed["social_security"]["wage_base"] = _scale(fed["social_security"]["wage_base"], factor)
    tiers = fed.get("irmaa", {}).get("tiers", {})
    if any(tiers.values()):
        fed["irmaa"]["tiers"] = _indexed_irmaa(tiers, base, target, rate)
    law["tax_year"] = target
    law["_meta"] = {"tax_year": target, "source": label, "hypothetical": True}
    return law, label


def _year_inputs(row: dict, law: dict) -> tuple[dict, dict, dict]:
    """Construct the existing keystone's regular SS evidence from annual facts."""
    status = row["filing_status"]
    key = _full_status_key(status)
    deduction = (_number(law["federal"]["filing_statuses"][key]["standard_deduction"], "fetched standard deduction") + row["additional_standard_deduction"]
                 if row["deduction"] == "standard" else row["deduction"])
    # _rate_kwargs's return contract rejects a zero placeholder. A forecast's
    # explicit zero is legitimate; use a positive carrier then replace it.
    carrier = {"form_1040": {"line_12_standard_deduction": deduction or 1}}
    profile = {"filing_status": status, "dependents": [{"ctc_eligible": True}] * row["ctc_children"]}
    kwargs = _rate_kwargs(profile, law, status, carrier)
    kwargs["deduction"] = deduction
    previous = 0.0
    for index, bracket in enumerate(kwargs["brackets"]):
        if not isinstance(bracket, dict) or set(bracket) != {"min", "max", "rate"}:
            raise PlanError("Federal brackets need explicit min, max and rate")
        if _number(bracket["min"], "bracket minimum") != previous:
            raise PlanError("Federal brackets must be contiguous from zero")
        if not 0 <= _number(bracket["rate"], "bracket rate") <= 1:
            raise PlanError("Federal bracket rate must be a fraction")
        if index == len(kwargs["brackets"]) - 1:
            if bracket["max"] is not None:
                raise PlanError("Federal brackets require an open top bracket")
        else:
            upper = _number(bracket["max"], "bracket maximum")
            if upper <= previous:
                raise PlanError("Federal brackets must ascend")
            previous = upper
    for key in ("ctc_per_child", "ctc_phase_out_start", "addl_medicare_threshold", "ss_wage_base"):
        if kwargs[key] is not None:
            _number(kwargs[key], key)
    for key in ("qbi_rate", "addl_medicare_rate"):
        if not 0 <= _number(kwargs[key], key) <= 1:
            raise PlanError("Federal rate must be a fraction")
    if row["self_employment_net"] > 0 and law.get("federal", {}).get("qbi_deduction_rate") is None:
        raise PlanError("Self-employment planning needs a fetched QBI rate")
    inputs: dict = {key: 0.0 for key in SCENARIO_INPUT_KEYS}
    inputs.update(wages=row["wages"], other_income=row["other_ordinary_income"] - row["adjustments"],
                  self_employment_net=row["self_employment_net"], ordinary_dividends=row["qualified_dividends"],
                  qualified_dividends=row["qualified_dividends"], capital_gains=row["capital_gains"])
    if row["social_security"]:
        capital = max(row["capital_gains"], -kwargs["capital_loss_limit"])
        ss = row["social_security_inputs"]
        half_se = (compute_se_tax(row["self_employment_net"], ss_wage_base=kwargs["ss_wage_base"],
                   w2_ss_wages=row["wages"])["deductible_half"] if row["self_employment_net"] > 0 else 0)
        worksheet = {"ss_benefits": row["social_security"], "other_income": row["wages"] + row["other_ordinary_income"]
            + row["qualified_dividends"] + capital + row["self_employment_net"],
            "tax_exempt_interest": ss["tax_exempt_interest"], "adjustments": row["adjustments"] + half_se,
            "filing_status": status, "lived_apart_from_spouse_all_year": row["medicare_lived_apart_all_year"],
            "lump_sum_prior_year_benefits": 0, "excluded_income_addbacks": ss["excluded_income_addbacks"]}
        inputs["other_income"] += compute_taxable_ss(**worksheet)["taxable_ss"]
        inputs["social_security"] = {"regular_worksheet_confirmed": True, "worksheet": worksheet,
                                     "origin": {key: inputs[key] for key in SCENARIO_INPUT_KEYS}}
    return inputs, kwargs, profile


def _anchor_checks(row: dict, result: dict, profile: dict, law: dict) -> None:
    status = _canonical_status(row["filing_status"], "Roth plan")
    if (_canonical_status(profile.get("filing_status"), "Roth plan") != status
            or _canonical_status(result.get("meta", {}).get("filing_status"), "Roth plan") != status):
        raise PlanError("The filing-year plan status must match the computed return and profile")
    inputs = extract_baseline_inputs(result)
    form = result["form_1040"]
    half_se = (compute_se_tax(inputs["self_employment_net"],
               ss_wage_base=law.get("federal", {}).get("social_security", {}).get("wage_base"),
               w2_ss_wages=inputs["wages"])["deductible_half"] if inputs["self_employment_net"] > 0 else 0)
    if abs((form.get("line_10_adjustments", 0) or 0) - half_se) > 0.01:
        raise PlanError("The anchored ladder does not reprice non-SE above-AGI adjustments; use separate reviewed modeling")
    taxable_ss = form.get("line_6b_taxable_ss", 0) or 0
    aggregate = (inputs["taxable_interest"] + inputs["ordinary_dividends"] - inputs["qualified_dividends"]
                 + inputs["ira_distribution"] + inputs["other_income"] - taxable_ss)
    expected = {"wages": inputs["wages"], "other_ordinary_income": aggregate,
        "self_employment_net": inputs["self_employment_net"], "qualified_dividends": inputs["qualified_dividends"],
        "capital_gains": inputs["capital_gains"], "social_security": form.get("line_6a_social_security", 0) or 0,
        "net_investment_income": roth_ladder._net_investment_income(inputs)}
    if any(abs(row[key] - value) > 0.01 for key, value in expected.items()) or row["adjustments"] != 0 or row["niit_magi_addbacks"] != 0:
        raise PlanError("Filing-year income assumptions must match the anchored ladder; separately model non-SE adjustments or NIIT addbacks")
    if row["irmaa_magi_addbacks"] < (form.get("line_2a_tax_exempt_interest", 0) or 0):
        raise PlanError("IRMAA MAGI addbacks must include the return's tax-exempt interest (Form 1040 line 2a)")
    forecast, kwargs, _ = _year_inputs(row, law)
    if abs(kwargs["deduction"] - return_deduction(result)) > 0.01 or row["ctc_children"] != sum(
            bool(d.get("ctc_eligible")) for d in profile.get("dependents", []) if isinstance(d, dict)):
        raise PlanError("Filing-year deduction/child assumptions must match the computed return")
    if row["social_security"]:
        saved = copy.deepcopy(inputs["social_security"]["worksheet"])
        saved["filing_status"] = row["filing_status"]
        if forecast["social_security"]["worksheet"] != saved:
            raise PlanError("Filing-year Social Security assumptions must match the original reviewed worksheet")
    if row["medicare_lived_apart_all_year"] != profile.get("medicare_lived_apart_all_year"):
        if row["filing_status"] == "MFS":
            raise PlanError("Filing-year MFS Medicare residence answer differs from the profile")
    state = (profile.get("address") or {}).get("state")
    if (state == "CA") != (row["state"] == "CA"):
        raise PlanError("Filing-year state assumption differs from the computed California return")
    if row["state_inputs"] is not None and row["state_inputs"] != result.get("state_planning_inputs"):
        raise PlanError("Filing-year state assumptions differ from the return's reviewed inputs")


def _basis_check(plan: dict, result: dict, profile: dict, return_year: int) -> None:
    ira = plan["ira"]
    if (profile.get("prior_year") or {}).get("import_unresolved"):
        raise PlanError("Resolve imported carryovers before planning from the return")
    form_8606 = result.get("form_8606") or {}
    closing = (form_8606.get("by_taxpayer") or {}).get(ira["owner"])
    if plan["start_year"] == return_year:
        # The plan's worksheet starts from the opening basis and knows no
        # other flow, so it would price the conversion as if these never were.
        # Without the owner's own record, the household lines decide.
        recorded = closing if isinstance(closing, dict) else form_8606
        unattributed = not isinstance(closing, dict) and (result.get("form_1040") or {}).get("line_4a_ira_distributions")
        if unattributed or any(recorded.get(key) for key in (
                "line_1_nondeductible_current", "line_7_distributions", "line_8_converted")):
            raise PlanError("The computed return already records this owner's IRA contributions, distributions "
                            "or conversions; plan from the following year")
        if isinstance(closing, dict) and "line_6_year_end_value" in closing and abs(
                round(ira["traditional_balance"] * (1 + ira["growth_rate"]), 2) - closing["line_6_year_end_value"]) > 1:
            raise PlanError("Filing-year balance and growth must reproduce the return's Form 8606 Dec 31 value (line 6)")
    if (profile.get("flags") or {}).get("subject_to_rmd_" + ira["owner"]) is True and ira["traditional_balance"]:
        raise PlanError("The selected owner is already subject to RMDs; stop before the RMD period")
    if ira["basis_source"] == "reviewed_worksheet":
        if plan["start_year"] == return_year:
            recorded = (profile.get("prior_year") or {}).get("ira_basis_by_taxpayer", {})
            if ira["owner"] in recorded and ira["basis"] != recorded[ira["owner"]]:
                raise PlanError("Reviewed worksheet conflicts with the owner's recorded opening basis; resolve it first")
        else:
            recorded = result.get("form_8606", {}).get("by_taxpayer", {}).get(ira["owner"])
            if isinstance(recorded, dict) and ira["basis"] != recorded.get("basis_remaining"):
                raise PlanError("Reviewed worksheet conflicts with the owner's recorded closing basis; resolve it first")
        return
    if ira["basis_source"] == "opening_profile":
        if plan["start_year"] != return_year:
            raise PlanError("A next-year plan needs the closing Form 8606 balance or a reviewed worksheet")
        from agents.lib.tax_computations.federal.ira_basis import opening_basis
        value = opening_basis(profile)[ira["owner"]]
    else:
        if plan["start_year"] != return_year + 1:
            raise PlanError("Closing Form 8606 basis applies to the following year")
        from agents.lib.tax_computations.federal.ira_basis import validate_individual_8606
        validate_individual_8606(result, profile)
        section = result.get("form_8606", {}).get("by_taxpayer", {}).get(ira["owner"])
        if not isinstance(section, dict):
            raise PlanError("No verified closing individual Form 8606 for the plan owner")
        value = section["basis_remaining"]
        # Line 14's basis includes line 4, the closed year's contributions made
        # from January to April; they are in the account when the year starts.
        if "line_6_year_end_value" in section and ira["traditional_balance"] != round(
                section["line_6_year_end_value"] + (section.get("line_4_following_year_contributions") or 0), 2):
            raise PlanError("Plan balance differs from the owner's closing Form 8606 Dec 31 value (line 6) "
                            "plus its January-April contributions (line 4)")
    if ira["basis"] != value:
        raise PlanError("Plan basis differs from the selected owner's recorded basis source")


def _forecast_state(row: dict, law: dict, outcome: dict) -> tuple[float | None, str | None]:
    if row["state"] is None:
        return None, "State tax is not modeled in this federal-only year; totals exclude it"
    from agents.lib.tax_computations.state.planning import _ca, _REVIEWED_YEARS, StatePlanningError
    if row["year"] not in _REVIEWED_YEARS["CA"]:
        return None, "CA planning has not been reviewed for this year; state cost excluded"
    p = row["state_inputs"]
    if not isinstance(p, dict):
        return None, "Record the forecast year's reviewed state_planning_inputs; state cost excluded"
    cfg = law.get("state", {})
    if (p.get("state") != "CA" or p.get("tax_year") != row["year"]
            or str(p.get("filing_status")).upper() != row["filing_status"].upper() or p.get("scope_confirmed") is not True
            or p.get("residency") != "full_year" or cfg.get("code") != "CA"
            or cfg.get("type") != "graduated_income" or p.get("retirement_basis_matches_federal") is not True
            or p.get("income_treatment", {}).get("ira_distribution") != "taxable"):
        return None, "Confirm full-year CA scope, covered year, status and individual retirement basis/treatment; state cost excluded"
    try:
        priced = _ca(outcome["agi"], outcome["taxable_ss"], p, cfg, row["filing_status"].lower(), row["year"])
        return priced["total_tax"], None
    except (StatePlanningError, ValueError, TypeError, KeyError):
        return None, "CA forecast worksheet is incomplete or outside reviewed scope; state cost excluded"


def _fill(balance: float, value_at, limit: float) -> tuple[float, list[str]]:
    if value_at(0) > limit:
        return 0.0, ["Baseline already exceeds the selected target; no conversion fits"]
    low, high = 0, int(Decimal(str(balance)) * 100)
    while low < high:
        mid = (low + high + 1) // 2
        if value_at(mid / 100) <= limit:
            low = mid
        else:
            high = mid - 1
    return low / 100, []


def build_plan(plan: dict, result: dict, profile: dict, laws: dict[int, dict], *, today=None) -> dict:
    return_year = _integer((result.get("meta") or {}).get("tax_year"), 2000, 2099, "computed return year")
    plan = validate_plan(plan, return_year)
    read_tax_year(result, return_year)
    if (profile.get("tax_year") != return_year
            or _canonical_status(profile.get("filing_status"), "Roth plan") != _canonical_status(
                result.get("meta", {}).get("filing_status"), "Roth plan")):
        raise PlanError("Computed return/profile year and filing status must agree")
    if (result.get("meta") or {}).get("source") == "imported_prior_return":
        raise PlanError("An imported seed is not a computed-return planning baseline")
    _basis_check(plan, result, profile, return_year)
    balance, roth, basis = (float(plan["ira"][key]) for key in ("traditional_balance", "roth_balance", "basis"))
    rows = []
    for assumptions in plan["years"]:
        row = copy.deepcopy(assumptions)
        year, status = row["year"], row["filing_status"]
        law, label = resolve_law(row["law"], year, laws)
        inputs, kwargs, _ = _year_inputs(row, law)
        anchored = year == return_year
        if anchored:
            _anchor_checks(row, result, profile, law)
        threshold, niit_rate = roth_ladder._niit_params(law, status)
        if threshold is not None:
            _number(threshold, "NIIT threshold")
            if law.get("federal", {}).get("niit", {}).get("rate") is None:
                raise PlanError("NIIT planning needs its fetched rate")
        if not 0 <= _number(niit_rate, "NIIT rate") <= 1:
            raise PlanError("NIIT rate must be a fraction")
        premium_year = year + 2
        premium_law, premium_label, tiers = None, "not modeled", []
        if row["premium_law"] is not None:
            premium_law, premium_label = resolve_law(row["premium_law"], premium_year, laws)
            tiers = read_irmaa_tiers(premium_law, status, lived_apart=row["medicare_lived_apart_all_year"])
        separate = irmaa_status_key(status, row["medicare_lived_apart_all_year"]) == "mfs"
        notes = []
        priced: dict[float, dict] = {}
        growth = plan["ira"]["growth_rate"]

        def year_end(amount):
            """Form 8606 line 6, the Dec 31 value: what the conversion leaves,
            grown at the plan's rate. It is also next year's starting balance."""
            return round((balance - amount) * (1 + growth), 2)

        def price(amount):
            if amount in priced:
                return priced[amount]
            worksheet = compute_form_8606(0, basis, year_end(amount), 0, amount)
            taxable = worksheet["taxable_conversion"]
            if anchored:
                # The ladder's line 6 is its value less the conversion, so
                # hand it the year-end value plus the conversion.
                ladder = roth_ladder.build_ladder(result, profile, law, year,
                    max_amount=amount, step=amount or 1, name=plan["name"], today=today or datetime.date.today(),
                    ira_basis=basis, ira_value=round(year_end(amount) + amount, 2), ira_owner=plan["ira"]["owner"])
                rung = ladder.rungs[-1]
                out = {"agi": rung.agi, "taxable_income": rung.taxable_income,
                       "total_federal_tax": rung.total_tax}
                state_cost = rung.state_local_cost if row["state"] == "CA" else None
                state_tax = rung.state_local_tax if row["state"] == "CA" else None
                if ladder.salt_cap_may_move:
                    notes.append("The anchored ladder holds an AGI-sensitive SALT deduction; conversion cost can be higher")
                if ladder.medical_floor_may_move:
                    notes.append("The anchored ladder holds the medical deduction floor; conversion cost can be higher")
                notes.extend(ladder.schedule_a_held)
                if row["state"] == "CA" and not ladder.state_effect.get("modeled"):
                    notes.extend(ladder.state_effect.get("limitations", []))
            else:
                out = compute_scenario(inputs, {"ira_distribution": taxable}, **kwargs)
                out["total_federal_tax"] += compute_niit(out["agi"] + row["niit_magi_addbacks"],
                    row["net_investment_income"], threshold, rate=niit_rate)
                state_tax, state_note = _forecast_state(row, law, out)
                state_cost = None  # converted to a delta against the annual zero-conversion point below
                if state_note:
                    notes.append(state_note)
            magi = round(out["agi"] + row["irmaa_magi_addbacks"], 2)
            premium = (round(irmaa_surcharge(magi, tiers, separate=separate, premium_year=premium_year)
                * sum(row["medicare_months_two_years_later"]) / 12, 2) if tiers else None)
            if not row["medicare_enrollees_two_years_later"]:
                premium = 0.0
            answer = {"outcome": out, "worksheet": worksheet, "state_cost": state_cost, "state_tax": state_tax,
                      "magi": magi, "premium": premium}
            priced[amount] = answer
            return answer

        base = price(0.0)
        if "conversion" in row:
            conversion = row["conversion"]
            if conversion > balance:
                raise PlanError("Conversion exceeds this year's projected IRA balance")
        elif row["fill_to"]["kind"] == "bracket":
            rate = row["fill_to"]["rate"]
            matches = [b for b in kwargs["brackets"] if b["rate"] == rate]
            if len(matches) != 1:
                raise PlanError("Selected bracket rate is not present uniquely in this year's law")
            edge = matches[0]["max"]
            preferential = row["qualified_dividends"] + max(row["capital_gains"], 0)
            conversion, flags = ((balance, []) if edge is None else _fill(balance,
                lambda c: max(0.0, price(c)["outcome"]["taxable_income"] - preferential), edge))
            notes += flags
        else:
            if not tiers:
                raise PlanError("IRMAA target needs complete premium-year tiers")
            index = row["fill_to"]["tier"] - 1
            if index >= len(tiers):
                raise PlanError("IRMAA target tier is absent for this filing status")
            edge = tiers[index]["magi_threshold"]
            if premium_year >= 2019 and index == len(tiers) - 1:
                # The top tier starts AT its boundary, the others above theirs.
                # A return rounded to whole dollars turns 499,999.99 into
                # 500,000, so stop a dollar short.
                edge -= 1
            conversion, flags = _fill(balance, lambda c: price(c)["magi"], edge)
            notes += flags
        selected = price(conversion)
        if not anchored and selected["state_tax"] is not None and base["state_tax"] is not None:
            selected["state_cost"] = round(selected["state_tax"] - base["state_tax"], 2)
        if balance > conversion:
            probe, direction = min(balance, conversion + 1), 1
        elif conversion > 0:
            probe, direction = max(0.0, conversion - 1), -1
        else:
            probe, direction = conversion, 0
        marginal = (round((price(probe)["outcome"]["total_federal_tax"] - selected["outcome"]["total_federal_tax"])
                     / (probe - conversion), 4) if direction else None)
        federal_cost = round(selected["outcome"]["total_federal_tax"] - base["outcome"]["total_federal_tax"], 2)
        premium_cost = (round(selected["premium"] - base["premium"], 2)
                        if selected["premium"] is not None and base["premium"] is not None else None)
        if selected["state_cost"] is None:
            notes.append("State not modeled for this year; totals exclude state tax")
        if premium_cost is None:
            notes.append("IRMAA not modeled for this premium year; totals exclude Medicare costs")
        if threshold is None and row["net_investment_income"]:
            notes.append("NIIT not modeled; federal forecast excludes this cost")
        if row["ctc_children"]:
            notes.append("The child tax credit is priced as nonrefundable. Where a conversion only uses up unused "
                         "credit, the refundable additional child tax credit (Schedule 8812) also falls, so the "
                         "federal cost can be higher")
        next_basis = selected["worksheet"]["basis_remaining"]
        next_balance = year_end(conversion)
        next_roth = round((roth + conversion) * (1 + growth), 2)
        record = {"year": year, "calculation": "computed" if anchored else "forecast", "law": label,
            "premium_year": premium_year, "premium_law": premium_label, "assumptions": row,
            "conversion": round(conversion, 2), "taxable_conversion": selected["worksheet"]["taxable_conversion"],
            "federal_tax": round(selected["outcome"]["total_federal_tax"], 2), "federal_cost": federal_cost,
            "federal_marginal_rate": marginal, "state_cost": selected["state_cost"],
            "irmaa_surcharge": selected["premium"], "irmaa_cost": premium_cost, "magi": selected["magi"],
            "ira_start": balance, "roth_start": roth, "basis_start": basis, "basis_remaining": next_basis,
            "ira_next_year": next_balance, "roth_next_year": next_roth, "growth_rate": growth,
            "form_8606": selected["worksheet"], "rate_inputs": kwargs, "baseline_income_inputs": inputs,
            "niit_inputs": {"threshold": threshold, "rate": niit_rate, "net_investment_income": row["net_investment_income"],
                            "magi_addbacks": row["niit_magi_addbacks"]},
            "premium_inputs": {"tax_year": premium_year, "tiers": tiers, "separate": separate,
                               "enrollment_months": row["medicare_months_two_years_later"]},
            "state_law_inputs": copy.deepcopy(law.get("state")) if row["state"] == "CA" else None,
            "source_law_sha256": hashlib.sha256(json.dumps(laws[_law_ref(row["law"], year)[0]],
                                       sort_keys=True, allow_nan=False).encode()).hexdigest(),
            "law_sources": copy.deepcopy(laws[_law_ref(row["law"], year)[0]].get("_sources", {})),
            "premium_law_sources": copy.deepcopy(premium_law.get("_sources", {})) if premium_law is not None else None,
            "notes": list(dict.fromkeys(notes))}
        if anchored:
            record["anchor_inputs"] = {"line_24": result["form_1040"]["line_24_total_tax"],
                "baseline_income": extract_baseline_inputs(result), "deduction": return_deduction(result),
                "schedule_a_inputs": copy.deepcopy((result.get("schedule_a") or {}).get("inputs")),
                "state_planning_inputs": copy.deepcopy(result.get("state_planning_inputs")),
                "recorded_ca_tax": (result.get("california_540") or {}).get("ca_total_tax")}
        if first_non_finite(record) is not None:
            raise PlanError("Plan overflowed; no non-finite forecast can be published")
        rows.append(record)
        balance, roth, basis = next_balance, next_roth, next_basis
    report = {"schema": SCHEMA, "name": plan["name"], "created": (today or datetime.date.today()).isoformat(),
        "input": plan, "return_year": return_year, "years": rows, "limitations": LIMITATIONS,
        "totals": {"conversions": round(sum(r["conversion"] for r in rows), 2),
                   "federal_cost": round(sum(r["federal_cost"] for r in rows), 2),
                   "state_cost": round(sum(r["state_cost"] for r in rows), 2) if all(r["state_cost"] is not None for r in rows) else None,
                   "irmaa_cost": round(sum(r["irmaa_cost"] for r in rows), 2) if all(r["irmaa_cost"] is not None for r in rows) else None}}
    if first_non_finite(report) is not None:
        raise PlanError("Plan totals overflowed; no non-finite forecast can be published")
    return report


def render_markdown(report: dict) -> str:
    def money(value):
        return "not modeled" if value is None else f"${value:,.2f}"
    lines = [f"# Roth plan: {report['name']}", "",
        "Forecast assumptions are shown per year. Hypothetical law is never a filing computation.", "",
        "| Year | Calculation / law | Conversion | Federal cost | Marginal federal rate | State cost | IRMAA cost (premium year) | Basis left | IRA start → next | Roth start → next |",
        "|---|---|---:|---:|---:|---:|---|---:|---|---|"]
    for row in report["years"]:
        rate = "—" if row["federal_marginal_rate"] is None else f"{row['federal_marginal_rate']:.2%}"
        lines.append(f"| {row['year']} | {row['calculation']}; {row['law']} | {money(row['conversion'])} | {money(row['federal_cost'])} | {rate} | {money(row['state_cost'])} | {money(row['irmaa_cost'])} ({row['premium_year']}) | {money(row['basis_remaining'])} | {money(row['ira_start'])} → {money(row['ira_next_year'])} | {money(row['roth_start'])} → {money(row['roth_next_year'])} |")
    lines += ["", "Totals (unmodeled costs are excluded): " + "; ".join(
        f"{key.replace('_', ' ')} {money(value)}" for key, value in report["totals"].items()), ""]
    lines += ["Marginal federal rate uses an adjacent feasible gross conversion amount up to one dollar. State tax and Medicare premiums are separate.", ""]
    for row in report["years"]:
        lines += [f"- {row['year']}: growth assumption {row['growth_rate']:.2%}; premium law: {row['premium_law']}."]
        lines += [f"- {row['year']}: {note}" for note in row["notes"]]
    lines += ["", "Limitations:", "", *[f"- {line}" for line in report["limitations"]], ""]
    return "\n".join(lines)


def _read(path: Path) -> dict:
    secure_path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or first_non_finite(data) is not None:
        raise PlanError("Plan/baseline input must be a finite JSON object")
    return data


def fresh_laws(plan: dict, workspace: Path) -> dict[int, dict]:
    from agents.lib.tax_law_fetcher import check_freshness
    needed = set()
    for row in plan["years"]:
        needed.add(_law_ref(row["law"], row["year"])[0])
        if row["premium_law"] is not None:
            needed.add(_law_ref(row["premium_law"], row["year"] + 2)[0])
    laws = {}
    for year in sorted(needed):
        path = workspace / f"tax_law_{year}.json"
        data = _read(path)  # refuse corrupt JSON before the cache reader's deletion path
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        _validate_tax_law_year(data, year)
        if check_freshness(year, str(workspace))["status"] != "FRESH":
            raise PlanError("Each explicitly selected law cache must pass freshness; no implicit future-law fallback")
        if hashlib.sha256(path.read_bytes()).hexdigest() != before:
            raise PlanError("Law changed during its freshness check")
        laws[year] = data
    return laws


def _main(argv=None) -> int:
    from agents.lib.workspace import WorkspaceError, add_workspace_argument, resolve_workspace
    parser = argparse.ArgumentParser(description=__doc__)
    add_workspace_argument(parser)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    try:
        if not _NAME.fullmatch(args.name):
            raise PlanError("Invalid plan name")
        workspace = resolve_workspace(args.workspace).resolve()
        scenarios = workspace / "scenarios"
        secure_path(scenarios)  # inspect raw path: refuse an internal symlink/junction too
        plan = _read(scenarios / f"roth_plan_{args.name}.input.json")
        result, profile = _read(workspace / "result.json"), _read(workspace / "taxpayer_profile.json")
        plan = validate_plan(plan, result.get("meta", {}).get("tax_year"))
        if plan["name"] != args.name:
            raise PlanError("Plan name differs from its input filename")
        report = build_plan(plan, result, profile, fresh_laws(plan, workspace))
        for target in (scenarios / f"roth_plan_{args.name}.json", scenarios / f"roth_plan_{args.name}.md"):
            try:
                target.lstat()
            except FileNotFoundError:
                continue
            secure_path(target)  # raw leaf: do not follow a link to an input or another artifact
        save_scenario(workspace, "roth_plan_" + args.name, report)
        write_secure(scenarios / f"roth_plan_{args.name}.md", render_markdown(report))
        print("Saved the reviewed Roth plan JSON and Markdown under scenarios/.")
        return 0
    except WorkspaceError:
        print("Roth plan refused: workspace unavailable.", file=sys.stderr)
        return 2
    except PlanError as exc:
        print(f"Roth plan refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # Library/JSON/OS exceptions may quote private input paths or values.
        print("Roth plan refused: invalid, missing, inconsistent or unsupported local inputs; review the plan contract.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_main())
