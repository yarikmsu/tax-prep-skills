---
name: tax-setup
description: Initialize workspace for a new tax year — interview taxpayer, fetch current tax law, create config files. Use when starting a tax year from scratch, when taxpayer_profile.json or tax_law_<year>.json is missing, or the user mentions setting up, initializing, or beginning a return.
license: Apache-2.0
---

# /tax-setup

Initialize a tax preparation workspace for a new tax year.

## Usage
```
/tax-setup <year>
```
If no year is provided, default to the current calendar year minus 1 (e.g., in 2026 → TY2025).

**SECURITY:** Validate that `{year}` is a 4-digit integer (2000–2099) before using in any shell command or file path. Reject non-numeric input.

## Steps

### 0. Refuse if prior-year files exist at root

Before doing anything else, check if there's an existing `result.json` at the workspace root. If yes, read its `meta.tax_year`:

- If `meta.tax_year == {requested year}`: the workspace is already initialized for this year — ask the user whether to overwrite (the existing behavior for re-setup).
- If `meta.tax_year != {requested year}`: refuse with:

  > Prior-year files detected at root (TY{X}). Run `/tax-archive {X}` first, then re-run `/tax-setup {requested year}`.

  Do **not** proceed past this point. This refusal exists to prevent accidentally losing the prior year's data — `/tax-rollover` later depends on archived files being present.

### 1. Create Directory Structure

Ensure these directories exist in the workspace:
- `documents/` — source tax documents (PDFs, XLSX, CSV)
- `summaries/` — parsed document summaries
- `agents/` — if not already present (should exist from install)

**Create `documents/` and `summaries/` at owner-only permissions, not at the
shell's default mode.** Both hold taxpayer data from their first write:
`documents/` the scanned source documents themselves, and `summaries/` the
financial data `/tax-parse` extracts from them — well before `/tax-compute`'s
tie-out gate ever runs and secures it. Restricting the DIRECTORY is not
redundant with the 600 on each file inside: an owner-only file in a listable
directory still discloses its NAME, and under `documents/` those names say what
each document is. Refuse first if either path is already a symlink — a
bare `chmod` follows it to whatever it points at (possibly outside the
workspace), and `mkdir -p` treats a symlink to a directory as already satisfied,
so neither command on its own would catch it. Use `agents/lib/secure_fs.py`,
not a bare `chmod 700`: on Windows `chmod` only flips the read-only DOS
attribute and leaves the underlying NTFS permissions untouched, which is exactly
the gap that module closes.

```bash
for d in documents summaries; do
  if [ -L "$d" ]; then
    echo "$d is a symlink — refusing to secure through it. Remove or replace it with a real directory, then re-run /tax-setup." >&2
    exit 1
  fi
  mkdir -p "$d" && python3 -m agents.lib.secure_fs "$d" || exit 1
done
```

A non-zero exit here is a refusal, same as Step 0's above — do not proceed past
it until the user has replaced the offending path with a real directory. The
`[ -L ]` test is only the first line of defence: it is POSIX-only and reads
false for an NTFS junction, so on Windows the refusal that actually fires is
`secure_fs`'s own — it checks for both and exits non-zero rather than applying
permissions to whatever the junction points at.

### 2. Interview the Taxpayer

Ask the user, one topic at a time, to collect taxpayer information. Ask in logical groups:

**Non-interactive runs:** if this session cannot prompt the user (headless /
batch / CI execution), read the answers from `answers.md` instead and hard-stop
on any missing REQUIRED answer — never guess. A guessed filing status,
residency or account fact silently produces a wrong return. Record every answer
you do obtain back into `answers.md` so a later non-interactive run can reuse it.

The question groups themselves — filing basics, life events, address, dependents,
tax-situation flags, foreign currencies, income estimates, retirement, deductions
and prior-year carryforwards — are in
[references/interview.md](references/interview.md). Read it now and work through
it in order; the non-interactive rule above applies to every question in it.

### 9.5. Auto-invoke /tax-rollover if archive exists

After the carryforward questions in Group 9, check whether `archive/{year - 1}/` exists. If yes:

> Detected prior-year archive at `archive/{year - 1}/`. Running `/tax-rollover {year - 1}` to pre-fill carryforwards, identity, dependents (with age tick), and recurring payer list.

Invoke `/tax-rollover {year - 1}` inline. The rollover skill asks the user its own questions, one category at a time (and falls back to `answers.md` in a non-interactive run).

After rollover completes, treat the Group 9 carryforward questions as "verify these pre-filled values" rather than "type these from memory."

If `archive/{year - 1}/` does not exist, skip silently — fall back to manual carryforward entry from Group 9.

