# 2026-09-10 — NY state module (Form IT-201) — design spec

Epic Z, items Z1-Z3. Companion guide:
`docs/contributing/state-module-playbook.md`; template:
`docs/superpowers/specs/TEMPLATE-state-module.md`.

**Owner ruling 2026-09-05: done means PARITY WITH CALIFORNIA, not an
acknowledgeable block.** A New York City taxpayer must never see the capability
gate, never be asked to sign a limitation, and never be handed a bracket
estimate. That is what makes this a module rather than a rule.

**Answers below are LIVE-VERIFIED or marked OPEN.** A blank means not
researched, never "does not apply" — the playbook's own rule, and the one that
keeps a spec from reading as more finished than it is. Every OPEN row names the
source to consult, so the next session starts from a question rather than a
guess. **No indexed figure appears anywhere in this document**: what goes in the
spec is which block a number lives in and where it is fetched FROM. AGENTS.md §2
is why, and it applies to a design document exactly as it applies to code.

## 1. Scope

- **State / form:** NY / Form IT-201, full-year resident.
- **Residency scope:** full-year resident ONLY. Part-year and nonresident
  (IT-203) stay gated — that is Z8, and `moved_states_this_year` blocks it
  today. The residency guardrails that decide who is a full-year resident
  shipped separately in Z7 (`docs/contributing/residency-guardrails.md`).
- **Local taxes in scope: YES — New York City.** The `nyc_resident` capability
  rule is DELETED at done and the flag becomes the city-tax switch. **Yonkers is
  in scope too and currently has no flag at all**; New York's own definitions
  give it the same parity with the State test that the City has, so a module
  that models the City and not Yonkers ships a silent gap for a real population.
  Z7 recorded that gap; this is where it gets closed or explicitly gated.
- **MCTMT** (the metropolitan commuter transportation mobility tax) rides the
  same return for self-employed filers in the MCTD. In scope as a computation or
  as a NAMED limitation — not as silence.

## 2. Questionnaire answers (playbook step 0)

