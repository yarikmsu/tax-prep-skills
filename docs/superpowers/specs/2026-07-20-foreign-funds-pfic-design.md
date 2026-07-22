# Foreign funds (PFIC) + Russian sanctioned holdings — requirements

Status: **requirements / not yet scheduled.** Trigger: 2026-07-20 product-owner
review **open question #3 ("Does the persona hold foreign *funds*?") is now a
demand-driven requirement.** This spec scopes foreign-fund / PFIC support,
centred on **sanctioned-country (Russian) bonds and ETFs** because that
combination exercises every hard case at once (PFIC + §988 + FBAR/8938 + OFAC +
suspended treaty). Predecessor context: Epic K3 shipped the PFIC tripwire
**flag-only** (`has_foreign_mutual_funds` → surfaced in `/tax-setup`,
`/tax-review`, `/tax-fbar`; computes nothing).

This document specifies **what a correct return for such a filer requires**, decides
**compute-vs-flag per item under the never-silently-understate rule**, and lists
the **primary-source verifications that MUST run before any implementation**. It
is deliberately conservative: most of this surface is scoped MANUAL / capability
gate BLOCK, because the two things that would let us compute PFIC tax safely
(a QEF Annual Information Statement, or §1296 marketable-stock status) are
exactly what sanctions remove.

---

## 1. Context — why "foreign funds" is not one feature

"Russian bonds and ETFs" is **four distinct tax surfaces**, and conflating them
is the primary failure mode:

| Holding | Surface | Today |
|---|---|---|
| Russian-**domiciled** ETF / mutual fund (equity or bond fund) | **PFIC** (§1291–1298) → Form 8621 per fund per year | flag-only |
| Russian **bond** held directly (debt) | Ordinary interest + **OID** (§1271–5) + **§988** FX gain/loss if ruble-denominated | not modeled |
| Any of the above at a **foreign broker** | **FBAR** (FinCEN 114) + **Form 8938** (§6038D) | FBAR: yes; 8938: gap |
| All of the above under **OFAC sanctions** | blocked-asset income/disposition + §165(g) worthlessness + **suspended US–Russia treaty** + §901(j) FTC question | not modeled |

**Load-bearing distinctions the schema MUST encode (not infer):**

1. **A fund's PFIC status is the FUND's domicile, not its holdings.** A
   US-listed ETF that holds Russian assets (e.g. the former RSX) is **NOT** a
   PFIC; a Russia-domiciled fund (ISIN `RU…`, MOEX-listed) **is**. K3's flag
   already encodes "test is domicile" — the interview must capture domicile per
   fund, never assume from the word "Russian."
2. **A bond is debt, not equity → not a PFIC.** A bond *ETF* is a fund → PFIC.
   The interview must not let "Russian bonds and ETFs" collapse into one bucket.
3. **Broker domicile (not fund domicile) drives FBAR/8938.** Foreign broker →
   both apply. US broker → generally neither (a §6038D account at a US financial
   institution is excluded; FBAR is for foreign *accounts*).

---

## 2. Guiding bias direction (the whole epic turns on this)

The never-silently-understate rule cuts unusually hard here because the PFIC
default regime is **punitive**, so the understatement risk is treating PFIC
income as something cheaper:

- A PFIC "dividend" is **NOT** a qualified dividend — it is ordinary income, and
  an **excess distribution** (§1291) is taxed at the **highest** ordinary rate of
  the allocation years **plus a compounding §6621 interest charge**. Silently
  running a Russian fund distribution through the qualified-dividend 0/15/20%
  worksheet is the exact understatement this epic exists to prevent.
- Therefore the default for the punitive, election-dependent PFIC math is
  **capability-gate BLOCK** (named form, CLI exit 2, `acknowledged_limitations`
  override), **not** a computed number — until/unless a bounded §1291 engine is
  justified (item P4, gated on demand).
- Pieces that ARE safely computable in the taxpayer-unfavorable-or-exact
  direction (interest income, OID, §988 FX gain, FBAR/8938 presence) get
  computed or hard-surfaced.
