---
name: tax-withholding
description: Withholding true-up — compare YTD federal withholding against the actual computed liability in result.json, then recommend a W-4 4(c) and/or 1040-ES schedule to close the gap. Use when the user asks whether enough tax is being withheld, wants to update a W-4, or needs an estimated-payment schedule to close a shortfall.
license: Apache-2.0
---

# /tax-withholding — Federal Withholding True-Up

Compares YTD federal withholding + estimated payments (projected to year-end at the
current pace) against the **actual** computed liability in result.json, then recommends
a W-4 line 4(c) per-paycheck adjustment and/or a 1040-ES voucher schedule to hit the
safe-harbor (penalty-avoidance) and zero-balance targets. Read-only: never modifies
result.json or taxpayer_profile.json. Re-runnable.

Distinct from `/tax-project`: this trues up against the real computed number
(result.json line 24), not an annualized estimate. Run it after `/tax-compute` has
produced a current-year result.json.

**State coverage: CA and NY.** When result.json carries a computed `california_540`
section, the report adds a **California true-up** block: the actual Form 540 tax vs
YTD CA withholding projected at the current pace, the FTB 5805 safe harbor (90% of
current / 100%–110% of prior CA tax — EXCEPT current-year CA AGI ≥ $1M / $500K MFS
forces 90%-of-current only per R&TC §19136.3), a DE 4 per-paycheck
extra-withholding figure, and a 540-ES voucher schedule under CA's **30/40/0/30**
installment weighting (no September payment — R&TC §19136.1).

A computed `new_york_it201` section adds a **New York true-up**, and its target is
**IT-201 line 60, not line 46**: one Form IT-2105 voucher carries the State tax, the
New York City resident tax, the Yonkers surcharge and the MCTMT together, so building
the requirement on the state total alone would under-state a City resident's by the
whole of their city tax. The Tax Law §685(c) safe harbor has the same SHAPE as the
federal one and none of its constants — the shares and thresholds are FETCHED from
`tax_law_{year}.json` and keyed to **New York** AGI, not federal — and the instalments
are four **EQUAL** quarters, emphatically not California's split. The IT-2104
adjustment is the New York analogue of W-4 4(c).

**`ytd.state_withholding` means ALL New York withholding** on an NY return — W-2 box
17, box 19 for a New York City locality, and any Yonkers amount together — because one
voucher is measured against all of them. Entering the state figure alone over-states
the shortfall.

Returns with neither section get the federal report plus a caveat naming both.

## Prerequisites
- `result.json` with a computed `form_1040.line_24_total_tax` (else: run `/tax-compute`).
- `projection_inputs.json` (else: run `/tax-project` first, or create it from
  `agents/templates/projection_inputs_template.json` and fill YTD federal withholding,
  estimated payments, pay frequency, and as-of date).
- `tax_law_{year}.json`, with populated `federal.capital_gains_rates` (both `zero_pct_limit`
  and `fifteen_pct_limit` for the filing status) whenever the return has qualified
  dividends or a net long-term gain: the engine REFUSES to price that income at ordinary
  rates and names `/tax-setup` as the fix. A wages-only return runs without them.
- Optional: `archive/{year-1}/` — supplies CA prior-return data. It does NOT supply
  the federal prior-year AGI: that is paired explicitly via `prior_year_form_2210_agi`,
  from the same return the tax base was composed from.
  The federal 100%/110%-of-prior prong additionally requires
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

**North Carolina:** a computed D-400 adds an NC true-up using the year's
`state.estimated_tax` block. Record `nc_farmer_fisherman` explicitly in
`projection_inputs.json`; a qualifying farmer/fisherman is referred to the
separate January/March worksheet instead of ordinary quarters. Record
`nc_prior_year_return_filed_12_months_confirmed` only after confirming that the
archived NC return was actually filed for a twelve-month year. Separately ask
about no NC liability for the preceding year and record
`nc_prior_year_no_liability_confirmed`. These are NC answers, never copied from
the federal confirmations. `ytd.state_withholding` and state estimates are all
NC amounts. Relay `nc.notes`, `nc.voucher_dates_note` and `nc_cache_missing`;
the latter means the fetched-law cache is missing, not that no payment is due.
NC-4 accepts an additional amount per pay period; NC-40 uses equal quarters.
Use tax belongs to the D-400 balance target, not the income-tax harbor. Confirm
NC legal-holiday dates; the engine rolls weekends only conservatively.

1. **Validate prerequisites.** Confirm `result.json`, `projection_inputs.json`, and
   `tax_law_{year}.json` exist. If `result.json` lacks a computed line 24, stop and tell
   the user to run `/tax-compute`. If you create `projection_inputs.json` from the
   template in this skill, immediately run
   `sh agents/lib/run.sh -m agents.lib.secure_fs projection_inputs.json` after writing it.

