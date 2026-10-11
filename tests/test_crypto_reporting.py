"""AA41: Form 8949 routing reconciled with broker forms, on synthetic events.

Every figure is worked by hand from the Form 8949 instructions: column (h) =
(d) - (e) + (g); a basis-reported form keeps its basis in (e) and corrects it
in (g) (Worksheet for Basis Adjustments: line 1 reported, line 2 correct); a
basis-not-reported form takes the correct basis in (e) with -0- in (g).
"""
import copy
import json
from pathlib import Path

import pytest

from agents.lib import crypto_importer as C
from agents.lib import tie_out

REPO = Path(__file__).resolve().parent.parent


def event(account, txid, kind, date, qty=1, usd=0, **extra):
    return dict(account_id=account, txn_id=txid, kind=kind, date=date,
                asset='BTC', quantity=qty, usd_amount=usd, **extra)


# A: 1 BTC bought for $100 on 2023-01-01, sold for $150 on 2025-03-01 (long, gain $50).
BASE = [event('A', 'buy', 'buy', '2023-01-01', usd=100),
        event('A', 'sale', 'sell', '2025-03-01', usd=150)]


def form(**extra):
    fields = dict(form_id='da-1', form_type='1099-DA', account_id='A', txn_id='sale',
                  asset='BTC', quantity='1', date_sold='2025-03-01', proceeds=150,
                  basis_reported_to_irs=False, ordinary=False, accrued_market_discount=None,
                  wash_sale_loss_disallowed=None, loss_not_allowed=False)
    return {**fields, **extra}


def reporting(*forms, received=True, resolutions=None, **account):
    return dict(accounts={'A': dict(forms_received=received, evidence='broker-statement-A', **account)},
                forms=list(forms), resolutions=resolutions or {})


def run(reporting_block, tx=BASE, year=2025):
    return C.build_crypto_import(copy.deepcopy(tx), tax_year=year, broker_reporting=reporting_block)


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
    assert (routing['row'], routing['schedule_d_line']) == ('line_10_1099b_box_f', line)
    assert (routing['proceeds'], routing['cost_basis'], routing['adjustment'], routing['gain']) == (150, 100, 0, 50)
    assert r['broker_reporting']['not_reported'] == [
        dict(account_id='A', txn_id='sale', evidence='broker-statement-A')]


def test_short_term_digital_asset_without_a_form_is_box_i_never_c():
    tx = [event('A', 'buy', 'buy', '2025-01-01', usd=100), BASE[1]]
    r = resolved(run(reporting(received=False), tx))
    assert only(r)['box'] == 'I'
    assert r['broker_reporting']['schedule_d_routing']['I']['row'] == 'line_1c_1099b_box_c'


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
        part='part_ii_long_term', row='line_8a_1099b_box_d', schedule_d_line='8b',
        proceeds=150, cost_basis=100, adjustment=0, gain=50, rows=1)


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
    blocked(run(block, SPLIT), 'spans both holding periods')
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
    blocked(run(reporting(*forms), SPLIT), fragment)


@pytest.mark.parametrize('field,value,fragment', [
    ('asset', 'ETH', 'asset differs'), ('date_sold', '2025-03-02', 'date sold differs'),
    ('quantity', '0.5', 'units differ')])
def test_a_form_describing_another_sale_is_unresolved(field, value, fragment):
    blocked(run(reporting(form(**{field: value}))), fragment)


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
    blocked(C.build_crypto_import(copy.deepcopy(BASE)), 'needs the filing year')


@pytest.mark.parametrize('per_account', [False, True])
def test_legacy_pools_cannot_route_a_box(per_account):
    tx = [dict(date='2023-01-01', kind='buy', asset='BTC', quantity=1, usd_amount=100),
          dict(date='2024-03-01', kind='sell', asset='BTC', quantity=1, usd_amount=150)]
    r = C.build_crypto_import(tx, tax_year=2024, per_account=per_account)
    blocked(r, 'account-aware import')
    with pytest.raises(C.CryptoImportError, match='account-aware'):
        C.build_crypto_import(tx, tax_year=2024, per_account=per_account, broker_reporting=reporting())


def mutate(change):
    block = reporting(form())
    change(block)
    return block


@pytest.mark.parametrize('change', [
    lambda b: b.update(extra=1),
    lambda b: b.update(forms={}),
    lambda b: b['forms'][0].update(federal_withholding=0),
    lambda b: b['forms'][0].pop('proceeds'),
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
])
def test_malformed_or_contradictory_evidence_is_refused(change):
    with pytest.raises(C.CryptoImportError):
        run(mutate(change))


@pytest.mark.parametrize('year', [2026, None])
def test_evidence_for_an_unreviewed_or_unknown_year_is_refused(year):
    with pytest.raises(C.CryptoImportError):
        run(reporting(form()), year=year)


def test_reconciled_rows_keep_input_order_and_identity():
    tx = [*BASE, event('A', 'buy2', 'buy', '2025-01-05', usd=40), event('A', 'sale2', 'sell', '2025-04-01', usd=50)]
    block = reporting(form(), not_reported=['sale2'])
    r = resolved(run(block, tx))
    assert [(row['txn_id'], row['box']) for row in rows(r)] == [('sale2', 'I'), ('sale', 'K')]


