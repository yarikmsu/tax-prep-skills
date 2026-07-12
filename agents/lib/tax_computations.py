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

import json
import math
import sys

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


def compute_qbi_deduction(
    qbi_income: float,
    reit_dividends: float,
    rate: float = 0.20,
    taxable_income_before_qbi: float = None,
    net_capital_gain: float = None,
    threshold: float = None,
) -> dict:
    """Compute QBI deduction (Form 8995 simplified).

    Applies the Form 8995 OVERALL cap (lines 11-14): the deduction cannot exceed
    rate x (taxable income before QBI - net capital gain). This cap is on the
    simplified 8995 itself, not just 8995-A — omitting it overstates the deduction
    (understates tax) whenever business income is large relative to taxable income
    (retiree with a side business, loss-heavy year, large itemized/standard
    deduction).

    Args:
        qbi_income: Qualified business income (can be negative = loss).
        reit_dividends: Section 199A REIT dividends.
        rate: QBI deduction rate (default 20%).
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

    qbi_component = max(0, qbi_income * rate)
    reit_component = round(reit_dividends * rate, 2)
    total_deduction = qbi_component + reit_component
    loss_carryforward = min(0, qbi_income)

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
        "qbi_income": round(qbi_income, 2),
        "qbi_component": round(qbi_component, 2),
        "reit_dividends": round(reit_dividends, 2),
        "reit_component": round(reit_component, 2),
        "total_deduction": round(total_deduction, 2),
        "loss_carryforward": round(loss_carryforward, 2),
        "income_cap_applied": taxable_income_before_qbi is not None,
        "income_cap": round(income_cap, 2) if income_cap is not None else None,
        "income_limited": income_limited,
        "over_threshold": over_threshold,
    }
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
    ti_before_qbi = max(0.0, agi - standard_deduction)
    if se_net > 0:
        qbi = compute_qbi_deduction(
            se_net, 0.0, rate=qbi_rate,
            taxable_income_before_qbi=ti_before_qbi,
            net_capital_gain=eff_qualified + net_ltcg,
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