| # | Question | Answer + citation |
|---|---|---|
| 1 | Starting point | **Federal AGI**, recomputed. IT-201 carries federal AGI forward and applies NY modifications; the "recomputed" wording matters because NY does not conform to every federal change. Same shape as CA Form 540, so `compute_state_tax`'s CA scaffolding is the right analogue. **OPEN: the exact IT-201 line and what "recomputed" excludes** — IT-201 instructions (tax.ny.gov current-year forms). |
| 2 | Additions / subtractions | **Form IT-225** carries NY additions and subtractions as a coded list, which is a different shape from CA's Schedule CA and needs its own accessor. Known members: **414(h) retirement contributions and IRC §125 cafeteria-plan amounts for NYS and NYC public employees** are ADD-BACKS and appear in W-2 box 14 — this is the parse-side item in the Z-epic table. **OPEN: HSA conformity, IRA/deferral conformity, alimony, out-of-state muni interest, the pension-and-annuity exclusion, and the Social Security subtraction** — IT-225 instructions and Tax Law §§612-613. |
| 3 | Credits | **CONFIRMED: no exemption for the taxpayer or spouse.** Tax Law §616 grants one for each exemption allowed under **IRC §151(c)** — the DEPENDENT subsection; §151(b), the taxpayer and spouse, is not referenced. The per-dependent amount is stated in §616 and not indexed, but is FETCHED anyway as a confirmation, because a statutory figure may still be amended. Beyond that NY has dependent exemptions, a household credit, an Empire State child credit, an EITC piggyback, a college-tuition credit and a real-property-tax credit. All are FETCHED, none are structural. **OPEN: the credit ORDERING and which are refundable** — that ordering is what a hand-computed test has to pin. IT-201 instructions. |
| 4 | Local income taxes | **NYC and Yonkers.** IT-201 lines 47-58 carry city tax, Yonkers resident surcharge, Yonkers nonresident earnings tax and MCTMT. The precedent this repository set for NYC was to GATE rather than half-model; the owner ruling reverses that for this epic. Residency at the city level is the STATE's test applied to the city — see the Z7 guardrails — so city days are a subset of state days and the reverse cannot happen. |
| 5 | Surtaxes | **None in CA's shape** (no separate mental-health-style surtax). The equivalent trap is row 13. |
| 6 | Estimated payments | **Form IT-2105, EQUAL QUARTERS** — April, June, September and the following January (tax.ny.gov estimated-tax due dates), so NOT California's 30/40/0/30 and `tax_calendar.py` needs no weighted branch. One voucher covers NYS + NYC + Yonkers + MCTMT together, which CA's separate 540-ES does not. Safe harbour mirrors federal §6654 in SHAPE — 90% of the current year or 100% of the prior, rising to 110% above a NYAGI threshold with MFS at its own (tax.ny.gov interest-and-penalties). The percentages and thresholds are FETCHED, deliberately not shared with the federal constants: they are New York's and may diverge. |
| 7 | SDI / PFL-style items | NY has both a statutory disability benefits deduction and a **Paid Family Leave** employee contribution, both wage-capped and both indexed. **OPEN: the cap mechanics and their year-awareness** — the CA scar here is that the SDI cap DISAPPEARED in TY2024, so this has to be year-aware from the first line of code. NYS Paid Family Leave site and the DBL statute. |
| 8 | Withholding-form analogue | **Form IT-2104** is the DE 4 analogue, with a separate IT-2104.1 for nonresidents. Feeds the `/tax-withholding` true-up — see the registration table, where withholding has NO generic state path. |
| 9 | Extension | **Form IT-370.** **OPEN: automatic on payment like CA's FTB 3519, or form-based** — IT-370 instructions. `/tax-extend` models only CA today and says so. |
| 10 | PTET | **NY has an elective PTET**, and the entity side is already modeled — `ptet.py` carries NY. The OWNER side is **SHIPPED (slice 8)**: `compute_ny_ptet_owner` pairs the IT-653 credit with its REQUIRED IT-225 add-back. **The Mar 15/16 defect carried here from PR #22's deferred list is already FIXED** — `ptet._election_deadline` returns March 16, 2026 because `irs_dates.next_business_day_state` observes that March 15 is a Sunday, and `tax_calendar.py` uses the same helper. Verified rather than re-fixed; the note was stale. |
| 11 | Community property | **NY is NOT a community-property state** — confirm membership of `capabilities._COMMUNITY_PROPERTY_STATES` is absent, which is the correct outcome, not an omission. |
| 12 | Itemized deductions | **Form IT-196**, and it is INDEPENDENT of the federal election: "the Tax Law allows you to itemize your deductions for New York State income tax purposes whether or not you itemized your deductions on your federal income tax return" (tax.ny.gov). It is computed on **federal rules AS THEY EXISTED BEFORE the TCJA** — so the 2% miscellaneous floor and the pre-2018 treatment of items the federal return no longer allows are live here. §615 then imposes **two separate high-AGI reductions** and a **separate charitable-contribution limitation** at very high NYAGI. This is the single largest piece of new computation in the module and it has no CA analogue. |
| 13 | Straight bracket math? | **NO — and this is the canonical trap the playbook names.** Tax Law **§601(d-1)** imposes a supplemental tax that recaptures the benefit of the graduated rate schedules above a NYAGI threshold, phasing in over a band. Plain bracket math UNDERSTATES tax there, which violates the bias-direction rule before a line is written. **The recapture is the FIRST thing implemented and the first thing tested**, not an enhancement — §601(a)/(b)/(c) carry the resident schedules for MFJ-and-surviving-spouse, head-of-household, and single-and-MFS respectively, and (d-1) sits on top of all three. |

## 3. Fetched-law block

