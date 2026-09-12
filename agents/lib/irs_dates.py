#!/usr/bin/env python3
"""IRC §7503 next-business-day rollover for federal tax deadlines.

When a deadline falls on a Saturday, Sunday, or a legal holiday, the act is
timely if performed on the next day that is none of those. "Legal holiday"
means a legal holiday in the District of Columbia (§7503), which is why DC
Emancipation Day (April 16, observed) moves the April 15 deadline nationwide
(TY2021 filing day was Apr 18 2022; TY2022's was Apr 18 2023).

Consumers: compute_form_2210 rolls the four §6654 installment due dates with
this, projection rolls both voucher schedules, tax_calendar.py rolls every
federal and CA/NY/NJ deadline it emits, and ptet.py rolls its election and
payment dates. An Epic-L-era caveat stood here claiming the two named skills
emitted unrolled statutory dates; the same epic wired them through this module,
which is what made it false. It is not to be restored, in any wording. Two deliberate exceptions remain, and each
says why at its own site: an RMD is a distribution deadline rather than a §7503
file/pay act, and FL COUNTY property dates are kept on the statutory date
because no Florida rollover rule has been verified and rolling on unverified
authority extends a deadline in the harmful direction.

STATE deadlines take `next_business_day_state`, which needs to be told WHICH
state: the holiday set is the state's own, not a single "not federal" rule.
California ADDS the federal set to its own by statute (R&TC §18410 — a legal
holiday "includes" a federal one), and the federal half is the half modeled
here; `_STATES_CONFORMING_TO_FEDERAL_HOLIDAYS` carries why that half is
sufficient for every date this toolkit dates. A state whose law has not been
read here rolls past weekends only, as a labelled degrade rather than a claim
about its law.

Holiday set: the federal legal holidays observed in DC plus DC Emancipation
Day. Inauguration Day (Jan 20 every 4th year) is deliberately omitted — no
§6654/§6072 deadline can roll onto it (the longest roll from Jan 15 ends
Jan 18). Saturday holidays are observed the preceding Friday, Sunday holidays
the following Monday (5 U.S.C. §6103(b)); an observed-Friday holiday matters
because a deadline landing ON that Friday rolls forward (Apr 15 2022 was
Emancipation Day observed → tax day Apr 18 2022).
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
# The state-code DOMAIN, imported rather than re-listed. `capabilities` owns the
# 56-entry USPS set and `tax_calendar` already reads it from there; a third copy
# would be a third thing to drift, and this module's whole reason for existing
# is that one symbol standing for two jurisdictions hid a wrong date. The import
# direction (dates → the capability gate) is the odd-looking half of that trade
# and is the cheaper one.
from agents.lib.capabilities import _USPS_STATES  # noqa: E402

_ONE_DAY = datetime.timedelta(days=1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
    """n-th <weekday> (Mon=0) of a month, e.g. MLK = 3rd Monday of January."""
    first = datetime.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + datetime.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> datetime.date:
    nxt = (datetime.date(year + 1, 1, 1) if month == 12
           else datetime.date(year, month + 1, 1))
    last = nxt - _ONE_DAY
    return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: datetime.date) -> datetime.date:
    """Federal observance shift: Saturday → preceding Friday, Sunday →
    following Monday."""
    if d.weekday() == 5:
        return d - _ONE_DAY
    if d.weekday() == 6:
        return d + _ONE_DAY
    return d


def dc_legal_holidays(year: int) -> "set[datetime.date]":
    """Observed DC legal holidays falling within the given calendar year."""
    if not isinstance(year, int) or isinstance(year, bool) or not (1990 <= year <= 2100):
        raise ValueError(f"year must be an int in 1990-2100, got {year!r}")
    hol = set()
    fixed = [(1, 1), (7, 4), (11, 11), (12, 25)]  # New Year, July 4, Veterans, Christmas
    if year >= 2021:
        fixed.append((6, 19))  # Juneteenth (federal since 2021)
    if year >= 2005:
        fixed.append((4, 16))  # DC Emancipation Day (DC legal holiday since 2005)
    for month, day in fixed:
        hol.add(_observed(datetime.date(year, month, day)))
    # Next year's New Year observed on Dec 31 when Jan 1 is a Saturday
    if datetime.date(year + 1, 1, 1).weekday() == 5:
        hol.add(datetime.date(year, 12, 31))
    hol.add(_nth_weekday(year, 1, 0, 3))    # MLK Day — 3rd Monday of January
    hol.add(_nth_weekday(year, 2, 0, 3))    # Washington's Birthday — 3rd Monday of February
    hol.add(_last_weekday(year, 5, 0))      # Memorial Day — last Monday of May
    hol.add(_nth_weekday(year, 9, 0, 1))    # Labor Day — 1st Monday of September
    hol.add(_nth_weekday(year, 10, 0, 2))   # Columbus Day — 2nd Monday of October
    hol.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving — 4th Thursday of November
    return hol


def next_business_day(d: datetime.date) -> datetime.date:
    """§7503 rollover: the given date if it is a business day, else the next
    day that is neither a weekend nor a DC legal holiday."""
    if not isinstance(d, datetime.date) or isinstance(d, datetime.datetime):
        raise ValueError(f"expected a datetime.date, got {d!r}")
    while d.weekday() >= 5 or d in dc_legal_holidays(d.year):
        d += _ONE_DAY
    return d


# States whose deadline-rollover statute has actually been READ, and which
# adopt the FEDERAL legal-holiday set.
#
# California is here on R&TC §18410: "a legal holiday includes a federal legal
# holiday recognized by the Internal Revenue Service under Section 7503 of the
# Internal Revenue Code." Read the verb. That is a conformity rule that ADDS
# the federal set to California's own, not one that replaces it: CA keeps the
# Gov Code §6700 holidays the federal calendar has never had — Lincoln's
# Birthday, César Chávez Day, Native American Day, the day after Thanksgiving
# — so CA's set is a SUPERSET of `next_business_day`'s, and membership here
# means the federal half is modeled, not that the two sets are equal.
# FTB says so itself in the Form 540-ES instructions this module dates: "Due to
# the federal Emancipation Day holiday observed on April 15, 2022, tax returns
# filed and payments mailed or submitted on April 18, 2022, will be considered
# timely," and the same sentence for April 17 2023 → April 18 2023. FTB's
# published 540-ES quarters bear it out end to end: TY2023 ran April 18 2023,
# June 15 2023, September 15 2023 and January 16 2024 — that last one being
# MLK Day, which no weekend-only rule can reach.
#
# The unmodeled half is SUFFICIENTLY unmodeled, and that is asserted rather
# than assumed. No (month, day) any caller hands this function for CA can land
# on a CA-only §6700 holiday, or traverse one on the way to its observed date:
# the four §7503 rolls are short, and the nearest CA-only holiday to each of
# the dates the callers pass is weeks away.
# `test_irs_dates.py` re-derives that over 2000-2060 from the call sites
# themselves — `test_no_california_deadline_can_reach_a_state_only_holiday` —
# so a date that CAN collide fails the suite rather than publishing early.
#
# Not closed by unioning §6700 in anyway, and deliberately. Every CA date is
# right today; adding the §6700 half would move dates on nothing but unverified
# authority, and rolling on unverified authority extends a deadline in the
# harmful direction — the reason the FL county dates above keep the statutory
# date. When a colliding date arrives, FTB's own published answer FOR IT is
# what settles the roll, the way the 540-ES quarters settled this one.
#
# Membership is EVIDENCE, not a guess: add a state only with its own statute
# or its own agency's published dates in hand, the way CA is above.
_STATES_CONFORMING_TO_FEDERAL_HOLIDAYS = frozenset({"CA"})


def state_conforms_to_federal_holidays(state: str) -> bool:
    """Does `state` adopt the federal legal-holiday set for tax deadlines?

    Exported so a caller that must SAY something about the rule reads the same
    set the rule is applied from. tax_calendar's observed-date caveat is the
    live case: it tells the reader to go and confirm the date, which is live
    advice for a state outside the set and noise for one inside it, whose
    dates match the authority's own published ones. Deriving that sentence
    from a second hand-kept list is how the caveat and the behaviour would
    come to disagree.

    The name is about the FEDERAL half only, and says so: a member adopts the
    federal set, it does not thereby have no holidays of its own. CA's §6700
    holidays are outside what this module models — unreachable by any date it
    dates, which is the assertion above, not a property of the statute.
    """
    return isinstance(state, str) and \
        state.strip().upper() in _STATES_CONFORMING_TO_FEDERAL_HOLIDAYS


def next_business_day_state(d: datetime.date, state: str) -> datetime.date:
    """A STATE deadline as observed, under that state's own holiday rule.

    `state` is REQUIRED, and that is the whole point of this signature. The
    rule this function used to implement — weekends only, for every state at
    once — rested on the premise that a state tax authority ignores the DC
    holiday set. For California, the one state this toolkit models in depth,
    that is false: R&TC §18410 folds the federal set into CA's own (see
    above, including which half of CA's is modeled here). The old
    rule therefore published CA payment dates EARLY — TY2023 Q1 as April 17
    when FTB's own instructions say April 18, and TY2023 Q4 as January 15 when
    FTB says January 16 — and, once the passed-deadline gate started reading
    the same rule, reported a 540-ES row as already missed on the very day it
    was due. One symbol standing for two jurisdictions is what hid that: a
    caller could not say which state it meant, so it never had to be right
    about either.

    A code that is not a US state at all is REFUSED, not degraded. The
    weekend-only path is a statement about a jurisdiction whose law has not been
    read — it is not a place to put "California", "CA1" or "ZZ", none of which
    name a jurisdiction at all. Silently taking it published TY2023 April 15 as
    April 17 for a caller that meant California, whose own FTB instructions say
    April 18, and the passed-deadline gate downstream reads these dates: an
    early one reports a 540-ES row missed on a day it is still open, which is
    the over-stating direction this repo treats as a hard error. Every live
    caller passes a literal "CA" or an already-validated code today, so this is
    the boundary holding rather than a bug being fixed — which is the point of
    putting it at the boundary.

    A state NOT in the conforming set keeps the weekend-only roll, and that is
    a DEGRADE rather than a rule: nobody has read NY's or NJ's statute here.
    It is the safe direction for the thing these dates are — a payment date
    published EARLIER than the statute allows can never make a payment late,
    while one published later can. It is the wrong direction for a
    passed-deadline warning, which is why the conforming set exists at all and
    why a state belongs in it as soon as its law is actually checked.

    Federal deadlines take `next_business_day` above.
    """
    if not isinstance(d, datetime.date) or isinstance(d, datetime.datetime):
        raise ValueError(f"expected a datetime.date, got {d!r}")
    if not isinstance(state, str) or not state.strip():
        raise ValueError(
            "next_business_day_state requires a state code — the rollover rule "
            f"differs by jurisdiction, got {state!r}")
    code = state.strip().upper()
    if code not in _USPS_STATES:
        raise ValueError(
            f"next_business_day_state got {state!r}, which is not a 2-letter "
            "USPS state/territory code — the weekend-only path is the rule for "
            "a state whose statute has not been read here, not a fallback for "
            "an unrecognised one, and taking it would publish a date EARLY for "
            "a caller that meant a conforming state")
    if code in _STATES_CONFORMING_TO_FEDERAL_HOLIDAYS:
        return next_business_day(d)
    while d.weekday() >= 5:
        d += _ONE_DAY
    return d


if __name__ == "__main__":
    # Self-test against known federal tax days (all verified vs IRS notices):
    cases = [
        (datetime.date(2022, 4, 15), datetime.date(2022, 4, 18)),  # Emancipation observed Fri
        (datetime.date(2023, 4, 15), datetime.date(2023, 4, 18)),  # Sat → Mon 17 = Emancipation obs → Tue
        (datetime.date(2024, 4, 15), datetime.date(2024, 4, 15)),  # plain Monday
        (datetime.date(2025, 4, 15), datetime.date(2025, 4, 15)),
        (datetime.date(2025, 6, 15), datetime.date(2025, 6, 16)),  # Sunday → Monday
        (datetime.date(2024, 1, 15), datetime.date(2024, 1, 16)),  # Jan 15 2024 IS MLK Monday
        (datetime.date(2022, 1, 15), datetime.date(2022, 1, 18)),  # Sat → Sun → MLK Mon → Tue
    ]
    for given, expected in cases:
        got = next_business_day(given)
        assert got == expected, f"{given} → {got}, expected {expected}"
    # CALIFORNIA takes the federal set (R&TC §18410). Every case below is a
    # date FTB itself publishes in the Form 540-ES instructions, which is the
    # only evidence that settles it — the first two roll past DC Emancipation
    # Day and the third past MLK Day, none of which a weekend-only rule
    # reaches. All three were previously asserted the other way round here.
    ca_cases = [
        (datetime.date(2023, 4, 15), datetime.date(2023, 4, 18)),  # Sat → Mon=Emancipation obs → Tue
        (datetime.date(2022, 4, 15), datetime.date(2022, 4, 18)),  # Fri IS Emancipation obs → Mon
        (datetime.date(2024, 1, 15), datetime.date(2024, 1, 16)),  # MLK Monday → Tue (FTB: Jan 16)
        (datetime.date(2025, 6, 15), datetime.date(2025, 6, 16)),  # Sunday → Monday
    ]
    for given, expected in ca_cases:
        got = next_business_day_state(given, "CA")
        assert got == expected, f"CA {given} → {got}, expected {expected}"
        assert got == next_business_day(given), (
            f"CA conforms to the federal set, so {given} must match the "
            f"federal roll {next_business_day(given)}, got {got}")
    # A state whose statute has NOT been read keeps the weekend-only degrade.
    # This is the pair that proves the two paths are still distinct — delete it
    # and a conforming-set regression looks identical to correct behaviour.
    unread_cases = [
        (datetime.date(2023, 4, 15), datetime.date(2023, 4, 17)),  # Sat → Mon, holiday unmodeled
        (datetime.date(2024, 1, 15), datetime.date(2024, 1, 15)),  # MLK Monday: not rolled
        (datetime.date(2025, 6, 15), datetime.date(2025, 6, 16)),  # Sunday → Monday
    ]
    for given, expected in unread_cases:
        got = next_business_day_state(given, "NY")
        assert got == expected, f"NY {given} → {got}, expected {expected}"
    assert next_business_day_state(datetime.date(2024, 1, 15), "CA") != \
        next_business_day_state(datetime.date(2024, 1, 15), "NY"), \
        "a conforming state and an unread one must not resolve identically"
    # Shape-valid but not a jurisdiction, alongside the shape-invalid set: the
    # first three are what a caller reaches for when it has a state NAME, a typo
    # or a corrupt field, and each used to take the weekend-only path silently.
    for bad in (None, "", "   ", 5, "California", "CA1", "ZZ", "BC", "C"):
        try:
            next_business_day_state(datetime.date(2025, 6, 15), bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"state {bad!r} must be refused, not guessed")
    print("irs_dates self-test OK")
