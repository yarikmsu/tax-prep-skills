"""AA41: Form 8949 routing reconciled with broker forms, on synthetic events.

Every figure is worked by hand from the Form 8949 instructions: column (h) =
(d) - (e) + (g); a basis-reported form keeps its basis in (e) and corrects it
in (g) (Worksheet for Basis Adjustments: line 1 reported, line 2 correct); a
basis-not-reported form takes the correct basis in (e) with -0- in (g).
"""
import copy
import json
import re
from pathlib import Path

import pytest

from agents.lib import crypto_importer as C
from agents.lib import tie_out
from tests.skill_text import listed_boxes, skill_text

REPO = Path(__file__).resolve().parent.parent


def event(account, txid, kind, date, qty=1, usd=0, **extra):
    return dict(account_id=account, txn_id=txid, kind=kind, date=date,
                asset='BTC', quantity=qty, usd_amount=usd, **extra)


# A: 1 BTC bought for $100 on 2023-01-01, sold for $150 on 2025-03-01 (long, gain $50).
BASE = [event('A', 'buy', 'buy', '2023-01-01', usd=100),
        event('A', 'sale', 'sell', '2025-03-01', usd=150)]


# Reviewed form-specific boxes, each copied blank.
DA_BLANKS = dict(net_proceeds=False, qof=False, customer_acquisition_info=False,
                 optional_method=None, primary_sale_proceeds=None, cusip=None)
B_BLANKS = dict(collectibles=False, qof=False, net_proceeds=False, cusip=None,
                contract_profit_loss=None, prior_unrealized_profit_loss=None,
                current_unrealized_profit_loss=None, aggregate_contract_profit_loss=None,
                bartering=None)


def form(**extra):
    fields = dict(form_id='da-1', form_type='1099-DA', account_id='A', txn_id='sale',
                  asset='BTC', quantity='1', date_sold='2025-03-01', proceeds=150, cost_basis=None,
                  basis_reported_to_irs=False, term=None, ordinary=False, accrued_market_discount=None,
                  wash_sale_loss_disallowed=None, loss_not_allowed=False)
    merged = {**fields, **extra}
    return {**(DA_BLANKS if merged['form_type'] == '1099-DA' else B_BLANKS), **merged}


def reporting(*forms, received=True, resolutions=None, **account):
    return dict(accounts={'A': dict(forms_received=received, evidence='broker-statement-A', **account)},
                forms=list(forms), resolutions=resolutions or {})


def run(reporting_block, tx=BASE, year=2025):
    return C.build_crypto_import(copy.deepcopy(tx), tax_year=year, broker_reporting=reporting_block, specific_identification=False)


def rows(result):
    return result['form_8949']['part_i_short_term'] + result['form_8949']['part_ii_long_term']


def only(result):
    (row,) = rows(result)
    return row


def reasons(result):
    return [issue['reason'] for issue in result['broker_reporting']['unresolved']]


def resolved(result):
    assert result['broker_reporting']['complete'], reasons(result)
    assert result['crypto_summary']['reporting_complete'] is True
    assert result['crypto_summary']['reporting_unresolved'] == []
    return result


def blocked(result, fragment, *, unrouted=True):
    assert result['crypto_summary']['reporting_complete'] is False
    assert any(fragment in reason for reason in reasons(result)), reasons(result)
    if unrouted:
        assert all(row['box'] is None for row in rows(result))
    return result


@pytest.mark.parametrize('year,box,line', [(2025, 'L', '10'), (2024, 'F', '10'), (2023, 'F', '10')])
def test_reviewed_declaration_of_no_broker_form_routes_long_term(year, box, line):
    tx = [event('A', 'buy', 'buy', f'{year - 2}-01-01', usd=100),
          event('A', 'sale', 'sell', f'{year}-03-01', usd=150)]
    r = resolved(run(reporting(received=False), tx, year))
    assert only(r)['box'] == box
    routing = r['broker_reporting']['schedule_d_routing'][box]
    assert (routing['row'], routing['schedule_d_line']) == ('line_10_digital_assets', line)
    assert (routing['proceeds'], routing['cost'], routing['adjustment'], routing['gain']) == (150, 100, 0, 50)
    assert r['broker_reporting']['not_reported'] == [
        dict(account_id='A', txn_id='sale', evidence='broker-statement-A')]


def test_short_term_digital_asset_without_a_form_is_box_i_never_c():
    tx = [event('A', 'buy', 'buy', '2025-01-01', usd=100), BASE[1]]
    r = resolved(run(reporting(received=False), tx))
    assert only(r)['box'] == 'I'
    assert r['broker_reporting']['schedule_d_routing']['I']['row'] == 'line_3_digital_assets'


def test_form_without_reported_basis_routes_k_with_fifo_basis():
    r = resolved(run(reporting(form())))
    row = only(r)
    assert (row['box'], row['cost_basis'], row['adjustment'], row['adjustment_code']) == ('K', 100, 0, None)
    assert row['form_id'] == 'da-1' and row['gain_loss'] == 50
    assert r['broker_reporting']['schedule_d_routing']['K']['schedule_d_line'] == '9'
    assert r['broker_reporting']['forms'][0]['status'] == 'reconciled'


def test_form_with_matching_reported_basis_routes_j():
    r = resolved(run(reporting(form(basis_reported_to_irs=True, cost_basis=100, term='long'))))
    assert only(r)['box'] == 'J'
    assert r['broker_reporting']['schedule_d_routing']['J'] == dict(
        part='part_ii_long_term', row='line_8b_digital_assets', schedule_d_line='8b',
        proceeds=150, cost=100, adjustment=0, adjustment_code=None, gain=50, rows=1)


def test_tax_year_2024_routes_a_1099b_reported_basis_to_box_d():
    tx = [event('A', 'buy', 'buy', '2022-01-01', usd=100), event('A', 'sale', 'sell', '2024-03-01', usd=150)]
    block = reporting(form(form_type='1099-B', date_sold='2024-03-01', basis_reported_to_irs=True,
                           cost_basis=100, term='long'))
    assert only(resolved(run(block, tx, 2024)))['box'] == 'D'


@pytest.mark.parametrize('reported_basis,adjustment', [(80, -20), (130, 30)])
def test_reported_basis_difference_blocks_until_evidence_then_code_b_keeps_fifo_gain(reported_basis, adjustment):
    # Worksheet: line 1 = reported basis, line 2 = FIFO $100. 80 < 100 -> (20) in (g);
    # 130 > 100 -> 30 in (g). Either way (h) = 150 - reported + (g) = 50.
    block = reporting(form(basis_reported_to_irs=True, cost_basis=reported_basis, term='long'))
    blocked(run(block), 'cost basis differs from the form by')
    block['resolutions'] = {'da-1': dict(basis='taxpayer_records', evidence='fifo-basis-workpaper')}
    r = resolved(run(block))
    row = only(r)
    assert (row['box'], row['adjustment_code'], row['cost_basis'], row['adjustment']) == (
        'J', 'B', reported_basis, adjustment)
    assert (row['fifo_cost_basis'], row['gain_loss']) == (100, 50)
    assert r['schedule_d']['long_term'] == dict(proceeds=150, cost_basis=reported_basis,
                                                adjustment=adjustment, gain=50, rows=1)
    record = r['broker_reporting']['forms'][0]
    assert (record['codes'], record['evidence']) == ('B', 'fifo-basis-workpaper')
    assert r['broker_reporting']['schedule_d_routing']['J']['adjustment_code'] == 'B'


def test_unreported_but_shown_wrong_basis_takes_correct_basis_with_zero_adjustment():
    block = reporting(form(cost_basis=90))
    blocked(run(block), 'cost basis differs')
    block['resolutions'] = {'da-1': dict(basis='taxpayer_records', evidence='fifo-basis-workpaper')}
    row = only(resolved(run(block)))
    assert (row['box'], row['adjustment_code'], row['cost_basis'], row['adjustment'], row['gain_loss']) == (
        'K', 'B', 100, 0, 50)


def test_higher_actual_proceeds_go_in_column_d_and_are_reported_above_the_form():
    block = reporting(form(proceeds=140))
    blocked(run(block), 'proceeds differ from the form by +10.00')
    block['resolutions'] = {'da-1': dict(proceeds='taxpayer_records', evidence='trade-confirmation')}
    r = resolved(run(block))
    row = only(r)
    assert (row['proceeds'], row['adjustment_code'], row['gain_loss']) == (150, None, 50)
    assert r['crypto_summary']['reporting_proceeds_above_forms'] == 10


def test_unreflected_transaction_costs_are_code_e_on_form_proceeds():
    # Sold for $155 less a $5 fee: FIFO net $150. The form shows $155 gross:
    # (d) 155, (e) 100, E (5) in (g), (h) 50.
    tx = [BASE[0], event('A', 'sale', 'sell', '2025-03-01', usd=155, fee_usd=5)]
    block = reporting(form(proceeds=155))
    blocked(run(block, tx), 'proceeds differ from the form by -5.00')
    block['resolutions'] = {'da-1': dict(proceeds='transaction_costs', evidence='fee-schedule')}
    row = only(resolved(run(block, tx)))
    assert (row['proceeds'], row['cost_basis'], row['adjustment_code'], row['adjustment'], row['gain_loss']) == (
        155, 100, 'E', -5, 50)


