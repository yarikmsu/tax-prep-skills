# tax-compute Step 10 — North Carolina Form D-400

Full-year resident only. The engine has read the TY2025 D-401; it refuses other
form years until their Schedule S and conformity rules are verified. A fetched
rate for another year is not permission to reuse the TY2025 form.

## Before computing

1. Confirm the cache and profile both name NC and the covered year. The
   capability gate must have explicit answers to all six NC screening flags.
   An unsupported item requires its named manual worksheet and any accepted
   limitation must appear on `meta.limitations`. Never claim the engine
   computed an acknowledged manual line. A dependent filer is refused.
2. Collect NC deductions independently of the federal election. Read raw
   mortgage interest, real-estate taxes, gifts, medical expenses and eligible
   claim-of-right repayments. Do not reuse federal Schedule A amounts.
3. Populate `taxpayer_profile.north_carolina_inputs` with the keyword inputs
   below, using explicit answers from `answers.md` or the interview. Amounts
   absent from source documents are zero only after their absence is confirmed.
   Secure the profile with `sh agents/lib/run.sh -m agents.lib.secure_fs taxpayer_profile.json`.

## Inputs to record

- `schedule_s`: `government_retirement_1099r`,
  `uniformed_services_retirement_1099r`, and the six required amounts
  `exempt_interest` (1040 line 2a), `state_local_refund_in_agi`,
  `us_obligation_interest` (1099-INT box 3), `us_government_fund_dividends`,
  `taxable_social_security` (1040 line 6b) and `railroad_tier2_in_agi`.
  Exempt interest requires `nc_obligation_share`; government-fund dividends
  require `us_government_share`. Obtain the allocation from fund statements.
  A government/military 1099-R requires explicit `bailey_retirement` and/or
  `uniformed_services_retirement` eligibility amounts, bounded by
  `government_retirement_taxable` from those plans alone. No private IRA or
  401(k) income may enter that bound. Missing answers refuse.
- `ctc_qualifying_children`: the count of qualifying children for whom the
  federal section 24 child credit was allowed, from the federal worksheet;
  never the total dependent count or other-dependent-credit count.
- `itemized`: a dict of `compute_nc_itemized` raw keyword inputs, or `null`
  after confirming there are no NC itemized expenses. For mortgage interest,
  answer `mortgage_over_acquisition_limit` and `home_equity_debt` under NC's
  fetched conformity date; for gifts answer `charitable_agi_limit_may_bind`.
  Unknown or true answers refuse: prepare that deduction manually under the
  correct Code. Mortgage-insurance premiums refuse. A claim-of-right amount
  requires `claim_of_right_eligible: true`, certifying no current federal AGI
  deduction or section 1341 credit already covers it. For MFS, record `paid_by`
  for each nonzero item and `spouse_mortgage_and_property_tax`; joint-account
  proration requires manual treatment. See the function's validation messages.
- `spouse_itemizes_nc`: explicit boolean for MFS, `null` for other statuses.
  `claimed_as_dependent` and `eligible_for_federal_standard_deduction`: explicit
  answers; no age/blind NC standard-deduction increment is assumed.
- `other_state_credit`: `null` only after confirming no credit applies;
  otherwise `{"income_all_sources": amount, "other_states": rows}`. Each row
  uses the exact keyword fields in `compute_nc_other_state_credit`'s docstring,
  with the other return's taxable income and net tax paid, never an estimate.
  Other-state filing obligations remain gated; computing the resident credit
  does not prepare a nonresident return.
- `use_tax`: amount, or `use_tax_certified_none: true` to fill the none-due
  circle. Unknown leaves line 18 null and a prominent balance warning.
- `withholding` / `spouse_withholding`: all NC withholding from W-2, W-2G and
  1099 boxes, allocated by person (spouse amount only for MFJ). The ledger category
  `state_withholding_nc` ties their sum to lines 20a/20b. `estimated_payments`,
  `extension_payment`, `penalties`, `interest`, `underpayment_interest`,
  `applied_to_next_year`, `wildlife_fund`, `education_fund`, `cancer_program`:
  confirmed amounts; contributions cannot exceed overpayment.
- `federal_extension_circle` and `out_of_country_circle`: explicit answers.
  Automatic federal extension requires its circle on D-400; otherwise D-410.

## Run and preserve the result

Call `compute_nc_d400` with the cached state and the recorded inputs. The
following launcher call replaces the entire NC scaffold with computed outputs:

```bash
sh agents/lib/run.sh -c '
import json
from agents.lib.workspace import resolve_workspace
from agents.lib.secure_fs import write_secure
from agents.lib.tax_computations import compute_nc_d400
w = resolve_workspace()
p = json.loads((w / "taxpayer_profile.json").read_text())
r = json.loads((w / "result.json").read_text())
law = json.loads((w / ("tax_law_" + str(p["tax_year"]) + ".json")).read_text())
if law["tax_year"] != p["tax_year"] or law["state"]["code"] != p["address"]["state"] or p["address"]["state"] != "NC": raise ValueError("NC cache/profile mismatch")
if r["meta"]["tax_year"] != p["tax_year"] or r["meta"]["filing_status"] != p["filing_status"]: raise ValueError("Complete the federal return for this year/status before NC")
args = p["north_carolina_inputs"]
if not isinstance(args, dict): raise ValueError("Record all required north_carolina_inputs first")
nc = compute_nc_d400(p["tax_year"], p["filing_status"], r["form_1040"]["line_11_agi"], law["state"], **args)
r["north_carolina_d400"] = nc
r["meta"]["limitations"] = list(dict.fromkeys(r["meta"].get("limitations", []) + nc["notes"]))
write_secure(w / "result.json", json.dumps(r, indent=2) + "\n")
'
```

Review other Schedule S ADDITIONS against the year's D-401 and disclose every
uncomputed item. Do not present an unchanged engine result as incorporating
manual additions/deductions. D-422 interest, penalties, NC business/recovery/NOL
and Taxed-PTE items and other credits are outside this computation. The engine
carries cents; transfer to the state's form using D-401 whole-dollar rounding.
Continue all federal return-builder, tie-out and receipt gates before declaring
the return complete. Run `/tax-withholding` for NC-40 targets using this return.
