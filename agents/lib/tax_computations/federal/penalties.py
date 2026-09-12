#!/usr/bin/env python3
"""Form 2210, §6651 and §6601 — what a late or underpaid return costs.

One file because they are one question asked three ways: did enough arrive in
time (Form 2210 and the two §6654 prongs), what does filing or paying late
cost (§6651), and what does the unpaid balance accrue (§6601/§6622). They
share the rate-period machinery and the date validators, which is why the
§1291 interest in `international.py` reaches back into this module rather than
growing a second copy.
"""

import calendar
import datetime
import math

import agents.lib  # noqa: F401 — the host-interpreter guard
from agents.lib.irs_dates import next_business_day
from agents.lib.tax_computations._shared import (
    _amount,
    _coerce_numeric,
    _normalize_filing_status,
)

# Form 2210 structural statutory constants (IRC §6654, none indexed):
# required annual payment = LESSER of 90% of current-year tax or 100%/110%
# of prior-year tax (110% above $150K prior-year AGI, $75K MFS) — §6654(d);
# no penalty when the balance after withholding is under $1,000 — §6654(e)(1);
# no penalty when there was no prior-year LIABILITY — §6654(e)(2).
# PUBLIC, and named without the form number, because they are not Form 2210's:
# they are §6654's, and `projection.compute_safe_harbor` applies the same rule
# for the same reason. `tax-safe-harbor` then RESTATES them in prose, having no
# engine to call — so this is one statutory quantity with three readers, and the
# copy that prices the penalty on the filed return is this one. Two named copies
# plus a sentence, related by nothing, is how an amendment lands in one module
# and leaves the other two quoting the old law while every test stays green.
# Ints, not floats: `f"${x:,}"` renders 150000.0 as "$150,000.0", and the skill
# parity test formats these to compare against the prose. The de-minimis floor
# was left `_F2210_`-named and private by the revision that made its neighbours
# public — inside this comment, which says why neither is right: §6654(e)(1) is
# the authority, and the skill states the same $1,000 in step 6. It is public
# and interpolated into the message below for the same reason as the rest.
DE_MINIMIS_BALANCE = 1000
PRIOR_YEAR_AGI_THRESHOLD = 150_000
PRIOR_YEAR_AGI_THRESHOLD_MFS = 75_000
PRIOR_YEAR_FACTOR_HIGH = 1.10
PRIOR_YEAR_FACTOR = 1.00


def prior_year_agi_threshold(mfs: bool) -> int:
    """The §6654(d)(1)(C)(i) prior-AGI threshold above which 110% applies.

    Its own function because the threshold has a SECOND reader: the note
    `compute_form_2210` writes when prior AGI is unknown quotes the figure it
    could not test against. Selecting it there by hand is the same two-copy
    drift the prong below exists to remove, one layer down.
    """
    return PRIOR_YEAR_AGI_THRESHOLD_MFS if mfs else PRIOR_YEAR_AGI_THRESHOLD


