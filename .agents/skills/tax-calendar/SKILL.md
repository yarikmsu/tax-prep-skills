---
name: tax-calendar
description: Generate or regenerate the quarterly reminder .ics calendar from taxpayer_profile.json. Use when the user asks about tax deadlines, quarterly estimated-payment reminders, or wants a calendar or .ics export.
license: Apache-2.0
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
/tax-calendar           # resolves to current calendar year - 1
/tax-calendar {year}    # explicit year
```

Resolve `{year}` yourself before invoking the CLI — the underlying
`agents.lib.tax_calendar` tool requires `--year` explicitly.

## Prerequisites

- `taxpayer_profile.json` exists at the workspace root (created by `/tax-setup`)
- Optional: `tax_law_{year}.json` is **not** required — calendar generation uses
  only statutory dates, not bracketed amounts

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
sh agents/lib/run.sh -m agents.lib.tax_calendar \
 --profile taxpayer_profile.json \
 --year {year}
```

The generator:
- **Always** emits 4 events for 1040-ES quarterly deadlines (Q1: Apr 15,
  Q2: Jun 15, Q3: Sep 15, Q4: Jan 15 of the next year — Q4 lands in
  `{year}+1` per statute, not a typo)
- Emits 3 **CA 540-ES** events when `address.state` is `CA` — California's
  installments are NOT four equal quarters: **30% Apr 15, 40% Jun 15,
  NOTHING in September, 30% Jan 15 of the next year** (R&TC §19136.1;
  FTB Form 540-ES). Only three events are emitted; each description calls
  out the September gap so the federal Q3 reminder isn't mistaken for a CA
  deadline, and notes the R&TC §19136.3 high-income rule (CA AGI ≥ $1M /
  $500K MFS → 90%-of-current-year only) and mandatory e-pay (R&TC §19011.5)
- Emits 4 **NY IT-2105** events when `address.state` is `NY` — four **EQUAL**
  quarters (Apr 15, Jun 15, **Sep 15**, Jan 15 of the next year), the federal
  dates and emphatically not California's 30/40/0/30. Each description says
  that **one voucher covers the New York State tax, the New York City resident
  tax, the Yonkers surcharge and the MCTMT together** — a City resident who
  pays the State amount alone on these dates has under-paid — and sends the
  reader to `/tax-withholding` for the §685(c) shares, which are fetched and
  keyed to **New York** AGI rather than federal
