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
- **If HoH selected — run the qualifying tests (H2) and record each answer in
  `answers.md` as the substantiation trail** (HoH is among the most-audited
  statuses; the IRS asks exactly these):
  1. **Qualifying person** (Pub 501 Table 4) — one of:
     - a qualifying CHILD who lived with you MORE than half the year (a
       MARRIED child counts only if you can claim them as a dependent —
       generally not if they file a joint return, unless filed solely to
       claim a refund);
     - a dependent PARENT (a parent need NOT live with you if you paid more
       than half the cost of keeping up the parent's main home for the
       whole year);
     - another QUALIFYING RELATIVE you can claim as a dependent who is
       related to you under the §152(d)(2)(A)-(G) list (grandparent,
       sibling, grandchild, aunt/uncle, in-law, step-parent — NOT the
       unrelated member-of-household catch-all) AND lived with you more
       than half the year.
     Name the person and their relationship.
  2. **Cost of keeping up the home:** did you pay MORE than half — rent or
     mortgage interest + property taxes, utilities, repairs, home insurance,
     and food eaten in the home (NOT clothing, education, medical, or the
     rental value of a home you own)?
  3. **If still legally married:** the "considered unmarried" test (§7703(b))
     — you file separately from your spouse, you paid >1/2 the cost of the
     home, the home was the main home of YOUR CHILD whom you can claim as a
     dependent (or could, but for a Form 8332 release to the noncustodial
     parent — HoH still stands), and your spouse did NOT live in the home
     during the LAST 6 months of the year. **Temporary absences do not
     count as not-living-there:** a spouse away for illness, education,
     business, vacation, or MILITARY DEPLOYMENT is considered to live in
     the home — a deployed spouse does NOT make you "considered unmarried".
     ALTERNATIVE: a spouse who was a NONRESIDENT ALIEN at any time in the
     year (no §6013(g) election) also makes you unmarried for HoH.
  If ANY test fails, HoH is not allowed — re-ask filing status (usually
  Single, or MFS if married). Append to `answers.md`:
  ```markdown
  ## HoH qualifying tests — TY{year}
  - Qualifying person: {name}, {relationship}, {months lived with taxpayer}
  - Paid >1/2 cost of keeping up home: {yes + basis}
  - Considered-unmarried (if married): {N/A | spouse absent since {month}}
  ```

**Group 1.5 — Life events THIS tax year (H1):**
Most yes answers set a profile flag the `/tax-compute` capability gate (C1)
turns into a limitations-manifest line — ask ALL of these; a life event the
interview never captures is invisible to every downstream check.
- Did you get married this year? → sets `married_this_year` (Dec-31 rule:
  married at year-end → MFJ or MFS — or HoH ONLY via the considered-unmarried
  or nonresident-alien-spouse tests in Group 1; if MFS in a
  community-property state (incl. CA), Form 8958 income splitting applies
  and is NOT modeled)
- Did you divorce or legally separate this year? → sets `divorced_this_year`
  (unmarried at Dec 31 → Single or HoH only; ask who claims each dependent —
  Form 8332 release — and check any QDRO distributions)
- A child born or adopted this year? → add to Group 3 dependents (a child
  born ANY day of the year counts for the full year)
- ANY adoption expenses this year — including an adoption still IN
  PROGRESS, a failed domestic adoption, prior-year expenses first
  claimable this year (domestic expenses are claimed the year AFTER
  payment until final), or an adoption-credit carryforward? → sets
  `has_adoption_expenses` (Form 8839 credit — manual worksheet). Ask this
  even when no child was adopted THIS year.
- Did you SELL your primary home? → sets `sold_primary_home` (§121
  $250K/$500K-MFJ exclusion with the 2-of-5-year ownership AND use tests;
  the exclusion is once-every-2-years (§121(b)(3) — ask about other home
  sales in the prior 2 years); the $500K joint amount additionally needs
  BOTH spouses to meet the use test, either to meet ownership, and neither
  to have used the exclusion in the prior 2 years; **ask whether a Form
  1099-S was received** — if yes, the sale must be reported even when
  fully excluded)
