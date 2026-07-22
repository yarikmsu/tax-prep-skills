# Foreign funds (PFIC) + Russian holdings — implementation plan (near-term slice)

**Spec:** `docs/superpowers/specs/2026-07-20-foreign-funds-pfic-design.md`
**Backlog:** 2026-07-20 review, open question #3 (demand-driven). This plan covers
**only the near-term slice: P1 + P2 + P3.** P4 (§1291 engine) is demand-gated;
P5/P6 are surfaced-but-thin and ride along in the P2 PR where trivial.

**Two PRs, in order.** PR-A (P1+P2) makes any such return **fail closed with the
named forms** — it is the safety net and ships first even if P3 slips. PR-B (P3)
adds the one safely-computable number (§988 + interest/OID on directly-held
bonds). Verification results V1/V3/V5 (spec §4) are already pinned; V2 is a
build-time per-ISIN input; V4 is counsel-scope.

---

## PR-A — Interview/schema (P1) + capability-gate BLOCK (P2)

Ships the safety net: the return can no longer silently run a foreign-fund
distribution through the qualified-dividend worksheet. **No computation** — only
intake + fail-closed gating. This is the whole immediate win.

### Build order (TDD per unit)

1. **Profile template — `foreign_holdings` block**
   (`agents/templates/taxpayer_profile_template.json`). An array; per holding:
   `kind` ∈ {`fund`,`bond`,`other_security`}, `fund_domicile` (2-letter),
   `is_fund` (bool), `custody` ∈ {`foreign_broker`,`us_broker`,`direct_registered`},
   `currency` (ISO-4217), `blocked_by_sanctions` (bool), `blocking_event_date`,
   `pfic_election` ∈ {`none`,`qef`,`mtm`}, `election_first_year`,
   `received_distribution` (bool), `disposed` (bool), `aggregate_pfic_value`.
   Keep K3's `has_foreign_mutual_funds` (derive it from the block for backward
   compat so existing callers/tests are unchanged). Test: a legacy profile with
   no block reads all-false / empty and is unchanged.

2. **`/tax-setup` interview** (`agents/skills/tax-setup.md`). Add the
   foreign-holdings questions — per the **capability-registry invariant every new
   flag that keys a P2 rule MUST be asked here** (enforced by the existing test).
   The fund-vs-bond and domicile questions carry the two load-bearing distinctions
   from spec §1 (domicile not holdings; debt not equity) as interviewer notes.

3. **Value validation at the parse boundary.** `fund_domicile` / `currency`
   validated against code sets (the `address.state` 2-letter precedent);
   `aggregate_pfic_value` through the strict finite/non-negative validator;
   `pfic_election` / `custody` / `kind` ∈ their canonical sets (reject
   type-valid-but-out-of-domain). Test: bad code / NaN / unknown enum rejected.

4. **Capability rules** (`agents/lib/capabilities.py`) — all **BLOCK class**
   (CLI exit 2, named form, `acknowledged_limitations`-overridable), each keyed on
   a P1 flag:
   - `pfic_8621` — any holding `is_fund` AND foreign `fund_domicile` → **Form 8621
     required; PFIC computation out of scope v1.** The primary guard.
   - `form_8938_foreign_asset` — any `foreign_broker`/`direct_registered` custody
     over the status-based §6038D threshold → **Form 8938 required.**
   - `sanctioned_blocked_asset` — any `blocked_by_sanctions` → **manual review of
     blocked-income + §165(g) worthlessness** (P5 surface; do not auto-claim a loss).
   Tests: each rule fires on its trigger and BLOCKs; a US-domiciled fund does NOT
   trip `pfic_8621` (the domicile distinction); a US-broker holding does NOT trip
   8938; `acknowledged_limitations` unblocks; an unknown ack id still hard-errors.

5. **Registry-invariant test green** (flag ∈ template ∧ asked by interview ∧ keys a
   rule) + the `meta.limitations` manifest (C2) carries the three new BLOCK lines
   → printed on PDF page 1 by `/tax-generate` (existing mechanism, no new code).

6. **Docs**: CLAUDE.md capability-gate line (add the three rules), spec status →
   "P1+P2 implemented", test-count bump.

### Reuse map (PR-A)

| Need | Reuse from |
|---|---|
| capability rule shape + exit-2 + ack | existing `capabilities.py` rules (e.g. `rental_schedule_e`) |
| `meta.limitations` manifest → PDF page 1 | C2 mechanism (unchanged) |
| strict numeric / enum-at-boundary validators | `tax_computations._coerce_numeric`, the `filing_status`/`address.state` value-validation precedent |
| K3 flag continuity | `has_foreign_mutual_funds` derived from the new block |

