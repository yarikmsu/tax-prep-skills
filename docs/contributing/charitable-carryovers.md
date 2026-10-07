# Charitable vintage accounting

AA35 adds accounting for **reviewed carryover worksheets**, not an automatic
Pub. 526 worksheet generator. The federal and CA engines accept their own
`charitable_prior_ledger` and `charitable_worksheet`. A worksheet is tied to
the covered year, federal AGI, jurisdiction and deduction election. It supplies
the charitable candidate before the engine's overall itemized reduction.
The engine still computes the deduction election and refuses disagreement.

The prior ledger lives in `prior_year.charitable_carryforward_ledger.federal`
or `.CA`. Its shape is:

```json
{
  "as_of_year": 2024,
  "verified": true,
  "worksheet_ref": "Reviewed prior carryover worksheet",
  "entries": [{
    "id": "gift-a-federal",
    "origin_year": 2023,
    "category": "cash_public",
    "jurisdiction": "federal",
    "expires_after_year": 2028,
    "eligibility_verified": true,
    "source_ref": "Prior worksheet vintage A",
    "original_amount": 12000,
    "used_amount": 2000,
    "lapsed_amount": 0,
    "remaining_amount": 10000
  }]
}
```

Amounts and years in this example are synthetic worksheet inputs, not defaults.
`original_amount = used_amount + lapsed_amount + remaining_amount` for every
row. IDs are unique within a jurisdiction. Categories are `cash_public`,
`cash_other`, `noncash_appreciated_public`, `noncash_other`, `conservation`.
Categories classify the evidence; they never select an inferred tax rate.
Expiry comes from the reviewed worksheet and covered-year law, including
exceptions, not from an assumed age of an aggregate. `used_amount` is
cumulative absorption under the charitable rules, including hypothetical
absorption in standard-deduction years; `lapsed_amount` records expiration or
permanent disallowance, with the reason documented in the worksheet.

The current worksheet is an object containing:

- `tax_year`, `jurisdiction`, `agi`, `itemizes` (boolean), `verified: true`.
- `worksheet_ref`, `law_source_url` (authoritative under the cache's source
  policy) and `law_source_year` matching the year being computed. Verify the
  live source actually names that year before recording it.
- `new_entries`: unconsumed rows in the above shape, originating this year.
  Their originals must reconcile to source `charitable_cash` and
  `charitable_noncash`, respectively. `cash_*` is cash; the other categories
  are noncash. No gifts means an explicit empty list.
- `allocations`: exactly one `{id, used_this_year, lapsed_this_year}` per
  opening or new vintage, including zeros. Resolve category priority and
  oldest-vintage ordering in the worksheet; do not add prior balances to
  current gifts and apply the current-year-only formula.
- `deductible`: the charitable itemized candidate, matching total absorption
  allocated to vintages. Standard-deduction absorption is recorded here as
  the hypothetical candidate; the election controls whether it is claimed.
- If there is no opening ledger, `no_opening_carryover_confirmed: true` is
  required. Missing worksheets are not this confirmation.

Reviewed sources: [IRS Publication 526 (2025)](https://www.irs.gov/publications/p526)
and [FTB Schedule CA instructions (2025)](https://www.ftb.ca.gov/forms/2025/2025-540-ca-instructions.html).
These establish why current-gift-only formulas cannot resolve a carryover and
why CA requires independent accounting. Fetch each computation's covered-year
instructions; these links do not authorize copying TY2025 rules into later years.

Save the returned `charitable_ledger` in the federal `schedule_a` or
`schedule_ca_itemized`, together with federal `inputs` or CA
`charitable_inputs`. Keep these sections even when taking the standard
deduction or when every remaining balance is zero. The derived-lines gate
reproduces each settlement against the opening profile, actual AGI and election.
Runtime files use the existing secured writers and remain local-only.

Rollover copies verified closing ledgers without resetting origin years. It
retains zero rows as audit evidence. An aggregate with no verified ledger,
inconsistent totals, missing opening evidence or a filing-status transition
becomes `verified: false`, requiring manual resolution from worksheets and an
explicit `[charitable_carryovers]` answer. Approving a rollover category cannot
clear that marker. Do not declare that a missing worksheet establishes zero.
An AGI-changing scenario must obtain a newly reviewed worksheet; it cannot
reuse the baseline allocation. Other states retain their manual worksheet
contracts; CA support does not assert their conformity.
