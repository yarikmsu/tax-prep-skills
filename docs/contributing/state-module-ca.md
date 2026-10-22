# California Form 540 — maintained contract

## Scope and reviewed sources

Full-year resident Form 540, **TY2025 only** for the checked builder. A new
form year must be reviewed before adding it to the engine's reviewed-year set.
A separately declared TY2025 part-year Form540NR follows
[state-module-ca-part-year.md](state-module-ca-part-year.md); it never uses this
resident builder. RDP pro-forma federal returns and the situations named by the
CA capability rules retain their gates. Acknowledging a gate permits a separately
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
| 3 | Credits | Personal, blind, senior and dependent exemptions are credits. The line-32 federal-AGI limitation applies separately per eligible head. FTB3510 PartII and FTB3804-CR owner credits are derived under AA72 below. Other credits are separately worked allowed amounts, subject to their own worksheets and existing gates. |
| 4 | Local income taxes | No CA local income-tax return is computed or required by this module. |
| 5 | Surtax | Form 540 line 62 is Behavioral Health Services Tax in TY2025. The existing statutory-fixed `mental_health_surcharge` cache block retains its stable key. |
| 6 | Estimates | Calendar-year regular-method FTB5805 uses its fetched weighted installments for BOTH the requirement and deemed withholding. Prior/current-year floors precede the current-AGI override; prior-year AGI controls the step-up. AA70's dated records supply actual payments. Annualized, farmer, waiver and special-deadline cases require their own reviewed worksheets. The extension contract below governs line113 pricing. |
| 7 | Payroll items | SDI has no wage ceiling from TY2024. Employer errors are recovered from the employer. TY2025 line 74 is the refundable Motion Picture credit, never excess SDI. |
| 8 | Withholding form | DE 4 is the payroll analogue; actual W-2/1099/W-2G and 592-B/593 withholding is routed by AA60. |
| 9 | Extension | Automatic filing extension, payment via FTB 3519; a filing extension does not defer payment. |
| 10 | PTET | FTB3804-CR is a derived nonrefundable owner credit with vintage carryovers, subject to AA72's scope and eligibility facts below. It is never a payment. |
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
`tax_fraction` for FTB5805's current-year floor and annual-payment share. Missing or malformed
figures refuse, including on a zero-income return.

`california_540` keeps its stable `ca_*` fields and adds `line_<number>` fields
for the reviewed form. `computed_tax_year` records that reviewed year; the
positive fetched `ca_standard_deduction` preserves the existing registry's
zero-income computation evidence and compatibility with prior returns. The builder
also returns the Schedule P, exemption and FTB5805 workpapers, and stores the
selected method's line-113 note; that note must reach `meta.limitations`.
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
carryforward attribution remains ISO-only; other deferral items need their
separately worked worksheets and disclosures. AA72 below derives the current
AMT and owner PTET credit limits.

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

This builder derives arithmetic over reviewed components and prices TY2025
FTB5805's regular method. Manually gated credits, Schedule CA exceptions,
late-payment interest and special penalty methods require their named
worksheets. A confirmed external FTB5805/5805F worksheet or eligible FTB billing
is explicit and disclosed. It does not file a return.

## FTB5805 regular-method extension contract (AA76)

