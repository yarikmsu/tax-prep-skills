# Individual Form 8606 basis

A joint return keeps independent `primary` and `spouse` worksheets. Only an
individual's own traditional, SEP and SIMPLE IRA balances enter their pro-rata
denominator. The [IRS Form 8606 instructions](https://www.irs.gov/instructions/i8606)
require separate forms for spouses and explain December 31 values and outstanding
IRA-to-IRA rollovers. Fetch the covered year's instructions before applying any
special worksheet. No annual contribution limit is supplied by this module.

## Opening balances and migration

`prior_year.ira_basis_by_taxpayer` holds each owner's reviewed opening basis.
Legacy `prior_year.ira_basis` remains supported for a return filed by one
individual (including MFS); it belongs to `primary`. A nonzero legacy joint
scalar needs `prior_year.ira_basis_owner: primary|spouse` confirmed from the
individual's prior worksheet. A scalar and explicit owner balance must agree;
clear the scalar after migration. Never split or sum the spouses' basis.

Rollover preserves separate line 14 balances, including a worked zero. An
ambiguous joint archive creates `prior_year.ira_basis_unresolved`, which blocks
computation. An identity change or a spouse's transition to a separate return
also creates that marker. Confirm ownership and place each balance in the same
individual's next workspace, then remove the marker. No spouse balance moves
to `primary` automatically. Existing target conflicts require review.

## Reviewed inputs

Call `compute_individual_8606(inputs, profile)` from the public computation
package through `sh agents/lib/run.sh`. `inputs` has exactly `tax_year` and
`by_taxpayer`. Each participating owner supplies:

```json
{
  "regular_worksheet_confirmed": true,
  "accounts_complete": true,
  "nondeductible_contrib_current": 0,
  "contrib_made_by_deadline": 0,
  "outstanding_rollovers": 0,
  "accounts": [
    {"account_id": "IRA-A", "person": "primary", "kind": "traditional_ira", "year_end_value": 0}
  ],
  "distributions": [
    {"doc_id": "R-A", "ordinary": 0, "converted": 0, "excluded": 0,
     "excluded_taxable": 0, "exclusion_reason": null}
  ]
}
```

Use opaque account identifiers, never full account numbers. List even zero
balances; an empty list explicitly confirms no remaining accounts. Include all
of that owner's traditional/SEP/SIMPLE accounts, including ones at unrelated
institutions. Confirm adjusted December 31 values after recharacterizations.
`outstanding_rollovers` contains only the reviewed amount that belongs on line 6.
The source `doc_id` comes from the parsed 1099-R, and each document is allocated
once to ordinary distributions, conversion, or exclusions. Gross is derived
from these allocations. Exclusion reasons are `qcd`, `rollover`, `hsa_funding`,
`returned_contribution`, or `recharacterization`; the reviewed taxable part of
returned-contribution earnings is supplied separately. Other exclusions must be
zero taxable. Review each exclusion's covered-year qualification; the enum does
not establish eligibility. Record confirmations in `answers.md`; missing
headless answers block.

`regular_worksheet_confirmed` affirms the existing regular Part I/II arithmetic
applies: no inherited IRA, Roth-distribution ordering, disaster repayment or
Form 8915-F amortization, divorce basis transfer, special combined deductible
contribution/distribution worksheet, or prior Line 15c Worksheet basis adjustment.
These need a preparer's separate worksheet; never set the flag just to bypass a
refusal. Every owner with opening basis needs a worksheet, even with no activity.
For distributions without basis, include that owner's reviewed worksheet too so
IRA-only 1040 totals have complete coverage. Pension distributions stay on 5a/5b.

## Gates, reporting and planning

On a joint return, known opening basis or an affirmative conversion/backdoor
screen requires the individual worksheets even if the result section is empty.
Classified IRA distribution records also require them; moving their gross onto
pension lines cannot waive the intake gate. Conversion screening values must be
boolean.

Save the complete returned block under `result.form_8606`. Its `by_taxpayer`
retains raw inputs and independent computed lines; only `gross_to_1040` and
`taxable_to_1040` are household sums. Route them to 4a and 4b. Basis and ratios
are never household totals. The completion gate reproduces both worksheets,
checks opening profile balances/year/status and verifies IRA-only routing.

Each retirement parse record also needs `retirement_kind`: `traditional_ira`,
`sep_ira`, `simple_ira`, `pension`, `roth_ira` or `inherited_ira`. IRA recipients
must be `primary` or `spouse`, never `joint`. The intake gate checks each IRA
source's owner, gross and complete allocation; a household sum cannot hide an
owner swap. An unclassified retirement source, unsupported IRA type, omitted or
phantom allocation blocks the individual worksheet path. Old records remain
loadable, but must be classified before using this path.

The summary PDF labels independent worksheets and the filing checklist lists
separate Form 8606 attachments for owners who need them. It remains a transfer
summary, not an official form or e-file artifact. A legacy joint section must
be migrated before filing.

Roth pro-rata planning on a joint return requires `--ira-owner primary|spouse`.
`--ira-value` and any explicit `--ira-basis` describe only that person's pool;
otherwise basis comes from that owner's profile balance. The saved report names
the owner. The planning estimate retains its existing missing-balance limitation.
