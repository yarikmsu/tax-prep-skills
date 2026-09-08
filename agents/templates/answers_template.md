# Answers — Tax Year {{TAX_YEAR}}

Persistent answers extracted from documents. Future sessions should read this file first
to avoid re-parsing or re-asking resolved questions.

Last updated: {{DATE}}

---

## Taxpayer Identity

- **Primary:** {{PRIMARY_NAME}}
- **Spouse:** {{SPOUSE_NAME}} (if MFJ)
- **Address:** {{ADDRESS}}
- **Filing status:** {{FILING_STATUS}}
- **State:** {{STATE}}

---

## Tax Year & Period

- **Tax year:** {{TAX_YEAR}} (January 1 -- December 31, {{TAX_YEAR}})
- **Filing jurisdictions:** {{JURISDICTIONS}}

---

## Institutions & Accounts

### US Accounts

| Institution | Account | Type | Key Docs |
|---|---|---|---|
| | | | |

### Foreign Accounts (if applicable)

| Institution | Country | Account | Key Docs |
|---|---|---|---|
| | | | |

---

## Tax Figures

### Income Summary

| Source | Amount | Form |
|---|---|---|
| | | |
| **Total income** | **$0** | |

### Deductions & Credits

| Item | Amount | Notes |
|---|---|---|
| Standard deduction | | |
| | | |

### Tax Result

| Item | Amount |
|---|---|
| Federal tax | |
| Federal payments | |
| **Federal refund/(owed)** | |
| State tax | |
| State payments | |
| **State refund/(owed)** | |

_(State rows: for no-income-tax states, replace with one row "State: none — no individual income tax".)_

---

## Resolved Questions

1. _(Questions answered during document parsing go here)_

---

## Confirmed Assumptions
<!-- Updated by each skill after asking questions -->
| Assumption | Source | Skill | Date |
|------------|--------|-------|------|
<!-- Source: "User confirmed", "Defaulted (LOW impact)", "Prior year return", "Document" -->
| | | | |

---

## Verification Results
<!-- Updated by /tax-verify after cross-referencing against tax software output -->

| Software | Date Verified | Result | Discrepancies | Notes |
|----------|--------------|--------|---------------|-------|
| | | | | |

---

## Open Items

1. _(Items needing taxpayer input or further research)_

---

## Required answers

**This section is a CONTRACT, not a checklist.** Each line is one question a
headless run stops on, and the `[id]` is how it is recorded — put the answer
after the marker, on the same line, or on indented lines directly under it. An
id with a blank body counts as unanswered, which is what lets this section ship
pre-filled with the questions.

`sh agents/lib/run.sh -m agents.lib.answers --workspace . --check-required`
lists the ones THIS workspace owes, before a run starts, instead of stopping
mid-compute one question at a time. It exits 2 while any is missing; `--for
<skill>` narrows it to one skill's questions.

Not every question below applies to every return — the check works out which do
from the profile, the tax-law cache and the parsed result.

### Is the document set complete — is every W-2, 1099 and statement for the year in documents/?

*Asked by tax-parse Step 2.5; required when always, before parsing.*

- **[document_set_complete]**

### Is any document a CORRECTED form (W-2c, corrected 1099), and if so which figure does it supersede?

*Asked by tax-parse Step 2.5; required when always, before parsing.*

- **[corrected_documents]**

### From the prior return's Capital Loss Carryover Worksheet (lines 8 and 13): how much of the carryforward is SHORT-term and how much LONG-term?

*Asked by tax-compute income-and-agi.md; required when the legacy combined prior_year.capital_loss_carryforward is non-zero and both split fields are zero.*

- **[capital_loss_carryforward_split]**

### State estimated payments made DURING this tax year, and any prior-year state balance paid during it. (Schedule A line 5a — neither is on a W-2.)

*Asked by tax-compute income-and-agi.md; required when the state regime has an income tax; the two figures are inputs to compute_schedule_a and appear on no document.*

- **[state_taxes_paid_outside_withholding]**

### Cash and non-cash charitable gifts for the year, with the documentation you hold for each (a written acknowledgment is required for every gift of $250 or more).

*Asked by tax-compute income-and-agi.md; required when always — cash gifts feed the §170(p) non-itemizer deduction, so a STANDARD-deduction filer in a state with no income tax is asked too; unasked, that deduction is silently zero and the tax overstated.*

- **[charitable_gifts]**

### Does your spouse itemize? Under §63(c)(6)(A) an MFS filer whose spouse itemizes must itemize too, whatever the total.

*Asked by tax-compute income-and-agi.md; required when the filing status is married filing separately.*

- **[mfs_spouse_itemizes]**

### Is this the COMPLETE list of foreign financial accounts you held at ANY point during the year, including accounts closed mid-year and ones you only have signature authority over?

*Asked by tax-fbar Step 1.5; required when the profile reports foreign accounts or signature authority over one; an omitted account is an unfiled FBAR.*

- **[foreign_account_completeness]**

### These disposals have no acquisition on record and were given $0 basis, which OVERSTATES the gain. Supply the missing acquisition, or accept over-reporting on those rows.

*Asked by tax-parse-crypto Step 4; required when result.json crypto_summary.needs_basis is non-empty.*

**This is an ACKNOWLEDGEMENT, and it is re-asked every run.** It accepts a known wrong-direction risk on THIS year's figures, so a recorded one never satisfies it again — the run clears it with `--acknowledge`.

- **[crypto_disposals_without_basis]**

*Raised by the run itself rather than predictable from the inputs, so it is not listed above and `--check-required` names it separately:* `software_filing_status_mismatch`.
