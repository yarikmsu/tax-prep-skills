#!/usr/bin/env python3
"""North Carolina Form D-400 — `agents/lib/tax_computations/state/nc.py`.

Z4 slice 3, spec §5 and §7 of
`docs/superpowers/specs/2026-10-02-nc-state-module-design.md`.

Every expected value is HAND-COMPUTED from the TY2025 NCDOR D-401 instructions
and cites the page. The cache figures below are TEST DATA transcribed from those
pages, as `tests/ny_fixtures.py` transcribes IT-201-I: the engine holds none of
them, and each is an argument fetched per year (AGENTS.md §2).
"""

import copy
import json
import math
from pathlib import Path

import pytest

from agents.lib.tax_computations import (
    compute_nc_child_deduction,
    compute_nc_d400,
    compute_nc_deduction_choice,
    compute_nc_itemized,
    compute_nc_other_state_credit,
    compute_nc_schedule_s,
    compute_nc_tax,
)
from agents.lib.tax_computations._shared import _round_half_up

REPO = Path(__file__).resolve().parents[1]

# D-401 (2025) p.13, Child Deduction Table — upper bounds INCLUSIVE.
_MFJ_QSS = [{"agi_up_to": 40000, "per_child": 3000},
            {"agi_up_to": 60000, "per_child": 2500},
            {"agi_up_to": 80000, "per_child": 2000},
            {"agi_up_to": 100000, "per_child": 1500},
            {"agi_up_to": 120000, "per_child": 1000},
            {"agi_up_to": 140000, "per_child": 500},
            {"agi_up_to": None, "per_child": 0}]
_HOH = [{"agi_up_to": 30000, "per_child": 3000},
        {"agi_up_to": 45000, "per_child": 2500},
        {"agi_up_to": 60000, "per_child": 2000},
        {"agi_up_to": 75000, "per_child": 1500},
        {"agi_up_to": 90000, "per_child": 1000},
        {"agi_up_to": 105000, "per_child": 500},
        {"agi_up_to": None, "per_child": 0}]
_SINGLE_MFS = [{"agi_up_to": 20000, "per_child": 3000},
               {"agi_up_to": 30000, "per_child": 2500},
               {"agi_up_to": 40000, "per_child": 2000},
               {"agi_up_to": 50000, "per_child": 1500},
               {"agi_up_to": 60000, "per_child": 1000},
               {"agi_up_to": 70000, "per_child": 500},
               {"agi_up_to": None, "per_child": 0}]

_NC_2025 = {
    "code": "NC", "type": "graduated_income",
    # D-401 p.14 Line 15: "multiply Line 14 by the income tax rate of 4.25%".
    "brackets": [{"rate": 0.0425, "min": 0, "max": None}],
    "rate_tax_year": 2025,
    # D-401 p.14, NC Standard Deduction Chart.
    "standard_deduction": {"single": 12750, "mfj": 25500, "mfs": 12750,
                           "hoh": 19125, "qss": 25500,
                           "mfs_spouse_itemizes": 0},
    "child_deduction": {"mfj_qss": _MFJ_QSS, "hoh": _HOH,
                        "single_mfs": _SINGLE_MFS},
    # D-401 p.20, Schedule A lines 2, 4, 7c and 8.
    "itemized": {"real_estate_tax_cap": {"default": 10000, "mfs": 5000},
                 "mortgage_plus_property_tax_cap": 20000,
                 "medical_agi_floor_rate": 0.075,
                 "claim_of_right": {"repayment_threshold": 3000,
                                    "agi_rate": 0.02}},
    # D-401 p.17, Schedule S intro: "federal adjusted gross income as of
    # January 1, 2023".
    "irc_conformity_date": "2023-01-01",
}


def cfg(**overrides):
    c = copy.deepcopy(_NC_2025)
    c.update(overrides)
    return c


_NO_1099R = {"government_retirement_1099r": False,
             "uniformed_services_retirement_1099r": False}
# The six Schedule S amounts have NO default (an omitted deduction over-states
# NC tax, an omitted line-1 addition under-states it), so every call names them.
_SCHED_S_AMOUNTS = ("exempt_interest", "state_local_refund_in_agi",
                    "us_obligation_interest", "us_government_fund_dividends",
                    "taxable_social_security", "railroad_tier2_in_agi")
_NO_SCHED_S = dict(_NO_1099R, **{name: 0 for name in _SCHED_S_AMOUNTS})
_BAD_NUMBERS = [True, float("nan"), float("inf"), -1.0, "100"]


# ---------------------------------------------------------------------------
# Line 15 — tax
# ---------------------------------------------------------------------------

class TestTax:
    def test_ty2025_rate_times_line_14(self):
        # D-401 p.14: 50,000 × 0.0425 = 2,125.00.
        r = compute_nc_tax(50000, 2025, cfg())
        assert r["line_15_nc_tax"] == 2125.00
        assert r["line_14_nc_taxable_income"] == 50000

    def test_cents_survive(self):
        # 61,500 × 0.0425 = 2,613.75.
        assert compute_nc_tax(61500, 2025, cfg())["line_15_nc_tax"] == 2613.75

    @pytest.mark.parametrize("line_14", [0, -5000.0])
    def test_zero_or_negative_line_14_is_zero_tax(self, line_14):
        # D-401 p.14: "If North Carolina taxable income is zero or less, enter
        # a zero on Line 15" — line 14 itself keeps its sign.
        r = compute_nc_tax(line_14, 2025, cfg())
        assert r["line_15_nc_tax"] == 0
        assert r["line_14_nc_taxable_income"] == line_14

    @pytest.mark.parametrize("stamped", [2024, 2026, None, True])
    def test_a_rate_stamped_for_another_year_refuses(self, stamped):
        with pytest.raises(ValueError, match="rate_tax_year"):
            compute_nc_tax(50000, 2025, cfg(rate_tax_year=stamped))

    def test_ty2026_rate(self):
        # G.S. 105-153.7(a): "After 2025 3.99%". 50,000 × 0.0399 = 1,995.00.
        c = cfg(brackets=[{"rate": 0.0399, "min": 0, "max": None}],
                rate_tax_year=2026)
        assert compute_nc_tax(50000, 2026, c)["line_15_nc_tax"] == 1995.00

    @pytest.mark.parametrize("brackets", [
        [{"rate": None, "min": 0, "max": None}],
        [{"rate": 4.25, "min": 0, "max": None}],
        [{"rate": 0.0425, "min": 0, "max": 100000}],
        [{"rate": 0.0425, "min": 0, "max": None}] * 2,
        [], None])
    def test_a_malformed_rate_band_refuses(self, brackets):
        with pytest.raises(ValueError):
            compute_nc_tax(50000, 2025, cfg(brackets=brackets))

    @pytest.mark.parametrize("low", [False, None, "0", 1])
    def test_the_band_must_start_at_a_numeric_zero(self, low):
        # `False == 0` in Python, so a `!= 0` test alone lets a flag through.
        with pytest.raises(ValueError, match="run from 0"):
            compute_nc_tax(50000, 2025, cfg(brackets=[
                {"rate": 0.0425, "min": low, "max": None}]))

    @pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "1"])
    def test_line_14_validated(self, bad):
        with pytest.raises(ValueError):
            compute_nc_tax(bad, 2025, cfg())


