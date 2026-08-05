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

### 1. Input validation

**SECURITY:** Validate `{prior_year}` is a 4-digit integer (2000–2099). Reject non-numeric.

### 2. Compute the diff

Run via Bash:

```bash
python3 -m agents.lib.rollover diff \
  --from archive/{prior_year} \
  --to . \
  --year {current_year} \
  --json
```

Where `{current_year}` is read from `result.json` `meta.tax_year`. Parse the JSON output.

### 3. Present each category and ask the user

For each of the five categories (`carryforwards`, `identity`, `dependents`, `expected_sources`, `foreign_holdings`) that has at least one change in the diff:

1. Render a brief preview of the changes (3–8 lines): show `json_path`, `prior_value`, `current_value`, `action_suggested`, `reason` for each change.
2. Highlight any change with `action_suggested == "ask"` — these are the ones with warnings (filing-status change, age tick crossings, non-zero current values, etc.).
3. Ask the user, one topic at a time — one prompt per category, with the options `Apply all` and `Skip category`. Do not batch several categories into a single question; each category is decided on its own.

**Non-interactive runs:** if this session cannot prompt the user (headless / batch / CI), read the answers from `answers.md` — a prior `## Rollover from TY{prior_year}` block, or an explicit per-category decision recorded there — and hard-stop on any missing REQUIRED answer. Never guess. A guessed filing status, residency or account fact silently produces a wrong return, and a guessed `ask`-flagged category (filing-status change, age tick crossing, non-zero current value, a foreign fund that is no longer held) writes a wrong carryforward into the current year.

The `foreign_holdings` category carries each prior-year PFIC/§988 fund forward so the §1291 base history (the full zero-inclusive `distributions_by_year` and the correct `preceding_holding_years`) is not lost — it always surfaces as `ask`: `preceding_holding_years` is incremented and the per-year event fields (`event_date`, `received_distribution`, `disposed`, `disposition_gain_usd`) are reset, so the preparer MUST confirm each fund is still held and fold THIS year's distribution total into `distributions_by_year` before `/tax-compute` runs §1291. A rolled fund with a QEF/MTM election, or one blocked, stays as recorded.

If every change in a category has `action_suggested == "skip"`, do not ask — just note it in the summary.

### 4. Apply selected categories

Run:

```bash
python3 -m agents.lib.rollover apply \
  --from archive/{prior_year} \
  --to . \
  --year {current_year} \
  --categories <comma-separated user-selected categories>
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
