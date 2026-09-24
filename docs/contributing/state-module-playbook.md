# Adding a state module — contributor playbook (E5)

TWO states are computed in depth; every other state is a bracket-based estimate
behind an honest capability note. That is a product decision (2026-07-12), not
a technical limit — the two modules are a worked pattern this playbook writes
down.

- **California, Form 540** — the first, and the one this playbook's examples
  are drawn from.
- **New York, Form IT-201**, FULL-YEAR RESIDENT only — the State tax, and the
  New York City resident tax with the metropolitan commuter transportation
  mobility tax (lines 47-58). Line 47a reads the City's own tax table below its
  fetched ceiling and the City rate schedule above it (AA13, which found that
  nothing had computed it before). The Yonkers lines 55 and 56 are summed from
  arguments that default to zero, not computed (AA12). The `nyc_resident`
  capability rule was deleted when the City block landed and the flag became its
  switch, so a City taxpayer sees no gate. The scope is a full-year resident of
  New York alone: a move between states BLOCKS (`part_year_residency`), so does
  statutory residency in a second state (`statutory_residency_second_state`),
  and so does income sourced to any state outside the no-income-tax list
  (`nonresident_source_income`), because part-year and nonresident returns
  (IT-203) and the resident credit for tax paid to another state (IT-112-R) are
  not modeled — Z8. A City resident for only part of the year who stayed in the
  state is REFUSED at compute time, single or joint: the part-year City line 50
  (IT-360.1) is not modeled (AA11). Within that scope, IT-196 lines 1-45, IT-201
  line 45's other taxes, the AMOUNT of each refundable credit and line 59's
  sales and use tax are not computed either — each taken as an input or asked,
  and each NAMED on `meta.limitations`. The design spec is
  `docs/superpowers/specs/2026-09-10-ny-state-module-design.md`; the New York
  keying gotchas a cache reader needs are in
  `docs/contributing/tax-law-cache.md`.
  Expectations: steps 0-2 (the
questionnaire, the fetched-law block, the result section) fit a ~90-minute
Claude Code session; compute functions + hand-computed worksheet tests are
a further multi-hour session plus at least one adversarial review round —
that is what CA's own features took, per the roadmap history this playbook
cites. Open this file and
`docs/superpowers/specs/TEMPLATE-state-module.md` in your session and tell
Claude which state you're building.

## The pattern in one paragraph

A state module is: a **fetched-law block** (`create_state_template`), a
**result section** modeled on `california_540`, **rates-parameterized
compute functions**, a **`/tax-compute` step 10 branch**, and
**registrations** in the handful of modules that enumerate states
(projection, tie-out, capabilities, and any nonconformity sets). Everything
else — the PDF and the verify diff — is section-generic and picks your
state up automatically once the section exists (the YoY review is NOT:
its state movers are registered in step 5).

## Variant: no-income-tax states (`type: "none"`)

