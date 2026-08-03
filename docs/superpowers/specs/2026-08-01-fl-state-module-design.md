# 2026-08-01 — FL state module — design spec

## 1. Scope
Florida for full-year residents: an AFFIRMATIVE no-income-tax pathway. FL levies
no personal income tax (Fla. Const. Art. VII, §5) — there is no return to
compute. The module's product value: (a) a positive "no FL return exists or is
required" declaration in result.json/PDF/capability note (absence-of-tax must
never read as absence-of-data); (b) the federal Schedule A line 5a SALES-tax
election input via the IRS optional-tables method (the one federal computation
FL residents systematically miss); (c) FL county property-deadline reminders
(DR-501 homestead Mar 1, DR-405 tangible personal property Apr 1); (d) /tax-review
guardrails (use tax, nonresident exposure, NY→FL domicile-audit checklist).

## 2. Questionnaire answers (playbook step 0)
1. Starting point: N/A — no income tax, no return (Fla. Const. Art. VII §5).
2. Additions/subtractions: N/A. 3. Credits: N/A. 4. Local income taxes: none
(county PROPERTY deadlines handled via calendar instead). 5. Surtaxes: none.
6. Estimated payments: none exist. 7. Payroll-style items: none (no SDI).
8. Withholding forms: none — FL W-2s carry empty Boxes 15-17.
9. Extension: N/A. 10. PTET: FL has none (not in _PTET_STATES — correct).
11. Community property: FL is not in _COMMUNITY_PROPERTY_STATES — correct.
12. Itemized: no state schedule; the FEDERAL Schedule A sales-tax election is
the FL-relevant piece (compute_schedule_a already elects max(income, sales)).
13. Straight bracket math: N/A — introduce `type: "none"` so consumers can
distinguish "no tax" from "not fetched" (the regime discriminator this repo
lacked; WA's excise will add a third type later).

## 3. Fetched-law block
`create_state_template("FL")`: code/name/authority/`type: "none"`/
`no_income_tax: true`/note + `sales_tax: {state_rate: 0.06 (F.S. 212.05,
structural), county_surtax_rate: None (fetched)}`. Deliberately NO
brackets/standard_deduction keys — the freshness walker flags every empty
list, so a no-income-tax state must structure them out (playbook step 1).
CA/NY templates gain `type: "graduated_income"`; the generic fallback is
UNCHANGED (no type key) so other states' freshness behavior doesn't move.
Source pin: `state.FL` → floridarevenue.com (added to
_AUTHORITATIVE_NONGOV_DOMAINS — FL DOR is a .com, not .gov).

## 4. Result section
`florida_state`: informational — no_income_tax_statement,
state_return_required: false, sales_tax_deduction sub-block (table_amount,
local_addition, big_ticket_tax, total_line_5a_candidate), use_tax_note.
NO computed-evidence pair (playbook step 2): there is no computed state tax
for scaffold-vs-computed gating to protect; stated here explicitly per the
playbook's "if you add none, say so" rule.

## 5. Compute functions
`compute_sales_tax_deduction(table_amount, state_rate, local_rate=0,
big_ticket_tax=0) -> dict` — IRS Schedule A Optional State Sales Tax Tables
method: table + table×(local/state) + actual big-ticket tax. Its `total` is
the INPUT for compute_schedule_a's existing `state_local_sales_taxes`
parameter; the income-vs-sales election and SALT cap already live there.

## 6. Registrations
projection.STATE_SECTION_KEYS += "florida_state"; yoy_diff.OPTIONAL_SECTIONS
+= "florida_state" (appear/disappear tracking on a move year); capabilities
_state_note: type-none branch via _NO_INCOME_TAX_STATES (AK/FL/NV/SD/TX/WY —
WA and NH deliberately EXCLUDED: WA has the capital-gains excise, NH is
transition-year; they keep the estimate note until modeled); tax_calendar
state_property category. NOT registered (explicit non-goals below): tie-out
category, withholding true-up block, /tax-extend, retirement conformity sets
(FL conforms trivially — no tax).

## 7. Tests
Template shape; freshness (clean once county rate fetched; unfetched county
rate flagged; empty state dict still flagged); sales-tax math + validator
rejections; harvest banner both directions; capability note; calendar events
(gating, dates, weekend rollover, UID stability); registration literals.

## 8. Explicit non-goals
No tie_out.CATEGORY_TARGETS entry (no FL withholding exists — the playbook's
both-halves-or-neither rule → neither). No withholding.py state block (no
state withholding to true up). No /tax-extend mechanics (no FL return to
extend). No part-year/nonresident (gated at C1 moved_states_this_year). No
FL corporate/TPP tax computation (calendar reminder only). WA/NH regime
notes unchanged. yoy STATE_MOVER_FIELDS untouched (FL has no movers; the
per-section restructure the playbook prescribes lands with the NY module).
