"""Tests for agents/lib/withholding.py — federal withholding true-up."""
import datetime
import textwrap
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.lib import projection as proj
from agents.lib import withholding as wh
from tests.skill_text import skill_text
from tests.ny_fixtures import ny_state_cache


def test_read_liability_happy_path():
    result = {"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": 42000}}
    assert wh.read_liability(result, 2026) == 42000.0


def test_read_liability_wrong_year_raises():
    result = {"meta": {"tax_year": 2025}, "form_1040": {"line_24_total_tax": 42000}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)


def test_read_liability_missing_line_raises():
    result = {"meta": {"tax_year": 2026}, "form_1040": {}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)


def test_read_liability_negative_raises():
    result = {"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": -5}}
    with pytest.raises(wh.WithholdingError):
        wh.read_liability(result, 2026)


def test_read_liability_zero_is_valid():
    # A genuine $0-tax return (e.g. liability fully offset) is valid, not an error.
    # The tax_year guard already rejects uncomputed/blank templates (tax_year is null).
    result = {"meta": {"tax_year": 2026}, "form_1040": {"line_24_total_tax": 0}}
    assert wh.read_liability(result, 2026) == 0.0


def test_build_inputs_projects_at_current_pace():
    # as_of June 30, 2026 -> day 181 of 365 -> frac ~0.4959
    as_of = datetime.date(2026, 6, 30)
    proj_inputs = {
        "pay_frequency": "biweekly",
        "ytd": {"federal_withholding": 10000},
        "estimated_payments_made": {"federal": 2000},
    }
    inp = wh.build_inputs(proj_inputs, as_of, "biweekly")
    frac = as_of.timetuple().tm_yday / 365
    assert inp.ytd_withholding == 10000.0
    assert inp.estimates_paid == 2000.0
    assert inp.projected_full_year_withholding == round(10000 / frac, 2)
    assert inp.projected_paid == round(round(10000 / frac, 2) + 2000, 2)
    assert inp.paychecks_remaining == max(0, round(26 * (1 - frac)))
    assert inp.pay_frequency == "biweekly"


def test_build_inputs_missing_blocks_default_zero():
    as_of = datetime.date(2026, 6, 30)
    inp = wh.build_inputs({"pay_frequency": "monthly"}, as_of, "monthly")
    assert inp.ytd_withholding == 0.0
    assert inp.estimates_paid == 0.0
    assert inp.projected_full_year_withholding == 0.0


class _FakePrior:
    """Stand-in for projection.load_prior_baseline's PriorReturn.

    `ca_tax`/`ca_agi` add a COMPUTED california_540 section — without one
    `_prior_ca_tax_agi` returns (None, None) and the CA prior-year prong does
    not resolve, which several assertions here turn on.
    """
    def __init__(self, total_tax, agi, ca_tax=None, ca_agi=None):
        self.result = {"form_1040": {"line_24_total_tax": total_tax, "line_11_agi": agi}}
        if ca_tax is not None:
            self.result["california_540"] = {"ca_total_tax": ca_tax,
                                             "ca_agi": ca_agi}


def _proj_inputs(ytd_wh, est=0, prior_base=None, prior_agi=None):
    inputs = {
        "pay_frequency": "biweekly",
        "ytd": {"federal_withholding": ytd_wh},
        "estimated_payments_made": {"federal": est},
    }
    if prior_base is not None:
        inputs["prior_year_form_2210_tax_base"] = prior_base
    if prior_agi is not None:
        inputs["prior_year_form_2210_agi"] = prior_agi
    return inputs


def test_build_report_underpaid_both_targets():
    # Liability 40000; prior tax 30000, prior AGI 120000 (factor 1.00) -> safe harbor
    # required = min(0.9*40000=36000, 1.0*30000=30000) = 30000.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    frac = as_of.timetuple().tm_yday / 365
    # YTD withholding 10000 -> projected full-year = 10000/frac (~20165), no estimates.
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000, prior_base=30000), prior, 2026, as_of)
    proj_wh = round(10000 / frac, 2)
    assert r.liability == 40000.0
    assert r.degraded is False
    assert r.safe_harbor.required_annual == 30000.0
    assert r.penalty_target.name == "safe_harbor"
    assert r.penalty_target.target_amount == 30000.0
    assert r.penalty_target.shortfall == round(max(0.0, 30000 - proj_wh), 2)
    assert r.zero_target.name == "zero_balance"
    assert r.zero_target.target_amount == 40000.0
    assert r.zero_target.shortfall == round(max(0.0, 40000 - proj_wh), 2)
    # 4(c) per paycheck = shortfall / paychecks_remaining
    pr = max(0, round(26 * (1 - frac)))
    assert r.w4_penalty.per_paycheck == round(r.penalty_target.shortfall / pr, 2)
    assert r.zero_target.shortfall > r.penalty_target.shortfall


def test_early_estimates_are_applied_before_requesting_first_installments():
    """An estimate paid before Q1 offsets every cumulative checkpoint.

    With a $20,000 required annual payment and $8,000 already paid by March 1,
    federal Q1's cumulative $5,000 and California Q1's cumulative $6,000 are
    both satisfied. Splitting the $12,000 annual remainder by itself would
    instead request $3,000 and $3,600 at April 15 — payments not yet needed.
    """
    inputs = _ca_proj_inputs(fed_wh=0, state_wh=0, state_est=8000)
    inputs["estimated_payments_made"]["federal"] = 8000
    r = wh.build_report(
        _ca_result(fed_tax=22222.22, ca_tax=22222.22, ca_agi=200000),
        inputs, None, 2026, datetime.date(2026, 3, 1))

    assert r.safe_harbor.required_annual == 20000.0
    assert r.safe_harbor.shortfall == 12000.0
    assert [(v.quarter, v.amount) for v in r.vouchers_penalty] == [
        (1, 0.0), (2, 2000.0), (3, 5000.0), (4, 5000.0)]
    assert r.ca.safe_harbor.required_annual == 20000.0
    assert r.ca.safe_harbor.shortfall == 12000.0
    assert [(v.quarter, v.amount) for v in r.ca.vouchers_penalty] == [
        (1, 0.0), (2, 6000.0), (3, 0.0), (4, 6000.0)]
    assert r.voucher_catchup_note is None
    assert r.ca.voucher_catchup_note is None


def test_build_report_covered_when_pace_exceeds_target():
    # High YTD withholding so projected full-year covers even the zero-balance target.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(30000, prior_base=30000), prior, 2026, as_of)
    assert r.penalty_target.covered is True
    assert r.penalty_target.shortfall == 0.0
    assert r.zero_target.covered is True
    assert r.w4_penalty.per_paycheck == 0.0


def test_the_header_does_not_call_an_archive_absent_that_the_ca_block_uses():
    """"No federal line-8 base" is not "no archive", and the header said so.

    `degraded` now tracks only whether an explicit
    `prior_year_form_2210_tax_base` was supplied — the common NO. With an
    archive present the header still printed "Prior-year baseline: none —
    DEGRADED MODE" while the CA table below computed a prior-year-based CA
    harbor from that very archive. `ProjectionReport` grew `has_prior_archive`
    to separate the two; `TrueUpReport` did not.
    """
    prior = _FakePrior(30000, 120000, ca_tax=7000, ca_agi=180000)
    r = wh.build_report(_ca_result(fed_tax=40000.0, ca_tax=9000.0,
                                   ca_agi=200000),
                        _proj_inputs(10000), prior, 2026,
                        datetime.date(2026, 6, 30))
    assert r.degraded is True and r.baseline_source is None
    assert r.ca is not None and r.ca.safe_harbor.prior_based is not None, (
        "the fixture no longer resolves a CA prior-year prong, so the clause "
        "this pins would be a false claim rather than a true one")
    assert r.has_prior_archive is True, (
        "the withholding report cannot tell 'no explicit base' from "
        "'no archive', which is the distinction the header turns on")
    md = wh.render_markdown(r)
    line = next(ln for ln in md.splitlines()
                if ln.startswith("- Prior-year baseline:"))
    assert "prior return IS present" in line, (
        f"the header reads {line!r} with an archive present and in use by the "
        f"CA block below it")
    assert wh._report_to_dict(r)["has_prior_archive"] is True

    # Without an archive the plain wording must survive, or this has merely
    # replaced one unconditional claim with another.
    bare = wh.build_report(
        {"meta": {"tax_year": 2026, "filing_status": "single"},
         "form_1040": {"line_24_total_tax": 40000}},
        _proj_inputs(10000), None, 2026, datetime.date(2026, 6, 30))
    assert bare.has_prior_archive is False
    assert "prior return IS present" not in wh.render_markdown(bare)

    # And an archive that resolves NO CA prong must not claim one either: a
    # federal-only return renders no CA section at all, so the clause would
    # point at a harbor the report does not contain.
    fed_only = wh.build_report(
        {"meta": {"tax_year": 2026, "filing_status": "single"},
         "form_1040": {"line_24_total_tax": 40000}},
        _proj_inputs(10000), _FakePrior(30000, 120000), 2026,
        datetime.date(2026, 6, 30))
    assert fed_only.has_prior_archive is True and fed_only.ca is None
    assert "prior return IS present" not in wh.render_markdown(fed_only), (
        "the clause names a CA harbor in a report with no CA section")