2. **Resolve prior-year baseline.** An `archive/{year-1}/` return supplies CA prior-return
   data, but its Form 1040 line 24 is not the federal safe-harbor base, and its AGI must
   not be paired with an explicit `prior_year_form_2210_tax_base` — `/tax-amend`
   overwrites `archive/{year-1}/result.json` with the amended return's figures, so the
   archive AGI may belong to a different return than the one the tax base was composed
   from. Populate `prior_year_form_2210_tax_base` only from a completed pre-factor
   composition under line 8 of the Form 2210 instructions for the covered year; line 24
   minus refundable credits is still insufficient because the chart enumerates other-tax
   inclusions and exclusions. If the composition is not complete, leave the field null
   and note that the federal prior prong is unresolved. **Before storing a POSITIVE base, confirm the
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
   with no exposure whatever for 90% of this year's tax. Whenever you answer any
   `prior_year_*` field, also set `tax_year` to the covered year — the CLI refuses a
   file that answers them without declaring which year it is for, rather than
   silently reusing last year's base against this year's prong. When it is complete, also
   populate `prior_year_form_2210_agi` with AGI from that SAME return (its amendment rule
   included), off THAT year's own layout by the printed label — **line 11 holds AGI only
   from 2020 on**; it is 8b on 2019, 7 on 2018, 37 on 2017, and the number printed at 11 on
   each of those is a small one that reads as under the $150,000/$75,000 threshold and buys
   100% where 110% applies, putting the required annual payment a tenth low. Leaving it null
   degrades to the flagged "prior AGI unknown" 100% factor rather than guessing. If the
   completed base is exactly $0, there are **two independent routes** to a $0 requirement
   and you must ask about both — either one alone is enough, and the engine refuses an
   unconfirmed $0 base. Ask whether the prior year was a full 12-month year, whether the
   taxpayer had NO LIABILITY for it, and whether they were a US citizen or resident
   throughout it (§6654(e)(2), which does not require that a return was filed) — all three
   confirmed, set `prior_year_zero_liability_confirmed: true`. **No
   liability is that year's Form 1040 TOTAL TAX line at $0, or a year that required no
   return at all — never the $0 base itself**, which subtracts refundable credits and
   reaches $0 on a positive total tax ($500 of SE tax under a $2,000 EIC). Reading the base
   as the liability sets the flag for a taxpayer who has no exception, and on an unfiled
   prior year that zeroes a requirement they owe in full. Ask **separately** whether the
   prior year was a full 12-month year and a return was filed for it (§6654(d)(1)(B)'s
   closing text, which asks nothing about citizenship or residency) — both confirmed, set
   `prior_year_return_filed_12_months_confirmed: true`, and 100%/110% of $0 makes the
   ordinary prong's answer $0. A "no" to the first does not imply a "no" to the second, and
   asking only the citizenship question bills a filer the ordinary prong already covers 90%
   of this year's tax.
   **California asks neither question.** A prior-year Form 540 total tax of exactly $0
   read from `archive/{year-1}/` is used as-is: R&TC §19136(c)(2) is a California rule,
   not a conformity modification of §6654(e)(2), and it carries no citizenship, no
   residency and no 12-month condition — either year's tax less credits under $500
   ($250 MFS) means no CA underpayment penalty. The report says so in its own note.