@pytest.mark.parametrize('usd,fee,proceeds', [(150, 0, 1500), (155, 5, 160)])
def test_code_e_explains_no_more_than_the_recorded_fee(usd, fee, proceeds):
    # A form 1,350 above a fee-less sale is a wrong price, not unreflected costs;
    # code E there would cut the gain by 1,350 while the proceeds still tie.
    tx = [BASE[0], event('A', 'sale', 'sell', '2025-03-01', usd=usd, fee_usd=fee)]
    block = reporting(form(proceeds=proceeds),
                      resolutions={'da-1': dict(proceeds='transaction_costs', evidence='fee-schedule')})
    blocked(run(block, tx), 'recorded fees left to explain it')


@pytest.mark.parametrize('proceeds,treatment', [(140, 'transaction_costs'), (155, 'taxpayer_records')])
def test_a_treatment_in_the_wrong_direction_never_resolves(proceeds, treatment):
    block = reporting(form(proceeds=proceeds),
                      resolutions={'da-1': dict(proceeds=treatment, evidence='review')})
    r = blocked(run(block), 'proceeds differ')
    assert any('names no difference' in reason for reason in reasons(r))


def test_wrong_holding_period_is_code_t_on_the_correct_part():
    block = reporting(form(basis_reported_to_irs=True, cost_basis=80, term='short'))
    r = blocked(run(block), 'holding period differs')
    block['resolutions'] = {'da-1': dict(basis='taxpayer_records', term='taxpayer_records', evidence='lot-history')}
    r = resolved(run(block))
    assert r['form_8949']['part_i_short_term'] == []
    row = only(r)
    assert (row['box'], row['adjustment_code'], row['adjustment'], row['gain_loss']) == ('J', 'BT', -20, 50)
    # The Schedule D row's code is the union of its rows' column (f) codes.
    assert r['broker_reporting']['schedule_d_routing']['J']['adjustment_code'] == 'BT'


def test_stale_resolution_blocks_instead_of_lingering():
    block = reporting(form(), resolutions={'da-1': dict(basis='taxpayer_records', evidence='review')})
    blocked(run(block), "resolution ['basis'] names no difference")


# One disposal of 3 BTC for $100: FIFO takes 1 long-term unit, then 2 short-term.
SPLIT = [event('A', 'old', 'buy', '2023-01-01', 1, 10), event('A', 'new', 'buy', '2025-01-01', 2, 60),
         event('A', 'sale', 'sell', '2025-03-01', 3, 100)]


def test_unreported_basis_form_spanning_both_periods_routes_h_and_k():
    r = resolved(run(reporting(form(quantity='3', proceeds=100)), SPLIT))
    by_box = {row['box']: row for row in rows(r)}
    assert set(by_box) == {'H', 'K'}
    assert (by_box['K']['proceeds'], by_box['K']['cost_basis']) == (33.33, 10)
    assert (by_box['H']['proceeds'], by_box['H']['cost_basis']) == (66.67, 60)


def test_basis_reported_single_form_cannot_be_split_across_periods():
    block = reporting(form(quantity='3', proceeds=100, basis_reported_to_irs=True, cost_basis=70, term='long'),
                      resolutions={'da-1': dict(term='taxpayer_records', evidence='lot-history')})
    r = blocked(run(block, SPLIT), 'spans both holding periods')
    # Even with the reported basis equal to FIFO, a stated term the FIFO split
    # contradicts is a lot-identification difference. The term resolution
    # addresses it but cannot settle it, so it is never called stale.
    assert not any('names no difference' in reason for reason in reasons(r)), reasons(r)
    # A blank box 6 is no licence to split a wrong reported basis by quantity.
    block = reporting(form(quantity='3', proceeds=100, basis_reported_to_irs=True, cost_basis=100),
                      resolutions={'da-1': dict(basis='taxpayer_records', evidence='lot-history')})
    blocked(run(block, SPLIT), 'spans both holding periods')
    # When the reported basis equals FIFO, each part keeps its own FIFO basis.
    r = resolved(run(reporting(form(quantity='3', proceeds=100, basis_reported_to_irs=True,
                                    cost_basis=70)), SPLIT))
    assert {row['box']: row['cost_basis'] for row in rows(r)} == {'G': 60, 'J': 10}


def test_split_forms_adopt_the_brokers_apportionment_cent():
    # FIFO apportions 33.33 / 66.67; the broker rounded 33.34 / 66.66. The totals
    # agree, so each part takes the form's figure instead of blocking on a cent.
    forms = [form(form_id='da-lt', quantity='1', proceeds=33.34, basis_reported_to_irs=True,
                  cost_basis=10, term='long'),
             form(form_id='da-st', quantity='2', proceeds=66.66, basis_reported_to_irs=True,
                  cost_basis=60, term='short')]
    r = resolved(run(reporting(*forms), SPLIT))
    by_box = {row['box']: row for row in rows(r)}
    assert (by_box['J']['proceeds'], by_box['J']['gain_loss']) == (33.34, 23.34)
    assert (by_box['G']['proceeds'], by_box['G']['gain_loss']) == (66.66, 6.66)
    assert r['schedule_d']['net_gain'] == 30


# One disposal of 1 BTC for $400 from three lots of $100.005 each, all
# short-term. Exact basis 300.015; each row takes the rounded running total
# less the cents already posted: 100.01, 100.00, 100.01 (300.02), gain 99.98.
THIRDS = [event('A', 'b1', 'buy', '2025-01-01', '0.333333', '100.005'),
          event('A', 'b2', 'buy', '2025-01-02', '0.333333', '100.005'),
          event('A', 'b3', 'buy', '2025-01-03', '0.333334', '100.005'),
          event('A', 'sale', 'sell', '2025-03-01', 1, 400)]


def _basis_and_gain(result):
    return [(row['cost_basis'], row['gain_loss']) for row in rows(result)]


def test_a_matching_form_keeps_the_fifo_gain_it_would_have_without_one():
    no_form = resolved(run(reporting(received=False), THIRDS))
    with_form = resolved(run(reporting(form(proceeds=400, term='short')), THIRDS))
    # Proceeds post the same way: 133.33, 133.34, 133.33.
    expected = [(100.01, 33.32), (100.0, 33.34), (100.01, 33.32)]
    assert _basis_and_gain(no_form) == _basis_and_gain(with_form) == expected
    assert no_form['schedule_d']['net_gain'] == with_form['schedule_d']['net_gain'] == 99.98
    assert [row['fifo_cost_basis'] for row in rows(with_form)] == [100.01, 100.0, 100.01]
    # A basis-reported form showing the same total routes G with no code.
    reported = resolved(run(reporting(form(proceeds=400, basis_reported_to_irs=True, cost_basis=300.02,
                                           term='short')), THIRDS))
    assert {(row['box'], row['adjustment_code']) for row in rows(reported)} == {('G', None)}
    assert _basis_and_gain(reported) == expected


def test_split_forms_adopt_the_brokers_basis_apportionment_cent():
    # Lots of $10.005 (1 unit, long) and $50.005 (2 units, short): FIFO posts
    # 10.01 then 50.00 (rounded running total 60.01). The broker rounded each
    # part on its own the other way, 10.00 / 50.01; the totals agree, so each
    # part takes the form's basis and the net gain is unchanged.
    tx = [event('A', 'old', 'buy', '2023-01-01', 1, '10.005'), event('A', 'new', 'buy', '2025-01-01', 2, '50.005'),
          event('A', 'sale', 'sell', '2025-03-01', 3, 100)]
    forms = [form(form_id='da-lt', quantity='1', proceeds=33.33, basis_reported_to_irs=True,
                  cost_basis=10.00, term='long'),
             form(form_id='da-st', quantity='2', proceeds=66.67, basis_reported_to_irs=True,
                  cost_basis=50.01, term='short')]
    r = resolved(run(reporting(*forms), tx))
    by_box = {row['box']: row for row in rows(r)}
    assert (by_box['J']['cost_basis'], by_box['J']['gain_loss'], by_box['J']['adjustment_code']) == (10.0, 23.33, None)
    assert (by_box['G']['cost_basis'], by_box['G']['gain_loss'], by_box['G']['adjustment_code']) == (50.01, 16.66, None)
    assert r['schedule_d']['net_gain'] == 39.99
    forms[0]['cost_basis'], forms[1]['cost_basis'] = 9.99, 50.02
    blocked(run(reporting(*forms), tx), 'cost basis differs')


