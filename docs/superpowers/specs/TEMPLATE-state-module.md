# YYYY-MM-DD — {STATE} state module — design spec (TEMPLATE)

> Copy this file to `docs/superpowers/specs/YYYY-MM-DD-{xx}-state-module-design.md`,
> fill every section, and keep it honest — blank answers mean "not researched
> yet", not "doesn't apply". Companion guide:
> `docs/contributing/state-module-playbook.md`.

## 1. Scope

- State / form: {e.g. NY / Form IT-201}
- Residency scope: full-year resident ONLY (part-year/nonresident stays
  gated at C1 — out of scope for a state module)
- Local taxes in scope? {e.g. NYC — if NO, name the gate that blocks it}

## 2. Questionnaire answers (playbook step 0)

| # | Question | Answer + citation (statute / DOR pub) |
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
| 13 | Straight bracket math, or a recapture/worksheet regime above a threshold (NY §601(d-1) precedent)? | |

## 3. Fetched-law block

Paste the exact `create_state_template` entry you'll add, marking each
field FETCHED (indexed) vs STRUCTURAL (statutory constant + citation).

## 4. Result section

Section key: `{xx}_{form}`. Paste the field list. Name the ALWAYS-non-zero
computed-evidence field and paste your `_{XX}_COMPUTED_EVIDENCE_FIELDS` /
`_{xx}_section_computed()` pair (mirroring the CA pair in projection.py) —
or state explicitly that no projection/withholding block consumes it yet.

## 5. Compute functions

List each function (name, inputs incl. which fetched-block paths, outputs,
notes emitted). State the bias direction of every degrade path.

## 6. Registrations

Tick each row of the playbook step-5 table with the concrete change or an
explicit "N/A because …".

## 7. Tests

List the hand-computed cases with their worksheet sources, the validator
cases, and the fixture files. Target test-count delta.

## 8. Explicit non-goals

What this module deliberately does not model, and the loud note each gap
emits (these lines land on `meta.limitations`).
