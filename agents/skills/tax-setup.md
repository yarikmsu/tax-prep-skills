---
name: tax-setup
description: Initialize workspace for a new tax year — interview taxpayer, fetch current tax law, create config files
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

### 2. Interview the Taxpayer

Use **AskUserQuestion** to collect taxpayer information. Ask in logical groups:

**Group 1 — Filing basics:**
- Filing status: Single, Married Filing Jointly (MFJ), Married Filing Separately (MFS), Head of Household (HoH), Qualifying Surviving Spouse (QSS)
- Primary taxpayer: full legal name, SSN (last 4 for display), date of birth, occupation
- If MFJ/MFS: Spouse name, SSN (last 4), DOB, occupation

**Group 2 — Address & state:**
- Street address, city, state, ZIP
- State of residence (for state return)
- Did you move states during THIS tax year? → sets `moved_states_this_year`
  (part-year returns are NOT modeled — the `/tax-compute` capability gate will
  block; tell the user now, not at compute time)
- If state is NY: are you a New York City resident? → sets `nyc_resident`
  (NYC resident tax is NOT computed — same gate)

**Group 3 — Dependents:**
- Number of dependents (0 = skip)
- For each: name, SSN (last 4), DOB, relationship, months lived with taxpayer
- Under 17 at end of tax year? (for CTC eligibility)
- Does any dependent have investment/unearned income over the kiddie-tax
  threshold (~$2,600)? → sets `has_dependent_unearned_income` (Form 8615 is
  NOT modeled — capability gate)

**Group 4 — Tax situations (yes/no flags):**
- Do you have foreign bank/investment accounts? → triggers FBAR/8938 workflow (`has_foreign_accounts`)
- Do you have SIGNATURE AUTHORITY over (but no financial interest in) any foreign account — an employer's, a parent's, a nonprofit's? → FBAR Part IV even with no financial interest (`signature_authority_foreign_accounts`)
- Did you LIVE abroad (tax home outside the US) during the year? → sets
  `lives_abroad` (raises the Form 8938 thresholds; the FEIE / Form 2555
  election is NOT modeled — only the FTC path — so this lands on the
  limitations manifest)
- Do you have self-employment income (1099-NEC, freelance, business)? → triggers Schedule C (`has_self_employment`)
- Do you have rental income? → sets `has_rental_income` (Schedule E is NOT
  modeled — the `/tax-compute` capability gate will block; tell the user now)
- Do you have an HSA (Health Savings Account)?
- Do you have a 401(k) or employer retirement plan?
- Did you make IRA contributions this year?
- Did you have a 401(k) rollover (Form 1099-R)?
- Did you pay foreign taxes?
- Did you receive a state tax refund for the prior year (Form 1099-G)? → may be taxable if you itemized
- Did you employ anyone in your household (nanny, caregiver)? → sets
  `has_household_employees` (Schedule H is NOT modeled — capability gate)

**Group 5 — Foreign currencies (if applicable):**
- Which foreign currencies? (for exchange rate lookup)

**Group 6 — Income estimates:**
- Approximate total W-2 income? (for threshold screening: AMT, NIIT, Medicare surtax)
- Any gig/freelance income (1099-NEC)? Crypto transactions (buy/sell/exchange)?
- Stock compensation: RSU vesting, ISO exercises, ESPP purchases?
  - **If ISOs were EXERCISED (and any shares held past year-end)** → sets
    `has_iso_exercise`. This is load-bearing: AMT (Form 6251) is NOT modeled
    and the `/tax-compute` capability gate will block — the ISO bargain
    element is the most common AMT trigger and can add tens of thousands of
    tax. Warn the user at interview time, not at compute time. (RSU/ESPP alone
    do NOT set this flag — they are ordinary W-2 wage events.)
- K-1 income from partnerships or S-corporations? If yes, ask **which states the entities are based in** (sets `flags.k1_states`, e.g. `["CA", "NY"]`). PTET is an entity-level state tax — the partnership / S-corp files in its operating state, not the partner's resident state — so a TX-resident partner in a CA partnership needs `k1_states: ["CA"]` to get the right PTET reminders in `tax_calendar_{year}.ics`.
- Gambling income (W-2G)? → sets `has_gambling_income` flag

