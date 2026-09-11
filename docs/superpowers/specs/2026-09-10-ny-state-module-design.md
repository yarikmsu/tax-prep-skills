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
| 3 | Credits | NY has **no personal exemption for the taxpayer or spouse**; it has dependent exemptions, a household credit, an Empire State child credit, an EITC piggyback, a college-tuition credit and a real-property-tax credit. All are FETCHED, none are structural. **OPEN: the credit ORDERING and which are refundable** — that ordering is what a hand-computed test has to pin. IT-201 instructions. |
| 4 | Local income taxes | **NYC and Yonkers.** IT-201 lines 47-58 carry city tax, Yonkers resident surcharge, Yonkers nonresident earnings tax and MCTMT. The precedent this repository set for NYC was to GATE rather than half-model; the owner ruling reverses that for this epic. Residency at the city level is the STATE's test applied to the city — see the Z7 guardrails — so city days are a subset of state days and the reverse cannot happen. |
| 5 | Surtaxes | **None in CA's shape** (no separate mental-health-style surtax). The equivalent trap is row 13. |
| 6 | Estimated payments | **Form IT-2105.** One voucher covers NYS + NYC + Yonkers + MCTMT together, which is a real difference from CA's separate 540-ES. **OPEN: equal quarters or weighted, and whether NY has a high-income safe-harbor override like R&TC §19136.3** — IT-2105 instructions and Tax Law §685(c). This decides whether `tax_calendar.py` needs a weighted-event branch. |
| 7 | SDI / PFL-style items | NY has both a statutory disability benefits deduction and a **Paid Family Leave** employee contribution, both wage-capped and both indexed. **OPEN: the cap mechanics and their year-awareness** — the CA scar here is that the SDI cap DISAPPEARED in TY2024, so this has to be year-aware from the first line of code. NYS Paid Family Leave site and the DBL statute. |
| 8 | Withholding-form analogue | **Form IT-2104** is the DE 4 analogue, with a separate IT-2104.1 for nonresidents. Feeds the `/tax-withholding` true-up — see the registration table, where withholding has NO generic state path. |
| 9 | Extension | **Form IT-370.** **OPEN: automatic on payment like CA's FTB 3519, or form-based** — IT-370 instructions. `/tax-extend` models only CA today and says so. |
| 10 | PTET | **NY has an elective PTET**, and the entity side is already modeled — `ptet.py` carries NY. The OWNER side is not: **Form IT-653** (the PTET credit) and the IT-225 add-back are new. A known live defect from PR #22 sits here: NY PTET's TY2026 deadline is really Mar 16, not Mar 15, because both `ptet.py` and `tax_calendar.py` emit statutory dates only. |
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

**OPEN:** the exact key paths. They are written against
`create_state_template` at implementation time, not guessed here.

## 4. Result section

Section key **`new_york_it201`**, with an `nyc` sub-block. Never reuse or
overload `california_540` — the playbook forbids it and `yoy_diff`'s
`STATE_MOVER_FIELDS` is currently a flat CA-only list that would gate NY movers
on CA sections.

**OPEN:** the field list, the ALWAYS-non-zero computed-evidence field, and the
`_NY_COMPUTED_EVIDENCE_FIELDS` / `_ny_section_computed()` pair mirroring CA's in
`projection.py`. These are written with the compute functions, in the same slice,
because a section key registered before anything computes into it is a scaffold
that reads as coverage.

## 5. Compute functions

In dependency order. Each states its bias direction, because a degrade that
OVER-states the number the taxpayer acts on is a hard error here:

1. `compute_ny_agi` — federal AGI plus IT-225 modifications. **Degrade: a
   modification we cannot classify is NOT silently dropped** — dropping an
   ADDITION under-states tax, dropping a SUBTRACTION over-states it, and the
   two directions cannot share a fallback. It refuses and names the code.
2. `compute_ny_itemized` — IT-196 on pre-TCJA federal rules, then §615's two
   reductions, then the separate charitable limitation. **Degrade: refuse**,
   because a partially-applied limitation over-states the deduction and
   under-states tax.
3. `compute_ny_taxable_income` — NYAGI less the greater of the standard
   deduction or IT-196, less dependent exemptions.
4. `compute_ny_tax` — the §601 schedule for the status, **then §601(d-1) on
   top**. The recapture is inside this function, not a caller's option: a
   caller that can forget it is a caller that will.
5. `compute_nyc_tax` and `compute_yonkers_surcharge` — the city layer.
6. `compute_mctmt` — self-employment in the MCTD, or a named limitation.
7. NY credits, in the statutory ordering (**OPEN**, row 3).

## 6. Registrations (playbook step 5)

| Where | Change |
|---|---|
| `projection.STATE_SECTION_KEYS` | add `new_york_it201` |
| `yoy_diff` | **RESTRUCTURE `STATE_MOVER_FIELDS` per-section first.** It is a flat CA-only list gated on `california_540` in `_state_fields_present`; appending NY fields would gate NY movers on CA sections. Add the key to `OPTIONAL_SECTIONS`. |
| `tie_out.CATEGORY_TARGETS` **paired with `/tax-parse`** | `state_withholding_ny` AND `city_withholding_nyc` (W-2 boxes 17 and 19/20), each with its matching row in tax-parse's step-6.5 table. **Both halves or neither** — a registered-but-never-emitted category is dead protection and the gate passes vacuously. |
| `/tax-setup` step 4 | per-field WebSearch bullets for the NY block; the generic fallback fetches brackets and standard deduction only, which would leave the recapture and §615 unfetched and therefore unset |
| `capabilities._state_note` | upgrade NY from bracket-estimate to a depth note — **LAST**, and only when the above is real |
| `capabilities` rules | **DELETE the `nyc_resident` UNSUPPORTED rule** and re-purpose the flag as the city-tax switch. Decide Yonkers: a flag and a computation, or a named gate. Not silence. |
| `retirement_dashboard` sets | **OPEN** — pending row 2's conformity answers |
| `tax_calendar.py` + `ptet.py` | IT-2105 events (weighting pending row 6); PTET: **both modules carry independent `_PTET_STATES`/`_PTET_WINDOWS` copies — update both or neither**; fix the Mar 15/16 next-business-day defect while here |
| `withholding.py` | **has NO generic state path** — it is federal + CA only. Either add an NY true-up block or list the gap in §8 as an explicit non-goal. An unlisted gap silently drops NY's true-up. |
| `/tax-extend` | IT-370 mechanics (pending row 9) |
| `tax-law-freshness-auditor` | audit `state.NY` including the city tables |

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
- IT-201 line map for `/tax-verify`, including lines 47-58.

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
- **City-level day counts.** `residency.days_present_by_state` is keyed by
  STATE; there is no field for city days, so the City residency answer stays the
  taxpayer's assertion via `flags.nyc_resident`.
- **NY estimated-payment safe-harbor subtleties** beyond whatever row 6
  resolves.
- **Anything `withholding.py` cannot do**, if row 6/8 lands on "no NY true-up
  block in this slice".

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
8. **Registrations, `/tax-verify` line map, the depth note** — LAST, which is
   when `capabilities._state_note` tells the truth.

The first thing to resolve before slice 1 is the OPEN rows above. They are
questions with named sources, not guesses waiting to be made.
