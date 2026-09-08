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
| `run.py` | Dispatches an AGENT against a fresh fixture workspace and scores what it produced. |

## Running it

The whole thing, with a model:

```bash
sh agents/lib/run.sh -m evals.run --agent claude --case ca_mfj_2026 \
    --workspace ./eval-out --repeat 3
```

That builds a fresh workspace per repeat, dispatches the agent into it, scores
`result.json` against the golden, and writes `eval-out/eval_run.json`. It exits 0
only when every repeat matched. **It runs locally, on your own credentials** —
see "Where the model-driven runs happen" below.

Or the halves separately, when you want to drive the agent yourself:

```bash
sh agents/lib/run.sh -m evals.fixture --dest /tmp/eval-ws     # build the workspace
# ...run /tax-parse and /tax-compute against /tmp/eval-ws...
sh agents/lib/run.sh -m evals.compare --result /tmp/eval-ws/result.json
```

`compare` exits 0 on a match and 2 on any difference, so it drops into a script.

## What a run records, and what it never prints

`eval_run.json` carries the agent's version, a DIGEST of `.agents/skills/` (the
instructions under test), a digest of `agents/lib/` (the engine), the repeat
count, the per-run timings and the scored comparison. An eval number is a claim
about a PAIR — this model, these instructions — and one recorded without both is
unattributable a month later; the digests are digests rather than version strings
for the reason `receipt.engine_digest` gives, which is that a constant nobody
bumps reads fresh while describing something that has moved.

The agent's own output is never printed. It ran inside a workspace holding a
return, so its transcript may quote any of it; the runner keeps the exit status,
the timings, and a redacted TAIL for a run that failed before producing anything —
enough to tell "the agent crashed" from "the agent computed the wrong number",
which is the only thing the transcript was needed for.

Five outcomes, and they are not the same failure:

| | |
|---|---|
| `MATCH` | the result is the golden, within tolerance |
| `DIFFERS` | a return was produced and it is wrong — see the comparison |
| `NO_RESULT` | the agent finished and wrote no `result.json` at all |
| `UNREADABLE_RESULT` | it wrote one that is not a result object |
| `AGENT_FAILED` / `TIMEOUT` | it never got as far as a claim about the return |

`NO_RESULT` is separated from `DIFFERS` deliberately: the difference between a
wrong return and no return is the whole false-completion question, and a scorer
reporting the second as a hundred MISSING fields would bury it under the first.

The workspace is deleted afterwards unless you pass `--keep`. It holds a return.

## Where the model-driven runs happen, and what it does not enforce

Locally, on demand, on the operator's own credentials — the owner's ruling (Q2,
2026-09-07). A key in this repository's secrets would be readable by every
workflow the repository ever gains, and the runner's output is a scored
comparison of a tax return going into a durable CI log. There is no workflow for
it and a test pins that `ci.yml` stays the only workflow with a trigger.

Three residuals, stated rather than implied, because they are the difference
between what the runner CONTROLS and what it asks for:

- **The agent runs with your environment and your agent settings.** It needs its
  own credentials, so stripping the environment is not an option; the consequence
  is that it sees every variable in your shell and whatever tool allowlists your
  agent already has. Point `--workspace` somewhere with nothing else in it.
- **"Do not access the network" is asked, not enforced.** Nothing here sandboxes
  the agent. If that matters for your run, sandbox it outside.
- **The workspace path is a command-line argument**, so it is visible to other
  users of the machine in `ps`. Do not name a workspace after a taxpayer.

The prompt deliberately does NOT tell the agent that documents are data and that
instruction-like text inside one is never a command. The skills say that;
whether they say it loudly enough is what the hostile-document-text case exists
to measure, and a harness that says it first can only confirm that a warning
works when given.

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

The cases the backlog's V3 entry names beyond the first: a missing and a corrected
document, hostile document text, a stale cache, a context interruption, and a
valid zero-income return.

**Only the last of those is another golden.** The rest are cases whose correct
outcome is a REFUSAL — the tie-out gate blocking a missing document, Step 0.5
stopping on a stale cache, the parse treating instruction-like text as data — and
scoring a refusal against a `result.json` is the wrong shape entirely: `run.py`
reports a missing `result.json` as `NO_RESULT` and exits 2, which for a refusal
case is the CORRECT behaviour being marked as failure.

So the next piece here is an expected-outcome contract per case, not a fifth copy
of this golden: `goldens/<case>/` declaring what the case expects, with today's
golden comparison as one kind among several. Two things will have to move when it
lands, and they are named here so the move is a decision rather than a discovery
— the exit contract (`matched == repeats`) and how much of the agent's output
survives, because a refusal's evidence is the agent's own stop message and the
runner currently keeps twenty redacted lines of it in the record only.

**A case is a WORKSPACE as well as a golden.** `fixture.build_workspace` takes the
case and refuses any but its own, because `--case` reaching only the golden meant
`--case zero_income_2026` would have built this CA MFJ return, scored it against
the other golden, and reported the model wrong for it.

The **golden builder** is the other open piece: `ca_mfj_2026` is filled in by
hand, verified figure by figure against the engine in `tests/test_evals.py`, and
that is affordable exactly once. The reasoning for building it with the second
golden rather than now is at the end of `docs/changelog/epic-V.md`.

Owner rulings that shaped this (2026-09-07, Part 5 of
`docs/roadmap/2026-09-05-staff-agent-review.md`): the first golden is synthetic
rather than a real return's shape; the model-driven runs happen locally on
demand, not nightly in CI, so no API key becomes a repository secret; and the
cross-agent matrix starts at one agent and grows when a second is in use.