# ---------------------------------------------------------------------------
# Line 10 — child deduction
# ---------------------------------------------------------------------------

# Status → the D-401 p.13 table it reads. QSS ≡ MFJ and MFS ≡ Single there.
_STATUS_TABLE = {"Single": _SINGLE_MFS, "MFS": _SINGLE_MFS, "MFJ": _MFJ_QSS,
                 "QSS": _MFJ_QSS, "HoH": _HOH}


def _boundary_cases():
    for status, table in _STATUS_TABLE.items():
        for i, band in enumerate(table[:-1]):
            top = band["agi_up_to"]
            # AT the bound: this band ("Up to X" is inclusive).
            yield status, top, band["per_child"]
            # A cent over: the next band ("Over X").
            yield status, top + 0.01, table[i + 1]["per_child"]


class TestChildDeduction:
    @pytest.mark.parametrize("status,agi,per_child", list(_boundary_cases()))
    def test_every_band_boundary_is_inclusive(self, status, agi, per_child):
        # D-401 p.13: two children × the table amount.
        r = compute_nc_child_deduction(status, agi, 2, cfg())
        assert r["line_10a_children"] == 2
        assert r["line_10b_child_deduction"] == 2 * per_child

    @pytest.mark.parametrize("status,key", [
        ("Single", "single_mfs"), ("MFS", "single_mfs"), ("MFJ", "mfj_qss"),
        ("QSS", "mfj_qss"), ("HoH", "hoh")])
    def test_all_five_statuses_map_to_one_of_three_tables(self, status, key):
        assert compute_nc_child_deduction(status, 50000, 1, cfg())["table"] == key

    def test_the_mapping_reads_the_cache_not_a_coincidence(self):
        # Change only the mfj_qss table: QSS must move with it, MFS must not.
        c = cfg()
        c["child_deduction"]["mfj_qss"] = [{"agi_up_to": None, "per_child": 7}]
        assert compute_nc_child_deduction("QSS", 1, 1, c)["line_10b_child_deduction"] == 7
        assert compute_nc_child_deduction("MFS", 1, 1, c)["line_10b_child_deduction"] == 3000

    def test_zero_children(self):
        assert compute_nc_child_deduction("MFJ", 30000, 0, cfg())[
            "line_10b_child_deduction"] == 0

    def test_at_and_above_the_top_band(self):
        # D-401 p.13: MFJ "Up to $140,000 $500", "Over $140,000 $0".
        assert compute_nc_child_deduction("MFJ", 140000, 3, cfg())[
            "line_10b_child_deduction"] == 1500
        assert compute_nc_child_deduction("MFJ", 10_000_000, 3, cfg())[
            "line_10b_child_deduction"] == 0

    def test_negative_agi_lands_in_the_first_band(self):
        assert compute_nc_child_deduction("Single", -2000, 1, cfg())[
            "line_10b_child_deduction"] == 3000

    @pytest.mark.parametrize("n", [True, -1, 1.5, "2", None])
    def test_the_child_count_is_validated(self, n):
        with pytest.raises(ValueError):
            compute_nc_child_deduction("Single", 50000, n, cfg())

    @pytest.mark.parametrize("table", [
        [],
        [{"agi_up_to": 40000, "per_child": 3000}],                      # closed top
        [{"agi_up_to": None, "per_child": 3000},
         {"agi_up_to": None, "per_child": 0}],                         # open middle
        [{"agi_up_to": 60000, "per_child": 3000},
         {"agi_up_to": 40000, "per_child": 2500},
         {"agi_up_to": None, "per_child": 0}],                         # descending
        [{"agi_up_to": 40000, "per_child": 2000},
         {"agi_up_to": None, "per_child": 3000}],                      # rising
        [{"agi_up_to": 40000, "per_child": None},
         {"agi_up_to": None, "per_child": 0}],                         # unfetched
    ])
    def test_a_malformed_table_refuses(self, table):
        c = cfg()
        c["child_deduction"]["single_mfs"] = table
        with pytest.raises(ValueError):
            compute_nc_child_deduction("Single", 50000, 1, c)

    @pytest.mark.parametrize("bad", ["married", "", None, 1])
    def test_the_status_is_validated(self, bad):
        with pytest.raises(ValueError):
            compute_nc_child_deduction(bad, 50000, 1, cfg())


# ---------------------------------------------------------------------------
# Line 11 — standard deduction and the choice
# ---------------------------------------------------------------------------

def _choice(status, itemized=0.0, spouse=None, dependent=False, eligible=True,
            c=None):
    return compute_nc_deduction_choice(
        status, itemized, c or cfg(), spouse_itemizes_nc=spouse,
        claimed_as_dependent=dependent,
        eligible_for_federal_standard_deduction=eligible)


