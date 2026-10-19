# New York IT-203 and NYC IT-360.1 — TY2025

Use only a validated `taxpayer_profile.state_filings.NY` plan declaring IT203.
No plan means the existing full-year IT-201 rules and scope gates still apply.
The NY filing plan selects NY law independently of the mailing address. Fetch
the complete annual NY block at setup, including City schedules when applicable.
Preparing NY does not prepare another state's return or determine domicile.

The plan requires `tax_year`, `residency: part_year|nonresident`,
`resident_period: [YYYY-MM-DD,YYYY-MM-DD]` (null for nonresidents),
`nyc_resident_period` (dates within NY residence, or null), `resident_states`,
`source_states`, and `other_state_obligations` rows with `state` and confirmed
`return_required`. Confirm `obligation_inventory_complete`, `allocation_reviewed`,
`convenience_rule_reviewed`, `special_accruals_reviewed` as true;
`yonkers_tax_required` and `special_condition_a6` as false. MFJ requires
`joint_residency_identical: true` for both NY and City periods. Keep scope flags
truthful. The gate clears move/source blocks only for a complete inventory whose
other states have no individual income tax and require no return. Other-state
taxable returns, foreign moves and statutory second residency retain their gates.
MFS, differing spouse residence periods, IT-230 special ratios and all IT-203
Yonkers tax require manual preparation. Do not type zeros to bypass them.

Record `profile.ny_it203_inputs` with EVERY keyword of `compute_ny_it203`,
except `plan`, which comes from the validated filing plan. Required zero or
empty answers must be confirmed in `answers.md`. Consult the covered year's
[IT-203-I](https://www.tax.ny.gov/pdf/2025/inc/it203i_2025.pdf) and
[IT-360.1-I](https://www.tax.ny.gov/pdf/2025/inc/it360_1i_2025.pdf).

- `income_rows`: unique activity/document `id`, IT-203 `line` (1–11, 13–16),
  signed `federal`, `resident`, `nonresident_ny`, `nonresident_elsewhere`, and
  `method: actual|ny_workdays|entity_statement|special_accrual`. The last three
  amounts are DISJOINT and sum to the federal amount. Line 12 is informational
  rental income already included in line 11, never a second income row.
  `adjustment_rows` use the same structure with line 18. All-source AGI must
  equal the derived federal return. NY residents' worldwide income during
  residence belongs in `resident`; nonresident NY-source income belongs in
  `nonresident_ny`. Never prorate annual wages by residence calendar days.
  Review employer convenience rules, pensions, intangible income, partnership
  sourcing and special accruals. Signed losses outside NY may produce a ratio
  above one; do not clamp it.
- `modifications`: keys `20`, `21`, `22`, `24`, `25`, `26`, `27`, `28`, `29`,
  each with nonnegative `federal` and `ny_source` (source no greater than total).
  These are reviewed IT-203/IT-225 worksheet amounts, including pension
  exclusions, 529 adjustments and allocated public-retirement additions.
  Confirm `modification_worksheets_verified` and `allocation_complete`.
  Pension line 28 uses ONE annual eligible-person cap, not a cap per period;
  on MFJ verify each spouse's separate eligibility even below the household cap.
- `dependents`, boolean `claimed_as_dependent`, and `itemized`: confirmed null
  for the annual standard deduction or verified `compute_ny_itemized` IT-196
  keyword inputs. The state deduction is annual, never prorated. All fetched
  reduction parameters must be present when using itemized deductions.
- `credits`: required nonnegative worksheet amounts `child_care`,
  `earned_income`, `nonrefundable`, `other_ny_taxes`, `nyc_child_care`,
  `nyc_school_fixed`, `nyc_school_rate`, `nyc_elimination`, `other_refundable`.
  `credit_worksheets_verified: true` confirms eligibility AND the correct
  resident/nonresident share. State childcare/EIC precede the percentage;
  IT-203-ATT nonrefundable credits follow it. Line 49 `other_ny_taxes` is net
  OTHER NEW YORK TAXES from IT-203-ATT line 33. An other-state tax CREDIT belongs
  in the reviewed nonrefundable-credit worksheet, never line 49.
- `payments`: `state_withholding`, `city_withholding`, `yonkers_withholding`,
  `estimates_and_extension`; confirm `payments_verified`. Compare actual
  payer/jurisdiction boxes from W-2 and 1099/W-2G sources with ledger categories
  (IT-203 lines 62, 63 and 64, including 1099-R/W-2G local box 17); another state's withholding
  does not pay NY. Supply confirmed `use_tax`, `voluntary_contributions`,
  `penalty`, `interest`, `applied_to_next_year`, and
  `special_it230_income_percentage: false`.
- `mctmt_zone1`, `mctmt_zone2` are verified NY zone-specific net earnings;
  `mctmt_allocations_verified: true`. City credits cannot offset MCTMT.
- `city_inputs`: null unless NYC residence occurred. Otherwise provide
  `resident_nyagi`, `resident_full_months`, `resident_itemized_deduction`
  (verified IT-360.1 Part 2 amount, or null for the prorated standard deduction),
  `charitable_trust_addback`, `household_and_accumulation_credit`,
  `other_city_taxes`, `ubt_credit`, `worksheets_verified: true`. Line 39 rounds
  each partial month UP only when more than half the month's days were resident.
  The count must agree with actual dates. City income is NOT annualized.
  The City RATE SCHEDULE applies even below the full-year tax-table ceiling.
  Prorate one dependent exemption, round it to whole dollars, then multiply by
  dependent count. Use the same deduction election required by IT-360.1/IT-196;
  do not supply a City itemized amount without the reviewed Part 2 worksheet.

Run after the federal return is complete; replace the selected NY section and
remove the full-year scaffold so PDF/package/YoY cannot sum two NY returns:

```bash
sh agents/lib/run.sh -c '
import json
from agents.lib.workspace import resolve_workspace
from agents.lib.secure_fs import write_secure
from agents.lib.tax_computations import compute_ny_it203, ny_it203_plan
w = resolve_workspace()
p = json.loads((w / "taxpayer_profile.json").read_text())
r = json.loads((w / "result.json").read_text())
law = json.loads((w / ("tax_law_" + str(p["tax_year"]) + ".json")).read_text())
plan = ny_it203_plan(p)
if plan is None: raise ValueError("A reviewed IT203 filing plan is required")
if r["meta"]["tax_year"] != p["tax_year"] or r["meta"]["filing_status"] != p["filing_status"]: raise ValueError("Complete this year/status federal return first")
args = dict(p["ny_it203_inputs"])
args["plan"] = plan
ny = compute_ny_it203(p["tax_year"], p["filing_status"], r["form_1040"]["line_11_agi"], law, **args)
r.pop("new_york_it201", None)
r["new_york_it203"] = ny
r["meta"]["ny_return_type"] = "IT203"
r["meta"]["limitations"] = list(dict.fromkeys(r["meta"].get("limitations", []) + ny["notes"]))
write_secure(w / "result.json", json.dumps(r, indent=2) + "\n")
'
```

Continue builder/tie-out/receipt gates. Raw income allocations tie to federal
AGI before NY rounds each aggregated form box to whole dollars and derives
the source percentage. Attach IT-2/IT-1099-R, source allocation IT-203-B,
IT-225, IT-196 and IT-203-ATT as applicable; NYC requires IT-360.1.
IT-201-V can pay IT-203; a separate IT-370 extends filing, with payment due at
the original deadline (IT-203 line 70). IT-203-X amendments need independent
NY review. Ordinary projection/withholding reports explicitly skip this NY
forecast; use reviewed IT-2105 source allocations and actual NY payment plans.
