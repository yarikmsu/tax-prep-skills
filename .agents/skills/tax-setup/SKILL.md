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

### 0. Which directory is the workspace

```
sh agents/lib/run.sh -m agents.lib.workspace
```

It prints the directory every command in this toolkit will read and write, and
where that answer came from. **If it prints the repository checkout, say so to
the user before creating anything**: a return kept inside the clone is one
`git add -A` away from being committed, and the `.gitignore`, the installer's
ignore list and the pre-commit PII scan are three backstops for a decision that
`TAX_WORKSPACE` settles instead.

```
export TAX_WORKSPACE=~/tax/{year}    # any directory outside the checkout
```

Keep the SHELL in the checkout — `sh agents/lib/run.sh` is a relative path and
the engine's venv lives there. The variable moves the data, never the code.
Unset, everything behaves as it always did and the workspace is the current
directory.

### 0.1. Refuse if prior-year files exist at root

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
WS="$(sh agents/lib/run.sh -m agents.lib.workspace --path)" || exit 1
for d in "$WS/documents" "$WS/summaries"; do
  if [ -L "$d" ]; then
    echo "$d is a symlink — refusing to secure through it. Remove or replace it with a real directory, then re-run /tax-setup." >&2
    exit 1
  fi
  mkdir -p "$d" && sh agents/lib/run.sh -m agents.lib.secure_fs "$d" || exit 1
done
```

`$WS` is what makes this the WORKSPACE's directories rather than the checkout's:
the shell stays in the clone, so a bare `mkdir documents` creates it beside the
source. `secure_fs` resolves a relative name against the workspace by itself —
the two would otherwise disagree about which `documents/` they meant, which is
worse than either being wrong alone.

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

Create `taxpayer_profile.json` from the interview answers. Use the template at `agents/templates/taxpayer_profile_template.json` as the structure. Populate all fields from interview. The capability gate's required questions ship `null`, and each must end `true` or `false`: a question its condition skipped is answered `false`, and a null one blocks at `/tax-compute` Step 0.7.

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
sh agents/lib/run.sh -m agents.lib.secure_fs taxpayer_profile.json
```

### 4. Fetch Current Tax Law

For a validated `state_filings.NY` IT203 plan, fetch NY as the selected filing
state even if the mailing address is elsewhere. Seed/replace `state` with
`create_state_template("NY", tax_year=year)` through the fetcher's `set` command,
then fetch its complete annual schema. Do not fetch another state's block over
it; those states' returns keep their capability gates and require separate work.

Seed the cache first, then write every fetched figure through the ingest
command — never by editing the JSON:

```bash
sh agents/lib/run.sh agents/lib/tax_law_fetcher.py create {year}
sh agents/lib/run.sh agents/lib/tax_law_fetcher.py set <dotted.path> '<value>' --source '<authoritative URL>' --year {year}
```

`set` validates the path and value against the schema, refuses a
non-authoritative source, pins it under `_sources` and dates the block under
`_provenance`; a refusal is a misread figure to fix, not a file to open.

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
  - Remove `state_planning_inputs` until a reviewed state scenario worksheet
    is requested; its null is not a completed return or evidence of conformity.
  - Remove `social_security_worksheet` when no SSA/RRB benefits apply; otherwise
    retain its unconfirmed placeholders until `/tax-compute` records the evidence.
  - Validated NY IT203 plan: set `meta.ny_return_type: IT203`, remove
    `new_york_it201`, retain `new_york_it203`. Otherwise remove `new_york_it203`.
    Never retain both computed NY variants or initialize an unsupported state's
    zero-tax declaration merely because that state receives the taxpayer's mail.

- Copy `agents/templates/answers_template.md` → `answers.md`
  - Replace all `{{PLACEHOLDER}}` values with actual taxpayer data

After creating both files, set restrictive permissions:
```bash
sh agents/lib/run.sh -m agents.lib.secure_fs result.json answers.md
```

### 6. Check Dependencies

Run via Bash:
```bash
sh agents/lib/run.sh -c "import pdfplumber; import openpyxl; import reportlab; import defusedxml; print('All dependencies installed')"
```

If any import fails, create a virtual environment and install pinned dependencies:
```bash
python3 -m venv .venv && sh agents/lib/run.sh -m pip install -r requirements.txt
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
sh agents/lib/run.sh -m agents.lib.tax_calendar \
 --profile taxpayer_profile.json \
 --year {year}
```

`write_calendar` already restricts the output through `agents.lib.secure_fs`.
The dates it writes are the OBSERVED ones: a deadline landing on a weekend or a
DC legal holiday rolls to the next business day (§7503) and the `.ics` carries
the rolled date, with the statutory one kept in the event description. So do
not cross-check a rolled date back to the statutory one and do not repeat the
statutory date as the deadline — `/tax-calendar`'s own caveats list the two
kinds of date that deliberately do not roll.

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
