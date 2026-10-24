# W-2G intake and other-form federal withholding

AA66 reviews the [December 2023 W-2G](https://www.irs.gov/pub/irs-prior/fw2g--2023.pdf)
used before the new revision, [January 2021 continuous-use instructions](https://www.irs.gov/pub/irs-prior/iw2g--2021.pdf),
[January 2026 W-2G](https://www.irs.gov/pub/irs-pdf/fw2g.pdf), and its
[instructions](https://www.irs.gov/instructions/iw2g), read live 2026-10-20.
The [IRS prior-products index](https://www.irs.gov/prior-year-forms-and-instructions?find=w-2g&items_per_page=200)
has no separate 2025 form: the reviewed covered-year/revision pairs are
2025/`12/2023` and 2026/`01/2026`. Reporting thresholds and gambling deductions
are not computed by this source mapper; no rate or threshold is introduced.

The [TY2025 Form 1040 instructions](https://www.irs.gov/pub/irs-prior/i1040gi--2025.pdf)
put nonbusiness gambling winnings on Schedule1 line8b and W-2G box4 withholding
on Form1040 line25c, not the 1099 line25b. Line25c also includes actual Form8959
line24 Additional Medicare withholding, K-1 withholding and other qualifying
forms. Preserve those components. The net `over_withholding_credit` from a
Medicare comparison is not a substitute for the actual line24 amount.

## Reviewed source inventory

Each W-2G carries `w2g_income` on its per-document record, including state-only
legacy records whose federal boxes were never inventoried. Preserve covered
year/revision, boxes1/4/7, actual ownership, income route and review decisions.
Classify the source using `classify_w2g_income`; the ledger validator uses the
same rules. Never record a full TIN or the identification numbers in boxes11/12.

`gambling_winnings` records box1 as a gross SOURCE amount.
`federal_withholding_w2g` records box4, separate from W-2 and 1099 withholding.
State/local withholding remains box15/17, using state13 and locality18;
boxes14/16 are winnings, not tax paid. Corrected and duplicate forms must not
be counted twice. Resolve Form5754 ownership with the payer's allocated W-2Gs;
never claim another winner's share or withholding on this return.

Income treatment is an explicit split between `schedule_1_income` and
`schedule_c_gross`, including zero components, after reviewing whether gambling
is a trade or business. A combined physical statement can contain both routes. The [TY2025 Publication17](https://www.irs.gov/publications/p17)
and [Publication525](https://www.irs.gov/publications/p525) preserve the
business route. Its source contribution joins `self_employment_gross`; the
separately reviewed Schedule C NET result belongs in remaining Schedule1 income.
This mapper does not compute wagering losses, expense limits or professional
qualification. Keep the gambling MANUAL capability disclosure.

For `income_method: reported`, the income components sum to box1. A separately
reviewed source worksheet may instead determine the income contribution:
`income_method: reviewed_worksheet`, an opaque `worksheet_id` and
`worksheet_confirmed: true`. For Schedule C this means the contribution to
business receipts, not net profit after expenses. For Schedule1 this permits
reviewed session/source adjustments after ownership is settled without forcing a raw information
return amount into taxable income. Do not net annual gambling losses against
casual winnings. The derived workpaper and return disclose a worksheet method.

Box7 is additional identical-wager information. Reconcile it across the forms
and annual records; never blindly add it to every box1, ignore an unreported
winning, or deduct the same wager twice. A positive box7 requires explicit
`identical_wagers_reviewed`. Include additional actual winning income not represented
by any mapped income contribution in the reviewed remaining-income input, without netting losses.
No payer reporting threshold limits what the taxpayer includes in income.

## Checked settlements

The existing `schedule_1_other_income` workpaper keeps MISC income and adds
reported W-2G source winnings, the reviewed casual-income contribution and
professional source receipts. Its total adds ONLY casual income, MISC other
income and signed remaining income. Remaining income excludes those separately
mapped casual amounts and UI/PFL; it includes actual Schedule C net results and
other sources. Adding W-2Gs requires a new explicit `w2g_excluded_from_remaining`
answer, even when an older remaining-income answer was confirmed. Never guess
whether an old aggregate already contains winnings. UI/PFL composition stays
unchanged: the non-benefit total supplies its other-income input.

`other_forms_withholding` derives W-2G withholding plus separately confirmed
`remaining_withholding`, which excludes W-2Gs and all line25a/25b amounts.
It settles actual line25c before line25d, payments and refund/balance arithmetic.
A positive W-2G credit cannot reuse an old aggregate by default. Source-proven
zero W-2G withholding leaves the existing manual component unchanged unless
the new worksheet is explicitly adopted.

The source workpapers, actual Form1040 line8 and line25c are checked by both
builder and tie-out before any federal scope waiver. Rebuilds preserve other
components and are idempotent; source changes invalidate the validation receipt.
The base Form1040 derivation roster remains stable, as for AA65/AA67's extended
line8 check. Compute income before Social Security and AGI/MAGI-dependent
worksheets; refresh affected worksheets after source changes.
