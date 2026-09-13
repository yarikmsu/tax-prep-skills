#!/usr/bin/env python3
"""California — Form 540 and Schedule P (540), full-year resident.

The second member of `state/`, and the one the split was really about:
California is the state AGENTS.md §4 says this toolkit computes in depth, and
its law had been sitting in the federal file since before there was a `state/`
package to put it in.

Three blocks, and they were never contiguous — excess SDI/VPDI next to the
federal excess-Social-Security credit it shares an allocator with, Schedule CA
itemized next to federal Schedule A, and Schedule P (540) next to Form 6251.
Each sat beside the FEDERAL form it mirrors rather than beside the other two,
which is exactly why the file read as one subject and was three.

Every figure is an argument. What is inline is statutory and unindexed with its
citation, which is the narrow exception AGENTS.md §2 names: the 7% CA
tentative-minimum-tax rate and the 25% exemption phase-out (R&TC conformity
fixed at 2015-01-01), the $1M mortgage limit California kept, the 50% cash
charitable cap it never raised to the federal 60%, and the R&TC §17077 6%/80%
itemized phase-out. Indexed CA figures — the phase-out THRESHOLD, the AMT
exemption and its own threshold, the SDI rate and ceiling — are all parameters.
"""

import math

from agents.lib.tax_computations._shared import (
    _CHARITY_APPRECIATED_AGI_CAP,
    _SCHED_A_MEDICAL_FLOOR,
    _amount,
    _coerce_numeric,
    _multi_employer_excess,
    _normalize_filing_status,
    _prorated_mortgage_interest,
    _validated_withholding_list,
)


# SB 951 removed the CA SDI taxable wage ceiling starting TY2024 — from that
# year there is no per-person maximum and therefore no excess-SDI credit.
_SDI_NO_CEILING_FROM = 2024


