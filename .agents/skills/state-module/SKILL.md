---
name: state-module
description: Add support for a new state to the toolkit — the ordered gates for the questionnaire, fetched-law block, result section, compute functions, registrations and tests, with the traps that cost a review round each. Use when explicitly asked to implement new toolkit state support or extend an existing state module; do not use for preparing a taxpayer return in an unsupported state.
license: Apache-2.0
---

# state-module

Adding a state is the toolkit's designed extension point, and the pattern is
already written down. This skill is the ORDER and the GATES; the detail lives in
two files you should open alongside it and follow directly:

- `docs/contributing/state-module-playbook.md` — the worked pattern, step by step
- `docs/superpowers/specs/TEMPLATE-state-module.md` — the spec template

Worked precedents: **California** (the in-depth `graduated_income` module) and
**Florida** (PR #65, the `type: "none"` regime).

Do not re-derive any of it from the CA code by reading around. The playbook
exists because each of its warnings cost a review round the first time.

## Gate 0 — Is this actually a state module?

Two things get mistaken for one:

- **A `type: "none"` state** has no income tax at all (FL and TX are examples). It
  is a POSITIVE declaration in the fetched-law block, never an omitted section.
- **A state with a non-income tax base** — Washington's capital-gains excise is
  the example — is NOT `type: "none"`. It needs its own regime type and its own
  module. Modelling it as "none" silently drops a real tax.

And two things are explicitly out of scope, gated rather than half-modelled:
city income tax, the one exception being New York City's full-year resident
tax, built as its own epics, and part-year or nonresident returns.
If the request needs either, say so before starting rather than shipping
something that looks complete.

## Gate 1 — Answer the questionnaire BEFORE writing code

Step 0 of the playbook is thirteen questions. Answer them for your state and
record the answers in your spec first. They are not a formality — each one maps
to a place where a state differs from California in a way the code cannot infer,
and skipping them is what turns a state module into a review loop.

The one that most often invalidates work already done:

> **Is the tax straight bracket math?** New York's §601(d-1) tax-table benefit
> recapture is the canonical trap. Plain bracket math UNDERSTATES the tax there,
> so the module violates the bias-direction rule before a line of it is written.

## Gate 2 — Every figure is fetched, and the STOP rule applies

AGENTS.md §2 governs state figures exactly as it governs federal ones. Never
hardcode a bracket, rate, cap or threshold — the fetched-law block defines the
shape, and those values arrive at runtime. Only a statutory-fixed structural
constant explicitly enumerated in AGENTS.md §2 may remain inline; today that is
the existing CA `mental_health_surcharge` rate and threshold. Fetch a new
state's surtax figures even when the current statute does not index them, and do
not expand the exception list from this skill.

To fill the block you will need to search the web for the state revenue
department's current schedules. Confirm each page explicitly names the tax year
being filed.

<!-- Keep the next line UNWRAPPED. test_web_dependent_skills_carry_a_stale_data_degrade
     splits on newlines as well as sentence ends and requires "stop" and "paste"
     in one segment, so reflowing this paragraph to 80 columns turns the
     assertion red for a reason nothing else in the file would explain. -->
If live web access is unavailable or cached-only, **stop and ask the user to paste the current figures** with the source URL, rather than carrying a prior year's number forward.

Use the authoritative-host policy in
`agents/lib/tax_law_fetcher.py::_source_host_authoritative`: http/https `.gov`
hosts plus its explicit primary-authority exceptions (`law.cornell.edu` and
Florida DOR's `floridarevenue.com` today). Every source goes in the cache's
`_sources` block; do not invent another exception in this skill.

A wrong state bracket produces a plausible-looking return that nothing
downstream detects. There is no test for currency, only for shape.

**Structure out what you will never fetch.** The freshness walker flags every
empty list, so a `type: "none"` state must omit `brackets` and
`standard_deduction` entirely rather than carrying them empty. A gate that fires
on correct data teaches the next agent to reason past the freshness gate — this
repository has already paid for that once, on its flagship state.

## Gate 3 — Registrations are the silent half

The PDF and the verify diff are section-generic and pick a new state up for
free. The modules that ENUMERATE states do not, and a missing registration fails
silently:

- the state REGISTRY, `agents/lib/tax_computations/state/registry.py` — one
  row that `STATE_SECTION_KEYS`, `OPTIONAL_SECTIONS`, the computed-evidence
  fields and the YoY movers all derive from (W10)
- `capabilities` — `_NO_INCOME_TAX_STATES` and `_state_note` for a none-regime
  state; `_COMMUNITY_PROPERTY_STATES` if applicable
- the projection module, the tie-out categories, any nonconformity set

Playbook step 5 is the checklist. Walk it item by item; do not judge by whether
the output looks right, because a state missing from an enumeration produces
output that looks entirely right.

For a `type: "none"` state, do NOT register a tie-out withholding category or a
withholding true-up block. There is no Box 17 to tie out, and the rule is
both-halves-or-neither.

## Gate 4 — Tests, with the expected values hand-computed

Expected values come from the state's own worksheets, computed by hand, with the
worksheet named in a comment beside the assertion. A test whose expected value
came from running the code pins the bug in place.

Include the boundary the questionnaire surfaced: the surtax threshold, the
recapture entry point, the wage cap that changed in a particular year. And add
the template-level check — `test_tax_law_fetcher.py` walks every state template
and fails if one provokes a shape complaint about its own canonical data, which
is what stops the next state repeating a false positive that has now happened
four times.

## Gate 5 — Honesty surfaces

The module is not done when it computes. State plainly, in the capability note
and in `meta.limitations`, what it does NOT do: the forms not modelled, the
local taxes gated out, the residency cases refused. Those strings reach page 1
of the generated PDF, and they are the difference between a bracket estimate a
user can rely on and one they cannot tell apart from a full return.

Playbook step 7 and its "Definition of done" close this out.

## Non-interactive use

This is contributor work, not a taxpayer run: decisions belong in the spec file
under `docs/superpowers/specs/`, never in `answers.md`, which is taxpayer
workspace. In a headless run, if a questionnaire answer is not already recorded
in the spec, stop and ask — do not infer a state's conformity from a neighbour's.

## Scope note

State support is California-first by product decision (2026-07-12). Contributing
a state is welcome and well-scoped; changing that ordering is not a code
decision. If a state module would require reworking the shared engine rather
than adding to it, raise that before building.
