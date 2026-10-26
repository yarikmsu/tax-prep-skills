"""AA47: evidenced option premiums enter the original FIFO source event.

Pub. 550 Table 4-3: purchased call +basis, written put -basis,
written call +proceeds, purchased put -proceeds. Synthetic dollars only.
"""
import copy
import json

import pytest

from agents.lib import crypto_importer as C
from agents.lib import tie_out
from tests.test_crypto_reporting import BASE, _broker_ledger, event, form, only, reporting, resolved, rows


def option_review(tx, treatment, *, included=False, premium=10):
    return dict(scope='plain_usd_premiums_no_other_tax_effects',
                all_accounts_and_prior_lots_reviewed=True, unsupported_items=[],
                evidence='option-scope-workpaper', events=[dict(
                    account_id=tx['account_id'], txn_id=tx['txn_id'], treatment=treatment,
                    option_opened='2024-06-01', premium_usd=premium,
                    source_includes_premium=included, fees_exclude_premium=True, evidence='exercise-confirmation',
                    expected={**{key: tx[key] for key in ('date', 'asset', 'kind', 'quantity', 'usd_amount')},
                              'fee_usd': tx.get('fee_usd', 0)})])


def inputs(treatment='purchased_call', *, included=False, premium=10):
    tx = copy.deepcopy(BASE)
    index = 0 if treatment in ('purchased_call', 'written_put') else 1
    if index == 0:
        tx[0]['date'] = '2024-07-01'
    block = reporting(received=False)
    block['option_exercise_review'] = option_review(tx[index], treatment, included=included, premium=premium)
    return dict(transactions=tx, broker_reporting=block, specific_identification=False)


def compute(source):
    return C.build_crypto_import(**copy.deepcopy(source), tax_year=2025)


@pytest.mark.parametrize('treatment,proceeds,basis,gain', [
    ('purchased_call', 150, 110, 40), ('written_put', 150, 90, 60),
    ('written_call', 160, 100, 60), ('purchased_put', 140, 100, 40),
])
def test_four_exercise_premium_directions(treatment, proceeds, basis, gain):
    source = inputs(treatment)
    original = copy.deepcopy(source)
    result = resolved(compute(source))
    row = only(result)
    assert (row['proceeds'], row['cost_basis'], row['gain_loss']) == (proceeds, basis, gain)
    assert source == original
    review = result['crypto_summary']['option_exercise_review']
    assert review['events'][0]['applied_usd_amount'] == str(basis if treatment in ('purchased_call', 'written_put') else proceeds)


@pytest.mark.parametrize('treatment', ['purchased_call', 'written_put', 'written_call', 'purchased_put'])
def test_a_premium_already_in_the_source_is_not_applied_twice(treatment):
    result = resolved(compute(inputs(treatment, included=True)))
    assert (only(result)['proceeds'], only(result)['cost_basis'], only(result)['gain_loss']) == (150, 100, 50)


def test_prior_exercise_basis_reaches_every_later_disposal_and_remaining_inventory():
    source = inputs(premium=30)
    source['transactions'][0].update(quantity=3, usd_amount=300, date='2023-07-01')
    source['transactions'].append(event('A', 'second', 'sell', '2025-04-01', usd=170))
    source['broker_reporting']['option_exercise_review'] = option_review(
        source['transactions'][0], 'purchased_call', premium=30)
    source['broker_reporting']['option_exercise_review']['events'][0]['option_opened'] = '2023-06-01'
    result = resolved(compute(source))
    assert [(r['cost_basis'], r['gain_loss']) for r in rows(result)] == [(110, 40), (110, 60)]
    lots = result['account_fifo']['inventory']['A']['BTC']
    assert len(lots) == 1
    assert lots[0]['basis_usd'] == 110


@pytest.mark.parametrize('key,value', [
    ('all_accounts_and_prior_lots_reviewed', False), ('all_accounts_and_prior_lots_reviewed', 1),
    ('unsupported_items', ['straddle']), ('unsupported_items', None), ('evidence', ''),
    ('events', []), ('unexpected', True),
    ('scope', 'unknown'),
])
def test_incomplete_or_out_of_scope_review_refuses(key, value):
    source = inputs()
    source['broker_reporting']['option_exercise_review'][key] = value
    with pytest.raises(C.CryptoImportError):
        compute(source)