Primary sources read live 2026-10-20: [TY2025 form](https://www.ftb.ca.gov/forms/2025/2025-5805.pdf),
[instructions, PartII and WorksheetII](https://www.ftb.ca.gov/forms/2025/2025-5805-instructions.html)
and [FTB interest/estimate penalty schedule](https://www.ftb.ca.gov/pay/penalties-and-interest/interest-and-estimate-penalty-rates.html).
This extends questionnaire item6. Items1-5 and7-13 keep their reviewed Form540
answers above; this computation introduces no new residency, income, credit,
local-tax, payroll, PTET, deduction or ordinary-tax regime.

The covered scope is TY2025, calendar-year individuals filing the existing
full-year resident Form540. Compute the regular method, including early/late
payments and either deemed withholding or a reviewed actual-date election.
Form5805F, fiscal years, estates/trusts, annualized income, disaster/other special
deadlines, waiver requests and separately prepared exceptional worksheets must
be identified and handled explicitly. A regular-method amount cannot stand in
for a potentially smaller annualized or waived amount without the taxpayer's
recorded method decision. An explicit choice for the FTB to calculate and bill
the penalty is a disclosed manual route, never a computed-zero claim.

The fetched CA parameters must supply the minimum balance, current-year share,
prior-year factors and AGI thresholds, current-AGI override and installment
shares. Reuse the existing `underpayment_screen` amounts where the worksheet
uses the same quantities. New figures ship unfetched and are collected by
`tax-setup`; no rate, factor, threshold or share is introduced as an engine
default. Keep planning computations in their existing scope.

The additional annual block is `state.estimated_tax_safe_harbor`:
`prior_year_share`, `prior_year_share_high_income`, five-status
`prior_agi_threshold` and `current_agi_override`, four `installment_shares`,
four effective ISO `due_dates`, `penalty_end` and `early_filing_deadline`.
The latter is the effective, legally rolled filing/payment deadline, not an
unadjusted nominal date. `underpayment_screen.minimum_balance` and
`underpayment_screen.tax_fraction` remain the shared floor/current-year share.
Rate rows carry `effective_from` (January1 or July1) and `annual_rate`; each row
expires at the end of its own half-year even if the next row is absent.

`state.underpayment_rates` is a separate fetched schedule of effective dates
and annual rates, spanning the underpayment periods through the following
April15. Source-pinning and provenance use **only**
`state.CA.underpayment_rates` as its independent multi-period pin. The ordinary
`state.CA` pin remains annual. Half-year provenance periods must be validated;
rate rows must be finite, ordered, nonoverlapping and cover every day actually
priced. A missing future period cannot inherit the last rate. The auditor's
schema, multi-period roster and instructions must reflect the same rule.

PartII line1 uses checked Form540 lines48+61+62 plus the actual IRC453A
interest component of line63, excluding lump-sum and accumulation-distribution
tax. Do not use all of line63 or total tax indiscriminately. Line3 includes
BOTH lines71 and73. Prior-year tax is the analogous reviewed worksheet amount;
the prior-year floor needs the amount net of its relevant credits/withholding,
not a total-tax upper bound incorrectly treated as proof that the floor fails.
Full-year/prior-return facts and California AGI are distinct from federal ones.
Unknown necessary prior facts cannot silently select the more expensive prong.

Use the same fetched installment weights for required installments and deemed
withholding. Preserve actual payment dates and apply money to the oldest unpaid
installment first, regardless of a payment's designation. The opening applied
overpayment belongs in the first period once; it is not new cash. Actual-date
withholding must reconcile to both checked withholding lines. Unknown dates
cannot support pricing. A filed-and-paid early-return exception may remove the
last installment's penalty only under its reviewed covered-year conditions.

Price simple per-day penalty, splitting at rate changes, payments and calendar
year boundaries (the denominator follows the actual year). Do not reuse federal
equal-quarter requirements, federal rates, or daily-compounded interest.
Keep intermediate precision and round the published result to cents. Hand-worked
tests must cover a rate change, leap-year denominator validation, an early
payment allocated backward, deemed versus actual withholding, each zero-penalty
screen and incomplete rate coverage.

The result needs a dedicated checked `california_540.ftb_5805` workpaper with input
echoes, method, required annual payment, installment/payment/penalty detail,
total and `computed_tax_year`. Its priced total owns Form540 line113 before
settlement lines114/115 and balance summaries are derived. Builder checks and
standalone tie-out must recompute it from the same profile, law and checked
Form540 components, before any federal scope waiver. Rebuilds are idempotent;
source changes invalidate the receipt. Retire the old uncomputed-penalty note
only when the selected route supports that change; a manual route keeps an
explicit disclosure. Required facts belong in the setup interview, profile and
registered answer contract. Filing-package instructions must name the method
and the official form/attachment conditions without claiming an e-file artifact.

No new state row or withholding category is introduced. Register any additional
CA package workpaper on the existing row; PDF and verification remain section
generic. Ordinary projection/withholding forecasts retain their advisory
timing disclosures. The freshness-auditor definition edit, optional-schema
coverage fix and revised acceptance digests require the same fresh-session
replay as AA52, using prewritten criteria and the known-answer fixtures.

## AMT and owner PTET credits extension contract (AA72)

Contract recorded before implementation, 2026-10-21. The thirteen questionnaire
answers above remain in force; answers 3 and 10 now include calculated FTB3510
Part II and FTB3804-CR owner credits. The reviewed form year remains TY2025.
Sources read live, including visual inspection of the printed credit columns:
[FTB3510](https://www.ftb.ca.gov/forms/2025/2025-3510.pdf),
[its instructions](https://www.ftb.ca.gov/forms/2025/2025-3510-instructions.html),
[FTB3804-CR](https://www.ftb.ca.gov/forms/2025/2025-3804-cr.pdf),
[its instructions](https://www.ftb.ca.gov/forms/2025/2025-3804-cr-instructions.html),
and the Schedule P sources above.

The opening `prior_year.ca_amt_credit_carryforward` is the qualified credit
available this year (FTB3510 line32), reconciled to the prior return. It includes
unused older credit and the prior year's deferral AMT. For an external return,
work Part I's exclusion refigure before recording that balance; the engine does
not reconstruct an unparsed prior return. Confirm that reconciliation in
`ca_credits.amt_opening_confirmed` and the existing headless worksheet answer.
FTB3510 lines33–39 are derived: line33 is Form540 line31; line34 is exemptions
plus the other allowed A1/A2 credits; line36 is current TMT. The line38 allowable
credit replaces the old entered `ftb3510_credit_allowable`. The next balance is
opening minus ALLOWABLE plus current ISO-attributable AMT. Existing ISO-only
attribution remains disclosed; other deferrals need their separately worked
exclusion/deferral worksheet. No amount is carried twice or preserved simply
because the filer omitted an allowable claim.

`ca_credits.other_credits` identifies separately worked special credits by
printed code and allowed amount. The TY2025 Schedule P code table determines
their section; the caller cannot relabel A credits as B. Line40 dependent care
is A1 and line46 renter credit is B1. Other credits must already satisfy their
own eligibility, business-credit and carryover worksheets. Their allowed amounts
must fit the appropriate remaining tax; unknown codes and Section C solar AMT
offsets require separate support and refuse this path. The builder orders A1,
other A2, code188, B1/B2, B3 OSTC and finally code242. It derives lines43/44 for
one or two positive special credits, or line45 alone for more than two. Credit
names/codes and the workpapers persist beside the amounts. Neither new credit
uses `amt_screening` or Box C as a limit.

`ca_credits.ptet_current` records the owner's K-1 credit amounts allocated to this return before the tax limit, not
entity QNI multiplied again. Each record has an opaque `credit_id`, `source_id`
matching the parse ledger, and amount. The ledger's separate
`ca_ptet_credit` key box ties to the full reported credit, including any allocated amount
limited this year. Retain the whole reported amount and separate-spouse allocation
in the source summary; the source ledger and current credit rows carry the same
reviewed share for this return. Confirm election/payment eligibility and separate-spouse
allocation in `ptet_eligibility_confirmed`. This path covers direct individual
owners; a disregarded SMLLC's additional tax-difference limit requires a
separate reviewed worksheet and is refused by `ptet_owner_type` until supported.
`prior_year.ca_ptet_credit_carryovers` preserves credit_id, origin_year and
amount. Apply oldest first, preserve remaining vintages and report expired
amounts explicitly. The fetched `state.ptet_credit.carryforward_years` supplies
the expiry period and `.first_tax_year` rejects vintages before the programme
(including predecessor-regime carryovers; a successor statute does not reset their origin); no inline annual or unindexed credit figure is added.
FTB3804-CR PartII lines1–5 are derived. B4 uses remaining regular tax after
OSTC, including tax below TMT; it cannot offset Schedule P's AMT.

CA AMT rates move into fetched `state.amt.tmt_rate` and `.phaseout_rate`, with
empty seeds and the existing annual CA source pin, alongside exemptions and
phaseout thresholds. Missing/nonfinite values refuse checked computation.
The public math functions take those rates as parameters. No new exception to
AGENTS §2 is created.

The current-law check is distinct from enabling a new form year. FTB's
[current SB132 guidance](https://www.ftb.ca.gov/file/business/credits/pass-through-entity-elective-tax/help.html)
confirms the successor credit under **R&TC17052.11** for 2026–2030, including
the June-payment shortfall reduction and fiscal-year-owner timing. Section
17052.10's older sunset in the TY2025 booklet is not the current 2026 rule.
The legislature site refused direct access; the FTB's live primary-authority
explanation establishes the enacted change. TY2026 Form540 and that reduction
need their own reviewed form contract before computation is enabled.

Registrations: the existing CA registry, PDF, verification, withholding,
projection and YoY tables remain applicable; no new state row is needed.
Public helpers are re-exported. The checked builder derives and checks credits
before settlement and FTB5805; the independent tie-out checks reported K-1
credits and carry balances. Rollover preserves PTET vintages. Capability and
headless intake retain explicit unanswered screens, with worksheet disclosures
for facts the new computation needs. Old acknowledgement IDs remain valid.
Tests use hand-worked forms and synthetic law, including exemption-vs-line31,
nonzero TMT with zero AMT, B3-before-B4, carry expiry, line45 routing, missing
facts, nonfinite values, rebuilt checks and deliberate omission mutations.

## Residential rental extension contract (AA73)

The pre-implementation federal/California rental contract, questionnaire deltas,
source-verified form ordering, law/schema, scope gates and registrations are in
[`rental-income.md`](rental-income.md). In particular, FTB3801 uses federal
Form8582 MAGI, while depreciation and passive-loss balances remain separate.
Only the final allowed rental-net difference reaches ScheduleCA line5.
