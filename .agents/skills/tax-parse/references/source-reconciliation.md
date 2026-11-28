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
- Name the correction in `form_type` too: "Form W-2c", or the 1099 with
  "Corrected". A W-2 is corrected only by a W-2c, so a plain W-2 cannot
  supersede another.
- When two or more active forms share the recipient, the form and an issuer
  name (one name's words contained in the other's), give each a distinct
  reviewed `series_id`, such as the account or control number's lineage. A
  download repeated, a PDF beside its CSV export, or a correction does not
  change the bytes into a different statement. A consolidated 1099 relates to
  every 1099 from that issuer.
- A standalone 1099 already reported inside a consolidated statement is a
  skip with `source_form.included_in` naming the consolidated record.
  On an included Form 1099-NEC, record Box 1 in
  `source_form.included_boxes.self_employment_gross`. For an included Form
  1099-K, record Box 1a in `source_form.included_boxes.form_1099k_box_1a`
  and record the amount routed to business in
  `source_form.included_boxes.self_employment_gross`. This link requires the
  full Box 1a amount routed to business. Keep a Form 1099-K with a mixed or
  nonbusiness route active and route its receipts separately. The retained
  consolidated record carries the combined gross for the included business
  forms in `key_boxes.self_employment_gross`. The included amounts serve as
  evidence and the retained record contributes the gross to the return.
  If one statement includes both positive 1099-NEC and 1099-K amounts, use
  separate active business source records so the books can track overlap.
- A skipped W-2 or 1099 beside an active one from the same issuer and
  recipient needs one of `duplicate_of`, `superseded_by` or `included_in`.
  A reason alone is not checked, and a real second form would vanish.
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

Copy each 1099-K's original Box 1a in `form_1099k_box_1a` and route its
reviewed receipts to the appropriate categories (`self_employment_gross`,
`personal_1099k`, `capital_proceeds`). The routed amounts must add up to
Box 1a to the cent: every dollar has a line, and a remainder left unrouted
drops from the return. Record an explicit Box 1a zero when the effective form
reports zero. Personal-item gains go to Form 8949. Personal-item losses are
nondeductible.

When the return has a business — `flags.has_self_employment` true, an active
Schedule C, or business receipts in the ledger — review the receipts before
treating any portion of a 1099-K as personal, including personal sales routed
to Form 8949. Record `input.1099k_personal_routing` with `--outcome reviewed`
using the material-evidence command. Bind the current `parse_ledger.json`
and each affected 1099-K with repeatable `--input` arguments. Include
supporting transaction records when used. Refresh the review after a source
or its ledger entry changes.

For a return with both business forms, ledger the accounting source with the
same taxpayer `person` and a `source_form.tax_year` matching the return. Give
the record only the `self_employment_book_gross` category and
`business_receipts` rows with `payment_id`, `amount`, and `reported_by`.
`reported_by` contains each active form record's `doc_id` for that payment.
Unreported receipts have an empty list. Keep a repeated payment on one row
and give every distinct payment its own local id.

When a row names more than one reporter, set `evidence_file` to the
workspace-relative processor transaction report or client remittance that
identifies the shared payment. It is never a document the ledger records,
whatever its status, nor a `summaries/` file: a copy of the form cannot prove
the form overlaps. Review that source alongside the forms and
record `input.business_receipt_overlap` with `--outcome reviewed`.
Bind `parse_ledger.json` and every named evidence file with repeatable
`--input` arguments. Refresh the review when the receipts or evidence change.

For the supported single Schedule C, use the verified book total for line 1.
Tie-out checks each form's full reported amount against the linked rows and
the unique payments against line 1. The filing disclosure includes the count
and total of overlapping receipts counted once.
