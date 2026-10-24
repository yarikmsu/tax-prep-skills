#!/usr/bin/env python3
"""The `tax_computations` self-test AGENTS.md §8 names, run as a package.

A package cannot be executed the way a module can, so the `__main__` block that
sat at the foot of the old single file lives here. What it runs is unchanged;
`sh agents/lib/run.sh -m agents.lib.tax_computations` is the invocation, and
§8's command became that in the same change. The old script-path form names a
file that is now a directory.
"""

import json
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.lib.secure_fs import redact  # noqa: E402
from agents.lib.tax_computations import (  # noqa: E402
    check_form_8938_required,
    compute_additional_medicare,
    compute_bracket_tax,
    compute_ca_schedule_p,
    compute_child_tax_credit,
    compute_form_2441,
    compute_form_8606,
    compute_form_8889,
    compute_ftc_limitation,
    compute_late_filing_penalty,
    compute_niit,
    compute_pfic_1291,
    compute_pfic_excess_distribution,
    compute_qbi_deduction,
    compute_sales_tax_deduction,
    compute_scenario,
    compute_se_tax,
    compute_section_988,
    compute_underpayment_interest,
)

# Quick self-test with sample data
print("=== Bracket Tax Test (MFJ 2025 OBBBA) ===")
brackets_2025: list[dict] = [
    {"rate": 0.10, "min": 0, "max": 23850},
    {"rate": 0.12, "min": 23850, "max": 96950},
    {"rate": 0.22, "min": 96950, "max": 206700},
    {"rate": 0.24, "min": 206700, "max": 394600},
    {"rate": 0.32, "min": 394600, "max": 501050},
    {"rate": 0.35, "min": 501050, "max": 751600},
    {"rate": 0.37, "min": 751600, "max": None},
]
tax, detail = compute_bracket_tax(150000, brackets_2025)
print(f"Tax on $150,000: ${tax:,.2f}")
for d in detail:
    print(f"  {d['rate']*100:.0f}%: {d['range']} → ${d['tax']:,.2f}")

print("\n=== Empty Brackets Test ===")
try:
    compute_bracket_tax(100000, [])
except ValueError as e:
    print(f"Caught expected error: {redact(e)}")

print("\n=== SE Tax Test ===")
se = compute_se_tax(-500)
print(json.dumps(se, indent=2))

print("\n=== SE Tax Threshold Test ===")
se2 = compute_se_tax(300)
print(json.dumps(se2, indent=2))

print("\n=== CTC Test ===")
ctc = compute_child_tax_credit(2, 2200, 200000, 400000)
print(json.dumps(ctc, indent=2))

print("\n=== Additional Medicare Test ===")
med = compute_additional_medicare(260000, 0, 250000, 4000)
print(json.dumps(med, indent=2))

print("\n=== NIIT Test (MFJ, MAGI $300K, NII $20K, threshold $250K) ===")
# excess = 300K-250K = 50K; min(20K, 50K) = 20K; 3.8% = $760
print(f"NIIT owed: ${compute_niit(300000, 20000, 250000):,.2f}")
print(f"NIIT below threshold: ${compute_niit(240000, 20000, 250000):,.2f}")
print(f"NIIT no threshold (not modeled): ${compute_niit(300000, 20000, None):,.2f}")

print("\n=== Form 8606 Pro-Rata Test (backdoor Roth trap) ===")
# basis $7K, $50K pre-tax IRA, convert $7K → ratio 7000/57000, ~88% taxable
f8606 = compute_form_8606(0, 7000, 50000, 0, 7000)
print(f"Ratio: {f8606['ratio']}; taxable conversion: ${f8606['taxable_conversion']:,.2f}; "
      f"basis remaining: ${f8606['basis_remaining']:,.2f}; trap: {f8606['pro_rata_triggered']}")
clean = compute_form_8606(0, 7000, 0, 0, 7000)
print(f"Clean backdoor (no pre-tax): taxable conversion ${clean['taxable_conversion']:,.2f}")

