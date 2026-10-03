# Epic Z — State coverage for the named next users

Backlog: `docs/roadmap/BACKLOG.md`. Epic Z was adopted 2026-09-05 as part of the
staff agent-developer review and re-cut the state-support roadmap against the
next users the owner named that day: CA first, FL and NYC at P2, NC and WA at P3.

**This file was written on 2026-09-20 by MOVING the narrative out of the backlog,
where it had accumulated.** Epic Z is PR #121 and PRs #123-#137 — sixteen, not
the whole #120-#137 span, because #120 and #122 are Y7's — and it never had a
changelog file, so sixteen pull requests of implementation history lived in a
backlog's item entries, the exact content `docs/changelog/README.md` says belongs
here. The text below is the backlog's and is unedited except at one line, where a
pointer to the state-support roadmap became a pointer to git history because that
file was deleted the same day. Z4, Z5, Z6 and Z8 have not shipped and stayed in
the backlog.

---

## The epic as adopted — P2 for FL and NY/NYC, P3 for NC and WA

> As a NYC taxpayer, I want the same straightforward experience a CA or FL taxpayer
> gets — interview, fetch, parse, compute, PDF, verify, review — with the city tax
> simply part of my return; as a Seattle or NC taxpayer, I want the toolkit to say what
> my state actually owes.

**Definition of done for NYC (owner ruling 2026-09-05): parity with CA, not an
acknowledgeable block.** A NYC user must never see the capability gate, never be asked
to sign a limitation, and never be handed a "bracket estimate". Concretely, every
surface where CA is wired in has an NY/NYC twin:

| Surface | CA today | NYC at done |
|---|---|---|
| Capability gate | no rule | `nyc_resident` rule DELETED, flag re-purposed as the city-tax switch — **SHIPPED, slice 14** |
| `/tax-setup` interview | CA questions | NY residency + NYC/Yonkers residency + 414(h)/IRC-125 prompts |
| `tax_law_<year>.json` | `state.CA` block | `state.NY` block with year-gated tables, recapture, NYC and Yonkers brackets, pinned to tax.ny.gov |
| `/tax-parse` + ledger | `state_withholding_ca` | `state_withholding_ny`, `city_withholding_nyc` (W-2 box 19/20), box 14 414(h)/IRC-125 |
| `/tax-compute` Step 10 | Form 540 | IT-201 including lines 47-58 (NYC tax, Yonkers, MCTMT), IT-225, IT-196 |
| `result.json` | `california_540` | `new_york_it201` with a `nyc` sub-block, in `STATE_SECTION_KEYS` and `STATE_MOVER_FIELDS` |
| `/tax-generate` PDF | 540 page | IT-201 page with the city lines, filing package entry |
| `/tax-verify` | 540 line map | IT-201 line map incl. NYC lines against the software return |
| `/tax-review` | CA section | NY section: residency tests, 184-day rule, STAR/homestead conflicts |
| `/tax-calendar`, `/tax-project`, `/tax-withholding` | 540-ES | IT-2105 vouchers (one voucher covers NYS+NYC+Yonkers+MCTMT), NY safe harbor on NYAGI |
| `/tax-harvest`, `/tax-scenario`, `/tax-roth-convert` | CA marginal | NY+NYC combined marginal, recapture-band caveat |
| `/tax-ptet` | CA/NY/NJ entity side | plus owner-side IT-653 credit, IT-225 add-back, NYC PTET |
| `/tax-rollover`, `/tax-amend`, YoY diff | CA fields | NY fields, IT-201-X three-column |
| Tests | CA hand-computed worksheets | IT-201 worksheet-pinned expectations, NYC recapture and cliff cases, playbook doc-sync tests |
| Freshness auditor | audits `state.CA` | audits `state.NY` incl. the city tables |

Acceptance: a synthetic NYC fixture workspace runs the full pipeline with no gate, no
acknowledgement, no manual note about the city, and `/tax-verify` ties to a
software-prepared IT-201 within $1 on every line including 47-58. It is the second
eval-harness case, after V3's CA fixture. Z1-Z3 are one epic with three milestones,
not three optional stories, and they ship together at P2: a NYC return without
IT-2105 events, the NY withholding layer and the credits is not the CA experience.