@pytest.mark.parametrize('key,value', [
    ('premium_usd', True), ('premium_usd', float('nan')), ('premium_usd', float('inf')),
    ('premium_usd', -10), ('premium_usd', 0), ('premium_usd', 10.001), ('premium_usd', 1 << 4096),
    ('source_includes_premium', 1),
    ('source_includes_premium', None), ('treatment', 'unknown'), ('txn_id', 'missing'),
    ('account_id', 'missing'), ('evidence', ''), ('option_opened', '2026-01-01'),
    ('option_opened', 'invalid'), ('unexpected', True),
    ('fees_exclude_premium', False), ('fees_exclude_premium', 1),
])
def test_invalid_option_event_refuses(key, value):
    source = inputs()
    source['broker_reporting']['option_exercise_review']['events'][0][key] = value
    with pytest.raises(C.CryptoImportError):
        compute(source)


@pytest.mark.parametrize('key', ['date', 'asset', 'kind', 'quantity', 'usd_amount', 'fee_usd'])
def test_stale_option_context_refuses(key):
    source = inputs()
    expected = source['broker_reporting']['option_exercise_review']['events'][0]['expected']
    expected[key] = 'stale' if key in ('date', 'asset', 'kind') else 7
    with pytest.raises(C.CryptoImportError, match='stale'):
        compute(source)


def test_duplicate_option_events_refuse():
    source = inputs()
    source['broker_reporting']['option_exercise_review']['events'] *= 2
    with pytest.raises(C.CryptoImportError, match='repeat'):
        compute(source)


@pytest.mark.parametrize('treatment,included', [('written_put', False), ('purchased_call', True)])
def test_negative_underlying_or_adjusted_amount_refuses(treatment, included):
    with pytest.raises(C.CryptoImportError, match='negative'):
        compute(inputs(treatment, included=included, premium=200))


def test_purchased_put_requires_lots_already_long_term_when_the_put_was_bought():
    source = inputs('purchased_put')
    source['transactions'][0]['date'] = '2024-07-01'
    with pytest.raises(C.CryptoImportError, match='holding.period'):
        compute(source)


def test_reviewed_put_cost_can_explain_code_e_but_not_another_gap():
    source = inputs('purchased_put')
    source['broker_reporting'].update(reporting(form(), resolutions={
        'da-1': dict(proceeds='transaction_costs', evidence='gross-proceeds-exclude-put-cost')}))
    row = only(resolved(compute(source)))
    assert (row['proceeds'], row['adjustment_code'], row['adjustment'], row['gain_loss']) == (150, 'E', -10, 40)
    source['broker_reporting']['forms'][0]['proceeds'] = 151
    assert compute(source)['crypto_summary']['reporting_complete'] is False


def test_reviewed_purchased_put_can_reconcile_net_proceeds():
    source = inputs('purchased_put')
    source['broker_reporting'].update(reporting(form(net_proceeds=True, proceeds=140)))
    assert only(resolved(compute(source)))['gain_loss'] == 40
    source['broker_reporting']['option_exercise_review']['events'][0]['treatment'] = 'written_call'
    with pytest.raises(C.CryptoImportError, match='net_proceeds'):
        compute(source)


def test_net_proceeds_cannot_deduct_the_put_cost_again():
    source = inputs('purchased_put')
    source['broker_reporting'].update(reporting(form(net_proceeds=True), resolutions={
        'da-1': dict(proceeds='transaction_costs', evidence='cost-already-netted')}))
    assert compute(source)['crypto_summary']['reporting_complete'] is False


def test_net_proceeds_can_still_deduct_a_separate_unreported_commission():
    source = inputs('purchased_put')
    source['transactions'][1]['fee_usd'] = 2
    source['broker_reporting']['option_exercise_review'] = option_review(source['transactions'][1], 'purchased_put')
    source['broker_reporting'].update(reporting(form(net_proceeds=True, proceeds=140), resolutions={
        'da-1': dict(proceeds='transaction_costs', evidence='commission-not-in-form')}))
    row = only(resolved(compute(source)))
    assert (row['proceeds'], row['adjustment'], row['gain_loss']) == (140, -2, 38)