- Taxpayer-favorable items that we cannot substantiate (a §165(g) worthlessness
  loss on a merely-frozen asset) are **withheld** (do not deduct) with a loud
  note — the safe direction is to NOT claim the loss, not to guess it in.

Per-item class is stated explicitly below (BLOCK / COMPUTE / SURFACE / WITHHOLD).

---

## 3. Scope decomposition

### P1 (P1, S) — Interview + schema deepening (prerequisite for everything)

The `has_foreign_mutual_funds` boolean is insufficient; per the
capability-registry invariant (**every rule flag exists in
`taxpayer_profile_template.json` AND is asked by the `/tax-setup` interview AND
keys a `capabilities.py` rule**, enforced by test) add a structured
`foreign_holdings` block collecting, per holding:

- `kind` ∈ {`fund`, `bond`, `other_security`} — forces the fund/bond split.
- `fund_domicile` (2-letter country) and `is_fund` → PFIC determination; a
  US-domiciled fund is explicitly NOT a PFIC.
- `custody` ∈ {`foreign_broker`, `us_broker`, `direct_registered`} → FBAR/8938.
- `currency` (ISO 4217) → §988 applicability (non-USD debt).
- `blocked_by_sanctions` (bool) + `blocking_event_date` → OFAC path.
- `pfic_election` ∈ {`none`, `qef`, `mtm`} + `election_first_year` → whether the
  punitive §1291 default applies (a prior timely election changes everything).
- `received_distribution` / `disposed` (bools) + `aggregate_pfic_value` → the
  §1298(f) / Reg. §1.1298-1(c)(2) reporting-threshold test (Form 8621 can be
  required **for reporting even with no distribution**).

Acceptance: the capability-registry test passes with the new flags; a legacy
profile without the block reads all-false and is unchanged.

### P2 (P1, S) — Capability-gate hardening (the safety net) — IMPLEMENTED

`capabilities.py` gains three rules. **Bias-taxonomy refinement made during
implementation:** only the case that would UNDERSTATE tax BLOCKs; a form omission
without a tax understatement is MANUAL (manifest note), consistent with how
Form 8938/FBAR are already treated. So the blanket "any foreign fund BLOCKs" from
the first draft was narrowed:

- `pfic_taxable_event` (flag `has_pfic_taxable_event`) — **UNSUPPORTED / BLOCK**.
  A foreign-domiciled fund with a DISTRIBUTION or DISPOSITION this year → the
  §1291 tax is punitive and uncomputed; routing it through the qualified-
  dividend/LTCG path understates → hard stop (Form 8621, acknowledgeable). A
  buy-and-hold fund with NO taxable event is NOT blocked — it carries only the
  reporting-only §1298(f) obligation (with the $25K/$50K-MFJ de-minimis) on the
  existing `has_foreign_mutual_funds` MANUAL note. This precision keeps a small
  buy-and-hold holder from being over-blocked.
- `foreign_asset_reporting` (flag `has_foreign_financial_account`) — **MANUAL**.
  Form 8938 §6038D reporting when foreign-custodied assets exceed the threshold;
  complements `/tax-fbar`. A form obligation, no tax understatement → manifest,
  not a block. US-broker assets generally excluded.
- `sanctioned_blocked_asset` (flag `has_sanctioned_blocked_asset`) — **MANUAL /
  WITHHOLD**. Blocked-income + §165(g) worthlessness are hand-analyzed; do NOT
  auto-claim a loss on a merely-frozen asset. The note also carries the verified
  FTC facts (a §901(j) country denies the FTC; a suspended treaty removes treaty
  relief — Russia NOT §901(j) as of 2026-07, US-Russia treaty suspended
  2024-08-16) so a future FTC path is not silently mis-set.

Each new rule flag exists in the template + interview (P1) — the registry
invariant (flag ∈ template ∧ asked ∧ keys a rule) is enforced by test.

### P3 (P1, S) — §988 FX gain/loss + foreign interest/OID (COMPUTE, bounded)

Directly-held Russian **bonds** are computable and safe:

