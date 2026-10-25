# Standard form extraction

### W-2 Extraction
Must capture: Boxes 1-8, 10-11, 12 (all codes), 13, 14 (14a-14b from TY2026; every label, SDI, PFL and UI among them), 15-20 (state and local wages, tax withheld and locality, every row)
Check: Box 12 codes (AA=Roth 401k, C=life ins, D=401k, DD=health, W=HSA)
For TY2026, read TP (cash tips), TT (qualified overtime premium), TA (employer
Trump-account contributions), 14a (other labels) and 14b (TTOC). TP/TT feed
the [tip/overtime source review](tips-overtime.md); TA feeds neither deduction.

### 1099-NEC Extraction
Must capture: TY2025 boxes 1-7; TY2026 boxes 1a-1d and 2-7, including every state row.
TY2026 box1a is total compensation, including 1b cash tips and 1d qualified
overtime premium; 1c is TTOC, not money. Federal withholding remains box4;
state tax/state ID/state income remain 5/6/7. Preserve source amounts once
and follow the [tip/overtime source review](tips-overtime.md).

### 1099-DIV Extraction
Must capture: Boxes 1a (ordinary), 1b (qualified), 2a (cap gain dist), 5 (Sec 199A), 12 (exempt-interest dividends), 14-16 (state, state tax withheld). For CA, obtain the annual fund supplement and follow [California investments](california-investments.md).
Note: Foreign tax paid in Box 7 → feeds Form 1116

### 1099-B Extraction
Must capture: Proceeds, cost basis, wash sale adjustments, gain/loss, boxes 14-16 (state, state tax withheld)
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