---

## PR-B — §988 FX + foreign interest/OID compute (P3)

The one safely-computable, taxpayer-exact piece — for the **directly-held bonds**
(NOT the funds, which stay BLOCKed by PR-A). Ordinary character; the bias risk is
UNDER-stating ordinary income, so this is safe to compute (and loud-degrades
rather than guessing a rate).

### Build order (TDD per unit)

1. **`compute_section_988(...)`** in `agents/lib/tax_computations.py` — rates
   parameterized (no hardcoded FX). Given acquisition amount + date-spot-rate and
   disposition/principal amount + date-spot-rate, return the **market** gain/loss
   (capital) and the **exchange** gain/loss (**ordinary**, §988(a)(1)) *separately*
   — do not net them into one figure. Round to 2 dp. Self-test in `_main`.
   Tests: hand-computed ruble-bond example (buy rate, sell rate) with the
   market/exchange split matching the §988 regs; a gain-on-market + loss-on-FX case
   (opposite signs) to prove they don't cancel.

2. **Foreign interest + OID.** Interest → 1040 Schedule B (ordinary, foreign-source).
   `compute_oid_accrual(...)` for a bond bought at discount (§1272 constant-yield),
   rates-parameterized. Tests: coupon interest to Schedule B; OID accrual for a
   discount bond; a par bond → $0 OID.

3. **Degrade, never guess.** §988 needs a spot rate on TWO dates; if either is
   unfetched (`federal.exchange_rates` absent for the date) → **surface the FX
   component as MANUAL** with a loud note, do NOT compute a wrong ordinary amount
   (the `compute_underpayment_interest` refuse-on-gap precedent). Test: missing
   rate → MANUAL flag, not a silent number.

4. **`/tax-compute` wiring** (`agents/skills/tax-compute.md`): collect the bond
   facts (currency, dates, amounts, coupon/OID) from parsed docs; route §988
   ordinary to Schedule 1 / the interest to Schedule B; the market capital
   gain/loss to Schedule D. Funds remain BLOCKed (PR-A) — P3 touches bonds only.

5. **Result template**: add a `section_988` sub-block (market vs exchange split,
   source) to `result_template.json` (conditional, removed when inapplicable).

6. **Docs + verify**: CLAUDE.md (lib list: `compute_section_988` / `compute_oid_accrual`;
   gotcha line — "ruble debt: market gain is capital, FX gain is ordinary §988, do
   not net"); test-count bump; full pytest green + lib self-test.

### Reuse map (PR-B)

| Need | Reuse from |
|---|---|
| rates-parameterized compute + 2-dp round + `_main` self-test | every `tax_computations.py` function |
| FX rate cache | `federal.exchange_rates` (already in `_fetched_federal_blocks`) |
| loud-degrade-on-missing-input | `compute_underpayment_interest` refuse-on-gap pattern |
| capital vs ordinary routing | existing Schedule D / Schedule 1 wiring in `/tax-compute` |

---

## Test plan (both PRs)

- Registry invariant + legacy-profile-unchanged (PR-A).
- Each capability rule: fires / BLOCKs / ack-unblocks; the two negative cases
  (US-domiciled fund, US-broker custody) that must NOT trip (PR-A).
- §988 market/exchange split hand-computed; opposite-sign non-cancellation;
  missing-rate loud degrade; OID accrual; par-bond $0 (PR-B).
- New fixtures under `tests/fixtures/`; count updated in CLAUDE.md.

## Non-goals (v1 — caveated in output, see spec §7, NOT silently omitted)

- **PFIC tax computation** — §1291 excess-distribution / QEF / MTM (spec P4,
  demand-gated). PR-A BLOCKs; it does not compute.
- **Form 8938 valuation** beyond the presence/threshold BLOCK.
- **§165(g) worthlessness loss** — WITHHELD (surfaced, never auto-claimed).
- **OFAC adjudication** (V4) — surfaced to counsel.
- Per-ISIN §1296 marketability determination (V2) — a build-time input to P4, not P3.

## Open inputs (do not block PR-A/PR-B)

- Fund **domicile** + **custody** for the live case — decide whether P4 is ever
  scheduled; both PRs behave correctly for either answer.
- Per-ISIN trading history (V2) — needed only when/if P4 is built.