- `compute_section_988(...)` — ordinary FX gain/loss on a non-functional-currency
  debt instrument: separate the **market** gain/loss (capital) from the
  **exchange** gain/loss (ordinary, §988(a)(1)), using acquisition-date and
  disposition/payment-date spot rates. Ordinary character; sources to the
  residence of the taxpayer (§988(a)(3)) — matters for FTC baskets.
- Foreign-source **interest** → 1040 Schedule B; **OID** (§1272) accrual if
  issued at discount. These are ordinary income, taxpayer-UNfavorable-or-exact
  → safe to compute.
- Degrade: if the ruble spot rates are unfetched → **loud degrade, never a
  guessed rate** (the `compute_underpayment_interest` precedent). §988 requires
  a rate on two dates; absent either → surface the FX component as MANUAL rather
  than compute a wrong ordinary amount.

Acceptance: hand-computed tests from a ruble-bond example (buy rate, sell rate,
coupon) with the market/exchange split matching the §988 regs; unfetched-rate
variant degrades loudly.

### P4 (P2/P3, L — DEMAND-GATED) — §1291 excess-distribution engine

Only if a real return needs the number AND the filer accepts the scope. A §1291
engine is large and, for **sanctioned** funds, may still land on "punitive,
no election available":

- §1291 excess-distribution: a distribution > 125% of the prior-3-year average is
  an excess distribution; allocate ratably across the holding period; current-year
  and pre-PFIC portions are ordinary current income; prior-year portions are taxed
  at the **highest** §1 rate for that year **plus** the §6621 interest charge
  (reuse the `underpayment_rates` cache + the daily-compounding machinery M3 built
  for `compute_underpayment_interest`).
- Disposition of a §1291 fund = an excess distribution of the whole gain.
- QEF (§1295) and MTM (§1296) are **out of v1** — and for sanctioned funds are
  likely **unavailable** (QEF needs a PFIC Annual Information Statement Russian
  funds won't issue; MTM needs §1296(e) "marketable stock" a delisted/sanctioned
  security likely fails — see verification V2). The engine surfaces this, it does
  not silently assume an election.

