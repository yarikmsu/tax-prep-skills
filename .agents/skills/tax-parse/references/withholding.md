# State and local withholding

- Every `other_state_withholding` record also carries `other_state_withholding_by_state`, for example `{"CO": 100, "NJ": 50}` for a category total of 150. The breakdown must sum to the category, use valid uppercase state codes, and survive into the derived ledger. Resolve an unknown state from the source before continuing; an older record without its breakdown must be re-read. The tie-out fails a breakdown containing CA, NY or NC when that dedicated return is selected or computed, even if its amount was placed in the advisory category. Select the planned return from the profile and declared NY filing plan, not from whether its result section has been populated; dropping that section cannot make its payment external.
- Legacy `unrouted_state_withholding` records still block: re-read their source and route the payment through the supported categories above; never delete a payment to clear the gate. Keep federal Schedule A line 5a's once-per-source treatment.

| Form box | Category |
|---|---|
| W-2 box 17 (CA) | `state_withholding_ca` when this run computes CA; otherwise `other_state_withholding` with state CA |
| W-2 box 17 (NY) | `state_withholding_ny` when this run computes NY; otherwise `other_state_withholding` with state NY |
| W-2 box 17 (NC) | `state_withholding_nc` when this run computes NC; otherwise `other_state_withholding` with state NC |
| Any 1099's state-tax-withheld box, or a W-2G's, read by label and state (1099-R 14, W-2G 15, 1099-B/1099-DA/1099-DIV 16, 1099-INT 17, 1099-NEC 5; confirm the covered revision), when this run computes that state's return | CA: `ca_1099_withholding`; NY: `state_withholding_ny`; NC: `state_withholding_nc`. CA's `state_withholding_ca` remains W-2-only. Otherwise use `other_state_withholding` with the per-state breakdown below. |
| Form 1099-G state tax withheld: TY2025 box 11 (state code 10a); TY2026 box 12 (state code 11a) | Same dedicated CA/NY/NC or other-state routes above. State IDs (10b/11b) are not tax paid. New TY2026 box 10 is family income, never withholding |
| Form 592-B or 593, recipient's actual CA tax withheld (not income, sale price or payer-wide remittance), when this run computes CA's return | `ca_592b_593_withholding` → Form 540 line 73, separate from W-2/1099 line 71. Otherwise `other_state_withholding` with state CA. |
| Any state withholding for a return this run does not compute, including a W-2's box 17 for a generic-estimate state | `other_state_withholding`, with `other_state_withholding_by_state` mapping every state code to its withheld amount. A generic estimate is not a dedicated state return. Never hide a computed CA/NY/NC payment here. |
| W-2 boxes 18-20 or a 1099/W-2G local-tax box, locality NYC, when this run computes NY | `city_withholding_nyc` (box 19 LOCAL tax withheld; box 20 confirms NYC). Separate from state withholding: IT-201 lines 72/73, or selected IT-203 lines 62/63. Never combine them. |
| W-2 box 19 or a 1099/W-2G local-tax box, locality Yonkers, when this run computes NY | `city_withholding_yonkers`: IT-201 line 74 or selected IT-203 line 64. Confirm box 20 locality; do not infer it from employer address. |

### California Forms 592-B and 593 Extraction

Extract the tax year, withholding agent, recipient/ownership and actual California tax withheld by its label, using SSN last four only. Keep Form 592-B's recipient statement distinct from a payer's Form 592 remittance total, and Form 593's withholding distinct from sale proceeds. Confirm which filer owns the payment and whether a corrected statement replaces an original. Form 540 line 73 takes these amounts; line 71 excludes them. If filing status changed after escrow, resolve the FTB's credit-allocation instructions before claiming the amount.

Withholding authorities reviewed for TY2025: [CA Form 540 lines 71 and 73](https://www.ftb.ca.gov/forms/2025/2025-540-instructions.html), [IT-201 lines 72-74](https://www.tax.ny.gov/forms/html-instructions/2025/it/it201i-2025.htm), [IT-203 lines 62-64](https://www.tax.ny.gov/forms/html-instructions/2025/it/it203i-2025.htm), and [NY Tax Law section 673](https://www.nysenate.gov/legislation/laws/TAX/673). Read the covered year's labels before mapping a box; never infer a jurisdiction from an employer or payer address.

## Ledger permissions

The builder restricts `parse_ledger.json` as it writes it: it carries dollar
figures and is gitignored. Use the cross-platform helper, never a bare
permission command. Under a Windows POSIX-compatible shell, `chmod` only
flips the read-only DOS attribute and leaves NTFS access unchanged.
