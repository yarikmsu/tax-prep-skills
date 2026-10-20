"""AA70: TY2025 Form540 lines72/98, payments retained once and carried once."""

import copy
import datetime
import json

import pytest

from agents.lib import answers, filing_package, receipt, return_builder as B, rollover as R
from agents.lib.secure_fs import write_secure
from tests.test_ca_540_builder import case, law, workspace


def payment(identifier="ca-estimate", **changes):
    record = {"payment_id": identifier, "state": "CA", "tax_year": 2025,
        "date": "2025-04-15", "kind": "estimate", "amount": 1000, "person": "primary"}
    record.update(changes)
    return record


def paying(records=None, opening=0):
    p, result = case()
    p["estimated_payments"] = {"state": records if records is not None else [payment()], "ca_verified": True}
    p["prior_year"] = {"ca_estimated_payments_applied": opening}
    p["flags"]["ca_has_nonw2_payments"] = True
    return p, result


def test_line72_sums_only_claimable_ca_sources_once_and_preserves_other_records():
    # TY2025 line72: estimate1,000 + extension200 + K-1 allocation300 +
    # prior applied500 =2,000. NY700 and CA TY2024's900 pay other returns.
    records = [payment(), payment("extension", kind="extension", amount=200, date="2026-04-15"),
        payment("k1", kind="k1_estimate", amount=300, date=None),
        payment("ny", state="NY", amount=700), payment("old", tax_year=2024, amount=900)]
    p, result = paying(records, opening=500)
    original = copy.deepcopy(p)
    out = B.build_california(result, {"state": law()}, [], p)
    assert p == original  # the profile is the single dated-record source
    assert out["line_72"] == 2000
    summary = out["ca_payment_summary"]
    assert summary["estimates"] == 1000 and summary["extension"] == 200
    assert summary["k1_allocations"] == 300 and summary["prior_applied"] == 500
    assert summary["payment_count"] == 3 and summary["unknown_date_count"] == 1
    assert summary["external_record_count"] == 2
    assert "unknown dates" in out["ca_payment_note"] and "other states/tax years" in out["ca_payment_note"]


@pytest.mark.parametrize("field,bad", [
    ("state", "ca"), ("state", "ZZ"), ("state", None), ("tax_year", True),
    ("tax_year", "2025"), ("tax_year", 0), ("kind", "prior_applied"),
    ("kind", "withholding"), ("kind", []), ("person", "taxpayer"),
    ("date", "2025-02-30"), ("date", "20250415"), ("date", True),
    ("amount", True), ("amount", -1), ("amount", "1000"),
    ("amount", float("nan")), ("amount", float("inf")),
    ("amount", 10 ** 400),
    ("payment_id", ""), ("payment_id", " ca-estimate "), ("payment_id", []),
])
def test_malformed_payment_records_refuse_without_echoing_values(field, bad):
    # Another valid CA payment keeps the total positive: an invalid record
    # cannot pass merely because it was ignored as an external/unknown one.
    p, _ = paying([payment('base'), payment(**{field: bad})])
    with pytest.raises(ValueError):
        B._ca_payments(p, 2025, "single")


def test_old_untagged_records_and_duplicate_ids_cannot_be_guessed_or_counted_twice():
    p, _ = paying([{"date": "2025-04-15", "amount": 1000}])
    with pytest.raises(ValueError, match="migrate"):
        B._ca_payments(p, 2025, "single")
    p["estimated_payments"]["state"] = [payment(), payment()]
    with pytest.raises(ValueError, match="unique"):
        B._ca_payments(p, 2025, "single")


def test_a_negative_record_cannot_hide_inside_a_positive_payment_total():
    p, _ = paying([payment('valid'), payment('negative', amount=-1)])
    with pytest.raises(ValueError, match='nonnegative'):
        B._ca_payments(p, 2025, 'single')


@pytest.mark.parametrize("field,bad", [("ca_verified", False), ("ca_verified", None), ("ca_verified", "yes")])
def test_positive_payments_require_source_and_ownership_confirmation(field, bad):
    p, result = paying()
    p["estimated_payments"][field] = bad
    with pytest.raises(B.Blocked, match="ca_verified"):
        B.build_california(result, {"state": law()}, [], p)


def test_screens_cannot_silently_erase_known_payments_or_invent_an_unanswered_zero():
    p, result = paying()
    for value in (None, False, "yes"):
        p["flags"]["ca_has_nonw2_payments"] = value
        with pytest.raises(B.Blocked):
            B.build_california(result, {"state": law()}, [], p)
    p, result = case()
    p["flags"]["ca_has_nonw2_payments"] = True
    with pytest.raises(B.Blocked, match="disagrees"):
        B.build_california(result, {"state": law()}, [], p)


def test_future_planned_payments_are_not_credits_already_paid():
    future = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    p, _ = paying([payment(date=future)])
    with pytest.raises(ValueError, match="future"):
        B._ca_payments(p, 2025, "single")


