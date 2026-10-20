# Form 1099-G benefits and Schedule 1

AA65 preserves unemployment and governmental paid family leave as separate
source categories. The gross amounts tie to a derived benefits workpaper;
their taxable amounts enter Form 1040 line 8 exactly once. The remaining
Schedule 1 income is a separate signed, confirmed input. Never infer it by
subtracting new benefits from an older aggregate: that aggregate may already
contain all, some or none of the benefits.

## Reviewed sources and versions

[March 2024 continuous-use Form 1099-G](https://www.irs.gov/pub/irs-prior/f1099g--2024.pdf)
and [instructions](https://www.irs.gov/pub/irs-prior/i1099g--2024.pdf) provide
the form used for TY2025: box 1 includes unemployment and legacy PFL
reporting; state information is in boxes 10a, 10b and 11.
[December 2026 instructions](https://www.irs.gov/instructions/i1099g) and
[form](https://www.irs.gov/pub/irs-pdf/f1099g.pdf) explicitly apply to TY2026:
box 1 is unemployment, new box 10 is governmental family leave, and state
information moves to 11a, 11b and 12. Federal withholding stays in box 4.
Read labels and year, never infer a state from the payer's mailing address.

[TY2025 Form 1040 instructions](https://www.irs.gov/pub/irs-prior/i1040gi--2025.pdf)
place unemployment on Schedule 1 line 7, permit applicable UI contribution
reductions and same-year repayments, and end the old nonitemizer PFL
contribution netting. [Revenue Ruling 2025-4](https://www.irs.gov/pub/irs-drop/rr-25-04.pdf)
includes governmental family benefits in gross income under section 61;
they are not employment-tax wages. Medical leave has different exclusion
and wage rules and must never be classified as family leave by default.

Preserve a source-reviewed Schedule 1 reporting route for each family program:
legacy line 7 or other income. The 2026 box-10 route is other income. This
is distinct from the gross/taxable classification and the box-number revision.
At filing, confirm the exact printed Other Income line on the covered year's
published Schedule 1; the currently published Schedule 1 is TY2025. No
unpublished annual line number is invented by this workpaper.

## Record and computation contracts

Every parsed 1099-G carries `government_benefits`, including refund-only
forms with explicit zero benefits. Non-G records with benefit categories also
need it. The record has its covered `tax_year`, `form_revision`, actual
`box_1`, `box_10` (null before the new revision), source-reviewed
`unemployment_repaid` and `unemployment_contribution_reduction`, `reviewed`
and `family_programs`. Review reductions against the annual worksheet and
deduction election; do not reduce an amount already reported net. Prior-year
repayments and medical/unknown benefits need separate source resolution.

`unemployment_compensation` and `government_family_leave` are gross canonical
categories. Family rows retain program (`ca_edd_pfl` or `other_government`),
amount and reporting route (`7` or `other`). Other-government rows may also
retain a separately confirmed CA exemption decision; an unknown decision
blocks a selected CA return, not a federal-only return. The metadata must
reconcile to both category totals and the revision's boxes. Unknown or malformed
facts refuse before a ledger is written.

When the [non-benefit workpaper](misc-income.md) applies, its recomputed total (including separately mapped casual W-2G income) supplies
the benefits workpaper's non-benefit input. Do not add MISC a second time or
reuse an older benefits input that already contained it. The source-only
benefits calculation remains unchanged; its caller composes the inputs.

`schedule_1_government_benefits` holds `other_income` and
`other_income_confirmed`, alongside the derived gross, reductions, taxable
amounts and total. Positive source benefits require an explicit non-benefit
income answer, including zero. Compute the benefits total before Social
Security or any AGI/MAGI-dependent worksheet; a later source or non-benefit
component change requires those affected worksheets to be refreshed.
With source-proven zero benefits, an existing
explicit line-8 component can be preserved as the initial non-benefit input;
it is never reused automatically once positive benefits appear. Returns with
neither benefits records nor this worksheet retain their existing Schedule 1
component path. The existing Form 1040 slice stays checked; the additional
workpaper and its line-8 settlement are checked on the same receipt before any
federal out-of-scope waiver.

For a TY2025 CA return, Schedule CA subtracts only federally included net UI
and exempt PFL. CA EDD PFL is identified from the program, not withholding
state boxes. Other programs require a confirmed source worksheet for CA
treatment. UI/PFL no longer sets the generic other-adjustment screen by itself.
Uncomputed conformity items and the TY2025-only checked CA form guard remain.
