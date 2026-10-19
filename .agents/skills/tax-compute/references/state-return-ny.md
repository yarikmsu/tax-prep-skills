# tax-compute Step 10 — New York (Form IT-201)

New York State, New York City and Yonkers in depth for a **full-year
resident** — see what this module does NOT compute. Read this instead of
`references/state-return.md`'s California branch when `tax_law.state.code ==
"NY"`; the cache-vs-taxpayer cross-check at the top of that file runs FIRST and
is not repeated here.

## Contents

- [Before anything: who this computes for](#before-anything-who-this-computes-for)
- [10.NY.1 — New York AGI (lines 19-33)](#10ny1--new-york-agi-lines-19-33)
- [10.NY.2 — Deduction and taxable income (lines 34-38)](#10ny2--deduction-and-taxable-income-lines-34-38)
- [10.NY.3 — New York State tax (lines 39-46)](#10ny3--new-york-state-tax-lines-39-46)
- [10.NY.4 — City, Yonkers and the MCTMT (lines 47-58)](#10ny4--city-yonkers-and-the-mctmt-lines-47-58)
- [10.NY.5 — Sales or use tax (line 59)](#10ny5--sales-or-use-tax-line-59)
- [10.NY.6 — Settlement (lines 60-82)](#10ny6--settlement-lines-60-82)
- [10.NY.7 — PTET and next year's estimates](#10ny7--ptet-and-next-years-estimates)
- [What this module does NOT compute](#what-this-module-does-not-compute)

---

## Before anything: who this computes for

**FULL-YEAR RESIDENTS ONLY.** A part-year or nonresident files Form IT-203
against a different base, and nothing in the engine refuses one. The capability
gate blocks `moved_states_this_year` before this step is reached; residency
itself is decided by `docs/contributing/residency-guardrails.md`, and a
taxpayer who is a New York statutory resident while domiciled elsewhere is a
resident here.

Write everything below into `result.json`'s **`new_york_it201`** section. Never
reuse or overload `california_540` — they are not interchangeable, and
`yoy_diff`'s mover list is keyed per section for exactly that reason. Prune the
sub-blocks whose `_include_if` does not hold, the way every other section is
pruned.

## 10.NY.1 — New York AGI (lines 19-33)

Run `compute_ny_agi(federal_agi, additions, subtractions, filing_status,
limits, it225_invalid_codes)` with `limits = state.subtractions` and
`it225_invalid_codes = state.it225.codes_not_valid_on_it201`.

`additions` carries the four named lines plus `it225`, a list of
`{"code", "amount"}`; `subtractions` carries its four named lines plus
`pension_annuity` (a list of PER-TAXPAYER entries),
`college_savings_contributions` and `it225`.

- **The IT-225 direction is CROSS-CHECKED, not inferred.** Additions are
  Schedule A codes (`A-`, or `EA-` when they arrive through a partnership,
  S corporation or estate); subtractions are Schedule B (`S-`, `ES-`). Hand
  each list to the side it belongs on and the prefix checks the claim — a
  subtraction filed as an addition OVER-states the tax and an addition filed
  as a subtraction UNDER-states it. **A negative amount REFUSES** rather than
  flipping sides: that is the same confusion arriving by a signed figure. New
  York enters both directions as positive figures and lets the SCHEDULE carry
  the sign.
- **W-2 box 14 is where two of the commonest additions live.** Public-employee
  **414(h)** retirement contributions and **IRC §125** cafeteria-plan amounts
  for NYS and NYC public employees are ADD-BACKS. `/tax-parse` records box 14;
  read it rather than asking.
- **The pension and annuity exclusion is PER TAXPAYER and does not pool.**
  "Neither of you can claim any unused part of your spouse's exclusion", so
  pass one entry per person. Entitlement is an ASSERTION you must make
  explicitly and is REFUSED if unstated — the age gate, the part-year rule for
  someone who turns 59½ during the year, and a beneficiary's claim on a
  decedent's entitlement are judgements about facts the engine is not given,
  and granting it unearned under-states the tax. **A government pension routed
  here refuses too**: those are fully excluded on line 26, and capping one
  would silently limit a full exclusion.
- **Line 33 may be NEGATIVE** and that is an ordinary return. A Schedule C
  loss, a K-1 loss or an NOL carryforward reaches it routinely.

Record `lines`, `total_additions`, `total_subtractions` and every note. Record
the IT-225 CODES claimed, not just the totals, in `additions.it225_codes` /
`subtractions.it225_codes` — `/tax-review` cannot audit a lump sum.

## 10.NY.2 — Deduction and taxable income (lines 34-38)

**New York's itemized election is INDEPENDENT of the federal one** — "the Tax
Law allows you to itemize your deductions for New York State income tax
purposes whether or not you itemized your deductions on your federal income
tax return."

Run `compute_ny_itemized(itemized_before_adjustment, charitable_contributions,
nyagi, filing_status, reduction, charitable_limit, college_tuition_deduction,
standard_deduction=..., mfs_spouse_itemizes=...)` with `reduction =
state.itemized_reduction`, `charitable_limit = state.charitable_limit` and
`standard_deduction = state.standard_deduction[status]`. It returns
`use_itemized`, which decides `deduction_regime`, and `deduction`, which is
line 34.

**`standard_deduction` is REQUIRED and keyword-only.** It used to be optional,
and omitting it returned `deduction` holding the ITEMIZED total — a figure that
reads as elected, under-stating the deduction for any taxpayer whose NY standard
deduction is larger. If `state.standard_deduction` is not in the cache, STOP and
re-run `/tax-setup`; do not call this with a guess.

- **`itemized_before_adjustment` is IT-196 lines 1-45 and is an INPUT.** It is
  computed on federal rules **as they existed before the TCJA** — the 2%
  miscellaneous floor and the pre-2018 treatment of items the federal return no
  longer allows are live. **The federal Schedule A total is NOT a substitute.**
  Building it from source figures is a named non-goal below; until it exists,
  hand over the figure the taxpayer's own IT-196 shows and say so in
  `answers.md`.
- §615 imposes **two separate** high-AGI reductions and a **separate**
  charitable-contribution limitation at very high NYAGI. All three are in the
  function; none is optional.
- The college tuition ITEMIZED DEDUCTION (IT-196 line 48) and the college
  tuition CREDIT (IT-201 line 68) are **mutually exclusive**. Pass the
  deduction here OR claim the credit in 10.NY.6, never both — the settlement
  step re-checks this, because the two live in different functions and neither
  would see the other without being told.

Then, in the form's own arithmetic:

- `line_35_after_deduction` = line 33 − line 34
- `line_36_dependent_exemptions` = the §616 per-dependent amount
  (`state.dependent_exemption`) × the number of dependents. **DEPENDENTS ONLY.**
  §616 grants an exemption for each exemption allowed under **IRC §151(c)** —
  the dependent subsection. §151(b), the taxpayer and spouse, is not
  referenced: New York has NO personal exemption for either, and
  `state.personal_exemption.amount` is 0 as a positive declaration rather than
  an omission.
- `line_37_taxable_income` = line 35 − line 36, **floored at zero** ("if line 34
  is more than line 33, leave blank").
- `line_38_taxable_income` = line 37.

## 10.NY.3 — New York State tax (lines 39-46)

Run `compute_ny_tax(taxable_income, nyagi, filing_status, brackets,
schedule_by_status, recapture, tax_table)` with `brackets = state.brackets`,
`schedule_by_status = state.bracket_schedule_by_status`, `recapture =
state.tax_table_benefit_recapture` and `tax_table = state.tax_table`.

**The §601(d-x) benefit recapture is INSIDE that function and is not yours to
skip.** Above the entry threshold, plain bracket math UNDER-states New York tax
by the whole benefit of the graduated schedule. An unfetched recapture block
REFUSES; re-run `/tax-setup` rather than computing without it.

**`state.brackets` is keyed by §601 SCHEDULE, not by filing status** — (a) for
married-joint and the surviving spouse, (c) for single and married-separate,
(b) for head of household. Pass the whole dict and let the function map it;
never index it yourself. Anywhere else that needs a flat list for a state, the
one accessor is `tax_computations.state_brackets_for_status`.

Two methods, selected on BOTH figures: at or below the entry threshold AND
under the table's published taxable income, New York's TABLE applies, and it is
the schedule at each row's MIDPOINT, rounded. The two answers differ by up to
about two dollars in either direction. Record `regime` and `schedule` as
`tax_regime` and `tax_schedule`, and `recapture_amount` as `benefit_recapture`.
**Carry every entry of its `notes` into the state section's `notes`, and tell
the user.** A note is how an under-stating degrade reaches anyone — an
unfetched `state.tax_table.table_bases` computes, a dollar low on some rows,
and says so only there.

Then `compute_ny_state_tax_total(tax_before_credits, household_credit,
resident_credit, other_nonrefundable_credits, net_other_state_taxes)`:

- `household_credit` comes from `compute_ny_household_credit(federal_agi,
  filing_status, exemptions, table, can_be_claimed_as_dependent)` with
  `table = state.credits.household.table`. It is keyed on **FEDERAL** AGI, and
  the table has three sub-tables: single, married-joint/head-of-household/
  surviving-spouse, and married-separate.
- **Line 45's net other New York State taxes (IT-201-ATT) are ADDED AFTER the
  nonrefundable credits are capped at line 44.** A credit cannot reduce them,
  so folding them in earlier would let one cancel the other. `credits_lost`
  records what the cap wasted; surface it.
- Line 46 is the state total. **Line 39 is not**, and neither line 44's
  component nor line 45's is derivable from `compute_ny_tax`'s inputs.

## 10.NY.4 — City, Yonkers and the MCTMT (lines 47-58)

Skip the whole block only when the taxpayer is neither a City nor a Yonkers
resident and has no Yonkers earnings and no MCTD self-employment — and say so
in `notes`, rather than leaving the section silent.

**New York City** — gated on `flags.nyc_resident`, which is the CITY-TAX
SWITCH rather than a block: the rule that used to stop the return on it was
deleted when this branch took on the City block. City residency is the
STATE's test applied to the city, so city days are a subset of state days and
the reverse cannot happen; there is no `days_present_by_city` field, so this
stays the taxpayer's assertion.

Run `compute_nyc_taxable_income(state_taxable_income,
charitable_gifts_trust_deducted, nyc_resident_spouses, filing_status,
nyc_resident_full_year)`. City
taxable income is the STATE's **except** that a Charitable Gifts Trust
deduction taken for the state is added back.

**On a joint return `nyc_resident_spouses` is REQUIRED**, and it comes from
`flags.nyc_resident_spouses` (0, 1 or 2), which `/tax-setup` asks as its own
question — `flags.nyc_resident` answers whether the household owes city tax,
not whether BOTH spouses are residents. **A joint return with only ONE city
resident REFUSES** rather than taxing the household as residents: that return
is computed on line 51, which this engine does not do. Omitting the argument
refuses too, because assuming two over-states the city tax by the non-resident
spouse's entire share.

**So does a City resident for only PART of the year.** A count of ZERO refuses:
reaching this step means `nyc_resident` is true, so zero whole-year spouses is a
couple who both lived in the City for part of the year. Every other filer passes
`nyc_resident_full_year` from `flags.nyc_resident_full_year`, and false or
absent refuses. A part-year City return is IT-360.1 on line 50, which is not
modeled, and taxing the whole year's income over-states the City tax.

The NYC household credit REUSES `compute_ny_household_credit` against the
city's own tables (4/5/6 rather than 1/2/3) — the same function, a different
table, verified against the instructions rather than assumed.

**Line 47a, the City resident tax** —
`compute_nyc_resident_tax(nyc_taxable_income, filing_status,
state.nyc_tax.brackets, state.bracket_schedule_by_status,
state.nyc_tax.tax_table)`, and pass its `tax` to
`compute_city_and_yonkers_total` as `nyc_resident_tax`; its `notes` go where
`compute_ny_tax`'s do. Below the fetched table
ceiling it reads the City TAX TABLE, which is the schedule at each row's
midpoint on the City's OWN rows (their bottom differs from the State table's);
at or above it, the rate schedule. There is no New York AGI condition and no
recapture. An unfetched schedule or table REFUSES: never pass the City tax as
zero, and never work it out by hand.

**MCTMT** — `compute_mctmt(zone_1_net_earnings, zone_2_net_earnings, mctmt)`
with `mctmt = state.mctmt`. **TWO ZONES, which are GEOGRAPHY and not income**,
carrying DIFFERENT rates, and the threshold is a per-zone **CLIFF**: over it,
the WHOLE amount is taxed, not the excess. Neither zone's rate may stand in for
the other's, and an unfetched rate for a zone with earnings refuses.

**Yonkers** — TWO DIFFERENT TAXES with two different populations, and a
taxpayer can owe either without the other, so they have two flags:
`flags.yonkers_resident` drives line 55's resident income tax surcharge, and
`flags.yonkers_nonresident_earnings` drives line 56's nonresident earnings tax
for someone who earns in Yonkers while living elsewhere. Both are
required-present in the capability gate for the same reason `nyc_resident` is:
absence reads as False and drops a real liability.

**Line 55, the resident surcharge** — when `flags.yonkers_resident`, run
`compute_yonkers_resident_surcharge(line_46, filing_status,
state.yonkers_tax, worksheet_credits, star_reconciliation,
yonkers_resident_spouses, yonkers_resident_full_year,
college_tuition_deduction_claimed)` and pass its `tax` to
`compute_city_and_yonkers_total` as `yonkers_resident_surcharge`. `line_46` is
`compute_ny_state_tax_total`'s `line_46_total_state_tax`. It is the
IT-201-I worksheet: line 46 less the refundable credits it lists, net of the
STAR reconciliation amount (Form IT-119 line 3), times the fetched rate.
`worksheet_credits` is keyed by the worksheet's own lines — `empire_state_child`
(IT-213 line 9), `real_property_tax` (IT-214 line 20),
`state_child_and_dependent_care` (IT-216 line 14, the State's credit alone,
NOT the settlement's line-64 `child_and_dependent_care`), `earned_income`
(IT-215 line 16, or 17 where IT-215 says so), `noncustodial_parent_eic`
(IT-209), `college_tuition` (IT-272 line 5 or 7, only where the credit was
elected), `nyc_school_tax` (lines 69 and 69a together) and `other_tax_credits`
(IT-201-ATT line 13). **Pass every one the return claims**: an omitted credit
leaves the base too high and over-states the surcharge. `worksheet_credits`
and `star_reconciliation` are both REQUIRED, `{}` and `0` where the return has
none, the STAR amount coming from `answers.md`; pass the same
`college_tuition_deduction_claimed` the settlement takes. A joint return passes
`flags.yonkers_resident_spouses`, every other one
`flags.yonkers_resident_full_year`; one resident spouse (special condition
Y1), a part-year resident or an unanswered question REFUSES.

**Line 56, the nonresident earnings tax** — when
`flags.yonkers_nonresident_earnings`, run
`compute_yonkers_nonresident_earnings_tax(earner, filing_status,
state.yonkers_tax, line_46, worksheet_credits, star_reconciliation,
yonkers_resident=flags.yonkers_resident, college_tuition_deduction_claimed)`
and pass its `tax` as `yonkers_nonresident_earnings_tax`. `earner` is the one
Form Y-203 — `{"wages", "self_employment", "startup_ny_wages"}`, each already
allocated to Yonkers — from `answers.md`. The form is in whole dollars, the
exclusion comes off the fetched table, and line 6 carries the tax LIMITED to
the resident surcharge this return would owe, which is why it takes the
worksheet's inputs too; where the limit applies, submit the calculation with
Form Y-203 as a separate schedule. A household with a Yonkers resident REFUSES
here (a part-year resident or special condition Y1), and so does a JOINT
return: its limit can be read as each spouse's surcharge figured as if they
had filed separately, which is not computed.

Then `compute_city_and_yonkers_total(...)`. **Line 58 adds the MCTMT and both
Yonkers taxes AFTER the two city credit caps** — the line-45 pattern a second
time.

Pass it the household's three flags, `nyc_resident`, `yonkers_resident` and
`yonkers_nonresident_earnings`, straight from `taxpayer_profile.json`; they
are required. A flagged tax you did not pass is REFUSED, not read as zero: run
the function above that computes it and pass its tax. A tax passed for a
household whose flag is false is refused too — fix the flag or the amount,
never drop the argument to get past it.

Lines 47a, 55 and 56 ship `null`, not 0. Write each one only from the function
that computes it, and beside it, as `<line field>_method` (e.g.
`line_47a_nyc_resident_tax_method`), the `method` that same call returned; an
unflagged one stays null. `compute_city_and_yonkers_total` adds them into line
58 but returns none of the three, so writing its dict cannot overwrite one.
At Step 12.7 the tie-out FAILS a flagged line still null or absent, a flagged
line without its function's `method` — a 0 typed over the
null reads exactly like a computed one — and a line 58 below line 54 plus lines
54e, 55 and 56, whichever way line 58 was assembled. A household the function
refuses still owes the tax: prepare it by hand, never enter 0. Those households
block at Step 0.7 as `ny_city_or_yonkers_household_unmodeled`; acknowledged, the
tax goes on line 50, 51 or 57 by hand and the tie-out stops requiring 47a, 55
or 56 for that household.

## 10.NY.5 — Sales or use tax (line 59)

**ASK. Do not write 0.** New York reports sales and use tax on untaxed
out-of-state and online purchases **on the income tax return**, and it changes
the amount owed. `line_59_sales_or_use_tax` ships `null` on purpose: a silent 0
asserts an answer nobody gave.

Ask the taxpayer whether they made untaxed out-of-state or online purchases
this year. Record the answer and its arithmetic in `answers.md`. If they cannot
state one, leave the field null, add the line to `meta.limitations`, and say in
`notes` that the return is incomplete on that line — never that it is zero.

## 10.NY.6 — Settlement (lines 60-82)

`line_60_total_taxes` = line 46 + line 58 + line 59 (0 where line 59 is null,
with the limitation recorded above).

Run `compute_ny_settlement(total_tax, refundable_credits, state_withholding,
city_withholding, yonkers_withholding, estimated_payments,
applied_to_next_year, estimated_tax_penalty, other_penalties,
college_tuition_deduction_claimed)`.

**EVERY remaining New York credit is REFUNDABLE** and belongs here with the
payments, not above line 44. `refundable_credits` is a CLOSED set keyed by
name; an unknown key refuses rather than being silently dropped.

- `state_withholding` totals NY tax withheld from W-2 and 1099/W-2G sources;
  `city_withholding` and `yonkers_withholding` total the corresponding locality's
  withholding from those sources, including 1099-R/W-2G box 17. Confirm each
  source jurisdiction by label. IT-201 lines 72, 73 and 74 and their ledger
  categories stay separate. Prepare IT-2 and IT-1099-R and attach the required
  1099-G/W-2G statements per the covered-year instructions.
- The estimated tax penalty **reduces an overpayment** where there is one and
  is **included in the amount owed** where there is not. It is never a separate
  bill beside line 80. Reporting it beside the amount owed double-counts it and
  reporting it nowhere loses it.
- Pass `college_tuition_deduction_claimed=True` when 10.NY.2 took the
  deduction, so claiming the credit as well is refused.
- An `applied_to_next_year` larger than the overpayment refuses: line 78 is
  line 77 less line 79, so a larger figure manufactures a refund out of
  nothing.

## 10.NY.7 — PTET and next year's estimates

**Only when the taxpayer is an owner of an electing pass-through entity.**

`compute_ny_ptet_owner(nys_ptet_credit, nyc_ptet_credit, it225_additions)`.
**The credit and its IT-225 add-back are an EXACT PAIR**: code **A-219** is
"the amount of the credit claimed on Form IT-653, **line 1**" and **A-222** the
amount on **line 2**. They do not combine, and adding both back under one code
is a different return from the one the form describes. Claiming the credit
without the addition under-states New York tax by the whole of the credit, so
the add-backs are DERIVED here and checked against whatever you assembled in
10.NY.1 — in both directions. Feed `required_it225_additions` back into the
additions before finalising line 33 if they were not already there, and the
`refundable_credit` into `refundable_credits.other_refundable`.

`compute_ny_required_annual_payment(current_year_tax, prior_year_tax,
prior_year_nyagi, filing_status, safe_harbor)` with `safe_harbor =
state.estimated_tax_safe_harbor`. **Form IT-2105, EQUAL QUARTERS** — April,
June, September and the following January, one voucher covering NYS, NYC,
Yonkers and the MCTMT together, which California's separate 540-ES does not.
The higher prior-year share is keyed to **NEW YORK** adjusted gross income, not
federal, with married-filing-separately at its own threshold; **a prior-year
tax handed over without its NYAGI REFUSES**. With no prior year at all the
current-year share is the only floor and the function SAYS so — a first-year
filer genuinely has none, and a taxpayer who simply has not supplied one is in
a different position you have to resolve.

## What this module does NOT compute

Each of these goes on `meta.limitations` verbatim — a named gap, never silence:

- **Part-year and nonresident returns (Form IT-203).**
- **Special condition Y1** — a joint return with one Yonkers resident spouse
  and one nonresident. Refused.
- **Form Y-203 on a joint return** — its limit can be read as each spouse's
  surcharge figured as if they had filed separately. Refused.
- **Part-year city and Yonkers lines 50 and 57**, which belong to IT-360.1.
- **City-level day counts** — `residency.days_present_by_state` is keyed by
  STATE, so City residency stays the taxpayer's assertion.
- **IT-196 lines 1 through 45** — the New York itemized deduction BEFORE the
  §615 adjustment, on pre-TCJA federal rules. Taken as an input.
- **IT-201 line 45's components** (IT-201-ATT, the IT-230 lump-sum tax among
  them). Taken as an input.
- **The AMOUNT of each refundable credit.** IT-213, IT-216, IT-215, IT-214 and
  IT-272 each carry their own worksheet; the settlement takes the figures and
  settles the return around them. Where each one LANDS is settled; what each
  one IS, is not. **The three earned-income credits — IT-215 (line 65),
  IT-209's noncustodial parent credit (line 66) and the NYC credit (line 70) —
  are singled out** by the capability gate's `earned_income_credit` rule and by
  a tie-out advisory, because they are the state half of the one federal line
  — line 27 — that nothing computes either, and a filer told about the federal
  gap alone is told the wrong size of it. This paragraph already asked YOU to
  put each credit's amount on `meta.limitations`; the rule is what puts the
  earned-income ones there whether anyone remembers or not. Each of the three
  has its own form and its own eligibility test: **never copy one line's figure
  onto another, and never copy the federal figure onto any of them.**
- **Sales or use tax on line 59** where the taxpayer cannot state one.
- **Estates and trusts**, which §601(c) also covers. An IT-201 never is one.