def test_has_prior_archive_is_a_documented_payload_contract():
    """It drives no renderer branch here, so the docs are what keep it real.

    The projection field of the same name gates a warning; this one is read by
    nothing in `render_markdown` — the header clarifier gates on the stronger
    `ca_prior_resolved`, and there is no `recurring_from_prior` on this side to
    warn about. That left it looking like spare weight a maintainer could
    delete, breaking a payload contract the skill implied but never stated.
    Stating it is the fix, so the statement is pinned.
    """
    r = wh.build_report(_ca_result(fed_tax=40000.0, ca_tax=9000.0,
                                   ca_agi=200000),
                        _proj_inputs(10000),
                        _FakePrior(30000, 120000, ca_tax=7000, ca_agi=180000),
                        2026, datetime.date(2026, 6, 30))
    assert "has_prior_archive" in wh._report_to_dict(r), (
        "the field left the payload; the skill still tells the agent to read it")

    doc = skill_text("tax-withholding")
    assert "`has_prior_archive`" in doc, (
        "tax-withholding/SKILL.md does not document `has_prior_archive`, so "
        "the only thing keeping an unread field alive is that nobody noticed "
        "it — which is how the JSON contract gets deleted by a tidy-up")
    at = doc.index("`has_prior_archive`")
    entry = doc[at:at + 900].lower()
    # The two mistakes the field invites, both named in the entry.
    assert "degraded" in entry, (
        "the entry does not separate it from `safe_harbor.degraded`, which is "
        "the confusion the field exists to prevent")
    assert "prior_based" in entry, (
        "the entry does not warn that archive PRESENCE is not a computed CA "
        "prior-year harbor — the weaker fact reading as the stronger one")


def test_build_report_degraded_mode_uses_90pct_only():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    r = wh.build_report(result, _proj_inputs(5000), None, 2026, as_of)
    assert r.degraded is True
    assert r.baseline_source is None
    # required = 0.90 * 40000 = 36000 (no prior-year test available)
    assert r.safe_harbor.required_annual == 36000.0


def test_build_report_prior_factor_110_above_threshold():
    # An explicit tax base without its paired AGI must not borrow the
    # archive's — tax-amend can leave that AGI belonging to a different
    # return — so the paired AGI has to be supplied explicitly here too.
    # prior AGI 200000 (single) > 150000 -> factor 1.10 -> prior_based = 1.10*30000 = 33000
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 100000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 200000)
    r = wh.build_report(
        result, _proj_inputs(1000, prior_base=30000, prior_agi=200000),
        prior, 2026, as_of)
    # min(0.9*100000=90000, 1.10*30000=33000) = 33000
    assert r.safe_harbor.required_annual == 33000.0


def test_render_markdown_contains_key_sections():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000, prior_base=30000), prior, 2026, as_of)
    md = wh.render_markdown(r)
    assert "# Withholding True-Up — TY2026 (as of 06-30)" in md
    assert "## Liability & payments" in md
    assert "## Gap to targets" in md
    assert "## Recommended actions" in md
    assert "W-4 line 4(c)" in md
    assert "1040-ES" in md


def test_render_markdown_degraded_note():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    r = wh.build_report(result, _proj_inputs(5000), None, 2026, as_of)
    md = wh.render_markdown(r)
    assert "DEGRADED MODE" in md


def test_report_to_dict_roundtrips_json():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000, prior_base=30000), prior, 2026, as_of)
    d = wh._report_to_dict(r)
    s = json.dumps(d, default=str)  # must be JSON-serializable
    back = json.loads(s)
    assert back["liability"] == 40000.0
    assert back["penalty_target"]["name"] == "safe_harbor"
    assert back["zero_target"]["name"] == "zero_balance"
    assert isinstance(back["vouchers_penalty"], list)
    assert isinstance(back["vouchers_zero"], list)


def test_vouchers_zero_exceeds_penalty_when_liability_above_safe_harbor():
    # Prior tax 30000 < liability 40000 -> safe harbor required (30000) < full liability.
    # The zero-balance voucher schedule must demand more per quarter than the
    # penalty-avoidance one, so the report offers an actionable path to zero balance.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 6, 30)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(1000, prior_base=30000), prior, 2026, as_of)
    assert r.safe_harbor.required_annual < r.liability
    pen_total = sum(v.amount for v in r.vouchers_penalty)
    zero_total = sum(v.amount for v in r.vouchers_zero)
    assert zero_total > pen_total
    # The two schedules share identical quarters/due dates (same as_of).
    assert [v.due_date for v in r.vouchers_penalty] == [v.due_date for v in r.vouchers_zero]


def test_vouchers_zero_covers_full_gap_when_no_paychecks_remain():
    # as_of late in year -> 0 paychecks remaining -> W-4 4(c) is $0 for both targets,
    # so vouchers are the only lever. The zero-balance schedule must sum to the
    # zero-balance shortfall (the full gap), not merely the safe-harbor gap.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    as_of = datetime.date(2026, 12, 31)
    prior = _FakePrior(30000, 120000)
    r = wh.build_report(result, _proj_inputs(10000, prior_base=30000), prior, 2026, as_of)
    assert r.inputs.paychecks_remaining == 0
    assert r.w4_zero.per_paycheck == 0.0
    zero_total = round(sum(v.amount for v in r.vouchers_zero), 2)
    assert zero_total == r.zero_target.shortfall


FIX = Path(__file__).resolve().parent / "fixtures" / "withholding"


def test_main_json_output_ok(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "06-30", "--today", "2026-07-01", "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["year"] == 2026
    assert out["liability"] == 40000.0
    assert out["degraded"] is False
    assert out["baseline_source"] == \
        "projection_inputs.json completed Form 2210 line-8 base"


def test_main_markdown_output_ok(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "06-30", "--today", "2026-07-01",
    ])
    assert rc == 0
    assert "# Withholding True-Up — TY2026" in capsys.readouterr().out


def test_main_wrong_year_returns_1(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2025",
        "--as-of", "06-30", "--today", "2026-07-01",
    ])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err


def test_main_future_as_of_returns_1(capsys):
    rc = wh._main([
        "--result", str(FIX / "result.json"),
        "--inputs", str(FIX / "projection_inputs.json"),
        "--workspace", str(FIX), "--year", "2026",
        "--as-of", "12-31", "--today", "2026-07-01",
    ])
    assert rc == 1
    assert "future" in capsys.readouterr().err.lower()


def _closed_year_workspace(tmp_path, year):
    """The fixture workspace re-dated to `year`, with no stored `as_of_date`.

    Dropping that key is the whole point: with one present the default chain
    never runs, so a fixture that carries it cannot see this bug at all.
    """
    result = json.loads((FIX / "result.json").read_text())
    result["meta"]["tax_year"] = year
    (tmp_path / "result.json").write_text(json.dumps(result))
    inputs = json.loads((FIX / "projection_inputs.json").read_text())
    inputs.pop("as_of_date")
    inputs["tax_year"] = year
    (tmp_path / "projection_inputs.json").write_text(json.dumps(inputs))
    return ["--result", str(tmp_path / "result.json"),
            "--inputs", str(tmp_path / "projection_inputs.json"),
            "--workspace", str(tmp_path), "--year", str(year), "--json"]


def test_the_true_up_carries_the_prior_prong_precondition_too():
    """The other renderer of the same prior-year row.

    Both reports print `{factor}% of prior-year tax` off the same SafeHarbor,
    so §6654(d)(1)(B)'s closing-text precondition has to travel with it in
    both places or the caveat has a hole exactly where a workspace user lands
    — /tax-safe-harbor's step 0 hands the computed-liability question HERE.
    """
    from agents.lib.projection import _PRIOR_PRONG_PRECONDITION_NOTE
    result = json.loads((FIX / "result.json").read_text())
    result["meta"]["tax_year"] = 2026
    inputs = json.loads((FIX / "projection_inputs.json").read_text())
    inputs["tax_year"] = 2026
    inputs["prior_year_form_2210_tax_base"] = 40000.0
    md_report = wh.build_report(
        result=result, proj_inputs=inputs, year=2026,
        as_of=datetime.date(2026, 6, 30), prior=None)
    md = wh.render_markdown(md_report)
    assert not md_report.safe_harbor.degraded, (
        "fixture drift: the prior prong must be IN PLAY here, or the test "
        "proves nothing about the gate")
    assert _PRIOR_PRONG_PRECONDITION_NOTE in md
    assert "6654(d)(1)(B)" in md

    # Degraded: no base, no prong, and so nothing for the caveat to qualify.
    del inputs["prior_year_form_2210_tax_base"]
    bare = wh.build_report(result=result, proj_inputs=inputs, year=2026,
                           as_of=datetime.date(2026, 6, 30), prior=None)
    assert bare.safe_harbor.degraded, "fixture drift: expected degraded mode"
    assert _PRIOR_PRONG_PRECONDITION_NOTE not in wh.render_markdown(bare)

    # A CONFIRMED $0 base: the prong IS resolved, so the degraded gate above
    # does not reach it, and the note is false of both routes to it —
    # §6654(e)(2) requires no filed return at all. Suppressed by the shared
    # predicate rather than by a second copy of the rule here, for the reason
    # the note itself is shared: a suppression in one renderer only is the
    # same drift as a caveat in one renderer only.
    for flag in ("prior_year_zero_liability_confirmed",
                 "prior_year_return_filed_12_months_confirmed"):
        inputs["prior_year_form_2210_tax_base"] = 0
        inputs.pop("prior_year_zero_liability_confirmed", None)
        inputs.pop("prior_year_return_filed_12_months_confirmed", None)
        inputs[flag] = True
        zero = wh.build_report(result=result, proj_inputs=inputs, year=2026,
                               as_of=datetime.date(2026, 6, 30), prior=None)
        assert not zero.safe_harbor.degraded, (
            f"fixture drift: {flag} must leave the prong resolved")
        assert zero.safe_harbor.prior_tax == 0
        assert _PRIOR_PRONG_PRECONDITION_NOTE not in wh.render_markdown(zero), (
            f"the caveat claims an unfiled prior year makes the figure too "
            f"LOW; confirmed via {flag} the $0 target is correct as printed")


