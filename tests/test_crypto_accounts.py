"""AA40: account inventory and transfer chain of custody, with synthetic events."""
import copy
import json
from fractions import Fraction
from pathlib import Path

import pytest

from agents.lib import crypto_importer as C
from agents.lib import tie_out


def event(account, txid, kind, date, qty=1, usd=0, **extra):
    return dict(account_id=account, txn_id=txid, kind=kind, date=date,
                asset='BTC', quantity=qty, usd_amount=usd, **extra)


def transfer(source='A', dest='B', qty=1, tid='T', date='2025-02-01'):
    return [event(source, f'{tid}-out', 'send', date, qty, transfer_id=tid),
            event(dest, f'{tid}-in', 'receive', date, qty, transfer_id=tid)]


def evidence(*ids):
    return {tid: dict(ownership_confirmed=True, evidence=f'ownership-workpaper-{tid}') for tid in ids}


def rows(report):
    return report['form_8949']['part_i_short_term'] + report['form_8949']['part_ii_long_term']


def rational(record):
    return Fraction(int(record['numerator']), int(record['denominator']))


def test_unrelated_account_cannot_supply_basis():
    r = C.build_crypto_import([event('A', 'buy', 'buy', '2024-01-01', usd=100),
                               event('B', 'sell', 'sell', '2025-03-01', usd=200)], tax_year=2025)
    assert not r['account_fifo']['complete']
    assert rows(r)[0]['needs_basis']
    assert rows(r)[0]['cost_basis'] == 0
    assert r['account_fifo']['inventory']['A']['BTC'][0]['basis_usd'] == 100
    assert 'BLOCKED' in C.render_markdown(r).splitlines()[0]


def test_partial_transfer_moves_two_original_lots_then_destination_fifo():
    # A: 2 BTC/$200 old + 2 BTC/$600 new. Move 3: basis $200+$300.
    # B's existing 1 BTC/$400 is newer; sell 2.5 -> old2 + transferred0.5.
    tx = [event('A', 'old', 'buy', '2023-01-01', 2, 200),
          event('A', 'new', 'buy', '2024-12-01', 2, 600),
          event('B', 'existing', 'buy', '2025-01-01', 1, 400),
          *transfer(qty=3), event('B', 'sell', 'sell', '2025-03-01', '2.5', 1000),
          event('A', 'source-sale', 'sell', '2025-03-02', 1, 350)]
    r = C.build_crypto_import(tx, transfer_links=evidence('T'), tax_year=2025)
    assert r['account_fifo']['complete']
    sold = {row['origin_txn_id']: row for row in rows(r) if row['txn_id'] == 'sell'}
    assert sold['old']['cost_basis'] == 200
    assert sold['old']['date_acquired'] == '2023-01-01'
    assert sold['old']['box'] == 'F'
    assert sold['new']['cost_basis'] == 150
    assert sold['new']['box'] == 'C'
    assert next(row for row in rows(r) if row['txn_id'] == 'source-sale')['cost_basis'] == 300
    inventory = r['account_fifo']['inventory']['B']['BTC']
    assert [lot['quantity'] for lot in inventory] == ['0.5', '1']
    assert sum(rational(lot['basis_exact']) for lot in inventory) == 550
    ledger = r['account_fifo']['transfer_ledger'][0]
    assert ledger['from_txn_id'] == 'T-out' and ledger['to_txn_id'] == 'T-in'
    assert sum(rational(lot['basis_exact']) for lot in ledger['slices']) == 500


