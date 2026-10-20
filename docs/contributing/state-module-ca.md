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
| 2 | Modifications | The builder derives common US-obligation/municipal/fund, Social Security, state-refund and UI/eligible PFL adjustments. HSA components and other acknowledged conformity worksheets remain separate inputs; their totals are derived. AA68 gates uncomputed cases. |
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

The builder derives common Schedule CA adjustments and their totals from
reviewed source components, then takes the elected deduction,
eligible exemption counts, allowed nonrefundable credits, other taxes, use tax,
ISR penalty and refundable credits. Each input is finite and explicit. It
derives AGI, taxable income, tax, credit floors, every settlement line and
stable aliases. Line 72 is derived from the profile's reviewed payment records
and opening applied overpayment; line 98 is the confirmed forward election.

## State payments and the forward election

`estimated_payments.state` is the one dated state-payment list. Each record
has exactly `payment_id`, `state`, `tax_year`, `date`, `kind`, `amount` and
`person`. IDs are stable opaque local identifiers, never account numbers.
State is an uppercase USPS state code; tax year identifies the return credited,
not the calendar year of the payment. Kind is `estimate`, `extension` or
`k1_estimate` (only CA estimates allocated on K-1 (541)/(100S), never a PTET
credit or nonconsenting-nonresident tax). Person identifies the credited account: `primary`, `spouse` or
`joint`. Amount is the share CLAIMABLE ON THIS RETURN, after any allocation;
never copy the full joint receipt onto two separate returns. Duplicate IDs,
nonfinite/negative amounts and malformed records refuse. Old untagged records
must be re-read, not guessed into a state or year.

Dates are ISO calendar dates. An explicit null date preserves a known payment
when timing is unavailable (a K-1 allocation can lack installment dates); it
creates a visible timing disclosure and cannot support a priced FTB 5805.
Keep dates even though AA76 owns penalty pricing. Payments allocated to a
different state or tax year remain in the list and receive an advisory; they
never pay this return. A spouse's separate-account payment is not claimable on
a nonjoint return. Joint-account payments on a separate return require the
FTB allocation described in the line-72 instructions; individual-account
payments combined onto a joint return need the required explanation.

`flags.ca_has_nonw2_payments` is an explicit screen for current-year CA line-72
payments, opening applied credit included. A yes, positive source records or
an opening credit requires `estimated_payments.ca_verified: true`: the filer
has reconciled the claimable amounts, tax-year allocation and ownership with
source confirmations/MyFTB. A no must agree with the source amounts. Neither
an unknown flag nor an unverified payment is zero.

`prior_year.ca_estimated_payments_applied` carries the prior Form 540 line 98
and is added once, outside the payment list. It is distinct from the federal
applied credit and from new cash payments. Line 72 sums only current-year CA
estimates, extension payments, K-1 allocations and this opening credit.
`ca_payment_summary` records that derivation; original dated records stay in
the profile and are covered by the validation receipt. Schedule A's separate
cash-year deduction question remains authoritative for federal deductions;
line 72 is not automatically a Schedule A deduction.

