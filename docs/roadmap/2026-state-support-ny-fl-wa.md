# State Support Expansion — New York, Florida, Washington

Source: product-owner review with CPA / EA / IRS-officer lenses, conducted 2026-08-01.
All structural tax facts below were researched against primary sources (tax.ny.gov,
dor.wa.gov, floridarevenue.com, NY Tax Law, RCW 82.87, F.S.) and passed an adversarial
fact-check round; per-year figures cite TY2025/TY2026. Codebase gaps were inventoried
against the current `main` (1049-test suite, post PR #23).

> ## ⚠ Re-baseline addendum (2026-08-01, same day)
>
> The codebase-gap claims below were written against a **stale local checkout (PR #23)**;
> origin/main was 40 PRs ahead (PR #64, 1891 tests). **The state-LAW research (NY/WA/FL
> facts, rates, deadlines) remains fully valid.** Corrections to the codebase claims:
>
> - `docs/contributing/state-module-playbook.md` (PR #40) now prescribes the house pattern
>   for adding states: **enumerate-and-register** with doc-sync tests. **Epic 0.3's central
>   `state_registry.py` is SUPERSEDED** (user ruling 2026-08-01) — the `type` discriminator
>   (0.1/0.2) survives as a playbook variant for no-income-tax states.
> - A **Schedule A engine exists** (`compute_schedule_a`, PR #33) including the
>   sales-vs-income SALT election and a fetched `federal.salt_cap` — Epic 1.3 reduces to
>   producing the `state_local_sales_taxes` INPUT (optional-tables method).
> - A **capability gate exists** (`capabilities.py`, PR #28): non-CA states get an honest
>   "bracket-based ESTIMATE" note; part-year moves are gated at C1
>   (`moved_states_this_year`) — Epic 4.3's caveat partially exists; Epic 4.1/4.2 stand.
> - `state.ptet` fetched block (PR #56) and §7503 next-business-day rollover (PR #49)
>   shipped — two "v2 follow-ups" cited below are DONE.
> - `tie_out.CATEGORY_TARGETS` (PR #30) pairs state withholding categories with
>   `/tax-parse` — a registration surface the epics below must respect (FL: deliberately
>   none — no Box 17 exists).
> - CA state-specific compute functions now exist (`compute_ca_itemized`, Schedule P AMT,
>   PR #33/#55) — "zero state functions in tax_computations.py" is no longer true.
> - Florida DOR's domain is `floridarevenue.com` (.com) — the source-pin authoritative-host
>   allowlist (PR #48) must include it for any FL fetched-law block.
>
> Phase 1 (Epic 0 core + Epic 1) is re-planned accordingly in
> `docs/superpowers/plans/2026-08-01-state-engine-florida.md` (RE-BASELINED revision).

## Why these three states force a redesign (and that's a feature)

The toolkit's state layer today is **CA-shaped**: one `california_540` result section, one
`state.brackets` graduated-income model, CA hardcoded in ≥6 places that must move in
lockstep (`result_template.json`, `projection.STATE_SECTION_KEYS`,
`yoy_diff.STATE_MOVER_FIELDS`, `generate_tax_pdf.py`, `tax-compute.md` step 10,
`answers_template.md`). The three requested states are three **different tax regimes**,
which is exactly the forcing function needed to generalize the layer once instead of
bolting on three more special cases:

| State | Regime | The core product problem |
|-------|--------|--------------------------|
| **NY** | Full graduated income tax + **local sub-taxes** (NYC resident tax, Yonkers surcharge) + AGI modifications + a recapture tax that breaks plain bracket math | The deepest compute build: a second full return with its own itemize-vs-standard choice |
| **FL** | **No individual income tax** at all | Absence-of-tax must become a first-class, *complete* state — today the schema can't distinguish "no tax" from "data not fetched", so `check_freshness` nags forever and `/tax-harvest` shows a spurious warning |
| **WA** | No income tax **but** a capital-gains **excise** on federal net *long-term* gain above a ~$278K deduction | A non-income tax base the `state.brackets` vocabulary cannot express; faking it with brackets would tax a wage-only WA filer who owes nothing |

**Keystone architectural insight:** the schema needs a state `type` discriminator
(`graduated_income | none | ltcg_excise`) so that *absence of tax data* is distinguishable
from *absence of tax*. Every downstream gap traces back to this.

## What already works — do not rebuild

- **NY PTET is fully modeled**: `/tax-ptet` + `/tax-calendar` carry NY's graduated PTET
  schedule and the March-15-in-year election trap, keyed on `flags.k1_states`
  (entity states, deliberately not `address.state`). Verified unchanged for 2026.
- `flags.k1_states` is the right hook for a FL/WA resident holding an NY K-1 — no change needed.
- `retirement_dashboard` already strictly validates `address.state` and NY **conforms** to
  federal HSA/401(k)/IRA treatment (confirmed — no CA/NJ-style add-back), so its conformity
  sets need no NY entry.
- `loss_harvest` / `projection` degrade paths (empty `state.brackets` → $0 + banner) are the
  *correct math* for FL — only the messaging and freshness-nag are wrong.
- Federal-only caveats in `roth_ladder`/`withholding` are accidentally **correct for WA**
  (conversions and wages are outside the excise base) and wrong only for NY.

---

## Epic 0 — State engine generalization (prerequisite, do first)

The `/tax-ptet` v2 follow-up ("lift PTET schedules into a `state.ptet` block") is the same
schema-evolution vehicle — design the per-state block once so income tax, local tax,
excise, and PTET data all live under it.

| # | Story | Touches | Acceptance sketch | Effort |
|---|-------|---------|-------------------|--------|
| 0.1 | **State `type` discriminator** in `tax_law_<year>.json` (`graduated_income` \| `none` \| `ltcg_excise`) + formalize `state.code` (today `projection.py` reads a `code` field that `create_state_template` never emits — fixtures hand-add it) | `tax_law_fetcher.py`, fixtures | A `type: "none"` state is representable; `code` guaranteed present | S |
| 0.2 | **Type-aware `check_freshness`**: drop the unconditional "state (no state tax data configured)" flag when `type=none`; stop flagging legitimately-empty lists when a sibling gate says the sub-tax doesn't apply (`nyc_tax.applies=false` → skip `nyc_tax.brackets`); per-type required-field sets (an `ltcg_excise` block requires `deduction`+`rate`, never `brackets`) | `tax_law_fetcher.py` | FL/WA/upstate-NY caches can reach FRESH; no permanent INCOMPLETE nag | S |
| 0.3 | **Generic state result section**: registry keyed by state code replacing the hardcoded `california_540` fan-out (also de-CA-ify the unconditional leaks: `balance_due_summary.california_refund`, `ira_contribution.ca_savings`) | `result_template.json`, `projection.STATE_SECTION_KEYS`, `yoy_diff`, `generate_tax_pdf.py`, `tax-compute.md`, `answers_template.md` | Adding a state = one registry entry, not 6 parallel edits; YoY diff and projection see any state section | M |
| 0.4 | **Data-driven AGI modifications block** (`state.modifications`: additions/subtractions). CA's HSA add-back is prose in `tax-compute.md` + hardcoded fields today; NY's IT-225 list is longer and needs a declarative home | `tax_law_fetcher.py`, `tax-compute.md` | CA HSA add-back re-expressed through the block (regression-neutral); NY list plugs in | M |
| 0.5 | **Local-tax block shape**: `state.local: [{name, applies_flag, kind: 'brackets' \| 'pct_of_state_tax', ...}]` + machine-readable profile residency flags (`nyc_resident`, `yonkers_resident`) — free-text `address.city` is not decidable | schema, `taxpayer_profile_template.json`, `/tax-setup` interview | Yonkers' 16.75%-of-state-tax shape is expressible; NYC applicability is a user-asserted flag, not a city-string guess | S |
| 0.6 | **Per-status state standard-deduction maps** (full single/mfj/mfs/hoh key) — `projection._binary_status_key` would silently give an NY HoH filer the single deduction ($8,000 vs $11,200) | `projection.py`, schema | HoH state std correct; mirrors the known federal MFS/HoH CG-bracket gap | S |
| 0.7 | **State compute functions live in `tax_computations.py`** (parameterized, self-tested — per repo rule). Today there are ZERO state functions there; CA's surcharge/SDI live as skill prose | `tax_computations.py` | `compute_state_bracket_tax` + hooks for NY recapture / WA excise land with self-tests | S |

> **Re-baseline annotation (2026-08-01):** 0.1/0.2 delivered as the type-none discriminator +
> FL template (playbook-conformant); 0.3 registry SUPERSEDED by the state-module playbook's
> enumerate-and-register pattern (re-baseline ruling 2026-08-01); 0.4-0.7 live with the
> WA/NY modules.

## Epic 1 — Florida: the no-income-tax pathway (pilot for `type: none`)

Smallest epic; ship first to validate the discriminator end-to-end. The product bar is
CPA-grade **affirmative absence**, not silent emptiness.

> **Status (2026-08-02): ✅ Done (this PR)** — `feat/state-engine-florida`, all five stories
> shipped as Tasks 1-9 (see `docs/superpowers/plans/2026-08-01-state-engine-florida.md` and
> `docs/changelog/epic-P.md`). Suite 1891→1925 (+34).

| # | Story | Why | Effort | Status |
|---|-------|-----|--------|--------|
| 1.1 | **Affirmative no-return statement**: for `type=none`, `/tax-compute` writes a positive assertion into the state section ("Florida imposes no personal income tax — Fla. Const. Art. VII §5; no FL individual return exists or is required") and `/tax-generate` renders it; kill the CA-branded PDF/header for non-CA filers | A FL client reading an empty section can't tell correctness from a gap; also fixes `generate_tax_pdf.py` rendering "California Full-Year Resident" for everyone | S | ✅ Done (this PR) |
| 1.2 | **Suppress spurious warnings on `type=none`**: `/tax-harvest` banner says "no state income tax — $0 state savings is correct", not "state not modeled"; `tax-parse.md`'s missing-Box-17 flag and `tax-generate.md`'s step-4 branch get their machine-readable signal | Bias-direction: today's banner reads a *right* answer as *missing data* — erodes trust | XS | ✅ Done (this PR) |
| 1.3 | **Sales-tax SALT optimizer** (federal-side, the real FL win): Schedule A line 5a election is essentially always *sales* tax for FL (no income tax to elect, Box 17 empty). IRS optional sales-tax tables (AGI + exemptions + 6% state + county surtax) + interview prompt for big-ticket adds (vehicle/boat). Newly material under the OBBBA cap: **$40,000 TY2025 / $40,400 TY2026 (indexed)**, phase-down 30% of MAGI over **$500K TY2025 / $505K TY2026 (indexed — do NOT cache the 2025 threshold as static)**, floor $10K, reverts 2030 | The one federal computation FL residents systematically miss; adversarial fact-check caught that BOTH the cap and the phase-down threshold index annually | S | ✅ Done (this PR) |
| 1.4 | **Calendar items** (flag-gated): DR-405 tangible personal property Apr 1 (gate on Schedule C + a new `has_business_tpp` flag; $25K exemption, 25% failure-to-file penalty), homestead DR-501 Mar 1 (first-year-FL-homeowner flag) | Real county-level deadlines with real penalties; both statutory dates | XS | ✅ Done (this PR) |
| 1.5 | **`/tax-review` FL section**: use-tax self-reporting note (DR-15MO), nonresident-exposure reminder (FL residency does NOT remove NY IT-203 obligations on NY-source income — and with no FL return there is no resident-state credit, so other-state tax is a pure cost) | EA-lens completeness; feeds Epic 4 | XS | ✅ Done (this PR) |

## Epic 2 — Washington: capital-gains excise module (pilot for `type: ltcg_excise`)

A different tax base, not a different bracket table. **Verified numbers:** 7% on WA-allocated
federal net long-term capital gain above a standard deduction — $250K (2022) / $262K (2023) /
$270K (2024) / **$278K (2025)** / 2026 unpublished as of Aug 2026 (fetch at runtime, never
extrapolate); **+2.9% on the portion over $1,000,000 (fixed, not indexed) effective TY2025**
(ESSB 5813). Legal status settled (Quinn 2023, cert denied 2024, I-2109 repeal failed Nov 2024).

| # | Story | Why | Effort |
|---|-------|-----|--------|
| 2.1 | **`state.wa_capital_gains` tax_law block** `{standard_deduction, surcharge_threshold, rates, charitable_threshold, charitable_cap}` + `/tax-setup` fetch step; leave `state.brackets` EMPTY so income-bracket consumers correctly no-op | Encoding WA as `brackets=[{rate:0.07,min:250000}]` would make projection/harvest tax *ordinary income* — a wage-only WA filer would show phantom liability | S |
| 2.2 | **`compute_wa_capital_gains` keystone** in `tax_computations.py`: base = federal net **LT** gain (§1222(7) sense — **short-term losses do NOT offset**; start from Schedule D LT net, NOT line 16) → remove exempt/non-WA-allocated gains *and losses* → **add back pre-2022 LT loss carryforwards** → ONE combined deduction per return regardless of filing status → 7% + 2.9% tiers → charitable offset (excess over $278K, cap $111K, TY2025, both indexed) | The three add-back rules and the combined-deduction rule are the correctness traps the fact-check hardened; doubling the deduction for MFS-style logic UNDERSTATES tax → hard-error per bias taxonomy | M |
| 2.3 | **Result section + verify + PDF**: `washington_ltcg_excise` block via the 0.3 registry `{federal_net_ltcg, exemptions, wa_allocated, deduction, taxable, tax, charitable_offset}`; `tax-verify.md` gets an LTCG-base line comparison (a WA return has no AGI concept); PDF renders a WA summary page (the WA return is NOT part of federal e-file — separate My DOR filing with federal return attached) | Exemption flags per disposal: real estate, retirement-account assets, condemnation, qualifying farm livestock (>50% farming income), depreciable trade/business property; v1 = flags + caveats, not auto-classification | M |
| 2.4 | **`/tax-harvest` WA mode**: LT-loss harvesting has a direct 7–9.9% state payoff above the deduction; **ST harvesting saves $0 state-side** — the exact inverse sensitivity of the current "ordinary rate × Δ taxable income" model. Model the deduction cliff (losses only pay when gains exceed $278K) | Today real WA savings are silently understated to $0 — safe direction but wrong, and the banner blames missing data | S |
| 2.5 | **Crypto importer synergy**: `/tax-parse-crypto` LT rows are exactly the WA base input (DOR: crypto = intangible, **domicile-at-time-of-sale** test); ST rows + staking/ordinary income are irrelevant to WA. Caveat: mid-year movers need per-transaction domicile, not proration | Free leverage from PR #23; the allocation caveat prevents silent understatement for movers | XS |
| 2.6 | **Calendar + workflow events** gated on a new `flags.wa_capital_gains` (domicile + expected LT gains > deduction): Apr 15 filing/payment (with federal deadline); "confirm federal extension in My DOR" reminder — extension extends FILING only, payment still due Apr 15; **no quarterly estimates exist** — don't emit phantom 1040-ES-style state events | Deadline mechanics verified against DOR; SSB 5314 (2025) also added mandatory amended returns when the federal return changes — `/tax-review` note | S |
| 2.7 | **WFTC reminder** in `/tax-review`: EITC-eligible WA residents must file a SEPARATE application (workingfamiliescredit.wa.gov, $335–$1,330 TY2025); not a return line; ITIN filers qualify | Easy-to-miss money — the EA lens | XS |
| 2.8 | Long-tail (flags/caveats, explicitly NOT v1 math): family-owned small business deduction (RCW 82.87.070), other-jurisdiction credit (lesser-of), B&O credit — note the **direction flipped in 2025** (pre-2025: credit against B&O; SSB 5314: credit against the cap-gains tax) | Document so caveats don't lie; the B&O flip is exactly the hardcoded-law drift the PTET P1s taught | XS |

## Epic 3 — New York: IT-201 resident return (the long pole)

The deepest build. Verified structural facts that drive the design:

- **Rates are year-gated statutory tables, not indexed**: TY2025 bottom-five
  4.00/4.50/5.25/5.50/6.00%; **TY2026 cut year 1: 3.90/4.40/5.15/5.40/5.90%**; TY2027–2032
  year 2: 3.80/4.30/5.05/5.30/5.80%. Top rates 9.65/10.30/10.90% **extended through TY2032**
  (FY2026 budget), statute reverts to 8.82% in 2033. Three real bracket tables
  (Single/MFS, MFJ/QSS, HoH) — 9.65% starts at $1,077,550 / $2,155,350 / $1,616,450.
- **Standard deduction not indexed**: $8,000 S/MFS, $16,050 MFJ, $11,200 HoH ($3,100 dependent filer).
- **Tax benefit recapture** (NYAGI > $107,650, linear phase over $50K per bracket) makes a
  plain bracket function WRONG for any high earner — it needs BOTH NYAGI and taxable income.
- **NYC resident tax** 3.078/3.762/3.819/3.876% (4 brackets, own breakpoints, unchanged 2026);
  **Yonkers** = 16.75% *of net state tax* (a pct-of-another-tax shape) + 0.5% nonresident earnings tax.
- **MCTMT is a true statutory cliff** (binary by law, not by modeling shortcut): $1 over the
  per-zone threshold taxes ALL net SE earnings in that zone. Zone 1 (NYC) 0.60% / Zone 2 0.34%;
  threshold **$50K TY2025 → $150K TY2026** — a year-gated constant like the §603 handling.
- **IT-558 is a dead form** — all modifications flow through IT-225/dedicated lines now.

| # | Story | Why | Effort |
|---|-------|-----|--------|
| 3.1 | **NY tax_law block**: full 3-status year-gated bracket tables, std deductions, recapture parameter tables `{threshold, base, incremental benefit, $50K width}` per status, NYC brackets, Yonkers rates, dependent exemption ($1,000), credit parameters. Replace the current 2-field NY stub | The fetch can be seeded from statute for 2025–2032 but MUST be year-aware — three different bottom-five rate sets in that window | M |
| 3.2 | **`compute_ny_tax` with benefit recapture** in `tax_computations.py` (inputs: NYAGI + NY taxable income; per-status recapture tables; self-tested against IT-201 worksheets) | The single biggest NY-specific engine requirement; also breaks naive marginal-rate math in planning skills — inside a recapture phase-in band the true marginal rate exceeds the bracket rate | M |
| 3.3 | **IT-225 modifications via the 0.4 block** — additions: 414(h) pickup (W-2 Box 14), NYC IRC-125, non-NY muni interest, 529 nonqualified/rollover recapture (incl. post-9/5/2024 529→Roth rollovers), **PTET add-back** (pairs with IT-653 credit — without it an NY return understates NYAGI); subtractions: Social Security (fully exempt), US government bond interest, NYS/local/federal pensions (100%), **$20K/person pension-and-annuity exclusion age 59½+**, NY 529 deduction ($5K/$10K MFJ) | The closed must-have set per the CPA review; `/tax-parse` must learn to surface 414(h)/IRC-125 from W-2 Box 14 | M |
| 3.4 | **NY standard-vs-itemized (IT-196), decoupled**: NY may itemize even when federal took the standard; state income tax added back; pre-TCJA survivals (2%-misc, uncapped real-estate tax); high-income limitation (phase-downs $100K–$525K, 50% haircut $525K–$1M, charitable-only 50% >$1M, 25% >$10M) | The itemize decision must run independently per jurisdiction — a structural difference from CA's conformity model | M |
| 3.5 | **Local layer**: NYC resident tax + NYC school tax credit; Yonkers resident surcharge + Y-203 nonresident earnings tax — driven by the 0.5 profile flags | Planning-skill impact: a NYC resident's true state+city marginal is ~10–14%; `/tax-harvest` reading only `state.brackets` understates savings ~3.9pp | S |
| 3.6 | **MCTMT (self-employed)**: cliff at year-gated threshold, per-individual per-zone even on MFJ, rides IT-201 lines 54a–e and the IT-2105 voucher | Bias: cliff taxes are the one place binary IS the statute — encode with the year-gate pattern | S |
| 3.7 | **Credits tier 1**: Empire State child credit ($1,000 <4 + $330 TY2025 / $500 TY2026-27 ages 4–16, refundable, phased at $16.50/$1,000 over $110K MFJ/$75K S-HoH), NY EITC (30% of federal, *reduced by the household credit*), child & dependent care, college tuition credit/deduction, household credit | Covers the overwhelming majority of NY filers; tier 2 (IT-214 circuit breaker, noncustodial EITC, niche 2025 credits) deferred | M |
| 3.8 | **Estimates + withholding**: IT-2105 (one voucher covers NYS+NYC+Yonkers+MCTMT) 4/15, 6/15, 9/15, 1/15 into `/tax-calendar`; safe harbor 90% current / 100% prior / **110% if prior NYAGI > $150K ($75K MFS)** — same dollar trigger as federal but measured on NYAGI; extend `/tax-withholding` with an NY layer (its "federal only" line is materially wrong for a ~10% marginal state) | First state estimated-tax events in the calendar; $300 minimum-owed trigger applies per tax (NYS/NYC/Yonkers) | M |
| 3.9 | **PTET owner-side reconciliation**: IT-225 add-back + IT-653 credit wiring into `/tax-compute`; add the separate **NYC PTET (flat 3.876%, same Mar-15 window)** to `/tax-ptet` | Completes the PTET loop the entity-side skill already models; verify exact IT-225 code numbers during implementation (flagged uncertain in research) | S |
| 3.10 | **Parse/forms map**: IT-2 (W-2 transcription), Box 14 414(h)/IRC-125 extraction, refund-to-529 (IT-195); do NOT scaffold IT-558 (dead form) | `/tax-parse` groundwork for everything above | S |
| 3.11 | **Planning-skill NY layers**: `/tax-harvest` state savings from NY+NYC combined marginal (NY taxes cap gains as ordinary — the v1 harvest assumption is exactly right here); fix `roth_ladder`'s blanket "state not modeled" caveat (wrong for NY, accidentally right for WA/FL) | Anchored-skill contract extension; recapture-band marginal-rate caveat from 3.2 applies | S |

**Deferred (explicitly out of v1, documented as caveats):** IT-203 part-year/nonresident
returns and income allocation; convenience-of-the-employer telecommuter rule; NY-specific
credit carryforward rollover entries. These need the single-state assumption in
`taxpayer_profile.address.state` to be relaxed — see Open Questions.

## Epic 4 — Residency & multistate guardrails (the IRS-officer epic)

NY→FL is the most-audited migration corridor in the country; a toolkit serving these three
states will inevitably serve movers.

| # | Story | Why | Effort |
|---|-------|-----|--------|
| 4.1 | **Residency as user-asserted profile input** (statutory-resident vs domicile vs nonresident), with the tests documented in skill text, not computed: NY statutory resident = **184+ days** (any part of a day counts) AND a permanent place of abode maintained "substantially all of the year" (**>10 months** per the current Nonresident Audit Guidelines — down from 11) | Same pattern as `subject_to_rmd_*`: cohort/fact-dependent rules stay user-asserted; day-count computation is v2+ | S |
| 4.2 | **NY→FL/WA mover checklist** in `/tax-review`: FL Declaration of Domicile (F.S. 222.17, recorded with clerk of circuit court), DR-501 homestead by Mar 1 — **claiming FL homestead while keeping NY STAR is disqualifying/audit bait** — driver license, voter/vehicle registration; plus the items NY auditors actually weigh (day counts, "near and dear" items, home size comparison) | The single highest-$-stakes advisory content for this corridor; move-year = IT-203 part-year + ongoing nonresident exposure | S |
| 4.3 | **Nonresident-nexus interview questions** in `/tax-setup` (out-of-state workdays, rental property, K-1 entity states — even for FL/WA clients) + a loud "nonresident returns not prepared by this toolkit (v1)" caveat wherever source income in another state is detected | Bias-direction: silence here lets a user believe the toolkit covered an obligation it didn't — the worst failure class | XS |

---

## Build order & sizing

```
Epic 0 (schema)  ──►  Epic 1 (FL, pilots type=none)  ──►  Epic 2 (WA, pilots ltcg_excise)
                                                             │
                                                             ▼
                      Epic 4 (residency guardrails) ◄──  Epic 3 (NY, the long pole)
```

| Epic | Stories | Total effort | Rationale for order |
|------|---------|--------------|---------------------|
| 0 — Engine | 7 | ~2 M-equiv | Everything depends on the discriminator + registry |
| 1 — FL | 5 | ~1 M-equiv | Smallest; validates `type=none` end-to-end; immediate user value (SALT optimizer) |
| 2 — WA | 8 | ~2.5 M-equiv | Validates the non-income base + registry with a *small* compute surface before NY |
| 3 — NY | 11 | ~5 M-equiv | Largest compute build; lands on a proven engine |
| 4 — Residency | 3 | ~1 M-equiv | Content-heavy, code-light; can parallel Epic 3 |

FL before WA before NY is deliberate: each epic de-risks the next architectural layer
(discriminator → new tax base → local taxes + modifications + recapture) while shipping
standalone value.

## Bias-direction ledger (house taxonomy applied)

| Risk | Direction | Treatment |
|------|-----------|-----------|
| WA: doubling the combined deduction for separate filers | UNDER-states tax | Hard error / interview question |
| WA: assuming zero WA allocation (domicile unset) | UNDER-states tax | Domicile is an explicit `/tax-setup` flag; no silent default |
| WA: allowing pre-2022 loss carryforwards | UNDER-states tax | Add back unless user asserts post-2021 WA-allocated origin |
| WA: `/tax-harvest` reporting $0 state savings | UNDER-states savings (safe) but banner blames missing data | Fix messaging via `type` (2.4) |
| NY: plain bracket math above $107,650 NYAGI | UNDER-states tax | Recapture is v1-mandatory, not a fast-follow (3.2) |
| NY: skipping the PTET add-back | UNDER-states NYAGI | Ships with 3.3, not deferred |
| NY: HoH getting the single standard deduction | OVER-states tax | Fixed by 0.6 before NY ships |
| FL: OBBBA phase-down threshold cached as static $500K | OVER-states tax for $500–505K MAGI band in TY2026 | Threshold is fetched/indexed, never hardcoded (1.3) |
| All: "state not modeled" banner on a no-tax state | Misreports a right answer as a gap | `type=none` messaging (1.2) |
| MCTMT / WA-deduction / recapture cliffs | Binary-vs-continuous check | MCTMT is genuinely binary BY STATUTE (encode as cliff); recapture is continuous (model the $50K phase, never a flag); WA deduction is a threshold on a continuous base |

## Open product questions

1. **Part-year/nonresident scope.** `taxpayer_profile.address.state` is single-state,
   full-year; `prior_year.state_residency_changes` is write-only prose nothing consumes.
   Multi-state representation (keyed `state: {CODE: {...}}` vs list) touches every
   `tax_law.get('state')` consumer. Decision needed before Epic 3 finishes: accept
   full-year-only for v1 (with the 4.3 caveat), or invest in the keyed schema now?
   Recommendation: full-year-only v1; design the 0.1 schema so `state` *can* become keyed
   later without renaming fields.
2. **NYC/Yonkers residency source of truth.** Recommendation in 0.5 is user-asserted flags
   (free-text city is undecidable). Alternative — ZIP-based inference — rejected: NYC
   residency for tax is fact-dependent (statutory residency applies at the city level too).
3. **NY credits tier 2** (IT-214 circuit breaker, noncustodial EITC, 2025 niche credits):
   include in 3.7 or defer? Recommendation: defer with visible "not modeled" notes —
   same pattern as PTET's non-modeled states.
4. **WA exemption auto-classification** (was a disposal real estate? retirement-account?):
   v1 treats these as per-disposal user flags. Auto-classification from broker data is a
   v2 candidate alongside the crypto importer's 1099-DA reconciliation.
5. **Where does the sales-tax optimizer live?** Options: inside `/tax-compute` Schedule A
   logic (recommended — it's a return computation, not planning) vs a standalone skill.

## Primary-source anchors (for the eventual tax_law fetch steps)

- NY: Tax Law §601 (rate tables, FY2026 budget A3009/S3009-C), tax.ny.gov IT-201/IT-225/IT-196
  instructions, Nonresident Audit Guidelines (2022+, >10-month PPA), MCTMT TY2026 threshold change.
- WA: RCW 82.87 (.050 exemptions, .070 small-business deduction, .100 other-jurisdiction credit),
  ESSB 5813 (2025 surcharge), SSB 5314 (2025 compliance + B&O credit flip), dor.wa.gov annual
  deduction adjustments, Quinn v. Washington (2023).
- FL: Fla. Const. Art. VII §5; F.S. 196.183 (TPP $25K), 222.17 (Declaration of Domicile),
  197.162 (discounts); Amendment 5 (2024) CPI-adjusted homestead ($50,722 for 2025);
  IRS §164(b)(7) OBBBA SALT cap + indexed phase-down ($40,400 / $505K TY2026).