def test_transfer_chain_roundtrip_does_not_duplicate_basis():
    tx = [event('A', 'buy', 'buy', '2023-01-01', 3, 100),
          *transfer('A', 'B', 2, 'T1', '2025-02-01'),
          *transfer('B', 'C', 1, 'T2', '2025-02-02'),
          *transfer('C', 'A', 1, 'T3', '2025-02-03'),
          event('A', 'sale', 'sell', '2025-03-01', 2, 200)]
    r = C.build_crypto_import(tx, transfer_links=evidence('T1', 'T2', 'T3'))
    assert r['account_fifo']['complete']
    assert len(rows(r)) == 2
    assert sum(row['proceeds'] for row in rows(r)) == 200
    # Exact remaining basis is 100/3 in B; A and C have no remaining coins.
    inv = r['account_fifo']['inventory']
    assert inv['A']['BTC'] == inv['C']['BTC'] == []
    assert rational(inv['B']['BTC'][0]['basis_exact']) == Fraction(100, 3)
    assert all(row['date_acquired'] == '2023-01-01' for row in rows(r))


def test_transfer_arrival_cannot_borrow_future_source_buy():
    tx = [*transfer(), event('A', 'later', 'buy', '2025-03-01', usd=100),
          event('B', 'sale', 'sell', '2025-04-01', usd=200)]
    r = C.build_crypto_import(tx, transfer_links=evidence('T'))
    assert not r['account_fifo']['complete']
    assert rows(r)[0]['needs_basis']
    assert r['account_fifo']['inventory']['A']['BTC'][0]['quantity'] == '1'


def test_same_time_destination_export_first_still_preserves_transfer_causality():
    send, receive = transfer()
    tx = [event('A', 'buy', 'buy', '2024-01-01', usd=100), receive,
          event('B', 'sale', 'sell', '2025-02-01', usd=200), send]
    r = C.build_crypto_import(tx, transfer_links=evidence('T'))
    assert r['account_fifo']['complete']
    assert rows(r)[0]['cost_basis'] == 100


def test_same_time_sequence_conflict_is_refused():
    send, receive = transfer()
    send['sequence'], receive['sequence'] = 2, 1
    with pytest.raises(C.CryptoImportError, match='Conflicting'):
        C.build_crypto_import([send, receive], transfer_links=evidence('T'))


@pytest.mark.parametrize('sequence', [True, -1, 1.5, '1'])
def test_sequence_domain(sequence):
    send, receive = transfer()
    send['sequence'], receive['sequence'] = sequence, 2
    with pytest.raises(C.CryptoImportError, match='sequence'):
        C.build_crypto_import([send, receive], transfer_links=evidence('T'))


def test_offsets_and_utc_export_timestamps_are_ordered_in_utc():
    tx = [event('A', 'sell', 'sell', '2025-01-01T10:00:00+02:00', usd=200),
          event('A', 'buy', 'buy', '2025-01-01 07:30:00 UTC', usd=100)]
    r = C.build_crypto_import(tx)
    assert r['account_fifo']['complete']
    assert rows(r)[0]['cost_basis'] == 100


def test_tiny_units_and_partial_basis_remain_exact():
    tx = [event('A', 'buy', 'buy', '2023-01-01', '0.000000000000000003', 90),
          *transfer(qty='0.000000000000000001'),
          event('B', 'sale', 'sell', '2025-03-01', '0.000000000000000001', 50)]
    r = C.build_crypto_import(tx, transfer_links=evidence('T'))
    assert r['account_fifo']['complete']
    assert rows(r)[0]['description'] == '0.000000000000000001 BTC'
    assert rows(r)[0]['cost_basis'] == 30
    assert r['account_fifo']['inventory']['A']['BTC'][0]['quantity'] == '0.000000000000000002'


def test_external_owned_receive_requires_original_basis_and_date_even_without_sale():
    tx = [event('B', 'in', 'receive', '2025-01-01', basis_usd=100,
                acquired='2023-01-01', external_transfer_confirmed=True)]
    r = C.build_crypto_import(tx)
    assert r['account_fifo']['complete']
    assert r['digital_asset_question'] == 'No'
    for field in ['basis_usd', 'acquired', 'external_transfer_confirmed']:
        bad = copy.deepcopy(tx)
        del bad[0][field]
        assert not C.build_crypto_import(bad)['account_fifo']['complete']


