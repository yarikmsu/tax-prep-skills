"""AA76 regular-method FTB5805, hand-worked from the TY2025 worksheets."""

import copy
import datetime as dt

import pytest

from agents.lib.tax_computations import compute_ca_5805
from agents.lib.tax_computations.state.ca import _ca_5805_price, validate_ca_underpayment_rates


def law():
    # FTB5805 PartII/WorksheetII structure; numeric policy values below are
    # deliberately synthetic. Source: FTB's 2025-5805-instructions.html.
    statuses = ('single', 'mfj', 'mfs', 'hoh', 'qss')
    return {'code': 'CA', 'underpayment_screen': {
        'minimum_balance': dict.fromkeys(statuses, 50), 'tax_fraction': .1},
        'estimated_tax_safe_harbor': {
            'current_year_share': .9,
            'prior_year_share': 1, 'prior_year_share_high_income': 1.1,
            'prior_agi_threshold': dict.fromkeys(statuses, 10000),
            'current_agi_override': dict.fromkeys(statuses, 100000),
            'installment_shares': [.3, .4, 0, .3],
            'due_dates': ['2025-04-15', '2025-06-16', '2025-09-15', '2026-01-15'],
            'penalty_end': '2026-04-15', 'early_filing_deadline': '2026-02-02'},
        'underpayment_rates': [
            {'effective_from': '2025-01-01', 'annual_rate': .08},
            {'effective_from': '2025-07-01', 'annual_rate': .07},
            {'effective_from': '2026-01-01', 'annual_rate': .07}]}


def inputs(**changes):
    args = dict(tax_year=2025, filing_status='Single', state_law=law(),
        current_tax=1000, current_agi=50000, withholding=0,
        prior_tax=1000, prior_agi=5000, prior_withholding=0, prior_full_year=True,
        payments=[], opening_credit=0, withholding_method='deemed', actual_withholding=[],
        regular_method_confirmed=True, prior_return_filed=True)
    args.update(changes)
    return args


def payment(identifier, date, amount):
    return {'payment_id': identifier, 'date': date, 'amount': amount}


def test_weighted_requirements_half_year_rates_and_payment_day_endpoints():
    out = compute_ca_5805(**inputs())
    assert out['required_annual_payment'] == 900
    assert out['required_installments'] == [270, 360, 0, 270]
    # Q1:270*(.08*76+.07*289)/365; Q2:360*(.08*14+.07*289)/365;
    # Q4:270*.07*90/365. June16 is the rolled TY2025 second due date.
    expected = 270*(.08*76+.07*289)/365 + 360*(.08*14+.07*289)/365 + 270*.07*90/365
    assert out['total'] == round(expected, 2)
    q1 = [r for r in out['penalty_details'] if r['installment'] == 1]
    assert sum(r['days'] for r in q1 if r['annual_rate'] == .08) == 76
    assert sum(r['days'] for r in q1 if r['annual_rate'] == .07) == 289


def test_deemed_withholding_uses_same_weights_and_never_equal_quarters():
    # 900 withholding exactly covers the current-year900 requirement at each
    # weighted due date. Its600+? comparison never uses federal quarters.
    out = compute_ca_5805(**inputs(withholding=900, prior_tax=None,
        prior_agi=None, prior_withholding=None, prior_full_year=None))
    assert out['total'] == 0 and out['basis'] == 'current_year_requirement_sufficient'
    assert out['required_annual_payment'] is None  # lower prior prong was unnecessary
    assert out['required_installments'] is None
    assert out['tested_current_year_installments'] == [270, 360, 0, 270]


def test_early_payments_apply_to_the_oldest_unpaid_installment_first():
    # Q1 debt270 curedJun10 after56days. Of the630payment,360 remains
    # to coverQ2 onJun16. Q4's270 is timely, so onlyQ1 incurs penalty.
    out = compute_ca_5805(**inputs(payments=[payment('second', '2025-06-10', 630),
        payment('fourth', '2026-01-15', 270)]))
    assert out['total'] == round(270 * .08 * 56 / 365, 2)
    assert all(r['installment'] == 1 for r in out['penalty_details'])


def test_rate_change_payment_day_uses_new_rate_before_reducing_balance():
    # Pay270 onJuly1, Q2 debt360 is independent; fully pay900Jan15.
    out = compute_ca_5805(**inputs(payments=[payment('july', '2025-07-01', 270),
        payment('january', '2026-01-15', 630)]))
    q1 = [r for r in out['penalty_details'] if r['installment'] == 1]
    assert sum(r['days'] for r in q1 if r['annual_rate'] == .08) == 76
    assert sum(r['days'] for r in q1 if r['annual_rate'] == .07) == 1


def test_actual_withholding_can_be_late_and_must_reconcile():
    actual = [payment('withheld', '2025-12-31', 900)]
    out = compute_ca_5805(**inputs(withholding=900, withholding_method='actual', actual_withholding=actual))
    assert out['total'] > 0
    with pytest.raises(ValueError, match='reconcile'):
        compute_ca_5805(**inputs(withholding=901, withholding_method='actual', actual_withholding=actual))


@pytest.mark.parametrize('prior_agi,expected', [(10000, 700), (10000.01, 770)])
def test_prior_year_step_up_is_strictly_above_the_prior_ca_agi_threshold(prior_agi, expected):
    out = compute_ca_5805(**inputs(prior_tax=700, prior_agi=prior_agi))
    assert out['required_annual_payment'] == expected