print("\n=== QBI Test ===")
qbi = compute_qbi_deduction(-1000, 50)
print(json.dumps(qbi, indent=2))

print("\n=== §988 FX Bond Test (ruble bond, market gain + FX loss) ===")
# 100k FC cost @ 0.013, 110k FC proceeds @ 0.011:
#   exchange (ordinary) = 100000 × (0.011 − 0.013) = −$200
#   market   (capital)  = (110000 − 100000) × 0.011 =  $110
#   total = 1210 − 1300 = −$90 = −200 + 110  ✓
s988 = compute_section_988(100000, 110000, 0.013, 0.011, long_term=True)
print(f"exchange(ordinary)=${s988['exchange_gain_ordinary']}, "
      f"market(capital)=${s988['market_gain_capital']}, total=${s988['total_gain']}")
assert s988["exchange_gain_ordinary"] == -200.0
assert s988["market_gain_capital"] == 110.0
assert s988["total_gain"] == -90.0

print("\n=== PFIC §1291 Test (excess distribution + allocation) ===")
# 125% test: current $10,000 vs prior [2000, 3000, 1000] → avg 2000, 125% = 2500
ed = compute_pfic_excess_distribution(10000, [2000, 3000, 1000])
assert ed["threshold"] == 2500.0 and ed["excess_distribution"] == 7500.0
assert ed["ordinary_now"] == 2500.0
# First year → zero excess, whole distribution ordinary
fy = compute_pfic_excess_distribution(10000, [], is_first_holding_year=True)
assert fy["excess_distribution"] == 0.0 and fy["ordinary_now"] == 10000.0
# Allocation: PFIC from acquisition, held 2022-01-02..2025-12-31 (holding
# period = day AFTER acq, 1460 days), $40k excess. 2025 (current) → ordinary
# (365 days); 2022-2024 → deferred at 37% (364+365+366). No rates → degrades.
r1291 = compute_pfic_1291(40000, "2022-01-01", "2025-12-31", 2025,
                          {2022: 0.37, 2023: 0.37, 2024: 0.37}, underpayment_rates=None)
print(f"ordinary_now=${r1291['ordinary_income_current']}, "
      f"deferred_tax=${r1291['deferred_tax_total']}, complete={r1291['complete']}")
assert r1291["ordinary_income_current"] == round(40000 * 365 / 1460, 2)  # 10000.0
# per-year deferred rounds independently: 3689.86 + 3700.00 + 3710.14
assert r1291["deferred_tax_total"] == 11100.0
assert r1291["complete"] is False  # no §6621 rates supplied → interest incomplete

print("\n=== Form 8938 String Coercion Test ===")
f8938 = check_form_8938_required(
    [{"year_end_value_usd": "$5,000", "max_value_usd": "7500"}],
    "mfj",
)
print(json.dumps(f8938, indent=2))

print("\n=== FTC Limitation Test ===")
ftc = compute_ftc_limitation(300000, 200000, 50000, 10000)
print(f"FTC with foreign > worldwide: ratio capped at 1.0, limitation=${ftc['limitation']:,.2f}")

print("\n=== Scenario Test (MFJ, +$40K Roth conversion as ordinary income) ===")
base_inputs = {"wages": 150000, "qualified_dividends": 5000, "ordinary_dividends": 5000}
base = compute_scenario(base_inputs, {}, brackets=brackets_2025, deduction=30000,
                        cg_zero_limit=96700, cg_fifteen_limit=600050)
scen = compute_scenario(base_inputs, {"ira_distribution": 40000},
                        brackets=brackets_2025, deduction=30000,
                        cg_zero_limit=96700, cg_fifteen_limit=600050)
delta_tax = scen["total_federal_tax"] - base["total_federal_tax"]
print(f"Baseline tax: ${base['total_federal_tax']:,.2f}; +$40K → ${scen['total_federal_tax']:,.2f}")
print(f"Marginal cost of $40K conversion: ${delta_tax:,.2f} "
      f"({delta_tax / 40000:.1%} marginal rate)")

