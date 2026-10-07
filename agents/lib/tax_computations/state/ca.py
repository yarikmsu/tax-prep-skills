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
    _amount,
    _coerce_numeric,
    _medical_deductible,
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
    mfs_spouse_itemizes: bool | None = None,
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
    # KEYWORD-ONLY, and added here rather than beside its siblings: inserted
    # into the positional block it shifted `married_filing_separately` by one
    # for every caller that passed it by position, turning a $1,000 deduction
    # into a ValueError.
    mfs_spouse_is_a_separate_filer: bool | None = None,
    ca_standard_deduction: float,
    phaseout_threshold: float | None = None,
    charitable_prior_ledger: dict | None = None,
    charitable_worksheet: dict | None = None,
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
      excludes those too). phaseout_threshold None REFUSES where the return
      has deductions subject to it (Q8, 2026-09-13): skipping the phase-out
      over-states a deduction on a filed return, and AGENTS.md §2 allows a
      degrade only where the output is advisory AND under-states. It was a
      loud note, which is what the other direction is owed.

    EVERY AGI-anchored floor/cap here (medical 7.5%, charitable 50%/30%,
    misc 2%) uses FEDERAL AGI — Schedule CA Part II lines reference federal
    Form 1040 line 11, not CA AGI. ca_agi is accepted for future CA-AGI-
    based lines but is currently unused in the math; a CA-AGI base would
    overstate a retiree's medical deduction (CA subtracts Social Security,
    so CA AGI < federal AGI).

    mfs_spouse_itemizes: the spouse's election, which binds this return in
    BOTH directions — a yes forces itemizing (their itemizing zeroes this
    filer's standard deduction under §63(c)(6)(A), incorporated by R&TC
    §17073.5), and a no bars it (FTB: "both itemize ... or both take the
    standard deduction"). REQUIRED whenever married_filing_separately is
    set, and the two are refused apart: unknown is not "no", and a default
    would assert something about a return nobody asked about. The engine
    cannot see whether the spouse is CONSIDERED UNMARRIED and filing as head
    of household, in which case the coupling does not bind at all; the note
    it emits says so.

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
    if not isinstance(married_filing_separately, bool):
        raise ValueError("married_filing_separately must be a boolean, got "
                         f"{married_filing_separately!r}")
    if mfs_spouse_itemizes is not None \
            and not isinstance(mfs_spouse_itemizes, bool):
        raise ValueError("mfs_spouse_itemizes must be a boolean or None, got "
                         f"{mfs_spouse_itemizes!r}")
    # UNKNOWN is not "no". The flag used to default False and False used to be
    # NEUTRAL — it meant "run the ordinary comparison". Now that the coupling
    # binds both ways, False is an assertion about the SPOUSE's return, and a
    # caller who simply did not ask would have that assertion made for them:
    # the standard deduction taken while the spouse itemizes, over-stating by
    # up to the whole standard deduction on the commonest separate-return
    # shape — one spouse holding the mortgage and property tax, the other
    # holding almost nothing. The tax-setup interview records this answer, so
    # a run that has it can pass it; a run that cannot is a run that must not
    # guess. One symbol stopped meaning two things.
    if married_filing_separately and mfs_spouse_itemizes is None:
        raise ValueError(
            "married_filing_separately=True needs mfs_spouse_itemizes — the "
            "CA election is the spouse's to make as much as this filer's, and "
            "defaulting it silently picks one. Ask whether the spouse "
            "itemizes (tax-setup records it) and pass True or False.")
    if mfs_spouse_itemizes is not None and not married_filing_separately:
        raise ValueError(
            "mfs_spouse_itemizes needs married_filing_separately=True — a "
            "spouse's election only binds this return when it is filed "
            "separately, and IGNORING the flag would restore the ordinary "
            "itemized-vs-standard comparison for a filer the coupling "
            "governs.")
    if mfs_spouse_is_a_separate_filer is not None \
            and not isinstance(mfs_spouse_is_a_separate_filer, bool):
        raise ValueError("mfs_spouse_is_a_separate_filer must be a boolean or "
                         f"None, got {mfs_spouse_is_a_separate_filer!r}")
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

    medical_deductible = _medical_deductible(med, agi_v)
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

    charity_settlement = {}
    if charitable_prior_ledger is not None or charitable_worksheet is not None:
        from agents.lib.tax_computations.charitable import settle_charitable_ledger
        charity_settlement = settle_charitable_ledger(
            charitable_prior_ledger, charitable_worksheet, tax_year=tax_year,
            jurisdiction="CA", agi=federal_agi, charitable_cash=cash,
            charitable_noncash=noncash)
        charitable_deductible = charity_settlement["charitable_deductible"]
        ca_charitable_carryforward = charity_settlement["charitable_carryforward"]
        notes = [n for n in notes if not n.startswith("CA cash charitable cap")]
        notes.append("CA charitable vintages use the separate reviewed CA worksheet")

    misc_deductible = round(max(0.0, misc - 0.02 * agi_v), 2)
    if misc > 0:
        notes.append("misc 2%-floor deductions allowed for CA only "
                     "(federal TCJA suspension does not apply)")

    subtotal = round(medical_deductible + taxes_deductible
                     + mortgage_deductible + inv_int
                     + charitable_deductible + misc_deductible, 2)

    # What §17077 can reduce: medical and investment interest are outside it.
    # ROUNDED, because `subtotal` already is and subtracting unrounded exempt
    # amounts from it leaves float residue — 1e-13 of "deductions subject to
    # the phase-out" on a return made entirely of the two categories the FTB
    # worksheet exempts. That residue is > 0, so it armed the refusal below and
    # then printed "$0.00" as the amount at stake.
    # Each component is finite — `_amount` rejects NaN and infinity at the
    # boundary — and their SUM need not be. Two deductions near the float
    # maximum overflow to infinity here, and infinity defeats the endpoint
    # comparison below the same way NaN defeats a range check: both ends
    # return `inf`, they compare EQUAL, and the refusal passes a return whose
    # itemized total is `inf`. The enumerated guard refused it by accident,
    # through `subject > 0`; this says so on purpose.
    if not math.isfinite(subtotal):
        raise ValueError(
            "CA itemized deductions sum to a non-finite total — the "
            "individual amounts are each valid but their sum overflows. "
            "Check the source figures; no return has deductions this large.")
    subject = round(max(0.0, subtotal - medical_deductible - inv_int), 2)
    std = _amount(ca_standard_deduction, "ca_standard_deduction")
    # What actually reaches the return, given a phase-out reduction. ONE
    # expression, used both by the refusal below and by the election further
    # down. The refusal used to restate this rule instead of reading it, and
    # the two drifted in the OVER-stating direction.
    #
    # FTB Form 540, married/RDP filing separately (verified 2026-09-14): "you
    # and your spouse/RDP must either both itemize your deductions (even if
    # the itemized deductions of one spouse/RDP are less than the standard
    # deduction) or both take the standard deduction." The coupling binds in
    # BOTH directions and it is not an election either spouse makes alone, so
    # for a separate filer the spouse's choice decides it outright and the
    # comparison never runs.
    #
    # Only the spouse-ITEMIZES half was implemented. A separate filer whose
    # spouse took the STANDARD deduction still itemized whenever itemizing was
    # larger — $12,000 against the $5,706 allowed on the case in the test,
    # $6,294 OVER-stated on a filed return, which is §2's hard-error
    # direction. `compute_ny_itemized` has forced both halves since #156, and its
    # comment said this function "has forced the mirror-image federal
    # §63(c)(6)(A) case since it was written" — true of the half it was
    # looking at. The citation stood for ONE DAY — #156 landed 2026-09-13 and
    # this 2026-09-14 — while the half it cited had been missing since #33 on
    # 2026-07-13, two months. Inspecting a sibling closely enough to quote it
    # is not the same as sweeping it.
    def elects_itemized(total: float) -> bool:
        if (married_filing_separately and mfs_spouse_itemizes is False
                and mfs_spouse_is_a_separate_filer is False):
            # The spouse is considered unmarried, files as head of household
            # and does NOT itemize. They do file their own return — what they
            # are not is a MARRIED individual filing SEPARATELY, which is the
            # status §63(c)(6)(A) and FTB's both-or-neither both key on. So
            # the coupling does not bar this filer and the ordinary
            # comparison governs.
            #
            # ONE DIRECTION ONLY. §63(c)(6)(A) zeroes the standard deduction
            # of "a married individual filing a separate return where EITHER
            # spouse itemizes" — THIS filer is such an individual whatever
            # their spouse's own filing status, so a spouse who itemizes still
            # forces itemizing here. Written symmetrically it handed that
            # filer `max(total, std)`, which takes a standard deduction
            # §63(c)(6)(A) has already zeroed for them whenever their own
            # itemized total is the smaller — "may not have" understated it:
            # they do not have one at all.
            return total > std
        if married_filing_separately:
            # `is True`, not a bare return: None is refused above, but that
            # guard narrows for a READER and not for mypy, which still sees
            # `bool | None` here. A guard a person can follow and the checker
            # cannot is exactly how a float-minus-None shipped two rounds ago,
            # so the narrowing is spelled where the value is used.
            return mfs_spouse_itemizes is True
        return total > std

    def reaches_the_return(reduction: float) -> float:
        """The figure that reaches the return, given a phase-out reduction.

        The ELECTED figure, and deliberately not also the published
        `itemized_total`. For a separate filer whose spouse takes the standard
        deduction the election is constant, so no threshold can change what is
        filed and refusing would be a STOP on a return the missing figure
        cannot affect — the cry-wolf case five rounds were spent removing.

        `itemized_total` and `election_margin` DO still move with the
        threshold for that filer, and come back un-reduced. They are not
        filed; they are the counterfactual a preparer reads when advising the
        couple whether the spouse should itemize. So they are LABELLED rather
        than refused: see the note below, which says the figure is before an
        uncomputed §17077 reduction instead of printing it as an alternative.
        """
        total = round(subtotal - reduction, 2)
        return total if elects_itemized(total) else std

    phaseout = 0.0
    if phaseout_threshold is None:
        # Q8, ruled 2026-09-13 and now AGENTS.md §2: a fallback may DEGRADE
        # only when the output is advisory AND it under-states. This is a
        # figure on a filed CA return and skipping the phase-out OVER-states
        # the deduction, so it STOPS. It was a banner, which is what the
        # under-statement direction is owed and not this one.
        #
        # WHEN it stops is derived, not enumerated. Five review rounds each
        # added one more hand-proved exemption — "at a zero subject amount no
        # threshold matters", "at a zero AGI none matters", "when the standard
        # deduction already wins none matters" — and the fifth of them was
        # wrong, because it open-coded one branch of an election computed forty
        # lines below. A gate is a claim about which inputs cannot matter, and
        # a hand-written claim is only ever as complete as the cases somebody
        # thought of.
        #
        # So price BOTH ENDS of the range the missing threshold spans instead.
        # The reduction is monotone non-increasing in the threshold and reaches
        # the return through one expression, so two evaluations settle it:
        # zero at a threshold at or above this AGI, and its largest value at a
        # threshold of zero. `thr >= 0` is not a tax figure — it is "a dollar
        # threshold is not negative" — so nothing indexed is hardcoded here.
        # If the same figure reaches the return at both ends, no threshold in
        # between can change it.
        #
        # `test_the_derived_gate_matches_the_conditions_it_replaced` holds this
        # to those conditions AS CORRECTED — with the `not mfs_spouse_itemizes`
        # the shipped version lacked — and against that it adds no STOP. Say it
        # that way round: measured against what actually SHIPPED it adds 116
        # stops over a grid, every one of them an MFS filer whose spouse
        # itemizes, which is the defect. "Adds no STOP" about the shipped
        # guard would be false and would hide the fix.
        largest = round(min(_CA_ITEMIZED_PHASEOUT_RATE * max(0.0, fed_agi),
                            _CA_ITEMIZED_PHASEOUT_MAX * subject), 2)
        if reaches_the_return(0.0) != reaches_the_return(largest):
            raise ValueError(
                f"CA itemized phase-out threshold (R&TC §17077, indexed) is "
                f"not in the tax_law cache, and this return has "
                f"${subject:,.2f} of deductions subject to it at a federal AGI "
                f"of ${fed_agi:,.2f} — up to ${largest:,.2f} of reduction "
                f"turns on the missing figure. Computing without it OVER-states "
                f"the CA deduction. Run /tax-setup to fetch "
                f"state.itemized_phaseout, or pass phaseout_threshold "
                f"explicitly.")
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
    # The coupling bars itemizing only when the spouse is an ordinary separate
    # filer. A spouse who is CONSIDERED UNMARRIED and files as head of
    # household is not a MARRIED individual filing SEPARATELY — they file
    # their own return under a different status — so §63(c)(6) never reaches
    # them and FTB's both-or-neither does not bind this filer either —
    # they may itemize. Barring them anyway UNDER-states line 18 by the whole
    # margin: $12,000 against $5,706 on the case in the test.
    #
    # This STOPS rather than degrading, and the earlier draft of this change
    # got that wrong. AGENTS.md §2 allows a banner INSTEAD of a stop only when
    # the output is ADVISORY as well as under-stating, and line 18 is filed.
    # An under-statement is not a safe harbour on a return.
    #
    # It asks only where the answer changes the number: the question is moot
    # unless the coupling would actually bar a larger itemized total. That is
    # NOT "only an unusual return sees it" — an ordinary separate-filing
    # couple with itemized deductions worth more than the standard reaches it
    # every time, and should, because that is precisely where the two answers
    # differ. What it spares is the return where they do not. Both ends of the
    # unknown are priced, the same shape the §17077 gate above uses.
    if (married_filing_separately and mfs_spouse_itemizes is False
            and itemized_total > std
            and mfs_spouse_is_a_separate_filer is None):
        # Asked only on the NO branch, and only when itemizing wins: those are
        # exactly the returns where the spouse's status changes the figure.
        raise ValueError(
            f"This filer's ${itemized_total:,.2f} of CA itemized deductions "
            f"beat their ${std:,.2f} standard deduction, and whether they may "
            f"take them turns on the SPOUSE. If the spouse files their own "
            f"separate return, FTB's both-or-neither rule bars itemizing here "
            f"and the answer is ${std:,.2f}; if the spouse is CONSIDERED "
            f"UNMARRIED and files as head of household, they are not a "
            f"separate filer, the coupling does not bind, and the answer is "
            f"${itemized_total:,.2f}. Pass mfs_spouse_is_a_separate_filer. "
            f"The two differ by ${itemized_total - std:,.2f} on a filed "
            f"return, so this is not a figure to default.")

    use_itemized = elects_itemized(itemized_total)
    if married_filing_separately and mfs_spouse_itemizes:
        notes.append(
            "MFS/RDP-separate with an itemizing spouse MUST itemize for "
            "CA too — the CA standard deduction is unavailable"
            + (f", and the itemized deduction is the SMALLER of the two: "
               f"${itemized_total:,.2f} against a ${std:,.2f} standard "
               f"deduction this filer may not take"
               if itemized_total < std else ""))
    elif married_filing_separately and mfs_spouse_is_a_separate_filer is False:
        notes.append(
            "MFS/RDP-separate, but the spouse is CONSIDERED UNMARRIED and "
            "files as head of household — they file their own return, but "
            "not as a married person filing separately, which is the status "
            "FTB's both-or-neither rule keys on. It does not bind this filer, "
            "so the ordinary itemized-vs-standard comparison governs.")
    elif married_filing_separately:
        # The counterfactual is only printed when it was actually COMPUTED.
        # Without the threshold the phase-out never ran for this filer — the
        # election does not depend on it, so nothing refused — and the
        # itemized total is un-reduced by up to 80% of the subject amount.
        # Printing it as "itemizing would have been $X" put an over-stated
        # alternative in front of a preparer deciding whether the spouse
        # should itemize instead. Said as what it is, or not said.
        alternative = ""
        if itemized_total > std and phaseout_threshold is not None:
            alternative = (f", though itemizing would have been "
                           f"${itemized_total:,.2f}")
        elif itemized_total > std:
            alternative = (
                f", and itemizing would have been ${itemized_total:,.2f} "
                f"BEFORE an §17077 phase-out that could not be computed — "
                f"fetch state.itemized_phaseout before acting on that figure")
        notes.append(
            "MFS/RDP-separate: BOTH spouses must itemize or NEITHER may (FTB "
            "Form 540), and the spouse is NOT itemizing — the CA standard "
            "deduction is taken here" + alternative
            + ". If the spouse is CONSIDERED UNMARRIED and files as head of "
            "household, they are not an MFS filer and this coupling does not "
            "bind: say so and re-run, because this filer may then itemize.")
    if use_itemized and itemized_total > std:
        notes.append(
            "CA itemizes even when the federal return takes the "
            "standard deduction — the elections are independent")

    if charity_settlement:
        assert charitable_worksheet is not None
        if charitable_worksheet["itemizes"] != use_itemized:
            raise ValueError("CA charitable worksheet election differs; resolve absorption for the actual election")
    return {
        **charity_settlement,
        "charitable_inputs": ({"charitable_prior_ledger": charitable_prior_ledger,
                               "charitable_worksheet": charitable_worksheet,
                               "agi": federal_agi, "charitable_cash": cash,
                               "charitable_noncash": noncash} if charity_settlement else None),
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
