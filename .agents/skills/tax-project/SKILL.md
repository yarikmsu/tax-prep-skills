---
name: tax-project
description: Mid-year projection — project full-year tax from YTD figures, compute safe-harbor estimates, suggest W-4 4(c) and 1040-ES adjustments. Use when the user asks mid-year what they will owe, whether to adjust withholding, or how to avoid an underpayment penalty.
license: Apache-2.0
---

# /tax-project — Mid-Year Tax Projection

Projects full-year federal (and conditional state) tax from year-to-date figures,
then computes safe-harbor required payments, a 1040-ES voucher schedule, and a
W-4 line 4(c) per-paycheck adjustment. When the state projection runs and the
state is CA, the report also includes the **CA safe harbor** (FTB 5805 — with the
R&TC §19136.3 rule that current-year CA AGI ≥ $1M / $500K MFS forces
90%-of-current-year only) and a **CA 540-ES voucher schedule** under California's
**30/40/0/30** installment weighting (30% Apr 15, 40% Jun 15, NO September
payment, 30% Jan 15 — R&TC §19136.1). Read-only: never modifies result.json or
taxpayer_profile.json. Re-runnable quarterly.

## Prerequisites
- `taxpayer_profile.json` and `tax_law_{year}.json` exist (else: run `/tax-setup {year}` first).
- `tax_law_{year}.json` with populated `federal.capital_gains_rates` (both `zero_pct_limit`
  and `fifteen_pct_limit` for the filing status) whenever the return has qualified
  dividends or a net long-term gain: the engine REFUSES to price that income at ordinary
  rates and names `/tax-setup` as the fix. A wages-only return runs without them.
- Optional: `archive/{year-1}/` (from `/tax-archive`) — supplies recurring-income
  defaults and CA prior-return data. It does NOT supply the federal prior-year AGI:
  that is paired explicitly via `prior_year_form_2210_agi`, from the same return the
  tax base was composed from. The federal 100%/110%-of-prior prong additionally requires
  `projection_inputs.json.prior_year_form_2210_tax_base`, completed under the covered
  year's Form 2210 line-8 instructions. Without that explicit base, the federal prong is
  unresolved and the report runs in degraded 90%-of-current mode.

## Steps

### Engine preflight (HARD GATE — runs before every other step)

Run: `sh agents/lib/run.sh agents/lib/preflight.py`

- **Exit 0** — the engine is present and usable; continue. If the verdict says
  `OK, with N warning(s)`, show the user those lines first: the engine is fine,
  but something a later step needs is not — a missing package, or a workspace
  with no virtualenv — and it surfaces at first use instead of here.
- **Anything else — including the command failing because the file does not
  exist — STOP.** Do not parse, compute, or emit anything. Show the user the
  command's output and stop there.

The gates and computation this skill names are scripts under `agents/lib/`, a
sibling of `.agents/skills/` that does not travel with a copied skill — which
then still reads as a complete procedure while every script it names is absent.
So a failure to RUN this check IS the blocking condition, never a step to skip.

### Procedure

1. **Validate prerequisites.** Confirm `taxpayer_profile.json` and `tax_law_{year}.json`
   exist. If not, stop and tell the user to run `/tax-setup {year}`.