**Group 10 — Estimated taxes** (the last interview group; it sits HERE rather
than in `references/interview.md` because the rollover above pre-fills the
prior-year overpayment it asks about)**:**
- Federal estimated tax payments made? Amounts and dates (1040-ES)?
- State estimated tax payments made? Amounts and dates?
- Prior year overpayment applied to this year?

### 3. Save Taxpayer Profile

Create `taxpayer_profile.json` from the interview answers. Use the template at `agents/templates/taxpayer_profile_template.json` as the structure. Populate all fields from interview.

**Input validation before saving:**
- State: must be a 2-letter US state code (e.g., CA, NY, TX)
- ssn_last4: must be exactly 4 digits — reject if user provides more
- Names: letters, hyphens, apostrophes, spaces only — reject shell metacharacters (`$`, `` ` ``, `!`, `;`, `|`, `&`)
- ZIP: 5 or 9 digits only

**After saving**, set restrictive permissions — use `secure_fs`, and
not a bare `chmod 600`, for the same reason Step 1 does: under a
POSIX-compatible shell on Windows, `chmod` only flips the read-only DOS
attribute and leaves the file readable by every other local account.
```bash
python3 -m agents.lib.secure_fs taxpayer_profile.json
```

### 4. Fetch Current Tax Law

To fetch tax rates for the specified year from authoritative sources, search the web (live results required — tax figures change annually; if your agent's web access is unavailable or cached-only, STOP and ask the user to paste the current figures with their source URL):

The per-figure search list (federal, state, exchange rates, new-legislation
check), the cache-write schema and the `_sources` pinning rules are in
[references/tax-law-fetch.md](references/tax-law-fetch.md). Read it now. The
STOP rule above binds every fetch in it.

### 5. Initialize Result Files

- Copy `agents/templates/result_template.json` → `result.json`
  - Populate `meta` section (year, filing status, standard deduction from fetched rates)
  - Populate `taxpayers` section from profile
  - Populate `dependents` from profile
  - **Remove sections that don't apply** (e.g., remove `schedule_c` if no self-employment)

- Copy `agents/templates/answers_template.md` → `answers.md`
  - Replace all `{{PLACEHOLDER}}` values with actual taxpayer data

After creating both files, set restrictive permissions:
```bash
python3 -m agents.lib.secure_fs result.json answers.md
```

### 6. Check Dependencies

Run via Bash:
```bash
python3 -c "import pdfplumber; import openpyxl; import reportlab; import defusedxml; print('All dependencies installed')"
```

If any import fails, create a virtual environment and install pinned dependencies:
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

⚠️ **Do NOT use `--break-system-packages`** — always use a virtual environment to isolate dependencies.

### 6.5. Generate quarterly reminder calendar

Generate `tax_calendar_{year}.ics` so the taxpayer can subscribe to upcoming
deadlines (1040-ES quarterly payments, CA 540-ES installments for CA residents
per `address.state` — 30%/40%/0%/30% weighting with NO September payment,
FBAR statutory + auto-extended due dates, RMD Dec 31 reminders for filers
flagged `subject_to_rmd_primary` or `subject_to_rmd_spouse`, and CA/NY/NJ
PTET windows for K-1 recipients driven by `flags.k1_states`). The generator
reads `taxpayer_profile.json` and emits only the categories applicable per
the flags.

Run via Bash:
```bash
python3 -m agents.lib.tax_calendar \
  --profile taxpayer_profile.json \
  --workspace . \
  --year {year}
```

`write_calendar` already restricts the output through `agents.lib.secure_fs`.
v1 uses statutory dates only — when April 15 lands on a weekend or DC
Emancipation Day, the IRS rolls the date forward; cross-check IRS.gov for the
authoritative deadline.

Users can subscribe by importing the `.ics` into Google Calendar / Apple
Calendar / Outlook. A 7-day-prior reminder is embedded per event. The skill
`/tax-calendar` can regenerate this file whenever flags change (e.g. after a
mid-year foreign-account opening).

### 7. Summary

Print a summary of what was created:
- Taxpayer profile saved
- Tax law data fetched for {year}
- result.json initialized with {N} applicable sections
- answers.md initialized
- tax_calendar_{year}.ics generated (subscribe in calendar app for deadline reminders)
- Dependencies verified
- Ready for `/tax-parse` — place source documents in `documents/` first

## Notes
- **SECURITY: Store ONLY the last 4 digits of SSN in taxpayer_profile.json. Never store full 9-digit SSNs in any file.** The `ssn_last4` field name in the template enforces this.
- taxpayer_profile.json is listed in .gitignore (created by install.sh) to prevent accidental commits
- If taxpayer_profile.json already exists, ask before overwriting
- If tax_law_{year}.json already exists, ask if they want to re-fetch
