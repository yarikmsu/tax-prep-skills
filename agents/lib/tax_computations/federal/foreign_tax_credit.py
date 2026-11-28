"""Validate reviewed Form 1116 workpapers and their Schedule B balances."""

from __future__ import annotations

import math

from agents.lib.tax_computations.federal.government_benefits import _money

_BASKETS = {"section_951a", "foreign_branch", "passive", "general",
            "section_901j", "treaty_resourced", "lump_sum"}
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
    return by_basket


def verify_form_1116(section: dict, result: dict, profile: dict) -> list[dict]:
    """Check reviewed source amounts and form arithmetic; return closing baskets."""
    form = result.get("form_1040") or {}
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
        raise ValueError("Reconcile the prior foreign tax carryover by category and origin year before using Form 1116.")

    line_18_base = _money(workpaper.get("line_18_base"), "Form 1116 line 18 worksheet base", signed=True)
    if year == 2025:
        agi = _money(form.get("line_11_agi"), "Form 1040 line 11b", signed=True)
        deductions = _money(form.get("line_14_total_deductions"), "Form 1040 line 14")
        schedule_1a = _money(form.get("line_13b_schedule_1a_deductions", 0), "Schedule 1-A line 37")
        if line_18_base != round(agi - deductions + schedule_1a, 2):
            raise ValueError("Form 1116 line 18 starts with Form 1040 line 11b minus line 14 plus Schedule 1-A line 37.")
    adjustment = _money(workpaper.get("line_18_adjustment"), "Reviewed worldwide qualified dividend and gain adjustments")
    line_18 = _money(workpaper.get("line_18"), "Form 1116 line 18")
    if line_18 != max(0.0, round(line_18_base - adjustment, 2)):
        raise ValueError("Form 1116 line 18 must match its adjusted worldwide income worksheet.")
    line_20 = _money(workpaper.get("line_20"), "Form 1116 line 20")
    if method == "checked":
        schedule_2 = _money(workpaper.get("schedule_2_line_1z"), "Schedule 2 line 1z")
        regular_tax = _money(form.get("line_16_tax"), "Form 1040 line 16")
        if (schedule_2 > _money(form.get("line_17_schedule2_part1", 0), "Form 1040 line 17")
                or line_20 != round(regular_tax + schedule_2, 2)):
            raise ValueError("Form 1116 line 20 must reconcile to Form 1040 line 16 and Schedule 2 line 1z.")

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
        ref = row.get("source_ref")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError(f"Form 1116 category #{number} needs the reviewed form or workpaper source.")
        tax = _money(row.get("line_9_tax_paid"), "Form 1116 line 9")
        carry = _origins(row.get("line_10_carryover_by_origin_year"), year - 10, year - 1)
        prior_amounts = opening.get(basket, {})
        if any(amount > prior_amounts.get(origin, 0) for origin, amount in carry.items()):
            raise ValueError(f"Form 1116 category #{number} exceeds the available carryover for an origin year.")
        eligible = _money(row.get("line_14_eligible_tax"), "Form 1116 line 14")
        if eligible > round(tax + math.fsum(carry.values()), 2):
            raise ValueError(f"Form 1116 category #{number} overstates eligible foreign tax.")
        income = _money(row.get("line_17_foreign_income"), "Form 1116 line 17", signed=True)
        limitation = _money(row.get("line_23_limitation"), "Form 1116 line 23")
        credit = _money(row.get("line_24_credit"), "Form 1116 line 24")
        if credit != min(eligible, limitation) or (not line_18 or income <= 0) and credit:
            raise ValueError(f"Form 1116 category #{number} must reconcile its eligible tax, limitation and credit.")
        used = _origins(row.get("used_carryover_by_origin_year", {}), year - 10, year - 1)
        if any(amount > carry.get(origin, 0) for origin, amount in used.items()):
            raise ValueError(f"Form 1116 category #{number} overstates carryover used.")
        used_total = round(math.fsum(used.values()), 2)
        if used_total > credit:
            raise ValueError(f"Form 1116 category #{number} uses more carryover than its credit.")
        current_unused = round(tax - (credit - used_total), 2)
        if current_unused < 0:
            raise ValueError(f"Form 1116 category #{number} claims a credit above the taxes and carryover used.")
        if method == "checked":
            if basket not in ("foreign_branch", "passive", "general"):
                raise ValueError(f"Form 1116 category #{number} needs the reviewed external form.")
            ratio = min(1.0, max(0.0, income) / line_18) if line_18 else 0.0
            # The instructions allow a ratio rounded to at least four decimal
            # places. Validate its rounding before checking the recorded tax.
            recorded_ratio = row.get("line_19_ratio")
            if (not isinstance(recorded_ratio, (int, float)) or isinstance(recorded_ratio, bool)
                    or not 0 <= recorded_ratio <= 1
                    or not math.isfinite(recorded_ratio)
                    or not math.isclose(recorded_ratio, ratio, rel_tol=0, abs_tol=0.00005)):
                raise ValueError(f"Form 1116 category #{number} has a line 19 ratio inconsistent with lines 17 and 18.")
            if abs(limitation - round(recorded_ratio * line_20, 2)) > 0.50:
                raise ValueError(f"Form 1116 category #{number} has a line 23 limitation inconsistent with line 21.")
            current_tax = round(eligible - math.fsum(carry.values()), 2)
            if current_tax < 0:
                raise ValueError(f"Form 1116 category #{number} needs the reviewed external reconciliation.")
            remaining = max(0.0, round(credit - current_tax, 2))
            for origin, available in sorted(carry.items()):
                expected = min(available, remaining)
                if used.get(origin, 0) != expected:
                    raise ValueError(f"Form 1116 category #{number} must use the oldest carryover first.")
                remaining = round(remaining - expected, 2)
            current_unused = max(0.0, round(current_tax - limitation, 2))
        balances = _origins(row.get("closing_carryforward_by_origin_year"), year - 9, year)
        if balances.get(str(year), 0) > current_unused or any(
            balances.get(origin, 0) != round(available - used.get(origin, 0), 2)
            for origin, available in carry.items() if int(origin) >= year - 9
        ) or any(origin not in carry and int(origin) != year for origin in balances):
            raise ValueError(f"Form 1116 category #{number} must reconcile the closing carryforward to Schedule B.")
        if balances:
            closing.append({"basket": basket, "carryforward_by_origin_year": balances,
                            "provenance": {"tax_year": year, "source_ref": ref}})
        paid.append(tax)
        credits.append(credit)
        applied.append(used_total)
        created.append(balances.get(str(year), 0))

    if any(balances and basket not in reviewed for basket, balances in opening.items()):
        raise ValueError("Reconcile each opening Form 1116 carryover category, including any balance not used this year.")
    if round(math.fsum(paid), 2) != _money(section.get("foreign_tax_paid_usd"), "Total foreign tax paid"):
        raise ValueError("Form 1116 category amounts must reconcile to total foreign tax paid.")
    if (round(math.fsum(applied), 2) != _money(section.get("carryover_applied_usd"), "Schedule B line 4 total")
            or round(math.fsum(created), 2) != _money(section.get("carryforward_created_usd"), "Form 1116 new carryforward")):
        raise ValueError("Form 1116 must reconcile the carryover applied and carryforward created by category and year.")
    reduction = _money(workpaper.get("line_34_boycott_reduction"), "Form 1116 line 34")
    allowed = round(min(line_20, math.fsum(credits)) - reduction, 2)
    if allowed < 0 or allowed != _money(section.get("ftc_allowed"), "Form 1116 line 35"):
        raise ValueError("Form 1116 Part IV must reconcile to the credit on Schedule 3 line 1.")
    return closing