class TestStandardDeduction:
    @pytest.mark.parametrize("status,spouse,amount", [
        # D-401 p.14, NC Standard Deduction Chart.
        ("Single", None, 12750), ("MFJ", None, 25500), ("QSS", None, 25500),
        ("HoH", None, 19125), ("MFS", False, 12750)])
    def test_five_statuses(self, status, spouse, amount):
        r = _choice(status, spouse=spouse)
        assert r["line_11_deduction"] == amount
        assert r["line_11_method"] == "standard"

    def test_qss_reads_its_own_key(self):
        c = cfg()
        c["standard_deduction"]["qss"] = 1
        assert _choice("QSS", c=c)["standard_deduction"] == 1

    def test_mfs_spouse_itemizes_is_zero(self):
        # D-401 p.14: "If spouse claims itemized deductions 0".
        r = _choice("MFS", spouse=True)
        assert r["standard_deduction"] == 0
        assert r["line_11_deduction"] == 0

    def test_mfs_spouse_itemizes_takes_the_itemized_total(self):
        r = _choice("MFS", itemized=4000, spouse=True)
        assert (r["line_11_deduction"], r["line_11_method"]) == (4000, "itemized")

    def test_mfs_without_the_spouse_answer_refuses(self):
        with pytest.raises(ValueError, match="spouse_itemizes_nc"):
            _choice("MFS")

    def test_the_spouse_answer_on_another_status_refuses(self):
        with pytest.raises(ValueError):
            _choice("Single", spouse=False)

    def test_ineligible_for_the_federal_standard_deduction_is_zero(self):
        # D-401 p.14: "not eligible for the federal standard deduction, your
        # NC standard deduction is ZERO".
        r = _choice("Single", eligible=False)
        assert r["line_11_deduction"] == 0

    def test_a_dependent_filer_refuses(self):
        with pytest.raises(ValueError, match="dependent"):
            _choice("Single", dependent=True)

    @pytest.mark.parametrize("bad", [None, 0, "no"])
    def test_the_required_flags_are_booleans(self, bad):
        with pytest.raises(ValueError):
            _choice("Single", dependent=bad)
        with pytest.raises(ValueError):
            _choice("Single", eligible=bad)

    def test_itemized_wins_only_when_larger(self):
        assert _choice("Single", itemized=13000)["line_11_method"] == "itemized"
        assert _choice("Single", itemized=13000)["line_11_deduction"] == 13000
        tie = _choice("Single", itemized=12750)
        assert (tie["line_11_method"], tie["line_11_deduction"]) == ("standard", 12750)

    def test_an_unfetched_standard_deduction_refuses(self):
        c = cfg()
        c["standard_deduction"]["hoh"] = None
        with pytest.raises(ValueError, match="standard_deduction.hoh is not in"):
            _choice("HoH", c=c)


# ---------------------------------------------------------------------------
# NC Schedule A
# ---------------------------------------------------------------------------

_ANSWERS = {"mortgage_over_acquisition_limit": False, "home_equity_debt": False,
            "charitable_agi_limit_may_bind": False}


def _sched_a(status="Single", agi=100000, c=None, **kw):
    args = dict(_ANSWERS)
    args.update(kw)
    return compute_nc_itemized(status, agi, c or cfg(), **args)