A `state.NY` entry in `create_state_template("NY")`, `type: "graduated_income"`.
Marked FETCHED (indexed, re-fetched every season) vs STRUCTURAL (a statutory
constant that may sit inline WITH its citation, per AGENTS.md §2's narrow rule):

- **FETCHED:** resident rate schedules per filing status; standard deduction per
  status; dependent exemption; household-credit table; Empire State child credit;
  EITC piggyback percentage; college-tuition credit; the §601(d-1) recapture
  thresholds and phase-in band; the §615 reduction thresholds and percentages;
  the charitable-contribution limitation thresholds; NYC rate schedule; NYC
  school-tax credit; Yonkers resident surcharge rate; Yonkers nonresident
  earnings-tax rate; MCTMT rates and thresholds; PFL and DBL wage caps.
- **STRUCTURAL:** the SHAPE of §601(d-1) — that the recapture is the sum of
  per-tier benefits times a phase-in fraction — and the SHAPE of §615's two
  reductions. Shapes are statutory and do not index; the numbers inside them do.
- **Keying warning:** `tax_law_<year>.json` is NOT uniformly keyed by four
  filing statuses — `child_tax_credit.phase_out_start` and `qbi_threshold` are
  keyed `single`/`mfj` alone. NY's schedules are per-status and must say so
  explicitly in the block rather than inheriting an assumption. See
  `docs/contributing/tax-law-cache.md` before writing the accessor.

**SLICE 1 IS SHIPPED.** The block exists in `create_state_template("NY")` as a
SKELETON — every indexed figure null, every regime with a key to live in — and
`/tax-setup`'s fetch bullets name each key with the search that populates it.

**Its first draft had the wrong shape, and the correction is the substance of
this section.** A review against the statute and the current IT-201 instructions
found the skeleton could not hold two regimes, both in the UNDER-statement
direction:

- **The recapture is MULTI-TIER, and its citation is a RANGE.** §601 carries (d)
  through (d-7), each opening "Notwithstanding the provisions of subsection (d),
  (d-1) …" with its own taxable-year window — **(d-5) governs 2026**, and the
  first draft cited (d-1), which had been superseded four times. Above the entry
  threshold the taxpayer's NY TAXABLE INCOME selects a tier with its own
  recapture base and incremental benefit, and those amounts MOVE YEAR TO YEAR:
  they are fetched, not structural, or the compute function hardcodes tax law.
  A shape holding only the entry threshold reproduces the first worksheet and
  **under-states every return above the first tier**. There is also a top regime
  that is not a recapture at all — above a very high NYAGI the whole taxable
  income is taxed at one rate with no schedule — and it needs its own key.
- **The MCTMT splits by GEOGRAPHY, not by income tier.** Two zones with
  different rates, so an unlabelled rate list read positionally mis-assigns
  them. The threshold is **per zone**, computed individually even on a joint
  return, and it is a **CLIFF**: once a zone's earnings exceed it the whole zone
  amount is taxed, not the excess. It also changes for tax year 2026 — the year
  this module targets — so a fetch landing on an older page taxes a filer who
  owes nothing.

Six further corrections, each one a figure with nowhere to live or a mechanism
that could be applied twice: §615 has **four** mutually exclusive bands, not
two, and the second reduction has its own band; the charitable tiers need year
windows, or a lapsed tier reads as unfetched; every NY credit had nowhere to
land; the NYC school tax credit is **two** credits, one of them a bracket
schedule; the Yonkers nonresident earnings tax allows an **exclusion** first,
and omitting it OVER-states the tax; and the estimated-tax safe harbour needs
New York's own de-minimis figure and the farmer-and-fisherman share, or a
compute reaches for the federal ones.

**Brackets are keyed by SCHEDULE, not by status**, for a reason worth keeping:
`qss ≡ mfj` and `mfs ≡ single` are statutory identities under §601(a) and (c),
and five per-status lists would let a fetch populate `mfj` and leave `qss`
empty — an empty schedule being a silent ZERO tax rather than an error.

One correction runs the other way, and it is worth recording because the first
draft got the emphasis backwards: **QSS's standard deduction EQUALS MFJ's**, and
MFS's equals single's. Those are pairings to get right, not differences to hunt
for, and **MFS is the one most often guessed wrong** because it looks married.
The genuinely anomalous key is `single_dependent` — a single filer who can be
claimed as a dependent on another federal return — and nothing in the intake
asks that yet (IT-201 item C), so the key is ahead of its question on purpose.

## 4. Result section

Section key **`new_york_it201`**, with an `nyc` sub-block. Never reuse or
overload `california_540` — the playbook forbids it and `yoy_diff`'s
`STATE_MOVER_FIELDS` is currently a flat CA-only list that would gate NY movers
on CA sections.

**SHIPPED (slice 10)**, and NOT in the same slice as the compute functions the
way this section said it would be — the functions shipped first and the section
came eight slices later, which is the drift the rule was written to prevent.

The field names carry the FORM's line numbers, and the assertion that keeps
them honest is not that the section exists: **every line key the engine emits
must have somewhere in the template to go**, collected by RUNNING the functions
rather than reading the source, because three of the four line tables are
expanded into f-strings at call time. That check found eight mismatches on its
first run and one real defect — `compute_mctmt` and
`compute_city_and_yonkers_total` spelled line 54e two different ways.

The computed-evidence field is **line 34**, the deduction: New York's standard
deduction has a floor for every filing status, including the filer who can be
claimed as a dependent, so a genuinely computed return carries a non-zero one
even at zero income. Line 33 counts too, NEGATIVE included — a loss year must
not read as an uncomputed section. `_ca_section_computed` and
`_ny_section_computed` are now both thin calls on one `_section_computed`,
which takes the field list as an argument because only the caller knows which
of its fields a PARSER can fill.

**Line 59 ships `null`, not 0.** Sales and use tax is reported ON the income
tax return and changes the amount owed, and nothing computes it; a 0 default
would assert an answer nobody gave.

## 5. Compute functions

In dependency order. Each states its bias direction, because a degrade that
OVER-states the number the taxpayer acts on is a hard error here:

1. `compute_ny_agi` — **SHIPPED (slice 3)**, lines 19 through 33, with
   `compute_ny_modifications` and `compute_ny_pension_exclusion` beside it.

   **The direction is cross-checked, not inferred.** Form IT-225 puts additions
   on Schedule A and subtractions on Schedule B, and the codes carry that —
   `A-`/`EA-` against `S-`/`ES-`, where the `E` forms arrive through a
   partnership, S corporation or estate. The caller states which side it is
   handing over and the prefix checks the claim, because a subtraction filed as
   an addition OVER-states the tax and an addition filed as a subtraction
   UNDER-states it. A NEGATIVE amount refuses rather than flipping sides: that
   is the same confusion arriving by a signed figure.

   New York DISALLOWS several codes on a resident return rather than adjusting
   them, and the set changes as codes are retired, so it is FETCHED. Unfetched,
   the totals are still computed and a LOUD note says the eligibility was not
   checked — an absent cross-check may not become a silent pass, but neither
   may it block a correct return.

   **The pension and annuity exclusion is PER TAXPAYER and does not pool.**
   "Neither of you can claim any unused part of your spouse's exclusion", so a
   household-level cap would let one spouse's headroom shelter the other's
   income. Entitlement is the caller's assertion and is REFUSED if unstated —
   the age gate, the part-year rule for someone who turns 59½ during the year,
   and a beneficiary's claim on a decedent's entitlement are judgements about
   facts the engine is not given, and granting it unearned under-states the
   tax. A government pension routed here refuses too: those are fully excluded
   on their own line, and capping one would silently limit a full exclusion.

   **NYAGI may be NEGATIVE** — line 33 has no floor — while each modification
   is floored, because New York enters both directions as positive figures.
   And the 529 cap is keyed married-joint against EVERYBODY ELSE, the same
   two-key shape the federal child-credit phase-out uses; reading it with four
   status keys finds three absent and claims an uncapped deduction.
2. `compute_ny_itemized` — **SHIPPED (slice 4)**, IT-196 lines 46 through 49:
   the §615 adjustment and the standard-versus-itemized election. FOUR regimes
   by NYAGI, mutually exclusive, and the top two do not reduce the deduction at
   all — above the charitable-only threshold it BECOMES a share of the
   charitable contributions and every other itemized amount is disregarded.
   The second reduction takes the WHOLE first quarter and then phases a second
   in on top; reading it as one phase-in over-states the deduction by a
   quarter. The first threshold is PER STATUS while the gate into the
   worksheet is not, so a head-of-household filer between the two gets no
   adjustment at all.

   Cross-checks that cost nothing and catch a half-fetch: the charitable-only
   threshold and the lowest charitable tier describe ONE boundary and must
   agree, or a band opens where both regimes apply or neither; and the
   charitable total is REQUIRED in the top regime, because a caller that was
   not tracking the split would silently zero the deduction.

   **NOT in this slice: IT-196 line 45 itself.** Building it from source
   figures is its own piece of work — New York computes it on federal rules AS
   THEY STOOD BEFORE THE TCJA, so the 2% miscellaneous floor is live again, the
   mortgage limit is the pre-TCJA one, and state and local INCOME taxes are
   adjusted back out. The function takes line 45 as an input and will not
   pretend a federal Schedule A total is that figure.
3. `compute_ny_taxable_income` — NYAGI less the greater of the standard
   deduction or IT-196, less dependent exemptions.
4. `compute_ny_tax` — **SHIPPED (slice 2).** Not one method but three,
   selected on BOTH NYAGI and taxable income: New York's TAX TABLE below a
   published taxable income, the §601 rate schedule above it, and the benefit
   recapture on top once NYAGI passes the entry threshold. The recapture is
   inside the function, not a caller's option — a caller that can forget it is
   a caller that will.

   **The table is not a restatement of the schedule**, and the first draft used
   the schedule everywhere. It is the schedule evaluated at each row's MIDPOINT
   and rounded to the dollar — the published TY2025 $50,000-$50,050 row reads
   2,587 / 2,419 / 2,503 against a schedule-at-midpoint of 2,586.875 /
   2,418.875 / 2,502.875, while the row FLOOR is a dollar low in all three.
   Using the schedule there is worth up to two dollars and runs BOTH ways: it
   OVER-states for anyone in the upper half of their row, which is half of all
   returns, against an acceptance bar of one dollar a line. Slice 1's own cache
   carried the cutoff key and the warning, and the compute ignored it.

   Three more corrections from the same review, each a dollar or a block:
   the rate schedule needs each band's PUBLISHED cumulative base (New York
   rounds them, so summing the bands drifts by cents at most bands) and the
   base is now cross-checked against that sum, because a MIS-TRANSCRIBED base
   is likelier than a missing one and shifts a return by hundreds; the form
   rounds the phase-in FRACTION and nothing else, so rounding the recapture to
   cents first carried values across a whole-dollar boundary the form never
   crosses; and `tax` is now the WHOLE-DOLLAR line 39, because returning cents
   left the caller to round with Python's half-EVEN `round` where the form says
   half-up.

   **A negative New York AGI is an ordinary return** and the first draft
   refused it. Line 33 has no floor — a Schedule C loss, a K-1 loss or an NOL
   carryforward makes it negative routinely — while line 38 is floored, so the
   tax is simply zero. Refusing blocked the whole return.