def test_account_ownership_and_required_explanations():
    p, _ = paying([payment(person="spouse")])
    with pytest.raises(ValueError, match="spouse"):
        B._ca_payments(p, 2025, "mfs")
    _, note = B._ca_payments(p, 2025, "mfj")
    assert "explanation" in note
    p["estimated_payments"]["state"] = [payment(person="joint")]
    _, note = B._ca_payments(p, 2025, "mfs")
    assert "allocation" in note


@pytest.mark.parametrize("opening", [True, -1, None, "500", float("nan"), float("inf")])
def test_opening_applied_credit_is_not_a_plausible_numeric_default(opening):
    p, _ = paying([], opening)
    with pytest.raises(ValueError, match="ca_estimated_payments_applied"):
        B._ca_payments(p, 2025, "single")


@pytest.mark.parametrize("answer,amount", [(None, 0), (False, 100), (True, 0), (True, None), (True, -1), (True, 0.001)])
def test_forward_election_needs_explicit_agreement_and_valid_amount(answer, amount):
    p, result = case()
    # A separately worked refundable credit makes the100 election valid
    # against line97, so the answer guard cannot hide behind that limit.
    result['california_540']['line_74'] = 5000
    p["flags"]["ca_applies_overpayment_forward"] = answer
    result["california_540"]["line_98"] = amount
    with pytest.raises(B.Blocked):
        B.build_california(result, {"state": law()}, [], p)


def test_forward_answer_agrees_with_the_election_after_cent_rounding():
    p, result = case()
    result['california_540']['line_98'] = 0.001
    # The form rounds this component to zero cents; a no stays true on rebuild.
    built = B.build_california(result, {'state': law()}, [], p)
    assert built['line_98'] == 0
    result['california_540'].update(built)
    assert B.build_california(result, {'state': law()}, [], p)['line_98'] == 0


def test_cli_settlement_receipt_and_edited_payment_profile(tmp_path):
    ws = workspace(tmp_path)
    p = json.loads((ws / 'taxpayer_profile.json').read_text())
    p["estimated_payments"] = {"state": [payment(amount=2000)], "ca_verified": True}
    p["flags"].update(ca_has_nonw2_payments=True, ca_applies_overpayment_forward=True)
    result = json.loads((ws / 'result.json').read_text())
    result["california_540"]["line_98"] = 1000
    write_secure(ws / 'taxpayer_profile.json', json.dumps(p))
    write_secure(ws / 'result.json', json.dumps(result))
    assert B._main(['--workspace', str(ws)]) == 0
    result = json.loads((ws / 'result.json').read_text())
    # 5,000 withholding +2,000 estimated -4,440 tax =2,560 overpaid;
    # 1,000 elected forward leaves1,560 refund, never another payment.
    ca = result['california_540']
    assert ca['line_72'] == 2000 and ca['line_97'] == 2560
    assert ca['line_98'] == 1000 and ca['line_115'] == 1560
    assert not any(s.startswith('CA balance due:') for s in filing_package.attachment_checklist(result))
    assert B._main(['--workspace', str(ws), '--check']) == 0
    assert receipt.verify(ws, required=(B.GATE,)).valid
    p['estimated_payments']['state'][0]['amount'] = 2500
    write_secure(ws / 'taxpayer_profile.json', json.dumps(p))
    assert not receipt.verify(ws, required=(B.GATE,)).valid
    assert B._main(['--workspace', str(ws), '--check']) == 2


def test_payment_disclosures_are_required_and_stale_ones_cannot_pass_check():
    p, result = paying([payment(date=None)])
    out = B.build_california(result, {'state': law()}, [], p)
    result['california_540'].update(out)
    result['meta']['limitations'] = [out['line_113_note']]
    assert 'meta.limitations (CA payments)' in B.california_differences(result, out)
    result['meta']['limitations'].append(out['ca_payment_note'])
    assert not B.california_differences(result, out)
    result['meta']['limitations'].append('CA payments: obsolete allocation warning')
    assert 'meta.limitations (stale CA payment disclosure)' in B.california_differences(result, out)


def prior(applied=500, *, elected=True, status='MFJ'):
    return R.PriorReturn(2025, {'meta': {'tax_year': 2025, 'filing_status': status},
        'california_540': {'ca_filing_status': status, 'line_97': 1000, 'line_98': applied}},
        {'flags': {'ca_applies_overpayment_forward': elected}})


def current(value=0, *, year=2026, status='MFJ'):
    return R.CurrentState(result={}, profile={'tax_year': year, 'filing_status': status,
        'prior_year': {'ca_estimated_payments_applied': value},
        'estimated_payments': {'state': [], 'ca_verified': True}}, tax_law={})


def changes(p, c):
    return R.compute_diff(p, c, {'carryforwards'}, {})['carryforwards']


