#!/usr/bin/env python3
"""Tax computation functions — parameterized by rates from tax_law JSON.

All functions take rates/brackets as parameters. The only hardcoded values
are structural IRS constants that rarely change:
  - SE threshold: $400 minimum, SE factor: 92.35%, SE deduction: 50%
  - Additional Medicare Tax: 0.9% (on wages > threshold)
  - Regular Medicare: 1.45% employee / 2.9% SE total
  - Social Security: 6.2% employee + 6.2% employer = 12.4% SE total
  - Capital gains rates: 0% / 15% / 20%
  - QBI deduction: 20% (Sec 199A)
All bracket/threshold/credit values come from tax_law JSON.
"""

import calendar
import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from agents.lib.irs_dates import next_business_day  # noqa: E402

# SE tax filing threshold per IRS rules
SE_TAX_THRESHOLD = 400

# Max net capital loss deductible against ordinary income per year (IRC §1211(b));
# excess carries forward. Structural statutory constants.
CAPITAL_LOSS_LIMIT = 3000
CAPITAL_LOSS_LIMIT_MFS = 1500

# Schedule 8812 structural statutory constants (IRC §24(d)): the refundable ACTC
# is 15% of earned income over $2,500. Neither is indexed. The per-child
# refundable CAP ($1,700 for TY2024/2025) IS indexed and lives in tax_law
# (federal.child_tax_credit.refundable_limit), not here.
ACTC_EARNED_INCOME_FLOOR = 2500
ACTC_EARNED_INCOME_RATE = 0.15

# Credit for Other Dependents (IRC §24(h)(4)): flat $500 per non-CTC dependent,
# statutorily fixed (not indexed), never refundable.
ODC_PER_DEPENDENT = 500


