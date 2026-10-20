"""AA76: price and receipt the CA penalty with its source payments."""
import copy

import pytest

from agents.lib import return_builder as B, tie_out
from agents.lib.tax_computations.state.ca import settle_ca_5805
from tests.test_ca_540_builder import case, law
from tests.test_ca_payments import payment


def priced():
    profile, result = case()
    state = law()
    state['underpayment_screen']['tax_fraction'] = .9
    profile['ca_5805']['prior_return'].update(tax=10000, agi=5000)
    return profile, result, {'state': state}


def publish(profile, result, cache):
    built = B.build_california(result, cache, [], profile)
    result['california_540'].update(built)
    result['meta']['limitations'] = [built['line_113_note']]
    return built


def test_penalty_settles_before_amount_owed_and_rebuilds_idempotently():
    profile, result, cache = priced()
    original = copy.deepcopy(profile)
    out = publish(profile, result, cache)
    # FTB5805 WorksheetII: base4,440 * .9=3,996, weighted .3/.4/0/.3.
    # Q1:76 days at .08 +184 at .07 +105 at .06; Q2 starts June17,
    # so14 days at .08, then same184/105. Q4:90 days at .06.
    expected = round(1198.8 * (76*.08 + 184*.07 + 105*.06)/365
        +1598.4 * (14*.08 + 184*.07 + 105*.06)/365 +1198.8*90*.06/365, 2)
    assert out['ftb_5805']['current_tax'] == 4440
    assert out['ftb_5805']['required_annual_payment'] == 3996
    assert out['line_113'] == out['ftb_5805']['total'] == expected > 0
    assert out['line_114'] == out['ca_amount_owed'] == 4440 + expected
    assert out['ca_refund'] == 0
    assert B.build_california(result, cache, [], profile) == out
    assert not B.california_differences(result, out)
    assert profile == original
    assert not tie_out._ca_5805_checks([], result, profile, cache)


@pytest.mark.parametrize('field', ['ftb_5805', 'line_113', 'line_114', 'ca_amount_owed', 'line_113_note'])
def test_standalone_tie_out_rejects_dropped_or_typed_penalty_fields(field):
    p, r, c = priced()
    publish(p, r, c)
    del r['california_540'][field]
    assert tie_out._ca_5805_checks([], r, p, c)


def test_source_payment_change_invalidates_stored_workpaper_and_balance():
    p, r, c = priced()
    p['flags']['ca_has_nonw2_payments'] = True
    p['estimated_payments'] = {'ca_verified': True, 'state': [payment(amount=3996)]}
    original = publish(p, r, c)
    assert original['line_113'] == 0  # paid before all requirements
    p['estimated_payments']['state'][0]['date'] = '2026-04-15'
    assert tie_out._ca_5805_checks([], r, p, c)
    rebuilt = B.build_california(r, c, [], p)
    assert rebuilt['line_113'] > 0 and rebuilt['line_72'] == 3996


def test_balance_payment_changes_timing_without_becoming_line72_credit():
    p, r, c = priced()
    p['ca_5805'].update(balance_payments=[{'payment_id': 'balance', 'date': '2026-01-20', 'amount': 4440}],
        balance_payments_confirmed=True)
    out = B.build_california(r, c, [], p)
    assert out['line_72'] == 0 and out['ftb_5805']['dated_payments'][0]['amount'] == 4440
    assert all(row['through'] <= '2026-01-20' for row in out['ftb_5805']['penalty_details'])
    assert out['line_113'] < B.build_california(priced()[1], c, [], priced()[0])['line_113']


def test_same_payment_cannot_be_both_estimate_and_return_balance():
    p, r, c = priced()
    p['flags']['ca_has_nonw2_payments'] = True
    p['estimated_payments'] = {'ca_verified': True, 'state': [payment()]}
    p['ca_5805'].update(balance_payments=[{'payment_id': 'ca-estimate', 'date': '2026-01-20', 'amount': 1000}],
        balance_payments_confirmed=True)
    with pytest.raises(B.Blocked, match='unique'):
        B.build_california(r, c, [], p)


def test_unknown_payment_date_can_prove_zero_but_cannot_price_positive_penalty():
    p, r, c = priced()
    p['flags']['ca_has_nonw2_payments'] = True
    p['estimated_payments'] = {'ca_verified': True, 'state': [payment(date=None)]}
    with pytest.raises(B.Blocked, match='unknown CA payment dates'):
        B.build_california(r, c, [], p)
    p['ca_5805']['prior_return']['tax'] = 0
    out = B.build_california(r, c, [], p)
    assert out['line_113'] == 0 and 'unknown dates' in out['ca_payment_note']


@pytest.mark.parametrize('field', ['method', 'current_tax_components_confirmed', 'regular_method_confirmed'])
def test_missing_method_and_component_facts_cannot_mean_zero(field):
    p, r, c = priced()
    del p['ca_5805'][field]
    with pytest.raises(B.Blocked):
        B.build_california(r, c, [], p)


