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

import datetime
import math

# No `sys.path` bootstrap. The line that stood here counted TWO directories up
# to reach the repository root, which was right while this was
# `agents/lib/tax_computations.py` and resolves to `agents/` from inside the
# package — putting the wrong directory on the path, where a bare `lib` could
# shadow. Nothing needed it: `run.sh -m` and the package's own `__main__.py`
# both start at the root, `state/ny.py` imports the same way with no shim, and
# importing `agents.lib.irs_dates` is what fires the host-interpreter guard.
from agents.lib.secure_fs import redact

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


def state_brackets_for_status(state_cfg: dict,
                              filing_status: "str | None") -> "list[dict]":
    """The flat list of bands that `state.brackets` means for one filing status.

    TWO CACHE SHAPES, and a consumer that knows only the first computes
    nonsense for the second. California's `state.brackets` is a LIST of bands
    — one schedule, the filing status already applied when it was fetched.
    New York's is a DICT keyed by RATE SCHEDULE, because three statuses share
    one schedule and no status can be read off the bands themselves;
    `state.bracket_schedule_by_status` carries the mapping and is fetched
    beside it.

    A non-empty dict is TRUTHY, so every `state.get("brackets") or []` gate in
    this repository waved a New York cache through to code that iterates a
    list of dicts. What arrives there is the schedule KEYS, as strings:
    `AttributeError: 'str' object has no attribute 'keys'`, raised from inside
    bracket math, naming neither the cache nor a remedy. Reproduced against
    `create_state_template("NY")` before this was written.

    This is the ONE reader of `state.brackets` outside the state engine — a
    test sweeps the source for others, the way `capital_gains_limits` is
    swept, because a second reader is a second chance to know only one shape.

    Returns `[]` for a cache that carries no bands for this status, which is
    the existing "state not modeled" signal every caller already handles. A
    cache whose SHAPE cannot be read raises instead: that is corruption, and
    degrading it to "no state tax" would publish a zero liability.
    """
    brackets = (state_cfg or {}).get("brackets")
    if not brackets:
        # No bands at all — a `type: "none"` state, or a cache not fetched yet.
        # The status is not consulted because there is nothing to select with
        # it, and this arm is why the parameter is typed `str | None`: refusing
        # here would be a STOP on a question nobody asked.
        return []
    # From here there ARE bands, so which ones apply is a real question and the
    # status is the argument to it. Validated for BOTH shapes: the first draft
    # put this beside the schedule lookup, one branch further down, so a FLAT
    # bracket list returned its bands for a status that does not exist — the
    # module that had just been converted still had an arm taking anything (X8
    # review). Refusing a bad status must not depend on how one state happens
    # to key its table.
    filing_status = _normalize_filing_status(filing_status).lower()
    if isinstance(brackets, list):
        return brackets
    if not isinstance(brackets, dict):
        raise ValueError(
            "state.brackets must be a list of bands or a dict keyed by rate "
            f"schedule, got {type(brackets).__name__} — the cache is corrupted "
            "or was hand-edited; re-run /tax-setup.")
    by_status = (state_cfg or {}).get("bracket_schedule_by_status") or {}
    if not by_status:
        raise ValueError(
            "state.brackets is keyed by rate SCHEDULE but "
            "state.bracket_schedule_by_status is not fetched, so nothing here "
            "knows which schedule this filing status reads. Picking one would "
            "put a married couple on a single filer's rates; re-run "
            "/tax-setup.")
    # The lookup used the RAW argument against a lowercase-keyed cache, so the
    # canonical "MFJ" this engine's own normaliser returns missed every key and
    # was reported as a missing SCHEDULE — which reads as "re-run /tax-setup"
    # for a cache that is complete. `filing_status` is already canonical and
    # lower-cased above, so a status the cache genuinely lacks still reaches
    # the message below and a status that does not exist never gets here.
    schedule = by_status.get(filing_status)
    if schedule is None:
        raise ValueError(
            f"no state rate schedule for filing status {filing_status!r} — "
            f"known: {sorted(by_status)}")
    bands = brackets.get(schedule)
    if not bands:
        return []
    if not isinstance(bands, list):
        raise ValueError(
            f"state.brackets[{schedule!r}] must be a list of bands, got "
            f"{type(bands).__name__}")
    return bands


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

    if remaining > 0:
        # A table whose last bracket is closed — a transcription that dropped
        # the "and over" row — used to return the tax on the part below it
        # and nothing on the rest: an unbounded silent under-statement.
        raise ValueError(
            f"taxable_income exceeds the top of a closed bracket table by "
            f"{remaining:,.2f}; the last bracket's max must be null. Re-run "
            "/tax-setup to re-fetch the brackets.")
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
    threshold: float | None,
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
    netting), and projection (ST/LT forecast split) so every consumer applies
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

# §199A(b)(3)(B) / (e)(2) phase-in range — the band above the threshold over
# which the W-2/UBIA limit phases in and an SSTB phases out. YEAR-DEPENDENT:
# OBBBA §70105 expanded it for TY2026+. Statutory schedule with an optional
# cache override (federal.qbi_phase_in_range), the _resolve_amt_phaseout_rate
# precedent. Only MFJ vs non-MFJ matters (QSS→non-MFJ, like the threshold key).
_QBI_PHASE_IN_RANGE_OBBBA_FROM = 2026
_QBI_PHASE_IN_RANGE_PRE_2026 = {"mfj": 100000.0, "other": 50000.0}
_QBI_PHASE_IN_RANGE_2026 = {"mfj": 150000.0, "other": 75000.0}


def _qbi_phase_in_range(tax_year, is_mfj: bool, override=None) -> float:
    """Resolve the §199A phase-in range: a cache override wins, else the
    statutory schedule by year ($50k/$100k-MFJ ≤2025, $75k/$150k-MFJ TY2026+)."""
    if override is not None:
        if isinstance(override, bool) or not isinstance(override, (int, float)) \
                or not math.isfinite(float(override)) or float(override) <= 0:
            raise ValueError(
                f"qbi_phase_in_range override must be a positive number, got {override!r}")
        return float(override)
    key = "mfj" if is_mfj else "other"
    if isinstance(tax_year, int) and not isinstance(tax_year, bool) \
            and tax_year >= _QBI_PHASE_IN_RANGE_OBBBA_FROM:
        return _QBI_PHASE_IN_RANGE_2026[key]
    return _QBI_PHASE_IN_RANGE_PRE_2026[key]


