#!/usr/bin/env python3
"""IRC §7503 next-business-day rollover for federal tax deadlines.

When a deadline falls on a Saturday, Sunday, or a legal holiday, the act is
timely if performed on the next day that is none of those. "Legal holiday"
means a legal holiday in the District of Columbia (§7503), which is why DC
Emancipation Day (April 16, observed) moves the April 15 deadline nationwide
(TY2021 filing day was Apr 18 2022; TY2022's was Apr 18 2023).

Consumers: compute_form_2210 rolls the four §6654 installment due dates with
this. /tax-calendar and /tax-ptet still emit STATUTORY dates (their documented
v1 caveat) — wiring them through this helper is the roadmap's v2 follow-up so
both roll identically when they do.

Holiday set: the federal legal holidays observed in DC plus DC Emancipation
Day. Inauguration Day (Jan 20 every 4th year) is deliberately omitted — no
§6654/§6072 deadline can roll onto it (the longest roll from Jan 15 ends
Jan 18). Saturday holidays are observed the preceding Friday, Sunday holidays
the following Monday (5 U.S.C. §6103(b)); an observed-Friday holiday matters
because a deadline landing ON that Friday rolls forward (Apr 15 2022 was
Emancipation Day observed → tax day Apr 18 2022).
"""

import datetime

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
    print("irs_dates self-test OK")