**Group 7 — Retirement & savings detail:**
- IRA type: Traditional, Roth, or both? Any Roth conversion this year?
- Backdoor Roth? (If yes: check pro-rata trap — any pre-tax Traditional IRA balance at year-end?)
- 401(k): Traditional/Roth split? Total deferral amount?
- Age 50-59 or 64+? (catch-up eligibility for 401k/IRA — amounts from `tax_law JSON` `retirement_401k.catch_up_50_plus` and `ira_limits.catch_up_50_plus`)
- Age 60-63? (SECURE 2.0 super catch-up for 401k — amount from `tax_law JSON` `retirement_401k.catch_up_60_63`, which **supersedes** the standard catch-up if both apply)
- **Fetch the SECURE 2.0 §603 high-earner threshold** → `tax_law JSON` `retirement_401k.secure_2_0_high_earner_threshold`: the prior-year FICA-wage threshold above which 401(k) catch-up must be designated Roth (IRC §414(v)(7)(A); $145k indexed from 2023, ≈ $150k for TY2026). It indexes annually — **REQUIRED for TY2027+** (`/tax-retirement`'s built-in $150k default is TY2026-only and hard-errors for later years if this is null). For TY2026 it may be left null (the $150k default applies), but fetching the exact value is preferred.
- Age 55+? (HSA catch-up: amount from `tax_law JSON` `hsa_limits.catch_up_55_plus`)
- Are RMDs (required minimum distributions) currently required from primary or spouse's traditional IRA / 401(k) accounts? (sets `subject_to_rmd_primary` / `subject_to_rmd_spouse` — drives Dec 31 RMD reminders in `tax_calendar_{year}.ics`). RMD start age is birth-year-cohort-dependent under SECURE 2.0 — generally age 73 for those born 1951–1959, age 75 for those born 1960 or later; inherited IRAs trigger RMDs at any age. If unsure, confirm with the taxpayer's preparer.
- Mega backdoor Roth: Does employer plan allow after-tax contributions + in-service conversion?

**Group 8 — Deductions & credits probe:**
- Student loan interest paid? Educator expenses (if teacher)?
- College tuition/education expenses? (AOTC / Lifetime Learning Credit) → sets `has_education_expenses`
- Child/dependent care expenses? (Form 2441) → sets `has_dependent_care`
- Charitable giving? (approximate amount, cash vs property donations) → sets `has_charitable_giving`
- Energy-efficient home improvements? (heat pump, solar, insulation — 25C/25D) → sets `has_energy_credits`
- EV purchase or lease this year? (30D/25E) → sets `has_ev_purchase`
- Marketplace health insurance? (Form 1095-A) → sets `has_marketplace_insurance`
  (Form 8962 APTC reconciliation is NOT modeled — the `/tax-compute` capability
  gate will block; reconciliation is mandatory when advance PTC was paid, so
  warn the user now)

**Group 9 — Prior year carryforwards:**
- Capital loss carryforward from prior year? Amount?
- QBI (qualified business income) loss carryforward?
- Unused foreign tax credit carryforward?
- Net operating loss (NOL) carryforward?

### 9.5. Auto-invoke /tax-rollover if archive exists

After the carryforward questions in Group 9, check whether `archive/{year - 1}/` exists. If yes:

> Detected prior-year archive at `archive/{year - 1}/`. Running `/tax-rollover {year - 1}` to pre-fill carryforwards, identity, dependents (with age tick), and recurring payer list.

Invoke `/tax-rollover {year - 1}` inline. The rollover skill handles its own AskUserQuestion prompts per category.

After rollover completes, treat the Group 9 carryforward questions as "verify these pre-filled values" rather than "type these from memory."

If `archive/{year - 1}/` does not exist, skip silently — fall back to manual carryforward entry from Group 9.

**Group 10 — Estimated taxes:**
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

**After saving**, set restrictive permissions:
```bash
chmod 600 taxpayer_profile.json
```

### 4. Fetch Current Tax Law

Use **WebSearch** to fetch tax rates for the specified year from authoritative sources:

**Federal (IRS.gov):**
- Search: `"IRS {year} tax brackets married filing jointly"`
- Search: `"IRS {year} standard deduction"`
- Search: `"IRS {year} child tax credit amount"` — populate BOTH
  `child_tax_credit.amount_per_child` AND `child_tax_credit.refundable_limit`
  (the per-child ACTC cap, indexed — $1,700 for TY2024/2025; needed for the
  Schedule 8812 refundable split in `/tax-compute`)
- Search: `"IRS {year} section 199A QBI taxable income threshold"` — populate
  `federal.qbi_threshold` (`single`/`mfj`; above it Form 8995-A limits apply and
  `compute_qbi_deduction` flags `over_threshold`)
- Search: `"IRS {year} SALT deduction cap"` — populate `federal.salt_cap`
  (`amount`/`mfs_amount`; OBBBA $40K/$20K-MFS for TY2025-2029 indexed ~1%/yr,
  $10K/$5K-MFS in TY2030 — consumed by `/tax-ptet` and the itemize-vs-standard
  check in `/tax-compute`)
- Search: `"IRS {year} HSA contribution limits"`
- Search: `"IRS {year} 401k contribution limit"`
- Search: `"IRS {year} IRA contribution limit phase out"`
- Search: `"IRS {year} social security wage base"`
- Search: `"IRS {year} capital gains tax brackets"`
- Search: `"Medicare IRMAA {year} income brackets Part B Part D"` — populate `federal.irmaa.tiers`
  (`single`/`mfj` lists of `{magi_threshold, annual_surcharge_per_person}`, annualizing the
  monthly Part B + Part D surcharge above the standard premium). Optional but unlocks IRMAA
  flags in `/tax-roth-convert`; leave as `[]` if not relevant to the taxpayer.
- Search: `"IRS quarterly interest rates underpayment {year}"` (and `{year+1}` Q1-Q2
  when published) — populate `federal.underpayment_rates` as a list of
  `{"effective_from": "YYYY-MM-DD", "annual_rate": 0.08}` covering every §6621
  rate period from Apr 15 of {year} through Apr 15 of {year+1} (the Form 2210
  accrual span — the irs.gov quarterly interest-rate news releases are the
  source). Rates for future quarters aren't published yet at setup time —
  `/tax-compute` re-checks at filing and `compute_form_2210` refuses to guess
  missing periods.

**State (based on taxpayer's state):**
- For CA: Search `"FTB California {year} tax brackets"`, `"California {year} standard deduction"`,
  and `"California SDI rate {year}"` → populate `state.sdi_rate`. Populate
  `state.sdi_wage_limit` ONLY for pre-2024 years (SB 951 removed the SDI wage
  ceiling from TY2024 — leave it null for 2024+, which correctly disables the
  excess-SDI credit in `/tax-compute`)
- For CA: Search `"FTB California {year} itemized deduction phaseout AGI threshold"`
  → populate `state.itemized_phaseout` (`single`/`mfj`/`hoh` AGI thresholds,
  indexed annually — R&TC §17077; FTB publishes them with the exemption
  credits). Unfetched → `compute_ca_itemized` skips the phase-out with a
  loud overstatement note for high-AGI itemizers.
- For NY: Search `"New York {year} income tax brackets"`
- For other states: Search `"{state} {year} income tax rates"`

**Exchange rates (if foreign income):**
- Search: `"IRS yearly average exchange rate {year} {currency}"`

**Check for new tax legislation:**
- Search: `"federal tax law changes {year}"` to catch any mid-year legislation (like OBBBA for 2025)

Save all fetched rates to `tax_law_{year}.json` using the structure from `agents/lib/tax_law_fetcher.py`.

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
chmod 600 result.json answers.md
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
deadlines (1040-ES quarterly payments, FBAR statutory + auto-extended due
dates, RMD Dec 31 reminders for filers flagged `subject_to_rmd_primary` or
`subject_to_rmd_spouse`, and CA/NY/NJ PTET windows for K-1 recipients driven
by `flags.k1_states`). The generator reads `taxpayer_profile.json` and emits
only the categories applicable per the flags.

Run via Bash:
```bash
python3 -m agents.lib.tax_calendar \
  --profile taxpayer_profile.json \
  --workspace . \
  --year {year}
```

`write_calendar` already sets `chmod 600` on the output. v1 uses statutory
dates only — when April 15 lands on a weekend or DC Emancipation Day, the IRS
rolls the date forward; cross-check IRS.gov for the authoritative deadline.

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