def prior_year_prong(prior_tax: float, prior_agi: "float | None",
                     mfs: bool) -> "tuple[float, float]":
    """§6654(d)(1)(C)'s prior-year prong: `(factor, prior_based)`.

    The constants above were unified across three modules while the four
    lines that APPLY them stayed hand-copied in each — which is the half an
    amendment has to change. §6654(d)(1)(C)(i) carried a different percentage
    schedule before 2002 and §6654(i)(1) suppresses the factor outright for
    farmers and fishermen; implementing either against three copies is three
    edits with nothing relating them, and the comment above says why that is
    how one module ends up quoting the old law with every test still green.

    CA conforms to both the thresholds and the factor (R&TC §19136 via FTB
    5805), so `compute_ca_safe_harbor` reads this too. A future CA
    de-conformity is what would split them, and it would split them HERE.

    Callers keep what differs: their own `min(pct90, ...)` rounding, and
    whether a missing prior AGI is flagged to the reader. A missing prior AGI
    takes the 100% factor — the UNDERSTATING direction, flagged by every
    caller rather than silently assumed.
    """
    factor = (PRIOR_YEAR_FACTOR_HIGH
              if (prior_agi or 0) > prior_year_agi_threshold(mfs)
              else PRIOR_YEAR_FACTOR)
    return factor, round(factor * prior_tax, 2)


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
    prior_year_tax: float | None = None,
    prior_year_agi: float | None = None,
    estimated_payments: list | None = None,
    underpayment_rates: list | None = None,
    prior_year_zero_liability_confirmed: bool = False,
    prior_year_return_filed_12_months_confirmed: bool = False,
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
        prior_year_tax: completed pre-factor prior-year tax base constructed
            under line 8 of the Form 2210 instructions for `tax_year`; never
            raw Form 1040 line 24 or Form 2210 line 8 after its factor. None =
            unresolved → the required annual payment degrades to the
            90%-of-current test (the prior-year harbor could only LOWER it).
            Explicit 0 → no required annual payment, by whichever of the two
            flags below is confirmed.
        prior_year_agi: prior-year AGI for the 110% factor. None with a
            non-zero prior_year_tax assumes the 100% factor and notes the
            possible understatement.
        prior_year_zero_liability_confirmed: §6654(e)(2) — the prior year had
            no tax liability, covered 12 months, and the taxpayer was a US
            citizen or resident THROUGHOUT it. Clears the year outright. Does
            NOT require that a return was filed.
        prior_year_return_filed_12_months_confirmed: §6654(d)(1)(B)'s closing
            text — the prior year covered 12 months AND a return was filed for
            it. Keeps a $0 prior-year tax available to the ORDINARY prong,
            where 100%/110% of $0 is $0. Carries NO citizenship or residency
            condition, which is the whole reason it is asked separately: the
            two tests are independent, and one flag standing for both refused
            a $0 prior year to every taxpayer who could not affirm citizenship
            — demanding 90% of the current year from someone §6654(d)(1)(B)(ii)
            already covers.
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
    tax = _amount(current_year_tax, "current_year_tax",
                        allow_negative=True)
    wh = _amount(withholding, "withholding")

    est_events = []
    for i, p in enumerate(estimated_payments or []):
        if not isinstance(p, dict):
            raise ValueError(f"estimated_payments[{i}] must be an object")
        amt = _amount(p.get("amount"), f"estimated_payments[{i}].amount")
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

    # Rounded to cents BEFORE the strict `<` comparison, and the SAME rounded
    # figure printed in the reason. Two already-rounded dollar amounts can
    # subtract to 999.9999999999982 in raw float — under the floor, on a
    # balance that is exactly $1,000.00 — and the message then reads "$1,000.00
    # is under the $1,000 de minimis", contradicting itself while clearing a
    # penalty that attaches. `projection.compute_safe_harbor` rounds for this
    # reason; this is the copy that prices the penalty on the FILED return, so
    # the two must not disagree (pinned by test).
    balance_after_withholding = round(tax - wh, 2)
    if balance_after_withholding < DE_MINIMIS_BALANCE:
        result["no_penalty"] = True
        result["no_penalty_reason"] = (
            f"balance after withholding (${balance_after_withholding:,.2f}) is "
            f"under the ${DE_MINIMIS_BALANCE:,} de minimis (§6654(e)(1)) — no "
            f"penalty")
        return result

    # Distinguishes "no prior-year figures were supplied" from "they were
    # supplied and the §6654(e)(2) gate below dropped them". Both reach the
    # `prior_year_tax is None` branch, and without this the second one also
    # collected that branch's note — so a caller who HAD supplied the figures
    # was told they were unavailable, and told that supplying them "can only
    # LOWER" the requirement, six lines after being told that confirming them
    # would clear the year outright. Two notes, opposite instructions, same
    # result dict.
    prior_dropped_unconfirmed_zero = False
    if prior_year_tax is not None:
        pt = _amount(prior_year_tax, "prior_year_tax")
        if pt == 0:
            # A $0 prior-year tax has TWO independent statutory routes to a
            # zero requirement, and this gated both behind the FIRST one's
            # preconditions:
            #   §6654(e)(2)      — no prior liability + 12-month prior year +
            #                      US citizen/resident throughout it. Clears
            #                      the year outright; needs no filed return.
            #   §6654(d)(1)(B)(ii) — the ORDINARY prong at 100%/110% of $0.
            #                      Its closing text asks only for a 12-month
            #                      prior year AND a filed return, and says
            #                      NOTHING about citizenship or residency.
            # One flag standing for both meant a taxpayer who filed a full
            # 12-month prior return showing $0 — and who could not affirm the
            # citizenship half, or simply was not asked about it — had the $0
            # thrown away and was priced at 90% of the current year. That is
            # the over-stating direction, but it is over-stating a penalty
            # against a taxpayer the statute already covers, and
            # tax-safe-harbor SKILL.md has listed the two as separate
            # preconditions since it shipped: the engine-free skill and the
            # engine returned different answers for the same person.
            #
            # Unconfirmed on BOTH, the prong is still dropped entirely (NOT
            # taken at $0, which would zero the required payment by another
            # route) and the 90%-of-current test stands alone. A $0 prior year
            # is short/part-year often enough that reading it as a cleared year
            # unasked is the costly default.
            #
            # LITERAL booleans, never truthiness, on both flags. `bool("false")`
            # is True and so is `bool(1)`, and either flag can zero the
            # requirement — so a JSON string or an int reaching here suppresses
            # a penalty that may well apply, the over-clearing direction
            # AGENTS.md makes a hard error. The planning twin
            # (`projection._confirmed_flag`) has rejected non-booleans since it
            # shipped; this is the copy that prices the penalty on the FILED
            # return, and it is called by an agent assembling arguments out of
            # answers.md, which is exactly where a hand-typed "false" comes from.
            for _flag_name, _flag in (
                    ("prior_year_zero_liability_confirmed",
                     prior_year_zero_liability_confirmed),
                    ("prior_year_return_filed_12_months_confirmed",
                     prior_year_return_filed_12_months_confirmed)):
                if not isinstance(_flag, bool):
                    raise ValueError(
                        f"{_flag_name} must be a bool (True/False), got "
                        f"{type(_flag).__name__}")
            if prior_year_zero_liability_confirmed:
                result["no_penalty"] = True
                result["no_penalty_reason"] = (
                    "the §6654(e)(2) preconditions were CONFIRMED by the "
                    "caller: a 12-month prior year, NO LIABILITY for it, "
                    "and US citizenship or residence throughout. No "
                    "liability is that year's Form 1040 TOTAL TAX line at "
                    "$0, or a year that required no return at all — not "
                    "the $0 prior_year_tax passed here, which is net of "
                    "the refundable credits Form 2210 line 8 subtracts and "
                    "reaches $0 on a positive total tax — so nothing in "
                    "this engine verified it. No penalty")
                return result
            if prior_year_return_filed_12_months_confirmed:
                # Kept at $0 and run through the ordinary prong below, rather
                # than returned as a no-penalty here: the two are the same
                # arithmetic (min(90%, factor x $0) = $0) but not the same
                # finding, and the quarters table this builds is what a filer
                # attaches to Form 2210. §6654(e)(2) is a reason not to file
                # the form; this is a line 8 of zero.
                notes.append(
                    "prior-year tax is $0 and the §6654(d)(1)(B) conditions "
                    "were confirmed (prior year covered 12 months; a return "
                    "was filed for it): the ordinary prior-year prong applies "
                    "at 100%/110% of $0, so the required annual payment is $0. "
                    "§6654(e)(2) was not confirmed and is not needed for this "
                    "— it carries a citizenship/residency condition the "
                    "prior-year prong does not")
            else:
                prior_year_tax = None
                prior_dropped_unconfirmed_zero = True
                notes.append(
                    "prior-year tax is $0 but neither route to a $0 "
                    "requirement was confirmed: the prior-year prong is "
                    "dropped and the 90%-of-current test stands alone, which "
                    "OVERSTATES if either applies. §6654(e)(2) needs THREE "
                    "things, and this $0 is not one of them: a 12-month "
                    "prior year, NO LIABILITY for it, and US citizenship "
                    "or residence throughout. No liability is that year's "
                    "Form 1040 TOTAL TAX line at $0, or a year that "
                    "required no return at all — never this $0, which is "
                    "net of refundable credits — pass "
                    "prior_year_zero_liability_confirmed=True. §6654(d)(1)(B) "
                    "needs only a 12-month prior year AND a return filed for "
                    "it, with no citizenship condition — pass "
                    "prior_year_return_filed_12_months_confirmed=True instead")

    # Required annual payment — same math as projection.compute_safe_harbor
    # (kept in sync by test; projection imports THIS module, so the planning
    # dataclass can't be reused here without a circular import).
    pct90 = round(0.90 * tax, 2)
    if prior_year_tax is None:
        required = pct90
        basis = ("90% of current-year tax (prior-year prong dropped)"
                 if prior_dropped_unconfirmed_zero else
                 "90% of current-year tax (prior-year figures unavailable)")
        # Only when they really were absent. On the dropped-$0 path the note
        # appended above already says what happened and what to do, and this
        # one would contradict it on both counts.
        if not prior_dropped_unconfirmed_zero:
            notes.append(
                "prior-year tax/AGI unavailable: required annual payment uses "
                "the 90%-of-current test only — supplying prior-year figures "
                "can only LOWER it, never raise it")
    else:
        pa = (None if prior_year_agi is None
              else _amount(prior_year_agi, "prior_year_agi",
                                 allow_negative=True))
        factor, prior_based = prior_year_prong(pt, pa, fs == "MFS")
        required = min(pct90, prior_based)
        basis = (f"lesser of 90% of current ({pct90:,.2f}) and "
                 f"{factor:.0%} of prior-year tax ({prior_based:,.2f})")
        if pa is None and pt != 0:
            # `pt != 0` because at a $0 base the factor cannot matter: 110% of
            # $0 and 100% of $0 are both $0, so the note's claim that the
            # penalty "may be UNDERSTATED" is false on that path. A caveat
            # that is untrue where it fires teaches the reader to discount the
            # ones that are true.
            #
            # Selected inside the branch that quotes it, not beside the prong
            # that also selects it: computing it unconditionally put two reads
            # of one statutory figure six lines apart, in the block whose
            # purpose is to have exactly one. `prior_year_agi_threshold` is
            # what `prior_year_prong` tests against, and a test pins this note
            # to it so the pair cannot drift.
            threshold = prior_year_agi_threshold(fs == "MFS")
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


