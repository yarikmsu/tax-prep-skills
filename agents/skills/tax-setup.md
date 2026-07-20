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
- **Trump accounts (OBBBA §530A "MAGA"/newborn savings accounts) are NOT
  modeled** (out of persona): the $1,000 seed for children born 2025-2028 and
  the contribution/withdrawal mechanics are not computed — if a dependent has
  one, flag it for manual handling rather than silently omitting it.

**Group 4 — Tax situations (yes/no flags):**
- Do you have foreign bank/investment accounts? → triggers FBAR/8938 workflow (`has_foreign_accounts`)
- Do you have SIGNATURE AUTHORITY over (but no financial interest in) any foreign account — an employer's, a parent's, a nonprofit's? → FBAR Part IV even with no financial interest (`signature_authority_foreign_accounts`)
- Do you hold any NON-US-DOMICILED mutual funds or ETFs in ANY brokerage
  account — foreign OR US (e.g. a UK OEIC/unit trust, an Irish/Luxembourg UCITS
  ETF, a Canadian mutual fund)? → sets `has_foreign_mutual_funds`. **Ask this
  independently of the foreign-account questions:** PFIC status follows the
  FUND's domicile, NOT where it's held, so a foreign-domiciled fund bought
  through a US broker is still a PFIC and must set the flag even when the
  taxpayer has no foreign accounts. These are almost
  always PFICs (§1291-1298); **Form 8621 may be required per fund** — the
  obligation is EVENT-DRIVEN (an excess distribution, a sale, a QEF/MTM
  election, or the §1298(f) annual reporting, which has de-minimis exceptions),
  so determine per fund rather than assuming every fund needs a form. When a
  trigger applies the default §1291 regime is punitive (excess-distribution
  spreading + interest charge) and is NOT modeled here, so this lands on the
  limitations manifest and is flagged in `/tax-review`. US-domiciled funds
  (even ones holding foreign stocks) are NOT PFICs — this is about the FUND's
  domicile, not what it invests in.
- Did you LIVE abroad (tax home outside the US) during the year? → sets
  `lives_abroad` (raises the Form 8938 thresholds; the FEIE / Form 2555
  election is NOT modeled — only the FTC path — so this lands on the
  limitations manifest)
