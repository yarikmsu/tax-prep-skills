# California Form 540 — maintained contract

## Scope and reviewed sources

Full-year resident Form 540, **TY2025 only** for the checked builder. A new
form year must be reviewed before adding it to the engine's reviewed-year set.
Form 540NR, RDP pro-forma federal returns and the situations named by the CA
capability rules retain their gates. Acknowledging a gate permits a separately
worked component; it does not turn that worksheet into an engine computation.
`ca_filing_status` must be confirmed and match the federal return; a differing
status refuses because the checked builder cannot reuse that federal AGI.

Primary sources, read live on 2026-10-19:
[Form 540](https://www.ftb.ca.gov/forms/2025/2025-540.pdf),
[instructions](https://www.ftb.ca.gov/forms/2025/2025-540-instructions.html),
[Schedule P](https://www.ftb.ca.gov/forms/2025/2025-540-p.pdf), and
[Schedule P instructions](https://www.ftb.ca.gov/forms/2025/2025-540-p-instructions.html).

## Questionnaire (state-module playbook step 0)

| # | Question | California answer |
|---|---|---|
| 1 | Starting point | Federal AGI, Form 540 line 13; Schedule CA subtractions and additions produce line 17. |
| 2 | Modifications | Schedule CA has its own conformity rules. HSA contributions/earnings, state refunds, Social Security, retirement/basis and municipal-interest differences must be worked separately; the builder takes the reviewed Schedule CA totals. AA68 gates uncomputed cases; AA71 owns additional automatic adjustments. |
| 3 | Credits | Personal, blind, senior and dependent exemptions are credits. The line-32 federal-AGI limitation applies separately per eligible head. Other credits are separately worked allowed amounts, subject to their own worksheets and existing gates. |
| 4 | Local income taxes | No CA local income-tax return is computed or required by this module. |
| 5 | Surtax | Form 540 line 62 is Behavioral Health Services Tax in TY2025. The existing statutory-fixed `mental_health_surcharge` cache block retains its stable key. |
| 6 | Estimates | Weighted installments and high-income safe-harbor rules remain in the planning engine; AA70 owns recording line-72 payments. Line-113 screening is advisory, not a computed penalty. |
| 7 | Payroll items | SDI has no wage ceiling from TY2024. Employer errors are recovered from the employer. TY2025 line 74 is the refundable Motion Picture credit, never excess SDI. |
| 8 | Withholding form | DE 4 is the payroll analogue; actual W-2/1099/W-2G and 592-B/593 withholding is routed by AA60. |
| 9 | Extension | Automatic filing extension, payment via FTB 3519; a filing extension does not defer payment. |
| 10 | PTET | FTB 3804-CR is a nonrefundable credit, gated pending its own computation. It is never a payment. |
| 11 | Community property | CA is already in the global community-property/MFS gate. |
| 12 | Itemized deduction | Schedule CA Part II, with its own mortgage, charitable, miscellaneous and high-AGI rules. The builder takes the reviewed deduction and method, respecting separate-filer/dependent worksheets. |
| 13 | Table versus rates | Published Tax Table at/below its fetched ceiling, rate schedule above. No inferred midpoint replaces a published row. |

The answers above refer to the named sections/lines of the TY2025 instructions;
they establish form structure, not default annual amounts.

## Law and result contracts

Every CA return requires `state.tax_table` (`max_taxable_income`, published
`rows`), `state.amt.exemption` and `state.amt.phaseout_start`, alongside the
existing standard deduction, exemptions, exemption phaseout and rate schedule.
All annual figures are fetched for the covered year and source-pinned.
`state.underpayment_screen` holds per-status `minimum_balance` and the
`tax_fraction` for Form 540 line 113's two screens. Missing or malformed
figures refuse, including on a zero-income return.

`california_540` keeps its stable `ca_*` fields and adds `line_<number>` fields
for the reviewed form. `computed_tax_year` records that reviewed year; the
positive fetched `ca_standard_deduction` preserves the existing registry's
zero-income computation evidence and compatibility with prior returns. The builder
also returns the Schedule P and exemption workpapers, and stores the screened
line-113 note; that note must reach `meta.limitations`.
When `balance_due_summary` is present, its federal and California settlements
are signed (refund positive, amount owed negative); `net_refund` is their sum.
Those summary fields are derived and checked too. Filing and extension
payment guidance reads the checked line-114 `ca_amount_owed`.

The builder takes separately worked Schedule CA totals, elected deduction,
eligible exemption counts, allowed nonrefundable credits, other taxes, use tax,
ISR penalty and refundable credits. Each input is finite and explicit. It
derives AGI, taxable income, tax, credit floors, every settlement line and
stable aliases. AA70's payment/forward-election fields remain gated until its
implementation; the pure form function can accept already-worked components.

## Schedule P

Every return runs Schedule P. The standard deduction addback is derived when
the standard method is used. The signed `ca_preference_adjustments` input is
the reviewed net of the other Part I items: medical, property taxes, mortgage
interest, miscellaneous deductions, property-tax refunds, investment interest,
depreciation, gain/loss and basis differences, passive activities, estates and
trusts, other line-13 preferences, NOL addback, AMTI exclusion, itemized
limitation reversal and AMT NOL deduction. ISO spread is separate. Do not put
the standard deduction or ISO spread in this net a second time. An unanswered
worksheet is not zero. Schedule P line 15 uses the *unfloored* line 17 minus
18 when Form 540 line 19 is zero. Line 25 uses Form 540 line 31 before credits.

The MFS Part I line-21 worksheet adds the smaller of one exemption or the
phaseout fraction of AMTI above the exemption-elimination ceiling. Both the
ceiling and cap derive from the fetched exemption/phaseout values. Credit
carryforward attribution remains ISO-only; other deferral items and credit
limits need their separately worked worksheets and disclosures.

## Gates, registrations and checks

The existing `return_builder` CLI writes and `--check`s the CA arithmetic
alongside Form 1040, using its existing receipt input set (profile, ledger,
result and covered-year law). A federal out-of-scope waiver must never waive
the CA check. A selected CA return cannot evade checking by removing its
result section. A mismatch or unfetched law refuses and records a failed
verdict; changed inputs invalidate the receipt as before.

Public form math is re-exported by `tax_computations`; no new state registry
row, withholding category, projection or YoY table is introduced. Existing
planning functions retain their advisory scope. PDF and verification expose
the result's form lines; the filing package uses the checked settlement.

Tests use hand-worked published-form arithmetic plus synthetic law values to
isolate floors, table boundaries, signed preferences, senior/blind counts,
AMT before credits, withholding and settlement. Missing/nonfinite law and
inputs, unreviewed years, receipt invalidation and post-build edits must fail.
Mutation checks remove the new checks to prove their tests detect omissions.

## Limits

This builder derives arithmetic over reviewed components. It does not price
the manually gated credits, Schedule CA exceptions, late-payment interest or
FTB 5805 penalty, and does not file a return. Line 113 is screened from its own
lines 100 and 48 (excluding only `ca_lump_sum_tax`, the Schedule G-1 part of
line 34, not FTB 5870A tax), using fetched thresholds. A possible penalty
is disclosed as uncomputed; the FTB may compute and bill it.