5. **SHIPPED (slice 7)** — the city layer: `compute_nyc_taxable_income`,
   `compute_mctmt` and `compute_city_and_yonkers_total`, IT-201 lines 47-58.

   New York City taxable income is the STATE's in the ordinary case — "if No,
   enter the line 38 amount on line 47" — and the worksheet exists for one
   narrow situation: a New York Charitable Gifts Trust Fund contribution also
   claimed as an IT-196 deduction, which the City adds back. **A joint return
   with only ONE city resident does not use the line at all** and is computed
   on line 51; taxing the whole household as residents would over-state the
   city tax by the non-resident spouse's entire share, so it refuses.

   The city block has TWO credit caps — the household credit against the city
   tax, then the city's nonrefundable credits against that total — and line 58
   adds lines 54 and **54e through 57**, so the MCTMT and both Yonkers taxes
   come after both. No city credit offsets any of them. That is the pattern
   line 45 sets on the state side, appearing a second time.

   The MCTMT threshold is a **CLIFF applied PER ZONE**: the instructions' own
   worked example exempts $40,000 in one zone entirely and taxes the WHOLE
   $70,000 in the other. Subtracting the threshold — what an ordinary
   exemption would suggest — under-states the tax by the threshold itself, and
   adding the zones together before the test would tax both.

   The New York City household credit reuses `compute_ny_household_credit`:
   tables 4, 5 and 6 to the state's 1, 2 and 3, with the same three-way status
   grouping, the same federal-AGI keying, the same item-C bar and the same
   over-7 increment — verified against the instructions, not assumed from the
   shape.