FL is the worked example. The state template carries `type: "none"` +
`no_income_tax: true` and deliberately NO `brackets`/`standard_deduction`
keys (the freshness walker flags every empty list — structure out what you
will never fetch). The result section is INFORMATIONAL (an affirmative
no-return statement) with no computed-evidence pair — state that non-goal in
your spec. Register: `STATE_SECTION_KEYS`, `OPTIONAL_SECTIONS`,
`capabilities._NO_INCOME_TAX_STATES` (+ `_state_note`). Do NOT register a
tie-out withholding category (no Box 17 exists — both-halves-or-neither →
neither) or a withholding true-up block. The federal Schedule A sales-tax
election is the state's one computation (`compute_sales_tax_deduction` →
`compute_schedule_a state_local_sales_taxes`). States with a NON-income tax
base (WA's capital-gains excise) are NOT `type: "none"` — they get their own
regime type and module.

## Step 0 — Answer the state questionnaire first

The CA sharp edges below all cost a review round each. Ask them about YOUR
state before writing code, and record the answers in your spec:

1. **Starting point**: does the state return start from federal AGI (like
   CA Form 540) or federal taxable income, or define its own income?
2. **Additions/subtractions**: HSA conformity? Retirement-deferral and IRA
   conformity (PA/NJ/MA differ federally)? Alimony (CA doesn't conform to
   the TCJA repeal)? Muni interest from other states?
3. **Credits**: personal/dependent exemption credits vs deductions?
   Renter's/EITC-piggyback credits?
4. **Local taxes**: county/city income tax (the NYC precedent: GATED until
   Z1-Z3 modeled it for a full-year resident, never half-modeled)?
5. **Surtaxes**: anything like CA's 1% mental-health tax over $1M?
6. **Estimated payments**: equal quarters or weighted (CA is 30/40/0/30
   with NO September installment)? A high-income safe-harbor override like
   R&TC §19136.3?
7. **Payroll-style items**: an SDI/PFL with a wage cap (year-aware — CA's
   cap disappeared in TY2024)?
8. **Withholding forms**: the DE 4 analogue for the true-up skill?
9. **Extension**: automatic like CA (payment-only via FTB 3519) or
   form-based?
10. **PTET**: does the state have an elective pass-through tax, and is it
    already in `_PTET_STATES` / `ptet.py`?
11. **Community property**: already handled globally via
    `capabilities._COMMUNITY_PROPERTY_STATES` — is your state in it?
12. **Itemized deductions**: own schedule (CA Schedule CA) or federal
    piggyback? High-AGI phase-out?
13. **Is the tax straight bracket math?** Or does a recapture/worksheet
    regime override it above an income threshold (the NY §601(d-1)
    tax-table benefit recapture is the canonical trap — plain bracket
    math UNDERSTATES tax there, violating the bias rule before you've
    written a line)?

## Step 1 — Fetched-law block (`agents/lib/tax_law_fetcher.py`)

Add a state entry to `create_state_template(state)` next to `"CA"`:

- REQUIRED: `code` (2-letter — `/tax-project`'s state block gates on it),
  `name`, `standard_deduction` (`single`/`mfj` at minimum), `brackets: []`.
- Every bracket, rate, cap and threshold ships as `None`/empty and is FETCHED
  by `/tax-setup` via WebSearch from the state DOR, even when the current
  statute does not index it. Only statutory-fixed structural constants
  explicitly enumerated in AGENTS.md §2 may remain inline; today that is the
  existing CA `mental_health_surcharge` rate and threshold. Bracket count and
  other nonnumeric shape facts may remain structural.
- **A stub for your state may already exist** (NY ships one) — rework it,
  don't duplicate. Audit `_SKIP_KEYS` for any new descriptive key (the
  fetcher's MAINTENANCE comment requires it), and remember the freshness
  walker flags EVERY empty list and None — a field your design never
  fetches will nag INCOMPLETE forever, so either fetch it or restructure
  it out.
- Add absent-key guards to `check_freshness` for any block whose absence
  must nag (see the CA `itemized_phaseout` precedent) and extend the
  freshness fixtures in `tests/test_tax_law_fetcher.py`.
- **Wire the fetch**: extend `.agents/skills/tax-setup/SKILL.md` step 4 with one
  WebSearch bullet per FETCHED field of your block, mapped to its JSON
  path (CA-precedent style — the generic other-state fallback fetches
  only brackets + standard deduction, so unfetched fields hard-error
  compute or nag forever).
- **New input facts**: if an addition/subtraction needs something the
  pipeline never collects (a 529 deduction, a W-2 box-14 pickup code, an
  age-linked exclusion), extend the `tax-setup.md` interview groups and
  the `tax-parse.md` extraction notes in the same PR — CA's add-backs
  ride existing flags/documents; yours may not.

## Step 2 — Result section (`agents/templates/result_template.json`)

Model on `california_540`: a `<state>_<form>` section (e.g. `ny_it201`)
with `_include_if`, the computed lines, withholding, and a `note`. Two
conventions are load-bearing:

- **All-zero scaffold ≠ computed.** Downstream consumers use
  computed-evidence gating so a template copied by `/tax-setup` never
  reads as a computed $0 return. There is no generic registry: write a
  `_<XX>_COMPUTED_EVIDENCE_FIELDS` tuple + `_<xx>_section_computed()`
  pair in `projection/` mirroring the CA pair
  (`_CA_COMPUTED_EVIDENCE_FIELDS` / `_ca_section_computed`), and consume
  it from any state-specific projection/withholding block you add — if
  you add none, say so explicitly in your spec. Give your section a field
  that is ALWAYS non-zero when computed (CA uses the standard deduction).
- Field names must match what the skills instruct writing — the tie-out
  and `/tax-generate` read them literally.

## Step 3 — Compute functions

**The state's FORM math goes in `agents/lib/tax_computations/state/<postal>.py`**,
one file named by the postal code. W2 made `tax_computations` a package for
this; `ny.py` and `ca.py` are the two worked examples.

That is form math and not "everything about the state". PLANNING-side state
math stays beside its federal twin in the planning module that owns the
question — `projection.compute_ca_safe_harbor` and
`projection.compute_ca_estimated_vouchers` are the pattern, with
`withholding.py`, `tax_calendar.py` and `ptet.py` carrying their own CA blocks
for the same reason. `compute_ny_required_annual_payment` sits in `state/ny.py`
instead and is the EXCEPTION, not a second convention to copy.

Two rules that are structural rather than stylistic:

- Shared primitives come from `_shared.py`, **never from the parent package** —
  the parent re-exports every state module, so importing from it is a cycle. If
  a primitive you need is still in the parent, MOVE it to `_shared.py` in your
  change; `_shared.py`'s docstring says what may live there.
- Add your public names to the parent `__init__.py`'s re-export block in the
  same change. Callers address them as `tax_computations.<name>`, and some of
  those callers are skill markdown that no sweep can rewrite.

Follow `compute_ca_itemized` and the rest of `state/ca.py`:
rates and thresholds as PARAMETERS (from the fetched block), round to 2dp,
strict validators (`_shared._amount` rejects bool/NaN/Infinity, and negative
unless you pass `allow_negative`; `negative_hint` appends a sentence saying what
a negative MEANS in your domain, which is the whole of what NY once kept a
private copy of the function for), return dicts with a `notes` list.

**A figure the module ELECTS on is a required parameter, never an optional
one.** Both itemized forms took an optional standard deduction and, without it,
returned something that read as a settled election — CA a `None` that tests
falsy, NY the itemized total sitting in the field a caller reads as elected.
Both under-state the deduction for a taxpayer whose standard deduction is
larger. Q7, ruled 2026-09-13: require it. A banner warning that an election may
be wrong is not a substitute for the election.

**Filing status is VALUE-validated at every entry point, and a test derives
that rather than listing it.** Both examples now agree: `ca.py` calls
`_shared._normalize_filing_status`, which returns a canonical UPPERCASE code and
hard-errors on anything outside the set, and `ny.py` calls `_ny_status`, a
one-line wrapper that delegates to the same validator and lower-cases the result
because New York keys its own tables and its cache blocks in lowercase. Either
shape is fine; validating nothing is not. `ny.py` used to compare lowercase
literals and never validate, so the very string `_normalize_filing_status`
returns took the SINGLE branch rather than raising — Q9 in the 09-05 backlog,
ruled and converted 2026-09-13. `TestEveryStateEntryPointValidatesItsFilingStatus`
asks the tree which public functions take a `filing_status` and requires each to
normalise it, so a new one cannot arrive unvalidated. Bias rule: **never silently understate
tax or overstate deductions** — degrade loudly (a note naming the gap) or
hard-error; a missing fetched value is an error or a loud skip, never a
guessed number.

## Step 4 — `/tax-compute` step 10 branch

Add your state branch beside the CA one in `.agents/skills/tax-compute/SKILL.md`
step 10: starting-point derivation, additions/subtractions, tax from the
fetched brackets, credits, surtaxes, withholding, balance. Instruct writing
every field your template defines (input echoes included — see the
`iso_bargain_element` lesson: a field the compute step never writes
false-fails the tie-out).

## Step 5 — Registrations (the enumeration checklist)

Grep-verify each of these — they are the places "state" is enumerated:

| Where | What to add |
|---|---|
| `projection.STATE_SECTION_KEYS` | your result-section key (enables the prior-year state gate) |
| `yoy_diff` | RESTRUCTURE `STATE_MOVER_FIELDS` per-section (it is a flat CA-only list gated on `california_540` in `_state_fields_present` — appending NY fields to it would gate NY movers on CA sections) AND add your section key to `OPTIONAL_SECTIONS` |
| `tie_out.CATEGORY_TARGETS` **paired with `.agents/skills/tax-parse/SKILL.md`** | a `state_withholding_<xx>` category → your section's withholding field, AND the matching `W-2 box 17 (<XX>) → state_withholding_<xx>` row in tax-parse.md's step-6.5 category table. **Both halves or neither**: the tie-out only checks categories the ledger EMITS, so a registered-but-never-emitted category is dead protection (the gate passes vacuously with your state's withholding unverified), and a parse subagent improvising the name before registration hard-errors the whole ledger. A doc-sync test enforces the pairing. |
| `.agents/skills/tax-setup/SKILL.md` step 4 | per-field WebSearch fetch bullets for your block (see step 1 — the generic fallback fetches brackets + standard deduction only) |
| `capabilities._state_note` | upgrade your state from the bracket-estimate note to a depth note — **do this LAST (step 7)** |
| `retirement_dashboard` sets | `_HSA_NON_CONFORMING_STATES`, `_IRA_DEDUCTION_NONCONFORMING_STATES`, `_RETIREMENT_DEFERRAL_NONCONFORMING_STATES` if your state belongs |
| `tax_calendar.py` **and** `agents/lib/ptet.py` | weighted estimated-payment events if not equal quarters (CA-ES precedent); PTET: BOTH modules carry independent `_PTET_STATES` + `_PTET_WINDOWS` copies — update both or neither |
| `withholding.py` / `projection/` | projection has a generic bracket path (`project_state_tax`); **withholding has NO generic state path** — it is federal + CA-only, so either add a state true-up block or list the gap explicitly in your spec's non-goals (an unlisted gap silently drops your state's true-up) |
| `/tax-extend` | your state's extension mechanics (only CA is modeled today — the skill says so) |

## Step 6 — Tests

- Hand-compute expected values from your state's OWN tax tables/worksheets
  and cite the source in comments (repo rule).
- Cover: bracket math, each addition/subtraction, credit ordering,
  presence/scaffold gating, validator rejections (bool/NaN/negative),
  freshness fixtures, and one end-to-end result-section shape test.
- Update the test-count line in `docs/contributing/test-invariants.md`
  (enforced habit) and keep fixtures under `tests/fixtures/` (PII-scan
  allowlist).

## Step 7 — Honesty surfaces

- The capability gate's state note is the user-facing truth about depth —
  update it LAST, only when the above is real.
- Anything you deliberately don't model (a local tax, a niche credit) goes
  in the compute step as a loud note that lands on `meta.limitations`,
  never silently.

## What NOT to do

- Don't hardcode indexed amounts in code (two PTET P1s were exactly this
  drift).
- Don't map QSS→MFJ by reflex — check YOUR state's grouping (federal Form
  8863 groups QSS with Single; states vary).
- Don't reuse `california_540` for a second state or overload its fields.
- Don't model part-year/nonresident allocation — that's the gated
  community track (`moved_states_this_year` blocks at C1) and a much
  bigger design.

## Definition of done

`pytest tests/` green with your additions; `/tax-setup` fetches your block;
a synthetic profile computes your section end-to-end; the tie-out passes
with your withholding category; the capability note tells the truth; the
`docs/contributing/test-invariants.md` test count + your state's depth
description in `AGENTS.md` updated; spec written from
`docs/superpowers/specs/TEMPLATE-state-module.md`.
