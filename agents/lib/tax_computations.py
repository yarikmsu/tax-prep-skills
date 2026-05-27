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

    # Form 8959 over-withholding credit logic:
    #   Regular Medicare = 1.45% of W-2 wages + 1.45% of SE earnings
    #   (SE pays 2.9% total, but for Form 8959 line 9, only the employee-equivalent
    #   half (1.45%) counts as "regular Medicare tax" for over-withholding calc)
    #   Addl Medicare withheld by employer = total withheld (Box 6) − regular Medicare
    #   Over-withholding credit = Addl Medicare withheld − Addl Medicare owed, if positive
    regular_medicare = medicare_wages * 0.0145 + se_income * 0.029 / 2
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
) -> dict:
    """Compute Child Tax Credit.

    Args:
        num_qualifying_children: Number of qualifying children under 17.
        credit_per_child: CTC amount per child.
        agi: Adjusted Gross Income.
        phase_out_start: Phase-out threshold for filing status.
        phase_out_rate: Reduction per $1,000 over threshold (default $50).

    Returns:
        Dict with CTC computation.
    Raises:
        ValueError: If any parameter is None or invalid.
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

    full_credit = num_qualifying_children * credit_per_child

    if agi > phase_out_start:
        excess = agi - phase_out_start
        # IRS: "$50 for each $1,000 (or fraction thereof)" — use ceiling, not floor
        reduction = math.ceil(excess / 1000) * phase_out_rate
        credit = max(0, full_credit - reduction)
    else:
        reduction = 0
        credit = full_credit

    return {
        "num_children": num_qualifying_children,
        "credit_per_child": credit_per_child,
        "full_credit": round(full_credit, 2),
        "agi": round(agi, 2),
        "phase_out_start": phase_out_start,
        "phase_out_reduction": round(reduction, 2),
        "credit_allowed": round(credit, 2),
    }


def compute_qbi_deduction(
    qbi_income: float,
    reit_dividends: float,
    rate: float = 0.20,
) -> dict:
    """Compute QBI deduction (Form 8995 simplified).

    Args:
        qbi_income: Qualified business income (can be negative = loss).
        reit_dividends: Section 199A REIT dividends.
        rate: QBI deduction rate (default 20%).

    Returns:
        Dict with QBI deduction.

    Raises:
        ValueError: If any parameter is None or invalid.
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

    return {
        "qbi_income": round(qbi_income, 2),
        "qbi_component": round(qbi_component, 2),
        "reit_dividends": round(reit_dividends, 2),
        "reit_component": round(reit_component, 2),
        "total_deduction": round(total_deduction, 2),
        "loss_carryforward": round(loss_carryforward, 2),
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
        qbi = compute_qbi_deduction(se_net, 0.0, rate=qbi_rate)["total_deduction"]
    else:
        se = {"se_tax": 0.0, "deductible_half": 0.0, "se_earnings": 0.0}
        qbi = 0.0

    # A net capital LOSS offsets ordinary income only up to the annual limit
    # ($3,000; $1,500 MFS); the excess carries forward (not modeled here). A net GAIN
    # flows in full and also feeds the preferential QDCG bucket below.
    cap_gain_in_gross = (capital_gains if capital_gains >= 0
                         else max(capital_gains, -capital_loss_limit))
    gross = (wages + taxable_interest + ordinary_dividends + cap_gain_in_gross
             + ira_distribution + other_income + se_net)
    adjustments = se["deductible_half"]
    agi = max(0.0, gross - adjustments)
    taxable_income = max(0.0, agi - standard_deduction - qbi)

    net_ltcg = max(0.0, capital_gains)
    # Qualified dividends are a subset of ordinary dividends (which are already in
    # gross), so they can never exceed them. Clamp defensively: a malformed input
    # with qualified > ordinary must not move income into the preferential bucket
    # that does not exist in gross income (which would lower tax for free).
    eff_qualified = min(max(0.0, qualified_dividends), max(0.0, ordinary_dividends))
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
