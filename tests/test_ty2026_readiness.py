"""M1 — TY2026 golden-path integration fixture (season readiness, Epic M).

The Epic-I (TY2026 OBBBA) work added six fetched cache blocks, several of which
HARD-ERROR when absent. That is the correct bias direction — but it means the
FIRST real TY2026 /tax-setup run is the integration test, and it would run on a
user. This file is that integration test, run now instead:

1. GOLDEN PATH: a single TY2026 tax_law cache populated in EVERY Epic-I block
   with representative Rev. Proc. 2025-32 / OBBBA-era values is driven through
   each block's compute function, pinned against hand-computed expectations, and
   the populated cache must read FRESH (not INCOMPLETE) — the end-to-end wiring
   proof.

2. ABSENCE MATRIX: each block's DECLARED absence behavior (fail-closed /
   loud-degrade / statutory-fallback — see the 2026-07-20 review, Part 1) is
   pinned by a deliberately-absent variant RUN AT A YEAR WHERE THE BEHAVIOR IS
   REACHABLE. X10 removed the annual §603 threshold default, so its
   absence cases require the fetched indexed amount in both TY2026 and TY2027.

SCOPE HONESTY (per the review's M1 note): a fixture proves WIRING and
COMPUTATION, not FETCHABILITY. /tax-setup's law fetch is an interactive
WebSearch step no unit test can exercise; validating the live fetch + K2 source
pin is the SEPARATE operational acceptance under M2. The representative values
below are hand-chosen for clean arithmetic and to be confirmed against the
published Rev. Proc. at live fetch — the tests assert the ENGINE computes
correctly FROM the fixture, not that a given dollar figure is the canonical IRS
amount.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.lib import tax_computations as tc
from agents.lib import tax_law_fetcher as tlf
from agents.lib.ptet import _salt_cap
from agents.lib.retirement_dashboard import (
    _get_secure_2_0_high_earner_threshold,
    RetirementError,
)


def _ty2026_tax_law():
    """A TY2026 cache populated in every Epic-I block with representative
    Rev. Proc. 2025-32 / OBBBA-era values. Built on create_empty_tax_law so it
    stays schema-conformant."""
    tl = tlf.create_empty_tax_law(2026)
    fed = tl["federal"]

    # Schedule 1-A (I2) — tips §224 / overtime §225 / car-loan §70203 / senior
    # §70103. Representative enacted caps + MAGI phase-out starts.
    fed["schedule_1a"] = {
        "tips_cap": 25000,
        "overtime_cap": {"single": 12500, "mfj": 25000},
        "car_loan_cap": 10000,
        "senior_amount_per_person": 6000,
        "tips_overtime_phaseout_start": {"single": 150000, "mfj": 300000},
        "car_loan_phaseout_start": {"single": 100000, "mfj": 200000},
        "senior_phaseout_start": {"single": 75000, "mfj": 150000},
    }
    # §170(p) non-itemizer charitable (I3) — $1,000 / $2,000-MFJ (statutory).
    fed["charitable_non_itemizer"] = {"amount": 1000, "joint_amount": 2000}
    # §70105 minimum QBI (I4) — indexed floor for a small active trade.
    fed["qbi_minimum"] = {"income_threshold": 1000, "amount": 400}
    # §164(b)(7)(A)(ii): TY2026 is $40,400 / $20,200-MFS.
    fed["salt_cap"] = {"amount": 40400, "mfs_amount": 20200}
    # AMT exemption phase-out rate — §70107 raises it to 50% for TY2026+ (the
    # cache OVERRIDE; the engine also has the statutory schedule as fallback).
    fed["amt"]["phaseout_rate"] = 0.50
    # SECURE 2.0 §603 high-earner threshold — the TY2026 indexed value.
    fed["retirement_401k"]["secure_2_0_high_earner_threshold"] = 150000
    # §6621 underpayment rates. A TY2026 return's UNEXTENDED due date is April
    # 2027, so the October-extension interest window is Apr 2027 → Oct 2027 —
    # the rates must be the TY+1 (2027) Q2/Q3/Q4 quarters, NOT 2026 quarters (a
    # 2026 span would be a TY2025 return). This is the M3 forward-fetch window
    # /tax-setup documents. (The 2210 estimated-tax window would separately need
    # the 2026 quarters; a real TY2026 cache carries both — 2027 shown here to
    # exercise the extension-interest path this fixture is about.)
    fed["underpayment_rates"] = [
        {"effective_from": "2026-04-01", "annual_rate": 0.08},  # 2210 window
        {"effective_from": "2026-07-01", "annual_rate": 0.08},
        {"effective_from": "2026-10-01", "annual_rate": 0.08},
        {"effective_from": "2027-01-01", "annual_rate": 0.08},
        {"effective_from": "2027-04-01", "annual_rate": 0.08},  # M3 interest window
        {"effective_from": "2027-07-01", "annual_rate": 0.08},
        {"effective_from": "2027-10-01", "annual_rate": 0.08},
    ]
    return tl


def _complete_federal_ty2026_tax_law():
    """The Epic-I fixture PLUS every remaining federal structural block filled,
    so check_freshness reports no `federal.*` null_fields — a genuinely
    season-ready federal cache. Values are representative (freshness checks
    presence, not exact figures; compute correctness is pinned elsewhere). State
    freshness is out of Epic M's scope, so only the federal side is completed."""
    tl = _ty2026_tax_law()
    fed = tl["federal"]
    brackets = [{"rate": 0.10, "min": 0, "max": 12000},
                {"rate": 0.22, "min": 12000, "max": 200000},
                {"rate": 0.35, "min": 200000, "max": None}]
    for s in ("single", "mfj", "mfs", "hoh"):
        fed["filing_statuses"][s]["standard_deduction"] = 15750
        fed["filing_statuses"][s]["brackets"] = brackets
    fed["child_tax_credit"].update({"amount_per_child": 2200, "refundable_limit": 1700})
    fed["child_tax_credit"]["phase_out_start"] = {"single": 200000, "mfj": 400000}
    fed["irmaa"]["tiers"] = {
        "single": [{"magi_threshold": 106000, "annual_surcharge_per_person": 1000}],
        "mfj": [{"magi_threshold": 212000, "annual_surcharge_per_person": 1000}]}
    fed["capital_gains_rates"] = {
        "zero_pct_limit": {"single": 49000, "mfj": 98000, "mfs": 49000, "hoh": 65600},
        "fifteen_pct_limit": {"single": 540000, "mfj": 608000, "mfs": 304000, "hoh": 574000}}
    fed["social_security"]["wage_base"] = 184500
    fed["ira_limits"].update({"contribution_limit": 7500, "catch_up_50_plus": 1100})
    fed["ira_limits"]["traditional_deduction_phaseout"] = {
        "mfj_covered": {"start": 129000, "end": 149000},
        "mfj_spouse_not_covered": {"start": 242000, "end": 252000},
        "single_covered": {"start": 81000, "end": 91000}}
    fed["ira_limits"]["roth_income_phaseout"] = {
        "mfj": {"start": 242000, "end": 252000},
        "single": {"start": 153000, "end": 168000}}
    fed["hsa_limits"].update({"self_only": 4400, "family": 8750, "catch_up_55_plus": 1000})
    fed["retirement_401k"].update({
        "elective_deferral": 24500, "catch_up_50_plus": 8000,
        "catch_up_60_63": 11250, "total_annual_limit": 72000})
    fed["qbi_threshold"] = {"single": 197300, "mfj": 394600}
    fed["amt"]["exemption"] = {"single": 90400, "mfj": 140600, "mfs": 70300}
    fed["amt"]["phaseout_start"] = {"single": 639300, "mfj": 1278700, "mfs": 639300}
    fed["amt"]["rate_28_breakpoint"] = 244600
    tl["exchange_rates"] = {"EUR": 1.08}
    return tl


