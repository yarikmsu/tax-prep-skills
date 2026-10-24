"""AA62: FIFO cannot replace the taxpayer's actual selection of lots."""
import copy
import json
from pathlib import Path

import pytest

from agents.lib import capabilities, crypto_importer as C, tie_out
from agents.lib.secure_fs import secure_path, write_secure
from tests.test_capabilities import _profile
from tests.test_crypto_reporting import BASE, COMPLETE, form, reporting

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('answer', [None, True])
@pytest.mark.parametrize('account_mode', [False, True])
def test_screening_happens_before_either_fifo_matcher(monkeypatch, answer, account_mode):
    def forbidden(*args, **kwargs):
        pytest.fail('FIFO must not price an unanswered or specific-ID import')
    monkeypatch.setattr(C, 'match_lots', forbidden)
    monkeypatch.setattr(C, '_match_account_fifo', forbidden)
    tx = copy.deepcopy(BASE)
    if not account_mode:
        for event in tx:
            event.pop('account_id')
            event.pop('txn_id')
    with pytest.raises(C.CryptoImportError, match='[Ss]pecific.identification'):
        C.build_crypto_import(tx, tax_year=2025, specific_identification=answer)


@pytest.mark.parametrize('answer', [0, 1, 'false', [], {}, float('nan'), float('inf')])
def test_lot_answer_is_a_boolean_not_a_truthy_or_falsy_value(answer):
    with pytest.raises(C.CryptoImportError, match='must be boolean'):
        C.build_crypto_import(BASE, specific_identification=answer)


def test_omitted_answer_cannot_price_prior_sales_even_when_the_return_year_has_none():
    with pytest.raises(C.CryptoImportError, match='unanswered'):
        C.build_crypto_import(BASE, tax_year=2026)


def test_transfer_lot_removal_also_needs_the_answer():
    tx = [dict(account_id='A', txn_id='transfer', kind='send', date='2025-03-01',
               asset='BTC', quantity=1, usd_amount=0, external_transfer_confirmed=True)]
    with pytest.raises(C.CryptoImportError, match='unanswered'):
        C.build_crypto_import(tx, tax_year=2025)


def test_acquisitions_alone_do_not_run_lot_selection():
    result = C.build_crypto_import(BASE[:1])
    assert result['crypto_summary']['disposals'] == 0


def test_a_yes_does_not_refuse_an_import_that_removes_no_lots():
    # Specific identification in an earlier year, only a buy this year:
    # nothing here depends on which lots were chosen.
    result = C.build_crypto_import(BASE[:1], specific_identification=True)
    assert result['crypto_summary']['disposals'] == 0


def test_the_question_names_a_brokers_only_method_and_the_relief_limits():
    # Reg. 1.1012-1(j)(3)(ii): a broker's only method is a standing order;
    # Notice 2025-7 covers broker-held units disposed of in 2025 only.
    for path in ('.agents/skills/tax-setup/references/interview.md',
                 '.agents/skills/tax-parse-crypto/SKILL.md'):
        text = ' '.join((REPO / path).read_text().split())
        assert 'only one' in text and '1.1012-1(j)(3)(ii)' in text, path
        assert 'disposed of in 2025' in text and 'after the sale is not adequate' in text, path


@pytest.mark.parametrize('checked', [True, False, None])
def test_box8_is_evidence_not_a_selection_method(checked):
    # Two possible bases: oldest lot $100, a specifically selected later lot $300.
    # A $200 sale would gain $100 FIFO or lose $100 under that identification.
    # A noncovered broker form supplies no basis to reveal the difference.
    tx = [BASE[0], dict(BASE[0], txn_id='second-buy', date='2024-01-01', usd_amount=300),
          dict(BASE[1], usd_amount=200)]
    evidence = reporting(form(proceeds=200, customer_acquisition_info=checked))
    result = C.build_crypto_import(tx, tax_year=2025, specific_identification=False,
                                   broker_reporting=evidence)
    assert result['crypto_summary']['reporting_complete'] is True
    assert result['schedule_d']['net_gain'] == 100
    assert result['broker_reporting']['forms'][0]['customer_acquisition_info'] is checked
    for answer in (None, True):
        with pytest.raises(C.CryptoImportError, match='[Ss]pecific.identification'):
            C.build_crypto_import(tx, tax_year=2025, specific_identification=answer,
                                  broker_reporting=evidence)


@pytest.mark.parametrize('bad', [0, 1, 'yes', float('nan')])
def test_checked_box8_is_typed(bad):
    with pytest.raises(C.CryptoImportError, match='customer_acquisition_info must be boolean'):
        C.build_crypto_import(BASE, tax_year=2025, specific_identification=False,
                              broker_reporting=reporting(form(customer_acquisition_info=bad)))


def test_box8_still_has_to_be_read_when_fifo_is_confirmed():
    entry = form()
    del entry['customer_acquisition_info']
    with pytest.raises(C.CryptoImportError, match="missing.*customer_acquisition_info"):
        C.build_crypto_import(BASE, tax_year=2025, specific_identification=False,
                              broker_reporting=reporting(entry))


@pytest.mark.parametrize('answer', [None, True, 0, 1, 'false'])
def test_cli_does_not_default_the_answer(tmp_path, monkeypatch, capsys, answer):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('TAX_WORKSPACE', raising=False)
    write_secure(tmp_path / 'synthetic.json', json.dumps(
        dict(transactions=BASE, specific_identification=answer)))
    assert C.main(['compute', 'synthetic.json', '--year', '2025']) == 1
    output = capsys.readouterr()
    assert not output.out  # no diagnostic prices before the answer
    assert 'specific' in output.err.lower()