def test_option_acquisition_basis_survives_a_reviewed_account_transfer():
    source = inputs('purchased_call')
    source['transactions'][1]['account_id'] = 'B'
    source['transactions'][1]['kind'] = 'spend'
    source['transactions'][1:1] = [event('A', 'send', 'send', '2024-12-01', transfer_id='move'),
                                  event('B', 'receive', 'receive', '2024-12-02', transfer_id='move')]
    source['transfer_links'] = {'move': dict(ownership_confirmed=True, evidence='wallet-transfer')}
    source['broker_reporting']['accounts']['B'] = dict(forms_received=False, evidence='wallet-review')
    assert only(resolved(compute(source)))['cost_basis'] == 110


def test_source_correction_is_bound_before_premium_inclusion_is_reviewed():
    source = inputs('purchased_call')
    source['broker_reporting']['source_corrections'] = [dict(account_id='A', txn_id='buy',
        expected=dict(date='2024-07-01', asset='BTC', kind='buy', usd_amount=100),
        corrected=dict(usd_amount=105), evidence='purchase-confirmation')]
    with pytest.raises(C.CryptoImportError, match='stale'):
        compute(source)
    source['broker_reporting']['option_exercise_review']['events'][0]['expected']['usd_amount'] = 105
    assert only(resolved(compute(source)))['cost_basis'] == 115


@pytest.mark.parametrize('kind', ['trade', 'spend', 'receive', 'income'])
def test_an_option_caused_event_cannot_reprice_other_transaction_kinds(kind):
    source = inputs('written_call')
    source['transactions'][1]['kind'] = kind
    source['broker_reporting']['option_exercise_review']['events'][0]['expected']['kind'] = kind
    with pytest.raises(C.CryptoImportError, match='direct USD sell'):
        compute(source)


def test_option_caused_disposal_cannot_lend_evidence_to_a_fill_group():
    source = inputs('purchased_put')
    source['broker_reporting']['fill_groups'] = [dict(account_id='A', group_id='combined',
        txn_ids=['sale', 'other'], evidence='combined-order')]
    with pytest.raises(C.CryptoImportError, match='Grouped option'):
        compute(source)


@pytest.mark.parametrize('key', ['scope', 'events', 'evidence'])
def test_missing_option_review_fields_refuse(key):
    source = inputs()
    del source['broker_reporting']['option_exercise_review'][key]
    with pytest.raises(C.CryptoImportError):
        compute(source)


def test_a_purchased_put_with_unknown_acquired_lots_cannot_price():
    source = inputs('purchased_put')
    source['transactions'].pop(0)
    with pytest.raises(C.CryptoImportError, match='holding.period'):
        compute(source)


def test_unreviewed_filing_year_does_not_gain_option_support():
    with pytest.raises(C.CryptoImportError, match='reviewed filing year'):
        C.build_crypto_import(**inputs(), tax_year=2026)


@pytest.mark.parametrize('year', [2023, 2024, 2025])
def test_each_supported_option_year_keeps_its_reviewed_primary_source(year):
    source = inputs()
    source['transactions'][0]['date'] = f'{year - 1}-07-01'
    source['transactions'][1]['date'] = f'{year}-03-01'
    review = option_review(source['transactions'][0], 'purchased_call')
    review['events'][0]['option_opened'] = f'{year - 1}-06-01'
    source['broker_reporting']['option_exercise_review'] = review
    result = resolved(C.build_crypto_import(**source, tax_year=year))
    assert only(result)['cost_basis'] == 110
    assert result['crypto_summary']['option_exercise_review']['source_url'] == f'https://www.irs.gov/pub/irs-prior/p550--{year}.pdf'


def test_a_new_reporting_year_does_not_implicitly_review_option_treatment(monkeypatch):
    monkeypatch.setitem(C._REPORTING_REGIMES, 2026, C._REPORTING_REGIMES[2025])
    with pytest.raises(C.CryptoImportError, match='reviewed filing year'):
        C.build_crypto_import(**inputs(), tax_year=2026)


