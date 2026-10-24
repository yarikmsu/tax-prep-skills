"""AA76 rates are half-year data; CA's annual figures stay annual."""
import copy
import json
from datetime import datetime

import pytest

from agents.lib import tax_law_fetcher as F
from agents.lib.tax_law_export import audit_schema

SOURCE = 'https://www.ftb.ca.gov/pay/penalties-and-interest/interest-and-estimate-penalty-rates.html'
NOW = datetime(2026, 10, 20, 12)
RATES = [{'effective_from': '2025-01-01', 'annual_rate': .08},
         {'effective_from': '2025-07-01', 'annual_rate': .07},
         {'effective_from': '2026-01-01', 'annual_rate': .06}]


def cache(tmp_path):
    data = F.create_empty_tax_law(2025)
    data['state'] = F.create_state_template('CA', tax_year=2025)
    F.save_tax_law(2025, data, str(tmp_path))
    return tmp_path / 'tax_law_2025.json'


def set_rates(tmp_path, rates=None, **kwargs):
    return F.set_figure(2025, 'state.underpayment_rates', RATES if rates is None else rates,
        SOURCE, str(tmp_path), now=NOW, **kwargs)


def test_only_the_schedule_has_an_independent_multi_period_pin(tmp_path):
    path = cache(tmp_path)
    info = set_rates(tmp_path, applies_to='2026-H1')
    data = json.loads(path.read_text())
    assert info['pin'] == 'state.CA.underpayment_rates'
    assert F.source_pins(data)['state.CA.underpayment_rates'] == 'state.underpayment_rates'
    assert 'state.CA.underpayment_rates' in F.multi_period_pins()
    assert 'state.CA' not in F.multi_period_pins()
    assert data['_sources']['state.CA.underpayment_rates'] == SOURCE
    assert data['_provenance']['state.CA.underpayment_rates']['applies_to'] == '2026-H1'
    # Unfetched scaffold fields are expected; the valid next-year period isn't.
    report = F.check_freshness(2025, str(tmp_path))
    assert not any('_provenance.state.CA.underpayment_rates' in field for field in report['null_fields'])
    schema = audit_schema(data, 2025)
    assert 'state.CA.underpayment_rates' in schema['multi_period_pins']
    assert schema['source_pins']['state.CA.underpayment_rates'] == 'state.underpayment_rates'


@pytest.mark.parametrize('period', ['2025-H1', '2025-H2', '2026-H1', 2026, None])
def test_valid_half_year_or_calendar_year_can_be_written(tmp_path, period):
    cache(tmp_path)
    assert set_rates(tmp_path, applies_to=period)['applies_to'] == (2025 if period is None else period)


@pytest.mark.parametrize('period', ['2026-Q1', '2026-H3', '2026-H0', '2026-h1', True, [], {}, 0])
def test_other_periods_are_refused_atomically(tmp_path, period):
    path = cache(tmp_path)
    before = path.read_bytes()
    with pytest.raises(F.TaxLawSetError):
        set_rates(tmp_path, applies_to=period)
    assert path.read_bytes() == before


def test_next_year_does_not_leak_into_brackets_or_exemptions(tmp_path):
    path = cache(tmp_path)
    set_rates(tmp_path, applies_to='2026-H1')
    before = path.read_bytes()
    for field, value in [('state.standard_deduction.single', 1234),
                         ('state.personal_exemption.amount', 99),
                         ('state.brackets', [{'min': 0, 'max': None, 'rate': .05}])]:
        with pytest.raises(F.TaxLawSetError, match='2025 figure'):
            F.set_figure(2025, field, value, SOURCE, str(tmp_path), applies_to='2026-H1', now=NOW)
        assert path.read_bytes() == before


@pytest.mark.parametrize('rates', [[], [{'effective_from': '2025-04-01', 'annual_rate': .08}],
    [{'effective_from': '20250101', 'annual_rate': .08}],
    [{'effective_from': '2025-01-01', 'annual_rate': float('nan')}],
    [{'effective_from': '2025-01-01', 'annual_rate': True}],
    [{'effective_from': '2025-01-01', 'annual_rate': -1}],
    [{'effective_from': '2025-01-01', 'annual_rate': .08, 'extra': 1}], RATES[::-1], RATES + RATES[-1:]])
def test_schedule_shape_cannot_hide_a_gap_with_a_carryable_rate(tmp_path, rates):
    path = cache(tmp_path)
    before = path.read_bytes()
    with pytest.raises(F.TaxLawSetError):
        set_rates(tmp_path, rates)
    assert path.read_bytes() == before


def test_hand_edited_half_year_period_is_checked_by_freshness_too(tmp_path):
    from agents.lib.secure_fs import write_secure
    path = cache(tmp_path)
    set_rates(tmp_path, applies_to='2026-H1')
    data = json.loads(path.read_text())
    data['_provenance']['state.CA.underpayment_rates']['applies_to'] = '2026-Q1'
    write_secure(path, json.dumps(data))
    assert any('_provenance.state.CA.underpayment_rates' in field
        for field in F.check_freshness(2025, str(tmp_path))['null_fields'])


def test_seed_contains_no_default_shares_thresholds_dates_or_rates():
    seed = F.create_state_template('CA', tax_year=2025)
    assert seed['underpayment_rates'] == []
    policy = copy.deepcopy(seed['estimated_tax_safe_harbor'])
    assert policy['installment_shares'] == policy['due_dates'] == []
    assert all(v is None for v in policy['prior_agi_threshold'].values())
    assert policy['prior_year_share_high_income'] is None
    assert policy['current_year_share'] is None
    assert policy['early_filing_deadline'] is None