class TestScheduleA:
    def test_the_worked_case(self):
        # D-401 p.20. Single, AGI 100,000:
        #   1 mortgage 8,000; 2 RE 12,000 capped at 10,000; 3 = 18,000;
        #   4 limit 20,000; 5 = 18,000; 6 charitable 2,000;
        #   7a 9,000; 7b 100,000; 7c 7.5% = 7,500; 7d 1,500; 8 = 0;
        #   10 = 18,000 + 2,000 + 1,500 = 21,500.
        r = _sched_a(mortgage_interest=8000, real_estate_tax=12000,
                     charitable=2000, medical=9000)
        assert (r["line_1_mortgage_interest"], r["line_2_real_estate_tax"],
                r["line_3"], r["line_4_mortgage_and_property_tax"],
                r["line_5"]) == (8000, 10000, 18000, 20000, 18000)
        assert r["line_6_charitable"] == 2000
        assert (r["line_7a"], r["line_7b"], r["line_7c"],
                r["line_7d_medical"]) == (9000, 100000, 7500, 1500)
        assert (r["line_8_claim_of_right"], r["line_9"], r["line_10"]) == (0, 0, 21500)

    def test_the_mfs_real_estate_cap(self):
        # D-401 p.20 line 2: MFS "may not deduct more than $5,000".
        r = _sched_a("MFS", real_estate_tax=7000, paid_by={"real_estate_tax": "filer"},
                     spouse_mortgage_and_property_tax=0)
        assert r["line_2_real_estate_tax"] == 5000

    def test_the_combined_20k_limit_binds(self):
        # D-401 p.20 line 4: MFJ 15,000 + 9,000 = 24,000 → 20,000.
        r = _sched_a("MFJ", mortgage_interest=15000, real_estate_tax=9000)
        assert (r["line_3"], r["line_5"]) == (24000, 20000)

    def test_mfs_household_limit_is_prorated_by_share_paid(self):
        # D-401 p.20 line 4: both spouses combined may not exceed 20,000,
        # prorated by share. Filer 12,000 + 4,000 = 16,000, spouse 9,000;
        # household 25,000 → limit 20,000 × 16,000 / 25,000 = 12,800.
        r = _sched_a("MFS", mortgage_interest=12000, real_estate_tax=4000,
                     paid_by={"mortgage_interest": "filer",
                              "real_estate_tax": "filer"},
                     spouse_mortgage_and_property_tax=9000)
        assert (r["line_3"], r["line_4_mortgage_and_property_tax"],
                r["line_5"]) == (16000, 12800, 12800)

    def test_mfs_proration_is_on_amounts_paid_before_the_cap(self):
        # D-401 p.20 line 4: "If the amount of home mortgage interest and real
        # estate property taxes PAID by both spouses exceeds $20,000, the
        # deductions must be prorated based on the percentage PAID by each".
        # Filer: mortgage 12,000 + RE tax 8,000 paid (line 2 caps it at the MFS
        # 5,000, so line 3 = 17,000). Spouse paid 5,000.
        #   paid:   household 25,000 → limit 20,000 × 20,000 / 25,000 = 16,000
        #   (line-3 reading: 22,000 → 20,000 × 17,000 / 22,000 = 15,454.55)
        #   line 5 = min(17,000, 16,000) = 16,000.
        r = _sched_a("MFS", mortgage_interest=12000, real_estate_tax=8000,
                     paid_by={"mortgage_interest": "filer",
                              "real_estate_tax": "filer"},
                     spouse_mortgage_and_property_tax=5000)
        assert (r["line_2_real_estate_tax"], r["line_3"]) == (5000, 17000)
        assert (r["line_4_mortgage_and_property_tax"], r["line_5"]) == (16000, 16000)

    def test_mfs_the_over_20k_test_is_on_amounts_paid(self):
        # Filer mortgage 8,000 + RE tax 9,000 paid (capped 5,000; line 3 =
        # 13,000), spouse paid 6,000: 23,000 PAID exceeds 20,000 although the
        # line-3 figures (19,000) do not → limit 20,000 × 17,000 / 23,000 =
        # 14,782.61; line 5 = 13,000.
        r = _sched_a("MFS", mortgage_interest=8000, real_estate_tax=9000,
                     paid_by={"mortgage_interest": "filer",
                              "real_estate_tax": "filer"},
                     spouse_mortgage_and_property_tax=6000)
        assert (r["line_4_mortgage_and_property_tax"], r["line_5"]) == (
            14782.61, 13000)

    def test_mfs_under_the_household_limit_keeps_the_full_limit(self):
        r = _sched_a("MFS", mortgage_interest=6000,
                     paid_by={"mortgage_interest": "filer"},
                     spouse_mortgage_and_property_tax=4000)
        assert (r["line_4_mortgage_and_property_tax"], r["line_5"]) == (20000, 6000)

    def test_the_medical_floor_is_on_federal_agi(self):
        # 7c = 60,000 × 7.5% = 4,500; 7d = 5,000 − 4,500 = 500.
        r = _sched_a(agi=60000, medical=5000)
        assert (r["line_7c"], r["line_7d_medical"]) == (4500, 500)
        # Below the floor → 0 (D-401 p.20 line 7d).
        assert _sched_a(agi=60000, medical=4000)["line_7d_medical"] == 0

    def test_a_negative_agi_floor_is_zero(self):
        # D-401 p.20 line 7c: "If this amount is zero or less, enter a zero".
        r = _sched_a(agi=-10000, medical=500)
        assert (r["line_7b"], r["line_7c"], r["line_7d_medical"]) == (-10000, 0, 500)

    @pytest.mark.parametrize("repaid,expected", [
        # D-401 p.20 worksheet, AGI 50,000 → 2% = 1,000.
        (2500, 1500), (3000, 2000), (800, 0),
        # "If the repayment is more than $3,000, the deduction is the amount".
        (3000.01, 3000.01)])
    def test_claim_of_right(self, repaid, expected):
        assert _sched_a(agi=50000, claim_of_right_repayment=repaid)[
            "line_8_claim_of_right"] == expected

    def test_no_state_income_or_sales_tax_line_exists(self):
        with pytest.raises(TypeError):
            _sched_a(state_local_income_taxes=5000)

    def test_mortgage_insurance_premiums_block(self):
        with pytest.raises(ValueError, match="box 5"):
            _sched_a(mortgage_interest=5000, mortgage_insurance_premiums=300)

    @pytest.mark.parametrize("name", ["mortgage_over_acquisition_limit",
                                      "home_equity_debt"])
    @pytest.mark.parametrize("answer", [None, True])
    def test_the_mortgage_questions_block(self, name, answer):
        with pytest.raises(ValueError, match=name):
            _sched_a(mortgage_interest=5000, **{name: answer})

    def test_the_mortgage_questions_are_moot_without_mortgage_interest(self):
        r = _sched_a(real_estate_tax=3000, mortgage_over_acquisition_limit=None,
                     home_equity_debt=None)
        assert r["line_5"] == 3000

    @pytest.mark.parametrize("answer", [None, True])
    def test_gifts_the_agi_limit_may_reach_block(self, answer):
        with pytest.raises(ValueError, match="charitable_agi_limit_may_bind"):
            _sched_a(charitable=4000, charitable_agi_limit_may_bind=answer)

    @pytest.mark.parametrize("paid_by,match", [
        (None, "paid_by"), ({}, "paid_by"), ({"medical": "spouse"}, "spouse"),
        ({"medical": "joint_account"}, "joint_account"),
        ({"medical": "me"}, "paid_by"), ({"medical": "filer", "boat": "filer"},
                                         "unknown")])
    def test_mfs_paid_by_is_required_per_item(self, paid_by, match):
        with pytest.raises(ValueError, match=match):
            _sched_a("MFS", agi=20000, medical=5000, paid_by=paid_by)

    def test_mfs_without_the_spouse_line_3_refuses(self):
        with pytest.raises(ValueError, match="HOUSEHOLD"):
            _sched_a("MFS", mortgage_interest=5000,
                     paid_by={"mortgage_interest": "filer"})

    def test_mfs_only_inputs_refuse_on_another_status(self):
        with pytest.raises(ValueError):
            _sched_a("Single", medical=10, paid_by={"medical": "filer"})
        with pytest.raises(ValueError):
            _sched_a("Single", spouse_mortgage_and_property_tax=0)

    def test_an_unfetched_conformity_date_refuses(self):
        with pytest.raises(ValueError, match="irc_conformity_date"):
            _sched_a(c=cfg(irc_conformity_date=None), medical=1)

    def test_an_unfetched_cap_refuses(self):
        c = cfg()
        c["itemized"]["mortgage_plus_property_tax_cap"] = None
        with pytest.raises(ValueError, match="mortgage_plus_property_tax_cap is not in"):
            _sched_a(c=c, real_estate_tax=100)

    def test_a_floor_written_as_a_percentage_refuses(self):
        c = cfg()
        c["itemized"]["medical_agi_floor_rate"] = 7.5
        with pytest.raises(ValueError, match="decimal share"):
            _sched_a(c=c, medical=100)

    @pytest.mark.parametrize("path,kw", [
        (("medical_agi_floor_rate",), {"medical": 100}),
        (("claim_of_right", "agi_rate"), {"claim_of_right_repayment": 100})])
    def test_a_zero_rate_is_the_unfetched_state(self, path, kw):
        # A fetched 0.0 is what an unfilled template holds; a zero floor would
        # deduct every dollar of medical or claim-of-right repayment.
        c = cfg()
        node = c["itemized"]
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = 0.0
        with pytest.raises(ValueError, match="unfetched"):
            _sched_a(c=c, **kw)

    @pytest.mark.parametrize("name", list(_ANSWERS))
    @pytest.mark.parametrize("bad", ["no", 0, 1, "False"])
    def test_the_answers_are_validated_with_no_amount(self, name, bad):
        # Validated whenever passed, not only when the amount they qualify is
        # non-zero — a malformed answer is a malformed input either way.
        with pytest.raises(ValueError, match=name):
            _sched_a(medical=100, **{name: bad})

    @pytest.mark.parametrize("bad", _BAD_NUMBERS)
    def test_the_mfs_spouse_figure_is_validated_with_no_line_3(self, bad):
        with pytest.raises(ValueError, match="spouse_mortgage_and_property_tax"):
            _sched_a("MFS", agi=20000, medical=5000,
                     paid_by={"medical": "filer"},
                     spouse_mortgage_and_property_tax=bad)

    @pytest.mark.parametrize("name", ["mortgage_interest", "real_estate_tax",
                                      "charitable", "medical",
                                      "claim_of_right_repayment",
                                      "mortgage_insurance_premiums"])
    @pytest.mark.parametrize("bad", _BAD_NUMBERS)
    def test_validators(self, name, bad):
        with pytest.raises(ValueError):
            _sched_a(**{name: bad})