@pytest.mark.parametrize('reported', [(True, False), (False, True), (False, False)])
@pytest.mark.parametrize('shown', [(10.00, 50.01), (10.02, 49.99)])
def test_split_basis_cents_require_evidence_unless_both_forms_adopt_them(reported, shown):
    # FIFO posts $10.01 long + $50.00 short = $60.01; proceeds $100 -> gain
    # $39.99. Both broker splits also total $60.01, but an unreported basis
    # stays FIFO. Correct reported basis via (g) = shown - FIFO and retain
    # unreported FIFO basis in (e), so either direction keeps the same gain.
    tx = [event('A', 'old', 'buy', '2023-01-01', 1, '10.005'),
          event('A', 'new', 'buy', '2025-01-01', 2, '50.005'),
          event('A', 'sale', 'sell', '2025-03-01', 3, 100)]
    forms = [form(form_id='da-lt', quantity='1', proceeds=33.33, cost_basis=shown[0],
                  basis_reported_to_irs=reported[0], term='long'),
             form(form_id='da-st', quantity='2', proceeds=66.67, cost_basis=shown[1],
                  basis_reported_to_irs=reported[1], term='short')]
    block = reporting(*forms)
    r = blocked(run(block, tx), 'cost basis differs')
    assert {record['status'] for record in r['broker_reporting']['forms']} == {'unresolved'}
    block['resolutions'] = {'da-lt': dict(basis='taxpayer_records', evidence='fifo-basis-workpaper')}
    blocked(run(block, tx), 'cost basis differs')
    block['resolutions']['da-st'] = dict(basis='taxpayer_records', evidence='fifo-basis-workpaper')
    r = resolved(run(block, tx))
    by_form = {row['form_id']: row for row in rows(r)}
    for i, (form_id, fifo, proceeds, gain, boxes) in enumerate([
            ('da-lt', 10.01, 33.33, 23.32, ('K', 'J')),
            ('da-st', 50.00, 66.67, 16.67, ('H', 'G'))]):
        row = by_form[form_id]
        assert (row['box'], row['proceeds'], row['cost_basis'], row['adjustment'],
                row['adjustment_code'], row['fifo_cost_basis'], row['gain_loss']) == (
            boxes[reported[i]], proceeds, shown[i] if reported[i] else fifo,
            round(shown[i] - fifo, 2) if reported[i] else 0, 'B', fifo, gain)
    assert r['schedule_d']['net_gain'] == 39.99
    assert {(record['codes'], record['evidence']) for record in r['broker_reporting']['forms']} == {
        ('B', 'fifo-basis-workpaper')}


@pytest.mark.parametrize('value,cents', [
    ('0.005', 1), ('-0.005', -1), ('0.015', 2), ('-0.015', -2), ('0.0049', 0), ('-0.0049', 0), ('-1.234', -123)])
def test_cents_rounds_ties_away_from_zero(value, cents):
    from fractions import Fraction
    assert C._cents(Fraction(value)) == cents


def test_split_forms_two_cents_apart_still_block():
    forms = [form(form_id='da-lt', quantity='1', proceeds=33.35, term='long'),
             form(form_id='da-st', quantity='2', proceeds=66.65, term='short')]
    blocked(run(reporting(*forms), SPLIT), 'proceeds differ')


@pytest.mark.parametrize('terms,quantities,fragment', [
    (('long', 'long'), ('1', '2'), 'different holding period'),
    (('long', None), ('1', '2'), 'different holding period'),
    (('long', 'short'), ('2', '1'), 'units differ'),
])
def test_split_forms_must_mirror_the_fifo_split(terms, quantities, fragment):
    forms = [form(form_id=f'da-{i}', quantity=q, proceeds=50, term=t)
             for i, (t, q) in enumerate(zip(terms, quantities))]
    r = blocked(run(reporting(*forms), SPLIT), fragment)
    # Each form still reaches the workpaper with the broker's figures, and each
    # issue names its form: a reviewer has to know which line to correct.
    records = r['broker_reporting']['forms']
    assert [(f['form_id'], f['status'], f['broker_proceeds']) for f in records] == [
        ('da-0', 'unresolved', 50), ('da-1', 'unresolved', 50)]
    assert all(fragment in f['reason'] for f in records)
    assert sorted(i['form_id'] for i in r['broker_reporting']['unresolved']) == ['da-0', 'da-1']


def test_unpaired_split_forms_are_set_against_the_whole_disposal():
    forms = [form(form_id='da-a', quantity='1', proceeds=50, term='long'),
             form(form_id='da-b', quantity='2', proceeds=50, term='long')]
    records = blocked(run(reporting(*forms), SPLIT), 'different holding period')['broker_reporting']['forms']
    # Neither form pairs with a part, so each shows the disposal's FIFO
    # figures: $100 proceeds, $10 + $60 basis.
    assert {(f['fifo_proceeds'], f['fifo_cost_basis']) for f in records} == {(100, 70)}


def test_a_form_that_reconciles_alone_is_blocked_with_its_disposal():
    # The long-term form agrees; the short-term one is $0.67 short. A disposal
    # routes all its rows or none, so the agreeing form must not read as done.
    forms = [form(form_id='da-lt', quantity='1', proceeds=33.33, term='long'),
             form(form_id='da-st', quantity='2', proceeds=66.00, term='short')]
    r = blocked(run(reporting(*forms), SPLIT), 'proceeds differ')
    status = {f['form_id']: (f['status'], f['reason']) for f in r['broker_reporting']['forms']}
    assert status['da-lt'] == ('blocked', 'another form for this disposal is unresolved: da-st')
    assert status['da-st'][0] == 'unresolved'


@pytest.mark.parametrize('field,value,fragment', [
    ('asset', 'ETH', 'asset differs'), ('date_sold', '2025-03-03', 'date sold differs'),
    ('quantity', '0.5', 'units differ')])
def test_a_form_describing_another_sale_is_unresolved(field, value, fragment):
    r = blocked(run(reporting(form(**{field: value}))), fragment)
    # The reviewer still sees both figures for the form that needs review.
    (record,) = r['broker_reporting']['forms']
    assert (record['form_id'], record['status'], record['broker_proceeds'], record['fifo_proceeds'],
            record['fifo_cost_basis']) == ('da-1', 'unresolved', 150, 150, 100)
    assert fragment in record['reason']


@pytest.mark.parametrize('stamp,form_date', [
    ('2025-03-01T22:00:00-05:00', '2025-03-02'),  # the broker dated it in UTC
    ('2025-03-01T01:00:00+09:00', '2025-02-28'),  # a stamp east of UTC, the form in UTC
    ('2025-03-01', '2025-03-02')])                # a date-only stamp: its zone is unknown
def test_a_broker_date_one_time_zone_away_reconciles(stamp, form_date):
    # §1.6045-1(d)(2)(i)(B)(3) asks only for "the sale date", naming no time
    # zone; every offset is under 24 hours, so a broker's date is the import's
    # calendar date or the day either side of it. The row keeps the import's date.
    tx = [BASE[0], event('A', 'sale', 'sell', stamp, usd=150)]
    r = resolved(run(reporting(form(date_sold=form_date)), tx))
    assert only(r)['date_sold'] == stamp[:10]
    (record,) = r['broker_reporting']['forms']
    assert (record['date_sold'], record['broker_date_sold']) == (stamp[:10], form_date)


@pytest.mark.parametrize('stamp,form_date', [
    ('2025-03-01T22:00:00-05:00', '2025-03-03'),  # two days is no time zone
    ('2025-12-31T22:00:00-05:00', '2026-01-01')])  # another year's form
def test_a_broker_date_further_away_or_in_another_year_blocks(stamp, form_date):
    tx = [BASE[0], event('A', 'sale', 'sell', stamp, usd=150)]
    blocked(run(reporting(form(date_sold=form_date)), tx), 'date sold differs')


@pytest.mark.parametrize('txn_id,date', [('buy', '2023-01-01'), ('old-sale', '2024-06-01')])
def test_not_reported_must_name_a_filing_year_disposal(txn_id, date):
    tx = [event('A', 'buy', 'buy', '2023-01-01', 2, 200), event('A', 'old-sale', 'sell', '2024-06-01', usd=150),
          BASE[1]]
    r = blocked(run(reporting(form(), not_reported=[txn_id]), tx), 'names no filing-year disposal', unrouted=False)
    assert only(r)['box'] == 'K'
    assert r['broker_reporting']['accounts']['A']['not_reported'] == [txn_id]


def test_received_account_must_list_each_disposal_without_a_form():
    blocked(run(reporting()), 'no broker form reports this disposal')
    row = only(resolved(run(reporting(not_reported=['sale']))))
    assert row['box'] == 'L'


def test_undeclared_account_is_never_presumed_formless():
    block = reporting(form())
    block['accounts'] = {}
    blocked(run(block), 'declare whether this account received broker forms')
    blocked(run(None), 'declare whether')


@pytest.mark.parametrize('txn_id,date_sold', [('buy', '2023-01-01'), ('old-sale', '2024-06-01')])
def test_a_form_for_a_non_filing_year_disposal_is_surfaced(txn_id, date_sold):
    tx = [event('A', 'buy', 'buy', '2023-01-01', 2, 200), event('A', 'old-sale', 'sell', '2024-06-01', usd=150),
          BASE[1]]
    block = reporting(form(), form(form_id='da-2', txn_id=txn_id, date_sold=date_sold))
    # The sale its own form reconciles keeps its box; the stray form blocks.
    r = blocked(run(block, tx), 'reports no filing-year disposal', unrouted=False)
    assert only(r)['box'] == 'K'


def test_unreviewed_year_blocks_rows_but_not_a_year_without_disposals():
    tx = [event('A', 'buy', 'buy', '2024-01-01', usd=100), event('A', 'sale', 'sell', '2026-03-01', usd=150)]
    blocked(run(None, tx, 2026), 'TY2026 has no reviewed Form 8949 reporting regime')
    assert resolved(run(None, tx[:1], 2026))['broker_reporting']['schedule_d_routing'] == {}
    blocked(C.build_crypto_import(copy.deepcopy(BASE), specific_identification=False), 'needs the filing year')


@pytest.mark.parametrize('per_account', [False, True])
def test_legacy_pools_cannot_route_a_box(per_account):
    tx = [dict(date='2023-01-01', kind='buy', asset='BTC', quantity=1, usd_amount=100),
          dict(date='2024-03-01', kind='sell', asset='BTC', quantity=1, usd_amount=150)]
    r = C.build_crypto_import(tx, tax_year=2024, per_account=per_account, specific_identification=False)
    blocked(r, 'account-aware import')
    with pytest.raises(C.CryptoImportError, match='account-aware'):
        C.build_crypto_import(tx, tax_year=2024, per_account=per_account, broker_reporting=reporting(), specific_identification=False)


