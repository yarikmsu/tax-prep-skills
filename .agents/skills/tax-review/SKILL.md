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
2. Run (self-contained one-liner — some agents start a fresh shell per command):
   `[ -d .venv ] && source .venv/bin/activate; python3 agents/lib/tax_law_fetcher.py freshness {year}`
2. Act on the result:
   - **FRESH:** Proceed. Print key rates being used.
   - **STALE:** search the web (live results required — tax figures change
     annually; if your agent's web access is unavailable or cached-only, STOP
     and ask the user to paste the current figures with their source URL) for
     new legislation since fetch date. Re-fetch if found.
   - **INCOMPLETE:** Re-fetch missing fields by searching the web (same
     live-results requirement — if web access is unavailable or cached-only,
     STOP and ask the user to paste the missing figures with their source URL).
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
  - Investment income under the year's cap (~$11,600 TY2024, indexed —
    verify from the fetched tax law)?
  - Filing status consistent (MFS generally disqualifies unless the
    ARPA separated-spouse rules — effective TY2021 onward — are met)?
  - SSNs valid for employment for taxpayer, spouse, AND qualifying children?
  - NOTE: the toolkit does NOT compute EITC (community-track D7) — if the
    checks suggest eligibility, say so loudly and direct the taxpayer to
    software/a preparer rather than silently dropping a refundable credit.

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

### 3.7. No-income-tax state completeness (tax_law state.type == "none")

This step covers EVERY `type: "none"` state (FL, AK, NV, SD, TX, WY, and TN
for tax_year >= 2021 — the `create_state_template`/`_NO_INCOME_TAX_STATES`
allowlist), not just FL. **BEFORE branching, run the same cache-vs-taxpayer
cross-check `/tax-compute` Step 10 and `/tax-generate` apply (Codex
round-27 P2 — review lacked the twin):** (a) `tax_law.state.code` MUST
equal the reviewed `taxpayer_profile.json address.state`; (b) the cache's
own declared `tax_year` MUST equal the year under review; (c) the
code-vs-regime allowlist must hold in both directions (`type: "none"` only
for an allowlisted code AND an allowlisted code never `graduated_income`
with real brackets). Any failure is itself a review FINDING and this step
must NOT validate the state content — a completed Florida result beside a
fresh FL cache left over after the profile was corrected to CA would
otherwise have its populated FL statement and sales-tax block validated
here, exactly when the real finding is a MISSING California return. Only
after all three hold, branch on `tax_law_{year}.json state.code`:

**If `state.code == "FL"`** — a dedicated `florida_state` result.json section
exists, so verify ABSENCE was handled affirmatively in full detail:

1. **Affirmative statement present:** `florida_state` exists with
   `state_return_required: false` and the constitutional citation — an
   empty/missing section is a finding, not a pass.
2. **Sales-tax candidate computed — check this BEFORE the itemized-vs-
   standard election, not only when the return already itemizes:**
   `/tax-compute` step 2 always runs the FL sales-tax computation
   regardless of outcome, so `florida_state.sales_tax_deduction.
   total_line_5a_candidate` must be populated (a real computed number, not
   the template's placeholder `0`) on EVERY FL return. Gating this check on
   "the return currently itemizes" is circular (Codex round-9 P2): if the
   candidate was never computed, Schedule A stays below the standard
   deduction, the return elects standard by default, and a check that only
   fires "if itemizing" would never catch the very omission that caused the
   standard-deduction outcome. So: (a) first confirm
   `total_line_5a_candidate` exists and is a real computed value; (b) THEN,
   if it was passed to `compute_schedule_a` as `state_local_sales_taxes`
   and the return still elects the standard deduction, verify that's
   actually the larger of the two (not just assumed) — and if it elects to
   itemize with $0 on line 5a despite a nonzero candidate, that's forgotten
   money. (c) **Method election was OFFERED (Codex round-19 P2):**
   Schedule A line 5a also permits documented ACTUAL general sales taxes
   instead of the optional tables — `sales_tax_deduction.method` must
   record which method was elected (`"actual_receipts"` or
   `"optional_tables"`), and `answers.md` must carry the substantiation
   question's answer (plus both figures when receipts existed). A populated
   candidate with NO recorded method election means the actual-expense
   alternative was never offered — a potential line-5a understatement for
   a receipts-keeping taxpayer, and a finding. When `method` is
   `"actual_receipts"`, the `total_line_5a_candidate` intentionally does
   not equal the table components' sum (they record the losing table
   computation as comparison evidence).
3. **Use-tax note — require ANSWER EVIDENCE, not note presence (Codex
   round-25 P2):** FL self-reports use tax on untaxed out-of-state/online
   purchases — via Form DR-15MO (unregistered), or the dealer's regular
   Form DR-15 if registered as a FL sales-and-use-tax dealer (Codex
   round-16 P2, verified against floridarevenue.com — registered dealers
   must NOT use DR-15MO). **`florida_state.use_tax_note` ships as a
   POPULATED template default, so a present note is NOT evidence the
   questions were ever asked** — the same scaffold-read trap the
   null-by-default `no_income_tax_statement` rule (item 1) exists to
   prevent. In EVERY path, `answers.md` must record BOTH answers: (a) the
   untaxed-purchases question (yes with amounts / affirmative none), and
   (b) the dealer-registration question. Then verify the note reflects
   them: removed (or an affirmative-none rewrite) when no purchases;
   an actual use-tax figure when purchases were identified; DR-15 (not
   DR-15MO) wording for a registered dealer. Findings: a note still
   reading as the generic template text ("Review whether any apply") with
   NO recorded answers = the question was never asked — a taxpayer with a
   real use-tax liability would sail through; a note simply ABSENT with no
   recorded affirmative-none answer = same finding (never asked, not
   cleared); a registered-dealer note that still says DR-15MO = a
   wrong-form finding.