def test_the_withholding_cli_shares_the_year_guard(tmp_path, capsys):
    """The other caller of `_require_inputs_tax_year`.

    Both CLIs read the same projection_inputs.json and the same four
    year-bound prior-year fields, so a guard implemented in one of them is a
    guard with a hole in it — this side previously compared `tax_year` only
    when the file DECLARED one, which the template's null default defeats.
    The rule is shared, and so is this pin: a yearless file that answers a
    year-bound field refuses; the same file with the year declared runs.
    """
    result = json.loads((FIX / "result.json").read_text())
    result["meta"]["tax_year"] = 2026
    (tmp_path / "result.json").write_text(json.dumps(result))
    inputs = json.loads((FIX / "projection_inputs.json").read_text())
    inputs.pop("tax_year", None)
    inputs["prior_year_form_2210_tax_base"] = 40000.0
    (tmp_path / "projection_inputs.json").write_text(json.dumps(inputs))
    argv = ["--result", str(tmp_path / "result.json"),
            "--inputs", str(tmp_path / "projection_inputs.json"),
            "--workspace", str(tmp_path), "--year", "2026", "--json",
            "--today", "2026-07-01"]

    assert wh._main(argv) == 1
    err = capsys.readouterr().err
    assert "prior_year_form_2210_tax_base" in err and "tax_year" in err, err

    inputs["tax_year"] = 2026
    (tmp_path / "projection_inputs.json").write_text(json.dumps(inputs))
    assert wh._main(argv) == 0, capsys.readouterr().err

    # And a declared mismatch still refuses, as it always did.
    inputs["tax_year"] = 2025
    (tmp_path / "projection_inputs.json").write_text(json.dumps(inputs))
    assert wh._main(argv) == 1
    assert "TY2025" in capsys.readouterr().err


def test_main_defaults_a_closed_year_to_december_31(tmp_path, capsys):
    """`--year 2025` run in January 2026 used to annualize five days.

    The `as_of > today` guard cannot fire here — 2025-01-05 is not in the
    future of 2026-01-05 — so nothing downstream caught it, and the report
    divided a full year's withholding by 5/365 and printed the resulting
    shortfall as a payment to make. The CLI default is where it enters; the
    guard next to it never could.
    """
    rc = wh._main(_closed_year_workspace(tmp_path, 2025)
                  + ["--today", "2026-01-05"])
    assert rc == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out)["as_of"] == "12-31"


def test_main_preserves_a_stored_as_of_on_a_closed_year(tmp_path, capsys):
    """The true-up keeps the date attached to the stored YTD actuals."""
    result = json.loads((FIX / "result.json").read_text())
    result["meta"]["tax_year"] = 2025
    result["california_540"] = {"ca_total_tax": 8000, "ca_agi": 100000}
    (tmp_path / "result.json").write_text(json.dumps(result))
    inputs = json.loads((FIX / "projection_inputs.json").read_text())
    assert inputs["as_of_date"] == "06-30", (
        "the fixture no longer carries a stored mid-year stamp, so this test "
        "passes without exercising the override it exists for")
    inputs["tax_year"] = 2025
    inputs.pop("prior_year_form_2210_tax_base", None)
    inputs["ytd"]["state_withholding"] = 2000
    (tmp_path / "projection_inputs.json").write_text(json.dumps(inputs))

    rc = wh._main(["--result", str(tmp_path / "result.json"),
                   "--inputs", str(tmp_path / "projection_inputs.json"),
                   "--workspace", str(tmp_path), "--year", "2025",
                   "--json", "--today", "2026-01-05"])
    assert rc == 0, capsys.readouterr().err
    data = json.loads(capsys.readouterr().out)
    assert data["as_of"] == "06-30"
    assert data["stale_as_of_note"] is None
    assert data["safe_harbor"]["shortfall"] == 15834.25, (
        f"shortfall is {data['safe_harbor']['shortfall']} — relabeling the "
        "same half-year actuals as 12-31 gave $26,000.00")
    assert data["inputs"]["paychecks_remaining"] == 0
    assert data["w4_penalty"]["paychecks_remaining"] == 0
    assert data["w4_penalty"]["per_paycheck"] == 0.0
    assert data["w4_zero"]["paychecks_remaining"] == 0
    assert data["w4_zero"]["per_paycheck"] == 0.0
    assert data["ca"]["de4_penalty"]["paychecks_remaining"] == 0
    assert data["ca"]["de4_penalty"]["per_paycheck"] == 0.0
    assert data["ca"]["de4_zero"]["paychecks_remaining"] == 0
    assert data["ca"]["de4_zero"]["per_paycheck"] == 0.0
    assert "No paychecks remain in TY2025" in data["w4_lever_note"]

    sensitive = "-".join(("123", "45", "6789"))
    inputs["as_of_date"] = sensitive
    (tmp_path / "projection_inputs.json").write_text(json.dumps(inputs))
    common = ["--result", str(tmp_path / "result.json"),
              "--inputs", str(tmp_path / "projection_inputs.json"),
              "--workspace", str(tmp_path), "--year", "2025",
              "--today", "2026-01-05"]
    for output_args in (["--json"], []):
        rc = wh._main(common + output_args)
        captured = capsys.readouterr()
        assert rc == 0, captured.err
        assert sensitive not in captured.out and sensitive not in captured.err
        assert "Invalid stored as-of date omitted" in captured.out


def test_main_still_defaults_an_open_year_to_today(tmp_path, capsys):
    """The counterweight: freezing the CURRENT year at 12-31 would annualize
    nothing and report a full year of withholding as already banked."""
    rc = wh._main(_closed_year_workspace(tmp_path, 2026)
                  + ["--today", "2026-01-05"])
    assert rc == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out)["as_of"] == "01-05"


# ---------------------------------------------------------------------------
# E7: California true-up (Form 540 + FTB 5805 + 540-ES 30/40/0/30)
# ---------------------------------------------------------------------------

class _FakeCaPrior:
    def __init__(self, total_tax, agi, ca_tax, ca_agi):
        self.result = {
            "form_1040": {"line_24_total_tax": total_tax, "line_11_agi": agi},
            "california_540": {"ca_total_tax": ca_tax, "ca_agi": ca_agi},
        }


def _ca_result(ca_tax=8000, ca_agi=200000, fed_tax=40000):
    return {"meta": {"tax_year": 2026, "filing_status": "single"},
            "form_1040": {"line_24_total_tax": fed_tax},
            "california_540": {"ca_total_tax": ca_tax, "ca_agi": ca_agi}}


def _ca_proj_inputs(fed_wh=10000, state_wh=1000, state_est=0,
                    prior_base=None):
    inputs = {
        "pay_frequency": "biweekly",
        "ytd": {"federal_withholding": fed_wh, "state_withholding": state_wh},
        "estimated_payments_made": {"federal": 0, "state": state_est},
    }
    if prior_base is not None:
        inputs["prior_year_form_2210_tax_base"] = prior_base
    return inputs


def test_read_ca_inputs_happy_and_absent():
    assert wh.read_ca_inputs(_ca_result()) == (8000.0, 200000.0)
    assert wh.read_ca_inputs({"meta": {}}) is None


def test_read_ca_inputs_requires_tax_and_agi():
    with pytest.raises(wh.WithholdingError):
        wh.read_ca_inputs({"california_540": {"ca_agi": 100000}})
    # Missing ca_agi would silently disable the R&TC §19136.3 high-income
    # check — the understating direction — so it hard-errors too.
    with pytest.raises(wh.WithholdingError):
        wh.read_ca_inputs({"california_540": {"ca_total_tax": 8000}})
    with pytest.raises(wh.WithholdingError):
        wh.read_ca_inputs({"california_540": "corrupt"})
    with pytest.raises(wh.WithholdingError):
        wh.read_ca_inputs({"california_540": {"ca_total_tax": -1, "ca_agi": 0}})


def test_read_ca_inputs_negative_agi_valid():
    # A loss year: negative CA AGI is legitimate (just not high-income).
    assert wh.read_ca_inputs(
        {"california_540": {"ca_total_tax": 0, "ca_agi": -5000}}) == (0.0, -5000.0)