3. **Compute.** Run:
   ```bash
   sh agents/lib/run.sh -m agents.lib.withholding --result result.json --inputs projection_inputs.json \
 --year {year} --as-of {MM-DD} --json
   ```
   (Omit `--as-of` for the default: today's date, or 12-31 once the year has closed and
   no stored date exists. A valid stored `as_of_date` is preserved in every year as the
   provenance for the YTD figures; a malformed stored value in a closed year is omitted
   and produces a redacted banner. Payment-lever availability is separate: the real date
   determines remaining paychecks, so a closed-year report preserves a valid mid-year
   annualization date but emits zero W-4 and DE 4 paychecks.

   **In a closed year, a valid stored `as_of_date` is NOT verified against the YTD
   figures beside it.** Nothing ties the two together, so figures updated to the year's
   finals with the stamp left mid-year (June 30, say) get annualized from that stale
   date — silently understating the shortfall by the omitted months. Before trusting a
   closed-year report, confirm out loud with the user whether the stored YTD figures ARE
   the year's finals; if so, update `as_of_date` to 12-31 in `projection_inputs.json`
   before running, rather than relying on the tool to catch the mismatch.)

   **`as_of_date` must be null, not empty, to mean "absent".** In an OPEN year every PRESENT value is validated and a malformed one is refused outright — the run produces no report at all, because substituting today's date would silently relabel the YTD figures beside it. A workspace created from an older template may carry `"as_of_date": ""` (or `0`), which used to fall through to today's date and now blocks: edit it to `null`, to the MM-DD the YTD figures are current to, or pass `--as-of MM-DD` for this run.

   **Read these thirteen things out of the payload before you summarize anything.** The
   markdown in the next step carries them in prose, but the audit line below is written
   from HERE, and `safe_harbor.covered` alone is not the verdict:

   - **`safe_harbor.de_minimis`** — true means the PENALTY target is met by §6654(e)(1)
     (projected tax minus WITHHOLDING under $1,000), so `covered` reads true and every
     PENALTY-target voucher prints $0.00. It says NOTHING about whether tax is due: the
     test is a subtraction that may come out negative, so the flag is set just as readily
     where withholding covers the whole liability and the year ends in a refund.
     **`zero_target` decides the message** — its `shortfall` is what is actually left to
     pay and `covered` true means nothing is. On a positive shortfall report "no
     underpayment penalty can arise; $X is still due in April", never "no payment
     needed"; on a covered one report that no penalty can arise and nothing is due —
     `vouchers_zero` prints $0.00 across the board there, and there is no $X to name.
     `ca.safe_harbor.de_minimis` is the same for CA at $500/$250-MFS, read against
     `ca.zero_shortfall`.
   - **`safe_harbor.de_minimis_margin`**, then
     **`safe_harbor.de_minimis_cliff_less_withholding`** — the margin is how far under the
     floor the run sits. In THIS report the tax is the computed liability and does not
     move; the annualized WITHHOLDING pace is the side that can, so the cliff is the pace
     coming in that much lower. Quote the margin. The withholding cliff field says what
     happens THEN, and **0.00 means nothing does** — either the required annual payment is
     met on its own, or a separate exemption clears the year regardless of the pace (CA's
     PRIOR-year $500 floor is one) — so do not warn about a penalty at all. Take the reason
     from the status cell and the CA notes. Warn only when it is positive, and name that
     figure. **`de_minimis_cliff_more_tax` is the OTHER path and
     does not apply here**: it prices a tax higher than projected, and this report does not
     project the tax. Never quote it as a withholding figure. And when `withholding` is
     below `de_minimis_margin`, no withholding pace lapses this floor at all — the tax is
     under it on its own.
   - **`safe_harbor.degraded`** — true means no completed covered-year Form 2210
     line-8 pre-factor base was supplied, so the federal prior-year prong is unresolved
     and the displayed federal target uses only 90% of current-year tax. Never fill the
     input from raw Form 1040 line 24. `ca.safe_harbor.prior_tax_unnetted` is the
     separate CA disclosure because the CA prior-tax composition is not modeled;
     there is deliberately no federal counterpart field.
   - **`safe_harbor.current_tax_unnetted`** — the same §6654(f) gap on THIS year, and it
     drives both prongs and the $1,000 floor rather than one prong. True means the tax the
     harbor ran on is a line-24 total, not Form 2210 line 4 net of the refundable credits.
     Ask whether this year's return claims any of them; if it does, the required annual
     payment and the shortfall may be OVERSTATED, and a balance those credits bring under
     $1,000 owes no penalty at any pace whatever the shortfall says.
     `ca.safe_harbor.current_tax_unnetted` is the CA analogue.
   - **`voucher_catchup_note`** and **`ca.voucher_catchup_note`** — non-null means the
     schedule needs a caveat the table cannot carry, and it is ONE OF TWO: either the
     printed schedule under-states the earliest still-open instalment, or nothing already
     DUE is short but the estimated payments went in on dates this engine never saw, so an
     earlier instalment may still have run late. The second one opens by saying which of
     those it is — the annual requirement settled, or merely nothing overdue with vouchers
     still printed below — and that opening is not decoration: a schedule still asking for
     money is still short for the year. Surface the note verbatim beside the table; do not
     paraphrase it into "pay on the dates shown", and never summarize the second one as
     "nothing to do".
   - **`voucher_dates_note`** and **`ca.voucher_dates_note`** — the CALENDAR half
     of the same table, and independently true of the catch-up note above: a
     schedule can be correctly split and entirely obsolete. Non-null means at least
     one printed due date is BEHIND today, so an amount shown against it is to pay
     NOW rather than on the date beside it. When it opens "This 1040-ES schedule is
     obsolete" (or "540-ES", for CA) it is the stronger case — §6654(b)(2) has closed
     the accrual period,
     the penalty is FIXED, and what remains is a BALANCE DUE for that year, not a
     voucher. Surface it verbatim and BEFORE the catch-up note, whose "keeps
     accruing until it is paid" that case explicitly overrides. Never report a
     printed date as an upcoming action while this is non-null. It reads BOTH columns
     of the schedule, which `voucher_catchup_note` above cannot: that one is scoped to
     the "To avoid penalty" column and goes silent whenever the harbor is covered or
     de minimis, while "To zero balance" beside it still carries the whole balance.
   - **`w4_lever_note`** — the lead for "Recommended actions", always a string. When
     it opens "No paychecks remain" the W-4 route is closed for the covered year and
     the $0.00 4(c) figures mean the lever is gone, NOT that nothing is owed — do not
     recommend raising withholding, and do not read those zeros as a covered year.
   - **`has_prior_archive`** — whether an `archive/{year-1}/` prior return was supplied at
     all, which is NOT what `safe_harbor.degraded` says. Degraded tracks only the missing
     federal line-8 base, so the two disagree in both directions: an archive can be present
     with the federal prong still unresolved (the ordinary case), and an explicit
     `prior_year_form_2210_tax_base` can resolve it with no archive at all. Read it to
     answer "is there a prior return to go back to?" — false means the CA prior-year prong
     had no source either, and the remedy is `/tax-rollover` or the archive, not another
     input. Do NOT read it as a claim that a prior-year CA harbor was computed: that is
     `ca.safe_harbor.prior_based`. Unlike the /tax-project field of the same name it drives
     no warning of its own here — this report annualizes no income, so there is no
     `recurring_from_prior` to have silently defaulted to $0.
   - **`nc_cache_missing`** — true means no NC true-up was computed because its
     fetched-law cache was missing. Ask for the covered year's NC cache; never
     summarize this as no NC payment due.
   - **`nc.notes`** and **`nc.voucher_dates_note`** — relay all NC scope,
     prior-year, use-tax and calendar warnings beside the NC targets. Closed
     schedules name balance payments, not upcoming estimated vouchers.
   - **`ny_cache_missing`** — true means result.json carries a COMPUTED Form IT-201
     but no tax-law cache reached this run, so `ny` is null and NO New York true-up
     was computed. Report it as a missing input, never as a finding: the Tax Law
     §685(c) shares and thresholds are FETCHED — they are New York's own and may
     diverge from the federal ones — so there is no requirement to publish without
     them. An omitted New York block reads as "no New York estimated tax is due",
     which is the wrong direction for a payment. The remedy is re-running with the
     year's `tax_law_{year}.json`, not another figure.
   - **`ny.notes`** — what `compute_ny_required_annual_payment` SAID rather than
     assumed. The one that matters most is the no-prior-year note: with no prior
     return the current-year share is the only floor, and a first-year New York filer
     genuinely has none while a taxpayer who simply has not supplied one may have a
     LOWER requirement. Surface each verbatim; do not fold them into the status cell.
     The other is the high-income note, which fires when prior-year NEW YORK AGI —
     not federal — is over the threshold and the higher prior-year share applies.
   - **`stale_as_of_note`** — non-null means `projection_inputs.json` held a MALFORMED
     `as_of_date` inside a closed year, so it was dropped and 12-31 used instead. The
     figures beside it were entered against some other date and nothing reconciles the
     two: annualizing a mid-year snapshot as if it were the year's finals UNDER-states
     the shortfall. Surface it verbatim and confirm with the user that the stored YTD
     figures ARE the year's finals before reporting any number from this run. It is a
     payload field precisely because you read `--json` first — the markdown carries it
     too, but by then the summary is written.

