# Residential rental return contract (AA73)

Recorded before implementation, 2026-10-21. This extends the maintained
California contract; it does not enable a new Form540 year.

## Scope and sources

TY2025, calendar-year cash-method individuals: directly owned U.S. long-term
residential buildings and structural improvements, with no personal use,
substantial tenant services, self-rental, disposition, grouping/recharacterized
income, passive credits, PTP/K-1 passive activities, commercial revitalization
deductions or real-estate-professional treatment. Property receipts and expenses
are reviewed shares belonging to this return. Basis/at-risk and other loss-limit
worksheets must confirm that they do not restrict the amounts entering the PAL
calculation. Equipment, cost segregation, ADS, bonus/section179, amortization,
pre-MACRS methods and accounting-method corrections require separate support.
A false or unanswered scope fact retains the named unsupported rental gate;
acknowledgement permits external worksheets, never this engine's calculations.

Federal computation covers this scope for full-year CA filers and the toolkit's
existing typed no-income-tax regimes, including WA's ordinary-income treatment. Other computed
state returns retain their rental gate pending their own reviewed conformity
contracts. Existing residency, RDP/pro-forma and community-property gates remain.
A property outside the return's supported state still needs its separately
reviewed source-state obligation; nothing declares that no return is required.
Known opening CA loss or depreciation balances cannot be discarded by a
federal-only path after a state change. That case refuses until separate CA
closing worksheets are supported; it never advances old CA figures by inference.