def test_build_report_ca_block_hand_computed():
    # as_of Jun 30: frac = 181/365. YTD CA withholding 1000 ->
    # projected = round(1000 * 365/181, 2) = 2016.57; paid = 2016.57.
    # Prior CA tax 6000, prior CA AGI 120000 (<=150K -> 1.00) -> prior_based
    # 6000; pct90 = 7200 -> required 6000 -> shortfall 3983.43.
    # Zero shortfall = 8000 - 2016.57 = 5983.43.
    as_of = datetime.date(2026, 6, 30)
    frac = as_of.timetuple().tm_yday / 365
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), prior, 2026, as_of)
    assert r.ca is not None
    proj_wh = round(1000 / frac, 2)
    assert r.ca.liability == 8000.0
    assert r.ca.projected_full_year_withholding == proj_wh
    assert r.ca.safe_harbor.required_annual == 6000.0
    assert r.ca.safe_harbor.shortfall == round(6000 - proj_wh, 2)
    assert r.ca.zero_shortfall == round(8000 - proj_wh, 2)
    # Jun 30 -> Q2 gone; open Q3 (0 weight) + Q4 -> whole shortfall on Q4.
    assert [(v.quarter, v.amount) for v in r.ca.vouchers_penalty] == [
        (3, 0.0), (4, r.ca.safe_harbor.shortfall)]
    assert [(v.quarter, v.amount) for v in r.ca.vouchers_zero] == [
        (3, 0.0), (4, r.ca.zero_shortfall)]
    # DE 4 per-paycheck over the same remaining-paychecks base as federal.
    pr = r.inputs.paychecks_remaining
    assert r.ca.de4_penalty.per_paycheck == round(r.ca.safe_harbor.shortfall / pr, 2)
    assert r.ca.de4_zero.per_paycheck == round(r.ca.zero_shortfall / pr, 2)


def test_build_report_ca_none_without_section():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r = wh.build_report(result, _ca_proj_inputs(), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca is None
    md = wh.render_markdown(r)
    assert "California true-up" not in md
    assert "covered for CA and NY returns" in md


def test_build_report_ca_high_income_90_only():
    # Current-year CA AGI $1.2M -> R&TC §19136.3 forbids the prior-year
    # harbor even though prior CA tax (1000) would be far cheaper.
    prior = _FakeCaPrior(30000, 120000, 1000, 900000)
    r = wh.build_report(_ca_result(ca_tax=100000, ca_agi=1_200_000),
                        _ca_proj_inputs(state_wh=20000), prior, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca.safe_harbor.high_income_90_only is True
    assert r.ca.safe_harbor.required_annual == 90000.0
    assert "R&TC §19136.3" in wh.render_markdown(r)


def test_build_report_ca_degraded_without_prior():
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca.safe_harbor.degraded is True
    assert r.ca.safe_harbor.required_annual == round(0.9 * 8000, 2)


def test_render_markdown_ca_sections():
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), prior, 2026,
                        datetime.date(2026, 6, 30))
    md = wh.render_markdown(r)
    assert "## California true-up (Form 540)" in md
    assert "CA safe harbor (FTB 5805)" in md
    assert "DE 4 per-paycheck extra CA withholding" in md
    assert "CA 540-ES voucher schedule" in md
    # The statutory split is NOT in the heading. This table has no Share
    # column, so the heading was the only place the split was stated —
    # and it was stated above amounts that follow a different one. This
    # assertion used to pin the contradicting form, so the two modules
    # asserted opposite contracts for the same string.
    assert "CA 540-ES voucher schedule (30%" not in md
    assert "the statutory weighting is 30% / 40% / 0% / 30%" in md
    assert "there is no September (Q3) payment" in md
    assert "R&TC §19011.5" in md  # mandatory e-pay caveat


def test_report_to_dict_includes_ca_and_roundtrips():
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), prior, 2026,
                        datetime.date(2026, 6, 30))
    back = json.loads(json.dumps(wh._report_to_dict(r), default=str))
    assert back["ca"]["liability"] == 8000.0
    assert back["ca"]["safe_harbor"]["required_annual"] == 6000.0
    assert isinstance(back["ca"]["vouchers_penalty"], list)
    # Non-CA report serializes ca as null.
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r2 = wh.build_report(result, _ca_proj_inputs(), None, 2026,
                         datetime.date(2026, 6, 30))
    assert wh._report_to_dict(r2)["ca"] is None