def test_rollover_keeps_the_ca_credit_separate_and_invalidates_old_verification():
    p = prior()
    p.result['form_1040'] = {'line_36_applied_to_next_year': 200}
    rows = {c.json_path: c for c in changes(p, current())}
    assert rows['prior_year.ca_estimated_payments_applied'].prior_value == 500
    assert rows['prior_year.estimated_payments_applied'].prior_value == 200
    assert rows['estimated_payments.ca_verified'].prior_value is False
    assert rows['prior_year.ca_estimated_payments_applied'].action_suggested == 'apply'


def test_reviewed_2024_line_98_supplies_the_2025_opening_credit():
    # TY2024 Form 540 line 98 applies the elected amount to TY2025 estimates.
    p = prior()
    p.year = 2024
    p.result['meta']['tax_year'] = 2024
    rows = {c.json_path: c for c in changes(p, current(year=2025))}
    assert rows['prior_year.ca_estimated_payments_applied'].prior_value == 500
    assert rows['estimated_payments.ca_verified'].prior_value is False


def test_rollover_worked_zero_retires_a_stale_credit_and_status_changes_need_review():
    assert changes(prior(0, elected=False), current(0, status='Single')) == []
    rows = {c.json_path: c for c in changes(prior(0, elected=False), current(500))}
    assert rows['prior_year.ca_estimated_payments_applied'].prior_value == 0
    assert rows['prior_year.ca_estimated_payments_applied'].action_suggested == 'ask'
    rows = {c.json_path: c for c in changes(prior(), current(status='Single'))}
    assert rows['prior_year.ca_estimated_payments_applied'].action_suggested == 'ask'
    assert 'joint' in rows['prior_year.ca_estimated_payments_applied'].reason
    assert rows['estimated_payments.ca_verified'].prior_value is False
    # Equal dollar figures do not prove ownership after a joint-status change.
    rows = {c.json_path: c for c in changes(prior(), current(500, status='Single'))}
    assert rows['prior_year.ca_estimated_payments_applied'].action_suggested == 'ask'
    assert rows['estimated_payments.ca_verified'].prior_value is False


@pytest.mark.parametrize('bad', [None, True, -500, float('nan'), float('inf'), '500', 1001, 10 ** 400])
def test_rollover_refuses_missing_or_invalid_closing_elections(bad):
    with pytest.raises(R.RolloverError):
        changes(prior(bad), current())


def test_rollover_does_not_treat_a_malformed_archived_answer_as_no_election():
    with pytest.raises(R.RolloverError):
        changes(prior(None, elected='yes'), current())


def test_rollover_forward_answer_uses_the_same_cent_precision_as_the_return():
    with pytest.raises(R.RolloverError, match='contradicts'):
        changes(prior(0.001), current())
    assert changes(prior(0.001, elected=False), current()) == []


def test_rollover_never_credits_a_later_year_or_reuses_a_missing_source():
    with pytest.raises(R.RolloverError, match='immediately following'):
        changes(prior(), current(year=2027))
    p = prior(0, elected=False)
    del p.result['california_540']['line_98']
    with pytest.raises(R.RolloverError, match='missing'):
        changes(p, current(500))


def test_rollover_checks_both_source_and_destination_year_metadata():
    c = current()
    c.result['meta'] = {'tax_year': 2025}
    with pytest.raises(R.RolloverError, match='disagree'):
        changes(prior(), c)
    p = prior()
    p.result['meta']['tax_year'] = 2024
    with pytest.raises(R.RolloverError, match='Archived'):
        changes(p, current())


def test_apply_then_reconcile_rollover_makes_the_credit_available_once(tmp_path):
    p, result = case()
    result['meta']['tax_year'] = 2026
    p.update(tax_year=2026, estimated_payments={'state': [], 'ca_verified': True})
    p['flags']['ca_has_nonw2_payments'] = True
    p['prior_year'] = {'ca_estimated_payments_applied': 0}
    c = R.CurrentState(result=result, profile=p, tax_law={})
    paths = R.WritePaths(tmp_path / 'result.json', tmp_path / 'taxpayer_profile.json')
    write_secure(paths.result, json.dumps(result)); write_secure(paths.profile, json.dumps(p))
    R.apply_changes({'carryforwards': changes(prior(), c)}, {'carryforwards': 'apply'}, paths)
    written = json.loads(paths.profile.read_text())
    assert written['estimated_payments']['ca_verified'] is False
    with pytest.raises(ValueError, match='reconcile claimable'):
        B._ca_payments(written, 2026, 'single')
    written['estimated_payments']['ca_verified'] = True
    total, _ = B._ca_payments(written, 2026, 'single')
    assert total['total'] == total['prior_applied'] == 500


def test_headless_payment_and_election_answers_are_required_even_for_a_zero(tmp_path):
    write_secure(tmp_path / 'taxpayer_profile.json', json.dumps({'address': {'state': 'CA'}}))
    wanted = {'ca_state_payments', 'ca_overpayment_election'}
    assert wanted <= {a.id for a in answers.applicable(tmp_path)}
    write_secure(tmp_path / 'answers.md', '- **[ca_state_payments]** none confirmed\n')
    owed, _ = answers.missing(tmp_path)
    assert 'ca_overpayment_election' in {a.id for a in owed}