def _coerce_numeric(value, default=None):
    """Coerce a value to float, handling string amounts like '$5,000'.

    Returns None by default for unparseable values so callers must handle
    the failure explicitly. Pass default=0 only for non-financial display fields.

    Rejects bool (isinstance(True, int) is True in Python, but booleans
    are never valid financial amounts — always a data error).
    """
    if isinstance(value, bool):
        raise TypeError(f"Expected numeric value, got bool: {value}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def compute_bracket_tax(taxable_income: float, brackets: list[dict]) -> tuple[float, list[dict]]:
    """Compute tax using progressive brackets.

    Args:
        taxable_income: Amount to tax.
        brackets: List of {"rate": float, "min": int, "max": int|None}.
                  max=None means no upper limit.

    Returns:
        (total_tax, breakdown) where breakdown is list of
        {"rate", "range", "taxable_in_bracket", "tax"}.

    Raises:
        ValueError: If brackets list is empty, None, or contains malformed entries.
    """
    if taxable_income is None:
        raise ValueError("taxable_income cannot be None. Check Form 1040 line 15 computation.")
    if taxable_income < 0:
        raise ValueError(
            f"taxable_income cannot be negative (got {taxable_income}). "
            "Check Form 1040 line 15 computation."
        )
    if not brackets:
        raise ValueError("Brackets list is empty or None. Load brackets from tax_law JSON first.")

    remaining = taxable_income
    total_tax = 0.0
    breakdown = []

    for i, bracket in enumerate(brackets):
        if "rate" not in bracket or "min" not in bracket:
            raise ValueError(
                f"Bracket at index {i} missing required keys 'rate' and/or 'min'. "
                f"Got keys: {list(bracket.keys())}. "
                "Check tax_law JSON for malformed bracket entries."
            )
        rate = bracket["rate"]
        b_min = bracket["min"]
        b_max = bracket.get("max")

        if remaining <= 0:
            break

        if b_max is not None:
            bracket_size = b_max - b_min
            taxable_in_bracket = min(remaining, bracket_size)
        else:
            taxable_in_bracket = remaining

        tax = taxable_in_bracket * rate
        total_tax += tax
        remaining -= taxable_in_bracket

        b_upper = b_max if b_max is not None else b_min + taxable_in_bracket
        range_str = f"{b_min:,.0f} - {b_upper:,.0f}"
        breakdown.append({
            "rate": rate,
            "range": range_str,
            "taxable_in_bracket": round(taxable_in_bracket, 2),
            "tax": round(tax, 2),
        })

    return round(total_tax, 2), breakdown


def compute_qdcg_tax(
    taxable_income: float,
    qualified_dividends: float,
    net_ltcg: float,
    brackets: list[dict],
    cg_zero_limit: float,
    cg_fifteen_limit: float,
) -> dict:
    """Qualified Dividends and Capital Gain Tax Worksheet.

    IMPORTANT: net_ltcg must be NET long-term capital gain from Schedule D line 15,
    AFTER subtracting any long-term losses. Do NOT use gross capital gain distributions.

    Args:
        taxable_income: Form 1040 line 15.
        qualified_dividends: Form 1040 line 3a.
        net_ltcg: Schedule D line 15 (net, after losses).
        brackets: Ordinary income brackets.
        cg_zero_limit: 0% capital gains threshold for filing status.
        cg_fifteen_limit: 15% capital gains threshold for filing status.

    Returns:
        Dict with keys including:
          final_tax: The tax amount to use (lower of QDCG worksheet vs regular).
          used_qdcg_worksheet: bool — True if QDCG method was beneficial.
          ordinary_tax, qdcg_tax, regular_tax: Component details.

    Raises:
        ValueError: If rate thresholds are None (not yet fetched from tax_law JSON).
    """
    if taxable_income is None:
        raise ValueError("taxable_income cannot be None. Check Form 1040 line 15.")
    if qualified_dividends is None:
        raise ValueError("qualified_dividends cannot be None. Check Form 1040 line 3a.")
    if net_ltcg is None:
        raise ValueError("net_ltcg cannot be None. Check Schedule D line 15.")
    if cg_zero_limit is None or cg_fifteen_limit is None:
        raise ValueError(
            "Capital gains rate thresholds not loaded from tax_law JSON. "
            "Run /tax-setup to fetch current rates."
        )

    qdcg = qualified_dividends + max(0, net_ltcg)
    ordinary_income = max(0, taxable_income - qdcg)

    # Preferential income is capped at taxable income (QDCG worksheet line 1): the
    # standard deduction can shield part of the gain, and dollars eliminated by
    # deductions must not be taxed. When qdcg <= taxable_income this is a no-op.
    qdcg_taxed = min(qdcg, max(0.0, taxable_income))

    # Tax on ordinary income
    ordinary_tax, ordinary_breakdown = compute_bracket_tax(ordinary_income, brackets)

    # Tax on QDCG at preferential rates (on the capped amount)
    qdcg_in_zero = max(0, min(qdcg_taxed, cg_zero_limit - ordinary_income))
    remaining_qdcg = qdcg_taxed - qdcg_in_zero
    qdcg_in_fifteen = max(0, min(remaining_qdcg, cg_fifteen_limit - ordinary_income - qdcg_in_zero))
    qdcg_in_twenty = max(0, remaining_qdcg - qdcg_in_fifteen)

    qdcg_tax = (qdcg_in_fifteen * 0.15) + (qdcg_in_twenty * 0.20)

    total_tax = ordinary_tax + qdcg_tax

    # Compare with regular tax (use lower)
    regular_tax, _ = compute_bracket_tax(taxable_income, brackets)
    final_tax = min(total_tax, regular_tax)

    return {
        "taxable_income": taxable_income,
        "qualified_dividends": qualified_dividends,
        "net_ltcg": net_ltcg,
        "qdcg_total": round(qdcg, 2),
        "ordinary_income": round(ordinary_income, 2),
        "ordinary_tax": round(ordinary_tax, 2),
        "ordinary_breakdown": ordinary_breakdown,
        "qdcg_at_0_pct": round(qdcg_in_zero, 2),
        "qdcg_at_15_pct": round(qdcg_in_fifteen, 2),
        "qdcg_at_20_pct": round(qdcg_in_twenty, 2),
        "qdcg_tax": round(qdcg_tax, 2),
        "worksheet_total": round(total_tax, 2),
        "regular_tax": round(regular_tax, 2),
        "final_tax": round(final_tax, 2),
        "used_qdcg_worksheet": final_tax < regular_tax,
    }


def compute_se_tax(
    net_profit: float,
    ss_wage_base: float = None,
    w2_ss_wages: float = 0,
    ss_rate: float = 0.124,
    medicare_rate: float = 0.029,
    deductible_factor: float = 0.9235,
) -> dict:
    """Compute self-employment tax with SS wage base cap.

    SE tax has two components: Social Security (12.4%, capped at SS wage base
    minus W-2 SS wages) and Medicare (2.9%, uncapped).

    When net_profit is zero or negative, returns $0 SE tax (net loss).
    When net_profit is positive but below SE_TAX_THRESHOLD ($400), also returns
    $0 SE tax (IRS minimum filing threshold). The returned 'note' field
    distinguishes these two cases. Input validation runs unconditionally
    before either early return to catch data errors regardless of profit.

    Args:
        net_profit: Schedule C line 31 (can be negative for net loss).
        ss_wage_base: SS wage base from tax_law JSON. If None, uses legacy flat
                      (ss_rate + medicare_rate) combined rate. Must be positive
                      if provided — 0 or non-numeric values are rejected.
        w2_ss_wages: W-2 Social Security wages (Box 3) to subtract from SS cap.
                     Must be non-negative.
        ss_rate: Social Security rate (default 12.4%).
        medicare_rate: Medicare rate (default 2.9%).
        deductible_factor: Net earnings factor applied to net profit (default 92.35%).

    Returns:
        Dict with SE tax details including computation method and component
        breakdown (when ss_wage_base is provided).

    Raises:
        ValueError: If ss_wage_base is 0, negative, or non-numeric (including bool),
                    or if w2_ss_wages is negative.
    """
    # Validate inputs unconditionally — catches data errors even on $0 profit
    if net_profit is None:
        raise ValueError("net_profit cannot be None. Check Schedule C line 31.")
    if ss_wage_base is not None:
        try:
            ss_wage_base = _coerce_numeric(ss_wage_base, default=None)
        except TypeError as e:
            raise ValueError(
                f"ss_wage_base must be a numeric value, got {type(ss_wage_base).__name__}. "
                "Check tax_law JSON federal.social_security.wage_base."
            ) from e
        if ss_wage_base is None or ss_wage_base <= 0:
            raise ValueError(
                f"ss_wage_base must be positive (got {ss_wage_base!r}). "
                "Check tax_law JSON federal.social_security.wage_base."
            )
    if w2_ss_wages is None:
        raise ValueError("w2_ss_wages cannot be None. Pass 0 if no W-2. Check W-2 Box 3.")
    if w2_ss_wages < 0:
        raise ValueError(
            f"w2_ss_wages cannot be negative (got {w2_ss_wages}). "
            "Check W-2 Box 3 value."
        )

    if net_profit <= 0:
        return {
            "net_profit": round(net_profit, 2),
            "se_earnings": 0,
            "se_tax": 0,
            "deductible_half": 0,
            "note": "No SE tax — net loss",
        }

    if net_profit < SE_TAX_THRESHOLD:
        return {
            "net_profit": round(net_profit, 2),
            "se_earnings": 0,
            "se_tax": 0,
            "deductible_half": 0,
            "note": f"No SE tax — net profit ${net_profit:.2f} below ${SE_TAX_THRESHOLD} threshold",
        }

    se_earnings = net_profit * deductible_factor

    if ss_wage_base is not None:
        # Split into SS (capped) and Medicare (uncapped) components
        ss_room = max(0, ss_wage_base - w2_ss_wages)
        ss_taxable = min(se_earnings, ss_room)
        ss_tax = ss_taxable * ss_rate
        medicare_tax = se_earnings * medicare_rate
        se_tax = ss_tax + medicare_tax
    else:
        # Legacy flat rate (backward compatible)
        se_tax = se_earnings * (ss_rate + medicare_rate)

    deductible_half = se_tax / 2

    result = {
        "net_profit": round(net_profit, 2),
        "se_earnings": round(se_earnings, 2),
        "se_tax": round(se_tax, 2),
        "deductible_half": round(deductible_half, 2),
    }
    if ss_wage_base is not None:
        result["ss_wage_base"] = ss_wage_base
        result["w2_ss_wages"] = w2_ss_wages
        result["ss_room"] = round(ss_room, 2)
        result["ss_tax"] = round(ss_tax, 2)
        result["medicare_tax"] = round(medicare_tax, 2)
    else:
        result["note"] = "Legacy flat rate used (ss_wage_base not provided)"
    return result


def compute_additional_medicare(
    medicare_wages: float,
    se_income: float,
    threshold: float,
    withheld: float,
    rate: float = 0.009,
) -> dict:
    """Compute Additional Medicare Tax (Form 8959).

    Args:
        medicare_wages: W-2 Box 5.
        se_income: 92.35%-adjusted SE earnings (Schedule SE line 4), not raw
                   Schedule C profit. Pass 0 if no self-employment.
        threshold: Filing status threshold (MFJ=250K, Single=200K).
        withheld: Medicare tax withheld from W-2 Box 6.
        rate: Additional Medicare rate (0.9%).

    Returns:
        Dict with Form 8959 computation.

    Raises:
        ValueError: If threshold or rate is None, or wages/withheld are negative.
    """
    if se_income is None:
        raise ValueError("se_income cannot be None. Pass 0 if no self-employment.")
    if threshold is None:
        raise ValueError(
            "Additional Medicare threshold cannot be None. "
            "Load from tax_law JSON federal.additional_medicare_tax.threshold."
        )
    if rate is None:
        raise ValueError(
            "Additional Medicare rate cannot be None. "
            "Load from tax_law JSON federal.additional_medicare_tax.rate."
        )
    if medicare_wages is None:
        raise ValueError("medicare_wages cannot be None. Pass 0 if no W-2. Check W-2 Box 5.")
    if withheld is None:
        raise ValueError("withheld cannot be None. Pass 0 if no W-2. Check W-2 Box 6.")
    if medicare_wages < 0:
        raise ValueError(f"medicare_wages cannot be negative (got {medicare_wages}). Check W-2 Box 5.")
    if withheld < 0:
        raise ValueError(f"withheld cannot be negative (got {withheld}). Check W-2 Box 6.")

    combined = medicare_wages + se_income
    excess = max(0, combined - threshold)
    additional_tax = excess * rate

    # Form 8959 Part IV reconciliation (lines 19-22):
    #   line 19 = Medicare tax withheld (W-2 Box 6)
    #   line 20 = Medicare WAGES (Box 5) × 1.45% — wages only. SE income never
    #   enters Part IV: there is no withholding on SE earnings to reconcile
    #   (SE Additional Medicare is paid with the return, not withheld).
    #   line 21 = line 19 − line 20 = Additional Medicare withheld by employer
    #   Over-withholding credit = Addl Medicare withheld − Addl Medicare owed, if positive
    regular_medicare = medicare_wages * 0.0145
    withheld_addl = max(0, withheld - regular_medicare)  # employer's Addl Medicare portion
    over_withholding = max(0, withheld_addl - additional_tax)  # credit if over-withheld

    return {
        "medicare_wages": round(medicare_wages, 2),
        "se_income": round(se_income, 2),
        "combined": round(combined, 2),
        "threshold": threshold,
        "excess_over_threshold": round(excess, 2),
        "additional_tax": round(additional_tax, 2),
        "regular_medicare": round(regular_medicare, 2),
        "withheld": round(withheld, 2),
        "withheld_addl": round(withheld_addl, 2),
        "over_withholding_credit": round(over_withholding, 2),
    }


def compute_niit(
    magi: float,
    net_investment_income: float,
    threshold: float,
    *,
    rate: float = 0.038,
) -> float:
    """Net Investment Income Tax (Form 8960) — the 3.8% surtax.

    NIIT is 3.8% of the LESSER of net investment income and the amount by which MAGI
    exceeds the filing-status threshold. The keystone compute_scenario deliberately
    omits NIIT; the Roth-ladder layers it on top, because a conversion raises MAGI and
    can newly subject existing investment income to the surtax.

    Args:
        magi: Modified AGI (the toolkit approximates this as AGI for planning).
        net_investment_income: Interest + dividends + net capital gains, etc. A Roth
                  conversion / IRA distribution is NOT investment income — exclude it.
        threshold: Filing-status MAGI threshold (single 200K / MFJ 250K / MFS 125K).
                  When None (not fetched into tax_law), NIIT is treated as not modeled
                  and 0.0 is returned rather than raising — callers degrade gracefully.
        rate: NIIT rate (0.038).

    Returns:
        NIIT owed, rounded to 2 dp. 0.0 when threshold is None, NII <= 0, or MAGI is
        below the threshold.

    Raises:
        ValueError: If magi, net_investment_income, threshold (when not None), or rate
            is non-numeric/bool.
    """
    if threshold is None:
        return 0.0
    try:
        magi_n = _coerce_numeric(magi, default=None)
        nii_n = _coerce_numeric(net_investment_income, default=None)
        thr_n = _coerce_numeric(threshold, default=None)
        rate_n = _coerce_numeric(rate, default=None)
    except TypeError:
        raise ValueError("compute_niit arguments must be numeric, got bool")
    if None in (magi_n, nii_n, thr_n, rate_n):
        raise ValueError("compute_niit arguments must be numeric")
    if nii_n <= 0:
        return 0.0
    excess = max(0.0, magi_n - thr_n)
    base = min(nii_n, excess)
    return round(base * rate_n, 2)


def compute_form_8606(
    nondeductible_contrib_current: float,
    prior_basis: float,
    year_end_value: float,
    distributions_not_converted: float,
    amount_converted: float,
    contrib_made_by_deadline: float = 0.0,
    disaster_repayable_amount: float = 0.0,
) -> dict:
    """Form 8606 Part I — nondeductible IRA basis and the pro-rata rule.

    When a traditional IRA holds after-tax basis (nondeductible contributions)
    alongside pre-tax money, the IRS does not let you withdraw or convert the
    after-tax dollars selectively. Every distribution and Roth conversion is a
    blended withdrawal, with the nontaxable fraction set by the basis ratio
    (line 10 = basis / total IRA value). This closes the backdoor-Roth pro-rata
    trap and the "full conversion is taxable" simplification deferred by the
    Roth ladder (#6).

    Pure and structural — Form 8606 carries no tax rates, only arithmetic.

    Args:
        nondeductible_contrib_current: Line 1 — nondeductible contributions for
            the current year.
        prior_basis: Line 2 — total basis in traditional IRAs from prior years
            (last year's line 14 carryforward).
        year_end_value: Line 6 — Dec 31 FMV of ALL traditional/SEP/SIMPLE IRAs.
            On a conversion of C out of a beginning balance B, this is B - C, so
            line 9 (= line 6 + line 7 + line 8) recovers the beginning balance B.
        distributions_not_converted: Line 7 — distributions taken (not converted).
        amount_converted: Line 8 — amount converted to a Roth IRA.
        contrib_made_by_deadline: Line 4 — contributions made Jan 1-Apr 15 of the
            following year that are **included on line 1** (usually 0). Per the IRS
            instruction this is a SUBSET of line 1, so the function rejects
            contrib_made_by_deadline > nondeductible_contrib_current.
        disaster_repayable_amount: Line 15b — per the IRS instruction, "the amount on
            line 15a allocable to qualified disaster distributions reported on Form
            8915-F." Note this is the **post-pro-rata TAXABLE portion** of the disaster
            distribution, NOT the gross disaster amount from line 7 (pro-rata strips out
            the basis-recovery share before any of it reaches line 15b). If line 7 is
            entirely disaster, line 15b equals `line_15a_taxable_pre_disaster`. Disaster
            amounts are amortized separately on Form 8915-F over 3 years rather than
            taxed immediately on 1040 4b; the toolkit does NOT model that amortization.

    Returns:
        Dict of Form 8606 Part I lines plus:
          basis_remaining: line 14 (next-year carryforward).
          taxable_conversion: line 18 (taxable part of the conversion).
          taxable_distributions: line 15c (taxable distributions flowing to 1040 4b,
              after disaster-allocable carve-out). Equals line 15a when disaster_repayable_amount=0.
          taxable_to_1040: line 15c + line 18 (flows to 1040 line 4b).
          ratio: line 10, rounded to 5 dp for display.
          pro_rata_triggered: True when basis, pre-tax money, and a conversion
            all coexist — the conversion is only partly tax-free (the trap).

    Raises:
        ValueError: If any input is non-numeric/bool or negative. amount_converted
            exceeding the available balance is NOT rejected here (this function is
            pure arithmetic); the caller caps and flags that.
    """
    fields = {
        "nondeductible_contrib_current": nondeductible_contrib_current,
        "prior_basis": prior_basis,
        "year_end_value": year_end_value,
        "distributions_not_converted": distributions_not_converted,
        "amount_converted": amount_converted,
        "contrib_made_by_deadline": contrib_made_by_deadline,
        "disaster_repayable_amount": disaster_repayable_amount,
    }
    coerced = {}
    for name, value in fields.items():
        try:
            num = _coerce_numeric(value, default=None)
        except TypeError as e:
            raise ValueError(f"Form 8606 input {name!r} must be numeric, got bool.") from e
        if num is None:
            raise ValueError(f"Form 8606 input {name!r} is not numeric: {value!r}")
        if num < 0:
            raise ValueError(f"Form 8606 input {name!r} cannot be negative (got {num}).")
        coerced[name] = num

    line_1 = coerced["nondeductible_contrib_current"]
    line_2 = coerced["prior_basis"]
    line_4 = coerced["contrib_made_by_deadline"]
    line_6 = coerced["year_end_value"]
    line_7 = coerced["distributions_not_converted"]
    line_8 = coerced["amount_converted"]
    line_15b = coerced["disaster_repayable_amount"]

    # Line 4 is, per Form 8606, the portion of line 1 made Jan 1-Apr 15 of the next year
    # — definitionally a subset of line 1. Allowing line_4 > line_1 silently shrinks the
    # numerator (line 5 = line 3 - line 4) and over-taxes the conversion. Surface the
    # data error like the other strict input checks rather than auto-clamping.
    if line_4 > line_1:
        raise ValueError(
            f"Form 8606 contrib_made_by_deadline ({line_4}) cannot exceed "
            f"nondeductible_contrib_current ({line_1}) — line 4 is a subset of line 1."
        )

    line_3 = line_1 + line_2
    line_5 = line_3 - line_4
    line_9 = line_6 + line_7 + line_8

    # Line 10: basis ratio, capped at 1.0; 0.0 when nothing was distributed and no
    # balance remains (line 9 == 0). Kept at FULL float precision for the dollar
    # lines below — the IRS "round to >=3 places" rule injects error, and tax
    # software (which /tax-verify cross-references) carries full precision. Only
    # the reported `ratio` is rounded, for display.
    ratio = 0.0 if line_9 == 0 else min(line_5 / line_9, 1.0)

    line_11 = line_8 * ratio   # nontaxable portion of conversion
    line_12 = line_7 * ratio   # nontaxable portion of distributions
    line_13 = line_11 + line_12
    line_14 = line_3 - line_13          # basis remaining (carryforward)
    line_15a = line_7 - line_12         # taxable distributions, before disaster carve-out
    # Line 15c = 15a − 15b: only the non-disaster taxable portion flows to 1040 4b. The
    # disaster-allocable amount is reported separately on Form 8915-F and amortized over
    # 3 years (not modeled by this toolkit; 15b ≥ 15a is clamped to 15c = 0 defensively).
    line_15c = max(0.0, line_15a - line_15b)
    line_18 = line_8 - line_11          # taxable conversion

    # The trap = a conversion split into BOTH a tax-free (basis) and a taxable (pre-tax)
    # portion. Detect it from the two portions directly, NOT from year-end FMV: a
    # FULL-balance conversion leaves FMV $0 yet is still pro-rated (e.g. $7k basis in a
    # $57k IRA, all converted → $50k taxable). Use the rounded line values so float dust
    # near ratio==1.0 (a clean backdoor) cannot spuriously trip the flag.
    pro_rata_triggered = round(line_11, 2) > 0 and round(line_18, 2) > 0
    # Form 15b carve-out doesn't affect the trap flag — basis still pro-rates across the
    # full line 7, including the disaster portion (which consumes basis on line 12).

    return {
        "line_1_nondeductible_current": round(line_1, 2),
        "line_2_prior_basis": round(line_2, 2),
        "line_3_total_basis": round(line_3, 2),
        "line_5_basis_net_of_deadline": round(line_5, 2),
        "line_6_year_end_value": round(line_6, 2),
        "line_7_distributions": round(line_7, 2),
        "line_8_converted": round(line_8, 2),
        "line_9_total_value": round(line_9, 2),
        "line_10_ratio": round(ratio, 5),
        "line_11_nontaxable_conversion": round(line_11, 2),
        "line_12_nontaxable_distributions": round(line_12, 2),
        "line_13_nontaxable_total": round(line_13, 2),
        "line_14_basis_remaining": round(line_14, 2),
        # 15a = pre-disaster-carve-out (line 7 - line 12); 15b = disaster-allocable
        # (input, reported on Form 8915-F); 15c = 15a - 15b, the amount flowing to 1040 4b.
        "line_15a_taxable_pre_disaster": round(line_15a, 2),
        "line_15b_disaster_repayable": round(line_15b, 2),
        "line_15c_taxable_to_1040": round(line_15c, 2),
        # Backward-compat alias: line_15_taxable_distributions now means line 15c (the
        # 1040-bound amount), so callers without disaster distributions see no change.
        "line_15_taxable_distributions": round(line_15c, 2),
        "line_18_taxable_conversion": round(line_18, 2),
        "ratio": round(ratio, 5),
        "basis_remaining": round(line_14, 2),
        "taxable_conversion": round(line_18, 2),
        "taxable_distributions": round(line_15c, 2),
        "taxable_to_1040": round(line_15c + line_18, 2),
        "pro_rata_triggered": pro_rata_triggered,
    }


def compute_ftc_limitation(
    foreign_source_income: float,
    worldwide_income: float,
    us_tax: float,
    foreign_tax_paid: float,
) -> dict:
    """Compute Foreign Tax Credit limitation (Form 1116).

    Args:
        foreign_source_income: Total foreign source income.
        worldwide_income: Total worldwide income (AGI).
        us_tax: US tax before FTC.
        foreign_tax_paid: Total foreign taxes paid in USD.

    Returns:
        Dict with FTC limitation details.

    Raises:
        ValueError: If any parameter is None or negative (where applicable).
    """
    if foreign_source_income is None:
        raise ValueError("foreign_source_income cannot be None. Check Form 1116 Part I.")
    if worldwide_income is None:
        raise ValueError("worldwide_income cannot be None. Check Form 1040 line 11 (AGI).")
    if us_tax is None:
        raise ValueError("us_tax cannot be None. Compute federal tax before FTC limitation.")
    if us_tax < 0:
        raise ValueError(f"us_tax cannot be negative (got {us_tax}). Check federal tax computation.")
    if foreign_tax_paid is None:
        raise ValueError("foreign_tax_paid cannot be None. Check Form 1116 foreign tax amounts.")
    if foreign_tax_paid < 0:
        raise ValueError(f"foreign_tax_paid cannot be negative (got {foreign_tax_paid}).")

    if worldwide_income <= 0:
        limitation = 0.0
    else:
        # FTC limitation ratio: capped at 1.0 (upper) and 0.0 (lower, net foreign loss)
        ratio = min(1.0, max(0.0, foreign_source_income / worldwide_income))
        limitation = ratio * us_tax

    ftc_allowed = min(foreign_tax_paid, limitation)

    return {
        "foreign_source_income": round(foreign_source_income, 2),
        "worldwide_income": round(worldwide_income, 2),
        "us_tax": round(us_tax, 2),
        "foreign_tax_paid": round(foreign_tax_paid, 2),
        "limitation": round(limitation, 2),
        "ftc_allowed": round(ftc_allowed, 2),
        "limited_by": "tax_paid" if foreign_tax_paid <= limitation else "limitation",
    }


def compute_child_tax_credit(
    num_qualifying_children: int,
    credit_per_child: float,
    agi: float,
    phase_out_start: float,
    phase_out_rate: float = 50,
    num_other_dependents: int = 0,
    odc_per_dependent: float = ODC_PER_DEPENDENT,
    tax_liability: float = None,
    earned_income: float = None,
    actc_limit_per_child: float = None,
) -> dict:
    """Compute Child Tax Credit + Credit for Other Dependents + refundable ACTC
    (Schedule 8812).

    The phase-out reduces the COMBINED CTC+ODC (Schedule 8812 lines 8-12). When
    `tax_liability` is provided, the credit is split into the nonrefundable
    portion (line 14: lesser of the credit and tax) and the refundable Additional
    Child Tax Credit (Part II-A): the unused credit, capped at
    `actc_limit_per_child` per qualifying child (line 16b — this cap also
    excludes the never-refundable ODC) and at 15% of earned income over $2,500
    (line 19). The 3+-children Social-Security-tax alternative (line 27
    worksheet), which can only INCREASE the ACTC, is not modeled — flagged when
    it could apply, so the refundable figure may be understated (safe direction).

    Args:
        num_qualifying_children: Number of qualifying children under 17.
        credit_per_child: CTC amount per child (tax_law child_tax_credit.amount_per_child).
        agi: Adjusted Gross Income.
        phase_out_start: Phase-out threshold for filing status.
        phase_out_rate: Reduction per $1,000 over threshold (default $50).
        num_other_dependents: Dependents not qualifying for CTC (17+, qualifying
            relatives) — each earns the $500 nonrefundable ODC.
        odc_per_dependent: ODC amount per dependent (statutory $500).
        tax_liability: The Credit Limit Worksheet A amount — Form 1040 line 18
            tax MINUS the nonrefundable credits claimed before the CTC
            (Schedule 3 part I: FTC, dependent care, education, energy, etc.).
            NOT raw line 18: passing that lets the CTC consume tax already used
            by earlier credits, overstating line 19 and suppressing the ACTC.
            None (default) skips the refundable/nonrefundable split — legacy
            potential-credit behavior.
        earned_income: Wages + SE earnings for the ACTC 15% formula. Required
            when tax_liability is provided (pass 0 if none).
        actc_limit_per_child: Refundable cap per qualifying child (tax_law
            child_tax_credit.refundable_limit, indexed — $1,700 TY2024/2025).
            Required when tax_liability is provided and there are qualifying
            children.

    Returns:
        Dict with the combined computation. `credit_allowed` stays the
        post-phase-out potential credit (pre-tax-liability), matching the legacy
        contract used by compute_scenario/projection; the split keys
        (`nonrefundable_used`, `refundable_actc`) are None unless tax_liability
        is given.

    Raises:
        ValueError: If any parameter is None where required, non-finite, or invalid.
    """
    if num_qualifying_children is None:
        raise ValueError("num_qualifying_children cannot be None. Check dependent data.")
    if agi is None:
        raise ValueError("agi cannot be None. Check Form 1040 line 11.")
    if credit_per_child is None:
        raise ValueError(
            "credit_per_child cannot be None. "
            "Load from tax_law JSON federal.child_tax_credit.amount_per_child."
        )
    if phase_out_start is None:
        raise ValueError(
            "phase_out_start cannot be None. "
            "Load from tax_law JSON federal.child_tax_credit.phase_out_start."
        )
    if num_qualifying_children < 0:
        raise ValueError(
            f"num_qualifying_children cannot be negative (got {num_qualifying_children}). "
            "Check source data for number of qualifying children."
        )
    if num_other_dependents is None or num_other_dependents < 0:
        raise ValueError(
            f"num_other_dependents must be a non-negative count (got {num_other_dependents})."
        )
    if odc_per_dependent is None or odc_per_dependent < 0:
        raise ValueError(f"odc_per_dependent must be non-negative (got {odc_per_dependent}).")

    full_credit = num_qualifying_children * credit_per_child
    odc_full = num_other_dependents * odc_per_dependent
    combined_full = full_credit + odc_full

    if agi > phase_out_start:
        excess = agi - phase_out_start
        # IRS: "$50 for each $1,000 (or fraction thereof)" — use ceiling, not floor
        reduction = math.ceil(excess / 1000) * phase_out_rate
        credit = max(0, combined_full - reduction)
    else:
        reduction = 0
        credit = combined_full

    nonrefundable_used = None
    refundable_actc = None
    ss_alternative_flag = False
    if tax_liability is not None:
        try:
            tl = _coerce_numeric(tax_liability, default=None)
            ei = _coerce_numeric(earned_income, default=None) if earned_income is not None else None
        except TypeError:
            raise ValueError("tax_liability/earned_income must be numeric, got bool")
        if tl is None or not math.isfinite(tl) or tl < 0:
            raise ValueError(f"tax_liability must be a finite non-negative number (got {tax_liability!r}).")
        if ei is None or not math.isfinite(ei) or ei < 0:
            raise ValueError(
                "earned_income is required for the ACTC split (pass 0 if none) "
                f"and must be finite non-negative (got {earned_income!r})."
            )
        nonrefundable_used = min(credit, tl)
        unused = credit - nonrefundable_used
        if num_qualifying_children > 0 and unused > 0:
            if actc_limit_per_child is None:
                raise ValueError(
                    "actc_limit_per_child cannot be None when computing the refundable "
                    "ACTC. Load from tax_law JSON federal.child_tax_credit.refundable_limit."
                )
            # The cap comes from the fetched cache — validate like every other
            # split input: json.loads accepts NaN (bypasses the per-child cap)
            # and a negative value would produce a negative refund.
            try:
                cap_per_child = _coerce_numeric(actc_limit_per_child, default=None)
            except TypeError:
                raise ValueError("actc_limit_per_child must be numeric, got bool")
            if cap_per_child is None or not math.isfinite(cap_per_child) or cap_per_child < 0:
                raise ValueError(
                    "actc_limit_per_child must be a finite non-negative number "
                    f"(got {actc_limit_per_child!r}). Check tax_law JSON "
                    "federal.child_tax_credit.refundable_limit."
                )
            refundable_cap = num_qualifying_children * cap_per_child
            ei_limit = ACTC_EARNED_INCOME_RATE * max(0.0, ei - ACTC_EARNED_INCOME_FLOOR)
            potential = min(unused, refundable_cap)
            refundable_actc = min(potential, ei_limit)
            # With 3+ children the SS-tax alternative can lift the ACTC above the
            # 15% earned-income formula — not modeled, may understate.
            ss_alternative_flag = num_qualifying_children >= 3 and ei_limit < potential
        else:
            refundable_actc = 0.0

    return {
        "num_children": num_qualifying_children,
        "credit_per_child": credit_per_child,
        "full_credit": round(full_credit, 2),
        "num_other_dependents": num_other_dependents,
        "odc_full": round(odc_full, 2),
        "combined_full": round(combined_full, 2),
        "agi": round(agi, 2),
        "phase_out_start": phase_out_start,
        "phase_out_reduction": round(reduction, 2),
        "credit_allowed": round(credit, 2),
        "nonrefundable_used": round(nonrefundable_used, 2) if nonrefundable_used is not None else None,
        "refundable_actc": round(refundable_actc, 2) if refundable_actc is not None else None,
        "actc_3plus_ss_alternative_not_modeled": ss_alternative_flag,
    }


def net_capital_gains(net_st: float, net_lt: float) -> "tuple[float, float]":
    """Net Schedule D short- and long-term results, preserving rate character.

    Returns (capital_gains_input, ordinary_short_term_gain):
    - net loss overall → (combined_loss, 0): routed through capital_gains so the
      annual loss cap applies (a loss is never ordinary uncapped income).
    - both gains → (net_lt, net_st): LT preferential, ST ordinary.
    - mixed signs, net gain → the surviving gain keeps the character of the larger
      side: ST-gain-survives → (0, combined); LT-gain-survives → (combined, 0).

    Shared by scenario.py (baseline extraction), loss_harvest.py (post-harvest
    netting), and projection.py (ST/LT forecast split) so every consumer applies
    identical character preservation.
    """
    combined = net_st + net_lt
    if combined <= 0:
        return combined, 0.0
    if net_st >= 0 and net_lt >= 0:
        return net_lt, net_st
    if net_lt < 0:  # long-term loss absorbed by short-term gain → surviving gain is ST
        return 0.0, combined
    return combined, 0.0  # short-term loss absorbed by long-term gain → surviving gain is LT


_QBI_MINIMUM_FROM = 2026  # §70105 minimum QBI deduction, OBBBA — TY2026+


def compute_qbi_deduction(
    qbi_income: float,
    reit_dividends: float,
    rate: float = 0.20,
    taxable_income_before_qbi: float = None,
    net_capital_gain: float = None,
    threshold: float = None,
    tax_year: int = None,
    qbi_minimum: dict = None,
    active_qualified_trade: bool = False,
    half_se_tax: float = 0.0,
    se_health_insurance: float = 0.0,
    se_retirement: float = 0.0,
) -> dict:
    """Compute QBI deduction (Form 8995 simplified).

    Applies the Form 8995 OVERALL cap (lines 11-14): the deduction cannot exceed
    rate x (taxable income before QBI - net capital gain). This cap is on the
    simplified 8995 itself, not just 8995-A — omitting it overstates the deduction
    (understates tax) whenever business income is large relative to taxable income
    (retiree with a side business, loss-heavy year, large itemized/standard
    deduction).

    QBI base netting (§199A(c)(4) / Reg. §1.199A-3(b)(1)(vi) / Form 8995 line 1):
    qualified business income is REDUCED by the deductions attributable to the
    trade or business — the deductible half of SE tax, the SE health-insurance
    deduction, and SE retirement-plan (SEP/SIMPLE/qualified) contributions. These
    are the `half_se_tax` / `se_health_insurance` / `se_retirement` args (each the
    portion attributable to the qualifying trade); their sum is subtracted from
    `qbi_income` BEFORE the 20% is taken. Omitting them overstates QBI and the
    deduction (understates tax). They default to 0 for backward compatibility, so
    a caller that already nets QBI, or has no SE activity, is unaffected.

    Args:
        qbi_income: Qualified business income (can be negative = loss), BEFORE the
            §1.199A-3(b)(1)(vi) SE-deduction netting (pass the SE deductions
            separately via the three args below; do not pre-net them here).
        reit_dividends: Section 199A REIT dividends.
        rate: QBI deduction rate (default 20%).
        half_se_tax: Deductible half of SE tax attributable to the trade
            (Schedule 1 line 15 portion). Reduces QBI. Non-negative.
        se_health_insurance: SE health-insurance deduction attributable to the
            trade (Schedule 1 line 17 portion). Reduces QBI. Non-negative.
        se_retirement: SE retirement-plan (SEP/SIMPLE/qualified) contribution
            deduction attributable to the trade (Schedule 1 line 16 portion).
            Reduces QBI. Non-negative.
        taxable_income_before_qbi: Taxable income computed WITHOUT the QBI
            deduction (AGI - deductions, floored at 0). When None the overall cap
            is SKIPPED (legacy behavior) and `income_cap_applied` is False — the
            result may overstate the deduction, so callers should pass it.
        net_capital_gain: Form 8995 line 12 — qualified dividends + net LTCG
            (never negative). Defaults to 0 when omitted while the cap is active.
        threshold: Section 199A taxable-income threshold for the filing status
            (tax_law federal.qbi_threshold, keyed single/mfj). Above it the
            W-2-wage/UBIA and SSTB limits of Form 8995-A apply, which this
            simplified function does NOT model — the result is flagged
            `over_threshold` and may OVERSTATE the deduction (an SSTB should
            phase to zero). None skips the check (`over_threshold` = None).

    Returns:
        Dict with QBI deduction, the cap, and the over-threshold flag.

    Raises:
        ValueError: If any parameter is None where required, non-finite, or invalid.
    """
    if qbi_income is None:
        raise ValueError("qbi_income cannot be None. Check Schedule C / Form 8995.")
    if reit_dividends is None:
        raise ValueError("reit_dividends cannot be None. Pass 0 if no REIT dividends. Check 1099-DIV.")
    if rate is None:
        raise ValueError("QBI deduction rate not loaded from tax_law JSON.")
    if reit_dividends < 0:
        raise ValueError(
            f"reit_dividends cannot be negative (got {reit_dividends}). "
            "REIT dividends from 1099-DIV are always non-negative."
        )

    # §199A(c)(4) / Reg. §1.199A-3(b)(1)(vi): net QBI down by the deductions
    # attributable to the trade (½ SE tax, SE health insurance, SE retirement).
    # Each must be a finite, non-negative number (they are deductions — they can
    # only REDUCE QBI). In practice these stay below QBI (SEHI/retirement are
    # limited to net SE earnings), but the netting is left natural: if they were
    # to exceed QBI the excess becomes a qualified-business-loss carryforward.
    se_reductions = 0.0
    for _name, _val in (("half_se_tax", half_se_tax),
                        ("se_health_insurance", se_health_insurance),
                        ("se_retirement", se_retirement)):
        if isinstance(_val, bool):
            raise ValueError(
                f"{_name} must be a finite number, got {_val!r}")
        _v = _coerce_numeric(_val, default=None)
        if _v is None or not math.isfinite(_v):
            raise ValueError(
                f"{_name} must be a finite number, got {_val!r}")
        if _v < 0:
            raise ValueError(
                f"{_name} cannot be negative (got {_v}) — it is a deduction that "
                "reduces QBI per Reg. §1.199A-3(b)(1)(vi).")
        se_reductions += _v
    se_reductions = round(se_reductions, 2)
    qbi_income_net = qbi_income - se_reductions

    qbi_component = max(0, qbi_income_net * rate)
    reit_component = round(reit_dividends * rate, 2)
    total_deduction = qbi_component + reit_component
    loss_carryforward = min(0, qbi_income_net)

    income_cap = None
    income_limited = False
    if taxable_income_before_qbi is not None:
        ti = _coerce_numeric(taxable_income_before_qbi, default=None)
        ncg = _coerce_numeric(net_capital_gain if net_capital_gain is not None else 0, default=None)
        if ti is None or ncg is None or not math.isfinite(ti) or not math.isfinite(ncg):
            raise ValueError(
                "taxable_income_before_qbi and net_capital_gain must be finite numbers"
            )
        if ti < 0 or ncg < 0:
            raise ValueError(
                "taxable_income_before_qbi and net_capital_gain cannot be negative "
                f"(got {ti}, {ncg}) — both are floored at 0 upstream."
            )
        # Form 8995 line 13 = taxable income - net capital gain; line 14 = 20% of that
        income_cap = max(0.0, (ti - ncg) * rate)
        if total_deduction > income_cap:
            total_deduction = income_cap
            income_limited = True

    # §70105 minimum QBI deduction (OBBBA, TY2026+): a taxpayer whose ACTIVE
    # qualified-trade QBI is at/above the income threshold gets AT LEAST the
    # indexed minimum, even if 20% of QBI (post-cap) is smaller. Taxpayer-
    # favorable, so it DEGRADES (no floor + note) when the cache is unfetched —
    # an indexed figure is never guessed. Requires the active-trade attestation
    # (a passive/portfolio activity does not qualify).
    if not isinstance(active_qualified_trade, bool):
        raise ValueError(
            f"active_qualified_trade must be a bool, got {active_qualified_trade!r}")
    qbi_minimum_applied = False
    qbi_minimum_note = None
    if (isinstance(tax_year, int) and not isinstance(tax_year, bool)
            and tax_year >= _QBI_MINIMUM_FROM
            and active_qualified_trade and qbi_income_net > 0):
        if isinstance(qbi_minimum, dict) \
                and qbi_minimum.get("income_threshold") is not None \
                and qbi_minimum.get("amount") is not None:
            inc_thr = _sched_a_amount(
                qbi_minimum["income_threshold"], "qbi_minimum.income_threshold")
            floor = _sched_a_amount(qbi_minimum["amount"], "qbi_minimum.amount")
            if qbi_income_net >= inc_thr and total_deduction < floor:
                # The floor lifts the QBI amount, but the §199A(a)(2) OVERALL
                # ceiling (20% of taxable income − net capital gain) still binds
                # — the minimum cannot exceed the Form 8995 overall cap. Re-cap
                # so a taxpayer with little ordinary taxable-income headroom
                # (e.g. mostly preferential income) is not handed a deduction
                # larger than the ceiling.
                lifted = floor if income_cap is None else min(floor, income_cap)
                if lifted > total_deduction:
                    total_deduction = lifted
                    qbi_minimum_applied = True
        else:
            qbi_minimum_note = (
                "§70105 minimum QBI deduction (TY2026+) NOT applied — "
                "federal.qbi_minimum is unfetched; the deduction may be "
                "understated for a small active qualified trade. Fetch the cache "
                "to enable the minimum.")

    over_threshold = None
    if threshold is not None:
        thr = _coerce_numeric(threshold, default=None)
        if thr is None or not math.isfinite(thr):
            raise ValueError("threshold must be a finite number when provided")
        if taxable_income_before_qbi is None:
            raise ValueError(
                "threshold check requires taxable_income_before_qbi — pass both."
            )
        over_threshold = ti > thr

    result = {
        "qbi_income": round(qbi_income_net, 2),
        "qbi_income_before_se_deductions": round(qbi_income, 2),
        "se_deductions_attributable": se_reductions,
        "qbi_component": round(qbi_component, 2),
        "reit_dividends": round(reit_dividends, 2),
        "reit_component": round(reit_component, 2),
        "total_deduction": round(total_deduction, 2),
        "loss_carryforward": round(loss_carryforward, 2),
        "income_cap_applied": taxable_income_before_qbi is not None,
        "income_cap": round(income_cap, 2) if income_cap is not None else None,
        "income_limited": income_limited,
        "over_threshold": over_threshold,
        "qbi_minimum_applied": qbi_minimum_applied,
    }
    if qbi_minimum_note:
        result["qbi_minimum_note"] = qbi_minimum_note
    if over_threshold:
        result["warning"] = (
            "Taxable income exceeds the Section 199A threshold: Form 8995-A "
            "W-2-wage/UBIA limits and the SSTB phase-out apply but are NOT modeled "
            "here — the deduction may be overstated (an SSTB phases to $0). "
            "Verify against Form 8995-A."
        )
    return result


# Social Security Benefits Worksheet base amounts (IRC §86) — statutory-fixed
# and UNINDEXED since 1984/1993, so they are structural constants, not tax_law
# cache fields. Keyed by treatment: MFS-lived-with-spouse has a $0 base and its
# own 85% shortcut path.
_SS_BASE_AMOUNT = {"single": 25000.0, "mfj": 32000.0}
_SS_SECOND_THRESHOLD = {"single": 34000.0, "mfj": 44000.0}

_CANONICAL_FILING_STATUSES = {"SINGLE", "MFJ", "MFS", "HOH", "QSS"}


def _normalize_filing_status(filing_status) -> str:
    """Canonical filing-status validation (repo VALUE-validation rule):
    'Married Filing Jointly' or '' must hard-error, not fall through to a
    Single-shaped default."""
    if not isinstance(filing_status, str):
        raise ValueError(f"filing_status must be a string, got {type(filing_status).__name__}")
    fs = filing_status.strip().upper()
    if fs not in _CANONICAL_FILING_STATUSES:
        raise ValueError(
            f"filing_status must be one of Single/MFJ/MFS/HoH/QSS, got {filing_status!r}"
        )
    return fs


def compute_taxable_ss(
    ss_benefits: float,
    other_income: float,
    tax_exempt_interest: float = None,
    adjustments: float = 0.0,
    filing_status: str = None,
    lived_apart_from_spouse_all_year: bool = None,
    lump_sum_prior_year_benefits: float = 0.0,
    excluded_income_addbacks: float = 0.0,
) -> dict:
    """Taxable Social Security benefits — the 1040 line 6b worksheet (Pub 915).

    Args:
        ss_benefits: Total benefits, SSA-1099 box 5 (both spouses combined on
            a joint return). Negative net benefits → $0 taxable.
        other_income: All other income that lands on the 1040 EXCEPT the SS
            benefits themselves (lines 1z, 2b, 3b, 4b, 5b, 7, 8).
        tax_exempt_interest: Line 2a — counts toward provisional income even
            though it is not taxed (the classic muni-bond surprise). REQUIRED:
            pass an explicit 0 when there is no line 2a amount — a silent 0
            default would let an accidental omission understate line 6b for
            exactly the taxpayers the input exists for.
        adjustments: Schedule 1 above-the-line adjustments (worksheet line 5).
        filing_status: Single/MFJ/MFS/HoH/QSS (case-insensitive; value-
            validated). REQUIRED — no default: silently assuming MFJ would
            report $0 taxable for a Single/HoH/QSS filer with provisional
            income between the $25K and $32K bases (fail closed, like every
            other load-bearing tax fact).
        excluded_income_addbacks: Pub 915 Worksheet 1 line-5-region add-backs
            that re-enter provisional income even though excluded from the
            1040: Form 2555 foreign earned income/housing exclusions, excluded
            adoption benefits (Form 8839), Form 8815 excluded Series EE/I
            savings-bond interest (the worksheet uses Schedule B line 2, not
            the post-exclusion 1040 line 2b), and excluded U.S.-territory
            income. Omitting these for an affected taxpayer UNDERSTATES
            line 6b.
        lived_apart_from_spouse_all_year: REQUIRED for MFS (hard error when
            None) — it selects between the Single-style worksheet (lived apart
            all year) and the $0-base 85% path (lived together at any point).
            Same load-bearing-flag rule as the retirement dashboard.
        lump_sum_prior_year_benefits: The portion of box 5 paid FOR AN EARLIER
            year (SSA-1099 "paid in {year} for {prior year}" boxes). The Pub
            915 lump-sum election (Worksheets 2-4, the line 6c checkbox) can
            only LOWER taxable benefits and is NOT modeled — when this is > 0
            the result carries `lump_sum_election_available` + a warning that
            the regular-worksheet figure is an UPPER BOUND to verify in tax
            software. The amount is already inside ss_benefits; it changes
            nothing numerically here.

    Returns:
        Dict with the worksheet lines: provisional_income, base_amount,
        taxable_ss (→ 1040 line 6b), taxable_fraction, and the path taken.

    Raises:
        ValueError: On non-finite/negative-where-invalid inputs, a
            non-canonical filing status, or MFS without the lived-apart flag.
    """
    if tax_exempt_interest is None:
        raise ValueError(
            "tax_exempt_interest is required — pass 1040 line 2a, or an "
            "explicit 0 when there is none. Munis raise provisional income "
            "even though untaxed; a silent default would understate line 6b."
        )
    try:
        benefits = _coerce_numeric(ss_benefits, default=None)
        other = _coerce_numeric(other_income, default=None)
        tei = _coerce_numeric(tax_exempt_interest, default=None)
        adj = _coerce_numeric(adjustments, default=None)
        lump = _coerce_numeric(lump_sum_prior_year_benefits, default=None)
        addbacks = _coerce_numeric(excluded_income_addbacks, default=None)
    except TypeError:
        raise ValueError("compute_taxable_ss inputs must be numeric, got bool")
    if None in (benefits, other, tei, adj, lump, addbacks) or not all(
        math.isfinite(x) for x in (benefits, other, tei, adj, lump, addbacks)
    ):
        raise ValueError("compute_taxable_ss inputs must be finite numbers")
    if tei < 0 or adj < 0 or lump < 0 or addbacks < 0:
        raise ValueError(
            "tax_exempt_interest, adjustments, lump_sum_prior_year_benefits, "
            "and excluded_income_addbacks cannot be negative"
        )
    if filing_status is None:
        raise ValueError(
            "filing_status is required (Single/MFJ/MFS/HoH/QSS) — silently "
            "defaulting would apply the wrong base amount and can report $0 "
            "taxable for a Single filer between the $25K and $32K bases."
        )

    def _with_lump_sum_flag(result: dict) -> dict:
        result["lump_sum_election_available"] = lump > 0
        if lump > 0:
            result["warning"] = (
                "Box 5 includes benefits paid for an earlier year: the Pub 915 "
                "lump-sum election (Worksheets 2-4, line 6c) can only LOWER "
                "taxable benefits and is NOT modeled — this regular-worksheet "
                "figure is an UPPER BOUND. Verify the election in tax software "
                "before filing."
            )
        return result

    fs = _normalize_filing_status(filing_status)
    if fs == "MFS":
        if lived_apart_from_spouse_all_year is None:
            raise ValueError(
                "MFS requires lived_apart_from_spouse_all_year (true/false) — "
                "it decides between the Single-style worksheet and the "
                "$0-base 85% path. Set flags.lived_apart_from_spouse_all_year."
            )
        if not isinstance(lived_apart_from_spouse_all_year, bool):
            raise ValueError("lived_apart_from_spouse_all_year must be a bool")

    if benefits <= 0:
        return _with_lump_sum_flag({
            "ss_benefits": round(max(benefits, 0.0), 2), "provisional_income": 0.0,
            "base_amount": None, "taxable_ss": 0.0, "taxable_fraction": 0.0,
            "path": "no_benefits",
        })

    half_benefits = benefits / 2.0
    provisional = half_benefits + other + tei + addbacks - adj

    if fs == "MFS" and lived_apart_from_spouse_all_year is False:
        # Lived with spouse at any point: base is $0 — taxable is the lesser
        # of 85% of provisional income and 85% of benefits.
        taxable = max(0.0, min(0.85 * provisional, 0.85 * benefits))
        return _with_lump_sum_flag({
            "ss_benefits": round(benefits, 2),
            "provisional_income": round(provisional, 2),
            "base_amount": 0.0,
            "taxable_ss": round(taxable, 2),
            "taxable_fraction": round(taxable / benefits, 4),
            "path": "mfs_lived_together_85pct",
        })

    # Pub 915 groups QSS with Single/HoH at the $25,000 base and $9,000 tier
    # span — ONLY MFJ gets $32,000/$12,000 (and MFS-lived-apart also uses the
    # single base). QSS-as-joint here would wrongly report $0 taxable for
    # provisional income between $25K and $32K.
    key = "mfj" if fs == "MFJ" else "single"
    base = _SS_BASE_AMOUNT[key]
    second = _SS_SECOND_THRESHOLD[key]

    if provisional <= base:
        taxable = 0.0
    else:
        excess = provisional - base  # worksheet line 8
        tier1_span = second - base  # line 9 ($9,000 / $12,000)
        over_second = max(0.0, excess - tier1_span)  # line 10
        tier1 = min(excess, tier1_span)  # line 11
        taxable = min(
            min(half_benefits, tier1 / 2.0) + 0.85 * over_second,  # line 15
            0.85 * benefits,  # line 16 cap
        )
    return _with_lump_sum_flag({
        "ss_benefits": round(benefits, 2),
        "provisional_income": round(provisional, 2),
        "base_amount": base,
        "taxable_ss": round(taxable, 2),
        "taxable_fraction": round(taxable / benefits, 4),
        "path": "worksheet",
    })


def _multi_employer_excess(
    withheld: "list[float]",
    wages: "list[float]",
    rate: float,
    wage_cap: float,
) -> "tuple[float, float]":
    """Split withholding above a per-person maximum into (return_credit,
    employer_error_amount).

    IRS Topic 608 (and EDD DE 1964 for CA SDI): anything an employer withheld
    beyond what that employer's SUBJECT WAGES support — min(wages, cap) × rate
    — is an employer error, recovered from the employer (or Form 843), NEVER
    as a credit on the return. Withholding alone cannot establish the credit:
    two below-base W-2s with erroneous over-withholding can exceed the annual
    max without any creditable excess existing. Only the correctly-withheld
    portions count, and only their multi-employer total above the per-person
    max is creditable.
    """
    clamped_total = 0.0
    employer_error = 0.0
    for w_amt, wg in zip(withheld, wages):
        proper = min(wg, wage_cap) * rate  # what this employer SHOULD have withheld, at most
        allowed = min(w_amt, proper)
        clamped_total += allowed
        employer_error += w_amt - allowed
    per_person_max = round(wage_cap * rate, 2)
    credit = (max(0.0, round(clamped_total - per_person_max, 2))
              if len(withheld) >= 2 else 0.0)
    return credit, round(employer_error, 2)


def _validated_withholding_list(withheld_by_employer, what: str) -> "list[float]":
    if not isinstance(withheld_by_employer, list):
        raise ValueError(f"{what} must be a list of per-employer amounts")
    out = []
    for i, raw in enumerate(withheld_by_employer):
        try:
            v = _coerce_numeric(raw, default=None)
        except TypeError:
            raise ValueError(f"{what}[{i}] must be numeric, got bool")
        if v is None or not math.isfinite(v) or v < 0:
            raise ValueError(f"{what}[{i}] must be a finite non-negative number, got {raw!r}")
        out.append(v)
    return out


def compute_excess_ss_withholding(
    withheld_by_employer: list,
    ss_wages_by_employer: list,
    ss_wage_base: float,
    employee_rate: float = 0.062,
) -> dict:
    """Excess Social Security withholding credit (Schedule 3 line 11).

    PER PERSON: each spouse's employers are evaluated separately — never pool
    a couple's Box 4 amounts. The credit exists only for the MULTI-employer
    case, and per IRS Topic 608 the amount any INDIVIDUAL employer withheld
    beyond what that employer's SS wages support (min(Box 3, base) × 6.2%) is
    an employer error recovered from that employer (or Form 843) — reported as
    employer_error_amount, never credited. Wages are REQUIRED: withholding
    alone can exceed the annual max on below-base wages (employer rate error)
    with no creditable excess at all.

    NOT MODELED — Tier 1 RRTA: Topic 608 also credits excess tier 1 railroad
    retirement tax (and combined SS + tier 1 RRTA over the wage base) on
    Schedule 3 line 11. This function accepts only SS Box 4/Box 3+7 figures —
    a taxpayer with a railroad employer must compute the line 11 amount per
    Topic 608 by hand; feeding only the SS amounts here UNDERSTATES the credit
    for that (rare, out-of-persona) case.

    Args:
        withheld_by_employer: One SS-tax figure (W-2 Box 4) per employer for
            ONE person.
        ss_wages_by_employer: The matching SS wages per employer — W-2 **Box 3
            PLUS Box 7 (Social Security tips)**, since Box 4 withholding covers
            both — parallel to withheld_by_employer (lengths must match).
            Passing Box 3 alone misclassifies a tipped employee's correct
            withholding as employer error and understates the credit.
        ss_wage_base: The year's SS wage base (tax_law federal.social_security
            .wage_base) — hard error when None (unfetched cache must not
            silently compute a $0 maximum).
        employee_rate: Employee SS rate (0.062).

    Returns:
        Dict with max_ss_tax, total_withheld, excess_credit (Schedule 3 line
        11), employer_error_amount + employer_over_withheld (recover from the
        employer, not the return).
    """
    if ss_wage_base is None:
        raise ValueError(
            "ss_wage_base cannot be None. Load from tax_law JSON "
            "federal.social_security.wage_base."
        )
    try:
        base = _coerce_numeric(ss_wage_base, default=None)
        rate = _coerce_numeric(employee_rate, default=None)
    except TypeError:
        raise ValueError("ss_wage_base/employee_rate must be numeric, got bool")
    if None in (base, rate) or not all(math.isfinite(x) for x in (base, rate)) \
            or base <= 0 or rate <= 0:
        raise ValueError("ss_wage_base and employee_rate must be finite positive numbers")

    amounts = _validated_withholding_list(withheld_by_employer, "withheld_by_employer")
    wages = _validated_withholding_list(ss_wages_by_employer, "ss_wages_by_employer")
    if len(wages) != len(amounts):
        raise ValueError(
            f"ss_wages_by_employer must parallel withheld_by_employer "
            f"({len(amounts)} employers, got {len(wages)} wage figures)."
        )
    max_ss = round(base * rate, 2)
    total = round(sum(amounts), 2)
    credit, employer_error = _multi_employer_excess(amounts, wages, rate, base)
    return {
        "max_ss_tax": max_ss,
        "total_withheld": total,
        "num_employers": len(amounts),
        "excess_credit": credit,
        "employer_error_amount": employer_error,
        "employer_over_withheld": employer_error > 0,
    }


# SB 951 removed the CA SDI taxable wage ceiling starting TY2024 — from that
# year there is no per-person maximum and therefore no excess-SDI credit.
_SDI_NO_CEILING_FROM = 2024


def compute_excess_sdi(
    sdi_by_employer: list,
    sdi_wages_by_employer: list,
    sdi_rate: float,
    sdi_wage_limit: float,
    tax_year: int,
) -> dict:
    """CA excess-SDI/VPDI credit (Form 540 line 74) — per person, like the
    federal excess-SS credit.

    The tax year DISAMBIGUATES a None wage limit: for TY2024+ (SB 951) None
    is CORRECT — no ceiling exists, so the credit is not applicable. For
    pre-2024 years a ceiling DID exist, so a None limit means the cache is
    unfetched and the function HARD-ERRORS instead of silently reporting $0
    for a refundable credit the taxpayer is owed (fail closed, not fail-$0).

    Args:
        sdi_by_employer: One SDI/VPDI figure (W-2 Box 14) per employer for ONE
            person.
        sdi_wages_by_employer: The matching CA wages subject to SDI per
            employer, parallel to sdi_by_employer (lengths must match).
        sdi_rate: CA SDI rate (tax_law state.sdi_rate).
        sdi_wage_limit: SDI taxable wage ceiling (tax_law state.sdi_wage_limit);
            None is valid ONLY for tax_year >= 2024.
        tax_year: The return's tax year (4-digit int).

    Per EDD DE 1964, the amount any individual employer withheld beyond what
    that employer's SDI wages support (min(wages, limit) × rate) is recovered
    from that employer, not via the excess-SDI claim — the same wage-aware
    per-employer clamp as the federal excess-SS credit applies (erroneous
    over-withholding on below-limit wages is never creditable).

    Returns:
        Dict with max_sdi, total_withheld, excess_credit, not_applicable,
        employer_error_amount + employer_over_withheld.
    """
    if isinstance(tax_year, bool) or not isinstance(tax_year, int) \
            or not (2000 <= tax_year <= 2099):
        raise ValueError(f"tax_year must be a 4-digit year integer, got {tax_year!r}")
    amounts = _validated_withholding_list(sdi_by_employer, "sdi_by_employer")
    wages = _validated_withholding_list(sdi_wages_by_employer, "sdi_wages_by_employer")
    if len(wages) != len(amounts):
        raise ValueError(
            f"sdi_wages_by_employer must parallel sdi_by_employer "
            f"({len(amounts)} employers, got {len(wages)} wage figures)."
        )
    total = round(sum(amounts), 2)
    try:
        rate = _coerce_numeric(sdi_rate, default=None)
    except TypeError:
        raise ValueError("sdi_rate must be numeric, got bool")
    if rate is None or not math.isfinite(rate) or rate <= 0:
        raise ValueError("sdi_rate must be a finite positive number")

    # The YEAR decides applicability, not the cache: for TY2024+ SB 951 means
    # no ceiling regardless of what the cache carries — a stale pre-2024
    # sdi_wage_limit left in a newer cache must not invent a line 74 credit.
    if tax_year >= _SDI_NO_CEILING_FROM:
        # No ceiling ≠ no employer errors: each employer should still withhold
        # exactly wages × rate, and anything above that is recovered from the
        # employer — hiding it here would contradict the guidance in the note.
        employer_error = round(
            sum(max(0.0, a - wg * rate) for a, wg in zip(amounts, wages)), 2)
        note = ("No SDI wage ceiling (SB 951, TY2024+) — no excess-SDI "
                "credit exists; an employer that over-withheld must "
                "refund it directly.")
        if sdi_wage_limit is not None:
            note += (" A stale state.sdi_wage_limit in the tax_law cache was "
                     "IGNORED — computing against it would invent a credit; "
                     "clear the field for TY2024+.")
        return {
            "max_sdi": None, "total_withheld": total, "num_employers": len(amounts),
            "excess_credit": 0.0, "employer_error_amount": employer_error,
            "employer_over_withheld": employer_error > 0,
            "not_applicable": True,
            "note": note,
        }
    if sdi_wage_limit is None:
        raise ValueError(
            f"TY{tax_year} had an SDI wage ceiling, but state.sdi_wage_limit "
            "is missing from tax_law — fetch it. Treating a missing pre-2024 "
            "cap as 'no ceiling' would silently drop a refundable credit."
        )
    try:
        limit = _coerce_numeric(sdi_wage_limit, default=None)
    except TypeError:
        raise ValueError("sdi_wage_limit must be numeric, got bool")
    if limit is None or not math.isfinite(limit) or limit <= 0:
        raise ValueError("sdi_wage_limit must be a finite positive number")

    max_sdi = round(rate * limit, 2)
    credit, employer_error = _multi_employer_excess(amounts, wages, rate, limit)
    return {
        "max_sdi": max_sdi, "total_withheld": total, "num_employers": len(amounts),
        "excess_credit": credit,
        "employer_error_amount": employer_error,
        "employer_over_withheld": employer_error > 0,
        "not_applicable": False,
    }


# Schedule A structural statutory constants (none indexed):
# medical floor 7.5% of AGI (§213(a), permanent since 2021); charitable AGI
# caps — cash 60% (§170(b)(1)(G), made permanent by OBBBA), appreciated
# property 30%, overall 50% ordering per Pub 526; OBBBA §70425 0.5%-of-AGI
# charitable floor for itemizers from TY2026; acquisition-debt limits
# $750K post-TCJA / $1M for pre-Dec-16-2017 originations (§163(h)(3));
# the SALT cap dollar amount is INDEXED and comes from federal.salt_cap.
_SCHED_A_MEDICAL_FLOOR = 0.075
_CHARITY_CASH_AGI_CAP = 0.60
_CHARITY_APPRECIATED_AGI_CAP = 0.30
_CHARITY_OBBBA_FLOOR_RATE = 0.005
_CHARITY_OBBBA_FLOOR_FROM = 2026
_MORTGAGE_LIMIT_TCJA = 750000.0
_MORTGAGE_LIMIT_GRANDFATHERED = 1000000.0
# OBBBA §70111 (TY2026+): itemized deductions are reduced by 2/37 of the
# lesser of the itemized total or the income taxed in the 37% bracket.
_OBBBA_ITEMIZED_HAIRCUT = 2.0 / 37.0
_OBBBA_ITEMIZED_HAIRCUT_FROM = 2026
# CA Schedule CA: mortgage limit stays $1M (+$100K equity, unmodeled); cash
# charitable cap stays 50% (CA never conformed to the TCJA 60%); the
# high-AGI itemized phase-out reduces itemized by the LESSER of 6% of
# FEDERAL AGI over the (indexed, cached) threshold or 80% of the
# phase-out-subject deductions (R&TC §17077 — exempt: medical, investment
# interest, and — if ever modeled — casualty and gambling losses).
_CA_MORTGAGE_LIMIT = 1000000.0
_CA_CHARITY_CASH_AGI_CAP = 0.50
_CA_ITEMIZED_PHASEOUT_RATE = 0.06
_CA_ITEMIZED_PHASEOUT_MAX = 0.80


def _sched_a_amount(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {value!r}")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{what} must be finite, got {value!r}")
    if v < 0:
        raise ValueError(f"{what} cannot be negative, got {v}")
    return v


def _prorated_mortgage_interest(interest: float, avg_balance, limit: float,
                                notes: list, label: str) -> float:
    """Pub 936 simplified method: deductible interest = interest ×
    limit / average balance when the balance exceeds the limit."""
    if avg_balance is None:
        if interest > 0:
            notes.append(
                f"{label}: average mortgage balance not provided — interest "
                f"taken in full, ASSUMING acquisition debt ≤ ${limit:,.0f}; "
                "if the balance exceeds the limit this OVERSTATES the "
                "deduction (Pub 936 proration)")
        return interest
    bal = _sched_a_amount(avg_balance, "average_mortgage_balance")
    if bal <= limit or bal == 0:
        return interest
    notes.append(
        f"{label}: interest prorated × {limit:,.0f}/{bal:,.0f} per the "
        "Pub 936 average-balance method")
    return interest * limit / bal


def compute_schedule_a(
    agi: float,
    filing_status: str,
    tax_year: int,
    standard_deduction: float,
    salt_cap: float,
    medical_expenses: float = 0.0,
    state_local_income_taxes: float = 0.0,
    state_local_sales_taxes: float = 0.0,
    real_estate_taxes: float = 0.0,
    personal_property_taxes: float = 0.0,
    mortgage_interest: float = 0.0,
    mortgage_points: float = 0.0,
    average_mortgage_balance: float = None,
    mortgage_pre_dec_16_2017: bool = False,
    investment_interest: float = 0.0,
    charitable_cash: float = 0.0,
    charitable_noncash: float = 0.0,
    charitable_over_250_substantiated: bool = None,
    mfs_spouse_itemizes: bool = False,
    top_bracket_income: float = None,
) -> dict:
    """Federal Schedule A + the itemize-vs-standard election, computed.

    Args:
        salt_cap: the DOLLAR cap for this year/status from federal.salt_cap
            (amount, or mfs_amount for MFS) — never hardcoded here. The
            OBBBA MAGI phasedown above $500K is not modeled (caveated).
        medical_expenses: total unreimbursed — the 7.5% AGI floor is applied
            HERE, pass the gross figure.
        state_local_income_taxes: W-2 box 17 + CA SDI + estimated state
            payments made during the year + prior-year state balance paid.
        state_local_sales_taxes: the ELECTIVE alternative — the greater of
            income vs sales taxes is used (both can't be deducted).
        mortgage_interest/points: 1098 box 1 / deductible points.
        average_mortgage_balance: for the §163(h)(3) limit proration
            (Pub 936 simplified method). None → interest taken in full with
            a loud overstatement caveat.
        mortgage_pre_dec_16_2017: origination before Dec 16 2017 → $1M
            grandfathered limit instead of $750K.
        investment_interest: the ALLOWED amount after the Form 4952 net-
            investment-income limit — the limit itself is not modeled
            (caveated); pass the post-limit figure.
        charitable_cash/noncash: noncash is treated as appreciated property
            (30% AGI cap — conservative; basis-only gifts allow 50%).
        charitable_over_250_substantiated: None → unknown → warning; False →
            substantiation gate warning (deduction still computed — the gate
            is a preparer stop, not a disallowance).
        mfs_spouse_itemizes: MFS must itemize when the spouse itemizes,
            even if the standard deduction is larger (§63(c)(6)(A)).
        top_bracket_income: TY2026+ only — taxable income falling in the 37%
            bracket, for the OBBBA §70111 haircut (itemized reduced by 2/37
            of the lesser of the itemized total or this amount). The figure
            is circular with the deduction, so /tax-compute computes taxable
            income provisionally first and re-calls; None for a 2026+ year
            emits a loud not-applied note instead of silently overstating.

    Returns dict with per-line amounts, itemized_total, use_itemized,
    election margin, charitable carryforward, and notes.
    """
    fs = _normalize_filing_status(filing_status)
    if isinstance(tax_year, bool) or not isinstance(tax_year, int):
        raise ValueError(f"tax_year must be an int, got {tax_year!r}")
    if isinstance(agi, bool) or not isinstance(agi, (int, float)) \
            or not math.isfinite(float(agi)):
        raise ValueError(f"agi must be a finite number, got {agi!r}")
    # AGI may be negative (NOL-shaped returns). Every AGI-derived floor/cap
    # uses the FLOORED base — an unclamped negative AGI would turn the 7.5%
    # medical floor into a medical BONUS (med − negative = med + x).
    agi_v = max(0.0, float(agi))
    std = _sched_a_amount(standard_deduction, "standard_deduction")
    cap = _sched_a_amount(salt_cap, "salt_cap")
    med = _sched_a_amount(medical_expenses, "medical_expenses")
    inc_tax = _sched_a_amount(state_local_income_taxes, "state_local_income_taxes")
    sales_tax = _sched_a_amount(state_local_sales_taxes, "state_local_sales_taxes")
    re_tax = _sched_a_amount(real_estate_taxes, "real_estate_taxes")
    pp_tax = _sched_a_amount(personal_property_taxes, "personal_property_taxes")
    mort_int = _sched_a_amount(mortgage_interest, "mortgage_interest")
    points = _sched_a_amount(mortgage_points, "mortgage_points")
    inv_int = _sched_a_amount(investment_interest, "investment_interest")
    cash = _sched_a_amount(charitable_cash, "charitable_cash")
    noncash = _sched_a_amount(charitable_noncash, "charitable_noncash")
    for flag_name, flag in (("mortgage_pre_dec_16_2017", mortgage_pre_dec_16_2017),
                            ("mfs_spouse_itemizes", mfs_spouse_itemizes)):
        if not isinstance(flag, bool):
            raise ValueError(f"{flag_name} must be a boolean, got {flag!r} — "
                             "a truthy string would silently change the money")
    if charitable_over_250_substantiated is not None \
            and not isinstance(charitable_over_250_substantiated, bool):
        raise ValueError(
            "charitable_over_250_substantiated must be a boolean or None, "
            f"got {charitable_over_250_substantiated!r}")

    notes = [
        "OBBBA SALT-cap MAGI phasedown above $500K ($250K MFS) not modeled",
        "Form 4952 investment-interest limit not modeled — pass the "
        "post-limit allowed amount",
        "casualty/theft losses (federally-declared disasters only) not "
        "modeled",
        "single-loan mortgage model — mixed pre/post-Dec-16-2017 loans need "
        "the Pub 936 combined-limit worksheet",
    ]

    medical_deductible = round(max(0.0, med - _SCHED_A_MEDICAL_FLOOR * agi_v), 2)

    salt_before_cap = max(inc_tax, sales_tax) + re_tax + pp_tax
    if sales_tax > inc_tax and sales_tax > 0:
        notes.append("sales-tax election used (exceeds state income taxes)")
    salt_deductible = round(min(cap, salt_before_cap), 2)

    limit = (_MORTGAGE_LIMIT_GRANDFATHERED if mortgage_pre_dec_16_2017
             else _MORTGAGE_LIMIT_TCJA)
    if fs == "MFS":
        limit /= 2  # Pub 936: $375K post-TCJA / $500K grandfathered for MFS
    mortgage_deductible = round(
        _prorated_mortgage_interest(mort_int + points,
                                    average_mortgage_balance, limit, notes,
                                    "federal") , 2)

    # Charitable: cash first against the 60% cap, then appreciated noncash
    # against the LESSER of 30% AGI or remaining overall headroom (Pub 526
    # ordering). Excess carries forward 5 years.
    cash_cap = _CHARITY_CASH_AGI_CAP * agi_v
    cash_allowed = min(cash, cash_cap)
    noncash_cap = min(_CHARITY_APPRECIATED_AGI_CAP * agi_v,
                      max(0.0, 0.50 * agi_v - cash_allowed))
    noncash_allowed = min(noncash, noncash_cap)
    charity_floor = 0.0
    if tax_year >= _CHARITY_OBBBA_FLOOR_FROM and (cash_allowed + noncash_allowed) > 0:
        charity_floor = _CHARITY_OBBBA_FLOOR_RATE * agi_v
        notes.append(
            f"OBBBA §70425 charitable floor applied for TY{tax_year}: the "
            f"first 0.5% of AGI (${charity_floor:,.2f}) is not deductible")
    charitable_deductible = round(
        max(0.0, cash_allowed + noncash_allowed - charity_floor), 2)
    charitable_carryforward = round(
        (cash - cash_allowed) + (noncash - noncash_allowed), 2)
    if charitable_carryforward > 0:
        # §170(d)(1)(C) (OBBBA): when the percentage cap is exceeded, the
        # floor-disallowed slice ALSO carries forward (it isn't re-floored
        # next year). A floored amount under the cap is permanently lost.
        charitable_carryforward = round(charitable_carryforward + charity_floor, 2)
        notes.append(
            f"charitable contributions over the AGI caps: "
            f"${charitable_carryforward:,.2f} carries forward 5 years — "
            "enter it manually in next year's prior_year block "
            "(/tax-rollover lists it in the manual-carryforward reminder; "
            "it is not auto-tracked)")
    if cash > 0:
        notes.append(
            "assumes all donees are 50%-limit public charities — cash to a "
            "private non-operating foundation is capped at 30% of AGI "
            "(§170(b)(1)(B)), not modeled")
    if (cash + noncash) > 0:
        if charitable_over_250_substantiated is None:
            notes.append(
                "SUBSTANTIATION UNKNOWN: any single gift ≥ $250 requires a "
                "contemporaneous written acknowledgment (§170(f)(8)) — "
                "confirm before filing")
        elif charitable_over_250_substantiated is False:
            notes.append(
                "SUBSTANTIATION GATE: gifts ≥ $250 lack the written "
                "acknowledgment — the deduction is disallowed on audit; "
                "obtain the letters before filing")
    if noncash > 500:
        notes.append("noncash gifts over $500 require Form 8283 (not modeled)")

    itemized_total = round(medical_deductible + salt_deductible
                           + mortgage_deductible + inv_int
                           + charitable_deductible, 2)

    obbba_70111_reduction = 0.0
    if tax_year >= _OBBBA_ITEMIZED_HAIRCUT_FROM and itemized_total > 0:
        if top_bracket_income is None:
            notes.append(
                f"OBBBA §70111 (TY{tax_year}): itemized deductions are "
                "reduced by 2/37 of the lesser of the itemized total or "
                "income taxed in the 37% bracket — NOT applied "
                "(top_bracket_income not provided); a 37%-bracket "
                "itemizer's line 12 is OVERSTATED — compute taxable income "
                "provisionally and re-call with top_bracket_income")
        else:
            tbi = _sched_a_amount(top_bracket_income, "top_bracket_income")
            if tbi > 0:
                obbba_70111_reduction = round(
                    _OBBBA_ITEMIZED_HAIRCUT * min(itemized_total, tbi), 2)
                itemized_total = round(itemized_total - obbba_70111_reduction, 2)
                notes.append(
                    f"OBBBA §70111 haircut applied: −${obbba_70111_reduction:,.2f}"
                    " (2/37 of the lesser of itemized total or 37%-bracket "
                    "income)")

    if fs == "MFS" and mfs_spouse_itemizes:
        use_itemized = True
        notes.append(
            "MFS with an itemizing spouse MUST itemize (§63(c)(6)(A)) even "
            "though the standard deduction is larger")
    else:
        use_itemized = itemized_total > std
    return {
        "medical_deductible": medical_deductible,
        "salt_before_cap": round(salt_before_cap, 2),
        "salt_deductible": salt_deductible,
        "mortgage_deductible": mortgage_deductible,
        "investment_interest": round(inv_int, 2),
        "charitable_deductible": charitable_deductible,
        "charitable_carryforward": charitable_carryforward,
        "obbba_70111_reduction": obbba_70111_reduction,
        "itemized_total": itemized_total,
        "standard_deduction": std,
        "use_itemized": use_itemized,
        "election_margin": round(itemized_total - std, 2),
        "notes": notes,
    }


def compute_ca_itemized(
    federal_agi: float,
    ca_agi: float,
    tax_year: int,
    mfs_spouse_itemizes: bool = False,
    married_filing_separately: bool = False,
    medical_expenses: float = 0.0,
    state_local_income_taxes: float = 0.0,
    real_estate_taxes: float = 0.0,
    personal_property_taxes: float = 0.0,
    mortgage_interest: float = 0.0,
    mortgage_points: float = 0.0,
    average_mortgage_balance: float = None,
    investment_interest: float = 0.0,
    charitable_cash: float = 0.0,
    charitable_noncash: float = 0.0,
    misc_2pct_expenses: float = 0.0,
    ca_standard_deduction: float = None,
    phaseout_threshold: float = None,
) -> dict:
    """CA Schedule CA itemized deductions — computed from the SOURCE figures
    (not by adjusting the federal Schedule A total), because the differences
    are structural:

    - State/local INCOME taxes (incl. CA income tax and SDI) and sales taxes
      are disallowed entirely, but property taxes have NO cap (no SALT cap).
    - Mortgage limit stays $1M regardless of origination date — interest the
      federal $750K limit prorated away comes BACK for CA.
    - Cash charitable cap is 50% of AGI (CA never conformed to the TCJA
      60%); the OBBBA 0.5% floor is federal-only.
    - Misc 2%-floor deductions (unreimbursed employee expenses, investment
      fees, tax prep) are STILL allowed for CA (no TCJA suspension).
    - High-AGI itemized phase-out (R&TC §17077): reduce by the lesser of 6%
      of FEDERAL AGI over the (indexed, cached) threshold or 80% of the
      phase-out-subject amount — exempt: medical, investment interest (and,
      when ever modeled, casualty and gambling losses — the FTB worksheet
      excludes those too). phaseout_threshold None → phase-out NOT applied,
      loud note (a high-AGI CA itemizer's deduction would be overstated
      until fetched).

    EVERY AGI-anchored floor/cap here (medical 7.5%, charitable 50%/30%,
    misc 2%) uses FEDERAL AGI — Schedule CA Part II lines reference federal
    Form 1040 line 11, not CA AGI. ca_agi is accepted for future CA-AGI-
    based lines but is currently unused in the math; a CA-AGI base would
    overstate a retiree's medical deduction (CA subtracts Social Security,
    so CA AGI < federal AGI).

    mfs_spouse_itemizes: CA mirrors the federal §63(c)(6)(A) coupling — an
    MFS/RDP-separate filer whose spouse itemizes cannot take the CA
    standard deduction.

    ca_standard_deduction None → election not computed (amounts only).
    """
    if isinstance(tax_year, bool) or not isinstance(tax_year, int):
        raise ValueError(f"tax_year must be an int, got {tax_year!r}")
    for name, v in (("federal_agi", federal_agi), ("ca_agi", ca_agi)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            raise ValueError(f"{name} must be a finite number, got {v!r}")
    for fname, flag in (("mfs_spouse_itemizes", mfs_spouse_itemizes),
                        ("married_filing_separately", married_filing_separately)):
        if not isinstance(flag, bool):
            raise ValueError(f"{fname} must be a boolean, got {flag!r}")
    fed_agi = float(federal_agi)     # unfloored §17077 trigger
    # FTB anchors the medical/charitable/misc floors on FEDERAL AGI
    # (Schedule CA Part II references 1040 line 11) — floored for the same
    # negative-AGI reason as the federal function. ca_agi intentionally
    # unused in the current math.
    agi_v = max(0.0, fed_agi)
    med = _sched_a_amount(medical_expenses, "medical_expenses")
    _sched_a_amount(state_local_income_taxes, "state_local_income_taxes")
    re_tax = _sched_a_amount(real_estate_taxes, "real_estate_taxes")
    pp_tax = _sched_a_amount(personal_property_taxes, "personal_property_taxes")
    mort_int = _sched_a_amount(mortgage_interest, "mortgage_interest")
    points = _sched_a_amount(mortgage_points, "mortgage_points")
    inv_int = _sched_a_amount(investment_interest, "investment_interest")
    cash = _sched_a_amount(charitable_cash, "charitable_cash")
    noncash = _sched_a_amount(charitable_noncash, "charitable_noncash")
    misc = _sched_a_amount(misc_2pct_expenses, "misc_2pct_expenses")

    notes = ["CA disallows state/local income taxes (incl. CA tax + SDI) "
             "and sales taxes — property taxes deductible WITHOUT cap"]

    medical_deductible = round(max(0.0, med - _SCHED_A_MEDICAL_FLOOR * agi_v), 2)
    taxes_deductible = round(re_tax + pp_tax, 2)
    # CA keeps the pre-TCJA acquisition-debt limit, halved for MFS/RDP-
    # separate filers ($500K) per the Schedule CA line 8 instructions.
    ca_limit = (_CA_MORTGAGE_LIMIT / 2 if married_filing_separately
                else _CA_MORTGAGE_LIMIT)
    mortgage_deductible = round(
        _prorated_mortgage_interest(mort_int + points,
                                    average_mortgage_balance,
                                    ca_limit, notes,
                                    f"CA (${ca_limit:,.0f} limit)"),
        2)

    cash_cap = _CA_CHARITY_CASH_AGI_CAP * agi_v
    cash_allowed = min(cash, cash_cap)
    noncash_cap = min(_CHARITY_APPRECIATED_AGI_CAP * agi_v,
                      max(0.0, 0.50 * agi_v - cash_allowed))
    noncash_allowed = min(noncash, noncash_cap)
    charitable_deductible = round(cash_allowed + noncash_allowed, 2)
    ca_charitable_carryforward = round(
        (cash - cash_allowed) + (noncash - noncash_allowed), 2)
    if ca_charitable_carryforward > 0:
        notes.append(
            "CA cash charitable cap is 50% of FEDERAL AGI (no TCJA 60% "
            f"conformity) — ${ca_charitable_carryforward:,.2f} carries "
            "forward for CA (tracked separately from the federal "
            "carryforward; enter manually next year)")

    misc_deductible = round(max(0.0, misc - 0.02 * agi_v), 2)
    if misc > 0:
        notes.append("misc 2%-floor deductions allowed for CA only "
                     "(federal TCJA suspension does not apply)")

    subtotal = round(medical_deductible + taxes_deductible
                     + mortgage_deductible + inv_int
                     + charitable_deductible + misc_deductible, 2)

    phaseout = 0.0
    if phaseout_threshold is None:
        notes.append(
            "CA itemized phase-out threshold (R&TC §17077, indexed) not in "
            "the tax_law cache — phase-out NOT applied; a high-AGI "
            "itemizer's CA deduction may be OVERSTATED until /tax-setup "
            "fetches state.itemized_phaseout")
    else:
        thr = _sched_a_amount(phaseout_threshold, "phaseout_threshold")
        if fed_agi > thr:
            subject = max(0.0, subtotal - medical_deductible - inv_int)
            phaseout = round(min(_CA_ITEMIZED_PHASEOUT_RATE * (fed_agi - thr),
                                 _CA_ITEMIZED_PHASEOUT_MAX * subject), 2)
            notes.append(
                f"CA itemized phase-out applied: −${phaseout:,.2f} (lesser "
                "of 6% of AGI over threshold or 80% of subject deductions)")
    itemized_total = round(subtotal - phaseout, 2)

    out = {
        "medical_deductible": medical_deductible,
        "taxes_deductible": taxes_deductible,
        "mortgage_deductible": mortgage_deductible,
        "investment_interest": round(inv_int, 2),
        "charitable_deductible": charitable_deductible,
        "misc_deductible": misc_deductible,
        "charitable_carryforward": ca_charitable_carryforward,
        "phaseout_reduction": phaseout,
        "itemized_total": itemized_total,
        "use_itemized": None,
        "notes": notes,
    }
    if ca_standard_deduction is not None:
        std = _sched_a_amount(ca_standard_deduction, "ca_standard_deduction")
        if mfs_spouse_itemizes:
            out["use_itemized"] = True
            notes.append(
                "MFS/RDP-separate with an itemizing spouse MUST itemize for "
                "CA too — the CA standard deduction is unavailable")
        else:
            out["use_itemized"] = itemized_total > std
        out["election_margin"] = round(itemized_total - std, 2)
        if out["use_itemized"] and itemized_total > std:
            notes.append(
                "CA itemizes even when the federal return takes the "
                "standard deduction — the elections are independent")
    return out


# Schedule 1-A structural statutory constants (OBBBA). Amounts (caps, senior
# per-person figure) and phase-out THRESHOLDS are INDEXED and come from the
# fetched federal.schedule_1a block — never hardcoded. Only the phase-out
# MECHANICS are bill-fixed and coded here. NOTE the rounding differs by
# provision (see _sch1a_per1000_phaseout):
#   §224 tips / §225 overtime: reduced by $100 for each WHOLE $1,000 of MAGI
#     over the threshold — the statute (§224(b)(2)(A)/§225) has NO "or fraction
#     thereof", so the excess/$1,000 is rounded DOWN (floor).
#   §70203 car-loan interest: reduced by $200 for each $1,000 "or fraction
#     thereof" — Schedule 1-A Part IV line 27 rounds UP (ceil).
#   §70103 senior: reduced by 6% of MAGI over the threshold (continuous %).
# Effective TY2025-2028 for all four (verify the sunset per provision at the
# final instructions).
_SCH1A_TIPS_OT_PHASEOUT_PER_1000 = 100.0
_SCH1A_CARLOAN_PHASEOUT_PER_1000 = 200.0
_SCH1A_SENIOR_PHASEOUT_RATE = 0.06
_SCH1A_EFFECTIVE_FROM = 2025
_SCH1A_EFFECTIVE_THROUGH = 2028


def _sch1a_status_group(fs: str) -> str:
    """Schedule 1-A caps/thresholds are keyed single vs mfj. The doubled figures
    apply ONLY to a JOINT RETURN — so QSS maps to `single`, NOT `mfj` (a
    qualifying surviving spouse does not file a joint return; the deceased
    spouse can't be on it). Mapping QSS→mfj would hand a QSS filer the $25K
    overtime cap and doubled MAGI thresholds, overstating line 13b. This is
    deliberately narrower than the usual MFJ/QSS grouping used for brackets and
    the standard deduction. (MFS is gated to $0 for the joint-filing-required
    provisions before this matters, and uses the single figure for car-loan.)"""
    return "mfj" if fs == "MFJ" else "single"


def _sch1a_require(limits, path: str):
    """Pull a required indexed figure from the fetched federal.schedule_1a
    block, hard-erroring (the federal.amt unfetched-table precedent) when it
    is absent — a guessed cap would silently mis-limit the deduction."""
    node = limits
    for key in path.split("."):
        if not isinstance(node, dict) or node.get(key) is None:
            raise ValueError(
                f"federal.schedule_1a.{path} is unfetched — refusing to "
                "compute a Schedule 1-A deduction without the indexed cap/"
                "threshold (a guess would mis-limit it); fetch via /tax-setup")
        node = node[key]
    return _sched_a_amount(node, f"schedule_1a.{path}")


def _sch1a_per1000_phaseout(magi: float, threshold: float, per_1000: float,
                            capped_amount: float, round_up: bool) -> float:
    """$X per $1,000 of MAGI over the threshold, not below 0, never exceeding
    the (already-capped) deduction it reduces. Rounding differs by provision:
    the car-loan worksheet (§70203) counts each $1,000 "or fraction thereof"
    (round UP), but the tips (§224) and overtime (§225) worksheets count only
    whole $1,000 increments (round DOWN) — a taxpayer $1 over the threshold
    loses nothing there. Using the wrong direction on tips/overtime understates
    the deduction (overstates tax) by up to one $100 increment."""
    if magi <= threshold:
        return 0.0
    ratio = (magi - threshold) / 1000.0
    increments = math.ceil(ratio) if round_up else math.floor(ratio)
    return min(capped_amount, per_1000 * increments)


def compute_schedule_1a(
    filing_status: str,
    tax_year: int,
    magi: float,
    limits: dict,
    qualified_tips: float = 0.0,
    overtime_premium: float = 0.0,
    car_loan_interest: float = 0.0,
    primary_age_65: bool = False,
    spouse_age_65: bool = False,
) -> dict:
    """OBBBA Schedule 1-A deductions — tips (§224), overtime premium (§225),
    car-loan interest (§70203), and the enhanced senior deduction (§70103).

    These are BELOW-the-line deductions (new Form 1040 line 13b), claimed
    AFTER AGI and available to itemizers and non-itemizers alike. They do NOT
    reduce AGI — routing them into AGI adjustments corrupts every MAGI-gated
    computation downstream (IRA phase-outs, taxable SS, NIIT, education
    credits, ACA PTC) and understates tax.

    Args:
        magi: modified AGI = AGI + §911/§931/§933 exclusions (the caller
            computes it; reuse the FEIE-addback convention from
            compute_education_credits). Each provision phases out on MAGI.
        limits: the fetched federal.schedule_1a block. Its indexed figures
            (caps, senior per-person amount, per-provision phase-out
            thresholds) are REQUIRED when the corresponding input is nonzero;
            an unfetched figure hard-errors rather than guessing. When ALL
            inputs are zero the block is not consulted (a return with no
            Schedule 1-A items needs no cache).
        overtime_premium: the FLSA §7 PREMIUM portion only — the "half" of
            time-and-a-half, NOT the whole overtime wage. The note carries the
            warning; the caller/parser must extract the premium, not gross OT.
        primary_age_65 / spouse_age_65: 65-or-older by year end. spouse only
            counts on a JOINT return.

    Returns: per-provision {allowed, disallowed_by_cap, disallowed_by_phaseout}
    dicts, a top-level `total` (sum of alloweds → line 13b), `senior_deduction`
    (= senior allowed, for the Form 6251 AMT addback), and notes.
    """
    fs = _normalize_filing_status(filing_status)
    if isinstance(tax_year, bool) or not isinstance(tax_year, int) \
            or not (2000 <= tax_year <= 2099):
        raise ValueError(f"tax_year must be a 4-digit year integer, got {tax_year!r}")
    for name, flag in (("primary_age_65", primary_age_65),
                       ("spouse_age_65", spouse_age_65)):
        if not isinstance(flag, bool):
            raise ValueError(f"{name} must be a bool, got {flag!r}")
    tips_raw = _sched_a_amount(qualified_tips, "qualified_tips")
    ot_raw = _sched_a_amount(overtime_premium, "overtime_premium")
    car_raw = _sched_a_amount(car_loan_interest, "car_loan_interest")
    senior_eligible = int(primary_age_65) + int(
        spouse_age_65 and fs == "MFJ")

    def _zero(reason=None):
        blank = {"allowed": 0.0, "disallowed_by_cap": 0.0,
                 "disallowed_by_phaseout": 0.0}
        out = {"tips": dict(blank), "overtime": dict(blank),
               "car_loan": dict(blank), "senior": dict(blank),
               "total": 0.0, "senior_deduction": 0.0,
               "notes": [reason] if reason else []}
        return out

    # Effective-year gate: outside TY2025-2028 all four are $0.
    if not (_SCH1A_EFFECTIVE_FROM <= tax_year <= _SCH1A_EFFECTIVE_THROUGH):
        return _zero(
            f"Schedule 1-A deductions (tips/overtime/car-loan/senior) are not "
            f"in effect for TY{tax_year} (OBBBA window "
            f"{_SCH1A_EFFECTIVE_FROM}-{_SCH1A_EFFECTIVE_THROUGH})")

    # No inputs at all → $0 without needing the cache.
    if tips_raw == 0 and ot_raw == 0 and car_raw == 0 and senior_eligible == 0:
        return _zero()

    # MAGI may be NEGATIVE on a loss/NOL year — it's used only to test excess
    # over the phase-out threshold (max(0, magi − threshold)), so a negative
    # MAGI simply yields no phase-out rather than an error. allow_negative.
    magi_v = _f2210_amount(magi, "magi", allow_negative=True)
    notes = []
    group = _sch1a_status_group(fs)
    is_mfs = fs == "MFS"

    def _provision(raw, cap, threshold, per_1000, round_up, jointly_required,
                   label):
        blank = {"allowed": 0.0, "disallowed_by_cap": 0.0,
                 "disallowed_by_phaseout": 0.0}
        if raw == 0:
            return blank
        if jointly_required and is_mfs:
            notes.append(
                f"{label}: $0 — MFS filers cannot claim it (§ requires a "
                "joint return); the ${:,.0f} claimed is disallowed".format(raw))
            return {"allowed": 0.0, "disallowed_by_cap": 0.0,
                    "disallowed_by_phaseout": round(raw, 2)}
        capped = min(raw, cap)
        reduction = _sch1a_per1000_phaseout(magi_v, threshold, per_1000, capped,
                                            round_up)
        allowed = round(max(0.0, capped - reduction), 2)
        return {
            "allowed": allowed,
            "disallowed_by_cap": round(max(0.0, raw - cap), 2),
            "disallowed_by_phaseout": round(capped - allowed, 2),
        }

    # Tips (§224) — joint-filing required; phases out at $100 per WHOLE $1,000
    # (round DOWN — no "or fraction" language). MFS is definitionally $0, so the
    # cache is not consulted for it.
    tips = _provision(
        tips_raw,
        _sch1a_require(limits, "tips_cap") if tips_raw and not is_mfs else 0.0,
        _sch1a_require(limits, f"tips_overtime_phaseout_start.{group}")
        if tips_raw and not is_mfs else 0.0,
        _SCH1A_TIPS_OT_PHASEOUT_PER_1000, False, True, "Qualified tips")
    if tips_raw > 0 and not is_mfs:
        notes.append(
            "For tips from a Schedule C / 1099 trade or business, the deduction "
            "is capped at that trade-or-business's NET PROFIT (Schedule 1-A "
            "line 5) and excludes SSTB (§199A(d)(2)) tip income — v1 does NOT "
            "model those limits, so `qualified_tips` for a self-employed filer "
            "must already be net-profit-capped and SSTB-excluded (a low/"
            "negative-profit or SSTB tipped business would otherwise overstate "
            "the deduction). That net-profit limit is FURTHER REDUCED by the "
            "allocable ½-SE-tax / SE-retirement / SE-health-insurance "
            "deductions per the final Schedule 1-A line-5 instructions, so "
            "`qualified_tips` for a self-employed filer must already reflect "
            "that reduction")

    # Overtime PREMIUM (§225) — joint-filing required; same thresholds/rounding
    # as tips (round DOWN).
    overtime = _provision(
        ot_raw,
        _sch1a_require(limits, f"overtime_cap.{group}")
        if ot_raw and not is_mfs else 0.0,
        _sch1a_require(limits, f"tips_overtime_phaseout_start.{group}")
        if ot_raw and not is_mfs else 0.0,
        _SCH1A_TIPS_OT_PHASEOUT_PER_1000, False, True, "Overtime")
    if ot_raw > 0:
        notes.append(
            "Overtime deduction covers the FLSA premium portion ONLY (the "
            "'half' of time-and-a-half), not the whole overtime wage — "
            "confirm the input is the premium, not gross overtime pay")

    # Car-loan interest (§70203) — JOINT-filing required: the OBBBA car-loan
    # interest deduction is NOT available to a married taxpayer who files
    # separately (MFS), so _provision zeros it with a disallowed note (verified
    # vs IRS / H&R Block / Federal Register guidance — the round-1 "no MFS bar"
    # reading was wrong). Non-MFS filers claim it at the $100K/$200K-MFJ
    # threshold; phases out at $200 per $1,000 "or fraction thereof" (round UP).
    car_loan = _provision(
        car_raw,
        _sch1a_require(limits, "car_loan_cap") if car_raw and not is_mfs else 0.0,
        _sch1a_require(limits, f"car_loan_phaseout_start.{group}")
        if car_raw and not is_mfs else 0.0,
        _SCH1A_CARLOAN_PHASEOUT_PER_1000, True, True, "Car-loan interest")
    if car_raw > 0 and not is_mfs:
        notes.append(
            "car_loan_interest must be the PERSONAL-use portion of the vehicle "
            "loan interest, NET of any interest already deducted on Schedule "
            "C/E/F (business use) — passing gross interest double-deducts the "
            "business share and understates tax")

    # Senior (§70103) — per-person amount × eligible count, joint-required for
    # the spouse's share. The 6% phase-out is applied PER ELIGIBLE PERSON, not
    # once to the combined base: each person's $6,000 is reduced by 6% of the
    # excess MAGI, so a two-senior MFJ couple's deduction reaches $0 at the
    # SAME MAGI a one-senior filer's does (~threshold + amount/6%). Phasing the
    # combined $12,000 once would fully phase ~$100K later (350K vs 250K MFJ)
    # and overstate the deduction for phased-out couples (Codex P1).
    senior = {"allowed": 0.0, "disallowed_by_cap": 0.0,
              "disallowed_by_phaseout": 0.0}
    if senior_eligible > 0:
        if is_mfs:
            notes.append(
                "Senior deduction: $0 — the OBBBA §70103 enhanced senior "
                "deduction requires a joint return for a married filer")
        else:
            per_person = _sch1a_require(limits, "senior_amount_per_person")
            base = per_person * senior_eligible
            thr = _sch1a_require(limits, f"senior_phaseout_start.{group}")
            reduction = (_SCH1A_SENIOR_PHASEOUT_RATE * max(0.0, magi_v - thr))
            per_person_allowed = max(0.0, per_person - reduction)
            allowed = round(senior_eligible * per_person_allowed, 2)
            senior = {
                "allowed": allowed,
                "disallowed_by_cap": 0.0,
                "disallowed_by_phaseout": round(base - allowed, 2),
            }
            notes.append(
                "Senior deduction is IN ADDITION to the §63(f) age-65 "
                "additional standard deduction (they stack) and is available "
                "to itemizers — it is NOT a standard-deduction enhancement")

    if tips["allowed"] or overtime["allowed"] or senior["allowed"]:
        notes.append(
            "Valid-SSN eligibility is the caller's screen: tips (§224), "
            "overtime (§225), and the senior deduction (§70103) require the "
            "recipient/claimant to have a valid SSN — an ITIN filer does NOT "
            "qualify. This function takes amounts + age flags only, so a "
            "non-SSN person's amount must be zeroed / age flag omitted before "
            "the call (otherwise line 13b is overstated)")

    total = round(tips["allowed"] + overtime["allowed"]
                  + car_loan["allowed"] + senior["allowed"], 2)
    return {
        "tips": tips,
        "overtime": overtime,
        "car_loan": car_loan,
        "senior": senior,
        "total": total,
        # Convenience for the Form 6251 AMT addback (§70103 senior is a
        # Schedule 1-A line-37 addback; tips/overtime/car-loan are NOT added
        # back for AMT absent contrary language in the final 6251 instructions).
        "senior_deduction": senior["allowed"],
        "notes": notes,
    }


_CHARITABLE_NONITEMIZER_FROM = 2026  # §170(p), OBBBA §70424 — permanent TY2026+


def compute_charitable_non_itemizer(
    cash_contributions: float,
    filing_status: str,
    tax_year: int,
    itemizes: bool,
    over_250_substantiated: bool = None,
    amount: float = None,
    joint_amount: float = None,
) -> dict:
    """§170(p) (OBBBA §70424) — a permanent BELOW-AGI charitable deduction for
    STANDARD-deduction filers, effective TY2026+.

    CASH gifts only; donor-advised-fund and §509(a)(3) supporting-organization
    gifts do NOT qualify (the caller attests). Mutually exclusive with the
    Schedule A charitable deduction — an itemizer gets $0 here. Does NOT reduce
    AGI (it sits with Schedule 1-A in the line-13 region, below AGI); the §70425
    0.5%-of-AGI itemizer floor does NOT apply; amounts deducted here generate NO
    carryforward. The caps are INDEXED — `amount` (non-joint) and `joint_amount`
    (MFJ) come from federal.charitable_non_itemizer, never hardcoded; an
    unfetched cap hard-errors when the input is nonzero.

    Returns {allowed, disallowed_by_cap, notes}.
    """
    raw = _sched_a_amount(cash_contributions, "cash_contributions")
    fs = _normalize_filing_status(filing_status)
    if isinstance(tax_year, bool) or not isinstance(tax_year, int) \
            or not (2000 <= tax_year <= 2099):
        raise ValueError(f"tax_year must be a 4-digit year integer, got {tax_year!r}")
    if not isinstance(itemizes, bool):
        raise ValueError(f"itemizes must be a bool, got {itemizes!r}")
    if over_250_substantiated is not None \
            and not isinstance(over_250_substantiated, bool):
        raise ValueError(
            "over_250_substantiated must be a boolean or None, "
            f"got {over_250_substantiated!r}")

    notes = []
    if tax_year < _CHARITABLE_NONITEMIZER_FROM:
        notes.append(
            f"§170(p) non-itemizer charitable deduction is not in effect before "
            f"TY{_CHARITABLE_NONITEMIZER_FROM} (OBBBA §70424) — $0")
        return {"allowed": 0.0, "disallowed_by_cap": 0.0, "notes": notes}
    if itemizes:
        notes.append(
            "§170(p) is for STANDARD-deduction filers only — an itemizer claims "
            "cash gifts on Schedule A instead (mutually exclusive); $0 here")
        return {"allowed": 0.0, "disallowed_by_cap": 0.0, "notes": notes}
    if raw == 0:
        return {"allowed": 0.0, "disallowed_by_cap": 0.0, "notes": notes}

    # MFJ uses the joint cap; Single/HoH/MFS/QSS use the non-joint figure (MFS
    # follows the non-joint amount per the statute; QSS does not file jointly).
    cap_value = joint_amount if fs == "MFJ" else amount
    label = "joint_amount" if fs == "MFJ" else "amount"
    if cap_value is None:
        raise ValueError(
            f"federal.charitable_non_itemizer.{label} is unfetched — refusing "
            "to compute the §170(p) deduction without the indexed cap (a guess "
            "would mis-limit it); fetch via /tax-setup")
    cap = _sched_a_amount(cap_value, f"charitable_non_itemizer.{label}")
    allowed = round(min(raw, cap), 2)

    notes.append(
        "CASH gifts only — donor-advised-fund and §509(a)(3) supporting-"
        "organization gifts do NOT qualify for §170(p); confirm the gifts are "
        "direct cash to a qualifying charity")
    notes.append(
        "the §70425 0.5%-of-AGI charitable floor does NOT apply to §170(p) "
        "(that floor is itemizer-only); amounts deducted here carry NO "
        "carryforward")
    if over_250_substantiated is None:
        notes.append(
            "SUBSTANTIATION UNKNOWN: any single gift ≥ $250 requires a "
            "contemporaneous written acknowledgment (§170(f)(8)) — confirm "
            "before filing")
    elif over_250_substantiated is False:
        notes.append(
            "SUBSTANTIATION GATE: gifts ≥ $250 lack the written acknowledgment "
            "— the deduction is disallowed on audit; obtain the letters first")
    return {"allowed": allowed,
            "disallowed_by_cap": round(max(0.0, raw - cap), 2),
            "notes": notes}


# §423 ESPP qualifying-disposition holding periods (IRC §423(a)): a disposition
# is QUALIFYING only when it occurs AFTER both the 2-year period beginning on
# the grant date AND the 1-year period beginning on the purchase (transfer)
# date have expired — strictly more than, per the /tax-harvest
# derive_holding_period precedent (exact-anniversary days still fail).
_ESPP_QUALIFYING_GRANT_YEARS = 2
_ESPP_QUALIFYING_PURCHASE_YEARS = 1


def _espp_date(raw, label: str) -> datetime.date:
    """Parse an ISO date (or accept a date) for the §423 helper — house-style
    strict validation, distinct message per field."""
    if isinstance(raw, datetime.date) and not isinstance(raw, datetime.datetime):
        return raw
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if not isinstance(raw, str):
        raise ValueError(f"{label} must be a YYYY-MM-DD date string, got {raw!r}")
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError as e:
        raise ValueError(f"{label} {raw!r} is not a valid YYYY-MM-DD date: {e}")


def _add_years(d: datetime.date, years: int) -> datetime.date:
    """The date `years` calendar years after d, collapsing Feb 29 → Feb 28 in a
    non-leap target year (so a leap-day anniversary lands on the last valid
    February day rather than raising)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Only Feb 29 can fail — map to Feb 28 of the target year.
        return d.replace(year=d.year + years, day=28)


def compute_espp_disposition(
    shares: float,
    grant_fmv_per_share: float,
    purchase_fmv_per_share: float,
    price_paid_per_share: float,
    sale_proceeds: float,
    grant_date,
    purchase_date,
    sale_date,
) -> dict:
    """§423 ESPP disposition split (Form 3922 boxes 3/4/5/8 → ordinary + capital).

    Per sold lot, all per-share box values × `shares` totals:
      - box 3 = grant-date FMV per share (`grant_fmv_per_share`)
      - box 4 = purchase-date FMV per share (`purchase_fmv_per_share`)
      - box 5 = price paid per share (`price_paid_per_share`)
      - box 8 = shares

    QUALIFYING disposition (sold > 2 years after grant AND > 1 year after
    purchase, strictly): ordinary income = LESSER of (grant-date discount) and
    (actual gain), floored at $0 (a loss sale yields $0 ordinary, never
    negative); the remainder is long-term capital gain. The grant-date discount
    is (grant FMV − price paid) per share in v1 — the true §423(c) figure is
    FMV-at-grant × the plan discount %, but the plan's lookback/discount facts
    are not on Form 3922, so `box3 − box5` is the v1 proxy. It can OVERSTATE the
    discount (box 5 is often a lookback-lowered purchase price), which
    overstates ordinary income — the SAFE direction; caveated.

    DISQUALIFYING disposition (fails either holding test): ordinary income =
    (purchase FMV − price paid), floored at $0, REGARDLESS of the sale price;
    the capital basis is (price paid + ordinary income) and the capital
    gain/loss runs from the purchase date (short- or long-term).

    In both branches the corrected capital basis is (price paid + ordinary
    income) — the code-B column (g) adjustment is the caller's
    (broker-reported basis − corrected basis) once the broker figure is known
    (brokers routinely report price-paid-only basis, omitting the ordinary
    component). Returns {classification, ordinary_income, capital_basis,
    capital_gain, term, notes}.
    """
    n = _sched_a_amount(shares, "shares")
    if n == 0:
        raise ValueError("shares must be > 0 for an ESPP disposition")
    g_fmv = _sched_a_amount(grant_fmv_per_share, "grant_fmv_per_share")
    p_fmv = _sched_a_amount(purchase_fmv_per_share, "purchase_fmv_per_share")
    price = _sched_a_amount(price_paid_per_share, "price_paid_per_share")
    proceeds = _sched_a_amount(sale_proceeds, "sale_proceeds")
    gd = _espp_date(grant_date, "grant_date")
    pd = _espp_date(purchase_date, "purchase_date")
    sd = _espp_date(sale_date, "sale_date")
    if pd < gd:
        raise ValueError(
            f"purchase_date {pd} precedes grant_date {gd} — impossible for a "
            "§423 plan (the option is granted before it is exercised)")
    if sd < pd:
        raise ValueError(
            f"sale_date {sd} precedes purchase_date {pd} — a share cannot be "
            "sold before it is purchased")

    price_total = round(price * n, 2)
    notes = []
    qualifying = (sd > _add_years(gd, _ESPP_QUALIFYING_GRANT_YEARS)
                  and sd > _add_years(pd, _ESPP_QUALIFYING_PURCHASE_YEARS))

    if qualifying:
        grant_discount = max(0.0, (g_fmv - price)) * n
        actual_gain = max(0.0, proceeds - price_total)
        ordinary = round(min(grant_discount, actual_gain), 2)
        term = "long"  # a qualifying disposition is always > 1 year from purchase
        notes.append(
            "QUALIFYING §423 disposition (> 2 yr from grant AND > 1 yr from "
            "purchase): ordinary income = lesser of the grant-date discount and "
            "actual gain, floored at $0")
        notes.append(
            "grant-date discount = box3 − box5 (grant FMV − price paid). For a "
            "PLAIN discount plan this is EXACTLY the §423(c)(2) limit (FMV at "
            "grant − option price) — do NOT reduce it. Only for a LOOKBACK plan, "
            "where box5 reflects the lower purchase-date FMV, is the true limit "
            "lower: §423(c)'s flush rule sets the option price 'as if exercised "
            "at grant' (= discount % × grant FMV), so box3 − box5 is then a "
            "conservative UPPER BOUND (may overstate ordinary income → overstates "
            "tax, the safe direction). Reduce it ONLY on documented lookback "
            "plan terms")
        notes.append(
            "the ordinary component generally is NOT reported on W-2 for a "
            "qualifying disposition (the employer usually doesn't know you sold) "
            "→ report it on 1040 line 1h unless documented as already in W-2 box "
            "1; the corrected basis already removes it from the capital gain, so "
            "omitting it from wages would drop it from income entirely")
    else:
        ordinary = round(max(0.0, (p_fmv - price)) * n, 2)
        # Capital-gain term runs from the PURCHASE date: long-term iff sold more
        # than one year after purchase (strictly), same anniversary rule as the
        # qualifying test's 1-year leg.
        term = "long" if sd > _add_years(pd, 1) else "short"
        notes.append(
            "DISQUALIFYING §423 disposition (sold within 2 yr of grant or 1 yr "
            "of purchase): ordinary income = purchase-date bargain element "
            "(box4 − box5), regardless of sale price")
        notes.append(
            "the ordinary component belongs in W-2 box 1 — if the employer did "
            "NOT include it (common for former employees) it goes to 1040 line "
            "1h; do NOT double-count if the W-2 already includes it")

    capital_basis = round(price_total + ordinary, 2)
    capital_gain = round(proceeds - capital_basis, 2)
    notes.append(
        "corrected capital basis = price paid + ordinary income; the broker "
        "often reports price-paid-only basis, so the Form 8949 code-B column "
        "(g) adjustment = (broker basis − corrected basis) reduces the gain")
    return {
        "classification": "qualifying" if qualifying else "disqualifying",
        "ordinary_income": ordinary,
        "capital_basis": capital_basis,
        "capital_gain": capital_gain,
        "term": term,
        "notes": notes,
    }


# Form 6251 structural statutory constants (§55): the two AMT rates are fixed;
# the exemption amounts, phase-out starts, and the 26%→28% breakpoint are
# INDEXED and come from federal.amt. The exemption phase-out RATE is a
# bill-fixed structural fact keyed by year: §55(d)(3) sets 25% through TY2025,
# and OBBBA §70107 raises it to 50% for tax years beginning after 2025 (and
# resets the phase-out thresholds to the 2018 base, reindexed — those flow in
# through federal.amt.phaseout_start, so only the RATE is encoded here).
_AMT_RATE_LOW = 0.26
_AMT_RATE_HIGH = 0.28
_AMT_PHASEOUT_RATE_PRE_2026 = 0.25
_AMT_PHASEOUT_RATE_2026 = 0.50  # OBBBA §70107, TY2026+
_AMT_PHASEOUT_RATE_OBBBA_FROM = 2026


def _resolve_amt_phaseout_rate(tax_year: int, override) -> float:
    """Resolve the AMT exemption phase-out rate: an explicit cache-supplied
    override wins (the ptet._salt_cap precedent — a fetched figure beats the
    built-in schedule), else the statutory schedule by year. The MFS
    exemption-recapture ceiling uses this SAME rate — the two must never
    drift (§55(d)(3) drives both, and §70107 changes §55(d)(3)).
    """
    if override is not None:
        if isinstance(override, bool) \
                or not isinstance(override, (int, float)) \
                or not math.isfinite(float(override)) \
                or not (0 < float(override) <= 1):
            raise ValueError(
                "exemption_phaseout_rate must be a finite fraction in (0, 1], "
                f"got {override!r}")
        return float(override)
    if tax_year >= _AMT_PHASEOUT_RATE_OBBBA_FROM:
        return _AMT_PHASEOUT_RATE_2026
    return _AMT_PHASEOUT_RATE_PRE_2026


def _amt_2628(ordinary: float, breakpoint: float) -> float:
    """Form 6251 line 7 / Part III line 40 formula: 26% up to the
    breakpoint, else 28% minus the 2%-of-breakpoint offset."""
    if ordinary <= breakpoint:
        return _AMT_RATE_LOW * ordinary
    return _AMT_RATE_HIGH * ordinary - 0.02 * breakpoint


def compute_form_6251(
    taxable_income: float,
    filing_status: str,
    tax_year: int,
    regular_tax: float,
    exemption: float,
    phaseout_start: float,
    rate_28_breakpoint: float,
    salt_deducted: float = 0.0,
    standard_deduction_taken: float = 0.0,
    iso_bargain_element: float = 0.0,
    private_activity_bond_interest: float = 0.0,
    other_adjustments: float = 0.0,
    qualified_dividends: float = 0.0,
    net_ltcg: float = 0.0,
    zero_pct_limit: float = None,
    fifteen_pct_limit: float = None,
    amt_ftc: float = 0.0,
    senior_deduction: float = 0.0,
    exemption_phaseout_rate: float = None,
) -> dict:
    """Form 6251 AMT (v1: the ISO-exerciser path) + the Form 8801
    deferral-item credit carryforward.

    Args:
        taxable_income: Form 6251 line 1 — 1040 line 15 when positive. When line 15 is $0 because deductions exceeded AGI, pass line 11 AGI minus line 14 deductions (a NEGATIVE number) per the 6251 line 1 instructions — passing 0 overstates AMTI by the shortfall (the D9-review follow-up; a 0 input with addbacks still gets the may-be-OVERSTATED note since the caller may not have refigured).
        regular_tax: 1040 line 16 MINUS Schedule 3 line 1 foreign tax
            credit (the 6251 line 10 comparison base — simplified; Schedule
            J / Form 4972 recomputations not modeled).
        senior_deduction: the OBBBA enhanced senior deduction (Schedule
            1-A line 37, TY2025+) — NOT allowed for AMT, added back to AMTI
            (up to $6K per eligible spouse); omitting it understates AMTI
            for a senior ISO exerciser.
        amt_ftc: the AMT foreign tax credit (6251 line 8, subtracted from
            line 7 to give TMT). For filers on the repo's simplified-FTC
            path (≤$600 MFJ / $300 single, no Form 1116) it EQUALS the
            regular FTC — pass the same figure; omitting it overstates AMT
            by exactly the FTC.
        exemption / phaseout_start / rate_28_breakpoint: federal.amt from
            the tax_law cache, keyed by status (breakpoint is the single
            published figure — HALVED here for MFS per the form). REQUIRED:
            an unfetched AMT table hard-errors rather than producing $0 AMT
            for an ISO exerciser.
        tax_year: 4-digit int. Drives the §55(d)(3) exemption phase-out rate
            (and the MFS recapture rate): 25% through TY2025, 50% from TY2026
            (OBBBA §70107). REQUIRED — an unspecified year would silently keep
            25% for a TY2026 ISO exerciser and understate the AMT.
        exemption_phaseout_rate: optional cache override
            (federal.amt.phaseout_rate). When supplied (finite fraction in
            (0,1]) it WINS over the statutory schedule — the salt_cap
            precedent — so a fetched figure can correct the built-in schedule
            without a code change. None → statutory schedule by tax_year.
        salt_deducted: Schedule A line 7 as deducted (exclusion item,
            added back). standard_deduction_taken: 1040 line 12 when NOT
            itemizing (6251 line 2a adds back whichever applied — pass one
            or the other, never both).
        iso_bargain_element: ISO exercise spread (FMV − strike at exercise,
            for shares NOT sold in the same year) — the DEFERRAL item that
            generates the Form 8801 credit.
        private_activity_bond_interest: specified PAB tax-exempt interest
            (exclusion item).
        other_adjustments: signed catch-all for unmodeled lines
            (depreciation, NOL, etc.) — treated as an EXCLUSION item in the
            8801 split (conservative: understates the credit, never invents
            one); caveated.
        qualified_dividends / net_ltcg / zero_pct_limit / fifteen_pct_limit:
            Part III capital-gains coordination — AMT taxes qualified
            dividends and net LTCG at the SAME 0/15/20 rates using the
            regular breakpoints (required when the capital amount > 0).

    MFS note: the §55(d)(3) MFS exemption-recapture addback (AMTI increased
    when over the phase-out ceiling) is NOT modeled — caveated.

    Returns dict: amti, exemption_allowed, tmt, amt (Schedule 2 line 1),
    form_8801_carryforward (deferral-attributable AMT — persists to next
    year via /tax-rollover), notes.
    """
    fs = _normalize_filing_status(filing_status)
    if isinstance(tax_year, bool) or not isinstance(tax_year, int) \
            or not (2000 <= tax_year <= 2099):
        raise ValueError(f"tax_year must be a 4-digit year integer, got {tax_year!r}")
    # §55(d)(3) exemption phase-out rate — year-keyed (25% ≤2025, 50% from
    # 2026 per OBBBA §70107) unless the cache supplies an override. Both the
    # exemption phase-out and the MFS recapture ceiling use THIS resolved rate.
    po_rate = _resolve_amt_phaseout_rate(tax_year, exemption_phaseout_rate)
    ti = _f2210_amount(taxable_income, "taxable_income", allow_negative=True)
    rtax = _sched_a_amount(regular_tax, "regular_tax")
    for what, v in (("exemption", exemption),
                    ("phaseout_start", phaseout_start),
                    ("rate_28_breakpoint", rate_28_breakpoint)):
        if v is None:
            raise ValueError(
                f"federal.amt.{what} is unfetched — refusing to compute AMT "
                "without the indexed table (a guessed exemption would "
                "silently zero the AMT for an ISO exerciser); fetch via "
                "/tax-setup")
    ex = _sched_a_amount(exemption, "exemption")
    ph_start = _sched_a_amount(phaseout_start, "phaseout_start")
    bp = _sched_a_amount(rate_28_breakpoint, "rate_28_breakpoint")
    if fs == "MFS":
        bp /= 2  # the form prints the halved figure for MFS
    salt = _sched_a_amount(salt_deducted, "salt_deducted")
    std = _sched_a_amount(standard_deduction_taken, "standard_deduction_taken")
    if salt > 0 and std > 0:
        raise ValueError(
            "pass salt_deducted (itemized) OR standard_deduction_taken — "
            "both at once double-counts the line 2a addback")
    iso = _sched_a_amount(iso_bargain_element, "iso_bargain_element")
    pab = _sched_a_amount(private_activity_bond_interest,
                          "private_activity_bond_interest")
    if isinstance(other_adjustments, bool) \
            or not isinstance(other_adjustments, (int, float)) \
            or not math.isfinite(float(other_adjustments)):
        raise ValueError(
            f"other_adjustments must be a finite number, got {other_adjustments!r}")
    other = float(other_adjustments)
    ftc_amt = _sched_a_amount(amt_ftc, "amt_ftc")
    senior = _sched_a_amount(senior_deduction, "senior_deduction")
    qd = _sched_a_amount(qualified_dividends, "qualified_dividends")
    if isinstance(net_ltcg, bool) or not isinstance(net_ltcg, (int, float)) \
            or not math.isfinite(float(net_ltcg)):
        raise ValueError(f"net_ltcg must be a finite number, got {net_ltcg!r}")
    ltcg = max(0.0, float(net_ltcg))

    notes = [
        "v1 models the ISO-exerciser path: depreciation/NOL/intangible-"
        "drilling and other 6251 lines go through other_adjustments "
        "(treated as exclusion items — the 8801 credit may be understated, "
        "never overstated)",
        "CA has its OWN AMT (Schedule P, 7% flat, different exemption) — "
        "NOT computed; a CA ISO exerciser must verify Schedule P in "
        "software",
    ]


    def _amti(iso_amount: float) -> float:
        amti = ti + salt + std + iso_amount + pab + other + senior
        if fs == "MFS":
            # §55(d)(3) last sentence: an MFS filer past the phase-out
            # CEILING adds back the phase-out rate × the excess, capped at the
            # exemption amount (prevents the halved exemption from advantaging
            # MFS). Uses the SAME year-resolved rate as the phase-out below.
            ceiling = ph_start + ex / po_rate
            amti += min(ex, po_rate * max(0.0, amti - ceiling))
        return amti

    def _tmt(iso_amount: float) -> float:
        amti = _amti(iso_amount)
        exemption_allowed = max(
            0.0, ex - po_rate * max(0.0, amti - ph_start))
        base = max(0.0, amti - exemption_allowed)
        cg = min(base, qd + ltcg)
        if cg > 0:
            if zero_pct_limit is None or fifteen_pct_limit is None:
                raise ValueError(
                    "capital_gains_rates limits are required for the Part "
                    "III coordination when qualified dividends / net LTCG "
                    "are present")
            z = _sched_a_amount(zero_pct_limit, "zero_pct_limit")
            f15 = _sched_a_amount(fifteen_pct_limit, "fifteen_pct_limit")
            ordinary = base - cg
            # Part III lines 43-51: the 0%/15% headroom is measured against
            # the REGULAR worksheet's ordinary income (regular taxable
            # income minus the capital amount), not the AMT base.
            regular_ordinary = max(0.0, ti - (qd + ltcg))
            at0 = min(cg, max(0.0, z - regular_ordinary))
            at15 = min(cg - at0, max(0.0, f15 - regular_ordinary - at0))
            at20 = cg - at0 - at15
            part_iii = (_amt_2628(ordinary, bp)
                        + 0.15 * at15 + 0.20 * at20)
            # line 64: the lesser of the coordinated tax or the flat 26/28
            return min(part_iii, _amt_2628(base, bp))
        return _amt_2628(base, bp)

    # 6251 line 8: the AMT FTC reduces line 7 to give TMT — applied to
    # both refigures (Form 8801 refigures it too; identical on the
    # simplified-FTC path).
    tmt = max(0.0, _tmt(iso) - ftc_amt)
    amt = round(max(0.0, tmt - rtax), 2)
    # Form 8801: the credit equals AMT attributable to DEFERRAL items — the
    # form refigures 6251 with exclusion items only; v1's only deferral
    # item is the ISO spread, so the split is the with/without-ISO delta.
    amt_exclusion_only = round(
        max(0.0, max(0.0, _tmt(0.0) - ftc_amt) - rtax), 2)
    credit = round(max(0.0, amt - amt_exclusion_only), 2)
    # Fire on ANY $0 line-15 input — deductions exceeding AGI is what
    # makes it 0, whether or not the deductions were AMT addbacks
    # (a charity/mortgage-only itemizer has the same refigure duty).
    if ti == 0:
        notes.append(
            "1040 line 15 is $0 — if deductions exceeded AGI, refigure "
            "line 1 as AGI minus deductions and pass the NEGATIVE result "
            "(now accepted); a $0 input overstates AMTI by the shortfall "
            "(safe direction, but refigure for precision)")

    amti_full = _amti(iso)
    exemption_allowed = max(
        0.0, ex - po_rate * max(0.0, amti_full - ph_start))
    if amt > 0:
        notes.append(
            f"AMT of ${amt:,.2f} applies (Schedule 2 line 1). "
            f"${credit:,.2f} of it is deferral-attributable (ISO) and "
            "becomes a Form 8801 minimum tax credit carryforward — record "
            "it; losing it forfeits the money permanently")
    return {
        "amti": round(amti_full, 2),
        "exemption_allowed": round(exemption_allowed, 2),
        "tmt": round(tmt, 2),
        "regular_tax": round(rtax, 2),
        "amt": amt,
        "form_8801_carryforward": credit,
        # Input echo — matches result_template form_6251.iso_bargain_element
        # so the copy-the-result flow writes it and the A2 tie-out's
        # iso_bargain category can verify the parsed 3921 sum. Without the
        # echo, a CORRECT return false-fails the gate against the template's
        # untouched 0 (reviewer P2 on the D9 follow-ups).
        "iso_bargain_element": round(iso, 2),
        "notes": notes,
    }


def compute_form_8801_credit(
    prior_carryforward: float,
    regular_tax: float,
    tentative_minimum_tax: float,
    nonrefundable_credits: float = 0.0,
) -> dict:
    """Form 8801 Part II (simplified): the prior-year minimum tax credit is
    usable this year up to the excess of regular tax MINUS ALL allowable
    nonrefundable credits (CTC, education, dependent care, FTC — §53(c)(1)
    nets the subpart A/B/D credits, not just the FTC) over this year's
    tentative minimum tax. It can never push you INTO AMT. The unused
    remainder carries forward indefinitely.

    Pass tentative_minimum_tax from compute_form_6251()["tmt"] (0.0 when no
    6251 inputs exist this year is WRONG — the TMT exists even when no AMT
    is due; always run compute_form_6251 first when a carryforward exists).
    nonrefundable_credits: the credits claimed BEFORE this one (1040 line 19
    + Schedule 3 lines 1-4/5b/6c...) — omitting them overstates the usable
    credit and understates tax.
    """
    cf = _sched_a_amount(prior_carryforward, "prior_carryforward")
    rtax = _sched_a_amount(regular_tax, "regular_tax")
    tmt = _sched_a_amount(tentative_minimum_tax, "tentative_minimum_tax")
    credits = _sched_a_amount(nonrefundable_credits, "nonrefundable_credits")
    usable = round(min(cf, max(0.0, rtax - credits - tmt)), 2)
    return {
        "credit_allowed": usable,          # Schedule 3 line 6b
        "carryforward_remaining": round(cf - usable, 2),
    }


# Form 2210 structural statutory constants (IRC §6654, none indexed):
# required annual payment = LESSER of 90% of current-year tax or 100%/110%
# of prior-year tax (110% above $150K prior-year AGI, $75K MFS) — §6654(d);
# no penalty when the balance after withholding is under $1,000 — §6654(e)(1);
# no penalty when prior-year tax was $0 — §6654(e)(2).
_F2210_DE_MINIMIS = 1000.0
_F2210_PRIOR_AGI_THRESHOLD = 150000.0
_F2210_PRIOR_AGI_THRESHOLD_MFS = 75000.0


def _f2210_amount(value, what: str, allow_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {value!r}")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{what} must be finite, got {value!r}")
    if v < 0 and not allow_negative:
        raise ValueError(f"{what} cannot be negative, got {v}")
    return v


def _f2210_date(value, what: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{what} must be an ISO date (YYYY-MM-DD), got {value!r}")


def _f2210_rate_periods(underpayment_rates) -> list:
    """Validate federal.underpayment_rates into a sorted
    [(effective_from, annual_rate)] timeline. Empty input → empty list (the
    caller degrades to penalty-not-computed). Coverage against the accrual
    span is the caller's job — it knows which slices actually accrue.
    annual_rate must be strictly positive: §6621 is the federal short-term
    rate + 3 points, so a 0 in the cache is a fetch bug that would silently
    zero line 38 (the understating direction) — hard error, like a corrupt
    SDI cap."""
    periods = []
    seen = set()
    for i, entry in enumerate(underpayment_rates or []):
        where = f"underpayment_rates[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        eff = _f2210_date(entry.get("effective_from"), f"{where}.effective_from")
        rate = entry.get("annual_rate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) \
                or not math.isfinite(float(rate)) or not (0 < float(rate) < 1):
            raise ValueError(
                f"{where}.annual_rate must be a finite fraction in (0, 1) — "
                f"§6621 can never be 0 — got {rate!r}")
        if eff in seen:
            raise ValueError(f"{where}: duplicate effective_from {eff.isoformat()}")
        seen.add(eff)
        periods.append((eff, float(rate)))
    periods.sort()
    return periods


def _f2210_accrue(amount: float, start: datetime.date, end: datetime.date,
                  periods: list) -> float:
    """Simple interest on one underpayment slice across the rate timeline,
    split at rate changes AND Jan 1 (the Form 2210 worksheet day-count uses
    365, or 366 for days falling in a leap year)."""
    if end <= start:
        return 0.0
    total = 0.0
    for i, (eff, rate) in enumerate(periods):
        nxt = periods[i + 1][0] if i + 1 < len(periods) else None
        s = max(start, eff)
        e = end if nxt is None else min(end, nxt)
        while s < e:
            year_end = datetime.date(s.year + 1, 1, 1)
            sub_e = min(e, year_end)
            denom = 366 if calendar.isleap(s.year) else 365
            total += amount * rate * (sub_e - s).days / denom
            s = sub_e
    return total


def compute_form_2210(
    current_year_tax: float,
    withholding: float,
    filing_status: str,
    tax_year: int,
    prior_year_tax: float = None,
    prior_year_agi: float = None,
    estimated_payments: list = None,
    underpayment_rates: list = None,
) -> dict:
    """Form 2210 underpayment-of-estimated-tax penalty at filing (1040 line 38).

    Args:
        current_year_tax: Form 2210 line 4 — total tax AFTER credits including
            other taxes, MINUS refundable credits (line 24 − EIC/ACTC/AOC
            refundable parts). NOT raw line 22.
        withholding: total federal withholding (line 25d). Deemed paid in four
            equal installments on the due dates (the §6654(g) default; the
            actual-withholding-date election is not modeled — caveated).
        filing_status: Single/MFJ/MFS/HoH/QSS (drives the $75K MFS AGI
            threshold for the 110% prior-year factor).
        tax_year: the year being filed. Installment due dates are the
            statutory Apr/Jun/Sep 15 and Jan 15 of tax_year+1 rolled forward
            per §7503 over weekends/DC legal holidays (matching the printed
            Form 2210 columns); accrual ends at the statutory Apr 15 of
            tax_year+1 (the worksheet endpoint, which does NOT roll).
        prior_year_tax: prior-year total tax. None = unknown → the required
            annual payment degrades to the 90%-of-current test (the prior-year
            harbor could only LOWER it). Explicit 0 → §6654(e)(2) no-penalty.
        prior_year_agi: prior-year AGI for the 110% factor. None with a
            non-zero prior_year_tax assumes the 100% factor and notes the
            possible understatement.
        estimated_payments: list of {"date": "YYYY-MM-DD", "amount": float}
            1040-ES payments (dates matter — a January payment can't cure an
            April underpayment retroactively).
        underpayment_rates: federal.underpayment_rates from the tax_law cache
            (IRC §6621 quarterly rates: {"effective_from", "annual_rate"}).
            Empty/None → the quarterly underpayments are still reported but
            the dollar penalty is NOT computed (loud note), never guessed.

    The annualized-income method (Schedule AI) is NOT modeled — for lumpy
    income it usually REDUCES the penalty, so the figure here may overstate;
    §6654(e)(3) waivers (casualty, disaster, retirement/disability) are also
    out of scope. Both are caveated in notes.

    Returns dict with required_annual_payment, no_penalty(+reason), quarters
    (per-installment required/underpayment), penalty, penalty_computed, notes.
    """
    fs = _normalize_filing_status(filing_status)
    if isinstance(tax_year, bool) or not isinstance(tax_year, int):
        raise ValueError(f"tax_year must be an int, got {tax_year!r}")
    # current_year_tax (2210 line 4) may be NEGATIVE: refundable credits
    # (EIC, refundable ACTC/AOTC, net PTC, fuel credit) can exceed line 24.
    # Rejecting it would crash the unconditional /tax-compute call on exactly
    # the returns that owe nothing — the de minimis test below handles it.
    tax = _f2210_amount(current_year_tax, "current_year_tax",
                        allow_negative=True)
    wh = _f2210_amount(withholding, "withholding")

    est_events = []
    for i, p in enumerate(estimated_payments or []):
        if not isinstance(p, dict):
            raise ValueError(f"estimated_payments[{i}] must be an object")
        amt = _f2210_amount(p.get("amount"), f"estimated_payments[{i}].amount")
        d = _f2210_date(p.get("date"), f"estimated_payments[{i}].date")
        # A payment dated before the tax year is almost surely a typo'd year
        # — and it would sit as day-one credit that UNDERSTATES the penalty,
        # so it hard-errors rather than degrades. (Late payments past Apr 15
        # of tax_year+1 are legal; accrual just stops at the statutory end.)
        if d.year < tax_year:
            raise ValueError(
                f"estimated_payments[{i}].date {d.isoformat()} predates tax "
                f"year {tax_year} — a wrong-year date would fabricate early "
                "payment credit and understate the penalty")
        est_events.append((d, amt))
    total_est = round(sum(a for _, a in est_events), 2)

    notes = [
        "annualized-income method (Schedule AI) not modeled — for income "
        "concentrated late in the year it usually REDUCES the penalty",
        "withholding deemed paid evenly across the four installments "
        "(§6654(g) default); the actual-date election is not modeled",
        "§6654(e)(3) waivers (casualty/disaster, retirement/disability) "
        "not modeled",
        "§6654(i) farmers/fishermen (66⅔% harbor, single Jan-15 installment) "
        "not modeled — the four-installment default OVERSTATES for them",
        "§6654(h) not modeled: filing AND paying in full by Jan 31 stops the "
        "fourth installment's accrual",
        "prior-year MFJ filers now filing MFS must allocate the joint "
        "prior-year tax per Pub 505 — passing the full joint figure "
        "OVERSTATES the prior-year harbor",
    ]
    result = {
        "current_year_tax": round(tax, 2),
        "withholding": round(wh, 2),
        "estimated_payments_total": total_est,
        "required_annual_payment": None,
        "required_basis": None,
        "no_penalty": False,
        "no_penalty_reason": None,
        "quarters": [],
        "penalty": None,
        "penalty_computed": False,
        "notes": notes,
    }

    if tax - wh < _F2210_DE_MINIMIS:
        result["no_penalty"] = True
        result["no_penalty_reason"] = (
            f"balance after withholding (${tax - wh:,.2f}) is under the "
            "$1,000 de minimis (§6654(e)(1)) — no penalty")
        return result

    if prior_year_tax is not None:
        pt = _f2210_amount(prior_year_tax, "prior_year_tax")
        if pt == 0:
            result["no_penalty"] = True
            result["no_penalty_reason"] = (
                "prior-year tax liability was $0 — §6654(e)(2) exception "
                "(requires the prior year covered 12 months and the taxpayer "
                "was a US citizen/resident all year — verify)")
            return result

    # Required annual payment — same math as projection.compute_safe_harbor
    # (kept in sync by test; projection imports THIS module, so the planning
    # dataclass can't be reused here without a circular import).
    pct90 = round(0.90 * tax, 2)
    if prior_year_tax is None:
        required = pct90
        basis = "90% of current-year tax (prior-year figures unavailable)"
        notes.append(
            "prior-year tax/AGI unavailable: required annual payment uses the "
            "90%-of-current test only — supplying prior-year figures can only "
            "LOWER it, never raise it")
    else:
        threshold = (_F2210_PRIOR_AGI_THRESHOLD_MFS if fs == "MFS"
                     else _F2210_PRIOR_AGI_THRESHOLD)
        pa = (None if prior_year_agi is None
              else _f2210_amount(prior_year_agi, "prior_year_agi",
                                 allow_negative=True))
        factor = 1.10 if (pa or 0) > threshold else 1.00
        prior_based = round(factor * pt, 2)
        required = min(pct90, prior_based)
        basis = (f"lesser of 90% of current ({pct90:,.2f}) and "
                 f"{factor:.0%} of prior-year tax ({prior_based:,.2f})")
        if pa is None:
            notes.append(
                "prior_year_agi unavailable — assumed at or below the "
                f"${threshold:,.0f} threshold (100% factor); if prior AGI "
                "exceeded it the 110% harbor applies and the penalty here "
                "may be UNDERSTATED")
    result["required_annual_payment"] = round(required, 2)
    result["required_basis"] = basis

    if wh >= required:
        result["no_penalty"] = True
        result["no_penalty_reason"] = (
            "withholding alone meets the required annual payment — no "
            "Form 2210 needed")
        return result

    # §7503 rolls the INSTALLMENT due dates forward over weekends/DC legal
    # holidays — the printed Form 2210 columns use the rolled dates and
    # accrual starts there (TY2025 Q2 is due Jun 16; TY2023 Q4 is Jan 16 2024
    # because Jan 15 is MLK Day). The accrual STOP stays statutory Apr 15:
    # the 2210 penalty worksheet ends every period at April 15 even in years
    # when filing day itself rolls (e.g. 2023's Apr 18).
    due_dates = [next_business_day(datetime.date(tax_year, 4, 15)),
                 next_business_day(datetime.date(tax_year, 6, 15)),
                 next_business_day(datetime.date(tax_year, 9, 15)),
                 next_business_day(datetime.date(tax_year + 1, 1, 15))]
    final_date = datetime.date(tax_year + 1, 4, 15)
    per_installment = required / 4.0

    # Timeline simulation per the Part III worksheet: charges open
    # underpayment slices; payments (deemed-even withholding on each due
    # date + dated estimates) retire slices oldest-first; excess sits as
    # credit against future installments. Charges sort before same-date
    # payments so a timely payment retires its installment at zero days.
    timeline = [(d, 0, per_installment) for d in due_dates]
    timeline += [(d, 1, wh / 4.0) for d in due_dates]
    timeline += [(d, 1, a) for d, a in est_events]
    timeline.sort(key=lambda t: (t[0], t[1]))

    open_slices = []   # [due_date, remaining]
    closed = []        # (amount, accrual_start, accrual_end)
    credit = 0.0
    for dt, kind, amt in timeline:
        if kind == 0:
            c = amt - min(credit, amt)
            credit -= min(credit, amt)
            if c > 1e-9:
                open_slices.append([dt, c])
        else:
            a = amt
            while a > 1e-9 and open_slices:
                s = open_slices[0]
                pay = min(a, s[1])
                # §6654(b)(2): the penalty period ends at the EARLIER of the
                # payment date or Apr 15 of tax_year+1 — a payment made after
                # the statutory stop retires the balance but never extends
                # the accrual.
                closed.append((pay, s[0], min(dt, final_date)))
                s[1] -= pay
                a -= pay
                if s[1] <= 1e-9:
                    open_slices.pop(0)
            credit += a
    for s in open_slices:
        closed.append((s[1], s[0], final_date))

    # Per-quarter display: only underpayment that PERSISTED past the due
    # date (a charge retired by a same-day payment never accrues and would
    # read as a false alarm).
    persisting = {d: 0.0 for d in due_dates}
    for amount, start, end in closed:
        if end > start:
            persisting[start] += amount
    result["quarters"] = [
        {"due_date": d.isoformat(),
         "required_installment": round(per_installment, 2),
         "underpayment": round(persisting[d], 2)}
        for d in due_dates]

    if any(d > final_date for d, _ in est_events):
        notes.append(
            f"estimated payment(s) dated after Apr 15 {tax_year + 1} cannot "
            "reduce this year's penalty — verify the date(s) (an off-by-one "
            "YEAR typo in the other direction hard-errors; this direction "
            "only forfeits credit)")

    accruing = [(a, s, e) for a, s, e in closed if e > s]
    if not accruing:
        # Every installment was covered by its due date — $0 penalty needs
        # no rate timeline (a fetched-late cache must not block a return
        # that owes nothing).
        result["penalty"] = 0.0
        result["penalty_computed"] = True
        return result

    periods = _f2210_rate_periods(underpayment_rates)
    if not periods:
        notes.append(
            "federal.underpayment_rates is unfetched — a penalty APPLIES on "
            "the quarterly underpayments above but the dollar amount is NOT "
            "computed; fetch the IRC §6621 quarterly rates via /tax-setup, "
            "or file without line 38 and let the IRS bill it")
        return result

    earliest = min(s for _, s, _ in accruing)
    if periods[0][0] > earliest:
        raise ValueError(
            f"underpayment_rates start {periods[0][0].isoformat()}, after "
            f"the earliest accruing underpayment ({earliest.isoformat()}) — "
            "the rate timeline must cover the whole accrual span")
    # The accrual span runs through Apr 15 of tax_year+1 and §6621 resets
    # quarterly — a timeline whose last entry predates Apr 1 of tax_year+1
    # is extrapolating the final segment (Jan-1-only coverage would silently
    # price Apr 1-15 at the Q1 rate).
    if periods[-1][0] < datetime.date(tax_year + 1, 4, 1):
        notes.append(
            f"underpayment_rates' last change is {periods[-1][0].isoformat()} "
            f"— rate periods through Apr 15 {tax_year + 1} assume that rate "
            "persists (§6621 resets quarterly; the Apr 1 rate is not in the "
            "cache); re-fetch the quarterly rates near filing")
    penalty = sum(_f2210_accrue(amount, start, end, periods)
                  for amount, start, end in accruing)
    result["penalty"] = round(penalty, 2)
    result["penalty_computed"] = True
    return result


def check_form_8938_required(
    accounts: list[dict],
    filing_status: str,
    lives_abroad: bool = False,
    form_8938_thresholds: dict = None,
) -> dict:
    """Check if Form 8938 filing is required.

    Per IRS rules, MFJ has its own thresholds; all other statuses (single,
    MFS, HoH) use the "single" thresholds.

    Args:
        accounts: List of {"year_end_value_usd": float, "max_value_usd": float}.
                  Values can be strings like "$5,000" — they will be coerced to float.
        filing_status: "single", "mfj", "mfs", "hoh".
        lives_abroad: Whether taxpayer lives outside the US.
        form_8938_thresholds: Thresholds from tax_law JSON (federal.form_8938_thresholds).
                              If None, uses IRS default thresholds as fallback.

    Returns:
        Dict with threshold analysis.

    Raises:
        ValueError: If threshold dict has wrong keys or None values.
    """
    if accounts is None:
        raise ValueError("accounts cannot be None. Pass an empty list [] if no foreign accounts.")

    _valid_statuses = {"single", "mfj", "mfs", "hoh"}
    if filing_status not in _valid_statuses:
        raise ValueError(
            f"Invalid filing_status '{filing_status}'. "
            f"Must be one of {sorted(_valid_statuses)}."
        )

    # Use tax_law JSON thresholds if provided, otherwise fall back to IRS defaults
    if form_8938_thresholds is not None:
        abroad_suffix = "_foreign" if lives_abroad else "_us"
        # IRS Form 8938 thresholds: MFJ has its own; all others (single, MFS, HoH) use "single"
        fs_key = "mfj" if filing_status == "mfj" else "single"
        threshold_key = f"{fs_key}{abroad_suffix}"
        threshold = form_8938_thresholds.get(threshold_key, {})
        # Validate threshold structure
        if "year_end" not in threshold or "any_time" not in threshold:
            raise ValueError(
                f"form_8938_thresholds['{threshold_key}'] must contain 'year_end' and 'any_time' keys. "
                f"Got keys: {list(threshold.keys())}. "
                f"Available top-level keys: {list(form_8938_thresholds.keys())}. "
                "Expected structure: {'mfj_us': {'year_end': 200000, 'any_time': 300000}, ...}"
            )
        if threshold["year_end"] is None or threshold["any_time"] is None:
            raise ValueError(
                f"Form 8938 thresholds for '{threshold_key}' contain None values — "
                "not yet populated from tax_law JSON. Run /tax-setup to fetch."
            )
    else:
        # Fallback: hardcoded IRS defaults (stable since FATCA inception)
        _defaults = {
            "mfj": {"year_end": 200000, "any_time": 300000} if not lives_abroad
                    else {"year_end": 400000, "any_time": 600000},
            "single": {"year_end": 50000, "any_time": 75000} if not lives_abroad
                      else {"year_end": 200000, "any_time": 300000},
        }
        fs_key = "mfj" if filing_status == "mfj" else "single"
        threshold = _defaults[fs_key]

    total_year_end = 0.0
    total_max = 0.0
    coercion_failures = []
    for i, acct in enumerate(accounts):
        raw_ye = acct.get("year_end_value_usd", 0)
        raw_max = acct.get("max_value_usd", 0)
        ye_val = _coerce_numeric(raw_ye, default=None)
        max_val = _coerce_numeric(raw_max, default=None)
        if ye_val is None or max_val is None:
            coercion_failures.append(
                f"account[{i}]: year_end={raw_ye!r}, max={raw_max!r}"
            )
            continue
        total_year_end += ye_val
        total_max += max_val
    if coercion_failures:
        raise ValueError(
            f"check_form_8938_required: {len(coercion_failures)} account(s) have "
            f"non-numeric values — cannot determine FATCA filing requirement: "
            + "; ".join(coercion_failures)
        )

    required = total_year_end > threshold["year_end"] or total_max > threshold["any_time"]

    return {
        "filing_status": filing_status,
        "lives_abroad": lives_abroad,
        "threshold_year_end": threshold["year_end"],
        "threshold_any_time": threshold["any_time"],
        "total_year_end": round(total_year_end, 2),
        "total_max": round(total_max, 2),
        "required": required,
    }


# Normalized income inputs a scenario operates on. Keys map 1:1 to baseline
# extraction in scenario.py. "capital_gains" is net LTCG (preferential); the
# qualified-dividend portion is also preferential. Everything else is ordinary.
# Payments (withholding/estimates) are intentionally absent — they shift the
# balance due, not the tax, and v1 reports only the tax effect.
SCENARIO_INPUT_KEYS = (
    "wages", "taxable_interest", "ordinary_dividends", "qualified_dividends",
    "capital_gains", "ira_distribution", "self_employment_net", "other_income",
)


def compute_scenario(
    baseline: dict,
    delta: dict,
    *,
    brackets: list[dict],
    standard_deduction: float,
    cg_zero_limit: float = None,
    cg_fifteen_limit: float = None,
    ss_wage_base: float = None,
    qbi_rate: float = 0.20,
    num_ctc_children: int = 0,
    ctc_per_child: float = None,
    ctc_phase_out_start: float = None,
    addl_medicare_threshold: float = None,
    addl_medicare_rate: float = 0.009,
    capital_loss_limit: float = CAPITAL_LOSS_LIMIT,
) -> dict:
    """Recompute the federal tax outcome for a normalized income set + a signed delta.

    Pure and rates-parameterized — the keystone shared by /tax-scenario (#3) and the
    Roth conversion ladder (#6). It is a SIMPLIFIED federal recompute (it does not
    reproduce every credit/schedule of a filed return); callers anchor the absolute
    figure to the real result.json line 24 and use only the *difference* between two
    compute_scenario calls. Mirrors projection.project_federal_tax so scenario numbers
    stay consistent with the rest of the toolkit.

    Args:
        baseline: Normalized income inputs (keys in SCENARIO_INPUT_KEYS). Missing keys
                  default to 0.
        delta: Signed additive adjustments (same key space). Applied on top of baseline.
        brackets: Ordinary-income brackets for the filing status.
        standard_deduction: Standard deduction for the filing status.
        cg_zero_limit, cg_fifteen_limit: QDCG 0%/15% thresholds (binary status key). When
                  either is None, preferential income is taxed at ordinary rates instead.
        ss_wage_base: SS wage base for SE tax; None falls back to the flat SE rate.
        qbi_rate: Sec 199A rate.
        num_ctc_children, ctc_per_child, ctc_phase_out_start: CTC params; credit is 0
                  unless num_ctc_children and ctc_per_child are both set.
        addl_medicare_threshold, addl_medicare_rate: Form 8959 params; skipped when
                  threshold is None.
        capital_loss_limit: Max net capital loss deductible against ordinary income
                  ($3,000; pass $1,500 for MFS).

    Returns:
        Dict with the federal outcome (all rounded to 2 dp):
        gross_income, adjustments, agi, taxable_income, tax_before_credits, ctc,
        se_tax, additional_medicare, total_federal_tax, used_qdcg.

    Raises:
        ValueError: If any present baseline/delta value is non-numeric (including
            bool), or if brackets are empty (propagated from compute_bracket_tax).
    """
    def merged(key):
        # Missing keys default to 0, but a PRESENT non-numeric value (e.g. a
        # hand-edited/corrupted stacked scenario inputs block) is a hard error —
        # silently coercing it to 0 would compute a materially wrong tax. Consistent
        # with the anchor-numeric validation in scenario.resolve_base.
        try:
            b = _coerce_numeric(baseline.get(key, 0), default=None)
            d = _coerce_numeric(delta.get(key, 0), default=None)
        except TypeError:  # bool is never a valid amount
            raise ValueError(f"scenario input {key!r} must be numeric, got bool")
        if b is None or d is None:
            raise ValueError(f"scenario input {key!r} is not numeric")
        return b + d

    wages = merged("wages")
    taxable_interest = merged("taxable_interest")
    ordinary_dividends = merged("ordinary_dividends")
    qualified_dividends = merged("qualified_dividends")
    capital_gains = merged("capital_gains")
    ira_distribution = merged("ira_distribution")
    se_net = merged("self_employment_net")
    other_income = merged("other_income")
    # OBBBA Schedule 1-A (line 13b) is a fixed below-AGI deduction for this
    # return — NOT a delta-able income field, so it's read straight from the
    # baseline (constant across the $0-delta and the scenario, so the anchored
    # delta is unchanged in magnitude but priced from the correct, lower
    # taxable-income position — matters near bracket/QDCG/credit thresholds).
    # SECOND-ORDER caveat (same class as the taxable-SS note in
    # scenario.extract_baseline_inputs): a MAGI-INCREASING delta (Roth
    # conversion, wages, cap gains) also phases OUT more of the Schedule 1-A
    # deduction near a threshold — held fixed here, so a delta crossing a
    # phase-out band is modeled slightly too cheap. Not re-derived (the
    # keystone lacks the caps/limits); surfaced in the planning skills' caveats.
    try:
        schedule_1a_deduction = _coerce_numeric(
            baseline.get("schedule_1a_deduction", 0), default=None)
    except TypeError:
        raise ValueError("scenario input 'schedule_1a_deduction' must be numeric, got bool")
    if (schedule_1a_deduction is None
            or not math.isfinite(schedule_1a_deduction)
            or schedule_1a_deduction < 0):
        raise ValueError(
            "scenario input 'schedule_1a_deduction' must be a finite "
            "non-negative number")

    if se_net > 0:
        # W-2 wages already consume the Social Security wage base via payroll, so the
        # SS portion of SE tax only applies to the remaining room. Pass wages as the
        # W-2 SS-wage figure (a planning proxy for Box 3) — matches projection.py and
        # prevents overstating SE SS tax for high-wage taxpayers.
        se = compute_se_tax(se_net, ss_wage_base=ss_wage_base, w2_ss_wages=max(0.0, wages))
    else:
        se = {"se_tax": 0.0, "deductible_half": 0.0, "se_earnings": 0.0}

    # A net capital LOSS offsets ordinary income only up to the annual limit
    # ($3,000; $1,500 MFS); the excess carries forward (not modeled here). A net GAIN
    # flows in full and also feeds the preferential QDCG bucket below.
    cap_gain_in_gross = (capital_gains if capital_gains >= 0
                         else max(capital_gains, -capital_loss_limit))
    gross = (wages + taxable_interest + ordinary_dividends + cap_gain_in_gross
             + ira_distribution + other_income + se_net)
    adjustments = se["deductible_half"]
    agi = max(0.0, gross - adjustments)

    net_ltcg = max(0.0, capital_gains)
    # Qualified dividends are a subset of ordinary dividends (which are already in
    # gross), so they can never exceed them. Clamp defensively: a malformed input
    # with qualified > ordinary must not move income into the preferential bucket
    # that does not exist in gross income (which would lower tax for free).
    eff_qualified = min(max(0.0, qualified_dividends), max(0.0, ordinary_dividends))

    # QBI needs taxable income BEFORE QBI (Form 8995 line 11) and net capital gain
    # (line 12 = qualified dividends + net LTCG) for the overall cap, so it is
    # computed after AGI and the preferential-income figures.
    ti_before_qbi = max(0.0, agi - standard_deduction - schedule_1a_deduction)
    if se_net > 0:
        # §199A(c)(4) / Reg. §1.199A-3(b)(1)(vi): net the QBI base by the
        # deductible half of SE tax (the only trade-attributable SE deduction the
        # scenario models — SE health insurance / retirement are not scenario
        # inputs). Omitting it overstates the QBI deduction and understates the
        # scenario tax for a self-employed filer.
        qbi = compute_qbi_deduction(
            se_net, 0.0, rate=qbi_rate,
            taxable_income_before_qbi=ti_before_qbi,
            net_capital_gain=eff_qualified + net_ltcg,
            half_se_tax=se["deductible_half"],
        )["total_deduction"]
    else:
        qbi = 0.0
    taxable_income = max(0.0, ti_before_qbi - qbi)
    if (eff_qualified or net_ltcg) and cg_zero_limit is not None and cg_fifteen_limit is not None:
        qd = compute_qdcg_tax(taxable_income, eff_qualified, net_ltcg,
                              brackets, cg_zero_limit, cg_fifteen_limit)
        tax_before_credits = qd["final_tax"]
        used_qdcg = qd["used_qdcg_worksheet"]
    else:
        tax_before_credits, _ = compute_bracket_tax(taxable_income, brackets)
        used_qdcg = False

    if num_ctc_children and ctc_per_child:
        ctc = compute_child_tax_credit(
            num_ctc_children, ctc_per_child, agi, ctc_phase_out_start or 0
        )["credit_allowed"]
    else:
        ctc = 0.0

    if addl_medicare_threshold is not None:
        # Floor wages at 0 (mirrors the SE-tax call): a wage-reducing delta can drive
        # merged wages negative, and compute_additional_medicare rejects negative wages.
        addl = compute_additional_medicare(
            max(0.0, wages), se["se_earnings"], addl_medicare_threshold, 0,
            rate=addl_medicare_rate,
        )["additional_tax"]
    else:
        addl = 0.0

    total = max(0.0, tax_before_credits - ctc) + se["se_tax"] + addl

    return {
        "gross_income": round(gross, 2),
        "adjustments": round(adjustments, 2),
        "agi": round(agi, 2),
        "taxable_income": round(taxable_income, 2),
        "tax_before_credits": round(tax_before_credits, 2),
        "ctc": round(ctc, 2),
        "se_tax": round(se["se_tax"], 2),
        "additional_medicare": round(addl, 2),
        "total_federal_tax": round(total, 2),
        "used_qdcg": used_qdcg,
    }



# ---------------------------------------------------------------------------
# Form 8863 — Education credits (AOTC + LLC), §25A
# ---------------------------------------------------------------------------
# Statutory-fixed parameters (NOT indexed — hardcoded per the ACTC-floor
# precedent). §25A(b): AOTC = 100% of the first $2,000 of qualified expenses
# + 25% of the next $2,000, per student. §25A(c): LLC = 20% of up to $10,000
# of expenses PER RETURN. §25A(i)(5)/(6): 40% of the AOTC is refundable
# unless the taxpayer is a child subject to the §1(g) kiddie conditions.
# §25A(d) (post-CAA-2021, both credits): phase-out over MAGI $80K-$90K
# (single/HoH/QSS) / $160K-$180K (MFJ); MFS may claim NEITHER credit.
_AOTC_TIER1_EXPENSES = 2000.0
_AOTC_TIER1_RATE = 1.00
_AOTC_TIER2_EXPENSES = 2000.0
_AOTC_TIER2_RATE = 0.25
_AOTC_REFUNDABLE_FRACTION = 0.40
_LLC_EXPENSE_CAP = 10000.0
_LLC_RATE = 0.20
# NOTE the status grouping is NOT the usual binary map: Form 8863 groups QSS
# with Single/HoH at $80K-$90K (only MFJ gets the doubled range) — mapping
# QSS→mfj here would phase a QSS filer out too LATE and overstate the credit.
_ED_PHASEOUT = {
    "SINGLE": (80000.0, 90000.0),
    "HOH": (80000.0, 90000.0),
    "QSS": (80000.0, 90000.0),
    "MFJ": (160000.0, 180000.0),
}


def compute_education_credits(
    magi: float,
    filing_status: str,
    students: list,
    tax_liability: float = None,
    no_refundable_aotc: bool = False,
) -> dict:
    """Form 8863 education credits — AOTC (per student) + LLC (per return).

    Every AOTC-eligible student takes the AOTC: its 100%/25% tiers strictly
    dominate the LLC's 20% at every expense level and the AOTC is per-student
    while the LLC's $10,000 cap is per-return, so the assignment needs no
    search. Non-eligible students' expenses pool into the LLC.

    Args:
        magi: Modified AGI (AGI + FEIE/foreign-housing/Puerto-Rico exclusions
            — MAGI≈AGI for domestic filers).
        filing_status: canonical Single/MFJ/MFS/HoH/QSS. MFS → neither credit
            (§25A(g)(6)); QSS phases out on the SINGLE range (Form 8863).
        students: list of dicts, each with:
            qualified_expenses: adjusted qualified education expenses —
                tuition + required fees (+ course materials for AOTC), NET of
                tax-free scholarships/grants, employer assistance, and
                529/Coverdell earnings used (the caller nets these — Pub 970
                coordination is the caller's worksheet). MUST belong to the
                taxpayer, spouse, or a dependent CLAIMED on this return
                (§25A(f)(1)(A)) — the caller screens claimants; a dependent
                student's own payments count as the parent's (§25A(g)(3)).
            aotc_eligible: bool — degree program, at least half-time, within
                the first 4 years, AOTC claimed fewer than 4 prior years, no
                felony drug conviction (all user-asserted facts).
            name: optional label echoed in the assignment.
        tax_liability: Credit Limit Worksheet amount (Form 1040 line 18 minus
            the nonrefundable credits claimed BEFORE education credits —
            currently just the FTC in this pipeline). None skips the limit
            (potential-credit behavior).
        no_refundable_aotc: Form 8863 line 7 checkbox — a taxpayer who was
            under 24 with the §25A(i)(5) kiddie conditions gets NO refundable
            AOTC (the 40% slice folds back into the nonrefundable pot).

    Returns dict with allowed, aotc_tentative, llc_tentative, phase-out
    ratios (FULL precision — compliant with the form's "at least three
    places" rounding rule and matches tax software), refundable_aotc
    (Form 1040 line 29),
    nonrefundable_credit (Schedule 3 line 3, tax-limited when tax_liability
    given), per-student assignment, and notes.
    """
    magi_v = _f2210_amount(magi, "magi", allow_negative=True)
    fs = _normalize_filing_status(filing_status)
    if not isinstance(no_refundable_aotc, bool):
        raise ValueError("no_refundable_aotc must be a bool")
    if tax_liability is not None:
        tax_liability = _f2210_amount(tax_liability, "tax_liability")
    if not isinstance(students, list) or not students:
        raise ValueError("students must be a non-empty list of student dicts")

    notes = []
    if fs == "MFS":
        return {
            "allowed": False,
            "aotc_tentative": 0.0, "llc_tentative": 0.0,
            "phase_out_ratio": 0.0,
            "refundable_aotc": 0.0, "aotc_nonrefundable": 0.0,
            "llc_after_phaseout": 0.0, "nonrefundable_credit": 0.0,
            "assignment": [],
            "notes": ["MFS may claim neither education credit (§25A(g)(6))."],
        }

    lower, upper = _ED_PHASEOUT[fs]
    if magi_v >= upper:
        ratio = 0.0
    elif magi_v <= lower:
        ratio = 1.0
    else:
        ratio = (upper - magi_v) / (upper - lower)
    if 0.0 < ratio < 1.0:
        notes.append(
            f"MAGI {magi_v:,.0f} is inside the {lower:,.0f}-{upper:,.0f} "
            f"phase-out — both credits scaled by {ratio:.4f} (full "
            f"precision; Form 8863 rounds to 3 decimals)."
        )
    elif ratio == 0.0:
        notes.append(
            f"MAGI {magi_v:,.0f} is at or above {upper:,.0f} — both "
            f"education credits fully phased out."
        )

    aotc_tentative = 0.0
    llc_pool = 0.0
    assignment = []
    for i, st in enumerate(students):
        if not isinstance(st, dict):
            raise ValueError(f"students[{i}] must be a dict")
        exp = _f2210_amount(st.get("qualified_expenses"),
                            f"students[{i}].qualified_expenses")
        eligible = st.get("aotc_eligible")
        if not isinstance(eligible, bool):
            raise ValueError(
                f"students[{i}].aotc_eligible must be a bool (the AOTC "
                f"eligibility facts are user-asserted — absence is not 'no')")
        label = st.get("name") or f"student {i + 1}"
        if eligible:
            credit = (_AOTC_TIER1_RATE * min(exp, _AOTC_TIER1_EXPENSES)
                      + _AOTC_TIER2_RATE * max(
                          0.0, min(exp, _AOTC_TIER1_EXPENSES
                                   + _AOTC_TIER2_EXPENSES)
                          - _AOTC_TIER1_EXPENSES))
            aotc_tentative += credit
            if exp < _AOTC_TIER1_EXPENSES + _AOTC_TIER2_EXPENSES:
                # Within-year dominance over the LLC still holds, but §25A
                # caps AOTC at 4 LIFETIME years per student and the credit
                # is elective — a low-expense year burns a claim worth up
                # to $2,500 in a full year.
                notes.append(
                    f"{label}: AOTC claimed on {exp:,.0f} of expenses "
                    f"(under the $4,000 that earns the full $2,500) — this "
                    f"consumes one of the student's 4 lifetime AOTC years; "
                    f"consider deferring (set aotc_eligible false) if "
                    f"future years will have higher expenses.")
            assignment.append({"name": label, "credit": "AOTC",
                               "expenses_used": round(min(exp, 4000.0), 2),
                               "tentative": round(credit, 2)})
        else:
            llc_pool += exp
            assignment.append({"name": label, "credit": "LLC",
                               "expenses_used": round(exp, 2),
                               "tentative": None})

    llc_tentative = _LLC_RATE * min(llc_pool, _LLC_EXPENSE_CAP)
    if llc_pool > _LLC_EXPENSE_CAP:
        notes.append(
            f"LLC expense pool {llc_pool:,.0f} exceeds the $10,000 "
            f"per-RETURN cap — {llc_pool - _LLC_EXPENSE_CAP:,.0f} of "
            f"expenses earn no credit.")

    aotc_after = aotc_tentative * ratio
    llc_after = llc_tentative * ratio

    if no_refundable_aotc:
        refundable = 0.0
        aotc_nonref = aotc_after
        if aotc_after > 0:
            notes.append(
                "Form 8863 line 7 checkbox: taxpayer meets the §25A(i)(5) "
                "under-24 conditions — the 40% refundable slice folds back "
                "into the nonrefundable credit.")
    else:
        refundable = aotc_after * _AOTC_REFUNDABLE_FRACTION
        aotc_nonref = aotc_after - refundable

    nonref_total = aotc_nonref + llc_after
    if tax_liability is not None and nonref_total > tax_liability:
        notes.append(
            f"Nonrefundable education credits limited by tax: "
            f"{nonref_total:,.2f} tentative vs {tax_liability:,.2f} "
            f"available — the excess is NOT refundable and does not carry "
            f"forward.")
        nonref_total = tax_liability

    return {
        "allowed": True,
        "aotc_tentative": round(aotc_tentative, 2),
        "llc_tentative": round(llc_tentative, 2),
        "phase_out_ratio": ratio,
        "refundable_aotc": round(refundable, 2),
        "aotc_nonrefundable": round(aotc_nonref, 2),
        "llc_after_phaseout": round(llc_after, 2),
        "nonrefundable_credit": round(nonref_total, 2),
        "assignment": assignment,
        "notes": notes,
    }


def compute_late_filing_penalty(
    unpaid_tax: float,
    ftf_months: int,
    ftp_months: int,
    filed_over_60_days_late: bool = False,
    min_penalty_floor: float = None,
    ftf_rate: float = 0.05,
    ftp_rate: float = 0.005,
    ftf_cap: float = 0.25,
    ftp_cap: float = 0.25,
) -> dict:
    """§6651 failure-to-file (FTF) + failure-to-pay (FTP) penalties.

    NOT interest — §6601 interest on the balance (and on the penalties
    themselves) is separate and NOT computed here (it needs the quarterly
    underpayment rates and daily compounding; see compute_form_2210 for the
    rate machinery). This is the additions-to-tax only.

    Rules encoded:
    - §6651(a)(1) FTF: `ftf_rate` (5%) per month or fraction the return is late,
      capped at `ftf_cap` (25% → 5 months of accrual).
    - §6651(a)(2) FTP: `ftp_rate` (0.5%) per month or fraction the tax is unpaid,
      capped at `ftp_cap` (25% → 50 months).
    - §6651(c)(1) overlap: in any month BOTH apply, the FTF penalty is reduced by
      that month's FTP penalty (so FTF nets to 4.5%/month while both run). The
      reduction covers the overlapping months during which FTF is still accruing
      (at most 5). Classic result: 5 months late, both running → FTF 22.5% + FTP
      2.5% = 25% combined, not 27.5%.
    - §6651(a) minimum (return filed > 60 days late): the FTF penalty is at least
      the LESSER of `min_penalty_floor` or 100% of the tax due, applied to the
      FTF piece. `min_penalty_floor` is the inflation-indexed §6651(a) dollar
      amount for the year the return is REQUIRED TO BE FILED — it is passed in
      and MUST be fetched from that year's IRS Revenue Procedure (e.g. Rev. Proc.
      2024-40 for returns required to be filed in 2025, Rev. Proc. 2025-32 for
      returns required to be filed in 2026), NOT hardcoded (it indexes annually —
      do not reuse a prior year's figure, which would understate the mandatory
      minimum).

    Base: `unpaid_tax` is the net tax due as of the deadline — the tax required
    to be shown MINUS payments/credits made by the due date (§6651(b)(1)). Both
    penalties run on this net amount, so a fully-paid-but-late return (unpaid_tax
    = 0) carries $0 of both (and $0 minimum — the minimum is a floor on the FTF
    penalty, which is measured on the net amount due).

    Months are whole-month counts (each month OR FRACTION thereof = one whole
    month) — the caller supplies the counted months for filing and for payment
    separately (they differ when the return is filed but the tax stays unpaid).

    Scope (explicit v1 limits — surfaced in the returned `note`):
    - CORRECTLY-REPORTED RETURN ASSUMED: `unpaid_tax` is used as the base for
      BOTH penalties, which is right only when the return reports the full tax
      required. For an UNDER-reported return the bases diverge — §6651(a)(1) FTF
      is on the tax REQUIRED to be shown (§6651(b)(1)) while §6651(a)(2) FTP is on
      the tax SHOWN on the return (§6651(b)(2)), and the unreported deficiency
      follows the separate §6651(a)(3) path after notice-and-demand. That case is
      NOT modeled here — use a full §6651 computation with the separate bases.
    - PARTIAL-PAYMENT SCHEDULE NOT MODELED: both penalties run on the single
      constant `unpaid_tax`. A post-deadline PARTIAL PAYMENT stops FTP on the paid
      portion but, per §6651(b)(1), does NOT shrink the FTF base (fixed at the tax
      unpaid on/before the due date). Because FTF, the declining FTP base, and the
      §6651(c)(1) overlap offset interact across months, a mid-period payment
      cannot be reconstructed by re-running this helper per balance segment —
      compute that case from the §6651 worksheet / tax software.
    - SINGLE FTP RATE (mixed pre/post-notice NOT modeled): §6651(d) raises FTP
      from 0.5% to 1%/month for months beginning >10 days after an IRS notice-and-
      demand / intent-to-levy. This helper applies ONE `ftp_rate` uniformly — for
      a wholly post-notice balance pass `ftp_rate=0.01`; a mid-period rate change
      while FTF is still accruing cannot be reconstructed by summing separate
      calls (each restarts the 5-month FTF cap) — use the §6651 worksheet.
    - The 90%-paid Form 4868 extension exception (no FTP during the extension
      period) is the CALLER's screen — do not pass FTP months for a filer who
      met it (see /tax-extend).

    Raises:
        ValueError: on non-finite / negative / wrong-type inputs, or a rate
            outside (0, 1].
    """
    # --- base ---
    if isinstance(unpaid_tax, bool):
        raise ValueError("unpaid_tax must be a number, not bool")
    ut = _coerce_numeric(unpaid_tax, default=None)
    if ut is None or not math.isfinite(ut):
        raise ValueError(f"unpaid_tax must be a finite number, got {unpaid_tax!r}")
    if ut < 0:
        raise ValueError(f"unpaid_tax cannot be negative (got {ut})")

    # --- month counts (whole months; part-month already counted as full) ---
    for _name, _m in (("ftf_months", ftf_months), ("ftp_months", ftp_months)):
        if isinstance(_m, bool) or not isinstance(_m, int):
            raise ValueError(f"{_name} must be a non-negative int, got {_m!r}")
        if _m < 0:
            raise ValueError(f"{_name} cannot be negative (got {_m})")

    if not isinstance(filed_over_60_days_late, bool):
        raise ValueError("filed_over_60_days_late must be a bool")

    # --- rates: validate AND store the coerced floats so the arithmetic below
    # uses numbers (a coercible "0.05" must not pass validation and then raise
    # TypeError at the multiply — the documented contract is ValueError). ---
    _rates = {}
    for _name, _r in (("ftf_rate", ftf_rate), ("ftp_rate", ftp_rate),
                      ("ftf_cap", ftf_cap), ("ftp_cap", ftp_cap)):
        if isinstance(_r, bool):
            raise ValueError(f"{_name} must be a number, not bool")
        _rv = _coerce_numeric(_r, default=None)
        if _rv is None or not math.isfinite(_rv) or _rv <= 0 or _rv > 1:
            raise ValueError(f"{_name} must be a finite fraction in (0, 1], got {_r!r}")
        _rates[_name] = _rv
    ftf_rate = _rates["ftf_rate"]
    ftp_rate = _rates["ftp_rate"]
    ftf_cap = _rates["ftf_cap"]
    ftp_cap = _rates["ftp_cap"]

    # --- FTF (gross, capped) ---
    ftf_pct = min(ftf_months * ftf_rate, ftf_cap)
    ftf_gross = round(ftf_pct * ut, 2)

    # --- FTP (capped) ---
    ftp_pct = min(ftp_months * ftp_rate, ftp_cap)
    ftp_penalty = round(ftp_pct * ut, 2)

    # --- §6651(c)(1) overlap: reduce FTF by FTP for months both run, but only
    # for the months FTF actually accrues. FTF accrues until it hits the cap;
    # the number of accrual months is CEIL(cap/rate) — the final month in which
    # the cap is reached still counts (and still overlaps a concurrent FTP
    # month), so a non-integer cap/rate (via overrides, e.g. 0.25/0.06 = 4.17)
    # rounds UP to 5, not `round`-to-4 (which would under-count the offset and
    # overstate the penalty). round(...,9) first kills float noise so an exact
    # 0.25/0.05 = 5.0 doesn't ceil to 6. ---
    ftf_accrual_months = min(ftf_months, math.ceil(round(ftf_cap / ftf_rate, 9)))
    overlap_months = min(ftf_accrual_months, ftp_months)
    # The §6651(c)(1) reduction is the FTP that ACTUALLY ACCRUED in the
    # overlapping months — it cannot exceed the FTP cap. Without this clamp a
    # `ftp_cap` override below `ftp_rate * overlap_months` (e.g. ftp_rate=0.10,
    # ftp_cap=0.10, 5 months) would subtract more FTP than was ever charged and
    # wipe out too much of the FTF penalty.
    # Round the reduction at source too, so the returned FTF breakdown
    # reconciles: ftf_penalty = ftf_penalty_gross − overlap_reduction exactly
    # (both operands already 2-dp) rather than deriving it from unrounded values.
    overlap_reduction = round(min(ftp_rate * overlap_months, ftp_cap) * ut, 2)
    # The percentage FTF (before any >60-day minimum). Kept as its own field so
    # the breakdown identity `ftf_penalty_gross − overlap_reduction ==
    # ftf_penalty_before_minimum` ALWAYS holds, even when the minimum overrides
    # the final ftf_penalty below.
    ftf_penalty_before_minimum = max(0.0, round(ftf_gross - overlap_reduction, 2))
    ftf_penalty = ftf_penalty_before_minimum

    # --- §6651(a) minimum for a return filed > 60 days late ---
    minimum_penalty = None
    minimum_penalty_applied = False
    min_note = None
    if filed_over_60_days_late:
        if min_penalty_floor is None:
            min_note = (
                "Return filed >60 days late: the §6651(a) minimum penalty (lesser "
                "of the indexed dollar floor or 100% of tax due) is NOT applied "
                "because the indexed floor was not supplied — the FTF penalty may "
                "be understated for a small balance. Provide min_penalty_floor.")
        else:
            if isinstance(min_penalty_floor, bool):
                raise ValueError("min_penalty_floor must be a number, not bool")
            floor = _coerce_numeric(min_penalty_floor, default=None)
            if floor is None or not math.isfinite(floor) or floor < 0:
                raise ValueError(
                    f"min_penalty_floor must be a finite non-negative number, "
                    f"got {min_penalty_floor!r}")
            minimum_penalty = min(floor, ut)  # lesser of floor or 100% of tax due
            if minimum_penalty > ftf_penalty:
                ftf_penalty = minimum_penalty
                minimum_penalty_applied = True

    # Round the two components and derive the total FROM the rounded values so
    # the displayed breakdown always reconciles (ftf + ftp == total) — rounding
    # the total independently from the unrounded pieces can drift a cent.
    ftf_penalty = round(ftf_penalty, 2)
    ftp_penalty = round(ftp_penalty, 2)
    total_penalty = round(ftf_penalty + ftp_penalty, 2)
    note = (
        "CORRECTLY-REPORTED RETURN ASSUMED: `unpaid_tax` is the base for BOTH "
        "penalties — correct only when the return shows the full tax required. An "
        "UNDER-reported return diverges (FTF on the tax REQUIRED per §6651(b)(1), "
        "FTP on the tax SHOWN per §6651(b)(2), the deficiency via §6651(a)(3) "
        "after notice) and is NOT modeled — use a full §6651 computation. "
        "PARTIAL-PAYMENT SCHEDULE NOT MODELED: this single-call helper runs both "
        "penalties on one constant `unpaid_tax`. A post-deadline PARTIAL PAYMENT "
        "stops FTP on the paid portion but, per §6651(b)(1), does NOT shrink the "
        "FTF base (fixed at the tax unpaid ON OR BEFORE the due date). Because "
        "FTF, the declining FTP base, and the §6651(c)(1) overlap offset interact "
        "across months, a mid-period payment cannot be reconstructed by re-running "
        "this helper per balance segment — compute that case from the §6651 "
        "worksheet or tax software instead of this estimate. "
        "SINGLE FTP RATE — MIXED PRE/POST-NOTICE NOT MODELED: §6651(d) raises the "
        "FTP rate from 0.5% to 1%/month for months beginning >10 days after an IRS "
        "notice-and-demand / intent-to-levy. This helper applies ONE `ftp_rate` "
        "uniformly — the default 0.5% UNDERstates a wholly post-notice balance "
        "(for that case pass ftp_rate=0.01). A MID-PERIOD rate change while FTF is "
        "still accruing cannot be reconstructed by summing separate calls (each "
        "call restarts the 5-month FTF cap) — compute that from the §6651 "
        "worksheet / tax software. "
        "§6601 interest on the unpaid tax and on these penalties is separate and "
        "NOT included. Penalties may be abated for reasonable cause / first-time "
        "abatement (Form 843 / §6651(a) reasonable-cause). Fraudulent failure to "
        "file (§6651(f)) raises the FTF rate to 15%/mo (75% cap) and is not "
        "modeled.")
    if min_note:
        note = min_note + " " + note

    return {
        "unpaid_tax": round(ut, 2),
        "ftf_months": ftf_months,
        "ftp_months": ftp_months,
        "ftf_penalty_gross": round(ftf_gross, 2),
        "overlap_reduction": round(overlap_reduction, 2),
        "ftf_penalty_before_minimum": round(ftf_penalty_before_minimum, 2),
        "ftf_penalty": round(ftf_penalty, 2),
        "ftp_penalty": round(ftp_penalty, 2),
        "minimum_penalty": round(minimum_penalty, 2) if minimum_penalty is not None else None,
        "minimum_penalty_applied": minimum_penalty_applied,
        "total_penalty": round(total_penalty, 2),
        "note": note,
    }


if __name__ == "__main__":
    # Quick self-test with sample data
    print("=== Bracket Tax Test (MFJ 2025 OBBBA) ===")
    brackets_2025 = [
        {"rate": 0.10, "min": 0, "max": 23850},
        {"rate": 0.12, "min": 23850, "max": 96950},
        {"rate": 0.22, "min": 96950, "max": 206700},
        {"rate": 0.24, "min": 206700, "max": 394600},
        {"rate": 0.32, "min": 394600, "max": 501050},
        {"rate": 0.35, "min": 501050, "max": 751600},
        {"rate": 0.37, "min": 751600, "max": None},
    ]
    tax, detail = compute_bracket_tax(150000, brackets_2025)
    print(f"Tax on $150,000: ${tax:,.2f}")
    for d in detail:
        print(f"  {d['rate']*100:.0f}%: {d['range']} → ${d['tax']:,.2f}")

    print("\n=== Empty Brackets Test ===")
    try:
        compute_bracket_tax(100000, [])
    except ValueError as e:
        print(f"Caught expected error: {e}")

    print("\n=== SE Tax Test ===")
    se = compute_se_tax(-500)
    print(json.dumps(se, indent=2))

    print("\n=== SE Tax Threshold Test ===")
    se2 = compute_se_tax(300)
    print(json.dumps(se2, indent=2))

    print("\n=== CTC Test ===")
    ctc = compute_child_tax_credit(2, 2200, 200000, 400000)
    print(json.dumps(ctc, indent=2))

    print("\n=== Additional Medicare Test ===")
    med = compute_additional_medicare(260000, 0, 250000, 4000)
    print(json.dumps(med, indent=2))

    print("\n=== NIIT Test (MFJ, MAGI $300K, NII $20K, threshold $250K) ===")
    # excess = 300K-250K = 50K; min(20K, 50K) = 20K; 3.8% = $760
    print(f"NIIT owed: ${compute_niit(300000, 20000, 250000):,.2f}")
    print(f"NIIT below threshold: ${compute_niit(240000, 20000, 250000):,.2f}")
    print(f"NIIT no threshold (not modeled): ${compute_niit(300000, 20000, None):,.2f}")

    print("\n=== Form 8606 Pro-Rata Test (backdoor Roth trap) ===")
    # basis $7K, $50K pre-tax IRA, convert $7K → ratio 7000/57000, ~88% taxable
    f8606 = compute_form_8606(0, 7000, 50000, 0, 7000)
    print(f"Ratio: {f8606['ratio']}; taxable conversion: ${f8606['taxable_conversion']:,.2f}; "
          f"basis remaining: ${f8606['basis_remaining']:,.2f}; trap: {f8606['pro_rata_triggered']}")
    clean = compute_form_8606(0, 7000, 0, 0, 7000)
    print(f"Clean backdoor (no pre-tax): taxable conversion ${clean['taxable_conversion']:,.2f}")

    print("\n=== QBI Test ===")
    qbi = compute_qbi_deduction(-1000, 50)
    print(json.dumps(qbi, indent=2))

    print("\n=== Form 8938 String Coercion Test ===")
    f8938 = check_form_8938_required(
        [{"year_end_value_usd": "$5,000", "max_value_usd": "7500"}],
        "mfj",
    )
    print(json.dumps(f8938, indent=2))

    print("\n=== FTC Limitation Test ===")
    ftc = compute_ftc_limitation(300000, 200000, 50000, 10000)
    print(f"FTC with foreign > worldwide: ratio capped at 1.0, limitation=${ftc['limitation']:,.2f}")

    print("\n=== Scenario Test (MFJ, +$40K Roth conversion as ordinary income) ===")
    base_inputs = {"wages": 150000, "qualified_dividends": 5000, "ordinary_dividends": 5000}
    base = compute_scenario(base_inputs, {}, brackets=brackets_2025, standard_deduction=30000,
                            cg_zero_limit=96700, cg_fifteen_limit=600050)
    scen = compute_scenario(base_inputs, {"ira_distribution": 40000},
                            brackets=brackets_2025, standard_deduction=30000,
                            cg_zero_limit=96700, cg_fifteen_limit=600050)
    delta_tax = scen["total_federal_tax"] - base["total_federal_tax"]
    print(f"Baseline tax: ${base['total_federal_tax']:,.2f}; +$40K → ${scen['total_federal_tax']:,.2f}")
    print(f"Marginal cost of $40K conversion: ${delta_tax:,.2f} "
          f"({delta_tax / 40000:.1%} marginal rate)")

    print("\n=== §6651 Late-Filing/Late-Payment Penalty Test ===")
    # $10K unpaid, 5 months late (both), filed >60 days late, 2025 floor $485.
    # FTF 22.5% + FTP 2.5% = $2,250 + $250 = $2,500 (classic 25% combined).
    pen = compute_late_filing_penalty(10000, 5, 5, filed_over_60_days_late=True,
                                      min_penalty_floor=485)
    print(f"FTF ${pen['ftf_penalty']:,.2f} + FTP ${pen['ftp_penalty']:,.2f} "
          f"= ${pen['total_penalty']:,.2f} (overlap −${pen['overlap_reduction']:,.2f})")