# ---------------------------------------------------------------------------
# Schedule S
# ---------------------------------------------------------------------------

def _sched_s(c=None, year=2025, **kw):
    args = dict(_NO_SCHED_S)
    args.update(kw)
    return compute_nc_schedule_s(year, c or cfg(), **args)


class TestScheduleS:
    def test_social_security_and_railroad(self):
        # D-401 p.18 line 19: taxable SS (incl. tier 1) 18,000 + tier 2 2,000.
        r = _sched_s(taxable_social_security=18000, railroad_tier2_in_agi=2000)
        assert r["line_19_taxable_social_security"] == 20000
        assert r["line_41_total_deductions"] == 20000

    def test_us_obligation_interest_direct_and_through_funds(self):
        # D-401 p.18 line 18: 300 direct + 1,000 fund dividends × 40% = 700.
        r = _sched_s(us_obligation_interest=300, us_government_fund_dividends=1000,
                     us_government_share=0.4)
        assert r["line_18_us_obligation_interest"] == 700

    def test_fund_dividends_without_a_share_refuse(self):
        with pytest.raises(ValueError, match="us_government_share"):
            _sched_s(us_government_fund_dividends=1000)

    def test_state_refund(self):
        # D-401 p.18 line 17.
        r = _sched_s(state_local_refund_in_agi=450)
        assert r["line_17_state_local_refund"] == 450
        assert r["line_41_total_deductions"] == 450

    def test_a1_with_the_nc_share(self):
        # D-401 p.17 line 1: 2,000 exempt × (1 − 25% NC) = 1,500.
        r = _sched_s(exempt_interest=2000, nc_obligation_share=0.25)
        assert r["line_1_non_nc_obligation_interest"] == 1500
        assert r["line_16_total_additions"] == 1500

    def test_a1_without_the_share_refuses(self):
        with pytest.raises(ValueError, match="nc_obligation_share"):
            _sched_s(exempt_interest=2000)

    @pytest.mark.parametrize("share", [25, 1.01, -0.1, float("nan"), True])
    def test_a_share_outside_zero_to_one_refuses(self, share):
        with pytest.raises(ValueError):
            _sched_s(exempt_interest=2000, nc_obligation_share=share)

    def test_bailey_refusal_on_a_government_1099r(self):
        with pytest.raises(ValueError, match="bailey_retirement"):
            _sched_s(government_retirement_1099r=True)

    def test_military_1099r_needs_both_answers(self):
        with pytest.raises(ValueError, match="bailey_retirement"):
            _sched_s(uniformed_services_retirement_1099r=True,
                     uniformed_services_retirement=0)
        with pytest.raises(ValueError, match="uniformed_services_retirement"):
            _sched_s(uniformed_services_retirement_1099r=True, bailey_retirement=0)

    @pytest.mark.parametrize("name", _SCHED_S_AMOUNTS)
    def test_every_amount_is_a_required_argument(self, name):
        args = dict(_NO_SCHED_S)
        del args[name]
        with pytest.raises(TypeError, match=name):
            compute_nc_schedule_s(2025, cfg(), **args)

    def test_the_presence_flags_are_required(self):
        with pytest.raises(TypeError):
            compute_nc_schedule_s(2025, cfg())
        with pytest.raises(ValueError):
            _sched_s(government_retirement_1099r=None)

    def test_bailey_and_uniformed_amounts(self):
        # D-401 pp.18-19: 20,000 Bailey + 5,000 line 21, bounded by 30,000 of
        # taxable government/uniformed-services 1099-R income; line 41 = 25,000.
        r = _sched_s(government_retirement_1099r=True,
                     uniformed_services_retirement_1099r=True,
                     bailey_retirement=20000, uniformed_services_retirement=5000,
                     government_retirement_taxable=30000)
        assert (r["line_20_bailey_retirement"],
                r["line_21_uniformed_services_retirement"],
                r["line_41_total_deductions"]) == (20000, 5000, 25000)

    def test_an_answered_zero_is_accepted(self):
        r = _sched_s(government_retirement_1099r=True, bailey_retirement=0)
        assert r["line_20_bailey_retirement"] == 0

    def test_retirement_deductions_beyond_the_government_1099rs_refuse(self):
        with pytest.raises(ValueError, match="exceed"):
            _sched_s(government_retirement_1099r=True, bailey_retirement=20000,
                     government_retirement_taxable=19999.99)
        with pytest.raises(ValueError, match="to bound them"):
            _sched_s(government_retirement_1099r=True, bailey_retirement=1)

    def test_private_pension_income_is_no_bound(self):
        # A Bailey retiree with 10,000 taxable from the state plan and 40,000
        # of private IRA income: 1040 lines 4b + 5b are 50,000, but only the
        # 10,000 is Bailey-eligible, so a 15,000 claim refuses.
        with pytest.raises(TypeError):
            _sched_s(government_retirement_1099r=True, bailey_retirement=15000,
                     taxable_pensions=50000)
        with pytest.raises(ValueError, match="exceed"):
            _sched_s(government_retirement_1099r=True, bailey_retirement=15000,
                     government_retirement_taxable=10000)

    @pytest.mark.parametrize("bad", _BAD_NUMBERS)
    def test_the_bound_is_validated_whenever_passed(self, bad):
        with pytest.raises(ValueError, match="government_retirement_taxable"):
            _sched_s(government_retirement_1099r=True, bailey_retirement=0,
                     government_retirement_taxable=bad)

    def test_a_government_amount_with_no_government_1099r_refuses(self):
        with pytest.raises(ValueError, match="presence flag"):
            _sched_s(government_retirement_taxable=1000)

    def test_a_deduction_with_no_1099r_refuses(self):
        with pytest.raises(ValueError):
            _sched_s(bailey_retirement=100, government_retirement_taxable=1000)
        with pytest.raises(ValueError):
            _sched_s(government_retirement_1099r=True, bailey_retirement=0,
                     uniformed_services_retirement=100,
                     government_retirement_taxable=1000)

    def test_an_unread_form_year_refuses(self):
        with pytest.raises(ValueError, match="D-401"):
            _sched_s(year=2026)

    def test_an_unfetched_conformity_date_refuses(self):
        with pytest.raises(ValueError, match="irc_conformity_date"):
            _sched_s(c=cfg(irc_conformity_date="Jan 1 2023"))

    @pytest.mark.parametrize("name", [
        "exempt_interest", "state_local_refund_in_agi", "us_obligation_interest",
        "us_government_fund_dividends", "taxable_social_security",
        "railroad_tier2_in_agi"])
    @pytest.mark.parametrize("bad", _BAD_NUMBERS)
    def test_validators(self, name, bad):
        with pytest.raises(ValueError):
            _sched_s(nc_obligation_share=0, us_government_share=0, **{name: bad})