def compute_excess_sdi(
    sdi_by_employer: list,
    sdi_wages_by_employer: list,
    sdi_rate: float,
    sdi_wage_limit: float | None,
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
    average_mortgage_balance: float | None = None,
    investment_interest: float = 0.0,
    charitable_cash: float = 0.0,
    charitable_noncash: float = 0.0,
    misc_2pct_expenses: float = 0.0,
    *,
    ca_standard_deduction: float,
    phaseout_threshold: float | None = None,
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

    ca_standard_deduction is REQUIRED and keyword-only (Q7, ruled
    2026-09-13). It was optional, and without it this returned
    `use_itemized: None` with no `election_margin` — which a caller
    testing for truth reads as "do not itemize", under-stating the
    deduction for anyone whose CA standard deduction is the larger.
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
    med = _amount(medical_expenses, "medical_expenses")
    _amount(state_local_income_taxes, "state_local_income_taxes")
    re_tax = _amount(real_estate_taxes, "real_estate_taxes")
    pp_tax = _amount(personal_property_taxes, "personal_property_taxes")
    mort_int = _amount(mortgage_interest, "mortgage_interest")
    points = _amount(mortgage_points, "mortgage_points")
    inv_int = _amount(investment_interest, "investment_interest")
    cash = _amount(charitable_cash, "charitable_cash")
    noncash = _amount(charitable_noncash, "charitable_noncash")
    misc = _amount(misc_2pct_expenses, "misc_2pct_expenses")

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

    # What §17077 can reduce: medical and investment interest are outside it.
    subject = max(0.0, subtotal - medical_deductible - inv_int)
    phaseout = 0.0
    if phaseout_threshold is None:
        # Only where there is something to overstate. The note fired on every
        # unfetched call, including a return with no itemized deductions at
        # all, where the phase-out cannot bite at any published threshold — a
        # STOP-shaped warning on correct data, which is what teaches an agent
        # to read past the one that is real. Gated on the SUBJECT amount, not
        # the subtotal: a return of nothing but investment interest has
        # deductions and still nothing the phase-out can touch. The AGI is
        # interpolated because the threshold is the figure that is missing: a
        # reader who knows the published tier can decide in one glance whether
        # this one matters.
        if subject > 0:
            notes.append(
                f"CA itemized phase-out threshold (R&TC §17077, indexed) not "
                f"in the tax_law cache — phase-out NOT applied against a "
                f"federal AGI of ${fed_agi:,.2f} and ${subject:,.2f} of "
                f"deductions subject to it, so the CA deduction may be "
                f"OVERSTATED. Run /tax-setup to fetch state.itemized_phaseout")
    else:
        thr = _amount(phaseout_threshold, "phaseout_threshold")
        if fed_agi > thr:
            phaseout = round(min(_CA_ITEMIZED_PHASEOUT_RATE * (fed_agi - thr),
                                 _CA_ITEMIZED_PHASEOUT_MAX * subject), 2)
            notes.append(
                f"CA itemized phase-out applied: −${phaseout:,.2f} (lesser "
                "of 6% of AGI over threshold or 80% of subject deductions)")
    itemized_total = round(subtotal - phaseout, 2)

    # The election is UNCONDITIONAL now (Q7, ruled 2026-09-13). It used to be
    # skipped when no standard deduction was supplied, and `use_itemized` came
    # back None with `election_margin` absent — both of which read as "no" to a
    # caller testing them for truth, so a taxpayer whose CA standard deduction
    # was the larger of the two took `itemized_total` instead: UNDER-stating
    # the deduction and over-stating the tax. A banner was what that direction
    # was owed, and it was added; requiring the figure is what stops there
    # being an election to warn about.
    std = _amount(ca_standard_deduction, "ca_standard_deduction")
    if mfs_spouse_itemizes:
        use_itemized = True
        notes.append(
            "MFS/RDP-separate with an itemizing spouse MUST itemize for "
            "CA too — the CA standard deduction is unavailable")
    else:
        use_itemized = itemized_total > std
    if use_itemized and itemized_total > std:
        notes.append(
            "CA itemizes even when the federal return takes the "
            "standard deduction — the elections are independent")

    return {
        "medical_deductible": medical_deductible,
        "taxes_deductible": taxes_deductible,
        "mortgage_deductible": mortgage_deductible,
        "investment_interest": round(inv_int, 2),
        "charitable_deductible": charitable_deductible,
        "misc_deductible": misc_deductible,
        "charitable_carryforward": ca_charitable_carryforward,
        "phaseout_reduction": phaseout,
        "itemized_total": itemized_total,
        "use_itemized": use_itemized,
        "election_margin": round(itemized_total - std, 2),
        "notes": notes,
    }


# CA Schedule P (540) statutory-structural constants (R&TC §17062, §17062.1
# conforming to IRC §§55-59 as of 2015-01-01). The 7% tentative-minimum-tax
# rate and the 25% exemption phase-out are set in statute and NOT indexed —
# inline with citation, like the 1% mental-health surcharge. CA does NOT adopt
# OBBBA §70107 (the federal 50% phase-out from TY2026) — its conformity date is
# fixed at 2015, so CA stays 25% in every year.
_CA_AMT_TMT_RATE = 0.07
_CA_AMT_PHASEOUT_RATE = 0.25


def compute_ca_schedule_p(
    ca_taxable_income: float,
    ca_regular_tax: float,
    filing_status: str,
    exemption: float,
    phaseout_start: float,
    ca_preference_adjustments: float = 0.0,
    iso_bargain_element: float = 0.0,
    tmt_rate: float = _CA_AMT_TMT_RATE,
    phaseout_rate: float = _CA_AMT_PHASEOUT_RATE,
) -> dict:
    """California Schedule P (540) alternative minimum tax (N1).

    The CA analogue of federal Form 6251 — v1 models the same ISO-exerciser
    path, since a CA equity-comp filer who triggers federal AMT on an ISO
    exercise almost always triggers CA AMT on the SAME spread (CA conforms to
    the ISO adjustment). CA differs from the federal computation in three ways,
    all reflected here: a FLAT 7% tentative-minimum-tax rate (no 26/28 tiers, so
    no rate_28 breakpoint), NO Part III capital-gains coordination (CA taxes
    capital gains as ordinary income), and a fixed 25% exemption phase-out that
    does NOT follow OBBBA §70107 (CA's conformity date is 2015).

    Args:
        ca_taxable_income: Form 540 line 19 (CA regular taxable income) — the
            Schedule P Part I line 1 starting point. May be negative (accepted).
        ca_regular_tax: the CA regular tax before AMT (the Schedule P comparison
            base) — CA AMT is the excess of TMT over this.
        exemption / phaseout_start: the CA AMT exemption and its phase-out
            threshold for the filing status, from state.amt in the tax_law
            cache (INDEXED annually — REQUIRED, never hardcoded; an unfetched
            table hard-errors rather than zeroing a CA ISO exerciser's AMT).
        ca_preference_adjustments: net CA Schedule P Part I adjustments and
            preference items OTHER than the ISO spread (signed) — scoped v1 to
            what the workspace already carries. Excludes the ISO spread, which
            is passed separately so the deferral/exclusion carryforward split
            can be computed.
        iso_bargain_element: the ISO exercise spread (FMV − strike for shares
            not sold in the same year) — the CA deferral item that generates the
            Schedule P AMT credit carryforward.

    Returns dict: ca_amti, ca_exemption_allowed, ca_tmt, ca_regular_tax,
    ca_amt (added to Form 540 tax), schedule_p_carryforward (deferral-
    attributable CA AMT — persists to next year via /tax-rollover, the Form
    8801 pattern), iso_bargain_element echo, notes.
    """
    fs = _normalize_filing_status(filing_status)
    for what, v in (("exemption", exemption), ("phaseout_start", phaseout_start)):
        if v is None:
            raise ValueError(
                f"state.amt.{what} is unfetched — refusing to compute CA "
                "Schedule P without the indexed table (a guessed exemption "
                "would silently zero the CA AMT for an ISO exerciser); fetch "
                "via /tax-setup")
    ti = _amount(ca_taxable_income, "ca_taxable_income", allow_negative=True)
    rtax = _amount(ca_regular_tax, "ca_regular_tax")
    ex = _amount(exemption, "exemption")
    ph_start = _amount(phaseout_start, "phaseout_start")
    adj = _amount(ca_preference_adjustments,
                        "ca_preference_adjustments", allow_negative=True)
    iso = _amount(iso_bargain_element, "iso_bargain_element")
    for what, r in (("tmt_rate", tmt_rate), ("phaseout_rate", phaseout_rate)):
        if isinstance(r, bool) or not isinstance(r, (int, float)) \
                or not math.isfinite(float(r)) or not (0 < float(r) <= 1):
            raise ValueError(f"{what} must be a finite fraction in (0, 1], got {r!r}")
    tmt_rate = float(tmt_rate)
    phaseout_rate = float(phaseout_rate)

    def _tmt(iso_amount: float) -> float:
        amti = ti + adj + iso_amount
        exemption_allowed = max(0.0, ex - phaseout_rate * max(0.0, amti - ph_start))
        return tmt_rate * max(0.0, amti - exemption_allowed)

    # Round the TMT and the regular tax at source so the returned breakdown
    # reconciles: ca_amt == round(ca_tmt − ca_regular_tax) exactly (the §6651
    # breakdown-reconciliation lesson — deriving ca_amt from the UNROUNDED tmt
    # while displaying a rounded ca_tmt can drift a cent at a boundary). The tax
    # form itself works with the rounded TMT, so this is also more form-accurate.
    rtax_r = round(rtax, 2)
    tmt_r = round(_tmt(iso), 2)
    ca_amt = round(max(0.0, tmt_r - rtax_r), 2)
    # Schedule P AMT credit: like Form 8801, the credit equals CA AMT
    # attributable to DEFERRAL items — refigure with exclusion items only (v1's
    # only deferral item is the ISO spread), so the split is the with/without-
    # ISO delta. Both AMT figures come from rounded TMTs for the same reason.
    amt_exclusion_only = round(max(0.0, round(_tmt(0.0), 2) - rtax_r), 2)
    carryforward = round(max(0.0, ca_amt - amt_exclusion_only), 2)

    amti_full = ti + adj + iso
    exemption_allowed = max(0.0, ex - phaseout_rate * max(0.0, amti_full - ph_start))
    notes = [
        "v1 models the CA ISO-exerciser path (7% flat TMT, 25% exemption "
        "phase-out per R&TC §17062); other CA Schedule P preference/adjustment "
        "items go through ca_preference_adjustments and are the caller's to "
        "supply. CA taxes capital gains as ordinary income, so there is no "
        "federal-style Part III coordination.",
    ]
    # MFS caveat: the federal §55(d)(3) MFS exemption-RECAPTURE addback (AMTI
    # increased past the phase-out ceiling, which compute_form_6251 models) is
    # NOT applied here — CA's exemption still phases to $0 at the ceiling
    # (modeled), but whether Schedule P carries the additional AMTI add-back is
    # not confirmed in the FTB instructions, so it is flagged rather than
    # invented (inventing it would OVERstate; the exemption-to-zero is the
    # modeled, verified part). Fires only for an MFS filer past the ceiling.
    if fs == "MFS":
        ceiling = ph_start + ex / phaseout_rate
        if amti_full > ceiling:
            notes.append(
                "MFS filer past the exemption phase-out ceiling: the CA "
                "exemption is fully phased out (modeled), but any federal-style "
                "§55(d)(3) MFS AMTI recapture add-back is NOT modeled — verify "
                "the MFS exemption worksheet on Schedule P; the CA AMT may be "
                "understated.")
    if ca_amt > 0:
        notes.append(
            f"CA AMT of ${ca_amt:,.2f} applies (added to the Form 540 tax). "
            f"${carryforward:,.2f} of it is deferral-attributable (ISO) and "
            "becomes a Schedule P AMT credit carryforward — record it via "
            "/tax-rollover; losing it forfeits the money permanently.")
    return {
        "ca_amti": round(amti_full, 2),
        "ca_exemption_allowed": round(exemption_allowed, 2),
        "ca_tmt": tmt_r,
        "ca_regular_tax": rtax_r,
        "ca_amt": ca_amt,
        "schedule_p_carryforward": carryforward,
        "iso_bargain_element": round(iso, 2),
        "notes": notes,
    }
