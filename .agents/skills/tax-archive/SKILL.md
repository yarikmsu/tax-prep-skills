---
name: tax-archive
description: Archive current year's tax files into archive/{year}/ before starting a new year. Use when starting a new tax year or at year-end cutover, before running tax-setup for the following year.
license: Apache-2.0
---

# /tax-archive

Move current-year tax files (`result.json`, `taxpayer_profile.json`, `answers.md`, `parse_ledger.json`, `tax_law_{year}.json`, `summaries/`, `documents/`, `reviews/`) into `archive/{year}/` so the workspace can be initialized for a new tax year.

## Usage
```
/tax-archive <year>
```

## Prerequisites
- `result.json` exists at workspace root with `meta.tax_year == {year}`
- `archive/{year}/` does NOT already exist

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

**SECURITY:** Validate that `{year}` is a 4-digit integer (2000–2099) before using in any shell command. Reject non-numeric input.

### 2. Run the archive script

```bash
sh agents/lib/run.sh -m agents.lib.archive {year}
```

The script:
- Refuses if `archive/{year}/` already exists
- Refuses if root `result.json` is missing or `meta.tax_year` doesn't match
- Moves all listed files atomically (rolls back on failure)
- Restricts every archived file and directory to the current OS identity through
  `agents.lib.secure_fs`

### 3. Print summary

Show the user which files were moved and which were skipped (not present). Remind them:

> Run `/tax-setup {year + 1}` next to initialize the workspace for the new year.

## Notes
- This is the **prerequisite** for `/tax-rollover` — without an archive, rollover has nothing to read.
- Archiving is non-destructive: files are moved, not deleted. If anything goes wrong before `/tax-setup`, the user can `mv archive/{year}/* .` to restore.
