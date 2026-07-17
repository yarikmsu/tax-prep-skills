# Epic I — TY2026 OBBBA Readiness — Design/Requirements

Status: requirements approved (product owner, 2026-07-17). Source: verified defects
BUG-10..14 in `docs/roadmap/2026-07-17-product-owner-review.md`. Priority **P0** —
the only cluster where the engine currently understates silently, and it is live
against the year being planned today (`/tax-project`, `/tax-scenario`,
`/tax-roth-convert` all run against TY2026 now).

This spec is self-contained: an implementing session needs this document, the
referenced files, and the repo conventions in CLAUDE.md — no chat context.

**Statutory-figure discipline (applies to every item):** dollar amounts and
thresholds quoted below are the product owner's TY2025-enacted recollection of
OBBBA (P.L. 119-21), written so the implementer knows the *shape* of each rule.
Every dollar figure and threshold MUST be fetched into `tax_law_<year>.json` at
`/tax-setup` time (WebSearch against irs.gov), per the no-hardcoded-tax-law rule.
Only bill-fixed structural facts (rates like the 50% AMT phase-out, effective-year
gates, per-$1,000 phase-out mechanics) are hardcoded constants, following the
`_CHARITY_OBBBA_FLOOR_FROM = 2026` precedent. Where this spec says "verify at
implementation", the final IRS form/instructions are the authority — WebSearch
them before encoding, and prefer denying a deduction with a loud note over
guessing eligibility (deny = overstates tax = the safe direction).

---

## I1 — AMT exemption phase-out rate year-awareness (P0, S)

**Defect (BUG-10).** `agents/lib/tax_computations.py:1828` hardcodes
`_AMT_EXEMPTION_PHASEOUT_RATE = 0.25` with a comment asserting the rate is
statutorily fixed. OBBBA §70107 amends §55(d)(3): for tax years beginning after
2025 the phase-out rate is **50%**, and the phase-out thresholds revert to the
2018 base (~$500K single/HoH/MFS-half, ~$1M MFJ), reindexed. The thresholds come
from the fetched `federal.amt.phaseout_start` so they will follow the cache; the
rate will not. A TY2026 ISO exerciser inside the phase-out range keeps too much
exemption → **understates AMT** — the exact population D9 was promoted for. The
same 25% is reused in the MFS exemption-recapture ceiling (~line 1960); §70107's
rate change flows through §55(d)(3) so the recapture mechanism uses the same rate.

**Requirements.**

1. `compute_form_6251` gains two parameters:
   - `tax_year: int` — **required** (same convention as `compute_schedule_a`).
     Validated as an int in a sane range (2000–2099, matching `_validate_year`
     conventions).
   - `exemption_phaseout_rate: float = None` — optional cache-supplied override.
     When provided: must be a finite non-bool float with `0 < r <= 1`
     (`_validate_rate`-style), else hard-error.
2. Rate resolution order (the `ptet._salt_cap` round-10 precedent): explicit
   param wins → statutory schedule by year. Statutory schedule (module
   constants): `0.25` for `tax_year <= 2025`, `0.50` for `tax_year >= 2026`
   (§70107). Both the exemption phase-out and the MFS recapture ceiling use the
   **same resolved rate** — never let the two drift.
3. Cache schema: `federal.amt` gains `"phaseout_rate": None` in
   `tax_law_fetcher.create_empty_tax_law`. `None` is VALID (the statutory
   fallback exists — mirror the `salt_cap` freshness treatment, do not nag);
   a present-but-corrupt value (bool/NaN/negative/>1) hard-errors.
4. `/tax-setup` fetch bullet (tax-setup.md:300-304) updated: also fetch the
   phase-out **rate** for the year, and add a staleness tripwire note — TY2026+
   `phaseout_start` values are dramatically LOWER than TY2025's (the §70107
   threshold reset); a TY2026 fetch returning TY2025-magnitude thresholds is a
   stale source, re-search.
5. `/tax-compute` step 6.5 (the 6251 call) updated to pass `tax_year` and the
   cached `phaseout_rate`.
