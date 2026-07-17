# Epic J — Equity-Comp Basis Correction (Form 8949 code B) — Design/Requirements

Status: requirements approved (product owner, 2026-07-17). Source: finding NEW-1
in `docs/roadmap/2026-07-17-product-owner-review.md`. Priority **P1** — the
persona is "CA, W-2 + equity comp (RSU/ISO/ESPP)" and this is that persona's
single most common real-dollar error.

Self-contained: an implementing session needs this document, the referenced
files, and CLAUDE.md — no chat context.

## The problem

Brokers report RSU/ESPP/NQSO share basis on the 1099-B **excluding the
compensation already included in W-2 box 1** — for RSUs, typically $0 basis.
The interview collects the facts (`tax-setup.md:187` asks about RSU vesting,
ISO exercises, ESPP purchases; `projection_inputs` carries `rsu_vesting`) but
nothing downstream acts on them: `/tax-parse` never asks for the broker's
supplemental statement, `/tax-compute` copies the 1099-B basis into
`schedule_d` verbatim, and `/tax-review` has no employer-stock check. An
unadjusted return **double-taxes** the W-2-included compensation — routinely a
four-to-five-figure overstatement (direction: overstates tax — "safe" for
compliance, catastrophic for trust).

The fix is the standard Form 8949 **column (f) code "B" / column (g)
adjustment**: report the 1099-B basis as-is, put the correction in column (g)
(negative = reduces gain), keyed to documentation.

**Mechanism scope:** the adjustment flow is generic "employer-stock
compensation basis adjustment" — it covers RSU vests, ESPP purchases, NQSO
exercises, and same-day ISO disqualifying dispositions alike (all share the
comp-in-W-2 + broker-omits-it shape). ESPP additionally needs the §423
ordinary-income split (J2). ISO *deferred* AMT basis (dual-basis tracking for
shares held through year-end) is NOT this epic — Form 6251/8801 already handle
the exercise year; dual-basis on later sale is listed under Out of scope.

---

## J1 — Parse → compute → tie-out → review flow (P1, M)

### Interview / profile

1. Audit whether the equity-comp answers persist as profile flags. Requirement:
   `flags.has_rsu_vesting`, `flags.has_espp` (names per existing conventions;
   reuse if flags already exist under other names — do NOT duplicate). Every
   new flag must be in `taxpayer_profile_template.json` AND asked by the
   interview (registry-invariant test enforces).
2. New derived expectation: equity-comp flag set AND a 1099-B present from any
   broker → the supplemental statement becomes a REQUIRED intake document
   (step 2.5-style blocking question, like `missing_expected_payers`).

### Parse (`/tax-parse` + `parse_utils.py`)

3. Per-broker prompt: "This 1099-B is from a broker that may hold employer
   stock. Provide the broker's *supplemental information* statement (the
   'adjusted cost basis' pages) or the equity-plan statements (vest
   confirmations / Form 3922 / Form 3921)." Classification support for common
   supplemental-statement shapes is best-effort; the load-bearing output is the
   ledger record.
4. Ledger: records for these documents carry a new key-box category
   `employer_stock_basis_adjustment` — the SUM of positive basis corrections
   (adjusted basis − 1099-B-reported basis) for lots sold this year. Per-lot
   detail goes in the summary markdown; the ledger carries the auditable total.
   Producer/consumer pairing rule (E5): register the category in
   `tie_out.CATEGORY_TARGETS` AND add the tax-parse.md table row — the
   doc-sync test enforces both halves.
5. Second-read (A3): adjustment totals are high-value by nature; records with
   the category ≥ $10K get the existing second-read pass (no new machinery —
   confirm the category participates).

### Compute (`/tax-compute` + `result.json`)

6. `result_template.json schedule_d` short-term/long-term detail rows gain an
   `adjustment` field (signed; negative reduces gain) and the computed gain
   becomes `proceeds − cost + adjustment`. A `_note_8949_code_b` documents the
   column (f)/(g) semantics and that basis stays as-reported (column (e) is
   the broker figure — matching software transfer and `/tax-verify`).
7. tax-compute.md Schedule D step: apply adjustments ONLY from documented
   sources (supplemental statement, 3922/3921, vest confirmations with
   per-share comp). **Never infer an adjustment** — an invented adjustment
   understates tax (the unsafe direction), so the gate is: no source document →
   no adjustment → loud `possible_double_taxation` warning instead (overstates
   tax but is visible, and the review check below escalates it). Each applied
   adjustment recorded in answers.md (source doc, per-lot math, no aggregate
   invention).
8. Character/term unchanged by the adjustment (an RSU lot's ST/LT term still
   runs from vest). Box classification unchanged (adjustment does not move a
   lot between 8949 boxes; foreign brokers stay Box B/E).
9. Sanity bound: total adjustment for a grant type must not exceed the
   W-2-included equity compensation the user attests (ask once; if unknown,
   warn rather than block). Adjustment > proceeds − 0 basis floor per lot →
   hard error (arithmetically impossible).

### Tie-out (`tie_out.py`)

10. New category target wiring so the hard gate covers the new figure:
    documents-without-records / records-without-files semantics unchanged; the
    category sum ties to the result-side sum of `schedule_d` row `adjustment`
    fields (build a `_adjustments_total` derivation mirroring
    `_proceeds_total`). A supplemental statement that parsed but whose
    adjustment never landed in `result.json` must FAIL the gate — that is the
    double-tax scenario this epic exists to kill.

### Review (`/tax-review`)

