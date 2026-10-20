# Government benefits — 1099-G

For every physical 1099-G, read the tax year, revision, payment labels, program
identity and withholding. Use SSN last four only. Keep box 2 refunds separate
from benefits; boxes 5–9 and medical/private benefits are not UI or PFL by
default and need their own reviewed tax treatment. Unknown facts STOP parsing.

| Covered year | Unemployment / family | State / payer state ID / tax withheld |
|---|---|---|
| TY2025, March 2024 continuous-use form | Box 1 contains UI or legacy governmental PFL; split by the source program | 10a / 10b / 11 |
| TY2026, December 2026 form | Box 1 UI; new box 10 governmental family leave | 11a / 11b / 12 |

Federal withholding is box 4 in both versions, routed to
`federal_withholding_1099`. Route state tax withheld by its state code and
label through the existing withholding reference: CA `ca_1099_withholding`,
NY `state_withholding_ny`, NC `state_withholding_nc`, otherwise the explicit
other-state breakdown. Never treat the state ID as withholding, use the box-10
family amount as tax paid, or subtract withholding from gross benefit income.

Set `has_1099g_benefits` when source benefits are present. Record gross amounts
as `unemployment_compensation` and `government_family_leave`, including
explicit zeros for a refund-only form. Preserve this `government_benefits`
metadata on its per-document record:

- `tax_year` and `form_revision`: reviewed combinations are 2025/`03/2024`
  and 2026/`12/2026`. Other years require a source review, never a guessed map.
- `box_1`: actual gross source amount; `box_10`: null in 2025, actual amount
  in 2026. The categories must sum to the revision's source boxes.
- `unemployment_repaid`: only applicable same-year UI repayments still
  INCLUDED in the reported gross, never a repayment already netted out.
- `unemployment_contribution_reduction`: only the reduction permitted by the
  covered-year UI worksheet and deduction election. Resolve it explicitly,
  including zero. Do not copy a PFL contribution reduction here.
- `reviewed`: true only after source program, year, gross receipts and all
  adjustments are resolved. Fraudulent/unreceived benefits need corrected
  source resolution; do not treat them as actual receipts.
- `family_programs`: rows with `program` (`ca_edd_pfl` or `other_government`),
  `amount` and source-reviewed `schedule1_route` (`7` for legacy reporting or
  `other`). In 2026, box-10 family benefits use other income. The rows sum to
  `government_family_leave`. For `other_government`, also include `ca_exempt`
  and `ca_exemption_confirmed`: a reviewed boolean/true or unanswered null/false.
  A selected CA return blocks an unknown exemption rather than taxing it by default.

Capture all other payments/boxes as read and prepare their separate source
worksheets; this benefits computation does not determine their taxability.
Do not double-record a corrected or duplicate form. Supporting statements
inform the paying form's metadata and get an informational skip when they add
no separate reportable payment.

UI is reported on Schedule 1 line 7. For family reporting, preserve the reviewed
source-program route: TY2025 CA EDD PFL commonly uses legacy line 7; new
TY2026 box 10 is other income under Revenue Ruling 2025-4, not section-85 UI.
Confirm the exact printed Other Income line from the covered-year published
Schedule 1 at filing. Both routes enter Form1040 line8, never wages or SE tax.

Sources: [March 2024 form](https://www.irs.gov/pub/irs-prior/f1099g--2024.pdf),
[December 2026 instructions](https://www.irs.gov/instructions/i1099g),
[TY2025 individual instructions](https://www.irs.gov/pub/irs-prior/i1040gi--2025.pdf),
[Revenue Ruling 2025-4](https://www.irs.gov/pub/irs-drop/rr-25-04.pdf), and
[TY2025 Schedule CA](https://www.ftb.ca.gov/forms/2025/2025-540-ca-instructions.html).
The old March 2024 form's nonitemizer PFL contribution wording must not
override the TY2025 individual instructions' changed rule.