print("\n=== §6651 Late-Filing/Late-Payment Penalty Test ===")
# $10K unpaid, 5 months late (both), filed >60 days late.
# FTF 22.5% + FTP 2.5% = $2,250 + $250 = $2,500 (classic 25% combined).
# The floor is ILLUSTRATIVE and cannot reach the printed figures: $535 is far
# below the $2,250 FTF piece, so the §6651(a) minimum never binds here. That is
# why the value sat at $485 — two cycles stale, and labelled with a year it did
# not belong to — through every run of this self-test: no output could move.
# $535 is Rev. Proc. 2025-32's figure for a return REQUIRED TO BE FILED in 2027.
pen = compute_late_filing_penalty(10000, 5, 5, filed_over_60_days_late=True,
                                  min_penalty_floor=535)
print(f"FTF ${pen['ftf_penalty']:,.2f} + FTP ${pen['ftp_penalty']:,.2f} "
      f"= ${pen['total_penalty']:,.2f} (overlap −${pen['overlap_reduction']:,.2f})")

print("\n=== §6601/§6622 Underpayment Interest Test (M3) ===")
_uprates = [{"effective_from": "2026-04-01", "annual_rate": 0.08},
            {"effective_from": "2026-07-01", "annual_rate": 0.08},
            {"effective_from": "2026-10-01", "annual_rate": 0.08}]
_int = compute_underpayment_interest(10000, "2026-04-15", "2026-10-15", _uprates)
print(f"$10,000 balance, due 2026-04-15, paid 2026-10-15 (8% daily): "
      f"${_int['interest']:,.2f} over {_int['days']} days")
_part = compute_underpayment_interest(
    10000, "2026-04-15", "2026-10-15",
    [{"effective_from": "2026-04-01", "annual_rate": 0.08}])
print(f"Partial (only Q2 cached): ${_part['interest']:,.2f} through "
      f"{_part['covered_through']} (complete={_part['complete']})")

print("\n=== CA Schedule P (540) AMT Test (N1) ===")
_cap = compute_ca_schedule_p(
    ca_taxable_income=300000, ca_regular_tax=25000, filing_status="Single",
    exemption=92749, phaseout_start=347808, iso_bargain_element=200000, tmt_rate=.07, phaseout_rate=.25)
print(f"CA taxable $300K, ISO spread $200K, CA regular tax $25K: "
      f"CA AMT ${_cap['ca_amt']:,.2f}, carryforward "
      f"${_cap['schedule_p_carryforward']:,.2f}")

print("\n=== Form 8889 HSA Return-Side Limit Test (N3a) ===")
_hsa = compute_form_8889("self_only", 6, 4300, 0, 4300, 8550)
print(f"6-month eligibility, contributed full $4,300: limit "
      f"${_hsa['allowed_limit']:,.2f}, EXCESS ${_hsa['excess_contribution']:,.2f}, "
      f"§4973 excise ${_hsa['excise_tax']:,.2f} (a full-year assumption misses it)")

print("\n=== Form 2441 Dependent-Care Credit Test (N3b) ===")
_dcc = compute_form_2441(2, 10000, 150000, 200000, "MFJ",
                         spouse_earned_income=80000)
print(f"MFJ, 2 qualifying persons, $10K expenses, AGI $200K: "
      f"{_dcc['applicable_percentage']:.0%} × $6,000 cap = "
      f"${_dcc['credit']:,.2f} credit")

print("\n=== Sales Tax Deduction Test (FL 6% + 1% county + $450 vehicle tax) ===")
# compute_sales_tax_deduction — FL 6% + 1% county + $450 vehicle tax
_stx = compute_sales_tax_deduction(1000.0, 0.06, 0.01, 450.0)
assert _stx["total"] == 1616.67, _stx
assert _stx["local_addition"] == 166.67, _stx
