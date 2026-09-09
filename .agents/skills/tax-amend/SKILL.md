---
name: tax-amend
description: Amend a filed return — snapshot the as-filed state, apply corrected documents through the normal parse/compute path, emit the 1040-X three-column summary. Use when a return has already been filed and a corrected or late document arrives, or the user mentions amending, Form 1040-X, or a corrected 1099 or W-2.
license: Apache-2.0
---

# /tax-amend — Amended return (Form 1040-X)

A corrected 1099 arrives after filing; the lifecycle must not jump from
verify → archive. This skill snapshots the return AS FILED, pushes the
corrections through the SAME parse/compute pipeline (no side math), and
renders the 1040-X three-column comparison with Part II explanations
computed from the parse-ledger diff.

## Usage
```
/tax-amend {year}
```

## Prerequisites
- A computed `result.json` for `{year}` that the taxpayer CONFIRMS matches
  the return actually filed (ask explicitly — if the filed return came from
  tax software with differences, this comparison baseline is wrong and the
  1040-X column A must come from the filed copy instead).
- The corrected/new document(s), placed in `documents/`.

## Steps

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

### 1. Snapshot the as-filed state (once, before ANY correction)

Create and restrict the snapshot directory BEFORE copying anything into it —
`archive/` entry names describe a taxpayer's documents, so a listable directory
discloses them even when every file inside is owner-only:

```bash
mkdir -p archive/{year}/as_filed
sh agents/lib/run.sh -m agents.lib.secure_fs archive archive/{year} archive/{year}/as_filed || exit 1
```

Copy `result.json`, `taxpayer_profile.json`, `parse_ledger.json`,
`answers.md`, and `summaries/` into `archive/{year}/as_filed/`. Then restrict
every copy — `secure_fs`, not a bare `chmod -R`: under a POSIX-compatible shell
on Windows `chmod` only flips the read-only DOS attribute and leaves the whole
as-filed snapshot readable by every other local account. It applies owner-only
to each path, files and directories alike, so no separate mode split is needed:

```bash
find archive/{year}/as_filed -print0 \
  | xargs -0 sh agents/lib/run.sh -m agents.lib.secure_fs || exit 1
```

- **If `archive/{year}/as_filed/` already exists, REFUSE to overwrite** —
  it is the immutable column-A baseline. A second amendment amends the
  FIRST amended return: the existing snapshot stays, and you tell the user
  the comparison will run against it (v1 models one live amendment; a
  chain needs manual bookkeeping in answers.md).
- Record in `answers.md`: date snapshotted, the user's confirmation that
  result.json matches the filed return, and which document(s) triggered
  the amendment. Then re-restrict it — an editor that REPLACES the file rather
  than editing it in place leaves the replacement at the default mode, and
  owner-only is not inherited from the file it overwrote:
  ```bash
  sh agents/lib/run.sh -m agents.lib.secure_fs answers.md || exit 1
  ```
  Every later `answers.md` write in this skill (Step 2.5's scan line, Step 5's
  amendment record) ends with the same command.

### 2. Timing checks (before doing the work)

- **Refund-claim deadline:** a 1040-X claiming a refund must generally be
  filed within 3 years of the original filing date (or 2 years of payment,
  if later) — §6511. Past that, an amendment can only cost money; say so.
- **Don't amend for math errors or missing forms the IRS asked about** —
  the IRS corrects math errors and requests missing forms by letter; amend
  for CHANGED amounts, status, dependents, or elections.
- If the original return's refund hasn't been received yet, tell the user
  to WAIT for it before filing the 1040-X (IRS guidance — amending mid-
  processing tangles both).

### 2.5. TY2025 Schedule 1-A misplacement scan (OBBBA, run for any TY2025 return)

TY2025 returns prepared before 2026-07 were computed while `/tax-compute`
carried the pre-fix instruction that treated OBBBA tips / overtime / car-loan
interest as **above-the-line adjustments inside `line_10_adjustments`** — which
wrongly CUT AGI (corrupting every MAGI-gated computation) and applied **no caps
or phase-outs**. The engine was corrected 2026-07 (these are BELOW-AGI Schedule
1-A line-13b deductions). Scan every TY2025 amendment for this:

1. **Detect.** The as-filed `result.json` misplaced these amounts if EITHER:
   (a) `answers.md` records tips/overtime/car-loan claimed as line-10
   adjustments; OR (b) `form_1040.line_10_adjustments` exceeds the sum of the
   legitimate adjustments (½ SE tax, HSA, IRA, SE health, student-loan
   interest, etc.) by roughly the tips/overtime/car-loan figures — and
   `line_13b_schedule_1a_deductions` is $0 while those items exist.