def compute_late_filing_penalty(
    unpaid_tax: float,
    ftf_months: int,
    ftp_months: int,
    filed_over_60_days_late: bool = False,
    min_penalty_floor: float | None = None,
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


_QUARTER_STARTS = ((1, 1), (4, 1), (7, 1), (10, 1))


def _quarter_start_on_or_before(d: datetime.date) -> datetime.date:
    """The §6621 quarter start (Jan/Apr/Jul/Oct 1) that CONTAINS d — i.e. the
    latest quarter start on or before d. The rate for d's interest period is the
    one effective on this date."""
    prior = [datetime.date(d.year, m, day) for (m, day) in _QUARTER_STARTS
             if datetime.date(d.year, m, day) <= d]
    return max(prior)  # d.year always has Jan 1 <= d, so prior is non-empty


def _next_quarter_start(d: datetime.date) -> datetime.date:
    """The earliest §6621 quarter start strictly after d. A rate effective on a
    quarter start is authoritative through the day BEFORE the next quarter start,
    so this is the coverage horizon of the last cached rate period."""
    for (m, day) in _QUARTER_STARTS:
        q = datetime.date(d.year, m, day)
        if q > d:
            return q
    return datetime.date(d.year + 1, 1, 1)


def _compound_daily(principal: float, start: datetime.date, end: datetime.date,
                    periods: list) -> float:
    """Daily-compounded interest (§6622) on `principal` across [start, end),
    split at rate changes AND Jan 1 (the daily factor uses days-in-year for the
    year each day falls in — 366 in a leap year). Returns the interest only
    (balance − principal). `periods` must already cover [start, end)."""
    if end <= start:
        return 0.0
    factor = 1.0
    for i, (eff, rate) in enumerate(periods):
        nxt = periods[i + 1][0] if i + 1 < len(periods) else None
        s = max(start, eff)
        e = end if nxt is None else min(end, nxt)
        while s < e:
            year_end = datetime.date(s.year + 1, 1, 1)
            sub_e = min(e, year_end)
            days = (sub_e - s).days
            denom = 366 if calendar.isleap(s.year) else 365
            factor *= (1.0 + rate / denom) ** days
            s = sub_e
    return principal * factor - principal


def compute_underpayment_interest(
    unpaid_tax: float,
    due_date,
    pay_date,
    underpayment_rates: list | None = None,
) -> dict:
    """§6601 interest (compounded daily per §6622) on a balance paid after the
    return due date.

    This is INTEREST, separate from the §6651 additions-to-tax
    (compute_late_filing_penalty) and from the §6654 estimated-tax penalty
    (compute_form_2210). Use it for the /tax-extend pay-now-vs-October decision
    and for interest on the additional tax in /tax-amend.

    Args:
        unpaid_tax: the balance on which interest runs — the tax not paid by the
            due date. Finite, non-negative. A $0 balance carries $0 interest.
        due_date: the UNEXTENDED return due date (ISO YYYY-MM-DD or a date) —
            interest runs from here even with a valid extension (an extension of
            time to FILE is not an extension of time to PAY). Pass the §7503
            OBSERVED due date (rolled past a weekend / DC holiday via
            irs_dates.next_business_day), NOT the statutory Apr 15 — payment is
            timely through the observed deadline, so interest cannot start before
            it (e.g. Apr 15 2028 is a Saturday → observed Apr 17).
        pay_date: the date the balance is (or will be) paid (ISO or a date).
        underpayment_rates: federal.underpayment_rates from the tax_law cache
            (list of {effective_from, annual_rate}). §6621 quarterly rates.

    Coverage contract (the load-bearing difference from compute_form_2210):
        A §6621 rate is authoritative only for its own quarter. This function
        NEVER extrapolates a cached rate across a NON-CONTIGUOUS or unfetched
        quarter — a §6621 rate is authoritative only for its own quarter, so
        coverage is a chain of contiguous quarter-start rates present in the
        cache. It reports honestly:
        - `underpayment_rates` empty → computed=False, interest=None (degrade).
        - The rate for the quarter CONTAINING `due_date` is absent (earliest
          cached rate is a later quarter, or an earlier one whose quarter doesn't
          reach the due date) → computed=False, interest=None (refuse rather than
          misattribute/skip the opening interest). Extend the fetch.
        - Coverage stops at the FIRST missing quarter — either a mid-timeline gap
          (e.g. Apr + Oct cached with no Jul → coverage stops Jul 1) or simply the
          end of the fetched span (the Oct-15 case: a balance paid Oct 15 needs
          the Jul + Oct quarters the 2210 fetch window stops short of) →
          computed=True, complete=False, interest = the PARTIAL interest THROUGH
          `covered_through`, with a loud note naming the uncovered span. The
          headline number is explicitly partial — fetch the missing quarters
          (see /tax-setup's M3 fetch scope).
        - Fully covered by a contiguous chain → computed=True, complete=True,
          interest = the full figure.

    Scope: interest on the tax only. §6601(e)(2) interest on the penalties
    themselves, and any interest-suspension provisions (§6404(g)), are not
    modeled. Estimator — verify against an IRS notice for a filed figure.

    Raises:
        ValueError: on non-finite / negative / wrong-type `unpaid_tax`, a
            malformed date, pay_date < due_date, or a corrupt rate entry.
    """
    if isinstance(unpaid_tax, bool):
        raise ValueError("unpaid_tax must be a number, not bool")
    ut = _coerce_numeric(unpaid_tax, default=None)
    if ut is None or not math.isfinite(ut):
        raise ValueError(f"unpaid_tax must be a finite number, got {unpaid_tax!r}")
    if ut < 0:
        raise ValueError(f"unpaid_tax cannot be negative (got {ut})")

    # Normalize each input to a plain date. datetime.datetime is a SUBCLASS of
    # datetime.date, so a naive isinstance(x, date) branch would retain a
    # datetime and later raise a raw TypeError on a mixed date/datetime compare;
    # collapse it to .date() so the arithmetic (and the documented ValueError
    # contract) hold. A string goes through the ISO validator.
    def _as_date(value, what):
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        return _f2210_date(value, what)
    dd = _as_date(due_date, "due_date")
    pd = _as_date(pay_date, "pay_date")
    if pd < dd:
        raise ValueError(
            f"pay_date {pd.isoformat()} is before due_date {dd.isoformat()}")

    base = {
        "unpaid_tax": round(ut, 2),
        "due_date": dd.isoformat(),
        "pay_date": pd.isoformat(),
    }

    if pd == dd or ut == 0:
        return {**base, "computed": True, "complete": True,
                "interest": 0.0, "days": 0, "covered_through": pd.isoformat(),
                "note": "Paid on/before the due date (or $0 balance) — no interest."}

    periods = _f2210_rate_periods(underpayment_rates)
    if not periods:
        return {**base, "computed": False, "complete": False, "interest": None,
                "covered_through": None,
                "note": ("federal.underpayment_rates is unfetched — §6601 "
                         "interest cannot be computed without the §6621 quarterly "
                         "rates (a guessed rate is never substituted). Fetch the "
                         "rate periods spanning the due date through the pay date.")}

    # A §6621 rate is authoritative ONLY for its own quarter, so coverage of the
    # interest window is a chain of CONTIGUOUS quarter-start rates present in the
    # cache — a missing quarter (even one bracketed by later rates) breaks it.
    starts = {p[0] for p in periods}

    # Opening gap: the rate for the quarter CONTAINING the due date must be
    # present. If it isn't (the earliest cached rate is a later quarter, or an
    # earlier one whose quarter doesn't reach the due date), computing from the
    # first cached rate would misattribute or drop the opening interest — refuse.
    q_dd = _quarter_start_on_or_before(dd)
    if q_dd not in starts:
        return {**base, "computed": False, "complete": False, "interest": None,
                "covered_through": None,
                "note": (f"the §6621 rate for the due-date quarter "
                         f"({q_dd.isoformat()}) is not in underpayment_rates — "
                         f"the opening interest period's rate is unavailable. "
                         f"Fetch the quarter containing the due date; computing "
                         f"from a later or non-adjacent cached rate would "
                         f"misstate the interest.")}

    # Closing horizon: walk forward from the due-date quarter through CONTIGUOUS
    # cached quarters. The first missing quarter (a gap like Apr+Oct with no Jul,
    # or simply the end of the fetched span) stops coverage there — the earlier
    # rate is NEVER extrapolated across the gap.
    covered_through = _next_quarter_start(q_dd)
    while covered_through < pd and covered_through in starts:
        covered_through = _next_quarter_start(covered_through)
    effective_end = min(pd, covered_through)
    interest = _compound_daily(ut, dd, effective_end, periods)
    days = (effective_end - dd).days

    if covered_through < pd:
        return {**base, "computed": True, "complete": False,
                "interest": round(interest, 2), "days": days,
                "covered_through": covered_through.isoformat(),
                "note": (f"PARTIAL: rates cover through {covered_through.isoformat()} "
                         f"(the last cached §6621 quarter), but the balance is paid "
                         f"{pd.isoformat()}. The interest shown accrues ONLY through "
                         f"{covered_through.isoformat()}; MORE accrues after it. "
                         f"Fetch the §6621 rate(s) for the quarter(s) "
                         f"{covered_through.isoformat()}..{pd.isoformat()} — the "
                         f"last cached rate is NOT extrapolated across an unfetched "
                         f"quarter. Interest on the tax only; penalty interest and "
                         f"§6404 suspensions not modeled.")}

    return {**base, "computed": True, "complete": True,
            "interest": round(interest, 2), "days": days,
            "covered_through": pd.isoformat(),
            "note": ("§6601 interest compounded daily (§6622) from the unextended "
                     "due date to the pay date. Interest on the tax only — "
                     "§6601(e)(2) interest on penalties and §6404 suspensions are "
                     "not modeled. Estimator; verify against the IRS notice.")}
