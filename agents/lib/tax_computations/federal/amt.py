#!/usr/bin/env python3
"""Form 6251 and Form 8801 — the alternative minimum tax and its credit.

Two forms and one arithmetic: the tentative minimum tax over the exemption,
and the part of last year's AMT that comes back when this year's regular tax
exceeds it. California's Schedule P (540) mirrors both and is in
`state/ca.py`, because the exemption, the phase-out and the conformity date
are California's own.
"""

import math

import agents.lib  # noqa: F401 — the host-interpreter guard
from agents.lib.tax_computations._shared import (
    _amount,
    _normalize_filing_status,
    qdcg_worksheet_applies,
)

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
    override wins, else the statutory schedule by year. The MFS
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
    zero_pct_limit: float | None = None,
    fifteen_pct_limit: float | None = None,
    amt_ftc: float = 0.0,
    senior_deduction: float = 0.0,
    exemption_phaseout_rate: float | None = None,
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
            (0,1]) it WINS over the statutory schedule, so a fetched figure can
            correct the built-in schedule
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
    ti = _amount(taxable_income, "taxable_income", allow_negative=True)
    rtax = _amount(regular_tax, "regular_tax")
    for what, v in (("exemption", exemption),
                    ("phaseout_start", phaseout_start),
                    ("rate_28_breakpoint", rate_28_breakpoint)):
        if v is None:
            raise ValueError(
                f"federal.amt.{what} is unfetched — refusing to compute AMT "
                "without the indexed table (a guessed exemption would "
                "silently zero the AMT for an ISO exerciser); fetch via "
                "/tax-setup")
    ex = _amount(exemption, "exemption")
    ph_start = _amount(phaseout_start, "phaseout_start")
    bp = _amount(rate_28_breakpoint, "rate_28_breakpoint")
    if fs == "MFS":
        bp /= 2  # the form prints the halved figure for MFS
    salt = _amount(salt_deducted, "salt_deducted")
    std = _amount(standard_deduction_taken, "standard_deduction_taken")
    if salt > 0 and std > 0:
        raise ValueError(
            "pass salt_deducted (itemized) OR standard_deduction_taken — "
            "both at once double-counts the line 2a addback")
    iso = _amount(iso_bargain_element, "iso_bargain_element")
    pab = _amount(private_activity_bond_interest,
                          "private_activity_bond_interest")
    if isinstance(other_adjustments, bool) \
            or not isinstance(other_adjustments, (int, float)) \
            or not math.isfinite(float(other_adjustments)):
        raise ValueError(
            f"other_adjustments must be a finite number, got {other_adjustments!r}")
    other = float(other_adjustments)
    ftc_amt = _amount(amt_ftc, "amt_ftc")
    senior = _amount(senior_deduction, "senior_deduction")
    qd = _amount(qualified_dividends, "qualified_dividends")
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
        # The X2 guard: the same fall-through by the AMT door.
        if qdcg_worksheet_applies(cg, zero_pct_limit, fifteen_pct_limit):
            z = _amount(zero_pct_limit, "zero_pct_limit")
            f15 = _amount(fifteen_pct_limit, "fifteen_pct_limit")
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
    cf = _amount(prior_carryforward, "prior_carryforward")
    rtax = _amount(regular_tax, "regular_tax")
    tmt = _amount(tentative_minimum_tax, "tentative_minimum_tax")
    credits = _amount(nonrefundable_credits, "nonrefundable_credits")
    usable = round(min(cf, max(0.0, rtax - credits - tmt)), 2)
    return {
        "credit_allowed": usable,          # Schedule 3 line 6b
        "carryforward_remaining": round(cf - usable, 2),
    }