def mutate(change):
    block = reporting(form())
    change(block)
    return block


@pytest.mark.parametrize('change', [
    lambda b: b.update(extra=1),
    lambda b: b.update(forms={}),
    lambda b: b['forms'][0].update(federal_withholding=0),
    lambda b: b['forms'][0].pop('proceeds'),
    lambda b: b['forms'][0].pop('term'),
    lambda b: b['forms'][0].pop('cost_basis'),
    lambda b: b['forms'][0].update(form_type='1099-B'),
    lambda b: b['forms'][0].update(txn_id='missing'),
    lambda b: b['forms'][0].update(account_id='Z'),
    lambda b: b['forms'][0].update(basis_reported_to_irs=True),
    lambda b: b['forms'][0].update(basis_reported_to_irs='yes'),
    lambda b: b['forms'][0].update(term='ordinary'),
    lambda b: b['forms'][0].update(date_sold='03/01/2025'),
    lambda b: b['forms'][0].update(date_sold=None),
    lambda b: b['forms'][0].update(quantity='0'),
    lambda b: b['forms'][0].update(proceeds=150.005),
    lambda b: b['forms'][0].update(proceeds=-1),
    lambda b: b['forms'][0].update(proceeds=float('nan')),
    lambda b: b['forms'][0].update(proceeds=True),
    lambda b: b['forms'][0].update(cost_basis=float('inf')),
    lambda b: b['forms'][0].update(ordinary=True),
    lambda b: b['forms'][0].update(wash_sale_loss_disallowed=12),
    lambda b: b['forms'][0].update(accrued_market_discount='1.50'),
    lambda b: b['forms'][0].update(loss_not_allowed=True),
    lambda b: b['forms'][0].pop('wash_sale_loss_disallowed'),
    lambda b: b['forms'][0].pop('ordinary'),
    lambda b: b['forms'].append(dict(b['forms'][0])),
    lambda b: b['accounts'].update(Z=dict(forms_received=False, evidence='review')),
    lambda b: b['accounts']['A'].update(forms_received=1),
    lambda b: b['accounts']['A'].pop('evidence'),
    # An SSN-shaped run in an evidence reference, assembled so no fixture holds one.
    lambda b: b['accounts']['A'].update(evidence='statement ' + '9' * 9),
    lambda b: b['accounts']['A'].update(forms_received=False),
    lambda b: b['accounts']['A'].update(not_reported=['sale']),
    lambda b: b['accounts']['A'].update(not_reported=['missing']),
    lambda b: b['accounts']['A'].update(not_reported=['buy', 'buy']),
    lambda b: b.update(resolutions={'nope': dict(basis='taxpayer_records', evidence='review')}),
    lambda b: b.update(resolutions={'da-1': dict(basis='broker_records', evidence='review')}),
    lambda b: b.update(resolutions={'da-1': dict(basis='taxpayer_records')}),
    lambda b: b.update(resolutions={'da-1': dict(evidence='review')}),
    lambda b: b.update(resolutions={'da-1': dict(gain='taxpayer_records', evidence='review')}),
    lambda b: b.update(resolutions={'da-1': dict(proceeds=['taxpayer_records'], evidence='review')}),
    lambda b: b.update(resolutions={'da-1': dict(basis={'taxpayer_records': 1}, evidence='review')}),
])
def test_malformed_or_contradictory_evidence_is_refused(change):
    with pytest.raises(C.CryptoImportError):
        run(mutate(change))


@pytest.mark.parametrize('year', [2026, None])
def test_evidence_for_an_unreviewed_or_unknown_year_is_refused(year):
    with pytest.raises(C.CryptoImportError):
        run(reporting(form()), year=year)


# Each value a broker could fill in: "Net proceeds" in 3a, the QOF box, box 8,
# either optional-method box in 11a, first-sale proceeds in 11c, any CUSIP.
@pytest.mark.parametrize('key,value', [
    ('net_proceeds', True), ('qof', True),
    ('optional_method', 'qualifying_stablecoins'), ('optional_method', True),
    ('primary_sale_proceeds', 25), ('cusip', 'TKN000A01')])
def test_a_filled_1099da_box_refuses_the_import_and_names_its_key(key, value):
    with pytest.raises(C.CryptoImportError, match=rf"fills \['{key}'\], which this engine does not model"):
        run(reporting(form(**{key: value})))


def test_the_refusal_says_how_a_blank_box_is_copied():
    # "" is the natural copy of a blank text box, and refusing it as a filled
    # one sends the preparer to report a sale by hand for nothing.
    with pytest.raises(C.CryptoImportError, match='a blank box is copied as null, false or 0'):
        run(reporting(form(cusip='')))


def test_the_refusal_names_every_filled_box_and_only_those():
    with pytest.raises(C.CryptoImportError, match=r"fills \['ordinary', 'qof'\],"):
        run(reporting(form(ordinary=True, qof=True)))


@pytest.mark.parametrize('key,blank', [('net_proceeds', None), ('qof', None),
                                       ('optional_method', False),
                                       ('primary_sale_proceeds', 0), ('primary_sale_proceeds', 0.0)])
def test_a_1099da_box_left_blank_passes(key, blank):
    assert only(resolved(run(reporting(form(**{key: blank})))))['box'] == 'K'


@pytest.mark.parametrize('key', sorted(DA_BLANKS))
def test_each_1099da_key_is_required_on_a_1099da(key):
    # A box nobody copied must not read as a blank one, and a summary parsed
    # before the box was read cannot say it was blank.
    copied = form()
    del copied[key]
    with pytest.raises(C.CryptoImportError, match=rf"is missing \['{key}'\].*; re-read each missing box "
                       "from the form itself, never from a summary that did not capture it, and copy a "
                       "blank one as null"):
        run(reporting(copied))


TX_2024 = [event('A', 'buy', 'buy', '2022-01-01', usd=100), event('A', 'sale', 'sell', '2024-03-01', usd=150)]


def form_1099b(**extra):
    return form(**{**dict(form_type='1099-B', date_sold='2024-03-01', basis_reported_to_irs=True,
                          cost_basis=100, term='long'), **extra})


@pytest.mark.parametrize('key', sorted(DA_BLANKS.keys() - B_BLANKS.keys()))
def test_a_1099b_names_none_of_the_1099da_only_keys(key):
    with pytest.raises(C.CryptoImportError, match=rf"names unknown \['{key}'\]"):
        run(reporting(form_1099b(**{key: DA_BLANKS[key]})), TX_2024, 2024)


def test_the_shared_boxes_still_refuse_on_a_1099b():
    with pytest.raises(C.CryptoImportError, match=r"fills \['wash_sale_loss_disallowed'\],"):
        run(reporting(form_1099b(wash_sale_loss_disallowed=12)), TX_2024, 2024)


def test_the_form_type_is_checked_before_the_keys_it_decides():
    # A 1099-B entered for a 1099-DA year is the wrong form, not one with
    # boxes missing: the keys a form needs depend on its type.
    with pytest.raises(C.CryptoImportError, match='form_type must be 1099-DA for TY2025'):
        run(reporting(form_1099b(date_sold='2025-03-01')))


def test_every_routed_form_type_has_its_own_key_set():
    assert set(C._FORM_UNSUPPORTED_BY_TYPE) == {regime['form_type'] for regime in C._REPORTING_REGIMES.values()}
    assert C._FORM_UNSUPPORTED_BY_TYPE['1099-DA'] | C._FORM_FIELDS_BY_TYPE['1099-DA'] == set(DA_BLANKS)
    assert C._FORM_UNSUPPORTED_BY_TYPE['1099-B'] == set(B_BLANKS)
    assert not any(C._FORM_UNSUPPORTED & keys for keys in C._FORM_UNSUPPORTED_BY_TYPE.values())


@pytest.mark.parametrize('year', [2023, 2024])
def test_a_legacy_1099b_summary_cannot_attest_unread_boxes_were_blank(year):
    tx = [event('A', 'buy', 'buy', f'{year - 2}-01-01', usd=100),
          event('A', 'sale', 'sell', f'{year}-03-01', usd=150)]
    copied = form_1099b(date_sold=f'{year}-03-01')
    for key in B_BLANKS:
        del copied[key]
    with pytest.raises(C.CryptoImportError, match='is missing.*collectibles.*re-read each missing box'):
        run(reporting(copied), tx, year)


@pytest.mark.parametrize('year', [2023, 2024])
@pytest.mark.parametrize('key', sorted(B_BLANKS))
def test_each_reviewed_1099b_box_must_be_copied_from_the_form(year, key):
    tx = [event('A', 'buy', 'buy', f'{year - 2}-01-01', usd=100),
          event('A', 'sale', 'sell', f'{year}-03-01', usd=150)]
    copied = form_1099b(date_sold=f'{year}-03-01')
    del copied[key]
    with pytest.raises(C.CryptoImportError, match=rf"is missing \['{key}'\].*re-read each missing box"):
        run(reporting(copied), tx, year)