def test_capability_blocks_yes_even_at_a_broker_whose_form_has_no_basis():
    profile = _profile({'has_crypto': True, 'has_crypto_specific_identification': True,
                        'has_digital_asset_option_exercise': False})
    result = capabilities.evaluate(profile)
    assert [f.rule_id for f in result.blocking] == ['crypto_specific_identification']
    assert 'standing' in result.blocking[0].note and 'prior' in result.blocking[0].note
    profile['acknowledged_limitations'] = ['crypto_specific_identification']
    assert not capabilities.evaluate(profile).blocking
    with pytest.raises(C.CryptoImportError, match='Acknowledging'):
        C.build_crypto_import(BASE, specific_identification=True)


def test_unasked_capability_question_is_not_an_acknowledgeable_limitation():
    for answer in ({}, {'has_crypto_specific_identification': None}):
        profile = _profile({'has_crypto': True, **answer},
                           acknowledged=['crypto_specific_identification'])
        report = capabilities.evaluate(profile)
        assert any(f.rule_id == 'unanswered_crypto_lot_selection' for f in report.blocking)
    with pytest.raises(capabilities.CapabilityError):
        capabilities.evaluate(_profile({'has_crypto': True},
                              acknowledged=['unanswered_crypto_lot_selection']))


@pytest.mark.parametrize('answer', [None, True, 0, 1, 'false', False])
def test_tie_out_requires_an_explicit_answer_independently_of_has_crypto(answer):
    # As for the option-exercise question, the capability gate blocks a yes
    # until it is acknowledged; the tie-out needs only an explicit answer, or
    # an acknowledged yes with external Form 8949 rows could never complete.
    report = tie_out.evaluate([], {'crypto_summary': COMPLETE}, [],
                              option_exercise_answer=False,
                              specific_identification_answer=answer)
    unanswered = not isinstance(answer, bool)
    assert report.failed is unanswered
    assert any('has_crypto_specific_identification' in t for t in report.missing_result_targets) is unanswered


def test_zero_proceeds_loss_still_owes_screening_when_the_summary_was_dropped():
    # A worthless sale: $0 proceeds less $100 basis = $100 loss. Choosing a
    # different $300 lot would change that loss even though proceeds are zero.
    result = {'schedule_d': {'part_i_short_term': {'line_2_digital_assets':
              {'proceeds': 0, 'cost': 100, 'adjustment': 0, 'gain': -100}}}}
    report = tie_out.evaluate([], result, [], option_exercise_answer=False)
    assert any('has_crypto_specific_identification' in t for t in report.missing_result_targets)
    assert any('crypto Form 8949 reporting' in t for t in report.missing_result_targets)


def test_empty_schedule_d_template_rows_do_not_invent_crypto_activity():
    result = {'schedule_d': {'part_i_short_term': {'line_2_digital_assets':
              {'proceeds': 0, 'cost': 0, 'adjustment': 0, 'gain': 0, 'source': None}}}}
    report = tie_out.evaluate([], result, [], option_exercise_answer=False)
    assert not any('has_crypto_specific_identification' in t for t in report.missing_result_targets)


@pytest.mark.parametrize('answer', [None, True, 0, 'false', False])
def test_tie_out_reads_the_actual_profile_answer(tmp_path, capsys, answer):
    directory = tmp_path / 'documents'
    directory.mkdir()
    secure_path(directory)
    for name, data in {
        'result.json': {'meta': {'tax_year': 2025}, 'crypto_summary': COMPLETE},
        'parse_ledger.json': {'records': []},
        'taxpayer_profile.json': {'flags': {'has_crypto': False,
            'has_digital_asset_option_exercise': False,
            'has_crypto_specific_identification': answer}},
    }.items():
        write_secure(tmp_path / name, json.dumps(data))
    answered = isinstance(answer, bool)
    assert tie_out._main(['--workspace', str(tmp_path)]) == (0 if answered else 2)
    assert ('has_crypto_specific_identification' in capsys.readouterr().out) is (not answered)


def test_skipped_interview_never_answers_fifo_for_a_later_crypto_import():
    profile = json.loads((REPO / 'agents/templates/taxpayer_profile_template.json').read_text())
    assert profile['flags']['has_crypto_specific_identification'] is None
    profile['flags']['has_crypto'] = True
    assert any(f.rule_id == 'unanswered_crypto_lot_selection'
               for f in capabilities.evaluate(profile).blocking)
    setup = (REPO / '.agents/skills/tax-setup/SKILL.md').read_text()
    assert 'leave it `null` without crypto and ask before FIFO matching' in setup
    interview = (REPO / '.agents/skills/tax-setup/references/interview.md').read_text()
    skill = (REPO / '.agents/skills/tax-parse-crypto/SKILL.md').read_text()
    for text in (interview, skill):
        text = ' '.join(text.split())
        assert 'has_crypto_specific_identification' in text
        assert 'standing instructions' in text and 'prior' in text
    assert skill.index('### 0.5 Confirm lot selection') < skill.index('### 4. Compute Form')
    assert skill.index('secure_fs taxpayer_profile.json answers.md') < skill.index('**A yes STOPs FIFO')
    assert 'Adapters and normalization\nworkers never supply or default this answer' in skill