def test_current_agi_override_includes_equality_but_prior_floor_still_wins():
    assert compute_ca_5805(**inputs(current_agi=100000, prior_tax=700))['required_annual_payment'] == 900
    out = compute_ca_5805(**inputs(current_agi=100000, prior_tax=49))
    assert out['total'] == 0 and out['basis'] == 'prior_year_floor'


def test_current_floor_and_zero_coverage_do_not_need_unused_prior_figures_or_rates():
    data = law(); data['underpayment_rates'] = []
    out = compute_ca_5805(**inputs(current_tax=49, state_law=data,
        prior_tax=None, prior_agi=None, prior_withholding=None, prior_full_year=None))
    assert out['basis'] == 'current_year_floor' and out['total'] == 0
    with pytest.raises(ValueError, match='full-year'):
        compute_ca_5805(**inputs(prior_full_year=None))


def test_missing_successor_rate_is_not_carried_forward():
    data = law(); data['underpayment_rates'].pop()
    with pytest.raises(ValueError, match='coverage'):
        compute_ca_5805(**inputs(state_law=data))


def test_early_return_exception_removes_only_fourth_installment_penalty():
    # Fulltax1000paidJan30: earlier debts accrue untilJan30; Q4's270 does not.
    paid = [payment('return', '2026-01-30', 1000)]
    normal = compute_ca_5805(**inputs(payments=paid))
    early = compute_ca_5805(**inputs(payments=paid, early_return={
        'filed_date': '2026-01-30', 'fully_paid_date': '2026-01-30', 'verified': True}))
    assert normal['total'] > early['total'] > 0
    assert all(r['installment'] != 4 for r in early['penalty_details'])
    with pytest.raises(ValueError, match='payment records'):
        compute_ca_5805(**inputs(early_return={
            'filed_date': '2026-01-30', 'fully_paid_date': '2026-01-30', 'verified': True}))


def test_opening_credit_is_available_in_first_period_once():
    out = compute_ca_5805(**inputs(opening_credit=900))
    assert out['total'] == 0

    # Opening200 pays only Q1:70 remains there, then360 and270. Extra
    # applications in later periods would erase debt that is actually unpaid.
    assert compute_ca_5805(**inputs(opening_credit=200))['total'] == 30.76


def test_simple_daily_interest_uses_a_leap_year_denominator():
    # Generic interval primitive: three inclusive days, Feb28-Feb29-Mar1.
    rates = validate_ca_underpayment_rates([{'effective_from': '2024-01-01', 'annual_rate': .1}])
    detail, total = _ca_5805_price([{'installment': 1, 'principal': 3660,
        'start': dt.date(2024, 2, 28), 'end': dt.date(2024, 3, 1)}], rates, False)
    assert total == 3 and detail[0]['year_days'] == 366


@pytest.mark.parametrize('bad', [True, None, -1, float('nan'), float('inf'), 10**400])
def test_malformed_amounts_do_not_become_a_penalty(bad):
    with pytest.raises(ValueError, match='finite'):
        compute_ca_5805(**inputs(current_tax=bad))


@pytest.mark.parametrize('change', [
    {'tax_year': 2026}, {'filing_status': 'unknown'}, {'regular_method_confirmed': False},
    {'withholding_method': 'unknown'}, {'payments': [payment('unknown', None, 100)]},
    {'actual_withholding': [payment('duplicate', '2025-01-01', 100)]},
])
def test_unreviewed_or_ambiguous_cases_refuse(change):
    with pytest.raises(ValueError):
        compute_ca_5805(**inputs(**change))


@pytest.mark.parametrize('change', [
    {'installment_shares': [.25, .25, .25, float('nan')]},
    {'installment_shares': [.3, .3, 0, .3]}, {'due_dates': []},
    {'penalty_end': '2025-04-15'}, {'early_filing_deadline': None},
    # A cache that holds only line 113's 10% screen has no Part II line 2 share.
    {'current_year_share': None},
])
def test_unfetched_or_wrong_year_policy_refuses(change):
    data = copy.deepcopy(law()); data['estimated_tax_safe_harbor'].update(change)
    with pytest.raises(ValueError):
        compute_ca_5805(**inputs(state_law=data))


@pytest.mark.parametrize('previous', [False, True])
def test_exact_floor_is_not_the_below_floor_exemption(previous):
    # Synthetic floor50 and current share1 isolate strictness; .9 with950
    # withholding would independently prove zero and miss this boundary.
    state = law(); state['estimated_tax_safe_harbor']['current_year_share'] = 1
    change = {'prior_withholding': 950} if previous else {'withholding': 950}
    assert compute_ca_5805(**inputs(state_law=state, **change))['total'] > 0


def test_small_installment_amounts_never_create_a_negative_remainder():
    from agents.lib.tax_computations.state.ca import _ca_5805_allocate
    assert _ca_5805_allocate(.02, [.25] * 4) == [.01, .01, 0, 0]


def test_payment_identifiers_are_unique_at_the_public_engine_boundary():
    with pytest.raises(ValueError, match='unique'):
        compute_ca_5805(**inputs(payments=[payment('same', '2025-04-15', 100),
            payment('same', '2025-06-16', 100)]))


def test_unanswered_prior_filing_status_cannot_select_the_cheaper_prong():
    with pytest.raises(ValueError, match='filed'):
        compute_ca_5805(**inputs(prior_return_filed=None, prior_tax=700))