6b. **SHIPPED (slice 8)** — `compute_ny_ptet_owner` and
   `compute_ny_required_annual_payment`.

   The PTET credit and its add-back are an exact PAIR. IT-225 code A-219 is
   "the amount of the credit claimed on Form IT-653, LINE 1" and A-222 is the
   amount on LINE 2 — not the entity's tax, not a share of it. They live on
   different forms, so a caller assembling IT-225 by hand has no reason to
   connect them, and **claiming the credit without the addition under-states
   New York tax by the whole of the credit**. There are TWO of each and they do
   not combine; adding both credits back under one code is the mistake a single
   "PTET addback" invites. The credit itself is REFUNDABLE — "the excess is
   treated as an overpayment, to be credited or refunded" — so it belongs with
   the payments.

   The required annual payment is the LESSER of the current-year and prior-year
   floors, in EQUAL QUARTERS, with the higher prior-year share keyed to NEW
   YORK adjusted gross income and MFS at its own threshold. A prior-year tax
   without its NYAGI refuses: assuming the lower share under-states the
   requirement for exactly the taxpayers it aims at. With no prior year at all
   the current-year share is the only floor, and that is SAID rather than
   assumed — a first-year filer genuinely has none, and a taxpayer who simply
   has not supplied one is in a different position.

