# PFIC §1291 excess-distribution engine (P4) — design spec

Status: **requirements / spec-first.** Trigger: open question #3 resolved —
Russia-domiciled ETFs (PFICs) at a foreign broker, some frozen, a taxable event
possible. The near-term slice (PR #63) already makes such a return **fail closed**
(`pfic_taxable_event` BLOCK + 8938/FBAR/sanctions surfaces). This spec scopes the
engine that replaces the BLOCK with a *computation* of the punitive §1291 tax.

**This is the demand-gated P4 from `2026-07-20-foreign-funds-pfic-design.md`.**
It is large and law-intensive; the mechanics below are written to best current
understanding and are **acceptance-gated on the V-items in §7** (primary-source
verify before implementation, per the repo rule — never assert §1291 mechanics
from memory).

---

## 1. Problem

The default PFIC regime (§1291) applies to any PFIC for which no timely QEF
(§1295) or mark-to-market (§1296) election was made. For a **sanctioned/delisted
Russian fund** the elections are effectively unavailable — QEF needs a PFIC
Annual Information Statement the fund won't issue, and MTM needs §1296(e)
"marketable stock" (regularly traded on a qualified exchange) that a delisted
security fails (V2) — so **§1291 is unavoidable**, and computing it is the only
alternative to blocking the return.

§1291 is **punitive and cannot be approximated** by the ordinary/qualified-
dividend path: it back-loads the tax to the highest rate of each prior year plus
a compounding interest charge. Routing a PFIC distribution or sale through the
normal engine understates tax — the whole reason the current gate BLOCKs.

## 2. Scope (v1)

- **§1291 only.** No QEF/MTM computation (surfaced as unavailable for sanctioned
  funds; a separate future item if a marketable/QEF-eligible fund appears).
- **The two §1291 taxable events:**
  1. an **excess distribution** (§1291(b)) — the part of the year's distributions
     exceeding 125% of the average distributions over the prior 3 years;
  2. a **disposition** (§1291(a)(2)) — the ENTIRE *gain* on sale/redemption is
     treated as an excess distribution. A disposition at a **LOSS** is NOT a
     §1291 event — it is an ordinary capital loss on Schedule D (§1291 converts
     only recognized gain); the caller routes losses there, never through
     `compute_pfic_1291`. (A **frozen** fund usually cannot be
     sold — so for the frozen holdings the live path is a *distribution* if any is
     received despite the block, or a future deemed event; the engine handles
     both, and blocked-distribution constructive-receipt stays the MANUAL
     `sanctioned_blocked_asset` question — do not assume a blocked distribution
     was received.)
- **Per fund, per TAX YEAR — aggregate first.** The §1291(b) 125% test is on the
  fund's TOTAL distributions for the year, so all distributions from one fund in
  the tax year are summed BEFORE the excess-distribution test (and each prior
  year's total feeds the base average); a disposition's whole gain is then its own
  excess distribution. Do NOT run the test per individual payout — two
  sub-threshold distributions would each escape while their annual total is an
  excess distribution. Multiple funds are looped; no cross-fund netting (each
  PFIC stands alone).
- **Federal only.** State handled downstream by `/tax-compute`.

## 3. Mechanics (§1291 — VERIFY per §7 before coding)

For an excess distribution `E` (or a disposition gain treated as one) on a fund
held over `H` days spanning tax years `y0..yc` (yc = current):

1. **Allocate `E` ratably over the holding period** (§1291(a)(1)(A)) — per-day,
   then summed into each tax year the fund was held: `E_y = E × (days_in_y / H)`.
2. **Current-year + pre-PFIC portions are ORDINARY income THIS year**
   (§1291(a)(1)(B)): `E_yc` and any `E_y` for years before the fund was a PFIC go
   straight onto the current return as ordinary income (1040 Schedule 1 / the
   PFIC ordinary line), taxed at the normal current-year rates.
3. **Each PRIOR PFIC year's portion is taxed at that year's HIGHEST ordinary rate**
   (§1291(c)(2)) — NOT the taxpayer's actual bracket, and NOT the current rate.
   `deferred_tax_y = E_y × highest_ordinary_rate(y)`. This needs a
   **fetched/pinned historical top-rate table** (`federal.historical_top_rate`,
   keyed by year — the no-hardcoded-tax-law rule; loud-degrade/refuse if a needed
   year is absent, never guess a rate).
4. **Interest charge on each prior-year deferred tax** (§1291(c)(3)) — §6621
   underpayment interest **compounded daily** from that prior year's return due
   date to the current year's return due date. **Reuse the M3
   `compute_underpayment_interest` machinery** (daily compounding, 365/366-leap
   denominators, `underpayment_rates` cache, refuse-on-uncovered-quarter).
5. **Total §1291 tax = current-year ordinary tax on (2) + Σ deferred_tax_y (3) +
   Σ interest_y (4).** The deferred tax + interest are an ADD-ON to TOTAL TAX
   (Form 8621 Part V → Form 1040 line 24: deferred tax to the LEFT of line 24,
   interest via Schedule 2 line 17p → line 23), added AFTER nonrefundable credits —
   NOT folded into line 16 (which credits reduce) and not run through brackets
   again.

**Excess-distribution threshold (§1291(b)(2)(A)):** `E = max(0, current_year_
distributions − 1.25 × avg(prior 3 years' distributions))`. First-year rule
(§1291(b)(2)(B)): no prior distributions → the entire current distribution can be
an excess distribution only after the first year; **the year the taxpayer first
holds the fund, there is NO excess distribution** (the 125% test needs a base
period). Verify the exact first-year/short-holding treatment (V1).

## 4. Inputs / schema

Extends `foreign_holdings_detail.holdings[]` (already added in PR #63) — per PFIC
fund with a taxable event:
- `acquisition_date`, `pfic_start_date` (when the fund became a PFIC; usually =
  acquisition for a foreign fund), `disposition_date` (if sold).
- `distributions_by_year`: `{year: amount_usd}` for the prior-3-year average +
  current-year distribution.
- `disposition_gain_usd` (proceeds − basis) when sold — the whole gain is the
  excess distribution.
- All USD-translated (FC distributions translated at each event-date spot rate —
  the §988 sourcing note does NOT apply here; PFIC amounts are already ordinary).

New fetched tax-law block: `federal.historical_top_rate` `{year: rate}` (highest
ordinary/individual rate per year), pinned per K2. `underpayment_rates` already
exists (reused for the interest charge).

## 5. Wiring

- `compute_pfic_1291(fund_inputs, historical_top_rates, underpayment_rates, ...)`
  in `tax_computations.py` — pure, rates-parameterized, 2-dp rounded, `_main`
  self-test; returns per-year allocation, current-ordinary, deferred-tax, and
  interest, plus the Form 8621 Part V line map.
- `/tax-compute`: when `has_pfic_taxable_event` AND the fund inputs are present,
  compute it and route the total to the "tax from Form 8621" add-on; the
  capability rule flips from BLOCK to **computed-with-scope** (the `iso_exercise`
  / `dependent_care` precedent). When inputs are absent it STILL BLOCKs (no
  silent skip).
- `result_template`: a `form_8621_pfic` block (per-fund Part V detail).
- `/tax-rollover`: the holding period + prior-distribution history carry forward
  (a §1291 fund spans years).

## 6. Bias direction

- §1291 is punitive → computing it can only over- or exactly-state; the risk is
  the INVERSE (understatement), so: a missing historical top rate, an uncovered
  §6621 quarter, or an absent distribution history **refuses/degrades loudly and
  keeps the BLOCK** rather than computing a too-low number.
- Blocked/frozen distributions are NOT assumed received (constructive-receipt
  stays MANUAL) — the engine computes only on distributions/dispositions the user
  attests actually happened.
- First-year / short-period edge cases resolve toward "no under-computation."

## 7. Required verifications (acceptance gates — primary source)

- **V1** — §1291(b) excess-distribution definition (125% of prior-3-year average;
  first-year treatment) + §1291(a)(1)(A) ratable per-day allocation.
- **V2** — §1291(c)(2) prior-year tax at the **highest** ordinary rate for that
  year, and §1291(c)(3) the §6621 interest-charge period (prior-year due date →
  current due date).
- **V3** — §1291(a)(2): a disposition's entire gain is an excess distribution.
- **V4** — historical highest ordinary rates by year (the pinned table) —
  authoritative source per year.
- **V5** — §1296(e) marketable-stock status of the specific delisted Russian
  funds (confirms MTM unavailable → §1291 path) — carried from the epic spec.

## 8. Out of scope (v1, caveated — not silently omitted)

- QEF (§1295) / MTM (§1296) computation and their elections; the §1291 "purging"
  / mark-to-market transition elections.
- **Mid-holding PFIC transition** — a fund that became a PFIC AFTER acquisition
  (`pfic_start_date` after `acquisition_date`). Its pre-PFIC period interacts with
  §1298(b)(1), the §1298(b)(2) start-up exception, and purging elections. v1
  computes only PFIC-FROM-ACQUISITION (the whole holding period is a PFIC period);
  a transition is blocked (`compute_pfic_1291` raises). The holding period runs
  the DAY AFTER acquisition through the event (§1223).
- §1291(f) nonrecognition-transfer rules; PFIC-in-a-CFC (§1297(d)) overlap.
- The blocked-income deferral election (Rev. Rul. 74-351) and §165(g)
  worthlessness on a frozen fund — stay the MANUAL `sanctioned_blocked_asset`
  surface (a frozen fund is not a disposition and not automatically worthless).
- State PFIC treatment beyond `/tax-compute`'s existing estimate.

## 9. Open questions

1. Distribution history: does the taxpayer have the prior-3-year distribution
   amounts per fund (needed for the 125% base)? If not, the first-year/no-base
   treatment applies or the case degrades to MANUAL.
2. Any actual disposition, or distributions-only? (Frozen funds → likely
   distributions-only or no event yet.)
3. Were the funds ever marketable/QEF-eligible in an earlier year (a prior timely
   election would change the regime)? Assumed no (§1291 default).
