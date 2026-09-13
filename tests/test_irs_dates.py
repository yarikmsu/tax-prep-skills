#!/usr/bin/env python3
"""Tests for agents/lib/irs_dates.py — §7503 next-business-day rollover.

Expected dates verified against IRS filing-season announcements and the
printed Form 2210 installment columns for each year.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.lib.irs_dates import (  # noqa: E402
    dc_legal_holidays,
    next_business_day,
    next_business_day_state,
    state_conforms_to_federal_holidays,
)
from tests.engine_tree import engine_files, engine_relpath  # noqa: E402


D = datetime.date


class TestNextBusinessDay:
    def test_plain_weekday_unchanged(self):
        assert next_business_day(D(2025, 4, 15)) == D(2025, 4, 15)  # Tuesday

    def test_sunday_rolls_to_monday(self):
        # TY2025 Q2 installment: Jun 15 2025 is a Sunday
        assert next_business_day(D(2025, 6, 15)) == D(2025, 6, 16)

    def test_emancipation_day_observed_friday(self):
        # Apr 16 2022 is a Saturday → Emancipation observed Fri Apr 15 →
        # tax day Mon Apr 18 2022 (matches the IRS announcement).
        assert next_business_day(D(2022, 4, 15)) == D(2022, 4, 18)

    def test_saturday_then_observed_emancipation_monday(self):
        # Apr 15 2023 Sat → Mon Apr 17 = Emancipation observed (Apr 16 is a
        # Sunday) → Tue Apr 18 2023 (matches the IRS announcement).
        assert next_business_day(D(2023, 4, 15)) == D(2023, 4, 18)

    def test_mlk_monday_jan_15(self):
        # Jan 15 2024 IS the 3rd Monday of January (MLK Day) — the TY2023
        # Q4 installment column on the printed Form 2210 is Jan 16 2024.
        assert next_business_day(D(2024, 1, 15)) == D(2024, 1, 16)

    def test_weekend_plus_mlk_cascade(self):
        # Jan 15 2022 Sat → Sun 16 → MLK Mon 17 → Tue Jan 18 2022.
        assert next_business_day(D(2022, 1, 15)) == D(2022, 1, 18)

    def test_rejects_datetime(self):
        with pytest.raises(ValueError):
            next_business_day(datetime.datetime(2025, 4, 15, 12, 0))


class TestDcLegalHolidays:
    def test_juneteenth_federal_from_2021_only(self):
        assert D(2021, 6, 18) in dc_legal_holidays(2021)  # Jun 19 2021 Sat → Fri
        assert not any(h.month == 6 and h.day in (18, 19, 21)
                       for h in dc_legal_holidays(2020))

    def test_emancipation_day_included(self):
        assert D(2024, 4, 16) in dc_legal_holidays(2024)  # Tuesday, no shift

    def test_new_years_saturday_observed_prior_dec_31(self):
        # Jan 1 2022 is a Saturday → observed Fri Dec 31 2021.
        assert D(2021, 12, 31) in dc_legal_holidays(2021)

    def test_year_validation(self):
        with pytest.raises(ValueError):
            dc_legal_holidays(1980)
        with pytest.raises(ValueError):
            dc_legal_holidays(True)


class TestNoModuleRestatesTheSupersededStateRule:
    """The premise the state rollover was BUILT on, and which turned out false.

    Epic L wired four modules to "a state tax authority does not observe the DC
    holiday set, so state deadlines roll past weekends only". For California —
    the one state this toolkit models in depth — R&TC §18410 says the opposite,
    and the rule published CA payment dates up to three days EARLY until it was
    corrected.

    The correction changed `next_business_day_state` and the docstrings around
    it, and left the premise standing in seven other places: `tax_calendar`'s
    module docstring AND its §7503 section header, `ptet._election_deadline`,
    three comments in `projection`, and this module's own consumer list. Each
    one is the comment a contributor reads BEFORE the code, and each said to do
    the thing the fix undid. A wrong comment about a rule is how the rule comes
    back.

    The ban is ABSOLUTE — no "unless a nearby sentence marks it superseded"
    escape. That escape was tried first and let two of the seven reverts through
    on nothing more than an adjacent §18410 citation, which every corrected site
    carries. Every claim below can be paraphrased where the history is worth
    recording, and each corrected site now does exactly that; what cannot
    survive is the literal wording, which is what a revert, a merge resolution
    or a copy from an older sibling module actually produces.
    """

    # The superseded claims as they were WRITTEN, one per site the correction
    # had to reach.
    PREMISE = (
        "does not observe dc emancipation day",
        "doesn't apply to state authorities",
        "don't apply to state authorities",
        # NOT "rolls past weekends only" — that is the TRUE description of the
        # degrade an unread state keeps, and this module's own docstring says
        # it. What was false was never the mechanism; it was attaching the
        # mechanism to every state, and to California in particular. So the ban
        # is on the JUSTIFICATIONS that did the attaching, each of which is a
        # claim about a state authority rather than about a calendar.
        # ptet._election_deadline said "next-business-day WEEKEND rollover"
        # while the helper it calls resolves CA to the federal set.
        "weekend rollover",
        # This module's own consumer list: both named skills DO route here.
        "still emit statutory dates",
        # projection's idempotence example, whose CA dates were the wrong ones.
        "monday the 17th",
        # ROUND SEVENTEEN, and the OPPOSITE error to the six above: the
        # correction overshot. §18410 says a CA legal holiday "INCLUDES" a
        # federal one, which ADDS the federal set to California's own — it
        # does not replace the Gov Code §6700 holidays the federal calendar
        # has never had. Six comments across three modules read the verb as
        # "IS", and one of them suppressed a user-facing "confirm this date"
        # caveat on the strength of it. What is banned is the EXCLUSIVITY,
        # never the conformity: "CA adopts the federal set" stays sayable,
        # because CA does, and the ban would otherwise demand the deletion of
        # the true half of the rule.
        "the federal set outright",
        "california's is the federal set",
        "california's set is the federal one",
        "gives california the federal set",
        "holidays are modeled",
        "not a list of its own",
    )

    def test_california_is_still_in_the_conforming_set(self):
        """The precondition for the ban. Behaviour, not prose.

        If CA ever left the conforming set the premise would be true of it
        again, and the ban below would be demanding the deletion of a correct
        comment. Asserting the behaviour here is what ties the two together.
        """
        assert state_conforms_to_federal_holidays("CA")
        assert next_business_day_state(D(2023, 4, 15), "CA") == D(2023, 4, 18)
        assert next_business_day_state(D(2024, 1, 15), "CA") == D(2024, 1, 16)
        # And an unread state still degrades to weekends only, which is what
        # keeps the ban a claim about CA rather than about every state.
        assert next_business_day_state(D(2024, 1, 15), "NY") == D(2024, 1, 15)

    def test_a_code_that_is_not_a_state_is_refused_not_degraded(self):
        """The weekend-only path is a rule about a jurisdiction, not a fallback.

        `next_business_day_state` accepted any non-empty string and sent
        everything outside the conforming set down the weekend-only branch. For
        a caller holding a state NAME, a typo or a corrupt field that is a
        silent wrong answer in the EARLY direction: "California" gives TY2023
        April 17 where FTB itself publishes April 18, and the passed-deadline
        gate reads these dates, so a 540-ES row reports as missed on a day it is
        still open. Every live caller passes "CA" or an already-validated code,
        which is exactly why the boundary is the place to say so — nothing
        downstream would notice a new one that did not.
        """
        for bad in ("California", "CA1", "ZZ", "BC", "C", "ca ny"):
            with pytest.raises(ValueError) as exc:
                next_business_day_state(D(2023, 4, 15), bad)
            assert "USPS" in str(exc.value)
        # Case and surrounding space are still accepted — the domain check is
        # on the CODE, and rejecting " ca " would be a new refusal, not this one.
        assert next_business_day_state(D(2023, 4, 15), " ca ") == D(2023, 4, 18)
        # And a real state outside the conforming set still degrades rather
        # than raising, which is the distinction being drawn.
        assert next_business_day_state(D(2023, 4, 15), "NY") == D(2023, 4, 17)

    def test_no_engine_module_restates_it(self):
        modules = engine_files()
        assert len(modules) > 10, "the engine walk found nothing — vacuous"
        for module in modules:
            flat = " ".join(module.read_text().split()).lower()
            for premise in self.PREMISE:
                assert premise not in flat, (
                    f"{engine_relpath(module)} restates the superseded "
                    f"state-rollover "
                    f"rule verbatim ({premise!r}). California adopts the "
                    "FEDERAL holiday set (R&TC §18410) and its deadlines roll "
                    "past DC Emancipation Day and MLK Day; paraphrase the "
                    "history instead of quoting the wording that was wrong")


class TestTheUnmodelledHalfOfCaliforniasHolidaySet:
    """R&TC §18410 says a CA legal holiday "INCLUDES" a federal one.

    Read as "IS", which the corrected comments now do not, that sentence makes
    California's set equal to `next_business_day`'s. It is additive: CA keeps
    the Gov Code §6700 holidays the federal calendar has never had, so its set
    is a SUPERSET and only the federal half is modeled here.

    Unioning §6700 in is NOT the fix, and this test is why. Every CA date this
    toolkit emits is already the one FTB publishes, so the union would move no
    date that is wrong — it would move dates on unverified authority, in the
    direction that turns a timely payment late, which is the same reason the FL
    county dates keep their statutory date. What the unmodeled half needs is
    not machinery but a GUARD: proof that no date reaches it, failing the day
    one can.
    """

    # Gov Code §6700 holidays with no federal counterpart. Over-inclusive on
    # purpose — Admission Day's status for state offices has moved, and a
    # holiday listed here that CA does not observe only makes the disjointness
    # below a stronger claim, never a weaker one.
    @staticmethod
    def _california_only_holidays(year: int) -> "set[datetime.date]":
        from agents.lib.irs_dates import _nth_weekday
        return {
            D(year, 2, 12),                              # Lincoln's Birthday
            D(year, 3, 31),                              # César Chávez Day
            D(year, 9, 9),                               # Admission Day
            _nth_weekday(year, 9, 4, 4),                 # Native American Day
            # The day after Thanksgiving:
            _nth_weekday(year, 11, 3, 4) + datetime.timedelta(days=1),
        }

    YEARS = range(2000, 2061)

    def _risky_month_days(self) -> "set[tuple[int, int]]":
        """Every (month, day) whose §7503 roll can touch a CA-only holiday."""
        risky = set()
        for year in self.YEARS:
            ca_only = self._california_only_holidays(year)
            for holiday in ca_only:
                # Walk BACK from the holiday: any statutory date whose roll
                # would pass through it is one of the few days before it.
                for back in range(0, 6):
                    start = holiday - datetime.timedelta(days=back)
                    if start.year != year:
                        continue
                    cur, stop = start, next_business_day(start)
                    while cur <= stop:
                        if cur == holiday:
                            risky.add((start.month, start.day))
                            break
                        cur += datetime.timedelta(days=1)
        return risky

    def _california_month_days(self) -> "set[tuple[int, int]]":
        """Every (month, day) the live callers actually hand the CA roller."""
        import agents.lib.irs_dates as dates_mod
        import agents.lib.ptet as ptet_mod
        import agents.lib.tax_calendar as calendar_mod

        seen: "set[tuple[int, int]]" = set()
        real = dates_mod.next_business_day_state

        def spy(d, state):
            if state.strip().upper() == "CA":
                seen.add((d.month, d.day))
            return real(d, state)

        patched = (calendar_mod, ptet_mod, dates_mod)
        for mod in patched:
            mod.next_business_day_state = spy
        try:
            for year in (2023, 2024, 2025, 2026):
                calendar_mod.events_ca_es(year)
                calendar_mod.events_extension(year, state="CA")
                calendar_mod.events_ptet(year, state="CA", has_k1_income=True)
                ptet_mod._election_deadline("CA", year)
        finally:
            for mod in patched:
                mod.next_business_day_state = real
        return seen

    def test_the_risky_dates_are_a_real_set(self):
        """Non-vacuity. If nothing could ever collide the disjointness below
        would pass on an empty intersection and prove nothing at all."""
        risky = self._risky_month_days()
        assert len(risky) >= 10, (
            f"only {len(risky)} statutory dates can reach a CA-only holiday; "
            "the holiday set or the roll has changed shape and the guard "
            "below is no longer testing anything")
        assert (3, 31) in risky, (
            "César Chávez Day is not reachable from its own date — the "
            "walk-back is broken, not the calendar")

    def test_no_california_deadline_can_reach_a_state_only_holiday(self):
        """The guard the corrected comments cite, and the whole licence for
        modelling only the federal half of CA's set."""
        used = self._california_month_days()
        assert len(used) >= 3, (
            f"only {len(used)} CA dates reached the roller — the emitters no "
            "longer route through it, so this guard is vacuous")
        collides = sorted(used & self._risky_month_days())
        assert not collides, (
            f"a CA deadline now falls on {collides}, whose §7503 roll can "
            "land on or cross a Gov Code §6700 holiday the federal set does "
            "not hold. Only the federal half of R&TC §18410's set is "
            "modeled, so this date publishes EARLY — and the "
            "passed-deadline gate reads "
            "these dates, reporting a row missed on a day it is still open. "
            "Settle the roll against FTB's own published date for it before "
            "adding the §6700 half; rolling on unverified authority is how a "
            "timely payment is published late")