7. NY credits — **the ORDERING is settled (slice 5), and the row-3 OPEN is
   closed for it.** `compute_ny_state_tax_total` covers lines 39-46. The
   nonrefundable credits are SUMMED and then capped at the tax — line 44 is
   "Subtract line 43 from line 39 (if line 43 is more than line 39, enter 0)" —
   so no ordering AMONG them changes the total, and the excess is simply lost
   with no carry-forward. What does matter is that **line 45, the net other
   New York State taxes, is added AFTER that cap**: a nonrefundable credit
   cannot offset it, and netting everything first would let a credit absorb a
   tax the form never lets it reach.

   `compute_ny_household_credit` (line 40) is the first of them, and it is
   unlike everything else in the module three ways: keyed on **FEDERAL** AGI
   rather than line 33; **barred entirely** for a taxpayer who can be claimed
   as a dependent on another federal return (item C, the same fact the
   standard deduction splits on and nothing in the intake asks yet); and the
   statuses grouped THREE ways that are not the §601 grouping — single alone,
   married-filing-separately alone, and married-joint with head-of-household
   and the surviving spouse together. MFS pairs with SINGLE for the rate
   schedule and gets roughly half the credit here.

   **The refundability question is CLOSED (slice 6), and the answer is all of
   them.** IT-201's block is headed "Payments and refundable credits" and line
   76 adds lines 63 THROUGH 75, so every New York credit other than the
   household and resident credits is a PAYMENT: it can produce a refund with no
   tax to offset. `compute_ny_settlement` covers lines 63-82, and its credit
   names are a CLOSED SET — handing it a nonrefundable credit refuses rather
   than refunding one the form only lets reduce the tax.

   Two credits sit between the ones everybody names and are easy to omit: the
   noncustodial parent EIC (line 66) and the New York City EIC (line 70).

   **The penalty is INSIDE the amount owed.** Line 80 is "the amount of tax you
   owe PLUS any estimated tax penalty you owe and any other" penalties, and
   against an overpayment it reduces line 77 instead. Reporting it beside the
   amount owed double-counts it and reporting it nowhere loses it — the exact
   shape a review already caught on federal Form 1040 line 38.

   The college tuition CREDIT and the college tuition itemized DEDUCTION are
   mutually exclusive, and they live in different functions, so neither would
   see the other without being told.

   **STILL OPEN**: the AMOUNT of each of those credits — IT-213, IT-216,
   IT-215, IT-214 and IT-272 each carry their own worksheet, and slice 6 takes
   the figures as inputs rather than computing them.

## 6. Registrations (playbook step 5)

