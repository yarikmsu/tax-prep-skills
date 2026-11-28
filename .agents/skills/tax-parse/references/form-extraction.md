# Standard form extraction

For a declared California Form 540NR part-year plan, retain actual receipt and
service dates, each W-2 CA Box 16 wage amount (including a confirmed zero when
absent), payroll workday and source evidence, stock-exercise source and basis
worksheets, and resident and nonresident income allocations. Keep differences
in tax law separate from sourcing. Use workday records and source evidence
for allocations based on where the wages were earned. Use the existing CA withholding, PTET and
ISO ledger categories once, regardless of mailing address. Leave uncertain
allocations open for tax-compute's `state-return-ca-part-year.md` reference.
Use the taxpayer's reviewed residence facts and income-source evidence.

### W-2 Extraction
Must capture: Boxes 1-8, 10-11, 12 (all codes), 13, 14 (14a-14b from TY2026; every label, SDI, PFL and UI among them), 15-20 (state and local wages, tax withheld and locality, every row)
Check: Box 12 codes (AA=Roth 401k, C=life ins, D=401k, DD=health, W=HSA)
For TY2026, read TP (cash tips), TT (qualified overtime premium), TA (employer
Trump-account contributions), 14a (other labels) and 14b (TTOC). TP/TT feed
the [tip/overtime source review](tips-overtime.md); TA feeds neither deduction.

For each W-2, record the verified Box 1 amount as `key_boxes.wages` and Box 2
as `key_boxes.federal_withholding_w2`. Use numeric `0` for a verified zero.
For a box that is visibly blank, record `"blank"` under its number in
`w2_box_status`, for example `"w2_box_status": {"2": "blank"}`. An unread or
masked box uses `"unread"` and leaves the amount absent. If both amounts are
unread, use empty `key_boxes` with both statuses recorded. A missing amount
without verified blank evidence stays open too. A Box 1 `"blank"` stays open
when the same W-2 reports Box 2, 3 or 5: read Box 1 again, and record a
verified zero as the amount `0`.

Record the employee's Social Security tax withheld from Box 4 as
`key_boxes.ss_tax_withheld` and Medicare tax withheld from Box 6 as
`key_boxes.medicare_tax_withheld`. Preserve the full Box 6 amount, including
any Additional Medicare Tax, for Form 8959. These amounts stay with this
employee and employer. Record zero when verified and leave unread amounts
absent. Federal income tax withholding comes from Box 2.

A blank on Form W-2c, however the form type is spelled, can mean the original
amount was unchanged, and the tie-out refuses it. Reconcile
the correction with the original Form W-2 before recording the effective
amounts once. Keep the other document's coverage record and explain why its
amounts were not added again.

Record the verified employer EIN from Box b as `source_form.employer_ein` on
each W-2, using the printed `NN-NNNNNNN` format. Leave the field absent when
Box b is unreadable and review the original before claiming an excess Social
Security credit. Keep the reviewed employer name in `payer`.
If tie-out flags related employer names with different EINs, re-read Box b on
both original W-2s. Correct any transcription error and rebuild the ledger.
If the EINs are genuinely different, record `input.excess_ss_employers` with
`material_evidence --outcome reviewed`, binding `parse_ledger.json`, each
flagged W-2 and every W-2 the refusal says to keep as `--input` files. Rerun tie-out after recording the review.
Record Tier 1 railroad retirement tax withheld as
`key_boxes.rrta_tier1_tax_withheld` when present in the covered-year W-2's
railroad reporting box. The amount triggers a manual Schedule 3 line 11 review.

For local manual recovery, inspect the original Box 1 or 2 and confirm its
owner against the record's `person`. Transcribe only the verified amount or
blank into that person's existing source record. Remove an `unread` status
when entering its amount. Rebuild the ledger, recompute the return and rerun
tie-out. Use the existing masked identification conventions throughout.
[IRS General Instructions for Forms W-2 and W-3](https://www.irs.gov/instructions/iw2w3).

### 1099-NEC Extraction
Must capture: TY2025 boxes 1-7; TY2026 boxes 1a-1d and 2-7, including every state row.
TY2026 box1a is total compensation, including 1b cash tips and 1d qualified
overtime premium; 1c is TTOC, not money. Federal withholding remains box4;
state tax/state ID/state income remain 5/6/7. Preserve source amounts once
and follow the [tip/overtime source review](tips-overtime.md).

### 1099-DIV Extraction
Must capture: Boxes 1a (ordinary), 1b (qualified), 2a (capital gain distributions), 2b (unrecaptured section 1250 gain), 2d (collectibles gain), 5 (Sec 199A), 12 (exempt-interest dividends), 14-16 (state, state tax withheld). Read boxes 2b and 2d on standalone and consolidated statements. They are already included in box 2a. For CA, obtain the annual fund supplement and follow [California investments](california-investments.md).
Note: Foreign tax paid in Box 7 → feeds Form 1116

### 1099-B Extraction
Must capture: Proceeds, cost basis, wash sale adjustments, gain/loss, box 3 (collectibles or QOF indicators), boxes 14-16 (state, state tax withheld).
Count transactions with the collectibles
box marked in `collectibles_transactions` for the Schedule D worksheet review.
For TY2023-2024 crypto, also capture boxes 1a-1g, 2-13, 14-16 and CUSIP, keeping blanks explicit.
Use the reviewed 1099-B input table in `docs/contributing/crypto-accounts.md`:
an older summary that omitted a special-treatment box must be re-read from
the form before `/tax-parse-crypto` can route it.
Note Box type (Form 8949 checkboxes):
A (short-term, basis reported to IRS), B (short-term, basis NOT reported to IRS),
C (short-term, no 1099-B received),
D (long-term, basis reported to IRS), E (long-term, basis NOT reported to IRS),
F (long-term, no 1099-B received)
Foreign brokerages: typically Box B or E (basis not reported to IRS)

### 1099-DA Extraction
Must capture, per transaction: boxes 1a-1i (blank stays blank), 2, 3a, 3b, 4-6, 8, 9, 11a-11c, 14-16 and CUSIP.
From TY2025 a digital asset never uses C/F: `/tax-parse-crypto` routes each
transaction G-L from these boxes, so never pick its Form 8949 box here.

### 1099-R Extraction
Must capture: Boxes 1, 2a, 2b, 4, 5, 7 (distribution code), 14-19 (state and local tax withheld, state, locality, distributions)
Code G = direct rollover (not taxable but report on 1040 line 5a)
Code 1 = early distribution (10% penalty unless exception)

### Form 3921 (ISO exercise)
One 3921 PER EXERCISE — a combined annual PDF holds several.
Must capture (PER exercise): Box 1 (grant date), Box 2 (exercise date),
Box 3 (exercise price PER SHARE), Box 4 (FMV PER SHARE on exercise date),
Box 5 (number of shares), corporation name.
The AMT bargain element is (box 4 − box 3) × box 5 per exercise — the
ledger row in step 6.5 explains held-vs-sold and the per-FILE record rule.


### HSA (1099-SA + 5498-SA)
1099-SA: Boxes 1 (distributions), 2 (earnings on excess), 3 (distribution code)
5498-SA: Boxes 1 (contributions), 2 (employer contributions)