@pytest.mark.parametrize('year', [2023, 2024])
@pytest.mark.parametrize('key,value', [
    ('collectibles', True), ('qof', True), ('net_proceeds', True), ('cusip', 'TKN000A01'),
    ('contract_profit_loss', 10), ('contract_profit_loss', -10),
    ('prior_unrealized_profit_loss', 10), ('prior_unrealized_profit_loss', -10),
    ('current_unrealized_profit_loss', 10), ('current_unrealized_profit_loss', -10),
    ('aggregate_contract_profit_loss', 10), ('aggregate_contract_profit_loss', -10),
    ('bartering', 10),
])
def test_1099b_special_treatments_refuse_before_capital_gain_routing(year, key, value):
    tx = [event('A', 'buy', 'buy', f'{year - 2}-01-01', usd=100),
          event('A', 'sale', 'sell', f'{year}-03-01', usd=150)]
    with pytest.raises(C.CryptoImportError, match=rf"fills \['{key}'\], which this engine does not model"):
        run(reporting(form_1099b(date_sold=f'{year}-03-01', **{key: value})), tx, year)


@pytest.mark.parametrize('year', [2023, 2024])
def test_reviewed_blank_1099b_boxes_allow_the_ordinary_crypto_sale(year):
    tx = [event('A', 'buy', 'buy', f'{year - 2}-01-01', usd=100),
          event('A', 'sale', 'sell', f'{year}-03-01', usd=150)]
    row = only(resolved(run(reporting(form_1099b(date_sold=f'{year}-03-01')), tx, year)))
    assert (row['box'], row['gain_loss']) == ('D', 50)


def test_reconciled_rows_keep_input_order_and_identity():
    tx = [*BASE, event('A', 'buy2', 'buy', '2025-01-05', usd=40), event('A', 'sale2', 'sell', '2025-04-01', usd=50)]
    block = reporting(form(), not_reported=['sale2'])
    r = resolved(run(block, tx))
    assert [(row['txn_id'], row['box']) for row in rows(r)] == [('sale2', 'I'), ('sale', 'K')]


FILLS = [BASE[0], event('A', 'fill1', 'sell', '2025-03-01', qty='0.4', usd=60),
         event('A', 'fill2', 'sell', '2025-03-01', qty='0.6', usd=90)]


def fill_reporting():
    return {**reporting(form(txn_id='order1')),
            'fill_groups': [dict(account_id='A', group_id='order1', txn_ids=['fill1', 'fill2'],
                                 evidence='single-sale-confirmation')]}


def test_a_reviewed_broker_sale_links_multiple_fills_without_changing_fifo_identity():
    result = resolved(run(fill_reporting(), FILLS))
    assert [(r['txn_id'], r['form_id'], r['box']) for r in rows(result)] == [
        ('fill1', 'da-1', 'K'), ('fill2', 'da-1', 'K')]
    assert [(r['proceeds'], r['cost_basis'], r['gain_loss']) for r in rows(result)] == [
        (60, 40, 20), (90, 60, 30)]
    record, = result['broker_reporting']['forms']
    assert record['txn_id'] == 'order1'
    assert record['fifo_proceeds'] == record['broker_proceeds'] == 150
    assert result['broker_reporting']['fill_groups'] == fill_reporting()['fill_groups']
    assert result['broker_reporting']['schedule_d_routing']['K']['rows'] == 2


@pytest.mark.parametrize('year', [2023, 2024])
def test_linked_fills_also_use_the_reviewed_1099b_regime(year):
    tx = [event('A', 'buy', 'buy', f'{year - 2}-01-01', usd=100),
          event('A', 'fill1', 'sell', f'{year}-03-01', qty='0.4', usd=60),
          event('A', 'fill2', 'sell', f'{year}-03-01', qty='0.6', usd=90)]
    block = fill_reporting()
    block['forms'] = [form_1099b(txn_id='order1', date_sold=f'{year}-03-01')]
    result = resolved(run(block, tx, year))
    assert [(r['box'], r['gain_loss']) for r in rows(result)] == [('D', 20), ('D', 30)]


@pytest.mark.parametrize('change', [
    lambda b: b.update(fill_groups={}),
    lambda b: b['fill_groups'][0].update(txn_ids=['fill1']),
    lambda b: b['fill_groups'][0].update(txn_ids=['fill1', 'fill1']),
    lambda b: b['fill_groups'][0].update(txn_ids=['fill1', 'missing']),
    lambda b: b['fill_groups'][0].update(txn_ids=['fill1', 'buy']),
    lambda b: b['fill_groups'][0].update(group_id='fill1'),
    lambda b: b['fill_groups'][0].update(account_id='elsewhere'),
    lambda b: b['fill_groups'][0].update(evidence=''),
    lambda b: b['fill_groups'][0].update(unknown=True),
    lambda b: b['fill_groups'].append({**b['fill_groups'][0], 'group_id':'overlap'}),
    lambda b: b['accounts']['A'].update(not_reported=['fill1']),
    lambda b: b['forms'].clear(),
    lambda b: b['forms'].append(form(form_id='direct', txn_id='fill1', quantity='0.4', proceeds=60)),
])
def test_fill_groups_refuse_ambiguous_missing_or_contradictory_evidence(change):
    block = fill_reporting()
    change(block)
    with pytest.raises(C.CryptoImportError):
        run(block, FILLS)


@pytest.mark.parametrize('change', [
    lambda tx: tx[2].update(date='2025-03-02'),
    lambda tx: tx[2].update(asset='ETH'),
])
def test_fills_must_be_one_same_asset_same_day_sale(change):
    tx = copy.deepcopy(FILLS)
    change(tx)
    with pytest.raises(C.CryptoImportError):
        run(fill_reporting(), tx)


def test_grouped_fill_totals_still_require_exact_form_reconciliation():
    block = fill_reporting()
    block['forms'][0]['proceeds'] = 150.01
    blocked(run(block, FILLS), 'proceeds differ')
    block = fill_reporting()
    block['forms'][0]['quantity'] = '0.999'
    blocked(run(block, FILLS), 'units differ')


def test_grouping_never_moves_an_intervening_acquisition_before_the_first_fill():
    tx = [event('A', 'old', 'buy', '2023-01-01', qty='0.4', usd=20),
          event('A', 'fill1', 'sell', '2025-03-01T12:00:00Z', qty='0.4', usd=60),
          event('A', 'new', 'buy', '2025-03-01T12:01:00Z', qty='0.6', usd=30),
          event('A', 'fill2', 'sell', '2025-03-01T12:02:00Z', qty='0.6', usd=90)]
    result = resolved(run(fill_reporting(), tx))
    actual = {r['txn_id']: (r['date_acquired'], r['box'], r['cost_basis'], r['gain_loss'])
              for r in rows(result)}
    assert actual == {'fill1': ('2023-01-01', 'K', 20, 40), 'fill2': ('2025-03-01', 'H', 30, 60)}


def test_group_corrections_cannot_spread_an_adjustment_across_fill_holding_periods():
    # Long fill: $60 - $1 fee - $20 basis = $39; short fill: $90 - $30 = $60.
    # Spreading the aggregate $1 fee by 0.4/0.6 units would produce $39.60
    # long and $59.40 short, moving gain between tax-rate classes.
    tx = [event('A', 'old', 'buy', '2023-01-01', qty='0.4', usd=20),
          event('A', 'fill1', 'sell', '2025-03-01T12:00:00Z', qty='0.4', usd=60, fee_usd=1),
          event('A', 'new', 'buy', '2025-03-01T12:01:00Z', qty='0.6', usd=30),
          event('A', 'fill2', 'sell', '2025-03-01T12:02:00Z', qty='0.6', usd=90)]
    block = fill_reporting()
    block['resolutions'] = {'da-1': dict(proceeds='transaction_costs', evidence='reviewed-fees')}
    with pytest.raises(C.CryptoImportError, match='group-level resolutions are not supported'):
        run(block, tx)


def test_grouped_fills_preserve_the_existing_reported_basis_holding_period_split():
    tx = [event('A', 'old', 'buy', '2023-01-01', qty='0.4', usd=20),
          event('A', 'new', 'buy', '2025-01-01', qty='0.6', usd=30), *FILLS[1:]]
    block = fill_reporting()
    block['forms'] = [form(form_id='long', txn_id='order1', quantity='0.4', proceeds=60,
                            cost_basis=20, basis_reported_to_irs=True, term='long'),
                      form(form_id='short', txn_id='order1', quantity='0.6', proceeds=90,
                            cost_basis=30, basis_reported_to_irs=True, term='short')]
    result = resolved(run(block, tx))
    assert {r['txn_id']: (r['form_id'], r['box'], r['gain_loss']) for r in rows(result)} == {
        'fill1': ('long', 'J', 40), 'fill2': ('short', 'G', 60)}


def test_a_fill_group_does_not_clear_an_unreviewed_reporting_year():
    tx = copy.deepcopy(FILLS)
    for fill in tx[1:]:
        fill['date'] = '2026-03-01'
    block = fill_reporting()
    block['forms'][0]['date_sold'] = '2026-03-01'
    with pytest.raises(C.CryptoImportError, match='no reviewed Form 8949 reporting regime'):
        run(block, tx, 2026)


def test_markdown_schedule_d_shows_the_column_g_adjustment():
    block = reporting(form(basis_reported_to_irs=True, cost_basis=80, term='long'),
                      resolutions={'da-1': dict(basis='taxpayer_records', evidence='fifo-basis-workpaper')})
    out = C.render_markdown(resolved(run(block))).splitlines()
    assert '| Term | Proceeds | Cost basis | Adjustment | Gain/(loss) | Rows |' in out
    assert '| Long-term | $150.00 | $80.00 | $-20.00 | $50.00 | 1 |' in out


