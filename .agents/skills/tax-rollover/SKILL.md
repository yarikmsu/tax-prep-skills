---
name: tax-rollover
description: Pull prior-year carryforwards, identity, dependents (age-ticked), recurring payers, and foreign-holding (PFIC/§988) history from archive/{year}/ into the current-year workspace. Use when starting a new tax year and archive/{year-1}/ exists, or the user mentions carryforwards, prior-year data, or last year's return.
license: Apache-2.0
---

# /tax-rollover

Roll forward data from a prior year's archived return into the current-year workspace. Eliminates manual re-typing of carryforward dollar amounts.

## Usage
```
/tax-rollover <prior_year>
```

Example: after `/tax-setup 2025` for TY2025, run `/tax-rollover 2024` to pull from `archive/2024/`.

## Prerequisites
- `archive/{prior_year}/result.json` exists with matching `meta.tax_year`
- Current-year workspace initialized (`result.json` + `taxpayer_profile.json` at root with `meta.tax_year > prior_year`)

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

### 1. Input validation

**SECURITY:** Validate `{prior_year}` is a 4-digit integer (2000–2099). Reject non-numeric.

### 2. Compute the diff

Run via Bash:

```bash
sh agents/lib/run.sh -m agents.lib.rollover diff \
  --from archive/{prior_year} \
  --to . \
  --year {current_year} \
  --json
```

Where `{current_year}` is read from `result.json` `meta.tax_year`. Parse the JSON output.

### 3. Present each category and ask the user

For each of the five categories (`carryforwards`, `identity`, `dependents`, `expected_sources`, `foreign_holdings`) that has at least one change in the diff:

1. Render a brief preview of the changes (3–8 lines): show `json_path`, `prior_value`, `current_value`, `action_suggested`, `reason` for each change.
2. Highlight any change with `action_suggested == "ask"` — these are the ones with warnings (filing-status change, age tick crossings, non-zero current values, etc.). The capital-loss carryforward asks in four shapes, each with its reason in the change: a combined lower bound on the legacy `prior_year.capital_loss_carryforward` when the archive has no Capital Loss Carryover Worksheet block, OR has one whose total sits outside the bounds line 16 implies (a contradiction — the block exists and is wrong), in either case to be resolved from the prior return's worksheet before `/tax-compute`; an ask to zero a split field that sits beside that bound (it would let `/tax-compute` run past its STOP); an ask on every split write after an MFJ → anything change (a joint carryforward is allocated between the spouses, Reg. §1.1212-1(c), and a decedent's ends, Rev. Rul. 74-175); and an ask to retire a figure the archive shows no longer carries. A consistent block on a same-status return rolls straight into `capital_loss_carryforward_short_term` / `_long_term`.
3. Ask the user, one topic at a time — one prompt per category, with the options `Apply all` and `Skip category`. Do not batch several categories into a single question; each category is decided on its own.

**Non-interactive runs:** if this session cannot prompt the user (headless / batch / CI), read the answers from `answers.md` — a prior `## Rollover from TY{prior_year}` block, or an explicit per-category decision recorded there — and hard-stop on any missing REQUIRED answer. Never guess. A guessed filing status, residency or account fact silently produces a wrong return, and a guessed `ask`-flagged category (filing-status change, age tick crossing, non-zero current value, a foreign fund that is no longer held) writes a wrong carryforward into the current year.

The `foreign_holdings` category carries each prior-year PFIC/§988 fund forward so the §1291 base history (the full zero-inclusive `distributions_by_year` and the correct `preceding_holding_years`) is not lost — it always surfaces as `ask`: `preceding_holding_years` is incremented and the per-year event fields (`event_date`, `received_distribution`, `disposed`, `disposition_gain_usd`) are reset, so the preparer MUST confirm each fund is still held and fold THIS year's distribution total into `distributions_by_year` before `/tax-compute` runs §1291. A rolled fund with a QEF/MTM election, or one blocked, stays as recorded.

If every change in a category has `action_suggested == "skip"`, do not ask — just note it in the summary.

### 4. Apply selected categories

Run:

```bash
sh agents/lib/run.sh -m agents.lib.rollover apply \
  --from archive/{prior_year} \
  --to . \
  --year {current_year} \
  --categories "{comma-separated user-selected categories}"
```

If the user selected no categories, skip this step and print *"No changes applied."*

### 5. Append audit block to `answers.md`

```markdown
## Rollover from TY{prior_year} — applied {today}
Categories applied: {comma-separated list}
Fields updated: {count}
Warnings:
- {warning 1}
- {warning 2}
```

Do **not** log specific field values (SSN, dollar amounts) into `answers.md` — only counts and warnings.

Then re-restrict it. Step 4's writes go through `agents.lib.rollover`, which
restricts what it writes itself; this block is written by YOU, and an editor
that REPLACES a file rather than editing it in place leaves the replacement at
the default mode — owner-only is not inherited from the file it overwrote.
`secure_fs`, not a bare `chmod`: under a POSIX-compatible shell on Windows
`chmod` only flips the read-only DOS attribute and leaves the inherited NTFS
ACL readable by every other local account.

```bash
sh agents/lib/run.sh -m agents.lib.secure_fs answers.md || exit 1
```

### 5.5. Surface the prior-year review checklist

If `archive/{prior_year}/reviews/{prior_year}-review.md` exists (the workpaper
`/tax-review` persists), read its **"Tax Planning for Next Year"** section and
print it as this year's starting checklist — those recommendations were made
FOR the year now being set up. Note in `answers.md` that the checklist was
surfaced (item count only, no dollar figures). If the file doesn't exist
(review never run, or pre-C3 archive), skip silently — this is additive.

### 6. Print final summary

Show:
- Categories applied and skipped
- Number of fields updated
- Warnings (aged-out dependents, deferred carryforward reminders, etc.)
- Reminder for deferred carryforwards (NOL, charitable carryover, prior_overpayment_applied — AMT credit and Form 8606 basis are now auto-rolled):

  > If you had NOL, 5-year charitable carryover, or prior-year overpayment applied to estimates in TY{prior_year}, enter manually in the `prior_year` section of taxpayer_profile.json — the toolkit doesn't track these yet. (Form 8606 IRA basis and the Form 8801 AMT credit ARE auto-rolled — don't double-enter them.)

## Notes
- Re-runnable safely — diff against existing values surfaces conflicts as `action_suggested: ask`.
- Reads `tax_law_{current_year}.json` to determine CTC age threshold for dependent age tick. If missing, defaults to 17.
