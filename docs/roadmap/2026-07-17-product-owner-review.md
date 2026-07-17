# Product-Owner Review + Next Backlog — 2026-07-17

Reviewer lenses: product owner with tax-adviser / CPA / IRS-compliance / enrolled-agent /
investor background. Every engine-defect claim below was verified against source
(file:line checked) before being written down.

Predecessor: `2026-07-12-three-perspective-review.md` — **every scheduled item in that
backlog is now shipped** (PRs #27–#42, tests 1049 → 1506). This review asks the next
question: with the foundation fixed (tie-out ledger, capability gate, Schedule A, AMT,
filing package, extension, amendment), *what is now the biggest gap between the product
and its persona?*

**Answer, in one line: the engine was built and reviewed against TY2025 law, but the
toolkit is planning TY2026 right now (it is July) — and two OBBBA provisions that first
bite in TY2025/TY2026 are modeled wrongly, in the unsafe direction, squarely inside the
persona.** Second answer: the persona is "CA, W-2 + equity comp" and the single most
common real-dollar equity-comp error (RSU/ESPP 1099-B basis double-tax) has no support
at all.

---

## Part 1 — State of the product (calibration)

Strengths confirmed this pass: the arithmetic chain of custody (A1/A2 + second-read +
expected-payers) closes the CP2000 vector; the capability gate makes out-of-scope
honest; Schedule A / AMT / education credits / taxable-SS / 2210 close the 2026-07-12
common-return gaps; the pipeline now actually ends in something actionable (filing
package, extension, amendment); OBBBA is *mostly* handled (year-aware SALT cap, §70425
0.5% charitable floor, §70111 2/37 itemized haircut, adoption-credit refundable note,
§603 Roth catch-up gating). The bias-direction taxonomy is applied consistently.

The findings below are therefore narrower than the last review's — but several are P0
because they sit on the unsafe side (silently understate tax) for the current planning
year.

---

## Part 2 — Verified defects (fix regardless of roadmap)

| ID | Defect | File | Direction |
|----|--------|------|-----------|
| BUG-10 | **AMT exemption phase-out rate hardcoded at 25%.** `_AMT_EXEMPTION_PHASEOUT_RATE = 0.25` with a comment asserting the rate is "fixed" — but OBBBA §70107 raises the phase-out rate to **50% for TY2026+** (and resets the phase-out thresholds to the 2018 $500K/$1M base, reindexed — the thresholds come from the fetched cache so they'll follow, but the *rate* will not). The MFS recapture ceiling reuses the same 25%. A TY2026 ISO exerciser in the phase-out range keeps too much exemption. | `agents/lib/tax_computations.py:1824,1828,1960` | **Understates AMT** (unsafe) — the exact in-persona population D9 was promoted for |
| BUG-11 | **OBBBA tips / overtime / car-loan-interest treated as above-the-line AGI adjustments, uncapped.** tax-compute.md Step 4 lists them as "Schedule 1 Part II" adjustments inside `line_10_adjustments`, "detect from W-2 data", with **no caps and no phase-outs**. In the enacted law + IRS implementation these are **Schedule 1-A deductions taken AFTER AGI** (new 1040 line 13b), with per-provision caps (~$25K tips; ~$12.5K/$25K-MFJ overtime; ~$10K car-loan interest) and MAGI phase-outs (~$150K/$300K; ~$100K/$200K for car loans), joint-filing-required-if-married and SSN rules, and the overtime deduction covers only the **FLSA premium portion** (the "half" of time-and-a-half), not the whole overtime wage. Current instruction (a) wrongly reduces AGI — corrupting every MAGI-gated computation downstream (IRA phase-outs, education credits, taxable SS, NIIT, PTC…), and (b) grants unlimited exclusions to over-cap/over-threshold earners. **TY2025 returns were filed through this engine last season** — any return that claimed these provisions should be re-checked (the `/tax-amend` path now exists). | `agents/skills/tax-compute.md:244-255` | **Understates AGI and tax** (unsafe), both years TY2025+ |
| BUG-12 | **Senior deduction misfiled into the standard deduction.** tax-compute.md says the OBBBA senior enhancement is "already reflected in the Line 12 standard deduction amount". The ~$6K/person senior deduction (TY2025–2028) is a **separate Schedule 1-A deduction** — available to itemizers too, with its own ~6%-of-MAGI-over-$75K/$150K phase-out. Current treatment denies it to itemizers (overstates tax) and skips the phase-out for high-MAGI seniors (understates tax). Internally inconsistent: `compute_form_6251` already models `senior_deduction` as a Schedule 1-A line-37 AMT addback. | `agents/skills/tax-compute.md:253-255,265-266` vs `agents/lib/tax_computations.py:1856-1869` | Both directions |
| BUG-13 | **Non-itemizer charitable deduction (§170(p), TY2026+) absent.** OBBBA's permanent $1,000/$2,000-MFJ below-the-line charitable deduction for standard-deduction filers appears nowhere (grep: zero hits). | engine-wide | Overstates tax (taxpayer-unfavorable — safe but wrong) |
| BUG-14 | **Gambling capability note pre-OBBBA.** Gambling stays a MANUAL hand-worksheet item (correct), but the manifest/worksheet note doesn't carry OBBBA §70114: from TY2026 only **90% of gambling losses** are deductible (still capped at winnings). A hand-worksheet done under the old 100% rule understates tax. | `agents/lib/capabilities.py` gambling rule text | Understates tax (unsafe) for TY2026 gamblers |

Also verified-open from the previous backlog (not new, listed for completeness): A5
(`/tax-verify` still drops user-declared overrides from the discrepancy report —
tax-verify.md:117 "do not flag them"), H4 (no `_sources` URL pinning in
`tax_law_fetcher.py`), B6 (QBI ½-SE-tax base netting + 8995-A wage/UBIA still a warning,
not a computation), G3 (PFIC/Form 8621: zero mentions in the codebase).

---

## Part 3 — Findings by lens

### Tax adviser / CPA — the persona's missing money

- **NEW-1 (P1): RSU/ESPP 1099-B basis correction is absent.** The interview asks about
  RSU/ESPP (`tax-setup.md:187`), the projection takes `rsu_vesting` — and nothing in
  `/tax-parse` or `/tax-compute` handles the defining equity-comp trap: brokers report
  RSU/ESPP basis **excluding the W-2-included compensation** (often $0 for RSUs), so an
  unadjusted Form 8949 double-taxes income already on the W-2. The fix is a Form 8949
  column (f)/(g) **code B adjustment** flow: parse prompts for the broker's supplemental
  statement / Form 3922, compute adjusts basis, review adds a zero-basis-RSU DIF check.
  Direction: overstates tax — "safe" for compliance, but for a CA equity-comp persona
  this is routinely a four-to-five-figure error, and it is *the* reason this persona
  distrusts self-prep tools. Form 3922 (ESPP) classification should ride the Form 3921
  pattern shipped in PR #39; ESPP disqualifying-disposition ordinary income (sometimes
  missing from the W-2) is the companion check.
- B6 (QBI 8995-A) and D6 (2441 / 5329 / 8889 limit-test) remain the largest in-scope
  compute gaps; both stay P2 (gated/caveated today, correctly).

### IRS officer — integrity round-out

- A5 override hardening is the last open finding from the "central finding" cluster:
  the lines most needing scrutiny are still excludable from the `/tax-verify` report by
  user declaration. Small, cheap, closes a documented audit-trail hole.
- H4 source pinning: an LLM-fetched bracket still flows into a filed return with no
  recorded authoritative-source URL. With everything else hardened, this is now the
  weakest link in the chain of custody.
- G3: for a persona defined partly by *foreign accounts*, the absence of any PFIC/Form
  8621 tripwire is the most likely silent multi-thousand-dollar compliance miss left
  (foreign mutual funds/ETFs in those same accounts). Flag-only, C1-gated — promote to
  the next sprint.

### Enrolled agent — practice workflow

- It is July: the near-term users are **extension filers (Oct 15)** and **TY2026
  planners**. That ordering drives the sequencing below — TY2026 law-readiness (Epic I)
  outranks everything, because `/tax-project`/`/tax-scenario`/`/tax-roth-convert` are
  being run against TY2026 *now* with BUG-10/11 live.
- `/tax-amend` v1 models one live amendment; fine. But BUG-11 creates a concrete
  amend-scan use case: a step-by-step "should I amend my TY2025 return?" check for
  tips/overtime/car-loan claimants belongs in the Epic I acceptance criteria.
- F4 (voucher schedule as a filing deliverable) shipped as a cross-reference inside
  F1's checklist — acceptable; keep closed. F5 (FTF/FTP note for late filing *without*
  an extension) remains open at P3; the §6651 facts already live in `/tax-extend`.

