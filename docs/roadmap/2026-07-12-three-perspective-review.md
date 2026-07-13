# Three-Perspective Review + Product Requirements — 2026-07-12

Source: multi-perspective review (CPA, Enrolled Agent, IRS compliance officer) conducted
2026-07-12 by three parallel review agents over the full codebase, followed by a
verification pass on every engine-defect claim (file:line checked against source), then
a product-owner synthesis into the requirements backlog at the bottom.

Predecessor: `2026-tax-planning-features.md` (all 12 items shipped). That review moved the
toolkit from post-mortem preparer → proactive planner. **This review's theme is different:
the planning layer is now stronger than the foundation under it.** The anchored planning
skills are rigorous, but the intake→return pipeline they anchor to has no arithmetic
tie-out, the federal engine's scope is much narrower than the interview implies, and the
output is not a filable return.

---

## Part 1 — Verified engine defects (bugs, fix regardless of roadmap)

These were claimed by the CPA reviewer and **verified against source** in a second pass.

| ID | Defect | File | Verified | Direction |
|----|--------|------|----------|-----------|
| BUG-1 | **QBI missing the Form 8995 overall cap.** `compute_qbi_deduction` returns `20% × QBI + 20% × REIT` with no `min(…, 20% × (taxable_income − net_capital_gain))` ceiling — the cap is on the *simplified* 8995 itself (lines 11–14), not just 8995-A. Also no W-2-wage/UBIA limit or SSTB phase-out above the §199A threshold. | `agents/lib/tax_computations.py:742` | ✅ confirmed | **Understates tax** (unsafe) for low-taxable-income filers with QBI; overstates deduction for over-threshold SSTBs |
| BUG-2 | **Form 8959 over-withholding credit inflated `regular_medicare`.** `regular_medicare = medicare_wages×1.45% + se_income×2.9%/2` — but Form 8959 Part IV line 20 is Medicare *wages* × 1.45% only (SE income has no withholding to reconcile). The SE term shrinks `withheld_addl` and understates the line 25c credit. The inline comment asserting SE belongs there is a wrong rationalization. | `agents/lib/tax_computations.py:367` | ✅ confirmed | Understates a **credit** (taxpayer-unfavorable, safe for compliance, still wrong) |
| BUG-3 | **CTC is nonrefundable-only.** `compute_child_tax_credit` computes the phase-out correctly (ceil-per-$1,000 is right) but there is no refundable ACTC (15% of earned income > $2,500, per-child cap) and no $500 ODC for 17+ dependents. | `agents/lib/tax_computations.py:649-709` | ✅ confirmed | Understates refund for lower-income families with kids — a **P1 wrong number on a common return** |
| BUG-4 | **Projection treats all capital gains as LTCG.** `net_ltcg = max(0, capital_gains)` routes net *short-term* gains through the QDCG preferential buckets. Note: `scenario.py` does this **correctly** via `_net_capital_gains` (line 189) — the defect is the projection path only. | `agents/lib/projection.py:268` | ✅ confirmed (scope narrowed) | **Understates projected tax** → understated safe-harbor target → underpayment penalty exposure |
| BUG-5 | **Projection omits NIIT.** `projection.py` never calls `compute_niit`, so a high earner with investment income gets an understated mid-year projection and safe-harbor target. | `agents/lib/projection.py` (total at ~:301) | ✅ confirmed | Understates projected tax (unsafe) |
| BUG-6 | **SALT cap hardcoded as "$10K" in the compute skill.** tax-compute.md's itemized-vs-standard optimization note says "SALT up to $10K" — stale vs. OBBBA ($40K/$20K-MFS TY2025–2029) and inconsistent with `ptet.py`'s own year-aware `_salt_cap`. Exactly the hardcoded-tax-law drift the PTET round-5/6 P1s already identified; the v2 follow-up (lift SALT cap into `tax_law_<year>.json`) fixes both call sites. | `agents/skills/tax-compute.md:303` | ✅ confirmed | Wrong itemize/standard recommendation for TY2025+ |
| BUG-7 | **QBI base not reduced by ½-SE-tax / SE-health / SE-retirement adjustments.** 20% of raw Schedule C net overstates the deduction. | `agents/lib/tax_computations.py:950`, `agents/lib/projection.py:252` | plausible (P2) | Overstates deduction |
| BUG-8 | **Capital-loss carryforward computed but never persisted.** The $3K/$1.5K cap is applied but the unused loss has no field in `result_template.json`, so `/tax-rollover` has nothing to carry — the exact "forgot my $3K carryover" error rollover was built to kill. | `agents/lib/tax_computations.py:958` + `result_template.json` (no field) | ✅ confirmed | Loses a taxpayer-favorable carryforward next year |
| BUG-9 | **FTC limitation simplifications.** No qualified-dividend rate adjustment (Form 1116 line 18), denominator choice, no baskets, no carryover. Acceptable v1 simplification but undisclosed at output time. | `agents/lib/tax_computations.py:596-646` | plausible (P3) | Either direction |