@pytest.mark.parametrize('kind', ['send', 'receive'])
def test_unmatched_transfer_blocks_even_when_no_disposals(kind):
    tx = [event('A', 'buy', 'buy', '2023-01-01', usd=100),
          event('A', 'leg', kind, '2025-02-01', transfer_id='T')]
    r = C.build_crypto_import(tx, transfer_links=evidence('T'))
    assert not r['account_fifo']['complete']
    assert r['account_fifo']['unresolved_transfers']


def test_paired_transfer_without_ownership_evidence_is_unresolved():
    tx = [event('A', 'buy', 'buy', '2023-01-01', usd=100), *transfer()]
    r = C.build_crypto_import(tx)
    assert not r['account_fifo']['complete']
    assert r['account_fifo']['inventory']['B']['BTC'][0]['basis_known'] is False


@pytest.mark.parametrize('field,value', [('quantity', '0.9'), ('asset', 'ETH'),
                                        ('account_id', 'A'), ('basis_usd', 100),
                                        ('acquired', '2023-01-01'), ('fee_usd', 1),
                                        ('date', '2025-01-01'), ('external_transfer_confirmed', True)])
def test_transfer_mismatches_are_errors(field, value):
    tx = transfer()
    tx[1][field] = value
    with pytest.raises(C.CryptoImportError):
        C.build_crypto_import(tx, transfer_links=evidence('T'))


@pytest.mark.parametrize('bad', [{}, {'ownership_confirmed': False, 'evidence': 'review'},
                               {'ownership_confirmed': 1, 'evidence': 'review'},
                               {'ownership_confirmed': True, 'evidence': ''},
                               {'ownership_confirmed': True, 'evidence': 'review', 'extra': 1}])
def test_evidence_requires_explicit_owned_confirmation_and_reference(bad):
    with pytest.raises(C.CryptoImportError):
        C.build_crypto_import(transfer(), transfer_links={'T': bad})


def test_external_send_consumes_inventory_without_form_row():
    tx = [event('A', 'buy', 'buy', '2023-01-01', 2, 200),
          event('A', 'out', 'send', '2025-02-01', external_transfer_confirmed=True)]
    r = C.build_crypto_import(tx)
    assert r['account_fifo']['complete']
    assert not rows(r)
    assert r['account_fifo']['inventory']['A']['BTC'][0]['basis_usd'] == 100


@pytest.mark.parametrize('field,value', [('quantity', 0), ('quantity', True),
                                        ('quantity', float('nan')), ('quantity', float('inf')),
                                        ('fee_usd', False), ('account_id', ''), ('account_id', ' A '),
                                        ('txn_id', None), ('external_transfer_confirmed', 1),
                                        ('date', '2025-01-01 garbage')])
def test_account_boundaries_are_strict(field, value):
    tx = [event('A', 'buy', 'buy', '2023-01-01', usd=100)]
    tx[0][field] = value
    with pytest.raises(C.CryptoImportError):
        C.build_crypto_import(tx)


def test_duplicate_mixed_and_universal_override_are_refused():
    buy = event('A', 'buy', 'buy', '2024-01-01', usd=100)
    with pytest.raises(C.CryptoImportError, match='Duplicate'):
        C.build_crypto_import([buy, buy])
    untagged = dict(buy)
    del untagged['account_id']
    with pytest.raises(C.CryptoImportError, match='account_id'):
        C.build_crypto_import([buy, untagged])
    with pytest.raises(C.CryptoImportError, match='universal'):
        C.build_crypto_import([buy], account_aware=False)


