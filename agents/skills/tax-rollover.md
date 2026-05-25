---
name: tax-rollover
description: Pull prior-year carryforwards, identity, dependents (age-ticked), and recurring payers from archive/{year}/ into the current-year workspace
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

### 3. Present each category via AskUserQuestion

For each of the four categories (`carryforwards`, `identity`, `dependents`, `expected_sources`) that has at least one change in the diff:

1. Render a brief preview of the changes (3–8 lines): show `json_path`, `prior_value`, `current_value`, `action_suggested`, `reason` for each change.
2. Highlight any change with `action_suggested == "ask"` — these are the ones with warnings (filing-status change, age tick crossings, non-zero current values, etc.).
3. Use **AskUserQuestion** with options: `Apply all`, `Skip category`.

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

### 6. Print final summary

Show:
- Categories applied and skipped
- Number of fields updated
- Warnings (aged-out dependents, deferred carryforward reminders, etc.)
- Reminder for deferred carryforwards (NOL, AMT credit, charitable carryover, Form 8606 basis, prior_overpayment_applied):

  > If you had NOL, AMT credit, 5-year charitable carryover, Form 8606 non-deductible IRA basis, or prior-year overpayment applied to estimates in TY{prior_year}, enter manually in the `prior_year` section of taxpayer_profile.json — the toolkit doesn't track these yet.

## Notes
- Re-runnable safely — diff against existing values surfaces conflicts as `action_suggested: ask`.
- Reads `tax_law_{current_year}.json` to determine CTC age threshold for dependent age tick. If missing, defaults to 17.
