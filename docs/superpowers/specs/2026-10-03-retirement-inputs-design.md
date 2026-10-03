# X10 — retirement inputs and explicit state conformity

Live primary sources read 2026-10-03:
[final Roth catch-up regulations](https://www.irs.gov/irb/2025-40_IRB)
and [Publication 969 (2025)](https://www.irs.gov/pub/irs-pdf/p969.pdf).
Annual amounts remain fetched parameters, including the high-earner threshold.

Track optional `roth_401k_ytd`, bounded by aggregate employee deferrals. The
remaining catch-up can be pre-tax only after the year's designated Roth
deferrals satisfy the applicable catch-up amount. Price only that available
pre-tax portion; the total contribution capacity is unchanged. Missing split
retains conservative Roth-only savings. Plans must confirm they permit the
pre-tax treatment (the final regulations generally apply from 2027, with early
application permitted); do not assume the payroll administrator implements it.

HSA capacity requires a twelve-entry first-of-month eligibility/tier schedule.
Monthly coverage may be none, self_only or family. The last-month election
requires December eligibility and acknowledgement of the following-year testing
period, and takes the greater of the monthly limit and December's annual limit.
Catch-ups are prorated too. The spouse's separate catch-up requires a separate
eligibility schedule and a joint return; shared base limits are never doubled.
Unknown eligibility blocks capacity recommendations instead of assuming a year.

A fetched `retirement_conformity` matrix has one row for each US state and DC,
with tax year and independent employee_401k/traditional_ira/hsa treatment.
Treatment is true, false or unverified (null); absence never implies conformity.
Each populated row is source-pinned and year-bound. Only a verified true entry
prices a state saving. False and unknown both price zero, with distinct notes;
unknown can be researched and filled at runtime from the resident state's
current-year DOR instructions. This covers all jurisdictions without encoding
unverified law or requiring every state to be fetched for one taxpayer.

Tests use deliberately synthetic annual limits for arithmetic and explicit
synthetic conformity rows for each jurisdiction, not claims about current law.
They cover prior Roth below/at/above the catch-up amount, contribution overages,
partial-month and tier schedules, last-month conditions, separate spouse limits,
and absent/stale/source-less/false conformity entries across all jurisdictions.
