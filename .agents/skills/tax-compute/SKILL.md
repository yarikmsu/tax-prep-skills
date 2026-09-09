---
name: tax-compute
description: Compute all tax form values from parsed data — federal 1040 + state return. Use when documents have been parsed and the user asks to compute, calculate or finalize the return, or when result.json must be produced or refreshed.
license: Apache-2.0
---

# /tax-compute

Compute all tax form line values from parsed source data.

## Usage
```
/tax-compute            # Compute all forms
/tax-compute --refresh  # Re-fetch tax law data before computing
```

## Prerequisites
- `taxpayer_profile.json` must exist
- `result.json` must have parsed income data (run `/tax-parse` first)
- `tax_law_{year}.json` must exist (run `/tax-setup` first)
- `summaries/*.md` should exist with detailed parsed data

## Gates that run before computation

### Engine preflight (HARD GATE — runs before every other step)

Run: `sh agents/lib/run.sh agents/lib/preflight.py`

- **Exit 0** — the engine is present and usable; continue. If the verdict says
  `OK, with N warning(s)`, show the user those lines first: the engine is fine,
  but something a later step needs is not — a missing package, or a workspace
  with no virtualenv — and it surfaces at first use instead of here.
- **Anything else — including the command failing because the file does not
  exist — STOP.** Do not parse, compute, or emit anything. Show the user the
  command's output and stop there.

The gates and computation this skill names are scripts under `agents/lib/`, a
sibling of `.agents/skills/` that does not travel with a copied skill — which
then still reads as a complete procedure while every script it names is absent.
So a failure to RUN this check IS the blocking condition, never a step to skip.

### Step 0.5: Tax Law Freshness Verification

Before any computation, verify tax law data is current:

