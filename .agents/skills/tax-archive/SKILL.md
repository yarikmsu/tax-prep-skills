---
name: tax-archive
description: Archive current year's tax files into archive/{year}/ before starting a new year
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

### 1. Input validation

**SECURITY:** Validate that `{year}` is a 4-digit integer (2000–2099) before using in any shell command. Reject non-numeric input.

### 2. Run the archive script

```bash
python3 -m agents.lib.archive {year}
```

The script:
- Refuses if `archive/{year}/` already exists
- Refuses if root `result.json` is missing or `meta.tax_year` doesn't match
- Moves all listed files atomically (rolls back on failure)
- Sets `chmod 600` on every archived file

### 3. Print summary

Show the user which files were moved and which were skipped (not present). Remind them:

> Run `/tax-setup {year + 1}` next to initialize the workspace for the new year.

## Notes
- This is the **prerequisite** for `/tax-rollover` — without an archive, rollover has nothing to read.
- Archiving is non-destructive: files are moved, not deleted. If anything goes wrong before `/tax-setup`, the user can `mv archive/{year}/* .` to restore.