def test_cli_exit_and_markdown_follow_reporting(tmp_path, capsys):
    path = tmp_path / 'synthetic.json'
    path.write_text(json.dumps(dict(transactions=BASE, broker_reporting=reporting(form()))))
    assert C._cli_compute([str(path), '--account-aware', '--year', '2025', '--markdown']) == 0
    out = capsys.readouterr().out
    assert 'BLOCKED' not in out and '| K | 9 | $150.00 | $100.00 | $0.00 | $50.00 | 1 |' in out
    path.write_text(json.dumps(dict(transactions=BASE, broker_reporting=reporting(form(proceeds=140)))))
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
    report = tie_out.evaluate([], {'crypto_summary': {**COMPLETE, **change}}, [])
    assert report.failed
    assert any('Form 8949 reporting' in target for target in report.missing_result_targets)


def test_tie_out_reporting_gate_survives_dropped_keys():
    for dropped in ('reporting_complete', 'reporting_unresolved'):
        summary = {key: value for key, value in COMPLETE.items() if key != dropped}
        assert tie_out.evaluate([], {'crypto_summary': summary}, []).failed
    summary = dict(reporting_complete=False)
    assert tie_out.evaluate([], {'crypto_summary': summary}, []).failed
    rows_only = {'form_8949': {'part_ii_long_term': [{'account_id': 'A'}]},
                 'crypto_summary': dict(basis_mode='account_fifo', complete=True, unresolved_inputs=[])}
    assert tie_out.evaluate([], rows_only, []).failed


def test_tie_out_accepts_reconciled_reporting():
    assert not tie_out.evaluate([], {'crypto_summary': COMPLETE}, []).failed


def _broker_ledger(proceeds):
    return tie_out.load_ledger({'records': [dict(
        doc_id='da', file='documents/1099DA.pdf', form_type='1099-DA', payer='Broker', person='primary',
        key_boxes={'capital_proceeds': proceeds}, skipped=None)]})


def _with_box_k(proceeds, above):
    return {'crypto_summary': {**COMPLETE, 'reporting_proceeds_above_forms': above},
            'schedule_d': {'part_ii_long_term': {'line_9_1099b_box_e': dict(proceeds=proceeds, cost=100, gain=50)}}}


def test_proceeds_above_their_form_tie_to_the_form_and_say_so():
    report = tie_out.evaluate(_broker_ledger(140), _with_box_k(150, 10), ['documents/1099DA.pdf'])
    assert not report.failed
    assert any('exceed their broker forms by 10.00' in note for note in report.advisories)
    # Without the documented excess the form's own figure no longer ties.
    assert tie_out.evaluate(_broker_ledger(140), _with_box_k(150, 0), ['documents/1099DA.pdf']).failed


def test_a_no_form_row_cannot_turn_a_dropped_form_into_a_warning():
    # The 1099-DA's 1,500 is ledgered; the sale went to Box L (the F row) at 150
    # after a wrong "no form" answer. Every other row is zero: the tie must FAIL,
    # not degrade to unverifiable.
    result = {'crypto_summary': COMPLETE,
              'schedule_d': {'part_ii_long_term': {'line_10_1099b_box_f': dict(proceeds=150, cost=100, gain=50)}}}
    report = tie_out.evaluate(_broker_ledger(1500), result, ['documents/1099DA.pdf'])
    assert report.failed and 'capital_proceeds' not in report.unverifiable
    check = next(c for c in report.sum_checks if c.category == 'capital_proceeds')
    assert (check.ledger_total, check.result_total) == (1500, 0)


def test_an_excess_alone_is_crypto_metadata_and_meets_the_gate():
    # A duplicated 500 lot on line 1a must not hide behind a bare excess key.
    result = {'crypto_summary': dict(reporting_proceeds_above_forms=500),
              'schedule_d': {'part_i_short_term': {'line_1a_1099b_box_a': dict(proceeds=10500, cost=0, gain=0)}}}
    report = tie_out.evaluate(_broker_ledger(10000), result, ['documents/1099DA.pdf'])
    assert report.failed
    assert any('Form 8949 reporting' in target for target in report.missing_result_targets)


def test_a_claimed_excess_can_only_lower_the_result_side():
    # Rows never merged: the result side is short of the form, and an
    # excess makes it shorter still — it cannot hide the dropped form.
    assert tie_out.evaluate(_broker_ledger(150), _with_box_k(0.01, 10), ['documents/1099DA.pdf']).failed


@pytest.mark.parametrize('above', [-1, float('nan'), float('inf'), True, '10'])
def test_excess_must_be_a_finite_non_negative_amount(above):
    with pytest.raises(tie_out.TieOutError):
        tie_out.evaluate(_broker_ledger(140), _with_box_k(150, above), ['documents/1099DA.pdf'])


def test_every_reviewed_year_and_source_is_documented():
    doc = (REPO / 'docs' / 'contributing' / 'crypto-accounts.md').read_text(encoding='utf-8')
    for year, regime in C._REPORTING_REGIMES.items():
        assert f'TY{year}' in doc and regime['source'] in doc, year
        assert set(regime['short'] + regime['long']) <= set(C._SCHEDULE_D_TARGETS)
    assert C._REPORTING_VERIFIED in doc