Docs bug: `README.md` still says "90 tests" — the suite is 1049. Stale claim erodes trust in the security/testing story the README sells.

---

## Part 2 — Findings by perspective

### 2a. CPA — computational coverage

The engine is correct within its declared scope (QDCG, SE tax w/ wage-base coordination,
NIIT, Form 8606 pro-rata, Additional Medicare, CA 540 are all solid), but the scope is
**much narrower than a general 1040 preparer** — and, critically, narrower than what the
`/tax-setup` interview *implies* is supported:

**Missing entirely (P1 = wrong number on a common return):**
- **Schedule A / itemized deductions** — no `schedule_a` in result_template, no computation
  behind the "itemized vs standard" optimization check. A high-SALT homeowner defaults to
  standard deduction. (P1)
- **Taxable Social Security worksheet** — `line_6b_taxable_ss` exists in the template but
  no worksheet exists anywhere; every retiree return is hand-computed. (P1)
- **Schedule E / rental** (incl. Form 8582 passive-loss limits, depreciation) — setup asks
  about rental, nothing computes it. (P1)
- **EITC** — checklist row only; the refundable credit that low-income filers depend on is
  never computed. (P1 for that population)
- **Refundable ACTC + $500 ODC** — see BUG-3. (P1)
- **Education credits (8863)** — AOTC/LLC amounts + MAGI phase-outs are LLM-hand-math. (P1)
- **AMT / Form 6251** — see EA finding I2; interview asks about ISOs, engine can't tax
  them. (P1 when triggered)
- **Form 2210 underpayment penalty at filing** — safe-harbor math exists only in the
  *planning* skills; the filed return's line 38 is never computed and has no template
  field. (P2, all three reviewers flagged it)
- P2 tier: Premium Tax Credit reconciliation (8962), dependent care (2441), excess-SS
  credit, Form 5329 early-withdrawal 10%, kiddie tax (8615), energy/EV credits, HSA 8889
  limit-test/20%-penalty computation, Schedule H.

**State coverage is realistically CA-only.** The CA 540 path is genuinely good (HSA
add-backs, exemption credits, 1% MHST, SDI). NY is a stub marked `conforms_to_federal:
true` with an **empty NYC bracket list and no NYC resident tax computation** (~3.1–3.9% —
a P1 for any NYC resident). All other states fall to a generic brackets-only template: no
additions/subtractions, no state credits, no local tax. `projection.py:571`
`STATE_SECTION_KEYS = ["california_540"]` confirms single-state assumptions are baked in.

### 2b. Enrolled Agent — practice workflow

**Filing logistics — the pipeline ends one step before a return:**
- **F1 (P1): No filable output.** `/tax-generate` produces a *summary* PDF — no
  signature-ready 1040, no Form 8879, no e-file artifact. Combined with `/tax-verify`
  requiring the user to re-do the return in commercial software, the toolkit is
  structurally a *checker* of returns prepared elsewhere, while marketing itself as a
  preparer. Either build toward a filable package or state the checker positioning
  honestly.
- **F2 (P1): No extension flow.** No Form 4868, no state extension, no extension-payment
  computation — the first thing a practitioner reaches for on April 10.
- **F3 (P1): No amended-return (1040-X) flow.** Corrected 1099 arriving after filing has
  no supported path; the lifecycle jumps from verify → archive.
- **F4 (P2): 1040-ES vouchers aren't a standard filing deliverable** — they exist in the
  planning skills but a balance-due filer isn't handed next year's voucher schedule.