def test_cli_exit_and_markdown_follow_reporting(tmp_path, capsys):
    path = tmp_path / 'synthetic.json'
    path.write_text(json.dumps(dict(transactions=BASE, specific_identification=False, broker_reporting=reporting(form()))))
    assert C._cli_compute([str(path), '--account-aware', '--year', '2025', '--markdown']) == 0
    out = capsys.readouterr().out
    assert 'BLOCKED' not in out and '| K | 9 | $150.00 | $100.00 | $0.00 | $50.00 | 1 |' in out
    path.write_text(json.dumps(dict(transactions=BASE, specific_identification=False, broker_reporting=reporting(form(proceeds=140)))))
    assert C._cli_compute([str(path), '--year', '2025', '--markdown']) == 2
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith('# BLOCKED — Form 8949 reporting')
    assert '- A / sale / da-1: proceeds differ from the form by +10.00' in out


# ---- tie-out ---------------------------------------------------------------

COMPLETE = dict(basis_mode='account_fifo', complete=True, unresolved_inputs=[],
                reporting_complete=True, reporting_unresolved=[], reporting_proceeds_above_forms=0)


@pytest.mark.parametrize('change', [dict(reporting_complete=False), dict(reporting_unresolved=[{'reason': 'x'}]),
                                    dict(reporting_complete=None)])
def test_tie_out_requires_reconciled_reporting(change):
    report = tie_out.evaluate([], {'crypto_summary': {**COMPLETE, **change}}, [], option_exercise_answer=False, specific_identification_answer=False)
    assert report.failed
    assert any('Form 8949 reporting' in target for target in report.missing_result_targets)


def test_tie_out_reporting_gate_survives_dropped_keys():
    for dropped in ('reporting_complete', 'reporting_unresolved'):
        summary = {key: value for key, value in COMPLETE.items() if key != dropped}
        assert tie_out.evaluate([], {'crypto_summary': summary}, [], option_exercise_answer=False, specific_identification_answer=False).failed
    summary = dict(reporting_complete=False)
    assert tie_out.evaluate([], {'crypto_summary': summary}, [], option_exercise_answer=False, specific_identification_answer=False).failed
    rows_only = {'form_8949': {'part_ii_long_term': [{'account_id': 'A'}]},
                 'crypto_summary': dict(basis_mode='account_fifo', complete=True, unresolved_inputs=[])}
    assert tie_out.evaluate([], rows_only, [], option_exercise_answer=False, specific_identification_answer=False).failed


def test_tie_out_accepts_reconciled_reporting():
    assert not tie_out.evaluate([], {'crypto_summary': COMPLETE}, [], option_exercise_answer=False, specific_identification_answer=False).failed


def test_merged_crypto_with_the_option_question_unanswered_fails():
    """The capability gate asks it once has_crypto is set, which a skipped
    /tax-parse-crypto step leaves false; the merged rows are the data."""
    report = tie_out.evaluate([], {'crypto_summary': COMPLETE}, [], option_exercise_answer=None, specific_identification_answer=False)
    assert report.failed
    assert any('has_digital_asset_option_exercise is unanswered' in t for t in report.missing_result_targets)
    assert not tie_out.evaluate([], {'crypto_summary': COMPLETE}, [], option_exercise_answer=True, specific_identification_answer=False).failed


def test_an_omitted_option_answer_is_unanswered():
    report = tie_out.evaluate([], {'crypto_summary': COMPLETE}, [], specific_identification_answer=False)
    assert report.failed
    assert any('has_digital_asset_option_exercise is unanswered' in target
               for target in report.missing_result_targets)
    assert any('sold, exchanged or spent' in target for target in report.missing_result_targets)


@pytest.mark.parametrize('answer', [0, 1, 'no'])
def test_a_non_boolean_option_answer_is_unanswered(answer):
    report = tie_out.evaluate([], {'crypto_summary': COMPLETE}, [], option_exercise_answer=answer, specific_identification_answer=False)
    assert report.failed
    assert any('has_digital_asset_option_exercise is unanswered' in target
               for target in report.missing_result_targets)


def test_a_missing_profile_does_not_answer_the_option_question(tmp_path, capsys):
    (tmp_path / 'documents').mkdir()
    (tmp_path / 'parse_ledger.json').write_text(json.dumps({'records': []}))
    (tmp_path / 'result.json').write_text(json.dumps({'crypto_summary': COMPLETE}))
    assert tie_out._main(['--workspace', str(tmp_path)]) == 2
    assert 'has_digital_asset_option_exercise is unanswered' in capsys.readouterr().out


@pytest.mark.parametrize('answer,owed', [(None, True), ('yes', True), (False, False), (True, False)])
def test_the_gate_reads_the_option_answer_from_the_profile(tmp_path, capsys, answer, owed):
    (tmp_path / 'documents').mkdir()
    (tmp_path / 'parse_ledger.json').write_text(json.dumps({'records': []}))
    (tmp_path / 'result.json').write_text(json.dumps({'meta': {'tax_year': 2025}, 'crypto_summary': COMPLETE}))
    (tmp_path / 'taxpayer_profile.json').write_text(json.dumps(
        {'flags': {'has_crypto': False, 'has_crypto_specific_identification': False,
                   'has_digital_asset_option_exercise': answer}}))
    assert tie_out._main(['--workspace', str(tmp_path)]) == (2 if owed else 0)
    captured = capsys.readouterr()
    assert captured.err == ''
    assert ('has_digital_asset_option_exercise is unanswered' in captured.out) is owed


def _ledgered_1099da(form_type='1099-DA', skipped=None):
    row = dict(doc_id='da', file='documents/da.pdf', form_type=form_type, payer='Exchange',
        person='primary', key_boxes={} if skipped else {'capital_proceeds': 0}, skipped=skipped)
    if '1099-MISC' in form_type and not skipped:
        from tests.test_misc_income import source
        row['misc_income'] = source(box_3=0,box_3_business=0,box_3_other=0,
            box_4=0,box_5=0,box_6=0,box_7=False)
    return tie_out.load_ledger({'records':[row]})


@pytest.mark.parametrize('form_type', ['1099-DA', 'Form 1099-da', '1099DA', '1099-DA (Corrected)',
                                       'Substitute 1099-DA', 'Consolidated 1099 (1099-DA, 1099-MISC)'])
def test_a_ledgered_1099da_owes_the_option_answer_with_no_crypto_rows(form_type):
    """A 1099-DA whose proceeds were routed to the non-digital rows leaves no
    crypto summary or digital-asset row; the ledgered form is the data."""
    report = tie_out.evaluate(_ledgered_1099da(form_type), {}, [], specific_identification_answer=False)
    assert any('has_digital_asset_option_exercise is unanswered' in t for t in report.missing_result_targets)
    answered = tie_out.evaluate(_ledgered_1099da(form_type), {}, [], option_exercise_answer=False, specific_identification_answer=False)
    assert not any('has_digital_asset_option_exercise' in t for t in answered.missing_result_targets)


def test_a_skipped_1099da_or_another_form_owes_no_option_answer():
    for ledger in (_ledgered_1099da(skipped='duplicate of da2'), _ledgered_1099da('1099-B')):
        report = tie_out.evaluate(ledger, {}, [], specific_identification_answer=False)
        assert not any('has_digital_asset_option_exercise' in t for t in report.missing_result_targets)


def test_tie_out_refuses_a_basis_mode_nothing_can_complete():
    # --per-account output never routes a box, so 'isolated_account' is not a
    # mode a return can finish under.
    assert tie_out.evaluate([], {'crypto_summary': {**COMPLETE, 'basis_mode': 'isolated_account'}}, [], option_exercise_answer=False, specific_identification_answer=False).failed


def _employer_ledger(adjustment):
    return tie_out.load_ledger({'records': [dict(
        doc_id='supp', file='documents/supplemental.pdf', form_type='supplemental', payer='Broker',
        person='primary', key_boxes={'employer_stock_basis_adjustment': adjustment}, skipped=None)]})


def _rsu_and_crypto(**crypto_row):
    # An RSU code-B correction of (5,000) on the Box A row, and a 1099-DA
    # code-B correction of +300 on the digital-asset row for the same line 1b.
    rsu = dict(proceeds=20000, cost=15000, adjustment=-5000, adjustment_code='B', employer_stock=True, gain=0)
    crypto = dict(proceeds=600, cost=300, adjustment=300, adjustment_code='B', gain=600, **crypto_row)
    return {'crypto_summary': COMPLETE, 'schedule_d': {'part_i_short_term': {
        'line_1a_1099b_box_a': rsu, 'line_1b_digital_assets': crypto}}}


def test_a_crypto_correction_never_touches_the_employer_stock_tie():
    report = tie_out.evaluate(_employer_ledger(5000), _rsu_and_crypto(), ['documents/supplemental.pdf'], option_exercise_answer=False, specific_identification_answer=False)
    assert not report.failed, report
    check = next(c for c in report.sum_checks if c.category == 'employer_stock_basis_adjustment')
    assert check.result_total == 5000


def test_a_digital_asset_row_is_never_employer_stock():
    report = tie_out.evaluate(_employer_ledger(5000), _rsu_and_crypto(employer_stock=True),
                              ['documents/supplemental.pdf'], option_exercise_answer=False, specific_identification_answer=False)
    assert report.failed
    assert any('digital-asset row' in target for target in report.missing_result_targets)


