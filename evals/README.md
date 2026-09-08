# `evals/` — the agent-level eval harness (V3)

The suite in `tests/` proves the ENGINE computes correctly. Nothing in it proves
that the agent-mediated path — a model reading `tax-parse` and `tax-compute` and
calling those functions — produces the right return. That path is the largest
remaining correctness risk in the toolkit, and it is untested by construction:
`tax-compute` is Markdown instructing an agent to call 27 functions and assemble
`result.json` by hand.

This directory is where that gets scored.

## The pieces

| | |
|---|---|
| `fixture.py` | Builds a synthetic workspace: W-2 and 1099 PDFs, a profile, and a fixed invented tax law. Nothing in it is a real person, employer or account. |
| `goldens/<case>/golden_result.json` | The known answer for that workspace. |
| `goldens/<case>/derivation.md` | Every figure in it, with the arithmetic — checkable with a pencil. |
| `compare.py` | Scores a computed `result.json` against a golden, with tolerance on money only. |

## Running it

```bash
sh agents/lib/run.sh -m evals.fixture --dest /tmp/eval-ws     # build the workspace
# ...run /tax-parse and /tax-compute against /tmp/eval-ws...
sh agents/lib/run.sh -m evals.compare --result /tmp/eval-ws/result.json
```

`compare` exits 0 on a match and 2 on any difference, so it drops into a script.

## What a difference means

Three classes, each failing differently, and all of them reported:

- **MISMATCH** — a figure moved. The agent computed something the golden says is
  wrong.
- **MISSING** — a field the golden has and the result does not. That is a step
  that never ran, which is the false-completion shape the tie-out gate exists
  for.
- **EXTRA** — a field the golden does not have. This is the one people wave
  through, and it is how a section belonging to another return, or a
  hallucinated schedule, arrives.

Tolerance applies to money only ($1.00, the same as the tie-out gate's, so the
two cannot drift apart on what "the same figure" means). A changed string, a
changed boolean, or a section appearing and disappearing is never rounding.
`--tolerance` refuses a non-finite or negative value: `float("inf")` scored every
wrong figure as a match, and a harness that calls a wrong return right is worse
than no harness.

**Identity differences are reported without their values**, in both the printed
and the `--json` form. `--result` takes any file and the golden being synthetic
constrains nothing about what is handed to it: pointed at a real return, a name
or an address mismatch would otherwise print the taxpayer's own details to the
terminal. The path still says which field moved, which is what a reader acts on.

Three rounds of that suppression were lists of field names, and each list missed
the next field: `employer_ein`, then the Schedule D row's `source`, which holds
the broker. **What prints is decided by which SIDE the value came from and by its
TYPE, never by the field's name.** `golden` is a synthetic file checked into this
repository, past the PII gate, swept by a test for identifier shapes — it cannot
disclose a taxpayer, so it prints. `actual` is whatever `--result` was pointed at,
so a STRING from it never prints; a number does, because a figure is the thing a
reader acts on and the report is worthless without one. Under both, every emitted
scalar and path goes through `secure_fs.redact`, which masks an identifier by its
shape.

The residual, stated rather than implied: a string difference shows the golden's
side and not the result's, so a reader who needs the value the agent actually
produced opens `result.json`, which is theirs and owner-only.

## Both files are the initialized template

`tax-setup` Step 5 copies `result_template.json` and fills it in: it removes
inapplicable SECTIONS, not individual lines, and it invents none. So a golden
hand-written line by line fails a run that did everything right, in both
directions at once — every template line it forgot scores as EXTRA and every line
it added that the template has no key for scores as MISSING. Build a new golden
by taking the template, dropping the sections the case cannot reach, and filling
in the rest; `tests/test_evals.py` pins the shape against the template and pins
the dropped sections as an explicit set.

`taxpayer_profile.json` is built from `taxpayer_profile_template.json` for the
same reason and was broken in three ways before it was: `taxpayers.primary` for
the template's top-level `primary`, `address.line1` for `address.street`, and a
`prior_year.agi`/`total_tax` pair the schema has no keys for. A fixture whose
whole claim is "a workspace the pipeline would accept" cannot be in a shape the
pipeline does not write.

**Building the golden is not yet automated, and that is a scope call, not an
oversight.** Every figure in it is re-derived here through the engine functions
`tax-compute` calls, and the arithmetic between the figures is pinned as well —
but the file is still filled in by hand. The five remaining cases below are where
a `build --case` generator pays for itself; the first golden is the one case
where it does not.

## Why the law here is invented

The golden is a known answer for a synthetic case; it is not a check that the tax
law is current — `check_freshness` and the freshness auditor are. Real figures
would make this golden go stale every filing season and be rewritten by whoever
noticed, which is how a golden stops being a known answer. The brackets are round
so the derivation can be checked by hand.

## What is not here yet

The **model-driven runner** — the part that actually dispatches an agent against
the fixture and scores the result — and the cases the backlog's V3 entry names: a
missing and a corrected document, hostile document text, a stale cache, a context
interruption, and a valid zero-income return. Each of those is its own golden.

Owner rulings that shaped this (2026-09-07, Part 5 of
`docs/roadmap/2026-09-05-staff-agent-review.md`): the first golden is synthetic
rather than a real return's shape; the model-driven runs happen locally on
demand, not nightly in CI, so no API key becomes a repository secret; and the
cross-agent matrix starts at one agent and grows when a second is in use.