# ---------------------------------------------------------------------------
# D-400TC Parts 1 and 3
# ---------------------------------------------------------------------------

class TestOtherStateCredit:
    def test_one_state_net_tax_paid_is_the_lesser(self):
        # D-401 pp.22-23: line 15 = 4,250; 30,000 / 120,000 = 0.25;
        # line 5 = 1,062.50; line 6 = 900 → credit 900.
        r = compute_nc_other_state_credit(
            4250, 120000, [{"jurisdiction": "VA", "income_taxed": 30000,
                            "net_tax_paid": 900}])
        row = r["part_1"][0]
        assert (row["line_3"], row["line_5"], row["line_7"]) == (0.25, 1062.50, 900)
        assert (r["line_7a"], r["line_7b"], r["line_20"]) == (900, 1, 900)

    def test_one_state_the_ratio_share_is_the_lesser(self):
        r = compute_nc_other_state_credit(
            4250, 120000, [{"jurisdiction": "VA", "income_taxed": 30000,
                            "net_tax_paid": 1500}])
        assert r["line_20"] == 1062.50

    def test_two_states_one_worksheet_each(self):
        # 100,000 of income: SC 20,000 → 0.2 × 4,250 = 850 vs 1,000 paid → 850;
        # GA 10,000 → 0.1 × 4,250 = 425 vs 300 paid → 300. Total 1,150.
        r = compute_nc_other_state_credit(4250, 100000, [
            {"jurisdiction": "SC", "income_taxed": 20000, "net_tax_paid": 1000},
            {"jurisdiction": "GA", "income_taxed": 10000, "net_tax_paid": 300}])
        assert [x["line_7"] for x in r["part_1"]] == [850, 300]
        assert (r["line_7a"], r["line_7b"], r["line_16"], r["line_20"]) == (
            1150, 2, 1150, 1150)

    def test_the_ratio_rounds_to_four_places(self):
        # D-401 p.22 line 3: 1 / 3 → 0.3333; 1,000 × 0.3333 = 333.30, not 333.33.
        r = compute_nc_other_state_credit(1000, 3, [
            {"jurisdiction": "VA", "income_taxed": 1, "net_tax_paid": 999}])
        assert (r["part_1"][0]["line_3"], r["line_20"]) == (0.3333, 333.30)

    def test_the_ratio_rounds_an_exact_tie_half_up(self):
        # D-401 p.22 line 3 "round to four decimal places": 9 / 4,000 = 0.00225
        # exactly → 0.0023 (half-even `round` gives 0.0022). Line 5 =
        # 1,000 × 0.0023 = 2.30.
        r = compute_nc_other_state_credit(1000, 4000, [
            {"jurisdiction": "VA", "income_taxed": 9, "net_tax_paid": 999}])
        assert (r["part_1"][0]["line_3"], r["line_20"]) == (0.0023, 2.30)

    def test_the_shared_half_up_rule(self):
        # One rule for NY's whole dollars and NC's four places.
        assert int(_round_half_up(2416.50)) == 2417
        assert float(_round_half_up(0.00225, 4)) == 0.0023
        assert float(_round_half_up(0.00224999, 4)) == 0.0022

    def test_part_3_caps_credits_at_line_15(self):
        # Two 0.6 ratios against a 1,000 tax → 600 + 600 = 1,200; D-401 p.24
        # Part 3 line 18 and line 20: "cannot exceed ... Line 15" → 1,000.
        r = compute_nc_other_state_credit(1000, 100000, [
            {"jurisdiction": "VA", "income_taxed": 60000, "net_tax_paid": 5000},
            {"jurisdiction": "SC", "income_taxed": 60000, "net_tax_paid": 5000}])
        assert (r["line_16"], r["line_18"], r["line_20"]) == (1200, 1000, 1000)

    def test_no_states_no_credit(self):
        assert compute_nc_other_state_credit(4250)["line_20"] == 0

    @pytest.mark.parametrize("rows", [None, []])
    def test_none_and_an_empty_list_both_mean_no_other_state(self, rows):
        r = compute_nc_other_state_credit(4250, 100000, rows)
        assert (r["part_1"], r["line_20"]) == ([], 0)

    @pytest.mark.parametrize("rows", ["", {}, "VA", 0, False,
                                      {"jurisdiction": "VA", "income_taxed": 1,
                                       "net_tax_paid": 1}])
    def test_other_states_must_be_a_list(self, rows):
        with pytest.raises(ValueError, match="must be a list"):
            compute_nc_other_state_credit(4250, 100000, rows)

    @pytest.mark.parametrize("rows", [None, []])
    @pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "100"])
    def test_income_all_sources_is_validated_with_no_state(self, rows, bad):
        with pytest.raises(ValueError, match="income_all_sources"):
            compute_nc_other_state_credit(4250, bad, rows)

    @pytest.mark.parametrize("rows,income,match", [
        ([{"jurisdiction": "VA", "income_taxed": 2, "net_tax_paid": 1}], 1, "exceeds"),
        ([{"jurisdiction": "VA", "income_taxed": 1, "net_tax_paid": 1}], -5, "exceeds"),
        ([{"jurisdiction": "VA", "income_taxed": 1, "net_tax_paid": 1}], 0, "exceeds"),
        ([{"jurisdiction": "VA", "income_taxed": 1, "net_tax_paid": 1}] * 2, 10, "twice"),
        ([{"jurisdiction": "nc", "income_taxed": 1, "net_tax_paid": 1}], 10, "another"),
        ([{"jurisdiction": "", "income_taxed": 1, "net_tax_paid": 1}], 10, "jurisdiction"),
        ([{"jurisdiction": "VA", "income_taxed": 1, "net_tax_paid": 1}], None,
         "income_all_sources"),
    ])
    def test_refusals(self, rows, income, match):
        with pytest.raises(ValueError, match=match):
            compute_nc_other_state_credit(1000, income, rows)

    @pytest.mark.parametrize("field", ["income_taxed", "net_tax_paid"])
    @pytest.mark.parametrize("bad", _BAD_NUMBERS + [None])
    def test_validators(self, field, bad):
        row = {"jurisdiction": "VA", "income_taxed": 1, "net_tax_paid": 1}
        row[field] = bad
        with pytest.raises(ValueError):
            compute_nc_other_state_credit(1000, 10, [row])