- Did you receive an inheritance? → sets `received_inheritance` (bequests
  themselves are not income, but capture date-of-death FMV basis
  documentation now; EXCEPTION — income in respect of a decedent (§691):
  inherited traditional IRA/401(k) distributions and the decedent's unpaid
  wages ARE taxable and get NO §1014 step-up; inherited IRAs carry
  SECURE-Act 10-year distribution rules)
- Do you pay or receive alimony/spousal support under ANY decree? Branch
  by instrument date:
  - PRE-2019 decree → sets `has_pre2019_alimony` (federally taxable to
    recipient / deductible by payer. CAUTION: a pre-2019 decree MODIFIED
    after 2018 loses grandfathering ONLY if the modification expressly
    applies the TCJA repeal — read the modification, not just the decree
    date)
  - POST-2018 decree → sets `has_post2018_alimony` (federally NEITHER
    income nor deduction — but CA does NOT conform to the TCJA repeal:
    post-2018-decree alimony stays CA-taxable to the recipient /
    CA-deductible by the payer via Schedule CA adjustments, not
    auto-computed; other states' conformity varies)
- Did your spouse pass away this year (or in the prior two years)? Year of
  death → MFJ with the deceased spouse is available ONLY if you did NOT
  remarry by Dec 31 (if you remarried, you file MFJ/MFS with the NEW
  spouse and the deceased spouse's final return is MFS); the TWO FOLLOWING
  years → QSS **only if the tests pass**: a dependent CHILD lived with you ALL year, you paid >1/2
  the cost of the home, you were entitled to file MFJ in the death year,
  and you have not remarried. Record the QSS test answers in `answers.md`
  like the HoH block. Also capture basis step-up on inherited assets
  (FULL step-up on community property incl. CA) → consider
  `received_inheritance`.
- Moved states? asked in Group 2 (`moved_states_this_year`).

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
    `has_iso_exercise`. This is load-bearing: it triggers the federal AMT
    computation (Form 6251 — the bargain element is the most common AMT
    trigger and can add tens of thousands of tax) and the Form 8801 credit
    carryforward. CA's own AMT (Schedule P) is NOT computed — tell a CA
    exerciser now that the CA side needs software verification. If Form 3921
    is among the documents, /tax-parse extracts the spread per exercise —
    only ask for the figure (FMV − strike per share × shares held) when no
    3921 will be provided.
    (RSU/ESPP alone do NOT set this flag — they are ordinary W-2 wage
    events.)
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
- College tuition/education expenses? (AOTC / Lifetime Learning Credit) → sets `has_education_expenses`.
  Form 8863 IS computed by `/tax-compute` (D5) — collect per student NOW:
  **whether the student is claimed as a dependent on THIS return** (only the
  taxpayer, spouse, or a CLAIMED dependent's expenses count — §25A(f)(1);
  a dependent student claims nothing on their own return, and a
  non-claimed relative's tuition earns $0), 1098-T box 1 payments and box 5
  scholarships (expenses are netted per Pub 970), and the AOTC eligibility
  facts (degree program, at least half-time, first 4 years, AOTC claimed
  fewer than 4 prior years, no felony drug conviction, TIN by the due
  date). MFS gets neither credit; QSS phases out on the SINGLE $80K-$90K
  MAGI range.
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
- Search: `"IRS {year} AMT exemption amount phase out"` — populate `federal.amt`
  (`exemption` + `phaseout_start` by status, and `rate_28_breakpoint` — the
  26%→28% figure, published with the Rev. Proc. inflation adjustments;
  the MFS breakpoint is derived by halving, don't store it). Required for
  ISO exercisers — `compute_form_6251` hard-errors on an unfetched table.
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
