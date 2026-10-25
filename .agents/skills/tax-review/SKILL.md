---
name: tax-review
description: EA + IRS officer review of tax return for audit risk, missed deductions, and optimization. Use when the user asks for a review, a second opinion, an audit-risk assessment, or missed deductions, after tax-compute and ideally after tax-verify.
license: Apache-2.0
---

# /tax-review

Perform a professional review of the tax return from dual perspectives.

## Usage
```
/tax-review              # Both perspectives
/tax-review --perspective irs   # IRS officer only
/tax-review --perspective ea    # Enrolled Agent only
```

## Prerequisites
- `result.json` must exist with computed values
- `taxpayer_profile.json` must exist
- `answers.md` should be up to date
- Ideally run after `/tax-verify` so verification results are available

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

### 0.5. Tax Law Freshness Verification

Before any review, verify tax law data is current:

1. **Validate** `{year}` is a 4-digit integer (2000–2099) before using in any command.
2. Run (self-contained one-liner — some agents start a fresh shell per command):
   `sh agents/lib/run.sh agents/lib/tax_law_fetcher.py freshness {year}`
3. **Exit 2 means NOT_FOUND — the STOP below.** It is the gates' blocked code,
   spent on the one status whose remedy is a stop; STALE and INCOMPLETE exit 0
   because their remedies are inline and are spelled out here. A 2 with an EMPTY
   report is not this gate at all — it is the launcher refusing (no venv, wrong
   interpreter), so read the JSON before acting on the code. Act on the status,
   which says WHICH figures:
   - **FRESH:** Proceed. Print key rates being used.
   - **STALE:** search the web (live results required — tax figures change
     annually; if your agent's web access is unavailable or cached-only, STOP
     and ask the user to paste the current figures with their source URL) for
     new legislation since fetch date. Re-fetch if found.
   - **INCOMPLETE:** Re-fetch missing fields by searching the web (same
     live-results requirement — if web access is unavailable or cached-only,
     STOP and ask the user to paste the missing figures with their source URL).
   - **NOT_FOUND:** **STOP.** Tell user to run `/tax-setup` first.
4. Print rates being used so user can eyeball them (standard deduction, CTC, top bracket).

### 1. Load Complete Picture

Read all available data:
- `result.json` — all form values
- `taxpayer_profile.json` — filing status, flags
- `answers.md` — resolved questions, verification results
- `tax_law_{year}.json` — current rates/thresholds
- All `summaries/*.md` — source document details

### 1.5. Review Context Questions

Gather context that affects review quality. **First check `answers.md` "Confirmed Assumptions" — skip any question already answered there.** For remaining unknowns, ask the user, one topic at a time:

**Documentation readiness:**
- Do you have receipts/records for all Schedule C business expenses?
- For charitable donations >$250: do you have written acknowledgment letters from each organization?
- For foreign accounts: do you have year-end statements for every foreign financial account?

**One-time events this year:**
- Any lawsuit settlements, inheritance, or large gifts received?
- Any property sales (real estate, vehicle, collectibles)?
- Any debt forgiveness or cancellation (Form 1099-C)?
- Any disaster losses or casualty losses?

**Planning context:**
- Do you expect income to be roughly the same, higher, or lower next year?
- Any major life changes expected next year (marriage, divorce, new child, retirement, move)?
- Are you considering any large purchases that might have tax implications (home, EV)?

Log all answers to `answers.md` under "Confirmed Assumptions". After every
write or rewrite of that file, immediately run:
```bash
sh agents/lib/run.sh -m agents.lib.secure_fs answers.md
```

### 2. IRS Officer Review (DIF Score & Audit Risk)

Simulate an IRS officer reviewing this return. Check:

