# Product-Owner Review + Next Backlog — 2026-07-20

Reviewer lenses: product owner with tax-adviser / CPA / IRS-officer / enrolled-agent /
investor background. Every defect claim below was verified against source before being
written down (file:line checked; full suite re-run: **1646 passing** on `main`).

Predecessor: `2026-07-17-product-owner-review.md` — **every scheduled item in that
backlog is now shipped** (PRs #43–#52, tests 1506 → 1646): Epic I (TY2026 OBBBA
readiness, incl. the Schedule 1-A engine through 18 review rounds), Epic J (equity-comp
Form 8949 code-B basis correction + §423 ESPP), Epic K (verify-override hardening,
tax-law source pinning, PFIC tripwire), Epic L (§7503 deadline rollover,
`schedule_e_k1_income` phantom guard, QBI SE-deduction base netting, §6651 penalty
helper). Two consecutive backlogs have now been driven to zero.

**This review's headline is different from the last two: there is no unsafe-direction
defect cluster left to find.** The 2026-07-12 review fixed the foundation (tie-out,
capability gate, coverage tier 1, filing logistics); 2026-07-17 fixed the law year and
the persona's money (OBBBA, equity-comp basis); Epic L cleared the deferred-engineering
tail. What remains is (a) **operational season readiness** — proving the TY2026 wiring
against real fetched law before users hit it, and serving the live Oct-15 extension
population; (b) a short **in-persona coverage tier 2**; (c) **hygiene debt**; and (d) an
honest statement about when to stop building.

---

## Part 1 — State of the product (calibration)

Strengths confirmed this pass, beyond those already recorded in the predecessors:

- The OBBBA surface is now the deepest part of the engine, not the shallowest — Schedule
  1-A (tips/overtime/car-loan/senior) with per-provision caps, MAGI phase-outs,
  per-provision rounding direction, MFS denial, QBI interaction, and a filing-package
  reconciliation guard; §170(p); the §70105 QBI floor under the §199A(a)(2) ceiling;
  50% AMT phase-out; year-aware SALT cap. All cache-driven, each with a *documented
  per-block absence behavior chosen by bias direction* — understatement-risk blocks
  fail closed (`schedule_1a` and `charitable_non_itemizer` hard-error on nonzero
  input, the §603 threshold hard-errors post-2026), taxpayer-favorable blocks degrade
  loudly (`qbi_minimum` skips the floor with a note), and blocks with a statutory
  schedule fall back to it (`salt_cap`, the AMT phase-out rate). Not uniformly
  fail-closed — M1 must test each block's *declared* class, not assume one.
- The integrity chain is closed end-to-end: parse ledger → tie-out hard gate →
  second-read → expected payers → source-pinned tax law → override-visible verify →
  page-1 limitations manifest. An IRS-officer lens finds no remaining silent path from
  a source document to a filed line.
- The §6651 penalty helper plus `/tax-extend` gives the October extension population a
  real late-filing advisory — with its three base-divergence scopes (under-reported,
  partial-payment, mixed §6651(d) rate) declared not-modeled rather than modeled wrong.

Calibration note: this is the third review in eight days, and the marginal finding is
now **below the "wrong number on a common in-persona return" bar** that governed the
first two. That is the definition of done for a review-driven build phase. The backlog
below is correspondingly small, and Part 5 says explicitly what posture should replace
the sprint cadence.

---

## Part 2 — Verified defects

| ID | Defect | File | Direction |
|----|--------|------|-----------|
| BUG-15 | README directory-tree comment still says "90 tests for tax math functions" — the prose count was fixed in the 2026-07-12 pass (line 171 now says "1,000+ … see CLAUDE.md") but the tree annotation was missed. Trivial, but it is the file's second stale-count incident; fold into O2. | `README.md:187` | Doc trust only |

