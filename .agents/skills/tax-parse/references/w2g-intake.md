# W-2G source extraction

Read the calendar-year header and revision. Reviewed pairs: TY2025 `12/2023`
(the prior continuous-use form), TY2026 `01/2026`. Do not infer tax year from
the date won: an annuity payment can relate to an older winning event.

Capture boxes1/4/7 (reported winnings / federal withholding / identical wagers),
2 (date won), 3 (wager type), 13 (state), 14/15 (state winnings/withholding),
16/17 (local winnings/withholding), and 18 (locality). Use 5/6/8/10 references
only when needed to reconcile duplicate or identical wagers. Box9 is SSN last
four only; never copy full TINs or box11/12 identification numbers.

Use `classify_w2g_income` from `agents.lib.tax_computations` through the launcher:

| Source | Canonical category / treatment |
|---|---|
| Box1 | `gambling_winnings`, reported source gross; the reviewed income split below determines actual income |
| Box4 | `federal_withholding_w2g` → actual Form1040 line25c, not 1099 line25b |
| Reviewed business contribution | `self_employment_gross` → Schedule C gross receipts; NET business income belongs in remaining Schedule1 income |
| Box15 / state13 | Existing CA/NY/NC or other-state withholding route |
| Box17 / locality18 | Existing NYC/Yonkers or advisory local withholding route |

Preserve `w2g_income` on the per-document record. Required fields:

- `tax_year`, `form_revision`, `reviewed: true` and `ownership_confirmed: true`.
  Resolve shared winners via Form5754 and payer-issued allocated/corrected
  W-2Gs before claiming these amounts. Do not claim another person's share.
- `box_1`, `box_4`, `box_7`: actual source amounts, including zero. Do not
  subtract withholding or annual gambling losses from reported winnings.
- `identical_wagers_reviewed`: explicit boolean. Positive box7 requires true
  after reconciling other W-2Gs and books. Do not blindly add or ignore box7.
- `schedule_1_income`, `schedule_c_gross`: explicit source contributions,
  including zero. Review casual versus trade/business status; combined physical
  statements may include both. A business contribution is receipts, not profit.
- `income_method: reported`: the two income contributions sum to box1;
  `worksheet_confirmed: false`, `worksheet_id: null`.
- Alternatively `income_method: reviewed_worksheet`: a separately reviewed
  source/session worksheet supplies the contributions, with
  `worksheet_confirmed: true` and a short opaque alphabetic `worksheet_id`
  (letters, underscores or hyphens; never identity numbers). Allocate its
  contribution across records once. The return discloses this manual method.
  Settle ownership first; do not use an income adjustment to claim someone
  else's box4 withholding. Do not net annual casual losses against winnings.
- `other_business_gross`: explicit reviewed business receipts from other
  subforms in this physical document, including zero. Shared categories are
  whole-document totals; reconcile helper outputs rather than adding the same
  business receipts twice. A MISC subform's other withholding includes only
  line25b 1099 amounts, never this W-2G box4.

Positive source winnings, income contributions or withholding cannot receive an
informational skip. Replace corrected/duplicate forms rather than summing them.
Re-read legacy state-only W-2G records: state withholding does not establish that
the federal boxes were zero. Set `has_w2g` when a W-2G is present and
`has_gambling_income` when actual winnings are present; record both answers.

The checked Schedule1 workpaper adds only the reviewed casual contribution to
MISC and remaining income. A new `w2g_excluded_from_remaining: true` answer is
required; an older MISC-only confirmation may already include gambling. Keep
reviewed business NET income in remaining income. Include other actual gross
winnings only when absent from all mapped income contributions; do not add
amounts the source worksheet already allocated. Wagering deductions remain manual.

For box4, confirm `other_forms_withholding.remaining_withholding` separately:
other qualifying Form1040 line25c amounts, excluding all W-2Gs and line25a/b
amounts. Preserve actual Form8959 line24 withholding, not its net overpayment.
`compute_other_forms_withholding` supplies actual line25c before payment totals.
Never reuse an old total that may already contain W-2G credits. The builder and
tie-out check both source workpapers and actual income/withholding settlements.

Sources: [prior W-2G](https://www.irs.gov/pub/irs-prior/fw2g--2023.pdf),
[current W-2G/instructions](https://www.irs.gov/instructions/iw2g), and
[TY2025 Form1040 instructions](https://www.irs.gov/pub/irs-prior/i1040gi--2025.pdf).