def test_a_routing_entry_adds_key_for_key_into_its_template_row():
    # /tax-parse-crypto ADDs each routed figure into the row it names. A key
    # the row lacks (the Form 8949 row's `cost_basis` against the row's `cost`)
    # would leave that field at the template's 0 while `gain` still looked right.
    template = json.loads((REPO / 'agents' / 'templates' / 'result_template.json').read_text(encoding='utf-8'))
    r = resolved(run(reporting(form(basis_reported_to_irs=True, cost_basis=100, term='long'))))
    (entry,) = r['broker_reporting']['schedule_d_routing'].values()
    merged = set(entry) - {'part', 'row', 'schedule_d_line', 'rows'}
    assert merged == {*C._ROUTED_AMOUNTS, 'adjustment_code'}
    assert merged <= set(template['schedule_d'][entry['part']][entry['row']])


def test_every_box_routes_to_its_own_digital_asset_row_in_the_template():
    template = json.loads((REPO / 'agents' / 'templates' / 'result_template.json').read_text(encoding='utf-8'))
    for box, (part, row, line) in C._SCHEDULE_D_TARGETS.items():
        fields = template['schedule_d'][part][row]
        assert row == f'line_{line}_digital_assets', box
        assert 'employer_stock' not in fields and {'proceeds', 'cost', 'adjustment', 'adjustment_code',
                                                   'gain'} <= set(fields), box
    flagged = {row for part in ('part_i_short_term', 'part_ii_long_term')
               for row, fields in template['schedule_d'][part].items()
               if isinstance(fields, dict) and 'employer_stock' in fields}
    assert flagged and not flagged & {row for _, row, _ in C._SCHEDULE_D_TARGETS.values()}


@pytest.mark.parametrize('summary', [
    # What the importer wrote before AA41: no account or reporting keys at all.
    dict(disposals=1, needs_basis_count=0, ordinary_income=0, per_asset={'BTC': {}}, realized_capital_gain=50),
    dict(disposals='1')])
def test_a_summary_from_before_aa41_still_owes_the_reconciliation(summary):
    report = tie_out.evaluate([], {'crypto_summary': summary}, [], option_exercise_answer=False, specific_identification_answer=False)
    assert report.failed
    assert any('Form 8949 reporting' in target for target in report.missing_result_targets)


def test_merged_digital_asset_rows_without_a_summary_owe_the_reconciliation():
    result = {'schedule_d': {'part_ii_long_term': {'line_9_digital_assets': dict(proceeds=150, cost=100, gain=50)}}}
    report = tie_out.evaluate([], result, [], option_exercise_answer=False, specific_identification_answer=False)
    assert any('Form 8949 reporting' in target for target in report.missing_result_targets)


def test_the_template_scaffold_alone_is_not_crypto():
    template = json.loads((REPO / 'agents' / 'templates' / 'result_template.json').read_text(encoding='utf-8'))
    scaffold = {key: template[key] for key in ('crypto_summary', 'schedule_d')}
    assert not tie_out.evaluate([], scaffold, [], specific_identification_answer=False).failed


def _broker_ledger(proceeds):
    return tie_out.load_ledger({'records': [dict(
        doc_id='da', file='documents/1099DA.pdf', form_type='1099-DA', payer='Broker', person='primary',
        key_boxes={'capital_proceeds': proceeds}, skipped=None)]})


def _with_box_k(proceeds, above):
    return {'crypto_summary': {**COMPLETE, 'reporting_proceeds_above_forms': above},
            'schedule_d': {'part_ii_long_term': {'line_9_digital_assets': dict(proceeds=proceeds, cost=100, gain=50)}}}


def test_proceeds_above_their_form_tie_to_the_form_and_say_so():
    report = tie_out.evaluate(_broker_ledger(140), _with_box_k(150, 10), ['documents/1099DA.pdf'], option_exercise_answer=False, specific_identification_answer=False)
    assert not report.failed
    assert any('exceed their broker forms by 10.00' in note for note in report.advisories)
    # Without the documented excess the form's own figure no longer ties.
    assert tie_out.evaluate(_broker_ledger(140), _with_box_k(150, 0), ['documents/1099DA.pdf'], option_exercise_answer=False, specific_identification_answer=False).failed


@pytest.mark.parametrize('row', ['line_10_digital_assets', 'line_10_1099b_box_f'])
def test_a_no_form_row_cannot_turn_a_dropped_form_into_a_warning(row):
    # The 1099-DA's 1,500 is ledgered; the sale went to Box L at 150 after a
    # wrong "no form" answer. Every other row is zero: the tie must FAIL, not
    # degrade to unverifiable. Line 10's rows hold no broker-form proceeds.
    result = {'crypto_summary': COMPLETE,
              'schedule_d': {'part_ii_long_term': {row: dict(proceeds=150, cost=100, gain=50)}}}
    report = tie_out.evaluate(_broker_ledger(1500), result, ['documents/1099DA.pdf'], option_exercise_answer=False, specific_identification_answer=False)
    assert report.failed and 'capital_proceeds' not in report.unverifiable
    check = next(c for c in report.sum_checks if c.category == 'capital_proceeds')
    assert (check.ledger_total, check.result_total) == (1500, 0)


def test_an_excess_alone_is_crypto_metadata_and_meets_the_gate():
    # A duplicated 500 lot on line 1a must not hide behind a bare excess key.
    result = {'crypto_summary': dict(reporting_proceeds_above_forms=500),
              'schedule_d': {'part_i_short_term': {'line_1a_1099b_box_a': dict(proceeds=10500, cost=0, gain=0)}}}
    report = tie_out.evaluate(_broker_ledger(10000), result, ['documents/1099DA.pdf'], option_exercise_answer=False, specific_identification_answer=False)
    assert report.failed
    assert any('Form 8949 reporting' in target for target in report.missing_result_targets)


def test_a_claimed_excess_can_only_lower_the_result_side():
    # Rows never merged: the result side is short of the form, and an
    # excess makes it shorter still — it cannot hide the dropped form.
    assert tie_out.evaluate(_broker_ledger(150), _with_box_k(0.01, 10), ['documents/1099DA.pdf'], option_exercise_answer=False, specific_identification_answer=False).failed


@pytest.mark.parametrize('above', [-1, float('nan'), float('inf'), True, '10'])
def test_excess_must_be_a_finite_non_negative_amount(above):
    with pytest.raises(tie_out.TieOutError):
        tie_out.evaluate(_broker_ledger(140), _with_box_k(150, above), ['documents/1099DA.pdf'], option_exercise_answer=False, specific_identification_answer=False)


def test_every_reviewed_year_and_source_is_documented():
    doc = (REPO / 'docs' / 'contributing' / 'crypto-accounts.md').read_text(encoding='utf-8')
    # Every routing row, both ways: a row for a year the engine refuses, or a
    # second row for one it routes, tells a reader what the engine does not do,
    # and checking only the engine's years finds neither.
    rows = {}
    for line in doc.splitlines():
        # Any spelling of the year cell: a padded or bold row renders as a row.
        if match := re.match(r'\s*\|[\s*_]*TY(\d{4})\b', line):
            assert int(match[1]) not in rows, f'TY{match[1]} has two routing rows'
            rows[int(match[1])] = line
    assert sorted(rows) == sorted(C._REPORTING_REGIMES), sorted(rows)
    for year, regime in C._REPORTING_REGIMES.items():
        # The year's own routing row, citing its source: the doc names years
        # and URLs in prose too, so finding either anywhere proves no row.
        row = (f"| TY{year} | {regime['form_type']} | {' / '.join(regime['short'])} | "
               f"{' / '.join(regime['long'])} | ")
        assert rows[year].startswith(row) and regime['source'] in rows[year], year
        # A year-pinned copy: the current-year page becomes next year's text.
        assert regime['source'].startswith('https://www.irs.gov/pub/irs-prior/') and str(year) in regime['source']
        assert set(regime['short'] + regime['long']) <= set(C._SCHEDULE_D_TARGETS)
    assert C._REPORTING_VERIFIED in doc


# Every box on Form 1099-DA, per reviewed year: the numbered boxes headed on
# pages 7-10 of the year's instructions, then the unnumbered fields of the
# recipient's Copy B. TY2026's form has the same boxes. A year re-read at
# season open joins here with its own set.
_1099DA_FORM_BOXES = {'1a', '1b', '1c', '1d', '1e', '1f', '1g', '1h', '1i', '2', '3a', '3b',
                      '4', '5', '6', '7', '8', '9', '10', '11a', '11b', '11c', '12a', '12b',
                      '13', '14', '15', '16', 'CORRECTED', 'Filer, recipient', 'Account number',
                      'CUSIP', 'Applicable checkbox', 'Reserved'}
_1099DA_BOXES = {2025: _1099DA_FORM_BOXES, 2026: _1099DA_FORM_BOXES}
# The box each key copies or refuses. The table is the only place this
# pairing is written, and checking each column as a set passes a swap.
_1099DA_KEY_BOXES = {'asset': '1b', 'quantity': '1c', 'date_sold': '1e', 'proceeds': '1f',
                     'cost_basis': '1g', 'basis_reported_to_irs': '2', 'term': '6', 'ordinary': '6',
                     'accrued_market_discount': '1h', 'wash_sale_loss_disallowed': '1i',
                     'loss_not_allowed': '5', 'net_proceeds': '3a', 'qof': '3b',
                     'customer_acquisition_info': '8',
                     'optional_method': '11a', 'primary_sale_proceeds': '11c', 'cusip': 'CUSIP',
                     'federal_withholding_1099': '4', 'state_withholding_nc': '16'}