11. New DIF-style check (rides H3's step-2 pattern): any 1099-B row in an
    employer-stock context (equity flags set) with basis $0, or basis < 20% of
    proceeds, and NO adjustment applied → banner: "presumptive missing code-B
    basis adjustment — broker basis for RSU/ESPP shares excludes W-2
    compensation; expected overstatement of gain." The 20% heuristic is a
    review flag, never an automatic adjustment.
12. ESPP/RSU wash-sale interplay caveat (documented, not modeled): selling at
    a loss within 30 days of a vest/purchase of the same ticker is a §1091
    wash sale (`/tax-harvest` already models the mechanics for `holdings.json`;
    the return-side caveat just points there).

### ESPP disqualifying-disposition W-2 check

13. For ESPP sales identified as disqualifying dispositions (J2 math), the
    ordinary-income component belongs in W-2 box 1. Verification step: if the
    employer W-2 does NOT include it (common for former employees), the amount
    goes to 1040 line 1h (other earned income) with an answers.md record.
    Direction note: omitting it understates tax → the check is loud and the
    line-1h routing is the default remedy; double-adding when the W-2 already
    includes it overstates → the check asks, never assumes.

---

## J2 — Form 3922 classification + §423 ESPP split (P2, S)

Rides the Form 3921 pattern shipped in PR #39 (`parse_utils.classify_document`
top-of-dict ordering; per-FILE ledger semantics; skips-with-reason).

1. **Classification:** Form 3922 ("Transfer of Stock Acquired Through an
   Employee Stock Purchase Plan Under Section 423(c)") pattern placed at the
   TOP of the classification dict alongside 3921 — the generic 1099-B "COST
   BASIS" keyword shadows broker-issued 3922 packets exactly as it did 3921.
2. **Ledger:** category `espp_purchase_info`, informational class (like
   `ss_tips` / the 3921 held-shares convention): box 3 (grant-date FMV), box 4
   (purchase-date FMV), box 5 (price paid), box 8 (shares). A 3922 for a year
   with no sale of those shares is a documented informational record, not a
   skip-error — it feeds FUTURE basis math; note carried forward via
   `/tax-rollover` is out of scope v1 (caveat: keep the 3922s in `documents/`).
3. **§423 split** (new pure helper, e.g. `compute_espp_disposition` in
   `tax_computations.py`), per sold lot:
   - Holding test: disposition is QUALIFYING iff > 2 years after grant AND
     > 1 year after purchase (strictly *more than*, per the `/tax-harvest`
     `derive_holding_period` precedent — exact-boundary days fail).
   - DISQUALIFYING: ordinary income = FMV at purchase (box 4) − price paid
     (box 5), regardless of sale price; capital basis = price paid + ordinary
     income; capital gain/loss from that basis, term from purchase date.
   - QUALIFYING: ordinary income = LESSER of (grant-date discount = box 3 ×
     plan discount %, computed as box 3 − price-paid-derived-from-grant per
     the plan's lookback — v1 uses box 3 − box 5 when the plan facts are
     unavailable, caveated) and (actual gain); remainder LTCG; ordinary
     income can be $0 on a loss sale (never negative).
   - Returns the ordinary component (→ line 1h check, J1 item 13) and the
     corrected basis (→ the code-B adjustment when the broker reported
     price-paid-only basis).
   - Strict validation house style (bool/NaN/negative rejection; dates
     validated; sale before purchase hard-errors).

---

## Acceptance criteria / tests

Hand-computed fixtures (IRS Pub 525 / 8949 instructions cited in comments):

- **Headline fixture:** RSU lot — broker basis $0, proceeds $60K, W-2-included
  comp $50K, supplemental statement present → adjustment −$50K, gain $10K;
  without the statement → no adjustment, gain $60K, `possible_double_taxation`
  warning present, review banner fires.
- Tie-out: fixture where the supplemental statement is ledgered but the
  result-side adjustments total is 0 → hard gate FAILS (exit 2).
- Adjustment bound: adjustment exceeding attested W-2 equity comp → warning;
  per-lot arithmetic impossibility → hard error.
- Multi-lot proration: two lots, one adjusted, sums tie.
- DIF heuristic: basis exactly 20% of proceeds boundary; non-employer ticker
  with $0 basis (flags unset) does NOT fire.
- 3922 classification: broker packet with "COST BASIS" text classifies as
  3922, not 1099-B (mirror of the 3921 test).
- §423 split: disqualifying (gain and loss sale variants — ordinary income
  fixed at box4−box5 even on the loss), qualifying with lesser-of both ways,
  exact 2-year/1-year boundary days = disqualifying, negative-ordinary
  impossible, sale-before-purchase error.
- Line 1h routing: DD ordinary income absent from W-2 → line 1h populated +
  answers.md instruction; present in W-2 → not double-added.
- Registry invariants for any new flags; tax-parse.md table rows for both new
  ledger categories (doc-sync test).
- CLAUDE.md test-count line updated.

## PR shape

One PR is acceptable (J1+J2 interlock via the ESPP basis path); if split, J1
first with the ESPP split stubbed to disqualifying-only. Two-reviewer panel
lenses: "equity-comp tax accuracy (Pub 525/§423/8949)" and
"ledger/tie-out integration". CI green, squash-merge, Yarik-authored.

## Out of scope (caveat, don't build)

- ISO dual-basis (AMT vs regular) tracking on post-exercise-year sales —
  documented caveat pointing at Form 8801/6251 records; candidate for a later
  epic.
- Rollover of unsold-lot basis facts (3922/vest records) into
  `prior_year` — v1 keeps source docs in `documents/`; note only.
- Broker supplemental-statement auto-parsing guarantees — best-effort
  classification; the human-confirmed ledger record is the contract.
- §83(b) elections; non-US plans; transfer-between-brokers basis chains.
- QSBS interaction (see Epic I sweep item 8).
