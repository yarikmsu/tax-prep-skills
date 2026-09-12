# tax-compute Steps 11-11.5 — international compliance and optimization discovery

FBAR / Form 8938 thresholds, then the six-category optimization sweep
that produces the opportunities table.

## Contents

- [Step 11: International Compliance](#step-11-international-compliance)
- [Step 11.5: Optimization Discovery Engine](#step-115-optimization-discovery-engine)

---

### Step 11: International Compliance

**FBAR check:**
- Sum all foreign account maximum balances
- If aggregate > `fbar_threshold` (from `tax_law JSON`): set fbar_required = true

**Form 8938 check:**
Use `check_form_8938_required()` from `agents/lib/tax_computations.py`:
- ⚠️ Use REALISTIC simultaneous peak values, NOT conservative FBAR max values
- FBAR and 8938 use different valuation approaches

### Step 11.5: Optimization Discovery Engine

After computing the return, systematically check for missed optimization opportunities. For each item, compare the current return against what's possible:

**Category 1 — Deduction optimization:**
- Itemized vs standard deduction: compute itemized total (SALT up to the cap in `federal.salt_cap` from tax_law JSON — OBBBA RAISED it for TY2025-2029 with the base INDEXED ~1%/yr 2026-2029, a MAGI phasedown above the high-income threshold that is NOT modeled, and a reversion in TY2030. No figure is written here: `ptet._salt_cap` is the one place that decides it, and `/tax-review` carried a stale $10,000 in prose for as long as this row carried a number — plus mortgage interest, charitable, medical >7.5% AGI). Would itemizing save money?
- Charitable bunching: if charitable is close to tipping point, mention bunching strategy for next year
- Above-the-line deductions: student loan interest ($2,500 max — §221(b)(1), flat), educator expenses (the cap is INDEXED; take it from the covered year's Schedule 1 instructions), SE health insurance — all used?

**Category 2 — Credit optimization:**
- American Opportunity Tax Credit (AOTC) — up to $2,500, 40% refundable
- Lifetime Learning Credit — up to $2,000
- Saver's Credit — 10-50% of retirement contributions if AGI qualifies
- Energy credits: Residential Clean Energy (25D), Energy Efficient Home (25C) — **confirm for the COVERED YEAR whether each still applies and at what cap**; OBBBA moved several energy provisions' end dates, and a credit claimed after its termination is disallowed on examination
- EV credit: Clean Vehicle (30D), Previously Owned (25E) — same check: **confirm availability and cap for the covered year** before claiming either
- Dependent Care Credit (Form 2441) — verify current limits on the web if legislation changed
- Adoption Credit (Form 8839) — the per-child exclusion AND the OBBBA refundable portion are both INDEXED; take both from the covered year's instructions, and record which year's figures were used
- Premium Tax Credit (Form 8962) — if marketplace insurance

**Category 3 — Retirement optimization:**
- 401(k) utilization: current deferral as % of max from `tax_law_{year}.json`. If `age_60_63`: use `catch_up_60_63` (supersedes `catch_up_50_plus`). If `age_50_plus` but NOT `age_60_63`: use `catch_up_50_plus`.
- IRA: room for contribution? Spousal IRA (IRC §219(c)) if spouse has low/no earned income?
- Roth IRA: income below phase-out? Direct contribution or backdoor needed?
- HSA: contribution at max (self-only / family limits from `tax_law_{year}.json` + catch-up from `hsa_limits.catch_up_55_plus` if 55+)?
- Mega backdoor Roth: if employer allows after-tax + in-service conversion

**Category 4 — Entity structure:**
- If Schedule C net profit > $50K (common heuristic — not from tax_law JSON): flag S-Corp election analysis opportunity (SE tax savings)
- If multiple Schedule Cs: consolidation opportunity?

**Category 5 — State optimization:**
- CA HSA: is the add-back computed correctly?
- State-specific credits: CA renter's credit ($60/$120), CA EITC, etc.
- Multi-state: any allocation or credit opportunities?

**Category 6 — Timing strategies:**
- Roth conversion window: if in lower bracket than expected future years
- Tax-loss harvesting: unrealized losses in brokerage accounts?
- Income deferral: if next year will be lower bracket (retirement, sabbatical, etc.)
- Estimated tax safe harbor: pay at least 90% of current year tax OR 100% of prior year tax (110% if prior year AGI >$150K MFJ / $75K MFS)

Output as a table:

```
## Optimization Opportunities Found

| # | Opportunity | Est. Savings | Action Required | Difficulty |
|---|------------|-------------|-----------------|------------|
| 1 | {description} | ${amount} | {what to do} | Easy/Medium/Hard |
...

Total potential savings: ${sum}
```

Only include opportunities where estimated savings > $50. Mark "Already optimized" items with ✅.

