# W11 — one state true-up pipeline

`StateTrueUpSpec` binds a state code to its liability/AGI fields, computed-section
guard, requirement policy, voucher calendar, withholding adjustment, compatible
report dataclass, and presentation metadata. Its result section comes from the
state registry. CA, NY and NC use one input/prior reader, payment annualizer,
target/schedule assembler, renderer and serializer. Public builders and report
fields keep their signatures and shapes.

State policy stays explicit. CA retains its existing safe harbor and installment
weights, including independently nullable prior tax/AGI. NY uses IT-201 line 60,
requires both prior tax and NYAGI, canonicalizes QSS to MFJ, and reads fetched
shares. NC requires explicit payment inputs and eligibility answers, excludes
use tax from its requirement, includes it in the balance target, and rounds
NC-4 extra withholding upward to whole dollars. Its existing input rounding is
preserved. No new law amounts are introduced.

Missing-cache notes and state balances are collected through the specifications;
a state balance still keeps a payment target alive after withholding closes.
The federal-only coverage note now names all three supported states. The CA,
NY and NC presentation policies keep their own statutory explanations around
one shared table/adjustment layout. Outer report fields remain explicit to keep
the public dataclass and static typing compatible.

A pre/post comparison over synthetic statuses, dates, payments and prior-year
states found an existing NY crash: the shared voucher timing helper accessed a
federal/CA `de_minimis` field absent from NY's adapter. That optional lookup now
uses the same duck-typing convention as the prior-year-floor field. NY's fetched
required-payment computation already owns its floor; no federal floor is
borrowed. Covered undated estimates retain their timing note; withholding that
covers the annual requirement gets none. Regression tests exercise both cases.

Validation compares report dataclasses, Python JSON payloads and rendered
Markdown with the pre-refactor implementation for ordinary stateful cases.
The intentionally updated federal-only wording and formerly crashing NY cases
are checked separately. Runtime taxpayer data is never used in this work.
