# Source form reconciliation

Use the original statements and the taxpayer's verified receipts to decide
which amounts belong on this return. Preserve each physical file's intake
record and `source_sha256`, including copies and replaced versions.

## Copies and corrections

- For an exact copy under another filename, retain one active record. Mark
  the other `skipped` with its reason and set
  `source_form.duplicate_of` to the retained `doc_id`. Tie-out checks the
  digests, recipient, payer and form identity.
- For a superseded form, keep its skip record and set
  `source_form.superseded_by` to the corrected record's `doc_id`. Put the
  covered `tax_year` and the same reviewed `series_id` on both records and
  set `source_form.corrected` to `true` on the active record. The series id
  is an opaque label for the issuer's account or statement lineage. Use the
  printed corrected indicator or issuer correspondence. Keep distinct ids
  for separate statements, even when the amounts match.
- Use the existing `payer`, `person` and `form_type` for the issuer, owner
  and form. Record `source_form.account_last4` only when needed to identify
  an account. Never use SSNs or full account numbers as record ids.

The filing package includes the reviewed copy and correction dispositions.

## Expected forms

Match the payer and the type of amounts present on the form. For example, a
consolidated statement with both interest and dividends can clear both
expected entries. An interest-only statement clears the interest entry. Carry
any known `person` or `account_last4` in the expected-source profile entry.

If the issuer confirmed that no form was issued for this year, keep the entry
as an object with its `name` or `institution` and set
`resolution` to `issuer_confirmed_no_form`. Keep the confirmation locally. An
expected form whose arrival or issuance has not been resolved remains open.

## Business Forms 1099-NEC and 1099-K

Keep each form's reported business gross in `self_employment_gross` and
preserve its withholding separately. Use the taxpayer's receipt-level books
to identify when both forms report the same payment. A 1099-K requires a
documented disposition, source routing, or reconciliation. Equal amounts alone
do not establish that two payments are the same. [IRS Form 1099-K guidance](https://www.irs.gov/businesses/what-to-do-with-form-1099-k).

For a return with both business forms, ledger the accounting source with the
same taxpayer `person` and a `source_form.tax_year` matching the return. Give
the record only the `self_employment_book_gross` category and
`business_receipts` rows with `payment_id`, `amount`, and `reported_by`.
`reported_by` contains each active form record's `doc_id` for that payment.
Unreported receipts have an empty list. Keep a repeated payment on one row
and give every distinct payment its own local id.

For the supported single Schedule C, use the verified book total for line 1.
Tie-out checks each form's full reported amount against the linked rows and
the unique payments against line 1. It records the overlap explanation for
filing export.