6. The module comment at tax_computations.py:1823-1825 ("the 25% exemption
   phase-out rate [is] fixed") is rewritten — it is now wrong on its face.

**Acceptance criteria / tests (hand-computed per repo convention):**
- TY2026 fixture with AMTI inside the phase-out range: exemption reduced at 50%,
  AMT hand-computed both ways in the test comment.
- TY2025 regression: all existing 6251 tests unchanged except for the added
  `tax_year=2025` argument.
- Boundary: AMTI exactly at `phaseout_start` → full exemption both years.
- MFS recapture ceiling test pinned at the year-resolved rate for TY2026.
- Param validation: `tax_year` bool/float/out-of-range rejected;
  `exemption_phaseout_rate` NaN/bool/0/1.5/negative rejected; cache override
  (e.g. 0.50 passed explicitly for a hypothetical year) wins over the schedule.
- Ladder interaction: `/tax-roth-convert` and `/tax-scenario` do not call 6251
  (verify by grep — if any caller exists beyond tax-compute.md, update it).

---

## I2 — Schedule 1-A engine (P0, M)

**Defect (BUG-11 + BUG-12).** tax-compute.md:244-255 instructs the
tips/overtime/car-loan provisions as **uncapped above-the-line Schedule 1
Part II adjustments** inside `line_10_adjustments` ("detect from W-2 data"), and
claims the senior deduction is "already reflected in the Line 12 standard
deduction amount". Both are wrong:

- In the enacted law + IRS implementation these are deductions on **new
  Schedule 1-A**, claimed AFTER AGI (new Form 1040 line 13b region), available
  to itemizers and non-itemizers alike. Treating them as AGI adjustments
  corrupts every MAGI-gated computation downstream (IRA phase-outs, taxable SS
  provisional income, NIIT, education credits, ACA PTC…) and grants unlimited
  exclusions to over-cap/over-threshold earners. Both effects **understate
  tax**.
- The senior deduction is a separate Schedule 1-A deduction with its own MAGI
  phase-out, NOT a standard-deduction enhancement. The current instruction
  denies it to itemizers (overstates tax) and skips the phase-out (understates
  tax). Note the engine half already knows this: `compute_form_6251`'s
  `senior_deduction` parameter (tax_computations.py:1856-1869) correctly cites
  "Schedule 1-A line 37" and models the AMT addback — only the skill
  instructions and the missing compute function are wrong.
- TY2025 returns were filed through the wrong instruction — see I5.

**Provision shapes to encode** (amounts/thresholds → fetched cache; mechanics →
code; all four effective TY2025–2028 except where noted; verify each against the
final Schedule 1-A instructions at implementation):

| Provision | OBBBA § / new IRC § | Cap | MAGI phase-out | Notes |
|---|---|---|---|---|
| Qualified tips | §70201 / §224 | ~$25,000 per return | −$100 per $1,000 of MAGI over ~$150K (~$300K MFJ) | Voluntary cash/charged tips in Treasury-listed customarily-tipped occupations; SE filers in §199A(d)(2) SSTB trades excluded; SSN required; married must file jointly |
| Overtime premium | §70202 / §225 | ~$12,500 (~$25,000 MFJ) | same thresholds/mechanic as tips | **FLSA §7 premium portion ONLY** — the "half" of time-and-a-half, not the whole overtime wage (the #1 anticipated user error — validate loudly); SSN; married must file jointly |
| Car-loan interest | §70203 | ~$10,000 | −$200 per $1,000 of MAGI over ~$100K (~$200K MFJ) | Loan originated after 2024-12-31, first lien, NEW vehicle (original use), US final assembly, personal use, VIN reported on the return |
| Senior deduction | §70103 | ~$6,000 per qualifying individual 65+ (both spouses eligible on MFJ) | reduced by 6% of MAGI over ~$75K (~$150K MFJ) | IN ADDITION to the §63(f) age-65 additional standard deduction (they stack); available to itemizers; SSN; married must file jointly |

MAGI for all four = AGI plus §911/§931/§933 exclusions (reuse the
`lives_abroad` FEIE-addback convention already used for education credits).

**Requirements.**

1. **New pure function** `compute_schedule_1a` in `tax_computations.py`
   (rates-parameterized, dict return, 2dp, strict validators — house style):
   - Inputs: `filing_status`, `tax_year`, `magi`, per-provision raw amounts
     (`qualified_tips`, `overtime_premium`, `car_loan_interest`), senior
     eligibility booleans (primary/spouse 65+), and a `limits` parameter
     carrying the fetched `federal.schedule_1a` block.
   - Per-provision caps and phase-outs applied independently; each returns
     `{allowed, disallowed_by_cap, disallowed_by_phaseout}` plus notes; total
     is the sum.
   - Effective-year gate: outside TY2025–2028 every provision returns $0 with a
     "not in effect for TY{year}" note (senior likewise 2025–2028).
   - Eligibility gates: MFS → $0 for tips/overtime/senior with a loud
     joint-filing-required note. Car-loan MFS treatment: verify against final
     instructions; if unresolved, deny with a note (safe direction).
   - Cache block absent while any corresponding input is nonzero → hard error
     (the `federal.amt` unfetched-table precedent); all inputs zero → $0
     without needing the cache.
   - Overtime input is DEFINED as the premium portion; the function note (and
     the parse instructions) carry the whole-wage warning.
2. **Cache schema:** new `federal.schedule_1a` block in
   `create_empty_tax_law` — `tips_cap`, `overtime_cap` (by status group),
   `car_loan_cap`, `senior_amount_per_person`, and the three phase-out
   threshold pairs. Ships `None`s; `/tax-setup` gains a fetch bullet
   (search: `"IRS Schedule 1-A {year} tips overtime deduction limits"`).
   Phase-out *mechanics* ($100-per-$1,000, $200-per-$1,000, 6%) are structural
   constants in code, like the ACTC 15% rate.
3. **Routing (the load-bearing structural fix):**
   - `result_template.json form_1040` gains `line_13b_schedule_1a_deductions`;
     `line_14_total_deductions` = line 12 + 13a + 13b. New conditional
     `schedule_1a` section with per-provision detail lines.
   - tax-compute.md Step 4 REWRITTEN: the OBBBA bullet list and the
     "Schedule 1 Part II / line_10_adjustments" instruction are deleted;
     Schedule 1-A is computed in Step 5 (below AGI), never in line 10.
   - tax-compute.md Step 5: Line 14 formula updated; the "senior enhancement
     already in Line 12" sentence deleted. Audit how the §63(f) age-65/blind
     additional standard deduction is currently handled in the fetch/skill and
     make the three-way distinction explicit (base standard deduction + §63(f)
     additional + Schedule 1-A senior all stack; none substitutes for another).
4. **Ordering invariants (each gets a pinning test):**
   - AGI (line 11) is UNCHANGED by any Schedule 1-A amount — and therefore so
     are: IRA deduction phase-out, taxable SS, NIIT MAGI, education-credit
     MAGI, saver's-credit-style AGI tests, state AGI-coupled computations
     (CA 540 starts from federal AGI).
   - Form 8995 `taxable_income_before_qbi` = line 11 − line 12 − line 13b
     (Schedule 1-A is computed BEFORE QBI and shrinks the QBI overall-cap
     base).
   - AMT: the senior allowed amount flows to `compute_form_6251
     senior_deduction` (existing parameter — wire it, don't duplicate).
     Tips/overtime/car-loan are NOT added back for AMT absent contrary language
     in the final 6251 instructions (verify; note the assumption in the 6251
     call-site comment).
5. **Intake:** `/tax-parse` gains ledger categories `qualified_tips` and
   `qualified_overtime_premium` (informational class, like `ss_tips` — they
   feed Schedule 1-A, not a 1040 income line), sourced from W-2 box 14 /
   employer statements per the TY2025 transition reporting guidance. BOTH
   halves of the producer/consumer pairing rule: register in
   `tie_out.CATEGORY_TARGETS` (or the informational set) AND add the
   tax-parse.md table rows — the E5 doc-sync test enforces this.
   `/tax-setup` interview: car-loan-interest question (with the
   post-2024/new-vehicle/US-assembly/VIN sub-facts) and tipped-occupation /
   overtime questions added; every new profile flag must exist in
   `taxpayer_profile_template.json` AND be asked by the interview (registry
   invariant test enforces).
6. **Projection parity:** `/tax-project` (`projection.py`) must not
   double-count either: if `projection_inputs` ever carried tips/overtime as
   income exclusions, correct to the deduction-below-AGI model. At minimum add
   a forecast_overrides field for the expected Schedule 1-A total and subtract
   it AFTER the AGI-dependent pieces (taxable SS, NIIT) are computed. Degrade
   loudly if the cache block is unfetched.

**Acceptance criteria / tests:**
- Over-threshold earner: MAGI high enough → each deduction phases to exactly
  $0 (hand-computed table in test comments).
- Cap binding: $40K of tips → $25K-cap figure from the cache, not $40K.
- MAGI invariance: a fixture with and without $20K of Schedule 1-A deductions
  produces IDENTICAL line 11, taxable SS, NIIT, IRA phase-out, and education
  credit figures — only lines 13b/14/15/16+ move.
- QBI interaction: 8995 cap base reduced by 13b (hand-computed).
- Senior + itemizer: itemizing filer still receives the senior deduction.
- MFS: tips/overtime/senior $0 with note.
- Year gates: TY2024 and TY2029 → all $0 with notes.
- Overtime premium-vs-total warning surfaces.
- Registry invariants green (template + interview presence for new flags).

---

## I3 — §170(p) non-itemizer charitable deduction (P1, S)

**Defect (BUG-13).** OBBBA §70424 (new §170(p)) — permanent, TY2026+ — allows
standard-deduction filers a below-the-line charitable deduction of ~$1,000
(~$2,000 MFJ; MFS follows the non-joint figure — verify). Absent from the
engine entirely (grep: zero hits). Missing it overstates tax
(taxpayer-unfavorable — safe but wrong, and in-persona for TY2026 planning).

**Requirements.**
1. New `compute_charitable_non_itemizer(cash_contributions, filing_status,
   tax_year, itemizes, over_250_substantiated=None)`:
   - TY < 2026 → $0 + not-in-effect note. `itemizes=True` → $0 + note
     (mutually exclusive with Schedule A charitable).
   - CASH contributions only; contributions to donor-advised funds and
     §509(a)(3) supporting organizations do NOT qualify — asked/attested, and
     the note says so.
   - §170(f)(8) ≥$250 written-acknowledgment gate: reuse the
     `charitable_over_250_substantiated` tri-state convention from
     `compute_schedule_a` (None → warning; False → gated).
   - The §70425 0.5%-of-AGI floor does NOT apply (itemizer-only) — note it.
   - Amounts deducted here generate NO carryforward.
   - Cap figures from a new `federal.charitable_non_itemizer` cache pair
     (`amount`, `joint_amount`) — fetched; absent while input nonzero →
     hard error.
2. Routing: its own result field in the line-13 region (working name
   `line_13c_charitable_non_itemizer`; verify the final TY2026 form placement
   at implementation and rename to match — the F1 filing package reads these
   field names). Included in line 14; NOT in AGI; reduces the 8995
   taxable-income-before-QBI base like 13b.
3. Election interplay: the Schedule A election comparison in tax-compute.md
   must compare (itemized total) vs (standard deduction + §170(p) amount) —
   the non-itemizer deduction raises the standard side of the election for a
   donor. Pinning test where §170(p) flips the election.

**Tests:** cap binding, MFJ vs single vs MFS amounts, TY2025 → $0, itemizer →
$0, substantiation gate, election-flip fixture, QBI-base interaction.

---

## I4 — TY2026 statutory sweep (P1, S+XS items)

One-time checklist against OBBBA effects not yet encoded. Each item is either a
small code/doc change now, or an explicit recorded no-action. Record outcomes in
this file's Status section when shipped.

1. **Gambling §70114 (fixes BUG-14, XS):** TY2026+ wagering-loss deduction
   limited to **90% of losses** (still capped at winnings). Gambling stays
   MANUAL in `capabilities.py`; the rule text + manifest line + tax-review
   mention gain the 90% fact with the year gate, so a hand-worksheet is not
   done under the 100% rule (understates tax).
2. **QBI minimum deduction §70105 (S — engine change):** TY2026+, taxpayers
   with ≥ ~$1,000 of QBI from active qualified trade(s) get a minimum QBI
   deduction of ~$400 (indexed). `compute_qbi_deduction`
   (tax_computations.py:852) gains `tax_year` + the floor: result =
   max(computed-and-capped deduction, floor) when the activity threshold is
   met; the active-participation fact is attested (note). Amounts → fetched
   cache (`federal.qbi_minimum` `{income_threshold, amount}`). Direction:
   taxpayer-favorable; do not apply when the cache is unfetched (degrade with
   note — never guess an indexed figure).
3. **1099-K threshold restored (XS, doc):** OBBBA retroactively restored the
   $20K/200-transaction threshold. `/tax-review`'s 1099-K reconciliation note
   and `/tax-parse` expectations text updated (fewer 1099-Ks expected; the
   reconciliation logic itself is unchanged).
4. **1099-NEC/MISC $2,000 threshold TY2026 (XS, doc):** payer filing threshold
   rises to $2,000 (indexed) for TY2026+ — parse-side expectations note only
   (income is reportable regardless of whether a form arrives; say that).
5. **Clean-energy terminations (XS, doc):** §25D residential clean energy —
   expenditures after 2025-12-31 get no credit; §30D/§25E EV credits — vehicles
   acquired after 2025-09-30. Update the 5695/8936 MANUAL rule text in
   `capabilities.py` so a TY2026 hand-worksheet isn't attempted for dead
   credits (and a TY2025 one carries the acquisition-date cutoffs).
6. **CTC (verify-only):** TY2025+ $2,200/child indexed — confirm the
   tax-setup fetch bullets and `compute_child_tax_credit` cache plumbing need
   no change (amount is fetched; expected no-op).
7. **Trump accounts (XS, doc):** out of persona; add a one-line
   not-modeled note wherever new-dependent facts are discussed (H1 group) so
   the omission is explicit, not silent.
8. **QSBS §1202 tiers (XS, doc):** post-2025-07-04 stock: 50%/75%/100%
   exclusion at 3/4/5-year holding, raised caps. Earliest affected sales ripen
   2028+; add a caveat line to `/tax-harvest` and the capabilities notes so the
   toolkit doesn't assert the old 5-year/100%-only rule.
9. **Already-handled (verify tests exist, else add):** SALT cap year schedule
   (done, round 10), §70425 0.5% itemizer floor (done, D1), §70111 2/37
   haircut (done, D1), §603 Roth catch-up gating (done, retirement dashboard),
   adoption-credit $5K refundable note (done, H1-era).

---

## I5 — TY2025 amend-scan guidance (P1, S)

**Why.** TY2025 returns were prepared while tax-compute.md carried the BUG-11
above-the-line instruction. Any TY2025 return that claimed tips/overtime/
car-loan through `line_10_adjustments` has a wrong AGI (and possibly uncapped
deductions) — a concrete, quantifiable `/tax-amend` use case.

**Requirements.**
1. `/tax-amend` (or `/tax-review` as a targeted section — implementer's choice,
   but ONE home) gains a "Schedule 1-A misplacement scan": if the as-filed
   TY2025 `result.json` shows these amounts inside `line_10_adjustments`
   (detectable via answers.md records and/or a nonzero delta between W-2 wage
   sums and line 1/line 10 composition), recompute under the corrected
   Schedule 1-A treatment (I2 engine) and report the three-column delta.
   Materiality framing per the /tax-amend §6511 timing rules already in the
   skill.
2. If the scan shows no TY2025 return claimed these provisions (open question
   3 in the 2026-07-17 review), the deliverable degrades to a doc note in the
   skill ("engine corrected 2026-07; TY2025 returns prepared before that date
   should be scanned").
3. answers.md record per scan run (date, outcome, no dollar figures).

---

## Sequencing & PR shape

- **PR 1 (Epic I core): I1 + I2** — one PR, because I2's AMT wiring touches
  I1's new signature. Tests + CLAUDE.md test-count + tax-parse.md table rows +
  registry invariants in the same PR.
- **PR 2: I3 + I4** (I4 items are small riders).
- **PR 3: I5** (depends on I2's engine).
- Standard cadence per CLAUDE.md: feature branch, two-reviewer adversarial
  panel (distinct lenses — suggest "IRS-worksheet accuracy" and
  "pipeline/schema integration"), every finding fixed with a pinning test, CI
  green, squash-merge.

## Out of scope (say so, don't build)

- SSTB determination for tipped SE income beyond an attested yes/no.
- Treasury tipped-occupation list validation (attested; list cited in notes).
- Car-loan VIN validation beyond presence.
- ACA PTC interaction (8962 remains gated by C1).
- Schedule 1-A for part-year/NYC (still gated).

## Open questions for the product owner

1. Car-loan MFS eligibility per final instructions (default deny-with-note).
2. Whether any TY2025 return in `archive/` claimed tips/overtime/car-loan
   (drives I5 depth).
3. Whether the §63(f) additional standard deduction is currently fetched
   age-adjusted or base-only (I2 req. 3 audit will answer; record here).