### Investor — planning depth

- `/tax-harvest` v1 caveats (no §1091 basis-shift modeling, same-ticker proxy) remain
  acceptable; basis-shift stays P3.
- The deferred keystone follow-ups from PR #22 (dedicated `schedule_e_k1_income` field;
  deduction-only marginal probe) remain the right v2 shape for `/tax-ptet` accuracy —
  P2/P3, unchanged.

---

## Part 4 — Backlog (product-owner synthesis)

Guiding principle unchanged: **never silently understate.** Epic I is P0 because it is
the only cluster where the engine currently *does* — for the year being planned today.

### EPIC I — TY2026 law readiness — **P0**

> As a TY2026 planner (and a TY2025 filer who claimed tips/overtime/car-loan), I want
> the OBBBA provisions that first bite now to be computed, capped, and phased out —
> not hand-waved above the line.

- **I1 (P0, S): AMT phase-out rate year-awareness.** Phase-out rate becomes a
  parameter sourced from `federal.amt` (fetched; 25% pre-2026, 50% TY2026+ per §70107),
  hard-error when absent for TY2026+ rather than silently using 25%. MFS recapture
  ceiling follows the same rate. Fixes BUG-10. Pin with a TY2026 in-phase-out fixture
  hand-computed both ways.
- **I2 (P0, M): Schedule 1-A engine.** New `compute_schedule_1a` in tax_computations:
  tips / overtime-premium / car-loan-interest / senior deductions with per-provision
  caps + MAGI phase-outs from a new `federal.schedule_1a` cache block (amounts fetched,
  never hardcoded — my figures above are backlog-spec approximations, verify at fetch
  time), joint-filing/SSN eligibility rules, **routed to a new 1040 line 13b field**
  (below AGI) — not `line_10_adjustments`. tax-compute.md Step 4/5 rewritten; senior
  deduction removed from the "already in the standard deduction" claim (fixes BUG-11 +
  BUG-12); AMT addback wiring already exists. Acceptance: MAGI-gated downstream
  computations (IRA phase-out, taxable SS, education credits, NIIT) are pinned
  unchanged by a Schedule 1-A deduction; an over-threshold earner's deduction phases
  to $0; overtime uses the premium portion only.
