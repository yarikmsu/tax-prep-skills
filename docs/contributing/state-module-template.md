# {STATE} state module — maintained contract template

Copy this file to `docs/contributing/state-module-{xx}.md`, fill every section,
and index it in AGENTS.md §9. Blank answers mean “not researched”, never
“does not apply”. Use `docs/contributing/state-module-playbook.md` for the
implementation gates. Keep the contract current as code changes; the PR holds
the implementation plan and review evidence, and the backlog holds status.

## 1. Scope

- State / form: {e.g. NY / Form IT-201}
- Covered form years and residency scope: full-year resident by default.
  Name any separately reviewed variant and the gates for other cases.
- Local taxes in scope: name each computation or the gate that refuses it.

## 2. Questionnaire answers (playbook step 0)

| # | Question | Answer + citation (statute / DOR publication) |
|---|---|---|
| 1 | Starting point (federal AGI / TI / own) | |
| 2 | Additions/subtractions (HSA, retirement, IRA, alimony, muni) | |
| 3 | Credits (exemption credits, renter's, EITC piggyback) | |
| 4 | Local income taxes | |
| 5 | Surtaxes | |
| 6 | Estimated-payment weighting + safe-harbor quirks | |
| 7 | SDI/PFL-style items (wage caps, year-awareness) | |
| 8 | Withholding-form analogue (DE 4 equivalent) | |
| 9 | Extension mechanics | |
| 10 | PTET regime + window | |
| 11 | Community property (already global — confirm membership) | |
| 12 | Itemized schedule + high-AGI phase-out | |
| 13 | Straight brackets or a recapture/worksheet regime? | |

## 3. Fetched-law block

Paste the exact `create_state_template` entry, marking fetched annual fields
and structural arithmetic separately. Inline figures require an enumerated
AGENTS.md §2 exception; a statutory citation alone does not authorize one.
Name the key groupings, year stamps, freshness checks and authoritative sources.

## 4. Result section

Section key: `{xx}_{form}`. Paste the field list and the registry row's
`evidence_fields`. Evidence must come from computation, never parsed withholding.
If all amount fields can be zero, use a computed witness such as
`computed_tax_year`, with its validator and consumer checks. An informational
no-return section instead names its `informational_key` and has empty evidence.
Explain how projection, true-up and variant selection recognize the section.

## 5. Compute functions

List names, inputs (including fetched-block paths), outputs and emitted notes.
State the bias direction and gate or advisory degradation for every missing
input. Name manual worksheets and required eligibility attestations.

## 6. Registrations

Fill every row of playbook step 5 with a concrete change or “N/A because …”.
The registry is the shared roster; do not introduce another per-state table.

## 7. Tests

List hand-computed worksheets and source years, validator cases, synthetic
fixtures and integration checks. Verify computed zero, absent scaffolds, wrong
years, ledger mappings, ordinary planners and the documented launcher.

## 8. Explicit limits

Name every unmodeled form, tax, residency case and worksheet, the gate or
manual input that handles it, and the note that reaches `meta.limitations`.
Generating a summary does not file a return.