def compute_form_8995a(
    qbi: float,
    w2_wages: float,
    ubia: float,
    is_sstb: bool,
    taxable_income_before_qbi: float,
    threshold: float,
    phase_in_range: float,
    net_capital_gain: float = 0.0,
    reit_dividends: float = 0.0,
    rate: float = 0.20,
) -> dict:
    """Form 8995-A QBI deduction with the §199A(b)(2) W-2-wage/UBIA limitation
    and the §199A(d)(3) SSTB phase-out, for ONE qualified trade or business (N2).

    Inputs are keyed at the TRADE-OR-BUSINESS level — §199A limits apply per
    trade; a single K-1 can report multiple trades whose combination requires a
    Reg. §1.199A-4 aggregation election (its own tests). v1 models one trade (or
    a caller-aggregated combined figure); pass one trade at a time otherwise.

    Regimes (`excess = TI − threshold`, `ratio = min(1, excess/range)`):
      - excess ≤ 0 → 20% of QBI (no limit; caller usually uses the simplified path).
      - excess ≥ range (ratio 1): SSTB → $0; non-SSTB → min(20%QBI, wage/UBIA limit).
      - within range: non-SSTB → 20%QBI reduced by (20%QBI − limit) × ratio; SSTB →
        reduce QBI/wages/UBIA by (1 − ratio) FIRST (Schedule A), then the same
        within-range formula (the double haircut).
    Wage/UBIA limit = max(50% W-2 wages, 25% W-2 wages + 2.5% UBIA).
    The §199A(a) overall cap (20% × (TI − net capital gain)) applies to the sum.

    Verified vs IRS Form 8995-A instructions + §199A(b)(2)/(3), §199A(d)(3).
    """
    if not isinstance(is_sstb, bool):
        raise ValueError(f"is_sstb must be a bool, got {is_sstb!r}")
    # QBI may be NEGATIVE (a qualified business loss): accept it and FLOOR the
    # component at 0 — the loss is carried forward (handled by the caller's
    # loss_carryforward), never a hard error. _sched_a_amount would wrongly
    # reject a loss, crashing an over-threshold filer whose trade ran a loss.
    q = max(0.0, _f2210_amount(qbi, "qbi", allow_negative=True))
    w = _sched_a_amount(w2_wages, "w2_wages")
    u = _sched_a_amount(ubia, "ubia")
    ti = _sched_a_amount(taxable_income_before_qbi, "taxable_income_before_qbi")
    thr = _sched_a_amount(threshold, "threshold")
    rng = _sched_a_amount(phase_in_range, "phase_in_range")
    ncg = _sched_a_amount(net_capital_gain, "net_capital_gain")
    reit = _sched_a_amount(reit_dividends, "reit_dividends")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) \
            or not math.isfinite(float(rate)) or not (0 < float(rate) <= 1):
        raise ValueError(f"rate must be a finite fraction in (0, 1], got {rate!r}")
    rate = float(rate)
    if rng <= 0:
        raise ValueError("phase_in_range must be positive")

    excess = max(0.0, ti - thr)
    ratio = min(1.0, excess / rng) if excess > 0 else 0.0
    applicable_pct = None

    def _wage_limit(wages, ubia_):
        return max(0.5 * wages, 0.25 * wages + 0.025 * ubia_)

    if excess <= 0:
        qbi_component = rate * q
        wage_ubia_limit = None
        regime = "below_threshold"
    elif is_sstb and ratio >= 1.0:
        qbi_component = 0.0
        wage_ubia_limit = 0.0
        regime = "sstb_fully_excluded"
    else:
        eff_q, eff_w, eff_u = q, w, u
        if is_sstb:  # 0 < ratio < 1 — Schedule A applicable-% haircut first
            applicable_pct = 1.0 - ratio
            eff_q, eff_w, eff_u = q * applicable_pct, w * applicable_pct, u * applicable_pct
        tentative = rate * eff_q
        wage_ubia_limit = _wage_limit(eff_w, eff_u)
        if ratio >= 1.0:  # non-SSTB above the range: full wage/UBIA limit
            qbi_component = min(tentative, wage_ubia_limit)
            regime = "wage_ubia_limited"
        else:  # within the range: the limit phases in proportionally
            reduction = max(0.0, tentative - wage_ubia_limit) * ratio
            qbi_component = tentative - reduction
            regime = "sstb_phase_in" if is_sstb else "phase_in"

    reit_component = round(rate * reit, 2)
    total = round(qbi_component + reit_component, 2)
    overall_cap = round(rate * max(0.0, ti - ncg), 2)
    deduction = min(total, overall_cap)
    income_limited = total > overall_cap

    notes = [
        "Form 8995-A: models ONE qualified trade or business (§199A limits are "
        "per-trade). Multiple un-aggregated trades are NOT combined here — run "
        "per trade or make the Reg. §1.199A-4 aggregation election and pass the "
        "combined QBI / W-2 wages / UBIA. Patron reduction (§199A(b)(7)) and "
        "§199A(g) DPAD not modeled.",
    ]
    if is_sstb and excess > 0:
        notes.append(
            "SSTB: fully qualified below the threshold, phased out across the "
            "range, and $0 above threshold + range. is_sstb is an attested fact "
            "— a wrong False on an actual SSTB overstates the deduction.")

    return {
        "qbi_component": round(qbi_component, 2),
        "wage_ubia_limit": round(wage_ubia_limit, 2) if wage_ubia_limit is not None else None,
        "reit_component": reit_component,
        "overall_cap": overall_cap,
        "income_limited": income_limited,
        "deduction": round(deduction, 2),
        "regime": regime,
        "ratio": round(ratio, 6),
        "applicable_percentage": round(applicable_pct, 6) if applicable_pct is not None else None,
        "is_sstb": is_sstb,
        "notes": notes,
    }


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
    w2_wages: float = None,
    ubia: float = None,
    is_sstb: bool = None,
    filing_status: str = None,
    phase_in_range: float = None,
) -> dict:
    """Compute QBI deduction (Form 8995 simplified, or Form 8995-A when over the
    §199A threshold and the trade's W-2 wages / UBIA / SSTB status are supplied).

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
    loss_carryforward = min(0, qbi_income_net)

    # Form 8995-A (N2): when the filer is OVER the §199A threshold AND the
    # trade's W-2 wages / UBIA / SSTB status are supplied, replace the simplified
    # 20%-of-QBI component with the wage/UBIA-limited (and SSTB-phased) component.
    # Needs taxable income + threshold; absent the wage inputs, the simplified
    # path + the `over_threshold` warning below is kept (backward compatible).
    form_8995a = None
    if (threshold is not None and taxable_income_before_qbi is not None
            and w2_wages is not None and ubia is not None and is_sstb is not None
            and qbi_income_net > 0):  # a QBI loss → 0 component (simplified path)
        _thr8995 = _coerce_numeric(threshold, default=None)
        _ti8995 = _coerce_numeric(taxable_income_before_qbi, default=None)
        if _thr8995 is not None and _ti8995 is not None \
                and math.isfinite(_thr8995) and math.isfinite(_ti8995) \
                and _ti8995 > _thr8995:
            # ABOVE the threshold the status changes the answer: §199A's
            # phase-in range for MFJ is double every other status's, so a
            # missing one silently took the narrower band and HALVED the
            # deduction — $6,666.67 where an MFJ filer is owed $13,333.33,
            # with `form_8995a_applied` reporting True and no warning. That
            # under-states a deduction on a FILED return, so it STOPS
            # (AGENTS.md §2 as amended by Q8: a fallback may degrade only when
            # the output is advisory AND it under-states). Below the threshold
            # the deduction is 20% for every status and this branch is not
            # reached, so nothing is refused where nothing can differ.
            if filing_status is None:
                raise ValueError(
                    "filing_status is required once taxable income exceeds the "
                    f"§199A threshold (${_thr8995:,.2f}): the phase-in range "
                    "for MFJ is double every other status's, so assuming one "
                    "halves or doubles the deduction. Pass it from "
                    "result.json meta.filing_status.")
            is_mfj = _normalize_filing_status(filing_status) == "MFJ"
            rng = phase_in_range if phase_in_range is not None \
                else _qbi_phase_in_range(tax_year, is_mfj)
            form_8995a = compute_form_8995a(
                qbi=qbi_income_net, w2_wages=w2_wages, ubia=ubia, is_sstb=is_sstb,
                taxable_income_before_qbi=_ti8995, threshold=_thr8995,
                phase_in_range=rng,
                net_capital_gain=net_capital_gain if net_capital_gain is not None else 0.0,
                reit_dividends=0.0, rate=rate)
            qbi_component = form_8995a["qbi_component"]

    reit_component = round(reit_dividends * rate, 2)
    total_deduction = qbi_component + reit_component

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
    # An SSTB above the §199A phase-out is NOT a qualified trade at all, so the
    # §70105 "active qualified trade" minimum cannot resurrect its $0 deduction.
    _sstb_excluded = form_8995a is not None and form_8995a["regime"] == "sstb_fully_excluded"
    qbi_minimum_applied = False
    qbi_minimum_note = None
    if (isinstance(tax_year, int) and not isinstance(tax_year, bool)
            and tax_year >= _QBI_MINIMUM_FROM
            and active_qualified_trade and qbi_income_net > 0
            and not _sstb_excluded):
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
    if form_8995a is not None:
        # 8995-A was computed — expose its detail and DO NOT emit the
        # unmodeled-limit warning (the limit is now modeled).
        result["form_8995a"] = form_8995a
        result["form_8995a_applied"] = True
    elif over_threshold:
        result["warning"] = (
            "Taxable income exceeds the Section 199A threshold: Form 8995-A "
            "W-2-wage/UBIA limits and the SSTB phase-out apply but are NOT modeled "
            "here — the deduction may be overstated (an SSTB phases to $0). Supply "
            "the trade's W-2 wages, UBIA, and SSTB status to compute the limit, "
            "or verify against Form 8995-A."
        )
    return result


# Social Security Benefits Worksheet base amounts (IRC §86) — statutory-fixed
# and UNINDEXED since 1984/1993, so they are structural constants, not tax_law
# cache fields. Keyed by treatment: MFS-lived-with-spouse has a $0 base and its
# own 85% shortcut path.
_SS_BASE_AMOUNT = {"single": 25000.0, "mfj": 32000.0}
_SS_SECOND_THRESHOLD = {"single": 34000.0, "mfj": 44000.0}


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


# Schedule A structural statutory constants (none indexed):
# charitable AGI caps — cash 60% (§170(b)(1)(G), made permanent by OBBBA),
# appreciated property 30%, overall 50% ordering per Pub 526; OBBBA §70425
# 0.5%-of-AGI charitable floor for itemizers from TY2026; acquisition-debt
# limits $750K post-TCJA / $1M for pre-Dec-16-2017 originations (§163(h)(3));
# the SALT cap's amount steps by statute, not by an index (federal/salt.py),
# and is read from federal.salt_cap.
# The medical floor and the appreciated-property cap are in `_shared.py`, not
# because they are less federal but because California conforms to both and
# `compute_ca_itemized` reads them — see that file's note on what belongs there.
_CHARITY_CASH_AGI_CAP = 0.60
_CHARITY_OBBBA_FLOOR_RATE = 0.005
_CHARITY_OBBBA_FLOOR_FROM = 2026
_MORTGAGE_LIMIT_TCJA = 750000.0
_MORTGAGE_LIMIT_GRANDFATHERED = 1000000.0
# OBBBA §70111 (TY2026+): itemized deductions are reduced by 2/37 of the
# lesser of the itemized total or the income taxed in the 37% bracket.
_OBBBA_ITEMIZED_HAIRCUT = 2.0 / 37.0
_OBBBA_ITEMIZED_HAIRCUT_FROM = 2026


# §4973 excise tax on excess HSA contributions — statutory 6%, not indexed.
_HSA_EXCISE_RATE = 0.06


def compute_form_8889(
    coverage_type: str,
    eligible_months: int,
    employee_contributions: float,
    employer_contributions: float,
    self_only_limit: float,
    family_limit: float,
    age_55_plus: bool = False,
    catch_up_amount: float = 1000.0,
    last_month_rule: bool = False,
    family_limit_share: float = None,
) -> dict:
    """Form 8889 return-side HSA limit test (N3): the §223(b) contribution limit
    vs actual contributions, the HSA deduction (line 13), and any EXCESS
    contribution + its §4973 6% excise.

    The load-bearing point (bias inversion): on the RETURN side, assuming
    full-year HDHP eligibility when the taxpayer was covered only part of the
    year OVERSTATES the limit and MISSES a real excess + its excise (an
    understatement). So the limit here is computed from the ACTUAL eligible
    MONTHS (§223(b)(1)–(3) monthly limitation), NOT the full annual limit —
    unless the §223(b)(8) last-month rule is attested (eligible on Dec 1 → full
    annual limit, with the testing-period trap flagged). The caller must supply
    `eligible_months` or `last_month_rule`; a full-year filer passes
    `eligible_months=12`.

    Args:
        coverage_type: "self_only" | "family" | "none" (none → $0 limit).
        eligible_months: months (0–12) the taxpayer was an eligible individual
            (HDHP coverage on the FIRST of the month, no disqualifying coverage).
        employee_contributions: contributions the taxpayer made directly (Form
            8889 line 2 — the deductible ones).
        employer_contributions: employer + cafeteria-plan contributions (W-2 box
            12 code W, line 9) — already excluded from income, not deductible.
        self_only_limit / family_limit: the annual §223(b)(2) limits for the year
            (fetched federal.hsa_limits — REQUIRED, indexed, never guessed).
        age_55_plus: the §223(b)(3) $1,000 catch-up (also monthly-prorated).
        last_month_rule: §223(b)(8) — eligible on Dec 1 → treated eligible all
            year (full annual limit). Flags the testing-period trap.
        family_limit_share: for married-both-HDHP with family coverage, the
            portion of the family limit allocated to THIS spouse (a fraction
            0–1). Default None → full family limit (single-HSA-holder case);
            a couple must split it and this is caveated.

    Returns dict: allowed_limit, total_contributions, hsa_deduction (line 13),
    excess_contribution, excise_tax (§4973 6%), excess_employer_taxable, notes.
    """
    ct = (coverage_type or "").strip().lower()
    if ct not in ("self_only", "family", "none"):
        raise ValueError(
            f"coverage_type must be self_only / family / none, got {coverage_type!r}")
    if isinstance(eligible_months, bool) or not isinstance(eligible_months, int) \
            or not (0 <= eligible_months <= 12):
        raise ValueError(
            f"eligible_months must be an int 0–12, got {eligible_months!r}")
    for name, flag in (("age_55_plus", age_55_plus),
                       ("last_month_rule", last_month_rule)):
        if not isinstance(flag, bool):
            raise ValueError(f"{name} must be a bool, got {flag!r}")
    emp = _sched_a_amount(employee_contributions, "employee_contributions")
    er = _sched_a_amount(employer_contributions, "employer_contributions")
    so_limit = _sched_a_amount(self_only_limit, "self_only_limit")
    fam_limit = _sched_a_amount(family_limit, "family_limit")
    catch = _sched_a_amount(catch_up_amount, "catch_up_amount")

    base_annual = {"self_only": so_limit, "family": fam_limit, "none": 0.0}[ct]
    if ct == "family" and family_limit_share is not None:
        share = _sched_a_amount(family_limit_share, "family_limit_share")
        if share > 1:
            raise ValueError(f"family_limit_share must be a fraction ≤ 1, got {share}")
        base_annual = round(base_annual * share, 2)

    notes = []
    if last_month_rule and ct != "none":
        # §223(b)(8): full annual limit regardless of partial-year eligibility.
        allowed_limit = round(base_annual + (catch if age_55_plus else 0.0), 2)
        notes.append(
            "§223(b)(8) last-month rule applied: full annual limit despite "
            "partial-year eligibility. TESTING PERIOD — you must remain an "
            "eligible individual through the last day of the 12th month after "
            "this December, or the excess is included in income + a 10% "
            "additional tax (a separate exposure not computed here).")
    else:
        # Monthly limitation: 1/12 of the annual limit (and catch-up) per
        # eligible month (§223(b)(1)–(3)). A constant coverage type is assumed;
        # a mid-year self↔family change is caveated.
        allowed_limit = round(base_annual * eligible_months / 12.0, 2)
        if age_55_plus:
            allowed_limit = round(allowed_limit + catch * eligible_months / 12.0, 2)
        if 0 < eligible_months < 12:
            notes.append(
                "Partial-year HDHP eligibility: the limit is prorated to "
                f"{eligible_months}/12 (§223(b) monthly limitation). Assumes a "
                "CONSTANT coverage type across the eligible months — a mid-year "
                "self-only↔family change needs the month-by-month worksheet. If "
                "you were eligible on Dec 1, the last-month rule may allow the "
                "FULL annual limit (pass last_month_rule=True).")

    total_contributions = round(emp + er, 2)
    excess_contribution = round(max(0.0, total_contributions - allowed_limit), 2)
    excise_tax = round(_HSA_EXCISE_RATE * excess_contribution, 2)
    # Line 12/13: the deduction is the taxpayer's own contributions, capped at
    # the limit remaining after employer contributions (which are already
    # income-excluded, never deducted).
    limit_after_employer = max(0.0, allowed_limit - er)
    hsa_deduction = round(min(emp, limit_after_employer), 2)
    # Employer contributions ABOVE the limit are taxable income (line 9 excess).
    excess_employer_taxable = round(max(0.0, er - allowed_limit), 2)

    if excess_contribution > 0:
        notes.append(
            f"EXCESS HSA contribution of ${excess_contribution:,.2f}: a §4973 6% "
            f"excise (${excise_tax:,.2f}) applies for EACH year it stays in the "
            "account. Withdraw the excess (plus its earnings) by the return due "
            "date to avoid the excise; otherwise report it on Form 5329.")
    if ct == "family" and family_limit_share is None:
        notes.append(
            "Family coverage: if BOTH spouses have HSAs the family limit must be "
            "SPLIT between them (§223(b)(5)) — pass family_limit_share. This "
            "assumes the full family limit belongs to this person (single HSA "
            "holder).")

    return {
        "allowed_limit": allowed_limit,
        "total_contributions": total_contributions,
        "hsa_deduction": hsa_deduction,
        "excess_contribution": excess_contribution,
        "excise_tax": excise_tax,
        "excess_employer_taxable": excess_employer_taxable,
        "eligible_months": eligible_months,
        "last_month_rule_applied": bool(last_month_rule and ct != "none"),
        "notes": notes,
    }


# §21 child- and dependent-care credit — STATUTORY-FIXED constants (unindexed
# since EGTRRA 2001/JGTRRA 2003; the 2021 ARPA expansion was temporary and
# expired). Inlined with citation, like the SS base amounts.
_DCC_EXPENSE_CAP_ONE = 3000.0        # §21(c)(1) — one qualifying person
_DCC_EXPENSE_CAP_MULTI = 6000.0      # §21(c)(2) — two or more
_DCC_AGI_FULL_RATE_CEILING = 15000.0  # §21(a)(2) — 35% up to $15,000 AGI
_DCC_RATE_MAX = 0.35
_DCC_RATE_FLOOR = 0.20               # floor for AGI > $43,000
_DCC_RATE_STEP_AGI = 2000.0          # −1% per $2,000 (or fraction) over $15,000
_DCC_DEEMED_MONTHLY_ONE = 250.0      # §21(d)(2) — student/disabled spouse, 1 person
_DCC_DEEMED_MONTHLY_MULTI = 500.0    # two or more


def _dcc_applicable_percentage(agi: float) -> float:
    """§21(a)(2): 35% for AGI ≤ $15,000, reduced 1 percentage point for each
    $2,000 (or fraction) of AGI over $15,000, floored at 20%."""
    if agi <= _DCC_AGI_FULL_RATE_CEILING:
        return _DCC_RATE_MAX
    steps = math.ceil((agi - _DCC_AGI_FULL_RATE_CEILING) / _DCC_RATE_STEP_AGI)
    return max(_DCC_RATE_FLOOR, round(_DCC_RATE_MAX - 0.01 * steps, 2))


def compute_form_2441(
    qualifying_persons: int,
    qualified_expenses: float,
    taxpayer_earned_income: float,
    agi: float,
    filing_status: str,
    spouse_earned_income: float = None,
    dependent_care_benefits: float = 0.0,
    taxpayer_student_disabled_months: int = 0,
    spouse_student_disabled_months: int = 0,
    lived_apart_exception: bool = False,
    tax_liability: float = None,
) -> dict:
    """Form 2441 child- and dependent-care credit (§21, N3b). NONREFUNDABLE.

    Credit = applicable_percentage × the SMALLEST of: qualified care expenses,
    the §21(c) expense cap ($3,000 one qualifying person / $6,000 two+) reduced
    by excluded employer dependent-care benefits (W-2 box 10, Part III before
    Part II), and the earned income of the taxpayer AND (if married) the spouse.

    The eligibility GATES are the caller's step-0 (collected by /tax-setup, per
    the capability-registry invariant): a qualifying person (§21(b)(1) — under
    13, or incapable of self-care, meeting the relationship/residency tests),
    the §21(d) work-related-expense rule (both spouses working / looking for
    work / full-time students), and provider name/TIN due diligence. This
    function computes the credit GIVEN those facts; it does not re-test them.

    Args:
        qualifying_persons: count of qualifying persons (drives the expense cap
            and the deemed-income amount). 0 → no credit.
        qualified_expenses: work-related care expenses PAID (already limited to
            expenses for qualifying persons).
        taxpayer_earned_income / spouse_earned_income: §21(d) earned income.
            spouse_earned_income None for an unmarried filer (no spouse limit).
        agi: AGI for the §21(a)(2) applicable percentage.
        dependent_care_benefits: the EXCLUDED employer DCB (Form 2441 Part III
            result) — reduces the expense cap. The $5,000/$2,500-MFS exclusion
            limit and any taxable excess are the caller's Part III (caveated).
        taxpayer_student_disabled_months / spouse_student_disabled_months:
            §21(d)(2) — a spouse who is a full-time student or incapable of
            self-care is deemed to have $250/mo (1 person) or $500/mo (2+) of
            earned income. Only ONE spouse may use deemed income for a month
            both qualify, so when BOTH are flagged the deemed amount is applied
            to the SPOUSE only (never overstates).
        lived_apart_exception: §21(e)(2)/(e)(4) — an MFS filer is denied the
            credit UNLESS legally separated or living apart from the spouse the
            last 6 months while maintaining the household. Default False → an
            MFS filer gets $0.
        tax_liability: when supplied, the nonrefundable credit is capped at it.

    Returns dict: credit, applicable_percentage, expense_cap_after_dcb,
    earned_income_limit, qualified_expenses_used, disallowed_mfs, notes.
    """
    fs = _normalize_filing_status(filing_status)
    if isinstance(qualifying_persons, bool) or not isinstance(qualifying_persons, int) \
            or qualifying_persons < 0:
        raise ValueError(
            f"qualifying_persons must be a non-negative int, got {qualifying_persons!r}")
    for name, m in (("taxpayer_student_disabled_months", taxpayer_student_disabled_months),
                    ("spouse_student_disabled_months", spouse_student_disabled_months)):
        if isinstance(m, bool) or not isinstance(m, int) or not (0 <= m <= 12):
            raise ValueError(f"{name} must be an int 0–12, got {m!r}")
    if not isinstance(lived_apart_exception, bool):
        raise ValueError("lived_apart_exception must be a bool")
    exp = _sched_a_amount(qualified_expenses, "qualified_expenses")
    tp_ei = _sched_a_amount(taxpayer_earned_income, "taxpayer_earned_income")
    agi_v = _sched_a_amount(agi, "agi")
    dcb = _sched_a_amount(dependent_care_benefits, "dependent_care_benefits")
    is_married = fs in ("MFJ", "MFS")
    sp_ei = None
    if spouse_earned_income is not None:
        sp_ei = _sched_a_amount(spouse_earned_income, "spouse_earned_income")

    notes = []
    if qualifying_persons == 0:
        return {"credit": 0.0, "applicable_percentage": 0.0,
                "expense_cap_after_dcb": 0.0, "earned_income_limit": 0.0,
                "qualified_expenses_used": 0.0, "disallowed_mfs": False,
                "notes": ["No qualifying person — no §21 credit."]}

    # §21(e)(2): an MFS filer is generally denied the credit.
    if fs == "MFS" and not lived_apart_exception:
        return {"credit": 0.0, "applicable_percentage": 0.0,
                "expense_cap_after_dcb": 0.0, "earned_income_limit": 0.0,
                "qualified_expenses_used": 0.0, "disallowed_mfs": True,
                "notes": ["MFS filers are DENIED the §21 credit (§21(e)(2)) "
                          "unless legally separated or living apart from the "
                          "spouse the last 6 months while maintaining the "
                          "household — pass lived_apart_exception=True if so."]}

    deemed_monthly = (_DCC_DEEMED_MONTHLY_ONE if qualifying_persons == 1
                      else _DCC_DEEMED_MONTHLY_MULTI)
    # §21(d)(2) deemed earned income for a student/disabled spouse. Only ONE
    # spouse may use it for a shared month, so when BOTH are flagged apply it to
    # the SPOUSE only (the common non-working-student case; never overstates).
    eff_tp = tp_ei
    eff_sp = sp_ei
    if is_married:
        if spouse_student_disabled_months > 0:
            eff_sp = max(sp_ei or 0.0, deemed_monthly * spouse_student_disabled_months)
            if taxpayer_student_disabled_months > 0:
                notes.append(
                    "Both spouses flagged as student/disabled: §21(d)(2) allows "
                    "only ONE to use deemed income for a shared month — applied "
                    "to the spouse only (may understate if the student months do "
                    "not overlap; verify Form 2441 line 5).")
        elif taxpayer_student_disabled_months > 0:
            eff_tp = max(tp_ei, deemed_monthly * taxpayer_student_disabled_months)
        earned_income_limit = round(min(eff_tp, eff_sp if eff_sp is not None else 0.0), 2)
    else:
        # Unmarried: only the taxpayer's earned income limits (deemed income is a
        # spouse concept; a single filer must actually work/look for work).
        earned_income_limit = round(eff_tp, 2)

    cap = _DCC_EXPENSE_CAP_ONE if qualifying_persons == 1 else _DCC_EXPENSE_CAP_MULTI
    expense_cap_after_dcb = round(max(0.0, cap - dcb), 2)
    if dcb > 0:
        notes.append(
            "Employer dependent-care benefits (W-2 box 10) reduce the expense "
            "cap (Form 2441 Part III before Part II). The $5,000/$2,500-MFS "
            "exclusion limit and any taxable excess DCB are the caller's Part III.")

    qualified_used = round(min(exp, expense_cap_after_dcb, earned_income_limit), 2)
    pct = _dcc_applicable_percentage(agi_v)
    credit = round(pct * qualified_used, 2)

    tax_limited = False
    if tax_liability is not None:
        tl = _sched_a_amount(tax_liability, "tax_liability")
        if credit > tl:
            credit = round(tl, 2)
            tax_limited = True
    notes.append(
        "NONREFUNDABLE (§21) — limited to tax; no carryforward of the unused "
        "portion. Eligibility gates (qualifying person, work-related rule, "
        "provider TIN) are the caller's step 0.")
    if tax_limited:
        notes.append("Credit capped at the tax liability (nonrefundable).")

    return {
        "credit": credit,
        "applicable_percentage": pct,
        "expense_cap_after_dcb": expense_cap_after_dcb,
        "earned_income_limit": earned_income_limit,
        "qualified_expenses_used": qualified_used,
        "disallowed_mfs": False,
        "notes": notes,
    }


def compute_sales_tax_deduction(
    table_amount: float,
    state_rate: float,
    local_rate: float = 0.0,
    big_ticket_tax: float = 0.0,
) -> dict:
    """Schedule A line 5a sales-tax INPUT — IRS Optional State Sales Tax
    Tables method (Schedule A instructions).

    For no-income-tax states (FL/TX/...) the SALT line-5a election is
    effectively always general sales tax — there is no state income tax to
    elect and W-2 Box 17 is empty. This computes the elective figure:

        total = table_amount                                (IRS table lookup)
              + table_amount * local_rate / state_rate      (local addition)
              + big_ticket_tax    (actual tax on specified items — motor
                                   vehicles, boats, etc., general rate only)

    The result feeds compute_schedule_a(state_local_sales_taxes=total) —
    the income-vs-sales election, the SALT cap (federal.salt_cap), and all
    Schedule A interactions live THERE, deliberately not here.

    Codex round-15 P1 pushback (web-verified, not implemented — the ratio
    method below IS the IRS-prescribed FL treatment): Codex claimed the
    local addition must be capped at "the smaller of table_amount *
    local_rate/state_rate and the applicable Optional Local Sales Tax
    Tables amount." Verified against the CURRENT IRS "State and Local
    General Sales Tax Deduction Worksheet—Line 5a" (2025 Instructions for
    Schedule A (Form 1040), irs.gov/pub/irs-pdf/i1040sca.pdf, revision
    dated 2025-12-08) — worksheet line 2 lists the states with a published
    Optional Local Sales Tax Table: Alabama, Alaska, Arizona, Arkansas,
    Colorado, Georgia, Illinois, Kansas, Louisiana, Mississippi, Missouri,
    New York, North Carolina, South Carolina, Tennessee, Utah, and
    Virginia (confirmed independently by the instructions' own "Which
    Optional Local Sales Tax Table Should I Use?" reference table).
    FLORIDA IS NOT ON EITHER LIST. For a state not on that list (line 2 =
    "No, enter -0-"), the worksheet's ONLY local-addition method is line 6
    "Multiply line 1 by line 5" — i.e. exactly `table_amount *
    local_rate/state_rate`, with no local-table figure to compare against
    at all. (Separately: even for a state that DOES have a published local
    table, the worksheet is a mutually-exclusive EITHER/OR by state, never
    a lesser-of comparison per taxpayer — Codex's "smaller of" framing
    doesn't describe the worksheet's actual mechanics for any state.) v1
    models FL only (see the FL-vs-other-no-tax-state note in
    tax-compute.md step 2), so this function's ratio-only method is
    correct as written; no local-table parameter is needed unless/until a
    state ON that list is modeled.

    Args:
        table_amount: IRS optional-tables amount for the taxpayer's income
            band / exemptions / state (looked up by /tax-compute from the
            current-year Schedule A instructions).
        state_rate: state general sales-tax rate as a decimal (FL 0.06,
            F.S. 212.05). Must be > 0.
        local_rate: county/local general surtax rate as a decimal. Time-
            weighted across localities when the taxpayer moved counties or
            the rate changed mid-year — see /tax-setup.
        big_ticket_tax: actual sales tax paid, at the GENERAL sales tax
            rate, on IRS-QUALIFYING big-ticket purchase classes ONLY (per
            the Schedule A Optional Sales Tax Tables instructions):
            motor vehicles (bought OR leased — the instructions include
            leased vehicles), boats, aircraft, a mobile/prefabricated
            home purchase that carried general sales tax, and building
            materials for a home/substantial addition/major home
            renovation. A conventional existing-home purchase is NOT in
            the class — documentary stamps and closing taxes never
            qualify. Ordinary
            purchases (furniture, jewelry, electronics, appliances,
            clothing, etc.) are already priced into table_amount — do not
            fold their sales tax in here, or the deduction double-counts.

    Returns:
        {"table_amount", "local_addition", "big_ticket_tax", "total"} — 2 dp.

    Raises:
        ValueError: bool/NaN/Infinity/negative input, state_rate not in
            (0, 1), or local_rate not in [0, 1) — rates must be decimal
            fractions (e.g. 0.06 for 6%), not percentages.
    """
    vals = {}
    for name, v in (("table_amount", table_amount), ("state_rate", state_rate),
                    ("local_rate", local_rate), ("big_ticket_tax", big_ticket_tax)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(
                f"compute_sales_tax_deduction: {name} must be numeric, got {v!r}")
        if not math.isfinite(float(v)):
            raise ValueError(
                f"compute_sales_tax_deduction: {name} must be finite, got {v!r}")
        if v < 0:
            raise ValueError(
                f"compute_sales_tax_deduction: {name} must be >= 0, got {v!r}")
        vals[name] = float(v)
    if not (0 < vals["state_rate"] < 1):
        raise ValueError(
            "compute_sales_tax_deduction: state_rate must be > 0 and < 1 — "
            f"rates must be decimal fractions (e.g. 0.06 for 6%), not "
            f"percentages, got {vals['state_rate']!r}")
    if not (vals["local_rate"] < 1):
        raise ValueError(
            "compute_sales_tax_deduction: local_rate must be < 1 — rates "
            f"must be decimal fractions (e.g. 0.06 for 6%), not "
            f"percentages, got {vals['local_rate']!r}")
    local_addition = round(
        vals["table_amount"] * vals["local_rate"] / vals["state_rate"], 2)
    total = round(round(vals["table_amount"], 2) + local_addition
                  + round(vals["big_ticket_tax"], 2), 2)
    return {
        "table_amount": round(vals["table_amount"], 2),
        "local_addition": local_addition,
        "big_ticket_tax": round(vals["big_ticket_tax"], 2),
        "total": total,
    }


def _schedule_a_salt_cap(agi: float, fs: str, tax_year: int, salt_cap,
                         salt_before_cap: float, modified_agi,
                         ) -> "tuple[float, float, float | None, bool]":
    """§164(b)(7) for the return: (the amount, the cap applied, MAGI, and
    whether that cap is known). One block, out of `compute_schedule_a`, so the
    name `cap` there means one thing.

    `salt_before_cap` is the tax the cap limits, at cents.
    """
    mfs = fs == "MFS"
    # At cents before the rule sees it, so `applied < base` means the
    # phasedown cut the cap, not that the rule's rounding trimmed a fraction
    # of a cent off the input.
    base = round(_amount(salt_cap, "salt_cap"), 2)
    # Validated whenever it is given. What it adds to AGI is never negative,
    # so a figure below AGI is some other number — taxable income, another
    # section's MAGI — and would leave the cap too high, AA16 again by input.
    # At cents, the precision AGI is written at and the figure goes back into
    # result.json at: a MAGI summed along another path from the same items
    # can land a float's width under AGI, and that is the same number.
    magi = None
    if modified_agi is not None:
        magi = round(_amount(modified_agi, "modified_agi", allow_negative=True), 2)
        if magi < round(float(agi), 2):
            raise ValueError(
                "modified_agi is below agi: it is AGI plus Form 2555 lines 45 "
                "and 50, Form 4563 line 15 and excluded Puerto Rico income, "
                "which only add, so a smaller figure is a different number and "
                "would leave the SALT cap unreduced")
    # Required only where it can change the answer. The deduction is the
    # lesser of the tax and the cap, and MAGI moves the cap between the amount
    # and the floor, so it matters only when the tax AND the amount are both
    # above the floor. Anywhere else a refusal is the noise `phasedown_applies`
    # exists to prevent — the kind that teaches an agent to pass AGI by reflex,
    # which is the wrong figure for the exclusion filers this check is for.
    if (magi is None and _salt_rule.phasedown_applies(tax_year)
            and min(salt_before_cap, base) > _salt_rule.phasedown_floor(mfs)):
        raise ValueError(
            f"modified_agi is required for TY{tax_year}: §164(b)(7)(B) "
            "reduces the SALT cap as modified AGI rises past the year's "
            "threshold, and without it the full cap would over-state the "
            "deduction. Pass AGI plus Form 2555 lines 45 and 50, Form 4563 "
            "line 15 and any excluded Puerto Rico income — AGI itself when "
            "there are none.")
    applied = _salt_rule.salt_cap(tax_year, mfs, base=base, modified_agi=magi)
    # Unknown where no MAGI was given and one could have moved the cap — which
    # the check above allows only when the tax is at or under the floor, so
    # the deduction is right at any MAGI but the cap is not known. Recording
    # the unreduced amount printed "Salt Cap Applied $40,400.00" for a filer
    # whose statutory cap was $10,000.
    known = (magi is not None or not _salt_rule.phasedown_applies(tax_year)
             or _salt_rule.cap_floor(base, mfs) == base)
    return base, applied, magi, known


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
    modified_agi: "float | None" = None,
) -> dict:
    """Federal Schedule A + the itemize-vs-standard election, computed.

    Args:
        salt_cap: the year's cap for this status from federal.salt_cap
            (amount, or mfs_amount for MFS) — never hardcoded here. It is the
            amount BEFORE §164(b)(7)(B)'s phasedown, which is applied here
            from `modified_agi` by the one rule, `federal/salt.py`.
        modified_agi: AGI plus Form 2555 lines 45 and 50, Form 4563 line 15
            and any excluded Puerto Rico income — the line 5e worksheet's
            add-backs, which are Schedule 1-A Part I's too — and AGI itself
            when there are none. Line 50 is the foreign housing DEDUCTION, not
            an exclusion: "AGI plus the §911/§931/§933 exclusions" stood here
            and left it out, which lowers MAGI and raises the cap. It can
            be negative but never below `agi`, which refuses. REQUIRED in
            TY2025-2029, the years §164(b)(7)(B) reduces the cap per return,
            whenever state and local tax before the cap and the cap itself
            are both above the phasedown's floor; otherwise no MAGI can move
            the deduction. Validated whenever given. Until AA16
            this function capped at the amount it was handed, with a note on
            every Schedule A that the phasedown was not modeled: at $700,000
            of AGI it deducted $40,000 where the statute allows $10,000. A cap
            left unreduced over-states the deduction and under-states the tax,
            and a return is not advisory output, so a missing MAGI refuses
            rather than degrading (AGENTS.md §2).
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
    # At cents: $3,932.55 + $5,888.01 + $179.44 is exactly the phasedown's
    # floor and sums a float's width over it, which refused a MAGI-less return.
    salt_before_cap = round(max(inc_tax, sales_tax) + re_tax + pp_tax, 2)
    base_cap, applied_cap, magi, cap_known = _schedule_a_salt_cap(
        agi, fs, tax_year, salt_cap, salt_before_cap, modified_agi)

    notes = [
        "Form 4952 investment-interest limit not modeled — pass the "
        "post-limit allowed amount",
        "casualty/theft losses (federally-declared disasters only) not "
        "modeled",
        "single-loan mortgage model — mixed pre/post-Dec-16-2017 loans need "
        "the Pub 936 combined-limit worksheet",
    ]

    medical_deductible = _medical_deductible(med, agi_v)

    if sales_tax > inc_tax and sales_tax > 0:
        notes.append("sales-tax election used (exceeds state income taxes)")
    if magi is not None and applied_cap < base_cap:
        # To the cent: a dollar over the threshold takes cents off, and a
        # whole-dollar note printed that as "$40,000 to $40,000".
        notes.append(
            f"SALT cap reduced by the §164(b)(7)(B) phasedown: ${base_cap:,.2f} "
            f"to ${applied_cap:,.2f} at modified AGI ${magi:,.2f}")
    elif not cap_known:
        notes.append(
            "SALT cap left unrecorded: no modified AGI was given, and at this "
            "state and local tax no MAGI changes the deduction")
    salt_deductible = round(min(applied_cap, salt_before_cap), 2)

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
        # The gross, not only what cleared the floor: the floor is a share of
        # AGI, and a planning tool asking whether a delta moves the deduction
        # cannot answer from the deducted amount when it is zero.
        "medical_expenses": round(med, 2),
        "salt_before_cap": salt_before_cap,
        # Not `salt_cap`: that name is the INPUT, the amount before the
        # phasedown, and a re-call fed this figure under it would reduce the
        # cap twice. `modified_agi` is recorded so `ptet`, which reads it back,
        # prices the same cap the return applied.
        "salt_cap_applied": applied_cap if cap_known else None,
        "modified_agi": magi,
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
                f"federal.schedule_1a.{redact(path)} is unfetched — refusing to "
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
        magi: modified AGI = AGI plus Form 2555 lines 45 and 50, Form 4563
            line 15 and excluded Puerto Rico income (Part I, lines 2a-2d; the
            caller computes it). Each provision phases out on MAGI.
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
        raise ValueError(f"{label} {raw!r} is not a valid YYYY-MM-DD date: {redact(e)}")


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
    deduction: float,
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
        deduction: The return's line 12, standard or itemized, held across
                  the baseline and the scenario. It was the cache's standard
                  deduction for every filer, which priced an itemizer from a
                  taxable-income position too high by the difference (AA20);
                  the planning tools pass `scenario.return_deduction`.
        cg_zero_limit, cg_fifteen_limit: QDCG 0%/15% thresholds (binary status key).
                  Required whenever the scenario has qualified dividends or a net
                  long-term gain — `qdcg_worksheet_applies` raises otherwise; an
                  ordinary-income-only scenario runs without them.
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

    # The return's line 12, validated as the fixed input beside it is: NaN
    # keeps `max(0.0, agi - nan)` at 0.0, a silent $0 of tax, and a negative
    # deduction raises taxable income.
    if (isinstance(deduction, bool) or not isinstance(deduction, (int, float))
            or not math.isfinite(deduction) or deduction < 0):
        raise ValueError(
            "scenario deduction must be a finite non-negative number (the "
            "return's line 12)")

    if se_net > 0:
        # W-2 wages already consume the Social Security wage base via payroll, so the
        # SS portion of SE tax only applies to the remaining room. Pass wages as the
        # W-2 SS-wage figure (a planning proxy for Box 3) — matches projection and
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
    ti_before_qbi = max(0.0, agi - deduction - schedule_1a_deduction)
    # Form 8995 line 12 and the QDCG worksheet price the same quantity.
    net_capital_gain = eff_qualified + net_ltcg
    if se_net > 0:
        # §199A(c)(4) / Reg. §1.199A-3(b)(1)(vi): net the QBI base by the
        # deductible half of SE tax (the only trade-attributable SE deduction the
        # scenario models — SE health insurance / retirement are not scenario
        # inputs). Omitting it overstates the QBI deduction and understates the
        # scenario tax for a self-employed filer.
        qbi = compute_qbi_deduction(
            se_net, 0.0, rate=qbi_rate,
            taxable_income_before_qbi=ti_before_qbi,
            net_capital_gain=net_capital_gain,
            half_se_tax=se["deductible_half"],
        )["total_deduction"]
    else:
        qbi = 0.0
    taxable_income = max(0.0, ti_before_qbi - qbi)
    if qdcg_worksheet_applies(net_capital_gain,
                              cg_zero_limit, cg_fifteen_limit):
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




# --- §6601/§6622 underpayment interest (M3) -------------------------------
# §6621 sets the underpayment rate = federal short-term rate + 3 points,
# announced QUARTERLY (effective Jan 1 / Apr 1 / Jul 1 / Oct 1). §6622 requires
# the interest to be COMPOUNDED DAILY. Interest on a late-paid balance runs from
# the return due date (unextended — an extension of time to file is not an
# extension of time to pay) to the pay date. This shares the underpayment_rates
# cache and the _f2210 rate/date validators with compute_form_2210, but differs
# in two deliberate ways: (1) DAILY COMPOUNDING, not the simple-interest 2210
# worksheet convention; (2) NO silent "last rate persists" extrapolation — a
# quarterly rate is authoritative only for ITS quarter, so past the last cached
# quarter the interest is reported as PARTIAL-through-a-date with a loud degrade,
# never a guessed rate across an uncovered quarter (the Oct-15-extension case the
# 2210 fetch window, Apr-of-TY..Apr-of-TY+1, does not reach).

























# --- W2: the state package ------------------------------------------------
#
# RE-EXPORTED so that `from agents.lib.tax_computations import compute_ny_tax`
# keeps working. Thirty modules and fifteen test modules import from this name;
# a split that changed it would be a rename dressed as a move, and the roadmap
# asked for a move. The private `_ny_*` helpers come across too — the New York
# tests reach for them by name, and a test that has to import from a deeper
# path than the code under test knows about is a seam in the wrong place.
# The shared primitives, imported back under the names the federal code below
# calls them by — this is not only a re-export. `_coerce_numeric` in particular
# is addressed as `tax_computations._coerce_numeric` from outside the package,
# by tests and by the review-subagent acceptance fixture.
from agents.lib.tax_computations._shared import (  # noqa: F401
    qdcg_worksheet_applies,
    _CHARITY_APPRECIATED_AGI_CAP,
    _SCHED_A_MEDICAL_FLOOR,
    _amount,
    _coerce_numeric,
    _f2210_amount,
    _medical_deductible,
    _multi_employer_excess,
    _normalize_filing_status,
    _prorated_mortgage_interest,
    _sched_a_amount,
    _split_remainder_last,
    _validated_withholding_list,
)
# The federal forms that have moved into `federal/`, re-exported under the
# addresses they already had. Twenty-three of the thirty-six names are reached
# from outside their own module; the other thirteen are each module's private
# machinery and are left there, because a re-export nothing imports is an
# address this file has to keep valid forever in exchange for nothing.

from agents.lib.tax_computations.federal.penalties import (  # noqa: E402,F401
    DE_MINIMIS_BALANCE,
    PRIOR_YEAR_AGI_THRESHOLD,
    PRIOR_YEAR_AGI_THRESHOLD_MFS,
    PRIOR_YEAR_FACTOR,
    PRIOR_YEAR_FACTOR_HIGH,
    _next_quarter_start,
    _quarter_start_on_or_before,
    compute_form_2210,
    compute_late_filing_penalty,
    compute_underpayment_interest,
    prior_year_agi_threshold,
    prior_year_prong,
)

from agents.lib.tax_computations.federal.international import (  # noqa: E402,F401
    FORM_8938_THRESHOLDS,
    _RETURN_DUE_POSTPONED,
    _return_due_date,
    check_form_8938_required,
    compute_ftc_limitation,
    compute_pfic_1291,
    compute_pfic_excess_distribution,
    compute_section_988,
)

from agents.lib.tax_computations.federal import salt as _salt_rule  # noqa: E402
from agents.lib.tax_computations.federal.amt import (  # noqa: E402,F401
    _resolve_amt_phaseout_rate,
    compute_form_6251,
    compute_form_8801_credit,
)


# California — same contract as New York below, and the same reason for it.
from agents.lib.tax_computations.state.ca import (  # noqa: E402,F401
    compute_ca_itemized,
    compute_ca_schedule_p,
    compute_excess_sdi,
)


# New York, re-exported so that `tax_computations.<name>` keeps working. The
# callers are skill MARKDOWN as well as code — an instruction file naming a
# function is not something a sweep can find and rewrite — so the addresses
# stay where they were and the code moves beneath them.
#
# The PUBLIC names plus `_ny_round_half_up`, which the New York tests reach for
# by name. Ten other privates were re-exported alongside it and referenced
# NOWHERE outside `state/ny.py`; a re-export nothing imports is an address this
# file has to keep valid forever in exchange for nothing.
from agents.lib.tax_computations.state.ny import (  # noqa: E402,F401
    NY_HOUSEHOLD_LINES,
    compute_city_and_yonkers_total,
    ny_household_refusals,
    compute_mctmt,
    compute_ny_agi,
    compute_ny_household_credit,
    compute_ny_itemized,
    compute_ny_modifications,
    compute_ny_pension_exclusion,
    compute_ny_ptet_owner,
    compute_ny_required_annual_payment,
    compute_ny_settlement,
    compute_ny_state_tax_total,
    compute_ny_tax,
    compute_nyc_taxable_income,
    compute_nyc_resident_tax,
    compute_yonkers_nonresident_earnings_tax,
    compute_yonkers_resident_surcharge,
    _ny_round_half_up,
)