def test_cli_refuses_an_option_review_at_the_wrong_input_level(tmp_path):
    source = inputs()
    source['option_exercise_review'] = source['broker_reporting'].pop('option_exercise_review')
    path = tmp_path / 'misplaced-option-review.json'
    path.write_text(json.dumps(source))
    with pytest.raises(C.CryptoImportError, match='inside broker_reporting'):
        C._cli_compute([str(path), '--year', '2025'])


def merged(source):
    built = resolved(compute(source))
    result = dict(meta={'tax_year': 2025}, crypto_summary=built['crypto_summary'],
                  form_8949=built['form_8949'], schedule_d={'part_i_short_term': {}, 'part_ii_long_term': {}})
    for key, route in built['broker_reporting']['schedule_d_routing'].items():
        part = 'part_i_short_term' if key in 'GHIABC' else 'part_ii_long_term'
        result['schedule_d'][part][route['row']] = {k: route[k] for k in (
            'proceeds', 'cost', 'adjustment', 'gain', 'adjustment_code', 'broker_proceeds') if k in route}
    return result


def check(source, result):
    forms = source['broker_reporting'].get('forms', [])
    ledger = _broker_ledger(sum(float(f['proceeds']) for f in forms)) if forms else []
    documents = ['documents/1099DA.pdf'] if forms else []
    return tie_out.evaluate(ledger, result, documents, option_exercise_answer=True,
                            specific_identification_answer=False, crypto_source=source)


def test_option_tie_out_uses_the_profile_year_when_meta_is_unset():
    source = inputs()
    result = merged(source)
    result['meta']['tax_year'] = None
    assert not tie_out.evaluate([], result, [], option_exercise_answer=True,
        specific_identification_answer=False, crypto_source=source, profile={'tax_year': 2025}).failed


def test_tie_out_rebuilds_the_option_source_and_verifies_merged_gains():
    source = inputs()
    result = merged(source)
    assert not check(source, result).failed
    result['schedule_d']['part_i_short_term']['line_3_digital_assets']['cost'] -= 0.01
    assert check(source, result).failed  # exact cents, never the normal $1 tolerance


def test_acknowledging_options_without_repriced_source_cannot_complete():
    result = dict(crypto_summary=dict(basis_mode='account_fifo', complete=True,
                  unresolved_inputs=[], reporting_complete=True, reporting_unresolved=[]))
    assert tie_out.evaluate([], result, [], option_exercise_answer=True,
                            specific_identification_answer=False).failed


@pytest.mark.parametrize('damage', ['source', 'review', 'form_rows', 'schedule_d', 'wrong_part', 'flag'])
def test_stale_missing_or_contradictory_option_pricing_fails_tie_out(damage):
    source = inputs()
    result = merged(source)
    if damage == 'source':
        source['transactions'][0]['usd_amount'] += 1
    elif damage == 'review':
        del result['crypto_summary']['option_exercise_review']
    elif damage == 'form_rows':
        result['form_8949']['part_i_short_term'].clear()
    elif damage == 'schedule_d':
        result['schedule_d']['part_i_short_term'].clear()
    elif damage == 'wrong_part':
        result['schedule_d']['part_ii_long_term'].update(result['schedule_d']['part_i_short_term'])
        result['schedule_d']['part_i_short_term'].clear()
    elif damage == 'flag':
        assert tie_out.evaluate([], result, [], option_exercise_answer=False,
                                specific_identification_answer=False, crypto_source=source).failed
        return
    assert check(source, result).failed


def test_cli_uses_normalized_source_for_option_tie_out(tmp_path, capsys):
    source = inputs()
    (tmp_path / 'documents').mkdir()
    for name, value in {'parse_ledger.json': {'records': []}, 'result.json': merged(source),
                        'crypto_transactions.json': source,
                        'taxpayer_profile.json': {'tax_year': 2025, 'flags': {
                            'has_digital_asset_option_exercise': True,
                            'has_crypto_specific_identification': False}}}.items():
        (tmp_path / name).write_text(json.dumps(value))
    assert tie_out._main(['--workspace', str(tmp_path)]) == 0
    (tmp_path / 'crypto_transactions.json').unlink()
    assert tie_out._main(['--workspace', str(tmp_path)]) == 2
    assert 'option' in capsys.readouterr().out.lower()