Bias: computing §1291 is safe-direction (it can only over- or exactly-state);
the risk is the OPPOSITE — never let a caller bypass it via the qualified-dividend
path (enforced by P2's BLOCK when P4 is absent).

### P5 (P2, S) — Sanctioned/blocked-asset treatment (SURFACE + WITHHOLD)

- **Blocked income:** distributions that accrue but are OFAC-blocked — surface
  the constructive-receipt / blocked-income question; do not silently assume
  received or not-received. (Rev. Rul. 74-351 blocked-income deferral is an
  election with conditions — MANUAL, caveated.)
- **Worthlessness (§165(g)):** a worthless SECURITY yields a capital loss (deemed
  sold on the last day of the year), BUT **frozen ≠ worthless** — §165 needs an
  identifiable event fixing zero value, not mere illiquidity/inability to trade.
  Class = **WITHHOLD**: do NOT auto-claim the loss; surface the high bar and the
  documentation required. Claiming a loss on a merely-blocked asset is the
  taxpayer-favorable error the rule forbids.
- **§988 loss** on ruble debt remains ordinary (P3), independent of worthlessness.

### P6 (P2, S) — Treaty-suspension + FTC awareness (SURFACE, verify-gated)

- The **US–Russia income tax treaty is suspended eff. 16 Aug 2024** (V1,
  Announcement 2024-26) → treaty-reduced withholding and treaty re-sourcing no
  longer apply for affected periods; the return uses statutory treatment. Surface
  in `/tax-review` and any FTC path.
- **FTC is available under general §901 (V3-verified):** Russia is **NOT** a
  §901(j) country as of 2026-07 (the adding bill was never enacted; Russia is not
  a State-Sponsor-of-Terrorism), so taxes actually paid to Russia remain
  creditable under the normal §901 limitation — just without the treaty relief
  above. **Do NOT block the FTC** on a §901(j) theory.
- **§901(j) tripwire (binary, re-pin annually):** §901(j) WOULD deny the FTC
  entirely if Russia were ever added — and it has been repeatedly proposed. Model
  §901(j) status as a **fetched, annually re-pinned flag** defaulting to the
  verified "not listed / allowed" state; re-run V3 each season. If a future year's
  pin says "listed", the FTC flips to denied — silently allowing it then is a
  direct understatement.

---

## 4. Required primary-source verifications (MUST run before implementation)

Per the project rule (web-verify contested/fast-moving tax law; never assert from
memory). Each is an **acceptance gate**, not a background nicety:

- **V1 — US–Russia treaty status + effective dates** for the specific tax year:
  which articles are suspended, from what date (Russia's Aug-2023 decree vs the
  US mutual-suspension effective date). Pin the authoritative Treasury/IRS
  announcement URL per K2 source-pinning.
- **V2 — §1296(e) "marketable stock" status of delisted/sanctioned Russian
  securities** — the determinant of whether MTM is even available (drives the
  §1291-is-unavoidable conclusion). Cite the reg + exchange-qualification basis.
- **V3 — §901(j) country list for the tax year** — is Russia listed? Binary
  FTC on/off. Pin the source.
- **V4 — current OFAC prohibitions** (E.O. 14024 et seq.) relevant to
  disposition/abandonment — because "can't relinquish under sanctions" affects
  the abandonment-loss and blocked-income analysis.
- **V5 — Form 8621 §1298(f) reporting thresholds** (Reg. §1.1298-1(c)(2)
  $25k/$50k-MFJ de-minimis) and current-year filing triggers.

The requirements are written to the best current understanding; **any figure or
status above is provisional until its V-item is verified and pinned.**

### Verification results (2026-07-21 — primary-source, re-pin annually)

- **V1 — RESOLVED.** The US–Russia income tax treaty is **suspended effective
  16 Aug 2024** (IRS **Announcement 2024-26**; US notice to Russia 17 Jun 2024,
  by mutual agreement following Russia's Aug-2023 Decree No. 585). Suspended:
  Art. 1(4), **Arts. 5–21** (business profits, dividends, interest, royalties,
  gains, employment, etc.), Art. 23 (non-discrimination), and the Protocol —
  indefinitely. ⇒ No treaty-reduced withholding, no treaty re-sourcing for
  affected periods. Pin: `irs.gov/pub/irs-drop/a-24-26.pdf`.
- **V3 — RESOLVED (corrected — the load-bearing one).** **Russia is NOT a
  §901(j) country.** The bill that would add Russia + Belarus ("Support Ukraine
  Through Our Tax Code Act", S.4218/117th) was **introduced but never enacted**
  (died with the 117th Congress; no successor enacted). Russia is also **not** a
  State-Sponsor-of-Terrorism (list = Cuba, Iran, DPRK, Syria), so it fails the
  §901(j)(2) statutory criteria too. ⇒ **The FTC is NOT categorically denied**;
  taxes actually paid to Russia remain creditable under general §901 (subject to
  the normal limitation) — but WITHOUT treaty relief (V1). **Caution recorded in
  the process log: several 2024-dated secondary sources assert Russia "was added
  to §901(j)" — they conflate the proposal with enacted law; primary Congress.gov
  status refuted this.** **Bias/design:** §901(j) status is a *fetched, annually
  re-pinned flag* defaulting to "not listed / FTC allowed" (the verified state),
  and the spec must re-run V3 each season — the provision has been repeatedly
  proposed, and if ever enacted the flag flips to "denied" (understatement risk
  if missed).
- **V5 — RESOLVED.** Per **Instructions for Form 8621 (12/2025)** + §1298(f):
  a US PFIC shareholder must file **annually even with no distribution/disposition**.
  De-minimis exception (Reg. §1.1298-1(c)(2)): if aggregate PFIC value ≤ **$25k
  ($50k MFJ)** on the last day AND no excess distribution/disposition, Part I can
  be skipped and all PFICs reported on a single form.
- **V2 — PARTIAL (reasoned, not directly cited).** §1296 MTM requires
  **"marketable stock" = regularly traded** (≥15 days/quarter) on a **qualified
  exchange** (SEC-registered national exchange or a Treasury-designated market).
  No source directly addressed sanctioned/delisted Russian securities, but a
  security that has stopped trading on a qualified exchange **fails "regularly
  traded" ⇒ MTM unavailable ⇒ §1291 default** — confirm per-security at build
  time (trading history for the specific ISIN in the tax year).
- **V4 — OPEN (out of tax scope, flag to counsel).** Russian holdings sit under
  OFAC's Russian Harmful Foreign Activities program (E.O. 14024 et seq.); new
  investment / disposition / relinquishment may be restricted. Detailed OFAC
  compliance is the taxpayer's/counsel's, not the toolkit's — surface, don't
  adjudicate.

---

## 5. Capability-registry + tax-law schema requirements

- Every new profile flag (P1) → template + `/tax-setup` interview + a
  `capabilities.py` rule (P2). Enforced by the existing registry test.
- No hardcoded tax law: ruble spot rates ride `federal.exchange_rates` (already
  in the schema/`_fetched_federal_blocks`); §901(j) status and treaty status ride
  new pinned blocks, NOT literals in a lib module (the NJ-BAIT / N4 lesson —
  state/foreign law drifts). §1291 uses the existing `underpayment_rates` cache.
- All new numeric inputs go through the strict validators
  (`_strict_non_negative` / finite-only), and country/currency codes are
  validated at the parse boundary (the `address.state` 2-letter precedent).

## 6. Test requirements

- P1/P2: registry-invariant test green; legacy-profile-unchanged test.
- P3: hand-computed §988 market/exchange split + OID accrual; unfetched-rate loud
  degrade.
- P4 (if built): §1291 allocation + §6621 interest-charge tests reusing the M3
  daily-compounding fixtures; disposition-as-excess-distribution.
- P5: worthlessness WITHHELD (no auto-loss) test; blocked-income surfaced-not-assumed.
- P6: §901(j)-listed variant BLOCKS the FTC; treaty-suspended variant drops treaty rate.
- Update the CLAUDE.md test count; new fixtures under `tests/fixtures/`.

## 7. Out of scope (v1, caveated — not silently omitted)

- QEF (§1295) and MTM (§1296) elections and their ongoing mechanics (P4 surfaces
  their unavailability for sanctioned funds; does not model them).
- Form 8938 full valuation/multi-asset computation beyond the presence/threshold
  BLOCK (P2) — the trigger is surfaced; the schedule is manual/professional v1.
- Mark-to-market for non-sanctioned foreign PFICs generally.
- Estate/gift and PFIC-in-a-CFC overlap (§1297(d)).
- Any state-level foreign-fund conformity beyond `/tax-compute`'s existing
  bracket estimate.

## 8. Sequencing

1. **Now (blocks the return from being silently wrong):** P1 + P2 —
   deepen the interview/schema and turn the capability gate into a hard stop for
   PFIC / blocked assets / 8938. This alone makes the current return SAFE (it
   BLOCKS with named forms instead of computing a wrong cheap number).
2. **Next (safe computed value):** P3 (§988 + interest/OID) for the directly-held
   bonds — real, bounded, taxpayer-exact.
3. **Verify-gated:** P6 + the V-items, in parallel.
4. **Demand-gated:** P4 (§1291 engine) only if an actual 8621 number is
   needed and the scope is accepted.

## 9. Open questions (carry into scheduling)

1. Are the ETFs Russian-**domiciled** (PFIC) or US-listed-holding-Russian (not)?
   — decides whether P4 is ever in play.
2. **Custody:** foreign/Russian broker (FBAR + 8938 + OFAC) or US broker?
3. Were any **QEF/MTM elections** made in the first year of holding? — decides
   whether §1291 applies at all.
4. Are the assets currently **blocked/frozen**, and is any distribution accruing?
   — drives P5.
5. Is FTC being claimed on Russian withholding (→ V3 §901(j) is then P1-urgent)?