- Emits 2 **extension** events when `flags.filed_extension` is true (set by
  `/tax-extend`, never by the interview): April 15 of `{year}+1` (Form 4868 +
  payment due — the extension extends FILING, not PAYMENT; CA residents get
  the FTB 3519 payment-only note) and October 15 of `{year}+1` (extended
  filing deadline)
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
- **FL property deadlines** — ALL flag-driven, independent of the owner's
  `address.state` (Codex round-25 P2 replaced the earlier residence gate):
  - **Homestead DR-501** is **flag-driven** (`fl_new_homestead`,
    `fl_homestead_unfiled`), NOT residence-gated: F.S. 196.031 extends the
    homestead exemption to property maintained as the permanent residence
    of someone legally or naturally DEPENDENT upon the owner, so a
    non-FL-resident owner can hold an eligible FL homestead — a residence
    gate would suppress that owner's real DR-501 deadline. `/tax-setup`
    owns the eligibility screening (including the non-resident
    FL-property elicitation question); the calendar trusts the asserted
    flags.
  - **Tangible personal property DR-405** (Apr 1, `flags.
    fl_business_tangible_property`, no look-ahead — property located in
    Florida on Jan 1 of `{year}`) is **situs-gated, NOT resident-gated**:
    tangible personal property tax is assessed where the assets physically
    sit, so it fires independent of `address.state` — a Georgia resident
    with business OR RENTAL equipment (e.g. furnishings/appliances in a
    Florida rental unit, not just a sole proprietorship's business
    equipment) located in Florida still gets the reminder.
  - **Continuing-filer waiver (F.S. 196.183):** after a timely INITIAL
    DR-405 establishes the $25,000 exemption, subsequent annual returns
    are generally waived while the property's value stays at or below
    $25,000 — unless the county property appraiser requests one. This
    module has no per-taxpayer filing-history/value input, so it cannot
    decide whether a filing is actually owed THIS year; that decision (and
    whether `flags.fl_business_tangible_property` is set at all) is made
    during `/tax-setup`'s interview, which asks whether the initial return
    was already filed, whether value exceeds $25,000, and whether the
    appraiser requested one, and only sets the flag when a filing is
    actually expected. The DR-405 event description itself notes the
    waiver so a taxpayer who does see the reminder isn't told they must
    file every single year regardless.
  - Both are county filings, not DOR — and unlike every other state event
    they do **NOT roll at all**: the event stays ON the statutory March 1 /
    April 1 even when it falls on a weekend (Codex round-19 P2 replaced
    the earlier weekend roll). No governing Florida rollover rule has been
    verified for county property filings — only the observation that most
    county offices accept the next business day — and an alert moved to
    Monday would have a taxpayer in a non-rolling county file late. The
    event description carries a confirmation caveat naming the common
    next-business-day practice without adopting it; never describe Monday
    as the actionable deadline. This flag/situs gating is itself the
    opposite of PTET's gating below, which follows `flags.k1_states`
    (entity states) rather than either residence or physical situs.
  - Homestead DR-501 is due **Mar 1 of `{year}+1`** — like FBAR, the event
    date is a year ahead of the flag year: FL homestead eligibility is
    determined as of January 1 (F.S. 196.011), so a homestead
    `flags.fl_new_homestead` marks as newly established DURING `{year}` is
    first eligible for exemption year `{year}+1` and the DR-501 filing
    deadline is March 1, `{year}+1` (clear the flag after — first year
    only; renewals are automatic in most counties).
  - `flags.fl_homestead_unfiled` covers the gap `fl_new_homestead` misses:
    a homestead already owned/occupied as of January 1 of `{year}` (e.g.
    established late in `{year}-1`) that hasn't yet had its INITIAL DR-501
    filed needs a **CURRENT-year** March 1, `{year}` reminder, not a
    look-ahead one. Distinct UID (`state-property-fl-homestead-initial-
    {year}@…`) so it coexists with `fl_new_homestead`'s event without
    colliding; clear the flag once filed.
  - The weekend-roll note for both homestead events and DR-405 does NOT
    cite §7503 (that IRC section governs federal filing/paying acts, not
    county property-appraiser deadlines) — it says the statutory date falls
    on a weekend and most county offices observe the next business day,
    and tells the taxpayer to confirm with their county property appraiser.

### 4. Report

Print:
- Path to generated `tax_calendar_{year}.ics`
- Count of events by category (e.g. "4 federal_estimated_tax, 2 fbar, 1 rmd")
- Reminder to subscribe in calendar app (Google Calendar: Settings → Add
  calendar → From URL or file; Apple Calendar: File → Import; Outlook:
  Calendar → Add → From file)

## Caveats

- **The `.ics` carries OBSERVED dates, not statutory ones.** A federal deadline
  landing on a weekend or a DC legal holiday rolls to the next business day
  (§7503), and the event date IS the rolled one, with the statutory date named
  in the description. California rolls on the same holiday set (R&TC §18410),
  so its 540-ES and PTET dates match FTB's published ones; NY and NJ dates —
  IT-2105 and PTET alike — roll past weekends only and carry a "confirm with
  the state tax authority" line for that reason, so a New York date may sit a
  day or two before the one the state observes. Do not repeat a statutory date back to the user over a
  rolled one, and do not "correct" the file — the two dates that deliberately
  do NOT roll are the Dec 31 RMD reminder and the FL county property deadlines,
  each for the reason given where it is emitted
- **First-year RMD deferral not modeled.** The IRS lets you defer the first
  RMD to April 1 of the year following the year you turn 73, but bunching two
  RMDs into one year usually costs more in tax. v1 uses Dec 31 as the safe
  default
- **PTET coverage is CA / NY / NJ only.** Other states (e.g. CT, IL, MA, NC,
  OR) have varying or no PTET regimes; the v1 backlog scope targets the three
  highest-leverage states for SALT-cap workarounds
- **State estimated-tax deadlines are CA and NY only.** CA 540-ES events carry
  the 30/40/0/30 weighting and NY IT-2105 events four equal quarters, each for
  residents of that state (`address.state`); other states'
  estimated-payment windows mostly match the federal Q1–Q4 schedule but are
  not emitted — add manual reminders for any other resident state
