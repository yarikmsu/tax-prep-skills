---
name: tax-calendar
description: Generate or regenerate the quarterly reminder .ics calendar from taxpayer_profile.json
---

# /tax-calendar

Generate `tax_calendar_{year}.ics` from `taxpayer_profile.json`. Use this skill
when the calendar needs to be regenerated after a flag change (e.g. opening a
foreign account mid-year, electing CA PTET on a new K-1, turning 73 in the
current year, or correcting an age-flag mistake from initial setup).

The calendar is normally produced by `/tax-setup` step 6.5. Re-running this
skill OVERWRITES `tax_calendar_{year}.ics` in place — UIDs are stable across
runs, so calendar apps that honor RFC 5545 UID semantics (Google Calendar,
Outlook subscriptions) update existing events on re-import. Apple Calendar's
`File → Import` is known to create duplicates even with stable UIDs;
subscribe to the file via URL when possible to avoid this.

## Usage

```
/tax-calendar           # Claude resolves to current calendar year - 1
/tax-calendar {year}    # explicit year
```

Claude must resolve `{year}` before invoking the CLI — the underlying
`agents.lib.tax_calendar` tool requires `--year` explicitly.

## Prerequisites

- `taxpayer_profile.json` exists at the workspace root (created by `/tax-setup`)
- Optional: `tax_law_{year}.json` is **not** required — calendar generation uses
  only statutory dates, not bracketed amounts

## Steps

### 1. Validate year

Validate `{year}` is a 4-digit integer (2000–2099). Default to the current
calendar year minus 1 (e.g. in 2026 → TY2025) when not supplied.

### 2. Confirm profile presence

If `taxpayer_profile.json` is missing or its `tax_year` differs from the
requested year, refuse:

> taxpayer_profile.json missing or year mismatch — run `/tax-setup {year}` first.

### 3. Generate the calendar

Run via Bash:
```bash
python3 -m agents.lib.tax_calendar \
  --profile taxpayer_profile.json \
  --workspace . \
  --year {year}
```

The generator:
- **Always** emits 4 events for 1040-ES quarterly deadlines (Q1: Apr 15,
  Q2: Jun 15, Q3: Sep 15, Q4: Jan 15 of the next year — Q4 lands in
  `{year}+1` per statute, not a typo)
- Emits 2 events for FBAR (statutory Apr 15 and auto-extended Oct 15) when
  `flags.has_foreign_accounts` is true. Dates are in `{year}+1` because FBAR
  reports the prior calendar year
- Emits Dec 31 RMD reminders for primary and/or spouse when the matching
  `flags.subject_to_rmd_*` is true (a user-asserted boolean; RMD start age
  is birth-year-cohort-dependent under SECURE 2.0 — see `/tax-setup` step
  for the cohort rules)
- Emits CA / NY / NJ PTET windows for each state in `flags.k1_states`
  when `flags.has_k1_income` is true. PTET is **entity-state-specific**
  (the partnership / S-corp files in its operating state, not the
  partner's resident state), so reminders are driven off the K-1 state
  list — not the taxpayer's mailing address:
  - CA: June 15 prepayment + March 15 (next year) balance/election
  - NY: March 15 (in-year) election deadline
  - NJ: March 15 (following year) BAIT election with PTE-100
  - Non-modeled states (everything other than CA / NY / NJ) get a
    visible "PTET not modeled for state X" info-event per state
  - If `has_k1_income` is true but `k1_states` is empty/missing, a
    "K-1 entity states not specified" guidance note is emitted instead
    of silently dropping PTET reminders

### 4. Report

Print:
- Path to generated `tax_calendar_{year}.ics`
- Count of events by category (e.g. "4 federal_estimated_tax, 2 fbar, 1 rmd")
- Reminder to subscribe in calendar app (Google Calendar: Settings → Add
  calendar → From URL or file; Apple Calendar: File → Import; Outlook:
  Calendar → Add → From file)

## Caveats

- **Statutory dates only.** When April 15 lands on a weekend or DC Emancipation
  Day, the IRS rolls the deadline forward. v1 surfaces the statutory date —
  cross-check IRS.gov for the moved date when relevant
- **First-year RMD deferral not modeled.** The IRS lets you defer the first
  RMD to April 1 of the year following the year you turn 73, but bunching two
  RMDs into one year usually costs more in tax. v1 uses Dec 31 as the safe
  default
- **PTET coverage is CA / NY / NJ only.** Other states (e.g. CT, IL, MA, NC,
  OR) have varying or no PTET regimes; the v1 backlog scope targets the three
  highest-leverage states for SALT-cap workarounds
- **No state estimated-tax deadlines.** State 1040-ES windows mostly match the
  federal Q1–Q4 schedule; the embedded note in each federal event reminds the
  taxpayer to make matching state payments
