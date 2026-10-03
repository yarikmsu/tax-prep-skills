# tax-compute Step 10 — state return

California Form 540 in depth; New York has its own file; every other state is
a bracket estimate. A `type: "none"` state is a positive declaration, not an
empty section.

**A TERRITORY IS NOT A STATE.** When `address.state` is PR, VI, GU, AS or MP,
the return reached here only by acknowledging `territory_resident`: compute
NO state return and no estimate. The territory's own return is out of scope.

**NEW YORK IS NOT HERE.** When `tax_law.state.code == "NY"`, run the
cache-vs-taxpayer cross-check below and then read
[state-return-ny.md](state-return-ny.md) instead of any branch in this file.
New York's `state.brackets` is keyed by §601 rate SCHEDULE rather than being a
flat list, so the generic path below does not even read it correctly — and
plain bracket math there UNDER-states the tax by the whole benefit of the
graduated schedule above the §601(d-x) recapture threshold.

**NORTH CAROLINA:** after the same cache-vs-taxpayer cross-check, when
`tax_law.state.code == "NC"`, read [state-return-nc.md](state-return-nc.md).
Its D-400 branch replaces the generic bracket estimate below.

**WASHINGTON:** after that cross-check, `state.code == "WA"` routes to
[state-return-wa.md](state-return-wa.md). Use its `capital_gains_excise`
regime and LONG-TERM base; never an ordinary-income bracket estimate.

## Contents