| Where | Change |
|---|---|
| `projection.STATE_SECTION_KEYS` | add `new_york_it201` |
| `yoy_diff` | **SHIPPED (slice 11).** `STATE_MOVER_FIELDS` is keyed per SECTION and `_state_fields_present` iterates, so the gate belongs to the fields it guards. New York carries a FOURTH row California has no counterpart for — line 58's city, Yonkers and MCTMT total, because a City resident's year-over-year move is invisible in the state figures and is often the larger one. The state total is **line 46, not line 39**: line 39 is the tax before the credits and before line 45. A taxpayer who moved between two modeled states gets no state movers at all, which is right — a delta between two different states' tax is not a movement in anything. |
| `tie_out.CATEGORY_TARGETS` **paired with `/tax-parse`** | **SHIPPED (slice 11).** `state_withholding_ny` and `city_withholding_nyc`, both halves, each with its row in tax-parse's step-6.5 table and a test that the two stay paired. TWO categories because they are two LINES and two refunds: a city amount folded into the state line still ties, and leaves the city withholding unclaimed on a return that owes city tax. Per STATE like `state_withholding_ca`, never a generic `state_withholding` — a category spanning states cannot fail when one state's amount lands in another's section. **Yonkers (line 74) has NO category** and the parse table says so rather than leaving a reader to assume a zero. |
| `/tax-setup` step 4 | per-field WebSearch bullets for the NY block; the generic fallback fetches brackets and standard deduction only, which would leave the recapture and §615 unfetched and therefore unset |
| `capabilities._state_note` | **SHIPPED (slice 14), and it was LAST.** Every clause names something that computes. What it does NOT claim is as load-bearing: each refundable credit's AMOUNT, IT-196 lines 1-45 and IT-201 lines 44/45 are INPUTS, line 59 is ASKED rather than assumed zero, and IT-203 is not modeled. A test asserts every form the note names as an input is also named as one in the compute reference. |
| `capabilities` rules | **SHIPPED (slice 14).** The `nyc_resident` rule is DELETED and the flag is the city-tax switch — a rule blocking a return the engine can now produce is a gate crying wolf. It STAYS required-present, and that matters MORE than it did: a missing answer once blocked loudly and would now silently omit the city tax from a return that owes it. Yonkers got **two** flags, not one — `yonkers_resident` for the line-55 surcharge and `yonkers_nonresident_earnings` for the line-56 earnings tax — because they are two taxes with two populations and a taxpayer can owe either without the other. |
| `retirement_dashboard` sets | **STILL OPEN, but no longer silent (slice 13).** New York is in none of the three non-conforming sets, and the module now SAYS that is not a conformity finding: absence from them makes the dashboard CLAIM state savings, which is the over-stating-a-BENEFIT direction rather than the safe default an absent figure usually is. Sources named: IT-225-I's addition codes and IT-201-I lines 20-24. One New York item IS recorded — the public-employee 414(h) and IRC §125 add-backs, which are not rows this dashboard models, so nothing over-states them today. |
| `tax_calendar.py` + `ptet.py` | **SHIPPED (slice 13)** for the calendar half: four IT-2105 events for a New York resident (`address.state`), FOUR EQUAL quarters including SEPTEMBER — the sibling California events say there is none, and a reader must not carry that across — each description saying that ONE VOUCHER covers the State tax, the City tax, the Yonkers surcharge and the MCTMT. The extension events gained the New York note. PTET was already correct and the Mar 15/16 defect was already fixed (see slice 8's stale-note correction). |
| `withholding.py` | **SHIPPED (slice 12).** A New York true-up beside the California one, targeting **IT-201 line 60, not line 46** — one IT-2105 voucher carries the State tax, the City tax, the Yonkers surcharge and the MCTMT together, so a requirement built on the state total under-states a City resident's by the whole of their city tax. §685(c) shares and thresholds are FETCHED and keyed to NEW YORK AGI, so the module now reads `tax_law_{year}.json`; an ABSENT cache sets `ny_cache_missing` and the report SAYS so, because an omitted block reads as "no New York estimated tax is due". Four EQUAL quarters, never California's 30/40/0/30. `ytd.state_withholding` means ALL New York withholding — boxes 17 and 19 plus Yonkers — because that is what the one voucher is measured against. **OPEN: New York's own rollover rule.** `next_business_day_state` leaves NY on the weekends-only degrade, so a printed IT-2105 date can be a day or two before the one New York observes — the safe direction for a payment, the wrong one for a missed-deadline warning. Source to read: General Construction Law §§24 and 25-a against the Department's published due dates. |
| `/tax-extend` | **SHIPPED (slice 13), and row 9's answer is the opposite of California's.** IT-370-I: *"Do not submit a copy of the federal extension form. We will not accept it in place of Form IT-370 and your due date will not be extended."* A taxpayer who carries CA's automatic rule across has filed LATE at 5% per month up to 25%. Payment is a CONDITION of the extension — *"you must make full payment of the properly estimated tax balances due"* — where federal Form 4868 is valid with none, and an IT-370 filed late *"will not be accepted"* at all. The estimate is IT-201 line 80, because the 10% reasonable-cause presumption is measured against the same four taxes line 60 adds up. One IT-370 also extends Form Y-203. |
| `tax-law-freshness-auditor` | **BLOCKED on a fresh-session acceptance replay, not deferred by choice.** The definition is digest-pinned in `docs/ops/review-subagent-acceptance.md`, and CLAUDE.md's rule is that a definition edit needs a replay from a FRESH session — definitions load at session start, so a same-session replay tests the old one. The NY keying gotchas therefore went to `docs/contributing/tax-law-cache.md`, which the auditor is already told to work from: `brackets` keyed by §601 SCHEDULE, the year-windowed recapture, the table as a SEPARATE method, the two MCTMT zones, the §685(c) thresholds being New York's own, and `personal_exemption.amount: 0` as an affirmative declaration that must not be reported as unfetched. Fold the definition edit into Y2. |

## 7. Tests

Hand-computed from New York's own worksheets, each citing its source in a
comment. The cases that must exist before this is called done:

- **§601(d-1) recapture**: one case just below the threshold, one inside the
  phase-in band, one above it. The band is where plain bracket math and the
  correct answer diverge, so a test only at the extremes proves nothing.
- **§615**: one case in each of the two reduction bands, and one charitable
  case above the separate limitation.
- **IT-196 on pre-TCJA rules**: one case whose federal itemized total differs
  from the NY one, which is the whole point of the independence.
- NYC resident tax; Yonkers surcharge; a full-year NYC fixture end-to-end.
- Validator rejections: bool, NaN, negative, out-of-domain filing status.
- Freshness fixtures for the NY block, including an INCOMPLETE cache.
- IT-201 line map for `/tax-verify`, including lines 47-58. **SHIPPED (slice 14)**, with three refusals beside it: a line-46 match is NOT a New York tie-out (47-58 are a separate stack, often the larger half for a City resident); line 39 is not the state tax; and a null line 59 is UNVERIFIED, never agreement with a software zero. Every mapped path is pinned against the template by test.

**Acceptance (owner ruling):** a synthetic NYC fixture workspace runs the full
pipeline with no gate, no acknowledgement and no manual note about the city, and
`/tax-verify` ties to a software-prepared IT-201 within $1 on every line
including 47-58.

**OPEN:** the test-count delta, and the `docs/contributing/test-invariants.md`
line that has to move with it.

## 8. Explicit non-goals

Each emits a LOUD note that lands on `meta.limitations` — never silence:

- **Part-year and nonresident returns (IT-203).** Z8. `moved_states_this_year`
  blocks today and continues to.
- **Residency by DOMICILE as a gate.** Z7 left it a review finding on purpose:
  domicile is a judgement, and a gate on one fires on every move ever made.
- **Part-year city and Yonkers lines (50 and 57).** They belong to IT-360.1,
  and this module is full-year residents.
- **City-level day counts.** `residency.days_present_by_state` is keyed by
  STATE; there is no field for city days, so the City residency answer stays the
  taxpayer's assertion via `flags.nyc_resident`.
- **NY estimated-payment safe-harbor subtleties** beyond whatever row 6
  resolves.
- **Anything `withholding.py` cannot do**, if row 6/8 lands on "no NY true-up
  block in this slice".
- **Sales or use tax on IT-201 line 59.** It is reported on the income tax
  return and changes the amount owed, and it has no key. Named here rather than
  left to be noticed.
- **Estates and trusts**, which §601(c) also covers. An IT-201 never is one.
- **The AMOUNT of each refundable credit.** IT-213, IT-216, IT-215, IT-214 and
  IT-272 each have their own worksheet; `compute_ny_settlement` takes the
  figures and settles the return around them. Where each one LANDS is settled;
  what each one IS, is not.
- **IT-196 lines 1 through 45** — the New York itemized deduction BEFORE the
  §615 adjustment, computed on pre-TCJA federal rules. Slice 4 takes it as an
  input; building it from source figures the way `compute_ca_itemized` does is
  the next piece, and the federal Schedule A total is NOT a substitute.
- **IT-201 line 44** (the separate tax on a lump-sum distribution, Form IT-230)
  and **line 45** (net other New York State taxes, IT-201-ATT). `compute_ny_tax`
  returns LINE 39, not the state total on line 46, and its inputs cannot
  produce either component. Named in the function's own docstring as well,
  because a caller that treats line 39 as the state tax under-states a return
  that has one.

## 9. Sequencing

Z1-Z3 is one delivery by the roadmap, but not one pull request. Proposed slices,
each behind its own tests:

1. **Fetched block + `/tax-setup` fetch bullets + freshness.** Nothing computes
   yet; the cache is real and the auditor can see it.
2. **§601 schedules + §601(d-1) recapture.** The recapture ships WITH the
   schedules, never after.
3. **IT-225 modifications and NYAGI.**
4. **IT-196 and §615.**
5. **Credits, in statutory order.**
6. **NYC, Yonkers, MCTMT** — Z2.
7. **IT-653 owner-side PTET, IT-2105, IT-370, the true-up** — Z3.
8. **The generic state-bracket consumers** — SHIPPED (slice 9), and it was
   not on this list because the shape problem was not seen until the
   registrations were started. `state.brackets` is a LIST for California and a
   DICT keyed by §601 rate schedule for New York, a non-empty dict is truthy,
   and every `or []` gate in the repository passed one straight into code that
   iterates bands. `/tax-project` and `/tax-harvest` both raised
   `AttributeError` for any New York cache.
   `tax_computations.state_brackets_for_status` is the one reader of both
   shapes; a structural sweep keeps it the one, forbidding a BINDING of the
   raw read outside it while leaving a boolean `if` alone. The projection now
   routes a cache declaring a benefit recapture through `compute_ny_tax` —
   plain bracket math there under-states the tax — and the harvest labels its
   state rate a FLOOR for the same reason in the other direction.

9. **Registrations, `/tax-verify` line map, the depth note** — LAST, which is
   when `capabilities._state_note` tells the truth. **SHIPPED across slices
   10-14.** The depth note was written last of all, which is the point of the
   ordering: it is the sentence a taxpayer and a reviewer read to decide how
   far to trust the return, so it may only be written once the depth it claims
   exists.

   **The result SECTION came first inside this slice** — SHIPPED (slice 10),
   with `/tax-compute`'s New York branch
   (`references/state-return-ny.md`), `projection.STATE_SECTION_KEYS` and the
   computed-evidence pair. §4 records what it settled. What remains here is
   `yoy_diff`, `tie_out` paired with `/tax-parse`, `withholding`,
   `tax_calendar`, `/tax-extend`, `retirement_dashboard`, the freshness
   auditor, the `/tax-verify` line map, the deletion of the `nyc_resident`
   UNSUPPORTED rule, and the depth note LAST.

The first thing to resolve before slice 1 is the OPEN rows above. They are
questions with named sources, not guesses waiting to be made.
