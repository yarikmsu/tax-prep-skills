# Residential rental return workpapers

Read the maintained [scope and state contract](../../../../docs/contributing/rental-income.md).
Only the reviewed TY2025 `residential_rental_v1` path is computed. Other rentals,
royalties, NY/NC conformity, personal/short-term/self-rental, services,
dispositions, special passive activities, equipment/cost segregation, ADS,
bonus/section179 and method corrections retain the named `rental_income` gate.
A scope acknowledgement is an external-workpaper path, never permission to
guess inputs or use this engine outside its scope.

## Before the first build

Record **[rental_sources_and_scope]**. Set `has_rental_income` even in a year
with no receipts if the activity or suspended losses remain. Reconcile every
property and asset against the prior return; do not drop fully depreciated
assets or a loss balance. Source-state filing obligations remain separate.

`taxpayer_profile.json.rental_return` is an object with:

- `method`: `residential_rental_v1`; `tax_year`: covered year.
- `scope`: every `RENTAL_SCOPE_FACTS` key from
  `agents/lib/tax_computations/federal/rentals.py`, explicitly true only after
  review. Calendar-year cash method, direct U.S. residential ownership,
  ordinary conforming expenses and no unsupported exceptions are prerequisites.
  Basis/at-risk and other loss limits require actual eligibility worksheets.
- `properties`: complete list. Each row has `property_id` (opaque alphabetic
  label), `address` (`street`, `city`, uppercase `state`, `zip`),
  `property_type` (`single_family`/`multi_family`), `days_rented`,
  `personal_use_days` (zero), `new_activity_this_year`, `active_participation`,
  `minimum_ownership_fraction` (including spouse's ownership for the active
  participation test), `source_ids`, `assets`, `basis_and_history_confirmed`.
  Management participation and ownership must meet the covered-year worksheet;
  do not infer them from rent collection or a mailing address.
- Each asset has `asset_id` (opaque alphabetic label), `kind`
  (`building`/`structural_improvement`), ISO `placed_in_service`,
  `federal_method` and `ca_method` (`formula`/`table`), separate
  `federal_basis`/`ca_basis`, `converted_from_personal` and `conversion_fmv`
  (explicit null unless converted). CA fields may be null only without a CA
  return. Exclude land, principal and personal amounts. Conversion uses
  building-only lower adjusted basis/FMV; improvements are separate assets.
  Preserve an established percentage-table method. Do not silently switch it
  to the formula, restart basis or make up missed depreciation.
- `lived_apart_all_year`: explicit boolean for MFS, otherwise null. It means
  apart for the entire covered year, not merely at year end.
- `magi_adjustments`: `reviewed: true`, `included_agi_adjustments`,
  `excluded_agi_adjustments`, `excluded_income_addbacks`. Read Form8582 line6:
  reconcile the first two to Form1040 line10; identify the named exclusions
  and addbacks from the covered-year instructions. Never enter final MAGI or
  infer it by subtracting a rental loss from old AGI. FTB3801 uses federal
  Form8582 MAGI, not CA AGI.

Existing activities require `prior_year.rental_carryforward` for the immediately
preceding year, even with worked zero losses. It contains `tax_year`,
`properties` and `asset_inventory`. Each property balance has `property_id`,
`federal_passive_losses` and `ca_passive_losses` (each split into
`active_origin`/`other_origin`), and `assets` (`asset_id`,
`federal_accumulated`, `ca_accumulated`). Inventory rows have `property_id`
and the same asset facts listed above. CA balances are independent; reconstruct
them if absent. A federal-only path with known opening CA balances refuses:
separate CA closing worksheets are needed before those records can advance.
Do not clear them or copy federal balances to bypass the refusal.
An older external schedule must establish whether a prior
loss originated in an active-participation year. Current management does not
promote an old nonactive loss into the special allowance.

Each property needs complete reviewed books and all overlapping information
returns in `parse_ledger.json`, including a documented zero-income book.
Only books contribute cash receipts/expenses; information returns reconcile
to those books. Resolve duplicate, corrected or disputed forms before running.
Fetch every used `federal.rental` and CA `state.rental` field, including the
published month/age table for an established table user. No prior-year inference.

Confirm `schedule_1_other_income.remaining_income` and
`remaining_income_confirmed`, excluding computed rentals, mapped MISC, casual
W-2G and benefits; set `rentals_excluded_from_remaining: true` only after that
reconciliation. Keep actual business/K-1 NET income and taxable refunds there.
Do not retain an old line8 aggregate beside the new rental workpaper.

## Build, refresh related forms, then check

Run `sh agents/lib/run.sh -m agents.lib.return_builder`. It writes the complete
`schedule_e_rentals` workpaper, derives depreciation and PAL limits separately
for federal/CA, settles line8 exactly once and derives the Form540 difference.
Do not type ScheduleE net, depreciation or PAL result lines. Do not duplicate
the difference in `other_additions` or `other_subtractions`.

Re-read the result and refresh taxable Social Security, deductible IRA/student
loan and other income-dependent adjustments, QBI, Form8960, federal/CA AMT,
credits and every affected worksheet. Use the signed allowed rental
contribution in the reviewed Form8960 worksheet, never gross rents. QBI
eligibility and vintage attributes require their separate worksheet. Federal
and CA AMT depreciation/preferences must be reviewed even when regular-tax
depreciation conforms. Rebuild if any income/adjustment or rental input changes;
repeat until stable. A changed related deduction can change PAL MAGI again.

Record **[rental_related_forms_review]** and set
`rental_return.related_forms_review` to `input_digest` copied from the final
`related_forms_input_digest`, with `qbi_reviewed`, `niit_reviewed`,
`amt_reviewed`, `income_dependent_forms_reviewed` explicitly true after review.
Run the builder again to persist that reviewed witness, then run
`sh agents/lib/run.sh -m agents.lib.return_builder --check` and the normal
tie-out. Both refuse missing/stale workpapers or review, even if federal
arithmetic has a scope waiver. No digest is taxpayer approval to skip a form.

Summary PDFs retain the property/asset/PAL detail and limitations. Transfer to
official ScheduleE, Form4562 and Form8582 (and CA3885A/3801) as required by
covered-year filing instructions; a summary is not an official form or e-file.
The builder's TY2025 residential Form4562 row is **19i**, not an older year's
19h. Rollover preserves separate jurisdiction/origin balances and asset facts;
re-review them before the next year's computation. Scenario, Roth and
projection planners currently refuse rental cases because they do not reprice
these worksheets. Actual-liability withholding true-up remains available.