# ---------------------------------------------------------------------------
# Form D-400, lines 6-34
# ---------------------------------------------------------------------------

def _d400(**kw):
    args = dict(
        tax_year=2025, filing_status="MFJ", federal_agi=90000, state_cfg=cfg(),
        schedule_s=dict(_NO_SCHED_S, exempt_interest=1000, nc_obligation_share=0,
                        us_obligation_interest=1000),
        ctc_qualifying_children=2, itemized=None, spouse_itemizes_nc=None,
        claimed_as_dependent=False, eligible_for_federal_standard_deduction=True,
        use_tax=25, withholding=2000, spouse_withholding=500,
        underpayment_interest=10, federal_extension_circle=False)
    args.update(kw)
    return compute_nc_d400(**args)


class TestD400:
    def test_balance_due(self):
        # MFJ, AGI 90,000 (D-401 pp.12-16):
        #   7 = Sch S line 1, 1,000 non-NC exempt interest; 8 = 91,000
        #   9 = Sch S line 18, 1,000 US interest
        #   10b = 2 × 1,500 (MFJ "Over $80,000 - Up to $100,000", p.13) = 3,000
        #   11 = MFJ standard 25,500 (p.14)
        #   12a = 1,000 + 3,000 + 25,500 = 29,500; 12b = 14 = 61,500
        #   15 = 61,500 × 4.25% = 2,613.75; 16 = 0; 17 = 2,613.75
        #   18 = 25; 19 = 2,638.75
        #   20a 2,000 + 20b 500 = 23 = 25 = 2,500
        #   26a = 138.75; 26e = 10; 27 = 148.75; 28 = 0
        r = _d400()
        assert (r["line_6_federal_agi"], r["line_7_additions"], r["line_8"],
                r["line_9_deductions"]) == (90000, 1000, 91000, 1000)
        assert (r["line_10a_children"], r["line_10b_child_deduction"]) == (2, 3000)
        assert (r["line_11_deduction"], r["line_11_method"]) == (25500, "standard")
        assert (r["line_12a"], r["line_12b"], r["line_14_nc_taxable_income"]) == (
            29500, 61500, 61500)
        assert (r["line_15_nc_tax"], r["line_16_credits"], r["line_17"]) == (
            2613.75, 0, 2613.75)
        assert (r["line_18_use_tax"], r["line_19"]) == (25, 2638.75)
        assert (r["line_23"], r["line_25"]) == (2500, 2500)
        assert (r["line_26a_tax_due"], r["line_26d"], r["line_26e"],
                r["line_27_amount_due"], r["line_28_overpayment"]) == (
            138.75, 0, 10, 148.75, 0)
        assert (r["line_33"], r["line_34_refund"]) == (0, 0)

    def test_overpayment_with_contributions(self):
        # Same return, 20a 3,000 + 20b 500 = 3,500; excess 861.25;
        # 28 = 861.25 − 26e 10 = 851.25 (p.16); 29 100 + 31 50 = 33 150;
        # 34 = 701.25; 27 = 0.
        r = _d400(withholding=3000, applied_to_next_year=100, education_fund=50)
        assert (r["line_26a_tax_due"], r["line_27_amount_due"],
                r["line_28_overpayment"]) == (0, 0, 851.25)
        assert (r["line_29"], r["line_31"], r["line_33"], r["line_34_refund"]) == (
            100, 50, 150, 701.25)

    def test_26e_larger_than_the_overpayment_moves_to_line_27(self):
        # excess 861.25, 26e 900 → 28 = 0, 27 = 38.75.
        r = _d400(withholding=3000, underpayment_interest=900)
        assert (r["line_28_overpayment"], r["line_27_amount_due"]) == (0, 38.75)

    def test_penalties_and_interest(self):
        # 26d = 26b + 26c; 27 = 26a + 26d + 26e = 138.75 + 30 + 10 = 178.75.
        r = _d400(penalties=20, interest=10)
        assert (r["line_26d"], r["line_27_amount_due"]) == (30, 178.75)

    def test_estimated_and_extension_payments(self):
        # 23 = 2,000 + 500 + 21a 100 + 21b 38.75 = 2,638.75 = line 19.
        r = _d400(estimated_payments=100, extension_payment=38.75,
                  underpayment_interest=0)
        assert (r["line_23"], r["line_26a_tax_due"], r["line_27_amount_due"],
                r["line_28_overpayment"]) == (2638.75, 0, 0, 0)

    def test_use_tax_unanswered_is_null_not_zero(self):
        r = _d400(use_tax=None)
        assert r["line_18_use_tax"] is None
        assert r["line_19"] == r["line_17"] == 2613.75
        assert any("USE TAX NOT ANSWERED" in n for n in r["notes"])
        # Unanswered is null beside a null line 18, as the template ships it —
        # `False` would read as an answer.
        assert r["use_tax_none_due_circle"] is None

    def test_a_stated_use_tax_leaves_the_circle_unfilled(self):
        r = _d400(use_tax=25)
        assert (r["line_18_use_tax"], r["use_tax_none_due_circle"]) == (25, False)

    def test_use_tax_certified_none(self):
        r = _d400(use_tax=None, use_tax_certified_none=True)
        assert (r["line_18_use_tax"], r["use_tax_none_due_circle"]) == (0, True)
        assert not any("USE TAX NOT ANSWERED" in n for n in r["notes"])

    def test_use_tax_contradiction_and_uncertified_zero_refuse(self):
        with pytest.raises(ValueError, match="contradict"):
            _d400(use_tax=25, use_tax_certified_none=True)
        with pytest.raises(ValueError, match="certified"):
            _d400(use_tax=0)

    def test_contributions_beyond_the_overpayment_refuse(self):
        with pytest.raises(ValueError, match="overpayment"):
            _d400(wildlife_fund=1)

    def test_spouse_withholding_on_a_separate_return_refuses(self):
        with pytest.raises(ValueError, match="20b"):
            _d400(filing_status="Single", spouse_withholding=500)

    def test_itemized_path_and_credit(self):
        # Single, AGI 100,000, no Sch S items, no children:
        #   Sch A (p.20): 8,000 + 10,000 (RE capped) = 18,000; charitable 2,000;
        #   medical 9,000 − 7,500 = 1,500; line 10 = 21,500 > 12,750 standard.
        #   12a = 21,500; 14 = 78,500; 15 = 78,500 × 4.25% = 3,336.25.
        #   D-400TC: 40,000 / 100,000 = 0.4 → 1,334.50 vs 1,200 paid → 1,200.
        #   17 = 2,136.25; use tax certified none; 19 = 2,136.25.
        r = _d400(filing_status="Single", federal_agi=100000,
                  schedule_s=dict(_NO_SCHED_S), ctc_qualifying_children=0,
                  itemized=dict(_ANSWERS, mortgage_interest=8000,
                                real_estate_tax=12000, charitable=2000,
                                medical=9000),
                  other_state_credit={"income_all_sources": 100000,
                                      "other_states": [{"jurisdiction": "VA",
                                                        "income_taxed": 40000,
                                                        "net_tax_paid": 1200}]},
                  use_tax=None, use_tax_certified_none=True, spouse_withholding=0,
                  withholding=2136.25, underpayment_interest=0)
        assert (r["line_11_deduction"], r["line_11_method"]) == (21500, "itemized")
        assert r["schedule_a"]["line_10"] == 21500
        assert (r["line_14_nc_taxable_income"], r["line_15_nc_tax"]) == (78500, 3336.25)
        assert (r["line_16_credits"], r["line_17"], r["line_19"]) == (
            1200, 2136.25, 2136.25)
        assert (r["line_27_amount_due"], r["line_34_refund"]) == (0, 0)

    def test_negative_line_14(self):
        r = _d400(federal_agi=20000, use_tax=None, use_tax_certified_none=True,
                  withholding=0, spouse_withholding=0, underpayment_interest=0)
        # 8 = 21,000; 12a = 1,000 + 6,000 (2 × 3,000, "Up to $40,000") +
        # 25,500 = 32,500; 12b = −11,500; tax 0.
        assert (r["line_12b"], r["line_15_nc_tax"]) == (-11500, 0)

    def test_the_circles_pass_through(self):
        r = _d400(federal_extension_circle=True, out_of_country_circle=False)
        assert (r["federal_extension_circle"], r["out_of_country_circle"]) == (
            True, False)
        r = _d400(federal_extension_circle=None)
        assert r["federal_extension_circle"] is None
        assert any("Federal Extension" in n for n in r["notes"])
        with pytest.raises(ValueError):
            _d400(out_of_country_circle="yes")

    def test_an_unread_form_year_refuses(self):
        with pytest.raises(ValueError, match="D-401"):
            _d400(tax_year=2026)

    def test_a_dependent_filer_refuses(self):
        with pytest.raises(ValueError, match="dependent"):
            _d400(claimed_as_dependent=True)

    def test_other_state_credit_shape_is_checked(self):
        with pytest.raises(ValueError):
            _d400(other_state_credit={"states": []})

    @pytest.mark.parametrize("name", [
        "use_tax", "withholding", "spouse_withholding", "estimated_payments",
        "extension_payment", "penalties", "interest", "underpayment_interest",
        "applied_to_next_year", "wildlife_fund", "education_fund",
        "cancer_program"])
    @pytest.mark.parametrize("bad", _BAD_NUMBERS)
    def test_validators(self, name, bad):
        with pytest.raises(ValueError):
            _d400(**{name: bad})

    @pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "90000"])
    def test_federal_agi_validated(self, bad):
        with pytest.raises(ValueError):
            _d400(federal_agi=bad)

    def test_every_amount_is_finite_and_two_place(self):
        r = _d400()
        for k, v in r.items():
            if isinstance(v, float):
                assert math.isfinite(v) and round(v, 2) == v, k


class TestTheResultSectionHoldsEveryKey:
    """Every key the engine emits has a home in `north_carolina_d400` — a
    renamed line otherwise shows up on a return rather than in a test."""

    def _section(self):
        return json.loads((REPO / "agents/templates/result_template.json")
                          .read_text())["north_carolina_d400"]

    def test_the_line_18_note_describes_an_input_or_certification(self):
        note = self._section()["_note_line_18"]
        assert "NOT COMPUTED" not in note
        assert "INPUT" in note and "CERTIFICATION" in note
        assert "use_tax_none_due_circle" in note

    def test_top_level_and_schedules(self):
        section = self._section()
        r = _d400(itemized=dict(_ANSWERS, medical=1))
        assert set(r) - set(section) == set()
        for block in ("schedule_s", "schedule_a"):
            assert set(r[block]) - set(section[block]) == set(), block