**DIF Score Red Flags:**
- Schedule C loss with W-2 income: Is there hobby-loss risk (3-of-5-year rule)?
- Schedule C expense ratio: Are expenses > 50% of gross income?
- Home office deduction: Is it reasonable relative to home size?
- Charitable deductions: Are they > 3% of AGI?
- Business use of vehicle: Is it 100%? (red flag)
- Medical expenses: Are they > 7.5% of AGI threshold?
- **Round-number detection (H3):** scan the deduction and expense entries in
  `result.json` (Schedule C expense lines, charitable contributions, misc
  Schedule A items) for suspiciously round values — whole hundreds or
  thousands ($500, $1,200, $3,000...). One is coincidence; a PATTERN of
  whole-hundred entries reads as estimation rather than records, a classic
  DIF signal. For each cluster found, ask whether receipts/records support
  the exact figures and note the answer in the review workpaper. (Statutory
  caps that legitimately land on round numbers — the $3,000 capital-loss
  cap, the year's SALT cap — are NOT flags; check provenance before flagging.)
- **HoH substantiation (H2 tie-in):** if filing status is HoH, confirm
  `answers.md` carries the "HoH qualifying tests" block from `/tax-setup`
  (qualifying person + cost-of-keeping-up-home + considered-unmarried).
  Missing block → have the taxpayer answer the tests now and record them;
  HoH without substantiation is a top correspondence-audit trigger.

**Information Return Matching:**
- Does every 1099 have a corresponding line on the return?
- Are there any W-2s missing?
- 1099-R: Is the distribution code handled correctly?
- Foreign income: Are Schedule B Part III questions answered correctly?
- Is there a discrepancy between W-2 Box 1 and Box 16? (state adjustment)
- **1099-K gross mismatch (H3):** if any 1099-K was parsed, compare its Box
  1a gross against the gross receipts reported (Schedule C line 1, or the
  personal-item disposition on Schedule 1/8949). 1099-K Box 1a is GROSS —
  it includes refunds, fees, chargebacks, and sales tax collected — so
  reported receipts BELOW the 1099-K total is exactly what the IRS
  matching program flags (CP2000). Any excess of 1099-K gross over reported
  receipts must be reconciled line-by-line (fees/refunds as expenses or
  adjustments, personal non-taxable transfers identified), and the
  reconciliation recorded in the review workpaper. Receipts far ABOVE the
  1099-K total is fine (cash/check sales don't hit a 1099-K).
- **Employer-stock $0-basis check (J1):** when the taxpayer has equity comp
  (`flags.has_rsu_vesting` or `flags.has_espp` set), scan every Schedule D /
  Form 8949 detail row (`result.json schedule_d` box rows) for a row with
  `cost` of $0, OR `cost` < 20% of `proceeds`, AND `adjustment` == 0 (no
  code-B correction applied). Each such row is a PRESUMPTIVE missing code-B
  basis adjustment — brokers report RSU/ESPP/NQSO basis EXCLUDING the W-2-
  included compensation (for RSUs, typically $0), so the unadjusted gain is
  overstated by exactly that compensation (a four-to-five-figure over-payment).
  Banner: "presumptive missing code-B basis adjustment on {row} — broker basis
  for employer stock excludes W-2 compensation; expected overstatement of gain.
  Obtain the broker supplemental statement / Form 3922 / vest confirmation and
  apply the adjustment via /tax-compute." The 20%-of-proceeds heuristic is a
  REVIEW FLAG only — never an automatic adjustment (an invented adjustment
  understates tax, the unsafe direction). A non-employer ticker with $0 basis
  (equity flags unset) does NOT fire.
- **ESPP/RSU wash-sale interplay (J1, caveat):** selling employer stock at a
  LOSS within 30 days of a vest or ESPP purchase of the SAME ticker is a §1091
  wash sale — the loss is disallowed and shifts to the replacement lot's basis.
  This return-side flow does NOT model it; if the taxpayer vested/purchased and
  sold the same ticker at a loss inside a 30-day window, point them at
  `/tax-harvest` (which models the §1091 mechanics against `holdings.json`).
- **EITC due diligence (H3):** if the return claims (or the income level
  suggests eligibility for) the Earned Income Tax Credit, run the Form
  8867-style checks — EITC has the highest audit rate of any credit:
  - Qualifying child: relationship, age (<19, <24 if a FULL-TIME student,
    any age if permanently disabled — and the child must be YOUNGER than
    the taxpayer/spouse unless disabled), and residency (lived with
    taxpayer in the US > half the year) — residency is the #1 EITC audit
    issue; what records exist (school/medical/lease showing the child's
    address)? With NO qualifying child, the taxpayer (or on MFJ, AT LEAST
    ONE spouse) must be 25-64 at year end.
  - Investment income under the year's cap (INDEXED; take it from the covered
  year's Form 1040 instructions, not from here —
    verify from the fetched tax law)?
  - Filing status consistent (MFS generally disqualifies unless the
    ARPA separated-spouse rules — effective TY2021 onward — are met)?
  - SSNs valid for employment for taxpayer, spouse, AND qualifying children?
  - NOTE: the toolkit does NOT compute EITC (community-track D7) — if the
    checks suggest eligibility, say so loudly and direct the taxpayer to
    software/a preparer rather than silently dropping a refundable credit.
  - Two runtime signals already carry this, and their ABSENCE is itself a
    finding: `meta.limitations` holds the capability gate's
    `earned_income_credit` line whenever `flags.may_qualify_for_eic` is set,
    and the tie-out advises by line while Form 1040 line 27 / IT-201 lines 65
    and 70 still record 0. If your checks suggest eligibility and the flag is
    false, the screening question was answered wrong — say so.

**Audit Trigger Assessment:**
Searching the web, check current year IRS audit statistics:
- Search: `"IRS audit rate {year} by income level"`
- What is the audit rate for this income level?
- Are there specific areas of IRS focus this year?

**International Compliance:**
- FBAR filed or flagged for filing?
- Form 8938: threshold correctly evaluated?
- Schedule B Part III: foreign accounts question answered YES?
- Foreign income properly reported with exchange rates?
- Foreign tax credit documentation adequate?
- **PFIC / Form 8621 (G3):** if `flags.has_foreign_mutual_funds` is set (or a
  foreign brokerage in the FBAR/8938 account list plausibly holds pooled
  investment funds), flag it LOUDLY: non-US mutual funds/ETFs are almost always
  PFICs (§1291-1298) that **may require Form 8621 per fund** — an EVENT-DRIVEN
  obligation (excess distribution, sale, QEF/MTM election, or §1298(f) annual
  reporting, which has de-minimis exceptions), so determine per fund rather than
  assuming every fund needs a form. When a trigger applies the default §1291
  regime is punitive (excess-distribution spreading + compounding interest
  charge) — a silent multi-thousand-dollar compliance miss that rides alongside
  the same accounts FBAR/8938 already cover. This engine does NOT compute Form
  8621 (it's on the limitations manifest); confirm the taxpayer's preparer /
  specialized software has determined the obligation per fund, and that any QEF
  / mark-to-market election was made timely (generally the first year held).
  US-domiciled funds holding foreign stocks are NOT PFICs — the test is the
  fund's domicile.

**Substantiation Checklist:**
- Schedule C expenses: Are receipts available?
- Home office: Is there documentation?
- Charitable contributions: Written acknowledgment for gifts > $250?
- IRA contributions: Is Form 5498 available?

### 3. EA (Enrolled Agent) Review

Simulate a tax professional reviewing for optimization.

**Forgotten Items Checklist — verify each item is either accounted for or confirmed N/A:**

| # | Category | Item | Status |
|---|----------|------|--------|
| 1 | Income | All W-2s accounted for (including spouse)? | |
| 2 | Income | All 1099-INT / 1099-DIV included? | |
| 3 | Income | All 1099-B (capital transactions) from all brokerages included? | |
| 4 | Income | State tax refund from prior year (1099-G)? | |
| 5 | Income | Jury duty pay, gambling winnings (W-2G)? | |
| 6 | Income | Alimony received (if pre-2019 agreement)? | |
| 7 | Income | Rental income / royalties (Schedule E)? | |
| 8 | Income | K-1 income from partnerships/S-corps/trusts? | |
| 9 | Deductions | Student loan interest (up to $2,500)? | |
| 10 | Deductions | Educator expenses (Schedule 1 line 11; the cap is INDEXED — take it from the covered year's instructions)? | |
| 11 | Deductions | SE health insurance deduction? | |
| 12 | Deductions | HSA deduction (personal contributions beyond employer)? | |
| 13 | Deductions | IRA deduction (if eligible — check phaseout)? | |
| 14 | Deductions | Alimony paid (if pre-2019 agreement)? | |
| 15 | Credits | Child Tax Credit — all qualifying children counted? | |
| 16 | Credits | AOTC / Lifetime Learning Credit? | |
| 17 | Credits | Dependent Care Credit (Form 2441)? | |
| 18 | Credits | Saver's Credit (if AGI qualifies)? | |
| 19 | Credits | Energy credits — 25C (home improvement) / 25D (solar)? | |
| 20 | Credits | EV credit — 30D (new) / 25E (used)? | |
| 21 | Credits | Earned Income Credit (if applicable)? | |
| 22 | Credits | Premium Tax Credit (Form 8962, if marketplace insurance)? | |
| 23 | International | FBAR filed (if aggregate foreign accounts >$10K)? | |
| 24 | International | Form 8938 threshold evaluated? | |
| 25 | International | Schedule B Part III checked "Yes" for foreign accounts? | |
| 26 | International | FTC: simplified or Form 1116? Correct method used? | |
| 27 | Retirement | 401(k) maxed (use `retirement_401k` limits from `tax_law JSON`: elective_deferral + catch-up; catch_up_60_63 if 60-63)? | |
| 28 | Retirement | IRA contributed (Traditional or Roth)? Spousal IRA? | |
| 29 | Retirement | HSA maxed (limits from `tax_law JSON` + catch-up if 55+)? | |

For any item marked "not checked" or "unknown", investigate whether it applies. Ask the user if needed.

**Missed Deductions/Credits:**
_(Covered by checklist rows 9-22 above. Additionally check:)_
- Adoption credit (Form 8839; both the exclusion and the OBBBA refundable
  portion are INDEXED — take both from the covered year's instructions)?
- SALT deduction — would itemizing beat standard deduction?

**Retirement Optimization:**
- 401(k): Is the max contribution reached? (elective_deferral from `tax_law JSON`)
- Catch-up contributions: If `age_60_63`: use `catch_up_60_63` (supersedes `catch_up_50_plus`). If `age_50_plus` but NOT `age_60_63`: use `catch_up_50_plus`. (SECURE 2.0 — amounts from `tax_law JSON`)
- Roth 401(k) vs Traditional 401(k): Is the split optimal for marginal rate?
- IRA: Is there room for contribution? Spousal IRA?
- Roth IRA: Is income below phase-out? Direct contribution or backdoor?
- Mega backdoor Roth: Does employer plan allow after-tax contributions?
- HSA: Is max contribution reached? Is it being used as investment vehicle?

**Tax Planning Recommendations:**
- Estimated tax payments needed next year?
- W-4 adjustment needed? (over/under-withholding analysis)
- Tax-loss harvesting opportunities?
- Roth conversion opportunity? (if in lower bracket than expected future)
- Bunch deductions strategy? (alternate itemizing/standard deduction years)
- Qualified business income: Can Schedule C income be restructured?
- State tax planning: Any state-specific credits or deductions missed?

**Document Retention Checklist:**
- How long to keep this year's return and supporting documents
- Which documents are critical to retain (especially for carryforward items)
- QBI loss carryforward documentation

### 3.5. YoY What-Changed

Produce a deterministic year-over-year diff against the prior return. This step is read-only — run as below, `yoy_diff.py` never writes workspace files.

1. Compute `prior_year = {year} - 1`.
2. If `archive/{prior_year}/` does NOT exist (or is missing `result.json` / `taxpayer_profile.json` — `/tax-archive` writes both together):
   - Record this note for the report: `_YoY diff skipped: no archive/{prior_year}/ found._`
   - Proceed to Step 4.
3. Else run:
   ```bash
   sh agents/lib/run.sh agents/lib/yoy_diff.py --prior archive/{prior_year} \
 --year {year} --threshold 1000
   ```
4. **Exit code 0:** embed stdout verbatim as the "YoY What-Changed" block in the Step 4 report.
5. **Exit code non-zero:** record `_YoY diff skipped: {stderr message}_` and proceed to Step 4.

A power user can run the same command with `--threshold 500` for a finer-grained diff or `--json` for machine-readable output. `--trend-years N` (1-3) adds columns for the archived years before the prior one, each read from `archive/{year}` and refused if missing; every Δ and the choice of movers stay prior year to this year. `--save` also writes the markdown, owner-only, to `summaries/yoy_diff_{prior_year}_to_{year}.md` as a standalone deliverable.

### 3.6. PTET Election Opportunity (SALT-cap workaround)

If the taxpayer has K-1 income from a pass-through entity in **CA, NY, or NJ**,
the pass-through-entity tax (PTET) election can convert state tax that's stranded
by the federal SALT cap into a fully-deductible federal expense. This step is
read-only (`ptet.py` writes nothing).

**Do not state the cap from memory.** OBBBA raised it from $10,000 to $40,000
($20,000 MFS) for TY2025, steps it 101% a year by statute through 2029, phases
it down on modified AGI, then reverts — so a figure written here would be
wrong for most of the years this skill runs in, and it was: this step said
"$10,000" while `ptet._salt_cap` had been returning the OBBBA figure since
PR #22. The engine prints the cap it
actually used, labelled with the regime it came from; read it off the output
rather than telling the taxpayer a number first.

1. Check `flags.has_k1_income`. If false and no K-1 surfaced during parsing,
   record `_PTET: not applicable (no pass-through income)._` and proceed to Step 4.
2. Else, for each entity state in `flags.k1_states` that is CA/NY/NJ, ask the
   taxpayer for the qualifying pass-through income and the credit its
   election makes usable, and read the owner's tax for that state from its
   section of `result.json` (ask when the run computes no return for it).
   Where `/tax-ptet` step 3 stops, a refundable credit
   beyond the owner's tax for that state, itemized or not, leave that state's
   `--income` out and record
   `_PTET (<STATE>): not priced — refundable credit exceeds the owner's <STATE> tax._`
   and continue with the others, or with Step 4. When the return itemized,
   also get their OTHER state and local taxes by step 3: SALT before the cap
   less that credit, refunded next year or not, never the tax attributed to
   the K-1. Pass the
   remaining SALT as `--other-salt`, so the engine derives the headroom against
   the cap it prices. Then run:
   ```bash
   sh agents/lib/run.sh -m agents.lib.ptet --year {year} \
 --income CA=<amount> [--income NY=<amount>] [--other-salt <amount>] \
 [--standard-deduction]
   ```
   The engine reads the return's modified AGI and itemize choice from
   `result.json`'s `schedule_a`, so it prices the cap the return applied. A
   return with no `schedule_a` section took the standard deduction: pass
   `--standard-deduction` then, because a missing section alone is not read
   as one.
   Never pass `schedule_a.salt_before_cap` as `--other-salt` without
   subtracting that credit: it includes the state tax the credit pays, so the
   headroom comes out too small and the savings too large. And never
   ask whether the cap is already used up, to pass `--salt-room`: the answer
   depends on a cap that moves every year and with MAGI, and `--salt-room`
   overrides the cap the engine priced.
3. Embed the rendered output as the "PTET Election Opportunity" report block, and
   flag the **earliest election deadline** (NY's is due *during* the tax year and
   cannot be made on the return — the highest-risk one to miss).
4. On non-zero exit, record `_PTET: skipped — {stderr}_`.

See `/tax-ptet` for the standalone skill and full caveats.

### 3.7. No-income-tax state completeness (tax_law state.type == "none")

If `tax_law state.type` is `"none"`, follow
[references/no-income-tax-state-review.md](references/no-income-tax-state-review.md)
in full and return here. It covers EVERY `type: "none"` state (FL, AK, NV, SD,
TX, WY, and TN for tax_year >= 2021), opens with the cache-vs-taxpayer
cross-check `/tax-compute` Step 10 and `/tax-generate` apply, and each of its
failures is itself a review FINDING.

If `state.type` is `graduated_income`, or is ABSENT, skip to step 4. The
generic no-state-code fallback deliberately carries no `type` key (so an
untouched state's freshness behaviour does not move), which makes "absent"
a real third case rather than an oversight — enumerate it, or an agent
reviewing an NJ or MA return matches neither branch and has no stated exit.

### 3.8. Residency and multistate guardrails (EVERY return)

Follow [references/residency-review.md](references/residency-review.md) in full
and return here. **Not conditional on the state's regime**: residency is a
question about the state being LEFT, and this checklist began life inside step
3.7's no-income-tax branch, where a taxpayer leaving New York for any income-tax
state reached none of it.

It covers whether residency was ASSERTED or read off the mailing address, the
mechanical test a change of domicile does not save a taxpayer from, what does
not count toward it, the mover checklist for the state being left, and the
convenience-of-the-employer rule, which is an ALLOCATION question that looks
like a residency one. The figures live in that file and in
`docs/contributing/residency-guardrails.md` and are deliberately not repeated
here. Each of its failures is itself a review FINDING.

### 4. Generate Review Report

Format the review using the templates in
[references/report-templates.md](references/report-templates.md) — the IRS
Officer and EA perspectives plus the YoY, PTET and no-income-tax-state blocks.
Read it now.

### 4.5. Persist the Review (workpaper)

Write the FULL Step 4 report to `reviews/{year}-review.md` — the review is a
workpaper that belongs with the return, not scrollback that vanishes:

```bash
mkdir -p reviews && sh agents/lib/run.sh -m agents.lib.secure_fs reviews
```

Now write the report. Only once the file exists, restrict it — the two commands
are in separate blocks because running them as one would call `secure_fs` on a
path that does not exist yet, which exits 1:

```bash
sh agents/lib/run.sh -m agents.lib.secure_fs reviews/{year}-review.md
```

- `reviews/` is gitignored (it derives from PII-bearing result.json) and is
  archived into `archive/{year}/reviews/` by `/tax-archive` at year-end
  cutover, where `/tax-rollover` surfaces its "Tax Planning for Next Year"
  section as next year's checklist.
- Re-running `/tax-review` overwrites the file (the latest review of a return
  supersedes earlier ones — result.json changed, so the old review audited a
  return that no longer exists).

### 5. Summary

Print:
- Overall audit risk level
- Number of flags identified
- Optimization opportunities found (with estimated dollar impact)
- Top 3 recommendations for next tax year
- Path of the persisted review (`reviews/{year}-review.md`)
