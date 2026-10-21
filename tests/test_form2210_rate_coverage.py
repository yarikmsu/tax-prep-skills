"""AA58: actual accruing quarters require their own fetched rate rows."""
import pytest

from agents.lib.tax_computations import compute_form_2210, compute_underpayment_interest


def rates():
    # Deliberately synthetic: a falling rate exposes an overstated penalty
    # if the previous quarter is carried forward. No production rate is inline.
    return [{'effective_from': date, 'annual_rate': rate} for date, rate in [
        ('2025-04-01', .08), ('2025-07-01', .07), ('2025-10-01', .06),
        ('2026-01-01', .05), ('2026-04-01', .04)]]


def compute(schedule, payments=None, withholding=8000):
    return compute_form_2210(20000, withholding, 'Single', 2025,
                             prior_year_tax=10000, prior_year_agi=100000,
                             estimated_payments=payments, underpayment_rates=schedule)


def test_january_tail_never_prices_the_unpublished_april_quarter(monkeypatch):
    from agents.lib.tax_computations.federal import penalties
    def forbidden(*args, **kwargs):
        pytest.fail('A missing quarter must be detected before any priced total')
    monkeypatch.setattr(penalties, '_f2210_accrue', forbidden)
    result = compute(rates()[:-1])
    assert result['penalty'] is None and result['penalty_computed'] is False
    assert result['no_penalty'] is False
    assert [row['underpayment'] for row in result['quarters']] == [500, 1000, 1500, 2000]
    assert any('2026-04-01' in note and 'IRS bill' in note for note in result['notes'])


@pytest.mark.parametrize('missing', [1, 2, 3, 4])
def test_a_later_rate_does_not_bridge_a_missing_accruing_quarter(missing):
    schedule = rates()
    gap = schedule.pop(missing)['effective_from']
    result = compute(schedule)
    assert result['penalty'] is None and result['penalty_computed'] is False
    assert any(gap in note and 'missing' in note for note in result['notes'])


def test_complete_falling_rate_schedule_prices_only_known_rates():
    result = compute(rates())
    # Q1 balance500 accrues Apr15–Jun16=62d; balance1000 Jun16–Sep15=91d;
    # 1500 Sep15–Jan15=122d; 2000 Jan15–Apr15=90d, split at calendar quarters.
    # 500*(.08*62)/365 +1000*(.08*15+.07*76)/365
    # +1500*(.07*16+.06*92+.05*14)/365 +2000*(.05*76+.04*14)/365.
    expected = (500*.08*62 + 1000*(.08*15+.07*76)
                + 1500*(.07*16+.06*92+.05*14) + 2000*(.05*76+.04*14))/365
    assert result['penalty_computed'] is True
    assert result['penalty'] == round(expected, 2)


@pytest.mark.parametrize('pay_date', ['2025-06-01', '2025-07-01'])
def test_payment_before_or_at_next_quarter_needs_no_later_rate(pay_date):
    # The2000estimate covers each installment's500shortfall. Q1accrues until
    # payment, Q2also accrues if the payment arrives after its June16 due date,
    # and subsequent installments are prepaid.
    result = compute(rates()[:1], [{'date': pay_date, 'amount': 2000}])
    from datetime import date
    days = (date.fromisoformat(pay_date)-date(2025, 4, 15)).days
    # A July1payment arrives after June16's due date, so Q2's500also accrues
    # for15days; July1itself is outside the existing [start,end) worksheet span.
    expected = (500*.08*days + (500*.08*15 if pay_date == '2025-07-01' else 0))/365
    assert result['penalty_computed'] is True and result['penalty'] == round(expected, 2)


def test_payment_after_quarter_boundary_needs_the_new_quarter():
    result = compute(rates()[:1], [{'date': '2025-07-02', 'amount': 2000}])
    assert result['penalty_computed'] is False and result['penalty'] is None
    assert any('2025-07-01' in note for note in result['notes'])


def test_missing_quarters_with_no_debt_do_not_block_later_known_accrual():
    # June1estimate1500 retires Q1's500 and prepays Q2/Q3. Only Q4's500
    # then accrues Jan15–Apr15. July/October2025 have no unpaid balance.
    schedule = [rates()[0], *rates()[3:]]
    result = compute(schedule, [{'date': '2025-06-01', 'amount': 1500}])
    expected = (500*.08*47 + 500*(.05*76+.04*14))/365
    assert result['penalty_computed'] is True
    assert result['penalty'] == round(expected, 2)


def test_zero_underpayment_proof_needs_no_rates():
    result = compute(None, [{'date': '2025-04-15', 'amount': 2000}])
    assert result['penalty_computed'] is True and result['penalty'] == 0


@pytest.mark.parametrize('date', ['2025-04-02', '2025-05-01', '2025-07-15'])
def test_nonquarter_effective_dates_are_rejected(date):
    schedule = [*rates(), {'effective_from': date, 'annual_rate': .03}]
    with pytest.raises(ValueError, match='quarter start'):
        compute(schedule)
    # Both federal penalty/interest users share the same parse boundary.
    with pytest.raises(ValueError, match='quarter start'):
        compute_underpayment_interest(100, '2025-04-15', '2025-06-01',
                                      underpayment_rates=schedule)