class TestTY2026GoldenPath:
    """Every Epic-I block present is driven through its compute function and
    pinned to a hand-computed expectation, on ONE populated TY2026 cache."""

    def test_schedule_1a_present_computes(self):
        fed = _ty2026_tax_law()["federal"]
        # Single, MAGI 120K, tips 30K (cap 25K), OT premium 10K (under cap),
        # car-loan 12K → cap 10K then phased 200×ceil(20K/1K)=4,000 → 6,000,
        # senior (1 elig) 6,000 − 6%×(120K−75K)=2,700 → 3,300. Total 44,300.
        r = tc.compute_schedule_1a(
            "Single", 2026, 120000, fed["schedule_1a"],
            qualified_tips=30000, overtime_premium=10000,
            car_loan_interest=12000, primary_age_65=True)
        assert r["total"] == 44300.0

    def test_charitable_non_itemizer_present_computes(self):
        fed = _ty2026_tax_law()["federal"]
        cni = fed["charitable_non_itemizer"]
        r = tc.compute_charitable_non_itemizer(
            5000, "MFJ", 2026, itemizes=False,
            over_250_substantiated=True,
            amount=cni["amount"], joint_amount=cni["joint_amount"])
        assert r["allowed"] == 2000.0  # capped at the joint amount

    def test_qbi_minimum_present_applies_floor(self):
        fed = _ty2026_tax_law()["federal"]
        # Small active trade: 20%-of-QBI is tiny, but the §70105 floor ($400)
        # applies when at/above the income threshold. QBI 1,500 → 20% = 300;
        # floor 400 wins (capped by the overall taxable-income ceiling).
        r = tc.compute_qbi_deduction(
            1500, 0, taxable_income_before_qbi=50000, net_capital_gain=0,
            tax_year=2026, qbi_minimum=fed["qbi_minimum"],
            active_qualified_trade=True)
        assert r["qbi_minimum_applied"] is True
        assert r["total_deduction"] == 400.0

    def test_salt_cap_present_from_cache(self):
        fed = _ty2026_tax_law()["federal"]
        assert _salt_cap(2026, "Single", fed["salt_cap"]) == 40400.0
        assert _salt_cap(2026, "MFS", fed["salt_cap"]) == 20200.0

    def test_amt_phaseout_rate_present_is_50pct(self):
        fed = _ty2026_tax_law()["federal"]
        assert tc._resolve_amt_phaseout_rate(
            2026, fed["amt"]["phaseout_rate"]) == 0.50

    def test_secure_603_threshold_present(self):
        tl = _ty2026_tax_law()
        assert _get_secure_2_0_high_earner_threshold(
            tl, effective_year=2026) == 150000

    def test_underpayment_rates_present_price_october_interest(self):
        """A TY2026 return's October-extension interest window is Apr 2027 →
        Oct 2027 (the unextended due date is April 2027) — the fixture must
        carry the TY+1 quarters, and the estimator must report COMPLETE over
        that span. $10,000, 183 days @8% daily = $409.20."""
        fed = _ty2026_tax_law()["federal"]
        r = tc.compute_underpayment_interest(
            10000, "2027-04-15", "2027-10-15", fed["underpayment_rates"])
        assert r["complete"] is True
        assert r["interest"] == 409.20

    def test_all_federal_blocks_populated_no_null_fields(self, tmp_path):
        """The end-to-end wiring proof, asserted against the REAL check_freshness
        return field (`null_fields`, not the nonexistent `issues`). A cache with
        every federal block filled — including all Epic-I blocks — leaves NO
        `federal.*` (or exchange_rates) entry in null_fields. If a future schema
        change adds a required federal field the fixture doesn't fill, this
        fails."""
        import json
        (tmp_path / "tax_law_2026.json").write_text(
            json.dumps(_complete_federal_ty2026_tax_law()))
        result = tlf.check_freshness(2026, str(tmp_path))
        federal_nulls = [f for f in result["null_fields"]
                         if f.startswith("federal.") or f.startswith("exchange_rates")]
        assert federal_nulls == [], f"unexpected federal null_fields: {federal_nulls}"

    def test_freshness_guard_is_non_vacuous(self, tmp_path):
        """Guard against the vacuous-test failure mode: nulling an AUDITED
        Epic-I federal field (salt_cap.amount) MUST surface it in null_fields,
        proving the assertion above actually depends on the data."""
        import json
        tl = _complete_federal_ty2026_tax_law()
        tl["federal"]["salt_cap"]["amount"] = None
        (tmp_path / "tax_law_2026.json").write_text(json.dumps(tl))
        result = tlf.check_freshness(2026, str(tmp_path))
        assert any("salt_cap.amount" in f for f in result["null_fields"]), \
            result["null_fields"]


