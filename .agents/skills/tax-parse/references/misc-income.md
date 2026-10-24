# Form 1099-MISC — reviewed source boxes

Read the covered year and source labels. Reviewed revisions: TY2025 `04/2025`,
TY2026 `12/2026`. The current December form is not for prior years. Read every
box and identify the recipient/payment before classifying income.

| Box | Route for an individual |
|---|---|
| 3 | Generally Schedule1 other income; source-confirmed business income may go to Schedule C. Split mixed payments explicitly. Schedule F, exclusions and unknown treatment must be resolved, never guessed |
| 4 | `federal_withholding_1099`, never receipts or an income reduction |
| 1 / 2 / 9 / 10 / 15 | Rents, royalties, crop insurance, attorney gross proceeds and nonqualified deferred compensation: no route here. A positive amount refuses; prepare that income outside this toolkit |
| 5 / 6 / 11 | `self_employment_gross` (fishing boat proceeds / medical-service receipts / fish purchased for resale) |
| 7 | Boolean direct-sales notice. No dollar amount. Obtain actual resale books separately |
| 8 | `miscellaneous_other_income`, ordinary Schedule1 other income; never qualified dividends or exempt interest |
| 16 / 17 / 18 | State tax withheld / state and payer ID / state income. Route withholding by the state code using the withholding reference; neither ID nor state income is tax paid |

Preserve `misc_income` on each per-document record. Call
`classify_misc_income` through `agents.lib.tax_computations` to get its canonical
categories, adding other independently reviewed categories. Its withholding
already includes the other-subform amount; do not add that withholding again. The
record/ledger validator checks the same mapping. Required fields:

- `tax_year`, `form_revision`, `reviewed`: true only after source classification.
- `box_3`, `box_3_business`, `box_3_other`, `box_3_unresolved`: finite gross
  amounts, including zeros. The three components reconcile to box 3. A positive
  unresolved component blocks parsing until its treatment is resolved.
- `box_1`, `box_2`, `box_4`, `box_5`, `box_6`, `box_8`, `box_9`, `box_10`,
  `box_11`, `box_15`: actual source amounts, including zero.
- `box_7`: actual boolean checked/unchecked, never a numeric threshold or "X".
- `other_business_gross`: separately reviewed business receipts from other boxes
  or subforms in this physical document, including zero; never withholding.
- `other_federal_withholding`: line25b 1099 withholding from other subforms in a
  combined physical document, including zero. Add it to box4 withholding once.
  Exclude W-2G and other line25c amounts; those use their own source workpaper.

The mapper emits positive monetary categories, omitting zero categories. A
checkbox-only notice with zero monetary boxes can carry an informational skip
and its reviewed inventory. A positive payment or withholding cannot be skipped
with this inventory. Re-read older MISC records before deriving a ledger;
withholding alone does not prove income boxes were empty.

Record the separately confirmed business judgment in `answers.md`. Set
`has_1099_misc` when a form or notice is present. Nonbusiness MISC feeds the
checked `schedule_1_other_income` workpaper; its remaining-income input excludes
tracked MISC and UI/PFL, includes signed business NET income and other sources,
and must be confirmed even when zero. Never reuse a prior line8 aggregate.
With UI/PFL, this total becomes the benefits workpaper's non-benefit input;
without benefits it supplies Form1040 line8 directly. Compute before dependent
Social Security/AGI worksheets. Rebuilds and both gates check the settlement.

For box8, also retain its source amount for the reviewed Form8960 NII input;
the [TY2025 instructions, line7](https://www.irs.gov/instructions/i8960)
include substitute interest/dividends. It is already ordinary Schedule1 income;
including it in NII does not add it to Form1040 income again. Review the covered
year's worksheet and allowable deductions; other box3 income is not automatically
NII, and active business income/UI/PFL must not be swept into NII with it.

Box 12's section 409A deferrals are informational; box 15 is the income.
Never force an unrouted box into the mapped ones, and do not repeat box 11
in `other_business_gross`. Capture TY2026 13a/13b/14 tips,
occupation and overtime labels for the separate AA64 qualification review.

Sources: [TY2025 recipient instructions](https://www.irs.gov/pub/irs-prior/f1099msc--2025.pdf),
[TY2025 payer instructions](https://www.irs.gov/pub/irs-prior/i1099mec--2025.pdf),
[current form](https://www.irs.gov/pub/irs-pdf/f1099msc.pdf) and
[current instructions](https://www.irs.gov/instructions/i1099mec).