2. **Load or create `projection_inputs.json`.** If absent, copy
   `agents/templates/projection_inputs_template.json` to the workspace and interview the
   user for: YTD wages, YTD federal + state withholding, pay frequency, as-of date, and any
   known lumpy items (year-end bonus, RSU vesting, capital gains, self-employment net,
   and — for TY2025-2028 — the OBBBA **Schedule 1-A** total via `forecast_overrides.schedule_1a`,
   estimated with `compute_schedule_1a`: tips + overtime premium + car-loan + senior, a
   below-AGI deduction that lowers projected taxable income but not AGI).
   For a Schedule A forecast, collect the year's full expense inputs and run
   `compute_schedule_a` with this year's fetched standard deduction and SALT cap.
   Store its complete `inputs` in `forecast_overrides.schedule_a_inputs`; confirm
   each forecast expense rather than copying a prior year's figures. The engine
   rebuilds MAGI from the recorded add-back, prices the AGI floors and phasedown,
   computes provisional QBI before the haircut, and makes the itemization election.
   Confirm the public-charity cash and appreciated-property noncash classifications
   before setting `schedule_a_charitable_limits_confirmed` true; binding charitable
   caps refuse otherwise. Investment-interest or lower-income sales-tax forecasts
   require updated worksheet inputs when the engine refuses their held values.
   Older workspaces can still supply a declared total, with its held-input warning.
   Ask for the year's deductions too, with step 3's archive defaults and its questions in
   the same pass; each is REQUIRED where it is read, and the engine refuses a file that
   leaves one unanswered: `forecast_overrides.itemized_deductions`, the itemized
   deductions the taxpayer expects (the Schedule A total, after its caps), or 0 to take
   the standard deduction; `forecast_overrides.state_itemized_deductions`, the state's
   (California's Schedule CA total), or 0 for the state's standard deduction; and, from
   TY2026, `forecast_overrides.charitable_cash_gifts`, the cash the taxpayer expects to
   give to charity this year, not to a donor-advised fund or a supporting organization,
   or 0 for none. An itemizer is asked for the gifts too: they can make the standard
   deduction the better choice, and they stay inside the itemized total as well. The
   state figure is read only where a state is projected, which New York normally is not,
   so a New York resident is not asked up front. The state's own rules decide whether it
   can differ from the federal choice: California lets it, while some states tie it to
   the federal return and some allow no itemized deductions, so enter 0 there. Each
   itemized total is priced where it beats its standard deduction, the federal one with
   the gifts' deduction added to it. Ask in an existing file too, wherever one is null or
   missing (as in one saved before it was required), and wherever step 5 refuses for one.
   Never write one on the taxpayer's behalf: 0 is their answer that they take the
   standard deduction or gave nothing, not a way past the refusal. A refusal naming
   `federal.charitable_non_itemizer` is the cache's, not an unanswered question: a cache
   set up when only a standard-deduction filer needed the gifts' cap lacks it, so run
   `/tax-setup`'s §170(p) search for the year alone and run again, the answers unchanged.
   Validate inputs (no shell metacharacters; numeric fields numeric). Immediately after
   writing the file, run `sh agents/lib/run.sh -m agents.lib.secure_fs projection_inputs.json`.

3. **Resolve prior-year baseline.** If `archive/{year-1}/` exists, read its
   `result.json` to pre-fill `recurring_from_prior` (interest/dividends) as editable
   defaults, and the defaults step 2 offers for the deductions, never over an answer the
   taxpayer gave: federal, `form_1040.line_12_standard_deduction` when
   `schedule_a.use_itemized` is true and line 13c is zero, else 0; state, for California
   only, `schedule_ca_itemized.itemized_total` when that section itemized, else 0. No
   other state's archive records a state itemized total, so there the state figure has no
   default and is asked. The gifts default to
   `form_1040.line_13c_charitable_non_itemizer` where that is nonzero, the capped
   deduction rather than everything given, so ask whether more was; otherwise they are
   asked. Ask what changed (a paid-off mortgage, a move, a change of filing status, this
   year's SALT cap) rather than carrying last year's total unexamined; whether this
   year's income moves the parts of it tied to AGI, the SALT cap's phasedown and the
   medical-expense floor, which the projection holds at the declared figure; and whether
   Schedule A's own rules changed between the two years: from TY2026 a charitable floor
   applies and itemized deductions are cut wherever taxable income plus those
   deductions exceeds the 37% bracket's start (§68, so below that bracket too), and a TY2025 total
   carries neither. Do not treat archived Form 1040 line 24 as the federal prior-year
   safe-harbor base, and do not pair an explicit `prior_year_form_2210_tax_base` with the
   archive's AGI — `/tax-amend` overwrites `archive/{year-1}/result.json` with the
   amended return's figures, so the archive AGI may belong to a different return than
   the one the tax base was composed from. Populate `prior_year_form_2210_tax_base`
   only from a completed pre-factor composition under line 8 of the Form 2210
   instructions for the covered year; line 24 minus refundable credits is still
   insufficient because the chart enumerates other-tax inclusions and exclusions. If
   the composition is not complete, leave the field null and note that the prior
   prong is unresolved. **Before storing a POSITIVE base, confirm the
   prior year was a full 12-month year AND that a return was filed for it.**
   §6654(d)(1)(B)'s closing text otherwise withdraws the ORDINARY prior-year
   prong, and that prong is the only route to a positive base — §6654(e)(2) is a
   no-LIABILITY exception and cannot produce one. The engine verifies neither
   half: it asks for a confirmation only at a base of exactly $0, and a positive
   one is taken as given. A taxpayer whose first
   filing year was short, or who never filed, would be handed 100%/110% of a
   small figure in place of the 90%-of-current prong that is the only one they
   have, which understates the required payment silently. If either half fails,
   leave the base null: the 90% degrade is the correct answer, not a fallback.
   **Do not carry the filed-return half over to a $0 base.** §6654(e)(2) requires
   no return at all — someone under last year's filing threshold is its ordinary
   case, not an exotic one — which is why the two routes to a $0 base are asked
   separately below, and demanding a filed return of both would bill a taxpayer
   with no exposure whatever for 90% of this year's tax. Whenever you answer any `prior_year_*` field, also set
   `tax_year` to the covered year: those figures are composed under that year's own
   Form 2210 instructions, and the CLI refuses a file that answers them without
   declaring which year it is for rather than silently reusing last year's base.
   When it is complete, also populate `prior_year_form_2210_agi` with AGI from that SAME
   return (its amendment rule included), off THAT year's own layout by the printed label —
   **line 11 holds AGI only from 2020 on**; it is 8b on 2019, 7 on 2018, 37 on 2017, and the
   number printed at 11 on each of those is a small one that reads as under the
   $150,000/$75,000 threshold and buys 100% where 110% applies, putting the required annual
   payment a tenth low. Leaving it null degrades to the flagged "prior AGI unknown" 100%
   factor rather than guessing. If that composition comes out to exactly $0, there are **two
   independent routes** to a $0 requirement and you must ask about both — either one alone
   is enough, and the engine refuses an unconfirmed $0 base rather than silently zeroing the
   required payment. Ask whether the prior year was a full 12-month year, whether the
   taxpayer had NO LIABILITY for it, and whether they were a US citizen or resident
   throughout it (§6654(e)(2), which does not require that a return was filed) — all three
   confirmed, set `prior_year_zero_liability_confirmed: true`. **No liability is that year's Form
   1040 TOTAL TAX line at $0, or a year that required no return at all — never the $0
   composition itself**, which subtracts refundable credits and reaches $0 on a positive
   total tax ($500 of SE tax under a $2,000 EIC). Reading the composition as the
   liability sets the flag for a taxpayer who has no exception, and on an unfiled prior
   year that zeroes a requirement they owe in full.
   Ask **separately** whether the prior year was a full 12-month year and a return was
   filed for it (§6654(d)(1)(B)'s closing text, which asks nothing about citizenship or
   residency) — both confirmed, set
   `prior_year_return_filed_12_months_confirmed: true`, and 100%/110% of $0 makes the
   ordinary prong's answer $0. Do not treat the second as implied by a "no" to the
   first: a taxpayer who was not a US citizen or resident all of last year but who filed
   a full 12-month return showing $0 is covered by the ordinary prong, and asking only
   the citizenship question bills them 90% of this year's tax.
   **California asks neither question.** A prior-year Form 540 total tax of exactly $0
   read from `archive/{year-1}/` is used as-is: R&TC §19136(c)(2) is a California rule,
   not a conformity modification of §6654(e)(2), and it carries no citizenship, no
   residency and no 12-month condition — either year's tax less credits under $500
   ($250 MFS) means no CA underpayment penalty. The report says so in its own note.

4. **Confirm the annualization split.** Show the user which items will be annualized
   (steady wages/withholding) vs. treated as full-year (overrides, recurring), and the
   elapsed fraction. Let the user correct lumpy overrides before computing.

5. **Compute.** Run:
   ```bash
   sh agents/lib/run.sh -m agents.lib.projection --inputs projection_inputs.json \
 --year {year} --as-of {MM-DD} --json
   ```
   (Omit `--as-of` for the default: today's date, or 12-31 once the year has closed and
   no stored date exists. A valid stored `as_of_date` is preserved in every year as the
   provenance for the YTD figures; a malformed stored value in a closed year is omitted
   and produces a redacted banner. Payment-lever availability is separate: the real date
   determines remaining paychecks, so a closed-year report preserves a valid mid-year
   annualization date but emits zero W-4 paychecks; `/tax-withholding` uses the same
   action-date count for its DE 4 figures.

   **In a closed year, a valid stored `as_of_date` is NOT verified against the YTD
   figures beside it.** Nothing ties the two together, so figures updated to the year's
   finals with the stamp left mid-year (June 30, say) get annualized from that stale
   date — silently understating the shortfall by the omitted months. Before trusting a
   closed-year report, confirm out loud with the user whether the stored YTD figures ARE
   the year's finals; if so, update `as_of_date` to 12-31 in `projection_inputs.json`
   before running, rather than relying on the tool to catch the mismatch.)

   **`as_of_date` must be null, not empty, to mean "absent".** In an OPEN year every PRESENT value is validated and a malformed one is refused outright — the run produces no report at all, because substituting today's date would silently relabel the YTD figures beside it. A workspace created from an older template may carry `"as_of_date": ""` (or `0`), which used to fall through to today's date and now blocks: edit it to `null`, to the MM-DD the YTD figures are current to, or pass `--as-of MM-DD` for this run.

   **Read these seventeen things out of the payload before you summarize anything.** The
   markdown in the next step carries them in prose, but the audit line below is written
   from HERE, and `safe_harbor.covered` alone is not the verdict:

   - **`safe_harbor.de_minimis`** — true means the PENALTY target is met by §6654(e)(1)
     (projected tax minus WITHHOLDING under $1,000), so `covered` reads true and every
     voucher prints $0.00. It says NOTHING about whether tax is due: the test is a
     subtraction that may come out negative, so the flag is set just as readily where
     withholding covers the whole liability and the year ends in a refund.
     **`federal.balance` decides the message** — positive is owed, negative is a refund.
     On a positive one report "no underpayment penalty can arise; $X is still due in
     April", never "no payment needed"; on a zero or negative one report that no penalty
     can arise and nothing is due. Naming an April payment against a projected refund
     asks for money the user does not owe. `ca_safe_harbor.de_minimis` is the same for CA
     at $500/$250-MFS, read against `state.balance`.
   - **`safe_harbor.de_minimis_margin`**, then **`safe_harbor.de_minimis_cliff_more_tax`**
     and **`safe_harbor.de_minimis_cliff_less_withholding`** — the margin is how far under
     the floor the run sits, and the floor is a cliff on a PROJECTION: that much more tax
     than projected, or that much less withholding, and it lapses. Quote the margin. The two
     cliff fields say what happens THEN, ONE PER PATH, and **0.00 means nothing does on that
     path** — either the required annual payment is met on its own, or a separate exemption
     clears the year whatever the projection does (CA's PRIOR-year $500 floor is one, and it
     is measured on a closed year, so no projection of this one touches it). Take the reason
     from the status cell and the CA notes, never by assuming the first. Warn only about a
     path that is positive and name it AS that path; do not merge them into "more tax or
     less withholding" when only one is positive, and when both are 0.00 do not warn about a
     penalty at all.
   - **`safe_harbor.degraded`** — true means no completed covered-year Form 2210
     line-8 pre-factor base was supplied, so the federal prior-year prong is unresolved
     and the displayed federal target uses only 90% of current-year tax. Never fill the
     input from raw Form 1040 line 24. `ca_safe_harbor.prior_tax_unnetted` is the
     separate CA disclosure because the CA prior-tax composition is not modeled;
     there is deliberately no federal counterpart field.
   - **`safe_harbor.current_tax_unnetted`** — the same §6654(f) gap on THIS year, and it
     drives both prongs and the $1,000 floor rather than one prong. True means the tax the
     harbor ran on is a line-24 total, not Form 2210 line 4 net of the refundable credits.
     Ask whether this year's return claims any of them; if it does, the required annual
     payment and the shortfall may be OVERSTATED, and a balance those credits bring under
     $1,000 owes no penalty at any pace whatever the shortfall says.
     `ca_safe_harbor.current_tax_unnetted` is the CA analogue.
   - **`voucher_catchup_note`** and **`ca_voucher_catchup_note`** — non-null means the
     schedule needs a caveat the table cannot carry, and it is ONE OF TWO: either the
     printed schedule under-states the earliest still-open instalment, or nothing already
     DUE is short but the estimated payments went in on dates this engine never saw, so an
     earlier instalment may still have run late. The second one opens by saying which of
     those it is — the annual requirement settled, or merely nothing overdue with vouchers
     still printed below — and that opening is not decoration: a schedule still asking for
     money is still short for the year. Surface the note verbatim beside the table; do not
     paraphrase it into "pay on the dates shown", and never summarize the second one as
     "nothing to do".
   - **`voucher_dates_note`** and **`ca_voucher_dates_note`** — the CALENDAR half
     of the same table, and independently true of the catch-up note above: a
     schedule can be correctly split and entirely obsolete. Non-null means at least
     one printed due date is BEHIND today, so an amount shown against it is to pay
     NOW rather than on the date beside it. When it opens "This 1040-ES schedule is
     obsolete" (or "540-ES", for CA) it is the stronger case — §6654(b)(2) has closed
     the accrual period,
     the penalty is FIXED, and what remains is a BALANCE DUE for that year, not a
     voucher. Surface it verbatim and BEFORE the catch-up note, whose "keeps
     accruing until it is paid" that case explicitly overrides. Never report a
     printed date as an upcoming action while this is non-null.
   - **`w4_lever_note`** — the lead for "Recommended actions", always a string. When
     it opens "No paychecks remain" the W-4 route is closed for the covered year and
     the $0.00 4(c) figures mean the lever is gone, NOT that nothing is owed — do not
     recommend raising withholding, and do not read those zeros as a covered year.
   - **`has_prior_archive`** — whether an `archive/{year-1}/` prior return was supplied at
     all, which is NOT what `safe_harbor.degraded` says: degraded tracks only the missing
     federal line-8 base, and the two disagree in both directions. False here also means
     `recurring_from_prior` (interest/dividends) was never pre-filled and defaults to $0 —
     the report carries that warning, and real recurring income left at $0 understates the
     projection, so confirm the figures rather than reporting the shortfall as final. The
     /tax-withholding payload carries the same field for the same provenance, but drives no
     warning there: that report annualizes no income.
   - **`stale_as_of_note`** — non-null means `projection_inputs.json` held a MALFORMED
     `as_of_date` inside a closed year, so it was dropped and 12-31 used instead. The
     figures beside it were entered against some other date and nothing reconciles the
     two: annualizing a mid-year snapshot as if it were the year's finals UNDER-states
     the shortfall. Surface it verbatim and confirm with the user that the stored YTD
     figures ARE the year's finals before reporting any number from this run. It is a
     payload field precisely because you read `--json` first — the markdown carries it
     too, but by then the summary is written.
   - **`state_bracket_estimate_note`** — non-null means the resident state is TAXED but
     NOT modeled in depth: only California's Form 540 is. Every other state's figures
     apply that state's brackets to a federally-derived taxable income and model no
     state-specific credit, addition or subtraction. Surface it whenever you report
     `state.balance`, and never present that figure as an amount owed — say it is an
     order of magnitude. It is a payload field for the same reason `stale_as_of_note`
     is: the qualification first existed only in the markdown and in the closed-year
     `w4_lever_note`, so on an OPEN year an agent reading `--json` first — as this step
     instructs — saw a bare `state.balance` with nothing marking it an estimate.
   - **`mfs_deduction_note`** and **`state_mfs_deduction_note`** — non-null only for a
     married-filing-separately taxpayer, whose spouse's choice can force the deduction
     the projection did not take. Each names the rule; where it binds, the tax and every
     payment recommended here read LOW. Surface each verbatim beside the figure it
     qualifies, and ask how the spouse files before presenting a balance as the amount
     to pay.
   - **`itemized_deduction_note`** — non-null wherever either projection priced a
     declared itemized total, which it holds as entered while parts of it move with
     income, so the tax and payments can read high or low. Surface it verbatim, and
     where the total was worked out at a different income than this projection's AGI,
     re-work it with the taxpayer and re-run.
     With Schedule A forecast inputs, the federal deduction is re-priced and its
     `federal.schedule_a_repriced` is true; read `federal.schedule_a_notes` too.
     A state total remains held and keeps its own warning.
   - **`charitable_gifts_note`** — non-null wherever the projection took the charitable
     deduction for non-itemizers, which it prices on the declared gifts as entered. Only
     some gifts qualify, and ones that do not make the tax and payments read LOW. Where
     the gifts made the standard deduction beat a larger declared itemized total, it
     says so. Surface it verbatim, and confirm what the gifts were before presenting a
     balance.
   - **`state_unsupported_note`** — non-null means the resident state's return is NOT
     modeled at all, and the note says why (a pre-2021 TN Hall return, say). No state
     figure is produced. Surface it as its own item: the reader has a state return to
     handle by hand, and a summary that simply omits the state reads as "nothing owed
     there".
   - **`nonresident_income_warning`** — non-null means the profile flags income sourced
     to ANOTHER state, whose nonresident liability and estimates are absent from every
     figure here. The projection is not wrong about the resident state; it is
     incomplete about a second one. Say so rather than presenting the total as the
     whole picture.
   - **`state_cache_mismatch`** — non-null means `tax_law_<year>.json` holds a different
     state than the taxpayer resides in, so the state projection was SKIPPED entirely.
     Report it as a blocked step with its remedy (`/tax-setup`), never as a $0 state
     balance.

   - **`state_no_income_tax`** and **`no_income_tax_state_code`** — together the THIRD
     reason `state` comes back null, and the only one that is good news: the resident
     state levies no individual income tax at all (`state.type = "none"`), so there is
     nothing to project and nothing owed. Say that positively — "FL levies no individual
     income tax, so no state return is due" — and never report it the way you report the
     two failure causes above it. A null `state` is not self-describing: these two flags,
     `state_cache_mismatch` and `state_unsupported_note` are what tell the three apart,
     and only one of them means the reader can stop thinking about it.

6. **Render + save.** Run the same command without `--json` to get the markdown report.
   Before writing, create and restrict its directory; then restrict the report immediately
   after saving it and print the report:
   ```bash
   mkdir -p projections && sh agents/lib/run.sh -m agents.lib.secure_fs projections
   ```
   Now save the report. Only once the file exists, restrict it — the two
   commands are in separate blocks because running them as one would call
   `secure_fs` on a path that does not exist yet, which exits 1:
   ```bash
   sh agents/lib/run.sh -m agents.lib.secure_fs projections/TY{year}_asof_{MM-DD}.md
   ```

7. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Mid-year projection TY{year} as-of {MM-DD} — run {today}. Safe-harbor: {covered/underpaid/de minimis — penalty excused, balance still due/de minimis — penalty excused, no balance due}.
   ```
   Then run `sh agents/lib/run.sh -m agents.lib.secure_fs answers.md` in case the write
   replaced the file rather than preserving its existing permissions.

8. **PTET election check (time-sensitive).** If `flags.has_k1_income` is true and any
   entry in `flags.k1_states` is **CA / NY / NJ**, the pass-through-entity tax election
   is a SALT-cap workaround worth flagging *while there's still time to elect* — NY's
   election is due March 15 **of the tax year itself**, so a mid-year projection is
   exactly when to surface it. Run:
   ```bash
   sh agents/lib/run.sh -m agents.lib.ptet --year {year} [--income CA=<amount> ...]
   ```
   With no `--income`, this runs in deadline-reminder mode (reads `flags.k1_states`).
   Append the earliest upcoming election deadline to the projection report. This is
   read-only and never blocks the projection — on non-zero exit, note `_PTET check
   skipped: {stderr}_`. See `/tax-ptet` for the full standalone analysis.

## Security & safety
- Any delegated sub-task must have NO network access and must NOT write outside `projections/`.
- Never extract or store full SSNs.
- This skill never writes `result.json` or `taxpayer_profile.json`.
- Restrict every written file and the `projections/` directory with
  `agents.lib.secure_fs`; on Windows a bare POSIX-mode command does not restrict the DACL.