Primary sources read live, explicitly TY2025:
[IRS Pub527](https://www.irs.gov/publications/p527),
[Form4562 instructions](https://www.irs.gov/instructions/i4562),
[Form8582](https://www.irs.gov/pub/irs-pdf/f8582.pdf),
[8582 instructions](https://www.irs.gov/instructions/i8582),
[Schedule E](https://www.irs.gov/pub/irs-pdf/f1040se.pdf),
[FTB3885A instructions](https://www.ftb.ca.gov/forms/2025/2025-3885a-instructions.html),
[FTB3801](https://www.ftb.ca.gov/forms/2025/2025-3801.pdf),
[3801 instructions](https://www.ftb.ca.gov/forms/2025/2025-3801-instructions.html),
and [Schedule CA](https://www.ftb.ca.gov/forms/2025/2025-540-ca-instructions.html).
Printed allocation columns were visually inspected. These establish form
structure; every law figure is still fetched for the return's covered year.

## State questionnaire

The thirteen California answers in `state-module-ca.md` continue to apply.
This extension changes answer2: ScheduleCA SectionB line5 is the difference
between the independently allowed federal and California rental results.
It does not copy a depreciation difference directly onto AGI before California
PAL limits. Answer1 still starts from federal AGI. Answers3–9 and12–13 retain
the existing exemption, local-tax, surcharge, estimates, payroll, withholding,
extension, deduction and tax-table contracts. Answer10: direct ScheduleE rents
never enter the PTET-eligible K-1-income memo. Answer11: existing ownership and
community-property allocation worksheets remain prerequisites.

## Intake and law

`profile.rental_return` selects `residential_rental_v1`, covered year,
explicit scope facts, property/asset inventories, ownership/active-participation
facts, separate-spouse living-apart decision, and the Form8582 MAGI adjustment
components. Property IDs and asset IDs are opaque alphabetic labels. Actual
addresses remain owner-only runtime facts. Assets retain service date, method
(formula/table), independently reviewed federal/CA depreciable basis, conversion
FMV when applicable, and prior depreciation evidence. Land is excluded;
conversion basis is the lower of adjusted basis and building FMV. Missing data
never becomes zero. Capital improvements are separate assets, never repairs.

The source ledger retains `rental_gross_rents` and
`rental_operating_expenses`, with per-property rows on each source record.
These are allocated source totals before depreciation/PAL, not the net tax
result. Source `kind: book` entries cover complete disjoint cash books;
`kind: information_return` entries reconcile `rental_reported_rents` separately.
MISC box1 is required even when zero. Reported rents are evidence already in
the full books, never additional cash; an unexplained excess over books blocks
until corrected/duplicate/disputed forms are resolved. Receipts retained as deposits are income only after the normal cash
rental-income worksheet determines their character. A returned deposit is not
rent. Reviewed ScheduleE expense categories exclude principal, escrow transfers,
capital improvements and personal amounts. Income and expense books must be
complete, including a documented zero when no rent was received.

`federal.rental` and `state.rental` are separate optional fetched-law blocks:
`residential_recovery_years`, `macrs_first_year`,
`minimum_active_ownership`, `special_allowance` and `phaseout_ceiling`
(by canonical filing status), `phaseout_rate`, and `gds_percentages`.
The percentage table holds month/age rates, not inferred copies of a prior
cache. Completely unfetched optional blocks do not nag non-rental returns;
a rental computation requires every parameter it uses. Populated blocks retain
normal shape, currency and source checks. Federal figures use their annual
federal pin, CA its annual state pin. No new inline-law exception is added.

## Arithmetic

Depreciation uses the reviewed GDS straight-line/mid-month method with the
fetched life, or the fetched published month/age percentages. Existing table
users cannot silently switch to the formula. Prior allowed/allowable
accumulation is reconciled to the retained schedule; a method/basis change or
missed depreciation requires the named correction worksheet. Current deduction
cannot exceed unrecovered basis. Federal and CA schedules, opening/closing
accumulation and current/prior-year asset subtotals stay separate.

Each property has current rents less expenses and depreciation, with separate
federal/CA prior passive losses. Form8582 PartsI–III/IV–VIII and FTB3801 use
those jurisdiction's amounts. Positive property income can absorb prior losses;
unused losses stay attached to the property. Prior losses retain active-origin
and other-origin buckets: PartIV requires active participation in both the
loss year and current year. Current participation never promotes an old
nonactive loss into the special allowance. Allocate the special allowance
only among eligible active-participation overall-loss activities (PartVI), then
allocate the remaining unallowed loss proportionally (PartVII). Cent remainders
are apportioned deterministically without exceeding a property's loss. ScheduleE
line24 retains current profits; line25 includes allowed current/prior losses;
line26 is their difference. Neither denied losses nor current depreciation
vanish from their separate closing records.

Form8582 MAGI is built from the return's non-rental income, positive overall
passive income and reviewed adjustment/exclusion components. It excludes taxable
Social Security and the adjustments named by the covered-year instructions;
it is not an entered final MAGI or a circular subtraction from stale AGI.
FTB3801 line6 uses **federal Form8582 MAGI**, not CA AGI. MFS living together
receives no special allowance; living apart uses its fetched status column.

The existing Schedule1 remainder is reconfirmed excluding rentals. Its tracked
MISC, W-2G and governmental-benefit totals combine with the computed rental
contribution exactly once. No manual aggregate survives beside that derived
line8. ScheduleCA receives only the final CA-versus-federal allowed-net
difference, after both PAL worksheets; do not also enter depreciation as an
`other_additions`/`other_subtractions` component.

## Gates, output and registrations

`schedule_e_rentals` contains the source/property inventory, federal/CA
ScheduleE/PAL/depreciation workpapers, closing asset and passive-loss records,
MAGI, line8 settlement and a checked `computed_tax_year` witness. The existing
builder writes and checks it, and the independent tie-out recomputes it from
profile, ledger and law even if the result section is dropped. Existing receipt
inputs already bind all these facts; a changed input invalidates the verdict.
Rollover preserves property and asset IDs, jurisdiction-specific balances and
review requirements. A dropped closing balance cannot silently restore zero.

Rental income also feeds income-dependent worksheets. After computation,
record a required review bound to the actual rental result/MAGI for QBI,
Form8960, federal/CA AMT and the other affected income-based forms. The checked return and
tie-out refuse a missing/stale review. NIIT uses the reviewed signed allowed
rental contribution, not gross receipts; QBI eligibility and vintage attributes
are not inferred. This is an explicit worksheet boundary, not a claim that
these additional forms were reconstructed from the rental books.

Register public math exports, ledger categories plus parse rows, setup/fetch
and compute steps, headless answer IDs, result/PDF/verify/filing-package forms,
and per-property rollover. CA already has a state registry row, withholding,
calendar and extension mechanics; no new jurisdiction or duplicate roster is
needed. Ordinary scenario/state planners retain their explicit rental/AMT
limits; they must not advertise repricing these new worksheets. Page1's named
limitations state every retained worksheet boundary. Summary PDFs are not
completed official forms or an e-file transmission.

Tests use hand-worked published-form cases and synthetic law: first/full/final
depreciation years, table continuity, conversions/land, prior accumulation,
active/nonactive mixes, income absorption, phaseout/MFS boundaries, distinct CA
balances with federal MAGI, negative net income, zero rentals and stale reviews.
Intake drops, duplicate IDs, nonfinite inputs/law, unsupported scope, receipt
invalidation, rollover and omissions are tested before a PR is marked ready.
