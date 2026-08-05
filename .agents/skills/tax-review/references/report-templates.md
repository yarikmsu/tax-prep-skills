# tax-review Step 4 — review report templates

The output format for `### 4. Generate Review Report`. Read this when you reach
Step 4, after Steps 2, 3, 3.5, 3.6 and 3.7 have produced their findings.

The YoY, PTET and no-income-tax-state blocks embed the output of Steps 3.5, 3.6
and 3.7 respectively, or that step's skip note — never omit the section, because
a missing section reads as "checked and clean" rather than "not run".

## Contents

- IRS Officer Perspective — audit risk, DIF flags, information-return matching, international compliance, substantiation gaps
- EA (Enrolled Agent) Perspective — missed opportunities, retirement optimization, next-year planning, document retention
- YoY What-Changed
- PTET Election Opportunity
- No-Income-Tax State Completeness

---

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