**Interview gaps:**
- **I1 (P2): Life events aren't asked as change events** — marriage/divorce, birth/adoption,
  **home sale (§121)**, inheritance are never asked for the filing year (ironically
  `/tax-review` asks about *next* year's changes). A home sale can be omitted wholesale.
- **I2 (P1): ISO exercise is collected, AMT is never computed** — the most dangerous
  single gap: the interview invites the taxpayer in, then silently produces a return
  missing potentially tens of thousands in AMT.
- **M1/M2 (P1/P2): `state_residency_changes` is captured and orphaned** — no part-year
  allocation, no nonresident return, no other-state-tax credit consumes it.

**Intake:**
- **D1 (P2, verified): `expected_sources` is dead.** `/tax-rollover` faithfully rolls
  forward last year's payer list, the template documents it as "informs /tax-parse to flag
  missing documents" — and `/tax-parse` never reads it (grep: zero consumers). The exact
  "you had a Fidelity 1099-DIV last year, where is it this year?" check the field exists
  for does not happen.

**Recordkeeping:**
- **R1 (P2): `/tax-review` output is not persisted** — the audit-risk assessment and
  substantiation gaps (the workpaper artifacts) vanish with the session.
- **R2 (P2): No per-return limitations manifest** — nothing in the workspace records
  "AMT not modeled, CA-only state, Schedule E not computed" for the return the taxpayer
  actually relied on. The only disclaimer is the README.

**Household (P2):** kiddie tax absent; HoH cost-of-keeping-up-home and qualifying-person
tests never tested (self-selected status survives unchallenged); dependent's own
filing-requirement check absent (P3).

### 2c. IRS officer — integrity & audit risk

**The central finding (P1): no computed reconciliation that parsed documents landed on
the return.** Numbers flow document → free-text `summaries/*.md` → LLM hand-aggregation
into `result.json` with no per-document ledger, no documents-in = lines-out count, no
sum-of-parts tie-out. `/tax-review`'s "does every 1099 have a line?" is a prose prompt
evaluated against the *already-aggregated* result — it structurally cannot see a document
that dropped during aggregation. This is the textbook CP2000/AUR under-report vector, and
it's the single most consequential defect in the project.

- **P1: No independent second extraction.** Intra-document sanity checks (Box 5 ≥ Box 1)
  can't catch a plausible misread ($52,100 for $92,100). The only true cross-check,
  `/tax-verify`, requires the user to own and re-key into commercial software.
- **P1: 1099-K has no extraction spec or gross-receipts reconciliation** — classified by
  `classify_document`, then nothing. `/tax-review`'s DIF checks look at Schedule C
  *expenses*, not 1099-K gross vs reported income.
- **P1: Crypto 1099-DA era.** The crypto model itself is a strength (trades taxable,
  staking ordinary, no wash-sale, safe $0-basis degrade), but hard-defaulting every
  disposal to Box C/F while TY2025+ exchanges issue 1099-DA creates a matching
  discrepancy the IRS will now catch; universal-pool default vs Rev. Proc. 2024-28
  per-wallet is the same story (a `--per-account` flag exists but isn't the guided
  default for 2025+).
- **P1: FBAR account list is never set-differenced against parse output.** A foreign
  account that produced interest in `summaries/` can be silently absent from the FBAR
  table; signature-authority accounts are reminder prose.
- **P2: `/tax-verify` "manual override" escape hatch** — user-declared overrides are
  excluded from the discrepancy report, suppressing exactly the lines most needing
  scrutiny.
- **P2: EITC due-diligence and round-number detection missing from the IRS-officer
  persona** — the highest-audit-rate area the IRS actually works is absent from the DIF
  checklist.
- **P2: FEIE (Form 2555) never mentioned; PFIC/Form 8621 absent** — a foreign brokerage
  holding non-US funds (extremely common alongside FBAR accounts) gets no PFIC flag.
- **P2: Substantiation is asked, never enforced** — "no, I don't have the >$250 letter"
  doesn't gate the deduction.
- **P3: WebSearch-refetched tax law has no authoritative-source pin** — an LLM-fetched
  bracket flows directly into the filed computation.

**Strengths (calibration, agreed by all three reviewers):** security posture is materially
better than typical self-prep tooling (prompt-injection hardening, SSN last-4 + redaction
sweep, path containment/XXE/file-size guards, chmod 600, layered git hooks — broadly Pub
4557-aligned); Form 8606 pro-rata handling is the best piece in the engine; FBAR-vs-8938
valuation methodology split is correct; the crypto tax model is correct and biased in the
safe direction; tax-law freshness gating is a real drift defense; the bias-direction
taxonomy (never silently understate tax) is consistently applied in the planning layer.

---

## Part 3 — Cross-cutting diagnosis

Three systemic patterns explain most findings:

1. **The interview writes checks the engine can't cash.** `/tax-setup` asks about ISOs,
   rentals, residency changes, marketplace insurance — then the pipeline silently produces
   a return missing AMT, Schedule E, part-year allocation, 8962. The project's own
   bias-direction taxonomy ("degrade loudly, never silently understate") is applied
   rigorously *inside* the planning skills but not at the **scope boundary** of the core
   pipeline.
2. **No arithmetic chain of custody.** Documents → prose summaries → hand-aggregated JSON,
   with the only tie-out being an optional diff against a return the user must prepare
   twice. Every other finding is bounded by this one: a perfect engine doesn't help if the
   inputs silently dropped a 1099.
3. **The product ends one step before its promise.** No filable package, no extension, no
   amendment, no penalty-at-filing — the "full pipeline through IRS review" stops at a
   summary PDF.

---

## Part 4 — Product requirements (product-owner synthesis)

### Product decisions (owner, 2026-07-12) — these re-cut the backlog below

1. **The product is BOTH preparer and checker.** Epic F (filing package / extension /
   amendment) is confirmed at P1 and F1 is a real filing package, not a
   software-transfer aid. `/tax-verify` stays first-class (the checker half).
2. **CA-focused.** All current users are California residents. CA depth beats state
   breadth: NYC/NY/part-year/nonresident work (old E2–E4) drops to the **community
   track** (P3) — other-state users are expected to extend the project themselves with
   Claude, so the investment shifts to making state contribution easy (new E5) and to
   CA-specific gaps the review surfaced (new E6/E7). Out-of-state situations remain
   **gated, not built** (C1).
3. **Crypto is nice-to-have.** No current user holds crypto. G1 (1099-DA
   reconciliation) drops P1 → P3; `/tax-parse-crypto` stays as-is with its existing
   caveats, and C1 gates it honestly if a crypto user ever appears.
4. **Persona sharpened:** CA resident, W-2 + equity comp (RSU/ISO/ESPP), investments,
   foreign accounts. Consequences: **AMT (D9) is promoted to P1** — ISO exercises are
   squarely in-persona and it's the most dangerous silent gap for these users; EITC
   (D7) drops to P3 (out of persona); Schedule A (D1) gains urgency (high CA SALT +
   OBBBA $40K cap makes itemizing the norm for this persona).

Guiding principle for prioritization: **this toolkit's differentiator is that it never
silently understates.** Requirements that protect that invariant (scope guards, tie-outs,
unsafe-direction bug fixes) outrank feature breadth. Where a gap can't be built soon
(AMT, multi-state), the P0 requirement is to *refuse or loudly degrade* — honesty is
cheaper than coverage and ships this quarter.

Severity: P0 = protects against silently-wrong filed returns, ship first.
Effort: S/M/L/XL. Test-count updates per CLAUDE.md convention apply to every item.

### EPIC A — Return Integrity Ledger (the tie-out) — **P0**

> As a taxpayer, I want deterministic proof that every parsed document landed on my
> return, so a dropped 1099 is impossible rather than unlikely.

- **A1 (P0, M): Structured per-document ledger.** `/tax-parse` step 6 additionally emits
  `parse_ledger.json`: one record per document — `{doc_id, form_type, payer, key_boxes:
  {box: amount}, target_lines: []}`. Machine-readable sibling of the prose summaries;
  summaries stay for humans. Acceptance: every file in `documents/` has exactly one ledger
  record or an explicit `skipped` record with reason; gitignored + chmod 600.
- **A2 (P0, M): Compute-side tie-out.** `/tax-compute` gains a mandatory final step: a new
  `agents/lib/tie_out.py` sums ledger `key_boxes` by destination (wages, interest,
  dividends, proceeds, withholding…) and diffs against `result.json` lines. Any unmatched
  document or >$1 aggregate delta = **hard gate** (return not marked complete). Acceptance:
  fixture with a deliberately-dropped 1099-DIV fails the gate; matched fixture passes;
  report written to `summaries/_tie_out.md`.
- **A3 (P1, S): Second-read verification for high-value fields.** For each ledger record
  with any box ≥ $10K, a second independent subagent re-extracts only the key boxes;
  mismatch → flagged for human confirmation. Cheaper than double-entry everything;
  targets the transposition risk where it's material.
- **A4 (P1, S): Consume `expected_sources`.** `/tax-parse` step 2.5 diffs the rolled-over
  payer list against ledger payers; missing payer → blocking question ("Fidelity 1099-DIV
  existed last year — confirm the account was closed or provide the document").
  Acceptance: the field is no longer write-only (today: zero consumers).
- **A5 (P2, S): `/tax-verify` override hardening.** User-declared "manual overrides" are
  still listed in the discrepancy report under an `OVERRIDDEN (unverified)` section with
  the $ delta, never silently dropped.

### EPIC B — Engine correctness fixes — **P0** (bugs, not features)

> As the maintainer, I want the engine's declared scope to be *correct within scope*,
> because anchored planning skills multiply any baseline error.

- **B1 (P0, S): QBI Form 8995 overall cap** — add `taxable_income_before_qbi` +
  `net_capital_gain` params to `compute_qbi_deduction`; deduction = min(component sum,
  20% × (TI − NCG)). Add SSTB/threshold **hard warning** (not silent) above the §199A
  threshold; full 8995-A wage/UBIA is a separate P2 (B6). Fixes BUG-1.
- **B2 (P0, S): ACTC + ODC** — extend `compute_child_tax_credit` with earned income,
  refundable cap per qualifying child, and $500 ODC for non-CTC dependents; return
  `{nonrefundable, refundable_actc, odc}`. Fixes BUG-3.
- **B3 (P0, XS): Form 8959 Part IV fix** — drop the SE term from `regular_medicare`
  (line 20 = Medicare wages × 1.45% only). Fixes BUG-2.
- **B4 (P0, S): Projection ST/LT split + NIIT** — `projection_inputs.json` gains
  `capital_gains_short/long`; projection reuses `scenario._net_capital_gains` and calls
  `compute_niit`. Fixes BUG-4/5 (both understate the safe-harbor target — unsafe).
- **B5 (P0, S): SALT cap → tax_law cache** — add `federal.salt_cap` to the schema
  (year/status-aware, OBBBA phasedown noted), fetched by `/tax-setup`; `ptet._salt_cap`
  and tax-compute.md:303 both read it. Executes the already-identified PTET v2 follow-up
  #1; fixes BUG-6.
- **B6 (P2, M): QBI base netting (½ SE tax etc.) + full 8995-A** (BUG-7).
- **B7 (P1, S): Capital-loss carryforward persistence** — add
  `schedule_d.loss_carryforward_to_next_year` (ST/LT split) to result_template +
  FIELD_MAP entry in `/tax-rollover`. Fixes BUG-8.
- **B8 (P3, XS): README test-count + capability claims refresh** ("90 tests" → current;
  soften "any state" per E1).

### EPIC C — Scope guards: never silently out-of-scope — **P0**

> As a taxpayer whose situation the engine can't compute, I want the pipeline to tell me
> loudly at compute time, not let a plausible-looking wrong return through.

- **C1 (P0, S): Capability matrix + gate.** New `agents/lib/capabilities.py`: declarative
  map of profile flags → engine support level (`computed | manual-with-worksheet |
  UNSUPPORTED`). `/tax-compute` step 0 evaluates it; any UNSUPPORTED trigger (ISO
  exercise, rental income, marketplace insurance, part-year residency, NYC residency,
  dependent with unearned income, household employee) produces a **blocking banner** with
  the specific form name and options (professional referral / verify-in-software path).
  Acceptance: fixture with `iso_exercise=true` cannot produce a "complete" result.json
  without an explicit user acknowledgment recorded in answers.md.
- **C2 (P0, S): Per-return limitations manifest.** `/tax-compute` writes
  `result.json.meta.limitations[]` (from C1's evaluation) and `/tax-generate` prints it
  on page 1 of the PDF — the EA's R2: what this return does NOT cover, recorded with the
  return itself.
- **C3 (P1, S): Persist `/tax-review` output** to `reviews/{year}-review.md` (chmod 600,
  gitignored) — workpaper file, feeds `/tax-rollover`'s next-year checklist.

### EPIC D — Coverage tier 1: the common-return gaps — **P1**

> As a mainstream filer (homeowner / retiree / parent / student), I want the forms that
> appear on tens of millions of returns computed, not hand-checked.

Build order by persona coverage × error cost:
- **D1 (P1, M): Schedule A engine + itemize/standard election.** `compute_schedule_a`
  (medical 7.5% floor, SALT from B5's cache, mortgage-interest limits, charitable AGI
  caps + >$250 substantiation gate per IRS reviewer P2-5); result_template `schedule_a`
  section; election computed, not vibes. Persona note: high CA SALT + the OBBBA $40K
  cap makes itemizing the norm for the target user — this is the top D item. Must also
  compute the **CA Schedule CA itemized path** (CA has no SALT cap on income tax paid
  to other states but disallows CA income tax itself; CA/federal itemize elections can
  differ).
- **D2 (P1, S): Taxable Social Security worksheet** (`compute_taxable_ss`, Pub 915
  lines) — template field exists, worksheet doesn't; every retiree return needs it.
- **D3 (P1, M): Form 2210 at filing** — reuse the safe-harbor logic already in
  `projection.py`/`withholding.py`; add `line_38_estimated_tax_penalty` to template.
  All three reviewers flagged this.
- **D4 (P1, S): Excess-SS-withholding credit** (two-employer case — pure arithmetic
  from W-2 ledger, cheap once A1 exists).
- **D5 (P1, M): Education credits (Form 8863)** — AOTC/LLC with MAGI phase-outs;
  interview already collects 1098-T.
- **D6 (P2, M): Dependent care (2441), Form 5329 10% additional tax, HSA 8889
  limit-test.**
- **D7 (P3, L — deprioritized 2026-07-12, out of persona): EITC with due-diligence
  checklist** — computation + the IRS reviewer's missing due-diligence flags
  (investment-income disqualifier, residency-of-child questions) added to
  `/tax-review`'s IRS persona. C1 gates it meanwhile.
- **D8 (P2, L): Schedule E (rental)** — income/expense, straight-line depreciation,
  §469 $25K active-participation allowance; 8582 full PAL is out-of-scope v1 (caveat).
  Confirm whether any current user has rentals (open question 2 below) — if not, drops
  to P3 behind C1's gate.
- **D9 (P1, L — promoted 2026-07-12): AMT / Form 6251.** ISO exercises are squarely
  in-persona for CA equity-comp users and this is the most dangerous silent gap in the
  product. v1 scope: the ISO-bargain-element path the interview already collects, AMT
  exemption + phase-out, the AMT capital-gains rate coordination, and **AMT credit /
  Form 8801 carryforward persistence** (via `/tax-rollover`, like ira_basis — an ISO
  AMT hit without the 8801 carryforward loses the money permanently). CA has its own
  AMT (Schedule P) — flag, don't compute, in v1. Until built, C1 gates. Kiddie tax
  (8615) rides behind C1's dependent-unearned-income flag as P3.

### EPIC E — CA depth + state strategy — **P1** (re-cut 2026-07-12: CA-focused)

> As a CA filer, I want the CA 540 computed as deeply as the federal return; as a
> future out-of-state contributor, I want a clear pattern for adding my state with
> Claude's help.

- **E1 (P0, XS): Truth-in-labeling.** README/CLAUDE.md "works for any state" →
  "California computed in depth; other states bracket-estimate only and gated;
  contributions welcome (see E5)". Costs nothing, ships today, closes the worst
  honesty gap — and now matches the actual product strategy.
- **E5 (P2, S): State contributor playbook.** Since other-state users are expected to
  extend the project with Claude: a `docs/superpowers/specs/` template + short guide
  describing the state-module pattern (what `create_state_template` provides, where
  additions/subtractions/credits/local taxes plug in, which tests to add, how C1's
  capability matrix registers the new state). Turns "any state, in principle" into a
  90-minute Claude session for a motivated user.
- **E6 (P2, S): CA excess-SDI credit.** Verified gap: `ca_sdi_box14` captures SDI
  withheld but nothing computes the refundable excess-SDI credit when two employers
  over-withhold past the annual cap — the classic job-switcher case, squarely
  in-persona. Cap/rate goes in the tax_law cache (`state.sdi`), computation in
  `/tax-compute` step 10, field in the CA 540 template.
- **E7 (P2, S): CA estimated-payment weighting (30/40/0/30).** CA 540-ES is NOT four
  equal quarters (no Q3 payment; 30/40/0/30 per FTB) — verified absent from
  `tax_calendar.py` (federal 1040-ES events only) and from `/tax-withholding`/
  `/tax-project` voucher math. Add CA-ES events to the calendar and a CA voucher
  schedule to the planning skills; also fold CA's automatic extension (no form —
  payment-only via FTB 3519) into F2.
- **Community track (P3, was E2–E4):** NYC resident tax / IT-201, NY decoupling pass,
  part-year allocation engine + other-state-tax credit (consuming the orphaned
  `state_residency_changes`). Not scheduled — C1 gates these situations honestly, and
  E5 is the leverage that lets affected users build them. `state_residency_changes`
  stays in the profile as the hook.

### EPIC F — Filing logistics: finish the pipeline — **P1**

> As a filer, I want the pipeline to end in something I can act on in April — a filable
> package, an extension, or an amendment — not a summary.

- **F1 (P1, M): Filing package.** `/tax-generate --filing-package`: line-numbered,
  form-ordered output matching official 1040/schedule layout + attachment checklist +
  "transfer to Free File / software" mapping. (True e-file/MeF is out of scope — say so.)
- **F2 (P1, S): `/tax-extend`** — Form 4868 with extension payment computed from the
  best available estimate (result.json if computed, else `/tax-project`), state extension
  notes for the profile's states, April + October `.ics` events via `tax_calendar.py`.
- **F3 (P1, M): `/tax-amend`** — post-filing corrections: snapshot filed return
  (`archive/{year}/as_filed/`), apply corrected documents through the normal
  parse/compute path, emit 1040-X three-column (original / change / corrected) summary.
  Prerequisite: A1's ledger makes "what changed" computable.
- **F4 (P2, S): 1040-ES vouchers as a filing deliverable** — when line 37 balance due
  > $1,000, `/tax-generate` auto-includes next year's voucher schedule (reuse
  `withholding.py`) instead of requiring the user to know a planning skill exists.
- **F5 (P2, S): FTF/FTP exposure note** — filing after the deadline with balance due →
  penalty/interest accrual estimate + pay-now guidance in the output.

### EPIC G — Information-return era upgrades (foreign/crypto) — **P2**

- **G1 (P3, M — deprioritized 2026-07-12: no current user holds crypto): 1099-DA
  reconciliation.** `/tax-parse-crypto` accepts a 1099-DA (or user attestation) per
  exchange; disposals from a covered exchange → Box A/D with reported basis, else C/F;
  guided per-wallet default for 2025+ per Rev. Proc. 2024-28. Becomes P1 again the day
  a crypto user appears — until then C1 surfaces the existing Box-C/F + universal-pool
  caveats loudly if the skill is invoked.
- **G2 (P1, S): FBAR set-difference check.** `/tax-fbar` diffs the account list against
  foreign payers in the parse ledger (A1) — accounts that earned interest in summaries
  can't silently miss the FBAR table. Signature-authority becomes an explicit
  yes/no interview question, not reminder prose.
- **G3 (P2, S): PFIC / Form 2555 tripwires.** Interview + `/tax-review` international
  section: foreign mutual-fund/ETF holdings → Form 8621 referral flag; foreign earned
  income → FEIE-vs-FTC note. Flag-only (computation out of scope — C1 gates).
- **G4 (P2, S): 1099-K reconciliation** — extraction spec for 1099-K + tie-out of box 1a
  against reported gross receipts (rides on A2); IRS persona gains the gross-vs-reported
  check.

### EPIC H — Interview & review upgrades — **P2**

- **H1 (P2, S): Life-event pass in `/tax-setup`** — for the *filing* year: marriage/
  divorce, birth/adoption, home sale (§121 + 1099-S prompt), inheritance, state move
  (feeds E3), alimony (pre-2019 decree). Each sets a profile flag that C1's matrix
  understands.
- **H2 (P2, S): HoH/qualifying-person tests** — cost-of-keeping-up-home >½ and
  qualifying-person checks asked when HoH selected; answers recorded in answers.md
  (substantiation trail).
- **H3 (P2, S): IRS-persona DIF upgrade** — add EITC due-diligence, round-number
  detection (whole-hundred entries), 1099-K gross mismatch to `/tax-review` step 2.
- **H4 (P3, S): Tax-law fetch source pinning** — Step-0.5 refetches must cite an
  irs.gov/state-DOR URL recorded in `tax_law_<year>.json._sources`; non-authoritative
  source → INCOMPLETE, not silently used.

### Sequencing (quarters, assuming current velocity — re-cut 2026-07-12)

1. **Now (correctness sprint):** B1–B5, B7 (engine bugs) + E1 (CA-focused labeling) +
   C1/C2 (scope guards) + B8. Small diffs, huge trust delta — everything else assumes a
   truthful baseline.
2. **Q3:** Epic A (ledger + tie-out — the CP2000 killer) + D2/D3/D4 (cheap
   common-return wins) + G2 (FBAR diff rides the ledger) + C3 + E6 (excess SDI — small
   and squarely in-persona).
3. **Q4 (filing-season prep):** F1/F2/F3 (package, extension incl. CA FTB 3519, amend)
   + D1 (Schedule A + Schedule CA itemized — the persona's top coverage gap) +
   **D9 (AMT — promoted)** + E7 (CA-ES weighting) + H1/H2/H3 + D5.
4. **Next year:** D6, D8 (if a rental user exists), E5 (contributor playbook), G3/G4,
   F4/F5, A3. **Community track (unscheduled):** D7 (EITC), NYC/NY/part-year, G1
   (1099-DA — reactivates at P1 if a crypto user appears).

### Status

- **Sprint 1 ("Now" — correctness): ✅ shipped.** B1–B5 + B7 (engine fixes) and
  B8/E1 (CA-first labeling) merged in PR #27 after five review rounds to
  convergence (tests 1049 → 1099).
- **C1/C2 (scope guards): ✅ shipped.** `agents/lib/capabilities.py` capability
  gate (`/tax-compute` step 0.7, blocking + per-rule acknowledgment) and the
  `meta.limitations` manifest printed on PDF page 1 (tests → 1140). Five new
  interview flags (`has_iso_exercise`, `moved_states_this_year`,
  `nyc_resident`, `has_household_employees`, `has_dependent_unearned_income`).
- **D2 (taxable-SS worksheet), D4 (excess-SS credit), E6 (CA excess SDI): ✅
  shipped.** `compute_taxable_ss` (Pub 915 worksheet incl. MFS paths + the
  muni-interest provisional-income input), `compute_excess_ss_withholding`
  (per-person, multi-employer only), `compute_excess_sdi` (cache-driven cap;
  TY2024+ SB 951 no-ceiling → not-applicable). Tests → 1188.
- **Epic A core (A1 parse ledger + A2 tie-out): ✅ shipped.**
  `parse_ledger.json` (canonical-category key boxes, every document parsed or
  skipped-with-reason) + `agents/lib/tie_out.py` hard gate at `/tax-compute`
  step 12.7 (coverage, category validity, per-category sums, loud
  unverifiable warnings). Tests → 1242.
- **Epic A hardening (Codex rounds 6–11 on PR #30): ✅ shipped.** Missing
  result sections/fields fail (not warn), "pending <skill>" skips block,
  Box C/F proceeds excluded from the broker tie, advisory classes for
  recomputed 1099-R box 2a / negative SSA-1099 / state refunds /
  personal-item 1099-Ks. Tests → 1266.
- **D3 (Form 2210 at filing), G2 (FBAR set-difference), C3 (persist
  /tax-review): ✅ shipped.** `compute_form_2210` (§6654 safe harbor +
  §6621 quarterly-rate accrual, FIFO payment cascade, loud degrade when
  rates unfetched) → 1040 line 38 via `/tax-compute` step 9.5;
  `foreign` marker on ledger records + `foreign_payers()` diffed against
  the FBAR table in `/tax-fbar` step 1.6 + explicit signature-authority
  interview flag; `reviews/{year}-review.md` workpaper persisted, archived
  at cutover, surfaced by `/tax-rollover` step 5.5. Tests → 1287.
- **Next (Q3 per sequencing):** A3/A4 (high-value second read; consume
  expected_sources), then Q4: D1 (Schedule A + CA Schedule CA), D9 (AMT).

### Decisions log

**Resolved 2026-07-12 (product owner):** (1) both preparer AND checker — Epic F
confirmed, F1 is a filing package; (2) CA-focused — state breadth moved to a community
track, CA depth items added (E6/E7, Schedule CA in D1, CA AMT flag in D9); (3) crypto
is nice-to-have — G1 dropped to P3; (4) persona = CA / W-2 + equity comp / foreign
accounts — AMT promoted to P1, EITC demoted to P3.

### Remaining open questions

1. **Does any current user have rental property?** Decides D8 (Schedule E) at P2 vs P3.
2. **Do any dependents have investment income >$2,600?** Decides whether kiddie-tax
   stays P3 or needs at least a C1 gate question in the interview this season.
3. **Second-extraction cost.** A3 doubles parse cost for high-value docs. Alternative:
   checksum-style prompts ("read box 1 twice, digit-by-digit"). Pilot A3 on W-2s only.