def test_prior_year_disposal_consumes_basis_before_filing_year():
    tx = [event('A', 'buy', 'buy', '2023-01-01', 2, 200),
          event('A', 'old-sale', 'sell', '2024-01-01', usd=150),
          *transfer(), event('B', 'sale', 'sell', '2025-03-01', usd=300)]
    r = C.build_crypto_import(tx, tax_year=2025, transfer_links=evidence('T'))
    assert r['account_fifo']['complete']
    assert len(rows(r)) == 1
    assert r['schedule_d']['net_gain'] == 200


def test_income_and_trade_lots_stay_in_their_account():
    tx = [event('A', 'reward', 'income', '2025-01-01', usd=100, income_type='staking'),
          event('A', 'swap', 'trade', '2025-02-01', usd=150,
                received_asset='ETH', received_quantity=2),
          event('A', 'sale', 'sell', '2025-03-01', 2, 200)]
    tx[-1]['asset'] = 'ETH'
    r = C.build_crypto_import(tx)
    assert r['account_fifo']['complete']
    assert r['ordinary_income']['total'] == 100
    assert r['ordinary_income']['events'][0]['account_id'] == 'A'
    assert r['schedule_d']['net_gain'] == 100  # swap50 + ETHsale50


def test_universal_post_transition_is_diagnostic_but_isolated_account_supported():
    tx = [dict(date='2024-01-01', kind='buy', asset='BTC', quantity=1, usd_amount=100),
          dict(date='2025-03-01', kind='sell', asset='BTC', quantity=1, usd_amount=200)]
    assert C.build_crypto_import(tx, tax_year=2025)['crypto_summary']['complete'] is False
    assert 'complete' not in C.build_crypto_import(tx, tax_year=2025, per_account=True)['crypto_summary']


@pytest.mark.parametrize('summary', [dict(basis_mode='account_fifo', complete=False, unresolved_inputs=[]),
                                    dict(basis_mode='account_fifo', complete=True, unresolved_inputs=['missing']),
                                    dict(basis_mode='account_fifo', unresolved_inputs=[]),
                                    dict(basis_mode='legacy_universal', complete=True, unresolved_inputs=[])])
def test_tie_out_cannot_complete_unresolved_crypto(summary):
    report = tie_out.evaluate([], {'crypto_summary': summary}, [])
    assert report.failed
    assert any('crypto account FIFO' in message for message in report.missing_result_targets)


def test_tie_out_accepts_complete_account_metadata():
    summary = dict(basis_mode='account_fifo', complete=True, unresolved_inputs=[])
    assert not tie_out.evaluate([], {'crypto_summary': summary}, []).failed


def test_cli_diagnostic_exit_and_adapter_account_identifiers(tmp_path, capsys):
    path = tmp_path / 'synthetic.json'
    path.write_text(json.dumps({'transactions': transfer()}))
    assert C._cli_compute([str(path), '--account-aware', '--year', '2025']) == 2
    assert not json.loads(capsys.readouterr().out)['account_fifo']['complete']
    assert C._cli_compute([str(path), '--markdown']) == 2
    assert 'BLOCKED' in capsys.readouterr().out
    fixture = Path(__file__).parent / 'fixtures' / 'crypto' / 'coinbase.csv'
    assert C._cli_adapt([str(fixture), '--exchange', 'coinbase', '--account-id', 'coinbase-primary',
                         '--assume-self-transfers']) == 0
    tagged = json.loads(capsys.readouterr().out)['transactions']
    assert all(t['account_id'] == 'coinbase-primary' and t['txn_id'] for t in tagged)
    assert len({t['txn_id'] for t in tagged}) == len(tagged)
    assert all(t['txn_id'].split(':')[0].isalpha() for t in tagged)
    assert all(C._account_id(t['txn_id'], 'txn_id') == t['txn_id'] for t in tagged)