def test_read_ca_inputs_scaffold_section_skipped():
    """P1: result_template.json ships an ALL-ZERO california_540 scaffold and
    /tax-setup copies it before /tax-compute step 10 runs — it must NOT
    become a confident 'CA tax $0 / covered' block."""
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000},
              "california_540": {"_include_if": "state is CA",
                                 "ca_filing_status": None,
                                 "ca_standard_deduction": 0,
                                 "ca_agi": 0, "ca_total_tax": 0,
                                 "ca_withholding_box17": 0, "note": None}}
    assert wh.read_ca_inputs(result) is None
    r = wh.build_report(result, _ca_proj_inputs(), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca is None
    assert "covered for CA and NY returns" in wh.render_markdown(r)


def test_read_ca_inputs_zero_tax_computed_return_still_counts():
    # A genuine $0-tax CA return has non-zero ca_agi → present, computed.
    result = {"california_540": {"ca_total_tax": 0, "ca_agi": 15000}}
    assert wh.read_ca_inputs(result) == (0.0, 15000.0)


def test_the_federal_voucher_table_carries_the_catchup_note_too():
    """This renderer shipped the 1040-ES table without the note beside it.

    `projection.render_markdown` prints the same note under the
    same schedule from the same `compute_estimated_vouchers`, whose split of
    the ANNUAL remainder under-funds the earliest still-open deadline. Here
    the table was bare — and /tax-safe-harbor step 0 hands a workspace user to
    BOTH tools, so half the handoff carried an under-stated earliest voucher
    with nothing marking it as one. Under-stating what must be paid by a date
    is the direction the toolkit is least allowed to move in.
    """
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r = wh.build_report(result, _proj_inputs(10000), _FakePrior(30000, 120000),
                        2026, datetime.date(2026, 6, 30))
    md = wh.render_markdown(r)
    assert "### 1040-ES voucher schedule (fallback)" in md, (
        "the federal voucher table stopped rendering")
    assert wh._FEDERAL_CATCHUP.text in md, (
        "the federal voucher table renders without the catch-up note that "
        "`projection.render_markdown` carries under the identical schedule")


def test_the_ca_catchup_note_is_gated_here_as_it_is_in_projection():
    """It was unconditional in this renderer after the other one was gated.

    Two renderers reading one note is one place for the fix and two places
    for the bug. With the CA harbor covered the schedule is all zeros, and
    "pay catch-up amounts NOW rather than at the printed dates" then asks for
    money that avoids no penalty — the OVER-stating direction AGENTS.md makes
    a hard error. `/tax-project` stopped saying it; `/tax-withholding` went on
    saying it about the same taxpayer.
    """
    r = wh.build_report(_ca_result(), _ca_proj_inputs(state_wh=20000),
                        _FakeCaPrior(30000, 120000, 6000, 120000), 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca is not None and r.ca.safe_harbor.covered, (
        "the fixture no longer covers the CA harbor, so it cannot pin the "
        "all-zero schedule case")
    assert all(v.amount == 0 for v in r.ca.vouchers_penalty), (
        "the covered fixture still produces non-zero CA vouchers")
    assert wh._CA_CATCHUP.text not in wh.render_markdown(r), (
        "the CA catch-up note renders against an all-zero CA schedule — a "
        "payment directive with nothing behind it")


def test_the_ca_de_minimis_silences_the_whole_ca_payment_advice():
    """The report that said "no penalty applies" and "pay now" in one section.

    Reproduces the shipped contradiction end to end. `compute_ca_safe_harbor`
    zeroed its shortfall under R&TC §19136 so the DE 4 figure would not demand
    payments that avoid nothing, but `compute_ca_estimated_vouchers` took
    `required_annual` and recomputed a shortfall of its own — so the same CA
    block printed the de-minimis status line, a $200.25 January instalment and
    a note telling the taxpayer to pay catch-up amounts NOW. Over-stating what
    must be paid is the hard-error direction; arriving as a banner over a
    voucher rather than as a wrong total does not soften it.

    $1,000 of CA tax against $347 YTD withholding, projected to $699.75 for the
    year, leaves $300.25 — under the $500 floor.
    """
    r = wh.build_report(_ca_result(ca_tax=1000, ca_agi=20000),
                        _ca_proj_inputs(state_wh=347), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca is not None and r.ca.safe_harbor.de_minimis, (
        "the fixture no longer lands under the CA de minimis, so it cannot "
        "pin the contradiction")
    assert all(v.amount == 0 for v in r.ca.vouchers_penalty), (
        "the CA penalty schedule still demands an instalment under §19136 — "
        "the exact payment the harbor's zeroed shortfall exists to suppress")
    assert r.ca.voucher_catchup_note is None, (
        "the CA catch-up note fires under the de minimis")
    md = wh.render_markdown(r)
    ca_section = md.split("## California", 1)
    assert len(ca_section) == 2, "the CA section stopped rendering"
    assert "no CA underpayment penalty applies" in ca_section[1], (
        "the de-minimis status line is gone, so this no longer pins the two "
        "claims appearing together")
    assert wh._CA_CATCHUP.text not in ca_section[1], (
        "one CA section says no penalty can apply AND directs an immediate "
        "catch-up payment")
    # The zero-balance column is untouched: the tax is still owed at filing,
    # and suppressing THAT would be the under-stating direction.
    assert any(v.amount > 0 for v in r.ca.vouchers_zero), (
        "the de minimis silenced the zero-balance schedule too — it excuses "
        "the penalty, not the tax")


def test_the_federal_de_minimis_silences_the_federal_payment_advice():
    """§6654(e)(1), the same fix on the side that had no floor at all.

    `compute_form_2210` reports `no_penalty` on these figures. Before the
    floor reached `compute_safe_harbor`, this renderer showed a shortfall,
    a voucher schedule for it and the catch-up note above them.
    """
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 5000}}
    r = wh.build_report(result, _proj_inputs(2000), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.safe_harbor.de_minimis, (
        "$2,000 YTD withholding projects to $4,033.15 for the year, leaving "
        "$966.85 of $5,000 — the fixture no longer lands under the floor")
    assert r.penalty_target.covered and r.penalty_target.shortfall == 0.0
    assert all(v.amount == 0 for v in r.vouchers_penalty), (
        "the federal penalty schedule still demands payments under "
        "§6654(e)(1)")
    assert r.voucher_catchup_note is None
    md = wh.render_markdown(r)
    # The SHARED sentence, not a second copy of it: `_federal_status` writes
    # /tax-project's status cell too, and two reports explaining one statute
    # in two wordings is the drift this asserts against.
    assert wh._federal_status(r.safe_harbor, tax_is_actual=True) in md, (
        "the safe-harbor row reads covered against a visible balance owed "
        "with nothing saying why")
    assert "6654(e)(1)" in wh._federal_status(r.safe_harbor, tax_is_actual=True), (
        "the shared status sentence no longer names the statute, so the "
        "assertion above would pass on a bare 'covered'")
    # ...and `annualized` is the ONE thing the two reports do not share. Here
    # the tax is `read_liability` — form_1040 line 24, which this report's own
    # Assumptions call "the **actual computed** tax" — and the annualized side
    # is the withholding pace. /tax-project's wording points the reader at more
    # tax than projected, which in THIS report is the one input that cannot
    # move.
    assert "more tax than projected" not in md, (
        "the true-up prints /tax-project's cliff sentence, telling a reader "
        "whose tax is already computed to watch it for movement")
    assert "WITHHOLDING pace" in md, (
        "the cliff sentence does not name the side that is actually "
        "annualized here")
    assert any(v.amount > 0 for v in r.vouchers_zero), (
        "the zero-balance schedule was silenced too — the tax is still due")


def test_the_catchup_notes_reach_this_json_payload_too():
    """/tax-withholding reads `--json` before it renders, like /tax-project."""
    r = wh.build_report(_ca_result(), _ca_proj_inputs(),
                        _FakeCaPrior(30000, 120000, 6000, 120000), 2026,
                        datetime.date(2026, 6, 30))
    payload = wh._report_to_dict(r)
    assert payload["voucher_catchup_note"] == r.voucher_catchup_note
    assert payload["ca"]["voucher_catchup_note"] == r.ca.voucher_catchup_note
    assert r.voucher_catchup_note, (
        "the fixture no longer fires the federal note, so this shows nothing")
    assert r.voucher_catchup_note in wh.render_markdown(r), (
        "the renderer prints a note the payload does not carry")


def test_render_markdown_ca_catchup_and_de_minimis_notes():
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    r = wh.build_report(_ca_result(), _ca_proj_inputs(), prior, 2026,
                        datetime.date(2026, 6, 30))
    md = wh.render_markdown(r)
    # Anchored to the CA SECTION and to the CA note's own text. Both notes end
    # "pay catch-up amounts NOW", and this renderer gained the federal one, so
    # a bare substring search over the whole document passed on the federal
    # note with the CA render block deleted — the test-vacuity class PR #74
    # was opened to close.
    ca_section = md.split("## California", 1)
    assert len(ca_section) == 2, "the CA section stopped rendering"
    assert wh._CA_CATCHUP.text in ca_section[1], (
        "the CA catch-up note is missing from the CA section")
    assert wh._FEDERAL_CATCHUP.text not in ca_section[1], (
        "the FEDERAL note renders inside the CA section — the two carry "
        "different weights and different step references, and this test "
        "cannot tell them apart by their shared closing sentence")
    assert "5805F" in md  # farmers/fishermen not-modeled caveat
    # De minimis: CA tax 1000, projected withholding ~700 (347/frac) ->
    # required 900 (degraded 90%) leaves a raw gap, but tax minus
    # withholding ~300 < $500 -> penalty target COVERED (shortfall zeroed,
    # DE 4 penalty figure $0) while the zero-balance target still shows
    # the ~$300 due.
    r2 = wh.build_report(_ca_result(ca_tax=1000, ca_agi=20000),
                         _ca_proj_inputs(state_wh=347), None, 2026,
                         datetime.date(2026, 6, 30))
    assert r2.ca.safe_harbor.de_minimis is True
    assert r2.ca.safe_harbor.shortfall == 0.0
    assert r2.ca.safe_harbor.covered is True
    assert r2.ca.de4_penalty.per_paycheck == 0.0
    assert r2.ca.zero_shortfall > 0
    assert "no CA underpayment penalty applies" in wh.render_markdown(r2)


def test_build_report_federal_prior_agi_missing_note():
    class _PriorNoAgi:
        result = {"form_1040": {"line_24_total_tax": 30000}}
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r = wh.build_report(result, _proj_inputs(10000, prior_base=30000),
                        _PriorNoAgi(), 2026,
                        datetime.date(2026, 6, 30))
    assert r.safe_harbor.prior_agi_missing is True
    assert "may be UNDERSTATED" in wh.render_markdown(r)


def test_read_ca_inputs_box17_only_scaffold_skipped():
    """Codex P2: a CA section whose only non-zero field is parsed W-2 Box 17
    withholding is NOT a computed return — must not print a confident
    '$0 / covered' CA block."""
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000},
              "california_540": {"_include_if": "state is CA",
                                 "ca_standard_deduction": 0,
                                 "ca_agi": 0, "ca_total_tax": 0,
                                 "ca_withholding_box17": 4500,
                                 "ca_sdi_box14": 1200}}
    assert wh.read_ca_inputs(result) is None
    r = wh.build_report(result, _ca_proj_inputs(), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca is None


def test_the_federal_prior_prong_requires_the_completed_line8_base():
    """An archive's raw line 24 cannot silently become Form 2210 line 8."""
    prior = _FakeCaPrior(30000, 120000, 6000, 120000)
    unresolved = wh.build_report(
        _ca_result(), _ca_proj_inputs(), prior, 2026,
        datetime.date(2026, 6, 30))
    assert unresolved.safe_harbor.degraded is True
    assert unresolved.safe_harbor.prior_tax is None
    assert "Do not substitute raw Form 1040 line 24" in \
        wh.render_markdown(unresolved)

    completed = wh.build_report(
        _ca_result(), _ca_proj_inputs(prior_base=30000), prior, 2026,
        datetime.date(2026, 6, 30))
    assert completed.safe_harbor.degraded is False
    assert completed.safe_harbor.prior_tax == 30000.0
    # No federal `prior_tax_unnetted`, on the object or in the payload — the
    # CA twin is the live one; see its field comment in projection.py.
    assert not hasattr(completed.safe_harbor, "prior_tax_unnetted")
    assert "prior_tax_unnetted" not in wh._report_to_dict(
        completed)["safe_harbor"]
    assert "prior_tax_unnetted" in wh._report_to_dict(
        completed)["ca"]["safe_harbor"]


def test_the_unnetted_current_year_note_reaches_this_renderer_too():
    """The same §6654(f) gap on THIS year, where it drives more than a prong.

    `read_liability` takes `form_1040.line_24_total_tax`, and §6654 measures
    Form 2210 line 4 — line 24 net of the part-IV refundable credits. Line 4
    feeds BOTH the 90% prong and the $1,000 floor, so the shortfall this
    report prints and the excuse it withholds are both computed too high:
    $5,000 of tax, $1,000 of refundable AOTC and $3,500 withheld is a true
    $500 balance §6654(e)(1) excuses, reported here as $1,500 to pay.
    Over-stating what the taxpayer must pay is the hard-error direction.
    """
    r = wh.build_report(_ca_result(), _ca_proj_inputs(),
                        _FakeCaPrior(30000, 120000, 6000, 120000), 2026,
                        datetime.date(2026, 6, 30))
    assert r.safe_harbor.current_tax_unnetted is True, (
        "the fixture no longer prints a shortfall, so it cannot show whether "
        "the note reaches this renderer")
    md = wh.render_markdown(r)
    assert wh._CURRENT_TAX_UNNETTED_NOTE in md, (
        "/tax-withholding asks for a payment computed off raw line 24 with "
        "nothing saying that is not the figure §6654 measures")
    from agents.lib.projection import _CURRENT_TAX_UNNETTED_NOTE as PROJ_NOTE
    assert wh._CURRENT_TAX_UNNETTED_NOTE is PROJ_NOTE, (
        "the two renderers no longer share ONE constant, which is how the "
        "catch-up note came to exist in one report and not the other")
    assert wh._report_to_dict(r)["safe_harbor"]["current_tax_unnetted"] is True, (
        "the flag does not reach --json, which is what both skills read first")

    # $40,000 of tax, $5,000 withheld and $40,000 of estimates: covered, and
    # off the floor, so the silence is about having nothing to over-state
    # rather than about §6654(e)(1) already excusing the year.
    inputs = _ca_proj_inputs(fed_wh=5000)
    inputs["estimated_payments_made"] = dict(inputs["estimated_payments_made"],
                                             federal=40000)
    covered = wh.build_report(_ca_result(), inputs, None, 2026,
                              datetime.date(2026, 6, 30))
    assert covered.safe_harbor.covered and not covered.safe_harbor.de_minimis
    assert covered.safe_harbor.current_tax_unnetted is False, (
        "the note fires on a year asking for nothing — a caveat on a run with "
        "no figure to over-state is how a real one stops being read")
    assert wh._CURRENT_TAX_UNNETTED_NOTE not in wh.render_markdown(covered)


def test_the_de_minimis_margin_reaches_this_report_and_its_payload():
    """The floor excuses the penalty on a PROJECTION; the margin says by how
    much. Both statuses are shared with /tax-project, so what this pins is
    that the true-up's own gap table and JSON carry them."""
    r = wh.build_report(_ca_result(ca_tax=1000, ca_agi=20000),
                        _ca_proj_inputs(state_wh=347), None, 2026,
                        datetime.date(2026, 6, 30))
    assert r.ca.safe_harbor.de_minimis_margin > 0
    payload = wh._report_to_dict(r)
    assert payload["ca"]["safe_harbor"]["de_minimis_margin"] == \
        r.ca.safe_harbor.de_minimis_margin, (
        "the margin does not reach --json, so a skill summarizing the payload "
        "cannot tell $1 of headroom from the whole floor")
    md = wh.render_markdown(r)
    ca_block = md.split("## California", 1)[1]
    assert wh._ca_status(r.ca.safe_harbor, tax_is_actual=True) in ca_block, (
        "the CA status states the excuse without saying what has to move for "
        "it to evaporate")
    assert "more CA tax than projected" not in ca_block, (
        "the CA cell carries /tax-project's wording into a report whose CA "
        "tax is read from result.json and does not move")


# --- The voucher table's DATES, read across BOTH columns -------------------
#
# The catch-up note this report already carried is scoped to the "To avoid
# penalty" column, and that column is all $0.00 whenever the harbor is covered
# or under §6654(e)(1)'s floor — so the only calendar caveat the report had went
# silent in exactly the runs where "To zero balance" beside it still carried the
# whole balance on a date already behind us. `resolve_as_of`'s closed-year
# default is what made that the ORDINARY run rather than an odd one.


def _closed_year_report(today, fed_wh=9000, prior_tax=8000):
    """TY2025 read at `today`, as-of the closed year's 12-31.

    Prior tax 8000 at AGI 90000 -> factor 1.00, so the harbor requires 8000
    against ~9000 projected withholding: COVERED, penalty column all zeros,
    with 30000 - 9000 = 21000 still owed in the zero-balance column.
    """
    result = {"meta": {"tax_year": 2025, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 30000}}
    return wh.build_report(result, _proj_inputs(fed_wh, prior_base=prior_tax),
                           _FakePrior(prior_tax, 90000), 2025,
                           datetime.date(2025, 12, 31), today=today)


def test_a_covered_harbor_does_not_silence_the_zero_columns_calendar():
    """The regression this pair exists for, stated as the two columns.

    A covered harbor zeroes the penalty column and with it `voucher_catchup_
    note`, which is correct — nothing is short against the safe harbor. It must
    not take the DATE caveat with it: the balance is in the other column, on
    the same row, under the same passed deadline.
    """
    r = _closed_year_report(datetime.date(2026, 3, 1))
    assert r.safe_harbor.covered and r.voucher_catchup_note is None
    assert all(v.amount == 0 for v in r.vouchers_penalty)
    assert [(v.quarter, v.due_date, v.amount) for v in r.vouchers_zero] == \
        [(4, "2026-01-15", 21000.0)]
    assert r.voucher_dates_note is not None, (
        "the only column with money in it is the one no note can reach"
    )
    assert "2026-01-15" in r.voucher_dates_note
    assert r.voucher_dates_note in wh.render_markdown(r)
    assert wh._report_to_dict(r)["voucher_dates_note"] == r.voucher_dates_note, (
        "the caveat does not reach --json, which both skills read FIRST"
    )


def test_a_zero_balance_row_is_optional_before_the_balance_due_deadline():
    r = _closed_year_report(datetime.date(2026, 3, 1))
    note = r.voucher_dates_note
    assert "zero-balance target" in note and "optional" in note
    assert "not due until" in note and "2026-04-15" in note
    for false_penalty_claim in ("pay NOW", "underpayment keeps accruing",
                                "expect the penalty", "penalty is FIXED"):
        assert false_penalty_claim not in note, (
            "a funded optional balance row inherited the penalty schedule's "
            f"instruction: {false_penalty_claim!r}")


def test_a_zero_balance_row_becomes_balance_due_without_inventing_a_penalty():
    """Past the payment deadline, change instruments but not penalty status."""
    r = _closed_year_report(datetime.date(2026, 6, 1))
    note = r.voucher_dates_note
    assert "balance-due deadline was 2026-04-15" in note
    assert "Do NOT send a TY2025 1040-ES voucher" in note
    assert "pay any unpaid TY2025 tax as a balance-due payment" in note
    for false_penalty_claim in ("pay NOW", "underpayment keeps accruing",
                                "expect the penalty", "penalty is FIXED"):
        assert false_penalty_claim not in note
    md = wh.render_markdown(r)
    assert md.index(note) < md.index("## Caveats")


def test_a_funded_penalty_row_keeps_penalty_specific_calendar_guidance():
    """Separating columns must not silence a real penalty-target row."""
    open_report = _closed_year_report(
        datetime.date(2026, 3, 1), fed_wh=9000, prior_tax=30000)
    assert open_report.safe_harbor.shortfall > 0
    assert "pay NOW" in open_report.voucher_dates_note
    assert "ONLY for the funded penalty-target row" in \
        open_report.voucher_dates_note

    closed_report = _closed_year_report(
        datetime.date(2026, 6, 1), fed_wh=9000, prior_tax=30000)
    assert "any TY2025 underpayment penalty is FIXED" in \
        closed_report.voucher_dates_note
    assert "expect the penalty" not in closed_report.voucher_dates_note


def test_the_cutoff_is_statutory_april_15_not_the_observed_due_date():
    """§7503 rolls installments, not §6654(b)(2)'s accrual endpoint."""
    assert proj.accrual_period_closed(2022, datetime.date(2023, 4, 15)) is True
    assert proj.accrual_period_closed(2022, datetime.date(2023, 4, 16)) is True
    assert proj.accrual_period_closed(2022, datetime.date(2023, 4, 18)) is True


def test_no_calendar_note_while_every_printed_date_is_still_ahead():
    """A gate that fires on correct data is the one readers learn to skip."""
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r = wh.build_report(result, _proj_inputs(10000), _FakePrior(30000, 120000),
                        2026, datetime.date(2026, 6, 30),
                        today=datetime.date(2026, 6, 30))
    assert r.penalty_target.shortfall > 0 and r.zero_target.shortfall > 0
    assert r.voucher_dates_note is None
    assert all(v.due_date >= "2026-06-30" for v in r.vouchers_penalty)


def test_a_passed_date_with_nothing_owed_against_it_stays_quiet():
    """Gated on the AMOUNT, not on the date alone: an all-zero table asks for
    nothing, and a caveat about nothing is the one that trains a reader past
    the real ones."""
    r = _closed_year_report(datetime.date(2026, 6, 1), fed_wh=30000)
    assert r.zero_target.covered and r.safe_harbor.covered
    assert all(v.amount == 0 for v in r.vouchers_zero)
    assert r.voucher_dates_note is None


def test_a_report_built_without_a_date_asserts_nothing_about_the_calendar():
    """`today` is optional and absent means SILENT, never assumed. Reading the
    clock inside build_report would make a pure function non-deterministic; the
    CLI owns the date and carries a --today override."""
    r = _closed_year_report(None)
    assert r.voucher_dates_note is None
    assert wh._report_to_dict(r)["voucher_dates_note"] is None


def test_the_ca_columns_get_the_same_reading_under_their_own_statute():
    r = wh.build_report(
        _ca_result(fed_tax=30000, ca_tax=9000, ca_agi=200000),
        _ca_proj_inputs(fed_wh=9000, state_wh=2000),
        _FakeCaPrior(8000, 90000, 1000, 90000), 2026,
        datetime.date(2026, 12, 31), today=datetime.date(2027, 6, 1))
    assert r.ca.safe_harbor.covered and r.ca.zero_shortfall > 0
    note = r.ca.voucher_dates_note
    assert note is not None and "540-ES" in note and "FTB 5805" in note
    assert "1040-ES" not in note, (
        "the CA block explained in terms of the federal instrument"
    )
    assert "expect the penalty" not in note and "penalty is FIXED" not in note
    assert note in wh.render_markdown(r)
    assert wh._report_to_dict(r)["ca"]["voucher_dates_note"] == note


# --- Which lever the covered year still has --------------------------------


def test_the_w4_lead_is_replaced_once_no_paycheck_remains():
    """The lead recommended the W-4 route unconditionally, directly above a
    4(c) figure of $0.00/paycheck over 0 remaining paychecks. `elapsed_fraction`
    is exactly 1.0 at 12-31, so the closed-year default guarantees that state."""
    r = _closed_year_report(datetime.date(2026, 3, 1))
    assert r.inputs.paychecks_remaining == 0
    assert "No paychecks remain in TY2025" in r.w4_lever_note
    assert "prefer it over uneven estimates" not in r.w4_lever_note
    md = wh.render_markdown(r)
    assert r.w4_lever_note in md
    assert md.index(r.w4_lever_note) < md.index("### W-4 line 4(c)"), (
        "the correction has to precede the figures it explains"
    )
    assert wh._report_to_dict(r)["w4_lever_note"] == r.w4_lever_note


def test_the_spent_lever_note_does_not_read_as_a_covered_year():
    """$0.00/paycheck is also what a fully covered year prints. The note has to
    separate 'no lever left' from 'nothing owed' — this run owes $21,000."""
    r = _closed_year_report(datetime.date(2026, 3, 1))
    assert r.zero_target.shortfall == 21000.0
    assert "not because nothing is owed" in r.w4_lever_note
    assert "$21,000" in r.w4_lever_note
    assert "not the safe-harbor shortfall" in r.w4_lever_note


def test_the_w4_lead_still_leads_while_payroll_remains():
    result = {"meta": {"tax_year": 2026, "filing_status": "single"},
              "form_1040": {"line_24_total_tax": 40000}}
    r = wh.build_report(result, _proj_inputs(10000), _FakePrior(30000, 120000),
                        2026, datetime.date(2026, 6, 30))
    assert r.inputs.paychecks_remaining > 0
    assert "prefer it over uneven estimates when paychecks remain" in r.w4_lever_note
    assert "No paychecks remain" not in r.w4_lever_note
    assert r.w4_lever_note in wh.render_markdown(r)


def test_the_cli_hands_build_report_the_real_date_not_the_as_of(tmp_path, capsys):
    """The whole calendar half is reachable only through `_main`'s `today`.

    Wired wrong — or not wired — every note above stays None on the exact run
    they exist for, with the unit tests still green because they pass `today`
    themselves.
    """
    rc = wh._main(_closed_year_workspace(tmp_path, 2025)
                  + ["--today", "2026-06-01"])
    assert rc == 0, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["as_of"] == "12-31"
    assert payload["w4_lever_note"] and "No paychecks remain in TY2025" in \
        payload["w4_lever_note"]
    # Asserted, not assumed: a fixture that came to owe nothing would make
    # every check below vacuous while staying green.
    assert any(v["amount"] > 0 for v in payload["vouchers_zero"]), (
        "the fixture no longer owes anything, so this test proves nothing")
    assert payload["voucher_dates_note"], (
        "the fixture owes money on a January 15 that is months behind "
        "2026-06-01, and the payload says nothing about it")
    assert "obsolete" in payload["voucher_dates_note"]
    assert "expect the penalty" not in payload["voucher_dates_note"]
    assert "2026-04-15" in payload["voucher_dates_note"]


def test_every_zero_liability_flag_is_validated_without_a_ca_section():
    """A parse boundary that fires on the shape of a DIFFERENT file is not one.

    The CA flag this once pinned was coerced only inside `build_ca_true_up`,
    which returns None before reaching it when result.json carries no
    `california_540` section — so the JSON string "true", the exact case
    `_confirmed_flag` exists to reject since `bool("false")` is True, ran
    clean for a season in a non-CA workspace and began hard-erroring the
    moment a CA section appeared. That flag is gone: R&TC §19136(c)(2) needs
    no confirmation, so nothing reads it and a legacy file carrying it is
    simply ignored rather than refused.

    The invariant outlives it on the two FEDERAL flags, which `build_report`
    coerces unconditionally — including the newer
    `prior_year_return_filed_12_months_confirmed`, added beside its twin
    precisely so it could not repeat the CA flag's history of being read on
    one path only.
    """
    result = {"meta": {"tax_year": 2026, "filing_status": "Single"},
              "form_1040": {"line_24_total_tax": 15000}}
    assert "california_540" not in result, "the premise: no CA section"

    # The retired CA flag: inert, not fatal, even in its rejected shape.
    inert = dict(_proj_inputs(0),
                 prior_year_ca_zero_liability_confirmed="true")
    assert wh.build_ca_true_up(result, inert, None, "Single",
                               datetime.date(2026, 6, 30), 13, 2026) is None

    # Both federal flags: refused on the non-CA path, where the CA flag was not.
    for flag in ("prior_year_zero_liability_confirmed",
                 "prior_year_return_filed_12_months_confirmed"):
        proj_inputs = dict(_proj_inputs(0), **{flag: "true"})
        with pytest.raises(proj.ProjectionError) as exc:
            wh.build_report(result, proj_inputs, None, 2026,
                            datetime.date(2026, 6, 30))
        assert flag in str(exc.value)


class TestTheNewYorkTrueUp:
    """One Form IT-2105 voucher carries the State tax, the New York City
    resident tax, the Yonkers surcharge and the MCTMT together."""

    AS_OF = datetime.date(2026, 6, 30)

    def _result(self, tax=20000.0, nyagi=250000.0, deduction=16050.0,
                **over):
        section = {"line_33_nyagi": nyagi, "line_34_deduction": deduction,
                   "line_46_total_state_tax": tax - 4000.0,
                   "line_58_total_city_and_yonkers": 4000.0,
                   "line_60_total_taxes": tax}
        section.update(over)
        return {"meta": {"tax_year": 2026, "filing_status": "MFJ"},
                "form_1040": {"line_24_total_tax": 60000.0},
                "new_york_it201": section}

    def _inputs(self, state_wh=6000.0, state_est=0.0):
        return {"pay_frequency": "biweekly",
                "ytd": {"federal_withholding": 25000, "state_withholding": state_wh},
                "estimated_payments_made": {"federal": 0, "state": state_est}}

    def _law(self):
        return {"tax_year": 2026, "state": ny_state_cache()}

    class _Prior:
        def __init__(self, result):
            self.result = result

    def _build(self, result=None, inputs=None, prior=None, law="default"):
        return wh.build_report(
            result if result is not None else self._result(),
            inputs if inputs is not None else self._inputs(),
            prior, 2026, self.AS_OF,
            today=datetime.date(2026, 6, 30),
            tax_law=self._law() if law == "default" else law)

    # --- what the requirement is measured against -------------------------

    def test_the_target_is_line_60_not_line_46(self):
        """A true-up built on the state total alone would under-state a City
        resident's requirement by the whole of their city tax — and a City
        resident is the population this module exists for."""
        r = self._build()
        assert r.ny is not None
        assert r.ny.liability == 20000.0            # line 60
        assert r.ny.liability != 16000.0            # line 46

    def test_with_no_prior_year_the_current_share_is_the_only_floor(self):
        """90% of 20,000, and the engine SAYS that is the only floor rather
        than letting an absent prior year read as a met one."""
        r = self._build()
        assert r.ny.options == {"current_year": 18000.0}
        assert r.ny.required_annual_payment == 18000.0
        assert any("no prior-year safe harbour" in n.lower() or
                   "first-year" in n.lower() for n in r.ny.notes)

    def test_a_computed_prior_year_supplies_the_lesser_floor(self):
        """Prior NY tax 10,000 with prior NYAGI under the threshold: 100% of
        10,000 beats 90% of 20,000, so the requirement is 10,000."""
        prior = self._Prior({"new_york_it201": {
            "line_33_nyagi": 120000.0, "line_34_deduction": 16050.0,
            "line_60_total_taxes": 10000.0}})
        r = self._build(prior=prior)
        assert r.ny.options == {"current_year": 18000.0, "prior_year": 10000.0}
        assert r.ny.required_annual_payment == 10000.0

    def test_the_higher_share_is_keyed_to_NEW_YORK_agi(self):
        """Prior NYAGI 200,000 is over the threshold, so 110% of the prior
        tax applies. Handing the FEDERAL AGI in would measure a New York safe
        harbour against the wrong figure."""
        prior = self._Prior({"new_york_it201": {
            "line_33_nyagi": 200000.0, "line_34_deduction": 16050.0,
            "line_60_total_taxes": 10000.0}})
        r = self._build(prior=prior)
        assert r.ny.options["prior_year"] == 11000.0
        assert any("higher prior-year share" in n for n in r.ny.notes)

    def test_a_scaffold_prior_section_supplies_no_floor(self):
        """An all-zero template must not become a $0 prior-year tax — that
        would make ANY current shortfall "covered"."""
        prior = self._Prior({"new_york_it201": {
            "line_33_nyagi": 0, "line_34_deduction": 0,
            "line_60_total_taxes": 0,
            "refundable_credits_and_payments": {
                "line_72_state_withholding": 9000.0}}})
        r = self._build(prior=prior)
        assert "prior_year" not in r.ny.options

    def test_a_prior_tax_without_its_nyagi_drops_the_pair(self):
        """`compute_ny_required_annual_payment` REFUSES the pair, and it is
        right to — assuming the lower share under-states the requirement for
        exactly the taxpayers the higher one is aimed at. Dropping BOTH leaves
        the current-year share, which over-states: the degrade direction."""
        prior = self._Prior({"new_york_it201": {
            "line_34_deduction": 16050.0, "line_60_total_taxes": 10000.0}})
        r = self._build(prior=prior)
        assert "prior_year" not in r.ny.options
        assert r.ny.required_annual_payment == 18000.0

    # --- the schedule -----------------------------------------------------

    def test_the_instalments_are_four_equal_quarters_not_californias_split(self):
        """30/40/0/30 with no September instalment would tell a New York
        filer to skip a payment they owe."""
        r = self._build(inputs=self._inputs(state_wh=0.0))
        quarters = [v.quarter for v in r.ny.vouchers_penalty]
        assert quarters == [3, 4]          # Q1 and Q2 already closed at 06-30
        amounts = [v.amount for v in r.ny.vouchers_penalty]
        assert amounts[0] == amounts[1]
        assert round(sum(amounts), 2) == r.ny.shortfall

    def test_the_dates_roll_under_new_yorks_own_rule(self):
        """September 15 2026 is a Tuesday and January 15 2027 a Friday, so
        neither rolls — what this pins is that the dates come from the STATE
        helper, which for New York rolls weekends only because General
        Construction Law §§24 and 25-a have not been read here."""
        r = self._build()
        due = {v.quarter: v.due_date for v in r.ny.vouchers_penalty}
        assert due == {3: "2026-09-15", 4: "2027-01-15"}

    def test_the_shortfall_nets_projected_withholding_and_estimates(self):
        """YTD 6,000 at 06-30 annualizes to about 12,000; 18,000 required."""
        r = self._build()
        assert r.ny.projected_paid == r.ny.projected_full_year_withholding
        assert r.ny.shortfall == round(
            18000.0 - r.ny.projected_paid, 2)
        assert not r.ny.covered

    def test_a_covered_year_reports_covered_and_zero_vouchers(self):
        r = self._build(inputs=self._inputs(state_wh=20000.0))
        assert r.ny.covered and r.ny.shortfall == 0.0
        assert all(v.amount == 0.0 for v in r.ny.vouchers_penalty)

    # --- when there is no New York block ----------------------------------

    def test_a_non_new_york_return_gets_no_block(self):
        r = self._build(result={"meta": {"tax_year": 2026,
                                         "filing_status": "MFJ"},
                                "form_1040": {"line_24_total_tax": 60000.0}})
        assert r.ny is None and r.ny_cache_missing is False

    def test_a_scaffold_section_is_not_a_computed_return(self):
        result = self._result(tax=0.0, nyagi=0.0, deduction=0.0)
        result["new_york_it201"]["line_46_total_state_tax"] = 0.0
        result["new_york_it201"]["line_58_total_city_and_yonkers"] = 0.0
        assert wh.read_ny_inputs(result) is None

    def test_a_missing_cache_is_said_out_loud_not_left_silent(self):
        """A report that simply omits the block reads as "no New York
        estimated tax is due", which is the wrong direction for a payment."""
        r = self._build(law=None)
        assert r.ny is None and r.ny_cache_missing is True
        md = wh.render_markdown(r)
        assert "No New York true-up was computed" in md
        assert "NOT a finding that no New York estimated tax is due" in md

    def test_a_missing_line_60_hard_errors_rather_than_degrading(self):
        result = self._result()
        del result["new_york_it201"]["line_60_total_taxes"]
        with pytest.raises(wh.WithholdingError, match="line_60_total_taxes"):
            wh.read_ny_inputs(result)

    def test_a_missing_nyagi_hard_errors(self):
        """The §685(c) threshold is keyed to NYAGI; assuming it is below one
        under-states the requirement for exactly the taxpayers the higher
        share is aimed at."""
        result = self._result()
        del result["new_york_it201"]["line_33_nyagi"]
        with pytest.raises(wh.WithholdingError, match="line_33_nyagi"):
            wh.read_ny_inputs(result)

    def test_a_negative_nyagi_is_an_ordinary_return(self):
        """Line 33 has no floor — a Schedule C loss reaches it routinely."""
        tax, nyagi = wh.read_ny_inputs(self._result(nyagi=-4000.0))
        assert nyagi == -4000.0 and tax == 20000.0

    # --- what the report says ---------------------------------------------

    def test_the_markdown_names_the_four_taxes_the_one_voucher_covers(self):
        md = wh.render_markdown(self._build())
        assert "## New York true-up (Form IT-201)" in md
        assert "line 60" in md and "MCTMT" in md
        assert "IT-2105 installment schedule (four EQUAL quarters)" in md
        assert "IT-2104 per-paycheck extra NY withholding" in md

    def test_the_caveat_says_what_the_withholding_figure_means(self):
        """`ytd.state_withholding` is ALL New York withholding — boxes 17 and
        19 together, plus Yonkers — because one voucher is measured against
        all of them. Entering the state figure alone over-states the
        shortfall."""
        md = wh.render_markdown(self._build())
        assert "ALL " in md and "box 19" in md

    def test_the_payload_carries_the_block(self):
        payload = wh._report_to_dict(self._build())
        assert payload["ny"]["liability"] == 20000.0
        assert payload["ny"]["required_annual_payment"] == 18000.0
        assert payload["ny_cache_missing"] is False
        assert payload["ny"]["notes"]

    def test_california_is_untouched(self):
        """Regression pin: the CA block still builds, and a return carrying
        both sections gets both."""
        result = self._result()
        result["california_540"] = {"ca_total_tax": 9000.0, "ca_agi": 240000.0,
                                    "ca_standard_deduction": 11080.0}
        r = self._build(result=result)
        assert r.ca is not None and r.ny is not None
        assert r.ca.liability == 9000.0 and r.ny.liability == 20000.0


class TestWhatTheReviewPassFoundInTheTrueUp:
    """Two findings, both REPRODUCED before they were believed."""

    AS_OF = datetime.date(2026, 12, 31)

    def _report(self, state_wh=3000.0, as_of=None, law="default"):
        result = {"meta": {"tax_year": 2026, "filing_status": "MFJ"},
                  "form_1040": {"line_24_total_tax": 10000.0},
                  "new_york_it201": {"line_33_nyagi": 200000.0,
                                     "line_34_deduction": 16050.0,
                                     "line_46_total_state_tax": 5000.0,
                                     "line_58_total_city_and_yonkers": 4000.0,
                                     "line_60_total_taxes": 9000.0}}
        inputs = {"pay_frequency": "biweekly",
                  "ytd": {"federal_withholding": 10000.0,
                          "state_withholding": state_wh},
                  "estimated_payments_made": {"federal": 0, "state": 0}}
        at = as_of or self.AS_OF
        return wh.build_report(
            result, inputs, None, 2026, at, today=at,
            tax_law=({"tax_year": 2026, "state": ny_state_cache()}
                     if law == "default" else law))

    def test_the_lede_names_a_NEW_YORK_balance_it_used_to_ignore(self):
        """`_W4_LEVER_SPENT_CLEAR_STATE_DUE` exists because "no remaining
        payment target" was a lie for a CA taxpayer with a state balance. The
        New York block reproduced the closed bug on the day it landed: a
        closed year with a $6,000 New York shortfall printed "there is no
        remaining payment target for this report" directly above the table
        showing it."""
        r = self._report()
        assert r.ny.zero_shortfall == 6000.0
        assert "no remaining payment target" not in (r.w4_lever_note or "")
        assert "state balance" in (r.w4_lever_note or "").lower()

    def test_a_covered_new_york_return_does_not_manufacture_a_state_lede(self):
        """The counterweight: a gate firing on a return with nothing owed is
        a gate firing on correct data."""
        r = self._report(state_wh=20000.0)
        assert r.ny.zero_shortfall == 0.0
        assert "state balance" not in (r.w4_lever_note or "").lower()

    def test_an_EMPTY_cache_is_reported_the_same_as_an_absent_one(self):
        """`ny_cache_missing` tested `tax_law is None` while the builder bailed
        on `if not tax_law`, so `tax_law={}` produced no New York block AND no
        explanation — the silent omission the flag exists to prevent."""
        r = self._report(law={})
        assert r.ny is None
        assert r.ny_cache_missing is True
        assert "No New York true-up was computed" in wh.render_markdown(r)

    def test_the_schedule_carries_a_CATCH_UP_note(self):
        """Both other jurisdictions pass through `_voucher_catchup_note`; the
        New York table shipped without one. Tax Law §685(c) has the same
        cumulative structure: a shortfall at October 1 prints the whole figure
        against the January row — exactly what January asks — while 75% fell
        due across April, June and September and keeps accruing."""
        r = self._report(state_wh=0.0, as_of=datetime.date(2026, 10, 1))
        assert r.ny.shortfall > 0
        assert r.ny.voucher_catchup_note, (
            "the NY schedule prints identically-split amounts with no warning "
            "that the earliest still-open instalment can need more")
        assert "IT-2105.9" in r.ny.voucher_catchup_note
        assert "MCTMT" in r.ny.voucher_catchup_note
        md = wh.render_markdown(r)
        assert r.ny.voucher_catchup_note in md

    def test_the_catch_up_note_is_silent_when_nothing_is_behind(self):
        """It runs the cumulative test rather than proxying it: over-warning
        is the hard-error direction for this note."""
        r = self._report(state_wh=20000.0,
                         as_of=datetime.date(2026, 10, 1))
        assert not r.ny.voucher_catchup_note

    def test_the_catch_up_note_is_paired_with_NEW_YORKs_own_weights(self):
        """`_CatchupNote` binds the text to the instalment fraction by LABEL,
        so a federal note cannot be run against California's 30/70/70/100.
        New York's four equal quarters share the FEDERAL fraction — the same
        question, not merely the same arithmetic."""
        assert proj._NY_CATCHUP.jurisdiction == "NY"
        assert (proj._NY_CATCHUP.instalment_fraction
                is proj._FEDERAL_CATCHUP.instalment_fraction)
        assert (proj._NY_CATCHUP.instalment_fraction
                is not proj._CA_CATCHUP.instalment_fraction)

    def test_the_status_key_is_the_canonical_one(self):
        """`.lower()` and `_full_status_key` agree on every status except QSS,
        which the hand-rolled form left as "qss" and the helper maps to "mfj".
        Harmless today, and the one that silently misses the day the function
        grows an MFJ-keyed lookup."""
        import ast
        import inspect
        source = inspect.getsource(wh.build_ny_true_up)
        assert '_full_status_key(filing_status, "NY estimated tax")' in source
        # The CODE, not the source text — the comment beside the call explains
        # the change and contains the very spelling it replaced, so a substring
        # check over the whole source can never go red.
        tree = ast.parse(textwrap.dedent(source))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "lower" not in called
