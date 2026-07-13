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
from agents.lib.irs_dates import dc_legal_holidays, next_business_day  # noqa: E402


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
