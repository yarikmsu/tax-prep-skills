# Form 1099-MISC — source classification and Schedule 1

AA67 reviews [April 2025 Form 1099-MISC](https://www.irs.gov/pub/irs-prior/f1099msc--2025.pdf),
its [instructions](https://www.irs.gov/pub/irs-prior/i1099mec--2025.pdf), and the
[December 2026 form](https://www.irs.gov/pub/irs-pdf/f1099msc.pdf) and
[instructions](https://www.irs.gov/instructions/i1099mec), read live 2026-10-20.
The December revision first reports TY2026; never use it to interpret TY2025.
The source's covered year and revision must be reviewed together.

For an individual, box 3 generally reports Schedule 1 other income; a supported
trade/business payment instead enters Schedule C. Schedule F and unresolved
or potentially excluded payments require separate source resolution, never an
automatic Schedule C classification. Boxes 5 and 6 report Schedule C receipts.
Box 4 is federal withholding, never income. Box 7 records the payer's direct
sales TO the recipient; it is a boolean, never a dollar amount or the recipient's
resale revenue. Ask for the actual resale books separately. Withholding does
not reduce gross receipts. State withholding stays in box 16, read with box 17's
state code; box 18 is state income and the state ID is not tax paid.

Every 1099-MISC preserves a reviewed `misc_income` inventory for boxes 3–8,
including explicit zeros and a real boolean for box 7. Its box-3 split identifies
Schedule C, Schedule 1 other income and unresolved portions, reconciling to the
source box. Unresolved amounts block; Other-business gross and other federal withholding on a combined physical
statement are separate explicit source amounts; both default to no assumed
amount and must be recorded, including zero. Other boxes still require their own
source treatment. A withholding-only legacy record cannot prove the income
boxes were empty. A checkbox-only notice can be informational only when all
these monetary boxes are zero; retain its inventory and request the resale
records. Never add the direct-sales reporting threshold to income.

The canonical `miscellaneous_other_income` category holds nonbusiness
box-3 income plus box-8 substitute payments (ordinary other income, never
qualified dividends or exempt interest). `self_employment_gross` holds only reviewed business box 3 plus
boxes 5 and 6 (and its existing NEC/business-K sources). Federal withholding
uses `federal_withholding_1099`. Source classification remains an explicit
judgment; a broad range of numbered boxes must never be summed as receipts.

`schedule_1_other_income` derives the non-benefit Schedule 1 component from
tracked MISC income plus signed `remaining_income`. The latter EXCLUDES both
this tracked MISC income and the separate UI/family benefits; it includes
Schedule C net profit/loss, taxable refunds and all remaining applicable
sources. Require `remaining_income_confirmed`, including a deliberate zero.
Do not reuse an old line-8 aggregate or a government-workpaper input that may
already contain MISC income. The explicit new answer resolves that ambiguity.

When this workpaper applies, it supplies `other_income` to the existing
government-benefits computation. Without government benefits, its total is
actual Form 1040 line 8. With benefits, their taxable amounts are added exactly
once by the benefits workpaper. Both workpapers and the actual settlement are
recomputed by the builder and tie-out before any federal scope waiver. Compute
this income before Social Security and AGI/MAGI-dependent worksheets; later
source changes require those worksheets to be refreshed. No new annual tax-law
rate or threshold is introduced. The checked CA form-year limit is unchanged.

Box8 also enters the reviewed Form8960 net-investment-income input when
applicable, without duplicating its ordinary income. The [TY2025 Form8960
instructions, line7](https://www.irs.gov/instructions/i8960) identify substitute
interest/dividend payments. The benefits/MISC assembly does not price Form8960;
its source worksheet remains required at the fetched filing-status threshold.

Other boxes retain their own source routes: rents/royalties (1/2), farming (9), attorney gross proceeds (10), fish resale (11),
deferred compensation (12/15), and TY2026 tips/occupation/overtime (13a/13b/14).
Do not treat substitute payments as qualified dividends or exempt interest, or
attorney gross proceeds as the attorney's fee. AA64 owns the new TY2026
qualification-box review; this inventory does not assert those deductions.