Story detail lived in the state-support roadmap (deleted 2026-09-20, in git history); the numbers there are its
epic.story ids. Every story here follows `docs/contributing/state-module-playbook.md`.

## Z1-Z3 — the spec, written 2026-09-10

**Z1-Z3 — SPEC WRITTEN 2026-09-10**, `docs/superpowers/specs/2026-09-10-ny-state-module-design.md`,
  from the playbook's step-0 questionnaire. Live-verified: NY starts from federal AGI;
  IT-196 itemizing is INDEPENDENT of the federal election and runs on PRE-TCJA federal
  rules; §615 imposes two high-AGI reductions plus a separate charitable limitation; and
  **§601(d-1)'s tax-table-benefit recapture means plain bracket math UNDERSTATES NY tax**
  above a threshold — the playbook's canonical trap, so the recapture ships WITH the rate
  schedules rather than after them. Yonkers is in scope and has no flag today. Eight
  OPEN rows remain, each naming the source to consult rather than guessing; eight slices
  proposed. No indexed figure appears in the spec — §2 applies to a design document too.

  **SLICE 1 SHIPPED**: the `state.NY` block is a real skeleton — brackets keyed PER
  STATUS (§601(a)/(b)/(c) are three schedules), a key for the §601(d-1) recapture and
  three separate keys for §615's two reductions and its charitable limit, NYC and
  Yonkers with `applies` NULL so "no city tax" and "not fetched" stay different
  answers, and both payroll caps fetched because the CA scar is a wage ceiling that
  DISAPPEARED mid-decade. `conforms_to_federal` read True and is now False. A third
  keying trap surfaced: NY's standard deduction has SIX rows, splitting a single filer
  on whether they can be claimed as a dependent. Twelve mutations, twelve reds.

  **SLICE 2 SHIPPED**: `compute_ny_tax` — three methods, not one, selected on BOTH
  NYAGI and taxable income. New York's TAX TABLE is not a restatement of its schedule
  (it is the schedule at each row's MIDPOINT, rounded), the row widths are not constant,
  and the first draft used the schedule everywhere — worth up to two dollars, BOTH ways,
  against a one-dollar bar. The schedule needs each band's published cumulative base and
  now cross-checks it against the sum beneath. The form rounds the phase-in FRACTION and
  nothing else. `tax` is the whole-dollar line 39, half-up, because Python's `round` is
  half-even. A negative New York AGI is an ORDINARY return and the first draft refused
  it. Every expected value hand-computed from IT-201-I (2025) pp.32-38; twenty-two
  mutations across the two rounds, twenty-two reds.

  **SLICE 3 SHIPPED**: `compute_ny_agi` (lines 19-33) with the IT-225 modifications and
  the pension exclusion. The modification DIRECTION is cross-checked against the code
  prefix rather than inferred, and a negative amount refuses rather than flipping sides
  — a subtraction filed as an addition over-states the tax and an addition filed as a
  subtraction under-states it. The codes NY disallows on a resident return are fetched;
  unfetched, the totals still compute and say loudly that eligibility was not checked.
  The pension exclusion is PER TAXPAYER and does not pool, refuses to assume
  entitlement, and refuses a government pension (fully excluded on its own line —
  capping one would silently limit it). NYAGI may be NEGATIVE. Seventeen mutations,
  seventeen reds — two of which only went red after clearing `__pycache__`, the
  same-length mutate-and-restore trap the memory records.

  **SLICE 4 SHIPPED**: `compute_ny_itemized` — IT-196 lines 46-49. FOUR regimes by
  NYAGI, and the top two REPLACE the deduction with a share of the charitable
  contributions rather than reducing it. The second reduction takes the whole first
  quarter and then phases a second in on top. The first threshold is per status while
  the gate into the worksheet is not. Fifteen mutations, fifteen reds — three of them
  only after writing cases that reach a cap no ordinary figure reaches, including one
  that can only bind if two separately fetched figures diverge. Line 45 itself is NOT
  in this slice and is named as a non-goal: pre-TCJA federal rules, so the 2%
  miscellaneous floor is live again and a federal Schedule A total is no substitute.

  **SLICE 5 SHIPPED**: the household credit and the credit ORDERING (lines 39-46),
  which closes the spec's open row on ordering. Nonrefundable credits are summed then
  capped at the tax and the excess is LOST — so no ordering among them matters — but
  **line 45 is added AFTER the cap**, so a credit cannot offset it and netting first
  would under-state the return by the whole of line 45. The household credit is keyed on
  FEDERAL AGI (the only threshold here that is), is BARRED for a dependent of another
  taxpayer, and groups the statuses three ways that are not the §601 grouping — MFS
  pairs with single for the schedule and gets half the credit here. Ten mutations, ten
  reds. The other five credits, and which are REFUNDABLE, remain open.

  **SLICE 6 SHIPPED**: `compute_ny_settlement` (lines 63-82), and the refundability
  question is closed — **every remaining NY credit is a PAYMENT**, not a credit against
  the tax, so it can produce a refund with no tax to offset. The credit names are a
  CLOSED SET so a nonrefundable one cannot be smuggled in and refunded. Two credits that
  get missed are named: the noncustodial parent EIC and the NYC EIC. **The penalty is
  INSIDE the amount owed** and reduces an overpayment instead of being billed — the same
  shape a review caught on federal 1040 line 38. The college tuition CREDIT and
  DEDUCTION are mutually exclusive across two functions, so neither sees the other
  without being told. Ten mutations, ten reds. Open: the AMOUNT of each credit
  (IT-213/216/215/214/272 each have their own worksheet).

  **SLICE 7 SHIPPED — this is Z2**: NYC, Yonkers and the MCTMT (lines 47-58). NYC
  taxable income is the STATE's except for a Charitable Gifts Trust add-back, and a
  joint return with only ONE city resident REFUSES rather than taxing the household as
  residents. Two city credit caps, and **line 58 adds the MCTMT and both Yonkers taxes
  AFTER both** — the line-45 pattern a second time. The MCTMT threshold is a CLIFF
  applied PER ZONE: the instructions' own example exempts $40,000 in one zone and taxes
  the whole $70,000 in the other. The NYC household credit REUSES the state function —
  tables 4/5/6 to 1/2/3, verified against the instructions rather than assumed. Ten
  mutations, ten reds; one survived until a case used the second of the two caps.
  **Corrected 2026-09-23 (AA7):** this slice did not compute the City resident tax
  itself (line 47a) or the Yonkers lines 55 and 56; those are AA13 and AA12.

  **SLICE 8 SHIPPED — this is Z3's owner side**: `compute_ny_ptet_owner` and
  `compute_ny_required_annual_payment`. The PTET credit and its IT-225 add-back are an
  exact PAIR — A-219 is "the amount of the credit claimed on IT-653 LINE 1" and A-222
  the amount on LINE 2, so claiming the credit without the addition under-states NY tax
  by the whole of the credit, and adding both back under one code is a different return.
  The credit is REFUNDABLE. The required annual payment is the LESSER floor in equal
  quarters, with the higher prior-year share keyed to NEW YORK AGI; a prior-year tax
  without its NYAGI refuses. Eleven mutations, eleven reds.

  **SLICE 9 SHIPPED — a shipped crash, found on the way into the registrations**:
  `state.brackets` has TWO shapes. California's is a flat list of bands; New York's is
  a dict keyed by §601 rate SCHEDULE, because three filing statuses share one schedule
  and none can be read off the bands. A non-empty dict is TRUTHY, so every
  `state.get("brackets") or []` gate waved a New York cache into code that iterates a
  list of dicts: `AttributeError: 'str' object has no attribute 'keys'`, from inside
  bracket math, naming neither the cache nor a remedy. Both `/tax-project` and
  `/tax-harvest` would have failed that way for every New York taxpayer.
  `tax_computations.state_brackets_for_status` is now the one reader that knows both
  shapes, and a STRUCTURAL sweep keeps it the one: asking `if state.get("brackets")`
  for a boolean stays fine anywhere, BINDING the value to a name is the accessor's job,
  because a bound name is one something later iterates. Two consequences beyond the
  crash: `/tax-project` routes a cache that declares a benefit recapture through
  `compute_ny_tax` rather than plain bracket math, which under-states New York tax by
  the whole benefit of the graduated schedule above the entry threshold; and
  `/tax-harvest` says out loud that its state rate is a FLOOR there, since a harvested
  dollar inside the recapture band saves more than the schedule's marginal rate.
  `_state_marginal_rate` took a `filing_status` it had never read — this is its first
  use. Seven mutations, seven reds.

  **SLICE 10 SHIPPED — the result section, eight slices later than the spec said**:
  `new_york_it201`, with an `nyc` sub-block, plus `/tax-compute`'s New York branch
  (`references/state-return-ny.md`), `projection.STATE_SECTION_KEYS` and the
  computed-evidence pair. The spec said the section ships WITH the compute functions
  "because a section key registered before anything computes into it is a scaffold that
  reads as coverage" — the functions shipped first and the section came last, which is
  exactly the drift that rule was written to prevent. The assertion that keeps the names
  honest is that **every line key the engine emits has somewhere in the template to go**,
  collected by RUNNING the functions rather than reading the source, because three of the
  four line tables are expanded into f-strings at call time. It found eight mismatches on
  its first run and one real defect: `compute_mctmt` and `compute_city_and_yonkers_total`
  spelled line 54e two different ways, so a reader tying out that line had to know which
  function produced the dict. Line 59 ships **null, not 0** — sales and use tax is
  reported ON the income tax return and changes the amount owed, and a 0 default asserts
  an answer nobody gave. `_ca_section_computed` and `_ny_section_computed` are now thin
  calls on one `_section_computed` that takes the field list as an argument, because only
  the caller knows which of its fields a PARSER can fill. Five mutations, five reds; two
  of them needed a purpose-written case — a renamed template field, and a truthiness test
  that accepted `True`, `NaN` and a string as a computed deduction.

  **SLICE 11 SHIPPED — the two registrations that had to be restructured first**:
  `yoy_diff.STATE_MOVER_FIELDS` is keyed per SECTION rather than being a flat CA-only
  list gated on `california_540`, which is correct for one modeled state and silently
  wrong for two — appending New York's paths would have gated NEW YORK's movers on
  CALIFORNIA's sections. New York carries a FOURTH row California has no counterpart
  for, line 58's city/Yonkers/MCTMT total: a City resident's year-over-year move is
  invisible in the state figures and is often the larger one. The state total is **line
  46, not line 39** — line 39 is the tax before the credits and before line 45's other
  New York State taxes. A taxpayer who moved between two modeled states gets no state
  movers at all, which is right: a delta between two different states' tax is not a
  movement in anything.

  `tie_out` gains `state_withholding_ny` AND `city_withholding_nyc` — **both halves**,
  each with its row in `/tax-parse`'s step-6.5 table and a test that keeps them paired,
  because a registered-but-never-emitted category is dead protection that makes the gate
  pass vacuously. TWO categories rather than one because they are two LINES and two
  refunds: folding the city amount into line 72 still ties, and leaves the city
  withholding unclaimed on a return that owes city tax. Per STATE like
  `state_withholding_ca`, never a generic `state_withholding`. **Yonkers withholding
  (line 74) has no category**, and the parse table SAYS so rather than leaving a reader
  to assume a zero. Four mutations, four reds.

  **SLICE 12 SHIPPED — the New York withholding true-up**: `/tax-withholding` was federal
  plus California and nothing else, so a New York taxpayer got the federal report and a
  caveat. The target is **IT-201 line 60, not line 46** — one Form IT-2105 voucher carries
  the State tax, the New York City resident tax, the Yonkers surcharge and the MCTMT
  together, so a requirement built on the state total alone under-states a City resident's
  by the whole of their city tax, and a City resident is the population this module exists
  for. The §685(c) shares and thresholds are FETCHED and keyed to **New York** AGI, not
  federal, so the module reads `tax_law_{year}.json` for the first time; an ABSENT cache
  sets `ny_cache_missing` and the report SAYS the cache was missing, because a report that
  simply omits the block reads as "no New York estimated tax is due". Four EQUAL quarters,
  never California's 30/40/0/30 — which would tell a New York filer to skip September. A
  prior-year tax without its NYAGI drops the PAIR rather than assuming the lower share:
  the engine refuses it, and dropping both leaves the current-year share, which
  over-states. `ytd.state_withholding` means ALL New York withholding — boxes 17 and 19
  plus Yonkers — because that is what the one voucher is measured against, and the skill
  says so where the figure is entered. **Left OPEN and named**: New York stays on
  `next_business_day_state`'s weekends-only degrade, so a printed IT-2105 date can be a
  day or two before the one New York observes — safe for a payment, wrong for a
  missed-deadline warning; the calendar note says so on the schedule rather than leaving
  the reader to find out. Six mutations, six reds; one needed a purpose-written case,
  because the two rollover rules agree on every date the first test happened to print.

  **SLICE 13 SHIPPED — the calendar, the extension, and two gaps named rather than
  guessed**: four **IT-2105** events for a New York resident, FOUR EQUAL quarters
  **including September** — the sibling California events say there is none, and a reader
  who has seen that rule must not carry it across — each description saying that ONE
  VOUCHER covers the State tax, the City tax, the Yonkers surcharge and the MCTMT
  together.

  **`/tax-extend`'s row 9 answer is the OPPOSITE of California's, and it is a trap.**
  Form IT-370-I, read directly: *"Do not submit a copy of the federal extension form. We
  will not accept it in place of Form IT-370 and your due date will not be extended."* A
  taxpayer who extends federally and assumes New York follows has filed LATE — 5% of the
  tax due per month, up to 25%. Payment is a **condition** of the extension rather than
  merely a way to avoid interest (*"you must make full payment of the properly estimated
  tax balances due"*), where federal Form 4868 is valid with no payment at all; and an
  IT-370 filed after the deadline *"will not be accepted"* at all. The estimate is IT-201
  **line 80**, because the 10% reasonable-cause presumption is measured against the same
  four taxes line 60 adds up. One IT-370 also extends Form Y-203.

  **Two registrations are NAMED rather than shipped, and the reasons are different.**
  `retirement_dashboard`: New York is in none of the three non-conforming sets, and the
  module now says that is NOT a conformity finding — absence from them makes the dashboard
  CLAIM state savings, the over-stating-a-BENEFIT direction, so it is not the safe default
  an absent figure usually is. The sources are named. `tax-law-freshness-auditor`: its
  definition is **digest-pinned to a passed acceptance**, and a definition edit needs a
  replay from a FRESH session, which a same-session replay cannot be. So the New York
  keying gotchas went to `docs/contributing/tax-law-cache.md`, which the auditor is
  already told to work from; the definition edit folds into Y2. Four mutations, four reds;
  one needed a purpose-written case, because TY2026's dates do not separate New York's
  rollover rule from the federal one at all.

  **SLICE 14 SHIPPED — Z1 IS DONE, and the depth note was written last of all**:
  the `nyc_resident` UNSUPPORTED rule is **DELETED**. A New York City taxpayer no longer
  sees the capability gate, which is the owner's own definition of done — and a rule that
  blocks a return the engine can now produce is a gate crying wolf. The flag STAYS
  required-present, and that matters MORE than it did, not less: a missing answer once
  blocked the return loudly and would now silently omit the city tax from a return that
  owes it, which is the under-stating direction.

  Yonkers got **two** flags, not one. The resident surcharge (line 55) and the
  nonresident earnings tax (line 56) are two different taxes with two different
  populations — a taxpayer can owe either without the other — so one question cannot
  stand for both, and both are required-present for the same reason.

  `/tax-verify` gained an **IT-201 line map** with three refusals beside it: a line-46
  match is NOT a New York tie-out (lines 47-58 are a separate stack and for a City
  resident often the larger half — a return can agree on line 46 to the cent and be
  thousands wrong on line 60); line 39 is not the state tax; and a null line 59 is
  UNVERIFIED, never agreement with a software zero. Every mapped path is pinned against
  the template by test.

  **The depth note comes last because it is the sentence a reader uses to decide how far
  to trust the return.** Every clause names something that computes. What it does NOT
  claim is as load-bearing: each refundable credit's AMOUNT, IT-196 lines 1-45 and IT-201
  lines 44/45 are INPUTS, line 59 is ASKED rather than assumed zero, and IT-203 is not
  modeled. A test asserts every form the note calls an input is named as one in the
  compute reference too. Five mutations, five reds.

  **A note carried here since slice 1 was STALE and is corrected rather than re-fixed**:
  the NY PTET Mar 15/16 next-business-day defect from PR #22's deferred list is already
  fixed — `_election_deadline` returns March 16, 2026 via
  `irs_dates.next_business_day_state`, and `tax_calendar.py` uses the same helper.
  Reproduced before believing it, which is the rule applied to my own notes.

## Z1 — New York IT-201 resident return — PRs #123, #124-#137

**Z1 — DONE (#123, #124-#137) (P2, L): New York IT-201 resident return** — Epic 3.1-3.4 and 3.10: the
  year-gated bracket tables and standard deductions, `compute_ny_tax` with tax-benefit
  recapture, the IT-225 modification set (414(h), IRC-125, PTET add-back, pension
  exclusion, Social Security subtraction), decoupled IT-196 itemizing, W-2 box 14
  extraction. Plus the registrations the playbook lists: a `new_york_it201` result
  section, a `state_withholding_ny` ledger category in `tie_out.CATEGORY_TARGETS`, PDF
  page, `STATE_SECTION_KEYS`, `STATE_MOVER_FIELDS`.
## Z2 — NYC resident tax and Yonkers — PR #130

**Z2 — DONE (#130) (P2, M): NYC resident tax and Yonkers** — Epic 3.5. Retires the `nyc_resident`
  UNSUPPORTED rule; until Z1 lands it cannot ship, because the city tax is computed on
  NY taxable income. Z1+Z2 turn the BLOCK into a return; Z3 turns the return into the
  CA experience.
## Z3 — NY credits tier 1, estimates and PTET reconciliation — PR #131

**Z3 — DONE (#131) (P2, M): NY credits tier 1, estimates and PTET reconciliation** — Epic 3.6-3.9,
  3.11: Empire State child credit, NY EITC, IT-2105 events in the calendar, the NY
  layer in `/tax-withholding`, IT-653 credit, NYC PTET in `/tax-ptet`, planning-skill
  NY marginal rates.

## Z7 — Residency and multistate guardrails — PR #121

**Z7 (P2, S): Residency and multistate guardrails — DONE.** Residency is now an
  ASSERTED input: `taxpayer_profile.json` carries a `residency` block with
  `domicile_state` and per-state COUNTS for days present and abode months, validated at
  the parse boundary the way `address.state` already was — a misspelled key used to make
  the counts for the state that matters look ABSENT, which is the dangerous direction.
  The `/tax-setup` interview asks for the numbers and states the test, and
  **the engine applies New York's threshold to the counts** rather than leaving the
  taxpayer to do the arithmetic: collecting numbers instead of a boolean buys nothing if
  the line is then drawn in somebody's head. `statutory_residency_second_state` BLOCKS —
  two resident states tax the same income and the credit between them is not modeled —
  and it is distinct from `part_year_residency` (a mid-year MOVE) and
  `nonresident_source_income` (income SOURCED elsewhere). The mover checklist moved to
  `/tax-review` step 3.8, on the MAIN path: it was inside the `type: "none"` branch, so
  a taxpayer leaving New York for any income-tax state reached none of it.

  **The review caught this PR trying to ship a tax regression, and the correction is
  the point of the entry.** The first draft "fixed" the shipped `>10 months` abode
  period to eleven, citing tax.ny.gov's bulletin, and wrote a test pinning eleven. The
  Department's own Nonresident Audit Guidelines (Dec 2021, pp. 49 and 51) say Audit
  Division policy moved to **more than 10 months beginning with tax year 2022**, while
  the website still says eleven. The original author was right and correctly cited; the
  "fix" would have told a taxpayer with a 10½-month abode and 190 New York days that
  New York does not have them, left the flag False so the new gate never fired either,
  and pinned the error with a test. Both figures are now stated, year-gated, with the
  guidelines as the authority.

  Five other corrections from the same pass, each one a gate that would have fired on
  CORRECT data or missed a real one: the day-count and abode exclusions (transit,
  medical confinement, active-duty service, an undergraduate's term-time residence) are
  stated, because every omission only moves the count DOWN; the other state has to
  actually TAX INCOME, or a Florida snowbird gets blocked over a tax that does not
  exist; BOTH prongs are required; New York's numbers are labelled New York's, with
  Massachusetts (no abode-duration prong at all), Connecticut (the entire year) and
  California (no mechanical test whatsoever) named so nobody transplants them; and the
  convenience-of-the-employer rule is a five-state MINORITY, not the general rule —
  stating it as general would have fed `nonresident_source_income`, which blocks, for
  the ordinary remote worker. The NYC question was also address-gated (`If state is
  NY`), which structurally could not reach the taxpayer this epic is about.

  Every residency figure now lives in one sourced, dated table
  (`docs/contributing/residency-guardrails.md`) under a registry that is ONE positive
  assertion — a number with a unit standing near a residency noun — over every skill,
  the engine's rule text and the profile template. The first version was two scopes and
  an eleven-entry exemption mask, i.e. the enumeration-with-no-stopping-rule Y7 had just
  removed from the PII gate: four of its eleven masks were dead on arrival and the
  ten-month figure could be reintroduced in three of the five files that state it with
  every test green. Twelve mutations, twelve reds, no false positives.

  NOT in scope, and still open: residency by DOMICILE is a review finding and not a
  gate — a mover whose change of domicile fails New York's burden passes clean;
  Yonkers has the same parity as NYC in New York's own definitions and has no flag;
  city-level counts have no field; and Z8's part-year/nonresident returns are what an
  acknowledged block hands to a preparer today.


## Z4 — North Carolina final pipeline integration (#219)

The existing fetched-law schema and parameterized tax functions now reach the
return pipeline: a state registry row, NC withholding ledger category, D-400
compute reference and recorded raw inputs, Schedule S capability screening,
NC-40 reminders, extension-circle instructions, filing-package attachments
and an NC withholding true-up. Scope is the source-verified TY2025 full-year
D-400. Other form years refuse; generic NC projections skip rather than omit
state deductions. Farmer and annualized-income estimated-payment worksheets
and NC holiday dates remain explicit manual work. The true-up excludes use
tax from its income-tax requirement while keeping it in the balance target.

Sources re-read live on 2026-10-03: [TY2025 D-401](https://www.ncdor.gov/2025-d-401-individual-income-tax-instructions/open),
[G.S. 105-163.15](https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_105/GS_105-163.15.html)
and [NCDOR estimated-tax guidance](https://www.ncdor.gov/taxes-forms/individual-income-tax/estimated-income-tax).

NC-4 line 2 accepts whole dollars, so the NC per-paycheck recommendation rounds
up to the next dollar to cover the target. Authority re-read live:
[NC-4](https://www.ncdor.gov/employees-withholding-allowance-certificate-0/open).

## Z5 — Washington LT capital-gains excise (#220)

A dedicated excise regime and fetched block price classified Schedule D LT gains, excluded losses/carryforwards, qualifying deductions and substantiated same-asset credits. WA ordinary brackets stay empty and the shared reader prevents wage taxation even with corrupt legacy data. The result, setup/compute instructions, separate My DOR package and annual April reminder are wired; there are no quarterly wage-tax events. TY2025 only. Separate household allocations and special federal normalization refuse. Z6 harvest repricing remains open under an explicit unpriced banner.

## Z8 — NY IT-203 and NYC IT-360.1 (#221)

TY2025 part-year/nonresident returns use an explicit NY filing plan and
reviewed disjoint residence/source allocations. All-source AGI ties to the
federal return. Annual deductions precede all-source table/schedule/recapture
and the four-decimal source percentage, which may exceed one. State childcare
and EIC precede allocation; IT-203-ATT credits and other NY taxes follow it.
NYC IT-360.1 uses actual period income, rounded months, prorated deductions and
exemptions, and the rate schedule even below the full-year table ceiling.

The registry keeps IT-201 as NY's primary and adds a separate variant. Setup
selects NY law independently of mailing address; a complete inventory clears
move/source gates only for other no-income-tax states needing no return.
Other taxable-state returns and credits still require manual preparation.
State/NYC/Yonkers withholding ties to the selected form. Competing NY variants
refuse; City/MCTMT totals and IT-360.1 evidence are checked. Calendar, extension,
filing attachments, PDF and YoY use the selected form. Ordinary projections and
IT-203 withholding true-ups skip with explicit reasons.

MFS, mixed joint residence periods, special IT-230 ratios, IT-203 Yonkers tax,
foreign moves and statutory second residency retain refusals/gates. Credit,
modification, City itemized and special-accrual worksheets remain verified
inputs. Hand-computed source worksheets and the documented launcher are tested.
