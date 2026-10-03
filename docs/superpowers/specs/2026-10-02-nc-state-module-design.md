# 2026-10-02 — NC state module (Form D-400) — design spec

Epic Z, item Z4. Companion guide: `docs/contributing/state-module-playbook.md`;
template: `docs/superpowers/specs/TEMPLATE-state-module.md`.

**Answers are SOURCE-VERIFIED or marked UNVERIFIED.** Primary source: the TY2025
NCDOR D-401 instructions ("D-401 p.N" below) and the TY2025 D-400 form face
(line arithmetic). Statutes read live on ncleg.gov 2026-10-02. A blank or
UNVERIFIED row names what to read next; it never means "does not apply".
Figures quoted in §2-§3 are CITATION EVIDENCE for the hand-computed tests, not
template seeds: every one ships `None` and is fetched (AGENTS.md §2).

## 1. Scope

- **State / form:** NC / Form D-400 with Schedule S (Parts A-B) and Schedule A,
  full-year resident.
- **Residency scope:** full-year resident ONLY. Schedule PN (line 13 taxable
  percentage) stays gated by `moved_states_this_year` at C1.
- **Local taxes in scope:** none exist — NC has no county/city income tax
  (UNVERIFIED by statute; the D-401 names no local income tax anywhere, and
  D-400TC Part 1 says "No credit is allowed for income taxes paid to a city,
  county …" of ANOTHER state, which is about other states, not NC).
- **Regime:** `type: "graduated_income"` with ONE band per year — the Z4
  backlog shape. Not a new `type`; the flat rate is a one-band schedule.

## 2. Questionnaire answers (playbook step 0)

| # | Question | Answer + citation |
|---|---|---|
| 1 | Starting point | **Federal AGI** — D-401 p.12 Line 6: "The starting point for determining North Carolina taxable income is federal adjusted gross income." **But AGI is read under the IRC as of January 1, 2023** (D-401 p.17, Sch S intro): OBBBA changes "DO NOT apply … unless North Carolina conforms". Schedule S "provides a list of all available NC adjustments", so line 6 = federal 1040 line 11 and every non-conformity travels through Sch S. |
| 2 | Additions / subtractions | **Schedule S.** Part A lines 1-16 (D-401 p.17-18), Part B lines 17-41 (p.18-20). v1-relevant: A1 other-state muni interest (incl. exempt-interest dividends not from NC obligations); B17 state/local refund in fed AGI; B18 US-obligation interest; B19 taxable Social Security + RR Tier 1/2; B20 **Bailey** retirement; B21 uniformed-services retirement. **HSA, IRA, 401(k)/deferral, alimony: NO Sch S line exists → NC conforms** (Sch S is exhaustive per p.17). Business items (A3 bonus 85%, A4 §179 85%, B23/B24 20% recovery, A7 federal NOL / B39 NC NOL, A14/B38 Taxed-PTE) are OUT for v1 (§8). |
| 3 | Credits | **No personal exemption or exemption credit.** Dependents act through the **child DEDUCTION** (line 10, below-AGI), not a credit. Credits = D-400TC (line 16): Part 1 credit for tax paid to another state (p.22-23), Part 2 historic rehab, NC-478 business credits. **No EITC piggyback, no renter's credit** in the D-401 credit list. |
| 4 | Local income taxes | None (see §1). |
| 5 | Surtaxes | None found in the D-401 or G.S. 105-153.7. |
| 6 | Estimated payments | **Form NC-40, EQUAL quarters**: G.S. 105-163.15 "twenty-five percent (25%) of the required annual payment", due Apr 15 / Jun 15 / Sep 15 / Jan 15 (ncdor.gov estimated-income-tax page). Required annual payment = lesser of **90%** current / **100%** prior-year tax; farmers/fishermen 66⅔%, single Jan 15 installment. **No 110% high-income tier**: §105-163.15(d) defines the required annual payment as the lesser of 90% of this year's tax and 100% of the preceding year's, with no AGI threshold (re-read in full before slice 2). Required if tax less withholding and credits ≥ **$1,000** (D-401 p.9); interest-free de minimis is pegged to **IRC §6654(e)** (statute). Jan 15 skippable if return filed and paid by Jan 31 (ncdor.gov). Penalty is INTEREST (Form D-422), line 26e. |
| 7 | SDI / PFL-style items | None. No NC payroll disability/PFL program appears in the D-401. |
| 8 | Withholding-form analogue | **Form NC-4** (NC-4EZ, NC-4P for pensions, NC-4 NRA) — ncdor.gov. Default with no NC-4 on file: "Single" with no allowances. |
| 9 | Extension | **Automatic with the federal extension** — D-401 p.7: a taxpayer granted an automatic federal extension "will be granted an automatic state extension", but MUST fill in the "Federal Extension" circle on D-400 p.1. Otherwise **Form D-410** by the original due date. **Neither extends payment** (p.7-8); a 90%-paid-by-due-date rule avoids the late-payment penalty (p.9). The extension is SIX months, to Oct 15 (ncdor.gov's D-410 guidance and its annual "returns with extensions are due Oct. 15" notice; G.S. 105-263 grants the automatic state extension and leaves the length to the Secretary). |
| 10 | PTET regime + window | **YES — "Taxed PTE"**, G.S. 105-154.1: "Effective for taxable years beginning on or after January 1, 2022" (ncleg.gov page text; confirm against S.L. 2022-6 as the enacting law), election "on its timely filed return" and "may not make or revoke the election after the return is filed" — **no separate prepayment window** like CA's June 15. Rate = the §105-153.7 individual rate. S-corporation election statute (G.S. 105-131.1A?) — UNVERIFIED. NOT in `PTET_WINDOWS` today. |
| 11 | Community property | NC is NOT in `capabilities._COMMUNITY_PROPERTY_STATES` — correct. |
| 12 | Itemized deductions | **Own Schedule A, independent of the federal election** (D-401 p.20: "even if you did not claim itemized deductions on your federal return"). Only five items: mortgage interest under §163(h) (Sch A line 1), real-estate tax capped **$10,000 / $5,000 MFS** (line 2), mortgage + property tax combined capped **$20,000**, combined across BOTH spouses even MFS with proration (line 4; G.S. 105-153.5(a2)), charitable under §170 (line 6), medical over **7.5% of federal AGI** (line 7c is the floor, 7d the deduction), claim-of-right repayment (line 8, its own 2%-of-AGI worksheet ≤ $3,000). **No state/local income or sales tax deduction at all.** No high-AGI phase-out. |
| 13 | Straight bracket math? | **YES.** D-400 line 15: "Multiply Line 14 by 4.25% (0.0425). If zero or less, enter a zero." No tax table, no recapture. **The rate is year-gated**: G.S. 105-153.7(a) "In 2025 4.25%", "After 2025 3.99%", and (a1) **triggers** cut it by 0.50 pts per year (floor 2.49%) from TY2027 when General Fund revenue clears a statutory threshold — so a year's rate is not knowable from the statute alone and MUST be fetched per year from NCDOR's published rate. |

**Further sharp edges found while answering:**

- **Child deduction is keyed by FEDERAL AGI (line 6), not NC income**, per
  qualifying child "for whom you were allowed a federal child tax credit"
  (D-401 p.13, IRC §24). The table has THREE keys — MFJ/QSS, HOH,
  Single/MFS — not four (D-401 p.13; G.S. 105-153.5(a1)). Bands are "Up to X"
  / "Over X - Up to Y": the upper bound is INCLUSIVE.
- **MFS standard deduction is ZERO if the spouse itemizes** (D-401 p.14 chart),
  and ZERO for anyone "not eligible for the federal standard deduction". No
  age-65/blind addition (p.14). The dependent-filer limit: G.S. 105-153.5(a)(1)
  sets the amount to zero only for a person "not eligible for a standard
  deduction under section 63 of the Code" and states no dependent limit, and
  the D-401 is silent. A dependent filer therefore gets the full NC amount on
  the statute's face; slice 3 asks NCDOR guidance before relying on it, and
  until then a dependent filer is refused (granting the full amount wrongly
  would UNDER-state, refusing costs nothing).
- **QSS ≡ MFJ** for the standard deduction and the child table. Do not map
  by reflex — it happens to hold here, and the test pins it.
- **Whole-dollar rounding** on every line (D-401 p.4). The engine rounds to
  cents (AGENTS.md §8); the result section must say which it carries.
- **TY2026 IRC-conformity trap — it cuts BOTH ways.** G.S. 105-228.90(b)(7)
  defines the Code "as enacted as of January 1, 2023, including any
  provisions … that become effective either before or after that date", so
  the 2023 Code's own TCJA sunsets DO apply to TY2026 for NC. Reusing a
  federal Schedule A figure computed under OBBBA is therefore wrong in both
  directions: the §170 0.5%-of-AGI floor and the §163(h) $750k / no
  home-equity limits make it SMALLER than NC's (OVER-states NC tax), while
  OBBBA's restored mortgage-insurance-premium interest and its permanent 60%
  cash-gift limit (the 2023 Code reverts to 50%) make it LARGER (UNDER-states).
  D-401 p.20 already asks for each Sch A line "allowed … under IRC section
  163(h)/164/170/213" — NC's own computation. So §5.3 computes NC Sch A from
  RAW inputs, never from the federal schedule, and BLOCKS only where a raw
  input cannot settle it (below). A blanket refusal would stop every TY2026
  NC itemizer with a mortgage.
- **AGI-level conformity gaps, year by year.** Line 6 is federal AGI, and an
  OBBBA exclusion that lowers W-2 box 1 lowers it with NO Sch S line for
  TY2025 (the dependent-care exclusion's raised ceiling; HSA expansions) —
  silently UNDER-stating NC tax. "Sch S is exhaustive" holds per YEAR only;
  Sch S line numbers and the conformity date are year-gated, and a year whose
  D-401 has not been read is refused rather than mapped by last year's lines.

## 3. Fetched-law block

`create_state_template("NC")`, `type: "graduated_income"`. All figures `None`
at template time; `/tax-setup` step 4 gets one WebSearch bullet per row.

| JSON path (`state.` …) | Kind | TY2025 evidence | Source |
|---|---|---|---|
| `code`, `name`, `authority`, `type` | STRUCTURAL | "NC", NCDOR | — |
| `brackets` = `[{"rate": None, "min": 0, "max": None}]` | FETCHED per year (statutory, NOT indexed, but trigger-reducible) | 4.25% (TY2026: 3.99%) | D-401 p.7; G.S. 105-153.7(a),(a1) |
| `rate_tax_year` | STAMPED by writing `state.brackets` into the year's cache, never set on its own (bias review: an attestation written apart from the rate could vouch for a carried-over one); freshness and compute refuse a mismatch | 2025 | NCDOR rate page |
| `standard_deduction.{single,mfj,mfs,hoh,qss}` | FETCHED (statutory, unindexed) | 12,750 / 25,500 / 12,750 / 19,125 / 25,500 | D-401 p.14; G.S. 105-153.5(a)(1) |
| `standard_deduction.mfs_spouse_itemizes` | FETCHED (statutory zero) | 0 | D-401 p.14 |
| `child_deduction.{mfj_qss,hoh,single_mfs}` = list of `{agi_up_to, per_child}`, last band `agi_up_to: null` | FETCHED (statutory, unindexed) — THREE keys | 7 bands each, $3,000 → $0 in $500 steps; top bands 140k / 105k / 70k | D-401 p.13; G.S. 105-153.5(a1) |
| `itemized.real_estate_tax_cap.{default,mfs}` | FETCHED (statutory) | 10,000 / 5,000 | D-401 p.20 Sch A line 2 |
| `itemized.mortgage_plus_property_tax_cap` | FETCHED (statutory; household-wide even MFS) | 20,000 | D-401 p.20 line 4; G.S. 105-153.5(a2) |
| `itemized.medical_agi_floor_rate` | FETCHED (IRC §213 by conformity) | 0.075 | D-401 p.20 line 7c |
| `itemized.claim_of_right.{repayment_threshold,agi_rate}` | FETCHED | 3,000 / 0.02 | D-401 p.20 line 8 |
| `irc_conformity_date` | FETCHED — names the Code NC's Sch A and Sch S read; a year whose date is unfetched is refused | 2023-01-01 | D-401 p.17; G.S. 105-228.90(b)(7) |
| `estimated_tax.required_threshold` | FETCHED | 1,000 | D-401 p.9 |
| `estimated_tax.{current_year_rate,prior_year_rate,farmer_rate}` | FETCHED | 0.90 / 1.00 / 0.6667 | G.S. 105-163.15 (the p.9 "90%" is the EXTENSION late-payment rule, not this); the no-prior-year-liability exception is D-401 p.15 |
| `use_tax.estimate_table` (bands on line 14) + `use_tax.top_rate` | FETCHED (only if §5's no-records path ships) | 30 bands, then line 14 × .000675 | D-401 p.26 |

**Slice 2 decisions** (the code moved, so the table above did): the band
carries `max: None` because the bracket validator requires all three keys and
the walker reads a null top `max` as shape; `claim_of_right` avoids the key
name `threshold`, which `_EXPECT_STRUCTURED` reads as per-status (a scalar
there is reported as corruption); the estimated-tax shares end in `_rate` so
`set` refuses `90` written for `0.90`; and `use_tax` is NOT seeded — §5.7
makes use tax an input, and a seeded block nobody fetches would nag
INCOMPLETE on every return. The ingest refuses a `rate_tax_year` other than
the cache's, and the freshness audit flags one.

`brackets` is a one-element LIST, not per-status: a flat rate has one schedule,
and `state_brackets_for_status` already reads a list as status-independent.
The **child table's three-key shape** is the cache's keying trap
(`docs/contributing/tax-law-cache.md`) — an accessor reading `qss` or `mfs`
directly finds nothing; map explicitly and test all five statuses. No new
`_SKIP_KEYS` beyond the descriptive keys; every `None` above is fetched, so
the freshness walker clears.

## 4. Result section

Key **`north_carolina_d400`**. Fields carry D-400 line numbers (form face
verified for TY2025): `line_6_federal_agi`, `line_7_additions` (Sch S line
16), `line_8` (6+7), `line_9_deductions` (Sch S line 41), `line_10a_children`,
`line_10b_child_deduction`, `line_11_deduction` + `line_11_method`
(`standard`|`itemized`), `line_12a` (9+10b+11), `line_12b` (8−12a),
`line_14_nc_taxable_income` (= 12b; may be NEGATIVE), `line_15_nc_tax`,
`line_16_credits`, `line_17` (15−16), `line_18_use_tax` (**`null` by default,
never 0** — NY line-59 precedent), `line_19` (17+18), `line_20a_withholding`,
`line_20b_spouse_withholding`, `line_21a_estimated`, `line_21b_extension_paid`,
`line_23` (20a..22), `line_25` (23−24), `line_26a_tax_due`, `line_26b`-
`line_26e` (penalties and D-422 interest — 26e is an input, §8),
`line_27_amount_due` (26a + 26d + 26e), `line_28_overpayment` (which also
subtracts 26e), `line_29`-`line_33` (contributions and 33 their total, input
passthrough) and `line_34_refund` (28 − 33), per D-401 p.15-16; the flags
`federal_extension_circle` and `out_of_country_circle` (without the circle an
extended return is delinquent — D-401 p.7, p.12 — so it is a result and
package fact, not calendar text alone); sub-blocks `schedule_s` (A1, B17-B21
and totals) and `schedule_a` (lines 1-10). Lines 13, 21c/d, 22, 24 are
PN/nonresident/amended-only and stay absent.

**Computed-evidence fields:** `line_15_nc_tax`, `line_14_nc_taxable_income`,
`line_12b`, `line_11_deduction`. **`line_11_deduction` is NOT always non-zero**
(MFS-spouse-itemizes and not-eligible cases are zero) — unlike CA/NY's floor —
so evidence must be the TUPLE, with `line_14` counting when NEGATIVE.
Withholding (20a/20b) is parse-filled and is NOT evidence.
`_section_computed(fields)` is reused as-is (the NY refactor made it generic).

## 5. Compute functions

1. `compute_nc_schedule_s(fed: dict, inputs: dict, state_cfg) -> dict` —
   A1, B17, B18, B19 from federal result + parse; B20/B21 from EXPLICIT inputs.
   B18 from mutual-fund dividends needs the US-government SHARE as an input,
   exactly like A1's NC-obligation share.
   A1 needs the NC-obligation share of exempt interest: **absent share →
   BLOCK** (treating all as non-NC over-states; treating all as NC
   under-states — the caller must say). B20/B21 absent while a government or
   military 1099-R is present → BLOCK (omission OVER-states).
2. `compute_nc_child_deduction(status, federal_agi, n_children, state_cfg)` —
   `n_children` is the federal §24 qualifying-child count from the federal
   CTC computation, never the dependent count (an "other dependent" is not a
   §24 child — counting one OVER-states the deduction and UNDER-states tax).
3. `compute_nc_itemized(sched_a_inputs, status, federal_agi, state_cfg)` —
   from RAW inputs under the Code `irc_conformity_date` names (§2): 1098
   mortgage interest, gross gifts, real-estate tax BEFORE the federal SALT
   cap, medical. BLOCKS only where a raw input cannot settle the 2023-Code
   answer: any 1098 box 5 premium, mortgage principal over the 2023 Code's
   acquisition limit, home-equity debt, or gifts near the AGI percentage
   limit. MFS: only the spouse who was obligated for AND paid an expense may
   claim it (D-401 p.5, p.20), so a `paid_by` input is required per item.
4. `compute_nc_deduction_choice` — max(standard, itemized) with the MFS rule:
   `spouse_itemizes_nc` is a REQUIRED input for MFS; absent → hard stop
   (assuming the standard deduction under-states, assuming zero over-states).
5. `compute_nc_tax(nc_ti, tax_year, state_cfg)` — rate × max(0, line 14);
   refuses when `rate_tax_year != tax_year` (a carried-over TY2025 rate on a
   TY2026 return OVER-states by 0.26 pts — the backlog's year-gate).
6. `compute_nc_other_state_credit(line_15, per_state_inputs)` — D-400TC Part 1
   ratio worksheet (p.22-23), lesser of tax × ratio and net tax paid, one row
   per state. In v1 because omitting it OVER-states (§8). Inputs are the
   taxpayer's other-state return figures; nothing is inferred.
7. `compute_nc_d400(...)` — assembles lines 6-34; use tax is an INPUT
   (`nc_use_tax` amount, or `nc_use_tax_certified_none: true`); neither
   given → line 18 `null` + a loud note, payment lines still computed.

**Slice 3 decisions** (`state/nc.py`; the code moved, so this section did):

- **Keyword arguments, not `fed`/`inputs` dicts.** `compute_nc_schedule_s(tax_year,
  state_cfg, *, ...)` and `compute_nc_itemized(filing_status, federal_agi,
  state_cfg, *, ...)` name each raw input, so a misspelt key is a `TypeError`
  rather than a silently absent figure. `compute_nc_d400` takes each as a dict
  of those keyword arguments. Part 3 lives inside
  `compute_nc_other_state_credit` (its `line_16`-`line_20`).
- **The Schedule A BLOCKs the raw inputs cannot settle are CALLER ANSWERS**
  (`mortgage_over_acquisition_limit`, `home_equity_debt`,
  `charitable_agi_limit_may_bind`), required whenever the matching amount is
  non-zero, with `True` or unanswered refusing. The alternative was comparing
  against the conformity-date Code's dollar and percentage limits, which no
  cache field carries and §2 forbids inlining. Box 5 premiums refuse on the
  amount itself.
- **Claim-of-right eligibility is an explicit answer.** A non-zero repayment
  requires `claim_of_right_eligible: true`, certifying prior-year
  claim-of-right income, no repayment already deducted in current federal
  AGI, and no federal §1341(a)(5) credit. False or unanswered BLOCKS; mixed
  eligible/ineligible repayments require a manual line 8 calculation. See
  [D-401 p.20](https://www.ncdor.gov/2025-d-401-individual-income-tax-instructions/open)
  and [G.S. 105-153.5(a)(2)d](https://www.ncleg.gov/enactedlegislation/statutes/html/bysection/chapter_105/gs_105-153.5.html).
- **Credit jurisdictions have canonical identities.** A US state/DC code or
  full name normalizes to the code; foreign national taxes use `country:XX`
  (ISO alpha-2) for the supported UN member/observer countries. `CA` and
  `country:CA` distinguish California from Canada. No free-text foreign
  aliases or political subdivisions are accepted; unlisted jurisdictions
  require manual review. Duplicate canonical identities BLOCK even below
  the final tax cap. D-401 p.22 excludes city, county and federal taxes.
- **Child tables share one validator** at ingest, cache audit and compute.
  It requires at least two exact `{agi_up_to, per_child}` bands, positive
  ascending closed caps, non-increasing deductions, and an open zero top.
- **MFS `paid_by`** takes `filer` (claimed), `spouse` (refused: the item is on
  the other return) and `joint_account` (refused: p.20's income-share
  proration is not modelled). The household-wide line 4 limit is prorated by
  the PERCENTAGE PAID by each spouse, as D-401 p.20 line 4 says ("If the amount
  of home mortgage interest and real estate property taxes paid by both
  spouses exceeds twenty thousand dollars ($20,000), the deductions must be
  prorated based on the percentage paid by each spouse"): mortgage interest
  plus real-estate tax actually PAID, BEFORE the line 2 real-estate cap — both
  for the over-$20,000 test and for the share. `spouse_mortgage_and_property_tax`
  is that paid figure for the spouse, not the spouse's line 3. (An earlier
  draft prorated on line 3 after the cap; the two differ whenever a spouse's
  real-estate tax exceeds the MFS cap.)
- **Schedule S presence flags are REQUIRED booleans**
  (`government_retirement_1099r`, `uniformed_services_retirement_1099r`): a
  default of False would silently drop B20/B21. Lines 20+21 are bounded by
  `government_retirement_taxable` — the taxable amount (box 2a) of the
  government and uniformed-services 1099-Rs ONLY — required whenever either is
  claimed and validated whenever passed. Not 1040 lines 4b + 5b: those carry
  private IRA and 401(k) income the two lines can never deduct.
- **No zero defaults on Schedule S amounts.** `exempt_interest`,
  `state_local_refund_in_agi`, `us_obligation_interest`,
  `us_government_fund_dividends`, `taxable_social_security` and
  `railroad_tier2_in_agi` are REQUIRED keyword arguments: an omitted deduction
  over-states NC tax, an omitted line-1 addition under-states it silently.
- **D-400TC line 3 rounds HALF-UP** to four places from the exact quotient
  (`_shared._round_half_up`, the rule New York's whole-dollar rounding also
  uses); Python's half-even `round` gives 0.0022 for 9 / 4,000 where the form
  gives 0.0023. `other_states` is a list — `None` or `[]` mean no other
  state, anything else refuses.
- **A form-year gate**, `_NC_D401_YEARS_READ = {2025}`: Schedule S and D-400
  refuse a year whose D-401 was not read (§8's last row). `compute_nc_tax`
  alone is gated only by `rate_tax_year`, so the TY2026 rate test runs.
- **Schedule A carries line 7d**: the template had folded 7c (the floor) and
  7d (the deduction) into `line_7c_medical`; it is now `line_7c` +
  `line_7d_medical`.
- **Line 18 certification** is the field `use_tax_none_due_circle`; a `0`
  amount without it refuses, since the D-400 asks for the circle, not a zero.
  Unanswered, the circle is `null` beside a `null` line 18 — not `false`,
  which would read as an answer.
- **Line 28 with 26e larger than the overpayment** carries the excess 26e to
  line 27 rather than printing a negative overpayment. 26b/26c on an overpaid
  return stay on line 27, not netted.
- **Dependent filer** refused at `compute_nc_deduction_choice` (§2), and
  20b refuses on any status but MFJ (D-401 p.14).
- **§7's "update the test-invariants count"** is stale: that file no longer
  restates the suite's size (the `MEASURED COSTS` block owns it).

## 6. Registrations

| Where | Change |
|---|---|
| `state/registry.py` | `StateSection(code="NC", section="north_carolina_d400", evidence_fields=(…§4), mover_fields=(taxable income → line_14, total tax → line_19, refund → line_34), package_forms=(("north_carolina_d400", "North Carolina Form D-400"),))` |
| `tie_out.CATEGORY_TARGETS` + tax-parse SKILL.md | `state_withholding_nc` → `("north_carolina_d400", ["line_20a_withholding", "line_20b_spouse_withholding"])` AND the row `W-2 box 17 (NC) → state_withholding_nc`. Both halves (doc-sync test). 1099-R/1099-MISC NC withholding also lands on line 20 (D-401 p.14) — confirm the ledger maps those boxes too. |
| tax-setup SKILL.md step 4 | one WebSearch bullet per §3 row, ncdor.gov / ncleg.gov |
| `capabilities._state_note` | NC depth note, LAST. Must name: Sch S lines in scope, NC Sch A, child deduction, D-400TC Part 1 only, use tax ASKED, PN not modeled |
| `capabilities` rules | new UNSUPPORTED rules (§8) for NC business adjustments / Taxed-PTE owner / NC NOL, flags in profile template AND the tax-setup interview (test-enforced) |
| `tax_calendar.py` | NC-40 events: four EQUAL quarters, category `state_estimated_tax`, NC holiday roll (G.S. — "next succeeding day which is not a Saturday, Sunday, or a legal holiday", D-401 p.7) via `irs_dates.next_business_day_state` |
| `tax_calendar.events_extension` | NC text containing "Form D-400": automatic with federal extension IF the "Federal Extension" circle is filled; else D-410; payment NOT extended (90% rule) |
| `withholding.build_nc_true_up` | **REQUIRED by `test_a_computed_state_is_wired_into_every_module`** — cannot be a non-goal. Mirror `build_ny_true_up` (fetched 90%/100% shares; NC-4 4(c)-analogue is "additional amount per pay period") |
| `filing_package.attachment_checklist` | D-400 lines from D-401 p.3, p.7-8: refund → PO Box R / balance due → PO Box 25000 with personalized D-400V; attach W-2s/1099s showing NC withholding; Sch S/Sch A/D-400TC when used; other-state return copy for Part 1; federal return copy unless it shows an NC address or was e-filed; 1099-R for Bailey; DD-214 or other proof of service for B21 (D-401 p.3) |
| `registry.PTET_WINDOWS` | **Deferred — NOT added in Z4.** Adding `"NC": (2022, None)` makes `/tax-ptet` PRICE NC entity elections, which `ptet.py` does not model (owner side needs Sch S A14/B38). Record as non-goal; calendar has no prepayment window to schedule anyway |
| retirement_dashboard sets | none — NC conforms on HSA/IRA/deferrals (§2 row 2) |
| `/tax-extend` skill | NC paragraph (automatic-with-federal + circle; D-410 otherwise) |

## 7. Tests

- Tax math: TY2025 rate × line 14; zero/negative line 14 → 0; year-gate
  refusal; TY2026 rate case (G.S. 105-153.7 "After 2025 3.99%").
- Child deduction: every band boundary (inclusive upper bound) × 3 keys, all
  five statuses mapped; zero children; AGI exactly at the top band.
- Standard deduction: five statuses + MFS-spouse-itemizes zero + ineligible.
- Schedule A: $10k/$5k RE cap, $20k combined cap binding, medical floor on
  federal AGI, no SALT income-tax line, itemized-vs-standard choice.
- Schedule S: SS subtraction, US interest, refund, A1 with NC-share input;
  B20 refusal when a government 1099-R has no Bailey answer.
- D-400TC Part 1 worksheet (p.23) one and two states, lesser-of both ways.
- Line 18 `null` vs certified-none vs amount; lines 23-34 arithmetic.
- Validators: bool/NaN/Infinity/negative on every numeric input.
- Freshness fixtures; registry wiring test green; tie-out category round trip.
- Fixtures under `tests/fixtures/nc_*`. Target ≈ +60 tests; update
  `docs/contributing/test-invariants.md` count.

## 8. Explicit non-goals (each lands on `meta.limitations`)

| Non-goal | Omission biases NC tax | Treatment |
|---|---|---|
| Part-year / nonresident (Sch PN, PN-1) | n/a | Already BLOCKED at C1 (`moved_states_this_year`) |
| **Bailey (B20) / uniformed-services (B21) retirement** | **OVER-states** | NOT silent: required input when a government/military 1099-R exists, else BLOCK. Eligibility (Bailey: 5 yrs vested by 1989-08-12; B21: 20-yr service, medical retirement or Survivor Benefit Plan) is the taxpayer's assertion — the pipeline has no source for it |
| **Credit for tax paid to another state** | **OVER-states** | In v1 from inputs (§5.6); with no inputs, the existing `nonresident_source_income` / `statutory_resident_of_another_state` BLOCKS stand, and an acknowledgement must print the over-statement |
| Bonus depreciation / §179 85% add-backs (A3/A4) | UNDER-states | BLOCK-or-acknowledge with banner |
| **Prior-year 20% recovery deductions (B23/B24, B34-B37)** | **OVER-states** | BLOCK (rollover cannot read them yet) |
| **NC NOL (B39) / federal NOL add-back (A7)** | A7 under, **B39 over** | BLOCK |
| Taxed-PTE owner: loss add-back A14 / income deduction B38 | A14 under, **B38 over** | BLOCK when a K-1 from an NC Taxed PTE exists |
| Other Sch S ADDITIONS (A2, A5-A6, A8-A13; A9 is the 529 recapture — NC has had no 529 contribution deduction since 2014) | UNDER-states | degrade with a banner naming them |
| Other Sch S DEDUCTIONS (B22, B25-B33) | **OVER-states** | BLOCK unless acknowledged with the line named |
| D-400TC Part 2, line 14 carryovers, line 19 NC-478 credits | OVER-states | acknowledgeable. Part 3 lines 16-20 ARE in v1: they total Part 1 into D-400 line 16 and cap credits at line 15 |
| Use tax computation (line 18) | UNDER-states amount owed | ASKED; `null` + loud note when unanswered, never 0 |
| D-422 underpayment interest (26e), penalties | UNDER-states | note only |
| Contributions lines 29-32 | n/a | input passthrough |
| NC PTET pricing in `/tax-ptet` | n/a (federal advisory) | deferred; `PTET_WINDOWS` untouched |
| Federal Sch A figures reused for NC | BOTH (§2) | never reused: NC Sch A computes from raw inputs under the 2023 Code, BLOCKING only the cases in §5.3 |
| AGI-level OBBBA exclusions with no Sch S line | UNDER-states | banner naming the year's known gaps; a year whose D-401 was not read is refused |

## 9. Slices

Each slice is one pull request, reviewed and merged before the next; the
spec moves with the code when a slice proves an answer above wrong.

1. **This spec** — the questionnaire answered from the TY2025 D-401 and the
   statutes, the figures named as fetched fields, the non-goals with their
   bias direction. Verified by re-reading the load-bearing quotations
   (starting point, rate, conformity date, extension, standard deduction
   chart, child deduction, the combined $20,000 cap) against the D-401 text.
2. **Fetched-law block + result section** — `create_state_template("NC")`,
   the freshness fixtures, the tax-setup step-4 bullets, the
   `north_carolina_d400` template section.
3. **Compute functions** — §5 with hand-computed D-401 worksheet tests.
4. **Wiring** — the registry row, tie-out category and tax-parse row,
   calendar NC-40 and extension text, `build_nc_true_up`, the filing-package
   lines, the capability rules for §8's BLOCKs, `/tax-compute` step 10.
5. **Honesty surfaces** — the depth note written LAST, `meta.limitations`,
   the playbook's definition of done.

Before slice 2: resolve every UNVERIFIED row above, the §2 TY2026
conformity trap first, since it decides whether §5.3 refuses or computes.

## Final integration decisions (2026-10-03)

Slices 4 and 5 travel together: the depth note is written only after every
registration and honesty surface is present. NC projections deliberately skip
the generic bracket path: this forecast lacks Schedule S, the child deduction
and NC Schedule A inputs and cannot safely omit them. NC true-up instead reads
the completed D-400, excluding use tax from the estimated income-tax harbor
and including it in the balance target. Positive prior-year-prong use requires
a filed twelve-month-return confirmation; the separate no-liability exception
is recorded independently and refuses contradictory archived tax.

NC-40's general quarters are implemented. Qualifying farmers/fishermen and
annualized-income worksheets are manual, with the farmer answer required before
a quarterly true-up. `next_business_day_state(..., "NC")` currently rolls
weekends only; the NC legal-holiday set is not inferred from federal holidays.
Calendar events and true-up notes tell the reader to verify NCDOR dates.

NC screening fields live in the profile template and interview; absent answers
block for NC alone. The raw compute keyword inputs live at
`taxpayer_profile.north_carolina_inputs`, keeping all taxpayer facts in an
existing ignored and secured runtime file. The documented launcher command is
executed by a synthetic-workspace integration test.