class TestTY2026AbsenceMatrix:
    """Each Epic-I block's DECLARED absence class, pinned at a REACHABLE year."""

    def test_schedule_1a_absent_fails_closed(self):
        """Nonzero tips with the tips_cap absent → hard-error (understatement
        risk → fail closed)."""
        with pytest.raises((ValueError, KeyError, TypeError)):
            tc.compute_schedule_1a(
                "Single", 2026, 120000,
                {"overtime_cap": {"single": 12500, "mfj": 25000}},
                qualified_tips=30000)

    def test_charitable_non_itemizer_absent_fails_closed(self):
        """Nonzero cash gift with the cap unfetched → hard-error."""
        with pytest.raises(ValueError):
            tc.compute_charitable_non_itemizer(
                1000, "Single", 2026, itemizes=False, amount=None)

    def test_qbi_minimum_absent_degrades_loudly(self):
        """Absent §70105 floor → the deduction is NOT floored (taxpayer-
        favorable → degrade with a note, never guess)."""
        r = tc.compute_qbi_deduction(
            1500, 0, taxable_income_before_qbi=50000, net_capital_gain=0,
            tax_year=2026, qbi_minimum=None, active_qualified_trade=True)
        assert r["qbi_minimum_applied"] is False
        assert r.get("qbi_minimum_note")  # loud
        assert r["total_deduction"] == 300.0  # 20% of 1,500, unfloored

    def test_salt_cap_absent_falls_back_to_statutory(self):
        """Absent cache → §164(b)(7)(A)'s own schedule.

        This asserted $40,000 for TY2026 and so agreed with a `_salt_cap` that
        returned a FLAT amount for the whole of TY2025-2029, while (A)(ii)
        names $40,400 for TY2026. An absence matrix that pins the wrong
        fallback certifies the defect as the intended behaviour — which is why
        X6 found the same figure wrong in the skill text and nothing here went
        red. Corrected with the schedule, 2026-09-13.
        """
        assert _salt_cap(2026, "Single", None) == 40400.0

    def test_amt_phaseout_absent_falls_back_to_statutory(self):
        """Absent override → statutory 50% for TY2026 (§70107), 25% pre-2026."""
        assert tc._resolve_amt_phaseout_rate(2026, None) == 0.50
        assert tc._resolve_amt_phaseout_rate(2025, None) == 0.25

    @pytest.mark.parametrize("year", [2026, 2027])
    def test_secure_603_absent_fails_closed_for_every_covered_year(self, year):
        """X10 removes the annual default: every covered year is fetched."""
        no_threshold = {"federal": {"retirement_401k": {}}}
        with pytest.raises(RetirementError, match="secure_2_0_high_earner_threshold"):
            _get_secure_2_0_high_earner_threshold(no_threshold, effective_year=year)

    def test_underpayment_rates_absent_degrades_not_computed(self):
        """M3: absent rates → interest not computed (never a guessed rate)."""
        r = tc.compute_underpayment_interest(10000, "2026-04-15", "2026-10-15", [])
        assert r["computed"] is False
        assert r["interest"] is None


