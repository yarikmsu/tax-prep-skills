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

### Confirm the 1099-G year/revision, unemployment versus governmental family/medical programs, actual gross receipts, same-year UI repayments and eligible UI contribution reductions. Identify CA EDD PFL separately; unknown or medical benefits need resolution.

*Asked by tax-compute income-and-agi.md; required when governmental UI or family benefits are reported.*

- **[government_benefit_sources]**

### What is the signed Schedule1 income EXCLUDING these unemployment and family benefits, including a deliberate zero? Reconcile refunds, business/K-1 income, losses and other sources; an old aggregate may already contain benefits.

*Asked by tax-compute income-and-agi.md; required when governmental UI or family benefits are reported.*

- **[schedule1_nonbenefit_income]**

### Confirm the investment-source split for California: direct US-obligation interest, municipal interest by issuer, fund ordinary/exempt dividends, US RIC status and annual asset-test evidence and income percentages. Record an explicit none when absent; an unknown source or qualification is not zero.

*Asked by tax-compute state-return.md; required when this run selects a CA return.*

- **[ca_investment_sources]**

### From the federal state-refund tax-benefit worksheet, what refund amount was included in federal income, including a worked zero? Confirm that amount before its CA subtraction.

*Asked by tax-compute state-return.md; required when a CA return has a state refund or worked taxable-refund field.*

- **[ca_state_refund_worksheet]**

### What CA estimates, extension payments, K-1 allocations and prior applied overpayment are credited to this tax year? Confirm their dates, account ownership and claimable shares from source records, including an explicit none; identify the opening applied credit separately from new payments.

*Asked by tax-compute state-return.md; required when this run selects a CA return.*

- **[ca_state_payments]**

### How much of California line97 should be applied to next year's estimates? This is a binding election; confirm the amount, including a deliberate zero.

*Asked by tax-compute state-return.md; required when this run selects a CA return.*

- **[ca_overpayment_election]**

### From the worked FTB 3510, what CA AMT credit is available and ALLOWABLE this year, including a worked zero? Preserve the available amount less the allowable credit plus new ISO credit for rollover.

*Asked by tax-compute state-return.md; required when a CA return has a prior-year AMT credit flag or carryforward.*

- **[ca_amt_credit_worksheet]**

### Is the document set complete — is every W-2, 1099 and statement for the year in documents/?

*Asked by tax-parse Step 2.5; required when always, before parsing.*

- **[document_set_complete]**

### Is any document a CORRECTED form (W-2c, corrected 1099), and if so which figure does it supersede?

*Asked by tax-parse Step 2.5; required when always, before parsing.*

- **[corrected_documents]**

### From the prior return's Capital Loss Carryover Worksheet (lines 8 and 13): how much of the carryforward is SHORT-term and how much LONG-term?

*Asked by tax-compute income-and-agi.md; required when the legacy combined prior_year.capital_loss_carryforward is non-zero and both split fields are zero.*

- **[capital_loss_carryforward_split]**

### State and local estimated payments made in good faith DURING this tax year, any prior-year overpayment credited to them included, and any prior-year state or local balance paid during it, without its penalties or interest. Filing separately, only your own share. (Schedule A line 5a — neither is on a W-2.)

*Asked by tax-compute income-and-agi.md; required when always — a resident of a state with no income tax can still pay a former or another state's tax, and no profile field records last year's state; the two figures are inputs to compute_schedule_a and appear on no document.*

- **[state_local_taxes_paid_outside_withholding]**

### Cash and non-cash charitable gifts for the year, with the documentation you hold for each (a written acknowledgment is required for every gift of $250 or more).

*Asked by tax-compute income-and-agi.md; required when always — cash gifts feed the §170(p) non-itemizer deduction, so a STANDARD-deduction filer in a state with no income tax is asked too; unasked, that deduction is silently zero and the tax overstated.*

- **[charitable_gifts]**

### Resolve each charitable carryover's vintage, category, jurisdiction, eligibility and balance from prior worksheets; document the manual resolution.

*Asked by tax-compute income-and-agi.md; required when an opening charitable ledger is unresolved or a closing total lacks vintages.*

- **[charitable_carryovers]**

### Does your spouse itemize ON EACH RETURN they file — federal, and California or New York if either applies? The elections are INDEPENDENT (a spouse can take the federal standard deduction and itemize for CA), so record the state answer separately where it differs and pass each one to its own function. It binds this return BOTH ways: §63(c)(6) zeroes the standard deduction of both spouses once either itemizes, so a yes forces itemizing whatever the total, and a no bars it even when it is worth more. A no is an ANSWER, not an absence — the engine refuses to guess it. If the answer is no, also record whether the spouse files their own SEPARATE return or is considered unmarried and files as head of household: the coupling does not bind the second, and the two answers differ by the whole margin.

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