No engine defects found this pass. (Grep-and-read spot checks: capability-rule registry
vs template/interview invariants hold; Schedule 1-A rounding directions match the
round-17 statutory verification; `_salt_cap` prefers the fetched block; the §6651
helper's caps/overlap/minimum match the statute as verified in PR #52.)

Verified-open carry-forwards (not new, listed for completeness): PTET rate schedules
still hardcoded in `ptet.py` (the fetched-cache migration shipped only for the SALT
cap — PR #22 v2 follow-up #1, second half); `_est_marginal_fed` still probes ordinary
income (follow-up #3); 8995-A wage/UBIA/SSTB still a loud warning, not a computation;
CA Schedule P (CA AMT) still flagged-not-computed (`capabilities.py:110`); Form 8889
return-side limit test / Form 2441 / Form 5329 still MANUAL; §6601 interest caveated
not computed in the penalty helper; `/tax-harvest` §1091 basis-shift still P3; scenario
keystone still caveats (not threads) the §63(f) add-on.

---

## Part 3 — Findings by lens

### Enrolled agent — the calendar decides the priorities

It is July 20. The next three dates that touch real users: **Sep 15** (federal Q3
1040-ES — no CA Q3), **Oct 15** (TY2025 extended filing), **Jan 15** (Q4). The product
covers all three flows. Two gaps are live for the October population specifically:

- **§6601/§6622 interest is the missing half of the late-filer advisory.** The §6651
  helper prices the penalties but interest is caveated — yet the §6621 quarterly rates
  it needs are *already in the cache* (`underpayment_rates`, fetched for Form 2210).
  Daily compounding (§6622) from the due date is a small, closed-form helper. An
  extension filer deciding "pay now vs pay at filing" in August deserves the whole
  number. → M3.
- **TY2026 wiring has never been exercised against real fetched law.** Epic I added
  six new cache blocks (`schedule_1a`, `salt_cap`, `amt.phaseout_rate`,
  `charitable_non_itemizer`, `qbi_minimum`, plus the §603 threshold), several of which
  **hard-error when absent**. That is the correct bias direction — but it means the
  first TY2026 `/tax-setup` run is the integration test, and today that run would
  happen on a user. Rev. Proc. 2025-32 amounts are published; a golden-path TY2026
  fixture built from them proves every block is fetchable and wired *before* the
  season. → M1/M2.

### Tax adviser / CPA — the remaining in-persona money

- **CA Schedule P is now the largest computed-vs-flagged asymmetry.** D9 was promoted
  precisely for the CA ISO exerciser — who today gets a computed federal 6251 number
  and a verbatim "CA AMT is NOT computed" note. CA AMT (7% TMT, its own exemption and
  phase-out, its own prior-year credit) is routinely five figures on the same exercise
  that triggered the federal computation, and every input it needs (ISO bargain
  element, Schedule CA adjustments) already exists in the workspace. This is the top
  coverage item left — P1 *if* any current user exercised ISOs this year (open
  question 5), else P2. → N1.
- **8995-A limits**: the persona (high CA earners with K-1s) plausibly sits over the
  §199A taxable-income threshold, where the engine currently warns rather than
  computes wage/UBIA/SSTB limits. The warning is honest; the computation is the last
  QBI gap. → N2.
- **Form 8889 return side**: the retirement dashboard plans HSA capacity, but the
  return-side limit test (excess contributions, testing period, the 20% addition) is
  manual — squarely in-persona for CA tech HDHP holders (and CA already gets the
  non-conformity add-back). 2441 rides along; 5329 stays manual. → N3.

### IRS officer — integrity

Nothing new. The one structural note: the PTET rate schedules are the last piece of
tax law hardcoded in a lib module — and the NJ BAIT round-5 P1 already proved that
class of drift produces real understatement. Finish the migration the SALT cap
started. → N4.

### Investor — planning depth

- Carry-forwards only: `/tax-harvest` basis-shift (P3), deduction-only marginal probe
  (P3), scenario-keystone §63(f)/SE-tips threading (P3 — the projection half shipped
  in I2 round 17). Consolidated into O3; none blocks a real decision today.
- 1099-DA note: TY2025 is the first broker-reporting year, so an extension filer with
  crypto *would* be holding 1099-DAs right now. The product decision (no crypto users)
  stands; G1 stays community-track but its reactivation trigger is now live-by-date,
  not hypothetical.

### Maintainer (product hygiene — new lens this pass)

- **CLAUDE.md has become a change log wearing a guidance file's clothes.** 409 lines,
  with single "paragraphs" (the Testing bullet, the planning-skills bullet) running
  thousands of words of per-PR review history. The file's job is invariants and
  gotchas for the next session; its history belongs in `docs/`. This is real product
  risk, not tidiness: load-bearing invariants are buried in narrative, and every
  future edit risks contradicting one. → O1, with the guardrail that **no invariant
  is lost** — each moved narrative must first have its load-bearing rules distilled
  into a one-line invariant that stays.

---

## Part 4 — Backlog

Guiding principle unchanged: **never silently understate** — which now cashes out as
"prove the fail-closed TY2026 wiring before a user trips it" rather than "find the
next wrong formula".

### EPIC M — TY2026 season readiness (operational) — **P1**

> As the operator opening the TY2026 season, I want the new fail-closed cache blocks
> proven fetchable and wired end-to-end before the first real return — and the October
> extension population served with complete numbers.

- **M1 (P1, S): TY2026 golden-path fixture.** Build a full-pipeline integration
  fixture with real Rev. Proc. 2025-32 / OBBBA-era values in every Epic-I block
  (`schedule_1a`, `salt_cap`, `amt.phaseout_rate`, `charitable_non_itemizer`,
  `qbi_minimum`, §603 threshold, IRMAA tiers) and pin setup→parse→compute→generate
  against hand-computed expectations. Acceptance: every block is exercised present,
  and each block's *declared* absence behavior (fail-closed vs loud-degrade vs
  statutory-fallback — see Part 1) is pinned by a deliberately-absent variant **run
  at a year where that behavior is reachable** — the §603 threshold's absence
  variant must run at TY2027, because at TY2026 the absent-cache path returns the
  built-in $150K statutory default *by design* (the hard-error only arms for
  post-2026 indexed years), so a TY2026-only absence test would vacuously pass.
  **Scope honesty: a fixture proves wiring and computation, not fetchability** —
  `/tax-setup`'s fetch is an interactive WebSearch step, not code an integration
  test can exercise. Fetch validation is therefore a *separate operational
  acceptance* under M2: the season-open dry run performs the live fetch, records
  the K2 authoritative source URL per block in `_sources`, and must end with
  `verify_source_pinning` reporting no non-authoritative or unpinned core block.
  M1 without that step does not certify season readiness.
- **M2 (P2, S): Season-open runbook.** `docs/roadmap/` (or `docs/ops/`) checklist for
  the annual law refresh: which blocks to fetch, from which Rev. Proc. / statute,
  which statutory constants to re-verify annually (§6651 floor, SS wage base,
  retirement limits, SALT cap year), and the K2 source-pinning expectations per
  block — ending in the live-fetch operational acceptance M1 delegates here
  (fetch → pin → `verify_source_pinning` clean).
- **M3 (P1, S): §6601/§6622 interest estimator.** `compute_underpayment_interest`
  riding the `underpayment_rates` cache: daily-compounded federal interest from the
  due date to a supplied pay date, rates-parameterized, loud degrade when rates are
  unfetched (never a guessed rate). **The existing fetch contract is insufficient
  for exactly the October population this serves** — the Form 2210 window runs
  Apr 15 TY → Apr 15 TY+1 (the 2210 accrual stop), but a TY2025 extension payer on
  Oct 15 2026 needs the Apr–Oct 2026 §6621 periods. M3 therefore includes the
  cache/fetch-scope change (extend the rate timeline through the anticipated pay
  date at fetch time, with its own K2 pin), and the estimator must degrade loudly
  when the timeline ends before the pay date — never silently persist the last
  cached rate across an uncovered quarter. Threaded into `/tax-extend` (the
  pay-now-vs-October decision) and `/tax-amend` (interest on the additional tax).
  Scope: interest on tax only; interest-on-penalty accrual rules caveated.

### EPIC N — In-persona coverage tier 2 — **P1/P2**

> As the CA equity-comp persona, I want the last flagged-not-computed items that carry
> my money actually computed.

- **N1 (P1 if open question 5 is yes, else P2; M): CA Schedule P (CA AMT).** 7% TMT,
  CA exemption + phase-out (fetched into the `state` block, never hardcoded), CA
  preference/adjustment items scoped v1 to what the workspace already carries (ISO
  bargain element, standard-deduction/Schedule-CA addbacks), prior-year CA AMT credit
  carryforward via `/tax-rollover` (the 8801 pattern). The capability note flips from
  "not computed" to computed-with-scope. Acceptance: a TY fixture where federal 6251
  triggers and Schedule P triggers independently; the no-ISO CA return is unchanged.
- **N2 (P2, M): Form 8995-A.** Wage/UBIA limitation and SSTB phase-out above the
  §199A threshold, replacing the `over_threshold` warning. Inputs must be keyed at
  the **trade-or-business level, not the entity level** — the §199A limits apply
  per trade, a single K-1 can report multiple trades, and combining them is only
  permitted under a Reg. §1.199A-4 aggregation *election* with its own
  ownership/majority/factor requirements. A per-entity-only schema would let an
  implementation apply one limitation across unrelated trades and overstate the
  deduction. v1 may scope to one-trade-per-entity with that assumption attested
  and surfaced; anything beyond needs the aggregation election modeled
  explicitly. Spec first.
- **N3 (P2, S/M): Form 8889 return-side limit test + Form 2441.** 8889: contribution
  limit vs actual (employer + employee) with an excess-contribution flag — but the
  return-side test must NOT inherit the dashboard's full-year-HDHP *assumption*,
  because the failure mode inverts: on the planning side a full-year assumption
  overstates capacity (annoying), on the return side it can **miss a real excess
  contribution and its 6% §4973 excise** for a partial-year-coverage taxpayer who
  didn't use the last-month rule (understatement — the unsafe direction). The
  schema must therefore collect eligible-coverage months and the Dec-1
  last-month-rule election (+ testing-period facts) — or, absent a full-year
  attestation, the case stays MANUAL rather than getting a confident full-limit
  comparison. 2441: expenses + earned-income limits are
  NOT sufficient inputs — flipping the MANUAL status requires the interview/schema
  to first collect the eligibility gates the form actually turns on: qualifying
  person (under-13 or incapable-of-self-care + relationship/residency), the
  work-related-expense rule (both spouses working/studying, with the
  student/incapacitated deemed-earned-income amounts), the AGI-based 20–35%
  applicable percentage, employer dependent-care benefits (W-2 box 10 — excluded
  first, Part III before Part II), provider name/TIN (due diligence), and the MFS
  general disallowance with its exceptions. The `has_dependent_care` flag alone
  cannot gate a computed credit; spec these fields per the capability-registry
  invariant (template + interview + rule) before any engine change. 5329 remains
  MANUAL.
- **N4 (P2, S): PTET rate schedules → `state.ptet` fetched block.** Completes the
  PR #22 v2 follow-up the SALT-cap migration started — but NOT with `_salt_cap`'s
  silent-fallback shape. The SALT cap's fallback is a *federal statutory schedule*
  fixed by OBBBA; PTET rates are state-legislature-driven and have already drifted
  once (the NJ BAIT P1 — $35,600 on a $3M entity). Today the analyzer prices every
  year from the built-in schedule with no staleness signal at all. Required
  behavior: fetched-and-pinned block preferred; absent block → the built-in
  schedule may be used only with a LOUD unfetched-schedule warning naming the
  statutory vintage (never a silent confident price); corrupt block → hard error.
  This is the loud-degrade class from the bias taxonomy, not the
  statutory-fallback class. **Schema shape: keyed by K-1 entity state, not the
  resident state** — the cache today holds ONE flat resident-state block with a
  single `state.<code>` pin, while `/tax-ptet` deliberately prices each K-1
  entity state (a TX resident with CA and NJ K-1s needs CA *and* NJ schedules a
  resident-state block can't carry). Define a top-level map (e.g.
  `ptet.<entity-state>`) with a K2 authoritative pin per modeled entity-state
  schedule.

### EPIC O — Hygiene — **P2/P3**

- **O1 (P2, M): CLAUDE.md refactor.** Move per-PR review narratives into
  `docs/changelog/` (one file per epic); CLAUDE.md retains architecture, invariants,
  gotchas, and pointers. Hard guardrail: before any paragraph moves, its load-bearing
  rules are distilled into surviving one-line invariants; reviewed as its own PR so
  the diff is inspectable.
- **O2 (P3, XS): README refresh.** Fix BUG-15 (tree comment), re-verify counts and
  claims against current state.
- **O3 (P3, S): Planning-tool carry-forwards, consolidated.** Deduction-only marginal
  probe for `_est_marginal_fed`; scenario-keystone §63(f)/SE-tips threading;
  `/tax-harvest` §1091 basis-shift modeling. Do opportunistically; none is
  decision-blocking today.

### Community track (unchanged, unscheduled)

D7 (EITC), D8 (Schedule E rental — still no known rental user), G1 (1099-DA + crypto
per-wallet default — **reactivation trigger is now live**: TY2025 1099-DAs exist; the
day a crypto user appears this is P1), NYC/NY/part-year/nonresident, other states via
the E5 playbook.

### Sequencing

1. **Now:** M1 + M3 (season readiness + the October population) — both S, both serve
   dates already on the calendar.
2. **Next:** N1 (pending open question 5) + N4 + M2.
3. **Then:** O1 (hygiene, own PR), N2/N3 as demand appears, O2/O3 as filler.

---

## Part 5 — Posture: when to stop building

Three reviews in eight days have driven two full backlogs to zero, and this one is
one-third the size of its predecessor. The recommendation is an explicit cadence
change once Epic M lands:

- **Feature sprints end; season-driven ops begin.** The annual rhythm becomes: M2
  runbook at season-open (fetch + pin the year's law), the pipeline during filing
  season, planning skills quarterly, this review format once per season — not per
  week.
- **Usage becomes the prioritizer.** The six open questions below are now worth more
  than any further code review; every remaining P1/P2 is contingent on one of them.

### Open questions (carried + new)

1. Rental property user? (D8 P2 vs P3 — open since 2026-07-12)
2. Dependent investment income over the kiddie-tax threshold? (open since 2026-07-12)
3. Does the persona hold foreign *funds*? (K3 shipped flag-only; a "yes" is the
   trigger to consider Form 8621 computation depth — currently out of scope)
4. Any crypto user? (reactivates G1 at P1 — TY2025 1099-DAs now exist)
5. **Did any current user exercise ISOs in 2025 or 2026?** (decides N1 at P1 vs P2 —
   the largest remaining in-persona dollar gap)
6. **Is any K-1 owner over the §199A taxable-income threshold?** (decides N2 urgency)
