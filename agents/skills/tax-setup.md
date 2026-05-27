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
- Any state residency changes during the year?

**Group 3 — Dependents:**
- Number of dependents (0 = skip)
- For each: name, SSN (last 4), DOB, relationship, months lived with taxpayer
- Under 17 at end of tax year? (for CTC eligibility)

**Group 4 — Tax situations (yes/no flags):**
- Do you have foreign bank/investment accounts? → triggers FBAR/8938 workflow
- Do you have self-employment income (1099-NEC, freelance, business)? → triggers Schedule C
- Do you have rental income? → triggers Schedule E
- Do you have an HSA (Health Savings Account)?
- Do you have a 401(k) or employer retirement plan?
- Did you make IRA contributions this year?
- Did you have a 401(k) rollover (Form 1099-R)?
- Did you pay foreign taxes?
- Did you receive a state tax refund for the prior year (Form 1099-G)? → may be taxable if you itemized

**Group 5 — Foreign currencies (if applicable):**
- Which foreign currencies? (for exchange rate lookup)

**Group 6 — Income estimates:**
- Approximate total W-2 income? (for threshold screening: AMT, NIIT, Medicare surtax)
- Any gig/freelance income (1099-NEC)? Crypto transactions (buy/sell/exchange)?
- Stock compensation: RSU vesting, ISO exercises, ESPP purchases?
- K-1 income from partnerships or S-corporations?
- Gambling income (W-2G)? → sets `has_gambling_income` flag

**Group 7 — Retirement & savings detail:**
- IRA type: Traditional, Roth, or both? Any Roth conversion this year?
- Backdoor Roth? (If yes: check pro-rata trap — any pre-tax Traditional IRA balance at year-end?)
- 401(k): Traditional/Roth split? Total deferral amount?
- Age 50-59 or 64+? (catch-up eligibility for 401k/IRA — amounts from `tax_law JSON` `retirement_401k.catch_up_50_plus` and `ira_limits.catch_up_50_plus`)
- Age 60-63? (SECURE 2.0 super catch-up for 401k — amount from `tax_law JSON` `retirement_401k.catch_up_60_63`, which **supersedes** the standard catch-up if both apply)
- Age 55+? (HSA catch-up: amount from `tax_law JSON` `hsa_limits.catch_up_55_plus`)
- Mega backdoor Roth: Does employer plan allow after-tax contributions + in-service conversion?

**Group 8 — Deductions & credits probe:**
- Student loan interest paid? Educator expenses (if teacher)?
- College tuition/education expenses? (AOTC / Lifetime Learning Credit)
- Child/dependent care expenses? (Form 2441)
- Charitable giving? (approximate amount, cash vs property donations)
- Energy-efficient home improvements? (heat pump, solar, insulation — 25C/25D)
- EV purchase or lease this year? (30D/25E)
- Marketplace health insurance? (Form 1095-A for Premium Tax Credit)

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
- Search: `"IRS {year} child tax credit amount"`
- Search: `"IRS {year} HSA contribution limits"`
- Search: `"IRS {year} 401k contribution limit"`
- Search: `"IRS {year} IRA contribution limit phase out"`
- Search: `"IRS {year} social security wage base"`
- Search: `"IRS {year} capital gains tax brackets"`
- Search: `"Medicare IRMAA {year} income brackets Part B Part D"` — populate `federal.irmaa.tiers`
  (`single`/`mfj` lists of `{magi_threshold, annual_surcharge_per_person}`, annualizing the
  monthly Part B + Part D surcharge above the standard premium). Optional but unlocks IRMAA
  flags in `/tax-roth-convert`; leave as `[]` if not relevant to the taxpayer.

**State (based on taxpayer's state):**
- For CA: Search `"FTB California {year} tax brackets"`, `"California {year} standard deduction"`
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

### 7. Summary

Print a summary of what was created:
- Taxpayer profile saved
- Tax law data fetched for {year}
- result.json initialized with {N} applicable sections
- answers.md initialized
- Dependencies verified
- Ready for `/tax-parse` — place source documents in `documents/` first

## Notes
- **SECURITY: Store ONLY the last 4 digits of SSN in taxpayer_profile.json. Never store full 9-digit SSNs in any file.** The `ssn_last4` field name in the template enforces this.
- taxpayer_profile.json is listed in .gitignore (created by install.sh) to prevent accidental commits
- If taxpayer_profile.json already exists, ask before overwriting
- If tax_law_{year}.json already exists, ask if they want to re-fetch