2. **Recompute** under the corrected treatment (the I2 `compute_schedule_1a`
   engine): move the amounts out of line 10 (restoring the correct, HIGHER
   AGI), apply the per-provision caps + MAGI phase-outs, and route the capped
   total to line 13b. Then rerun the normal compute path so every MAGI-gated
   figure (IRA phase-out, taxable SS, NIIT, education credits) re-resolves.
3. **Report the three-column delta** (§6511 timing framing from Step 2). The
   net can go EITHER way: restoring AGI can raise tax (lost the wrong AGI cut)
   while capping an over-cap deduction also raises it, but the corrected
   below-AGI placement can restore MAGI-gated credits that the wrong AGI had
   phased out. Recommend amendment when the net delta is material.
4. **Record** a scan line in `answers.md` (run date + outcome; NO dollar
   figures). If no TY2025 return claimed these provisions, note that the scan
   ran clean — the correction is prophylactic for this taxpayer. Then
   `sh agents/lib/run.sh -m agents.lib.secure_fs answers.md || exit 1`, per Step 1.

(Not applicable to TY2026+ returns — those were computed with the corrected
engine from the start.)

### 3. Apply the corrections through the normal pipeline

1. Place corrected documents in `documents/` (a corrected 1099 usually
   says "CORRECTED" in the header). **Supersede the original, don't stack
   it:** the ledger is one-record-per-FILE, so a corrected document under
   a NEW filename ADDS a record while the original's record stays —
   double-counting that income in the tie-out. Either (a) overwrite the
   original file in `documents/` with the corrected PDF (same filename →
   the re-parse upserts the record), or (b) keep both files but DELETE the
   superseded original from `documents/` before re-parsing (a removed
   source file drops its ledger record). Never leave the original 1099 and
   its CORRECTED replacement both live.
2. Re-run `/tax-parse` (only the changed/new documents need parsing — the
   upsert semantics keep the rest).
3. Re-run `/tax-compute` in full (a changed 1099 moves AGI, which moves
   phase-outs, credits, NIIT, AMT — never hand-adjust single lines).
4. The tie-out gate (Step 12.7) must pass on the corrected return exactly
   as it does on an original.

### 4. Render the 1040-X three-column summary

```bash
sh agents/lib/run.sh -m agents.lib.amend --year {year} \
 --paid-with-original {amount}
```
`--paid-with-original` = tax actually remitted with (or after) the
original return — the balance due the taxpayer paid, NOT withholding or
estimates (those ride lines 12-13), and NOT penalties/interest. $0 for an
original refund return.

The output carries columns A/B/C for the mapped 1040-X lines, the line
16-22 refund-or-owe mechanics, and **Part II explanation bullets computed
from the parse-ledger diff** (added/removed/changed documents with their
category deltas). Turn the bullets into the Part II narrative — concrete
("Received corrected 1099-DIV from X increasing ordinary dividends by
$250"), never "amounts changed".

### 5. Filing the 1040-X

- E-file is available for 1040-X in most software for current + 2 prior
  years; paper otherwise. Attach every form/schedule that CHANGED (use
  `/tax-generate --filing-package` on the corrected return for the
  assembly order) — not the unchanged ones.
- **State:** an AGI-changing federal amendment almost always requires a
  state amendment too. CA: Schedule X with Form 540 — and CA requires
  amending within 6 months of the federal change being final when the IRS
  adjusts (R&TC §18622 reporting duty). Other states not modeled — say so.
- Pay any amount owed WITH the amendment. Interest (§6601) accrues from the
  ORIGINAL due date on the additional tax; the IRS bills the exact figure, but
  you can ESTIMATE it for the taxpayer with
  `compute_underpayment_interest(additional_tax, original_due_date,
  pay_date, federal.underpayment_rates)` (daily-compounded per §6622). It reports
  PARTIAL-through-a-date rather than guessing when the cached rate timeline
  doesn't reach the pay date — extend `/tax-setup`'s rate fetch if so. Estimate
  only; the notice governs. (An amendment that REDUCES tax instead earns
  overpayment interest — not modeled here.) Pass the §7503 OBSERVED original due
  date (roll April 15 past a weekend / DC holiday via
  `irs_dates.next_business_day`), not the statutory date — interest cannot start
  before the observed deadline.
- Add the amendment to `answers.md` (dates, documents, no dollar figures),
  then `sh agents/lib/run.sh -m agents.lib.secure_fs answers.md || exit 1` per Step 1, and
  re-run `/tax-calendar` if any flag changed.

## Caveats
- v1 models ONE live amendment per year (the as-filed snapshot is
  immutable; chains are manual).
- The 1040-X line mapping targets the current form layout; spot-check
  against the filing-year 1040-X.
- Filing-status changes MFJ→MFS are NOT allowed after the due date
  (MFS→MFJ is) — refuse that direction politely.
- Carryforward ripple: an amendment that changes carryforwards
  (capital loss, AMT credit, IRA basis) invalidates any LATER year already
  filed — flag it loudly if `archive/` shows a newer year.