4. **Render + save.** Run the same command without `--json` to get the markdown report.
   Before writing, create and restrict its directory; then restrict the report immediately
   after saving it and print the report:
   ```bash
   mkdir -p withholding && sh agents/lib/run.sh -m agents.lib.secure_fs withholding
   ```
   Now save the report. Only once the file exists, restrict it — the two
   commands are in separate blocks because running them as one would call
   `secure_fs` on a path that does not exist yet, which exits 1:
   ```bash
   sh agents/lib/run.sh -m agents.lib.secure_fs withholding/TY{year}_asof_{MM-DD}.md
   ```

5. **Audit note.** Append one line to `answers.md` (NO dollar PII duplicated):
   ```
   ## Withholding true-up TY{year} as-of {MM-DD} — run {today}. Safe-harbor: {covered/underpaid/de minimis — penalty excused, balance still due/de minimis — penalty excused, no balance due}.
   ```
   Then run `sh agents/lib/run.sh -m agents.lib.secure_fs answers.md` in case the write
   replaced the file rather than preserving its existing permissions.

## Security & safety
- Any delegated sub-task must have NO network access and must NOT write outside `withholding/`.
- Never extract or store full SSNs.
- This skill never writes `result.json` or `taxpayer_profile.json`.
- Restrict every written file and the `withholding/` directory with
  `agents.lib.secure_fs`; on Windows a bare POSIX-mode command does not restrict the DACL.