4. **Mover check (NY→FL corridor):** if the prior year shows an income-tax
   state (esp. NY), surface the domicile checklist: FL Declaration of
   Domicile (F.S. 222.17, recorded with the clerk of the circuit court),
   DR-501 homestead by Mar 1 (**claiming FL homestead while retaining NY
   STAR is disqualifying and audit bait**), driver license, voter + vehicle
   registration. NY's tests: statutory residency = 184+ NY days (any part
   of a day counts) + permanent place of abode substantially all year
   (>10 months per current Nonresident Audit Guidelines). Recommend
   contemporaneous day-count records for the move year.

**If `state.code` is any OTHER type-none state** (AK/NV/SD/TX/WY, or TN when
`tax_year >= 2021`) — no dedicated result.json section exists for these yet
(v1 gap; only FL has one), so verify the capability-gate's manifest note
landed correctly instead of the full FL checklist above:

1. **Affirmative no-income-tax statement present (state-NEUTRAL wording):**
   `meta.limitations` carries the state's affirmative "no individual income
   tax — no individual income-tax return exists or is required (this
   state's own property/use-tax filings, if any, are not modeled here)"
   line (the `capabilities._state_note` NON-FL text — the FL form names
   DR-405/DR-15MO are scoped to `code == "FL"` only; Codex round-17 P2,
   validation text aligned round-18 P2) for `state.code`. Two failure
   directions, BOTH findings: (a) a missing or generic "bracket-based
   ESTIMATE" line (the capability gate didn't recognize the state as
   type-none, or the note never made it into result.json); (b) a note that
   names Florida forms (DR-405 / DR-15MO / DR-15) for a NON-FL state — a
   TX/NV/AK taxpayer has no nexus with Florida filings, so that's a
   pre-round-17 capabilities module or a hand-edited manifest. Do NOT
   "correct" a neutral note by substituting Florida-specific wording; the
   neutral caveat IS the expected text here.
2. **Explicit MANUAL Schedule A sales-tax note present (not FL's automated
   wording):** `/tax-compute` step 2 explicitly SKIPS the sales-tax
   computation for every no-tax state other than FL — so the manifest note
   for a non-FL state (`capabilities._state_note`) must say the election
   must be computed MANUALLY from the IRS Optional State Sales Tax Tables,
   distinct from FL's "computed automatically" wording. **AK and TN
   variants (Codex rounds 23/26):** Alaska's note references the IRS
   Optional LOCAL Sales Tax Tables ONLY (AK has no statewide sales tax
   but is on the IRS local-table list), and post-repeal TN's note
   references BOTH the state table AND the local table (TN has a
   statewide sales tax and is on the local-table list too). A generic
   state-table-only note on an AK or TN return omits the taxpayer's
   locality amount and is itself a finding. Accepting FL's
   generic phrase here is itself a finding (Codex round-9 P2): it would let
   review pass a return that has neither a computed line-5a amount nor a
   warning that one is still needed, silently leaving an itemized deduction
   on the table with no evidence it was ever considered. Confirm the note's
   wording actually says MANUAL computation — not merely that a sales-tax
   note of some kind exists.

Missing either item for a non-FL type-none state is a finding — treat it the
same as a missing `florida_state` section: absence is not a pass.

**Nonresident exposure — the big one (state-independent, applies to EVERY
type-none state, not just FL):** no-tax-state residence does NOT remove
nonresident filing on other states' source income (NY IT-203 for NY
wages/K-1s), and with no resident-state return there is NO credit mechanism
— the other state's tax is a pure cost. **The true mechanism:** this is
tracked by `flags.has_nonresident_source_income` (set at `/tax-setup` from
the out-of-state source-income screening — W-2/K-1/rental are EXAMPLES
there, and the interview also asks specifically about Schedule C work
PERFORMED in another state and the SALE of real property LOCATED in
another state, per the round-15 broadening — and confirmed/set by
`/tax-parse` on a Box-15 mismatch) and gated by the
`nonresident_source_income` capability rule, which BLOCKS `/tax-compute`
until acknowledged — `moved_states_this_year`/`part_year_residency` alone
does NOT cover this (it only fires on a mid-year move; a full-year
no-tax-state resident with an NY W-2 or K-1 would otherwise sail through).
Cross-check EVERY screening category against the flag, not just the first
three (Codex round-21 P2): `flags.k1_states`, W-2 Box 15 codes ≠ resident
state, out-of-state rental income, Schedule C work performed in another
state, and an out-of-state real-property sale (`answers.md` records the
interview's answers for the last two) — a flag left False despite ANY of
these signals is itself a review finding, UNLESS the source state is on
the no-income-tax allowlist (`capabilities._NO_INCOME_TAX_STATES` — the
round-16 gate), where False is the CORRECT outcome: do not "fix" it by
setting the flag for an FL resident's TX K-1 or NV consulting gig.
Nonresident returns remain out of scope; flag loudly.

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

---

## 🏖️ No-Income-Tax State Completeness
{embed Step 3.7 output here, or the "not applicable" / skip note}
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