def test_utc_ordering_does_not_shift_declared_tax_dates_or_year():
    tx = [event('A', 'buy', 'buy', '2023-12-31T23:30:00-05:00', usd=100),
          event('A', 'sell', 'sell', '2024-12-31T23:45:00-05:00', usd=200),
          event('A', 'income', 'income', '2024-12-31T23:50:00-05:00', usd=20)]
    r = C.build_crypto_import(tx, tax_year=2024)
    assert rows(r)[0]['date_acquired'] == '2023-12-31'
    assert rows(r)[0]['date_sold'] == '2024-12-31'
    assert rows(r)[0]['box'] == 'C'  # exactly one calendar year
    assert r['ordinary_income']['total'] == 20


def test_account_rows_without_completeness_metadata_cannot_pass_tie_out():
    result = {'form_8949': {'part_i_short_term': [{'account_id': 'A'}]}}
    assert tie_out.evaluate([], result, []).failed
    result['crypto_summary'] = {}
    assert tie_out.evaluate([], result, []).failed


@pytest.mark.parametrize('kind', ['send', 'receive'])
def test_transfer_fee_bool_is_not_silently_erased(kind):
    with pytest.raises(C.CryptoImportError):
        C.build_crypto_import([event('A', 'bad', kind, '2025-01-01', fee_usd=False)])


def test_transfer_evidence_with_no_legs_is_refused():
    with pytest.raises(C.CryptoImportError):
        C.build_crypto_import([], account_aware=True, transfer_links=evidence('T'))


def test_duplicate_transfer_legs_are_refused():
    tx = transfer()
    tx.append(event('C', 'extra', 'receive', '2025-02-01', transfer_id='T'))
    with pytest.raises(C.CryptoImportError):
        C.build_crypto_import(tx, transfer_links=evidence('T'))


def test_external_original_date_cannot_follow_receipt():
    with pytest.raises(C.CryptoImportError):
        C.build_crypto_import([event('A', 'in', 'receive', '2025-01-01', basis_usd=100,
                                     acquired='2025-02-01', external_transfer_confirmed=True)])


def test_missing_historical_basis_cannot_be_hidden_by_year_filter():
    tx = [event('A', 'old-sale', 'sell', '2024-01-01', usd=100),
          event('A', 'buy', 'buy', '2025-01-01', usd=100),
          event('A', 'sale', 'sell', '2025-02-01', usd=150)]
    r = C.build_crypto_import(tx, tax_year=2025)
    assert rows(r)[0]['cost_basis'] == 100
    assert not r['account_fifo']['complete']


def test_optional_explicit_sequence_can_resolve_reviewed_same_time_local_order():
    tx = [event('A', 'sale', 'sell', '2025-01-01', usd=200, sequence=2),
          event('A', 'buy', 'buy', '2025-01-01', usd=100, sequence=1)]
    r = C.build_crypto_import(tx)
    assert r['account_fifo']['complete']
    assert rows(r)[0]['cost_basis'] == 100


@pytest.mark.parametrize('value', [True, 0, 10000, 2025.0, float('nan')])
def test_filing_year_domain_is_strict(value):
    with pytest.raises(C.CryptoImportError):
        C.build_crypto_import([], tax_year=value)


def test_dropping_basis_mode_cannot_clear_incomplete_metadata():
    summary = dict(complete=False, unresolved_inputs=[])
    assert tie_out.evaluate([], {'crypto_summary': summary}, []).failed


def test_fee_cents_reconcile_across_partial_sale_slices():
    # Three original lots; net proceeds $0.05 require a rounding residual on
    # the final slice (0.02 + 0.02 + 0.01), not three independent $0.02 rows.
    tx = [event('A', f'b{i}', 'buy', f'2023-01-0{i+1}', 1, 0.01) for i in range(3)]
    tx.append(event('A', 'sell', 'sell', '2025-01-01', 3, 0.07, fee_usd=0.02))
    r = C.build_crypto_import(tx)
    assert r['account_fifo']['complete']
    assert [row['proceeds'] for row in rows(r)] == [0.02, 0.02, 0.01]
    assert r['schedule_d']['net_gain'] == 0.02