- **I3 (P1, S): §170(p) non-itemizer charitable deduction** (TY2026+, cache-driven,
  standard-deduction filers only, interacts with I2's line 13b). Fixes BUG-13.
- **I4 (P1, XS): Gambling 90% note + TY2026 audit sweep.** Update the gambling MANUAL
  rule text for §70114 (fixes BUG-14) and run a one-time checklist sweep of every
  hardcoded constant / capability note against TY2026 OBBBA effects (CTC base, QSBS,
  1099-K threshold restoration note in `/tax-review`'s persona, etc.), recording the
  sweep's findings in this doc.
- **I5 (P1, S): TY2025 amend-scan guidance.** `/tax-amend` (or `/tax-review`) gains a
  targeted check: if the as-filed TY2025 return claimed tips/overtime/car-loan through
  the old above-the-line instruction, quantify the delta under the corrected Schedule
  1-A treatment and recommend amendment where material.

### EPIC J — Equity-comp depth — **P1** (in-persona)

> As a CA W-2 + equity-comp filer, I want my RSU/ESPP sales to stop double-taxing my
> W-2 income.

- **J1 (P1, M): Form 8949 basis-adjustment flow (code B).** Parse: per-broker prompt
  for the supplemental statement whenever the interview says RSU/ESPP and a 1099-B is
  present; ledger records carry `basis_adjustment` boxes. Compute: column (f)/(g)
  adjustments into Schedule D; tie-out extends to adjusted proceeds/basis. Review:
  zero-basis-RSU DIF check (a $0-basis sale of employer stock is presumptively a
  missing adjustment). Acceptance: fixture where broker basis $0 / W-2 comp $50K
  produces the corrected gain and the unadjusted path FAILS review loudly.
- **J2 (P2, S): Form 3922 (ESPP) classification + disqualifying-disposition check** —
  rides the Form 3921 pattern (PR #39): classify, ledger category, ordinary-income
  reconciliation against the W-2 (was the discount included?), §423 holding-period
  qualified/disqualified split.

### EPIC K — Integrity round-out — **P2**

- **K1 (P2, S): A5 — `/tax-verify` override hardening.** Overrides listed under
  `OVERRIDDEN (unverified)` with $ deltas, never dropped.
- **K2 (P2, S): H4 — tax-law source pinning.** `tax_law_<year>.json._sources` records
  the authoritative URL per fetched block; non-authoritative → INCOMPLETE.
- **K3 (P2, S): G3 — PFIC / Form 8621 tripwire.** Interview: "do the foreign accounts
  hold non-US mutual funds/ETFs?" → MANUAL capability rule + `/tax-review`
  international flag + `/tax-fbar` cross-note. Flag-only; computation stays out of
  scope.

### EPIC L — Deferred engineering (carry-forward) — **P2/P3**

- **L1 (P2, S):** `irs_dates.py` wiring into `tax_calendar.py` + `ptet.py` **together**
  (the documented v2 follow-up — both currently print statutory dates).
- **L2 (P2, S):** dedicated `schedule_e_k1_income` field in result.json + `/tax-ptet`
  phantom-income guard on it (PR #22 v2 follow-up #2).
- **L3 (P2, M):** B6 — QBI base netting (½ SE tax, SE health, SE retirement) + 8995-A
  wage/UBIA/SSTB limits.
- **L4 (P3):** PTET rate schedules → `state.ptet` cache block; deduction-only marginal
  probe; F5 FTF/FTP late-filing note; D6 (2441 / 5329 / 8889 limit-test);
  `/tax-harvest` basis-shift modeling.

### Community track (unchanged, unscheduled)

D7 (EITC), D8 (Schedule E rental — still no known rental user), G1 (1099-DA + crypto
per-wallet default), NYC/NY/part-year/nonresident. All C1-gated honestly; E5's playbook
is the leverage.

### Sequencing

1. **Now (this sprint): Epic I** — the only unsafe-direction cluster, live against the
   year users are planning today. I1+I2 first (one PR), then I3–I5.
2. **Next: Epic J** — the persona's biggest real-dollar win; J1 rides the ledger/tie-out
   rails that now exist.
3. **Then: Epic K** (small, closes the last documented integrity holes), Epic L as
   filler between seasons.

### Open questions (carried + new)

1. Rental property user? (decides D8 P2 vs P3 — still open from 2026-07-12)
2. Dependent investment income > threshold? (kiddie-tax gate question — still open)
3. Any TY2025 return actually claimed tips/overtime/car-loan through this engine?
   (decides I5's urgency — if none, I5 drops to a doc note)
4. Does the persona hold foreign *funds* (not just accounts)? (raises K3 to P1 if yes)