def _unreached_review_source():
    # The reviewed call-exercise lot was sold in TY2024; the TY2025 sale
    # comes from a later, ordinary lot that no reviewed premium reaches.
    tx = [event('A', 'ex23', 'buy', '2023-07-01', usd=100), event('A', 's24', 'sell', '2024-09-01', usd=150),
          event('A', 'b24', 'buy', '2024-10-01', usd=120), event('A', 'sale', 'sell', '2025-03-01', usd=150)]
    block = reporting(received=False)
    block['option_exercise_review'] = option_review(tx[0], 'purchased_call')
    block['option_exercise_review']['events'][0]['option_opened'] = '2023-06-01'
    return dict(transactions=tx, broker_reporting=block, specific_identification=False)


def test_a_review_no_filing_year_row_reaches_cannot_price_an_option_affected_sale():
    source = _unreached_review_source()
    report = check(source, merged(source))
    assert report.failed
    assert any('prices no filing-year Form 8949 row' in t for t in report.missing_result_targets)


def test_a_review_pricing_only_held_inventory_does_not_contradict_a_truthful_no():
    # This year's sale comes from an older ordinary lot; the reviewed 2025
    # exercise lot is still held, so "no option-affected sale" is true.
    tx = [event('A', 'old', 'buy', '2023-01-01', usd=100), event('A', 'exercise', 'buy', '2025-02-01', usd=120),
          event('A', 'sale', 'sell', '2025-03-01', usd=150)]
    block = reporting(received=False)
    block['option_exercise_review'] = option_review(tx[1], 'purchased_call')
    block['option_exercise_review']['events'][0]['option_opened'] = '2025-01-01'
    source = dict(transactions=tx, broker_reporting=block, specific_identification=False)
    assert not tie_out.evaluate([], merged(source), [], option_exercise_answer=False,
                                specific_identification_answer=False, crypto_source=source).failed


def test_a_premium_on_an_earlier_years_sale_names_that_return():
    result = compute(_unreached_review_source())
    assert result['caveats'][0].startswith('PRIOR-YEAR RETURN') and 'TY2024' in result['caveats'][0]


def test_schedule_d_adjustment_codes_must_match_the_rebuilt_source():
    source = inputs('purchased_put')
    source['broker_reporting'].update(reporting(form(), resolutions={
        'da-1': dict(proceeds='transaction_costs', evidence='gross-proceeds-exclude-put-cost')}))
    result = merged(source)
    assert not check(source, result).failed
    row, = (row for part in result['schedule_d'].values() for row in part.values())
    row['adjustment_code'] = None
    report = check(source, result)
    assert any('adjustment codes disagree' in t for t in report.missing_result_targets)


def test_tie_out_refuses_review_metadata_outside_broker_reporting():
    source = inputs()
    result = merged(source)
    source['option_exercise_review'] = source['broker_reporting']['option_exercise_review']
    report = check(source, result)
    assert any('inside broker_reporting' in t for t in report.missing_result_targets)


def test_a_null_option_review_refuses_rather_than_reading_as_none():
    source = inputs()
    source['broker_reporting']['option_exercise_review'] = None
    with pytest.raises(C.CryptoImportError, match='never null'):
        compute(source)


def _written_call_on_a_gross_form(proceeds=150, resolution='option_premium'):
    # The call's 10 premium raises the amount realized to 160; the broker's
    # gross 1099-DA shows the 150 the asset sold for.
    source = inputs('written_call')
    resolutions = {'da-1': dict(proceeds=resolution, evidence='call-premium-confirmation')} if resolution else {}
    source['broker_reporting'].update(reporting(form(proceeds=proceeds), resolutions=resolutions))
    return source


def test_a_written_calls_unreflected_premium_is_code_e_in_column_g():
    # i8949 (TY2023-2025): the form's proceeds go in column (d), and an option
    # premium the form does not reflect is code E with the adjustment in (g).
    row = only(resolved(compute(_written_call_on_a_gross_form())))
    assert (row['proceeds'], row['adjustment_code'], row['adjustment'], row['gain_loss']) == (150, 'E', 10, 60)
    report = resolved(compute(_written_call_on_a_gross_form()))['broker_reporting']
    assert report['proceeds_above_forms'] == 0