def test_prior_total_tax_is_not_a_default_for_missing_partii_facts():
    p, r, c = priced()
    p['prior_year'] = {'total_tax': 10000}
    p['ca_5805']['prior_return']['confirmed'] = False
    with pytest.raises(B.Blocked, match='prior-year'):
        B.build_california(r, c, [], p)


def test_no_prior_return_uses_current_prong_without_invented_prior_tax():
    p, r, c = priced()
    p['ca_5805']['prior_return'].update(filed=False, full_year=None, tax=10000, agi=None, withholding=0)
    out = B.build_california(r, c, [], p)
    assert out['ftb_5805']['basis'] == 'current_year' and out['line_113'] > 0


def test_distribution_exclusions_and_only_453a_interest_enter_partii_tax():
    p, r, c = priced()
    r['california_540'].update(line_34=1000, ca_lump_sum_tax=600, line_63=200)
    p['ca_5805'].update(accumulation_distribution_tax=400, section_453a_interest=50)
    out = B.build_california(r, c, [], p)
    assert out['ftb_5805']['current_tax'] == 4440 + 50
    assert out['line_64'] == 4440 + 1000 + 200
    p['ca_5805']['section_453a_interest'] = 201
    with pytest.raises(B.Blocked, match='reconcile'):
        B.build_california(r, c, [], p)


@pytest.mark.parametrize('method', ['manual', 'ftb_bill'])
def test_special_routes_are_deliberate_and_disclosed(method):
    p, r, c = priced()
    p['ca_5805']['method'] = method
    with pytest.raises(B.Blocked):
        B.build_california(r, c, [], p)
    p['ca_5805'].update(manual_form='FTB5805F', manual_penalty=123,
        manual_worksheet_confirmed=True, ftb_bill_eligible_confirmed=True)
    out = B.build_california(r, c, [], p)
    assert out['ftb_5805']['method'] == method
    assert out['line_113'] == (123 if method == 'manual' else 0)
    assert ('NOT COMPUTED' in out['line_113_note']) == (method == 'ftb_bill')


def test_penalty_consumes_refund_before_creating_balance():
    # Form540 settlement: refund100 is exhausted by penalty150 -> balance50.
    form = {f'line_{n}': 0 for n in (100, 94, 96, 110, 112)}
    form['line_99'] = 100
    out = settle_ca_5805(form, {'total': 150})
    assert out['line_114'] == out['ca_amount_owed'] == 50
    assert out['line_115'] == out['ca_refund'] == 0


def test_prior_floor_is_independent_of_filing_and_full_year_status():
    # FTB5805 IMPORTANT: either year's below-floor liability is enough.
    p, r, c = priced()
    p['ca_5805']['prior_return'].update(filed=None, full_year=None, tax=100, withholding=None)
    assert B.build_california(r, c, [], p)['line_113'] == 0
    p['ca_5805']['prior_return'].update(tax=10000, withholding=9999)
    assert B.build_california(r, c, [], p)['line_113'] == 0


@pytest.mark.parametrize('missing', ['tax', 'withholding'])
def test_nonfiler_cannot_guess_a_positive_penalty_without_prior_floor_facts(missing):
    p, r, c = priced()
    p['ca_5805']['prior_return'].update(filed=False, full_year=None)
    p['ca_5805']['prior_return'][missing] = None
    with pytest.raises(B.Blocked, match='prior-year'):
        B.build_california(r, c, [], p)


def test_deleting_both_markers_cannot_hide_a_computed_ca_return():
    p, r, c = priced()
    publish(p, r, c)
    del r['california_540']['computed_tax_year']
    del r['california_540']['ftb_5805']
    assert tie_out._ca_5805_checks([], r, p, c)


def test_withholding_includes_both_71_and_73_and_actual_dates_need_evidence():
    p, r, c = priced()
    built = B.build_california(r, c, [], p)
    built.update(line_71=100, line_73=300)
    paper, _ = B.derive_ca_5805(built, p, c['state'], 2025, 'single')
    assert paper['withholding'] == 400
    p['ca_5805'].update(withholding_method='actual', actual_withholding=[
        {'payment_id': 'held', 'date': '2025-12-31', 'amount': 400}])
    with pytest.raises(ValueError, match='source evidence'):
        B.derive_ca_5805(built, p, c['state'], 2025, 'single')
    p['ca_5805']['actual_withholding_confirmed'] = True
    paper, _ = B.derive_ca_5805(built, p, c['state'], 2025, 'single')
    assert paper['withholding_method'] == 'actual' and paper['total'] > 0


def test_one_credit_id_cannot_be_both_estimate_and_withholding():
    p, r, c = priced()
    p['flags']['ca_has_nonw2_payments'] = True
    p['estimated_payments'] = {'ca_verified': True, 'state': [payment()]}
    built = B.build_california(r, c, [], p)
    built['line_71'] = 500
    p['ca_5805'].update(withholding_method='actual', actual_withholding_confirmed=True,
        actual_withholding=[{'payment_id': 'ca-estimate', 'date': '2025-12-31', 'amount': 500}])
    with pytest.raises(ValueError, match='unique'):
        B.derive_ca_5805(built, p, c['state'], 2025, 'single')