1. **Validate** `{year}` is a 4-digit integer (2000–2099) before using in any command.
2. Run: `sh agents/lib/run.sh agents/lib/tax_law_fetcher.py freshness {year}`
3. **Exit 2 means NOT_FOUND — the STOP below.** It is the gates' blocked code,
   spent on the one status whose remedy is a stop; STALE and INCOMPLETE exit 0
   because their remedies are inline and are spelled out here. A 2 with an EMPTY
   report is not this gate at all — it is the launcher refusing (no venv, wrong
   interpreter), so read the JSON before acting on the code. Act on the status,
   which says WHICH figures:
   - **FRESH:** Proceed. Print the key rates being used (standard deduction, CTC amount, top bracket) so user can eyeball them.
   - **STALE:** Search the web (live results required — tax figures change annually; if your agent's web access is unavailable or cached-only, STOP and ask the user to paste the current figures with their source URL) to check for new legislation since the fetch date (`"federal tax law changes {year} since {fetch_date}"`). If new law found, re-fetch affected rates. If no changes, proceed.
   - **INCOMPLETE:** Re-fetch the missing fields listed in `null_fields` by searching the web (same live-results requirement — with no live web access, STOP and ask the user to paste the missing figures with their source URL; never guess a rate or threshold), then update `tax_law_{year}.json`.
   - **NOT_FOUND:** **STOP.** Tell user to run `/tax-setup` first. Do not proceed.
4. Print the rates being used:
   ```
   Tax Law Verification — TY{year}
   Status: {status} (fetched {age_days} days ago)
   MFJ standard deduction: ${amount}
   Child tax credit: ${amount}/child
   Top bracket: {rate}% on income over ${threshold}
   ```

### Step 0.6: Required Answers

Every question this return owes the taxpayer, at once, instead of stopping
mid-compute one at a time. It runs before the capability gate because a
question is cheaper to ask than a computation is to redo.

```
sh agents/lib/run.sh -m agents.lib.answers --workspace . --check-required --for tax-compute
```

- **Exit 0** — nothing owed; continue.
- **Exit 2 — STOP.** Each missing answer is listed by id, with the step it
  would have stopped at. Ask the taxpayer, then record each one in `answers.md`
  as `- **[id]** the answer`, on one line (or with the answer on indented lines
  directly under the marker). The id is what the next run matches on; prose
  alone is not read.
- **Exit 1** — the check could not run. That is the same blocking condition as
  a missing engine: do not proceed on the assumption that nothing was owed.

`--for tax-compute` narrows it to this skill's questions, so a question
`tax-parse` owns cannot block a computation. Drop the flag for the
whole-workspace survey.

**An ACKNOWLEDGEMENT is re-asked every run, by design** — it accepts a known
wrong-direction risk on THIS year's figures, so a recorded one never satisfies
it again. Re-present it, and when the taxpayer accepts it again pass
`--acknowledge <id>` on THIS invocation. It is written nowhere: consent that
was never re-asked is not acceptance.

### Step 0.7: Capability Gate (scope guard)

Before loading any data, verify the engine can actually compute this taxpayer's
situation — the toolkit must never produce a plausible-looking return that
silently omits a form it cannot model:

1. Run: `sh agents/lib/run.sh agents/lib/capabilities.py --workspace .`
2. Act on the exit code:
   - **0:** Proceed. The printed limitations manifest is reused in Step 12.
   - **2 (BLOCKED):** **STOP.** Show the user the blocked situations and the
     two options verbatim from the tool output. Do NOT compute past this point
     unless the user chooses one:
     a. Prepare this return in tax software / with a professional — the toolkit
        stays useful as a checker (`/tax-verify`) and planner; or
     b. The user EXPLICITLY accepts proceeding without the named form(s). Then:
        (1) record the decision in `answers.md` (date, rule id, form, one-line
        user rationale); (2) add the rule id(s) to `acknowledged_limitations`
        in `taxpayer_profile.json`; (3) run
        `sh agents/lib/run.sh -m agents.lib.secure_fs answers.md taxpayer_profile.json || exit 1`
        — an editor that REPLACES a file rather than editing it in place leaves
        the replacement at the default mode, and this branch can END the run
        (option a, or a gate that still blocks), so the securing step in
        `references/finalize.md` may never be reached; (4) re-run the gate and
        confirm exit 0.
   - **1 (error):** Fix the reported profile problem (non-bool flag, unknown
     acknowledged id, malformed state). Never bypass the gate on an error.
3. NEVER add ids to `acknowledged_limitations` without the user's explicit
   confirmation in this conversation — the acknowledgment is the taxpayer's
   decision, not a convenience.

## Computation Order

Order matters — each step depends on previous results. Run the steps in order.
Read each reference file when you reach its steps; do not read them all up front.

| Steps | What they cover | Read |
|---|---|---|
| 1 – 5 | Load inputs, confidence check, applicable forms, income, AGI, taxable income (1040 lines 1-15) | [references/income-and-agi.md](references/income-and-agi.md) |
| 6 – 9.5 | Tax, AMT, credits, other taxes, payments, estimated-tax penalty (1040 lines 16-38) | [references/tax-and-credits.md](references/tax-and-credits.md) |
| 10 | State return | [references/state-return.md](references/state-return.md) |
| 11 – 11.5 | International compliance and the optimization discovery engine | [references/international-and-optimization.md](references/international-and-optimization.md) |
| 12, 13 | Write result files and produce the summary | [references/finalize.md](references/finalize.md) |

Steps 0.5, 0.7, 12.6 and 12.7 are gates and are written out in full in this
file. Never skip one because a reference file did not mention it.

### Form 1040 lines 1-24 are DERIVED — run the builder, do not type them

```
sh agents/lib/run.sh -m agents.lib.return_builder --workspace .
```

Every 1040 line that is ARITHMETIC over other lines — 1z, 9, 11, 14, 15, 16, 17,
18, 21, 22, 24 — plus the six income lines that are a straight sum of the parse
records (1a, 2a, 2b, 3a, 3b, 6a) is computed by that command and written to
`result.json`. **Do not write one of them by hand.** Everything else on lines
1-24 is a COMPONENT you still decide: what is taxable, whether to itemize, what
the credits allow.

It is idempotent and it does not rewrite an unchanged file, so **run it whenever
a group of components is finished** and let Step 12.6 be what proves you
converged. The form's own dependencies make two passes the natural rhythm —
Form 6251 reads 1040 line 15, and Schedule 8812's Credit Limit Worksheet reads
line 18 — so run it at least:

- **After Step 5**, once the deductions are set. Lines 1z, 9, 11, 14, 15 and 16
  come back final — line 16 is the Qualified Dividends and Capital Gain Tax
  Worksheet, so Step 6 is now reading its answer rather than running it, and
  Step 6.5's AMT has the line 15 it needs.
- **After Step 8**, once the credits and other taxes are in. Lines 17, 18, 21,
  22 and 24 come back final.

Between those two passes lines 21-24 hold a figure built from components that
are still zero, and it is not marked as provisional — the template ships every
component at 0 and nothing can tell "decided as zero" from "not decided yet".
Do not quote a total tax to the user until the second pass has run.

- **Exit 0** — built (or already agreed). It prints every line it changed, old
  and new. If it changed one, RE-READ `result.json` before using any figure
  below it: a changed line 16 means the CTC base, the state return and the
  payments section were computed from a number that is no longer there.
- **Exit 2 — STOP.** The return carries something this module does not model
  (today: a computed PFIC fund, whose §1291 reclassification moves amounts out
  of lines 3a and 3b), or the tax-law cache cannot price the return. Read the
  message; do not build lines 1-24 around it.
- **Exit 1** — the check could not run: a missing `result.json`,
  `parse_ledger.json`, profile or cache. Same blocking condition as a missing
  engine — fix the input, never proceed as though the lines were right.

## Gates that run regardless

Both gates write their verdict, and the digest of every file they read, into
`validation_receipt.json` — `/tax-generate --filing-package` refuses to assemble
a package whose gates never ran or ran against other inputs. Nothing here to do:
the tools write it. Do NOT edit that file; re-run the gate instead.

### Step 12.6: Form 1040 lines 1-24 still agree with their components

```
sh agents/lib/run.sh -m agents.lib.return_builder --workspace . --check
```

The same rules, asserting instead of writing. It runs BEFORE the tie-out because
a derived line edited by hand after the build is exactly what the tie-out's
income checks can no longer see on their own: six of its category sums now
PRODUCE the lines they are compared against, so a later edit is the failure mode
that survived deriving them.

- **Exit 0:** every derived line matches. Continue.
- **Exit 2: STOP.** Each disagreeing line is named with both figures. Do not
  reconcile by editing the line — the components are the answer. Fix the
  component that is wrong, or re-run the builder without `--check` if the
  component was right and the line was stale.
- **Exit 1:** could not run — fix the input; do not bypass.

### Step 12.7: Parse-to-return tie-out (HARD GATE)

Prove every parsed document landed on the return — the CP2000 killer:

1. Run: `sh agents/lib/run.sh agents/lib/tie_out.py --workspace .`
2. Act on the exit code:
   - **0:** Tied out. If the report lists ⚠ unverifiable categories or ⚠
     advisory items (a possibly-taxable state refund, a personal-item 1099-K,
     a recomputed 1099-R box 2a, a net-negative SSA-1099), surface them to the
     user — they passed the gate but need a manual look before filing.
   - **2 (FAILED): STOP — the return is NOT complete.** The report
     (`summaries/_tie_out.md`) names the exact failure: a document with no
     ledger record, a ledger record pointing at a missing file, or a category
     sum that doesn't match the return (the classic dropped-1099). Fix the
     aggregation (or the ledger, if the parse was wrong) and re-run. NEVER
     proceed to `/tax-generate` past a failed tie-out, and never "fix" a
     mismatch by editing the ledger to match the return without re-checking
     the source document.
   - **1 (error):** parse_ledger.json is missing (re-run `/tax-parse` step
     6.5) or malformed — fix it; do not bypass.
3. This gate exists because `/tax-review` audits the already-aggregated
   result.json and structurally cannot see a document that dropped during
   aggregation — only the ledger-to-result comparison can.

