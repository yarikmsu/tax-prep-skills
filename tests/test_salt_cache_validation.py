"""AA19: a cached annual cap must agree with §164(b)(7) before pricing."""

import pytest

from agents.lib.ptet import PtetError, _salt_cap
from agents.lib.tax_computations import compute_schedule_a
from agents.lib.tax_computations.federal import salt


def schedule_a(year, status, cap, magi=300000, agi=300000):
    return compute_schedule_a(
        agi, status, year, 16000, cap, modified_agi=magi,
        state_local_income_taxes=30000)


@pytest.mark.parametrize("status,field,bad", [
    ("Single", "amount", 10000),       # pre-OBBBA or already phased down
    ("MFJ", "amount", 40000),         # prior year's amount
    ("HoH", "amount", 20200),         # separate-return amount in the full field
    ("Single", "amount", 60000),       # overstates the allowable deduction
    ("MFS", "mfs_amount", 5000),      # already phased down for MFS
    ("MFS", "mfs_amount", 20000),     # prior year's separate-return amount
    ("MFS", "mfs_amount", 40400),     # full amount would double the MFS cap
])
@pytest.mark.parametrize("magi", [None, 300000, 900000])
def test_consumers_refuse_stale_or_reduced_caps(status, field, bad, magi):
    # §164(b)(7)(A)(ii): TY2026 is 40,400 (20,200 MFS). Refuse BEFORE
    # phasedown, even when high MAGI would otherwise hide the corrupt base.
    with pytest.raises(ValueError, match="statutory SALT cap for TY2026"):
        schedule_a(2026, status, bad, magi)
    with pytest.raises(PtetError, match=rf"federal.salt_cap.{field} disagrees"):
        _salt_cap(2026, status, {field: bad}, magi)


@pytest.mark.parametrize("year,cap", [
    (2024, 10000), (2025, 40000), (2026, 40400), (2027, 40804),
    (2028, 41212.04), (2029, 41624.16), (2030, 10000),
])
@pytest.mark.parametrize("status", ["Single", "MFJ", "HoH", "QSS", "MFS"])
def test_matching_amounts_and_whole_dollar_rounding_stand(year, cap, status):
    # §164(b)(7)(A): named anchors, 101% steps through 2029, then reversion;
    # §164(b)(6)(B) halves for MFS. Round the status's figure, not the full one.
    expected = round(cap / (2 if status == "MFS" else 1), 2)
    field = "mfs_amount" if status == "MFS" else "amount"
    for cached in (expected, round(expected)):
        assert _salt_cap(year, status, {field: cached}, 100000) == cached
        out = schedule_a(year, status, cached, magi=100000, agi=100000)
        assert out["salt_cap_applied"] == cached
        assert out["salt_deductible"] == min(30000, cached)


@pytest.mark.parametrize("status,field,cap", [
    ("Single", "amount", 41212.05),
    ("Single", "amount", 41212.03),
    ("Single", "amount", 41213),
    ("MFS", "mfs_amount", 20606.03),
    ("MFS", "mfs_amount", 20606.01),
    ("MFS", "mfs_amount", 20607),
])
def test_rounding_is_not_an_arbitrary_tolerance(status, field, cap):
    # 2028: 40,400 x 1.01^2 = 41,212.04; MFS = 20,606.02. The only
    # alternatives are those exact amounts or 41,212 / 20,606 whole dollars.
    with pytest.raises(ValueError, match="whole-dollar rounding allowed"):
        schedule_a(2028, status, cap)
    with pytest.raises(PtetError, match="whole-dollar rounding allowed"):
        _salt_cap(2028, status, {field: cap})


def test_half_dollar_rounds_up(monkeypatch):
    # A synthetic anchor tests the tie convention without claiming a new law.
    monkeypatch.setattr(salt, "SALT_CAP_2026", 40400.50)
    assert _salt_cap(2026, "Single", {"amount": 40401}) == 40401
    with pytest.raises(PtetError, match="statutory SALT cap"):
        _salt_cap(2026, "Single", {"amount": 40400})


def test_correct_cap_prices_the_original_regression():
    # 30,000 eligible SALT below the 40,400 TY2026 cap, MAGI below 505,000:
    # the deduction is 30,000, rather than the stale cache's 10,000.
    assert schedule_a(2026, "Single", 40400)["salt_deductible"] == 30000


def test_accepted_subcent_input_is_priced_at_cents_in_both_consumers():
    # 40,400.004 is 40,400 at cents. MAGI excess .02 x 30% = .006;
    # 40,400 - .006 rounds to 40,399.99. Pricing the raw subcent amount instead
    # rounds to 40,400.00, disagreeing with Schedule A after the same validation.
    cap, magi = 40400.004, 505000.02
    assert _salt_cap(2026, "Single", {"amount": cap}, magi) == 40399.99
    assert schedule_a(2026, "Single", cap, magi)["salt_cap_applied"] == 40399.99


def test_missing_cap_preserves_each_consumers_contract():
    assert _salt_cap(2026, "Single", {"amount": None}) == 40400
    assert _salt_cap(2026, "MFS", {"amount": 40400}) == 20200
    with pytest.raises(ValueError, match="salt_cap"):
        schedule_a(2026, "Single", None)


def test_mismatch_refuses_even_when_no_salt_is_claimed():
    # A small tax cannot make a corrupt annual law figure valid.
    with pytest.raises(ValueError, match="statutory SALT cap"):
        compute_schedule_a(300000, "Single", 2026, 16000, 10000)