class TestCaliforniaPenaltyReadiness:
    """New cache wiring, restricted to the separately reviewed TY2025 form."""

    def test_fetched_policy_and_half_year_schedule_price_the_reviewed_form(self):
        from tests.test_ca_5805 import inputs
        # WorksheetII synthetic case:270*(76*.08+289*.07)/365 +
        # 360*(14*.08+289*.07)/365 +270*90*.07/365 =45.18.
        assert tc.compute_ca_5805(**inputs())['total'] == 45.18

    @pytest.mark.parametrize('block', ['estimated_tax_safe_harbor', 'underpayment_rates'])
    def test_absent_pricing_blocks_fail_closed_when_the_penalty_needs_them(self, block):
        from tests.test_ca_5805 import inputs, law
        state = law()
        del state[block]
        with pytest.raises(ValueError):
            tc.compute_ca_5805(**inputs(state_law=state))

    def test_empty_rate_schedule_is_safe_only_when_known_credits_prove_zero(self):
        from tests.test_ca_5805 import inputs, law
        state = law(); state['underpayment_rates'] = []
        assert tc.compute_ca_5805(**inputs(state_law=state, withholding=900,
            prior_tax=None, prior_full_year=None))['total'] == 0

    def test_new_year_law_does_not_attest_a_new_form_line_map(self):
        from tests.test_ca_5805 import inputs
        with pytest.raises(ValueError, match='form year'):
            tc.compute_ca_5805(**inputs(tax_year=2026))