- [Step 10: State Return](#step-10-state-return)

---

### Step 10: State Return

**Cache-vs-taxpayer cross-check — BEFORE any state branching, for ALL
regimes (round-4 finding E):** `tax_law.state.code` MUST equal the
taxpayer's validated `taxpayer_profile.json address.state`. The round-3/
round-4 freshness guards in `tax_law_fetcher.py` only validate that a cache
is internally consistent (`state.type` matches `state.code`) — they never
compare the cache against the TAXPAYER, so a stale-but-valid cache left over
from a different state (e.g. a leftover FL `type: "none"` cache sitting
beside a CA taxpayer's workspace after an address correction) would
otherwise pass every existing check and silently write the FL no-income-tax
statement while skipping Form 540 entirely — an affirmative "you owe
nothing" statement for the wrong reason. If `tax_law.state.code` !=
`taxpayer_profile.address.state`, HARD STOP: "state cache is for
{cache_code} but the taxpayer resides in {profile_state} — re-run
/tax-setup to fetch the correct state law; do NOT compute any state
branch." Do not compute Form 540, the FL no-income-tax statement, the
generic-state path, or any other state branch until this check passes.

Read taxpayer's state from `taxpayer_profile.json`. Compute state return:

**California (Form 540):**
1. Start with Federal AGI
2. Schedule CA adjustments:
   - HSA add-backs (employer contributions + HSA earnings) — CA non-conformity
   - Any other CA-specific additions/subtractions
3. CA AGI = Federal AGI + additions - subtractions
4. CA deduction — run `compute_ca_itemized()` whenever `compute_schedule_a`
   ran (SOURCE figures, not the federal total — CA has no SALT cap but
   disallows income/sales taxes, keeps the $1M mortgage limit, caps cash
   gifts at 50% of AGI, still allows misc 2%-floor expenses, and phases out
   itemized deductions above the R&TC §17077 FEDERAL-AGI threshold from
   `state.itemized_phaseout` — keyed `single`/`mfj`/`hoh`: map MFS→single
   (FTB publishes Single/MFS as one tier) and QSS→mfj; unfetched REFUSES
   when the return has deductions subject to the phase-out, so fetch it). All AGI-anchored floors use
   FEDERAL AGI (the function handles this — pass both AGIs). Pass
   `mfs_spouse_itemizes` AND `married_filing_separately` for MFS/RDP-
   separate — BOTH, or the call is refused, and `mfs_spouse_itemizes` must
   be the ANSWER rather than omitted: unknown is not "no". FTB states the
   rule as separate spouses "either both itemize your deductions (even if
   the itemized deductions of one spouse/RDP are less than the standard
   deduction) or both take the standard deduction", so the spouse's choice
   decides this return in BOTH directions and the itemized-vs-standard
   comparison never runs. This is not a CA-only rule — R&TC §17073.5
   incorporates §63(c)(6), which zeroes the standard deduction of BOTH
   spouses once either itemizes; FTB simply states the consequence as an
   instruction to the couple. **Exception:** a spouse who is CONSIDERED
   UNMARRIED and files as head of household is not an MFS filer, so the
   coupling does not bind and this filer may itemize — pass
   `mfs_spouse_is_a_separate_filer`, which the engine REFUSES to default
   wherever the two answers differ. `mfs_spouse_itemizes` here means the
   spouse's CALIFORNIA election, which may differ from their federal one. (The CA mortgage limit also halves to $500K.) **`ca_standard_deduction` is REQUIRED and
   keyword-only** — it used to be optional, and omitting it returned
   `use_itemized: None` with no `election_margin`, which reads as "do not
   itemize" to a caller testing it and under-states the deduction for any
   taxpayer whose CA standard deduction is larger. Pass it from the tax_law
   JSON CA section; if it is not there, STOP and re-run `/tax-setup`. The
   function makes the election: **the CA election is independent of the
   federal one** — with CA's small standard deduction, itemizing CA-only
   is the norm for the persona. Write the `schedule_ca_itemized` section
   when itemizing.
5. CA taxable income = CA AGI - CA deduction. **Line 18 is
   `use_itemized` — NEVER `max(itemized_total, ca_standard_deduction)`.**
   `compute_ca_itemized` returns no `deduction` key, so the caller picks, and
   "larger of" is wrong in both directions for a separate filer: it takes the
   itemized total where the coupling bars it (over-stating), and the standard
   deduction where the coupling forces itemizing (the election FTB forbids).
   Use `itemized_total if use_itemized else ca_standard_deduction`.
6. CA tax from FTB tables (use brackets from tax_law JSON)
7. CA exemption credits (personal + dependent, amounts from tax_law JSON)
8. CA tax after credits — **floored at zero**. The exemption credit is
   NON-refundable, so `max(0, tax_from_table - credits)`: a return with no CA
   tax still earns 2 x $150 for two filers, and subtracting it without the
   floor produces a negative tax that becomes an invented refund downstream.
   Over-stating a refund is the direction AGENTS.md §4 calls a hard error.
9. Mental health surcharge (1% on income > $1M)
9.5. **CA Schedule P (540) AMT** — run `compute_ca_schedule_p()` whenever
    `has_iso_exercise` (the same trigger as federal Form 6251). The CA analogue
    of Form 6251: pass `ca_taxable_income` (Form 540 line 19), `ca_regular_tax`
    (the CA tax before AMT — step 8's tax after credits), the CA
    `filing_status`, and `exemption` / `phaseout_start` from **`state.amt`**
    keyed by status (map MFS→`mfs`, QSS→`mfj`) — unfetched HARD-ERRORS (never a
    guessed exemption). Pass `iso_bargain_element` (same CA-conforming spread as
    federal — CA conforms to the ISO adjustment) and any other CA Schedule P
    preference/adjustment items via `ca_preference_adjustments`. The 7% TMT rate
    and 25% phase-out are statutory (in the function). **`ca_amt` is ADDED to
    the CA tax** (write the `schedule_p` block: ca_amti / ca_exemption_allowed /
    ca_tmt / ca_amt / iso_bargain_element / carryforward_to_next_year). The
    `schedule_p_carryforward` is the deferral-attributable (ISO) CA AMT credit —
    write it to `california_540.schedule_p.carryforward_to_next_year` so
    /tax-rollover carries it to `prior_year.ca_amt_credit_carryforward` (a
    SEPARATE carryforward from the federal Form 8801). A non-ISO CA return skips
    this step (no `schedule_p` block).
10. CA withholding from W-2 Box 17
    (`ca_total_tax` = CA tax after credits + mental-health surcharge + `ca_amt`
    from step 9.5 when present.)
11. Excess SDI/VPDI credit (Form 540 line 74): run `compute_excess_sdi()`
    for EVERY person with any CA-wage W-2 — no trigger condition (cheap pure
    function, zeros when nothing is wrong; conditional triggers miss
    below-cap wage-relative employer errors, and TY2024+ has no cap to
    exceed at all yet still surfaces recover-from-employer amounts). One
    call PER PERSON with the per-employer
    W-2 Box 14 SDI amounts, **the matching per-employer CA-wages-subject-to-SDI
    list** (same wage-eligibility rule as the federal credit),
    `state.sdi_rate` / `state.sdi_wage_limit` from
    tax_law JSON, **and the tax year** — the year disambiguates a null wage
    limit: for TY2024+ (SB 951 removed the ceiling) null correctly yields
    "not applicable"; for pre-2024 years null HARD-ERRORS (the cap existed —
    fetch it; treating it as no-ceiling would silently drop a refundable
    credit). A nonzero `employer_error_amount` means that portion is
    recovered from the employer (EDD DE 1964), not the return; only
    `excess_credit` goes on the 540. Write it to `ca_excess_sdi_credit`.
12. CA refund/owed

**No-income-tax states (`tax_law state.type == "none"` — FL):**
1. Do NOT compute a state return and do NOT leave the section empty.
   Populate `florida_state` from the template: `state_return_required:
   false` + the affirmative `no_income_tax_statement`. **The template ships
   `no_income_tax_statement: null` on purpose** (Codex round-11 P2) — WRITE
   the real sentence yourself here, verbatim: "Florida imposes no personal
   income tax (Fla. Const. Art. VII, §5). No Florida individual income-TAX
   return exists or is required (Florida property and use-tax filings may
   still apply — see the use-tax note and property-tax items in this
   section for the specific forms). Wages, capital gains, retirement
   income, and interest/dividends are not state-taxed." **The parenthetical
   is deliberately form-NEUTRAL (Codex round-23 P2):** the correct use-tax
   form depends on the dealer-registration answer collected in item 3
   below (DR-15 for registered dealers, DR-15MO otherwise), which has not
   been asked yet when this statement is written — naming DR-15MO here
   unconditionally would contradict the tailored `use_tax_note` in the
   same PDF for a registered dealer. Do NOT skip this
   because the template "already has similar text" — a null field here is
   the ONLY evidence `/tax-review` (step 3.7) and `/tax-diff`'s
   year-over-year report have that this section was genuinely computed for
   this taxpayer, as opposed to an un-pruned template scaffold sitting
   untouched in a non-FL taxpayer's result.json; leaving it null when this
   branch runs would make the affirmative statement invisible and let a
   dropped computation pass as complete. A blank section reads as "not
   done"; the written statement reads as "done — and the answer is zero by
   law." **`type: "none"` is not FL-exclusive** — setup can
   legitimately produce it for any state with no individual income tax
   (e.g. TX, TN, WY, AK, NV, SD). For a `type: "none"` state OTHER than FL,
   no `result_template.json` section exists yet for it (only `florida_state`
   is modeled) — do NOT fabricate one and do NOT leave the return silent on
   the point. Instead, record the same affirmative no-income-tax statement
   in `meta.limitations` (and note it in `answers.md`) so the return
   explicitly states "no state income tax return exists or is required for
   {state}" rather than omitting state coverage without explanation.
2. **Schedule A sales-tax input — FL ONLY (`state.code == "FL"`):** the SALT
   line-5a election for a no-income-tax state is general SALES tax (Box 17
   is empty — there is no income tax to elect).
   **Compute the worksheet income BEFORE selecting TABLE_AMOUNT (round-4
   finding C).** The IRS Optional State Sales Tax Tables are NOT keyed on
   AGI alone — the Schedule A instructions' Optional Sales Tax Tables
   worksheet defines the lookup "income" as AGI PLUS nontaxable receipts
   (nontaxable Social Security/railroad-retirement benefits, tax-exempt
   interest, veterans' benefits, workers' compensation, the nontaxable
   portion of IRA/pension distributions, nontaxable combat pay, public
   assistance, etc.). Using AGI alone UNDERSTATES the lookup income — and
   therefore the deduction — for any taxpayer with these receipts. Compute:
   `worksheet_income = form_1040.line_11_agi + (form_1040.
   line_6a_social_security − form_1040.line_6b_taxable_ss) [nontaxable
   Social Security/RRB benefits] + form_1040.line_2a_tax_exempt_interest
   [tax-exempt interest] + OTHER_NONTAXABLE_RECEIPTS`, where
   `OTHER_NONTAXABLE_RECEIPTS` is an interview catch-all — ask the taxpayer
   whether they received VA disability/veterans' benefits, workers'
   compensation, a nontaxable IRA/pension/annuity distribution (excluding
   rollovers), nontaxable combat pay, or public assistance during the
   year, **and also ask an explicit open-ended question: "Did you receive
   any OTHER nontaxable income or benefits this year not otherwise
   reported on your return?"** (Codex round-17 P2) — these five categories
   are EXAMPLES from the IRS worksheet's own income definition ("plus any
   nontaxable items, such as the following"), not an exhaustive list;
   stopping at only these five would UNDERSTATE `worksheet_income` (and
   therefore the FL table-lookup deduction) for a taxpayer with some other
   nontaxable receipt the worksheet's own wording doesn't name. Sum
   whatever they report. Show the arithmetic (each component and its
   source) in `answers.md` so it's auditable, the same way the
   county-surtax time-weighting above is. Use
   `worksheet_income`, NOT AGI, as {income} in the table lookup below.
   **Method election FIRST (Codex round-19 P2): the optional tables are an
   ALTERNATIVE method, not the only one.** Schedule A line 5a lets the
   taxpayer deduct their ACTUAL state and local general sales taxes when
   they kept receipts substantiating them (the Schedule A instructions
   frame the tables as the fallback: "Instead of using your actual
   expenses, you can use the optional sales tax tables"). Ask: "Did you
   keep receipts or records substantiating the actual general sales taxes
   you paid this year?" If YES: total the general-rate sales tax actually
   paid from those records for PERSONAL purchases ONLY — sales tax on
   items used in a trade or business (a Schedule C filer's supplies,
   equipment, materials) is EXCLUDED from the line-5a personal deduction
   (Codex round-21 P2): it belongs on the business side, already inside
   the Schedule C expense or the asset's depreciable basis, so counting it
   here both inflates line 5a and double-deducts the same tax. Ask a
   Schedule C / business-owner taxpayer to separate personal from
   business-use receipts BEFORE totaling. **Also ask NOW (not only at the
   use-tax disclosure in item 3): "Did you actually PAY Florida use tax
   during {year} on personal out-of-state/online purchases (a DR-15MO or
   DR-15 payment)?"** — compensating use tax PAID at the general rate is
   part of deductible state and local general sales taxes under the
   actual-expense method (Schedule A instructions), so include it in the
   actual total; include ONLY amounts actually paid during the tax year
   (never an accrued-but-unpaid liability) and ONLY on personal-use
   purchases (business-use follows the business-receipts exclusion above).
   Paid use tax is an ACTUAL-METHOD input only — never add it on top of
   the optional-table amount (the table method's only permitted additions
   are the enumerated big-ticket classes; Codex round-24 P2). (An
   actual-receipts total
   already includes the county surtax and any big-ticket purchases — do
   NOT add BIG_TICKET_TAX
   or apply the county-surtax ratio on top of it.) Still compute the table
   result below for comparison, and use the LARGER of the two as the
   line-5a candidate; set `sales_tax_deduction.method` to
   `"actual_receipts"` when actual wins (else `"optional_tables"`), and
   record both figures plus the substantiation answer in `answers.md`.
   Always selecting the table without asking UNDERSTATES line 5a (and can
   flip the standard-vs-itemized election) for a receipts-keeping taxpayer
   whose actual taxes exceed the table amount. If NO receipts: the table
   method below is the only permitted path (set `method` to
   `"optional_tables"`). Look
   up the IRS Optional State Sales Tax Table amount for {worksheet_income,
   exemptions, FL} in the current-year Schedule A instructions (search the web),
   then:
   ```bash
   sh agents/lib/run.sh -c "from agents.lib.tax_computations import compute_sales_tax_deduction; print(compute_sales_tax_deduction(TABLE_AMOUNT, STATE_RATE, COUNTY_SURTAX_RATE, BIG_TICKET_TAX))"
   ```
   (STATE_RATE from the runtime-fetched `state.sales_tax.state_rate`; never
   substitute a remembered or inline Florida rate. COUNTY_SURTAX_RATE from
   `state.sales_tax.county_surtax_rate` — UNLESS
   `answers.md` records a time-weighted local rate because the taxpayer lived
   in more than one FL county this year or the county's surtax rate changed
   mid-year (the `/tax-setup` county follow-up question), in which case use
   THAT recorded weighted rate instead of the single fetched county figure;
   BIG_TICKET_TAX from answers.md — actual sales tax paid on a QUALIFYING
   big-ticket purchase class ONLY: motor vehicles (INCLUDING sales tax
   paid on a LEASED motor vehicle — the Schedule A instructions include
   leased vehicles explicitly, and FL charges sales tax on each lease
   payment; Codex round-24 P2), boats, aircraft, a MOBILE/PREFABRICATED
   home purchase that carried general sales tax, or
   building materials for a home/substantial addition/major renovation, at
   the general sales tax rate, per the Schedule A Optional Sales Tax Tables
   instructions. A CONVENTIONAL existing-home purchase is NOT in the
   class (Codex round-25 P2) — it carries documentary stamp tax, not
   sales tax; doc stamps, intangible tax, and closing taxes NEVER feed
   BIG_TICKET_TAX. Ordinary purchases — furniture, jewelry, electronics,
   appliances, clothing — are already inside TABLE_AMOUNT; do not add their
   sales tax here or the deduction double-counts.) Write the result into
   `florida_state.sales_tax_deduction` (`total` → `total_line_5a_candidate`).
   When the actual-receipts method won the comparison above,
   `total_line_5a_candidate` is the ACTUAL total instead, `method` is
   `"actual_receipts"`, and the `table_amount`/`local_addition`/
   `big_ticket_tax` components record the LOSING table computation as
   comparison evidence — note in `answers.md` that the total intentionally
   does not equal the component sum in that case.
   **ORDERING (Codex round-18 P1): this computation is an INPUT to Step 5,
   which in document order has already run by the time you reach Step 10 —
   Step 5's Schedule A bullet therefore requires running THIS lookup before
   the `compute_schedule_a` call and passing the total as
   `state_local_sales_taxes`, so the income-vs-sales election and the SALT
   cap (`federal.salt_cap`) price it in.** If Schedule A was nonetheless
   already computed WITHOUT this value (or the value changed after — e.g. a
   big-ticket purchase surfaced late in the interview), do NOT just record
   the figure here: re-run `compute_schedule_a` with `state_local_sales_taxes`
   set and recompute EVERY dependent line — the Step 5 election and lines
   12–15, Step 6 tax, Step 6.5 AMT, Step 7 credits, Step 8 other taxes,
   Step 9 payments/refund, and the Step 9.5 §6654 penalty. A stale election
   silently omits line 5a or finalizes the wrong standard-vs-itemized
   choice. **The fetched FL state rate and the FL optional-table lookup are
   FLORIDA-SPECIFIC — do NOT reuse them for any other `type: "none"` state.**
   For a `type: "none"` state OTHER than FL (state.code != "FL" — e.g. TX,
   TN, WY, AK, NV, SD): do NOT run this computation. A TX filer computed
   at FL's 6% table/rate would silently misprice Schedule A line 5a (TX's
   actual state rate is 6.25%, and TX has no `county_surtax_rate` fetched
   at all — v1 models FL's sales-tax election only). Instead, skip the
   automated computation and record in BOTH `meta.limitations` and
   `answers.md` that "Schedule A line 5a general sales-tax deduction should
   be computed manually from the IRS Optional State Sales Tax Tables for
   {state} — the automated computation is FL-specific and does not apply
   here (v1 models FL only)." **AK and TN variants (Codex rounds 23/26):**
   the capability gate's note differs for these two — AK has NO statewide
   sales tax and uses the IRS Optional LOCAL Sales Tax Tables only, while
   post-repeal TN uses BOTH the state table AND the local table (both are
   on the IRS local-table list in the Schedule A instructions worksheet).
   Copy the gate's state-specific wording verbatim; a state-table-only
   note for AK or TN omits the locality amount and understates line 5a.
3. **Use-tax disclosure (`florida_state.use_tax_note`, FL only):** the
   template ships a fixed disclosure naming BOTH forms and the condition
   that selects between them. Confirm with the taxpayer whether they made
   untaxed out-of-state/online purchases this year and tailor or remove
   the note accordingly (e.g. record an actual use-tax liability if they
   identify one, or delete the field if they affirmatively made none) —
   record the answer in `answers.md`. **Also ask: "Are you registered as a
   Florida sales-and-use-tax dealer?"** (Codex round-16 P2, verified
   against floridarevenue.com's consumer use-tax guidance: "Registered
   Florida dealers should not use the Out-of-State Purchase Return
   (DR-15MO) to remit use tax" — they report and pay it on their regular
   Form DR-15 instead) — a Schedule C taxpayer who makes taxable retail
   sales and is registered to collect FL sales tax on those sales is
   typically ALSO registered as a dealer, so `has_self_employment` alone
   is not the right screen; ask directly. If registered, tailor the note
   to name Form DR-15 (NOT DR-15MO); if not registered, name Form DR-15MO
   as before. Naming the wrong form to a registered dealer directs them to
   file the wrong return. **If this question surfaces personal use tax
   that was actually PAID during {year} AND item 2 elected the
   actual-receipts method WITHOUT it** (the item-2 ask was missed or the
   taxpayer remembered late), do NOT just update the note: add the paid
   amount to the actual total, re-run the actual-vs-table comparison, and
   — if the winner or the amount changed — re-run `compute_schedule_a`
   and every dependent line per the Step 10 item 2 recompute fallback
   (Codex round-24 P2).
4. Skip THIS state's own refund/withholding pieces only: there is no
   {state} withholding to reconcile (no income tax to withhold), and a
   1099-G state-refund recovery **originating from {state} itself** can
   never arise (this state never taxed the income it would be refunding).
   **This does NOT extend to a 1099-G from a DIFFERENT, PRIOR income-tax
   state** — a full-year {state} resident who moved from an income-tax
   state (e.g. NY → FL) may still hold a taxable 1099-G for a refund of
   that PRIOR state's tax paid before the move, or from a return filed
   this year for a prior tax year. `flags.has_1099g_state_refund` therefore
   stays LIVE for type-none-state residents (do not suppress the
   `/tax-parse` check just because the CURRENT resident state has no
   income tax) — when set, route it through the normal taxable-state-
   refund worksheet (tax-benefit-rule limited to the lesser of the refund
   or the SALT amount actually itemized in the refund year) to
   **Schedule 1 line 1**, sourced from the issuing state's own prior-year
   return, not from {state}.

**Other states:** Follow state-specific rules from `tax_law_{year}.json`.
**EXCEPTION — NY:** never reaches this branch; see the pointer at the top of
this file.
**EXCEPTION — pre-2021 TN (Hall tax):** do NOT bracket-estimate a TN return
for a tax year before 2021. The Hall income tax applied ONLY to interest
and dividends — a generic bracket estimate over AGI would fabricate tax on
wages/SE/capital gains the Hall regime never imposed (the skill-level twin
of the lib guards: `projection.project_state_tax` and
`loss_harvest._state_marginal_rate` both refuse TN bracket math). Record
the capability gate's Hall-specific not-modeled note (Form INC 250 is
manual) and compute nothing.