- Do you have self-employment income (1099-NEC, freelance, business)? → triggers Schedule C (`has_self_employment`)
  - **If yes (or you have K-1 pass-through business income):** Is that income
    from an ACTIVE qualified trade or business you materially participate in —
    NOT a passive/portfolio investment (e.g. a rental, a limited-partner
    interest you don't run, or REIT dividends)? → sets `qbi_active_trade`. This
    is load-bearing for TY2026+: §70105 grants an active qualified business a
    MINIMUM QBI deduction (indexed ~$400) once QBI is at/above the indexed
    income threshold (~$1,000), even when 20% of QBI is smaller. It defaults
    False (no floor → no over-claim), so leaving it unset silently forgoes an
    otherwise-applicable floor and OVERSTATES tax for a small active business.
    Ask explicitly whenever `has_self_employment` or `has_k1_income` is set.
- Do you have rental income? → sets `has_rental_income` (Schedule E is NOT
  modeled — the `/tax-compute` capability gate will block; tell the user now)
- Do you have an HSA (Health Savings Account)? **If yes**, for the Form 8889
  return-side excess check (`hsa_detail`): what was your HDHP coverage
  (`self_only`/`family`), and for **how many months** were you an HSA-eligible
  individual (HDHP on the 1st of the month, no disqualifying coverage)? Were you
  eligible on **December 1** (the §223(b)(8) last-month rule → full annual limit,
  but a testing period applies)? If married and BOTH spouses have HSAs with
  family coverage, what share of the family limit is yours? (A partial-year
  filer who contributed the full annual limit has an EXCESS + a 6% excise — the
  months matter.)
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
  - **If RSUs VESTED this year, NQSOs were exercised, OR you SOLD any
    RSU/NQSO employer shares this year (even shares that vested/were exercised
    in a PRIOR year)** → sets `has_rsu_vesting`. **If ESPP shares were
    PURCHASED or SOLD this year (any purchase year)** → sets `has_espp`. The
    SALE trigger is load-bearing: the double-tax happens at SALE, so a taxpayer
    liquidating long-held employer stock with no current-year vesting still
    needs the basis correction — keying only on current-year vesting would
    leave the primary scenario unprotected. These are load-bearing for basis
    correction: brokers report the 1099-B cost basis for employer stock
    EXCLUDING the compensation already in W-2 box 1 (for RSUs, typically $0
    basis), so an unadjusted sale DOUBLE-TAXES the W-2 income — routinely a
    four-to-five-figure overstatement.
    When either flag is set AND any 1099-B is present, the broker's
    *supplemental information* statement (the "adjusted cost basis" pages) —
    or the equity-plan vest confirmations / Form 3922 (ESPP) / Form 3921 (ISO)
    — becomes a REQUIRED intake document; `/tax-parse` will prompt for it and
    `/tax-compute` applies the Form 8949 code-B basis adjustment only from that
    documentation (never inferred).
- K-1 income from partnerships or S-corporations? If yes, ask **which states the entities are based in** (sets `flags.k1_states`, e.g. `["CA", "NY"]`). PTET is an entity-level state tax — the partnership / S-corp files in its operating state, not the partner's resident state — so a TX-resident partner in a CA partnership needs `k1_states: ["CA"]` to get the right PTET reminders in `tax_calendar_{year}.ics`.
- Gambling income (W-2G)? → sets `has_gambling_income` flag
- **OBBBA Schedule 1-A deductions (TY2025-2028 only — BELOW-AGI, line 13b):**
  - Any **qualified tip income** in a customarily-tipped occupation? → sets
    `has_qualified_tips` (§224). Source: the employer's designated qualified-
    tip amount (W-2 box 14 code / separate statement) if provided, ELSE **W-2
    box 7 (Social Security tips)** — a single-employer tipped worker usually
    has no separate statement, so box 7 tips still set the flag. The SSN test
    is RECIPIENT-specific: the spouse who RECEIVED the tips must have a valid
    SSN (an ITIN on the OTHER, non-recipient spouse does NOT disqualify the
    return); MFS doesn't qualify — married must file jointly. **Unreported or
    employer-ALLOCATED tips (W-2 box 8)?** → sets
    `has_unreported_or_allocated_tips` (Form 4137 — adds them to wages on 1040
    line 1c and computes SS/Medicare tax on Schedule 2 line 5; NOT modeled by
    the engine, so it lands in the limitations manifest). They ARE §224
    qualified tips (Schedule 1-A Part II has a Form 4137 line), so once the
    paired Form 4137 income + FICA are hand-entered they CAN feed
    `qualified_tips` (set `has_qualified_tips` too) — exclude them only while
    that paired treatment is unhandled; don't permanently drop the deduction.
  - Any **overtime pay**? → sets `has_qualified_overtime`. Capture the **FLSA
    premium portion ONLY** (the "half" of time-and-a-half, box 14 / employer
    statement), NOT gross overtime wages (§225).
  - **Car-loan interest** on a vehicle bought after 2024-12-31? → sets
    `has_car_loan_interest` (§70203). Only qualifies if: NEW vehicle (original
    use), first-lien loan originated after 2024-12-31, US final assembly,
    a qualified passenger vehicle with **GVWR under 14,000 lbs** (car/SUV/van/
    pickup/motorcycle — NOT a heavier commercial vehicle), personal use, the
    VIN is reported on the return, and the filer is NOT married-filing-
    separately (§70203 is unavailable to MFS). Ask for the VIN and the GVWR;
    route an unknown/over-14,000-lb vehicle to manual verification (don't set
    the flag).
- **Age 65 or older** (either spouse) by year end? → sets
  `age_65_plus_primary` / `age_65_plus_spouse`. Drives BOTH the §63(f)
  additional standard deduction AND the separate OBBBA §70103 enhanced senior
  deduction (Schedule 1-A line 13b — they STACK). The spouse's §70103 senior
  deduction counts only on MFJ; the §63(f) additional standard deduction,
  however, may ALSO count the spouse's age/blindness box on an MFS return when
  the spouse had NO income, isn't filing a return, and can't be claimed as a
  dependent by another taxpayer (2025 Form 1040 line 12d). **Valid-SSN split:**
  the §70103 senior deduction (unlike §63(f)) requires the 65+ person to have a
  valid SSN. The `age_65_plus_*` flags drive §63(f), so KEEP them set for a 65+
  ITIN person — but do NOT pass that person's age boolean into
  `compute_schedule_1a` (screen §70103 SSN eligibility separately), or Schedule
  1-A over-claims the senior deduction for a non-SSN filer.
  **Final (decedent) returns:** a taxpayer/spouse who was born before the
  cutoff but DIED before actually reaching age 65 does NOT qualify (per the
  Schedule 1-A instructions) — do NOT set the flag in that case even though
  the birth year would otherwise suggest 65+.

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
- Child/dependent care expenses? (Form 2441) → sets `has_dependent_care`.
  **If yes**, collect `dependent_care_detail` for the §21 credit: how many
  QUALIFYING persons (under 13, or a spouse/dependent incapable of self-care who
  lived with you more than half the year)? How much did you pay in work-related
  care? Did BOTH spouses (if married) work, look for work, or attend school
  full-time (the §21(d) work-related rule — confirm)? Was any of it paid through
  an employer dependent-care FSA (W-2 box 10)? For a non-working spouse who was
  a full-time student or disabled, how many months? Do you have the care
  provider's name and TIN (required — due diligence)? If MFS, did you live apart
  from your spouse the last 6 months (the §21(e)(2) exception — otherwise MFS
  gets no credit)? The credit is now COMPUTED from these facts.
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
  Leave `phaseout_rate` as `null` unless a source contradicts the built-in
  schedule (25% ≤TY2025, 50% from TY2026 per OBBBA §70107) — the statutory
  rate is coded, so a null is correct, not a gap. **TY2026+ staleness
  tripwire:** §70107 also RESETS the phase-out thresholds to the 2018 base
  (~$500K single / $1M MFJ, reindexed), so a TY2026 fetch that returns
  TY2025-magnitude `phaseout_start` values (~$626K single) is reading a stale
  source — re-search for the post-OBBBA figures.
- Search: `"IRS Schedule 1-A {year} tips overtime deduction limits"` — populate
  `federal.schedule_1a` (`tips_cap`, `overtime_cap` by single/mfj,
  `car_loan_cap`, `senior_amount_per_person`, and the three phase-out-start
  pairs: `tips_overtime_phaseout_start`, `car_loan_phaseout_start`,
  `senior_phaseout_start`). ONLY needed when the taxpayer has tips, overtime,
  car-loan interest, or is 65+ (TY2025-2028) — leave the block all-null
  otherwise (freshness does not nag; `compute_schedule_1a` hard-errors at the
  point of use if a figure is missing when actually needed). The phase-out
  MECHANICS ($100/$1,000, $200/$1,000, 6%) are coded, not fetched.
- Search: `"IRS §170(p) non-itemizer charitable deduction {year} amount"` —
  populate `federal.charitable_non_itemizer` (`amount` ~$1,000 non-joint,
  `joint_amount` ~$2,000 MFJ). ONLY needed for a TY2026+ STANDARD-deduction
  filer with cash charitable gifts — leave all-null otherwise (freshness does
  not nag; `compute_charitable_non_itemizer` hard-errors at use if unfetched
  when actually needed).
- Search: `"IRS §70105 minimum QBI deduction {year} amount threshold"` —
  populate `federal.qbi_minimum` (`income_threshold` ~$1,000, `amount` ~$400,
  both indexed). ONLY needed for a TY2026+ filer with a small ACTIVE qualified
  trade — leave all-null otherwise (the minimum is taxpayer-favorable, so
  `compute_qbi_deduction` DEGRADES with a note rather than guessing).
- Search: `"IRS quarterly interest rates underpayment {year}"` (and `{year+1}`
  quarters as published) — populate `federal.underpayment_rates` as a list of
  `{"effective_from": "YYYY-MM-DD", "annual_rate": 0.08}` covering every §6621
  rate period. **Fetch scope — two consumers with DIFFERENT windows:**
  - `compute_form_2210` (estimated-tax penalty) accrues Apr 15 of {year} through
    Apr 15 of {year+1}.
  - `compute_underpayment_interest` (§6601/§6622 interest on a late-paid balance,
    M3) accrues from the {year+1} Apr-15 DUE DATE forward to the pay date — so an
    October extension filer needs the {year+1} Q2/Q3/Q4 quarters (Apr/Jul/Oct),
    which are BEYOND the 2210 window. Extend the fetch forward through the quarter
    containing the anticipated pay date whenever an extension is in play.

  The irs.gov quarterly interest-rate news releases are the source; record each
  under `_sources` (K2). Future quarters aren't published yet at setup time —
  both consumers refuse to guess: `compute_form_2210` degrades on missing periods,
  and `compute_underpayment_interest` reports interest only PARTIAL-through the
  last cached quarter with a loud note (never extrapolating the last rate across
  an unfetched quarter). `/tax-compute` and `/tax-extend` re-check at use.

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
- For CA **when the taxpayer has an ISO exercise** (`has_iso_exercise`): Search
  `"California Schedule P 540 {year} AMT exemption amount phase-out"` → populate
  `state.amt.exemption` and `state.amt.phaseout_start` (both keyed
  `single`/`mfj`/`mfs`/`hoh`, indexed annually — FTB publishes them in the
  Schedule P instructions). Record the URL under `_sources` (K2). The 7% TMT
  rate and 25% phase-out are statutory and coded in `compute_ca_schedule_p`;
  only these indexed figures are fetched. Unfetched HARD-ERRORS at compute for a
  CA ISO exerciser (never a guessed exemption that would zero the CA AMT). Leave
  all-null for a non-ISO CA return.
- For NY: Search `"New York {year} income tax brackets"`
- For other states: Search `"{state} {year} income tax rates"`
- **PTET rate schedules when the taxpayer has K-1 income** (`has_k1_income`,
  for EACH state in `flags.k1_states` that is CA/NY/NJ — the K-1 ENTITY state,
  not the resident state): Search `"{state} pass-through entity tax PTET rate
  schedule {year}"` → populate `ptet.<STATE>` (top-level, keyed by entity
  state) as `{"schedule": [[lower, rate, base_tax], ...], "vintage": "<cite>"}`.
  The first bracket must start at `lower: 0`; CA is a single flat bracket
  (`[[0, 0.093, 0]]`), NY/NJ are graduated. Record the URL under `_sources` as
  `ptet.<STATE>`. Unfetched is NOT fatal — `/tax-ptet` falls back to its
  built-in statutory schedule with a LOUD unfetched-schedule warning naming the
  vintage (the NJ BAIT overhaul is the drift precedent); a corrupt block
  hard-errors. Fetch it so the price is version-controlled per year.

**Exchange rates (if foreign income):**
- Search: `"IRS yearly average exchange rate {year} {currency}"`

**Check for new tax legislation:**
- Search: `"federal tax law changes {year}"` to catch any mid-year legislation (like OBBBA for 2025)

Save all fetched rates to `tax_law_{year}.json` using the structure from `agents/lib/tax_law_fetcher.py`.

**Pin the source per block (H4).** As you populate EACH fetched block, record
the AUTHORITATIVE URL you fetched it from in the `_sources` map, keyed by block
path — every populated federal rate/bracket block (`federal.filing_statuses`,
`federal.capital_gains_rates`, `federal.child_tax_credit`, `federal.amt`,
`federal.salt_cap`, `federal.qbi_threshold`, …), the single state block keyed by
its **2-letter code** (`state.CA`), AND `exchange_rates` when foreign currencies
are used. Example:
`"_sources": {"federal.filing_statuses": "https://www.irs.gov/pub/irs-drop/rp-24-40.pdf", "federal.capital_gains_rates": "https://www.irs.gov/...", "state.CA": "https://www.ftb.ca.gov/...", "exchange_rates": "https://www.irs.gov/individuals/international-taxpayers/yearly-average-currency-exchange-rates"}`.
Use only PRIMARY sources — any US-government `.gov` site (irs.gov, ftb.ca.gov,
edd.ca.gov, tax.ny.gov, nj.gov, ssa.gov, treasury.gov, congress.gov,
govinfo.gov) or law.cornell.edu, with an **https/http** URL. A blog or
aggregator (smartasset, nerdwallet, a news site) is NOT acceptable —
`tax_law_fetcher.check_freshness` reports a non-authoritative (or non-web)
recorded URL as **INCOMPLETE**, and a populated-but-unpinned fetched block as an
advisory. This is the chain-of-custody pin: an LLM-fetched bracket must trace to
a government source before it flows into a filed return.

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