`california_540.line_98` remains the single election amount. The explicit
`ca_applies_overpayment_forward` answer must agree with it; the form function
refuses an election above line 97. Record a deliberate zero and disclose the
binding nature of the election before filing. Rollover carries line 98 to the
CA opening-credit field only for the immediately following year, preserving
worked zero and refusing malformed/negative source amounts. A changed carried
amount resets `ca_verified` so a previous confirmation cannot authorize a new
credit; status changes involving a joint source require allocation review.
An affirmative archived election with a missing amount refuses rather than
silently skipping the credit. An archive with no line 98 and no election (it
predates both) keeps a setup-typed opening credit for the builder's
`ca_verified` reconciliation instead of refusing the whole rollover. Source: TY2025 Form 540 instructions, lines 72,
98 and 99, linked above. No new tax-law rate or threshold is introduced.
The [TY2024 Form 540](https://www.ftb.ca.gov/forms/2024/2024-540.pdf), line 98,
also confirms the opening credit for TY2025. Reviewing that carry line does
not extend the builder's reviewed form years.

Line 18 is the larger of itemized and standard deductions, so an itemized
amount below the standard deduction refuses except for a separate filer
(bound by a spouse who itemizes) or a filer someone else can claim. The
dependent worksheet is for a claimable filer only, never above the standard
deduction (its line 5 takes the smaller), and the standard method is not.
Claimability is read from line 7's box-6 count. `flags.ca_has_blind_exemption`
must agree with the eligible blind filers in `ca_exemptions`.

## Common Schedule CA income adjustments (AA71)

Reviewed against the live [TY2025 Schedule CA instructions](https://www.ftb.ca.gov/forms/2025/2025-540-ca-instructions.html),
[TY2025 Publication 1001, page 8](https://www.ftb.ca.gov/forms/2025/2025-1001-publication.pdf)
and [R&TC 17145's enacted text](https://www.leginfo.ca.gov/pub/11-12/bill/asm/ab_1401-1450/ab_1423_bill_20111006_chaptered.pdf).
Direct US-obligation interest and eligible fund distributions go to Part I,
Section A line 2, column B. Nonexempt municipal interest and exempt-interest
dividends go to line 2, column C. Section A line 6 subtracts federally taxable
Social Security; Section B line 1 subtracts the federally included state refund,
never either document's gross amount. The existing federal refund tax-benefit
worksheet remains required. AA65's source-reviewed government-benefits workpaper
also subtracts federally included net UI and eligible governmental PFL. Its
`unemployment_subtraction` and `paid_family_leave_subtraction` feed Section B
line 7 column B or the Other Income subtraction according to the family's
reviewed reporting route. CA EDD PFL is exempt; other-government programs need
an explicit confirmed CA decision. Unknown treatment refuses, and gross
benefits before federal UI reductions are never subtracted. See
[the source/revision contract](government-benefits.md). UI/PFL alone no longer
sets the generic other-adjustment flag; re-answer older flags set only by
these benefits and remove duplicate amounts from manual other subtractions.

Keep `taxable_interest` inclusive of 1099-INT boxes 1 and 3, and
`tax_exempt_interest` inclusive of INT box 8 and DIV box 12. Supporting ledger
categories `us_obligation_interest`, `municipal_interest`,
`exempt_interest_dividends` and `fund_ordinary_dividends` are components of
those existing totals, not additional federal income. Capture explicit zero
components too, so an old inclusive total cannot masquerade as a reviewed split.

Each investment record carries `ca_investments` with `tax_year`, `reviewed`,
`municipal_interest` and `funds`. Municipal rows retain `issuer_state` and
`amount`. CA obligations are exempt; other US states' obligations are taxable.
DC/territory rows also require an explicitly confirmed `ca_exempt` decision
from their source worksheet: special issuer rules cannot be inferred from a
broker's address. Fund rows retain opaque `fund_id`, `ordinary_dividends`,
`exempt_interest_dividends`, source-confirmed `is_us_ric`,
`us_obligation_fraction`, `ca_exempt_fraction`
and `asset_test`. Fractions describe income allocation, not asset qualification.
Per-record components must reconcile to the inclusive federal categories;
duplicate fund IDs, missing details, unknown years and nonfinite values refuse.
`parse_records` preserves this metadata when deriving the ledger.

Fetch `state.schedule_ca.fund_asset_minimum` for the covered year. For the
ordinary asset-test route, every quarter-end must meet that threshold using
assets whose interest would be exempt in an individual's hands (US and CA
obligations included). `asset_test.quarterly_fractions` holds four actual
quarter-end fractions. Alternatively, an explicit annual provider certification
uses `provider_qualified` plus the certification's `threshold`, which must match
the fetched rule. This is source evidence, not permission to infer qualification
from an income percentage. Review any qualified-fund-of-funds exception under
17145(d) separately before recording the provider's certification. Below the
ordinary threshold, no US-obligation share is subtracted and the federally
exempt dividend is added in full. At the threshold, subtract the ordinary
dividend's eligible US share and add only the nonexempt share of DIV box 12.
For a confirmed non-RIC, record `is_us_ric: false` and `asset_test: null`;
no passthrough exemption is available. Unknown RIC status refuses. The
workpaper preserves the qualification route, and special-issuer worksheets or
provider certifications also produce a checked disclosure on the return.

The builder replaces `schedule_ca_adjustments`' derived common fields and
totals. The employer and earnings HSA components remain separately worked
inputs, the employer one held to Form 8889 line 9; the federal HSA deduction
(Form 8889 line 13) is derived as `hsa_deduction_addback` (Section C line 13,
column B), and HSA earnings join line 2 column C;
`other_additions`/`other_subtractions` hold other acknowledged worksheets and
require `other_adjustments_confirmed` for a positive amount or a positive
other-adjustment screen. Never migrate an old aggregate total by subtraction.
The two investment screens must be explicitly answered and agree with the
source components. Missing facts cannot be waived by obsolete acknowledgements.
The resulting workpaper, ledger and law inputs participate in the existing
receipt; the standalone tie-out also checks these derived adjustments.

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
Schedule P line 22 limits the exemption of a child (under 19, or a full-time
student under 24, not filing jointly) to earned income plus a yearly base;
the builder applies the full exemption, so a claimable primary filer must
answer `ca_exemptions.primary_schedule_p_child`, and a yes refuses.

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
