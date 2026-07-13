---
name: tax-review
description: EA + IRS officer review of tax return for audit risk, missed deductions, and optimization
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

### 0.5. Tax Law Freshness Verification

Before any review, verify tax law data is current:

1. **Validate** `{year}` is a 4-digit integer (2000–2099) before using in any command.
2. Run: `python3 agents/lib/tax_law_fetcher.py freshness {year}`
2. Act on the result:
   - **FRESH:** Proceed. Print key rates being used.
   - **STALE:** WebSearch for new legislation since fetch date. Re-fetch if found.
   - **INCOMPLETE:** Re-fetch missing fields via WebSearch.
   - **NOT_FOUND:** **STOP.** Tell user to run `/tax-setup` first.
3. Print rates being used so user can eyeball them (standard deduction, CTC, top bracket).

### 1. Load Complete Picture

Read all available data:
- `result.json` — all form values
- `taxpayer_profile.json` — filing status, flags
- `answers.md` — resolved questions, verification results
- `tax_law_{year}.json` — current rates/thresholds
- All `summaries/*.md` — source document details

### 1.5. Review Context Questions

Gather context that affects review quality. **First check `answers.md` "Confirmed Assumptions" — skip any question already answered there.** For remaining unknowns, use **AskUserQuestion**:

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

Log all answers to `answers.md` under "Confirmed Assumptions".

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
  cap, a $10,000 SALT cap — are NOT flags; check provenance before flagging.)
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
  - Investment income under the year's cap (~$11,600 TY2024, indexed —
    verify from the fetched tax law)?
  - Filing status consistent (MFS generally disqualifies unless the
    ARPA separated-spouse rules — effective TY2021 onward — are met)?
  - SSNs valid for employment for taxpayer, spouse, AND qualifying children?
  - NOTE: the toolkit does NOT compute EITC (community-track D7) — if the
    checks suggest eligibility, say so loudly and direct the taxpayer to
    software/a preparer rather than silently dropping a refundable credit.

**Audit Trigger Assessment:**
Using **WebSearch**, check current year IRS audit statistics:
- Search: `"IRS audit rate {year} by income level"`
- What is the audit rate for this income level?
- Are there specific areas of IRS focus this year?

**International Compliance:**
- FBAR filed or flagged for filing?
- Form 8938: threshold correctly evaluated?
- Schedule B Part III: foreign accounts question answered YES?
- Foreign income properly reported with exchange rates?
- Foreign tax credit documentation adequate?

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
| 10 | Deductions | Educator expenses (up to $300)? | |
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
- Adoption credit (up to $17,280/child; OBBBA: $5,000 refundable)?
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

Produce a deterministic year-over-year diff against the prior return. This step is read-only — `yoy_diff.py` never writes workspace files.

1. Compute `prior_year = {year} - 1`.
2. If `archive/{prior_year}/` does NOT exist (or is missing `result.json` / `taxpayer_profile.json` — `/tax-archive` writes both together):
   - Record this note for the report: `_YoY diff skipped: no archive/{prior_year}/ found._`
   - Proceed to Step 4.
3. Else run:
   ```bash
   python3 agents/lib/yoy_diff.py --prior archive/{prior_year} --current . \
       --year {year} --threshold 1000
   ```
4. **Exit code 0:** embed stdout verbatim as the "YoY What-Changed" block in the Step 4 report.
5. **Exit code non-zero:** record `_YoY diff skipped: {stderr message}_` and proceed to Step 4.

A power user can run the same command with `--threshold 500` for a finer-grained diff or `--json` for machine-readable output.

### 3.6. PTET Election Opportunity (SALT-cap workaround)

If the taxpayer has K-1 income from a pass-through entity in **CA, NY, or NJ**,
the pass-through-entity tax (PTET) election can convert state tax that's stranded
by the $10,000 SALT cap into a fully-deductible federal expense. This step is
read-only (`ptet.py` writes nothing).

1. Check `flags.has_k1_income`. If false and no K-1 surfaced during parsing,
   record `_PTET: not applicable (no pass-through income)._` and proceed to Step 4.
2. Else, for each entity state in `flags.k1_states` that is CA/NY/NJ, ask the
   taxpayer for the qualifying pass-through income and whether their $10K SALT cap
   is already exhausted (usually yes). Then run:
   ```bash
   python3 -m agents.lib.ptet --workspace . --year {year} \
       --income CA=<amount> [--income NY=<amount>] [--salt-room <unused $>]
   ```
3. Embed the rendered output as the "PTET Election Opportunity" report block, and
   flag the **earliest election deadline** (NY's is due *during* the tax year and
   cannot be made on the return — the highest-risk one to miss).
4. On non-zero exit, record `_PTET: skipped — {stderr}_`.

See `/tax-ptet` for the standalone skill and full caveats.

### 4. Generate Review Report

Format the review as:

```
# Tax Return Review — TY{year}

## 🏛️ IRS Officer Perspective

### Audit Risk Assessment
**Overall Risk Level:** LOW / MEDIUM / HIGH

### DIF Score Flags
1. {flag description} — Risk: {low/medium/high}
...

### Information Return Matching
- ✅ All W-2s matched
- ✅ All 1099s matched
...

### International Compliance
- ✅/⚠️ FBAR: {status}
- ✅/⚠️ Form 8938: {status}
...

### Substantiation Gaps
1. {item needing documentation}
...

---

## 📊 EA (Enrolled Agent) Perspective

### Missed Opportunities
1. {opportunity} — Potential savings: ${X}
...

### Retirement Optimization
- 401(k) utilization: {X}% of max
- IRA: {status}
- HSA: {status}
...

### Tax Planning for Next Year
1. {recommendation}
...

### Document Retention
- Keep {year} return for {N} years
- Critical carryforward items: {list}

---

## 📅 YoY What-Changed
{embed Step 3.5 output here, or the skip note}

---

## 🏢 PTET Election Opportunity
{embed Step 3.6 output here, or the "not applicable" / skip note}
```

### 4.5. Persist the Review (workpaper)

Write the FULL Step 4 report to `reviews/{year}-review.md` — the review is a
workpaper that belongs with the return, not scrollback that vanishes:

```bash
mkdir -p reviews && chmod 700 reviews
# write the report, then:
chmod 600 reviews/{year}-review.md
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