# The boxes /tax-parse records in the parse ledger rather than the form entry.
_1099DA_LEDGER_KEYS = {'federal_withholding_1099', 'state_withholding_nc'}


def test_the_reviewed_1099b_table_accounts_for_both_years_entire_form():
    doc = (REPO / 'docs/contributing/crypto-accounts.md').read_text(encoding='utf-8')
    section = doc.split('### Reviewed Form 1099-B inputs (AA61)')[1].split('### Reviewed Form 1099-DA')[0]
    header, rule, *rows = [[cell.strip() for cell in line.strip().strip('|').split('|')]
                          for line in section.splitlines() if line.startswith('|')]
    assert header[1:3] == ['TY2023 box', 'TY2024 box']
    assert set(''.join(rule)) <= set('-: ') and all(len(row) == 4 for row in rows)
    for source in ('f1099b', 'i1099b'):
        assert set(re.findall(rf'/pub/irs-prior/{source}--(\d{{4}})\.pdf', section)) == {'2023', '2024'}
    expected = dict(asset='1a', quantity='1a', date_sold='1c', proceeds='1d', cost_basis='1e',
                    accrued_market_discount='1f', wash_sale_loss_disallowed='1g',
                    term='2', ordinary='2', collectibles='3', qof='3', net_proceeds='6',
                    loss_not_allowed='7', contract_profit_loss='8', prior_unrealized_profit_loss='9',
                    current_unrealized_profit_loss='10', aggregate_contract_profit_loss='11',
                    basis_reported_to_irs='12', bartering='13', cusip='CUSIP',
                    federal_withholding_1099='4', state_withholding_nc='16')
    keyed = [row for row in rows if row[0] != '—']
    keys = [row[0].split()[0].strip('`') for row in keyed]
    identity = {'form_id', 'form_type', 'account_id', 'txn_id'}
    ledger = {'federal_withholding_1099', 'state_withholding_nc'}
    assert len(keys) == len(set(keys))
    assert set(keys) == set(expected) == ((C._FORM_FIELDS - identity) | C._FORM_UNSUPPORTED
                                         | C._FORM_UNSUPPORTED_BY_TYPE['1099-B'] | ledger)
    boxes = ({f'1{letter}' for letter in 'abcdefg'} | {str(n) for n in range(2, 17)}
             | {'CUSIP', 'Applicable checkbox', 'CORRECTED', 'Account number', 'FATCA',
                'Parties', '2nd TIN not.', 'VOID'})
    for column in (1, 2):
        assert dict(zip(keys, (row[column] for row in keyed))) == expected
        paired = list(expected.values())
        assert sorted(row[column] for row in rows) == sorted([*boxes - set(paired), *paired])
    skill = skill_text('tax-parse').split('### 1099-B Extraction')[1].split('### 1099-DA')[0]
    (listed,) = re.findall(r'For TY2023-2024 crypto, also capture boxes ([^\n]+), keeping', skill)
    assert set(expected.values()) <= listed_boxes(listed)
    crypto = skill_text('tax-parse-crypto')
    assert '1099-B summary that omits' in crypto and 're-read them' in crypto


def test_the_reviewed_1099da_table_names_every_box_and_key():
    """AA55's review began from the importer's keys, so it could confirm only
    boxes the importer already knew. A row per box, held to the form's own
    list, is what makes "every box was re-read" a claim a test can check."""
    doc = (REPO / 'docs' / 'contributing' / 'crypto-accounts.md').read_text(encoding='utf-8')
    section = doc[doc.index('### Reviewed Form 1099-DA inputs (AA55)'):doc.index('\n## Primary authorities')]
    header, rule, *rows = [[cell.strip() for cell in line.strip().strip('|').split('|')]
                           for line in section.splitlines() if line.lstrip().startswith('|')]
    assert set(''.join(rule)) <= set('-: ') and all(len(row) == len(header) for row in rows), header
    # The columns between key and meaning are the reviewed years, and each
    # cites its own year's instructions and form: a relabelled or added
    # column fails here unless its year was re-read.
    assert header[0] == 'Input key' and header[-1].startswith('Reviewed meaning'), header
    years = [int(re.fullmatch(r'TY(\d{4}) box', cell)[1]) for cell in header[1:-1]]
    assert years == sorted(_1099DA_BOXES), header
    for source in ('i1099da', 'f1099da'):
        cited = re.findall(rf'/pub/irs-prior/{source}--(\d{{4}})\.pdf', section)
        assert sorted(set(map(int, cited))) == years, source
    assert {year for year, regime in C._REPORTING_REGIMES.items() if regime['form_type'] == '1099-DA'} <= set(years)
    # Both ways against the importer: each key it reads or refuses on a
    # 1099-DA has its row, and no row names a key it lacks. The four identity
    # keys copy no box.
    keyed = [row for row in rows if row[0] != '—']
    keys = [row[0].split()[0].strip('`') for row in keyed]
    identity = {'form_id', 'form_type', 'account_id', 'txn_id'}
    assert len(keys) == len(set(keys)), keys
    assert set(keys) == set(_1099DA_KEY_BOXES) == ((C._FORM_FIELDS - identity) | C._FORM_UNSUPPORTED
                                                  | C._FORM_UNSUPPORTED_BY_TYPE['1099-DA']
                                                  | C._FORM_FIELDS_BY_TYPE['1099-DA'] | _1099DA_LEDGER_KEYS)
    # Every category a parse-ledger row names, in its key or in the meaning a
    # preparer follows, is one the gate reads: tied, or refused outright.
    ledger_rows = [row for row in keyed if '(parse ledger only)' in row[0]]
    assert {row[0].split()[0].strip('`') for row in ledger_rows} == _1099DA_LEDGER_KEYS
    from agents.lib.parse_records import LEDGER_FIELDS
    known = set(tie_out.CATEGORY_TARGETS) | set(tie_out._BLOCKING_CATEGORIES) | tie_out._INFORMATIONAL | LEDGER_FIELDS
    for name in re.findall(r'`(\w+)`', ' '.join(' '.join(row) for row in ledger_rows)):
        assert name in known, name
    # Box 16 names every supported state route plus the external-state
    # fallback and its mandatory jurisdiction breakdown. CA uses its own
    # nonwage component; its W-2-only category must not absorb a 1099.
    (box_16,) = [row for row in ledger_rows if row[1] == '16']
    assert set(re.findall(r'`(\w+)`', ' '.join(box_16))) == {
        'ca_1099_withholding', 'state_withholding_ny', 'state_withholding_nc',
        'other_state_withholding', 'other_state_withholding_by_state'}
    assert 'unrouted_state_withholding' in tie_out._BLOCKING_CATEGORIES
    assert 'other_state_withholding' in tie_out._INFORMATIONAL
    # Box 11c's proceeds stay out of 1f, so the ledger blocks them too: a form
    # that never reaches broker_reporting adds nothing to capital_proceeds.
    (box_11c,) = [row for row in keyed if row[1] == '11c']
    assert '`nft_first_sale_proceeds`' in box_11c[-1]
    assert 'nft_first_sale_proceeds' in tie_out._BLOCKING_CATEGORIES
    for column, year in enumerate(years, start=1):
        assert {key: row[column] for key, row in zip(keys, keyed)} == _1099DA_KEY_BOXES, year
        # Every box once, and a box two keys share once per key.
        paired = list(_1099DA_KEY_BOXES.values())
        expected = sorted([*_1099DA_BOXES[year] - set(paired), *paired])
        assert sorted(row[column] for row in rows if row[column] != '—') == expected, year


def test_tax_parse_captures_every_1099da_box_a_key_or_category_reads():
    """A key the parse never captured has nothing to copy from, and a box
    copied from nothing reads as blank: AA59 began with every new key in
    that state."""
    skill = skill_text('tax-parse')
    listed = skill[skill.index('### 1099-DA Extraction'):].splitlines()[1]
    assert set(_1099DA_KEY_BOXES.values()) <= listed_boxes(listed.split(':', 1)[1]), listed


def test_tax_parse_crypto_names_every_box_a_1099da_only_key_reads():
    """An older summary never read these boxes, so the skill sends them back
    to the form; a key added without its box here is copied as blank from a
    summary that never saw it."""
    skill = (REPO / '.agents' / 'skills' / 'tax-parse-crypto' / 'SKILL.md').read_text(encoding='utf-8')
    (listed,) = re.findall(r'does\s+not show boxes ([^:]+) never read them', skill)
    assert listed_boxes(listed) == {_1099DA_KEY_BOXES[key] for key in
                                   C._FORM_UNSUPPORTED_BY_TYPE['1099-DA'] | C._FORM_FIELDS_BY_TYPE['1099-DA']}, listed


def test_the_documented_example_form_carries_exactly_the_1099da_keys():
    doc = (REPO / 'docs' / 'contributing' / 'crypto-accounts.md').read_text(encoding='utf-8')
    start = doc.index('"broker_reporting": {')
    example = json.loads('{' + doc[start:doc.index('```', start)] + '}')
    (entry,) = example['broker_reporting']['forms']
    assert entry['form_type'] == '1099-DA'
    assert set(entry) == (C._FORM_FIELDS | C._FORM_UNSUPPORTED | C._FORM_UNSUPPORTED_BY_TYPE['1099-DA']
                         | C._FORM_FIELDS_BY_TYPE['1099-DA'])