def test_the_premium_resolution_explains_no_more_than_the_reviewed_premium():
    result = compute(_written_call_on_a_gross_form(proceeds=140))
    assert result['crypto_summary']['reporting_complete'] is False
    assert any('more than the 10.00 of reviewed written-call premium' in issue['reason']
               for issue in result['broker_reporting']['unresolved'])
    # Without a resolution the gap is unexplained, as before.
    result = compute(_written_call_on_a_gross_form(resolution=None))
    assert any('proceeds differ from the form' in issue['reason']
               for issue in result['broker_reporting']['unresolved'])


def test_the_premium_resolution_needs_a_reviewed_written_call():
    # A purchased call's premium is basis, never unreflected proceeds.
    source = inputs('purchased_call')
    source['broker_reporting'].update(reporting(form(proceeds=140), resolutions={
        'da-1': dict(proceeds='option_premium', evidence='call-premium-confirmation')}))
    result = compute(source)
    assert any('more than the 0.00 of reviewed written-call premium' in issue['reason']
               for issue in result['broker_reporting']['unresolved'])


@pytest.mark.parametrize('other_lot,refused', [
    (('2024-01-01', '2024-07-01'), True),     # five months old when the put was bought, sold later
    (('2024-08-01', None), True),             # bought after the put, before exercise
    (('2023-09-01', '2024-01-15'), False),    # would be short-term at the put, but sold before it
    (('2022-01-01', None), False),            # long-term when the put was bought
])
def test_a_purchased_put_refuses_while_any_same_asset_lot_takes_the_married_put_rule(other_lot, refused):
    # The put (bought 2024-06-01) is exercised against the 2023-01-01 lot on
    # 2025-03-01. Another BTC lot, in a second account, decides whether
    # Pub. 550's married-put rule reaches any holding.
    source = inputs('purchased_put')
    bought, sold = other_lot
    source['transactions'].append(event('B', 'other', 'buy', bought, usd=40))
    if sold:
        source['transactions'].append(event('B', 'other-sale', 'sell', sold, usd=45))
    source['broker_reporting']['accounts']['B'] = dict(forms_received=False, evidence='statement-B')
    if refused:
        with pytest.raises(C.CryptoImportError, match='married-put'):
            compute(source)
    else:
        resolved(compute(source))


@pytest.mark.parametrize('extra', [
    # Linked to a second account, which sold it before the put: not held.
    [event('B', 'other', 'buy', '2024-03-01', usd=40), event('B', 'out', 'send', '2024-04-01', transfer_id='t1'),
      event('C', 'in', 'receive', '2024-04-01T01:00:00', transfer_id='t1'),
      event('C', 'other-sale', 'sell', '2024-05-01', usd=45)],
    # The same short-term holding sent out, but of another asset.
    [{**event('B', 'other', 'buy', '2024-03-01', usd=40), 'asset': 'ETH'},
      {**event('B', 'out', 'send', '2024-04-01', external_transfer_confirmed=True), 'asset': 'ETH'}],
])
def test_only_an_external_send_of_the_same_asset_counts_as_still_held(extra):
    source = inputs('purchased_put')
    source['transactions'] += extra
    for account in sorted({t['account_id'] for t in extra}):
        source['broker_reporting']['accounts'][account] = dict(forms_received=False,
                                                                evidence=f'statement-{account}')
    if any(t.get('transfer_id') for t in extra):
        source['transfer_links'] = {'t1': dict(ownership_confirmed=True, evidence='own-wallets')}
    resolved(compute(source))


@pytest.mark.parametrize('bought,sent', [('2024-08-01', '2024-09-01'), ('2024-01-01', '2024-07-01')])
def test_a_lot_sent_to_the_taxpayers_own_wallet_still_counts_for_the_married_put_rule(bought, sent):
    # Bought after the put, or five months old when it was bought, then sent
    # to an outside wallet the taxpayer owns: still held, still reached.
    source = inputs('purchased_put')
    source['transactions'] += [event('B', 'other', 'buy', bought, usd=40),
                               event('B', 'out', 'send', sent, external_transfer_confirmed=True)]
    source['broker_reporting']['accounts']['B'] = dict(forms_received=False, evidence='statement-B')
    with pytest.raises(C.CryptoImportError, match='married-put'):
        compute(source)
